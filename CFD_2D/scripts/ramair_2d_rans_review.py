#!/usr/bin/env python3
"""Traceable review and scalar diagnostics for existing RANS/SIMPLE bases."""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from openfoam_history import read_force_coefficient_history
from ramair_2d_postprocess import parse_solver_log
from ramair_2d_rans_plateau_gate import (
    AUTOMATIC_GATE_STATUSES,
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    RANS_DIVERGED,
    RANS_REVIEW_REQUIRED,
    evaluate_rans_gate,
)
from ramair_2d_postprocess_registry import write_postprocess_manifest
from ramair_2d_execution_registry import upsert_execution
from ramair_scientific_plot_style import (
    ACCESSIBLE_COLORS,
    MARKERS,
    save_scientific_figure,
)
from ramair_2d_study_registry import (
    MESH_IDS,
    active_workspace_root,
    load_study,
    read_json,
    utc_stamp,
    write_json_atomic,
)


# Compatibility alias for callers created before laboratory schema 4.
RANS_AUTO_CONVERGED = RANS_AUTO_CONVERGED_STRICT
RANS_USER_ACCEPTED_STATISTICALLY_STEADY = (
    "RANS_USER_ACCEPTED_STATISTICALLY_STEADY"
)
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY = (
    "RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY"
)
RANS_REJECTED = "RANS_REJECTED"
RANS_NOT_REVIEWED = "NOT_REVIEWED"

REVIEW_STATUSES = {
    RANS_NOT_REVIEWED,
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
    RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
    RANS_REJECTED,
}
RANS_SPATIAL_ACCEPTED = {
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
}
URANS_INITIALIZATION_ACCEPTED = {
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
    RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
}
MANUAL_REVIEW_STATUSES = {
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
    RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
    RANS_REJECTED,
}
REQUIRED_RESTART_FIELDS = ("U", "p", "nuTilda")
OPTIONAL_RESTART_FIELDS = ("phi", "nut", "alphat")


def _checkpoint_root(project_root: Path, mesh_id: str) -> Path:
    return active_workspace_root(project_root) / "checkpoints" / mesh_id


def _manifest_path(checkpoint_root: Path) -> Path:
    return checkpoint_root / "rans_review_manifest.json"


def _columns(frame: pd.DataFrame) -> tuple[str, str, str, str]:
    names = {str(name).strip().lower(): str(name) for name in frame.columns}
    iteration = names.get("iteration") or names.get("time")
    cl_name = names.get("cl")
    cd_name = names.get("cd")
    cm_name = names.get("cm")
    if not all((iteration, cl_name, cd_name, cm_name)):
        raise ValueError("RANS force history must contain Iteration/Time, Cl, Cd and Cm")
    return iteration, cl_name, cd_name, cm_name


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> Path | None:
    records = list(rows)
    if not records:
        return None
    columns = list(dict.fromkeys(key for row in records for key in row))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)
    return path


def _automatic_gate(checkpoint: dict[str, Any]) -> dict[str, Any]:
    gate = dict(checkpoint.get("gate") or {})
    checkpoint_status = str(checkpoint.get("status") or "RANS_BASE_NOT_CREATED")
    existing = str(gate.get("automatic_gate_status") or "")
    if existing in AUTOMATIC_GATE_STATUSES:
        return {
            **gate,
            "status": existing,
            "checkpoint_status_at_migration": checkpoint_status,
        }
    if checkpoint_status in {"RANS_BASE_DIVERGED", "RANS_BASE_FAILED"}:
        auto = RANS_DIVERGED
    elif checkpoint_status == "CHECKPOINT_READY" or checkpoint.get("converged") is True:
        auto = RANS_AUTO_CONVERGED_STRICT
    else:
        auto = RANS_REVIEW_REQUIRED
    failed: list[str] = []
    for field, values in (gate.get("residual_metrics") or {}).items():
        if values.get("acceptable") is False:
            failed.append(f"residual_{field}")
    for name, values in ((gate.get("force_plateau") or {}).get("metrics") or {}).items():
        if values.get("stable") is False:
            failed.append(f"force_plateau_{name}")
    if gate.get("force_plateau", {}).get("status") == "UNSTABLE" and not failed:
        failed.append("force_plateau")
    return {
        "status": auto,
        "schema_version": 1,
        "provenance": "legacy_checkpoint_gate_migration",
        "original_gate_status": gate.get("status"),
        "failed_criteria": failed,
        "checkpoint_status_at_migration": checkpoint_status,
        "details": gate,
    }


def _execution_status(checkpoint: dict[str, Any]) -> str:
    explicit = str(checkpoint.get("execution_status") or "").strip().upper()
    if explicit:
        return explicit
    status = str(checkpoint.get("status") or "RANS_BASE_NOT_CREATED")
    if status in {"RANS_BASE_PREPARED", "PREPARED"}:
        return "PREPARED"
    if status in {"RANS_BASE_RUNNING", "RANS_BASE_EXTENDING", "RUNNING"}:
        return "RUNNING"
    if status in {"RANS_PARTIAL", "TIMEOUT_PARTIAL", "STOPPED_BY_USER"}:
        return "PARTIAL"
    if status in {
        "CHECKPOINT_READY",
        "MANUAL_REVIEW_CHECKPOINT_READY",
        "RANS_BASE_BOUNDED_NOT_CONVERGED",
        "RANS_BASE_MAX_ITERATIONS",
        RANS_AUTO_CONVERGED_STRICT,
        RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
        RANS_REVIEW_REQUIRED,
    }:
        return "COMPLETED"
    if status in {"RANS_BASE_DIVERGED", "RANS_BASE_FAILED", RANS_DIVERGED}:
        return "FAILED"
    return "NOT_STARTED"


def _synchronize_state_fields(
    manifest: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Keep execution, automatic gate, review and permissions independent."""
    manifest["execution_status"] = _execution_status(checkpoint)
    manifest["automatic_gate_status"] = str(
        manifest.get("automatic_gate", {}).get("status") or RANS_REVIEW_REQUIRED
    )
    manifest["review_status"] = str(
        manifest.get("review", {}).get("status") or RANS_NOT_REVIEWED
    )
    manifest.setdefault(
        "allowed_uses",
        {
            "rans_spatial_convergence": False,
            "urans_initialization": False,
        },
    )
    return manifest


def _default_review_manifest(
    project_root: Path,
    mesh_id: str,
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    auto = _automatic_gate(checkpoint)
    automatic_accepted = auto["status"] in {
        RANS_AUTO_CONVERGED_STRICT,
        RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    }
    manifest = {
        "schema_version": 2,
        "run_id": str(checkpoint.get("checkpoint_id") or f"{mesh_id}_simple"),
        "mesh_id": mesh_id,
        "topology": checkpoint.get("topology"),
        "mesh_level": checkpoint.get("mesh_level"),
        "mesh_hash": checkpoint.get("mesh_hash", ""),
        "physics_hash": checkpoint.get("physics_hash", ""),
        "solver_config_hash": checkpoint.get("solver_config_hash", ""),
        "automatic_gate": auto,
        "review": {
            "status": RANS_NOT_REVIEWED,
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": None,
            "reason": None,
            "confirmation": False,
            "history": [],
        },
        "allowed_uses": {
            "rans_spatial_convergence": automatic_accepted,
            "urans_initialization": automatic_accepted,
        },
        "postprocess": {
            "status": "NOT_GENERATED",
            "generated_at": None,
            "evidence": [],
            "source_samples": 0,
        },
        "checkpoint": {
            "status": (
                "READY"
                if checkpoint.get("status") == "CHECKPOINT_READY"
                else "NOT_CREATED"
            ),
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "field_hashes": dict(checkpoint.get("field_hashes") or {}),
        },
        "source": {
            "checkpoint_manifest": str(
                _checkpoint_root(project_root, mesh_id) / "checkpoint_manifest.json"
            ),
            "checkpoint_status": checkpoint.get("status"),
            "migrated_at": utc_stamp(),
        },
        "updated_at": utc_stamp(),
    }
    return _synchronize_state_fields(manifest, checkpoint)


def migrate_existing_bases(
    project_root: Path,
    mesh_ids: Iterable[str] = MESH_IDS,
) -> list[dict[str, Any]]:
    """Create review metadata without changing existing simulation products."""
    project_root = Path(project_root).resolve()
    rows: list[dict[str, Any]] = []
    for mesh_id in mesh_ids:
        checkpoint_root = _checkpoint_root(project_root, mesh_id)
        checkpoint = read_json(checkpoint_root / "checkpoint_manifest.json", {}) or {}
        if not checkpoint:
            rows.append({"mesh_id": mesh_id, "status": "NO_RANS_BASE"})
            continue
        path = _manifest_path(checkpoint_root)
        existing = read_json(path, {}) or {}
        if existing:
            existing_auto = dict(existing.get("automatic_gate") or {})
            if str(existing_auto.get("status")) not in AUTOMATIC_GATE_STATUSES:
                existing_auto = _automatic_gate(checkpoint)
            existing["automatic_gate"] = existing_auto
            existing.setdefault("review", {})
            existing["review"].setdefault("history", [])
            legacy_review = str(existing["review"].get("status") or "")
            if legacy_review in {
                "RANS_AUTO_CONVERGED",
                RANS_AUTO_CONVERGED_STRICT,
                RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
                RANS_REVIEW_REQUIRED,
            }:
                existing["review"]["status"] = RANS_NOT_REVIEWED
            existing["review"].setdefault(
                "review_note", existing["review"].get("reason")
            )
            existing["review"].setdefault("confirmation", False)
            existing.setdefault("postprocess", {"status": "NOT_GENERATED"})
            existing.setdefault("checkpoint", {})
            manual = str(existing["review"].get("status") or "")
            automatic_accepted = str(existing_auto.get("status") or "") in {
                RANS_AUTO_CONVERGED_STRICT,
                RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
            }
            existing["allowed_uses"] = {
                "rans_spatial_convergence": (
                    automatic_accepted
                    or manual == RANS_USER_ACCEPTED_STATISTICALLY_STEADY
                ) and manual != RANS_REJECTED,
                "urans_initialization": (
                    automatic_accepted
                    or manual
                    in {
                        RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
                        RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
                    }
                ) and manual != RANS_REJECTED,
            }
            existing["source"] = {
                **dict(existing.get("source") or {}),
                "checkpoint_status": checkpoint.get("status"),
                "last_metadata_sync_at": utc_stamp(),
            }
            existing["updated_at"] = utc_stamp()
            existing["schema_version"] = 2
            manifest = _synchronize_state_fields(existing, checkpoint)
        else:
            manifest = _default_review_manifest(project_root, mesh_id, checkpoint)
        write_json_atomic(path, manifest)
        rows.append(
            {
                "mesh_id": mesh_id,
                "status": manifest["review"]["status"],
                "automatic_gate": manifest["automatic_gate"]["status"],
                "postprocess": manifest["postprocess"]["status"],
                "path": str(path),
            }
        )
    return rows


def review_manifest(project_root: Path, mesh_id: str) -> dict[str, Any]:
    migrate_existing_bases(project_root, [mesh_id])
    return read_json(_manifest_path(_checkpoint_root(project_root, mesh_id)), {}) or {}


def _force_frame(case: Path) -> tuple[pd.DataFrame, list[str]]:
    records, sources = read_force_coefficient_history(case, include_processor0=True)
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        history = case / "steadyInitialization/history"
        archives = sorted(path for path in history.glob("run_*") if path.is_dir())
        merged: list[dict[str, Any]] = []
        merged_sources: list[str] = []
        for archive in archives:
            archive_records, archive_sources = read_force_coefficient_history(
                archive, include_processor0=True
            )
            merged.extend(archive_records)
            merged_sources.extend(archive_sources)
        frame = pd.DataFrame.from_records(merged)
        sources = merged_sources
    if frame.empty:
        return frame, sources
    iteration, cl_name, cd_name, cm_name = _columns(frame)
    selected = frame[[iteration, cl_name, cd_name, cm_name]].copy()
    selected.columns = ["Iteration", "Cl", "Cd", "Cm"]
    for name in selected.columns:
        selected[name] = pd.to_numeric(selected[name], errors="coerce")
    selected = selected.dropna().sort_values("Iteration")
    selected = selected.drop_duplicates(subset=["Iteration"], keep="last")
    selected["Cl_over_Cd"] = selected["Cl"] / selected["Cd"].replace(0.0, np.nan)
    return selected.reset_index(drop=True), sources


def _solver_frames(
    case: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict[str, Any]]:
    roots = [case]
    history = case / "steadyInitialization/history"
    if history.is_dir():
        roots.extend(sorted(path for path in history.glob("run_*") if path.is_dir()))
    residual_frames: list[pd.DataFrame] = []
    courant_frames: list[pd.DataFrame] = []
    logs: list[str] = []
    discarded = 0
    for segment_order, root in enumerate(roots):
        residuals, courant, metadata = parse_solver_log(root)
        if metadata.get("solver_log_found"):
            logs.append(str(metadata.get("solver_log")))
        if not residuals.empty:
            residuals = residuals.copy()
            residuals["segment"] = root.name
            residuals["segment_order"] = segment_order
            residuals["absolute_iteration"] = pd.to_numeric(
                residuals.get("Time"), errors="coerce"
            )
            residuals["equation"] = residuals["field"].astype(str).map(
                {
                    "Ux": "U.x",
                    "Uy": "U.y",
                    "Uz": "U.z",
                }
            ).fillna(residuals["field"].astype(str))
            residuals["field"] = residuals["equation"]
            before = len(residuals)
            residuals = residuals[
                ~residuals["field"].str.fullmatch(
                    r"(?i)(phi|potentialPhi)", na=False
                )
            ]
            residuals = residuals.dropna(
                subset=["absolute_iteration", "initial_residual"]
            )
            discarded += before - len(residuals)
            residual_frames.append(residuals)
        if not courant.empty:
            courant = courant.copy()
            courant["segment"] = root.name
            courant_frames.append(courant)
    residuals = (
        pd.concat(residual_frames, ignore_index=True)
        if residual_frames
        else pd.DataFrame()
    )
    courant = (
        pd.concat(courant_frames, ignore_index=True)
        if courant_frames
        else pd.DataFrame()
    )
    if not residuals.empty:
        residuals = residuals.sort_values(
            ["absolute_iteration", "segment_order"]
        )
        before = len(residuals)
        residuals = residuals.drop_duplicates(
            subset=["absolute_iteration", "field"], keep="last"
        )
        discarded += before - len(residuals)
        residuals["Time"] = residuals["absolute_iteration"]
        residuals["iteration"] = residuals["absolute_iteration"]
    metadata = {
        "segments_found": len(roots),
        "segment_names": [root.name for root in roots],
        "iteration_start": (
            float(residuals["absolute_iteration"].min())
            if not residuals.empty
            else None
        ),
        "iteration_end": (
            float(residuals["absolute_iteration"].max())
            if not residuals.empty
            else None
        ),
        "fields_found": (
            sorted(set(residuals["field"].astype(str)))
            if not residuals.empty
            else []
        ),
        "rows_discarded": int(discarded),
        "rows_retained": int(len(residuals)),
    }
    return residuals.reset_index(drop=True), courant, logs, metadata


def _window_stats(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], int]:
    count = len(frame)
    requested = max(1000, int(math.ceil(0.10 * count)))
    window = min(requested, max(1, count // 2))
    rows: list[dict[str, Any]] = []
    for fraction in (0.05, 0.10, 0.20):
        size = min(max(2, int(math.ceil(fraction * count))), count)
        data = frame.iloc[-size:]
        for coefficient in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
            values = pd.to_numeric(data[coefficient], errors="coerce").dropna()
            if values.empty:
                continue
            x = np.arange(len(values), dtype=float)
            slope = float(np.polyfit(x, values.to_numpy(), 1)[0]) if len(values) > 1 else 0.0
            mean = float(values.mean())
            rows.append(
                {
                    "window_fraction": fraction,
                    "samples": int(len(values)),
                    "coefficient": coefficient,
                    "mean": mean,
                    "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
                    "rms_fluctuation": float(np.sqrt(np.mean((values - mean) ** 2))),
                    "drift_per_iteration": slope,
                    "relative_drift_percent_per_window": (
                        100.0 * slope * len(values) / max(abs(mean), 1.0e-12)
                    ),
                }
            )
    final = frame.iloc[-window:]
    previous = frame.iloc[-2 * window : -window]
    for coefficient in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
        current = pd.to_numeric(final[coefficient], errors="coerce").dropna()
        prior = pd.to_numeric(previous[coefficient], errors="coerce").dropna()
        if current.empty or prior.empty:
            continue
        scale = max(abs(float(current.mean())), abs(float(prior.mean())), 1.0e-12)
        rows.append(
            {
                "window_fraction": "comparison",
                "samples": int(min(len(current), len(prior))),
                "coefficient": coefficient,
                "previous_mean": float(prior.mean()),
                "mean": float(current.mean()),
                "standard_deviation": float(current.std(ddof=1)) if len(current) > 1 else 0.0,
                "mean_change_percent": 100.0 * abs(float(current.mean() - prior.mean())) / scale,
            }
        )
    return rows, window


def _block_stats(frame: pd.DataFrame, blocks: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block_id, indices in enumerate(np.array_split(np.arange(len(frame)), blocks), 1):
        if not len(indices):
            continue
        block = frame.iloc[indices]
        for coefficient in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
            values = pd.to_numeric(block[coefficient], errors="coerce").dropna()
            if values.empty:
                continue
            rows.append(
                {
                    "block": block_id,
                    "start_iteration": float(block["Iteration"].iloc[0]),
                    "end_iteration": float(block["Iteration"].iloc[-1]),
                    "coefficient": coefficient,
                    "mean": float(values.mean()),
                    "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
                    "samples": int(len(values)),
                }
            )
    return rows


def _gate_rows(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    gate = checkpoint.get("gate") or {}
    rows: list[dict[str, Any]] = []
    for field, values in (gate.get("residual_metrics") or {}).items():
        rows.append(
            {
                "group": "residual",
                "criterion": field,
                "value": values.get("last_initial_residual"),
                "threshold": values.get("tolerance"),
                "result": "PASS" if values.get("acceptable") else "FAIL",
            }
        )
    force_gate = gate.get("force_plateau") or {}
    for name, values in (force_gate.get("metrics") or {}).items():
        rows.extend(
            [
                {
                    "group": "force_mean_change",
                    "criterion": name,
                    "value": values.get("mean_change_percent"),
                    "threshold": values.get("mean_tolerance_percent"),
                    "result": (
                        "PASS"
                        if float(values.get("mean_change_percent", math.inf))
                        <= float(values.get("mean_tolerance_percent", -math.inf))
                        else "FAIL"
                    ),
                },
                {
                    "group": "force_fluctuation",
                    "criterion": name,
                    "value": values.get("current_fluctuation_percent"),
                    "threshold": values.get("fluctuation_tolerance_percent"),
                    "result": (
                        "PASS"
                        if float(values.get("current_fluctuation_percent", math.inf))
                        <= float(values.get("fluctuation_tolerance_percent", -math.inf))
                        else "FAIL"
                    ),
                },
            ]
        )
    return rows


def _diagnostic_gate_rows(diagnostic: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field, values in (diagnostic.get("residual_metrics") or {}).items():
        rows.append(
            {
                "group": "soft_residual",
                "criterion": field,
                "value": values.get("last_initial_residual"),
                "threshold": values.get("preferred_limit"),
                "result": "PASS" if values.get("pass") else "FAIL",
            }
        )
    force = diagnostic.get("force_stationarity") or {}
    for name, values in (force.get("metrics") or {}).items():
        rows.append(
            {
                "group": "force_stationarity",
                "criterion": name,
                "value": values.get("mean_change_percent"),
                "threshold": values.get("mean_tolerance_percent"),
                "result": "PASS" if values.get("stable") else "FAIL",
            }
        )
    continuity = diagnostic.get("continuity") or {}
    rows.append(
        {
            "group": "hard_continuity",
            "criterion": "continuity",
            "value": continuity.get("median"),
            "threshold": "bounded and non-growing",
            "result": "PASS" if continuity.get("pass") else "FAIL",
        }
    )
    return rows


def _plot_diagnostics(
    checkpoint_root: Path,
    frame: pd.DataFrame,
    residuals: pd.DataFrame,
    window: int,
    gate_rows: list[dict[str, Any]],
) -> list[Path]:
    import matplotlib.pyplot as plt

    output: list[Path] = []
    colors = dict(zip(("Cl", "Cd", "Cm"), ACCESSIBLE_COLORS))

    figure, axes = plt.subplots(2, 1, figsize=(9.0, 6.6), sharex=True)
    axis, efficiency_axis = axes
    for name in ("Cl", "Cd", "Cm"):
        axis.plot(
            frame["Iteration"],
            frame[name],
            color=colors[name],
            label=name,
            lw=0.9,
            marker=MARKERS[("Cl", "Cd", "Cm").index(name)],
            markevery=max(1, len(frame) // 25),
            markersize=3.0,
        )
    for panel in axes:
        panel.axvspan(
            frame["Iteration"].iloc[-window],
            frame["Iteration"].iloc[-1],
            color="#D9EAD3",
            alpha=0.35,
            label="Final review window" if panel is axis else None,
        )
    efficiency_axis.plot(
        frame["Iteration"],
        frame["Cl_over_Cd"],
        color=ACCESSIBLE_COLORS[3],
        label=r"$C_l/C_d$",
        lw=0.9,
        marker=MARKERS[3],
        markevery=max(1, len(frame) // 25),
        markersize=3.0,
    )
    axis.set(ylabel="Aerodynamic coefficient [-]", title="Aerodynamic coefficients - RANS/SIMPLE")
    efficiency_axis.set(xlabel="SIMPLE iteration", ylabel=r"Aerodynamic efficiency $C_l/C_d$ [-]")
    axis.legend(ncols=4, fontsize=8)
    efficiency_axis.legend(fontsize=8)
    path = checkpoint_root / "rans_forces.png"
    save_scientific_figure(
        figure,
        path,
        data=frame[["Iteration", "Cl", "Cd", "Cm", "Cl_over_Cd"]],
        metadata={
            "source": "RANS forceCoeffs history",
            "transformation": "raw coefficient histories; Cl/Cd computed row-wise",
            "filters": [f"final review window contains {window} samples"],
        },
    )
    output.append(path)

    rolling = max(25, min(window, max(25, len(frame) // 20)))
    figure, axes = plt.subplots(3, 1, figsize=(9.0, 8.4), sharex=True)
    for axis, name in zip(axes, ("Cl", "Cd", "Cm")):
        values = frame[name]
        mean = values.rolling(rolling, min_periods=max(5, rolling // 4)).mean()
        rms = values.rolling(rolling, min_periods=max(5, rolling // 4)).std()
        drift = mean.diff(rolling).abs()
        axis.plot(frame["Iteration"], mean, label="moving mean", lw=1.0)
        axis.plot(frame["Iteration"], rms, label="moving RMS", lw=0.9)
        axis.plot(frame["Iteration"], drift, label="moving drift", lw=0.9)
        axis.set_ylabel(name)
        axis.grid(alpha=0.22)
    axes[0].legend(ncol=3, fontsize=8)
    axes[-1].set_xlabel("SIMPLE iteration")
    figure.suptitle(f"Moving RANS statistics ({rolling} samples)")
    path = checkpoint_root / "rans_moving_statistics.png"
    moving = pd.DataFrame({"Iteration": frame["Iteration"]})
    for name in ("Cl", "Cd", "Cm"):
        values = frame[name]
        mean = values.rolling(rolling, min_periods=max(5, rolling // 4)).mean()
        moving[f"{name}_moving_mean"] = mean
        moving[f"{name}_moving_rms"] = values.rolling(
            rolling, min_periods=max(5, rolling // 4)
        ).std()
        moving[f"{name}_moving_drift"] = mean.diff(rolling).abs()
    save_scientific_figure(
        figure,
        path,
        data=moving,
        metadata={
            "source": "RANS forceCoeffs history",
            "transformation": f"rolling mean, standard deviation and lag-{rolling} drift",
            "filters": [],
        },
    )
    output.append(path)

    figure, axes = plt.subplots(2, 2, figsize=(9.0, 6.8))
    previous = frame.iloc[-2 * window : -window]
    current = frame.iloc[-window:]
    for axis, name in zip(axes.flat, ("Cl", "Cd", "Cm", "Cl_over_Cd")):
        axis.hist(previous[name].dropna(), bins=30, alpha=0.55, label="previous")
        axis.hist(current[name].dropna(), bins=30, alpha=0.55, label="final")
        axis.set_title(name)
        axis.grid(alpha=0.2)
    axes[0, 0].legend()
    figure.suptitle("Comparison and density of the two final windows")
    path = checkpoint_root / "rans_window_comparison.png"
    window_rows: list[dict[str, Any]] = []
    for label, source in (("previous", previous), ("final", current)):
        for name in ("Cl", "Cd", "Cm", "Cl_over_Cd"):
            window_rows.extend(
                {"window": label, "coefficient": name, "value": float(value)}
                for value in source[name].dropna()
            )
    save_scientific_figure(
        figure,
        path,
        data=window_rows,
        metadata={
            "source": "RANS forceCoeffs history",
            "transformation": "histograms of the previous and final review windows",
            "grouping": "coefficient and review window",
        },
    )
    output.append(path)

    figure, axis = plt.subplots(figsize=(9.0, 4.4))
    if not residuals.empty and {
        "field", "initial_residual", "absolute_iteration"
    }.issubset(residuals.columns):
        for field in ("p", "U.x", "U.y", "nuTilda"):
            data = residuals[residuals["field"].astype(str) == field]
            if not data.empty:
                x_values = data["absolute_iteration"]
                values = pd.to_numeric(
                    data["initial_residual"], errors="coerce"
                )
                values = values.where(values > 0.0, np.nan)
                axis.semilogy(
                    x_values,
                    values,
                    label=field,
                    lw=0.8,
                )
    axis.set(
        xlabel="SIMPLE iteration",
        ylabel="Initial residual [-]",
        title="Residual convergence - RANS/SIMPLE",
    )
    axis.grid(alpha=0.22, which="both")
    if axis.lines:
        axis.legend()
    path = checkpoint_root / "rans_residuals.png"
    save_scientific_figure(
        figure,
        path,
        data=residuals,
        metadata={
            "source": "RANS solver residual log",
            "transformation": "positive initial residuals on a logarithmic ordinate",
            "grouping": "field",
        },
    )
    output.append(path)
    return output


def _plot_execution_cost(
    checkpoint_root: Path,
    *,
    iterations: int,
    total_wall_seconds: float,
) -> Path | None:
    if iterations <= 0 or total_wall_seconds <= 0.0:
        return None
    import matplotlib.pyplot as plt

    x = np.linspace(0.0, float(iterations), 250)
    cumulative = x * total_wall_seconds / float(iterations)
    figure, axes = plt.subplots(2, 1, figsize=(8.5, 6.7), sharex=True)
    axes[0].plot(x, cumulative / 60.0, lw=1.0)
    axes[0].set_ylabel("Estimated cumulative wall time [min]")
    axes[0].grid(alpha=0.22)
    axes[1].plot(
        x,
        np.full_like(x, total_wall_seconds / float(iterations)),
        lw=1.0,
    )
    axes[1].set(
        xlabel="SIMPLE iteration",
        ylabel="Average [s/iteration]",
    )
    axes[1].grid(alpha=0.22)
    figure.suptitle(
        "RANS execution cost (aggregate timing distributed over iterations)"
    )
    path = checkpoint_root / "rans_execution_cost.png"
    save_scientific_figure(
        figure,
        path,
        data=pd.DataFrame({
            "Iteration": x,
            "estimated_cumulative_wall_time_min": cumulative / 60.0,
            "average_seconds_per_iteration": np.full_like(
                x, total_wall_seconds / float(iterations)
            ),
        }),
        metadata={
            "source": "RANS aggregate solver timing",
            "transformation": "linear attribution of aggregate wall time across iterations",
            "filters": [],
        },
    )
    return path


def generate_review_diagnostics(project_root: Path, mesh_id: str) -> dict[str, Any]:
    """Generate review evidence from existing scalar data without running a solver."""
    project_root = Path(project_root).resolve()
    checkpoint_root = _checkpoint_root(project_root, mesh_id)
    checkpoint = read_json(checkpoint_root / "checkpoint_manifest.json", {}) or {}
    if not checkpoint:
        raise FileNotFoundError(f"No existing RANS base for {mesh_id}")
    manifest = review_manifest(project_root, mesh_id)
    case = checkpoint_root / "case"
    frame, force_sources = _force_frame(case)
    if len(frame) < 16:
        manifest["postprocess"] = {
            "status": "INSUFFICIENT_REAL_DATA",
            "generated_at": utc_stamp(),
            "evidence": [],
            "source_samples": int(len(frame)),
            "reason": "At least 16 real force samples are required",
        }
        manifest["updated_at"] = utc_stamp()
        _synchronize_state_fields(manifest, checkpoint)
        write_json_atomic(_manifest_path(checkpoint_root), manifest)
        return manifest

    residuals, courant, logs, residual_history = _solver_frames(case)
    window_rows, window = _window_stats(frame)
    block_rows = _block_stats(frame)
    study = load_study(project_root)
    validation = study["study_config"]["validation_study"]
    diagnostic = evaluate_rans_gate(
        checkpoint,
        frame,
        residuals,
        rans_config=dict(validation.get("rans_base_states") or {}),
        convergence_config=dict(validation.get("rans_convergence") or {}),
    )
    gate_rows = _diagnostic_gate_rows(diagnostic)
    evidence: list[Path] = []
    force_csv = checkpoint_root / "force_coeffs.csv"
    frame.to_csv(force_csv, index=False)
    evidence.append(force_csv)
    if not residuals.empty:
        residual_csv = checkpoint_root / "residuals.csv"
        residuals.to_csv(residual_csv, index=False)
        evidence.append(residual_csv)
    for path in (
        _write_csv(checkpoint_root / "rans_window_statistics.csv", window_rows),
        _write_csv(checkpoint_root / "rans_block_statistics.csv", block_rows),
        _write_csv(checkpoint_root / "rans_gate_table.csv", gate_rows),
    ):
        if path is not None:
            evidence.append(path)
    evidence.extend(_plot_diagnostics(checkpoint_root, frame, residuals, window, gate_rows))
    diagnostic_path = checkpoint_root / "rans_diagnostic.json"
    write_json_atomic(
        diagnostic_path,
        {
            **diagnostic,
            "mesh_id": mesh_id,
            "generated_at": utc_stamp(),
            "source_checkpoint_status": checkpoint.get("status"),
            "force_samples": int(len(frame)),
            "residual_history": residual_history,
        },
    )
    evidence.append(diagnostic_path)

    total_wall = float(checkpoint.get("total_wall_time") or 0.0)
    iterations = int(checkpoint.get("iterations_completed") or frame["Iteration"].max())
    execution = {
        "iterations": iterations,
        "total_wall_seconds": total_wall or None,
        "seconds_per_iteration": total_wall / iterations if total_wall and iterations else None,
        "seconds_per_10000_iterations": (
            total_wall * 10000.0 / iterations if total_wall and iterations else None
        ),
        "solver_logs": logs,
        "residual_history": residual_history,
        "generated_at": utc_stamp(),
    }
    execution_path = checkpoint_root / "rans_execution_cost.json"
    write_json_atomic(execution_path, execution)
    evidence.append(execution_path)
    execution_plot = _plot_execution_cost(
        checkpoint_root,
        iterations=iterations,
        total_wall_seconds=total_wall,
    )
    if execution_plot is not None:
        evidence.append(execution_plot)

    final_rows = [
        row
        for row in window_rows
        if row.get("window_fraction") == "comparison"
    ]
    report_lines = [
        f"# RANS review: {mesh_id}",
        "",
        f"- Automatic gate: `{diagnostic['status']}`",
        f"- Original checkpoint status: `{checkpoint.get('status')}`",
        f"- Real force samples: `{len(frame)}`",
        f"- Review window: `{window}` samples (`max(1000, final 10%)`, limited by available data)",
        f"- Topology: `{checkpoint.get('topology')}`",
        "",
        "## Final Window Comparison",
        "",
        "| Coefficient | Previous mean | Final mean | Change [%] | Final std |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in final_rows:
        report_lines.append(
            f"| {row['coefficient']} | {row['previous_mean']:.8g} | "
            f"{row['mean']:.8g} | {row['mean_change_percent']:.5g} | "
            f"{row['standard_deviation']:.5g} |"
        )
    report_lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The automatic gate remains authoritative and is never rewritten by a manual review.",
            "A manual stationary acceptance may be used in RANS mesh convergence with explicit provenance.",
            "An initialization-only acceptance may initialize URANS/PIMPLE but is excluded from RANS GCI.",
        ]
    )
    if checkpoint.get("topology") == "open":
        report_lines.extend(
            [
                "",
                "Persistent SIMPLE force oscillations in the open cavity can indicate an inherently unsteady flow.",
                "Use stationary acceptance only with an explicit physical justification.",
            ]
        )
    report_path = checkpoint_root / "rans_review_report.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    evidence.append(report_path)

    previous_gate = dict(manifest.get("automatic_gate") or {})
    history = list(manifest.get("automatic_gate_history") or [])
    if previous_gate and previous_gate.get("status") != diagnostic.get("status"):
        history.append(
            {
                "status": previous_gate.get("status"),
                "replaced_at": utc_stamp(),
                "reason": "diagnostic_recalculated_under_schema_2",
            }
        )
    manifest["automatic_gate"] = {
        **diagnostic,
        "evaluated_at": utc_stamp(),
        "diagnostic": str(diagnostic_path),
    }
    manifest["automatic_gate_history"] = history
    manual_status = str(manifest.get("review", {}).get("status") or "")
    automatic_accepted = diagnostic["status"] in {
        RANS_AUTO_CONVERGED_STRICT,
        RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    }
    manifest["allowed_uses"] = {
        "rans_spatial_convergence": (
            automatic_accepted
            or manual_status == RANS_USER_ACCEPTED_STATISTICALLY_STEADY
        ) and manual_status != RANS_REJECTED,
        "urans_initialization": (
            automatic_accepted
            or manual_status
            in {
                RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
                RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
            }
        ) and manual_status != RANS_REJECTED,
    }
    manifest["postprocess"] = {
        "status": "GENERATED",
        "generated_at": utc_stamp(),
        "evidence": [str(path) for path in evidence if path.is_file() and path.stat().st_size > 0],
        "source_samples": int(len(frame)),
        "force_sources": force_sources,
        "review_window_samples": window,
        "sensitivity_fractions": [0.05, 0.10, 0.20],
    }
    manifest["updated_at"] = utc_stamp()
    _synchronize_state_fields(manifest, checkpoint)
    write_json_atomic(_manifest_path(checkpoint_root), manifest)
    write_postprocess_manifest(
        checkpoint_root,
        run_id=str(manifest.get("run_id") or mesh_id),
        mode="RANS",
        products=evidence,
        metadata={
            "review_manifest": str(_manifest_path(checkpoint_root)),
            "source_samples": int(len(frame)),
            "residual_history": residual_history,
        },
        regeneration_commands={
            "scalar_histories": [sys.executable, *sys.argv],
            "statistics_convergence": [sys.executable, *sys.argv],
            "technical_files": [sys.executable, *sys.argv],
        },
    )
    _persist_review_projection(project_root, mesh_id, manifest)
    return manifest


def _persist_review_projection(
    project_root: Path,
    mesh_id: str,
    manifest: dict[str, Any],
) -> None:
    """Project review metadata into live registries without changing the gate."""
    project_root = Path(project_root).resolve()
    checkpoint_root = _checkpoint_root(project_root, mesh_id)
    checkpoint_path = checkpoint_root / "checkpoint_manifest.json"
    checkpoint = read_json(checkpoint_path, {}) or {}
    if checkpoint:
        checkpoint.update(
            review_status=manifest.get("review_status"),
            allowed_uses=dict(manifest.get("allowed_uses") or {}),
            automatic_gate_status=manifest.get("automatic_gate_status"),
            review_updated_at=manifest.get("updated_at") or utc_stamp(),
        )
        write_json_atomic(checkpoint_path, checkpoint)
        upsert_execution(
            project_root,
            {
                "run_id": checkpoint.get("checkpoint_id")
                or f"{mesh_id}_simple",
                "mode": "RANS",
                "topology": checkpoint.get("topology"),
                "mesh_level": checkpoint.get("mesh_level"),
                "mesh_id": mesh_id,
                "stage": "SIMPLE",
                "status": checkpoint.get("status"),
                "case_path": checkpoint.get("case"),
                "iteration": checkpoint.get("iterations_completed", 0),
                "updated_at": utc_stamp(),
            },
            activate=False,
        )
    write_json_atomic(
        active_workspace_root(project_root) / "cache/review_cache_invalidation.json",
        {
            "mesh_id": mesh_id,
            "review_status": manifest.get("review_status"),
            "allowed_uses": dict(manifest.get("allowed_uses") or {}),
            "invalidated_at": utc_stamp(),
        },
    )


def set_review(
    project_root: Path,
    mesh_id: str,
    status: str,
    *,
    reason: str | None = None,
    reviewed_by: str = "user",
    confirmation: bool = True,
    decision_source: str = "EXPLICIT_USER_ACTION",
) -> dict[str, Any]:
    if status not in MANUAL_REVIEW_STATUSES:
        raise ValueError(f"Unsupported manual RANS review status: {status}")
    if not confirmation:
        raise ValueError("Explicit review confirmation is required")
    note = str(reason or "").strip() or None
    manifest = review_manifest(project_root, mesh_id)
    if manifest.get("postprocess", {}).get("status") != "GENERATED":
        raise RuntimeError("Generate the RANS review diagnostics before approval")
    history = list(manifest["review"].get("history") or [])
    history.append(
        {
            "previous_status": manifest["review"].get("status"),
            "new_status": status,
            "review_note": note,
            "reason": note,
            "confirmation": True,
            "reviewed_by": reviewed_by,
            "decision_source": decision_source,
            "reviewed_at": utc_stamp(),
        }
    )
    manifest["review"] = {
        **manifest["review"],
        "status": status,
        "reviewed_at": utc_stamp(),
        "reviewed_by": reviewed_by,
        "decision_source": decision_source,
        "review_source": decision_source,
        "review_note": note,
        "reason": note,
        "confirmation": True,
        "history": history,
    }
    manifest["allowed_uses"] = {
        "rans_spatial_convergence": (
            status == RANS_USER_ACCEPTED_STATISTICALLY_STEADY
        ),
        "urans_initialization": status
        in {
            RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
            RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY,
        },
    }
    manifest["updated_at"] = utc_stamp()
    checkpoint = read_json(
        _checkpoint_root(Path(project_root).resolve(), mesh_id)
        / "checkpoint_manifest.json",
        {},
    ) or {}
    _synchronize_state_fields(manifest, checkpoint)
    write_json_atomic(
        _manifest_path(_checkpoint_root(Path(project_root).resolve(), mesh_id)),
        manifest,
    )
    _persist_review_projection(project_root, mesh_id, manifest)
    if manifest["allowed_uses"]["urans_initialization"]:
        # Acceptance is the user decision that makes the restart eligible.
        # Materialize the immutable snapshot here so URANS selectors update
        # immediately and persistently, without a second approval-like action.
        from ramair_2d_rans_checkpoint_batch import create_reviewed_checkpoint

        try:
            manifest = create_reviewed_checkpoint(Path(project_root), mesh_id)
        except RuntimeError as exc:
            if "no saved real RANS state" not in str(exc):
                raise
            manifest["checkpoint"] = {
                **dict(manifest.get("checkpoint") or {}),
                "status": "NOT_AVAILABLE_NO_SAVED_STATE",
                "error": str(exc),
            }
            write_json_atomic(
                _manifest_path(
                    _checkpoint_root(Path(project_root).resolve(), mesh_id)
                ),
                manifest,
            )
        _persist_review_projection(project_root, mesh_id, manifest)
    return manifest


def accept_current_six_bases(
    project_root: Path,
    *,
    confirmation: bool,
    alpha_deg: float | None = None,
) -> dict[str, Any]:
    """Accept every evidence-backed canonical base under one explicit action."""
    if not confirmation:
        raise ValueError("Explicit confirmation is required for batch acceptance")
    from ramair_2d_rans_checkpoint_batch import (
        REQUIRED_BASE_FIELDS,
        _field_path,
        _latest_restart_state,
        create_reviewed_checkpoint,
        mesh_angle_id,
    )
    from ramair_2d_study_registry import MESH_IDS, load_study

    source = "EXPLICIT_USER_BATCH_INSTRUCTION_2026-08-04"
    accepted: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    study = load_study(project_root)
    selected_alpha = None if alpha_deg is None else float(alpha_deg)
    for base_mesh_id in MESH_IDS:
        mesh_id = mesh_angle_id(study, base_mesh_id, selected_alpha)
        checkpoint_root = _checkpoint_root(Path(project_root).resolve(), mesh_id)
        checkpoint = read_json(
            checkpoint_root / "checkpoint_manifest.json", {}
        ) or {}
        state = _latest_restart_state(checkpoint_root / "case")
        if not checkpoint or state is None:
            exceptions.append({
                "mesh_id": mesh_id,
                "status": "INSUFFICIENT_REAL_EVIDENCE",
                "reason": "missing checkpoint manifest or positive SIMPLE state",
            })
            continue
        iteration, state_path = state
        missing = [
            name for name in REQUIRED_BASE_FIELDS
            if _field_path(state_path, name) is None
        ]
        review = review_manifest(project_root, mesh_id)
        has_diagnostics = bool(
            review.get("postprocess", {}).get("status") == "GENERATED"
            or (checkpoint_root / "rans_diagnostic.json").is_file()
            or (checkpoint_root / "force_coeffs.csv").is_file()
        )
        if missing or not has_diagnostics:
            exceptions.append({
                "mesh_id": mesh_id,
                "status": "INSUFFICIENT_REAL_EVIDENCE",
                "source_iteration": iteration,
                "missing_fields": missing,
                "diagnostics_available": has_diagnostics,
            })
            continue
        previous_gate = review.get("automatic_gate_status") or (
            review.get("automatic_gate") or {}
        ).get("status")
        updated = set_review(
            project_root,
            mesh_id,
            RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
            reviewed_by="explicit_user_batch",
            confirmation=True,
            decision_source=source,
        )
        updated = create_reviewed_checkpoint(
            Path(project_root), mesh_id, force_new=True
        )
        accepted.append({
            "mesh_id": mesh_id,
            "status": updated.get("review_status"),
            "review_source": source,
            "source_iteration": iteration,
            "automatic_gate_status_before": previous_gate,
            "automatic_gate_status_after": updated.get(
                "automatic_gate_status"
            ),
            "allowed_uses": dict(updated.get("allowed_uses") or {}),
            "checkpoint": dict(updated.get("checkpoint") or {}),
        })
    report = {
        "schema_version": 1,
        "status": "COMPLETED" if not exceptions else "COMPLETED_WITH_EXCEPTIONS",
        "review_source": source,
        "alpha_deg": selected_alpha,
        "accepted": accepted,
        "exceptions": exceptions,
        "accepted_count": len(accepted),
        "exception_count": len(exceptions),
        "automatic_gates_preserved": all(
            row.get("automatic_gate_status_before")
            == row.get("automatic_gate_status_after")
            for row in accepted
        ),
        "completed_at": utc_stamp(),
    }
    output = (
        active_workspace_root(Path(project_root).resolve())
        / "reports"
        / (
            f"rans_six_base_batch_acceptance_alpha_{selected_alpha:g}.json"
            if selected_alpha is not None
            else "rans_six_base_batch_acceptance_20260804.json"
        )
    )
    write_json_atomic(output, report)
    return {**report, "report": str(output)}


def revoke_review(
    project_root: Path,
    mesh_id: str,
    *,
    reason: str | None = None,
    reviewed_by: str = "user",
    confirmation: bool = True,
) -> dict[str, Any]:
    if not confirmation:
        raise ValueError("Explicit revocation confirmation is required")
    note = str(reason or "").strip() or None
    manifest = review_manifest(project_root, mesh_id)
    current = manifest["review"].get("status")
    history = list(manifest["review"].get("history") or [])
    history.append(
        {
            "previous_status": current,
            "new_status": RANS_NOT_REVIEWED,
            "review_note": note,
            "reason": note,
            "confirmation": True,
            "reviewed_by": reviewed_by,
            "reviewed_at": utc_stamp(),
            "action": "REVOKED",
        }
    )
    manifest["review"] = {
        **manifest["review"],
        "status": RANS_NOT_REVIEWED,
        "reviewed_at": utc_stamp(),
        "reviewed_by": reviewed_by,
        "review_note": note,
        "reason": note,
        "confirmation": True,
        "history": history,
    }
    automatic_accepted = str(
        manifest.get("automatic_gate", {}).get("status") or ""
    ) in {
        RANS_AUTO_CONVERGED_STRICT,
        RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    }
    manifest["allowed_uses"] = {
        "rans_spatial_convergence": automatic_accepted,
        "urans_initialization": automatic_accepted,
    }
    manifest["updated_at"] = utc_stamp()
    checkpoint = read_json(
        _checkpoint_root(Path(project_root).resolve(), mesh_id)
        / "checkpoint_manifest.json",
        {},
    ) or {}
    _synchronize_state_fields(manifest, checkpoint)
    write_json_atomic(
        _manifest_path(_checkpoint_root(Path(project_root).resolve(), mesh_id)),
        manifest,
    )
    _persist_review_projection(project_root, mesh_id, manifest)
    return manifest


def review_table(project_root: Path) -> list[dict[str, Any]]:
    project_root = Path(project_root).resolve()
    if any(
        (_checkpoint_root(project_root, mesh_id) / "checkpoint_manifest.json").is_file()
        and not _manifest_path(_checkpoint_root(project_root, mesh_id)).is_file()
        for mesh_id in MESH_IDS
    ):
        migrate_existing_bases(project_root)
    rows: list[dict[str, Any]] = []
    for mesh_id in MESH_IDS:
        manifest = read_json(
            _manifest_path(_checkpoint_root(project_root, mesh_id)),
            {},
        ) or {}
        if not manifest:
            continue
        rows.append(
            {
                "mesh_id": mesh_id,
                "topology": manifest.get("topology"),
                "mesh_level": manifest.get("mesh_level"),
                "execution_status": manifest.get("execution_status"),
                "automatic_gate": manifest.get("automatic_gate_status"),
                "automatic_gate_status": manifest.get("automatic_gate_status"),
                "review_status": manifest.get("review_status"),
                "postprocess_status": manifest.get("postprocess", {}).get("status"),
                "checkpoint_status": manifest.get("checkpoint", {}).get("status"),
                "allowed_uses": dict(manifest.get("allowed_uses") or {}),
                "rans_spatial": bool(
                    manifest.get("allowed_uses", {}).get(
                        "rans_spatial_convergence"
                    )
                ),
                "urans_initialization": bool(
                    manifest.get("allowed_uses", {}).get(
                        "urans_initialization"
                    )
                ),
                "reviewed_at": manifest.get("review", {}).get("reviewed_at"),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("migrate")
    sub.add_parser("status")
    diagnostic = sub.add_parser("diagnose")
    diagnostic.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    review = sub.add_parser("review")
    review.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    review.add_argument(
        "--status",
        choices=sorted(MANUAL_REVIEW_STATUSES),
        required=True,
    )
    review.add_argument("--reason", required=True)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--mesh-id", choices=MESH_IDS, required=True)
    revoke.add_argument("--reason", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if args.action == "migrate":
        result: Any = migrate_existing_bases(root)
    elif args.action == "status":
        result = review_table(root)
    elif args.action == "diagnose":
        result = generate_review_diagnostics(root, args.mesh_id)
    elif args.action == "review":
        result = set_review(
            root,
            args.mesh_id,
            args.status,
            reason=args.reason,
        )
    else:
        result = revoke_review(root, args.mesh_id, reason=args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
