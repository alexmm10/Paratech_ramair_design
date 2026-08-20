#!/usr/bin/env python3
"""Summarize bounded native/PyFoam, MPI-rank and PIMPLE benchmark scenarios."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
SCENARIO = re.compile(r"^(previous|optimized|current)_(\d+)cores_(native|pyfoam)$")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def read_last_float(text: str, pattern: str) -> float | None:
    values = re.findall(pattern, text, re.MULTILINE)
    return float(values[-1]) if values else None


def scenario_record(root: Path, directory: Path) -> dict[str, Any] | None:
    match = SCENARIO.match(directory.name)
    if not match:
        return None
    numerics, cores, backend = match.groups()
    logs = [
        directory / "log.foamRun",
        directory / "PyFoamRunner.foamRun.logfile",
    ]
    existing_logs = [path for path in logs if path.is_file()]
    log_path = max(existing_logs, key=lambda path: path.stat().st_mtime) if existing_logs else None
    solver_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path else ""
    time_path = root / f"time_{directory.name}.txt"
    time_text = time_path.read_text(encoding="utf-8", errors="replace") if time_path.is_file() else ""
    physical_times = re.findall(rf"^Time\s*=\s*({NUMBER})s\s*$", solver_text, re.MULTILINE)
    status = read_json(directory / "run_status.json")
    return_code_path = root / f"returncode_{directory.name}.txt"
    return {
        "scenario": directory.name,
        "numerics": numerics,
        "cores": int(cores),
        "backend": backend,
        "status": status.get("status"),
        "return_code": (
            int(return_code_path.read_text(encoding="utf-8").strip())
            if return_code_path.is_file() else None
        ),
        "solver_steps": len(physical_times),
        "physical_time_reached_s": float(physical_times[-1]) if physical_times else None,
        "solver_clock_time_s": read_last_float(
            solver_text, rf"ClockTime\s*=\s*({NUMBER})\s*s"
        ),
        "solver_execution_time_s": read_last_float(
            solver_text, rf"ExecutionTime\s*=\s*({NUMBER})\s*s"
        ),
        "final_deltaT_s": read_last_float(
            solver_text, rf"^deltaT\s*=\s*({NUMBER})\s*$"
        ),
        "final_maxCo": read_last_float(
            solver_text, rf"Courant Number mean:\s*{NUMBER}\s+max:\s*({NUMBER})"
        ),
        "elapsed_wall_time": (
            match.group(1)
            if (
                match := re.search(
                    r"Elapsed \(wall clock\) time .*:\s*([0-9:.]+)\s*$",
                    time_text,
                    re.MULTILINE,
                )
            )
            else None
        ),
        "maximum_resident_kb": read_last_float(
            time_text, rf"Maximum resident set size \(kbytes\):\s*({NUMBER})"
        ),
        "solver_log": str(log_path) if log_path else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.benchmark_root.resolve()
    records = [
        record
        for directory in sorted(path for path in root.iterdir() if path.is_dir())
        if (record := scenario_record(root, directory)) is not None
    ]
    output_json = root / "benchmark_summary.json"
    output_csv = root / "benchmark_summary.csv"
    output_json.write_text(
        json.dumps(
            {
                "status": "COMPLETE" if records and all(item["status"] for item in records) else "INCOMPLETE",
                "records": records,
                "comparison_note": (
                    "Use matched pairs to isolate one effect: previous/optimized at fixed cores/backend, "
                    "6/8 cores at fixed numerics/backend, and native/pyfoam at fixed numerics/cores."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if records:
        with output_csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    print(json.dumps({"records": len(records), "json": str(output_json), "csv": str(output_csv)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
