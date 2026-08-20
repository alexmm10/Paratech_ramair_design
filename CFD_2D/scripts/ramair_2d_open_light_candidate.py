#!/usr/bin/env python3
"""Prepare and evaluate a non-destructive open-airfoil light-mesh sweep."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ramair_2d_closed_open_convergence_study import clone_variant
from ramair_2d_study_registry import (
    active_workspace_root,
    utc_stamp,
    write_json_atomic,
)


BASE_VARIANT = "open_ramair_validation_1m"
BASE_CONFIG = Path(
    "CFD_2D/CFD_2D_inputs/config/mesh_presets/"
    "open_ramair_validation_1m_candidate.json"
)
FACTORS = (1.10, 1.20, 1.30)
RELAXED_KEYS = (
    "open_nearfield_intermediate_size_chord",
    "open_nearfield_outer_size_chord",
    "open_farfield_size_chord",
    "open_cavity_size_chord",
)
PRESERVED_CRITICAL_KEYS = (
    "open_first_cell_height_m",
    "open_boundary_layer_layers",
    "open_boundary_layer_growth",
    "open_zero_thickness_contour_target_nodes",
    "open_zero_thickness_inlet_normal_y1_factor",
    "open_zero_thickness_te_transfinite_min_nodes",
    "open_te_transfinite_min_nodes",
    "open_cavity_wall_size_chord",
    "open_internal_inlet_matching_transition_chord",
    "open_internal_inlet_matching_size_factor",
    "open_internal_te_size_factor",
)
QUALITY_LIMITS = {
    "checkMesh_max_non_orthogonality_deg": ("max", 65.0),
    "checkMesh_max_skewness": ("max", 4.0),
    "checkMesh_min_cell_determinant": ("min", 1.0e-3),
    "checkMesh_min_face_interpolation_weight": ("min", 0.05),
    "checkMesh_min_face_volume_ratio": ("min", 0.01),
}
TARGET_CELL_RANGE = (280_000, 295_000)
OPEN_COARSE_CELL_COUNT = 269_864


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def _factor_tag(factor: float) -> str:
    return f"{factor:.2f}".replace(".", "p")


def candidate_variant(factor: float) -> str:
    return f"{BASE_VARIANT}_light_f{_factor_tag(factor)}"


def candidate_root(project_root: Path) -> Path:
    return (
        active_workspace_root(project_root)
        / "mesh_candidates/open_light"
    )


def prepare_sweep(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    base_path = project_root / BASE_CONFIG
    base = _read_json(base_path)
    root = candidate_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for factor in FACTORS:
        tag = _factor_tag(factor)
        variant = candidate_variant(factor)
        config = json.loads(json.dumps(base))
        before = {key: config.get(key) for key in RELAXED_KEYS}
        for key in RELAXED_KEYS:
            if config.get(key) is None:
                raise KeyError(f"Baseline light-mesh key is missing: {key}")
            config[key] = float(config[key]) * factor
        config["gmsh_threads"] = 12
        config_path = root / f"open_light_factor_{tag}.json"
        write_json_atomic(config_path, config)
        preserved = {
            key: {
                "baseline": base.get(key),
                "candidate": config.get(key),
                "unchanged": base.get(key) == config.get(key),
            }
            for key in PRESERVED_CRITICAL_KEYS
        }
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
            str(config_path),
            "--write-openfoam-mesh",
            "--check-mesh",
            "--overwrite",
            "--previous-output-action",
            "delete",
            "--gmsh-timeout-s",
            "900",
            "--openfoam-tool-timeout-s",
            "600",
            "--gmsh-threads",
            "12",
        ]
        entries.append({
            "factor": factor,
            "variant": variant,
            "config": str(config_path),
            "relaxed_values_before": before,
            "relaxed_values_after": {
                key: config[key] for key in RELAXED_KEYS
            },
            "critical_values": preserved,
            "command": command,
            "mesh_root": str(project_root / "CFD_2D/meshes" / variant),
            "status": "PREPARED_NOT_EXECUTED",
        })
    manifest = {
        "schema_version": 1,
        "status": "PREPARED_NOT_EXECUTED",
        "baseline_variant": BASE_VARIANT,
        "baseline_config": str(base_path),
        "factors": list(FACTORS),
        "relaxed_keys": list(RELAXED_KEYS),
        "preserved_critical_keys": list(PRESERVED_CRITICAL_KEYS),
        "target_cell_range": list(TARGET_CELL_RANGE),
        "quality_limits": QUALITY_LIMITS,
        "promotion_policy": "manual_only",
        "entries": entries,
        "updated_at": utc_stamp(),
    }
    write_json_atomic(root / "sweep_manifest.json", manifest)
    return manifest


def _quality(project_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    mesh_root = Path(entry["mesh_root"])
    report_path = mesh_root / "mesh_quality_report.json"
    if not report_path.is_file():
        return {
            **entry,
            "status": "NOT_MESHED",
            "accepted": False,
            "failures": ["mesh_quality_report_missing"],
        }
    report = _read_json(report_path)
    failures: list[str] = []
    if str(report.get("checkMesh_status")).upper() != "OK":
        failures.append("checkMesh_status")
    cells = int(report.get("checkMesh_cell_count") or 0)
    for key, (direction, limit) in QUALITY_LIMITS.items():
        try:
            value = float(report[key])
        except (KeyError, TypeError, ValueError):
            failures.append(f"{key}_missing")
            continue
        if direction == "max" and value >= limit:
            failures.append(key)
        if direction == "min" and value <= limit:
            failures.append(key)
    target = TARGET_CELL_RANGE[0] <= cells <= TARGET_CELL_RANGE[1]
    quality_ok = not failures
    classification = (
        "TARGET_LIGHT_CANDIDATE"
        if target
        else "RUNTIME_LIGHT_CANDIDATE"
        if cells < OPEN_COARSE_CELL_COUNT
        else "OUTSIDE_TARGET_CELL_RANGE"
    )
    status = (
        "SAVED_CANDIDATE_NOT_PROMOTED"
        if quality_ok and target
        else "QUALITY_OK_NOT_TARGET"
        if quality_ok
        else "REJECTED_QUALITY"
    )
    return {
        **entry,
        "status": status,
        "accepted": bool(quality_ok and target),
        "quality_ok": quality_ok,
        "classification": classification,
        "cell_count": cells,
        "failures": failures,
        "quality_report": str(report_path),
        "quality_metrics": {
            key: report.get(key) for key in QUALITY_LIMITS
        },
        "checkMesh_status": report.get("checkMesh_status"),
    }


def evaluate_sweep(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    root = candidate_root(project_root)
    manifest = _read_json(root / "sweep_manifest.json")
    entries = [_quality(project_root, entry) for entry in manifest["entries"]]
    accepted = [entry for entry in entries if entry["accepted"]]
    selected = (
        min(
            accepted,
            key=lambda entry: abs(
                int(entry["cell_count"]) - sum(TARGET_CELL_RANGE) / 2.0
            ),
        )
        if accepted
        else None
    )
    result = {
        **manifest,
        "status": (
            "CANDIDATE_AVAILABLE_NOT_PROMOTED"
            if selected
            else "NO_ACCEPTED_TARGET_CANDIDATE"
        ),
        "entries": entries,
        "selected_candidate": selected,
        "baseline_overwritten": False,
        "registry_promoted": False,
        "updated_at": utc_stamp(),
    }
    write_json_atomic(root / "sweep_evaluation.json", result)
    return result


def execute_sweep(project_root: Path, *, run: bool) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    manifest = prepare_sweep(project_root)
    if not run:
        return manifest
    for entry in manifest["entries"]:
        variant = str(entry["variant"])
        clone_variant(project_root, BASE_VARIANT, variant)
        completed = subprocess.run(
            entry["command"],
            cwd=str(project_root),
            timeout=1800,
        )
        entry["returncode"] = int(completed.returncode)
        entry["status"] = (
            "MESH_COMMAND_FINISHED"
            if completed.returncode == 0
            else "MESH_COMMAND_FAILED"
        )
        write_json_atomic(
            candidate_root(project_root) / "sweep_manifest.json",
            {**manifest, "entries": manifest["entries"], "updated_at": utc_stamp()},
        )
    return evaluate_sweep(project_root)


def cleanup_rejected_candidates(
    project_root: Path,
    *,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise RuntimeError("Candidate cleanup requires explicit confirmation")
    project_root = Path(project_root).resolve()
    evaluation = evaluate_sweep(project_root)
    removed: list[str] = []
    for entry in evaluation["entries"]:
        if entry.get("accepted"):
            continue
        mesh_root = Path(entry["mesh_root"]).resolve()
        expected = (project_root / "CFD_2D/meshes").resolve()
        if expected not in mesh_root.parents:
            raise RuntimeError(f"Refusing unsafe candidate cleanup: {mesh_root}")
        if mesh_root.is_dir():
            shutil.rmtree(mesh_root)
            removed.append(str(mesh_root))
    report = {
        "status": "REJECTED_CANDIDATES_REMOVED",
        "removed": removed,
        "accepted_candidates_preserved": [
            entry["variant"] for entry in evaluation["entries"]
            if entry.get("accepted")
        ],
        "updated_at": utc_stamp(),
    }
    write_json_atomic(
        candidate_root(project_root) / "cleanup_report.json",
        report,
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare")
    execute = sub.add_parser("execute")
    execute.add_argument("--run", action="store_true")
    sub.add_parser("evaluate")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "prepare":
        result = prepare_sweep(args.project_root)
    elif args.action == "execute":
        result = execute_sweep(args.project_root, run=args.run)
    elif args.action == "evaluate":
        result = evaluate_sweep(args.project_root)
    else:
        result = cleanup_rejected_candidates(
            args.project_root,
            confirm=args.confirm,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
