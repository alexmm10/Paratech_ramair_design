#!/usr/bin/env python3
"""Run a bounded, reproducible study of opt-in experimental Gmsh controls."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path
from typing import Any

from ramair_2d_open_experimental_mesh import build_2d_mesh, default_config, generate


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _study_config() -> dict[str, Any]:
    config = default_config()
    config["boundary_layer"].update({
        "layers": 30,
        "automatic_bump_matching": True,
        "segment_divisions": {
            "te": 16, "upper": 90, "leading_or_inlet": 50, "lower": 90,
        },
        "inlet_y1_transition_enabled": False,
    })
    config["external_volume"].update({
        "domain_radius_chord": 4.0,
        "farfield_size_chord": 0.8,
        "automatic_extend_enabled": False,
    })
    config["internal_volume"]["automatic_extend_enabled"] = False
    config["execution"].update({
        "mesh_smoothing": 1,
        "post_generation_optimization": "off",
        "post_generation_optimization_iterations": 5,
    })
    return config


def run(root: Path, *, check_mesh_optimizations: bool) -> dict[str, Any]:
    study_root = (
        root / "CFD_2D" / "experimental_meshes" / "open_reference_from_scratch"
        / "controlled_option_study"
    )
    study_root.mkdir(parents=True, exist_ok=True)
    matrix = (
        ("algorithm5_smoothing1", 5, 1, "off"),
        ("algorithm6_smoothing1", 6, 1, "off"),
        ("algorithm6_smoothing0", 6, 0, "off"),
        ("algorithm6_smoothing5", 6, 5, "off"),
        ("algorithm6_laplace2d", 6, 1, "laplace2d"),
        ("algorithm6_relocate2d", 6, 1, "relocate2d"),
        ("algorithm6_laplace_then_relocate", 6, 1, "laplace2d_then_relocate2d"),
    )
    rows: list[dict[str, Any]] = []
    for name, algorithm, smoothing, optimization in matrix:
        revision = study_root / name
        if revision.exists():
            shutil.rmtree(revision)
        revision.mkdir(parents=True)
        config = copy.deepcopy(_study_config())
        config["name"] = name
        config["external_volume"]["mesh_algorithm"] = algorithm
        config["execution"]["mesh_smoothing"] = smoothing
        config["execution"]["post_generation_optimization"] = optimization
        report = build_2d_mesh(root, revision, config)
        quality = report.get("gmsh_quality_diagnostics", {}).get("measures", {})
        rows.append({
            "case": name,
            "algorithm": algorithm,
            "smoothing": smoothing,
            "optimization": optimization,
            "nodes": report.get("nodes_2d"),
            "elements": sum(report.get("elements_2d_by_gmsh_type", {}).values()),
            "generation_time_s": report.get("gmsh_generation_and_optimization_time_s"),
            "analysis_time_s": report.get("gmsh_quality_diagnostics", {}).get("analysis_time_s"),
            "minDetJac": quality.get("minDetJac", {}).get("minimum"),
            "minSIGE": quality.get("minSIGE", {}).get("minimum"),
            "minSICN": quality.get("minSICN", {}).get("minimum"),
            "meanSICN": quality.get("minSICN", {}).get("mean"),
        })

    openfoam_rows: list[dict[str, Any]] = []
    if check_mesh_optimizations:
        base_name = "controlled_q_base"
        for optimization in (
            "off", "laplace2d", "relocate2d", "laplace2d_then_relocate2d"
        ):
            config = copy.deepcopy(_study_config())
            name = base_name if optimization == "off" else f"controlled_q_{optimization}"
            config["name"] = name
            config["execution"]["post_generation_optimization"] = optimization
            if optimization != "off":
                config["execution"]["optimization_base_revision"] = base_name
            config_path = study_root / f"{name}.json"
            config_path.write_text(
                json.dumps(config, indent=2, sort_keys=False), encoding="utf-8"
            )
            report = generate(root, config_path, name, True)
            openfoam_rows.append({
                "case": name,
                "optimization": optimization,
                "checkMesh_status": report.get("checkMesh_status"),
                "determinant": report.get("checkMesh_min_cell_determinant"),
                "interpolation_weight": report.get("checkMesh_min_face_interpolation_weight"),
                "volume_ratio": report.get("checkMesh_min_face_volume_ratio"),
                "non_orthogonality": report.get("checkMesh_max_non_orthogonality_deg"),
                "skewness": report.get("checkMesh_max_skewness"),
                "comparison": report.get("optimization_comparison"),
            })

    result = {"gmsh_controlled_comparison": rows, "openfoam_optimization_comparison": openfoam_rows}
    (study_root / "comparison.json").write_text(
        json.dumps(_jsonable(result), indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--check-mesh-optimizations", action="store_true")
    args = parser.parse_args()
    print(json.dumps(_jsonable(run(args.project_root.resolve(), check_mesh_optimizations=args.check_mesh_optimizations)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
