#!/usr/bin/env python3
"""Build the open ram-air comparison work case at LS(1)-0417 validation conditions."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from project_layout import find_project_root, project_path
from ramair_2d_scale_validation_geometry import build_scaled_variant
from ramair_2d_validation_workcase import copy_required, read_json, stage_entry
from ramair_case_library import (
    STAGE_COLLECTION_FOLDERS,
    schema3_manifest,
    seed_standard_solver_package,
    write_json_atomic,
)


DEFAULT_CASE_NAME = "Open_RamAir_comparison_M0p15_Re1p9e6"
VARIANT = "open_ramair_validation_1m"
BASE_VARIANT = "reference_uncut_validation_1m"
GEOMETRY_PACKAGE = "open_ramair_validation_1m_geometry"
CASE_PACKAGE = "M0p15_Re1p9e6_open_polar"
MESH_PACKAGE = "open_ramair_validation_1m_zero_thickness_mesh"
MESH_PRESET = Path(
    "CFD_2D/CFD_2D_inputs/config/mesh_presets/"
    "open_ramair_validation_1m_candidate.json"
)


def _prepare_destination(library: Path, case_name: str, existing_action: str) -> tuple[Path, Path]:
    destination = library / case_name
    if destination.exists():
        if existing_action == "keep":
            raise FileExistsError(destination)
        if existing_action == "archive":
            backup = library / ".case_backups" / f"{case_name}_{time.strftime('%Y%m%d_%H%M%S')}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(backup))
        elif existing_action == "replace-generated":
            previous = read_json(destination / "case_manifest.json")
            if (previous.get("comparison") or {}).get("comparison_id") != "open_vs_closed_LS1_conditions":
                raise RuntimeError(f"Refusing to replace an unrelated work case: {destination}")
            shutil.rmtree(destination)
        else:
            raise ValueError("existing_action must be archive, keep or replace-generated")
    incoming = library / f".{case_name}.incoming.{uuid.uuid4().hex}"
    incoming.mkdir(parents=True, exist_ok=False)
    return destination, incoming


def _ensure_scaled_geometry(root: Path) -> None:
    target = root / "CFD_2D/CFD_2D_inputs/case_package" / VARIANT
    if target.is_dir():
        return
    build_scaled_variant(
        root,
        source_variant="open_ramair",
        target_variant=VARIANT,
        target_chord_m=1.0,
    )


def _comparison_workflow(root: Path) -> dict[str, Any]:
    validation = read_json(
        root / "CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6/workflow_preset.json"
    )
    geometry = dict(validation.get("geometry") or {})
    geometry.update(
        variant=VARIANT,
        domain="circular_50c",
        run_preprocessor=False,
        rebuild_case_package=False,
    )
    validation["geometry"] = geometry
    execution = dict(validation.get("execution") or {})
    execution.update(
        steady_force_window_samples=500,
        execution_backend="native",
        n_cores=8,
    )
    validation["execution"] = execution
    validation.setdefault("mesh", {})["mesh_level"] = "custom"
    validation["mesh"]["domain"] = "circular_50c"
    return validation


def build_open_validation_workcase(
    root: Path,
    case_name: str = DEFAULT_CASE_NAME,
    existing_action: str = "archive",
) -> Path:
    root = find_project_root(root)
    _ensure_scaled_geometry(root)
    mesh_source = root / "CFD_2D/meshes" / VARIANT
    quality = read_json(mesh_source / "mesh_quality_report.json")
    if quality.get("checkMesh_status") != "OK":
        raise RuntimeError(
            f"The open comparison work case requires a real checkMesh OK mesh: {mesh_source}"
        )
    workflow = _comparison_workflow(root)
    conditions = dict(workflow.get("case_conditions") or {})
    alphas = [float(value) for value in conditions.get("alphas_deg", [])]
    if not alphas:
        raise ValueError("The comparison workflow contains no angle of attack.")

    library = project_path(root, "results_library")
    destination, incoming = _prepare_destination(library, case_name, existing_action)
    try:
        geometry_dir = incoming / STAGE_COLLECTION_FOLDERS["geometry"] / GEOMETRY_PACKAGE
        copy_required(
            root / "CFD_2D/CFD_2D_inputs/geometry" / VARIANT,
            geometry_dir / "CFD Geometry",
        )
        copy_required(
            project_path(root, "configurations", "ramair_catia_system_config.json"),
            geometry_dir / "Configurations/ramair_catia_system_config.json",
        )
        project_config = read_json(
            project_path(root, "configurations", "default_case_config.json")
        )
        profile_name = "LS1-0417_Cut_Standard_Re3000000.csv"
        project_config.setdefault("profile_inputs", {})["main_profile"] = (
            f"Airfoil Profiles/{profile_name}"
        )
        project_config["profile_inputs"]["reference_uncut_profile"] = (
            "Airfoil Profiles/NASA LS1-0417.dat"
        )
        write_json_atomic(
            geometry_dir / "Configurations/default_case_config.json",
            project_config,
        )
        copy_required(
            project_path(root, "profiles", profile_name),
            geometry_dir / "Airfoil Profile" / profile_name,
        )
        copy_required(
            project_path(root, "profiles", "NASA LS1-0417.dat"),
            geometry_dir / "Airfoil Profile/NASA LS1-0417.dat",
        )
        (geometry_dir / "README_OPEN_COMPARISON_GEOMETRY.txt").write_text(
            "The open profile and its uncut base curve are scaled to c=1 m for CFD only.\n"
            "The zero-thickness mesh uses the uncut-base LE arc as a nonphysical BL continuation;\n"
            "only the cut airfoil surfaces are wall patches.\n",
            encoding="utf-8",
        )

        case_dir = incoming / STAGE_COLLECTION_FOLDERS["case"] / CASE_PACKAGE
        copy_required(
            root / "CFD_2D/CFD_2D_inputs/case_package" / VARIANT,
            case_dir / "Case Package",
        )
        copy_required(
            root / "CFD_2D/CFD_2D_inputs/config",
            case_dir / "CFD Configurations",
        )
        write_json_atomic(
            case_dir / "CFD Configurations/cfd2d_workflow_config.json",
            workflow,
        )
        copy_required(
            root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json",
            case_dir / "CFD Configurations/cfd2d_solver_config.json",
        )
        temperature = float(conditions["temperature_K"])
        speed_of_sound = math.sqrt(1.4 * 287.05 * temperature)
        velocity = float(conditions["mach"]) * speed_of_sound
        rho = float(conditions["rho_kg_m3"])
        write_json_atomic(
            case_dir / "CFD Configurations/cfd2d_physical_defaults.json",
            {
                "reynolds": float(conditions["reynolds"]),
                "mach": float(conditions["mach"]),
                "rho_kg_m3": rho,
                "mu_pa_s": float(conditions["mu_pa_s"]),
                "pressure_ref_pa": float(conditions["pressure_ref_pa"]),
                "temperature_K": temperature,
                "speed_of_sound_m_s": speed_of_sound,
                "velocity_source": "mach",
                "velocity_m_s": velocity,
                "dynamic_pressure_pa": 0.5 * rho * velocity**2,
                "chord_m": 1.0,
                "condition_note": (
                    "Same Re, Mach, chord and sea-level-temperature convention as the "
                    "closed LS(1)-0417 comparison work case."
                ),
            },
        )

        mesh_dir = incoming / STAGE_COLLECTION_FOLDERS["mesh"] / MESH_PACKAGE
        copy_required(mesh_source, mesh_dir / "Mesh Data")
        copy_required(
            root / MESH_PRESET,
            mesh_dir / "Configurations/cfd2d_mesh_config.json",
        )
        write_json_atomic(
            mesh_dir / "Configurations/cfd2d_workflow_config.json",
            workflow,
        )
        (incoming / "Comparison").mkdir(parents=True, exist_ok=True)
        (incoming / "Comparison/HOW_TO_COMPARE_OPEN_AND_CLOSED.txt").write_text(
            "1. Load this complete work case in the application.\n"
            "2. Write the requested OpenFOAM angle case; start with alpha=4 deg.\n"
            "3. Run bounded SIMPLE initialization followed by URANS.\n"
            "4. Review convergence, y+, Cp and fields before accepting a point.\n"
            "5. Compare only matching Re=1.9e6, M=0.15, c=1 m and alpha values with\n"
            "   Results/LS1_0417_validation_M0p15_Re1p9e6.\n",
            encoding="utf-8",
        )

        manifest: dict[str, Any] = {
            "schema_version": 2,
            "case_name": case_name,
            "description": "Open ram-air versus closed LS(1)-0417 at matched validation conditions",
            "variant": VARIANT,
            "alpha_deg": 4.0,
            "reynolds": float(conditions["reynolds"]),
            "mach": float(conditions["mach"]),
            "computational_chord_m": 1.0,
            "rho_kg_m3": rho,
            "mu_pa_s": float(conditions["mu_pa_s"]),
            "main_profile": f"Airfoil Profiles/{profile_name}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "comparison": {
                "comparison_id": "open_vs_closed_LS1_conditions",
                "closed_work_case": "LS1_0417_validation_M0p15_Re1p9e6",
                "matched_quantities": ["reynolds", "mach", "chord_m", "alpha_deg"],
                "alphas_deg": alphas,
                "not_experimental_validation": True,
            },
            "stages": {
                "geometry": stage_entry(
                    GEOMETRY_PACKAGE, geometry_dir, VARIANT, 4.0
                ),
                "case": stage_entry(CASE_PACKAGE, case_dir, VARIANT, 4.0),
                "mesh": stage_entry(MESH_PACKAGE, mesh_dir, VARIANT, 4.0),
            },
        }
        seed_standard_solver_package(
            root,
            incoming,
            manifest,
            variant=VARIANT,
            alpha=4.0,
        )
        write_json_atomic(incoming / "case_manifest.json", schema3_manifest(incoming, manifest))
        incoming.replace(destination)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--case-name", default=DEFAULT_CASE_NAME)
    parser.add_argument(
        "--existing-action",
        choices=["archive", "keep", "replace-generated"],
        default="archive",
    )
    args = parser.parse_args()
    destination = build_open_validation_workcase(
        args.project_root,
        args.case_name,
        args.existing_action,
    )
    print(
        json.dumps(
            {
                "status": "CREATED",
                "case": args.case_name,
                "destination": str(destination),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
