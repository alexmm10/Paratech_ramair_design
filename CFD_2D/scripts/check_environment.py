#!/usr/bin/env python3
"""Environment checker for the ram-air CAD/CFD workflow.

The checker is read-only: it imports lightweight Python modules when available
and uses PATH lookup for external tools. It does not launch Gmsh/OpenFOAM.
"""
from __future__ import annotations

import importlib.util
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from openfoam_environment import sourced_openfoam_environment
from ramair_2d_inlet_designer import inspect_xfoil, find_project_root as find_inlet_project_root


@dataclass
class Check:
    name: str
    status: str
    detail: str
    remediation: str = ""


INSTALL_HINTS = {
    "wsl": "From an elevated Windows PowerShell: wsl --install -d Ubuntu-22.04",
    "venv": "Run INSTALL_AND_START_RAMAIR_CFD2D_APP.bat from the DESIGN APP root.",
    "gmsh": "Run: bash 'Documents and Manuals/Application/install_gmsh_4_15_wsl.sh'",
    "XFOIL": "Run: bash 'Documents and Manuals/Application/install_xfoil_wsl.sh' (fallback: sudo apt install xfoil)",
    "OpenFOAM_environment": "Configure the current OpenFOAM Foundation repository using https://openfoam.org/download/ and rerun the installer.",
    "mpirun": "Run: sudo apt update && sudo apt install -y openmpi-bin",
    "paraview": "Run: sudo apt update && sudo apt install -y paraview",
    "pvbatch": "Run: sudo apt update && sudo apt install -y paraview",
    "gnuplot": "Run: sudo apt update && sudo apt install -y gnuplot",
    "pyFoamPlotWatcher.py": "Run the DESIGN APP installer to reinstall the pinned PyFoam environment.",
}


def remediation_for(check: Check) -> str:
    if check.status == "OK":
        return ""
    if check.name in INSTALL_HINTS:
        return INSTALL_HINTS[check.name]
    if check.name in {"numpy", "scipy", "pandas", "matplotlib", "Pillow", "pytest", "gmsh_api", "streamlit", "pyarrow_runtime", "PyFoam", "PyFoam_runtime"}:
        return "Run INSTALL_AND_START_RAMAIR_CFD2D_APP.bat to repair the pinned Python environment."
    if check.name in {
        "gmshToFoam", "checkMesh", "foamRun", "pimpleFoam", "potentialFoam",
        "decomposePar", "reconstructPar", "foamPostProcess", "postProcess",
        "foamToVTK", "paraFoam",
    }:
        return INSTALL_HINTS["OpenFOAM_environment"]
    return "Review the detail and rerun the complete DESIGN APP installer."


def module_check(name: str, package: str | None = None) -> Check:
    package = package or name
    spec = importlib.util.find_spec(package)
    if spec is None:
        return Check(name, "MISSING", f"Python package '{package}' not importable")
    try:
        found_version = importlib.metadata.version(name if name != "gmsh_api" else "gmsh")
        detail = f"Python package '{package}' found; version {found_version}"
    except importlib.metadata.PackageNotFoundError:
        detail = f"Python package '{package}' found"
    return Check(name, "OK", detail)


def executable_check(
    name: str,
    exe: str | None = None,
    required: bool = False,
    environment: dict[str, str] | None = None,
) -> Check:
    exe = exe or name
    path = shutil.which(exe, path=(environment or os.environ).get("PATH"))
    if path:
        return Check(name, "OK", path)
    return Check(name, "MISSING" if required else "WARNING", f"Executable '{exe}' not found on PATH")


def pyfoam_runtime_check() -> Check:
    try:
        from PyFoam.Execution.BasicRunner import BasicRunner  # type: ignore
        from PyFoam.RunDictionary.SolutionDirectory import SolutionDirectory  # type: ignore  # noqa: F401
        found_version = importlib.metadata.version("PyFoam")
        return Check(
            "PyFoam_runtime",
            "OK",
            f"PyFoam {found_version}; BasicRunner={BasicRunner.__module__}.{BasicRunner.__name__}",
        )
    except Exception as exc:
        return Check("PyFoam_runtime", "MISSING", f"PyFoam runtime import failed: {type(exc).__name__}: {exc}")


def pyarrow_runtime_check() -> Check:
    """Report the native Arrow runtime used by Streamlit.

    PyArrow 25.0.0 reproducibly segfaults in libarrow.so.2500 in the current
    Ubuntu 22.04/WSL application environment.  A plain import check misses
    that failure because the crash occurs later, during table serialization.
    """
    spec = importlib.util.find_spec("pyarrow")
    if spec is None:
        return Check("pyarrow_runtime", "MISSING", "Python package 'pyarrow' not importable")
    try:
        found_version = importlib.metadata.version("pyarrow")
    except importlib.metadata.PackageNotFoundError:
        return Check("pyarrow_runtime", "WARNING", "PyArrow importable but its version could not be determined")
    if os.environ.get("WSL_DISTRO_NAME") and found_version == "25.0.0":
        return Check(
            "pyarrow_runtime",
            "WARNING",
            "PyArrow 25.0.0 is unstable in this WSL UI environment (libarrow.so.2500 SIGSEGV); "
            "run bash 'Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh' --install to restore pyarrow 18.1.0",
        )
    return Check("pyarrow_runtime", "OK", f"PyArrow {found_version}")


def gmsh_check() -> Check:
    requested = os.environ.get("RAMAIR_GMSH_EXECUTABLE")
    candidates = [Path(requested).expanduser()] if requested else []
    candidates += [Path.home() / ".local" / "opt" / "gmsh-4.15.2" / "bin" / "gmsh"]
    path = next((str(candidate.resolve()) for candidate in candidates if candidate.is_file()), None) or shutil.which("gmsh")
    if not path:
        return Check("gmsh", "MISSING", "Gmsh not found. Run 'Documents and Manuals/Application/install_gmsh_4_15_wsl.sh' in WSL.")
    try:
        output = subprocess.run([path, "--version"], check=False, text=True, capture_output=True, timeout=15)
        text = (output.stdout or output.stderr or "").strip()
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
        version = tuple(int(value or 0) for value in match.groups()) if match else None
    except Exception as exc:
        return Check("gmsh", "WARNING", f"{path}; version check failed: {exc}")
    if not version:
        return Check("gmsh", "WARNING", f"{path}; version could not be parsed from '{text}'")
    detail = f"{path}; version {match.group(0)}"
    if version < (4, 10, 0):
        return Check("gmsh", "WARNING", detail + "; too old for the curved BoundaryLayer workflow, use 4.15.2")
    return Check("gmsh", "OK", detail)


def python_check() -> list[Check]:
    checks = [
        Check("python", "OK", sys.executable),
        Check("python_version", "OK", sys.version.replace("\n", " ")),
        Check("platform", "OK", platform.platform()),
        Check("wsl", "OK" if os.environ.get("WSL_DISTRO_NAME") else "WARNING", os.environ.get("WSL_DISTRO_NAME", "Not running inside WSL")),
    ]
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        checks.append(Check("venv", "WARNING", "No active virtual environment detected"))
    else:
        checks.append(Check("venv", "OK", sys.prefix))
    return checks


def run_checks() -> list[Check]:
    checks: list[Check] = []
    foam_environment, foam_metadata = sourced_openfoam_environment()
    checks.extend(python_check())
    checks.extend([
        module_check("numpy"),
        module_check("scipy"),
        module_check("pandas"),
        module_check("matplotlib"),
        module_check("Pillow", "PIL"),
        module_check("pytest"),
        module_check("gmsh_api", "gmsh"),
        module_check("streamlit"),
        pyarrow_runtime_check(),
        module_check("PyFoam"),
        pyfoam_runtime_check(),
        gmsh_check(),
    ])
    try:
        xfoil = inspect_xfoil(find_inlet_project_root())
        checks.append(Check("XFOIL", xfoil.status, f"{xfoil.executable or 'not found'}; {xfoil.detail}"))
    except Exception as exc:
        checks.append(Check("XFOIL", "WARNING", f"XFOIL probe failed: {type(exc).__name__}: {exc}"))
    if foam_metadata.get("sourced"):
        checks.append(Check(
            "OpenFOAM_environment",
            "OK",
            f"sourced {foam_metadata.get('bashrc')}; WM_PROJECT_DIR={foam_metadata.get('wm_project_dir')}",
        ))
    else:
        checks.append(Check(
            "OpenFOAM_environment",
            "MISSING",
            str(foam_metadata.get("error") or "OpenFOAM environment could not be loaded"),
        ))
    for command in [
        "gmshToFoam", "checkMesh", "foamRun", "pimpleFoam", "potentialFoam",
        "decomposePar", "reconstructPar", "foamPostProcess", "postProcess",
        "foamToVTK", "mpirun", "paraFoam", "paraview", "pvbatch", "gnuplot",
        "pyFoamPlotWatcher.py",
    ]:
        checks.append(executable_check(command, environment=foam_environment))
    return checks


def main() -> None:
    checks = run_checks()
    for check in checks:
        check.remediation = remediation_for(check)
    for c in checks:
        print(f"{c.status:8} {c.name:16} {c.detail}")
        if c.remediation:
            print(f"         ACTION          {c.remediation}")
    out = Path("CFD_2D") / "reports" / "environment_report.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([asdict(c) for c in checks], indent=2), encoding="utf-8")
        print(f"\nReport written: {out}")
    except Exception as exc:
        print(f"\nWARNING  environment_report could not be written: {exc}")


if __name__ == "__main__":
    main()
