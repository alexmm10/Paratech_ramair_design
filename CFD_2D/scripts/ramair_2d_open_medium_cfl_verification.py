#!/usr/bin/env python3
"""Run a disposable five-step open-medium CFL diagnostic and locate max Co."""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from ramair_2d_study_registry import utc_stamp, write_json_atomic


def _read_field(path: Path) -> str:
    candidate = path if path.is_file() else path.with_name(path.name + ".gz")
    if candidate.suffix == ".gz":
        with gzip.open(candidate, "rt", encoding="utf-8", errors="replace") as stream:
            return stream.read()
    return candidate.read_text(encoding="utf-8", errors="replace")


def _internal_values(path: Path, components: int) -> np.ndarray:
    text = _read_field(path)
    match = re.search(
        r"internalField\s+nonuniform\s+List<[^>]+>\s+(\d+)\s*\((.*?)\)\s*;",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"No nonuniform internalField in {path}")
    count = int(match.group(1))
    values = np.fromstring(
        match.group(2).replace("(", " ").replace(")", " "), sep=" ", dtype=float
    )
    expected = count * components
    if values.size != expected:
        raise ValueError(f"{path}: expected {expected} values, found {values.size}")
    return values.reshape(count, components) if components > 1 else values


def _latest_time(case: Path) -> tuple[float, Path]:
    values: list[tuple[float, Path]] = []
    for path in case.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0.0:
            values.append((value, path))
    if not values:
        raise RuntimeError("No positive reconstructed time was produced")
    return max(values, key=lambda item: item[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-case", type=Path, required=True)
    parser.add_argument("--target-dt-s", type=float, default=1.0e-6)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--n-cores", type=int, default=2)
    parser.add_argument("--timeout-min", type=float, default=15.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_case.resolve()
    phase_dt = float(args.target_dt_s) * 0.25
    with tempfile.TemporaryDirectory(prefix="ramair_open_medium_cfl_") as temporary:
        temp = Path(temporary)
        run_root = temp / "open_medium_cfl"
        case = run_root / "case"
        for name in ("0", "constant", "system"):
            shutil.copytree(source / name, case / name)
        for name in ("case_config.json", "case_input_summary.json"):
            if (source / name).is_file():
                shutil.copy2(source / name, case / name)
        stage = {
            "stage": "A", "purpose": "open-medium CFL diagnostic",
            "scheme": "Euler", "dt_s": phase_dt, "start_s": 0.0,
            "end_s": max(1, int(args.steps)) * phase_dt,
            "steps": max(1, int(args.steps)), "sampling": False,
        }
        write_json_atomic(
            run_root / "stage_plan.json",
            {
                "schema_version": 2, "target_dt_s": float(args.target_dt_s),
                "stages": [stage], "steps_total": stage["steps"],
            },
        )
        write_json_atomic(
            run_root / "case_manifest.json",
            {
                "schema_version": 2, "case_id": "open_medium_cfl_diagnostic",
                "run_id": "open_medium_cfl_diagnostic", "mode": "URANS",
                "status": "READY", "case": str(case), "mesh_id": "open_medium",
                "deltaT_s": float(args.target_dt_s),
                "scientific_key": {
                    "topology": "open", "mesh_level": "medium",
                    "mesh_id": "open_medium", "deltaT_s": float(args.target_dt_s),
                },
            },
        )
        isolated_project = temp / "isolated_project"
        isolated_project.mkdir()
        command = [
            sys.executable,
            str(Path(__file__).with_name("ramair_2d_validation_staged_runner.py")),
            "--project-root", str(isolated_project), "--run-root", str(run_root),
            "--startup-mode", "progressive", "--n-cores", str(max(1, int(args.n_cores))),
            "--timeout-min", str(min(15.0, float(args.timeout_min))), "--run",
        ]
        completed = subprocess.run(
            command, cwd=str(args.project_root.resolve()), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=max(120, int(float(args.timeout_min) * 60 + 120)), check=False,
        )
        journal_path = run_root / "stage_journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.is_file() else {}
        phase = (journal.get("phases") or [{}])[-1]
        hotspot: dict[str, Any] = {"status": "NOT_AVAILABLE"}
        cell_centres: dict[str, Any] = {"status": "NOT_RUN"}
        if completed.returncode == 0:
            centres_command = ["postProcess", "-func", "writeCellCentres", "-latestTime"]
            centres = subprocess.run(
                centres_command, cwd=str(case), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=300, check=False,
            )
            cell_centres = {
                "status": "OK" if centres.returncode == 0 else "FAILED",
                "command": centres_command,
                "returncode": int(centres.returncode),
                "log_tail": (centres.stdout or "")[-3000:],
            }
            latest_value, latest = _latest_time(case)
            try:
                co = _internal_values(latest / "Co", 1)
                centres_values = _internal_values(latest / "C", 3)
                index = int(np.nanargmax(co))
                hotspot = {
                    "status": "LOCATED", "cell_index_zero_based": index,
                    "Co_max": float(co[index]),
                    "cell_centre_m": [float(value) for value in centres_values[index]],
                    "time_s": latest_value,
                }
            except (FileNotFoundError, ValueError) as exc:
                hotspot = {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
        event = dict(phase.get("openfoam_event") or {})
        report = {
            "schema_version": 1,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "diagnostic_only": True,
            "source_case": str(source),
            "target_deltaT_s": float(args.target_dt_s),
            "phase_A_deltaT_s": phase_dt,
            "steps": int(stage["steps"]),
            "returncode": int(completed.returncode),
            "terminal_reason": phase.get("terminal_reason"),
            "maximum_courant_from_log": event.get("maximum_courant"),
            "openfoam_event": event,
            "checkMesh_log": "log.checkMesh.preRun",
            "cell_centres": cell_centres,
            "courant_hotspot": hotspot,
            "output_tail": (completed.stdout or "")[-8000:],
            "temporary_case_removed": True,
            "generated_at": utc_stamp(),
        }
    write_json_atomic(args.output.resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
