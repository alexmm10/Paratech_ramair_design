#!/usr/bin/env python3
"""Save and restore reusable RamAir workflow stages under ``Results/``."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from project_layout import canonicalize_project_relative, find_project_root, project_path
from ramair_workcase_schema import (
    ACTIVE_WORKSPACE_SCHEMA_VERSION,
    CASE_MANIFEST_SCHEMA_VERSION,
    make_package_revision,
    migrate_case_manifest as migrate_schema_manifest,
    migrate_case_library as migrate_schema_library,
    new_work_case_id,
    normalize_case_manifest,
    package_compatibility,
    rebuild_entity_index,
    refresh_compatibility,
    set_revision_approval,
)


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


def _canonical_signature(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "open", "opened"}:
        return True
    if text in {"0", "false", "no", "closed"}:
        return False
    return None


def geometry_identity(root: Path, variant: str) -> dict[str, object]:
    """Build a portable geometry identity independent of Work Case names."""
    geometry_root = root / "CFD_2D/CFD_2D_inputs/geometry" / variant
    package_root = root / "CFD_2D/CFD_2D_inputs/case_package" / variant
    documents = [
        read_json(geometry_root / "profile_manifest.json", {}) or {},
        read_json(geometry_root / "manifest.json", {}) or {},
        read_json(geometry_root / "mesh_input_contract.json", {}) or {},
        read_json(package_root / "manifest.json", {}) or {},
        read_json(package_root / "mesh_input_contract.json", {}) or {},
    ]
    merged: dict[str, object] = {}
    for document in documents:
        if isinstance(document, dict):
            merged.update(document)
    profile = variant_profile(root, variant)
    profile_sha256 = hashlib.sha256(profile.read_bytes()).hexdigest() if profile and profile.is_file() else None

    def first(*keys: str) -> object:
        for key in keys:
            if merged.get(key) is not None:
                return merged[key]
        return None

    identity = {
        "variant": str(variant),
        "profile_path": portable_profile_name(root, variant),
        "profile_sha256": profile_sha256,
        "chord_m": first("chord_m", "reference_chord_m", "chord"),
        "open_profile": _as_optional_bool(first("open_profile", "is_open", "open_inlet", "has_ram_air_opening_feature")),
        "inlet_fraction_chord": first(
            "inlet_fraction_chord", "inlet_length_chord", "cut_fraction_chord",
            "opening_fraction_chord", "nominal_inlet_percent_chord"
        ),
        "geometry_contract_version": first("schema_version", "contract_version", "geometry_contract_version"),
    }
    comparable = {key: value for key, value in identity.items() if key != "variant" and value is not None}
    identity["signature"] = _canonical_signature(comparable or {"variant": str(variant)})
    return identity


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
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
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
    normalized = normalize_case_manifest(
        case_root,
        manifest,
        stage_folders=STAGE_FOLDERS,
        collection_folders=STAGE_COLLECTION_FOLDERS,
    )
    manifest.clear()
    manifest.update(normalized)
    stages = manifest.setdefault("stages", {})
    current = stages.get("solver")
    previous_info = None
    if isinstance(current, dict) and isinstance(current.get("packages"), dict):
        candidate = current["packages"].get(STANDARD_SOLVER_PACKAGE)
        previous_info = candidate if isinstance(candidate, dict) else None
    archived_path: Path | None = None
    if destination.is_file() and previous_info:
        archived_path = (
            root
            / "Previous Versions/Results Library Revision Backups"
            / case_root.name
            / "solver"
            / str(previous_info.get("entity_id") or STANDARD_SOLVER_PACKAGE)
            / str(previous_info.get("revision_id") or "unversioned")
        )
        archived_config = archived_path / "Configurations/cfd2d_solver_config.json"
        archived_config.parent.mkdir(parents=True, exist_ok=True)
        if not archived_config.exists():
            shutil.copy2(destination, archived_config)
    shutil.copy2(source, destination)
    stages["solver"] = {
        "folder": STAGE_COLLECTION_FOLDERS["solver"],
        "active_package": STANDARD_SOLVER_PACKAGE,
        "packages": {},
    }
    relative = (Path(STAGE_COLLECTION_FOLDERS["solver"]) / STANDARD_SOLVER_PACKAGE).as_posix()
    info = make_package_revision(
        manifest,
        case_root,
        "solver",
        STANDARD_SOLVER_PACKAGE,
        folder=relative,
        variant=variant,
        alpha=alpha,
        provenance={
            "origin": "active_application_default",
            "source": STANDARD_SOLVER_CONFIG.as_posix(),
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        previous_info=previous_info,
        archived_path=str(archived_path) if archived_path else None,
    )
    info["standard_default"] = True
    stages["solver"]["packages"][STANDARD_SOLVER_PACKAGE] = info
    rebuild_entity_index(manifest)
    refresh_compatibility(manifest)
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
        "schema_version": CASE_MANIFEST_SCHEMA_VERSION,
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
    manifest["work_case_id"] = new_work_case_id()
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
            "schema_version": ACTIVE_WORKSPACE_SCHEMA_VERSION,
            "work_case_id": manifest["work_case_id"],
            "case": case_name,
            "stage": "workspace_defaults",
            "package": STANDARD_SOLVER_PACKAGE,
            "packages": {"solver": STANDARD_SOLVER_PACKAGE},
            "entities": {
                "solver": {
                    "entity_id": manifest["stages"]["solver"]["packages"][STANDARD_SOLVER_PACKAGE]["entity_id"],
                    "revision_id": manifest["stages"]["solver"]["packages"][STANDARD_SOLVER_PACKAGE]["revision_id"],
                }
            },
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


def schema3_manifest(case_root: Path, manifest: dict[str, object]) -> dict[str, object]:
    """Read schema-1/2 work cases through the non-mutating schema-3 adapter."""
    return normalize_case_manifest(
        case_root,
        manifest,
        stage_folders=STAGE_FOLDERS,
        collection_folders=STAGE_COLLECTION_FOLDERS,
    )


def mutable_schema3_manifest(root: Path, case_root: Path) -> dict[str, object]:
    """Load a writable manifest, backing up legacy metadata before migration."""
    manifest_path = case_root / "case_manifest.json"
    raw = read_json(manifest_path, {}) or {}
    if manifest_path.is_file() and int(raw.get("schema_version") or 1) < CASE_MANIFEST_SCHEMA_VERSION:
        migrate_schema_manifest(
            root,
            manifest_path,
            stage_folders=STAGE_FOLDERS,
            collection_folders=STAGE_COLLECTION_FOLDERS,
            writer=write_json_atomic,
            dry_run=False,
        )
        raw = read_json(manifest_path, {}) or {}
    return schema3_manifest(case_root, raw)


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
    *,
    allow_stale: bool = False,
) -> tuple[str, Path]:
    manifest = schema3_manifest(case_root, manifest)
    packages = saved_stage_packages(case_root, manifest, stage)
    if not packages:
        raise FileNotFoundError(f"Saved stage does not exist: {case_root / STAGE_FOLDERS[stage]}")
    entry = (manifest.get("stages") or {}).get(stage) or {}
    selected = safe_package_name(package_name) if package_name else str(entry.get("active_package") or "")
    if not package_name:
        active_status = (
            package_compatibility(manifest, stage, selected).get("status")
            if selected in packages
            else None
        )
        if active_status == "stale" or not selected:
            compatible = sorted(
                (
                    str(info.get("saved_at") or ""),
                    name,
                )
                for name, info in packages.items()
                if package_compatibility(manifest, stage, name).get("status")
                == "compatible"
            )
            if compatible:
                selected = compatible[-1][1]
            elif not selected:
                selected = sorted(packages)[-1]
    if selected not in packages:
        raise KeyError(f"Unknown {stage} package '{selected}'. Available: {', '.join(packages)}")
    compatibility = package_compatibility(manifest, stage, selected)
    if compatibility.get("status") == "stale" and not allow_stale:
        warnings = ", ".join(map(str, compatibility.get("warnings") or []))
        raise ValueError(
            f"Refusing to load stale {stage} package '{selected}': {warnings}"
        )
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
    package_key = package or "legacy"
    relative_destination = (
        Path(STAGE_COLLECTION_FOLDERS[stage]) / package
        if package
        else Path(STAGE_FOLDERS[stage])
    )
    destination = case_root / relative_destination
    available = [(source, relative) for source, relative in source_items(root, stage, variant, alpha) if source.exists()]
    if not available:
        raise FileNotFoundError(f"No active {stage} output exists for {variant}, alpha={alpha:g}.")
    manifest_path = case_root / "case_manifest.json"
    manifest = mutable_schema3_manifest(root, case_root)
    current_entry = (manifest.get("stages") or {}).get(stage) or {}
    current_packages = current_entry.get("packages") or {}
    previous_info = current_packages.get(package_key)
    previous_info = previous_info if isinstance(previous_info, dict) else None
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
    manifest.update(case_metadata(root, case_name, variant, alpha, description))
    manifest.setdefault("work_case_id", new_work_case_id())
    stages = manifest.setdefault("stages", {})
    current = stages.get(stage)
    if not isinstance(current, dict):
        current = {"folder": STAGE_COLLECTION_FOLDERS[stage], "packages": {}}
    packages = current.setdefault("packages", {})
    stage_info = make_package_revision(
        manifest,
        case_root,
        stage,
        package_key,
        folder=relative_destination.as_posix(),
        variant=variant,
        alpha=alpha,
        provenance={
            "origin": "application_stage_save",
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_stage": stage,
        },
        previous_info=previous_info,
        archived_path=str(backup) if backup else None,
    )
    geometry_meta = geometry_identity(root, variant)
    if stage in {"geometry", "case", "mesh"}:
        stage_info["geometry_identity"] = geometry_meta
    if stage == "mesh":
        mesh_cfg = read_json(destination / "Configurations/cfd2d_mesh_config.json", {}) or {}
        workflow_cfg = read_json(destination / "Configurations/cfd2d_workflow_config.json", {}) or {}
        conditions = workflow_cfg.get("case_conditions") if isinstance(workflow_cfg, dict) else {}
        stage_info["mesh_condition_basis"] = {
            "reynolds": (conditions or {}).get("reynolds"),
            "mach": (conditions or {}).get("mach"),
            "target_y_plus": mesh_cfg.get("target_y_plus") if isinstance(mesh_cfg, dict) else None,
            "strategy_version": mesh_cfg.get("mesh_strategy_version") if isinstance(mesh_cfg, dict) else None,
        }
    packages[package_key] = stage_info
    current["active_package"] = package_key
    current["folder"] = STAGE_COLLECTION_FOLDERS[stage]
    stages[stage] = current
    rebuild_entity_index(manifest)
    refresh_compatibility(manifest)
    manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_atomic(manifest_path, manifest)
    return {
        "status": "SAVED",
        "stage": stage,
        "package": package_key,
        "entity_id": stage_info["entity_id"],
        "revision_id": stage_info["revision_id"],
        "approval": stage_info["approval"],
        "case": case_name,
        "destination": str(destination),
        "backup": str(backup) if backup else None,
        "file_count": stage_info["file_count"],
        "size_bytes": stage_info["size_bytes"],
    }


def restore_stage(
    root: Path,
    stage: str,
    case_name: str,
    variant: str | None,
    alpha: float | None,
    action: str,
    package_name: str | None = None,
    apply_solver_precedence: bool = True,
    allow_stale: bool = False,
) -> dict[str, object]:
    case_name = safe_case_name(case_name)
    case_root = project_path(root, "results_library", case_name)
    manifest = schema3_manifest(
        case_root, read_json(case_root / "case_manifest.json", {}) or {}
    )
    package, case_stage = resolve_stage_package(
        case_root, manifest, stage, package_name, allow_stale=allow_stale
    )
    package_info = saved_stage_packages(case_root, manifest, stage)[package]
    compatibility = package_compatibility(manifest, stage, package)
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
    warnings = list(compatibility.get("warnings") or [])
    if apply_solver_precedence and stage != "solver":
        solver_packages = saved_stage_packages(case_root, manifest, "solver")
        if solver_packages:
            solver_entry = (manifest.get("stages") or {}).get("solver") or {}
            solver_package = str(
                solver_entry.get("active_package") or next(reversed(solver_packages))
            )
            solver_compatibility = package_compatibility(
                manifest, "solver", solver_package
            )
            if solver_compatibility.get("status") == "stale":
                warnings.extend(
                    f"solver_not_restored:{value}"
                    for value in solver_compatibility.get("warnings") or []
                )
            else:
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
        "schema_version": ACTIVE_WORKSPACE_SCHEMA_VERSION,
        "work_case_id": manifest["work_case_id"],
        "case": case_name,
        "stage": stage,
        "package": package,
        "variant": variant,
        "alpha_deg": alpha,
        "restored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(case_stage.resolve()),
        "entity_id": package_info.get("entity_id"),
        "revision_id": package_info.get("revision_id"),
        "approval": package_info.get("approval"),
        "compatibility": compatibility,
        "warnings": warnings,
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
        "work_case_id": manifest["work_case_id"],
        "entity_id": package_info.get("entity_id"),
        "revision_id": package_info.get("revision_id"),
        "approval": package_info.get("approval"),
        "compatibility": compatibility,
        "warnings": warnings,
        "restored": restored,
        "backups": backups,
        "active_workspace": str(active_workspace_path),
    }


def restore_workspace(root: Path, case_name: str, action: str) -> dict[str, object]:
    """Restore the complete saved snapshot, including review-required revisions.

    A stale package remains blocked when loaded on its own.  For a complete work-case
    restore, however, the active package set is the user's saved snapshot and must be
    recoverable as a unit.  Compatibility drift is preserved as an explicit warning.
    """
    case_name = safe_case_name(case_name)
    case_root = project_path(root, "results_library", case_name)
    manifest = schema3_manifest(
        case_root, read_json(case_root / "case_manifest.json", {}) or {}
    )
    restored_stages: dict[str, object] = {}
    packages_used: dict[str, str] = {}
    warnings: list[str] = []
    selected_packages: dict[str, str] = {}
    for stage in ("geometry", "case", "mesh"):
        packages = saved_stage_packages(case_root, manifest, stage)
        if not packages:
            warnings.append(f"work_case_has_no_{stage}_package")
            continue
        stage_entry = (manifest.get("stages") or {}).get(stage) or {}
        package = str(stage_entry.get("active_package") or "")
        if not package or package not in packages:
            package = sorted(packages)[-1]
        resolve_stage_package(
            case_root, manifest, stage, package, allow_stale=True
        )
        selected_packages[stage] = package
    for stage, package in selected_packages.items():
        result = restore_stage(
            root,
            stage,
            case_name,
            str(manifest.get("variant") or ""),
            float(manifest.get("alpha_deg", 0.0)),
            action,
            package,
            apply_solver_precedence=False,
            allow_stale=True,
        )
        restored_stages[stage] = result
        packages_used[stage] = package
        warnings.extend(map(str, result.get("warnings") or []))

    solver_packages = saved_stage_packages(case_root, manifest, "solver")
    if solver_packages:
        try:
            solver_entry = (manifest.get("stages") or {}).get("solver") or {}
            package = str(solver_entry.get("active_package") or "")
            if not package or package not in solver_packages:
                package = sorted(solver_packages)[-1]
            resolve_stage_package(
                case_root, manifest, "solver", package, allow_stale=True
            )
            result = restore_stage(
                root,
                "solver",
                case_name,
                str(manifest.get("variant") or ""),
                float(manifest.get("alpha_deg", 0.0)),
                action,
                package,
                apply_solver_precedence=False,
                allow_stale=True,
            )
            restored_stages["solver"] = result
            packages_used["solver"] = package
            warnings.extend(map(str, result.get("warnings") or []))
        except ValueError as exc:
            warnings.append(f"solver_not_restored:{exc}")

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
    entities = {
        stage: {
            "entity_id": result.get("entity_id"),
            "revision_id": result.get("revision_id"),
            "approval": result.get("approval"),
            "compatibility": result.get("compatibility"),
        }
        for stage, result in restored_stages.items()
        if isinstance(result, dict)
    }
    active_workspace = {
        "schema_version": ACTIVE_WORKSPACE_SCHEMA_VERSION,
        "work_case_id": manifest["work_case_id"],
        "case": case_name,
        "stage": "workspace",
        "package": "active_packages",
        "packages": packages_used,
        "entities": entities,
        "variant": str(manifest.get("variant") or ""),
        "alpha_deg": float(manifest.get("alpha_deg", 0.0)),
        "restored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(case_root.resolve()),
        "validation": manifest.get("validation"),
        "warnings": sorted(set(warnings)),
    }
    write_json_atomic(active_workspace_path, active_workspace)
    return {
        "status": "WORKSPACE_RESTORED",
        "case": case_name,
        "variant": active_workspace["variant"],
        "alpha_deg": active_workspace["alpha_deg"],
        "packages": packages_used,
        "entities": entities,
        "warnings": sorted(set(warnings)),
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
    manifest = mutable_schema3_manifest(root, case_root)
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
    rebuild_entity_index(manifest)
    refresh_compatibility(manifest)
    stale = {
        stage: package_compatibility(manifest, stage, package)
        for stage in ("geometry", "case", "mesh")
        if package_compatibility(manifest, stage, package).get("status") == "stale"
    }
    if stale:
        details = "; ".join(
            f"{stage}: {', '.join(map(str, value.get('warnings') or []))}"
            for stage, value in stale.items()
        )
        raise ValueError(f"Incompatible configuration '{package}': {details}")
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
        manifest = mutable_schema3_manifest(root, case_root)
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


def approve_stage_package(
    root: Path,
    case_name: str,
    stage: str,
    package_name: str,
    status: str,
    *,
    actor: str = "local-user",
    evidence: object = None,
) -> dict[str, object]:
    """Persist a decision against the package's current immutable revision."""
    case_name = safe_case_name(case_name)
    package = safe_package_name(package_name)
    case_root = project_path(root, "results_library", case_name)
    manifest_path = case_root / "case_manifest.json"
    manifest = mutable_schema3_manifest(root, case_root)
    approval = set_revision_approval(
        manifest,
        stage,
        package,
        status,
        actor=actor,
        evidence=evidence,
    )
    write_json_atomic(manifest_path, manifest)
    return {
        "status": "APPROVAL_RECORDED",
        "case": case_name,
        "stage": stage,
        "package": package,
        "approval": approval,
    }


def migrate_work_case_library(root: Path, *, dry_run: bool = True) -> dict[str, object]:
    """Migrate/index Results manifests without copying any package artifacts."""
    return migrate_schema_library(
        root,
        project_path(root, "results_library"),
        stage_folders=STAGE_FOLDERS,
        collection_folders=STAGE_COLLECTION_FOLDERS,
        writer=write_json_atomic,
        dry_run=dry_run,
    )


def list_cases(root: Path) -> list[dict[str, object]]:
    library = project_path(root, "results_library")
    cases: list[dict[str, object]] = []
    for manifest_path in sorted(library.glob("*/case_manifest.json")):
        try:
            manifest = schema3_manifest(
                manifest_path.parent, read_json(manifest_path, {}) or {}
            )
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
    migrate = subparsers.add_parser("migrate")
    migrate.add_argument(
        "--apply",
        action="store_true",
        help="Write schema-3 manifests and the Results index after creating backups.",
    )
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
    approve = subparsers.add_parser("approve")
    approve.add_argument("--case-name", required=True)
    approve.add_argument("--stage", choices=sorted(STAGE_FOLDERS), required=True)
    approve.add_argument("--package-name", required=True)
    approve.add_argument("--status", choices=["pending", "approved", "rejected"], required=True)
    approve.add_argument("--actor", default="local-user")
    approve.add_argument("--evidence")
    args = parser.parse_args()
    root = find_project_root(args.project_root)
    if args.command == "list":
        result: object = {"status": "OK", "cases": list_cases(root)}
    elif args.command == "migrate":
        result = migrate_work_case_library(root, dry_run=not args.apply)
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
    elif args.command == "approve":
        result = approve_stage_package(
            root,
            args.case_name,
            args.stage,
            args.package_name,
            args.status,
            actor=args.actor,
            evidence=args.evidence,
        )
    else:
        result = restore_stage(root, args.stage, args.case_name, args.variant, args.alpha, args.existing_action, args.package_name)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
