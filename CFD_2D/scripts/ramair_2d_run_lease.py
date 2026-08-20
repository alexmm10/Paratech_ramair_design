#!/usr/bin/env python3
"""Atomic single-flight leases for long validation-study executions."""
from __future__ import annotations

import hashlib
import json
import os
import time
import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _utc_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a harmless existence probe on Windows:
        # CPython can route it through TerminateProcess with exit code zero.
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_command(pid: int) -> str:
    command = Path(f"/proc/{pid}/cmdline")
    if not command.is_file():
        return ""
    try:
        return command.read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return ""


def _command_hash(command: list[str] | tuple[str, ...] | str) -> str:
    text = command if isinstance(command, str) else "\0".join(command)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DuplicateExecutionError(RuntimeError):
    """Raised when a live execution already owns the requested lease."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(
            f"BLOCKED_DUPLICATE_EXECUTION: {payload.get('lease_path', '')}"
        )
        self.payload = payload


@dataclass
class RunLease:
    path: Path
    history_root: Path
    payload: dict[str, Any]
    acquired: bool = True

    def heartbeat(self, *, state: str | None = None, **updates: Any) -> None:
        if not self.acquired:
            return
        current = dict(self.payload)
        current.update(updates)
        current["heartbeat_at"] = _utc_stamp()
        if state:
            current["state"] = state
        current["pid_alive"] = _pid_is_alive(int(current.get("pid") or 0))
        _atomic_json(self.path, current)
        self.payload = current

    def release(self, *, state: str = "RELEASED", **updates: Any) -> None:
        if not self.acquired:
            return
        current = dict(self.payload)
        current.update(updates)
        current.update(
            state=state,
            heartbeat_at=_utc_stamp(),
            released_at=_utc_stamp(),
        )
        self.history_root.mkdir(parents=True, exist_ok=True)
        history = self.history_root / (
            f"{self.path.stem}_{time.strftime('%Y%m%d_%H%M%S')}_"
            f"{current.get('run_id', 'run')}.json"
        )
        _atomic_json(history, current)
        try:
            disk = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            disk = {}
        if disk.get("lease_id") == current.get("lease_id"):
            self.path.unlink(missing_ok=True)
        self.payload = current
        self.acquired = False

    def __enter__(self) -> "RunLease":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release(
            state="FAILED" if exc is not None else "COMPLETED",
            error=str(exc) if exc is not None else None,
        )


def acquire_run_lease(
    locks_root: Path,
    *,
    study_id: str,
    run_id: str,
    mode: str,
    command: list[str] | tuple[str, ...] | str,
    job_id: str | None = None,
    worker_id: str | None = None,
) -> RunLease:
    """Acquire an O_EXCL lease, reclaiming only demonstrably stale owners."""
    locks_root = Path(locks_root)
    locks_root.mkdir(parents=True, exist_ok=True)
    safe = "_".join(
        part.replace("/", "_").replace("\\", "_").replace(" ", "_")
        for part in (study_id, run_id, mode.lower())
    )
    path = locks_root / f"{safe}.json"
    history = locks_root / "history"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        owner_pid = int(existing.get("pid") or 0)
        owner_command = _process_command(owner_pid)
        live = _pid_is_alive(owner_pid)
        if live:
            raise DuplicateExecutionError(
                {
                    "status": "BLOCKED_DUPLICATE_EXECUTION",
                    "lease_path": str(path),
                    "owner": existing,
                    "matching_process_command": owner_command,
                }
            )
        history.mkdir(parents=True, exist_ok=True)
        stale = history / (
            f"{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}_STALE.json"
        )
        existing.update(
            state="STALE_RECLAIMED",
            reclaimed_at=_utc_stamp(),
            pid_alive=_pid_is_alive(owner_pid),
            inspected_process_command=owner_command,
        )
        _atomic_json(stale, existing)
        path.unlink(missing_ok=True)

    now = _utc_stamp()
    payload = {
        "schema_version": 1,
        "lease_id": hashlib.sha256(
            f"{os.getpid()}:{time.monotonic_ns()}:{run_id}:{mode}".encode()
        ).hexdigest()[:20],
        "study_id": study_id,
        "run_id": run_id,
        "mode": mode,
        "job_id": job_id or run_id,
        "pid": os.getpid(),
        "worker_id": worker_id or f"pid-{os.getpid()}",
        "started_at": now,
        "heartbeat_at": now,
        "command_hash": _command_hash(command),
        "state": "ACQUIRED",
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError:
        try:
            owner = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            owner = {}
        raise DuplicateExecutionError(
            {
                "status": "BLOCKED_DUPLICATE_EXECUTION",
                "lease_path": str(path),
                "owner": owner,
            }
        )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return RunLease(path=path, history_root=history, payload=payload)
