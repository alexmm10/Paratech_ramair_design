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


def elapsed_seconds(value: str | None) -> float | None:
    if not value:
        return None
    parts = [float(item) for item in value.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60.0 + parts[1]
    return parts[0]


def cell_count(case: Path) -> int | None:
    owner = case / "constant/polyMesh/owner"
    if not owner.is_file():
        return None
    match = re.search(r"\bnCells\s*:\s*(\d+)", owner.read_text(
        encoding="utf-8", errors="ignore"
    )[:16000])
    return int(match.group(1)) if match else None


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
    clock = read_last_float(solver_text, rf"ClockTime\s*=\s*({NUMBER})\s*s")
    cells = cell_count(directory)
    elapsed_label = (
        match.group(1)
        if (
            match := re.search(
                r"Elapsed \(wall clock\) time .*:\s*([0-9:.]+)\s*$",
                time_text,
                re.MULTILINE,
            )
        )
        else None
    )
    total_wall = elapsed_seconds(elapsed_label)
    steps = len(physical_times)
    solver_started = bool(status.get("solver_started")) or bool(physical_times)
    benchmark_valid = solver_started and steps >= 5 and clock is not None and clock > 0
    if not solver_started:
        invalid_reason = "solver_not_started"
    elif steps < 5:
        invalid_reason = "insufficient_solver_steps"
    elif clock is None or clock <= 0:
        invalid_reason = "missing_solver_clock_time"
    else:
        invalid_reason = None
    seconds_per_step = clock / steps if clock is not None and steps else None
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
        "cell_count": cells,
        "cells_per_rank": float(cells) / int(cores) if cells else None,
        "solver_steps": steps,
        "benchmark_valid": benchmark_valid,
        "invalid_reason": invalid_reason,
        "physical_time_reached_s": float(physical_times[-1]) if physical_times else None,
        "solver_clock_time_s": clock,
        "seconds_per_step": seconds_per_step,
        "steps_per_second": 1.0 / seconds_per_step if seconds_per_step else None,
        "cell_steps_per_second": (
            float(cells) / seconds_per_step if cells and seconds_per_step else None
        ),
        "core_seconds_per_step": (
            seconds_per_step * int(cores) if seconds_per_step else None
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
        "elapsed_wall_time": elapsed_label,
        "elapsed_wall_time_s": total_wall,
        "setup_and_io_overhead_s": (
            max(0.0, total_wall - clock)
            if total_wall is not None and clock is not None else None
        ),
        "maximum_resident_kb": read_last_float(
            time_text, rf"Maximum resident set size \(kbytes\):\s*({NUMBER})"
        ),
        "solver_log": str(log_path) if log_path else None,
        "fvSchemes_sha256": (
            (root / f"fvSchemes_{directory.name}.sha256").read_text(
                encoding="utf-8"
            ).strip()
            if (root / f"fvSchemes_{directory.name}.sha256").is_file()
            else None
        ),
        "fvSolution_sha256": (
            (root / f"fvSolution_{directory.name}.sha256").read_text(
                encoding="utf-8"
            ).strip()
            if (root / f"fvSolution_{directory.name}.sha256").is_file()
            else None
        ),
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
    baselines = {
        (row["numerics"], row["backend"]): row
        for row in records
        if row["cores"] == 1 and row.get("benchmark_valid")
    }
    for row in records:
        baseline = baselines.get((row["numerics"], row["backend"]))
        if not baseline or not row.get("benchmark_valid"):
            row["speedup"] = None
            row["parallel_efficiency"] = None
            continue
        speedup = float(baseline["seconds_per_step"]) / float(row["seconds_per_step"])
        row["speedup"] = speedup
        row["parallel_efficiency"] = speedup / int(row["cores"])
    valid = [row for row in records if row.get("benchmark_valid")]
    minimum_latency = min(valid, key=lambda row: float(row["seconds_per_step"])) if valid else None
    minimum_cpu_cost = min(valid, key=lambda row: float(row["core_seconds_per_step"])) if valid else None
    output_json = root / "benchmark_summary.json"
    output_csv = root / "benchmark_summary.csv"
    output_json.write_text(
        json.dumps(
            {
                "status": (
                    "COMPLETE"
                    if records and all(item.get("benchmark_valid") for item in records)
                    else "INCOMPLETE"
                ),
                "records": records,
                "numerics_invariant": bool(records) and len({
                    (item["fvSchemes_sha256"], item["fvSolution_sha256"])
                    for item in records
                }) == 1,
                "minimum_latency": minimum_latency,
                "minimum_cpu_cost": minimum_cpu_cost,
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
