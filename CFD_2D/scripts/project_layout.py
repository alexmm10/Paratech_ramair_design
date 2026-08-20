#!/usr/bin/env python3
"""Canonical, user-facing RamAir DESIGN APP paths.

The project previously exposed implementation names such as ``profiles`` and
``CATIA_inputs`` at its root.  New code must use the paths below.  Hidden
compatibility aliases can be created by ``initialize_project_layout.py`` so a
previously distributed CATScript or debug command remains usable.
"""
from __future__ import annotations

from pathlib import Path


LAYOUT = {
    "profiles": Path("Airfoil Profiles"),
    "configurations": Path("Application Support/Configurations"),
    "tools": Path("Application Support/Tools"),
    "packages": Path("Application Support/Packages"),
    "logs": Path("Application Support/Logs"),
    "reports": Path("Application Support/Reports"),
    "temp": Path("Application Support/Temp"),
    "test_support": Path("Application Support/Tests"),
    "catia": Path("CATIA"),
    "catia_inputs": Path("CATIA/Inputs"),
    "catia_exports": Path("CATIA/Exports"),
    "catia_utilities": Path("CATIA/Utilities"),
    "documents": Path("Documents and Manuals"),
    "application_documents": Path("Documents and Manuals/Application"),
    "results_library": Path("Results"),
    "previous_versions": Path("Previous Versions"),
    "cfd2d": Path("CFD_2D"),
}

LEGACY_ALIASES = {
    Path("profiles"): LAYOUT["profiles"],
    Path("configs"): LAYOUT["configurations"],
    Path("tools"): LAYOUT["tools"],
    Path("dist"): LAYOUT["packages"],
    Path("logs"): LAYOUT["logs"],
    Path("reports"): LAYOUT["reports"],
    Path("tmp"): LAYOUT["temp"],
    Path("CATIA_inputs"): LAYOUT["catia_inputs"],
    Path("CATIA_exports"): LAYOUT["catia_exports"],
    Path("docs"): LAYOUT["application_documents"],
    Path("previous_versions"): LAYOUT["previous_versions"],
}


def project_path(root: Path, key: str, *parts: str | Path) -> Path:
    """Return one canonical path and reject unknown layout keys."""
    try:
        base = LAYOUT[key]
    except KeyError as exc:
        raise KeyError(f"Unknown project layout key: {key}") from exc
    return root.resolve() / base.joinpath(*map(Path, parts))


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "preprocess_ramair_main.py").is_file() and (candidate / "CFD_2D").is_dir():
            return candidate
    raise FileNotFoundError(
        "RamAir project root not found; expected preprocess_ramair_main.py and CFD_2D/."
    )


def canonicalize_project_relative(path: str | Path) -> Path:
    """Translate a legacy project-relative path without touching absolute paths."""
    value = Path(path)
    if value.is_absolute() or not value.parts:
        return value
    first = Path(value.parts[0])
    target = LEGACY_ALIASES.get(first)
    if target is None:
        return value
    return target.joinpath(*value.parts[1:])

