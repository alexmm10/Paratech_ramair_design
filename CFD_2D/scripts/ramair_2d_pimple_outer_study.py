#!/usr/bin/env python3
"""Executable isolated nOuterCorrectors sensitivity study (2, 3 and 4)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from ramair_scientific_plot_style import apply_scientific_style, save_scientific_figure

apply_scientific_style()

from ramair_2d_convergence_analysis import compare_pimple_outer_correctors
from ramair_2d_execution_registry import upsert_execution
from ramair_2d_rans_checkpoint_batch import (
    RansCheckpointBlocked,
    require_compatible_checkpoint,
)
from ramair_2d_study_registry import (
    active_workspace_root,
    hardlink_tree,
    load_study,
    read_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_validation_report import analyze_run
from ramair_2d_validation_staged_runner import (
    CONTINUE_STAGE,
    FRESH_FROM_CHECKPOINT,
    configure_stage,
    runner_command,
)


def _safe_dt(dt_s: float) -> str:
    return f"{dt_s:.8g}".replace(".", "p").replace("-", "m").replace("+", "")


def _replace_pimple_outer(path: Path, value: int) -> None:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"PIMPLE\s*\{(?P<body>.*?)\n\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"PIMPLE dictionary missing from {path}")
    body, count = re.subn(
        r"(?m)^(\s*nOuterCorrectors\s+)[^;]+;",
        rf"\g<1>{int(value)};",
        match.group("body"),
        count=1,
    )
    if count != 1:
        raise ValueError(f"nOuterCorrectors missing from {path}")
    path.write_text(
        text[: match.start("body")] + body + text[match.end("body") :],
        encoding="utf-8",
    )


def _foam_entry(text: str, name: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s+([^;]+);", text)
    return match.group(1).strip() if match else None


def _foam_dictionary(text: str, name: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*\{{", text)
    if not match:
        return None
    opening = text.find("{", match.start())
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index]
    return None


def _same_value(applied: Any, selected: Any) -> bool:
    if isinstance(applied, (int, float)) and isinstance(selected, (int, float)):
        return math.isclose(float(applied), float(selected), rel_tol=1.0e-10)
    return applied == selected


def _audit_pimple_case(
    case: Path,
    entry: dict[str, Any],
    stage: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    solution = (case / "system/fvSolution").read_text(encoding="utf-8")
    schemes = (case / "system/fvSchemes").read_text(encoding="utf-8")
    control = (case / "system/controlDict").read_text(encoding="utf-8")
    pimple = _foam_dictionary(solution, "PIMPLE")
    if pimple is None:
        raise ValueError(f"PIMPLE dictionary missing from {case / 'system/fvSolution'}")
    actual = {
        "nOuterCorrectors": int(
            float(_foam_entry(pimple, "nOuterCorrectors") or -1)
        ),
        "nCorrectors": int(float(_foam_entry(pimple, "nCorrectors") or -1)),
        "nNonOrthogonalCorrectors": int(
            float(_foam_entry(pimple, "nNonOrthogonalCorrectors") or -1)
        ),
        "time_scheme": _foam_entry(
            _foam_dictionary(schemes, "ddtSchemes") or "", "default"
        ),
        "deltaT": float(_foam_entry(control, "deltaT") or -1.0),
        "write_control": _foam_entry(control, "writeControl"),
        "write_interval": float(_foam_entry(control, "writeInterval") or -1.0),
        "purge_write": int(float(_foam_entry(control, "purgeWrite") or -1)),
    }
    selected = {
        "nOuterCorrectors": int(entry["nOuterCorrectors"]),
        "nCorrectors": int(entry["nCorrectors"]),
        "nNonOrthogonalCorrectors": int(entry["nNonOrthogonalCorrectors"]),
        "time_scheme": str(stage["scheme"]),
        "deltaT": float(stage["dt_s"]),
        "write_control": actual["write_control"],
        "write_interval": actual["write_interval"],
        "purge_write": actual["purge_write"],
    }
    rows = [
        {
            "parameter": key,
            "selected": value,
            "applied": actual[key],
            "matches": _same_value(actual[key], value),
        }
        for key, value in selected.items()
    ]
    audit = {
        "schema_version": 1,
        "status": (
            "CONFIGURATION_APPLIED"
            if all(row["matches"] for row in rows)
            else "CONFIGURATION_MISMATCH"
        ),
        "case": str(case),
        "stage": str(stage["stage"]),
        "rows": rows,
        "checked_at": utc_stamp(),
    }
    write_json_atomic(output_path, audit)
    if audit["status"] != "CONFIGURATION_APPLIED":
        raise RuntimeError(
            "Resolved PIMPLE configuration mismatch: "
            f"{[row for row in rows if not row['matches']]}"
        )
    return audit


def _source_run(
    study: dict[str, Any],
    run_id: str | None,
    *,
    topology: str,
    mesh_level: str,
    dt_s: float | None,
) -> dict[str, Any]:
    candidates = [
        row
        for row in study["run_matrix"]["runs"]
        if row["mesh_id"] == f"{topology}_{mesh_level}"
    ]
    if run_id:
        selected = next((row for row in candidates if row["run_id"] == run_id), None)
        if selected is None:
            raise ValueError(
                f"The selected run does not match {topology}-{mesh_level}: {run_id}"
            )
        return selected
    if dt_s is None:
        raise ValueError("Select topology, mesh level and deltaT for the PIMPLE study")
    selected = next(
        (
            row
            for row in candidates
            if math.isclose(float(row["dt_s"]), float(dt_s), rel_tol=1.0e-12)
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            f"No canonical definition exists for {topology}-{mesh_level} at deltaT={dt_s:.8g} s"
        )
    return selected


def _source_case(project_root: Path, row: dict[str, Any]) -> Path:
    return (
        active_workspace_root(project_root)
        / "runs"
        / str(row["topology"])
        / str(row["mesh_level"])
        / str(row["run_id"])
        / "case"
    )


def _copy_case(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    for name in ("0", "constant", "system"):
        source_part = source / name
        destination_part = destination / name
        if name == "constant":
            destination_part.mkdir(parents=True)
            for child in source_part.iterdir():
                if child.name == "polyMesh":
                    hardlink_tree(child, destination_part / child.name)
                elif child.is_dir():
                    shutil.copytree(child, destination_part / child.name)
                else:
                    shutil.copy2(child, destination_part / child.name)
        else:
            shutil.copytree(source_part, destination_part)


def _clone_temporal_case(source: Path, destination: Path) -> None:
    """Clone dictionaries, mesh and every reconstructed physical time."""
    _copy_case(source, destination)
    for child in source.iterdir():
        if not child.is_dir() or child.name in {"0", "constant", "system"}:
            continue
        try:
            if float(child.name) <= 0.0:
                continue
        except ValueError:
            continue
        shutil.copytree(child, destination / child.name)


def _positive_times(case: Path) -> list[float]:
    values: list[float] = []
    for child in case.iterdir() if case.is_dir() else ():
        if not child.is_dir():
            continue
        try:
            value = float(child.name)
        except ValueError:
            continue
        if value > 0.0:
            values.append(value)
    return sorted(values)


def _entry_completion_evidence(
    study_root: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Recognize completed legacy entries without mutating their metadata."""
    run_root = Path(study_root) / str(entry["run_id"])
    case = Path(str(entry["case"]))
    plan = read_json(run_root / "stage_plan.json", {}) or {}
    stages = list(plan.get("stages") or [])
    final_stage = stages[-1] if stages else {}
    target = float(final_stage.get("end_s") or 0.0)
    dt_s = float(final_stage.get("dt_s") or entry.get("dt_s") or 0.0)
    times = _positive_times(case)
    latest = max(times, default=None)
    reached_target = bool(
        latest is not None
        and target > 0.0
        and latest >= target - max(2.0 * dt_s, 1.0e-12)
    )
    status = read_json(run_root / "execution_status.json", {}) or {}
    summary = read_json(run_root / "case_summary.json", {}) or {}
    force_candidates = [
        run_root / "force_coeffs.csv",
        *sorted(case.glob("postProcessing/forceCoeffs/*/forceCoeffs.dat")),
    ]
    force_path = next(
        (path for path in force_candidates if path.is_file() and path.stat().st_size > 0),
        None,
    )
    log = case / "log.foamRun"
    normal_end = bool(
        log.is_file()
        and re.search(r"(?m)^End\s*$", log.read_text(encoding="utf-8", errors="replace"))
    )
    explicit_complete = str(status.get("status") or "") == "COMPLETED"
    analyzed_complete = str(summary.get("status") or "") == "COMPLETED"
    complete = bool(
        reached_target
        and force_path is not None
        and normal_end
        and (explicit_complete or analyzed_complete)
    )
    return {
        "complete": complete,
        "run_id": entry.get("run_id"),
        "nOuterCorrectors": entry.get("nOuterCorrectors"),
        "explicit_execution_status": status.get("status"),
        "analysis_status": summary.get("status"),
        "latest_time_s": latest,
        "target_time_s": target,
        "target_tolerance_s": max(2.0 * dt_s, 1.0e-12),
        "target_reached": reached_target,
        "normal_solver_end": normal_end,
        "force_history": str(force_path) if force_path else None,
    }


def _resume_entry_selection(
    study_root: Path,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = {
        str(entry["run_id"]): _entry_completion_evidence(study_root, entry)
        for entry in entries
    }
    return {
        "execute": [
            str(entry["run_id"])
            for entry in entries
            if not evidence[str(entry["run_id"])]["complete"]
        ],
        "preserve_completed": [
            str(entry["run_id"])
            for entry in entries
            if evidence[str(entry["run_id"])]["complete"]
        ],
        "evidence": evidence,
    }


def _measurement_signature(case: Path) -> str:
    """Hash frozen science while normalizing phase-owned runtime cursors."""
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in case.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(case).as_posix(),
    ):
        relative = path.relative_to(case).as_posix()
        if relative.split("/", 1)[0] not in {"0", "constant", "system"}:
            continue
        if relative.startswith("processor") or relative.startswith("postProcessing/"):
            continue
        content = path.read_bytes()
        if relative.startswith("system/"):
            text = content.decode("utf-8", errors="replace")
            if relative == "system/fvSolution":
                text = re.sub(
                    r"(?m)^(\s*nOuterCorrectors\s+)[^;]+;",
                    r"\g<1><OUTER>;",
                    text,
                    count=1,
                )
            elif relative == "system/fvSchemes":
                # configure_stage owns the Euler -> backward phase cursor.
                text = re.sub(
                    r"(?m)^(\s*ddtSchemes\s*\{\s*default\s+)[^;]+;",
                    r"\g<1><PHASE_SCHEME>;",
                    text,
                    count=1,
                )
            elif relative == "system/decomposeParDict":
                # The common serial bootstrap and the MPI measurements write
                # different transient rank counts before execution.
                text = re.sub(
                    r"(?m)^(\s*numberOfSubdomains\s+)[^;]+;",
                    r"\g<1><RUNTIME_RANKS>;",
                    text,
                    count=1,
                )
            elif relative == "system/controlDict":
                for name in (
                    "startFrom", "startTime", "stopAt", "endTime", "deltaT",
                    "writeControl", "writeInterval", "purgeWrite",
                ):
                    text = re.sub(
                        rf"(?m)^(\s*{name}\s+)[^;]+;",
                        rf"\g<1><PHASE_{name.upper()}>;",
                        text,
                        count=1,
                    )
            content = text.encode("utf-8")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def prepare_study(
    project_root: Path,
    *,
    run_id: str | None = None,
    topology: str | None = None,
    mesh_level: str | None = None,
    dt_s: float | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    root = active_workspace_root(project_root) / "pimple_outer_study"
    selected_topology = str(topology or "closed")
    selected_mesh_level = str(mesh_level or "coarse")
    mesh_id = f"{selected_topology}_{selected_mesh_level}"
    try:
        checkpoint = require_compatible_checkpoint(
            project_root, mesh_id
        )
    except RansCheckpointBlocked as exc:
        manifest = {
            "schema_version": 1,
            "status": exc.payload["status"],
            "study": "nOuterCorrectors 2-3-4",
            "source_mesh_id": mesh_id,
            "entries": [],
            "blocked_reason": exc.payload["message"],
            "required_actions": [
                f"Generate or update the {mesh_id} RANS diagnostics.",
                "Review the real RANS evidence.",
                "Explicitly accept the base as statistically steady or for URANS initialization.",
                "Create the compatible reviewed RANS checkpoint.",
            ],
            "automatic_approval_forbidden": True,
            "updated_at": utc_stamp(),
        }
        write_json_atomic(root / "pimple_outer_study_manifest.json", manifest)
        write_json_atomic(root / "study_manifest.json", manifest)
        return manifest
    study = load_study(project_root)
    row = _source_run(
        study,
        run_id,
        topology=selected_topology,
        mesh_level=selected_mesh_level,
        dt_s=dt_s,
    )
    source = _source_case(project_root, row)
    if not (source / "system/controlDict").is_file():
        # Internal preparation is intentional: the PIMPLE action must never
        # require a separate URANS preparation click or a pilot result.
        from ramair_2d_validation_study import prepare_run

        prepare_run(project_root, str(row["run_id"]))
    if not (source / "system/controlDict").is_file():
        raise FileNotFoundError(f"Could not construct the selected source case: {source}")
    selected_dt = float(dt_s or row["dt_s"])
    config = study["study_config"]["pimple_outer_study"]
    condition = study["study_config"]["operating_condition"]
    tc_s = float(condition["tc_s"])
    previous = read_json(root / "pimple_outer_study_manifest.json", {}) or {}
    for old_entry in previous.get("entries", []):
        old_root = root / str(old_entry.get("run_id") or "")
        if old_root.is_dir() and old_root.parent == root:
            shutil.rmtree(old_root)
    common_root = root / "common_initialization"
    common_case = common_root / "case"
    if common_root.exists():
        shutil.rmtree(common_root)
    _copy_case(source, common_case)
    checkpoint_zero = Path(str(checkpoint.get("restart_zero") or ""))
    if not checkpoint_zero.is_dir():
        raise RuntimeError(f"The accepted {mesh_id} RANS checkpoint has no restart fields")
    shutil.rmtree(common_case / "0")
    shutil.copytree(checkpoint_zero, common_case / "0")
    common_stage = {
        "stage": "COMMON_INITIALIZATION",
        "purpose": "Common three-step Euler history for backward",
        "scheme": "Euler",
        "dt_s": selected_dt,
        "start_s": 0.0,
        "end_s": 3.0 * selected_dt,
        "duration_s": 3.0 * selected_dt,
        "steps": 3,
        "sampling": False,
    }
    write_json_atomic(common_root / "stage_plan.json", {"stages": [common_stage]})
    entries: list[dict[str, Any]] = []
    for outer in (2, 3, 4):
        identifier = (
            f"{mesh_id}_a08_{_safe_dt(selected_dt)}_"
            f"pimple{outer}_backward"
        )
        run_root = root / identifier
        case = run_root / "case"
        if run_root.exists():
            shutil.rmtree(run_root)
        _copy_case(common_case, case)
        _replace_pimple_outer(case / "system/fvSolution", outer)
        cursor_s = float(common_stage["end_s"])
        cursor_tc = cursor_s / tc_s
        stages = []
        for name, purpose, duration, sampling in (
            ("D", "PIMPLE sensitivity settling", float(config["settling_tc"]), False),
            ("E", "PIMPLE sensitivity sampling", float(config["sampling_tc"]), True),
        ):
            stages.append(
                {
                    "stage": name,
                    "purpose": purpose,
                    "scheme": "backward",
                    "dt_s": selected_dt,
                    "dt_star": selected_dt / tc_s,
                    "start_s": cursor_s,
                    "end_s": cursor_s + duration * tc_s,
                    "duration_s": duration * tc_s,
                    "start_tc": cursor_tc,
                    "end_tc": cursor_tc + duration,
                    "duration_tc": duration,
                    "steps": int(round(duration * tc_s / selected_dt)),
                    "sampling": sampling,
                }
            )
            cursor_s += duration * tc_s
            cursor_tc += duration
        metadata = {
            "run_id": identifier,
            "topology": selected_topology,
            "mesh_level": selected_mesh_level,
            "mesh_id": mesh_id,
            "mesh_hash": checkpoint["mesh_hash"],
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_hash": checkpoint.get("field_hashes", {}),
            "dt_s": selected_dt,
            "dt_star": selected_dt / tc_s,
            "nOuterCorrectors": outer,
            "nCorrectors": 2,
            "nNonOrthogonalCorrectors": 1,
            "time_scheme": "backward",
            "same_physics": True,
            "same_duration": True,
            "same_mpi_ranks": True,
            "sampling_start_s": stages[-1]["start_s"],
            "operating_condition": condition,
            "status": "READY",
        }
        write_json_atomic(run_root / "case_metadata.json", metadata)
        write_json_atomic(
            run_root / "stage_plan.json",
            {
                "target_dt_s": selected_dt,
                "target_dt_star": selected_dt / tc_s,
                "stages": stages,
                "steps_total": sum(stage["steps"] for stage in stages),
            },
        )
        entries.append({**metadata, "case": str(case)})
        upsert_execution(
            project_root,
            {
                "run_id": identifier,
                "mode": "PIMPLE_SENSITIVITY",
                "topology": selected_topology,
                "mesh_level": selected_mesh_level,
                "mesh_id": mesh_id,
                "stage": "PREPARED",
                "status": metadata["status"],
                "case_path": str(case),
                "deltaT": selected_dt,
                "nOuterCorrectors": outer,
            },
        )
    manifest = {
        "schema_version": 1,
        "status": "READY",
        "study": "nOuterCorrectors 2-3-4",
        "entries": entries,
        "scientific_key": {
            "topology": selected_topology,
            "mesh_level": selected_mesh_level,
            "mesh_id": mesh_id,
            "deltaT_s": selected_dt,
        },
        "common_initialization": {
            "case": str(common_case),
            "stage": common_stage,
            "status": "READY",
        },
        "constraints": {
            "same_mesh_hash": checkpoint["mesh_hash"],
            "same_checkpoint_id": checkpoint["checkpoint_id"],
            "same_dt_s": selected_dt,
            "same_duration_tc": cursor_tc,
            "outer_correctors": [2, 3, 4],
            "nCorrectors": 2,
            "nNonOrthogonalCorrectors": 1,
        },
        "prepared_at": utc_stamp(),
        "execution_requirement": (
            "Prepared internally from the selected compatible RANS checkpoint; "
            "no URANS pilot or external prepared case is required."
        ),
    }
    write_json_atomic(root / "pimple_outer_study_manifest.json", manifest)
    write_json_atomic(root / "study_manifest.json", manifest)
    return manifest


def execute_study(
    project_root: Path,
    *,
    run: bool,
    resume: bool = False,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    root = active_workspace_root(project_root) / "pimple_outer_study"
    stop_marker = root / ".ramair_pimple_stop_request.json"
    # A new explicit execution acknowledges any marker left by an earlier,
    # already reconciled stop. A marker created after this point belongs to
    # the current execution and is checked between every expensive phase.
    stop_marker.unlink(missing_ok=True)
    manifest = read_json(
        root / "pimple_outer_study_manifest.json",
        read_json(root / "study_manifest.json", {}) or {},
    ) or {}
    if not manifest.get("entries"):
        manifest = prepare_study(project_root)
    if not manifest.get("entries"):
        return manifest
    study = load_study(project_root)
    ranks = int(study["study_config"]["validation_study"]["mpi_ranks"])
    timeout = float(study["study_config"]["validation_study"]["timeout_hours"]) * 60
    common = dict(manifest.get("common_initialization") or {})
    common_case = Path(str(common.get("case") or ""))
    common_stage = dict(common.get("stage") or {})
    if not common_case.is_dir() or not common_stage:
        raise RuntimeError("The common PIMPLE initialization was not prepared")
    common_status_path = common_case.parent / "execution_status.json"
    common_status = read_json(common_status_path, {}) or {}
    common_completed = (
        str(common_status.get("status")) == "COMPLETED"
        and len(_positive_times(common_case)) >= 3
    )
    common_record: dict[str, Any]
    if common_completed:
        common_record = {
            "stage": "COMMON_INITIALIZATION",
            "status": "ALREADY_COMPLETED",
            "returncode": 0,
        }
    else:
        applied_common = configure_stage(
            common_case,
            common_stage,
            start_mode=FRESH_FROM_CHECKPOINT,
            preserve_temporal_history=True,
        )
        common_command = runner_command(
            common_case,
            n_cores=1,
            timeout_min=min(timeout, 15.0),
            start_mode=FRESH_FROM_CHECKPOINT,
            expected_start_time=None,
            run=run,
        )
        if run:
            started = time.monotonic()
            completed = subprocess.run(common_command, cwd=str(common_case), check=False)
            common_record = {
                **applied_common,
                "command": common_command,
                "returncode": int(completed.returncode),
                "wall_seconds": time.monotonic() - started,
                "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            }
        else:
            common_record = {
                **applied_common,
                "command": common_command,
                "returncode": None,
                "status": "DRY_RUN_READY",
            }
        write_json_atomic(
            common_status_path,
            {**common_record, "updated_at": utc_stamp()},
        )
        common_completed = bool(run and common_record["returncode"] == 0)
    manifest["common_initialization"] = {
        **common,
        "status": common_record["status"],
        "execution": common_record,
    }
    if run and not common_completed:
        manifest.update(status="COMMON_INITIALIZATION_FAILED", updated_at=utc_stamp())
        write_json_atomic(root / "pimple_outer_study_manifest.json", manifest)
        write_json_atomic(root / "study_manifest.json", manifest)
        return manifest

    if run:
        resume_selection = (
            _resume_entry_selection(root, list(manifest["entries"]))
            if resume else {
                "execute": [str(entry["run_id"]) for entry in manifest["entries"]],
                "preserve_completed": [],
                "evidence": {},
            }
        )
        manifest["resume_selection"] = resume_selection
        protected_completed = set(resume_selection["preserve_completed"])
        common_times = _positive_times(common_case)
        if len(common_times) < 3:
            raise RuntimeError(
                "PIMPLE common initialization did not retain the current state and two old states"
            )
        for entry in manifest["entries"]:
            case = Path(entry["case"])
            if str(entry["run_id"]) in protected_completed:
                continue
            if not (resume and _positive_times(case)):
                _clone_temporal_case(common_case, case)
                _replace_pimple_outer(
                    case / "system/fvSolution", int(entry["nOuterCorrectors"])
                )
        signatures = {
            str(entry["run_id"]): _measurement_signature(Path(entry["case"]))
            for entry in manifest["entries"]
        }
        if len(set(signatures.values())) != 1:
            raise RuntimeError(
                "PIMPLE_STRUCTURED_DIFF_FAILED: the three measurement clones differ by more than nOuterCorrectors"
            )
        manifest["structured_diff"] = {
            "status": "ONLY_N_OUTER_CORRECTORS_DIFFERS",
            "normalized_signatures": signatures,
        }

    results: list[dict[str, Any]] = []
    stop_requested = False
    protected_completed = set(
        (manifest.get("resume_selection") or {}).get("preserve_completed") or []
    ) if resume else set()
    for entry in manifest["entries"]:
        if stop_requested:
            break
        if str(entry["run_id"]) in protected_completed:
            results.append({
                "run_id": entry["run_id"],
                "status": "PRESERVED_COMPLETED",
                "evidence": (
                    (manifest.get("resume_selection") or {}).get("evidence") or {}
                ).get(str(entry["run_id"])),
            })
            continue
        run_root = root / entry["run_id"]
        case = Path(entry["case"])
        stages = read_json(run_root / "stage_plan.json", {})["stages"]
        records: list[dict[str, Any]] = []
        previous_status = read_json(run_root / "execution_status.json", {}) or {}
        completed_stages = {
            str(item.get("stage") or item.get("phase"))
            for item in previous_status.get("stages", [])
            if item.get("returncode") == 0
        } if resume else set()
        for index, stage in enumerate(stages):
            if stop_marker.is_file():
                stop_requested = True
                records.append(
                    {
                        "stage": stage["stage"],
                        "returncode": None,
                        "status": "PAUSED_RESTARTABLE",
                    }
                )
                break
            if str(stage["stage"]) in completed_stages:
                records.append(
                    {
                        "stage": stage["stage"],
                        "returncode": 0,
                        "status": "ALREADY_COMPLETED",
                    }
                )
                continue
            physical_times = _positive_times(case)
            if run and len(physical_times) < 3:
                raise RuntimeError(
                    f"{entry['run_id']}: backward measurement requires three consecutive input times"
                )
            history_times = (
                physical_times[-3:]
                if len(physical_times) >= 3
                else [
                    float(common_stage["dt_s"]),
                    2.0 * float(common_stage["dt_s"]),
                    3.0 * float(common_stage["dt_s"]),
                ]
            )
            applied = configure_stage(
                case,
                stage,
                start_mode=CONTINUE_STAGE,
                preserve_temporal_history=True,
            )
            configuration_audit = _audit_pimple_case(
                case,
                entry,
                stage,
                run_root
                / f"applied_configuration_audit_{stage['stage']}.json",
            )
            command = runner_command(
                case,
                n_cores=ranks,
                timeout_min=timeout,
                start_mode=CONTINUE_STAGE,
                expected_start_time=history_times[-1],
                run=run,
                decompose_times=history_times,
                reconstruct_times=[
                    float(stage["end_s"]) - 2.0 * float(stage["dt_s"]),
                    float(stage["end_s"]) - float(stage["dt_s"]),
                    float(stage["end_s"]),
                ],
            )
            if not run:
                records.append(
                    {
                        **applied,
                        "command": command,
                        "returncode": None,
                        "configuration_audit": configuration_audit,
                    }
                )
                continue
            started = time.monotonic()
            completed = subprocess.run(command, cwd=str(case))
            requested_after_stage = stop_marker.is_file()
            records.append(
                {
                    **applied,
                    "command": command,
                    "returncode": completed.returncode,
                    "wall_seconds": time.monotonic() - started,
                    "configuration_audit": configuration_audit,
                    "status": (
                        "PAUSED_RESTARTABLE"
                        if requested_after_stage
                        else "COMPLETED"
                        if completed.returncode == 0
                        else "FAILED"
                    ),
                }
            )
            if requested_after_stage:
                stop_requested = True
                break
            if completed.returncode != 0:
                break
        status = (
            "DRY_RUN_READY"
            if not run
            else "PAUSED_RESTARTABLE"
            if stop_requested
            else "COMPLETED"
            if records and all(row["returncode"] == 0 for row in records)
            else "FAILED"
        )
        write_json_atomic(
            run_root / "execution_status.json",
            {"status": status, "stages": records, "updated_at": utc_stamp()},
        )
        upsert_execution(
            project_root,
            {
                "run_id": entry["run_id"],
                "mode": "PIMPLE_SENSITIVITY",
                "topology": entry["topology"],
                "mesh_level": entry["mesh_level"],
                "mesh_id": entry["mesh_id"],
                "stage": records[-1].get("stage") if records else "PREPARED",
                "status": status,
                "case_path": str(case),
                "deltaT": entry["dt_s"],
                "nOuterCorrectors": entry["nOuterCorrectors"],
            },
            activate=True,
        )
        results.append({"run_id": entry["run_id"], "status": status})
        if stop_requested:
            break
    manifest.update(
        status=(
            "DRY_RUN_READY"
            if not run
            else "PAUSED_RESTARTABLE"
            if stop_requested
            else "EXECUTION_FINISHED"
        ),
        results=results,
        updated_at=utc_stamp(),
    )
    write_json_atomic(root / "pimple_outer_study_manifest.json", manifest)
    write_json_atomic(root / "study_manifest.json", manifest)
    return manifest


def analyze_study(project_root: Path) -> dict[str, Any]:
    root = active_workspace_root(Path(project_root).resolve()) / "pimple_outer_study"
    manifest = read_json(
        root / "pimple_outer_study_manifest.json",
        read_json(root / "study_manifest.json", {}) or {},
    ) or {}
    metrics: list[dict[str, Any]] = []
    histories: dict[int, Any] = {}
    for entry in manifest.get("entries", []):
        run_root = root / entry["run_id"]
        try:
            summary = analyze_run(run_root)
        except (FileNotFoundError, ValueError):
            continue
        if summary.get("status") != "COMPLETED":
            continue
        force_metrics = summary.get("metrics") or {}
        cl_metrics = force_metrics.get("CL") or {}
        cd_metrics = force_metrics.get("CD") or {}
        cm_metrics = force_metrics.get("CM") or {}
        cl_mode = (summary.get("dominant_modes") or {}).get("CL") or {}
        metrics.append(
            {
                "nOuterCorrectors": entry["nOuterCorrectors"],
                "mean_CL": cl_metrics.get("mean"),
                "mean_CD": cd_metrics.get("mean"),
                "mean_CM": cm_metrics.get("mean"),
                "rms_CL": cl_metrics.get("rms"),
                "dominant_strouhal": cl_mode.get("strouhal"),
                "cpu_seconds_per_step": summary.get("cpu_seconds_per_step"),
                "continuity_stable": summary.get("continuity_stable"),
                "pimple_converged": summary.get("pimple_converged"),
            }
        )
        history_path = run_root / "time_history.csv"
        if history_path.is_file():
            try:
                import pandas as pd
                history = pd.read_csv(history_path)
                time_name = next((name for name in ("Time", "time", "time_s") if name in history.columns), None)
                if time_name:
                    histories[int(entry["nOuterCorrectors"])] = (history, time_name)
            except (OSError, ValueError):
                pass
    comparison = compare_pimple_outer_correctors(metrics)
    result = {
        "status": comparison.get("status", "NOT_AVAILABLE"),
        "runs": metrics,
        "comparison": comparison,
        "interpretation": {
            "iterative_convergence_improvement": True,
            "physical_solution_change": (
                "Assess force means, RMS and dominant frequency; residual reduction "
                "alone is not a favourable physical change."
            ),
            "computational_cost_increase": True,
            "recommendation_rule": (
                "Recommend 2 only when it matches 3/4 within laboratory tolerances "
                "and converges per step; otherwise retain 3. Use 4 as sensitivity reference."
            ),
        },
        "updated_at": utc_stamp(),
    }
    reports = active_workspace_root(project_root) / "postprocess/pimple"
    reports.mkdir(parents=True, exist_ok=True)
    write_json_atomic(reports / "pimple_outer_2_3_4_comparison.json", result)
    if metrics:
        csv_path = reports / "pimple_outer_comparison.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
            writer.writeheader()
            writer.writerows(metrics)

        outer = [int(row["nOuterCorrectors"]) for row in metrics]

        if len(histories) >= 2:
            overlap_start = max(float(frame[time_name].min()) for frame, time_name in histories.values())
            overlap_end = min(float(frame[time_name].max()) for frame, time_name in histories.values())
            if overlap_end > overlap_start:
                fig, axes = plt.subplots(3, 1, figsize=(9.0, 8.0), sharex=True)
                plotted = False
                for axis, candidates, label in zip(
                    axes,
                    (("Cl", "CL"), ("Cd", "CD"), ("Cm", "CM")),
                    ("Cl", "Cd", "Cm"),
                ):
                    collected: list[float] = []
                    for n_outer, (frame, time_name) in sorted(histories.items()):
                        column = next((name for name in candidates if name in frame.columns), None)
                        if column is None:
                            continue
                        selected = frame[
                            (frame[time_name] >= overlap_start) & (frame[time_name] <= overlap_end)
                        ]
                        if selected.empty:
                            continue
                        axis.plot(selected[time_name], selected[column], lw=0.9, label=f"nOuter={n_outer}")
                        collected.extend(pd.to_numeric(selected[column], errors="coerce").dropna().tolist())
                        plotted = True
                    if collected:
                        lower, upper = min(collected), max(collected)
                        span = upper - lower
                        pad = 0.25 * span if span > 0.0 else 0.25 * max(abs(lower), 1.0e-6)
                        axis.set_ylim(lower - pad, upper + pad)
                    axis.set_ylabel(label)
                    axis.grid(alpha=0.25)
                    axis.legend(fontsize=8)
                axes[-1].set_xlabel("Physical time [s]")
                axes[0].set_title("PIMPLE 2/3/4: coefficients over the same final-time interval")
                fig.tight_layout()
                if plotted:
                    save_scientific_figure(
                        fig,
                        reports / "pimple_outer_coefficients_common_final_period.png",
                        data={
                            "overlap_start_s": overlap_start,
                            "overlap_end_s": overlap_end,
                            "nOuterCorrectors": sorted(histories),
                        },
                        metadata={
                            "source": "PIMPLE 2/3/4 real force histories",
                            "y_scale": "observed min/max plus 25 percent of observed span",
                        },
                    )
                else:
                    plt.close(fig)

        def plot_values(
            filename: str,
            keys: list[str],
            ylabel: str,
        ) -> None:
            fig, axis = plt.subplots(figsize=(7.2, 4.2))
            plotted = False
            for key in keys:
                values = [row.get(key) for row in metrics]
                if any(value is None for value in values):
                    continue
                axis.plot(outer, values, marker="o", label=key)
                plotted = True
            if not plotted:
                plt.close(fig)
                return
            axis.set_xlabel("nOuterCorrectors")
            axis.set_ylabel(ylabel)
            axis.set_xticks(outer)
            axis.grid(True, alpha=0.25)
            if len(keys) > 1:
                axis.legend()
            fig.tight_layout()
            save_scientific_figure(
                fig,
                reports / filename,
                data=metrics,
                metadata={
                    "source": "PIMPLE 2/3/4 sensitivity study",
                    "transformation": f"Plot {', '.join(keys)} against nOuterCorrectors",
                },
            )

        for key, label in (
            ("mean_CL", "Mean CL"),
            ("mean_CD", "Mean CD"),
            ("mean_CM", "Mean Cm"),
            ("rms_CL", "RMS CL"),
        ):
            plot_values(f"pimple_outer_{key}.png", [key], label)
        plot_values(
            "pimple_outer_frequency.png",
            ["dominant_strouhal"],
            "Dominant Strouhal number",
        )
        plot_values(
            "pimple_outer_cost.png",
            ["cpu_seconds_per_step"],
            "CPU seconds per step",
        )
        baseline = next(
            (row for row in metrics if int(row["nOuterCorrectors"]) == 2),
            None,
        )
        if baseline is not None:
            relative_rows: list[dict[str, Any]] = []
            for row in metrics:
                for key in (
                    "mean_CL", "mean_CD", "mean_CM", "rms_CL",
                    "dominant_strouhal", "cpu_seconds_per_step",
                ):
                    value = row.get(key)
                    reference = baseline.get(key)
                    if value is None or reference in {None, 0}:
                        continue
                    relative_rows.append({
                        "nOuterCorrectors": int(row["nOuterCorrectors"]),
                        "metric": key,
                        "relative_to_nOuter2_percent": (
                            100.0 * (float(value) - float(reference))
                            / abs(float(reference))
                        ),
                    })
            if relative_rows:
                write_json_atomic(
                    reports / "pimple_relative_to_nOuter2.json",
                    {"reference": 2, "rows": relative_rows},
                )
                for key in sorted({row["metric"] for row in relative_rows}):
                    selected = [row for row in relative_rows if row["metric"] == key]
                    fig, axis = plt.subplots(figsize=(6.4, 3.8))
                    axis.bar(
                        [row["nOuterCorrectors"] for row in selected],
                        [row["relative_to_nOuter2_percent"] for row in selected],
                        color="#4472c4",
                    )
                    axis.axhline(0.0, color="black", linewidth=0.7)
                    axis.set(
                        xlabel="nOuterCorrectors",
                        ylabel="Difference from nOuter=2 [%]",
                        title=f"{key}: relative change from nOuter=2",
                    )
                    axis.set_xticks([2, 3, 4])
                    axis.grid(True, axis="y", alpha=0.25)
                    fig.tight_layout()
                    save_scientific_figure(
                        fig,
                        reports / f"pimple_relative_{key}_vs_nOuter2.png",
                        data=selected,
                        metadata={
                            "source": "PIMPLE 2/3/4 sensitivity study",
                            "reference": "nOuterCorrectors=2",
                        },
                    )
        # A truthful categorical convergence view; no synthetic residual values.
        fig, axis = plt.subplots(figsize=(7.2, 3.8))
        values = [
            1.0 if row.get("pimple_converged") else 0.0
            for row in metrics
        ]
        axis.bar(outer, values, color="#4472c4")
        axis.set(
            xlabel="nOuterCorrectors",
            ylabel="Per-step convergence",
            yticks=[0, 1],
            yticklabels=["FAIL/unknown", "PASS"],
        )
        axis.grid(True, axis="y", alpha=0.25)
        fig.tight_layout()
        save_scientific_figure(
            fig,
            reports / "pimple_outer_residuals.png",
            data=metrics,
            metadata={
                "source": "PIMPLE 2/3/4 sensitivity study",
                "transformation": "Boolean per-step convergence encoded as 0/1",
            },
        )
        write_json_atomic(
            reports / "pimple_residual_reduction_availability.json",
            {
                "status": "AVAILABLE_ONLY_WHEN_OUTER_ITERATION_RESIDUALS_ARE_LOGGED",
                "synthetic_values_used": False,
                "note": (
                    "The boolean per-step criterion is retained. Reduction order "
                    "is not fabricated when OpenFOAM logs do not identify residuals "
                    "by outer-corrector index."
                ),
            },
        )

        report_lines = [
            "# PIMPLE nOuterCorrectors sensitivity",
            "",
            f"- Status: `{result['status']}`",
            "- Common mesh: `closed_coarse`",
            "- Independent clones: `nOuterCorrectors = 2, 3, 4`",
            "- Reference: highest available nOuterCorrectors.",
            "",
            "The study changes only nOuterCorrectors. Missing physical "
            "histories are omitted rather than replaced with placeholders.",
        ]
        (reports / "pimple_outer_report.md").write_text(
            "\n".join(report_lines) + "\n", encoding="utf-8"
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id")
    prepare.add_argument("--topology", choices=["closed", "open"], required=True)
    prepare.add_argument("--mesh-level", choices=["coarse", "medium", "fine"], required=True)
    prepare.add_argument("--dt-s", type=float)
    execute = sub.add_parser("execute")
    execute.add_argument("--run", action="store_true")
    resume = sub.add_parser("resume")
    resume.add_argument("--run", action="store_true")
    sub.add_parser("analyze")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "prepare":
        result = prepare_study(
            args.project_root,
            run_id=args.run_id,
            topology=args.topology,
            mesh_level=args.mesh_level,
            dt_s=args.dt_s,
        )
    elif args.action in {"execute", "resume"}:
        result = execute_study(
            args.project_root,
            run=args.run,
            resume=args.action == "resume",
        )
    else:
        result = analyze_study(args.project_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
