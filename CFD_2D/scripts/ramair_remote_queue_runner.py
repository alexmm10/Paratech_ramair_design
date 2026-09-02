#!/usr/bin/env python3
"""Run, stop, resume and post-process a frozen remote OpenFOAM queue."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import zipfile
import hashlib
from pathlib import Path
from typing import Any

from ramair_execution_control import (
    ExecutionState,
    execution_idempotency_key,
    load_execution_state,
    load_solver_process,
    reconcile_solver_record,
    signal_solver_process,
    transition_execution_state,
)
from ramair_2d_openfoam_runner import update_control_dict_stop_at


ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "remote_queue.json"
STATUS = ROOT / "remote_queue_status.json"
RUNTIME = ROOT / "remote_active_execution.json"
STOP = ROOT / ".ramair_remote_stop"
LOGS = ROOT / "Remote Execution Logs"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def numeric_times(case: Path) -> list[float]:
    values: list[float] = []
    for path in case.iterdir() if case.is_dir() else ():
        if path.is_dir():
            try:
                value = float(path.name)
            except ValueError:
                continue
            if value > 0:
                values.append(value)
    return sorted(values)


def request_stop(force: bool = False) -> dict[str, Any]:
    runtime = read_json(RUNTIME, {}) or {}
    case_text = runtime.get("case")
    if not case_text:
        return {"status": "NO_ACTIVE_CASE"}
    case = Path(case_text).resolve()
    STOP.write_text(json.dumps({"requested_at": time.time(), "force": force}) + "\n", encoding="utf-8")
    (case / ".ramair_stop_request.json").write_text(
        json.dumps({"mode": "writeNow", "requested_at": time.time()}) + "\n",
        encoding="utf-8",
    )
    control_result = None
    try:
        control_result = str(update_control_dict_stop_at(case, "writeNow"))
    except Exception as exc:  # The runner marker remains the primary request.
        control_result = f"ERROR: {exc}"
    signal_result = None
    if force:
        signal_result = signal_solver_process(case, signal.SIGINT)
    result = {
        "status": "FORCE_STOP_REQUESTED" if force else "CLEAN_STOP_REQUESTED",
        "case": str(case),
        "control_backup": control_result,
        "signal": signal_result,
    }
    write_json(RUNTIME, {**runtime, **result, "updated_at": time.time()})
    return result


def staged_command(entry: dict[str, Any], resume: bool) -> list[str]:
    case = (ROOT / str(entry["case"])).resolve()
    command = [
        sys.executable,
        str(ROOT / "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py"),
        "--case", str(case),
        "--solver", str(entry.get("solver", "auto")),
        "--execution-backend", "native",
        "--n-cores", str(int(entry.get("n_cores", 8))),
        "--timeout-min", str(float(entry.get("timeout_min", 120.0))),
        "--cleanup-processor-directories",
        "--run",
    ]
    command.append(
        "--automatic-core-selection"
        if bool(entry.get("automatic_core_selection", True))
        else "--no-automatic-core-selection"
    )
    command.append(
        "--renumber-before-decompose"
        if bool(entry.get("renumber_before_decompose", True))
        else "--no-renumber-before-decompose"
    )
    phase_plan = entry.get("transient_phase_plan")
    if phase_plan:
        command += ["--transient-phase-plan", str((ROOT / str(phase_plan)).resolve())]
    execution_mode = str(entry.get("execution_mode") or "rans_urans")
    if execution_mode == "rans_only":
        command += ["--steady-initialization", "--steady-only"]
        command += ["--steady-timeout-min", str(float(entry.get("steady_timeout_min", 180.0)))]
    elif bool(entry.get("steady_initialization", True)) and not (resume and numeric_times(case)):
        command.append("--steady-initialization")
        command += ["--steady-timeout-min", str(float(entry.get("steady_timeout_min", 120.0)))]
    if execution_mode != "rans_only" and resume and numeric_times(case):
        command.append("--resume")
        extension = entry.get("resume_additional_time_star")
        if extension is not None:
            command += ["--resume-additional-time-star", str(float(extension))]
    if bool(entry.get("continue_transient_after_steady_timeout", False)):
        command.append("--continue-transient-after-steady-timeout")
    return command


def _stop_timed_out_process(process: subprocess.Popen[Any], case: Path) -> None:
    """Request a field write first, then stop the complete orchestration group."""
    (case / ".ramair_stop_request.json").write_text(
        json.dumps({"mode": "writeNow", "reason": "remote_case_timeout", "requested_at": time.time()}) + "\n",
        encoding="utf-8",
    )
    try:
        update_control_dict_stop_at(case, "writeNow")
    except Exception:
        pass
    try:
        os.killpg(process.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=120.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        try:
            process.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()


def run_queue(resume: bool = False) -> int:
    queue = read_json(QUEUE, {}) or {}
    entries = list(queue.get("cases") or [])
    if not entries:
        raise RuntimeError(f"No cases in {QUEUE}")
    STOP.unlink(missing_ok=True)
    status = read_json(STATUS, {}) or {"schema_version": 1, "cases": {}}
    status.setdefault("cases", {})
    LOGS.mkdir(parents=True, exist_ok=True)
    for index, entry in enumerate(entries):
        case_id = str(entry["id"])
        previous = dict(status["cases"].get(case_id) or {})
        if previous.get("status") == "COMPLETED":
            continue
        if STOP.is_file():
            break
        case = (ROOT / str(entry["case"])).resolve()
        lifecycle = load_execution_state(case)
        if lifecycle.get("state") == ExecutionState.RUNNING.value:
            reconcile_solver_record(case)
            lifecycle = load_execution_state(case)
        if lifecycle.get("state") == ExecutionState.COMPLETED.value:
            status["cases"][case_id] = {
                **previous,
                "status": ExecutionState.COMPLETED.value,
                "case": str(case),
                "reason": "idempotent_completed_case_skip",
            }
            write_json(STATUS, status)
            continue
        command = staged_command(entry, resume or previous.get("status") == "PAUSED_RECOVERABLE")
        key = execution_idempotency_key(case, command, phase="QUEUE")
        transition_execution_state(
            case,
            ExecutionState.PREPARED,
            phase="QUEUE",
            run_id=case_id,
            idempotency_key=key,
            reason="sequential_queue_dispatch",
            force=bool(lifecycle),
        )
        log_path = LOGS / f"{index + 1:03d}_{case_id}.log"
        payload = {
            "status": "RUNNING",
            "case": str(case),
            "case_id": case_id,
            "command": command,
            "started_at": time.time(),
            "log": str(log_path),
        }
        write_json(RUNTIME, payload)
        status["cases"][case_id] = payload
        write_json(STATUS, status)
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            case_timeout_s = 60.0 * max(1.0, float(entry.get("case_timeout_min", 360.0)))
            deadline = time.monotonic() + case_timeout_s
            case_timed_out = False
            transition_execution_state(
                case,
                ExecutionState.RUNNING,
                phase="QUEUE",
                run_id=case_id,
                idempotency_key=key,
                reason="sequential_queue_started",
                pid=process.pid,
            )
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    case_timed_out = True
                    _stop_timed_out_process(process, case)
                    break
                if STOP.is_file():
                    (case / ".ramair_stop_request.json").write_text(
                        json.dumps({"mode": "writeNow", "requested_at": time.time()}) + "\n",
                        encoding="utf-8",
                    )
                solver = load_solver_process(case)
                write_json(RUNTIME, {**payload, "orchestrator_pid": process.pid, "solver": solver, "heartbeat": time.time()})
                time.sleep(2.0)
        stopped = STOP.is_file()
        terminal = (
            "PAUSED_RECOVERABLE" if stopped
            else "CASE_TIMEOUT_PARTIAL" if case_timed_out
            else "COMPLETED" if process.returncode == 0
            else "FAILED"
        )
        transition_execution_state(
            case,
            terminal,
            phase="QUEUE",
            run_id=case_id,
            idempotency_key=key,
            reason="user_stop" if stopped else "queue_case_exit",
            returncode=int(process.returncode or 0),
        )
        finished = {
            **payload,
            "status": terminal,
            "case_timeout": bool(case_timed_out),
            "returncode": int(process.returncode or 0),
            "finished_at": time.time(),
        }
        status["cases"][case_id] = finished
        write_json(STATUS, status)
        write_json(RUNTIME, finished)
        if stopped or (process.returncode and not bool(queue.get("continue_after_error", True))):
            break
    complete = all((status["cases"].get(str(item["id"])) or {}).get("status") == "COMPLETED" for item in entries)
    status["status"] = "COMPLETED" if complete else "PAUSED_OR_INCOMPLETE"
    status["updated_at"] = time.time()
    write_json(STATUS, status)
    return 0 if complete else 2


def postprocess_queue() -> int:
    queue = read_json(QUEUE, {}) or {}
    rc = 0
    for entry in queue.get("cases") or []:
        case = (ROOT / str(entry["case"])).resolve()
        output = ROOT / "Remote Postprocess" / str(entry["id"])
        command = [
            sys.executable,
            str(ROOT / "CFD_2D/scripts/ramair_2d_postprocess.py"),
            "--case-root", str(ROOT),
            "--case-dir", str(case),
            "--output-dir", str(output),
            "--variant", str(entry["variant"]),
            "--alpha", str(float(entry["alpha_deg"])),
            "--run-openfoam-postprocess",
            "--automatic-paraview-products",
        ]
        completed = subprocess.run(command, cwd=str(ROOT), check=False)
        rc = max(rc, int(completed.returncode))
    return rc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_results() -> int:
    """Create a deterministic return archive that the main application can classify."""
    queue = read_json(QUEUE, {}) or {}
    package_id = str(queue.get("package_id") or ROOT.name)
    return_root = ROOT / "Remote Return"
    return_root.mkdir(parents=True, exist_ok=True)
    output = return_root / f"RamAir_Remote_Return_{package_id}.zip"
    selected: list[tuple[Path, Path]] = []
    case_rows: list[dict[str, Any]] = []
    for entry in queue.get("cases") or []:
        relative = Path(str(entry["case"]))
        source = (ROOT / relative).resolve()
        if not source.is_dir():
            continue
        for path in source.rglob("*"):
            if path.is_file() and "processor" not in path.parts and "__pycache__" not in path.parts:
                selected.append((path, Path("payload") / relative / path.relative_to(source)))
        case_rows.append({
            "id": entry.get("id"),
            "variant": entry.get("variant"),
            "alpha_deg": entry.get("alpha_deg"),
            "case": relative.as_posix(),
            "status": (read_json(STATUS, {}).get("cases", {}).get(str(entry.get("id"))) or {}).get("status"),
        })
    for source in (STATUS, QUEUE, RUNTIME):
        if source.is_file():
            selected.append((source, Path("payload") / source.name))
    if LOGS.is_dir():
        for path in LOGS.rglob("*"):
            if path.is_file():
                selected.append((path, Path("payload/Remote Execution Logs") / path.relative_to(LOGS)))
    files = {
        target.as_posix(): {"sha256": _sha256(source), "bytes": source.stat().st_size}
        for source, target in selected
    }
    manifest = {
        "schema_version": 1,
        "package_id": package_id,
        "package_scope": queue.get("package_scope", "generic"),
        "created_at": time.time(),
        "cases": case_rows,
        "files": files,
    }
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("RAMAir_REMOTE_RETURN/return_manifest.json", json.dumps(manifest, indent=2) + "\n")
        for source, target in selected:
            archive.write(source, Path("RAMAir_REMOTE_RETURN") / target)
    print(json.dumps({"status": "COLLECTED", "archive": str(output), "cases": len(case_rows)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "resume", "stop", "force-stop", "postprocess", "collect", "status"))
    args = parser.parse_args()
    if args.action == "run":
        return run_queue(False)
    if args.action == "resume":
        return run_queue(True)
    if args.action in {"stop", "force-stop"}:
        print(json.dumps(request_stop(args.action == "force-stop"), indent=2))
        return 0
    if args.action == "postprocess":
        return postprocess_queue()
    if args.action == "collect":
        return collect_results()
    print(json.dumps(read_json(STATUS, {"status": "NOT_STARTED"}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
