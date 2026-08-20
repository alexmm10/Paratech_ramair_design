#!/usr/bin/env python3
"""Diagnose fixed or adaptive OpenFOAM time-step behavior from a real solver log."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return data


def solver_log(case_dir: Path) -> Path:
    candidates = [
        case_dir / "log.foamRun",
        case_dir / "PyFoamRunner.foamRun.logfile",
        case_dir / "log.pimpleFoam",
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise FileNotFoundError(f"No solver log found under {case_dir}")
    return max(existing, key=lambda path: path.stat().st_mtime)


def parse_history(text: str) -> dict[str, list[float]]:
    return {
        "delta_t_s": [
            float(value)
            for value in re.findall(rf"^deltaT\s*=\s*({NUMBER})\s*$", text, re.MULTILINE)
        ],
        "courant_mean": [
            float(value)
            for value in re.findall(
                rf"Courant Number mean:\s*({NUMBER})\s+max:\s*{NUMBER}", text
            )
        ],
        "courant_max": [
            float(value)
            for value in re.findall(
                rf"Courant Number mean:\s*{NUMBER}\s+max:\s*({NUMBER})", text
            )
        ],
        "physical_time_s": [
            float(value)
            for value in re.findall(rf"^Time\s*=\s*({NUMBER})s\s*$", text, re.MULTILINE)
        ],
    }


def diagnose(case_dir: Path, log_path: Path) -> dict[str, Any]:
    config = read_json(case_dir / "case_config.json")
    history = parse_history(log_path.read_text(encoding="utf-8", errors="replace"))
    if not history["courant_max"]:
        raise ValueError(f"No transient deltaT/Courant history found in {log_path}")
    velocity = float(config["velocity_m_s"])
    chord = float(config["chord_m"])
    time_step_mode = str(config.get("time_step_mode", "adaptive_courant"))
    if not history["delta_t_s"]:
        configured_delta_t = config.get("deltaT_s")
        if configured_delta_t is None:
            configured_delta_t = float(config["deltaT_star"]) * chord / max(velocity, 1.0e-30)
        history["delta_t_s"] = [float(configured_delta_t)]
    max_co = float(config.get("maxCo", 1.0))
    max_dt_star = float(config["maxDeltaT_star"])
    max_dt_s = max_dt_star * chord / max(velocity, 1.0e-30)
    final_dt = history["delta_t_s"][-1]
    final_co = history["courant_max"][-1]
    dt_fraction = final_dt / max(max_dt_s, 1.0e-30)
    co_fraction = final_co / max(max_co, 1.0e-30)
    if time_step_mode == "fixed":
        limiter = "FIXED_DELTA_T"
    elif dt_fraction >= 0.95:
        limiter = "MAX_DELTA_T"
    elif co_fraction >= 0.8:
        limiter = "MAX_CO"
    else:
        limiter = "STARTUP_OR_OTHER_CONTROL"
    return {
        "status": "DIAGNOSED_FROM_REAL_SOLVER_LOG",
        "case": str(case_dir.resolve()),
        "solver_log": str(log_path.resolve()),
        "configured": {
            "time_step_mode": time_step_mode,
            "maxCo": max_co,
            "maxDeltaT_star": max_dt_star,
            "maxDeltaT_s": max_dt_s,
        },
        "measured_final": {
            "deltaT_s": final_dt,
            "deltaT_star": final_dt * velocity / chord,
            "courant_mean": history["courant_mean"][-1] if history["courant_mean"] else None,
            "courant_max": final_co,
            "physical_time_s": history["physical_time_s"][-1] if history["physical_time_s"] else None,
            "deltaT_fraction_of_ceiling": dt_fraction,
            "Courant_fraction_of_limit": co_fraction,
        },
        "active_limiter": limiter,
        "interpretation": (
            "deltaT is imposed by the fixed-step configuration; Courant is diagnostic and does not reduce the step."
            if limiter == "FIXED_DELTA_T"
            else (
                "The time-step ceiling is active."
                if limiter == "MAX_DELTA_T"
                else (
                    "A local cell/face flux is controlling adaptive deltaT through maxCo; increasing maxDeltaT will not accelerate this state."
                    if limiter == "MAX_CO"
                    else "The final sample is not close to either configured limit; inspect startup, write alignment and solver events."
                )
            )
        ),
        "samples": {
            "deltaT": len(history["delta_t_s"]),
            "Courant": len(history["courant_max"]),
        },
    }


def locate_max_courant(case_dir: Path, bashrc: Path) -> dict[str, Any]:
    command = (
        "set +u; "
        f"source {shlex.quote(str(bashrc))} >/dev/null 2>&1; "
        "set -u; "
        f"foamPostProcess -case {shlex.quote(str(case_dir))} -func CourantNo -latestTime; "
        f"foamPostProcess -case {shlex.quote(str(case_dir))} -func 'cellMax(Co)' -latestTime"
    )
    completed = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(case_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    match = re.search(
        rf"max\(all\)\s+of\s+Co\s*=\s*({NUMBER})\s+at location\s+\(([^)]+)\)\s+in cell\s+(\d+)",
        completed.stdout,
    )
    return {
        "status": "LOCATED" if completed.returncode == 0 and match else "NOT_LOCATED",
        "exit_code": completed.returncode,
        "maximum_Co": float(match.group(1)) if match else None,
        "cell_id": int(match.group(3)) if match else None,
        "location": [float(value) for value in match.group(2).split()] if match else None,
        "log_tail": "\n".join(completed.stdout.splitlines()[-80:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--solver-log", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--locate-max-courant", action="store_true")
    parser.add_argument("--openfoam-bashrc", type=Path)
    args = parser.parse_args()
    case_dir = args.case.resolve()
    log_path = args.solver_log.resolve() if args.solver_log else solver_log(case_dir)
    report = diagnose(case_dir, log_path)
    if args.locate_max_courant:
        if args.openfoam_bashrc is None or not args.openfoam_bashrc.is_file():
            raise FileNotFoundError("--openfoam-bashrc is required to locate the maximum Co cell")
        report["maximum_courant_cell"] = locate_max_courant(case_dir, args.openfoam_bashrc)
    output = args.output or case_dir / "courant_diagnostics.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "active_limiter": report["active_limiter"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
