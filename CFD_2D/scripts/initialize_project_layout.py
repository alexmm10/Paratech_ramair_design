#!/usr/bin/env python3
"""Create, validate or migrate the RamAir DESIGN APP folder layout safely."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from project_layout import LAYOUT, LEGACY_ALIASES, find_project_root, project_path


REQUIRED_FILES = [
    Path("preprocess_ramair_main.py"),
    Path("Generate_RamAir_Canopy_MAIN.CATScript"),
    LAYOUT["configurations"] / "default_case_config.json",
    LAYOUT["configurations"] / "ramair_catia_system_config.json",
    Path("CFD_2D/app/ramair_cfd2d_app.py"),
    Path("CFD_2D/app/workflow_backend.py"),
    Path("CFD_2D/scripts/ramair_2d_mesh_builder.py"),
    Path("CFD_2D/scripts/ramair_2d_inlet_designer.py"),
]

REQUIRED_DIRECTORIES = [
    *LAYOUT.values(),
    Path("Documents and Manuals/General"),
    Path("Documents and Manuals/CATIA"),
    Path("Documents and Manuals/CFD 2D"),
    Path("Documents and Manuals/Gmsh"),
    Path("Documents and Manuals/OpenFOAM"),
    Path("Documents and Manuals/PyFoam"),
    Path("Documents and Manuals/XFOIL and XFLR5"),
    Path("Results"),
    Path("CFD_2D/CFD_2D_inputs/config"),
    Path("CFD_2D/meshes"),
    Path("CFD_2D/openfoam_cases"),
    Path("CFD_2D/results"),
    Path("CFD_2D/reports"),
]

ROOT_FILE_MOVES = {
    Path("pytest.ini"): LAYOUT["test_support"] / "pytest.ini",
    Path("requirements-catia-preprocessor.txt"): LAYOUT["catia_utilities"] / "requirements-catia-preprocessor.txt",
    Path("VERIFY_CATIA_PACKAGE.py"): LAYOUT["catia_utilities"] / "VERIFY_CATIA_PACKAGE.py",
}

LEGACY_ROOT_ITEMS = (
    Path(".agents"),
    Path(".git"),
    Path(".pytest_cache"),
    Path("__pycache__"),
    Path("mesh_test.msh"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_alias(path: Path) -> bool:
    return path.is_symlink() or (os.name == "nt" and path.exists() and bool(path.stat().st_file_attributes & 0x400))


def _create_alias(alias: Path, target: Path) -> str:
    if alias.exists() or alias.is_symlink():
        if _is_alias(alias):
            return "existing"
        raise FileExistsError(f"Cannot create compatibility alias; a real path exists: {alias}")
    alias.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Could not create junction {alias} -> {target}: {completed.stdout.strip()}")
        subprocess.run(["attrib", "+h", str(alias)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        alias.symlink_to(os.path.relpath(target, alias.parent), target_is_directory=True)
    return "created"


def _move_without_overwrite(source: Path, destination: Path, conflicts_root: Path) -> dict[str, str] | None:
    if not source.exists() and not source.is_symlink():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.move(str(source), str(destination))
        return {"from": str(source), "to": str(destination), "method": "move"}
    conflict = conflicts_root / source.name
    counter = 1
    while conflict.exists():
        conflict = conflicts_root / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    conflict.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(conflict))
    return {"from": str(source), "to": str(conflict), "method": "preserved_conflict"}


def _document_category(path: Path) -> str | None:
    name = path.name.lower()
    if path.suffix.lower() == ".pdf" or "technical_specifications" in name:
        if "gmsh" in name:
            return "Gmsh"
        if "openfoam" in name:
            return "OpenFOAM"
        if "pyfoam" in name:
            return "PyFoam"
        if "xfoil" in name or "xflr" in name:
            return "XFOIL and XFLR5"
        if "catia" in name:
            return "CATIA"
        if "gridquality" in name or "cfd" in name:
            return "CFD 2D"
        return "General"
    return None


def organize_support_content(root: Path, conflicts_root: Path, stamp: str) -> list[dict[str, str]]:
    moved: list[dict[str, str]] = []
    for source_relative, destination_relative in ROOT_FILE_MOVES.items():
        record = _move_without_overwrite(root / source_relative, root / destination_relative, conflicts_root)
        if record:
            moved.append(record)

    legacy_destination = project_path(root, "previous_versions", "Legacy Root Items", stamp)
    for relative in LEGACY_ROOT_ITEMS:
        record = _move_without_overwrite(root / relative, legacy_destination / relative.name, conflicts_root)
        if record:
            moved.append(record)

    application_docs = project_path(root, "application_documents")
    for source in sorted(path for path in application_docs.rglob("*") if path.is_file()):
        category = _document_category(source)
        if category is None or source.parent == project_path(root, "documents", category):
            continue
        destination = project_path(root, "documents", category, source.name)
        record = _move_without_overwrite(source, destination, conflicts_root / "Documentation")
        if record:
            moved.append(record)
    for directory in sorted((path for path in application_docs.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return moved


def migrate_layout(root: Path, compatibility_aliases: bool) -> dict[str, object]:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    moved: list[dict[str, object]] = []
    aliases: list[dict[str, str]] = []
    conflicts_root = project_path(root, "previous_versions", "Layout Migration Conflicts", stamp)
    for legacy_relative, target_relative in LEGACY_ALIASES.items():
        legacy = root / legacy_relative
        target = root / target_relative
        if _is_alias(legacy):
            continue
        if not legacy.exists():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            legacy.rename(target)
            moved.append({"from": str(legacy_relative), "to": str(target_relative), "method": "rename"})
            continue
        # A partially migrated tree is merged without overwriting. Conflicts
        # are preserved with hashes for manual review.
        for source in sorted(legacy.rglob("*")):
            relative = source.relative_to(legacy)
            destination = target / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.move(str(source), str(destination))
            elif sha256(source) != sha256(destination):
                conflict = conflicts_root / legacy_relative / relative
                conflict.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(conflict))
        for directory in sorted((path for path in legacy.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            legacy.rmdir()
        except OSError:
            pass
        moved.append({"from": str(legacy_relative), "to": str(target_relative), "method": "merge"})

    for relative in REQUIRED_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)

    moved.extend(organize_support_content(root, conflicts_root, stamp))

    if compatibility_aliases:
        for legacy_relative, target_relative in LEGACY_ALIASES.items():
            alias = root / legacy_relative
            target = root / target_relative
            state = _create_alias(alias, target)
            aliases.append({"alias": str(legacy_relative), "target": str(target_relative), "state": state})

    report = {
        "schema_version": 2,
        "project_root": str(root),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "recommended_root_name": "DESIGN APP",
        "moved": moved,
        "compatibility_aliases": aliases,
        "conflicts_directory": str(conflicts_root) if conflicts_root.exists() else None,
    }
    report_path = project_path(root, "reports", "layout_migration_manifest.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def inspect_layout(root: Path, create: bool) -> dict[str, object]:
    created: list[str] = []
    missing_directories: list[str] = []
    for relative in REQUIRED_DIRECTORIES:
        path = root / relative
        if path.is_dir():
            continue
        if create:
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(relative))
        else:
            missing_directories.append(str(relative))
    missing_files = [str(relative) for relative in REQUIRED_FILES if not (root / relative).is_file()]
    return {
        "project_root": str(root),
        "created_directories": created,
        "missing_directories": missing_directories,
        "missing_required_files": missing_files,
        "status": "OK" if not missing_directories and not missing_files else "MISSING",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--compatibility-aliases", action="store_true")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    root = find_project_root(args.project_root)
    migration = migrate_layout(root, args.compatibility_aliases) if args.migrate else None
    report = inspect_layout(root, args.create)
    if migration is not None:
        report["migration"] = migration
    print(f"project_root: {root}")
    print(f"status: {report['status']}")
    for item in report["missing_directories"]:
        print(f"MISSING directory: {item}")
    for item in report["missing_required_files"]:
        print(f"MISSING file: {item}")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
