#!/usr/bin/env python3
"""Durable process identity and restart evidence for RamAir OpenFOAM runs."""
from __future__ import annotations

import json
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


PROCESS_RECORD = ".ramair_solver_process.json"
STOP_REQUEST = ".ramair_stop_request.json"
_JSON_WRITE_LOCK = threading.RLock()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    with _JSON_WRITE_LOCK:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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
    return write_json_atomic(path, payload)


def load_solver_process(case_dir: Path) -> dict[str, Any]:
    return read_json(Path(case_dir).resolve() / PROCESS_RECORD, {}) or {}


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
        status = "PAUSED_RESTARTABLE" if evidence["restartable"] else "STOPPED_INCOMPLETE"
        publish_solver_process(
            case_dir,
            status=status,
            outcome="reconciled_stale_process_record",
            returncode=record.get("returncode"),
        )
        record = load_solver_process(case_dir)
    return {**record, **evidence, "live": False}
