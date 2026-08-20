#!/usr/bin/env python3
"""Audit the experimental Docker definition without building or running it."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_IGNORES = {
    "Results",
    "CFD_2D/validation_studies",
    "CFD_2D/openfoam_cases",
    "processor*",
    "postProcessing",
    "*.msh",
    "*.vtk",
}


def _docker_runtime() -> dict[str, Any]:
    executable = shutil.which("docker")
    if not executable:
        host = {"cli_available": False, "server_available": False, "detail": "docker CLI not found"}
    else:
        completed = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=15,
        )
        host = {
            "cli_available": True,
            "server_available": completed.returncode == 0,
            "detail": completed.stdout.strip(),
        }
    wsl_available = host["server_available"]
    wsl_detail = "same runtime as host"
    if os.name == "nt" and shutil.which("wsl.exe"):
        completed = subprocess.run(
            [
                "wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-lc",
                "command -v docker >/dev/null 2>&1 && docker info --format '{{.ServerVersion}}'",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=15,
        )
        wsl_available = completed.returncode == 0
        wsl_detail = completed.stdout.strip() or "docker unavailable in Ubuntu-22.04"
    return {
        "host": host,
        "active_wsl_server_available": wsl_available,
        "active_wsl_detail": wsl_detail,
    }


def audit(root: Path = ROOT, *, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    ignored = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    runtime_evidence = dict(runtime if runtime is not None else _docker_runtime())
    checks = {
        "openfoam14_declared": "openfoam14" in dockerfile,
        "gmsh_4_15_2_pinned": "gmsh==4.15.2" in dockerfile,
        "workspace_is_host_mounted": "./:/workspace" in compose,
        "heavy_context_excluded": REQUIRED_IGNORES <= ignored,
    }
    blockers = [
        "The image executes as root and apt packages are not pinned to immutable versions.",
        "OpenFOAM/Open MPI ABI compatibility and production scaling have not been benchmarked in the container.",
        "The existing native WSL OpenFOAM 14 path is already validated and retains canonical heavy data.",
    ]
    if not runtime_evidence.get("active_wsl_server_available"):
        blockers.insert(0, "No usable Docker server is integrated with the active WSL runtime.")
    return {
        "schema_version": 1,
        "decision": "NATIVE_WSL_PRODUCTION_DOCKER_EXPERIMENTAL",
        "production_ready": False,
        "build_or_run_performed": False,
        "static_checks": checks,
        "runtime": runtime_evidence,
        "blockers": blockers,
        "promotion_gate": (
            "Requires explicit approval plus a small serial and MPI benchmark with identical numerics, "
            "host-mounted outputs and measured benefit."
        ),
    }


def main() -> int:
    report = audit()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(report["static_checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
