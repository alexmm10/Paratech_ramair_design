#!/usr/bin/env python3
"""Static and data-level verification for the standalone CATIA package.

CATIA is deliberately not started. The checker validates the Python inputs,
generated CSVs and the macro's portable folder-resolution contract.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def find_root() -> Path:
    start = Path(__file__).resolve().parent
    for candidate in [start, *start.parents]:
        if (candidate / "preprocess_ramair_main.py").is_file() and (candidate / "Generate_RamAir_Canopy_MAIN.CATScript").is_file():
            return candidate
    raise FileNotFoundError("Could not locate the RamAir project/package root.")


def read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle), [])


def verify(root: Path) -> list[str]:
    errors: list[str] = []
    canonical = (root / "CATIA/Inputs").is_dir()
    config_dir = Path("Application Support/Configurations") if canonical else Path("configs")
    catia_inputs = Path("CATIA/Inputs") if canonical else Path("CATIA_inputs")
    required_files = (
        Path("preprocess_ramair_main.py"),
        Path("Generate_RamAir_Canopy_MAIN.CATScript"),
        config_dir / "default_case_config.json",
        config_dir / "ramair_catia_system_config.json",
        Path("CFD_2D/scripts/ramair_profile_utils.py"),
        catia_inputs / "ramair_global_inputs.csv",
        catia_inputs / "Canopy/ramair_profile_points_for_CATIA.csv",
        catia_inputs / "Canopy/ramair_rib_stations.csv",
        catia_inputs / "Canopy/ramair_cell_midsections.csv",
        catia_inputs / "Canopy/ramair_crossports.csv",
    )
    for relative in required_files:
        path = root / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing_or_empty: {relative}")

    for config_name in ("default_case_config.json", "last_preprocessor_run_config.json"):
        config_path = root / config_dir / config_name
        if not config_path.is_file():
            if config_name == "last_preprocessor_run_config.json":
                errors.append(f"missing last successful configuration: {config_path.relative_to(root)}")
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            paths = config.get("project_paths", {})
            expected_inputs = catia_inputs.as_posix()
            if Path(str(paths.get("catia_inputs_dir", ""))).as_posix() != expected_inputs:
                errors.append(f"project_paths.catia_inputs_dir must be {expected_inputs}")
            for key, raw in config.get("profile_inputs", {}).items():
                if not key.endswith("profile") and key != "main_profile":
                    continue
                path = Path(str(raw))
                if path.is_absolute() or not (root / path).is_file():
                    errors.append(f"profile path is not portable or missing in {config_name}: {key}={raw}")
        except Exception as exc:
            errors.append(f"invalid {config_name}: {type(exc).__name__}: {exc}")

    launcher_path = root / "RUN_CATIA_PREPROCESSOR_WINDOWS.bat"
    if launcher_path.is_file():
        launcher = launcher_path.read_text(encoding="utf-8", errors="replace")
        if "%~dp0" not in launcher or "RAMAIR_CONFIG" not in launcher:
            errors.append("preprocessor launcher is not relocation/configuration aware")
        if canonical and "Application Support\\Configurations\\default_case_config.json" not in launcher:
            errors.append("canonical launcher does not select the editable project configuration")
        if not canonical and "configs\\default_case_config.json" not in launcher:
            errors.append("portable launcher does not select configs\\default_case_config.json")

    macro_path = root / "Generate_RamAir_Canopy_MAIN.CATScript"
    if macro_path.is_file():
        macro = macro_path.read_text(encoding="utf-8", errors="replace")
        tokens = (
            'Const BASE_FOLDER = ""',
            "RAMAIR_CATIA_INPUTS",
            'Const PROFILE_FILE = "Canopy\\ramair_profile_points_for_CATIA.csv"',
        )
        for token in tokens:
            if token not in macro:
                errors.append(f"CATScript portability token missing: {token}")
        if "C:\\Users\\" in macro or "ramair_CATIA_input_dist" in macro:
            errors.append("CATScript contains a legacy or hardcoded input path")

    csv_contracts = {
        catia_inputs / "Canopy/ramair_profile_points_for_CATIA.csv": {"section", "X_mm", "Y_span_mm", "Z_mm"},
        catia_inputs / "Canopy/ramair_rib_stations.csv": {"rib_id", "Y_flat_mm", "chord_mm"},
        catia_inputs / "ramair_global_inputs.csv": {"parameter", "value", "unit"},
    }
    for relative, required_columns in csv_contracts.items():
        path = root / relative
        if not path.is_file():
            continue
        header = set(read_csv_header(path))
        missing = sorted(required_columns.difference(header))
        if missing:
            errors.append(f"CSV columns missing in {relative}: {missing}")

    return errors


def main() -> int:
    root = find_root()
    errors = verify(root)
    if errors:
        print("CATIA PACKAGE: FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("CATIA PACKAGE: OK")
    print(f"Project root: {root}")
    print("CATIA was not executed; CSV and macro contracts passed static verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
