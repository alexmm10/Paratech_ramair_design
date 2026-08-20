#!/usr/bin/env python3
"""Build protected coarse/fine alpha=8 meshes around the existing medium validation case."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BASE_VARIANT = "reference_uncut_validation_1m"
ALPHA_DEG = 8.0
LEVEL_VARIANTS = {
    "coarse": "reference_uncut_validation_1m_coarse",
    "medium": BASE_VARIANT,
    "fine": "reference_uncut_validation_1m_fine",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def replace_variant(value: Any, source: str, target: str) -> Any:
    if isinstance(value, dict):
        return {key: replace_variant(item, source, target) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_variant(item, source, target) for item in value]
    if isinstance(value, str):
        return value.replace(source, target)
    return value


def clone_geometry_package(root: Path, target_variant: str, replace: bool) -> None:
    for collection in ("geometry", "case_package"):
        source = root / "CFD_2D/CFD_2D_inputs" / collection / BASE_VARIANT
        target = root / "CFD_2D/CFD_2D_inputs" / collection / target_variant
        if target.exists():
            if not replace:
                continue
            shutil.rmtree(target)
        shutil.copytree(source, target)
        for path in target.rglob("*.json"):
            try:
                payload = read_json(path)
            except (json.JSONDecodeError, TypeError):
                continue
            write_json_atomic(path, replace_variant(payload, BASE_VARIANT, target_variant))


def run_checked(command: list[str], cwd: Path, timeout_s: int) -> None:
    print("COMMAND:", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=str(cwd), text=True, timeout=timeout_s)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {command}")


def approve_mesh(root: Path, variant: str) -> None:
    report_path = root / "CFD_2D/meshes" / variant / "mesh_quality_report.json"
    report = read_json(report_path)
    check_status = str(report.get("checkMesh_status", "")).upper()
    status = str(report.get("status", "")).upper()
    poly = root / "CFD_2D/meshes" / variant / "constant/polyMesh/boundary"
    if check_status != "OK" or status not in {"PASS", "WARNING_ACCEPTABLE"} or not poly.is_file():
        raise RuntimeError(
            f"Mesh {variant} is not eligible for the study: quality={status}, "
            f"checkMesh={check_status}, polyMesh={poly.is_file()}"
        )
    flag = report_path.parent / "MESH_APPROVED.flag"
    flag.write_text(
        f"approved_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"variant={variant}\nstatus={status}\nopenfoam_check_ok=True\n"
        "purpose=LS1_0417_alpha8_mesh_refinement_study\n",
        encoding="utf-8",
    )


def verify_case_matches_base(root: Path, variant: str) -> None:
    safe = "alpha_p8p000"
    base = read_json(root / "CFD_2D/openfoam_cases" / BASE_VARIANT / safe / "case_config.json")
    candidate = read_json(root / "CFD_2D/openfoam_cases" / variant / safe / "case_config.json")
    keys = (
        "alpha_deg", "reynolds", "mach_input", "chord_m", "rho", "mu",
        "velocity_m_s", "solver", "solver_module", "turbulence_model", "ddt_scheme",
    )
    mismatches = {
        key: {"base": base.get(key), "candidate": candidate.get(key)}
        for key in keys
        if base.get(key) != candidate.get(key)
    }
    if mismatches:
        raise RuntimeError(f"Generated case does not match protected medium operating point: {mismatches}")


def build_level(root: Path, level: str, timeout_s: int, replace: bool) -> dict[str, Any]:
    variant = LEVEL_VARIANTS[level]
    if level == "medium":
        report = read_json(root / "CFD_2D/meshes" / variant / "mesh_quality_report.json")
        return {
            "level": level,
            "variant": variant,
            "protected_existing_data": True,
            "mesh_report": str(root / "CFD_2D/meshes" / variant / "mesh_quality_report.json"),
            "case_dir": str(root / "CFD_2D/openfoam_cases" / variant / "alpha_p8p000"),
            "cell_count": report.get("checkMesh_cell_count"),
            "quality_status": report.get("status"),
            "checkMesh_status": report.get("checkMesh_status"),
        }
    clone_geometry_package(root, variant, replace)
    preset = (
        root / "CFD_2D/CFD_2D_inputs/config/mesh_presets"
        / f"reference_uncut_validation_1m_{level}.json"
    )
    mesh_command = [
        sys.executable,
        str(root / "CFD_2D/scripts/ramair_2d_mesh_builder.py"),
        "--case-root", str(root),
        "--variant", variant,
        "--domain", "circular_50c",
        "--mesh-level", "custom",
        "--mesh-config", str(preset),
        "--write-openfoam-mesh",
        "--check-mesh",
        "--overwrite",
        "--previous-output-action", "delete",
        "--gmsh-timeout-s", str(int(timeout_s)),
        "--openfoam-tool-timeout-s", "600",
    ]
    run_checked(mesh_command, root, timeout_s + 900)
    approve_mesh(root, variant)
    writer_command = [
        sys.executable,
        str(root / "CFD_2D/scripts/ramair_2d_openfoam_case_writer.py"),
        "--case-root", str(root),
        "--variant", variant,
        "--alpha", str(ALPHA_DEG),
        "--reynolds", "1900000",
        "--write-case",
        "--overwrite",
        "--existing-case-action", "delete",
        "--require-converted-polymesh",
    ]
    run_checked(writer_command, root, 900)
    verify_case_matches_base(root, variant)
    report = read_json(root / "CFD_2D/meshes" / variant / "mesh_quality_report.json")
    return {
        "level": level,
        "variant": variant,
        "protected_existing_data": False,
        "mesh_config": str(preset),
        "mesh_report": str(root / "CFD_2D/meshes" / variant / "mesh_quality_report.json"),
        "case_dir": str(root / "CFD_2D/openfoam_cases" / variant / "alpha_p8p000"),
        "cell_count": report.get("checkMesh_cell_count"),
        "quality_status": report.get("status"),
        "checkMesh_status": report.get("checkMesh_status"),
        "max_non_orthogonality_deg": report.get("checkMesh_max_non_orthogonality_deg"),
        "max_skewness": report.get("checkMesh_max_skewness"),
    }


def existing_level(root: Path, level: str) -> dict[str, Any] | None:
    variant = LEVEL_VARIANTS[level]
    report_path = root / "CFD_2D/meshes" / variant / "mesh_quality_report.json"
    case_dir = root / "CFD_2D/openfoam_cases" / variant / "alpha_p8p000"
    if not report_path.is_file() or not (case_dir / "system/controlDict").is_file():
        return None
    report = read_json(report_path)
    return {
        "level": level,
        "variant": variant,
        "protected_existing_data": level == "medium",
        "mesh_report": str(report_path),
        "case_dir": str(case_dir),
        "cell_count": report.get("checkMesh_cell_count"),
        "quality_status": report.get("status"),
        "checkMesh_status": report.get("checkMesh_status"),
        "max_non_orthogonality_deg": report.get("checkMesh_max_non_orthogonality_deg"),
        "max_skewness": report.get("checkMesh_max_skewness"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--levels",
        choices=["coarse", "fine"],
        nargs="*",
        default=["coarse", "fine"],
        help="Levels to rebuild. Pass --levels with no values to refresh only the study manifest.",
    )
    parser.add_argument("--gmsh-timeout-s", type=int, default=900)
    parser.add_argument("--replace-generated", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    study_dir = root / "Results/LS1_0417_alpha8_mesh_refinement"
    study_dir.mkdir(parents=True, exist_ok=True)
    rows = [build_level(root, "medium", args.gmsh_timeout_s, False)]
    requested = set(args.levels)
    for level in ("coarse", "fine"):
        if level in requested:
            rows.append(build_level(root, level, args.gmsh_timeout_s, bool(args.replace_generated)))
        else:
            current = existing_level(root, level)
            if current is not None:
                rows.append(current)
    order = {"coarse": 0, "medium": 1, "fine": 2}
    rows.sort(key=lambda row: order[str(row["level"])])
    report = {
        "status": "PREPARED",
        "purpose": "alpha=8 LS(1)-0417 mesh-refinement study",
        "base_variant": BASE_VARIANT,
        "alpha_deg": ALPHA_DEG,
        "existing_medium_case_preserved": True,
        "levels": rows,
        "next_step": "Run each prepared case, postprocess it, then execute ramair_2d_mesh_refinement_analysis.py.",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(study_dir / "study_manifest.json", report)
    for row in rows:
        level_dir = study_dir / str(row["level"])
        level_dir.mkdir(exist_ok=True)
        write_json_atomic(level_dir / "active_paths.json", row)
        mesh_report = Path(str(row["mesh_report"]))
        if mesh_report.is_file():
            shutil.copy2(mesh_report, level_dir / "mesh_quality_report.json")
        config = root / "CFD_2D/meshes" / str(row["variant"]) / "mesh_config_used.json"
        if config.is_file():
            shutil.copy2(config, level_dir / "mesh_config_used.json")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
