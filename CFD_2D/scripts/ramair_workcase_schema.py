#!/usr/bin/env python3
"""Versioned identities, revisions and dependencies for RamAir work cases.

The schema adapter is intentionally metadata-only.  It never relocates package
artifacts and it can normalize schema-1/2 manifests in memory for read-only
callers before an explicit migration is approved.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable


CASE_MANIFEST_SCHEMA_VERSION = 3
ACTIVE_WORKSPACE_SCHEMA_VERSION = 4
APPROVAL_STATES = frozenset({"pending", "approved", "rejected"})
PROTECTED_WORK_CASES = frozenset(
    {
        "LS1_0417_validation_M0p15_Re1p9e6",
        "Open_RamAir_comparison_M0p15_Re1p9e6",
        "RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6",
    }
)

STAGE_DEPENDENCY_ROLES: dict[str, tuple[str, ...]] = {
    "geometry": (),
    "case": ("geometry",),
    "mesh": ("geometry", "case"),
    "solver": ("case",),
    "simulation": ("geometry", "case", "mesh", "solver"),
    "postprocess": ("simulation",),
}


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_uuid(*parts: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ramair-workcase:" + ":".join(map(str, parts))))


def new_work_case_id() -> str:
    return str(uuid.uuid4())


def _work_case_id(manifest: dict[str, Any], case_root: Path) -> str:
    value = str(manifest.get("work_case_id") or "").strip()
    if value:
        return value
    return _stable_uuid(
        str(manifest.get("case_name") or case_root.name),
        str(manifest.get("created_at") or "legacy"),
    )


def _stage_packages(
    case_root: Path,
    stages: dict[str, Any],
    stage: str,
    stage_folders: dict[str, str],
    collection_folders: dict[str, str],
) -> dict[str, Any]:
    entry = stages.get(stage)
    if not isinstance(entry, dict):
        return {}
    if isinstance(entry.get("packages"), dict):
        return entry
    folder = str(entry.get("folder") or stage_folders[stage])
    legacy_info = dict(entry)
    legacy_info["folder"] = folder
    stages[stage] = {
        "folder": collection_folders[stage],
        "active_package": "legacy",
        "packages": {"legacy": legacy_info},
    }
    return stages[stage]


def artifact_manifest(path: Path) -> dict[str, Any]:
    """Build a cheap, deterministic inventory without hashing heavy CFD fields."""
    rows: list[dict[str, Any]] = []
    content_suffixes = {".json", ".yaml", ".yml", ".csv", ".geo", ".cfg"}
    for item in sorted(path.rglob("*")) if path.is_dir() else ([path] if path.is_file() else []):
        if not item.is_file():
            continue
        stat = item.stat()
        relative = item.relative_to(path).as_posix() if path.is_dir() else item.name
        row: dict[str, Any] = {
            "path": relative,
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
        if item.suffix.lower() in content_suffixes and stat.st_size <= 4 * 1024 * 1024:
            row["content_sha256"] = hashlib.sha256(item.read_bytes()).hexdigest()
        rows.append(row)
    return {
        "folder": path.name,
        "file_count": len(rows),
        "size_bytes": sum(int(row["size_bytes"]) for row in rows),
        "inventory_sha256": _canonical_sha256(rows),
        "hash_policy": "content_for_small_configs_metadata_for_generated_v1",
    }


def _legacy_artifacts(info: dict[str, Any]) -> dict[str, Any]:
    seed = {
        "folder": str(info.get("folder") or ""),
        "file_count": int(info.get("file_count") or 0),
        "size_bytes": int(info.get("size_bytes") or 0),
        "saved_at": str(info.get("saved_at") or ""),
    }
    return {
        "folder": seed["folder"],
        "file_count": seed["file_count"],
        "size_bytes": seed["size_bytes"],
        "inventory_sha256": _canonical_sha256(seed),
        "hash_policy": "legacy_manifest_metadata_v1",
    }


def _revision_id(
    entity_id: str,
    stage: str,
    info: dict[str, Any],
    artifacts: dict[str, Any],
) -> str:
    return _canonical_sha256(
        {
            "entity_id": entity_id,
            "stage": stage,
            "variant": info.get("variant"),
            "alpha_deg": info.get("alpha_deg"),
            "artifacts": artifacts,
        }
    )


def _pending_approval(revision_id: str) -> dict[str, Any]:
    return {
        "status": "pending",
        "revision_id": revision_id,
        "decided_at": None,
        "actor": None,
        "evidence": None,
    }


def _active_package_name(stage_entry: dict[str, Any]) -> str | None:
    packages = stage_entry.get("packages")
    if not isinstance(packages, dict) or not packages:
        return None
    selected = str(stage_entry.get("active_package") or "")
    if selected in packages:
        return selected
    return sorted(map(str, packages))[-1]


def _matching_dependency(
    stages: dict[str, Any],
    role: str,
    package_name: str,
) -> dict[str, Any] | None:
    entry = stages.get(role)
    if not isinstance(entry, dict):
        return None
    packages = entry.get("packages")
    if not isinstance(packages, dict) or not packages:
        return None
    candidate = package_name if package_name in packages else _active_package_name(entry)
    info = packages.get(candidate) if candidate else None
    return info if isinstance(info, dict) else None


def _dependency_snapshot(
    stages: dict[str, Any],
    stage: str,
    package_name: str,
) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    for role in STAGE_DEPENDENCY_ROLES.get(stage, ()):
        info = _matching_dependency(stages, role, package_name)
        if not info:
            continue
        entity_id = str(info.get("entity_id") or "")
        revision_id = str(info.get("revision_id") or "")
        if entity_id and revision_id:
            dependencies.append(
                {"role": role, "entity_id": entity_id, "revision_id": revision_id}
            )
    return dependencies


def rebuild_entity_index(manifest: dict[str, Any]) -> None:
    entities: dict[str, Any] = {}
    active_entities: dict[str, str] = {}
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        manifest["entities"] = entities
        manifest["active_entities"] = active_entities
        return
    for stage, entry in stages.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("packages"), dict):
            continue
        for package_name, info in entry["packages"].items():
            if not isinstance(info, dict):
                continue
            entity_id = str(info.get("entity_id") or "")
            if not entity_id:
                continue
            entities[entity_id] = {
                "kind": stage,
                "display_name": str(info.get("display_name") or package_name),
                "package_name": str(package_name),
                "revision_id": str(info.get("revision_id") or ""),
                "folder": str(info.get("folder") or ""),
            }
        active = _active_package_name(entry)
        active_info = entry["packages"].get(active) if active else None
        if isinstance(active_info, dict) and active_info.get("entity_id"):
            active_entities[str(stage)] = str(active_info["entity_id"])
    manifest["entities"] = entities
    manifest["active_entities"] = active_entities


def package_compatibility(
    manifest: dict[str, Any],
    stage: str,
    package_name: str,
) -> dict[str, Any]:
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return {"status": "incomplete", "warnings": ["missing_stages"]}
    entry = stages.get(stage)
    packages = entry.get("packages") if isinstance(entry, dict) else None
    info = packages.get(package_name) if isinstance(packages, dict) else None
    if not isinstance(info, dict):
        return {"status": "incomplete", "warnings": [f"missing_package:{stage}:{package_name}"]}
    dependencies = info.get("dependencies")
    dependencies = dependencies if isinstance(dependencies, list) else []
    by_role = {
        str(item.get("role")): item
        for item in dependencies
        if isinstance(item, dict) and item.get("role")
    }
    warnings: list[str] = []
    for role in STAGE_DEPENDENCY_ROLES.get(stage, ()):
        expected = by_role.get(role)
        active_entry = stages.get(role)
        active_name = _active_package_name(active_entry) if isinstance(active_entry, dict) else None
        active_packages = active_entry.get("packages") if isinstance(active_entry, dict) else None
        active_info = active_packages.get(active_name) if isinstance(active_packages, dict) and active_name else None
        if not expected:
            warnings.append(f"missing_dependency:{role}")
            continue
        if not isinstance(active_info, dict):
            warnings.append(f"missing_active_dependency:{role}")
            continue
        if str(expected.get("entity_id")) != str(active_info.get("entity_id")):
            warnings.append(f"dependency_entity_changed:{role}")
        elif str(expected.get("revision_id")) != str(active_info.get("revision_id")):
            warnings.append(f"dependency_revision_changed:{role}")
    status = "compatible"
    if any(value.startswith("dependency_") for value in warnings):
        status = "stale"
    elif warnings:
        status = "incomplete"
    return {"status": status, "warnings": warnings}


def refresh_compatibility(manifest: dict[str, Any]) -> None:
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return
    for stage, entry in stages.items():
        packages = entry.get("packages") if isinstance(entry, dict) else None
        if not isinstance(packages, dict):
            continue
        for package_name, info in packages.items():
            if isinstance(info, dict):
                info["compatibility"] = package_compatibility(
                    manifest, str(stage), str(package_name)
                )


def normalize_case_manifest(
    case_root: Path,
    manifest: dict[str, Any],
    *,
    stage_folders: dict[str, str],
    collection_folders: dict[str, str],
) -> dict[str, Any]:
    """Return a schema-3 view without writing files or copying artifacts."""
    normalized = json.loads(json.dumps(manifest))
    original_schema = int(normalized.get("schema_version") or 1)
    normalized["schema_version"] = CASE_MANIFEST_SCHEMA_VERSION
    normalized.setdefault("case_name", case_root.name)
    normalized["work_case_id"] = _work_case_id(normalized, case_root)
    stages = normalized.setdefault("stages", {})
    if not isinstance(stages, dict):
        stages = {}
        normalized["stages"] = stages

    for stage in stage_folders:
        entry = _stage_packages(
            case_root, stages, stage, stage_folders, collection_folders
        )
        packages = entry.get("packages") if isinstance(entry, dict) else None
        if not isinstance(packages, dict):
            continue
        for package_name, value in list(packages.items()):
            if not isinstance(value, dict):
                packages.pop(package_name)
                continue
            info = value
            info.setdefault("display_name", str(package_name))
            info.setdefault("kind", stage)
            info.setdefault(
                "entity_id",
                _stable_uuid(normalized["work_case_id"], stage, str(package_name)),
            )
            artifacts = info.get("artifacts")
            if not isinstance(artifacts, dict):
                artifacts = _legacy_artifacts(info)
                info["artifacts"] = artifacts
            info.setdefault(
                "revision_id",
                _revision_id(str(info["entity_id"]), stage, info, artifacts),
            )
            info.setdefault(
                "provenance",
                {
                    "origin": "legacy_manifest_adapter" if original_schema < 3 else "application",
                    "recorded_at": str(info.get("saved_at") or normalized.get("created_at") or ""),
                },
            )
            info.setdefault("revision_history", [])
            approval = info.get("approval")
            if not isinstance(approval, dict) or str(approval.get("revision_id") or "") != str(info["revision_id"]):
                info["approval"] = _pending_approval(str(info["revision_id"]))

    # Dependencies are resolved only after all legacy entries have identities.
    for stage, entry in stages.items():
        packages = entry.get("packages") if isinstance(entry, dict) else None
        if not isinstance(packages, dict):
            continue
        for package_name, info in packages.items():
            if isinstance(info, dict) and not isinstance(info.get("dependencies"), list):
                info["dependencies"] = _dependency_snapshot(
                    stages, str(stage), str(package_name)
                )

    rebuild_entity_index(normalized)
    refresh_compatibility(normalized)
    return normalized


def make_package_revision(
    manifest: dict[str, Any],
    case_root: Path,
    stage: str,
    package_name: str,
    *,
    folder: str,
    variant: str,
    alpha: float,
    provenance: dict[str, Any] | None = None,
    previous_info: dict[str, Any] | None = None,
    archived_path: str | None = None,
) -> dict[str, Any]:
    """Create a new immutable revision record for one logical package entity."""
    entity_id = str((previous_info or {}).get("entity_id") or uuid.uuid4())
    package_root = case_root / folder
    artifacts = artifact_manifest(package_root)
    info: dict[str, Any] = {
        "folder": folder,
        "saved_at": _stamp(),
        "file_count": artifacts["file_count"],
        "size_bytes": artifacts["size_bytes"],
        "variant": variant,
        "alpha_deg": float(alpha),
        "display_name": package_name,
        "kind": stage,
        "entity_id": entity_id,
        "artifacts": artifacts,
        "provenance": dict(provenance or {"origin": "application", "recorded_at": _stamp()}),
        "dependencies": _dependency_snapshot(
            dict(manifest.get("stages") or {}), stage, package_name
        ),
    }
    info["revision_id"] = _revision_id(entity_id, stage, info, artifacts)
    history = list((previous_info or {}).get("revision_history") or [])
    revision_changed = bool(
        previous_info and previous_info.get("revision_id") != info["revision_id"]
    )
    if revision_changed:
        history.append(
            {
                "revision_id": previous_info.get("revision_id"),
                "saved_at": previous_info.get("saved_at"),
                "artifacts": previous_info.get("artifacts"),
                "dependencies": previous_info.get("dependencies", []),
                "provenance": previous_info.get("provenance"),
                "approval": previous_info.get("approval"),
                "archived_path": archived_path,
            }
        )
    info["revision_history"] = history
    if previous_info and not revision_changed and isinstance(previous_info.get("approval"), dict):
        info["approval"] = dict(previous_info["approval"])
    else:
        info["approval"] = _pending_approval(str(info["revision_id"]))
    return info


def refresh_package_revision(
    case_root: Path,
    manifest: dict[str, Any],
    stage: str,
    package_name: str,
    *,
    provenance: dict[str, Any],
    archived_path: str | None = None,
) -> dict[str, Any]:
    stages = manifest.get("stages")
    entry = stages.get(stage) if isinstance(stages, dict) else None
    packages = entry.get("packages") if isinstance(entry, dict) else None
    previous = packages.get(package_name) if isinstance(packages, dict) else None
    if not isinstance(previous, dict):
        raise KeyError(f"Unknown {stage} package: {package_name}")
    refreshed = make_package_revision(
        manifest,
        case_root,
        stage,
        package_name,
        folder=str(previous.get("folder") or ""),
        variant=str(previous.get("variant") or manifest.get("variant") or ""),
        alpha=float(previous.get("alpha_deg", manifest.get("alpha_deg", 0.0)) or 0.0),
        provenance=provenance,
        previous_info=previous,
        archived_path=archived_path,
    )
    for key, value in previous.items():
        if key not in refreshed and key not in {"approval", "revision_history", "compatibility"}:
            refreshed[key] = value
    packages[package_name] = refreshed
    rebuild_entity_index(manifest)
    refresh_compatibility(manifest)
    manifest["updated_at"] = _stamp()
    return refreshed


def set_revision_approval(
    manifest: dict[str, Any],
    stage: str,
    package_name: str,
    status: str,
    *,
    actor: str,
    evidence: Any = None,
) -> dict[str, Any]:
    if status not in APPROVAL_STATES:
        raise ValueError(f"Approval status must be one of: {', '.join(sorted(APPROVAL_STATES))}")
    stages = manifest.get("stages")
    entry = stages.get(stage) if isinstance(stages, dict) else None
    packages = entry.get("packages") if isinstance(entry, dict) else None
    info = packages.get(package_name) if isinstance(packages, dict) else None
    if not isinstance(info, dict):
        raise KeyError(f"Unknown {stage} package: {package_name}")
    approval = {
        "status": status,
        "revision_id": str(info.get("revision_id") or ""),
        "decided_at": _stamp() if status != "pending" else None,
        "actor": actor if status != "pending" else None,
        "evidence": evidence if status != "pending" else None,
    }
    info["approval"] = approval
    manifest["updated_at"] = _stamp()
    return approval


def classify_work_case(manifest: dict[str, Any]) -> str:
    if isinstance(manifest.get("mesh_convergence_study"), dict):
        return "mesh_convergence"
    if isinstance(manifest.get("validation"), dict):
        return "validation"
    if isinstance(manifest.get("comparison"), dict):
        return "comparison"
    if any(
        stage in (manifest.get("stages") or {})
        for stage in ("simulation", "postprocess")
    ):
        return "executed_case"
    return "design_case"


def migrate_case_manifest(
    project_root: Path,
    manifest_path: Path,
    *,
    stage_folders: dict[str, str],
    collection_folders: dict[str, str],
    writer: Callable[[Path, object], None],
    dry_run: bool = True,
) -> dict[str, Any]:
    current = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(current, dict):
        raise TypeError(f"Work-case manifest must be a JSON object: {manifest_path}")
    normalized = normalize_case_manifest(
        manifest_path.parent,
        current,
        stage_folders=stage_folders,
        collection_folders=collection_folders,
    )
    changed = normalized != current
    backup: Path | None = None
    if changed and not dry_run:
        backup = (
            project_root
            / "Previous Versions/Results Library Manifest Backups"
            / manifest_path.parent.name
            / f"{time.strftime('%Y%m%d_%H%M%S')}_case_manifest_schema{int(current.get('schema_version') or 1)}.json"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_path, backup)
        normalized["migration"] = {
            "source_schema_version": int(current.get("schema_version") or 1),
            "migrated_at": _stamp(),
            "metadata_only": True,
            "backup": str(backup),
        }
        writer(manifest_path, normalized)
    return {
        "case": manifest_path.parent.name,
        "work_case_id": normalized["work_case_id"],
        "source_schema_version": int(current.get("schema_version") or 1),
        "target_schema_version": CASE_MANIFEST_SCHEMA_VERSION,
        "changed": changed,
        "written": bool(changed and not dry_run),
        "backup": str(backup) if backup else None,
        "classification": classify_work_case(normalized),
        "protected": manifest_path.parent.name in PROTECTED_WORK_CASES,
        "package_count": len(normalized.get("entities") or {}),
    }


def migrate_case_library(
    project_root: Path,
    results_root: Path,
    *,
    stage_folders: dict[str, str],
    collection_folders: dict[str, str],
    writer: Callable[[Path, object], None],
    dry_run: bool = True,
) -> dict[str, Any]:
    cases = [
        migrate_case_manifest(
            project_root,
            path,
            stage_folders=stage_folders,
            collection_folders=collection_folders,
            writer=writer,
            dry_run=dry_run,
        )
        for path in sorted(results_root.glob("*/case_manifest.json"))
    ]
    index = {
        "schema_version": 1,
        "generated_at": _stamp(),
        "case_manifest_schema_version": CASE_MANIFEST_SCHEMA_VERSION,
        "metadata_only": True,
        "heavy_artifacts_copied": False,
        "cases": cases,
    }
    index_path = results_root / "work_case_index.json"
    if not dry_run:
        writer(index_path, index)
    return {
        "status": "DRY_RUN" if dry_run else "MIGRATED",
        "results_root": str(results_root),
        "index": str(index_path),
        "cases": cases,
        "heavy_artifacts_copied": False,
    }
