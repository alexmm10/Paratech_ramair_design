#!/usr/bin/env python3
"""Save and restore reusable RamAir workflow stages under ``Results/``."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Iterable

from project_layout import canonicalize_project_relative, find_project_root, project_path


STAGE_FOLDERS = {
    "geometry": "Geometry",
    "case": "Operating Case",
    "mesh": "Mesh",
    "solver": "Solver Configuration",
    "simulation": "Simulation",
    "postprocess": "Postprocess",
}

STAGE_COLLECTION_FOLDERS = {
    "geometry": "Geometry Packages",
    "case": "CFD Cases",
    "mesh": "Meshes",
    "solver": "Solver Configurations",
    "simulation": "Simulations",
    "postprocess": "Postprocess Packages",
}

STANDARD_SOLVER_PACKAGE = "topology_solver_v11"
STANDARD_SOLVER_CONFIG = Path("CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json")


def safe_alpha_dir(alpha: float) -> str:
    return f"alpha_{float(alpha):+0.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def safe_case_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError("Case name must contain at least one letter or number.")
    return cleaned[:120]


def safe_package_name(value: str) -> str:
    return safe_case_name(value)


def read_json(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _standard_solver_package_path(case_root: Path) -> Path:
    return (
        case_root
        / STAGE_COLLECTION_FOLDERS["solver"]
        / STANDARD_SOLVER_PACKAGE
        / "Configurations"
        / "cfd2d_solver_config.json"
    )


def seed_standard_solver_package(
    root: Path,
    case_root: Path,
    manifest: dict[str, object],
    *,
    variant: str,
    alpha: float,
) -> Path:
    """Install the active full solver configuration as the work-case default."""
    source = root / STANDARD_SOLVER_CONFIG
    if not source.is_file():
        raise FileNotFoundError(f"Standard solver configuration is missing: {source}")
    destination = _standard_solver_package_path(case_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    files, size = tree_stats(destination.parents[1])
    stages = manifest.setdefault("stages", {})
    stages["solver"] = {
        "folder": STAGE_COLLECTION_FOLDERS["solver"],
        "active_package": STANDARD_SOLVER_PACKAGE,
        "packages": {
            STANDARD_SOLVER_PACKAGE: {
                "folder": (
                    Path(STAGE_COLLECTION_FOLDERS["solver"]) / STANDARD_SOLVER_PACKAGE
                ).as_posix(),
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "file_count": files,
                "size_bytes": size,
                "variant": variant,
                "alpha_deg": float(alpha),
                "standard_default": True,
            }
        },
    }
    return destination


def tree_stats(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def copy_or_link_file(source: str, target: str) -> str:
    """Link only large generated artifacts; mutable inputs remain snapshots."""
    source_path = Path(source)
    generated_suffixes = {"", ".msh", ".gz", ".vtk", ".vtu", ".vtp", ".pvtu", ".pvd", ".png", ".gif", ".mp4"}
    eligible = (
        source_path.stat().st_size >= 1024 * 1024
        and source_path.suffix.lower() in generated_suffixes
        and "0" not in source_path.parts
    )
    if eligible:
        try:
            os.link(source, target)
            return target
        except OSError:
            pass
    return shutil.copy2(source, target)


def copy_item(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            symlinks=True,
            dirs_exist_ok=target.is_dir(),
            copy_function=copy_or_link_file,
        )
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_or_link_file(str(source), str(target))
    else:
        raise FileNotFoundError(source)


def active_profile(root: Path) -> Path | None:
    config = read_json(project_path(root, "configurations", "default_case_config.json"), {}) or {}
    relative = ((config.get("profile_inputs") or {}).get("main_profile"))
    if not relative:
        return None
    candidate = Path(str(relative))
    if not candidate.is_absolute():
        candidate = root / canonicalize_project_relative(candidate)
    return candidate if candidate.is_file() else None


def variant_profile(root: Path, variant: str) -> Path | None:
    """Resolve the profile recorded by a variant before consulting UI state."""
    manifests = [
        root / "CFD_2D/CFD_2D_inputs/case_package" / variant / "manifest.json",
        root / "CFD_2D/CFD_2D_inputs/geometry" / variant / "manifest.json",
    ]
    source_keys = ("source", "source_profile", "profile", "profile_path")
    for manifest_path in manifests:
        manifest = read_json(manifest_path, {}) or {}
        if not isinstance(manifest, dict):
            continue
        source = next((manifest.get(key) for key in source_keys if manifest.get(key)), None)
        if not source:
            continue
        source_path = Path(str(source))
        project_candidate = project_path(root, "profiles", source_path.name)
        if project_candidate.is_file():
            return project_candidate
        if not source_path.is_absolute():
            source_path = root / canonicalize_project_relative(source_path)
        if source_path.is_file():
            return source_path
    return active_profile(root)


def portable_profile_name(root: Path, variant: str) -> str | None:
    profile = variant_profile(root, variant)
    if profile is None:
        return None
    try:
        return profile.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return f"Airfoil Profiles/{profile.name}"


def mesh_configuration_used(root: Path, variant: str) -> Path:
    mesh_root = root / "CFD_2D/meshes" / variant
    snapshot = mesh_root / "mesh_config_used.json"
    if snapshot.is_file():
        return snapshot
    report = read_json(mesh_root / "mesh_quality_report.json", {}) or {}
    reported = report.get("mesh_config_source") if isinstance(report, dict) else None
    if reported:
        candidate = Path(str(reported))
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            return candidate
    return root / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"


def source_items(root: Path, stage: str, variant: str, alpha: float) -> list[tuple[Path, Path]]:
    alpha_dir = safe_alpha_dir(alpha)
    if stage == "geometry":
        items = [
            (root / "CFD_2D/CFD_2D_inputs/geometry" / variant, Path("CFD Geometry")),
            (project_path(root, "catia_inputs"), Path("CATIA Inputs")),
            (project_path(root, "configurations", "default_case_config.json"), Path("Configurations/default_case_config.json")),
            (project_path(root, "configurations", "ramair_catia_system_config.json"), Path("Configurations/ramair_catia_system_config.json")),
        ]
        profile = variant_profile(root, variant)
        if profile is not None:
            items.append((profile, Path("Airfoil Profile") / profile.name))
        return items
    if stage == "case":
        return [
            (root / "CFD_2D/CFD_2D_inputs/case_package" / variant, Path("Case Package")),
            (
                root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json",
                Path("CFD Configurations/cfd2d_workflow_config.json"),
            ),
        ]
    if stage == "mesh":
        return [
            (root / "CFD_2D/meshes" / variant, Path("Mesh Data")),
            (mesh_configuration_used(root, variant), Path("Configurations/cfd2d_mesh_config.json")),
            (root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json", Path("Configurations/cfd2d_workflow_config.json")),
        ]
    if stage == "solver":
        return [
            (
                root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json",
                Path("Configurations/cfd2d_solver_config.json"),
            ),
        ]
    if stage == "simulation":
        return [
            (root / "CFD_2D/openfoam_cases" / variant / alpha_dir, Path("Case Data")),
            (root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json", Path("Configurations/cfd2d_solver_config.json")),
            (root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json", Path("Configurations/cfd2d_workflow_config.json")),
        ]
    if stage == "postprocess":
        return [
            (root / "CFD_2D/results" / variant / alpha_dir, Path("Results Data")),
            (root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json", Path("Configurations/cfd2d_solver_config.json")),
            (root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json", Path("Configurations/cfd2d_workflow_config.json")),
        ]
    raise KeyError(stage)


def restore_items(root: Path, stage: str, case_stage: Path, variant: str, alpha: float) -> list[tuple[Path, Path]]:
    alpha_dir = safe_alpha_dir(alpha)
    if stage == "geometry":
        items = [
            (case_stage / "CFD Geometry", root / "CFD_2D/CFD_2D_inputs/geometry" / variant),
            (case_stage / "CATIA Inputs", project_path(root, "catia_inputs")),
            (case_stage / "Configurations/default_case_config.json", project_path(root, "configurations", "default_case_config.json")),
            (case_stage / "Configurations/ramair_catia_system_config.json", project_path(root, "configurations", "ramair_catia_system_config.json")),
        ]
        profile_root = case_stage / "Airfoil Profile"
        if profile_root.is_dir():
            items.extend((path, project_path(root, "profiles", path.name)) for path in profile_root.iterdir() if path.is_file())
        return items
    if stage == "case":
        return [
            (case_stage / "Case Package", root / "CFD_2D/CFD_2D_inputs/case_package" / variant),
            (
                case_stage / "CFD Configurations/cfd2d_workflow_config.json",
                root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json",
            ),
        ]
    if stage == "mesh":
        mesh_data = case_stage / "Mesh Data"
        items = [
            (mesh_data if mesh_data.is_dir() else case_stage, root / "CFD_2D/meshes" / variant),
        ]
        if mesh_data.is_dir():
            items.extend([
                (case_stage / "Configurations/cfd2d_mesh_config.json", root / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"),
                (case_stage / "Configurations/cfd2d_workflow_config.json", root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"),
            ])
        return items
    if stage == "solver":
        return [
            (
                case_stage / "Configurations/cfd2d_solver_config.json",
                root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json",
            ),
        ]
    if stage == "simulation":
        case_data = case_stage / "Case Data"
        items = [
            (case_data if case_data.is_dir() else case_stage, root / "CFD_2D/openfoam_cases" / variant / alpha_dir),
        ]
        if case_data.is_dir():
            items.extend([
                (case_stage / "Configurations/cfd2d_solver_config.json", root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json"),
                (case_stage / "Configurations/cfd2d_workflow_config.json", root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"),
            ])
        return items
    if stage == "postprocess":
        results_data = case_stage / "Results Data"
        items = [
            (results_data if results_data.is_dir() else case_stage, root / "CFD_2D/results" / variant / alpha_dir),
        ]
        if results_data.is_dir():
            items.extend([
                (case_stage / "Configurations/cfd2d_solver_config.json", root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json"),
                (case_stage / "Configurations/cfd2d_workflow_config.json", root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"),
            ])
        return items
    raise KeyError(stage)


def prepare_destination(root: Path, destination: Path, action: str, label: str) -> Path | None:
    if not destination.exists():
        return None
    if action == "keep":
        raise FileExistsError(f"Destination already exists: {destination}")
    if action == "delete":
        if destination.resolve() == root.resolve() or root.resolve() not in destination.resolve().parents:
            raise ValueError(f"Refusing to delete outside project root: {destination}")
        shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
        return None
    if action != "archive":
        raise ValueError("Existing action must be archive, delete or keep.")
    backup = project_path(
        root,
        "previous_versions",
        "Results Library Backups",
        f"{safe_case_name(label)}_{time.strftime('%Y%m%d_%H%M%S')}",
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(destination), str(backup))
    return backup


def case_metadata(root: Path, case_name: str, variant: str, alpha: float, description: str) -> dict[str, object]:
    workflow = read_json(root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json", {}) or {}
    conditions = workflow.get("case_conditions") or {}
    return {
        "schema_version": 2,
        "case_name": case_name,
        "description": description,
        "variant": variant,
        "alpha_deg": float(alpha),
        "reynolds": conditions.get("reynolds"),
        "mach": conditions.get("mach"),
        "rho_kg_m3": conditions.get("rho_kg_m3"),
        "mu_pa_s": conditions.get("mu_pa_s"),
        "main_profile": portable_profile_name(root, variant),
    }


def create_case(root: Path, case_name: str, variant: str, alpha: float, description: str) -> dict[str, object]:
    """Create a work-case container initialized with the current solver standard."""
    case_name = safe_case_name(case_name)
    case_root = project_path(root, "results_library", case_name)
    manifest_path = case_root / "case_manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"Working case already exists: {case_root}")
    case_root.mkdir(parents=True, exist_ok=False)
    manifest = case_metadata(root, case_name, variant, alpha, description)
    manifest["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["stages"] = {}
    solver_config = seed_standard_solver_package(
        root,
        case_root,
        manifest,
        variant=variant,
        alpha=alpha,
    )
    write_json_atomic(manifest_path, manifest)
    active_workspace_path = root / "CFD_2D/app_state/active_workspace.json"
    write_json_atomic(
        active_workspace_path,
        {
            "schema_version": 3,
            "case": case_name,
            "stage": "workspace_defaults",
            "package": STANDARD_SOLVER_PACKAGE,
            "packages": {"solver": STANDARD_SOLVER_PACKAGE},
            "variant": variant,
            "alpha_deg": float(alpha),
            "restored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": str(case_root.resolve()),
            "note": "New work case initialized from active application defaults.",
        },
    )
    return {
        "status": "CREATED",
        "case": case_name,
        "destination": str(case_root),
        "solver_package": str(solver_config),
        "active_workspace": str(active_workspace_path),
    }


def _promote_legacy_stage_entry(stage: str, entry: dict[str, object]) -> dict[str, object]:
    if isinstance(entry.get("packages"), dict):
        return entry
    folder = str(entry.get("folder") or STAGE_FOLDERS[stage])
    legacy = dict(entry)
    legacy["folder"] = folder
    return {
        "folder": STAGE_COLLECTION_FOLDERS[stage],
        "active_package": "legacy",
        "packages": {"legacy": legacy},
    }


def saved_stage_packages(case_root: Path, manifest: dict[str, object], stage: str) -> dict[str, dict[str, object]]:
    """Return package metadata for schema-2 and legacy schema-1 cases."""
    entry = ((manifest.get("stages") or {}).get(stage) if isinstance(manifest.get("stages"), dict) else None)
    if not isinstance(entry, dict):
        return {}
    packages = entry.get("packages")
    if isinstance(packages, dict):
        return {str(name): dict(info) for name, info in packages.items() if isinstance(info, dict)}
    folder = str(entry.get("folder") or STAGE_FOLDERS[stage])
    if (case_root / folder).is_dir():
        legacy = dict(entry)
        legacy["folder"] = folder
        return {"legacy": legacy}
    return {}


def resolve_stage_package(
    case_root: Path,
    manifest: dict[str, object],
    stage: str,
    package_name: str | None,
) -> tuple[str, Path]:
    packages = saved_stage_packages(case_root, manifest, stage)
    if not packages:
        raise FileNotFoundError(f"Saved stage does not exist: {case_root / STAGE_FOLDERS[stage]}")
    entry = (manifest.get("stages") or {}).get(stage) or {}
    selected = safe_package_name(package_name) if package_name else str(entry.get("active_package") or "")
    if not selected:
        selected = next(reversed(packages))
    if selected not in packages:
        raise KeyError(f"Unknown {stage} package '{selected}'. Available: {', '.join(packages)}")
    folder = str(packages[selected].get("folder") or STAGE_FOLDERS[stage])
    path = case_root / folder
    if not path.is_dir():
        raise FileNotFoundError(f"Saved package does not exist: {path}")
    return selected, path


def save_stage(
    root: Path,
    stage: str,
    case_name: str,
    variant: str,
    alpha: float,
    description: str,
    action: str,
    package_name: str | None = None,
) -> dict[str, object]:
    case_name = safe_case_name(case_name)
    case_root = project_path(root, "results_library", case_name)
    package = safe_package_name(package_name) if package_name else None
    relative_destination = (
        Path(STAGE_COLLECTION_FOLDERS[stage]) / package
        if package
        else Path(STAGE_FOLDERS[stage])
    )
    destination = case_root / relative_destination
    available = [(source, relative) for source, relative in source_items(root, stage, variant, alpha) if source.exists()]
    if not available:
        raise FileNotFoundError(f"No active {stage} output exists for {variant}, alpha={alpha:g}.")
    backup = prepare_destination(root, destination, action, f"{case_name}_{stage}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    incoming = case_root / f".{stage}.incoming.{uuid.uuid4().hex}"
    incoming.mkdir(parents=True, exist_ok=False)
    try:
        for source, relative in available:
            target = incoming if str(relative) == "." else incoming / relative
            copy_item(source, target)
        incoming.replace(destination)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    files, size = tree_stats(destination)
    manifest_path = case_root / "case_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    manifest.update(case_metadata(root, case_name, variant, alpha, description))
    stages = manifest.setdefault("stages", {})
    stage_info = {
        "folder": relative_destination.as_posix(),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": files,
        "size_bytes": size,
        "variant": variant,
        "alpha_deg": float(alpha),
    }
    if package:
        current = stages.get(stage)
        if isinstance(current, dict) and current and not isinstance(current.get("packages"), dict):
            current = _promote_legacy_stage_entry(stage, current)
        if not isinstance(current, dict):
            current = {"folder": STAGE_COLLECTION_FOLDERS[stage], "packages": {}}
        packages = current.setdefault("packages", {})
        packages[package] = stage_info
        current["active_package"] = package
        current["folder"] = STAGE_COLLECTION_FOLDERS[stage]
        stages[stage] = current
    else:
        stages[stage] = stage_info
    write_json_atomic(manifest_path, manifest)
    return {"status": "SAVED", "stage": stage, "package": package or "legacy", "case": case_name, "destination": str(destination), "backup": str(backup) if backup else None, "file_count": files, "size_bytes": size}


def restore_stage(
    root: Path,
    stage: str,
    case_name: str,
    variant: str | None,
    alpha: float | None,
    action: str,
    package_name: str | None = None,
    apply_solver_precedence: bool = True,
) -> dict[str, object]:
    case_name = safe_case_name(case_name)
    case_root = project_path(root, "results_library", case_name)
    manifest = read_json(case_root / "case_manifest.json", {}) or {}
    package, case_stage = resolve_stage_package(case_root, manifest, stage, package_name)
    package_info = saved_stage_packages(case_root, manifest, stage)[package]
    variant = str(package_info.get("variant") or variant or manifest.get("variant") or "")
    alpha_value = package_info.get("alpha_deg")
    if alpha_value is None:
        alpha_value = alpha if alpha is not None else manifest.get("alpha_deg")
    if not variant:
        raise ValueError("The saved package has no variant and none was provided.")
    if alpha_value is None:
        raise ValueError("The saved package has no angle of attack and none was provided.")
    alpha = float(alpha_value)
    restored: list[str] = []
    backups: list[str] = []
    for source, destination in restore_items(root, stage, case_stage, variant, alpha):
        if not source.exists():
            continue
        backup = prepare_destination(root, destination, action, f"workspace_{case_name}_{stage}_{destination.name}")
        if backup:
            backups.append(str(backup))
        copy_item(source, destination)
        restored.append(str(destination))
    if not restored:
        raise FileNotFoundError(f"Saved {stage} stage contains no restorable data.")
    if apply_solver_precedence and stage != "solver":
        solver_packages = saved_stage_packages(case_root, manifest, "solver")
        if solver_packages:
            solver_entry = (manifest.get("stages") or {}).get("solver") or {}
            solver_package = str(
                solver_entry.get("active_package") or next(reversed(solver_packages))
            )
            _, solver_root = resolve_stage_package(
                case_root, manifest, "solver", solver_package
            )
            solver_source = (
                solver_root / "Configurations/cfd2d_solver_config.json"
            )
            solver_destination = root / STANDARD_SOLVER_CONFIG
            if solver_source.is_file():
                solver_destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(solver_source, solver_destination)
                restored.append(str(solver_destination))
    active_workspace_path = root / "CFD_2D/app_state/active_workspace.json"
    active_workspace = {
        "schema_version": 2,
        "case": case_name,
        "stage": stage,
        "package": package,
        "variant": variant,
        "alpha_deg": alpha,
        "restored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(case_stage.resolve()),
        "restored": restored,
        "backups": backups,
    }
    write_json_atomic(active_workspace_path, active_workspace)
    return {
        "status": "RESTORED",
        "stage": stage,
        "package": package,
        "case": case_name,
        "variant": variant,
        "alpha_deg": alpha,
        "restored": restored,
        "backups": backups,
        "active_workspace": str(active_workspace_path),
    }


def restore_workspace(root: Path, case_name: str, action: str) -> dict[str, object]:
    """Restore the coherent geometry, CFD-case and mesh packages of one work case."""
    case_name = safe_case_name(case_name)
    case_root = project_path(root, "results_library", case_name)
    manifest = read_json(case_root / "case_manifest.json", {}) or {}
    restored_stages: dict[str, object] = {}
    packages_used: dict[str, str] = {}
    for stage in ("geometry", "case", "mesh"):
        packages = saved_stage_packages(case_root, manifest, stage)
        if not packages:
            raise FileNotFoundError(f"The work case has no restorable {stage} package: {case_root}")
        entry = (manifest.get("stages") or {}).get(stage) or {}
        package = str(entry.get("active_package") or next(reversed(packages)))
        result = restore_stage(
            root,
            stage,
            case_name,
            str(manifest.get("variant") or ""),
            float(manifest.get("alpha_deg", 0.0)),
            action,
            package,
            apply_solver_precedence=False,
        )
        restored_stages[stage] = result
        packages_used[stage] = package

    solver_packages = saved_stage_packages(case_root, manifest, "solver")
    if solver_packages:
        entry = (manifest.get("stages") or {}).get("solver") or {}
        package = str(entry.get("active_package") or next(reversed(solver_packages)))
        result = restore_stage(
            root,
            "solver",
            case_name,
            str(manifest.get("variant") or ""),
            float(manifest.get("alpha_deg", 0.0)),
            action,
            package,
            apply_solver_precedence=False,
        )
        restored_stages["solver"] = result
        packages_used["solver"] = package

    # A legacy package can contain a stale workflow variant from the workspace
    # where it was saved. The case manifest is the authoritative identity after
    # all stage files have been restored.
    workflow_path = root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"
    workflow = read_json(workflow_path, {}) or {}
    geometry = dict(workflow.get("geometry") or {})
    geometry["variant"] = str(manifest.get("variant") or "")
    workflow["geometry"] = geometry
    write_json_atomic(workflow_path, workflow)

    active_workspace_path = root / "CFD_2D/app_state/active_workspace.json"
    active_workspace = {
        "schema_version": 3,
        "case": case_name,
        "stage": "workspace",
        "package": "active_packages",
        "packages": packages_used,
        "variant": str(manifest.get("variant") or ""),
        "alpha_deg": float(manifest.get("alpha_deg", 0.0)),
        "restored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(case_root.resolve()),
        "validation": manifest.get("validation"),
    }
    write_json_atomic(active_workspace_path, active_workspace)
    return {
        "status": "WORKSPACE_RESTORED",
        "case": case_name,
        "variant": active_workspace["variant"],
        "alpha_deg": active_workspace["alpha_deg"],
        "packages": packages_used,
        "stages": restored_stages,
        "active_workspace": str(active_workspace_path),
    }


def activate_workspace_configuration(
    root: Path,
    case_name: str,
    package_name: str,
    action: str,
) -> dict[str, object]:
    """Select and restore one coherent geometry/case/mesh package triplet."""
    case_name = safe_case_name(case_name)
    package = safe_package_name(package_name)
    case_root = project_path(root, "results_library", case_name)
    manifest_path = case_root / "case_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    stages = manifest.get("stages") or {}
    variant = ""
    for stage in ("geometry", "case", "mesh"):
        packages = saved_stage_packages(case_root, manifest, stage)
        if package not in packages:
            raise KeyError(
                f"Package '{package}' is unavailable for {stage}. "
                f"Available: {', '.join(packages)}"
            )
        entry = stages.get(stage) or {}
        entry["active_package"] = package
        stages[stage] = entry
        package_variant = str(packages[package].get("variant") or "")
        if variant and package_variant and package_variant != variant:
            raise ValueError(
                f"Incoherent package variants: {variant} versus "
                f"{package_variant} in {stage}"
            )
        variant = package_variant or variant
    manifest["stages"] = stages
    if variant:
        manifest["variant"] = variant
    convergence = manifest.get("mesh_convergence_study")
    if isinstance(convergence, dict):
        convergence["active_configuration"] = package
    manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_atomic(manifest_path, manifest)
    result = restore_workspace(root, case_name, action)
    result["configuration"] = package
    return result


def standardize_solver_defaults(root: Path) -> dict[str, object]:
    """Update editable work-case defaults without rewriting run provenance."""
    source = root / STANDARD_SOLVER_CONFIG
    if not source.is_file():
        raise FileNotFoundError(f"Standard solver configuration is missing: {source}")
    updated_cases: list[dict[str, object]] = []
    library = project_path(root, "results_library")
    for manifest_path in sorted(library.glob("*/case_manifest.json")):
        case_root = manifest_path.parent
        manifest = read_json(manifest_path, {}) or {}
        variant = str(manifest.get("variant") or "")
        alpha = float(manifest.get("alpha_deg", 0.0) or 0.0)
        solver_path = seed_standard_solver_package(
            root,
            case_root,
            manifest,
            variant=variant,
            alpha=alpha,
        )
        editable_case_configs: list[str] = []
        for destination in sorted(
            case_root.glob(
                f"{STAGE_COLLECTION_FOLDERS['case']}/*/CFD Configurations/"
                "cfd2d_solver_config.json"
            )
        ):
            shutil.copy2(source, destination)
            editable_case_configs.append(str(destination))
        editable_workflow_configs: list[str] = []
        for pattern in (
            f"{STAGE_COLLECTION_FOLDERS['case']}/*/CFD Configurations/"
            "cfd2d_workflow_config.json",
            f"{STAGE_COLLECTION_FOLDERS['mesh']}/*/Configurations/"
            "cfd2d_workflow_config.json",
        ):
            for destination in sorted(case_root.glob(pattern)):
                workflow = read_json(destination, {}) or {}
                if not isinstance(workflow, dict):
                    continue
                execution = workflow.setdefault("execution", {})
                if not isinstance(execution, dict):
                    execution = {}
                    workflow["execution"] = execution
                execution["steady_force_window_samples"] = 500
                write_json_atomic(destination, workflow)
                editable_workflow_configs.append(str(destination))
        manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json_atomic(manifest_path, manifest)
        updated_cases.append(
            {
                "case": case_root.name,
                "solver_package": str(solver_path),
                "editable_case_configs": editable_case_configs,
                "editable_workflow_configs": editable_workflow_configs,
            }
        )
    return {
        "status": "STANDARDIZED",
        "source": str(source),
        "package": STANDARD_SOLVER_PACKAGE,
        "cases": updated_cases,
        "historical_simulations_modified": False,
    }


def list_cases(root: Path) -> list[dict[str, object]]:
    library = project_path(root, "results_library")
    cases: list[dict[str, object]] = []
    for manifest_path in sorted(library.glob("*/case_manifest.json")):
        try:
            manifest = read_json(manifest_path, {}) or {}
            manifest["folder"] = manifest_path.parent.name
            cases.append(manifest)
        except Exception:
            continue
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    subparsers.add_parser("standardize-solvers")
    create = subparsers.add_parser("create")
    create.add_argument("--case-name", required=True)
    create.add_argument("--variant", required=True)
    create.add_argument("--alpha", type=float, required=True)
    create.add_argument("--description", default="")
    for name in ("save", "restore"):
        command = subparsers.add_parser(name)
        command.add_argument("--stage", choices=sorted(STAGE_FOLDERS), required=True)
        command.add_argument("--case-name", required=True)
        command.add_argument("--variant")
        command.add_argument("--alpha", type=float)
        command.add_argument("--existing-action", choices=["archive", "delete", "keep"], default="archive")
        command.add_argument("--package-name")
        if name == "save":
            command.add_argument("--description", default="")
    restore_all = subparsers.add_parser("restore-workspace")
    restore_all.add_argument("--case-name", required=True)
    restore_all.add_argument("--existing-action", choices=["archive", "delete", "keep"], default="archive")
    activate = subparsers.add_parser("activate-configuration")
    activate.add_argument("--case-name", required=True)
    activate.add_argument("--package-name", required=True)
    activate.add_argument(
        "--existing-action",
        choices=["archive", "delete", "keep"],
        default="archive",
    )
    args = parser.parse_args()
    root = find_project_root(args.project_root)
    if args.command == "list":
        result: object = {"status": "OK", "cases": list_cases(root)}
    elif args.command == "standardize-solvers":
        result = standardize_solver_defaults(root)
    elif args.command == "create":
        result = create_case(root, args.case_name, args.variant, args.alpha, args.description)
    elif args.command == "save":
        if not args.variant or args.alpha is None:
            parser.error("save requires --variant and --alpha")
        result = save_stage(root, args.stage, args.case_name, args.variant, args.alpha, args.description, args.existing_action, args.package_name)
    elif args.command == "restore-workspace":
        result = restore_workspace(root, args.case_name, args.existing_action)
    elif args.command == "activate-configuration":
        result = activate_workspace_configuration(
            root,
            args.case_name,
            args.package_name,
            args.existing_action,
        )
    else:
        result = restore_stage(root, args.stage, args.case_name, args.variant, args.alpha, args.existing_action, args.package_name)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
