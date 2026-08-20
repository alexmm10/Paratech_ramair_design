#!/usr/bin/env python3
"""Publish a read-only CPU/MPI/GPU audit and fixed-numerics rank decision."""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
from pathlib import Path
from typing import Any, Callable

from ramair_2d_study_registry import utc_stamp, write_json_atomic


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(command: list[str], runner: Runner) -> dict[str, Any]:
    try:
        result = runner(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "error": str(exc)}
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _lscpu_fields(text: str) -> dict[str, str]:
    wanted = {"Model name", "CPU(s)", "Thread(s) per core", "Core(s) per socket", "Socket(s)"}
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() in wanted:
            values[key.strip()] = value.strip()
    return values


def _benchmark_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [
        dict(row) for row in payload.get("records") or []
        if row.get("cores") in {1, 2, 4, 8}
        and row.get("solver_execution_time_s") is not None
    ]
    baseline = next((row for row in rows if int(row["cores"]) == 1), None)
    baseline_time = float(baseline["solver_execution_time_s"]) if baseline else None
    for row in rows:
        elapsed = float(row["solver_execution_time_s"])
        ranks = int(row["cores"])
        row["speedup_vs_1_rank"] = baseline_time / elapsed if baseline_time else None
        row["parallel_efficiency"] = (
            baseline_time / elapsed / ranks if baseline_time else None
        )
    candidates = [row for row in rows if int(row.get("solver_steps") or 0) > 0]
    recommended = min(
        candidates,
        key=lambda row: float(row["solver_execution_time_s"]),
        default=None,
    )
    steps = {int(row.get("solver_steps") or 0) for row in rows}
    return {
        "available": bool(rows),
        "numerics_invariant": bool(payload.get("numerics_invariant")),
        "same_step_count": len(steps) == 1 and bool(steps),
        "evidence_quality": "BOUNDED_FIXTURE_NOT_PRODUCTION_CAPACITY",
        "records": rows,
        "recommended_ranks": int(recommended["cores"]) if recommended else None,
    }


def build_audit(
    benchmark: dict[str, Any] | None = None,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    lscpu = _run(["lscpu"], runner)
    mpi = _run(["mpirun", "--version"], runner)
    gpu = _run([
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader",
    ], runner)
    nvcc = _run(["nvcc", "--version"], runner)
    analysis = _benchmark_analysis(benchmark or {})
    cuda_visible = bool(gpu.get("available") and gpu.get("stdout"))
    report = {
        "schema_version": 1,
        "generated_at": utc_stamp(),
        "platform": platform.platform(),
        "cpu": _lscpu_fields(str(lscpu.get("stdout") or "")),
        "memory": _run(["free", "-b"], runner),
        "mpi": mpi,
        "gpu": {
            "cuda_device_visible": cuda_visible,
            "nvidia_smi": gpu,
            "nvcc": nvcc,
        },
        "software_capability": {
            "openfoam14_solver_backend": "CPU_MPI",
            "gmsh_4_15_2_backend": "CPU",
            "gpu_solver_integrated": False,
        },
        "rank_benchmark": analysis,
        "decision": {
            "production_backend": "NATIVE_WSL_CPU_MPI",
            "recommended_ranks": analysis.get("recommended_ranks"),
            "integrate_gpu": False,
            "reason": (
                "No CUDA device is visible and the installed OpenFOAM 14/Gmsh "
                "production path is CPU based; no measured supported GPU benefit exists."
                if not cuda_visible
                else "A visible GPU alone is insufficient; no supported solver speedup was measured."
            ),
            "docker_production": False,
        },
        "safety": {
            "numerics_changed": False,
            "canonical_case_modified": False,
            "benchmark_workspace": "temporary_copy",
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-summary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = {}
    if args.benchmark_summary and args.benchmark_summary.is_file():
        benchmark = json.loads(args.benchmark_summary.read_text(encoding="utf-8-sig"))
    report = build_audit(benchmark)
    write_json_atomic(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
