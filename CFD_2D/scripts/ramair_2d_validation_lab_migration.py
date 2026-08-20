#!/usr/bin/env python3
"""Evidence-preserving migrations for the Validation & Convergence Lab."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from ramair_2d_execution_registry import upsert_execution
from ramair_2d_study_registry import (
    active_workspace_root,
    load_study,
    read_json,
    utc_stamp,
    write_json_atomic,
)


EXPLICIT_ACCEPTANCE = "RANS_USER_ACCEPTED_STATISTICALLY_STEADY"


def _field_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field_path(time_root: Path, name: str) -> Path | None:
    for candidate in (time_root / name, time_root / f"{name}.gz"):
        if candidate.is_file():
            return candidate
    return None


def _automatic_gate_status(
    checkpoint: dict[str, Any],
    review: dict[str, Any],
) -> tuple[str | None, str | None]:
    automatic_gate = dict(review.get("automatic_gate") or {})
    raw_status = automatic_gate.get("status")
    source_statuses = (
        str(checkpoint.get("status") or "").upper(),
        str(automatic_gate.get("checkpoint_status") or "").upper(),
    )
    if any("NOT_CONVERGED" in status for status in source_statuses):
        return "NOT_CONVERGED", raw_status
    return raw_status, raw_status


def restore_closed_coarse_user_acceptance(project_root: Path) -> dict[str, Any]:
    """Restore the real 20k base without changing its automatic gate."""
    project_root = Path(project_root).resolve()
    active = active_workspace_root(project_root)
    root = active / "checkpoints/closed_coarse"
    checkpoint_path = root / "checkpoint_manifest.json"
    review_path = root / "rans_review_manifest.json"
    checkpoint = read_json(checkpoint_path, {}) or {}
    review = read_json(review_path, {}) or {}
    if not checkpoint:
        return {
            "status": "CLOSED_COARSE_HISTORY_MISSING",
            "reason": "checkpoint_manifest_missing",
        }
    study = load_study(project_root)
    mesh = next(
        (
            row
            for row in study.get("mesh_registry", {}).get("meshes", [])
            if row.get("id") == "closed_coarse"
        ),
        None,
    )
    if mesh is None:
        return {
            "status": "CLOSED_COARSE_HISTORY_MISSING",
            "reason": "mesh_registry_row_missing",
        }
    if checkpoint.get("mesh_hash") != mesh.get("mesh_hash"):
        raise RuntimeError("closed_coarse mesh identity mismatch")
    iterations = int(checkpoint.get("iterations_completed") or 0)
    if iterations < 20000:
        return {
            "status": "CLOSED_COARSE_HISTORY_MISSING",
            "reason": f"only_{iterations}_iterations_recorded",
        }
    time_root = root / "case" / f"{iterations:g}"
    if not time_root.is_dir():
        return {
            "status": "CLOSED_COARSE_HISTORY_MISSING",
            "reason": f"final_time_directory_missing:{time_root}",
        }
    required = ("U", "p", "nuTilda")
    fields = {name: _field_path(time_root, name) for name in required}
    if any(path is None for path in fields.values()):
        return {
            "status": "CLOSED_COARSE_HISTORY_MISSING",
            "reason": "required_final_fields_missing",
            "missing_fields": [
                name for name, path in fields.items() if path is None
            ],
        }
    field_hashes = {
        name: _field_hash(path)
        for name, path in fields.items()
        if path is not None
    }
    automatic_status, automatic_raw_status = _automatic_gate_status(
        checkpoint,
        review,
    )
    nested_review = dict(review.get("review") or {})
    nested_review.update(
        {
            "status": EXPLICIT_ACCEPTANCE,
            "reviewed_by": "user",
            "decision_source": "EXPLICIT_USER_INSTRUCTION",
            "review_note": (
                "Acceptance restored from the explicit project instruction; "
                "the automatic gate remains unchanged."
            ),
            "confirmation": True,
            "reviewed_at": nested_review.get("reviewed_at") or utc_stamp(),
        }
    )
    review.update(
        {
            "review": nested_review,
            "review_status": EXPLICIT_ACCEPTANCE,
            "execution_status": "COMPLETED",
            "automatic_gate_status": automatic_status,
            "automatic_gate_raw_status": automatic_raw_status,
            "allowed_uses": {
                "rans_spatial_convergence": True,
                "urans_initialization": True,
            },
            "source": {
                **dict(review.get("source") or {}),
                "decision_source": "EXPLICIT_USER_INSTRUCTION",
                "contract": (
                    "CODEX_VALIDATION_LAB_COMPLETE_RANS_URANS_"
                    "SPACE_TIME_RESTRUCTURE.md"
                ),
                "verified_at": utc_stamp(),
            },
            "checkpoint": {
                **dict(review.get("checkpoint") or {}),
                "status": "READY",
                "restart_zero": str(time_root),
                "source_iteration": iterations,
                "solver_config_hash": checkpoint.get("solver_config_hash"),
                "field_hashes": field_hashes,
            },
            "updated_at": utc_stamp(),
        }
    )
    write_json_atomic(review_path, review)
    upsert_execution(
        project_root,
        {
            "run_id": checkpoint.get("checkpoint_id")
            or "closed_coarse_simple",
            "mode": "RANS",
            "topology": "closed",
            "mesh_level": "coarse",
            "mesh_id": "closed_coarse",
            "stage": "SIMPLE",
            "status": "COMPLETED",
            "case_path": str(root / "case"),
            "log_path": (checkpoint.get("gate") or {}).get("solver_log"),
            "iteration": iterations,
            "config_hash": checkpoint.get("solver_config_hash"),
            "updated_at": utc_stamp(),
        },
    )
    migration = {
        "schema_version": 1,
        "status": "CLOSED_COARSE_RESTORED",
        "mesh_id": "closed_coarse",
        "iterations": iterations,
        "mesh_hash": mesh["mesh_hash"],
        "physics_hash": checkpoint.get("physics_hash"),
        "field_hashes": field_hashes,
        "execution_status": "COMPLETED",
        "automatic_gate_status": automatic_status,
        "automatic_gate_raw_status": automatic_raw_status,
        "review_status": EXPLICIT_ACCEPTANCE,
        "decision_source": "EXPLICIT_USER_INSTRUCTION",
        "allowed_uses": review["allowed_uses"],
        "solver_relaunched": False,
        "verified_at": utc_stamp(),
    }
    write_json_atomic(
        active / "registry/closed_coarse_restoration.json",
        migration,
    )
    return migration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    result = restore_closed_coarse_user_acceptance(
        parse_args().project_root
    )
    print(result)
    return 0 if result.get("status") == "CLOSED_COARSE_RESTORED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
