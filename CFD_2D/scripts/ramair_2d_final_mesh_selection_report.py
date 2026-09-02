#!/usr/bin/env python3
"""Persist the final closed/open URANS mesh selections and comparisons."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


METRICS = {
    "cells": "checkMesh_cell_count",
    "max_non_orthogonality_deg": "checkMesh_max_non_orthogonality_deg",
    "max_skewness": "checkMesh_max_skewness",
    "max_aspect_ratio": "checkMesh_max_aspect_ratio",
    "min_interpolation_weight": "checkMesh_min_face_interpolation_weight",
    "min_volume_ratio": "checkMesh_min_face_volume_ratio",
    "min_determinant": "checkMesh_min_cell_determinant",
}

CASES = {
    "closed": {
        "experiment": "closed_reference_from_scratch",
        "selected": "closed_urans_refine_20260901_extent20_size4_manual_balanced",
        "previous": "closed_urans_final_20260831_tangent1800_interface055",
        "medium": "reference_uncut_validation_1m",
        "reason": (
            "Manual mild wall bumping and a 20c/4c Extend envelope reduce cost while improving "
            "determinant, interpolation weight and volume ratio without the automatic variant's skewness penalty."
        ),
    },
    "open": {
        "experiment": "open_reference_from_scratch",
        "selected": "open_urans_refine_20260901_body2150_manual_mild_bump",
        "previous": "open_urans_final_20260831_isolated_tangent2050_interface040",
        "medium": "open_ramair_validation_1m",
        "reason": (
            "A less aggressive body bump and compact inlet refinement improve determinant, volume ratio, "
            "non-orthogonality, skewness and aspect ratio with fewer cells; the small interpolation-weight "
            "tradeoff remains comfortably above the OpenFOAM warning level."
        ),
    },
}


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def metrics(report: dict[str, Any]) -> dict[str, float | int | None]:
    return {name: report.get(key) for name, key in METRICS.items()}


def deltas(candidate: dict[str, Any], reference: dict[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name in METRICS:
        try:
            current = float(candidate[name])
            baseline = float(reference[name])
            result[name] = 100.0 * (current - baseline) / baseline
        except (TypeError, ValueError, ZeroDivisionError):
            result[name] = None
    return result


def build(root: Path) -> dict[str, Any]:
    output: dict[str, Any] = {"schema_version": 1, "selections": {}}
    for topology, config in CASES.items():
        experiment = root / "CFD_2D/experimental_meshes" / config["experiment"]
        selected_dir = experiment / "revisions" / config["selected"]
        previous_dir = experiment / "revisions" / config["previous"]
        medium_dir = root / "CFD_2D/meshes" / config["medium"]
        selected_report = read(selected_dir / "mesh_report.json")
        previous_report = read(previous_dir / "mesh_report.json")
        medium_report = read(medium_dir / "mesh_quality_report.json")
        selected_metrics = metrics(selected_report)
        previous_metrics = metrics(previous_report)
        medium_metrics = metrics(medium_report)
        row = {
            "topology": topology,
            "selected_revision": config["selected"],
            "selected_directory": str(selected_dir),
            "mesh_file": str(selected_dir / "mesh_final.msh"),
            "checkMesh_status": selected_report.get("checkMesh_status"),
            "quality_tables_generated": (
                selected_dir / "quality_distributions/quality_distributions.json"
            ).is_file(),
            "selection_reason": config["reason"],
            "selected": selected_metrics,
            "previous_experimental": {
                "revision": config["previous"],
                "metrics": previous_metrics,
                "candidate_delta_percent": deltas(selected_metrics, previous_metrics),
            },
            "validation_medium": {
                "mesh_id": config["medium"],
                "metrics": medium_metrics,
                "candidate_delta_percent": deltas(selected_metrics, medium_metrics),
            },
        }
        output["selections"][topology] = row
        (experiment / "final_urans_selection.json").write_text(
            json.dumps(row, indent=2) + "\n", encoding="utf-8"
        )
    destination = root / "CFD_2D/experimental_meshes/final_urans_mesh_comparison.json"
    destination.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.project_root.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
