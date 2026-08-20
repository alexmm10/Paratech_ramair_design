#!/usr/bin/env python3
"""Preview/apply the authorized Validation Lab schema-8 to schema-9 reset."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import (
    STUDY_CONFIG_SCHEMA_VERSION,
    active_workspace_root,
    build_run_matrix,
    migrate_study_config,
    read_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_2d_urans_cases import (
    case_id_from_row,
    directory_size,
    process_identity_is_live,
    runtime_paths,
)


MIGRATION_SCHEMA_VERSION = 1
REFERENCE_DT = [2.5e-4, 1.25e-4, 6.25e-5]


def migration_paths(project_root: Path) -> dict[str, Path]:
    root = active_workspace_root(Path(project_root).resolve()) / "reports/schema9_migration"
    return {
        "root": root,
        "preview": root / "migration_preview.json",
        "report": root / "deletion_report.json",
    }


def _tree_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    root = path.resolve()
    if path.is_file():
        stat = path.stat()
        digest.update(f"file:{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        return digest.hexdigest()
    for child in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        if child.is_symlink():
            relative = child.relative_to(root).as_posix()
            digest.update(f"l:{relative}:{child.readlink()}\n".encode())
            continue
        relative = child.relative_to(root).as_posix()
        stat = child.stat()
        kind = "d" if child.is_dir() else "f"
        size = stat.st_size if child.is_file() else 0
        digest.update(f"{kind}:{relative}:{size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _assert_no_live_execution(project_root: Path) -> None:
    paths = runtime_paths(project_root)
    for name in ("active", "lease"):
        payload = read_json(paths[name], {}) or {}
        if not payload:
            continue
        pid = payload.get("PID")
        token = payload.get("process_start_token")
        active_claim = name == "lease" or str(payload.get("status") or "").upper() in {
            "PREPARING",
            "RUNNING",
        }
        if active_claim and (pid in (None, "") or token in (None, "")):
            raise RuntimeError(
                f"UNCERTAIN_EXECUTION_BLOCKS_MIGRATION: {name}: "
                f"{payload.get('case_id')}"
            )
        if process_identity_is_live(pid, token):
            raise RuntimeError(
                f"LIVE_EXECUTION_BLOCKS_MIGRATION: {name}: {payload.get('case_id')}"
            )


def _canonical_ids(active: Path) -> set[str]:
    matrix = read_json(active / "run_matrix.json", {}) or {}
    result: set[str] = set()
    for row in matrix.get("runs", []):
        try:
            result.add(case_id_from_row(row))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _candidate(
    path: Path,
    *,
    active: Path,
    kind: str,
) -> dict[str, Any]:
    resolved = path.resolve()
    if active.resolve() not in resolved.parents:
        raise RuntimeError(f"OUTSIDE_WORKSPACE_REJECTED: {resolved}")
    if path.is_symlink():
        raise RuntimeError(f"SYMLINK_REJECTED: {path}")
    active_resolved = active.resolve()
    for child in path.rglob("*"):
        if not child.is_symlink():
            continue
        target = child.resolve(strict=False)
        if target != active_resolved and active_resolved not in target.parents:
            raise RuntimeError(f"EXTERNAL_SYMLINK_REJECTED: {child} -> {target}")
    return {
        "kind": kind,
        "relative_path": resolved.relative_to(active.resolve()).as_posix(),
        "resolved_path": str(resolved),
        "bytes": directory_size(resolved) if resolved.is_dir() else resolved.stat().st_size,
        "fingerprint": _tree_fingerprint(resolved),
    }


def inventory(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    active = active_workspace_root(project_root)
    _assert_no_live_execution(project_root)
    source_schema = int((read_json(active / "study_config.json", {}) or {}).get("schema_version", 0))
    allowed_ids = _canonical_ids(active) if source_schema >= STUDY_CONFIG_SCHEMA_VERSION else set()
    candidates: list[dict[str, Any]] = []
    runs_root = active / "runs"
    for topology in ("closed", "open"):
        for level in ("coarse", "medium", "fine"):
            level_root = runs_root / topology / level
            if not level_root.is_dir():
                continue
            for case_root in sorted(level_root.iterdir()):
                if not case_root.is_dir():
                    continue
                if case_root.name in allowed_ids and (
                    case_root / "case_manifest.json"
                ).is_file():
                    continue
                candidates.append(
                    _candidate(
                        case_root,
                        active=active,
                        kind="legacy_urans_case_definition",
                    )
                )
    for quick_name in ("quick_check", "quick_checks"):
        quick = active / quick_name
        if quick.exists():
            candidates.append(
                _candidate(quick, active=active, kind="legacy_quick_check")
            )
    candidates.sort(key=lambda row: row["relative_path"])
    identity = hashlib.sha256(
        json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    runtime_references = {
        name: read_json(path, {}) or {}
        for name, path in runtime_paths(project_root).items()
        if name in {"active", "latest", "lease"}
    }
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "operation": "VALIDATION_LAB_SCHEMA8_TO_SCHEMA9",
        "project_root": str(project_root),
        "active_workspace": str(active),
        "source_schema": source_schema,
        "target_schema": STUDY_CONFIG_SCHEMA_VERSION,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "bytes_candidate": sum(int(row["bytes"]) for row in candidates),
        "inventory_hash": identity,
        "runtime_references": runtime_references,
        "preserved": [
            "mesh_registry and mesh packages",
            "geometry",
            "RANS checkpoints and RANS postprocess",
            "shared configuration outside URANS legacy fields",
            "curated Results",
            "PIMPLE studies with real data",
        ],
        "generated_at": utc_stamp(),
    }


def preview(project_root: Path) -> dict[str, Any]:
    payload = inventory(project_root)
    paths = migration_paths(project_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    write_json_atomic(paths["preview"], payload)
    return payload


def _assert_allowed_target(path: Path, active: Path, kind: str) -> None:
    resolved = path.resolve()
    active = active.resolve()
    if resolved in {active, active.parent, Path.home().resolve(), Path(resolved.anchor)}:
        raise RuntimeError(f"DANGEROUS_DELETE_TARGET: {resolved}")
    allowed = (
        kind == "legacy_urans_case_definition"
        and active / "runs" in resolved.parents
        and len(resolved.relative_to(active / "runs").parts) == 3
    ) or (
        kind == "legacy_quick_check"
        and resolved.parent == active
        and resolved.name in {"quick_check", "quick_checks"}
    )
    if not allowed:
        raise RuntimeError(f"DELETE_TARGET_NOT_ALLOWLISTED: {resolved}")
    if path.is_symlink():
        raise RuntimeError(f"SYMLINK_REJECTED: {path}")


def _clean_legacy_references(active: Path) -> list[str]:
    cleaned: list[str] = []
    for name in (
        "urans_queue_state.json",
        "urans_queue_state.csv",
        "runtime/active_execution.json",
        "runtime/solver_lease.json",
    ):
        path = active / name
        if path.exists():
            path.unlink()
            cleaned.append(str(path))
    registry_path = active / "execution_registry.json"
    registry = read_json(registry_path, {}) or {}
    if registry:
        removed_ids = {
            str(row.get("run_id"))
            for row in registry.get("runs", [])
            if str(row.get("mode") or "").upper() == "URANS"
        }
        retained = [
            row
            for row in registry.get("runs", [])
            if str(row.get("mode") or "").upper() != "URANS"
        ]
        registry["runs"] = retained
        if str(registry.get("active_run_id") or "") in removed_ids:
            registry["active_run_id"] = None
        if str(registry.get("pinned_run_id") or "") in removed_ids:
            registry["pinned_run_id"] = None
        registry["updated_at"] = utc_stamp()
        write_json_atomic(registry_path, registry)
        cleaned.append(str(registry_path))
    return cleaned


def apply(project_root: Path, *, confirm: str) -> dict[str, Any]:
    if confirm != "APPLY_SCHEMA9_RESET":
        raise ValueError("Apply requires --confirm APPLY_SCHEMA9_RESET")
    project_root = Path(project_root).resolve()
    paths = migration_paths(project_root)
    stored = read_json(paths["preview"], {}) or {}
    if not stored:
        raise RuntimeError("Run migration preview before apply")
    current = inventory(project_root)
    if current.get("inventory_hash") != stored.get("inventory_hash"):
        raise RuntimeError("MIGRATION_INVENTORY_CHANGED: rerun preview")
    active = active_workspace_root(project_root)
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in current["candidates"]:
        path = Path(row["resolved_path"])
        try:
            _assert_allowed_target(path, active, str(row["kind"]))
            if not path.exists():
                skipped.append({**row, "reason": "already_missing"})
                continue
            if _tree_fingerprint(path) != row["fingerprint"]:
                raise RuntimeError("candidate changed after inventory")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(row)
        except Exception as exc:
            failed.append({**row, "error": f"{type(exc).__name__}: {exc}"})
            break
    if failed:
        report = {
            **current,
            "status": "PARTIAL_FAILURE",
            "deleted": deleted,
            "skipped": skipped,
            "failed": failed,
            "applied_at": utc_stamp(),
        }
        write_json_atomic(paths["report"], report)
        raise RuntimeError(f"Schema-9 migration stopped safely: {failed[0]['error']}")
    cleaned = _clean_legacy_references(active)
    config_path = active / "study_config.json"
    config = migrate_study_config(read_json(config_path, {}) or {})
    write_json_atomic(config_path, config)
    registry = read_json(active / "mesh_registry.json", {}) or {}
    matrix = build_run_matrix(
        registry,
        dt_values_s=REFERENCE_DT,
        preset="paper-reference",
        previous=None,
    )
    matrix["preset_definition"] = {
        "source": "paper_reference",
        "formula": "2.5e-4 s, 1.25e-4 s, 6.25e-5 s",
    }
    write_json_atomic(active / "run_matrix.json", matrix)
    report = {
        **current,
        "status": "APPLIED",
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "cleaned_references": cleaned,
        "schema9_case_count": len(matrix.get("runs", [])),
        "schema9_deltaT_per_mesh": 3,
        "bytes_removed": sum(int(row["bytes"]) for row in deleted),
        "applied_at": utc_stamp(),
    }
    write_json_atomic(paths["report"], report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("preview")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = (
        preview(args.project_root)
        if args.action == "preview"
        else apply(args.project_root, confirm=args.confirm)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
