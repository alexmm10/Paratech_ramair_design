#!/usr/bin/env python3
"""Run independent RamAir case commands concurrently within one core budget."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


TERMINAL = {"COMPLETED", "FAILED", "PAUSED"}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def run_campaign(manifest_path: Path) -> int:
    manifest_path = manifest_path.resolve()
    manifest = _read(manifest_path)
    cases = [dict(item) for item in manifest.get("cases") or []]
    if not cases:
        raise ValueError("A parallel campaign requires at least one case")
    total_budget = max(1, int(manifest.get("total_core_budget") or 1))
    max_concurrent = max(1, int(manifest.get("max_concurrent_cases") or 1))
    if max_concurrent < 2:
        raise ValueError("Parallel campaign requires max_concurrent_cases >= 2")
    for item in cases:
        item["command"] = [str(value) for value in item.get("command") or []]
        item["n_cores"] = max(1, int(item.get("n_cores") or 1))
        if not item["command"]:
            raise ValueError(f"Missing command for {item.get('case_id')}")
        if item["n_cores"] > total_budget:
            raise ValueError(
                f"{item.get('case_id')} requests {item['n_cores']} cores, above budget {total_budget}"
            )
        item.update(status="PENDING", pid=None, returncode=None)

    status_path = manifest_path.with_suffix(".status.json")
    control_path = manifest_path.with_suffix(".control.json")
    logs_root = manifest_path.parent / (manifest_path.stem + "_logs")
    logs_root.mkdir(parents=True, exist_ok=True)
    control_path.unlink(missing_ok=True)
    state = {
        "schema_version": 1,
        "campaign_id": manifest.get("campaign_id") or manifest_path.stem,
        "status": "RUNNING",
        "total_core_budget": total_budget,
        "max_concurrent_cases": max_concurrent,
        "started_at": _stamp(),
        "updated_at": _stamp(),
        "cases": cases,
    }
    _write(status_path, state)
    active: dict[int, tuple[subprocess.Popen[str], Any, float]] = {}
    stop_requested = False

    def stop_all(_signum: int | None = None, _frame: Any = None) -> None:
        nonlocal stop_requested
        stop_requested = True
        for process, _, _ in active.values():
            if process.poll() is None:
                try:
                    process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    while True:
        if control_path.is_file():
            try:
                request = _read(control_path)
            except (OSError, ValueError, json.JSONDecodeError):
                request = {}
            action = str(request.get("action") or "pause_all")
            if action == "pause_all":
                stop_all()
            elif action == "pause_case":
                requested_id = str(request.get("case_id") or "")
                for index, (process, _, _) in list(active.items()):
                    if str(cases[index].get("case_id")) == requested_id and process.poll() is None:
                        process.send_signal(signal.SIGINT)
                        cases[index]["pause_requested"] = True
            control_path.unlink(missing_ok=True)

        for index, (process, handle, started) in list(active.items()):
            returncode = process.poll()
            if returncode is None:
                continue
            handle.flush()
            handle.close()
            item = cases[index]
            item.update(
                returncode=int(returncode),
                status=("PAUSED" if item.get("pause_requested") or stop_requested else
                        "COMPLETED" if returncode == 0 else "FAILED"),
                finished_at=_stamp(),
                wall_time_s=time.monotonic() - started,
            )
            active.pop(index, None)

        used = sum(cases[index]["n_cores"] for index in active)
        if not stop_requested:
            for index, item in enumerate(cases):
                if len(active) >= max_concurrent:
                    break
                if item["status"] != "PENDING" or used + item["n_cores"] > total_budget:
                    continue
                log_path = logs_root / f"{index + 1:02d}_{item.get('case_id', 'case')}.log"
                handle = log_path.open("w", encoding="utf-8", buffering=1)
                environment = os.environ.copy()
                environment["RAMAIR_CASE_RANK_LIMIT"] = str(item["n_cores"])
                environment["RAMAIR_PARALLEL_WORKLOAD_MODE"] = "concurrent_throughput"
                environment["RAMAIR_PARALLEL_CAMPAIGN_ID"] = str(state["campaign_id"])
                process = subprocess.Popen(
                    item["command"],
                    cwd=str(manifest.get("project_root") or manifest_path.parent),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=environment,
                )
                item.update(
                    status="RUNNING", pid=int(process.pid), started_at=_stamp(),
                    log_path=str(log_path),
                )
                active[index] = (process, handle, time.monotonic())
                used += item["n_cores"]

        state["updated_at"] = _stamp()
        state["active_case_ids"] = [str(cases[index].get("case_id")) for index in active]
        state["active_cores"] = sum(cases[index]["n_cores"] for index in active)
        _write(status_path, state)
        if not active and (stop_requested or all(item["status"] in TERMINAL for item in cases)):
            break
        if not active and not any(item["status"] == "PENDING" for item in cases):
            break
        time.sleep(1.0)

    for index, (process, handle, started) in list(active.items()):
        try:
            process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        handle.close()
        cases[index].update(
            status="PAUSED", returncode=process.returncode,
            finished_at=_stamp(), wall_time_s=time.monotonic() - started,
        )
    state.update(
        status=("PAUSED" if stop_requested else
                "FINISHED_WITH_ISSUES" if any(item["status"] == "FAILED" for item in cases)
                else "FINISHED"),
        active_case_ids=[], active_cores=0, finished_at=_stamp(), updated_at=_stamp(),
    )
    _write(status_path, state)
    print(json.dumps(state, indent=2))
    return 1 if state["status"] == "FINISHED_WITH_ISSUES" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    return run_campaign(parser.parse_args().manifest)


if __name__ == "__main__":
    raise SystemExit(main())
