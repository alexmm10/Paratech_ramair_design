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


def feasible_automatic_divisions(
    lengths: dict[str, float],
    divisions: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """Increase curved-segment divisions only when exact junction matching needs it."""
    selected = {name: int(divisions[name]) for name in SEGMENTS}
    requested = dict(selected)
    body_mean_limit = min(
        float(lengths["upper"]) / selected["upper"],
        float(lengths["lower"]) / selected["lower"],
    )
    warnings: list[str] = []
    for name in ("te", "leading_or_inlet"):
        minimum = max(
            2,
            int(math.floor(float(lengths[name]) / (0.98 * body_mean_limit))) + 1,
        )
        if selected[name] < minimum:
            selected[name] = minimum
            warnings.append(
                f"Automatic matching increased {name} divisions from "
                f"{requested[name]} to {minimum} after segment extension."
            )
    return selected, warnings


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


def partition_composite_bump(
    section_lengths: dict[str, float],
    divisions: int,
    coefficient: float,
    *,
    minimum_divisions_per_section: int = 4,
) -> dict[str, Any]:
    """Partition one conceptual Bump distribution over connected real curves.

    The returned sections retain the cell sizes of the conceptual curve at each
    split.  Monotone pieces can therefore be represented by a local Gmsh
    Progression without replacing the physical geometry by an overlapping
    auxiliary spline.
    """
    names = list(section_lengths)
    lengths = np.asarray([float(section_lengths[name]) for name in names], dtype=float)
    if len(names) < 2 or np.any(lengths <= 0.0):
        raise ValueError("A composite Bump requires at least two positive sections")
    minimum = max(2, int(minimum_divisions_per_section))
    total_divisions = int(divisions)
    if total_divisions < minimum * len(names):
        raise ValueError("Too few divisions for the composite Bump sections")

    sizes = bump_cell_sizes(float(coefficient), float(np.sum(lengths)), total_divisions)
    positions = np.concatenate(([0.0], np.cumsum(sizes)))
    targets = np.cumsum(lengths)[:-1]
    cuts: list[int] = []
    previous = 0
    for index, target in enumerate(targets):
        remaining_sections = len(names) - index - 1
        lower = previous + minimum
        upper = total_divisions - minimum * remaining_sections
        selected = int(np.argmin(np.abs(positions[lower : upper + 1] - target))) + lower
        cuts.append(selected)
        previous = selected
    bounds = [0, *cuts, total_divisions]

    sections: dict[str, dict[str, float | int]] = {}
    for index, name in enumerate(names):
        start, end = bounds[index], bounds[index + 1]
        local_sizes = sizes[start:end]
        count = end - start
        first = float(local_sizes[0])
        last = float(local_sizes[-1])
        progression = (
            float((last / first) ** (1.0 / (count - 1))) if count > 1 else 1.0
        )
        sections[name] = {
            "divisions": count,
            "target_length_m": float(lengths[index]),
            "represented_length_m": float(np.sum(local_sizes)),
            "first_size_m": first,
            "last_size_m": last,
            "progression_coefficient": progression,
        }
    return {
        "total_length_m": float(np.sum(lengths)),
        "divisions": total_divisions,
        "bump_coefficient": float(coefficient),
        "sections": sections,
        "split_position_errors_m": [
            float(positions[cut] - target) for cut, target in zip(cuts, targets)
        ],
    }


def _geometric_sizes(length: float, divisions: int, ratio: float) -> np.ndarray:
    count = int(divisions)
    value = float(ratio)
    if count < 2 or value <= 0.0:
        raise ValueError("A progression requires at least two divisions and ratio > 0")
    if math.isclose(value, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        return np.full(count, float(length) / count, dtype=float)
    exponents = np.arange(count, dtype=float) * math.log(value)
    exponents -= float(np.max(exponents))
    weights = np.exp(exponents)
    return float(length) * weights / float(np.sum(weights))


def solve_progression_for_fixed_endpoint(
    length: float,
    divisions: int,
    endpoint_size: float,
    *,
    endpoint: str,
) -> float:
    """Solve a Gmsh per-cell Progression ratio for one fixed endpoint size."""
    if endpoint not in {"first", "last"}:
        raise ValueError("endpoint must be 'first' or 'last'")
    target = float(endpoint_size)
    if not 0.0 < target < float(length):
        raise ValueError("Progression endpoint size must lie between zero and its length")
    low_log, high_log = -12.0, 12.0
    for _ in range(90):
        middle_log = 0.5 * (low_log + high_log)
        ratio = math.exp(middle_log)
        sizes = _geometric_sizes(length, divisions, ratio)
        value = float(sizes[0] if endpoint == "first" else sizes[-1])
        increasing = endpoint == "last"
        if (value < target) == increasing:
            low_log = middle_log
        else:
            high_log = middle_log
    return math.exp(0.5 * (low_log + high_log))


def match_extended_inlet_distribution(
    section_lengths: dict[str, float],
    divisions: int,
    junction_size: float,
    *,
    minimum_divisions_per_section: int = 4,
) -> dict[str, Any]:
    """Match wall extensions and the virtual inlet without changing the wall."""
    required = {"upper_wall_extension", "virtual_inlet", "lower_wall_extension"}
    if set(section_lengths) != required:
        raise ValueError(f"Extended inlet sections must be exactly {sorted(required)}")
    total = int(divisions)
    minimum = max(3, int(minimum_divisions_per_section))
    lengths = {name: float(section_lengths[name]) for name in required}
    if total < 3 * minimum or any(value <= 0.0 for value in lengths.values()):
        raise ValueError("Invalid extended inlet lengths or division count")

    upper_guess = lengths["upper_wall_extension"] / float(junction_size)
    lower_guess = lengths["lower_wall_extension"] / float(junction_size)
    upper_range = range(
        minimum,
        min(total - 2 * minimum, max(minimum + 1, int(math.ceil(2.0 * upper_guess)) + 12)),
    )
    lower_limit = min(
        total - 2 * minimum,
        max(minimum + 1, int(math.ceil(2.0 * lower_guess)) + 12),
    )
    upper_candidates: list[tuple[int, float, np.ndarray]] = []
    for upper_count in upper_range:
        upper_ratio = solve_progression_for_fixed_endpoint(
            lengths["upper_wall_extension"], upper_count, junction_size, endpoint="last"
        )
        upper_sizes = _geometric_sizes(
            lengths["upper_wall_extension"], upper_count, upper_ratio
        )
        upper_candidates.append((upper_count, upper_ratio, upper_sizes))
    lower_candidates: list[tuple[int, float, np.ndarray]] = []
    for lower_count in range(minimum, lower_limit):
        lower_ratio = solve_progression_for_fixed_endpoint(
            lengths["lower_wall_extension"], lower_count, junction_size, endpoint="first"
        )
        lower_sizes = _geometric_sizes(
            lengths["lower_wall_extension"], lower_count, lower_ratio
        )
        lower_candidates.append((lower_count, lower_ratio, lower_sizes))

    # Rank inexpensive geometric candidates first.  Solving the virtual-inlet
    # Bump by bisection for every Cartesian pair was needlessly quadratic and
    # made a UI diagnostic take tens of seconds.  The shortlist retains the
    # best lip match, balanced division count and smoothest extension laws.
    ranked: list[tuple[float, tuple[int, float, np.ndarray], tuple[int, float, np.ndarray]]] = []
    for upper in upper_candidates:
        upper_count, upper_ratio, upper_sizes = upper
        for lower in lower_candidates:
            lower_count, lower_ratio, lower_sizes = lower
            inlet_count = total - upper_count - lower_count
            if inlet_count < minimum:
                continue
            lip_target = math.sqrt(float(upper_sizes[0] * lower_sizes[-1]))
            inlet_mean = lengths["virtual_inlet"] / inlet_count
            rank = (
                abs(math.log(float(upper_sizes[0] / lower_sizes[-1])))
                + 0.08 * abs(math.log(lip_target / inlet_mean))
                + 0.02 * max(
                    upper_ratio, 1.0 / upper_ratio,
                    lower_ratio, 1.0 / lower_ratio,
                )
            )
            ranked.append((rank, upper, lower))

    best: tuple[float, dict[str, Any]] | None = None
    for _, upper, lower in sorted(ranked, key=lambda item: item[0])[:192]:
        upper_count, upper_ratio, upper_sizes = upper
        lower_count, lower_ratio, lower_sizes = lower
        inlet_count = total - upper_count - lower_count
        lip_target = math.sqrt(float(upper_sizes[0] * lower_sizes[-1]))
        inlet_mean = lengths["virtual_inlet"] / inlet_count
        try:
            if math.isclose(lip_target, inlet_mean, rel_tol=1.0e-7):
                inlet_bump = 1.0
            else:
                branch = "greater" if lip_target > inlet_mean else "less"
                inlet_bump = solve_bump_for_endpoint(
                    lengths["virtual_inlet"], inlet_count, lip_target, branch=branch
                )
        except ValueError:
            continue
        inlet_sizes = bump_cell_sizes(
            inlet_bump, lengths["virtual_inlet"], inlet_count
        )
        interface_ratios = {
                "upper_wall_to_virtual_inlet": max(
                    float(upper_sizes[0] / inlet_sizes[0]),
                    float(inlet_sizes[0] / upper_sizes[0]),
                ),
                "virtual_inlet_to_lower_wall": max(
                    float(inlet_sizes[-1] / lower_sizes[-1]),
                    float(lower_sizes[-1] / inlet_sizes[-1]),
                ),
        }
        local_growth = max(
                upper_ratio,
                1.0 / upper_ratio,
                lower_ratio,
                1.0 / lower_ratio,
                _distribution_metrics(inlet_sizes)["maximum_growth_ratio"],
        )
        mismatch = max(interface_ratios.values())
        objective = local_growth + 8.0 * (mismatch - 1.0)
        report = {
                "divisions": total,
                "junction_size_m": float(junction_size),
                "lip_target_size_m": lip_target,
                "sections": {
                    "upper_wall_extension": {
                        "divisions": upper_count,
                        "length_m": lengths["upper_wall_extension"],
                        "progression_coefficient": upper_ratio,
                        "lip_size_m": float(upper_sizes[0]),
                        "junction_size_m": float(upper_sizes[-1]),
                        "minimum_size_m": float(np.min(upper_sizes)),
                        "maximum_size_m": float(np.max(upper_sizes)),
                    },
                    "virtual_inlet": {
                        "divisions": inlet_count,
                        "length_m": lengths["virtual_inlet"],
                        "bump_coefficient": inlet_bump,
                        "upper_lip_size_m": float(inlet_sizes[0]),
                        "lower_lip_size_m": float(inlet_sizes[-1]),
                        "minimum_size_m": float(np.min(inlet_sizes)),
                        "maximum_size_m": float(np.max(inlet_sizes)),
                    },
                    "lower_wall_extension": {
                        "divisions": lower_count,
                        "length_m": lengths["lower_wall_extension"],
                        "progression_coefficient": lower_ratio,
                        "junction_size_m": float(lower_sizes[0]),
                        "lip_size_m": float(lower_sizes[-1]),
                        "minimum_size_m": float(np.min(lower_sizes)),
                        "maximum_size_m": float(np.max(lower_sizes)),
                    },
                },
                "interface_size_ratios": interface_ratios,
                "maximum_local_growth_ratio": local_growth,
        }
        if best is None or objective < best[0]:
            best = (objective, report)
    if best is None:
        raise ValueError("No feasible extended inlet transfinite distribution was found")
    return best[1]


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
