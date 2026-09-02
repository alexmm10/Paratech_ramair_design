#!/usr/bin/env python3
"""Shared, opt-in Gmsh controls for the experimental 2D meshers.

The helpers in this module are deliberately independent from airfoil topology:
when a feature is disabled the caller keeps its original sizing field and mesh
workflow unchanged.
"""
from __future__ import annotations

import math
import time
from typing import Any, Iterable

import numpy as np


QUALITY_MEASURES = ("minDetJac", "minSIGE", "minSICN")


def normalize_geometry_to_total_chord(
    point_groups: dict[str, np.ndarray],
    *,
    chord: float,
    reference_groups: Iterable[str],
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Apply one similarity transform so the complete rounded contour is ``chord``.

    The transform is deliberately shared by every supplied group.  In
    particular, an open retained wall and its uncut virtual inlet cannot drift
    apart through two independent normalizations.
    """
    chord = float(chord)
    if not math.isfinite(chord) or chord <= 0.0:
        raise ValueError("The nominal total chord must be finite and positive")
    names = [str(name) for name in reference_groups]
    if not names:
        raise ValueError("At least one reference group is required")
    reference = np.vstack([np.asarray(point_groups[name], dtype=float) for name in names])
    xmin = float(np.min(reference[:, 0]))
    xmax = float(np.max(reference[:, 0]))
    raw_chord = xmax - xmin
    if not math.isfinite(raw_chord) or raw_chord <= 0.0:
        raise ValueError("The rounded reference contour has zero or invalid chord")
    scale = chord / raw_chord
    normalized: dict[str, np.ndarray] = {}
    for name, points in point_groups.items():
        values = np.asarray(points, dtype=float).copy()
        values[:, 0] = (values[:, 0] - xmin) * scale
        values[:, 1] *= scale
        normalized[str(name)] = values
    normalized_reference = np.vstack([normalized[name] for name in names])
    return normalized, {
        "raw_minimum_x_m": xmin,
        "raw_maximum_x_m": xmax,
        "raw_total_chord_m": raw_chord,
        "similarity_scale": scale,
        "normalized_minimum_x_m": float(np.min(normalized_reference[:, 0])),
        "normalized_maximum_x_m": float(np.max(normalized_reference[:, 0])),
        "normalized_total_chord_m": float(np.ptp(normalized_reference[:, 0])),
        "nominal_total_chord_m": chord,
    }


def add_extend_field(
    gmsh: Any,
    *,
    surfaces: Iterable[int],
    curves: Iterable[int],
    dist_max: float,
    power: float,
    size_max: float,
) -> int:
    """Extend the *geometric-boundary* mesh sizes into selected surfaces.

    Gmsh evaluates background fields before the generated BoundaryLayer front
    exists.  Consequently the source is the real transfinite discretization of
    the CAD curves that also generates the BL columns, not a reconstructed or
    imposed uniform interface size.  This preserves the variable Bump spacing
    without changing BL topology.
    """
    dist_max = float(dist_max)
    power = float(power)
    size_max = float(size_max)
    if not math.isfinite(dist_max) or dist_max <= 0.0:
        raise ValueError("Extend DistMax must be finite and positive")
    if not math.isfinite(power) or power <= 0.0:
        raise ValueError("Extend Power must be finite and positive")
    if not math.isfinite(size_max) or size_max <= 0.0:
        raise ValueError("Extend SizeMax must be finite and positive")
    surface_tags = [int(v) for v in surfaces]
    field = gmsh.model.mesh.field.add("Extend")
    gmsh.model.mesh.field.setNumbers(field, "SurfacesList", surface_tags)
    gmsh.model.mesh.field.setNumbers(field, "CurvesList", [int(v) for v in curves])
    gmsh.model.mesh.field.setNumber(field, "DistMax", dist_max)
    gmsh.model.mesh.field.setNumber(field, "Power", power)
    gmsh.model.mesh.field.setNumber(field, "SizeMax", size_max)
    # Extend itself can still return a finite value outside its source
    # surfaces.  Restrict is required before combining independent internal
    # and external fields through Min, otherwise the internal law can refine
    # the whole farfield.
    restricted = gmsh.model.mesh.field.add("Restrict")
    gmsh.model.mesh.field.setNumber(restricted, "InField", field)
    gmsh.model.mesh.field.setNumbers(restricted, "SurfacesList", surface_tags)
    return restricted


def add_smooth_interface_guard(
    gmsh: Any,
    *,
    surfaces: Iterable[int],
    curves: Iterable[int],
    size_at_interface: float,
    size_far: float,
    boundary_layer_thickness: float,
    transition_distance: float,
) -> int:
    """Cap the first unstructured rows with a smooth BL-interface size law."""
    size_at_interface = float(size_at_interface)
    size_far = float(size_far)
    thickness = float(boundary_layer_thickness)
    transition = float(transition_distance)
    if min(size_at_interface, size_far, transition) <= 0.0:
        raise ValueError("Interface-guard sizes and transition must be positive")
    if thickness < 0.0:
        raise ValueError("Boundary-layer thickness cannot be negative")
    distance = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(distance, "CurvesList", [int(v) for v in curves])
    gmsh.model.mesh.field.setNumber(distance, "Sampling", 1200)
    threshold = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
    gmsh.model.mesh.field.setNumber(threshold, "SizeMin", size_at_interface)
    gmsh.model.mesh.field.setNumber(threshold, "SizeMax", size_far)
    gmsh.model.mesh.field.setNumber(threshold, "DistMin", thickness)
    gmsh.model.mesh.field.setNumber(threshold, "DistMax", thickness + transition)
    gmsh.model.mesh.field.setNumber(threshold, "Sigmoid", 1)
    restricted = gmsh.model.mesh.field.add("Restrict")
    gmsh.model.mesh.field.setNumber(restricted, "InField", threshold)
    gmsh.model.mesh.field.setNumbers(
        restricted, "SurfacesList", [int(value) for value in surfaces]
    )
    return restricted


def smooth_inlet_size_profile(
    count: int,
    *,
    wall_size: float,
    inlet_factor: float,
    transition_fraction: float,
) -> list[float]:
    """Symmetric log-smooth y1 profile from both lips to the inlet centre."""
    count = int(count)
    wall_size = float(wall_size)
    inlet_factor = float(inlet_factor)
    transition_fraction = float(transition_fraction)
    if count < 2:
        raise ValueError("At least two inlet points are required")
    if wall_size <= 0.0 or inlet_factor < 1.0:
        raise ValueError("wall_size must be positive and inlet_factor >= 1")
    if not 0.0 < transition_fraction <= 0.5:
        raise ValueError("transition_fraction must be in (0, 0.5]")
    values: list[float] = []
    for index in range(count):
        s = index / max(count - 1, 1)
        xi = min(1.0, min(s, 1.0 - s) / transition_fraction)
        smooth = 3.0 * xi * xi - 2.0 * xi * xi * xi
        values.append(wall_size * math.exp(math.log(inlet_factor) * smooth))
    return values


def gmsh_quality_summary(gmsh: Any, element_tags: Iterable[int]) -> dict[str, Any]:
    """Return robust numerical Gmsh quality metrics and analysis timing."""
    tags = [int(tag) for tag in element_tags]
    started = time.perf_counter()
    measures: dict[str, Any] = {}
    unsupported: dict[str, str] = {}
    for measure in QUALITY_MEASURES:
        try:
            values = np.asarray(
                gmsh.model.mesh.getElementQualities(tags, measure), dtype=float
            )
            finite = values[np.isfinite(values)]
            measures[measure] = {
                "minimum": float(np.min(finite)) if finite.size else None,
                "mean": float(np.mean(finite)) if finite.size else None,
                "p05": float(np.percentile(finite, 5.0)) if finite.size else None,
                "samples": int(finite.size),
            }
        except Exception as exc:  # Gmsh builds expose a slightly different set.
            unsupported[measure] = str(exc)
    plugin_status = "NOT_RUN"
    plugin_error = None
    try:
        gmsh.plugin.run("AnalyseMeshQuality")
        plugin_status = "RUN"
    except Exception as exc:
        plugin_status = "UNAVAILABLE"
        plugin_error = str(exc)
    return {
        "element_count": len(tags),
        "measures": measures,
        "unsupported_measures": unsupported,
        "analyse_mesh_quality_plugin": plugin_status,
        "analyse_mesh_quality_plugin_error": plugin_error,
        "analysis_time_s": time.perf_counter() - started,
        "role": "geometric diagnostic; native OpenFOAM checkMesh remains authoritative",
    }


def normalized_openfoam_risk(
    value: float | None,
    threshold: float,
    *,
    higher_is_better: bool,
) -> float:
    """Risk definition requested for BASE/candidate mesh comparisons."""
    if value is None or not math.isfinite(float(value)):
        return 3.0
    x = float(value)
    threshold = float(threshold)
    comfortable = 2.0 * threshold if higher_is_better else 0.5 * threshold
    denominator = comfortable - threshold if higher_is_better else threshold - comfortable
    raw = (
        (comfortable - x) / denominator
        if higher_is_better
        else (x - comfortable) / denominator
    )
    return float(np.clip(raw, 0.0, 3.0))


def compare_openfoam_quality(
    base: dict[str, Any],
    candidate: dict[str, Any],
    *,
    tolerance: float = 1.0e-6,
) -> dict[str, Any]:
    """Compare two checkMesh reports without ever replacing the base silently."""
    specifications = (
        ("determinant", "checkMesh_min_cell_determinant", 0.001, True, 0.35),
        ("interpolation_weight", "checkMesh_min_face_interpolation_weight", 0.05, True, 0.25),
        ("volume_ratio", "checkMesh_min_face_volume_ratio", 0.01, True, 0.20),
        ("non_orthogonality", "checkMesh_max_non_orthogonality_deg", 70.0, False, 0.10),
        ("skewness", "checkMesh_max_skewness", 4.0, False, 0.10),
    )
    rows: list[dict[str, Any]] = []
    numerator = 0.0
    denominator = 0.0
    newly_failed: list[str] = []
    for name, key, threshold, higher, priority in specifications:
        base_value = base.get(key)
        candidate_value = candidate.get(key)
        base_risk = normalized_openfoam_risk(base_value, threshold, higher_is_better=higher)
        candidate_risk = normalized_openfoam_risk(candidate_value, threshold, higher_is_better=higher)
        weight = priority * (1.0 + 2.0 * base_risk)
        numerator += weight * (base_risk - candidate_risk)
        denominator += weight
        base_pass = (
            float(base_value) >= threshold if higher and base_value is not None
            else float(base_value) <= threshold if base_value is not None else False
        )
        candidate_pass = (
            float(candidate_value) >= threshold if higher and candidate_value is not None
            else float(candidate_value) <= threshold if candidate_value is not None else False
        )
        if base_pass and not candidate_pass:
            newly_failed.append(name)
        rows.append({
            "metric": name,
            "base": base_value,
            "candidate": candidate_value,
            "delta": (
                float(candidate_value) - float(base_value)
                if base_value is not None and candidate_value is not None else None
            ),
            "threshold": threshold,
            "base_pass": base_pass,
            "candidate_pass": candidate_pass,
            "base_risk": base_risk,
            "candidate_risk": candidate_risk,
            "weight": weight,
        })
    score = numerator / max(denominator, 1.0e-30)
    base_ok = str(base.get("checkMesh_status") or "").upper() == "OK"
    candidate_ok = str(candidate.get("checkMesh_status") or "").upper() == "OK"
    fatal = bool(candidate.get("checkMesh_fatal_errors"))
    accepted = (
        not fatal
        and not newly_failed
        and ((base_ok and candidate_ok and score > tolerance) or (not base_ok and score > tolerance))
    )
    return {
        "Q": score,
        "accepted": accepted,
        "decision": "ACCEPT_CANDIDATE" if accepted else "KEEP_BASE",
        "newly_failed_metrics": newly_failed,
        "candidate_fatal": fatal,
        "rows": rows,
        "note": "Acceptance is advisory; activation still requires explicit user approval.",
    }
