#!/usr/bin/env python3
"""Pure RANS/SIMPLE convergence and numerical-plateau evaluation."""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


RANS_AUTO_CONVERGED_STRICT = "RANS_AUTO_CONVERGED_STRICT"
RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING = (
    "RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING"
)
RANS_REVIEW_REQUIRED = "RANS_REVIEW_REQUIRED"
RANS_DIVERGED = "RANS_DIVERGED"

AUTOMATIC_GATE_STATUSES = {
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    RANS_REVIEW_REQUIRED,
    RANS_DIVERGED,
}


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def canonical_residual_field(name: str) -> str | None:
    """Map OpenFOAM component names to the three configured RANS fields."""
    token = str(name).strip()
    lowered = token.lower()
    if lowered in {"p", "p_rgh"}:
        return "p"
    if lowered.startswith("u"):
        return "U"
    if lowered in {"nutilda", "nutilde"}:
        return "nuTilda"
    if lowered == "continuity_global":
        return "continuity"
    return None


def robust_log_slope(x: Iterable[float], residual: Iterable[float]) -> float | None:
    """Return a Theil-Sen-like slope on binned log10 residuals.

    At most 32 median bins are used, so the pairwise median remains bounded for
    long SIMPLE histories while resisting isolated spikes.
    """
    frame = pd.DataFrame(
        {
            "x": pd.to_numeric(pd.Series(list(x)), errors="coerce"),
            "r": pd.to_numeric(pd.Series(list(residual)), errors="coerce"),
        }
    )
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    frame = frame[frame["r"] > 0.0].sort_values("x")
    if len(frame) < 3 or frame["x"].nunique() < 2:
        return None
    group_count = min(32, max(3, int(math.sqrt(len(frame)))))
    bins = np.array_split(frame.to_numpy(dtype=float), group_count)
    points = [
        (float(np.median(block[:, 0])), float(np.median(np.log10(block[:, 1]))))
        for block in bins
        if len(block)
    ]
    slopes: list[float] = []
    for index, (x0, y0) in enumerate(points):
        for x1, y1 in points[index + 1 :]:
            if x1 != x0:
                slopes.append((y1 - y0) / (x1 - x0))
    return float(np.median(slopes)) if slopes else None


def residual_block_statistics(
    residuals: pd.DataFrame,
    *,
    extension_iterations: int,
) -> list[dict[str, Any]]:
    """Summarize residuals in physical extension-sized SIMPLE blocks."""
    if residuals.empty or extension_iterations <= 0:
        return []
    required = {"Time", "field", "initial_residual"}
    if not required.issubset(residuals.columns):
        return []
    frame = residuals[list(required)].copy()
    frame["iteration"] = pd.to_numeric(frame["Time"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["initial_residual"], errors="coerce")
    frame["canonical_field"] = frame["field"].map(canonical_residual_field)
    frame = frame.dropna(subset=["iteration", "value", "canonical_field"])
    frame = frame[
        np.isfinite(frame["iteration"])
        & np.isfinite(frame["value"])
        & (frame["value"] >= 0.0)
    ]
    frame = frame[frame["canonical_field"] != "continuity"]
    if frame.empty:
        return []
    frame["block"] = (
        np.floor(np.maximum(frame["iteration"] - 1.0, 0.0) / extension_iterations)
        .astype(int)
        + 1
    )
    rows: list[dict[str, Any]] = []
    for (field, block), values in frame.groupby(
        ["canonical_field", "block"], sort=True
    ):
        values = values.sort_values("iteration")
        positive = values[values["value"] > 0.0]
        rows.append(
            {
                "field": str(field),
                "block": int(block),
                "block_start": float(values["iteration"].min()),
                "block_end": float(values["iteration"].max()),
                "samples": int(len(values)),
                "median_residual": (
                    float(positive["value"].median()) if not positive.empty else None
                ),
                "final_residual": float(values["value"].iloc[-1]),
                "log10_slope_per_iteration": robust_log_slope(
                    positive["iteration"], positive["value"]
                ),
            }
        )
    return rows


def block_diagnostics(
    residual_blocks: list[dict[str, Any]],
    force_frame: pd.DataFrame,
    residuals: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Combine residual, force and continuity evidence for each SIMPLE block."""
    if not residual_blocks:
        return []
    force = force_frame.copy()
    if not force.empty:
        force["iteration"] = pd.to_numeric(
            force.get("Time", pd.Series(np.arange(len(force)))),
            errors="coerce",
        )
        for field in ("Cl", "Cd", "Cm"):
            force[field] = pd.to_numeric(force.get(field), errors="coerce")
    continuity = pd.DataFrame()
    if not residuals.empty and {
        "Time",
        "field",
        "initial_residual",
    }.issubset(residuals.columns):
        continuity = residuals[
            residuals["field"].astype(str).str.lower() == "continuity_global"
        ].copy()
        continuity["iteration"] = pd.to_numeric(
            continuity["Time"], errors="coerce"
        )
        continuity["value"] = pd.to_numeric(
            continuity["initial_residual"], errors="coerce"
        )
    block_ids = sorted({int(row["block"]) for row in residual_blocks})
    summaries: list[dict[str, Any]] = []
    for block_id in block_ids:
        residual_rows = [
            row for row in residual_blocks if int(row["block"]) == block_id
        ]
        start = min(float(row["block_start"]) for row in residual_rows)
        end = max(float(row["block_end"]) for row in residual_rows)
        force_rows = force[
            (force.get("iteration") >= start) & (force.get("iteration") <= end)
        ] if not force.empty else pd.DataFrame()
        force_metrics: dict[str, dict[str, Any]] = {}
        if not force_rows.empty:
            for field in ("Cl", "Cd", "Cm"):
                values = force_rows[field].replace([np.inf, -np.inf], np.nan).dropna()
                if values.empty:
                    continue
                x = force_rows.loc[values.index, "iteration"].to_numpy(dtype=float)
                drift = (
                    float(np.polyfit(x, values.to_numpy(dtype=float), 1)[0])
                    if len(values) > 2 and np.ptp(x) > 0.0
                    else 0.0
                )
                force_metrics[field] = {
                    "mean": float(values.mean()),
                    "standard_deviation": (
                        float(values.std(ddof=1)) if len(values) > 1 else 0.0
                    ),
                    "drift_per_iteration": drift,
                    "samples": int(len(values)),
                }
        continuity_rows = continuity[
            (continuity.get("iteration") >= start)
            & (continuity.get("iteration") <= end)
        ] if not continuity.empty else pd.DataFrame()
        continuity_metrics: dict[str, Any] = {}
        if not continuity_rows.empty:
            values = continuity_rows["value"].abs().dropna()
            if not values.empty:
                continuity_metrics = {
                    "median_absolute": float(values.median()),
                    "maximum_absolute": float(values.max()),
                    "samples": int(len(values)),
                }
        summaries.append({
            "block": block_id,
            "block_start": start,
            "block_end": end,
            "residuals": {
                str(row["field"]): {
                    "median_residual": row.get("median_residual"),
                    "final_residual": row.get("final_residual"),
                    "log10_slope_per_iteration": row.get(
                        "log10_slope_per_iteration"
                    ),
                }
                for row in residual_rows
            },
            "forces": force_metrics,
            "continuity": continuity_metrics,
        })
    return summaries


def _force_statistics(
    force_frame: pd.DataFrame,
    *,
    window_samples: int,
    mean_tolerance_percent: float,
    fluctuation_tolerance_percent: float,
) -> dict[str, Any]:
    required = {"Cl", "Cd", "Cm"}
    if force_frame.empty or not required.issubset(force_frame.columns):
        return {
            "status": "INSUFFICIENT_DATA",
            "pass": False,
            "metrics": {},
            "hard_failures": ["force_history_incomplete"],
        }
    frame = force_frame.copy()
    for name in required:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    if frame[list(required)].isna().any().any():
        return {
            "status": "NON_FINITE",
            "pass": False,
            "metrics": {},
            "hard_failures": ["non_finite_force_coefficient"],
        }
    if (frame[list(required)].abs() > 1.0e4).any().any():
        return {
            "status": "RUNAWAY",
            "pass": False,
            "metrics": {},
            "hard_failures": ["force_runaway"],
        }
    frame["Cl_over_Cd"] = np.where(
        frame["Cd"].abs() > 1.0e-12,
        frame["Cl"] / frame["Cd"],
        np.nan,
    )
    window = max(16, int(window_samples))
    if len(frame) < 2 * window:
        return {
            "status": "INSUFFICIENT_DATA",
            "pass": False,
            "metrics": {},
            "hard_failures": [],
            "samples": int(len(frame)),
            "required_samples": 2 * window,
        }
    previous = frame.iloc[-2 * window : -window]
    current = frame.iloc[-window:]
    metrics: dict[str, dict[str, Any]] = {}
    for name in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
        previous_values = pd.to_numeric(previous[name], errors="coerce").dropna()
        current_values = pd.to_numeric(current[name], errors="coerce").dropna()
        if previous_values.empty or current_values.empty:
            metrics[name] = {"stable": False, "reason": "NO_FINITE_VALUES"}
            continue
        previous_mean = float(previous_values.mean())
        current_mean = float(current_values.mean())
        scale = max(abs(previous_mean), abs(current_mean), 1.0e-8)
        mean_change = 100.0 * abs(current_mean - previous_mean) / scale
        standard_deviation = (
            float(current_values.std(ddof=1)) if len(current_values) > 1 else 0.0
        )
        fluctuation = 100.0 * standard_deviation / scale
        x = np.arange(len(current_values), dtype=float)
        drift = (
            float(np.polyfit(x, current_values.to_numpy(), 1)[0])
            if len(current_values) > 2
            else 0.0
        )
        drift_percent = 100.0 * abs(drift) * len(current_values) / scale
        stable = (
            mean_change <= mean_tolerance_percent
            and fluctuation <= fluctuation_tolerance_percent
            and drift_percent <= mean_tolerance_percent
        )
        metrics[name] = {
            "previous_mean": previous_mean,
            "mean": current_mean,
            "standard_deviation": standard_deviation,
            "mean_change_percent": mean_change,
            "fluctuation_percent": fluctuation,
            "drift_per_iteration": drift,
            "drift_percent_per_window": drift_percent,
            "mean_tolerance_percent": mean_tolerance_percent,
            "fluctuation_tolerance_percent": fluctuation_tolerance_percent,
            "stable": bool(stable),
        }
    return {
        "status": "STABLE" if all(row.get("stable") for row in metrics.values()) else "UNSTABLE",
        "pass": bool(all(row.get("stable") for row in metrics.values())),
        "metrics": metrics,
        "hard_failures": [],
        "window_samples": window,
    }


def _continuity_statistics(residuals: pd.DataFrame) -> dict[str, Any]:
    if residuals.empty or "field" not in residuals:
        return {"status": "NOT_AVAILABLE", "pass": False}
    rows = residuals[
        residuals["field"].astype(str).str.lower() == "continuity_global"
    ].copy()
    if rows.empty:
        return {"status": "NOT_AVAILABLE", "pass": False}
    values = pd.to_numeric(rows["initial_residual"], errors="coerce")
    if not np.isfinite(values.dropna()).all() or values.dropna().empty:
        return {
            "status": "NON_FINITE",
            "pass": False,
            "hard_failure": "continuity_non_finite",
        }
    values = values.abs().dropna()
    window = min(max(16, len(values) // 10), len(values))
    current = values.iloc[-window:]
    previous = values.iloc[-2 * window : -window]
    current_median = float(current.median())
    previous_median = (
        float(previous.median()) if not previous.empty else current_median
    )
    growth_ratio = current_median / max(previous_median, 1.0e-30)
    bounded = float(current.max()) <= max(1.0e-2, 20.0 * max(previous_median, 1.0e-12))
    stable = bounded and growth_ratio <= 2.0
    return {
        "status": "STABLE" if stable else "UNSTABLE",
        "pass": bool(stable),
        "median": current_median,
        "previous_median": previous_median,
        "growth_ratio": growth_ratio,
        "maximum": float(current.max()),
        "samples": int(len(values)),
    }


def _residual_metrics(
    checkpoint: dict[str, Any],
    residuals: pd.DataFrame,
    limits: dict[str, float],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, float] = {}
    if not residuals.empty and {"field", "initial_residual"}.issubset(residuals):
        frame = residuals.copy()
        frame["canonical"] = frame["field"].map(canonical_residual_field)
        frame["value"] = pd.to_numeric(
            frame["initial_residual"], errors="coerce"
        )
        frame = frame.dropna(subset=["canonical", "value"])
        for field in ("p", "U", "nuTilda"):
            values = frame[frame["canonical"] == field]["value"]
            finite = values[np.isfinite(values)]
            if not finite.empty:
                latest[field] = float(finite.iloc[-1])
    legacy = (checkpoint.get("gate") or {}).get("residual_metrics") or {}
    rows: dict[str, dict[str, Any]] = {}
    for field in ("p", "U", "nuTilda"):
        value = latest.get(field)
        if value is None:
            value = _finite_float((legacy.get(field) or {}).get("last_initial_residual"))
        limit = float(limits[field])
        rows[field] = {
            "last_initial_residual": value,
            "preferred_limit": limit,
            "pass": value is not None and value <= limit,
            "available": value is not None,
        }
    return rows


def _plateau_for_field(
    field: str,
    blocks: list[dict[str, Any]],
    *,
    required_blocks: int,
    log_improvement_min: float,
    relative_improvement_min: float,
) -> dict[str, Any]:
    rows = sorted(
        (row for row in blocks if row.get("field") == field),
        key=lambda row: int(row["block"]),
    )
    comparisons: list[dict[str, Any]] = []
    for previous, current in zip(rows, rows[1:]):
        old = _finite_float(previous.get("median_residual"))
        new = _finite_float(current.get("median_residual"))
        if old is None or new is None or old <= 0.0 or new <= 0.0:
            continue
        decade_improvement = math.log10(old) - math.log10(new)
        relative_improvement = (old - new) / old
        comparisons.append(
            {
                "previous_block": int(previous["block"]),
                "current_block": int(current["block"]),
                "decade_improvement": decade_improvement,
                "relative_improvement": relative_improvement,
                "meaningful_improvement": bool(
                    decade_improvement >= log_improvement_min
                    or relative_improvement >= relative_improvement_min
                ),
            }
        )
    tail = comparisons[-required_blocks:]
    plateau = len(tail) == required_blocks and not any(
        row["meaningful_improvement"] for row in tail
    )
    return {
        "field": field,
        "detected": plateau,
        "required_consecutive_blocks": required_blocks,
        "comparisons": comparisons,
        "decision_window": tail,
    }


def evaluate_rans_gate(
    checkpoint: dict[str, Any],
    force_frame: pd.DataFrame,
    residuals: pd.DataFrame,
    *,
    rans_config: dict[str, Any],
    convergence_config: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate strict convergence and the guarded one-residual plateau rule."""
    absolute_iteration_raw = checkpoint.get(
        "absolute_simple_iteration",
        checkpoint.get("iterations_completed"),
    )
    minimum_iteration = int(
        rans_config.get(
            "minimum_simple_iterations_before_convergence_check",
            10000,
        )
    )
    if absolute_iteration_raw is not None:
        absolute_iteration = int(float(absolute_iteration_raw))
        if absolute_iteration < minimum_iteration:
            return {
                "schema_version": 3,
                "status": RANS_REVIEW_REQUIRED,
                "recommendation": "WAIT_UNTIL_MINIMUM_ITERATION",
                "gate_evaluated": False,
                "strict_pass": False,
                "plateau_warning_pass": False,
                "absolute_simple_iteration": absolute_iteration,
                "minimum_convergence_iteration": minimum_iteration,
                "iterations_before_gate": minimum_iteration - absolute_iteration,
                "hard_failures": [],
                "missing_required_residual_fields": [],
                "soft_residual_failures": [],
                "extension_recommended": True,
                "checkpoint_status": str(checkpoint.get("status") or ""),
            }
    limits = {
        field: float((rans_config.get("residual_tolerances") or {}).get(field, 1.0e-5))
        for field in ("p", "U", "nuTilda")
    }
    extension = int(convergence_config.get("extension_iterations", 2500))
    preferred_override = _finite_float(
        convergence_config.get("pressure_residual_preferred_limit")
    )
    if preferred_override is not None:
        limits["p"] = preferred_override
    multiplier = float(
        convergence_config.get("pressure_residual_plateau_multiplier", 10.0)
    )
    absolute_ceiling = float(
        convergence_config.get("pressure_residual_absolute_ceiling", 1.0e-2)
    )
    ceilings = {
        field: min(limit * multiplier, absolute_ceiling)
        for field, limit in limits.items()
    }
    force = _force_statistics(
        force_frame,
        window_samples=int(rans_config.get("force_window_samples", 500)),
        mean_tolerance_percent=float(
            rans_config.get("force_mean_tolerance_percent", 1.0)
        ),
        fluctuation_tolerance_percent=float(
            rans_config.get("force_fluctuation_tolerance_percent", 2.0)
        ),
    )
    continuity = _continuity_statistics(residuals)
    residual_metrics = _residual_metrics(checkpoint, residuals, limits)
    blocks = residual_block_statistics(
        residuals, extension_iterations=extension
    )
    block_summaries = block_diagnostics(blocks, force_frame, residuals)
    required_plateau_blocks = int(
        convergence_config.get("consecutive_plateau_blocks", 2)
    )
    plateaus = {
        field: _plateau_for_field(
            field,
            blocks,
            required_blocks=required_plateau_blocks,
            log_improvement_min=float(
                convergence_config.get(
                    "plateau_log_decade_improvement_min", 0.10
                )
            ),
            relative_improvement_min=float(
                convergence_config.get(
                    "plateau_relative_improvement_min", 0.20
                )
            ),
        )
        for field in ("p", "U", "nuTilda")
    }
    fatal_statuses = {
        "RANS_BASE_DIVERGED",
        "RANS_BASE_FAILED",
        "STEADY_STAGE_DIVERGED",
        "STEADY_STAGE_FAILED",
    }
    hard_failures = list(force.get("hard_failures") or [])
    continuity_hard = continuity.get("hard_failure")
    if continuity_hard:
        hard_failures.append(str(continuity_hard))
    checkpoint_status = str(checkpoint.get("status") or "")
    if checkpoint_status in fatal_statuses:
        hard_failures.append(f"solver_status:{checkpoint_status}")
    missing_fields = [
        field for field, row in residual_metrics.items() if not row["available"]
    ]
    soft_failures = [
        field for field, row in residual_metrics.items() if not row["pass"]
    ]
    stationarity_passes = bool(force.get("pass"))
    continuity_passes = bool(continuity.get("pass"))
    strict_pass = (
        not hard_failures
        and not missing_fields
        and stationarity_passes
        and continuity_passes
        and not soft_failures
    )
    plateau_field = soft_failures[0] if len(soft_failures) == 1 else None
    plateau_pass = bool(
        not hard_failures
        and not missing_fields
        and stationarity_passes
        and continuity_passes
        and bool(convergence_config.get("allow_single_soft_failure", True))
        and plateau_field is not None
        and residual_metrics[plateau_field]["last_initial_residual"]
        <= ceilings[plateau_field]
        and plateaus[plateau_field]["detected"]
    )
    if hard_failures:
        automatic_status = RANS_DIVERGED
        recommendation = "REJECT_RECOMMENDED"
    elif strict_pass:
        automatic_status = RANS_AUTO_CONVERGED_STRICT
        recommendation = "AUTO_CONVERGED_STRICT"
    elif plateau_pass:
        automatic_status = RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING
        recommendation = "AUTO_CONVERGED_WITH_PLATEAU_WARNING"
    else:
        automatic_status = RANS_REVIEW_REQUIRED
        no_improvement = bool(
            soft_failures
            and all(plateaus[field]["detected"] for field in soft_failures)
            and stationarity_passes
            and continuity_passes
        )
        recommendation = (
            "REVIEW_RECOMMENDED" if no_improvement else "EXTENSION_RECOMMENDED"
        )
    no_meaningful_improvement = bool(
        soft_failures
        and all(plateaus[field]["detected"] for field in soft_failures)
        and stationarity_passes
        and continuity_passes
    )
    return {
        "schema_version": 3,
        "status": automatic_status,
        "recommendation": recommendation,
        "gate_evaluated": True,
        "absolute_simple_iteration": (
            int(float(absolute_iteration_raw))
            if absolute_iteration_raw is not None
            else None
        ),
        "minimum_convergence_iteration": minimum_iteration,
        "strict_pass": strict_pass,
        "plateau_warning_pass": plateau_pass,
        "hard_failures": hard_failures,
        "missing_required_residual_fields": missing_fields,
        "soft_residual_failures": soft_failures,
        "exactly_one_soft_residual_failure": len(soft_failures) == 1,
        "force_stationarity": force,
        "continuity": continuity,
        "residual_metrics": residual_metrics,
        "residual_limits": {
            "preferred": limits,
            "plateau_ceiling": ceilings,
            "pressure_formula": (
                "min(preferred_limit * plateau_multiplier, absolute_ceiling)"
            ),
        },
        "residual_blocks": blocks,
        "block_summaries": block_summaries,
        "plateau": plateaus,
        "no_meaningful_extension_improvement": no_meaningful_improvement,
        "extension_recommended": (
            recommendation == "EXTENSION_RECOMMENDED"
        ),
        "checkpoint_status": checkpoint_status,
    }
