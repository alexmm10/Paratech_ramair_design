#!/usr/bin/env python3
"""Create a portable RamAir source package without heavy generated outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path


ALWAYS_EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".git", ".mypy_cache"}
GENERATED_PREFIXES = {
    ".venv-cfd2d-ui",
    ".venv-cfd2d",
    "CATIA/Exports",
    "Application Support/Logs",
    "Application Support/Reports",
    "Application Support/Packages",
    "Application Support/Temp",
    "Previous Versions",
    "Results",
    "CFD_2D/app_state",
    "CFD_2D/meshes",
    "CFD_2D/openfoam_cases",
    "CFD_2D/reports",
    "CFD_2D/results",
    "CFD_2D/CFD_2D_inputs/case_package",
    "CFD_2D/CFD_2D_inputs/geometry",
    "CFD_2D/CFD_2D_inputs/previews",
    "CFD_2D/CFD_2D_inputs/validation",
    "CFD_2D/CFD_2D_inputs/inlet_design",
}
REQUIRED_ARCHIVE_MEMBERS = {
    "DESIGN APP/preprocess_ramair_main.py",
    "DESIGN APP/Generate_RamAir_Canopy_MAIN.CATScript",
    "DESIGN APP/Application Support/Configurations/default_case_config.json",
    "DESIGN APP/Application Support/Configurations/last_preprocessor_run_config.json",
    "DESIGN APP/CFD_2D/app/ramair_cfd2d_app.py",
    "DESIGN APP/CFD_2D/scripts/openfoam_environment.py",
    "DESIGN APP/CFD_2D/scripts/ramair_2d_openfoam_case_writer.py",
    "DESIGN APP/CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py",
    "DESIGN APP/CFD_2D/scripts/ramair_2d_postprocess.py",
    "DESIGN APP/Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh",
    "DESIGN APP/Documents and Manuals/OpenFOAM/OpenFOAMUserGuide-A4.pdf",
    "DESIGN APP/CFD_2D/scripts/ramair_2d_inlet_designer.py",
    "DESIGN APP/CFD_2D/CFD_2D_inputs/config/cfd2d_inlet_design_config.json",
    "DESIGN APP/Application Support/Tools/xfoil/linux/xfoil",
    "DESIGN APP/Application Support/Tools/xfoil/source/Xfoil699src.zip",
    "DESIGN APP/Application Support/Tools/xfoil/source/xfoil699-gfortran-eof.patch",
    "DESIGN APP/Documents and Manuals/Application/build_xfoil_699_wsl.sh",
}
CRITICAL_SOURCE_MEMBERS = (
    "preprocess_ramair_main.py",
    "Generate_RamAir_Canopy_MAIN.CATScript",
    "run_ramair_cfd2d_app.py",
    "CFD_2D/app/ramair_cfd2d_app.py",
    "CFD_2D/app/workflow_backend.py",
    "CFD_2D/scripts/ramair_2d_openfoam_case_writer.py",
    "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py",
    "CFD_2D/scripts/ramair_2d_postprocess.py",
)


def console_safe(text: object) -> str:
    """Keep Unicode paths printable in legacy Windows console encodings."""
    encoding = sys.stdout.encoding or "utf-8"
    return str(text).encode(encoding, errors="backslashreplace").decode(encoding)


def excluded(relative: Path, include_generated: bool, output: Path, source: Path) -> bool:
    if any(part in ALWAYS_EXCLUDED_NAMES or part.endswith(".pyc") for part in relative.parts):
        return True
    text = relative.as_posix()
    if not include_generated and any(text == prefix or text.startswith(prefix + "/") for prefix in GENERATED_PREFIXES):
        return True
    try:
        if (source / relative).resolve() == output.resolve():
            return True
    except OSError:
        pass
    return False


def collect_files(source: Path, output: Path, include_generated: bool) -> tuple[list[Path], list[str]]:
    """Walk top-down so heavy generated trees are never enumerated."""
    files: list[Path] = []
    skipped_unreadable: list[str] = []
    for directory, dir_names, file_names in os.walk(source, topdown=True, followlinks=False):
        current = Path(directory)
        kept_dirs: list[str] = []
        for name in dir_names:
            relative = (current / name).relative_to(source)
            if not excluded(relative, include_generated, output, source):
                kept_dirs.append(name)
        dir_names[:] = kept_dirs
        for name in file_names:
            path = current / name
            relative = path.relative_to(source)
            if excluded(relative, include_generated, output, source):
                continue
            try:
                if path.is_file():
                    files.append(path)
            except OSError as exc:
                skipped_unreadable.append(f"{relative.as_posix()}: {exc}")
    return files, skipped_unreadable


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-generated", action="store_true", help="Include meshes, cases, results, backups and generated CATIA folders")
    args = parser.parse_args()
    source = args.project_root.resolve()
    output = (
        args.output
        or source / "Application Support" / "Packages"
        / f"RamAir_Design_CFD_Portable_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    ).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files, skipped_unreadable = collect_files(source, output, args.include_generated)
    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_project_name": source.name,
        "include_generated": args.include_generated,
        "file_count": len(files),
        "skipped_unreadable": skipped_unreadable,
        "excluded_generated_prefixes": [] if args.include_generated else sorted(GENERATED_PREFIXES),
        "openfoam_reference_version": "14",
        "critical_source_sha256": {
            relative: sha256(source / relative)
            for relative in CRITICAL_SOURCE_MEMBERS
            if (source / relative).is_file()
        },
        "install_windows": "python run_ramair_cfd2d_app.py --install",
        "install_wsl": "bash 'Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh' --install",
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.write(path, Path("DESIGN APP") / path.relative_to(source))
        archive.writestr("DESIGN APP/PORTABLE_PACKAGE_MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        missing = REQUIRED_ARCHIVE_MEMBERS.difference(archive.namelist())
    if missing:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"Portable archive validation failed; missing: {sorted(missing)}")
    if len(manifest["critical_source_sha256"]) != len(CRITICAL_SOURCE_MEMBERS):
        output.unlink(missing_ok=True)
        raise RuntimeError("Portable archive validation failed; one or more critical source files were not hashed")
    print(console_safe(f"Portable package: {output}"))
    print(f"Files: {len(files)}")
    if skipped_unreadable:
        print(f"WARNING: skipped unreadable filesystem entries: {len(skipped_unreadable)}")
    print("Generated meshes/results are excluded." if not args.include_generated else "Generated outputs are included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
