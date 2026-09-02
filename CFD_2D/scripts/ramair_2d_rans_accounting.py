#!/usr/bin/env python3
"""Absolute SIMPLE-iteration and timing accounting for Validation Lab."""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Iterable


MINIMUM_CONVERGENCE_ITERATION = 20000
TARGETS = (20000,)

SIMPLE_EXIT_REASONS = {
    "TARGET_REACHED",
    "AUTO_CONVERGED_AFTER_MINIMUM",
    "TIMEOUT_PARTIAL",
    "USER_STOPPED_PARTIAL",
    "DIVERGED",
    "RUN_SETUP_FAILED",
    "SOLVER_ERROR",
    "ENVIRONMENT_ERROR",
    "PREMATURE_NORMAL_EXIT",
    "ORCHESTRATION_ERROR",
}


def numeric_iteration_directories(case: Path) -> list[int]:
    values: list[int] = []
    roots = [case]
    history = case / "steadyInitialization/history"
    if history.is_dir():
        roots.extend(path / "time_directories" for path in history.glob("run_*"))
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                number = float(path.name)
            except ValueError:
                continue
            if number > 0 and abs(number - round(number)) < 1.0e-8:
                values.append(int(round(number)))
    return sorted(set(values))


def _iterations_from_log(path: Path) -> list[int]:
    if not path.is_file():
        return []
    values: list[int] = []
    patterns = (
        re.compile(r"(?m)^\s*Time\s*=\s*(\d+)\s*$"),
        re.compile(r"(?m)^\s*Iteration\s*[=:]\s*(\d+)\s*$"),
    )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for pattern in patterns:
        values.extend(int(value) for value in pattern.findall(text))
    return values


def authoritative_simple_iteration(case: Path) -> dict[str, Any]:
    """Combine restart directories, runner metadata and SIMPLE log counters."""
    sources: dict[str, int] = {}
    directories = numeric_iteration_directories(case)
    if directories:
        sources["valid_steady_iteration_directory"] = max(directories)
    for relative in (
        "staged_run_status.json",
        "steadyInitialization/pending_stage.json",
        "run_status.json",
    ):
        path = case / relative
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        candidates = [
            data.get("absolute_simple_iteration"),
            data.get("latest_iteration"),
            data.get("iteration"),
            (data.get("steady_transfer") or {}).get("latest_steady_iteration"),
        ]
        finite = [
            int(round(float(value)))
            for value in candidates
            if value is not None and math.isfinite(float(value))
        ]
        if finite:
            sources[f"metadata:{relative}"] = max(finite)
    for relative in (
        "log.foamRun",
        "PyFoamRunner.foamRun.logfile",
        "log.runner",
        "steadyInitialization/log.foamRun",
    ):
        values = _iterations_from_log(case / relative)
        if values:
            sources[f"log:{relative}"] = max(values)
    absolute = max(sources.values(), default=0)
    return {
        "absolute_simple_iteration": absolute,
        "sources": sources,
        "consistent": len(set(sources.values())) <= 1 if sources else True,
    }


def target_for_iteration(
    iteration: int,
    *,
    initial: int = 20000,
    extension: int = 20000,
    maximum: int = 20000,
) -> int:
    if iteration < initial:
        return initial
    target = initial
    while target <= iteration and target < maximum:
        target = min(maximum, target + extension)
    return min(target, maximum)


def block_accounting(
    iteration: int,
    *,
    block_start: int | None = None,
    initial: int = 20000,
    extension: int = 20000,
    maximum: int = 20000,
) -> dict[str, int]:
    target = target_for_iteration(
        iteration,
        initial=initial,
        extension=extension,
        maximum=maximum,
    )
    if block_start is None:
        block_start = 0 if target == initial else target - extension
    return {
        "absolute_simple_iteration": int(iteration),
        "block_start_iteration": int(block_start),
        "block_target_iteration": int(target),
        "block_completed_iterations": max(0, int(iteration) - int(block_start)),
    }


def convergence_gate_is_allowed(
    iteration: int,
    *,
    minimum_iteration: int = MINIMUM_CONVERGENCE_ITERATION,
) -> bool:
    """Return whether statistical convergence may affect execution state."""
    return int(iteration) >= int(minimum_iteration)


def gate_is_due(
    iteration: int,
    target: int,
    *,
    minimum_iteration: int = MINIMUM_CONVERGENCE_ITERATION,
) -> bool:
    return bool(
        convergence_gate_is_allowed(
            iteration,
            minimum_iteration=minimum_iteration,
        )
        and int(iteration) >= int(target)
        and int(target) in TARGETS
    )


def classify_simple_exit(
    *,
    return_code: int | None,
    absolute_iteration: int,
    block_target_iteration: int,
    staged_status: str = "",
    explicit_stop_requested: bool = False,
    environment_failure: bool = False,
    setup_failure: bool = False,
) -> str:
    """Classify one SIMPLE process exit without inferring convergence.

    A clean process exit below the absolute block target is deliberately
    classified as ``PREMATURE_NORMAL_EXIT``. Statistical histories and a zero
    return code are never enough to promote it to completion.
    """
    status = str(staged_status or "").upper()
    if explicit_stop_requested or status in {
        "STEADY_AWAITING_USER_DECISION_STOPPED",
        "STEADY_STAGE_STOPPED_BY_USER",
        "STOPPED_BY_USER",
        "STOPPED_PARTIAL",
        "STOPPED_FORCED_PARTIAL",
    }:
        reason = "USER_STOPPED_PARTIAL"
    elif "TIMEOUT" in status:
        reason = "TIMEOUT_PARTIAL"
    elif "DIVERG" in status:
        reason = "DIVERGED"
    elif environment_failure:
        reason = "ENVIRONMENT_ERROR"
    elif setup_failure or any(
        token in status for token in ("SETUP", "CHECKMESH", "FAILED_PRE")
    ):
        reason = "RUN_SETUP_FAILED"
    elif return_code is None:
        reason = "ORCHESTRATION_ERROR"
    elif int(return_code) != 0:
        reason = "SOLVER_ERROR"
    elif int(absolute_iteration) < int(block_target_iteration):
        reason = "PREMATURE_NORMAL_EXIT"
    else:
        reason = "TARGET_REACHED"
    if reason not in SIMPLE_EXIT_REASONS:  # pragma: no cover - defensive
        raise AssertionError(reason)
    return reason


def timing_summary(
    segments: Iterable[dict[str, Any]],
    *,
    first_target: int = 10000,
) -> dict[str, Any]:
    unique_rows: list[dict[str, Any]] = []
    seen_segments: set[str] = set()
    for raw in segments:
        row = dict(raw)
        identity = str(
            row.get("segment_id")
            or (
                f"{row.get('run_id', '')}:"
                f"{row.get('iteration_start', 0)}:"
                f"{row.get('iteration_end', 0)}"
            )
        )
        if identity in seen_segments:
            continue
        seen_segments.add(identity)
        unique_rows.append(row)
    rows = sorted(
        unique_rows,
        key=lambda row: (
            int(row.get("iteration_start") or 0),
            int(row.get("iteration_end") or 0),
        ),
    )
    solver_seconds = 0.0
    setup_seconds = 0.0
    post_seconds = 0.0
    elapsed_seconds = 0.0
    samples: list[float] = []
    counted = 0
    iteration_rates: dict[int, float] = {}
    time_by_segment: list[dict[str, Any]] = []
    for row in rows:
        start = int(row.get("iteration_start") or 0)
        end = int(row.get("iteration_end") or start)
        if end <= start:
            continue
        span = end - start
        full_active = float(row.get("active_solver_seconds") or 0.0)
        rate = full_active / span
        contributed = 0
        for iteration in range(start + 1, end + 1):
            if iteration in iteration_rates:
                continue
            iteration_rates[iteration] = rate
            contributed += 1
        time_by_segment.append(
            {
                "segment_id": row.get("segment_id"),
                "run_id": row.get("run_id"),
                "iteration_start": start,
                "iteration_end": end,
                "iterations_contributed": contributed,
                "overlap_iterations_discarded": span - contributed,
                "active_solver_seconds": rate * contributed,
                "seconds_per_iteration": rate,
            }
        )
        overlap = max(0, min(end, first_target) - min(start, first_target))
        fraction = min(1.0, overlap / span)
        active = float(row.get("active_solver_seconds") or 0.0) * fraction
        solver_seconds += active
        setup_seconds += float(row.get("setup_seconds") or 0.0) * fraction
        post_seconds += float(row.get("post_seconds") or 0.0) * fraction
        elapsed_seconds += float(
            row.get("total_elapsed_seconds")
            or (
                float(row.get("active_solver_seconds") or 0.0)
                + float(row.get("setup_seconds") or 0.0)
                + float(row.get("post_seconds") or 0.0)
            )
        ) * fraction
    first_rates = [
        rate
        for iteration, rate in sorted(iteration_rates.items())
        if 1 <= iteration <= first_target
    ]
    samples = first_rates
    counted = len(first_rates)
    complete = counted >= first_target
    ordered = sorted(samples)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
        return ordered[index]

    median = statistics.median(ordered) if ordered else None
    all_rates = sorted(iteration_rates.values())
    all_median = statistics.median(all_rates) if all_rates else None
    total_solver_seconds = float(sum(all_rates))
    total_iterations = len(all_rates)
    return {
        "timing_status": "COMPLETE" if complete else "PARTIAL",
        "solver_active_wall_time_first_10000_s": (
            solver_seconds if complete else None
        ),
        "total_elapsed_wall_time_to_10000_s": (
            elapsed_seconds if complete else None
        ),
        "setup_overhead_to_10000_s": setup_seconds if complete else None,
        "monitoring_overhead_estimate_s": (
            max(0.0, elapsed_seconds - solver_seconds - setup_seconds - post_seconds)
            if complete
            else None
        ),
        "median_seconds_per_iteration": median,
        "p25_seconds_per_iteration": percentile(0.25),
        "p75_seconds_per_iteration": percentile(0.75),
        "normalized_hours_per_10000_iterations": (
            median * first_target / 3600.0 if median is not None else None
        ),
        "iterations_accounted": counted,
        "solver_active_total_seconds": total_solver_seconds,
        "total_iterations_solved": total_iterations,
        "mean_solver_seconds_per_iteration": (
            total_solver_seconds / total_iterations if total_iterations else None
        ),
        "median_solver_seconds_per_iteration": all_median,
        "time_first_10000_iterations": (
            float(sum(first_rates)) if complete else None
        ),
        "time_by_segment": time_by_segment,
        "overlap_iterations_discarded": sum(
            int(row["overlap_iterations_discarded"])
            for row in time_by_segment
        ),
    }
