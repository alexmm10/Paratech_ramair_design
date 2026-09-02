#!/usr/bin/env python3
"""Run bounded open-mesh transition trials and retain the best URANS candidate."""
from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from ramair_2d_open_experimental_mesh import (
    EXPERIMENT_ID,
    generate,
    read_json,
    run_quality_study,
    write_json_atomic,
)


TRIALS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "interface035_balanced",
        "external_volume": {"interface_tangential_factor": 0.35},
        "internal_volume": {"inlet_size_factor": 0.45},
        "boundary_layer": {
            "segment_divisions": {
                "te": 38, "upper": 800, "leading_or_inlet": 200, "lower": 650,
            },
        },
        "execution": {"mesh_smoothing": 1, "analyse_mesh_quality": False},
    },
    {
        "suffix": "interface040_balanced",
        "external_volume": {"interface_tangential_factor": 0.40},
        "internal_volume": {"inlet_size_factor": 0.425},
        "boundary_layer": {
            "segment_divisions": {
                "te": 38, "upper": 800, "leading_or_inlet": 200, "lower": 650,
            },
        },
        "execution": {"mesh_smoothing": 2, "analyse_mesh_quality": False},
    },
    {
        "suffix": "interface040_upper_lip_relief",
        "external_volume": {"interface_tangential_factor": 0.40},
        "internal_volume": {"inlet_size_factor": 0.425},
        "boundary_layer": {
            "segment_divisions": {
                "te": 40, "upper": 800, "leading_or_inlet": 220, "lower": 650,
            },
        },
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
)


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _score(report: dict[str, Any], baseline_cells: int) -> float:
    if report.get("checkMesh_status") != "OK":
        return float("-inf")
    interpolation = float(report.get("checkMesh_min_face_interpolation_weight") or 0.0)
    volume_ratio = float(report.get("checkMesh_min_face_volume_ratio") or 0.0)
    determinant = float(report.get("checkMesh_min_cell_determinant") or 0.0)
    non_orthogonality = float(report.get("checkMesh_max_non_orthogonality_deg") or 90.0)
    skewness = float(report.get("checkMesh_max_skewness") or 4.0)
    cells = int(report.get("checkMesh_cell_count") or 0)
    cost = max(0.0, (cells - baseline_cells) / max(baseline_cells, 1))
    # The near-threshold interpolation and volume-ratio metrics dominate the
    # ranking; moderate non-orthogonality and cell growth remain advisory.
    return (
        8.0 * interpolation
        + 4.0 * volume_ratio
        + 0.5 * min(determinant, 0.02)
        - 0.004 * non_orthogonality
        - 0.04 * max(0.0, skewness - 1.0)
        - 0.25 * cost
    )


def run_study(root: Path, base_revision: str, prefix: str) -> dict[str, Any]:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    base_path = experiment / "revisions" / base_revision
    base_config = read_json(base_path / "mesh_config.json", {}) or {}
    base_report = read_json(base_path / "mesh_report.json", {}) or {}
    if not base_config or base_report.get("checkMesh_status") != "OK":
        raise ValueError("The base revision must exist and pass checkMesh")
    # Point-wise y1 variation is deliberately purged: the factor study proved
    # it did not alter this transfinite BoundaryLayer topology.
    for key in (
        "inlet_y1_transition_enabled", "inlet_y1_factor",
        "inlet_y1_transition_fraction",
    ):
        base_config.get("boundary_layer", {}).pop(key, None)
    study_dir = experiment / "studies" / prefix
    study_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    baseline_cells = int(base_report.get("checkMesh_cell_count") or 1)
    for trial in TRIALS:
        name = f"{prefix}_{trial['suffix']}"
        update = {key: value for key, value in trial.items() if key != "suffix"}
        config = _merge(base_config, update)
        config["name"] = name
        config_path = study_dir / f"{name}.json"
        write_json_atomic(config_path, config)
        report = generate(root, config_path, name, True)
        rows.append({
            "revision": name,
            "score": _score(report, baseline_cells),
            "checkMesh_status": report.get("checkMesh_status"),
            "cells": report.get("checkMesh_cell_count"),
            "max_non_orthogonality_deg": report.get("checkMesh_max_non_orthogonality_deg"),
            "max_skewness": report.get("checkMesh_max_skewness"),
            "min_interpolation_weight": report.get("checkMesh_min_face_interpolation_weight"),
            "min_volume_ratio": report.get("checkMesh_min_face_volume_ratio"),
            "min_determinant": report.get("checkMesh_min_cell_determinant"),
        })
    viable = [row for row in rows if row["checkMesh_status"] == "OK"]
    best = max(viable, key=lambda row: float(row["score"])) if viable else None
    if best:
        run_quality_study(root, str(best["revision"]))
    result = {
        "schema_version": 1,
        "base_revision": base_revision,
        "baseline": {
            "cells": baseline_cells,
            "min_interpolation_weight": base_report.get("checkMesh_min_face_interpolation_weight"),
            "min_volume_ratio": base_report.get("checkMesh_min_face_volume_ratio"),
            "max_non_orthogonality_deg": base_report.get("checkMesh_max_non_orthogonality_deg"),
            "max_skewness": base_report.get("checkMesh_max_skewness"),
        },
        "trials": rows,
        "selected_best": best,
        "selection_rule": "URANS quality score with checkMesh OK as a hard gate",
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(study_dir / "study_report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--prefix", default="open_urans_quality_20260831")
    args = parser.parse_args()
    result = run_study(args.project_root.resolve(), args.base_revision, args.prefix)
    print(json.dumps(result, indent=2))
    return 0 if result.get("selected_best") else 2


if __name__ == "__main__":
    raise SystemExit(main())
