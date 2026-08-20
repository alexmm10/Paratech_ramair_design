#!/usr/bin/env python3
"""Build and package closed/open mesh-convergence baselines.

The study contains coarse, medium and fine meshes for the closed validation
airfoil and the open ram-air validation geometry.  No CFD solver is executed.
Every packaged mesh must come from a real Gmsh run and pass OpenFOAM checkMesh.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from project_layout import find_project_root, project_path
from ramair_case_library import (
    STAGE_COLLECTION_FOLDERS,
    copy_item,
    schema3_manifest,
    seed_standard_solver_package,
    tree_stats,
    variant_profile,
    write_json_atomic,
)


STUDY_ID = "RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6"
BASE_VARIANTS = {
    "closed": "reference_uncut_validation_1m",
    "open": "open_ramair_validation_1m",
}
LEVEL_VARIANTS = {
    "closed": {
        "coarse": "reference_uncut_validation_1m_coarse",
        "medium": "reference_uncut_validation_1m",
        "fine": "reference_uncut_validation_1m_fine",
    },
    "open": {
        "coarse": "open_ramair_validation_1m_coarse",
        "medium": "open_ramair_validation_1m",
        "fine": "open_ramair_validation_1m_fine",
    },
}
PRESETS = {
    (topology, level): Path(
        "CFD_2D/CFD_2D_inputs/config/mesh_presets"
    )
    / f"{BASE_VARIANTS[topology]}_{level}.json"
    for topology in ("closed", "open")
    for level in ("coarse", "fine")
}
PRESETS[("closed", "medium")] = Path(
    "CFD_2D/CFD_2D_inputs/config/mesh_presets/reference_uncut_validation_1m.json"
)
PRESETS[("open", "medium")] = Path(
    "CFD_2D/CFD_2D_inputs/config/mesh_presets/open_ramair_validation_1m_candidate.json"
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def replace_variant(value: Any, source: str, target: str) -> Any:
    if isinstance(value, dict):
        return {
            key: replace_variant(item, source, target)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_variant(item, source, target) for item in value]
    if isinstance(value, str):
        return value.replace(source, target)
    return value


def clone_variant(root: Path, source_variant: str, target_variant: str) -> None:
    """Clone generated geometry inputs without altering the physical geometry."""
    if source_variant == target_variant:
        return
    for collection in ("geometry", "case_package"):
        source = root / "CFD_2D/CFD_2D_inputs" / collection / source_variant
        target = root / "CFD_2D/CFD_2D_inputs" / collection / target_variant
        if not source.is_dir():
            raise FileNotFoundError(source)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        for path in target.rglob("*.json"):
            try:
                payload = read_json(path)
            except (json.JSONDecodeError, TypeError):
                continue
            write_json_atomic(
                path,
                replace_variant(payload, source_variant, target_variant),
            )


def run_mesh(
    root: Path,
    topology: str,
    level: str,
    gmsh_timeout_s: int,
) -> None:
    variant = LEVEL_VARIANTS[topology][level]
    clone_variant(root, BASE_VARIANTS[topology], variant)
    command = [
        sys.executable,
        str(root / "CFD_2D/scripts/ramair_2d_mesh_builder.py"),
        "--case-root",
        str(root),
        "--variant",
        variant,
        "--domain",
        "circular_50c",
        "--mesh-level",
        "custom",
        "--mesh-config",
        str(root / PRESETS[(topology, level)]),
        "--write-openfoam-mesh",
        "--check-mesh",
        "--overwrite",
        "--previous-output-action",
        "delete",
        "--gmsh-timeout-s",
        str(gmsh_timeout_s),
        "--openfoam-tool-timeout-s",
        "600",
        "--gmsh-threads",
        "12",
    ]
    print("COMMAND:", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=str(root),
        text=True,
        timeout=gmsh_timeout_s + 900,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{topology}/{level} mesh command failed with exit code "
            f"{completed.returncode}"
        )


def quality_summary(root: Path, topology: str, level: str) -> dict[str, Any]:
    variant = LEVEL_VARIANTS[topology][level]
    mesh_root = root / "CFD_2D/meshes" / variant
    report_path = mesh_root / "mesh_quality_report.json"
    report = read_json(report_path)
    summary = {
        "id": f"{topology}_{level}",
        "topology": topology,
        "level": level,
        "variant": variant,
        "preset": PRESETS[(topology, level)].as_posix(),
        "cell_count": report.get("checkMesh_cell_count"),
        "quality_status": report.get("status"),
        "checkMesh_status": report.get("checkMesh_status"),
        "max_non_orthogonality_deg": report.get(
            "checkMesh_max_non_orthogonality_deg"
        ),
        "max_skewness": report.get("checkMesh_max_skewness"),
        "min_cell_determinant": report.get("checkMesh_min_cell_determinant"),
        "min_face_interpolation_weight": report.get(
            "checkMesh_min_face_interpolation_weight"
        ),
        "min_face_volume_ratio": report.get("checkMesh_min_face_volume_ratio"),
        "boundary_layer_layers": report.get(
            "boundary_layer_layers_requested"
        ),
        "mesh_root": str(mesh_root),
        "report": str(report_path),
    }
    boundary = mesh_root / "constant/polyMesh/boundary"
    failures: list[str] = []
    if str(summary["quality_status"]).upper() not in {
        "PASS",
        "WARNING_ACCEPTABLE",
    }:
        failures.append("quality_status")
    if str(summary["checkMesh_status"]).upper() != "OK":
        failures.append("checkMesh_status")
    if not boundary.is_file():
        failures.append("converted_polyMesh")
    limits = {
        "max_non_orthogonality_deg": (65.0, lambda value, limit: value < limit),
        "max_skewness": (4.0, lambda value, limit: value < limit),
        "min_cell_determinant": (1e-3, lambda value, limit: value > limit),
        "min_face_interpolation_weight": (
            0.05,
            lambda value, limit: value > limit,
        ),
        "min_face_volume_ratio": (0.01, lambda value, limit: value > limit),
    }
    for key, (limit, predicate) in limits.items():
        try:
            value = float(summary[key])
        except (TypeError, ValueError):
            failures.append(f"{key}_missing")
            continue
        if not predicate(value, limit):
            failures.append(key)
    summary["acceptance_failures"] = failures
    summary["accepted"] = not failures
    return summary


def verify_series(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if not row["accepted"]:
            raise RuntimeError(
                f"Mesh {row['id']} is not acceptable: "
                f"{row['acceptance_failures']}"
            )
    for topology in ("closed", "open"):
        ordered = [
            next(
                row
                for row in rows
                if row["topology"] == topology and row["level"] == level
            )
            for level in ("coarse", "medium", "fine")
        ]
        counts = [int(row["cell_count"]) for row in ordered]
        if not counts[0] < counts[1] < counts[2]:
            raise RuntimeError(
                f"{topology} cell counts are not strictly increasing: {counts}"
            )


def package_metadata(
    actual_path: Path,
    relative_path: Path,
    variant: str,
) -> dict[str, Any]:
    files, size = tree_stats(actual_path)
    return {
        "folder": relative_path.as_posix(),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": files,
        "size_bytes": size,
        "variant": variant,
        "alpha_deg": 4.0,
    }


def write_workflow_variant(
    source: Path,
    destination: Path,
    variant: str,
) -> None:
    workflow = read_json(source)
    geometry = dict(workflow.get("geometry") or {})
    geometry["variant"] = variant
    geometry["domain"] = "circular_50c"
    workflow["geometry"] = geometry
    mesh = dict(workflow.get("mesh") or {})
    mesh["mesh_level"] = "custom"
    mesh["domain"] = "circular_50c"
    workflow["mesh"] = mesh
    write_json_atomic(destination, workflow)


def copy_configuration_packages(
    root: Path,
    incoming: Path,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    for stage in ("geometry", "case", "mesh"):
        stages[stage] = {
            "folder": STAGE_COLLECTION_FOLDERS[stage],
            "active_package": "closed_medium",
            "packages": {},
        }
    default_project_config = project_path(
        root, "configurations", "default_case_config.json"
    )
    system_config = project_path(
        root, "configurations", "ramair_catia_system_config.json"
    )
    workflow_source = (
        root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"
    )
    for row in rows:
        package = str(row["id"])
        variant = str(row["variant"])
        geometry_dir = (
            incoming / STAGE_COLLECTION_FOLDERS["geometry"] / package
        )
        copy_item(
            root / "CFD_2D/CFD_2D_inputs/geometry" / variant,
            geometry_dir / "CFD Geometry",
        )
        copy_item(
            default_project_config,
            geometry_dir / "Configurations/default_case_config.json",
        )
        copy_item(
            system_config,
            geometry_dir / "Configurations/ramair_catia_system_config.json",
        )
        profile = variant_profile(root, variant)
        if profile is not None:
            copy_item(profile, geometry_dir / "Airfoil Profile" / profile.name)
        if row["topology"] == "open":
            base_profile = project_path(root, "profiles", "NASA LS1-0417.dat")
            if base_profile.is_file():
                copy_item(
                    base_profile,
                    geometry_dir / "Airfoil Profile" / base_profile.name,
                )

        case_dir = incoming / STAGE_COLLECTION_FOLDERS["case"] / package
        copy_item(
            root / "CFD_2D/CFD_2D_inputs/case_package" / variant,
            case_dir / "Case Package",
        )
        config_dir = case_dir / "CFD Configurations"
        config_dir.mkdir(parents=True, exist_ok=True)
        write_workflow_variant(
            workflow_source,
            config_dir / "cfd2d_workflow_config.json",
            variant,
        )
        copy_item(
            root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json",
            config_dir / "cfd2d_solver_config.json",
        )

        mesh_dir = incoming / STAGE_COLLECTION_FOLDERS["mesh"] / package
        copy_item(
            root / "CFD_2D/meshes" / variant,
            mesh_dir / "Mesh Data",
        )
        copy_item(
            root / PRESETS[(str(row["topology"]), str(row["level"]))],
            mesh_dir / "Configurations/cfd2d_mesh_config.json",
        )
        write_workflow_variant(
            workflow_source,
            mesh_dir / "Configurations/cfd2d_workflow_config.json",
            variant,
        )

        geometry_relative = Path(STAGE_COLLECTION_FOLDERS["geometry"]) / package
        case_relative = Path(STAGE_COLLECTION_FOLDERS["case"]) / package
        mesh_relative = Path(STAGE_COLLECTION_FOLDERS["mesh"]) / package
        stages["geometry"]["packages"][package] = package_metadata(
            geometry_dir,
            geometry_relative,
            variant,
        )
        stages["case"]["packages"][package] = package_metadata(
            case_dir,
            case_relative,
            variant,
        )
        stages["mesh"]["packages"][package] = package_metadata(
            mesh_dir,
            mesh_relative,
            variant,
        )
    return stages


def build_workspace(
    root: Path,
    rows: list[dict[str, Any]],
    case_name: str,
    existing_action: str,
) -> Path:
    library = project_path(root, "results_library")
    destination = library / case_name
    if destination.exists():
        if existing_action == "keep":
            raise FileExistsError(destination)
        if existing_action == "archive":
            backup = (
                library
                / ".case_backups"
                / f"{case_name}_{time.strftime('%Y%m%d_%H%M%S')}"
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(backup))
        elif existing_action == "replace-generated":
            old = read_json(destination / "case_manifest.json")
            study = old.get("mesh_convergence_study") or {}
            if study.get("study_id") != STUDY_ID:
                raise RuntimeError(
                    f"Refusing to replace unrelated work case: {destination}"
                )
            shutil.rmtree(destination)
        else:
            raise ValueError(existing_action)
    incoming = library / f".{case_name}.incoming.{uuid.uuid4().hex}"
    incoming.mkdir(parents=True, exist_ok=False)
    try:
        stages = copy_configuration_packages(root, incoming, rows)
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "case_name": case_name,
            "description": (
                "Closed/open RamAir spatial and temporal convergence workspace "
                "at M=0.15, Re=1.9e6 and c=1 m"
            ),
            "variant": LEVEL_VARIANTS["closed"]["medium"],
            "alpha_deg": 4.0,
            "reynolds": 1.9e6,
            "mach": 0.15,
            "computational_chord_m": 1.0,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mesh_convergence_study": {
                "study_id": STUDY_ID,
                "levels": ["coarse", "medium", "fine"],
                "topologies": ["closed", "open"],
                "active_configuration": "closed_medium",
                "configurations": {
                    str(row["id"]): row for row in rows
                },
                "all_meshes_real": True,
                "all_checkMesh_ok": True,
                "solver_executed": False,
            },
            "stages": stages,
        }
        seed_standard_solver_package(
            root,
            incoming,
            manifest,
            variant=LEVEL_VARIANTS["closed"]["medium"],
            alpha=4.0,
        )
        manifest = schema3_manifest(incoming, manifest)
        write_json_atomic(incoming / "case_manifest.json", manifest)
        study_dir = incoming / "Convergence Study"
        study_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(study_dir / "study_manifest.json", manifest[
            "mesh_convergence_study"
        ])
        with (study_dir / "mesh_quality_matrix.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            columns = [
                "id",
                "topology",
                "level",
                "variant",
                "cell_count",
                "boundary_layer_layers",
                "max_non_orthogonality_deg",
                "max_skewness",
                "min_cell_determinant",
                "min_face_interpolation_weight",
                "min_face_volume_ratio",
                "quality_status",
                "checkMesh_status",
                "accepted",
            ]
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key) for key in columns})
        (study_dir / "README.md").write_text(
            "# Closed/Open mesh-convergence workspace\n\n"
            "This workspace contains six real, converted and checkMesh-tested "
            "meshes: coarse, medium and fine for the closed and open c=1 m "
            "validation geometries.\n\n"
            "In the application, choose the same package name in Geometry, "
            "CFD Case and Mesh (for example `open_fine`) before loading those "
            "three stages. This keeps geometry, case package and polyMesh "
            "coherent. The default active package is `closed_medium`.\n\n"
            "No solver result is synthesized or included by this builder. "
            "Future spatial and temporal convergence runs belong in this same "
            "work case as separate Simulation and Postprocess packages.\n",
            encoding="utf-8",
        )
        incoming.replace(destination)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--case-name", default=STUDY_ID)
    parser.add_argument(
        "--build",
        nargs="*",
        choices=[
            "closed_coarse",
            "closed_fine",
            "open_coarse",
            "open_fine",
        ],
        default=[],
        help="Regenerate selected non-medium meshes before packaging.",
    )
    parser.add_argument("--gmsh-timeout-s", type=int, default=900)
    parser.add_argument(
        "--existing-action",
        choices=["archive", "keep", "replace-generated"],
        default="archive",
    )
    args = parser.parse_args()
    root = find_project_root(args.project_root)
    for item in args.build:
        topology, level = item.split("_", 1)
        run_mesh(root, topology, level, args.gmsh_timeout_s)
    rows = [
        quality_summary(root, topology, level)
        for topology in ("closed", "open")
        for level in ("coarse", "medium", "fine")
    ]
    verify_series(rows)
    destination = build_workspace(
        root,
        rows,
        args.case_name,
        args.existing_action,
    )
    print(
        json.dumps(
            {
                "status": "CREATED",
                "workspace": str(destination),
                "meshes": rows,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
