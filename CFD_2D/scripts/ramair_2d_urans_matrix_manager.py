#!/usr/bin/env python3
"""Atomic sequential queue for canonical Validation Lab URANS cases."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from ramair_2d_study_registry import (
    MESH_IDS,
    active_workspace_root,
    load_study,
    read_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_urans_cases import (
    CanonicalCaseError,
    ExecutionOutcome,
    case_id_from_row,
    inspect_canonical_case,
)
from ramair_2d_validation_study import execute_run, prepare_run


QUEUE_SCHEMA_VERSION = 3
MAX_QUEUE_CASES = 18
MAX_DT_PER_MESH = 3
QUEUE_COLUMNS = (
    "order",
    "case_id",
    "mesh_id",
    "deltaT_s",
    "startup_mode",
    "calculated_action",
    "phase",
    "initial_physical_time_s",
    "final_physical_time_s",
    "simulated_physical_time_s",
    "wall_time_s",
    "result",
    "terminal_reason",
)
GLOBAL_FAILURE_MARKERS = (
    "no space left on device",
    "disk quota exceeded",
    "cannot allocate memory",
    "out of memory",
    "mpi_init",
    "mpi_abort",
    "mpirun was unable",
    "openfoam environment",
    "wm_project_dir",
    "executable was not found",
    "solver_lease_busy",
    "solver_lease_ownership_mismatch",
    "atomic",
    "filesystem",
)


def is_global_queue_failure(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return isinstance(exc, (FileNotFoundError, OSError)) or any(
        marker in message for marker in GLOBAL_FAILURE_MARKERS
    )


def queue_paths(project_root: Path) -> dict[str, Path]:
    root = active_workspace_root(Path(project_root).resolve())
    return {
        "json": root / "urans_queue_state.json",
        "csv": root / "urans_queue_state.csv",
    }


def queue_path(project_root: Path) -> Path:
    return queue_paths(project_root)["json"]


def queue_control_path(project_root: Path) -> Path:
    return active_workspace_root(Path(project_root).resolve()) / ".urans_queue_control_request.json"


def _queue_control_action(project_root: Path) -> str | None:
    request = read_json(queue_control_path(project_root), {}) or {}
    action = str(request.get("action") or "")
    return action if action in {"pause_current_continue", "pause_queue"} else None


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(QUEUE_COLUMNS))
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name) for name in QUEUE_COLUMNS})
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist(project_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    state["updated_at"] = utc_stamp()
    paths = queue_paths(project_root)
    write_json_atomic(paths["json"], state)
    _write_csv_atomic(paths["csv"], list(state.get("runs") or []))
    return state


def _available_rows(project_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    study = load_study(project_root)
    available: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for source in study["run_matrix"].get("runs", []):
        row = dict(source)
        case_id = case_id_from_row(row)
        row["case_id"] = case_id
        available[case_id] = row
        aliases[case_id] = case_id
        aliases[str(row.get("run_id") or case_id)] = case_id
    return available, aliases


def _queue_row(
    project_root: Path,
    row: dict[str, Any],
    *,
    order: int,
    startup_mode: str,
) -> dict[str, Any]:
    state = inspect_canonical_case(project_root, row)
    current = state.get("current_time_s")
    return {
        "order": order,
        "run_id": str(row.get("run_id") or state["case_id"]),
        "case_id": state["case_id"],
        "topology": row["topology"],
        "mesh_id": row["mesh_id"],
        "mesh_level": row["mesh_level"],
        "deltaT_s": float(row.get("dt_s", row.get("deltaT_s"))),
        "startup_mode": startup_mode,
        "calculated_action": state["calculated_action"],
        "phase": state.get("current_phase"),
        "initial_physical_time_s": current,
        "final_physical_time_s": current,
        "simulated_physical_time_s": 0.0,
        "wall_time_s": 0.0,
        "result": "QUEUED",
        "terminal_reason": state.get("terminal_reason"),
        "case_path": state["case_path"],
    }


def prepare_queue(
    project_root: Path,
    run_ids: Iterable[str],
    *,
    startup_mode: str = "progressive",
) -> dict[str, Any]:
    if startup_mode not in {"progressive", "direct"}:
        raise ValueError("startup_mode must be progressive or direct")
    available, aliases = _available_rows(project_root)
    requested = [str(value) for value in run_ids]
    unknown = [value for value in requested if value not in aliases]
    if unknown:
        raise KeyError(f"Unknown URANS cases: {unknown}")
    canonical_requested = [aliases[value] for value in requested]
    ordered_ids = list(dict.fromkeys(canonical_requested))
    duplicates = [
        value for index, value in enumerate(canonical_requested)
        if value in canonical_requested[:index]
    ]
    if not ordered_ids:
        raise ValueError("Select at least one canonical URANS case")
    if len(ordered_ids) > MAX_QUEUE_CASES:
        raise ValueError(f"A URANS queue is limited to {MAX_QUEUE_CASES} cases")
    per_mesh: dict[str, list[float]] = {}
    for case_id in ordered_ids:
        row = available[case_id]
        per_mesh.setdefault(str(row["mesh_id"]), []).append(
            float(row.get("dt_s", row.get("deltaT_s")))
        )
    for mesh_id, values in per_mesh.items():
        normalized = [f"{float(value):.12g}" for value in values]
        if len(set(normalized)) > MAX_DT_PER_MESH:
            raise ValueError(
                f"{mesh_id} has more than {MAX_DT_PER_MESH} distinct deltaT values"
            )
        descending = sorted(values, reverse=True)
        if values != descending:
            raise ValueError(
                f"deltaT values for {mesh_id} must be ordered from largest to smallest"
            )
    runs = [
        _queue_row(
            project_root,
            available[case_id],
            order=index + 1,
            startup_mode=startup_mode,
        )
        for index, case_id in enumerate(ordered_ids)
    ]
    state = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "queue_id": time.strftime("urans_queue_%Y%m%dT%H%M%SZ", time.gmtime()),
        "status": "READY",
        "startup_mode": startup_mode,
        "requested_case_ids": canonical_requested,
        "ordered_case_ids": ordered_ids,
        "deduplicated_case_ids": duplicates,
        "current_index": 0,
        "total": len(runs),
        "continue_after_local_failure": True,
        "stop_after_global_failure": True,
        "runs": runs,
        "created_at": utc_stamp(),
    }
    return _persist(project_root, state)


def _refresh_entry(
    project_root: Path,
    entry: dict[str, Any],
    row: dict[str, Any],
    *,
    started_wall: float,
) -> dict[str, Any]:
    state = inspect_canonical_case(project_root, row)
    initial = entry.get("initial_physical_time_s")
    final = state.get("current_time_s")
    entry.update(
        calculated_action=state["calculated_action"],
        phase=state.get("current_phase"),
        final_physical_time_s=final,
        simulated_physical_time_s=(
            max(0.0, float(final) - float(initial or 0.0))
            if final is not None else 0.0
        ),
        wall_time_s=max(0.0, time.monotonic() - started_wall),
        result=state["execution_outcome"],
        terminal_reason=state.get("terminal_reason"),
        case_path=state["case_path"],
    )
    return state


def execute_queue(
    project_root: Path,
    *,
    run: bool,
    resume: bool = False,
) -> dict[str, Any]:
    path = queue_path(project_root)
    state = read_json(path, {}) or {}
    if int(state.get("schema_version") or 0) != QUEUE_SCHEMA_VERSION:
        raise RuntimeError("Prepare a schema-3 canonical URANS queue first")
    if not state.get("runs"):
        raise RuntimeError("Prepare the canonical URANS queue first")
    if not run:
        return state
    available, aliases = _available_rows(project_root)
    start_index = int(state.get("current_index") or 0) if resume else 0
    queue_control_path(project_root).unlink(missing_ok=True)
    state["status"] = "RUNNING"
    _persist(project_root, state)
    for index in range(start_index, len(state["runs"])):
        entry = state["runs"][index]
        case_id = str(entry["case_id"])
        if case_id not in available:
            entry.update(
                result=ExecutionOutcome.ERROR.value,
                terminal_reason="CASE_DEFINITION_MISSING",
            )
            state["current_index"] = index + 1
            _persist(project_root, state)
            continue
        row = available[case_id]
        current = inspect_canonical_case(project_root, row)
        entry["calculated_action"] = current["calculated_action"]
        entry["initial_physical_time_s"] = current.get("current_time_s")
        if current["execution_outcome"] == ExecutionOutcome.COMPLETED.value:
            entry.update(
                result="SKIPPED_COMPLETED",
                terminal_reason=current.get("terminal_reason"),
            )
            state["current_index"] = index + 1
            _persist(project_root, state)
            continue
        if current["calculated_action"].startswith("RESTART_REQUIRED"):
            entry.update(
                result="SKIPPED_RESTART_REQUIRED",
                terminal_reason=current.get("terminal_reason") or current["calculated_action"],
            )
            state["current_index"] = index + 1
            _persist(project_root, state)
            continue
        started_wall = time.monotonic()
        entry["result"] = "RUNNING"
        state["current_index"] = index
        _persist(project_root, state)
        try:
            if current["case_presence"] == "NOT_STARTED":
                prepare_run(project_root, str(row["run_id"]), overwrite=False)
            manifest_path = Path(str(entry["case_path"])).parent / "case_manifest.json"
            manifest = read_json(manifest_path, {}) or {}
            manifest.update(
                queue_id=state.get("queue_id"),
                queue_position=index + 1,
                queue_total=len(state["runs"]),
                updated_at=utc_stamp(),
            )
            write_json_atomic(manifest_path, manifest)
            code = execute_run(
                project_root,
                str(row["run_id"]),
                run=True,
                startup_mode=str(entry.get("startup_mode") or "progressive"),
            )
            refreshed = _refresh_entry(
                project_root, entry, row, started_wall=started_wall
            )
            entry["returncode"] = int(code)
            state["current_index"] = index + 1
            if (
                refreshed["execution_outcome"] == ExecutionOutcome.PAUSED.value
                and str(refreshed.get("terminal_reason") or "")
                == "USER_REQUESTED_STOP"
            ):
                action = _queue_control_action(project_root) or "pause_queue"
                if action == "pause_current_continue":
                    entry["result"] = "PAUSED_AND_SKIPPED_BY_USER"
                    entry["terminal_reason"] = "USER_REQUESTED_SKIP"
                    queue_control_path(project_root).unlink(missing_ok=True)
                    state["current_index"] = index + 1
                    _persist(project_root, state)
                    continue
                state["status"] = "PAUSED_BY_USER"
                state["current_index"] = index
                _persist(project_root, state)
                return state
        except Exception as exc:
            global_failure = is_global_queue_failure(exc)
            entry.update(
                result="GLOBAL_ERROR" if global_failure else ExecutionOutcome.ERROR.value,
                terminal_reason=f"{type(exc).__name__}: {exc}",
                wall_time_s=max(0.0, time.monotonic() - started_wall),
            )
            state["current_index"] = index if global_failure else index + 1
            if global_failure:
                state["status"] = "STOPPED_GLOBAL_FAILURE"
                _persist(project_root, state)
                return state
        _persist(project_root, state)
    failures = {
        ExecutionOutcome.ERROR.value,
        ExecutionOutcome.DIVERGED.value,
        "GLOBAL_ERROR",
        "SKIPPED_RESTART_REQUIRED",
    }
    state["status"] = (
        "COMPLETED_WITH_LOCAL_FAILURES"
        if any(str(row.get("result")) in failures for row in state["runs"])
        else "COMPLETED"
    )
    state["current_index"] = len(state["runs"])
    return _persist(project_root, state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id", action="append", required=True)
    prepare.add_argument(
        "--startup-mode", choices=["progressive", "direct"], default="progressive"
    )
    execute = sub.add_parser("execute")
    execute.add_argument("--run", action="store_true")
    execute.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "prepare":
        result = prepare_queue(
            args.project_root,
            args.run_id,
            startup_mode=args.startup_mode,
        )
    else:
        result = execute_queue(
            args.project_root,
            run=bool(args.run),
            resume=bool(args.resume),
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
