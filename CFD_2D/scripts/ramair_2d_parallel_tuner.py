#!/usr/bin/env python3
"""Bounded, scratch-only OpenFOAM MPI pilot used by the pre-run optimiser."""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from openfoam_environment import activate_openfoam_environment
from ramair_2d_parallel import (
    case_cell_count,
    configure_decompose_dictionary,
    decompose_load_balance,
    linux_parallel_preflight,
    parallel_profile_key,
    practical_rank_candidates,
    select_benchmark_winner,
    store_parallel_profile,
)


def _replace_entry(text: str, key: str, value: str) -> str:
    pattern = rf"\b{re.escape(key)}\s+[^;]+;"
    line = f"{key} {value};"
    return re.sub(pattern, line, text, count=1) if re.search(pattern, text) else text + "\n" + line + "\n"


def _latest_time(case: Path) -> float:
    values = []
    for path in case.iterdir():
        if path.is_dir():
            try:
                values.append(float(path.name))
            except ValueError:
                pass
    return max(values, default=0.0)


def _prepare_control(case: Path, steps: int) -> tuple[float, float]:
    path = case / "system/controlDict"
    text = path.read_text(encoding="utf-8", errors="ignore")
    start = _latest_time(case)
    delta_match = re.search(r"\bdeltaT\s+([^;]+);", text)
    delta = float(delta_match.group(1)) if delta_match else 1.0
    end = start + max(1, int(steps)) * delta
    text = _replace_entry(text, "startFrom", "latestTime")
    text = _replace_entry(text, "stopAt", "endTime")
    text = _replace_entry(text, "endTime", f"{end:.16g}")
    text = _replace_entry(text, "writeControl", "timeStep")
    text = _replace_entry(text, "writeInterval", str(max(steps + 1, 1000000)))
    text = _replace_entry(text, "purgeWrite", "1")
    path.write_text(text, encoding="utf-8")
    return start, end


def _copy_case(source: Path, target: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name for name in names
            if re.fullmatch(r"processor\d+", name)
            or name in {"postProcessing", "VTK", "quality_distributions"}
            or name.startswith("log.")
        }
    shutil.copytree(source, target, ignore=ignore)


def _run(command: list[str], cwd: Path, timeout_s: int) -> tuple[int, str, float]:
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=str(cwd), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout_s, check=False,
    )
    return completed.returncode, completed.stdout, time.perf_counter() - started


def _sustained_step_timing(
    solver_log: str, *, warmup_steps: int, fallback_wall_s: float, requested_steps: int
) -> tuple[float, int, str]:
    clocks = [
        float(value)
        for value in re.findall(
            r"ExecutionTime\s*=\s*[0-9.eE+-]+\s+s\s+ClockTime\s*=\s*([0-9.eE+-]+)",
            solver_log,
        )
    ]
    warmup = max(0, int(warmup_steps))
    if len(clocks) >= warmup + 2:
        elapsed = clocks[-1] - clocks[warmup]
        measured = len(clocks) - warmup - 1
        if elapsed > 0.0 and measured > 0:
            return elapsed / measured, measured, "solver_clock_after_warmup"
    measured = max(1, int(requested_steps) - warmup)
    return fallback_wall_s / measured, measured, "process_wall_time_fallback"


def tune(args: argparse.Namespace) -> dict[str, Any]:
    case = args.case.resolve()
    preflight = linux_parallel_preflight(case)
    if preflight.get("native_linux_filesystem") is False:
        raise RuntimeError("Parallel pilot must run from the native Linux filesystem, not /mnt/*")
    cells, source = case_cell_count(case)
    if not cells:
        raise RuntimeError("Cannot tune without an exact mesh cell count")
    physical = int(preflight.get("physical_cores") or 1)
    candidates = args.ranks or practical_rank_candidates(
        cells, physical_cores=min(physical, args.maximum_ranks),
    )
    candidates = sorted({
        int(value) for value in candidates
        if 1 < int(value) <= min(physical, int(args.maximum_ranks))
    })
    if not candidates:
        raise RuntimeError("No parallel candidate fits the physical-core limit")
    signature = json.dumps({"solver_command": args.solver_command, "stage": args.stage}, sort_keys=True)
    key = parallel_profile_key(case, solver=args.solver_command, stage=args.stage, numerical_signature=signature)
    project_root = next((parent for parent in case.parents if (parent / "CFD_2D").is_dir()), case)
    cache = args.cache or project_root / "CFD_2D/app_state/parallel_execution_profiles.json"
    results: list[dict[str, Any]] = []
    scratch_parent = project_root / "CFD_2D/scratch/parallel_tuning"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pilot_", dir=scratch_parent) as temporary:
        scratch = Path(temporary)
        fresh_case = _latest_time(case) <= 0.0
        renumber_variants = [False, True] if args.compare_renumber and fresh_case else [False]
        for ranks in candidates:
            for renumbered in renumber_variants:
                pilot = scratch / f"p{ranks}_{'renumbered' if renumbered else 'original'}"
                _copy_case(case, pilot)
                _prepare_control(pilot, args.steps)
                renumber_s = 0.0
                renumber_rc = None
                mesh_recheck_rc = None
                if renumbered:
                    renumber_rc, renumber_log, renumber_s = _run(
                        ["renumberMesh", "-overwrite"], pilot, args.timeout_s,
                    )
                    (pilot / "log.renumberMesh").write_text(renumber_log, encoding="utf-8")
                    if renumber_rc == 0:
                        mesh_recheck_rc, mesh_recheck_log, _ = _run(
                            ["checkMesh", "-allTopology", "-allGeometry"],
                            pilot,
                            args.timeout_s,
                        )
                        (pilot / "log.checkMesh.afterRenumber").write_text(
                            mesh_recheck_log, encoding="utf-8"
                        )
                    if renumber_rc or mesh_recheck_rc:
                        results.append({
                            "ranks": ranks,
                            "method": args.method,
                            "renumber": True,
                            "renumber_returncode": renumber_rc,
                            "mesh_recheck_returncode": mesh_recheck_rc,
                            "rejected": True,
                            "reason": "renumber_or_mesh_recheck_failed",
                        })
                        continue
                configure_decompose_dictionary(
                    pilot / "system/decomposeParDict", ranks, method=args.method
                )
                decompose_rc, decompose_log, decompose_s = _run(
                    ["decomposePar", "-force"], pilot, args.timeout_s,
                )
                (pilot / "log.decomposePar").write_text(decompose_log, encoding="utf-8")
                balance = decompose_load_balance(pilot / "log.decomposePar")
                row: dict[str, Any] = {
                    "ranks": ranks, "method": args.method,
                    "renumber": renumbered,
                    "renumber_time_s": renumber_s,
                    "renumber_returncode": renumber_rc,
                    "mesh_recheck_returncode": mesh_recheck_rc,
                    "decompose_time_s": decompose_s, "decompose_returncode": decompose_rc,
                    "cells_per_rank": cells / ranks, "load_balance": balance,
                }
                imbalance = float(balance.get("maximum_deviation_percent", 0.0) or 0.0)
                if decompose_rc or imbalance > 20.0:
                    row.update(
                        rejected=True,
                        reason="decomposition_failed_or_imbalance_gt_20pct",
                    )
                    results.append(row)
                    continue
                command = [
                    "mpirun", "--map-by", "core", "--bind-to", "core", "--report-bindings",
                    "-np", str(ranks), *args.solver_command.split(), "-parallel",
                ]
                solver_rc, solver_log, solver_s = _run(command, pilot, args.timeout_s)
                (pilot / "log.parallelPilot").write_text(solver_log, encoding="utf-8")
                reconstruct_rc, _, reconstruct_s = _run(
                    ["reconstructPar", "-latestTime"], pilot, args.timeout_s,
                )
                seconds_per_step, measured_steps, timing_source = _sustained_step_timing(
                    solver_log,
                    warmup_steps=args.warmup_steps,
                    fallback_wall_s=solver_s,
                    requested_steps=args.steps,
                )
                projected = (
                    renumber_s + decompose_s
                    + args.planned_steps * seconds_per_step + reconstruct_s
                )
                row.update(
                    solver_returncode=solver_rc,
                    reconstruct_returncode=reconstruct_rc,
                    measured_wall_time_s=solver_s,
                    measured_steps=measured_steps,
                    seconds_per_step=seconds_per_step,
                    timing_source=timing_source,
                    reconstruct_time_s=reconstruct_s,
                    projected_wall_time_s=projected,
                    rejected=bool(solver_rc or reconstruct_rc),
                )
                results.append(row)
    winner = select_benchmark_winner(results)
    profile = {
        "schema_version": 1,
        "profile_key": key,
        "mesh_cell_count": cells,
        "cell_count_source": source,
        "solver": args.solver_command,
        "stage": args.stage,
        "ranks": int(winner["ranks"]),
        "method": str(winner["method"]),
        "renumber": bool(winner.get("renumber")),
        "winner": winner,
        "candidates": results,
        "preflight": preflight,
        "scratch_only": True,
    }
    store_parallel_profile(cache, key, profile)
    output = args.output or case / "parallel_tuning_report.json"
    output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--solver-command", default="foamRun -solver incompressibleFluid")
    parser.add_argument("--stage", choices=["RANS", "URANS"], default="URANS")
    parser.add_argument("--ranks", type=int, nargs="*")
    parser.add_argument("--maximum-ranks", type=int, default=8)
    parser.add_argument("--method", choices=["scotch", "hierarchical"], default="scotch")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--warmup-steps", type=int, default=5)
    parser.add_argument(
        "--compare-renumber",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Compare original and renumbered mesh only when the source case is fresh.",
    )
    parser.add_argument("--planned-steps", type=int, default=10000)
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    activate_openfoam_environment()
    args = parse_args()
    print(json.dumps(tune(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
