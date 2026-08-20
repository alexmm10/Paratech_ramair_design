#!/usr/bin/env python3
"""Build and verify a standalone Windows package for Python + CATIA V5."""
from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


ROOT_FILES = (
    "preprocess_ramair_main.py",
    "Generate_RamAir_Canopy_MAIN.CATScript",
    "SETUP_CATIA_PREPROCESSOR_WINDOWS.bat",
    "RUN_CATIA_PREPROCESSOR_WINDOWS.bat",
)
SOURCE_TO_PACKAGE_FILES = {
    "CATIA/Utilities/VERIFY_CATIA_PACKAGE.py": "VERIFY_CATIA_PACKAGE.py",
    "CATIA/Utilities/requirements-catia-preprocessor.txt": "requirements-catia-preprocessor.txt",
    "Application Support/Configurations/default_case_config.json": "configs/default_case_config.json",
    "Application Support/Configurations/ramair_catia_system_config.json": "configs/ramair_catia_system_config.json",
    "Documents and Manuals/Application/README_CATIA_WINDOWS_PACKAGE.md": "docs/README_CATIA_WINDOWS_PACKAGE.md",
}
LAST_RUN_CONFIG = "configs/last_preprocessor_run_config.json"
CONFIG_FILES = (
    *tuple(path for path in SOURCE_TO_PACKAGE_FILES.values() if path.startswith("configs/")),
    LAST_RUN_CONFIG,
)
HELPER_FILES = ("CFD_2D/scripts/ramair_profile_utils.py",)
REQUIRED_GENERATED = (
    "CATIA_inputs/ramair_global_inputs.csv",
    "CATIA_inputs/Canopy/ramair_profile_points_for_CATIA.csv",
    "CATIA_inputs/Canopy/ramair_rib_stations.csv",
)


def copy_file(source_root: Path, stage_root: Path, relative: str, destination_relative: str | None = None) -> None:
    source = source_root / relative
    if not source.is_file():
        raise FileNotFoundError(f"Required package source is missing: {source}")
    destination = stage_root / (destination_relative or relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_config_file(path: Path) -> None:
    """Make one saved configuration portable and CAD-only."""
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("project_paths", {})["profiles_dir"] = "profiles"
    config["project_paths"]["catia_inputs_dir"] = "CATIA_inputs"
    config["project_paths"]["catia_exports_dir"] = "CATIA_exports"
    config["project_paths"]["reports_dir"] = "reports"
    config["project_paths"]["logs_dir"] = "logs"
    for key, value in (config.get("profile_inputs") or {}).items():
        if not isinstance(value, str):
            continue
        if key == "main_profile" or key.endswith("_profile"):
            profile_name = Path(value.replace("\\", "/")).name
            if profile_name:
                config["profile_inputs"][key] = f"profiles/{profile_name}"
    config.setdefault("catia_exports", {})["exports_subfolder"] = "../CATIA_exports"
    config.setdefault("optional_modules", {})["system_config_json"] = "configs/ramair_catia_system_config.json"
    config.setdefault("cfd_2d_exports", {})["enable_2d_cae_exports"] = False
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def package_configs(stage_root: Path) -> None:
    """Sanitize the editable default and preserved last-run configuration."""
    for name in ("default_case_config.json", "last_preprocessor_run_config.json"):
        package_config_file(stage_root / "configs" / name)


def copy_last_run_config(source_root: Path, stage_root: Path) -> str:
    """Copy the latest saved setup, falling back to the active editable setup."""
    last_run = source_root / "Application Support/Configurations/last_preprocessor_run_config.json"
    active = source_root / "Application Support/Configurations/default_case_config.json"
    selected = last_run if last_run.is_file() else active
    if not selected.is_file():
        raise FileNotFoundError(
            "Neither last_preprocessor_run_config.json nor default_case_config.json is available "
            f"under {source_root / 'Application Support/Configurations'}"
        )
    destination = stage_root / LAST_RUN_CONFIG
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(selected, destination)
    return "last_successful_run" if selected == last_run else "active_default_fallback"


def verify_packaged_profile_references(stage_root: Path) -> None:
    """Fail early when an editable package configuration references a missing profile."""
    missing: list[str] = []
    for config_path in sorted((stage_root / "configs").glob("*case_config.json")):
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for key, raw in (data.get("profile_inputs") or {}).items():
            if key != "main_profile" and not key.endswith("_profile"):
                continue
            profile_path = stage_root / str(raw)
            if not profile_path.is_file():
                missing.append(f"{config_path.name}:{key}={raw}")
    if missing:
        raise FileNotFoundError(
            "Portable CATIA configuration references profiles not included in the package: "
            + ", ".join(missing)
        )


def prepare_stage(source_root: Path, stage_root: Path, regenerate: bool) -> dict[str, object]:
    for relative in (*ROOT_FILES, *HELPER_FILES):
        copy_file(source_root, stage_root, relative)
    for source_relative, destination_relative in SOURCE_TO_PACKAGE_FILES.items():
        copy_file(source_root, stage_root, source_relative, destination_relative)
    last_config_source = copy_last_run_config(source_root, stage_root)
    shutil.copytree(source_root / "Airfoil Profiles", stage_root / "profiles", dirs_exist_ok=True)
    shutil.copy2(
        stage_root / "docs/README_CATIA_WINDOWS_PACKAGE.md",
        stage_root / "README_FIRST_CATIA_WINDOWS.md",
    )
    package_configs(stage_root)
    verify_packaged_profile_references(stage_root)

    # The standalone CATIA ZIP intentionally keeps its compact legacy layout;
    # adapt only the staged launchers/macro, never the canonical DESIGN APP.
    replacements = {
        "CATIA\\Utilities\\requirements-catia-preprocessor.txt": "requirements-catia-preprocessor.txt",
        "CATIA\\Utilities\\VERIFY_CATIA_PACKAGE.py": "VERIFY_CATIA_PACKAGE.py",
        "Application Support\\Configurations\\default_case_config.json": "configs\\default_case_config.json",
        "CATIA\\Inputs": "CATIA_inputs",
    }
    for relative in ("SETUP_CATIA_PREPROCESSOR_WINDOWS.bat", "RUN_CATIA_PREPROCESSOR_WINDOWS.bat"):
        path = stage_root / relative
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    macro_path = stage_root / "Generate_RamAir_Canopy_MAIN.CATScript"
    macro_path.write_text(
        macro_path.read_text(encoding="utf-8").replace('GetAbsolutePathName("CATIA\\Inputs")', 'GetAbsolutePathName("CATIA_inputs")'),
        encoding="utf-8",
    )

    for folder in ("CATIA_exports", "logs", "reports"):
        target = stage_root / folder
        target.mkdir(parents=True, exist_ok=True)
        (target / "README.txt").write_text(
            f"Generated {folder} files are written here.\n",
            encoding="ascii",
        )

    for relative in ("preprocess_ramair_main.py", "VERIFY_CATIA_PACKAGE.py", *HELPER_FILES):
        py_compile.compile(str(stage_root / relative), doraise=True)

    preprocessor_log = stage_root / "logs/preprocessor_package_build.log"
    if regenerate:
        completed = subprocess.run(
            [
                sys.executable,
                "preprocess_ramair_main.py",
                "--config",
                "configs/default_case_config.json",
            ],
            cwd=stage_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
        preprocessor_log.write_text(completed.stdout or "", encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            raise RuntimeError(
                f"Preprocessor failed while building the CATIA package (exit {completed.returncode}). "
                f"See {preprocessor_log}"
            )
    else:
        shutil.copytree(source_root / "CATIA/Inputs", stage_root / "CATIA_inputs", dirs_exist_ok=True)
        preprocessor_log.write_text("CATIA_inputs copied without regeneration.\n", encoding="ascii")

    missing = [relative for relative in REQUIRED_GENERATED if not (stage_root / relative).is_file()]
    if missing:
        raise RuntimeError(f"Generated CATIA contract is incomplete: {missing}")
    verification = subprocess.run(
        [sys.executable, "VERIFY_CATIA_PACKAGE.py"],
        cwd=stage_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    (stage_root / "reports/package_verification.txt").write_text(
        verification.stdout or "", encoding="utf-8", errors="replace"
    )
    if verification.returncode != 0:
        raise RuntimeError(f"CATIA package verification failed:\n{verification.stdout}")

    files = sorted(path for path in stage_root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "python_used_for_build": sys.executable,
        "catia_executed": False,
        "catia_inputs_regenerated": regenerate,
        "last_run_config_source": last_config_source,
        "file_count": len(files),
        "critical_sha256": {
            relative: sha256(stage_root / relative)
            for relative in (*ROOT_FILES, *CONFIG_FILES, *REQUIRED_GENERATED)
        },
        "verification": "reports/package_verification.txt",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--use-existing-catia-inputs",
        action="store_true",
        help="Copy current CATIA_inputs instead of regenerating them in the staging directory.",
    )
    args = parser.parse_args()
    source_root = args.project_root.resolve()
    output = (
        args.output
        or source_root / "Application Support/Packages" / f"RamAir_CATIA_Windows_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    ).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ramair_catia_package_") as temporary:
        stage_root = Path(temporary) / "RamAir_CATIA_Windows"
        stage_root.mkdir(parents=True)
        manifest = prepare_stage(source_root, stage_root, not args.use_existing_catia_inputs)
        (stage_root / "PACKAGE_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(stage_root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, Path(stage_root.name) / path.relative_to(stage_root))

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        required = {f"RamAir_CATIA_Windows/{relative}" for relative in (*ROOT_FILES, *REQUIRED_GENERATED)}
        missing = sorted(required.difference(names))
        if missing:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"ZIP validation failed; missing members: {missing}")
    print(f"CATIA Windows package: {output}")
    print(f"Size: {output.stat().st_size / (1024 * 1024):.2f} MiB")
    print("CATIA was not executed. Generated CSVs and macro contracts were verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
