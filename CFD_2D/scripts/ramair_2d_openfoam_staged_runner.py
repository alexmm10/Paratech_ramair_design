#!/usr/bin/env python3
"""Optional SIMPLE initialization followed by the transient OpenFOAM runner.

The script is an explicit orchestrator: dry-run is the default, it never mixes
steady iteration histories with transient physical time, and it preserves the
steady stage before transferring reconstructed fields into transient ``0/``.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

from openfoam_history import read_force_coefficient_history
from paraview_case_viewer import prepare_paraview_case
from ramair_2d_postprocess import write_aerodynamic_efficiency_products


# Transfer fields are discovered from the transient 0/ template and the latest
# SIMPLE directory. This supports SA, k-omega SST and other RANS models without
# silently dropping a model-specific transported field.
FIELD_CLASSES = {
    "volScalarField",
    "volVectorField",
    "volSphericalTensorField",
    "volSymmTensorField",
    "volTensorField",
    "surfaceScalarField",
    "surfaceVectorField",
}
OPTIONAL_RESTART_FIELDS = {"phi", "nut", "alphat"}
PRIMARY_TURBULENCE_FIELDS = {
    "nuTilda",
    "k",
    "omega",
    "epsilon",
    "v2",
    "f",
    "gammaInt",
    "ReThetat",
}
FIELD_TRANSFER_ORDER = (
    "U",
    "p",
    "phi",
    "nuTilda",
    "k",
    "omega",
    "epsilon",
    "v2",
    "f",
    "gammaInt",
    "ReThetat",
    "nut",
    "alphat",
)
TRANSIENT_SYSTEM_FILES = ("controlDict", "fvSchemes", "fvSolution")
PENDING_STATE_NAME = "pending_stage.json"


def _hardlink_or_copy(source: str, destination: str) -> str:
    """Reuse mesh/field disk blocks on Linux and remain portable elsewhere."""
    try:
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).hardlink_to(Path(source))
        return destination
    except (OSError, NotImplementedError):
        return shutil.copy2(source, destination)


def _selected_steady_snapshots(
    positive: list[tuple[float, Path]],
    count: int,
) -> list[tuple[float, Path]]:
    count = max(2, int(count))
    if len(positive) <= count:
        return positive
    indices = {
        int(round(index * (len(positive) - 1) / (count - 1)))
        for index in range(count)
    }
    return [positive[index] for index in sorted(indices)]


def create_steady_paraview_case(
    case_dir: Path,
    archive: Path,
    positive: list[tuple[float, Path]],
    snapshot_count: int,
) -> dict[str, Any]:
    """Create a standalone steady-iteration case without duplicating disk blocks."""
    target = archive / "paraview_case"
    if int(snapshot_count) <= 0:
        if target.exists():
            shutil.rmtree(target)
        return {
            "status": "SKIPPED_COMPACT_STORAGE",
            "reason": "RANS compact profile does not create automatic ParaView snapshots.",
            "snapshot_count_requested": int(snapshot_count),
            "time_semantics": "SIMPLE iteration counter; values are not physical seconds",
        }
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    constant = case_dir / "constant"
    if constant.is_dir():
        shutil.copytree(
            constant,
            target / "constant",
            copy_function=_hardlink_or_copy,
            symlinks=True,
        )
    (target / "system").mkdir(parents=True, exist_ok=True)
    for name in ("case_input_summary.json", "case_config.json"):
        source = case_dir / name
        if source.is_file():
            shutil.copy2(source, target / name)
    steady_templates = case_dir / "system" / "steadyInitialization"
    for name in TRANSIENT_SYSTEM_FILES:
        source = steady_templates / name
        if source.is_file():
            shutil.copy2(source, target / "system" / name)
    initial_zero = archive / "initial_zero"
    initial_snapshot_included = initial_zero.is_dir()
    if initial_snapshot_included:
        shutil.copytree(
            initial_zero,
            target / "0",
            copy_function=_hardlink_or_copy,
            symlinks=True,
        )
    selected = _selected_steady_snapshots(positive, snapshot_count)
    for _, source in selected:
        shutil.copytree(
            source,
            target / source.name,
            copy_function=_hardlink_or_copy,
            symlinks=True,
        )
    marker = target / "steady_initialization.foam"
    marker.touch()
    control_dict = target / "system" / "controlDict"
    if control_dict.is_file():
        prepared_view = prepare_paraview_case(target)
    else:
        prepared_view = {
            "status": "NOT_PREPARED",
            "reason": "The failed steady stage did not provide a controlDict for ParaView.",
        }
    manifest = {
        "status": "PREPARED_FOR_PARAVIEW",
        "render_verified": False,
        "render_verification_note": (
            "The package and absolute startup script exist. ParaView alone writes "
            "case_latest.ready.json after it has loaded and rendered the OpenFOAM case."
        ),
        "time_semantics": "SIMPLE iteration counter; values are not physical seconds",
        "source_case": str(case_dir.resolve()),
        "all_steady_iterations": [value for value, _ in positive],
        "paraview_snapshots": (
            ([0.0] if initial_snapshot_included else [])
            + [value for value, _ in selected]
        ),
        "initial_snapshot_included": initial_snapshot_included,
        "snapshot_count_requested": int(snapshot_count),
        "foam_marker": str(marker),
        "scripted_paraview": prepared_view,
        "storage_note": "Files use hard links on Linux when possible; packaged archives contain normal file data.",
    }
    (target / "steady_paraview_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_steady_efficiency(case_or_archive: Path, archive: Path) -> dict[str, Any]:
    records, sources = read_force_coefficient_history(case_or_archive, include_processor0=True)
    if not records:
        return {"status": "NOT_AVAILABLE", "reason": "steady forceCoeffs history missing"}
    frame = pd.DataFrame(records)
    skip = min(20, max(0, len(frame) // 10))
    display = frame.iloc[skip:].copy()
    result = write_aerodynamic_efficiency_products(
        display,
        archive / "aerodynamic_efficiency_steady.csv",
        archive / "aerodynamic_efficiency_steady.png",
        x_label="SIMPLE iteration",
        title="Steady initialization aerodynamic efficiency",
        mean_from_fraction=0.6,
    )
    result.update(
        source_files=[str(path) for path in sources],
        startup_samples_omitted=skip,
        time_semantics="SIMPLE iteration counter; not physical time",
    )
    return result


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def numeric_time_dirs(case_dir: Path) -> list[tuple[float, Path]]:
    found: list[tuple[float, Path]] = []
    for path in case_dir.iterdir() if case_dir.is_dir() else []:
        if not path.is_dir():
            continue
        try:
            found.append((float(path.name), path))
        except ValueError:
            continue
    return sorted(found)


def runner_command(
    args: argparse.Namespace,
    *,
    timeout_min: float,
    resume: bool,
    include_transient_convergence: bool = True,
    live_monitor: bool | None = None,
    include_resume_extension: bool = True,
    potential_foam: bool = False,
) -> list[str]:
    runner = Path(__file__).with_name("ramair_2d_openfoam_runner.py")
    command = [
        sys.executable,
        str(runner),
        "--case", str(args.case.resolve()),
        "--solver", args.solver,
        "--execution-backend", args.execution_backend,
        "--n-cores", str(max(1, int(args.n_cores))),
        "--timeout-min", str(float(timeout_min)),
        "--stop-grace-min", str(float(args.stop_grace_min)),
        "--stop-mode", args.stop_mode,
    ]
    command.append(
        "--automatic-core-selection" if args.automatic_core_selection
        else "--no-automatic-core-selection"
    )
    command.append(
        "--renumber-before-decompose" if args.renumber_before_decompose
        else "--no-renumber-before-decompose"
    )
    if args.run:
        command.append("--run")
    if potential_foam:
        command.append("--potentialFoam")
    if include_transient_convergence and args.stop_after_min is not None and args.stop_after_min > 0:
        command += ["--stop-after-min", str(float(args.stop_after_min))]
    if not args.stop_if_checkmesh_fails:
        command.append("--no-stop-if-checkMesh-fails")
    if args.pyfoam_live_monitor if live_monitor is None else live_monitor:
        command.append("--pyfoam-live-monitor")
    if not args.cleanup_processor_directories:
        command.append("--keep-processor-directories")
    if resume:
        command.append("--resume")
        if include_resume_extension and args.resume_additional_time_star is not None:
            command += ["--resume-additional-time-star", str(float(args.resume_additional_time_star))]
    if args.stop_when_force_stable and include_transient_convergence:
        command += [
            "--stop-when-force-stable",
            "--convergence-minimum-time-star", str(float(args.convergence_minimum_time_star)),
            "--convergence-window-time-star", str(float(args.convergence_window_time_star)),
            "--convergence-mean-tolerance", str(float(args.convergence_mean_tolerance)),
            "--convergence-oscillation-tolerance", str(float(args.convergence_oscillation_tolerance)),
        ]
    return command


def install_steady_templates(case_dir: Path, archive: Path) -> None:
    template = case_dir / "system" / "steadyInitialization"
    ensure_simple_pressure_reference(template / "fvSolution")
    ensure_potential_phi_solver(template / "fvSolution")
    ensure_steady_stability_numerics(template)
    missing = [name for name in TRANSIENT_SYSTEM_FILES if not (template / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Steady templates missing under {template}: {', '.join(missing)}. Rewrite the OpenFOAM case first."
        )
    (archive / "transient_system_before_steady").mkdir(parents=True, exist_ok=True)
    initial_zero = archive / "initial_zero"
    if not initial_zero.exists():
        shutil.copytree(case_dir / "0", initial_zero)
    for name in TRANSIENT_SYSTEM_FILES:
        shutil.copy2(case_dir / "system" / name, archive / "transient_system_before_steady" / name)
        shutil.copy2(template / name, case_dir / "system" / name)


def reactivate_steady_templates(case_dir: Path) -> None:
    template = case_dir / "system" / "steadyInitialization"
    ensure_simple_pressure_reference(template / "fvSolution")
    ensure_potential_phi_solver(template / "fvSolution")
    ensure_steady_stability_numerics(template)
    missing = [name for name in TRANSIENT_SYSTEM_FILES if not (template / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Steady templates missing under {template}: {', '.join(missing)}")
    for name in TRANSIENT_SYSTEM_FILES:
        shutil.copy2(template / name, case_dir / "system" / name)


def ensure_simple_pressure_reference(path: Path) -> bool:
    """Upgrade older generated SIMPLE templates that predate pRefCell support."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "pRefCell" in text or not re.search(r"\bSIMPLE\s*\{", text):
        return False
    updated = re.sub(
        r"(\bSIMPLE\s*\{)",
        r"\1\n    pRefCell        0;\n    pRefValue       0;",
        text,
        count=1,
    )
    path.write_text(updated, encoding="utf-8")
    return True


def ensure_potential_phi_solver(path: Path) -> bool:
    """Add the OpenFOAM 13/14 potentialFoam field solver to older templates."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(?m)^\s*Phi\s*$\s*\{", text):
        return False
    match = re.search(r"\bsolvers\s*\{", text)
    if match is None:
        raise ValueError(f"Cannot add the Phi solver: no solvers dictionary in {path}")
    phi_solver = (
        "\n    Phi\n"
        "    {\n"
        "        solver          GAMG;\n"
        "        smoother        DIC;\n"
        "        tolerance       1e-6;\n"
        "        relTol          0.01;\n"
        "    }\n"
    )
    updated = text[:match.end()] + phi_solver + text[match.end():]
    path.write_text(updated, encoding="utf-8")
    return True


def ensure_steady_stability_numerics(template: Path) -> list[str]:
    """Upgrade only known legacy generated SIMPLE defaults.

    Exact replacements upgrade project-generated legacy profiles. The v3
    bootstrap keeps bounded first-order convection for a robust initial field,
    while using moderate relaxation so SIMPLE is useful rather than needlessly
    slow. Final transient dictionaries remain untouched.
    """
    stage_config_path = template / "stage_config.json"
    stage_config = read_json(stage_config_path, {}) or {}
    if (
        int(stage_config.get("config_schema_version", 0) or 0) >= 5
        and stage_config.get("geometry_topology") in {"closed_external_airfoil", "open_internal_cavity"}
    ):
        return []

    changes: list[str] = []
    schemes_path = template / "fvSchemes"
    if schemes_path.is_file():
        text = schemes_path.read_text(encoding="utf-8", errors="ignore")
        updated = text
        updated = updated.replace(
            "gradSchemes { default Gauss linear; }",
            "gradSchemes\n{\n    default          Gauss linear;\n"
            "    grad(U)          cellLimited Gauss linear 1;\n"
            "    grad(nuTilda)    cellLimited Gauss linear 1;\n}",
        )
        updated = updated.replace(
            "div(phi,nuTilda) bounded Gauss linearUpwind grad(nuTilda);",
            "div(phi,nuTilda) bounded Gauss upwind;",
        )
        updated = updated.replace(
            "grad(U)          cellLimited Gauss linear 1;",
            "grad(U)          cellLimited Gauss linear 0.5;",
        ).replace(
            "grad(nuTilda)    cellLimited Gauss linear 1;",
            "grad(nuTilda)    cellLimited Gauss linear 0.5;",
        ).replace(
            "div(phi,U) bounded Gauss linearUpwind grad(U);",
            "div(phi,U) bounded Gauss upwind;",
        )
        if updated != text:
            schemes_path.write_text(updated, encoding="utf-8")
            changes.append("fvSchemes")
    solution_path = template / "fvSolution"
    if solution_path.is_file():
        text = solution_path.read_text(encoding="utf-8", errors="ignore")
        updated = text.replace(
            "nNonOrthogonalCorrectors 1;",
            "nNonOrthogonalCorrectors 0;",
        ).replace(
            "equations { U 0.7; nuTilda 0.7; }",
            "equations { U 0.5; nuTilda 0.5; }",
        ).replace(
            "fields { p 0.2; }",
            "fields { p 0.3; }",
        ).replace(
            "equations { U 0.5; nuTilda 0.3; }",
            "equations { U 0.5; nuTilda 0.5; }",
        ).replace(
            "equations { U 0.3; nuTilda 0.2; }",
            "equations { U 0.5; nuTilda 0.5; }",
        ).replace(
            "p { solver GAMG; tolerance 1e-6; relTol 0.1; smoother GaussSeidel; }",
            "p { solver GAMG; smoother DIC; tolerance 1e-8; relTol 0.01; }",
        ).replace(
            "U { solver smoothSolver; smoother GaussSeidel; nSweeps 2; tolerance 1e-8; relTol 0.1; }",
            "U { solver PBiCGStab; preconditioner DILU; tolerance 1e-10; relTol 0.05; }",
        ).replace(
            "nuTilda { solver smoothSolver; smoother GaussSeidel; nSweeps 2; tolerance 1e-8; relTol 0.1; }",
            "nuTilda { solver PBiCGStab; preconditioner DILU; tolerance 1e-10; relTol 0.05; }",
        )
        if updated != text:
            solution_path.write_text(updated, encoding="utf-8")
            changes.append("fvSolution")
    v3_ready = (
        schemes_path.is_file()
        and "div(phi,U) bounded Gauss upwind;" in schemes_path.read_text(encoding="utf-8", errors="ignore")
        and solution_path.is_file()
        and "equations { U 0.5; nuTilda 0.5; }" in solution_path.read_text(encoding="utf-8", errors="ignore")
    )
    target_profile = "balanced_sa_initialization_v3" if v3_ready else stage_config.get("numerics_profile", "custom")
    stored_numerics = stage_config.get("numerics") if isinstance(stage_config.get("numerics"), dict) else {}
    v3_metadata_stale = bool(v3_ready and (
        stored_numerics.get("div_phi_U") != "bounded Gauss upwind"
        or stored_numerics.get("relaxation") != {"p": 0.3, "U": 0.5, "nuTilda": 0.5}
    ))
    if changes or stage_config.get("numerics_profile") != target_profile or v3_metadata_stale:
        stage_config.update({
            "config_schema_version": 4,
            "numerics_profile": target_profile,
            "compatibility_upgrade_applied": changes,
            "compatibility_upgrade_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if v3_ready:
            numerics = dict(stage_config.get("numerics") or {})
            numerics.update({
                "grad_U": "cellLimited Gauss linear 0.5",
                "grad_nuTilda": "cellLimited Gauss linear 0.5",
                "div_phi_U": "bounded Gauss upwind",
                "div_phi_nuTilda": "bounded Gauss upwind",
                "pressure_solver": "GAMG/DIC tolerance=1e-8 relTol=0.01",
                "transport_solver": "PBiCGStab/DILU tolerance=1e-10 relTol=0.05",
                "relaxation": {"p": 0.3, "U": 0.5, "nuTilda": 0.5},
            })
            stage_config["numerics"] = numerics
        stage_config_path.write_text(json.dumps(stage_config, indent=2) + "\n", encoding="utf-8")
    return changes


def restore_transient_system(case_dir: Path, archive: Path) -> None:
    source = archive / "transient_system_before_steady"
    for name in TRANSIENT_SYSTEM_FILES:
        shutil.copy2(source / name, case_dir / "system" / name)


def parse_last_initial_residuals(log_path: Path) -> dict[str, float]:
    residuals: dict[str, float] = {}
    pattern = re.compile(r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+-]+)")
    if not log_path.is_file():
        return residuals
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.search(line)
        if match:
            residuals[match.group(1).strip()] = float(match.group(2))
    return residuals


def steady_force_plateau(
    case_dir: Path,
    window_samples: int,
    mean_tolerance_percent: float,
    fluctuation_tolerance_percent: float,
) -> dict[str, Any]:
    history, sources = read_force_coefficient_history(case_dir, include_processor0=True)
    width = max(10, int(window_samples))
    if len(history) < 2 * width:
        return {"status": "WAITING", "reason": "insufficient_force_samples", "samples": len(history), "required": 2 * width, "sources": sources}
    previous = history[-2 * width:-width]
    current = history[-width:]
    metrics: dict[str, Any] = {}
    stable = True
    scale_floors = {"Cl": 0.05, "Cd": 0.005, "Cm": 0.005, "Cl_over_Cd": 1.0}

    def coefficient_values(rows: list[dict[str, float]], label: str) -> list[float] | None:
        if label != "Cl_over_Cd":
            if any(label not in row or not math.isfinite(float(row[label])) for row in rows):
                return None
            return [float(row[label]) for row in rows]
        values: list[float] = []
        for row in rows:
            if "Cl" not in row or "Cd" not in row:
                return None
            cl = float(row["Cl"])
            cd = float(row["Cd"])
            if not (math.isfinite(cl) and math.isfinite(cd)) or abs(cd) <= 1.0e-12:
                return None
            values.append(cl / cd)
        return values

    for label in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
        previous_values = coefficient_values(previous, label)
        current_values = coefficient_values(current, label)
        if previous_values is None or current_values is None:
            metrics[label] = {"stable": False, "reason": "coefficient_missing"}
            stable = False
            continue
        previous_mean = sum(previous_values) / len(previous_values)
        current_mean = sum(current_values) / len(current_values)
        scale = max(abs(previous_mean), abs(current_mean), scale_floors[label])
        mean_change_percent = 100.0 * abs(current_mean - previous_mean) / scale
        midpoint = max(1, len(current_values) // 2)
        first_half = current_values[:midpoint]
        second_half = current_values[midpoint:] or current_values[-1:]
        drift_percent = 100.0 * abs(
            sum(second_half) / len(second_half) - sum(first_half) / len(first_half)
        ) / scale
        standard_deviation = statistics.pstdev(current_values) if len(current_values) > 1 else 0.0
        fluctuation_percent = 100.0 * standard_deviation / scale
        item_stable = (
            mean_change_percent <= mean_tolerance_percent
            and drift_percent <= mean_tolerance_percent
            and fluctuation_percent <= fluctuation_tolerance_percent
        )
        stable = stable and item_stable
        metrics[label] = {
            "previous_mean": previous_mean,
            "current_mean": current_mean,
            "normalization_scale": scale,
            "mean_change_percent": mean_change_percent,
            "current_window_drift_percent": drift_percent,
            "current_standard_deviation": standard_deviation,
            "current_fluctuation_percent": fluctuation_percent,
            "mean_tolerance_percent": mean_tolerance_percent,
            "fluctuation_tolerance_percent": fluctuation_tolerance_percent,
            "stable": item_stable,
        }
    return {
        "status": "STABLE" if stable and len(metrics) == 4 else "UNSTABLE",
        "window_samples": width,
        "samples_compared": 2 * width,
        "mean_tolerance_percent": mean_tolerance_percent,
        "fluctuation_tolerance_percent": fluctuation_tolerance_percent,
        "normalization_note": "Percentages use max(abs(previous mean), abs(current mean), coefficient floor) to remain defined near zero.",
        "metrics": metrics,
        "sources": sources,
    }


def evaluate_steady_transition(case_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    stage_cfg = read_json(case_dir / "system" / "steadyInitialization" / "stage_config.json", {}) or {}
    limits = stage_cfg.get("residual_control", {}) or {}
    logs = [path for path in case_dir.glob("*PyFoamRunner*") if path.is_file()]
    logs += [path for path in case_dir.glob("log.foamRun") if path.is_file()]
    log_path = max(logs, key=lambda path: path.stat().st_mtime) if logs else case_dir / "log.foamRun"
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.is_file() else ""
    last_residuals = parse_last_initial_residuals(log_path)
    residual_metrics: dict[str, Any] = {}
    residuals_acceptable = True
    for field, tolerance in limits.items():
        matching = [value for name, value in last_residuals.items() if name == field or name.startswith(field)]
        actual = max(matching) if matching else None
        acceptable = actual is not None and actual <= float(tolerance)
        residuals_acceptable = residuals_acceptable and acceptable
        residual_metrics[field] = {"last_initial_residual": actual, "tolerance": float(tolerance), "acceptable": acceptable}
    native_convergence_message = "SIMPLE solution converged" in log_text
    force_report = steady_force_plateau(
        case_dir,
        args.steady_force_window_samples,
        args.steady_force_mean_tolerance_percent,
        args.steady_force_fluctuation_tolerance_percent,
    )
    latest_iteration = max(
        (value for value, _ in numeric_time_dirs(case_dir) if value > 0.0),
        default=0.0,
    )
    maximum_iterations = int(stage_cfg.get("maximum_iterations") or 0)
    maximum_reached = bool(
        maximum_iterations > 0
        and latest_iteration >= maximum_iterations - max(1.0, 1.0e-9 * maximum_iterations)
    )
    statistically_stable = (
        (native_convergence_message or residuals_acceptable)
        and force_report.get("status") == "STABLE"
    )
    # The validation contract has exactly two valid automatic exits from
    # SIMPLE: statistical/residual stability, or the configured hard iteration
    # ceiling.  A wall-clock timeout alone never authorizes URANS.
    transition_ready = statistically_stable or maximum_reached
    return {
        "status": "READY_FOR_TRANSIENT" if transition_ready else "NOT_READY_FOR_TRANSIENT",
        "transition_reason": (
            "STABLE_RANS" if statistically_stable
            else "MAXIMUM_RANS_ITERATIONS_REACHED" if maximum_reached
            else "RANS_NOT_STABLE_AND_MAXIMUM_NOT_REACHED"
        ),
        "latest_iteration": latest_iteration,
        "maximum_iterations": maximum_iterations,
        "maximum_iterations_reached": maximum_reached,
        "statistically_stable": statistically_stable,
        "native_simple_convergence_message": native_convergence_message,
        "residuals_acceptable": residuals_acceptable,
        "residual_metrics": residual_metrics,
        "force_plateau": force_report,
        "solver_log": str(log_path),
    }


def _read_field_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_field_text(path: Path, text: str, compressed: bool) -> Path:
    text = re.sub(r'\blocation\s+"[^"]+"\s*;', 'location    "0";', text, count=1)
    target = path.with_suffix(path.suffix + ".gz") if compressed else path
    if compressed:
        with gzip.open(target, "wt", encoding="utf-8") as handle:
            handle.write(text)
    else:
        target.write_text(text, encoding="utf-8")
    return target


def _field_inventory(directory: Path) -> dict[str, dict[str, Any]]:
    """Return restart-capable OpenFOAM fields found in one time directory."""
    inventory: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return inventory
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        name = path.name.removesuffix(".gz")
        try:
            text = _read_field_text(path)
        except (OSError, UnicodeError):
            continue
        class_match = re.search(r"\bclass\s+([A-Za-z0-9_<>]+)\s*;", text)
        field_class = class_match.group(1) if class_match else None
        if field_class not in FIELD_CLASSES:
            # Keep compact fixture/legacy files discoverable; production
            # OpenFOAM fields always provide the class in FoamFile.
            if name == "U":
                field_class = "volVectorField"
            elif name == "phi":
                field_class = "surfaceScalarField"
            elif name in {"p", *PRIMARY_TURBULENCE_FIELDS, *OPTIONAL_RESTART_FIELDS}:
                field_class = "volScalarField"
            else:
                continue
        object_match = re.search(r"\bobject\s+([A-Za-z0-9_]+)\s*;", text)
        object_name = object_match.group(1) if object_match else name
        inventory[object_name] = {
            "path": path,
            "class": field_class,
            "compressed": path.suffix == ".gz",
        }
    return inventory


def steady_transfer_plan(
    case_dir: Path,
    archive: Path,
    latest: Path,
) -> dict[str, Any]:
    """Discover every state field required to restart the transient model."""
    initial = _field_inventory(archive / "initial_zero")
    available = _field_inventory(latest)
    initial_names = set(initial)
    available_names = set(available)

    required = {
        name
        for name, item in initial.items()
        if item["class"].startswith("vol") and name not in OPTIONAL_RESTART_FIELDS
    }
    required.update({"U", "p"})
    # Older cases or focused tests may not carry an initial_zero inventory.
    # Preserve any primary turbulence state present in the converged directory.
    required.update(available_names.intersection(PRIMARY_TURBULENCE_FIELDS))

    requested = initial_names.intersection(available_names)
    requested.update(required.intersection(available_names))
    requested.update(available_names.intersection(OPTIONAL_RESTART_FIELDS))
    ordered = [name for name in FIELD_TRANSFER_ORDER if name in requested]
    ordered.extend(sorted(requested.difference(ordered)))
    optional = [name for name in ordered if name in OPTIONAL_RESTART_FIELDS]
    missing = sorted(required.difference(available_names))
    return {
        "fields": ordered,
        "required": sorted(required, key=lambda name: (name not in FIELD_TRANSFER_ORDER, FIELD_TRANSFER_ORDER.index(name) if name in FIELD_TRANSFER_ORDER else name)),
        "optional": optional,
        "missing_required": missing,
        "initial_field_classes": {
            name: str(item["class"]) for name, item in sorted(initial.items())
        },
        "latest_field_classes": {
            name: str(item["class"]) for name, item in sorted(available.items())
        },
        "latest_inventory": available,
    }


def _normalized_field_digest(text: str) -> str:
    normalized = re.sub(r'\blocation\s+"[^"]+"\s*;', 'location    "0";', text, count=1)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def audit_steady_to_transient_continuity(
    case_dir: Path,
    archive: Path,
    transfer_report: dict[str, Any],
) -> dict[str, Any]:
    """Verify field identity and the force sample at the transient time origin."""
    steady_rows, steady_sources = read_force_coefficient_history(
        archive,
        include_processor0=False,
    )
    transient_rows, transient_sources = read_force_coefficient_history(
        case_dir,
        include_processor0=True,
    )
    steady_last = steady_rows[-1] if steady_rows else None
    transient_initial = (
        min(transient_rows, key=lambda row: abs(float(row.get("Time", math.inf))))
        if transient_rows
        else None
    )
    coefficient_differences: dict[str, float] = {}
    coefficient_origin_matches = None
    if steady_last is not None and transient_initial is not None:
        for name in ("Cl", "Cd", "Cm"):
            if name in steady_last and name in transient_initial:
                coefficient_differences[name] = float(transient_initial[name]) - float(steady_last[name])
        coefficient_origin_matches = (
            abs(float(transient_initial.get("Time", math.inf))) <= 1.0e-12
            and all(
                abs(delta) <= 1.0e-8 * max(1.0, abs(float(steady_last[name])))
                for name, delta in coefficient_differences.items()
            )
        )
    field_checks = transfer_report.get("field_continuity") or {}
    fields_match = bool(field_checks) and all(
        bool(item.get("digest_matches")) for item in field_checks.values()
    )
    first_solved = next(
        (
            row
            for row in transient_rows
            if float(row.get("Time", 0.0)) > 1.0e-12
        ),
        None,
    )
    first_step_change: dict[str, float] = {}
    if transient_initial is not None and first_solved is not None:
        for name in ("Cl", "Cd", "Cm"):
            if name in transient_initial and name in first_solved:
                first_step_change[name] = float(first_solved[name]) - float(transient_initial[name])
    status = (
        "VERIFIED"
        if fields_match and coefficient_origin_matches is not False
        else "INCOMPLETE_EVIDENCE"
        if fields_match and coefficient_origin_matches is None
        else "MISMATCH"
    )
    return {
        "status": status,
        "fields_match_exactly": fields_match,
        "coefficient_time_zero_matches_steady_final": coefficient_origin_matches,
        "steady_last_coefficients": steady_last,
        "transient_time_zero_coefficients": transient_initial,
        "coefficient_time_zero_differences": coefficient_differences,
        "first_solved_transient_coefficients": first_solved,
        "first_solved_step_change": first_step_change,
        "interpretation": (
            "A large change after the exact t=0 sample is a solver startup transient, "
            "not evidence that the steady fields were omitted. Do not force the transition "
            "when SIMPLE residual and force-plateau criteria are still unsatisfied."
        ),
        "steady_force_sources": steady_sources,
        "transient_force_sources": transient_sources,
        "field_continuity": field_checks,
    }


def archive_stage_runtime_artifacts(case_dir: Path, archive: Path) -> list[str]:
    """Move generated stage logs/postprocessing without touching case inputs."""
    moved: list[str] = []
    post = case_dir / "postProcessing"
    if post.is_dir():
        destination = archive / "postProcessing"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(post), str(destination))
        moved.append(str(destination))
    for pattern in (
        "log.*",
        "PyFoam*.logfile",
        "PyFoam*.json",
        "run_status.json",
        "convergence_monitor.json",
        "resume_plan.json",
        "ramairForceCoeffsRegexp",
        "ramair_live_monitor_*_snapshot.png",
        "ramair_live_monitor_status.json",
        "ramair_live_monitor.stop",
        "log.ramair_live_monitor",
    ):
        for path in case_dir.glob(pattern):
            if path.is_file():
                destination = archive / path.name
                if destination.exists():
                    destination.unlink()
                shutil.move(str(path), destination)
                moved.append(str(destination))
    return moved


def runner_was_stopped_by_user(case_dir: Path) -> bool:
    """Distinguish an explicit UI stop from timeout/convergence completion."""
    status = read_json(case_dir / "run_status.json", {}) or {}
    return status.get("status") in {"STOPPED_PARTIAL", "STOPPED_FORCED_PARTIAL"}


def archive_steady_outputs(
    case_dir: Path,
    archive: Path,
    *,
    transfer_to_transient_zero: bool,
    paraview_snapshot_count: int = 6,
) -> dict[str, Any]:
    positive = [(value, path) for value, path in numeric_time_dirs(case_dir) if value > 0.0]
    if not positive:
        raise RuntimeError("Steady stage did not produce a positive reconstructed time directory.")
    latest_value, latest = positive[-1]
    transferred: list[str] = []
    transfer_plan = steady_transfer_plan(case_dir, archive, latest)
    field_continuity: dict[str, dict[str, Any]] = {}
    if transfer_to_transient_zero:
        if transfer_plan["missing_required"]:
            raise RuntimeError(
                "Steady-to-transient transfer is incomplete; "
                f"missing required fields {transfer_plan['missing_required']} in {latest}"
            )
        for field in transfer_plan["fields"]:
            source = Path(transfer_plan["latest_inventory"][field]["path"])
            for existing in (case_dir / "0" / field, case_dir / "0" / f"{field}.gz"):
                if existing.exists():
                    existing.unlink()
            source_text = _read_field_text(source)
            target = _write_field_text(
                case_dir / "0" / field,
                source_text,
                source.suffix == ".gz",
            )
            transferred.append(str(target))
            field_continuity[field] = {
                "class": transfer_plan["latest_inventory"][field]["class"],
                "source": str(source),
                "target": str(target),
                "source_digest": _normalized_field_digest(source_text),
                "target_digest": _normalized_field_digest(_read_field_text(target)),
            }
            field_continuity[field]["digest_matches"] = (
                field_continuity[field]["source_digest"]
                == field_continuity[field]["target_digest"]
            )
    else:
        initial_zero = archive / "initial_zero"
        if initial_zero.is_dir():
            if (case_dir / "0").exists():
                shutil.rmtree(case_dir / "0")
            shutil.copytree(initial_zero, case_dir / "0")
    paraview_case = create_steady_paraview_case(
        case_dir,
        archive,
        positive,
        paraview_snapshot_count,
    )
    efficiency = write_steady_efficiency(case_dir, archive)
    times_archive = archive / "time_directories"
    times_archive.mkdir(parents=True, exist_ok=True)
    for _, path in positive:
        shutil.move(str(path), str(times_archive / path.name))
    archived_latest = times_archive / latest.name
    for field, item in field_continuity.items():
        item["source"] = str(archived_latest / Path(item["source"]).name)
    moved_artifacts = archive_stage_runtime_artifacts(case_dir, archive)
    return {
        "latest_steady_iteration": latest_value,
        "source": str(archived_latest),
        "transferred_to_transient_zero": transfer_to_transient_zero,
        "transferred_fields": transferred,
        "required_transferred_fields": transfer_plan["required"],
        "optional_transferred_fields": transfer_plan["optional"],
        "field_continuity": field_continuity,
        "initial_field_classes": transfer_plan["initial_field_classes"],
        "latest_field_classes": transfer_plan["latest_field_classes"],
        "flux_consistency": (
            (
                "steady face flux phi transferred with U"
                if any(Path(path).name.removesuffix(".gz") == "phi" for path in transferred)
                else "phi was not written by SIMPLE; transient incompressibleFluid reconstructs it from transferred U"
            )
            if transfer_to_transient_zero
            else "not transferred"
        ),
        "steady_time_semantics": "SIMPLE iteration counter; not physical time",
        "transient_time_origin": 0.0 if transfer_to_transient_zero else None,
        "paraview_case": paraview_case,
        "aerodynamic_efficiency": efficiency,
        "archive": str(archive),
        "moved_artifacts": moved_artifacts,
    }


def archive_failed_steady_outputs(
    case_dir: Path,
    archive: Path,
    *,
    paraview_snapshot_count: int = 6,
) -> dict[str, Any]:
    """Preserve a failed SIMPLE attempt and restore its pristine initial fields."""
    positive = [(value, path) for value, path in numeric_time_dirs(case_dir) if value > 0.0]
    if positive:
        result = archive_steady_outputs(
            case_dir,
            archive,
            transfer_to_transient_zero=False,
            paraview_snapshot_count=paraview_snapshot_count,
        )
        result["failed_stage"] = True
        return result
    initial_zero = archive / "initial_zero"
    if initial_zero.is_dir():
        if (case_dir / "0").exists():
            shutil.rmtree(case_dir / "0")
        shutil.copytree(initial_zero, case_dir / "0")
    return {
        "failed_stage": True,
        "latest_steady_iteration": None,
        "transferred_to_transient_zero": False,
        "transferred_fields": [],
        "archive": str(archive),
        "moved_artifacts": archive_stage_runtime_artifacts(case_dir, archive),
    }


def recover_unarchived_failed_steady(case_dir: Path) -> dict[str, Any] | None:
    """Close an older failed stage left active by a pre-fix runner release."""
    status = read_json(case_dir / "staged_run_status.json", {}) or {}
    if status.get("status") not in {"STEADY_STAGE_FAILED", "STEADY_STAGE_DIVERGED"}:
        return None
    history = case_dir / "steadyInitialization" / "history"
    candidates = sorted(
        (path for path in history.glob("run_*") if (path / "initial_zero").is_dir()),
        key=lambda path: path.stat().st_mtime,
    ) if history.is_dir() else []
    if not candidates:
        return None
    archive = candidates[-1]
    result = archive_failed_steady_outputs(case_dir, archive)
    transient_system = archive / "transient_system_before_steady"
    if transient_system.is_dir():
        restore_transient_system(case_dir, archive)
    result["recovered_legacy_failed_stage"] = True
    return result


def steady_failure_evidence(case_dir: Path) -> dict[str, Any]:
    pyfoam = read_json(case_dir / "pyfoam_run_report.json", {}) or {}
    run_status = read_json(case_dir / "run_status.json", {}) or {}
    log_candidates = [path for path in case_dir.glob("PyFoam*.logfile") if path.is_file()]
    failed_log = Path(str(pyfoam.get("failed_log", "")))
    if failed_log.is_file():
        log_candidates.append(failed_log)
    log_candidates += [path for path in case_dir.glob("log.foamRun") if path.is_file()]
    log_path = max(log_candidates, key=lambda path: path.stat().st_mtime) if log_candidates else None
    log_tail = ""
    if log_path is not None:
        log_tail = "\n".join(
            log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-80:]
        )
    divergence = bool(
        pyfoam.get("status") == "RUN_DIVERGED"
        or run_status.get("divergence_detected")
        or any(marker in log_tail for marker in ("Floating point exception", "Segmentation fault", "Divergence detected"))
    )
    setup_error = bool(
        run_status.get("status") == "RUN_SETUP_FAILED"
        or run_status.get("setup_error_detected")
        or any(
            marker in log_tail
            for marker in (
                "FOAM FATAL IO ERROR",
                "cannot find file",
                "Unable to set reference cell",
                "keyword ",
            )
        )
    )
    return {
        "divergence_detected": divergence,
        "setup_error_detected": setup_error,
        "pyfoam_report": pyfoam,
        "inner_run_status": run_status,
        "failed_stage": pyfoam.get("failed_stage"),
        "solver_log": str(log_path) if log_path is not None else None,
        "solver_log_tail": log_tail,
    }


def pending_state_path(case_dir: Path) -> Path:
    return case_dir / "steadyInitialization" / PENDING_STATE_NAME


def write_pending_state(case_dir: Path, data: dict[str, Any]) -> Path:
    path = pending_state_path(case_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load_pending_state(case_dir: Path) -> dict[str, Any]:
    path = pending_state_path(case_dir)
    data = read_json(path, None)
    if not isinstance(data, dict):
        raise FileNotFoundError(
            f"No pending steady initialization was found at {path}. Start a new staged run first."
        )
    archive = Path(str(data.get("archive", ""))).resolve()
    if not archive.is_dir():
        raise FileNotFoundError(f"Pending steady archive is missing: {archive}")
    data["archive"] = str(archive)
    return data


def set_steady_extension(case_dir: Path, additional_iterations: int) -> dict[str, float]:
    positive = [(value, path) for value, path in numeric_time_dirs(case_dir) if value > 0.0]
    if not positive:
        raise RuntimeError("Cannot extend SIMPLE: no positive steady iteration directory exists.")
    latest = positive[-1][0]
    end_iteration = latest + max(1, int(additional_iterations))
    control = case_dir / "system" / "controlDict"
    text = control.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"\bstartFrom\s+\w+\s*;", "startFrom       latestTime;", text, count=1)
    text = re.sub(r"\bstartTime\s+[0-9.eE+-]+\s*;", f"startTime       {latest:.12g};", text, count=1)
    text = re.sub(r"\bendTime\s+[0-9.eE+-]+\s*;", f"endTime         {end_iteration:.12g};", text, count=1)
    control.write_text(text, encoding="utf-8")
    return {"latest_iteration_before_extension": latest, "new_end_iteration": end_iteration}


def reset_transient_time_origin(case_dir: Path) -> dict[str, Any]:
    """Start PIMPLE at physical t=0 while retaining transferred steady fields."""
    control = case_dir / "system" / "controlDict"
    text = control.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"\bstartFrom\s+\w+\s*;", "startFrom       startTime;", text, count=1)
    text = re.sub(r"\bstartTime\s+[0-9.eE+-]+\s*;", "startTime       0;", text, count=1)
    text = re.sub(r"\bstopAt\s+\w+\s*;", "stopAt          endTime;", text, count=1)
    control.write_text(text, encoding="utf-8")
    return {
        "status": "TRANSIENT_TIME_ORIGIN_RESET",
        "startFrom": "startTime",
        "startTime_s": 0.0,
        "initial_fields_source": "latest SIMPLE iteration transferred into 0/",
    }


def _phase_d_steady_equivalence(
    case: Path,
    steady_archive: Path | None,
    *,
    stage_end_s: float,
    tc_s: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Strictly compare the final SIMPLE mean with the final phase-D window."""
    if not bool(settings.get("enabled", False)):
        return {"status": "DISABLED", "accepted": False}
    if steady_archive is None or not steady_archive.is_dir():
        return {"status": "MISSING_STEADY_ARCHIVE", "accepted": False}
    steady_rows, steady_sources = read_force_coefficient_history(
        steady_archive, include_processor0=False,
    )
    transient_rows, transient_sources = read_force_coefficient_history(
        case, include_processor0=True,
    )
    minimum_samples = max(20, int(settings.get("minimum_samples", 200)))
    window_star = max(0.1, float(settings.get("window_time_star", 2.5)))
    start_s = float(stage_end_s) - window_star * float(tc_s)
    d_window = [
        row for row in transient_rows
        if float(row.get("Time", -math.inf)) >= start_s - 1.0e-12
        and float(row.get("Time", math.inf)) <= float(stage_end_s) + 1.0e-12
    ]
    steady_window = steady_rows[-minimum_samples:]
    if len(steady_window) < minimum_samples or len(d_window) < minimum_samples:
        return {
            "status": "INSUFFICIENT_SAMPLES", "accepted": False,
            "steady_samples": len(steady_window), "phase_D_samples": len(d_window),
            "minimum_samples": minimum_samples,
        }
    floors = {
        "Cl": 0.05, "Cd": 0.005, "Cm": 0.005, "Cl_over_Cd": 1.0,
        **dict(settings.get("coefficient_floors") or {}),
    }
    mean_tolerance = float(settings.get("mean_difference_tolerance_percent", 0.30))
    fluctuation_tolerance = float(settings.get("fluctuation_tolerance_percent", 0.50))

    def values(rows: list[dict[str, float]], label: str) -> list[float]:
        if label != "Cl_over_Cd":
            return [float(row[label]) for row in rows if label in row and math.isfinite(float(row[label]))]
        return [
            float(row["Cl"]) / float(row["Cd"])
            for row in rows
            if "Cl" in row and "Cd" in row
            and math.isfinite(float(row["Cl"])) and math.isfinite(float(row["Cd"]))
            and abs(float(row["Cd"])) > 1.0e-12
        ]

    metrics: dict[str, Any] = {}
    accepted = True
    for label in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
        steady_values = values(steady_window, label)
        transient_values = values(d_window, label)
        if len(steady_values) < minimum_samples or len(transient_values) < minimum_samples:
            metrics[label] = {"accepted": False, "reason": "missing_finite_samples"}
            accepted = False
            continue
        steady_mean = statistics.fmean(steady_values)
        transient_mean = statistics.fmean(transient_values)
        scale = max(abs(steady_mean), abs(transient_mean), float(floors[label]))
        difference = 100.0 * abs(transient_mean - steady_mean) / scale
        fluctuation = 100.0 * statistics.pstdev(transient_values) / scale
        item = difference <= mean_tolerance and fluctuation <= fluctuation_tolerance
        accepted = accepted and item
        metrics[label] = {
            "steady_mean": steady_mean,
            "phase_D_mean": transient_mean,
            "relative_mean_difference_percent": difference,
            "phase_D_fluctuation_percent": fluctuation,
            "accepted": item,
        }
    return {
        "status": "STEADY_EQUIVALENT" if accepted else "TRANSIENT_PRODUCTION_REQUIRED",
        "accepted": accepted,
        "window_time_star": window_star,
        "window_start_s": start_s,
        "window_end_s": float(stage_end_s),
        "mean_difference_tolerance_percent": mean_tolerance,
        "fluctuation_tolerance_percent": fluctuation_tolerance,
        "metrics": metrics,
        "steady_sources": steady_sources,
        "phase_D_sources": transient_sources,
    }


def run_transient_phase_plan(
    args: argparse.Namespace,
    plan_path: Path,
    *,
    steady_archive: Path | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Execute a study-specific A-E plan after the steady fields reach t=0."""
    from ramair_2d_validation_staged_runner import configure_stage
    from ramair_2d_urans_contract import FRESH_FROM_CHECKPOINT, RESUME_EXISTING

    plan = read_json(plan_path, {}) or {}
    stages = plan.get("stages") or []
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"Transient phase plan has no stages: {plan_path}")
    config = read_json(args.case / "case_config.json", {}) or {}
    chord = float(config.get("chord_m", 1.0))
    velocity = float(config.get("velocity_m_s", 0.0))
    if chord <= 0.0 or velocity <= 0.0:
        raise ValueError("case_config.json must provide positive chord_m and velocity_m_s")
    convective_second = chord / velocity
    target_dt_star = float(plan.get("target_deltaT_star", 0.0025))
    field_write_star = float(config.get("field_write_interval_star", 0.1))
    retained_snapshots = max(0, int(config.get("purgeWrite", 24)))
    elapsed_star = 0.0
    phase_reports: list[dict[str, Any]] = []
    phase_d_gate: dict[str, Any] | None = None
    current_times = [value for value, _ in numeric_time_dirs(args.case) if value > 0.0]
    latest_time_s = max(current_times, default=0.0)
    completion_tolerance_s = max(1.0e-12, abs(target_dt_star * convective_second) * 0.25)
    for index, phase in enumerate(stages):
        duration_star = float(phase["duration_time_star"])
        dt_star = target_dt_star * float(phase.get("dt_factor", 1.0))
        start_star = elapsed_star
        elapsed_star += duration_star
        stage = {
            "stage": str(phase["stage"]),
            "scheme": str(phase["scheme"]),
            "dt_s": dt_star * convective_second,
            "start_s": start_star * convective_second,
            "end_s": elapsed_star * convective_second,
            "steps": max(1, int(math.ceil(duration_star / dt_star))),
            "adjust_time_step": bool(plan.get("adjust_time_step", True)),
            "maxCo": float(plan.get("maxCo", 50.0)),
            "maxDeltaT_s": dt_star * convective_second,
            "write_interval_s": field_write_star * convective_second,
            "purge_write": retained_snapshots,
            "retain_outer_residual_control": bool(plan.get("outer_residual_control_enabled", True)),
        }
        if latest_time_s >= float(stage["end_s"]) - completion_tolerance_s:
            phase_reports.append({
                "phase": str(phase["stage"]),
                "sampling": bool(phase.get("sampling", False)),
                "status": "ALREADY_COMPLETED_ON_RESUME",
                "latest_time_s": latest_time_s,
                "target_end_s": float(stage["end_s"]),
            })
            continue
        configuration = configure_stage(
            args.case,
            stage,
            start_mode=RESUME_EXISTING if (index > 0 or current_times) else FRESH_FROM_CHECKPOINT,
            preserve_temporal_history=str(phase["stage"]) in {"A", "B"},
        )
        command = runner_command(
            args,
            timeout_min=args.timeout_min,
            resume=bool(index > 0 or current_times),
            include_transient_convergence=False,
            include_resume_extension=False,
        )
        completed = subprocess.run(command, cwd=str(args.case), text=True)
        run_status = read_json(args.case / "run_status.json", {}) or {}
        phase_report = {
            "phase": str(phase["stage"]),
            "sampling": bool(phase.get("sampling", False)),
            "configuration": configuration,
            "command": command,
            "returncode": int(completed.returncode),
            "run_status": run_status,
        }
        phase_reports.append(phase_report)
        runner_status = str(run_status.get("status", "")).upper()
        if completed.returncode != 0 or runner_status in {
            "STOPPED_BY_USER", "STOPPED_PARTIAL", "STOPPED_FORCED_PARTIAL",
            "TIMEOUT_PARTIAL", "RUN_TIMEOUT_PARTIAL",
        }:
            return int(completed.returncode), phase_reports
        current_times = [value for value, _ in numeric_time_dirs(args.case) if value > 0.0]
        latest_time_s = max(current_times, default=latest_time_s)
        if str(phase["stage"]).upper() == "D":
            phase_d_gate = _phase_d_steady_equivalence(
                args.case,
                steady_archive,
                stage_end_s=float(stage["end_s"]),
                tc_s=convective_second,
                settings=dict(plan.get("phase_d_steady_equivalence") or {}),
            )
            phase_report["steady_equivalence_gate"] = phase_d_gate
            (args.case / "validation_phase_D_steady_equivalence.json").write_text(
                json.dumps(phase_d_gate, indent=2) + "\n", encoding="utf-8",
            )
            if bool(phase_d_gate.get("accepted")):
                break
    early_accept = bool(phase_d_gate and phase_d_gate.get("accepted"))
    total_star = float(elapsed_star)
    production_start_star = (
        total_star - float(phase_d_gate.get("window_time_star", 0.0))
        if early_accept else float(plan.get("production_start_time_star", 14.0))
    )
    sampling = {
        "production_stage": "D" if early_accept else str(plan.get("production_stage", "E")),
        "production_start_time_star": production_start_star,
        "total_time_star": total_star if early_accept else float(plan.get("total_time_star", elapsed_star)),
        "average_from_fraction": (
            production_start_star / max(total_star, 1.0e-30)
            if early_accept else float(plan.get("average_from_fraction", 14.0 / 64.0))
        ),
        "phase_D_steady_equivalence": phase_d_gate,
        "phase_E_skipped": early_accept,
        "phase_plan": str(plan_path.resolve()),
    }
    (args.case / "validation_sampling_window.json").write_text(
        json.dumps(sampling, indent=2) + "\n", encoding="utf-8",
    )
    return 0, phase_reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run optional steady SIMPLE initialization and transient PIMPLE sequentially. Dry-run by default.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--solver", default="auto")
    parser.add_argument("--execution-backend", choices=["native", "pyfoam"], default="pyfoam")
    parser.add_argument("--n-cores", type=int, default=4)
    parser.add_argument(
        "--automatic-core-selection", action=argparse.BooleanOptionalAction, default=True,
    )
    parser.add_argument(
        "--renumber-before-decompose", action=argparse.BooleanOptionalAction, default=True,
    )
    parser.add_argument("--timeout-min", type=float, default=120.0, help="Transient per-case timeout.")
    parser.add_argument("--steady-initialization", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--steady-only",
        action="store_true",
        help=(
            "Run and preserve only the SIMPLE initialization. When its transition "
            "gate passes, transfer all restart fields into 0/ and exit without "
            "starting the transient solver."
        ),
    )
    parser.add_argument("--steady-timeout-min", type=float, default=30.0)
    parser.add_argument(
        "--steady-force-window-samples",
        "--steady-force-window-iterations",
        dest="steady_force_window_samples",
        type=int,
        default=500,
        help="Samples in each of two adjacent Cl/Cd/Cm/Cl-over-Cd windows. forceCoeffs writes one sample per SIMPLE iteration.",
    )
    parser.add_argument("--steady-force-mean-tolerance-percent", type=float, default=1.0)
    parser.add_argument("--steady-force-fluctuation-tolerance-percent", type=float, default=2.0)
    parser.add_argument(
        "--steady-force-mean-tolerance",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--continue-transient-after-steady-timeout", action="store_true", help="Diagnostic override: transfer the latest steady fields even when transition checks fail.")
    parser.add_argument(
        "--steady-decision",
        choices=["auto", "extend", "start-transient", "finish"],
        default="auto",
        help="Resolve a pending steady stage without discarding its latest fields.",
    )
    parser.add_argument("--steady-additional-iterations", type=int, default=500)
    parser.add_argument(
        "--steady-paraview-snapshots",
        type=int,
        default=6,
        help="Number of evenly spaced SIMPLE iteration snapshots retained in the standalone steady ParaView case.",
    )
    parser.add_argument(
        "--steady-pyfoam-live-monitor",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Open PyFoam residual/iteration/Cl-Cd-Cm windows during SIMPLE when the pyfoam backend and a display are available.",
    )
    parser.add_argument(
        "--steady-potential-foam",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Initialize a fresh SIMPLE stage with potentialFoam before decomposition; extensions never rerun it.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resume-additional-time-star", type=float, default=None)
    parser.add_argument(
        "--stop-if-checkMesh-fails",
        "--stop-if-checkmesh-fails",
        dest="stop_if_checkmesh_fails",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--pyfoam-live-monitor", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--cleanup-processor-directories", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stop-when-force-stable", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--convergence-minimum-time-star", type=float, default=8.0)
    parser.add_argument("--convergence-window-time-star", type=float, default=2.0)
    parser.add_argument("--convergence-mean-tolerance", type=float, default=0.02)
    parser.add_argument("--convergence-oscillation-tolerance", type=float, default=0.10)
    parser.add_argument("--stop-grace-min", type=float, default=5.0)
    parser.add_argument("--stop-after-min", type=float, default=None)
    parser.add_argument("--stop-mode", choices=["writeNow", "nextWrite", "noWriteNow"], default="writeNow")
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--transient-phase-plan",
        type=Path,
        default=None,
        help="Optional progressive transient schedule executed after SIMPLE transfer.",
    )
    args = parser.parse_args()
    if args.steady_force_mean_tolerance is not None:
        legacy = float(args.steady_force_mean_tolerance)
        args.steady_force_mean_tolerance_percent = 100.0 * legacy if legacy <= 1.0 else legacy
    if args.steady_force_mean_tolerance_percent <= 0.0:
        parser.error("--steady-force-mean-tolerance-percent must be positive")
    if args.steady_force_fluctuation_tolerance_percent <= 0.0:
        parser.error("--steady-force-fluctuation-tolerance-percent must be positive")
    return args


def main() -> int:
    args = parse_args()
    args.case = args.case.resolve()
    if not (args.case / "system" / "controlDict").is_file():
        raise FileNotFoundError(f"Invalid OpenFOAM case: {args.case}")
    positive_times = [value for value, _ in numeric_time_dirs(args.case) if value > 0.0]
    pending_exists = pending_state_path(args.case).is_file()
    recovered_failed_stage: dict[str, Any] | None = None
    if (
        args.run
        and args.steady_initialization
        and positive_times
        and not args.resume
        and not pending_exists
    ):
        recovered_failed_stage = recover_unarchived_failed_steady(args.case)
        if recovered_failed_stage is not None:
            positive_times = [value for value, _ in numeric_time_dirs(args.case) if value > 0.0]
    resolving_pending = args.steady_decision != "auto"
    steady_requested = bool(
        args.steady_initialization
        and not resolving_pending
        and not (args.resume and positive_times)
    )
    plan = {
        "case": str(args.case),
        "run": bool(args.run),
        "steady_time_semantics": "SIMPLE iteration counter; not physical time",
        "transient_time_semantics": "physical seconds starting from t=0 after steady-field transfer",
        "steady_paraview_snapshot_count": int(args.steady_paraview_snapshots),
        "steady_initialization_requested": bool(args.steady_initialization),
        "steady_only": bool(args.steady_only),
        "steady_stage_will_run": steady_requested,
        "steady_decision": args.steady_decision,
        "pending_steady_stage_exists": pending_exists,
        "resume": bool(args.resume),
        "existing_positive_times": positive_times,
        "recovered_failed_steady_stage": recovered_failed_stage,
        "steady_command": runner_command(
            args,
            timeout_min=args.steady_timeout_min,
            resume=args.steady_decision == "extend",
            include_transient_convergence=False,
            live_monitor=bool(args.steady_pyfoam_live_monitor),
            include_resume_extension=False,
            potential_foam=bool(args.steady_potential_foam and args.steady_decision != "extend"),
        ) if steady_requested or args.steady_decision == "extend" else None,
        "transient_command": runner_command(
            args,
            timeout_min=args.timeout_min,
            resume=bool(args.resume and positive_times and not steady_requested and not resolving_pending),
        ),
    }
    (args.case / "staged_run_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    if not args.run:
        print(json.dumps(plan, indent=2))
        return 0
    if pending_exists and not resolving_pending:
        raise RuntimeError(
            "A steady initialization is awaiting a user decision. Use --steady-decision extend, "
            "start-transient or finish before starting another run."
        )
    if resolving_pending and not pending_exists:
        raise RuntimeError(
            f"--steady-decision {args.steady_decision} requires an existing pending steady stage."
        )
    if args.steady_initialization and positive_times and not args.resume:
        raise RuntimeError("Steady initialization requires a fresh case. Select resume for existing transient data or archive/rewrite the case.")

    report: dict[str, Any] = {
        **(read_json(args.case / "staged_run_status.json", {}) or {}),
        **plan,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    archive: Path | None = None
    transition: dict[str, Any] | None = None
    force_transient = False

    if resolving_pending:
        pending = load_pending_state(args.case)
        archive = Path(str(pending["archive"]))
        report["pending_stage_loaded"] = pending
        if args.steady_decision == "finish":
            restore_transient_system(args.case, archive)
            report["steady_archive"] = archive_steady_outputs(
                args.case,
                archive,
                transfer_to_transient_zero=False,
                paraview_snapshot_count=args.steady_paraview_snapshots,
            )
            pending_state_path(args.case).unlink(missing_ok=True)
            report.update(
                status="STEADY_STAGE_FINISHED_BY_USER",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                available_actions=[],
            )
            (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("Steady stage archived and closed without starting the transient solver.")
            return 0
        if args.steady_decision == "extend":
            reactivate_steady_templates(args.case)
            extension = set_steady_extension(args.case, args.steady_additional_iterations)
            report["steady_extension"] = extension
            steady_command = runner_command(
                args,
                timeout_min=args.steady_timeout_min,
                resume=True,
                include_transient_convergence=False,
                live_monitor=bool(args.steady_pyfoam_live_monitor),
                include_resume_extension=False,
                potential_foam=False,
            )
            report["steady_command"] = steady_command
            steady_completed = subprocess.run(steady_command, cwd=str(args.case), text=True)
            report["steady_runner_returncode"] = steady_completed.returncode
            restore_transient_system(args.case, archive)
            if steady_completed.returncode != 0:
                evidence = steady_failure_evidence(args.case)
                report["steady_failure"] = evidence
                report["steady_failure_archive"] = archive_failed_steady_outputs(
                    args.case,
                    archive,
                    paraview_snapshot_count=args.steady_paraview_snapshots,
                )
                pending_state_path(args.case).unlink(missing_ok=True)
                report.update(
                    status=(
                        "STEADY_STAGE_DIVERGED" if evidence["divergence_detected"] else
                        "STEADY_STAGE_SETUP_FAILED" if evidence["setup_error_detected"] else
                        "STEADY_STAGE_FAILED"
                    ),
                    available_actions=[],
                    finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                return int(steady_completed.returncode)
            if runner_was_stopped_by_user(args.case):
                active_times = [
                    value for value, _ in numeric_time_dirs(args.case) if value > 0.0
                ]
                pending.update(
                    status="STOPPED_PARTIAL",
                    latest_iteration=max(active_times, default=0.0),
                    last_extension=extension,
                    updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    available_actions=["extend", "start-transient", "finish"],
                )
                write_pending_state(args.case, pending)
                report.update(
                    status="STEADY_AWAITING_USER_DECISION_STOPPED",
                    latest_iteration=max(active_times, default=0.0),
                    data_preserved_for_resume=True,
                    available_actions=pending["available_actions"],
                    finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print("Steady solver stopped by the user; fields and scalar histories remain resumable.")
                return 0
            transition = evaluate_steady_transition(args.case, args)
            report["steady_transition"] = transition
            (archive / "steady_transition_report.json").write_text(json.dumps(transition, indent=2), encoding="utf-8")
            if (
                transition.get("status") != "READY_FOR_TRANSIENT"
                and not args.continue_transient_after_steady_timeout
            ):
                active_positive = [(value, path) for value, path in numeric_time_dirs(args.case) if value > 0.0]
                report["steady_paraview_preview"] = create_steady_paraview_case(
                    args.case,
                    archive,
                    active_positive,
                    args.steady_paraview_snapshots,
                )
                report["steady_aerodynamic_efficiency"] = write_steady_efficiency(args.case, archive)
                pending.update(
                    status="AWAITING_USER_DECISION",
                    transition=transition,
                    last_extension=extension,
                    updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                write_pending_state(args.case, pending)
                report.update(
                    status="STEADY_AWAITING_USER_DECISION",
                    available_actions=["extend", "start-transient", "finish"],
                    finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print("Steady extension finished without satisfying all transition criteria. User decision required.")
                return 0
            force_transient = bool(transition.get("status") != "READY_FOR_TRANSIENT")
        elif args.steady_decision == "start-transient":
            transition = pending.get("transition") if isinstance(pending.get("transition"), dict) else None
            force_transient = True

    if steady_requested:
        archive = args.case / "steadyInitialization" / "history" / time.strftime("run_%Y%m%d_%H%M%S")
        archive.mkdir(parents=True, exist_ok=False)
        install_steady_templates(args.case, archive)
        steady_command = runner_command(
            args,
            timeout_min=args.steady_timeout_min,
            resume=False,
            include_transient_convergence=False,
            live_monitor=bool(args.steady_pyfoam_live_monitor),
            include_resume_extension=False,
            potential_foam=bool(args.steady_potential_foam),
        )
        steady_completed = subprocess.run(steady_command, cwd=str(args.case), text=True)
        report["steady_runner_returncode"] = steady_completed.returncode
        if steady_completed.returncode != 0:
            evidence = steady_failure_evidence(args.case)
            report["steady_failure"] = evidence
            report["steady_failure_archive"] = archive_failed_steady_outputs(
                args.case,
                archive,
                paraview_snapshot_count=args.steady_paraview_snapshots,
            )
            restore_transient_system(args.case, archive)
            report.update(
                status=(
                    "STEADY_STAGE_DIVERGED" if evidence["divergence_detected"] else
                    "STEADY_STAGE_SETUP_FAILED" if evidence["setup_error_detected"] else
                    "STEADY_STAGE_FAILED"
                ),
                available_actions=[],
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            return int(steady_completed.returncode)
        if runner_was_stopped_by_user(args.case):
            restore_transient_system(args.case, archive)
            active_times = [
                value for value, _ in numeric_time_dirs(args.case) if value > 0.0
            ]
            pending = {
                "status": "STOPPED_PARTIAL",
                "archive": str(archive.resolve()),
                "latest_iteration": max(active_times, default=0.0),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "available_actions": ["extend", "start-transient", "finish"],
            }
            write_pending_state(args.case, pending)
            report.update(
                status="STEADY_AWAITING_USER_DECISION_STOPPED",
                latest_iteration=max(active_times, default=0.0),
                data_preserved_for_resume=True,
                available_actions=pending["available_actions"],
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("Steady solver stopped by the user; fields and scalar histories remain resumable.")
            return 0
        transition = evaluate_steady_transition(args.case, args)
        report["steady_transition"] = transition
        (archive / "steady_transition_report.json").write_text(json.dumps(transition, indent=2), encoding="utf-8")
        can_continue = transition.get("status") == "READY_FOR_TRANSIENT" or args.continue_transient_after_steady_timeout
        if not can_continue:
            active_positive = [(value, path) for value, path in numeric_time_dirs(args.case) if value > 0.0]
            report["steady_paraview_preview"] = create_steady_paraview_case(
                args.case,
                archive,
                active_positive,
                args.steady_paraview_snapshots,
            )
            report["steady_aerodynamic_efficiency"] = write_steady_efficiency(args.case, archive)
            restore_transient_system(args.case, archive)
            pending = {
                "status": "AWAITING_USER_DECISION",
                "archive": str(archive.resolve()),
                "transition": transition,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "available_actions": ["extend", "start-transient", "finish"],
            }
            write_pending_state(args.case, pending)
            report.update(
                status="STEADY_AWAITING_USER_DECISION",
                diagnostic_override=False,
                available_actions=pending["available_actions"],
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("Steady stage was preserved and awaits: extend, start-transient or finish.")
            return 0
        force_transient = bool(transition.get("status") != "READY_FOR_TRANSIENT")

    if args.steady_only:
        if archive is None or not (
            steady_requested
            or (
                resolving_pending
                and args.steady_decision == "start-transient"
            )
        ):
            raise RuntimeError(
                "--steady-only requires a new SIMPLE run or an explicit "
                "start-transient decision for a pending bounded state"
            )
        report["steady_transfer"] = archive_steady_outputs(
            args.case,
            archive,
            transfer_to_transient_zero=True,
            paraview_snapshot_count=args.steady_paraview_snapshots,
        )
        restore_transient_system(args.case, archive)
        report["transient_time_origin"] = reset_transient_time_origin(args.case)
        pending_state_path(args.case).unlink(missing_ok=True)
        report.update(
            status="STEADY_CHECKPOINT_READY",
            transient_started=False,
            available_actions=[],
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        (args.case / "staged_run_status.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
        print("SIMPLE checkpoint is ready; transient solver was not started.")
        return 0

    if archive is not None and (steady_requested or resolving_pending):
        report["steady_transfer"] = archive_steady_outputs(
            args.case,
            archive,
            transfer_to_transient_zero=True,
            paraview_snapshot_count=args.steady_paraview_snapshots,
        )
        restore_transient_system(args.case, archive)
        report["transient_time_origin"] = reset_transient_time_origin(args.case)
        pending_state_path(args.case).unlink(missing_ok=True)
        report["diagnostic_override"] = bool(force_transient)

    transient_resume = bool(args.resume and positive_times and archive is None)
    transient_command = runner_command(args, timeout_min=args.timeout_min, resume=transient_resume)
    if args.transient_phase_plan is not None:
        transient_returncode, phase_reports = run_transient_phase_plan(
            args, args.transient_phase_plan.resolve(), steady_archive=archive,
        )
        report["transient_phase_plan"] = str(args.transient_phase_plan.resolve())
        report["transient_phases"] = phase_reports
    else:
        transient_completed = subprocess.run(transient_command, cwd=str(args.case), text=True)
        transient_returncode = int(transient_completed.returncode)
    transition_audit = None
    if archive is not None and report.get("steady_transfer"):
        transition_audit = audit_steady_to_transient_continuity(
            args.case,
            archive,
            report["steady_transfer"],
        )
        (args.case / "steady_to_transient_continuity.json").write_text(
            json.dumps(transition_audit, indent=2) + "\n",
            encoding="utf-8",
        )
    transient_run_status = read_json(args.case / "run_status.json", {}) or {}
    solver_status = str(transient_run_status.get("status") or "").upper()
    if transient_returncode != 0:
        staged_outcome = "TRANSIENT_STAGE_FAILED"
    elif solver_status in {"RUN_COMPLETED", "CONVERGED_STATISTICALLY"}:
        staged_outcome = "TRANSIENT_STAGE_FINISHED"
    else:
        staged_outcome = "TRANSIENT_STAGE_PARTIAL"
    report.update(
        status=staged_outcome,
        production_complete=staged_outcome == "TRANSIENT_STAGE_FINISHED",
        completion_reason=(
            "production_target_reached"
            if solver_status == "RUN_COMPLETED"
            else "statistical_convergence_detector"
            if solver_status == "CONVERGED_STATISTICALLY"
            else solver_status.lower() or "incomplete_transient"
        ),
        transient_runner_returncode=transient_returncode,
        transient_run_status=transient_run_status,
        steady_to_transient_continuity=transition_audit,
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    (args.case / "staged_run_status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return int(transient_returncode)


if __name__ == "__main__":
    raise SystemExit(main())
