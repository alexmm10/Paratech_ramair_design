#!/usr/bin/env python3
"""Build, evaluate and atomically promote definitive open coarse/fine meshes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection

from ramair_scientific_plot_style import save_scientific_figure

from ramair_2d_closed_open_convergence_study import (
    BASE_VARIANTS,
    LEVEL_VARIANTS,
    PRESETS,
    clone_variant,
)
from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    results_case_root,
    utc_stamp,
    write_json_atomic,
)


LEVELS = ("coarse", "fine")
MEDIUM_CONFIG = Path(
    "CFD_2D/CFD_2D_inputs/config/mesh_presets/"
    "open_ramair_validation_1m_candidate.json"
)
TARGET_CELL_RANGES = {
    "coarse": (210_000, 245_000),
    "fine": (500_000, 600_000),
}
STRICT_LIMITS = {
    "checkMesh_max_non_orthogonality_deg": ("max", 45.0),
    "checkMesh_max_skewness": ("max", 0.72),
    "checkMesh_min_cell_determinant": ("min", 0.04),
    "checkMesh_min_face_interpolation_weight": ("min", 0.08),
    "checkMesh_min_face_volume_ratio": ("min", 0.12),
}
EXPECTED_PARAMETERS = {
    "coarse": {
        "open_zero_thickness_contour_target_nodes": 2800,
        "open_zero_thickness_inlet_normal_y1_factor": 8.0,
        "open_zero_thickness_te_transfinite_min_nodes": 32,
        "open_inner_wall_node_factor": 0.40,
        "open_inner_te_node_factor": 0.28,
        "open_nearfield_intermediate_size_chord": 0.11,
        "open_nearfield_outer_size_chord": 0.30,
        "open_farfield_size_chord": 4.5,
        "open_boundary_layer_layers": 35,
        "open_boundary_layer_growth": 1.1247262343923976,
        "open_first_cell_height_m": 25.0e-6,
        "open_near_wall_size_chord": 0.0035,
    },
    "fine": {
        "open_zero_thickness_contour_target_nodes": 4600,
        "open_zero_thickness_inlet_normal_y1_factor": 8.0,
        "open_zero_thickness_te_transfinite_min_nodes": 42,
        "open_inner_wall_node_factor": 0.30,
        "open_inner_te_node_factor": 0.20,
        "open_nearfield_intermediate_size_chord": 0.055,
        "open_nearfield_outer_size_chord": 0.15,
        "open_farfield_size_chord": 3.0,
        "open_boundary_layer_layers": 65,
        "open_boundary_layer_growth": 1.0512255954311551,
        "open_first_cell_height_m": 25.0e-6,
        "open_near_wall_size_chord": 0.0035,
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bl_thickness(y1: float, layers: int, growth: float) -> float:
    if abs(growth - 1.0) < 1.0e-12:
        return y1 * layers
    return y1 * (growth**layers - 1.0) / (growth - 1.0)


def refinement_root(project_root: Path) -> Path:
    return active_workspace_root(project_root) / "mesh_candidates/open_refinement"


def _preset(project_root: Path, level: str) -> Path:
    return Path(project_root).resolve() / PRESETS[("open", level)]


def _mesh_root(project_root: Path, level: str) -> Path:
    return (
        Path(project_root).resolve()
        / "CFD_2D/meshes"
        / LEVEL_VARIANTS["open"][level]
    )


def _validate_presets(project_root: Path) -> dict[str, Any]:
    medium_path = Path(project_root).resolve() / MEDIUM_CONFIG
    medium = read_json(medium_path, {}) or {}
    y1 = float(medium["open_first_cell_height_m"])
    medium_h = _bl_thickness(
        y1,
        int(medium["open_boundary_layer_layers"]),
        float(medium["open_boundary_layer_growth"]),
    )
    levels: dict[str, Any] = {}
    for level in LEVELS:
        path = _preset(project_root, level)
        config = read_json(path, {}) or {}
        mismatches = [
            {
                "parameter": name,
                "expected": value,
                "actual": config.get(name),
            }
            for name, value in EXPECTED_PARAMETERS[level].items()
            if not math.isclose(
                float(config.get(name, math.nan)),
                float(value),
                rel_tol=1.0e-10,
                abs_tol=1.0e-12,
            )
        ]
        current_h = _bl_thickness(
            float(config["open_first_cell_height_m"]),
            int(config["open_boundary_layer_layers"]),
            float(config["open_boundary_layer_growth"]),
        )
        if not math.isclose(current_h, medium_h, rel_tol=1.0e-8):
            mismatches.append(
                {
                    "parameter": "derived_boundary_layer_thickness",
                    "expected": medium_h,
                    "actual": current_h,
                }
            )
        if mismatches:
            raise ValueError(f"{level} preset mismatches: {mismatches}")
        levels[level] = {
            "config": str(path),
            "config_hash": _sha256(path),
            "y1_m": float(config["open_first_cell_height_m"]),
            "boundary_layer_layers": int(config["open_boundary_layer_layers"]),
            "boundary_layer_growth": float(config["open_boundary_layer_growth"]),
            "derived_boundary_layer_thickness_m": current_h,
            "target_cell_range": list(TARGET_CELL_RANGES[level]),
        }
    return {
        "medium_config": str(medium_path),
        "medium_config_hash": _sha256(medium_path),
        "medium_y1_m": y1,
        "medium_boundary_layer_thickness_m": medium_h,
        "levels": levels,
    }


def prepare_refinement(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    audit = _validate_presets(project_root)
    root = refinement_root(project_root)
    previous = read_json(root / "open_mesh_refinement_manifest.json", {}) or {}
    entries: list[dict[str, Any]] = []
    for level in LEVELS:
        variant = LEVEL_VARIANTS["open"][level]
        command = [
            sys.executable,
            str(project_root / "CFD_2D/scripts/ramair_2d_mesh_builder.py"),
            "--case-root",
            str(project_root),
            "--variant",
            variant,
            "--domain",
            "circular_50c",
            "--mesh-level",
            "custom",
            "--mesh-config",
            str(_preset(project_root, level)),
            "--write-openfoam-mesh",
            "--check-mesh",
            "--overwrite",
            "--previous-output-action",
            "archive",
            "--gmsh-timeout-s",
            "900",
            "--openfoam-tool-timeout-s",
            "600",
            "--gmsh-threads",
            "12",
        ]
        entries.append(
            {
                "level": level,
                "mesh_id": f"open_{level}",
                "variant": variant,
                "config": str(_preset(project_root, level)),
                "command": command,
                "mesh_root": str(_mesh_root(project_root, level)),
                "status": "PREPARED_NOT_EXECUTED",
            }
        )
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "status": "PREPARED_NOT_EXECUTED",
        "medium_preserved": True,
        "medium_config_hash_before": audit["medium_config_hash"],
        "boundary_layer_audit": audit,
        "strict_quality_limits": STRICT_LIMITS,
        "entries": entries,
        "candidate_history": list(previous.get("candidate_history") or []),
        "promotion_policy": "explicit_confirmation_only",
        "updated_at": utc_stamp(),
    }
    write_json_atomic(root / "open_mesh_size_field_audit.json", audit)
    write_json_atomic(root / "open_mesh_refinement_manifest.json", manifest)
    return manifest


def _quality(project_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    report_path = Path(entry["mesh_root"]) / "mesh_quality_report.json"
    if not report_path.is_file():
        return {
            **entry,
            "status": "MESH_REPORT_MISSING",
            "accepted": False,
            "failures": ["mesh_quality_report_missing"],
        }
    report = read_json(report_path, {}) or {}
    failures: list[str] = []
    if str(report.get("checkMesh_status")).upper() != "OK":
        failures.append("checkMesh_status")
    if int(report.get("checkMesh_cell_count") or 0) <= 0:
        failures.append("cell_count")
    for key, (direction, threshold) in STRICT_LIMITS.items():
        value = report.get(key)
        if value is None:
            failures.append(f"{key}_missing")
            continue
        numeric = float(value)
        if direction == "max" and numeric > threshold:
            failures.append(key)
        if direction == "min" and numeric < threshold:
            failures.append(key)
    level = str(entry["level"])
    cells = int(report.get("checkMesh_cell_count") or 0)
    count_ok = TARGET_CELL_RANGES[level][0] <= cells <= TARGET_CELL_RANGES[level][1]
    if not count_ok:
        failures.append("target_cell_range")
    return {
        **entry,
        "status": "ACCEPTED_CANDIDATE" if not failures else "REJECTED_CANDIDATE",
        "accepted": not failures,
        "failures": failures,
        "cell_count": cells,
        "target_cell_range": list(TARGET_CELL_RANGES[level]),
        "quality_report": str(report_path),
        "quality_metrics": {
            "checkMesh_status": report.get("checkMesh_status"),
            **{key: report.get(key) for key in STRICT_LIMITS},
        },
        "boundary_layer_layers": report.get("boundary_layer_layers_requested"),
        "boundary_layer_thickness": report.get(
            "boundary_layer_total_thickness_chord"
        ),
        "gmsh_wall_time_s": report.get("gmsh_wall_time_s"),
        "gmshToFoam_wall_time_s": report.get("gmshToFoam_wall_time_s"),
        "checkMesh_wall_time_s": report.get("checkMesh_wall_time_s"),
    }


def _gmsh_quality_values(mesh_path: Path) -> np.ndarray:
    """Read real element quality values from a completed Gmsh mesh."""
    try:
        import gmsh  # type: ignore
    except ImportError:
        return np.asarray([], dtype=float)
    initialized_here = not gmsh.isInitialized()
    if initialized_here:
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.open(str(mesh_path))
        _types, element_tags, _node_tags = gmsh.model.mesh.getElements(3)
        tags = np.concatenate(
            [np.asarray(values, dtype=np.uint64) for values in element_tags]
        ) if element_tags else np.asarray([], dtype=np.uint64)
        if tags.size == 0:
            return np.asarray([], dtype=float)
        return np.asarray(
            gmsh.model.mesh.getElementQualities(tags.tolist(), "minSICN"),
            dtype=float,
        )
    finally:
        gmsh.clear()
        if initialized_here:
            gmsh.finalize()


def _write_quality_histograms(
    root: Path,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    series: dict[str, np.ndarray] = {}
    for entry in entries:
        mesh = Path(entry["mesh_root"]) / "mesh_final.msh"
        if not mesh.is_file():
            mesh = Path(entry["mesh_root"]) / "mesh.msh"
        if not mesh.is_file():
            continue
        values = _gmsh_quality_values(mesh)
        values = values[np.isfinite(values)]
        if values.size:
            series[str(entry["level"])] = values
    if not series:
        return {
            "status": "NOT_GENERATED_NO_REAL_GMSH_QUALITY_VALUES",
            "path": None,
        }
    fig, axis = plt.subplots(figsize=(8.4, 4.8))
    bins = np.linspace(-1.0, 1.0, 81)
    for level, values in series.items():
        axis.hist(
            values,
            bins=bins,
            density=True,
            histtype="step",
            linewidth=1.5,
            label=f"{level} (n={len(values):,})",
        )
    axis.set(
        xlabel="Gmsh minSICN element quality (-1 inverted, 1 ideal)",
        ylabel="Probability density",
        title="Open-airfoil mesh quality distribution",
    )
    axis.set_xlim(-0.05, 1.0)
    axis.grid(True, alpha=0.25)
    axis.legend()
    path = root / "open_mesh_histograms.png"
    save_scientific_figure(
        fig, path,
        data=[{"mesh_level": level, "element_count": int(len(values)), "minimum_minSICN": float(np.min(values)), "median_minSICN": float(np.median(values))} for level, values in series.items()],
        metadata={"source": "Gmsh element quality", "grouping": "mesh refinement level", "transformation": "density histogram"},
    )
    return {
        "status": "GENERATED_FROM_REAL_GMSH_ELEMENTS",
        "path": str(path),
        "levels": {
            level: {
                "element_count": int(len(values)),
                "minimum_minSICN": float(np.min(values)),
                "p01_minSICN": float(np.percentile(values, 1)),
                "median_minSICN": float(np.median(values)),
            }
            for level, values in series.items()
        },
    }


def _collect_previews(root: Path, entries: list[dict[str, Any]]) -> list[str]:
    destination = root / "open_mesh_previews"
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for entry in entries:
        mesh_root = Path(entry["mesh_root"])
        for source in sorted(mesh_root.glob("*.png")):
            target = destination / f"{entry['level']}_{source.name}"
            shutil.copy2(source, target)
            copied.append(str(target))
    if not copied:
        destination.rmdir()
    return copied


def _front_surface_triangles(
    mesh_path: Path,
) -> tuple[dict[int, np.ndarray], tuple[float, float, float, float] | None]:
    """Return real front-surface triangles from an extruded Gmsh mesh."""
    try:
        import gmsh  # type: ignore
    except ImportError:
        return {}, None
    initialized_here = not gmsh.isInitialized()
    if initialized_here:
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.open(str(mesh_path))
        physical_tag = next(
            (
                tag
                for dim, tag in gmsh.model.getPhysicalGroups(2)
                if gmsh.model.getPhysicalName(dim, tag) == "frontAndBack"
            ),
            None,
        )
        if physical_tag is None:
            return {}, None
        wall_coordinates: list[np.ndarray] = []
        for dim, tag in gmsh.model.getPhysicalGroups(2):
            if not gmsh.model.getPhysicalName(dim, tag).startswith("airfoil_wall"):
                continue
            _, group_coordinates = gmsh.model.mesh.getNodesForPhysicalGroup(dim, tag)
            if len(group_coordinates):
                wall_coordinates.append(
                    np.asarray(group_coordinates, dtype=float).reshape(-1, 3)
                )
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        order = np.argsort(np.asarray(node_tags, dtype=np.int64))
        sorted_tags = np.asarray(node_tags, dtype=np.int64)[order]
        xyz = np.asarray(coordinates, dtype=float).reshape(-1, 3)[order]
        front_faces: dict[int, list[np.ndarray]] = {}
        for entity in gmsh.model.getEntitiesForPhysicalGroup(2, physical_tag):
            types, _, element_nodes = gmsh.model.mesh.getElements(2, int(entity))
            for element_type, connectivity in zip(types, element_nodes):
                properties = gmsh.model.mesh.getElementProperties(element_type)
                nodes_per_element = int(properties[3])
                if nodes_per_element not in {3, 4}:
                    continue
                tags = np.asarray(connectivity, dtype=np.int64).reshape(
                    -1, nodes_per_element
                )
                indices = np.searchsorted(sorted_tags, tags)
                points = xyz[indices]
                minimum_z = float(np.min(xyz[:, 2]))
                on_front = np.all(
                    np.isclose(points[:, :, 2], minimum_z, rtol=0.0, atol=1.0e-10),
                    axis=1,
                )
                front_faces.setdefault(nodes_per_element, []).append(
                    points[on_front, :, :2]
                )
        polygons = {
            nodes_per_element: np.concatenate(parts, axis=0)
            for nodes_per_element, parts in front_faces.items()
            if parts
        }
        wall_points = (
            np.concatenate(wall_coordinates, axis=0)
            if wall_coordinates
            else np.empty((0, 3), dtype=float)
        )
        wall_box = None
        if len(wall_points):
            wall_box = (
                float(np.min(wall_points[:, 0])),
                float(np.min(wall_points[:, 1])),
                float(np.max(wall_points[:, 0])),
                float(np.max(wall_points[:, 1])),
            )
        return polygons, wall_box
    finally:
        gmsh.clear()
        if initialized_here:
            gmsh.finalize()


def _write_mesh_previews(
    root: Path,
    entries: list[dict[str, Any]],
) -> list[str]:
    """Render bounded real-mesh previews without loading ParaView."""
    destination = root / "open_mesh_previews"
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for entry in entries:
        mesh = Path(entry["mesh_root"]) / "mesh_final.msh"
        if not mesh.is_file():
            mesh = Path(entry["mesh_root"]) / "mesh.msh"
        if not mesh.is_file():
            continue
        polygon_groups, wall_box = _front_surface_triangles(mesh)
        if not polygon_groups:
            continue
        all_points = np.concatenate(
            [polygons.reshape(-1, 2) for polygons in polygon_groups.values()],
            axis=0,
        )
        if wall_box is None:
            x_min, y_min = np.min(all_points, axis=0)
            x_max, y_max = np.max(all_points, axis=0)
        else:
            x_min, y_min, x_max, y_max = wall_box
        chord = max(1.0e-12, float(x_max - x_min))
        views = {
            "front_surface": (
                lambda centroids: np.ones(len(centroids), dtype=bool),
                None,
            ),
            "inlet_zoom": (
                lambda centroids: (
                    (centroids[:, 0] >= x_min - 0.15 * chord)
                    & (centroids[:, 0] <= x_min + 0.30 * chord)
                    & (centroids[:, 1] >= y_min - 0.20 * chord)
                    & (centroids[:, 1] <= y_max + 0.20 * chord)
                ),
                (
                    x_min - 0.15 * chord,
                    x_min + 0.30 * chord,
                    y_min - 0.20 * chord,
                    y_max + 0.20 * chord,
                ),
            ),
            "te_zoom": (
                lambda centroids: (
                    (centroids[:, 0] >= x_min + 0.70 * chord)
                    & (centroids[:, 0] <= x_max + 0.20 * chord)
                    & (centroids[:, 1] >= y_min - 0.18 * chord)
                    & (centroids[:, 1] <= y_max + 0.18 * chord)
                ),
                (
                    x_min + 0.70 * chord,
                    x_max + 0.20 * chord,
                    y_min - 0.18 * chord,
                    y_max + 0.18 * chord,
                ),
            ),
        }
        for view, (mask_function, bounds) in views.items():
            selected_groups = [
                polygons[mask_function(polygons.mean(axis=1))]
                for polygons in polygon_groups.values()
            ]
            selected_groups = [item for item in selected_groups if len(item)]
            if not selected_groups:
                continue
            # Plotting every farfield triangle is unnecessary for a visual
            # preview; retain a deterministic sample only when the view is huge.
            total_selected = sum(len(item) for item in selected_groups)
            stride = max(1, int(math.ceil(total_selected / 180_000)))
            selected_groups = [item[::stride] for item in selected_groups]
            figure, axis = plt.subplots(figsize=(9.0, 5.4))
            for selected in selected_groups:
                axis.add_collection(
                    PolyCollection(
                        selected,
                        facecolors="none",
                        edgecolors="#27364a",
                        linewidths=0.12,
                        rasterized=True,
                    )
                )
            if bounds is None:
                points = np.concatenate(
                    [selected.reshape(-1, 2) for selected in selected_groups],
                    axis=0,
                )
                axis.set_xlim(float(points[:, 0].min()), float(points[:, 0].max()))
                axis.set_ylim(float(points[:, 1].min()), float(points[:, 1].max()))
            else:
                axis.set_xlim(bounds[0], bounds[1])
                axis.set_ylim(bounds[2], bounds[3])
            axis.set_aspect("equal", adjustable="box")
            axis.set(
                xlabel="x [m]",
                ylabel="y [m]",
                title=f"Open {entry['level']} - {view.replace('_', ' ')}",
            )
            axis.grid(False)
            target = destination / f"open_{entry['level']}_{view}.png"
            save_scientific_figure(
                figure, target,
                data=[{"mesh_level": entry["level"], "view": view, "displayed_elements": int(total_selected), "sampling_stride": int(stride)}],
                metadata={"source": str(mesh), "transformation": "deterministic front-surface triangle preview"},
            )
            outputs.append(str(target))
    if not outputs:
        destination.rmdir()
    return outputs


def evaluate_refinement(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    root = refinement_root(project_root)
    manifest = read_json(root / "open_mesh_refinement_manifest.json", {}) or {}
    if not manifest:
        manifest = prepare_refinement(project_root)
    entries = [_quality(project_root, entry) for entry in manifest["entries"]]
    medium_hash_after = _sha256(project_root / MEDIUM_CONFIG)
    medium_unchanged = medium_hash_after == manifest["medium_config_hash_before"]
    if not medium_unchanged:
        raise RuntimeError("The open-medium baseline changed during refinement")
    for entry in entries:
        quality_path = Path(entry["mesh_root"]) / "mesh_quality_report.json"
        if quality_path.is_file():
            shutil.copy2(
                quality_path,
                root / f"open_{entry['level']}_quality_report.json",
            )
    history = list(manifest.get("candidate_history") or [])
    history.append(
        {
            "evaluated_at": utc_stamp(),
            "entries": [
                {
                    "level": entry["level"],
                    "status": entry["status"],
                    "cell_count": entry.get("cell_count"),
                    "failures": entry.get("failures"),
                }
                for entry in entries
            ],
        }
    )
    histogram = _write_quality_histograms(root, entries)
    previews = _collect_previews(root, entries)
    if not previews:
        previews = _write_mesh_previews(root, entries)
    result = {
        **manifest,
        "status": (
            "CANDIDATES_ACCEPTED_NOT_PROMOTED"
            if all(entry["accepted"] for entry in entries)
            else "CANDIDATES_REQUIRE_ITERATION"
        ),
        "medium_config_hash_after": medium_hash_after,
        "medium_unchanged": medium_unchanged,
        "entries": entries,
        "candidate_history": history,
        "quality_histograms": histogram,
        "previews": previews,
        "updated_at": utc_stamp(),
    }
    write_json_atomic(root / "open_mesh_refinement_manifest.json", result)
    return result


def execute_refinement(project_root: Path, *, run: bool) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    manifest = prepare_refinement(project_root)
    if not run:
        return manifest
    for entry in manifest["entries"]:
        clone_variant(
            project_root,
            BASE_VARIANTS["open"],
            str(entry["variant"]),
        )
        started = time.monotonic()
        completed = subprocess.run(
            entry["command"],
            cwd=str(project_root),
            timeout=1800,
        )
        entry["returncode"] = int(completed.returncode)
        entry["wall_time_s"] = time.monotonic() - started
        entry["status"] = (
            "MESH_COMMAND_FINISHED"
            if completed.returncode == 0
            else "MESH_COMMAND_FAILED"
        )
        write_json_atomic(
            refinement_root(project_root) / "open_mesh_refinement_manifest.json",
            {**manifest, "updated_at": utc_stamp()},
        )
        if completed.returncode != 0:
            break
    return evaluate_refinement(project_root)


def _tree_stats(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def _promote_level(
    project_root: Path,
    case_root: Path,
    manifest: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    level = str(entry["level"])
    mesh_id = f"open_{level}"
    destination = case_root / "Meshes" / mesh_id
    incoming = case_root / "Meshes" / f".{mesh_id}.incoming.{uuid.uuid4().hex}"
    incoming.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(Path(entry["mesh_root"]), incoming / "Mesh Data")
        config_dir = incoming / "Configurations"
        config_dir.mkdir(parents=True)
        shutil.copy2(_preset(project_root, level), config_dir / "cfd2d_mesh_config.json")
        old_workflow = destination / "Configurations/cfd2d_workflow_config.json"
        if old_workflow.is_file():
            shutil.copy2(old_workflow, config_dir / old_workflow.name)
        files, size = _tree_stats(incoming)
        archive = (
            case_root
            / "Convergence Study/Archived Mesh Packages"
            / f"{mesh_id}_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.replace(archive)
        incoming.replace(destination)
        configuration = (
            manifest["mesh_convergence_study"]["configurations"][mesh_id]
        )
        configuration.update(
            variant=LEVEL_VARIANTS["open"][level],
            cell_count=int(entry["cell_count"]),
            boundary_layer_layers=int(entry["boundary_layer_layers"]),
            max_non_orthogonality_deg=entry["quality_metrics"][
                "checkMesh_max_non_orthogonality_deg"
            ],
            max_skewness=entry["quality_metrics"]["checkMesh_max_skewness"],
            min_cell_determinant=entry["quality_metrics"][
                "checkMesh_min_cell_determinant"
            ],
            min_face_interpolation_weight=entry["quality_metrics"][
                "checkMesh_min_face_interpolation_weight"
            ],
            min_face_volume_ratio=entry["quality_metrics"][
                "checkMesh_min_face_volume_ratio"
            ],
            quality_status="PASS",
            checkMesh_status="OK",
            accepted=True,
            preset=PRESETS[("open", level)].as_posix(),
            promoted_at=utc_stamp(),
            previous_mesh_archive=str(archive),
        )
        package = manifest["stages"]["mesh"]["packages"][mesh_id]
        package.update(
            folder=f"Meshes/{mesh_id}",
            saved_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            file_count=files,
            size_bytes=size,
            variant=LEVEL_VARIANTS["open"][level],
        )
        return {
            "mesh_id": mesh_id,
            "destination": str(destination),
            "archive": str(archive),
            "cell_count": int(entry["cell_count"]),
        }
    finally:
        if incoming.exists():
            shutil.rmtree(incoming)


def promote_refinement(project_root: Path, *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise RuntimeError("Mesh promotion requires explicit confirmation")
    project_root = Path(project_root).resolve()
    evaluation = evaluate_refinement(project_root)
    if evaluation["status"] != "CANDIDATES_ACCEPTED_NOT_PROMOTED":
        raise RuntimeError(
            "Both open coarse/fine candidates must pass strict quality and cell-count gates"
        )
    case_root = results_case_root(project_root)
    manifest_path = case_root / "case_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    if not manifest:
        raise FileNotFoundError(manifest_path)
    promotions = [
        _promote_level(project_root, case_root, manifest, entry)
        for entry in evaluation["entries"]
    ]
    write_json_atomic(manifest_path, manifest)
    write_json_atomic(
        case_root / "Convergence Study/study_manifest.json",
        manifest["mesh_convergence_study"],
    )
    evaluation.update(
        status="PROMOTED",
        promotions=promotions,
        promoted_at=utc_stamp(),
    )
    write_json_atomic(
        refinement_root(project_root) / "open_mesh_refinement_manifest.json",
        evaluation,
    )
    return evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare")
    execute = sub.add_parser("execute")
    execute.add_argument("--run", action="store_true")
    sub.add_parser("evaluate")
    promote = sub.add_parser("promote")
    promote.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "prepare":
        result = prepare_refinement(args.project_root)
    elif args.action == "execute":
        result = execute_refinement(args.project_root, run=args.run)
    elif args.action == "evaluate":
        result = evaluate_refinement(args.project_root)
    else:
        result = promote_refinement(args.project_root, confirm=args.confirm)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
