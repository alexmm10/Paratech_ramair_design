#!/usr/bin/env python3
"""Closed-airfoil companion to the from-scratch experimental Gmsh mesher."""
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

import numpy as np
import pandas as pd

from ramair_2d_mesh_builder import (
    _tangent_continuous_te_cap_points,
    extrude_msh2_surface_one_cell,
    run_openfoam_mesh_checks,
)
from ramair_2d_mesh_quality_distributions import generate_quality_distributions
from boundary_layer_estimates import (
    first_cell_height_from_yplus,
    turbulent_flat_plate_delta99,
)
from ramair_2d_bump_matching import bump_cell_sizes, match_four_segment_bumps
from ramair_2d_split_progression import (
    automatic_split_progression,
    evaluate_manual_split_progression,
    split_polyline_at_x,
)
from ramair_2d_gmsh_experimental import (
    add_extend_field,
    add_smooth_interface_guard,
    compare_openfoam_quality,
    gmsh_quality_summary,
    normalize_geometry_to_total_chord,
)
from ramair_2d_open_experimental_mesh import (
    _clean_polyline,
    _json_default,
    _transfinite_node_bounds,
    audit_boundary_layer_mesh,
    confirm_measured_boundary_layer_count,
    declare_boundary_layer_metadata,
    flat_plate_first_height,
    read_json,
    write_json_atomic,
    write_previews,
)


EXPERIMENT_ID = "closed_reference_from_scratch"
DEFAULT_NAME = "closed_validation_beta75_experimental_v1"


def default_closed_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": DEFAULT_NAME,
        "topology": "closed",
        "geometry": {
            "closed_variant": "reference_uncut_validation_1m",
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
            "leading_edge_nodes": 70,
            "te_cap_nodes": 35,
            "minimum_profile_point_spacing_chord": 1.0e-6,
            "automatic_bump_matching": True,
            "tangential_distribution_method": "four_bumps",
            "split_progression_midpoint_x_chord": 0.50,
            "segment_divisions": {
                "te": 22, "upper": 220, "leading_or_inlet": 120, "lower": 220,
            },
            "manual_bump_coefficients": {
                "te": 1.20, "upper": 0.10, "leading_or_inlet": 1.10, "lower": 0.10,
            },
            "manual_split_progression": {
                "split_divisions": {
                    "upper_leading_or_inlet": 110, "upper_te": 110,
                    "lower_leading_or_inlet": 110, "lower_te": 110,
                },
                "progression_coefficients": {
                    "upper_leading_or_inlet": 1.02, "upper_te": 1.02,
                    "lower_leading_or_inlet": 1.02, "lower_te": 1.02,
                },
            },
            "bump_maximum_growth_ratio": 1.10,
            "bump_maximum_size_percent_chord": 1.00,
            "leading_edge_curvature_fraction": 0.20,
            "te_transition_extension_chord": 0.0,
            "te_segment_early_start_enabled": False,
            "te_segment_start_x_over_c": 0.98,
            "te_geometry_points": 35,
        },
        "external_volume": {
            "domain_radius_chord": 50.0,
            "interface_size_mode": "tangential_match",
            "interface_size_chord": 0.00035,
            "interface_tangential_factor": 0.70,
            "farfield_size_chord": 5.0,
            "radial_growth_rate": 0.13,
            "mesh_algorithm": 6,
            "automatic_extend_enabled": False,
            "extend_distance_max_chord": 50.0,
            "extend_power": 2.0,
            "extend_size_max_chord": 5.0,
            "extend_interface_guard_enabled": True,
            "extend_interface_transition_chord": 0.10,
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


def _merge_config(path: Path | None) -> dict[str, Any]:
    config = default_closed_config()
    supplied = read_json(path, {}) if path is not None else {}
    if path is not None and not path.is_file():
        raise FileNotFoundError(path)
    for key, value in (supplied or {}).items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    config["boundary_layer"].pop("beta_coefficient", None)
    config["boundary_layer"].pop("total_thickness_chord", None)
    return config


def load_closed_geometry(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    variant = str(config["geometry"]["closed_variant"])
    path = root / "CFD_2D/CFD_2D_inputs/geometry" / variant / "profile_points.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing closed experimental profile: {path}")
    frame = pd.read_csv(path)
    chord = float(config["geometry"].get("chord_m", 1.0))
    tolerance = max(
        1.0e-12,
        chord * float(config["boundary_layer"].get("minimum_profile_point_spacing_chord", 1.0e-6)),
    )

    def branch(name: str) -> np.ndarray:
        rows = frame[frame["source_section"] == name].sort_values("source_order")
        return _clean_polyline(rows[["x_m", "z_m"]].to_numpy(dtype=float), tolerance)

    upper = branch("UPPER")
    lower = branch("LOWER")
    if len(upper) < 8 or len(lower) < 8:
        raise ValueError("Closed profile branches are under-resolved")
    # Detect the full high-curvature LE neighborhood on the continuous source
    # contour.  The old fixed eight-point split started too close to xmin and
    # left part of the actual nose curvature in the body splines.
    contour = np.vstack([upper, lower[1:]])
    previous = contour[1:-1] - contour[:-2]
    following = contour[2:] - contour[1:-1]
    denominator = np.linalg.norm(previous, axis=1) * np.linalg.norm(following, axis=1)
    angles = np.arccos(np.clip(
        np.sum(previous * following, axis=1) / np.maximum(denominator, 1.0e-30),
        -1.0, 1.0,
    ))
    curvature = np.zeros(len(contour), dtype=float)
    curvature[1:-1] = angles / np.maximum(
        0.5 * (np.linalg.norm(previous, axis=1) + np.linalg.norm(following, axis=1)),
        1.0e-30,
    )
    smoothed = np.convolve(curvature, np.ones(5, dtype=float) / 5.0, mode="same")
    le_index = int(np.argmin(contour[:, 0]))
    peak_window = range(max(1, le_index - 12), min(len(contour) - 1, le_index + 13))
    peak = max(peak_window, key=lambda index: smoothed[index])
    fraction = min(0.95, max(0.02, float(
        config["boundary_layer"].get("leading_edge_curvature_fraction", 0.20)
    )))
    threshold = fraction * smoothed[peak]

    def find_limit(start: int, direction: int) -> int:
        index = start
        while 2 < index < len(contour) - 3:
            check = [index + direction * offset for offset in range(3)]
            if all(0 <= item < len(smoothed) and smoothed[item] < threshold for item in check):
                break
            index += direction
        return index

    upper_limit = find_limit(peak, -1)
    lower_limit = find_limit(peak, 1)
    lower_local = lower_limit - (len(upper) - 1)
    if not (2 <= upper_limit < len(upper) - 2 and 2 <= lower_local < len(lower) - 2):
        raise ValueError("Curvature-based LE segmentation did not produce valid branch limits")
    te_extension = max(0.0, float(
        config["boundary_layer"].get("te_transition_extension_chord", 0.0)
    )) * chord
    upper_anchor = 0
    lower_anchor = len(lower) - 1
    early_start = bool(config["boundary_layer"].get("te_segment_early_start_enabled", False))
    if early_start:
        start_x = chord * min(0.999, max(0.50, float(
            config["boundary_layer"].get("te_segment_start_x_over_c", 0.98)
        )))
        upper_anchor = min(upper_limit - 2, max(1, int(np.argmin(np.abs(upper[:, 0] - start_x)))))
        lower_anchor = max(lower_local + 2, min(len(lower) - 2, int(np.argmin(np.abs(lower[:, 0] - start_x)))))
    elif te_extension > 0.0:
        upper_arc = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(upper, axis=0), axis=1))))
        lower_from_te = np.concatenate((
            [0.0], np.cumsum(np.linalg.norm(np.diff(lower[::-1], axis=0), axis=1))
        ))
        upper_anchor = min(upper_limit - 2, max(1, int(np.searchsorted(upper_arc, te_extension))))
        lower_count = min(len(lower) - lower_local - 2, max(1, int(np.searchsorted(lower_from_te, te_extension))))
        lower_anchor = len(lower) - 1 - lower_count
    upper_body = upper[upper_anchor : upper_limit + 1]
    le_curve = contour[upper_limit : lower_limit + 1]
    lower_body = lower[lower_local : lower_anchor + 1]
    cap_internal, cap_info = _tangent_continuous_te_cap_points(
        lower[-1], upper[0], lower[-2], upper[1], chord,
        max(5, int(config["boundary_layer"].get("te_geometry_points", 35)) - 2),
    )
    te_cap = np.vstack([
        lower[lower_anchor:],
        cap_internal,
        upper[: upper_anchor + 1],
    ])
    normalized, chord_report = normalize_geometry_to_total_chord(
        {
            "upper_body": upper_body,
            "leading_edge": le_curve,
            "lower_body": lower_body,
            "te_cap": te_cap,
            "full_upper": upper,
            "full_lower": lower,
            "full_cap": np.vstack([lower[-1], cap_internal, upper[0]]),
        },
        chord=chord,
        reference_groups=("full_upper", "full_lower", "full_cap"),
    )
    upper_body = normalized["upper_body"]
    le_curve = normalized["leading_edge"]
    lower_body = normalized["lower_body"]
    te_cap = normalized["te_cap"]
    return {
        "frame": frame,
        "variant": variant,
        "chord": chord,
        "upper_body": upper_body,
        "leading_edge": le_curve,
        "lower_body": lower_body,
        "te_cap": te_cap,
        "te_cap_info": cap_info,
        "identity": {
            "source": str(path),
            "upper_points": len(upper),
            "lower_points": len(lower),
            "single_closed_wall": True,
            "fluid_inside_airfoil": False,
            "leading_edge_segmentation": {
                "method": "smoothed_curvature_fraction",
                "fraction": fraction,
                "peak_index": peak,
                "upper_limit_index": upper_limit,
                "lower_limit_index": lower_local,
                "peak_curvature_1_m": float(smoothed[peak]),
            },
            "te_transition_extension": {
                "requested_chord": te_extension / chord,
                "upper_anchor_index": upper_anchor,
                "lower_anchor_index": lower_anchor,
                "strategy": "include_straight_approach_in_TE_Bump_segment",
                "early_start_enabled": early_start,
                "start_x_over_c": float(config["boundary_layer"].get(
                    "te_segment_start_x_over_c", 0.98
                )) if early_start else None,
            },
            "total_chord_normalization": chord_report,
        },
    }


def build_2d_mesh(root: Path, revision: Path, config: dict[str, Any]) -> dict[str, Any]:
    try:
        import gmsh
    except ImportError as exc:
        raise RuntimeError("The Gmsh Python API is required") from exc
    geometry = load_closed_geometry(root, config)
    chord = float(geometry["chord"])
    layer = flat_plate_first_height(config)
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 1)
    threads = int(config["execution"].get("gmsh_threads", 12))
    for option in ("General.NumThreads", "Mesh.MaxNumThreads1D", "Mesh.MaxNumThreads2D"):
        gmsh.option.setNumber(option, threads)
    gmsh.option.setNumber("Mesh.Algorithm", int(config["external_volume"].get("mesh_algorithm", 6)))
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    gmsh.option.setNumber("Mesh.Binary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMax", chord * float(config["external_volume"]["farfield_size_chord"]))
    gmsh.option.setNumber(
        "Mesh.Smoothing", int(config.get("execution", {}).get("mesh_smoothing", 1))
    )
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 1)
    gmsh.option.setNumber("Mesh.RecombineAll", 0)
    gmsh.logger.start()
    try:
        gmsh.model.add(EXPERIMENT_ID)
        geo = gmsh.model.geo
        point_cache: dict[tuple[float, float], int] = {}

        def point_tag(point: np.ndarray) -> int:
            key = (round(float(point[0]), 14), round(float(point[1]), 14))
            if key not in point_cache:
                point_cache[key] = geo.addPoint(float(point[0]), float(point[1]), 0.0)
            return point_cache[key]

        base_polylines = [
            ("upper_body", geometry["upper_body"]),
            ("leading_edge", geometry["leading_edge"]),
            ("lower_body", geometry["lower_body"]),
            ("te_cap", geometry["te_cap"]),
        ]
        method = str(config["boundary_layer"].get(
            "tangential_distribution_method", "four_bumps"
        ))
        if method not in {"four_bumps", "bump_split_progression"}:
            raise ValueError(f"Unsupported tangential distribution method: {method}")
        split_report: dict[str, Any] | None = None
        split_geometry: dict[str, Any] | None = None
        if method == "bump_split_progression":
            midpoint_x = chord * float(config["boundary_layer"].get(
                "split_progression_midpoint_x_chord", 0.50
            ))
            upper_te, upper_le_mid, upper_split = split_polyline_at_x(
                geometry["upper_body"], midpoint_x
            )
            lower_le_mid, lower_te, lower_split = split_polyline_at_x(
                geometry["lower_body"], midpoint_x
            )
            # Every Progression entity is intrinsically oriented fine -> midpoint.
            polylines = [
                ("upper_te", upper_te, 1),
                ("upper_leading_or_inlet", upper_le_mid[::-1], -1),
                ("leading_edge", geometry["leading_edge"], 1),
                ("lower_leading_or_inlet", lower_le_mid, 1),
                ("lower_te", lower_te[::-1], -1),
                ("te_cap", geometry["te_cap"], 1),
            ]
            split_geometry = {"upper": upper_split, "lower": lower_split}
        else:
            polylines = [(label, points, 1) for label, points in base_polylines]
        curves: list[int] = []
        loop_curves: list[int] = []
        curve_report: list[dict[str, Any]] = []
        lengths = [
            float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
            for _, points, _ in polylines
        ]
        total_body_length = max(sum(
            length for (label, _, _), length in zip(polylines, lengths)
            if label not in {"leading_edge", "te_cap"}
        ), 1.0e-15)
        target_nodes = int(config["boundary_layer"].get("wall_nodes_target", 1200))
        minimum_size = float(config["boundary_layer"].get("minimum_tangential_size_chord", 0.00030))
        maximum_size = float(config["boundary_layer"].get("maximum_tangential_size_chord", 0.0050))
        bump = float(config["boundary_layer"].get("wall_bump_coefficient", 0.45))
        automatic_bump = bool(config["boundary_layer"].get("automatic_bump_matching", False))
        automatic_report: dict[str, Any] | None = None
        segment_labels = {
            "te_cap": "te", "upper_body": "upper",
            "leading_edge": "leading_or_inlet", "lower_body": "lower",
        }
        base_lengths = {
            "te": float(np.linalg.norm(np.diff(geometry["te_cap"], axis=0), axis=1).sum()),
            "upper": float(np.linalg.norm(np.diff(geometry["upper_body"], axis=0), axis=1).sum()),
            "leading_or_inlet": float(np.linalg.norm(np.diff(geometry["leading_edge"], axis=0), axis=1).sum()),
            "lower": float(np.linalg.norm(np.diff(geometry["lower_body"], axis=0), axis=1).sum()),
        }
        if automatic_bump:
            automatic_report = match_four_segment_bumps(
                base_lengths,
                {
                    name: int(value)
                    for name, value in dict(config["boundary_layer"].get("segment_divisions") or {}).items()
                },
                chord=chord,
                maximum_growth_ratio=float(config["boundary_layer"].get("bump_maximum_growth_ratio", 1.10)),
                maximum_size_percent_chord=float(
                    config["boundary_layer"].get("bump_maximum_size_percent_chord", 1.00)
                ),
            )
        if method == "bump_split_progression":
            body_divisions = dict(config["boundary_layer"].get("segment_divisions") or {})
            curved_divisions = {
                "leading_or_inlet": int(body_divisions["leading_or_inlet"]),
                "te": int(body_divisions["te"]),
            }
            curved_bumps = (
                {
                    name: float(automatic_report["coefficients"][name])
                    for name in ("leading_or_inlet", "te")
                }
                if automatic_report is not None else {
                    name: float(dict(config["boundary_layer"].get(
                        "manual_bump_coefficients") or {})[name])
                    for name in ("leading_or_inlet", "te")
                }
            )
            half_lengths = {
                side: {
                    end: next(
                        length for (label, _, _), length in zip(polylines, lengths)
                        if label == f"{side}_{end}"
                    )
                    for end in ("leading_or_inlet", "te")
                }
                for side in ("upper", "lower")
            }
            if automatic_bump:
                split_report = automatic_split_progression(
                    half_lengths=half_lengths,
                    body_divisions={
                        "upper": int(body_divisions["upper"]),
                        "lower": int(body_divisions["lower"]),
                    },
                    curved_lengths={name: base_lengths[name] for name in curved_divisions},
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
                        curved_bumps[name], base_lengths[name], curved_divisions[name]
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
        for (label, points, loop_sign), length in zip(polylines, lengths):
            ids = [point_tag(point) for point in points]
            curve = geo.addSpline(ids)
            lower_bound, upper_bound = _transfinite_node_bounds(
                length, chord, minimum_size, maximum_size,
            )
            if split_report is not None and label in {
                "upper_te", "upper_leading_or_inlet",
                "lower_te", "lower_leading_or_inlet",
            }:
                key = label
                divisions = int(split_report["split_divisions"][key])
                selected_progression = float(
                    split_report["progression_coefficients"][key]
                )
                nodes = divisions + 1
                geo.mesh.setTransfiniteCurve(
                    curve, nodes, "Progression", selected_progression
                )
                requested = nodes
                distribution = f"progression_fine_to_mid_{selected_progression:.8g}"
            elif split_report is not None and label in {"leading_edge", "te_cap"}:
                segment = segment_labels[label]
                nodes = int(config["boundary_layer"]["segment_divisions"][segment]) + 1
                selected_bump = float(split_report["curved_bump_coefficients"][segment])
                geo.mesh.setTransfiniteCurve(curve, nodes, "Bump", selected_bump)
                requested = nodes
                distribution = f"bump_{selected_bump:.8g}"
            elif automatic_report is not None:
                segment = segment_labels[label]
                divisions = int(automatic_report["divisions"][segment])
                nodes = divisions + 1
                selected_bump = float(automatic_report["coefficients"][segment])
                geo.mesh.setTransfiniteCurve(curve, nodes, "Bump", selected_bump)
                requested = nodes
                distribution = f"automatic_bump_{selected_bump:.8g}"
            elif config["boundary_layer"].get("manual_bump_coefficients"):
                segment = segment_labels[label]
                manual_divisions = dict(
                    config["boundary_layer"].get("segment_divisions") or {}
                )
                manual_coefficients = dict(
                    config["boundary_layer"].get("manual_bump_coefficients") or {}
                )
                fallback_divisions = {
                    "te": 22, "upper": 220, "leading_or_inlet": 120, "lower": 220,
                }
                nodes = int(manual_divisions.get(segment, fallback_divisions[segment])) + 1
                selected_bump = float(manual_coefficients.get(segment, bump))
                geo.mesh.setTransfiniteCurve(curve, nodes, "Bump", selected_bump)
                requested = nodes
                distribution = f"manual_bump_{selected_bump:.8g}"
            elif label == "te_cap":
                requested = int(config["boundary_layer"].get("te_cap_nodes", 35))
                nodes = max(4, lower_bound, min(upper_bound, requested))
                geo.mesh.setTransfiniteCurve(curve, nodes)
                distribution = "uniform_explicit_TE"
            elif label == "leading_edge":
                requested = int(config["boundary_layer"].get("leading_edge_nodes", 70))
                nodes = max(8, lower_bound, min(upper_bound, requested))
                geo.mesh.setTransfiniteCurve(curve, nodes)
                distribution = "uniform_explicit_LE"
            else:
                requested = max(40, int(round(target_nodes * length / total_body_length)))
                nodes = max(lower_bound, min(upper_bound, requested))
                geo.mesh.setTransfiniteCurve(curve, nodes, "Bump", bump)
                distribution = f"bump_{bump:.6g}_toward_LE_and_TE"
            curves.append(curve)
            loop_curves.append(loop_sign * curve)
            curve_report.append({
                "label": label, "curve": curve, "length_m": length,
                "nodes": nodes, "requested_nodes": requested,
                "distribution": distribution,
            })

        radius = chord * float(config["external_volume"]["domain_radius_chord"])
        center_x = 0.25 * chord
        center = geo.addPoint(center_x, 0.0, 0.0)
        far_points = [
            geo.addPoint(center_x + radius, 0.0, 0.0),
            geo.addPoint(center_x, radius, 0.0),
            geo.addPoint(center_x - radius, 0.0, 0.0),
            geo.addPoint(center_x, -radius, 0.0),
        ]
        far_curves = [
            geo.addCircleArc(far_points[index], center, far_points[(index + 1) % 4])
            for index in range(4)
        ]
        outer_loop = geo.addCurveLoop(far_curves)
        wall_loop = geo.addCurveLoop(loop_curves)
        fluid_surface = geo.addPlaneSurface([outer_loop, wall_loop])
        geo.synchronize()
        wall_group = gmsh.model.addPhysicalGroup(1, curves)
        gmsh.model.setPhysicalName(1, wall_group, "airfoil_wall")
        far_group = gmsh.model.addPhysicalGroup(1, far_curves)
        gmsh.model.setPhysicalName(1, far_group, "farfield")
        fluid_group = gmsh.model.addPhysicalGroup(2, [fluid_surface])
        gmsh.model.setPhysicalName(2, fluid_group, "fluid")

        mean_spacings = [item["length_m"] / max(1, item["nodes"] - 1) for item in curve_report]
        tangential_reference = min(mean_spacings)
        external = config["external_volume"]
        interface_size = (
            tangential_reference * float(external.get("interface_tangential_factor", 0.70))
            if str(external.get("interface_size_mode", "tangential_match")) == "tangential_match"
            else chord * float(external.get("interface_size_chord", 0.00035))
        )
        boundary = gmsh.model.mesh.field.add("BoundaryLayer")
        gmsh.model.mesh.field.setNumbers(boundary, "CurvesList", curves)
        gmsh.model.mesh.field.setNumber(boundary, "Size", float(layer["first_cell_height_m"]))
        gmsh.model.mesh.field.setNumber(boundary, "Thickness", float(layer["total_thickness_m"]))
        gmsh.model.mesh.field.setNumber(boundary, "SizeFar", interface_size)
        if layer["distribution_mode"] == "beta_law":
            gmsh.model.mesh.field.setNumber(boundary, "BetaLaw", 1)
            gmsh.model.mesh.field.setNumber(boundary, "Beta", float(layer["beta_calculated"]))
            gmsh.model.mesh.field.setNumber(boundary, "NbLayers", int(layer["layers"]))
        else:
            gmsh.model.mesh.field.setNumber(boundary, "Ratio", float(layer["growth_rate"]))
        gmsh.model.mesh.field.setNumber(boundary, "Quads", 1)
        gmsh.model.mesh.field.setAsBoundaryLayer(boundary)

        far_size = chord * float(external["farfield_size_chord"])
        growth = min(0.50, max(0.005, float(external.get("radial_growth_rate", 0.13))))
        external_extend = bool(external.get("automatic_extend_enabled", False))
        if external_extend:
            size_law = add_extend_field(
                gmsh,
                surfaces=[fluid_surface],
                curves=curves,
                dist_max=chord * float(external.get(
                    "extend_distance_max_chord", external["domain_radius_chord"]
                )),
                power=float(external.get("extend_power", 2.0)),
                size_max=chord * float(external.get(
                    "extend_size_max_chord", external["farfield_size_chord"]
                )),
            )
            if bool(external.get("extend_interface_guard_enabled", True)):
                guard = add_smooth_interface_guard(
                    gmsh,
                    surfaces=[fluid_surface],
                    curves=curves,
                    size_at_interface=interface_size,
                    size_far=far_size,
                    boundary_layer_thickness=float(layer["total_thickness_m"]),
                    transition_distance=chord * float(external.get(
                        "extend_interface_transition_chord", 0.10
                    )),
                )
                combined = gmsh.model.mesh.field.add("Min")
                gmsh.model.mesh.field.setNumbers(combined, "FieldsList", [size_law, guard])
                size_law = combined
            external_size_report = {
                "type": "gmsh_extend_from_variable_geometric_boundary",
                "distance_max_chord": float(external.get(
                    "extend_distance_max_chord", external["domain_radius_chord"]
                )),
                "power": float(external.get("extend_power", 2.0)),
                "size_max_chord": float(external.get(
                    "extend_size_max_chord", external["farfield_size_chord"]
                )),
                "source": "actual transfinite Bump sizes on the four airfoil curves",
                "requested_start": "generated outer boundary-layer front",
                "actual_start": "wall curves carrying the identical tangential column discretization",
                "surface_scope": "external fluid surface only",
                "farfield_reach": (
                    "At and beyond DistMax the field reaches SizeMax; the circular farfield "
                    "therefore remains governed by the configured farfield target."
                ),
                "limitation": (
                    "The generated outer BL row does not exist when fields are evaluated; "
                    "the transfinite source preserves the same column widths without changing topology."
                ),
                "interface_guard": {
                    "enabled": bool(external.get("extend_interface_guard_enabled", True)),
                    "size_chord": interface_size / chord,
                    "starts_at_bl_thickness_chord": float(layer["total_thickness_m"]) / chord,
                    "transition_distance_chord": float(external.get(
                        "extend_interface_transition_chord", 0.10
                    )),
                },
            }
        else:
            distance = gmsh.model.mesh.field.add("Distance")
            gmsh.model.mesh.field.setNumbers(distance, "CurvesList", curves)
            gmsh.model.mesh.field.setNumber(distance, "Sampling", 1200)
            size_law = gmsh.model.mesh.field.add("MathEval")
            gmsh.model.mesh.field.setString(
                size_law, "F", f"Min({far_size:.16g}, {interface_size:.16g} + {growth:.16g}*F{distance})",
            )
            external_size_report = {
                "type": "linear_distance_capped",
                "interface_size_chord": interface_size / chord,
                "tangential_reference_chord": tangential_reference / chord,
                "farfield_size_chord": far_size / chord,
                "maximum_local_growth_fraction": growth,
            }
        gmsh.model.mesh.field.setAsBackgroundMesh(size_law)
        generation_started = time.perf_counter()
        if automatic_report is not None or split_report is not None:
            gmsh.model.mesh.generate(1)
            real_segments: dict[str, Any] = {}
            source_polylines = {label: points for label, points, _ in polylines}
            for item in curve_report:
                node_tags, coordinates, _ = gmsh.model.mesh.getNodes(1, int(item["curve"]), True)
                xyz = np.asarray(coordinates, dtype=float).reshape((-1, 3))[:, :2]
                # getNodes does not guarantee curve order; project on the
                # source polyline through nearest cumulative arc coordinate.
                source = source_polylines[item["label"]]
                arc = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(source, axis=0), axis=1))))
                projected = []
                for point in xyz:
                    nearest = int(np.argmin(np.linalg.norm(source - point, axis=1)))
                    projected.append(float(arc[nearest]))
                ordered = xyz[np.argsort(projected)]
                sizes = np.linalg.norm(np.diff(ordered, axis=0), axis=1)
                real_segments[item["label"]] = {
                    "nodes_read": int(len(node_tags)),
                    "minimum_chord_m": float(np.min(sizes)) if len(sizes) else None,
                    "maximum_chord_m": float(np.max(sizes)) if len(sizes) else None,
                }
            if split_report is not None:
                split_report["gmsh_1d_verification"] = real_segments
            elif automatic_report is not None:
                automatic_report["gmsh_1d_verification"] = real_segments
        gmsh.model.mesh.generate(2)
        optimization = str(config.get("execution", {}).get("post_generation_optimization", "off"))
        optimization_iterations = max(
            1, int(config["execution"].get("post_generation_optimization_iterations", 5))
        )
        if optimization in {"laplace2d", "laplace2d_then_relocate2d"}:
            gmsh.model.mesh.optimize("Laplace2D", niter=optimization_iterations)
        if optimization in {"relocate2d", "laplace2d_then_relocate2d"}:
            gmsh.model.mesh.optimize("Relocate2D", niter=optimization_iterations)
        mesh_2d = revision / "mesh_2d.msh"
        gmsh.write(str(mesh_2d))
        measured = audit_boundary_layer_mesh(mesh_2d)
        node_tags, _, _ = gmsh.model.mesh.getNodes()
        element_types, element_tags, _ = gmsh.model.mesh.getElements(2)
        all_elements = np.concatenate(element_tags) if element_tags else np.asarray([], dtype=np.int64)
        quality = np.asarray(
            gmsh.model.mesh.getElementQualities(all_elements.tolist(), "minSICN"), dtype=float,
        ) if len(all_elements) else np.asarray([])
        quality_diagnostics = gmsh_quality_summary(gmsh, all_elements.tolist())
        (revision / "log.gmsh").write_text("\n".join(gmsh.logger.get()) + "\n", encoding="utf-8")
        return {
            "mesh_2d": str(mesh_2d),
            "nodes_2d": int(len(node_tags)),
            "elements_2d_by_gmsh_type": {
                str(kind): int(len(tags)) for kind, tags in zip(element_types, element_tags)
            },
            "minimum_gmsh_minSICN": float(np.min(quality)) if len(quality) else None,
            "mean_gmsh_minSICN": float(np.mean(quality)) if len(quality) else None,
            "gmsh_quality_diagnostics": quality_diagnostics,
            "gmsh_generation_and_optimization_time_s": time.perf_counter() - generation_started,
            "boundary_layer": layer,
            "boundary_layer_mesh_audit": measured,
            "wall_curves": curve_report,
            "automatic_bump_matching": automatic_report,
            "split_progression_matching": split_report,
            "split_progression_geometry": split_geometry,
            "post_generation_optimization": optimization,
            "geometry_identity": geometry["identity"],
            "te_cap": geometry["te_cap_info"],
            "external_size_law": external_size_report,
            "physical_groups": ["airfoil_wall", "farfield", "fluid"],
        }
    finally:
        try:
            gmsh.logger.stop()
        except Exception:
            pass
        gmsh.finalize()


def generate(root: Path, config_path: Path | None, name: str | None, check_mesh: bool) -> dict[str, Any]:
    config = _merge_config(config_path)
    revision_name = re.sub(
        r"[^A-Za-z0-9_.-]+", "_", name or str(config.get("name") or DEFAULT_NAME),
    ).strip("_")
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revision = experiment / "revisions" / revision_name
    if revision.exists():
        history = experiment / "history" / f"{revision_name}_{time.strftime('%Y%m%d_%H%M%S')}"
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(revision), str(history))
    revision.mkdir(parents=True)
    config["name"] = revision_name
    write_json_atomic(revision / "mesh_config.json", config)
    report: dict[str, Any] = {
        "schema_version": 1, "experiment_id": EXPERIMENT_ID,
        "revision": revision_name, "status": "GENERATING",
        "configuration": config,
    }
    started = time.perf_counter()
    try:
        report.update(build_2d_mesh(root, revision, config))
        extrusion = extrude_msh2_surface_one_cell(
            revision / "mesh_2d.msh", revision / "mesh_final.msh",
            float(config["geometry"]["chord_m"]) * float(config["geometry"]["spanwise_thickness_chord"]),
        )
        report.update(extrusion)
        declare_boundary_layer_metadata(report, config)
        write_previews(revision / "mesh_2d.msh", revision)
        if check_mesh:
            geometry = load_closed_geometry(root, config)
            run_openfoam_mesh_checks(
                revision, revision / "mesh_final.msh", report,
                timeout_s=int(config["execution"]["openfoam_timeout_s"]),
                use_temp_workdir=True, profile_points=geometry["frame"],
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
        report["status"] = (
            "READY" if str(report.get("checkMesh_status")) == "OK"
            else "MESH_GENERATED_UNCHECKED" if not check_mesh
            else "QUALITY_REVIEW_REQUIRED"
        )
    except Exception as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["wall_time_s"] = float(time.perf_counter() - started)
        write_json_atomic(revision / "mesh_report.json", report)
        write_json_atomic(experiment / "current.json", {
            "revision": revision_name, "path": str(revision.resolve()), "status": report["status"],
        })
    return report


def approve(root: Path, name: str) -> dict[str, Any]:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revision = experiment / "revisions" / name
    report = read_json(revision / "mesh_report.json", {}) or {}
    if str(report.get("checkMesh_status")) != "OK":
        raise RuntimeError("Only a closed experimental mesh with checkMesh OK can be approved")
    payload = {"status": "APPROVED", "revision": name, "approved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    write_json_atomic(revision / "approval.json", payload)
    write_json_atomic(experiment / "approved.json", payload)
    return payload


def quality_study(root: Path, name: str) -> dict[str, Any]:
    revision = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID / "revisions" / name
    report = read_json(revision / "mesh_report.json", {}) or {}
    if str(report.get("checkMesh_status")) != "OK":
        raise RuntimeError("Quality distributions require checkMesh OK")
    config = read_json(revision / "mesh_config.json", {}) or default_closed_config()
    geometry = dict(config.get("geometry") or {})
    boundary = dict(config.get("boundary_layer") or {})
    mesh_2d = revision / "mesh_2d.msh"
    mesh_audit = audit_boundary_layer_mesh(mesh_2d) if mesh_2d.is_file() else {}
    schlichting = first_cell_height_from_yplus(
        target_y_plus=float(boundary.get("target_y_plus", 1.0)),
        reynolds=float(geometry.get("reynolds", 1.9e6)),
        rho_kg_m3=float(geometry.get("rho_kg_m3", 0.66606662)),
        mu_pa_s=float(geometry.get("mu_pa_s", 1.7894e-5)),
        chord_m=float(geometry.get("chord_m", 1.0)),
    )
    delta = turbulent_flat_plate_delta99(
        chord_m=float(geometry.get("chord_m", 1.0)),
        reynolds_chord=float(geometry.get("reynolds", 1.9e6)),
        x_over_chord=1.0,
    )
    counts = dict(mesh_audit.get("cell_counts_2d") or {})
    measured = dict(mesh_audit.get("measured_first_cell_height_m") or {})
    measured_thickness = dict(mesh_audit.get("measured_total_thickness_m") or {})
    layer_report = dict(report.get("boundary_layer") or {})
    safety = float(boundary.get("thickness_safety_factor", 1.20))
    beta_value = layer_report.get("beta_calculated", boundary.get("beta_coefficient"))
    external_triangle_count = int(
        (counts.get("external_triangles", 0) or 0)
        + (counts.get("other_triangles", 0) or 0)
    )
    basic_characteristics = {
        "topologia": "closed",
        "y+ objetivo": f"{float(boundary.get('target_y_plus', math.nan)):.6g}",
        "y1 Schlichting/CFD-Online [m]": f"{float(schlichting['y1_m']):.9g}",
        "y1 real medido, mediana [m]": f"{float(measured.get('median', math.nan)):.9g}",
        "celdas 2D totales": f"{int(counts.get('total', report.get('checkMesh_cell_count', 0)) or 0):,}",
        "quads de capa prismatica": f"{int(counts.get('boundary_layer_quads', 0) or 0):,}",
        # The closed topology has no internal fluid surface.  Gmsh MSH2 can
        # leave the external triangular surface without the open-mesh
        # physical split, in which case the shared audit calls it ``other``.
        "triangulos exteriores": f"{external_triangle_count:,}",
        "delta99 turbulenta teorica [m]": f"{delta:.9g}",
        "espesor objetivo delta99*FS [m]": f"{delta * safety:.9g}",
        "espesor prismatico real mediano [m]": f"{float(measured_thickness.get('median', math.nan)):.9g}",
        "capas reales medianas": f"{float(mesh_audit.get('measured_layer_count', {}).get('median', math.nan)):.6g}",
        "Beta usado": "-" if beta_value is None else f"{float(beta_value):.9g}",
        "factor de seguridad BL": f"{safety:.6g}",
        "checkMesh": str(report.get("checkMesh_status", "NOT_RUN")),
    }
    result = generate_quality_distributions(
        mesh_path=revision / "mesh_final.msh",
        output_dir=revision / "quality_distributions",
        basic_characteristics=basic_characteristics,
        exact_extrema={
            "non_orthogonality_max": report.get("checkMesh_max_non_orthogonality_deg"),
            "skewness_max": report.get("checkMesh_max_skewness"),
            "interpolation_weight_min": report.get("checkMesh_min_face_interpolation_weight"),
            "volume_ratio_min": report.get("checkMesh_min_face_volume_ratio"),
            "determinant_min": report.get("checkMesh_min_cell_determinant"),
        },
    )
    report["quality_distributions"] = result
    write_json_atomic(revision / "mesh_report.json", report)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--approve")
    parser.add_argument("--quality-study")
    parser.add_argument("--check-mesh", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.approve:
        result = approve(args.project_root.resolve(), args.approve)
    elif args.quality_study:
        result = quality_study(args.project_root.resolve(), args.quality_study)
    elif args.generate:
        result = generate(
            args.project_root.resolve(), args.config.resolve() if args.config else None,
            args.name, bool(args.check_mesh),
        )
    else:
        result = {"default_config": default_closed_config()}
    print(json.dumps(result, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
