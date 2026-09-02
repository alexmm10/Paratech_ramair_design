#!/usr/bin/env python3
"""Four-segment Gmsh Bump matching for experimental airfoil meshes.

The implementation reproduces the case-2 density used by Gmsh's
``F_Transfinite`` distribution.  It works with physical arc lengths and never
changes the user-selected divisions.  A common endpoint size is selected so
the TE/upper/LE-or-inlet/lower interfaces have the same tangential scale.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np


SEGMENTS = ("te", "upper", "leading_or_inlet", "lower")


def _bump_density(coefficient: float, length: float, divisions: int, t: np.ndarray) -> np.ndarray:
    coefficient = float(coefficient)
    length = float(length)
    nbpt = int(divisions) + 1
    if length <= 0.0 or divisions < 2:
        raise ValueError("Bump matching requires positive length and at least two divisions")
    if coefficient <= 0.0 or math.isclose(coefficient, 1.0, abs_tol=1.0e-12):
        if math.isclose(coefficient, 1.0, abs_tol=1.0e-12):
            return np.ones_like(t)
        raise ValueError("A Gmsh Bump coefficient must be positive")
    if coefficient > 1.0:
        root = math.sqrt(coefficient - 1.0)
        a = -(4.0 * root * math.atan2(1.0, root)) / (nbpt * length)
    else:
        root = math.sqrt(1.0 - coefficient)
        ratio = abs((1.0 + 1.0 / root) / (1.0 - 1.0 / root))
        a = (2.0 * root * math.log(ratio)) / (nbpt * length)
    b = -(a * length * length) / (4.0 * (coefficient - 1.0))
    denominator = -a * (t * length - 0.5 * length) ** 2 + b
    if np.any(denominator <= 0.0) or not np.all(np.isfinite(denominator)):
        raise ValueError(f"Invalid Bump density for coefficient {coefficient:g}")
    return length / denominator


def bump_cell_sizes(
    coefficient: float,
    length: float,
    divisions: int,
    *,
    integration_points: int = 4097,
) -> np.ndarray:
    """Return physical cell sizes produced by Gmsh's symmetric Bump law."""
    coefficient = float(coefficient)
    if math.isclose(coefficient, 1.0, rel_tol=0.0, abs_tol=1.0e-10):
        return np.full(int(divisions), float(length) / int(divisions), dtype=float)
    count = max(1025, int(integration_points) | 1)
    parameter = np.linspace(0.0, 1.0, count)
    density = _bump_density(coefficient, length, divisions, parameter)
    cumulative = np.concatenate((
        [0.0],
        np.cumsum(0.5 * (density[:-1] + density[1:]) * np.diff(parameter)),
    ))
    cumulative /= cumulative[-1]
    positions = np.interp(
        np.linspace(0.0, 1.0, int(divisions) + 1), cumulative, parameter,
    ) * float(length)
    sizes = np.diff(positions)
    if np.any(sizes <= 0.0) or not np.all(np.isfinite(sizes)):
        raise ValueError("The Bump distribution generated invalid physical cell sizes")
    return sizes


def _endpoint_size(coefficient: float, length: float, divisions: int) -> float:
    return float(bump_cell_sizes(coefficient, length, divisions)[0])


def solve_bump_for_endpoint(
    length: float,
    divisions: int,
    endpoint_size: float,
    *,
    branch: str,
) -> float:
    """Solve one coefficient on the requested Gmsh branch by bisection."""
    average = float(length) / int(divisions)
    target = float(endpoint_size)
    tolerance = max(1.0e-13, 2.0e-7 * target)
    if branch == "greater":
        if target <= average:
            raise ValueError("C>1 requires endpoint size greater than the segment mean")
        low, high = 1.0 + 1.0e-7, 2.0
        while _endpoint_size(high, length, divisions) < target and high < 1.0e6:
            high *= 2.0
        if _endpoint_size(high, length, divisions) < target:
            raise ValueError("No finite C>1 Bump matches the requested endpoint size")
        increasing = True
    elif branch == "less":
        if target >= average:
            raise ValueError("0<C<1 requires endpoint size smaller than the segment mean")
        low, high = 1.0e-6, 1.0 - 1.0e-7
        if _endpoint_size(low, length, divisions) > target:
            raise ValueError("No positive C<1 Bump matches the requested endpoint size")
        increasing = True
    else:
        raise ValueError("branch must be 'greater' or 'less'")
    for _ in range(70):
        middle = 0.5 * (low + high)
        value = _endpoint_size(middle, length, divisions)
        if abs(value - target) <= tolerance:
            return middle
        if (value < target) == increasing:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def _distribution_metrics(sizes: np.ndarray) -> dict[str, float]:
    ratios = np.maximum(sizes[1:] / sizes[:-1], sizes[:-1] / sizes[1:])
    return {
        "minimum_size_m": float(np.min(sizes)),
        "maximum_size_m": float(np.max(sizes)),
        "endpoint_size_m": float(sizes[0]),
        "maximum_growth_ratio": float(np.max(ratios)) if len(ratios) else 1.0,
    }


def match_four_segment_bumps(
    lengths: dict[str, float],
    divisions: dict[str, int],
    *,
    chord: float,
    maximum_growth_ratio: float,
    maximum_size_percent_chord: float,
) -> dict[str, Any]:
    """Calculate four independent coefficients and a common junction size."""
    missing = [name for name in SEGMENTS if name not in lengths or name not in divisions]
    if missing:
        raise ValueError(f"Missing Bump segments: {missing}")
    means = {name: float(lengths[name]) / int(divisions[name]) for name in SEGMENTS}
    lower = max(means["te"], means["leading_or_inlet"])
    upper = min(means["upper"], means["lower"])
    margin = max(1.0e-10 * chord, 2.0e-5 * max(lower, upper))
    low = lower + margin
    high = upper - margin
    if not low < high:
        raise ValueError(
            "No common tangential junction size exists. Reduce TE/LE-or-inlet divisions "
            "or increase upper/lower divisions. "
            f"Required interval: ({lower:.8g}, {upper:.8g}) m."
        )

    best: tuple[float, dict[str, float], dict[str, dict[str, float]]] | None = None
    # A logarithmic search resolves the usually narrow feasible interval; a
    # second local pass avoids making the selected hJ depend on grid endpoints.
    candidates = np.geomspace(low, high, 72)
    for junction in candidates:
        try:
            coefficients = {
                "te": solve_bump_for_endpoint(lengths["te"], divisions["te"], junction, branch="greater"),
                "upper": solve_bump_for_endpoint(lengths["upper"], divisions["upper"], junction, branch="less"),
                "leading_or_inlet": solve_bump_for_endpoint(
                    lengths["leading_or_inlet"], divisions["leading_or_inlet"], junction, branch="greater"
                ),
                "lower": solve_bump_for_endpoint(lengths["lower"], divisions["lower"], junction, branch="less"),
            }
        except ValueError:
            continue
        metrics = {
            name: _distribution_metrics(
                bump_cell_sizes(coefficients[name], lengths[name], divisions[name])
            )
            for name in SEGMENTS
        }
        objective = max(item["maximum_growth_ratio"] for item in metrics.values())
        if best is None or objective < best[0]:
            best = (objective, coefficients, metrics)
    if best is None:
        raise ValueError("The four Bump coefficients could not be matched numerically")
    objective, coefficients, metrics = best
    endpoint_values = [metrics[name]["endpoint_size_m"] for name in SEGMENTS]
    junction = float(sum(endpoint_values) / len(endpoint_values))
    hmax = float(max(item["maximum_size_m"] for item in metrics.values()))
    hmax_limit = float(maximum_size_percent_chord) * float(chord) / 100.0
    warnings: list[str] = []
    if objective > float(maximum_growth_ratio):
        warnings.append(
            f"Predicted GR={objective:.5g} exceeds GRmax={float(maximum_growth_ratio):.5g}. "
            "Increase upper/lower divisions; for TE/LE-or-inlet, excessive divisions can require a stronger Bump."
        )
    if hmax > hmax_limit:
        critical = max(metrics, key=lambda name: metrics[name]["maximum_size_m"])
        warnings.append(
            f"Segment {critical} reaches hmax={hmax / chord * 100.0:.4g}%c, above "
            f"{float(maximum_size_percent_chord):.4g}%c; increase its divisions."
        )
    return {
        "status": "WARNING" if warnings else "OK",
        "junction_size_m": junction,
        "feasible_interval_m": [low, high],
        "coefficients": coefficients,
        "lengths_m": {name: float(lengths[name]) for name in SEGMENTS},
        "divisions": {name: int(divisions[name]) for name in SEGMENTS},
        "segments": metrics,
        "maximum_growth_ratio": objective,
        "maximum_size_m": hmax,
        "maximum_size_percent_chord": 100.0 * hmax / float(chord),
        "interface_ratios": {
            "te_upper": metrics["te"]["endpoint_size_m"] / metrics["upper"]["endpoint_size_m"],
            "upper_le_or_inlet": metrics["upper"]["endpoint_size_m"] / metrics["leading_or_inlet"]["endpoint_size_m"],
            "le_or_inlet_lower": metrics["leading_or_inlet"]["endpoint_size_m"] / metrics["lower"]["endpoint_size_m"],
            "lower_te": metrics["lower"]["endpoint_size_m"] / metrics["te"]["endpoint_size_m"],
        },
        "warnings": warnings,
        "blocks_generation": False,
    }

