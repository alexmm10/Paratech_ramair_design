#!/usr/bin/env python3
"""Extensible, metadata-only campaign engine for the current Validation Lab schema.

The engine plans and indexes scientific work.  It never launches OpenFOAM and
never copies or deletes meshes, RANS checkpoints, canonical URANS cases or
postprocess products.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from ramair_2d_study_registry import (
    STUDY_CONFIG_SCHEMA_VERSION,
    active_workspace_root,
    load_study,
    read_json,
    utc_stamp,
    write_json_atomic,
)


CAMPAIGN_SCHEMA_VERSION = 1
CAMPAIGN_CASE_STATES = {
    "PLANNED",
    "DEFERRED",
    "PREPARED",
    "RUNNING",
    "PAUSED_RECOVERABLE",
    "FAILED",
    "COMPLETED",
    "REVIEW_REQUIRED",
    "APPROVED",
    "REJECTED",
    "SKIPPED",
}
CAMPAIGN_DECISIONS = {"APPROVED", "REJECTED", "REVIEW_REQUIRED"}
MESH_LEVELS = ("coarse", "medium", "fine")
CLOSED_DT_STAR = (0.01, 0.005, 0.0025, 0.00125, 0.000625, 0.0003125)
OPEN_DT_STAR = (0.02, 0.01, 0.005, 0.0025, 0.00125, 0.000625)
CLOSED_OPTIMIZED = (
    ("C1", "coarse", 0.01),
    ("C2", "coarse", 0.005),
    ("M1", "medium", 0.005),
    ("M2", "medium", 0.0025),
    ("F1", "fine", 0.0025),
    ("F2", "fine", 0.00125),
)
CLOSED_CUMMINGS = (
    ("1", "coarse", 0.01),
    ("2", "coarse", 0.005),
    ("3", "medium", 0.0025),
    ("4", "medium", 0.00125),
    ("5", "fine", 0.000625),
    ("6", "fine", 0.0003125),
)
OPEN_CUMMINGS = (
    ("1", "coarse", 0.02),
    ("2", "coarse", 0.01),
    ("3", "medium", 0.005),
    ("4", "medium", 0.0025),
    ("5", "fine", 0.00125),
    ("6", "fine", 0.000625),
)


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _angle_token(value: float) -> str:
    sign = "m" if value < 0 else "p"
    text = f"{abs(float(value)):.6f}".rstrip("0").rstrip(".")
    return sign + text.replace(".", "p")


def campaign_id(topology: str, strategy: str, angles_deg: Iterable[float]) -> str:
    angles = "_".join(_angle_token(float(value)) for value in angles_deg)
    return f"{topology}_{strategy}_alpha_{angles}"


def methodology_contract(topology: str) -> dict[str, Any]:
    if topology not in {"closed", "open"}:
        raise ValueError("topology must be closed or open")
    common = {
        "joint_space_time": True,
        "same_physical_time_required": True,
        "minimum_cycles": 10,
        "nyquist_is_not_precision_criterion": True,
        "psd": {
            "method": "Welch",
            "window": "hann",
            "detrend": "constant",
            "overlap_fraction": 0.5,
            "strouhal_reference": "chord_and_U_inf",
            "wave_number_definition": "W=1/St",
        },
        "required_integral_signals": ["Cl", "Cd", "Cm"],
        "required_statistics": ["mean", "RMS"],
        "required_diagnostics": [
            "residuals",
            "probes",
            "separation",
            "Courant",
        ],
        "selection_uses_approved_runs_only": True,
        "full_matrix_is_capacity_not_default_execution": True,
    }
    if topology == "closed":
        return {
            **common,
            "angles_deg": [16.0, 8.0],
            "angle_order_reason": "stall_screening_then_operating_confirmation",
            "dt_star_ladder": list(CLOSED_DT_STAR),
            "screening_collection_time_star": 50.0,
            "final_collection_time_star": 100.0,
            "settling_detection": ["Cl", "Cd", "Cm", "residuals", "separation"],
            "probes_x_over_c": [0.5, 0.7, 0.85, 0.95],
            "wake_x_over_c": [1.05, 1.25, 1.5, 2.0],
            "default_strategy": "optimized",
        }
    return {
        **common,
        "angles_deg": [8.0, 16.0],
        "angle_order_reason": "inlet_physics_then_stall_confirmation",
        "dt_star_ladder": list(OPEN_DT_STAR),
        "screening_collection_time_star": 100.0,
        "final_collection_time_star": 100.0,
        "low_frequency_extension_time_star": 200.0,
        "settling_detection": [
            "Cl",
            "Cd",
            "Cm",
            "Cp_internal",
            "inlet_probes",
            "separation",
        ],
        "rans_diagnostics_required": [
            "stagnation",
            "lip_separation",
            "reattachment",
            "internal_pressure",
            "recirculation",
            "wake",
        ],
        "wake_x_over_c": [1.05, 1.25, 1.5, 2.0],
        "geometry_must_remain_fixed": True,
        "default_strategy": "progressive_medium_first",
    }


def _mesh_lookup(study: dict[str, Any], topology: str) -> dict[str, dict[str, Any]]:
    rows = (study.get("mesh_registry") or {}).get("meshes") or []
    result = {
        str(row.get("level")): dict(row)
        for row in rows
        if str(row.get("topology")) == topology
    }
    missing = [level for level in MESH_LEVELS if level not in result]
    if missing:
        raise RuntimeError(
            f"Missing {topology} mesh levels in the real registry: {missing}"
        )
    return result


def _existing_case_inventory(active: Path) -> dict[tuple[str, str, float, float], dict[str, Any]]:
    inventory: dict[tuple[str, str, float, float], dict[str, Any]] = {}
    for path in sorted((active / "runs").glob("*/*/*/case_manifest.json")):
        payload = read_json(path, {}) or {}
        key = dict(payload.get("scientific_key") or {})
        try:
            identity = (
                str(key["topology"]),
                str(key["mesh_level"]),
                float(key["alpha_deg"]),
                float(key["deltaT_s"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        inventory[identity] = {
            "case_id": payload.get("case_id") or path.parent.name,
            "case_root": str(path.parent),
            "case_path": payload.get("case_path"),
            "execution_outcome": payload.get("execution_outcome"),
            "current_phase": payload.get("current_phase"),
            "current_time_s": payload.get("current_time_s"),
            "terminal_reason": payload.get("terminal_reason"),
            "manifest": str(path),
        }
    return inventory


def _case_record(
    *,
    topology: str,
    label: str,
    mesh: dict[str, Any],
    alpha_deg: float,
    dt_star: float | None,
    tc_s: float,
    collection_time_star: float,
    state: str,
    reason: str,
    existing: dict[tuple[str, str, float, float], dict[str, Any]],
) -> dict[str, Any]:
    if state not in CAMPAIGN_CASE_STATES:
        raise ValueError(f"Unsupported campaign case state: {state}")
    dt_s = float(dt_star) * float(tc_s) if dt_star is not None else None
    identity = None
    if dt_s is not None:
        identity = existing.get(
            (topology, str(mesh["level"]), float(alpha_deg), float(dt_s))
        )
        if identity is not None:
            outcome = str(identity.get("execution_outcome") or "")
            state = {
                "COMPLETED": "COMPLETED",
                "RUNNING": "RUNNING",
                "PAUSED": "PAUSED_RECOVERABLE",
                "READY": "PREPARED",
            }.get(outcome, state)
            reason = "EXISTING_CANONICAL_CASE_INDEXED_WITHOUT_COPY"
    scientific = {
        "topology": topology,
        "mesh_id": mesh["id"],
        "mesh_level": mesh["level"],
        "alpha_deg": float(alpha_deg),
        "deltaT_s": dt_s,
        "deltaT_star": dt_star,
    }
    dependencies = [
        {
            "role": "mesh",
            "entity_id": str(mesh["id"]),
            "revision_id": str(mesh.get("mesh_hash") or ""),
        },
    ]
    if dt_star is not None:
        dependencies.append({
            "role": "rans_checkpoint",
            "entity_id": str(mesh["id"]),
            "revision_id": "RESOLVE_COMPATIBLE_REVIEWED_CHECKPOINT",
        })
    return {
        "case_key": _sha256_json(scientific),
        "label": label,
        "kind": "RANS_DIAGNOSTIC" if dt_star is None else "URANS",
        **scientific,
        "cell_count": int(mesh.get("cell_count") or 0),
        "geometry_reference": str(mesh.get("geometry_package") or ""),
        "mesh_reference": str(mesh.get("mesh_package") or ""),
        "mesh_revision": str(mesh.get("mesh_hash") or ""),
        "dependencies": dependencies,
        "settling": {
            "mode": "physical_signal_windows",
            "excluded_from_statistics": True,
        },
        "collection_time_star": float(collection_time_star),
        "minimum_cycles": 10,
        "state": state,
        "state_reason": reason,
        "existing_case": identity,
        "approval": {
            "state": "REVIEW_REQUIRED",
            "revision": 0,
            "history": [],
        },
    }


def build_campaign(
    project_root: Path,
    *,
    topology: str,
    strategy: str,
    angles_deg: Iterable[float],
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    study = load_study(project_root)
    if int((study.get("study_config") or {}).get("schema_version") or 0) != STUDY_CONFIG_SCHEMA_VERSION:
        raise RuntimeError(
            f"Validation Lab schema {STUDY_CONFIG_SCHEMA_VERSION} is required"
        )
    angles = tuple(float(value) for value in angles_deg)
    if not angles or any(not math.isfinite(value) for value in angles):
        raise ValueError("At least one finite angle is required")
    if topology not in {"closed", "open"}:
        raise ValueError("topology must be closed or open")
    allowed = {
        "closed": {"optimized", "cummings", "full_capacity"},
        "open": {"progressive_medium_first", "cummings", "full_capacity"},
    }[topology]
    if strategy not in allowed:
        raise ValueError(f"Unsupported {topology} campaign strategy: {strategy}")
    active = active_workspace_root(project_root)
    meshes = _mesh_lookup(study, topology)
    existing = _existing_case_inventory(active)
    condition = (study.get("study_config") or {}).get("operating_condition") or {}
    tc_s = float(condition.get("tc_s") or 0.0)
    if tc_s <= 0.0:
        raise RuntimeError("The operating condition has no positive tc_s")
    contract = methodology_contract(topology)
    cases: list[dict[str, Any]] = []
    for angle in angles:
        if topology == "closed":
            if strategy == "optimized":
                sequence = CLOSED_OPTIMIZED
            elif strategy == "cummings":
                sequence = CLOSED_CUMMINGS
            else:
                sequence = tuple(
                    (f"{level.upper()}-T{index + 1}", level, dt_star)
                    for level in MESH_LEVELS
                    for index, dt_star in enumerate(CLOSED_DT_STAR)
                )
            for label, level, dt_star in sequence:
                cases.append(_case_record(
                    topology=topology,
                    label=f"a{angle:g}-{label}",
                    mesh=meshes[level],
                    alpha_deg=angle,
                    dt_star=dt_star,
                    tc_s=tc_s,
                    collection_time_star=float(contract["screening_collection_time_star"]),
                    state="PLANNED",
                    reason="CLOSED_SCREENING_PLAN",
                    existing=existing,
                ))
        else:
            for level in MESH_LEVELS:
                cases.append(_case_record(
                    topology=topology,
                    label=f"a{angle:g}-RANS-{level}",
                    mesh=meshes[level],
                    alpha_deg=angle,
                    dt_star=None,
                    tc_s=tc_s,
                    collection_time_star=0.0,
                    state="PLANNED",
                    reason="RANS_DIAGNOSTICS_PRECEDE_URANS",
                    existing=existing,
                ))
            if strategy == "cummings":
                for label, level, dt_star in OPEN_CUMMINGS:
                    cases.append(_case_record(
                        topology=topology,
                        label=f"a{angle:g}-{label}",
                        mesh=meshes[level],
                        alpha_deg=angle,
                        dt_star=dt_star,
                        tc_s=tc_s,
                        collection_time_star=float(contract["screening_collection_time_star"]),
                        state="PLANNED",
                        reason="OPEN_CUMMINGS_LOW_COST_PLAN",
                        existing=existing,
                    ))
                continue
            # Keep the complete 3x6 capacity visible in both strategies.  The
            # progressive strategy only unlocks Medium first; Coarse/Fine are
            # explicit deferred spatial-crossing records, never hidden work.
            for level in MESH_LEVELS:
                for index, dt_star in enumerate(OPEN_DT_STAR):
                    if strategy == "progressive_medium_first" and level != "medium":
                        reason = "AWAIT_MEDIUM_TEMPORAL_CONVERGENCE"
                    else:
                        reason = "AWAIT_RANS_DIAGNOSTICS_AND_PREVIOUS_TIMESTEP"
                    cases.append(_case_record(
                        topology=topology,
                        label=f"a{angle:g}-{level}-T{index + 1}",
                        mesh=meshes[level],
                        alpha_deg=angle,
                        dt_star=dt_star,
                        tc_s=tc_s,
                        collection_time_star=float(contract["screening_collection_time_star"]),
                        state="DEFERRED",
                        reason=reason,
                        existing=existing,
                    ))
    campaign = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "validation_config_schema_version": STUDY_CONFIG_SCHEMA_VERSION,
        "campaign_id": campaign_id(topology, strategy, angles),
        "study_id": (study.get("study_manifest") or {}).get("study_id"),
        "topology": topology,
        "strategy": strategy,
        "angles_deg": list(angles),
        "methodology": contract,
        "physics": {
            "reynolds": condition.get("reynolds"),
            "mach": condition.get("mach"),
            "chord_m": condition.get("chord_m"),
            "velocity_m_s": condition.get("velocity_m_s"),
            "tc_s": tc_s,
            "turbulence_model": "SpalartAllmaras",
            "solver_family": "RANS_to_URANS_PIMPLE",
        },
        "cases": cases,
        "case_counts": {
            state: sum(row["state"] == state for row in cases)
            for state in sorted(CAMPAIGN_CASE_STATES)
        },
        "execution_policy": {
            "dry_run_default": True,
            "automatic_full_matrix": False,
            "prepare_lazily": True,
            "one_solver_lease": True,
            "existing_cases_are_indexed_not_copied": True,
            "preserve_rans_bases": True,
            "preserve_executed_runs": True,
        },
        "selection_policy": {
            "approved_runs_only": True,
            "same_physical_time_required": True,
            "minimum_cycles": 10,
            "adaptive_extension": True,
        },
        "campaign_revision": 1,
        "created_at": utc_stamp(),
        "updated_at": utc_stamp(),
    }
    campaign["campaign_hash"] = _sha256_json(
        {key: campaign[key] for key in ("topology", "strategy", "angles_deg", "physics", "cases")}
    )
    return campaign


def write_campaign(project_root: Path, campaign: dict[str, Any]) -> Path:
    active = active_workspace_root(Path(project_root).resolve()).resolve()
    destination = (active / "campaigns" / str(campaign["campaign_id"])).resolve()
    if active not in destination.parents:
        raise RuntimeError("Campaign path escapes the Validation Lab workspace")
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "campaign_manifest.json"
    previous = read_json(path, {}) or {}
    previous_cases = {
        str(row.get("case_key")): row
        for row in previous.get("cases", [])
        if isinstance(row, dict)
    }
    merged = []
    for source in campaign.get("cases", []):
        row = dict(source)
        old = previous_cases.get(str(row.get("case_key")))
        if old:
            row["approval"] = old.get("approval", row.get("approval"))
            if str(old.get("state")) in CAMPAIGN_CASE_STATES:
                row["state"] = old["state"]
                row["state_reason"] = old.get("state_reason")
        merged.append(row)
    campaign = dict(campaign)
    campaign["cases"] = merged
    campaign["campaign_revision"] = int(previous.get("campaign_revision") or 0) + 1
    campaign["created_at"] = previous.get("created_at", campaign.get("created_at", utc_stamp()))
    campaign["updated_at"] = utc_stamp()
    write_json_atomic(path, campaign)
    write_json_atomic(
        active / "campaigns/campaign_index.json",
        {
            "schema_version": 1,
            "updated_at": utc_stamp(),
            "campaigns": [
                {
                    "campaign_id": item.parent.name,
                    "manifest": str(item.relative_to(active)),
                }
                for item in sorted((active / "campaigns").glob("*/campaign_manifest.json"))
            ],
        },
    )
    return path


def set_case_decision(
    campaign_path: Path,
    case_key: str,
    decision: str,
    *,
    actor: str,
    evidence: dict[str, Any],
    note: str | None = None,
) -> dict[str, Any]:
    if decision not in CAMPAIGN_DECISIONS:
        raise ValueError(f"Unsupported campaign decision: {decision}")
    campaign_path = Path(campaign_path).resolve()
    campaign = read_json(campaign_path, {}) or {}
    rows = list(campaign.get("cases") or [])
    selected = next((row for row in rows if str(row.get("case_key")) == case_key), None)
    if selected is None:
        raise KeyError(f"Unknown campaign case key: {case_key}")
    approval = dict(selected.get("approval") or {})
    history = list(approval.get("history") or [])
    revision = int(approval.get("revision") or 0) + 1
    entry = {
        "revision": revision,
        "decision": decision,
        "actor": str(actor).strip() or "local-user",
        "evidence": dict(evidence),
        "note": str(note or "").strip() or None,
        "decided_at": utc_stamp(),
    }
    history.append(entry)
    selected["approval"] = {
        "state": decision,
        "revision": revision,
        "history": history,
        "current": entry,
    }
    selected["state"] = decision
    selected["state_reason"] = "IMMUTABLE_REVIEW_REVISION"
    campaign["updated_at"] = utc_stamp()
    write_json_atomic(campaign_path, campaign)
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--topology", choices=("closed", "open"), required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--angle", type=float, action="append", required=True)
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign = build_campaign(
        args.project_root,
        topology=args.topology,
        strategy=args.strategy,
        angles_deg=args.angle,
    )
    if args.write:
        result: Any = {"manifest": str(write_campaign(args.project_root, campaign))}
    else:
        result = campaign
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
