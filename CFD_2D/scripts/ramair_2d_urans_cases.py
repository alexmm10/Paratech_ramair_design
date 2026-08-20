#!/usr/bin/env python3
"""Canonical URANS case identity, presence and lifecycle services.

The Validation Lab owns exactly one mutable production timeline for each
topology/mesh/angle/time-step key.  This module deliberately contains no
pilot, attempt, version or archive concepts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    utc_stamp,
    write_json_atomic,
)


CANONICAL_CASE_SCHEMA_VERSION = 1
RUNTIME_SCHEMA_VERSION = 1
REQUIRED_RESTART_FIELDS = ("U", "p", "nuTilda")


class CasePresence(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    STARTED = "STARTED"


class ExecutionOutcome(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    DIVERGED = "DIVERGED"
    ERROR = "ERROR"


class CanonicalCaseError(RuntimeError):
    """Lifecycle error with a stable reason and remediation."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        remediation: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.remediation = remediation
        self.evidence = evidence or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "message": str(self),
            "remediation": self.remediation,
            "evidence": self.evidence,
        }


def _finite_positive(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError("deltaT must be finite and positive")
    return result


def _decimal_token(value: float) -> str:
    """Serialize a positive float without losing supported deltaT precision."""
    text = f"{_finite_positive(value):.12e}"
    mantissa, exponent = text.split("e")
    mantissa = mantissa.rstrip("0").rstrip(".").replace(".", "p")
    exp_value = int(exponent)
    exp_token = f"m{abs(exp_value):02d}" if exp_value < 0 else f"p{exp_value:02d}"
    return f"{mantissa}e{exp_token}"


def _alpha_token(alpha_deg: float) -> str:
    value = float(alpha_deg)
    if not math.isfinite(value):
        raise ValueError("alpha_deg must be finite")
    sign = "m" if value < 0 else ""
    magnitude = f"{abs(value):.6f}".rstrip("0").rstrip(".").replace(".", "p")
    if "p" not in magnitude and len(magnitude) < 2:
        magnitude = magnitude.zfill(2)
    return sign + magnitude


def canonical_case_id(
    topology: str,
    mesh_level: str,
    alpha_deg: float,
    delta_t_s: float,
) -> str:
    topology = str(topology).strip().lower()
    mesh_level = str(mesh_level).strip().lower()
    if topology not in {"closed", "open"}:
        raise ValueError("topology must be closed or open")
    if mesh_level not in {"coarse", "medium", "fine"}:
        raise ValueError("mesh_level must be coarse, medium or fine")
    return (
        f"{topology}_{mesh_level}_a{_alpha_token(alpha_deg)}_"
        f"dt{_decimal_token(delta_t_s)}"
    )


def scientific_key(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "topology": str(row["topology"]).lower(),
        "mesh_id": str(row["mesh_id"]),
        "mesh_level": str(row["mesh_level"]).lower(),
        "alpha_deg": float(row.get("alpha_deg", 8.0)),
        "deltaT_s": _finite_positive(row.get("dt_s", row.get("deltaT_s"))),
    }


def case_id_from_row(row: dict[str, Any]) -> str:
    key = scientific_key(row)
    return canonical_case_id(
        key["topology"],
        key["mesh_level"],
        key["alpha_deg"],
        key["deltaT_s"],
    )


def display_identity(row: dict[str, Any]) -> str:
    key = scientific_key(row)
    return (
        f"{key['topology'].title()} | {key['mesh_level'].title()} | "
        f"alpha={key['alpha_deg']:g} deg | deltaT={key['deltaT_s']:.6g} s"
    )


def canonical_case_root(project_root: Path, row: dict[str, Any]) -> Path:
    key = scientific_key(row)
    return (
        active_workspace_root(Path(project_root).resolve())
        / "runs"
        / key["topology"]
        / key["mesh_level"]
        / case_id_from_row(row)
    )


def canonical_case_path(project_root: Path, row: dict[str, Any]) -> Path:
    return canonical_case_root(project_root, row) / "case"


def _field_exists(time_dir: Path, field: str) -> bool:
    return (time_dir / field).is_file() or (time_dir / f"{field}.gz").is_file()


def _time_directories(root: Path) -> dict[float, Path]:
    result: dict[float, Path] = {}
    if not root.is_dir():
        return result
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if math.isfinite(value) and value > 0.0:
            result[value] = path
    return result


def restart_time_evidence(
    case: Path,
    *,
    required_fields: Iterable[str] = REQUIRED_RESTART_FIELDS,
) -> dict[str, Any]:
    """Find the latest complete direct or common decomposed restart time."""
    case = Path(case).resolve()
    fields = tuple(str(value) for value in required_fields)
    direct = _time_directories(case)
    direct_valid = {
        value: path
        for value, path in direct.items()
        if all(_field_exists(path, field) for field in fields)
    }
    processors = sorted(
        path for path in case.glob("processor[0-9]*") if path.is_dir()
    )
    common: set[float] | None = None
    per_processor: dict[str, list[float]] = {}
    for processor in processors:
        valid = {
            value
            for value, path in _time_directories(processor).items()
            if all(_field_exists(path, field) for field in fields)
        }
        per_processor[processor.name] = sorted(valid)
        common = valid if common is None else common & valid
    latest_direct = max(direct_valid, default=None)
    latest_common = max(common or set(), default=None)
    candidates = [value for value in (latest_direct, latest_common) if value]
    latest = max(candidates, default=None)
    source = (
        "direct"
        if latest is not None and latest == latest_direct
        else "processor_common"
        if latest is not None
        else None
    )
    return {
        "valid": latest is not None,
        "time_s": latest,
        "source": source,
        "required_fields": list(fields),
        "latest_direct_time_s": latest_direct,
        "latest_common_processor_time_s": latest_common,
        "processor_count": len(processors),
        "processor_times": per_processor,
    }


def complete_time_history(
    case: Path,
    *,
    required_fields: Iterable[str] = REQUIRED_RESTART_FIELDS,
) -> dict[str, Any]:
    """Return complete direct and common decomposed time histories."""
    case = Path(case).resolve()
    fields = tuple(str(value) for value in required_fields)
    direct = sorted(
        value
        for value, path in _time_directories(case).items()
        if all(_field_exists(path, field) for field in fields)
    )
    processors = sorted(
        path for path in case.glob("processor[0-9]*") if path.is_dir()
    )
    common: set[float] | None = None
    for processor in processors:
        valid = {
            value
            for value, path in _time_directories(processor).items()
            if all(_field_exists(path, field) for field in fields)
        }
        common = valid if common is None else common & valid
    common_values = sorted(common or set())
    selected = direct if direct else common_values
    return {
        "valid": bool(selected),
        "source": "direct" if direct else "processor_common" if selected else None,
        "times_s": selected,
        "direct_times_s": direct,
        "common_processor_times_s": common_values,
        "processor_count": len(processors),
        "required_fields": list(fields),
    }


def solver_started_evidence(case_root: Path) -> dict[str, Any]:
    case_root = Path(case_root).resolve()
    case = case_root / "case"
    status = read_json(case / "run_status.json", {}) or {}
    journal = read_json(case_root / "stage_journal.json", {}) or {}
    solver_rows = [
        row for row in journal.get("phases", [])
        if bool(row.get("solver_started"))
    ]
    logs = [
        path for path in (
            case / "log.foamRun",
            case / "PyFoamRunner.foamRun.logfile",
        )
        if path.is_file() and path.stat().st_size > 0
    ]
    status_started = str(status.get("status") or "").upper() in {
        "RUNNING",
        "RUN_COMPLETED",
        "RUN_FAILED",
        "RUN_DIVERGED",
        "TIMEOUT_PARTIAL",
        "STOPPED_PARTIAL",
        "CONVERGED_STATISTICALLY",
    }
    return {
        "solver_started": bool(status_started or solver_rows or logs),
        "run_status": str(status.get("status") or ""),
        "journal_solver_phases": [str(row.get("phase")) for row in solver_rows],
        "nonempty_logs": [str(path) for path in logs],
    }


def compatibility_hashes(
    *,
    mesh_hash: str,
    physics: dict[str, Any],
    solver_config: dict[str, Any],
) -> dict[str, str]:
    def digest(payload: Any) -> str:
        data = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    return {
        "mesh_hash": str(mesh_hash),
        "physics_hash": digest(physics),
        "solver_config_hash": digest(solver_config),
    }


def _outcome_from_status(
    raw_status: str,
    *,
    started: bool,
    restartable: bool,
) -> ExecutionOutcome:
    status = raw_status.upper()
    if status in {"RUNNING", "PREPARING"}:
        return ExecutionOutcome.RUNNING
    if status in {"COMPLETED", "ANALYSIS_PENDING", "ACCEPTED"}:
        return ExecutionOutcome.COMPLETED
    if "DIVERG" in status or "NONFINITE" in status:
        return ExecutionOutcome.DIVERGED
    if status in {
        "STOPPED_PARTIAL",
        "TIMEOUT_PARTIAL",
        "PAUSED",
        "INTERRUPTED",
    } or (started and restartable):
        return ExecutionOutcome.PAUSED
    if status in {"", "READY", "NOT_RUN", "NOT_STARTED"} and not started:
        return ExecutionOutcome.READY
    return ExecutionOutcome.ERROR


def inspect_canonical_case(
    project_root: Path,
    row: dict[str, Any],
    *,
    expected_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = canonical_case_root(project_root, row)
    case = root / "case"
    manifest = read_json(root / "case_manifest.json", {}) or {}
    restart = restart_time_evidence(case)
    solver = solver_started_evidence(root)
    started = bool(restart["valid"] and solver["solver_started"])
    configured_hashes = {
        name: manifest.get(name)
        for name in ("mesh_hash", "physics_hash", "solver_config_hash")
    }
    mismatches = {
        name: {"stored": configured_hashes.get(name), "expected": value}
        for name, value in (expected_hashes or {}).items()
        if configured_hashes.get(name) not in {None, value}
    }
    raw = str(
        manifest.get("execution_outcome")
        or (read_json(case / "run_status.json", {}) or {}).get("status")
        or "READY"
    )
    outcome = _outcome_from_status(
        raw,
        started=started,
        restartable=bool(restart["valid"] and not mismatches),
    )
    if mismatches and started:
        outcome = ExecutionOutcome.ERROR
    result = {
        "case_id": case_id_from_row(row),
        "scientific_key": scientific_key(row),
        "display_identity": display_identity(row),
        "case_root": str(root),
        "case_path": str(case),
        "case_presence": (
            CasePresence.STARTED.value if started else CasePresence.NOT_STARTED.value
        ),
        "execution_outcome": outcome.value,
        "restartable": bool(started and restart["valid"] and not mismatches),
        "current_time_s": restart.get("time_s"),
        "current_phase": manifest.get("current_phase"),
        "target_end_time_s": manifest.get("target_end_time_s"),
        "solver_started": solver["solver_started"],
        "restart_evidence": restart,
        "solver_evidence": solver,
        "configuration_compatible": not mismatches,
        "configuration_mismatches": mismatches,
        "terminal_reason": manifest.get("terminal_reason"),
        "primary_error": manifest.get("primary_error"),
        "secondary_errors": list(manifest.get("secondary_errors") or []),
        "manifest": manifest,
    }
    result["calculated_action"] = calculated_action(result)
    return result


def calculated_action(state: dict[str, Any]) -> str:
    if not state.get("configuration_compatible", True):
        return "RESTART_REQUIRED_INCOMPATIBLE_CONFIGURATION"
    if state.get("case_presence") == CasePresence.NOT_STARTED.value:
        return "START_FROM_RANS"
    outcome = str(state.get("execution_outcome") or "")
    if outcome == ExecutionOutcome.PAUSED.value and state.get("restartable"):
        return "RESUME"
    if outcome == ExecutionOutcome.COMPLETED.value:
        return "REVIEW"
    return "RESTART_REQUIRED"


def write_case_manifest(
    case_root: Path,
    row: dict[str, Any],
    *,
    hashes: dict[str, str],
    effective_solver_config: dict[str, Any],
    startup_mode: str,
    outcome: str = ExecutionOutcome.READY.value,
    **updates: Any,
) -> dict[str, Any]:
    case_root = Path(case_root).resolve()
    previous = read_json(case_root / "case_manifest.json", {}) or {}
    now = utc_stamp()
    payload = {
        **previous,
        "schema_version": CANONICAL_CASE_SCHEMA_VERSION,
        "case_id": case_id_from_row(row),
        "scientific_key": scientific_key(row),
        "display_identity": display_identity(row),
        "case_path": str(case_root / "case"),
        "mesh_id": str(row["mesh_id"]),
        **hashes,
        "alpha_deg": float(row.get("alpha_deg", 8.0)),
        "deltaT_s": float(row.get("dt_s", row.get("deltaT_s"))),
        "startup_mode": str(startup_mode),
        "effective_solver_config": effective_solver_config,
        "case_presence": previous.get("case_presence", CasePresence.NOT_STARTED.value),
        "execution_outcome": str(outcome),
        "restartable": bool(previous.get("restartable", False)),
        "current_phase": previous.get("current_phase"),
        "current_time_s": previous.get("current_time_s"),
        "target_end_time_s": previous.get("target_end_time_s"),
        "solver_started": bool(previous.get("solver_started", False)),
        "created_at": previous.get("created_at", now),
        "updated_at": now,
        "terminal_reason": previous.get("terminal_reason"),
        "primary_error": previous.get("primary_error"),
        "secondary_errors": list(previous.get("secondary_errors") or []),
        **updates,
    }
    write_json_atomic(case_root / "case_manifest.json", payload)
    return payload


def _assert_child(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    if resolved == parent_resolved or parent_resolved not in resolved.parents:
        raise CanonicalCaseError(
            "UNSAFE_CASE_PATH",
            f"Refusing lifecycle operation outside canonical runs root: {resolved}",
            remediation="Select the case again from the Validation Lab registry.",
        )
    if path.is_symlink() or any(part.is_symlink() for part in [path, *path.parents] if part.exists() and part != parent_resolved):
        raise CanonicalCaseError(
            "SYMLINK_CASE_PATH_REJECTED",
            f"Canonical case path contains a symlink: {path}",
            remediation="Use the real Validation Lab case directory.",
        )
    return resolved


def restart_canonical_case(
    project_root: Path,
    row: dict[str, Any],
    *,
    confirm_delete: str,
) -> dict[str, Any]:
    case_id = case_id_from_row(row)
    if str(confirm_delete) != case_id:
        raise CanonicalCaseError(
            "DELETE_CONFIRMATION_MISMATCH",
            "The restart confirmation must equal the exact canonical case ID.",
            remediation=f"Enter {case_id} to confirm this one case.",
        )
    active = active_workspace_root(Path(project_root).resolve())
    runs_root = active / "runs"
    root = canonical_case_root(project_root, row)
    _assert_child(root, runs_root)
    runtime = read_json(active / "runtime/active_execution.json", {}) or {}
    if str(runtime.get("case_id") or "") == case_id and str(runtime.get("status") or "") in {"PREPARING", "RUNNING"}:
        raise CanonicalCaseError(
            "ACTIVE_CASE_DELETE_REJECTED",
            f"{case_id} has an active runtime record.",
            remediation="Stop the solver and wait for its terminal state before restarting.",
            evidence=runtime,
        )
    removed_bytes = directory_size(root)
    existed = root.exists()
    if existed:
        shutil.rmtree(root)
    report = {
        "schema_version": 1,
        "operation": "CANONICAL_CASE_RESTART_DELETE",
        "case_id": case_id,
        "deleted_path": str(root),
        "deleted": existed,
        "bytes_removed": removed_bytes,
        "preserved": ["meshes", "RANS checkpoints", "shared configuration", "Results"],
        "generated_at": utc_stamp(),
    }
    reports = active / "reports/deletions"
    reports.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        reports / f"{case_id}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json",
        report,
    )
    return report


def directory_size(path: Path) -> int:
    if not Path(path).exists():
        return 0
    total = 0
    for item in Path(path).rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def process_start_token(pid: int) -> str | None:
    if pid <= 0:
        return None
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
        return fields[21] if len(fields) > 21 else None
    except OSError:
        return None


def process_identity_is_live(pid: Any, token: Any) -> bool:
    """Check PID and Linux process-start token together to reject PID reuse."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    current = process_start_token(value)
    return current is not None and str(current) == str(token or "")


def runtime_paths(project_root: Path) -> dict[str, Path]:
    root = active_workspace_root(Path(project_root).resolve()) / "runtime"
    return {
        "root": root,
        "active": root / "active_execution.json",
        "latest": root / "latest_execution.json",
        "lease": root / "solver_lease.json",
    }


def publish_runtime(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    paths = runtime_paths(project_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    now = utc_stamp()
    current = read_json(paths["active"], {}) or {}
    merged = {
        **current,
        "schema_version": RUNTIME_SCHEMA_VERSION,
        **payload,
        "updated_at": now,
    }
    write_json_atomic(paths["active"], merged)
    if str(merged.get("status") or "") not in {"PREPARING", "RUNNING"}:
        write_json_atomic(paths["latest"], merged)
    return merged


def acquire_solver_lease(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Acquire the single Validation Lab solver lease atomically."""
    paths = runtime_paths(project_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    existing = read_json(paths["lease"], {}) or {}
    if existing and process_identity_is_live(
        existing.get("PID"), existing.get("process_start_token")
    ):
        raise CanonicalCaseError(
            "SOLVER_LEASE_BUSY",
            "Another Validation Lab solver process owns the execution lease.",
            remediation="Wait for the active case to finish or stop it explicitly.",
            evidence=existing,
        )
    if existing:
        paths["lease"].unlink(missing_ok=True)
    lease = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        **payload,
        "acquired_at": utc_stamp(),
        "updated_at": utc_stamp(),
    }
    temporary = paths["lease"].with_name(
        f".{paths['lease'].name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    temporary.write_text(
        json.dumps(lease, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.link(temporary, paths["lease"])
    except FileExistsError:
        current = read_json(paths["lease"], {}) or {}
        if process_identity_is_live(
            current.get("PID"), current.get("process_start_token")
        ):
            raise CanonicalCaseError(
                "SOLVER_LEASE_BUSY",
                "Another Validation Lab solver process acquired the lease.",
                remediation="Wait for the active case to finish or stop it explicitly.",
                evidence=current,
            )
        paths["lease"].unlink(missing_ok=True)
        os.replace(temporary, paths["lease"])
    finally:
        temporary.unlink(missing_ok=True)
    return lease


def release_solver_lease(project_root: Path, lease_id: str) -> None:
    paths = runtime_paths(project_root)
    current = read_json(paths["lease"], {}) or {}
    if current and str(current.get("lease_id") or "") != str(lease_id):
        raise CanonicalCaseError(
            "SOLVER_LEASE_OWNERSHIP_MISMATCH",
            "The current process does not own the Validation Lab solver lease.",
            remediation="Do not remove a lease owned by another active execution.",
            evidence=current,
        )
    paths["lease"].unlink(missing_ok=True)


def clear_active_runtime(project_root: Path, terminal: dict[str, Any]) -> None:
    paths = runtime_paths(project_root)
    payload = {**terminal, "updated_at": utc_stamp()}
    write_json_atomic(paths["latest"], payload)
    paths["active"].unlink(missing_ok=True)


def quick_check_paths(project_root: Path) -> dict[str, Path]:
    root = active_workspace_root(Path(project_root).resolve()) / "quick_check"
    return {
        "root": root,
        "report": root / "latest_quick_check_report.json",
        "log": root / "latest_quick_check.log",
    }


def create_quick_check_sandbox(project_root: Path, case_id: str) -> Path:
    paths = quick_check_paths(project_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{case_id}_", dir=paths["root"]))


def finalize_quick_check(
    project_root: Path,
    sandbox: Path,
    report: dict[str, Any],
    log_text: str,
) -> dict[str, Any]:
    paths = quick_check_paths(project_root)
    allowed = {
        "QUICK_CHECK_STABLE_START",
        "QUICK_CHECK_DIVERGED",
        "QUICK_CHECK_ERROR",
        "QUICK_CHECK_STOPPED",
    }
    status = str(report.get("status") or "")
    if status not in allowed:
        raise ValueError(f"Unsupported quick-check status: {status}")
    payload = {
        **report,
        "schema_version": 1,
        "production_gate": "NOT_APPLICABLE",
        "generated_at": utc_stamp(),
    }
    write_json_atomic(paths["report"], payload)
    bounded = log_text[-250_000:]
    paths["log"].write_text(bounded, encoding="utf-8")
    sandbox = Path(sandbox).resolve()
    _assert_child(sandbox, paths["root"])
    shutil.rmtree(sandbox, ignore_errors=False)
    return payload


def reconcile_quick_check_sandboxes(project_root: Path) -> list[str]:
    paths = quick_check_paths(project_root)
    removed: list[str] = []
    if not paths["root"].is_dir():
        return removed
    retained = {paths["report"].name, paths["log"].name}
    for child in paths["root"].iterdir():
        if child.name in retained or not child.is_dir():
            continue
        runtime = read_json(child / "runtime.json", {}) or {}
        pid = int(runtime.get("PID") or 0)
        token = str(runtime.get("process_start_token") or "")
        alive = pid > 0 and process_start_token(pid) == token
        if alive:
            continue
        _assert_child(child, paths["root"])
        shutil.rmtree(child)
        removed.append(str(child))
    return removed
