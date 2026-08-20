#!/usr/bin/env python3
"""Run a bounded closed-coarse SIMPLE-to-URANS software smoke test."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import (
    active_workspace_root,
    initialize_study,
    load_study,
    read_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_validation_study import prepare_run


SIMPLE_ITERATIONS = 100
URANS_STEPS = 40


def _replace_entry(path: Path, name: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        rf"(?m)^(\s*{re.escape(name)}\s+)[^;]+;",
        rf"\g<1>{value};",
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"{name} is missing from {path}")
    path.write_text(updated, encoding="utf-8")


def _copy_case(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def _version(command: list[str], cwd: Path) -> str:
    if command == ["foamVersion"]:
        project = os.environ.get("WM_PROJECT", "OpenFOAM")
        version = os.environ.get("WM_PROJECT_VERSION")
        if version:
            return f"{project} {version}"
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE: {exc}"
    return completed.stdout.strip().splitlines()[0] if completed.stdout else ""


def prepare_smoke(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    study = load_study(project_root)
    if not study:
        initialize_study(project_root)
        study = load_study(project_root)
    row = next(
        item for item in study["run_matrix"]["runs"]
        if item["mesh_id"] == "closed_coarse"
    )
    source_root = (
        active_workspace_root(project_root)
        / "runs/closed/coarse"
        / str(row["run_id"])
    )
    if not (source_root / "case/system/controlDict").is_file():
        prepare_run(project_root, str(row["run_id"]))
    smoke_root = active_workspace_root(project_root) / "smoke/closed_coarse"
    case = smoke_root / "case"
    _copy_case(source_root / "case", case)
    steady_control = case / "system/steadyInitialization/controlDict"
    _replace_entry(steady_control, "endTime", str(SIMPLE_ITERATIONS))
    _replace_entry(steady_control, "writeInterval", "25")
    _replace_entry(steady_control, "purgeWrite", "2")
    transient_control = case / "system/controlDict"
    dt_s = float(row["dt_s"])
    _replace_entry(transient_control, "startFrom", "startTime")
    _replace_entry(transient_control, "startTime", "0")
    _replace_entry(transient_control, "deltaT", f"{dt_s:.12g}")
    _replace_entry(
        transient_control,
        "endTime",
        f"{URANS_STEPS * dt_s:.12g}",
    )
    _replace_entry(transient_control, "writeControl", "timeStep")
    _replace_entry(transient_control, "writeInterval", "10")
    _replace_entry(transient_control, "purgeWrite", "4")
    staged = [
        sys.executable,
        str(project_root / "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py"),
        "--case",
        str(case),
        "--solver",
        "auto",
        "--execution-backend",
        "native",
        "--n-cores",
        "8",
        "--steady-initialization",
        "--steady-only",
        "--steady-timeout-min",
        "5",
        "--steady-force-window-samples",
        "50",
        "--steady-paraview-snapshots",
        "0",
        "--no-steady-pyfoam-live-monitor",
        "--timeout-min",
        "5",
    ]
    transfer = [
        sys.executable,
        str(project_root / "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py"),
        "--case",
        str(case),
        "--solver",
        "auto",
        "--execution-backend",
        "native",
        "--n-cores",
        "8",
        "--steady-only",
        "--steady-decision",
        "start-transient",
        "--steady-paraview-snapshots",
        "0",
        "--no-steady-pyfoam-live-monitor",
        "--timeout-min",
        "5",
    ]
    transient = [
        sys.executable,
        str(project_root / "CFD_2D/scripts/ramair_2d_openfoam_runner.py"),
        "--case",
        str(case),
        "--solver",
        "auto",
        "--execution-backend",
        "native",
        "--n-cores",
        "8",
        "--timeout-min",
        "5",
        "--no-pyfoam-live-monitor",
        "--cleanup-processor-directories",
    ]
    report = {
        "schema_version": 1,
        "status": "PREPARED_NOT_EXECUTED",
        "purpose": "bounded software smoke; not an aerodynamic validation",
        "mesh_id": "closed_coarse",
        "mesh_hash": next(
            mesh["mesh_hash"]
            for mesh in study["mesh_registry"]["meshes"]
            if mesh["id"] == "closed_coarse"
        ),
        "run_id": row["run_id"],
        "deltaT_s": dt_s,
        "simple_iterations": SIMPLE_ITERATIONS,
        "urans_steps": URANS_STEPS,
        "case": str(case),
        "commands": {
            "simple": staged,
            "diagnostic_transfer_if_needed": transfer,
            "urans": transient,
        },
        "diagnostic_override_policy": (
            "If 100 SIMPLE iterations do not pass the physical transition gate, "
            "the latest bounded fields are transferred explicitly and labelled "
            "diagnostic for this software smoke only."
        ),
        "updated_at": utc_stamp(),
    }
    write_json_atomic(
        active_workspace_root(project_root)
        / "postprocess/reports/closed_coarse_bounded_smoke.json",
        report,
    )
    return report


def execute_smoke(project_root: Path, *, run: bool) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    report = prepare_smoke(project_root)
    if not run:
        return report
    case = Path(report["case"])
    records: list[dict[str, Any]] = []
    simple_command = [*report["commands"]["simple"], "--run"]
    started = time.monotonic()
    simple = subprocess.run(simple_command, cwd=str(project_root))
    records.append({
        "stage": "SIMPLE_PARTIAL",
        "returncode": simple.returncode,
        "wall_seconds": time.monotonic() - started,
        "command": simple_command,
    })
    staged = read_json(case / "staged_run_status.json", {}) or {}
    if simple.returncode != 0:
        report.update(
            status="SMOKE_SIMPLE_FAILED",
            stages=records,
            staged_status=staged.get("status"),
            finished_at=utc_stamp(),
        )
        write_json_atomic(
            active_workspace_root(project_root)
            / "postprocess/reports/closed_coarse_bounded_smoke.json",
            report,
        )
        return report
    diagnostic_transfer = staged.get("status") == "STEADY_AWAITING_USER_DECISION"
    if diagnostic_transfer:
        transfer_command = [
            *report["commands"]["diagnostic_transfer_if_needed"],
            "--run",
        ]
        started = time.monotonic()
        transfer = subprocess.run(transfer_command, cwd=str(project_root))
        records.append({
            "stage": "DIAGNOSTIC_FIELD_TRANSFER",
            "returncode": transfer.returncode,
            "wall_seconds": time.monotonic() - started,
            "command": transfer_command,
        })
        if transfer.returncode != 0:
            report.update(
                status="SMOKE_TRANSFER_FAILED",
                stages=records,
                finished_at=utc_stamp(),
            )
            write_json_atomic(
                active_workspace_root(project_root)
                / "postprocess/reports/closed_coarse_bounded_smoke.json",
                report,
            )
            return report
    transient_command = [*report["commands"]["urans"], "--run"]
    started = time.monotonic()
    transient = subprocess.run(transient_command, cwd=str(project_root))
    records.append({
        "stage": "URANS_40_STEPS",
        "returncode": transient.returncode,
        "wall_seconds": time.monotonic() - started,
        "command": transient_command,
    })
    latest_status = read_json(case / "run_status.json", {}) or {}
    transferred = read_json(case / "staged_run_status.json", {}) or {}
    report.update(
        status=(
            "SMOKE_COMPLETED_DIAGNOSTIC_TRANSFER"
            if transient.returncode == 0 and diagnostic_transfer
            else "SMOKE_COMPLETED"
            if transient.returncode == 0
            else "SMOKE_URANS_FAILED"
        ),
        solver_executed=True,
        diagnostic_transfer=diagnostic_transfer,
        stages=records,
        transient_status=latest_status,
        field_transfer=transferred.get("steady_transfer"),
        versions={
            "python": sys.version.split()[0],
            "gmsh": _version(["gmsh", "-version"], project_root),
            "openfoam": _version(["foamVersion"], project_root),
        },
        finished_at=utc_stamp(),
    )
    write_json_atomic(
        active_workspace_root(project_root)
        / "postprocess/reports/closed_coarse_bounded_smoke.json",
        report,
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = execute_smoke(args.project_root, run=args.run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not str(result["status"]).endswith("FAILED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
