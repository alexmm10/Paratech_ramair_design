#!/usr/bin/env python3
"""Run bounded closed/open experimental mesh trials for URANS selection."""
from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from ramair_2d_closed_experimental_mesh import (
    EXPERIMENT_ID as CLOSED_EXPERIMENT_ID,
    generate as generate_closed,
    quality_study as quality_closed,
)
from ramair_2d_open_experimental_mesh import (
    EXPERIMENT_ID as OPEN_EXPERIMENT_ID,
    generate as generate_open,
    read_json,
    run_quality_study as quality_open,
    write_json_atomic,
)


CLOSED_TRIALS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "tangent1200_interface035",
        "boundary_layer": {"segment_divisions": {
            "te": 60, "upper": 540, "leading_or_inlet": 100, "lower": 500,
        }},
        "external_volume": {"interface_tangential_factor": 0.35},
        "execution": {"post_generation_optimization": "off", "mesh_smoothing": 3,
                      "analyse_mesh_quality": False},
    },
    {
        "suffix": "tangent1500_interface045",
        "boundary_layer": {"segment_divisions": {
            "te": 75, "upper": 675, "leading_or_inlet": 125, "lower": 625,
        }},
        "external_volume": {"interface_tangential_factor": 0.45},
        "execution": {"post_generation_optimization": "off", "mesh_smoothing": 3,
                      "analyse_mesh_quality": False},
    },
    {
        "suffix": "tangent1800_interface055",
        "boundary_layer": {"segment_divisions": {
            "te": 90, "upper": 810, "leading_or_inlet": 150, "lower": 750,
        }},
        "external_volume": {"interface_tangential_factor": 0.55},
        "execution": {"post_generation_optimization": "off", "mesh_smoothing": 3,
                      "analyse_mesh_quality": False},
    },
    {
        "suffix": "tangent1500_uniform_body",
        "boundary_layer": {
            "segment_divisions": {
                "te": 75, "upper": 675, "leading_or_inlet": 125, "lower": 625,
            },
            "manual_bump_coefficients": {
                "te": 1.0, "upper": 0.85, "leading_or_inlet": 1.00007,
                "lower": 0.85,
            },
        },
        "external_volume": {"interface_tangential_factor": 0.45},
        "execution": {"post_generation_optimization": "off", "mesh_smoothing": 3,
                      "analyse_mesh_quality": False},
    },
    {
        "suffix": "tangent1800_uniform_body",
        "boundary_layer": {
            "segment_divisions": {
                "te": 90, "upper": 810, "leading_or_inlet": 150, "lower": 750,
            },
            "manual_bump_coefficients": {
                "te": 1.0, "upper": 0.85, "leading_or_inlet": 1.00007,
                "lower": 0.85,
            },
        },
        "external_volume": {"interface_tangential_factor": 0.55},
        "execution": {"post_generation_optimization": "off", "mesh_smoothing": 3,
                      "analyse_mesh_quality": False},
    },
)


OPEN_TRIALS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "baseline_controls_fixed_te",
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
    {
        "suffix": "isolated_tangent1900_interface040",
        "boundary_layer": {"segment_divisions": {
            "te": 44, "upper": 880, "leading_or_inlet": 220, "lower": 756,
        }},
        "external_volume": {"interface_tangential_factor": 0.40},
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
    {
        "suffix": "isolated_tangent2050_interface040",
        "boundary_layer": {"segment_divisions": {
            "te": 48, "upper": 950, "leading_or_inlet": 220, "lower": 832,
        }},
        "external_volume": {"interface_tangential_factor": 0.40},
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
    {
        "suffix": "local_inlet260_interface040",
        "boundary_layer": {"segment_divisions": {
            "te": 42, "upper": 800, "leading_or_inlet": 260, "lower": 680,
        }},
        "external_volume": {"interface_tangential_factor": 0.40},
        "internal_volume": {
            "inlet_size_factor": 0.38,
            "inner_wall_size_chord": 0.0015,
            "inner_wall_transition_distance_chord": 0.060,
            "te_internal_size_chord": 0.00032,
            "te_internal_transition_distance_chord": 0.012,
        },
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
    {
        "suffix": "local_inlet300_interface040",
        "boundary_layer": {"segment_divisions": {
            "te": 44, "upper": 850, "leading_or_inlet": 300, "lower": 720,
        }},
        "external_volume": {"interface_tangential_factor": 0.40},
        "internal_volume": {
            "inlet_size_factor": 0.35,
            "inner_wall_size_chord": 0.0015,
            "inner_wall_transition_distance_chord": 0.065,
            "te_internal_size_chord": 0.00030,
            "te_internal_transition_distance_chord": 0.014,
        },
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
    {
        "suffix": "local_inlet260_interface036",
        "boundary_layer": {"segment_divisions": {
            "te": 42, "upper": 800, "leading_or_inlet": 260, "lower": 680,
        }},
        "external_volume": {"interface_tangential_factor": 0.36},
        "internal_volume": {
            "inlet_size_factor": 0.35,
            "inner_wall_size_chord": 0.0015,
            "inner_wall_transition_distance_chord": 0.065,
            "te_internal_size_chord": 0.00030,
            "te_internal_transition_distance_chord": 0.014,
        },
        "execution": {"mesh_smoothing": 3, "analyse_mesh_quality": False},
    },
    {
        "suffix": "interface045_tangent2050",
        "boundary_layer": {"segment_divisions": {
            "te": 48, "upper": 950, "leading_or_inlet": 240, "lower": 812,
        }},
        "external_volume": {"interface_tangential_factor": 0.45},
        "internal_volume": {
            "inlet_size_factor": 0.45,
            "inner_wall_size_chord": 0.0014,
            "inner_wall_transition_distance_chord": 0.065,
            "te_internal_size_chord": 0.00035,
            "te_internal_transition_distance_chord": 0.012,
        },
        "execution": {"mesh_smoothing": 2, "analyse_mesh_quality": False},
    },
    {
        "suffix": "interface050_tangent2200",
        "boundary_layer": {"segment_divisions": {
            "te": 52, "upper": 1020, "leading_or_inlet": 260, "lower": 868,
        }},
        "external_volume": {"interface_tangential_factor": 0.50},
        "internal_volume": {
            "inlet_size_factor": 0.475,
            "core_size_chord": 0.009,
            "inner_wall_size_chord": 0.0013,
            "inner_wall_transition_distance_chord": 0.070,
            "te_internal_size_chord": 0.00030,
            "te_internal_transition_distance_chord": 0.014,
        },
        "execution": {"mesh_smoothing": 2, "analyse_mesh_quality": False},
    },
    {
        "suffix": "interface055_tangent2400_extend",
        "boundary_layer": {"segment_divisions": {
            "te": 56, "upper": 1110, "leading_or_inlet": 280, "lower": 954,
        }},
        "external_volume": {"interface_tangential_factor": 0.55},
        "internal_volume": {
            "inlet_size_factor": 0.50,
            "core_size_chord": 0.009,
            "inner_wall_size_chord": 0.0012,
            "inner_wall_transition_distance_chord": 0.075,
            "te_internal_size_chord": 0.00028,
            "te_internal_transition_distance_chord": 0.016,
            "automatic_extend_enabled": True,
            "extend_distance_max_chord": 0.12,
            "extend_power": 0.8,
            "extend_size_max_chord": 0.009,
        },
        "execution": {"mesh_smoothing": 2, "analyse_mesh_quality": False},
    },
)


CLOSED_REFINEMENT_TRIALS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "extent20_size4_manual_soft",
        "boundary_layer": {
            "automatic_bump_matching": False,
            "segment_divisions": {
                "te": 78, "upper": 880, "leading_or_inlet": 130, "lower": 820,
            },
            "manual_bump_coefficients": {
                "te": 1.0, "upper": 0.70, "leading_or_inlet": 1.00007,
                "lower": 0.70,
            },
        },
        "external_volume": {
            "farfield_size_chord": 4.0, "extend_distance_max_chord": 20.0,
            "extend_power": 0.50, "extend_size_max_chord": 4.0,
            "interface_tangential_factor": 0.55,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "extent20_size4_manual_softer",
        "boundary_layer": {
            "automatic_bump_matching": False,
            "segment_divisions": {
                "te": 72, "upper": 900, "leading_or_inlet": 120, "lower": 850,
            },
            "manual_bump_coefficients": {
                "te": 1.0, "upper": 0.80, "leading_or_inlet": 1.00007,
                "lower": 0.80,
            },
        },
        "external_volume": {
            "farfield_size_chord": 4.0, "extend_distance_max_chord": 20.0,
            "extend_power": 0.65, "extend_size_max_chord": 4.0,
            "interface_tangential_factor": 0.50,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "extent20_size4_manual_balanced",
        "boundary_layer": {
            "automatic_bump_matching": False,
            "segment_divisions": {
                "te": 80, "upper": 850, "leading_or_inlet": 140, "lower": 820,
            },
            "manual_bump_coefficients": {
                "te": 1.0, "upper": 0.65, "leading_or_inlet": 1.00007,
                "lower": 0.65,
            },
        },
        "external_volume": {
            "farfield_size_chord": 4.0, "extend_distance_max_chord": 20.0,
            "extend_power": 0.45, "extend_size_max_chord": 4.0,
            "interface_tangential_factor": 0.55,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "extent20_size4_automatic",
        "boundary_layer": {
            "automatic_bump_matching": True,
            "segment_divisions": {
                "te": 76, "upper": 880, "leading_or_inlet": 130, "lower": 840,
            },
            "bump_maximum_growth_ratio": 1.15,
            "bump_maximum_size_percent_chord": 0.20,
        },
        "external_volume": {
            "farfield_size_chord": 4.0, "extend_distance_max_chord": 20.0,
            "extend_power": 0.55, "extend_size_max_chord": 4.0,
            "interface_tangential_factor": 0.55,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
)


OPEN_REFINEMENT_TRIALS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "body2240_inlet_short",
        "boundary_layer": {"segment_divisions": {
            "te": 44, "upper": 1040, "leading_or_inlet": 220, "lower": 936,
        }},
        "internal_volume": {
            "inlet_size_factor": 0.45, "core_size_chord": 0.011,
            "inlet_fine_distance_chord": 0.0008, "transition_distance_chord": 0.040,
            "inner_wall_size_chord": 0.0016, "inner_wall_transition_distance_chord": 0.040,
            "te_internal_size_chord": 0.00050, "te_internal_transition_distance_chord": 0.008,
            "automatic_extend_enabled": False,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "body2240_inlet_short_extend",
        "boundary_layer": {"segment_divisions": {
            "te": 44, "upper": 1040, "leading_or_inlet": 220, "lower": 936,
        }},
        "internal_volume": {
            "inlet_size_factor": 0.45, "core_size_chord": 0.011,
            "inlet_fine_distance_chord": 0.0008, "transition_distance_chord": 0.040,
            "inner_wall_size_chord": 0.0016, "inner_wall_transition_distance_chord": 0.040,
            "te_internal_size_chord": 0.00050, "te_internal_transition_distance_chord": 0.008,
            "automatic_extend_enabled": True, "extend_distance_max_chord": 0.080,
            "extend_power": 1.0, "extend_size_max_chord": 0.011,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "body2300_inlet_compact",
        "boundary_layer": {"segment_divisions": {
            "te": 42, "upper": 1080, "leading_or_inlet": 210, "lower": 968,
        }},
        "internal_volume": {
            "inlet_size_factor": 0.46, "core_size_chord": 0.012,
            "inlet_fine_distance_chord": 0.0007, "transition_distance_chord": 0.035,
            "inner_wall_size_chord": 0.00155, "inner_wall_transition_distance_chord": 0.040,
            "te_internal_size_chord": 0.00045, "te_internal_transition_distance_chord": 0.008,
            "automatic_extend_enabled": False,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "body2150_manual_mild_bump",
        "boundary_layer": {
            "automatic_bump_matching": False,
            "manual_four_segment_bump_enabled": True,
            "segment_divisions": {
                "te": 44, "upper": 990, "leading_or_inlet": 220, "lower": 896,
            },
            "manual_bump_coefficients": {
                "te": 1.8, "upper": 0.35, "leading_or_inlet": 1.0001,
                "lower": 0.35,
            },
        },
        "internal_volume": {
            "inlet_size_factor": 0.45, "core_size_chord": 0.011,
            "inlet_fine_distance_chord": 0.0008, "transition_distance_chord": 0.040,
            "inner_wall_size_chord": 0.0016, "inner_wall_transition_distance_chord": 0.040,
            "te_internal_size_chord": 0.00050, "te_internal_transition_distance_chord": 0.008,
            "automatic_extend_enabled": False,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
    {
        "suffix": "body2150_manual_mild_bump_interface_recovery",
        "boundary_layer": {
            "automatic_bump_matching": False,
            "manual_four_segment_bump_enabled": True,
            "segment_divisions": {
                "te": 44, "upper": 990, "leading_or_inlet": 220, "lower": 896,
            },
            "manual_bump_coefficients": {
                "te": 1.8, "upper": 0.35, "leading_or_inlet": 1.0001,
                "lower": 0.35,
            },
        },
        "internal_volume": {
            # Recover the stronger inlet/TE interface sizing of the previous
            # baseline while retaining the compact radial refinement envelope.
            "inlet_size_factor": 0.425, "core_size_chord": 0.011,
            "inlet_fine_distance_chord": 0.0008, "transition_distance_chord": 0.040,
            "inner_wall_size_chord": 0.0016, "inner_wall_transition_distance_chord": 0.040,
            "te_internal_size_chord": 0.00045, "te_internal_transition_distance_chord": 0.0075,
            "automatic_extend_enabled": False,
        },
        "execution": {"mesh_smoothing": 1, "post_generation_optimization": "relocate2d",
                      "post_generation_optimization_iterations": 5, "analyse_mesh_quality": False},
    },
)


SPLIT_PROGRESSION_TRIALS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "automatic_four_bumps_hmax025",
        "boundary_layer": {
            "automatic_bump_matching": True,
            "manual_four_segment_bump_enabled": False,
            "tangential_distribution_method": "four_bumps",
            "bump_maximum_size_percent_chord": 0.25,
        },
        "execution": {"analyse_mesh_quality": False},
    },
    {
        "suffix": "split_progression_hmax050",
        "boundary_layer": {
            "automatic_bump_matching": True,
            "manual_four_segment_bump_enabled": False,
            "tangential_distribution_method": "bump_split_progression",
            "bump_maximum_size_percent_chord": 0.50,
        },
        "execution": {"analyse_mesh_quality": False},
    },
    {
        "suffix": "split_progression_hmax025",
        "boundary_layer": {
            "automatic_bump_matching": True,
            "manual_four_segment_bump_enabled": False,
            "tangential_distribution_method": "bump_split_progression",
            "bump_maximum_size_percent_chord": 0.25,
        },
        "execution": {"analyse_mesh_quality": False},
    },
    {
        "suffix": "split_progression_hmax010",
        "boundary_layer": {
            "automatic_bump_matching": True,
            "manual_four_segment_bump_enabled": False,
            "tangential_distribution_method": "bump_split_progression",
            "bump_maximum_size_percent_chord": 0.10,
        },
        "execution": {"analyse_mesh_quality": False},
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


def _row(report: dict[str, Any], revision: str) -> dict[str, Any]:
    return {
        "revision": revision,
        "checkMesh_status": report.get("checkMesh_status"),
        "cells": report.get("checkMesh_cell_count"),
        "max_non_orthogonality_deg": report.get("checkMesh_max_non_orthogonality_deg"),
        "max_skewness": report.get("checkMesh_max_skewness"),
        "max_aspect_ratio": report.get("checkMesh_max_aspect_ratio"),
        "min_interpolation_weight": report.get("checkMesh_min_face_interpolation_weight"),
        "min_volume_ratio": report.get("checkMesh_min_face_volume_ratio"),
        "min_determinant": report.get("checkMesh_min_cell_determinant"),
    }


def _score(row: dict[str, Any], base_cells: int) -> float:
    if row.get("checkMesh_status") != "OK":
        return float("-inf")
    cost = max(0.0, (int(row.get("cells") or 0) - base_cells) / max(base_cells, 1))
    return (
        10.0 * float(row.get("min_interpolation_weight") or 0.0)
        + 6.0 * float(row.get("min_volume_ratio") or 0.0)
        + 6.0 * min(float(row.get("min_determinant") or 0.0), 0.03)
        - 0.003 * float(row.get("max_non_orthogonality_deg") or 90.0)
        - 0.04 * max(0.0, float(row.get("max_skewness") or 4.0) - 1.0)
        - 0.12 * cost
    )


def run(
    root: Path, topology: str, base_revision: str, prefix: str,
    campaign: str = "baseline",
) -> dict[str, Any]:
    closed = topology == "closed"
    experiment_id = CLOSED_EXPERIMENT_ID if closed else OPEN_EXPERIMENT_ID
    generate: Callable[..., dict[str, Any]] = generate_closed if closed else generate_open
    quality: Callable[..., dict[str, Any]] = quality_closed if closed else quality_open
    trials = (
        SPLIT_PROGRESSION_TRIALS if campaign == "split_progression" else
        CLOSED_REFINEMENT_TRIALS if closed and campaign == "refine20260901" else
        OPEN_REFINEMENT_TRIALS if not closed and campaign == "refine20260901" else
        CLOSED_TRIALS if closed else OPEN_TRIALS
    )
    experiment = root / "CFD_2D/experimental_meshes" / experiment_id
    base_path = experiment / "revisions" / base_revision
    base_config = read_json(base_path / "mesh_config.json", {}) or {}
    base_report = read_json(base_path / "mesh_report.json", {}) or {}
    if not base_config or base_report.get("checkMesh_status") != "OK":
        raise ValueError("The base revision must exist and pass checkMesh")
    study_dir = experiment / "studies" / prefix
    study_dir.mkdir(parents=True, exist_ok=True)
    base_cells = int(base_report.get("checkMesh_cell_count") or 1)
    rows: list[dict[str, Any]] = []
    for trial in trials:
        revision = f"{prefix}_{trial['suffix']}"
        update = {key: value for key, value in trial.items() if key != "suffix"}
        config = _merge(base_config, update)
        config["name"] = revision
        config_path = study_dir / f"{revision}.json"
        write_json_atomic(config_path, config)
        existing_report = read_json(
            experiment / "revisions" / revision / "mesh_report.json", {}
        ) or {}
        try:
            report = (
                existing_report
                if existing_report.get("checkMesh_status") == "OK"
                else generate(root, config_path, revision, True)
            )
            row = _row(report, revision)
        except (RuntimeError, ValueError) as exc:
            row = {
                "revision": revision,
                "checkMesh_status": "CONFIGURATION_BLOCKED",
                "error": str(exc),
            }
        row["score"] = _score(row, base_cells)
        rows.append(row)
    viable = [row for row in rows if row["checkMesh_status"] == "OK"]
    best = max(viable, key=lambda item: float(item["score"])) if viable else None
    if best:
        quality(root, str(best["revision"]))
    result = {
        "schema_version": 1,
        "topology": topology,
        "campaign": campaign,
        "base_revision": base_revision,
        "baseline": _row(base_report, base_revision),
        "trials": rows,
        "selected_best": best,
        "selection_rule": "checkMesh hard gate; interpolation, volume ratio and determinant weighted for URANS",
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(study_dir / "study_report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--topology", choices=("closed", "open"), required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument(
        "--campaign", choices=("baseline", "refine20260901", "split_progression"),
        default="baseline",
    )
    args = parser.parse_args()
    result = run(
        args.project_root.resolve(), args.topology, args.base_revision,
        args.prefix, args.campaign,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("selected_best") else 2


if __name__ == "__main__":
    raise SystemExit(main())
