#!/usr/bin/env python3
"""Reproducible scientific checks for the RamAir Gmsh meshing policy.

The module is intentionally independent from the active mesh workspace.  Its
CLI runs only versioned, compact fixtures in a temporary directory and writes
one small JSON report.  It never replaces a production mesh or approval.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


MESH_SCIENCE_SCHEMA_VERSION = 1

# AIAA High-Lift workshop sequence quoted verbatim as numerical values by
# Ghoreyshi et al. (2016).  These are targets, not ranges such as 2-3 or 4-9.
Y_PLUS_TARGETS: dict[str, float] = {
    "coarse": 1.0,
    "medium": 2.0 / 3.0,
    "fine": 4.0 / 9.0,
    "extra_fine": 8.0 / 27.0,
}

BOUNDARY_LAYER_LAYER_POLICY: dict[str, int] = {
    "coarse": 50,
    "medium": 50,
    "fine": 50,
    "extra_fine": 75,
}


def first_cell_height_audit(
    reynolds: float,
    chord_m: float,
    target_y_plus: float,
    rho_kg_m3: float = 1.225,
    mu_pa_s: float = 1.81e-5,
) -> dict[str, Any]:
    """Compare the project estimate with the two requested reference formulae.

    Smaller first-cell height is the conservative choice for a fixed target
    y+.  The existing skin-friction estimate is retained whenever it is the
    most restrictive; the audit makes that decision explicit and traceable.
    """
    re_l = float(reynolds)
    length = float(chord_m)
    target = float(target_y_plus)
    rho = float(rho_kg_m3)
    mu = float(mu_pa_s)
    if re_l <= 0 or length <= 0 or target <= 0 or rho <= 0 or mu <= 0:
        raise ValueError("Re, chord, y+, rho and mu must all be positive")

    laminar_distance = length * 1.3016 * target / (re_l ** 0.75)
    turbulent_distance = length * ((13.1463 * target) ** 0.875) / (re_l ** 0.90)
    velocity = re_l * mu / (rho * length)
    skin_friction = 0.026 / (re_l ** (1.0 / 7.0))
    wall_shear = 0.5 * rho * velocity * velocity * skin_friction
    friction_velocity = math.sqrt(max(wall_shear / rho, 1.0e-30))
    project_distance = target * mu / (rho * friction_velocity)

    wall_distance_candidates = {
        "project_flat_plate_skin_friction_m": float(project_distance),
        "provided_laminar_formula_m": float(laminar_distance),
        "provided_turbulent_formula_m": float(turbulent_distance),
    }
    # OpenFOAM stores FV unknowns at cell centres.  The y+ wall distance is
    # therefore half of the geometric first-cell height for an orthogonal
    # near-wall cell.
    candidates = {name: 2.0 * value for name, value in wall_distance_candidates.items()}
    selected_source, selected = min(candidates.items(), key=lambda item: item[1])
    return {
        "schema_version": MESH_SCIENCE_SCHEMA_VERSION,
        "reynolds": re_l,
        "chord_m": length,
        "target_y_plus": target,
        "rho_kg_m3": rho,
        "mu_pa_s": mu,
        "candidates": candidates,
        "wall_centre_distance_candidates_m": wall_distance_candidates,
        "selected_wall_centre_distance_m": float(selected / 2.0),
        "selected_first_cell_height_m": float(selected),
        "selected_source": selected_source,
        "finite_volume_height_multiplier": 2.0,
        "discretization_basis": "OpenFOAM cell-centred finite volume: first-cell height = 2*y",
        "selection_rule": "twice the minimum positive wall-centre distance (most restrictive y+ estimate)",
    }


def boundary_layer_stack(first_height_m: float, growth: float, layers: int) -> dict[str, float | int]:
    y1 = float(first_height_m)
    ratio = float(growth)
    count = int(layers)
    if y1 <= 0 or ratio < 1 or count <= 0:
        raise ValueError("first height and layers must be positive; growth must be >= 1")
    total = y1 * count if abs(ratio - 1.0) <= 1.0e-14 else y1 * (ratio**count - 1.0) / (ratio - 1.0)
    return {
        "first_height_m": y1,
        "growth": ratio,
        "layers": count,
        "last_height_m": y1 * ratio ** (count - 1),
        "total_thickness_m": total,
        "maximum_adjacent_growth_percent": 100.0 * (ratio - 1.0),
    }


def _run(command: list[str], cwd: Path, timeout_s: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(1, int(timeout_s)),
            check=False,
        )
        return {
            "command": command,
            "exit_code": int(completed.returncode),
            "wall_time_s": float(time.perf_counter() - started),
            "output": completed.stdout or "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {
            "command": command,
            "exit_code": 124,
            "wall_time_s": float(time.perf_counter() - started),
            "output": str(output),
            "timed_out": True,
        }


def _msh2_counts(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    node_match = re.search(r"\$Nodes\s+(\d+)", text)
    element_match = re.search(r"\$Elements\s+(\d+)", text)
    line_elements = 0
    triangle_elements = 0
    volume_elements = 0
    in_elements = False
    remaining = 0
    for line in text.splitlines():
        if line.strip() == "$Elements":
            in_elements = True
            remaining = -1
            continue
        if not in_elements:
            continue
        if remaining == -1:
            remaining = int(line.strip())
            continue
        if remaining <= 0:
            break
        parts = line.split()
        if len(parts) >= 2:
            element_type = int(parts[1])
            line_elements += element_type in {1, 8, 26, 27, 28}
            triangle_elements += element_type in {2, 9, 21, 23, 25}
            volume_elements += element_type in {4, 5, 6, 7, 11, 12, 13, 14, 17, 18, 19}
        remaining -= 1
    return {
        "nodes": int(node_match.group(1)) if node_match else 0,
        "elements": int(element_match.group(1)) if element_match else 0,
        "line_elements": int(line_elements),
        "triangle_elements": int(triangle_elements),
        "volume_elements": int(volume_elements),
    }


def _rewrite_fixture_boundary(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    for name, patch_type in (("frontAndBack", "empty"), ("airfoil_wall", "wall")):
        pattern = re.compile(rf"({re.escape(name)}\s*\{{.*?\btype\s+)\w+(\s*;)", re.DOTALL)
        text = pattern.sub(rf"\1{patch_type}\2", text)
    path.write_text(text, encoding="utf-8")


def _parse_check_mesh(text: str) -> dict[str, Any]:
    numeric = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"

    def number(pattern: str) -> float | None:
        match = re.search(pattern, text, re.IGNORECASE)
        return float(match.group(1)) if match else None

    return {
        "mesh_ok": "Mesh OK." in text,
        "cells": int(number(r"\bcells:\s*([0-9]+)") or 0),
        "max_non_orthogonality_deg": number(r"Mesh non-orthogonality Max:\s*" + numeric),
        "max_skewness": number(r"Max skewness\s*=\s*" + numeric),
        "min_volume_m3": number(r"Min volume\s*=\s*" + numeric),
    }


def _openfoam_check(mesh_path: Path, work: Path, timeout_s: int) -> dict[str, Any]:
    case_dir = work / "openfoam_check"
    (case_dir / "system").mkdir(parents=True, exist_ok=True)
    (case_dir / "constant").mkdir(parents=True, exist_ok=True)
    (case_dir / "system" / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application foamRun; startFrom startTime; startTime 0; stopAt endTime; endTime 0; "
        "deltaT 1; writeControl timeStep; writeInterval 1;\n",
        encoding="utf-8",
    )
    convert = _run(["gmshToFoam", str(mesh_path)], case_dir, timeout_s)
    boundary = case_dir / "constant" / "polyMesh" / "boundary"
    if convert["exit_code"] == 0 and boundary.is_file():
        _rewrite_fixture_boundary(boundary)
        checked = _run(["checkMesh", "-allGeometry", "-allTopology"], case_dir, timeout_s)
    else:
        checked = {"exit_code": 127, "wall_time_s": 0.0, "output": "gmshToFoam failed", "timed_out": False}
    return {
        "gmshToFoam": {key: value for key, value in convert.items() if key != "output"},
        "checkMesh": {key: value for key, value in checked.items() if key != "output"},
        "metrics": _parse_check_mesh(str(checked.get("output", ""))),
    }


def run_fixture_audit(
    project_root: Path,
    *,
    gmsh_executable: str,
    openfoam_check: bool,
    timeout_s: int,
) -> dict[str, Any]:
    fixtures = project_root / "CFD_2D" / "tests" / "fixtures" / "mesh_science"
    algorithm_template = (fixtures / "hybrid_algorithm.geo").read_text(encoding="utf-8")
    curvature_template = (fixtures / "curvature_transfinite.geo").read_text(encoding="utf-8")
    result: dict[str, Any] = {"algorithms": {}, "curvature_transfinite": {}}
    with tempfile.TemporaryDirectory(prefix="ramair_mesh_science_") as temporary:
        root = Path(temporary)
        for algorithm in (5, 6):
            work = root / f"algorithm_{algorithm}"
            work.mkdir()
            geo = work / "fixture.geo"
            msh = work / "fixture.msh"
            geo.write_text(algorithm_template.replace("__ALGORITHM__", str(algorithm)), encoding="utf-8")
            run = _run(
                [gmsh_executable, "-3", geo.name, "-format", "msh2", "-o", msh.name, "-v", "3"],
                work,
                timeout_s,
            )
            entry: dict[str, Any] = {
                "algorithm": algorithm,
                "name": "Delaunay" if algorithm == 5 else "Frontal-Delaunay",
                "gmsh": {key: value for key, value in run.items() if key != "output"},
                "mesh_counts": _msh2_counts(msh) if run["exit_code"] == 0 and msh.is_file() else {},
            }
            if openfoam_check and run["exit_code"] == 0 and msh.is_file():
                entry["openfoam"] = _openfoam_check(msh, work, timeout_s)
            result["algorithms"][str(algorithm)] = entry

        for mode, directive in (
            ("curvature_only", ""),
            ("curvature_plus_transfinite", "Transfinite Curve {1, 2, 3, 4} = 9;"),
        ):
            work = root / mode
            work.mkdir()
            geo = work / "fixture.geo"
            msh = work / "fixture.msh"
            geo.write_text(curvature_template.replace("__TRANSFINITE_DIRECTIVE__", directive), encoding="utf-8")
            run = _run(
                [gmsh_executable, "-1", geo.name, "-format", "msh2", "-o", msh.name, "-v", "3"],
                work,
                min(timeout_s, 15),
            )
            result["curvature_transfinite"][mode] = {
                "gmsh": {key: value for key, value in run.items() if key != "output"},
                "mesh_counts": _msh2_counts(msh) if run["exit_code"] == 0 and msh.is_file() else {},
            }

    curvature_lines = int(
        result["curvature_transfinite"].get("curvature_only", {}).get("mesh_counts", {}).get("line_elements", 0)
    )
    transfinite_lines = int(
        result["curvature_transfinite"].get("curvature_plus_transfinite", {}).get("mesh_counts", {}).get("line_elements", 0)
    )
    result["curvature_transfinite"]["assessment"] = {
        "curvature_probe_bounded_timeout_s": min(timeout_s, 15),
        "curvature_only_timed_out": bool(
            result["curvature_transfinite"].get("curvature_only", {}).get("gmsh", {}).get("timed_out", False)
        ),
        "transfinite_expected_boundary_elements": 32,
        "transfinite_boundary_elements": transfinite_lines,
        "curvature_only_boundary_elements": curvature_lines,
        "transfinite_precedence_observed": transfinite_lines == 32 and curvature_lines != transfinite_lines,
        "production_policy": (
            "Compute curvature-aware node counts first, then emit Transfinite Curve. "
            "Do not expect Mesh.MeshSizeFromCurvature to alter a transfinite curve."
        ),
        "work_item_2633_status": (
            "NOT_CLAIMED_RESOLVED: the protected work-item text was unavailable; "
            "the bounded fixture records local Gmsh 4.15.2 behavior without claiming it is the same defect."
        ),
    }
    return result


def build_report(project_root: Path, fixture_results: dict[str, Any] | None = None) -> dict[str, Any]:
    physical_path = project_root / "CFD_2D" / "CFD_2D_inputs" / "case_package" / "physical_config.json"
    physical = json.loads(physical_path.read_text(encoding="utf-8")) if physical_path.is_file() else {}
    reynolds = float(physical.get("reynolds", 1.9e6))
    chord = float(physical.get("chord_m", 1.0))
    rho = float(physical.get("rho", physical.get("rho_kg_m3", 1.225)))
    mu = float(physical.get("mu", physical.get("mu_pa_s", 1.81e-5)))
    levels: dict[str, Any] = {}
    for level, target in Y_PLUS_TARGETS.items():
        y1 = first_cell_height_audit(reynolds, chord, target, rho, mu)
        layers = BOUNDARY_LAYER_LAYER_POLICY[level]
        levels[level] = {
            "target_y_plus": target,
            "first_cell_height": y1,
            "boundary_layer_50_or_75": boundary_layer_stack(
                float(y1["selected_first_cell_height_m"]), 1.10, layers
            ),
        }
    return {
        "schema_version": MESH_SCIENCE_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": "TAREA 05 diagnostic; no active mesh replacement and no CFD solver run",
        "paper_interpretation": {
            "y_plus_targets": Y_PLUS_TARGETS,
            "growth_limit": 1.20,
            "preferred_growth": 1.10,
            "minimum_prismatic_layers": 50,
            "comparison_layers": 75,
            "farfield_radius_chord": 50.0,
            "interior_open_cavity_policy": "coarse except inlet and internal trailing-edge transition",
        },
        "physical_inputs": {"reynolds": reynolds, "chord_m": chord, "rho_kg_m3": rho, "mu_pa_s": mu},
        "levels": levels,
        "fixtures": fixture_results or {"status": "NOT_RUN"},
        "production_decision": {
            "algorithm": (
                "Keep Frontal-Delaunay (6) as the general/open starting point and preserve the measured "
                "closed Delaunay (5) presets. Select by fixed-fixture and real checkMesh evidence, not globally."
            ),
            "curvature": "Use the existing project curvature-aware point/node calculation before transfinite constraints.",
            "boundary_layer": "Use 50 layers normally; keep 75 as Extra Fine comparison because the paper reports marginal gains above 50.",
            "approval": "No generated fixture or preset replaces an active mesh without explicit review and approval.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded Gmsh mesh-science fixtures; never runs CFD.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gmsh", default="gmsh")
    parser.add_argument("--run-fixtures", action="store_true")
    parser.add_argument("--openfoam-check", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=120)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    fixture_results: dict[str, Any] | None = None
    if args.run_fixtures:
        gmsh = shutil.which(args.gmsh) or args.gmsh
        fixture_results = run_fixture_audit(
            project_root,
            gmsh_executable=str(gmsh),
            openfoam_check=bool(args.openfoam_check),
            timeout_s=int(args.timeout_s),
        )
    report = build_report(project_root, fixture_results)
    output = args.output if args.output.is_absolute() else project_root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
