#!/usr/bin/env python3
"""Backend shared by the Streamlit UI and command-line launchers.

The backend translates user choices into calls to the existing, tested stage
scripts.  It does not duplicate geometry, meshing or CFD physics.
"""
from __future__ import annotations

import json
import hashlib
import os
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from openfoam_environment import sourced_openfoam_environment  # noqa: E402
from ramair_execution_control import (  # noqa: E402
    pid_is_alive as _pid_is_alive,
    process_group_id as _process_group_id,
    process_start_token as _process_start_token,
    reconcile_solver_record as _reconcile_solver_record,
    signal_solver_process as _signal_solver_process,
    write_json_atomic as _write_execution_json_atomic,
)
from paraview_case_viewer import (  # noqa: E402
    launch_paraview_case as _launch_paraview_case,
    launch_paraview_vtk_set as _launch_paraview_vtk_set,
)
from ramair_2d_rans_paraview_final import resolve_final_vtk_artifacts  # noqa: E402
from project_layout import LEGACY_ALIASES, find_project_root, project_path  # noqa: E402
from ramair_2d_study_registry import (  # noqa: E402
    active_workspace_root as _validation_active_workspace_root,
    load_study as _load_validation_study,
    migrate_study_config as _migrate_validation_study_config,
    write_json_atomic as _write_validation_json_atomic,
)
from ramair_2d_validation_live_monitor import (  # noqa: E402
    build_monitor_snapshot as _build_validation_monitor_snapshot,
    resolve_live_execution as _resolve_validation_live_execution,
)
from ramair_2d_rans_checkpoint_batch import (  # noqa: E402
    checkpoint_table as _validation_checkpoint_table,
)
from ramair_2d_rans_review import review_table as _validation_review_table  # noqa: E402
from ramair_2d_execution_registry import (  # noqa: E402
    load_registry as _load_validation_execution_registry,
    migrate_known_executions as _validation_execution_registry,
    registry_path as _validation_execution_registry_path,
)
from ramair_2d_urans_cases import (  # noqa: E402
    inspect_canonical_case as _inspect_validation_urans_case,
)


BACKEND_API_VERSION = 24
SOLVER_CONFIG_SCHEMA_VERSION = 14


_IDLE_WATCHDOG_STARTED = False
_IDLE_WATCHDOG_LOCK = threading.Lock()
_HEARTBEAT_WRITE_LOCK = threading.Lock()
_VIEWER_STATE_LOCK = threading.Lock()


CONFIG_PATHS = {
    "project": Path("Application Support/Configurations/default_case_config.json"),
    "catia_system": Path("Application Support/Configurations/ramair_catia_system_config.json"),
    "workflow": Path("CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"),
    "mesh": Path("CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"),
    "mesh_reference": Path("CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config_reference.json"),
    "solver": Path("CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json"),
    "physical": Path("CFD_2D/CFD_2D_inputs/config/cfd2d_physical_defaults.json"),
    "inlet_design": Path("CFD_2D/CFD_2D_inputs/config/cfd2d_inlet_design_config.json"),
}

ACTIVE_WORKCASE_CONFIG_TARGETS = {
    "project": (("geometry", Path("Configurations/default_case_config.json")),),
    "catia_system": (("geometry", Path("Configurations/ramair_catia_system_config.json")),),
    "workflow": (
        ("case", Path("CFD Configurations/cfd2d_workflow_config.json")),
        ("mesh", Path("Configurations/cfd2d_workflow_config.json")),
    ),
    "mesh": (("mesh", Path("Configurations/cfd2d_mesh_config.json")),),
    "solver": (
        ("solver", Path("Configurations/cfd2d_solver_config.json")),
        ("case", Path("CFD Configurations/cfd2d_solver_config.json")),
    ),
    "physical": (("case", Path("CFD Configurations/cfd2d_physical_defaults.json")),),
    "inlet_design": (("case", Path("CFD Configurations/cfd2d_inlet_design_config.json")),),
}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def backup_file(path: Path, project_root: Path) -> Path | None:
    if not path.is_file():
        return None
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        relative = Path(path.name)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = project_path(project_root, "previous_versions", "Config Backups", stamp) / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(path.read_bytes())
    return backup


def write_json_with_backup(path: Path, data: Any, project_root: Path) -> Path | None:
    backup = backup_file(path, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def config_path(project_root: Path, name: str) -> Path:
    if name not in CONFIG_PATHS:
        raise KeyError(f"Unknown configuration: {name}")
    return project_root / CONFIG_PATHS[name]


def _canonicalize_config_layout_paths(value: Any, project_root: Path) -> Any:
    """Translate legacy layout paths while leaving non-path settings untouched."""
    if isinstance(value, dict):
        return {key: _canonicalize_config_layout_paths(item, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize_config_layout_paths(item, project_root) for item in value]
    if not isinstance(value, str):
        return value

    normalized = value.replace("\\", "/")
    legacy_roots = {
        str(project_root.parent / "INPUT_FILES").replace("\\", "/"),
        str(project_root.parent / "INPUT FILES").replace("\\", "/"),
    }
    for legacy_root in legacy_roots:
        if normalized == legacy_root or normalized.startswith(legacy_root + "/"):
            normalized = str(project_root).replace("\\", "/") + normalized[len(legacy_root):]
            break

    for legacy, canonical in LEGACY_ALIASES.items():
        old = legacy.as_posix()
        if normalized == old:
            return canonical.as_posix()
        if normalized.startswith(old + "/"):
            return canonical.as_posix() + normalized[len(old):]
    return normalized if normalized != value and ("/" in value or "\\" in value) else value


def load_config(project_root: Path, name: str) -> dict[str, Any]:
    path = config_path(project_root, name)
    data = read_json(path, {}) or {}
    if not isinstance(data, dict):
        raise TypeError(f"Configuration {name} must contain a JSON object")
    migrated = _canonicalize_config_layout_paths(data, project_root)
    if name == "solver":
        migrated = migrate_solver_config_schema(migrated)
    if migrated != data:
        write_json_with_backup(path, migrated, project_root)
    return migrated


def migrate_solver_config_schema(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate older solver files while preserving explicit user choices."""
    migrated = dict(data)
    version = int(migrated.get("config_schema_version", 0) or 0)
    if version >= SOLVER_CONFIG_SCHEMA_VERSION:
        return migrated
    migrated.setdefault(
        "outer_corrector_residual_control",
        {
            "enabled": True,
            "U_tolerance": 1.0e-4,
            "nuTilda_tolerance": 1.0e-4,
            "relative_tolerance": 0.0,
        },
    )
    migrated.setdefault("transport_correction_final", False)
    profiles = dict(migrated.get("topology_profiles") or {})
    open_profile = dict(profiles.get("open_internal_cavity") or {})
    open_profile.setdefault(
        "outer_corrector_residual_control",
        dict(migrated["outer_corrector_residual_control"]),
    )
    open_profile.setdefault("transport_correction_final", False)
    profiles["open_internal_cavity"] = open_profile
    migrated["topology_profiles"] = profiles
    study = dict(migrated.get("validation_study") or {})
    study_defaults = {
        "enabled": False,
        "study_id": "",
        "alpha_deg": 8.0,
        "time_policy": "fixed_staged",
        "startup_scheme": "Euler",
        "production_scheme": "backward",
        "sensitivity_scheme": "CrankNicolson",
        "crank_nicolson_psi": 0.9,
        "dt_target_s": None,
        "startup_factors": [0.25, 0.5, 1.0],
        "startup_duration_tc": [1.0, 1.0, 2.0],
        "settling_tc": None,
        "sampling_tc": None,
        "nOuterCorrectors": 3,
        "nCorrectors": 2,
        "nNonOrthogonalCorrectors": 0,
        "courant_controls_dt": False,
        "field_write_interval_tc": 1.0,
        "retained_snapshots": 24,
        "mpi_ranks": 8,
        "timeout_hours": 24.0,
        "steady_checkpoint_timeout_min": 120.0,
    }
    for key, value in study_defaults.items():
        study.setdefault(key, value)
    migrated["validation_study"] = study
    migrated["config_schema_version"] = SOLVER_CONFIG_SCHEMA_VERSION
    return migrated


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sync_active_workcase_config(project_root: Path, name: str) -> list[Path]:
    """Persist one edited JSON into the selected work-case package(s)."""
    targets = ACTIVE_WORKCASE_CONFIG_TARGETS.get(name, ())
    if not targets:
        return []
    workspace = read_json(
        project_root / "CFD_2D/app_state/active_workspace.json", {}
    ) or {}
    case_name = str(workspace.get("case") or "")
    if not case_name:
        return []
    selection = read_json(
        project_root / "CFD_2D/app_state/workcase_selection.json", {}
    ) or {}
    selected_case = str(selection.get("case") or "")
    if selected_case != case_name:
        return []
    case_root = project_path(project_root, "results_library", case_name)
    manifest_path = case_root / "case_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    stages = manifest.get("stages") or {}
    source = config_path(project_root, name)
    if not source.is_file():
        return []
    content = source.read_bytes()
    written: list[Path] = []
    for stage, relative in targets:
        entry = stages.get(stage) if isinstance(stages, dict) else None
        if not isinstance(entry, dict):
            continue
        packages = entry.get("packages")
        if not isinstance(packages, dict) or not packages:
            continue
        package = str(entry.get("active_package") or next(reversed(packages)))
        info = packages.get(package)
        if not isinstance(info, dict):
            continue
        package_root = case_root / str(info.get("folder") or "")
        if not package_root.is_dir():
            continue
        destination = package_root / relative
        _write_bytes_atomic(destination, content)
        written.append(destination)
        files = [item for item in package_root.rglob("*") if item.is_file()]
        info["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        info["file_count"] = len(files)
        info["size_bytes"] = sum(item.stat().st_size for item in files)
        info["configuration_updated_in_app"] = True
    if written:
        manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_bytes_atomic(
            manifest_path,
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
    return written


def set_workcase_selection(project_root: Path, case_name: str | None) -> Path:
    """Declare which loaded work case may receive synchronized config edits."""
    path = project_root / "CFD_2D/app_state/workcase_selection.json"
    selected = str(case_name or "")
    current = read_json(path, {}) or {}
    if str(current.get("case") or "") == selected:
        return path
    payload = {
        "case": selected,
        "temporary_workspace": not bool(case_name),
        "selected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_bytes_atomic(
        path,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return path


def save_config(project_root: Path, name: str, data: dict[str, Any]) -> Path | None:
    normalized = _canonicalize_config_layout_paths(data, project_root)
    backup = write_json_with_backup(config_path(project_root, name), normalized, project_root)
    sync_active_workcase_config(project_root, name)
    return backup


def available_variants(project_root: Path) -> list[str]:
    variants: set[str] = set()
    geometry_root = project_root / "CFD_2D/CFD_2D_inputs/geometry"
    if geometry_root.is_dir():
        for path in geometry_root.iterdir():
            if not path.is_dir() or path.name in {"validation", "source"}:
                continue
            if any(
                (path / name).is_file()
                for name in ("profile_manifest.json", "manifest.json", "mesh_input_contract.json")
            ):
                variants.add(path.name)
    package_root = project_root / "CFD_2D/CFD_2D_inputs/case_package"
    if package_root.is_dir():
        variants.update(
            path.name
            for path in package_root.iterdir()
            if path.is_dir() and path.name != "validation" and (path / "manifest.json").is_file()
        )
    return sorted(variants)


def safe_alpha_dir(alpha: float) -> str:
    return f"alpha_{float(alpha):+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def case_directory(project_root: Path, variant: str, alpha: float) -> Path:
    return project_root / "CFD_2D/openfoam_cases" / variant / safe_alpha_dir(alpha)


def result_directory(project_root: Path, variant: str, alpha: float) -> Path:
    return project_root / "CFD_2D/results" / variant / safe_alpha_dir(alpha)


def prepare_existing_outputs(project_root: Path, paths: Iterable[Path], action: str, label: str) -> Path | None:
    """Archive, delete or retain explicitly selected generated output paths."""
    if action not in {"archive", "delete", "keep"}:
        raise ValueError(f"Unsupported existing-output action: {action}")
    existing = [path.resolve() for path in paths if path.exists()]
    if not existing or action == "keep":
        return None
    project_root = project_root.resolve()
    for path in existing:
        try:
            path.relative_to(project_root)
        except ValueError as exc:
            raise RuntimeError(f"Refusing to modify output outside the project: {path}") from exc
        if path == project_root:
            raise RuntimeError("Refusing to modify the project root")
    backup_root: Path | None = None
    if action == "archive":
        backup_root = project_path(
            project_root,
            "previous_versions",
            "CFD 2D App Output Backups",
            f"{label}_{time.strftime('%Y%m%d_%H%M%S')}",
        )
        backup_root.mkdir(parents=True, exist_ok=False)
    for path in existing:
        if action == "archive":
            assert backup_root is not None
            destination = backup_root / path.name
            suffix = 1
            while destination.exists():
                destination = backup_root / f"{path.name}_{suffix}"
                suffix += 1
            shutil.move(str(path), str(destination))
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return backup_root


def prepare_existing_simulation(case_dir: Path, action: str) -> list[str]:
    """Resume, stop on, or explicitly delete only generated solver outputs."""
    if action not in {"resume", "delete", "stop"}:
        raise ValueError(f"Unsupported existing-simulation action: {action}")
    case_dir = case_dir.resolve()
    if "openfoam_cases" not in case_dir.parts or not (case_dir / "system" / "controlDict").is_file():
        raise RuntimeError(f"Refusing to modify an unexpected OpenFOAM case path: {case_dir}")
    generated: list[Path] = []
    for path in case_dir.iterdir():
        if path.is_dir():
            try:
                if float(path.name) > 0.0:
                    generated.append(path)
                    continue
            except ValueError:
                pass
            if path.name == "postProcessing" or path.name == "steadyInitialization" or path.name.startswith("processor"):
                generated.append(path)
        elif (
            path.name.startswith("log.")
            or path.name.startswith("PyFoam")
            or path.name in {
                "run_status.json",
                "staged_run_status.json",
                "staged_run_plan.json",
                "convergence_monitor.json",
                "run_case.sh",
            }
        ):
            generated.append(path)
    if action == "resume":
        return []
    if action == "stop" and generated:
        raise RuntimeError(
            f"Existing simulation output was found in {case_dir}. Select resume or explicit delete."
        )
    removed: list[str] = []
    if action == "delete":
        for path in sorted(generated, key=lambda item: len(item.parts), reverse=True):
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        manifest = case_dir / "fresh_run_cleanup.json"
        manifest.write_text(
            json.dumps(
                {
                    "action": "delete",
                    "removed": removed,
                    "note": "Active generated solver outputs were deleted by explicit user choice; Results library packages were not modified.",
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    return removed


def python_command(project_root: Path, script: str, *arguments: object) -> list[str]:
    return [sys.executable, str((project_root / script).resolve()), *(str(value) for value in arguments)]


def environment_command(project_root: Path) -> list[str]:
    return python_command(project_root, "CFD_2D/scripts/check_environment.py")


def preprocessor_command(project_root: Path) -> list[str]:
    return python_command(
        project_root,
        "preprocess_ramair_main.py",
        "--config",
        config_path(project_root, "project"),
    )


def catia_detection(project_root: Path) -> dict[str, Any]:
    """Detect CATIA V5 without starting CATIA or creating a COM object."""
    command = python_command(
        project_root,
        "Application Support/Tools/launch_catia_macro.py",
        "--project-root", project_root,
        "--detect",
    )
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        report = {
            "available": False,
            "cnext": None,
            "reason": completed.stderr.strip() or completed.stdout.strip() or "CATIA detection failed.",
        }
    report["returncode"] = completed.returncode
    report["inputs_ready"] = (
        project_root / "CATIA" / "Inputs" / "ramair_global_inputs.csv"
    ).is_file()
    report["macro_ready"] = (
        project_root / "Generate_RamAir_Canopy_MAIN.CATScript"
    ).is_file()
    return report


def catia_macro_command(project_root: Path, cnext: str | None = None) -> list[str]:
    """Return the explicit visible CATIA launch command used by the UI."""
    command = python_command(
        project_root,
        "Application Support/Tools/launch_catia_macro.py",
        "--project-root", project_root,
        "--run",
    )
    if cnext:
        command.extend(["--cnext", cnext])
    return command


def inlet_design_command(project_root: Path) -> list[str]:
    return python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_inlet_designer.py",
        "--project-root", project_root,
        "--config", config_path(project_root, "inlet_design"),
    )


def xfoil_check_command(project_root: Path) -> list[str]:
    return python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_inlet_designer.py",
        "--project-root", project_root,
        "--check-environment",
    )


def case_builder_command(
    project_root: Path,
    *,
    variant: str,
    alpha_start: float,
    alpha_end: float,
    alpha_step: float,
    reynolds: float,
    mach: float,
    rho: float,
    mu: float,
    pressure_ref_pa: float = 101325.0,
    temperature_K: float = 288.15,
    velocity: str | float = "auto",
    plot: bool = True,
    validate: bool = True,
    overwrite: bool = True,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_profile_case_builder.py",
        "--case-root", project_root,
        "--variant", variant,
        "--alpha-start", alpha_start,
        "--alpha-end", alpha_end,
        "--alpha-step", alpha_step,
        "--reynolds", reynolds,
        "--mach", mach,
        "--rho", rho,
        "--mu", mu,
        "--pressure-ref-pa", pressure_ref_pa,
        "--temperature-K", temperature_K,
        "--velocity", velocity,
    )
    if plot:
        command.append("--plot")
    if validate:
        command.append("--validate")
    if overwrite:
        command.append("--overwrite")
    return command


def mesh_command(
    project_root: Path,
    *,
    variant: str,
    domain: str,
    mesh_level: str,
    gmsh_backend: str,
    gmsh_timeout_s: int,
    openfoam_timeout_s: int,
    threads: int | None,
    previous_output_action: str,
    write_openfoam_mesh: bool,
    check_mesh: bool,
    plot: bool,
    mesh_config: Path | None = None,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_mesh_builder.py",
        "--case-root", project_root,
        "--variant", variant,
        "--domain", domain,
        "--mesh-level", mesh_level,
        "--gmsh-backend", gmsh_backend,
        "--gmsh-timeout-s", max(60, int(gmsh_timeout_s)),
        "--openfoam-tool-timeout-s", max(60, int(openfoam_timeout_s)),
        "--previous-output-action", previous_output_action,
        "--overwrite",
    )
    if threads is not None:
        command += ["--gmsh-threads", str(max(1, int(threads)))]
    if mesh_config is not None:
        command += ["--mesh-config", str(Path(mesh_config).resolve())]
    if write_openfoam_mesh:
        command.append("--write-openfoam-mesh")
        if check_mesh:
            command.append("--check-mesh")
    else:
        command.append("--write-2d-mesh")
    if plot:
        command.append("--plot")
    return command


def mesh_optimizer_command(
    project_root: Path,
    *,
    variant: str,
    domain: str,
    mesh_level: str,
    iterations: int,
    vary_first_cell: bool,
    gmsh_backend: str,
    gmsh_timeout_s: int,
    openfoam_timeout_s: int,
    threads: int,
    previous_output_action: str,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_mesh_optimizer.py",
        "--case-root", project_root,
        "--variant", variant,
        "--domain", domain,
        "--mesh-level", mesh_level,
        "--iterations", max(2, min(5, int(iterations))),
        "--gmsh-backend", gmsh_backend,
        "--gmsh-timeout-s", max(60, int(gmsh_timeout_s)),
        "--openfoam-timeout-s", max(60, int(openfoam_timeout_s)),
        "--gmsh-threads", max(1, int(threads)),
        "--previous-output-action", previous_output_action,
    )
    if vary_first_cell:
        command.append("--vary-first-cell")
    return command


def approve_mesh_command(project_root: Path, variant: str, force: bool = False) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_mesh_builder.py",
        "--case-root", project_root,
        "--variant", variant,
        "--approve-mesh",
    )
    if force:
        command.append("--force-approve")
    return command


def case_writer_command(
    project_root: Path,
    *,
    variant: str,
    alpha: float,
    reynolds: float,
    require_converted_polymesh: bool,
    overwrite: bool = True,
    alphas: list[float] | None = None,
    existing_case_action: str = "archive",
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_openfoam_case_writer.py",
        "--case-root", project_root,
        "--variant", variant,
        "--reynolds", reynolds,
        "--write-case",
    )
    if alphas:
        command += ["--alphas", *[str(float(value)) for value in alphas]]
    else:
        command += ["--alpha", str(float(alpha))]
    if require_converted_polymesh:
        command.append("--require-converted-polymesh")
    else:
        command.append("--no-mesh-approved-required")
    if overwrite:
        command += ["--overwrite", "--existing-case-action", existing_case_action]
    return command


def runner_command(
    project_root: Path,
    *,
    variant: str,
    alpha: float,
    solver: str,
    execution_backend: str,
    n_cores: int,
    timeout_min: float,
    run: bool,
    stop_after_min: float | None,
    stop_grace_min: float,
    stop_mode: str,
    stop_if_checkmesh_fails: bool,
    pyfoam_live_monitor: bool = False,
    cleanup_processor_directories: bool = True,
    stop_when_force_stable: bool = False,
    convergence_minimum_time_star: float = 40.0,
    convergence_window_time_star: float = 10.0,
    convergence_mean_tolerance: float = 0.02,
    convergence_oscillation_tolerance: float = 0.10,
    convergence_poll_s: float = 10.0,
    resume: bool = False,
    resume_additional_time_star: float | None = None,
) -> list[str]:
    cdir = case_directory(project_root, variant, alpha)
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_openfoam_runner.py",
        "--case", cdir,
        "--solver", solver,
        "--execution-backend", execution_backend,
        "--n-cores", max(1, int(n_cores)),
        "--timeout-min", float(timeout_min),
        "--stop-grace-min", float(stop_grace_min),
        "--stop-mode", stop_mode,
    )
    if run:
        command.append("--run")
    if resume:
        command.append("--resume")
        if resume_additional_time_star is not None and resume_additional_time_star > 0:
            command += ["--resume-additional-time-star", str(float(resume_additional_time_star))]
    if stop_after_min is not None and stop_after_min > 0:
        command += ["--stop-after-min", str(float(stop_after_min))]
    if not stop_if_checkmesh_fails:
        command.append("--no-stop-if-checkMesh-fails")
    if pyfoam_live_monitor:
        command.append("--pyfoam-live-monitor")
    if not cleanup_processor_directories:
        command.append("--keep-processor-directories")
    if stop_when_force_stable:
        command += [
            "--stop-when-force-stable",
            "--convergence-minimum-time-star", str(float(convergence_minimum_time_star)),
            "--convergence-window-time-star", str(float(convergence_window_time_star)),
            "--convergence-mean-tolerance", str(float(convergence_mean_tolerance)),
            "--convergence-oscillation-tolerance", str(float(convergence_oscillation_tolerance)),
            "--convergence-poll-s", str(float(convergence_poll_s)),
        ]
    return command


def staged_runner_command(
    project_root: Path,
    *,
    variant: str,
    alpha: float,
    solver: str,
    execution_backend: str,
    n_cores: int,
    timeout_min: float,
    run: bool,
    stop_if_checkmesh_fails: bool,
    pyfoam_live_monitor: bool,
    cleanup_processor_directories: bool,
    stop_when_force_stable: bool,
    convergence_minimum_time_star: float,
    convergence_window_time_star: float,
    convergence_mean_tolerance: float,
    convergence_oscillation_tolerance: float,
    steady_initialization: bool,
    steady_timeout_min: float,
    steady_force_window_samples: int,
    steady_force_mean_tolerance_percent: float,
    steady_force_fluctuation_tolerance_percent: float,
    continue_transient_after_steady_timeout: bool,
    resume: bool,
    resume_additional_time_star: float | None,
    stop_grace_min: float = 5.0,
    stop_after_min: float | None = None,
    steady_decision: str = "auto",
    steady_additional_iterations: int = 500,
    steady_pyfoam_live_monitor: bool = True,
    steady_paraview_snapshots: int = 6,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py",
        "--case", case_directory(project_root, variant, alpha),
        "--solver", solver,
        "--execution-backend", execution_backend,
        "--n-cores", max(1, int(n_cores)),
        "--timeout-min", float(timeout_min),
        "--steady-timeout-min", float(steady_timeout_min),
        "--steady-force-window-samples", max(10, int(steady_force_window_samples)),
        "--steady-force-mean-tolerance-percent", float(steady_force_mean_tolerance_percent),
        "--steady-force-fluctuation-tolerance-percent", float(steady_force_fluctuation_tolerance_percent),
        "--steady-decision", steady_decision,
        "--steady-additional-iterations", max(1, int(steady_additional_iterations)),
        "--steady-paraview-snapshots", max(2, int(steady_paraview_snapshots)),
        "--stop-grace-min", float(stop_grace_min),
        "--convergence-minimum-time-star", float(convergence_minimum_time_star),
        "--convergence-window-time-star", float(convergence_window_time_star),
        "--convergence-mean-tolerance", float(convergence_mean_tolerance),
        "--convergence-oscillation-tolerance", float(convergence_oscillation_tolerance),
    )
    if run:
        command.append("--run")
    if stop_after_min is not None and stop_after_min > 0:
        command += ["--stop-after-min", str(float(stop_after_min))]
    if steady_initialization:
        command.append("--steady-initialization")
    if not steady_pyfoam_live_monitor:
        command.append("--no-steady-pyfoam-live-monitor")
    if continue_transient_after_steady_timeout:
        command.append("--continue-transient-after-steady-timeout")
    if resume:
        command.append("--resume")
        if resume_additional_time_star is not None and resume_additional_time_star > 0:
            command += ["--resume-additional-time-star", str(float(resume_additional_time_star))]
    if not stop_if_checkmesh_fails:
        command.append("--no-stop-if-checkMesh-fails")
    if pyfoam_live_monitor:
        command.append("--pyfoam-live-monitor")
    if not cleanup_processor_directories:
        command.append("--no-cleanup-processor-directories")
    if stop_when_force_stable:
        command.append("--stop-when-force-stable")
    return command


def sweep_runner_command(
    project_root: Path,
    *,
    variant: str,
    alphas: list[float],
    solver: str,
    execution_backend: str,
    n_cores: int,
    timeout_min_per_alpha: float,
    run: bool,
    steady_initialization: bool,
    steady_timeout_min: float,
    steady_force_window_samples: int,
    steady_force_mean_tolerance_percent: float,
    steady_force_fluctuation_tolerance_percent: float,
    continue_transient_after_steady_timeout: bool,
    resume_existing: bool,
    resume_additional_time_star: float | None,
    continue_after_timeout: bool,
    stop_when_force_stable: bool,
    convergence_minimum_time_star: float,
    convergence_window_time_star: float,
    convergence_mean_tolerance: float,
    convergence_oscillation_tolerance: float,
    stop_if_checkmesh_fails: bool,
    pyfoam_live_monitor: bool,
    steady_pyfoam_live_monitor: bool,
    cleanup_processor_directories: bool,
    postprocess_after_each: bool,
    continue_after_error: bool,
    average_from_fraction: float,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_openfoam_sweep.py",
        "--case-root", project_root,
        "--variant", variant,
        "--alphas", *[str(float(value)) for value in alphas],
        "--solver", solver,
        "--execution-backend", execution_backend,
        "--n-cores", max(1, int(n_cores)),
        "--timeout-min-per-alpha", float(timeout_min_per_alpha),
        "--steady-timeout-min", float(steady_timeout_min),
        "--steady-force-window-samples", max(10, int(steady_force_window_samples)),
        "--steady-force-mean-tolerance-percent", float(steady_force_mean_tolerance_percent),
        "--steady-force-fluctuation-tolerance-percent", float(steady_force_fluctuation_tolerance_percent),
        "--convergence-minimum-time-star", float(convergence_minimum_time_star),
        "--convergence-window-time-star", float(convergence_window_time_star),
        "--convergence-mean-tolerance", float(convergence_mean_tolerance),
        "--convergence-oscillation-tolerance", float(convergence_oscillation_tolerance),
        "--average-from-fraction", float(average_from_fraction),
    )
    if run:
        command.append("--run")
    if steady_initialization:
        command.append("--steady-initialization")
    if continue_transient_after_steady_timeout:
        command.append("--continue-transient-after-steady-timeout")
    else:
        command.append("--no-continue-transient-after-steady-timeout")
    if not resume_existing:
        command.append("--no-resume-existing")
    if resume_additional_time_star is not None and resume_additional_time_star > 0:
        command += ["--resume-additional-time-star", str(float(resume_additional_time_star))]
    if not continue_after_timeout:
        command.append("--no-continue-after-timeout")
    if not stop_when_force_stable:
        command.append("--no-stop-when-force-stable")
    if not stop_if_checkmesh_fails:
        command.append("--no-stop-if-checkMesh-fails")
    if pyfoam_live_monitor:
        command.append("--pyfoam-live-monitor")
    if not steady_pyfoam_live_monitor:
        command.append("--no-steady-pyfoam-live-monitor")
    if not cleanup_processor_directories:
        command.append("--no-cleanup-processor-directories")
    if postprocess_after_each:
        command.append("--postprocess-after-each")
    if not continue_after_error:
        command.append("--no-continue-after-error")
    return command


def request_openfoam_clean_stop(case_dir: Path, mode: str = "writeNow") -> Path:
    """Ask a running OpenFOAM case to stop and write without signalling MPI."""
    if mode not in {"writeNow", "nextWrite", "noWriteNow"}:
        raise ValueError(f"Unsupported OpenFOAM stop mode: {mode}")
    control = case_dir / "system" / "controlDict"
    if not control.is_file():
        raise FileNotFoundError(f"Missing controlDict for clean stop: {control}")
    text = control.read_text(encoding="utf-8", errors="ignore")
    backup = control.with_name(f"controlDict.before_ui_stop_{time.strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(text, encoding="utf-8")
    import re
    if re.search(r"\bstopAt\s+\w+\s*;", text):
        text = re.sub(r"\bstopAt\s+\w+\s*;", f"stopAt          {mode};", text, count=1)
    else:
        text += f"\nstopAt          {mode};\n"
    if re.search(r"\brunTimeModifiable\s+\w+\s*;", text):
        text = re.sub(r"\brunTimeModifiable\s+\w+\s*;", "runTimeModifiable true;", text, count=1)
    else:
        text += "runTimeModifiable true;\n"
    control.write_text(text, encoding="utf-8")
    marker = case_dir / ".ramair_stop_request.json"
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "mode": mode,
                "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "case_dir": str(case_dir.resolve()),
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(marker)
    return backup


def request_openfoam_sweep_stop(command: list[str]) -> Path:
    """Stop a sweep after the active angle has written its latest state."""
    try:
        root_index = command.index("--case-root")
        variant_index = command.index("--variant")
        root = Path(command[root_index + 1]).resolve()
        variant = str(command[variant_index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError("The active command is not an OpenFOAM alpha sweep") from exc
    sweep_root = root / "CFD_2D" / "openfoam_cases" / variant
    if not sweep_root.is_dir():
        raise FileNotFoundError(f"Missing sweep case directory: {sweep_root}")
    marker = sweep_root / ".ramair_sweep_stop_request.json"
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "variant": variant,
                "sweep_root": str(sweep_root),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(marker)
    return marker


def request_validation_rans_stop(
    project_root: Path,
    command: list[str],
) -> dict[str, Any]:
    """Request a resumable stop for a validation RANS base or queue."""
    project_root = Path(project_root).resolve()
    active = _validation_active_workspace_root(project_root)
    active.mkdir(parents=True, exist_ok=True)
    mesh_id: str | None = None
    try:
        index = command.index("--mesh-id")
        mesh_id = str(command[index + 1])
    except (ValueError, IndexError):
        queue = read_json(active / "rans_queue_state.json", {}) or {}
        mesh_id = str(queue.get("current_mesh_id") or "") or None
    marker = active / ".rans_queue_stop_request.json"
    _write_validation_json_atomic(
        marker,
        {
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mesh_id": mesh_id,
            "policy": "writeNow_then_preserve_for_resume",
        },
    )
    case_dir = active / "checkpoints" / str(mesh_id) / "case" if mesh_id else None
    backup: Path | None = None
    if case_dir is not None and (case_dir / "system/controlDict").is_file():
        backup = request_openfoam_clean_stop(case_dir, "writeNow")
    return {
        "status": "STOP_REQUESTED",
        "mesh_id": mesh_id,
        "queue_marker": str(marker),
        "control_dict_backup": str(backup) if backup else None,
        "data_policy": "Preserve all written fields and scalar histories for resume",
    }


def validation_runtime_case(project_root: Path) -> Path | None:
    """Return the case published by the Validation Lab runtime, if trustworthy."""
    active = _validation_active_workspace_root(Path(project_root).resolve())
    runtime = read_json(active / "runtime/active_execution.json", {}) or {}
    value = str(runtime.get("case_path") or "").strip()
    if not value:
        return None
    candidate = Path(value).resolve()
    try:
        candidate.relative_to(active.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_dir() else None


def request_validation_pimple_stop(project_root: Path) -> dict[str, Any]:
    """Stop the active PIMPLE pilot after a write and retain its study queue."""
    project_root = Path(project_root).resolve()
    active = _validation_active_workspace_root(project_root)
    study_root = active / "pimple_outer_study"
    marker = study_root / ".ramair_pimple_stop_request.json"
    _write_execution_json_atomic(
        marker,
        {
            "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "policy": "writeNow_then_pause_study_queue",
        },
    )
    case_dir = validation_runtime_case(project_root)
    backup: Path | None = None
    if case_dir is not None and (case_dir / "system/controlDict").is_file():
        backup = request_openfoam_clean_stop(case_dir, "writeNow")
    return {
        "status": "STOP_REQUESTED",
        "case_dir": str(case_dir) if case_dir else None,
        "study_marker": str(marker),
        "control_dict_backup": str(backup) if backup else None,
        "data_policy": "Preserve completed phases and latest written decomposed fields",
    }


def reconcile_validation_runtime(project_root: Path) -> dict[str, Any] | None:
    """Repair a stale Validation Lab runtime record without deleting fields."""
    case_dir = validation_runtime_case(project_root)
    if case_dir is None:
        return None
    result = _reconcile_solver_record(case_dir)
    active = _validation_active_workspace_root(Path(project_root).resolve())
    runtime_path = active / "runtime/active_execution.json"
    runtime = read_json(runtime_path, {}) or {}
    runtime_pid = int(runtime.get("solver_pid") or runtime.get("pid") or 0)
    runtime_token = str(runtime.get("solver_pid_start_token") or "") or None
    if str(runtime.get("status")) in {"RUNNING", "STOP_REQUESTED", "STOPPING"} and not _pid_is_alive(
        runtime_pid, runtime_token
    ):
        runtime.update(
            status=("PAUSED_RESTARTABLE" if result.get("restartable") else "STOPPED_INCOMPLETE"),
            reconciled_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            restart_evidence={
                key: result.get(key)
                for key in (
                    "latest_time",
                    "latest_root_time",
                    "latest_processor_time",
                    "requires_reconstruction",
                )
            },
        )
        _write_execution_json_atomic(runtime_path, runtime)
    return result


def openfoam_case_from_command(command: list[str]) -> Path | None:
    """Return the active OpenFOAM case owned by a single or sweep runner.

    A sweep has no ``--case`` argument. Its runner publishes the exact active
    case atomically in ``alpha_sweep_status.json``, which prevents the UI
    from displaying a stale monitor belonging to another angle.
    """
    try:
        index = command.index("--case")
        return Path(command[index + 1]).resolve()
    except (ValueError, IndexError):
        pass
    try:
        root_index = command.index("--case-root")
        variant_index = command.index("--variant")
        root = Path(command[root_index + 1]).resolve()
        variant = str(command[variant_index + 1])
    except (ValueError, IndexError):
        return None
    sweep_root = root / "CFD_2D" / "openfoam_cases" / variant
    status = read_json(sweep_root / "alpha_sweep_status.json", {}) or {}
    active = str(status.get("active_case") or "").strip()
    if active:
        active_path = Path(active).resolve()
        try:
            active_path.relative_to(sweep_root.resolve())
        except ValueError:
            return None
        return active_path
    try:
        alpha_index = command.index("--alphas")
        alpha = float(command[alpha_index + 1])
    except (ValueError, IndexError):
        return None
    return case_directory(root, variant, alpha).resolve()


def postprocess_command(
    project_root: Path,
    *,
    variant: str,
    alpha: float,
    average_from_fraction: float,
    run_openfoam_postprocess: bool,
    export_mode: str,
    timeout_s: int,
    open_results_folder: bool,
    open_paraview: bool,
    wall_profile_analysis: bool = True,
    velocity_profile_stations: list[float] | None = None,
    velocity_profile_sample_points: int = 40,
    automatic_paraview_products: bool = False,
    paraview_maximum_frames: int = 24,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_postprocess.py",
        "--case-root", project_root,
        "--variant", variant,
        "--alpha", alpha,
        "--average-from-fraction", average_from_fraction,
        "--openfoam-postprocess-timeout-s", max(30, int(timeout_s)),
        "--velocity-profile-sample-points", max(10, int(velocity_profile_sample_points)),
    )
    if velocity_profile_stations:
        command += ["--velocity-profile-stations", *[str(float(value)) for value in velocity_profile_stations]]
    if run_openfoam_postprocess:
        command.append("--run-openfoam-postprocess")
    if export_mode in {"latest_vtk", "all_vtk"}:
        command.append("--export-vtk")
    if export_mode == "all_vtk":
        command.append("--export-vtk-all-times")
    if open_results_folder:
        command.append("--open-results-folder")
    if open_paraview:
        command.append("--open-paraview")
    if automatic_paraview_products:
        command += [
            "--automatic-paraview-products",
            "--paraview-maximum-frames", str(max(2, int(paraview_maximum_frames))),
        ]
    if not wall_profile_analysis:
        command.append("--no-wall-profile-analysis")
    return command


def batch_postprocess_command(
    project_root: Path,
    *,
    variant: str,
    alphas: list[float],
    average_from_fraction: float,
    run_openfoam_postprocess: bool,
    export_mode: str,
    timeout_s: int,
    wall_profile_analysis: bool,
    velocity_profile_stations: list[float],
    velocity_profile_sample_points: int,
    automatic_paraview_products: bool = False,
    paraview_maximum_frames: int = 24,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_batch_postprocess.py",
        "--case-root", project_root,
        "--variant", variant,
        "--alphas", *[str(float(value)) for value in alphas],
        "--average-from-fraction", float(average_from_fraction),
        "--export-mode", export_mode,
        "--timeout-s", max(30, int(timeout_s)),
        "--velocity-profile-sample-points", max(10, int(velocity_profile_sample_points)),
    )
    if velocity_profile_stations:
        command += ["--velocity-profile-stations", *[str(float(value)) for value in velocity_profile_stations]]
    if not run_openfoam_postprocess:
        command.append("--no-run-openfoam-postprocess")
    if not wall_profile_analysis:
        command.append("--no-wall-profile-analysis")
    if automatic_paraview_products:
        command += [
            "--automatic-paraview-products",
            "--paraview-maximum-frames", str(max(2, int(paraview_maximum_frames))),
        ]
    return command


def validation_publish_command(
    project_root: Path,
    *,
    variant: str,
    alphas: list[float],
    action: str = "add",
) -> list[str]:
    if action not in {"add", "remove"}:
        raise ValueError("Validation publication action must be add or remove")
    return python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_validation_publish.py",
        "--case-root", project_root,
        "--variant", variant,
        "--action", action,
        "--alphas", *[str(float(value)) for value in alphas],
    )


def mesh_refinement_study_command(
    project_root: Path,
    *,
    levels: list[str],
    rebuild: bool,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_mesh_refinement_study.py",
        "--project-root", project_root,
        "--levels", *levels,
    )
    if not rebuild:
        command.append("--no-replace-generated")
    return command


def validation_study_command(
    project_root: Path,
    action: str,
    *,
    run_id: str | None = None,
    mesh_id: str | None = None,
    topology: str | None = None,
    mesh_level: str | None = None,
    run_ids: Iterable[str] | None = None,
    overwrite: bool = False,
    run: bool = False,
    startup_mode: str | None = None,
    confirm_delete: str | None = None,
    seconds_per_step: float | None = None,
    snapshot_size_bytes: float | None = None,
    refresh_hashes: bool = False,
    preset: str | None = None,
    anchor_dt_s: float | None = None,
    study_action: str | None = None,
    allow_open_diagnostic: bool = False,
    continue_on_nonfatal_failure: bool = False,
    confirm: bool = False,
    dt_s: float | None = None,
    custom_dt_values_s: Iterable[float] | None = None,
    review_action: str | None = None,
    reason: str | None = None,
    registry_action: str | None = None,
    mode: str | None = None,
    pin: bool = False,
    archive_before_delete: bool = True,
    manual_extension_iterations: int | None = None,
) -> list[str]:
    """Build one isolated Validation & Convergence Lab command."""
    allowed = {
        "init", "status", "select-mesh", "prepare", "budget",
        "execute", "checkpoint", "preset", "analyze", "analyze-checkpoint",
        "report", "reference-table", "rans-base", "rans-queue",
        "storage-inventory", "storage-cleanup", "pimple-study",
        "open-light", "open-refinement", "rans-review", "execution-registry",
        "rans-delete", "inspect-case", "restart", "quick-check",
    }
    if action not in allowed:
        raise ValueError(f"Unsupported validation-study action: {action}")
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_validation_study.py",
        "--project-root",
        project_root,
        action,
    )
    if refresh_hashes:
        command.append("--refresh-hashes")
    if run_id:
        command += ["--run-id", run_id]
    if mesh_id:
        command += ["--mesh-id", mesh_id]
    if topology is not None:
        if action != "pimple-study" or topology not in {"closed", "open"}:
            raise ValueError("topology is only valid for a PIMPLE study")
        command += ["--topology", topology]
    if mesh_level is not None:
        if action != "pimple-study" or mesh_level not in {"coarse", "medium", "fine"}:
            raise ValueError("mesh_level is only valid for a PIMPLE study")
        command += ["--mesh-level", mesh_level]
    for value in run_ids or ():
        command += ["--run-id", value]
    if overwrite:
        command.append("--overwrite")
    if preset is not None:
        if action != "preset":
            raise ValueError("preset is only valid for the preset action")
        command += ["--preset", preset]
    if anchor_dt_s is not None:
        command += ["--anchor-dt-s", str(float(anchor_dt_s))]
    if custom_dt_values_s is not None:
        if action != "preset":
            raise ValueError(
                "custom_dt_values_s is only valid for the preset action"
            )
        for value in custom_dt_values_s:
            command += ["--custom-dt-s", str(float(value))]
    if startup_mode is not None:
        if action != "execute":
            raise ValueError("startup_mode is only valid for execute")
        if startup_mode not in {"progressive", "direct"}:
            raise ValueError("startup_mode must be progressive or direct")
        command += ["--startup-mode", startup_mode]
    if confirm_delete is not None:
        if action != "restart":
            raise ValueError("confirm_delete is only valid for restart")
        command += ["--confirm-delete", str(confirm_delete)]
    if run:
        command.append("--run")
    if study_action is not None:
        if action not in {"pimple-study", "open-light", "open-refinement"}:
            raise ValueError(
                "study_action is only valid for pimple-study or open-light"
            )
        command += ["--study-action", study_action]
    if review_action is not None:
        if action != "rans-review":
            raise ValueError("review_action is only valid for rans-review")
        command += ["--review-action", review_action]
    if reason is not None:
        if action != "rans-review":
            raise ValueError("reason is only valid for rans-review")
        command += ["--reason", reason]
    if registry_action is not None:
        if action != "execution-registry":
            raise ValueError(
                "registry_action is only valid for execution-registry"
            )
        command += ["--registry-action", registry_action]
    if mode is not None:
        if action != "execution-registry":
            raise ValueError("mode is only valid for execution-registry")
        command += ["--mode", mode]
    if pin:
        if action != "execution-registry":
            raise ValueError("pin is only valid for execution-registry")
        command.append("--pin")
    if allow_open_diagnostic:
        if action != "rans-base":
            raise ValueError(
                "allow_open_diagnostic is only valid for rans-base"
            )
        command.append("--allow-open-diagnostic")
    if manual_extension_iterations is not None:
        if action != "rans-base":
            raise ValueError(
                "manual_extension_iterations is only valid for rans-base"
            )
        if int(manual_extension_iterations) <= 0:
            raise ValueError("manual_extension_iterations must be positive")
        command += [
            "--manual-extension-iterations",
            str(int(manual_extension_iterations)),
        ]
    if continue_on_nonfatal_failure:
        if action != "rans-queue":
            raise ValueError(
                "continue_on_nonfatal_failure is only valid for rans-queue"
            )
        command.append("--continue-on-nonfatal-failure")
    if confirm:
        if action not in {
            "storage-cleanup",
            "open-light",
            "open-refinement",
            "rans-delete",
            "rans-review",
        }:
            raise ValueError(
                "confirm is only valid for destructive validation actions"
            )
        command.append("--confirm")
    if action == "rans-delete" and not archive_before_delete:
        command.append("--no-archive")
    if dt_s is not None:
        if action != "pimple-study":
            raise ValueError("dt_s is only valid for pimple-study")
        command += ["--dt-s", str(float(dt_s))]
    if seconds_per_step is not None:
        command += ["--seconds-per-step", str(float(seconds_per_step))]
    if snapshot_size_bytes is not None:
        command += ["--snapshot-size-bytes", str(float(snapshot_size_bytes))]
    return command


def validation_smoke_command(
    project_root: Path,
    *,
    run: bool = False,
) -> list[str]:
    """Prepare or run the bounded closed-coarse software smoke."""
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_validation_smoke.py",
        "--project-root",
        project_root,
    )
    if run:
        command.append("--run")
    return command


def validation_urans_queue_command(
    project_root: Path,
    action: str,
    *,
    run_ids: Iterable[str] | None = None,
    startup_mode: str = "progressive",
    run: bool = False,
    resume: bool = False,
) -> list[str]:
    """Build the canonical sequential URANS queue command."""
    if action not in {"prepare", "execute"}:
        raise ValueError("URANS queue action must be prepare or execute")
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_urans_matrix_manager.py",
        "--project-root",
        project_root,
        action,
    )
    if action == "prepare":
        values = list(run_ids or ())
        if not values:
            raise ValueError("Select at least one canonical URANS case")
        for value in values:
            command += ["--run-id", str(value)]
        command += ["--startup-mode", startup_mode]
    if action == "execute":
        if run:
            command.append("--run")
        if resume:
            command.append("--resume")
    return command


def validation_study_snapshot(project_root: Path) -> dict[str, Any]:
    """Read the isolated lab state without consulting active_workspace.json."""
    project_root = Path(project_root)
    snapshot = _load_validation_study(project_root)
    if snapshot:
        snapshot["rans_checkpoints"] = _validation_checkpoint_table(
            Path(project_root)
        )
        snapshot["rans_reviews"] = _validation_review_table(Path(project_root))
        registry_file = _validation_execution_registry_path(Path(project_root))
        snapshot["execution_registry"] = (
            _load_validation_execution_registry(Path(project_root))
            if registry_file.is_file()
            else _validation_execution_registry(Path(project_root))
        )
    return snapshot


def validation_live_execution(
    project_root: Path,
    *,
    follow_active_execution: bool,
    pinned_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the live Validation Lab target freshly from persistent state."""
    return _resolve_validation_live_execution(
        Path(project_root),
        follow_active_execution=follow_active_execution,
        pinned_run_id=pinned_run_id,
    )


def validation_monitor_snapshot(
    case: Path,
    *,
    mode: str,
    run_id: str,
    topology: str,
    mesh_level: str,
    cell_count: int,
    stage: str = "",
    tc_s: float | None = None,
    steps_planned: int | None = None,
    queue_position: int | None = None,
    queue_total: int | None = None,
    target_delta_t: float | None = None,
    phase_delta_t: float | None = None,
) -> dict[str, Any]:
    """Build one bounded incremental monitor snapshot for Streamlit."""
    return _build_validation_monitor_snapshot(
        case,
        mode=mode,
        run_id=run_id,
        topology=topology,
        mesh_level=mesh_level,
        cell_count=cell_count,
        stage=stage,
        tc_s=tc_s,
        steps_planned=steps_planned,
        queue_position=queue_position,
        queue_total=queue_total,
        target_delta_t=target_delta_t,
        phase_delta_t=phase_delta_t,
    )


def validation_urans_case_snapshot(
    project_root: Path,
    case_id: str,
) -> dict[str, Any]:
    """Inspect one canonical URANS case from its scientific identity."""
    study = _load_validation_study(Path(project_root))
    row = next(
        (
            value
            for value in (study.get("run_matrix") or {}).get("runs", [])
            if str(value.get("run_id")) == str(case_id)
            or str(value.get("case_id")) == str(case_id)
        ),
        None,
    )
    if row is None:
        raise KeyError(f"Unknown Validation Lab case: {case_id}")
    return _inspect_validation_urans_case(Path(project_root), row)


def save_validation_study_config(
    project_root: Path,
    config: dict[str, Any],
) -> Path:
    """Atomically save only the laboratory-owned configuration."""
    config = _migrate_validation_study_config(config)
    if float(config.get("study_angle_deg", 8.0)) != 8.0:
        raise ValueError("The first validation campaign is locked to alpha=8 deg")
    validation = config.get("validation_study") or {}
    if float(validation.get("alpha_deg", 8.0)) != 8.0:
        raise ValueError("validation_study.alpha_deg is locked to 8 deg")
    if validation.get("time_policy") != "fixed_staged":
        raise ValueError("The validation laboratory requires time_policy=fixed_staged")
    if list(validation.get("startup_factors") or []) != [0.25, 0.5, 1.0]:
        raise ValueError("Startup factors must remain 0.25/0.5/1.0")
    safety = config.get("safety") or {}
    if int(validation.get("mpi_ranks", 8)) > 8:
        raise ValueError("The validation laboratory permits at most 8 MPI ranks")
    rans = validation.get("rans_base_states") or {}
    urans = validation.get("urans") or {}
    if int(
        rans.get(
            "minimum_simple_iterations_before_convergence_check",
            10000,
        )
    ) != 10000:
        raise ValueError(
            "RANS convergence cannot be evaluated before absolute SIMPLE "
            "iteration 10000"
        )
    if bool(rans.get("native_residual_control_enabled", False)):
        raise ValueError(
            "Native SIMPLE residualControl must remain disabled; the external "
            "Validation Lab gate owns stopping decisions after iteration 10000"
        )
    if int(rans.get("simple_non_orthogonal_correctors", 0)) < 0:
        raise ValueError("SIMPLE non-orthogonal correctors cannot be negative")
    if int(urans.get("pimple_non_orthogonal_correctors", 1)) < 0:
        raise ValueError("PIMPLE non-orthogonal correctors cannot be negative")
    pimple = dict(urans.get("pimple") or {})
    expected_pimple = {
        "nOuterCorrectors": int(
            urans.get("pimple_outer_correctors", 3)
        ),
        "nCorrectors": int(urans.get("pimple_correctors", 2)),
        "nNonOrthogonalCorrectors": int(
            urans.get("pimple_non_orthogonal_correctors", 1)
        ),
    }
    if any(
        int(pimple.get(name, value)) != value
        for name, value in expected_pimple.items()
    ):
        raise ValueError(
            "Nested PIMPLE settings must match their compatibility mirrors"
        )
    supported_schemes = {"Euler", "backward", "CrankNicolson"}
    for stage_group in ("startup_stages",):
        stages = list(urans.get(stage_group) or [])
        if not stages:
            raise ValueError(f"{stage_group} cannot be empty")
        for stage in stages:
            if str(stage.get("scheme")) not in supported_schemes:
                raise ValueError(
                    f"Unsupported {stage_group} scheme: {stage.get('scheme')}"
                )
            if float(stage.get("dt_factor") or 0.0) <= 0.0:
                raise ValueError(
                    f"{stage_group} dt_factor must be positive"
                )
            if str(stage.get("duration_mode") or "steps") not in {
                "steps",
                "t_star",
            }:
                raise ValueError(
                    f"{stage_group} duration_mode must be steps or t_star"
                )
            if float(
                stage.get("duration", stage.get("steps", 0)) or 0.0
            ) <= 0.0:
                raise ValueError(
                    f"{stage_group} duration must be positive"
                )
    if int(urans.get("monitor_refresh_seconds", 30)) not in {15, 30, 60}:
        raise ValueError("Monitor refresh must be 15, 30 or 60 seconds")
    postprocess = dict(config.get("postprocess") or {})
    supported_scales = {"exact", "robust", "manual"}
    static_scale = str(
        postprocess.get("static_scale_mode") or "exact"
    ).removeprefix("global_")
    animation_scale = str(
        postprocess.get("animation_scale_mode") or "global_exact"
    ).removeprefix("global_")
    if static_scale not in supported_scales:
        raise ValueError("Unsupported static postprocess scale mode")
    if animation_scale not in supported_scales:
        raise ValueError("Unsupported animation postprocess scale mode")
    percentiles = list(
        postprocess.get("robust_percentiles") or [1.0, 99.0]
    )
    if (
        len(percentiles) != 2
        or not 0.0 <= float(percentiles[0]) < float(percentiles[1]) <= 100.0
    ):
        raise ValueError("Invalid robust postprocess percentile interval")
    for name, bounds in dict(
        postprocess.get("manual_scales") or {}
    ).items():
        values = list(bounds or [])
        if len(values) != 2 or float(values[0]) >= float(values[1]):
            raise ValueError(f"Invalid manual postprocess scale for {name}")
    if safety.get("dry_run_default") is not True:
        raise ValueError("The validation laboratory must remain dry-run by default")
    path = _validation_active_workspace_root(Path(project_root)) / "study_config.json"
    return _write_validation_json_atomic(path, config)


def mesh_refinement_analysis_command(project_root: Path) -> list[str]:
    return python_command(
        project_root,
        "CFD_2D/scripts/ramair_2d_mesh_refinement_analysis.py",
        "--project-root", project_root,
    )


def case_library_command(
    project_root: Path,
    action: str,
    stage: str | None = None,
    case_name: str | None = None,
    variant: str | None = None,
    alpha: float | None = None,
    description: str = "",
    existing_action: str = "archive",
    package_name: str | None = None,
) -> list[str]:
    command = python_command(
        project_root,
        "CFD_2D/scripts/ramair_case_library.py",
        "--project-root", project_root,
        action,
    )
    if action == "list":
        return command
    if action == "create":
        if not case_name or not variant or alpha is None:
            raise ValueError("Case-library create requires case_name, variant and alpha.")
        command.extend([
            "--case-name", case_name,
            "--variant", variant,
            "--alpha", str(float(alpha)),
            "--description", description,
        ])
        return command
    if action == "restore-workspace":
        if not case_name:
            raise ValueError("Case-library restore-workspace requires case_name.")
        command.extend([
            "--case-name", case_name,
            "--existing-action", existing_action,
        ])
        return command
    if action == "activate-configuration":
        if not case_name or not package_name:
            raise ValueError(
                "Case-library activate-configuration requires case_name "
                "and package_name."
            )
        command.extend([
            "--case-name", case_name,
            "--package-name", package_name,
            "--existing-action", existing_action,
        ])
        return command
    if not stage or not case_name:
        raise ValueError("Case-library save/restore requires stage and case_name.")
    command.extend(["--stage", stage, "--case-name", case_name, "--existing-action", existing_action])
    if variant:
        command.extend(["--variant", variant])
    if alpha is not None:
        command.extend(["--alpha", str(float(alpha))])
    if action == "save":
        command.extend(["--description", description])
    if package_name:
        command.extend(["--package-name", package_name])
    return command


def saved_cases(project_root: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for manifest_path in sorted(project_path(project_root, "results_library").glob("*/case_manifest.json")):
        try:
            data = read_json(manifest_path, {}) or {}
            if isinstance(data, dict):
                data["folder"] = manifest_path.parent.name
                cases.append(data)
        except Exception:
            continue
    return cases


def command_text(command: Iterable[object]) -> str:
    return shlex.join(str(value) for value in command)


@dataclass
class Job:
    job_id: str
    stage: str
    command: list[str]
    cwd: str
    log_path: str
    status_path: str
    status: str
    pid: int | None
    returncode: int | None
    started_at: str
    finished_at: str | None = None
    pid_start_token: str | None = None
    process_group_id: int | None = None
    stop_requested_at: str | None = None
    stop_stage: str | None = None


class JobManager:
    """Small persistent process manager suitable for Streamlit reruns."""

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.state_dir = self.project_root / "CFD_2D/app_state/jobs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, tuple[subprocess.Popen[str], Any]] = {}

    def _status_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}.json"

    def _write(self, job: Job) -> None:
        _write_execution_json_atomic(Path(job.status_path), asdict(job))

    def start(self, stage: str, command: list[str]) -> Job:
        active = [
            job
            for job in self.list_jobs(limit=20)
            if self.poll(job).status in {"RUNNING", "STOP_REQUESTED", "STOPPING"}
        ]
        if active:
            raise RuntimeError(f"Another workflow job is still running: {active[0].stage} ({active[0].job_id})")
        job_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{stage}_{uuid.uuid4().hex[:6]}"
        log_path = project_path(self.project_root, "logs", "CFD 2D App", f"{job_id}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("w", encoding="utf-8", buffering=1)
        job_environment, foam_metadata = sourced_openfoam_environment()
        handle.write(
            f"Stage: {stage}\nCommand: {command_text(command)}\nWorking directory: {self.project_root}\n"
            f"OpenFOAM environment: {json.dumps(foam_metadata, ensure_ascii=False)}\n\n"
        )
        kwargs: dict[str, Any] = {
            "cwd": str(self.project_root),
            "stdout": handle,
            "stderr": subprocess.STDOUT,
            "text": True,
            "env": job_environment,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        status_path = self._status_path(job_id)
        job = Job(
            job_id=job_id,
            stage=stage,
            command=command,
            cwd=str(self.project_root),
            log_path=str(log_path),
            status_path=str(status_path),
            status="RUNNING",
            pid=int(process.pid),
            returncode=None,
            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            pid_start_token=_process_start_token(process.pid),
            process_group_id=_process_group_id(process.pid),
        )
        self._processes[job_id] = (process, handle)
        self._write(job)
        threading.Thread(
            target=self._wait_and_finalize,
            args=(job, process, handle),
            name=f"ramair-job-{job_id}",
            daemon=True,
        ).start()
        return job

    def _wait_and_finalize(self, job: Job, process: subprocess.Popen[str], handle: Any) -> None:
        """Persist completion independently of Streamlit fragment reruns."""
        returncode = process.wait()
        try:
            handle.flush()
            handle.close()
        finally:
            self._processes.pop(job.job_id, None)
        try:
            current = self.load(Path(job.status_path))
        except Exception:
            current = job
        current.returncode = int(returncode)
        if current.status in {"STOP_REQUESTED", "STOPPING"}:
            current.status = "PAUSED_RESTARTABLE"
        else:
            current.status = "COMPLETED" if returncode == 0 else "FAILED"
        current.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write(current)

    def load(self, path: Path) -> Job:
        return Job(**read_json(path, {}))

    def list_jobs(self, limit: int = 30) -> list[Job]:
        paths = sorted(self.state_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        jobs: list[Job] = []
        for path in paths[:limit]:
            try:
                jobs.append(self.load(path))
            except Exception:
                continue
        return jobs

    def poll(self, job: Job) -> Job:
        entry = self._processes.get(job.job_id)
        if entry is not None:
            process, handle = entry
            returncode = process.poll()
            if returncode is not None and job.status in {"RUNNING", "STOP_REQUESTED", "STOPPING"}:
                handle.flush()
                handle.close()
                self._processes.pop(job.job_id, None)
                job.returncode = int(returncode)
                if job.status in {"STOP_REQUESTED", "STOPPING"}:
                    job.status = "PAUSED_RESTARTABLE"
                else:
                    job.status = "COMPLETED" if returncode == 0 else "FAILED"
                job.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
                self._write(job)
            return job
        # The monitor thread owns finalization. Streamlit fragments and tests
        # can still hold an older Job instance, so refresh it from disk before
        # attempting PID-based recovery.
        try:
            persisted = self.load(Path(job.status_path))
            if persisted.status != job.status or persisted.returncode != job.returncode:
                return persisted
        except Exception:
            pass
        if job.status in {"RUNNING", "STOP_REQUESTED", "STOPPING"} and job.pid:
            if not _pid_is_alive(job.pid, job.pid_start_token):
                # The monitor thread writes the return code immediately after
                # process exit. Avoid racing it and mislabelling a successful
                # job as UNKNOWN_FINISHED.
                if time.time() - Path(job.status_path).stat().st_mtime > 5.0:
                    job.status = (
                        "PAUSED_RESTARTABLE"
                        if job.status in {"STOP_REQUESTED", "STOPPING"}
                        else "UNKNOWN_FINISHED"
                    )
                    job.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._write(job)
            else:
                proc_cmdline = Path(f"/proc/{job.pid}/cmdline")
                if proc_cmdline.is_file():
                    command_line = proc_cmdline.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
                    identity_candidates = [
                        str(token)
                        for token in job.command[1:]
                        if str(token).endswith(".py") or "/" in str(token) or "\\" in str(token)
                    ]
                    if identity_candidates and not any(
                        candidate in command_line or Path(candidate).name in command_line
                        for candidate in identity_candidates[:2]
                    ):
                        job.status = "UNKNOWN_FINISHED"
                        job.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        self._write(job)
        return job

    def mark_stop_requested(self, job: Job) -> Job:
        if job.status == "RUNNING":
            job.status = "STOP_REQUESTED"
            job.stop_requested_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            job.stop_stage = "controlDict_writeNow"
            self._write(job)
        return job

    def stop(self, job: Job) -> Job:
        if job.status not in {"RUNNING", "STOP_REQUESTED"} or not job.pid:
            return job
        try:
            if os.name != "nt":
                os.killpg(int(job.process_group_id or job.pid), signal.SIGINT)
            else:
                os.kill(job.pid, signal.SIGTERM)
            job.status = "STOPPING"
            job.stop_requested_at = job.stop_requested_at or time.strftime("%Y-%m-%dT%H:%M:%S%z")
            job.stop_stage = "orchestrator_sigint"
            self._write(job)
        except ProcessLookupError:
            job.status = "UNKNOWN_FINISHED"
            self._write(job)
        return job

    def force_stop(self, job: Job) -> Job:
        """Escalate a requested stop to the recorded solver, then orchestrator."""
        if job.status not in {"RUNNING", "STOP_REQUESTED", "STOPPING"}:
            return job
        case_dir = openfoam_case_from_command(job.command) or validation_runtime_case(self.project_root)
        solver_result: dict[str, Any] | None = None
        if case_dir is not None:
            solver_result = _signal_solver_process(case_dir, signal.SIGINT)
        if not solver_result or solver_result.get("status") != "SIGNALLED":
            self.stop(job)
        job.status = "STOPPING"
        job.stop_stage = "solver_sigint" if solver_result else "orchestrator_sigint"
        job.stop_requested_at = job.stop_requested_at or time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._write(job)
        return job

    def active_jobs(self) -> list[Job]:
        """Return only jobs that still own a live workflow process."""
        return [
            job
            for job in self.list_jobs(limit=100)
            if self.poll(job).status in {"RUNNING", "STOP_REQUESTED", "STOPPING"}
        ]


def touch_application_heartbeat(project_root: Path) -> Path:
    """Record that at least one browser session is rendering the application."""
    path = project_root.resolve() / "CFD_2D/app_state/application_heartbeat.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Streamlit can execute the sidebar and console fragments concurrently.
    # A shared ``.tmp`` name lets one fragment replace a file that the other
    # still expects, which caused the intermittent FileNotFoundError at startup.
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    with _HEARTBEAT_WRITE_LOCK:
        try:
            temporary.write_text(
                json.dumps(
                    {
                        "unix_time": time.time(),
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "streamlit_pid": os.getpid(),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def _write_shutdown_marker(project_root: Path, reason: str) -> Path:
    state_dir = project_root.resolve() / "CFD_2D/app_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / "shutdown_wsl.request"
    marker.write_text(
        json.dumps(
            {
                "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "streamlit_pid": os.getpid(),
                "reason": reason,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def start_application_idle_watchdog(project_root: Path, manager: JobManager) -> None:
    """Stop an abandoned UI after a configurable idle period.

    Streamlit fragments update the heartbeat while a browser tab is connected.
    The watchdog never stops the runtime while a CAE job is active. Set
    ``RAMAIR_APP_IDLE_SHUTDOWN_MIN=0`` to disable automatic idle shutdown.
    """
    global _IDLE_WATCHDOG_STARTED
    with _IDLE_WATCHDOG_LOCK:
        if _IDLE_WATCHDOG_STARTED:
            return
        _IDLE_WATCHDOG_STARTED = True

    idle_minutes = max(0.0, float(os.environ.get("RAMAIR_APP_IDLE_SHUTDOWN_MIN", "15")))
    if idle_minutes <= 0.0 or os.name == "nt":
        return
    heartbeat = touch_application_heartbeat(project_root)

    def monitor() -> None:
        threshold = idle_minutes * 60.0
        while True:
            time.sleep(min(30.0, max(5.0, threshold / 6.0)))
            try:
                data = read_json(heartbeat, {}) or {}
                last_seen = float(data.get("unix_time", heartbeat.stat().st_mtime))
                if time.time() - last_seen < threshold or manager.active_jobs():
                    continue
                close_project_viewers(project_root)
                _write_shutdown_marker(project_root, "browser_idle_timeout")
                os.kill(os.getpid(), signal.SIGTERM)
                return
            except (OSError, TypeError, ValueError):
                continue

    threading.Thread(target=monitor, name="ramair-app-idle-watchdog", daemon=True).start()


def request_application_shutdown(project_root: Path, manager: JobManager) -> Path:
    """Stop Streamlit after the response and ask the Windows launcher to release WSL.

    The request is rejected while a managed CAE task is running.  A detached
    helper sends SIGTERM after Streamlit has returned the button response; the
    Windows launcher consumes the marker and terminates the distro only after
    confirming that no RamAir stage process remains.
    """
    active = manager.active_jobs()
    if active:
        raise RuntimeError(
            f"Cannot close the application while {active[0].stage} is {active[0].status}. "
            "Stop or finish the task first."
        )
    close_project_viewers(project_root)
    marker = _write_shutdown_marker(project_root, "explicit_user_request")
    if os.name == "nt":
        raise RuntimeError("Application shutdown must be requested from the WSL runtime, not native Windows.")
    subprocess.Popen(
        ["bash", "-lc", f"sleep 1.5; kill -TERM {os.getpid()} 2>/dev/null || true"],
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return marker


def tail_file(path: Path, lines: int = 160) -> str:
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-max(1, int(lines)):])


def latest_files(root: Path, patterns: Iterable[str], limit: int = 30) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    return sorted(set(files), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _wsl_windows_path(path: Path) -> str:
    completed = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError(f"wslpath could not convert {path}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _viewer_state_path(project_root: Path) -> Path:
    return project_root.resolve() / "CFD_2D/app_state/viewers.json"


def _write_viewer_state(project_root: Path, entries: list[dict[str, Any]]) -> None:
    path = _viewer_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"viewers": entries}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _register_project_viewer(project_root: Path, pid: int, kind: str, command: list[str]) -> None:
    with _VIEWER_STATE_LOCK:
        state = read_json(_viewer_state_path(project_root), {}) or {}
        entries = [entry for entry in state.get("viewers", []) if int(entry.get("pid", -1)) != int(pid)]
        entries.append({
            "pid": int(pid),
            "kind": str(kind),
            "command": list(command),
            "project_root": str(project_root.resolve()),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _write_viewer_state(project_root, entries)


def _unregister_project_viewer(project_root: Path, pid: int) -> None:
    with _VIEWER_STATE_LOCK:
        state = read_json(_viewer_state_path(project_root), {}) or {}
        entries = [entry for entry in state.get("viewers", []) if int(entry.get("pid", -1)) != int(pid)]
        _write_viewer_state(project_root, entries)


def _process_cmdline(pid: int) -> str:
    path = Path(f"/proc/{int(pid)}/cmdline")
    if not path.is_file():
        return ""
    return path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")


def _process_cwd(pid: int) -> str:
    try:
        return str(Path(f"/proc/{int(pid)}/cwd").resolve(strict=True))
    except (OSError, RuntimeError):
        return ""


def close_project_viewers(project_root: Path) -> dict[str, object]:
    """Terminate only ParaView/Gmsh viewers launched for this project."""
    project = str(project_root.resolve())
    project_markers = [project]
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("wslpath"):
        try:
            project_markers.append(_wsl_windows_path(project_root))
        except (OSError, RuntimeError):
            pass

    def belongs_to_project(pid: int, command_line: str) -> bool:
        if any(marker and marker in command_line for marker in project_markers):
            return True
        cwd = _process_cwd(pid)
        return bool(cwd and (cwd == project or cwd.startswith(project + os.sep)))

    state = read_json(_viewer_state_path(project_root), {}) or {}
    candidates = {int(entry.get("pid")) for entry in state.get("viewers", []) if entry.get("pid")}
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for process_dir in proc_root.iterdir():
            if not process_dir.name.isdigit():
                continue
            pid = int(process_dir.name)
            command_line = _process_cmdline(pid)
            lowered = command_line.lower()
            if ("paraview" in lowered or "gmsh" in lowered) and belongs_to_project(pid, command_line):
                candidates.add(pid)
    stopped: list[int] = []
    skipped: list[int] = []
    for pid in sorted(candidates):
        command_line = _process_cmdline(pid)
        lowered = command_line.lower()
        if command_line and not belongs_to_project(pid, command_line) and "checkmesh_problem_viewer.py" not in lowered:
            skipped.append(pid)
            continue
        try:
            if os.name != "nt":
                try:
                    os.killpg(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            continue
    _write_viewer_state(project_root, [])
    return {"stopped": stopped, "skipped": skipped}


def _write_checkmesh_paraview_script(
    script_path: Path,
    foam_marker: Path | None,
    vtk_files: list[Path],
    *,
    windows_paths: bool,
) -> Path:
    """Write a ParaView startup script that applies readers and frames data."""
    convert = _wsl_windows_path if windows_paths else lambda path: str(path.resolve())
    foam_value = convert(foam_marker) if foam_marker is not None else None
    vtk_values = [convert(path) for path in vtk_files]
    ignored_focus_tokens = ("shortedge", "twointernalfaces", "underdetermined")
    focus_indices = [
        index
        for index, path in enumerate(vtk_files)
        if not any(token in path.name.lower() for token in ignored_focus_tokens)
    ]
    if not focus_indices:
        focus_indices = list(range(len(vtk_files)))
    screenshot = convert(script_path.with_name("checkMesh_problem_view.png"))
    palette = [
        [0.84, 0.15, 0.16],
        [0.96, 0.55, 0.10],
        [0.20, 0.45, 0.85],
        [0.20, 0.65, 0.40],
        [0.60, 0.30, 0.80],
        [0.10, 0.65, 0.70],
    ]
    script = f'''from paraview.simple import *
try:
    from paraview.simple import _DisableFirstRenderCameraReset
    _DisableFirstRenderCameraReset()
except (ImportError, AttributeError):
    pass
view = GetActiveViewOrCreate("RenderView")
view.CameraParallelProjection = 1

foam_path = {repr(foam_value)}
vtk_paths = {json.dumps(vtk_values)}
focus_indices = {json.dumps(focus_indices)}
palette = {json.dumps(palette)}
base = None
base_display = None

if foam_path:
    base = OpenDataFile(foam_path)
    try:
        base.MeshRegions = ["internalMesh"]
    except Exception:
        pass
    base.UpdatePipeline()
    base_display = Show(base, view)
    base_display.Representation = "Surface With Edges"
    base_display.Opacity = 0.08
    try:
        ColorBy(base_display, None)
        base_display.DiffuseColor = [0.72, 0.75, 0.80]
        base_display.EdgeColor = [0.35, 0.38, 0.42]
    except Exception:
        pass

problem_sources = []
problem_displays = []
problem_highlights = []
focus_bounds = []
for index, vtk_path in enumerate(vtk_paths):
    source = OpenDataFile(vtk_path)
    source.UpdatePipeline()
    display = Show(source, view)
    display.Representation = "Surface With Edges"
    display.Opacity = 1.0
    color = palette[index % len(palette)]
    try:
        ColorBy(display, None)
        display.DiffuseColor = color
        display.AmbientColor = color
        display.EdgeColor = color
        display.LineWidth = 4.0
        display.PointSize = 7.0
    except Exception:
        pass
    problem_sources.append(source)
    problem_displays.append(display)
    if index in focus_indices:
        bounds = source.GetDataInformation().GetBounds()
        focus_bounds.append(bounds)
        try:
            edges = ExtractEdges(Input=source)
            edges.UpdatePipeline()
            tube = Tube(Input=edges)
            diagonal = max(
                ((bounds[1] - bounds[0]) ** 2 + (bounds[3] - bounds[2]) ** 2 + (bounds[5] - bounds[4]) ** 2) ** 0.5,
                1.0e-6,
            )
            tube.Radius = max(diagonal * 0.012, 1.0e-6)
            tube.NumberofSides = 12
            tube.UpdatePipeline()
            highlight = Show(tube, view)
            highlight.DiffuseColor = color
            highlight.AmbientColor = color
            problem_highlights.append(tube)
        except Exception:
            pass

# Frame the actual failed-quality sets, not the farfield or informational
# short-edge inventory. The base mesh is shown again after camera fitting.
if base is not None:
    Hide(base, view)
for index, source in enumerate(problem_sources):
    if index not in focus_indices:
        Hide(source, view)
ResetCamera(view)
if focus_bounds:
    xmin = min(bounds[0] for bounds in focus_bounds)
    xmax = max(bounds[1] for bounds in focus_bounds)
    ymin = min(bounds[2] for bounds in focus_bounds)
    ymax = max(bounds[3] for bounds in focus_bounds)
    zmin = min(bounds[4] for bounds in focus_bounds)
    zmax = max(bounds[5] for bounds in focus_bounds)
    cx, cy, cz = (0.5 * (xmin + xmax), 0.5 * (ymin + ymax), 0.5 * (zmin + zmax))
    span = max(xmax - xmin, ymax - ymin, zmax - zmin, 1.0e-6)
    view.CameraFocalPoint = [cx, cy, cz]
    view.CameraPosition = [cx + 1.8 * span, cy - 1.8 * span, cz + 1.3 * span]
    view.CameraViewUp = [0.0, 0.0, 1.0]
    view.CameraParallelScale = max(0.72 * span, 1.0e-6)
if base is not None:
    Show(base, view)
Render(view)
try:
    SaveScreenshot({json.dumps(screenshot)}, view, ImageResolution=[1600, 1000])
except Exception:
    pass
'''
    script_path.write_text(script, encoding="utf-8")
    return script_path


def results_library_locations(project_root: Path) -> dict[str, str]:
    """Return the real library path and its Windows-visible representation."""
    root = project_path(project_root, "results_library")
    root.mkdir(parents=True, exist_ok=True)
    locations = {"linux": str(root.resolve())}
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("wslpath"):
        locations["windows"] = _wsl_windows_path(root)
    else:
        locations["windows"] = str(root.resolve())
    return locations


def open_results_library(project_root: Path) -> int:
    """Open the actual Results library, including native-WSL storage."""
    locations = results_library_locations(project_root)
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("explorer.exe"):
        process = subprocess.Popen(
            ["explorer.exe", locations["windows"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(process.pid)
    if os.name == "nt":
        os.startfile(locations["windows"])  # type: ignore[attr-defined]
        return 0
    opener = shutil.which("xdg-open")
    if opener is None:
        raise FileNotFoundError("No folder opener was found (explorer.exe/xdg-open).")
    process = subprocess.Popen(
        [opener, locations["linux"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(process.pid)


def open_local_folder(path: Path) -> int:
    """Open one existing product folder in the host-visible file browser."""
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("explorer.exe"):
        visible = _wsl_windows_path(folder)
        process = subprocess.Popen(
            ["explorer.exe", visible],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(process.pid)
    if os.name == "nt":
        os.startfile(str(folder))  # type: ignore[attr-defined]
        return 0
    opener = shutil.which("xdg-open")
    if opener is None:
        raise FileNotFoundError("No folder opener was found")
    process = subprocess.Popen(
        [opener, str(folder)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(process.pid)


def validation_paraview_readiness(
    case_dir: Path | None,
    *,
    selected_run_id: str | None = None,
) -> dict[str, Any]:
    """Validate a registered RANS/URANS case before constructing a viewer command.

    This deliberately returns data instead of guessing a fallback location.  A
    RANS final state at ``0`` is valid; an URANS selection reports its latest
    reconstructed or processor-only positive time and any reconstruction need.
    """
    base = {
        "selected_run_id": selected_run_id,
        "artifact_path": None,
        "latest_time": None,
        "reconstruction_state": "NOT_CHECKED",
        "missing_requirement": None,
        "recommended_action": None,
    }
    if case_dir is None or not str(case_dir).strip():
        return {
            **base,
            "status": "ERROR",
            "error_code": "PARAVIEW_CASE_PATH_MISSING",
            "missing_requirement": "registered case path",
            "recommended_action": "Select one concrete RANS or URANS execution.",
        }
    case = Path(case_dir).expanduser().resolve()
    if not case.is_dir() or not (case / "system" / "controlDict").is_file():
        return {
            **base,
            "status": "ERROR",
            "error_code": "PARAVIEW_OPENFOAM_CASE_INVALID",
            "artifact_path": str(case),
            "missing_requirement": "OpenFOAM system/controlDict",
            "recommended_action": "Prepare the selected case or correct its registered path.",
        }
    positive: list[float] = []
    for path in case.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0.0:
            positive.append(value)
    processor_times: list[set[float]] = []
    for processor in sorted(case.glob("processor*")):
        if not processor.is_dir():
            continue
        values: set[float] = set()
        for path in processor.iterdir():
            try:
                value = float(path.name)
            except (ValueError, OSError):
                continue
            if path.is_dir() and value > 0.0:
                values.add(value)
        if values:
            processor_times.append(values)
    common = set.intersection(*processor_times) if processor_times else set()
    latest = max(positive, default=max(common, default=None))
    reconstruction = (
        "RECONSTRUCTED" if positive else "PROCESSOR_ONLY" if common else "ZERO_OR_RANS_FINAL"
    )
    vtk_set: dict[str, Any] = {}
    if "checkpoints" in {part.lower() for part in case.parts} or (case / "VTK").is_dir():
        vtk_set = resolve_final_vtk_artifacts(case, generate_if_missing=True)
    if vtk_set.get("status") == "READY":
        return {
            **base,
            "status": "READY",
            "error_code": None,
            "artifact_path": str(case),
            "artifact_kind": "RESOLVED_VTK_SET",
            "reader_paths": list(vtk_set.get("reader_paths") or []),
            "latest_time": vtk_set.get("iteration"),
            "reconstruction_state": reconstruction,
            "reconstruction_required": False,
            "paraview_artifact_set": vtk_set,
            "recommended_action": "Open the exact VTK set for the selected final RANS iteration.",
        }
    return {
        **base,
        "status": "READY",
        "error_code": None,
        "artifact_path": str(case),
        "artifact_kind": "OPENFOAM_CASE",
        "latest_time": latest,
        "reconstruction_state": reconstruction,
        "reconstruction_required": bool(not positive and common),
        "recommended_action": (
            "Reconstruct the selected latest URANS time before opening ParaView."
            if not positive and common
            else "Open the registered case with the absolute path."
        ),
    }


def open_paraview_case(project_root: Path, case_dir: Path) -> dict[str, Any]:
    """Open an OpenFOAM case and register the viewer with the app watchdog."""
    readiness = validation_paraview_readiness(case_dir)
    if readiness.get("status") != "READY":
        raise RuntimeError(json.dumps(readiness, ensure_ascii=False))
    if readiness.get("reconstruction_required"):
        raise RuntimeError(json.dumps({
            **readiness,
            "status": "ERROR",
            "error_code": "PARAVIEW_RECONSTRUCTION_REQUIRED",
        }, ensure_ascii=False))
    if readiness.get("artifact_kind") == "RESOLVED_VTK_SET":
        result = _launch_paraview_vtk_set(
            [Path(str(value)) for value in readiness.get("reader_paths") or []],
            support_dir=(
                Path(str(readiness["artifact_path"]))
                / "postProcessing/ParaViewResolved"
            ),
            selected_time=float(readiness.get("latest_time") or 0.0),
        )
    else:
        result = _launch_paraview_case(Path(str(readiness["artifact_path"])))
    if result.get("status") != "OPEN_REQUESTED":
        raise RuntimeError(
            f"ParaView did not start: {result.get('reason') or result.get('error') or result}"
        )
    pid = int(result.get("pid") or 0)
    if pid > 0:
        _register_project_viewer(
            project_root,
            pid,
            "paraview_case",
            [str(value) for value in result.get("command", [])],
        )
    return {**result, "readiness": readiness}


def open_mesh_viewer(project_root: Path, mesh_path: Path, viewer: str = "auto") -> int:
    """Open a mesh through Windows Gmsh/Python or the native Linux WSLg GUI."""
    if not mesh_path.is_file():
        raise FileNotFoundError(f"Mesh file not found: {mesh_path}")
    if viewer not in {"auto", "windows_python", "linux_wslg"}:
        raise ValueError(f"Unsupported Gmsh viewer: {viewer}")
    if viewer == "auto":
        viewer = "windows_python" if os.environ.get("WSL_DISTRO_NAME") else "linux_wslg"

    log_path = project_path(project_root, "logs", "CFD 2D App", "gmsh_viewer.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if viewer == "windows_python":
        if not os.environ.get("WSL_DISTRO_NAME"):
            raise RuntimeError("Windows Python Gmsh viewer is available only when the application runs inside WSL.")
        python_launcher = Path("/mnt/c/Windows/py.exe")
        pythonw_launcher = Path("/mnt/c/Windows/pyw.exe")
        worker = project_root / "CFD_2D/app/windows_gmsh_viewer.py"
        if not python_launcher.is_file() or not pythonw_launcher.is_file():
            raise FileNotFoundError("Windows py.exe/pyw.exe launcher was not found under C:/Windows")
        windows_worker = _wsl_windows_path(worker)
        windows_mesh = _wsl_windows_path(mesh_path)
        windows_log = _wsl_windows_path(log_path)
        probe = subprocess.run(
            [str(python_launcher), "-3", windows_worker, "--probe"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        if probe.returncode != 0:
            raise RuntimeError(
                "Windows Python cannot import the Gmsh API. Install it with "
                f"'py -3 -m pip install gmsh==4.15.2'.\n{probe.stdout}"
            )
        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launching Windows Python Gmsh\n"
                f"probe={probe.stdout.strip()}\nmesh={windows_mesh}\n"
            )
        process = subprocess.Popen(
            [str(pythonw_launcher), "-3", windows_worker, windows_mesh, "--log", windows_log],
            cwd=str(mesh_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _register_project_viewer(
            project_root,
            int(process.pid),
            "gmsh_windows_python",
            [str(pythonw_launcher), "-3", windows_worker, windows_mesh],
        )
        time.sleep(0.8)
        returncode = process.poll()
        if returncode not in {None, 0}:
            raise RuntimeError(f"Windows Gmsh viewer exited immediately with code {returncode}. Inspect {log_path}.")
        return int(process.pid)

    candidates = [
        Path.home() / ".local/opt/gmsh-4.15.2/bin/gmsh",
        Path.home() / ".local/bin/gmsh",
    ]
    executable = next((str(path) for path in candidates if path.is_file()), None) or shutil.which("gmsh")
    if not executable:
        raise FileNotFoundError("Gmsh GUI executable was not found")
    version_probe = subprocess.run(
        [executable, "--version"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    version = version_probe.stdout.strip().splitlines()[-1] if version_probe.stdout.strip() else "unknown"
    if version_probe.returncode != 0:
        raise RuntimeError(f"Gmsh executable failed its version probe: {executable}\n{version_probe.stdout}")
    handle = log_path.open("a", encoding="utf-8")
    handle.write(
        f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launching Gmsh {version}\n"
        f"executable={executable}\nmesh={mesh_path.resolve()}\n"
    )
    handle.flush()
    environment = os.environ.copy()
    if os.name != "nt" and environment.get("WSL_DISTRO_NAME"):
        # WSLg normally exports these values. Supplying only missing defaults
        # avoids falling back to a Windows file association or a headless copy.
        environment.setdefault("DISPLAY", ":0")
        environment.setdefault("WAYLAND_DISPLAY", "wayland-0")
        environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        environment.setdefault("PULSE_SERVER", "unix:/mnt/wslg/PulseServer")
    kwargs: dict[str, Any] = {
        "cwd": str(mesh_path.parent),
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "env": environment,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen([executable, str(mesh_path.resolve())], **kwargs)
    _register_project_viewer(
        project_root,
        int(process.pid),
        "gmsh_linux",
        [str(executable), str(mesh_path.resolve())],
    )
    handle.close()

    def reap_viewer() -> None:
        returncode = process.wait()
        _unregister_project_viewer(project_root, int(process.pid))
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"viewer_pid={process.pid} returncode={returncode}\n")

    threading.Thread(target=reap_viewer, name=f"gmsh-viewer-{process.pid}", daemon=True).start()
    time.sleep(0.6)
    returncode = process.poll()
    if returncode is not None and returncode != 0:
        details = tail_file(log_path, 40)
        raise RuntimeError(
            f"Gmsh {version} closed immediately with code {returncode}. "
            f"Inspect {log_path}.\n{details}"
        )
    return int(process.pid)


def open_validation_mesh_viewer(
    project_root: Path,
    mesh_id: str,
    *,
    viewer: str = "linux_wslg",
) -> dict[str, Any]:
    """Open the registered validation `.msh` without regenerating it."""
    snapshot = _load_validation_study(Path(project_root))
    mesh = next(
        (
            row
            for row in snapshot.get("mesh_registry", {}).get("meshes", [])
            if str(row.get("id")) == mesh_id
        ),
        None,
    )
    if mesh is None:
        raise KeyError(f"Unknown validation-study mesh: {mesh_id}")
    package = Path(str(mesh["mesh_package"])).resolve()
    manifest = read_json(package / "mesh_package_manifest.json", {}) or {}
    reported = [
        manifest.get("mesh_final_msh"),
        manifest.get("mesh_file"),
        (manifest.get("files") or {}).get("mesh_final_msh")
        if isinstance(manifest.get("files"), dict)
        else None,
    ]
    candidates = [
        Path(str(value)) if Path(str(value)).is_absolute() else package / str(value)
        for value in reported
        if value
    ]
    candidates += [
        package / "Mesh Data/mesh_final.msh",
        package / "Mesh Data/mesh_attempt_001/mesh.msh",
        package / "Mesh Data/mesh.msh",
    ]
    mesh_path = next((path.resolve() for path in candidates if path.is_file()), None)
    if mesh_path is None:
        raise FileNotFoundError(
            f"The registered package {mesh_id} has no mesh_final.msh. "
            "The OpenFOAM polyMesh is not opened as a Gmsh file."
        )
    if package not in mesh_path.parents:
        raise RuntimeError(
            f"Resolved mesh is outside its registered package: {mesh_path}"
        )
    digest = hashlib.sha256(mesh_path.read_bytes()).hexdigest()
    pid = open_mesh_viewer(Path(project_root), mesh_path, viewer=viewer)
    request = {
        "status": "OPEN_REQUESTED",
        "mesh_id": mesh_id,
        "mesh_path": str(mesh_path),
        "mesh_sha256": digest,
        "mesh_hash": mesh.get("mesh_hash"),
        "viewer": viewer,
        "pid": pid,
        "regenerated": False,
        "modified": False,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    report = (
        _validation_active_workspace_root(Path(project_root))
        / "logs/gmsh_viewer_last.json"
    )
    _write_validation_json_atomic(report, request)
    return request


def open_checkmesh_problem_viewer(project_root: Path, variant: str) -> int:
    """Open the converted mesh and exact checkMesh VTK problem sets in ParaView."""
    mesh_root = project_root / "CFD_2D/meshes" / variant
    problem_dir = mesh_root / "checkMesh_problem_locations"
    vtk_files = sorted(problem_dir.glob("*.vtk")) if problem_dir.is_dir() else []
    if not vtk_files:
        raise FileNotFoundError(f"No checkMesh problem VTK files were found under {problem_dir}")
    foam_marker: Path | None = None
    if (mesh_root / "constant/polyMesh/boundary").is_file():
        foam_marker = mesh_root / "checkMesh_quality.foam"
        foam_marker.touch(exist_ok=True)

    requested = os.environ.get("RAMAIR_PARAVIEW_EXECUTABLE")
    candidates: list[Path] = [Path(requested).expanduser()] if requested else []
    native = shutil.which("paraview")
    if native:
        candidates.append(Path(native))
    if os.environ.get("WSL_DISTRO_NAME"):
        for pattern in (
            "/mnt/c/Program Files/ParaView*/bin/paraview.exe",
            "/mnt/c/Program Files (x86)/ParaView*/bin/paraview.exe",
        ):
            candidates.extend(Path("/").glob(pattern.lstrip("/")))
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise FileNotFoundError("ParaView was not found; install it or set RAMAIR_PARAVIEW_EXECUTABLE")

    windows_executable = bool(executable.suffix.lower() == ".exe" and os.environ.get("WSL_DISTRO_NAME"))
    startup_script = _write_checkmesh_paraview_script(
        mesh_root / "checkMesh_problem_viewer.py",
        foam_marker,
        vtk_files,
        windows_paths=windows_executable,
    )
    script_argument = _wsl_windows_path(startup_script) if windows_executable else str(startup_script.resolve())
    # Ignore stale ParaView user/session state. This prevents crash-recovery or
    # copy-mode dialogs from replacing the scripted readers and emptying the view.
    arguments = ["--disable-registry", f"--script={script_argument}"]
    log_path = project_path(project_root, "logs", "CFD 2D App", "paraview_problem_viewer.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    environment = os.environ.copy()
    if os.environ.get("WSL_DISTRO_NAME") and executable.suffix.lower() != ".exe":
        environment.setdefault("DISPLAY", ":0")
        environment.setdefault("WAYLAND_DISPLAY", "wayland-0")
        environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        environment.setdefault("PULSE_SERVER", "unix:/mnt/wslg/PulseServer")
        # Ubuntu's ParaView 5.10 packages can omit dist-packages from the
        # embedded Python search path. Preserve any user path while making the
        # packaged paraview.simple module available to --script and pvpython.
        dist_packages = "/usr/lib/python3/dist-packages"
        python_path = environment.get("PYTHONPATH", "")
        path_entries = [entry for entry in python_path.split(os.pathsep) if entry]
        if dist_packages not in path_entries:
            environment["PYTHONPATH"] = os.pathsep.join([dist_packages, *path_entries])
    log.write(
        f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launching ParaView\n"
        f"executable={executable}\nmesh_root={mesh_root.resolve()}\n"
        f"startup_script={startup_script.resolve()}\n"
        f"problem_sets={json.dumps([path.name for path in vtk_files], ensure_ascii=False)}\n"
        f"display={environment.get('DISPLAY')} wayland={environment.get('WAYLAND_DISPLAY')}\n"
        f"pythonpath={environment.get('PYTHONPATH')}\n"
    )
    log.flush()
    process = subprocess.Popen(
        [str(executable), *arguments],
        cwd=str(mesh_root),
        stdout=log,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    _register_project_viewer(
        project_root,
        int(process.pid),
        "paraview_checkmesh",
        [str(executable), *arguments],
    )
    log.close()

    def reap_viewer() -> None:
        returncode = process.wait()
        _unregister_project_viewer(project_root, int(process.pid))
        with log_path.open("a", encoding="utf-8") as viewer_log:
            viewer_log.write(f"viewer_pid={process.pid} returncode={returncode}\n")

    threading.Thread(
        target=reap_viewer,
        name=f"paraview-viewer-{process.pid}",
        daemon=True,
    ).start()
    time.sleep(1.0)
    returncode = process.poll()
    if returncode not in {None, 0}:
        raise RuntimeError(
            f"ParaView closed immediately with code {returncode}. Inspect {log_path}.\n"
            f"{tail_file(log_path, 50)}"
        )
    return int(process.pid)
