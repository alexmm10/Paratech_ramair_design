#!/usr/bin/env python3
"""Create the real LS(1)-0417 validation work case under ``Results``.

The technical preset under ``CFD_2D/validation_cases`` is immutable reference
material.  This builder creates the application-facing workspace containing a
geometry package, operating-case package, the approved active mesh and a
validation folder that is updated angle by angle by the postprocessor.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from project_layout import find_project_root, project_path
from ramair_2d_validation import generate_validation_report
from ramair_2d_scale_validation_geometry import build_scaled_variant
from ramair_case_library import STAGE_COLLECTION_FOLDERS, tree_stats, variant_profile, write_json_atomic


DEFAULT_CASE_NAME = "LS1_0417_validation_M0p15_Re1p9e6"
VARIANT = "reference_uncut_validation_1m"
GEOMETRY_PACKAGE = "reference_uncut_validation_1m_geometry"
CASE_PACKAGE = "M0p15_Re1p9e6_polar"
MESH_PACKAGE = "reference_uncut_validation_1m_approved_mesh"


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return data


def copy_required(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def stage_entry(package: str, relative: Path, variant: str, alpha: float) -> dict[str, Any]:
    files, size = tree_stats(relative)
    collection = relative.parent.name
    return {
        "folder": collection,
        "active_package": package,
        "packages": {
            package: {
                "folder": (Path(collection) / relative.name).as_posix(),
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "file_count": files,
                "size_bytes": size,
                "variant": variant,
                "alpha_deg": float(alpha),
            }
        },
    }


def build_validation_workcase(root: Path, case_name: str, existing_action: str) -> Path:
    preset_dir = root / "CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6"
    workflow = read_json(preset_dir / "workflow_preset.json")
    solver_published = read_json(preset_dir / "solver_preset.json")
    solver_screening = read_json(preset_dir / "solver_preset_laptop_screening.json")
    solver_smoke = read_json(preset_dir / "solver_preset_laptop_smoke.json")
    reference_manifest = read_json(root / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016/reference_manifest.json")
    alphas = [float(value) for value in (workflow.get("case_conditions") or {}).get("alphas_deg", [])]
    if not alphas:
        raise ValueError("The validation workflow preset contains no angles of attack.")
    scaled_geometry = root / "CFD_2D/CFD_2D_inputs/geometry" / VARIANT
    scaled_package = root / "CFD_2D/CFD_2D_inputs/case_package" / VARIANT
    if not scaled_geometry.is_dir() or not scaled_package.is_dir():
        build_scaled_variant(root)

    library = project_path(root, "results_library")
    destination = library / case_name
    if destination.exists():
        if existing_action == "keep":
            raise FileExistsError(destination)
        if existing_action == "replace-generated":
            previous = read_json(destination / "case_manifest.json")
            dataset = (previous.get("validation") or {}).get("dataset_id")
            if dataset != "LS1_0417_Ghoreyshi_2016_Fig10":
                raise RuntimeError(f"Refusing to replace a non-validation work case: {destination}")
            shutil.rmtree(destination)
        elif existing_action == "archive":
            backup = library / ".case_backups" / f"{case_name}_{time.strftime('%Y%m%d_%H%M%S')}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(backup))
        else:
            raise ValueError("existing_action must be archive, keep or replace-generated")

    incoming = library / f".{case_name}.incoming.{uuid.uuid4().hex}"
    incoming.mkdir(parents=True, exist_ok=False)
    try:
        geometry_dir = incoming / STAGE_COLLECTION_FOLDERS["geometry"] / GEOMETRY_PACKAGE
        copy_required(root / "CFD_2D/CFD_2D_inputs/geometry" / VARIANT, geometry_dir / "CFD Geometry")
        copy_required(project_path(root, "configurations", "ramair_catia_system_config.json"), geometry_dir / "Configurations/ramair_catia_system_config.json")
        profile = variant_profile(root, VARIANT)
        if profile is None:
            raise FileNotFoundError("Could not resolve the reference_uncut airfoil from its geometry/case manifest.")
        project_config = read_json(project_path(root, "configurations", "default_case_config.json"))
        profile_inputs = dict(project_config.get("profile_inputs") or {})
        profile_inputs["main_profile"] = f"Airfoil Profiles/{profile.name}"
        project_config["profile_inputs"] = profile_inputs
        write_json_atomic(geometry_dir / "Configurations/default_case_config.json", project_config)
        copy_required(profile, geometry_dir / "Airfoil Profile" / profile.name)
        (geometry_dir / "README_VALIDATION_GEOMETRY.txt").write_text(
            "This CFD-only geometry is rebuilt from the preprocessor normalized LS(1)-0417 coordinates at c=1 m.\n"
            "It does not replace or rescale the CATIA canopy inputs. See CFD Geometry/geometry_scaling_report.json.\n",
            encoding="utf-8",
        )

        case_dir = incoming / STAGE_COLLECTION_FOLDERS["case"] / CASE_PACKAGE
        copy_required(root / "CFD_2D/CFD_2D_inputs/case_package" / VARIANT, case_dir / "Case Package")
        copy_required(root / "CFD_2D/CFD_2D_inputs/config", case_dir / "CFD Configurations")
        write_json_atomic(case_dir / "CFD Configurations/cfd2d_workflow_config.json", workflow)
        write_json_atomic(case_dir / "CFD Configurations/cfd2d_solver_config.json", solver_smoke)
        conditions = dict(workflow.get("case_conditions") or {})
        temperature_K = float(conditions["temperature_K"])
        speed_of_sound = math.sqrt(1.4 * 287.05 * temperature_K)
        velocity = float(conditions["mach"]) * speed_of_sound
        rho = float(conditions["rho_kg_m3"])
        write_json_atomic(case_dir / "CFD Configurations/cfd2d_physical_defaults.json", {
            "reynolds": float(conditions["reynolds"]),
            "mach": float(conditions["mach"]),
            "rho_kg_m3": rho,
            "mu_pa_s": float(conditions["mu_pa_s"]),
            "pressure_ref_pa": float(conditions["pressure_ref_pa"]),
            "temperature_K": temperature_K,
            "speed_of_sound_m_s": speed_of_sound,
            "velocity_source": "mach",
            "velocity_m_s": velocity,
            "dynamic_pressure_pa": 0.5 * rho * velocity**2,
            "chord_m": 1.0,
            "condition_note": conditions.get("condition_note", ""),
        })
        write_json_atomic(case_dir / "CFD Configurations/solver_preset_laptop_smoke_tstar0p2.json", solver_smoke)
        write_json_atomic(case_dir / "CFD Configurations/solver_preset_published_25000_steps.json", solver_published)
        write_json_atomic(case_dir / "CFD Configurations/solver_preset_laptop_screening_2500_steps.json", solver_screening)

        mesh_dir = incoming / STAGE_COLLECTION_FOLDERS["mesh"] / MESH_PACKAGE
        mesh_source = root / "CFD_2D/meshes" / VARIANT
        copy_required(mesh_source, mesh_dir / "Mesh Data")
        mesh_config = mesh_source / "mesh_config_used.json"
        if not mesh_config.is_file():
            mesh_config = root / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
        copy_required(mesh_config, mesh_dir / "Configurations/cfd2d_mesh_config.json")
        write_json_atomic(mesh_dir / "Configurations/cfd2d_workflow_config.json", workflow)

        validation_dir = incoming / "Validation"
        copy_required(root / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016", validation_dir / "Reference Data")
        generate_validation_report(
            root,
            validation_dir,
            ramair_points=pd.DataFrame(columns=[
                "alpha_deg", "Cl", "Cd", "Cm", "L_D", "reynolds", "mach",
                "velocity_source", "ddt_scheme", "result_dir", "status", "updated_at",
            ]),
            ignored_points=pd.DataFrame(),
        )
        (validation_dir / "HOW_TO_RUN_THE_POLAR.txt").write_text(
            "LS(1)-0417 VALIDATION WORKFLOW\n"
            "================================\n\n"
            "1. Select this work case in the left sidebar.\n"
            "2. Click 'Load geometry + CFD case + mesh'.\n"
            "3. In 'Caso OpenFOAM', write the requested alpha or all preset alphas.\n"
            "4. In 'Ejecucion', select one alpha or sweep, confirm and run.\n"
            "5. Postprocess each completed or statistically converged alpha. The\n"
            "   validation plots in this folder are updated only when Re=1.9e6 and\n"
            "   M=0.15 match tolerances. Interrupted non-converged runs are audited\n"
            "   but are not added as validation points.\n"
            "6. Save each accepted Simulation/Postprocess package in this same work case.\n\n"
            "The one-metre validation mesh is reused. The incompressible SA-RANS model is a low-Mach baseline,\n"
            "not an exact reproduction of the compressible Cobalt/Kestrel solvers.\n",
            encoding="utf-8",
        )

        manifest = {
            "schema_version": 2,
            "case_name": case_name,
            "description": "LS(1)-0417 polar validation against Ghoreyshi et al. Fig. 10",
            "variant": VARIANT,
            "alpha_deg": alphas[0],
            "reynolds": 1.9e6,
            "mach": 0.15,
            "computational_chord_m": 1.0,
            "rho_kg_m3": float((workflow.get("case_conditions") or {}).get("rho_kg_m3", 1.225)),
            "mu_pa_s": float((workflow.get("case_conditions") or {}).get("mu_pa_s")),
            "main_profile": f"Airfoil Profiles/{profile.name}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "validation": {
                "enabled": True,
                "dataset_id": "LS1_0417_Ghoreyshi_2016_Fig10",
                "variant": VARIANT,
                "required_reynolds": 1.9e6,
                "required_mach": 0.15,
                "alphas_deg": alphas,
                "reference_manifest": reference_manifest,
                "results_folder": "Validation",
            },
            "stages": {
                "geometry": stage_entry(GEOMETRY_PACKAGE, geometry_dir, VARIANT, alphas[0]),
                "case": stage_entry(CASE_PACKAGE, case_dir, VARIANT, alphas[0]),
                "mesh": stage_entry(MESH_PACKAGE, mesh_dir, VARIANT, alphas[0]),
            },
        }
        write_json_atomic(incoming / "case_manifest.json", manifest)
        incoming.replace(destination)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--case-name", default=DEFAULT_CASE_NAME)
    parser.add_argument("--existing-action", choices=["archive", "keep", "replace-generated"], default="archive")
    args = parser.parse_args()
    root = find_project_root(args.project_root)
    destination = build_validation_workcase(root, args.case_name, args.existing_action)
    print(json.dumps({"status": "CREATED", "case": args.case_name, "destination": str(destination)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
