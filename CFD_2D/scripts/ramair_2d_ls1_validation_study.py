#!/usr/bin/env python3
"""Create/migrate the standalone LS(1)-0417 polar-validation workspace."""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from ramair_2d_validation import (
    IGNORED_POINT_COLUMNS,
    VALIDATION_POINT_COLUMNS,
    generate_validation_report,
    read_csv_or_empty,
)


STUDY_NAME = "ls1_0417_closed_polar_M0p15_Re1p9e6"
OLD_WORKCASE = "LS1_0417_validation_M0p15_Re1p9e6"
VARIANT = "reference_uncut_validation_1m"


def validation_solver_profile(base: dict | None = None) -> dict:
    """Return the enforced LS(1)-0417 validation solver contract."""
    profile = dict(base or {})
    # This laboratory validates one closed topology.  Removing topology
    # overlays makes the saved file the only source that can reach OpenFOAM.
    profile.pop("topology_profiles", None)
    rans_gate = dict(profile.get("validation_rans_convergence") or {})
    rans_gate.setdefault("window_samples", 1000)
    rans_gate.setdefault("minimum_samples", 500)
    rans_gate.setdefault("residual_tolerance", 1.0e-6)
    rans_gate.setdefault("mean_change_tolerance_percent", 0.5)
    rans_gate.setdefault("fluctuation_tolerance_percent", 1.0)
    rans_gate.setdefault("required_consecutive_windows", 3)
    write_strategy = dict(profile.get("validation_write_strategy") or {})
    write_strategy.setdefault("rans_full_field_interval_iterations", 1000)
    write_strategy.setdefault("urans_control", "adjustableRunTime")
    write_strategy.setdefault("urans_interval_time_star", 0.10)
    write_strategy.setdefault("purge_write", 24)
    write_strategy["authoritative"] = True
    for obsolete in (
        "field_write_interval_s",
        "field_write_interval_steps",
        "field_write_step_equivalent",
        "writeInterval_star",
    ):
        profile.pop(obsolete, None)
    profile.pop("n_non_orthogonal_correctors", None)
    steady_numerics = dict(profile.get("steady_numerics") or {})
    steady_numerics.pop("n_non_orthogonal_correctors", None)
    profile.update({
        "config_schema_version": max(16, int(profile.get("config_schema_version", 0))),
        "preset_id": "LS1_0417_validation_RANS_ABCDE_URANS_v1",
        "single_solver_contract": True,
        "solver": "foamRun",
        "solver_module": "incompressibleFluid",
        "velocity_source": "mach",
        "transient": True,
        "time_step_mode": "adaptive_physics_limited",
        "ddt_scheme": "backward",
        "deltaT_star": 0.0025,
        "maxDeltaT_star": 0.0025,
        "maxCo": 50.0,
        "n_outer_correctors": 5,
        "n_correctors": 2,
        "mesh_quality_numerics_mode": "automatic",
        "endTime_star": 64.0,
        "average_from_fraction": 14.0 / 64.0,
        "field_write_control": "adjustableRunTime",
        "field_write_interval_star": float(write_strategy["urans_interval_time_star"]),
        "purgeWrite": int(write_strategy["purge_write"]),
        "steady_initialization_enabled": True,
        "steady_max_iterations": 15000,
        "steady_write_interval_iterations": int(
            write_strategy["rans_full_field_interval_iterations"]
        ),
        "steady_native_residual_control_enabled": False,
        "steady_residual_control": {"p": 1.0e-6, "U": 1.0e-6, "nuTilda": 1.0e-6},
        "steady_numerics": {
            **steady_numerics,
            "p_relaxation": 0.3,
            "U_relaxation": 0.7,
            "nuTilda_relaxation": 0.7,
        },
        "outer_corrector_residual_control": {
            "enabled": True,
            "fields": {
                "p": {"tolerance": 1.0e-4, "relTol": 0.0},
                "U": {"tolerance": 1.0e-4, "relTol": 0.0},
                "nuTilda": {"tolerance": 1.0e-4, "relTol": 0.0},
            },
        },
        "transient_relaxation": {"p": 0.3, "U": 0.9, "nuTilda": 0.7},
        "validation_rans_convergence": rans_gate,
        "validation_write_strategy": write_strategy,
        "validation_polar_protocol": {
            "enforced": True,
            "steady_required": True,
            "steady_max_iterations": 15000,
            "target_deltaT_star": 0.0025,
            "maxCo_emergency_guard": 50.0,
            "maximum_outer_correctors": 5,
            "settling_phase_D_time_star": 10.0,
            "production_phase_E_time_star": 50.0,
            "production_start_time_star": 14.0,
            "total_time_star": 64.0,
        },
        "validation_phase_d_steady_equivalence": {
            "enabled": False,
            "window_time_star": 2.5,
            "minimum_samples": 200,
            "mean_difference_tolerance_percent": 0.30,
            "fluctuation_tolerance_percent": 0.50,
            "coefficient_floors": {
                "Cl": 0.05, "Cd": 0.005, "Cm": 0.005, "Cl_over_Cd": 1.0,
            },
            "policy": "skip_E_and_use_final_D_window_only_when_all_coefficients_pass",
        },
    })
    return profile


def validation_phase_plan() -> dict:
    """Canonical progressive A-E startup and production schedule in convective time."""
    stages = [
        {"stage": "A", "scheme": "Euler", "dt_factor": 0.25, "duration_time_star": 1.0, "sampling": False},
        {"stage": "B", "scheme": "Euler", "dt_factor": 0.50, "duration_time_star": 1.0, "sampling": False},
        {"stage": "C", "scheme": "Euler", "dt_factor": 1.00, "duration_time_star": 2.0, "sampling": False},
        {"stage": "D", "scheme": "backward", "dt_factor": 1.00, "duration_time_star": 10.0, "sampling": False},
        {"stage": "E", "scheme": "backward", "dt_factor": 1.00, "duration_time_star": 50.0, "sampling": True},
    ]
    return {
        "schema_version": 1,
        "target_deltaT_star": 0.0025,
        "adjust_time_step": True,
        "maxCo": 50.0,
        "max_outer_correctors": 5,
        "outer_residual_control_enabled": True,
        "stages": stages,
        "production_stage": "E",
        "production_start_time_star": 14.0,
        "total_time_star": 64.0,
        "average_from_fraction": 14.0 / 64.0,
        "phase_d_steady_equivalence": {
            "enabled": False,
            "window_time_star": 2.5,
            "minimum_samples": 200,
            "mean_difference_tolerance_percent": 0.30,
            "fluctuation_tolerance_percent": 0.50,
        },
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def initialize(project_root: Path, archive_old: bool = False, refresh_solver_profile: bool = False) -> dict:
    root = project_root.resolve()
    study = root / "CFD_2D/validation_studies" / STUDY_NAME
    output = study / "postprocess/validation"
    configs = study / "configurations"
    output.mkdir(parents=True, exist_ok=True)
    configs.mkdir(parents=True, exist_ok=True)
    old = root / "Results" / OLD_WORKCASE
    old_validation = old / "Validation"

    for filename in ("ramair_validation_points.csv", "ignored_nonmatching_results.csv"):
        source = old_validation / filename
        destination = output / filename
        if source.is_file() and (not destination.exists() or destination.stat().st_size == 0):
            shutil.copy2(source, destination)

    solver_candidates = sorted((old / "Solver Configurations").glob("*/Configurations/cfd2d_solver_config.json"))
    solver_source = solver_candidates[-1] if solver_candidates else root / "Application Support/Configurations/cfd2d_solver_config.json"
    solver_destination = configs / "cfd2d_solver_config.json"
    if not solver_destination.exists() or refresh_solver_profile:
        selected_source = solver_destination if solver_destination.is_file() else solver_source
        base_solver = json.loads(selected_source.read_text(encoding="utf-8")) if selected_source.is_file() else {}
        write_json_atomic(solver_destination, validation_solver_profile(base_solver))
    phase_plan_destination = configs / "validation_phase_plan.json"
    if not phase_plan_destination.exists() or refresh_solver_profile:
        write_json_atomic(phase_plan_destination, validation_phase_plan())
    for source, name in (
        (root / "Application Support/Configurations/cfd2d_mesh_config.json", "cfd2d_mesh_config.json"),
        (root / "Application Support/Configurations/cfd2d_workflow_config.json", "cfd2d_workflow_config.json"),
    ):
        if source.is_file() and not (configs / name).exists():
            shutil.copy2(source, configs / name)

    points = read_csv_or_empty(output / "ramair_validation_points.csv", VALIDATION_POINT_COLUMNS)
    ignored = read_csv_or_empty(output / "ignored_nonmatching_results.csv", IGNORED_POINT_COLUMNS)
    generate_validation_report(root, output, ramair_points=points, ignored_points=ignored)

    case_root = root / "CFD_2D/openfoam_cases" / VARIANT
    result_root = root / "CFD_2D/results" / VARIANT
    angles = sorted(path.name for path in case_root.glob("alpha_*") if path.is_dir())
    manifest = {
        "schema_version": 2,
        "study_id": STUDY_NAME,
        "title": "LS(1)-0417 closed polar validation",
        "independent_from_workcase": True,
        "variant": VARIANT,
        "validation": {"enabled": True, "variant": VARIANT},
        "conditions": {"reynolds": 1.9e6, "mach": 0.15, "chord_m": 1.0},
        "solver_protocol": validation_phase_plan(),
        "paths": {
            "geometry": f"CFD_2D/CFD_2D_inputs/geometry/{VARIANT}",
            "mesh": f"CFD_2D/meshes/{VARIANT}",
            "openfoam_cases": f"CFD_2D/openfoam_cases/{VARIANT}",
            "results": f"CFD_2D/results/{VARIANT}",
            "postprocess": f"CFD_2D/validation_studies/{STUDY_NAME}/postprocess/validation",
            "configurations": f"CFD_2D/validation_studies/{STUDY_NAME}/configurations",
        },
        "available_case_directories": angles,
        "storage_policy": "Reference canonical OpenFOAM fields; copy only configurations, tables and figures.",
        "migrated_from": OLD_WORKCASE if old.exists() else None,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(study / "study_manifest.json", manifest)

    archived = None
    if archive_old and old.is_dir():
        archive_root = root / "Previous Versions/Retired Work Cases"
        archive_root.mkdir(parents=True, exist_ok=True)
        archived = archive_root / f"{OLD_WORKCASE}_{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.move(str(old), str(archived))
    return {"status": "READY", "study_root": str(study), "old_workcase_archived": str(archived) if archived else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--archive-old-workcase", action="store_true")
    parser.add_argument("--refresh-solver-profile", action="store_true")
    args = parser.parse_args()
    print(json.dumps(initialize(
        args.project_root, args.archive_old_workcase, args.refresh_solver_profile,
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
