#!/usr/bin/env python3
"""Compare one MPI case with two simultaneous cases at a fixed core budget."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def _numeric_times(case: Path) -> list[float]:
    values: list[float] = []
    for path in case.iterdir():
        if not path.is_dir():
            continue
        try:
            values.append(float(path.name))
        except ValueError:
            pass
    return sorted(value for value in values if value > 0.0)


def _replace_entry(path: Path, name: str, value: str) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    pattern = rf"(?m)^\s*{re.escape(name)}\s+[^;]+;"
    replacement = f"{name} {value};"
    if not re.search(pattern, text):
        raise ValueError(f"Missing {name} in {path}")
    path.write_text(re.sub(pattern, replacement, text, count=1), encoding="utf-8")


def _prepare(source: Path, destination: Path, ranks: int, steps: int) -> dict[str, Any]:
    shutil.copytree(source, destination)
    for path in destination.glob("processor*"):
        if path.is_dir():
            shutil.rmtree(path)
    latest = _numeric_times(destination)[-1]
    control = destination / "system/controlDict"
    delta_t_match = re.search(
        r"(?m)^\s*deltaT\s+([-+0-9.eE]+)\s*;",
        control.read_text(encoding="utf-8", errors="ignore"),
    )
    if not delta_t_match:
        raise ValueError("The benchmark requires a fixed deltaT")
    delta_t = float(delta_t_match.group(1))
    end_time = latest + steps * delta_t
    _replace_entry(control, "startFrom", "latestTime")
    _replace_entry(control, "endTime", f"{end_time:.12g}")
    _replace_entry(control, "writeControl", "timeStep")
    _replace_entry(control, "writeInterval", str(steps))
    _replace_entry(control, "purgeWrite", "1")
    decomposition = destination / "system/decomposeParDict"
    _replace_entry(decomposition, "numberOfSubdomains", str(ranks))
    decompose = subprocess.run(
        ["decomposePar", "-case", str(destination), "-force"],
        cwd=str(destination),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (destination / "log.decomposePar").write_text(decompose.stdout, encoding="utf-8")
    if decompose.returncode != 0:
        raise RuntimeError(
            f"decomposePar failed for {destination}:\n" + "\n".join(decompose.stdout.splitlines()[-80:])
        )
    return {"start_time_s": latest, "end_time_s": end_time, "deltaT_s": delta_t}


def _launch(case: Path, ranks: int) -> tuple[subprocess.Popen[str], Any, float]:
    handle = (case / "log.throughput_benchmark").open("w", encoding="utf-8")
    command = [
        "mpirun", "--bind-to", "core", "--map-by", "core", "-np", str(ranks),
        "foamRun", "-solver", "incompressibleFluid", "-parallel",
    ]
    process = subprocess.Popen(command, cwd=str(case), stdout=handle, stderr=subprocess.STDOUT, text=True)
    return process, handle, time.monotonic()


def _finish(run: tuple[subprocess.Popen[str], Any, float]) -> float:
    process, handle, started = run
    returncode = process.wait()
    elapsed = time.monotonic() - started
    handle.close()
    if returncode != 0:
        raise RuntimeError(f"Benchmark solver failed with exit code {returncode}")
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--single-ranks", type=int, default=4)
    parser.add_argument("--parallel-ranks", type=int, default=2)
    args = parser.parse_args()
    source = args.source_case.resolve()
    output = args.output.resolve()
    steps = max(5, int(args.steps))
    single_ranks = max(1, int(args.single_ranks))
    parallel_ranks = max(1, int(args.parallel_ranks))
    if single_ranks != 2 * parallel_ranks:
        raise ValueError("single-ranks must equal twice parallel-ranks for a fixed budget")
    workspace = Path(tempfile.mkdtemp(prefix="ramair_throughput_", dir="/tmp"))
    try:
        single = workspace / f"single_{single_ranks}"
        pair_a = workspace / f"pair_a_{parallel_ranks}"
        pair_b = workspace / f"pair_b_{parallel_ranks}"
        evidence = {
            "single": _prepare(source, single, single_ranks, steps),
            "pair_a": _prepare(source, pair_a, parallel_ranks, steps),
            "pair_b": _prepare(source, pair_b, parallel_ranks, steps),
        }
        single_wall = _finish(_launch(single, single_ranks))
        pair_started = time.monotonic()
        run_a = _launch(pair_a, parallel_ranks)
        run_b = _launch(pair_b, parallel_ranks)
        pair_case_walls = [_finish(run_a), _finish(run_b)]
        pair_wall = time.monotonic() - pair_started
        single_throughput = steps / single_wall
        pair_throughput = (2 * steps) / pair_wall
        report = {
            "schema_version": 1,
            "status": "COMPLETE",
            "source_case": str(source),
            "steps_per_case": steps,
            "fixed_total_core_budget": single_ranks,
            "single_ranks": single_ranks,
            "parallel_ranks_per_case": parallel_ranks,
            "evidence": evidence,
            "single": {
                "wall_s": single_wall,
                "aggregate_case_steps_per_s": single_throughput,
            },
            "parallel_pair": {
                "wall_s": pair_wall,
                "individual_wall_s": pair_case_walls,
                "aggregate_case_steps_per_s": pair_throughput,
            },
            "throughput_gain_percent": 100.0 * (pair_throughput / single_throughput - 1.0),
            "conclusion": (
                "parallel_pair" if pair_throughput > single_throughput else "single"
            ),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
