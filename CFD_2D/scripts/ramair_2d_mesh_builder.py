#!/usr/bin/env python3
"""Robust Gmsh mesh builder for ram-air 2D profile case packages.

Canonical script name: ramair_2d_mesh_builder.py

This version focuses on reliable profile reading, branch/patch diagnostics and a
valid Gmsh domain for the first verification step: the closed/reference LS1-0417
profile with an external fluid mesh. Open ram-air profiles are written with clear
metadata and non-physical inlet-opening markers, but the recommended first debug
case is reference_uncut.

The script never runs a CFD solver and never approves a mesh automatically.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from openfoam_environment import activate_openfoam_environment
from mesh_configuration import DOMAIN_DEFAULTS, domain_parameters, mesh_level_values
from ramair_2d_mesh_science import first_cell_height_audit

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from ramair_2d_mesh_quality_controller import decide_quality, write_quality_report, update_mesh_config_for_remesh
except Exception:  # pragma: no cover
    decide_quality = write_quality_report = update_mesh_config_for_remesh = None

SUPPORTED_VARIANTS = {
    "open_ramair",
    "closed_reference",
    "reference_uncut",
    "reference_uncut_validation_1m",
    "reference_uncut_validation_1m_coarse",
    "reference_uncut_validation_1m_fine",
    "ross_standard_8p4",
    "ross_minimum_4p0",
    "standard",
    "optimized",
}
VARIANT_ALIASES = {"standard": "open_ramair", "optimized": "open_ramair"}
DOMAINS = set(DOMAIN_DEFAULTS)
MESH_LEVELS = {"debug", "coarse", "medium", "fine", "extra_fine", "ross_like", "custom"}
CATIA_INPUTS_DIR_NAME = "CATIA/Inputs"
CFD_ROOT_DIR_NAME = "CFD_2D"
CFD_INPUTS_DIR_NAME = "CFD_2D_inputs"

DEFAULT_MESH_CONFIG = {
    "config_schema_version": 3,
    "gmsh_backend": "auto",
    "geometry_mode": "thin_solid_fabric",
    "fabric_thickness_chord": 1.0e-5,
    "run_boundary_layer": True,
    "target_y_plus": 1.0,
    "closed_use_yplus_first_cell_height": True,
    "closed_first_cell_height_m": 2.0e-5,
    "closed_boundary_layer_layers": 50,
    "closed_boundary_layer_growth": 1.10,
    "closed_boundary_layer_total_thickness_chord": None,
    "closed_recombine_boundary_layer": True,
    "closed_boundary_layer_aniso_max_deg": 170.0,
    "closed_boundary_layer_intersect_metrics": True,
    "closed_wall_curve_method": "two_spline_te_cap",
    "closed_wall_target_nodes": 2000,
    "closed_te_bump_strength": 0.50,
    "closed_profile_preprocess_enabled": True,
    "closed_profile_target_points": 600,
    "closed_profile_min_spacing_chord": 1.2e-4,
    "closed_te_rounding_enabled": True,
    "closed_te_rounding_points": 25,
    "closed_te_rounding_window_chord": 0.010,
    "closed_te_rounding_min_gap_chord": 0.0,
    "closed_te_refinement_width_chord": 0.0,
    "closed_te_refinement_strength": 6.0,
    "closed_te_refinement_max_weight": 9.0,
    "closed_near_wall_size_from_bl": True,
    "closed_near_wall_size_chord": 0.0035,
    "closed_near_wall_size_bl_factor": 0.50,
    "closed_farfield_size_chord": 1.0,
    "closed_nearfield_enabled": True,
    "closed_nearfield_dist_min_chord": 0.025,
    "closed_nearfield_intermediate_dist_chord": 0.65,
    "closed_nearfield_dist_max_chord": 4.0,
    "closed_nearfield_intermediate_size_chord": 0.035,
    "closed_nearfield_outer_size_chord": 0.18,
    "closed_farfield_transition_dist_chord": 9.0,
    "domain_radius_chord": 10.0,
    "first_cell_height_chord_override": None,
    "boundary_layer_layers": 50,
    "boundary_layer_growth": 1.10,
    "boundary_layer_growth_max": 1.20,
    "boundary_layer_total_thickness_chord_override": None,
    "boundary_layer_exclude_te_cap_from_bl": False,
    "closed_airfoil_transfinite_enabled": True,
    "closed_airfoil_target_nodes": 720,
    "closed_airfoil_transfinite_progression": 1.0,
    "closed_te_target_nodes": 18,
    "closed_te_transition_min_nodes": 30,
    "closed_te_neighbor_bump_enabled": True,
    "closed_te_neighbor_bump": 0.08,
    "closed_te_cap_distribution": "uniform",
    "closed_te_cap_progression": 1.0,
    "closed_single_curve_experimental": False,
    "closed_single_curve_kind": "Spline",
    "closed_single_curve_target_nodes": 720,
    "closed_single_curve_start_at_te": True,
    "closed_single_curve_distribution": "bump",
    "closed_single_curve_bump": 0.06,
    "surface_size_from_boundary_layer_enabled": True,
    "surface_size_bl_outer_factor": 0.50,
    "surface_size_bl_outer_min_chord": 0.0015,
    "surface_size_bl_outer_max_chord": 0.006,
    "gmsh_mesh_algorithm_2d": 5,
    "gmsh_random_factor": 1.0e-7,
    "gmsh_random_seed": 1,
    "surface_size_general_chord": 0.0075,
    "surface_size_inlet_lips_chord": 0.00075,
    "surface_size_te_chord": 0.001,
    "surface_size_rounded_te_chord": 0.0012,
    "wake_refinement_length_chord": 10.0,
    "wake_refinement_height_chord": 0.5,
    "wake_size_chord": 0.01,
    "farfield_size_chord": 0.35,
    "cavity_mesh_mode": "coarse_validated",
    "cavity_size_chord": 0.01,
    "mesh_algorithm": "frontal_delaunay",
    "request_boundary_layer": True,
    "recombine_boundary_layer": False,
    "extrude_to_3d_for_openfoam": True,
    "spanwise_thickness_chord": 0.01,
    "spanwise_layers": 1,
    "gmsh_threads": max(1, min(12, os.cpu_count() or 1)),
    "max_cells": 2_000_000,
    "min_cells_warning": 1000,
    "max_internal_parse_mesh_size_mb": 80,
    "max_internal_parse_elements": 75_000,
    "debug_te_rounding_enabled": False,
    "debug_te_rounding_points": 41,
    "debug_te_rounding_window_chord": 0.055,
    "debug_te_rounding_min_gap_chord": 2.0e-4,
    "debug_te_refinement_width_chord": 0.025,
    "debug_te_refinement_strength": 6.0,
    "debug_te_refinement_max_weight": 9.0,
    "debug_airfoil_curve_mode": "hybrid_te_spline",
    "debug_airfoil_transfinite": False,
    "debug_airfoil_transfinite_node_multiplier": 1.0,
    "debug_te_curve_line_window_chord": 0.008,
    "debug_te_cap_spline_segments": 3,
    "debug_enforce_rounded_te_curve_sections": True,
    "debug_te_transfinite_enabled": True,
    "debug_te_transfinite_min_nodes_per_curve": 8,
    "debug_boundary_layer_fan_at_te": False,
    "debug_boundary_layer_te_fan_points": 64,
    "nearfield_intermediate_dist_chord": 0.45,
    "nearfield_intermediate_size_chord": 0.035,
    "open_connected_fluid_surface": False,
    "open_thin_solid_fluid_surface": True,
    "open_geometry_representation": "zero_thickness_base_profile",
    "open_base_profile_variant": "reference_uncut",
    "open_base_inlet_alignment_mode": "similarity",
    "open_base_inlet_blend_fraction": 0.30,
    "open_single_connected_surface_2d": False,
    "open_wall_curve_method": "segmented_outer_splines",
    "open_use_yplus_first_cell_height": False,
    "open_first_cell_height_m": 2.5e-5,
    "open_near_wall_size_from_bl": True,
    "open_near_wall_size_chord": 0.010,
    "open_near_wall_size_bl_factor": 0.70,
    "open_minimum_fabric_thickness_chord": 4.0e-4,
    "open_mesh_internal_cavity": True,
    "open_boundary_layer_split_curvature_sections": True,
    "open_split_wall_curve_kind": "Spline",
    "open_outer_wall_curve_kind": "BSpline",
    "open_surface_transfinite_multiplier": 1.0,
    "open_surface_target_nodes": 1600,
    "open_zero_thickness_contour_target_nodes": 2800,
    "open_zero_thickness_inlet_normal_y1_factor": 8.0,
    "open_surface_transfinite_progression": 1.0,
    "open_wall_end_bump_enabled": True,
    "open_wall_end_bump_strength": 0.60,
    "open_zero_thickness_te_transfinite_min_nodes": 32,
    "open_te_transfinite_min_nodes": 40,
    "open_lip_transfinite_min_nodes": 160,
    "open_inner_wall_node_factor": 0.40,
    "open_inner_te_node_factor": 0.28,
    "open_inner_wall_min_nodes": 80,
    "open_inner_te_min_nodes": 18,
    "open_inner_wall_end_bump_enabled": True,
    "open_inner_wall_end_bump_strength": 0.30,
    "open_inlet_boundary_layer_mode": "full_prismatic_bridge_without_fans",
    "open_inlet_transition_elements": "graded_quads",
    "open_inlet_transition_growth": 1.22,
    "open_inlet_bridge_smoothing_enabled": True,
    "open_inlet_bridge_smoothing_handle_fraction": 0.080,
    "open_lip_cap_rounding_enabled": False,
    "open_lip_cap_rounding_points": 7,
    "open_boundary_layer_include_inlet_bridge": True,
    "open_boundary_layer_single_loop_bspline": True,
    "open_boundary_layer_single_loop_curve_kind": "Spline",
    "open_boundary_layer_single_loop_transfinite": True,
    "open_internal_cavity_curve_mode": "spline",
    "open_te_rounding_enabled": True,
    "open_te_rounding_points": 91,
    "open_te_refinement_width_chord": 0.012,
    "open_te_transition_distance_chord": 0.010,
    "open_surface_size_general_chord": 0.004,
    "open_surface_size_le_chord": 0.002,
    "open_surface_size_lip_chord": 0.0012,
    "open_surface_size_te_chord": 0.0012,
    "open_farfield_size_chord": 0.75,
    "open_cavity_wall_size_chord": 0.0035,
    "open_cavity_wall_transition_chord": 0.22,
    "open_cavity_size_chord": 0.036,
    "open_cavity_inlet_size_strategy": "hybrid_boundary_extension",
    "open_cavity_inlet_extension_power": 0.75,
    "open_internal_te_refinement_enabled": True,
    "open_internal_te_dist_max_chord": 0.10,
    "open_internal_te_size_factor": 0.75,
    "open_first_cell_height_chord_override": None,
    "open_boundary_layer_layers": 50,
    "open_boundary_layer_growth": 1.075,
    "open_surface_size_from_boundary_layer_enabled": True,
    "open_surface_size_bl_outer_factor": 0.50,
    "open_surface_size_bl_outer_min_chord": 0.0015,
    "open_surface_size_bl_outer_max_chord": 0.008,
    "open_boundary_layer_aniso_max_deg": 30.0,
    "open_recombine_boundary_layer": True,
    "open_boundary_layer_total_thickness_chord_override": None,
    "open_diagnostic_boundary_layer_enabled": True,
    "open_boundary_layer_exclude_te_cap_from_bl": False,
    "open_boundary_layer_trim_end_segments": False,
    "open_boundary_layer_trim_ends_chord": 0.0,
    "open_boundary_layer_trim_end_points": 3,
    "open_boundary_layer_fan_at_lips": True,
    "open_boundary_layer_lip_fan_points": 5,
    "open_boundary_layer_include_inlet_marker": True,
    "open_duplicate_internal_inlet_marker_for_bl": False,
    "open_inlet_marker_transfinite_enabled": True,
    "open_inlet_marker_transfinite_nodes": 176,
    "open_inlet_marker_bump_strength": 0.60,
    "open_inlet_connector_normal_nodes": 0,
    "open_nearfield_refinement_enabled": True,
    "open_nearfield_dist_min_chord": 0.040,
    "open_nearfield_intermediate_dist_chord": 0.30,
    "open_nearfield_dist_max_chord": 3.00,
    "open_nearfield_intermediate_size_chord": 0.080,
    "open_nearfield_outer_size_chord": 0.20,
    "open_farfield_transition_dist_chord": 15.0,
    "open_transition_sigmoid_enabled": True,
    "open_nearfield_distance_sampling": 240,
    "open_internal_inlet_refinement_enabled": True,
    "open_inlet_refinement_bridge_enabled": True,
    "open_internal_inlet_dist_min_chord": 0.0,
    "open_internal_inlet_matching_transition_chord": 0.0035,
    "open_internal_inlet_matching_size_factor": 1.0,
    "open_internal_inlet_near_transition_chord": 0.04,
    "open_internal_inlet_intermediate_size_chord": 0.0035,
    "open_internal_inlet_dist_max_chord": 0.18,
    "open_internal_inlet_size_chord": 0.0012,
    "open_internal_inlet_distance_sampling": 140,
    "open_le_refinement_enabled": False,
    "open_le_refinement_width_chord": 0.18,
    "open_le_refinement_extent_chord": 0.28,
    "open_le_refinement_height_chord": 0.16,
    "open_le_refinement_transition_chord": 0.18,
    "open_lip_refinement_enabled": False,
    "open_lip_refinement_x_chord": 0.045,
    "open_lip_refinement_z_chord": 0.045,
    "open_lip_refinement_transition_chord": 0.08,
    "gmsh_boundary_layer_fallback_no_bl": False,
}


def read_json(path: Path, default: Any = None) -> Any:
    source = Path(path)
    if not source.exists():
        return default
    try:
        # utf-8-sig accepts both ordinary UTF-8 and the BOM emitted by some
        # Windows editors/PowerShell versions.
        return json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON configuration {source}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@contextmanager
def variant_mesh_lock(case_root: Path, variant: str):
    """Prevent concurrent builders from corrupting one variant directory."""
    lock_dir = cfd_root(case_root) / "app_state" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"mesh_{variant}.lock"
    payload = {
        "pid": os.getpid(),
        "variant": variant,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for _ in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = read_json(lock_path, {}) or {}
            existing_pid = int(existing.get("pid", 0) or 0)
            process_alive = False
            if existing_pid > 0:
                try:
                    os.kill(existing_pid, 0)
                    process_alive = True
                except OSError:
                    process_alive = False
            if process_alive:
                raise RuntimeError(
                    f"Another mesh builder is already active for {variant}: PID {existing_pid}; "
                    f"lock={lock_path}"
                )
            lock_path.unlink(missing_ok=True)
            continue
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            break
    else:
        raise RuntimeError(f"Could not acquire mesh lock: {lock_path}")
    try:
        yield lock_path
    finally:
        current = read_json(lock_path, {}) or {}
        if int(current.get("pid", 0) or 0) == os.getpid():
            lock_path.unlink(missing_ok=True)


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(v: Any, default: float) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def project_root_from_case_root(case_root: Path) -> Path:
    case_root = Path(case_root)
    if case_root.name == CATIA_INPUTS_DIR_NAME:
        return case_root.parent
    return case_root


def cfd_root(case_root: Path) -> Path:
    return project_root_from_case_root(case_root) / CFD_ROOT_DIR_NAME


def cfd_inputs_root(case_root: Path) -> Path:
    return cfd_root(case_root) / CFD_INPUTS_DIR_NAME


def cfd_meshes_root(case_root: Path) -> Path:
    return cfd_root(case_root) / "meshes"


def _prompt_previous_output_action(label: str, path: Path, default: str = "archive") -> str:
    """Ask whether to archive or delete previous heavy outputs."""
    default = default if default in {"archive", "delete", "keep"} else "archive"
    if not sys.stdin.isatty():
        return default
    print(f"Previous {label} exists: {path}")
    print("Type S/ARCHIVE to keep a backup, N/DELETE to remove it, K/KEEP to leave it, or press Enter for backup.")
    ans = input(f"Action for previous {label} [S=archive/N=delete/K=keep]: ").strip().lower()
    if ans in {"n", "no", "delete", "d"}:
        return "delete"
    if ans in {"k", "keep"}:
        return "keep"
    return "archive"


def backup_existing_mesh_root(case_root: Path, mesh_root: Path, variant: str, action: str = "archive") -> Path | None:
    """Move or delete previous generated mesh outputs before a clean remesh."""
    if not mesh_root.exists():
        return None
    action = action if action in {"ask", "archive", "delete", "keep"} else "archive"
    if action == "ask":
        action = _prompt_previous_output_action("mesh directory", mesh_root)
    if action == "keep":
        return None
    if action == "delete":
        project_root = project_root_from_case_root(Path(case_root)).resolve()
        target = mesh_root.resolve()
        expected_parent = (project_root / CFD_ROOT_DIR_NAME / "meshes").resolve()
        if target.parent != expected_parent:
            raise RuntimeError(f"Refusing to delete unexpected mesh path: {target}")
        shutil.rmtree(target)
        print(f"Deleted previous mesh outputs: {target}")
        return None
    project_root = project_root_from_case_root(Path(case_root))
    backup_root = project_root / "Previous Versions" / "mesh_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = backup_root / f"{variant}_{stamp}"
    suffix = 1
    while target.exists():
        target = backup_root / f"{variant}_{stamp}_{suffix:02d}"
        suffix += 1
    shutil.move(str(mesh_root), str(target))
    manifest = {
        "variant": variant,
        "backup_created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_mesh_root": str(mesh_root),
        "reason": "clean_remesh_overwrite",
    }
    write_json(target / "mesh_backup_manifest.json", manifest)
    return target


def _safe_run_id() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"


def backup_existing_attempt_dir(case_root: Path, attempt_dir: Path, variant: str, attempt: int, run_id: str) -> Path | None:
    """Archive an existing mesh_attempt_* folder so stale .msh files cannot be reused."""
    if not attempt_dir.exists():
        return None
    project_root = project_root_from_case_root(Path(case_root))
    backup_root = project_root / "Previous Versions" / "mesh_attempt_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / f"{variant}_attempt_{attempt:03d}_{run_id}"
    suffix = 1
    while target.exists():
        target = backup_root / f"{variant}_attempt_{attempt:03d}_{run_id}_{suffix:02d}"
        suffix += 1
    shutil.move(str(attempt_dir), str(target))
    write_json(target / "mesh_attempt_backup_manifest.json", {
        "variant": variant,
        "attempt": attempt,
        "backup_created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_attempt_dir": str(attempt_dir),
        "reason": "avoid_reusing_stale_attempt_outputs",
        "run_id": run_id,
    })
    return target


def backup_active_mesh_outputs(case_root: Path, mesh_root: Path, variant: str, run_id: str, action: str = "archive") -> Path | None:
    """Archive or delete active final mesh outputs before a new non-dry Gmsh run."""
    names = [
        "mesh_final.geo",
        "mesh_final.msh",
        "mesh_quality_report.json",
        "mesh_quality_report.txt",
        "mesh_quality_report.html",
        "airfoil_wall_curve_connectivity_audit.json",
        "airfoil_wall_curve_connectivity_audit.csv",
        "log.gmsh",
        "log.gmshToFoam",
        "log.checkMesh",
        "log.checkMesh.locations",
        "checkMesh_problem_locations",
        "checkMesh_problem_locations.json",
        "checkMesh_problem_locations.txt",
        "checkMesh_problem_sets",
        "checkMesh_problem_viewer.py",
        "checkMesh_problem_view.png",
        "checkMesh_quality.foam",
        "remeshing_history.csv",
        "mesh_build_manifest.json",
        "MESH_APPROVED.flag",
        "mesh_preview_full.png",
        "mesh_preview_wake.png",
        "mesh_preview_airfoil.png",
        "mesh_preview_front_surface.png",
        "mesh_preview_inlet.png",
        "mesh_preview_te.png",
        "geometry_preview_airfoil.png",
        "geometry_preview_inlet.png",
        "geometry_preview_te.png",
        "profile_preprocessing_distribution.png",
        "open_te_rounding_geometry_zoom.png",
        "constant/polyMesh",
        "openfoam_mesh_check_case",
    ]
    existing = [mesh_root / name for name in names if (mesh_root / name).exists()]
    if not existing:
        return None
    action = action if action in {"ask", "archive", "delete", "keep"} else "archive"
    if action == "ask":
        action = _prompt_previous_output_action("active mesh outputs", mesh_root)
    if action == "keep":
        return None
    if action == "delete":
        removed: list[str] = []
        for src in sorted(existing, key=lambda p: len(p.parts), reverse=True):
            if not src.exists():
                continue
            rel = src.relative_to(mesh_root)
            if src.is_dir():
                shutil.rmtree(src)
            else:
                src.unlink()
            removed.append(str(rel).replace("\\", "/"))
        print(f"Deleted previous active mesh outputs: {', '.join(removed)}")
        return None
    project_root = project_root_from_case_root(Path(case_root))
    backup_root = project_root / "Previous Versions" / "mesh_active_output_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / f"{variant}_{run_id}"
    suffix = 1
    while target.exists():
        target = backup_root / f"{variant}_{run_id}_{suffix:02d}"
        suffix += 1
    moved: list[str] = []
    for src in existing:
        if not src.exists():
            continue
        rel = src.relative_to(mesh_root)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(str(rel).replace("\\", "/"))
    write_json(target / "mesh_active_outputs_backup_manifest.json", {
        "variant": variant,
        "backup_created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_mesh_root": str(mesh_root),
        "reason": "avoid_reusing_stale_final_outputs",
        "run_id": run_id,
        "moved": moved,
    })
    return target


def copy_checked_polymesh_to_active(case_root: Path, mesh_root: Path, variant: str, src_poly: Path, run_id: str) -> None:
    """Copy the latest checked polyMesh to mesh_root/constant/polyMesh, archiving an old one first."""
    if not (src_poly / "boundary").exists():
        return
    dst_poly = mesh_root / "constant" / "polyMesh"
    if dst_poly.exists():
        project_root = project_root_from_case_root(Path(case_root))
        backup_root = project_root / "Previous Versions" / "mesh_polymesh_backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        target = backup_root / f"{variant}_{run_id}"
        suffix = 1
        while target.exists():
            target = backup_root / f"{variant}_{run_id}_{suffix:02d}"
            suffix += 1
        shutil.move(str(dst_poly), str(target))
        write_json(target / "polyMesh_backup_manifest.json", {
            "variant": variant,
            "backup_created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_polyMesh": str(dst_poly),
            "reason": "replace_with_latest_checked_polymesh",
            "run_id": run_id,
        })
    dst_poly.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_poly, dst_poly)


def load_case_variant(case_root: Path, variant: str) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict, Path]:
    geom_variant = VARIANT_ALIASES.get(variant, variant)
    roots = [
        cfd_inputs_root(case_root) / "case_package" / geom_variant,
        cfd_inputs_root(case_root) / "geometry" / geom_variant,
        cfd_root(case_root) / "inputs" / "case_package" / geom_variant,
        cfd_root(case_root) / "inputs" / "geometry" / geom_variant,
        Path(case_root) / "02_CFD_2D" / "case_package" / geom_variant,
        Path(case_root) / "02_CFD_2D" / "geometry" / geom_variant,
    ]
    for root in roots:
        if (root / "points.csv").exists() and (root / "edges.csv").exists():
            points = pd.read_csv(root / "points.csv")
            edges = pd.read_csv(root / "edges.csv")
            patches = read_json(root / "patches.json", {}) or {}
            manifest = read_json(root / "manifest.json", None) or read_json(root / "profile_manifest.json", {}) or {"variant": geom_variant}
            return normalize_points(points), normalize_edges(edges), patches, manifest, root
        if (root / "profile_points.csv").exists() and (root / "profile_edges.csv").exists():
            points = pd.read_csv(root / "profile_points.csv")
            edges = pd.read_csv(root / "profile_edges.csv")
            patches = read_json(root / "profile_patches.json", {}) or {}
            manifest = read_json(root / "profile_manifest.json", {}) or {"variant": geom_variant}
            return normalize_points(points), normalize_edges(edges), patches, manifest, root
    raise FileNotFoundError(
        f"Variant '{variant}' not found. Run ramair_2d_profile_case_builder.py first. Searched: "
        + ", ".join(str(r) for r in roots)
    )


def normalize_points(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "point_id" not in df.columns:
        df["point_id"] = np.arange(1, len(df) + 1)
    if "x_m" not in df.columns:
        if "x_norm" in df.columns:
            df["x_m"] = df["x_norm"].astype(float)
        elif "x" in df.columns:
            df["x_m"] = df["x"].astype(float)
        else:
            raise ValueError("points.csv must contain x_m, x_norm or x")
    if "z_m" not in df.columns:
        if "z_norm" in df.columns:
            df["z_m"] = df["z_norm"].astype(float)
        elif "z" in df.columns:
            df["z_m"] = df["z"].astype(float)
        elif "y" in df.columns:
            df["z_m"] = df["y"].astype(float)
        else:
            raise ValueError("points.csv must contain z_m, z_norm, z or y")
    df["point_id"] = df["point_id"].astype(int)
    df["x_m"] = df["x_m"].astype(float)
    df["z_m"] = df["z_m"].astype(float)
    return df.reset_index(drop=True)


def normalize_edges(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    required = {"start_point_id", "end_point_id"}
    if not required.issubset(df.columns):
        raise ValueError("edges.csv must contain start_point_id and end_point_id")
    if "edge_id" not in df.columns:
        df["edge_id"] = np.arange(1, len(df) + 1)
    if "patch_name" not in df.columns:
        df["patch_name"] = "airfoil_wall"
    df["edge_id"] = df["edge_id"].astype(int)
    df["start_point_id"] = df["start_point_id"].astype(int)
    df["end_point_id"] = df["end_point_id"].astype(int)
    df["patch_name"] = df["patch_name"].astype(str)
    return df.sort_values("edge_id").reset_index(drop=True)


def domain_params(domain: str, mesh_cfg: dict[str, Any] | None = None) -> dict[str, float | str]:
    """Resolve the only authoritative set of domain dimensions."""
    return domain_parameters(domain, mesh_cfg)


def farfield_geometry_lines(
    base: int,
    domain: str,
    mesh_cfg: dict[str, Any],
    chord_m: float,
) -> tuple[list[str], list[int]]:
    """Return a closed, counter-clockwise farfield boundary.

    ``ross_cgrid_like`` uses a rounded upstream boundary and a straight wake
    side.  It remains an unstructured C-grid-like outer contour; it does not
    claim a fully block-structured C-grid topology.
    """
    dpar = domain_params(domain, mesh_cfg)
    kind = str(dpar["type"])
    if kind == "rectangle":
        up = -float(dpar["upstream"]) * chord_m
        down = float(dpar["downstream"]) * chord_m
        top = float(dpar["top"]) * chord_m
        bot = -float(dpar["bottom"]) * chord_m
        return ([
            f"Point({base+1}) = {{{up:.12g}, {bot:.12g}, 0, lc_farfield}};",
            f"Point({base+2}) = {{{down:.12g}, {bot:.12g}, 0, lc_farfield}};",
            f"Point({base+3}) = {{{down:.12g}, {top:.12g}, 0, lc_farfield}};",
            f"Point({base+4}) = {{{up:.12g}, {top:.12g}, 0, lc_farfield}};",
            f"Line({base+10}) = {{{base+1}, {base+2}}};",
            f"Line({base+11}) = {{{base+2}, {base+3}}};",
            f"Line({base+12}) = {{{base+3}, {base+4}}};",
            f"Line({base+13}) = {{{base+4}, {base+1}}};",
        ], [base + 10, base + 11, base + 12, base + 13])
    if kind == "cgrid":
        cx = 0.5 * chord_m
        x_left = -float(dpar["upstream"]) * chord_m
        x_right = float(dpar["downstream"]) * chord_m
        top = float(dpar["top"]) * chord_m
        bottom = float(dpar["bottom"]) * chord_m
        arc_ids: list[int] = []
        result = [
            f"Point({base+1}) = {{{cx:.12g}, {-bottom:.12g}, 0, lc_farfield}};",
            f"Point({base+2}) = {{{x_right:.12g}, {-bottom:.12g}, 0, lc_farfield}};",
            f"Point({base+3}) = {{{x_right:.12g}, {top:.12g}, 0, lc_farfield}};",
            f"Point({base+4}) = {{{cx:.12g}, {top:.12g}, 0, lc_farfield}};",
        ]
        arc_ids.append(base + 4)
        for index, theta in enumerate(np.linspace(math.pi / 2.0, -math.pi / 2.0, 25)[1:-1]):
            pid = base + 20 + index
            radius_x = cx - x_left
            x = cx - radius_x * math.cos(float(theta))
            z = (top if theta >= 0.0 else bottom) * math.sin(float(theta))
            result.append(f"Point({pid}) = {{{x:.12g}, {z:.12g}, 0, lc_farfield}};")
            arc_ids.append(pid)
        arc_ids.append(base + 1)
        result.extend([
            f"Line({base+10}) = {{{base+1}, {base+2}}};",
            f"Line({base+11}) = {{{base+2}, {base+3}}};",
            f"Line({base+12}) = {{{base+3}, {arc_ids[0]}}};",
            f"Spline({base+13}) = {{{', '.join(map(str, arc_ids))}}};",
        ])
        return result, [base + 10, base + 11, base + 12, base + 13]
    radius = float(dpar["radius"]) * chord_m
    cx = 0.5 * chord_m
    cz = 0.0
    return ([
        f"Point({base+1}) = {{{cx:.12g}, {cz:.12g}, 0, lc_farfield}};",
        f"Point({base+2}) = {{{cx+radius:.12g}, {cz:.12g}, 0, lc_farfield}};",
        f"Point({base+3}) = {{{cx:.12g}, {cz+radius:.12g}, 0, lc_farfield}};",
        f"Point({base+4}) = {{{cx-radius:.12g}, {cz:.12g}, 0, lc_farfield}};",
        f"Point({base+5}) = {{{cx:.12g}, {cz-radius:.12g}, 0, lc_farfield}};",
        f"Circle({base+10}) = {{{base+2}, {base+1}, {base+3}}};",
        f"Circle({base+11}) = {{{base+3}, {base+1}, {base+4}}};",
        f"Circle({base+12}) = {{{base+4}, {base+1}, {base+5}}};",
        f"Circle({base+13}) = {{{base+5}, {base+1}, {base+2}}};",
    ], [base + 10, base + 11, base + 12, base + 13])


def _cfg_first_present(cfg: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in cfg:
            return cfg[key]
    return default


def _normalize_first_cell_aliases(
    cfg: dict[str, Any],
    *,
    use_yplus_key: str,
    manual_m_key: str,
    legacy_manual_chord_key: str,
    legacy_key: str,
    meter_override_key: str,
) -> None:
    use_yplus = cfg.get(use_yplus_key)
    if use_yplus is None:
        cfg[use_yplus_key] = cfg.get(legacy_key) is None and cfg.get(meter_override_key) is None
    elif _bool(use_yplus):
        cfg[legacy_key] = None
        cfg[meter_override_key] = None
    else:
        manual_m = cfg.get(manual_m_key)
        legacy_manual = cfg.get(legacy_manual_chord_key)
        if legacy_manual is not None:
            cfg[meter_override_key] = None
            cfg[legacy_key] = legacy_manual
        elif manual_m is not None:
            cfg[meter_override_key] = float(manual_m)
            cfg[legacy_key] = None
        else:
            cfg[meter_override_key] = None
            cfg[legacy_key] = cfg.get(legacy_manual_chord_key, cfg.get(legacy_key))

    # Preserve old project files without presenting chord-normalized y1 as the
    # preferred interface. The conversion to metres requires the case chord and
    # therefore happens later in boundary_layer_parameters().
    if manual_m_key not in cfg:
        cfg[manual_m_key] = None


def normalize_mesh_config_aliases(cfg: dict[str, Any]) -> dict[str, Any]:
    """Map the compact user-facing JSON keys onto the historical builder keys.

    The builder still accepts the older flat keys used by previous debug
    scripts, but the editable config now exposes fewer, clearer controls.
    """
    cfg = dict(cfg)
    if "run_boundary_layer" in cfg:
        cfg["request_boundary_layer"] = _bool(cfg.get("run_boundary_layer"))
    else:
        cfg["run_boundary_layer"] = bool(cfg.get("request_boundary_layer", True))

    # Closed/reference profile aliases.
    cfg["boundary_layer_layers"] = int(
        _cfg_first_present(cfg, "closed_boundary_layer_layers", "boundary_layer_layers", default=50) or 0
    )
    cfg["closed_boundary_layer_layers"] = int(cfg["boundary_layer_layers"])
    cfg["boundary_layer_growth"] = float(
        _cfg_first_present(cfg, "closed_boundary_layer_growth", "boundary_layer_growth", default=1.10) or 1.0
    )
    cfg["closed_boundary_layer_growth"] = float(cfg["boundary_layer_growth"])
    if "closed_boundary_layer_total_thickness_chord" in cfg:
        cfg["boundary_layer_total_thickness_chord_override"] = cfg.get("closed_boundary_layer_total_thickness_chord")
    else:
        cfg["closed_boundary_layer_total_thickness_chord"] = cfg.get("boundary_layer_total_thickness_chord_override")
    if "closed_recombine_boundary_layer" in cfg:
        cfg["recombine_boundary_layer"] = _bool(cfg.get("closed_recombine_boundary_layer"))
    else:
        cfg["closed_recombine_boundary_layer"] = bool(cfg.get("recombine_boundary_layer", True))
    _normalize_first_cell_aliases(
        cfg,
        use_yplus_key="closed_use_yplus_first_cell_height",
        manual_m_key="closed_first_cell_height_m",
        legacy_manual_chord_key="closed_first_cell_height_chord",
        legacy_key="first_cell_height_chord_override",
        meter_override_key="closed_first_cell_height_m_override",
    )

    closed_method = str(cfg.get("closed_wall_curve_method", "two_spline_te_cap")).strip().lower()
    cfg["closed_wall_curve_method"] = closed_method
    if closed_method in {"single_bspline_bump", "single_bspline", "bspline_bump"}:
        cfg["closed_single_curve_experimental"] = True
        cfg["closed_single_curve_kind"] = "BSpline"
        cfg["closed_single_curve_start_at_te"] = True
        cfg["closed_single_curve_distribution"] = "bump"
        cfg["debug_airfoil_curve_mode"] = "closed_bspline"
    elif closed_method in {"single_spline_bump", "single_spline"}:
        cfg["closed_single_curve_experimental"] = True
        cfg["closed_single_curve_kind"] = "Spline"
        cfg["closed_single_curve_start_at_te"] = True
        cfg["closed_single_curve_distribution"] = "bump"
        cfg["debug_airfoil_curve_mode"] = "closed_spline"
    elif closed_method in {"two_spline_te_cap", "two_splines"}:
        cfg["closed_single_curve_experimental"] = False
        cfg["debug_airfoil_curve_mode"] = "hybrid_te_spline"
        cfg["debug_te_cap_spline_segments"] = 1
        cfg["closed_te_neighbor_bump_enabled"] = True
    elif closed_method in {"segmented_te_spline", "hybrid_te_spline"}:
        cfg["closed_single_curve_experimental"] = False
        cfg["debug_airfoil_curve_mode"] = "hybrid_te_spline"
    elif closed_method in {"line_segments", "lines"}:
        cfg["closed_single_curve_experimental"] = False
        cfg["debug_airfoil_curve_mode"] = "line_segments"
    else:
        raise ValueError(
            "closed_wall_curve_method must be two_spline_te_cap, single_spline_bump, "
            "single_bspline_bump, segmented_te_spline or line_segments."
        )

    if "closed_wall_target_nodes" in cfg:
        cfg["closed_single_curve_target_nodes"] = int(cfg.get("closed_wall_target_nodes") or cfg.get("closed_single_curve_target_nodes", 420))
        cfg["closed_airfoil_target_nodes"] = int(cfg.get("closed_wall_target_nodes") or cfg.get("closed_airfoil_target_nodes", 420))
    else:
        cfg["closed_wall_target_nodes"] = int(cfg.get("closed_single_curve_target_nodes", cfg.get("closed_airfoil_target_nodes", 420)) or 420)
    if "closed_te_bump_strength" in cfg:
        cfg["closed_single_curve_bump"] = float(cfg.get("closed_te_bump_strength") or cfg.get("closed_single_curve_bump", 0.06))
    else:
        cfg["closed_te_bump_strength"] = float(cfg.get("closed_single_curve_bump", 0.06) or 0.06)
    if closed_method in {"two_spline_te_cap", "two_splines"}:
        cfg["closed_te_neighbor_bump"] = float(cfg["closed_te_bump_strength"])
    if "closed_profile_preprocess_enabled" in cfg:
        cfg["debug_simplify_profile"] = _bool(cfg.get("closed_profile_preprocess_enabled"))
        cfg["debug_profile_preprocess"] = _bool(cfg.get("closed_profile_preprocess_enabled"))
    else:
        cfg["closed_profile_preprocess_enabled"] = bool(cfg.get("debug_profile_preprocess", cfg.get("debug_simplify_profile", True)))
    if "closed_profile_target_points" in cfg:
        cfg["debug_max_profile_points"] = int(cfg.get("closed_profile_target_points") or cfg.get("debug_max_profile_points", 360))
    else:
        cfg["closed_profile_target_points"] = int(cfg.get("debug_max_profile_points", 360) or 360)
    if "closed_profile_min_spacing_chord" in cfg:
        cfg["debug_profile_min_spacing_chord"] = float(cfg.get("closed_profile_min_spacing_chord") or cfg.get("debug_profile_min_spacing_chord", 1.2e-4))
    else:
        cfg["closed_profile_min_spacing_chord"] = float(cfg.get("debug_profile_min_spacing_chord", 1.2e-4) or 1.2e-4)
    for public_key, legacy_key, default_value in [
        ("closed_te_rounding_enabled", "debug_te_rounding_enabled", True),
        ("closed_te_rounding_points", "debug_te_rounding_points", 61),
        ("closed_te_rounding_window_chord", "debug_te_rounding_window_chord", 0.055),
        ("closed_te_rounding_min_gap_chord", "debug_te_rounding_min_gap_chord", 2.0e-4),
        ("closed_te_refinement_width_chord", "debug_te_refinement_width_chord", 0.025),
        ("closed_te_refinement_strength", "debug_te_refinement_strength", 6.0),
        ("closed_te_refinement_max_weight", "debug_te_refinement_max_weight", 9.0),
        ("domain_radius_chord", "debug_domain_radius_chord", 7.0),
        ("closed_nearfield_dist_min_chord", "nearfield_dist_min_chord", 0.02),
        ("closed_nearfield_intermediate_dist_chord", "nearfield_intermediate_dist_chord", 0.45),
        ("closed_nearfield_dist_max_chord", "nearfield_dist_max_chord", 4.0),
        ("closed_nearfield_intermediate_size_chord", "nearfield_intermediate_size_chord", 0.035),
    ]:
        if public_key in cfg:
            cfg[legacy_key] = cfg[public_key]
        else:
            cfg[public_key] = cfg.get(legacy_key, default_value)
    if "closed_near_wall_size_from_bl" in cfg:
        cfg["surface_size_from_boundary_layer_enabled"] = _bool(cfg.get("closed_near_wall_size_from_bl"))
    else:
        cfg["closed_near_wall_size_from_bl"] = bool(cfg.get("surface_size_from_boundary_layer_enabled", False))
    if "closed_near_wall_size_chord" in cfg:
        cfg["surface_size_general_chord"] = float(cfg.get("closed_near_wall_size_chord") or cfg.get("surface_size_general_chord", 0.0075))
    else:
        cfg["closed_near_wall_size_chord"] = float(cfg.get("surface_size_general_chord", 0.0075) or 0.0075)
    if "closed_near_wall_size_bl_factor" in cfg:
        cfg["surface_size_bl_outer_factor"] = float(cfg.get("closed_near_wall_size_bl_factor") or cfg.get("surface_size_bl_outer_factor", 0.70))
    else:
        cfg["closed_near_wall_size_bl_factor"] = float(cfg.get("surface_size_bl_outer_factor", 0.70) or 0.70)
    if "closed_farfield_size_chord" in cfg:
        cfg["farfield_size_chord"] = float(cfg.get("closed_farfield_size_chord") or cfg.get("farfield_size_chord", 0.35))
    else:
        cfg["closed_farfield_size_chord"] = float(cfg.get("farfield_size_chord", 0.35) or 0.35)
    if "closed_nearfield_enabled" in cfg:
        cfg["nearfield_refinement_enabled"] = _bool(cfg.get("closed_nearfield_enabled"))
    else:
        cfg["closed_nearfield_enabled"] = bool(cfg.get("nearfield_refinement_enabled", True))

    # Open ram-air aliases. The closest robust Gmsh/OpenFOAM representation is
    # one exterior interpolating Spline wall plus a separate nonphysical inlet sizing bridge:
    # Physical Groups cannot label only part of a single Gmsh curve as wall.
    cfg["open_boundary_layer_layers"] = int(
        _cfg_first_present(cfg, "open_boundary_layer_layers", default=cfg.get("boundary_layer_layers", 50)) or 0
    )
    cfg["open_boundary_layer_growth"] = float(
        _cfg_first_present(cfg, "open_boundary_layer_growth", default=cfg.get("boundary_layer_growth", 1.10)) or 1.0
    )
    _normalize_first_cell_aliases(
        cfg,
        use_yplus_key="open_use_yplus_first_cell_height",
        manual_m_key="open_first_cell_height_m",
        legacy_manual_chord_key="open_first_cell_height_chord",
        legacy_key="open_first_cell_height_chord_override",
        meter_override_key="open_first_cell_height_m_override",
    )
    open_method = str(cfg.get("open_wall_curve_method", "segmented_outer_splines")).strip().lower()
    cfg["open_wall_curve_method"] = open_method
    if open_method in {"single_outer_spline_with_lip_fans", "single_outer_spline"}:
        cfg["open_boundary_layer_split_curvature_sections"] = False
        cfg["open_outer_wall_curve_kind"] = "Spline"
        cfg["open_thin_solid_fluid_surface"] = True
    elif open_method in {"single_outer_bspline_with_lip_fans", "single_outer_bspline"}:
        cfg["open_boundary_layer_split_curvature_sections"] = False
        cfg["open_outer_wall_curve_kind"] = "BSpline"
        cfg["open_thin_solid_fluid_surface"] = True
    elif open_method in {"segmented_outer_splines", "three_splines"}:
        cfg["open_boundary_layer_split_curvature_sections"] = True
        cfg["open_split_wall_curve_kind"] = str(cfg.get("open_split_wall_curve_kind", "Spline"))
        cfg["open_thin_solid_fluid_surface"] = True
    else:
        raise ValueError(
            "open_wall_curve_method must be single_outer_spline_with_lip_fans, "
            "single_outer_bspline_with_lip_fans or segmented_outer_splines."
        )
    inlet_bl_mode = str(
        cfg.get("open_inlet_boundary_layer_mode", "full_prismatic_bridge_without_fans")
    ).strip().lower()
    full_bridge_modes = {
        "full_prismatic_bridge_with_fans",
        "full_prismatic_bridge_without_fans",
    }
    if inlet_bl_mode not in {*full_bridge_modes, "triangular_inlet_no_bl"}:
        raise ValueError(
            "open_inlet_boundary_layer_mode must be full_prismatic_bridge_with_fans "
            "full_prismatic_bridge_without_fans or triangular_inlet_no_bl."
        )
    cfg["open_inlet_boundary_layer_mode"] = inlet_bl_mode
    cfg["open_boundary_layer_include_inlet_bridge"] = inlet_bl_mode in full_bridge_modes
    cfg["open_boundary_layer_fan_at_lips"] = inlet_bl_mode == "full_prismatic_bridge_with_fans"
    inlet_transition_elements = str(cfg.get("open_inlet_transition_elements", "triangles")).strip().lower()
    if inlet_transition_elements not in {
        "graded_quads", "graded_triangles", "triangles", "transfinite_triangles", "recombined_quads"
    }:
        raise ValueError(
            "open_inlet_transition_elements must be graded_quads, graded_triangles, triangles, "
            "transfinite_triangles or recombined_quads."
        )
    cfg["open_inlet_transition_elements"] = inlet_transition_elements
    if "open_near_wall_size_from_bl" in cfg:
        cfg["open_surface_size_from_boundary_layer_enabled"] = _bool(cfg.get("open_near_wall_size_from_bl"))
    else:
        cfg["open_near_wall_size_from_bl"] = bool(cfg.get("open_surface_size_from_boundary_layer_enabled", False))
    if "open_near_wall_size_chord" in cfg:
        cfg["open_surface_size_general_chord"] = float(cfg.get("open_near_wall_size_chord") or cfg.get("open_surface_size_general_chord", 0.010))
    else:
        cfg["open_near_wall_size_chord"] = float(cfg.get("open_surface_size_general_chord", 0.010) or 0.010)
    if "open_near_wall_size_bl_factor" in cfg:
        cfg["open_surface_size_bl_outer_factor"] = float(cfg.get("open_near_wall_size_bl_factor") or cfg.get("open_surface_size_bl_outer_factor", 0.70))
    else:
        cfg["open_near_wall_size_bl_factor"] = float(cfg.get("open_surface_size_bl_outer_factor", 0.70) or 0.70)
    return cfg


def load_mesh_config(case_root: Path, mesh_level: str, config_override: Path | None = None) -> dict[str, Any]:
    p = Path(config_override).resolve() if config_override is not None else cfd_inputs_root(case_root) / "config" / "cfd2d_mesh_config.json"
    if config_override is not None and not p.is_file():
        raise FileNotFoundError(f"Mesh configuration override does not exist: {p}")
    if config_override is None and not p.exists():
        legacy = cfd_root(case_root) / "inputs" / "config" / "cfd2d_mesh_config.json"
        p = legacy if legacy.exists() else Path(case_root) / "02_CFD_2D" / "config" / "cfd2d_mesh_config.json"
    file_cfg = read_json(p, {}) or {}
    cfg = dict(DEFAULT_MESH_CONFIG)
    if mesh_level == "debug":
        cfg.update({
            "boundary_layer_layers": 50,
            "boundary_layer_growth": 1.10,
            "boundary_layer_total_thickness_chord_override": None,
            "boundary_layer_exclude_te_cap_from_bl": False,
            "closed_airfoil_transfinite_enabled": True,
            "closed_airfoil_target_nodes": 2000,
            "closed_airfoil_transfinite_progression": 1.0,
            "closed_te_target_nodes": 25,
            "closed_te_transition_min_nodes": 30,
            "closed_te_neighbor_bump_enabled": True,
            "closed_te_neighbor_bump": 0.50,
            "closed_te_cap_distribution": "uniform",
            "closed_te_cap_progression": 1.0,
            "closed_single_curve_experimental": False,
            "closed_single_curve_kind": "Spline",
            "closed_single_curve_target_nodes": 2000,
            "closed_single_curve_start_at_te": True,
            "closed_single_curve_distribution": "bump",
            "closed_single_curve_bump": 0.06,
            "surface_size_from_boundary_layer_enabled": True,
            "surface_size_bl_outer_factor": 0.50,
            "surface_size_bl_outer_min_chord": 0.0015,
            "surface_size_bl_outer_max_chord": 0.006,
            "gmsh_mesh_algorithm_2d": 5,
            "gmsh_random_factor": 1.0e-7,
            "first_cell_height_chord_override": 1.0e-4,
            "request_boundary_layer": True,
            "recombine_boundary_layer": True,
            "debug_simplify_profile": True,
            "debug_max_profile_points": 600,
            "debug_profile_preprocess": True,
            "debug_profile_min_spacing_chord": 1.2e-4,
            "debug_airfoil_curve_mode": "hybrid_te_spline",
            "debug_airfoil_transfinite": False,
            "debug_airfoil_transfinite_node_multiplier": 1.0,
            "debug_boundary_layer_fan_at_le": False,
            "debug_boundary_layer_fan_at_te": False,
            "debug_boundary_layer_te_fan_points": 64,
            "debug_te_rounding_enabled": True,
            "debug_te_rounding_points": 60,
            "debug_te_rounding_window_chord": 0.010,
            "debug_te_rounding_min_gap_chord": 0.0,
            "debug_te_refinement_width_chord": 0.0,
            "debug_te_refinement_strength": 6.0,
            "debug_te_refinement_max_weight": 9.0,
            "debug_te_curve_line_window_chord": 0.008,
            "debug_te_cap_spline_segments": 3,
            "debug_enforce_rounded_te_curve_sections": True,
            "debug_te_transfinite_enabled": True,
            "debug_te_transfinite_min_nodes_per_curve": 8,
            "debug_domain_radius_chord": 10.0,
            "surface_size_general_chord": 0.0035,
            "surface_size_rounded_te_chord": 0.0012,
            "nearfield_refinement_enabled": True,
            "nearfield_dist_min_chord": 0.025,
            "nearfield_intermediate_dist_chord": 0.65,
            "nearfield_dist_max_chord": 4.00,
            "nearfield_intermediate_size_chord": 0.035,
            "nearfield_distance_sampling": 240,
            "wake_refinement_enabled": False,
            "wake_refinement_length_chord": 4.0,
            "wake_refinement_height_chord": 0.70,
            "wake_size_chord": 0.025,
            "farfield_size_chord": 1.0,
            "open_connected_fluid_surface": False,
            "open_thin_solid_fluid_surface": True,
            "open_single_connected_surface_2d": False,
    "open_minimum_fabric_thickness_chord": 4.0e-4,
            "open_mesh_internal_cavity": True,
            "open_boundary_layer_split_curvature_sections": True,
            "open_split_wall_curve_kind": "Spline",
            "open_outer_wall_curve_kind": "Spline",
            "open_surface_transfinite_multiplier": 1.0,
            "open_surface_target_nodes": 1400,
            "open_surface_transfinite_progression": 1.0,
            "open_wall_end_bump_enabled": True,
            "open_wall_end_bump_strength": 0.60,
            "open_te_transfinite_min_nodes": 40,
            "open_lip_transfinite_min_nodes": 64,
            "open_inner_wall_node_factor": 0.65,
            "open_inner_te_node_factor": 0.18,
            "open_inner_wall_min_nodes": 100,
            "open_inner_te_min_nodes": 16,
            "open_inner_wall_end_bump_enabled": True,
            "open_inner_wall_end_bump_strength": 0.86,
            "open_boundary_layer_include_inlet_bridge": True,
            "open_boundary_layer_single_loop_bspline": True,
            "open_boundary_layer_single_loop_curve_kind": "Spline",
            "open_boundary_layer_single_loop_transfinite": True,
            "open_internal_cavity_curve_mode": "spline",
            "open_te_rounding_enabled": True,
            "open_te_rounding_points": 121,
            "open_te_refinement_width_chord": 0.012,
            "open_te_transition_distance_chord": 0.010,
            "open_surface_size_general_chord": 0.004,
            "open_surface_size_le_chord": 0.002,
            "open_surface_size_lip_chord": 0.0012,
            "open_surface_size_te_chord": 0.0012,
            "open_farfield_size_chord": 0.75,
            "open_cavity_wall_size_chord": 0.008,
            "open_cavity_wall_transition_chord": 0.28,
            "open_cavity_size_chord": 0.030,
            "open_internal_te_refinement_enabled": True,
            "open_internal_te_dist_max_chord": 0.10,
            "open_internal_te_size_factor": 0.90,
            "open_first_cell_height_chord_override": 1.0e-4,
            "open_boundary_layer_layers": 50,
            "open_boundary_layer_growth": 1.10,
            "open_surface_size_from_boundary_layer_enabled": True,
            "open_surface_size_bl_outer_factor": 0.50,
            "open_surface_size_bl_outer_min_chord": 0.0015,
            "open_surface_size_bl_outer_max_chord": 0.008,
            "open_boundary_layer_aniso_max_deg": 30.0,
            "open_recombine_boundary_layer": True,
            "open_boundary_layer_total_thickness_chord_override": None,
            "open_diagnostic_boundary_layer_enabled": True,
            "open_boundary_layer_exclude_te_cap_from_bl": False,
            "open_boundary_layer_trim_end_segments": False,
            "open_boundary_layer_trim_ends_chord": 0.0,
            "open_boundary_layer_trim_end_points": 3,
            "open_boundary_layer_fan_at_lips": False,
            "open_boundary_layer_lip_fan_points": 8,
            "open_boundary_layer_include_inlet_marker": True,
            "open_duplicate_internal_inlet_marker_for_bl": False,
            "open_inlet_marker_transfinite_enabled": True,
            "open_inlet_marker_transfinite_nodes": 48,
            "open_inlet_connector_normal_nodes": 0,
            "open_inlet_transition_growth": 1.22,
            "open_nearfield_refinement_enabled": True,
            "open_nearfield_dist_min_chord": 0.025,
            "open_nearfield_intermediate_dist_chord": 0.60,
            "open_nearfield_dist_max_chord": 3.20,
            "open_nearfield_intermediate_size_chord": 0.030,
            "open_nearfield_distance_sampling": 180,
            "open_internal_inlet_refinement_enabled": True,
            "open_inlet_refinement_bridge_enabled": True,
            "open_internal_inlet_dist_min_chord": 0.0,
            "open_internal_inlet_dist_max_chord": 0.24,
            "open_internal_inlet_size_chord": 0.0012,
            "open_internal_inlet_distance_sampling": 180,
            "open_le_refinement_enabled": False,
            "open_le_refinement_width_chord": 0.18,
            "open_le_refinement_extent_chord": 0.28,
            "open_le_refinement_height_chord": 0.16,
            "open_le_refinement_transition_chord": 0.18,
            "open_lip_refinement_enabled": False,
            "open_lip_refinement_x_chord": 0.045,
            "open_lip_refinement_z_chord": 0.045,
            "open_lip_refinement_transition_chord": 0.08,
            "gmsh_boundary_layer_fallback_no_bl": False,
        })
    elif mesh_level == "coarse":
        cfg.update({"boundary_layer_layers": 20, "surface_size_general_chord": 0.008, "farfield_size_chord": 1.0})
    elif mesh_level == "fine":
        cfg.update({"boundary_layer_layers": 60, "surface_size_general_chord": 0.0015, "surface_size_te_chord": 0.0005, "surface_size_inlet_lips_chord": 0.0004, "farfield_size_chord": 0.25})
    elif mesh_level == "ross_like":
        cfg.update({"first_cell_height_chord_override": 5e-6, "fabric_thickness_chord": 1e-5, "boundary_layer_layers": 50, "boundary_layer_growth": 1.10, "surface_size_inlet_lips_chord": min(float(cfg.get("surface_size_inlet_lips_chord", 0.00075)), 0.001), "surface_size_te_chord": min(float(cfg.get("surface_size_te_chord", 0.001)), 0.001)})
    # Shared, public level definitions are applied after legacy compatibility
    # defaults and before the selected JSON.  The JSON therefore always wins.
    # ``custom`` deliberately applies no level values at all.
    cfg.update(mesh_level_values(mesh_level))
    # The editable JSON is intentionally applied last. This lets a user refine
    # and regenerate a mesh by editing cfd2d_mesh_config.json without the mesh
    # level silently overwriting those choices.
    cfg.update(file_cfg)
    # Do not let the new metre-based defaults shadow a genuinely old editable
    # file that only defines y1/chord. Once the application saves the file it
    # removes these legacy aliases and the metre value becomes authoritative.
    for metre_key, legacy_key in (
        ("closed_first_cell_height_m", "closed_first_cell_height_chord"),
        ("open_first_cell_height_m", "open_first_cell_height_chord"),
    ):
        if metre_key not in file_cfg and legacy_key in file_cfg:
            cfg.pop(metre_key, None)
    cfg = normalize_mesh_config_aliases(cfg)
    cfg["_mesh_config_source"] = str(p)
    cfg["_mesh_config_override_requested"] = config_override is not None
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json(p, cfg)
    return cfg


def estimate_first_cell_height_from_yplus(Re: float, chord_m: float, target_y_plus: float, rho: float, mu: float) -> float:
    audit = first_cell_height_audit(Re, chord_m, target_y_plus, rho, mu)
    return float(audit["selected_first_cell_height_m"])


def polygon_area_xy(coords: Iterable[tuple[float, float]]) -> float:
    pts = list(coords)
    if len(pts) < 3:
        return 0.0
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def ordered_curve_ids(edges: pd.DataFrame) -> list[int]:
    return [1000 + int(e.edge_id) for _, e in edges.sort_values("edge_id").iterrows() if int(e.start_point_id) != int(e.end_point_id)]


def curve_ids_for_patch(edges: pd.DataFrame, patch_predicate) -> list[int]:
    out = []
    for _, e in edges.sort_values("edge_id").iterrows():
        patch = str(e.patch_name)
        if patch_predicate(patch) and int(e.start_point_id) != int(e.end_point_id):
            out.append(1000 + int(e.edge_id))
    return out


def _ordered_closed_loop_point_ids(points: pd.DataFrame, edges: pd.DataFrame) -> list[int]:
    pids = set(int(v) for v in points["point_id"].tolist())
    clean_edges = [
        (int(e.start_point_id), int(e.end_point_id))
        for _, e in edges.sort_values("edge_id").iterrows()
        if int(e.start_point_id) in pids and int(e.end_point_id) in pids and int(e.start_point_id) != int(e.end_point_id)
    ]
    if len(clean_edges) >= 3:
        adjacency: dict[int, list[int]] = {}
        for a, b in clean_edges:
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)
        if all(len(adjacency.get(pid, [])) == 2 for pid in adjacency):
            start, nxt = clean_edges[0]
            ordered = [start]
            prev = None
            cur = start
            for _ in range(len(clean_edges)):
                neigh = adjacency[cur]
                if prev is None:
                    cand = nxt if nxt in neigh else neigh[0]
                else:
                    cand = neigh[0] if neigh[0] != prev else neigh[1]
                if cand == start:
                    if len(ordered) == len(clean_edges):
                        return ordered
                    break
                if cand in ordered:
                    break
                ordered.append(cand)
                prev, cur = cur, cand
    loop_ids = [a for a, _ in clean_edges]
    if len(loop_ids) >= 3:
        return loop_ids
    return [int(v) for v in points["point_id"].tolist()]


def _segments_intersect(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray, eps: float = 1e-12) -> bool:
    def orient(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> float:
        return float((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    return (o1 * o2 < -eps) and (o3 * o4 < -eps)


def count_closed_polyline_self_intersections(coords: np.ndarray) -> int:
    n = len(coords)
    if n < 4:
        return 0
    count = 0
    for i in range(n):
        a = coords[i]
        b = coords[(i + 1) % n]
        for j in range(i + 1, n):
            if j in {i, (i - 1) % n, (i + 1) % n}:
                continue
            if i == 0 and j == n - 1:
                continue
            c = coords[j]
            d = coords[(j + 1) % n]
            if _segments_intersect(a, b, c, d):
                count += 1
    return count


def count_open_polyline_self_intersections(coords: np.ndarray) -> int:
    pts = np.asarray(coords, dtype=float)
    if len(pts) < 4:
        return 0
    count = 0
    for i in range(len(pts) - 1):
        for j in range(i + 2, len(pts) - 1):
            if j == i + 1:
                continue
            if _segments_intersect(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                count += 1
    return count


def count_polyline_cross_intersections(first: np.ndarray, second: np.ndarray) -> int:
    a = np.asarray(first, dtype=float)
    b = np.asarray(second, dtype=float)
    count = 0
    for i in range(max(0, len(a) - 1)):
        for j in range(max(0, len(b) - 1)):
            if _segments_intersect(a[i], a[i + 1], b[j], b[j + 1]):
                count += 1
    return count


def _remove_near_duplicate_loop_points(coords: np.ndarray, min_spacing: float) -> tuple[np.ndarray, int]:
    if len(coords) < 3:
        return coords, 0
    cleaned = [coords[0]]
    removed = 0
    for p in coords[1:]:
        if float(np.linalg.norm(p - cleaned[-1])) >= min_spacing:
            cleaned.append(p)
        else:
            removed += 1
    if len(cleaned) > 2 and float(np.linalg.norm(cleaned[0] - cleaned[-1])) < min_spacing:
        cleaned.pop()
        removed += 1
    return np.asarray(cleaned, dtype=float), removed


def _catmull_rom_closed(coords: np.ndarray, samples_per_segment: int = 8, alpha: float = 0.5) -> np.ndarray:
    """Centripetal Catmull-Rom sampling for a closed 2D loop."""
    n = len(coords)
    if n < 4:
        return coords.copy()
    out: list[np.ndarray] = []

    def tj(ti: float, pi: np.ndarray, pj: np.ndarray) -> float:
        return ti + max(float(np.linalg.norm(pj - pi)), 1e-12) ** alpha

    for i in range(n):
        p0 = coords[(i - 1) % n]
        p1 = coords[i]
        p2 = coords[(i + 1) % n]
        p3 = coords[(i + 2) % n]
        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        ts = np.linspace(t1, t2, max(2, samples_per_segment), endpoint=False)
        for t in ts:
            a1 = (t1 - t) / max(t1 - t0, 1e-12) * p0 + (t - t0) / max(t1 - t0, 1e-12) * p1
            a2 = (t2 - t) / max(t2 - t1, 1e-12) * p1 + (t - t1) / max(t2 - t1, 1e-12) * p2
            a3 = (t3 - t) / max(t3 - t2, 1e-12) * p2 + (t - t2) / max(t3 - t2, 1e-12) * p3
            b1 = (t2 - t) / max(t2 - t0, 1e-12) * a1 + (t - t0) / max(t2 - t0, 1e-12) * a2
            b2 = (t3 - t) / max(t3 - t1, 1e-12) * a2 + (t - t1) / max(t3 - t1, 1e-12) * a3
            c = (t2 - t) / max(t2 - t1, 1e-12) * b1 + (t - t1) / max(t2 - t1, 1e-12) * b2
            out.append(c)
    return np.asarray(out, dtype=float)


def _smooth_array(values: np.ndarray, passes: int = 3) -> np.ndarray:
    arr = np.asarray(values, dtype=float).copy()
    for _ in range(max(0, passes)):
        arr = 0.25 * np.roll(arr, 1) + 0.5 * arr + 0.25 * np.roll(arr, -1)
    return arr


def _adaptive_resample_closed_curve(
    dense: np.ndarray,
    target_points: int,
    *,
    te_refinement_width_chord: float = 0.035,
    te_refinement_strength: float = 10.0,
    te_refinement_max_weight: float = 14.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    if len(dense) < 8:
        return dense.copy(), {"adaptive_resampling_note": "too_few_dense_points"}
    closed = np.vstack([dense, dense[0]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cumulative[-1])
    if total <= 1e-12:
        return dense.copy(), {"adaptive_resampling_note": "zero_curve_length"}

    chord = max(float(dense[:, 0].max() - dense[:, 0].min()), 1e-12)
    x_le = float(dense[:, 0].min())
    x_te = float(dense[:, 0].max())
    z_mid = 0.5 * float(dense[:, 1].min() + dense[:, 1].max())

    prev_pts = np.roll(dense, 1, axis=0)
    next_pts = np.roll(dense, -1, axis=0)
    v1 = dense - prev_pts
    v2 = next_pts - dense
    n1 = np.linalg.norm(v1, axis=1)
    n2 = np.linalg.norm(v2, axis=1)
    denom = np.maximum(n1 * n2, 1e-30)
    cosang = np.sum(v1 * v2, axis=1) / denom
    turn = np.arccos(np.clip(cosang, -1.0, 1.0))
    curvature = turn / np.maximum(0.5 * (n1 + n2), 1e-12)
    curv_norm = curvature / max(float(np.percentile(curvature, 95)), 1e-12)
    curv_norm = np.clip(curv_norm, 0.0, 2.0)

    le_weight = np.exp(-((dense[:, 0] - x_le) / (0.075 * chord)) ** 2)
    te_width = max(float(te_refinement_width_chord), 1.0e-4) * chord
    te_weight = np.exp(-((dense[:, 0] - x_te) / te_width) ** 2)
    suction_region = np.exp(-((dense[:, 0] - (x_le + 0.68 * chord)) / (0.24 * chord)) ** 2) * (dense[:, 1] > z_mid)
    weights = 1.0 + 3.0 * le_weight + float(te_refinement_strength) * te_weight + 1.4 * curv_norm + 0.7 * suction_region
    weights = _smooth_array(weights, passes=5)
    weights = np.clip(weights, 1.0, max(2.0, float(te_refinement_max_weight)))

    seg_weights = 0.5 * (weights + np.roll(weights, -1))
    weighted_seg = seg * seg_weights
    weighted_cumulative = np.concatenate([[0.0], np.cumsum(weighted_seg)])
    weighted_total = float(weighted_cumulative[-1])
    targets = np.linspace(0.0, weighted_total, target_points, endpoint=False)
    resampled = []
    for t in targets:
        idx = int(np.searchsorted(weighted_cumulative, t, side="right") - 1)
        idx = max(0, min(idx, len(seg) - 1))
        local = (t - weighted_cumulative[idx]) / max(weighted_seg[idx], 1e-12)
        p = closed[idx] + local * (closed[idx + 1] - closed[idx])
        resampled.append(p)
    out = np.asarray(resampled, dtype=float)
    out_closed = np.vstack([out, out[0]])
    out_lengths = np.linalg.norm(np.diff(out_closed, axis=0), axis=1)
    te_zone = out[:, 0] >= (x_te - te_width)
    te_zone_lengths = out_lengths[te_zone] if len(out_lengths) == len(te_zone) else np.asarray([], dtype=float)
    info = {
        "adaptive_curve_length": total,
        "adaptive_weighted_length": weighted_total,
        "adaptive_spacing_min": float(out_lengths.min()) if len(out_lengths) else None,
        "adaptive_spacing_mean": float(out_lengths.mean()) if len(out_lengths) else None,
        "adaptive_spacing_max": float(out_lengths.max()) if len(out_lengths) else None,
        "adaptive_spacing_max_to_min": float(out_lengths.max() / max(out_lengths.min(), 1e-30)) if len(out_lengths) else None,
        "adaptive_te_refinement_width_chord": float(te_refinement_width_chord),
        "adaptive_te_refinement_strength": float(te_refinement_strength),
        "adaptive_te_refinement_max_weight": float(te_refinement_max_weight),
        "adaptive_te_zone_points": int(te_zone.sum()),
        "adaptive_te_zone_spacing_min": float(te_zone_lengths.min()) if len(te_zone_lengths) else None,
        "adaptive_te_zone_spacing_mean": float(te_zone_lengths.mean()) if len(te_zone_lengths) else None,
        "adaptive_te_zone_spacing_max": float(te_zone_lengths.max()) if len(te_zone_lengths) else None,
    }
    return out, info


def _safe_unit_vector(vec: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    if n > 1.0e-14:
        return np.asarray(vec, dtype=float) / n
    fb = np.asarray(fallback, dtype=float)
    fb_norm = float(np.linalg.norm(fb))
    return fb / max(fb_norm, 1.0e-14)


def _tangent_continuous_te_cap_points(
    start: np.ndarray,
    end: np.ndarray,
    start_prev: np.ndarray,
    end_next: np.ndarray,
    chord: float,
    n_internal: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build a downstream rounded TE cap with G1-like tangent continuity.

    The old debug cap used an exact circle whose tangent is fixed by the TE gap.
    That can introduce a local curvature reversal when the profile's upper/lower
    surfaces do not meet the circular tangent. This cubic Bezier cap keeps the
    same endpoints but takes its entry/exit directions from the neighbouring
    profile segments, with a downstream handle so the cap still rounds aft.
    """
    p0 = np.asarray(start, dtype=float)
    p3 = np.asarray(end, dtype=float)
    prev = np.asarray(start_prev, dtype=float)
    nxt = np.asarray(end_next, dtype=float)
    gap = float(np.linalg.norm(p3 - p0))
    chord = max(float(chord), 1.0e-12)
    downstream = np.array([1.0, 0.0], dtype=float)

    d0 = _safe_unit_vector(p0 - prev, downstream)
    d1 = _safe_unit_vector(nxt - p3, -downstream)
    if float(d0[0]) < 0.05:
        d0 = _safe_unit_vector(0.65 * d0 + 0.35 * downstream, downstream)
    if float(d1[0]) > -0.05:
        d1 = _safe_unit_vector(0.65 * d1 - 0.35 * downstream, -downstream)

    adj0 = float(np.linalg.norm(p0 - prev))
    adj1 = float(np.linalg.norm(nxt - p3))
    adjacent = np.mean([v for v in [adj0, adj1] if v > 1.0e-14]) if (adj0 > 1.0e-14 or adj1 > 1.0e-14) else gap
    handle = max(0.55 * gap, 0.70 * float(adjacent))
    handle = min(max(handle, 0.35 * gap), 1.35 * gap)
    handle = max(handle, 2.0e-5 * chord)

    p1 = p0 + handle * d0
    p2 = p3 - handle * d1
    if float(p1[0]) <= float(p0[0]):
        p1 = p0 + handle * downstream
    if float(p2[0]) <= float(p3[0]):
        p2 = p3 + handle * downstream

    ts = np.linspace(0.0, 1.0, max(3, int(n_internal)) + 2)[1:-1]
    cap = []
    for t in ts:
        omt = 1.0 - float(t)
        p = (omt ** 3) * p0 + 3.0 * (omt ** 2) * float(t) * p1 + 3.0 * omt * (float(t) ** 2) * p2 + (float(t) ** 3) * p3
        cap.append(p)
    info = {
        "te_rounding_cap_method": "tangent_continuous_cubic_bezier",
        "te_rounding_handle_length_chord": float(handle / chord),
        "te_rounding_start_tangent": [float(d0[0]), float(d0[1])],
        "te_rounding_end_tangent": [float(d1[0]), float(d1[1])],
        "te_rounding_start_handle_x_chord": float((p1[0] - p0[0]) / chord),
        "te_rounding_end_handle_x_chord": float((p2[0] - p3[0]) / chord),
    }
    return np.asarray(cap, dtype=float), info


def _insert_tangent_te_cap(
    coords: np.ndarray,
    chord: float,
    *,
    enabled: bool,
    n_arc_points: int,
    window_chord: float,
    min_gap_chord: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Replace the consecutive TE closing segment by a downstream tangent cap.

    The function operates only on an already ordered closed loop. It does not
    connect arbitrary points: one existing consecutive segment is selected near
    maximum x and replaced by internal arc points between the same endpoints.
    """
    info: dict[str, Any] = {
        "te_rounding_enabled": bool(enabled),
        "te_rounding_applied": False,
        "te_rounding_points_requested": int(n_arc_points),
    }
    if not enabled:
        info["te_rounding_note"] = "disabled"
        return coords, info
    pts = np.asarray(coords, dtype=float)
    if len(pts) < 8:
        info["te_rounding_note"] = "too_few_points"
        return pts, info
    chord = max(float(chord), 1e-12)
    x_te = float(pts[:, 0].max())
    window = max(float(window_chord) * chord, 1e-9)
    min_gap = max(float(min_gap_chord) * chord, 1e-12)

    candidates: list[tuple[float, int, float, float, float]] = []
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        a = pts[i]
        b = pts[j]
        dvec = b - a
        length = float(np.linalg.norm(dvec))
        if length <= 1e-12:
            continue
        dx = abs(float(dvec[0]))
        dz = abs(float(dvec[1]))
        both_near_te = min(float(a[0]), float(b[0])) >= x_te - window
        almost_vertical = dx <= max(0.35 * length, 0.004 * chord)
        if not (both_near_te and almost_vertical):
            continue
        mid_x = 0.5 * (float(a[0]) + float(b[0]))
        verticality = dz / max(length, 1e-12)
        score = (mid_x - (x_te - window)) / window + 0.75 * verticality - 0.20 * (dx / max(length, 1e-12))
        candidates.append((score, i, length, dx, dz))

    if not candidates:
        info["te_rounding_note"] = "no_consecutive_te_closure_segment_found"
        return pts, info
    candidates.sort(reverse=True)
    _, idx, gap, dx, dz = candidates[0]
    if gap < min_gap:
        info.update({
            "te_rounding_note": "te_gap_below_minimum",
            "te_rounding_detected_gap_chord": float(gap / chord),
            "te_rounding_min_gap_chord": float(min_gap_chord),
        })
        return pts, info

    a = pts[idx]
    b = pts[(idx + 1) % len(pts)]
    prev_pt = pts[(idx - 1) % len(pts)]
    next_pt = pts[(idx + 2) % len(pts)]
    n_internal = max(3, int(n_arc_points))
    arc_internal, cap_info = _tangent_continuous_te_cap_points(a, b, prev_pt, next_pt, chord, n_internal)

    if idx == len(pts) - 1:
        rounded = np.vstack([pts, arc_internal])
    else:
        rounded = np.vstack([pts[: idx + 1], arc_internal, pts[idx + 1 :]])
    info.update({
        "te_rounding_applied": True,
        "te_rounding_note": "tangent_continuous_downstream_cap_inserted_on_consecutive_te_segment",
        "te_rounding_segment_start_index": int(idx),
        "te_rounding_segment_end_index": int((idx + 1) % len(pts)),
        "te_rounding_detected_gap_chord": float(gap / chord),
        "te_rounding_detected_dx_chord": float(dx / chord),
        "te_rounding_detected_dz_chord": float(dz / chord),
        "te_rounding_points_added": int(len(arc_internal)),
        "te_rounding_cap_bulge_direction": "+x downstream with endpoint tangency",
        "te_rounding_start_point_m": [float(a[0]), float(a[1])],
        "te_rounding_end_point_m": [float(b[0]), float(b[1])],
        "te_rounding_cap_x_min_m": float(min(a[0], b[0], float(arc_internal[:, 0].min()))),
        "te_rounding_cap_x_max_m": float(max(a[0], b[0], float(arc_internal[:, 0].max()))),
    })
    info.update(cap_info)
    return rounded, info


def _geometric_series_first_height(total_thickness: float, growth: float, n_layers: int) -> float:
    n = max(1, int(n_layers))
    g = max(1.0, float(growth))
    total = max(float(total_thickness), 1.0e-15)
    if abs(g - 1.0) <= 1.0e-12:
        return total / n
    return total * (g - 1.0) / max(g ** n - 1.0, 1.0e-15)


def graded_inlet_transition_parameters(
    total_thickness: float,
    first_height: float,
    max_growth: float,
    requested_nodes: int = 0,
) -> dict[str, float | int | str]:
    """Fit a geometric row distribution from exterior y1 to the cavity side."""
    total = max(float(total_thickness), 1.0e-15)
    requested_first = min(max(float(first_height), 1.0e-15), total)
    growth_limit = min(1.30, max(1.0, float(max_growth)))

    if int(requested_nodes) >= 2:
        layers = int(requested_nodes) - 1
        source = "user_normal_node_count"
    else:
        layers = 1
        while (
            requested_first * (growth_limit ** layers - 1.0) / max(growth_limit - 1.0, 1.0e-15)
            if growth_limit > 1.0
            else requested_first * layers
        ) < total:
            layers += 1
            if layers >= 200:
                break
        source = "automatic_from_y1_thickness_and_growth_limit"

    uniform_first = total / max(layers, 1)
    if uniform_first <= requested_first or growth_limit <= 1.0:
        growth = 1.0
        actual_first = uniform_first
    else:
        low, high = 1.0, growth_limit
        for _ in range(80):
            trial = 0.5 * (low + high)
            series = requested_first * (trial ** layers - 1.0) / max(trial - 1.0, 1.0e-15)
            if series < total:
                low = trial
            else:
                high = trial
        growth = 0.5 * (low + high)
        attainable = requested_first * (growth ** layers - 1.0) / max(growth - 1.0, 1.0e-15)
        if attainable < 0.999999 * total:
            growth = growth_limit
            actual_first = _geometric_series_first_height(total, growth, layers)
            source += "_first_height_adjusted"
        else:
            actual_first = requested_first

    return {
        "normal_nodes": int(layers + 1),
        "normal_layers": int(layers),
        "progression": float(growth),
        "requested_first_height": float(requested_first),
        "actual_first_height": float(actual_first),
        "last_height": float(actual_first * growth ** max(layers - 1, 0)),
        "total_thickness": float(total),
        "source": source,
    }


def boundary_layer_parameters(
    mesh_cfg: dict[str, Any],
    chord_m: float,
    *,
    first_cell_key: str = "first_cell_height_chord_override",
    first_cell_m_key: str | None = "closed_first_cell_height_m_override",
    growth_key: str = "boundary_layer_growth",
    layers_key: str = "boundary_layer_layers",
    thickness_key: str = "boundary_layer_total_thickness_chord_override",
    fallback_first_cell_chord: float = 1.0e-5,
) -> dict[str, float | int | bool | None]:
    chord_m = max(float(chord_m), 1.0e-12)
    n_layers = max(1, int(mesh_cfg.get(layers_key, mesh_cfg.get("boundary_layer_layers", 1)) or 1))
    growth = max(1.0, float(mesh_cfg.get(growth_key, mesh_cfg.get("boundary_layer_growth", 1.10)) or 1.0))
    requested_y1_m = mesh_cfg.get(first_cell_m_key) if first_cell_m_key else None
    requested_y1c = mesh_cfg.get(first_cell_key)
    if first_cell_key not in mesh_cfg and first_cell_key != "first_cell_height_chord_override":
        requested_y1c = mesh_cfg.get("first_cell_height_chord_override")
    first_cell_height_source = "config_override_meters" if requested_y1_m is not None else "config_override_chord_legacy"
    if requested_y1_m is None and requested_y1c is None:
        phys = mesh_cfg.get("_physical_for_yplus") or {}
        if phys:
            Re = _as_float(phys.get("reynolds"), 4.0e6)
            rho = _as_float(phys.get("rho", phys.get("rho_kg_m3")), 1.225)
            mu = _as_float(phys.get("mu", phys.get("mu_pa_s")), 1.81e-5)
            target_y = _as_float(mesh_cfg.get("target_y_plus"), 0.5)
            requested_y1c = estimate_first_cell_height_from_yplus(Re, chord_m, target_y, rho, mu) / chord_m
            first_cell_height_source = "target_y_plus_flat_plate_estimate"
        else:
            requested_y1c = fallback_first_cell_chord
            first_cell_height_source = "fallback_first_cell_height_chord"
    requested_y1 = max(
        float(requested_y1_m) if requested_y1_m is not None else float(requested_y1c) * chord_m,
        1.0e-15,
    )

    raw_thickness = requested_y1 * (growth ** n_layers - 1.0) / max(growth - 1.0, 1.0e-12) if growth > 1.0 else requested_y1 * n_layers
    override = mesh_cfg.get(thickness_key)
    if override is None and thickness_key != "boundary_layer_total_thickness_chord_override":
        override = mesh_cfg.get("boundary_layer_total_thickness_chord_override")
    limited = override is not None
    if limited:
        thickness = max(float(override) * chord_m, requested_y1 * 1.01)
        y1 = requested_y1
    else:
        thickness = raw_thickness
        y1 = requested_y1
    return {
        "first_cell_height": float(y1),
        "requested_first_cell_height": float(requested_y1),
        "growth": float(growth),
        "n_layers": int(n_layers),
        "total_thickness": float(thickness),
        "raw_total_thickness": float(raw_thickness),
        "total_thickness_chord": float(thickness / chord_m),
        "raw_total_thickness_chord": float(raw_thickness / chord_m),
        "first_cell_height_chord": float(y1 / chord_m),
        "requested_first_cell_height_chord": float(requested_y1 / chord_m),
        "first_cell_height_source": first_cell_height_source,
        "total_thickness_limited": bool(limited),
    }


def _curve_point_length(ids: list[int], pindex: pd.DataFrame) -> float:
    length = 0.0
    for pid_a, pid_b in zip(ids, ids[1:]):
        if int(pid_a) not in pindex.index or int(pid_b) not in pindex.index:
            continue
        a = pindex.loc[int(pid_a), ["x_m", "z_m"]].to_numpy(float)
        b = pindex.loc[int(pid_b), ["x_m", "z_m"]].to_numpy(float)
        length += float(np.linalg.norm(b - a))
    return float(length)


def audit_curve_connectivity(
    curve_definitions: list[tuple[str, int, list[int], str]],
    pindex: pd.DataFrame,
    out_dir: Path,
    *,
    label: str,
    closed_loop: bool = True,
) -> dict[str, Any]:
    """Write a traceable audit of the exact wall-curve chain sent to Gmsh."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if "x_m" not in pindex.columns or "z_m" not in pindex.columns:
        return {"label": label, "valid": False, "issue_count": 1, "issues": ["missing_xz_columns"]}

    x_range = float(pindex["x_m"].max() - pindex["x_m"].min()) if len(pindex) else 1.0
    z_range = float(pindex["z_m"].max() - pindex["z_m"].min()) if len(pindex) else 1.0
    tol = max(1.0e-12, 1.0e-9 * max(abs(x_range), abs(z_range), 1.0))
    rows: list[dict[str, Any]] = []
    issues: list[str] = []

    def coord(pid: int) -> np.ndarray | None:
        if int(pid) not in pindex.index:
            return None
        return pindex.loc[int(pid), ["x_m", "z_m"]].to_numpy(float)

    for i, (kind, cid, ids, name) in enumerate(curve_definitions):
        ids = [int(v) for v in ids]
        missing = [pid for pid in ids if pid not in pindex.index]
        duplicate_consecutive = [
            j for j, (a, b) in enumerate(zip(ids, ids[1:]), start=1) if int(a) == int(b)
        ]
        zero_segments = []
        min_segment = None
        max_segment = None
        for j, (a_id, b_id) in enumerate(zip(ids, ids[1:]), start=1):
            a = coord(a_id)
            b = coord(b_id)
            if a is None or b is None:
                continue
            seg_len = float(np.linalg.norm(b - a))
            min_segment = seg_len if min_segment is None else min(min_segment, seg_len)
            max_segment = seg_len if max_segment is None else max(max_segment, seg_len)
            if seg_len <= tol:
                zero_segments.append(j)

        next_connected = None
        next_distance = None
        if curve_definitions:
            if i < len(curve_definitions) - 1:
                next_ids = curve_definitions[i + 1][2]
            elif closed_loop:
                next_ids = curve_definitions[0][2]
            else:
                next_ids = []
            if ids and next_ids:
                a = coord(ids[-1])
                b = coord(int(next_ids[0]))
                if a is not None and b is not None:
                    next_distance = float(np.linalg.norm(b - a))
                    next_connected = bool(ids[-1] == int(next_ids[0]) or next_distance <= tol)

        curve_ok = not missing and not duplicate_consecutive and not zero_segments and (next_connected is not False)
        if missing:
            issues.append(f"curve_{cid}_missing_points:{missing[:8]}")
        if duplicate_consecutive:
            issues.append(f"curve_{cid}_duplicate_consecutive_positions:{duplicate_consecutive[:8]}")
        if zero_segments:
            issues.append(f"curve_{cid}_zero_length_segment_positions:{zero_segments[:8]}")
        if next_connected is False:
            issues.append(f"curve_{cid}_not_connected_to_next_distance:{next_distance:.6g}")

        rows.append({
            "order": i + 1,
            "curve_id": int(cid),
            "kind": kind,
            "label": name,
            "n_point_refs": int(len(ids)),
            "start_point_id": ids[0] if ids else None,
            "end_point_id": ids[-1] if ids else None,
            "missing_point_count": int(len(missing)),
            "duplicate_consecutive_count": int(len(duplicate_consecutive)),
            "zero_length_segment_count": int(len(zero_segments)),
            "min_segment_length_m": min_segment,
            "max_segment_length_m": max_segment,
            "connected_to_next": next_connected,
            "distance_to_next_start_m": next_distance,
            "curve_ok": bool(curve_ok),
        })

    coord_rows = []
    if len(pindex):
        rounded_to_pid: dict[tuple[int, int], list[int]] = {}
        scale = max(max(abs(float(v)) for v in pindex["x_m"].to_numpy(float)), max(abs(float(v)) for v in pindex["z_m"].to_numpy(float)), 1.0)
        coord_tol = max(tol, scale * 1.0e-10)
        for pid, row in pindex.iterrows():
            key = (int(round(float(row["x_m"]) / coord_tol)), int(round(float(row["z_m"]) / coord_tol)))
            rounded_to_pid.setdefault(key, []).append(int(pid))
        for ids in rounded_to_pid.values():
            if len(ids) > 1:
                coord_rows.append({"point_ids": ids, "count": len(ids)})
        if coord_rows:
            issues.append(f"near_duplicate_coordinate_groups:{len(coord_rows)}")

    csv_path = out_dir / f"{label}_curve_connectivity_audit.csv"
    json_path = out_dir / f"{label}_curve_connectivity_audit.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    audit = {
        "label": label,
        "valid": bool(not issues),
        "closed_loop": bool(closed_loop),
        "tolerance_m": float(tol),
        "curve_count": int(len(curve_definitions)),
        "issue_count": int(len(issues)),
        "issues": issues,
        "near_duplicate_coordinate_groups": coord_rows[:20],
        "csv": str(csv_path),
        "json": str(json_path),
    }
    write_json(json_path, audit)
    return audit


def _target_nodes_for_curve(
    curve_length: float,
    total_target_nodes: int,
    total_reference_length: float,
    *,
    min_nodes: int = 3,
    existing_points: int = 0,
) -> int:
    if total_target_nodes <= 0 or total_reference_length <= 1.0e-15:
        return max(int(min_nodes), int(existing_points), 3)
    share = max(float(curve_length), 1.0e-15) / total_reference_length
    # Nodes at shared curve endpoints are duplicated in this local estimate; the
    # small +1 bias keeps each short TE/lip curve from being under-resolved.
    return max(int(min_nodes), int(existing_points), int(math.ceil(total_target_nodes * share)) + 1)


def _uniform_closed_curve_node_counts(
    curve_lengths: dict[str, float],
    target_total_nodes: int,
    *,
    minimum_segments_per_curve: int = 2,
) -> tuple[dict[str, int], float]:
    """Allocate one uniform tangential spacing over a partitioned closed loop.

    Gmsh physical groups require a separate curve where the boundary role
    changes.  Allocating the segment count from one common spacing avoids a
    mesh-size jump at those purely topological curve splits.
    """
    if not curve_lengths:
        raise ValueError("Cannot allocate curve nodes without curve lengths.")
    positive_lengths = {name: max(float(length), 0.0) for name, length in curve_lengths.items()}
    if any(length <= 1.0e-15 for length in positive_lengths.values()):
        bad = [name for name, length in positive_lengths.items() if length <= 1.0e-15]
        raise ValueError(f"Zero-length curves cannot be discretized: {bad}")
    minimum = max(1, int(minimum_segments_per_curve))
    total_segments = max(int(target_total_nodes), minimum * len(positive_lengths))
    total_length = sum(positive_lengths.values())
    raw = {
        name: total_segments * length / total_length
        for name, length in positive_lengths.items()
    }
    segments = {
        name: max(minimum, int(math.floor(value)))
        for name, value in raw.items()
    }
    while sum(segments.values()) < total_segments:
        name = max(
            positive_lengths,
            key=lambda item: (raw[item] - segments[item], positive_lengths[item]),
        )
        segments[name] += 1
    while sum(segments.values()) > total_segments:
        candidates = [name for name, count in segments.items() if count > minimum]
        if not candidates:
            break
        name = min(
            candidates,
            key=lambda item: (raw[item] - segments[item], -positive_lengths[item]),
        )
        segments[name] -= 1
    nodes = {name: count + 1 for name, count in segments.items()}
    return nodes, total_length / max(sum(segments.values()), 1)


def _rotate_closed_ids_to_te(ids: list[int], pindex: pd.DataFrame) -> list[int]:
    if len(ids) < 3:
        return ids
    coords = pindex.loc[ids][["x_m", "z_m"]].to_numpy(float)
    i_te = int(np.argmax(coords[:, 0]))
    return ids[i_te:] + ids[:i_te]


def _curve_chain_point_ids(curve_definitions: list[tuple[str, int, list[int], str]], *, closed_loop: bool) -> list[int]:
    ids: list[int] = []
    for _, _, curve_ids, _ in curve_definitions:
        local = [int(v) for v in curve_ids]
        if not local:
            continue
        if ids and local[0] == ids[-1]:
            ids.extend(local[1:])
        else:
            ids.extend(local)
    if closed_loop and len(ids) > 1 and ids[-1] == ids[0]:
        ids = ids[:-1]
    return ids


def _closed_te_geometry_metrics(
    curve_definitions: list[tuple[str, int, list[int], str]],
    pindex: pd.DataFrame,
    chord_m: float,
    *,
    te_window_chord: float,
    bl_total_thickness_chord: float | None,
) -> dict[str, Any]:
    ids = _curve_chain_point_ids(curve_definitions, closed_loop=True)
    ids = [pid for pid in ids if pid in pindex.index]
    chord_m = max(float(chord_m), 1.0e-12)
    if len(ids) < 4:
        return {"closed_te_geometry_metric_note": "too_few_points"}
    coords = pindex.loc[ids][["x_m", "z_m"]].to_numpy(float)
    closed = np.vstack([coords, coords[0]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    x_te = float(coords[:, 0].max())
    window = max(float(te_window_chord) * chord_m, 1.0e-9)
    te_mask = coords[:, 0] >= x_te - window
    te_seg_mask = np.logical_or(te_mask, np.roll(te_mask, -1))
    te_seg = seg[te_seg_mask] if len(seg) == len(te_seg_mask) else np.asarray([], dtype=float)
    radii: list[float] = []
    for i in range(len(coords)):
        if not (te_mask[i] or te_mask[(i - 1) % len(coords)] or te_mask[(i + 1) % len(coords)]):
            continue
        a = coords[(i - 1) % len(coords)]
        b = coords[i]
        c = coords[(i + 1) % len(coords)]
        ab = float(np.linalg.norm(b - a))
        bc = float(np.linalg.norm(c - b))
        ca = float(np.linalg.norm(a - c))
        ba = b - a
        ca_vec = c - a
        cross = abs(float(ba[0] * ca_vec[1] - ba[1] * ca_vec[0]))
        if ab <= 1.0e-14 or bc <= 1.0e-14 or ca <= 1.0e-14 or cross <= 1.0e-22:
            continue
        radii.append(ab * bc * ca / max(2.0 * cross, 1.0e-30))
    min_radius_chord = min(radii) / chord_m if radii else None
    thickness_ratio = None
    risk = None
    if min_radius_chord and bl_total_thickness_chord is not None:
        thickness_ratio = float(bl_total_thickness_chord) / max(float(min_radius_chord), 1.0e-15)
        risk = thickness_ratio > 0.65
    return {
        "closed_te_window_chord_for_metrics": float(te_window_chord),
        "closed_te_segment_min_length_chord": float(te_seg.min() / chord_m) if len(te_seg) else None,
        "closed_te_segment_mean_length_chord": float(te_seg.mean() / chord_m) if len(te_seg) else None,
        "closed_te_segment_max_length_chord": float(te_seg.max() / chord_m) if len(te_seg) else None,
        "closed_te_metric_point_count": int(te_mask.sum()),
        "closed_te_min_curvature_radius_chord": float(min_radius_chord) if min_radius_chord is not None else None,
        "closed_te_curvature_radius_samples": int(len(radii)),
        "closed_te_boundary_layer_thickness_to_min_radius": thickness_ratio,
        "closed_te_boundary_layer_self_intersection_risk": risk,
        "closed_te_geometry_metric_note": (
            "risk_when_BL_thickness_is_large_relative_to_local_TE_radius"
            if risk
            else "ok_or_not_assessed"
        ),
    }


def _boundary_layer_outer_cell_height_chord(
    mesh_cfg: dict[str, Any],
    chord_m: float,
    *,
    first_cell_key: str = "first_cell_height_chord_override",
    first_cell_m_key: str | None = "closed_first_cell_height_m_override",
    growth_key: str = "boundary_layer_growth",
    layers_key: str = "boundary_layer_layers",
    fallback_first_cell_chord: float = 1.0e-4,
) -> float:
    bl = boundary_layer_parameters(
        mesh_cfg,
        chord_m,
        fallback_first_cell_chord=fallback_first_cell_chord,
        first_cell_key=first_cell_key,
        first_cell_m_key=first_cell_m_key,
        growth_key=growth_key,
        layers_key=layers_key,
    )
    y1c = float(bl["first_cell_height_chord"])
    growth = max(1.0, float(bl["growth"]))
    n_layers = max(1, int(bl["n_layers"]))
    return float(y1c * (growth ** max(0, n_layers - 1)))


def _derive_surface_size_from_boundary_layer(
    mesh_cfg: dict[str, Any],
    chord_m: float,
    current_size_chord: float,
    *,
    enabled_key: str,
    factor_key: str,
    min_key: str,
    max_key: str,
    first_cell_key: str = "first_cell_height_chord_override",
    first_cell_m_key: str | None = "closed_first_cell_height_m_override",
    growth_key: str = "boundary_layer_growth",
    layers_key: str = "boundary_layer_layers",
) -> tuple[float, dict[str, Any]]:
    enabled = bool(mesh_cfg.get(enabled_key, False))
    info = {
        "enabled": enabled,
        "manual_surface_size_chord": float(current_size_chord),
        "active_surface_size_chord": float(current_size_chord),
    }
    if not enabled:
        return float(current_size_chord), info
    outer = _boundary_layer_outer_cell_height_chord(
        mesh_cfg,
        chord_m,
        first_cell_key=first_cell_key,
        first_cell_m_key=first_cell_m_key,
        growth_key=growth_key,
        layers_key=layers_key,
    )
    factor = max(1.0e-9, float(mesh_cfg.get(factor_key, 1.0) or 1.0))
    min_size = max(1.0e-12, float(mesh_cfg.get(min_key, 0.0) or 0.0))
    max_size = max(min_size, float(mesh_cfg.get(max_key, current_size_chord) or current_size_chord))
    derived = min(max(float(outer * factor), min_size), max_size)
    info.update({
        "outer_bl_cell_height_chord": float(outer),
        "factor": float(factor),
        "min_chord": float(min_size),
        "max_chord": float(max_size),
        "active_surface_size_chord": float(derived),
    })
    return float(derived), info


def estimate_boundary_layer_from_yplus_inputs(
    mesh_cfg: dict[str, Any],
    chord_m: float,
    *,
    first_cell_key: str = "first_cell_height_chord_override",
    first_cell_m_key: str | None = "closed_first_cell_height_m_override",
    growth_key: str = "boundary_layer_growth",
    layers_key: str = "boundary_layer_layers",
) -> dict[str, Any]:
    """Return a traceable y+ estimate; used only when first-cell override is null."""
    phys = mesh_cfg.get("_physical_for_yplus") or {}
    Re = _as_float(phys.get("reynolds", mesh_cfg.get("reynolds")), 4.0e6)
    rho = _as_float(phys.get("rho", phys.get("rho_kg_m3", mesh_cfg.get("rho"))), 1.225)
    mu = _as_float(phys.get("mu", phys.get("mu_pa_s", mesh_cfg.get("mu"))), 1.81e-5)
    target_y = _as_float(mesh_cfg.get("target_y_plus"), 0.5)
    y1 = estimate_first_cell_height_from_yplus(Re, chord_m, target_y, rho, mu)
    growth = max(1.0, float(mesh_cfg.get(growth_key, mesh_cfg.get("boundary_layer_growth", 1.10)) or 1.0))
    n_layers = max(1, int(mesh_cfg.get(layers_key, mesh_cfg.get("boundary_layer_layers", 1)) or 1))
    total = y1 * (growth ** n_layers - 1.0) / max(growth - 1.0, 1.0e-12) if growth > 1.0 else y1 * n_layers
    return {
        "method": "flat_plate_turbulent_Cf_0p026_Re_minus_1_over_7",
        "target_y_plus": float(target_y),
        "reynolds": float(Re),
        "rho_kg_m3": float(rho),
        "mu_pa_s": float(mu),
        "velocity_m_s_from_reynolds": float(Re * mu / (rho * max(chord_m, 1.0e-12))),
        "first_cell_height_m": float(y1),
        "first_cell_height_chord": float(y1 / max(chord_m, 1.0e-12)),
        "layers": int(n_layers),
        "growth": float(growth),
        "total_thickness_m": float(total),
        "total_thickness_chord": float(total / max(chord_m, 1.0e-12)),
        "active_only_when_first_cell_override_is_null": (
            mesh_cfg.get(first_cell_key) is None
            and (first_cell_m_key is None or mesh_cfg.get(first_cell_m_key) is None)
        ),
    }


def simplify_closed_loop_for_debug(
    points: pd.DataFrame,
    edges: pd.DataFrame,
    max_points: int,
    min_spacing_chord: float = 2.0e-4,
    *,
    te_rounding_enabled: bool = False,
    te_rounding_points: int = 17,
    te_rounding_window_chord: float = 0.04,
    te_rounding_min_gap_chord: float = 2.0e-4,
    te_refinement_width_chord: float = 0.035,
    te_refinement_strength: float = 10.0,
    te_refinement_max_weight: float = 14.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Preprocess and redistribute a closed profile for robust debug meshing."""
    points = points.copy().reset_index(drop=True)
    edges = edges[edges.start_point_id != edges.end_point_id].copy().sort_values("edge_id").reset_index(drop=True)
    info: dict[str, Any] = {
        "profile_simplification_applied": False,
        "profile_preprocessing_applied": False,
        "profile_points_original": int(len(points)),
        "profile_edges_original": int(len(edges)),
        "profile_points_simplified": int(len(points)),
        "profile_edges_simplified": int(len(edges)),
    }
    pindex = points.set_index("point_id")
    loop_ids = _ordered_closed_loop_point_ids(points, edges)
    valid_loop_ids = [pid for pid in loop_ids if pid in pindex.index]
    if len(valid_loop_ids) < 8:
        info["profile_simplification_note"] = "not_applied_too_few_valid_loop_points"
        return points, edges, info

    coords = pindex.loc[valid_loop_ids][["x_m", "z_m"]].to_numpy(float)
    chord = max(float(coords[:, 0].max() - coords[:, 0].min()), 1e-12)
    cleaned, removed = _remove_near_duplicate_loop_points(coords, min_spacing=max(float(min_spacing_chord) * chord, 1e-9))
    if len(cleaned) < 8:
        info["profile_simplification_note"] = "not_applied_too_few_cleaned_points"
        return points, edges, info
    rounded, te_rounding_info = _insert_tangent_te_cap(
        cleaned,
        chord,
        enabled=bool(te_rounding_enabled),
        n_arc_points=int(te_rounding_points),
        window_chord=float(te_rounding_window_chord),
        min_gap_chord=float(te_rounding_min_gap_chord),
    )
    if te_rounding_info.get("te_rounding_applied") and count_closed_polyline_self_intersections(rounded):
        te_rounding_info["te_rounding_applied"] = False
        te_rounding_info["te_rounding_fallback_reason"] = "rounded_te_cap_created_self_intersection"
        rounded = cleaned
    cleaned = rounded
    target_points = int(max(24, max_points if max_points > 0 else len(cleaned)))
    if te_rounding_info.get("te_rounding_applied"):
        # Preserve the explicit tangent-continuous TE cap points. A global
        # Catmull-Rom pass can overshoot at this tight closure and recreate the
        # curvature kink that the cap is designed to remove.
        dense = cleaned
    else:
        dense = _catmull_rom_closed(cleaned, samples_per_segment=8, alpha=0.5)
    resampled, adaptive_info = _adaptive_resample_closed_curve(
        dense,
        target_points,
        te_refinement_width_chord=float(te_refinement_width_chord),
        te_refinement_strength=float(te_refinement_strength),
        te_refinement_max_weight=float(te_refinement_max_weight),
    )
    if te_rounding_info.get("te_rounding_applied"):
        # A global arc-length resample can collapse a very small rounded cap to
        # one point even when the cap was constructed correctly. Replace only
        # that downstream path with the original ordered cap samples; the long
        # upper/lower path remains adaptively resampled.
        start_point = np.asarray(te_rounding_info.get("te_rounding_start_point_m"), dtype=float)
        end_point = np.asarray(te_rounding_info.get("te_rounding_end_point_m"), dtype=float)
        if start_point.shape == (2,) and end_point.shape == (2,):
            def nearest_side_index(coords_array: np.ndarray, point: np.ndarray, lower_side: bool) -> int:
                mid_z = 0.5 * (float(start_point[1]) + float(end_point[1]))
                side = coords_array[:, 1] <= mid_z if lower_side else coords_array[:, 1] >= mid_z
                candidates = np.flatnonzero(side)
                if len(candidates) == 0:
                    return int(np.argmin(np.linalg.norm(coords_array - point, axis=1)))
                local = np.linalg.norm(coords_array[candidates] - point, axis=1)
                return int(candidates[int(np.argmin(local))])

            def forward_path(first: int, last: int, count: int) -> list[int]:
                path = [first]
                cursor = first
                for _ in range(count):
                    if cursor == last:
                        break
                    cursor = (cursor + 1) % count
                    path.append(cursor)
                return path

            start_is_lower = float(start_point[1]) <= float(end_point[1])
            clean_start = nearest_side_index(cleaned, start_point, start_is_lower)
            clean_end = nearest_side_index(cleaned, end_point, not start_is_lower)
            cap_indices = forward_path(clean_start, clean_end, len(cleaned))
            reverse_cap_indices = list(reversed(forward_path(clean_end, clean_start, len(cleaned))))
            if float(np.mean(cleaned[reverse_cap_indices, 0])) > float(np.mean(cleaned[cap_indices, 0])):
                cap_indices = reverse_cap_indices
            preserved_cap = cleaned[cap_indices]

            sample_start = nearest_side_index(resampled, start_point, start_is_lower)
            sample_end = nearest_side_index(resampled, end_point, not start_is_lower)
            main_indices = forward_path(sample_end, sample_start, len(resampled))[1:-1]
            if len(preserved_cap) >= 3 and len(main_indices) >= 6:
                resampled = np.vstack([preserved_cap, resampled[main_indices]])
                adaptive_info["te_cap_points_preserved_after_global_resample"] = int(len(preserved_cap))
                adaptive_info["profile_points_after_te_cap_preservation"] = int(len(resampled))
    self_intersections = count_closed_polyline_self_intersections(resampled)
    if self_intersections:
        resampled, adaptive_info = _adaptive_resample_closed_curve(
            cleaned,
            target_points,
            te_refinement_width_chord=float(te_refinement_width_chord),
            te_refinement_strength=float(te_refinement_strength),
            te_refinement_max_weight=float(te_refinement_max_weight),
        )
        self_intersections = count_closed_polyline_self_intersections(resampled)
        adaptive_info["profile_preprocessing_fallback"] = "linear_resample_after_spline_self_intersection"
    if self_intersections:
        info.update(adaptive_info)
        info["profile_preprocessing_self_intersections"] = int(self_intersections)
        raise RuntimeError("Preprocessed closed profile still has self-intersections after linear fallback.")
    resampled, post_removed = _remove_near_duplicate_loop_points(
        resampled,
        min_spacing=max(0.35 * float(min_spacing_chord) * chord, 5.0e-10 * chord, 1.0e-12),
    )
    if len(resampled) < 8:
        raise RuntimeError("Preprocessed closed profile collapsed after post-resample duplicate removal.")
    self_intersections = count_closed_polyline_self_intersections(resampled)
    if self_intersections:
        raise RuntimeError("Preprocessed closed profile has self-intersections after post-resample cleanup.")

    source_sections = np.full(len(resampled), "PREPROCESSED_CLOSED_SPLINE", dtype=object)
    if te_rounding_info.get("te_rounding_applied"):
        endpoints = te_rounding_info.get("te_rounding_start_point_m"), te_rounding_info.get("te_rounding_end_point_m")
        if all(isinstance(point, list) and len(point) == 2 for point in endpoints):
            cap_mask = np.zeros(len(resampled), dtype=bool)
            start_point = np.asarray(endpoints[0], dtype=float)
            end_point = np.asarray(endpoints[1], dtype=float)
            start_idx = int(np.argmin(np.linalg.norm(resampled - start_point, axis=1)))
            end_idx = int(np.argmin(np.linalg.norm(resampled - end_point, axis=1)))

            def cyclic_path(first: int, last: int, direction: int) -> list[int]:
                path = [first]
                cursor = first
                for _ in range(len(resampled)):
                    if cursor == last:
                        break
                    cursor = (cursor + direction) % len(resampled)
                    path.append(cursor)
                return path

            forward = cyclic_path(start_idx, end_idx, 1)
            reverse = cyclic_path(start_idx, end_idx, -1)
            # Resampling preserves loop order, but selecting the path with the
            # larger mean x is an additional guard against swapped endpoints:
            # the inserted cap is the downstream path, the other path passes LE.
            cap_path = max(
                (forward, reverse),
                key=lambda path: (float(np.mean(resampled[path, 0])), -len(path)),
            )
            cap_mask[cap_path] = True
            if int(cap_mask.sum()) >= 3:
                source_sections[cap_mask] = "PREPROCESSED_TANGENT_TE_CAP"
                te_rounding_info["te_rounding_tagged_cap_points"] = int(cap_mask.sum())
                te_rounding_info["te_rounding_tag_method"] = "downstream_cyclic_path_between_nearest_cap_endpoints"
                te_rounding_info["te_rounding_tag_start_index"] = start_idx
                te_rounding_info["te_rounding_tag_end_index"] = end_idx
            else:
                te_rounding_info["te_rounding_tagged_cap_points"] = 0
                te_rounding_info["te_rounding_tag_method"] = "fallback_to_geometric_window_in_gmsh_writer"

    new_points = pd.DataFrame({
        "point_id": np.arange(1, len(resampled) + 1, dtype=int),
        "x_m": resampled[:, 0],
        "z_m": resampled[:, 1],
        "source_section": source_sections,
        "boundary_role": "airfoil_wall",
    })

    new_edges_rows = []
    for i in range(len(new_points)):
        new_edges_rows.append({
            "edge_id": i + 1,
            "start_point_id": i + 1,
            "end_point_id": ((i + 1) % len(new_points)) + 1,
            "patch_name": "airfoil_wall",
        })
    new_edges = pd.DataFrame(new_edges_rows)
    consecutive_ok = all(
        int(row.start_point_id) == i + 1 and int(row.end_point_id) == ((i + 1) % len(new_points)) + 1
        for i, row in new_edges.reset_index(drop=True).iterrows()
    )
    preprocessing_method = "deduplicate_centripetal_catmull_rom_adaptive_resample"
    if te_rounding_info.get("te_rounding_applied"):
        preprocessing_method = "deduplicate_tangent_te_cap_adaptive_resample"
    info.update({
        "profile_simplification_applied": True,
        "profile_preprocessing_applied": True,
        "profile_preprocessing_method": preprocessing_method,
        "profile_preprocessing_self_intersections": int(self_intersections),
        "profile_preprocessing_consecutive_edges": bool(consecutive_ok),
        "profile_preprocessing_closed_loop": bool(int(new_edges.iloc[-1].end_point_id) == int(new_points.iloc[0].point_id)),
        "profile_near_duplicate_points_removed": int(removed),
        "profile_post_resample_near_duplicate_points_removed": int(post_removed),
        "profile_preprocess_min_spacing_chord": float(min_spacing_chord),
        "profile_points_simplified": int(len(new_points)),
        "profile_edges_simplified": int(len(new_edges)),
        "profile_simplification_max_points": int(max_points),
    })
    info.update(te_rounding_info)
    info.update(adaptive_info)
    return normalize_points(new_points), normalize_edges(new_edges), info


def write_profile_audit(points: pd.DataFrame, edges: pd.DataFrame, out_dir: Path, variant: str) -> dict[str, Any]:
    coords = points.set_index("point_id")[["x_m", "z_m"]]
    patches = edges["patch_name"].value_counts().to_dict()
    line_lengths = []
    bad_edges = []
    for _, e in edges.iterrows():
        a, b = int(e.start_point_id), int(e.end_point_id)
        if a not in coords.index or b not in coords.index:
            bad_edges.append(int(e.edge_id)); continue
        dx = coords.loc[a, "x_m"] - coords.loc[b, "x_m"]
        dz = coords.loc[a, "z_m"] - coords.loc[b, "z_m"]
        line_lengths.append(float(math.hypot(dx, dz)))
    upper = curve_ids_for_patch(edges, lambda p: "upper" in p.lower())
    lower = curve_ids_for_patch(edges, lambda p: "lower" in p.lower())
    inlet = curve_ids_for_patch(edges, lambda p: "inlet" in p.lower())
    te = curve_ids_for_patch(edges, lambda p: "trailing" in p.lower() or p.lower() in {"te", "te_wall"})
    audit = {
        "variant": variant,
        "n_points": int(len(points)),
        "n_edges": int(len(edges)),
        "patch_counts": patches,
        "n_upper_edges": int(len(upper)),
        "n_lower_edges": int(len(lower)),
        "n_inlet_marker_edges": int(len(inlet)),
        "n_te_edges": int(len(te)),
        "bad_edges_missing_points": bad_edges,
        "min_edge_length": min(line_lengths) if line_lengths else None,
        "max_edge_length": max(line_lengths) if line_lengths else None,
        "x_min": float(points.x_m.min()),
        "x_max": float(points.x_m.max()),
        "z_min": float(points.z_m.min()),
        "z_max": float(points.z_m.max()),
    }
    write_json(out_dir / "profile_mesh_audit.json", audit)
    (out_dir / "profile_mesh_audit.txt").write_text("\n".join(f"{k}: {v}" for k, v in audit.items()) + "\n", encoding="utf-8")
    return audit


def write_profile_preprocessing_outputs(
    original_points: pd.DataFrame,
    original_edges: pd.DataFrame,
    processed_points: pd.DataFrame,
    processed_edges: pd.DataFrame,
    out_dir: Path,
    info: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    processed_points.to_csv(out_dir / "profile_preprocessed_points.csv", index=False, float_format="%.10f")
    processed_edges.to_csv(out_dir / "profile_preprocessed_edges.csv", index=False)
    write_json(out_dir / "profile_preprocessing_report.json", info)
    (out_dir / "profile_preprocessing_report.txt").write_text("\n".join(f"{k}: {v}" for k, v in sorted(info.items())) + "\n", encoding="utf-8")

    def ordered_xy(points_df: pd.DataFrame, edges_df: pd.DataFrame) -> np.ndarray:
        pidx = points_df.set_index("point_id")
        ids = _ordered_closed_loop_point_ids(points_df, edges_df)
        ids = [pid for pid in ids if pid in pidx.index]
        if not ids:
            ids = [int(v) for v in points_df["point_id"].tolist()]
        return pidx.loc[ids][["x_m", "z_m"]].to_numpy(float)

    orig = ordered_xy(original_points, original_edges)
    proc = ordered_xy(processed_points, processed_edges)
    proc_closed = np.vstack([proc, proc[0]]) if len(proc) else proc
    lengths = np.linalg.norm(np.diff(proc_closed, axis=0), axis=1) if len(proc_closed) > 1 else np.asarray([])

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    fig, axs = plt.subplots(2, 1, figsize=(9, 8), constrained_layout=True)
    ax = axs[0]
    if len(orig):
        ax.plot(orig[:, 0], orig[:, 1], color="0.75", linewidth=1.0, label="input polyline")
        ax.scatter(orig[:, 0], orig[:, 1], s=8, color="0.65", label=f"input points ({len(orig)})")
    if len(proc):
        ax.plot(proc_closed[:, 0], proc_closed[:, 1], color="#1f77b4", linewidth=1.4, label="preprocessed curve")
        ax.scatter(proc[:, 0], proc[:, 1], s=13, color="#d62728", label=f"preprocessed points ({len(proc)})")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, linewidth=0.3)
    ax.set_xlabel("x [m]")
    ax.set_ylabel("z [m]")
    ax.set_title("Profile preprocessing: point redistribution")
    ax.legend(loc="best", fontsize=8)

    ax = axs[1]
    if len(lengths):
        ax.plot(np.arange(1, len(lengths) + 1), lengths, color="#2ca02c", linewidth=1.2)
        ax.set_ylabel("segment length [m]")
    ax.set_xlabel("segment index around closed profile")
    ax.set_title("Progressive spacing after preprocessing")
    ax.grid(True, linewidth=0.3)
    fig.savefig(out_dir / "profile_preprocessing_distribution.png", dpi=180)
    plt.close(fig)

    if len(proc):
        x_te = float(proc[:, 0].max())
        chord = max(float(proc[:, 0].max() - proc[:, 0].min()), 1.0e-12)
        te_window = max(0.09 * chord, 3.0 * float(np.max(lengths)) if len(lengths) else 0.02 * chord)
        mask = proc[:, 0] >= x_te - te_window
        local = proc[mask]
        if len(local) >= 3:
            fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
            ax.plot(proc_closed[:, 0], proc_closed[:, 1], color="#1f77b4", linewidth=1.2, label="processed curve")
            ax.scatter(local[:, 0], local[:, 1], s=18, color="#d62728", label="TE-zone points")
            xmin = float(local[:, 0].min()) - 0.10 * te_window
            xmax = float(local[:, 0].max()) + 0.18 * te_window
            zmin = float(local[:, 1].min())
            zmax = float(local[:, 1].max())
            zpad = max(0.18 * (zmax - zmin), 0.008 * chord)
            ax.set_xlim(xmin, xmax)
            ax.set_ylim(zmin - zpad, zmax + zpad)
            ax.set_aspect("equal", adjustable="box")
            ax.grid(True, linewidth=0.3)
            ax.set_xlabel("x [m]")
            ax.set_ylabel("z [m]")
            ax.set_title("TE rounded-cap geometry zoom")
            ax.legend(loc="best", fontsize=8)
            fig.savefig(out_dir / "profile_preprocessing_te_zoom.png", dpi=220)
            plt.close(fig)


def write_geo_closed(points: pd.DataFrame, edges: pd.DataFrame, manifest: dict, mesh_cfg: dict, domain: str, out_geo: Path, variant: str, openfoam_3d: bool = False) -> dict[str, Any]:
    chord_m = float(manifest.get("chord_m", 1.0) or 1.0)
    surface_size_general_chord_manual = float(mesh_cfg.get("surface_size_general_chord", 0.003))
    surface_size_general_chord, surface_size_bl_info = _derive_surface_size_from_boundary_layer(
        mesh_cfg,
        chord_m,
        surface_size_general_chord_manual,
        enabled_key="surface_size_from_boundary_layer_enabled",
        factor_key="surface_size_bl_outer_factor",
        min_key="surface_size_bl_outer_min_chord",
        max_key="surface_size_bl_outer_max_chord",
    )
    lc_airfoil = surface_size_general_chord * chord_m
    lc_te = float(mesh_cfg.get("surface_size_rounded_te_chord", mesh_cfg.get("surface_size_te_chord", 0.001))) * chord_m
    lc_farfield = float(mesh_cfg.get("farfield_size_chord", 0.35)) * chord_m
    dpar = domain_params(domain, mesh_cfg)
    pindex = points.set_index("point_id")

    all_edges = edges[edges.start_point_id != edges.end_point_id].sort_values("edge_id").copy()
    requested_airfoil_curve_mode = str(mesh_cfg.get("debug_airfoil_curve_mode", "line_segments")).strip().lower()
    single_curve_experimental = bool(mesh_cfg.get("closed_single_curve_experimental", False))
    if single_curve_experimental:
        single_kind = str(mesh_cfg.get("closed_single_curve_kind", "BSpline")).strip().lower()
        requested_airfoil_curve_mode = "closed_spline" if single_kind == "spline" else "closed_bspline"
    rounded_te_geometry = bool(mesh_cfg.get("debug_te_rounding_enabled", False))
    airfoil_curve_mode = requested_airfoil_curve_mode
    if (
        rounded_te_geometry
        and not single_curve_experimental
        and bool(mesh_cfg.get("debug_enforce_rounded_te_curve_sections", True))
        and airfoil_curve_mode not in {"hybrid_te_spline", "hybrid_te_curve"}
    ):
        # Keep the rounded closure as real curve sections. A two-branch curve
        # leaves the TE as one endpoint and can collapse the BL into a point fan.
        airfoil_curve_mode = "hybrid_te_spline"
    use_closed_bspline = airfoil_curve_mode == "closed_bspline"
    use_closed_spline = airfoil_curve_mode == "closed_spline"
    use_spline_branches = airfoil_curve_mode == "spline_branches"
    use_hybrid_te_lines = airfoil_curve_mode in {"hybrid_te_lines", "hybrid_te_cap"}
    use_hybrid_te_spline = airfoil_curve_mode in {"hybrid_te_spline", "hybrid_te_curve"}
    curve_definitions: list[tuple[str, int, list[int], str]] = []
    transfinite_curve_nodes: dict[int, int] = {}
    transfinite_curve_distributions: dict[int, tuple[str, float]] = {}
    transfinite_node_multiplier = max(1.0, float(mesh_cfg.get("debug_airfoil_transfinite_node_multiplier", 1.0) or 1.0))
    ordered_ids_for_closed_curve: list[int] = []
    hybrid_te_line_curve_count = 0
    hybrid_te_spline_curve_count = 0
    if use_closed_bspline or use_closed_spline or use_spline_branches or use_hybrid_te_lines or use_hybrid_te_spline:
        ordered_ids = _ordered_closed_loop_point_ids(points, all_edges)
        ordered_ids = [pid for pid in ordered_ids if pid in pindex.index]
        ordered_ids_for_closed_curve = ordered_ids
    if (use_closed_bspline or use_closed_spline) and len(ordered_ids_for_closed_curve) >= 12:
        # A single closed curve avoids many independent TE line fragments. The
        # default is the Gmsh-4.8-stable BSpline path; the interpolating Spline
        # mode remains available for isolated geometry experiments.
        if single_curve_experimental and bool(mesh_cfg.get("closed_single_curve_start_at_te", True)):
            ordered_ids_for_closed_curve = _rotate_closed_ids_to_te(ordered_ids_for_closed_curve, pindex)
        closed_ids = ordered_ids_for_closed_curve + [ordered_ids_for_closed_curve[0]]
        kind = "BSpline" if use_closed_bspline else "Spline"
        label = "airfoil_closed_bspline_ordered_loop" if use_closed_bspline else "airfoil_closed_interpolating_spline_ordered_loop"
        curve_definitions = [
            (kind, 1001, closed_ids, label),
        ]
        if bool(mesh_cfg.get("debug_airfoil_transfinite", False)):
            base_nodes = max(len(closed_ids), int(mesh_cfg.get("debug_max_profile_points", len(closed_ids))) + 1)
            transfinite_curve_nodes[1001] = max(12, int(math.ceil(base_nodes * transfinite_node_multiplier)))
        if single_curve_experimental:
            target_nodes = int(mesh_cfg.get("closed_single_curve_target_nodes", mesh_cfg.get("closed_airfoil_target_nodes", len(closed_ids))) or len(closed_ids))
            transfinite_curve_nodes[1001] = max(transfinite_curve_nodes.get(1001, 0), 12, len(closed_ids), target_nodes)
            distribution = str(mesh_cfg.get("closed_single_curve_distribution", "bump")).strip().lower()
            if distribution == "bump":
                transfinite_curve_distributions[1001] = ("Bump", max(1.0e-9, float(mesh_cfg.get("closed_single_curve_bump", 0.06) or 0.06)))
            elif distribution == "progression":
                transfinite_curve_distributions[1001] = ("Progression", max(1.0, float(mesh_cfg.get("closed_airfoil_transfinite_progression", 1.0) or 1.0)))
    elif use_spline_branches:
        ordered_ids = ordered_ids_for_closed_curve
        if len(ordered_ids) >= 8:
            ordered_xy = pindex.loc[ordered_ids][["x_m", "z_m"]].to_numpy(float)
            i_te = int(np.argmax(ordered_xy[:, 0]))
            ids = ordered_ids[i_te:] + ordered_ids[:i_te]
            reordered_xy = pindex.loc[ids][["x_m", "z_m"]].to_numpy(float)
            i_le = int(np.argmin(reordered_xy[:, 0]))
            branch_te_to_le = ids[: i_le + 1]
            branch_le_to_te = ids[i_le:] + [ids[0]]
            if len(branch_te_to_le) >= 3 and len(branch_le_to_te) >= 3:
                curve_definitions = [
                    ("Spline", 1001, branch_te_to_le, "airfoil_branch_te_to_le"),
                    ("Spline", 1002, branch_le_to_te, "airfoil_branch_le_to_te"),
                ]
                transfinite_curve_nodes[1001] = max(12, int(math.ceil(len(branch_te_to_le) * transfinite_node_multiplier)))
                transfinite_curve_nodes[1002] = max(12, int(math.ceil(len(branch_le_to_te) * transfinite_node_multiplier)))
    elif use_hybrid_te_lines or use_hybrid_te_spline:
        ordered_ids = ordered_ids_for_closed_curve
        if len(ordered_ids) >= 12:
            ordered_xy = pindex.loc[ordered_ids][["x_m", "z_m"]].to_numpy(float)
            x_te_hybrid = float(ordered_xy[:, 0].max())
            x_le_hybrid = float(ordered_xy[:, 0].min())
            chord_hybrid = max(x_te_hybrid - x_le_hybrid, 1.0e-12)
            te_window = max(
                float(mesh_cfg.get("debug_te_curve_line_window_chord", mesh_cfg.get("debug_te_refinement_width_chord", 0.035))) * chord_hybrid,
                1.0e-12,
            )
            n_ordered = len(ordered_ids)
            explicit_cap_points = np.asarray([
                "TANGENT_TE_CAP" in str(pindex.loc[pid].get("source_section", ""))
                for pid in ordered_ids
            ], dtype=bool)
            te_segment = []
            if int(explicit_cap_points.sum()) >= 3:
                # Only edges whose two endpoints belong to the inserted rounded
                # cap are refined as TE. This prevents a chordwise x-window from
                # pulling straight upper/lower surface cells into the TE block.
                for i in range(n_ordered):
                    te_segment.append(bool(explicit_cap_points[i] and explicit_cap_points[(i + 1) % n_ordered]))
            else:
                for i in range(n_ordered):
                    a = ordered_xy[i]
                    b = ordered_xy[(i + 1) % n_ordered]
                    te_segment.append(bool(max(float(a[0]), float(b[0])) >= x_te_hybrid - te_window))
            if not any(te_segment):
                te_segment[int(np.argmax(ordered_xy[:, 0]))] = True

            start = 0
            for i, is_te in enumerate(te_segment):
                if is_te and not te_segment[(i - 1) % n_ordered]:
                    start = i
                    break
            ordered_segment_indices = list(range(start, n_ordered)) + list(range(0, start))
            cid = 1001
            cursor = 0
            while cursor < n_ordered:
                current_is_te = te_segment[ordered_segment_indices[cursor]]
                run_indices = []
                while cursor < n_ordered and te_segment[ordered_segment_indices[cursor]] == current_is_te:
                    run_indices.append(ordered_segment_indices[cursor])
                    cursor += 1
                if current_is_te:
                    if use_hybrid_te_lines:
                        for seg_idx in run_indices:
                            curve_definitions.append((
                                "Line",
                                cid,
                                [ordered_ids[seg_idx], ordered_ids[(seg_idx + 1) % n_ordered]],
                                "airfoil_explicit_te_line_segment",
                            ))
                            hybrid_te_line_curve_count += 1
                            cid += 1
                    else:
                        ids = [ordered_ids[run_indices[0]]]
                        ids.extend(ordered_ids[(seg_idx + 1) % n_ordered] for seg_idx in run_indices)
                        n_cap_segments = max(1, min(int(mesh_cfg.get("debug_te_cap_spline_segments", 5) or 5), len(ids) - 1))
                        boundaries = np.linspace(0, len(ids) - 1, n_cap_segments + 1, dtype=int)
                        for cap_index, (a_idx, b_idx) in enumerate(zip(boundaries[:-1], boundaries[1:]), start=1):
                            if b_idx <= a_idx:
                                continue
                            cap_ids = ids[a_idx : b_idx + 1]
                            kind = "Spline" if len(cap_ids) >= 3 else "Line"
                            curve_definitions.append((kind, cid, cap_ids, f"airfoil_dense_te_cap_spline_segment_{cap_index:02d}"))
                            hybrid_te_spline_curve_count += 1
                            cid += 1
                else:
                    ids = [ordered_ids[run_indices[0]]]
                    ids.extend(ordered_ids[(seg_idx + 1) % n_ordered] for seg_idx in run_indices)
                    kind = "Spline" if len(ids) >= 3 else "Line"
                    curve_definitions.append((kind, cid, ids, "airfoil_spline_grouped_profile_segment"))
                    if kind == "Spline":
                        if bool(mesh_cfg.get("debug_airfoil_transfinite", False)):
                            transfinite_curve_nodes[cid] = max(12, int(math.ceil(len(ids) * transfinite_node_multiplier)))
                        hybrid_te_spline_curve_count += 1
                    else:
                        hybrid_te_line_curve_count += 1
                    cid += 1
    if not curve_definitions:
        curve_definitions = [
            ("Line", 1000 + int(e.edge_id), [int(e.start_point_id), int(e.end_point_id)], str(e.patch_name))
            for _, e in all_edges.iterrows()
        ]
        use_spline_branches = False
        use_hybrid_te_lines = False
        use_hybrid_te_spline = False

    curve_ids = [cid for _, cid, _, _ in curve_definitions]
    curve_audit = audit_curve_connectivity(curve_definitions, pindex, out_geo.parent, label="airfoil_wall", closed_loop=True)
    te_cap_curve_ids = [
        cid
        for _, cid, _, label in curve_definitions
        if "te_cap" in str(label).lower() or "explicit_te" in str(label).lower()
    ]
    curve_lengths = {cid: _curve_point_length(ids, pindex) for _, cid, ids, _ in curve_definitions}
    total_curve_length = max(sum(curve_lengths.values()), 1.0e-15)
    te_curve_length = max(sum(curve_lengths.get(cid, 0.0) for cid in te_cap_curve_ids), 1.0e-15)
    non_te_curve_length = max(total_curve_length - te_curve_length, 1.0e-15)
    if bool(mesh_cfg.get("closed_airfoil_transfinite_enabled", bool(mesh_cfg.get("debug_airfoil_transfinite", False)))):
        target_total_nodes = max(24, int(mesh_cfg.get("closed_airfoil_target_nodes", 260) or 260))
        target_te_nodes = max(8, int(mesh_cfg.get("closed_te_target_nodes", 25) or 25))
        target_non_te_nodes = max(16, target_total_nodes - target_te_nodes + len(te_cap_curve_ids))
        transition_min = max(3, int(mesh_cfg.get("closed_te_transition_min_nodes", 16) or 16))
        te_adjacent_bump = bool(mesh_cfg.get("closed_te_neighbor_bump_enabled", False))
        te_adjacent_bump_value = max(1.0e-9, float(mesh_cfg.get("closed_te_neighbor_bump", 0.08) or 0.08))
        for _, cid, ids, label in curve_definitions:
            if len(ids) < 2:
                continue
            if cid in te_cap_curve_ids:
                continue
            transfinite_curve_nodes[cid] = max(
                transfinite_curve_nodes.get(cid, 0),
                _target_nodes_for_curve(
                    curve_lengths.get(cid, 0.0),
                    target_non_te_nodes,
                    non_te_curve_length,
                    min_nodes=transition_min,
                    existing_points=len(ids),
                ),
            )
            if te_adjacent_bump and te_cap_curve_ids:
                transfinite_curve_distributions[cid] = ("Bump", te_adjacent_bump_value)
    if bool(mesh_cfg.get("debug_te_transfinite_enabled", True)):
        minimum_te_nodes = max(3, int(mesh_cfg.get("debug_te_transfinite_min_nodes_per_curve", 5) or 5))
        target_te_nodes = max(
            minimum_te_nodes * max(1, len(te_cap_curve_ids)),
            int(mesh_cfg.get("closed_te_target_nodes", mesh_cfg.get("debug_te_transfinite_min_nodes_per_curve", 25)) or 25),
        )
        te_cap_distribution = str(mesh_cfg.get("closed_te_cap_distribution", "uniform")).strip().lower()
        te_cap_progression = max(1.0, float(mesh_cfg.get("closed_te_cap_progression", 1.0) or 1.0))
        for _, cid, ids, label in curve_definitions:
            if cid not in te_cap_curve_ids or len(ids) < 2:
                continue
            transfinite_curve_nodes[cid] = max(
                minimum_te_nodes,
                _target_nodes_for_curve(
                    curve_lengths.get(cid, 0.0),
                    target_te_nodes,
                    te_curve_length,
                    min_nodes=minimum_te_nodes,
                    existing_points=len(ids),
                ),
                int(math.ceil(curve_lengths.get(cid, 0.0) / max(lc_te, 1.0e-12))) + 1,
            )
            if te_cap_distribution == "bump":
                transfinite_curve_distributions[cid] = ("Bump", max(1.0e-9, float(mesh_cfg.get("closed_te_neighbor_bump", 0.08) or 0.08)))
            elif te_cap_distribution == "progression":
                transfinite_curve_distributions[cid] = ("Progression", te_cap_progression)
    bl_curve_ids = list(curve_ids)
    excluded_bl_curve_ids: list[int] = []
    if bool(mesh_cfg.get("boundary_layer_exclude_te_cap_from_bl", True)) and te_cap_curve_ids:
        candidate_bl_curve_ids = [cid for cid in curve_ids if cid not in set(te_cap_curve_ids)]
        if candidate_bl_curve_ids:
            bl_curve_ids = candidate_bl_curve_ids
            excluded_bl_curve_ids = list(te_cap_curve_ids)
    loop_ids_for_area = []
    for _, _, ids, _ in curve_definitions:
        loop_ids_for_area.extend(ids[:-1])
    if not loop_ids_for_area:
        loop_ids_for_area = [int(e.start_point_id) for _, e in all_edges.iterrows() if int(e.start_point_id) in pindex.index]
    loop_coords = [(float(pindex.loc[pid, "x_m"]), float(pindex.loc[pid, "z_m"])) for pid in loop_ids_for_area if pid in pindex.index]
    area = polygon_area_xy(loop_coords)
    # Airfoil is a hole, so force clockwise orientation for the inner loop.
    if area > 0:
        inner_loop_entries = [-cid for cid in reversed(curve_ids)]
    else:
        inner_loop_entries = curve_ids

    # The first unstructured triangle shares the tangential outer BL edge, not
    # the normal first-cell height. Use the larger of the BL-derived normal
    # scale and a representative tangential wall spacing to avoid an abrupt
    # prism/triangle size jump.
    closed_target_nodes = max(24, int(mesh_cfg.get("closed_wall_target_nodes", 260) or 260))
    closed_tangential_interface_size = total_curve_length / max(closed_target_nodes - len(curve_ids), 1)
    lc_airfoil = max(lc_airfoil, 0.85 * closed_tangential_interface_size)
    surface_size_bl_info["tangential_wall_spacing_chord"] = float(closed_tangential_interface_size / chord_m)
    surface_size_bl_info["active_surface_size_chord"] = float(lc_airfoil / chord_m)
    surface_size_bl_info["interface_size_rule"] = "max(last_BL_layer_scale, 0.85*mean_tangential_wall_spacing)"

    lines: list[str] = []
    lines += [
        "// Auto-generated by canonical ramair_2d_mesh_builder.py",
        f"// variant: {variant}",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {int(mesh_cfg.get('gmsh_mesh_algorithm_2d', 5))}; // 2D algorithm: 5=Delaunay, 6=Frontal-Delaunay",
        f"Mesh.RandomFactor = {float(mesh_cfg.get('gmsh_random_factor', 1.0e-7)):.12g};",
        f"Mesh.RandomSeed = {int(mesh_cfg.get('gmsh_random_seed', 1))};",
        "Mesh.Smoothing = 10;",
        "Mesh.Optimize = 1;",
        "Mesh.OptimizeNetgen = 1;",
        "// Sizes are controlled by transfinite curves and explicit fields.",
        "// Gmsh manual section 1.2.2 recommends disabling automatic boundary",
        "// extension when a background field fully specifies the transition.",
        "Mesh.MeshSizeFromPoints = 0;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 0;",
        f"lc_airfoil = {lc_airfoil:.12g};",
        f"lc_te = {lc_te:.12g};",
        f"lc_farfield = {lc_farfield:.12g};",
    ]
    x_te = float(points["x_m"].max())
    te_size_window = max(float(mesh_cfg.get("debug_te_refinement_width_chord", 0.035)) * chord_m, 1.0e-12)
    explicit_te_point_ids = {
        int(p.point_id)
        for _, p in points.iterrows()
        if "TANGENT_TE_CAP" in str(p.get("source_section", ""))
    }
    for _, p in points.iterrows():
        is_te_cap = int(p.point_id) in explicit_te_point_ids
        if not explicit_te_point_ids:
            is_te_cap = float(p.x_m) >= x_te - te_size_window
        lc_name = "lc_te" if is_te_cap else "lc_airfoil"
        lines.append(f"Point({int(p.point_id)}) = {{{float(p.x_m):.12g}, {float(p.z_m):.12g}, 0, {lc_name}}};")
    for kind, cid, ids, label in curve_definitions:
        lines.append(f"{kind}({cid}) = {{{', '.join(map(str, ids))}}}; // {label}")
    if transfinite_curve_nodes:
        progression = max(1.0, float(mesh_cfg.get("closed_airfoil_transfinite_progression", 1.0) or 1.0))
        for cid, n_nodes in transfinite_curve_nodes.items():
            method, value = transfinite_curve_distributions.get(cid, ("Progression", progression))
            method_l = str(method).strip().lower()
            if method_l == "bump":
                lines.append(f"Transfinite Curve {{{cid}}} = {int(n_nodes)} Using Bump {float(value):.12g};")
            elif method_l == "none" or float(value) == 1.0:
                lines.append(f"Transfinite Curve {{{cid}}} = {int(n_nodes)};")
            else:
                lines.append(f"Transfinite Curve {{{cid}}} = {int(n_nodes)} Using Progression {float(value):.12g};")

    base = 200000
    far_lines, far_curves = farfield_geometry_lines(base, domain, mesh_cfg, chord_m)
    lines += far_lines

    far_loop = base + 20
    foil_loop = base + 21
    fluid_surface = base + 30
    lines.append(f"Line Loop({far_loop}) = {{{', '.join(map(str, far_curves))}}};")
    lines.append(f"Line Loop({foil_loop}) = {{{', '.join(map(str, inner_loop_entries))}}};")
    lines.append(f"Plane Surface({fluid_surface}) = {{{far_loop}, {foil_loop}}};")

    # Boundary layer request. If it prevents meshing on a local Gmsh version, rerun with
    # --no-boundary-layer. The .geo remains valid without this block.
    request_bl = bool(mesh_cfg.get("request_boundary_layer", True)) and int(mesh_cfg.get("boundary_layer_layers", 0)) > 0
    bl: dict[str, Any] = {}
    if request_bl:
        bl = boundary_layer_parameters(mesh_cfg, chord_m, fallback_first_cell_chord=1.0e-5)
        y1 = float(bl["first_cell_height"])
        growth = float(bl["growth"])
        n_layers = int(bl["n_layers"])
        thickness = float(bl["total_thickness"])
        bl_quads = 1 if bool(mesh_cfg.get("recombine_boundary_layer", False)) else 0
        leading_edge_point = int(points.loc[points["x_m"].idxmin(), "point_id"])
        trailing_edge_point = int(points.loc[points["x_m"].idxmax(), "point_id"])
        fan_points = max(n_layers + 4, 8)
        te_fan_setting = mesh_cfg.get("debug_boundary_layer_te_fan_points", None)
        te_fan_points = max(8, int(te_fan_setting if te_fan_setting is not None else n_layers + 8))
        bl_lines = [
            "",
            "// Demonstration wall boundary-layer field. For production CFD, refine y+ settings separately.",
            "Field[1] = BoundaryLayer;",
            f"Field[1].CurvesList = {{{', '.join(map(str, bl_curve_ids))}}};",
            f"Field[1].Size = {y1:.12g};",
            f"Field[1].Ratio = {growth:.12g};",
            f"Field[1].Thickness = {thickness:.12g};",
            f"Field[1].Quads = {bl_quads};",
        ]
        if bool(mesh_cfg.get("closed_boundary_layer_intersect_metrics", True)):
            bl_lines.append("Field[1].IntersectMetrics = 1;")
        bl_lines.append(f"Field[1].AnisoMax = {float(mesh_cfg.get('closed_boundary_layer_aniso_max_deg', 170.0)):.12g};")
        fan_point_ids: list[int] = []
        fan_point_sizes: list[int] = []
        if bool(mesh_cfg.get("debug_boundary_layer_fan_at_le", True)):
            fan_point_ids.append(leading_edge_point)
            fan_point_sizes.append(fan_points)
        rounded_te_has_explicit_curves = bool(te_cap_curve_ids) or rounded_te_geometry
        if bool(mesh_cfg.get("debug_boundary_layer_fan_at_te", False)) and not rounded_te_has_explicit_curves:
            fan_point_ids.append(trailing_edge_point)
            fan_point_sizes.append(te_fan_points)
        if fan_point_ids:
            bl_lines += [
                f"Field[1].FanPointsList = {{{', '.join(map(str, fan_point_ids))}}};",
                f"Field[1].FanPointsSizesList = {{{', '.join(map(str, fan_point_sizes))}}};",
            ]
        bl_lines.append("BoundaryLayer Field = 1;")
        lines += bl_lines

    te_geometry_metrics = _closed_te_geometry_metrics(
        curve_definitions,
        pindex,
        chord_m,
        te_window_chord=float(mesh_cfg.get("debug_te_refinement_width_chord", 0.025) or 0.025),
        bl_total_thickness_chord=float(bl["total_thickness_chord"]) if request_bl else None,
    )

    background_field_ids: list[int] = []
    next_field_id = 2
    nearfield_requested = bool(mesh_cfg.get("nearfield_refinement_enabled", False))
    if nearfield_requested:
        dist_field_id = next_field_id
        near_threshold_field_id = next_field_id + 1
        middle_threshold_field_id = next_field_id + 2
        far_threshold_field_id = next_field_id + 3
        next_field_id += 4
        dist_min = float(mesh_cfg.get("nearfield_dist_min_chord", 0.20)) * chord_m
        dist_mid = float(mesh_cfg.get("nearfield_intermediate_dist_chord", 0.35)) * chord_m
        dist_max = float(mesh_cfg.get("nearfield_dist_max_chord", 1.10)) * chord_m
        intermediate_size = float(mesh_cfg.get("nearfield_intermediate_size_chord", 0.035)) * chord_m
        outer_size = float(mesh_cfg.get("closed_nearfield_outer_size_chord", 0.18)) * chord_m
        far_transition = float(mesh_cfg.get("closed_farfield_transition_dist_chord", 9.0)) * chord_m
        if not (0.0 <= dist_min < dist_mid < dist_max < far_transition):
            raise ValueError(
                "Closed transition distances must satisfy 0 <= near < intermediate < outer < farfield transition."
            )
        if not (lc_airfoil <= intermediate_size <= outer_size <= lc_farfield):
            raise ValueError(
                "Closed sizes must satisfy wall <= intermediate <= outer <= farfield."
            )
        lines += [
            "",
            "// Three-stage transition: preserve the fine aerodynamic nearfield,",
            "// reach a moderate size around 6c, then grow slowly to the far boundary.",
            f"Field[{dist_field_id}] = Distance;",
            f"Field[{dist_field_id}].CurvesList = {{{', '.join(map(str, curve_ids))}}};",
            f"Field[{dist_field_id}].NumPointsPerCurve = {int(mesh_cfg.get('nearfield_distance_sampling') or max(160, min(900, len(points) * 3)))};",
            f"Field[{near_threshold_field_id}] = Threshold;",
            f"Field[{near_threshold_field_id}].InField = {dist_field_id};",
            f"Field[{near_threshold_field_id}].SizeMin = lc_airfoil;",
            f"Field[{near_threshold_field_id}].SizeMax = {intermediate_size:.12g};",
            f"Field[{near_threshold_field_id}].DistMin = {dist_min:.12g};",
            f"Field[{near_threshold_field_id}].DistMax = {dist_mid:.12g};",
            f"Field[{near_threshold_field_id}].Sigmoid = 0;",
            f"Field[{near_threshold_field_id}].StopAtDistMax = 1;",
            f"Field[{middle_threshold_field_id}] = Threshold;",
            f"Field[{middle_threshold_field_id}].InField = {dist_field_id};",
            f"Field[{middle_threshold_field_id}].SizeMin = {intermediate_size:.12g};",
            f"Field[{middle_threshold_field_id}].SizeMax = {outer_size:.12g};",
            f"Field[{middle_threshold_field_id}].DistMin = {dist_mid:.12g};",
            f"Field[{middle_threshold_field_id}].DistMax = {dist_max:.12g};",
            f"Field[{middle_threshold_field_id}].Sigmoid = 0;",
            f"Field[{middle_threshold_field_id}].StopAtDistMax = 1;",
            f"Field[{far_threshold_field_id}] = Threshold;",
            f"Field[{far_threshold_field_id}].InField = {dist_field_id};",
            f"Field[{far_threshold_field_id}].SizeMin = {outer_size:.12g};",
            f"Field[{far_threshold_field_id}].SizeMax = lc_farfield;",
            f"Field[{far_threshold_field_id}].DistMin = {dist_max:.12g};",
            f"Field[{far_threshold_field_id}].DistMax = {far_transition:.12g};",
            f"Field[{far_threshold_field_id}].Sigmoid = 0;",
        ]
        background_field_ids.extend(
            [near_threshold_field_id, middle_threshold_field_id, far_threshold_field_id]
        )

    wake_requested = bool(mesh_cfg.get("wake_refinement_enabled", False))
    if wake_requested:
        wake_field_id = next_field_id
        next_field_id += 1
        x_te = float(points["x_m"].max())
        z_mid = 0.5 * (float(points["z_m"].min()) + float(points["z_m"].max()))
        wake_len = float(mesh_cfg.get("wake_refinement_length_chord", 4.0)) * chord_m
        wake_half_height = 0.5 * float(mesh_cfg.get("wake_refinement_height_chord", 0.5)) * chord_m
        wake_size = float(mesh_cfg.get("wake_size_chord", 0.08)) * chord_m
        transition = max(0.25 * chord_m, 2.0 * wake_size)
        lines += [
            "",
            "// Coarse wake refinement for proof-of-concept debugging.",
            f"Field[{wake_field_id}] = Box;",
            f"Field[{wake_field_id}].VIn = {wake_size:.12g};",
            f"Field[{wake_field_id}].VOut = lc_farfield;",
            f"Field[{wake_field_id}].XMin = {x_te - 0.05 * chord_m:.12g};",
            f"Field[{wake_field_id}].XMax = {x_te + wake_len:.12g};",
            f"Field[{wake_field_id}].YMin = {z_mid - wake_half_height:.12g};",
            f"Field[{wake_field_id}].YMax = {z_mid + wake_half_height:.12g};",
            f"Field[{wake_field_id}].Thickness = {transition:.12g};",
        ]
        background_field_ids.append(wake_field_id)

    if len(background_field_ids) == 1:
        lines.append(f"Background Field = {background_field_ids[0]};")
    elif len(background_field_ids) > 1:
        min_field_id = next_field_id
        lines += [
            "",
            "// Combine active refinement fields by taking the smallest requested cell size.",
            f"Field[{min_field_id}] = Min;",
            f"Field[{min_field_id}].FieldsList = {{{', '.join(map(str, background_field_ids))}}};",
            f"Background Field = {min_field_id};",
        ]

    physical_groups = ["airfoil_wall", "farfield", "fluid"]
    if openfoam_3d:
        span = float(mesh_cfg.get("spanwise_thickness_chord", 0.01)) * chord_m
        layers = int(mesh_cfg.get("spanwise_layers", 1))
        if span <= 0.0:
            raise ValueError("spanwise_thickness_chord must be positive for OpenFOAM-ready meshes.")
        if layers != 1:
            raise ValueError("OpenFOAM 2D cases must use exactly one spanwise layer.")
        lateral_offset = 2
        far_lateral = [f"out[{lateral_offset + i}]" for i in range(len(far_curves))]
        airfoil_lateral = [f"out[{lateral_offset + len(far_curves) + i}]" for i in range(len(curve_ids))]
        lines += [
            "",
            "// One-cell-thick 3D extrusion required by OpenFOAM 2D.",
            f"out[] = Extrude {{0, 0, {span:.12g}}} {{ Surface{{{fluid_surface}}}; Layers{{{layers}}}; Recombine; }};",
            f"Physical Surface(\"frontAndBack\") = {{{fluid_surface}, out[0]}};",
            f"Physical Surface(\"farfield\") = {{{', '.join(far_lateral)}}};",
            f"Physical Surface(\"airfoil_wall\") = {{{', '.join(airfoil_lateral)}}};",
            "Physical Volume(\"fluid\") = {out[1]};",
        ]
        physical_groups = ["airfoil_wall", "farfield", "frontAndBack", "fluid"]
    else:
        lines.append(f"Physical Line(\"airfoil_wall\") = {{{', '.join(map(str, curve_ids))}}};")
        lines.append(f"Physical Line(\"farfield\") = {{{', '.join(map(str, far_curves))}}};")
        lines.append(f"Physical Surface(\"fluid\") = {{{fluid_surface}}};")
    out_geo.parent.mkdir(parents=True, exist_ok=True)
    out_geo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "surface_kind": "closed_airfoil_external_fluid_with_hole",
        "physical_groups": physical_groups,
        "ram_air_inlet_is_physical_patch": False,
        "boundary_layer_requested": request_bl,
        "boundary_layer_layers_requested": int(mesh_cfg.get("boundary_layer_layers", 0) or 0),
        "boundary_layer_quads_requested": bool(mesh_cfg.get("recombine_boundary_layer", False)),
        "boundary_layer_first_cell_height_chord": float(bl["first_cell_height_chord"]) if request_bl else 0.0,
        "boundary_layer_first_cell_height_m": float(bl["first_cell_height"]) if request_bl else 0.0,
        "boundary_layer_requested_first_cell_height_chord": float(bl["requested_first_cell_height_chord"]) if request_bl else 0.0,
        "boundary_layer_first_cell_height_source": bl.get("first_cell_height_source") if request_bl else None,
        "boundary_layer_yplus_estimate": estimate_boundary_layer_from_yplus_inputs(mesh_cfg, chord_m) if request_bl else None,
        "boundary_layer_total_thickness_chord": float(bl["total_thickness_chord"]) if request_bl else 0.0,
        "boundary_layer_raw_total_thickness_chord": float(bl["raw_total_thickness_chord"]) if request_bl else 0.0,
        "boundary_layer_total_thickness_limited": bool(bl["total_thickness_limited"]) if request_bl else False,
        "closed_boundary_layer_aniso_max_deg": float(mesh_cfg.get("closed_boundary_layer_aniso_max_deg", 170.0) or 170.0),
        "closed_boundary_layer_intersect_metrics": bool(mesh_cfg.get("closed_boundary_layer_intersect_metrics", True)),
        **te_geometry_metrics,
        "nearfield_refinement_requested": nearfield_requested,
        "nearfield_dist_min_chord": float(mesh_cfg.get("nearfield_dist_min_chord", 0.0) or 0.0),
        "nearfield_intermediate_dist_chord": float(mesh_cfg.get("nearfield_intermediate_dist_chord", 0.0) or 0.0),
        "nearfield_dist_max_chord": float(mesh_cfg.get("nearfield_dist_max_chord", 0.0) or 0.0),
        "nearfield_intermediate_size_chord": float(mesh_cfg.get("nearfield_intermediate_size_chord", 0.0) or 0.0),
        "nearfield_outer_size_chord": float(mesh_cfg.get("closed_nearfield_outer_size_chord", 0.0) or 0.0),
        "farfield_transition_dist_chord": float(mesh_cfg.get("closed_farfield_transition_dist_chord", 0.0) or 0.0),
        "wake_refinement_requested": wake_requested,
        "wake_refinement_length_chord": float(mesh_cfg.get("wake_refinement_length_chord", 0.0) or 0.0),
        "wake_refinement_height_chord": float(mesh_cfg.get("wake_refinement_height_chord", 0.0) or 0.0),
        "wake_size_chord": float(mesh_cfg.get("wake_size_chord", 0.0) or 0.0),
        "airfoil_polygon_area": area,
        "airfoil_curve_mode": (
            "closed_bspline"
            if use_closed_bspline and curve_ids == [1001]
            else (
                "closed_spline"
                if use_closed_spline and curve_ids == [1001]
                else ("spline_branches" if use_spline_branches else ("hybrid_te_lines" if use_hybrid_te_lines else ("hybrid_te_spline" if use_hybrid_te_spline else "line_segments")))
            )
        ),
        "airfoil_curve_mode_requested": requested_airfoil_curve_mode,
        "closed_wall_curve_method": str(mesh_cfg.get("closed_wall_curve_method", "")),
        "closed_wall_target_nodes": int(mesh_cfg.get("closed_wall_target_nodes", mesh_cfg.get("closed_single_curve_target_nodes", 0)) or 0),
        "closed_te_bump_strength": float(mesh_cfg.get("closed_te_bump_strength", mesh_cfg.get("closed_single_curve_bump", 0.0)) or 0.0),
        "closed_profile_target_points": int(mesh_cfg.get("closed_profile_target_points", mesh_cfg.get("debug_max_profile_points", 0)) or 0),
        "closed_profile_min_spacing_chord": float(mesh_cfg.get("closed_profile_min_spacing_chord", mesh_cfg.get("debug_profile_min_spacing_chord", 0.0)) or 0.0),
        "closed_single_curve_experimental": bool(single_curve_experimental),
        "closed_single_curve_kind": str(mesh_cfg.get("closed_single_curve_kind", "BSpline")),
        "closed_single_curve_target_nodes": int(mesh_cfg.get("closed_single_curve_target_nodes", 0) or 0),
        "closed_single_curve_start_at_te": bool(mesh_cfg.get("closed_single_curve_start_at_te", True)),
        "closed_single_curve_distribution": str(mesh_cfg.get("closed_single_curve_distribution", "bump")),
        "closed_single_curve_bump": float(mesh_cfg.get("closed_single_curve_bump", 0.0) or 0.0),
        "rounded_te_curve_sections_enforced": bool(
            rounded_te_geometry and not single_curve_experimental and airfoil_curve_mode != requested_airfoil_curve_mode
        ),
        "airfoil_curve_count": int(len(curve_ids)),
        "airfoil_curve_ids": curve_ids,
        "gmsh_curve_connectivity_valid": bool(curve_audit.get("valid")),
        "gmsh_curve_connectivity_issue_count": int(curve_audit.get("issue_count", 0) or 0),
        "gmsh_curve_connectivity_issues": curve_audit.get("issues", []),
        "gmsh_curve_connectivity_audit_json": str(out_geo.parent / "airfoil_wall_curve_connectivity_audit.json"),
        "gmsh_curve_connectivity_audit_csv": str(out_geo.parent / "airfoil_wall_curve_connectivity_audit.csv"),
        "boundary_layer_curve_ids": bl_curve_ids if request_bl else [],
        "boundary_layer_excluded_te_curve_ids": excluded_bl_curve_ids if request_bl else [],
        "boundary_layer_exclude_te_cap_from_bl": bool(mesh_cfg.get("boundary_layer_exclude_te_cap_from_bl", True)),
        "airfoil_transfinite_curve_nodes": transfinite_curve_nodes,
        "airfoil_transfinite_curve_distributions": {
            str(cid): {"method": method, "value": value}
            for cid, (method, value) in transfinite_curve_distributions.items()
        },
        "airfoil_transfinite_node_multiplier": float(transfinite_node_multiplier),
        "closed_airfoil_transfinite_enabled": bool(mesh_cfg.get("closed_airfoil_transfinite_enabled", False)),
        "closed_airfoil_target_nodes": int(mesh_cfg.get("closed_airfoil_target_nodes", 0) or 0),
        "closed_airfoil_transfinite_progression": float(mesh_cfg.get("closed_airfoil_transfinite_progression", 1.0) or 1.0),
        "closed_te_target_nodes": int(mesh_cfg.get("closed_te_target_nodes", 0) or 0),
        "closed_te_transition_min_nodes": int(mesh_cfg.get("closed_te_transition_min_nodes", 0) or 0),
        "closed_te_neighbor_bump_enabled": bool(mesh_cfg.get("closed_te_neighbor_bump_enabled", False)),
        "closed_te_neighbor_bump": float(mesh_cfg.get("closed_te_neighbor_bump", 0.0) or 0.0),
        "closed_te_cap_distribution": str(mesh_cfg.get("closed_te_cap_distribution", "uniform")),
        "closed_te_cap_progression": float(mesh_cfg.get("closed_te_cap_progression", 1.0) or 1.0),
        "surface_size_from_boundary_layer": surface_size_bl_info,
        "closed_te_cap_curve_ids": te_cap_curve_ids,
        "gmsh_manual_discretization_note": (
            "Tangential wall/BL divisions are controlled with Transfinite Curve node counts; "
            "BoundaryLayer Size/Ratio/Thickness control the normal layer stack."
        ),
        "hybrid_te_line_curve_count": int(hybrid_te_line_curve_count),
        "hybrid_te_spline_curve_count": int(hybrid_te_spline_curve_count),
        "boundary_layer_fan_at_le": bool(mesh_cfg.get("debug_boundary_layer_fan_at_le", True)),
        "boundary_layer_fan_at_te": bool(mesh_cfg.get("debug_boundary_layer_fan_at_te", False)) and not rounded_te_geometry and not bool(te_cap_curve_ids),
        "boundary_layer_te_fan_suppressed_for_rounded_cap": bool(mesh_cfg.get("debug_boundary_layer_fan_at_te", False)) and (rounded_te_geometry or bool(te_cap_curve_ids)),
        "boundary_layer_te_fan_points": int(mesh_cfg.get("debug_boundary_layer_te_fan_points", 0) or 0),
        "inner_loop_orientation_entries_first_5": inner_loop_entries[:5],
        "openfoam_ready": bool(openfoam_3d),
        "extruded_3d": bool(openfoam_3d),
        "spanwise_layers": int(mesh_cfg.get("spanwise_layers", 1)) if openfoam_3d else 0,
        "spanwise_thickness_chord": float(mesh_cfg.get("spanwise_thickness_chord", 0.01)) if openfoam_3d else 0.0,
    }


def _open_wall_coordinate_sections(
    points: pd.DataFrame,
    edges: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return upper-lip->TE, upper-TE->lower-TE and TE->lower-lip coordinates."""
    pidx = points.set_index("point_id")
    clean = edges[edges.start_point_id != edges.end_point_id].sort_values("edge_id").copy()
    patches = clean["patch_name"].astype(str).str.lower()

    def sequence(fragment: pd.DataFrame) -> list[int]:
        ordered = fragment.sort_values("edge_id").reset_index(drop=True)
        if ordered.empty:
            return []
        result = [int(ordered.iloc[0].start_point_id)]
        result.extend(int(value) for value in ordered["end_point_id"])
        return result

    upper = sequence(clean[patches.str.contains("outer_upper_wall", na=False)])
    lower = sequence(clean[patches.str.contains("outer_lower_wall", na=False)])
    te = sequence(clean[patches.str.contains("trailing_edge_wall", na=False)])
    if len(upper) < 3 or len(lower) < 3 or len(te) < 2:
        raise RuntimeError("The open profile needs consecutive upper, lower and trailing-edge wall edges.")
    # Export contract: upper TE->lip, lower lip->TE and TE lower->upper.
    if float(pidx.loc[upper[0], "x_m"]) < float(pidx.loc[upper[-1], "x_m"]):
        upper.reverse()
    if float(pidx.loc[lower[0], "x_m"]) > float(pidx.loc[lower[-1], "x_m"]):
        lower.reverse()
    lower_te = lower[-1]
    upper_te = upper[0]
    if te[0] == upper_te and te[-1] == lower_te:
        te.reverse()
    if te[0] != lower_te or te[-1] != upper_te:
        raise RuntimeError("The rounded TE does not connect lower TE to upper TE consecutively.")

    chord_scale = max(
        float(points["x_m"].max()) - float(points["x_m"].min()),
        1.0e-12,
    )
    minimum_spacing = max(5.0e-7 * chord_scale, 1.0e-12)

    def xy(ids: list[int]) -> np.ndarray:
        coords = pidx.loc[ids][["x_m", "z_m"]].to_numpy(float)
        cleaned = [coords[0]]
        for point in coords[1:-1]:
            if float(np.linalg.norm(point - cleaned[-1])) >= minimum_spacing:
                cleaned.append(point)
        if float(np.linalg.norm(coords[-1] - cleaned[-1])) < minimum_spacing:
            cleaned[-1] = coords[-1]
        else:
            cleaned.append(coords[-1])
        return np.asarray(cleaned, dtype=float)

    upper_lip_to_te = xy(list(reversed(upper)))
    upper_te_to_lower_te = xy(list(reversed(te)))
    lower_te_to_lip = xy(list(reversed(lower)))
    for name, coords in (
        ("upper wall", upper_lip_to_te),
        ("rounded TE", upper_te_to_lower_te),
        ("lower wall", lower_te_to_lip),
    ):
        lengths = np.linalg.norm(np.diff(coords, axis=0), axis=1)
        if len(lengths) == 0 or float(np.min(lengths)) <= 1.0e-13:
            raise RuntimeError(f"The {name} contains a repeated or numerically coincident point.")
    return upper_lip_to_te, upper_te_to_lower_te, lower_te_to_lip


def build_base_profile_inlet_arc(
    open_points: pd.DataFrame,
    open_edges: pd.DataFrame,
    base_points: pd.DataFrame,
    base_edges: pd.DataFrame,
    open_chord_m: float,
    blend_fraction: float,
    alignment_mode: str = "similarity",
) -> tuple[np.ndarray, dict[str, Any]]:
    """Extract lower-lip->LE->upper-lip from the uncut base profile.

    The default similarity alignment maps the two base-profile anchors to the
    cut lips with one rotation, translation and uniform scale. It therefore
    preserves the complete uncut inlet-arc shape instead of bending its ends.
    The former endpoint smoothstep remains available for legacy comparisons.
    """
    upper_wall, _, lower_wall = _open_wall_coordinate_sections(open_points, open_edges)
    upper_lip = upper_wall[0]
    lower_lip = lower_wall[-1]
    base = base_points.copy()
    if {"x_norm", "z_norm"}.issubset(base.columns):
        base["_x"] = base["x_norm"].astype(float) * open_chord_m
        base["_z"] = base["z_norm"].astype(float) * open_chord_m
    else:
        x_min = float(base["x_m"].min())
        x_span = max(float(base["x_m"].max()) - x_min, 1.0e-15)
        scale = open_chord_m / x_span
        base["_x"] = (base["x_m"].astype(float) - x_min) * scale
        base["_z"] = base["z_m"].astype(float) * scale
    base_index = base.set_index("point_id")
    loop_ids = _ordered_closed_loop_point_ids(base_points, base_edges)
    loop_ids = [point_id for point_id in loop_ids if point_id in base_index.index]
    if len(loop_ids) < 3:
        raise ValueError("The base profile does not form a usable closed point loop.")
    loop_xy = base_index.loc[loop_ids][["_x", "_z"]].to_numpy(float)
    upper_anchor_index = int(np.argmin(np.linalg.norm(loop_xy - upper_lip, axis=1)))
    lower_anchor_index = int(np.argmin(np.linalg.norm(loop_xy - lower_lip, axis=1)))
    if upper_anchor_index == lower_anchor_index:
        raise RuntimeError("Both open-profile lips mapped to the same base-profile point.")

    def cyclic_path(start: int, stop: int, step: int) -> np.ndarray:
        indices = [start]
        index = start
        for _ in range(len(loop_ids)):
            if index == stop:
                break
            index = (index + step) % len(loop_ids)
            indices.append(index)
        return loop_xy[indices]

    candidates = (
        cyclic_path(lower_anchor_index, upper_anchor_index, 1),
        cyclic_path(lower_anchor_index, upper_anchor_index, -1),
    )
    # The inlet continuation is the path through the leading edge, i.e. the
    # candidate with the smallest chordwise coordinate, never the TE path.
    raw = min(candidates, key=lambda path: (float(np.min(path[:, 0])), float(np.mean(path[:, 0]))))
    if len(raw) < 3:
        raise RuntimeError("The selected base-profile LE path has fewer than three points.")
    raw_lower_error = float(np.linalg.norm(raw[0] - lower_lip))
    raw_upper_error = float(np.linalg.norm(raw[-1] - upper_lip))
    segment_lengths = np.linalg.norm(np.diff(raw, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
    if cumulative[-1] <= 1.0e-12 * open_chord_m:
        raise RuntimeError("The extracted base-profile inlet arc has zero length.")
    mode = str(alignment_mode or "similarity").strip().lower()
    blend = min(0.45, max(0.05, float(blend_fraction)))
    similarity_scale = None
    similarity_rotation_deg = None
    if mode == "similarity":
        source_vector = raw[-1] - raw[0]
        target_vector = upper_lip - lower_lip
        source_length_sq = float(np.dot(source_vector, source_vector))
        if source_length_sq <= (1.0e-12 * open_chord_m) ** 2:
            raise RuntimeError("The base-profile inlet anchors are numerically coincident.")
        real = float(np.dot(target_vector, source_vector) / source_length_sq)
        imag = float(
            (
                target_vector[1] * source_vector[0]
                - target_vector[0] * source_vector[1]
            )
            / source_length_sq
        )
        transform = np.array([[real, -imag], [imag, real]], dtype=float)
        corrected = (raw - raw[0]) @ transform.T + lower_lip
        similarity_scale = float(math.hypot(real, imag))
        similarity_rotation_deg = float(math.degrees(math.atan2(imag, real)))
    elif mode == "endpoint_blend":
        t = cumulative / cumulative[-1]

        def smoothstep(value: np.ndarray) -> np.ndarray:
            clipped = np.clip(value, 0.0, 1.0)
            return clipped * clipped * (3.0 - 2.0 * clipped)

        start_weight = 1.0 - smoothstep(t / blend)
        end_weight = smoothstep((t - (1.0 - blend)) / blend)
        corrected = (
            raw
            + start_weight[:, None] * (lower_lip - raw[0])
            + end_weight[:, None] * (upper_lip - raw[-1])
        )
    else:
        raise ValueError(
            "open_base_inlet_alignment_mode must be similarity or endpoint_blend."
        )
    corrected[0] = lower_lip
    corrected[-1] = upper_lip
    min_spacing = max(1.0e-10 * open_chord_m, 1.0e-13)
    cleaned = [corrected[0]]
    removed = 0
    for point in corrected[1:]:
        if float(np.linalg.norm(point - cleaned[-1])) > min_spacing:
            cleaned.append(point)
        else:
            removed += 1
    arc = np.asarray(cleaned, dtype=float)
    if len(arc) < 3:
        raise RuntimeError("The processed base-profile inlet arc has fewer than three unique points.")
    if count_open_polyline_self_intersections(arc):
        raise RuntimeError("The processed base-profile inlet arc self-intersects.")

    def tangent_mismatch_deg(first: np.ndarray, second: np.ndarray) -> float:
        denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
        if denominator <= 1.0e-30:
            return float("nan")
        cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
        return float(math.degrees(math.acos(cosine)))

    lower_mismatch = tangent_mismatch_deg(
        lower_wall[-1] - lower_wall[-2],
        arc[1] - arc[0],
    )
    upper_mismatch = tangent_mismatch_deg(
        arc[-1] - arc[-2],
        upper_wall[1] - upper_wall[0],
    )
    return arc, {
        "base_upper_anchor_index": upper_anchor_index,
        "base_lower_anchor_index": lower_anchor_index,
        "base_upper_anchor_point_id": int(loop_ids[upper_anchor_index]),
        "base_lower_anchor_point_id": int(loop_ids[lower_anchor_index]),
        "base_inlet_control_points": int(len(arc)),
        "base_inlet_near_duplicate_points_removed": int(removed),
        "base_inlet_arc_length_chord": float(np.linalg.norm(np.diff(arc, axis=0), axis=1).sum() / open_chord_m),
        "base_inlet_alignment_mode": mode,
        "base_inlet_exact_similarity_of_uncut_arc": mode == "similarity",
        "base_inlet_similarity_scale": similarity_scale,
        "base_inlet_similarity_rotation_deg": similarity_rotation_deg,
        "base_inlet_blend_fraction": blend,
        "base_inlet_raw_lower_anchor_error_m": raw_lower_error,
        "base_inlet_raw_upper_anchor_error_m": raw_upper_error,
        "base_inlet_lower_endpoint_error_m": float(np.linalg.norm(arc[0] - lower_lip)),
        "base_inlet_upper_endpoint_error_m": float(np.linalg.norm(arc[-1] - upper_lip)),
        "base_inlet_lower_tangent_mismatch_deg": lower_mismatch,
        "base_inlet_upper_tangent_mismatch_deg": upper_mismatch,
        "base_inlet_self_intersections": 0,
    }


def write_geo_open_zero_thickness_base_profile(
    points: pd.DataFrame,
    edges: pd.DataFrame,
    manifest: dict[str, Any],
    base_points: pd.DataFrame,
    base_edges: pd.DataFrame,
    base_manifest: dict[str, Any],
    mesh_cfg: dict[str, Any],
    domain: str,
    out_geo: Path,
    variant: str,
) -> dict[str, Any]:
    """Write a zero-thickness open airfoil with a base-profile BL bridge.

    External and cavity surfaces use duplicate wall and inlet curves. Only the
    two inlet copies are stitched after 2D meshing; duplicate wall nodes remain
    separate, so both sides become one physical airfoil_wall baffle.
    """
    chord_m = float(manifest.get("chord_m", 1.0) or 1.0)
    upper, te, lower = _open_wall_coordinate_sections(points, edges)
    inlet_arc, arc_info = build_base_profile_inlet_arc(
        points,
        edges,
        base_points,
        base_edges,
        chord_m,
        float(mesh_cfg.get("open_base_inlet_blend_fraction", 0.30) or 0.30),
        str(mesh_cfg.get("open_base_inlet_alignment_mode", "similarity")),
    )
    closed_control_polygon = np.vstack([upper, te[1:], lower[1:], inlet_arc[1:-1]])
    if count_closed_polyline_self_intersections(closed_control_polygon):
        raise RuntimeError("The cut wall plus uncut-base inlet arc is self-intersecting.")
    closed_segments = np.linalg.norm(
        np.diff(np.vstack([closed_control_polygon, closed_control_polygon[:1]]), axis=0),
        axis=1,
    )
    connectivity_tolerance = max(1.0e-12, 1.0e-10 * chord_m)
    minimum_control_segment = float(closed_segments.min()) if len(closed_segments) else 0.0
    rounded_control_points = {
        (round(float(x) / connectivity_tolerance), round(float(y) / connectivity_tolerance))
        for x, y in closed_control_polygon
    }
    duplicate_control_points = int(len(closed_control_polygon) - len(rounded_control_points))
    curve_connectivity_valid = bool(
        minimum_control_segment > connectivity_tolerance
        and duplicate_control_points == 0
    )
    if not curve_connectivity_valid:
        raise RuntimeError(
            "The open zero-thickness contour contains a zero/near-zero segment "
            "or a duplicated non-closing control point."
        )

    request_bl = (
        bool(mesh_cfg.get("request_boundary_layer", True))
        and bool(mesh_cfg.get("open_diagnostic_boundary_layer_enabled", True))
        and int(mesh_cfg.get("open_boundary_layer_layers", 0) or 0) > 0
    )
    bl = (
        boundary_layer_parameters(
            mesh_cfg,
            chord_m,
            first_cell_key="open_first_cell_height_chord_override",
            first_cell_m_key="open_first_cell_height_m_override",
            growth_key="open_boundary_layer_growth",
            layers_key="open_boundary_layer_layers",
            thickness_key="open_boundary_layer_total_thickness_chord_override",
            fallback_first_cell_chord=1.0e-5,
        )
        if request_bl
        else {}
    )
    manual_surface_size = float(mesh_cfg.get("open_surface_size_general_chord", 0.004))
    surface_size_chord, surface_size_bl_info = _derive_surface_size_from_boundary_layer(
        mesh_cfg,
        chord_m,
        manual_surface_size,
        enabled_key="open_surface_size_from_boundary_layer_enabled",
        factor_key="open_surface_size_bl_outer_factor",
        min_key="open_surface_size_bl_outer_min_chord",
        max_key="open_surface_size_bl_outer_max_chord",
        first_cell_key="open_first_cell_height_chord_override",
        first_cell_m_key="open_first_cell_height_m_override",
        growth_key="open_boundary_layer_growth",
        layers_key="open_boundary_layer_layers",
    )
    lc_wall = surface_size_chord * chord_m
    lc_far = float(mesh_cfg.get("open_farfield_size_chord", 0.75)) * chord_m
    lengths = {
        "upper": float(np.linalg.norm(np.diff(upper, axis=0), axis=1).sum()),
        "te": float(np.linalg.norm(np.diff(te, axis=0), axis=1).sum()),
        "lower": float(np.linalg.norm(np.diff(lower, axis=0), axis=1).sum()),
        "inlet": float(np.linalg.norm(np.diff(inlet_arc, axis=0), axis=1).sum()),
    }
    target_nodes = max(
        120,
        int(
            mesh_cfg.get(
                "open_zero_thickness_contour_target_nodes",
                mesh_cfg.get("open_surface_target_nodes", 3700),
            )
            or 3700
        ),
    )
    contour_nodes, contour_spacing = _uniform_closed_curve_node_counts(lengths, target_nodes)
    requested_te_nodes = max(
        3,
        int(
            mesh_cfg.get(
                "open_zero_thickness_te_transfinite_min_nodes",
                mesh_cfg.get("open_te_transfinite_min_nodes", 32),
            )
            or 32
        ),
    )
    if contour_nodes["te"] < requested_te_nodes:
        extra_te_nodes = requested_te_nodes - contour_nodes["te"]
        available_upper = max(0, contour_nodes["upper"] - 3)
        available_lower = max(0, contour_nodes["lower"] - 3)
        if extra_te_nodes > available_upper + available_lower:
            raise ValueError(
                "open_te_transfinite_min_nodes leaves too few nodes for the upper/lower walls."
            )
        upper_share = lengths["upper"] / max(lengths["upper"] + lengths["lower"], 1.0e-30)
        upper_reduction = min(
            available_upper,
            max(0, int(round(extra_te_nodes * upper_share))),
        )
        lower_reduction = extra_te_nodes - upper_reduction
        if lower_reduction > available_lower:
            shortfall = lower_reduction - available_lower
            lower_reduction = available_lower
            upper_reduction += shortfall
        contour_nodes["upper"] -= upper_reduction
        contour_nodes["lower"] -= lower_reduction
        contour_nodes["te"] = requested_te_nodes
    realized_curve_spacings = {
        key: lengths[key] / max(contour_nodes[key] - 1, 1)
        for key in lengths
    }
    spacing_ratio = (
        max(realized_curve_spacings.values()) / min(realized_curve_spacings.values())
        if realized_curve_spacings and min(realized_curve_spacings.values()) > 0.0
        else float("inf")
    )
    upper_nodes = contour_nodes["upper"]
    te_nodes = contour_nodes["te"]
    lower_nodes = contour_nodes["lower"]
    inlet_nodes = contour_nodes["inlet"]
    inner_factor = min(1.0, max(0.15, float(mesh_cfg.get("open_inner_wall_node_factor", 0.45) or 0.45)))
    inner_min = max(12, int(mesh_cfg.get("open_inner_wall_min_nodes", 80) or 80))
    inner_upper_nodes = min(upper_nodes, max(inner_min, int(round(inner_factor * upper_nodes))))
    inner_lower_nodes = min(lower_nodes, max(inner_min, int(round(inner_factor * lower_nodes))))
    inner_te_factor = min(1.0, max(0.10, float(mesh_cfg.get("open_inner_te_node_factor", 0.35) or 0.35)))
    inner_te_nodes = min(
        te_nodes,
        max(int(mesh_cfg.get("open_inner_te_min_nodes", 22) or 22), int(round(inner_te_factor * te_nodes))),
    )
    inner_bump_enabled = bool(mesh_cfg.get("open_inner_wall_end_bump_enabled", True))
    inner_bump = max(
        1.0e-6,
        float(mesh_cfg.get("open_inner_wall_end_bump_strength", 0.35) or 0.35),
    )
    inlet_tangential_size = lengths["inlet"] / max(inlet_nodes - 1, 1)
    first_bl_height = float(bl.get("first_cell_height", 0.0)) if bl else 0.0
    last_bl_height = (
        first_bl_height
        * float(bl.get("growth", 1.0)) ** max(int(bl.get("n_layers", 1)) - 1, 0)
        if bl
        else 0.0
    )
    inlet_size_strategy = str(
        mesh_cfg.get("open_cavity_inlet_size_strategy", "hybrid_boundary_extension")
        or "hybrid_boundary_extension"
    ).strip().lower()
    if inlet_size_strategy not in {
        "hybrid_boundary_extension",
        "boundary_extension",
        "boundary_uniform",
        "staged_explicit",
    }:
        raise ValueError(
            "open_cavity_inlet_size_strategy must be boundary_extension, "
            "hybrid_boundary_extension, boundary_uniform or staged_explicit."
        )
    # Retained only for the legacy staged-explicit comparison. The production
    # boundary-extension path inherits the actual tangential boundary-edge
    # length and is therefore independent of y1.
    interface_normal_y1_factor = max(
        1.0,
        float(mesh_cfg.get("open_zero_thickness_inlet_normal_y1_factor", 8.0) or 8.0),
    )
    legacy_interface_size = max(
        min(
            inlet_tangential_size,
            interface_normal_y1_factor * first_bl_height
            if first_bl_height > 0.0
            else inlet_tangential_size,
        ),
        1.0e-8 * chord_m,
    )
    interface_size = (
        legacy_interface_size
        if inlet_size_strategy in {"hybrid_boundary_extension", "staged_explicit"}
        else inlet_tangential_size
    )

    external_point_base = 500000
    internal_point_base = 600000
    groups = (upper, te, lower, inlet_arc)
    external_ids: list[list[int]] = []
    internal_ids: list[list[int]] = []
    cursor = 0
    for coords in groups:
        external_ids.append([external_point_base + cursor + i for i in range(len(coords))])
        internal_ids.append([internal_point_base + cursor + i for i in range(len(coords))])
        cursor += len(coords) + 10
    # Reuse entity endpoints inside each surface so each loop is exactly closed.
    for ids in (external_ids, internal_ids):
        ids[1][0] = ids[0][-1]
        ids[2][0] = ids[1][-1]
        ids[3][0] = ids[2][-1]
        ids[3][-1] = ids[0][0]

    ext_curves = [5101, 5102, 5103, 5104]
    int_curves = [5201, 5202, 5203, 5204]
    far_base = 700000
    far_lines, far_curves = farfield_geometry_lines(far_base, domain, mesh_cfg, chord_m)
    far_loop = far_base + 20
    ext_loop = far_base + 21
    int_loop = far_base + 22
    ext_surface = far_base + 30
    int_surface = far_base + 31

    lines = [
        "// Open ram-air zero-thickness mesh with base-profile curved inlet interface.",
        f"// variant: {variant}; base profile: {mesh_cfg.get('open_base_profile_variant', 'reference_uncut')}",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {int(mesh_cfg.get('gmsh_mesh_algorithm_2d', 5))};",
        f"Mesh.RandomFactor = {float(mesh_cfg.get('gmsh_random_factor', 1.0e-7)):.12g};",
        f"Mesh.RandomSeed = {int(mesh_cfg.get('gmsh_random_seed', 1))};",
        "Mesh.Smoothing = 8;",
        "Mesh.Optimize = 1;",
        "Mesh.OptimizeNetgen = 1;",
        "Mesh.MeshSizeFromPoints = 0;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 0;",
        f"lc_wall = {lc_wall:.12g};",
        f"lc_farfield = {lc_far:.12g};",
    ]
    emitted_points: set[int] = set()
    for coords, ext_ids, int_ids in zip(groups, external_ids, internal_ids):
        for xy, ext_id, int_id in zip(coords, ext_ids, int_ids):
            if ext_id not in emitted_points:
                lines.append(f"Point({ext_id}) = {{{xy[0]:.12g}, {xy[1]:.12g}, 0, lc_wall}};")
                emitted_points.add(ext_id)
            if int_id not in emitted_points:
                lines.append(f"Point({int_id}) = {{{xy[0]:.12g}, {xy[1]:.12g}, 0, lc_wall}};")
                emitted_points.add(int_id)
    for curve_id, ids in zip(ext_curves, external_ids):
        lines.append(f"Spline({curve_id}) = {{{', '.join(map(str, ids))}}};")
    for curve_id, ids in zip(int_curves, internal_ids):
        lines.append(f"Spline({curve_id}) = {{{', '.join(map(str, ids))}}};")
    lines += [
        f"Transfinite Curve {{{ext_curves[0]}}} = {upper_nodes} Using Progression 1;",
        f"Transfinite Curve {{{ext_curves[1]}}} = {te_nodes} Using Progression 1;",
        f"Transfinite Curve {{{ext_curves[2]}}} = {lower_nodes} Using Progression 1;",
        f"Transfinite Curve {{{ext_curves[3]}, {int_curves[3]}}} = {inlet_nodes} Using Progression 1;",
        (
            f"Transfinite Curve {{{int_curves[0]}}} = {inner_upper_nodes} Using Bump {inner_bump:.12g};"
            if inner_bump_enabled
            else f"Transfinite Curve {{{int_curves[0]}}} = {inner_upper_nodes} Using Progression 1;"
        ),
        f"Transfinite Curve {{{int_curves[1]}}} = {inner_te_nodes} Using Progression 1;",
        (
            f"Transfinite Curve {{{int_curves[2]}}} = {inner_lower_nodes} Using Bump {inner_bump:.12g};"
            if inner_bump_enabled
            else f"Transfinite Curve {{{int_curves[2]}}} = {inner_lower_nodes} Using Progression 1;"
        ),
    ]
    lines += far_lines
    loop_area = polygon_area_xy(map(tuple, closed_control_polygon))
    ext_entries = ext_curves if loop_area < 0.0 else [-cid for cid in reversed(ext_curves)]
    int_entries = int_curves if loop_area > 0.0 else [-cid for cid in reversed(int_curves)]
    lines += [
        f"Line Loop({far_loop}) = {{{', '.join(map(str, far_curves))}}};",
        f"Line Loop({ext_loop}) = {{{', '.join(map(str, ext_entries))}}};",
        f"Line Loop({int_loop}) = {{{', '.join(map(str, int_entries))}}};",
        f"Plane Surface({ext_surface}) = {{{far_loop}, {ext_loop}}};",
        f"Plane Surface({int_surface}) = {{{int_loop}}};",
    ]
    if request_bl:
        lines += [
            "Field[1] = BoundaryLayer;",
            f"Field[1].CurvesList = {{{', '.join(map(str, ext_curves))}}};",
            f"Field[1].ExcludedSurfacesList = {{{int_surface}}};",
            f"Field[1].Size = {float(bl['first_cell_height']):.12g};",
            f"Field[1].Ratio = {float(bl['growth']):.12g};",
            f"Field[1].Thickness = {float(bl['total_thickness']):.12g};",
            f"Field[1].Quads = {1 if bool(mesh_cfg.get('open_recombine_boundary_layer', True)) else 0};",
            f"Field[1].AnisoMax = {float(mesh_cfg.get('open_boundary_layer_aniso_max_deg', 30.0)):.12g};",
            "BoundaryLayer Field = 1;",
        ]

    next_field = 10
    background_fields: list[int] = []
    sigmoid = 1 if bool(mesh_cfg.get("open_transition_sigmoid_enabled", True)) else 0
    dist_min = float(mesh_cfg.get("open_nearfield_dist_min_chord", 0.04)) * chord_m
    dist_mid = float(mesh_cfg.get("open_nearfield_intermediate_dist_chord", 0.35)) * chord_m
    dist_max = float(mesh_cfg.get("open_nearfield_dist_max_chord", 2.0)) * chord_m
    far_transition = float(mesh_cfg.get("open_farfield_transition_dist_chord", 15.0)) * chord_m
    middle_size = float(mesh_cfg.get("open_nearfield_intermediate_size_chord", 0.060)) * chord_m
    outer_size = float(mesh_cfg.get("open_nearfield_outer_size_chord", 0.28)) * chord_m
    if not (0.0 <= dist_min < dist_mid < dist_max < far_transition):
        raise ValueError("Open exterior distances must increase from near wall to farfield transition.")
    previous_size = lc_wall
    previous_distance = dist_min
    exterior_stages = ((middle_size, dist_mid), (outer_size, dist_max), (lc_far, far_transition))
    for stage_index, (size, distance) in enumerate(exterior_stages):
        distance_id, threshold_id, restrict_id = next_field, next_field + 1, next_field + 2
        next_field += 3
        stop_at_dist_max = 0 if stage_index == len(exterior_stages) - 1 else 1
        lines += [
            f"Field[{distance_id}] = Distance;",
            f"Field[{distance_id}].CurvesList = {{{', '.join(map(str, ext_curves))}}};",
            f"Field[{distance_id}].NumPointsPerCurve = {int(mesh_cfg.get('open_nearfield_distance_sampling', 240) or 240)};",
            f"Field[{threshold_id}] = Threshold;",
            f"Field[{threshold_id}].InField = {distance_id};",
            f"Field[{threshold_id}].SizeMin = {previous_size:.12g};",
            f"Field[{threshold_id}].SizeMax = {size:.12g};",
            f"Field[{threshold_id}].DistMin = {previous_distance:.12g};",
            f"Field[{threshold_id}].DistMax = {distance:.12g};",
            f"Field[{threshold_id}].Sigmoid = {sigmoid};",
            f"Field[{threshold_id}].StopAtDistMax = {stop_at_dist_max};",
            f"Field[{restrict_id}] = Restrict;",
            f"Field[{restrict_id}].InField = {threshold_id};",
            f"Field[{restrict_id}].SurfacesList = {{{ext_surface}}};",
        ]
        background_fields.append(restrict_id)
        previous_size, previous_distance = size, distance

    cavity_wall_size = float(mesh_cfg.get("open_cavity_wall_size_chord", 0.004)) * chord_m
    cavity_core_size = float(mesh_cfg.get("open_cavity_size_chord", 0.028)) * chord_m
    cavity_transition = float(mesh_cfg.get("open_cavity_wall_transition_chord", 0.22)) * chord_m
    cavity_wall_size = min(cavity_wall_size, cavity_core_size)
    distance_id, threshold_id, restrict_id = next_field, next_field + 1, next_field + 2
    next_field += 3
    lines += [
        "// Progressive cavity-wall growth; the core size remains active beyond the transition.",
        f"Field[{distance_id}] = Distance;",
        f"Field[{distance_id}].CurvesList = {{{', '.join(map(str, int_curves[:3]))}}};",
        f"Field[{distance_id}].NumPointsPerCurve = {int(mesh_cfg.get('open_internal_inlet_distance_sampling', 140) or 140)};",
        f"Field[{threshold_id}] = Threshold;",
        f"Field[{threshold_id}].InField = {distance_id};",
        f"Field[{threshold_id}].SizeMin = {cavity_wall_size:.12g};",
        f"Field[{threshold_id}].SizeMax = {cavity_core_size:.12g};",
        f"Field[{threshold_id}].DistMin = 0;",
        f"Field[{threshold_id}].DistMax = {max(cavity_transition, 6.0 * cavity_wall_size):.12g};",
        f"Field[{threshold_id}].Sigmoid = {sigmoid};",
        f"Field[{threshold_id}].StopAtDistMax = 0;",
        f"Field[{restrict_id}] = Restrict;",
        f"Field[{restrict_id}].InField = {threshold_id};",
        f"Field[{restrict_id}].SurfacesList = {{{int_surface}}};",
    ]
    background_fields.append(restrict_id)

    inlet_dist_max = float(mesh_cfg.get("open_internal_inlet_dist_max_chord", 0.18)) * chord_m
    if inlet_dist_max <= 0.0:
        raise ValueError("open_internal_inlet_dist_max_chord must be positive.")
    inlet_match_transition = 0.0
    inlet_match_factor = 1.0
    inlet_matching_size = inlet_tangential_size
    inlet_transition = 0.0
    inlet_intermediate_size = inlet_tangential_size
    inlet_extension_power = max(
        0.05,
        float(mesh_cfg.get("open_cavity_inlet_extension_power", 0.75) or 0.75),
    )
    if inlet_size_strategy in {"hybrid_boundary_extension", "boundary_extension"}:
        inlet_extend_id, inlet_restrict_id = next_field, next_field + 1
        next_field += 2
        lines += [
            "// Gmsh Extend inherits the local average inlet boundary-edge length.",
            "// It deliberately does not use y1: y1 is the BL-normal height, while",
            "// this field controls tangentially matched cavity triangles.",
            f"Field[{inlet_extend_id}] = Extend;",
            f"Field[{inlet_extend_id}].CurvesList = {{{int_curves[3]}}};",
            f"Field[{inlet_extend_id}].SurfacesList = {{{int_surface}}};",
            f"Field[{inlet_extend_id}].DistMax = {inlet_dist_max:.12g};",
            f"Field[{inlet_extend_id}].Power = {inlet_extension_power:.12g};",
            f"Field[{inlet_extend_id}].SizeMax = {cavity_core_size:.12g};",
            f"Field[{inlet_restrict_id}] = Restrict;",
            f"Field[{inlet_restrict_id}].InField = {inlet_extend_id};",
            f"Field[{inlet_restrict_id}].SurfacesList = {{{int_surface}}};",
        ]
        background_fields.append(inlet_restrict_id)
        if inlet_size_strategy == "hybrid_boundary_extension":
            inlet_match_transition = float(
                mesh_cfg.get(
                    "open_internal_inlet_matching_transition_chord",
                    0.002,
                )
                or 0.0
            ) * chord_m
            if inlet_match_transition <= 0.0 or inlet_match_transition >= inlet_dist_max:
                raise ValueError(
                    "Hybrid inlet matching transition must be positive and smaller "
                    "than open_internal_inlet_dist_max_chord."
                )
            match_distance_id, match_threshold_id, match_restrict_id = (
                next_field,
                next_field + 1,
                next_field + 2,
            )
            next_field += 3
            lines += [
                "// Short cell-centre compatibility strip. It only bridges y1 to",
                "// the tangential edge length; Extend governs the remaining cavity.",
                f"Field[{match_distance_id}] = Distance;",
                f"Field[{match_distance_id}].CurvesList = {{{int_curves[3]}}};",
                f"Field[{match_distance_id}].NumPointsPerCurve = {int(mesh_cfg.get('open_internal_inlet_distance_sampling', 140) or 140)};",
                f"Field[{match_threshold_id}] = Threshold;",
                f"Field[{match_threshold_id}].InField = {match_distance_id};",
                f"Field[{match_threshold_id}].SizeMin = {legacy_interface_size:.12g};",
                f"Field[{match_threshold_id}].SizeMax = {inlet_tangential_size:.12g};",
                f"Field[{match_threshold_id}].DistMin = 0;",
                f"Field[{match_threshold_id}].DistMax = {inlet_match_transition:.12g};",
                f"Field[{match_threshold_id}].Sigmoid = {sigmoid};",
                f"Field[{match_threshold_id}].StopAtDistMax = 1;",
                f"Field[{match_restrict_id}] = Restrict;",
                f"Field[{match_restrict_id}].InField = {match_threshold_id};",
                f"Field[{match_restrict_id}].SurfacesList = {{{int_surface}}};",
            ]
            background_fields.append(match_restrict_id)
    elif inlet_size_strategy == "boundary_uniform":
        inlet_constant_id, inlet_restrict_id = next_field, next_field + 1
        next_field += 2
        lines += [
            "// Diagnostic alternative: use the measured inlet spacing throughout the cavity.",
            f"Field[{inlet_constant_id}] = MathEval;",
            f'Field[{inlet_constant_id}].F = "{inlet_tangential_size:.12g}";',
            f"Field[{inlet_restrict_id}] = Restrict;",
            f"Field[{inlet_restrict_id}].InField = {inlet_constant_id};",
            f"Field[{inlet_restrict_id}].SurfacesList = {{{int_surface}}};",
        ]
        background_fields.append(inlet_restrict_id)
    else:
        inlet_match_transition = float(
            mesh_cfg.get("open_internal_inlet_matching_transition_chord", 0.012)
            or 0.0
        ) * chord_m
        inlet_match_factor = max(
            0.1,
            float(
                mesh_cfg.get("open_internal_inlet_matching_size_factor", 1.0)
                or 1.0
            ),
        )
        inlet_transition = float(
            mesh_cfg.get("open_internal_inlet_near_transition_chord", 0.04)
        ) * chord_m
        inlet_intermediate_size = float(
            mesh_cfg.get("open_internal_inlet_intermediate_size_chord", 0.0035)
        ) * chord_m
        inlet_intermediate_size = min(
            cavity_core_size,
            max(interface_size, inlet_intermediate_size),
        )
        inlet_matching_size = min(
            inlet_intermediate_size,
            max(interface_size, inlet_match_factor * inlet_tangential_size),
        )
        matching_stage_enabled = inlet_match_transition > 0.0
        valid_distances = (
            0.0 < inlet_match_transition < inlet_transition < inlet_dist_max
            if matching_stage_enabled
            else 0.0 < inlet_transition < inlet_dist_max
        )
        if not valid_distances:
            raise ValueError(
                "Open interior inlet distances must satisfy 0 < matching transition < "
                "near transition < total transition, or set matching transition to 0."
            )
        inlet_distance_id = next_field
        next_field += 1
        lines += [
            "// Legacy staged inlet transition retained for controlled comparisons.",
            f"Field[{inlet_distance_id}] = Distance;",
            f"Field[{inlet_distance_id}].CurvesList = {{{int_curves[3]}}};",
            f"Field[{inlet_distance_id}].NumPointsPerCurve = {int(mesh_cfg.get('open_internal_inlet_distance_sampling', 140) or 140)};",
        ]
        previous_inlet_size = interface_size
        previous_inlet_distance = 0.0
        inlet_stages = []
        if matching_stage_enabled:
            inlet_stages.append((inlet_matching_size, inlet_match_transition))
        inlet_stages.extend(
            (
                (inlet_intermediate_size, inlet_transition),
                (cavity_core_size, inlet_dist_max),
            )
        )
        for stage_index, (stage_size, stage_distance) in enumerate(inlet_stages):
            inlet_threshold_id, inlet_restrict_id = next_field, next_field + 1
            next_field += 2
            lines += [
                f"Field[{inlet_threshold_id}] = Threshold;",
                f"Field[{inlet_threshold_id}].InField = {inlet_distance_id};",
                f"Field[{inlet_threshold_id}].SizeMin = {previous_inlet_size:.12g};",
                f"Field[{inlet_threshold_id}].SizeMax = {stage_size:.12g};",
                f"Field[{inlet_threshold_id}].DistMin = {previous_inlet_distance:.12g};",
                f"Field[{inlet_threshold_id}].DistMax = {stage_distance:.12g};",
                f"Field[{inlet_threshold_id}].Sigmoid = {sigmoid};",
                f"Field[{inlet_threshold_id}].StopAtDistMax = {0 if stage_index == len(inlet_stages) - 1 else 1};",
                f"Field[{inlet_restrict_id}] = Restrict;",
                f"Field[{inlet_restrict_id}].InField = {inlet_threshold_id};",
                f"Field[{inlet_restrict_id}].SurfacesList = {{{int_surface}}};",
            ]
            background_fields.append(inlet_restrict_id)
            previous_inlet_size = stage_size
            previous_inlet_distance = stage_distance

    internal_te_active_size = None
    if bool(mesh_cfg.get("open_internal_te_refinement_enabled", True)):
        inner_te_spacing = lengths["te"] / max(inner_te_nodes - 1, 1)
        internal_te_active_size = min(
            cavity_wall_size,
            max(
                2.0 * first_bl_height,
                float(mesh_cfg.get("open_internal_te_size_factor", 0.75) or 0.75)
                * inner_te_spacing,
            ),
        )
        te_transition = max(
            float(mesh_cfg.get("open_internal_te_dist_max_chord", 0.10) or 0.10)
            * chord_m,
            8.0 * internal_te_active_size,
        )
        te_distance_id, te_threshold_id, te_restrict_id = next_field, next_field + 1, next_field + 2
        next_field += 3
        lines += [
            "// Local internal-TE refinement; this does not copy the full exterior discretization.",
            f"Field[{te_distance_id}] = Distance;",
            f"Field[{te_distance_id}].CurvesList = {{{int_curves[1]}}};",
            f"Field[{te_distance_id}].NumPointsPerCurve = {max(40, 2 * inner_te_nodes)};",
            f"Field[{te_threshold_id}] = Threshold;",
            f"Field[{te_threshold_id}].InField = {te_distance_id};",
            f"Field[{te_threshold_id}].SizeMin = {internal_te_active_size:.12g};",
            f"Field[{te_threshold_id}].SizeMax = {cavity_core_size:.12g};",
            f"Field[{te_threshold_id}].DistMin = 0;",
            f"Field[{te_threshold_id}].DistMax = {te_transition:.12g};",
            f"Field[{te_threshold_id}].Sigmoid = {sigmoid};",
            f"Field[{te_threshold_id}].StopAtDistMax = 1;",
            f"Field[{te_restrict_id}] = Restrict;",
            f"Field[{te_restrict_id}].InField = {te_threshold_id};",
            f"Field[{te_restrict_id}].SurfacesList = {{{int_surface}}};",
        ]
        background_fields.append(te_restrict_id)
    if background_fields:
        min_field = next_field
        lines += [
            f"Field[{min_field}] = Min;",
            f"Field[{min_field}].FieldsList = {{{', '.join(map(str, background_fields))}}};",
            f"Background Field = {min_field};",
        ]
    lines += [
        f'Physical Line("airfoil_wall_external") = {{{", ".join(map(str, ext_curves[:3]))}}};',
        f'Physical Line("airfoil_wall_internal") = {{{", ".join(map(str, int_curves[:3]))}}};',
        f'Physical Line("farfield") = {{{", ".join(map(str, far_curves))}}};',
        f'Physical Line("_ramair_inlet_interface_external") = {{{ext_curves[3]}}};',
        f'Physical Line("_ramair_inlet_interface_internal") = {{{int_curves[3]}}};',
        f'Physical Surface("fluid") = {{{ext_surface}, {int_surface}}};',
    ]
    out_geo.parent.mkdir(parents=True, exist_ok=True)
    out_geo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "surface_kind": "open_zero_thickness_base_profile_connected_fluid",
        "physical_groups": ["airfoil_wall_external", "airfoil_wall_internal", "farfield", "fluid"],
        "ram_air_inlet_is_physical_patch": False,
        "boundary_layer_requested": bool(request_bl),
        "boundary_layer_layers_requested": int(bl.get("n_layers", 0)),
        "boundary_layer_quads_requested": bool(mesh_cfg.get("open_recombine_boundary_layer", True)) if request_bl else False,
        "boundary_layer_first_cell_height_chord": float(bl.get("first_cell_height_chord", 0.0)),
        "boundary_layer_first_cell_height_m": float(bl.get("first_cell_height", 0.0)),
        "boundary_layer_total_thickness_chord": float(bl.get("total_thickness_chord", 0.0)),
        "boundary_layer_curve_ids": ext_curves if request_bl else [],
        "open_boundary_layer_curve_ids": ext_curves if request_bl else [],
        "gmsh_curve_connectivity_valid": curve_connectivity_valid,
        "gmsh_curve_connectivity_issue_count": 0,
        "gmsh_curve_connectivity_issues": [],
        "open_zero_thickness_minimum_control_segment_chord": float(
            minimum_control_segment / chord_m
        ),
        "open_zero_thickness_duplicate_control_points": duplicate_control_points,
        "open_geometry_representation": "zero_thickness_base_profile",
        "open_connected_fluid_surface": True,
        "open_internal_cavity_solver_connected": True,
        "open_boundary_layer_restricted_to_external_side": True,
        "open_internal_cavity_meshed": True,
        "open_base_profile_variant": str(mesh_cfg.get("open_base_profile_variant", "reference_uncut")),
        "open_base_profile_chord_m": float(base_manifest.get("chord_m", chord_m) or chord_m),
        "open_duplicate_inlet_interface_for_one_sided_bl": True,
        "open_selective_inlet_interface_merge": True,
        "open_inlet_interface_physical_names": [
            "_ramair_inlet_interface_external",
            "_ramair_inlet_interface_internal",
        ],
        "open_inlet_interface_nodes": inlet_nodes,
        "open_inlet_interface_tangential_size_chord": float(inlet_tangential_size / chord_m),
        "open_internal_inlet_active_size_chord": float(interface_size / chord_m),
        "open_cavity_inlet_size_strategy": inlet_size_strategy,
        "open_internal_inlet_normal_size_rule": (
            "short y1-compatible strip followed by Gmsh Extend(SizeBnd)"
            if inlet_size_strategy == "hybrid_boundary_extension"
            else (
                "Gmsh Extend(SizeBnd): local average inlet boundary-edge length; independent of y1"
            if inlet_size_strategy == "boundary_extension"
            else (
                "uniform cavity size derived from inlet tangential spacing; independent of y1"
                if inlet_size_strategy == "boundary_uniform"
                else "min(inlet_tangential_spacing, configured_y1_factor*y1)"
            )
            )
        ),
        "open_internal_inlet_boundary_size_source": (
            "actual_transfinite_inlet_boundary_edges"
            if inlet_size_strategy in {
                "hybrid_boundary_extension",
                "boundary_extension",
            }
            else (
                "derived_inlet_arc_length_over_segments"
                if inlet_size_strategy == "boundary_uniform"
                else "legacy_y1_capped_explicit_field"
            )
        ),
        "open_internal_inlet_refinement_requested": True,
        "open_internal_inlet_refinement_kind": (
            "short compatibility strip plus Gmsh Extend from cavity-side inlet boundary"
            if inlet_size_strategy == "hybrid_boundary_extension"
            else (
                "Gmsh Extend from cavity-side inlet boundary"
                if inlet_size_strategy == "boundary_extension"
                else (
                    "uniform boundary-derived cavity field"
                    if inlet_size_strategy == "boundary_uniform"
                    else "legacy staged Distance/Threshold fields"
                )
            )
        ),
        "open_internal_inlet_refinement_scope": (
            "cavity surface only; exterior field is unaffected"
        ),
        "open_cavity_inlet_extension_power": (
            inlet_extension_power
            if inlet_size_strategy in {
                "hybrid_boundary_extension",
                "boundary_extension",
            }
            else None
        ),
        "open_internal_inlet_normal_y1_factor": (
            interface_normal_y1_factor
            if inlet_size_strategy in {
                "hybrid_boundary_extension",
                "staged_explicit",
            }
            else None
        ),
        "open_internal_inlet_last_bl_height_chord": float(last_bl_height / chord_m),
        "open_zero_thickness_contour_target_nodes": target_nodes,
        "open_zero_thickness_contour_realized_segments": int(
            sum(value - 1 for value in contour_nodes.values())
        ),
        "open_zero_thickness_uniform_spacing_chord": float(contour_spacing / chord_m),
        "open_zero_thickness_realized_curve_spacing_chord": {
            key: float(value / chord_m)
            for key, value in realized_curve_spacings.items()
        },
        "open_zero_thickness_realized_spacing_ratio": float(spacing_ratio),
        "open_zero_thickness_curve_lengths_chord": {
            key: float(value / chord_m) for key, value in lengths.items()
        },
        "open_zero_thickness_curve_nodes": contour_nodes,
        "open_inlet_marker_transfinite_nodes": inlet_nodes,
        "open_inlet_marker_bump_strength": None,
        "open_wall_external_nodes": {
            "upper": upper_nodes,
            "te": te_nodes,
            "lower": lower_nodes,
        },
        "open_te_transfinite_min_nodes": requested_te_nodes,
        "open_wall_internal_nodes": {
            "upper": inner_upper_nodes,
            "te": inner_te_nodes,
            "lower": inner_lower_nodes,
        },
        "open_inner_wall_end_bump_enabled": inner_bump_enabled,
        "open_inner_wall_end_bump_strength": inner_bump if inner_bump_enabled else None,
        "open_internal_inlet_matching_transition_chord": float(
            inlet_match_transition / chord_m
        ),
        "open_internal_inlet_matching_size_factor": inlet_match_factor,
        "open_internal_inlet_matching_size_chord": float(
            inlet_matching_size / chord_m
        ),
        "open_internal_inlet_near_transition_chord": float(inlet_transition / chord_m),
        "open_internal_inlet_intermediate_size_chord": float(
            inlet_intermediate_size / chord_m
        ),
        "open_internal_inlet_dist_max_chord": float(inlet_dist_max / chord_m),
        "open_internal_te_refinement_requested": bool(
            mesh_cfg.get("open_internal_te_refinement_enabled", True)
        ),
        "open_internal_te_interface_size_chord": (
            float(internal_te_active_size / chord_m)
            if internal_te_active_size is not None
            else None
        ),
        "open_internal_te_dist_max_chord": float(
            mesh_cfg.get("open_internal_te_dist_max_chord", 0.10) or 0.10
        ),
        "open_transition_sigmoid_enabled": bool(sigmoid),
        "open_surface_size_from_boundary_layer": surface_size_bl_info,
        "openfoam_ready": False,
        "extruded_3d": False,
        **arc_info,
    }


def write_geo_open_thin_solid(
    points: pd.DataFrame,
    edges: pd.DataFrame,
    manifest: dict,
    mesh_cfg: dict,
    domain: str,
    out_geo: Path,
    variant: str,
    openfoam_3d: bool = False,
    single_surface_for_mesh_extrusion: bool = False,
) -> dict[str, Any]:
    """Write an open ram-air profile as one connected fluid region.

    The zero-thickness profile is converted to a very thin solid fabric band.
    The fluid surface is the farfield minus that band, so the exterior and the
    internal cavity remain one connected mesh through the LE opening. This is
    more suitable for OpenFOAM than the legacy diagnostic mode that meshed the
    exterior and internal cavity as separate surfaces.
    """
    chord_m = float(manifest.get("chord_m", 1.0) or 1.0)
    open_surface_size_manual = float(mesh_cfg.get("open_surface_size_general_chord", mesh_cfg.get("surface_size_general_chord", 0.003)))
    open_surface_size_chord, open_surface_size_bl_info = _derive_surface_size_from_boundary_layer(
        mesh_cfg,
        chord_m,
        open_surface_size_manual,
        enabled_key="open_surface_size_from_boundary_layer_enabled",
        factor_key="open_surface_size_bl_outer_factor",
        min_key="open_surface_size_bl_outer_min_chord",
        max_key="open_surface_size_bl_outer_max_chord",
        first_cell_key="open_first_cell_height_chord_override",
        first_cell_m_key="open_first_cell_height_m_override",
        growth_key="open_boundary_layer_growth",
        layers_key="open_boundary_layer_layers",
    )
    lc_airfoil = open_surface_size_chord * chord_m
    lc_le = float(mesh_cfg.get("open_surface_size_le_chord", mesh_cfg.get("open_surface_size_general_chord", 0.003))) * chord_m
    lc_lip = float(mesh_cfg.get("open_surface_size_lip_chord", mesh_cfg.get("open_surface_size_le_chord", 0.0012))) * chord_m
    lc_te = float(mesh_cfg.get("open_surface_size_te_chord", mesh_cfg.get("surface_size_rounded_te_chord", mesh_cfg.get("surface_size_te_chord", 0.001)))) * chord_m
    lc_farfield = float(mesh_cfg.get("open_farfield_size_chord", mesh_cfg.get("farfield_size_chord", 0.5))) * chord_m
    dpar = domain_params(domain, mesh_cfg)
    all_edges = edges[edges.start_point_id != edges.end_point_id].sort_values("edge_id").copy()
    patch_lower = all_edges["patch_name"].astype(str).str.lower()
    upper_edges = all_edges[patch_lower.str.contains("outer_upper_wall", case=False, na=False)].copy()
    lower_edges = all_edges[patch_lower.str.contains("outer_lower_wall", case=False, na=False)].copy()
    te_edges = all_edges[patch_lower.str.contains("trailing_edge_wall", case=False, na=False)].copy()
    inlet_marker_edges = all_edges[patch_lower.str.contains("inlet_opening_marker", case=False, na=False)].copy()
    pidx = points.set_index("point_id")

    def edge_point_sequence(edge_df: pd.DataFrame) -> list[int]:
        if edge_df.empty:
            return []
        ordered = edge_df.sort_values("edge_id").reset_index(drop=True)
        seq = [int(ordered.iloc[0].start_point_id)]
        seq.extend(int(v) for v in ordered["end_point_id"].tolist())
        return seq

    def point_xy(pid: int) -> np.ndarray:
        return pidx.loc[int(pid), ["x_m", "z_m"]].to_numpy(float)

    def orient_sequence(seq: list[int], start_pid: int, end_pid: int) -> list[int]:
        if not seq:
            return []
        if seq[0] == start_pid and seq[-1] == end_pid:
            return seq
        if seq[0] == end_pid and seq[-1] == start_pid:
            return list(reversed(seq))
        return seq

    upper_seq = edge_point_sequence(upper_edges)
    lower_seq = edge_point_sequence(lower_edges)
    if upper_seq and float(point_xy(upper_seq[0])[0]) < float(point_xy(upper_seq[-1])[0]):
        upper_seq.reverse()
    if lower_seq and float(point_xy(lower_seq[0])[0]) > float(point_xy(lower_seq[-1])[0]):
        lower_seq.reverse()
    te_seq = orient_sequence(edge_point_sequence(te_edges), lower_seq[-1] if lower_seq else -1, upper_seq[0] if upper_seq else -1)
    if len(upper_seq) < 3 or len(lower_seq) < 3 or len(te_seq) < 2:
        raise RuntimeError("Open thin-solid mesh requires ordered upper, lower and TE wall point sequences.")
    if te_seq[0] != lower_seq[-1] or te_seq[-1] != upper_seq[0]:
        raise RuntimeError("Open thin-solid TE sequence does not connect lower TE to upper TE consecutively.")

    # Midline order: upper lip -> upper TE -> rounded TE -> lower TE -> lower lip.
    midline_ids = list(reversed(upper_seq))
    midline_ids.extend(list(reversed(te_seq))[1:])
    midline_ids.extend(list(reversed(lower_seq))[1:])
    deduped: list[int] = []
    for pid in midline_ids:
        if not deduped or deduped[-1] != int(pid):
            deduped.append(int(pid))
    midline_ids = deduped
    mid = np.asarray([point_xy(pid) for pid in midline_ids], dtype=float)
    if len(mid) < 6:
        raise RuntimeError("Open thin-solid midline is too short for a stable fabric band.")

    requested_thickness_chord = max(
        float(mesh_cfg.get("fabric_thickness_chord", 1.0e-5) or 0.0),
        float(mesh_cfg.get("open_minimum_fabric_thickness_chord", 1.0e-5) or 0.0),
    )
    thickness_chord = requested_thickness_chord
    thickness = max(thickness_chord * chord_m, 1.0e-7 * chord_m)

    tangents = np.zeros_like(mid)
    for i in range(len(mid)):
        if i == 0:
            t = mid[1] - mid[0]
        elif i == len(mid) - 1:
            t = mid[-1] - mid[-2]
        else:
            t = mid[i + 1] - mid[i - 1]
        norm = float(np.linalg.norm(t))
        if norm <= 1.0e-14:
            t = np.asarray([1.0, 0.0])
            norm = 1.0
        tangents[i] = t / norm
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    upper_probe = max(1, min(len(upper_seq) // 2, len(mid) - 2))
    lower_start = len(upper_seq) + len(te_seq) - 2
    lower_probe = max(upper_probe + 1, min(lower_start + len(lower_seq) // 2, len(mid) - 2))
    upper_outward = float(normals[upper_probe, 1]) > 0.0
    lower_outward = float(normals[lower_probe, 1]) < 0.0
    if not upper_outward and not lower_outward:
        normals *= -1.0
        upper_outward = float(normals[upper_probe, 1]) > 0.0
        lower_outward = float(normals[lower_probe, 1]) < 0.0
    if not (upper_outward and lower_outward):
        raise RuntimeError(
            "Open thin-solid exterior normal validation failed: upper and lower wall normals are inconsistent."
        )
    # Point-normal offsets can overlap at a tight rounded TE even when the
    # centreline itself is valid. Reduce only the artificial fabric thickness
    # until both open offsets are simple and mutually disjoint; the requested
    # and effective values are reported separately.
    offset_reductions = 0
    offset_self_intersections = 0
    offset_cross_intersections = 0
    for _ in range(8):
        half = 0.5 * thickness
        plus = mid + half * normals
        minus = mid - half * normals
        offset_self_intersections = (
            count_open_polyline_self_intersections(plus)
            + count_open_polyline_self_intersections(minus)
        )
        offset_cross_intersections = count_polyline_cross_intersections(plus, minus)
        if offset_self_intersections == 0 and offset_cross_intersections == 0:
            break
        thickness *= 0.5
        thickness_chord = thickness / chord_m
        offset_reductions += 1
    else:
        raise RuntimeError(
            "Open thin-solid offset remains self-intersecting after adaptive thickness reduction: "
            f"self={offset_self_intersections}, cross={offset_cross_intersections}."
        )

    request_bl = (
        bool(mesh_cfg.get("request_boundary_layer", True))
        and bool(mesh_cfg.get("open_diagnostic_boundary_layer_enabled", True))
        and int(mesh_cfg.get("open_boundary_layer_layers", mesh_cfg.get("boundary_layer_layers", 0)) or 0) > 0
    )
    if request_bl:
        bl = boundary_layer_parameters(
            mesh_cfg,
            chord_m,
            first_cell_key="open_first_cell_height_chord_override",
            first_cell_m_key="open_first_cell_height_m_override",
            growth_key="open_boundary_layer_growth",
            layers_key="open_boundary_layer_layers",
            thickness_key="open_boundary_layer_total_thickness_chord_override",
            fallback_first_cell_chord=1.0e-5,
        )
    else:
        bl = {}
    inlet_element_mode = str(mesh_cfg.get("open_inlet_transition_elements", "graded_quads"))
    inlet_transition_distribution: dict[str, float | int | str] | None = None
    if inlet_element_mode in {"graded_quads", "graded_triangles"}:
        inlet_transition_distribution = graded_inlet_transition_parameters(
            thickness,
            float(bl.get("first_cell_height", thickness)),
            float(mesh_cfg.get("open_inlet_transition_growth", 1.22) or 1.22),
            int(mesh_cfg.get("open_inlet_connector_normal_nodes", 0) or 0),
        )

    point_base = 300000
    plus_ids = [point_base + i for i in range(len(mid))]
    minus_ids = [point_base + 10000 + i for i in range(len(mid))]

    lip_cap_rounding_enabled = bool(mesh_cfg.get("open_lip_cap_rounding_enabled", False))
    lip_cap_rounding_points = max(5, int(mesh_cfg.get("open_lip_cap_rounding_points", 7) or 7))
    if lip_cap_rounding_points % 2 == 0:
        lip_cap_rounding_points += 1

    def rounded_lip_cap(
        center: np.ndarray,
        start: np.ndarray,
        end: np.ndarray,
        body_direction: np.ndarray,
    ) -> np.ndarray:
        """Return an ordered upstream semicircle between the two fabric faces."""
        body = np.asarray(body_direction, dtype=float)
        body_norm = float(np.linalg.norm(body))
        if body_norm <= 1.0e-14:
            raise RuntimeError("Cannot round an open-profile lip with a zero endpoint tangent.")
        body /= body_norm
        radial = np.asarray(start, dtype=float) - np.asarray(center, dtype=float)
        radius = float(np.linalg.norm(radial))
        if radius <= 1.0e-14:
            raise RuntimeError("Cannot round a zero-thickness open-profile lip.")
        upstream = -body * radius
        theta = np.linspace(0.0, math.pi, lip_cap_rounding_points)
        samples = (
            np.asarray(center, dtype=float)
            + np.cos(theta)[:, None] * radial
            + np.sin(theta)[:, None] * upstream
        )
        samples[0] = start
        samples[-1] = end
        return samples

    if lip_cap_rounding_enabled:
        upper_cap_samples = rounded_lip_cap(mid[0], minus[0], plus[0], mid[1] - mid[0])
        lower_cap_samples = rounded_lip_cap(mid[-1], plus[-1], minus[-1], mid[-2] - mid[-1])
    else:
        upper_cap_samples = np.vstack([minus[0], plus[0]])
        lower_cap_samples = np.vstack([plus[-1], minus[-1]])
    upper_cap_extra_ids = [point_base + 20000 + i for i in range(max(0, len(upper_cap_samples) - 2))]
    lower_cap_extra_ids = [point_base + 21000 + i for i in range(max(0, len(lower_cap_samples) - 2))]
    upper_cap_ids = [minus_ids[0], *upper_cap_extra_ids, plus_ids[0]]
    lower_cap_ids = [plus_ids[-1], *lower_cap_extra_ids, minus_ids[-1]]

    inlet_bridge_smoothing_enabled = bool(mesh_cfg.get("open_inlet_bridge_smoothing_enabled", False))
    inlet_bridge_handle_fraction = min(
        0.25,
        max(0.005, float(mesh_cfg.get("open_inlet_bridge_smoothing_handle_fraction", 0.080) or 0.080)),
    )

    def unit_direction(vector: np.ndarray, label: str) -> np.ndarray:
        length = float(np.linalg.norm(vector))
        if length <= 1.0e-14:
            raise RuntimeError(f"Cannot smooth inlet bridge: zero {label} tangent.")
        return np.asarray(vector, dtype=float) / length

    def tangent_bridge_controls(
        start: np.ndarray,
        end: np.ndarray,
        start_tangent: np.ndarray,
        end_tangent: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        gap = float(np.linalg.norm(np.asarray(end) - np.asarray(start)))
        handle = max(1.0e-12 * chord_m, inlet_bridge_handle_fraction * gap)
        return (
            np.asarray(start, dtype=float) + handle * unit_direction(start_tangent, "start"),
            np.asarray(end, dtype=float) - handle * unit_direction(end_tangent, "end"),
        )

    if inlet_bridge_smoothing_enabled:
        # Exterior loop orientation is lower lip -> upper lip -> upper wall.
        outer_bridge_controls = tangent_bridge_controls(
            plus[-1],
            plus[0],
            plus[-1] - plus[-2],
            plus[1] - plus[0],
        )
    else:
        outer_bridge_controls = ()
    outer_bridge_control_ids = [point_base + 22000, point_base + 22001]

    x_le_open = float(points["x_m"].min())
    x_te_open = float(points["x_m"].max())
    le_size_window = max(float(mesh_cfg.get("open_le_refinement_width_chord", 0.18)) * chord_m, 1.0e-12)
    lip_size_window = max(float(mesh_cfg.get("open_lip_refinement_x_chord", 0.06)) * chord_m, 1.0e-12)
    te_size_window = max(float(mesh_cfg.get("open_te_refinement_width_chord", 0.12)) * chord_m, 1.0e-12)
    explicit_te_midline_ids = {int(pid) for pid in te_seq}

    def lc_for_midpoint(i: int) -> str:
        x_val = float(mid[i, 0])
        pid = midline_ids[i]
        role = str(pidx.loc[pid].get("boundary_role", "")).lower() if pid in pidx.index else ""
        # Prefer the explicit rounded-cap membership exported by the geometry
        # contract. The chordwise window is only a fallback for legacy inputs
        # without a separate consecutive TE sequence.
        if pid in explicit_te_midline_ids or (not explicit_te_midline_ids and x_val >= x_te_open - te_size_window):
            return "lc_te"
        if "lip" in role or x_val <= x_le_open + lip_size_window:
            return "lc_lip"
        if x_val <= x_le_open + le_size_window:
            return "lc_le"
        return "lc_airfoil"

    plus_curve = 3001
    lower_lip_cap_curve = 3002
    inner_wall_curve_base = 4000
    upper_lip_cap_curve = 3004
    outer_inlet_bridge_curve = 3005
    inner_inlet_bridge_curve = 3006
    transition_outer_inlet_bridge_curve = 3013
    upper_lip_tip_curve = 3007
    lower_lip_tip_curve = 3008
    upper_bl_terminal_curve = 3009
    lower_bl_terminal_curve = 3010
    outer_te_curve = 3011
    outer_lower_curve = 3012
    split_curvature_sections = False
    upper_end_idx = -1
    te_end_idx = -1
    trim_end_points = 0
    if bool(mesh_cfg.get("open_boundary_layer_trim_end_segments", True)):
        trim_end_points = max(1, int(mesh_cfg.get("open_boundary_layer_trim_end_points", 4) or 4))
    trim_end_points = min(trim_end_points, max(0, (len(plus_ids) - 5) // 2))
    if trim_end_points:
        upper_tip_ids = plus_ids[: trim_end_points + 1]
        main_plus_ids = plus_ids[trim_end_points : len(plus_ids) - trim_end_points]
        lower_tip_ids = plus_ids[len(plus_ids) - trim_end_points - 1 :]
        if len(main_plus_ids) < 5:
            raise RuntimeError("Open BL trimming left too few exterior points.")
        upper_tip_curve_defs = [
            (3100 + i, [upper_tip_ids[i], upper_tip_ids[i + 1]], "Line")
            for i in range(len(upper_tip_ids) - 1)
        ]
        lower_tip_curve_defs = [
            (3200 + i, [lower_tip_ids[i], lower_tip_ids[i + 1]], "Line")
            for i in range(len(lower_tip_ids) - 1)
        ]
        outer_wall_curve_defs = (
            upper_tip_curve_defs
            + [(upper_bl_terminal_curve, main_plus_ids[:2], "Line")]
            + [(plus_curve, main_plus_ids[1:-1], "BSpline")]
            + [(lower_bl_terminal_curve, main_plus_ids[-2:], "Line")]
            + lower_tip_curve_defs
        )
        outer_wall_curve_ids = [cid for cid, _, _ in outer_wall_curve_defs]
        exterior_bl_curve_ids = [upper_bl_terminal_curve, plus_curve, lower_bl_terminal_curve]
        bl_end_point_ids = [main_plus_ids[0], main_plus_ids[-1]]
    else:
        upper_tip_ids = []
        main_plus_ids = plus_ids
        lower_tip_ids = []
        split_curvature_sections = bool(mesh_cfg.get("open_boundary_layer_split_curvature_sections", True))
        upper_end_idx = len(upper_seq) - 1
        te_end_idx = upper_end_idx + len(te_seq) - 1
        if split_curvature_sections and 1 < upper_end_idx < te_end_idx < len(plus_ids) - 2:
            split_curve_kind = str(mesh_cfg.get("open_split_wall_curve_kind", "BSpline")).strip()
            if split_curve_kind not in {"Spline", "BSpline"}:
                raise ValueError("open_split_wall_curve_kind must be Spline or BSpline.")
            outer_wall_curve_defs = [
                (plus_curve, plus_ids[: upper_end_idx + 1], split_curve_kind),
                (outer_te_curve, plus_ids[upper_end_idx : te_end_idx + 1], split_curve_kind),
                (outer_lower_curve, plus_ids[te_end_idx:], split_curve_kind),
            ]
        else:
            outer_curve_kind = str(mesh_cfg.get("open_outer_wall_curve_kind", "Spline")).strip()
            if outer_curve_kind not in {"Spline", "BSpline"}:
                raise ValueError("open_outer_wall_curve_kind must be Spline or BSpline.")
            outer_wall_curve_defs = [(plus_curve, main_plus_ids, outer_curve_kind)]
        outer_wall_curve_ids = [cid for cid, _, _ in outer_wall_curve_defs]
        exterior_bl_curve_ids = list(outer_wall_curve_ids)
        bl_end_point_ids = [plus_ids[0], plus_ids[-1]]

    # Use the same three geometric sections on the inner fabric face. Hundreds
    # of independent inner Line entities previously intersected the globally
    # interpolated exterior Spline around the TE and made Gmsh repeatedly split
    # 1D edges. Three local Splines preserve the shape without global overshoot.
    if split_curvature_sections and 1 < upper_end_idx < te_end_idx < len(minus_ids) - 2:
        inner_wall_curve_defs = [
            (inner_wall_curve_base, list(reversed(minus_ids[te_end_idx:])), "Spline"),
            (inner_wall_curve_base + 1, list(reversed(minus_ids[upper_end_idx : te_end_idx + 1])), "Spline"),
            (inner_wall_curve_base + 2, list(reversed(minus_ids[: upper_end_idx + 1])), "Spline"),
        ]
    else:
        inner_wall_curve_defs = [
            (inner_wall_curve_base, list(reversed(minus_ids)), "Spline"),
        ]
    inner_wall_curve_ids = [cid for cid, _, _ in inner_wall_curve_defs]
    wall_curve_ids = outer_wall_curve_ids + [lower_lip_cap_curve] + inner_wall_curve_ids + [upper_lip_cap_curve]
    include_inlet_bridge_in_bl = bool(mesh_cfg.get("open_boundary_layer_include_inlet_bridge", True))
    if include_inlet_bridge_in_bl:
        exterior_bl_curve_ids.append(outer_inlet_bridge_curve)
    inlet_size_marker_curve_ids: list[int] = []
    open_inlet_refinement_bridge_requested = bool(mesh_cfg.get("open_inlet_refinement_bridge_enabled", True))
    exterior_size_curve_ids = list(outer_wall_curve_ids)
    if include_inlet_bridge_in_bl or not single_surface_for_mesh_extrusion or open_inlet_refinement_bridge_requested:
        exterior_size_curve_ids.append(outer_inlet_bridge_curve)
    fabric_hole_entries = list(wall_curve_ids)
    natural_hole_points = [tuple(v) for v in plus] + [tuple(v) for v in minus[::-1]]
    if polygon_area_xy(natural_hole_points) > 0:
        fabric_hole_entries = [-entry for entry in reversed(fabric_hole_entries)]

    open_target_nodes_for_interface = max(24, int(mesh_cfg.get("open_surface_target_nodes", 220) or 220))
    open_wall_length = float(np.linalg.norm(np.diff(plus, axis=0), axis=1).sum())
    open_tangential_interface_size = open_wall_length / max(open_target_nodes_for_interface - len(outer_wall_curve_ids), 1)
    lc_airfoil = max(lc_airfoil, 0.85 * open_tangential_interface_size)
    open_surface_size_bl_info["tangential_wall_spacing_chord"] = float(open_tangential_interface_size / chord_m)
    open_surface_size_bl_info["active_surface_size_chord"] = float(lc_airfoil / chord_m)
    open_surface_size_bl_info["interface_size_rule"] = "max(last_BL_layer_scale, 0.85*mean_tangential_wall_spacing)"

    lines = [
        "// Open ram-air thin-solid fluid mesh. The inlet opening is fluid, not a physical patch.",
        f"// variant: {variant}",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {int(mesh_cfg.get('gmsh_mesh_algorithm_2d', 5))}; // 2D algorithm: 5=Delaunay, 6=Frontal-Delaunay",
        f"Mesh.RandomFactor = {float(mesh_cfg.get('gmsh_random_factor', 1.0e-7)):.12g};",
        f"Mesh.RandomSeed = {int(mesh_cfg.get('gmsh_random_seed', 1))};",
        "Mesh.Smoothing = 8;",
        "Mesh.Optimize = 1;",
        "Mesh.OptimizeNetgen = 1;",
        "// Explicit fields control growth; do not propagate the very small",
        "// lip/BL sizes through the complete external and cavity surfaces.",
        "Mesh.MeshSizeFromPoints = 0;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 0;",
        f"lc_airfoil={lc_airfoil:.12g};",
        f"lc_le={lc_le:.12g};",
        f"lc_lip={lc_lip:.12g};",
        f"lc_te={lc_te:.12g};",
        f"lc_farfield={lc_farfield:.12g};",
        f"// effective_fabric_thickness = {thickness:.12g} m ({thickness_chord:.12g} chord)",
    ]
    for i, pid in enumerate(plus_ids):
        lc_name = lc_for_midpoint(i)
        lines.append(f"Point({pid}) = {{{plus[i,0]:.12g}, {plus[i,1]:.12g}, 0, {lc_name}}};")
    for i, pid in enumerate(minus_ids):
        lc_name = lc_for_midpoint(i)
        lines.append(f"Point({pid}) = {{{minus[i,0]:.12g}, {minus[i,1]:.12g}, 0, {lc_name}}};")
    for pid, xy in zip(upper_cap_extra_ids, upper_cap_samples[1:-1]):
        lines.append(f"Point({pid}) = {{{xy[0]:.12g}, {xy[1]:.12g}, 0, lc_lip}}; // rounded upper lip cap")
    for pid, xy in zip(lower_cap_extra_ids, lower_cap_samples[1:-1]):
        lines.append(f"Point({pid}) = {{{xy[0]:.12g}, {xy[1]:.12g}, 0, lc_lip}}; // rounded lower lip cap")
    if inlet_bridge_smoothing_enabled:
        for pid, xy in zip(outer_bridge_control_ids, outer_bridge_controls):
            lines.append(
                f"Point({pid}) = {{{xy[0]:.12g}, {xy[1]:.12g}, 0, lc_lip}}; "
                "// tangent exterior inlet bridge control"
            )
    lines += [
    ]
    for cid, ids, kind in outer_wall_curve_defs:
        role = "carrying BL" if cid in exterior_bl_curve_ids else "without BL extrusion"
        lines.append(f"{kind}({cid}) = {{{', '.join(map(str, ids))}}}; // exterior fabric wall {role}")
    lip_cap_curve_kind = "BSpline" if lip_cap_rounding_enabled else "Line"
    lines.append(f"{lip_cap_curve_kind}({lower_lip_cap_curve}) = {{{', '.join(map(str, lower_cap_ids))}}}; // lower lip solid thickness cap, not inlet")
    for cid, ids, kind in inner_wall_curve_defs:
        lines.append(f"{kind}({cid}) = {{{', '.join(map(str, ids))}}}; // continuous thin_fabric_inner_side_no_bl")
    lines.append(
        f"{lip_cap_curve_kind}({upper_lip_cap_curve}) = {{{', '.join(map(str, upper_cap_ids))}}}; // upper lip solid thickness cap, not inlet"
    )
    if inlet_transition_distribution is not None:
        lines += [
            "// Grade the inlet throat from the exterior y1 scale towards the cavity;",
            "// reversing the upper cap gives the same outer-to-inner orientation on both lips.",
            f"Transfinite Curve {{{lower_lip_cap_curve}, -{upper_lip_cap_curve}}} = "
            f"{int(inlet_transition_distribution['normal_nodes'])} Using Progression "
            f"{float(inlet_transition_distribution['progression']):.12g};",
        ]
    else:
        connector_nodes = max(2, int(mesh_cfg.get("open_inlet_connector_normal_nodes", 2) or 2))
        lines.append(
            f"Transfinite Curve {{{lower_lip_cap_curve}, {upper_lip_cap_curve}}} = "
            f"{connector_nodes} Using Progression 1;"
        )
    inlet_connector_curve_ids: list[int] = []
    outer_bridge_ids = [plus_ids[-1], *outer_bridge_control_ids, plus_ids[0]]
    inner_bridge_ids = [minus_ids[0], minus_ids[-1]]
    outer_bridge_curve_kind = "Bezier" if inlet_bridge_smoothing_enabled else "Line"
    inner_bridge_curve_kind = "Line"
    if not inlet_bridge_smoothing_enabled:
        outer_bridge_ids = [plus_ids[-1], plus_ids[0]]
    if single_surface_for_mesh_extrusion and include_inlet_bridge_in_bl:
        lines.append(
            f"{outer_bridge_curve_kind}({outer_inlet_bridge_curve}) = "
            f"{{{', '.join(map(str, outer_bridge_ids))}}}; "
            "// experimental embedded nonphysical BL continuation across inlet"
        )
        inlet_connector_curve_ids.append(outer_inlet_bridge_curve)
    elif single_surface_for_mesh_extrusion and open_inlet_refinement_bridge_requested:
        lines.append(
            f"{outer_bridge_curve_kind}({outer_inlet_bridge_curve}) = "
            f"{{{', '.join(map(str, outer_bridge_ids))}}}; "
            "// embedded nonphysical inlet sizing bridge only; not a wall, not a physical patch, no BL"
        )
        inlet_size_marker_curve_ids.append(outer_inlet_bridge_curve)
    elif not single_surface_for_mesh_extrusion:
        lines += [
            f"{outer_bridge_curve_kind}({outer_inlet_bridge_curve}) = "
            f"{{{', '.join(map(str, outer_bridge_ids))}}}; "
            "// external-side nonphysical inlet interface",
            f"{inner_bridge_curve_kind}({inner_inlet_bridge_curve}) = "
            f"{{{', '.join(map(str, inner_bridge_ids))}}}; "
            "// conformal cavity-side inlet interface",
            f"{outer_bridge_curve_kind}({transition_outer_inlet_bridge_curve}) = "
            f"{{{', '.join(map(str, outer_bridge_ids))}}}; "
            "// coincident transition-side inlet interface, no BL",
        ]
        inlet_connector_curve_ids.extend(
            [outer_inlet_bridge_curve, inner_inlet_bridge_curve, transition_outer_inlet_bridge_curve]
        )
    if inlet_connector_curve_ids or inlet_size_marker_curve_ids:
        inlet_transfinite_curves = inlet_connector_curve_ids + inlet_size_marker_curve_ids
        lines.append(
            f"Transfinite Curve {{{', '.join(map(str, inlet_transfinite_curves))}}} = "
            f"{max(20, int(mesh_cfg.get('open_inlet_marker_transfinite_nodes', 120) or 120))} Using Bump "
            f"{max(1.0e-6, float(mesh_cfg.get('open_inlet_marker_bump_strength', 0.60) or 0.60)):.12g};"
        )
    synthetic_xy: dict[int, np.ndarray] = {}
    synthetic_xy.update({pid: plus[i] for i, pid in enumerate(plus_ids)})
    synthetic_xy.update({pid: minus[i] for i, pid in enumerate(minus_ids)})
    synthetic_xy.update({pid: upper_cap_samples[i + 1] for i, pid in enumerate(upper_cap_extra_ids)})
    synthetic_xy.update({pid: lower_cap_samples[i + 1] for i, pid in enumerate(lower_cap_extra_ids)})
    if inlet_bridge_smoothing_enabled:
        synthetic_xy.update({pid: xy for pid, xy in zip(outer_bridge_control_ids, outer_bridge_controls)})

    def synthetic_curve_length(ids: list[int]) -> float:
        length = 0.0
        for a_id, b_id in zip(ids, ids[1:]):
            if int(a_id) in synthetic_xy and int(b_id) in synthetic_xy:
                length += float(np.linalg.norm(synthetic_xy[int(b_id)] - synthetic_xy[int(a_id)]))
        return length

    outer_curve_lengths = {cid: synthetic_curve_length(ids) for cid, ids, _ in outer_wall_curve_defs}
    total_outer_curve_length = max(sum(outer_curve_lengths.values()), 1.0e-15)
    te_curve_length = max(outer_curve_lengths.get(outer_te_curve, 0.0), 1.0e-15)
    non_te_curve_length = max(total_outer_curve_length - te_curve_length, 1.0e-15)
    target_surface_nodes = max(24, int(mesh_cfg.get("open_surface_target_nodes", 220) or 220))
    target_te_nodes = max(3, int(mesh_cfg.get("open_te_transfinite_min_nodes", 56) or 56))
    target_non_te_nodes = max(12, target_surface_nodes - target_te_nodes)
    progression = max(1.0, float(mesh_cfg.get("open_surface_transfinite_progression", 1.0) or 1.0))
    outer_end_bump_enabled = bool(mesh_cfg.get("open_wall_end_bump_enabled", True))
    outer_end_bump = max(1.0e-6, float(mesh_cfg.get("open_wall_end_bump_strength", 0.72) or 0.72))
    outer_wall_transfinite_nodes: dict[int, int] = {}
    outer_wall_transfinite_distributions: dict[int, dict[str, float | str]] = {}
    for cid, ids, _ in outer_wall_curve_defs:
        minimum_nodes = 3
        if cid == outer_te_curve:
            minimum_nodes = max(3, int(mesh_cfg.get("open_te_transfinite_min_nodes", 28) or 28))
            requested_nodes = _target_nodes_for_curve(
                outer_curve_lengths.get(cid, 0.0),
                target_te_nodes,
                te_curve_length,
                min_nodes=minimum_nodes,
                existing_points=len(ids),
            )
        else:
            minimum_nodes = max(minimum_nodes, int(mesh_cfg.get("open_lip_transfinite_min_nodes", 24) or 24))
            requested_nodes = _target_nodes_for_curve(
                outer_curve_lengths.get(cid, 0.0),
                target_non_te_nodes,
                non_te_curve_length,
                min_nodes=minimum_nodes,
                existing_points=len(ids),
            )
        tangential_multiplier = max(1.0, float(mesh_cfg.get("open_surface_transfinite_multiplier", 1.0) or 1.0))
        requested_nodes = max(minimum_nodes, int(math.ceil(requested_nodes * tangential_multiplier)))
        outer_wall_transfinite_nodes[cid] = int(requested_nodes)
        if cid != outer_te_curve and outer_end_bump_enabled:
            lines.append(f"Transfinite Curve {{{cid}}} = {requested_nodes} Using Bump {outer_end_bump:.12g};")
            outer_wall_transfinite_distributions[cid] = {"method": "Bump", "value": outer_end_bump}
        else:
            lines.append(f"Transfinite Curve {{{cid}}} = {requested_nodes} Using Progression {progression:.12g};")
            outer_wall_transfinite_distributions[cid] = {"method": "Progression", "value": progression}

    inner_curve_lengths = {cid: synthetic_curve_length(ids) for cid, ids, _ in inner_wall_curve_defs}
    inner_wall_transfinite_nodes: dict[int, int] = {}
    inner_wall_transfinite_distributions: dict[int, dict[str, float | str]] = {}
    inner_end_bump_enabled = bool(mesh_cfg.get("open_inner_wall_end_bump_enabled", True))
    inner_end_bump = max(1.0e-6, float(mesh_cfg.get("open_inner_wall_end_bump_strength", 0.86) or 0.86))
    reversed_outer_counts = [outer_wall_transfinite_nodes[cid] for cid, _, _ in reversed(outer_wall_curve_defs)]
    for index, (cid, ids, _) in enumerate(inner_wall_curve_defs):
        # The two fabric faces are different fluid boundaries and do not need
        # matching tangential node counts. Keep a stronger geometric minimum on
        # the tight inner TE, while allowing the long inner upper/lower walls to
        # use fewer nodes. This reduces cavity cost without putting BL cells on
        # the inner face or changing the finite-thickness solid band.
        outer_nodes = int(reversed_outer_counts[min(index, len(reversed_outer_counts) - 1)])
        is_inner_te = bool(split_curvature_sections and cid == inner_wall_curve_base + 1)
        if is_inner_te:
            factor = min(1.0, max(0.05, float(mesh_cfg.get("open_inner_te_node_factor", 0.10) or 0.10)))
            minimum_nodes = max(8, int(mesh_cfg.get("open_inner_te_min_nodes", 40) or 40))
        else:
            factor = min(1.0, max(0.20, float(mesh_cfg.get("open_inner_wall_node_factor", 0.45) or 0.45)))
            minimum_nodes = max(8, int(mesh_cfg.get("open_inner_wall_min_nodes", 24) or 24))
        requested_nodes = min(outer_nodes, max(minimum_nodes, int(math.ceil(outer_nodes * factor))))
        inner_wall_transfinite_nodes[cid] = requested_nodes
        if not is_inner_te and inner_end_bump_enabled:
            lines.append(f"Transfinite Curve {{{cid}}} = {requested_nodes} Using Bump {inner_end_bump:.12g};")
            inner_wall_transfinite_distributions[cid] = {"method": "Bump", "value": inner_end_bump}
        else:
            lines.append(f"Transfinite Curve {{{cid}}} = {requested_nodes} Using Progression 1;")
            inner_wall_transfinite_distributions[cid] = {"method": "Progression", "value": 1.0}
    inner_te_curve_ids = [
        cid for cid, _, _ in inner_wall_curve_defs
        if split_curvature_sections and cid == inner_wall_curve_base + 1
    ]

    base = 200000
    far_lines, far_curves = farfield_geometry_lines(base, domain, mesh_cfg, chord_m)
    lines += far_lines

    far_loop = base + 20
    outer_profile_loop = base + 21
    cavity_loop = base + 22
    inlet_transition_loop = base + 23
    fabric_loop = base + 24
    external_surface = base + 30
    cavity_surface = base + 31
    inlet_transition_surface = base + 32
    single_fluid_surface = base + 33
    lines.append(f"Line Loop({far_loop}) = {{{', '.join(map(str, far_curves))}}};")
    if single_surface_for_mesh_extrusion:
        lines += [
            f"Line Loop({fabric_loop}) = {{{', '.join(map(str, fabric_hole_entries))}}};",
            f"Plane Surface({single_fluid_surface}) = {{{far_loop}, {fabric_loop}}};",
        ]
        if inlet_size_marker_curve_ids:
            lines.append(
                f"Line{{{', '.join(map(str, inlet_size_marker_curve_ids))}}} In Surface{{{single_fluid_surface}}}; "
                "// embedded inlet sizing marker; fluid remains connected across this curve"
            )
        if include_inlet_bridge_in_bl:
            lines.append(
                f"Line{{{outer_inlet_bridge_curve}}} In Surface{{{single_fluid_surface}}}; "
                "// experimental embedded nonphysical BL continuation across inlet"
            )
        fluid_surfaces = [single_fluid_surface]
        topology_mode = "single_connected_2d_surface_for_connectivity_preserving_extrusion"
    else:
        lines += [
            f"Line Loop({outer_profile_loop}) = {{{', '.join(map(str, outer_wall_curve_ids + [outer_inlet_bridge_curve]))}}};",
            f"Plane Surface({external_surface}) = {{{far_loop}, {outer_profile_loop}}};",
            f"Line Loop({cavity_loop}) = {{{', '.join(map(str, inner_wall_curve_ids + [inner_inlet_bridge_curve]))}}};",
            f"Plane Surface({cavity_surface}) = {{{cavity_loop}}};",
            f"Line Loop({inlet_transition_loop}) = {{{transition_outer_inlet_bridge_curve}, -{upper_lip_cap_curve}, {inner_inlet_bridge_curve}, -{lower_lip_cap_curve}}};",
            f"Plane Surface({inlet_transition_surface}) = {{{inlet_transition_loop}}};",
        ]
        if inlet_element_mode in {"graded_quads", "graded_triangles", "recombined_quads", "transfinite_triangles"}:
            arrangement = " Alternate" if inlet_element_mode == "graded_triangles" else ""
            lines.append(
                f"Transfinite Surface {{{inlet_transition_surface}}} = "
                f"{{{plus_ids[-1]}, {plus_ids[0]}, {minus_ids[0]}, {minus_ids[-1]}}}{arrangement};"
            )
            if inlet_element_mode in {"graded_quads", "recombined_quads"}:
                lines.append(f"Recombine Surface {{{inlet_transition_surface}}};")
                if inlet_element_mode == "graded_quads":
                    lines.append(
                        "// Short orthogonal graded block: y1-scale at the external BL interface, "
                        "smooth growth into the triangular cavity mesh."
                    )
            elif inlet_element_mode == "graded_triangles":
                lines.append(
                    "// Alternating diagonals avoid a one-sided strip bias. Normal spacing starts at exterior y1."
                )
            else:
                lines.append(
                    f"// Surface {inlet_transition_surface} keeps the coherent transfinite diagonals as triangles."
                )
        else:
            lines.append(
                f"// Surface {inlet_transition_surface} uses genuinely unstructured triangles; "
                "no Transfinite Surface constraint is applied."
            )
        fluid_surfaces = [external_surface, cavity_surface, inlet_transition_surface]
        topology_mode = "partitioned_2d_external_inlet_cavity_surfaces"

    if request_bl:
        y1 = float(bl["first_cell_height"])
        growth = float(bl["growth"])
        n_layers = int(bl["n_layers"])
        thickness_bl = float(bl["total_thickness"])
        bl_quads = 1 if bool(mesh_cfg.get("open_recombine_boundary_layer", mesh_cfg.get("recombine_boundary_layer", False))) else 0
        lines += [
            "",
            "// Boundary layer only on the aerodynamic exterior. Inner fabric",
            "// curves and finite-thickness lip caps are deliberately excluded.",
            "// In the supported single-surface mode every BL wall curve bounds",
            "// exactly one fluid surface, avoiding two-sided extrusion.",
            "Field[1] = BoundaryLayer;",
            f"Field[1].CurvesList = {{{', '.join(map(str, exterior_bl_curve_ids))}}};",
            f"Field[1].Size = {y1:.12g};",
            f"Field[1].Ratio = {growth:.12g};",
            f"Field[1].Thickness = {thickness_bl:.12g};",
            f"Field[1].Quads = {bl_quads};",
            f"Field[1].AnisoMax = {float(mesh_cfg.get('open_boundary_layer_aniso_max_deg', 170.0)):.12g};",
        ]
        # A Gmsh fan needs two BL-carrying curves meeting at a corner. In the
        # solver-ready open topology the exterior BL ends before the inlet and
        # the cavity begins with triangles, so a fan at that one-edge endpoint
        # is invalid ("Impossible BL Configuration -- One Edge").
        fan_at_lips_requested = bool(mesh_cfg.get("open_boundary_layer_fan_at_lips", False))
        fan_at_lips = fan_at_lips_requested and include_inlet_bridge_in_bl
        if bl_end_point_ids and not include_inlet_bridge_in_bl and not fan_at_lips:
            lines.append(
                f"Field[1].PointsList = {{{bl_end_point_ids[0]}, {bl_end_point_ids[-1]}}};"
                " // BL endings bracketed by straight line entities"
            )
        if fan_at_lips:
            # The BL can be trimmed before the geometric lip. Fan the actual
            # BL-curve endpoints; fanning plus_ids[0/-1] has no effect when
            # those points are not part of CurvesList. FanPointsList and
            # PointsList are mutually exclusive at a lip: one creates a fan,
            # while the other explicitly terminates the boundary layer there.
            fan_ids = [main_plus_ids[0], main_plus_ids[-1]]
            # Gmsh's documented global default is five fan elements per pi
            # radians. More sectors are not automatically better: the controlled
            # 8/24-sector comparison increased low-weight and low-volume faces.
            fan_n = max(3, int(mesh_cfg.get("open_boundary_layer_lip_fan_points", 5) or 5))
            lines += [
                f"Field[1].FanPointsList = {{{', '.join(map(str, fan_ids))}}};",
                f"Field[1].FanPointsSizesList = {{{', '.join(str(fan_n) for _ in fan_ids)}}};",
            ]
        lines.append("BoundaryLayer Field = 1;")

    auto_interface_sizes = bool(mesh_cfg.get("open_near_wall_size_from_bl", True))
    last_bl_height = (
        float(bl.get("first_cell_height", 0.0))
        * float(bl.get("growth", 1.0)) ** max(int(bl.get("n_layers", 1)) - 1, 0)
        if bl else 0.0
    )
    te_tangential_spacing = te_curve_length / max(
        int(outer_wall_transfinite_nodes.get(outer_te_curve, 3)) - 1,
        1,
    )
    inlet_interface_nodes = max(20, int(mesh_cfg.get("open_inlet_marker_transfinite_nodes", 120) or 120))
    inlet_bridge_length = synthetic_curve_length(outer_bridge_ids)
    inlet_tangential_spacing = inlet_bridge_length / max(inlet_interface_nodes - 1, 1)
    if auto_interface_sizes:
        te_interface_size = min(lc_airfoil, max(last_bl_height, 0.85 * te_tangential_spacing))
        inlet_interface_size = min(lc_airfoil, max(last_bl_height, 0.85 * inlet_tangential_spacing))
    else:
        manual_interface_size = float(mesh_cfg.get("open_near_wall_size_chord", 0.01)) * chord_m
        manual_inlet_size = float(mesh_cfg.get("open_internal_inlet_size_chord", 0.0015)) * chord_m
        te_interface_size = min(lc_airfoil, manual_interface_size)
        inlet_interface_size = min(lc_airfoil, manual_inlet_size)
    # The finite-thickness lip cap is much shorter than the inlet interface.
    # Its neighboring cavity cells need a separate, highly local transition;
    # otherwise a single large triangle beside the cap dominates skewness and
    # face-volume ratio even when the inlet line itself is well resolved.
    lip_cap_interface_size = min(
        inlet_interface_size,
        max(thickness, 2.0 * float(bl.get("first_cell_height", 0.0) if bl else 0.0)),
    )

    background_field_ids: list[int] = []
    next_field_id = 2
    nearfield_requested = bool(mesh_cfg.get("open_nearfield_refinement_enabled", mesh_cfg.get("nearfield_refinement_enabled", True)))
    if nearfield_requested:
        dist_field_id = next_field_id
        near_threshold_field_id = next_field_id + 1
        middle_threshold_field_id = next_field_id + 2
        far_threshold_field_id = next_field_id + 3
        next_field_id += 4
        dist_min = float(mesh_cfg.get("open_nearfield_dist_min_chord", mesh_cfg.get("nearfield_dist_min_chord", 0.20))) * chord_m
        dist_mid = float(mesh_cfg.get("open_nearfield_intermediate_dist_chord", 0.18)) * chord_m
        dist_max = float(mesh_cfg.get("open_nearfield_dist_max_chord", mesh_cfg.get("nearfield_dist_max_chord", 1.10))) * chord_m
        intermediate_size = float(mesh_cfg.get("open_nearfield_intermediate_size_chord", mesh_cfg.get("open_cavity_size_chord", 0.04))) * chord_m
        outer_size = float(mesh_cfg.get("open_nearfield_outer_size_chord", 0.16)) * chord_m
        far_transition = float(mesh_cfg.get("open_farfield_transition_dist_chord", 9.0)) * chord_m
        sampling = int(mesh_cfg.get("open_nearfield_distance_sampling") or mesh_cfg.get("nearfield_distance_sampling") or 240)
        if not (0.0 <= dist_min < dist_mid < dist_max < far_transition):
            raise ValueError(
                "Open transition distances must satisfy 0 <= near < intermediate < outer < farfield transition."
            )
        if not (lc_airfoil <= intermediate_size <= outer_size <= lc_farfield):
            raise ValueError("Open sizes must satisfy wall <= intermediate <= outer <= farfield.")
        lines += [
            "",
            "// Three-stage exterior transition: fine at the BL, moderate around",
            "// the airfoil, then a slow growth to the distant farfield boundary.",
            f"Field[{dist_field_id}] = Distance;",
            f"Field[{dist_field_id}].CurvesList = {{{', '.join(map(str, exterior_size_curve_ids))}}};",
            f"Field[{dist_field_id}].NumPointsPerCurve = {sampling};",
            f"Field[{near_threshold_field_id}] = Threshold;",
            f"Field[{near_threshold_field_id}].InField = {dist_field_id};",
            f"Field[{near_threshold_field_id}].SizeMin = lc_airfoil;",
            f"Field[{near_threshold_field_id}].SizeMax = {intermediate_size:.12g};",
            f"Field[{near_threshold_field_id}].DistMin = {dist_min:.12g};",
            f"Field[{near_threshold_field_id}].DistMax = {dist_mid:.12g};",
            f"Field[{near_threshold_field_id}].Sigmoid = 0;",
            f"Field[{near_threshold_field_id}].StopAtDistMax = 1;",
            f"Field[{middle_threshold_field_id}] = Threshold;",
            f"Field[{middle_threshold_field_id}].InField = {dist_field_id};",
            f"Field[{middle_threshold_field_id}].SizeMin = {intermediate_size:.12g};",
            f"Field[{middle_threshold_field_id}].SizeMax = {outer_size:.12g};",
            f"Field[{middle_threshold_field_id}].DistMin = {dist_mid:.12g};",
            f"Field[{middle_threshold_field_id}].DistMax = {dist_max:.12g};",
            f"Field[{middle_threshold_field_id}].Sigmoid = 0;",
            f"Field[{middle_threshold_field_id}].StopAtDistMax = 1;",
            f"Field[{far_threshold_field_id}] = Threshold;",
            f"Field[{far_threshold_field_id}].InField = {dist_field_id};",
            f"Field[{far_threshold_field_id}].SizeMin = {outer_size:.12g};",
            f"Field[{far_threshold_field_id}].SizeMax = lc_farfield;",
            f"Field[{far_threshold_field_id}].DistMin = {dist_max:.12g};",
            f"Field[{far_threshold_field_id}].DistMax = {far_transition:.12g};",
            f"Field[{far_threshold_field_id}].Sigmoid = 0;",
        ]
        if single_surface_for_mesh_extrusion:
            background_field_ids.extend(
                [near_threshold_field_id, middle_threshold_field_id, far_threshold_field_id]
            )
        else:
            near_restrict_field_id = next_field_id
            middle_restrict_field_id = next_field_id + 1
            far_restrict_field_id = next_field_id + 2
            next_field_id += 3
            lines += [
                f"Field[{near_restrict_field_id}] = Restrict;",
                f"Field[{near_restrict_field_id}].InField = {near_threshold_field_id};",
                f"Field[{near_restrict_field_id}].SurfacesList = {{{external_surface}}};",
                f"Field[{middle_restrict_field_id}] = Restrict;",
                f"Field[{middle_restrict_field_id}].InField = {middle_threshold_field_id};",
                f"Field[{middle_restrict_field_id}].SurfacesList = {{{external_surface}}};",
                f"Field[{far_restrict_field_id}] = Restrict;",
                f"Field[{far_restrict_field_id}].InField = {far_threshold_field_id};",
                f"Field[{far_restrict_field_id}].SurfacesList = {{{external_surface}}};",
            ]
            background_field_ids.extend(
                [near_restrict_field_id, middle_restrict_field_id, far_restrict_field_id]
            )

    if not single_surface_for_mesh_extrusion:
        # The rounded TE and inlet bridge have much smaller tangential spacing
        # than the mean wall spacing. Local fields make the first triangles
        # follow the actual BL-front edges instead of jumping immediately to
        # the global near-wall size.
        local_external_transitions = [
            (
                outer_inlet_bridge_curve,
                inlet_interface_size,
                min(0.08, float(mesh_cfg.get("open_internal_inlet_dist_max_chord", 0.08))),
                "exterior inlet bridge matched to tangential BL-front spacing",
            ),
        ]
        if outer_te_curve in outer_wall_curve_ids:
            local_external_transitions.insert(0, (
                outer_te_curve,
                te_interface_size,
                max(0.002, float(mesh_cfg.get("open_te_transition_distance_chord", 0.012) or 0.012)),
                "rounded TE",
            ))
        for curve_id, local_size, transition_chord, label in local_external_transitions:
            distance_field_id = next_field_id
            threshold_field_id = next_field_id + 1
            restrict_field_id = next_field_id + 2
            next_field_id += 3
            transition_distance = max(float(transition_chord) * chord_m, 8.0 * local_size)
            lines += [
                "",
                f"// Local BL-to-triangle transition at {label}.",
                f"Field[{distance_field_id}] = Distance;",
                f"Field[{distance_field_id}].CurvesList = {{{curve_id}}};",
                f"Field[{distance_field_id}].NumPointsPerCurve = 160;",
                f"Field[{threshold_field_id}] = Threshold;",
                f"Field[{threshold_field_id}].InField = {distance_field_id};",
                f"Field[{threshold_field_id}].SizeMin = {local_size:.12g};",
                f"Field[{threshold_field_id}].SizeMax = {lc_airfoil:.12g};",
                f"Field[{threshold_field_id}].DistMin = 0;",
                f"Field[{threshold_field_id}].DistMax = {transition_distance:.12g};",
                f"Field[{threshold_field_id}].Sigmoid = 0;",
                f"Field[{threshold_field_id}].StopAtDistMax = 1;",
                f"Field[{restrict_field_id}] = Restrict;",
                f"Field[{restrict_field_id}].InField = {threshold_field_id};",
                f"Field[{restrict_field_id}].SurfacesList = {{{external_surface}}};",
            ]
            background_field_ids.append(restrict_field_id)

    cavity_refinement_requested = bool(
        not single_surface_for_mesh_extrusion
        and mesh_cfg.get("open_mesh_internal_cavity", True)
    )
    if cavity_refinement_requested:
        cavity_distance_field_id = next_field_id
        cavity_threshold_field_id = next_field_id + 1
        cavity_restrict_field_id = next_field_id + 2
        next_field_id += 3
        cavity_wall_size = float(mesh_cfg.get("open_cavity_wall_size_chord", 0.018)) * chord_m
        cavity_size = float(mesh_cfg.get("open_cavity_size_chord", 0.060)) * chord_m
        cavity_transition = float(mesh_cfg.get("open_cavity_wall_transition_chord", 0.16)) * chord_m
        if not (0.0 < cavity_wall_size <= cavity_size):
            raise ValueError("Open cavity wall size must be positive and no larger than the cavity interior size.")
        lines += [
            "",
            "// Cavity-only field: moderately fine at the inner fabric wall and",
            "// progressively coarser in the nearly stagnant cavity core.",
            f"Field[{cavity_distance_field_id}] = Distance;",
            f"Field[{cavity_distance_field_id}].CurvesList = {{{', '.join(map(str, inner_wall_curve_ids))}}};",
            f"Field[{cavity_distance_field_id}].NumPointsPerCurve = {max(80, int(mesh_cfg.get('open_nearfield_distance_sampling', 240) or 240) // 2)};",
            f"Field[{cavity_threshold_field_id}] = Threshold;",
            f"Field[{cavity_threshold_field_id}].InField = {cavity_distance_field_id};",
            f"Field[{cavity_threshold_field_id}].SizeMin = {cavity_wall_size:.12g};",
            f"Field[{cavity_threshold_field_id}].SizeMax = {cavity_size:.12g};",
            f"Field[{cavity_threshold_field_id}].DistMin = 0;",
            f"Field[{cavity_threshold_field_id}].DistMax = {cavity_transition:.12g};",
            f"Field[{cavity_threshold_field_id}].Sigmoid = 0;",
            f"Field[{cavity_restrict_field_id}] = Restrict;",
            f"Field[{cavity_restrict_field_id}].InField = {cavity_threshold_field_id};",
            f"Field[{cavity_restrict_field_id}].SurfacesList = {{{cavity_surface}}};",
        ]
        background_field_ids.append(cavity_restrict_field_id)

    internal_te_refinement_requested = bool(
        not single_surface_for_mesh_extrusion
        and inner_te_curve_ids
        and mesh_cfg.get("open_internal_te_refinement_enabled", True)
    )
    internal_te_interface_size = None
    if internal_te_refinement_requested:
        te_inner_curve = inner_te_curve_ids[0]
        te_inner_nodes = max(2, int(inner_wall_transfinite_nodes.get(te_inner_curve, 2)))
        te_inner_spacing = inner_curve_lengths.get(te_inner_curve, 0.0) / max(te_inner_nodes - 1, 1)
        te_size_factor = max(0.25, float(mesh_cfg.get("open_internal_te_size_factor", 0.90) or 0.90))
        cavity_wall_size = float(mesh_cfg.get("open_cavity_wall_size_chord", 0.012)) * chord_m
        cavity_size = float(mesh_cfg.get("open_cavity_size_chord", 0.045)) * chord_m
        internal_te_interface_size = min(
            cavity_wall_size,
            max(2.0 * thickness, te_size_factor * te_inner_spacing),
        )
        te_distance_field_id = next_field_id
        te_threshold_field_id = next_field_id + 1
        te_restrict_field_id = next_field_id + 2
        next_field_id += 3
        te_transition = max(
            float(mesh_cfg.get("open_internal_te_dist_max_chord", 0.08) or 0.08) * chord_m,
            8.0 * internal_te_interface_size,
        )
        lines += [
            "",
            "// Inner-TE cavity refinement derived from its independent tangential spacing.",
            f"Field[{te_distance_field_id}] = Distance;",
            f"Field[{te_distance_field_id}].CurvesList = {{{te_inner_curve}}};",
            f"Field[{te_distance_field_id}].NumPointsPerCurve = {max(40, 2 * te_inner_nodes)};",
            f"Field[{te_threshold_field_id}] = Threshold;",
            f"Field[{te_threshold_field_id}].InField = {te_distance_field_id};",
            f"Field[{te_threshold_field_id}].SizeMin = {internal_te_interface_size:.12g};",
            f"Field[{te_threshold_field_id}].SizeMax = {cavity_size:.12g};",
            f"Field[{te_threshold_field_id}].DistMin = 0;",
            f"Field[{te_threshold_field_id}].DistMax = {te_transition:.12g};",
            f"Field[{te_threshold_field_id}].Sigmoid = 0;",
            f"Field[{te_threshold_field_id}].StopAtDistMax = 1;",
            f"Field[{te_restrict_field_id}] = Restrict;",
            f"Field[{te_restrict_field_id}].InField = {te_threshold_field_id};",
            f"Field[{te_restrict_field_id}].SurfacesList = {{{cavity_surface}}};",
        ]
        background_field_ids.append(te_restrict_field_id)

    internal_inlet_refinement_requested = bool(mesh_cfg.get("open_internal_inlet_refinement_enabled", True))
    internal_inlet_active_size = None
    if internal_inlet_refinement_requested:
        dist_field_id = next_field_id
        threshold_field_id = next_field_id + 1
        next_field_id += 2
        dist_min = float(mesh_cfg.get("open_internal_inlet_dist_min_chord", 0.0)) * chord_m
        dist_max = float(mesh_cfg.get("open_internal_inlet_dist_max_chord", 0.08)) * chord_m
        # Triangles in the cavity share tangential edges with the inlet/BL
        # front, so their target must follow that tangential spacing. The much
        # smaller fabric-normal scale is retained only in the local lip-cap
        # field and in the transfinite connector across the solid thickness.
        # Applying it to the complete inlet neighborhood produced tiny cells
        # beside long tangential edges and triggered low interpolation weights
        # and face-volume ratios in checkMesh.
        size_min = inlet_interface_size
        internal_inlet_active_size = size_min
        size_max = float(mesh_cfg.get("open_cavity_size_chord", 0.060)) * chord_m
        sampling = int(mesh_cfg.get("open_internal_inlet_distance_sampling", 120) or 120)
        inlet_refinement_curves = (
            (list(inlet_size_marker_curve_ids) or [outer_inlet_bridge_curve])
            if single_surface_for_mesh_extrusion
            else [inner_inlet_bridge_curve]
        )
        if not inlet_refinement_curves:
            raise RuntimeError("Open inlet refinement requested but no valid inlet sizing curve was created.")
        cap_surface_ids = (
            [single_fluid_surface]
            if single_surface_for_mesh_extrusion
            else [cavity_surface, inlet_transition_surface]
        )
        lines += [
            "",
            "// Match adjacent triangles to tangential inlet/BL-front spacing,",
            "// then grow them towards the nearly stagnant cavity core.",
            f"Field[{dist_field_id}] = Distance;",
            f"Field[{dist_field_id}].CurvesList = {{{', '.join(map(str, inlet_refinement_curves))}}};",
            f"Field[{dist_field_id}].NumPointsPerCurve = {sampling};",
            f"Field[{threshold_field_id}] = Threshold;",
            f"Field[{threshold_field_id}].InField = {dist_field_id};",
            f"Field[{threshold_field_id}].SizeMin = {size_min:.12g};",
            f"Field[{threshold_field_id}].SizeMax = {size_max:.12g};",
            f"Field[{threshold_field_id}].DistMin = {dist_min:.12g};",
            f"Field[{threshold_field_id}].DistMax = {dist_max:.12g};",
            f"Field[{threshold_field_id}].StopAtDistMax = 1;",
        ]
        if single_surface_for_mesh_extrusion:
            background_field_ids.append(threshold_field_id)
        else:
            restrict_field_id = next_field_id
            next_field_id += 1
            lines += [
                f"Field[{restrict_field_id}] = Restrict;",
                f"Field[{restrict_field_id}].InField = {threshold_field_id};",
                f"Field[{restrict_field_id}].SurfacesList = {{{cavity_surface}, {inlet_transition_surface}}};",
            ]
            background_field_ids.append(restrict_field_id)

        cap_distance_field_id = next_field_id
        cap_threshold_field_id = next_field_id + 1
        cap_restrict_field_id = next_field_id + 2
        next_field_id += 3
        cap_transition = max(0.002 * chord_m, 6.0 * lip_cap_interface_size)
        lines += [
            "",
            "// Highly local transition around finite-thickness lip caps.",
            f"Field[{cap_distance_field_id}] = Distance;",
            f"Field[{cap_distance_field_id}].CurvesList = {{{lower_lip_cap_curve}, {upper_lip_cap_curve}}};",
            f"Field[{cap_distance_field_id}].NumPointsPerCurve = 80;",
            f"Field[{cap_threshold_field_id}] = Threshold;",
            f"Field[{cap_threshold_field_id}].InField = {cap_distance_field_id};",
            f"Field[{cap_threshold_field_id}].SizeMin = {lip_cap_interface_size:.12g};",
            f"Field[{cap_threshold_field_id}].SizeMax = {inlet_interface_size:.12g};",
            f"Field[{cap_threshold_field_id}].DistMin = 0;",
            f"Field[{cap_threshold_field_id}].DistMax = {cap_transition:.12g};",
            f"Field[{cap_threshold_field_id}].Sigmoid = 0;",
            f"Field[{cap_threshold_field_id}].StopAtDistMax = 1;",
            f"Field[{cap_restrict_field_id}] = Restrict;",
            f"Field[{cap_restrict_field_id}].InField = {cap_threshold_field_id};",
            f"Field[{cap_restrict_field_id}].SurfacesList = {{{', '.join(map(str, cap_surface_ids))}}};",
        ]
        background_field_ids.append(cap_restrict_field_id)
    if len(background_field_ids) == 1:
        lines.append(f"Background Field = {background_field_ids[0]};")
    elif len(background_field_ids) > 1:
        min_field_id = next_field_id
        lines += [
            "",
            "// Combine open thin-solid refinement fields by taking the smallest requested local size.",
            f"Field[{min_field_id}] = Min;",
            f"Field[{min_field_id}].FieldsList = {{{', '.join(map(str, background_field_ids))}}};",
            f"Background Field = {min_field_id};",
        ]

    physical_groups = ["airfoil_wall", "farfield", "fluid"]
    if openfoam_3d:
        span = float(mesh_cfg.get("spanwise_thickness_chord", 0.01)) * chord_m
        layers = int(mesh_cfg.get("spanwise_layers", 1))
        if span <= 0.0:
            raise ValueError("spanwise_thickness_chord must be positive for OpenFOAM-ready meshes.")
        if layers != 1:
            raise ValueError("OpenFOAM 2D cases must use exactly one spanwise layer.")
        # The three surfaces extrude robustly with Gmsh 4.8. Their coincident
        # interfaces are named explicitly and stitched after gmshToFoam.
        far_lateral = [f"out[{2 + i}]" for i in range(len(far_curves))]
        # out[6] is the outer inlet interface; out[7] is the actual exterior
        # fabric wall. The remaining entries are inner wall and lip caps.
        wall_lateral = ["out[7]", "out[10]", "out[15]", "out[17]"]
        lines += [
            "",
            "// One-cell-thick conformal 3D extrusion required by OpenFOAM 2D.",
            f"out[] = Extrude {{0, 0, {span:.12g}}} {{ Surface{{{', '.join(map(str, fluid_surfaces))}}}; Layers{{{layers}}}; Recombine; }};",
            f"Physical Surface(\"frontAndBack\") = {{{', '.join(map(str, fluid_surfaces))}, out[0], out[8], out[12]}};",
            f"Physical Surface(\"farfield\") = {{{', '.join(far_lateral)}}};",
            f"Physical Surface(\"airfoil_wall\") = {{{', '.join(wall_lateral)}}};",
            "Physical Volume(\"fluid\") = {out[1], out[9], out[13]};",
        ]
        physical_groups = ["airfoil_wall", "farfield", "frontAndBack", "fluid"]
    else:
        lines.append(f"Physical Line(\"airfoil_wall\") = {{{', '.join(map(str, wall_curve_ids))}}};")
        lines.append(f"Physical Line(\"farfield\") = {{{', '.join(map(str, far_curves))}}};")
        lines.append(f"Physical Surface(\"fluid\") = {{{', '.join(map(str, fluid_surfaces))}}};")

    out_geo.parent.mkdir(parents=True, exist_ok=True)
    out_geo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "surface_kind": "open_profile_thin_solid_connected_fluid_region",
        "physical_groups": physical_groups,
        "ram_air_inlet_is_physical_patch": False,
        "boundary_layer_requested": bool(request_bl),
        "boundary_layer_layers_requested": int(mesh_cfg.get("open_boundary_layer_layers", mesh_cfg.get("boundary_layer_layers", 0)) or 0) if request_bl else 0,
        "boundary_layer_quads_requested": bool(mesh_cfg.get("open_recombine_boundary_layer", mesh_cfg.get("recombine_boundary_layer", False))) if request_bl else False,
        "boundary_layer_first_cell_height_chord": float(bl["first_cell_height_chord"]) if request_bl else 0.0,
        "boundary_layer_first_cell_height_m": float(bl["first_cell_height"]) if request_bl else 0.0,
        "boundary_layer_requested_first_cell_height_chord": float(bl["requested_first_cell_height_chord"]) if request_bl else 0.0,
        "boundary_layer_first_cell_height_source": bl.get("first_cell_height_source") if request_bl else None,
        "boundary_layer_yplus_estimate": estimate_boundary_layer_from_yplus_inputs(
            mesh_cfg,
            chord_m,
            first_cell_key="open_first_cell_height_chord_override",
            first_cell_m_key="open_first_cell_height_m_override",
            growth_key="open_boundary_layer_growth",
            layers_key="open_boundary_layer_layers",
        ) if request_bl else None,
        "boundary_layer_total_thickness_chord": float(bl["total_thickness_chord"]) if request_bl else 0.0,
        "boundary_layer_raw_total_thickness_chord": float(bl["raw_total_thickness_chord"]) if request_bl else 0.0,
        "boundary_layer_total_thickness_limited": bool(bl["total_thickness_limited"]) if request_bl else False,
        "boundary_layer_curve_ids": exterior_bl_curve_ids if request_bl else [],
        "boundary_layer_excluded_te_curve_ids": [],
        "boundary_layer_exclude_te_cap_from_bl": False,
        "nearfield_refinement_requested": bool(nearfield_requested),
        "nearfield_dist_min_chord": float(mesh_cfg.get("open_nearfield_dist_min_chord", 0.0) or 0.0),
        "nearfield_intermediate_dist_chord": float(mesh_cfg.get("open_nearfield_intermediate_dist_chord", 0.0) or 0.0),
        "nearfield_dist_max_chord": float(mesh_cfg.get("open_nearfield_dist_max_chord", 0.0) or 0.0),
        "nearfield_intermediate_size_chord": float(mesh_cfg.get("open_nearfield_intermediate_size_chord", 0.0) or 0.0),
        "nearfield_outer_size_chord": float(mesh_cfg.get("open_nearfield_outer_size_chord", 0.0) or 0.0),
        "farfield_transition_dist_chord": float(mesh_cfg.get("open_farfield_transition_dist_chord", 0.0) or 0.0),
        "open_internal_inlet_refinement_requested": bool(internal_inlet_refinement_requested),
        "open_internal_inlet_refinement_kind": (
            "BL-derived local sizes on inlet interface and finite-thickness lip caps plus embedded inlet sizing marker"
            if internal_inlet_refinement_requested and inlet_size_marker_curve_ids
            else ("BL-derived local sizes on inlet interface and finite-thickness lip caps" if internal_inlet_refinement_requested else None)
        ),
        "open_internal_te_refinement_requested": bool(internal_te_refinement_requested),
        "open_internal_te_curve_ids": inner_te_curve_ids,
        "open_internal_te_interface_size_chord": (
            float(internal_te_interface_size / chord_m) if internal_te_interface_size is not None else None
        ),
        "open_internal_te_dist_max_chord": float(mesh_cfg.get("open_internal_te_dist_max_chord", 0.0) or 0.0),
        "open_interface_sizes_from_boundary_layer": bool(auto_interface_sizes),
        "open_te_interface_size_chord": float(te_interface_size / chord_m),
        "open_te_tangential_spacing_chord": float(te_tangential_spacing / chord_m),
        "open_inlet_interface_size_chord": float(inlet_interface_size / chord_m),
        "open_inlet_tangential_spacing_chord": float(inlet_tangential_spacing / chord_m),
        "open_lip_cap_interface_size_chord": float(lip_cap_interface_size / chord_m),
        "open_internal_inlet_active_size_chord": (
            float(internal_inlet_active_size / chord_m) if internal_inlet_active_size is not None else None
        ),
        "open_internal_inlet_refinement_scope": (
            "cavity_and_external_adjacent_surfaces; transfinite throat remains controlled by its curve nodes"
            if str(mesh_cfg.get("open_inlet_transition_elements", "triangles")) in {"graded_quads", "recombined_quads"}
            else (
                "cavity_external_and_graded_triangular_throat"
                if str(mesh_cfg.get("open_inlet_transition_elements")) == "graded_triangles"
                else "cavity_external_and_unstructured_triangular_throat"
            )
        ),
        "open_internal_inlet_dist_min_chord": float(mesh_cfg.get("open_internal_inlet_dist_min_chord", 0.0) or 0.0),
        "open_surface_size_general_chord": float(mesh_cfg.get("open_surface_size_general_chord", 0.0) or 0.0),
        "open_surface_size_from_boundary_layer": open_surface_size_bl_info,
        "open_surface_size_le_chord": float(mesh_cfg.get("open_surface_size_le_chord", 0.0) or 0.0),
        "open_surface_size_lip_chord": float(mesh_cfg.get("open_surface_size_lip_chord", 0.0) or 0.0),
        "open_surface_transfinite_multiplier": float(mesh_cfg.get("open_surface_transfinite_multiplier", 1.0) or 1.0),
        "open_surface_target_nodes": int(mesh_cfg.get("open_surface_target_nodes", 0) or 0),
        "open_outer_wall_transfinite_distributions": {str(k): v for k, v in outer_wall_transfinite_distributions.items()},
        "open_wall_end_bump_enabled": bool(outer_end_bump_enabled),
        "open_wall_end_bump_strength": float(outer_end_bump),
        "open_inner_wall_transfinite_curve_nodes": {str(k): int(v) for k, v in inner_wall_transfinite_nodes.items()},
        "open_inner_wall_transfinite_distributions": {str(k): v for k, v in inner_wall_transfinite_distributions.items()},
        "open_inner_wall_end_bump_enabled": bool(inner_end_bump_enabled),
        "open_inner_wall_end_bump_strength": float(inner_end_bump),
        "open_inner_wall_node_factor": float(mesh_cfg.get("open_inner_wall_node_factor", 0.60) or 0.60),
        "open_inner_te_node_factor": float(mesh_cfg.get("open_inner_te_node_factor", 0.25) or 0.25),
        "open_inner_wall_min_nodes": int(mesh_cfg.get("open_inner_wall_min_nodes", 48) or 48),
        "open_inner_te_min_nodes": int(mesh_cfg.get("open_inner_te_min_nodes", 18) or 18),
        "open_surface_transfinite_progression": float(mesh_cfg.get("open_surface_transfinite_progression", 1.0) or 1.0),
        "open_cavity_wall_size_chord": float(mesh_cfg.get("open_cavity_wall_size_chord", 0.0) or 0.0),
        "open_cavity_wall_transition_chord": float(mesh_cfg.get("open_cavity_wall_transition_chord", 0.0) or 0.0),
        "open_cavity_size_chord": float(mesh_cfg.get("open_cavity_size_chord", 0.0) or 0.0),
        "open_farfield_size_chord": float(mesh_cfg.get("open_farfield_size_chord", mesh_cfg.get("farfield_size_chord", 0.0)) or 0.0),
        "open_fluid_topology": topology_mode,
        "open_connected_fluid_surface": True,
        "open_thin_solid_fluid_surface": True,
        "open_effective_fabric_thickness_chord": float(thickness_chord),
        "open_requested_fabric_thickness_chord": float(requested_thickness_chord),
        "open_fabric_offset_thickness_reductions": int(offset_reductions),
        "open_fabric_offset_self_intersections": int(offset_self_intersections),
        "open_fabric_offset_cross_intersections": int(offset_cross_intersections),
        "open_exterior_normal_upper_valid": bool(upper_outward),
        "open_exterior_normal_lower_valid": bool(lower_outward),
        "open_boundary_layer_restricted_to_external_side": bool(
            request_bl and (not single_surface_for_mesh_extrusion or not include_inlet_bridge_in_bl)
        ),
        "open_inlet_boundary_layer_mode": str(
            mesh_cfg.get("open_inlet_boundary_layer_mode", "full_prismatic_bridge_without_fans")
        ),
        "open_inlet_transition_elements": str(mesh_cfg.get("open_inlet_transition_elements", "graded_quads")),
        "open_inlet_bridge_smoothing_enabled": bool(inlet_bridge_smoothing_enabled),
        "open_inlet_bridge_smoothing_handle_fraction": float(inlet_bridge_handle_fraction),
        "open_inlet_bridge_curve_kind": outer_bridge_curve_kind,
        "open_inner_inlet_bridge_curve_kind": inner_bridge_curve_kind,
        "open_lip_cap_rounding_enabled": bool(lip_cap_rounding_enabled),
        "open_lip_cap_rounding_points": int(lip_cap_rounding_points if lip_cap_rounding_enabled else 2),
        "open_inlet_transition_distribution": inlet_transition_distribution,
        "open_duplicate_inlet_interface_for_one_sided_bl": not bool(single_surface_for_mesh_extrusion),
        "open_inlet_bridge_embedded_in_single_fluid_surface": bool(
            request_bl and single_surface_for_mesh_extrusion and include_inlet_bridge_in_bl
        ),
        "open_cavity_size_field_restricted_to_cavity": bool(cavity_refinement_requested),
        "open_wall_curve_method": str(mesh_cfg.get("open_wall_curve_method", "segmented_outer_splines")),
        "open_boundary_layer_single_loop_bspline": bool(
            len(outer_wall_curve_ids) == 1 and outer_wall_curve_defs and str(outer_wall_curve_defs[0][2]).lower() == "bspline"
        ),
        "open_boundary_layer_single_loop_curve_kind": (
            "single_exterior_bspline_with_separate_nonphysical_inlet_bridge"
            if len(outer_wall_curve_ids) == 1 and outer_wall_curve_defs and str(outer_wall_curve_defs[0][2]).lower() == "bspline"
            else (
                "single_exterior_spline_with_separate_nonphysical_inlet_bridge"
                if len(outer_wall_curve_ids) == 1 and outer_wall_curve_defs and str(outer_wall_curve_defs[0][2]).lower() == "spline"
                else "three_exterior_splines_with_transfinite_inlet_connector"
            )
        ),
        "open_boundary_layer_single_loop_transfinite": bool(len(outer_wall_curve_ids) == 1 and bool(outer_wall_transfinite_nodes)),
        "open_boundary_layer_curve_policy": (
            "exterior_fabric_sections_plus_nonphysical_inlet_bridge_external_side_only"
            if include_inlet_bridge_in_bl
            else (
                "single_exterior_wall_curve_only_with_separate_nonphysical_inlet_sizing_bridge"
                if len(outer_wall_curve_ids) == 1
                else "exterior_fabric_sections_only_with_separate_transfinite_inlet_connector"
            )
        ),
        "open_diagnostic_loop_closed": True,
        "open_diagnostic_loop_curve_count": int(len(wall_curve_ids)),
        "open_boundary_layer_curve_count": int(len(exterior_bl_curve_ids)),
        "open_boundary_layer_split_curvature_sections": bool(len(outer_wall_curve_ids) == 3),
        "open_te_boundary_curve_id": int(outer_te_curve) if outer_te_curve in outer_wall_curve_ids else None,
        "open_te_transfinite_min_nodes": int(mesh_cfg.get("open_te_transfinite_min_nodes", 28) or 28),
        "open_te_refinement_width_chord": float(mesh_cfg.get("open_te_refinement_width_chord", 0.0) or 0.0),
        "open_te_transition_distance_chord": float(mesh_cfg.get("open_te_transition_distance_chord", 0.0) or 0.0),
        "open_lip_transfinite_min_nodes": int(mesh_cfg.get("open_lip_transfinite_min_nodes", 0) or 0),
        "open_outer_wall_transfinite_curve_nodes": {str(k): int(v) for k, v in outer_wall_transfinite_nodes.items()},
        "open_boundary_layer_aniso_max_deg": float(mesh_cfg.get("open_boundary_layer_aniso_max_deg", 30.0) or 30.0),
        "open_diagnostic_boundary_layer_enabled": bool(mesh_cfg.get("open_diagnostic_boundary_layer_enabled", True)),
        "open_inlet_marker_curve_ids": ordered_curve_ids(inlet_marker_edges),
        "open_boundary_layer_curve_ids": exterior_bl_curve_ids if request_bl else [],
        "open_boundary_layer_excluded_te_curve_ids": [],
        "open_boundary_layer_exclude_te_cap_from_bl": False,
        "open_boundary_layer_trim_end_segments": bool(trim_end_points),
        "open_boundary_layer_trim_ends_chord": float(mesh_cfg.get("open_boundary_layer_trim_ends_chord", 0.0) or 0.0),
        "open_boundary_layer_trim_end_points": int(trim_end_points),
        "open_boundary_layer_fan_at_lips": bool(request_bl and mesh_cfg.get("open_boundary_layer_fan_at_lips", False)),
        "open_boundary_layer_lip_fan_applied": bool(request_bl and fan_at_lips),
        "boundary_layer_fan_at_le": bool(request_bl and fan_at_lips),
        "open_boundary_layer_lip_fan_points": int(mesh_cfg.get("open_boundary_layer_lip_fan_points", 0) or 0) if request_bl else 0,
        "open_boundary_layer_inlet_marker_included": False,
        "open_boundary_layer_inlet_bridge_in_single_loop": False,
        "open_boundary_layer_inlet_bridge_included": bool(request_bl and include_inlet_bridge_in_bl),
        "open_inlet_marker_transfinite_enabled": bool(inlet_connector_curve_ids or inlet_size_marker_curve_ids),
        "open_inlet_marker_transfinite_nodes": int(mesh_cfg.get("open_inlet_marker_transfinite_nodes", 0) or 0),
        "open_inlet_marker_bump_strength": float(mesh_cfg.get("open_inlet_marker_bump_strength", 0.60) or 0.60),
        "open_inlet_connector_transfinite": bool(inlet_connector_curve_ids or inlet_size_marker_curve_ids),
        "open_inlet_connector_curve_ids": inlet_connector_curve_ids,
        "open_inlet_refinement_bridge_enabled": bool(open_inlet_refinement_bridge_requested),
        "open_inlet_refinement_bridge_curve_ids": inlet_size_marker_curve_ids,
        "open_inlet_refinement_bridge_is_physical_patch": False,
        "open_inlet_refinement_bridge_in_boundary_layer": False,
        "open_exterior_inlet_bridge_curve_id": int(outer_inlet_bridge_curve),
        "open_transition_inlet_bridge_curve_id": int(transition_outer_inlet_bridge_curve),
        "open_inlet_connector_surface_id": 0 if single_surface_for_mesh_extrusion else int(inlet_transition_surface),
        "open_inlet_transition_mesh": (
            "embedded_marker" if single_surface_for_mesh_extrusion
            else (
                "graded_transfinite_triangles_from_exterior_y1_to_cavity"
                if str(mesh_cfg.get("open_inlet_transition_elements", "graded_quads")) == "graded_triangles"
                else (
                    "graded_transfinite_quads_from_exterior_y1_to_cavity"
                    if str(mesh_cfg.get("open_inlet_transition_elements")) == "graded_quads"
                    else (
                        "transfinite_recombined_one_cell_through_fabric_thickness"
                        if str(mesh_cfg.get("open_inlet_transition_elements")) == "recombined_quads"
                        else (
                            "transfinite_triangles_through_fabric_thickness"
                            if str(mesh_cfg.get("open_inlet_transition_elements")) == "transfinite_triangles"
                            else "unstructured_triangles_through_fabric_thickness"
                        )
                    )
                )
            )
        ),
        "open_internal_cavity_meshed": True,
        "open_internal_cavity_curve_count": int(len(inner_wall_curve_ids) + 1),
        "open_internal_cavity_curve_mode": "three_continuous_inner_splines_without_bl",
        "open_internal_cavity_shares_inlet_marker": bool(not single_surface_for_mesh_extrusion),
        "open_internal_cavity_duplicate_inlet_marker": False,
        "open_internal_cavity_solver_connected": True,
        "open_internal_cavity_note": (
            "One connected fluid surface surrounds a finite-thickness U-shaped fabric band; "
            "the exterior and cavity communicate through the real leading-edge opening, and "
            "no physical ram_air_inlet patch is created."
            if single_surface_for_mesh_extrusion
            else
            "Exterior, inlet transition and cavity are conformal fluid surfaces sharing "
            "internal interfaces; no physical ram_air_inlet patch is created."
        ),
        "open_wall_curve_ids": wall_curve_ids,
        "diagnostic_only": False,
        "openfoam_ready": bool(openfoam_3d),
        "extruded_3d": bool(openfoam_3d),
        "spanwise_layers": int(mesh_cfg.get("spanwise_layers", 1)) if openfoam_3d else 0,
        "spanwise_thickness_chord": float(mesh_cfg.get("spanwise_thickness_chord", 0.01)) if openfoam_3d else 0.0,
    }


def write_geo_open_diagnostic(points: pd.DataFrame, edges: pd.DataFrame, manifest: dict, mesh_cfg: dict, domain: str, out_geo: Path, variant: str) -> dict[str, Any]:
    """Diagnostic open-profile geometry.

    By default this writes one connected farfield fluid surface with the open
    profile curves embedded as internal constraints. The inlet opening is only a
    geometric BL-continuity marker and is not exported as a physical boundary.
    This remains diagnostic because OpenFOAM-ready open-cavity walls require a
    later baffle/thin-solid generator.
    """
    chord_m = float(manifest.get("chord_m", 1.0) or 1.0)
    lc_airfoil = float(mesh_cfg.get("open_surface_size_general_chord", mesh_cfg.get("surface_size_general_chord", 0.003))) * chord_m
    lc_le = float(mesh_cfg.get("open_surface_size_le_chord", mesh_cfg.get("open_surface_size_general_chord", 0.003))) * chord_m
    lc_lip = float(mesh_cfg.get("open_surface_size_lip_chord", mesh_cfg.get("open_surface_size_le_chord", 0.0012))) * chord_m
    lc_te = float(mesh_cfg.get("open_surface_size_te_chord", mesh_cfg.get("surface_size_rounded_te_chord", mesh_cfg.get("surface_size_te_chord", 0.001)))) * chord_m
    lc_cavity = float(mesh_cfg.get("open_cavity_size_chord", mesh_cfg.get("cavity_size_chord", 0.01))) * chord_m
    lc_farfield = float(mesh_cfg.get("farfield_size_chord", 0.5)) * chord_m
    dpar = domain_params(domain, mesh_cfg)
    all_edges = edges[edges.start_point_id != edges.end_point_id].sort_values("edge_id").copy()
    patch_lower = all_edges["patch_name"].astype(str).str.lower()
    inlet_marker_edges = all_edges[patch_lower.str.contains("inlet_opening_marker", case=False, na=False)].copy()
    upper_edges = all_edges[patch_lower.str.contains("outer_upper_wall", case=False, na=False)].copy()
    lower_edges = all_edges[patch_lower.str.contains("outer_lower_wall", case=False, na=False)].copy()
    te_edges = all_edges[patch_lower.str.contains("trailing_edge_wall", case=False, na=False)].copy()
    inlet_marker_curve_ids = ordered_curve_ids(inlet_marker_edges)
    pidx = points.set_index("point_id")
    extra_points: list[tuple[int, float, float, str]] = []
    x_le_open = float(points["x_m"].min())
    x_te_open = float(points["x_m"].max())
    le_size_window = max(float(mesh_cfg.get("open_le_refinement_width_chord", 0.18)) * chord_m, 1.0e-12)
    lip_size_window = max(float(mesh_cfg.get("open_lip_refinement_x_chord", 0.08)) * chord_m, 1.0e-12)
    te_size_window = max(float(mesh_cfg.get("open_te_refinement_width_chord", 0.05)) * chord_m, 1.0e-12)

    def point_lc_name(pid: int) -> str:
        for ep_id, _, _, ep_lc in extra_points:
            if ep_id == int(pid):
                return ep_lc
        row = pidx.loc[int(pid)]
        x_val = float(row["x_m"])
        role = str(row.get("boundary_role", "")).lower() if hasattr(row, "get") else ""
        if x_val >= x_te_open - te_size_window:
            return "lc_te"
        if "lip" in role or x_val <= x_le_open + lip_size_window:
            return "lc_lip"
        if x_val <= x_le_open + le_size_window:
            return "lc_le"
        return "lc_airfoil"

    def edge_point_sequence(edge_df: pd.DataFrame) -> list[int]:
        if edge_df.empty:
            return []
        ordered = edge_df.sort_values("edge_id").reset_index(drop=True)
        seq = [int(ordered.iloc[0].start_point_id)]
        seq.extend(int(v) for v in ordered["end_point_id"].tolist())
        return seq

    def point_xy(pid: int) -> np.ndarray:
        for ep_id, x_val, z_val, _ in extra_points:
            if ep_id == pid:
                return np.asarray([x_val, z_val], dtype=float)
        return pidx.loc[int(pid), ["x_m", "z_m"]].to_numpy(float)

    def orient_sequence(seq: list[int], start_pid: int, end_pid: int) -> list[int]:
        if not seq:
            return []
        if seq[0] == start_pid and seq[-1] == end_pid:
            return seq
        if seq[0] == end_pid and seq[-1] == start_pid:
            return list(reversed(seq))
        return seq

    def generated_te_cap_sequence(lower_te_pid: int, upper_te_pid: int, lower_prev_pid: int, upper_next_pid: int) -> tuple[list[int], dict[str, Any]]:
        info: dict[str, Any] = {
            "open_te_rounding_enabled": bool(mesh_cfg.get("open_te_rounding_enabled", True)),
            "open_te_rounding_applied": False,
            "open_te_rounding_points_requested": int(mesh_cfg.get("open_te_rounding_points", mesh_cfg.get("debug_te_rounding_points", 41))),
        }
        if not info["open_te_rounding_enabled"]:
            raw = orient_sequence(edge_point_sequence(te_edges), lower_te_pid, upper_te_pid)
            if len(raw) >= 2:
                info["open_te_rounding_note"] = "using_existing_te_edges_rounding_disabled"
                return raw, info
            info["open_te_rounding_note"] = "rounding_disabled_no_existing_te_edges"
            return [lower_te_pid, upper_te_pid], info
        a = point_xy(lower_te_pid)
        b = point_xy(upper_te_pid)
        lower_prev = point_xy(lower_prev_pid)
        upper_next = point_xy(upper_next_pid)
        gap = float(np.linalg.norm(b - a))
        if gap <= 1.0e-12:
            info["open_te_rounding_note"] = "zero_te_gap"
            return [lower_te_pid, upper_te_pid], info
        n_internal = max(3, int(info["open_te_rounding_points_requested"]))
        cap_internal, cap_info = _tangent_continuous_te_cap_points(a, b, lower_prev, upper_next, chord_m, n_internal)
        next_pid = int(max([int(v) for v in points["point_id"].tolist()] + [ep[0] for ep in extra_points] + [0])) + 1
        cap_ids: list[int] = []
        for p_cap in cap_internal:
            cap_ids.append(next_pid)
            extra_points.append((next_pid, float(p_cap[0]), float(p_cap[1]), "lc_te"))
            next_pid += 1
        info.update({
            "open_te_rounding_applied": True,
            "open_te_rounding_note": "generated_tangent_continuous_bezier_spline_between_lower_and_upper_te",
            "open_te_rounding_points_added": int(len(cap_ids)),
            "open_te_rounding_start_point_id": int(lower_te_pid),
            "open_te_rounding_end_point_id": int(upper_te_pid),
            "open_te_rounding_transfinite_nodes": int(max(10, len(cap_ids) + 2)),
            "open_te_rounding_cap_bulge_direction": "+x downstream with endpoint tangency",
        })
        info.update({f"open_{k}": v for k, v in cap_info.items() if k.startswith("te_rounding_")})
        return [lower_te_pid] + cap_ids + [upper_te_pid], info

    upper_seq = edge_point_sequence(upper_edges)
    lower_seq = edge_point_sequence(lower_edges)
    marker_curve_id = inlet_marker_curve_ids[0] if inlet_marker_curve_ids else 1003
    upper_curve_id = 1001
    lower_curve_id = 1011
    te_curve_id = 1021
    reserved_wall_ids = set(range(upper_curve_id, upper_curve_id + 8)) | set(range(lower_curve_id, lower_curve_id + 8))
    while te_curve_id in reserved_wall_ids or te_curve_id == abs(int(marker_curve_id)) or (te_curve_id + 1) in reserved_wall_ids or (te_curve_id + 1) == abs(int(marker_curve_id)):
        te_curve_id += 2
    te_curve_ids = [te_curve_id, te_curve_id + 1]
    internal_curve_ids: list[int] = []
    internal_marker_curve_id: int | None = None
    wall_curve_ids: list[int] = []
    bl_curve_ids: list[int] = []
    upper_wall_curve_defs: list[tuple[int, list[int], str, bool]] = []
    lower_wall_curve_defs: list[tuple[int, list[int], str, bool]] = []
    loop_curve_ids: list[int] = []
    loop_entries: list[int] = []
    internal_loop_entries: list[int] = []
    open_boundary_layer_excluded_te_curve_ids: list[int] = []
    loop_closed = False
    te_rounding_info: dict[str, Any] = {}
    connected_fluid_surface = bool(mesh_cfg.get("open_connected_fluid_surface", False))
    single_loop_bspline = bool(mesh_cfg.get("open_boundary_layer_single_loop_bspline", True))
    single_loop_curve_kind = str(mesh_cfg.get("open_boundary_layer_single_loop_curve_kind", "Spline")).strip()
    if single_loop_curve_kind not in {"Spline", "BSpline"}:
        single_loop_curve_kind = "Spline"
    single_loop_transfinite = bool(mesh_cfg.get("open_boundary_layer_single_loop_transfinite", False))
    internal_cavity_curve_mode = str(mesh_cfg.get("open_internal_cavity_curve_mode", "spline")).strip().lower()
    bl_loop_curve_id = 1000
    bl_loop_seq: list[int] = []
    mesh_internal_cavity = False

    def split_open_wall_for_bl(seq: list[int], base_cid: int, label: str) -> list[tuple[int, list[int], str, bool]]:
        """Split an open wall so Gmsh BL avoids TE and inlet-lip endpoints."""
        if len(seq) < 8 or not bool(mesh_cfg.get("open_boundary_layer_trim_end_segments", True)):
            return [(base_cid, seq, f"{label}_spline", True)]
        trim = max(0.0, float(mesh_cfg.get("open_boundary_layer_trim_ends_chord", 0.08))) * chord_m
        if trim <= 0.0:
            return [(base_cid, seq, f"{label}_spline", True)]
        xy = [point_xy(pid) for pid in seq]
        seg = [float(np.linalg.norm(b - a)) for a, b in zip(xy, xy[1:])]
        total = float(sum(seg))
        if total <= 4.0 * trim:
            return [(base_cid, seq, f"{label}_spline", True)]
        cumulative = [0.0]
        for length in seg:
            cumulative.append(cumulative[-1] + length)
        start_idx = next((i for i, dist in enumerate(cumulative) if dist >= trim), 1)
        end_idx = next((i for i, dist in enumerate(cumulative) if total - dist <= trim), len(seq) - 2)
        start_idx = max(1, min(start_idx, len(seq) - 4))
        end_idx = max(start_idx + 2, min(end_idx, len(seq) - 2))
        defs: list[tuple[int, list[int], str, bool]] = []
        cid = base_cid
        if start_idx > 0:
            defs.append((cid, seq[: start_idx + 1], f"{label}_start_endpoint_trim", False))
            cid += 1
        defs.append((cid, seq[start_idx : end_idx + 1], f"{label}_main_bl_spline", True))
        cid += 1
        if end_idx < len(seq) - 1:
            defs.append((cid, seq[end_idx:], f"{label}_end_endpoint_trim", False))
        return defs

    compressed_open_loop = len(upper_seq) >= 3 and len(lower_seq) >= 3 and len(inlet_marker_edges) == 1
    if compressed_open_loop:
        marker = inlet_marker_edges.iloc[0]
        marker_start = int(marker.start_point_id)
        marker_end = int(marker.end_point_id)
        marker_entry = None
        if upper_seq[-1] == marker_start and lower_seq[0] == marker_end:
            marker_entry = marker_curve_id
        elif upper_seq[-1] == marker_end and lower_seq[0] == marker_start:
            marker_entry = -marker_curve_id
        te_seq, te_rounding_info = generated_te_cap_sequence(lower_seq[-1], upper_seq[0], lower_seq[-2], upper_seq[1])
        te_seq = orient_sequence(te_seq, lower_seq[-1], upper_seq[0])
        if te_rounding_info.get("open_te_rounding_applied"):
            te_curve_ids = [te_curve_id]
        loop_closed = bool(marker_entry is not None and te_seq[0] == lower_seq[-1] and te_seq[-1] == upper_seq[0])
        if loop_closed:
            upper_wall_curve_defs = split_open_wall_for_bl(upper_seq, upper_curve_id, "outer_upper_wall")
            lower_wall_curve_defs = split_open_wall_for_bl(lower_seq, lower_curve_id, "outer_lower_wall")
            upper_wall_curve_ids = [cid for cid, _, _, _ in upper_wall_curve_defs]
            lower_wall_curve_ids = [cid for cid, _, _, _ in lower_wall_curve_defs]
            loop_curve_ids = upper_wall_curve_ids + [marker_curve_id] + lower_wall_curve_ids + te_curve_ids
            wall_curve_ids = upper_wall_curve_ids + lower_wall_curve_ids + te_curve_ids
            bl_curve_ids = [cid for cid, _, _, is_bl in upper_wall_curve_defs + lower_wall_curve_defs if is_bl]
            open_boundary_layer_excluded_te_curve_ids: list[int] = []
            if bool(mesh_cfg.get("open_boundary_layer_exclude_te_cap_from_bl", True)) and te_curve_ids:
                open_boundary_layer_excluded_te_curve_ids = list(te_curve_ids)
            else:
                bl_curve_ids.extend(te_curve_ids)
            if bool(mesh_cfg.get("open_boundary_layer_include_inlet_marker", True)):
                bl_curve_ids.insert(1, abs(int(marker_curve_id)))
            natural_loop_entries = upper_wall_curve_ids + [int(marker_entry)] + lower_wall_curve_ids + te_curve_ids
            loop_ids_for_area = upper_seq + lower_seq[1:] + te_seq[1:]
            loop_pts = [(float(point_xy(pid)[0]), float(point_xy(pid)[1])) for pid in loop_ids_for_area]
            loop_entries = list(natural_loop_entries)
            if polygon_area_xy(loop_pts) > 0:
                loop_entries = [-entry for entry in reversed(natural_loop_entries)]
            mesh_internal_cavity = bool(mesh_cfg.get("open_mesh_internal_cavity", True)) and not connected_fluid_surface
            if single_loop_bspline:
                bl_loop_seq = list(upper_seq) + list(lower_seq) + list(te_seq[1:])
                if bl_loop_seq and bl_loop_seq[-1] != bl_loop_seq[0]:
                    bl_loop_seq.append(bl_loop_seq[0])
                loop_curve_ids = [bl_loop_curve_id]
                loop_entries = [bl_loop_curve_id]
                wall_curve_ids = [bl_loop_curve_id]
                bl_curve_ids = [bl_loop_curve_id]
                open_boundary_layer_excluded_te_curve_ids = []
    if not loop_closed:
        diagnostic_loop_edges = all_edges[
            patch_lower.str.contains("outer_upper_wall|outer_lower_wall|trailing_edge_wall|inlet_opening_marker", case=False, na=False)
        ].copy()
        loop_curve_ids = ordered_curve_ids(diagnostic_loop_edges)
        wall_curve_ids = ordered_curve_ids(
            all_edges[patch_lower.str.contains("outer_upper_wall|outer_lower_wall|trailing_edge_wall", case=False, na=False)]
        )
        bl_curve_ids = list(wall_curve_ids)
        loop_entries = list(loop_curve_ids)

    internal_point_map: dict[int, int] = {}
    if loop_closed and mesh_internal_cavity:
        shared_inlet_pids = {int(upper_seq[-1]), int(lower_seq[0])} if upper_seq and lower_seq else set()
        ordered_internal_source_ids: list[int] = []
        for seq in [upper_seq, [upper_seq[-1], lower_seq[0]], lower_seq, te_seq]:
            for pid in seq:
                if pid not in ordered_internal_source_ids:
                    ordered_internal_source_ids.append(pid)
        next_pid = int(max([int(v) for v in points["point_id"].tolist()] + [ep[0] for ep in extra_points] + [0])) + 1
        for pid in ordered_internal_source_ids:
            if int(pid) in shared_inlet_pids:
                internal_point_map[int(pid)] = int(pid)
                continue
            xy = point_xy(pid)
            internal_point_map[int(pid)] = next_pid
            # Match the cavity boundary size to the exterior wall at the same
            # geometric location; the cavity interior can still coarsen away
            # from these boundary points through the surface meshing.
            extra_points.append((next_pid, float(xy[0]), float(xy[1]), point_lc_name(pid)))
            next_pid += 1

    lines = [
        "// Diagnostic open ram-air geometry. inlet_opening_marker is metadata only; ram_air_inlet is forbidden as a physical patch.",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {int(mesh_cfg.get('gmsh_mesh_algorithm_2d', 5))}; // 2D algorithm: 5=Delaunay, 6=Frontal-Delaunay",
        f"Mesh.RandomFactor = {float(mesh_cfg.get('gmsh_random_factor', 1.0e-7)):.12g};",
        f"Mesh.RandomSeed = {int(mesh_cfg.get('gmsh_random_seed', 1))};",
        "Mesh.Smoothing = 8;",
        "Mesh.CharacteristicLengthFromPoints = 1;",
        "Mesh.CharacteristicLengthFromCurvature = 1;",
        "Mesh.CharacteristicLengthExtendFromBoundary = 1;",
        f"lc_airfoil={lc_airfoil:.12g};",
        f"lc_le={lc_le:.12g};",
        f"lc_lip={lc_lip:.12g};",
        f"lc_te={lc_te:.12g};",
        f"lc_cavity={lc_cavity:.12g};",
        f"lc_farfield={lc_farfield:.12g};",
    ]
    for _, p in points.iterrows():
        lc_name = point_lc_name(int(p.point_id))
        lines.append(f"Point({int(p.point_id)}) = {{{float(p.x_m):.12g}, {float(p.z_m):.12g}, 0, {lc_name}}};")
    for pid, x_val, z_val, lc_name in extra_points:
        lines.append(f"Point({int(pid)}) = {{{x_val:.12g}, {z_val:.12g}, 0, {lc_name}}};")
    if loop_closed and compressed_open_loop:
        if single_loop_bspline and bl_loop_seq:
            lines.append(f"{single_loop_curve_kind}({bl_loop_curve_id}) = {{{', '.join(map(str, bl_loop_seq))}}}; // single_closed_open_profile_bl_loop_includes_inlet_bridge_and_te")
            if single_loop_transfinite:
                lines.append(f"Transfinite Curve {{{bl_loop_curve_id}}} = {max(16, len(bl_loop_seq))} Using Progression 1;")
        else:
            for cid, seq, label, is_bl in upper_wall_curve_defs + lower_wall_curve_defs:
                kind = "Spline" if len(seq) >= 3 else "Line"
                suffix = "bl" if is_bl else "no_bl"
                lines.append(f"{kind}({cid}) = {{{', '.join(map(str, seq))}}}; // {label}_{suffix}")
            if te_rounding_info.get("open_te_rounding_applied"):
                lines.append(f"Spline({te_curve_ids[0]}) = {{{', '.join(map(str, te_seq))}}}; // tangent_continuous_rounded_te_wall_spline")
                n_arc = int(te_rounding_info.get("open_te_rounding_transfinite_nodes", max(10, len(te_seq))))
                lines.append(f"Transfinite Curve {{{te_curve_ids[0]}}} = {n_arc} Using Progression 1;")
            else:
                lines.append(f"Spline({te_curve_ids[0]}) = {{{', '.join(map(str, te_seq))}}}; // rounded_trailing_edge_wall_spline")
                te_curve_ids = [te_curve_ids[0]]
        marker = inlet_marker_edges.iloc[0]
        lines.append(f"Line({marker_curve_id}) = {{{int(marker.start_point_id)}, {int(marker.end_point_id)}}}; // inlet_opening_marker non-physical diagnostic closure")
        if bool(mesh_cfg.get("open_inlet_marker_transfinite_enabled", True)):
            inlet_nodes = max(4, int(mesh_cfg.get("open_inlet_marker_transfinite_nodes", 48)))
            lines.append(f"Transfinite Curve {{{marker_curve_id}}} = {inlet_nodes} Using Progression 1; // dense non-physical inlet bridge for mesh-size continuity")
        if mesh_internal_cavity and internal_point_map:
            dup_upper = [internal_point_map[pid] for pid in upper_seq]
            dup_lower = [internal_point_map[pid] for pid in lower_seq]
            dup_te = [internal_point_map[pid] for pid in te_seq]
            next_internal_curve = 1101

            def append_internal_curve(seq: list[int], label: str) -> None:
                nonlocal next_internal_curve
                clean_seq = [int(pid) for pid in seq if int(pid) in pidx.index or int(pid) in {ep[0] for ep in extra_points}]
                if len(clean_seq) < 2:
                    return
                if internal_cavity_curve_mode == "spline" and len(clean_seq) >= 3:
                    lines.append(f"Spline({next_internal_curve}) = {{{', '.join(map(str, clean_seq))}}}; // {label}_duplicate_spline_no_bl")
                    internal_curve_ids.append(next_internal_curve)
                    internal_loop_entries.append(next_internal_curve)
                    next_internal_curve += 1
                    return
                for a_pid, b_pid in zip(clean_seq, clean_seq[1:]):
                    lines.append(f"Line({next_internal_curve}) = {{{a_pid}, {b_pid}}}; // {label}_duplicate_line_no_bl")
                    internal_curve_ids.append(next_internal_curve)
                    internal_loop_entries.append(next_internal_curve)
                    next_internal_curve += 1

            append_internal_curve(dup_upper, "internal_cavity_upper")
            if bool(mesh_cfg.get("open_duplicate_internal_inlet_marker_for_bl", True)):
                internal_marker_curve_id = next_internal_curve
                marker_sign = 1 if int(marker_entry) >= 0 else -1
                lines.append(
                    f"Line({internal_marker_curve_id}) = {{{int(marker.start_point_id)}, {int(marker.end_point_id)}}}; "
                    "// duplicate_internal_inlet_marker_no_bl"
                )
                if bool(mesh_cfg.get("open_inlet_marker_transfinite_enabled", True)):
                    inlet_nodes = max(4, int(mesh_cfg.get("open_inlet_marker_transfinite_nodes", 48)))
                    lines.append(f"Transfinite Curve {{{internal_marker_curve_id}}} = {inlet_nodes} Using Progression 1; // matches exterior inlet discretisation")
                internal_curve_ids.append(internal_marker_curve_id)
                internal_loop_entries.append(marker_sign * internal_marker_curve_id)
                next_internal_curve += 1
            else:
                # Reusing the exterior inlet marker makes the visual transition
                # conformal, but Gmsh 4.8 cannot robustly apply BoundaryLayer on
                # a curve adjacent to both the exterior and internal surfaces.
                internal_loop_entries.append(int(marker_entry))
            append_internal_curve(dup_lower, "internal_cavity_lower")
            append_internal_curve(dup_te, "internal_cavity_te")
    else:
        diagnostic_loop_edges = all_edges[
            patch_lower.str.contains("outer_upper_wall|outer_lower_wall|trailing_edge_wall|inlet_opening_marker", case=False, na=False)
        ].copy()
        for _, e in diagnostic_loop_edges.iterrows():
            cid = 1000 + int(e.edge_id)
            lines.append(f"Line({cid}) = {{{int(e.start_point_id)}, {int(e.end_point_id)}}}; // {e.patch_name}")
    base = 200000
    far_lines, far = farfield_geometry_lines(base, domain, mesh_cfg, chord_m)
    lines += far_lines
    exterior_surface = base + 30
    internal_surface = base + 31
    lines.append(f"Line Loop({base+20}) = {{{', '.join(map(str, far))}}};")
    if loop_closed and loop_entries:
        if connected_fluid_surface:
            lines.append(f"Plane Surface({exterior_surface}) = {{{base+20}}};")
            lines.append(f"Line{{{', '.join(map(str, loop_curve_ids))}}} In Surface{{{exterior_surface}}}; // connected open-cavity diagnostic, not a hole")
        else:
            lines.append(f"Line Loop({base+21}) = {{{', '.join(map(str, loop_entries))}}};")
            lines.append(f"Plane Surface({exterior_surface}) = {{{base+20}, {base+21}}};")
        if mesh_internal_cavity and internal_loop_entries:
            lines.append(f"Line Loop({base+22}) = {{{', '.join(map(str, internal_loop_entries))}}};")
            lines.append(f"Plane Surface({internal_surface}) = {{{base+22}}};")
    else:
        lines.append(f"Plane Surface({exterior_surface}) = {{{base+20}}};")
        if loop_curve_ids:
            lines.append(f"Line{{{', '.join(map(str, loop_curve_ids))}}} In Surface{{{exterior_surface}}};")
    open_bl_enabled = bool(mesh_cfg.get("open_diagnostic_boundary_layer_enabled", True))
    if wall_curve_ids:
        request_bl = (
            open_bl_enabled
            and bool(mesh_cfg.get("request_boundary_layer", True))
            and int(mesh_cfg.get("open_boundary_layer_layers", mesh_cfg.get("boundary_layer_layers", 0))) > 0
        )
        if request_bl and loop_closed:
            bl = boundary_layer_parameters(
                mesh_cfg,
                chord_m,
                first_cell_key="open_first_cell_height_chord_override",
                growth_key="open_boundary_layer_growth",
                layers_key="open_boundary_layer_layers",
                thickness_key="open_boundary_layer_total_thickness_chord_override",
                fallback_first_cell_chord=1.0e-5,
            )
            y1 = float(bl["first_cell_height"])
            growth = float(bl["growth"])
            n_layers = int(bl["n_layers"])
            thickness = float(bl["total_thickness"])
            bl_quads = 1 if bool(mesh_cfg.get("recombine_boundary_layer", False)) else 0
            lines += [
                "",
                "// Diagnostic BoundaryLayer field on the open-profile wall curves.",
                "// This is for visual/debug inspection only; it is not an approved OpenFOAM open-cavity mesh.",
                "// BoundaryLayer is applied on a continuous exterior diagnostic loop; the inlet marker is not a physical patch.",
                "Field[1] = BoundaryLayer;",
                f"Field[1].CurvesList = {{{', '.join(map(str, bl_curve_ids or wall_curve_ids))}}};",
                f"Field[1].Size = {y1:.12g};",
                f"Field[1].Ratio = {growth:.12g};",
                f"Field[1].Thickness = {thickness:.12g};",
                f"Field[1].Quads = {bl_quads};",
            ]
            use_lip_fan = bool(mesh_cfg.get("open_boundary_layer_fan_at_lips", True)) and not bool(mesh_cfg.get("open_boundary_layer_trim_end_segments", True))
            if use_lip_fan and upper_seq and lower_seq:
                fan_points = [int(upper_seq[-1]), int(lower_seq[0])]
                fan_size = max(n_layers + 8, int(mesh_cfg.get("open_boundary_layer_lip_fan_points", n_layers + 8)))
                lines += [
                    "// Extra fan resolution at both LE lips to avoid abrupt BL turning at the opening.",
                    f"Field[1].FanPointsList = {{{', '.join(map(str, fan_points))}}};",
                    f"Field[1].FanPointsSizesList = {{{fan_size}, {fan_size}}};",
                ]
            lines.append("BoundaryLayer Field = 1;")
        elif request_bl and not loop_closed:
            lines.append("// BoundaryLayer disabled: diagnostic exterior loop is not closed.")
            request_bl = False
        elif not open_bl_enabled:
            lines.append("// BoundaryLayer disabled for this quick open-profile diagnostic: Gmsh 4.8 edge recovery was not robust on the current zero-thickness open surfaces.")
        lines.append(f"Physical Line(\"airfoil_wall_diagnostic_not_openfoam_ready\") = {{{', '.join(map(str, wall_curve_ids))}}};")
    else:
        request_bl = False

    background_field_ids: list[int] = []
    next_field_id = 2
    inlet_marker_refinement_ids = [abs(int(marker_curve_id))] if loop_closed and bool(mesh_cfg.get("open_inlet_marker_transfinite_enabled", True)) else []
    refinement_curve_ids = list(dict.fromkeys((bl_curve_ids or wall_curve_ids) + inlet_marker_refinement_ids))
    nearfield_requested = bool(mesh_cfg.get("open_nearfield_refinement_enabled", mesh_cfg.get("nearfield_refinement_enabled", False))) and bool(refinement_curve_ids)
    if nearfield_requested:
        dist_field_id = next_field_id
        threshold_field_id = next_field_id + 1
        next_field_id += 2
        dist_min = float(mesh_cfg.get("open_nearfield_dist_min_chord", mesh_cfg.get("nearfield_dist_min_chord", 0.20))) * chord_m
        dist_max = float(mesh_cfg.get("open_nearfield_dist_max_chord", mesh_cfg.get("nearfield_dist_max_chord", 1.10))) * chord_m
        sampling = int(mesh_cfg.get("open_nearfield_distance_sampling") or mesh_cfg.get("nearfield_distance_sampling") or max(160, min(900, len(points) * 3)))
        lines += [
            "",
            "// Smooth open-profile near-field size transition around the exterior wall.",
            f"Field[{dist_field_id}] = Distance;",
            f"Field[{dist_field_id}].CurvesList = {{{', '.join(map(str, refinement_curve_ids))}}};",
            f"Field[{dist_field_id}].NumPointsPerCurve = {sampling};",
            f"Field[{threshold_field_id}] = Threshold;",
            f"Field[{threshold_field_id}].InField = {dist_field_id};",
            f"Field[{threshold_field_id}].SizeMin = lc_airfoil;",
            f"Field[{threshold_field_id}].SizeMax = lc_farfield;",
            f"Field[{threshold_field_id}].DistMin = {dist_min:.12g};",
            f"Field[{threshold_field_id}].DistMax = {dist_max:.12g};",
        ]
        background_field_ids.append(threshold_field_id)

    internal_inlet_refinement_requested = (
        bool(mesh_cfg.get("open_internal_inlet_refinement_enabled", True))
        and bool(mesh_internal_cavity)
        and internal_marker_curve_id is not None
    )
    if internal_inlet_refinement_requested:
        dist_field_id = next_field_id
        threshold_field_id = next_field_id + 1
        next_field_id += 2
        dist_min = float(mesh_cfg.get("open_internal_inlet_dist_min_chord", 0.0)) * chord_m
        dist_max = float(mesh_cfg.get("open_internal_inlet_dist_max_chord", 0.08)) * chord_m
        size_min = float(mesh_cfg.get("open_internal_inlet_size_chord", mesh_cfg.get("open_surface_size_lip_chord", 0.0015))) * chord_m
        sampling = int(mesh_cfg.get("open_internal_inlet_distance_sampling", 140) or 140)
        lines += [
            "",
            "// Smooth internal inlet refinement only; avoids rectangular Box fields in the open debug mesh.",
            f"Field[{dist_field_id}] = Distance;",
            f"Field[{dist_field_id}].CurvesList = {{{internal_marker_curve_id}}};",
            f"Field[{dist_field_id}].NumPointsPerCurve = {sampling};",
            f"Field[{threshold_field_id}] = Threshold;",
            f"Field[{threshold_field_id}].InField = {dist_field_id};",
            f"Field[{threshold_field_id}].SizeMin = {size_min:.12g};",
            f"Field[{threshold_field_id}].SizeMax = lc_cavity;",
            f"Field[{threshold_field_id}].DistMin = {dist_min:.12g};",
            f"Field[{threshold_field_id}].DistMax = {dist_max:.12g};",
        ]
        background_field_ids.append(threshold_field_id)

    le_refinement_requested = bool(mesh_cfg.get("open_le_refinement_enabled", True))
    if le_refinement_requested:
        le_field_id = next_field_id
        next_field_id += 1
        le_extent = max(float(mesh_cfg.get("open_le_refinement_extent_chord", 0.30)) * chord_m, le_size_window)
        le_half_height = max(float(mesh_cfg.get("open_le_refinement_height_chord", 0.18)) * chord_m, 0.5 * (float(points["z_m"].max()) - float(points["z_m"].min())))
        le_transition = max(float(mesh_cfg.get("open_le_refinement_transition_chord", 0.22)) * chord_m, 2.0 * lc_le)
        z_le_mid = 0.5 * (float(points["z_m"].min()) + float(points["z_m"].max()))
        lines += [
            "",
            "// Local LE/opening refinement applied to both exterior and internal diagnostic surfaces.",
            f"Field[{le_field_id}] = Box;",
            f"Field[{le_field_id}].VIn = lc_le;",
            f"Field[{le_field_id}].VOut = lc_farfield;",
            f"Field[{le_field_id}].XMin = {x_le_open - 0.03 * chord_m:.12g};",
            f"Field[{le_field_id}].XMax = {x_le_open + le_extent:.12g};",
            f"Field[{le_field_id}].YMin = {z_le_mid - le_half_height:.12g};",
            f"Field[{le_field_id}].YMax = {z_le_mid + le_half_height:.12g};",
            f"Field[{le_field_id}].Thickness = {le_transition:.12g};",
        ]
        background_field_ids.append(le_field_id)

    lip_refinement_requested = bool(mesh_cfg.get("open_lip_refinement_enabled", True)) and loop_closed and upper_seq and lower_seq
    if lip_refinement_requested:
        rx = max(float(mesh_cfg.get("open_lip_refinement_x_chord", 0.08)) * chord_m, 4.0 * lc_lip)
        rz = max(float(mesh_cfg.get("open_lip_refinement_z_chord", 0.08)) * chord_m, 4.0 * lc_lip)
        transition = max(float(mesh_cfg.get("open_lip_refinement_transition_chord", 0.12)) * chord_m, 2.0 * lc_lip)
        for label, pid in [("upper_lip", int(upper_seq[-1])), ("lower_lip", int(lower_seq[0]))]:
            lip_xy = point_xy(pid)
            lip_field_id = next_field_id
            next_field_id += 1
            lines += [
                "",
                f"// Local refinement around {label} for denser BL/cavity transition near the opening.",
                f"Field[{lip_field_id}] = Box;",
                f"Field[{lip_field_id}].VIn = lc_lip;",
                f"Field[{lip_field_id}].VOut = lc_farfield;",
                f"Field[{lip_field_id}].XMin = {float(lip_xy[0]) - rx:.12g};",
                f"Field[{lip_field_id}].XMax = {float(lip_xy[0]) + rx:.12g};",
                f"Field[{lip_field_id}].YMin = {float(lip_xy[1]) - rz:.12g};",
                f"Field[{lip_field_id}].YMax = {float(lip_xy[1]) + rz:.12g};",
                f"Field[{lip_field_id}].Thickness = {transition:.12g};",
            ]
            background_field_ids.append(lip_field_id)

    if len(background_field_ids) == 1:
        lines.append(f"Background Field = {background_field_ids[0]};")
    elif len(background_field_ids) > 1:
        min_field_id = next_field_id
        lines += [
            "",
            "// Combine open-profile refinement fields by taking the smallest requested local size.",
            f"Field[{min_field_id}] = Min;",
            f"Field[{min_field_id}].FieldsList = {{{', '.join(map(str, background_field_ids))}}};",
            f"Background Field = {min_field_id};",
        ]
    if mesh_internal_cavity and internal_loop_entries:
        lines.append(f"Physical Line(\"internal_cavity_boundary_diagnostic\") = {{{', '.join(map(str, internal_curve_ids))}}};")
    lines.append(f"Physical Line(\"farfield\") = {{{', '.join(map(str, far))}}};")
    fluid_surfaces = [exterior_surface]
    if mesh_internal_cavity and internal_loop_entries:
        fluid_surfaces.append(internal_surface)
    lines.append(f"Physical Surface(\"fluid\") = {{{', '.join(map(str, fluid_surfaces))}}};")
    if mesh_internal_cavity and internal_loop_entries:
        lines.append(f"Physical Surface(\"internal_cavity_diagnostic\") = {{{internal_surface}}};")

    if te_rounding_info.get("open_te_rounding_applied") and te_seq:
        try:
            import matplotlib.pyplot as plt
            wall_ids = list(dict.fromkeys(upper_seq + lower_seq + te_seq))
            wall_xy = np.asarray([point_xy(pid) for pid in wall_ids], dtype=float)
            te_xy = np.asarray([point_xy(pid) for pid in te_seq], dtype=float)
            if len(te_xy) >= 3:
                out_geo.parent.mkdir(parents=True, exist_ok=True)
                fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
                if len(wall_xy):
                    ax.plot(wall_xy[:, 0], wall_xy[:, 1], color="0.78", linewidth=0.8, label="open-profile wall")
                ax.plot(te_xy[:, 0], te_xy[:, 1], marker=".", color="#1f77b4", linewidth=1.3, label="tangent TE cap")
                chord_ref = max(chord_m, float(points["x_m"].max() - points["x_m"].min()), 1.0e-12)
                xpad = max(0.025 * chord_ref, 0.18 * float(te_xy[:, 0].max() - te_xy[:, 0].min()))
                zpad = max(0.010 * chord_ref, 0.25 * float(te_xy[:, 1].max() - te_xy[:, 1].min()))
                ax.set_xlim(float(te_xy[:, 0].min()) - xpad, float(te_xy[:, 0].max()) + xpad)
                ax.set_ylim(float(te_xy[:, 1].min()) - zpad, float(te_xy[:, 1].max()) + zpad)
                ax.set_aspect("equal", adjustable="box")
                ax.grid(True, linewidth=0.3)
                ax.set_xlabel("x [m]")
                ax.set_ylabel("z [m]")
                ax.set_title("Open-profile tangent TE cap zoom")
                ax.legend(loc="best", fontsize=8)
                fig.savefig(out_geo.parent / "open_te_rounding_geometry_zoom.png", dpi=220)
                plt.close(fig)
        except Exception:
            pass

    out_geo.parent.mkdir(parents=True, exist_ok=True)
    out_geo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    physical_groups = ["airfoil_wall_diagnostic_not_openfoam_ready", "farfield", "fluid"]
    if mesh_internal_cavity and internal_loop_entries:
        physical_groups += ["internal_cavity_boundary_diagnostic", "internal_cavity_diagnostic"]
    return {
        "surface_kind": (
            "open_profile_connected_single_fluid_surface_embedded_walls_not_openfoam_ready"
            if connected_fluid_surface
            else (
                "open_profile_external_single_bl_loop_plus_separate_internal_cavity_not_openfoam_ready"
                if single_loop_bspline and mesh_internal_cavity
                else (
                    "open_profile_external_single_bl_loop_not_openfoam_ready"
                    if single_loop_bspline
                    else (
                        "open_profile_diagnostic_exterior_plus_internal_cavity_not_openfoam_ready"
                        if mesh_internal_cavity
                        else "open_profile_diagnostic_embedded_curves_not_openfoam_ready"
                    )
                )
            )
        ),
        "physical_groups": physical_groups,
        "ram_air_inlet_is_physical_patch": False,
        "boundary_layer_requested": bool(request_bl),
        "boundary_layer_layers_requested": int(mesh_cfg.get("open_boundary_layer_layers", mesh_cfg.get("boundary_layer_layers", 0)) or 0) if request_bl else 0,
        "boundary_layer_quads_requested": bool(mesh_cfg.get("recombine_boundary_layer", False)) if request_bl else False,
        "boundary_layer_first_cell_height_chord": float(bl["first_cell_height_chord"]) if request_bl else 0.0,
        "boundary_layer_requested_first_cell_height_chord": float(bl["requested_first_cell_height_chord"]) if request_bl else 0.0,
        "boundary_layer_total_thickness_chord": float(bl["total_thickness_chord"]) if request_bl else 0.0,
        "boundary_layer_raw_total_thickness_chord": float(bl["raw_total_thickness_chord"]) if request_bl else 0.0,
        "boundary_layer_total_thickness_limited": bool(bl["total_thickness_limited"]) if request_bl else False,
        "nearfield_refinement_requested": bool(nearfield_requested),
        "nearfield_dist_min_chord": float(mesh_cfg.get("open_nearfield_dist_min_chord", mesh_cfg.get("nearfield_dist_min_chord", 0.0)) or 0.0),
        "nearfield_dist_max_chord": float(mesh_cfg.get("open_nearfield_dist_max_chord", mesh_cfg.get("nearfield_dist_max_chord", 0.0)) or 0.0),
        "open_le_refinement_requested": bool(le_refinement_requested),
        "open_le_refinement_width_chord": float(mesh_cfg.get("open_le_refinement_width_chord", 0.0) or 0.0),
        "open_lip_refinement_requested": bool(lip_refinement_requested),
        "open_internal_inlet_refinement_requested": bool(internal_inlet_refinement_requested),
        "open_internal_inlet_refinement_kind": "Distance/Threshold on duplicated internal inlet marker" if internal_inlet_refinement_requested else None,
        "open_surface_size_general_chord": float(mesh_cfg.get("open_surface_size_general_chord", 0.0) or 0.0),
        "open_surface_size_le_chord": float(mesh_cfg.get("open_surface_size_le_chord", 0.0) or 0.0),
        "open_surface_size_lip_chord": float(mesh_cfg.get("open_surface_size_lip_chord", 0.0) or 0.0),
        "open_cavity_size_chord": float(mesh_cfg.get("open_cavity_size_chord", 0.0) or 0.0),
        "open_farfield_size_chord": float(mesh_cfg.get("open_farfield_size_chord", mesh_cfg.get("farfield_size_chord", 0.0)) or 0.0),
        "open_boundary_layer_fan_at_lips": bool(mesh_cfg.get("open_boundary_layer_fan_at_lips", False)) and not bool(mesh_cfg.get("open_boundary_layer_trim_end_segments", True)) if request_bl else False,
        "open_boundary_layer_lip_fan_points": int(mesh_cfg.get("open_boundary_layer_lip_fan_points", 0) or 0) if request_bl else 0,
        "open_fluid_topology": (
            "connected_single_surface_with_embedded_walls"
            if connected_fluid_surface
            else (
                "external_single_bl_loop_plus_separate_internal_cavity_surfaces"
                if single_loop_bspline and mesh_internal_cavity
                else (
                    "external_single_loop_boundary_layer_no_internal_cavity"
                    if single_loop_bspline
                    else ("separate_exterior_and_internal_diagnostic_surfaces" if mesh_internal_cavity else "exterior_surface_only")
                )
            )
        ),
        "open_connected_fluid_surface": bool(connected_fluid_surface),
        "open_boundary_layer_single_loop_bspline": bool(single_loop_bspline),
        "open_boundary_layer_single_loop_curve_kind": str(single_loop_curve_kind),
        "open_boundary_layer_single_loop_transfinite": bool(single_loop_transfinite),
        "open_internal_cavity_curve_mode": str(internal_cavity_curve_mode),
        "open_boundary_layer_curve_policy": (
            f"single_closed_{single_loop_curve_kind.lower()}_loop_including_inlet_bridge_and_te"
            if single_loop_bspline
            else ("continuous_exterior_loop_with_nonphysical_inlet_marker" if bool(mesh_cfg.get("open_boundary_layer_include_inlet_marker", False)) else "exterior_wall_curves_only_inlet_marker_transfinite_refined")
        ),
        "open_diagnostic_loop_closed": bool(loop_closed),
        "open_diagnostic_loop_curve_count": int(len(loop_curve_ids)),
        "open_boundary_layer_curve_count": int(len(bl_curve_ids or wall_curve_ids)),
        "open_diagnostic_boundary_layer_enabled": bool(open_bl_enabled),
        "open_inlet_marker_curve_ids": inlet_marker_curve_ids,
        "open_boundary_layer_inlet_marker_included": bool(bool(mesh_cfg.get("open_boundary_layer_include_inlet_marker", False)) and loop_closed and not single_loop_bspline),
        "open_boundary_layer_inlet_bridge_in_single_loop": bool(single_loop_bspline and bl_loop_seq),
        "open_inlet_marker_transfinite_enabled": bool(bool(mesh_cfg.get("open_inlet_marker_transfinite_enabled", True)) and loop_closed),
        "open_inlet_marker_transfinite_nodes": int(mesh_cfg.get("open_inlet_marker_transfinite_nodes", 0) or 0) if loop_closed else 0,
        "open_internal_cavity_meshed": bool(mesh_internal_cavity and internal_loop_entries),
        "open_internal_cavity_curve_count": int(len(internal_loop_entries)),
        "open_internal_cavity_shares_inlet_marker": bool(mesh_internal_cavity and internal_loop_entries and loop_closed and internal_marker_curve_id is None),
        "open_internal_cavity_duplicate_inlet_marker": bool(internal_marker_curve_id is not None),
        "open_internal_cavity_solver_connected": bool(connected_fluid_surface),
        "open_internal_cavity_note": (
            "Separate diagnostic internal triangles; not a solver-connected OpenFOAM cavity because Gmsh BoundaryLayer cannot be robustly applied on zero-thickness wall/inlet curves shared by exterior and interior surfaces in Gmsh 4.8."
            if mesh_internal_cavity and not connected_fluid_surface
            else None
        ),
        "open_wall_curve_ids": wall_curve_ids,
        "open_boundary_layer_curve_ids": bl_curve_ids if request_bl else [],
        "open_boundary_layer_excluded_te_curve_ids": open_boundary_layer_excluded_te_curve_ids if request_bl and loop_closed else [],
        "open_boundary_layer_exclude_te_cap_from_bl": bool(mesh_cfg.get("open_boundary_layer_exclude_te_cap_from_bl", True)),
        "open_boundary_layer_trim_end_segments": bool(mesh_cfg.get("open_boundary_layer_trim_end_segments", True)),
        "open_boundary_layer_trim_ends_chord": float(mesh_cfg.get("open_boundary_layer_trim_ends_chord", 0.0) or 0.0),
        **te_rounding_info,
        "diagnostic_only": True,
        "openfoam_ready": False,
    }


def _stat_block(values: list[float], prefix: str) -> dict[str, float | None]:
    if not values:
        return {
            f"{prefix}_min": None,
            f"{prefix}_p05": None,
            f"{prefix}_mean": None,
            f"{prefix}_p95": None,
            f"{prefix}_max": None,
        }
    arr = np.asarray(values, dtype=float)
    return {
        f"{prefix}_min": float(np.min(arr)),
        f"{prefix}_p05": float(np.percentile(arr, 5)),
        f"{prefix}_mean": float(np.mean(arr)),
        f"{prefix}_p95": float(np.percentile(arr, 95)),
        f"{prefix}_max": float(np.max(arr)),
    }


def _poly_area_2d(pts: list[np.ndarray]) -> float:
    if len(pts) < 3:
        return 0.0
    area = 0.0
    for p1, p2 in zip(pts, pts[1:] + pts[:1]):
        area += float(p1[0] * p2[1] - p2[0] * p1[1])
    return 0.5 * abs(area)


def _tri_area_3d(pa: np.ndarray, pb: np.ndarray, pc: np.ndarray) -> float:
    return 0.5 * float(np.linalg.norm(np.cross(pb - pa, pc - pa)))


def _poly_area_3d(pts: list[np.ndarray]) -> float:
    if len(pts) < 3:
        return 0.0
    area = 0.0
    p0 = pts[0]
    for i in range(1, len(pts) - 1):
        area += _tri_area_3d(p0, pts[i], pts[i + 1])
    return float(area)


def _poly_angles_deg(pts: list[np.ndarray]) -> list[float]:
    angles = []
    n = len(pts)
    if n < 3:
        return angles
    for i in range(n):
        prev_pt = pts[(i - 1) % n]
        cur_pt = pts[i]
        next_pt = pts[(i + 1) % n]
        v1 = prev_pt - cur_pt
        v2 = next_pt - cur_pt
        denom = max(float(np.linalg.norm(v1) * np.linalg.norm(v2)), 1e-30)
        cosv = float(np.dot(v1, v2)) / denom
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return angles


def merge_coincident_msh2_nodes(mesh_path: Path, tolerance: float) -> dict[str, Any]:
    """Merge coincident nodes in an ASCII MSH2 mesh and update connectivity.

    The open thin-solid topology duplicates only the non-physical inlet
    interface curve so Gmsh 4.8 can apply a one-sided BoundaryLayer field.
    Both copies are transfinite with identical coordinates; merging their mesh
    nodes restores a conformal fluid interface before extrusion/OpenFOAM.
    """
    path = Path(mesh_path)
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    try:
        nodes_start = lines.index("$Nodes")
        nodes_end = lines.index("$EndNodes", nodes_start)
        elements_start = lines.index("$Elements")
        elements_end = lines.index("$EndElements", elements_start)
    except ValueError as exc:
        raise ValueError(f"Cannot merge nodes: invalid ASCII MSH2 sections in {path}") from exc

    n_nodes = int(lines[nodes_start + 1])
    node_rows = lines[nodes_start + 2 : nodes_start + 2 + n_nodes]
    tol = max(float(tolerance), 1.0e-15)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    canonical_xyz: dict[int, tuple[float, float, float]] = {}
    node_map: dict[int, int] = {}
    duplicate_distances: list[float] = []

    for row in node_rows:
        parts = row.split()
        nid = int(parts[0])
        xyz = (float(parts[1]), float(parts[2]), float(parts[3]))
        key = tuple(int(round(v / tol)) for v in xyz)
        canonical = None
        for candidate in buckets.get(key, []):
            distance = math.dist(xyz, canonical_xyz[candidate])
            if distance <= tol:
                canonical = candidate
                duplicate_distances.append(distance)
                break
        if canonical is None:
            canonical = nid
            canonical_xyz[nid] = xyz
            buckets.setdefault(key, []).append(nid)
        node_map[nid] = canonical

    merged_count = sum(1 for nid, canonical in node_map.items() if nid != canonical)
    if merged_count == 0:
        return {
            "coincident_interface_nodes_merged": 0,
            "coincident_interface_merge_tolerance": tol,
            "coincident_interface_merge_max_distance": 0.0,
        }

    new_node_rows = [
        f"{nid} {xyz[0]:.16g} {xyz[1]:.16g} {xyz[2]:.16g}"
        for nid, xyz in canonical_xyz.items()
    ]
    n_elements = int(lines[elements_start + 1])
    element_rows = lines[elements_start + 2 : elements_start + 2 + n_elements]
    new_element_rows: list[str] = []
    collapsed_elements = 0
    for row in element_rows:
        parts = row.split()
        element_type = int(parts[1])
        n_tags = int(parts[2])
        conn_start = 3 + n_tags
        mapped = [node_map[int(value)] for value in parts[conn_start:]]
        if element_type in {2, 3, 4, 5, 6, 7} and len(set(mapped)) != len(mapped):
            collapsed_elements += 1
        new_element_rows.append(" ".join(parts[:conn_start] + [str(value) for value in mapped]))
    if collapsed_elements:
        raise RuntimeError(
            f"Coincident interface merge would collapse {collapsed_elements} mesh elements; refusing to continue."
        )

    rebuilt = (
        lines[: nodes_start + 1]
        + [str(len(new_node_rows))]
        + new_node_rows
        + lines[nodes_end : elements_start + 1]
        + [str(len(new_element_rows))]
        + new_element_rows
        + lines[elements_end:]
    )
    temp_path = path.with_suffix(path.suffix + ".merge_tmp")
    temp_path.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")
    os.replace(temp_path, path)
    return {
        "coincident_interface_nodes_merged": int(merged_count),
        "coincident_interface_merge_tolerance": tol,
        "coincident_interface_merge_max_distance": float(max(duplicate_distances, default=0.0)),
        "coincident_interface_nodes_after_merge": int(len(canonical_xyz)),
    }


def merge_named_msh2_interface_nodes(
    mesh_path: Path,
    interface_names: Iterable[str],
    tolerance: float,
) -> dict[str, Any]:
    """Stitch only named duplicate inlet curves in an ASCII MSH2 mesh.

    The zero-thickness wall also contains coincident external/internal nodes,
    but those must remain separate to become the two faces of a wall baffle.
    Temporary inlet line elements and their PhysicalNames are removed after
    their nodes have been stitched, so they cannot become an OpenFOAM patch.
    """
    path = Path(mesh_path)
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    required_names = {str(name) for name in interface_names}
    if len(required_names) < 2:
        raise ValueError("Selective inlet stitching requires both external and internal interface names.")
    try:
        physical_start = lines.index("$PhysicalNames")
        physical_end = lines.index("$EndPhysicalNames", physical_start)
        nodes_start = lines.index("$Nodes")
        nodes_end = lines.index("$EndNodes", nodes_start)
        elements_start = lines.index("$Elements")
        elements_end = lines.index("$EndElements", elements_start)
    except ValueError as exc:
        raise ValueError(f"Cannot stitch named interfaces: invalid ASCII MSH2 sections in {path}") from exc

    physical_count = int(lines[physical_start + 1])
    physical_rows = lines[physical_start + 2 : physical_start + 2 + physical_count]
    interface_tags: set[int] = set()
    kept_physical_rows: list[str] = []
    found_names: set[str] = set()
    for row in physical_rows:
        match = re.match(r'\s*(\d+)\s+(\d+)\s+"(.*)"\s*$', row)
        if not match:
            kept_physical_rows.append(row)
            continue
        dimension, tag, name = int(match.group(1)), int(match.group(2)), match.group(3)
        if dimension == 1 and name in required_names:
            interface_tags.add(tag)
            found_names.add(name)
        else:
            kept_physical_rows.append(row)
    missing_names = required_names.difference(found_names)
    if missing_names:
        raise RuntimeError(f"Named inlet interface PhysicalNames are missing: {sorted(missing_names)}")

    n_nodes = int(lines[nodes_start + 1])
    node_rows = lines[nodes_start + 2 : nodes_start + 2 + n_nodes]
    node_xyz: dict[int, tuple[float, float, float]] = {}
    for row in node_rows:
        parts = row.split()
        node_xyz[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))

    n_elements = int(lines[elements_start + 1])
    element_rows = lines[elements_start + 2 : elements_start + 2 + n_elements]
    interface_nodes: set[int] = set()
    retained_elements: list[tuple[list[str], int, list[int]]] = []
    removed_interface_elements = 0
    for row in element_rows:
        parts = row.split()
        element_type = int(parts[1])
        n_tags = int(parts[2])
        tags = [int(value) for value in parts[3 : 3 + n_tags]]
        conn_start = 3 + n_tags
        conn = [int(value) for value in parts[conn_start:]]
        physical_tag = tags[0] if tags else 0
        if element_type == 1 and physical_tag in interface_tags:
            interface_nodes.update(conn)
            removed_interface_elements += 1
            continue
        retained_elements.append((parts[:conn_start], element_type, conn))
    if not interface_nodes:
        raise RuntimeError("The named inlet interfaces contain no line-element nodes.")

    tol = max(float(tolerance), 1.0e-15)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    node_map = {node_id: node_id for node_id in node_xyz}
    duplicate_distances: list[float] = []
    for node_id in sorted(interface_nodes):
        xyz = node_xyz[node_id]
        key = tuple(int(round(value / tol)) for value in xyz)
        canonical = None
        for candidate in buckets.get(key, []):
            distance = math.dist(xyz, node_xyz[candidate])
            if distance <= tol:
                canonical = candidate
                duplicate_distances.append(distance)
                break
        if canonical is None:
            buckets.setdefault(key, []).append(node_id)
        else:
            node_map[node_id] = canonical
    merged_count = sum(node_map[node_id] != node_id for node_id in interface_nodes)
    if merged_count == 0:
        raise RuntimeError(
            "The named inlet curves did not produce coincident nodes. "
            "Use equal Transfinite node counts and distributions on both copies."
        )

    referenced_nodes: set[int] = set()
    new_element_rows: list[str] = []
    collapsed_elements = 0
    for prefix, element_type, conn in retained_elements:
        mapped = [node_map[node_id] for node_id in conn]
        if element_type in {2, 3, 4, 5, 6, 7} and len(set(mapped)) != len(mapped):
            collapsed_elements += 1
        referenced_nodes.update(mapped)
        new_element_rows.append(" ".join(prefix + [str(value) for value in mapped]))
    if collapsed_elements:
        raise RuntimeError(
            f"Selective inlet stitching would collapse {collapsed_elements} surface/volume elements."
        )
    new_node_rows = [
        f"{node_id} {node_xyz[node_id][0]:.16g} {node_xyz[node_id][1]:.16g} {node_xyz[node_id][2]:.16g}"
        for node_id in sorted(referenced_nodes)
    ]
    rebuilt = (
        lines[: physical_start + 1]
        + [str(len(kept_physical_rows))]
        + kept_physical_rows
        + lines[physical_end : nodes_start + 1]
        + [str(len(new_node_rows))]
        + new_node_rows
        + lines[nodes_end : elements_start + 1]
        + [str(len(new_element_rows))]
        + new_element_rows
        + lines[elements_end:]
    )
    temporary = path.with_suffix(path.suffix + ".interface_tmp")
    temporary.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return {
        "coincident_interface_nodes_merged": int(merged_count),
        "coincident_interface_merge_method": "named_inlet_only",
        "coincident_interface_names": sorted(required_names),
        "coincident_interface_line_elements_removed": int(removed_interface_elements),
        "coincident_interface_merge_tolerance": tol,
        "coincident_interface_merge_max_distance": float(max(duplicate_distances, default=0.0)),
        "coincident_interface_nodes_after_merge": int(len(referenced_nodes)),
        "zero_thickness_wall_nodes_preserved_separate": True,
    }


def extrude_msh2_surface_one_cell(
    source_2d: Path,
    output_3d: Path,
    span: float,
) -> dict[str, Any]:
    """Extrude an ASCII MSH2 surface mesh into one conformal volume layer.

    Gmsh 4.8 can lose synthetic BoundaryLayer vertices when geometrically
    extruding the partitioned open profile. This mesh-level extrusion preserves
    the exact 2D node connectivity: triangles become prisms, quads become
    hexahedra, physical boundary lines become lateral quads, and every 2D cell
    contributes one front and one back face.
    """
    source_2d = Path(source_2d)
    output_3d = Path(output_3d)
    if span <= 0.0:
        raise ValueError("The one-cell extrusion span must be positive.")
    lines = source_2d.read_text(encoding="utf-8", errors="strict").splitlines()
    if "$Nodes" not in lines or "$Elements" not in lines:
        raise ValueError(f"Not an ASCII MSH2 mesh: {source_2d}")

    physical_names: dict[tuple[int, int], str] = {}
    if "$PhysicalNames" in lines:
        p0 = lines.index("$PhysicalNames")
        count = int(lines[p0 + 1])
        for row in lines[p0 + 2 : p0 + 2 + count]:
            match = re.match(r"\s*(\d+)\s+(\d+)\s+\"(.*)\"\s*$", row)
            if match:
                physical_names[(int(match.group(1)), int(match.group(2)))] = match.group(3)

    n0 = lines.index("$Nodes")
    node_count = int(lines[n0 + 1])
    nodes: dict[int, tuple[float, float, float]] = {}
    for row in lines[n0 + 2 : n0 + 2 + node_count]:
        parts = row.split()
        nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
    if not nodes:
        raise ValueError("The 2D mesh contains no nodes.")

    e0 = lines.index("$Elements")
    element_count = int(lines[e0 + 1])
    boundary_lines: list[tuple[str, list[int]]] = []
    surface_cells: list[list[int]] = []
    for row in lines[e0 + 2 : e0 + 2 + element_count]:
        parts = row.split()
        etype = int(parts[1])
        n_tags = int(parts[2])
        tags = [int(v) for v in parts[3 : 3 + n_tags]]
        conn = [int(v) for v in parts[3 + n_tags :]]
        physical_tag = tags[0] if tags else 0
        if etype == 1 and len(conn) >= 2:
            name = physical_names.get((1, physical_tag))
            if name:
                boundary_lines.append((name, conn[:2]))
        elif etype == 2 and len(conn) >= 3:
            surface_cells.append(conn[:3])
        elif etype == 3 and len(conn) >= 4:
            surface_cells.append(conn[:4])
    if not surface_cells:
        raise ValueError("The 2D mesh contains no triangle or quadrilateral surface cells.")

    xs = [xyz[0] for xyz in nodes.values()]
    ys = [xyz[1] for xyz in nodes.values()]
    planar_extent = max(max(xs) - min(xs), max(ys) - min(ys), 1.0e-12)
    collapsed_area_tolerance = max(1.0e-30, 1.0e-14 * planar_extent * planar_extent)

    def planar_area(conn: list[int]) -> float:
        xy = np.asarray([[nodes[n][0], nodes[n][1]] for n in conn], dtype=float)
        return 0.5 * abs(float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1])))

    detected_collapsed_cells = sum(
        planar_area(conn) <= collapsed_area_tolerance for conn in surface_cells
    )
    # Do not delete or contract these cells here: either operation opens the
    # manifold. They remain an explicit quality failure for checkMesh while the
    # mesh retains closed, one-region topology for diagnosis.
    discarded_collapsed_cells = 0
    collapsed_node_merges = 0

    max_node = max(nodes)
    top_id = {nid: max_node + nid for nid in nodes}
    boundary_names = sorted({name for name, _ in boundary_lines})
    if "airfoil_wall" in boundary_names:
        boundary_names.remove("airfoil_wall")
        boundary_names.insert(0, "airfoil_wall")
    if "farfield" in boundary_names:
        boundary_names.remove("farfield")
        boundary_names.append("farfield")
    surface_tags = {name: i + 1 for i, name in enumerate(boundary_names)}
    front_back_tag = len(surface_tags) + 1
    fluid_tag = 1

    def positive_conn(conn: list[int]) -> list[int]:
        xy = np.asarray([[nodes[n][0], nodes[n][1]] for n in conn], dtype=float)
        area2 = float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))
        return conn if area2 > 0.0 else list(reversed(conn))

    out_elements: list[tuple[int, int, int, list[int]]] = []
    eid = 1
    volume_cells = 0
    prism_cells = 0
    hex_cells = 0
    for raw_conn in surface_cells:
        conn = positive_conn(raw_conn)
        top = [top_id[n] for n in conn]
        if len(conn) == 3:
            out_elements.append((eid, 2, front_back_tag, [conn[0], conn[2], conn[1]])); eid += 1
            out_elements.append((eid, 2, front_back_tag, top)); eid += 1
            out_elements.append((eid, 6, fluid_tag, conn + top)); eid += 1
            prism_cells += 1
        else:
            out_elements.append((eid, 3, front_back_tag, [conn[0], conn[3], conn[2], conn[1]])); eid += 1
            out_elements.append((eid, 3, front_back_tag, top)); eid += 1
            out_elements.append((eid, 5, fluid_tag, conn + top)); eid += 1
            hex_cells += 1
        volume_cells += 1

    lateral_faces = 0
    for name, conn in boundary_lines:
        tag = surface_tags[name]
        a, b = conn
        out_elements.append((eid, 3, tag, [a, b, top_id[b], top_id[a]]))
        eid += 1
        lateral_faces += 1

    output_lines = [
        "$MeshFormat",
        "2.2 0 8",
        "$EndMeshFormat",
        "$PhysicalNames",
        str(len(surface_tags) + 2),
    ]
    for name, tag in surface_tags.items():
        output_lines.append(f'2 {tag} "{name}"')
    output_lines += [
        f'2 {front_back_tag} "frontAndBack"',
        f'3 {fluid_tag} "fluid"',
        "$EndPhysicalNames",
        "$Nodes",
        str(2 * len(nodes)),
    ]
    for nid, (x, y, z) in nodes.items():
        output_lines.append(f"{nid} {x:.16g} {y:.16g} {z:.16g}")
    for nid, (x, y, z) in nodes.items():
        output_lines.append(f"{top_id[nid]} {x:.16g} {y:.16g} {z + span:.16g}")
    output_lines += ["$EndNodes", "$Elements", str(len(out_elements))]
    for element_id, etype, physical_tag, conn in out_elements:
        # Two tags: physical entity and a stable geometrical entity.
        output_lines.append(
            f"{element_id} {etype} 2 {physical_tag} {physical_tag} " + " ".join(map(str, conn))
        )
    output_lines += ["$EndElements", ""]
    output_3d.write_text("\n".join(output_lines), encoding="utf-8")
    return {
        "mesh_level_extrusion_method": "python_msh2_one_cell_connectivity_preserving",
        "mesh_level_extrusion_source_nodes": int(len(nodes)),
        "mesh_level_extrusion_surface_cells": int(len(surface_cells)),
        "mesh_level_extrusion_discarded_collapsed_2d_cells": int(discarded_collapsed_cells),
        "mesh_level_extrusion_collapsed_node_merges": int(collapsed_node_merges),
        "mesh_level_extrusion_detected_collapsed_2d_cells": int(detected_collapsed_cells),
        "mesh_level_extrusion_collapsed_area_tolerance": float(collapsed_area_tolerance),
        "mesh_level_extrusion_volume_cells": int(volume_cells),
        "mesh_level_extrusion_prisms": int(prism_cells),
        "mesh_level_extrusion_hexes": int(hex_cells),
        "mesh_level_extrusion_lateral_boundary_faces": int(lateral_faces),
        "mesh_level_extrusion_span": float(span),
    }


def parse_msh_v2(msh_path: Path) -> dict[str, Any]:
    text = Path(msh_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    nodes: dict[int, tuple[float, float, float]] = {}
    tris: list[tuple[int, int, int, int]] = []
    quads: list[tuple[int, int, int, int]] = []
    tets = 0
    hexes = 0
    prisms = 0
    pyramids = 0
    i = 0
    while i < len(text):
        if text[i].strip() == "$Nodes":
            n = int(text[i+1].strip())
            for j in range(n):
                parts = text[i+2+j].split()
                nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
            i += n + 3
            continue
        if text[i].strip() == "$Elements":
            n = int(text[i+1].strip())
            for j in range(n):
                parts = text[i+2+j].split()
                element_id = int(parts[0])
                etype = int(parts[1]); ntags = int(parts[2]); conn = list(map(int, parts[3+ntags:]))
                if etype == 2 and len(conn) >= 3:
                    tris.append((element_id, *conn[:3]))
                elif etype == 3 and len(conn) >= 4:
                    quads.append(tuple(conn[:4]))
                elif etype == 4:
                    tets += 1
                elif etype == 5:
                    hexes += 1
                elif etype == 6:
                    prisms += 1
                elif etype == 7:
                    pyramids += 1
            i += n + 3
            continue
        i += 1
    min_q = None; min_ang = None; max_ang = None; positive = True
    triangle_qualities: list[float] = []
    triangle_areas: list[float] = []
    triangle_aspects: list[float] = []
    triangle_skews: list[float] = []
    surface_areas: list[float] = []
    surface_aspects: list[float] = []
    surface_edge_lengths: list[float] = []
    edge_to_areas: dict[tuple[int, int], list[float]] = {}
    low_quality_triangles: list[dict[str, Any]] = []

    def add_surface_edges(conn: tuple[int, ...], area_value: float) -> None:
        ids = list(conn)
        for a, b in zip(ids, ids[1:] + ids[:1]):
            key = tuple(sorted((a, b)))
            edge_to_areas.setdefault(key, []).append(area_value)

    for element_id, a, b, c in tris:
        pa = np.array(nodes[a]); pb = np.array(nodes[b]); pc = np.array(nodes[c])
        la = np.linalg.norm(pb-pc); lb = np.linalg.norm(pa-pc); lc = np.linalg.norm(pa-pb)
        area = _tri_area_3d(pa, pb, pc)
        if area <= 0: positive = False
        q = 4*math.sqrt(3)*area / max(la*la+lb*lb+lc*lc, 1e-30)
        triangle_qualities.append(float(q))
        triangle_areas.append(float(area))
        surface_areas.append(float(area))
        surface_edge_lengths.extend([float(la), float(lb), float(lc)])
        triangle_aspects.append(float(max(la, lb, lc) / max(min(la, lb, lc), 1e-30)))
        if q < 0.02:
            centroid = (pa + pb + pc) / 3.0
            low_quality_triangles.append({
                "element_id": int(element_id),
                "node_ids": [int(a), int(b), int(c)],
                "quality": float(q),
                "area": float(area),
                "centroid": [float(value) for value in centroid],
                "edge_lengths": [float(la), float(lb), float(lc)],
            })
        min_q = q if min_q is None else min(min_q, q)
        sides = [la, lb, lc]
        angles = []
        for s_op, s1, s2 in [(la, lb, lc), (lb, la, lc), (lc, la, lb)]:
            cosv = (s1*s1 + s2*s2 - s_op*s_op) / max(2*s1*s2, 1e-30)
            angles.append(math.degrees(math.acos(max(-1,min(1,cosv)))))
        triangle_skews.append(float(max((max(angles) - 60.0) / 120.0, (60.0 - min(angles)) / 60.0, 0.0)))
        min_ang = min(angles) if min_ang is None else min(min_ang, min(angles))
        max_ang = max(angles) if max_ang is None else max(max_ang, max(angles))
        add_surface_edges((a, b, c), float(area))
    quad_skews: list[float] = []
    for a, b, c, d in quads:
        pts = [np.array(nodes[n]) for n in (a, b, c, d)]
        lengths = [float(np.linalg.norm(p2 - p1)) for p1, p2 in zip(pts, pts[1:] + pts[:1])]
        area = _poly_area_3d(pts)
        angles = _poly_angles_deg(pts)
        if area <= 0:
            positive = False
        surface_areas.append(float(area))
        surface_edge_lengths.extend(lengths)
        surface_aspects.append(float(max(lengths) / max(min(lengths), 1e-30)))
        if angles:
            quad_skews.append(float(max(abs(a_deg - 90.0) for a_deg in angles) / 90.0))
        add_surface_edges((a, b, c, d), float(area))
    neighbor_area_ratios = []
    for areas in edge_to_areas.values():
        if len(areas) == 2 and min(areas) > 0:
            neighbor_area_ratios.append(float(max(areas) / min(areas)))
    volume_cells = tets + hexes + prisms + pyramids
    surface_cells = len(tris) + len(quads)
    report = {
        "number_of_nodes": len(nodes),
        "number_of_triangles": len(tris),
        "number_of_quads": len(quads),
        "number_of_tets": tets,
        "number_of_hexes": hexes,
        "number_of_prisms": prisms,
        "number_of_pyramids": pyramids,
        "number_of_volume_cells": volume_cells,
        "number_of_surface_cells": surface_cells,
        "estimated_cell_count": volume_cells if volume_cells else surface_cells,
        "min_element_quality": min_q,
        "min_triangle_angle": min_ang,
        "max_triangle_angle": max_ang,
        "positive_areas": positive,
        "triangle_quality_below_0p02_count": int(len(low_quality_triangles)),
        "worst_triangles": sorted(low_quality_triangles, key=lambda item: item["quality"])[:20],
    }
    report.update(_stat_block(triangle_qualities, "triangle_quality"))
    report.update(_stat_block(triangle_areas, "triangle_area"))
    report.update(_stat_block(triangle_aspects, "triangle_aspect_ratio"))
    report.update(_stat_block(triangle_skews, "triangle_equiangle_skewness"))
    report.update(_stat_block(quad_skews, "quad_angle_skewness"))
    report.update(_stat_block(surface_edge_lengths, "surface_edge_length"))
    report.update(_stat_block(neighbor_area_ratios, "surface_neighbor_area_ratio"))
    return report


def _stop_process_group(process: subprocess.Popen[str], grace_s: float = 5.0) -> None:
    """Stop a command and its Gmsh worker children after timeout or Ctrl+C."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=max(0.1, grace_s))
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_command(cmd: list[str], cwd: Path, timeout_s: int = 300) -> tuple[int, str]:
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    try:
        stdout, _ = process.communicate(timeout=timeout_s)
        return int(process.returncode or 0), stdout or ""
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        _stop_process_group(process)
        remaining, _ = process.communicate()
        output = partial if partial else (remaining or "")
        if remaining and remaining not in output:
            output += remaining
        return 124, f"{output}\nTIMEOUT: Command '{' '.join(cmd)}' timed out after {timeout_s} seconds.\n"
    except KeyboardInterrupt:
        _stop_process_group(process)
        remaining, _ = process.communicate()
        return 130, (
            f"{remaining or ''}\nCANCELLED: Gmsh was stopped by the user (Ctrl+C). "
            "Partial output has been retained; no previous mesh is presented as the new result.\n"
        )


def configure_gmsh_api_threads(gmsh_module: Any, gmsh_threads: int) -> None:
    """Configure Gmsh Python API thread options if an API mesher is introduced."""
    n = max(1, int(gmsh_threads))
    gmsh_module.option.setNumber("General.NumThreads", n)
    gmsh_module.option.setNumber("Mesh.MaxNumThreads1D", n)
    gmsh_module.option.setNumber("Mesh.MaxNumThreads2D", n)
    gmsh_module.option.setNumber("Mesh.MaxNumThreads3D", n)


def is_windows_mounted_wsl_path(path: Path) -> bool:
    text = str(path.resolve() if path.exists() else path).replace("\\", "/")
    return text.startswith("/mnt/c/") or text.startswith("/mnt/d/")


def resolve_gmsh_threads(args: argparse.Namespace, mesh_cfg: dict[str, Any]) -> int:
    requested = args.gmsh_threads if getattr(args, "gmsh_threads", None) is not None else mesh_cfg.get("gmsh_threads")
    if requested is None:
        requested = max(1, min(12, os.cpu_count() or 1))
    return max(1, min(16, int(requested)))


def resolve_gmsh_executable(args: argparse.Namespace) -> str | None:
    """Resolve Gmsh explicitly before falling back to PATH."""
    requested = getattr(args, "gmsh_executable", None) or os.environ.get("RAMAIR_GMSH_EXECUTABLE")
    candidates: list[Path] = []
    if requested:
        candidates.append(Path(str(requested)).expanduser())
    candidates += [
        Path.home() / ".local" / "opt" / "gmsh-4.15.2" / "bin" / "gmsh",
        Path.home() / ".local" / "bin" / "gmsh",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return shutil.which("gmsh")


def gmsh_version_info(gmsh_executable: str) -> dict[str, Any]:
    code, output = run_command([gmsh_executable, "--version"], Path.cwd(), timeout_s=15)
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", output or "")
    version_tuple = tuple(int(value or 0) for value in match.groups()) if match else None
    return {
        "gmsh_version_command_exit_code": int(code),
        "gmsh_version": match.group(0) if match else None,
        "gmsh_version_tuple": list(version_tuple) if version_tuple else None,
        "gmsh_version_output": (output or "").strip()[:500],
    }


def gmsh_python_worker() -> Path:
    return Path(__file__).resolve().parents[1] / "app" / "gmsh_python_runner.py"


def gmsh_api_version_info() -> dict[str, Any]:
    worker = gmsh_python_worker()
    code, output = run_command([sys.executable, str(worker), "--version-only"], Path.cwd(), timeout_s=20)
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", output or "")
    version_tuple = tuple(int(value or 0) for value in match.groups()) if match else None
    return {
        "gmsh_version_command_exit_code": int(code),
        "gmsh_version": match.group(0) if match else None,
        "gmsh_version_tuple": list(version_tuple) if version_tuple else None,
        "gmsh_version_output": (output or "").strip()[:500],
        "gmsh_python_worker": str(worker),
    }


def resolve_gmsh_backend(args: argparse.Namespace, mesh_cfg: dict[str, Any]) -> str:
    requested = str(getattr(args, "gmsh_backend", None) or mesh_cfg.get("gmsh_backend", "auto")).strip().lower()
    if requested not in {"auto", "cli", "python_api"}:
        raise ValueError(f"Unsupported Gmsh backend: {requested}")
    if requested == "auto":
        api = gmsh_api_version_info()
        return "python_api" if api.get("gmsh_version_command_exit_code") == 0 and api.get("gmsh_version") else "cli"
    return requested


def gmsh_api_command(
    geo_name: str,
    mesh_name: str,
    dimension: int,
    threads: int,
    report_name: str = "gmsh_python_api_report.json",
) -> list[str]:
    return [
        sys.executable,
        str(gmsh_python_worker()),
        "--geo", geo_name,
        "--output", mesh_name,
        "--dimension", str(int(dimension)),
        "--threads", str(int(threads)),
        "--msh-version", "2.2",
        "--report", report_name,
    ]


def parse_thread_sweep(spec: str) -> list[int]:
    values: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        values.append(max(1, min(16, int(part))))
    return values or [1, 4, 8, 12]


def run_gmsh_thread_benchmark(
    gmsh: str,
    geo_path: Path,
    out_dir: Path,
    dim_flag: str,
    thread_values: list[int],
    timeout_s: int,
) -> None:
    rows = []
    bench_root = out_dir / "gmsh_thread_benchmark"
    bench_root.mkdir(parents=True, exist_ok=True)
    for n_threads in thread_values:
        bdir = bench_root / f"nt_{n_threads}"
        if bdir.exists():
            shutil.rmtree(bdir)
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(geo_path, bdir / "mesh.geo")
        cmd = [gmsh, "-nt", str(n_threads), "mesh.geo", dim_flag, "-format", "msh2", "-o", "mesh.msh", "-v", "3"]
        t0 = time.perf_counter()
        code, log = run_command(cmd, bdir, timeout_s=timeout_s)
        wall = time.perf_counter() - t0
        (bdir / "log.gmsh").write_text(log, encoding="utf-8", errors="ignore")
        msh = bdir / "mesh.msh"
        rows.append({
            "threads": n_threads,
            "exit_code": code,
            "wall_time_s": wall,
            "mesh_created": bool(msh.exists() and msh.stat().st_size > 0),
            "mesh_size_bytes": int(msh.stat().st_size) if msh.exists() else 0,
            "command": " ".join(cmd),
            "directory": str(bdir.resolve()),
        })
    pd.DataFrame(rows).to_csv(out_dir / "gmsh_thread_benchmark.csv", index=False)


def infer_gmsh_last_phase(log_text: str) -> str:
    phase = "not_started"
    patterns = [
        ("reading_geo", "Reading"),
        ("meshing_1d", "Meshing 1D"),
        ("meshing_2d", "Meshing 2D"),
        ("meshing_3d_or_extrusion", "Meshing 3D"),
        ("optimizing", "Optimizing mesh"),
        ("writing_msh", "Writing"),
        ("finished", "Stopped on"),
    ]
    for line in log_text.splitlines():
        for name, marker in patterns:
            if marker in line:
                phase = name
    return phase


def parse_gmsh_mesh_counts(log_text: str) -> dict[str, int]:
    match = re.search(r"Info\s+:\s+(\d+)\s+nodes\s+(\d+)\s+elements", log_text or "")
    if not match:
        return {}
    return {
        "gmsh_log_nodes": int(match.group(1)),
        "gmsh_log_elements": int(match.group(2)),
        "estimated_cell_count": int(match.group(2)),
        "estimated_cell_count_source": "gmsh_log_elements",
    }


def gmsh_boundary_layer_edge_recovery_failed(log_text: str) -> bool:
    """Detect Gmsh failures caused by the synthetic BoundaryLayer offset curve."""
    text = log_text or ""
    return (
        "curve 444444" in text
        or "Identical points in triangulation" in text
        or ("No triangles in initial mesh" in text and "BoundaryLayer" in text)
    )


def strip_boundary_layer_block_from_geo(geo_path: Path) -> int:
    """Remove the generated Field[1]=BoundaryLayer block from a .geo file.

    This is intentionally narrow: it only acts on the Gmsh block generated by
    this script and leaves Distance/Threshold/Box refinement fields untouched.
    """
    text = geo_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    removed = 0
    skipping = False
    for line in lines:
        if "Field[1] = BoundaryLayer;" in line:
            skipping = True
            removed += 1
            continue
        if skipping:
            removed += 1
            if "BoundaryLayer Field = 1;" in line:
                skipping = False
            continue
        out.append(line)
    if removed:
        out.append("")
        out.append("// BoundaryLayer block removed by fallback after Gmsh edge-recovery failure.")
        geo_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return removed


def _write_minimal_foam_case(case_dir: Path) -> None:
    (case_dir / "system").mkdir(parents=True, exist_ok=True)
    (case_dir / "constant").mkdir(parents=True, exist_ok=True)
    (case_dir / "0").mkdir(parents=True, exist_ok=True)
    (case_dir / "system" / "controlDict").write_text("""FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      controlDict;
}
application     pimpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
writeFormat     ascii;
writePrecision  16;
writeCompression off;
timeFormat      general;
timePrecision   12;
""", encoding="utf-8")


def _desired_openfoam_boundary_type(name: str, current: str = "patch") -> str:
    lname = name.lower()
    if "frontandback" in lname:
        return "empty"
    if "wall" in lname or "airfoil" in lname or "lip" in lname or "trailing_edge" in lname:
        return "wall"
    return current if current not in {"empty", "wall"} else "patch"


def rewrite_openfoam_boundary_types(boundary_file: Path) -> dict[str, str]:
    """Set OpenFOAM patch types that gmsh physical names cannot encode."""
    text = boundary_file.read_text(encoding="utf-8", errors="ignore")
    changes: dict[str, str] = {}

    def repl(match) -> str:
        name = match.group(1)
        if name == "FoamFile":
            return match.group(0)
        body = match.group(2)
        current_match = re.search(r"\btype\s+([A-Za-z0-9_]+)\s*;", body)
        current = current_match.group(1) if current_match else "patch"
        desired = _desired_openfoam_boundary_type(name, current)
        changes[name] = desired
        if current_match:
            body = re.sub(r"\btype\s+[A-Za-z0-9_]+\s*;", f"type {desired};", body, count=1)
        else:
            body = "\n    type " + desired + ";" + body
        return f"{name}\n{{{body}}}"

    text = re.sub(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{([^{}]*)\}", repl, text, flags=re.MULTILINE | re.DOTALL)
    boundary_file.write_text(text, encoding="utf-8")
    return changes


def parse_checkmesh_metrics(log_text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    num = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"

    def to_float(value: str) -> float:
        return float(value.rstrip("."))

    cell_count = re.search(r"^\s*cells:\s*(\d+)\s*$", log_text, re.MULTILINE)
    if cell_count:
        metrics["checkMesh_cell_count"] = int(cell_count.group(1))
    for label, key in [
        ("hexahedra", "checkMesh_hexahedra"),
        ("prisms", "checkMesh_prisms"),
        ("wedges", "checkMesh_wedges"),
        ("pyramids", "checkMesh_pyramids"),
        ("tetrahedra", "checkMesh_tetrahedra"),
        ("polyhedra", "checkMesh_polyhedra"),
    ]:
        match = re.search(rf"^\s*{label}:\s*(\d+)\s*$", log_text, re.MULTILINE)
        if match:
            metrics[key] = int(match.group(1))

    non_orth = re.search(rf"Mesh non-orthogonality Max:\s*({num})\s+average:\s*({num})", log_text)
    if non_orth:
        metrics["checkMesh_max_non_orthogonality_deg"] = to_float(non_orth.group(1))
        metrics["checkMesh_average_non_orthogonality_deg"] = to_float(non_orth.group(2))
    severe_non_orth = re.search(r"Number of severely non-orthogonal\s*\(>\s*70 degrees\)\s*faces:\s*(\d+)", log_text)
    if severe_non_orth:
        metrics["checkMesh_severely_non_orthogonal_faces"] = int(severe_non_orth.group(1))
    skew = re.search(rf"Max skewness\s*=\s*({num})", log_text)
    if skew:
        metrics["checkMesh_max_skewness"] = to_float(skew.group(1))
    skew_faces = re.search(r"(\d+)\s+highly skew faces", log_text)
    if skew_faces:
        metrics["checkMesh_highly_skew_faces"] = int(skew_faces.group(1))
    aspect = re.search(rf"Max aspect ratio\s*=\s*({num})", log_text)
    if aspect:
        metrics["checkMesh_max_aspect_ratio"] = to_float(aspect.group(1))
    vol = re.search(rf"Min volume\s*=\s*({num})\.\s*Max volume\s*=\s*({num})", log_text)
    if vol:
        metrics["checkMesh_min_volume"] = to_float(vol.group(1))
        metrics["checkMesh_max_volume"] = to_float(vol.group(2))
    determinant = re.search(rf"Cell determinant .*?minimum:\s*({num})\s+average:\s*({num})", log_text)
    if determinant:
        metrics["checkMesh_min_cell_determinant"] = to_float(determinant.group(1))
        metrics["checkMesh_average_cell_determinant"] = to_float(determinant.group(2))
    small_det = re.search(r"Cells with small determinant .*?number of cells:\s*(\d+)", log_text)
    if small_det:
        metrics["checkMesh_small_determinant_cells"] = int(small_det.group(1))
    interp = re.search(rf"Face interpolation weight\s*:\s*minimum:\s*({num})\s+average:\s*({num})", log_text)
    if interp:
        metrics["checkMesh_min_face_interpolation_weight"] = to_float(interp.group(1))
        metrics["checkMesh_average_face_interpolation_weight"] = to_float(interp.group(2))
    small_interp = re.search(r"Faces with small interpolation weight .*?number of faces:\s*(\d+)", log_text)
    if small_interp:
        metrics["checkMesh_small_interpolation_weight_faces"] = int(small_interp.group(1))
    vol_ratio = re.search(rf"Face volume ratio\s*:\s*minimum:\s*({num})\s+average:\s*({num})", log_text)
    if vol_ratio:
        metrics["checkMesh_min_face_volume_ratio"] = to_float(vol_ratio.group(1))
        metrics["checkMesh_average_face_volume_ratio"] = to_float(vol_ratio.group(2))
    small_vol_ratio = re.search(r"Faces with small volume ratio .*?number of faces:\s*(\d+)", log_text)
    if small_vol_ratio:
        metrics["checkMesh_small_volume_ratio_faces"] = int(small_vol_ratio.group(1))
    failed = re.search(r"Failed\s+(\d+)\s+mesh checks", log_text)
    if failed:
        metrics["checkMesh_failed_checks_count"] = int(failed.group(1))
    failed_names: list[str] = []
    if metrics.get("checkMesh_highly_skew_faces"):
        failed_names.append("highly_skew_faces")
    if metrics.get("checkMesh_small_determinant_cells"):
        failed_names.append("small_cell_determinant")
    if metrics.get("checkMesh_small_interpolation_weight_faces"):
        failed_names.append("small_face_interpolation_weight")
    if metrics.get("checkMesh_small_volume_ratio_faces"):
        failed_names.append("small_face_volume_ratio")
    if "negative volume" in log_text.lower():
        failed_names.append("negative_volume")
    if " ***error" in log_text.lower():
        failed_names.append("topology_or_geometry_error")
    if failed_names:
        metrics["checkMesh_failed_checks"] = failed_names
    return metrics


def update_mesh_counts_from_checkmesh(report: dict[str, Any]) -> None:
    """Use OpenFOAM checkMesh cell counts as a fallback/confirmation source."""
    if report.get("estimated_cell_count") is None and report.get("checkMesh_cell_count") is not None:
        report["estimated_cell_count"] = int(report["checkMesh_cell_count"])
        report["estimated_cell_count_source"] = "checkMesh_cell_count"
    for src, dst in [
        ("checkMesh_hexahedra", "number_of_hexes"),
        ("checkMesh_prisms", "number_of_prisms"),
        ("checkMesh_pyramids", "number_of_pyramids"),
        ("checkMesh_tetrahedra", "number_of_tets"),
    ]:
        if report.get(dst) in (None, 0) and report.get(src) is not None:
            report[dst] = int(report[src])


def update_boundary_layer_confirmation(report: dict[str, Any]) -> None:
    """Confirm that BL-like extruded cells exist without claiming exact layer count."""
    if not report.get("boundary_layer_requested"):
        report["boundary_layer_layers_created"] = False
        report["boundary_layer_confirmation_basis"] = "boundary_layer_not_requested"
        return

    hexes = int(report.get("number_of_hexes", 0) or report.get("checkMesh_hexahedra", 0) or 0)
    prisms = int(report.get("number_of_prisms", 0) or report.get("checkMesh_prisms", 0) or 0)
    quads = int(report.get("number_of_quads", 0) or 0)
    report["boundary_layer_candidate_hex_cells"] = hexes
    report["boundary_layer_candidate_prism_cells"] = prisms
    report["boundary_layer_exact_layer_count_confirmed"] = False

    if report.get("extruded_3d"):
        if hexes > 0:
            report["boundary_layer_layers_created"] = True
            report["boundary_layer_confirmation_basis"] = "hex_volume_cells_detected_after_extrusion"
        elif prisms > 0:
            report["boundary_layer_layers_created"] = True
            report["boundary_layer_confirmation_basis"] = "prism_volume_cells_detected_after_extrusion"
        else:
            report["boundary_layer_layers_created"] = False
            report["boundary_layer_confirmation_basis"] = "no_hex_or_prism_volume_cells_detected"
    else:
        report["boundary_layer_layers_created"] = quads > 0
        report["boundary_layer_confirmation_basis"] = "quad_surface_cells_detected_in_2d_mesh" if quads > 0 else "no_quad_surface_cells_detected"


def _read_legacy_vtk_problem_geometry(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """Read points and entity centroids from checkMesh legacy VTK output."""
    if not path.is_file():
        empty = np.empty((0, 3), dtype=float)
        return empty, empty, 0
    text = path.read_text(encoding="utf-8", errors="ignore")
    points_match = re.search(r"\bPOINTS\s+(\d+)\s+\w+\s+(.*?)(?=\n(?:POLYGONS|VERTICES|LINES|CELLS|POINT_DATA|CELL_DATA)\b)", text, re.S)
    if not points_match:
        empty = np.empty((0, 3), dtype=float)
        return empty, empty, 0
    count = int(points_match.group(1))
    values = np.fromstring(points_match.group(2), sep=" ", dtype=float)
    if values.size < 3 * count:
        empty = np.empty((0, 3), dtype=float)
        return empty, empty, 0
    points = values[: 3 * count].reshape((-1, 3))
    entities_match = re.search(
        r"\b(?:POLYGONS|VERTICES|LINES|CELLS)\s+(\d+)\s+(\d+)\s+"
        r"(.*?)(?=\n(?:CELL_TYPES|POINT_DATA|CELL_DATA|FIELD|SCALARS|VECTORS)\b|\Z)",
        text,
        re.S,
    )
    if not entities_match:
        return points, np.empty((0, 3), dtype=float), 0
    entity_count = int(entities_match.group(1))
    connectivity = np.fromstring(entities_match.group(3), sep=" ", dtype=np.int64)
    centroids: list[np.ndarray] = []
    cursor = 0
    for _ in range(entity_count):
        if cursor >= connectivity.size:
            break
        size = int(connectivity[cursor])
        cursor += 1
        indices = connectivity[cursor : cursor + size]
        cursor += size
        if size > 0 and len(indices) == size and int(indices.min()) >= 0 and int(indices.max()) < len(points):
            centroids.append(points[indices].mean(axis=0))
    entity_centroids = np.asarray(centroids, dtype=float).reshape((-1, 3)) if centroids else np.empty((0, 3), dtype=float)
    return points, entity_centroids, entity_count


def _read_legacy_vtk_problem_points(path: Path) -> tuple[np.ndarray, int]:
    """Backward-compatible point reader used by focused unit tests."""
    points, _, entity_count = _read_legacy_vtk_problem_geometry(path)
    return points, entity_count


def _read_foam_label_set(path: Path) -> list[int]:
    """Read labels from an ASCII OpenFOAM cellSet/faceSet/pointSet file."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"\n\s*(\d+)\s*\n\s*\(\s*(.*?)\s*\)", text, re.S)
    if not match:
        return []
    expected = int(match.group(1))
    labels = [int(value) for value in re.findall(r"(?m)^\s*(\d+)\s*$", match.group(2))]
    return labels[:expected]


def summarize_checkmesh_problem_locations(
    vtk_dir: Path,
    profile_points: pd.DataFrame | None,
    check_mesh_log: str = "",
    sets_dir: Path | None = None,
) -> dict[str, Any]:
    """Summarize where checkMesh wrote its problem face/point sets."""
    profile_x_min = profile_x_max = profile_y_mid = chord = None
    if profile_points is not None and not profile_points.empty and {"x_m", "z_m"}.issubset(profile_points.columns):
        profile_x_min = float(profile_points["x_m"].min())
        profile_x_max = float(profile_points["x_m"].max())
        profile_y_mid = float(profile_points["z_m"].mean())
        chord = max(profile_x_max - profile_x_min, 1.0e-15)

    files = {
        "high_skew_faces": "skewFaces.vtk",
        "small_determinant_cells": "underdeterminedCells.vtk",
        "low_interpolation_weight_faces": "lowWeightFaces.vtk",
        "low_volume_ratio_faces": "lowVolRatioFaces.vtk",
        "short_edge_points": "shortEdges.vtk",
    }
    set_metadata = {
        "high_skew_faces": ("skewFaces", "face"),
        "small_determinant_cells": ("underdeterminedCells", "cell"),
        "low_interpolation_weight_faces": ("lowWeightFaces", "face"),
        "low_volume_ratio_faces": ("lowVolRatioFaces", "face"),
        "short_edge_points": ("shortEdges", "point"),
    }
    metric_patterns = {
        "high_skew_faces": (
            r"Max skewness\s*=\s*([0-9.eE+-]+)", 4.0, "maximum",
            r"(\d+)\s+highly skew faces",
        ),
        "small_determinant_cells": (
            r"Cell determinant.*?minimum:\s*([0-9.eE+-]+)", 0.001, "minimum",
            r"Cells with small determinant.*?number of cells:\s*(\d+)",
        ),
        "low_interpolation_weight_faces": (
            r"Face interpolation weight\s*:\s*minimum:\s*([0-9.eE+-]+)", 0.05, "minimum",
            r"Faces with small interpolation weight.*?number of faces:\s*(\d+)",
        ),
        "low_volume_ratio_faces": (
            r"Face volume ratio\s*:\s*minimum:\s*([0-9.eE+-]+)", 0.01, "minimum",
            r"Faces with small volume ratio.*?number of faces:\s*(\d+)",
        ),
    }
    result: dict[str, Any] = {}
    for name, filename in files.items():
        coordinates, entity_centroids, entity_count = _read_legacy_vtk_problem_geometry(vtk_dir / filename)
        if coordinates.size == 0:
            continue
        analysis_coordinates = entity_centroids if entity_centroids.size else coordinates
        centroid = analysis_coordinates.mean(axis=0)
        bounds_min = coordinates.min(axis=0)
        bounds_max = coordinates.max(axis=0)
        location = "unclassified"
        x_over_c = None
        x_region_unique_point_counts: dict[str, int] = {}
        if chord is not None and profile_x_min is not None:
            x_over_c = float((centroid[0] - profile_x_min) / chord)
            unique_coordinates = np.unique(np.round(coordinates, 12), axis=0)
            normalized_x = (unique_coordinates[:, 0] - profile_x_min) / chord
            x_region_unique_point_counts = {
                "inlet_or_LE_x_over_c_le_0p08": int(np.count_nonzero(normalized_x <= 0.08)),
                "mid_chord_0p08_to_0p92": int(np.count_nonzero((normalized_x > 0.08) & (normalized_x < 0.92))),
                "TE_x_over_c_ge_0p92": int(np.count_nonzero(normalized_x >= 0.92)),
            }
            if x_over_c <= 0.08:
                side = "upper" if profile_y_mid is not None and centroid[1] >= profile_y_mid else "lower"
                location = f"{side}_inlet_lip_or_LE_transition"
            elif x_over_c >= 0.92:
                location = "trailing_edge_transition"
            else:
                location = "near_wall_or_size_transition"
        entity_x_quantiles: dict[str, float] = {}
        region_entity_counts: dict[str, int] = {}
        entity_centroid_sample: list[list[float]] = []
        if chord is not None and profile_x_min is not None and len(analysis_coordinates):
            entity_x = (analysis_coordinates[:, 0] - profile_x_min) / chord
            quantiles = np.quantile(entity_x, [0.0, 0.1, 0.5, 0.9, 1.0])
            entity_x_quantiles = {
                key: float(value)
                for key, value in zip(("min", "p10", "median", "p90", "max"), quantiles)
            }
            side_upper = analysis_coordinates[:, 1] >= float(profile_y_mid or 0.0)
            x_bands = {
                "inlet_or_LE": entity_x <= 0.08,
                "mid_chord": (entity_x > 0.08) & (entity_x < 0.92),
                "TE": entity_x >= 0.92,
            }
            for band_name, band_mask in x_bands.items():
                region_entity_counts[f"upper_{band_name}"] = int(np.count_nonzero(band_mask & side_upper))
                region_entity_counts[f"lower_{band_name}"] = int(np.count_nonzero(band_mask & ~side_upper))
            order = np.argsort(entity_x)
            sample_indices = order[: min(12, len(order))]
            entity_centroid_sample = [
                [float(value) for value in analysis_coordinates[index]] for index in sample_indices
            ]
        set_name, label_type = set_metadata[name]
        labels = _read_foam_label_set(sets_dir / set_name) if sets_dir is not None else []
        cause_hint = "Inspect the written VTK set and neighboring size transition."
        if name == "high_skew_faces" and x_over_c is not None and x_over_c <= 0.08:
            cause_hint = "The worst skew face is at an inlet lip/connector corner; compare triangular versus recombined inlet-transition elements."
        elif name == "low_interpolation_weight_faces" and x_over_c is not None and x_over_c <= 0.08:
            cause_hint = (
                "Low weights are concentrated in the inlet transition. For a zero-thickness topology, "
                "compare the first BL/interface-cell volume with the first cavity triangle and tune the "
                "short normal compatibility strip; for a finite-thickness topology, inspect the lip "
                "connector and any selected fan. The inlet tangential spacing and y1 are independent controls."
            )
        elif name == "small_determinant_cells":
            cause_hint = "A broad chordwise distribution is typical of the highly anisotropic near-wall stack; compare y1 with tangential wall spacing before changing physics-driven y+."
        elif name == "short_edge_points":
            cause_hint = (
                "Short edges are diagnostic, not a failed check by themselves. When they are distributed "
                "along the complete wall and their length matches the physical y1, they are the intended "
                "first boundary-layer height rather than duplicate geometry. Investigate only isolated "
                "clusters or zero/nearly-zero consecutive geometry segments."
            )
        item = {
            "vtk_file": filename,
            "entity_count": entity_count,
            "vtk_entity_centroid_count": int(len(entity_centroids)),
            "unique_point_count": int(len(np.unique(np.round(coordinates, 12), axis=0))),
            "centroid_m": [float(value) for value in centroid],
            "bounds_min_m": [float(value) for value in bounds_min],
            "bounds_max_m": [float(value) for value in bounds_max],
            "centroid_x_over_chord": x_over_c,
            "likely_region": location,
            "x_region_unique_point_counts": x_region_unique_point_counts,
            "entity_x_over_chord_quantiles": entity_x_quantiles,
            "region_entity_counts": region_entity_counts,
            "entity_centroids_nearest_inlet_sample_m": entity_centroid_sample,
            "openfoam_set_name": set_name,
            "openfoam_label_type": label_type,
            "openfoam_label_count": int(len(labels)),
            "openfoam_label_sample": labels[:40],
            "cause_hint": cause_hint,
        }
        metric = metric_patterns.get(name)
        if metric:
            match = re.search(metric[0], check_mesh_log, re.I | re.S)
            item[f"reported_{metric[2]}"] = float(match.group(1)) if match else None
            item["checkMesh_threshold"] = metric[1]
            count_match = re.search(metric[3], check_mesh_log, re.I | re.S)
            item["checkMesh_reported_count"] = int(count_match.group(1)) if count_match else None
        result[name] = item
    return result


def write_checkmesh_problem_location_report(mesh_dir: Path, locations: dict[str, Any]) -> None:
    write_json(mesh_dir / "checkMesh_problem_locations.json", locations)
    meanings = {
        "high_skew_faces": "Caras deformadas: una interpolacion muy oblicua puede introducir difusion o inestabilidad numerica.",
        "small_determinant_cells": "Celdas mal condicionadas, normalmente por anisotropia elevada, colapso o angulos internos deficientes.",
        "low_interpolation_weight_faces": "El centro de la cara queda demasiado cerca de uno de los centros de celda; empeora la interpolacion entre vecinos.",
        "low_volume_ratio_faces": "La cara separa celdas con volumenes muy distintos; indica una transicion de tamano demasiado brusca.",
        "short_edge_points": "Aristas cortas de diagnostico. Una distribucion continua con longitud similar a y1 es esperable en la primera capa; grupos aislados pueden indicar puntos casi coincidentes.",
    }
    lines = [
        "OpenFOAM checkMesh problem locations",
        "====================================",
        "",
        "Coordinates come from checkMesh -writeSets/-writeSurfaces VTK output.",
        "They identify where the failed faces/short edges are; they do not alter the mesh.",
        "",
    ]
    if not locations:
        lines.append("No problem-location VTK sets were produced.")
    for name, item in locations.items():
        lines += [
            f"[{name}]",
            f"meaning: {meanings.get(name, 'Conjunto diagnostico escrito por checkMesh.')}",
            f"entities: {item.get('entity_count')}",
            f"checkMesh_reported_count: {item.get('checkMesh_reported_count')}",
            f"likely_region: {item.get('likely_region')}",
            f"centroid_x_over_chord: {item.get('centroid_x_over_chord')}",
            f"centroid_m: {item.get('centroid_m')}",
            f"bounds_min_m: {item.get('bounds_min_m')}",
            f"bounds_max_m: {item.get('bounds_max_m')}",
            f"x_region_unique_point_counts: {item.get('x_region_unique_point_counts')}",
            f"region_entity_counts: {item.get('region_entity_counts')}",
            f"entity_x_over_chord_quantiles: {item.get('entity_x_over_chord_quantiles')}",
            f"openfoam_set_name: {item.get('openfoam_set_name')}",
            f"openfoam_label_type: {item.get('openfoam_label_type')}",
            f"openfoam_label_count: {item.get('openfoam_label_count')}",
            f"openfoam_label_sample: {item.get('openfoam_label_sample')}",
            f"cause_hint: {item.get('cause_hint')}",
            f"reported_minimum: {item.get('reported_minimum')}",
            f"reported_maximum: {item.get('reported_maximum')}",
            f"checkMesh_threshold: {item.get('checkMesh_threshold')}",
            f"vtk_file: checkMesh_problem_locations/{item.get('vtk_file')}",
            "",
        ]
    (mesh_dir / "checkMesh_problem_locations.txt").write_text("\n".join(lines), encoding="utf-8")


def run_openfoam_mesh_checks(
    mesh_dir: Path,
    msh_path: Path,
    report: dict[str, Any],
    timeout_s: int = 600,
    use_temp_workdir: bool = True,
    profile_points: pd.DataFrame | None = None,
) -> None:
    """Run gmshToFoam/checkMesh only when explicitly requested by the caller."""
    report["check_mesh_requested"] = True

    def mirror_converted_polymesh(conv_dir: Path) -> None:
        if use_temp_workdir:
            mirror = mesh_dir / "openfoam_mesh_check_case"
            if mirror.exists():
                shutil.rmtree(mirror)
            mirror_poly = mirror / "constant" / "polyMesh"
            mirror_poly.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(conv_dir / "constant" / "polyMesh", mirror_poly)
            shutil.copy2(local_msh, mirror / "mesh_final.msh")
            report["openfoam_polyMesh_source"] = str(mirror_poly.resolve())
        else:
            report["openfoam_polyMesh_source"] = str((conv_dir / "constant" / "polyMesh").resolve())

    gmsh_to_foam = shutil.which("gmshToFoam")
    check_mesh = shutil.which("checkMesh")
    if not gmsh_to_foam:
        report["gmshToFoam_status"] = "MISSING"
        report["openfoam_polyMesh_created"] = False
        return
    if not check_mesh:
        report["checkMesh_status"] = "MISSING"
    temp_dir: Path | None = None
    if use_temp_workdir:
        temp_dir = Path(tempfile.mkdtemp(prefix="ramair_openfoam_mesh_check_"))
        conv = temp_dir / "case"
    else:
        conv = mesh_dir / "openfoam_mesh_check_case"
        if conv.exists():
            shutil.rmtree(conv)
    report["openfoam_temp_workdir_used"] = bool(use_temp_workdir)
    report["openfoam_execution_directory"] = str(conv.resolve())
    _write_minimal_foam_case(conv)
    local_msh = conv / "mesh_final.msh"
    shutil.copy2(msh_path, local_msh)
    gmsh_to_foam_cmd = [gmsh_to_foam]
    if report.get("mesh_level_extrusion_method"):
        gmsh_to_foam_cmd.append("-keepOrientation")
    gmsh_to_foam_cmd.append(str(local_msh.name))
    conversion_started = time.perf_counter()
    code, log = run_command(gmsh_to_foam_cmd, conv, timeout_s=timeout_s)
    report["gmshToFoam_wall_time_s"] = float(time.perf_counter() - conversion_started)
    (mesh_dir / "log.gmshToFoam").write_text(log, encoding="utf-8", errors="ignore")
    report["gmshToFoam_command"] = " ".join(map(str, gmsh_to_foam_cmd))
    report["gmshToFoam_exit_code"] = code
    boundary_file = conv / "constant" / "polyMesh" / "boundary"
    report["openfoam_polyMesh_created"] = bool(boundary_file.exists())
    report["openfoam_polyMesh_source"] = str((conv / "constant" / "polyMesh").resolve()) if boundary_file.exists() else None
    if code != 0 or not boundary_file.exists():
        report["gmshToFoam_status"] = "FAIL"
        if "fileName::stripInvalid" in log:
            report["gmshToFoam_failure_comment"] = (
                "OpenFOAM rejected the execution path. This commonly happens in WSL when the project lives under "
                "/mnt/c with spaces or non-ASCII characters. Keep --openfoam-temp-workdir enabled so gmshToFoam "
                "and checkMesh run in /tmp, then copy polyMesh back to the project."
            )
        if temp_dir is not None:
            report["openfoam_temp_workdir_left_for_debug"] = str(temp_dir.resolve())
        return
    report["gmshToFoam_status"] = "OK"
    report["openfoam_boundary_type_rewrites"] = rewrite_openfoam_boundary_types(boundary_file)
    boundary_text = boundary_file.read_text(encoding="utf-8", errors="ignore")
    report["frontAndBack_boundary_present"] = "frontAndBack" in boundary_text
    report["frontAndBack_empty_declared"] = "frontAndBack" in boundary_text and "empty" in boundary_text
    report["forbidden_ram_air_inlet_patch_present"] = "ram_air_inlet" in boundary_text
    mirror_converted_polymesh(conv)
    if check_mesh:
        check_started = time.perf_counter()
        ccode, clog = run_command([check_mesh, "-allTopology", "-allGeometry"], conv, timeout_s=timeout_s)
        report["checkMesh_wall_time_s"] = float(time.perf_counter() - check_started)
        (mesh_dir / "log.checkMesh").write_text(clog, encoding="utf-8", errors="ignore")
        report["checkMesh_command"] = f"{check_mesh} -allTopology -allGeometry"
        report["checkMesh_exit_code"] = ccode
        try:
            report.update(parse_checkmesh_metrics(clog))
        except Exception as exc:
            report["checkMesh_metrics_parse_error"] = str(exc)
        update_mesh_counts_from_checkmesh(report)
        update_boundary_layer_confirmation(report)
        failed_count = report.get("checkMesh_failed_checks_count")
        if failed_count is not None:
            failed_text = int(failed_count) > 0
        else:
            failed_text = any(s in clog for s in [" ***Error", "negative volume", "negative cell"])
        report["checkMesh_failed_text_detected"] = failed_text
        report["checkMesh_status"] = "OK" if ccode == 0 and not report["checkMesh_failed_text_detected"] else "FAIL"
        if report["checkMesh_status"] == "FAIL":
            location_command = [
                check_mesh,
                "-allTopology",
                "-allGeometry",
                "-writeSets",
                "-writeSurfaces",
                "-setFormat",
                "vtk",
                "-surfaceFormat",
                "vtk",
            ]
            location_code, location_log = run_command(location_command, conv, timeout_s=timeout_s)
            (mesh_dir / "log.checkMesh.locations").write_text(location_log, encoding="utf-8", errors="ignore")
            report["checkMesh_location_command"] = " ".join(location_command)
            report["checkMesh_location_exit_code"] = location_code
            source_locations = conv / "postProcessing" / "checkMesh" / "constant"
            target_locations = mesh_dir / "checkMesh_problem_locations"
            if target_locations.exists():
                shutil.rmtree(target_locations)
            if source_locations.is_dir():
                shutil.copytree(source_locations, target_locations)
            source_sets = conv / "constant" / "polyMesh" / "sets"
            target_sets = mesh_dir / "checkMesh_problem_sets"
            if target_sets.exists():
                shutil.rmtree(target_sets)
            if source_sets.is_dir():
                shutil.copytree(source_sets, target_sets)
            locations = summarize_checkmesh_problem_locations(
                target_locations,
                profile_points,
                clog,
                sets_dir=target_sets if target_sets.is_dir() else None,
            )
            report["checkMesh_problem_locations"] = locations
            report["checkMesh_problem_locations_directory"] = str(target_locations.resolve())
            report["checkMesh_problem_sets_directory"] = str(target_sets.resolve()) if target_sets.is_dir() else None
            write_checkmesh_problem_location_report(mesh_dir, locations)
    if temp_dir is not None:
        shutil.rmtree(temp_dir, ignore_errors=True)


def check_existing_openfoam_mesh(args: argparse.Namespace) -> None:
    """Convert/check the active mesh_final.msh without launching Gmsh again."""
    case_root: Path = args.case_root
    variant = args.variant
    run_id = _safe_run_id()
    mesh_root = cfd_meshes_root(case_root) / variant
    mesh_file = mesh_root / "mesh_final.msh"
    if not mesh_file.exists() or mesh_file.stat().st_size <= 0:
        raise FileNotFoundError(f"Existing mesh_final.msh not found or empty: {mesh_file}")
    report = read_json(mesh_root / "mesh_quality_report.json", {}) or {}
    report.update({
        "variant": variant,
        "mesh_check_run_id": run_id,
        "check_existing_mesh": True,
        "dry_run": False,
        "mesh_file_created": True,
        "mesh_file_size_bytes": int(mesh_file.stat().st_size),
        "mesh_file_mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mesh_file.stat().st_mtime)),
        "check_mesh_requested": True,
        "openfoam_mesh_requested": True,
    })
    run_openfoam_mesh_checks(
        mesh_root,
        mesh_file,
        report,
        timeout_s=max(60, int(args.openfoam_tool_timeout_s)),
        use_temp_workdir=bool(args.openfoam_temp_workdir),
        profile_points=(load_case_variant(args.case_root, variant)[0]),
    )
    mirror_poly = mesh_root / "openfoam_mesh_check_case" / "constant" / "polyMesh"
    if (mirror_poly / "boundary").exists():
        copy_checked_polymesh_to_active(case_root, mesh_root, variant, mirror_poly, run_id)
    decision = decide_quality(report, int(report.get("attempt", 1) or 1)) if decide_quality else None
    if write_quality_report and decision:
        write_quality_report(mesh_root, report, decision)
        status = decision.status
    else:
        status = "FAIL" if report.get("checkMesh_status") == "FAIL" or not report.get("openfoam_polyMesh_created") else "WARNING_ACCEPTABLE"
        report["status"] = status
        write_json(mesh_root / "mesh_quality_report.json", report)
        (mesh_root / "mesh_quality_report.txt").write_text(str(report), encoding="utf-8")
    write_json(mesh_root / "mesh_build_manifest.json", {
        "variant": variant,
        "mesh_level": args.mesh_level,
        "history": [{"attempt": report.get("attempt"), "status": status, "mesh_file_created": True, "gmsh_exit_code": report.get("gmsh_exit_code")}],
        "dry_run": False,
        "write_openfoam_mesh": True,
        "check_mesh": True,
        "check_existing_mesh": True,
        "run_id": run_id,
        "note": "Existing mesh_final.msh was converted/checked without launching Gmsh.",
    })
    final_report = read_json(mesh_root / "mesh_quality_report.json", {}) or {}
    print(f"Existing mesh checked: {mesh_file.resolve()}")
    print(f"OpenFOAM mesh check status: {status}")
    if final_report.get("openfoam_execution_gate"):
        print(f"OpenFOAM execution gate: {final_report.get('openfoam_execution_gate')} - {final_report.get('openfoam_execution_gate_reason')}")
    if str(status) == "FAIL":
        failed = final_report.get("failed_checks", []) or []
        warnings = final_report.get("warnings", []) or []
        if failed:
            print(f"Internal quality failed checks: {', '.join(map(str, failed))}")
        if warnings:
            print(f"Internal quality warnings: {', '.join(map(str, warnings[:8]))}")
        print(f"Review quality report: {(mesh_root / 'mesh_quality_report.txt').resolve()}")


def plot_previews(points: pd.DataFrame, edges: pd.DataFrame, mesh_dir: Path, variant: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    lookup = points.set_index("point_id")
    plot_specs = [
        ("geometry_preview_airfoil.png", "airfoil"),
        ("geometry_preview_inlet.png", "inlet"),
        ("geometry_preview_te.png", "te"),
    ]
    for name, mode in plot_specs:
        fig, ax = plt.subplots(figsize=(8, 4))
        for _, e in edges.iterrows():
            a,b = int(e.start_point_id), int(e.end_point_id)
            if a not in lookup.index or b not in lookup.index:
                continue
            p1=lookup.loc[a]; p2=lookup.loc[b]
            patch=str(e.patch_name)
            lw = 2.0 if "inlet" in patch.lower() or "trailing" in patch.lower() else 1.0
            ax.plot([p1.x_m,p2.x_m],[p1.z_m,p2.z_m], linewidth=lw)
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, linewidth=0.3)
        ax.set_xlabel("x [m]"); ax.set_ylabel("z [m]")
        ax.set_title(f"{variant} - {name.replace('_',' ')}")
        if mode == "inlet":
            ax.set_xlim(points.x_m.min()-0.03, points.x_m.min()+0.16)
        elif mode == "te":
            ax.set_xlim(points.x_m.max()-0.16, points.x_m.max()+0.03)
        elif mode == "wake":
            ax.set_xlim(points.x_m.max()-0.1, points.x_m.max()+1.0)
        fig.tight_layout(); fig.savefig(mesh_dir/name, dpi=180); plt.close(fig)


def plot_msh_front_surface_preview(
    msh_path: Path,
    out_png: Path,
    *,
    points: pd.DataFrame | None = None,
    view: str = "full",
) -> bool:
    try:
        lines = Path(msh_path).read_text(encoding="utf-8", errors="ignore").splitlines()
        nodes: dict[int, tuple[float, float, float]] = {}
        elems: list[list[int]] = []
        i = 0
        while i < len(lines):
            if lines[i].strip() == "$Nodes":
                n = int(lines[i + 1].strip())
                for j in range(n):
                    parts = lines[i + 2 + j].split()
                    nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
                i += n + 3
                continue
            if lines[i].strip() == "$Elements":
                n = int(lines[i + 1].strip())
                for j in range(n):
                    parts = lines[i + 2 + j].split()
                    etype = int(parts[1])
                    ntags = int(parts[2])
                    conn = list(map(int, parts[3 + ntags:]))
                    if etype in {2, 3} and len(conn) >= (3 if etype == 2 else 4):
                        elems.append(conn[:3] if etype == 2 else conn[:4])
                i += n + 3
                continue
            i += 1
        if not nodes or not elems:
            return False
        zmin = min(p[2] for p in nodes.values())
        zmax = max(p[2] for p in nodes.values())
        tol = max(1e-10, 1e-6 * max(abs(zmax - zmin), 1.0))
        segments = []
        for conn in elems:
            pts3 = [nodes[nid] for nid in conn if nid in nodes]
            if len(pts3) != len(conn):
                continue
            if max(abs(p[2] - zmin) for p in pts3) > tol:
                continue
            pts2 = [(p[0], p[1]) for p in pts3]
            for a, b in zip(pts2, pts2[1:] + pts2[:1]):
                segments.append([a, b])
        if not segments:
            return False
        xy = np.asarray([p for seg in segments for p in seg], dtype=float)
        try:
            import matplotlib.pyplot as plt
            from matplotlib.collections import LineCollection
        except Exception:
            return True
        fig, ax = plt.subplots(figsize=(9, 6))
        ax.add_collection(LineCollection(segments, colors="0.15", linewidths=0.25))
        xlim = (float(xy[:, 0].min()), float(xy[:, 0].max()))
        ylim = (float(xy[:, 1].min()), float(xy[:, 1].max()))
        if points is not None and not points.empty and view in {"te", "inlet"}:
            px = points["x_m"].to_numpy(float)
            py = points["z_m"].to_numpy(float)
            chord = max(float(px.max() - px.min()), 1.0e-12)
            if view == "te":
                cx = float(px.max())
                local = points[np.asarray(px >= px.max() - 0.18 * chord)]
                cy = float(local["z_m"].mean()) if not local.empty else float(np.mean(py))
                half_x = 0.18 * chord
                half_y = 0.16 * chord
            else:
                cx = float(px.min())
                local = points[np.asarray(px <= px.min() + 0.22 * chord)]
                cy = float(local["z_m"].mean()) if not local.empty else float(np.mean(py))
                half_x = 0.24 * chord
                half_y = 0.24 * chord
            xlim = (cx - half_x, cx + half_x)
            ylim = (cy - half_y, cy + half_y)
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, linewidth=0.2)
        ax.set_xlabel("x [m]")
        ax.set_ylabel("y/profile vertical [m]")
        title_suffix = "" if view == "full" else f" - {view} zoom"
        ax.set_title(f"Gmsh front-surface mesh preview from mesh.msh{title_suffix}")
        fig.tight_layout()
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_png, dpi=220)
        plt.close(fig)
        return True
    except Exception:
        return False


def basic_report(points: pd.DataFrame, edges: pd.DataFrame, variant: str, geo_info: dict, mesh_cfg: dict) -> dict[str, Any]:
    coords = points[["x_m", "z_m"]].to_numpy(float)
    dup = int(pd.DataFrame(coords, columns=["x", "z"]).round(12).duplicated().sum())
    is_open = variant in {"open_ramair", "ross_standard_8p4", "ross_minimum_4p0", "standard", "optimized"}
    return {
        "variant": variant,
        "no_nan_coordinates": bool(np.isfinite(coords).all()),
        "no_duplicate_points": dup == 0,
        "domain_contains_profile": True,
        "inlet_opening_detected_for_open_cases": bool(edges.patch_name.str.contains("ram_air_inlet|inlet_opening_marker", case=False, na=False).any()) if is_open else True,
        "cavity_connected_to_exterior": True if not is_open else bool(geo_info.get("open_connected_fluid_surface", False)),
        "physical_groups_exist": bool(geo_info.get("physical_groups")),
        "wall_patches_exist": any("wall" in p for p in geo_info.get("physical_groups", [])),
        "farfield_patch_exists": "farfield" in geo_info.get("physical_groups", []),
        "frontAndBack_patch_exists": "frontAndBack" in geo_info.get("physical_groups", []),
        "ram_air_inlet_is_physical_patch": bool(geo_info.get("ram_air_inlet_is_physical_patch", False)),
        "boundary_layer_requested": bool(geo_info.get("boundary_layer_requested", False)),
        "boundary_layer_layers_requested": int(geo_info.get("boundary_layer_layers_requested", 0) or 0),
        "boundary_layer_quads_requested": bool(geo_info.get("boundary_layer_quads_requested", False)),
        "boundary_layer_curve_ids": geo_info.get("boundary_layer_curve_ids", []),
        "boundary_layer_excluded_te_curve_ids": geo_info.get("boundary_layer_excluded_te_curve_ids", []),
        "boundary_layer_exclude_te_cap_from_bl": bool(geo_info.get("boundary_layer_exclude_te_cap_from_bl", False)),
        "boundary_layer_first_cell_height_chord": float(geo_info.get("boundary_layer_first_cell_height_chord", 0.0) or 0.0),
        "boundary_layer_first_cell_height_m": float(geo_info.get("boundary_layer_first_cell_height_m", 0.0) or 0.0),
        "boundary_layer_requested_first_cell_height_chord": float(geo_info.get("boundary_layer_requested_first_cell_height_chord", 0.0) or 0.0),
        "boundary_layer_first_cell_height_source": geo_info.get("boundary_layer_first_cell_height_source"),
        "boundary_layer_yplus_estimate": geo_info.get("boundary_layer_yplus_estimate"),
        "boundary_layer_total_thickness_chord": float(geo_info.get("boundary_layer_total_thickness_chord", 0.0) or 0.0),
        "boundary_layer_raw_total_thickness_chord": float(geo_info.get("boundary_layer_raw_total_thickness_chord", 0.0) or 0.0),
        "boundary_layer_total_thickness_limited": bool(geo_info.get("boundary_layer_total_thickness_limited", False)),
        "closed_boundary_layer_aniso_max_deg": geo_info.get("closed_boundary_layer_aniso_max_deg"),
        "closed_boundary_layer_intersect_metrics": geo_info.get("closed_boundary_layer_intersect_metrics"),
        "closed_te_segment_min_length_chord": geo_info.get("closed_te_segment_min_length_chord"),
        "closed_te_segment_mean_length_chord": geo_info.get("closed_te_segment_mean_length_chord"),
        "closed_te_segment_max_length_chord": geo_info.get("closed_te_segment_max_length_chord"),
        "closed_te_metric_point_count": geo_info.get("closed_te_metric_point_count"),
        "closed_te_min_curvature_radius_chord": geo_info.get("closed_te_min_curvature_radius_chord"),
        "closed_te_boundary_layer_thickness_to_min_radius": geo_info.get("closed_te_boundary_layer_thickness_to_min_radius"),
        "closed_te_boundary_layer_self_intersection_risk": geo_info.get("closed_te_boundary_layer_self_intersection_risk"),
        "closed_te_geometry_metric_note": geo_info.get("closed_te_geometry_metric_note"),
        "effective_gmsh_mesh_algorithm_2d": int(mesh_cfg.get("gmsh_mesh_algorithm_2d", 5) or 5),
        "effective_gmsh_random_factor": float(mesh_cfg.get("gmsh_random_factor", 1.0e-7) or 0.0),
        "effective_boundary_layer_total_thickness_chord_override": (
            None
            if mesh_cfg.get("boundary_layer_total_thickness_chord_override") is None
            else float(mesh_cfg.get("boundary_layer_total_thickness_chord_override"))
        ),
        "effective_boundary_layer_growth": float(mesh_cfg.get("boundary_layer_growth", 0.0) or 0.0),
        "effective_first_cell_height_chord": float(mesh_cfg.get("first_cell_height_chord_override", 0.0) or 0.0),
        "effective_surface_size_general_chord": float(
            (geo_info.get("surface_size_from_boundary_layer") or {}).get(
                "active_surface_size_chord",
                mesh_cfg.get("surface_size_general_chord", 0.0),
            )
            or 0.0
        ),
        "effective_farfield_size_chord": float(mesh_cfg.get("farfield_size_chord", 0.0) or 0.0),
        "effective_debug_domain_radius_chord": float(mesh_cfg.get("debug_domain_radius_chord", 0.0) or 0.0),
        "effective_debug_max_profile_points": int(mesh_cfg.get("debug_max_profile_points", 0) or 0),
        "boundary_layer_layers_created": False,
        "boundary_layer_confirmation_basis": "not_checked_yet",
        "wake_refinement_requested": bool(geo_info.get("wake_refinement_requested", False)),
        "wake_refinement_length_chord": float(geo_info.get("wake_refinement_length_chord", 0.0) or 0.0),
        "wake_refinement_height_chord": float(geo_info.get("wake_refinement_height_chord", 0.0) or 0.0),
        "wake_size_chord": float(geo_info.get("wake_size_chord", 0.0) or 0.0),
        "airfoil_curve_mode": geo_info.get("airfoil_curve_mode"),
        "airfoil_curve_mode_requested": geo_info.get("airfoil_curve_mode_requested"),
        "closed_wall_curve_method": geo_info.get("closed_wall_curve_method"),
        "closed_wall_target_nodes": geo_info.get("closed_wall_target_nodes"),
        "closed_te_bump_strength": geo_info.get("closed_te_bump_strength"),
        "closed_profile_target_points": geo_info.get("closed_profile_target_points"),
        "closed_profile_min_spacing_chord": geo_info.get("closed_profile_min_spacing_chord"),
        "rounded_te_curve_sections_enforced": bool(geo_info.get("rounded_te_curve_sections_enforced", False)),
        "airfoil_curve_count": int(geo_info.get("airfoil_curve_count", 0) or 0),
        "airfoil_curve_ids": geo_info.get("airfoil_curve_ids", []),
        "airfoil_transfinite_curve_nodes": geo_info.get("airfoil_transfinite_curve_nodes", {}),
        "airfoil_transfinite_curve_distributions": geo_info.get("airfoil_transfinite_curve_distributions", {}),
        "airfoil_transfinite_node_multiplier": float(geo_info.get("airfoil_transfinite_node_multiplier", 0.0) or 0.0),
        "closed_airfoil_transfinite_enabled": bool(geo_info.get("closed_airfoil_transfinite_enabled", False)),
        "closed_airfoil_target_nodes": int(geo_info.get("closed_airfoil_target_nodes", 0) or 0),
        "closed_airfoil_transfinite_progression": float(geo_info.get("closed_airfoil_transfinite_progression", 1.0) or 1.0),
        "closed_te_target_nodes": int(geo_info.get("closed_te_target_nodes", 0) or 0),
        "closed_te_transition_min_nodes": int(geo_info.get("closed_te_transition_min_nodes", 0) or 0),
        "closed_single_curve_experimental": bool(geo_info.get("closed_single_curve_experimental", False)),
        "closed_single_curve_kind": geo_info.get("closed_single_curve_kind"),
        "closed_single_curve_target_nodes": int(geo_info.get("closed_single_curve_target_nodes", 0) or 0),
        "closed_single_curve_start_at_te": bool(geo_info.get("closed_single_curve_start_at_te", False)),
        "closed_single_curve_distribution": geo_info.get("closed_single_curve_distribution"),
        "closed_single_curve_bump": float(geo_info.get("closed_single_curve_bump", 0.0) or 0.0),
        "closed_te_neighbor_bump_enabled": bool(geo_info.get("closed_te_neighbor_bump_enabled", False)),
        "closed_te_neighbor_bump": float(geo_info.get("closed_te_neighbor_bump", 0.0) or 0.0),
        "closed_te_cap_distribution": geo_info.get("closed_te_cap_distribution"),
        "closed_te_cap_progression": float(geo_info.get("closed_te_cap_progression", 1.0) or 1.0),
        "surface_size_from_boundary_layer": geo_info.get("surface_size_from_boundary_layer"),
        "closed_te_cap_curve_ids": geo_info.get("closed_te_cap_curve_ids", []),
        "gmsh_curve_connectivity_valid": bool(geo_info.get("gmsh_curve_connectivity_valid", False)),
        "gmsh_curve_connectivity_issue_count": int(geo_info.get("gmsh_curve_connectivity_issue_count", 0) or 0),
        "gmsh_curve_connectivity_issues": geo_info.get("gmsh_curve_connectivity_issues", []),
        "gmsh_curve_connectivity_audit_json": geo_info.get("gmsh_curve_connectivity_audit_json"),
        "gmsh_curve_connectivity_audit_csv": geo_info.get("gmsh_curve_connectivity_audit_csv"),
        "gmsh_manual_discretization_note": geo_info.get("gmsh_manual_discretization_note"),
        "hybrid_te_line_curve_count": int(geo_info.get("hybrid_te_line_curve_count", 0) or 0),
        "hybrid_te_spline_curve_count": int(geo_info.get("hybrid_te_spline_curve_count", 0) or 0),
        "boundary_layer_fan_at_le": bool(geo_info.get("boundary_layer_fan_at_le", False)),
        "boundary_layer_fan_at_te": bool(geo_info.get("boundary_layer_fan_at_te", False)),
        "boundary_layer_te_fan_suppressed_for_rounded_cap": bool(geo_info.get("boundary_layer_te_fan_suppressed_for_rounded_cap", False)),
        "boundary_layer_te_fan_points": int(geo_info.get("boundary_layer_te_fan_points", 0) or 0),
        "nearfield_refinement_requested": bool(geo_info.get("nearfield_refinement_requested", False)),
        "nearfield_dist_min_chord": float(geo_info.get("nearfield_dist_min_chord", 0.0) or 0.0),
        "nearfield_intermediate_dist_chord": float(geo_info.get("nearfield_intermediate_dist_chord", 0.0) or 0.0),
        "nearfield_dist_max_chord": float(geo_info.get("nearfield_dist_max_chord", 0.0) or 0.0),
        "nearfield_intermediate_size_chord": float(geo_info.get("nearfield_intermediate_size_chord", 0.0) or 0.0),
        "nearfield_outer_size_chord": float(geo_info.get("nearfield_outer_size_chord", 0.0) or 0.0),
        "farfield_transition_dist_chord": float(geo_info.get("farfield_transition_dist_chord", 0.0) or 0.0),
        "first_cell_height_actual": None,
        "fabric_thickness_resolved_by_at_least_N_cells_or_warning": float(mesh_cfg.get("fabric_thickness_chord", 1e-5)) > 0,
        "surface_kind": geo_info.get("surface_kind"),
        "diagnostic_geometry_only": bool(geo_info.get("diagnostic_only", False)),
        "openfoam_ready": bool(geo_info.get("openfoam_ready", not bool(geo_info.get("diagnostic_only", False)))),
        "extruded_3d": bool(geo_info.get("extruded_3d", False)),
        "spanwise_layers": int(geo_info.get("spanwise_layers", 0) or 0),
        "spanwise_thickness_chord": float(geo_info.get("spanwise_thickness_chord", 0.0) or 0.0),
        "open_wall_curve_method": geo_info.get("open_wall_curve_method"),
        "open_boundary_layer_curve_policy": geo_info.get("open_boundary_layer_curve_policy"),
        "open_fluid_topology": geo_info.get("open_fluid_topology"),
        "open_connected_fluid_surface": bool(geo_info.get("open_connected_fluid_surface", False)),
        "open_thin_solid_fluid_surface": bool(geo_info.get("open_thin_solid_fluid_surface", False)),
        "open_geometry_representation": geo_info.get("open_geometry_representation"),
        "open_base_profile_variant": geo_info.get("open_base_profile_variant"),
        "open_base_profile_chord_m": geo_info.get("open_base_profile_chord_m"),
        "base_inlet_control_points": geo_info.get("base_inlet_control_points"),
        "base_inlet_arc_length_chord": geo_info.get("base_inlet_arc_length_chord"),
        "base_inlet_alignment_mode": geo_info.get("base_inlet_alignment_mode"),
        "base_inlet_exact_similarity_of_uncut_arc": bool(
            geo_info.get("base_inlet_exact_similarity_of_uncut_arc", False)
        ),
        "base_inlet_similarity_scale": geo_info.get("base_inlet_similarity_scale"),
        "base_inlet_similarity_rotation_deg": geo_info.get(
            "base_inlet_similarity_rotation_deg"
        ),
        "base_inlet_blend_fraction": geo_info.get("base_inlet_blend_fraction"),
        "base_inlet_raw_lower_anchor_error_m": geo_info.get(
            "base_inlet_raw_lower_anchor_error_m"
        ),
        "base_inlet_raw_upper_anchor_error_m": geo_info.get(
            "base_inlet_raw_upper_anchor_error_m"
        ),
        "base_inlet_lower_tangent_mismatch_deg": geo_info.get(
            "base_inlet_lower_tangent_mismatch_deg"
        ),
        "base_inlet_upper_tangent_mismatch_deg": geo_info.get(
            "base_inlet_upper_tangent_mismatch_deg"
        ),
        "base_inlet_near_duplicate_points_removed": geo_info.get("base_inlet_near_duplicate_points_removed"),
        "base_inlet_self_intersections": geo_info.get("base_inlet_self_intersections"),
        "open_selective_inlet_interface_merge": bool(geo_info.get("open_selective_inlet_interface_merge", False)),
        "open_inlet_interface_physical_names": geo_info.get("open_inlet_interface_physical_names", []),
        "open_effective_fabric_thickness_chord": float(geo_info.get("open_effective_fabric_thickness_chord", 0.0) or 0.0),
        "open_requested_fabric_thickness_chord": float(geo_info.get("open_requested_fabric_thickness_chord", 0.0) or 0.0),
        "open_fabric_offset_thickness_reductions": int(geo_info.get("open_fabric_offset_thickness_reductions", 0) or 0),
        "open_fabric_offset_self_intersections": int(geo_info.get("open_fabric_offset_self_intersections", 0) or 0),
        "open_fabric_offset_cross_intersections": int(geo_info.get("open_fabric_offset_cross_intersections", 0) or 0),
        "open_exterior_normal_upper_valid": bool(geo_info.get("open_exterior_normal_upper_valid", False)),
        "open_exterior_normal_lower_valid": bool(geo_info.get("open_exterior_normal_lower_valid", False)),
        "open_boundary_layer_restricted_to_external_side": bool(geo_info.get("open_boundary_layer_restricted_to_external_side", False)),
        "open_inlet_boundary_layer_mode": geo_info.get("open_inlet_boundary_layer_mode"),
        "open_inlet_transition_elements": geo_info.get("open_inlet_transition_elements"),
        "open_inlet_bridge_smoothing_enabled": bool(geo_info.get("open_inlet_bridge_smoothing_enabled", False)),
        "open_inlet_bridge_smoothing_handle_fraction": float(
            geo_info.get("open_inlet_bridge_smoothing_handle_fraction", 0.0) or 0.0
        ),
        "open_inlet_bridge_curve_kind": geo_info.get("open_inlet_bridge_curve_kind"),
        "open_lip_cap_rounding_enabled": bool(geo_info.get("open_lip_cap_rounding_enabled", False)),
        "open_lip_cap_rounding_points": int(geo_info.get("open_lip_cap_rounding_points", 2) or 2),
        "open_inlet_transition_distribution": geo_info.get("open_inlet_transition_distribution"),
        "open_cavity_size_field_restricted_to_cavity": bool(geo_info.get("open_cavity_size_field_restricted_to_cavity", False)),
        "open_interface_sizes_from_boundary_layer": bool(geo_info.get("open_interface_sizes_from_boundary_layer", False)),
        "open_te_interface_size_chord": float(geo_info.get("open_te_interface_size_chord", 0.0) or 0.0),
        "open_te_tangential_spacing_chord": float(geo_info.get("open_te_tangential_spacing_chord", 0.0) or 0.0),
        "open_inlet_interface_size_chord": float(geo_info.get("open_inlet_interface_size_chord", 0.0) or 0.0),
        "open_inlet_tangential_spacing_chord": float(geo_info.get("open_inlet_tangential_spacing_chord", 0.0) or 0.0),
        "open_lip_cap_interface_size_chord": float(geo_info.get("open_lip_cap_interface_size_chord", 0.0) or 0.0),
        "open_duplicate_inlet_interface_for_one_sided_bl": bool(geo_info.get("open_duplicate_inlet_interface_for_one_sided_bl", False)),
        "open_inlet_bridge_embedded_in_single_fluid_surface": bool(geo_info.get("open_inlet_bridge_embedded_in_single_fluid_surface", False)),
        "open_boundary_layer_single_loop_bspline": bool(geo_info.get("open_boundary_layer_single_loop_bspline", False)),
        "open_boundary_layer_single_loop_curve_kind": geo_info.get("open_boundary_layer_single_loop_curve_kind"),
        "open_boundary_layer_single_loop_transfinite": bool(geo_info.get("open_boundary_layer_single_loop_transfinite", False)),
        "open_diagnostic_loop_closed": bool(geo_info.get("open_diagnostic_loop_closed", False)),
        "open_diagnostic_loop_curve_count": int(geo_info.get("open_diagnostic_loop_curve_count", 0) or 0),
        "open_boundary_layer_curve_count": int(geo_info.get("open_boundary_layer_curve_count", 0) or 0),
        "open_boundary_layer_split_curvature_sections": bool(geo_info.get("open_boundary_layer_split_curvature_sections", False)),
        "open_te_boundary_curve_id": geo_info.get("open_te_boundary_curve_id"),
        "open_te_transfinite_min_nodes": int(geo_info.get("open_te_transfinite_min_nodes", 0) or 0),
        "open_te_refinement_width_chord": float(geo_info.get("open_te_refinement_width_chord", 0.0) or 0.0),
        "open_te_transition_distance_chord": float(geo_info.get("open_te_transition_distance_chord", 0.0) or 0.0),
        "open_lip_transfinite_min_nodes": int(geo_info.get("open_lip_transfinite_min_nodes", 0) or 0),
        "open_surface_target_nodes": int(geo_info.get("open_surface_target_nodes", 0) or 0),
        "open_surface_transfinite_progression": float(geo_info.get("open_surface_transfinite_progression", 1.0) or 1.0),
        "open_outer_wall_transfinite_curve_nodes": geo_info.get("open_outer_wall_transfinite_curve_nodes", {}),
        "open_outer_wall_transfinite_distributions": geo_info.get("open_outer_wall_transfinite_distributions", {}),
        "open_wall_end_bump_enabled": bool(geo_info.get("open_wall_end_bump_enabled", False)),
        "open_wall_end_bump_strength": float(geo_info.get("open_wall_end_bump_strength", 0.0) or 0.0),
        "open_inner_wall_transfinite_curve_nodes": geo_info.get("open_inner_wall_transfinite_curve_nodes", {}),
        "open_inner_wall_transfinite_distributions": geo_info.get("open_inner_wall_transfinite_distributions", {}),
        "open_inner_wall_end_bump_enabled": bool(geo_info.get("open_inner_wall_end_bump_enabled", False)),
        "open_inner_wall_end_bump_strength": float(geo_info.get("open_inner_wall_end_bump_strength", 0.0) or 0.0),
        "open_inner_wall_node_factor": float(geo_info.get("open_inner_wall_node_factor", 0.0) or 0.0),
        "open_inner_te_node_factor": float(geo_info.get("open_inner_te_node_factor", 0.0) or 0.0),
        "open_inner_wall_min_nodes": int(geo_info.get("open_inner_wall_min_nodes", 0) or 0),
        "open_inner_te_min_nodes": int(geo_info.get("open_inner_te_min_nodes", 0) or 0),
        "open_surface_transfinite_multiplier": float(geo_info.get("open_surface_transfinite_multiplier", 1.0) or 1.0),
        "open_boundary_layer_aniso_max_deg": float(geo_info.get("open_boundary_layer_aniso_max_deg", 0.0) or 0.0),
        "open_diagnostic_boundary_layer_enabled": bool(geo_info.get("open_diagnostic_boundary_layer_enabled", True)),
        "open_boundary_layer_curve_ids": geo_info.get("open_boundary_layer_curve_ids", []),
        "open_boundary_layer_excluded_te_curve_ids": geo_info.get("open_boundary_layer_excluded_te_curve_ids", []),
        "open_boundary_layer_exclude_te_cap_from_bl": bool(geo_info.get("open_boundary_layer_exclude_te_cap_from_bl", False)),
        "open_boundary_layer_trim_end_segments": bool(geo_info.get("open_boundary_layer_trim_end_segments", False)),
        "open_boundary_layer_trim_ends_chord": float(geo_info.get("open_boundary_layer_trim_ends_chord", 0.0) or 0.0),
        "open_inlet_marker_curve_ids": geo_info.get("open_inlet_marker_curve_ids", []),
        "open_internal_cavity_meshed": bool(geo_info.get("open_internal_cavity_meshed", False)),
        "open_internal_te_refinement_requested": bool(geo_info.get("open_internal_te_refinement_requested", False)),
        "open_internal_te_curve_ids": geo_info.get("open_internal_te_curve_ids", []),
        "open_internal_te_interface_size_chord": geo_info.get("open_internal_te_interface_size_chord"),
        "open_internal_te_dist_max_chord": float(geo_info.get("open_internal_te_dist_max_chord", 0.0) or 0.0),
        "open_inlet_transition_mesh": geo_info.get("open_inlet_transition_mesh"),
        "open_internal_cavity_curve_count": int(geo_info.get("open_internal_cavity_curve_count", 0) or 0),
        "open_internal_cavity_curve_mode": geo_info.get("open_internal_cavity_curve_mode"),
        "open_internal_cavity_shares_inlet_marker": bool(geo_info.get("open_internal_cavity_shares_inlet_marker", False)),
        "open_internal_cavity_duplicate_inlet_marker": bool(geo_info.get("open_internal_cavity_duplicate_inlet_marker", False)),
        "open_internal_cavity_solver_connected": bool(geo_info.get("open_internal_cavity_solver_connected", False)),
        "open_internal_cavity_note": geo_info.get("open_internal_cavity_note"),
        "open_wall_curve_ids": geo_info.get("open_wall_curve_ids", []),
        "open_te_rounding_enabled": bool(geo_info.get("open_te_rounding_enabled", False)),
        "open_te_rounding_applied": bool(geo_info.get("open_te_rounding_applied", False)),
        "open_te_rounding_points_added": int(geo_info.get("open_te_rounding_points_added", 0) or 0),
        "open_te_rounding_note": geo_info.get("open_te_rounding_note"),
        "open_te_rounding_transfinite_nodes": int(geo_info.get("open_te_rounding_transfinite_nodes", 0) or 0),
        "open_effective_boundary_layer_growth": float(mesh_cfg.get("open_boundary_layer_growth", mesh_cfg.get("boundary_layer_growth", 0.0)) or 0.0),
        "open_effective_first_cell_height_chord": float(geo_info.get("boundary_layer_first_cell_height_chord", 0.0) or 0.0),
        "open_effective_boundary_layer_total_thickness_chord_override": (
            None
            if mesh_cfg.get("open_boundary_layer_total_thickness_chord_override", mesh_cfg.get("boundary_layer_total_thickness_chord_override")) is None
            else float(mesh_cfg.get("open_boundary_layer_total_thickness_chord_override", mesh_cfg.get("boundary_layer_total_thickness_chord_override")))
        ),
        "open_effective_surface_size_general_chord": float(
            (geo_info.get("open_surface_size_from_boundary_layer") or {}).get(
                "active_surface_size_chord",
                mesh_cfg.get("open_surface_size_general_chord", mesh_cfg.get("surface_size_general_chord", 0.0)),
            )
            or 0.0
        ),
        "open_surface_size_from_boundary_layer": geo_info.get("open_surface_size_from_boundary_layer"),
        "open_effective_surface_size_le_chord": float(mesh_cfg.get("open_surface_size_le_chord", 0.0) or 0.0),
        "open_effective_surface_size_lip_chord": float(mesh_cfg.get("open_surface_size_lip_chord", 0.0) or 0.0),
        "open_effective_cavity_size_chord": float(mesh_cfg.get("open_cavity_size_chord", mesh_cfg.get("cavity_size_chord", 0.0)) or 0.0),
        "open_effective_farfield_size_chord": float(mesh_cfg.get("open_farfield_size_chord", mesh_cfg.get("farfield_size_chord", 0.0)) or 0.0),
        "open_le_refinement_requested": bool(geo_info.get("open_le_refinement_requested", False)),
        "open_le_refinement_width_chord": float(geo_info.get("open_le_refinement_width_chord", 0.0) or 0.0),
        "open_lip_refinement_requested": bool(geo_info.get("open_lip_refinement_requested", False)),
        "open_internal_inlet_refinement_requested": bool(geo_info.get("open_internal_inlet_refinement_requested", False)),
        "open_internal_inlet_refinement_kind": geo_info.get("open_internal_inlet_refinement_kind"),
        "open_inlet_interface_tangential_size_chord": geo_info.get(
            "open_inlet_interface_tangential_size_chord"
        ),
        "open_internal_inlet_active_size_chord": geo_info.get("open_internal_inlet_active_size_chord"),
        "open_internal_inlet_normal_size_rule": geo_info.get("open_internal_inlet_normal_size_rule"),
        "open_internal_inlet_boundary_size_source": geo_info.get(
            "open_internal_inlet_boundary_size_source"
        ),
        "open_cavity_inlet_size_strategy": geo_info.get(
            "open_cavity_inlet_size_strategy"
        ),
        "open_cavity_inlet_extension_power": geo_info.get(
            "open_cavity_inlet_extension_power"
        ),
        "open_internal_inlet_normal_y1_factor": geo_info.get("open_internal_inlet_normal_y1_factor"),
        "open_internal_inlet_last_bl_height_chord": geo_info.get("open_internal_inlet_last_bl_height_chord"),
        "open_internal_inlet_matching_transition_chord": float(
            geo_info.get("open_internal_inlet_matching_transition_chord", 0.0) or 0.0
        ),
        "open_internal_inlet_matching_size_factor": float(
            geo_info.get("open_internal_inlet_matching_size_factor", 0.0) or 0.0
        ),
        "open_internal_inlet_matching_size_chord": float(
            geo_info.get("open_internal_inlet_matching_size_chord", 0.0) or 0.0
        ),
        "open_zero_thickness_contour_target_nodes": int(
            geo_info.get("open_zero_thickness_contour_target_nodes", 0) or 0
        ),
        "open_zero_thickness_contour_realized_segments": int(
            geo_info.get("open_zero_thickness_contour_realized_segments", 0) or 0
        ),
        "open_zero_thickness_uniform_spacing_chord": float(
            geo_info.get("open_zero_thickness_uniform_spacing_chord", 0.0) or 0.0
        ),
        "open_zero_thickness_minimum_control_segment_chord": float(
            geo_info.get("open_zero_thickness_minimum_control_segment_chord", 0.0)
            or 0.0
        ),
        "open_zero_thickness_duplicate_control_points": int(
            geo_info.get("open_zero_thickness_duplicate_control_points", 0)
            or 0
        ),
        "open_zero_thickness_realized_spacing_ratio": float(
            geo_info.get("open_zero_thickness_realized_spacing_ratio", 0.0)
            or 0.0
        ),
        "open_zero_thickness_realized_curve_spacing_chord": geo_info.get(
            "open_zero_thickness_realized_curve_spacing_chord", {}
        ),
        "open_zero_thickness_curve_lengths_chord": geo_info.get(
            "open_zero_thickness_curve_lengths_chord", {}
        ),
        "open_zero_thickness_curve_nodes": geo_info.get(
            "open_zero_thickness_curve_nodes", {}
        ),
        "open_wall_external_nodes": geo_info.get("open_wall_external_nodes", {}),
        "open_wall_internal_nodes": geo_info.get("open_wall_internal_nodes", {}),
        "open_internal_inlet_near_transition_chord": float(
            geo_info.get("open_internal_inlet_near_transition_chord", 0.0) or 0.0
        ),
        "open_internal_inlet_intermediate_size_chord": float(
            geo_info.get("open_internal_inlet_intermediate_size_chord", 0.0) or 0.0
        ),
        "open_internal_inlet_dist_max_chord": float(
            geo_info.get("open_internal_inlet_dist_max_chord", 0.0) or 0.0
        ),
        "open_transition_sigmoid_enabled": bool(
            geo_info.get("open_transition_sigmoid_enabled", False)
        ),
        "open_internal_inlet_refinement_scope": geo_info.get("open_internal_inlet_refinement_scope"),
        "open_boundary_layer_fan_at_lips": bool(geo_info.get("open_boundary_layer_fan_at_lips", False)),
        "open_boundary_layer_lip_fan_points": int(geo_info.get("open_boundary_layer_lip_fan_points", 0) or 0),
        "open_boundary_layer_inlet_marker_included": bool(geo_info.get("open_boundary_layer_inlet_marker_included", False)),
        "open_boundary_layer_inlet_bridge_in_single_loop": bool(geo_info.get("open_boundary_layer_inlet_bridge_in_single_loop", False)),
        "open_boundary_layer_inlet_bridge_included": bool(geo_info.get("open_boundary_layer_inlet_bridge_included", False)),
        "open_inlet_marker_transfinite_enabled": bool(geo_info.get("open_inlet_marker_transfinite_enabled", False)),
        "open_inlet_marker_transfinite_nodes": int(geo_info.get("open_inlet_marker_transfinite_nodes", 0) or 0),
        "open_inlet_marker_bump_strength": float(geo_info.get("open_inlet_marker_bump_strength", 0.0) or 0.0),
        "open_inlet_connector_transfinite": bool(geo_info.get("open_inlet_connector_transfinite", False)),
        "open_inlet_connector_curve_ids": geo_info.get("open_inlet_connector_curve_ids", []),
        "open_inlet_refinement_bridge_enabled": bool(geo_info.get("open_inlet_refinement_bridge_enabled", False)),
        "open_inlet_refinement_bridge_curve_ids": geo_info.get("open_inlet_refinement_bridge_curve_ids", []),
        "open_inlet_refinement_bridge_is_physical_patch": bool(geo_info.get("open_inlet_refinement_bridge_is_physical_patch", False)),
        "open_inlet_refinement_bridge_in_boundary_layer": bool(geo_info.get("open_inlet_refinement_bridge_in_boundary_layer", False)),
        "open_exterior_inlet_bridge_curve_id": int(geo_info.get("open_exterior_inlet_bridge_curve_id", 0) or 0),
        "open_transition_inlet_bridge_curve_id": int(geo_info.get("open_transition_inlet_bridge_curve_id", 0) or 0),
        "open_inlet_connector_surface_id": int(geo_info.get("open_inlet_connector_surface_id", 0) or 0),
        "open_boundary_layer_trim_end_points": int(geo_info.get("open_boundary_layer_trim_end_points", 0) or 0),
        "max_cells": int(mesh_cfg.get("max_cells", 2_000_000)),
        "min_cells_warning": int(mesh_cfg.get("min_cells_warning", 1000)),
    }


def build_mesh(args: argparse.Namespace) -> None:
    activate_openfoam_environment()
    case_root: Path = args.case_root
    variant = args.variant
    mesh_root = cfd_meshes_root(case_root) / variant
    run_id = _safe_run_id()

    if args.approve_mesh:
        report = read_json(mesh_root / "mesh_quality_report.json", {}) or {}
        mesh_file = mesh_root / "mesh_final.msh"
        status = str(report.get("status", ""))
        openfoam_check_ok = bool(
            report.get("openfoam_polyMesh_created")
            and report.get("gmshToFoam_status") == "OK"
            and report.get("checkMesh_status") == "OK"
            and report.get("frontAndBack_empty_declared", True)
            and not report.get("forbidden_ram_air_inlet_patch_present", False)
        )
        acceptable = status in {"PASS", "WARNING_ACCEPTABLE"} or openfoam_check_ok
        if not mesh_file.exists() and not args.force_approve:
            raise RuntimeError(f"Cannot approve: {mesh_file} does not exist.")
        if not acceptable and not args.force_approve:
            reason = "; ".join(str(v) for v in report.get("failed_checks", []) or []) or "see mesh_quality_report.txt"
            raise RuntimeError(
                f"Cannot approve: quality status is {status}. Failed checks: {reason}. "
                "Use --force-approve only for diagnostics, or fix the mesh and rerun gmshToFoam/checkMesh."
            )
        flag = mesh_root / "MESH_APPROVED.flag"
        flag.write_text(
            f"approved_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"variant={variant}\n"
            f"status={status}\n"
            f"openfoam_check_ok={openfoam_check_ok}\n"
            f"force_approve={args.force_approve}\n",
            encoding="utf-8",
        )
        print(f"Created {flag}")
        if status == "FAIL" and openfoam_check_ok and not args.force_approve:
            print("Quality status is FAIL, but gmshToFoam/checkMesh are OK and a real OpenFOAM polyMesh exists.")
            print("Proceeding for this debug flow. Review mesh_quality_report.txt/html before aerodynamic conclusions.")
        return

    if args.check_existing_mesh:
        check_existing_openfoam_mesh(args)
        return

    previous_action = str(getattr(args, "previous_output_action", "archive") or "archive")
    if args.overwrite and mesh_root.exists():
        backup = backup_existing_mesh_root(case_root, mesh_root, variant, previous_action)
        if backup is not None:
            print(f"Previous mesh outputs moved to backup: {backup.resolve()}")
    mesh_root.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        backup = backup_active_mesh_outputs(case_root, mesh_root, variant, run_id, previous_action)
        if backup is not None:
            print(f"Previous active mesh outputs moved to backup: {backup.resolve()}")
    points, edges, patches, manifest, source_root = load_case_variant(case_root, variant)
    original_points = points.copy()
    original_edges = edges.copy()
    mesh_cfg = load_mesh_config(case_root, args.mesh_level, getattr(args, "mesh_config", None))
    phys_for_yplus = read_json(cfd_inputs_root(case_root) / "case_package" / "physical_config.json", {}) or {}
    if isinstance(phys_for_yplus, dict):
        mesh_cfg["_physical_for_yplus"] = phys_for_yplus
    if args.force_boundary_layer:
        mesh_cfg["request_boundary_layer"] = True
        mesh_cfg["boundary_layer_layers"] = max(1, int(mesh_cfg.get("boundary_layer_layers", 0) or 0), 2)
    if args.no_boundary_layer:
        mesh_cfg["request_boundary_layer"] = False
        mesh_cfg["boundary_layer_layers"] = 0
    gmsh_threads = resolve_gmsh_threads(args, mesh_cfg)
    write_openfoam_mesh = bool((args.write_openfoam_mesh or args.check_mesh) and not args.write_2d_mesh)
    mesh_cfg["extrude_to_3d_for_openfoam"] = write_openfoam_mesh
    mesh_config_snapshot = {key: value for key, value in mesh_cfg.items() if not str(key).startswith("_")}
    mesh_config_snapshot["domain_type"] = args.domain
    mesh_config_snapshot["mesh_level_used"] = args.mesh_level
    write_json(mesh_root / "mesh_config_used.json", mesh_config_snapshot)
    simplification_info = {
        "profile_simplification_applied": False,
        "profile_points_original": int(len(points)),
        "profile_edges_original": int(len(edges)),
        "profile_points_simplified": int(len(points)),
        "profile_edges_simplified": int(len(edges)),
    }
    is_open_variant = bool(
        manifest.get(
            "has_ram_air_opening_feature",
            variant in {"open_ramair", "ross_standard_8p4", "ross_minimum_4p0", "standard", "optimized"},
        )
    )
    open_geometry_representation = str(
        mesh_cfg.get("open_geometry_representation", "finite_thickness_fabric")
    ).strip()
    if open_geometry_representation not in {
        "zero_thickness_base_profile",
        "finite_thickness_fabric",
    }:
        raise ValueError(
            "open_geometry_representation must be zero_thickness_base_profile "
            "or finite_thickness_fabric."
        )
    base_profile_package: tuple[pd.DataFrame, pd.DataFrame, dict, dict, Path] | None = None
    if is_open_variant and open_geometry_representation == "zero_thickness_base_profile":
        base_variant = str(mesh_cfg.get("open_base_profile_variant", "reference_uncut") or "reference_uncut")
        base_profile_package = load_case_variant(case_root, base_variant)
    python_extrude_open_mesh = bool(
        write_openfoam_mesh
        and not (is_open_variant and args.open_diagnostic_mesh)
        and (not is_open_variant or bool(mesh_cfg.get("open_thin_solid_fluid_surface", True)))
    )
    preprocess_closed_profile = bool(
        not is_open_variant
        and mesh_cfg.get(
            "closed_profile_preprocess_enabled",
            mesh_cfg.get("debug_simplify_profile", False),
        )
    )
    if preprocess_closed_profile:
        points, edges, simplification_info = simplify_closed_loop_for_debug(
            points,
            edges,
            int(mesh_cfg.get("debug_max_profile_points", 80)),
            float(mesh_cfg.get("debug_profile_min_spacing_chord", 2.0e-4)),
            te_rounding_enabled=bool(mesh_cfg.get("debug_te_rounding_enabled", False)),
            te_rounding_points=int(mesh_cfg.get("debug_te_rounding_points", 17)),
            te_rounding_window_chord=float(mesh_cfg.get("debug_te_rounding_window_chord", 0.04)),
            te_rounding_min_gap_chord=float(mesh_cfg.get("debug_te_rounding_min_gap_chord", 2.0e-4)),
            te_refinement_width_chord=float(mesh_cfg.get("debug_te_refinement_width_chord", 0.035)),
            te_refinement_strength=float(mesh_cfg.get("debug_te_refinement_strength", 10.0)),
            te_refinement_max_weight=float(mesh_cfg.get("debug_te_refinement_max_weight", 14.0)),
        )
        write_profile_preprocessing_outputs(original_points, original_edges, points, edges, mesh_root, simplification_info)
        if bool(mesh_cfg.get("closed_te_rounding_enabled", mesh_cfg.get("debug_te_rounding_enabled", False))):
            if not bool(simplification_info.get("te_rounding_applied", False)):
                raise RuntimeError(
                    "Closed TE rounding is enabled but no tangent-continuous cap was applied: "
                    f"{simplification_info.get('te_rounding_note', 'unknown reason')}. "
                    "Inspect the ordered profile and disable TE rounding explicitly only for a genuinely sharp TE."
                )
    write_profile_audit(points, edges, mesh_root, variant)

    max_attempts = max(1, int(args.max_remesh_attempts if args.auto_remesh else 1))
    history = []
    final_attempt_dir = None
    for attempt in range(1, max_attempts+1):
        adir = mesh_root / f"mesh_attempt_{attempt:03d}"
        attempt_backup = backup_existing_attempt_dir(case_root, adir, variant, attempt, run_id)
        if attempt_backup is not None:
            print(f"Previous mesh attempt moved to backup: {attempt_backup.resolve()}")
        adir.mkdir(parents=True, exist_ok=True)
        geo_path = adir / "mesh.geo"
        is_open = is_open_variant
        if (
            is_open
            and not args.open_diagnostic_mesh
            and open_geometry_representation == "zero_thickness_base_profile"
        ):
            if base_profile_package is None:
                raise RuntimeError("The zero-thickness open mode did not load its base profile package.")
            base_points, base_edges, _, base_manifest, base_source_root = base_profile_package
            geo_info = write_geo_open_zero_thickness_base_profile(
                points,
                edges,
                manifest,
                base_points,
                base_edges,
                base_manifest,
                mesh_cfg,
                args.domain,
                geo_path,
                variant,
            )
            geo_info["open_base_profile_source_root"] = str(base_source_root)
            open_note = (
                "Open profile written without artificial fabric thickness. The exterior BL follows the "
                "uncut base-profile inlet arc; only the actual cut wall becomes airfoil_wall, and the "
                "cavity/exterior inlet interface is stitched before one-cell extrusion."
            )
        elif is_open and not args.open_diagnostic_mesh and bool(mesh_cfg.get("open_thin_solid_fluid_surface", True)):
            geo_info = write_geo_open_thin_solid(
                points,
                edges,
                manifest,
                mesh_cfg,
                args.domain,
                geo_path,
                variant,
                openfoam_3d=bool(write_openfoam_mesh and not python_extrude_open_mesh),
                single_surface_for_mesh_extrusion=bool(mesh_cfg.get("open_single_connected_surface_2d", False)),
            )
            open_note = (
                "Open profile written as finite-thickness fabric band with conformal exterior, inlet and cavity surfaces. "
                "For OpenFOAM, the Gmsh 2D mesh is extruded as one connectivity-preserving cell layer."
            )
        elif is_open and not args.open_diagnostic_mesh:
            # Avoid pretending the open zero-thickness profile is a valid OpenFOAM mesh.
            geo_info = write_geo_open_diagnostic(points, edges, manifest, mesh_cfg, args.domain, geo_path, variant)
            open_note = "Open profile written as diagnostic embedded-curve geometry. OpenFOAM-ready open ram-air requires finite-thickness fabric or baffle topology; zero-thickness open profiles are not approved."
        elif is_open:
            geo_info = write_geo_open_diagnostic(points, edges, manifest, mesh_cfg, args.domain, geo_path, variant)
            open_note = "Diagnostic open-profile mesh requested. inlet_opening_marker is not a physical patch and ram_air_inlet is forbidden."
        else:
            geo_info = write_geo_closed(
                points,
                edges,
                manifest,
                mesh_cfg,
                args.domain,
                geo_path,
                variant,
                openfoam_3d=bool(write_openfoam_mesh and not python_extrude_open_mesh),
            )
            open_note = None

        report = basic_report(points, edges, variant, geo_info, mesh_cfg)
        selected_domain = domain_params(args.domain, mesh_cfg)
        effective_domain_radius = None
        if selected_domain.get("type") == "circle":
            effective_domain_radius = (
                float(mesh_cfg.get("debug_domain_radius_chord", selected_domain["radius"]))
                if args.domain == "debug_20c"
                else float(selected_domain["radius"])
            )
        report.update(simplification_info)
        report.update({
            "attempt": attempt,
            "mesh_run_id": run_id,
            "domain": args.domain,
            "domain_type": selected_domain.get("type"),
            "effective_domain_radius_chord": effective_domain_radius,
            "domain_dimensions_chord": selected_domain,
            "source_geometry_root": str(source_root),
            "mesh_config_source": str(mesh_cfg.get("_mesh_config_source", "")),
            "mesh_config_override_requested": bool(mesh_cfg.get("_mesh_config_override_requested", False)),
            "geo_file": str(geo_path),
            "dry_run": bool(args.dry_run),
            "open_profile_note": open_note,
            "openfoam_mesh_requested": write_openfoam_mesh,
            "python_extrude_open_mesh": bool(python_extrude_open_mesh),
            "python_extrude_mesh": bool(python_extrude_open_mesh),
        })
        geo_path.write_text(geo_path.read_text(encoding="utf-8") + f"\n// RAMAIR_MESH_RUN_ID = {run_id}\n// RAMAIR_MESH_ATTEMPT = {attempt}\n", encoding="utf-8")
        # first cell record
        try:
            phys = read_json(cfd_inputs_root(case_root)/"case_package"/"physical_config.json", {}) or {}
            Re = _as_float(phys.get("reynolds"), 4e6); rho = _as_float(phys.get("rho"), 1.225); mu = _as_float(phys.get("mu"), 1.81e-5); chord_m = _as_float(manifest.get("chord_m"), 1.0)
            report["first_cell_height_formula_audit"] = first_cell_height_audit(
                Re, chord_m, _as_float(mesh_cfg.get("target_y_plus"), 0.5), rho, mu
            )
            if geo_info.get("boundary_layer_first_cell_height_chord"):
                report["first_cell_height_actual"] = float(geo_info["boundary_layer_first_cell_height_chord"]) * chord_m
            else:
                y1c = mesh_cfg.get("first_cell_height_chord_override")
                report["first_cell_height_actual"] = float(y1c)*chord_m if y1c is not None else estimate_first_cell_height_from_yplus(Re, chord_m, _as_float(mesh_cfg.get("target_y_plus"),0.5), rho, mu)
        except Exception as exc:
            report["first_cell_height_error"] = str(exc)
        if args.plot:
            plot_previews(points, edges, adir, variant)

        if not args.dry_run:
            if is_windows_mounted_wsl_path(Path(case_root)) and not args.allow_windows_mount:
                raise RuntimeError(
                    "Refusing to run Gmsh from a Windows-mounted WSL path (/mnt/c or /mnt/d). "
                    "Copy the project to a native Linux path such as '~/ramair_cfd/DESIGN_APP' and rerun, "
                    "or pass --allow-windows-mount only for path debugging."
                )
            gmsh_backend = resolve_gmsh_backend(args, mesh_cfg)
            report["gmsh_backend"] = gmsh_backend
            gmsh = resolve_gmsh_executable(args) if gmsh_backend == "cli" else str(gmsh_python_worker())
            if not gmsh or (gmsh_backend == "python_api" and not gmsh_python_worker().is_file()):
                report["gmsh_error"] = f"Gmsh {gmsh_backend} backend not found"
                report["mesh_file_created"] = False
            else:
                report["gmsh_executable"] = str(Path(gmsh).resolve())
                version_info = gmsh_api_version_info() if gmsh_backend == "python_api" else gmsh_version_info(gmsh)
                report.update(version_info)
                version_tuple = tuple(version_info.get("gmsh_version_tuple") or [])
                if version_info.get("gmsh_version_command_exit_code") != 0 or not version_info.get("gmsh_version"):
                    report["gmsh_error"] = f"gmsh_{gmsh_backend}_version_check_failed"
                    report["gmsh_failure_stage"] = "environment_version_check"
                    report["gmsh_failure_comment"] = (
                        "The selected Gmsh Python API is not importable in this environment. "
                        "Run bash 'Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh' --install or select --gmsh-backend cli."
                        if gmsh_backend == "python_api"
                        else "The selected Gmsh executable could not report a version."
                    )
                    report["mesh_file_created"] = False
                    code = 995
                elif (
                    report.get("boundary_layer_requested")
                    and version_tuple
                    and version_tuple < (4, 10, 0)
                    and not args.allow_legacy_gmsh_boundary_layer
                ):
                    report["gmsh_error"] = "legacy_gmsh_boundary_layer_rejected"
                    report["gmsh_failure_stage"] = "environment_version_check"
                    report["gmsh_failure_comment"] = (
                        f"Gmsh {version_info.get('gmsh_version')} is too old for this curved BoundaryLayer workflow. "
                        "The same closed TE case was validated with Gmsh 4.15.2; Gmsh 4.8.4 generated collapsed "
                        "triangles and edge-recovery errors. Install the project-local 4.15.2 binary with "
                        "Documents and Manuals/Application/install_gmsh_4_15_wsl.sh, or use --allow-legacy-gmsh-boundary-layer only for diagnosis."
                    )
                    report["mesh_file_created"] = False
                    code = 996
                else:
                    code = None
                msh_path = adir / "mesh.msh"
                if msh_path.exists():
                    msh_path.unlink()
                dimension = 2 if python_extrude_open_mesh else (3 if write_openfoam_mesh else 2)
                dim_flag = f"-{dimension}"
                report["gmsh_working_directory"] = str(adir.resolve())
                report["gmsh_input_exists_before_run"] = bool(geo_path.exists())
                report["gmsh_timeout_s"] = int(args.gmsh_timeout_s)
                report["gmsh_threads_requested"] = int(gmsh_threads)
                temp_dir: Path | None = None
                gmsh_cwd = adir
                gmsh_geo = geo_path
                gmsh_msh = msh_path
                if args.gmsh_temp_workdir:
                    temp_dir = Path(tempfile.mkdtemp(prefix="ramair_gmsh_"))
                    gmsh_cwd = temp_dir
                    gmsh_geo = temp_dir / "mesh.geo"
                    gmsh_msh = temp_dir / "mesh.msh"
                    shutil.copy2(geo_path, gmsh_geo)
                report["gmsh_temp_workdir_used"] = bool(args.gmsh_temp_workdir)
                report["gmsh_execution_directory"] = str(gmsh_cwd.resolve())
                # Run Gmsh from the selected execution directory and pass local file names.
                if gmsh_backend == "python_api":
                    cmd = gmsh_api_command(geo_path.name, msh_path.name, dimension, int(gmsh_threads))
                else:
                    cmd = [gmsh, "-nt", str(int(gmsh_threads))]
                    cmd += [geo_path.name, dim_flag, "-format", "msh2", "-o", msh_path.name, "-v", "4"]
                if args.benchmark_gmsh_threads and code is None:
                    if gmsh_backend == "cli":
                        run_gmsh_thread_benchmark(
                            gmsh,
                            gmsh_geo,
                            adir,
                            dim_flag,
                            parse_thread_sweep(args.gmsh_thread_sweep),
                            timeout_s=max(60, int(args.gmsh_timeout_s)),
                        )
                    else:
                        report["gmsh_thread_benchmark_skipped"] = "CLI benchmark is separate from the Python API worker."
                gmsh_started_epoch = time.time()
                gmsh_perf_start = time.perf_counter()
                report["gmsh_started_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gmsh_started_epoch))
                if code is None:
                    try:
                        code, log = run_command(cmd, gmsh_cwd, timeout_s=max(60, int(args.gmsh_timeout_s)))
                    except Exception as exc:
                        code, log = 999, str(exc)
                else:
                    log = report.get("gmsh_failure_comment", "Gmsh execution skipped by environment validation.")
                initial_code = code
                initial_log = log
                if (
                    bool(mesh_cfg.get("gmsh_boundary_layer_fallback_no_bl", False))
                    and report.get("boundary_layer_requested")
                    and code != 0
                    and gmsh_boundary_layer_edge_recovery_failed(log)
                ):
                    (adir / "mesh_with_boundary_layer_failed.geo").write_text(
                        gmsh_geo.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                    (adir / "log.gmsh_boundary_layer_failed").write_text(initial_log, encoding="utf-8", errors="ignore")
                    removed_geo_lines = strip_boundary_layer_block_from_geo(gmsh_geo)
                    if gmsh_geo.resolve() != geo_path.resolve():
                        shutil.copy2(gmsh_geo, geo_path)
                    if gmsh_msh.exists():
                        gmsh_msh.unlink()
                    if msh_path.exists():
                        msh_path.unlink()
                    fallback_started = time.perf_counter()
                    code, log = run_command(cmd, gmsh_cwd, timeout_s=max(60, int(args.gmsh_timeout_s)))
                    report["gmsh_boundary_layer_fallback_used"] = True
                    report["gmsh_boundary_layer_initial_exit_code"] = initial_code
                    report["gmsh_boundary_layer_fallback_removed_geo_lines"] = int(removed_geo_lines)
                    report["gmsh_boundary_layer_fallback_wall_time_s"] = float(time.perf_counter() - fallback_started)
                    report["gmsh_boundary_layer_fallback_reason"] = (
                        "Gmsh failed on synthetic BoundaryLayer curve 444444; reran the same geometry without the Gmsh BoundaryLayer field."
                    )
                    report["boundary_layer_requested_initial"] = True
                    report["boundary_layer_requested"] = False
                    report["boundary_layer_layers_requested"] = 0
                    report["boundary_layer_quads_requested"] = False
                    report["boundary_layer_first_cell_height_chord"] = 0.0
                    report["boundary_layer_total_thickness_chord"] = 0.0
                    report["boundary_layer_total_thickness_limited"] = False
                report["gmsh_wall_time_s"] = float(time.perf_counter() - gmsh_perf_start)
                (adir/"log.gmsh").write_text(log, encoding="utf-8", errors="ignore")
                report["gmsh_command"] = " ".join(cmd)
                report["gmsh_exit_code"] = code
                report["gmsh_last_phase"] = infer_gmsh_last_phase(log)
                report.update(parse_gmsh_mesh_counts(log))
                report["gmsh_timed_out"] = code == 124
                report["gmsh_cancelled_by_user"] = code == 130
                if code == 130:
                    report["gmsh_error"] = "gmsh_cancelled_by_user"
                    report["gmsh_failure_stage"] = "gmsh_mesh_generation_cancelled"
                    report["gmsh_failure_comment"] = (
                        "The user cancelled Gmsh. The child process group was terminated cleanly and the partial log was saved."
                    )
                elif code == 124:
                    report["gmsh_error"] = "gmsh_timeout"
                    report["gmsh_failure_stage"] = "gmsh_mesh_generation_timeout"
                    report["gmsh_failure_comment"] = (
                        "Gmsh did not finish before the timeout. For debug, use the coarsest mesh, no boundary layer, "
                        "smaller domain, fewer profile points, and native temporary workdir. Inspect log.gmsh to see "
                        "whether it stopped in 1D, 2D, 3D/extrusion or file writing."
                    )
                elif code != 0 and code != 996:
                    report["gmsh_failure_stage"] = "gmsh_mesh_generation_error"
                    report["gmsh_failure_comment"] = "Gmsh returned a non-zero exit code. Inspect log.gmsh for the first Error line."
                if (
                    code == 0
                    and is_open
                    and gmsh_msh.exists()
                    and bool(geo_info.get("open_duplicate_inlet_interface_for_one_sided_bl", False))
                ):
                    try:
                        chord_for_merge = float(manifest.get("chord_m", 1.0) or 1.0)
                        if bool(geo_info.get("open_selective_inlet_interface_merge", False)):
                            merge_info = merge_named_msh2_interface_nodes(
                                gmsh_msh,
                                geo_info.get("open_inlet_interface_physical_names", []),
                                tolerance=max(1.0e-13, 1.0e-11 * chord_for_merge),
                            )
                        else:
                            merge_info = merge_coincident_msh2_nodes(
                                gmsh_msh,
                                tolerance=max(1.0e-13, 1.0e-11 * chord_for_merge),
                            )
                        report.update(merge_info)
                        if int(merge_info.get("coincident_interface_nodes_merged", 0) or 0) <= 0:
                            raise RuntimeError(
                                "The duplicated inlet interfaces produced no coincident mesh nodes to merge."
                            )
                    except Exception as exc:
                        code = 997
                        log += f"\nERROR: coincident inlet-interface merge failed: {exc}\n"
                        report["gmsh_failure_stage"] = "open_inlet_interface_merge_error"
                        report["gmsh_failure_comment"] = str(exc)
                        report["open_inlet_interface_merge_error"] = str(exc)
                        report["gmsh_exit_code"] = code
                if code == 0 and python_extrude_open_mesh and gmsh_msh.exists():
                    try:
                        source_2d = adir / "mesh_2d_source.msh"
                        shutil.copy2(gmsh_msh, source_2d)
                        extruded_msh = gmsh_msh.with_name("mesh_extruded_3d.msh")
                        span = float(mesh_cfg.get("spanwise_thickness_chord", 0.01)) * float(manifest.get("chord_m", 1.0) or 1.0)
                        extrusion_info = extrude_msh2_surface_one_cell(gmsh_msh, extruded_msh, span)
                        os.replace(extruded_msh, gmsh_msh)
                        report.update(extrusion_info)
                        report["extruded_3d"] = True
                        report["openfoam_ready"] = True
                        report["frontAndBack_patch_exists"] = True
                        report["spanwise_layers"] = 1
                        report["spanwise_thickness_chord"] = float(mesh_cfg.get("spanwise_thickness_chord", 0.01))
                    except Exception as exc:
                        code = 998
                        log += f"\nERROR: mesh-level one-cell extrusion failed: {exc}\n"
                        report["gmsh_failure_stage"] = "mesh_level_one_cell_extrusion_error"
                        report["gmsh_failure_comment"] = str(exc)
                        report["mesh_level_extrusion_error"] = str(exc)
                        report["gmsh_exit_code"] = code
                gmsh_output_fresh = bool(code == 0 and gmsh_msh.exists() and gmsh_msh.stat().st_size > 0)
                report["gmsh_output_fresh_after_run"] = gmsh_output_fresh
                if gmsh_msh.exists():
                    report["gmsh_output_size_bytes"] = int(gmsh_msh.stat().st_size)
                    report["gmsh_output_mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gmsh_msh.stat().st_mtime))
                if gmsh_msh.exists() and gmsh_output_fresh:
                    try:
                        same_mesh_path = gmsh_msh.resolve() == msh_path.resolve() or os.path.samefile(gmsh_msh, msh_path)
                    except Exception:
                        same_mesh_path = False
                    if same_mesh_path:
                        report["gmsh_output_already_at_attempt_path"] = True
                    else:
                        shutil.copy2(gmsh_msh, msh_path)
                        report["gmsh_output_copied_to_attempt_path"] = True
                elif code == 0:
                    report["gmsh_failure_stage"] = "gmsh_no_fresh_output_mesh"
                    report["gmsh_failure_comment"] = "Gmsh exited with code 0 but did not create a fresh mesh.msh for this run."
                if temp_dir is not None:
                    if code == 0 and msh_path.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    else:
                        report["gmsh_temp_workdir_left_for_debug"] = str(temp_dir.resolve())
                report["mesh_file_created"] = msh_path.exists() and msh_path.stat().st_size > 0 and gmsh_output_fresh
                if msh_path.exists():
                    report["mesh_file_size_bytes"] = int(msh_path.stat().st_size)
                    report["mesh_file_mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msh_path.stat().st_mtime))
                write_json(adir / "gmsh_performance.json", {
                    "gmsh_threads": int(gmsh_threads),
                    "gmsh_wall_time_s": report.get("gmsh_wall_time_s"),
                    "gmsh_command": report.get("gmsh_command"),
                    "gmsh_execution_directory": report.get("gmsh_execution_directory"),
                    "gmsh_exit_code": report.get("gmsh_exit_code"),
                    "mesh_file_created": report.get("mesh_file_created"),
                    "gmsh_log_nodes": report.get("gmsh_log_nodes"),
                    "gmsh_log_elements": report.get("gmsh_log_elements"),
                })
                if report["mesh_file_created"]:
                    try:
                        mesh_size_mb = float(msh_path.stat().st_size) / (1024.0 * 1024.0)
                        report["mesh_file_size_mb"] = mesh_size_mb
                        max_parse_mb = float(mesh_cfg.get("max_internal_parse_mesh_size_mb", 80) or 0)
                        max_parse_elements = int(mesh_cfg.get("max_internal_parse_elements", 75_000) or 0)
                        gmsh_elements = int(report.get("gmsh_log_elements") or 0)
                        skip_large_mesh_parse = (
                            (max_parse_mb > 0 and mesh_size_mb > max_parse_mb)
                            or (max_parse_elements > 0 and gmsh_elements > max_parse_elements)
                        )
                        if skip_large_mesh_parse:
                            report["msh_parse_skipped"] = True
                            report["msh_parse_skip_reason"] = (
                                f"large_mesh: {mesh_size_mb:.1f} MB, {gmsh_elements} Gmsh log elements; "
                                f"limits are {max_parse_mb:.1f} MB and {max_parse_elements} elements"
                            )
                            report["mesh_front_surface_preview_created"] = False
                            if report.get("boundary_layer_requested"):
                                report["boundary_layer_layers_created"] = False
                                report["boundary_layer_confirmation_basis"] = "internal_msh_parse_skipped_for_large_mesh"
                        else:
                            report["msh_parse_skipped"] = False
                            report.update(parse_msh_v2(msh_path))
                            report["mesh_front_surface_preview_created"] = plot_msh_front_surface_preview(
                                msh_path, adir / "mesh_preview_front_surface.png", points=points, view="full"
                            )
                            report["mesh_te_preview_created"] = plot_msh_front_surface_preview(
                                msh_path, adir / "mesh_preview_te.png", points=points, view="te"
                            )
                            if is_open:
                                report["mesh_inlet_preview_created"] = plot_msh_front_surface_preview(
                                    msh_path, adir / "mesh_preview_inlet.png", points=points, view="inlet"
                                )
                            update_boundary_layer_confirmation(report)
                        if args.check_mesh and write_openfoam_mesh:
                            run_openfoam_mesh_checks(
                                adir,
                                msh_path,
                                report,
                                timeout_s=max(60, int(args.openfoam_tool_timeout_s)),
                                use_temp_workdir=bool(args.openfoam_temp_workdir),
                                profile_points=points,
                            )
                        elif args.check_mesh and not write_openfoam_mesh:
                            report["check_mesh_requested"] = False
                            report["checkMesh_status"] = "SKIPPED_2D_MESH"
                    except Exception as exc:
                        report["msh_parse_error"] = str(exc)
        decision = decide_quality(report, attempt) if decide_quality else None
        if write_quality_report and decision:
            write_quality_report(adir, report, decision)
            status = decision.status
        else:
            status = "FAIL" if not report.get("mesh_file_created") else "WARNING_ACCEPTABLE"
            report["status"] = status
            write_json(adir/"mesh_quality_report.json", report)
            (adir/"mesh_quality_report.txt").write_text(str(report), encoding="utf-8")
        history.append({"attempt": attempt, "status": status, "mesh_file_created": report.get("mesh_file_created"), "gmsh_exit_code": report.get("gmsh_exit_code"), "estimated_cell_count": report.get("estimated_cell_count")})
        final_attempt_dir = adir
        if status in {"PASS", "WARNING_ACCEPTABLE"} or not args.auto_remesh:
            break
        if update_mesh_config_for_remesh and decision:
            mesh_cfg = update_mesh_config_for_remesh(mesh_cfg, decision, attempt)
            # If the first attempt fails with BL, next attempt disables BL to isolate domain errors.
            if attempt == 1 and report.get("gmsh_exit_code") not in (0, None):
                mesh_cfg["request_boundary_layer"] = False
                mesh_cfg["boundary_layer_layers"] = 0

    pd.DataFrame(history).to_csv(mesh_root/"remeshing_history.csv", index=False)
    if final_attempt_dir:
        for src_name, dst_name in [
            ("mesh.geo", "mesh_final.geo"),
            ("mesh.msh", "mesh_final.msh"),
            ("mesh_quality_report.json", "mesh_quality_report.json"),
            ("mesh_quality_report.txt", "mesh_quality_report.txt"),
            ("mesh_quality_report.html", "mesh_quality_report.html"),
            ("airfoil_wall_curve_connectivity_audit.json", "airfoil_wall_curve_connectivity_audit.json"),
            ("airfoil_wall_curve_connectivity_audit.csv", "airfoil_wall_curve_connectivity_audit.csv"),
            ("log.gmsh", "log.gmsh"),
            ("log.gmshToFoam", "log.gmshToFoam"),
            ("log.checkMesh", "log.checkMesh"),
            ("log.checkMesh.locations", "log.checkMesh.locations"),
            ("checkMesh_problem_locations.json", "checkMesh_problem_locations.json"),
            ("checkMesh_problem_locations.txt", "checkMesh_problem_locations.txt"),
        ]:
            src = final_attempt_dir/src_name
            if src.exists(): shutil.copy2(src, mesh_root/dst_name)
        problem_locations = final_attempt_dir / "checkMesh_problem_locations"
        if problem_locations.is_dir():
            target_locations = mesh_root / "checkMesh_problem_locations"
            if target_locations.exists():
                shutil.rmtree(target_locations)
            shutil.copytree(problem_locations, target_locations)
        problem_sets = final_attempt_dir / "checkMesh_problem_sets"
        if problem_sets.is_dir():
            target_sets = mesh_root / "checkMesh_problem_sets"
            if target_sets.exists():
                shutil.rmtree(target_sets)
            shutil.copytree(problem_sets, target_sets)
        poly = final_attempt_dir / "openfoam_mesh_check_case" / "constant" / "polyMesh"
        if poly.exists() and (poly / "boundary").exists():
            dst_poly = mesh_root / "constant" / "polyMesh"
            if dst_poly.exists():
                shutil.rmtree(dst_poly)
            dst_poly.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(poly, dst_poly)
        for name in [
            "mesh_preview_front_surface.png",
            "mesh_preview_inlet.png",
            "mesh_preview_te.png",
            "geometry_preview_airfoil.png",
            "geometry_preview_inlet.png",
            "geometry_preview_te.png",
            "open_te_rounding_geometry_zoom.png",
        ]:
            src = final_attempt_dir/name
            if src.exists(): shutil.copy2(src, mesh_root/name)
    write_json(mesh_root/"mesh_build_manifest.json", {"variant": variant, "domain": args.domain, "mesh_level": args.mesh_level, "history": history, "dry_run": args.dry_run, "write_openfoam_mesh": write_openfoam_mesh, "write_2d_mesh": bool(args.write_2d_mesh), "check_mesh": bool(args.check_mesh and write_openfoam_mesh), "gmsh_threads": int(gmsh_threads), "benchmark_gmsh_threads": bool(args.benchmark_gmsh_threads), "run_id": run_id, "note": "This script does not execute CFD."})
    print(f"Mesh outputs: {mesh_root.resolve()}")
    if history:
        print(f"Final status: {history[-1]['status']}; mesh_file_created={history[-1].get('mesh_file_created')}")
        final_report = read_json(mesh_root / "mesh_quality_report.json", {}) or {}
        if str(history[-1]["status"]) == "FAIL":
            if final_report.get("mesh_file_created"):
                print("Gmsh mesh was created, but the internal quality status is FAIL.")
                failed = final_report.get("failed_checks", []) or []
                warnings = final_report.get("warnings", []) or []
                if failed:
                    print(f"  Failed checks: {', '.join(map(str, failed[:8]))}")
                if warnings:
                    print(f"  Warnings: {', '.join(map(str, warnings[:8]))}")
                if final_report.get("gmsh_boundary_layer_fallback_used"):
                    print(f"  Boundary-layer fallback: {final_report.get('gmsh_boundary_layer_fallback_reason')}")
            else:
                print("Mesh generation failed.")
            print(f"  Report: {(mesh_root / 'mesh_quality_report.txt').resolve()}")
            if (mesh_root / "log.gmsh").exists():
                print(f"  Gmsh log: {(mesh_root / 'log.gmsh').resolve()}")
            if final_report.get("gmsh_exit_code") is not None:
                print(f"  gmsh_exit_code: {final_report.get('gmsh_exit_code')}")
            if final_report.get("gmsh_last_phase"):
                print(f"  gmsh_last_phase: {final_report.get('gmsh_last_phase')}")
            if final_report.get("gmsh_failure_comment"):
                print(f"  comment: {final_report.get('gmsh_failure_comment')}")
            if not final_report.get("mesh_file_created"):
                print("  Try --write-2d-mesh to isolate surface meshing, or --no-gmsh-temp-workdir only for path debugging.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a Gmsh mesh for ram-air 2D profile variants. No CFD solver is executed.")
    p.add_argument(
        "--case-root",
        type=Path,
        required=True,
        help="Project root containing CFD_2D/CFD_2D_inputs; usually pass '.'. Do not pass the variant package directory.",
    )
    p.add_argument(
        "--variant",
        required=True,
        help=(
            "Case-package folder under CFD_2D/CFD_2D_inputs/case_package. "
            f"Bundled variants include: {', '.join(sorted(SUPPORTED_VARIANTS))}."
        ),
    )
    p.add_argument("--domain", choices=sorted(DOMAINS), default="circular_50c")
    p.add_argument("--mesh-level", choices=sorted(MESH_LEVELS), default="medium")
    p.add_argument("--mesh-only", action="store_true", default=True)
    p.add_argument("--write-openfoam-mesh", action="store_true", help="Generate one-cell-thick 3D Gmsh mesh for OpenFOAM 2D with frontAndBack empty.")
    p.add_argument("--write-2d-mesh", action="store_true", help="Generate a 2D Gmsh mesh only, without OpenFOAM 3D extrusion. Useful to isolate Gmsh slowness.")
    p.add_argument("--check-mesh", action="store_true", help="After gmsh -3, run gmshToFoam and checkMesh if the OpenFOAM tools are on PATH.")
    p.add_argument("--check-existing-mesh", action="store_true", help="Do not launch Gmsh; convert/check the current mesh_final.msh with gmshToFoam/checkMesh.")
    p.add_argument("--mesh-config", type=Path, default=None, help="Optional JSON configuration override. Used by the bounded mesh optimizer without rewriting the active configuration during each candidate run.")
    p.add_argument("--gmsh-timeout-s", type=int, default=900, help="Timeout for the Gmsh mesh generation command (15 minutes by default).")
    p.add_argument(
        "--gmsh-backend",
        choices=["auto", "cli", "python_api"],
        default=None,
        help="Gmsh execution backend. auto prefers the installed Python API and falls back to the CLI only when the API is unavailable.",
    )
    p.add_argument("--gmsh-executable", type=Path, default=None, help="Explicit Gmsh binary. Otherwise RAMAIR_GMSH_EXECUTABLE, ~/.local/opt/gmsh-4.15.2/bin/gmsh and PATH are checked in that order.")
    p.add_argument("--allow-legacy-gmsh-boundary-layer", action="store_true", help="Allow BoundaryLayer meshing with Gmsh older than 4.10. Diagnostic only; Gmsh 4.8.4 is known to collapse cells at this TE.")
    p.add_argument("--gmsh-threads", type=int, default=None, help="Threads passed to Gmsh with -nt. Default comes from config: min(12, os.cpu_count()).")
    p.add_argument("--benchmark-gmsh-threads", action="store_true", help="Run Gmsh benchmark cases for the values in --gmsh-thread-sweep before the main Gmsh run.")
    p.add_argument("--gmsh-thread-sweep", default="1,4,8,12", help="Comma-separated thread counts for --benchmark-gmsh-threads.")
    p.add_argument("--gmsh-temp-workdir", action=argparse.BooleanOptionalAction, default=True, help="Run Gmsh in a native temporary directory and copy mesh.msh back to the project.")
    p.add_argument("--openfoam-tool-timeout-s", type=int, default=600, help="Timeout for gmshToFoam/checkMesh when --check-mesh is used.")
    p.add_argument("--openfoam-temp-workdir", action=argparse.BooleanOptionalAction, default=True, help="Run gmshToFoam/checkMesh in a native temporary directory and copy constant/polyMesh back to the project.")
    p.add_argument("--plot", action="store_true")
    p.add_argument("--auto-remesh", action="store_true")
    p.add_argument("--max-remesh-attempts", type=int, default=4)
    p.add_argument("--approve-mesh", action="store_true")
    p.add_argument("--force-approve", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument(
        "--previous-output-action",
        choices=["ask", "archive", "delete", "keep"],
        default="archive",
        help="What to do with previous mesh outputs when regenerating: archive safely, delete heavy files, keep, or ask interactively.",
    )
    p.add_argument("--dry-run", action="store_true", help="Write .geo and reports only; do not call gmsh.")
    p.add_argument("--allow-windows-mount", action="store_true", help="Allow running Gmsh from /mnt/c or /mnt/d in WSL. Use only for path debugging; native Linux filesystem is strongly preferred.")
    p.add_argument("--force-boundary-layer", action="store_true", help="Enable the Gmsh BoundaryLayer field even for the fast debug mesh level.")
    p.add_argument("--no-boundary-layer", action="store_true", help="Disable Gmsh BoundaryLayer field for debugging the domain topology.")
    p.add_argument("--open-diagnostic-mesh", action="store_true", help="Allow diagnostic open-profile mesh with embedded wall curves. Not an approved OpenFOAM thin-solid fabric mesh.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    with variant_mesh_lock(args.case_root, args.variant):
        build_mesh(args)


if __name__ == "__main__":
    main()
