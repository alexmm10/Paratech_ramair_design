#!/usr/bin/env python3
"""Run a bounded mesh-parameter study and retain only the best real mesh.

The optimizer never runs a CFD solver.  Every candidate launches the canonical
mesh builder, converts through gmshToFoam and runs checkMesh.  Failed candidates
remain visible in the final JSON report but their heavy mesh directories are
deleted after the best candidate is selected.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from openfoam_environment import activate_openfoam_environment


OPEN_VARIANTS = {"open_ramair", "ross_standard_8p4", "ross_minimum_4p0", "standard", "optimized"}
PATTERNS = [
    (1.00, 0.00, 1.00, 1.00),
    (1.15, 0.10, 0.82, 1.10),
    (1.30, 0.18, 0.68, 0.90),
    (0.92, -0.08, 1.12, 1.18),
    (1.10, 0.04, 0.92, 0.82),
]


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def candidate_configurations(
    base: dict[str, Any],
    variant: str,
    iterations: int,
    vary_first_cell: bool,
) -> list[dict[str, Any]]:
    iterations = max(2, min(5, int(iterations)))
    is_open = variant in OPEN_VARIANTS
    node_key = "open_surface_target_nodes" if is_open else "closed_wall_target_nodes"
    bump_key = "open_wall_end_bump_strength" if is_open else "closed_te_bump_strength"
    te_key = "open_te_transfinite_min_nodes" if is_open else "closed_te_target_nodes"
    yplus_key = "open_use_yplus_first_cell_height" if is_open else "closed_use_yplus_first_cell_height"
    first_cell_key = "open_first_cell_height_m" if is_open else "closed_first_cell_height_m"
    base_nodes = max(24, int(base.get(node_key, 720) or 720))
    base_bump = max(1.0e-6, float(base.get(bump_key, 0.70) or 0.70))
    base_te = max(8, int(base.get(te_key, 25 if not is_open else 32) or 8))
    base_first_cell = max(1.0e-10, float(base.get(first_cell_key, 2.0e-5) or 2.0e-5))
    first_cell_is_manual = not bool(base.get(yplus_key, True))
    candidates: list[dict[str, Any]] = []
    for index, (node_factor, bump_delta, te_factor, first_cell_factor) in enumerate(PATTERNS[:iterations], start=1):
        cfg = deepcopy(base)
        cfg[node_key] = max(24, int(round(base_nodes * node_factor)))
        cfg[bump_key] = min(0.98, max(0.15, base_bump + bump_delta))
        cfg[te_key] = max(8, int(round(base_te * te_factor)))
        first_cell_varied = bool(vary_first_cell and first_cell_is_manual)
        if first_cell_varied:
            cfg[first_cell_key] = base_first_cell * first_cell_factor
        cfg["mesh_optimizer_candidate"] = {
            "index": index,
            "node_key": node_key,
            "bump_key": bump_key,
            "te_key": te_key,
            "first_cell_key": first_cell_key,
            "first_cell_varied": first_cell_varied,
            "first_cell_variation_skipped_due_to_yplus": bool(vary_first_cell and not first_cell_is_manual),
        }
        candidates.append(cfg)
    return candidates


def quality_score(report: dict[str, Any]) -> tuple[float, dict[str, float]]:
    mesh_created = bool(report.get("mesh_file_created"))
    gmsh_exit_code = report.get("gmsh_exit_code")
    gmsh_ok = gmsh_exit_code is not None and int(gmsh_exit_code) == 0
    check_ok = str(report.get("checkMesh_status", "")).upper() == "OK"
    cells = max(1, int(report.get("checkMesh_cell_count") or report.get("estimated_cell_count") or 1))
    failed = max(0, int(report.get("checkMesh_failed_checks_count") or len(report.get("checkMesh_failed_checks", []) or [])))
    severe = max(0, int(report.get("checkMesh_severely_non_orthogonal_faces") or 0))
    skew_faces = max(0, int(report.get("checkMesh_highly_skew_faces") or 0))
    small_det = max(0, int(report.get("checkMesh_small_determinant_cells") or 0))
    small_interp = max(0, int(report.get("checkMesh_small_interpolation_weight_faces") or 0))
    small_ratio = max(0, int(report.get("checkMesh_small_volume_ratio_faces") or 0))
    max_nonorth = float(report.get("checkMesh_max_non_orthogonality_deg") or 180.0)
    max_skew = float(report.get("checkMesh_max_skewness") or 100.0)
    min_triangle_angle_raw = report.get("min_triangle_angle")
    max_aspect_ratio_raw = report.get("checkMesh_max_aspect_ratio")
    min_triangle_angle = float(min_triangle_angle_raw) if min_triangle_angle_raw is not None else None
    max_aspect_ratio = float(max_aspect_ratio_raw) if max_aspect_ratio_raw is not None else None
    components = {
        "missing_mesh": 0.0 if mesh_created else 1.0e12,
        "gmsh_failure": 0.0 if gmsh_ok else 1.0e11,
        "checkmesh_failure": 0.0 if check_ok else 1.0e9 + failed * 1.0e8,
        "severe_nonorthogonal_faces": severe * 2.0e6,
        "highly_skew_faces": skew_faces * 2.0e6,
        "nonorthogonality_excess": max(0.0, max_nonorth - 65.0) * 2.0e4,
        "skewness_excess": max(0.0, max_skew - 3.0) * 2.0e5,
        "triangle_angle_deficit": 0.0 if min_triangle_angle is None else max(0.0, 10.0 - min_triangle_angle) * 2.0e5,
        "aspect_ratio_excess": 0.0 if max_aspect_ratio is None else max(0.0, max_aspect_ratio - 100.0) * 1.0e3,
        "small_determinant_fraction": small_det / cells * 1.0e7,
        "small_interpolation_fraction": small_interp / cells * 1.0e7,
        "small_volume_ratio_fraction": small_ratio / cells * 1.0e7,
        "cell_cost": cells / 1000.0,
    }
    return float(sum(components.values())), components


def archive_or_delete_existing(project_root: Path, mesh_root: Path, variant: str, action: str, stamp: str) -> Path | None:
    if not mesh_root.exists():
        return None
    if action == "delete":
        shutil.rmtree(mesh_root)
        return None
    backup = project_root / "Previous Versions/mesh_optimizer_backups" / f"{variant}_{stamp}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(mesh_root), str(backup))
    return backup


def optimize(args: argparse.Namespace) -> Path:
    activate_openfoam_environment()
    project_root = args.case_root.resolve()
    active_config_path = project_root / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    base = read_json(active_config_path)
    candidates = candidate_configurations(base, args.variant, args.iterations, args.vary_first_cell)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_root = project_root / "CFD_2D/app_state/mesh_optimization" / f"{args.variant}_{stamp}"
    mesh_root = project_root / "CFD_2D/meshes" / args.variant
    run_root.mkdir(parents=True, exist_ok=False)
    previous_backup = archive_or_delete_existing(project_root, mesh_root, args.variant, args.previous_output_action, stamp)
    builder = project_root / "CFD_2D/scripts/ramair_2d_mesh_builder.py"
    results: list[dict[str, Any]] = []
    for index, cfg in enumerate(candidates, start=1):
        candidate_config = run_root / f"candidate_{index:02d}.json"
        write_json(candidate_config, cfg)
        if mesh_root.exists():
            shutil.rmtree(mesh_root)
        command = [
            sys.executable, str(builder),
            "--case-root", str(project_root),
            "--variant", args.variant,
            "--domain", args.domain,
            "--mesh-level", args.mesh_level,
            "--mesh-config", str(candidate_config),
            "--gmsh-backend", args.gmsh_backend,
            "--gmsh-timeout-s", str(max(60, args.gmsh_timeout_s)),
            "--openfoam-tool-timeout-s", str(max(60, args.openfoam_timeout_s)),
            "--gmsh-threads", str(max(1, args.gmsh_threads)),
            "--previous-output-action", "delete",
            "--overwrite", "--write-openfoam-mesh", "--check-mesh",
        ]
        print(f"\n=== Mesh optimizer candidate {index}/{len(candidates)} ===", flush=True)
        print("Command:", " ".join(command), flush=True)
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=str(project_root), check=False)
        elapsed = time.perf_counter() - started
        candidate_output = run_root / f"candidate_{index:02d}_mesh"
        if mesh_root.exists():
            shutil.move(str(mesh_root), str(candidate_output))
        report_path = candidate_output / "mesh_quality_report.json"
        report = read_json(report_path) if report_path.is_file() else {}
        score, components = quality_score(report)
        result = {
            "candidate": index,
            "returncode": completed.returncode,
            "wall_time_s": elapsed,
            "score": score,
            "score_components": components,
            "mesh_directory": str(candidate_output),
            "mesh_file_created": bool(report.get("mesh_file_created")),
            "gmsh_exit_code": report.get("gmsh_exit_code"),
            "checkMesh_status": report.get("checkMesh_status"),
            "checkMesh_failed_checks": report.get("checkMesh_failed_checks", []),
            "cell_count": report.get("checkMesh_cell_count") or report.get("estimated_cell_count"),
            "max_nonorthogonality_deg": report.get("checkMesh_max_non_orthogonality_deg"),
            "max_skewness": report.get("checkMesh_max_skewness"),
            "min_triangle_angle_deg": report.get("min_triangle_angle"),
            "max_aspect_ratio": report.get("checkMesh_max_aspect_ratio"),
            "parameters": cfg.get("mesh_optimizer_candidate", {}),
            "selected_values": {
                key: cfg.get(key) for key in [
                    "closed_wall_target_nodes", "closed_te_bump_strength", "closed_te_target_nodes",
                    "closed_first_cell_height_m", "open_surface_target_nodes",
                    "open_wall_end_bump_strength", "open_te_transfinite_min_nodes",
                    "open_first_cell_height_m",
                ] if key in cfg
            },
        }
        results.append(result)
        print(f"Candidate score={score:.6g}; checkMesh={result['checkMesh_status']}; cells={result['cell_count']}", flush=True)

    eligible = [
        result for result in results
        if result["mesh_file_created"] and (Path(result["mesh_directory"]) / "mesh_final.msh").is_file()
    ]
    report_path = project_root / "CFD_2D/reports" / f"mesh_optimization_{args.variant}_{stamp}.json"
    if not eligible:
        if previous_backup is not None and previous_backup.exists() and not mesh_root.exists():
            shutil.move(str(previous_backup), str(mesh_root))
        write_json(report_path, {
            "status": "FAIL_NO_VALID_CANDIDATE",
            "variant": args.variant,
            "results": results,
            "previous_mesh_restored": bool(mesh_root.exists()),
        })
        shutil.rmtree(run_root, ignore_errors=True)
        raise RuntimeError(f"No candidate produced a real mesh. Previous mesh restored when available. Report: {report_path}")

    best = min(eligible, key=lambda item: float(item["score"]))
    best_index = int(best["candidate"])
    best_dir = Path(best["mesh_directory"])
    shutil.move(str(best_dir), str(mesh_root))
    best_config = candidates[best_index - 1]
    best_config.pop("mesh_optimizer_candidate", None)
    config_backup = project_root / "Previous Versions/config_backups" / stamp / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    config_backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(active_config_path, config_backup)
    write_json(active_config_path, best_config)
    final_report = {
        "status": "BEST_CANDIDATE_SELECTED",
        "variant": args.variant,
        "iterations": len(candidates),
        "best_candidate": best_index,
        "best_score": best["score"],
        "best_parameters": best["selected_values"],
        "best_mesh_directory": str(mesh_root),
        "active_config_updated": str(active_config_path),
        "previous_config_backup": str(config_backup),
        "previous_mesh_backup": str(previous_backup) if previous_backup else None,
        "results": results,
        "scoring_note": "Lower is better. Real mesh and checkMesh status dominate; quality fractions and cell cost break ties.",
    }
    write_json(report_path, final_report)
    csv_path = report_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "candidate", "score", "checkMesh_status", "cell_count",
            "max_nonorthogonality_deg", "max_skewness", "min_triangle_angle_deg",
            "max_aspect_ratio", "wall_time_s",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({key: result.get(key) for key in fields})
    shutil.rmtree(run_root, ignore_errors=True)
    print(f"\nSelected candidate {best_index}; score={best['score']:.6g}")
    print(f"Final mesh: {mesh_root}")
    print(f"Optimization report: {report_path}")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded Gmsh/checkMesh parameter optimizer; never runs a CFD solver.")
    parser.add_argument("--case-root", type=Path, default=Path("."))
    parser.add_argument("--variant", required=True)
    parser.add_argument("--domain", default="debug_20c")
    parser.add_argument("--mesh-level", default="debug")
    parser.add_argument("--iterations", type=int, default=3, choices=range(2, 6))
    parser.add_argument("--vary-first-cell", action="store_true")
    parser.add_argument("--gmsh-backend", choices=["auto", "python_api", "cli"], default="python_api")
    parser.add_argument("--gmsh-timeout-s", type=int, default=900)
    parser.add_argument("--openfoam-timeout-s", type=int, default=600)
    parser.add_argument("--gmsh-threads", type=int, default=8)
    parser.add_argument("--previous-output-action", choices=["archive", "delete"], default="archive")
    return parser.parse_args()


def main() -> None:
    optimize(parse_args())


if __name__ == "__main__":
    main()
