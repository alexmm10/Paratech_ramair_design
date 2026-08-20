#!/usr/bin/env python3
"""Resolve the OpenFOAM shell environment without relying on login-shell state.

OpenFOAM Foundation installations expose their executables and libraries from
``etc/bashrc``.  Streamlit jobs and direct Python invocations are not guaranteed
to inherit a shell that sourced that file, so every workflow stage uses this
small helper before looking up OpenFOAM commands.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping


def _version_key(path: Path) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(path.parent.parent))
    return tuple(int(value) for value in numbers) or (0,)


def find_openfoam_bashrc() -> Path | None:
    requested = os.environ.get("RAMAIR_OPENFOAM_BASHRC")
    candidates: list[Path] = []
    if requested:
        candidates.append(Path(requested).expanduser())
    for pattern in (
        "~/.local/opt/openfoam*/etc/bashrc",
        "/opt/openfoam*/etc/bashrc",
        "/usr/lib/openfoam/openfoam*/etc/bashrc",
    ):
        if pattern.startswith("~"):
            candidates.extend(Path.home().glob(pattern[2:]))
            continue
        candidates.extend(Path("/").glob(pattern.lstrip("/")))
    available = sorted({path.resolve() for path in candidates if path.is_file()}, key=_version_key, reverse=True)
    return available[0] if available else None


def _merged_path(*path_values: str | None) -> str:
    """Join search paths while preserving order and removing duplicates."""
    entries: list[str] = []
    seen: set[str] = set()
    for path_value in path_values:
        for raw_entry in (path_value or "").split(os.pathsep):
            entry = raw_entry.strip()
            if not entry:
                continue
            key = os.path.normcase(os.path.normpath(entry))
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    return os.pathsep.join(entries)


def sourced_openfoam_environment(
    base_environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, object]]:
    """Return a copy of *base_environment* extended by OpenFOAM ``bashrc``.

    The shell emits a NUL-separated environment, avoiding quoting and newline
    ambiguity.  The function never mutates ``os.environ`` itself.
    """
    environment = dict(base_environment or os.environ)
    bashrc = find_openfoam_bashrc()
    metadata: dict[str, object] = {
        "bashrc": str(bashrc) if bashrc else None,
        "sourced": False,
        "error": None,
    }
    if bashrc is None or os.name == "nt" or shutil.which("bash") is None:
        if bashrc is None:
            metadata["error"] = "OpenFOAM etc/bashrc was not found"
        elif os.name == "nt":
            metadata["error"] = "Native Windows cannot load the Linux OpenFOAM environment"
        else:
            metadata["error"] = "bash executable was not found"
        return environment, metadata

    # OpenFOAM bashrc files probe ZSH_NAME directly.  Define it before sourcing
    # so callers that use nounset (``set -u``) cannot abort the application.
    shell = (
        'set +u; ZSH_NAME="${ZSH_NAME-}"; export ZSH_NAME; '
        'source "$1" >/dev/null 2>&1; source_status=$?; env -0; exit "$source_status"'
    )
    try:
        completed = subprocess.run(
            ["bash", "-c", shell, "ramair-openfoam-env", str(bashrc)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        return environment, metadata

    if completed.returncode != 0:
        metadata["error"] = completed.stderr.decode("utf-8", errors="replace").strip() or f"bashrc returned {completed.returncode}"
        return environment, metadata

    parsed: dict[str, str] = {}
    for item in completed.stdout.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        parsed[key.decode("utf-8", errors="replace")] = value.decode("utf-8", errors="replace")
    original_path = environment.get("PATH")
    environment.update(parsed)
    # The application invokes the venv interpreter by absolute path. Sourcing
    # OpenFOAM can therefore produce a valid FOAM PATH that still omits the
    # venv ``bin`` directory and its PyFoam console scripts. Keep that runtime
    # directory first, then the sourced OpenFOAM path and the caller path.
    # Do not call ``resolve()`` here: venv Python is commonly a symlink to
    # /usr/bin/python3 and resolving it would discard the venv ``bin`` path.
    runtime_bin = str(Path(sys.executable).absolute().parent)
    environment["PATH"] = _merged_path(runtime_bin, parsed.get("PATH"), original_path)
    metadata.update(
        sourced=True,
        wm_project_dir=environment.get("WM_PROJECT_DIR"),
        wm_project_version=environment.get("WM_PROJECT_VERSION"),
        python_runtime_bin=runtime_bin,
    )
    return environment, metadata


def activate_openfoam_environment() -> dict[str, object]:
    environment, metadata = sourced_openfoam_environment()
    if bool(metadata.get("sourced")):
        os.environ.update(environment)
    return metadata


def which_openfoam(command: str, environment: Mapping[str, str] | None = None) -> str | None:
    active = environment or os.environ
    return shutil.which(command, path=active.get("PATH"))
