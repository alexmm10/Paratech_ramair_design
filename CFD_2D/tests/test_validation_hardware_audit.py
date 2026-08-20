from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "CFD_2D/scripts"))

from ramair_2d_hardware_audit import build_audit  # noqa: E402


def _runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    executable = command[0]
    if executable == "lscpu":
        output = (
            "CPU(s): 16\nModel name: Test CPU\nThread(s) per core: 2\n"
            "Core(s) per socket: 8\nSocket(s): 1\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")
    if executable == "mpirun":
        return subprocess.CompletedProcess(command, 0, "mpirun (Open MPI) 4.1.2", "")
    if executable == "free":
        return subprocess.CompletedProcess(command, 0, "Mem: 8000000000", "")
    return subprocess.CompletedProcess(command, 127, "", "not found")


def test_cpu_mpi_is_selected_without_measured_gpu_support() -> None:
    benchmark = {
        "numerics_invariant": True,
        "records": [
            {"cores": 1, "solver_steps": 5, "solver_execution_time_s": 20.0},
            {"cores": 2, "solver_steps": 5, "solver_execution_time_s": 11.0},
            {"cores": 4, "solver_steps": 5, "solver_execution_time_s": 7.0},
            {"cores": 8, "solver_steps": 5, "solver_execution_time_s": 8.0},
        ],
    }
    report = build_audit(benchmark, runner=_runner)
    assert report["cpu"]["Core(s) per socket"] == "8"
    assert report["rank_benchmark"]["recommended_ranks"] == 4
    assert report["rank_benchmark"]["numerics_invariant"] is True
    assert report["decision"]["production_backend"] == "NATIVE_WSL_CPU_MPI"
    assert report["decision"]["integrate_gpu"] is False
    assert report["safety"]["numerics_changed"] is False
