#!/usr/bin/env python3
"""Execute one canonical Validation Lab URANS timeline.

The command is dry-run unless ``--run`` is present.  User-facing execution
selects progressive A-E or direct startup; technical start modes are resolved
from the persisted case state and never exposed as a UI choice.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from ramair_2d_execution_registry import upsert_execution
from ramair_execution_control import (
    ExecutionState as CanonicalExecutionState,
    execution_idempotency_key,
    load_execution_state,
    transition_execution_state,
)
from ramair_2d_study_registry import read_json, utc_stamp, write_json_atomic
from ramair_2d_urans_cases import (
    ExecutionOutcome,
    acquire_solver_lease,
    clear_active_runtime,
    complete_time_history,
    process_start_token,
    publish_runtime,
    release_solver_lease,
    restart_time_evidence,
)
from ramair_2d_urans_contract import (
    CONTINUE_STAGE,
    FRESH_FROM_CHECKPOINT,
    RESUME_EXISTING,
    normalize_start_mode,
)
from ramair_2d_openfoam_log_events import classify_openfoam_log


PARTIAL_RUNNER_STATES = {
    "TIMEOUT_PARTIAL",
    "RUN_TIMEOUT_PARTIAL",
    "STOPPED_BY_USER",
    "STOPPED_PARTIAL",
    "STOPPED_FORCED_PARTIAL",
}
USER_STOP_RUNNER_STATES = {
    "STOPPED_BY_USER",
    "STOPPED_PARTIAL",
    "STOPPED_FORCED_PARTIAL",
}
def _replace_entry(text: str, name: str, value: str) -> str:
    pattern = rf"(?m)^(\s*{re.escape(name)}\s+)[^;]+;"
    if not re.search(pattern, text):
        raise ValueError(f"OpenFOAM entry {name!r} is missing")
    return re.sub(pattern, rf"\g<1>{value};", text, count=1)


def _latest_time_index(case: Path) -> tuple[Decimal, int] | None:
    """Read OpenFOAM's persisted global time index from the latest state."""
    candidates: list[tuple[Decimal, Path]] = []
    for root in (case, case / "processor0"):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                value = Decimal(path.name)
            except Exception:
                continue
            if value >= 0:
                candidates.append((value, path))
    for value, path in sorted(candidates, key=lambda item: item[0], reverse=True):
        time_file = path / "uniform/time"
        if not time_file.is_file():
            if value == 0:
                return value, 0
            continue
        text = time_file.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"(?m)^\s*index\s+([0-9]+)\s*;",
            text,
        )
        if match:
            exact = re.search(
                r"(?m)^\s*value\s+([-+0-9.eE]+)\s*;",
                text,
            )
            return (
                Decimal(exact.group(1)) if exact else value,
                int(match.group(1)),
            )
    return None


def _phase_boundary_write_interval(
    case: Path,
    *,
    intended_end: Decimal,
    delta_t: Decimal,
    stage_steps: int,
) -> tuple[int, int | None]:
    """Choose a sparse timeStep interval guaranteed to write the phase target."""
    evidence = _latest_time_index(case)
    if evidence is None:
        return 1, None
    current_time, current_index = evidence
    remaining = max(
        1,
        int(((intended_end - current_time) / delta_t).to_integral_value()),
    )
    target_index = current_index + remaining
    upper = max(1, min(int(stage_steps), remaining))
    for interval in range(upper, 0, -1):
        if target_index % interval == 0:
            return interval, target_index
    return 1, target_index


def configure_stage(
    case: Path,
    stage: dict[str, Any],
    *,
    start_mode: str,
    preserve_temporal_history: bool = False,
) -> dict[str, Any]:
    """Apply one frozen phase without modifying its scientific target."""
    effective = normalize_start_mode(start_mode)
    from_latest = effective in {CONTINUE_STAGE, RESUME_EXISTING}
    stage_steps = int(stage.get("steps") or 1)
    delta_t = Decimal(str(stage["dt_s"]))
    intended_end = Decimal(str(stage.get("end_s"))) if stage.get("end_s") is not None else (
        Decimal(str(stage["start_s"])) + delta_t * Decimal(stage_steps)
    )
    control_path = case / "system/controlDict"
    schemes_path = case / "system/fvSchemes"
    solution_path = case / "system/fvSolution"
    control = control_path.read_text(encoding="utf-8")
    control = _replace_entry(control, "startFrom", "latestTime" if from_latest else "startTime")
    if not from_latest:
        control = _replace_entry(control, "startTime", "0")
    control = _replace_entry(control, "stopAt", "endTime")
    control = _replace_entry(control, "endTime", format(intended_end, ".12g"))
    control = _replace_entry(control, "deltaT", f"{float(stage['dt_s']):.12g}")
    adaptive = bool(stage.get("adjust_time_step", False))
    control = _replace_entry(control, "adjustTimeStep", "yes" if adaptive else "no")
    if adaptive:
        control = _replace_entry(control, "maxCo", f"{float(stage.get('maxCo', 50.0)):.12g}")
        control = _replace_entry(
            control, "maxDeltaT", f"{float(stage.get('maxDeltaT_s', stage['dt_s'])):.12g}",
        )
        control = _replace_entry(control, "writeControl", "adjustableRunTime")
    else:
        control = _replace_entry(control, "writeControl", "timeStep")
    boundary_target_index: int | None = None
    if preserve_temporal_history:
        write_interval = 1
    else:
        write_interval, boundary_target_index = _phase_boundary_write_interval(
            case,
            intended_end=intended_end,
            delta_t=delta_t,
            stage_steps=stage_steps,
        )
    if adaptive:
        requested_write = float(stage.get("write_interval_s", stage["dt_s"]))
        control = _replace_entry(control, "writeInterval", f"{requested_write:.12g}")
    else:
        control = _replace_entry(control, "writeInterval", str(write_interval))
    requested_purge = stage.get("purge_write")
    effective_purge: int | None = None
    if requested_purge is not None:
        effective_purge = max(0, int(requested_purge))
    if preserve_temporal_history:
        effective_purge = max(3, int(effective_purge or 0))
    if effective_purge is not None:
        if re.search(r"(?m)^\s*purgeWrite\s+[^;]+;", control):
            control = _replace_entry(control, "purgeWrite", str(effective_purge))
        else:
            control += (
                "\n// Rolling retention; backward needs at least three states.\n"
                f"purgeWrite {effective_purge};\n"
            )
    control_path.write_text(control, encoding="utf-8")

    schemes = schemes_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(ddtSchemes\s*\{\s*)default\s+[^;]+;",
        rf"\g<1>default {stage['scheme']};",
        schemes,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError("Could not update ddtSchemes.default")
    schemes_path.write_text(updated, encoding="utf-8")
    # Frozen convergence studies remove adaptive outer-loop exit. General
    # validation runs explicitly retain it to cap work while preserving a
    # maximum of five PIMPLE outer corrections.
    if solution_path.is_file() and not bool(stage.get("retain_outer_residual_control", False)):
        solution = solution_path.read_text(encoding="utf-8")
        token = "outerCorrectorResidualControl"
        start = solution.find(token)
        if start >= 0:
            brace = solution.find("{", start + len(token))
            if brace < 0:
                raise ValueError(f"Malformed {token} block in {solution_path}")
            depth = 0
            end = None
            for index in range(brace, len(solution)):
                if solution[index] == "{":
                    depth += 1
                elif solution[index] == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end is None:
                raise ValueError(f"Unterminated {token} block in {solution_path}")
            line_start = solution.rfind("\n", 0, start) + 1
            solution = solution[:line_start] + solution[end:].lstrip(" \t")
            solution_path.write_text(solution, encoding="utf-8")
    return {
        "phase": str(stage["stage"]),
        "scheme": str(stage["scheme"]),
        "deltaT_s": float(stage["dt_s"]),
        "intended_start_s": float(stage["start_s"]),
        "intended_end_s": float(stage["end_s"]),
        "decimal_end_s": float(intended_end),
        "start_mode": effective,
        "write_interval_steps": write_interval,
        "boundary_target_time_index": boundary_target_index,
        "retains_temporal_history": preserve_temporal_history,
        "purge_write_latest_states": effective_purge,
        "adjust_time_step": adaptive,
        "maxCo": float(stage.get("maxCo", 50.0)) if adaptive else None,
        "maxDeltaT_s": float(stage.get("maxDeltaT_s", stage["dt_s"])) if adaptive else None,
        "first_order_bootstrap": bool(stage.get("first_order_bootstrap", False)),
    }


def runner_command(
    case: Path,
    *,
    n_cores: int,
    timeout_min: float,
    start_mode: str,
    expected_start_time: float | None,
    run: bool,
    decompose_times: list[float] | None = None,
    reconstruct_times: list[float] | None = None,
    automatic_core_selection: bool = True,
    renumber_before_decompose: bool = True,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("ramair_2d_openfoam_runner.py")),
        "--case", str(case),
        "--solver", "auto",
        "--execution-backend", "native",
        "--n-cores", str(max(1, min(8, int(n_cores)))),
        "--timeout-min", str(float(timeout_min)),
        "--stop-grace-min", "5",
        "--stop-mode", "writeNow",
        "--start-mode", normalize_start_mode(start_mode),
        "--automatic-core-selection" if automatic_core_selection else "--no-automatic-core-selection",
        "--renumber-before-decompose" if renumber_before_decompose else "--no-renumber-before-decompose",
    ]
    if expected_start_time is not None:
        command += ["--expected-start-time", f"{float(expected_start_time):.12g}"]
    for value in decompose_times or ():
        command += ["--decompose-time", f"{float(value):.12g}"]
    for value in reconstruct_times or ():
        command += ["--reconstruct-time", f"{float(value):.12g}"]
    if run:
        command.append("--run")
    return command


def _phase_complete(row: dict[str, Any]) -> bool:
    return (
        str(row.get("terminal_reason") or "") == "PHASE_TARGET_REACHED"
        and int(row.get("returncode", 1)) == 0
        and bool((row.get("output_checkpoint") or {}).get("valid"))
    )


def _history_evidence(case: Path, target_dt_s: float) -> dict[str, Any]:
    base_history = complete_time_history(case)
    available = list(base_history.get("times_s") or [])
    direct_records = list(base_history.get("direct_records") or [])
    latest_path = (
        Path(str(direct_records[-1]["directory"]))
        if direct_records else None
    )
    required_fields = ["U", "p", "nuTilda"]
    if latest_path is not None:
        for optional in ("phi", "nut"):
            if (latest_path / optional).is_file() or (latest_path / f"{optional}.gz").is_file():
                required_fields.append(optional)
    history = complete_time_history(case, required_fields=required_fields)
    selected = list(history.get("times_s") or [])[-3:]
    spacings = [
        selected[index + 1] - selected[index]
        for index in range(len(selected) - 1)
    ]
    tolerance = max(1.0e-12, abs(float(target_dt_s)) * 1.0e-6)
    return {
        "valid": bool(
            len(selected) == 3
            and len(spacings) == 2
            and all(abs(value - float(target_dt_s)) <= tolerance for value in spacings)
        ),
        "retained_old_times": selected,
        "current_and_two_previous_times": selected,
        "source": history.get("source"),
        "processor_count": history.get("processor_count", 0),
        "spacings_s": spacings,
        "target_deltaT_s": float(target_dt_s),
        "tolerance_s": tolerance,
        "required_fields": required_fields,
    }


def _run_phase_command(
    command: list[str],
    *,
    case: Path,
    project_root: Path,
    runtime_base: dict[str, Any],
    phase: str,
    current_log: Path,
    log_offset: int,
) -> tuple[int, dict[str, Any]]:
    """Run one phase while publishing the effective solver PID and heartbeat."""
    process = subprocess.Popen(command, cwd=str(case))
    last_status: dict[str, Any] = {}
    while process.poll() is None:
        last_status = read_json(case / "run_status.json", {}) or last_status
        solver_pid = last_status.get("solver_pid") or last_status.get("pid")
        publish_runtime(
            project_root,
            {
                **runtime_base,
                "phase": phase,
                "status": "RUNNING",
                "PID": solver_pid or process.pid,
                "process_start_token": process_start_token(
                    int(solver_pid or process.pid)
                ),
                "orchestrator_PID": runtime_base.get("PID"),
                "orchestrator_process_start_token": runtime_base.get(
                    "process_start_token"
                ),
                "current_log": str(current_log),
                "log_offset": int(log_offset),
                "physical_time": last_status.get("last_time"),
                "deltaT": last_status.get("deltaT") or runtime_base.get("deltaT"),
                "heartbeat": utc_stamp(),
            },
        )
        time.sleep(1.0)
    last_status = read_json(case / "run_status.json", {}) or last_status
    return int(process.returncode or 0), last_status


def _bootstrap_backward_history(
    *,
    case: Path,
    run_root: Path,
    stage: dict[str, Any],
    project_root: Path,
    runtime_base: dict[str, Any],
    journal: dict[str, Any],
    n_cores: int,
    timeout_min: float,
    automatic_core_selection: bool,
    renumber_before_decompose: bool,
) -> dict[str, Any]:
    """Create three target-deltaT Euler states before enabling backward."""
    checkpoint = restart_time_evidence(case)
    if not checkpoint.get("valid"):
        raise RuntimeError("BACKWARD_BOOTSTRAP_INPUT_MISSING")
    start = Decimal(str(checkpoint["time_s"]))
    delta_t = Decimal(str(stage["dt_s"]))

    topology = str(
        (runtime_base.get("scientific_key") or {}).get("topology") or ""
    ).lower()
    if topology == "open":
        # The open lip contains the smallest and most distorted local cells. A
        # direct 10x jump from phase C to the target step produced a local Co
        # spike before backward was enabled. Ramp with Euler and robust Co=10,
        # then create the three equally-spaced target-step states below.
        ramp_start = start
        ramp_dt = delta_t * Decimal("0.25")
        ramp_end = ramp_start + Decimal("8") * delta_t
        ramp = {
            **stage,
            "stage": f"{stage['stage']}_EULER_RAMP",
            "purpose": "bounded open-lip time-step ramp before backward history",
            "scheme": "Euler",
            "dt_s": float(ramp_dt),
            "start_s": float(ramp_start),
            "end_s": float(ramp_end),
            "duration_s": float(ramp_end - ramp_start),
            "sampling": False,
            "purge_write": max(4, int(stage.get("purge_write") or 0)),
            "adjust_time_step": True,
            "maxCo": 10.0,
            "maxDeltaT_s": float(delta_t),
            "first_order_bootstrap": True,
        }
        ramp_applied = configure_stage(
            case,
            ramp,
            start_mode=RESUME_EXISTING,
            preserve_temporal_history=False,
        )
        ramp_log = _log_file(case)
        ramp_offset = ramp_log.stat().st_size if ramp_log.is_file() else 0
        ramp_started = time.monotonic()
        ramp_command = runner_command(
            case,
            n_cores=n_cores,
            timeout_min=timeout_min,
            start_mode=RESUME_EXISTING,
            expected_start_time=float(ramp_start),
            run=True,
            automatic_core_selection=automatic_core_selection,
            renumber_before_decompose=renumber_before_decompose,
        )
        ramp_returncode, ramp_status = _run_phase_command(
            ramp_command,
            case=case,
            project_root=project_root,
            runtime_base={
                **runtime_base,
                "target_deltaT": float(delta_t),
                "phase_deltaT": float(ramp_dt),
                "deltaT": float(ramp_dt),
            },
            phase=str(ramp["stage"]),
            current_log=ramp_log,
            log_offset=ramp_offset,
        )
        ramp_output = restart_time_evidence(case)
        ramp_segment = _log_segment(
            ramp_log,
            ramp_offset,
            run_root / "logs" / f"phase_{ramp['stage']}_{len(journal['phases']) + 1:03d}.log",
            returncode=ramp_returncode,
        )
        ramp_event = dict(ramp_segment.get("openfoam_event") or {})
        ramp_complete = bool(
            ramp_returncode == 0
            and ramp_output.get("valid")
            and ramp_event.get("normal_end")
            and not ramp_event.get("numerical_divergence")
            and (
                ramp_event.get("maximum_courant") is None
                or float(ramp_event["maximum_courant"]) <= 50.0
            )
        )
        ramp_row = {
            **ramp_applied,
            "actual_start_s": float(ramp_start),
            "actual_end_s": ramp_output.get("time_s"),
            "input_checkpoint": checkpoint,
            "output_checkpoint": ramp_output,
            "returncode": int(ramp_returncode),
            "wall_seconds": time.monotonic() - ramp_started,
            "terminal_reason": (
                "OPEN_EULER_RAMP_READY" if ramp_complete else "OPEN_EULER_RAMP_FAILED"
            ),
            **ramp_segment,
        }
        journal["phases"].append(ramp_row)
        journal["updated_at"] = utc_stamp()
        write_json_atomic(run_root / "stage_journal.json", journal)
        if not ramp_complete:
            raise RuntimeError(
                "OPEN_EULER_RAMP_FAILED: the target time step is not stable at the open lip"
            )
        start = Decimal(str(ramp_output["time_s"]))
        checkpoint = ramp_output

    end = start + 3 * delta_t
    if end >= Decimal(str(stage["end_s"])):
        raise RuntimeError("BACKWARD_BOOTSTRAP_DOES_NOT_FIT_PHASE")

    bootstrap = {
        **stage,
        "stage": f"{stage['stage']}_BOOTSTRAP",
        "purpose": "first-order history for backward",
        "scheme": "Euler",
        "start_s": float(start),
        "end_s": float(end),
        "duration_s": float(3 * delta_t),
        "steps": 3,
        "sampling": False,
        "purge_write": max(3, int(stage.get("purge_write") or 0)),
        "first_order_bootstrap": True,
    }
    applied = configure_stage(
        case,
        bootstrap,
        start_mode=RESUME_EXISTING,
        preserve_temporal_history=True,
    )
    expected_times = [
        float(start + delta_t),
        float(start + 2 * delta_t),
        float(end),
    ]
    log = _log_file(case)
    started_at = utc_stamp()
    started = time.monotonic()
    command = runner_command(
        case,
        n_cores=n_cores,
        timeout_min=timeout_min,
        start_mode=RESUME_EXISTING,
        expected_start_time=float(start),
        run=True,
        reconstruct_times=expected_times,
        automatic_core_selection=automatic_core_selection,
        renumber_before_decompose=renumber_before_decompose,
    )
    returncode, run_status = _run_phase_command(
        command,
        case=case,
        project_root=project_root,
        runtime_base={
            **runtime_base,
            "target_deltaT": float(stage["dt_s"]),
            "phase_deltaT": float(stage["dt_s"]),
            "deltaT": float(stage["dt_s"]),
        },
        phase=str(bootstrap["stage"]),
        current_log=log,
        log_offset=0,
    )
    output = restart_time_evidence(case)
    segment = _log_segment(
        log,
        0,
        run_root / "logs" / f"phase_{bootstrap['stage']}_{len(journal['phases']) + 1:03d}.log",
        returncode=returncode,
    )
    event = dict(segment.get("openfoam_event") or {})
    runner_status = str(run_status.get("status") or "")
    partial = runner_status.upper() in PARTIAL_RUNNER_STATES
    end_tolerance = max(1.0e-12, abs(float(delta_t)) * 1.0e-4)
    target_reached = bool(
        output.get("valid")
        and float(output["time_s"]) + end_tolerance >= float(end)
        and event.get("normal_end")
        and returncode == 0
    )
    history = _history_evidence(case, float(delta_t))
    maximum_courant = event.get("maximum_courant")
    courant_limit = float(stage.get("maxCo") or 50.0)
    numerically_acceptable = bool(
        not event.get("numerical_divergence")
        and (
            maximum_courant is None
            or float(maximum_courant) <= courant_limit
        )
    )
    history_ready = bool(
        target_reached and history.get("valid") and numerically_acceptable
    )
    terminal_reason = (
        "USER_REQUESTED_STOP"
        if runner_status.upper() in USER_STOP_RUNNER_STATES
        else "RUN_TIMEOUT"
        if partial
        else "NUMERICAL_DIVERGENCE"
        if target_reached and not numerically_acceptable
        else "BACKWARD_HISTORY_READY"
        if history_ready
        else "BACKWARD_BOOTSTRAP_HISTORY_INVALID"
        if target_reached
        else "BACKWARD_BOOTSTRAP_FAILED"
    )
    row = {
        **applied,
        "actual_start_s": float(start),
        "actual_end_s": output.get("time_s"),
        "input_checkpoint": checkpoint,
        "output_checkpoint": output,
        "started_at": started_at,
        "ended_at": utc_stamp(),
        "returncode": int(returncode),
        "solver_started": bool(run_status.get("solver_started")),
        "steps_completed": run_status.get("steps_completed"),
        "wall_seconds": time.monotonic() - started,
        "terminal_reason": terminal_reason,
        "primary_error": None if history_ready else terminal_reason,
        "backward_history": history,
        "maximum_courant_limit": courant_limit,
        **segment,
    }
    journal["phases"].append(row)
    journal["updated_at"] = utc_stamp()
    write_json_atomic(run_root / "stage_journal.json", journal)
    return {
        "complete": history_ready,
        "partial": partial,
        "row": row,
        "history": history,
        "checkpoint": output,
        "returncode": int(returncode),
    }


def _log_file(case: Path) -> Path:
    candidates = [case / "log.foamRun", case / "PyFoamRunner.foamRun.logfile"]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _log_segment(
    log: Path,
    start: int,
    destination: Path,
    *,
    returncode: int | None = None,
) -> dict[str, Any]:
    end = log.stat().st_size if log.is_file() else start
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = b""
    if log.is_file() and end >= start:
        with log.open("rb") as stream:
            stream.seek(start)
            data = stream.read(end - start)
    destination.write_bytes(data)
    text = data.decode("utf-8", errors="replace")
    event = classify_openfoam_log(text, returncode=returncode).as_dict()
    return {
        "log_path": str(destination),
        "log_byte_start": int(start),
        "log_byte_end": int(end),
        "fatal_markers": list(event.get("fatal_markers") or []),
        "warning_markers": list(event.get("warning_markers") or []),
        "openfoam_event": event,
    }


def _archive_phase_operation_logs(
    case: Path,
    destination: Path,
    phase: str,
    sequence: int,
) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=True)
    archived: dict[str, str] = {}
    for operation, source_name in (
        ("decompose", "log.decomposePar"),
        ("reconstruct", "log.reconstructPar"),
        ("runner", "log.runner"),
    ):
        source = case / source_name
        if not source.is_file():
            continue
        target = destination / f"phase_{phase}_{sequence:03d}_{operation}.log"
        shutil.copy2(source, target)
        archived[operation] = str(target)
    return archived


def _journal(run_root: Path, case_id: str) -> dict[str, Any]:
    current = read_json(run_root / "stage_journal.json", {}) or {}
    phases = list(current.get("phases") or [])
    corrections = list(current.get("classification_corrections") or [])
    for row in phases:
        if (
            str(row.get("terminal_reason") or "") == "NUMERICAL_DIVERGENCE"
            and int(row.get("returncode", 1)) == 0
            and bool((row.get("output_checkpoint") or {}).get("valid"))
        ):
            log_path = Path(str(row.get("log_path") or ""))
            text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            event = classify_openfoam_log(text, returncode=0).as_dict()
            if event["normal_end"] and not event["numerical_divergence"] and not event["setup_error"]:
                row["original_terminal_reason"] = row["terminal_reason"]
                row["terminal_reason"] = "PHASE_TARGET_REACHED"
                row["primary_error"] = None
                row["classification_correction"] = {
                    "reason": "NORMAL_SIGFPE_BANNER_WAS_NOT_AN_EXCEPTION",
                    "corrected_at": utc_stamp(),
                }
                row["openfoam_event"] = event
                corrections.append({
                    "phase": row.get("phase"),
                    "original_terminal_reason": "NUMERICAL_DIVERGENCE",
                    "corrected_terminal_reason": "PHASE_TARGET_REACHED",
                    **row["classification_correction"],
                })
    return {
        "schema_version": 2,
        "case_id": case_id,
        "phases": phases,
        "classification_corrections": corrections,
        "updated_at": utc_stamp(),
    }


def _persist_classification_repair(
    run_root: Path,
    manifest: dict[str, Any],
    journal: dict[str, Any],
    stages: list[dict[str, Any]],
    start_index: int,
    restart: dict[str, Any],
) -> dict[str, Any]:
    """Persist the legacy SIGFPE repair without launching or deleting fields."""
    corrections = list(journal.get("classification_corrections") or [])
    if not corrections:
        return manifest
    write_json_atomic(run_root / "stage_journal.json", journal)
    already_repaired = isinstance(manifest.get("classification_correction"), dict)
    if already_repaired or str(manifest.get("terminal_reason") or "") != "NUMERICAL_DIVERGENCE":
        return manifest
    original = {
        "execution_outcome": manifest.get("execution_outcome"),
        "restartable": manifest.get("restartable"),
        "current_phase": manifest.get("current_phase"),
        "terminal_reason": manifest.get("terminal_reason"),
        "primary_error": manifest.get("primary_error"),
    }
    completed = start_index >= len(stages)
    next_phase = (
        str(stages[start_index].get("stage") or "")
        if not completed
        else str(stages[-1].get("stage") or "")
    )
    correction = {
        "reason": "NORMAL_SIGFPE_BANNER_WAS_NOT_AN_EXCEPTION",
        "corrected_at": utc_stamp(),
        "original": original,
        "journal": str((run_root / "stage_journal.json").resolve()),
    }
    manifest.update(
        execution_outcome=(
            ExecutionOutcome.COMPLETED.value
            if completed
            else ExecutionOutcome.PAUSED.value
        ),
        restartable=bool(restart.get("valid")) and not completed,
        current_phase=next_phase,
        terminal_reason=("RUN_COMPLETED" if completed else "PHASE_TARGET_REACHED"),
        primary_error=None,
        classification_correction=correction,
        updated_at=utc_stamp(),
    )
    write_json_atomic(run_root / "case_manifest.json", manifest)
    write_json_atomic(
        run_root / "classification_correction.json",
        {
            "schema_version": 1,
            "case_id": manifest.get("case_id") or run_root.name,
            "status": "RECLASSIFIED_WITHOUT_SOLVER_EXECUTION",
            "restart_time": restart,
            "next_phase": next_phase,
            "corrections": corrections,
            **correction,
        },
    )
    return manifest


def _resume_cursor(
    case: Path,
    stages: list[dict[str, Any]],
    journal: dict[str, Any],
) -> tuple[int, str]:
    restart = restart_time_evidence(case)
    if not restart["valid"]:
        raise RuntimeError("RESUME_CURSOR_INCONSISTENT: no complete positive restart time")
    rows = list(journal.get("phases") or [])
    by_phase: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_phase[str(row.get("phase"))] = row
    for index, stage in enumerate(stages):
        previous = by_phase.get(str(stage["stage"]))
        if previous is None:
            return index, CONTINUE_STAGE if index > 0 else RESUME_EXISTING
        if _phase_complete(previous):
            continue
        current = float(restart["time_s"])
        if current <= float(stage["end_s"]) + 1.0e-12:
            return index, RESUME_EXISTING
        raise RuntimeError(
            f"RESUME_CURSOR_INCONSISTENT: restart t={current} exceeds phase "
            f"{stage['stage']} end={stage['end_s']}"
        )
    return len(stages), RESUME_EXISTING


def _expected_phase_start_time(
    stage: dict[str, Any],
    start_mode: str,
    restart_time: float | None,
) -> float | None:
    if start_mode == FRESH_FROM_CHECKPOINT:
        return None
    if start_mode == RESUME_EXISTING:
        return float(restart_time) if restart_time is not None else None
    planned = stage.get("start_s")
    return float(planned) if planned is not None else (
        float(restart_time) if restart_time is not None else None
    )


def _stages_for_mode(
    manifest: dict[str, Any],
    plan: dict[str, Any],
    startup_mode: str,
) -> list[dict[str, Any]]:
    stages = list(plan.get("stages") or [])
    if startup_mode != "direct":
        return stages
    stages = [dict(plan.get("direct_stage") or {})]
    if stages[0]:
        return stages
    target = float(plan["target_dt_s"])
    end = float(plan["sampling_end_s"])
    scheme = str(
        (manifest.get("effective_solver_config") or {}).get(
            "production_scheme"
        )
        or "backward"
    )
    return [{
        "stage": "DIRECT",
        "scheme": scheme,
        "dt_s": target,
        "start_s": 0.0,
        "end_s": end,
        "steps": int(math.ceil(end / target)),
        "sampling": True,
        "first_order_bootstrap": scheme.lower() == "backward",
    }]


def repair_legacy_classification(run_root: Path) -> dict[str, Any]:
    """Repair proven legacy false positives without running a solver."""
    run_root = Path(run_root).resolve()
    manifest = read_json(run_root / "case_manifest.json", {}) or {}
    plan = read_json(run_root / "stage_plan.json", {}) or {}
    case_id = str(manifest.get("case_id") or run_root.name)
    startup_mode = str(manifest.get("startup_mode") or "progressive")
    stages = _stages_for_mode(manifest, plan, startup_mode)
    if not stages:
        return manifest
    journal = _journal(run_root, case_id)
    restart = restart_time_evidence(run_root / "case")
    if not restart.get("valid"):
        return manifest
    start_index, _ = _resume_cursor(run_root / "case", stages, journal)
    temporal_false_positive = (
        str(manifest.get("terminal_reason") or "") == "ORCHESTRATION_ERROR"
        and "TEMPORAL_HISTORY_MISSING" in str(manifest.get("primary_error") or "")
        and start_index < len(stages)
        and str(stages[start_index].get("scheme") or "").lower() == "backward"
    )
    if temporal_false_positive:
        history = _history_evidence(
            run_root / "case",
            float(stages[start_index]["dt_s"]),
        )
        if history.get("valid"):
            original = {
                "execution_outcome": manifest.get("execution_outcome"),
                "restartable": manifest.get("restartable"),
                "current_phase": manifest.get("current_phase"),
                "terminal_reason": manifest.get("terminal_reason"),
                "primary_error": manifest.get("primary_error"),
            }
            correction = {
                "reason": "ROUNDED_DIRECTORY_LABELS_REPLACED_BY_PERSISTED_OPENFOAM_TIME",
                "corrected_at": utc_stamp(),
                "original": original,
                "history_evidence": history,
                "next_phase": str(stages[start_index]["stage"]),
            }
            corrections = list(journal.get("classification_corrections") or [])
            corrections.append(correction)
            journal["classification_corrections"] = corrections
            journal["updated_at"] = utc_stamp()
            write_json_atomic(run_root / "stage_journal.json", journal)
            manifest.update(
                execution_outcome=ExecutionOutcome.PAUSED.value,
                restartable=True,
                current_phase=str(stages[start_index]["stage"]),
                current_time_s=restart.get("time_s"),
                terminal_reason="TEMPORAL_HISTORY_RECOVERED",
                primary_error=None,
                classification_correction=correction,
                updated_at=utc_stamp(),
            )
            write_json_atomic(run_root / "case_manifest.json", manifest)
            write_json_atomic(
                run_root / "classification_correction.json",
                {
                    "schema_version": 1,
                    "case_id": case_id,
                    "status": "RECLASSIFIED_WITHOUT_SOLVER_EXECUTION",
                    "restart_time": restart,
                    **correction,
                },
            )
            transition_execution_state(
                run_root / "case",
                CanonicalExecutionState.PAUSED_RECOVERABLE,
                phase=str(stages[start_index]["stage"]),
                run_id=case_id,
                reason="temporal_history_recovered_from_uniform_time",
                evidence=history,
                force=bool(load_execution_state(run_root / "case")),
            )
            return manifest
    if not journal.get("classification_corrections"):
        return manifest
    return _persist_classification_repair(
        run_root,
        manifest,
        journal,
        stages,
        start_index,
        restart,
    )


def execute(
    project_root: Path,
    run_root: Path,
    *,
    run: bool,
    startup_mode: str,
    n_cores: int,
    timeout_min: float,
    automatic_core_selection: bool = True,
    renumber_before_decompose: bool = True,
) -> int:
    project_root = Path(project_root).resolve()
    run_root = Path(run_root).resolve()
    case = run_root / "case"
    metadata = read_json(run_root / "case_metadata.json", {}) or {}
    manifest = read_json(run_root / "case_manifest.json", {}) or {}
    plan = read_json(run_root / "stage_plan.json", {}) or {}
    case_id = str(manifest.get("case_id") or metadata.get("case_id") or run_root.name)
    if startup_mode not in {"progressive", "direct"}:
        raise ValueError("startup_mode must be progressive or direct")
    stages = _stages_for_mode(manifest, plan, startup_mode)
    if not stages:
        raise RuntimeError("The frozen stage plan has no executable phases")

    journal = _journal(run_root, case_id)
    restart = restart_time_evidence(case)
    if restart["valid"]:
        start_index, first_mode = _resume_cursor(case, stages, journal)
    else:
        start_index, first_mode = 0, FRESH_FROM_CHECKPOINT
    manifest = _persist_classification_repair(
        run_root,
        manifest,
        journal,
        stages,
        start_index,
        restart,
    )
    if start_index >= len(stages):
        transition_execution_state(
            case,
            CanonicalExecutionState.COMPLETED,
            phase=str(stages[-1]["stage"]),
            run_id=case_id,
            reason="all_frozen_phases_already_complete",
            evidence=restart,
            force=bool(load_execution_state(case)),
        )
        return 0

    commands = []
    for index in range(start_index, len(stages)):
        mode = first_mode if index == start_index else CONTINUE_STAGE
        expected = _expected_phase_start_time(
            stages[index],
            mode,
            restart.get("time_s"),
        )
        commands.append(
            runner_command(
                case,
                n_cores=n_cores,
                timeout_min=timeout_min,
                start_mode=mode,
                expected_start_time=expected,
                run=run,
                automatic_core_selection=automatic_core_selection,
                renumber_before_decompose=renumber_before_decompose,
            )
        )
    execution_plan = {
        "schema_version": 1,
        "case_id": case_id,
        "startup_mode": startup_mode,
        "dry_run": not run,
        "start_phase_index": start_index,
        "calculated_start_mode": first_mode,
        "stages": stages,
        "commands": commands,
        "generated_at": utc_stamp(),
    }
    write_json_atomic(run_root / "execution_plan.json", execution_plan)
    if not run:
        print(json.dumps(execution_plan, indent=2))
        return 0

    lifecycle_key = execution_idempotency_key(
        case,
        [item for command in commands for item in command],
        phase=str(stages[start_index]["stage"]),
    )
    transition_execution_state(
        case,
        CanonicalExecutionState.PREPARED,
        phase=str(stages[start_index]["stage"]),
        run_id=case_id,
        idempotency_key=lifecycle_key,
        reason="frozen_phase_plan_ready",
        evidence=restart,
        force=bool(load_execution_state(case)),
    )
    transition_execution_state(
        case,
        CanonicalExecutionState.RUNNING,
        phase=str(stages[start_index]["stage"]),
        run_id=case_id,
        idempotency_key=lifecycle_key,
        reason="phase_orchestrator_started",
        pid=os.getpid(),
    )

    manifest.update(
        execution_outcome=ExecutionOutcome.RUNNING.value,
        solver_started=False,
        terminal_reason=None,
        primary_error=None,
        updated_at=utc_stamp(),
    )
    write_json_atomic(run_root / "case_manifest.json", manifest)
    pid = os.getpid()
    runtime_base = {
        "case_id": case_id,
        "case_path": str(case),
        "display_identity": manifest.get("display_identity", case_id),
        "scientific_key": dict(manifest.get("scientific_key") or {}),
        "mesh_id": manifest.get("mesh_id"),
        "mode": "PRODUCTION",
        "queue_id": manifest.get("queue_id"),
        "queue_position": manifest.get("queue_position"),
        "queue_total": manifest.get("queue_total"),
        "PID": pid,
        "process_start_token": process_start_token(pid),
        "lease_id": f"{case_id}:{pid}:{time.time_ns()}",
        "heartbeat": utc_stamp(),
        "physical_time": restart.get("time_s"),
        "deltaT": float(manifest.get("deltaT_s") or plan.get("target_dt_s") or 0.0),
        "started_at": utc_stamp(),
    }
    acquire_solver_lease(project_root, runtime_base)
    try:
        publish_runtime(project_root, {**runtime_base, "phase": "PREPARING", "status": "PREPARING"})
        upsert_execution(
            project_root,
            {
                "run_id": case_id,
                "case_id": case_id,
                "run_kind": "CANONICAL",
                "mode": "URANS",
                "topology": manifest.get("scientific_key", {}).get("topology"),
                "mesh_level": manifest.get("scientific_key", {}).get("mesh_level"),
                "mesh_id": manifest.get("mesh_id"),
                "stage": str(stages[start_index]["stage"]),
                "status": "RUNNING",
                "case_path": str(case),
                "deltaT": manifest.get("deltaT_s"),
            },
            activate=True,
        )
    except Exception:
        release_solver_lease(project_root, str(runtime_base["lease_id"]))
        raise

    try:
        for index in range(start_index, len(stages)):
            stage = stages[index]
            phase_key = execution_idempotency_key(
                case,
                commands[index - start_index],
                phase=str(stage["stage"]),
            )
            transition_execution_state(
                case,
                CanonicalExecutionState.RUNNING,
                phase=str(stage["stage"]),
                run_id=case_id,
                idempotency_key=phase_key,
                reason="phase_started_or_resumed",
                pid=os.getpid(),
            )
            start_mode = first_mode if index == start_index else CONTINUE_STAGE
            input_checkpoint = restart_time_evidence(case)
            if start_mode != FRESH_FROM_CHECKPOINT and not input_checkpoint["valid"]:
                raise RuntimeError("STAGE_CHECKPOINT_MISSING: no complete input checkpoint")
            if str(stage.get("scheme") or "").lower() == "backward":
                history = _history_evidence(case, float(stage["dt_s"]))
                if not history["valid"]:
                    bootstrap_result = _bootstrap_backward_history(
                        case=case,
                        run_root=run_root,
                        stage=stage,
                        project_root=project_root,
                        runtime_base=runtime_base,
                        journal=journal,
                        n_cores=n_cores,
                        timeout_min=timeout_min,
                        automatic_core_selection=automatic_core_selection,
                        renumber_before_decompose=renumber_before_decompose,
                    )
                    if not bootstrap_result["complete"]:
                        reason = str(
                            (bootstrap_result.get("row") or {}).get("terminal_reason")
                            or "BACKWARD_BOOTSTRAP_FAILED"
                        )
                        manifest.update(
                            execution_outcome=(
                                ExecutionOutcome.PAUSED.value
                                if bootstrap_result.get("partial")
                                else ExecutionOutcome.ERROR.value
                            ),
                            restartable=bool(
                                (bootstrap_result.get("checkpoint") or {}).get("valid")
                            ),
                            current_phase=str(stage["stage"]),
                            current_time_s=(
                                bootstrap_result.get("checkpoint") or {}
                            ).get("time_s"),
                            terminal_reason=reason,
                            primary_error=(bootstrap_result.get("row") or {}).get(
                                "primary_error"
                            ),
                            updated_at=utc_stamp(),
                        )
                        write_json_atomic(run_root / "case_manifest.json", manifest)
                        transition_execution_state(
                            case,
                            (
                                CanonicalExecutionState.PAUSED_RECOVERABLE
                                if bootstrap_result.get("partial")
                                else CanonicalExecutionState.FAILED
                            ),
                            phase=str(stage["stage"]),
                            run_id=case_id,
                            reason=reason,
                            evidence=bootstrap_result.get("checkpoint") or {},
                            returncode=int(bootstrap_result.get("returncode") or 0),
                            force=True,
                        )
                        clear_active_runtime(
                            project_root,
                            {
                                **runtime_base,
                                "phase": str(stage["stage"]),
                                "status": manifest["execution_outcome"],
                                "terminal_reason": reason,
                            },
                        )
                        return 0 if bootstrap_result.get("partial") else 2
                    input_checkpoint = dict(bootstrap_result["checkpoint"])
                    history = dict(bootstrap_result["history"])
                    transition_execution_state(
                        case,
                        CanonicalExecutionState.RUNNING,
                        phase=str(stage["stage"]),
                        run_id=case_id,
                        idempotency_key=phase_key,
                        reason="backward_history_ready",
                        evidence=history,
                        force=True,
                    )
            else:
                history = {}
            next_stage = stages[index + 1] if index + 1 < len(stages) else {}
            preserve_history = bool(
                str(stage.get("scheme") or "").lower() == "backward"
                or str(next_stage.get("scheme") or "").lower() == "backward"
            )
            applied = configure_stage(
                case,
                stage,
                start_mode=start_mode,
                preserve_temporal_history=preserve_history,
            )
            reconstruct_times = None
            if preserve_history and int(stage.get("steps") or 0) >= 3:
                end = Decimal(str(applied["decimal_end_s"]))
                dt = Decimal(str(stage["dt_s"]))
                reconstruct_times = [float(end - 2 * dt), float(end - dt), float(end)]
            log = _log_file(case)
            byte_start = 0
            publish_runtime(
                project_root,
                {
                    **runtime_base,
                    "phase": str(stage["stage"]),
                    "status": "RUNNING",
                    "current_log": str(log),
                    "log_offset": byte_start,
                    "heartbeat": utc_stamp(),
                    "physical_time": input_checkpoint.get("time_s"),
                    "target_deltaT": float(manifest.get("deltaT_s") or plan.get("target_dt_s") or 0.0),
                    "phase_deltaT": float(stage["dt_s"]),
                },
            )
            started_at = utc_stamp()
            started = time.monotonic()
            command = runner_command(
                case,
                n_cores=n_cores,
                timeout_min=timeout_min,
                start_mode=start_mode,
                expected_start_time=(
                    float(input_checkpoint["time_s"])
                    if start_mode != FRESH_FROM_CHECKPOINT
                    else None
                ),
                run=True,
                decompose_times=(
                    list(history.get("current_and_two_previous_times") or [])
                    if history.get("valid")
                    else None
                ),
                reconstruct_times=reconstruct_times,
                automatic_core_selection=automatic_core_selection,
                renumber_before_decompose=renumber_before_decompose,
            )
            returncode, run_status = _run_phase_command(
                command,
                case=case,
                project_root=project_root,
                runtime_base={
                    **runtime_base,
                    "target_deltaT": float(manifest.get("deltaT_s") or plan.get("target_dt_s") or 0.0),
                    "phase_deltaT": float(stage["dt_s"]),
                    "deltaT": float(stage["dt_s"]),
                },
                phase=str(stage["stage"]),
                current_log=log,
                log_offset=byte_start,
            )
            elapsed = time.monotonic() - started
            output_checkpoint = restart_time_evidence(case)
            segment = _log_segment(
                log,
                byte_start,
                run_root / "logs" / f"phase_{stage['stage']}_{len(journal['phases']) + 1:03d}.log",
                returncode=returncode,
            )
            operation_logs = _archive_phase_operation_logs(
                case,
                run_root / "logs",
                str(stage["stage"]),
                len(journal["phases"]) + 1,
            )
            runner_status = str(run_status.get("status") or "")
            partial = runner_status.upper() in PARTIAL_RUNNER_STATES
            event = dict(segment.get("openfoam_event") or {})
            numerical_divergence = bool(
                event.get("numerical_divergence")
                or "DIVERG" in runner_status.upper()
            )
            setup_failed = bool(event.get("setup_error"))
            target_reached = bool(
                output_checkpoint["valid"]
                and float(output_checkpoint["time_s"]) + 1.0e-12 >= float(stage["end_s"])
            )
            terminal_reason = (
                "SETUP_FAILED" if setup_failed
                else "NUMERICAL_DIVERGENCE" if numerical_divergence
                else "USER_REQUESTED_STOP" if runner_status.upper() in USER_STOP_RUNNER_STATES
                else "RUN_TIMEOUT" if partial
                else "PHASE_TARGET_REACHED" if returncode == 0 and target_reached and bool(event.get("normal_end"))
                else "STAGE_CHECKPOINT_MISSING" if returncode == 0
                else "SOLVER_OR_ORCHESTRATION_ERROR"
            )
            row = {
                **applied,
                "actual_start_s": input_checkpoint.get("time_s") or 0.0,
                "actual_end_s": output_checkpoint.get("time_s"),
                "input_checkpoint": input_checkpoint,
                "output_checkpoint": output_checkpoint,
                "required_fields": ["U", "p", "nuTilda"],
                "common_processor_time": output_checkpoint.get("latest_common_processor_time_s"),
                "retained_old_times": history.get("retained_old_times", []),
                "backward_history": history,
                "PID": run_status.get("pid") or run_status.get("solver_pid"),
                "started_at": started_at,
                "ended_at": utc_stamp(),
                "returncode": int(returncode),
                "solver_started": bool(run_status.get("solver_started")),
                "operation_logs": operation_logs,
                "failed_stage": run_status.get("failed_stage"),
                "failed_log": run_status.get("failed_log"),
                **segment,
                "steps_completed": run_status.get("steps_completed"),
                "wall_seconds": elapsed,
                "terminal_reason": terminal_reason,
                "primary_error": None if terminal_reason == "PHASE_TARGET_REACHED" else runner_status or terminal_reason,
                "secondary_errors": [],
            }
            journal["phases"].append(row)
            journal["updated_at"] = utc_stamp()
            write_json_atomic(run_root / "stage_journal.json", journal)
            restart = output_checkpoint
            manifest.update(
                case_presence="STARTED" if output_checkpoint["valid"] else manifest.get("case_presence", "NOT_STARTED"),
                solver_started=bool(manifest.get("solver_started") or row["solver_started"]),
                current_phase=str(stage["stage"]),
                current_time_s=output_checkpoint.get("time_s"),
                restartable=bool(output_checkpoint["valid"] and not numerical_divergence and not setup_failed),
                terminal_reason=terminal_reason,
                primary_error=row["primary_error"],
                updated_at=utc_stamp(),
            )
            if numerical_divergence:
                manifest["execution_outcome"] = ExecutionOutcome.DIVERGED.value
            elif partial:
                manifest["execution_outcome"] = ExecutionOutcome.PAUSED.value
            elif terminal_reason != "PHASE_TARGET_REACHED":
                manifest["execution_outcome"] = ExecutionOutcome.ERROR.value
            else:
                manifest["execution_outcome"] = ExecutionOutcome.RUNNING.value
            write_json_atomic(run_root / "case_manifest.json", manifest)
            if manifest["execution_outcome"] != ExecutionOutcome.RUNNING.value:
                canonical_terminal = (
                    CanonicalExecutionState.PAUSED_RECOVERABLE
                    if manifest["execution_outcome"] == ExecutionOutcome.PAUSED.value
                    else CanonicalExecutionState.FAILED
                )
                transition_execution_state(
                    case,
                    canonical_terminal,
                    phase=str(stage["stage"]),
                    run_id=case_id,
                    idempotency_key=phase_key,
                    reason=terminal_reason,
                    evidence=output_checkpoint,
                    returncode=returncode,
                )
                clear_active_runtime(project_root, {**runtime_base, "phase": str(stage["stage"]), "status": manifest["execution_outcome"], "terminal_reason": terminal_reason})
                return 2 if numerical_divergence or manifest["execution_outcome"] == ExecutionOutcome.ERROR.value else 0
            if index + 1 < len(stages) and not output_checkpoint["valid"]:
                raise RuntimeError("STAGE_CHECKPOINT_MISSING")

        manifest.update(
            execution_outcome=ExecutionOutcome.COMPLETED.value,
            restartable=True,
            current_phase=str(stages[-1]["stage"]),
            terminal_reason="TARGET_END_TIME_REACHED",
            primary_error=None,
            updated_at=utc_stamp(),
        )
        write_json_atomic(run_root / "case_manifest.json", manifest)
        transition_execution_state(
            case,
            CanonicalExecutionState.COMPLETED,
            phase=str(stages[-1]["stage"]),
            run_id=case_id,
            idempotency_key=execution_idempotency_key(case, commands[-1], phase=str(stages[-1]["stage"])),
            reason="target_end_time_reached",
            evidence=restart_time_evidence(case),
            returncode=0,
        )
        clear_active_runtime(project_root, {**runtime_base, "phase": str(stages[-1]["stage"]), "status": "COMPLETED", "terminal_reason": "TARGET_END_TIME_REACHED"})
        upsert_execution(project_root, {"run_id": case_id, "case_id": case_id, "mode": "URANS", "run_kind": "CANONICAL", "status": "COMPLETED", "case_path": str(case), "stage": str(stages[-1]["stage"]), "mesh_id": manifest.get("mesh_id")}, activate=True)
        return 0
    except Exception as exc:
        primary = f"{type(exc).__name__}: {exc}"
        secondary_errors: list[str] = []
        manifest.update(
            execution_outcome=ExecutionOutcome.ERROR.value,
            restartable=bool(restart_time_evidence(case)["valid"]),
            terminal_reason="ORCHESTRATION_ERROR",
            primary_error=primary,
            secondary_errors=secondary_errors,
            updated_at=utc_stamp(),
        )
        try:
            write_json_atomic(run_root / "case_manifest.json", manifest)
        except Exception as secondary:
            secondary_errors.append(f"manifest_update: {type(secondary).__name__}: {secondary}")
        try:
            recovery = restart_time_evidence(case)
            transition_execution_state(
                case,
                (
                    CanonicalExecutionState.PAUSED_RECOVERABLE
                    if recovery.get("valid")
                    else CanonicalExecutionState.FAILED
                ),
                phase=str(manifest.get("current_phase") or stages[start_index]["stage"]),
                run_id=case_id,
                idempotency_key=lifecycle_key,
                reason=primary,
                evidence=recovery,
                returncode=2,
                force=True,
            )
            clear_active_runtime(project_root, {
                **runtime_base, "phase": manifest.get("current_phase"),
                "status": "ERROR", "terminal_reason": "ORCHESTRATION_ERROR",
                "primary_error": primary, "secondary_errors": secondary_errors,
            })
        except Exception as secondary:
            secondary_errors.append(f"runtime_cleanup: {type(secondary).__name__}: {secondary}")
        raise
    finally:
        try:
            release_solver_lease(project_root, str(runtime_base["lease_id"]))
        except Exception:
            # Lease cleanup cannot replace the solver/orchestration exception.
            if sys.exc_info()[0] is None:
                raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--startup-mode", choices=["progressive", "direct"], default="progressive")
    parser.add_argument("--n-cores", type=int, default=8)
    parser.add_argument("--timeout-min", type=float, default=1440.0)
    parser.add_argument(
        "--automatic-core-selection", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--renumber-before-decompose", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return execute(
        args.project_root,
        args.run_root,
        run=bool(args.run),
        startup_mode=args.startup_mode,
        n_cores=max(1, min(8, int(args.n_cores))),
        timeout_min=max(0.1, float(args.timeout_min)),
        automatic_core_selection=bool(args.automatic_core_selection),
        renumber_before_decompose=bool(args.renumber_before_decompose),
    )


if __name__ == "__main__":
    raise SystemExit(main())
