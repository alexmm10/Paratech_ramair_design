#!/usr/bin/env python3
"""Durable process identity and restart evidence for RamAir OpenFOAM runs."""
from __future__ import annotations

import json
import hashlib
import os
import signal
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


PROCESS_RECORD = ".ramair_solver_process.json"
STOP_REQUEST = ".ramair_stop_request.json"
EXECUTION_STATE_RECORD = ".ramair_execution_state.json"
_JSON_WRITE_LOCK = threading.RLock()


class ExecutionState(str, Enum):
    """Canonical lifecycle shared by UI, queues, solvers and post-processing."""

    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    PAUSED_RECOVERABLE = "PAUSED_RECOVERABLE"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


_STATE_ALIASES = {
    "READY": ExecutionState.PREPARED,
    "PREPARING": ExecutionState.PREPARED,
    "QUEUED": ExecutionState.PREPARED,
    "NOT_STARTED": ExecutionState.PREPARED,
    "RUNNING_RANS": ExecutionState.RUNNING,
    "RUNNING_URANS": ExecutionState.RUNNING,
    "POSTPROCESSING": ExecutionState.RUNNING,
    "STOP_REQUESTED": ExecutionState.RUNNING,
    "STOPPING": ExecutionState.RUNNING,
    "PAUSED": ExecutionState.PAUSED_RECOVERABLE,
    "PAUSED_RESTARTABLE": ExecutionState.PAUSED_RECOVERABLE,
    "STOPPED_PARTIAL": ExecutionState.PAUSED_RECOVERABLE,
    "STOPPED_FORCED_PARTIAL": ExecutionState.PAUSED_RECOVERABLE,
    "TIMEOUT_PARTIAL": ExecutionState.PAUSED_RECOVERABLE,
    "INTERRUPTED": ExecutionState.PAUSED_RECOVERABLE,
    "CONVERGED_STATISTICALLY": ExecutionState.REVIEW_REQUIRED,
    "ANALYSIS_PENDING": ExecutionState.REVIEW_REQUIRED,
    "RANS_COMPLETED": ExecutionState.REVIEW_REQUIRED,
    "URANS_COMPLETED": ExecutionState.REVIEW_REQUIRED,
    "POSTPROCESSED": ExecutionState.REVIEW_REQUIRED,
    "AWAITING_USER_DECISION": ExecutionState.REVIEW_REQUIRED,
    "ERROR": ExecutionState.FAILED,
    "DIVERGED": ExecutionState.FAILED,
    "SOLVER_DIVERGED": ExecutionState.FAILED,
    "RUN_DIVERGED": ExecutionState.FAILED,
    "PREPARATION_FAILED": ExecutionState.FAILED,
    "RUN_SETUP_FAILED": ExecutionState.FAILED,
    "RUN_COMMAND_FAILED": ExecutionState.FAILED,
    "SOLVER_FAILED": ExecutionState.FAILED,
    "STOPPED_INCOMPLETE": ExecutionState.FAILED,
    "UNKNOWN_FINISHED": ExecutionState.FAILED,
}

_ALLOWED_TRANSITIONS = {
    None: set(ExecutionState),
    ExecutionState.PREPARED: {ExecutionState.RUNNING, ExecutionState.FAILED, ExecutionState.REJECTED},
    ExecutionState.RUNNING: {
        ExecutionState.PAUSED_RECOVERABLE,
        ExecutionState.FAILED,
        ExecutionState.COMPLETED,
        ExecutionState.REVIEW_REQUIRED,
    },
    ExecutionState.PAUSED_RECOVERABLE: {ExecutionState.RUNNING, ExecutionState.FAILED, ExecutionState.REJECTED},
    ExecutionState.FAILED: {ExecutionState.PREPARED, ExecutionState.RUNNING, ExecutionState.REJECTED},
    # A staged URANS timeline completes one solver subprocess per phase.  The
    # next phase has a different idempotency key and legitimately returns the
    # same canonical case to RUNNING; final-case replay protection is enforced
    # by the canonical manifest/action gate, not this segment lifecycle.
    ExecutionState.COMPLETED: {
        ExecutionState.RUNNING,
        ExecutionState.REVIEW_REQUIRED,
        ExecutionState.APPROVED,
        ExecutionState.REJECTED,
    },
    ExecutionState.REVIEW_REQUIRED: {ExecutionState.APPROVED, ExecutionState.REJECTED, ExecutionState.RUNNING},
    ExecutionState.APPROVED: {ExecutionState.REJECTED},
    ExecutionState.REJECTED: {ExecutionState.PREPARED, ExecutionState.RUNNING},
}


def normalize_execution_state(
    value: str | ExecutionState | None,
    *,
    restartable: bool | None = None,
) -> ExecutionState:
    """Map all legacy spellings to the eight-state public contract."""
    if isinstance(value, ExecutionState):
        return value
    normalized = str(value or "PREPARED").strip().upper()
    try:
        return ExecutionState(normalized)
    except ValueError:
        if normalized in _STATE_ALIASES:
            state = _STATE_ALIASES[normalized]
            if state == ExecutionState.PAUSED_RECOVERABLE and restartable is False:
                return ExecutionState.FAILED
            return state
        if any(token in normalized for token in ("FAIL", "ERROR", "DIVERG", "MISSING")):
            return ExecutionState.FAILED
        if "APPROV" in normalized:
            return ExecutionState.APPROVED
        if "REJECT" in normalized:
            return ExecutionState.REJECTED
        if any(token in normalized for token in ("PAUS", "STOP", "TIMEOUT", "INTERRUPT")):
            return ExecutionState.PAUSED_RECOVERABLE if restartable is not False else ExecutionState.FAILED
        if any(token in normalized for token in ("REVIEW", "AWAIT", "ANALYSIS", "POSTPROCESS")):
            return ExecutionState.REVIEW_REQUIRED
        if "COMPLETE" in normalized or "FINISHED" in normalized:
            return ExecutionState.COMPLETED
        if "RUNNING" in normalized:
            return ExecutionState.RUNNING
        if any(token in normalized for token in ("READY", "PREPAR", "QUEUE")):
            return ExecutionState.PREPARED
        raise ValueError(f"Unsupported execution state: {value!r}")


def execution_idempotency_key(
    case_dir: Path,
    command: Iterable[object] | None = None,
    *,
    phase: str | None = None,
) -> str:
    material = json.dumps(
        {
            "case_dir": str(Path(case_dir).resolve()),
            "command": [str(value) for value in command or ()],
            "phase": str(phase or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_execution_state(case_dir: Path) -> dict[str, Any]:
    return read_json(Path(case_dir).resolve() / EXECUTION_STATE_RECORD, {}) or {}


def transition_execution_state(
    case_dir: Path,
    state: str | ExecutionState,
    *,
    phase: str | None = None,
    run_id: str | None = None,
    idempotency_key: str | None = None,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
    pid: int | None = None,
    returncode: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Persist one legal transition atomically and suppress duplicate launches."""
    case = Path(case_dir).resolve()
    path = case / EXECUTION_STATE_RECORD
    with _JSON_WRITE_LOCK:
        previous = read_json(path, {}) or {}
        previous_state = (
            normalize_execution_state(previous.get("state"), restartable=previous.get("restartable"))
            if previous
            else None
        )
        requested = normalize_execution_state(state)
        key = str(idempotency_key or previous.get("idempotency_key") or "") or None
        if (
            requested == ExecutionState.RUNNING
            and previous_state == ExecutionState.RUNNING
            and key
            and key == previous.get("idempotency_key")
        ):
            return {**previous, "duplicate_suppressed": True}
        if not force and requested != previous_state and requested not in _ALLOWED_TRANSITIONS[previous_state]:
            raise RuntimeError(
                f"Illegal execution transition {previous_state.value if previous_state else 'NONE'} -> {requested.value}"
            )
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        event = {
            "sequence": int(previous.get("sequence") or 0) + 1,
            "from": previous_state.value if previous_state else None,
            "to": requested.value,
            "phase": phase if phase is not None else previous.get("phase"),
            "reason": reason,
            "at": now,
            "returncode": returncode,
        }
        history = [*list(previous.get("history") or []), event][-256:]
        payload = {
            **previous,
            "schema_version": 1,
            "case_dir": str(case),
            "run_id": run_id or previous.get("run_id") or uuid.uuid4().hex,
            "state": requested.value,
            "phase": event["phase"],
            "sequence": event["sequence"],
            "idempotency_key": key,
            "pid": int(pid) if pid else previous.get("pid"),
            "pid_start_token": process_start_token(int(pid)) if pid else previous.get("pid_start_token"),
            "returncode": returncode,
            "reason": reason,
            "evidence": dict(evidence or previous.get("evidence") or {}),
            "history": history,
            "updated_at": now,
            "updated_unix": time.time(),
            "duplicate_suppressed": False,
        }
        if requested == ExecutionState.RUNNING:
            payload["started_at"] = previous.get("started_at") or now
            payload.pop("finished_at", None)
        elif requested in {
            ExecutionState.PAUSED_RECOVERABLE,
            ExecutionState.FAILED,
            ExecutionState.COMPLETED,
            ExecutionState.REVIEW_REQUIRED,
            ExecutionState.APPROVED,
            ExecutionState.REJECTED,
        }:
            payload["finished_at"] = now
        write_json_atomic(path, payload)
        return payload


def write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    with _JSON_WRITE_LOCK:
        # Workflow payloads can contain Path/numpy scalar values because the
        # UI builds them from filesystem selections and numerical widgets.
        # Keep the atomic writer the single serialization boundary so every
        # caller gets the same portable JSON representation.
        temporary.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")
        for attempt in range(20):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                # Windows virus scanners and a simultaneous Streamlit read can
                # briefly hold the destination open. Keep the same complete
                # temporary file and retry; never expose a partial JSON file.
                time.sleep(0.01 * (attempt + 1))
    return path


def _json_default(value: Any) -> Any:
    """Serialize filesystem and numeric scalar values used by workflow state."""
    if isinstance(value, Path):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def process_start_token(pid: int) -> str | None:
    """Return Linux /proc start ticks so recycled PIDs are never signalled."""
    if os.name == "nt" or int(pid) <= 0:
        return None
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        remainder = raw[raw.rfind(")") + 2 :].split()
        return remainder[19]
    except (OSError, IndexError, ValueError):
        return None


def process_group_id(pid: int) -> int | None:
    if os.name == "nt" or int(pid) <= 0:
        return None
    try:
        return int(os.getpgid(int(pid)))
    except (OSError, ProcessLookupError, ValueError):
        return None


def pid_is_alive(pid: int | None, start_token: str | None = None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ProcessLookupError, ValueError):
        return False
    if start_token and os.name != "nt":
        return process_start_token(int(pid)) == str(start_token)
    return True


def publish_solver_process(
    case_dir: Path,
    *,
    status: str,
    pid: int | None = None,
    command: Iterable[object] | None = None,
    outcome: str | None = None,
    returncode: int | None = None,
) -> Path:
    case_dir = Path(case_dir).resolve()
    path = case_dir / PROCESS_RECORD
    previous = read_json(path, {}) or {}
    effective_pid = int(pid) if pid else previous.get("pid")
    payload = {
        **previous,
        "schema_version": 1,
        "case_dir": str(case_dir),
        "status": str(status),
        "pid": effective_pid,
        "process_group_id": (
            process_group_id(int(effective_pid)) if effective_pid else previous.get("process_group_id")
        ),
        "pid_start_token": (
            process_start_token(int(effective_pid)) if effective_pid else previous.get("pid_start_token")
        ),
        "command": [str(value) for value in command] if command is not None else previous.get("command"),
        "outcome": outcome,
        "returncode": returncode,
        "updated_unix": time.time(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if status == "RUNNING" and not previous.get("started_at"):
        payload["started_at"] = payload["updated_at"]
    if status != "RUNNING":
        payload["finished_at"] = payload["updated_at"]
    written = write_json_atomic(path, payload)
    try:
        canonical = normalize_execution_state(status)
        transition_execution_state(
            case_dir,
            canonical,
            phase=str(previous.get("phase") or "SOLVER"),
            idempotency_key=execution_idempotency_key(case_dir, command or payload.get("command")),
            reason=outcome or str(status),
            pid=effective_pid,
            returncode=returncode,
            evidence=restart_evidence(case_dir) if canonical != ExecutionState.RUNNING else {},
            force=canonical == ExecutionState.RUNNING and bool(load_execution_state(case_dir)),
        )
    except (OSError, RuntimeError, ValueError):
        # The legacy process record remains authoritative for older packages.
        # Lifecycle publication must never mask the real solver outcome.
        pass
    return written


def load_solver_process(case_dir: Path) -> dict[str, Any]:
    return read_json(Path(case_dir).resolve() / PROCESS_RECORD, {}) or {}


def discover_live_solver_cases(project_root: Path) -> list[dict[str, Any]]:
    """Find live OpenFOAM cases even when their Python owner has disappeared.

    Process records are authoritative when their PID identity still matches.
    The Linux ``/proc`` fallback recovers MPI/foamRun children left behind by a
    crashed UI worker, while only accepting working directories below the
    requested project root.
    """
    root = Path(project_root).resolve()
    discovered: dict[Path, dict[str, Any]] = {}
    for record_path in root.rglob(PROCESS_RECORD):
        record = read_json(record_path, {}) or {}
        case_dir = record_path.parent.resolve()
        pid = int(record.get("pid") or 0)
        token = str(record.get("pid_start_token") or "") or None
        if pid_is_alive(pid, token):
            discovered[case_dir] = {
                **record,
                "case_dir": str(case_dir),
                "pid": pid,
                "live": True,
                "discovery_source": "solver_process_record",
            }
    if os.name != "nt" and Path("/proc").is_dir():
        command_markers = ("foamrun", "simplefoam", "pimplefoam", "mpirun", "mpiexec")
        for process_dir in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(process_dir.name)
                command = (process_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", errors="replace"
                ).lower()
                if not any(marker in command for marker in command_markers):
                    continue
                cwd = (process_dir / "cwd").resolve()
                cwd.relative_to(root)
            except (OSError, ValueError):
                continue
            case_dir = cwd
            while case_dir != root and not (case_dir / "system/controlDict").is_file():
                case_dir = case_dir.parent
            if not (case_dir / "system/controlDict").is_file():
                continue
            existing = discovered.get(case_dir, {})
            existing_pid = int(existing.get("pid") or 0)
            # Prefer the recorded owner. For an orphan, retain the smallest PID
            # in the process group so a later forced stop targets the full tree.
            if existing_pid and existing.get("discovery_source") == "solver_process_record":
                continue
            chosen_pid = min(value for value in (existing_pid, pid) if value > 0)
            discovered[case_dir] = {
                **existing,
                "case_dir": str(case_dir),
                "pid": chosen_pid,
                "process_group_id": process_group_id(chosen_pid),
                "pid_start_token": process_start_token(chosen_pid),
                "status": "RUNNING",
                "live": True,
                "discovery_source": "linux_proc_openfoam_child",
                "command": command.strip(),
                "updated_unix": time.time(),
            }
    return sorted(
        discovered.values(),
        key=lambda row: float(row.get("updated_unix") or 0.0),
        reverse=True,
    )


def signal_solver_process(case_dir: Path, sig: int = signal.SIGINT) -> dict[str, Any]:
    """Signal the recorded solver group only when PID identity still matches."""
    record = load_solver_process(case_dir)
    pid = int(record.get("pid") or 0)
    token = str(record.get("pid_start_token") or "") or None
    if not pid_is_alive(pid, token):
        return {"status": "NOT_RUNNING", "pid": pid or None, "signal": int(sig)}
    if os.name == "nt":
        os.kill(pid, sig)
    else:
        pgid = int(record.get("process_group_id") or process_group_id(pid) or pid)
        if pgid in {0, 1, os.getpgrp()}:
            raise RuntimeError(f"Refusing to signal unsafe process group {pgid}")
        os.killpg(pgid, sig)
    return {"status": "SIGNALLED", "pid": pid, "signal": int(sig)}


def _numeric_times(root: Path) -> list[float]:
    values: list[float] = []
    if not root.is_dir():
        return values
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            values.append(float(child.name))
        except ValueError:
            continue
    return sorted(values)


def restart_evidence(case_dir: Path) -> dict[str, Any]:
    """Find the latest root or decomposed time without modifying the case."""
    case_dir = Path(case_dir).resolve()
    root_times = _numeric_times(case_dir)
    processor_times: list[float] = []
    for processor in sorted(case_dir.glob("processor[0-9]*")):
        processor_times.extend(_numeric_times(processor))
    latest_root = max(root_times) if root_times else None
    latest_processor = max(processor_times) if processor_times else None
    latest = max(
        value for value in (latest_root, latest_processor) if value is not None
    ) if any(value is not None for value in (latest_root, latest_processor)) else None
    return {
        "latest_time": latest,
        "latest_root_time": latest_root,
        "latest_processor_time": latest_processor,
        "restartable": latest is not None,
        "requires_reconstruction": (
            latest_processor is not None
            and (latest_root is None or latest_processor > latest_root + 1.0e-12)
        ),
    }


def reconcile_solver_record(case_dir: Path) -> dict[str, Any]:
    """Repair stale RUNNING records while preserving restartable output."""
    case_dir = Path(case_dir).resolve()
    record = load_solver_process(case_dir)
    evidence = restart_evidence(case_dir)
    pid = int(record.get("pid") or 0)
    token = str(record.get("pid_start_token") or "") or None
    if pid_is_alive(pid, token):
        return {**record, **evidence, "live": True}
    previous_status = str(record.get("status") or "UNKNOWN")
    if previous_status in {"RUNNING", "STOP_REQUESTED", "STOPPING"}:
        status = "PAUSED_RECOVERABLE" if evidence["restartable"] else "FAILED"
        publish_solver_process(
            case_dir,
            status=status,
            outcome="reconciled_stale_process_record",
            returncode=record.get("returncode"),
        )
        record = load_solver_process(case_dir)
    state = load_execution_state(case_dir)
    if state and str(state.get("state")) == ExecutionState.RUNNING.value:
        recovered = (
            ExecutionState.PAUSED_RECOVERABLE
            if evidence["restartable"]
            else ExecutionState.FAILED
        )
        state = transition_execution_state(
            case_dir,
            recovered,
            phase=state.get("phase"),
            idempotency_key=state.get("idempotency_key"),
            reason="reconciled_stale_process_record",
            evidence=evidence,
            returncode=record.get("returncode"),
        )
    return {**record, **evidence, "execution_state": state, "live": False}
