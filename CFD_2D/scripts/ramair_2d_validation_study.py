#!/usr/bin/env python3
"""CLI/backend for the isolated closed/open validation convergence lab."""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from ramair_2d_convergence_analysis import deterministic_run_id
from ramair_2d_openfoam_case_writer import (
    OpenFOAMCaseConfig,
    case_input_summary,
    parse_boundary_patches,
    write_0,
    write_constant,
    write_system,
)
from ramair_2d_mesh_numerics import (
    quality_controls_for_mesh,
    quality_controls_from_paths,
)
from ramair_2d_study_registry import (
    MESH_IDS,
    active_workspace_root,
    archive_existing,
    hardlink_tree,
    initialize_study,
    load_study,
    read_json,
    results_study_root,
    select_mesh,
    set_run_matrix_preset,
    sha256_json,
    update_run_status,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_temporal_budget import (
    build_reference_dt_table,
    operating_condition,
    temporal_computational_budget,
)
from ramair_2d_validation_report import (
    analyze_checkpoint,
    analyze_run,
    generate_study_report,
)
from ramair_2d_rans_checkpoint_batch import (
    RansCheckpointBlocked,
    checkpoint_table,
    create_reviewed_checkpoint,
    delete_active_base,
    execute_base,
    execute_queue,
    execute_selection_queue,
    mesh_angle_id,
    require_compatible_checkpoint,
)
from ramair_2d_rans_review import (
    RANS_REJECTED,
    RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
    accept_current_six_bases,
    generate_review_diagnostics,
    migrate_existing_bases,
    review_table,
    revoke_review,
    set_review,
)
from ramair_2d_execution_registry import (
    filtered_runs,
    migrate_known_executions,
    set_active_execution,
    upsert_execution,
)
from ramair_2d_storage_inventory import (
    clean_active_volumetric_products,
    generate_storage_inventory,
)
from ramair_2d_pimple_outer_study import (
    analyze_study as analyze_pimple_study,
    execute_study as execute_pimple_study,
    prepare_study as prepare_pimple_study,
)
from ramair_2d_open_light_candidate import (
    cleanup_rejected_candidates,
    evaluate_sweep as evaluate_open_light_sweep,
    execute_sweep as execute_open_light_sweep,
    prepare_sweep as prepare_open_light_sweep,
)
from ramair_2d_open_mesh_refinement import (
    evaluate_refinement as evaluate_open_refinement,
    execute_refinement as execute_open_refinement,
    prepare_refinement as prepare_open_refinement,
    promote_refinement as promote_open_refinement,
)
from ramair_2d_urans_cases import (
    CanonicalCaseError,
    ExecutionOutcome,
    calculated_action,
    canonical_case_root,
    case_id_from_row,
    compatibility_hashes,
    create_quick_check_sandbox,
    finalize_quick_check,
    inspect_canonical_case,
    restart_canonical_case,
    write_case_manifest,
)
from ramair_2d_validation_staged_runner import (
    configure_stage,
    repair_legacy_classification,
    runner_command,
)
from ramair_2d_checkpoint_integrity import (
    checkpoint_mesh_identity,
    copied_checkpoint_matches,
)


CLOSED_PROBES = {
    "upper_te": [0.98, 0.02, 0.5],
    "lower_te": [0.98, -0.02, 0.5],
    "wake_1p02": [1.02, 0.0, 0.5],
    "wake_1p10": [1.10, 0.0, 0.5],
    "wake_1p50": [1.50, 0.0, 0.5],
}

OPEN_PROBES = {
    "exterior_upper_lip": [0.02, 0.06, 0.5],
    "upper_shear_layer": [0.06, 0.035, 0.5],
    "exterior_lower_lip": [0.02, -0.04, 0.5],
    "lower_shear_layer": [0.06, -0.02, 0.5],
    "inlet_center": [0.03, 0.0, 0.5],
    "near_cavity": [0.12, 0.0, 0.5],
    "deep_cavity": [0.50, 0.0, 0.5],
    "upper_after_inlet": [0.12, 0.08, 0.5],
    "lower_after_inlet": [0.12, -0.05, 0.5],
    "near_wake": [1.10, 0.0, 0.5],
    "medium_wake": [1.50, 0.0, 0.5],
}

_MONITOR_ONLY_PLACEHOLDER_FILES = {
    "case/.validation_monitor_rans_cache.json",
    "case/validation_live_monitor_rans.json",
}


def _remove_monitor_only_placeholder(run_root: Path) -> bool:
    """Remove derived monitor caches that cannot constitute a prepared case."""
    if not run_root.is_dir():
        return False
    entries = [
        path
        for path in run_root.rglob("*")
        if path.is_file() or path.is_symlink()
    ]
    relative_files = {
        path.relative_to(run_root).as_posix() for path in entries
    }
    if not relative_files.issubset(_MONITOR_ONLY_PLACEHOLDER_FILES):
        return False
    for path in entries:
        path.unlink(missing_ok=True)
    for directory in sorted(
        (path for path in run_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.rmdir()
    run_root.rmdir()
    return True


def _find_run(study: dict[str, Any], run_id: str) -> dict[str, Any]:
    for row in study.get("run_matrix", {}).get("runs", []):
        if row.get("run_id") == run_id:
            return row
    raise KeyError(f"Unknown validation-study run: {run_id}")


def _find_mesh(study: dict[str, Any], mesh_id: str) -> dict[str, Any]:
    for row in study.get("mesh_registry", {}).get("meshes", []):
        if row.get("id") == mesh_id:
            return row
    raise KeyError(f"Unknown validation-study mesh: {mesh_id}")


def _run_root(project_root: Path, row: dict[str, Any]) -> Path:
    return canonical_case_root(project_root, row)


def _checkpoint_root(project_root: Path, mesh_id: str) -> Path:
    return active_workspace_root(project_root) / "checkpoints" / mesh_id


def _copy_case_inputs(source: Path, destination: Path) -> None:
    """Copy mutable case inputs while sharing only the immutable polyMesh."""
    if destination.exists():
        raise FileExistsError(destination)
    for name in ("0", "constant", "system"):
        source_part = source / name
        destination_part = destination / name
        if not source_part.exists():
            continue
        if name != "constant":
            shutil.copytree(source_part, destination_part)
            continue
        destination_part.mkdir(parents=True, exist_ok=True)
        for child in source_part.iterdir():
            if child.name == "polyMesh":
                hardlink_tree(child, destination_part / child.name)
            elif child.is_dir():
                shutil.copytree(child, destination_part / child.name)
            else:
                shutil.copy2(child, destination_part / child.name)


def prepare_checkpoint(
    project_root: Path,
    mesh_id: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Prepare one common SIMPLE checkpoint case for a mesh.

    The source must be one already prepared canonical run for the same mesh.
    This keeps checkpoint preparation deterministic without relying on a
    persistent matrix-selection flag.
    """
    study = load_study(project_root)
    base_mesh_id = str(mesh_id).split("__alpha_", 1)[0]
    mesh = _find_mesh(study, base_mesh_id)
    candidates = [
        row for row in study["run_matrix"]["runs"]
        if row["mesh_id"] == base_mesh_id
        and (_run_root(project_root, row) / "case/system/controlDict").is_file()
    ]
    if not candidates:
        raise RuntimeError(
            f"{mesh_id}: prepare at least one canonical dry-run case before its checkpoint"
        )
    matching_angle = [
        row for row in candidates
        if mesh_angle_id(
            study,
            base_mesh_id,
            float(row.get("alpha_deg", 0.0)),
        ) == mesh_id
    ]
    source_row = sorted(
        matching_angle or candidates,
        key=lambda row: (float(row["dt_s"]), row["run_id"]),
    )[0]
    source_case = _run_root(project_root, source_row) / "case"
    checkpoint = _checkpoint_root(project_root, mesh_id)
    if checkpoint.exists():
        if not overwrite:
            current = read_json(checkpoint / "checkpoint_manifest.json", {}) or {}
            if current.get("mesh_hash") == mesh["mesh_hash"]:
                return current
            raise FileExistsError(
                f"Checkpoint exists for a different mesh state: {checkpoint}"
            )
        current = read_json(checkpoint / "checkpoint_manifest.json", {}) or {}
        preserve_solver_evidence = bool(current.get("solver_executed")) or str(
            current.get("status") or ""
        ) in {"CHECKPOINT_READY", "DIAGNOSTIC_CHECKPOINT"}
        if preserve_solver_evidence:
            archive_existing(
                checkpoint,
                active_workspace_root(project_root) / "checkpoints/overwritten",
            )
        else:
            shutil.rmtree(checkpoint)
    case = checkpoint / "case"
    checkpoint.mkdir(parents=True, exist_ok=True)
    _copy_case_inputs(source_case, case)
    target_alpha = next(
        float(row.get("alpha_deg", 0.0))
        for row in study["run_matrix"]["runs"]
        if row["mesh_id"] == base_mesh_id
        and mesh_angle_id(
            study,
            base_mesh_id,
            float(row.get("alpha_deg", 0.0)),
        ) == mesh_id
    )
    source_config = read_json(source_case / "case_config.json", {}) or {}
    if not source_config:
        source_config = read_json(source_case / "case_input_summary.json", {}) or {}
    config_fields = OpenFOAMCaseConfig.__dataclass_fields__
    config_values = {
        name: source_config[name]
        for name in config_fields
        if name in source_config
    }
    config_values["alpha_deg"] = target_alpha
    cfg = OpenFOAMCaseConfig(**config_values)
    patches = parse_boundary_patches(case / "constant/polyMesh")
    shutil.rmtree(case / "0", ignore_errors=True)
    write_0(case, cfg, patches)
    write_constant(case, cfg)
    write_system(case, cfg, patches)
    manifest = {
        "schema_version": 1,
        "checkpoint_id": f"{mesh_id}_simple",
        "mesh_id": mesh_id,
        "mesh_hash": mesh["mesh_hash"],
        "topology": mesh["topology"],
        "mesh_level": mesh["level"],
        "alpha_deg": target_alpha,
        "source_run_id": source_row["run_id"],
        "source_alpha_deg": float(source_row.get("alpha_deg", 0.0)),
        "initial_conditions_retargeted_to_alpha_deg": target_alpha,
        "source_case": str(source_case),
        "case": str(case),
        "status": "READY_TO_RUN",
        "required_restart_fields": ["U", "p", "nuTilda"],
        "optional_restart_fields": ["phi"],
        "shared_by_all_dt_for_mesh": True,
        "prepared_at": utc_stamp(),
        "solver_executed": False,
    }
    write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
    return manifest


def execute_checkpoint(
    project_root: Path,
    mesh_id: str,
    *,
    run: bool,
    overwrite: bool = False,
) -> int:
    result = execute_base(
        project_root,
        mesh_id,
        run=run,
        overwrite=overwrite,
    )
    generate_storage_inventory(project_root)
    return 0 if result.get("status") not in {
        "RANS_BASE_FAILED",
        "RANS_BASE_DIVERGED",
    } else 2


def _plan_config_for_topology(
    study_config: dict[str, Any], topology: str
) -> tuple[dict[str, Any], str]:
    plan_config = copy.deepcopy(study_config)
    active_package = str(
        (study_config.get("temporal_packages") or {}).get("active") or "reference"
    )
    if active_package.startswith("cummings_"):
        validation = plan_config["validation_study"]
        urans = dict(validation.get("urans") or {})
        urans["sampling_time_star"] = 100.0 if topology == "closed" else 200.0
        urans["temporal_package"] = active_package
        if topology == "open":
            # The open-cavity meshes contain much smaller cells at both lips.
            # A quarter-target first step produced local Co~9 and a nuTilda
            # runaway in real startup tests. Keep the convergence target dt
            # fixed, but approach it through a conservative open-only ramp.
            safe_factors = (0.02, 0.05, 0.10)
            startup = copy.deepcopy(list(urans.get("startup_stages") or []))
            for stage, factor in zip(startup, safe_factors):
                stage["dt_factor"] = factor
                stage["stability_basis"] = "open_lip_minimum_cell_courant_startup"
            urans["startup_stages"] = startup
            urans["startup_profile"] = "open_cavity_conservative_0p02_0p05_0p10"
        validation["urans"] = urans
    return plan_config, active_package


def _stage_plan(
    *,
    dt_s: float,
    condition: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    validation = config["validation_study"]
    urans = dict(validation.get("urans") or {})
    configured_startup = list(urans.get("startup_stages") or [])
    stages: list[dict[str, Any]] = []
    warnings: list[str] = []
    cursor_decimal = Decimal("0")
    cursor_s = 0.0
    if configured_startup:
        for source in configured_startup:
            if not bool(source.get("enabled", True)):
                continue
            factor = float(source["dt_factor"])
            duration_mode = str(
                source.get("duration_mode") or "steps"
            ).lower()
            stage_dt_decimal = Decimal(str(dt_s)) * Decimal(str(factor))
            stage_dt = float(stage_dt_decimal)
            duration = float(
                source.get("duration", source.get("steps", 0))
            )
            if factor <= 0.0 or duration <= 0.0:
                raise ValueError(
                    "URANS startup dt_factor and duration must be positive"
                )
            if duration_mode == "steps":
                steps = int(round(duration))
                duration_decimal = stage_dt_decimal * Decimal(steps)
                duration_s = float(duration_decimal)
            elif duration_mode in {"t_star", "tc", "t*"}:
                duration_s = duration * condition["tc_s"]
                steps = math.ceil(duration_s / stage_dt)
                duration_decimal = stage_dt_decimal * Decimal(steps)
                duration_s = float(duration_decimal)
            else:
                raise ValueError(
                    "URANS stage duration_mode must be steps or t_star"
                )
            stages.append({
                "stage": str(source["name"]),
                "purpose": str(
                    source.get("purpose") or "fixed-dt startup"
                ),
                "scheme": str(source.get("scheme") or "Euler"),
                "dt_factor": factor,
                "dt_s": stage_dt,
                "dt_star": stage_dt / condition["tc_s"],
                "start_s": float(cursor_decimal),
                "duration_s": duration_s,
                "end_s": float(cursor_decimal + duration_decimal),
                "start_tc": float(cursor_decimal) / condition["tc_s"],
                "duration_tc": duration_s / condition["tc_s"],
                "end_tc": float(cursor_decimal + duration_decimal) / condition["tc_s"],
                "steps": steps,
                "duration_mode": duration_mode,
                "configured_duration": duration,
                "sampling": False,
            })
            cursor_decimal += duration_decimal
            cursor_s = float(cursor_decimal)
    else:
        factors = [float(value) for value in validation["startup_factors"]]
        durations = [
            float(value) for value in validation["startup_duration_tc"]
        ]
        if factors != [0.25, 0.5, 1.0] or len(durations) != 3:
            raise ValueError(
                "The legacy laboratory contract requires 0.25/0.5/1.0 startup"
            )
        cursor_tc = 0.0
        for name, factor, duration in zip(("A", "B", "C"), factors, durations):
            stages.append({
                "stage": name,
                "purpose": "fixed-dt startup",
                "scheme": "Euler",
                "dt_factor": factor,
                "dt_s": dt_s * factor,
                "dt_star": dt_s * factor / condition["tc_s"],
                "start_tc": cursor_tc,
                "duration_tc": duration,
                "end_tc": cursor_tc + duration,
                "sampling": False,
            })
            cursor_tc += duration
        for stage in stages:
            stage["start_s"] = stage["start_tc"] * condition["tc_s"]
            stage["end_s"] = stage["end_tc"] * condition["tc_s"]
            stage["duration_s"] = stage["duration_tc"] * condition["tc_s"]
            stage["steps"] = math.ceil(
                stage["duration_s"] / stage["dt_s"]
            )
        cursor_s = stages[-1]["end_s"]
    settling_value = urans.get("settling_time_star")
    sampling_value = urans.get("sampling_time_star")
    settling = float(
        validation["settling_tc"]
        if settling_value is None
        else settling_value
    )
    sampling = float(
        validation["sampling_tc"]
        if sampling_value is None
        else sampling_value
    )
    production_scheme = str(
        urans.get("production_scheme", validation.get("production_scheme", "backward"))
    )
    cursor_tc = cursor_s / condition["tc_s"]
    stages.extend([
        {
            "stage": "D",
            "purpose": "production-scheme settling",
            "scheme": production_scheme,
            "dt_s": dt_s,
            "dt_star": dt_s / condition["tc_s"],
            "start_tc": cursor_tc,
            "duration_tc": settling,
            "end_tc": cursor_tc + settling,
            "sampling": False,
        },
        {
            "stage": "E",
            "purpose": "accepted sampling window",
            "scheme": production_scheme,
            "dt_s": dt_s,
            "dt_star": dt_s / condition["tc_s"],
            "start_tc": cursor_tc + settling,
            "duration_tc": sampling,
            "end_tc": cursor_tc + settling + sampling,
            "sampling": True,
        },
    ])
    for stage in stages[-2:]:
        stage["start_s"] = stage["start_tc"] * condition["tc_s"]
        stage["end_s"] = stage["end_tc"] * condition["tc_s"]
        stage["duration_s"] = stage["duration_tc"] * condition["tc_s"]
        stage["steps"] = math.ceil(stage["duration_s"] / stage["dt_s"])
    startup_target_steps = sum(
        int(stage["steps"])
        for stage in stages
        if stage["stage"] in {"A", "B", "C"}
        and math.isclose(float(stage.get("dt_factor", 0.0)), 1.0)
    )
    if startup_target_steps <= 0:
        warnings.append("NO_TARGET_DT_HISTORY_BEFORE_PRODUCTION_SCHEME")
    if any(
        str(stage["scheme"]).lower().startswith("backward")
        for stage in stages[:3]
    ):
        warnings.append("BACKWARD_USED_DURING_STARTUP")
    retained_snapshots = max(0, int(
        urans.get("retained_snapshots", validation["retained_snapshots"])
    ))
    write_interval_s = float(validation["field_write_interval_tc"]) * condition["tc_s"]
    for stage in stages:
        stage["purge_write"] = retained_snapshots
        stage["write_interval_s"] = write_interval_s
    return {
        "schema_version": 2,
        "time_policy": "fixed_staged",
        "adjustTimeStep": False,
        "target_dt_s": dt_s,
        "target_dt_star": dt_s / condition["tc_s"],
        "stages": stages,
        "sampling_start_s": stages[-1]["start_s"],
        "sampling_end_s": stages[-1]["end_s"],
        "steps_total": sum(int(stage["steps"]) for stage in stages),
        "startup_data_excluded_from_sampling": True,
        "requires_current_and_two_previous_target_dt_states_before_backward": True,
        "configuration_warnings": warnings,
    }


def _probe_contract(topology: str) -> dict[str, Any]:
    probes = OPEN_PROBES if topology == "open" else CLOSED_PROBES
    return {
        "coordinate_system": "normalized x/c, y/c, span fraction",
        "validation_status": "REQUIRES_POINT_IN_FLUID_CHECK_BEFORE_RUN",
        "policy": "A point outside the fluid must be reported, never moved silently.",
        "probes": [
            {"name": name, "coordinates_normalized": coordinates, "inside_fluid": None}
            for name, coordinates in probes.items()
        ],
    }


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
                return text[opening + 1:index]
    return None


def _foam_entry(text: str, name: str) -> str | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(name)}\s+([^;]+);",
        text,
    )
    return match.group(1).strip() if match else None


def _same_applied_value(actual: Any, expected: Any) -> bool:
    try:
        return math.isclose(
            float(actual),
            float(expected),
            # OpenFOAM dictionaries are emitted with 12 significant digits.
            # The half-unit formatting error can approach 5e-10 relatively.
            rel_tol=5.0e-10,
            abs_tol=1.0e-14,
        )
    except (TypeError, ValueError):
        return str(actual).strip().lower() == str(expected).strip().lower()


def audit_prepared_urans_case(
    case: Path,
    cfg: OpenFOAMCaseConfig,
    output_path: Path,
) -> dict[str, Any]:
    """Verify the generated dictionaries before allowing execution."""
    control = (case / "system/controlDict").read_text(encoding="utf-8")
    schemes = (case / "system/fvSchemes").read_text(encoding="utf-8")
    solution = (case / "system/fvSolution").read_text(encoding="utf-8")
    ddt = _foam_dictionary(schemes, "ddtSchemes") or ""
    pimple = _foam_dictionary(solution, "PIMPLE") or ""
    actual = {
        "adjustTimeStep": _foam_entry(control, "adjustTimeStep"),
        "deltaT": _foam_entry(control, "deltaT"),
        "ddt_scheme": _foam_entry(ddt, "default"),
        "nOuterCorrectors": _foam_entry(pimple, "nOuterCorrectors"),
        "nCorrectors": _foam_entry(pimple, "nCorrectors"),
        "nNonOrthogonalCorrectors": _foam_entry(
            pimple,
            "nNonOrthogonalCorrectors",
        ),
    }
    expected = {
        "adjustTimeStep": "no",
        "deltaT": float(cfg.deltaT),
        "ddt_scheme": str(cfg.ddt_scheme),
        "nOuterCorrectors": int(cfg.n_outer_correctors),
        "nCorrectors": int(cfg.n_correctors),
        "nNonOrthogonalCorrectors": int(
            cfg.n_non_orthogonal_correctors
        ),
    }
    rows = [
        {
            "parameter": name,
            "selected": expected[name],
            "applied": actual[name],
            "matches": _same_applied_value(actual[name], expected[name]),
        }
        for name in expected
    ]
    audit = {
        "schema_version": 1,
        "status": (
            "CONFIGURATION_APPLIED"
            if all(row["matches"] for row in rows)
            else "CONFIGURATION_MISMATCH"
        ),
        "case": str(case),
        "rows": rows,
        "checked_at": utc_stamp(),
    }
    write_json_atomic(output_path, audit)
    if audit["status"] != "CONFIGURATION_APPLIED":
        failures = [row for row in rows if not row["matches"]]
        raise RuntimeError(
            f"Prepared URANS dictionary mismatch: {failures}"
        )
    return audit


def prepare_run(
    project_root: Path,
    run_id: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    study = load_study(project_root)
    if not study:
        study = initialize_study(project_root)
    row = _find_run(study, run_id)
    mesh = _find_mesh(study, str(row["mesh_id"]))
    if mesh.get("checkMesh_status") != "OK" or not mesh.get("frontAndBack_empty"):
        update_run_status(project_root, run_id, "REJECTED_MESH")
        raise RuntimeError(f"{run_id}: source mesh is not eligible")
    checkpoint_id = mesh_angle_id(
        study, str(row["mesh_id"]), float(row.get("alpha_deg", 0.0))
    )
    checkpoint = require_compatible_checkpoint(project_root, checkpoint_id)
    checkpoint_case = _checkpoint_root(project_root, checkpoint_id) / "case"
    checkpoint_zero = Path(str(checkpoint.get("restart_zero") or ""))
    checkpoint_identity = checkpoint_mesh_identity(checkpoint_case, checkpoint_zero)
    if checkpoint_identity.get("status") != "READY":
        raise RuntimeError(
            f"{run_id}: RANS checkpoint mesh/field integrity failed: {checkpoint_identity}"
        )
    checkpoint_mesh_hash = str(checkpoint_identity["poly_mesh_hash"])
    run_root = _run_root(project_root, row)
    if run_root.exists():
        prepared_control = run_root / "case/system/controlDict"
        existing_state = inspect_canonical_case(project_root, row)
        existing_manifest = read_json(run_root / "case_manifest.json", {}) or {}
        existing_mesh_matches = str(existing_manifest.get("mesh_hash") or "") == checkpoint_mesh_hash
        if prepared_control.is_file() and not overwrite and existing_mesh_matches:
            metadata = read_json(run_root / "case_metadata.json", {}) or {}
            return {
                **metadata,
                "case_id": case_id_from_row(row),
                "preparation_status": "REUSED_EXISTING_CANONICAL_CASE",
                "case_state": existing_state,
            }
        if existing_state["case_presence"] == "STARTED":
            raise CanonicalCaseError(
                "STARTED_CASE_CANNOT_BE_REPREPARED",
                f"{existing_state['case_id']} already contains physical time.",
                remediation=(
                    "Resume it, or use the explicit exact-ID restart action."
                ),
                evidence=existing_state,
            )
        if overwrite or not existing_mesh_matches or _remove_monitor_only_placeholder(run_root):
            if run_root.exists():
                shutil.rmtree(run_root)
        elif any(run_root.iterdir()):
            raise CanonicalCaseError(
                "INCOMPLETE_CANONICAL_DEFINITION",
                f"Incomplete canonical definition exists at {run_root}.",
                remediation=(
                    "Use prepare with overwrite before the solver has started."
                ),
                evidence=existing_state,
            )
    case = run_root / "case"
    case.mkdir(parents=True, exist_ok=True)
    source_poly = Path(str(checkpoint_identity["poly_mesh"]))
    hardlink_tree(source_poly, case / "constant/polyMesh")
    patches = parse_boundary_patches(case / "constant/polyMesh")

    study_config = study["study_config"]
    condition = operating_condition(study_config["operating_condition"])
    topology = str(row["topology"])
    plan_config, active_temporal_package = _plan_config_for_topology(
        study_config, topology
    )
    validation = plan_config["validation_study"]
    urans = dict(validation.get("urans") or {})
    dt_s = float(row["dt_s"])
    plan = _stage_plan(dt_s=dt_s, condition=condition, config=plan_config)
    plan["production_duration_source"] = (
        f"{active_temporal_package}:{topology}"
        if active_temporal_package.startswith("cummings_")
        else "user_or_study_configuration"
    )
    checkpoint_case_config = read_json(checkpoint_case / "case_config.json", {}) or {}
    mesh_config = read_json(
        Path(mesh["mesh_package"]) / "Configurations/cfd2d_mesh_config.json", {}
    ) or {}
    spanwise = float(
        checkpoint_case_config.get(
            "spanwise_thickness_chord",
            mesh_config.get("spanwise_thickness_chord", 0.01),
        )
    )
    mesh_quality_controls = quality_controls_for_mesh(Path(mesh["mesh_package"]))
    if mesh_quality_controls is None:
        mesh_quality_controls = quality_controls_from_paths([
            checkpoint_case / "mesh_quality_report.json",
            checkpoint_case / "log.checkMesh.preRun",
            checkpoint_case / "log.checkMesh",
        ])
    if mesh_quality_controls is None:
        raise RuntimeError(
            f"{run_id}: automatic non-orthogonal controls require the checkMesh "
            "report belonging to the selected mesh package"
        )
    automatic_correctors = int(
        mesh_quality_controls["n_non_orthogonal_correctors"]
    )
    automatic_laplacian = str(mesh_quality_controls["laplacian_scheme"])
    cfg = OpenFOAMCaseConfig(
        solver="foamRun",
        solver_module="incompressibleFluid",
        turbulence_model="SpalartAllmaras",
        reynolds=condition["reynolds"],
        alpha_deg=float(row["alpha_deg"]),
        rho=condition["rho_kg_m3"],
        mu=condition["mu_pa_s"],
        chord_m=condition["chord_m"],
        velocity_m_s=condition["velocity_m_s"],
        velocity_source="mach",
        reynolds_from_velocity=condition["reynolds_from_properties"],
        mach_input=condition["mach"],
        temperature_K=condition["temperature_K"],
        pressure_ref_pa=condition["pressure_ref_pa"],
        speed_of_sound_m_s=condition["speed_of_sound_m_s"],
        spanwise_thickness_chord=spanwise,
        geometry_topology=(
            "open_internal_cavity" if topology == "open"
            else "closed_external_airfoil"
        ),
        numerics_profile="validation_fixed_staged_v1",
        ddt_scheme="backward",
        n_outer_correctors=int(
            urans.get("pimple_outer_correctors", validation["nOuterCorrectors"])
        ),
        n_correctors=int(
            urans.get("pimple_correctors", validation["nCorrectors"])
        ),
        n_non_orthogonal_correctors=automatic_correctors,
        outer_corrector_residual_control=dict(
            urans.get("outer_corrector_residual_control") or {
                "enabled": True,
                "p": {"tolerance": 1.0e-4, "relTol": 0.0},
                "U": {"tolerance": 1.0e-4, "relTol": 0.0},
                "nuTilda": {"tolerance": 1.0e-4, "relTol": 0.0},
            }
        ),
        transient_velocity_divergence_scheme=(
            "Gauss limitedLinearV 1"
            if topology == "open"
            else "Gauss linearUpwind limited"
        ),
        transient_turbulence_divergence_scheme=(
            "Gauss upwind"
            if topology == "open"
            else "Gauss linearUpwind limited"
        ),
        time_step_mode="fixed",
        deltaT_star=dt_s / condition["tc_s"],
        maxDeltaT_star=dt_s / condition["tc_s"],
        endTime_star=float(plan["stages"][-1]["end_tc"]),
        field_write_interval_star=float(validation["field_write_interval_tc"]),
        field_write_control="runTime",
        field_write_interval_steps=1,
        average_from_fraction=(
            plan["sampling_start_s"] / plan["sampling_end_s"]
        ),
        maxCo=1.0,
        purgeWrite=int(
            urans.get("retained_snapshots", validation["retained_snapshots"])
        ),
        farfield_boundary_condition="freestream",
        steady_initialization_enabled=True,
        steady_max_iterations=20000,
        steady_write_interval_iterations=50,
        steady_n_non_orthogonal_correctors=automatic_correctors,
        laplacian_scheme=automatic_laplacian,
        steady_laplacian_scheme=automatic_laplacian,
        mesh_non_orthogonality_deg=float(
            mesh_quality_controls["maximum_non_orthogonality_deg"]
        ),
        mesh_quality_numerics_source=str(mesh_quality_controls["source"]),
        mesh_quality_numerics_mode="automatic",
        temporal_accuracy={
            "profile_id": "validation_convergence_fixed_staged_v1",
            "target_min_strouhal": 0.05,
            "target_max_strouhal": 20.0,
            "target_samples_per_cycle": 20,
            "minimum_cycles_for_statistics": 10,
        },
    )
    write_0(case, cfg, patches)
    write_constant(case, cfg)
    write_system(case, cfg, patches)
    shutil.rmtree(case / "0")
    shutil.copytree(checkpoint_zero, case / "0")
    copied_mesh_audit = copied_checkpoint_matches(checkpoint_identity, case)
    if copied_mesh_audit["status"] != "MATCH":
        raise RuntimeError(f"{run_id}: copied checkpoint mesh digest mismatch")
    case_config = {
        **asdict(cfg),
        "variant": mesh["variant"],
        "alpha_deg": float(row["alpha_deg"]),
        "mesh_id": mesh["id"],
        "mesh_hash": checkpoint_mesh_hash,
        "registry_mesh_hash": mesh["mesh_hash"],
        "mesh_source": "RANS_CHECKPOINT",
        "checkpoint_mesh_identity": checkpoint_identity,
        "reference_area_m2": cfg.reference_area_m2,
        "reference_length_m": cfg.chord_m,
        "force_wall_patches": [
            patch["name"]
            for patch in patches
            if str(patch.get("type", "")).lower() == "wall"
        ],
        "validation_study": validation,
        "stage_plan": plan,
    }
    write_json_atomic(case / "case_config.json", case_config)
    resolved_config = {
        "schema_version": 1,
        "study_config_schema_version": study_config.get("schema_version"),
        "study_config": plan_config,
        "source_study_config": study_config,
        "active_temporal_package": active_temporal_package,
        "run_matrix_row": row,
        "operating_condition": condition,
        "stage_plan": plan,
        "openfoam_case_config": case_config,
        "resolved_at": utc_stamp(),
    }
    write_json_atomic(run_root / "resolved_config.json", resolved_config)
    audit = audit_prepared_urans_case(
        case,
        cfg,
        run_root / "applied_configuration_audit.json",
    )
    input_summary = case_input_summary(cfg, Path(mesh["mesh_package"]) / "Mesh Data", source_poly, patches)
    write_json_atomic(case / "case_input_summary.json", input_summary)
    canonical_id = case_id_from_row(row)
    metadata = {
        "study_id": study_config["study_id"],
        "case_id": canonical_id,
        "matrix_run_id": run_id,
        "run_id": canonical_id,
        "topology": topology,
        "mesh_level": row["mesh_level"],
        "mesh_package": mesh["mesh_package"],
        "mesh_hash": checkpoint_mesh_hash,
        "registry_mesh_hash": mesh["mesh_hash"],
        "mesh_source": "RANS_CHECKPOINT",
        "cell_count": checkpoint_identity.get("cell_count") or mesh["cell_count"],
        "alpha_deg": float(row["alpha_deg"]),
        "mach": condition["mach"],
        "reynolds": condition["reynolds"],
        "chord_m": condition["chord_m"],
        "U_inf_m_s": condition["velocity_m_s"],
        "tc_s": condition["tc_s"],
        "time_scheme": "backward",
        "dt_s": dt_s,
        "dt_star": dt_s / condition["tc_s"],
        "nOuterCorrectors": cfg.n_outer_correctors,
        "nCorrectors": cfg.n_correctors,
        "nNonOrthogonalCorrectors": cfg.n_non_orthogonal_correctors,
        "physical_duration_s": plan["sampling_end_s"],
        "physical_duration_star": plan["stages"][-1]["end_tc"],
        "sampling_duration_s": plan["stages"][-1]["duration_s"],
        "sampling_start_s": plan["sampling_start_s"],
        "steps_planned": plan["steps_total"],
        "steps_completed": 0,
        "status": "READY",
        "acceptance": "",
        "operating_condition": condition,
        "exact_case_config_hash": sha256_json(case_config),
        "prepared_at": utc_stamp(),
        "solver_executed": False,
        "checkpoint_id": f"{mesh['id']}_simple",
        "checkpoint_required_for_real_run": True,
        "checkpoint_status": (
            read_json(
                _checkpoint_root(project_root, str(mesh["id"]))
                / "checkpoint_manifest.json",
                {},
            )
            or {}
        ).get("status", "NOT_PREPARED"),
    }
    write_json_atomic(run_root / "case_metadata.json", metadata)
    write_json_atomic(run_root / "stage_plan.json", plan)
    write_json_atomic(run_root / "probe_contract.json", _probe_contract(topology))
    write_json_atomic(
        run_root / "run_manifest.json",
        {
            "schema_version": 1,
            "case_id": canonical_id,
            "run_id": canonical_id,
            "mode": "URANS",
            "status": "READY",
            "case": str(case),
            "mesh_id": mesh["id"],
            "mesh_hash": checkpoint_mesh_hash,
            "checkpoint_mesh_hash": checkpoint_mesh_hash,
            "mesh_source": "RANS_CHECKPOINT",
            "resolved_config": str(run_root / "resolved_config.json"),
            "applied_configuration_audit": str(
                run_root / "applied_configuration_audit.json"
            ),
            "configuration_audit_status": audit["status"],
            "execution_status": "READY",
            "review_status": "NOT_REVIEWED",
            "allowed_uses": {
                "space_time_convergence": False,
                "frequency_analysis": False,
            },
            "prepared_at": utc_stamp(),
        },
    )
    hashes = compatibility_hashes(
        mesh_hash=checkpoint_mesh_hash,
        physics={
            "operating_condition": condition,
            "topology": topology,
            "force_wall_patches": case_config["force_wall_patches"],
            "turbulence_model": cfg.turbulence_model,
        },
        solver_config={
            "stage_plan": plan,
            "nOuterCorrectors": cfg.n_outer_correctors,
            "nCorrectors": cfg.n_correctors,
            "nNonOrthogonalCorrectors": cfg.n_non_orthogonal_correctors,
            "ddt_scheme": cfg.ddt_scheme,
        },
    )
    write_case_manifest(
        run_root,
        row,
        hashes=hashes,
        effective_solver_config={
            "production_scheme": str(
                urans.get("production_scheme", "backward")
            ),
            "nOuterCorrectors": cfg.n_outer_correctors,
            "nCorrectors": cfg.n_correctors,
            "nNonOrthogonalCorrectors": cfg.n_non_orthogonal_correctors,
            "mpi_ranks": min(
                8,
                int(
                    urans.get(
                        "mpi_ranks",
                        validation.get("mpi_ranks", 8),
                    )
                ),
            ),
        },
        startup_mode=str(urans.get("startup_mode", "progressive")),
        outcome=ExecutionOutcome.READY.value,
        target_end_time_s=float(plan["sampling_end_s"]),
        checkpoint_mesh_identity=checkpoint_identity,
        checkpoint_copy_audit=copied_mesh_audit,
        registry_mesh_hash=str(mesh["mesh_hash"]),
    )
    (case / "README_validation_run.md").write_text(
        "# Validation convergence run\n\n"
        "This case was prepared but not executed. The solver is dry-run by default.\n"
        "Startup stages A-C and settling stage D are excluded from sampling stage E.\n",
        encoding="utf-8",
    )
    update_run_status(
        project_root,
        run_id,
        "READY",
        physical_duration_s=metadata["physical_duration_s"],
        physical_duration_star=metadata["physical_duration_star"],
        sampling_duration_s=metadata["sampling_duration_s"],
        steps_planned=metadata["steps_planned"],
        case_path=str(case),
        exact_case_config_hash=metadata["exact_case_config_hash"],
    )
    return metadata


def measured_step_cost(
    project_root: Path,
    row: dict[str, Any],
    requested: float | None = None,
) -> dict[str, Any]:
    """Select a traceable cost estimate without presenting 2.48 s as universal."""
    run_root = _run_root(project_root, row)
    candidates = [
        run_root / "case/measured_step_performance.json",
    ]
    candidates = [path for path in candidates if path.is_file()]
    if candidates:
        data = read_json(candidates[0], {}) or {}
        if data.get("median_s_per_step"):
            return {
                "seconds_per_step": float(data["median_s_per_step"]),
                "source": "current canonical case for this mesh",
                "source_path": str(candidates[0]),
                "statistics": data,
                "warning": "",
            }
    study_root = active_workspace_root(project_root)
    same_hash: list[tuple[Path, dict[str, Any]]] = []
    for path in study_root.glob("runs/*/*/*/case/measured_step_performance.json"):
        data = read_json(path, {}) or {}
        metadata = read_json(path.parent.parent / "case_metadata.json", {}) or {}
        if (
            metadata.get("mesh_hash") == row.get("mesh_hash")
            and data.get("median_s_per_step")
        ):
            same_hash.append((path, data))
    if same_hash:
        path, data = max(same_hash, key=lambda item: item[0].stat().st_mtime)
        return {
            "seconds_per_step": float(data["median_s_per_step"]),
            "source": "previous run with identical mesh hash",
            "source_path": str(path),
            "statistics": data,
            "warning": "",
        }
    if requested is not None and requested > 0:
        return {
            "seconds_per_step": float(requested),
            "source": "user-entered measurement",
            "source_path": "",
            "statistics": {},
            "warning": "Verify that the measurement uses the same mesh and solver settings.",
        }
    benchmark = (
        Path(project_root)
        / "CFD_2D/reports/solver_benchmark_matrix_20260723.json"
    )
    if benchmark.is_file():
        return {
            "seconds_per_step": 2.48,
            "source": "Ryzen 7 4800H host benchmark",
            "source_path": str(benchmark),
            "statistics": {},
            "warning": (
                "Historical 334857-cell, 8-rank native measurement; it is not "
                "universal and is not scaled only by cell count."
            ),
        }
    return {
        "seconds_per_step": 2.48,
        "source": "cell-count screening estimate",
        "source_path": "",
        "statistics": {},
        "warning": (
            "No measured canonical run exists for this mesh; use the optional "
            "ephemeral quick-check or begin the case."
        ),
    }


def run_budget(
    project_root: Path,
    run_id: str,
    *,
    seconds_per_step: float | None = None,
    snapshot_size_bytes: float = 0.0,
) -> dict[str, Any]:
    study = load_study(project_root)
    row = _find_run(study, run_id)
    config = study["study_config"]
    condition = operating_condition(config["operating_condition"])
    validation = config["validation_study"]
    cost = measured_step_cost(project_root, row, seconds_per_step)
    budget = temporal_computational_budget(
        dt_s=float(row["dt_s"]),
        condition=condition,
        startup_tc=sum(float(value) for value in validation["startup_duration_tc"]),
        settling_tc=float(validation["settling_tc"]),
        sampling_tc=float(validation["sampling_tc"]),
        field_write_interval_s=float(validation["field_write_interval_tc"]) * condition["tc_s"],
        measured_seconds_per_step=float(cost["seconds_per_step"]),
        measured_snapshot_size_bytes=0.0,
        mpi_ranks=int(validation["mpi_ranks"]),
        free_space_bytes=None,
        wall_time_limit_s=float(validation["timeout_hours"]) * 3600.0,
    )
    budget.pop("estimated_snapshot_count", None)
    budget.pop("estimated_storage_bytes", None)
    budget.pop("free_space_bytes", None)
    budget["storage_estimate"] = (
        "NOT_CALCULATED_HERE; use Informes y workspace"
    )
    budget["step_cost_source"] = cost
    output = active_workspace_root(project_root) / "postprocess/reports" / f"budget_{run_id}.json"
    write_json_atomic(output, budget)
    return budget


def reference_table(project_root: Path) -> list[dict[str, Any]]:
    study = load_study(project_root)
    table = build_reference_dt_table(study["study_config"]["operating_condition"])
    write_json_atomic(
        active_workspace_root(project_root)
        / "postprocess/reports/reference_dt_table.json",
        table,
    )
    return table


def execute_run(
    project_root: Path,
    run_id: str,
    *,
    run: bool,
    startup_mode: str = "progressive",
) -> int:
    """Execute or resume the one canonical timeline for a matrix row."""
    project_root = Path(project_root).resolve()
    study = load_study(project_root)
    row = _find_run(study, run_id)
    run_root = _run_root(project_root, row)
    required_definition = (
        run_root / "case/system/controlDict",
        run_root / "case_metadata.json",
        run_root / "case_manifest.json",
        run_root / "run_manifest.json",
        run_root / "stage_plan.json",
    )
    missing_definition = [str(path) for path in required_definition if not path.is_file()]
    if missing_definition:
        existing_state = inspect_canonical_case(project_root, row)
        if existing_state.get("case_presence") == "STARTED":
            raise CanonicalCaseError(
                "STARTED_CASE_WITH_INCOMPLETE_DEFINITION",
                f"{run_id} contains solver time but its frozen definition is incomplete.",
                remediation="Recover the missing manifests before resuming; solver fields were preserved.",
                evidence={"missing": missing_definition, "case_state": existing_state},
            )
        # A failed preparation can leave controlDict/resolved_config behind.
        # Before physical time exists it is safe and deterministic to rebuild
        # the complete canonical package from the same matrix identity.
        prepare_run(project_root, run_id, overwrite=run_root.exists())
    repair_legacy_classification(run_root)
    manifest = read_json(run_root / "case_manifest.json", {}) or {}
    state = inspect_canonical_case(
        project_root,
        row,
        expected_hashes={
            name: str(manifest.get(name) or "")
            for name in ("mesh_hash", "physics_hash", "solver_config_hash")
            if manifest.get(name)
        },
    )
    action = calculated_action(state)
    if action == "REVIEW":
        raise CanonicalCaseError(
            "CANONICAL_CASE_ALREADY_COMPLETED",
            f"{state['case_id']} already reached its frozen target.",
            remediation=(
                "Review it, or explicitly restart this exact case from RANS."
            ),
            evidence=state,
        )
    if action.startswith("RESTART_REQUIRED"):
        raise CanonicalCaseError(
            action,
            f"{state['case_id']} cannot be resumed safely.",
            remediation=(
                "Inspect the diagnostic and explicitly restart this exact case."
            ),
            evidence=state,
        )
    checkpoint_id = mesh_angle_id(
        study, str(row["mesh_id"]), float(row.get("alpha_deg", 0.0))
    )
    checkpoint = require_compatible_checkpoint(project_root, checkpoint_id)
    case = run_root / "case"
    checkpoint_case = Path(str(checkpoint.get("checkpoint_case") or ""))
    restart_zero = Path(str(checkpoint.get("restart_zero") or ""))
    checkpoint_identity = checkpoint_mesh_identity(checkpoint_case, restart_zero)
    if checkpoint_identity.get("status") != "READY":
        raise CanonicalCaseError(
            "CHECKPOINT_INVALID",
            f"{run_id}: the RANS checkpoint mesh or restart fields are incomplete.",
            remediation="Regenerate or repair the selected RANS checkpoint before URANS.",
            evidence=checkpoint_identity,
        )
    expected_checkpoint_hash = str(manifest.get("mesh_hash") or "")
    actual_checkpoint_hash = str(checkpoint_identity.get("poly_mesh_hash") or "")
    started_case = state["case_presence"] == "STARTED"
    copied_identity = checkpoint_mesh_identity(case, case / "0")
    if not started_case and (
        copied_identity.get("status") != "READY"
        or str(copied_identity.get("poly_mesh_hash") or "") != actual_checkpoint_hash
    ):
        raise CanonicalCaseError(
            "CASE_CHECKPOINT_MISMATCH",
            f"{run_id}: the prepared URANS case does not contain the checkpoint mesh and fields.",
            remediation="Restart this exact URANS identity from RANS.",
            evidence={
                "checkpoint": checkpoint_identity,
                "prepared_case": copied_identity,
            },
        )
    if started_case and not bool((state.get("restart_evidence") or {}).get("valid")):
        raise CanonicalCaseError(
            "URANS_RESTART_STATE_INVALID",
            f"{run_id}: no complete written URANS state is available for resume.",
            remediation="Inspect the retained time directories before restarting this identity.",
            evidence=state.get("restart_evidence") or {},
        )
    if not started_case and expected_checkpoint_hash and expected_checkpoint_hash != actual_checkpoint_hash:
        # Historical manifests used a registry/package hash.  A stale stored
        # value is not a mesh change when the actual prepared case and the
        # actual RANS checkpoint are byte-identical.  Persist the content hash
        # correction and retain the old value as audit evidence.
        correction = {
            "schema_version": 1,
            "status": "STORED_HASH_REPLACED_BY_CONTENT_IDENTITY",
            "case_id": run_id,
            "original_mesh_hash": expected_checkpoint_hash,
            "checkpoint_mesh_hash": actual_checkpoint_hash,
            "prepared_case_mesh_hash": copied_identity.get("poly_mesh_hash"),
            "cell_count": copied_identity.get("cell_count"),
            "corrected_at": utc_stamp(),
        }
        manifest["mesh_hash"] = actual_checkpoint_hash
        manifest["mesh_identity_correction"] = correction
        manifest["updated_at"] = correction["corrected_at"]
        write_json_atomic(run_root / "case_manifest.json", manifest)
        write_json_atomic(run_root / "mesh_identity_correction.json", correction)
    if state["case_presence"] == "NOT_STARTED":
        if not restart_zero.is_dir():
            raise RuntimeError(
                f"{run_id}: compatible RANS restart fields are missing"
            )
        zero = case / "0"
        if zero.exists():
            shutil.rmtree(zero)
        shutil.copytree(restart_zero, zero)
    urans = study["study_config"]["validation_study"]["urans"]
    command = [
        sys.executable,
        str(
            Path(__file__).with_name(
                "ramair_2d_validation_staged_runner.py"
            )
        ),
        "--project-root",
        str(project_root),
        "--run-root",
        str(run_root),
        "--startup-mode",
        startup_mode,
        "--n-cores",
        str(min(8, int(urans.get("mpi_ranks", 8)))),
        "--timeout-min",
        str(
            float(
                study["study_config"]["validation_study"].get(
                    "timeout_hours", 24.0
                )
            )
            * 60.0
        ),
        (
            "--automatic-core-selection"
            if bool(urans.get("automatic_core_selection", True))
            else "--no-automatic-core-selection"
        ),
        (
            "--renumber-before-decompose"
            if bool(urans.get("renumber_before_decompose", True))
            else "--no-renumber-before-decompose"
        ),
    ]
    if run:
        command.append("--run")
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        check=False,
    )
    return int(completed.returncode)


def execute_quick_check(
    project_root: Path,
    run_id: str,
    *,
    run: bool,
) -> dict[str, Any]:
    """Run an optional ephemeral temporal check outside the production case."""
    study = load_study(project_root)
    row = _find_run(study, run_id)
    source_root = canonical_case_root(project_root, row)
    source_case = source_root / "case"
    if not source_case.is_dir():
        prepare_run(project_root, run_id)
    if not source_case.is_dir():
        raise CanonicalCaseError(
            "QUICK_CHECK_PREPARATION_FAILED",
            "The optional quick-check case could not be constructed from RANS.",
            remediation="Inspect the RANS checkpoint integrity diagnostic.",
        )
    sandbox = create_quick_check_sandbox(project_root, case_id_from_row(row))
    sandbox_case = sandbox / "case"
    try:
        shutil.copytree(source_case, sandbox_case, copy_function=shutil.copy2)
        target_dt = float(row.get("dt_s", row.get("deltaT_s")))
        restart = inspect_canonical_case(project_root, row)["restart_evidence"]
        start_s = float(restart.get("time_s") or 0.0)
        steps = int(
            ((study["study_config"].get("validation_study") or {}).get("urans") or {})
            .get("quick_check", {})
            .get("steps", 20)
        )
        stage = {
            "stage": "QUICK_CHECK",
            "scheme": "Euler",
            "dt_s": target_dt,
            "start_s": start_s,
            "end_s": start_s + max(1, steps) * target_dt,
            "steps": max(1, steps),
            "sampling": False,
        }
        configure_stage(
            sandbox_case,
            stage,
            start_mode="RESUME_EXISTING" if start_s > 0.0 else "FRESH_FROM_CHECKPOINT",
        )
        command = runner_command(
            sandbox_case,
            n_cores=min(8, int(((study["study_config"].get("validation_study") or {}).get("urans") or {}).get("mpi_ranks", 8))),
            timeout_min=10.0,
            start_mode="RESUME_EXISTING" if start_s > 0.0 else "FRESH_FROM_CHECKPOINT",
            expected_start_time=start_s if start_s > 0.0 else None,
            run=run,
        )
        if not run:
            report = {
                "status": "QUICK_CHECK_STOPPED",
                "dry_run": True,
                "case_id": case_id_from_row(row),
                "command": command,
                "steps": max(1, steps),
            }
            return finalize_quick_check(
                project_root, sandbox, report, json.dumps(report, indent=2)
            )
        completed = subprocess.run(
            command,
            cwd=str(sandbox_case),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log_text = completed.stdout or ""
        lowered = log_text.lower()
        status = (
            "QUICK_CHECK_DIVERGED"
            if completed.returncode != 0
            and any(marker in lowered for marker in ("foam fatal", "floating point exception", "nan detected"))
            else "QUICK_CHECK_STABLE_START"
            if completed.returncode == 0
            else "QUICK_CHECK_ERROR"
        )
        report = {
            "status": status,
            "dry_run": False,
            "case_id": case_id_from_row(row),
            "returncode": int(completed.returncode),
            "steps": max(1, steps),
            "production_case_unchanged": True,
        }
        return finalize_quick_check(project_root, sandbox, report, log_text)
    except Exception:
        if sandbox.exists():
            shutil.rmtree(sandbox)
        raise


def status_summary(project_root: Path) -> dict[str, Any]:
    study = load_study(project_root)
    if not study:
        return {"status": "NOT_INITIALIZED"}
    counts: dict[str, int] = {}
    for row in study["run_matrix"].get("runs", []):
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    return {
        "status": "READY",
        "study_id": study["study_manifest"]["study_id"],
        "active_workspace": study["study_manifest"]["active_workspace"],
        "results_workspace": study["study_manifest"]["results_workspace"],
        "mesh_count": len(study["mesh_registry"].get("meshes", [])),
        "run_status_counts": counts,
        "solver_campaign_executed": bool(
            study["study_manifest"].get("solver_campaign_executed", False)
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--refresh-hashes", action="store_true")
    sub.add_parser("status")
    mesh = sub.add_parser("select-mesh")
    mesh.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--overwrite", action="store_true")
    budget = sub.add_parser("budget")
    budget.add_argument("--run-id", required=True)
    budget.add_argument("--seconds-per-step", type=float)
    budget.add_argument("--snapshot-size-bytes", type=float, default=0.0)
    execute = sub.add_parser("execute")
    execute.add_argument("--run-id", required=True)
    execute.add_argument(
        "--startup-mode",
        choices=["progressive", "direct"],
        default="progressive",
    )
    execute.add_argument("--run", action="store_true")
    quick_check = sub.add_parser("quick-check")
    quick_check.add_argument("--run-id", required=True)
    quick_check.add_argument("--run", action="store_true")
    inspect_case_parser = sub.add_parser("inspect-case")
    inspect_case_parser.add_argument("--run-id", required=True)
    restart = sub.add_parser("restart")
    restart.add_argument("--run-id", required=True)
    restart.add_argument("--confirm-delete", required=True)
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    checkpoint.add_argument("--overwrite", action="store_true")
    checkpoint.add_argument("--run", action="store_true")
    rans_base = sub.add_parser("rans-base")
    rans_base.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    rans_base.add_argument("--alpha-deg", type=float)
    rans_base.add_argument("--overwrite", action="store_true")
    rans_base.add_argument("--allow-open-diagnostic", action="store_true")
    rans_base.add_argument("--manual-extension-iterations", type=int)
    rans_base.add_argument("--run", action="store_true")
    rans_queue = sub.add_parser("rans-queue")
    rans_queue.add_argument("--alpha-deg", type=float)
    rans_queue.add_argument("--continue-on-nonfatal-failure", action="store_true")
    rans_queue.add_argument("--run", action="store_true")
    rans_selection = sub.add_parser("rans-selection-queue")
    rans_selection.add_argument(
        "--case", action="append", required=True,
        help="Ordered mesh_id:alpha_deg identity; may be repeated.",
    )
    rans_selection.add_argument("--continue-on-nonfatal-failure", action="store_true")
    rans_selection.add_argument("--run", action="store_true")
    rans_delete = sub.add_parser("rans-delete")
    rans_delete.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    rans_delete.add_argument("--confirm", action="store_true")
    rans_delete.add_argument(
        "--archive",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    rans_review = sub.add_parser("rans-review")
    rans_review.add_argument(
        "--review-action",
        choices=[
            "migrate",
            "status",
            "diagnose",
            "accept-stationary",
            "accept-initialization",
            "reject",
            "revoke",
            "create-checkpoint",
            "accept-six-current",
        ],
        required=True,
    )
    rans_review.add_argument("--mesh-id")
    rans_review.add_argument("--alpha-deg", type=float)
    rans_review.add_argument("--reason")
    rans_review.add_argument("--confirm", action="store_true")
    execution_registry = sub.add_parser("execution-registry")
    execution_registry.add_argument(
        "--registry-action",
        choices=["migrate", "list", "activate"],
        required=True,
    )
    execution_registry.add_argument(
        "--mode",
        choices=["ALL", "RANS", "URANS", "PIMPLE_SENSITIVITY"],
        default="ALL",
    )
    execution_registry.add_argument("--run-id")
    execution_registry.add_argument("--pin", action="store_true")
    sub.add_parser("storage-inventory")
    storage_cleanup = sub.add_parser("storage-cleanup")
    storage_cleanup.add_argument("--confirm", action="store_true")
    pimple_study = sub.add_parser("pimple-study")
    pimple_study.add_argument(
        "--study-action", choices=["prepare", "execute", "resume", "analyze"], required=True
    )
    pimple_study.add_argument("--run-id")
    pimple_study.add_argument("--topology", choices=["closed", "open"])
    pimple_study.add_argument("--mesh-level", choices=["coarse", "medium", "fine"])
    pimple_study.add_argument("--dt-s", type=float)
    pimple_study.add_argument("--run", action="store_true")
    open_light = sub.add_parser("open-light")
    open_light.add_argument(
        "--study-action",
        choices=["prepare", "execute", "evaluate", "cleanup"],
        required=True,
    )
    open_light.add_argument("--run", action="store_true")
    open_light.add_argument("--confirm", action="store_true")
    open_refinement = sub.add_parser("open-refinement")
    open_refinement.add_argument(
        "--study-action",
        choices=["prepare", "execute", "evaluate", "promote"],
        required=True,
    )
    open_refinement.add_argument("--run", action="store_true")
    open_refinement.add_argument("--confirm", action="store_true")
    preset = sub.add_parser("preset")
    preset.add_argument(
        "--preset",
        choices=[
            "reference",
            "frequency",
            "manual",
            "cummings_closed_low_cost",
            "cummings_open_low_cost",
        ],
        required=True,
    )
    preset.add_argument("--anchor-dt-s", type=float)
    preset.add_argument("--custom-dt-s", type=float, action="append")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--run-id", required=True)
    analyze_checkpoint_parser = sub.add_parser("analyze-checkpoint")
    analyze_checkpoint_parser.add_argument("--mesh-id", required=True)
    sub.add_parser("report")
    sub.add_parser("reference-table")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if args.action == "init":
        result = initialize_study(root, refresh_hashes=args.refresh_hashes)
    elif args.action == "status":
        result = status_summary(root)
    elif args.action == "select-mesh":
        result = select_mesh(root, args.mesh_id)
    elif args.action == "prepare":
        result = prepare_run(root, args.run_id, overwrite=args.overwrite)
    elif args.action == "budget":
        result = run_budget(
            root,
            args.run_id,
            seconds_per_step=args.seconds_per_step,
            snapshot_size_bytes=args.snapshot_size_bytes,
        )
    elif args.action == "execute":
        return execute_run(
            root,
            args.run_id,
            run=args.run,
            startup_mode=args.startup_mode,
        )
    elif args.action == "quick-check":
        result = execute_quick_check(root, args.run_id, run=args.run)
    elif args.action == "inspect-case":
        study = load_study(root)
        result = inspect_canonical_case(
            root, _find_run(study, args.run_id)
        )
    elif args.action == "restart":
        study = load_study(root)
        result = restart_canonical_case(
            root,
            _find_run(study, args.run_id),
            confirm_delete=args.confirm_delete,
        )
    elif args.action == "checkpoint":
        return execute_checkpoint(
            root,
            args.mesh_id,
            run=args.run,
            overwrite=args.overwrite,
        )
    elif args.action == "rans-base":
        selected_mesh_id = mesh_angle_id(
            load_study(root), args.mesh_id, args.alpha_deg
        )
        result = execute_base(
            root,
            selected_mesh_id,
            run=args.run,
            overwrite=args.overwrite,
            allow_open_diagnostic=args.allow_open_diagnostic,
            manual_extension_iterations=args.manual_extension_iterations,
        )
        generate_storage_inventory(root)
    elif args.action == "rans-queue":
        result = execute_queue(
            root,
            run=args.run,
            alpha_deg=args.alpha_deg,
            continue_on_nonfatal_failure=args.continue_on_nonfatal_failure,
        )
        generate_storage_inventory(root)
    elif args.action == "rans-selection-queue":
        result = execute_selection_queue(
            root,
            list(args.case),
            run=bool(args.run),
            continue_on_nonfatal_failure=bool(args.continue_on_nonfatal_failure),
        )
    elif args.action == "rans-delete":
        result = delete_active_base(
            root,
            args.mesh_id,
            confirm=args.confirm,
            archive=args.archive,
        )
        generate_storage_inventory(root)
    elif args.action == "rans-review":
        if args.review_action in {
            "diagnose",
            "accept-stationary",
            "accept-initialization",
            "reject",
            "revoke",
            "create-checkpoint",
        } and not args.mesh_id:
            raise ValueError(f"{args.review_action} requires --mesh-id")
        if args.review_action == "accept-six-current":
            result = accept_current_six_bases(
                root, confirmation=args.confirm, alpha_deg=args.alpha_deg
            )
        elif args.review_action == "migrate":
            result = migrate_existing_bases(root)
        elif args.review_action == "status":
            result = review_table(root)
        elif args.review_action == "diagnose":
            result = generate_review_diagnostics(root, args.mesh_id)
        elif args.review_action == "accept-stationary":
            result = set_review(
                root,
                args.mesh_id,
                RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
                reason=args.reason or "",
            )
        elif args.review_action == "accept-initialization":
            result = set_review(
                root,
                args.mesh_id,
                RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
                reason=args.reason or "",
            )
        elif args.review_action == "reject":
            result = set_review(
                root,
                args.mesh_id,
                RANS_REJECTED,
                reason=args.reason or "",
            )
        elif args.review_action == "revoke":
            result = revoke_review(
                root, args.mesh_id, reason=args.reason or ""
            )
        else:
            result = create_reviewed_checkpoint(root, args.mesh_id)
    elif args.action == "execution-registry":
        if args.registry_action == "migrate":
            result = migrate_known_executions(root)
        elif args.registry_action == "list":
            result = filtered_runs(root, args.mode)
        else:
            if not args.run_id:
                raise ValueError("activate requires --run-id")
            result = set_active_execution(root, args.run_id, pin=args.pin)
    elif args.action == "storage-inventory":
        result = generate_storage_inventory(root)
    elif args.action == "storage-cleanup":
        result = clean_active_volumetric_products(root, confirm=args.confirm)
    elif args.action == "pimple-study":
        if args.study_action == "prepare":
            result = prepare_pimple_study(
                root,
                run_id=args.run_id,
                topology=args.topology,
                mesh_level=args.mesh_level,
                dt_s=args.dt_s,
            )
        elif args.study_action in {"execute", "resume"}:
            result = execute_pimple_study(
                root,
                run=args.run,
                resume=args.study_action == "resume",
            )
        else:
            result = analyze_pimple_study(root)
    elif args.action == "open-light":
        if args.study_action == "prepare":
            result = prepare_open_light_sweep(root)
        elif args.study_action == "execute":
            result = execute_open_light_sweep(root, run=args.run)
        elif args.study_action == "evaluate":
            result = evaluate_open_light_sweep(root)
        else:
            result = cleanup_rejected_candidates(
                root,
                confirm=args.confirm,
            )
    elif args.action == "open-refinement":
        if args.study_action == "prepare":
            result = prepare_open_refinement(root)
        elif args.study_action == "execute":
            result = execute_open_refinement(root, run=args.run)
        elif args.study_action == "evaluate":
            result = evaluate_open_refinement(root)
        else:
            result = promote_open_refinement(root, confirm=args.confirm)
    elif args.action == "preset":
        result = set_run_matrix_preset(
            root,
            args.preset,
            anchor_dt_s=args.anchor_dt_s,
            custom_values_s=args.custom_dt_s,
        )
    elif args.action == "analyze":
        study = load_study(root)
        row = _find_run(study, args.run_id)
        result = analyze_run(_run_root(root, row))
        if result.get("status") == "COMPLETED":
            update_run_status(root, args.run_id, "COMPLETED")
        elif result.get("status") == "NOT_STATISTICALLY_ESTABLISHED":
            update_run_status(root, args.run_id, "NOT_STATISTICALLY_ESTABLISHED")
    elif args.action == "analyze-checkpoint":
        result = analyze_checkpoint(_checkpoint_root(root, args.mesh_id))
    elif args.action == "report":
        result = generate_study_report(root)
    elif args.action == "reference-table":
        result = reference_table(root)
    else:  # pragma: no cover
        raise AssertionError(args.action)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RansCheckpointBlocked as exc:
        print(json.dumps(exc.payload, indent=2, ensure_ascii=False))
        raise SystemExit(2)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "VALIDATION_STUDY_ACTION_FAILED",
                    "message": str(exc),
                    "remediation_actions": ["Ver diagnóstico"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)
