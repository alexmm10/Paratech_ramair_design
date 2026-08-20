#!/usr/bin/env python3
"""Strict, small mesh-quality controller for the ram-air 2D Gmsh workflow.

This file intentionally avoids giving a PASS when no real .msh file exists.
It is designed to be imported by ramair_2d_mesh_builder.py and can also be
used as a standalone checker.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class QualityDecision:
    status: str
    failed_checks: list[str]
    warnings: list[str]
    notes: list[str]
    details: list[dict[str, Any]]


METRIC_DESCRIPTIONS: dict[str, str] = {
    "status": "Overall internal quality status from this Python controller. It is stricter than OpenFOAM checkMesh for some diagnostic metrics.",
    "openfoam_execution_gate": "Whether this mesh can continue to OpenFOAM in the debug workflow. OK requires gmshToFoam OK, checkMesh OK, a real polyMesh and valid 2D empty front/back patches.",
    "gmshToFoam_status": "Status of conversion from Gmsh .msh to OpenFOAM polyMesh.",
    "gmshToFoam_wall_time_s": "Elapsed wall-clock seconds spent by the real gmshToFoam conversion.",
    "checkMesh_status": "OpenFOAM checkMesh result. This is the primary safety gate for running OpenFOAM in the debug workflow.",
    "checkMesh_wall_time_s": "Elapsed wall-clock seconds spent by checkMesh -allTopology -allGeometry.",
    "checkMesh_failed_checks": "Named OpenFOAM checkMesh failures parsed from the log.",
    "checkMesh_max_non_orthogonality_deg": "Maximum angle between face normal and cell-centre vector. Lower is better; OpenFOAM reports severe faces above 70 deg.",
    "checkMesh_average_non_orthogonality_deg": "Average non-orthogonality across the mesh.",
    "checkMesh_max_skewness": "OpenFOAM skewness metric. High values indicate distorted cells/faces.",
    "checkMesh_min_face_interpolation_weight": "Minimum interpolation weight. Very small values indicate poor cell-centre alignment across a face.",
    "checkMesh_min_face_volume_ratio": "Minimum volume ratio between neighboring cells. Very small values indicate abrupt local size transitions.",
    "checkMesh_min_cell_determinant": "Minimum cell well-posedness determinant. Small values indicate poor cells.",
    "min_element_quality": "Internal Gmsh-surface element quality estimate parsed from the .msh file; stricter diagnostic, not the same as checkMesh.",
    "surface_neighbor_area_ratio_p95": "95th percentile of neighboring surface-cell area ratios. Large values indicate abrupt surface mesh growth.",
    "triangle_equiangle_skewness_p95": "95th percentile triangle equiangle skewness from the internal parser. Lower is better.",
    "boundary_layer_layers_created": "Whether the parser could confirm boundary-layer cells. For extruded quad BL this is inferred from hex elements.",
    "number_of_hexes": "Hexahedral cells, usually produced by recombined prism/quad boundary-layer extrusion.",
    "number_of_prisms": "Prismatic cells in the extruded OpenFOAM 2D mesh.",
    "estimated_cell_count": "Estimated volume cell count parsed from the mesh file.",
    "estimated_cell_count_source": "Source used for estimated_cell_count. Prefer Gmsh .msh parsing; fallback is OpenFOAM checkMesh cell count.",
    "gmsh_log_nodes": "Node count reported directly by Gmsh in log.gmsh.",
    "gmsh_log_elements": "Element count reported directly by Gmsh in log.gmsh.",
    "mesh_file_size_mb": "Size of the generated .msh file. Large meshes may skip the internal Python parser to avoid slow post-Gmsh processing.",
    "msh_parse_skipped": "True when the internal Python .msh parser was intentionally skipped because the mesh exceeded configured size/count limits.",
    "msh_parse_skip_reason": "Reason the internal Python .msh parser was skipped.",
    "boundary_layer_confirmation_basis": "Evidence used to decide whether boundary-layer-like cells were created.",
    "boundary_layer_exact_layer_count_confirmed": "Whether the exact requested number of BL layers was automatically counted. False means inspect the mesh visually.",
    "boundary_layer_candidate_hex_cells": "Hexahedral volume cells detected; expected when quad BL cells are recombined and extruded.",
    "boundary_layer_candidate_prism_cells": "Prismatic volume cells detected; common in extruded triangular/prismatic 2D OpenFOAM meshes.",
    "checkMesh_cell_count": "Cell count reported by OpenFOAM checkMesh.",
    "effective_gmsh_mesh_algorithm_2d": "Gmsh 2D meshing algorithm passed to Mesh.Algorithm in the .geo file. In this workflow 5=Delaunay is used for robust debug meshing; 6=Frontal-Delaunay may give smoother meshes but was less robust with the current BoundaryLayer field.",
    "effective_gmsh_random_factor": "Gmsh coordinate randomization factor used to avoid exact coincident points in triangulation. A small non-zero value helps after dense wall/BoundaryLayer offsets.",
    "boundary_layer_first_cell_height_chord": "First boundary-layer cell height actually written to Gmsh, nondimensionalized by chord.",
    "boundary_layer_first_cell_height_m": "First boundary-layer cell height actually written to Gmsh, in metres.",
    "boundary_layer_requested_first_cell_height_chord": "First boundary-layer cell height requested by the input configuration before any robustness cap is applied.",
    "boundary_layer_total_thickness_chord": "Total BoundaryLayer field thickness actually written to Gmsh, nondimensionalized by chord.",
    "boundary_layer_raw_total_thickness_chord": "Total BoundaryLayer thickness implied by requested first cell, growth and layer count before any optional thickness override.",
    "boundary_layer_total_thickness_limited": "True only when an explicit BoundaryLayer total-thickness override reduced the raw requested thickness.",
    "boundary_layer_curve_ids": "Gmsh curve IDs included in Field[1].CurvesList for the BoundaryLayer field.",
    "boundary_layer_excluded_te_curve_ids": "TE closure/cap curve IDs intentionally excluded from BoundaryLayer.CurvesList to avoid synthetic BL edge recovery failures at very short closure curves.",
    "boundary_layer_first_cell_height_source": "Source of the first boundary-layer height: config override, y+ flat-plate estimate, or fallback.",
    "boundary_layer_yplus_estimate": "Traceable flat-plate y+ estimate used when the first-cell-height override is null.",
    "closed_airfoil_target_nodes": "Target total tangential nodes for the closed-profile wall curves written with Gmsh Transfinite Curve.",
    "closed_te_target_nodes": "Target tangential nodes allocated to rounded TE cap curves in the closed debug profile.",
    "closed_te_cap_curve_ids": "Gmsh curve IDs representing the closed rounded TE cap.",
    "gmsh_manual_discretization_note": "Reminder that Gmsh Transfinite Curve controls wall tangential divisions while BoundaryLayer controls normal layers.",
    "open_boundary_layer_curve_ids": "Open diagnostic Gmsh curve IDs included in the BoundaryLayer field.",
    "open_boundary_layer_excluded_te_curve_ids": "Open diagnostic TE cap curve IDs intentionally excluded from BoundaryLayer.CurvesList.",
    "open_fluid_topology": "Topology used for open-profile meshing. The supported path uses conformal exterior, inlet-transition and cavity surfaces that form one connected solver fluid region around a finite-thickness fabric solid.",
    "open_connected_fluid_surface": "True when the open-profile exterior and internal cavity are one connected fluid region through the inlet gap.",
    "open_boundary_layer_single_loop_bspline": "True when the open debug BL is requested on one closed diagnostic loop to avoid short independent inlet/TE BL curves.",
    "open_boundary_layer_single_loop_curve_kind": "Gmsh curve primitive used for the single open debug BL loop. Spline interpolates the profile points; BSpline treats them as control points.",
    "open_boundary_layer_single_loop_transfinite": "True when a Transfinite Curve constraint is applied to the single open debug BL loop. False lets local point sizes control surface discretization.",
    "open_boundary_layer_curve_policy": "Human-readable summary of which open-profile curves are used in the Gmsh BoundaryLayer field.",
    "open_surface_target_nodes": "Target total tangential nodes for the open exterior wall curves written with Gmsh Transfinite Curve.",
    "open_zero_thickness_contour_target_nodes": "Total tangential segments distributed by arc length over the complete zero-thickness wall/TE/nonphysical-inlet loop.",
    "open_zero_thickness_uniform_spacing_chord": "Common target tangential spacing divided by chord. It prevents a discretization jump where the boundary role changes at an inlet lip.",
    "open_outer_wall_transfinite_curve_nodes": "Actual Gmsh Transfinite Curve node counts written for each open exterior wall curve.",
    "open_lip_transfinite_min_nodes": "Minimum node count imposed on open upper/lower exterior wall curves so the inlet lips are not under-discretized.",
    "open_inlet_refinement_bridge_enabled": "True when a non-physical embedded line is written across the inlet only to control mesh size; it is not a wall patch.",
    "open_inlet_refinement_bridge_curve_ids": "Gmsh curve IDs of non-physical embedded inlet sizing markers.",
    "open_inlet_refinement_bridge_in_boundary_layer": "Whether the nonphysical inlet bridge participates in the exterior BoundaryLayer loop. It is never exported as a physical wall patch.",
    "open_boundary_layer_inlet_bridge_in_single_loop": "True when the geometric inlet bridge is part of the single closed diagnostic BL loop, without using the inlet marker as a physical wall patch.",
    "open_internal_cavity_meshed": "True when the internal cavity fluid surface is triangulated and included in the open-profile mesh.",
    "open_internal_cavity_curve_mode": "How the internal fabric boundary is written. Continuous splines reduce curve fragmentation while preserving ordered input points.",
    "open_inlet_transition_mesh": "Element strategy for the narrow fluid connector between exterior and cavity. The supported transfinite/recombined strip avoids degenerate free-triangulation diagonals without creating a wall patch.",
    "open_interface_sizes_from_boundary_layer": "True when local TE/inlet triangle targets are derived from the final BL-layer height and actual tangential curve spacing instead of a global manual size.",
    "open_te_interface_size_chord": "Active local triangle target at the rounded TE, divided by chord.",
    "open_inlet_interface_size_chord": "Active local triangle target on both sides of the nonphysical inlet interface, divided by chord.",
    "open_lip_cap_interface_size_chord": "Highly local triangle target adjacent to the finite-thickness lip cap, divided by chord.",
    "open_internal_cavity_solver_connected": "True when the internal cavity is part of the same solver fluid topology as the exterior. False means separate diagnostic internal triangles.",
    "open_internal_cavity_note": "Explanation of the diagnostic internal cavity topology and Gmsh BoundaryLayer limitations.",
    "gmsh_boundary_layer_fallback_used": "True when Gmsh failed on its synthetic BoundaryLayer curve and the builder reran the same geometry without the Gmsh BoundaryLayer block to obtain a debug mesh.",
    "gmsh_boundary_layer_fallback_reason": "Reason for disabling the Gmsh BoundaryLayer block in the fallback run.",
}


CHECK_DESCRIPTIONS: dict[str, str] = {
    "mesh_file_not_created": "No real .msh file was produced.",
    "gmsh_error": "Gmsh reported an error; inspect log.gmsh.",
    "diagnostic_geometry_not_openfoam_ready": "The geometry is diagnostic only and must not be used as an OpenFOAM case.",
    "nan_coordinates": "The input/profile contains NaN or infinite coordinates.",
    "profile_outside_domain": "The profile is outside the farfield domain.",
    "no_physical_groups": "Gmsh physical groups are missing, so boundaries cannot be mapped safely.",
    "wall_patches_missing": "No wall physical patch exists.",
    "farfield_patch_missing": "No farfield patch exists.",
    "openfoam_mesh_not_extruded_to_3d": "OpenFOAM 2D requires a one-cell-thick 3D mesh.",
    "openfoam_mesh_must_have_one_spanwise_layer": "The OpenFOAM 2D mesh must have exactly one spanwise layer.",
    "frontAndBack_patch_missing": "The frontAndBack patch is missing.",
    "ram_air_inlet_must_not_be_physical_patch": "ram_air_inlet must remain a diagnostic marker, not a physical CFD boundary.",
    "ram_air_inlet_patch_present_in_openfoam_boundary": "The forbidden ram_air_inlet patch appears in OpenFOAM boundary.",
    "negative_or_zero_area_elements": "The internal parser found non-positive surface element area.",
    "frontAndBack_missing_after_gmshToFoam": "gmshToFoam did not produce a frontAndBack boundary.",
    "frontAndBack_not_empty_after_gmshToFoam": "frontAndBack exists but is not declared as empty.",
    "very_low_element_quality": "Internal Gmsh element quality is below the strict debug threshold.",
    "high_checkMesh_skewness": "OpenFOAM checkMesh reported high skewness.",
    "checkMesh_small_interpolation_weight_faces": "OpenFOAM found faces with small interpolation weight.",
    "checkMesh_small_volume_ratio_faces": "OpenFOAM found abrupt volume jumps across faces.",
    "boundary_layer_layers_not_confirmed": "The workflow requested a boundary layer, but no confirming quad/hex/prism cell evidence was found in the parsed mesh/checkMesh data.",
}


def _detail(name: str, severity: str, description: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "severity": severity,
        "description": description,
        "values": values or {},
    }


def _openfoam_execution_gate(report: dict[str, Any]) -> tuple[str, str]:
    if not report.get("openfoam_mesh_requested", False):
        return "NOT_REQUESTED", "OpenFOAM mesh conversion/check was not requested."
    if not report.get("openfoam_polyMesh_created"):
        return "BLOCKED", "No real OpenFOAM constant/polyMesh was created."
    if report.get("gmshToFoam_status") != "OK":
        return "BLOCKED", f"gmshToFoam_status is {report.get('gmshToFoam_status')}."
    if report.get("checkMesh_status") != "OK":
        return "BLOCKED", f"checkMesh_status is {report.get('checkMesh_status')}."
    if report.get("frontAndBack_boundary_present") is False:
        return "BLOCKED", "frontAndBack boundary is missing after conversion."
    if report.get("frontAndBack_empty_declared") is False:
        return "BLOCKED", "frontAndBack boundary is not declared empty."
    if report.get("forbidden_ram_air_inlet_patch_present"):
        return "BLOCKED", "Forbidden ram_air_inlet patch appears in OpenFOAM boundary."
    return "OK", "gmshToFoam/checkMesh passed and a real OpenFOAM polyMesh exists."


def engineering_quality_assessment(report: dict[str, Any]) -> dict[str, Any]:
    """Grade solver risk with margins, without replacing or blocking checkMesh.

    The limits marked ``OpenFOAM failure`` are the values parsed from the
    OpenFOAM Foundation checkMesh output. The tighter bands are project engineering
    targets used to expose a mesh that technically passes but has little
    numerical margin.  High boundary-layer aspect ratio is reported but is not
    penalized by itself because aligned anisotropy is intentional.
    """
    if str(report.get("checkMesh_status", "")).upper() != "OK":
        return {
            "grade": "F",
            "label": "No apta para solver",
            "solver_risk": "BLOCKED",
            "blocking": False,
            "workflow_gate_unchanged": True,
            "summary": "checkMesh no ha finalizado con OK; el diagnostico informativo no sustituye ese fallo.",
            "metrics": [],
            "recommendations": ["Resolver primero todos los checks fallidos de OpenFOAM."],
        }

    specifications = [
        ("max_non_orthogonality_deg", "checkMesh_max_non_orthogonality_deg", "lower", 45.0, 60.0, 70.0, "deg", 70.0),
        ("average_non_orthogonality_deg", "checkMesh_average_non_orthogonality_deg", "lower", 10.0, 20.0, 35.0, "deg", None),
        ("max_skewness", "checkMesh_max_skewness", "lower", 1.0, 2.0, 4.0, "", 4.0),
        ("min_cell_determinant", "checkMesh_min_cell_determinant", "higher", 0.01, 0.005, 0.001, "", 0.001),
        ("min_face_interpolation_weight", "checkMesh_min_face_interpolation_weight", "higher", 0.20, 0.10, 0.05, "", 0.05),
        ("min_face_volume_ratio", "checkMesh_min_face_volume_ratio", "higher", 0.20, 0.10, 0.01, "", 0.01),
    ]
    rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    metric_rows: list[dict[str, Any]] = []
    worst = "A"
    recommendations: list[str] = []
    for name, key, direction, target_a, target_b, target_c, unit, failure_limit in specifications:
        raw = report.get(key)
        if raw is None:
            metric_rows.append({"metric": name, "source": key, "status": "NOT_REPORTED"})
            continue
        value = float(raw)
        if direction == "lower":
            grade = "A" if value <= target_a else "B" if value <= target_b else "C" if value <= target_c else "D"
        else:
            grade = "A" if value >= target_a else "B" if value >= target_b else "C" if value >= target_c else "D"
        worst = grade if rank[grade] > rank[worst] else worst
        margin = None
        if failure_limit is not None:
            margin = failure_limit - value if direction == "lower" else value - failure_limit
        metric_rows.append({
            "metric": name,
            "source": key,
            "value": value,
            "unit": unit,
            "grade": grade,
            "engineering_A": target_a,
            "engineering_B": target_b,
            "engineering_C_or_checkMesh_limit": target_c,
            "checkMesh_failure_limit": failure_limit,
            "margin_to_checkMesh_failure": margin,
            "preferred_direction": direction,
        })
        if grade in {"C", "D"}:
            recommendations.append(f"Revisar {name}: valor {value:.6g}, calificacion {grade}.")

    locations = report.get("checkMesh_problem_locations", {}) or {}
    wall_sensitive = [
        name for name, item in locations.items()
        if any(token in str(item.get("likely_region", "")).lower() for token in ("wall", "lip", "leading", "trailing", "inlet"))
    ]
    labels = {
        "A": ("Muy buena", "LOW"),
        "B": ("Buena", "LOW_TO_MODERATE"),
        "C": ("Aceptable con margen limitado", "ELEVATED"),
        "D": ("Marginal pese a checkMesh OK", "HIGH"),
    }
    label, risk = labels[worst]
    if wall_sensitive:
        recommendations.append(
            "Las peores entidades estan cerca de pared/labio; priorizar skewness y ortogonalidad local antes que el score global."
        )
    return {
        "grade": worst,
        "label": label,
        "solver_risk": risk,
        "blocking": False,
        "workflow_gate_unchanged": True,
        "summary": (
            f"checkMesh es OK, pero la calificacion de margen numerico es {worst} ({label}). "
            "No valida independencia de malla, y+, convergencia ni exactitud aerodinamica."
        ),
        "metrics": metric_rows,
        "wall_sensitive_problem_sets": wall_sensitive,
        "recommendations": list(dict.fromkeys(recommendations)),
        "aspect_ratio_note": (
            "El aspect ratio alto no se penaliza de forma aislada en una BL prismatica; debe estar alineado con la pared "
            "y acompanado de buena ortogonalidad, skewness y crecimiento suave."
        ),
        "technical_basis": [
            "OpenFOAM Foundation checkMesh limits parsed from the real case log.",
            "OpenFOAM guidance on non-orthogonal correction stability near 75 degrees.",
            "Ram-air open-airfoil grid study: wall skewness and orthogonality are decision-critical.",
        ],
    }


def decide_quality(report: dict[str, Any], attempt_index: int = 1) -> QualityDecision:
    failed: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    details: list[dict[str, Any]] = []

    if report.get("dry_run"):
        return QualityDecision("DRY_RUN", [], ["dry_run_no_mesh_was_generated"], [], [
            _detail("dry_run_no_mesh_was_generated", "warning", "Dry-run wrote geometry and reports only; no real mesh was generated.")
        ])

    if not report.get("mesh_file_created", False):
        failed.append("mesh_file_not_created")
        details.append(_detail("mesh_file_not_created", "fail", CHECK_DESCRIPTIONS["mesh_file_not_created"]))
    if report.get("gmsh_exit_code") not in (0, None):
        name = f"gmsh_exit_code_{report.get('gmsh_exit_code')}"
        failed.append(name)
        details.append(_detail(name, "fail", "Gmsh returned a non-zero exit code.", {"gmsh_exit_code": report.get("gmsh_exit_code")}))
    if report.get("gmsh_error"):
        failed.append("gmsh_error")
        details.append(_detail("gmsh_error", "fail", CHECK_DESCRIPTIONS["gmsh_error"], {"gmsh_error": report.get("gmsh_error")}))
    if report.get("diagnostic_geometry_only", False):
        failed.append("diagnostic_geometry_not_openfoam_ready")
        details.append(_detail("diagnostic_geometry_not_openfoam_ready", "fail", CHECK_DESCRIPTIONS["diagnostic_geometry_not_openfoam_ready"]))
    if report.get("openfoam_ready") is False:
        warnings.append("mesh_not_marked_openfoam_ready")
        details.append(_detail("mesh_not_marked_openfoam_ready", "warning", "The geometry report did not mark this mesh as OpenFOAM-ready. Continue only if the converted polyMesh and checkMesh are valid."))
    if not report.get("no_nan_coordinates", False):
        failed.append("nan_coordinates")
        details.append(_detail("nan_coordinates", "fail", CHECK_DESCRIPTIONS["nan_coordinates"]))
    if not report.get("no_duplicate_points", False):
        warnings.append("duplicate_points_detected_or_merged")
        details.append(_detail("duplicate_points_detected_or_merged", "warning", "Duplicate or near-duplicate profile points were detected or merged during preprocessing."))
    if not report.get("domain_contains_profile", False):
        failed.append("profile_outside_domain")
        details.append(_detail("profile_outside_domain", "fail", CHECK_DESCRIPTIONS["profile_outside_domain"]))
    if not report.get("physical_groups_exist", False):
        failed.append("no_physical_groups")
        details.append(_detail("no_physical_groups", "fail", CHECK_DESCRIPTIONS["no_physical_groups"]))
    if not report.get("wall_patches_exist", False):
        failed.append("wall_patches_missing")
        details.append(_detail("wall_patches_missing", "fail", CHECK_DESCRIPTIONS["wall_patches_missing"]))
    if not report.get("farfield_patch_exists", False):
        failed.append("farfield_patch_missing")
        details.append(_detail("farfield_patch_missing", "fail", CHECK_DESCRIPTIONS["farfield_patch_missing"]))
    if report.get("openfoam_mesh_requested", False):
        if not report.get("extruded_3d", False):
            failed.append("openfoam_mesh_not_extruded_to_3d")
            details.append(_detail("openfoam_mesh_not_extruded_to_3d", "fail", CHECK_DESCRIPTIONS["openfoam_mesh_not_extruded_to_3d"]))
        if int(report.get("spanwise_layers", 0) or 0) != 1:
            failed.append("openfoam_mesh_must_have_one_spanwise_layer")
            details.append(_detail("openfoam_mesh_must_have_one_spanwise_layer", "fail", CHECK_DESCRIPTIONS["openfoam_mesh_must_have_one_spanwise_layer"], {"spanwise_layers": report.get("spanwise_layers")}))
        if not report.get("frontAndBack_patch_exists", False):
            failed.append("frontAndBack_patch_missing")
            details.append(_detail("frontAndBack_patch_missing", "fail", CHECK_DESCRIPTIONS["frontAndBack_patch_missing"]))
    if report.get("ram_air_inlet_is_physical_patch", False):
        failed.append("ram_air_inlet_must_not_be_physical_patch")
        details.append(_detail("ram_air_inlet_must_not_be_physical_patch", "fail", CHECK_DESCRIPTIONS["ram_air_inlet_must_not_be_physical_patch"]))
    if report.get("forbidden_ram_air_inlet_patch_present", False):
        failed.append("ram_air_inlet_patch_present_in_openfoam_boundary")
        details.append(_detail("ram_air_inlet_patch_present_in_openfoam_boundary", "fail", CHECK_DESCRIPTIONS["ram_air_inlet_patch_present_in_openfoam_boundary"]))
    if report.get("positive_areas") is False:
        failed.append("negative_or_zero_area_elements")
        details.append(_detail("negative_or_zero_area_elements", "fail", CHECK_DESCRIPTIONS["negative_or_zero_area_elements"]))
    if report.get("check_mesh_requested", False):
        if report.get("gmshToFoam_status") in {"MISSING", "FAIL"}:
            name = f"gmshToFoam_{str(report.get('gmshToFoam_status')).lower()}"
            failed.append(name)
            details.append(_detail(name, "fail", "gmshToFoam conversion did not complete successfully.", {"gmshToFoam_status": report.get("gmshToFoam_status")}))
        if report.get("checkMesh_status") in {"MISSING", "FAIL"}:
            name = f"checkMesh_{str(report.get('checkMesh_status')).lower()}"
            failed.append(name)
            details.append(_detail(name, "fail", "OpenFOAM checkMesh did not pass.", {"checkMesh_status": report.get("checkMesh_status"), "checkMesh_failed_checks": report.get("checkMesh_failed_checks")}))
        if report.get("frontAndBack_boundary_present") is False:
            failed.append("frontAndBack_missing_after_gmshToFoam")
            details.append(_detail("frontAndBack_missing_after_gmshToFoam", "fail", CHECK_DESCRIPTIONS["frontAndBack_missing_after_gmshToFoam"]))
        if report.get("frontAndBack_empty_declared") is False:
            failed.append("frontAndBack_not_empty_after_gmshToFoam")
            details.append(_detail("frontAndBack_not_empty_after_gmshToFoam", "fail", CHECK_DESCRIPTIONS["frontAndBack_not_empty_after_gmshToFoam"]))

    cell_count = report.get("estimated_cell_count")
    if cell_count is None:
        if report.get("mesh_file_created"):
            warnings.append("could_not_parse_cell_count")
            details.append(_detail(
                "could_not_parse_cell_count",
                "warning",
                "The internal Gmsh .msh parser did not obtain a cell count. If checkMesh_cell_count is present, OpenFOAM still counted the mesh and that value should be used.",
                {"checkMesh_cell_count": report.get("checkMesh_cell_count"), "msh_parse_error": report.get("msh_parse_error")},
            ))
    else:
        if cell_count < int(report.get("min_cells_warning", 1000)):
            name = f"low_cell_count_{cell_count}"
            warnings.append(name)
            details.append(_detail(name, "warning", "The mesh is very coarse for CFD. Acceptable for software debugging only.", {"estimated_cell_count": cell_count, "minimum_warning": report.get("min_cells_warning", 1000)}))
        if cell_count > int(report.get("max_cells", 2_000_000)):
            name = f"high_cell_count_{cell_count}"
            warnings.append(name)
            details.append(_detail(name, "warning", "The mesh exceeds the configured maximum cell count for this workflow.", {"estimated_cell_count": cell_count, "maximum": report.get("max_cells", 2_000_000)}))

    min_q = report.get("min_element_quality")
    if min_q is not None:
        if min_q < 0.02:
            name = f"very_low_element_quality_{min_q:.4g}"
            failed.append(name)
            details.append(_detail(name, "fail", CHECK_DESCRIPTIONS["very_low_element_quality"], {"min_element_quality": min_q, "threshold": 0.02}))
        elif min_q < 0.08:
            warnings.append(f"low_element_quality_{min_q:.4g}")
            details.append(_detail(f"low_element_quality_{min_q:.4g}", "warning", "Internal Gmsh element quality is low but above the fail threshold.", {"min_element_quality": min_q, "warning_threshold": 0.08}))

    min_ang = report.get("min_triangle_angle")
    max_ang = report.get("max_triangle_angle")
    if min_ang is not None and min_ang < 8.0:
        name = f"low_min_triangle_angle_{min_ang:.2f}"
        warnings.append(name)
        details.append(_detail(name, "warning", "Internal surface parser found a very small triangle angle.", {"min_triangle_angle_deg": min_ang, "warning_threshold_deg": 8.0}))
    if max_ang is not None and max_ang > 170.0:
        name = f"high_max_triangle_angle_{max_ang:.2f}"
        warnings.append(name)
        details.append(_detail(name, "warning", "Internal surface parser found a very large triangle angle.", {"max_triangle_angle_deg": max_ang, "warning_threshold_deg": 170.0}))

    tri_skew_p95 = report.get("triangle_equiangle_skewness_p95")
    if tri_skew_p95 is not None and tri_skew_p95 > 0.75:
        name = f"high_triangle_skewness_p95_{tri_skew_p95:.3f}"
        warnings.append(name)
        details.append(_detail(name, "warning", "95th percentile internal triangle skewness is high.", {"triangle_equiangle_skewness_p95": tri_skew_p95, "warning_threshold": 0.75}))
    smooth_p95 = report.get("surface_neighbor_area_ratio_p95")
    if smooth_p95 is not None and smooth_p95 > 3.0:
        name = f"high_surface_smoothness_ratio_p95_{smooth_p95:.3f}"
        warnings.append(name)
        details.append(_detail(name, "warning", "Neighboring surface-cell area ratio changes too abruptly in the internal parser.", {"surface_neighbor_area_ratio_p95": smooth_p95, "warning_threshold": 3.0}))
    max_non_orth = report.get("checkMesh_max_non_orthogonality_deg")
    if max_non_orth is not None and max_non_orth > 70.0:
        name = f"high_checkMesh_non_orthogonality_{max_non_orth:.2f}"
        warnings.append(name)
        details.append(_detail(name, "warning", "OpenFOAM checkMesh reported high non-orthogonality.", {"max_non_orthogonality_deg": max_non_orth, "severe_threshold_deg": 70.0, "severely_non_orthogonal_faces": report.get("checkMesh_severely_non_orthogonal_faces")}))
    severe_non_orth = report.get("checkMesh_severely_non_orthogonal_faces")
    if severe_non_orth:
        name = f"checkMesh_severely_non_orthogonal_faces_{severe_non_orth}"
        warnings.append(name)
        details.append(_detail(name, "warning", "OpenFOAM checkMesh counted severely non-orthogonal faces.", {"faces": severe_non_orth, "threshold_deg": 70.0, "max_non_orthogonality_deg": max_non_orth}))
    max_skew = report.get("checkMesh_max_skewness")
    if max_skew is not None and max_skew > 4.0:
        name = f"high_checkMesh_skewness_{max_skew:.2f}"
        warnings.append(name)
        details.append(_detail(name, "warning", CHECK_DESCRIPTIONS["high_checkMesh_skewness"], {"checkMesh_max_skewness": max_skew, "warning_threshold": 4.0, "highly_skew_faces": report.get("checkMesh_highly_skew_faces")}))
    skew_faces = report.get("checkMesh_highly_skew_faces")
    if skew_faces:
        name = f"checkMesh_highly_skew_faces_{skew_faces}"
        warnings.append(name)
        details.append(_detail(name, "warning", "OpenFOAM checkMesh counted highly skew faces.", {"faces": skew_faces, "checkMesh_max_skewness": max_skew}))
    small_det = report.get("checkMesh_small_determinant_cells")
    if small_det:
        name = f"checkMesh_small_determinant_cells_{small_det}"
        warnings.append(name)
        details.append(_detail(name, "warning", "OpenFOAM checkMesh found cells with small determinant/well-posedness.", {"cells": small_det, "minimum": report.get("checkMesh_min_cell_determinant"), "threshold": 0.001}))
    small_interp = report.get("checkMesh_small_interpolation_weight_faces")
    if small_interp:
        name = f"checkMesh_small_interpolation_weight_faces_{small_interp}"
        warnings.append(name)
        details.append(_detail(name, "warning", CHECK_DESCRIPTIONS["checkMesh_small_interpolation_weight_faces"], {"faces": small_interp, "minimum": report.get("checkMesh_min_face_interpolation_weight"), "threshold": 0.05}))
    small_vol_ratio = report.get("checkMesh_small_volume_ratio_faces")
    if small_vol_ratio:
        name = f"checkMesh_small_volume_ratio_faces_{small_vol_ratio}"
        warnings.append(name)
        details.append(_detail(name, "warning", CHECK_DESCRIPTIONS["checkMesh_small_volume_ratio_faces"], {"faces": small_vol_ratio, "minimum": report.get("checkMesh_min_face_volume_ratio"), "threshold": 0.01}))

    if report.get("boundary_layer_requested") and not report.get("boundary_layer_layers_created", False):
        # This is a warning rather than an automatic fail because Gmsh's BL reporting
        # is not always easy to parse. The PNG/Gmsh view must be inspected.
        warnings.append("boundary_layer_layers_not_confirmed")
        details.append(_detail(
            "boundary_layer_layers_not_confirmed",
            "warning",
            CHECK_DESCRIPTIONS["boundary_layer_layers_not_confirmed"],
            {
                "requested_layers": report.get("boundary_layer_layers_requested"),
                "confirmation_basis": report.get("boundary_layer_confirmation_basis"),
                "hex_cells": report.get("boundary_layer_candidate_hex_cells"),
                "prism_cells": report.get("boundary_layer_candidate_prism_cells"),
                "exact_layer_count_confirmed": report.get("boundary_layer_exact_layer_count_confirmed"),
            },
        ))

    if failed:
        status = "FAIL"
    elif warnings:
        status = "WARNING_ACCEPTABLE"
    else:
        status = "PASS"
    gate_status, gate_reason = _openfoam_execution_gate(report)
    notes.append(f"openfoam_execution_gate_{gate_status.lower()}: {gate_reason}")
    details.append(_detail("openfoam_execution_gate", "fail" if gate_status == "BLOCKED" else "info", gate_reason, {"openfoam_execution_gate": gate_status}))
    return QualityDecision(status, failed, warnings, notes, details)


def write_quality_report(out_dir: Path, report: dict[str, Any], decision: QualityDecision) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = dict(report)
    data["status"] = decision.status
    data["failed_checks"] = decision.failed_checks
    data["warnings"] = decision.warnings
    data["notes"] = decision.notes
    gate_status, gate_reason = _openfoam_execution_gate(report)
    data["openfoam_execution_gate"] = gate_status
    data["openfoam_execution_gate_reason"] = gate_reason
    data["quality_check_details"] = decision.details
    assessment = engineering_quality_assessment(data)
    data["engineering_quality_assessment"] = assessment
    data["metric_descriptions"] = {k: v for k, v in METRIC_DESCRIPTIONS.items() if k in data or k in {"openfoam_execution_gate"}}
    (out_dir / "mesh_quality_report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (out_dir / "mesh_engineering_assessment.json").write_text(json.dumps(assessment, indent=2) + "\n", encoding="utf-8")

    lines = [
        "Ram-air 2D Mesh Quality Report",
        "==============================",
        "",
        f"INTERNAL QUALITY STATUS: {decision.status}",
        f"OPENFOAM EXECUTION GATE: {gate_status}",
        f"OPENFOAM GATE REASON: {gate_reason}",
        f"ENGINEERING QUALITY GRADE (NON-BLOCKING): {assessment['grade']} - {assessment['label']}",
        f"ESTIMATED SOLVER RISK: {assessment['solver_risk']}",
        "",
        "How to read this report:",
        "- INTERNAL QUALITY STATUS is a strict Python-side diagnostic.",
        "- OPENFOAM EXECUTION GATE is the debug-flow gate for continuing to case writing/running.",
        "- If checkMesh is OK but internal status is FAIL, the mesh may continue for software debugging, but review the failed diagnostics before aerodynamic conclusions.",
        "- ENGINEERING QUALITY GRADE reports numerical margin only and never changes the OpenFOAM execution gate.",
        "",
        "Failed Checks",
        "-------------",
    ]
    if decision.failed_checks:
        listed_failed: set[str] = set()
        for item in decision.details:
            if item["severity"] == "fail":
                listed_failed.add(str(item["name"]))
                lines.append(f"- {item['name']}: {item['description']} Values: {item.get('values', {})}")
        for name in decision.failed_checks:
            if name not in listed_failed:
                lines.append(f"- {name}: {CHECK_DESCRIPTIONS.get(name, 'See raw values and logs for this failed condition.')}")
    else:
        lines.append("- none")
    lines.extend(["", "Warnings", "--------"])
    if decision.warnings:
        listed_warnings: set[str] = set()
        for item in decision.details:
            if item["severity"] == "warning":
                listed_warnings.add(str(item["name"]))
                lines.append(f"- {item['name']}: {item['description']} Values: {item.get('values', {})}")
        for name in decision.warnings:
            if name not in listed_warnings:
                lines.append(f"- {name}: See raw values and logs for this warning.")
    else:
        lines.append("- none")
    lines.extend(["", "Key Metrics", "-----------"])
    key_order = [
        "estimated_cell_count",
        "number_of_hexes",
        "number_of_prisms",
        "gmshToFoam_status",
        "checkMesh_status",
        "checkMesh_failed_checks",
        "checkMesh_max_non_orthogonality_deg",
        "checkMesh_average_non_orthogonality_deg",
        "checkMesh_max_skewness",
        "checkMesh_min_face_interpolation_weight",
        "checkMesh_min_face_volume_ratio",
        "checkMesh_min_cell_determinant",
        "min_element_quality",
        "surface_neighbor_area_ratio_p95",
        "triangle_equiangle_skewness_p95",
        "boundary_layer_layers_created",
        "boundary_layer_confirmation_basis",
        "boundary_layer_exact_layer_count_confirmed",
        "boundary_layer_candidate_hex_cells",
        "boundary_layer_candidate_prism_cells",
        "checkMesh_cell_count",
        "effective_gmsh_mesh_algorithm_2d",
        "effective_gmsh_random_factor",
        "boundary_layer_first_cell_height_chord",
        "boundary_layer_first_cell_height_m",
        "boundary_layer_requested_first_cell_height_chord",
        "boundary_layer_first_cell_height_source",
        "boundary_layer_yplus_estimate",
        "boundary_layer_total_thickness_chord",
        "boundary_layer_raw_total_thickness_chord",
        "boundary_layer_total_thickness_limited",
        "closed_airfoil_target_nodes",
        "closed_te_target_nodes",
        "closed_te_cap_curve_ids",
        "gmsh_manual_discretization_note",
        "boundary_layer_curve_ids",
        "boundary_layer_excluded_te_curve_ids",
        "open_surface_target_nodes",
        "open_zero_thickness_contour_target_nodes",
        "open_zero_thickness_uniform_spacing_chord",
        "open_lip_transfinite_min_nodes",
        "open_outer_wall_transfinite_curve_nodes",
        "open_inlet_refinement_bridge_enabled",
        "open_inlet_refinement_bridge_curve_ids",
        "open_inlet_refinement_bridge_in_boundary_layer",
        "open_boundary_layer_curve_ids",
        "open_boundary_layer_excluded_te_curve_ids",
        "gmsh_boundary_layer_fallback_used",
        "gmsh_boundary_layer_fallback_reason",
    ]
    for k in key_order:
        if k in data:
            lines.append(f"- {k}: {data[k]}")
            desc = METRIC_DESCRIPTIONS.get(k)
            if desc:
                lines.append(f"  Description: {desc}")
    lines.extend(["", "All Raw Values", "--------------"])
    for k in sorted(data):
        if k not in {"metric_descriptions", "quality_check_details"}:
            lines.append(f"{k}: {data[k]}")
    (out_dir / "mesh_quality_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assessment_lines = [
        "Ram-air 2D Engineering Mesh Assessment (Non-blocking)",
        "====================================================",
        f"Grade: {assessment['grade']} - {assessment['label']}",
        f"Estimated solver risk: {assessment['solver_risk']}",
        f"Summary: {assessment['summary']}",
        "",
        "Metrics",
        "-------",
        *[json.dumps(row, ensure_ascii=False) for row in assessment.get("metrics", [])],
        "",
        "Recommendations",
        "---------------",
        *[f"- {item}" for item in assessment.get("recommendations", [])],
        "",
        assessment.get("aspect_ratio_note", ""),
    ]
    (out_dir / "mesh_engineering_assessment.txt").write_text("\n".join(assessment_lines) + "\n", encoding="utf-8")

    detail_rows = "\n".join(
        f"<tr><td>{d['severity']}</td><td>{d['name']}</td><td>{d['description']}</td><td><pre>{d.get('values', {})}</pre></td></tr>"
        for d in decision.details
    )
    html_rows = "\n".join(
        f"<tr><td>{k}</td><td><pre>{data[k]}</pre></td><td>{METRIC_DESCRIPTIONS.get(k, '')}</td></tr>"
        for k in sorted(data)
        if k not in {"metric_descriptions", "quality_check_details"}
    )
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Mesh quality report</title>
<style>body{{font-family:Arial,sans-serif;margin:20px;line-height:1.35}} table{{border-collapse:collapse;margin:14px 0;width:100%}} td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}} pre{{margin:0;white-space:pre-wrap}} .ok{{color:#0a7a28}} .fail{{color:#b00020}}</style>
</head><body>
<h1>Mesh quality report</h1>
<h2>Internal status: <span class='{"fail" if decision.status == "FAIL" else "ok"}'>{decision.status}</span></h2>
<h2>OpenFOAM execution gate: <span class='{"ok" if gate_status == "OK" else "fail"}'>{gate_status}</span></h2>
<p>{gate_reason}</p>
<h2>Engineering grade (non-blocking): {assessment['grade']} - {assessment['label']}</h2>
<p>{assessment['summary']}</p>
<p><strong>Interpretation:</strong> the internal status is a strict diagnostic. For this debug workflow, a real polyMesh plus checkMesh OK can continue to OpenFOAM after user approval.</p>
<h2>Checks and warnings</h2>
<table><tr><th>Severity</th><th>Name</th><th>Description</th><th>Values</th></tr>{detail_rows}</table>
<h2>Raw values</h2>
<table><tr><th>Metric</th><th>Value</th><th>Description</th></tr>{html_rows}</table>
</body></html>
"""
    (out_dir / "mesh_quality_report.html").write_text(html, encoding="utf-8")


def update_mesh_config_for_remesh(mesh_cfg: dict[str, Any], decision: QualityDecision, attempt_index: int) -> dict[str, Any]:
    cfg = dict(mesh_cfg)
    # Conservative bounded remeshing. Never loop indefinitely; caller controls max attempts.
    if any("te" in w.lower() for w in decision.warnings + decision.failed_checks):
        cfg["surface_size_te_chord"] = max(float(cfg.get("surface_size_te_chord", 0.001)) * 0.7, 1e-5)
    if any("inlet" in w.lower() for w in decision.warnings + decision.failed_checks):
        cfg["surface_size_inlet_lips_chord"] = max(float(cfg.get("surface_size_inlet_lips_chord", 0.00075)) * 0.7, 1e-5)
    if any("low_element_quality" in w.lower() or "angle" in w.lower() for w in decision.warnings + decision.failed_checks):
        cfg["surface_size_general_chord"] = max(float(cfg.get("surface_size_general_chord", 0.003)) * 0.8, 1e-5)
        cfg["boundary_layer_growth"] = max(float(cfg.get("boundary_layer_growth", 1.10)) * 0.98, 1.02)
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report_json", type=Path)
    args = ap.parse_args()
    report = json.loads(args.report_json.read_text())
    decision = decide_quality(report)
    print(json.dumps(asdict(decision), indent=2))


if __name__ == "__main__":
    main()
