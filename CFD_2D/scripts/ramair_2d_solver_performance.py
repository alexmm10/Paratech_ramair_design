#!/usr/bin/env python3
"""Project transient OpenFOAM run time from a completed bounded benchmark."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return data


def latest_solver_log(case_dir: Path) -> Path:
    candidates = [
        case_dir / "PyFoamRunner.foamRun.logfile",
        case_dir / "log.foamRun",
        case_dir / "log.pimpleFoam",
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise FileNotFoundError(f"No reconstructed transient solver log found under {case_dir}")
    return max(existing, key=lambda path: path.stat().st_mtime)


def mesh_cell_count(case_dir: Path) -> int | None:
    """Read the writer's planning count without depending on a checkMesh log."""
    summary_path = case_dir / "case_input_summary.json"
    if not summary_path.is_file():
        return None
    summary = read_json(summary_path)
    candidates = [
        (summary.get("mesh") or {}).get("cell_count"),
        (summary.get("estimated_storage") or {}).get("mesh_cell_count"),
    ]
    for value in candidates:
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return None


def benchmark_from_log(case_dir: Path, log_path: Path, targets_star: list[float]) -> dict[str, Any]:
    config = read_json(case_dir / "case_config.json")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    physical_times = [float(value) for value in re.findall(rf"^Time\s*=\s*({NUMBER})s\s*$", text, re.MULTILINE)]
    wall_times = [float(value) for value in re.findall(rf"ClockTime\s*=\s*({NUMBER})\s*s", text)]
    delta_ts = [float(value) for value in re.findall(rf"^deltaT\s*=\s*({NUMBER})\s*$", text, re.MULTILINE)]
    if not physical_times or not wall_times:
        raise ValueError(f"Solver log contains no transient Time/ClockTime samples: {log_path}")
    chord = float(config["chord_m"])
    velocity = float(config["velocity_m_s"])
    latest_time = max(physical_times)
    wall_time = max(wall_times)
    achieved_time_star = latest_time * velocity / chord
    if achieved_time_star <= 0.0:
        raise ValueError("The bounded benchmark did not advance to positive convective time.")
    seconds_per_time_star = wall_time / achieved_time_star
    final_delta_t = delta_ts[-1] if delta_ts else None
    requested_delta_t_star = float(config.get("deltaT_star", 0.0) or 0.0)
    requested_delta_t = requested_delta_t_star * chord / velocity if requested_delta_t_star > 0.0 else None
    estimates = []
    for target in targets_star:
        projected_seconds = seconds_per_time_star * float(target)
        target_physical_s = float(target) * chord / velocity
        estimates.append({
            "target_time_star": float(target),
            "target_physical_time_s": target_physical_s,
            "projected_wall_seconds": projected_seconds,
            "projected_wall_hours": projected_seconds / 3600.0,
            "effective_steps_at_final_delta_t": (
                math.ceil(target_physical_s / final_delta_t)
                if final_delta_t is not None and final_delta_t > 0.0 else None
            ),
        })
    return {
        "status": "ESTIMATED_FROM_BOUNDED_REAL_RUN",
        "case": str(case_dir.resolve()),
        "solver_log": str(log_path.resolve()),
        "n_cores": int(read_json(case_dir / "pyfoam_run_report.json").get("n_cores", 1))
        if (case_dir / "pyfoam_run_report.json").is_file() else None,
        "mesh_cells": mesh_cell_count(case_dir),
        "benchmark": {
            "physical_time_reached_s": latest_time,
            "convective_time_reached_star": achieved_time_star,
            "solver_clock_time_s": wall_time,
            "logged_time_steps": len(physical_times),
            "seconds_per_convective_time_star": seconds_per_time_star,
            "final_delta_t_s": final_delta_t,
            "requested_delta_t_s": requested_delta_t,
            "requested_delta_t_star": requested_delta_t_star,
            "final_to_requested_delta_t_ratio": (
                final_delta_t / requested_delta_t
                if final_delta_t is not None and requested_delta_t is not None and requested_delta_t > 0.0 else None
            ),
        },
        "projections": estimates,
        "interpretation": [
            "The projection is linear in convective time and includes startup overhead, so a very short run is conservative.",
            "The transient PIMPLE/backward scheme is implicit; maxCo is retained as an accuracy and robustness limit, not an explicit-CFL proof.",
            "Adaptive time stepping can create far more steps than the nominal deltaT* count when the smallest cells control maxCo.",
            "Re-benchmark after changing mesh, core count, turbulence model or numerics.",
        ],
    }


def write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "RamAir OpenFOAM transient performance estimate",
        "==============================================",
        "",
        f"Status: {report['status']}",
        f"Case: {report['case']}",
        f"Solver log: {report['solver_log']}",
        f"Cores: {report.get('n_cores')}",
        "",
        "Measured bounded run:",
    ]
    for key, value in report["benchmark"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Projected wall times:"])
    for item in report["projections"]:
        lines.append(
            f"- t*={item['target_time_star']:g}: {item['projected_wall_hours']:.3f} h "
            f"({item['effective_steps_at_final_delta_t']} effective steps at the final measured deltaT)"
        )
    lines.extend(["", "Caveats:"] + [f"- {note}" for note in report["interpretation"]])
    output.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--solver-log", type=Path)
    parser.add_argument(
        "--targets-star",
        type=float,
        nargs="+",
        default=[2.0, 10.0, 31.9024019, 40.0, 319.024019],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    case_dir = args.case.resolve()
    log_path = args.solver_log.resolve() if args.solver_log else latest_solver_log(case_dir)
    report = benchmark_from_log(case_dir, log_path, args.targets_star)
    output = args.output or case_dir / "solver_runtime_estimate.json"
    write_report(report, output)
    print(json.dumps({"status": report["status"], "output": str(output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
