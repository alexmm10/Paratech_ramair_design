#!/usr/bin/env python3
"""Run disposable bounded A->B and C->D OpenFOAM transition checks."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import utc_stamp, write_json_atomic


COURANT_RE = re.compile(r"Courant Number mean:\s*([0-9.eE+\-]+)\s+max:\s*([0-9.eE+\-]+)")
RESIDUAL_RE = re.compile(r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+\-]+)")


def _phase_scalar_metrics(row: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(row.get("log_path") or ""))
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    courant = [float(match.group(2)) for match in COURANT_RE.finditer(text)]
    residuals: dict[str, float] = {}
    for match in RESIDUAL_RE.finditer(text):
        residuals[str(match.group(1)).strip()] = float(match.group(2))
    return {
        "phase": row.get("phase"),
        "maximum_courant": max(courant) if courant else None,
        "final_initial_residual_by_field": residuals,
        "normal_sigfpe_banner": (row.get("openfoam_event") or {}).get("normal_sigfpe_banner"),
        "openfoam_status": (row.get("openfoam_event") or {}).get("status"),
    }


def _copy_definition(source_case: Path, target_case: Path) -> None:
    for name in ("0", "constant", "system"):
        source = source_case / name
        if not source.is_dir():
            raise FileNotFoundError(source)
        shutil.copytree(source, target_case / name)
    for name in ("case_config.json", "case_input_summary.json"):
        source = source_case / name
        if source.is_file():
            shutil.copy2(source, target_case / name)


def _configure_urans_dictionaries(target_case: Path) -> None:
    """Convert copied RANS dictionaries to the canonical URANS controls."""
    solution = target_case / "system/fvSolution"
    solution.write_text(
        """FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      fvSolution;
}

solvers
{
    p { solver GAMG; tolerance 1e-7; relTol 0.01; smoother DICGaussSeidel; }
    pFinal { $p; relTol 0; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }
    UFinal { $U; relTol 0; }
    nuTilda { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }
    nuTildaFinal { $nuTilda; relTol 0; }
}
PIMPLE
{
    nOuterCorrectors 3;
    nCorrectors 2;
    nNonOrthogonalCorrectors 1;
    pRefCell 0;
    pRefValue 0;
    residualControl
    {
        p               1e-4;
        U               1e-4;
        nuTilda         1e-4;
    }
}
relaxationFactors
{
    equations { \".*\" 1; }
}
""",
        encoding="utf-8",
    )
    schemes = target_case / "system/fvSchemes"
    text = schemes.read_text(encoding="utf-8")
    text = re.sub(
        r"div\(phi,U\)\s+bounded\s+Gauss\s+linearUpwind\s+limited\s*;",
        "div(phi,U) Gauss linearUpwind limited;",
        text,
    )
    text = re.sub(
        r"div\(phi,nuTilda\)\s+bounded\s+Gauss\s+upwind\s*;",
        "div(phi,nuTilda) Gauss linearUpwind limited;",
        text,
    )
    schemes.write_text(text, encoding="utf-8")


def _plan(kind: str, target_dt: float) -> dict[str, Any]:
    if kind == "A_B":
        stages = [
            {
                "stage": "A", "purpose": "bounded transition verification",
                "scheme": "Euler", "dt_s": target_dt * 0.25,
                "start_s": 0.0, "end_s": 25.0 * target_dt * 0.25,
                "steps": 25, "sampling": False,
            },
            {
                "stage": "B", "purpose": "bounded transition verification",
                "scheme": "Euler", "dt_s": target_dt * 0.5,
                "start_s": 25.0 * target_dt * 0.25,
                "end_s": 25.0 * target_dt * 0.25 + 3.0 * target_dt * 0.5,
                "steps": 3, "sampling": False,
            },
        ]
    elif kind == "C_D":
        stages = [
            {
                "stage": "C", "purpose": "build complete backward history",
                "scheme": "Euler", "dt_s": target_dt,
                "start_s": 0.0, "end_s": 3.0 * target_dt,
                "steps": 3, "sampling": False,
            },
            {
                "stage": "D", "purpose": "bounded backward transition verification",
                "scheme": "backward", "dt_s": target_dt,
                "start_s": 3.0 * target_dt, "end_s": 5.0 * target_dt,
                "steps": 2, "sampling": False,
            },
        ]
    else:
        raise ValueError(kind)
    return {
        "schema_version": 2,
        "time_policy": "fixed_staged",
        "adjustTimeStep": False,
        "target_dt_s": target_dt,
        "stages": stages,
        "sampling_start_s": stages[-1]["start_s"],
        "sampling_end_s": stages[-1]["end_s"],
        "steps_total": sum(int(stage["steps"]) for stage in stages),
        "requires_current_and_two_previous_target_dt_states_before_backward": True,
    }


def _run_one(
    project_root: Path,
    source_case: Path,
    kind: str,
    target_dt: float,
    n_cores: int,
    timeout_min: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"ramair_urans_{kind.lower()}_") as temporary:
        run_root = Path(temporary) / kind.lower()
        isolated_project = Path(temporary) / "isolated_project"
        isolated_project.mkdir(parents=True)
        case = run_root / "case"
        _copy_definition(source_case, case)
        _configure_urans_dictionaries(case)
        plan = _plan(kind, target_dt)
        write_json_atomic(run_root / "stage_plan.json", plan)
        write_json_atomic(
            run_root / "case_manifest.json",
            {
                "schema_version": 2,
                "case_id": f"transition_{kind.lower()}",
                "run_id": f"transition_{kind.lower()}",
                "mode": "URANS",
                "status": "READY",
                "case": str(case),
                "mesh_id": "closed_coarse",
                "deltaT_s": target_dt,
                "scientific_key": {
                    "topology": "closed", "mesh_level": "coarse",
                    "mesh_id": "closed_coarse", "deltaT_s": target_dt,
                },
            },
        )
        command = [
            sys.executable,
            str(Path(__file__).with_name("ramair_2d_validation_staged_runner.py")),
            "--project-root", str(isolated_project),
            "--run-root", str(run_root),
            "--startup-mode", "progressive",
            "--n-cores", str(n_cores),
            "--timeout-min", str(timeout_min),
            "--run",
        ]
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(60, int(timeout_min * 60 + 120)),
            check=False,
        )
        journal_path = run_root / "stage_journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.is_file() else {}
        phases = list(journal.get("phases") or [])
        return {
            "kind": kind,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": int(completed.returncode),
            "command": command,
            "target_deltaT_s": target_dt,
            "expected_phases": [stage["stage"] for stage in plan["stages"]],
            "terminal_reasons": [row.get("terminal_reason") for row in phases],
            "phase_deltaT_s": [row.get("deltaT_s") for row in phases],
            "phase_start_s": [row.get("actual_start_s") for row in phases],
            "phase_end_s": [row.get("actual_end_s") for row in phases],
            "history": [row.get("backward_history") for row in phases if row.get("backward_history")],
            "phase_metrics": [_phase_scalar_metrics(row) for row in phases],
            "phase_log_names": [
                {name: Path(path).name for name, path in (row.get("operation_logs") or {}).items()}
                for row in phases
            ],
            "output_tail": (completed.stdout or "")[-8000:],
            "temporary_case_removed": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-case", type=Path, required=True)
    parser.add_argument("--target-dt-s", type=float, default=2.5e-4)
    parser.add_argument("--n-cores", type=int, default=2)
    parser.add_argument("--timeout-min", type=float, default=15.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [
        _run_one(
            args.project_root.resolve(), args.source_case.resolve(), kind,
            float(args.target_dt_s), max(1, int(args.n_cores)),
            min(float(args.timeout_min), 15.0 if kind == "A_B" else 10.0),
        )
        for kind in ("A_B", "C_D")
    ]
    report = {
        "schema_version": 1,
        "status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
        "source_case": str(args.source_case.resolve()),
        "bounded_disposable_execution": True,
        "results": results,
        "generated_at": utc_stamp(),
    }
    write_json_atomic(args.output.resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
