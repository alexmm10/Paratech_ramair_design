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
    if bool(entry.get("steady_initialization", True)) and not (resume and numeric_times(case)):
        command.append("--steady-initialization")
        command += ["--steady-timeout-min", str(float(entry.get("steady_timeout_min", 120.0)))]
    if resume and numeric_times(case):
        command.append("--resume")
        extension = entry.get("resume_additional_time_star")
        if extension is not None:
            command += ["--resume-additional-time-star", str(float(extension))]
    if bool(entry.get("continue_transient_after_steady_timeout", False)):
        command.append("--continue-transient-after-steady-timeout")
    return command


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
            process = subprocess.Popen(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
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
                if STOP.is_file():
                    (case / ".ramair_stop_request.json").write_text(
                        json.dumps({"mode": "writeNow", "requested_at": time.time()}) + "\n",
                        encoding="utf-8",
                    )
                solver = load_solver_process(case)
                write_json(RUNTIME, {**payload, "orchestrator_pid": process.pid, "solver": solver, "heartbeat": time.time()})
                time.sleep(2.0)
        stopped = STOP.is_file()
        terminal = "PAUSED_RECOVERABLE" if stopped else "COMPLETED" if process.returncode == 0 else "FAILED"
        transition_execution_state(
            case,
            terminal,
            phase="QUEUE",
            run_id=case_id,
            idempotency_key=key,
            reason="user_stop" if stopped else "queue_case_exit",
            returncode=int(process.returncode or 0),
        )
        finished = {**payload, "status": terminal, "returncode": int(process.returncode or 0), "finished_at": time.time()}
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "resume", "stop", "force-stop", "postprocess", "status"))
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
    print(json.dumps(read_json(STATUS, {"status": "NOT_STARTED"}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
