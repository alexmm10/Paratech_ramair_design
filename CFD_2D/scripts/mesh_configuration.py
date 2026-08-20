#!/usr/bin/env python3
"""Shared domain and mesh-level configuration for the CFD 2D workflow.

The UI and the mesh builder import this module so a displayed preset cannot
silently differ from the values used by Gmsh.  Mesh levels are starting
points: an explicitly loaded or edited JSON always has higher priority.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


DOMAIN_DEFAULTS: dict[str, dict[str, float | str]] = {
    "circular_50c": {
        "type": "circle",
        "radius": 50.0,
    },
    "ross_cgrid_like": {
        "type": "cgrid",
        "upstream": 10.0,
        "downstream": 20.0,
        "top": 10.0,
        "bottom": 10.0,
    },
    "rectangular_balaji": {
        "type": "rectangle",
        "upstream": 5.0,
        "downstream": 11.0,
        "top": 5.0,
        "bottom": 5.0,
    },
    "debug_20c": {
        "type": "circle",
        "radius": 20.0,
    },
}

DOMAIN_CONFIG_KEYS = {
    "circular_50c": ("domain_circular_radius_chord",),
    "ross_cgrid_like": (
        "domain_cgrid_upstream_chord",
        "domain_cgrid_downstream_chord",
        "domain_cgrid_top_chord",
        "domain_cgrid_bottom_chord",
    ),
    "rectangular_balaji": (
        "domain_rectangular_upstream_chord",
        "domain_rectangular_downstream_chord",
        "domain_rectangular_top_chord",
        "domain_rectangular_bottom_chord",
    ),
    "debug_20c": ("domain_debug_radius_chord",),
}

DOMAIN_KEY_TO_PARAMETER = {
    "domain_circular_radius_chord": "radius",
    "domain_debug_radius_chord": "radius",
    "domain_cgrid_upstream_chord": "upstream",
    "domain_cgrid_downstream_chord": "downstream",
    "domain_cgrid_top_chord": "top",
    "domain_cgrid_bottom_chord": "bottom",
    "domain_rectangular_upstream_chord": "upstream",
    "domain_rectangular_downstream_chord": "downstream",
    "domain_rectangular_top_chord": "top",
    "domain_rectangular_bottom_chord": "bottom",
}


# Geometry cleanup, rounded-TE construction and tangential wall resolution are
# intentionally common to all levels.  Changing level must not change the
# airfoil represented by Gmsh or reintroduce a poor lip/TE discretization.
STANDARD_MESH_VALUES: dict[str, Any] = {
    "run_boundary_layer": True,
    "target_y_plus": 1.0,
    "closed_use_yplus_first_cell_height": True,
    "closed_first_cell_height_m": 2.0e-5,
    "closed_boundary_layer_growth": 1.10,
    "closed_boundary_layer_total_thickness_chord": None,
    "closed_recombine_boundary_layer": True,
    "closed_boundary_layer_aniso_max_deg": 170.0,
    "closed_boundary_layer_intersect_metrics": True,
    "closed_wall_curve_method": "two_spline_te_cap",
    "closed_wall_target_nodes": 2000,
    "closed_profile_preprocess_enabled": True,
    "closed_profile_target_points": 600,
    "closed_profile_min_spacing_chord": 1.2e-4,
    "closed_te_rounding_enabled": True,
    "closed_te_rounding_points": 25,
    "closed_te_rounding_window_chord": 0.01,
    "closed_te_rounding_min_gap_chord": 0.0,
    "closed_te_refinement_width_chord": 0.0,
    "closed_te_refinement_strength": 6.0,
    "closed_te_refinement_max_weight": 9.0,
    "closed_te_target_nodes": 18,
    "closed_te_transition_min_nodes": 30,
    "closed_te_bump_strength": 0.50,
    "closed_near_wall_size_from_bl": True,
    "closed_near_wall_size_chord": 0.0035,
    "closed_near_wall_size_bl_factor": 0.50,
    "open_use_yplus_first_cell_height": False,
    "open_first_cell_height_m": 2.5e-5,
    "open_boundary_layer_growth": 1.075,
    "open_boundary_layer_total_thickness_chord": None,
    "open_recombine_boundary_layer": True,
    "open_boundary_layer_aniso_max_deg": 30.0,
    "open_geometry_representation": "zero_thickness_base_profile",
    "open_base_profile_variant": "reference_uncut",
    "open_base_inlet_alignment_mode": "similarity",
    "open_base_inlet_blend_fraction": 0.30,
    "open_wall_curve_method": "segmented_outer_splines",
    "open_surface_target_nodes": 1920,
    "open_zero_thickness_contour_target_nodes": 2800,
    "open_zero_thickness_inlet_normal_y1_factor": 8.0,
    "open_surface_transfinite_multiplier": 1.0,
    "open_surface_transfinite_progression": 1.0,
    "open_wall_end_bump_enabled": True,
    "open_wall_end_bump_strength": 0.60,
    "open_zero_thickness_te_transfinite_min_nodes": 32,
    "open_te_transfinite_min_nodes": 40,
    "open_te_refinement_width_chord": 0.012,
    "open_te_transition_distance_chord": 0.010,
    "open_lip_transfinite_min_nodes": 160,
    "open_inlet_marker_transfinite_nodes": 176,
    "open_inlet_marker_bump_strength": 0.60,
    "open_inlet_boundary_layer_mode": "full_prismatic_bridge_without_fans",
    "open_inlet_transition_elements": "graded_quads",
    "open_inlet_transition_growth": 1.22,
    "open_inlet_bridge_smoothing_enabled": True,
    "open_inlet_bridge_smoothing_handle_fraction": 0.080,
    "open_inlet_connector_normal_nodes": 0,
    "open_lip_cap_rounding_enabled": False,
    "open_lip_cap_rounding_points": 7,
    "open_boundary_layer_lip_fan_points": 3,
    "open_minimum_fabric_thickness_chord": 5.0e-4,
    "open_surface_size_le_chord": 0.002,
    "open_surface_size_lip_chord": 0.0012,
    "open_surface_size_te_chord": 0.0012,
    "open_inner_wall_node_factor": 0.40,
    "open_inner_te_node_factor": 0.28,
    "open_inner_wall_min_nodes": 80,
    "open_inner_te_min_nodes": 18,
    "open_inner_wall_end_bump_enabled": True,
    "open_inner_wall_end_bump_strength": 0.30,
    "open_internal_inlet_refinement_enabled": True,
    "open_inlet_refinement_bridge_enabled": True,
    "open_cavity_inlet_size_strategy": "hybrid_boundary_extension",
    "open_cavity_inlet_extension_power": 0.75,
    "open_internal_inlet_dist_min_chord": 0.0,
    "open_internal_inlet_matching_transition_chord": 0.0035,
    "open_internal_inlet_matching_size_factor": 1.0,
    "open_internal_inlet_near_transition_chord": 0.035,
    "open_internal_inlet_intermediate_size_chord": 0.0032,
    "open_internal_inlet_dist_max_chord": 0.14,
    "open_internal_te_refinement_enabled": True,
    "open_internal_te_dist_max_chord": 0.09,
    "open_internal_te_size_factor": 0.80,
    "open_transition_sigmoid_enabled": True,
    "open_nearfield_refinement_enabled": True,
    "open_boundary_layer_trim_end_segments": False,
    "open_boundary_layer_trim_end_points": 3,
    "open_near_wall_size_from_bl": True,
    "open_near_wall_size_chord": 0.0035,
    "open_near_wall_size_bl_factor": 0.50,
    "wake_refinement_enabled": False,
}


MESH_LEVEL_OVERRIDES: dict[str, dict[str, Any]] = {
    "coarse": {
        "target_y_plus": 1.0,
        "closed_boundary_layer_layers": 50,
        "closed_nearfield_dist_min_chord": 0.025,
        "closed_nearfield_intermediate_dist_chord": 0.55,
        "closed_nearfield_dist_max_chord": 4.0,
        "closed_nearfield_intermediate_size_chord": 0.075,
        "closed_nearfield_outer_size_chord": 0.30,
        "closed_farfield_transition_dist_chord": 14.0,
        "closed_farfield_size_chord": 3.0,
        "open_use_yplus_first_cell_height": True,
        "open_boundary_layer_layers": 50,
        "open_boundary_layer_growth": 1.10,
        "open_nearfield_dist_min_chord": 0.04,
        "open_nearfield_intermediate_dist_chord": 0.35,
        "open_nearfield_dist_max_chord": 2.0,
        "open_nearfield_intermediate_size_chord": 0.060,
        "open_nearfield_outer_size_chord": 0.30,
        "open_farfield_transition_dist_chord": 12.0,
        "open_farfield_size_chord": 3.0,
        "open_cavity_wall_size_chord": 0.007,
        "open_cavity_wall_transition_chord": 0.18,
        "open_cavity_size_chord": 0.040,
        "open_internal_inlet_size_chord": 0.0006,
    },
    "medium": {
        "target_y_plus": 2.0 / 3.0,
        "closed_boundary_layer_layers": 50,
        "closed_nearfield_dist_min_chord": 0.025,
        "closed_nearfield_intermediate_dist_chord": 0.70,
        "closed_nearfield_dist_max_chord": 5.0,
        "closed_nearfield_intermediate_size_chord": 0.060,
        "closed_nearfield_outer_size_chord": 0.23,
        "closed_farfield_transition_dist_chord": 30.0,
        "closed_farfield_size_chord": 2.5,
        "open_use_yplus_first_cell_height": True,
        "open_boundary_layer_layers": 50,
        "open_boundary_layer_growth": 1.10,
        "open_nearfield_dist_min_chord": 0.04,
        "open_nearfield_intermediate_dist_chord": 0.45,
        "open_nearfield_dist_max_chord": 2.5,
        "open_nearfield_intermediate_size_chord": 0.050,
        "open_nearfield_outer_size_chord": 0.25,
        "open_farfield_transition_dist_chord": 16.0,
        "open_farfield_size_chord": 3.0,
        "open_cavity_wall_size_chord": 0.0055,
        "open_cavity_wall_transition_chord": 0.20,
        "open_cavity_size_chord": 0.038,
        "open_internal_inlet_size_chord": 0.0005,
    },
    "fine": {
        "target_y_plus": 4.0 / 9.0,
        "closed_boundary_layer_layers": 50,
        "closed_nearfield_dist_min_chord": 0.025,
        "closed_nearfield_intermediate_dist_chord": 0.80,
        "closed_nearfield_dist_max_chord": 6.0,
        "closed_nearfield_intermediate_size_chord": 0.050,
        "closed_nearfield_outer_size_chord": 0.18,
        "closed_farfield_transition_dist_chord": 45.0,
        "closed_farfield_size_chord": 2.5,
        "open_use_yplus_first_cell_height": True,
        "open_boundary_layer_layers": 50,
        "open_boundary_layer_growth": 1.10,
        "open_nearfield_dist_min_chord": 0.04,
        "open_nearfield_intermediate_dist_chord": 0.30,
        "open_nearfield_dist_max_chord": 1.6,
        "open_nearfield_intermediate_size_chord": 0.075,
        "open_nearfield_outer_size_chord": 0.35,
        "open_farfield_transition_dist_chord": 10.0,
        "open_farfield_size_chord": 3.5,
        "open_cavity_wall_size_chord": 0.0042,
        "open_cavity_wall_transition_chord": 0.16,
        "open_cavity_size_chord": 0.036,
        "open_internal_inlet_size_chord": 0.0004,
    },
    "extra_fine": {
        "target_y_plus": 8.0 / 27.0,
        "closed_boundary_layer_layers": 75,
        "closed_nearfield_dist_min_chord": 0.020,
        "closed_nearfield_intermediate_dist_chord": 0.90,
        "closed_nearfield_dist_max_chord": 7.0,
        "closed_nearfield_intermediate_size_chord": 0.040,
        "closed_nearfield_outer_size_chord": 0.14,
        "closed_farfield_transition_dist_chord": 45.0,
        "closed_farfield_size_chord": 2.0,
        "open_use_yplus_first_cell_height": True,
        "open_boundary_layer_layers": 75,
        "open_boundary_layer_growth": 1.10,
        "open_nearfield_dist_min_chord": 0.03,
        "open_nearfield_intermediate_dist_chord": 0.35,
        "open_nearfield_dist_max_chord": 2.0,
        "open_nearfield_intermediate_size_chord": 0.050,
        "open_nearfield_outer_size_chord": 0.25,
        "open_farfield_transition_dist_chord": 14.0,
        "open_farfield_size_chord": 3.0,
        "open_cavity_wall_size_chord": 0.0035,
        "open_cavity_wall_transition_chord": 0.16,
        "open_cavity_size_chord": 0.034,
        "open_internal_inlet_size_chord": 0.00035,
    },
}

LEGACY_LEVEL_ALIASES = {
    "debug": "coarse",
    "ross_like": "fine",
}


def domain_parameters(domain: str, config: dict[str, Any] | None = None) -> dict[str, float | str]:
    if domain not in DOMAIN_DEFAULTS:
        raise ValueError(f"Unknown mesh domain: {domain}")
    result = deepcopy(DOMAIN_DEFAULTS[domain])
    config = config or {}
    for key in DOMAIN_CONFIG_KEYS[domain]:
        parameter = DOMAIN_KEY_TO_PARAMETER[key]
        if key in config and config[key] is not None:
            result[parameter] = float(config[key])
    for key, value in result.items():
        if key != "type" and float(value) <= 0.0:
            raise ValueError(f"Domain dimension {key} must be positive, got {value}")
    return result


def mesh_level_values(level: str) -> dict[str, Any]:
    canonical = LEGACY_LEVEL_ALIASES.get(level, level)
    if canonical == "custom":
        return {}
    if canonical not in MESH_LEVEL_OVERRIDES:
        raise ValueError(f"Unknown mesh level: {level}")
    values = deepcopy(STANDARD_MESH_VALUES)
    values.update(deepcopy(MESH_LEVEL_OVERRIDES[canonical]))
    values["mesh_level_origin"] = canonical
    values["mesh_configuration_mode"] = "level_base"
    return values


def apply_mesh_level(config: dict[str, Any], level: str) -> dict[str, Any]:
    """Apply an explicit new-mesh baseline while preserving domain choices."""
    updated = deepcopy(config)
    updated.update(mesh_level_values(level))
    return updated
