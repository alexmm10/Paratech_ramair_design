#!/usr/bin/env python3
"""Manage the six mesh-specific RANS/SIMPLE bases for the validation lab."""
from __future__ import annotations

import hashlib
import gzip
import json
import math
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import (
    MESH_IDS,
    active_workspace_root,
    load_study,
    read_json,
    sha256_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_execution_registry import upsert_execution
from ramair_2d_rans_accounting import (
    MINIMUM_CONVERGENCE_ITERATION,
    authoritative_simple_iteration,
    block_accounting,
    classify_simple_exit,
    convergence_gate_is_allowed,
    gate_is_due,
    target_for_iteration,
    timing_summary,
)
from ramair_2d_run_lease import (
    DuplicateExecutionError,
    acquire_run_lease,
)
from ramair_2d_rans_review import (
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    RANS_REVIEW_REQUIRED,
    RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
    URANS_INITIALIZATION_ACCEPTED,
    generate_review_diagnostics,
    review_manifest,
)
from ramair_2d_checkpoint_integrity import (
    POLYMESH_FILES,
    checkpoint_mesh_identity,
)
from ramair_2d_mesh_numerics import quality_controls_for_mesh


READY_STATUSES = {
    "CHECKPOINT_READY",
    "DIAGNOSTIC_CHECKPOINT",
    "MANUAL_REVIEW_CHECKPOINT_READY",
}
REQUIRED_BASE_FIELDS = ("U", "p", "nuTilda")
OPTIONAL_BASE_FIELDS = ("phi", "nut", "alphat")
AUTOMATIC_READY_STATUSES = {
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
}
PARTIAL_STAGED_STATUSES = {
    "STEADY_AWAITING_USER_DECISION_STOPPED",
    "STEADY_STAGE_STOPPED_BY_USER",
}
# The canonical registry is the single source of truth for both the table and
# queue. Completed/reviewed bases remain visible and are skipped by state.
RANS_QUEUE_IDS = MESH_IDS
RANS_INITIAL_TARGET = 20000
RANS_EXTENSION_BLOCK = 20000
RANS_MAXIMUM_TARGET = 20000


def _primary_alpha(topology: str) -> float:
    return 16.0 if str(topology) == "closed" else 8.0


def _alpha_token(alpha_deg: float) -> str:
    value = float(alpha_deg)
    sign = "m" if value < 0.0 else "p"
    text = f"{abs(value):.6f}".rstrip("0").rstrip(".").replace(".", "p")
    return f"{sign}{text}"


def mesh_angle_id(study: dict[str, Any], mesh_id: str, alpha_deg: float | None) -> str:
    """Return a checkpoint identity while preserving legacy primary paths."""
    base_id = str(mesh_id).split("__alpha_", 1)[0]
    mesh = next(
        row for row in study["mesh_registry"].get("meshes", [])
        if str(row.get("id")) == base_id
    )
    alpha = _primary_alpha(str(mesh["topology"])) if alpha_deg is None else float(alpha_deg)
    if math.isclose(alpha, _primary_alpha(str(mesh["topology"])), abs_tol=1.0e-12):
        return base_id
    return f"{base_id}__alpha_{_alpha_token(alpha)}"


def _base_mesh_id(mesh_id: str) -> str:
    return str(mesh_id).split("__alpha_", 1)[0]


def _checkpoint_alpha(study: dict[str, Any], mesh_id: str) -> float:
    base = _mesh(study, mesh_id)
    marker = "__alpha_"
    if marker not in str(mesh_id):
        return _primary_alpha(str(base["topology"]))
    token = str(mesh_id).split(marker, 1)[1]
    sign = -1.0 if token.startswith("m") else 1.0
    numeric = token[1:].replace("p", ".")
    return sign * float(numeric)


class RansCheckpointBlocked(RuntimeError):
    """Structured missing/stale checkpoint error used by CLI and UI."""

    def __init__(self, mesh_id: str, status: str, message: str) -> None:
        super().__init__(message)
        self.payload = {
            "status": status,
            "mesh_id": mesh_id,
            "message": message,
            "remediation_actions": [
                "Generar esta base RANS",
                "Generar las seis bases RANS",
                "Ver requisitos",
            ],
        }


def _mesh(study: dict[str, Any], mesh_id: str) -> dict[str, Any]:
    base_id = _base_mesh_id(mesh_id)
    for row in study["mesh_registry"].get("meshes", []):
        if str(row.get("id")) == base_id:
            return row
    raise KeyError(f"Unknown validation-study mesh: {mesh_id}")


def _checkpoint_root(project_root: Path, mesh_id: str) -> Path:
    return active_workspace_root(project_root) / "checkpoints" / mesh_id


def migrate_legacy_checkpoint_angle_labels(project_root: Path, study: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve legacy a08 checkpoints after the closed primary angle changed to 16 degrees."""
    migrations: list[dict[str, Any]] = []
    for base_mesh_id in MESH_IDS:
        source = _checkpoint_root(project_root, base_mesh_id)
        manifest_path = source / "checkpoint_manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        encoded = str(manifest.get("source_case") or "")
        match = re.search(r"_a(\d{2})(?:_|$)", encoded)
        if not source.is_dir() or not match:
            continue
        actual_alpha = float(int(match.group(1)))
        expected_alpha = _checkpoint_alpha(study, base_mesh_id)
        if math.isclose(actual_alpha, expected_alpha, abs_tol=1.0e-12):
            continue
        destination_id = mesh_angle_id(study, base_mesh_id, actual_alpha)
        destination = _checkpoint_root(project_root, destination_id)
        row = {
            "source": str(source),
            "destination": str(destination),
            "base_mesh_id": base_mesh_id,
            "actual_alpha_deg": actual_alpha,
            "previous_interpretation_deg": expected_alpha,
        }
        if destination.exists():
            row["status"] = "DESTINATION_ALREADY_EXISTS"
            migrations.append(row)
            continue
        source.rename(destination)
        moved_manifest = read_json(destination / "checkpoint_manifest.json", {}) or {}
        moved_manifest.update({
            "mesh_id": destination_id,
            "base_mesh_id": base_mesh_id,
            "alpha_deg": actual_alpha,
            "angle_label_migration": row,
        })
        write_json_atomic(destination / "checkpoint_manifest.json", moved_manifest)
        row["status"] = "MIGRATED"
        migrations.append(row)
    if migrations:
        output = active_workspace_root(project_root) / "migrations" / "legacy_checkpoint_angle_labels.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output, {"migrations": migrations, "updated_at": utc_stamp()})
    return migrations


def _queue_stop_marker(project_root: Path) -> Path:
    return active_workspace_root(project_root) / ".rans_queue_stop_request.json"


def _stop_requested(project_root: Path) -> bool:
    return _queue_stop_marker(project_root).is_file()


def _queue_stop_action(project_root: Path) -> str | None:
    request = read_json(_queue_stop_marker(project_root), {}) or {}
    action = str(request.get("action") or "pause_queue")
    return action if action in {"pause_current_continue", "pause_queue"} else "pause_queue"


def _review_allows_use(project_root: Path, mesh_id: str) -> dict[str, bool]:
    review = review_manifest(project_root, mesh_id)
    allowed = dict(review.get("allowed_uses") or {})
    return {
        "rans_spatial_convergence": bool(
            allowed.get("rans_spatial_convergence")
        ),
        "urans_initialization": bool(allowed.get("urans_initialization")),
    }


def _is_review_pending_without_extension(
    project_root: Path, mesh_id: str
) -> bool:
    review = review_manifest(project_root, mesh_id)
    automatic = dict(review.get("automatic_gate") or {})
    return bool(
        automatic.get("status") == RANS_REVIEW_REQUIRED
        and automatic.get("extension_recommended") is False
    )


def delete_active_base(
    project_root: Path,
    mesh_id: str,
    *,
    confirm: bool,
    archive: bool = True,
) -> dict[str, Any]:
    """Explicitly remove one active RANS base without touching Results or meshes."""
    project_root = Path(project_root).resolve()
    if _base_mesh_id(mesh_id) not in MESH_IDS:
        raise ValueError(f"Unknown validation mesh: {mesh_id}")
    if not confirm:
        raise ValueError("Explicit confirmation is required to delete a RANS base")
    active = active_workspace_root(project_root).resolve()
    checkpoint = _checkpoint_root(project_root, mesh_id).resolve()
    checkpoint.relative_to(active)
    deleted_at = utc_stamp()
    archived_to: Path | None = None
    if checkpoint.exists():
        if archive:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            suffix = (
                "rans_orchestration_bug"
                if mesh_id == "closed_medium"
                else "active_rans_base"
            )
            archived_to = (
                project_root
                / "Previous Versions/ValidationLab"
                / f"{mesh_id}_{suffix}_{stamp}"
            )
            archived_to.parent.mkdir(parents=True, exist_ok=True)
            if archived_to.exists():
                raise FileExistsError(archived_to)
            shutil.move(str(checkpoint), str(archived_to))
        else:
            shutil.rmtree(checkpoint)
    queue = read_json(active / "rans_queue_state.json", {}) or {}
    if isinstance(queue.get("items"), dict):
        queue["items"].pop(mesh_id, None)
    queue.update(status="READY_TO_RESUME", updated_at=deleted_at)
    write_json_atomic(active / "rans_queue_state.json", queue)
    deletion = {
        "schema_version": 1,
        "mesh_id": mesh_id,
        "status": "RANS_DELETED_FROM_ACTIVE_WORKSPACE",
        "archived": bool(archived_to),
        "archive_path": str(archived_to) if archived_to else None,
        "results_untouched": True,
        "mesh_untouched": True,
        "deleted_at": deleted_at,
        "operation_log": (
            f"Deleting active {mesh_id} RANS execution only. "
            "Mesh and Results packages remain unchanged."
        ),
    }
    if archived_to:
        write_json_atomic(
            archived_to / "archive_manifest.json",
            {
                **deletion,
                "source_checkpoint": str(checkpoint),
                "archive_path": str(archived_to),
                "reason": (
                    "INVALIDATED_ORCHESTRATION_BUG"
                    if mesh_id == "closed_medium"
                    else "EXPLICIT_USER_RESTART"
                ),
                "timing_status": (
                    "INVALIDATED_ORCHESTRATION_BUG"
                    if mesh_id == "closed_medium"
                    else "ARCHIVED"
                ),
            },
        )
    write_json_atomic(
        active / "rans_deletions" / f"{mesh_id}_{time.strftime('%Y%m%d_%H%M%S')}.json",
        deletion,
    )
    upsert_execution(
        project_root,
        {
            "run_id": f"{mesh_id}_simple",
            "mode": "RANS",
            "mesh_id": mesh_id,
            "topology": mesh_id.split("_", 1)[0],
            "mesh_level": mesh_id.split("_", 1)[1],
            "stage": "SIMPLE",
            "status": "RANS_DELETED_FROM_ACTIVE_WORKSPACE",
            "updated_at": deleted_at,
        },
        activate=False,
    )
    return deletion


def _rans_config(study: dict[str, Any]) -> dict[str, Any]:
    return dict(
        study["study_config"]["validation_study"].get("rans_base_states") or {}
    )


def _effective_rans_config(study: dict[str, Any], topology: str) -> dict[str, Any]:
    """Resolve explicit topology overrides without hiding the common baseline."""
    common = _rans_config(study)
    overrides = dict(common.pop("topology_overrides", {}) or {})
    effective = dict(common)
    selected = dict(overrides.get(topology) or {})
    for key, value in selected.items():
        if isinstance(value, dict) and isinstance(effective.get(key), dict):
            effective[key] = {**dict(effective[key]), **value}
        else:
            effective[key] = value
    return effective


def _rans_config_for_mesh(
    project_root: Path,
    study: dict[str, Any],
    mesh_id: str,
) -> dict[str, Any]:
    """Resolve SIMPLE non-orthogonal controls from the selected mesh evidence."""
    mesh = _mesh(study, mesh_id)
    effective = _effective_rans_config(study, str(mesh["topology"]))
    package = Path(str(mesh.get("mesh_package") or ""))
    if not package.is_absolute():
        package = Path(project_root).resolve() / package
    controls = quality_controls_for_mesh(package)
    if not controls:
        raise RuntimeError(
            f"{mesh_id}: automatic SIMPLE numerics require a readable checkMesh report"
        )
    effective.update({
        "mesh_quality_numerics_mode": "automatic",
        "simple_non_orthogonal_correctors": int(controls["n_non_orthogonal_correctors"]),
        "laplacian_scheme": str(controls["laplacian_scheme"]),
        "maximum_non_orthogonality_deg": float(controls["maximum_non_orthogonality_deg"]),
        "mesh_quality_source": str(controls.get("source") or package),
    })
    return effective


def _resolved_batch_config(study: dict[str, Any]) -> dict[str, Any]:
    closed = _effective_rans_config(study, "closed")
    opened = _effective_rans_config(study, "open")
    for resolved in (closed, opened):
        resolved["initial_iterations"] = RANS_INITIAL_TARGET
        resolved["minimum_simple_iterations_before_convergence_check"] = (
            MINIMUM_CONVERGENCE_ITERATION
        )
        resolved["extension_iterations"] = RANS_EXTENSION_BLOCK
        resolved["maximum_iterations"] = RANS_MAXIMUM_TARGET
        # The Validation Lab uses the Python gate only at absolute targets.
        # Native SIMPLE residualControl caused clean exits at 6445-9908 and
        # therefore cannot own the campaign stopping decision.
        resolved["native_residual_control_enabled"] = False
    batch_id = f"rans_batch_{utc_stamp().replace(':', '').replace('-', '')}"
    payload = {
        "schema_version": 1,
        "batch_id": batch_id,
        "created_at": utc_stamp(),
        "ui_revision": int(study["study_config"].get("ui_revision", 0) or 0),
        "closed_effective": closed,
        "open_effective": opened,
        "runs": [
            {
                "mesh_id": mesh_id,
                "topology": mesh_id.split("_", 1)[0],
            }
            for mesh_id in RANS_QUEUE_IDS
        ],
    }
    payload["config_hash"] = sha256_json(payload)
    return payload


def freeze_batch_config(project_root: Path, study: dict[str, Any]) -> dict[str, Any]:
    """Freeze one immutable queue snapshot; a running queue always reuses it."""
    root = active_workspace_root(project_root)
    state = read_json(root / "rans_queue_state.json", {}) or {}
    path = root / "resolved_batch_config.json"
    existing = read_json(path, {}) or {}
    if state.get("status") == "RUNNING" and existing.get("config_hash"):
        return existing
    resolved = _resolved_batch_config(study)
    write_json_atomic(path, resolved)
    return resolved


def _extract_entry(text: str, name: str) -> str | None:
    match = re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(name)}\s+([^;{{}}]+);",
        text,
    )
    return match.group(1).strip() if match else None


def _dictionary_body(text: str, name: str) -> str | None:
    """Return one OpenFOAM dictionary body without relying on line layout."""
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*\{{", text)
    if not match:
        return None
    opening = text.find("{", match.start())
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index]
    return None


def _number(value: str | None) -> float | None:
    try:
        return float(str(value).strip()) if value is not None else None
    except ValueError:
        return None


def audit_simple_case_configuration(
    case: Path,
    expected: dict[str, Any],
    *,
    output_path: Path,
) -> dict[str, Any]:
    """Compare effective SIMPLE dictionaries against the frozen run snapshot."""
    template = case / "system/steadyInitialization"
    control = (template / "controlDict").read_text(encoding="utf-8")
    solution = (template / "fvSolution").read_text(encoding="utf-8")
    schemes = (template / "fvSchemes").read_text(encoding="utf-8")
    simple = _dictionary_body(solution, "SIMPLE")
    solvers = _dictionary_body(solution, "solvers")
    relaxation = _dictionary_body(solution, "relaxationFactors")
    if simple is None or solvers is None or relaxation is None:
        raise ValueError(f"SIMPLE dictionary missing from {template / 'fvSolution'}")
    residual_control_body = _dictionary_body(simple, "residualControl")
    residual_control = residual_control_body or ""
    fields_relaxation = _dictionary_body(relaxation, "fields") or ""
    equation_relaxation = _dictionary_body(relaxation, "equations") or ""
    field_solver_bodies = {
        field: _dictionary_body(solvers, field) or ""
        for field in ("p", "U", "nuTilda")
    }
    actual = {
        "initial_iterations": int(float(_extract_entry(control, "endTime") or -1)),
        "native_residual_control_enabled": residual_control_body is not None,
        "simple_non_orthogonal_correctors": int(
            float(_extract_entry(simple, "nNonOrthogonalCorrectors") or -1)
        ),
        "residual_p": _number(_extract_entry(residual_control, "p")),
        "residual_U": _number(_extract_entry(residual_control, "U")),
        "residual_nuTilda": _number(
            _extract_entry(residual_control, "nuTilda")
        ),
        "linear_solver_p": _extract_entry(field_solver_bodies["p"], "solver"),
        "linear_solver_U": _extract_entry(field_solver_bodies["U"], "solver"),
        "linear_solver_nuTilda": _extract_entry(
            field_solver_bodies["nuTilda"], "solver"
        ),
        "relaxation_p": _number(_extract_entry(fields_relaxation, "p")),
        "relaxation_U": _number(_extract_entry(equation_relaxation, "U")),
        "relaxation_nuTilda": _number(
            _extract_entry(equation_relaxation, "nuTilda")
        ),
        "velocity_divergence": _extract_entry(schemes, "div(phi,U)"),
        "turbulence_divergence": _extract_entry(schemes, "div(phi,nuTilda)"),
        "write_control": _extract_entry(control, "writeControl"),
        "write_interval": _number(_extract_entry(control, "writeInterval")),
        "purge_write": _number(_extract_entry(control, "purgeWrite")),
    }
    tolerances = dict(expected.get("residual_tolerances") or {})
    linear_solvers = dict(expected.get("linear_solvers") or {})
    relaxation_values = dict(expected.get("relaxation") or {})
    expected_values = {
        "initial_iterations": int(expected.get("initial_iterations", 20000)),
        "native_residual_control_enabled": bool(
            expected.get("native_residual_control_enabled", False)
        ),
        "simple_non_orthogonal_correctors": int(
            expected.get("simple_non_orthogonal_correctors", 0)
        ),
        "residual_p": (
            float(tolerances.get("p", 1.0e-5))
            if expected.get("native_residual_control_enabled", False)
            else None
        ),
        "residual_U": (
            float(tolerances.get("U", 1.0e-5))
            if expected.get("native_residual_control_enabled", False)
            else None
        ),
        "residual_nuTilda": (
            float(tolerances.get("nuTilda", 1.0e-5))
            if expected.get("native_residual_control_enabled", False)
            else None
        ),
        "linear_solver_p": str(linear_solvers.get("p", "GAMG")),
        "linear_solver_U": str(linear_solvers.get("U", "PBiCGStab")),
        "linear_solver_nuTilda": str(
            linear_solvers.get("nuTilda", "PBiCGStab")
        ),
        "relaxation_p": float(relaxation_values.get("p", 0.3)),
        "relaxation_U": float(relaxation_values.get("U", 0.7)),
        "relaxation_nuTilda": float(
            relaxation_values.get("nuTilda", 0.7)
        ),
        "velocity_divergence": (
            expected.get("initialization_schemes") or {}
        ).get("velocity_divergence"),
        "turbulence_divergence": (
            expected.get("initialization_schemes") or {}
        ).get("turbulence_divergence"),
        "write_control": "timeStep",
        "write_interval": float(
            expected.get("field_write_interval_iterations", 50)
        ),
        "purge_write": float(expected.get("purgeWrite", 2)),
    }
    rows: list[dict[str, Any]] = []
    for name, selected in expected_values.items():
        applied = actual.get(name)
        matches = (
            math.isclose(float(applied), float(selected), rel_tol=1.0e-10)
            if isinstance(applied, (int, float))
            and isinstance(selected, (int, float))
            else applied == selected
        )
        rows.append(
            {
                "parameter": name,
                "selected": selected,
                "applied": applied,
                "matches": matches,
            }
        )
    audit = {
        "schema_version": 1,
        "status": (
            "CONFIGURATION_APPLIED"
            if all(row["matches"] for row in rows)
            else "CONFIGURATION_MISMATCH"
        ),
        "case": str(case),
        "rows": rows,
        "checked_at": utc_stamp(),
    }
    write_json_atomic(output_path, audit)
    if audit["status"] != "CONFIGURATION_APPLIED":
        mismatches = [row for row in rows if not row["matches"]]
        raise RuntimeError(f"Resolved SIMPLE configuration mismatch: {mismatches}")
    return audit


def compatibility_contract(
    project_root: Path,
    study: dict[str, Any],
    mesh_id: str,
) -> dict[str, Any]:
    mesh = _mesh(study, mesh_id)
    condition = study["study_config"]["operating_condition"]
    rans = _rans_config_for_mesh(project_root, study, mesh_id)
    physics = {
        "topology": mesh["topology"],
        "variant": mesh["variant"],
        "mach": condition["mach"],
        "reynolds": condition["reynolds"],
        "chord_m": condition["chord_m"],
        "alpha_deg": _checkpoint_alpha(study, mesh_id),
        "rho_kg_m3": condition["rho_kg_m3"],
        "mu_pa_s": condition["mu_pa_s"],
        "turbulence_model": "SpalartAllmaras",
        "farfield_boundary_condition": "freestream",
        "frontAndBack": "empty",
    }
    solver = {
        "profile": "fixed_20000_manual_review_v3",
        "potentialFoam": bool(rans.get("potentialFoam", True)),
        "simple_non_orthogonal_correctors": int(
            rans.get("simple_non_orthogonal_correctors", 0)
        ),
        "laplacian_scheme": str(rans.get("laplacian_scheme", "Gauss linear corrected")),
        "maximum_non_orthogonality_deg": float(
            rans.get("maximum_non_orthogonality_deg", 0.0)
        ),
        "mesh_quality_source": str(rans.get("mesh_quality_source", "")),
        "initial_iterations": int(rans.get("initial_iterations", 20000)),
        "extension_iterations": int(rans.get("extension_iterations", 20000)),
        "maximum_iterations": int(rans.get("maximum_iterations", 20000)),
        "force_window_samples": int(rans.get("force_window_samples", 500)),
        "force_mean_tolerance_percent": float(
            rans.get("force_mean_tolerance_percent", 1.0)
        ),
        "force_fluctuation_tolerance_percent": float(
            rans.get("force_fluctuation_tolerance_percent", 2.0)
        ),
        "storage_profile": str(
            rans.get("storage_profile", "steady_checkpoint_compact")
        ),
    }
    return {
        "mesh_hash": str(mesh["mesh_hash"]),
        "physics": physics,
        "physics_hash": sha256_json(physics),
        "solver": solver,
        "solver_config_hash": sha256_json(solver),
    }


def _restart_freestream_alpha_deg(restart_zero: Path | None) -> float | None:
    """Read the physical incidence encoded in the restart U boundary field."""
    if restart_zero is None:
        return None
    field = restart_zero / "U"
    if not field.is_file():
        field = restart_zero / "U.gz"
    if not field.is_file():
        return None
    try:
        if field.suffix == ".gz":
            with gzip.open(field, "rt", encoding="utf-8", errors="replace") as stream:
                text = stream.read()
        else:
            text = field.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(
        r"freestreamValue\s+uniform\s*\(\s*"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+[-+0-9.eE]+\s*\)",
        text,
    )
    if not match:
        return None
    return math.degrees(math.atan2(float(match.group(2)), float(match.group(1))))


def canonical_rans_state(
    status: str,
    *,
    automatic_gate_status: str = "",
    review_status: str = "",
    execution_status: str = "",
) -> str:
    """Expose the schema-4 RANS state while retaining legacy status inputs."""
    execution = str(execution_status or "").upper()
    if execution == "DIVERGED" or status == "RANS_BASE_DIVERGED":
        return "RANS_DIVERGED"
    if execution in {"TIMEOUT_PARTIAL", "USER_STOPPED_PARTIAL", "PARTIAL"}:
        return "RANS_PARTIAL"
    if execution in {"SOLVER_FAILED", "FAILED"} or status == "RANS_BASE_FAILED":
        return "RANS_SOLVER_FAILED"
    if review_status in {
        "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
        "RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY",
        "RANS_REJECTED",
    }:
        return review_status
    if automatic_gate_status in {
        *AUTOMATIC_READY_STATUSES,
        RANS_REVIEW_REQUIRED,
    }:
        return automatic_gate_status
    if status in {"RANS_BASE_NOT_CREATED", "NOT_CONFIGURED"}:
        return "RANS_NOT_STARTED"
    if status in {"RANS_BASE_PREPARED", "PREPARED"}:
        return "RANS_PREPARED"
    if status in {"RANS_BASE_RUNNING", "RANS_BASE_EXTENDING", "RUNNING"}:
        return "RANS_RUNNING"
    if status in {"RANS_PARTIAL", "TIMEOUT_PARTIAL", "STOPPED_BY_USER"}:
        return "RANS_PARTIAL"
    if status in {"RANS_BASE_BOUNDED_NOT_CONVERGED", "RANS_BASE_MAX_ITERATIONS"}:
        return "RANS_REVIEW_REQUIRED"
    if status == "RANS_DELETED_FROM_ACTIVE_WORKSPACE":
        return status
    if status in READY_STATUSES:
        return "RANS_REVIEW_REQUIRED"
    return status


def checkpoint_status(project_root: Path, mesh_id: str) -> dict[str, Any]:
    study = load_study(project_root)
    contract = compatibility_contract(project_root, study, mesh_id)
    path = _checkpoint_root(project_root, mesh_id) / "checkpoint_manifest.json"
    manifest = read_json(path, {}) or {}
    if not manifest:
        return {
            "status": "RANS_BASE_NOT_CREATED",
            "rans_state": "RANS_NOT_STARTED",
            "mesh_id": mesh_id,
            **contract,
        }
    status = str(manifest.get("status") or "RANS_BASE_NOT_CREATED")
    review = review_manifest(project_root, mesh_id)
    review_status = str(review.get("review", {}).get("status") or "")
    allowed_uses = dict(review.get("allowed_uses") or {})
    reviewed_checkpoint = dict(review.get("checkpoint") or {})
    manual_review_ready = (
        reviewed_checkpoint.get("status") == "READY"
        and bool(allowed_uses.get("urans_initialization"))
    )
    if manual_review_ready:
        status = "MANUAL_REVIEW_CHECKPOINT_READY"
    compatibility_warnings: list[str] = []
    restart_zero = (
        reviewed_checkpoint.get("restart_zero")
        if manual_review_ready
        else manifest.get("restart_zero")
        if manifest.get("restart_zero")
        else str(Path(manifest.get("case", "")) / "0")
        if manifest.get("case")
        else None
    )
    checkpoint_case = _checkpoint_root(project_root, mesh_id) / "case"
    actual_restart_alpha = _restart_freestream_alpha_deg(
        Path(str(restart_zero)) if restart_zero else None
    )
    expected_restart_alpha = float(contract["physics"]["alpha_deg"])
    if (
        actual_restart_alpha is not None
        and not math.isclose(
            actual_restart_alpha,
            expected_restart_alpha,
            abs_tol=5.0e-2,
        )
    ):
        status = "CHECKPOINT_STALE_PHYSICS_CHANGED"
        compatibility_warnings.append(
            "RESTART_FREESTREAM_ANGLE_MISMATCH: "
            f"field={actual_restart_alpha:.6g} deg, "
            f"expected={expected_restart_alpha:.6g} deg"
        )
    checkpoint_poly = checkpoint_case / "constant/polyMesh"
    missing_poly = [name for name in POLYMESH_FILES if not (checkpoint_poly / name).is_file()]
    if missing_poly:
        source_case = Path(str(manifest.get("source_case") or ""))
        source_poly = source_case / "constant/polyMesh"
        source_identity = (
            checkpoint_mesh_identity(source_case, Path(str(restart_zero)))
            if restart_zero and source_case.is_dir()
            else {}
        )
        if source_identity.get("status") == "READY":
            checkpoint_poly.mkdir(parents=True, exist_ok=True)
            for name in POLYMESH_FILES:
                shutil.copy2(source_poly / name, checkpoint_poly / name)
            compatibility_warnings.append(
                "CHECKPOINT_POLYMESH_RESTORED_FROM_FIELD_COUNT_AND_PATCH_VERIFIED_SOURCE_CASE"
            )
    checkpoint_identity: dict[str, Any] = {}
    if restart_zero and Path(str(restart_zero)).is_dir() and checkpoint_case.is_dir():
        checkpoint_identity = checkpoint_mesh_identity(
            checkpoint_case,
            Path(str(restart_zero)),
        )
    if manifest.get("mesh_hash") != contract["mesh_hash"]:
        if checkpoint_identity.get("status") == "READY":
            compatibility_warnings.append(
                "REGISTRY_MESH_DIFFERS_FROM_CHECKPOINT; RANS_CHECKPOINT_MESH_IS_CANONICAL_FOR_URANS"
            )
        else:
            status = "CHECKPOINT_STALE_MESH_CHANGED"
    if manifest.get("physics_hash") != contract["physics_hash"]:
        status = "CHECKPOINT_STALE_PHYSICS_CHANGED"
    elif (
        manifest.get("solver_config_hash")
        and manifest.get("solver_config_hash") != contract["solver_config_hash"]
        and status in READY_STATUSES
    ):
        if manual_review_ready:
            compatibility_warnings.append(
                "HISTORICAL_RANS_SOLVER_CONFIG_DIFFERS_FROM_CURRENT_DEFAULT"
            )
        else:
            status = "CHECKPOINT_STALE_PHYSICS_CHANGED"
    initialization_risk = bool(
        manifest.get("bounded") is False
        or str(manifest.get("execution_status") or "").upper() == "DIVERGED"
        or str(manifest.get("status") or "").upper() == "RANS_BASE_DIVERGED"
    )
    if initialization_risk and manual_review_ready:
        compatibility_warnings.append(
            "RANS_CHECKPOINT_USER_ACCEPTED_FOR_INITIALIZATION_ONLY; BOUNDEDNESS_NOT_ESTABLISHED"
        )
        allowed_uses = {
            **allowed_uses,
            "rans_spatial": False,
            "urans_initialization": True,
        }
        review_status = RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
    field_hashes = (
        dict(reviewed_checkpoint.get("field_hashes") or {})
        if status == "MANUAL_REVIEW_CHECKPOINT_READY"
        else dict(manifest.get("field_hashes") or {})
    )
    automatic_gate_status = str(
        review.get("automatic_gate", {}).get("status") or ""
    )
    observed_iterations = (
        _pending_latest_iteration(checkpoint_case)
        if checkpoint_case.is_dir()
        else 0
    )
    reported_iterations = max(
        int(manifest.get("iterations_completed") or 0),
        int(manifest.get("absolute_simple_iteration") or 0),
        int(observed_iterations),
    )
    return {
        **manifest,
        "iterations_completed": reported_iterations,
        "absolute_simple_iteration": reported_iterations,
        "status": status,
        "rans_state": canonical_rans_state(
            status,
            automatic_gate_status=automatic_gate_status,
            review_status=review_status,
            execution_status=str(manifest.get("execution_status") or ""),
        ),
        "compatible": status in READY_STATUSES,
        "review_status": review_status,
        "automatic_gate_status": automatic_gate_status,
        "allowed_uses": allowed_uses,
        "checkpoint_case": str(checkpoint_case),
        "restart_zero": restart_zero,
        "field_hashes": field_hashes,
        "mesh_source": "RANS_CHECKPOINT",
        "checkpoint_mesh_identity": checkpoint_identity,
        "checkpoint_mesh_hash": checkpoint_identity.get("poly_mesh_hash"),
        "registry_mesh_hash": contract["mesh_hash"],
        "mesh_hash": checkpoint_identity.get("poly_mesh_hash") or manifest.get("mesh_hash"),
        "cell_count": checkpoint_identity.get("cell_count") or manifest.get("cell_count"),
        "initialization_risk": initialization_risk,
        "expected_restart_alpha_deg": expected_restart_alpha,
        "actual_restart_alpha_deg": actual_restart_alpha,
        "compatibility_warnings": compatibility_warnings,
    }


def require_compatible_checkpoint(
    project_root: Path,
    mesh_id: str,
    *,
    allow_diagnostic: bool = False,
) -> dict[str, Any]:
    state = checkpoint_status(project_root, mesh_id)
    accepted = {"CHECKPOINT_READY", "MANUAL_REVIEW_CHECKPOINT_READY"}
    if allow_diagnostic:
        accepted.add("DIAGNOSTIC_CHECKPOINT")
    if state["status"] not in accepted:
        status = str(state["status"])
        if status not in {
            "CHECKPOINT_STALE_MESH_CHANGED",
            "CHECKPOINT_STALE_PHYSICS_CHANGED",
        }:
            status = "BLOCKED_MISSING_RANS_CHECKPOINT"
        raise RansCheckpointBlocked(
            mesh_id,
            status,
            (
                f"No existe un estado base RANS compatible para {mesh_id}. "
                "Generelo antes de iniciar la prueba corta o URANS."
            ),
        )
    return state


def _latest_restart_state(case: Path) -> tuple[int, Path] | None:
    candidates = _numeric_iterations(case)
    history = case / "steadyInitialization/history"
    if history.is_dir():
        for path in history.glob("run_*/time_directories/*"):
            if not path.is_dir():
                continue
            try:
                iteration = int(round(float(path.name)))
            except ValueError:
                continue
            candidates.append((iteration, path))
    return max(candidates, key=lambda item: item[0]) if candidates else None


def create_reviewed_checkpoint(
    project_root: Path,
    mesh_id: str,
    *,
    force_new: bool = False,
) -> dict[str, Any]:
    """Create a versioned restart snapshot only after an explicit accepted review."""
    project_root = Path(project_root).resolve()
    checkpoint_root = _checkpoint_root(project_root, mesh_id)
    review = review_manifest(project_root, mesh_id)
    review_status = str(review.get("review", {}).get("status") or "")
    if not bool(review.get("allowed_uses", {}).get("urans_initialization")):
        raise RuntimeError(
            f"{mesh_id}: RANS review status {review_status!r} is not accepted for URANS"
        )
    existing_restart = str(review.get("checkpoint", {}).get("restart_zero") or "")
    if (
        not force_new
        and
        review.get("checkpoint", {}).get("status") == "READY"
        and existing_restart
        and Path(existing_restart).is_dir()
    ):
        return review
    case = checkpoint_root / "case"
    source = _latest_restart_state(case)
    if source is None:
        raise RuntimeError(f"{mesh_id}: no saved real RANS state is available")
    iteration, source_state = source
    identifier = f"{mesh_id}_reviewed_{iteration}_{utc_stamp().replace(':', '').replace('-', '')}"
    parent = checkpoint_root / "review_checkpoints"
    staging = parent / f".{identifier}.staging"
    destination = parent / identifier
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source_state, staging / "0")
    field_hashes: dict[str, str] = {}
    source_field_hashes: dict[str, str] = {}
    missing: list[str] = []
    for name in (*REQUIRED_BASE_FIELDS, *OPTIONAL_BASE_FIELDS):
        path = _field_path(staging / "0", name)
        if path is None:
            if name in REQUIRED_BASE_FIELDS:
                missing.append(name)
            continue
        field_hashes[name] = _normalized_field_hash(path)
        source_path = _field_path(source_state, name)
        if source_path is not None:
            source_field_hashes[name] = _normalized_field_hash(source_path)
    if missing:
        shutil.rmtree(staging)
        raise RuntimeError(
            f"{mesh_id}: reviewed checkpoint lacks required fields {missing}"
        )
    if source_field_hashes != field_hashes:
        shutil.rmtree(staging)
        raise RuntimeError(
            f"{mesh_id}: reviewed checkpoint field hashes differ from the "
            "source SIMPLE state"
        )
    write_json_atomic(
        staging / "checkpoint_provenance.json",
        {
            "checkpoint_id": identifier,
            "mesh_id": mesh_id,
            "source_state": str(source_state),
            "source_iteration": iteration,
            "automatic_gate": review.get("automatic_gate"),
            "review": review.get("review"),
            "mesh_hash": review.get("mesh_hash"),
            "physics_hash": review.get("physics_hash"),
            "field_hashes": field_hashes,
            "source_field_hashes": source_field_hashes,
            "hash_verification": "MATCH",
            "created_at": utc_stamp(),
        },
    )
    staging.replace(destination)
    review["checkpoint"] = {
        "status": "READY",
        "checkpoint_id": identifier,
        "restart_zero": str(destination / "0"),
        "source_iteration": iteration,
        "field_hashes": field_hashes,
        "created_at": utc_stamp(),
    }
    review["updated_at"] = utc_stamp()
    write_json_atomic(checkpoint_root / "rans_review_manifest.json", review)
    return review


def _replace_foam_entry(text: str, name: str, value: str) -> str:
    updated, count = re.subn(
        rf"(?m)^(\s*{re.escape(name)}\s+)[^;]+;",
        rf"\g<1>{value};",
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"OpenFOAM entry {name!r} is missing")
    return updated


def _dictionary_bounds(
    text: str,
    name: str,
    *,
    start: int = 0,
    end: int | None = None,
) -> tuple[int, int, int] | None:
    """Return ``(declaration_start, body_start, closing_brace)``."""
    limit = len(text) if end is None else int(end)
    match = re.search(
        rf"(?m)^\s*{re.escape(name)}\s*\{{",
        text[start:limit],
    )
    if not match:
        return None
    declaration = start + match.start()
    opening = text.find("{", declaration, limit)
    depth = 0
    for index in range(opening, limit):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return declaration, opening + 1, index
    return None


def _remove_nested_dictionary(text: str, parent: str, child: str) -> str:
    parent_bounds = _dictionary_bounds(text, parent)
    if parent_bounds is None:
        raise ValueError(f"OpenFOAM dictionary {parent!r} is missing")
    _, parent_body, parent_close = parent_bounds
    child_bounds = _dictionary_bounds(
        text,
        child,
        start=parent_body,
        end=parent_close,
    )
    if child_bounds is None:
        return text
    child_start, _, child_close = child_bounds
    remove_end = child_close + 1
    while remove_end < len(text) and text[remove_end] in " \t":
        remove_end += 1
    if remove_end < len(text) and text[remove_end] == "\n":
        remove_end += 1
    return text[:child_start] + text[remove_end:]


def _replace_entry_in_dictionary(
    text: str,
    dictionary: str,
    name: str,
    value: str,
) -> str:
    bounds = _dictionary_bounds(text, dictionary)
    if bounds is None:
        raise ValueError(f"OpenFOAM dictionary {dictionary!r} is missing")
    _, body_start, body_close = bounds
    body = text[body_start:body_close]
    body = _replace_foam_entry(body, name, value)
    return text[:body_start] + body + text[body_close:]


def _configure_simple_template(case: Path, rans: dict[str, Any]) -> None:
    template = case / "system/steadyInitialization"
    control_path = template / "controlDict"
    control = control_path.read_text(encoding="utf-8")
    control = _replace_foam_entry(
        control, "endTime", str(int(rans.get("initial_iterations", 20000)))
    )
    control = _replace_foam_entry(control, "purgeWrite", "2")
    control_path.write_text(control, encoding="utf-8")

    solution_path = template / "fvSolution"
    solution = solution_path.read_text(encoding="utf-8")
    solution = _replace_entry_in_dictionary(
        solution,
        "SIMPLE",
        "nNonOrthogonalCorrectors",
        str(int(rans.get("simple_non_orthogonal_correctors", 0))),
    )
    if not bool(rans.get("native_residual_control_enabled", False)):
        solution = _remove_nested_dictionary(
            solution,
            "SIMPLE",
            "residualControl",
        )
    residuals = dict(rans.get("residual_tolerances") or {})
    if bool(rans.get("native_residual_control_enabled", False)):
        for field in ("p", "U", "nuTilda"):
            if field in residuals:
                solution = re.sub(
                    rf"(?m)^(\s*{re.escape(field)}\s+)[0-9.eE+-]+;",
                    rf"\g<1>{float(residuals[field]):.8g};",
                    solution,
                    count=1,
                )
    linear_solvers = dict(rans.get("linear_solvers") or {})
    for field in ("p", "U", "nuTilda"):
        if field not in linear_solvers:
            continue
        pattern = rf"(?s)(\b{re.escape(field)}\s*\{{.*?\bsolver\s+)[^;]+;"
        solution, count = re.subn(
            pattern,
            rf"\g<1>{linear_solvers[field]};",
            solution,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Could not set SIMPLE linear solver for {field}")
    relaxation = dict(rans.get("relaxation") or {})
    if "p" in relaxation:
        solution = re.sub(
            r"(?s)(fields\s*\{[^}]*\bp\s+)[0-9.eE+-]+;",
            rf"\g<1>{float(relaxation['p']):.8g};",
            solution,
            count=1,
        )
    for field in ("U", "nuTilda"):
        if field in relaxation:
            solution = re.sub(
                rf"(?s)(equations\s*\{{[^}}]*\b{re.escape(field)}\s+)[0-9.eE+-]+;",
                rf"\g<1>{float(relaxation[field]):.8g};",
                solution,
                count=1,
            )
    solution_path.write_text(solution, encoding="utf-8")

    schemes_path = template / "fvSchemes"
    schemes = schemes_path.read_text(encoding="utf-8")
    initialization = dict(rans.get("initialization_schemes") or {})
    if initialization.get("velocity_divergence"):
        schemes = re.sub(
            r"(?m)^(\s*div\(phi,U\)\s+)[^;]+;",
            rf"\g<1>{initialization['velocity_divergence']};",
            schemes,
            count=1,
        )
    if initialization.get("turbulence_divergence"):
        schemes = re.sub(
            r"(?m)^(\s*div\(phi,nuTilda\)\s+)[^;]+;",
            rf"\g<1>{initialization['turbulence_divergence']};",
            schemes,
            count=1,
        )
    if rans.get("laplacian_scheme"):
        schemes = _replace_entry_in_dictionary(
            schemes,
            "laplacianSchemes",
            "default",
            str(rans["laplacian_scheme"]),
        )
    schemes_path.write_text(schemes, encoding="utf-8")


def _select_source_run(project_root: Path, mesh_id: str) -> dict[str, Any]:
    study = load_study(project_root)
    base_mesh_id = _base_mesh_id(mesh_id)
    candidates = [
        row
        for row in study["run_matrix"]["runs"]
        if row["mesh_id"] == base_mesh_id
        and mesh_angle_id(
            study,
            base_mesh_id,
            float(row.get("alpha_deg", 0.0)),
        ) == mesh_id
    ]
    if not candidates:
        raise RuntimeError(f"No run-matrix entry exists for {mesh_id}")
    return min(candidates, key=lambda item: float(item["dt_s"]))


def prepare_base(project_root: Path, mesh_id: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Prepare one coherent mesh/physics/case package without running OpenFOAM."""
    from ramair_2d_validation_study import prepare_checkpoint

    project_root = Path(project_root).resolve()
    row = _select_source_run(project_root, mesh_id)
    study = load_study(project_root)
    run_root = (
        active_workspace_root(project_root)
        / "runs"
        / str(row["topology"])
        / str(row["mesh_level"])
        / str(row["run_id"])
    )
    # A RANS base is built from the mesh and a dry canonical template.  If the
    # target-angle URANS case does not exist yet, prepare_checkpoint can use a
    # same-mesh sibling template and rewrites every angle-sensitive dictionary.
    manifest = prepare_checkpoint(project_root, mesh_id, overwrite=overwrite)
    study = load_study(project_root)
    mesh = _mesh(study, mesh_id)
    rans = _rans_config_for_mesh(project_root, study, mesh_id)
    contract = compatibility_contract(project_root, study, mesh_id)
    case = Path(manifest["case"])
    _configure_simple_template(case, rans)
    resolved_run = {
        "schema_version": 1,
        "mesh_id": mesh_id,
        "topology": mesh["topology"],
        "mesh_level": mesh["level"],
        "effective_rans": rans,
        "config_hash": sha256_json(rans),
        "created_at": utc_stamp(),
    }
    write_json_atomic(
        _checkpoint_root(project_root, mesh_id) / "resolved_run_config.json",
        resolved_run,
    )
    audit_simple_case_configuration(
        case,
        rans,
        output_path=(
            _checkpoint_root(project_root, mesh_id)
            / "applied_configuration_audit.json"
        ),
    )
    manifest.update(
        schema_version=2,
        checkpoint_id=f"{mesh_id}_simple",
        active_run_id=(
            f"{mesh_id}_rans_"
            f"{utc_stamp().replace(':', '').replace('-', '')}"
        ),
        status="RANS_BASE_PREPARED",
        mesh_hash=contract["mesh_hash"],
        physics_hash=contract["physics_hash"],
        solver_config_hash=contract["solver_config_hash"],
        compatibility=contract,
        initial_block=int(rans.get("initial_iterations", 20000)),
        extension_block=int(rans.get("extension_iterations", 20000)),
        max_iterations=int(rans.get("maximum_iterations", 20000)),
        iterations_completed=0,
        converged=False,
        bounded=False,
        gate={},
        final_window={},
        wall_time_to_10000_s=None,
        normalized_wall_time_per_10000_iterations=None,
        median_solver_seconds_per_iteration=None,
        extension_wall_time=[],
        total_wall_time=0.0,
        required_fields=list(REQUIRED_BASE_FIELDS),
        field_hashes={},
        storage_profile="steady_checkpoint_compact",
        prepared_at=utc_stamp(),
    )
    write_json_atomic(
        _checkpoint_root(project_root, mesh_id) / "checkpoint_manifest.json",
        manifest,
    )
    return manifest


def _numeric_iterations(case: Path) -> list[tuple[int, Path]]:
    values: list[tuple[int, Path]] = []
    for path in case.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0.0 and abs(value - round(value)) < 1.0e-8:
            values.append((int(round(value)), path))
    return sorted(values)


def _pending_latest_iteration(case: Path) -> int:
    return int(
        authoritative_simple_iteration(case).get(
            "absolute_simple_iteration", 0
        )
    )


def _runner_command(
    project_root: Path,
    case: Path,
    rans: dict[str, Any],
    *,
    decision: str | None = None,
    extension: int | None = None,
    run: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(
            project_root
            / "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py"
        ),
        "--case",
        str(case),
        "--solver",
        "auto",
        "--execution-backend",
        "native",
        "--n-cores",
        str(int(rans.get("mpi_ranks", 8))),
        "--steady-timeout-min",
        str(float(rans.get("timeout_min", 120.0))),
        "--steady-force-window-samples",
        str(int(rans.get("force_window_samples", 500))),
        "--steady-force-mean-tolerance-percent",
        str(float(rans.get("force_mean_tolerance_percent", 1.0))),
        "--steady-force-fluctuation-tolerance-percent",
        str(float(rans.get("force_fluctuation_tolerance_percent", 2.0))),
        "--steady-paraview-snapshots",
        "0",
        "--no-steady-pyfoam-live-monitor",
        "--steady-only",
    ]
    if decision:
        command += ["--steady-decision", decision]
    else:
        command.append("--steady-initialization")
        if not bool(rans.get("potentialFoam", True)):
            command.append("--no-steady-potential-foam")
    if extension is not None:
        command += ["--steady-additional-iterations", str(int(extension))]
    if run:
        command.append("--run")
    return command


def _run_command(command: list[str], cwd: Path) -> tuple[int, float]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=str(cwd))
    return int(completed.returncode), time.monotonic() - started


def _observed_solver_wall_time_s(case: Path) -> float:
    """Recover solver-active time from unique logs after an owner crash."""
    pattern = re.compile(r"ExecutionTime\s*=\s*([0-9.eE+\-]+)\s*s")
    candidates = [case / "log.foamRun", case / "PyFoamRunner.foamRun.logfile"]
    candidates.extend((case / "steadyInitialization/history").glob("run_*/**/log.foamRun"))
    candidates.extend((case / "steadyInitialization/history").glob("run_*/**/PyFoamRunner.foamRun.logfile"))
    seen: set[str] = set()
    total = 0.0
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        matches = pattern.findall(raw.decode("utf-8", errors="replace"))
        if matches:
            total += max(float(value) for value in matches)
    return total


def _normalized_field_hash(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            text = stream.read()
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r'\blocation\s+"[^"]+"\s*;', 'location "<normalized>";', text)
    text = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _field_path(zero: Path, name: str) -> Path | None:
    for path in (zero / name, zero / f"{name}.gz"):
        if path.is_file():
            return path
    return None


def _compact_steady_storage(checkpoint: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Keep restart data, two SIMPLE states and scalar diagnostics only."""
    case = checkpoint / "case"
    archives = sorted(
        (case / "steadyInitialization/history").glob("run_*"),
        key=lambda path: path.stat().st_mtime,
    )
    removed: list[str] = []
    retained: list[str] = []
    if archives:
        archive = archives[-1]
        time_root = archive / "time_directories"
        times = sorted(
            (
                (float(path.name), path)
                for path in time_root.iterdir()
                if path.is_dir()
            ),
            key=lambda item: item[0],
        ) if time_root.is_dir() else []
        keep = {path for _, path in times[-2:]}
        for _, path in times:
            if path in keep:
                retained.append(str(path))
            else:
                shutil.rmtree(path)
                removed.append(str(path))
        paraview_case = archive / "paraview_case"
        if paraview_case.exists():
            shutil.rmtree(paraview_case)
            removed.append(str(paraview_case))
    for pattern in ("VTK", "*.gif", "*.mp4"):
        for path in case.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
    return {
        "profile": "steady_checkpoint_compact",
        "retained_steady_states": retained,
        "removed_products": removed,
        "zero_preserved": (case / "0").is_dir(),
        "constant_preserved": (case / "constant").is_dir(),
        "system_preserved": (case / "system").is_dir(),
    }


def _finalize_manifest(
    project_root: Path,
    mesh_id: str,
    *,
    wall_times: list[float],
    diagnostic: bool = False,
) -> dict[str, Any]:
    checkpoint = _checkpoint_root(project_root, mesh_id)
    case = checkpoint / "case"
    manifest_path = checkpoint / "checkpoint_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    staged = read_json(case / "staged_run_status.json", {}) or {}
    accounting = authoritative_simple_iteration(case)
    iterations = int(accounting["absolute_simple_iteration"])
    gate = (
        read_json(checkpoint / "rans_review_manifest.json", {})
        or {}
    ).get("automatic_gate") or staged.get("steady_transition") or {}
    restart = _latest_restart_state(case)
    if restart is None:
        raise RuntimeError(f"{mesh_id}: no valid final SIMPLE state exists")
    restart_iteration, zero = restart
    if restart_iteration != iterations:
        iterations = restart_iteration
    required = list(REQUIRED_BASE_FIELDS)
    optional = list(OPTIONAL_BASE_FIELDS)
    field_hashes: dict[str, str] = {}
    missing: list[str] = []
    for name in dict.fromkeys([*required, *optional]):
        path = _field_path(zero, name)
        if path is None:
            if name in required:
                missing.append(name)
            continue
        field_hashes[name] = _normalized_field_hash(path)
    if missing:
        raise RuntimeError(
            f"{mesh_id}: checkpoint transfer is missing required fields {missing}"
        )
    final_window_size = max(1000, int(math.ceil(0.10 * max(iterations, 1))))
    # Each block already accumulates orchestration time in the manifest.  The
    # fallback is only for legacy callers that did not persist block timing.
    total_wall = float(manifest.get("total_wall_time") or 0.0)
    if total_wall <= 0.0:
        total_wall = float(sum(wall_times))
    segments = read_json(case / "solver_timing_segments.json", []) or []
    timing = timing_summary(segments if isinstance(segments, list) else [])
    manifest.update(
        status="DIAGNOSTIC_CHECKPOINT" if diagnostic else "CHECKPOINT_READY",
        iterations_completed=iterations,
        converged=not diagnostic,
        bounded=True,
        gate=gate,
        final_window={
            "definition": "max(1000 iterations, final 10%)",
            "samples_requested": final_window_size,
            "start_iteration": max(0, iterations - final_window_size),
            "end_iteration": iterations,
        },
        total_wall_time=total_wall,
        restart_zero=str(zero),
        absolute_simple_iteration=iterations,
        iteration_accounting=accounting,
        wall_time_to_10000_s=(
            total_wall * 10000.0 / iterations if iterations > 0 else None
        ),
        normalized_wall_time_per_10000_iterations=(
            total_wall * 10000.0 / iterations if iterations > 0 else None
        ),
        median_solver_seconds_per_iteration=(
            total_wall / iterations if iterations > 0 else None
        ),
        extension_wall_time=wall_times[1:],
        required_fields=required,
        optional_fields=optional,
        field_hashes=field_hashes,
        field_hash_method="normalized SHA-256 after transparent gzip decoding",
        solver_executed=True,
        updated_at=utc_stamp(),
    )
    manifest.update(timing)
    manifest["storage_audit"] = _compact_steady_storage(checkpoint, manifest)
    write_json_atomic(manifest_path, manifest)
    try:
        review = generate_review_diagnostics(project_root, mesh_id)
        manifest["automatic_gate_status"] = (
            review.get("automatic_gate") or {}
        ).get("status")
        manifest["review_manifest"] = str(
            checkpoint / "rans_review_manifest.json"
        )
        write_json_atomic(manifest_path, manifest)
    except (FileNotFoundError, RuntimeError, ValueError):
        pass
    _register_base_execution(project_root, manifest, activate=True)
    return manifest


def _register_base_execution(
    project_root: Path,
    manifest: dict[str, Any],
    *,
    activate: bool,
    queue_position: int | None = None,
    queue_total: int | None = None,
) -> None:
    checkpoint_id = str(
        manifest.get("checkpoint_id")
        or f"{manifest.get('mesh_id', 'unknown')}_simple"
    )
    upsert_execution(
        project_root,
        {
            "run_id": checkpoint_id,
            "mode": "RANS",
            "topology": manifest.get("topology"),
            "mesh_level": manifest.get("mesh_level"),
            "mesh_id": manifest.get("mesh_id"),
            "alpha_deg": manifest.get("alpha_deg"),
            "stage": "SIMPLE",
            "status": manifest.get("status"),
            "started_at": manifest.get("prepared_at"),
            "updated_at": manifest.get("updated_at") or utc_stamp(),
            "case_path": manifest.get("case"),
            "log_path": (manifest.get("gate") or {}).get("solver_log"),
            "iteration": manifest.get("iterations_completed", 0),
            "queue_position": queue_position,
            "queue_total": queue_total,
            "config_hash": manifest.get("solver_config_hash"),
        },
        activate=activate,
    )


def _append_orchestration_timing(
    case: Path,
    *,
    run_id: str,
    iteration_start: int,
    iteration_end: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Record orchestration elapsed time separately from solver-active timing."""
    path = case / "rans_orchestration_segments.json"
    rows = read_json(path, []) or []
    if not isinstance(rows, list):
        rows = []
    row = {
        "segment_id": f"{run_id}_{iteration_start}_{iteration_end}",
        "run_id": run_id,
        "iteration_start": int(iteration_start),
        "iteration_end": int(iteration_end),
        "orchestration_elapsed_seconds": float(elapsed_seconds),
        "recorded_at": utc_stamp(),
    }
    rows.append(row)
    write_json_atomic(path, rows)
    return row


def _simple_stop_request_source(
    case: Path,
    staged: dict[str, Any],
    *,
    explicit_stop_requested: bool,
) -> str | None:
    if explicit_stop_requested:
        return "VALIDATION_LAB_USER_STOP_MARKER"
    staged_source = staged.get("stop_request_source")
    if staged_source:
        return str(staged_source)
    for relative in (
        "log.foamRun",
        "PyFoamRunner.foamRun.logfile",
        "steadyInitialization/log.foamRun",
    ):
        path = case / relative
        if not path.is_file():
            continue
        tail = path.read_text(encoding="utf-8", errors="replace")[-20000:]
        if "SIMPLE solution converged" in tail:
            return "OPENFOAM_SIMPLE_RESIDUAL_CONTROL"
    return None


def _execute_targeted_rans_blocks(
    project_root: Path,
    mesh_id: str,
    case: Path,
    manifest: dict[str, Any],
    rans: dict[str, Any],
    *,
    initial_command: list[str],
    manual_extension_target: int | None = None,
) -> dict[str, Any]:
    """Execute SIMPLE only at absolute targets; never transfer to URANS."""
    initial = int(rans.get("initial_iterations", RANS_INITIAL_TARGET))
    extension = int(rans.get("extension_iterations", RANS_EXTENSION_BLOCK))
    maximum = int(
        manual_extension_target
        or rans.get("automatic_queue_max_iterations", RANS_MAXIMUM_TARGET)
    )
    minimum_convergence_iteration = int(
        rans.get(
            "minimum_simple_iterations_before_convergence_check",
            MINIMUM_CONVERGENCE_ITERATION,
        )
    )
    initial = RANS_INITIAL_TARGET
    extension = RANS_EXTENSION_BLOCK
    run_id = str(
        manifest.get("active_run_id")
        or f"{mesh_id}_rans_{utc_stamp().replace(':', '').replace('-', '')}"
    )
    manifest["active_run_id"] = run_id
    wall_times: list[float] = []
    command = initial_command
    while True:
        before = _pending_latest_iteration(case)
        target = target_for_iteration(
            before,
            initial=initial,
            extension=extension,
            maximum=maximum,
        )
        if before > 0:
            remaining = max(0, target - before)
            if remaining == 0:
                remaining = min(extension, maximum - before)
                target = min(maximum, before + remaining)
            command = _runner_command(
                project_root,
                case,
                rans,
                decision="extend",
                extension=remaining,
                run=True,
            )
        accounting = block_accounting(
            before,
            block_start=before,
            initial=initial,
            extension=extension,
            maximum=maximum,
        )
        accounting["block_target_iteration"] = target
        manifest.update(
            status=(
                "RANS_BASE_RUNNING"
                if before == 0
                else "RANS_BASE_EXTENDING"
            ),
            queue_state=(
                "RUNNING_INITIAL_BLOCK"
                if target == initial
                else "RUNNING_EXTENSION"
            ),
            command=command,
            minimum_convergence_iteration=minimum_convergence_iteration,
            process_exit_reason=None,
            stop_request_source=None,
            **accounting,
            updated_at=utc_stamp(),
        )
        write_json_atomic(
            _checkpoint_root(project_root, mesh_id) / "checkpoint_manifest.json",
            manifest,
        )
        code, elapsed = _run_command(command, project_root)
        wall_times.append(elapsed)
        after = _pending_latest_iteration(case)
        _append_orchestration_timing(
            case,
            run_id=run_id,
            iteration_start=before,
            iteration_end=after,
            elapsed_seconds=elapsed,
        )
        staged = read_json(case / "staged_run_status.json", {}) or {}
        explicit_stop = _stop_requested(project_root)
        stop_source = _simple_stop_request_source(
            case,
            staged,
            explicit_stop_requested=explicit_stop,
        )
        process_exit_reason = classify_simple_exit(
            return_code=code,
            absolute_iteration=after,
            block_target_iteration=target,
            staged_status=str(staged.get("status") or ""),
            explicit_stop_requested=explicit_stop,
            environment_failure=_environment_failure(
                {
                    "staged_status": staged.get("status"),
                    "message": staged.get("message"),
                    "error": staged.get("error"),
                }
            ),
        )
        accounting = block_accounting(
            after,
            block_start=before,
            initial=initial,
            extension=extension,
            maximum=maximum,
        )
        accounting["block_target_iteration"] = target
        manifest.update(
            iterations_completed=after,
            **accounting,
            staged_status=staged.get("status"),
            process_return_code=code,
            process_exit_reason=process_exit_reason,
            stop_request_source=stop_source,
            minimum_convergence_iteration=minimum_convergence_iteration,
            total_wall_time=float(manifest.get("total_wall_time") or 0.0)
            + elapsed,
            updated_at=utc_stamp(),
        )
        if process_exit_reason in {
            "USER_STOPPED_PARTIAL",
            "TIMEOUT_PARTIAL",
        }:
            manifest.update(
                status="RANS_PARTIAL",
                execution_status="PARTIAL",
                queue_state=(
                    "PAUSED_BY_USER"
                    if process_exit_reason == "USER_STOPPED_PARTIAL"
                    else "PAUSED_TIMEOUT"
                ),
                data_preserved_for_resume=True,
                stopped_reason=process_exit_reason,
            )
            write_json_atomic(
                _checkpoint_root(project_root, mesh_id)
                / "checkpoint_manifest.json",
                manifest,
            )
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        if process_exit_reason in {
            "DIVERGED",
            "RUN_SETUP_FAILED",
            "SOLVER_ERROR",
            "ENVIRONMENT_ERROR",
            "ORCHESTRATION_ERROR",
        }:
            manifest.update(
                status=(
                    "RANS_BASE_DIVERGED"
                    if process_exit_reason == "DIVERGED"
                    else "RANS_BASE_FAILED"
                ),
                execution_status="FAILED",
                queue_state=process_exit_reason,
                stopped_reason=process_exit_reason,
            )
            write_json_atomic(
                _checkpoint_root(project_root, mesh_id)
                / "checkpoint_manifest.json",
                manifest,
            )
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        if process_exit_reason == "PREMATURE_NORMAL_EXIT":
            manifest.update(
                status="RANS_PARTIAL",
                execution_status="PARTIAL",
                queue_state="PAUSED_PREMATURE_NORMAL_EXIT",
                data_preserved_for_resume=True,
                stopped_reason="PREMATURE_NORMAL_EXIT",
                remaining_to_target=target - after,
            )
            write_json_atomic(
                _checkpoint_root(project_root, mesh_id)
                / "checkpoint_manifest.json",
                manifest,
            )
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        if manual_extension_target is not None:
            try:
                diagnostic_manifest = generate_review_diagnostics(
                    project_root, mesh_id
                )
                automatic = dict(
                    diagnostic_manifest.get("automatic_gate") or {}
                )
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                automatic = {
                    "status": RANS_REVIEW_REQUIRED,
                    "diagnostic_error": str(exc),
                }
            segments = read_json(case / "solver_timing_segments.json", []) or []
            timing = timing_summary(segments if isinstance(segments, list) else [])
            manifest.update(
                status="RANS_BASE_BOUNDED_NOT_CONVERGED",
                execution_status="COMPLETED",
                queue_state="MANUAL_EXTENSION_COMPLETED",
                queue_action=f"MANUAL_EXTEND_TO_{after}",
                automatic_gate_status=automatic.get("status"),
                gate=automatic,
                manual_extension_completed=True,
                manual_extension_target=int(manual_extension_target),
                data_preserved_for_resume=True,
                solver_executed=True,
                updated_at=utc_stamp(),
            )
            manifest.update(timing)
            write_json_atomic(
                _checkpoint_root(project_root, mesh_id)
                / "checkpoint_manifest.json",
                manifest,
            )
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        if not convergence_gate_is_allowed(
            after,
            minimum_iteration=minimum_convergence_iteration,
        ):
            raise RuntimeError(
                f"{mesh_id}: convergence gate blocked below the configured "
                f"minimum ({after} < {minimum_convergence_iteration})"
            )
        if not gate_is_due(
            after,
            target,
            minimum_iteration=minimum_convergence_iteration,
        ):
            raise RuntimeError(
                f"{mesh_id}: gate requested outside an absolute target "
                f"(iteration={after}, target={target})"
            )
        try:
            diagnostic_manifest = generate_review_diagnostics(
                project_root, mesh_id
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            diagnostic_manifest = {
                "automatic_gate": {
                    "status": RANS_REVIEW_REQUIRED,
                    "diagnostic_error": str(exc),
                }
            }
        automatic = dict(diagnostic_manifest.get("automatic_gate") or {})
        automatic_status = str(
            automatic.get("status") or RANS_REVIEW_REQUIRED
        )
        manifest.update(
            gate=automatic,
            automatic_gate_status=automatic_status,
            gate_evaluated_at_absolute_iteration=after,
            minimum_convergence_iteration=minimum_convergence_iteration,
        )
        # The Cummings convergence campaign deliberately runs all 20 000 SIMPLE
        # iterations. Diagnostics are retained for the scientist, but never own
        # the stop/acceptance decision.
        if automatic_status in AUTOMATIC_READY_STATUSES:
            manifest["diagnostic_only_status"] = automatic_status
        if target < maximum:
            next_target = min(maximum, target + extension)
            manifest.update(
                status="RANS_BASE_EXTENDING",
                queue_state="RUNNING_EXTENSION",
                queue_action=f"AUTO_EXTEND_TO_{next_target}",
                block_start_iteration=after,
                block_target_iteration=next_target,
                block_completed_iterations=0,
            )
            write_json_atomic(
                _checkpoint_root(project_root, mesh_id)
                / "checkpoint_manifest.json",
                manifest,
            )
            continue
        manifest.update(
            status="RANS_BASE_BOUNDED_NOT_CONVERGED",
            execution_status="COMPLETED",
            queue_state="REVIEW_REQUIRED",
            queue_action="COMPLETED_20000_MANUAL_REVIEW_REQUIRED",
            converged=False,
            bounded=True,
            data_preserved_for_resume=True,
            solver_executed=True,
            updated_at=utc_stamp(),
        )
        write_json_atomic(
            _checkpoint_root(project_root, mesh_id) / "checkpoint_manifest.json",
            manifest,
        )
        _register_base_execution(project_root, manifest, activate=True)
        return manifest


def _execute_base_unlocked(
    project_root: Path,
    mesh_id: str,
    *,
    run: bool,
    overwrite: bool = False,
    allow_open_diagnostic: bool = False,
    consume_previous_stop_marker: bool = True,
    manual_extension_iterations: int | None = None,
) -> dict[str, Any]:
    """Prepare or execute one bounded, mesh-specific SIMPLE base."""
    project_root = Path(project_root).resolve()
    if run and consume_previous_stop_marker:
        # Consume only a marker left by an earlier stopped invocation. Any new
        # marker written after this point belongs to this execution and must
        # remain visible until the partial state has been persisted.
        _queue_stop_marker(project_root).unlink(missing_ok=True)
    study = load_study(project_root)
    mesh = _mesh(study, mesh_id)
    current_rans = _rans_config_for_mesh(project_root, study, mesh_id)
    checkpoint = _checkpoint_root(project_root, mesh_id)
    case = checkpoint / "case"
    existing_iteration = (
        _pending_latest_iteration(case) if case.is_dir() else 0
    )
    if overwrite and existing_iteration > 0:
        raise RuntimeError(
            f"{mesh_id}: a RANS solution already exists at iteration "
            f"{existing_iteration}. Use the explicit delete/restart action first."
        )
    if overwrite or not (checkpoint / "case/system/controlDict").is_file():
        manifest = prepare_base(project_root, mesh_id, overwrite=overwrite)
    else:
        manifest = read_json(checkpoint / "checkpoint_manifest.json", {}) or {}
        contract = compatibility_contract(project_root, study, mesh_id)
        mesh = _mesh(study, mesh_id)
        manifest.setdefault("schema_version", 2)
        manifest.setdefault("checkpoint_id", f"{mesh_id}_simple")
        manifest.setdefault("mesh_id", mesh_id)
        manifest.setdefault("topology", mesh["topology"])
        manifest.setdefault("mesh_level", mesh["level"])
        manifest.setdefault("mesh_hash", contract["mesh_hash"])
        manifest.setdefault("physics_hash", contract["physics_hash"])
        manifest.setdefault("solver_config_hash", contract["solver_config_hash"])
        manifest.setdefault("compatibility", contract)
        prepared_defaults = {
            "initial_block": int(current_rans.get("initial_iterations", 20000)),
            "extension_block": int(current_rans.get("extension_iterations", 20000)),
            "max_iterations": int(current_rans.get("maximum_iterations", 20000)),
            "iterations_completed": 0,
            "converged": False,
            "bounded": False,
            "gate": {},
            "final_window": {},
            "wall_time_to_10000_s": None,
            "normalized_wall_time_per_10000_iterations": None,
            "median_solver_seconds_per_iteration": None,
            "extension_wall_time": [],
            "total_wall_time": 0.0,
            "required_fields": list(REQUIRED_BASE_FIELDS),
            "field_hashes": {},
            "storage_profile": "steady_checkpoint_compact",
        }
        for key, value in prepared_defaults.items():
            manifest.setdefault(key, value)
        if existing_iteration == 0:
            # A prepared-but-never-run base may still carry metadata from an
            # older batch revision. Refresh it from the current frozen study
            # contract without touching any checkpoint that contains solver
            # iterations.
            manifest.update(
                mesh_hash=contract["mesh_hash"],
                physics_hash=contract["physics_hash"],
                solver_config_hash=contract["solver_config_hash"],
                compatibility=contract,
                initial_block=int(
                    current_rans.get("initial_iterations", RANS_INITIAL_TARGET)
                ),
                extension_block=int(
                    current_rans.get("extension_iterations", RANS_EXTENSION_BLOCK)
                ),
                max_iterations=int(
                    current_rans.get("maximum_iterations", RANS_MAXIMUM_TARGET)
                ),
            )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
    case = checkpoint / "case"
    resolved_run_path = checkpoint / "resolved_run_config.json"
    resolved_run = read_json(resolved_run_path, {}) or {}
    if existing_iteration == 0 or not resolved_run:
        resolved_run = {
            "schema_version": 1,
            "mesh_id": mesh_id,
            "topology": mesh["topology"],
            "mesh_level": mesh["level"],
            "effective_rans": current_rans,
            "config_hash": sha256_json(current_rans),
            "created_at": utc_stamp(),
        }
    rans = dict(resolved_run.get("effective_rans") or current_rans)
    _configure_simple_template(case, rans)
    write_json_atomic(resolved_run_path, resolved_run)
    audit = audit_simple_case_configuration(
        case,
        dict(resolved_run.get("effective_rans") or rans),
        output_path=checkpoint / "applied_configuration_audit.json",
    )
    existing_iteration = _pending_latest_iteration(case)
    allowed_uses = _review_allows_use(project_root, mesh_id)
    automatic_status = str(
        review_manifest(project_root, mesh_id)
        .get("automatic_gate", {})
        .get("status")
        or ""
    )
    completed_or_accepted = bool(
        manifest.get("status") in READY_STATUSES
        or automatic_status in AUTOMATIC_READY_STATUSES
        or any(allowed_uses.values())
    )
    if (
        run
        and existing_iteration > 0
        and completed_or_accepted
        and manual_extension_iterations is None
    ):
        manifest.update(
            restart_blocked=True,
            restart_blocked_reason=(
                "Existing completed/accepted RANS data are protected. "
                "Continue is unnecessary; delete explicitly before a fresh run."
            ),
            iterations_completed=existing_iteration,
            updated_at=utc_stamp(),
        )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
        return manifest
    automatic_maximum = int(
        rans.get(
            "automatic_queue_max_iterations",
            rans.get("maximum_iterations", 20000),
        )
    )
    if run and existing_iteration >= automatic_maximum and manual_extension_iterations is None:
        try:
            review = generate_review_diagnostics(project_root, mesh_id)
        except (FileNotFoundError, RuntimeError, ValueError):
            review = {}
        manifest.update(
            status="RANS_BASE_BOUNDED_NOT_CONVERGED",
            execution_status="COMPLETED",
            iterations_completed=existing_iteration,
            automatic_gate_status=(review.get("automatic_gate") or {}).get(
                "status", RANS_REVIEW_REQUIRED
            ),
            restart_blocked=True,
            restart_blocked_reason=(
                "Configured maximum iteration count already reached. "
                "Review, revise, or delete explicitly before a fresh run."
            ),
            updated_at=utc_stamp(),
        )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
        return manifest
    pending_path = case / "steadyInitialization/pending_stage.json"
    if run and existing_iteration > 0 and not pending_path.is_file():
        histories = sorted(
            path
            for path in (case / "steadyInitialization/history").glob("run_*")
            if (path / "transient_system_before_steady").is_dir()
        )
        if not histories:
            raise RuntimeError(
                f"{mesh_id}: iteration {existing_iteration} exists but the "
                "steady archive needed for a safe resume is missing."
            )
        write_json_atomic(
            pending_path,
            {
                "status": "RECOVERED_PARTIAL",
                "archive": str(histories[-1].resolve()),
                "latest_iteration": existing_iteration,
                "available_actions": ["extend", "start-transient", "finish"],
                "recovered_at": utc_stamp(),
            },
        )
    extension = int(rans.get("extension_iterations", RANS_EXTENSION_BLOCK))
    manual_extension = (
        int(manual_extension_iterations)
        if manual_extension_iterations is not None
        else None
    )
    if manual_extension is not None and manual_extension <= 0:
        raise ValueError("manual_extension_iterations must be positive")
    resume_target = (
        existing_iteration + manual_extension
        if manual_extension is not None
        else target_for_iteration(
            existing_iteration,
            initial=RANS_INITIAL_TARGET,
            extension=RANS_EXTENSION_BLOCK,
            maximum=automatic_maximum,
        )
    )
    resume_extension = max(0, resume_target - existing_iteration)
    initial_command = _runner_command(
        project_root,
        case,
        rans,
        decision="extend" if run and existing_iteration > 0 else None,
        extension=resume_extension if run and existing_iteration > 0 else None,
        run=run,
    )
    manifest.update(
        status=(
            "RANS_BASE_EXTENDING"
            if run and existing_iteration > 0
            else "RANS_BASE_RUNNING"
            if run
            else "RANS_BASE_PREPARED"
        ),
        command=initial_command,
        updated_at=utc_stamp(),
        parent_run_id=(
            str(manifest.get("last_run_id") or manifest.get("checkpoint_id"))
            if run and existing_iteration > 0
            else None
        ),
        resume_from_iteration=existing_iteration if run and existing_iteration > 0 else None,
        resume_block_index=(
            int(manifest.get("resume_block_index") or 0) + 1
            if run and existing_iteration > 0
            else int(manifest.get("resume_block_index") or 0)
        ),
        resolved_run_config=str(resolved_run_path),
        applied_configuration_audit=str(
            checkpoint / "applied_configuration_audit.json"
        ),
        configuration_audit_status=audit["status"],
        manual_extension_requested=manual_extension is not None,
        manual_extension_start_iteration=(
            existing_iteration if manual_extension is not None else None
        ),
        manual_extension_target=(
            resume_target if manual_extension is not None else None
        ),
        original_resolved_run_config=str(resolved_run_path),
    )
    if run and existing_iteration > 0:
        manifest["total_wall_time"] = max(
            float(manifest.get("total_wall_time") or 0.0),
            _observed_solver_wall_time_s(case),
        )
        manifest["wall_time_recovered_from_solver_logs"] = True
    write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
    _register_base_execution(project_root, manifest, activate=True)
    if not run:
        return manifest

    (case / ".ramair_stop_request.json").unlink(missing_ok=True)
    return _execute_targeted_rans_blocks(
        project_root,
        mesh_id,
        case,
        manifest,
        rans,
        initial_command=initial_command,
        manual_extension_target=(
            resume_target if manual_extension is not None else None
        ),
    )

    (case / ".ramair_stop_request.json").unlink(missing_ok=True)
    wall_times: list[float] = []
    code, elapsed = _run_command(initial_command, project_root)
    wall_times.append(elapsed)
    staged = read_json(case / "staged_run_status.json", {}) or {}
    if staged.get("status") in PARTIAL_STAGED_STATUSES or _stop_requested(
        project_root
    ):
        manifest.update(
            status="RANS_PARTIAL",
            execution_status="PARTIAL",
            iterations_completed=_pending_latest_iteration(case),
            total_wall_time=float(manifest.get("total_wall_time") or 0.0)
            + sum(wall_times),
            staged_status=staged.get("status"),
            data_preserved_for_resume=True,
            updated_at=utc_stamp(),
        )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
        _register_base_execution(project_root, manifest, activate=True)
        return manifest
    if code != 0:
        status = (
            "RANS_BASE_DIVERGED"
            if staged.get("status") == "STEADY_STAGE_DIVERGED"
            else "RANS_BASE_FAILED"
        )
        manifest.update(
            status=status,
            total_wall_time=sum(wall_times),
            staged_status=staged.get("status"),
            updated_at=utc_stamp(),
        )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
        _register_base_execution(project_root, manifest, activate=True)
        return manifest
    if staged.get("status") == "STEADY_CHECKPOINT_READY":
        return _finalize_manifest(
            project_root, mesh_id, wall_times=wall_times
        )

    try:
        diagnostic_manifest = generate_review_diagnostics(project_root, mesh_id)
    except (FileNotFoundError, RuntimeError, ValueError):
        diagnostic_manifest = {}
    automatic = dict(diagnostic_manifest.get("automatic_gate") or {})
    if automatic.get("status") in AUTOMATIC_READY_STATUSES:
        command = _runner_command(
            project_root, case, rans, decision="start-transient", run=True
        )
        code, elapsed = _run_command(command, project_root)
        wall_times.append(elapsed)
        if code == 0:
            return _finalize_manifest(
                project_root, mesh_id, wall_times=wall_times
            )
    if automatic.get("no_meaningful_extension_improvement"):
        manifest.update(
            status="RANS_BASE_BOUNDED_NOT_CONVERGED",
            execution_status="COMPLETED",
            automatic_gate_status=automatic.get("status"),
            iterations_completed=_pending_latest_iteration(case),
            converged=False,
            bounded=True,
            gate=automatic,
            total_wall_time=float(manifest.get("total_wall_time") or 0.0)
            + sum(wall_times),
            stopped_reason="NO_MEANINGFUL_EXTENSION_IMPROVEMENT",
            updated_at=utc_stamp(),
        )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
        _register_base_execution(project_root, manifest, activate=True)
        return manifest

    while _pending_latest_iteration(case) < maximum:
        if run and _stop_requested(project_root):
            manifest.update(
                status="RANS_PARTIAL",
                execution_status="PARTIAL",
                iterations_completed=_pending_latest_iteration(case),
                data_preserved_for_resume=True,
                updated_at=utc_stamp(),
            )
            write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        remaining = maximum - _pending_latest_iteration(case)
        extension_now = min(extension, remaining)
        manifest.update(
            status="RANS_BASE_EXTENDING",
            iterations_completed=_pending_latest_iteration(case),
            updated_at=utc_stamp(),
        )
        write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
        command = _runner_command(
            project_root,
            case,
            rans,
            decision="extend",
            extension=extension_now,
            run=True,
        )
        code, elapsed = _run_command(command, project_root)
        wall_times.append(elapsed)
        staged = read_json(case / "staged_run_status.json", {}) or {}
        if staged.get("status") in PARTIAL_STAGED_STATUSES or _stop_requested(
            project_root
        ):
            manifest.update(
                status="RANS_PARTIAL",
                execution_status="PARTIAL",
                iterations_completed=_pending_latest_iteration(case),
                total_wall_time=float(manifest.get("total_wall_time") or 0.0)
                + sum(wall_times),
                staged_status=staged.get("status"),
                data_preserved_for_resume=True,
                updated_at=utc_stamp(),
            )
            write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        if code != 0:
            manifest.update(
                status="RANS_BASE_DIVERGED"
                if staged.get("status") == "STEADY_STAGE_DIVERGED"
                else "RANS_BASE_FAILED",
                total_wall_time=sum(wall_times),
                staged_status=staged.get("status"),
                updated_at=utc_stamp(),
            )
            write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
            _register_base_execution(project_root, manifest, activate=True)
            return manifest
        if staged.get("status") == "STEADY_CHECKPOINT_READY":
            return _finalize_manifest(
                project_root, mesh_id, wall_times=wall_times
            )
        try:
            diagnostic_manifest = generate_review_diagnostics(
                project_root, mesh_id
            )
        except (FileNotFoundError, RuntimeError, ValueError):
            diagnostic_manifest = {}
        automatic = dict(diagnostic_manifest.get("automatic_gate") or {})
        if automatic.get("status") in AUTOMATIC_READY_STATUSES:
            command = _runner_command(
                project_root, case, rans, decision="start-transient", run=True
            )
            code, elapsed = _run_command(command, project_root)
            wall_times.append(elapsed)
            if code == 0:
                return _finalize_manifest(
                    project_root, mesh_id, wall_times=wall_times
                )
        if automatic.get("no_meaningful_extension_improvement"):
            manifest.update(
                status="RANS_BASE_BOUNDED_NOT_CONVERGED",
                execution_status="COMPLETED",
                automatic_gate_status=automatic.get("status"),
                iterations_completed=_pending_latest_iteration(case),
                converged=False,
                bounded=True,
                gate=automatic,
                total_wall_time=float(manifest.get("total_wall_time") or 0.0)
                + sum(wall_times),
                stopped_reason="NO_MEANINGFUL_EXTENSION_IMPROVEMENT",
                updated_at=utc_stamp(),
            )
            write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
            _register_base_execution(project_root, manifest, activate=True)
            return manifest

    topology = str(_mesh(load_study(project_root), mesh_id)["topology"])
    if topology == "open" and allow_open_diagnostic:
        command = _runner_command(
            project_root, case, rans, decision="start-transient", run=True
        )
        code, elapsed = _run_command(command, project_root)
        wall_times.append(elapsed)
        staged = read_json(case / "staged_run_status.json", {}) or {}
        if code == 0 and staged.get("status") == "STEADY_CHECKPOINT_READY":
            return _finalize_manifest(
                project_root, mesh_id, wall_times=wall_times, diagnostic=True
            )

    finish_command = _runner_command(
        project_root, case, rans, decision="finish", run=True
    )
    _, elapsed = _run_command(finish_command, project_root)
    wall_times.append(elapsed)
    manifest.update(
        status="RANS_BASE_BOUNDED_NOT_CONVERGED",
        iterations_completed=_pending_latest_iteration(case),
        converged=False,
        bounded=True,
        gate=(read_json(case / "staged_run_status.json", {}) or {}).get(
            "steady_transition", {}
        ),
        total_wall_time=sum(wall_times),
        solver_executed=True,
        updated_at=utc_stamp(),
    )
    write_json_atomic(checkpoint / "checkpoint_manifest.json", manifest)
    _register_base_execution(project_root, manifest, activate=True)
    return manifest


def execute_base(
    project_root: Path,
    mesh_id: str,
    *,
    run: bool,
    overwrite: bool = False,
    allow_open_diagnostic: bool = False,
    consume_previous_stop_marker: bool = True,
    manual_extension_iterations: int | None = None,
) -> dict[str, Any]:
    """Single-flight wrapper around one bounded RANS base execution."""
    project_root = Path(project_root).resolve()
    if not run:
        return _execute_base_unlocked(
            project_root,
            mesh_id,
            run=False,
            overwrite=overwrite,
            allow_open_diagnostic=allow_open_diagnostic,
            consume_previous_stop_marker=consume_previous_stop_marker,
            manual_extension_iterations=manual_extension_iterations,
        )
    checkpoint = _checkpoint_root(project_root, mesh_id)
    existing = read_json(checkpoint / "checkpoint_manifest.json", {}) or {}
    run_id = str(
        existing.get("active_run_id")
        or f"{mesh_id}_rans_{utc_stamp().replace(':', '').replace('-', '')}"
    )
    try:
        lease = acquire_run_lease(
            active_workspace_root(project_root) / "locks",
            study_id="validation_lab",
            run_id=run_id,
            mode="RANS",
            command=[str(project_root), mesh_id, "execute"],
        )
    except DuplicateExecutionError as exc:
        return {
            **exc.payload,
            "mesh_id": mesh_id,
            "run_id": run_id,
            "updated_at": utc_stamp(),
        }
    try:
        lease.heartbeat(state="RUNNING")
        result = _execute_base_unlocked(
            project_root,
            mesh_id,
            run=True,
            overwrite=overwrite,
            allow_open_diagnostic=allow_open_diagnostic,
            consume_previous_stop_marker=consume_previous_stop_marker,
            manual_extension_iterations=manual_extension_iterations,
        )
        lease.release(
            state=(
                "PARTIAL"
                if result.get("status") == "RANS_PARTIAL"
                else "FAILED"
                if result.get("status") in {
                    "RANS_BASE_FAILED",
                    "RANS_BASE_DIVERGED",
                }
                else "COMPLETED"
            ),
            final_status=result.get("status"),
            absolute_simple_iteration=result.get(
                "absolute_simple_iteration",
                result.get("iterations_completed", 0),
            ),
        )
        return result
    except BaseException as exc:
        lease.release(state="FAILED", error=str(exc))
        raise


def _environment_failure(result: dict[str, Any]) -> bool:
    text = " ".join(
        str(result.get(key) or "")
        for key in ("message", "error", "staged_status")
    ).lower()
    markers = (
        "openfoam environment",
        "executable not found",
        "no such file or directory",
        "mpirun",
        "foamrun",
        "checkmesh is required",
        "permission denied",
    )
    return any(marker in text for marker in markers)


def _execute_queue_unlocked(
    project_root: Path,
    *,
    run: bool,
    alpha_deg: float | None = None,
    continue_on_nonfatal_failure: bool | None = None,
) -> dict[str, Any]:
    """Visit all six canonical bases and execute only the incomplete ones."""
    project_root = Path(project_root).resolve()
    study = load_study(project_root)
    queue_ids = [mesh_angle_id(study, mesh_id, alpha_deg) for mesh_id in RANS_QUEUE_IDS]
    rans = _rans_config(study)
    if continue_on_nonfatal_failure is None:
        continue_on_nonfatal_failure = bool(
            rans.get("continue_after_case_failure", True)
        )
    state_path = active_workspace_root(project_root) / "rans_queue_state.json"
    if run:
        _queue_stop_marker(project_root).unlink(missing_ok=True)
    resolved_batch = freeze_batch_config(project_root, study)
    state = read_json(state_path, {}) or {
        "schema_version": 1,
        "order": list(queue_ids),
        "items": {},
        "started_at": utc_stamp(),
    }
    if str(state.get("batch_id") or "") != str(resolved_batch["batch_id"]):
        for stale_key in (
            "current_mesh_id",
            "current_queue_position",
            "current_target_iteration",
            "data_preserved_for_resume",
            "environment_failure",
            "finished_at",
            "resume_from_iteration",
            "stopped_before_mesh_id",
        ):
            state.pop(stale_key, None)
    state["order"] = list(queue_ids)
    state["items"] = {
        mesh_id: value
        for mesh_id, value in dict(state.get("items") or {}).items()
        if mesh_id in queue_ids
    }
    state.update(status="RUNNING" if run else "PREPARED", updated_at=utc_stamp())
    state["batch_id"] = resolved_batch["batch_id"]
    state["resume_policy"] = "FIRST_INCOMPLETE_CANONICAL_BASE"
    state["message"] = (
        "The queue continues from the first incomplete base; completed and "
        "accepted bases are never restarted automatically."
    )
    write_json_atomic(state_path, state)
    aggregate_audits: list[dict[str, Any]] = []
    state["batch_total"] = len(queue_ids)
    state["alpha_deg"] = alpha_deg
    for queue_position, mesh_id in enumerate(queue_ids, 1):
        previous = checkpoint_status(project_root, mesh_id)
        allowed = dict(previous.get("allowed_uses") or {})
        if run and (
            previous["status"] in READY_STATUSES
            or bool(allowed.get("rans_spatial_convergence"))
            or bool(allowed.get("urans_initialization"))
        ):
            state["items"][mesh_id] = {
                **previous,
                "queue_action": "SKIPPED_COMPLETED_OR_REVIEWABLE",
            }
            continue
        if run and _stop_requested(project_root):
            state.update(
                status="STOPPED_BY_USER",
                stopped_before_mesh_id=mesh_id,
                updated_at=utc_stamp(),
            )
            write_json_atomic(state_path, state)
            return state
        refresh_stale = previous["status"] in {
            "CHECKPOINT_STALE_MESH_CHANGED",
            "CHECKPOINT_STALE_PHYSICS_CHANGED",
        } or (
            previous["status"] == "RANS_BASE_NOT_CREATED"
            and _checkpoint_root(project_root, mesh_id).exists()
        )
        if refresh_stale:
            result = {
                **previous,
                "status": str(previous["status"]),
                "queue_action": "BLOCKED_REQUIRES_EXPLICIT_DELETE_OR_REVISION",
                "message": (
                    "The active base is incompatible and was preserved. "
                    "Delete it explicitly or create a new configuration revision."
                ),
                "updated_at": utc_stamp(),
            }
        else:
            state.update(
                current_mesh_id=mesh_id,
                current_queue_position=queue_position,
                queue_total=len(queue_ids),
                current_target_iteration=target_for_iteration(
                    int(previous.get("iterations_completed", 0) or 0),
                    initial=RANS_INITIAL_TARGET,
                    extension=RANS_EXTENSION_BLOCK,
                    maximum=RANS_MAXIMUM_TARGET,
                ),
                updated_at=utc_stamp(),
            )
            write_json_atomic(state_path, state)
            try:
                result = execute_base(
                    project_root,
                    mesh_id,
                    run=run,
                    overwrite=False,
                    consume_previous_stop_marker=False,
                )
            except Exception as exc:
                result = {
                    "mesh_id": mesh_id,
                    "status": "RANS_BASE_FAILED",
                    "message": str(exc),
                    "updated_at": utc_stamp(),
                }
        state["items"][mesh_id] = result
        state["current_mesh_id"] = mesh_id
        state["current_queue_position"] = queue_position
        state["queue_total"] = len(queue_ids)
        state["resolved_batch_config"] = str(
            active_workspace_root(project_root) / "resolved_batch_config.json"
        )
        state["config_hash"] = resolved_batch["config_hash"]
        state["updated_at"] = utc_stamp()
        write_json_atomic(state_path, state)
        checkpoint = _checkpoint_root(project_root, mesh_id)
        run_snapshot = read_json(checkpoint / "resolved_run_config.json", {}) or {}
        run_snapshot.update(
            batch_id=resolved_batch["batch_id"],
            batch_config_hash=resolved_batch["config_hash"],
            frozen_at=resolved_batch["created_at"],
        )
        write_json_atomic(checkpoint / "resolved_run_config.json", run_snapshot)
        audit = read_json(checkpoint / "applied_configuration_audit.json", {}) or {}
        if audit:
            aggregate_audits.append({"mesh_id": mesh_id, **audit})
            write_json_atomic(
                active_workspace_root(project_root)
                / "applied_configuration_audit.json",
                {
                    "batch_id": resolved_batch["batch_id"],
                    "config_hash": resolved_batch["config_hash"],
                    "status": (
                        "CONFIGURATION_APPLIED"
                        if all(
                            item.get("status") == "CONFIGURATION_APPLIED"
                            for item in aggregate_audits
                        )
                        else "CONFIGURATION_MISMATCH"
                    ),
                    "runs": aggregate_audits,
                    "updated_at": utc_stamp(),
                },
            )
        if isinstance(result, dict):
            _register_base_execution(
                project_root,
                result,
                activate=bool(run or mesh_id == queue_ids[0]),
                queue_position=queue_position,
                queue_total=len(queue_ids),
            )
        if (
            run
            and result.get("status") in {"RANS_BASE_DIVERGED", "RANS_BASE_FAILED"}
        ):
            if _environment_failure(result):
                state.update(
                    status="STOPPED_ON_ENVIRONMENT_FAILURE",
                    environment_failure=result,
                    updated_at=utc_stamp(),
                )
                write_json_atomic(state_path, state)
                return state
            if not continue_on_nonfatal_failure:
                state["status"] = "STOPPED_ON_FAILURE"
                write_json_atomic(state_path, state)
                return state
        if run and result.get("status") == "RANS_PARTIAL":
            exit_reason = str(
                result.get("process_exit_reason")
                or result.get("stopped_reason")
                or "UNKNOWN_PARTIAL_EXIT"
            )
            queue_status = {
                "USER_STOPPED_PARTIAL": "STOPPED_BY_USER",
                "TIMEOUT_PARTIAL": "PAUSED_TIMEOUT",
                "PREMATURE_NORMAL_EXIT": "PAUSED_PREMATURE_NORMAL_EXIT",
            }.get(exit_reason, "PAUSED_PARTIAL")
            stop_action = _queue_stop_action(project_root)
            state.update(
                status=queue_status,
                current_mesh_id=mesh_id,
                resume_from_iteration=result.get("iterations_completed"),
                data_preserved_for_resume=True,
                process_exit_reason=exit_reason,
                stop_request_source=result.get("stop_request_source"),
                updated_at=utc_stamp(),
            )
            write_json_atomic(state_path, state)
            if exit_reason == "USER_STOPPED_PARTIAL" and stop_action == "pause_current_continue":
                state["items"][mesh_id]["queue_action"] = "PAUSED_AND_SKIPPED_BY_USER"
                _queue_stop_marker(project_root).unlink(missing_ok=True)
                continue
            return state
    if run:
        state.update(status="COMPLETED", finished_at=utc_stamp())
    else:
        state.pop("finished_at", None)
        state.update(
            status="PREPARED",
            current_mesh_id=queue_ids[0],
            current_queue_position=0,
            current_target_iteration=RANS_INITIAL_TARGET,
            prepared_at=utc_stamp(),
        )
        first_manifest = state["items"].get(queue_ids[0])
        if isinstance(first_manifest, dict):
            _register_base_execution(
                project_root,
                first_manifest,
                activate=True,
                queue_position=1,
                queue_total=len(queue_ids),
            )
    write_json_atomic(state_path, state)
    return state


def execute_queue(
    project_root: Path,
    *,
    run: bool,
    alpha_deg: float | None = None,
    continue_on_nonfatal_failure: bool | None = None,
) -> dict[str, Any]:
    """Single-flight wrapper for the autonomous five-base RANS queue."""
    project_root = Path(project_root).resolve()
    if not run:
        return _execute_queue_unlocked(
            project_root,
            run=False,
            alpha_deg=alpha_deg,
            continue_on_nonfatal_failure=continue_on_nonfatal_failure,
        )
    active = active_workspace_root(project_root)
    previous = read_json(active / "rans_queue_state.json", {}) or {}
    batch_id = str(
        previous.get("batch_id")
        or f"rans_batch_{utc_stamp().replace(':', '').replace('-', '')}"
    )
    try:
        lease = acquire_run_lease(
            active / "locks",
            study_id="validation_lab",
            run_id=batch_id,
            mode="RANS_BATCH",
            command=[str(project_root), "queue", str(alpha_deg), *RANS_QUEUE_IDS],
        )
    except DuplicateExecutionError as exc:
        return {
            **exc.payload,
            "batch_id": batch_id,
            "updated_at": utc_stamp(),
        }
    try:
        lease.heartbeat(state="RUNNING")
        result = _execute_queue_unlocked(
            project_root,
            run=True,
            alpha_deg=alpha_deg,
            continue_on_nonfatal_failure=continue_on_nonfatal_failure,
        )
        lease.release(
            state=(
                "PARTIAL"
                if str(result.get("status") or "").startswith(("STOPPED", "PAUSED"))
                else "FAILED"
                if result.get("status") == "STOPPED_ON_FAILURE"
                else "COMPLETED"
            ),
            final_status=result.get("status"),
            current_mesh_id=result.get("current_mesh_id"),
        )
        return result
    except BaseException as exc:
        lease.release(state="FAILED", error=str(exc))
        raise


def execute_selection_queue(
    project_root: Path,
    case_specs: list[str],
    *,
    run: bool,
    continue_on_nonfatal_failure: bool = True,
) -> dict[str, Any]:
    """Run an ordered RANS selection with durable resume and stop semantics."""
    project_root = Path(project_root).resolve()
    active = active_workspace_root(project_root)
    state_path = active / "rans_selection_queue_state.json"
    study = load_study(project_root)
    ordered: list[dict[str, Any]] = []
    for order, case_spec in enumerate(case_specs, start=1):
        base_mesh_id, alpha_text = str(case_spec).rsplit(":", 1)
        alpha_deg = float(alpha_text)
        ordered.append({
            "order": order,
            "requested_case": str(case_spec),
            "mesh_id": mesh_angle_id(study, base_mesh_id, alpha_deg),
            "base_mesh_id": base_mesh_id,
            "alpha_deg": alpha_deg,
        })
    previous = read_json(state_path, {}) or {}
    same_order = [row["requested_case"] for row in ordered] == list(previous.get("order") or [])
    prior_cases = {
        str(row.get("requested_case")): row for row in previous.get("cases", [])
    } if same_order else {}
    state = {
        "schema_version": 2,
        "queue_id": previous.get("queue_id") if same_order else f"rans_selection_{utc_stamp().replace(':', '').replace('-', '')}",
        "status": "RUNNING" if run else "PREPARED",
        "order": [row["requested_case"] for row in ordered],
        "cases": [],
        "current_index": 0,
        "total": len(ordered),
        "resume_policy": "SKIP_COMPLETED_RESUME_FIRST_PARTIAL",
        "updated_at": utc_stamp(),
    }
    if run:
        _queue_stop_marker(project_root).unlink(missing_ok=True)
    write_json_atomic(state_path, state)
    first_pending_index: int | None = None
    for index, requested in enumerate(ordered):
        mesh_id = str(requested["mesh_id"])
        status = checkpoint_status(project_root, mesh_id)
        item = {**requested, **prior_cases.get(str(requested["requested_case"]), {})}
        item.update(status)
        state.update(
            current_index=index,
            current_mesh_id=mesh_id,
            current_queue_position=index + 1,
            current_target_iteration=target_for_iteration(
                int(status.get("iterations_completed") or 0),
                initial=RANS_INITIAL_TARGET,
                extension=RANS_EXTENSION_BLOCK,
                maximum=RANS_MAXIMUM_TARGET,
            ),
            updated_at=utc_stamp(),
        )
        if str(status.get("status")) in READY_STATUSES or int(status.get("iterations_completed") or 0) >= RANS_MAXIMUM_TARGET:
            item["queue_action"] = "SKIPPED_COMPLETED"
            state["cases"].append(item)
            state["current_index"] = index + 1
            write_json_atomic(state_path, state)
            continue
        if not run:
            item["queue_action"] = "PREPARED"
            state["cases"].append(item)
            if first_pending_index is None:
                first_pending_index = index
            continue
        write_json_atomic(state_path, state)
        try:
            result = execute_base(
                project_root, mesh_id, run=True, overwrite=False,
                consume_previous_stop_marker=False,
            )
        except Exception as exc:
            result = {"mesh_id": mesh_id, "status": "RANS_BASE_FAILED", "message": str(exc)}
        item.update(result)
        state["cases"].append(item)
        state["current_index"] = index + 1
        state["updated_at"] = utc_stamp()
        write_json_atomic(state_path, state)
        if str(result.get("status")) == "RANS_PARTIAL":
            action = _queue_stop_action(project_root)
            if action == "pause_current_continue":
                item["queue_action"] = "PAUSED_AND_SKIPPED_BY_USER"
                _queue_stop_marker(project_root).unlink(missing_ok=True)
                write_json_atomic(state_path, state)
                continue
            state.update(status="PAUSED_BY_USER", current_index=index)
            write_json_atomic(state_path, state)
            return state
        if str(result.get("status")) in {"RANS_BASE_FAILED", "RANS_BASE_DIVERGED"} and not continue_on_nonfatal_failure:
            state["status"] = "STOPPED_ON_FAILURE"
            write_json_atomic(state_path, state)
            return state
    state["status"] = "COMPLETED" if run else "PREPARED"
    state["current_index"] = (
        len(ordered)
        if run or first_pending_index is None
        else first_pending_index
    )
    if not run and first_pending_index is not None:
        pending = state["cases"][first_pending_index]
        state["current_mesh_id"] = pending["mesh_id"]
        state["current_queue_position"] = first_pending_index + 1
        state["current_target_iteration"] = target_for_iteration(
            int(pending.get("iterations_completed") or 0),
            initial=RANS_INITIAL_TARGET,
            extension=RANS_EXTENSION_BLOCK,
            maximum=RANS_MAXIMUM_TARGET,
        )
    state["updated_at"] = utc_stamp()
    write_json_atomic(state_path, state)
    return state


def checkpoint_table(project_root: Path) -> list[dict[str, Any]]:
    study = load_study(project_root)
    migrate_legacy_checkpoint_angle_labels(project_root, study)
    meshes = {str(row["id"]): row for row in study["mesh_registry"]["meshes"]}
    rows: list[dict[str, Any]] = []
    for base_mesh_id in MESH_IDS:
      for alpha_deg in (8.0, 16.0):
        mesh_id = mesh_angle_id(study, base_mesh_id, alpha_deg)
        state = checkpoint_status(project_root, mesh_id)
        rows.append(
            {
                "mesh_id": mesh_id,
                "base_mesh_id": base_mesh_id,
                "topology": meshes[base_mesh_id]["topology"],
                "level": meshes[base_mesh_id]["level"],
                "alpha_deg": alpha_deg,
                "status": state["status"],
                "rans_state": state.get("rans_state"),
                "iterations": int(state.get("iterations_completed", 0) or 0),
                "gate": (state.get("gate") or {}).get("status", ""),
                "checkpoint_id": state.get("checkpoint_id", ""),
                "mesh_hash": str(state.get("mesh_hash", ""))[:12],
            }
        )
    return rows


def recover_premature_exit_metadata(
    project_root: Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Audit and optionally repair historical early-exit classifications.

    This operation only amends orchestration metadata. It never starts a
    solver, changes a field, removes a time directory, or promotes a result.
    """
    project_root = Path(project_root).resolve()
    active = active_workspace_root(project_root)
    rows: list[dict[str, Any]] = []
    changed = 0
    for mesh_id in MESH_IDS:
        checkpoint = _checkpoint_root(project_root, mesh_id)
        manifest_path = checkpoint / "checkpoint_manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        case = checkpoint / "case"
        if not manifest or not case.is_dir():
            rows.append({
                "mesh_id": mesh_id,
                "classification": "NO_ACTIVE_RANS_CASE",
                "changed": False,
            })
            continue
        try:
            accounting = authoritative_simple_iteration(case)
            absolute = int(accounting["absolute_simple_iteration"])
        except (FileNotFoundError, RuntimeError, ValueError, KeyError):
            absolute = int(
                manifest.get("absolute_simple_iteration")
                or manifest.get("iterations_completed")
                or 0
            )
            accounting = {"absolute_simple_iteration": absolute}
        start = int(manifest.get("block_start_iteration") or 0)
        target = int(
            manifest.get("block_target_iteration")
            or target_for_iteration(
                start,
                initial=RANS_INITIAL_TARGET,
                extension=RANS_EXTENSION_BLOCK,
                maximum=RANS_MAXIMUM_TARGET,
            )
        )
        staged = read_json(case / "staged_run_status.json", {}) or {}
        source = _simple_stop_request_source(
            case,
            staged,
            explicit_stop_requested=False,
        )
        old_reason = str(
            manifest.get("process_exit_reason")
            or manifest.get("stopped_reason")
            or ""
        )
        early = absolute < target
        native_stop = source == "OPENFOAM_SIMPLE_RESIDUAL_CONTROL"
        historical_misclassification = bool(
            early
            and native_stop
            and old_reason
            not in {
                "TIMEOUT_PARTIAL",
                "DIVERGED",
                "RUN_SETUP_FAILED",
                "SOLVER_ERROR",
                "ENVIRONMENT_ERROR",
                "ORCHESTRATION_ERROR",
            }
        )
        classification = (
            "PREMATURE_NORMAL_EXIT"
            if historical_misclassification
            else old_reason
            or ("TARGET_REACHED" if not early else "UNCLASSIFIED_EARLY_EXIT")
        )
        row = {
            "mesh_id": mesh_id,
            "absolute_simple_iteration": absolute,
            "block_start_iteration": start,
            "block_target_iteration": target,
            "minimum_simple_iterations_before_convergence_check": (
                MINIMUM_CONVERGENCE_ITERATION
            ),
            "previous_classification": old_reason or None,
            "classification": classification,
            "stop_request_source": source,
            "historical_misclassification": historical_misclassification,
            "changed": False,
        }
        if apply and historical_misclassification:
            manifest.update(
                status="RANS_PARTIAL",
                execution_status="PARTIAL",
                queue_state="PAUSED_PREMATURE_NORMAL_EXIT",
                process_exit_reason="PREMATURE_NORMAL_EXIT",
                stopped_reason="PREMATURE_NORMAL_EXIT",
                stop_request_source=source,
                absolute_simple_iteration=absolute,
                block_start_iteration=start,
                block_target_iteration=target,
                minimum_convergence_iteration=(
                    MINIMUM_CONVERGENCE_ITERATION
                ),
                remaining_to_target=max(0, target - absolute),
                data_preserved_for_resume=True,
                recovery_metadata_only=True,
                recovered_at=utc_stamp(),
                updated_at=utc_stamp(),
            )
            write_json_atomic(manifest_path, manifest)
            _register_base_execution(project_root, manifest, activate=False)
            row["changed"] = True
            changed += 1
        rows.append(row)
    queue_path = active / "rans_queue_state.json"
    queue = read_json(queue_path, {}) or {}
    current = str(queue.get("current_mesh_id") or "")
    current_row = next(
        (row for row in rows if row.get("mesh_id") == current),
        None,
    )
    if apply and current_row and current_row.get("changed"):
        queue.update(
            status="PAUSED_PREMATURE_NORMAL_EXIT",
            process_exit_reason="PREMATURE_NORMAL_EXIT",
            stop_request_source=current_row.get("stop_request_source"),
            resume_from_iteration=current_row.get(
                "absolute_simple_iteration"
            ),
            data_preserved_for_resume=True,
            updated_at=utc_stamp(),
        )
        write_json_atomic(queue_path, queue)
    report = {
        "schema_version": 1,
        "status": "APPLIED" if apply else "AUDIT_ONLY",
        "metadata_only": True,
        "solver_executed": False,
        "fields_modified": False,
        "changed_count": changed,
        "runs": rows,
        "generated_at": utc_stamp(),
    }
    write_json_atomic(active / "rans_premature_exit_recovery_audit.json", report)
    return report


def parse_args() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    status = sub.add_parser("status")
    status.add_argument("--mesh-id", choices=MESH_IDS)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    prepare.add_argument("--overwrite", action="store_true")
    execute = sub.add_parser("execute")
    execute.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    execute.add_argument("--overwrite", action="store_true")
    execute.add_argument("--allow-open-diagnostic", action="store_true")
    execute.add_argument("--manual-extension-iterations", type=int)
    execute.add_argument("--run", action="store_true")
    queue = sub.add_parser("queue")
    queue.add_argument("--continue-on-nonfatal-failure", action="store_true")
    queue.add_argument("--run", action="store_true")
    reviewed = sub.add_parser("create-reviewed-checkpoint")
    reviewed.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    delete = sub.add_parser("delete-active")
    delete.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    delete.add_argument("--confirm", action="store_true")
    delete.add_argument(
        "--archive",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    recovery = sub.add_parser("recovery-audit")
    recovery.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    try:
        if args.action == "status":
            result = (
                checkpoint_status(root, args.mesh_id)
                if args.mesh_id
                else checkpoint_table(root)
            )
        elif args.action == "prepare":
            result = prepare_base(root, args.mesh_id, overwrite=args.overwrite)
        elif args.action == "execute":
            result = execute_base(
                root,
                args.mesh_id,
                run=args.run,
                overwrite=args.overwrite,
                allow_open_diagnostic=args.allow_open_diagnostic,
                manual_extension_iterations=args.manual_extension_iterations,
            )
        elif args.action == "queue":
            result = execute_queue(
                root,
                run=args.run,
                continue_on_nonfatal_failure=args.continue_on_nonfatal_failure,
            )
        elif args.action == "create-reviewed-checkpoint":
            result = create_reviewed_checkpoint(root, args.mesh_id)
        elif args.action == "delete-active":
            result = delete_active_base(
                root,
                args.mesh_id,
                confirm=args.confirm,
                archive=args.archive,
            )
        elif args.action == "recovery-audit":
            result = recover_premature_exit_metadata(root, apply=args.apply)
        else:  # pragma: no cover
            raise AssertionError(args.action)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not isinstance(result, dict) or result.get("status") not in {
            "RANS_BASE_FAILED",
            "RANS_BASE_DIVERGED",
        } else 2
    except RansCheckpointBlocked as exc:
        print(json.dumps(exc.payload, indent=2, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "RANS_BASE_FAILED",
                    "message": str(exc),
                    "remediation_actions": ["Ver diagnostico"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

