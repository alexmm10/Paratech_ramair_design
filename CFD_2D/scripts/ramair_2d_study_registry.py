#!/usr/bin/env python3
"""Isolated state and Results registry for the validation convergence lab."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from ramair_2d_convergence_analysis import (
    deterministic_run_id,
    effective_h,
    refinement_ratio,
)
from ramair_2d_temporal_budget import (
    DEFAULT_OPERATING_CONDITION,
    operating_condition,
)
from ramair_2d_urans_contract import RUN_STATUSES as URANS_RUN_STATUSES


STUDY_ID = "closed_open_M0p15_Re1p9e6_alpha8"
RESULTS_CASE_NAME = "RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6"
STUDY_CONFIG_SCHEMA_VERSION = 10
ACTIVE_RELATIVE = Path("CFD_2D/validation_studies") / STUDY_ID
STATE_RELATIVE = Path("CFD_2D/app_state/validation_convergence_workspace.json")
RESULTS_STUDY_RELATIVE = Path("Convergence Studies") / STUDY_ID
MESH_IDS = (
    "closed_coarse",
    "closed_medium",
    "closed_fine",
    "open_coarse",
    "open_medium",
    "open_fine",
)
RUN_STATUSES = {
    "NOT_CONFIGURED",
    "READY",
    "RUNNING",
    "TIMEOUT_PARTIAL",
    "STOPPED_PARTIAL",
    "COMPLETED",
    "ANALYSIS_PENDING",
    "ACCEPTED",
    "ACCEPTED_WITH_WARNINGS",
    "REJECTED_TEMPORAL",
    "REJECTED_SPATIAL",
    "REJECTED_SOLVER",
    "REJECTED_MESH",
    "NOT_STATISTICALLY_ESTABLISHED",
    "BLOCKED_MISSING_RANS_CHECKPOINT",
    "BLOCKED_INCOMPATIBLE_RANS_CHECKPOINT",
    "MANUAL_REVIEW_CHECKPOINT_READY",
    "RANS_AUTO_CONVERGED_STRICT",
    "RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING",
    "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
    "RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY",
    "RANS_REVIEW_REQUIRED",
    "RANS_REJECTED",
} | set(URANS_RUN_STATUSES)

RANS_BASE_STATUSES = {
    "RANS_BASE_NOT_CREATED",
    "RANS_BASE_PREPARED",
    "RANS_BASE_RUNNING",
    "RANS_PARTIAL",
    "RANS_BASE_EXTENDING",
    "RANS_BASE_CONVERGED",
    "RANS_BASE_BOUNDED_NOT_CONVERGED",
    "RANS_BASE_DIVERGED",
    "RANS_BASE_FAILED",
    "RANS_DELETED_FROM_ACTIVE_WORKSPACE",
    "CHECKPOINT_READY",
    "DIAGNOSTIC_CHECKPOINT",
    "CHECKPOINT_STALE_MESH_CHANGED",
    "CHECKPOINT_STALE_PHYSICS_CHANGED",
    "BLOCKED_MISSING_RANS_CHECKPOINT",
    "BLOCKED_INCOMPATIBLE_RANS_CHECKPOINT",
}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(data: Any) -> str:
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def mesh_content_hash(poly_mesh: Path, quality: dict[str, Any]) -> str:
    """Hash the real OpenFOAM mesh, not only its folder name."""
    digest = hashlib.sha256()
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        path = poly_mesh / name
        if not path.is_file():
            raise FileNotFoundError(f"Required polyMesh file is missing: {path}")
        digest.update(name.encode("ascii"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
    digest.update(sha256_json(quality).encode("ascii"))
    return digest.hexdigest()


def _boundary_front_and_back_is_empty(boundary: Path) -> bool:
    text = boundary.read_text(encoding="utf-8", errors="replace")
    compact = " ".join(text.split())
    marker = compact.find("frontAndBack")
    if marker < 0:
        return False
    return "type empty;" in compact[marker : marker + 500]


def _mesh_grade(config: dict[str, Any]) -> str:
    determinant = float(config.get("min_cell_determinant", 0.0) or 0.0)
    interpolation = float(
        config.get("min_face_interpolation_weight", 0.0) or 0.0
    )
    volume_ratio = float(config.get("min_face_volume_ratio", 0.0) or 0.0)
    skew = float(config.get("max_skewness", 999.0) or 999.0)
    if determinant >= 0.01 and interpolation >= 0.10 and volume_ratio >= 0.10 and skew < 2:
        return "A"
    if determinant >= 0.001 and interpolation >= 0.05 and volume_ratio >= 0.05 and skew < 4:
        return "B"
    return "C"


def active_workspace_root(project_root: Path) -> Path:
    return Path(project_root) / ACTIVE_RELATIVE


def results_case_root(project_root: Path) -> Path:
    return Path(project_root) / "Results" / RESULTS_CASE_NAME


def results_study_root(project_root: Path) -> Path:
    return results_case_root(project_root) / RESULTS_STUDY_RELATIVE


def state_path(project_root: Path) -> Path:
    return Path(project_root) / STATE_RELATIVE


def _source_packages(case_root: Path, manifest: dict[str, Any], mesh_id: str) -> dict[str, Path]:
    stages = manifest.get("stages") or {}
    result: dict[str, Path] = {}
    for stage in ("geometry", "case", "mesh"):
        info = (((stages.get(stage) or {}).get("packages") or {}).get(mesh_id) or {})
        folder = str(info.get("folder") or "")
        if not folder:
            raise KeyError(f"Results manifest has no {stage} package for {mesh_id}")
        path = case_root / folder
        if not path.is_dir():
            raise FileNotFoundError(f"Results package is missing: {path}")
        result[stage] = path
    return result


def build_mesh_registry(project_root: Path, *, refresh_hashes: bool = False) -> dict[str, Any]:
    case_root = results_case_root(project_root)
    manifest = read_json(case_root / "case_manifest.json", {}) or {}
    configurations = (manifest.get("mesh_convergence_study") or {}).get(
        "configurations"
    ) or {}
    cached = read_json(active_workspace_root(project_root) / "mesh_registry.json", {}) or {}
    cached_by_id = {
        str(row.get("id")): row for row in cached.get("meshes", []) if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for mesh_id in MESH_IDS:
        source = configurations.get(mesh_id)
        if not isinstance(source, dict):
            raise KeyError(f"Missing real mesh configuration in Results: {mesh_id}")
        packages = _source_packages(case_root, manifest, mesh_id)
        mesh_data = packages["mesh"] / "Mesh Data"
        quality_path = mesh_data / "mesh_quality_report.json"
        quality = read_json(quality_path, {}) or {}
        poly_mesh = mesh_data / "constant" / "polyMesh"
        if not (poly_mesh / "boundary").is_file():
            raise FileNotFoundError(f"Real converted polyMesh is missing for {mesh_id}")
        if not _boundary_front_and_back_is_empty(poly_mesh / "boundary"):
            raise ValueError(f"{mesh_id}: frontAndBack is missing or is not empty")
        cell_count = int(
            quality.get("checkMesh_cell_count")
            or source.get("cell_count")
            or 0
        )
        if cell_count <= 0:
            raise ValueError(f"{mesh_id}: real cell count could not be established")
        old = cached_by_id.get(mesh_id, {})
        # Recompute on every registry build. A cached digest is evidence only;
        # it must never hide that a mesh package changed beneath a RANS state.
        mesh_hash = mesh_content_hash(poly_mesh, quality)
        row = {
            **source,
            "id": mesh_id,
            "cell_count": cell_count,
            "effective_h": effective_h(cell_count),
            "grade": _mesh_grade(source),
            "mesh_hash": mesh_hash,
            "mesh_hash_method": "sha256(polyMesh points/faces/owner/neighbour/boundary + quality JSON)",
            "geometry_package": str(packages["geometry"]),
            "case_package": str(packages["case"]),
            "mesh_package": str(packages["mesh"]),
            "poly_mesh": str(poly_mesh),
            "quality_report": str(quality_path),
            "frontAndBack_empty": True,
            "active": True,
        }
        rows.append(row)
    ratios: dict[str, dict[str, float]] = {}
    for topology in ("closed", "open"):
        values = {row["level"]: row for row in rows if row["topology"] == topology}
        ratios[topology] = {
            "coarse_to_medium": refinement_ratio(
                values["coarse"]["cell_count"], values["medium"]["cell_count"]
            ),
            "medium_to_fine": refinement_ratio(
                values["medium"]["cell_count"], values["fine"]["cell_count"]
            ),
        }
    return {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "generated_at": utc_stamp(),
        "source_results_case": str(case_root),
        "meshes": rows,
        "effective_refinement_ratios": ratios,
        "warnings": (
            ["OPEN_NON_ASYMPTOTIC_OR_WEAK_REFINEMENT_RATIO"]
            if min(ratios["open"].values()) < 1.1
            else []
        ),
    }


def default_study_config() -> dict[str, Any]:
    condition = operating_condition(DEFAULT_OPERATING_CONDITION)
    return {
        "schema_version": STUDY_CONFIG_SCHEMA_VERSION,
        "study_id": STUDY_ID,
        "title": "Validation & Convergence Lab",
        "purpose": "Joint closed/open spatial-temporal convergence at one common condition.",
        "operating_condition": condition,
        "study_angle_deg": 8.0,
        "angle_locked": True,
        "not_a_polar": True,
        "validation_study": {
            "enabled": True,
            "study_id": STUDY_ID,
            "alpha_deg": 8.0,
            "time_policy": "fixed_staged",
            "startup_scheme": "Euler",
            "production_scheme": "backward",
            "sensitivity_scheme": "CrankNicolson",
            "crank_nicolson_psi": 0.9,
            "dt_target_s": None,
            "startup_factors": [0.25, 0.5, 1.0],
            "startup_duration_tc": [1.0, 1.0, 2.0],
            "settling_tc": 20.0,
            "sampling_tc": 200.0,
            "nOuterCorrectors": 3,
            "nCorrectors": 2,
            "nNonOrthogonalCorrectors": 1,
            "courant_controls_dt": False,
            "field_write_interval_tc": 1.0,
            "retained_snapshots": 24,
            "mpi_ranks": 8,
            "timeout_hours": 24.0,
            "steady_checkpoint_timeout_min": 120.0,
            "rans_base_states": {
                "enabled": True,
                "initial_iterations": 10000,
                "minimum_simple_iterations_before_convergence_check": 10000,
                "extension_iterations": 2500,
                "automatic_queue_max_iterations": 20000,
                "maximum_iterations": 20000,
                "allow_early_stop": False,
                "native_residual_control_enabled": False,
                "continue_queue_after_nonconvergence": True,
                "continue_on_nonfatal_failure": True,
                "continue_after_case_failure": True,
                "stop_after_environment_failure": True,
                "simple_non_orthogonal_correctors": 0,
                "potentialFoam": True,
                "storage_profile": "steady_checkpoint_compact",
                "timeout_min": 120.0,
                "mpi_ranks": 8,
                "force_window_samples": 500,
                "force_mean_tolerance_percent": 1.0,
                "force_fluctuation_tolerance_percent": 2.0,
                "residual_tolerances": {
                    "p": 1.0e-5,
                    "U": 1.0e-5,
                    "nuTilda": 1.0e-5,
                },
                "linear_solvers": {
                    "p": "GAMG",
                    "U": "PBiCGStab",
                    "nuTilda": "PBiCGStab",
                },
                "initialization_schemes": {
                    "velocity_divergence": "bounded Gauss linearUpwind limited",
                    "turbulence_divergence": "bounded Gauss upwind",
                },
                "relaxation": {"p": 0.3, "U": 0.5, "nuTilda": 0.5},
                "open_bounded_policy": "diagnostic_only_after_confirmation",
            },
            "rans_convergence": {
                "extension_iterations": 2500,
                "pressure_residual_preferred_limit": None,
                "pressure_residual_plateau_multiplier": 10.0,
                "pressure_residual_absolute_ceiling": 0.01,
                "plateau_log_decade_improvement_min": 0.10,
                "plateau_relative_improvement_min": 0.20,
                "consecutive_plateau_blocks": 2,
                "allow_single_soft_failure": True,
            },
            "urans": {
                "startup_mode": "progressive",
                "time_step_policy": "fixed",
                "temporal_package": "reference",
                "production_scheme": "backward",
                "pimple_outer_correctors": 3,
                "pimple_correctors": 2,
                "pimple_non_orthogonal_correctors": 1,
                "pimple": {
                    "nOuterCorrectors": 3,
                    "nCorrectors": 2,
                    "nNonOrthogonalCorrectors": 1,
                },
                "startup_stages": [
                    {
                        "name": "A",
                        "enabled": True,
                        "scheme": "Euler",
                        "dt_factor": 0.25,
                        "duration_mode": "steps",
                        "duration": 25,
                        "steps": 25,
                        "purpose": "startup",
                    },
                    {
                        "name": "B",
                        "enabled": True,
                        "scheme": "Euler",
                        "dt_factor": 0.50,
                        "duration_mode": "steps",
                        "duration": 25,
                        "steps": 25,
                        "purpose": "history",
                    },
                    {
                        "name": "C",
                        "enabled": True,
                        "scheme": "Euler",
                        "dt_factor": 1.00,
                        "duration_mode": "steps",
                        "duration": 50,
                        "steps": 50,
                        "purpose": "transition",
                    },
                ],
                "settling_time_star": 20.0,
                "sampling_time_star": 200.0,
                "field_write_interval_s": None,
                "purge_write": None,
                "storage_profile": "transient_convergence_compact",
                "monitor_refresh_seconds": 30,
                "retained_snapshots": 24,
                "quick_check": {
                    "enabled": True,
                    "steps": 20,
                    "retention": "latest_report_and_log_only",
                    "gates_production": False,
                },
            },
        },
        "pimple_outer_study": {
            "topology": "closed",
            "mesh_level": "coarse",
            "outer_correctors": [2, 3, 4],
            "settling_tc": 5.0,
            "sampling_tc": 20.0,
            "enabled": True,
        },
        "temporal_packages": {
            "active": "reference",
            "reference": {
                "values_s": [2.5e-4, 1.25e-4, 6.25e-5],
                "description": "Current three-step validation reference ladder.",
            },
            "frequency": {
                "st_max": 20.0,
                "samples_per_cycle": 20,
                "description": "Three steps centred on the highest resolved Strouhal frequency.",
            },
            "manual": {
                "values_s": [2.5e-4, 1.25e-4, 6.25e-5],
                "description": "Exactly three positive, unique, descending values.",
            },
        },
        "storage": {
            "rans_profile": "steady_checkpoint_compact",
            "urans_profile": "transient_convergence_compact",
            "urans_preset": "Compact",
            "compact_retained_states": 20,
            "analysis_retained_states": 40,
        },
        "postprocess": {
            "static_scale_mode": "exact",
            "animation_scale_mode": "global_exact",
            "robust_percentiles": [1.0, 99.0],
            "manual_scales": {
                "Cp": [-3.0, 1.5],
                "U": [0.0, 1.5],
            },
        },
        "acceptance_thresholds": {
            "mean_CL_percent": 1.0,
            "mean_CD_percent": 2.0,
            "mean_CM_percent": 2.0,
            "rms_percent": 5.0,
            "dominant_frequency_percent": 2.0,
            "psd_peak_amplitude_percent": 10.0,
            "stationarity_mean_percent": 1.0,
            "stationarity_rms_percent": 5.0,
            "stationarity_frequency_percent": 3.0,
        },
        "frequency_analysis": {
            "method": "Welch",
            "window": "hann",
            "detrend": "constant",
            "overlap_fraction": 0.5,
            "minimum_cycles": 10,
            "preferred_cycles": 20,
        },
        "safety": {
            "dry_run_default": True,
            "sequential_default": True,
            "maximum_mpi_ranks": 8,
            "require_budget_confirmation": True,
            "restart_requires_exact_case_id": False,
        },
    }


def migrate_study_config(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate only the isolated laboratory configuration.

    The general schema-13 solver configuration remains untouched. Older lab
    packages used one ambiguous non-orthogonal setting; migration keeps SIMPLE
    at zero and uses at least one correction for URANS/PIMPLE.
    """
    defaults = default_study_config()
    source_schema = int((data or {}).get("schema_version", 0) or 0)
    migrated = dict(data or {})
    for key in (
        "study_id",
        "title",
        "purpose",
        "operating_condition",
        "study_angle_deg",
        "angle_locked",
        "not_a_polar",
    ):
        migrated.setdefault(key, defaults[key])

    validation = dict(migrated.get("validation_study") or {})
    default_validation = defaults["validation_study"]
    for key, value in default_validation.items():
        if key not in {"rans_base_states", "rans_convergence", "urans"}:
            validation.setdefault(key, value)

    rans = dict(validation.get("rans_base_states") or {})
    for key, value in default_validation["rans_base_states"].items():
        rans.setdefault(key, value)
    if "automatic_queue_max_iterations" not in rans:
        rans["automatic_queue_max_iterations"] = int(
            rans.get("maximum_iterations", 20000)
        )
    for nested in (
        "residual_tolerances",
        "linear_solvers",
        "initialization_schemes",
        "relaxation",
    ):
        values = dict(rans.get(nested) or {})
        for key, value in default_validation["rans_base_states"][nested].items():
            values.setdefault(key, value)
        rans[nested] = values
    validation["rans_base_states"] = rans

    convergence = dict(validation.get("rans_convergence") or {})
    for key, value in default_validation["rans_convergence"].items():
        convergence.setdefault(key, value)
    if source_schema < 4 and int(rans.get("extension_iterations", 5000)) == 5000:
        rans["extension_iterations"] = 2500
    if source_schema < 5:
        if int(rans.get("maximum_iterations", 30000)) == 30000:
            rans["maximum_iterations"] = 20000
        rans["continue_on_nonfatal_failure"] = True
        rans["continue_after_case_failure"] = True
        rans["stop_after_environment_failure"] = True
    convergence["extension_iterations"] = int(
        rans.get("extension_iterations", 2500)
    )
    validation["rans_base_states"] = rans
    validation["rans_convergence"] = convergence

    old_urans = dict(validation.get("urans") or {})
    urans = dict(old_urans)
    for key, value in default_validation["urans"].items():
        urans.setdefault(key, value)
    for stage_key in ("startup_stages",):
        defaults_by_name = {
            str(row["name"]): dict(row)
            for row in default_validation["urans"][stage_key]
        }
        normalized_stages: list[dict[str, Any]] = []
        for index, source_stage in enumerate(urans.get(stage_key) or []):
            stage = dict(source_stage)
            name = str(stage.get("name") or chr(ord("A") + index))
            normalized = dict(defaults_by_name.get(name) or {})
            normalized.update(stage)
            normalized["name"] = name
            normalized.setdefault("enabled", True)
            normalized.setdefault("duration_mode", "steps")
            normalized.setdefault(
                "duration",
                int(normalized.get("steps") or 1),
            )
            normalized.setdefault(
                "steps",
                int(normalized.get("duration") or 1),
            )
            normalized.setdefault("purpose", "startup")
            normalized_stages.append(normalized)
        urans[stage_key] = normalized_stages
    if "pimple_non_orthogonal_correctors" not in old_urans:
        urans["pimple_non_orthogonal_correctors"] = max(
            1, int(validation.get("nNonOrthogonalCorrectors", 0) or 0)
        )
    validation["urans"] = urans
    for obsolete in (
        "pilot_policy",
        "allow_production_without_pilot",
        "matrix_pilot_policy",
        "pilot_status",
        "pilot_stages",
        "pilot_approval",
        "attempts",
        "retention",
        "archive",
    ):
        urans.pop(obsolete, None)
    source_pimple = dict(old_urans.get("pimple") or {})
    pimple = dict(source_pimple)
    pimple.setdefault(
        "nOuterCorrectors",
        int(urans.get("pimple_outer_correctors", 3)),
    )
    pimple.setdefault(
        "nCorrectors",
        int(urans.get("pimple_correctors", 2)),
    )
    pimple.setdefault(
        "nNonOrthogonalCorrectors",
        int(urans.get("pimple_non_orthogonal_correctors", 1)),
    )
    urans["pimple"] = pimple
    urans["pimple_outer_correctors"] = int(pimple["nOuterCorrectors"])
    urans["pimple_correctors"] = int(pimple["nCorrectors"])
    urans["pimple_non_orthogonal_correctors"] = int(
        pimple["nNonOrthogonalCorrectors"]
    )
    urans.setdefault(
        "settling_time_star",
        float(validation.get("settling_tc", 20.0)),
    )
    urans.setdefault(
        "sampling_time_star",
        float(validation.get("sampling_tc", 200.0)),
    )
    validation["urans"] = urans
    rans_alias = dict(validation.get("rans") or {})
    for key in (
        "initial_iterations",
        "minimum_simple_iterations_before_convergence_check",
        "extension_iterations",
        "maximum_iterations",
        "simple_non_orthogonal_correctors",
    ):
        rans_alias.setdefault(key, rans.get(key))
    rans_alias.setdefault("per_mesh_timeout_s", None)
    rans_alias.setdefault("continue_after_timeout", True)
    rans_alias.setdefault(
        "continue_after_nonfatal_failure",
        bool(rans.get("continue_after_nonfatal_failure", True)),
    )
    validation["rans"] = rans_alias
    # Compatibility mirrors for the existing case-preparation path.
    validation["nOuterCorrectors"] = int(urans["pimple_outer_correctors"])
    validation["nCorrectors"] = int(urans["pimple_correctors"])
    validation["nNonOrthogonalCorrectors"] = int(
        urans["pimple_non_orthogonal_correctors"]
    )
    validation["retained_snapshots"] = int(urans["retained_snapshots"])
    migrated["validation_study"] = validation

    for section in ("pimple_outer_study", "storage", "postprocess", "temporal_packages"):
        values = dict(migrated.get(section) or {})
        for key, value in defaults[section].items():
            values.setdefault(key, value)
        migrated[section] = values
    if (
        source_schema < 3
        and migrated["pimple_outer_study"].get("topology") == "closed"
        and migrated["pimple_outer_study"].get("mesh_level") == "medium"
    ):
        migrated["pimple_outer_study"]["mesh_level"] = "coarse"
    migrated.pop("pilot", None)
    for section in ("acceptance_thresholds", "frequency_analysis", "safety"):
        values = dict(migrated.get(section) or {})
        for key, value in defaults[section].items():
            values.setdefault(key, value)
        migrated[section] = values
    migrated["schema_version"] = STUDY_CONFIG_SCHEMA_VERSION
    return migrated


def ensure_logical_workspace_layout(active: Path) -> dict[str, Any]:
    """Create the light logical layout without moving any heavy run data."""
    directories = (
        "registry",
        "configs",
        "configs/solver_profiles",
        "configs/resolved_batches",
        "configs/resolved_runs",
        "configs/migrations",
        "meshes",
        "rans",
        "urans",
        "pimple_outer_studies",
        "pimple_sensitivity",
        "postprocess/RANS",
        "postprocess/URANS",
        "convergence/rans_spatial/closed",
        "convergence/rans_spatial/open",
        "convergence/space_time/closed",
        "convergence/space_time/open",
        "convergence/urans_space_time/closed",
        "convergence/urans_space_time/open",
        "convergence/frequency",
        "convergence/courant",
        "reports",
        "logs",
        "locks",
        "cache",
        "exports",
        "quick_check",
        "runtime",
    )
    for relative in directories:
        (active / relative).mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "generated_at": utc_stamp(),
        "storage_policy": (
            "Logical indices only. Existing heavy meshes, checkpoints and runs "
            "remain at their canonical paths."
        ),
        "canonical_files": {
            "mesh_registry": "../mesh_registry.json",
            "study_config": "../study_config.json",
            "run_matrix": "../run_matrix.json",
            "execution_registry": "../execution_registry.json",
            "rans_queue": "../rans_queue_state.json",
        },
        "canonical_heavy_data": {
            "meshes": str(results_case_root(active.parents[2]) / "Meshes"),
            "rans_checkpoints": "../checkpoints",
            "urans_runs": "../runs",
            "pimple": "../pimple_outer_study",
        },
    }
    write_json_atomic(active / "registry/workspace_layout.json", index)
    reference_targets = {
        "mesh_registry.json": "../mesh_registry.json",
        "execution_registry.json": "../execution_registry.json",
        "rans_checkpoint_registry.json": "../checkpoints",
        "review_registry.json": "../checkpoints",
        "batch_registry.json": "../rans_queue_state.json",
        "postprocess_registry.json": "../postprocess",
        "space_time_registry.json": "../convergence/space_time",
    }
    for name, target in reference_targets.items():
        write_json_atomic(
            active / "registry" / name,
            {
                "schema_version": 1,
                "kind": "CANONICAL_REFERENCE",
                "target": target,
                "heavy_data_duplicated": False,
            },
        )
    write_json_atomic(
        active / "configs/active_study_config.json",
        {
            "schema_version": 1,
            "kind": "CANONICAL_REFERENCE",
            "target": "../study_config.json",
            "heavy_data_duplicated": False,
        },
    )
    project_root = active.parents[2]
    results_root = results_case_root(project_root)
    for mesh_id in MESH_IDS:
        mesh_reference = active / "meshes" / mesh_id
        rans_reference = active / "rans" / mesh_id
        urans_reference = active / "urans" / mesh_id
        for path in (mesh_reference, rans_reference, urans_reference):
            path.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            mesh_reference / "reference.json",
            {
                "schema_version": 1,
                "kind": "CANONICAL_REFERENCE",
                "target": str(results_root / "Meshes" / mesh_id),
                "heavy_data_duplicated": False,
            },
        )
        write_json_atomic(
            rans_reference / "reference.json",
            {
                "schema_version": 1,
                "kind": "CANONICAL_REFERENCE",
                "target": str(active / "checkpoints" / mesh_id),
                "heavy_data_duplicated": False,
            },
        )
        write_json_atomic(
            urans_reference / "reference.json",
            {
                "schema_version": 1,
                "kind": "CANONICAL_REFERENCE",
                "target": str(active / "runs"),
                "mesh_id": mesh_id,
                "heavy_data_duplicated": False,
            },
        )
    write_json_atomic(
        active / "pimple_outer_studies/reference.json",
        {
            "schema_version": 1,
            "kind": "CANONICAL_REFERENCE",
            "target": "../pimple_outer_study",
            "heavy_data_duplicated": False,
        },
    )
    return index


def default_run_matrix(registry: dict[str, Any]) -> dict[str, Any]:
    return build_run_matrix(
        registry,
        dt_values_s=[2.5e-4, 1.25e-4, 6.25e-5],
        preset="reference",
    )


def synchronize_run_matrix_solver_controls(
    matrix: dict[str, Any],
    study_config: dict[str, Any],
) -> dict[str, Any]:
    """Keep inherited run rows consistent with schema-v2 URANS controls."""
    synchronized = dict(matrix or {})
    validation = dict(study_config.get("validation_study") or {})
    urans = dict(validation.get("urans") or {})
    expected = {
        "nOuterCorrectors": int(urans.get("pimple_outer_correctors", 3)),
        "nCorrectors": int(urans.get("pimple_correctors", 2)),
        "nNonOrthogonalCorrectors": int(
            urans.get("pimple_non_orthogonal_correctors", 1)
        ),
    }
    changed = False
    rows: list[dict[str, Any]] = []
    for source in synchronized.get("runs", []):
        row = dict(source)
        for key, value in expected.items():
            if int(row.get(key, -1)) != value:
                row[key] = value
                changed = True
        rows.append(row)
    synchronized["runs"] = rows
    if changed:
        synchronized["updated_at"] = utc_stamp()
        synchronized["solver_controls_source"] = (
            "study_config.validation_study.urans"
        )
    return synchronized


def build_run_matrix(
    registry: dict[str, Any],
    *,
    dt_values_s: Iterable[float],
    preset: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    condition = operating_condition(DEFAULT_OPERATING_CONDITION)
    values = sorted({float(value) for value in dt_values_s}, reverse=True)
    if len(values) != 3 or any(
        not math.isfinite(value) or value <= 0.0 for value in values
    ):
        raise ValueError(
            "Run-matrix presets require exactly three distinct positive deltaT values"
        )
    previous_by_id = {
        str(row.get("run_id")): row
        for row in (previous or {}).get("runs", [])
        if isinstance(row, dict)
    }
    runs: list[dict[str, Any]] = []
    for mesh in registry["meshes"]:
        for dt_s in values:
            run_id = deterministic_run_id(
                mesh["topology"], mesh["level"], dt_s, 3, "backward", 8.0
            )
            row = {
                    "run_id": run_id,
                    "topology": mesh["topology"],
                    "mesh_level": mesh["level"],
                    "mesh_id": mesh["id"],
                    "mesh_package": mesh["mesh_package"],
                    "mesh_hash": mesh["mesh_hash"],
                    "cell_count": mesh["cell_count"],
                    "alpha_deg": 8.0,
                    "mach": 0.15,
                    "reynolds": 1.9e6,
                    "chord_m": 1.0,
                    "U_inf_m_s": condition["velocity_m_s"],
                    "tc_s": condition["tc_s"],
                    "time_scheme": "backward",
                    "dt_s": dt_s,
                    "dt_star": dt_s / condition["tc_s"],
                    "nOuterCorrectors": 3,
                    "nCorrectors": 2,
                    "nNonOrthogonalCorrectors": 1,
                    "physical_duration_s": 0.0,
                    "physical_duration_star": 0.0,
                    "sampling_duration_s": 0.0,
                    "steps_planned": 0,
                    "steps_completed": 0,
                    "status": "NOT_CONFIGURED",
                    "acceptance": "",
                }
            prior = previous_by_id.get(run_id)
            if prior:
                for key in (
                    "status", "acceptance", "physical_duration_s",
                    "physical_duration_star", "sampling_duration_s",
                    "steps_planned", "steps_completed", "case_path",
                    "exact_case_config_hash",
                ):
                    if key in prior:
                        row[key] = prior[key]
            runs.append(row)
    return {
        "schema_version": 2,
        "study_id": STUDY_ID,
        "preset": preset,
        "dt_values_s": values,
        "runs": runs,
        "updated_at": utc_stamp(),
    }


def set_run_matrix_preset(
    project_root: Path,
    preset: str,
    *,
    anchor_dt_s: float | None = None,
    custom_values_s: Iterable[float] | None = None,
) -> dict[str, Any]:
    definition: dict[str, Any]
    canonical_preset = {
        "reference": "reference",
        "frequency": "frequency",
        "manual": "manual",
    }.get(str(preset))
    if canonical_preset is None:
        raise ValueError(f"Unsupported run-matrix preset: {preset}")
    if canonical_preset == "reference":
        values = [2.5e-4, 1.25e-4, 6.25e-5]
        definition = {
            "source": "paper_reference",
            "formula": "deltaT = 2.5e-4 s and successive halvings",
        }
    elif canonical_preset == "frequency":
        condition = operating_condition(DEFAULT_OPERATING_CONDITION)
        anchor = condition["tc_s"] / (20.0 * 20.0)
        values = [2.0 * anchor, anchor, 0.5 * anchor]
        definition = {
            "source": "spectral_resolution",
            "formula": "deltaT* <= 1/(St_max*N_cycle)",
            "st_max": 20.0,
            "samples_per_cycle": 20,
            "anchor_dt_s": anchor,
        }
    elif canonical_preset == "manual":
        raw_values = [float(value) for value in custom_values_s or ()]
        values = raw_values
        if (
            len(values) != 3
            or any(not math.isfinite(value) or value <= 0.0 for value in values)
            or len({f"{value:.12g}" for value in values}) != 3
            or values != sorted(values, reverse=True)
        ):
            raise ValueError(
                "manual package requires exactly three distinct positive deltaT values in descending order"
            )
        definition = {
            "source": "user_validated",
            "formula": "strictly positive user-entered deltaT values",
        }
    active = active_workspace_root(project_root)
    registry = read_json(active / "mesh_registry.json", {}) or {}
    previous = read_json(active / "run_matrix.json", {}) or {}
    matrix = build_run_matrix(
        registry,
        dt_values_s=values,
        preset=canonical_preset,
        previous=previous,
    )
    matrix["preset_definition"] = definition
    write_json_atomic(active / "run_matrix.json", matrix)
    config_path = active / "study_config.json"
    config = migrate_study_config(read_json(config_path, {}) or {})
    packages = dict(config.get("temporal_packages") or {})
    packages["active"] = canonical_preset
    packages.setdefault(canonical_preset, {})["values_s"] = values
    config["temporal_packages"] = packages
    write_json_atomic(config_path, config)
    return matrix


def initialize_study(project_root: Path, *, refresh_hashes: bool = False) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    active = active_workspace_root(project_root)
    result = results_study_root(project_root)
    for path in (
        active / "logs",
        active / "checkpoints",
        active / "runs/closed/coarse",
        active / "runs/closed/medium",
        active / "runs/closed/fine",
        active / "runs/open/coarse",
        active / "runs/open/medium",
        active / "runs/open/fine",
        active / "postprocess/per_run",
        active / "postprocess/spatial_rans",
        active / "postprocess/spatial_temporal_urans",
        active / "postprocess/frequency",
        active / "postprocess/courant",
        active / "postprocess/pimple",
        active / "postprocess/reports",
        active / "exports",
    ):
        path.mkdir(parents=True, exist_ok=True)
    ensure_logical_workspace_layout(active)

    registry = build_mesh_registry(project_root, refresh_hashes=refresh_hashes)
    config_path = active / "study_config.json"
    matrix_path = active / "run_matrix.json"
    selection_path = active / "active_selection.json"
    if not config_path.is_file():
        write_json_atomic(config_path, default_study_config())
    else:
        current_config = read_json(config_path, {}) or {}
        migrated_config = migrate_study_config(current_config)
        if migrated_config != current_config:
            write_json_atomic(config_path, migrated_config)
    if not matrix_path.is_file():
        write_json_atomic(matrix_path, default_run_matrix(registry))
    else:
        current_matrix = read_json(matrix_path, {}) or {}
        current_config = read_json(config_path, {}) or {}
        synchronized_matrix = synchronize_run_matrix_solver_controls(
            current_matrix,
            current_config,
        )
        if synchronized_matrix != current_matrix:
            write_json_atomic(matrix_path, synchronized_matrix)
    if not selection_path.is_file():
        write_json_atomic(
            selection_path,
            {
                "mesh_id": "closed_medium",
                "run_id": None,
                "selected_at": utc_stamp(),
            },
        )
    write_json_atomic(active / "mesh_registry.json", registry)
    study_manifest = {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "title": "Validation & Convergence Lab",
        "created_at": read_json(active / "study_manifest.json", {}).get(
            "created_at", utc_stamp()
        ),
        "updated_at": utc_stamp(),
        "active_workspace": str(active),
        "results_workspace": str(result),
        "source_results_case": str(results_case_root(project_root)),
        "angle_deg": 8.0,
        "not_a_polar": True,
        "mesh_count": len(registry["meshes"]),
        "real_meshes_loaded": all(
            mesh.get("checkMesh_status") == "OK" and mesh.get("frontAndBack_empty")
            for mesh in registry["meshes"]
        ),
        "solver_campaign_executed": False,
    }
    write_json_atomic(active / "study_manifest.json", study_manifest)
    state = {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "active_workspace": str(active),
        "results_workspace": str(result),
        "source_results_case": str(results_case_root(project_root)),
        "selected_mesh_id": read_json(selection_path, {}).get("mesh_id"),
        "updated_at": utc_stamp(),
        "isolated_from_general_workspace": True,
    }
    write_json_atomic(state_path(project_root), state)
    return {
        "study_manifest": study_manifest,
        "mesh_registry": registry,
        "study_config": read_json(config_path, {}),
        "run_matrix": read_json(matrix_path, {}),
        "active_selection": read_json(selection_path, {}),
        "state": state,
    }


def load_study(project_root: Path) -> dict[str, Any]:
    active = active_workspace_root(project_root)
    if not (active / "study_manifest.json").is_file():
        return {}
    ensure_logical_workspace_layout(active)
    config_path = active / "study_config.json"
    current_config = read_json(config_path, {}) or {}
    migrated_config = migrate_study_config(current_config)
    if migrated_config != current_config:
        write_json_atomic(config_path, migrated_config)
    matrix_path = active / "run_matrix.json"
    current_matrix = read_json(matrix_path, {}) or {}
    synchronized_matrix = synchronize_run_matrix_solver_controls(
        current_matrix,
        migrated_config,
    )
    if synchronized_matrix != current_matrix:
        write_json_atomic(matrix_path, synchronized_matrix)
    return {
        "study_manifest": read_json(active / "study_manifest.json", {}),
        "mesh_registry": read_json(active / "mesh_registry.json", {}),
        "study_config": migrated_config,
        "run_matrix": synchronized_matrix,
        "active_selection": read_json(active / "active_selection.json", {}),
        "state": read_json(state_path(project_root), {}),
    }


def select_mesh(project_root: Path, mesh_id: str) -> dict[str, Any]:
    study = load_study(project_root)
    rows = {
        str(row["id"]): row for row in study.get("mesh_registry", {}).get("meshes", [])
    }
    if mesh_id not in rows:
        raise KeyError(f"Unknown study mesh: {mesh_id}")
    selection = {
        "mesh_id": mesh_id,
        "topology": rows[mesh_id]["topology"],
        "level": rows[mesh_id]["level"],
        "geometry_package": rows[mesh_id]["geometry_package"],
        "case_package": rows[mesh_id]["case_package"],
        "mesh_package": rows[mesh_id]["mesh_package"],
        "mesh_hash": rows[mesh_id]["mesh_hash"],
        "selected_at": utc_stamp(),
        "restoration_scope": "validation_lab_only",
    }
    write_json_atomic(
        active_workspace_root(project_root) / "active_selection.json", selection
    )
    state = study.get("state", {}) or {}
    state.update(selected_mesh_id=mesh_id, updated_at=utc_stamp())
    write_json_atomic(state_path(project_root), state)
    return selection


def update_run_status(
    project_root: Path,
    run_id: str,
    status: str,
    **updates: Any,
) -> dict[str, Any]:
    if status not in RUN_STATUSES:
        raise ValueError(f"Unsupported run status: {status}")
    active = active_workspace_root(project_root)
    matrix = read_json(active / "run_matrix.json", {}) or {}
    for row in matrix.get("runs", []):
        if row.get("run_id") == run_id:
            row.update(updates)
            row["status"] = status
            row["updated_at"] = utc_stamp()
            matrix["updated_at"] = utc_stamp()
            write_json_atomic(active / "run_matrix.json", matrix)
            return row
    raise KeyError(f"Unknown run ID: {run_id}")


def archive_existing(path: Path, archive_root: Path) -> Path | None:
    if not path.exists():
        return None
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / f"{path.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    suffix = 1
    while destination.exists():
        destination = archive_root / (
            f"{path.name}_{time.strftime('%Y%m%d_%H%M%S')}_{suffix:02d}"
        )
        suffix += 1
    path.replace(destination)
    return destination


def hardlink_tree(source: Path, destination: Path) -> None:
    """Create a local mutable case without duplicating immutable mesh bytes."""
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)

    def copy_function(src: str, dst: str) -> str:
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        return dst

    shutil.copytree(source, destination, copy_function=copy_function)
