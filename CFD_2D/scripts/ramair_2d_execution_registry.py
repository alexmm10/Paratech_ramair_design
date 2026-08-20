#!/usr/bin/env python3
"""Atomic execution registry for real RANS, canonical URANS and PIMPLE runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    utc_stamp,
    write_json_atomic,
)
from ramair_execution_control import normalize_execution_state


MODES = {"RANS", "URANS", "PIMPLE_SENSITIVITY"}
_PRESERVE_IF_EMPTY_FIELDS = {
    "started_at",
    "log_path",
    "force_history_path",
    "residual_history_path",
    "case_path",
}


def registry_path(project_root: Path) -> Path:
    return active_workspace_root(Path(project_root).resolve()) / "execution_registry.json"


def load_registry(project_root: Path) -> dict[str, Any]:
    data = read_json(registry_path(project_root), {}) or {}
    data.setdefault("schema_version", 3)
    data["schema_version"] = 3
    for key, value in (
        ("active_run_id", None),
        ("active_mode", None),
        ("active_stage", None),
        ("queue_position", None),
        ("queue_total", None),
        ("follow_active_default", True),
        ("pinned_run_id", None),
        ("runs", []),
    ):
        data.setdefault(key, value)
    normalized_runs: list[dict[str, Any]] = []
    for raw_entry in data.get("runs", []):
        entry = dict(raw_entry)
        try:
            normalized_runs.append(_normalized_entry(entry))
        except ValueError:
            continue
    data["runs"] = normalized_runs
    known_ids = {str(row["run_id"]) for row in normalized_runs}
    if str(data.get("active_run_id") or "") not in known_ids:
        data.update(
            active_run_id=None,
            active_mode=None,
            active_stage=None,
            queue_position=None,
            queue_total=None,
        )
    if str(data.get("pinned_run_id") or "") not in known_ids:
        data["pinned_run_id"] = None
    return data


def _normalized_entry(entry: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry.get("mode") or "").upper()
    if mode not in MODES:
        raise ValueError(f"Unsupported execution mode: {mode}")
    run_id = str(entry.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("execution_registry entry requires run_id")
    legacy_status = str(entry.get("legacy_status") or entry.get("status") or "READY")
    try:
        status = normalize_execution_state(
            entry.get("status"), restartable=entry.get("restartable")
        ).value
    except ValueError:
        status = "PREPARED"
    return {
        "run_id": run_id,
        "case_id": entry.get("case_id"),
        "run_kind": "CANONICAL" if mode == "URANS" else entry.get("run_kind"),
        "mode": mode,
        "topology": entry.get("topology"),
        "mesh_level": entry.get("mesh_level"),
        "mesh_id": entry.get("mesh_id"),
        "stage": entry.get("stage"),
        "status": status,
        "legacy_status": legacy_status,
        "started_at": entry.get("started_at"),
        "updated_at": entry.get("updated_at") or utc_stamp(),
        "log_path": entry.get("log_path"),
        "force_history_path": entry.get("force_history_path"),
        "residual_history_path": entry.get("residual_history_path"),
        "case_path": entry.get("case_path"),
        "monitor_paths": dict(entry.get("monitor_paths") or {}),
        "queue_id": entry.get("queue_id"),
        "queue_position": entry.get("queue_position"),
        "queue_total": entry.get("queue_total"),
        "deltaT": entry.get("deltaT"),
        "iteration": entry.get("iteration"),
        "time_s": entry.get("time_s"),
        "time_star": entry.get("time_star"),
        "elapsed_s": entry.get("elapsed_s"),
        "remaining_s": entry.get("remaining_s"),
        "config_hash": entry.get("config_hash"),
        "nOuterCorrectors": entry.get("nOuterCorrectors"),
        "error": entry.get("error"),
        "remediation_actions": list(entry.get("remediation_actions") or []),
        "timing_evidence": dict(entry.get("timing_evidence") or {}),
        "steps_planned": entry.get("steps_planned"),
        "execution_intent": entry.get("execution_intent"),
        "effective_execution_intent": entry.get("effective_execution_intent"),
        "stage_start_mode": entry.get("stage_start_mode"),
        "case_key": entry.get("case_key"),
        "case_label": entry.get("case_label"),
        "terminal_reason": entry.get("terminal_reason"),
        "heartbeat_at": entry.get("heartbeat_at"),
        "identity_provenance": dict(entry.get("identity_provenance") or {}),
    }


def upsert_execution(project_root: Path, entry: dict[str, Any], *, activate: bool = False) -> dict[str, Any]:
    registry = load_registry(project_root)
    by_id = {str(row["run_id"]): dict(row) for row in registry["runs"]}
    run_id = str(entry.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("execution_registry entry requires run_id")
    previous = by_id.get(run_id, {})
    merged = {**previous, **entry}
    for field in _PRESERVE_IF_EMPTY_FIELDS:
        if entry.get(field) in (None, "") and previous.get(field) not in (None, ""):
            merged[field] = previous[field]
    merged["monitor_paths"] = {
        **dict(previous.get("monitor_paths") or {}),
        **dict(entry.get("monitor_paths") or {}),
    }
    merged["updated_at"] = entry.get("updated_at") or utc_stamp()
    normalized = _normalized_entry(merged)
    by_id[run_id] = normalized
    registry["runs"] = sorted(
        by_id.values(), key=lambda row: str(row.get("updated_at") or ""), reverse=True
    )
    if activate:
        registry.update(
            active_run_id=run_id,
            active_mode=normalized["mode"],
            active_stage=normalized.get("stage"),
            queue_position=normalized.get("queue_position"),
            queue_total=normalized.get("queue_total"),
        )
    registry["updated_at"] = utc_stamp()
    write_json_atomic(registry_path(project_root), registry)
    return registry


def set_active_execution(project_root: Path, run_id: str, *, pin: bool | None = None) -> dict[str, Any]:
    registry = load_registry(project_root)
    entry = next((row for row in registry["runs"] if str(row.get("run_id")) == run_id), None)
    if entry is None:
        raise KeyError(f"Unknown execution run_id: {run_id}")
    registry.update(
        active_run_id=run_id,
        active_mode=entry.get("mode"),
        active_stage=entry.get("stage"),
        queue_position=entry.get("queue_position"),
        queue_total=entry.get("queue_total"),
        updated_at=utc_stamp(),
    )
    if pin is True:
        registry["pinned_run_id"] = run_id
    elif pin is False:
        registry["pinned_run_id"] = None
    write_json_atomic(registry_path(project_root), registry)
    return registry


def execution_title(entry: dict[str, Any]) -> str:
    topology = str(entry.get("topology") or "Unknown").title()
    level = str(entry.get("mesh_level") or "Unknown").title()
    mode = str(entry.get("mode") or "")
    if mode == "RANS":
        queue = ""
        if entry.get("queue_position") and entry.get("queue_total"):
            queue = f" | Base-state queue {entry['queue_position']}/{entry['queue_total']}"
        return f"{topology} | {level} | RANS/SIMPLE | Iteration {int(entry.get('iteration') or 0):,}{queue}"
    if mode == "PIMPLE_SENSITIVITY":
        return (
            f"{topology} | {level} | PIMPLE sensitivity | "
            f"nOuter={entry.get('nOuterCorrectors', '?')} | dt={entry.get('deltaT', '?')}"
        )
    return (
        f"{topology} | {level} | URANS | dt={entry.get('deltaT', '?')} s | "
        f"Stage {entry.get('stage', '?')}"
    )


def filtered_runs(
    project_root: Path,
    mode: str = "ALL",
    topology: str | None = None,
    mesh_level: str | None = None,
) -> list[dict[str, Any]]:
    requested = mode.upper()
    if requested not in {"ALL", *MODES}:
        raise ValueError(f"Unsupported registry filter: {mode}")
    return [
        {**row, "title": execution_title(row)}
        for row in load_registry(project_root)["runs"]
        if (requested == "ALL" or row.get("mode") == requested)
        and (not topology or row.get("topology") == topology)
        and (not mesh_level or row.get("mesh_level") == mesh_level)
    ]


def migrate_known_executions(project_root: Path) -> dict[str, Any]:
    """Rebuild the light registry from canonical metadata; never launch a process."""
    root = active_workspace_root(Path(project_root).resolve())
    registry = load_registry(project_root)
    registry["runs"] = [row for row in registry["runs"] if str(row.get("mode")) != "URANS"]
    write_json_atomic(registry_path(project_root), registry)
    for checkpoint in sorted((root / "checkpoints").glob("*")) if (root / "checkpoints").is_dir() else ():
        manifest = read_json(checkpoint / "checkpoint_manifest.json", {}) or {}
        if manifest:
            upsert_execution(project_root, {
                "run_id": manifest.get("checkpoint_id") or f"{checkpoint.name}_simple",
                "mode": "RANS", "topology": manifest.get("topology"),
                "mesh_level": manifest.get("mesh_level"), "mesh_id": checkpoint.name,
                "stage": "SIMPLE", "status": manifest.get("status"),
                "started_at": manifest.get("prepared_at"),
                "updated_at": manifest.get("updated_at") or manifest.get("prepared_at"),
                "case_path": manifest.get("case"),
                "log_path": (manifest.get("gate") or {}).get("solver_log"),
                "iteration": manifest.get("iterations_completed", 0),
                "config_hash": manifest.get("solver_config_hash"),
            })
    runs_root = root / "runs"
    for manifest_path in sorted(runs_root.glob("*/*/*/case_manifest.json")) if runs_root.is_dir() else ():
        manifest = read_json(manifest_path, {}) or {}
        if not manifest:
            continue
        case_id = str(manifest.get("case_id") or manifest_path.parent.name)
        summary = read_json(manifest_path.parent / "execution_summary.json", {}) or {}
        upsert_execution(project_root, {
            "run_id": case_id, "case_id": case_id, "run_kind": "CANONICAL",
            "mode": "URANS", "topology": manifest.get("topology"),
            "mesh_level": manifest.get("mesh_level"), "mesh_id": manifest.get("mesh_id"),
            "stage": summary.get("stage") or manifest.get("stage"),
            "status": summary.get("status") or manifest.get("status"),
            "updated_at": summary.get("updated_at") or manifest.get("updated_at"),
            "case_path": str(manifest_path.parent / "case"),
            "deltaT": manifest.get("deltaT_s") or manifest.get("dt_s"),
        })
    return load_registry(project_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("migrate")
    listing = sub.add_parser("list")
    listing.add_argument("--mode", default="ALL", choices=["ALL", *sorted(MODES)])
    active = sub.add_parser("activate")
    active.add_argument("--run-id", required=True)
    active.add_argument("--pin", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "migrate":
        result: Any = migrate_known_executions(args.project_root)
    elif args.action == "list":
        result = filtered_runs(args.project_root, args.mode)
    else:
        result = set_active_execution(args.project_root, args.run_id, pin=args.pin)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
