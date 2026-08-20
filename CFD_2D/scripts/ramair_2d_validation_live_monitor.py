#!/usr/bin/env python3
"""Incremental scalar-only monitor for validation RANS and URANS runs."""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
from pathlib import Path
from typing import Any

from openfoam_history import (
    force_coefficient_files,
    read_recent_force_coefficient_history,
)
from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_execution_registry import load_registry


RESIDUAL_RE = re.compile(
    r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+-]+),"
    r"\s*Final residual\s*=\s*([0-9.eE+-]+),\s*No Iterations\s+(\d+)"
)
ITERATION_RE = re.compile(r"^\s*Time\s*=\s*([0-9.eE+-]+)")
DELTA_T_RE = re.compile(r"^\s*deltaT\s*=\s*([0-9.eE+-]+)")
COURANT_RE = re.compile(
    r"Courant Number mean:\s*([0-9.eE+-]+)\s+max:\s*([0-9.eE+-]+)"
)
CONTINUITY_RE = re.compile(
    r"time step continuity errors\s*:\s*sum local\s*=\s*([0-9.eE+-]+),"
    r"\s*global\s*=\s*([0-9.eE+-]+),\s*cumulative\s*=\s*([0-9.eE+-]+)"
)
EXECUTION_RE = re.compile(
    r"ExecutionTime\s*=\s*([0-9.eE+-]+)\s+s\s+ClockTime\s*=\s*([0-9.eE+-]+)"
)


def _candidate_log(case: Path) -> Path | None:
    candidates = [
        path
        for pattern in (
            "log.foamRun",
            "log.pimpleFoam",
            "PyFoamRunner*.logfile",
            "PyFoam*.logfile",
            "steadyInitialization/history/run_*/"
            "transient_system_before_steady/log.foamRun",
        )
        for path in case.glob(pattern)
        if path.is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def _cache_path(case: Path, mode: str) -> Path:
    return case / f".validation_monitor_{mode.lower()}_cache.json"


def _finite(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_increment(
    lines: list[str],
    recent: dict[str, Any],
    *,
    max_points: int,
) -> dict[str, Any]:
    residuals = list(recent.get("residuals") or [])
    iterations = list(recent.get("iterations") or [])
    courant = list(recent.get("courant") or [])
    continuity = list(recent.get("continuity") or [])
    execution = list(recent.get("execution") or [])
    delta_t = recent.get("deltaT")
    current_iteration = recent.get("current_iteration")
    for line in lines:
        match = ITERATION_RE.search(line)
        if match:
            current_iteration = _finite(match.group(1))
            iterations.append(current_iteration)
        match = DELTA_T_RE.search(line)
        if match:
            delta_t = _finite(match.group(1))
        match = RESIDUAL_RE.search(line)
        if match:
            value = _finite(match.group(2))
            final = _finite(match.group(3))
            if value is not None and final is not None:
                raw_field = match.group(1).strip()
                field = {
                    "Ux": "U.x",
                    "Uy": "U.y",
                    "Uz": "U.z",
                }.get(raw_field, raw_field)
                residuals.append(
                    {
                        "iteration": current_iteration,
                        "equation": field,
                        "component": (
                            field.split(".", 1)[1] if "." in field else ""
                        ),
                        "field": field,
                        "value": value,
                        "initial_residual": value,
                        "final_residual": final,
                        "n_iterations": int(match.group(4)),
                    }
                )
        match = COURANT_RE.search(line)
        if match:
            mean = _finite(match.group(1))
            maximum = _finite(match.group(2))
            if mean is not None and maximum is not None:
                courant.append(
                    {
                        "iteration": current_iteration,
                        "mean": mean,
                        "max": maximum,
                    }
                )
        match = CONTINUITY_RE.search(line)
        if match:
            values = [_finite(match.group(index)) for index in (1, 2, 3)]
            if all(value is not None for value in values):
                continuity.append(
                    {
                        "iteration": current_iteration,
                        "local": values[0],
                        "global": values[1],
                        "cumulative": values[2],
                    }
                )
        match = EXECUTION_RE.search(line)
        if match:
            cpu = _finite(match.group(1))
            clock = _finite(match.group(2))
            if cpu is not None and clock is not None:
                execution.append(
                    {
                        "iteration": current_iteration,
                        "cpu_s": cpu,
                        "clock_s": clock,
                    }
                )
    return {
        "residuals": residuals[-max_points:],
        "iterations": iterations[-max_points:],
        "courant": courant[-max_points:],
        "continuity": continuity[-max_points:],
        "execution": execution[-max_points:],
        "deltaT": delta_t,
        "current_iteration": current_iteration,
    }


def _read_log_increment(log: Path, cache: dict[str, Any]) -> tuple[list[str], int]:
    identity = f"{log.resolve()}:{log.stat().st_ino}"
    offset = int(cache.get("offset", 0)) if cache.get("identity") == identity else 0
    size = log.stat().st_size
    if size < offset:
        offset = 0
    with log.open("rb") as stream:
        stream.seek(offset)
        data = stream.read()
        new_offset = stream.tell()
    return data.decode("utf-8", errors="ignore").splitlines(), new_offset


def _performance(execution: list[dict[str, Any]]) -> dict[str, Any]:
    if len(execution) < 3:
        return {"status": "WAITING_FOR_STABLE_STEPS"}
    deltas = [
        float(current["clock_s"]) - float(previous["clock_s"])
        for previous, current in zip(execution, execution[1:])
        if float(current["clock_s"]) > float(previous["clock_s"])
    ]
    # Exclude startup/decomposition and use stable final increments only.
    stable = deltas[max(1, len(deltas) // 3) :]
    if not stable:
        return {"status": "WAITING_FOR_STABLE_STEPS"}
    ordered = sorted(stable)
    return {
        "status": "MEASURED",
        "samples": len(stable),
        "median_s_per_step": statistics.median(stable),
        "p25_s_per_step": ordered[int(0.25 * (len(ordered) - 1))],
        "p75_s_per_step": ordered[int(0.75 * (len(ordered) - 1))],
        "mean_s_per_step": statistics.fmean(stable),
        "stdev_s_per_step": statistics.pstdev(stable),
        "exclusions": [
            "first step",
            "decomposition",
            "reconstruction",
            "field writes",
            "stage transitions",
        ],
    }


def _force_snapshot(case: Path, max_points: int) -> tuple[list[dict[str, float]], list[str]]:
    files = force_coefficient_files(case, include_processor0=True)
    fingerprint = [
        f"{path.resolve()}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
        for path in files
    ]
    rows, sources = read_recent_force_coefficient_history(
        case,
        max_rows=max_points,
        include_processor0=True,
    )
    return rows, [*sources, *fingerprint]


def resolve_live_execution(
    project_root: Path,
    *,
    follow_active_execution: bool,
    pinned_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the monitor target from disk at every render tick.

    A manual pin is deliberately ignored while follow-active is selected.
    This function contains no cached case object, making it safe when a queue
    advances to another canonical case or phase.
    """
    if follow_active_execution:
        runtime = read_json(
            active_workspace_root(Path(project_root).resolve())
            / "runtime/active_execution.json",
            {},
        ) or {}
        if runtime.get("case_path"):
            key = dict(runtime.get("scientific_key") or {})
            return {
                "run_id": runtime.get("case_id"),
                "case_id": runtime.get("case_id"),
                "case_path": runtime.get("case_path"),
                "mode": "URANS",
                "run_kind": "CANONICAL",
                "topology": key.get("topology"),
                "mesh_id": runtime.get("mesh_id"),
                "mesh_level": key.get("mesh_level"),
                "stage": runtime.get("phase"),
                "status": runtime.get("status"),
                "deltaT": runtime.get("deltaT"),
                "target_deltaT": runtime.get("target_deltaT"),
                "phase_deltaT": runtime.get("phase_deltaT") or runtime.get("deltaT"),
                "queue_position": runtime.get("queue_position"),
                "queue_total": runtime.get("queue_total"),
            }
    registry = load_registry(Path(project_root))
    target_id = (
        registry.get("active_run_id")
        if follow_active_execution
        else pinned_run_id or registry.get("pinned_run_id")
    )
    if not target_id:
        return None
    return next(
        (
            dict(row)
            for row in registry.get("runs", [])
            if str(row.get("run_id")) == str(target_id)
        ),
        None,
    )


def build_monitor_snapshot(
    case: Path,
    *,
    mode: str,
    run_id: str,
    topology: str,
    mesh_level: str,
    cell_count: int,
    stage: str = "",
    tc_s: float | None = None,
    steps_planned: int | None = None,
    max_points: int = 800,
    queue_position: int | None = None,
    queue_total: int | None = None,
    target_delta_t: float | None = None,
    phase_delta_t: float | None = None,
) -> dict[str, Any]:
    """Update one snapshot using only logs and scalar histories."""
    case = Path(case).resolve()
    mode = mode.upper()
    if mode not in {"RANS", "URANS"}:
        raise ValueError("mode must be RANS or URANS")
    cache_path = _cache_path(case, mode)
    cache = read_json(cache_path, {}) or {}
    log = _candidate_log(case)
    case_manifest = read_json(case.parent / "case_manifest.json", {}) or {}
    recent = dict(cache.get("recent") or {})
    if log is not None:
        lines, offset = _read_log_increment(log, cache)
        recent = _parse_increment(lines, recent, max_points=max_points)
        identity = f"{log.resolve()}:{log.stat().st_ino}"
    else:
        offset = 0
        identity = ""
    forces, force_sources = _force_snapshot(case, max_points)
    current = recent.get("current_iteration")
    delta_t = recent.get("deltaT")
    if mode == "RANS":
        queue = (
            f" | Base-state queue {queue_position}/{queue_total}"
            if queue_position and queue_total
            else ""
        )
        title = (
            f"{topology.title()} | {mesh_level.title()} | {int(cell_count):,} cells "
            f"| RANS/SIMPLE | Iteration {int(current or 0):,}{queue}"
        )
    else:
        target_display = (
            target_delta_t if target_delta_t is not None else delta_t
        )
        phase_display = (
            phase_delta_t if phase_delta_t is not None else delta_t
        )
        title = (
            f"{topology.title()} | {mesh_level.title()} | URANS/PIMPLE "
            f"| target dt={float(target_display or 0):.6g} s "
            f"| phase dt={float(phase_display or 0):.6g} s "
            f"| Stage {stage or 'A-E'}"
        )
    elapsed = (
        float(recent["execution"][-1]["clock_s"])
        if recent.get("execution")
        else None
    )
    steps_done = len(recent.get("iterations") or [])
    remaining = None
    performance = _performance(list(recent.get("execution") or []))
    if (
        steps_planned
        and steps_done
        and performance.get("status") == "MEASURED"
    ):
        remaining = max(0, int(steps_planned) - steps_done) * float(
            performance["median_s_per_step"]
        )
    physical_time = float(current or 0.0) if mode == "URANS" else None
    snapshot = {
        "schema_version": 1,
        "status": (
            str(case_manifest.get("execution_outcome") or "RUNNING")
            if log is not None
            else "WAITING_FOR_SOLVER_LOG"
        ),
        "title": title,
        "run_id": run_id,
        "mode": mode,
        "topology": topology,
        "mesh_level": mesh_level,
        "cell_count": int(cell_count),
        "stage": stage,
        "startup_mode": case_manifest.get("startup_mode"),
        "iteration_or_time": current,
        "deltaT_s": delta_t if mode == "URANS" else None,
        "target_deltaT_s": target_delta_t if mode == "URANS" else None,
        "phase_deltaT_s": (
            phase_delta_t if phase_delta_t is not None else delta_t
        ) if mode == "URANS" else None,
        "physical_time_s": physical_time,
        "convective_time": (
            physical_time / tc_s
            if physical_time is not None and tc_s and tc_s > 0
            else None
        ),
        "steps_observed": steps_done,
        "steps_planned": steps_planned,
        "elapsed_s": elapsed,
        "estimated_remaining_s": remaining,
        "residuals": recent.get("residuals") or [],
        "forces": forces,
        "courant": recent.get("courant") or [],
        "continuity": recent.get("continuity") or [],
        "gate": read_json(case / "staged_run_status.json", {}).get(
            "steady_transition", {}
        ),
        "performance": performance,
        "source_log": str(log) if log else None,
        "heartbeat_at": case_manifest.get("updated_at"),
        "terminal_reason": case_manifest.get("terminal_reason"),
        "force_sources": force_sources,
        "monitor_policy": {
            "incremental_log_offset": True,
            "recent_window_only": True,
            "visual_downsampling_only": True,
            "raw_histories_preserved": True,
            "reads_volume_fields": False,
            "launches_paraview": False,
        },
        "parser_diagnostics": {
            "inspected_paths": [
                str(case / "validation_monitor_residuals.csv"),
                str(case / "steadyInitialization/history"),
                str(case / "PyFoamRunner*.logfile"),
                str(case / "log.foamRun"),
            ],
            "parsed_residual_lines": len(recent.get("residuals") or []),
            "fields_found": sorted(
                {
                    str(row.get("equation") or row.get("field") or "")
                    for row in recent.get("residuals") or []
                    if row.get("equation") or row.get("field")
                }
            ),
            "latest_iteration": current,
            "log_identity": identity,
            "incremental_offset_bytes": offset,
            "parser_error": (
                None
                if recent.get("residuals")
                else (
                    "Waiting for solver log." if log is None
                    else "No residual rows were parsed from the current solver log."
                )
            ),
        },
        "updated_at": utc_stamp(),
    }
    write_json_atomic(
        cache_path,
        {
            "identity": identity,
            "offset": offset,
            "recent": recent,
            "updated_at": utc_stamp(),
        },
    )
    output = case / f"validation_live_monitor_{mode.lower()}.json"
    write_json_atomic(output, snapshot)
    if performance.get("status") == "MEASURED":
        write_json_atomic(case / "measured_step_performance.json", performance)
    return snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--mode", choices=["RANS", "URANS"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--topology", choices=["closed", "open"], required=True)
    parser.add_argument("--mesh-level", choices=["coarse", "medium", "fine"], required=True)
    parser.add_argument("--cell-count", type=int, required=True)
    parser.add_argument("--stage", default="")
    parser.add_argument("--tc-s", type=float)
    parser.add_argument("--steps-planned", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_monitor_snapshot(
        args.case,
        mode=args.mode,
        run_id=args.run_id,
        topology=args.topology,
        mesh_level=args.mesh_level,
        cell_count=args.cell_count,
        stage=args.stage,
        tc_s=args.tc_s,
        steps_planned=args.steps_planned,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
