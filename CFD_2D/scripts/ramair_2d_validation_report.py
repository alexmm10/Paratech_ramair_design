#!/usr/bin/env python3
"""Per-run and study reports for the validation convergence laboratory."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from openfoam_history import read_force_coefficient_history
from ramair_2d_convergence_analysis import (
    compare_pimple_outer_correctors,
    generalized_gci,
    signal_summary,
    stationarity_blocks,
    welch_spectrum,
)
from ramair_2d_postprocess import parse_solver_log
from ramair_2d_rans_review import (
    RANS_AUTO_CONVERGED_STRICT,
    RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
    RANS_USER_ACCEPTED_STATISTICALLY_STEADY,
    generate_review_diagnostics,
)
from ramair_2d_rans_checkpoint_batch import checkpoint_table
from ramair_scientific_plot_style import save_scientific_figure
from ramair_2d_study_registry import (
    active_workspace_root,
    load_study,
    read_json,
    results_study_root,
    utc_stamp,
    write_json_atomic,
)


def _write_csv_nonempty(path: Path, records: list[dict[str, Any]]) -> Path | None:
    if not records:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(key for row in records for key in row))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)
    return path


def _force_columns(frame: pd.DataFrame) -> tuple[str, str, str, str]:
    normalized = {str(column).lower(): str(column) for column in frame.columns}
    time_name = normalized.get("time") or normalized.get("iteration")
    cl_name = normalized.get("cl")
    cd_name = normalized.get("cd")
    cm_name = normalized.get("cm")
    if not all((time_name, cl_name, cd_name, cm_name)):
        raise ValueError(
            "force_coeffs.csv must contain Time/Iteration, Cl, Cd and Cm columns"
        )
    return time_name, cl_name, cd_name, cm_name


def _sampling_frame(frame: pd.DataFrame, metadata: dict[str, Any]) -> pd.DataFrame:
    time_name, *_ = _force_columns(frame)
    start = metadata.get("sampling_start_s")
    if start is None:
        return frame.iloc[math.floor(0.6 * len(frame)) :].copy()
    selected = frame[pd.to_numeric(frame[time_name], errors="coerce") >= float(start)]
    return selected.copy()


def _solver_histories(run_root: Path, case: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Reuse the project OpenFOAM parser over the active case and archived stages."""
    roots = [case]
    stage_logs = run_root / "logs"
    if stage_logs.is_dir():
        roots.extend(path for path in sorted(stage_logs.glob("stage_*")) if path.is_dir())
    residual_frames: list[pd.DataFrame] = []
    courant_frames: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    for source_root in roots:
        residuals, courant, metadata = parse_solver_log(source_root)
        if metadata.get("solver_log_found"):
            sources.append(metadata)
        if not residuals.empty:
            residuals = residuals.copy()
            residuals["source_stage"] = source_root.name
            residual_frames.append(residuals)
        if not courant.empty:
            courant = courant.copy()
            courant["source_stage"] = source_root.name
            courant_frames.append(courant)
    residuals = (
        pd.concat(residual_frames, ignore_index=True)
        if residual_frames else pd.DataFrame()
    )
    courant = (
        pd.concat(courant_frames, ignore_index=True)
        if courant_frames else pd.DataFrame()
    )
    execution = [
        float(source["clock_time_s"])
        for source in sources
        if source.get("clock_time_s") is not None
    ]
    return residuals, courant, {
        "sources": sources,
        "clock_time_s": float(sum(execution)) if execution else None,
    }


def analyze_checkpoint(checkpoint_root: Path) -> dict[str, Any]:
    """Analyze one real SIMPLE checkpoint without treating it as URANS time."""
    checkpoint_root = Path(checkpoint_root)
    project_root = next(
        (
            parent
            for parent in checkpoint_root.parents
            if (parent / "CFD_2D").is_dir()
        ),
        None,
    )
    if project_root is not None:
        review = generate_review_diagnostics(project_root, checkpoint_root.name)
        summary = {
            "status": (
                "RANS_REVIEW_DIAGNOSTICS_GENERATED"
                if review.get("postprocess", {}).get("status") == "GENERATED"
                else "RANS_ANALYSIS_PENDING"
            ),
            "mesh_id": checkpoint_root.name,
            "automatic_gate": review.get("automatic_gate"),
            "review": review.get("review"),
            "postprocess": review.get("postprocess"),
            "generated_at": utc_stamp(),
        }
        write_json_atomic(checkpoint_root / "checkpoint_summary.json", summary)
        return summary

    # Standalone checkpoint fixtures and archived exports do not have enough
    # project context for the review manifest. Preserve their established
    # scalar-only analysis path.
    manifest = read_json(checkpoint_root / "checkpoint_manifest.json", {}) or {}
    topology = str(manifest.get("topology") or "unknown")
    if manifest.get("status") != "CHECKPOINT_READY":
        status = {
            "status": (
                "STEADY_RANS_NOT_ESTABLISHED"
                if topology == "open"
                else "RANS_CHECKPOINT_NOT_READY"
            ),
            "reason": f"checkpoint status is {manifest.get('status', 'NOT_PREPARED')}",
            "mesh_id": manifest.get("mesh_id", checkpoint_root.name),
            "generated_at": utc_stamp(),
        }
        write_json_atomic(checkpoint_root / "checkpoint_summary.json", status)
        return status

    history_root = checkpoint_root / "case/steadyInitialization/history"
    archives = sorted(
        (path for path in history_root.glob("run_*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    ) if history_root.is_dir() else []
    source = archives[-1] if archives else checkpoint_root / "case"
    records, force_sources = read_force_coefficient_history(
        source,
        include_processor0=True,
    )
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        status = {
            "status": (
                "STEADY_RANS_NOT_ESTABLISHED"
                if topology == "open"
                else "RANS_ANALYSIS_PENDING"
            ),
            "reason": "NO_REAL_SIMPLE_FORCE_COEFFICIENT_HISTORY",
            "mesh_id": manifest.get("mesh_id", checkpoint_root.name),
            "source": str(source),
            "generated_at": utc_stamp(),
        }
        write_json_atomic(checkpoint_root / "checkpoint_summary.json", status)
        return status

    time_name, cl_name, cd_name, cm_name = _force_columns(frame)
    for name in (time_name, cl_name, cd_name, cm_name):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=[time_name, cl_name, cd_name, cm_name])
    window = frame.iloc[math.floor(0.6 * len(frame)) :].copy()
    if len(window) < 16:
        status = {
            "status": "STEADY_RANS_NOT_ESTABLISHED",
            "reason": "INSUFFICIENT_FINAL_SIMPLE_WINDOW",
            "available_samples": int(len(window)),
            "mesh_id": manifest.get("mesh_id", checkpoint_root.name),
        }
        write_json_atomic(checkpoint_root / "checkpoint_summary.json", status)
        return status

    metrics = {
        label: signal_summary(window[column].to_numpy())
        for column, label in ((cl_name, "CL"), (cd_name, "CD"), (cm_name, "CM"))
    }
    ratio = window[cl_name] / window[cd_name].replace(0.0, np.nan)
    ratio = ratio[np.isfinite(ratio)]
    if len(ratio) >= 2:
        metrics["L_over_D"] = signal_summary(ratio.to_numpy())
    _write_csv_nonempty(
        checkpoint_root / "force_coeffs.csv",
        frame[[time_name, cl_name, cd_name, cm_name]].to_dict(orient="records"),
    )
    residuals, courant, solver = parse_solver_log(source)
    if not residuals.empty:
        _write_csv_nonempty(
            checkpoint_root / "residuals.csv",
            residuals.to_dict(orient="records"),
        )
        continuity = residuals[residuals["field"] == "continuity_global"]
        if not continuity.empty:
            _write_csv_nonempty(
                checkpoint_root / "continuity.csv",
                continuity.to_dict(orient="records"),
            )
    if not courant.empty:
        _write_csv_nonempty(
            checkpoint_root / "courant.csv",
            courant.to_dict(orient="records"),
        )
    dominant_modes: dict[str, dict[str, Any]] = {}
    for label, spectrum in spectra.items():
        dominant_st = spectrum["dominant_strouhal"]
        wave_number = None
        if dominant_st is not None and math.isfinite(float(dominant_st)):
            if abs(float(dominant_st)) > 1.0e-15:
                wave_number = 1.0 / float(dominant_st)
        dominant_modes[label] = {
            "frequency_hz": spectrum["dominant_frequency_hz"],
            "strouhal": dominant_st,
            "wave_number": wave_number,
            "peak_amplitude": spectrum["peak_amplitude"],
        }
    summary = {
        "status": "RANS_CHECKPOINT_ANALYZED",
        "mesh_id": manifest.get("mesh_id", checkpoint_root.name),
        "topology": topology,
        "mesh_level": manifest.get("mesh_level"),
        "mesh_hash": manifest.get("mesh_hash"),
        "source": str(source),
        "force_history_sources": force_sources,
        "final_window_fraction": 0.4,
        "final_window_samples": int(len(window)),
        "metrics": metrics,
        "solver": solver,
        "steady_time_semantics": "SIMPLE iteration counter; not physical time",
        "generated_at": utc_stamp(),
    }
    write_json_atomic(checkpoint_root / "checkpoint_summary.json", summary)
    return summary


def _plot_run_products(
    output: Path,
    frame: pd.DataFrame,
    sampling: pd.DataFrame,
    spectra: dict[str, dict[str, Any]],
) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    time_name, cl_name, cd_name, cm_name = _force_columns(frame)
    output.mkdir(parents=True, exist_ok=True)
    products: list[str] = []
    for column, label in (
        (cl_name, "CL"),
        (cd_name, "CD"),
        (cm_name, "CM"),
    ):
        figure, axis = plt.subplots(figsize=(8.0, 4.4))
        axis.plot(frame[time_name], frame[column], lw=0.8, alpha=0.8)
        axis.axvspan(
            float(sampling[time_name].iloc[0]),
            float(sampling[time_name].iloc[-1]),
            color="#d7efe3",
            alpha=0.5,
            label="accepted sampling window",
        )
        axis.set(xlabel="Physical time [s]", ylabel=label, title=f"{label} history")
        axis.grid(alpha=0.25)
        axis.legend()
        path = output / f"{label.lower()}_history.png"
        figure.tight_layout()
        save_scientific_figure(
            figure,
            path,
            data=frame[[time_name, column]],
            metadata={"source": "URANS force coefficient history", "filters": ["finite samples"]},
        )
        products.append(str(path))

    ratio = frame[cl_name] / frame[cd_name].replace(0.0, np.nan)
    figure, axis = plt.subplots(figsize=(8.0, 4.4))
    axis.plot(frame[time_name], ratio, lw=0.8)
    axis.set(xlabel="Physical time [s]", ylabel="CL/CD", title="Aerodynamic efficiency")
    axis.grid(alpha=0.25)
    path = output / "lift_to_drag_history.png"
    figure.tight_layout()
    save_scientific_figure(
        figure,
        path,
        data=[
            {"physical_time_s": time_value, "CL_over_CD": ratio_value}
            for time_value, ratio_value in zip(frame[time_name], ratio)
        ],
        metadata={"source": "URANS force coefficient history", "transformation": "CL/CD"},
    )
    products.append(str(path))

    figure, axis = plt.subplots(figsize=(8.0, 4.8))
    for label, spectrum in spectra.items():
        frequency = np.asarray(spectrum["strouhal"], dtype=float)
        density = np.asarray(spectrum["psd"], dtype=float)
        mask = frequency > 0.0
        axis.loglog(frequency[mask], density[mask], label=label)
    axis.set(xlabel="Strouhal number", ylabel="PSD", title="Welch spectra")
    axis.grid(alpha=0.25, which="both")
    axis.legend()
    path = output / "force_psd.png"
    figure.tight_layout()
    spectrum_rows = [
        {"signal": label, "strouhal": st_value, "psd": psd_value}
        for label, spectrum in spectra.items()
        for st_value, psd_value in zip(spectrum["strouhal"], spectrum["psd"])
    ]
    save_scientific_figure(
        figure,
        path,
        data=spectrum_rows,
        metadata={"source": "URANS sampling window", "transformation": "Welch power spectral density"},
    )
    products.append(str(path))
    return products


def analyze_run(run_root: Path) -> dict[str, Any]:
    run_root = Path(run_root)
    metadata_path = run_root / "case_metadata.json"
    metadata = read_json(metadata_path, {}) or {}
    case = run_root / "case"
    normalized_path = run_root / "force_coeffs.csv"
    force_sources: list[str] = []
    if normalized_path.is_file():
        frame = pd.read_csv(normalized_path)
        force_sources = [str(normalized_path)]
    else:
        records, force_sources = read_force_coefficient_history(
            case,
            include_processor0=True,
        )
        if records:
            _write_csv_nonempty(normalized_path, records)
            frame = pd.DataFrame.from_records(records)
        else:
            frame = pd.DataFrame()
    if frame.empty:
        status = {
            "status": "ANALYSIS_PENDING",
            "reason": "NO_REAL_FORCE_COEFFICIENT_HISTORY",
            "run_root": str(run_root),
            "generated_at": utc_stamp(),
        }
        write_json_atomic(run_root / "case_summary.json", status)
        return status
    try:
        time_name, cl_name, cd_name, cm_name = _force_columns(frame)
    except ValueError as exc:
        status = {
            "status": "NOT_STATISTICALLY_ESTABLISHED",
            "reason": "INCOMPLETE_REAL_FORCE_COEFFICIENT_HISTORY",
            "details": str(exc),
            "sources": force_sources,
            "generated_at": utc_stamp(),
        }
        write_json_atomic(run_root / "case_summary.json", status)
        return status
    for name in (time_name, cl_name, cd_name, cm_name):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=[time_name, cl_name, cd_name, cm_name])
    _write_csv_nonempty(
        run_root / "time_history.csv",
        frame[[time_name, cl_name, cd_name, cm_name]].to_dict(orient="records"),
    )
    sampling = _sampling_frame(frame, metadata)
    if len(sampling) < 16:
        status = {
            "status": "NOT_STATISTICALLY_ESTABLISHED",
            "reason": "INSUFFICIENT_SAMPLING_VALUES",
            "available_samples": int(len(sampling)),
        }
        write_json_atomic(run_root / "case_summary.json", status)
        return status

    condition = metadata.get("operating_condition") or {}
    chord = float(condition.get("chord_m", metadata.get("chord_m", 1.0)))
    velocity = float(
        condition.get("velocity_m_s", metadata.get("U_inf_m_s", 1.0))
    )
    metrics: dict[str, Any] = {}
    spectra: dict[str, dict[str, Any]] = {}
    for column, label in ((cl_name, "CL"), (cd_name, "CD"), (cm_name, "CM")):
        metrics[label] = signal_summary(sampling[column].to_numpy())
        metrics[label]["stationarity"] = stationarity_blocks(
            sampling[column].to_numpy()
        )
        spectra[label] = welch_spectrum(
            sampling[time_name].to_numpy(),
            sampling[column].to_numpy(),
            chord_m=chord,
            velocity_m_s=velocity,
        )
        psd_records = [
            {
                "frequency_hz": frequency,
                "strouhal": strouhal,
                "psd": density,
            }
            for frequency, strouhal, density in zip(
                spectra[label]["frequency_hz"],
                spectra[label]["strouhal"],
                spectra[label]["psd"],
            )
        ]
        _write_csv_nonempty(run_root / f"psd_{label}.csv", psd_records)

    ratio = sampling[cl_name] / sampling[cd_name].replace(0.0, np.nan)
    ratio = ratio[np.isfinite(ratio)]
    if len(ratio) >= 2:
        metrics["L_over_D"] = signal_summary(ratio.to_numpy())
    summary = {
        "status": "COMPLETED",
        "generated_at": utc_stamp(),
        "sampling_start_s": float(sampling[time_name].iloc[0]),
        "sampling_end_s": float(sampling[time_name].iloc[-1]),
        "sampling_samples": int(len(sampling)),
        "metrics": metrics,
        "dominant_modes": dominant_modes,
        "stationarity_passed": all(
            bool(metrics[label]["stationarity"]["passed"])
            for label in ("CL", "CD", "CM")
        ),
        "limitations": [
            "Surface, probe and Courant products are included only when real source files exist."
        ],
        "force_history_sources": force_sources,
    }
    residuals, courant, solver_history = _solver_histories(run_root, case)
    if not residuals.empty:
        _write_csv_nonempty(
            run_root / "residuals.csv",
            residuals.to_dict(orient="records"),
        )
        continuity = residuals[residuals["field"] == "continuity_global"]
        if not continuity.empty:
            _write_csv_nonempty(
                run_root / "continuity.csv",
                continuity.to_dict(orient="records"),
            )
    if not courant.empty:
        _write_csv_nonempty(
            run_root / "courant.csv",
            courant.to_dict(orient="records"),
        )
        finite_max = pd.to_numeric(courant["Co_max"], errors="coerce").dropna()
        finite_mean = pd.to_numeric(courant["Co_mean"], errors="coerce").dropna()
        if not finite_max.empty:
            summary["courant_history"] = {
                "samples": int(len(courant)),
                "max": float(finite_max.max()),
                "mean_of_means": (
                    float(finite_mean.mean()) if not finite_mean.empty else None
                ),
                "sources": solver_history["sources"],
            }
    if solver_history["clock_time_s"] is not None:
        summary["clock_time_s"] = solver_history["clock_time_s"]
        completed_steps = max(
            1,
            int(metadata.get("steps_completed") or metadata.get("steps_planned") or 1),
        )
        summary["cpu_seconds_per_step"] = (
            float(solver_history["clock_time_s"]) / completed_steps
        )
    products = _plot_run_products(run_root / "plots", frame, sampling, spectra)
    summary["plots"] = products
    write_json_atomic(run_root / "stationarity.json", {
        label: metrics[label]["stationarity"] for label in ("CL", "CD", "CM")
    })
    write_json_atomic(run_root / "dominant_modes.json", summary["dominant_modes"])
    write_json_atomic(run_root / "case_summary.json", summary)
    write_json_atomic(run_root / "acceptance.json", {
        "status": "NOT_EVALUATED_NO_FINER_REFERENCE",
        "accepted": False,
        "reason": (
            "Per-run analysis is complete, but scientific acceptance requires "
            "the next finer deltaT and a compatible finer mesh."
        ),
        "checkMesh_is_not_acceptance": True,
        "stationarity_passed": summary["stationarity_passed"],
    })
    return summary


def _flatten_completed_runs(study: dict[str, Any]) -> list[dict[str, Any]]:
    active = Path(study["study_manifest"]["active_workspace"])
    rows: list[dict[str, Any]] = []
    for run in study["run_matrix"].get("runs", []):
        run_root = active / "runs" / run["topology"] / run["mesh_level"] / run["run_id"]
        summary = read_json(run_root / "case_summary.json", {}) or {}
        if summary.get("status") != "COMPLETED":
            continue
        metrics = summary.get("metrics") or {}
        modes = summary.get("dominant_modes") or {}
        rows.append(
            {
                **{key: run.get(key) for key in (
                    "run_id", "topology", "mesh_level", "cell_count", "dt_s",
                    "dt_star", "nOuterCorrectors", "status",
                )},
                "mean_CL": (metrics.get("CL") or {}).get("mean"),
                "mean_CD": (metrics.get("CD") or {}).get("mean"),
                "mean_CM": (metrics.get("CM") or {}).get("mean"),
                "rms_CL": (metrics.get("CL") or {}).get("rms"),
                "dominant_St": (modes.get("CL") or {}).get("strouhal"),
                "dominant_W": (modes.get("CL") or {}).get("wave_number"),
                "psd_peak_amplitude": (modes.get("CL") or {}).get("peak_amplitude"),
                "stationarity_passed": summary.get("stationarity_passed"),
                "courant_max": (summary.get("courant_history") or {}).get("max"),
                "courant_mean": (summary.get("courant_history") or {}).get(
                    "mean_of_means"
                ),
                "cpu_seconds_per_step": summary.get("cpu_seconds_per_step"),
            }
        )
    return rows


def _gci_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for topology in ("closed", "open"):
        topology_rows = [
            row for row in rows
            if row["topology"] == topology
            and row.get("status") in {"ACCEPTED", "ACCEPTED_WITH_WARNINGS"}
        ]
        common_dt = sorted(
            {
                row["dt_s"] for row in topology_rows
                if {candidate["mesh_level"] for candidate in topology_rows if candidate["dt_s"] == row["dt_s"]}
                == {"coarse", "medium", "fine"}
            }
        )
        for dt_s in common_dt:
            by_level = {
                row["mesh_level"]: row
                for row in topology_rows
                if row["dt_s"] == dt_s
            }
            for metric in ("mean_CL", "mean_CD", "mean_CM"):
                if any(by_level[level].get(metric) is None for level in by_level):
                    continue
                report = generalized_gci(
                    coarse_value=float(by_level["coarse"][metric]),
                    medium_value=float(by_level["medium"][metric]),
                    fine_value=float(by_level["fine"][metric]),
                    coarse_cells=int(by_level["coarse"]["cell_count"]),
                    medium_cells=int(by_level["medium"]["cell_count"]),
                    fine_cells=int(by_level["fine"]["cell_count"]),
                )
                output.append({
                    "topology": topology,
                    "dt_s": dt_s,
                    "metric": metric,
                    **report,
                })
    return output


def _rans_review_rows(study: dict[str, Any]) -> list[dict[str, Any]]:
    active = Path(study["study_manifest"]["active_workspace"])
    rows: list[dict[str, Any]] = []
    mesh_lookup = {
        str(mesh["id"]): mesh for mesh in study["mesh_registry"].get("meshes", [])
    }
    project_root = active.parents[2]
    for candidate in checkpoint_table(project_root):
        mesh = mesh_lookup[str(candidate["base_mesh_id"])]
        checkpoint = active / "checkpoints" / str(candidate["mesh_id"])
        review = read_json(checkpoint / "rans_review_manifest.json", {}) or {}
        if not review:
            continue
        automatic = str((review.get("automatic_gate") or {}).get("status") or "")
        reviewed = str((review.get("review") or {}).get("status") or "")
        allowed = dict(review.get("allowed_uses") or {})
        included = bool(allowed.get("rans_spatial_convergence")) or (
            automatic
            in {
                RANS_AUTO_CONVERGED_STRICT,
                RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING,
            }
            or reviewed == RANS_USER_ACCEPTED_STATISTICALLY_STEADY
        )
        use = (
            "RANS_MESH_CONVERGENCE"
            if included
            else "URANS_INITIALIZATION_ONLY"
            if allowed.get("urans_initialization")
            else "EXCLUDED"
        )
        statistics_path = checkpoint / "rans_window_statistics.csv"
        statistics = (
            pd.read_csv(statistics_path)
            if statistics_path.is_file() and statistics_path.stat().st_size > 0
            else pd.DataFrame()
        )
        final = (
            statistics[statistics["window_fraction"].astype(str) == "comparison"]
            if not statistics.empty and "window_fraction" in statistics
            else pd.DataFrame()
        )
        values: dict[str, Any] = {}
        for coefficient, output_name in (
            ("Cl", "CL"),
            ("Cd", "CD"),
            ("Cm", "CM"),
            ("Cl_over_Cd", "L_over_D"),
        ):
            item = (
                final[final["coefficient"].astype(str) == coefficient]
                if not final.empty
                else pd.DataFrame()
            )
            values[f"mean_{output_name}"] = (
                float(item.iloc[-1]["mean"]) if not item.empty else None
            )
            values[f"std_{output_name}"] = (
                float(item.iloc[-1]["standard_deviation"])
                if not item.empty
                else None
            )
        wall_root = checkpoint / "rans_postprocess"
        separation = read_json(wall_root / "separation_events.json", {}) or {}
        physical_events = [
            event for event in separation.get("events", [])
            if not event.get("excluded_from_primary")
        ]
        separation_event = next(
            (event for event in physical_events if event.get("type") == "separation"),
            None,
        )
        reattachment_event = next(
            (event for event in physical_events if event.get("type") == "reattachment"),
            None,
        )
        yplus_path = wall_root / "wall_yplus_vs_xc.csv"
        yplus = pd.read_csv(yplus_path) if yplus_path.is_file() and yplus_path.stat().st_size else pd.DataFrame()
        if str(mesh["topology"]) == "open" and not yplus.empty:
            branch_column = next(
                (name for name in ("branch", "wall_branch", "surface", "patch") if name in yplus),
                None,
            )
            if branch_column:
                external = yplus[
                    yplus[branch_column].astype(str).str.lower().str.contains("external|outer")
                ]
                if not external.empty:
                    yplus = external
        cf_path = wall_root / "skin_friction_coefficient_vs_xc.csv"
        cf = pd.read_csv(cf_path) if cf_path.is_file() and cf_path.stat().st_size else pd.DataFrame()
        checkpoint_manifest = read_json(checkpoint / "checkpoint_manifest.json", {}) or {}
        checkpoint_identity = dict(checkpoint_manifest.get("checkpoint_mesh_identity") or {})
        actual_cell_count = int(
            checkpoint_identity.get("cell_count")
            or checkpoint_manifest.get("cell_count")
            or mesh["cell_count"]
        )
        timing = read_json(checkpoint / "timing_evidence.json", {}) or {}
        seconds_per_iteration = (
            timing.get("median_seconds_per_iteration")
            or checkpoint_manifest.get("median_seconds_per_iteration")
            or (checkpoint_manifest.get("timing_evidence") or {}).get("median_seconds_per_iteration")
        )
        rows.append(
            {
                "mesh_id": candidate["mesh_id"],
                "base_mesh_id": mesh["id"],
                "alpha_deg": float(candidate["alpha_deg"]),
                "topology": mesh["topology"],
                "mesh_level": mesh["level"],
                "cell_count": actual_cell_count,
                "registry_cell_count": int(mesh["cell_count"]),
                "effective_h_2d": 1.0 / math.sqrt(max(actual_cell_count, 1)),
                "seconds_per_iteration": seconds_per_iteration,
                "automatic_gate": automatic,
                "review_status": reviewed,
                "use": use,
                "included_in_rans_mesh_convergence": included,
                "manual_acceptance": (
                    reviewed == RANS_USER_ACCEPTED_STATISTICALLY_STEADY
                ),
                "review_reason": (review.get("review") or {}).get("reason"),
                "yplus_mean": (
                    float(pd.to_numeric(yplus["yPlus"], errors="coerce").mean())
                    if not yplus.empty and "yPlus" in yplus else None
                ),
                "yplus_max": (
                    float(pd.to_numeric(yplus["yPlus"], errors="coerce").max())
                    if not yplus.empty and "yPlus" in yplus else None
                ),
                "cf_min": (
                    float(pd.to_numeric(cf["Cf_filtered"], errors="coerce").min())
                    if not cf.empty and "Cf_filtered" in cf else None
                ),
                "cf_max": (
                    float(pd.to_numeric(cf["Cf_filtered"], errors="coerce").max())
                    if not cf.empty and "Cf_filtered" in cf else None
                ),
                "x_sep_over_c": separation_event.get("x_over_c") if separation_event else None,
                "x_reattach_over_c": reattachment_event.get("x_over_c") if reattachment_event else None,
                "s_sep_over_c": separation_event.get("s_over_c") if separation_event else None,
                "s_reattach_over_c": reattachment_event.get("s_over_c") if reattachment_event else None,
                "separation_confidence": (
                    separation_event.get("confidence") if separation_event else separation.get("confidence")
                ),
                "cp_product": str(wall_root / "wall_cp_vs_xc.csv") if (wall_root / "wall_cp_vs_xc.csv").is_file() else None,
                "yplus_product": str(yplus_path) if yplus_path.is_file() else None,
                "cf_product": str(cf_path) if cf_path.is_file() else None,
                **values,
            }
        )
    for topology in ("closed", "open"):
      for alpha_deg in (8.0, 16.0):
        eligible = [
            row for row in rows
            if row["topology"] == topology
            and _rans_row_alpha(row, topology) == alpha_deg
            and row["included_in_rans_mesh_convergence"]
        ]
        fine = next((row for row in eligible if row["mesh_level"] == "fine"), None)
        if fine:
            for row in eligible:
                for metric in ("mean_CL", "mean_CD", "mean_CM", "mean_L_over_D", "x_sep_over_c"):
                    value = row.get(metric)
                    reference = fine.get(metric)
                    row[f"relative_difference_{metric}_vs_fine_percent"] = (
                        100.0 * abs(float(value) - float(reference)) / max(abs(float(reference)), 1.0e-30)
                        if value is not None and reference is not None else None
                    )
    return rows


def _rans_gci_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for topology in ("closed", "open"):
        for alpha_deg in (8.0, 16.0):
            valid = {
                str(row["mesh_level"]): row
                for row in rows
                if row["topology"] == topology
                and _rans_row_alpha(row, topology) == alpha_deg
                and row["included_in_rans_mesh_convergence"]
            }
            if set(valid) != {"coarse", "medium", "fine"}:
                continue
            for metric in ("mean_CL", "mean_CD", "mean_CM"):
                if any(valid[level].get(metric) is None for level in valid):
                    continue
                values = [float(valid[level][metric]) for level in ("coarse", "medium", "fine")]
                pair_differences = [abs(values[index + 1] - values[index]) for index in range(2)]
                manual_uncertainties = [
                    abs(float(row.get(metric.replace("mean_", "std_")) or 0.0))
                    for row in valid.values() if row.get("manual_acceptance")
                ]
                common = {"topology": topology, "alpha_deg": alpha_deg, "metric": metric}
                if manual_uncertainties and max(manual_uncertainties) >= max(min(pair_differences), 1.0e-30):
                    output.append({
                        **common,
                        "status": "GCI_NOT_RELIABLE_RANS_REVIEW_UNCERTAINTY",
                        "maximum_manual_window_std": max(manual_uncertainties),
                        "minimum_spatial_difference": min(pair_differences),
                    })
                    continue
                output.append({
                    **common,
                    **generalized_gci(
                        coarse_value=values[0], medium_value=values[1], fine_value=values[2],
                        coarse_cells=int(valid["coarse"]["cell_count"]),
                        medium_cells=int(valid["medium"]["cell_count"]),
                        fine_cells=int(valid["fine"]["cell_count"]),
                    ),
                    "contains_manual_acceptance": any(row.get("manual_acceptance") for row in valid.values()),
                })
    return output


_RANS_SCALAR_METRICS = {
    "mean_CL": r"$C_L$ [-]",
    "mean_CD": r"$C_D$ [-]",
    "mean_CM": r"$C_M$ [-]",
    "mean_L_over_D": r"$C_L/C_D$ [-]",
}


def _rans_row_alpha(row: dict[str, Any], topology: str) -> float:
    """Read explicit angle while preserving pre-schema multi-angle reports."""
    fallback = 16.0 if topology == "closed" else 8.0
    try:
        return float(row.get("alpha_deg", fallback))
    except (TypeError, ValueError):
        return fallback


def _rans_scalar_change_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return signed coarse/fine differences relative to the medium grid."""
    records: list[dict[str, Any]] = []
    for topology in ("closed", "open"):
      for alpha_deg in (8.0, 16.0):
        selected = {
            str(row.get("mesh_level")): row for row in rows
            if row.get("topology") == topology
            and _rans_row_alpha(row, topology) == alpha_deg
            and row.get("included_in_rans_mesh_convergence")
        }
        medium = selected.get("medium")
        if medium is None:
            continue
        for metric in _RANS_SCALAR_METRICS:
            reference = medium.get(metric)
            available = [row.get(metric) for row in selected.values() if row.get(metric) is not None]
            if reference is None or not available:
                continue
            scale = max(max(abs(float(value)) for value in available), 1.0)
            threshold = max(1.0e-10, 1.0e-6 * scale)
            for level in ("coarse", "medium", "fine"):
                row = selected.get(level)
                if row is None or row.get(metric) is None:
                    continue
                value = float(row[metric])
                reference_f = float(reference)
                difference = value - reference_f
                near_zero = abs(reference_f) <= threshold
                row_cells = int(row.get("cell_count") or 0)
                medium_cells = int(medium.get("cell_count") or 0)
                if level == "coarse" and row_cells > 0:
                    cell_increase = 100.0 * (medium_cells - row_cells) / row_cells
                elif level == "fine" and medium_cells > 0:
                    cell_increase = 100.0 * (row_cells - medium_cells) / medium_cells
                else:
                    cell_increase = None
                response_change = (
                    None if near_zero else 100.0 * abs(difference) / abs(reference_f)
                )
                records.append({
                    "topology": topology,
                    "alpha_deg": alpha_deg,
                    "metric": metric,
                    "mesh_level": level,
                    "reference_level": "medium",
                    "comparison": f"{level}|medium",
                    "cell_count": row.get("cell_count"),
                    "reference_cell_count": medium.get("cell_count"),
                    "delta_signed": difference,
                    "delta_absolute": abs(difference),
                    "delta_percent": (
                        0.0 if level == "medium" else
                        (None if near_zero else 100.0 * difference / abs(reference_f))
                    ),
                    "percent_status": (
                        "REFERENCE" if level == "medium" else
                        ("NOT_DEFINED_NEAR_ZERO_REFERENCE" if near_zero else "DEFINED")
                    ),
                    "near_zero_threshold": threshold,
                    "cell_count_increase_percent": cell_increase,
                    "cell_increase_at_least_30_percent": (
                        None if cell_increase is None else cell_increase >= 30.0
                    ),
                    "response_change_percent": response_change,
                    "response_change_at_most_3_percent": (
                        None if response_change is None else response_change <= 3.0
                    ),
                    "mesh_independence_pair_pass": (
                        None if cell_increase is None or response_change is None
                        else cell_increase >= 30.0 and response_change <= 3.0
                    ),
                })
    return records


def _trend(values: list[float]) -> str:
    if len(values) < 3:
        return "insufficient_data"
    increments = np.diff(np.asarray(values, dtype=float))
    if not np.all(np.isfinite(increments)):
        return "insufficient_data"
    if np.all(increments >= 0.0) or np.all(increments <= 0.0):
        return "monotonic"
    if abs(increments[-1]) > abs(increments[0]):
        return "divergent"
    return "oscillatory"


def _rans_trend_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for topology in ("closed", "open"):
      for alpha_deg in (8.0, 16.0):
        selected = sorted(
            [row for row in rows if row.get("topology") == topology
             and _rans_row_alpha(row, topology) == alpha_deg
             and row.get("included_in_rans_mesh_convergence")],
            key=lambda row: int(row.get("cell_count") or 0),
        )
        for metric in _RANS_SCALAR_METRICS:
            values = [float(row[metric]) for row in selected if row.get(metric) is not None]
            records.append({
                "topology": topology,
                "alpha_deg": alpha_deg,
                "metric": metric,
                "trend": _trend(values),
                "available_levels": len(values),
                "levels": [row.get("mesh_level") for row in selected if row.get(metric) is not None],
            })
    return records


def _plot_rans_spatial_suite(
    output: Path,
    rows: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    gci: list[dict[str, Any]],
) -> list[str]:
    """Create separate, auditable scalar spatial-convergence figures."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    selected = [row for row in rows if row.get("included_in_rans_mesh_convergence")]
    products: list[str] = []
    if not selected:
        return products

    def save(name: str, title: str, x_key: str, y_value, y_label: str) -> None:
        figure, axes = plt.subplots(2, 2, figsize=(11.2, 7.2), constrained_layout=True)
        for axis, (metric, label) in zip(axes.flat, _RANS_SCALAR_METRICS.items()):
            for topology, marker in (("closed", "o"), ("open", "s")):
                values = sorted([row for row in selected if row["topology"] == topology and row.get(metric) is not None], key=lambda row: float(row.get(x_key) or 0.0))
                if values:
                    x_values = [
                        float(row[x_key]) / 1.0e5
                        if x_key == "cell_count" else float(row[x_key])
                        for row in values
                    ]
                    axis.plot(x_values, [y_value(row, metric) for row in values], marker=marker, label=topology.title())
            axis.set_title(f"{title}: {label}")
            axis.set_xlabel(r"Cell count, $N$ [$\times 10^5$]" if x_key == "cell_count" else r"Effective grid size, $h_{\mathrm{eff}}$ [-]")
            axis.set_ylabel(y_label.format(symbol=label))
            axis.grid(True, alpha=0.25)
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                axis.legend(handles, labels)
        path = output / name
        save_scientific_figure(
            figure, path, data=selected,
            metadata={"source": "RANS checkpoint review summaries", "grouping": "topology and mesh refinement"},
        )
        products.append(str(path))

    save("01_aerodynamic_coefficients_vs_cell_count.png", "Aerodynamic coefficients vs. cell count", "cell_count", lambda row, metric: row[metric], "{symbol}")
    save("02_aerodynamic_coefficients_vs_effective_grid_size.png", "Aerodynamic coefficients vs. effective grid size", "effective_h_2d", lambda row, metric: row[metric], "{symbol}")

    def change_plot(name: str, title: str, key: str, ylabel: str) -> None:
        figure, axes = plt.subplots(2, 2, figsize=(11.2, 7.2), constrained_layout=True)
        for axis, (metric, label) in zip(axes.flat, _RANS_SCALAR_METRICS.items()):
            levels = ["coarse", "medium", "fine"]
            positions = np.arange(len(levels), dtype=float)
            width = 0.36
            for offset, topology in ((-width / 2.0, "closed"), (width / 2.0, "open")):
                by_level = {
                    str(row["mesh_level"]): row
                    for row in changes
                    if row["topology"] == topology
                    and row["metric"] == metric
                    and row.get(key) is not None
                }
                if by_level:
                    heights = [
                        float(by_level[level][key]) if level in by_level else np.nan
                        for level in levels
                    ]
                    axis.bar(positions + offset, heights, width=width, label=topology.title())
            axis.axhline(0.0, color="black", linewidth=0.7, linestyle="--")
            if key == "delta_percent":
                axis.axhline(3.0, color="#b3261e", linewidth=0.8, linestyle=":")
                axis.axhline(-3.0, color="#b3261e", linewidth=0.8, linestyle=":")
            axis.set_title(f"{title}: {label}")
            axis.set_xlabel("Grid level relative to medium")
            axis.set_ylabel(ylabel)
            axis.set_xticks(positions, [level.title() for level in levels])
            axis.grid(True, alpha=0.25)
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                axis.legend(handles, labels)
        path = output / name
        save_scientific_figure(
            figure, path, data=changes,
            metadata={"source": "RANS checkpoint review summaries", "transformation": "signed differences relative to medium"},
        )
        products.append(str(path))

    change_plot("03_signed_difference_relative_to_medium.png", "Signed difference relative to medium", "delta_percent", "Signed difference relative to medium [%]")
    change_plot("04_absolute_difference_relative_to_medium.png", "Absolute difference relative to medium", "delta_absolute", "Absolute difference [-]")

    figure, axes = plt.subplots(2, 2, figsize=(11.2, 7.2), constrained_layout=True)
    for axis, (metric, label) in zip(axes.flat, _RANS_SCALAR_METRICS.items()):
        for topology, marker in (("closed", "o"), ("open", "s")):
            values = sorted([row for row in selected if row["topology"] == topology and row.get(metric) is not None and row.get("seconds_per_iteration") is not None], key=lambda row: float(row.get("seconds_per_iteration") or 0.0))
            if values:
                axis.scatter([row["seconds_per_iteration"] for row in values], [row[metric] for row in values], marker=marker, label=topology.title())
        axis.set_title(f"Accuracy-cost trade-off: {label}")
        axis.set(xlabel="Median solver time per iteration [s]", ylabel=label)
        axis.grid(True, alpha=0.25)
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            axis.legend(handles, labels)
    path = output / "05_accuracy_cost_tradeoff.png"
    save_scientific_figure(
        figure, path, data=selected,
        metadata={"source": "RANS checkpoint timing and coefficient summaries", "grouping": "topology"},
    )
    products.append(str(path))

    if gci:
        figure, axis = plt.subplots(figsize=(8.8, 4.8), constrained_layout=True)
        available = [row for row in gci if isinstance(row.get("gci_fine_percent"), (int, float))]
        if available:
            axis.bar([f"{row['topology']} {row['metric']}" for row in available], [row["gci_fine_percent"] for row in available])
            axis.set(ylabel="Fine-grid GCI [%]", title="GCI / observed-order assessment")
        else:
            axis.text(0.5, 0.5, "GCI_NOT_APPLICABLE\nSee spatial_rans_gci.csv for reasons.", ha="center", va="center")
            axis.set_axis_off()
        path = output / "11_gci_observed_order.png"
        save_scientific_figure(
            figure, path, data=gci,
            metadata={"source": "RANS three-grid GCI assessment", "filters": ["applicability rules retained in CSV"]},
        )
        products.append(str(path))
    return products


def _plot_rans_spatial(output: Path, rows: list[dict[str, Any]]) -> str | None:
    selected = [
        row for row in rows
        if row.get("included_in_rans_mesh_convergence")
        and row.get("mean_CL") is not None
    ]
    if not selected:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(1, 4, figsize=(14.2, 3.8))
    for axis, metric in zip(axes, ("mean_CL", "mean_CD", "mean_CM", "mean_L_over_D")):
        for topology in ("closed", "open"):
            topology_rows = sorted(
                [row for row in selected if row["topology"] == topology],
                key=lambda row: int(row["cell_count"]),
            )
            if not topology_rows or any(row.get(metric) is None for row in topology_rows):
                continue
            for manual in (False, True):
                points = [
                    row for row in topology_rows
                    if bool(row.get("manual_acceptance")) == manual
                ]
                if not points:
                    continue
                axis.scatter(
                    [float(row["cell_count"]) / 1.0e5 for row in points],
                    [row[metric] for row in points],
                    marker="s" if manual else "o",
                    label=(
                        f"{topology}, manual review"
                        if manual
                        else f"{topology}, auto gate"
                    ),
                )
            axis.plot(
                [float(row["cell_count"]) / 1.0e5 for row in topology_rows],
                [row[metric] for row in topology_rows],
                lw=0.7,
                alpha=0.45,
            )
        axis.set(xlabel=r"Cell count, $N$ [$\times 10^5$]", ylabel=metric)
        axis.grid(True, alpha=0.25)
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        figure.legend(handles, labels, loc="upper center", ncol=2, fontsize=8)
    figure.tight_layout(rect=(0, 0, 1, 0.9))
    path = output / "spatial_rans_comparison.png"
    save_scientific_figure(
        figure,
        path,
        data=selected,
        metadata={
            "source": "RANS checkpoint review summaries",
            "transformation": "cell count divided by 1e5 for display",
            "grouping": "topology, mesh level and review route",
        },
    )
    return str(path)


def _plot_rans_wall_metrics(output: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Plot final-iteration y+ and arc-length separation metrics by refinement."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eligible = [row for row in rows if row.get("included_in_rans_mesh_convergence")]
    if not eligible:
        return []
    output.mkdir(parents=True, exist_ok=True)
    products: list[str] = []
    level_order = {"coarse": 0, "medium": 1, "fine": 2}

    y_rows = [row for row in eligible if row.get("yplus_max") is not None]
    if y_rows:
        figure, axis = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
        for topology, marker in (("closed", "o"), ("open", "s")):
            values = sorted(
                [row for row in y_rows if row["topology"] == topology],
                key=lambda row: level_order.get(str(row["mesh_level"]), 99),
            )
            if values:
                axis.plot([row["mesh_level"].title() for row in values], [row["yplus_max"] for row in values], marker=marker, label=topology.title())
        axis.axhline(1.0, color="#D55E00", linestyle="--", linewidth=0.9, label=r"Reference $y^+=1$")
        axis.set(xlabel="Mesh refinement", ylabel=r"Maximum final-iteration $y^+$ [-]", title=r"Maximum wall $y^+$ versus mesh refinement")
        axis.legend()
        path = output / "yplus_max_vs_mesh_refinement.png"
        save_scientific_figure(
            figure, path, data=y_rows,
            metadata={
                "source": "selected RANS checkpoint wall_yplus_vs_xc.csv",
                "transformation": "maximum finite yPlus at the selected final iteration",
                "grouping": "topology and mesh_level",
            },
        )
        products.append(str(path))

    return products


def generate_study_report(project_root: Path) -> dict[str, Any]:
    study = load_study(project_root)
    if not study:
        raise FileNotFoundError("Validation convergence workspace is not initialized")
    active = active_workspace_root(project_root)
    output = active / "postprocess/reports"
    output.mkdir(parents=True, exist_ok=True)
    completed = _flatten_completed_runs(study)
    gci = _gci_records(completed)
    rans_rows = _rans_review_rows(study)
    rans_gci = _rans_gci_records(rans_rows)
    rans_changes = _rans_scalar_change_records(rans_rows)
    rans_trends = _rans_trend_records(rans_rows)
    rans_spatial_products: list[str] = []
    if rans_rows:
        _write_csv_nonempty(output / "spatial_rans_comparison.csv", rans_rows)
        _write_csv_nonempty(output / "spatial_rans_consecutive_changes.csv", rans_changes)
        _write_csv_nonempty(output / "spatial_rans_trends.csv", rans_trends)
        spatial_root = active / "postprocess/spatial_rans"
        obsolete_spatial_stems = (
            "rans_separation_vs_cells",
            "rans_separation_vs_effective_h",
            "separation_location_vs_refinement",
            "reattachment_location_vs_refinement",
            "03_relative_change_between_consecutive_grids",
            "04_difference_from_fine_grid_solution",
        )
        for folder in (spatial_root, spatial_root / "closed", spatial_root / "open"):
            for stem in obsolete_spatial_stems:
                for path in folder.glob(f"{stem}*"):
                    if path.is_file():
                        path.unlink()
        for topology in ("closed", "open"):
            for alpha_deg in (8.0, 16.0):
                selected_rows = [
                    row for row in rans_rows
                    if row["topology"] == topology
                    and _rans_row_alpha(row, topology) == alpha_deg
                ]
                selected_changes = [
                    row for row in rans_changes
                    if row["topology"] == topology
                    and _rans_row_alpha(row, topology) == alpha_deg
                ]
                selected_gci = [
                    row for row in rans_gci
                    if row["topology"] == topology
                    and _rans_row_alpha(row, topology) == alpha_deg
                ]
                angle_root = spatial_root / topology / f"alpha_{int(alpha_deg)}"
                _plot_rans_spatial(angle_root, selected_rows)
                rans_spatial_products.extend(
                    _plot_rans_spatial_suite(
                        angle_root, selected_rows, selected_changes, selected_gci
                    )
                )
                rans_spatial_products.extend(
                    _plot_rans_wall_metrics(angle_root, selected_rows)
                )
    if rans_gci:
        _write_csv_nonempty(output / "spatial_rans_gci.csv", rans_gci)
    matrix_rows = [
        {
            "run_id": row.get("run_id"),
            "topology": row.get("topology"),
            "mesh_level": row.get("mesh_level"),
            "cell_count": row.get("cell_count"),
            "dt_s": row.get("dt_s"),
            "dt_star": row.get("dt_star"),
            "status": row.get("status"),
            "acceptance": row.get("acceptance"),
        }
        for row in study["run_matrix"].get("runs", [])
    ]
    _write_csv_nonempty(output / "acceptance_matrix.csv", matrix_rows)
    if completed:
        _write_csv_nonempty(output / "spatial_temporal_comparison.csv", completed)
        _write_csv_nonempty(
            output / "frequency_comparison.csv",
            [
                {
                    key: row.get(key)
                    for key in (
                        "run_id", "topology", "mesh_level", "cell_count", "dt_s",
                        "dt_star", "dominant_St", "dominant_W", "psd_peak_amplitude",
                        "stationarity_passed",
                    )
                }
                for row in completed
            ],
        )
        courant_rows = [
            {
                key: row.get(key)
                for key in (
                    "run_id", "topology", "mesh_level", "cell_count", "dt_s",
                    "courant_max", "courant_mean",
                )
            }
            for row in completed
            if row.get("courant_max") is not None
        ]
        _write_csv_nonempty(output / "courant_comparison.csv", courant_rows)
        pimple_source = [
            row for row in completed
            if row.get("cpu_seconds_per_step") is not None
            and row.get("dominant_St") is not None
        ]
        pimple_report = compare_pimple_outer_correctors(pimple_source)
        if pimple_report["status"] == "COMPARISON_AVAILABLE":
            _write_csv_nonempty(
                output / "pimple_comparison.csv",
                list(pimple_report["comparisons"]),
            )
    if gci:
        _write_csv_nonempty(output / "spatial_temporal_gci.csv", gci)
    status_counts: dict[str, int] = {}
    for row in matrix_rows:
        status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
    report = {
        "schema_version": 1,
        "study_id": study["study_manifest"]["study_id"],
        "generated_at": utc_stamp(),
        "operating_condition": study["study_config"]["operating_condition"],
        "mesh_count": len(study["mesh_registry"].get("meshes", [])),
        "run_status_counts": status_counts,
        "completed_real_runs": len(completed),
        "analyzed_rans_checkpoints": sum(
            row.get("included_in_rans_mesh_convergence")
            for row in rans_rows
        ),
        "rans_gci_evaluations": rans_gci,
        "rans_scalar_change_records": len(rans_changes),
        "rans_mesh_independence_pairs": [
            row for row in rans_changes if row.get("mesh_level") != "medium"
        ],
        "rans_trend_records": rans_trends,
        "rans_spatial_products": rans_spatial_products,
        "gci_evaluations": gci,
        "warnings": list(study["mesh_registry"].get("warnings", [])),
        "validation_claim": False,
        "note": (
            "No aerodynamic validation is claimed. Missing run products remain explicit "
            "and are never replaced by synthetic CSV files."
        ),
    }
    write_json_atomic(output / "study_report.json", report)
    lines = [
        "# Validation & Convergence Lab report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- RANS base angles: closed 16 deg; open 8 deg",
        "- Mach: 0.15",
        "- Reynolds: 1.9e6",
        "- Chord: 1 m",
        "- This is not a polar validation.",
        "",
        "## Status",
        "",
        f"- Registered real meshes: {report['mesh_count']}",
        f"- Completed real runs: {report['completed_real_runs']}",
        f"- Analyzed SIMPLE checkpoints: {report['analyzed_rans_checkpoints']}",
        f"- Run states: `{json.dumps(status_counts, sort_keys=True)}`",
        "",
        "## Numerical warnings",
        "",
    ]
    lines.extend(f"- {warning}" for warning in report["warnings"])
    if not report["warnings"]:
        lines.append("- None recorded.")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "A checkMesh PASS is necessary but does not establish spatial or temporal convergence.",
        "GCI is omitted when ratios are weak, convergence is non-monotonic or the system is ill-conditioned.",
        "Courant hotspots are diagnostic; rejection requires unbounded behaviour or material time-step sensitivity.",
        "",
    ])
    (output / "study_report.md").write_text("\n".join(lines), encoding="utf-8")

    result = results_study_root(project_root)
    result.mkdir(parents=True, exist_ok=True)
    for source in output.iterdir():
        if source.is_file() and source.stat().st_size > 0:
            (result / source.name).write_bytes(source.read_bytes())
    return report
