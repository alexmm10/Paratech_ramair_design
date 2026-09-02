#!/usr/bin/env python3
"""Independent from-scratch Gmsh mesh experiment for the open LS(1)-0417 airfoil."""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ramair_2d_mesh_builder import (
    _tangent_continuous_te_cap_points,
    extrude_msh2_surface_one_cell,
    run_openfoam_mesh_checks,
)
from ramair_2d_mesh_quality_distributions import generate_quality_distributions
from boundary_layer_estimates import (
    beta_law_coefficient,
    beta_law_cumulative_distances,
    first_cell_height_from_yplus,
    geometric_layers_for_thickness,
    turbulent_flat_plate_delta99,
)
from ramair_2d_mesh_science import first_cell_height_audit
from ramair_2d_bump_matching import bump_cell_sizes, match_four_segment_bumps
from ramair_2d_split_progression import (
    automatic_split_progression,
    evaluate_manual_split_progression,
)
from ramair_2d_gmsh_experimental import (
    add_extend_field,
    add_smooth_interface_guard,
    compare_openfoam_quality,
    gmsh_quality_summary,
    normalize_geometry_to_total_chord,
)


EXPERIMENT_ID = "open_reference_from_scratch"
DEFAULT_NAME = "open_reference_hybrid_experimental_v1"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def default_config() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "name": DEFAULT_NAME,
        "geometry": {
            "open_variant": "open_ramair_validation_1m",
            "base_variant": "reference_uncut_validation_1m",
            "reynolds": 1.9e6,
            "rho_kg_m3": 0.66606662,
            "mu_pa_s": 1.7894e-5,
            "velocity_m_s": 51.04384,
            "chord_m": 1.0,
            "spanwise_thickness_chord": 0.01,
        },
        "boundary_layer": {
            "layers": 75,
            "growth_rate": 1.1,
            "distribution_mode": "beta_law",
            "thickness_safety_factor": 1.20,
            "target_y_plus": 2.0 / 3.0,
            "wall_nodes_target": 1200,
            "minimum_tangential_size_chord": 0.00030,
            "maximum_tangential_size_chord": 0.0050,
            "wall_bump_coefficient": 0.45,
            "te_cap_nodes": 35,
            "inlet_nodes": 140,
            "inlet_bump_coefficient": 1.00,
            "lip_fan_elements": 0,
            "minimum_profile_point_spacing_chord": 1.0e-6,
            "continue_over_base_inlet": True,
            "unstructured_lip_length_chord": 0.01,
            "base_inlet_blend_length_chord": 0.035,
            "base_inlet_tangent_scale": 1.00,
            "leading_edge_curvature_fraction": 0.50,
            "prefer_exact_base_inlet": True,
            "automatic_bump_matching": False,
            "manual_four_segment_bump_enabled": False,
            "tangential_distribution_method": "four_bumps",
            "split_progression_midpoint_x_chord": 0.50,
            "segment_divisions": {
                "te": 24, "upper": 320, "leading_or_inlet": 140, "lower": 320,
            },
            "manual_bump_coefficients": {
                "te": 1.20, "upper": 0.10, "leading_or_inlet": 1.00, "lower": 0.10,
            },
            "manual_split_progression": {
                "split_divisions": {
                    "upper_leading_or_inlet": 160, "upper_te": 160,
                    "lower_leading_or_inlet": 160, "lower_te": 160,
                },
                "progression_coefficients": {
                    "upper_leading_or_inlet": 1.02, "upper_te": 1.02,
                    "lower_leading_or_inlet": 1.02, "lower_te": 1.02,
                },
            },
            "bump_maximum_growth_ratio": 1.10,
            "bump_maximum_size_percent_chord": 1.00,
            "te_segment_early_start_enabled": False,
            "te_segment_start_x_over_c": 0.98,
            "te_geometry_points": 35,
        },
        "external_volume": {
            "domain_radius_chord": 50.0,
            "interface_size_mode": "fixed",
            "interface_size_chord": 0.000035,
            "interface_tangential_factor": 0.40,
            "farfield_size_chord": 5.00,
            "radial_growth_rate": 0.13,
            "mesh_algorithm": 6,
            "automatic_extend_enabled": False,
            "extend_distance_max_chord": 50.0,
            "extend_power": 2.0,
            "extend_size_max_chord": 5.00,
            "extend_interface_guard_enabled": True,
            "extend_interface_transition_chord": 0.10,
        },
        "internal_volume": {
            "inlet_size_factor": 0.45,
            "core_size_chord": 0.010,
            "inlet_fine_distance_chord": 0.001,
            "transition_distance_chord": 0.06,
            "inner_wall_size_chord": 0.0015,
            "inner_wall_transition_distance_chord": 0.05,
            "te_internal_size_chord": 0.0004,
            "te_internal_transition_distance_chord": 0.005,
            "automatic_extend_enabled": False,
            "extend_distance_max_chord": 0.10,
            "extend_power": 2.5,
            "extend_size_max_chord": 0.015,
        },
        "execution": {
            "gmsh_threads": 12,
            "openfoam_timeout_s": 900,
            "post_generation_optimization": "off",
            "post_generation_optimization_iterations": 5,
            "mesh_smoothing": 1,
            "analyse_mesh_quality": True,
        },
    }


def _clean_polyline(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Remove coincident points while preserving the original polyline order.

    Consecutive duplicates are the usual input error, but a processed profile
    can also repeat a point after a branch/cap join.  Keeping a quantized
    coordinate set makes that case explicit without an O(n**2) distance scan.
    The tolerance is deliberately the same physical tolerance used by the
    geometry loader, so this cannot silently collapse distinct CFD features.
    """
    if tolerance <= 0.0 or not math.isfinite(float(tolerance)):
        raise ValueError("Polyline tolerance must be finite and positive")
    clean: list[np.ndarray] = []
    seen: set[tuple[int, ...]] = set()
    for point in np.asarray(points, dtype=float):
        point = np.asarray(point, dtype=float)
        if point.ndim != 1 or not np.all(np.isfinite(point)):
            raise ValueError("A profile polyline contains a non-finite point")
        key = tuple(np.rint(point / tolerance).astype(np.int64).tolist())
        if key in seen:
            continue
        seen.add(key)
        clean.append(point)
    if len(clean) < 2:
        raise ValueError("A profile branch collapsed after duplicate-point removal")
    return np.asarray(clean)


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1.0e-15:
        raise ValueError("A zero-length tangent was found while joining the base inlet")
    return np.asarray(vector, dtype=float) / norm


def _endpoint_tangent(points: np.ndarray, *, at_start: bool) -> np.ndarray:
    """Estimate an endpoint tangent without trusting one exceptionally short edge."""
    values = np.asarray(points, dtype=float)
    if len(values) < 3:
        tangent = values[1] - values[0] if at_start else values[-1] - values[-2]
        return _unit(tangent)
    count = min(6, len(values))
    local = values[:count] if at_start else values[-count:]
    arc = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(local, axis=0), axis=1))))
    degree = min(3, len(local) - 1)
    evaluation = 0.0 if at_start else float(arc[-1])
    derivatives = []
    for coordinate in range(2):
        polynomial = np.polyfit(arc, local[:, coordinate], degree)
        derivatives.append(float(np.polyval(np.polyder(polynomial), evaluation)))
    return _unit(np.asarray(derivatives, dtype=float))


def _project_to_polyline(
    point: np.ndarray,
    polyline: np.ndarray,
) -> tuple[np.ndarray, int, float, float]:
    """Project *point* on the closest finite segment of *polyline*.

    Selecting the closest sampled vertex made the virtual inlet depend on the
    preprocessing density.  The projection on a segment is invariant to that
    sampling choice and provides an exact start/end location on the uncut
    profile used as geometric reference.
    """
    curve = np.asarray(polyline, dtype=float)
    if len(curve) < 2:
        raise ValueError("At least two base-profile points are required")
    segments = np.diff(curve, axis=0)
    squared = np.sum(segments * segments, axis=1)
    if np.any(squared <= 1.0e-30):
        raise ValueError("The base profile contains a zero-length segment")
    relative = np.asarray(point, dtype=float) - curve[:-1]
    parameters = np.clip(np.sum(relative * segments, axis=1) / squared, 0.0, 1.0)
    projections = curve[:-1] + parameters[:, None] * segments
    distances = np.linalg.norm(projections - point, axis=1)
    index = int(np.argmin(distances))
    return (
        projections[index],
        index,
        float(parameters[index]),
        float(distances[index]),
    )


def _polyline_projection_distances(
    points: np.ndarray,
    polyline: np.ndarray,
) -> np.ndarray:
    """Return the finite-segment distance of every point to a polyline."""
    return np.asarray([
        _project_to_polyline(point, polyline)[3]
        for point in np.asarray(points, dtype=float)
    ])


def _recover_open_profile_base_coordinates(
    open_upper: np.ndarray,
    open_lower: np.ndarray,
    base_upper: np.ndarray,
    base_lower: np.ndarray,
    chord: float,
    tolerance: float,
) -> tuple[float, dict[str, Any]]:
    """Recover the full-chord coordinates removed by cut-profile normalization.

    The cut profile is generated from the same LS(1)-0417 point set as the
    uncut profile, but the generic preprocessor normalizes its retained chord
    from the upper lip to the TE.  That affine normalization moves every
    retained wall point and makes an otherwise correct uncut inlet guide look
    incompatible.  Fit the single inverse scale about the TE and then require
    both retained branches to coincide with the uncut source contour.
    """
    if chord <= 0.0:
        raise ValueError("A positive chord is required for open/base alignment")

    def transform(points: np.ndarray, scale: float) -> np.ndarray:
        values = np.asarray(points, dtype=float).copy()
        values[:, 0] = chord + scale * (values[:, 0] - chord)
        values[:, 1] *= scale
        return values

    def objective(scale: float) -> float:
        upper_distance = _polyline_projection_distances(
            transform(open_upper, scale), base_upper,
        )
        lower_distance = _polyline_projection_distances(
            transform(open_lower, scale), base_lower,
        )
        distances = np.concatenate([upper_distance, lower_distance])
        return float(np.mean(distances * distances))

    # The coarse scan makes the one-dimensional fit robust to the piecewise
    # projection objective.  Golden-section refinement then avoids a SciPy
    # dependency in the portable mesher.
    candidates = np.linspace(0.75, 1.05, 301)
    scores = np.asarray([objective(float(value)) for value in candidates])
    best = int(np.argmin(scores))
    left = float(candidates[max(0, best - 1)])
    right = float(candidates[min(len(candidates) - 1, best + 1)])
    golden = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = right - golden * (right - left)
    x2 = left + golden * (right - left)
    f1 = objective(x1)
    f2 = objective(x2)
    for _ in range(64):
        if f1 <= f2:
            right, x2, f2 = x2, x1, f1
            x1 = right - golden * (right - left)
            f1 = objective(x1)
        else:
            left, x1, f1 = x1, x2, f2
            x2 = left + golden * (right - left)
            f2 = objective(x2)
    scale = 0.5 * (left + right)
    aligned_upper = transform(open_upper, scale)
    aligned_lower = transform(open_lower, scale)
    upper_distance = _polyline_projection_distances(aligned_upper, base_upper)
    lower_distance = _polyline_projection_distances(aligned_lower, base_lower)
    distances = np.concatenate([upper_distance, lower_distance])
    identity_tolerance = max(100.0 * tolerance, 2.0e-7 * chord)
    report = {
        "method": "inverse_retained_chord_normalization_about_TE",
        "scale_to_full_base_chord": float(scale),
        "cut_origin_offset_chord": float(1.0 - scale),
        "upper_max_distance_m": float(np.max(upper_distance)),
        "lower_max_distance_m": float(np.max(lower_distance)),
        "rms_distance_m": float(np.sqrt(np.mean(distances * distances))),
        "identity_tolerance_m": float(identity_tolerance),
        "retained_open_contour_matches_uncut_base": bool(
            np.max(distances) <= identity_tolerance
        ),
    }
    if not report["retained_open_contour_matches_uncut_base"]:
        raise ValueError(
            "The selected open and uncut profiles are not the same source contour: "
            f"maximum retained-wall mismatch={float(np.max(distances)):.6g} m, "
            f"allowed={identity_tolerance:.6g} m. Select the closed profile used to "
            "produce the cut geometry before generating the experimental mesh."
        )
    return float(scale), report


def _hermite_bridge(
    start: np.ndarray,
    end: np.ndarray,
    start_tangent: np.ndarray,
    end_tangent: np.ndarray,
    count: int,
    tangent_scale: float = 1.0,
) -> np.ndarray:
    """Return a bounded C1 bridge between two independently sampled curves.

    The previous implementation replaced the local tangent magnitude with the
    complete bridge length.  That is numerically unsafe when a cut lip is only
    a few millimetres from the uncut guide: the cubic then overshoots and can
    turn back near the leading edge.  Keep the measured local tangent vectors,
    apply the user scale, and limit each Hermite handle to 75% of the bridge
    chord.  The direction remains C1-continuous at both ends while the handle
    cannot create an artificial loop.
    """
    distance = max(float(np.linalg.norm(end - start)), 1.0e-12)
    user_scale = max(0.05, min(1.5, float(tangent_scale)))

    def bounded_handle(vector: np.ndarray) -> np.ndarray:
        handle = np.asarray(vector, dtype=float) * user_scale
        magnitude = float(np.linalg.norm(handle))
        if magnitude <= 1.0e-15:
            raise ValueError("A zero-length Hermite handle was found while joining the base inlet")
        return handle * min(1.0, 0.75 * distance / magnitude)

    m0 = bounded_handle(start_tangent)
    m1 = bounded_handle(end_tangent)
    sample_count = max(8, int(count))
    parameters = np.linspace(0.0, 1.0, sample_count)
    # A uniform sample can make the first polyline edge much longer than the
    # local tangent handle.  Keep a small, finite first/last interval so the
    # discretized spline enters and leaves each lip with the intended tangent
    # without producing zero-length points.
    if sample_count >= 8:
        parameters[1] = 0.01
        parameters[2] = 0.06
        parameters[-3] = 0.94
        parameters[-2] = 0.99
        parameters = np.maximum.accumulate(parameters)
    t = parameters[:, None]
    return (
        (2.0 * t**3 - 3.0 * t**2 + 1.0) * start
        + (t**3 - 2.0 * t**2 + t) * m0
        + (-2.0 * t**3 + 3.0 * t**2) * end
        + (t**3 - t**2) * m1
    )


def _canonical_base_inlet(
    base_path: np.ndarray,
    wall: np.ndarray,
    chord: float,
    blend_length_chord: float,
    tolerance: float,
    tangent_scale: float,
    prefer_exact_base_le: bool = True,
    leading_edge_curvature_fraction: float = 0.50,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Join both lips to an *unchanged* uncut-profile inlet guide.

    Only the two local connectors are interpolated.  The complete base-profile
    segment remains byte-for-byte present in the returned polyline and is later
    built as an independent Gmsh spline.  This prevents the global spline fit
    from bending the leading-edge guide when tangential resolution increases.
    """
    base = _clean_polyline(base_path, tolerance)
    base_segments = np.linalg.norm(np.diff(base, axis=0), axis=1)
    base_spacing = float(np.median(base_segments))
    endpoint_tolerance = max(100.0 * tolerance, 2.0e-7 * chord)
    upper_endpoint_distance = float(np.linalg.norm(base[0] - wall[0]))
    lower_endpoint_distance = float(np.linalg.norm(base[-1] - wall[-1]))
    upper_direct_angle = math.degrees(math.acos(float(np.clip(
        np.dot(_endpoint_tangent(base, at_start=True), -_endpoint_tangent(wall, at_start=True)),
        -1.0, 1.0,
    ))))
    lower_direct_angle = math.degrees(math.acos(float(np.clip(
        np.dot(_endpoint_tangent(base, at_start=False), -_endpoint_tangent(wall, at_start=False)),
        -1.0, 1.0,
    ))))
    if (
        upper_endpoint_distance <= endpoint_tolerance
        and lower_endpoint_distance <= endpoint_tolerance
        and upper_direct_angle <= 1.0
        and lower_direct_angle <= 1.0
    ):
        directions = np.diff(base, axis=0) / base_segments[:, None]
        turning = np.degrees(np.arctan2(
            directions[1:, 0] * directions[:-1, 1]
            - directions[1:, 1] * directions[:-1, 0],
            np.sum(directions[1:] * directions[:-1], axis=1),
        ))
        exact_base = base.copy()
        exact_base[0] = wall[0]
        exact_base[-1] = wall[-1]
        return exact_base, {
            "method": "exact_shared_source_inlet_without_artificial_connectors",
            "blend_length_chord": 0.0,
            "tangent_scale": None,
            "upper_join_tangent_error_deg": upper_direct_angle,
            "lower_join_tangent_error_deg": lower_direct_angle,
            "base_start_index": 0,
            "base_end_index": len(base) - 1,
            "upper_anchor_index": 0,
            "lower_anchor_index": len(base) - 1,
            "base_le_index": int(np.argmin(base[:, 0])),
            "upper_effective_blend_length_m": 0.0,
            "lower_effective_blend_length_m": 0.0,
            "upper_connector_points": 0,
            "lower_connector_points": 0,
            "upper_base_join_tangent_error_deg": 0.0,
            "lower_base_join_tangent_error_deg": 0.0,
            "upper_endpoint_distance_m": upper_endpoint_distance,
            "lower_endpoint_distance_m": lower_endpoint_distance,
            "inlet_segment_min_m": float(np.min(base_segments)),
            "inlet_segment_max_m": float(np.max(base_segments)),
            "inlet_max_turn_deg": float(np.max(np.abs(turning))) if len(turning) else 0.0,
            "inlet_turn_sign_changes": int(
                np.sum(np.sign(turning[1:]) != np.sign(turning[:-1]))
            ) if len(turning) > 1 else 0,
            "base_guide_exact_between_connectors": True,
            "exact_base_le_admissible": True,
        }
    cumulative = np.concatenate(([0.0], np.cumsum(base_segments)))
    blend_length = max(2.0 * base_spacing, float(blend_length_chord) * chord)
    le_index = int(np.argmin(base[:, 0]))
    # Never let either connector consume the nominal leading edge.  Doing so
    # replaced one side of the exact base contour with a long cubic bridge;
    # at high transfinite resolution the interpolating spline then developed
    # the artificial central-LE curvature reported by the user.  Each bridge
    # is now confined to at most half the available lip-to-LE arc length, so
    # the retained base segment always contains samples on both sides of LE.
    upper_available = float(cumulative[le_index])
    lower_available = float(cumulative[-1] - cumulative[le_index])
    if prefer_exact_base_le:
        curvature_fraction = min(0.90, max(0.05, float(leading_edge_curvature_fraction)))
        upper_blend = min(blend_length, max(2.0 * base_spacing, curvature_fraction * upper_available))
        lower_blend = min(blend_length, max(2.0 * base_spacing, curvature_fraction * lower_available))
    else:
        # Geometry-safe convex fallback.  With the present cut, the upper lip
        # lies ahead of the nominal uncut nose while its tangent points still
        # farther upstream.  A C1 connector to the upper base branch must then
        # reverse curvature.  Spanning the nose is the shortest bounded C1
        # closure that avoids that inflection and BL self-intersection.
        upper_blend = float(cumulative[min(le_index + 2, len(base) - 4)])
        lower_blend = blend_length
    upper_anchor = int(np.searchsorted(cumulative, upper_blend, side="left"))
    lower_anchor = int(np.searchsorted(
        cumulative, cumulative[-1] - lower_blend, side="right"
    ) - 1)
    upper_anchor = max(1, min(upper_anchor, len(base) - 4))
    lower_anchor = min(len(base) - 2, max(lower_anchor, upper_anchor + 2))
    retained_base = base[upper_anchor : lower_anchor + 1]
    upper_distance = float(np.linalg.norm(retained_base[0] - wall[0]))
    lower_distance = float(np.linalg.norm(wall[-1] - retained_base[-1]))
    upper_count = max(8, int(math.ceil(upper_distance / max(base_spacing, tolerance))) + 1)
    lower_count = max(8, int(math.ceil(lower_distance / max(base_spacing, tolerance))) + 1)
    upper_bridge = _hermite_bridge(
        wall[0], retained_base[0], wall[0] - wall[1],
        retained_base[1] - retained_base[0], upper_count,
        tangent_scale,
    )
    lower_bridge = _hermite_bridge(
        retained_base[-1], wall[-1],
        retained_base[-1] - retained_base[-2], wall[-2] - wall[-1], lower_count,
        tangent_scale,
    )
    base_start_index = len(upper_bridge) - 1
    base_end_index = base_start_index + len(retained_base) - 1
    inlet = _clean_polyline(
        np.vstack([upper_bridge[:-1], retained_base, lower_bridge[1:]]),
        tolerance,
    )
    inlet[0] = wall[0]
    inlet[-1] = wall[-1]
    segment_lengths = np.linalg.norm(np.diff(inlet, axis=0), axis=1)
    if not np.all(np.isfinite(segment_lengths)) or np.any(segment_lengths <= tolerance):
        raise ValueError("The inlet guide contains a zero or non-finite segment after the C1 join")
    directions = np.diff(inlet, axis=0) / segment_lengths[:, None]
    turning = np.degrees(np.arctan2(
        directions[1:, 0] * directions[:-1, 1]
        - directions[1:, 1] * directions[:-1, 0],
        np.sum(directions[1:] * directions[:-1], axis=1),
    ))
    upper_angle = math.degrees(math.acos(float(np.clip(
        np.dot(_unit(inlet[1] - inlet[0]), _unit(wall[0] - wall[1])), -1.0, 1.0
    ))))
    lower_angle = math.degrees(math.acos(float(np.clip(
        np.dot(_unit(inlet[-1] - inlet[-2]), _unit(wall[-2] - wall[-1])), -1.0, 1.0
    ))))
    upper_base_angle = math.degrees(math.acos(float(np.clip(
        np.dot(
            _unit(inlet[base_start_index] - inlet[base_start_index - 1]),
            _unit(inlet[base_start_index + 1] - inlet[base_start_index]),
        ), -1.0, 1.0,
    ))))
    lower_base_angle = math.degrees(math.acos(float(np.clip(
        np.dot(
            _unit(inlet[base_end_index] - inlet[base_end_index - 1]),
            _unit(inlet[base_end_index + 1] - inlet[base_end_index]),
        ), -1.0, 1.0,
    ))))
    report = {
        "method": (
            "exact_uncut_le_segment_with_bounded_c1_lip_connectors"
            if prefer_exact_base_le else
            "convex_bounded_c1_closure_over_incompatible_upper_lip"
        ),
        "blend_length_chord": float(blend_length_chord),
        "tangent_scale": float(tangent_scale),
        "upper_join_tangent_error_deg": upper_angle,
        "lower_join_tangent_error_deg": lower_angle,
        "base_start_index": base_start_index,
        "base_end_index": base_end_index,
        "upper_anchor_index": upper_anchor,
        "lower_anchor_index": lower_anchor,
        "base_le_index": le_index,
        "upper_effective_blend_length_m": upper_blend,
        "lower_effective_blend_length_m": lower_blend,
        "upper_connector_points": len(upper_bridge),
        "lower_connector_points": len(lower_bridge),
        "upper_base_join_tangent_error_deg": upper_base_angle,
        "lower_base_join_tangent_error_deg": lower_base_angle,
        "inlet_segment_min_m": float(np.min(segment_lengths)),
        "inlet_segment_max_m": float(np.max(segment_lengths)),
        "inlet_max_turn_deg": float(np.max(np.abs(turning))) if len(turning) else 0.0,
        "inlet_turn_sign_changes": int(np.sum(np.sign(turning[1:]) != np.sign(turning[:-1]))) if len(turning) > 1 else 0,
        "base_guide_exact_between_connectors": True,
    }
    inlet_le_index = int(np.argmin(inlet[:, 0]))
    exact_is_admissible = bool(
        base_start_index <= inlet_le_index <= base_end_index
        and report["inlet_turn_sign_changes"] == 0
    )
    report["exact_base_le_admissible"] = exact_is_admissible
    if prefer_exact_base_le and not exact_is_admissible:
        fallback, fallback_report = _canonical_base_inlet(
            base_path,
            wall,
            chord,
            blend_length_chord,
            tolerance,
            tangent_scale,
            prefer_exact_base_le=False,
            leading_edge_curvature_fraction=leading_edge_curvature_fraction,
        )
        fallback_report.update({
            "requested_method": "exact_uncut_base_le",
            "fallback_reason": (
                "Exact lip-to-base C1 closure introduced an inflection or placed the "
                "minimum-x point inside a connector; the convex closure was selected."
            ),
            "rejected_exact_diagnostics": report,
        })
        return fallback, fallback_report
    return inlet, report


def load_geometry(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    geometry = config["geometry"]
    geometry_root = root / "CFD_2D/CFD_2D_inputs/geometry"
    open_path = geometry_root / geometry["open_variant"] / "profile_points.csv"
    base_path = geometry_root / geometry["base_variant"] / "profile_points.csv"
    if not open_path.is_file() or not base_path.is_file():
        raise FileNotFoundError(
            "The experimental mesh requires both open and uncut profile_points.csv files: "
            f"{open_path}; {base_path}"
        )
    open_frame = pd.read_csv(open_path)
    base_frame = pd.read_csv(base_path)
    chord = float(geometry.get("chord_m", 1.0))
    tolerance = max(
        1.0e-12,
        chord * float(config["boundary_layer"].get("minimum_profile_point_spacing_chord", 1.0e-6)),
    )

    def branch(frame: pd.DataFrame, section: str) -> np.ndarray:
        selected = frame[frame["source_section"] == section].sort_values("source_order")
        return _clean_polyline(selected[["x_m", "z_m"]].to_numpy(dtype=float), tolerance)

    upper = branch(open_frame, "UPPER")
    lower = branch(open_frame, "LOWER")
    source_cap = branch(open_frame, "TE_ROUNDED_CAP")
    base_upper = branch(base_frame, "UPPER")
    base_lower = branch(base_frame, "LOWER")
    alignment_scale, geometry_identity = _recover_open_profile_base_coordinates(
        upper, lower, base_upper, base_lower, chord, tolerance,
    )

    def align_to_base(points: np.ndarray) -> np.ndarray:
        values = np.asarray(points, dtype=float).copy()
        values[:, 0] = chord + alignment_scale * (values[:, 0] - chord)
        values[:, 1] *= alignment_scale
        return values

    upper = align_to_base(upper)
    lower = align_to_base(lower)
    source_cap = align_to_base(source_cap)
    # The open and closed validation meshes must share the same physical TE
    # closure.  Rebuild it from the verified uncut source endpoints instead of
    # trusting a separately sampled preprocessor cap.
    te_geometry_points = max(7, int(config["boundary_layer"].get("te_geometry_points", 35)))
    cap_internal, cap_info = _tangent_continuous_te_cap_points(
        base_lower[-1], base_upper[0], base_lower[-2], base_upper[1], chord,
        te_geometry_points - 2,
    )
    cap = np.vstack([base_lower[-1], cap_internal, base_upper[0]])
    source_cap_distance = _polyline_projection_distances(source_cap, cap)
    upper_path = upper[::-1]
    lower_path = lower[::-1]
    wall = _clean_polyline(
        np.vstack([upper_path, cap[::-1], lower_path]), tolerance,
    )
    upper_lip = wall[0].copy()
    lower_lip = wall[-1].copy()

    upper_projection, upper_segment, upper_parameter, upper_distance = _project_to_polyline(
        upper_lip, base_upper,
    )
    lower_projection, lower_segment, lower_parameter, lower_distance = _project_to_polyline(
        lower_lip, base_lower,
    )
    raw_inlet = _clean_polyline(
        np.vstack([
            upper_projection,
            base_upper[upper_segment + 1 :],
            base_lower[: lower_segment + 1],
            lower_projection,
        ]),
        tolerance,
    )
    inlet, inlet_join = _canonical_base_inlet(
        raw_inlet, wall, chord,
        float(config["boundary_layer"].get("base_inlet_blend_length_chord", 0.035)),
        tolerance,
        float(config["boundary_layer"].get("base_inlet_tangent_scale", 0.40)),
        bool(config["boundary_layer"].get("prefer_exact_base_inlet", True)),
        float(config["boundary_layer"].get("leading_edge_curvature_fraction", 0.50)),
    )
    normalized, chord_report = normalize_geometry_to_total_chord(
        {
            "upper": upper,
            "lower": lower,
            "cap": cap,
            "wall": wall,
            "inlet": inlet,
            "base_upper": base_upper,
            "base_lower": base_lower,
        },
        chord=chord,
        reference_groups=("base_upper", "base_lower", "cap"),
    )
    upper = normalized["upper"]
    lower = normalized["lower"]
    cap = normalized["cap"]
    wall = normalized["wall"]
    inlet = normalized["inlet"]
    le_index = int(np.argmin(inlet[:, 0]))
    if le_index < 2 or le_index > len(inlet) - 3:
        raise ValueError("The uncut base curve did not provide a resolved leading-edge inlet segment")
    return {
        "open_frame": open_frame,
        "wall": wall,
        "inlet": inlet,
        "le_index": le_index,
        "upper_end_index": len(upper_path) - 1,
        # The shared cap has exactly the same endpoint objects as both wall
        # branches. _clean_polyline removes those two repeated join points, so
        # its inclusive range is [len(upper)-1, len(upper)+len(cap)-2].
        "cap_end_index": len(upper_path) + len(cap) - 2,
        "chord": chord,
        "inlet_join": inlet_join,
        "geometry_identity": {
            **geometry_identity,
            "open_variant": str(geometry["open_variant"]),
            "base_variant": str(geometry["base_variant"]),
            "upper_lip_projection_distance_m": upper_distance,
            "lower_lip_projection_distance_m": lower_distance,
            "total_chord_normalization": chord_report,
            "shared_te_cap": {
                "method": "same_tangent_continuous_cap_as_closed_validation",
                "geometry_points": te_geometry_points,
                "source_cap_max_distance_before_normalization_m": float(
                    np.max(source_cap_distance)
                ),
                "cap_diagnostics": cap_info,
            },
        },
        "base_projection": {
            "upper_segment": upper_segment,
            "upper_parameter": upper_parameter,
            "upper_distance_m": upper_distance,
            "lower_segment": lower_segment,
            "lower_parameter": lower_parameter,
            "lower_distance_m": lower_distance,
        },
    }


def flat_plate_first_height(config: dict[str, Any]) -> dict[str, Any]:
    geometry = config["geometry"]
    boundary = config["boundary_layer"]
    reynolds = float(geometry["reynolds"])
    rho = float(geometry["rho_kg_m3"])
    mu = float(geometry["mu_pa_s"])
    velocity = float(geometry["velocity_m_s"])
    chord = float(geometry["chord_m"])
    target_y_plus = float(boundary["target_y_plus"])
    schlichting = first_cell_height_from_yplus(
        target_y_plus=target_y_plus,
        reynolds=reynolds,
        rho_kg_m3=rho,
        mu_pa_s=mu,
        chord_m=chord,
    )
    y1 = float(schlichting["y1_m"])
    wall_distance = float(schlichting["first_cell_centre_distance_m"])
    cf = float(schlichting["skin_friction_coefficient"])
    friction_velocity = float(schlichting["friction_velocity_m_s"])
    cummings = first_cell_height_audit(
        reynolds, chord, target_y_plus, rho, mu
    )
    delta_turbulent = turbulent_flat_plate_delta99(
        chord_m=chord, reynolds_chord=reynolds, x_over_chord=1.0
    )
    safety = float(boundary.get("thickness_safety_factor", 1.20))
    target_total = delta_turbulent * safety
    distribution_mode = str(boundary.get("distribution_mode", "geometric")).strip().lower()
    if distribution_mode == "beta_law":
        layers = int(boundary["layers"])
        beta = beta_law_coefficient(
            first_cell_height_m=y1,
            total_thickness_m=target_total,
            layers=layers,
        )
        cumulative = beta_law_cumulative_distances(
            first_cell_height_m=y1, beta=beta, layers=layers
        )
        total = cumulative[-1]
        layer_heights = np.diff(np.concatenate(([0.0], np.asarray(cumulative))))
        last = float(layer_heights[-1])
        local_ratios = layer_heights[1:] / np.maximum(layer_heights[:-1], 1.0e-30)
        maximum_local_growth = float(np.max(local_ratios)) if len(local_ratios) else 1.0
        growth = None
        continuous_layers = float(layers)
    elif distribution_mode == "geometric":
        growth = float(boundary["growth_rate"])
        geometric = geometric_layers_for_thickness(
            first_cell_height_m=y1,
            growth_rate=growth,
            minimum_thickness_m=target_total,
        )
        layers = int(geometric["layers"])
        continuous_layers = float(geometric["continuous_layer_count"])
        total = float(geometric["total_thickness_m"])
        last = float(geometric["last_layer_height_m"])
        maximum_local_growth = growth
        beta = None
    else:
        raise ValueError(f"Unsupported boundary-layer distribution: {distribution_mode}")
    reynolds_from_state = rho * velocity * chord / max(mu, 1.0e-30)
    return {
        "skin_friction_coefficient": cf,
        "friction_velocity_m_s": friction_velocity,
        "first_cell_height_m": y1,
        "first_cell_height_chord": y1 / chord,
        "first_cell_centre_distance_m": wall_distance,
        "first_cell_centre_distance_chord": wall_distance / chord,
        "finite_volume_height_multiplier": 2.0,
        "total_thickness_m": total,
        "total_thickness_chord": total / chord,
        "target_total_thickness_m": target_total,
        "target_total_thickness_chord": target_total / chord,
        "theoretical_turbulent_delta99_m": delta_turbulent,
        "theoretical_turbulent_delta99_chord": delta_turbulent / chord,
        "thickness_safety_factor": safety,
        "distribution_mode": distribution_mode,
        "layers": layers,
        "continuous_layer_count": continuous_layers,
        "growth_rate": growth,
        "beta_calculated": beta,
        "maximum_local_normal_growth_ratio": maximum_local_growth,
        "last_layer_height_m": last,
        "last_layer_height_chord": last / chord,
        "reynolds_configured": reynolds,
        "reynolds_from_rho_u_mu": reynolds_from_state,
        "reynolds_relative_mismatch": abs(reynolds_from_state - reynolds) / max(reynolds, 1.0),
        "schlichting_y1_m": y1,
        "cummings_y1_selected_m": float(cummings["selected_first_cell_height_m"]),
        "cummings_y1_candidates_m": cummings["candidates"],
        "yplus_correlation": schlichting["correlation"],
    }


def _nearest_increasing_indices(points: np.ndarray, start: int, end: int, x_values: list[float]) -> list[int]:
    indices = [start]
    previous = start
    for x_value in x_values:
        local = int(np.argmin(np.abs(points[previous : end + 1, 0] - x_value))) + previous
        local = max(previous + 2, min(end - 2, local))
        if local > previous and local < end:
            indices.append(local)
            previous = local
    indices.append(end)
    return sorted(set(indices))


def _wall_segment_boundaries(geometry: dict[str, Any]) -> list[int]:
    wall = geometry["wall"]
    upper_end = int(geometry["upper_end_index"])
    cap_end = int(geometry["cap_end_index"])
    upper = _nearest_increasing_indices(wall, 0, upper_end, [0.05, 0.35, 0.80, 0.95])
    lower = _nearest_increasing_indices(wall, cap_end, len(wall) - 1, [0.95, 0.80, 0.35, 0.10])
    return sorted(set(upper + [cap_end] + lower))


def _surface_target(x_chord: float, on_cap: bool, *, internal: bool, config: dict[str, Any]) -> float:
    if internal:
        internal_cfg = config["internal_volume"]
        middle = float(internal_cfg["inner_wall_mid_size_chord"])
        end = float(internal_cfg["inner_wall_end_size_chord"])
        return min(middle, end + 0.035 * max(0.0, min(x_chord, 1.0 - x_chord)))
    boundary = config["boundary_layer"]
    minimum = float(boundary["minimum_tangential_size_chord"])
    maximum = float(boundary["maximum_tangential_size_chord"])
    if on_cap:
        return minimum
    return min(maximum, minimum + 0.020 * max(0.0, min(x_chord, 1.0 - x_chord)))


def _transfinite_node_bounds(
    length_m: float,
    chord_m: float,
    minimum_size_chord: float,
    maximum_size_chord: float,
) -> tuple[int, int]:
    """Return conservative node bounds from tangential target sizes.

    The explicit TE node control remains authoritative.  These bounds only
    prevent a body segment from being coarser than ``maximum`` or from asking
    for more nodes than the configured ``minimum`` can justify.  Gmsh's Bump
    parameter controls the placement of those nodes, not their count.
    """
    minimum = max(float(minimum_size_chord), 1.0e-12)
    maximum = max(float(maximum_size_chord), minimum)
    lower = max(3, int(math.ceil(length_m / (chord_m * maximum))) + 1)
    upper = max(lower, int(math.floor(length_m / (chord_m * minimum))) + 1)
    return lower, upper


def _add_curve_segments(
    gmsh: Any,
    point_tags: list[int],
    coordinates: np.ndarray,
    boundaries: list[int],
    *,
    internal: bool,
    cap_range: tuple[int, int],
    config: dict[str, Any],
) -> tuple[list[int], list[dict[str, Any]]]:
    curves: list[int] = []
    reports: list[dict[str, Any]] = []
    chord = float(config["geometry"]["chord_m"])
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end - start < 1:
            continue
        curve = gmsh.model.geo.addSpline(point_tags[start : end + 1])
        length = float(np.sum(np.linalg.norm(np.diff(coordinates[start : end + 1], axis=0), axis=1)))
        on_cap = start >= cap_range[0] and end <= cap_range[1]
        start_size = chord * _surface_target(coordinates[start, 0] / chord, on_cap, internal=internal, config=config)
        end_size = chord * _surface_target(coordinates[end, 0] / chord, on_cap, internal=internal, config=config)
        elements = max(3, int(math.ceil(2.0 * length / max(start_size + end_size, 1.0e-15))))
        progression = (end_size / start_size) ** (1.0 / max(elements - 1, 1))
        gmsh.model.geo.mesh.setTransfiniteCurve(curve, elements + 1, "Progression", progression)
        curves.append(curve)
        reports.append({
            "curve": curve, "start_index": start, "end_index": end, "length_m": length,
            "elements": elements, "start_size_m": start_size, "end_size_m": end_size,
            "progression_per_element": progression, "is_te_cap": on_cap,
        })
    return curves, reports


def split_shared_wall_baffle_msh2(mesh_path: Path) -> dict[str, Any]:
    """Duplicate only internal-side wall nodes while preserving the inlet interface."""
    lines = mesh_path.read_text(encoding="utf-8").splitlines()
    p0 = lines.index("$PhysicalNames")
    p1 = lines.index("$EndPhysicalNames", p0)
    physical_rows = lines[p0 + 2 : p1]
    names: dict[tuple[int, str], int] = {}
    kept_physical: list[str] = []
    for row in physical_rows:
        match = re.match(r'\s*(\d+)\s+(\d+)\s+"(.*)"\s*$', row)
        if not match:
            continue
        dimension, tag, name = int(match.group(1)), int(match.group(2)), match.group(3)
        names[(dimension, name)] = tag
        if name != "inlet_interface_temporary":
            kept_physical.append(row)
    required = [(1, "airfoil_wall"), (1, "inlet_interface_temporary"), (2, "fluid_internal")]
    missing = [item for item in required if item not in names]
    if missing:
        raise RuntimeError(f"Cannot split zero-thickness wall; missing physical groups: {missing}")

    n0 = lines.index("$Nodes")
    n1 = lines.index("$EndNodes", n0)
    node_rows = lines[n0 + 2 : n1]
    nodes: dict[int, tuple[float, float, float]] = {}
    for row in node_rows:
        parts = row.split()
        nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
    e0 = lines.index("$Elements")
    e1 = lines.index("$EndElements", e0)
    element_rows = lines[e0 + 2 : e1]
    wall_nodes: set[int] = set()
    inlet_nodes: set[int] = set()
    parsed: list[tuple[int, int, list[int], list[int]]] = []
    max_element = 0
    for row in element_rows:
        parts = row.split()
        element_id, element_type, number_tags = int(parts[0]), int(parts[1]), int(parts[2])
        tags = [int(value) for value in parts[3 : 3 + number_tags]]
        conn = [int(value) for value in parts[3 + number_tags :]]
        physical = tags[0] if tags else 0
        if element_type == 1 and physical == names[(1, "airfoil_wall")]:
            wall_nodes.update(conn)
        if element_type == 1 and physical == names[(1, "inlet_interface_temporary")]:
            inlet_nodes.update(conn)
        parsed.append((element_id, element_type, tags, conn))
        max_element = max(max_element, element_id)
    split_nodes = sorted(wall_nodes.difference(inlet_nodes))
    next_node = max(nodes) + 1
    duplicate = {node: next_node + index for index, node in enumerate(split_nodes)}
    rebuilt_elements: list[str] = []
    duplicated_wall_edges = 0
    for element_id, element_type, tags, conn in parsed:
        physical = tags[0] if tags else 0
        if element_type == 1 and physical == names[(1, "inlet_interface_temporary")]:
            continue
        mapped = conn
        if element_type in {2, 3} and physical == names[(2, "fluid_internal")]:
            mapped = [duplicate.get(node, node) for node in conn]
        prefix = [str(element_id), str(element_type), str(len(tags)), *map(str, tags)]
        rebuilt_elements.append(" ".join(prefix + [str(node) for node in mapped]))
        if element_type == 1 and physical == names[(1, "airfoil_wall")]:
            max_element += 1
            internal_edge = [duplicate.get(node, node) for node in conn]
            rebuilt_elements.append(
                " ".join([str(max_element), "1", str(len(tags)), *map(str, tags), *map(str, internal_edge)])
            )
            duplicated_wall_edges += 1
    rebuilt_nodes = list(node_rows)
    for original, created in duplicate.items():
        x, y, z = nodes[original]
        rebuilt_nodes.append(f"{created} {x:.16g} {y:.16g} {z:.16g}")
    rebuilt = (
        lines[: p0 + 1] + [str(len(kept_physical))] + kept_physical
        + lines[p1 : n0 + 1] + [str(len(rebuilt_nodes))] + rebuilt_nodes
        + lines[n1 : e0 + 1] + [str(len(rebuilt_elements))] + rebuilt_elements
        + lines[e1:]
    )
    mesh_path.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")
    return {
        "zero_thickness_wall_strategy": "shared_geometry_then_internal_baffle_node_split",
        "duplicated_internal_wall_nodes": len(duplicate),
        "duplicated_internal_wall_edges": duplicated_wall_edges,
        "shared_inlet_nodes": len(inlet_nodes),
        "temporary_inlet_line_elements_removed": True,
    }


def build_2d_mesh(root: Path, revision: Path, config: dict[str, Any]) -> dict[str, Any]:
    try:
        import gmsh
    except ImportError as exc:
        raise RuntimeError("The Gmsh Python API is required in the application environment") from exc

    geometry = load_geometry(root, config)
    wall = geometry["wall"]
    inlet = geometry["inlet"]
    chord = float(geometry["chord"])
    cap_range = (int(geometry["upper_end_index"]), int(geometry["cap_end_index"]))
    layer = flat_plate_first_height(config)
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 1)
    gmsh.option.setNumber("General.NumThreads", int(config["execution"]["gmsh_threads"]))
    gmsh.option.setNumber("Mesh.MaxNumThreads1D", int(config["execution"]["gmsh_threads"]))
    gmsh.option.setNumber("Mesh.MaxNumThreads2D", int(config["execution"]["gmsh_threads"]))
    gmsh.option.setNumber("Mesh.Algorithm", int(config["external_volume"]["mesh_algorithm"]))
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    gmsh.option.setNumber("Mesh.Binary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber(
        "Mesh.MeshSizeMax", chord * float(config["external_volume"]["farfield_size_chord"]),
    )
    gmsh.option.setNumber(
        "Mesh.Smoothing", int(config.get("execution", {}).get("mesh_smoothing", 1))
    )
    gmsh.option.setNumber("Mesh.Optimize", 1)
    # BoundaryLayer{Quads=1} is the authoritative BL recombination.  Blossom
    # improves the surrounding recombination when a geometric law is used,
    # while RecombineAll remains disabled so farfield triangles are preserved.
    gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 1)
    gmsh.option.setNumber("Mesh.RecombineAll", 0)
    gmsh.option.setNumber("Mesh.BoundaryLayerFanElements", 7)
    gmsh.logger.start()
    try:
        gmsh.model.add(EXPERIMENT_ID)
        geo = gmsh.model.geo
        ext_points = [geo.addPoint(float(x), float(y), 0.0) for x, y in wall]
        inlet_points = [ext_points[0]] + [geo.addPoint(float(x), float(y), 0.0) for x, y in inlet[1:-1]] + [ext_points[-1]]
        lip_transition_length = chord * float(
            config["boundary_layer"].get("unstructured_lip_length_chord", 0.01)
        )
        upper_candidates = np.arange(1, cap_range[0], dtype=int)
        lower_candidates = np.arange(cap_range[1] + 1, len(wall) - 1, dtype=int)
        upper_split = int(upper_candidates[np.argmin(np.abs(
            np.linalg.norm(wall[upper_candidates] - wall[0], axis=1) - lip_transition_length
        ))])
        lower_split = int(lower_candidates[np.argmin(np.abs(
            np.linalg.norm(wall[lower_candidates] - wall[-1], axis=1) - lip_transition_length
        ))])
        upper_split = max(2, min(cap_range[0] - 2, upper_split))
        lower_split = max(cap_range[1] + 2, min(len(wall) - 3, lower_split))
        # The two end segments remain curved and fine, but unstructured. The BL
        # starts where the wall tangent is already stable, avoiding a singular
        # open-end extrusion at either inlet lip.
        if bool(config["boundary_layer"].get("continue_over_base_inlet", True)):
            if bool(config["boundary_layer"].get("automatic_bump_matching", False)) and bool(
                config["boundary_layer"].get("te_segment_early_start_enabled", False)
            ):
                start_x = chord * min(0.999, max(0.50, float(
                    config["boundary_layer"].get("te_segment_start_x_over_c", 0.98)
                )))
                upper_te_start = int(np.argmin(np.abs(wall[: cap_range[0], 0] - start_x)))
                lower_te_end = cap_range[1] + int(np.argmin(
                    np.abs(wall[cap_range[1] :, 0] - start_x)
                ))
                upper_te_start = max(2, min(cap_range[0] - 1, upper_te_start))
                lower_te_end = max(cap_range[1] + 1, min(len(wall) - 3, lower_te_end))
            else:
                upper_te_start, lower_te_end = cap_range
            wall_sections = [
                (0, upper_te_start, "smooth_bump_at_lip_and_te"),
                (upper_te_start, lower_te_end, "te_segment_with_optional_approach"),
                (lower_te_end, len(wall) - 1, "smooth_bump_at_lip_and_te"),
            ]
        else:
            wall_sections = [
                (0, upper_split, "unstructured_lip_transition"),
                (upper_split, cap_range[0], "smooth_bump_at_lip_and_te"),
                (cap_range[0], cap_range[1], "uniform_te_cap"),
                (cap_range[1], lower_split, "smooth_bump_at_lip_and_te"),
                (lower_split, len(wall) - 1, "unstructured_lip_transition"),
            ]
        ext_curves: list[int] = []
        external_loop_curves: list[int] = []
        ext_report: list[dict[str, Any]] = []
        total_nodes = int(config["boundary_layer"].get("wall_nodes_target", 1050))
        minimum_tangential = float(
            config["boundary_layer"].get("minimum_tangential_size_chord", 0.00030)
        )
        maximum_tangential = float(
            config["boundary_layer"].get("maximum_tangential_size_chord", 0.0050)
        )
        if minimum_tangential <= 0 or maximum_tangential < minimum_tangential:
            raise ValueError(
                "Tangential sizes must satisfy 0 < minimum_tangential_size_chord "
                "<= maximum_tangential_size_chord"
            )
        lengths = [
            float(np.sum(np.linalg.norm(np.diff(wall[start : end + 1], axis=0), axis=1)))
            for start, end, _ in wall_sections
        ]
        total_length = max(sum(lengths), 1.0e-15)
        bump = float(config["boundary_layer"].get("wall_bump_coefficient", 0.06))
        tangential_method = str(config["boundary_layer"].get(
            "tangential_distribution_method", "four_bumps"
        ))
        if tangential_method not in {"four_bumps", "bump_split_progression"}:
            raise ValueError(f"Unsupported tangential distribution method: {tangential_method}")
        if tangential_method == "bump_split_progression" and not bool(
            config["boundary_layer"].get("continue_over_base_inlet", True)
        ):
            raise ValueError("Bump + Split Progression requires the continuous base-inlet BL")
        automatic_bump = bool(
            config["boundary_layer"].get("automatic_bump_matching", False)
            and config["boundary_layer"].get("continue_over_base_inlet", True)
        )
        manual_four_segment_bump = bool(
            not automatic_bump
            and (
                tangential_method == "bump_split_progression"
                or config["boundary_layer"].get("manual_four_segment_bump_enabled", False)
            )
            and config["boundary_layer"].get("continue_over_base_inlet", True)
        )
        auto_divisions = {
            name: int(value)
            for name, value in dict(
                config["boundary_layer"].get("segment_divisions") or {}
            ).items()
        }
        automatic_report: dict[str, Any] | None = None
        manual_coefficients = {
            name: float(value)
            for name, value in dict(
                config["boundary_layer"].get("manual_bump_coefficients") or {}
            ).items()
        }
        if manual_four_segment_bump:
            missing = {
                "te", "upper", "leading_or_inlet", "lower"
            }.difference(auto_divisions).union(
                {"te", "upper", "leading_or_inlet", "lower"}.difference(
                    manual_coefficients
                )
            )
            if missing:
                raise ValueError(
                    "Manual four-segment Bump is missing values for: "
                    + ", ".join(sorted(missing))
                )
            if any(value <= 0.0 for value in manual_coefficients.values()):
                raise ValueError("Manual Bump coefficients must be positive")
        wall_segment_names = ["upper", "te", "lower"]
        inlet_length_for_match = float(
            np.sum(np.linalg.norm(np.diff(inlet, axis=0), axis=1))
        )
        base_wall_lengths = {
            "upper": float(np.sum(np.linalg.norm(
                np.diff(wall[0 : cap_range[0] + 1], axis=0), axis=1
            ))),
            "te": float(np.sum(np.linalg.norm(
                np.diff(wall[cap_range[0] : cap_range[1] + 1], axis=0), axis=1
            ))),
            "lower": float(np.sum(np.linalg.norm(
                np.diff(wall[cap_range[1] :], axis=0), axis=1
            ))),
            "leading_or_inlet": inlet_length_for_match,
        }
        if automatic_bump:
            automatic_report = match_four_segment_bumps(
                base_wall_lengths,
                auto_divisions,
                chord=chord,
                maximum_growth_ratio=float(
                    config["boundary_layer"].get("bump_maximum_growth_ratio", 1.10)
                ),
                maximum_size_percent_chord=float(
                    config["boundary_layer"].get("bump_maximum_size_percent_chord", 1.00)
                ),
            )
        split_report: dict[str, Any] | None = None
        if tangential_method == "bump_split_progression":
            midpoint_x = chord * float(config["boundary_layer"].get(
                "split_progression_midpoint_x_chord", 0.50
            ))
            upper_index = min(cap_range[0] - 3, max(3, int(np.argmin(
                np.abs(wall[: cap_range[0], 0] - midpoint_x)
            ))))
            lower_index = cap_range[1] + int(np.argmin(
                np.abs(wall[cap_range[1] :, 0] - midpoint_x)
            ))
            lower_index = min(len(wall) - 4, max(cap_range[1] + 3, lower_index))
            specs = [
                ("upper_leading_or_inlet", ext_points[0 : upper_index + 1], 1),
                ("upper_te", list(reversed(ext_points[upper_index : cap_range[0] + 1])), -1),
                ("te", ext_points[cap_range[0] : cap_range[1] + 1], 1),
                ("lower_te", ext_points[cap_range[1] : lower_index + 1], 1),
                ("lower_leading_or_inlet", list(reversed(ext_points[lower_index:])), -1),
            ]
            half_lengths = {
                "upper": {
                    "leading_or_inlet": float(np.sum(np.linalg.norm(
                        np.diff(wall[0 : upper_index + 1], axis=0), axis=1
                    ))),
                    "te": float(np.sum(np.linalg.norm(
                        np.diff(wall[upper_index : cap_range[0] + 1], axis=0), axis=1
                    ))),
                },
                "lower": {
                    "leading_or_inlet": float(np.sum(np.linalg.norm(
                        np.diff(wall[lower_index:], axis=0), axis=1
                    ))),
                    "te": float(np.sum(np.linalg.norm(
                        np.diff(wall[cap_range[1] : lower_index + 1], axis=0), axis=1
                    ))),
                },
            }
            curved_bumps = {
                name: float(
                    automatic_report["coefficients"][name]
                    if automatic_report is not None else manual_coefficients[name]
                )
                for name in ("leading_or_inlet", "te")
            }
            curved_divisions = {
                name: int(auto_divisions[name]) for name in ("leading_or_inlet", "te")
            }
            if automatic_bump:
                split_report = automatic_split_progression(
                    half_lengths=half_lengths,
                    body_divisions={
                        "upper": int(auto_divisions["upper"]),
                        "lower": int(auto_divisions["lower"]),
                    },
                    curved_lengths={name: base_wall_lengths[name] for name in curved_divisions},
                    curved_divisions=curved_divisions,
                    curved_bumps=curved_bumps,
                    chord=chord,
                    maximum_growth_ratio=float(config["boundary_layer"].get(
                        "bump_maximum_growth_ratio", 1.10
                    )),
                    maximum_size_percent_chord=float(config["boundary_layer"].get(
                        "bump_maximum_size_percent_chord", 0.50
                    )),
                )
                if split_report["blocks_generation"]:
                    raise ValueError("; ".join(split_report["warnings"]))
            else:
                manual_split = dict(config["boundary_layer"].get(
                    "manual_split_progression") or {})
                endpoint_sizes = {
                    name: float(bump_cell_sizes(
                        curved_bumps[name], base_wall_lengths[name], curved_divisions[name]
                    )[0])
                    for name in curved_divisions
                }
                split_report = evaluate_manual_split_progression(
                    half_lengths=half_lengths,
                    split_divisions=dict(manual_split.get("split_divisions") or {}),
                    progression_coefficients=dict(
                        manual_split.get("progression_coefficients") or {}
                    ),
                    endpoint_sizes=endpoint_sizes,
                    chord=chord,
                    maximum_growth_ratio=float(config["boundary_layer"].get(
                        "bump_maximum_growth_ratio", 1.10
                    )),
                    maximum_size_percent_chord=float(config["boundary_layer"].get(
                        "bump_maximum_size_percent_chord", 0.50
                    )),
                )
            for label, tags, loop_sign in specs:
                ext_curve = geo.addSpline(tags)
                if label == "te":
                    divisions = int(auto_divisions["te"])
                    coefficient = curved_bumps["te"]
                    geo.mesh.setTransfiniteCurve(ext_curve, divisions + 1, "Bump", coefficient)
                    distribution = f"bump_{coefficient:.8g}"
                else:
                    divisions = int(split_report["split_divisions"][label])
                    coefficient = float(split_report["progression_coefficients"][label])
                    geo.mesh.setTransfiniteCurve(
                        ext_curve, divisions + 1, "Progression", coefficient
                    )
                    distribution = f"progression_fine_to_mid_{coefficient:.8g}"
                ext_curves.append(ext_curve)
                external_loop_curves.append(loop_sign * ext_curve)
                ext_report.append({
                    "curve": ext_curve,
                    "label": label,
                    "length_m": (
                        base_wall_lengths["te"] if label == "te"
                        else half_lengths[label.split("_", 1)[0]][
                            "leading_or_inlet" if label.endswith("leading_or_inlet") else "te"
                        ]
                    ),
                    "nodes": divisions + 1,
                    "method": distribution,
                    "tangential_sizing": {
                        "selected_divisions": divisions,
                        "coefficient": coefficient,
                    },
                })
        else:
            for wall_index, ((start, end, method), length) in enumerate(zip(wall_sections, lengths)):
                four_segment = (
                    wall_segment_names[wall_index]
                    if (automatic_report is not None or manual_four_segment_bump)
                    and len(wall_sections) == 3
                    else None
                )
                ext_curve = geo.addSpline(ext_points[start : end + 1])
                if four_segment is not None:
                    selected_divisions = (
                        automatic_report["divisions"]
                        if automatic_report is not None else auto_divisions
                    )
                    selected_coefficients = (
                        automatic_report["coefficients"]
                        if automatic_report is not None else manual_coefficients
                    )
                    ext_nodes = int(selected_divisions[four_segment]) + 1
                    selected_coefficient = float(selected_coefficients[four_segment])
                    geo.mesh.setTransfiniteCurve(
                        ext_curve, ext_nodes, "Bump", selected_coefficient,
                    )
                    sizing_report = {
                        "four_segment_mode": "automatic" if automatic_report is not None else "manual",
                        "segment": four_segment,
                        "selected_divisions": ext_nodes - 1,
                        "selected_nodes": ext_nodes,
                        "bump_coefficient": selected_coefficient,
                    }
                elif method in {"uniform_te_cap", "unstructured_lip_transition"}:
                    minimum_required_nodes, _ = _transfinite_node_bounds(
                        length, chord, minimum_tangential, maximum_tangential
                    )
                    explicit_nodes = (
                        int(config["boundary_layer"].get("te_cap_nodes", 36))
                        if method == "uniform_te_cap" else 12
                    )
                    ext_nodes = max(4, explicit_nodes, minimum_required_nodes)
                    geo.mesh.setTransfiniteCurve(ext_curve, ext_nodes)
                    sizing_report = {
                        "explicit_nodes": explicit_nodes,
                        "minimum_nodes_from_maximum_size": minimum_required_nodes,
                        "minimum_tangential_size_chord": minimum_tangential,
                        "maximum_tangential_size_chord": maximum_tangential,
                        "selected_nodes": ext_nodes,
                    }
                else:
                    lower_nodes, upper_nodes = _transfinite_node_bounds(
                        length, chord, minimum_tangential, maximum_tangential
                    )
                    requested_nodes = max(80, int(round(total_nodes * length / total_length)))
                    ext_nodes = max(lower_nodes, min(upper_nodes, requested_nodes))
                    geo.mesh.setTransfiniteCurve(ext_curve, ext_nodes, "Bump", bump)
                    sizing_report = {
                        "requested_nodes_from_wall_nodes_target": requested_nodes,
                        "minimum_nodes_from_maximum_size": lower_nodes,
                        "maximum_nodes_from_minimum_size": upper_nodes,
                        "minimum_tangential_size_chord": minimum_tangential,
                        "maximum_tangential_size_chord": maximum_tangential,
                        "selected_nodes": ext_nodes,
                    }
                ext_curves.append(ext_curve)
                external_loop_curves.append(ext_curve)
                ext_report.append({
                    "curve": ext_curve, "length_m": length, "nodes": ext_nodes, "method": method,
                    "bump": bump if method == "smooth_bump_at_lip_and_te" else None,
                    "tangential_sizing": sizing_report,
                })

        inlet_nodes = int(config["boundary_layer"].get("inlet_nodes", 240))
        inlet_bump = float(config["boundary_layer"].get("inlet_bump_coefficient", 0.08))
        base_start = int(geometry["inlet_join"]["base_start_index"])
        base_end = int(geometry["inlet_join"]["base_end_index"])
        if base_start == 0 and base_end == len(inlet_points) - 1:
            inlet_sections = [(0, base_end, "exact_shared_source_inlet")]
        else:
            inlet_sections = [
                (0, base_start, "upper_c1_connector"),
                (base_start, base_end, "exact_uncut_base_guide"),
                (base_end, len(inlet_points) - 1, "lower_c1_connector"),
            ]
        section_lengths = [
            float(np.sum(np.linalg.norm(np.diff(inlet[start : end + 1], axis=0), axis=1)))
            for start, end, _ in inlet_sections
        ]
        inlet_length = float(np.sum(np.linalg.norm(np.diff(inlet, axis=0), axis=1)))
        requested_elements = max(
            18,
            int(automatic_report["divisions"]["leading_or_inlet"])
            if automatic_report is not None
            else int(auto_divisions["leading_or_inlet"])
            if manual_four_segment_bump
            else inlet_nodes - 1,
        )
        element_counts = [
            max(5 if "connector" in method else 8, int(round(requested_elements * length / inlet_length)))
            for length, (_, _, method) in zip(section_lengths, inlet_sections)
        ]
        # Preserve the requested total while retaining enough elements to
        # resolve each C1 connector.  A uniform physical spacing on the three
        # curves gives both shared endpoints the same tangential scale.
        while sum(element_counts) < requested_elements:
            index = int(np.argmax(np.asarray(section_lengths) / np.asarray(element_counts)))
            element_counts[index] += 1
        while sum(element_counts) > requested_elements:
            candidates = [
                index for index, ((_, _, method), count) in enumerate(zip(inlet_sections, element_counts))
                if count > (5 if "connector" in method else 8)
            ]
            if not candidates:
                break
            index = max(candidates, key=lambda item: element_counts[item] / max(section_lengths[item], 1.0e-15))
            element_counts[index] -= 1
        # Gmsh's BoundaryLayer field treats endpoints of separate curves as
        # geometric corners even when their sampled tangents are C1.  Three
        # inlet entities consequently created artificial fan/collision points
        # on the offset front.  Keep the exact base-profile samples and the two
        # local C1 connectors, but expose them as one continuous interpolating
        # entity to the BL algorithm.  The dense base samples constrain this
        # spline to the original uncut contour without a global lip-to-lip
        # Hermite bridge.
        inlet_curve = geo.addSpline(inlet_points)
        if automatic_report is not None:
            inlet_bump = float(
                automatic_report["coefficients"]["leading_or_inlet"]
            )
            geo.mesh.setTransfiniteCurve(
                inlet_curve, requested_elements + 1, "Bump", inlet_bump,
            )
            inlet_distribution = f"automatic_bump_{inlet_bump:.8g}"
        elif manual_four_segment_bump:
            inlet_bump = float(manual_coefficients["leading_or_inlet"])
            geo.mesh.setTransfiniteCurve(
                inlet_curve, requested_elements + 1, "Bump", inlet_bump,
            )
            inlet_distribution = f"manual_four_segment_bump_{inlet_bump:.8g}"
        elif math.isclose(inlet_bump, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
            geo.mesh.setTransfiniteCurve(inlet_curve, requested_elements + 1)
            inlet_distribution = "uniform"
        else:
            geo.mesh.setTransfiniteCurve(
                inlet_curve, requested_elements + 1, "Bump", inlet_bump,
            )
            inlet_distribution = f"bump_{inlet_bump:.6g}"
        inlet_curves = [inlet_curve]
        inlet_report = [{
            "curve": inlet_curve,
            "length_m": inlet_length,
            "elements": requested_elements,
            "mean_size_m": inlet_length / max(requested_elements, 1),
            "method": "single_gmsh_spline_over_exact_base_samples_and_local_c1_connectors",
            "distribution": inlet_distribution,
            "sections": [
                {
                    "method": method,
                    "length_m": length,
                    "target_elements": elements,
                }
                for (_, _, method), length, elements in zip(
                    inlet_sections, section_lengths, element_counts
                )
            ],
            "inlet_bump_coefficient": inlet_bump,
        }]
        inlet_spacing = inlet_length / max(1, sum(element_counts))

        radius = chord * float(config["external_volume"]["domain_radius_chord"])
        center_x = 0.25 * chord
        center = geo.addPoint(center_x, 0.0, 0.0)
        far_points = [
            geo.addPoint(center_x + radius, 0.0, 0.0), geo.addPoint(center_x, radius, 0.0),
            geo.addPoint(center_x - radius, 0.0, 0.0), geo.addPoint(center_x, -radius, 0.0),
        ]
        far_curves = [
            geo.addCircleArc(far_points[index], center, far_points[(index + 1) % 4])
            for index in range(4)
        ]
        outer_loop = geo.addCurveLoop(far_curves)
        external_profile_loop = geo.addCurveLoop(
            external_loop_curves + [-curve for curve in reversed(inlet_curves)]
        )
        internal_profile_loop = geo.addCurveLoop(
            inlet_curves + [-curve for curve in reversed(external_loop_curves)]
        )
        external_surface = geo.addPlaneSurface([outer_loop, external_profile_loop])
        internal_surface = geo.addPlaneSurface([internal_profile_loop])
        geo.synchronize()

        wall_group = gmsh.model.addPhysicalGroup(1, ext_curves)
        gmsh.model.setPhysicalName(1, wall_group, "airfoil_wall")
        inlet_group = gmsh.model.addPhysicalGroup(1, inlet_curves)
        gmsh.model.setPhysicalName(1, inlet_group, "inlet_interface_temporary")
        farfield_group = gmsh.model.addPhysicalGroup(1, far_curves)
        gmsh.model.setPhysicalName(1, farfield_group, "farfield")
        external_group = gmsh.model.addPhysicalGroup(2, [external_surface])
        gmsh.model.setPhysicalName(2, external_group, "fluid_external")
        internal_group = gmsh.model.addPhysicalGroup(2, [internal_surface])
        gmsh.model.setPhysicalName(2, internal_group, "fluid_internal")

        boundary_field = gmsh.model.mesh.field.add("BoundaryLayer")
        if bool(config["boundary_layer"].get("continue_over_base_inlet", True)):
            layer_curves = list(ext_curves)
            layer_curves.extend(inlet_curves)
        else:
            layer_curves = [
                curve for curve, item in zip(ext_curves, ext_report)
                if item["method"] != "unstructured_lip_transition"
            ]
        gmsh.model.mesh.field.setNumbers(boundary_field, "CurvesList", layer_curves)
        gmsh.model.mesh.field.setNumber(boundary_field, "Size", layer["first_cell_height_m"])
        gmsh.model.mesh.field.setNumber(boundary_field, "Thickness", layer["total_thickness_m"])
        if layer["distribution_mode"] == "beta_law":
            gmsh.model.mesh.field.setNumber(boundary_field, "BetaLaw", 1)
            gmsh.model.mesh.field.setNumber(
                boundary_field, "Beta", float(layer["beta_calculated"]),
            )
            gmsh.model.mesh.field.setNumber(boundary_field, "NbLayers", int(layer["layers"]))
        else:
            gmsh.model.mesh.field.setNumber(boundary_field, "Ratio", float(layer["growth_rate"]))
        gmsh.model.mesh.field.setNumber(boundary_field, "Quads", 1)
        # SizeFar is corrected below after the tangential match is known.  It
        # must describe the first unstructured layer, not the raw UI cap.
        gmsh.model.mesh.field.setNumber(boundary_field, "SizeFar", chord * 0.001)
        fan_elements = int(config["boundary_layer"].get("lip_fan_elements", 0))
        if fan_elements > 0:
            gmsh.model.mesh.field.setNumbers(boundary_field, "FanPointsList", [ext_points[0], ext_points[-1]])
            gmsh.model.mesh.field.setNumbers(
                boundary_field, "FanPointsSizesList", [fan_elements] * 2,
            )
        gmsh.model.mesh.field.setNumbers(boundary_field, "ExcludedSurfacesList", [internal_surface])
        gmsh.model.mesh.field.setAsBoundaryLayer(boundary_field)

        requested_interface = chord * float(
            config["external_volume"].get("interface_size_chord", 0.0018)
        )
        interface_mode = str(
            config["external_volume"].get("interface_size_mode", "fixed")
        ).strip().lower()
        tangential_reference = min(
            inlet_spacing,
            min(
                float(item["length_m"]) / max(1, int(item["nodes"]) - 1)
                for item in ext_report
            ),
        )
        matched_interface = tangential_reference * float(
            config["external_volume"].get("interface_tangential_factor", 1.25)
        )
        # In automatic matching the tangential spacing is the source of truth.
        # Treating the optional fixed value as a hidden upper bound allowed a
        # stale 1e-5[c] draft value to override the actual wall spacing and
        # created a very abrupt BL-to-triangle transition.  The fixed value is
        # intentionally used only when the user selects ``fixed``.
        external_interface = (
            matched_interface if interface_mode == "tangential_match" else requested_interface
        )
        gmsh.model.mesh.field.setNumber(boundary_field, "SizeFar", external_interface)
        external_far = chord * float(config["external_volume"]["farfield_size_chord"])
        radial_growth = min(0.50, max(0.005, float(
            config["external_volume"].get("radial_growth_rate", 0.20)
        )))
        external_extend = bool(
            config["external_volume"].get("automatic_extend_enabled", False)
        )
        if external_extend:
            external_restrict = add_extend_field(
                gmsh,
                surfaces=[external_surface],
                curves=ext_curves + inlet_curves,
                dist_max=chord * float(config["external_volume"].get(
                    "extend_distance_max_chord", config["external_volume"]["domain_radius_chord"]
                )),
                power=float(config["external_volume"].get("extend_power", 2.0)),
                size_max=chord * float(config["external_volume"].get(
                    "extend_size_max_chord",
                    config["external_volume"]["farfield_size_chord"],
                )),
            )
            size_fields = [external_restrict]
            if bool(config["external_volume"].get("extend_interface_guard_enabled", True)):
                guard_transition = chord * float(config["external_volume"].get(
                    "extend_interface_transition_chord", 0.10
                ))
                guard = add_smooth_interface_guard(
                    gmsh,
                    surfaces=[external_surface],
                    curves=ext_curves + inlet_curves,
                    size_at_interface=external_interface,
                    size_far=external_far,
                    boundary_layer_thickness=float(layer["total_thickness_m"]),
                    transition_distance=guard_transition,
                )
                combined = gmsh.model.mesh.field.add("Min")
                gmsh.model.mesh.field.setNumbers(combined, "FieldsList", [external_restrict, guard])
                external_restrict = combined
            external_size_report = {
                "type": "gmsh_extend_from_variable_geometric_boundary",
                "distance_max_chord": float(config["external_volume"].get(
                    "extend_distance_max_chord", config["external_volume"]["domain_radius_chord"]
                )),
                "power": float(config["external_volume"].get("extend_power", 2.0)),
                "size_max_chord": float(config["external_volume"].get(
                    "extend_size_max_chord", config["external_volume"]["farfield_size_chord"]
                )),
                "source": "actual transfinite Bump sizes on wall and virtual-inlet curves",
                "requested_start": "generated external boundary-layer front",
                "actual_start": "wall and virtual-inlet curves with the same BL-column indexing",
                "surface_scope": "external fluid surface only",
                "farfield_reach": (
                    "At and beyond DistMax the external field reaches SizeMax up to the "
                    "circular farfield boundary."
                ),
                "limitation": (
                    "Gmsh evaluates fields before the generated outer BL row exists; "
                    "the topology-safe source is the geometric curve discretization that "
                    "also defines the BL-column widths."
                ),
                "interface_guard": {
                    "enabled": bool(config["external_volume"].get("extend_interface_guard_enabled", True)),
                    "size_chord": external_interface / chord,
                    "starts_at_bl_thickness_chord": float(layer["total_thickness_m"]) / chord,
                    "transition_distance_chord": float(config["external_volume"].get(
                        "extend_interface_transition_chord", 0.10
                    )),
                },
            }
        else:
            external_distance = gmsh.model.mesh.field.add("Distance")
            gmsh.model.mesh.field.setNumbers(external_distance, "CurvesList", ext_curves + inlet_curves)
            gmsh.model.mesh.field.setNumber(external_distance, "Sampling", 1200)
            external_threshold = gmsh.model.mesh.field.add("MathEval")
            gmsh.model.mesh.field.setString(
                external_threshold,
                "F",
                f"Min({external_far:.16g}, {external_interface:.16g} + {radial_growth:.16g}*F{external_distance})",
            )
            external_restrict = gmsh.model.mesh.field.add("Restrict")
            gmsh.model.mesh.field.setNumber(external_restrict, "InField", external_threshold)
            gmsh.model.mesh.field.setNumbers(external_restrict, "SurfacesList", [external_surface])
            external_size_report = {
                "type": "linear_distance_capped",
                "interface_size_chord": external_interface / chord,
                "requested_interface_size_chord": requested_interface / chord,
                "interface_size_mode": interface_mode,
                "tangential_reference_chord": tangential_reference / chord,
                "farfield_size_chord": external_far / chord,
                "maximum_local_growth_fraction": radial_growth,
            }

        internal_distance = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(internal_distance, "CurvesList", inlet_curves)
        gmsh.model.mesh.field.setNumber(internal_distance, "Sampling", 500)
        internal_threshold = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(internal_threshold, "InField", internal_distance)
        gmsh.model.mesh.field.setNumber(
            internal_threshold, "SizeMin", inlet_spacing * float(config["internal_volume"]["inlet_size_factor"]),
        )
        gmsh.model.mesh.field.setNumber(internal_threshold, "SizeMax", chord * float(config["internal_volume"]["core_size_chord"]))
        gmsh.model.mesh.field.setNumber(internal_threshold, "DistMin", chord * float(config["internal_volume"]["inlet_fine_distance_chord"]))
        gmsh.model.mesh.field.setNumber(internal_threshold, "DistMax", chord * float(config["internal_volume"]["transition_distance_chord"]))
        gmsh.model.mesh.field.setNumber(internal_threshold, "Sigmoid", 1)
        inner_wall_distance = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(inner_wall_distance, "CurvesList", ext_curves)
        gmsh.model.mesh.field.setNumber(inner_wall_distance, "Sampling", 1000)
        inner_wall_threshold = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(inner_wall_threshold, "InField", inner_wall_distance)
        gmsh.model.mesh.field.setNumber(
            inner_wall_threshold, "SizeMin",
            chord * float(config["internal_volume"].get("inner_wall_size_chord", 0.0012)),
        )
        gmsh.model.mesh.field.setNumber(
            inner_wall_threshold, "SizeMax",
            chord * float(config["internal_volume"]["core_size_chord"]),
        )
        gmsh.model.mesh.field.setNumber(inner_wall_threshold, "DistMin", 0.0)
        gmsh.model.mesh.field.setNumber(
            inner_wall_threshold, "DistMax",
            chord * float(config["internal_volume"].get(
                "inner_wall_transition_distance_chord", 0.045
            )),
        )
        gmsh.model.mesh.field.setNumber(inner_wall_threshold, "Sigmoid", 1)

        te_distance = gmsh.model.mesh.field.add("Distance")
        te_curves = [
            curve
            for curve, item in zip(ext_curves, ext_report)
            if item.get("label") == "te"
            or item["method"] == "uniform_te_cap"
            or dict(item.get("tangential_sizing") or {}).get("segment") == "te"
        ]
        if not te_curves:
            raise ValueError(
                "The internal-TE sizing field could not identify the physical TE curve"
            )
        gmsh.model.mesh.field.setNumbers(te_distance, "CurvesList", te_curves)
        gmsh.model.mesh.field.setNumber(te_distance, "Sampling", 240)
        te_threshold = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(te_threshold, "InField", te_distance)
        gmsh.model.mesh.field.setNumber(
            te_threshold, "SizeMin",
            chord * float(config["internal_volume"].get("te_internal_size_chord", 0.0008)),
        )
        gmsh.model.mesh.field.setNumber(
            te_threshold, "SizeMax",
            chord * float(config["internal_volume"]["core_size_chord"]),
        )
        gmsh.model.mesh.field.setNumber(te_threshold, "DistMin", 0.0)
        gmsh.model.mesh.field.setNumber(
            te_threshold, "DistMax",
            chord * float(config["internal_volume"].get(
                "te_internal_transition_distance_chord", 0.025
            )),
        )
        gmsh.model.mesh.field.setNumber(te_threshold, "Sigmoid", 1)

        internal_extend = bool(
            config["internal_volume"].get("automatic_extend_enabled", False)
        )
        if internal_extend:
            internal_extend_field = add_extend_field(
                gmsh,
                surfaces=[internal_surface],
                curves=ext_curves + inlet_curves,
                dist_max=chord * float(config["internal_volume"].get(
                    "extend_distance_max_chord", 0.10
                )),
                power=float(config["internal_volume"].get("extend_power", 2.0)),
                size_max=chord * float(config["internal_volume"].get(
                    "extend_size_max_chord", config["internal_volume"]["core_size_chord"]
                )),
            )
            internal_size_report = {
                "type": "gmsh_extend_from_variable_internal_boundaries",
                "distance_max_chord": float(config["internal_volume"].get("extend_distance_max_chord", 0.10)),
                "power": float(config["internal_volume"].get("extend_power", 2.0)),
                    "size_max_chord": float(config["internal_volume"].get(
                        "extend_size_max_chord", config["internal_volume"]["core_size_chord"]
                    )),
                "sources": ["virtual inlet", "shared internal airfoil wall"],
                "requested_start": (
                    "first prism interface at the inlet and internal airfoil walls elsewhere"
                ),
                "actual_start": (
                    "virtual inlet and shared wall CAD curves; Gmsh fields are evaluated before "
                    "a generated prism-front entity exists"
                ),
                "surface_scope": "internal cavity surface only",
                "local_thresholds_retained": [
                    "inlet", "inner_wall", "internal_te",
                ],
                "limitation": (
                    "The geometric source is topologically equivalent in tangential indexing, "
                    "but it is not the generated first-prism front. Native checkMesh must decide "
                    "whether the inherited transition is acceptable."
                ),
            }
        local_internal_min = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(
            local_internal_min,
            "FieldsList",
            [internal_threshold, inner_wall_threshold, te_threshold],
        )
        internal_min = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(
            internal_min,
            "FieldsList",
            [local_internal_min, internal_extend_field]
            if internal_extend else [local_internal_min],
        )
        internal_restrict = gmsh.model.mesh.field.add("Restrict")
        gmsh.model.mesh.field.setNumber(internal_restrict, "InField", internal_min)
        gmsh.model.mesh.field.setNumbers(internal_restrict, "SurfacesList", [internal_surface])
        if not internal_extend:
            internal_size_report = {"type": "threshold_minimum_baseline"}
        combined = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(combined, "FieldsList", [external_restrict, internal_restrict])
        gmsh.model.mesh.field.setAsBackgroundMesh(combined)

        generation_started = time.perf_counter()
        try:
            if automatic_report is not None:
                gmsh.model.mesh.generate(1)
                automatic_report["gmsh_1d_verification"] = {
                    "status": "GENERATED",
                    "note": (
                        "The predictor uses physical arc length; Gmsh generated the four "
                        "transfinite curves successfully before the 2D mesh."
                    ),
                }
            gmsh.model.mesh.generate(2)
        except Exception:
            last_error = str(gmsh.logger.getLastError())
            node_diagnostics: dict[str, Any] = {}
            for token in re.findall(r"\b[0-9]+\b", last_error):
                try:
                    xyz, _, _, _ = gmsh.model.mesh.getNode(int(token))
                    node_diagnostics[token] = [float(value) for value in xyz]
                except Exception:
                    continue
            write_json_atomic(revision / "gmsh_failure_diagnostics.json", {
                "last_error": last_error,
                "nodes": node_diagnostics,
                "boundary_layer": layer,
                "log": gmsh.logger.get(),
            })
            raise
        optimization = str(
            config.get("execution", {}).get("post_generation_optimization", "off")
        )
        optimization_iterations = max(
            1, int(config["execution"].get("post_generation_optimization_iterations", 5))
        )
        if optimization in {"laplace2d", "laplace2d_then_relocate2d"}:
            gmsh.model.mesh.optimize("Laplace2D", niter=optimization_iterations)
        if optimization in {"relocate2d", "laplace2d_then_relocate2d"}:
            gmsh.model.mesh.optimize("Relocate2D", niter=optimization_iterations)
        mesh_2d = revision / "mesh_2d.msh"
        gmsh.write(str(mesh_2d))
        baffle_report = split_shared_wall_baffle_msh2(mesh_2d)
        measured_boundary_layer = audit_boundary_layer_mesh(mesh_2d)
        try:
            gmsh.write(str(revision / "geometry.geo_unrolled"))
        except Exception:
            pass
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        element_types, element_tags, _ = gmsh.model.mesh.getElements(2)
        all_elements = np.concatenate(element_tags) if element_tags else np.asarray([], dtype=np.int64)
        quality = np.asarray(gmsh.model.mesh.getElementQualities(all_elements.tolist(), "minSICN"), dtype=float) if len(all_elements) else np.asarray([])
        quality_diagnostics = gmsh_quality_summary(gmsh, all_elements.tolist())
        generation_time_s = time.perf_counter() - generation_started
        element_counts = {str(kind): int(len(tags)) for kind, tags in zip(element_types, element_tags)}
        gmsh_log = "\n".join(gmsh.logger.get()) + "\n"
        (revision / "log.gmsh").write_text(gmsh_log, encoding="utf-8")
        return {
            "mesh_2d": str(mesh_2d), "nodes_2d": int(len(node_tags)),
            "elements_2d_by_gmsh_type": element_counts,
            "minimum_gmsh_minSICN": float(np.min(quality)) if len(quality) else None,
            "mean_gmsh_minSICN": float(np.mean(quality)) if len(quality) else None,
            "gmsh_quality_diagnostics": quality_diagnostics,
            "gmsh_generation_and_optimization_time_s": generation_time_s,
            "boundary_layer": layer, "external_wall_curves": ext_report,
            "boundary_layer_mesh_audit": measured_boundary_layer,
            "automatic_bump_matching": automatic_report,
            "split_progression_matching": split_report,
            "manual_four_segment_bump": {
                "enabled": manual_four_segment_bump,
                "divisions": auto_divisions if manual_four_segment_bump else None,
                "coefficients": (
                    manual_coefficients if manual_four_segment_bump else None
                ),
            },
            "post_generation_optimization": optimization,
            "boundary_layer_curves": (
                "wall_plus_single_curve_exact_base_inlet"
                if any(curve in layer_curves for curve in inlet_curves)
                else "wall_only"
            ),
            "internal_wall_curves": "inherits shared tangential discretization before baffle split",
            "inlet_curves": inlet_report,
            "inlet_y1_transition": {
                "enabled": False,
                "status": "REMOVED_AFTER_IDENTICAL_MESH_FACTOR_STUDY",
                "reason": (
                    "BoundaryLayer PointsList/SizesList had no measurable effect on the "
                    "continuous transfinite inlet spline; normal y1 remains uniform."
                ),
            },
            "inlet_join": geometry["inlet_join"],
            "geometry_identity": geometry["geometry_identity"],
            "inlet_mean_tangential_spacing_m": inlet_spacing,
            "external_surface_tag": external_surface, "internal_surface_tag": internal_surface,
            "external_size_law": external_size_report,
            "internal_size_law": internal_size_report,
            "physical_groups": ["airfoil_wall", "farfield", "fluid"],
            **baffle_report,
        }
    finally:
        try:
            gmsh.logger.stop()
        except Exception:
            pass
        gmsh.finalize()


def _read_msh2_edges(path: Path) -> tuple[dict[int, tuple[float, float]], list[tuple[int, int]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    n0 = lines.index("$Nodes")
    count = int(lines[n0 + 1])
    nodes = {}
    for row in lines[n0 + 2 : n0 + 2 + count]:
        parts = row.split()
        nodes[int(parts[0])] = (float(parts[1]), float(parts[2]))
    e0 = lines.index("$Elements")
    count = int(lines[e0 + 1])
    edges: set[tuple[int, int]] = set()
    for row in lines[e0 + 2 : e0 + 2 + count]:
        parts = row.split()
        kind = int(parts[1])
        tags = int(parts[2])
        conn = [int(value) for value in parts[3 + tags :]]
        if kind not in {2, 3}:
            continue
        for a, b in zip(conn, conn[1:] + conn[:1]):
            edges.add(tuple(sorted((a, b))))
    return nodes, sorted(edges)


def audit_boundary_layer_mesh(mesh_2d: Path) -> dict[str, Any]:
    """Measure the generated quad columns and count internal/external cells."""
    lines = mesh_2d.read_text(encoding="utf-8", errors="replace").splitlines()
    p0 = lines.index("$PhysicalNames")
    pcount = int(lines[p0 + 1])
    physical_names: dict[tuple[int, int], str] = {}
    for row in lines[p0 + 2 : p0 + 2 + pcount]:
        match = re.match(r'\s*(\d+)\s+(\d+)\s+"(.*)"\s*$', row)
        if match:
            physical_names[(int(match.group(1)), int(match.group(2)))] = match.group(3)
    n0 = lines.index("$Nodes")
    ncount = int(lines[n0 + 1])
    nodes: dict[int, np.ndarray] = {}
    for row in lines[n0 + 2 : n0 + 2 + ncount]:
        values = row.split()
        nodes[int(values[0])] = np.asarray(values[1:4], dtype=float)
    e0 = lines.index("$Elements")
    ecount = int(lines[e0 + 1])
    wall_edges: set[tuple[int, int]] = set()
    quads: list[tuple[int, int, int, int]] = []
    triangle_counts = {"fluid_external": 0, "fluid_internal": 0, "other": 0}
    for row in lines[e0 + 2 : e0 + 2 + ecount]:
        values = row.split()
        kind = int(values[1])
        tag_count = int(values[2])
        tags = [int(value) for value in values[3 : 3 + tag_count]]
        conn = tuple(int(value) for value in values[3 + tag_count :])
        physical = tags[0] if tags else 0
        name = physical_names.get((1 if kind == 1 else 2, physical), "other")
        if kind == 1 and name == "airfoil_wall":
            wall_edges.add(tuple(sorted(conn)))
        elif kind == 2:
            triangle_counts[name if name in triangle_counts else "other"] += 1
        elif kind == 3 and len(conn) == 4:
            quads.append(conn)

    edge_to_quads: dict[tuple[int, int], list[int]] = {}
    quad_edges: list[list[tuple[int, int]]] = []
    for index, quad in enumerate(quads):
        edges = [tuple(sorted((quad[offset], quad[(offset + 1) % 4]))) for offset in range(4)]
        quad_edges.append(edges)
        for edge in edges:
            edge_to_quads.setdefault(edge, []).append(index)

    first_heights: list[float] = []
    total_thicknesses: list[float] = []
    layer_counts: list[int] = []
    local_growth: list[float] = []
    for wall_edge in wall_edges:
        candidates = edge_to_quads.get(wall_edge, [])
        if len(candidates) != 1:
            continue
        current_edge = wall_edge
        current_quad = candidates[0]
        heights: list[float] = []
        traversed: set[int] = set()
        while current_quad not in traversed:
            traversed.add(current_quad)
            edges = quad_edges[current_quad]
            try:
                edge_index = edges.index(current_edge)
            except ValueError:
                break
            opposite = edges[(edge_index + 2) % 4]
            midpoint0 = 0.5 * (nodes[current_edge[0]] + nodes[current_edge[1]])
            midpoint1 = 0.5 * (nodes[opposite[0]] + nodes[opposite[1]])
            heights.append(float(np.linalg.norm(midpoint1 - midpoint0)))
            following = [item for item in edge_to_quads.get(opposite, []) if item != current_quad]
            if len(following) != 1:
                break
            current_edge = opposite
            current_quad = following[0]
        if not heights:
            continue
        first_heights.append(heights[0])
        total_thicknesses.append(sum(heights))
        layer_counts.append(len(heights))
        local_growth.extend(
            current / previous for previous, current in zip(heights[:-1], heights[1:])
            if previous > 1.0e-30
        )

    def stats(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"min": None, "median": None, "p95": None, "max": None}
        array = np.asarray(values, dtype=float)
        return {
            "min": float(np.min(array)),
            "median": float(np.median(array)),
            "p95": float(np.percentile(array, 95.0)),
            "max": float(np.max(array)),
        }

    first_stats = stats(first_heights)
    return {
        "measurement_basis": "quad columns starting at the external physical airfoil_wall",
        "measured_column_count": len(first_heights),
        "measured_first_cell_height_m": first_stats,
        "measured_first_cell_centre_distance_m": {
            key: (0.5 * value if value is not None else None)
            for key, value in first_stats.items()
        },
        "measured_total_thickness_m": stats(total_thicknesses),
        "measured_layer_count": stats([float(value) for value in layer_counts]),
        "measured_local_growth_ratio": stats(local_growth),
        "cell_counts_2d": {
            "total": len(quads) + sum(triangle_counts.values()),
            "boundary_layer_quads": len(quads),
            "external_triangles": triangle_counts["fluid_external"],
            "internal_triangles": triangle_counts["fluid_internal"],
            "other_triangles": triangle_counts["other"],
        },
    }


def write_previews(mesh_2d: Path, revision: Path) -> None:
    nodes, edges = _read_msh2_edges(mesh_2d)
    segments = np.asarray([[[*nodes[a]], [*nodes[b]]] for a, b in edges], dtype=float)
    from matplotlib.collections import LineCollection

    for filename, x_limits, y_limits, linewidth in (
        ("mesh_preview_full.png", (-2.0, 4.0), (-2.0, 2.0), 0.15),
        ("mesh_preview_airfoil.png", (-0.12, 1.12), (-0.18, 0.18), 0.22),
        ("mesh_preview_inlet.png", (-0.04, 0.14), (-0.09, 0.09), 0.32),
        ("mesh_preview_te.png", (0.94, 1.08), (-0.08, 0.07), 0.32),
    ):
        fig, ax = plt.subplots(figsize=(9.0, 5.2), constrained_layout=True)
        ax.add_collection(LineCollection(segments, colors="#264653", linewidths=linewidth))
        ax.set_xlim(*x_limits)
        ax.set_ylim(*y_limits)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("x/c")
        ax.set_ylabel("z/c")
        ax.grid(False)
        fig.savefig(revision / filename, dpi=220)
        plt.close(fig)


def declare_boundary_layer_metadata(report: dict[str, Any], config: dict[str, Any]) -> None:
    """Declare the experimental BL so the shared OpenFOAM checker can confirm it."""
    extrusion_hexes = int(report.get("mesh_level_extrusion_hexes", 0) or 0)
    extrusion_prisms = int(report.get("mesh_level_extrusion_prisms", 0) or 0)
    surface_quads = int(report.get("elements_2d_by_gmsh_type", {}).get("3", 0) or 0)
    report.update(
        boundary_layer_requested=True,
        boundary_layer_layers_requested=int(
            report.get("boundary_layer", {}).get(
                "layers", config["boundary_layer"].get("layers", 0)
            )
        ),
        boundary_layer_distribution_mode=str(config["boundary_layer"]["distribution_mode"]),
        number_of_quads=surface_quads,
        number_of_hexes=extrusion_hexes,
        number_of_prisms=extrusion_prisms,
        extruded_3d=bool(extrusion_hexes or extrusion_prisms),
    )


def confirm_measured_boundary_layer_count(report: dict[str, Any]) -> None:
    """Promote an exact layer count only when every audited wall column agrees."""
    audit = report.get("boundary_layer_mesh_audit") or {}
    measured = audit.get("measured_layer_count") or {}
    requested = int(report.get("boundary_layer_layers_requested", 0) or 0)
    columns = int(audit.get("measured_column_count", 0) or 0)
    extrema = [measured.get(key) for key in ("min", "median", "max")]
    confirmed = bool(
        requested > 0
        and columns > 0
        and all(value is not None and abs(float(value) - requested) < 0.5 for value in extrema)
    )
    report["boundary_layer_exact_layer_count_confirmed"] = confirmed
    report["boundary_layer_confirmation_basis"] = (
        f"direct_quad_column_measurement:{columns}_columns"
        if confirmed else "direct_quad_column_measurement_inconclusive"
    )


def generate(root: Path, config_path: Path | None, name: str | None, check_mesh: bool) -> dict[str, Any]:
    config = default_config()
    if config_path is not None:
        if not config_path.is_file():
            raise FileNotFoundError(
                f"The requested experimental mesh configuration does not exist: {config_path}"
            )
        supplied = read_json(config_path, {}) or {}
        if not supplied:
            raise ValueError(
                f"The requested experimental mesh configuration is empty or invalid JSON: {config_path}"
            )
        for section, values in supplied.items():
            if isinstance(values, dict) and isinstance(config.get(section), dict):
                config[section].update(values)
            else:
                config[section] = values
    boundary_config = config["boundary_layer"]
    # Legacy experimental revisions stored two controls that Gmsh cannot
    # satisfy independently in Beta-law mode.  New revisions retain the old
    # files untouched but migrate their editable copy to the physical contract.
    boundary_config.pop("beta_coefficient", None)
    boundary_config.pop("total_thickness_chord", None)
    boundary_config["thickness_safety_factor"] = float(
        boundary_config.get("thickness_safety_factor", 1.20)
    )
    revision_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or str(config.get("name") or DEFAULT_NAME)).strip("_")
    if not revision_name:
        raise ValueError("The revision name is empty after sanitization")
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revision = experiment / "revisions" / revision_name
    if revision.exists():
        backup = experiment / "history" / f"{revision_name}_{time.strftime('%Y%m%d_%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(revision), str(backup))
    revision.mkdir(parents=True)
    config["name"] = revision_name
    write_json_atomic(revision / "mesh_config.json", config)
    started = time.perf_counter()
    report: dict[str, Any] = {
        "schema_version": 2, "experiment_id": EXPERIMENT_ID, "revision": revision_name,
        "status": "GENERATING", "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "configuration": config,
    }
    try:
        report.update(build_2d_mesh(root, revision, config))
        extrusion = extrude_msh2_surface_one_cell(
            revision / "mesh_2d.msh", revision / "mesh_final.msh",
            float(config["geometry"]["chord_m"]) * float(config["geometry"]["spanwise_thickness_chord"]),
        )
        report.update(extrusion)
        declare_boundary_layer_metadata(report, config)
        write_previews(revision / "mesh_2d.msh", revision)
        geometry_for_profile = load_geometry(root, config)
        if check_mesh:
            geometry = geometry_for_profile
            run_openfoam_mesh_checks(
                revision, revision / "mesh_final.msh", report,
                timeout_s=int(config["execution"]["openfoam_timeout_s"]),
                use_temp_workdir=True, profile_points=geometry["open_frame"],
            )
        confirm_measured_boundary_layer_count(report)
        base_name = str(config.get("execution", {}).get("optimization_base_revision") or "")
        if base_name and base_name != revision_name:
            base_report = read_json(
                experiment / "revisions" / base_name / "mesh_report.json", {}
            ) or {}
            if base_report:
                report["optimization_comparison"] = compare_openfoam_quality(
                    base_report, report
                )
        check_status = str(report.get("checkMesh_status", "NOT_RUN"))
        if check_status == "OK":
            report["status"] = "READY"
        elif check_status == "NOT_RUN":
            report["status"] = (
                "CHECKMESH_NOT_RUN" if check_mesh else "MESH_GENERATED_UNCHECKED"
            )
        else:
            report["status"] = "QUALITY_REVIEW_REQUIRED"
    except Exception as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["wall_time_s"] = float(time.perf_counter() - started)
        report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json_atomic(revision / "mesh_report.json", report)
        write_json_atomic(experiment / "current.json", {
            "revision": revision_name, "path": str(revision.resolve()), "status": report["status"],
        })
    return report


def approve(root: Path, name: str) -> dict[str, Any]:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revision = experiment / "revisions" / name
    report = read_json(revision / "mesh_report.json", {}) or {}
    if not (revision / "mesh_final.msh").is_file():
        raise FileNotFoundError(f"No generated mesh exists in {revision}")
    if report.get("checkMesh_status") != "OK":
        raise RuntimeError(
            "This revision cannot be approved because checkMesh did not finish with OK. "
            "Review the report and problem-cell VTK sets, correct the mesh, then run checkMesh again."
        )
    approval = {
        "revision": name, "path": str(revision.resolve()),
        "checkMesh_status": report.get("checkMesh_status"),
        "quality_status": report.get("quality_status"),
        "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "manual_review_required": True,
    }
    write_json_atomic(experiment / "approved.json", approval)
    (revision / "MESH_APPROVED.flag").write_text(json.dumps(approval, indent=2) + "\n", encoding="utf-8")
    return approval


def check_existing(root: Path, name: str) -> dict[str, Any]:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revision = experiment / "revisions" / name
    report_path = revision / "mesh_report.json"
    report = read_json(report_path, {}) or {}
    config = read_json(revision / "mesh_config.json", {}) or default_config()
    mesh = revision / "mesh_final.msh"
    if not mesh.is_file():
        raise FileNotFoundError(mesh)
    declare_boundary_layer_metadata(report, config)
    geometry = load_geometry(root, config)
    run_openfoam_mesh_checks(
        revision, mesh, report,
        timeout_s=int(config["execution"]["openfoam_timeout_s"]),
        use_temp_workdir=True, profile_points=geometry["open_frame"],
    )
    mesh_2d = revision / "mesh_2d.msh"
    if mesh_2d.is_file():
        report["boundary_layer_mesh_audit"] = audit_boundary_layer_mesh(mesh_2d)
    confirm_measured_boundary_layer_count(report)
    report["status"] = "READY" if report.get("checkMesh_status") == "OK" else "QUALITY_REVIEW_REQUIRED"
    write_json_atomic(report_path, report)
    return report


def run_quality_study(root: Path, name: str) -> dict[str, Any]:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revision = experiment / "revisions" / name
    report_path = revision / "mesh_report.json"
    report = read_json(report_path, {}) or {}
    mesh = revision / "mesh_final.msh"
    if not mesh.is_file():
        raise FileNotFoundError(mesh)
    config = read_json(revision / "mesh_config.json", {}) or default_config()
    mesh_2d = revision / "mesh_2d.msh"
    mesh_audit = audit_boundary_layer_mesh(mesh_2d) if mesh_2d.is_file() else {}
    if mesh_audit:
        report["boundary_layer_mesh_audit"] = mesh_audit
    geometry = config["geometry"]
    boundary = config["boundary_layer"]
    schlichting = first_cell_height_from_yplus(
        target_y_plus=float(boundary["target_y_plus"]),
        reynolds=float(geometry["reynolds"]),
        rho_kg_m3=float(geometry["rho_kg_m3"]),
        mu_pa_s=float(geometry["mu_pa_s"]),
        chord_m=float(geometry["chord_m"]),
    )
    delta = turbulent_flat_plate_delta99(
        chord_m=float(geometry["chord_m"]),
        reynolds_chord=float(geometry["reynolds"]), x_over_chord=1.0,
    )
    layer_report = report.get("boundary_layer", {})
    measured = mesh_audit.get("measured_first_cell_height_m", {})
    measured_thickness = mesh_audit.get("measured_total_thickness_m", {})
    counts = mesh_audit.get("cell_counts_2d", {})
    beta_value = layer_report.get("beta_calculated", boundary.get("beta_coefficient"))
    safety = float(boundary.get("thickness_safety_factor", 1.20))
    basic_characteristics = {
        "y+ objetivo": f"{float(boundary['target_y_plus']):.6g}",
        "y1 Schlichting/CFD-Online [m]": f"{float(schlichting['y1_m']):.9g}",
        "y1 real medido, mediana [m]": f"{float(measured.get('median', math.nan)):.9g}",
        "celdas 2D totales": f"{int(counts.get('total', 0)):,}",
        "quads de capa prismática": f"{int(counts.get('boundary_layer_quads', 0)):,}",
        "triángulos exteriores": f"{int(counts.get('external_triangles', 0)):,}",
        "triángulos interiores": f"{int(counts.get('internal_triangles', 0)):,}",
        "delta99 turbulenta teórica [m]": f"{delta:.9g}",
        "espesor objetivo delta99*FS [m]": f"{delta * safety:.9g}",
        "espesor prismático real mediano [m]": f"{float(measured_thickness.get('median', math.nan)):.9g}",
        "capas reales medianas": f"{float(mesh_audit.get('measured_layer_count', {}).get('median', math.nan)):.6g}",
        "Beta usado": "-" if beta_value is None else f"{float(beta_value):.9g}",
        "factor de seguridad BL": f"{safety:.6g}",
    }
    distributions = generate_quality_distributions(
        mesh,
        revision / "quality_distributions",
        basic_characteristics,
        exact_extrema={
            "non_orthogonality_max": report.get("checkMesh_max_non_orthogonality_deg"),
            "skewness_max": report.get("checkMesh_max_skewness"),
            "interpolation_weight_min": report.get("checkMesh_min_face_interpolation_weight"),
            "volume_ratio_min": report.get("checkMesh_min_face_volume_ratio"),
            "determinant_min": report.get("checkMesh_min_cell_determinant"),
        },
    )
    report["quality_distributions"] = {
        "report": str(revision / "quality_distributions/quality_distributions.json"),
        "tables": distributions["images"],
        "cell_count": distributions["cell_count"],
        "boundary_layer_hex_cells": distributions["boundary_layer_hex_cells"],
        "unstructured_prism_cells": distributions["unstructured_prism_cells"],
        "hotspot_vtk_files": distributions["hotspot_vtk_files"],
        "generated_manually": True,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    confirm_measured_boundary_layer_count(report)
    write_json_atomic(report_path, report)
    return report["quality_distributions"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--generate", action="store_true")
    actions.add_argument("--approve", metavar="REVISION")
    actions.add_argument("--check", metavar="REVISION")
    actions.add_argument("--quality-study", metavar="REVISION")
    actions.add_argument("--defaults", action="store_true")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--check-mesh", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if args.defaults:
        print(json.dumps(default_config(), indent=2))
        return 0
    if args.approve:
        print(json.dumps(approve(root, args.approve), indent=2))
        return 0
    if args.check:
        report = check_existing(root, args.check)
        print(json.dumps(report, indent=2))
        return 0 if report.get("checkMesh_status") == "OK" else 2
    if args.quality_study:
        print(json.dumps(run_quality_study(root, args.quality_study), indent=2))
        return 0
    report = generate(root, args.config, args.name, bool(args.check_mesh))
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") in {"READY", "QUALITY_REVIEW_REQUIRED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
