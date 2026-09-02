#!/usr/bin/env python3
"""Shared Gmsh Bump + split-Progression tangential matching."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ramair_2d_bump_matching import bump_cell_sizes


SIDE_NAMES = ("upper", "lower")
END_NAMES = ("leading_or_inlet", "te")


def split_polyline_at_x(
    points: np.ndarray, target_x: float, *, minimum_points_per_half: int = 3,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int]]:
    """Split an ordered polyline at the existing point closest to ``target_x``."""
    values = np.asarray(points, dtype=float)
    if values.ndim != 2 or values.shape[1] < 2:
        raise ValueError("Polyline must be an Nx2 array")
    low = int(minimum_points_per_half) - 1
    high = len(values) - int(minimum_points_per_half)
    if high < low:
        raise ValueError("Polyline is too short for a stable midpoint split")
    index = min(high, max(low, int(np.argmin(np.abs(values[:, 0] - float(target_x))))))
    return values[: index + 1], values[index:], {
        "index": index,
        "requested_x_m": float(target_x),
        "actual_x_m": float(values[index, 0]),
        "actual_z_m": float(values[index, 1]),
    }


def progression_cell_sizes(first_size: float, ratio: float, divisions: int) -> np.ndarray:
    if first_size <= 0.0 or ratio <= 0.0 or divisions < 1:
        raise ValueError("Progression requires h0>0, r>0 and at least one division")
    return float(first_size) * np.power(float(ratio), np.arange(int(divisions), dtype=float))


def progression_length(first_size: float, ratio: float, divisions: int) -> float:
    if math.isclose(ratio, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        return float(first_size) * int(divisions)
    return float(first_size) * (float(ratio) ** int(divisions) - 1.0) / (float(ratio) - 1.0)


def solve_progression_ratio(length: float, divisions: int, first_size: float) -> float:
    """Solve Gmsh's geometric law using Newton iterations inside a safe bracket."""
    length = float(length)
    first_size = float(first_size)
    divisions = int(divisions)
    if length <= 0.0 or first_size <= 0.0 or divisions < 1:
        raise ValueError("Progression inputs must be positive")
    uniform_length = first_size * divisions
    if length < uniform_length * (1.0 - 1.0e-10):
        raise ValueError(
            f"No fine-to-mid Progression exists: N*h0={uniform_length:.8g} m exceeds L={length:.8g} m"
        )
    if math.isclose(length, uniform_length, rel_tol=1.0e-10, abs_tol=1.0e-14):
        return 1.0
    low, high = 1.0, 1.05
    while progression_length(first_size, high, divisions) < length and high < 100.0:
        high = 1.0 + 2.0 * (high - 1.0)
    if progression_length(first_size, high, divisions) < length:
        raise ValueError("Progression ratio exceeds the supported numerical bracket")
    ratio = min(high, max(low, (length / uniform_length) ** (2.0 / max(divisions - 1, 1))))
    for _ in range(60):
        value = progression_length(first_size, ratio, divisions) - length
        if abs(value) <= max(1.0e-13, 1.0e-11 * length):
            return ratio
        if value > 0.0:
            high = ratio
        else:
            low = ratio
        step = max(1.0e-7, 1.0e-6 * ratio)
        derivative = (
            progression_length(first_size, ratio + step, divisions)
            - progression_length(first_size, max(1.0, ratio - step), divisions)
        ) / (ratio + step - max(1.0, ratio - step))
        candidate = ratio - value / derivative if derivative > 0.0 else 0.5 * (low + high)
        ratio = candidate if low < candidate < high else 0.5 * (low + high)
    return 0.5 * (low + high)


def _half_metrics(length: float, divisions: int, first_size: float, ratio: float) -> dict[str, float | int]:
    sizes = progression_cell_sizes(first_size, ratio, divisions)
    return {
        "length_m": float(length),
        "divisions": int(divisions),
        "coefficient": float(ratio),
        "first_size_m": float(sizes[0]),
        "midpoint_size_m": float(sizes[-1]),
        "maximum_size_m": float(np.max(sizes)),
        "maximum_growth_ratio": float(max(ratio, 1.0 / ratio)),
        "length_reconstructed_m": float(np.sum(sizes)),
    }


def _optimize_side(
    lengths: dict[str, float], total_divisions: int,
    endpoint_sizes: dict[str, float], maximum_growth_ratio: float,
    maximum_size_m: float,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for leading_count in range(2, int(total_divisions) - 1):
        te_count = int(total_divisions) - leading_count
        try:
            leading_ratio = solve_progression_ratio(
                lengths["leading_or_inlet"], leading_count,
                endpoint_sizes["leading_or_inlet"],
            )
            te_ratio = solve_progression_ratio(
                lengths["te"], te_count, endpoint_sizes["te"],
            )
        except ValueError:
            continue
        leading = _half_metrics(
            lengths["leading_or_inlet"], leading_count,
            endpoint_sizes["leading_or_inlet"], leading_ratio,
        )
        te = _half_metrics(lengths["te"], te_count, endpoint_sizes["te"], te_ratio)
        mismatch = abs(math.log(float(leading["midpoint_size_m"]) / float(te["midpoint_size_m"])))
        gr = max(float(leading["maximum_growth_ratio"]), float(te["maximum_growth_ratio"]))
        hmax = max(float(leading["maximum_size_m"]), float(te["maximum_size_m"]))
        valid = gr <= maximum_growth_ratio and hmax <= maximum_size_m
        candidates.append({
            "leading_or_inlet": leading,
            "te": te,
            "midpoint_mismatch_log": mismatch,
            "midpoint_mismatch_percent": 100.0 * abs(
                float(leading["midpoint_size_m"]) - float(te["midpoint_size_m"])
            ) / max(0.5 * (float(leading["midpoint_size_m"]) + float(te["midpoint_size_m"])), 1.0e-15),
            "maximum_growth_ratio": gr,
            "maximum_size_m": hmax,
            "valid": valid,
        })
    if not candidates:
        raise ValueError("No integer division split can satisfy the two half lengths")
    valid = [item for item in candidates if item["valid"]]
    pool = valid or candidates
    selected = min(
        pool,
        key=lambda item: (
            float(item["midpoint_mismatch_log"]),
            max(0.0, float(item["maximum_growth_ratio"]) - maximum_growth_ratio),
            max(0.0, float(item["maximum_size_m"]) - maximum_size_m),
        ),
    )
    selected["feasible_candidate_count"] = len(valid)
    selected["candidate_count"] = len(candidates)
    length_total = float(lengths["leading_or_inlet"] + lengths["te"])
    minimum_from_hmax = int(math.ceil(length_total / maximum_size_m))
    minimum_from_growth = 0
    for end in END_NAMES:
        if maximum_growth_ratio <= 1.0:
            count = int(math.ceil(float(lengths[end]) / float(endpoint_sizes[end])))
        else:
            argument = 1.0 + (
                (maximum_growth_ratio - 1.0) * float(lengths[end])
                / float(endpoint_sizes[end])
            )
            count = int(math.ceil(math.log(argument) / math.log(maximum_growth_ratio)))
        minimum_from_growth += max(2, count)
    selected["estimated_minimum_total_divisions"] = max(
        minimum_from_hmax, minimum_from_growth
    )
    return selected


def automatic_split_progression(
    *,
    half_lengths: dict[str, dict[str, float]],
    body_divisions: dict[str, int],
    curved_lengths: dict[str, float],
    curved_divisions: dict[str, int],
    curved_bumps: dict[str, float],
    chord: float,
    maximum_growth_ratio: float,
    maximum_size_percent_chord: float,
) -> dict[str, Any]:
    endpoint_sizes = {
        end: float(bump_cell_sizes(
            float(curved_bumps[end]), float(curved_lengths[end]), int(curved_divisions[end])
        )[0])
        for end in END_NAMES
    }
    hmax = float(maximum_size_percent_chord) * float(chord) / 100.0
    sides = {
        side: _optimize_side(
            half_lengths[side], int(body_divisions[side]), endpoint_sizes,
            float(maximum_growth_ratio), hmax,
        )
        for side in SIDE_NAMES
    }
    actual_gr = max(float(item["maximum_growth_ratio"]) for item in sides.values())
    actual_hmax = max(float(item["maximum_size_m"]) for item in sides.values())
    warnings: list[str] = []
    for side, item in sides.items():
        if not item["valid"]:
            warnings.append(
                f"{side}: no integer split satisfies GRmax/hmax with {body_divisions[side]} divisions; "
                f"use approximately >= {item['estimated_minimum_total_divisions']} body divisions "
                "or relax the active limit."
            )
    coefficients = {
        f"{side}_{end}": float(sides[side][end]["coefficient"])
        for side in SIDE_NAMES for end in END_NAMES
    }
    split_divisions = {
        f"{side}_{end}": int(sides[side][end]["divisions"])
        for side in SIDE_NAMES for end in END_NAMES
    }
    return {
        "status": "WARNING" if warnings else "PASS",
        "method": "bump_split_progression",
        "curved_bump_coefficients": {name: float(curved_bumps[name]) for name in END_NAMES},
        "endpoint_sizes_m": endpoint_sizes,
        "split_divisions": split_divisions,
        "progression_coefficients": coefficients,
        "sides": sides,
        "maximum_growth_ratio": actual_gr,
        "maximum_size_m": actual_hmax,
        "maximum_size_percent_chord": 100.0 * actual_hmax / float(chord),
        "maximum_midpoint_mismatch_percent": max(
            float(item["midpoint_mismatch_percent"]) for item in sides.values()
        ),
        "warnings": warnings,
        "blocks_generation": bool(warnings),
    }


def evaluate_manual_split_progression(
    *,
    half_lengths: dict[str, dict[str, float]],
    split_divisions: dict[str, int],
    progression_coefficients: dict[str, float],
    endpoint_sizes: dict[str, float],
    chord: float,
    maximum_growth_ratio: float,
    maximum_size_percent_chord: float,
) -> dict[str, Any]:
    sides: dict[str, Any] = {}
    warnings: list[str] = []
    hmax_limit = float(maximum_size_percent_chord) * float(chord) / 100.0
    for side in SIDE_NAMES:
        side_data: dict[str, Any] = {}
        for end in END_NAMES:
            key = f"{side}_{end}"
            divisions = int(split_divisions[key])
            ratio = float(progression_coefficients[key])
            actual_first_size = float(half_lengths[side][end]) / progression_length(
                1.0, ratio, divisions
            )
            side_data[end] = _half_metrics(
                half_lengths[side][end], divisions, actual_first_size, ratio,
            )
            side_data[end]["requested_interface_size_m"] = float(endpoint_sizes[end])
            side_data[end]["interface_mismatch_percent"] = 100.0 * abs(
                actual_first_size - float(endpoint_sizes[end])
            ) / max(float(endpoint_sizes[end]), 1.0e-15)
            if float(side_data[end]["interface_mismatch_percent"]) > 10.0:
                warnings.append(
                    f"{key}: interface mismatch is "
                    f"{side_data[end]['interface_mismatch_percent']:.3g}%"
                )
        side_data["midpoint_mismatch_percent"] = 100.0 * abs(
            float(side_data["leading_or_inlet"]["midpoint_size_m"])
            - float(side_data["te"]["midpoint_size_m"])
        ) / max(0.5 * (
            float(side_data["leading_or_inlet"]["midpoint_size_m"])
            + float(side_data["te"]["midpoint_size_m"])
        ), 1.0e-15)
        sides[side] = side_data
    actual_gr = max(
        float(sides[side][end]["maximum_growth_ratio"])
        for side in SIDE_NAMES for end in END_NAMES
    )
    actual_hmax = max(
        float(sides[side][end]["maximum_size_m"])
        for side in SIDE_NAMES for end in END_NAMES
    )
    if actual_gr > maximum_growth_ratio:
        warnings.append(f"GR={actual_gr:.5g} exceeds GRmax={maximum_growth_ratio:.5g}")
    if actual_hmax > hmax_limit:
        warnings.append(
            f"hmax={100.0 * actual_hmax / chord:.4g}%c exceeds {maximum_size_percent_chord:.4g}%c"
        )
    mismatch = max(float(item["midpoint_mismatch_percent"]) for item in sides.values())
    if mismatch > 10.0:
        warnings.append(f"Midpoint mismatch reaches {mismatch:.3g}%")
    return {
        "status": "WARNING" if warnings else "PASS",
        "method": "bump_split_progression_manual",
        "split_divisions": {key: int(value) for key, value in split_divisions.items()},
        "progression_coefficients": {
            key: float(value) for key, value in progression_coefficients.items()
        },
        "sides": sides,
        "maximum_growth_ratio": actual_gr,
        "maximum_size_m": actual_hmax,
        "maximum_size_percent_chord": 100.0 * actual_hmax / float(chord),
        "maximum_midpoint_mismatch_percent": mismatch,
        "warnings": warnings,
        "blocks_generation": False,
    }
