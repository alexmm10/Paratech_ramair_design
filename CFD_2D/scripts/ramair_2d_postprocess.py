#!/usr/bin/env python3
"""Post-processing utilities for ram-air 2D OpenFOAM cases.

Reads forceCoeffs when available and exports mean/std statistics and plots.  The
script tolerates missing Ross reference placeholders and does not calibrate results.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from openfoam_environment import activate_openfoam_environment
from openfoam_history import read_force_coefficient_history
from openfoam_wall_analysis import analyze_wall_boundary_layer
from paraview_case_viewer import generate_automatic_paraview_products, launch_paraview_case
from ramair_2d_postprocess_registry import write_postprocess_manifest
from ramair_monitor_core import scalar_signal_inventory
from ramair_scientific_plot_style import apply_scientific_style, save_scientific_figure

apply_scientific_style()

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CATIA_INPUTS_DIR_NAME = "CATIA/Inputs"
CFD_ROOT_DIR_NAME = "CFD_2D"


def project_root_from_case_root(case_root: Path) -> Path:
    case_root = Path(case_root)
    if case_root.name == CATIA_INPUTS_DIR_NAME:
        return case_root.parent
    if case_root.name in {"openfoam_cases", "results", "meshes"} and case_root.parent.name == CFD_ROOT_DIR_NAME:
        return case_root.parent.parent
    if case_root.name == CFD_ROOT_DIR_NAME:
        return case_root.parent
    return case_root


def cfd_root(case_root: Path) -> Path:
    return project_root_from_case_root(case_root) / CFD_ROOT_DIR_NAME


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON from {path}: {exc}") from exc


def json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return None if not np.isfinite(value) else value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def read_force_coeffs(case_dir: Path) -> pd.DataFrame:
    records, sources = read_force_coefficient_history(case_dir, include_processor0=True)
    if not records:
        raise FileNotFoundError("forceCoeffs output not found")
    frame = pd.DataFrame(records)
    frame.attrs["sources"] = sources
    return frame


def numeric_time_dirs(case_dir: Path) -> list[tuple[float, Path]]:
    times: list[tuple[float, Path]] = []
    if not case_dir.exists():
        return times
    for p in case_dir.iterdir():
        if not p.is_dir():
            continue
        try:
            times.append((float(p.name), p))
        except ValueError:
            continue
    return sorted(times, key=lambda item: item[0])


def parse_solver_log(case_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    candidates = [
        path for pattern in ("log.foamRun", "log.pimpleFoam", "*PyFoamRunner*")
        for path in case_dir.glob(pattern)
        if path.is_file()
    ]
    if not candidates:
        return pd.DataFrame(), pd.DataFrame(), {"solver_log_found": False}
    log_path = max(candidates, key=lambda path: path.stat().st_mtime)
    residual_rows: list[dict[str, Any]] = []
    courant_rows: list[dict[str, Any]] = []
    current_time: float | None = None
    pending_courant: dict[str, Any] | None = None
    pending_delta_t: float | None = None
    execution_time = None
    clock_time = None
    residual_re = re.compile(
        r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+-]+),\s+Final residual\s*=\s*([0-9.eE+-]+),\s+No Iterations\s+(\d+)"
    )
    time_re = re.compile(r"^Time\s*=\s*([0-9.eE+-]+)")
    dt_re = re.compile(r"^deltaT\s*=\s*([0-9.eE+-]+)")
    courant_re = re.compile(r"Courant Number mean:\s*([0-9.eE+-]+)\s+max:\s*([0-9.eE+-]+)")
    exec_re = re.compile(r"ExecutionTime\s*=\s*([0-9.eE+-]+)\s*s\s+ClockTime\s*=\s*([0-9.eE+-]+)\s*s")
    continuity_re = re.compile(
        r"time step continuity errors\s*:\s*sum local\s*=\s*([0-9.eE+-]+),\s*"
        r"global\s*=\s*([0-9.eE+-]+)(?:,\s*cumulative\s*=\s*([0-9.eE+-]+))?"
    )
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        tm = time_re.search(line.strip())
        if tm:
            try:
                current_time = float(tm.group(1))
            except ValueError:
                current_time = None
            if pending_courant is not None:
                pending_courant["Time"] = current_time
                pending_courant["deltaT"] = pending_delta_t if pending_delta_t is not None else np.nan
                courant_rows.append(pending_courant)
                pending_courant = None
                pending_delta_t = None
            continue
        dm = dt_re.search(line.strip())
        if dm:
            try:
                pending_delta_t = float(dm.group(1))
            except ValueError:
                pass
            continue
        cm = courant_re.search(line)
        if cm:
            pending_courant = {
                "Time": None,
                "Co_mean": float(cm.group(1)),
                "Co_max": float(cm.group(2)),
                "deltaT": np.nan,
            }
            continue
        rm = residual_re.search(line)
        if rm:
            residual_rows.append({
                "Time": current_time,
                "field": rm.group(1).strip(),
                "initial_residual": float(rm.group(2)),
                "final_residual": float(rm.group(3)),
                "iterations": int(rm.group(4)),
            })
            continue
        continuity = continuity_re.search(line)
        if continuity:
            global_error = float(continuity.group(2))
            cumulative = float(continuity.group(3)) if continuity.group(3) is not None else global_error
            residual_rows.append({
                "Time": current_time,
                "field": "continuity_global",
                "initial_residual": abs(global_error),
                "final_residual": abs(cumulative),
                "iterations": 0,
            })
            continue
        em = exec_re.search(line)
        if em:
            execution_time = float(em.group(1))
            clock_time = float(em.group(2))
    if pending_courant is not None and current_time is not None:
        pending_courant["Time"] = current_time
        pending_courant["deltaT"] = pending_delta_t if pending_delta_t is not None else np.nan
        courant_rows.append(pending_courant)
    meta = {
        "solver_log_found": True,
        "solver_log": str(log_path),
        "execution_time_s": execution_time,
        "clock_time_s": clock_time,
    }
    return pd.DataFrame(residual_rows), pd.DataFrame(courant_rows), meta


def select_final_force_window(
    df: pd.DataFrame,
    average_from_fraction: float = 0.6,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select the final continuous force segment and an auditable tail window."""
    if "Time" not in df.columns:
        df.insert(0, "Time", np.arange(len(df)))
    clean = df.copy()
    clean["Time"] = pd.to_numeric(clean["Time"], errors="coerce")
    clean = clean[np.isfinite(clean["Time"])].copy()
    clean = clean.drop_duplicates(subset=["Time"], keep="last").sort_values("Time")
    if clean.empty:
        raise ValueError("Force history has no finite time samples")
    differences = clean["Time"].diff()
    positive = differences[differences > 0.0]
    median_dt = float(positive.median()) if not positive.empty else None
    gap_threshold = (
        max(5.0 * median_dt, 1.0e-12) if median_dt is not None else math.inf
    )
    gap_indices = [
        int(index)
        for index, value in differences.items()
        if pd.notna(value) and float(value) > gap_threshold
    ]
    final_segment = clean.loc[gap_indices[-1] :].copy() if gap_indices else clean
    fraction = min(0.99, max(0.0, float(average_from_fraction)))
    t0 = float(final_segment["Time"].min())
    t1 = float(final_segment["Time"].max())
    requested_start = t0 + fraction * (t1 - t0)
    win = final_segment[final_segment["Time"] >= requested_start].copy()
    minimum_samples = min(len(final_segment), max(5, min(50, int(math.ceil(0.1 * len(final_segment))))))
    minimum_applied = len(win) < minimum_samples
    if minimum_applied:
        win = final_segment.tail(minimum_samples).copy()
    manifest = {
        "schema_version": 1,
        "selection_mode": "final_continuous_fraction",
        "configured_average_from_fraction": fraction,
        "source_samples": int(len(clean)),
        "continuous_segment_samples": int(len(final_segment)),
        "selected_samples": int(len(win)),
        "source_start_time": float(clean["Time"].min()),
        "source_end_time": float(clean["Time"].max()),
        "continuous_segment_start_time": t0,
        "continuous_segment_end_time": t1,
        "requested_window_start_time": requested_start,
        "selected_window_start_time": float(win["Time"].min()),
        "selected_window_end_time": float(win["Time"].max()),
        "median_sample_delta_t": median_dt,
        "continuity_gap_threshold": None if not math.isfinite(gap_threshold) else gap_threshold,
        "detected_large_gaps": len(gap_indices),
        "minimum_samples": int(minimum_samples),
        "minimum_samples_override_applied": bool(minimum_applied),
        "reason": (
            "last continuous force segment after a detected history gap"
            if gap_indices
            else "final fraction of the continuous force history"
        ),
    }
    return win, manifest


def summarize_force_coeffs(df: pd.DataFrame, average_from_fraction: float = 0.6) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    win, window_manifest = select_final_force_window(df, average_from_fraction)
    cols = [c for c in ["Cl", "Cd", "Cm"] if c in win.columns]
    if not cols:
        cols = [c for c in win.columns if c != "Time"][:3]
    mean = win[cols].mean().to_frame("mean").T
    std = win[cols].std().to_frame("std").T
    if "Cl" in mean and "Cd" in mean:
        mean["L_D"] = mean["Cl"] / mean["Cd"].replace(0, np.nan)
    win.attrs["window_manifest"] = window_manifest
    return win, mean, std


def readable_axis_limits(values: pd.Series) -> tuple[float, float]:
    finite = pd.to_numeric(values, errors="coerce")
    finite = finite[np.isfinite(finite)]
    if finite.empty:
        return -1.0, 1.0
    lower, upper = float(finite.min()), float(finite.max())
    span = upper - lower
    pad = 0.08 * span if span > 0.0 else max(abs(lower), 1.0) * 0.08
    return lower - pad, upper + pad


def plot_force_coeffs(
    history: pd.DataFrame,
    mean: pd.DataFrame,
    output: Path,
    *,
    average_window: pd.DataFrame | None = None,
    x_label: str = r"Physical time, $t$ [s]",
    title: str = "Aerodynamic coefficients: complete history and averaging window",
) -> None:
    try:
        import matplotlib.pyplot as plt
        if not history.empty:
            columns = [name for name in ("Cl", "Cd", "Cm") if name in history.columns]
            if not columns:
                return
            fig, axes = plt.subplots(len(columns), 1, figsize=(9, 2.6 * len(columns)), sharex=True)
            axes = np.atleast_1d(axes)
            for ax, c in zip(axes, columns):
                history_line, = ax.plot(history["Time"], history[c], label=c, linewidth=1.15)
                if average_window is not None and not average_window.empty:
                    ax.axvspan(
                        float(average_window["Time"].min()),
                        float(average_window["Time"].max()),
                        color=history_line.get_color(), alpha=0.12,
                        label="averaging interval",
                    )
                if c in mean.columns:
                    m = float(mean[c].iloc[0])
                    if np.isfinite(m):
                        ax.axhline(
                            m,
                            color=history_line.get_color(),
                            linestyle="--",
                            linewidth=0.8,
                            alpha=0.75,
                            label=f"{c} mean={m:.5g}",
                        )
                ax.set_ylim(*readable_axis_limits(history[c]))
                ax.set_ylabel(c)
                ax.grid(True, linewidth=0.3)
                ax.legend(fontsize=8, loc="best")
            axes[-1].set_xlabel(x_label)
            axes[0].set_title(title)
            fig.tight_layout()
            save_scientific_figure(
                fig, output, data=history,
                metadata={"source": "OpenFOAM forceCoeffs", "overlay": "selected averaging window"},
            )
    except Exception as exc:
        raise RuntimeError(f"Could not plot force coefficients to {output}: {exc}") from exc


def write_aerodynamic_efficiency_products(
    history: pd.DataFrame,
    output_csv: Path,
    output_png: Path,
    *,
    x_label: str = r"Physical time, $t$ [s]",
    title: str = "Aerodynamic efficiency - averaging window",
    mean_from_fraction: float = 0.6,
) -> dict[str, Any]:
    """Write traceable Cl/Cd data without inventing values near Cd=0."""
    required = {"Time", "Cl", "Cd"}
    if history.empty or not required.issubset(history.columns):
        return {"status": "NOT_AVAILABLE", "reason": "Cl/Cd history is incomplete"}
    frame = history.copy()
    finite = np.isfinite(frame["Cl"]) & np.isfinite(frame["Cd"])
    safe_drag = np.abs(frame["Cd"]) > 1.0e-12
    frame["Cl_over_Cd"] = np.where(finite & safe_drag, frame["Cl"] / frame["Cd"], np.nan)
    frame.to_csv(output_csv, index=False)
    usable = frame.dropna(subset=["Time", "Cl_over_Cd"])
    if usable.empty:
        return {
            "status": "NOT_AVAILABLE",
            "reason": "No finite Cl/Cd samples after excluding Cd approximately zero",
            "csv": str(output_csv),
        }
    try:
        import matplotlib.pyplot as plt

        fraction = min(0.99, max(0.0, float(mean_from_fraction)))
        start = float(usable["Time"].min()) + fraction * (
            float(usable["Time"].max()) - float(usable["Time"].min())
        )
        mean_window = usable[usable["Time"] >= start]
        if mean_window.empty:
            mean_window = usable.tail(1)
        fig, ax = plt.subplots(figsize=(7.2, 3.6))
        ax.plot(usable["Time"], usable["Cl_over_Cd"], color="#176b87", linewidth=1.15, alpha=0.9)
        mean_value = float(mean_window["Cl_over_Cd"].mean())
        ax.axhline(
            mean_value,
            color="#b34b3f",
            linestyle="--",
            linewidth=0.9,
            label=f"final-window mean={mean_value:.5g}",
        )
        ax.set_xlabel(x_label)
        ax.set_ylabel(r"$C_L/C_D$ [-]")
        ax.set_ylim(0.0, 75.0)
        ax.set_title(title)
        ax.grid(True, linewidth=0.3, alpha=0.6)
        ax.legend(fontsize=8)
        fig.tight_layout()
        save_scientific_figure(
            fig, output_png, data=usable,
            metadata={"source": "OpenFOAM forceCoeffs", "transformation": "CL/CD where |CD| exceeds tolerance"},
        )
    except Exception as exc:
        raise RuntimeError(f"Could not plot aerodynamic efficiency to {output_png}: {exc}") from exc
    return {
        "status": "OK",
        "samples": int(len(usable)),
        "excluded_cd_near_zero": int((~safe_drag).sum()),
        "samples_outside_display_range_0_100": int(
            ((usable["Cl_over_Cd"] < 0.0) | (usable["Cl_over_Cd"] > 100.0)).sum()
        ),
        "mean_Cl_over_Cd": mean_value,
        "mean_from_fraction": fraction,
        "mean_window_start": float(mean_window["Time"].min()),
        "mean_window_end": float(mean_window["Time"].max()),
        "mean_window_samples": int(len(mean_window)),
        "csv": str(output_csv),
        "png": str(output_png),
    }


def plot_residuals(df: pd.DataFrame, out: Path) -> None:
    if df.empty or "Time" not in df.columns:
        return
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 5))
        for field, sub in df.groupby("field"):
            clean = sub.dropna(subset=["Time", "initial_residual"])
            if not clean.empty:
                ax.semilogy(clean["Time"], clean["initial_residual"], label=f"{field} initial", alpha=0.55, linewidth=1.0)
        ax.grid(True, which="both", linewidth=0.3)
        ax.legend(fontsize=8)
        ax.set_xlabel(r"Physical time, $t$ [s]")
        ax.set_ylabel("Initial residual [-]")
        ax.set_title("OpenFOAM solver residuals")
        fig.tight_layout()
        save_scientific_figure(
            fig, out, data=df,
            metadata={"source": "OpenFOAM solver log", "grouping": "solved field"},
        )
    except Exception as exc:
        raise RuntimeError(f"Could not plot residuals to {out}: {exc}") from exc


def _delta_t_table(df: pd.DataFrame) -> pd.DataFrame:
    dt = df[["Time", "deltaT"]].copy()
    if dt["deltaT"].isna().all():
        dt["deltaT"] = dt["Time"].diff()
    return dt.dropna(subset=["Time", "deltaT"])


def plot_delta_t(
    df: pd.DataFrame,
    out: Path,
    *,
    maximum_delta_t_s: float | None = None,
) -> None:
    if df.empty or "Time" not in df.columns or "deltaT" not in df.columns:
        return
    try:
        import matplotlib.pyplot as plt

        dt = _delta_t_table(df)
        if dt.empty:
            return
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(dt["Time"], dt["deltaT"], color="#1f4e79", linewidth=1.2, label="deltaT usado")
        if maximum_delta_t_s is not None and math.isfinite(maximum_delta_t_s) and maximum_delta_t_s > 0.0:
            ax.axhline(
                maximum_delta_t_s,
                color="#b22222",
                linestyle="--",
                linewidth=1.0,
                label=f"maxDeltaT = {maximum_delta_t_s:.4g} s",
            )
        ax.grid(True, linewidth=0.3)
        ax.set_yscale("log")
        ax.set_xlabel(r"Physical time, $t$ [s]")
        ax.set_ylabel(r"Time step, $\Delta t$ [s]")
        ax.set_title("Adaptive time-step history (log scale)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        save_scientific_figure(
            fig, out, data=dt,
            metadata={"source": "OpenFOAM solver log", "transformation": "reported deltaT or finite time difference"},
        )
    except Exception as exc:
        raise RuntimeError(f"Could not plot deltaT history to {out}: {exc}") from exc


def plot_courant(
    df: pd.DataFrame,
    out: Path,
    *,
    maximum_delta_t_s: float | None = None,
) -> None:
    if df.empty or "Time" not in df.columns:
        return
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4))
        for col in ["Co_mean", "Co_max"]:
            if col in df.columns:
                ax.plot(df["Time"], df[col], label=col)
        ax.grid(True, linewidth=0.3)
        ax.set_xlabel(r"Physical time, $t$ [s]")
        ax.set_ylabel("Courant number, Co [-]")
        dt = _delta_t_table(df)
        dt_axis = ax.twinx()
        if not dt.empty:
            dt_axis.plot(
                dt["Time"], dt["deltaT"], color="#7b3294", linewidth=1.0,
                alpha=0.82, label=r"$\Delta t$",
            )
        if maximum_delta_t_s is not None and math.isfinite(maximum_delta_t_s) and maximum_delta_t_s > 0.0:
            dt_axis.axhline(
                maximum_delta_t_s, color="#b22222", linestyle="--", linewidth=0.9,
                label=f"maxDeltaT={maximum_delta_t_s:.4g} s",
            )
        dt_axis.set_ylabel(r"Time step, $\Delta t$ [s]")
        if not dt.empty and (dt["deltaT"] > 0.0).all():
            dt_axis.set_yscale("log")
        handles, labels = ax.get_legend_handles_labels()
        dt_handles, dt_labels = dt_axis.get_legend_handles_labels()
        ax.legend(handles + dt_handles, labels + dt_labels, fontsize=8, loc="best")
        ax.set_title("Courant number and adaptive time-step history")
        fig.tight_layout()
        save_scientific_figure(
            fig, out, data=df,
            metadata={"source": "OpenFOAM solver log", "transformation": "Courant and deltaT share the physical-time axis; separate y axes"},
        )
    except Exception as exc:
        raise RuntimeError(f"Could not plot Courant history to {out}: {exc}") from exc


def run_optional_command(cmd: list[str], cwd: Path, log_path: Path, timeout_s: int) -> dict[str, Any]:
    activate_openfoam_environment()
    if shutil.which(cmd[0]) is None:
        return {"command": " ".join(cmd), "status": "MISSING_EXECUTABLE", "log": str(log_path)}
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_s)
        log_path.write_text(proc.stdout or "", encoding="utf-8", errors="ignore")
        return {"command": " ".join(cmd), "status": "OK" if proc.returncode == 0 else "FAIL", "returncode": proc.returncode, "log": str(log_path)}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        log_path.write_text(out + f"\nTIMEOUT after {timeout_s} s\n", encoding="utf-8", errors="ignore")
        return {"command": " ".join(cmd), "status": "TIMEOUT", "timeout_s": timeout_s, "log": str(log_path)}


def run_optional_command_variants(command_variants: list[list[str]], cwd: Path, log_prefix: Path, timeout_s: int) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for idx, cmd in enumerate(command_variants, start=1):
        log_path = log_prefix.with_name(f"{log_prefix.name}_attempt{idx:02d}.log")
        result = run_optional_command(cmd, cwd, log_path, timeout_s)
        attempts.append(result)
        if result.get("status") == "OK":
            # Do not attach `attempts` to the same dictionary already stored
            # in that list: that creates a self-referential object which JSON
            # cannot serialize.
            winning_result = dict(result)
            winning_result["attempts"] = [dict(attempt) for attempt in attempts]
            return winning_result
    return {"status": "FAIL", "attempts": attempts, "reason": "all_command_variants_failed_or_missing"}


POSTPROCESS_VOLUME_FIELDS = (
    "U", "p", "Cp", "nuTilda", "nut", "Co", "yPlus",
    "wallShearStress", "vorticity", "Q", "UMean", "pMean",
    "nuTildaMean", "UPrime2Mean", "pPrime2Mean",
)


def available_postprocess_fields(case_dir: Path) -> list[str]:
    """Return only relevant fields that exist at the latest written state."""
    positive = [(value, path) for value, path in numeric_time_dirs(case_dir) if value > 0.0]
    if not positive:
        return []
    latest = positive[-1][1]
    return [
        field
        for field in POSTPROCESS_VOLUME_FIELDS
        if (latest / field).is_file() or (latest / f"{field}.gz").is_file()
    ]


def reconstruct_pending_parallel_times(
    *,
    case_dir: Path,
    out_dir: Path,
    all_times: bool,
    timeout_s: int,
) -> dict[str, Any]:
    processor_dirs = sorted(path for path in case_dir.glob("processor[0-9]*") if path.is_dir())
    if not processor_dirs:
        return {"status": "SKIPPED", "reason": "serial_case"}
    root_times = [value for value, _ in numeric_time_dirs(case_dir) if value > 0.0]
    processor_times: list[float] = []
    for processor in processor_dirs:
        processor_times.extend(value for value, _ in numeric_time_dirs(processor) if value > 0.0)
    if not processor_times or (root_times and max(root_times) >= max(processor_times)):
        return {"status": "SKIPPED", "reason": "no_newer_processor_time"}
    logs_dir = out_dir / "openfoam_postprocess_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    command = ["reconstructPar"] if all_times else ["reconstructPar", "-latestTime"]
    fields = available_postprocess_fields(processor_dirs[0])
    if fields:
        command += ["-fields", f"({' '.join(fields)})"]
    return run_optional_command(command, case_dir, logs_dir / "log.reconstructPar_postprocess", timeout_s)


def derived_field_inventory(
    case_dir: Path,
    out_dir: Path,
    *,
    simulation_mode: str = "AUTO",
) -> dict[str, Any]:
    requested = [
        "U", "p", "Cp", "nuTilda", "nut", "yPlus", "wallShearStress",
        "vorticity", "Q", "UMean", "pMean", "nuTildaMean",
        "UPrime2Mean", "pPrime2Mean",
    ]
    if str(simulation_mode).upper() == "URANS":
        requested.insert(3, "Co")
    rows: list[dict[str, Any]] = []
    for value, directory in numeric_time_dirs(case_dir):
        if value <= 0.0:
            continue
        row: dict[str, Any] = {"time": value, "directory": directory.name}
        for field in requested:
            row[field] = (directory / field).is_file() or (directory / f"{field}.gz").is_file()
        rows.append(row)
    if rows:
        pd.DataFrame(rows).to_csv(out_dir / "written_field_inventory.csv", index=False)
    latest = rows[-1] if rows else {}
    return {
        "positive_time_count": len(rows),
        "latest_time": latest.get("time"),
        "latest_fields": {field: bool(latest.get(field, False)) for field in requested},
        "inventory_csv": str(out_dir / "written_field_inventory.csv") if rows else None,
    }


def copy_pyfoam_diagnostics(case_dir: Path, out_dir: Path) -> dict[str, Any]:
    source = case_dir / "postProcessing" / "PyFoamPlots"
    if not source.is_dir():
        return {"status": "SKIPPED", "reason": "PyFoamPlots_missing"}
    destination = out_dir / "PyFoamPlots"
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    selected_plot = re.compile(
        r"(?:linear_residuals|lift_coefficient|drag_moment_coefficients)\.png$",
        re.IGNORECASE,
    )
    for path in source.iterdir():
        is_current_plot = path.suffix.lower() != ".png" or selected_plot.search(path.name)
        if path.is_file() and is_current_plot and path.stat().st_size <= 25 * 1024 * 1024:
            target = destination / path.name
            shutil.copy2(path, target)
            copied.append(str(target))
    return {"status": "OK", "source": str(source), "files": copied}


def latest_steady_archive(case_dir: Path) -> Path | None:
    history = case_dir / "steadyInitialization" / "history"
    candidates = [
        path
        for path in history.glob("run_*")
        if path.is_dir() and (path / "time_directories").is_dir()
    ] if history.is_dir() else []
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def detect_simulation_mode(case_dir: Path, requested_mode: str) -> tuple[str, dict[str, Any]]:
    """Resolve iteration-count RANS versus physical-time URANS evidence.

    Validation post-processing used to default to URANS, which could label a
    timed-out SIMPLE history as physical time. Explicit RANS/URANS requests are
    preserved; AUTO requires actual transient evidence.
    """
    requested = str(requested_mode or "AUTO").strip().upper()
    if requested in {"RANS", "URANS"}:
        return requested, {"requested": requested, "reason": "explicit"}
    positive_times = [value for value, _ in numeric_time_dirs(case_dir) if value > 0.0]
    staged = read_json(case_dir / "staged_run_status.json", {}) or {}
    fv_schemes = ""
    schemes_path = case_dir / "system" / "fvSchemes"
    if schemes_path.is_file():
        fv_schemes = schemes_path.read_text(encoding="utf-8", errors="replace")
    transient_scheme = bool(re.search(
        r"ddtSchemes\s*\{[^}]*default\s+(?:backward|CrankNicolson|Euler)",
        fv_schemes,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    staged_transient_evidence = bool(
        staged.get("transient_phases")
        or staged.get("transient_runner_returncode") is not None
        or str(staged.get("status") or "").upper().startswith("TRANSIENT_STAGE_")
    )
    transfer_evidence = bool(
        staged.get("transient_time_origin")
        or staged.get("steady_to_transient_continuity")
        or (staged.get("steady_transfer") or {}).get("transferred_to_transient_zero")
    )
    physical_time_evidence = bool(positive_times) and (
        transfer_evidence
        or staged_transient_evidence
        or (transient_scheme and max(positive_times) < 10.0)
    )
    mode = "URANS" if physical_time_evidence else "RANS"
    return mode, {
        "requested": requested,
        "resolved": mode,
        "positive_time_count": len(positive_times),
        "latest_positive_time": max(positive_times) if positive_times else None,
        "transient_scheme": transient_scheme,
        "transfer_evidence": transfer_evidence,
        "staged_transient_evidence": staged_transient_evidence,
        "reason": "physical_time_evidence" if physical_time_evidence else "no_physical_time_evidence",
    }


def prepare_steady_stage_results(
    case_dir: Path,
    out_dir: Path,
    *,
    project_root: Path,
    variant: str,
    run_openfoam_tools: bool,
    wall_profile_analysis: bool,
    velocity_profile_stations: list[float],
    velocity_profile_sample_points: int,
    simulation_mode: str,
    automatic_paraview_products: bool,
    include_paraview_animations: bool,
    paraview_maximum_frames: int,
    timeout_s: int,
    field_scale_mode: str,
    robust_percentiles: tuple[float, float],
    manual_scales: dict[str, tuple[float, float]],
    average_tail_samples: int,
) -> dict[str, Any]:
    """Expose the latest SIMPLE stage separately from physical URANS time."""
    stage_dir = out_dir / "RANS"
    stage_dir.mkdir(parents=True, exist_ok=True)
    archive = latest_steady_archive(case_dir)
    if archive is None and str(simulation_mode).upper() == "RANS":
        archive = case_dir
    if archive is None:
        report = {"status": "NOT_AVAILABLE", "reason": "steady_archive_missing"}
        (stage_dir / "stage_summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    copied: list[str] = []
    for name in (
        "aerodynamic_efficiency_steady.csv",
        "aerodynamic_efficiency_steady.png",
        "steady_transition_report.json",
        "stage_transfer_report.json",
    ):
        source = archive / name
        if source.is_file() and source.stat().st_size <= 25 * 1024 * 1024:
            target = stage_dir / name
            shutil.copy2(source, target)
            copied.append(str(target))
    records, force_sources = read_force_coefficient_history(archive, include_processor0=True)
    force_summary: dict[str, Any] = {"status": "NOT_AVAILABLE"}
    if records:
        force_history = pd.DataFrame(records).drop_duplicates(subset=["Time"], keep="last")
        force_history = force_history.sort_values("Time").reset_index(drop=True)
        tail_count = min(max(1, int(average_tail_samples)), len(force_history))
        force_window = force_history.tail(tail_count).copy()
        columns = [name for name in ("Cl", "Cd", "Cm") if name in force_window.columns]
        mean = force_window[columns].mean().to_frame("mean").T
        std = force_window[columns].std().to_frame("std").T
        if "Cl" in mean and "Cd" in mean:
            mean["L_D"] = mean["Cl"] / mean["Cd"].replace(0, np.nan)
        force_history.to_csv(stage_dir / "forceCoeffs_RANS_full_history.csv", index=False)
        force_window.to_csv(stage_dir / "forceCoeffs_RANS_averaging_window.csv", index=False)
        mean.to_csv(stage_dir / "forceCoeffs_RANS_mean.csv", index=False)
        std.to_csv(stage_dir / "forceCoeffs_RANS_std.csv", index=False)
        plot_force_coeffs(
            force_history,
            mean,
            stage_dir / "forceCoeffs_RANS_history.png",
            average_window=force_window,
            x_label="SIMPLE iteration",
            title="RANS coefficients: complete iteration history and final averaging window",
        )
        force_summary = {
            "status": "AVAILABLE",
            "time_semantics": "SIMPLE iteration counter; not physical seconds",
            "history_samples": int(len(force_history)),
            "averaging_tail_samples_requested": int(average_tail_samples),
            "averaging_tail_samples_used": int(tail_count),
            "averaging_start_iteration": float(force_window["Time"].min()),
            "averaging_end_iteration": float(force_window["Time"].max()),
            "mean": json_safe(mean.iloc[0].to_dict()),
            "std": json_safe(std.iloc[0].to_dict()),
            "source_files": [str(path) for path in force_sources],
        }
        (stage_dir / "RANS_force_summary.json").write_text(
            json.dumps(force_summary, indent=2) + "\n", encoding="utf-8"
        )
    paraview_case = (
        archive / "paraview_case"
        if (archive / "paraview_case" / "system" / "controlDict").is_file()
        else archive
    )
    rans_field_exports: dict[str, Any] = {"status": "DISABLED"}
    rans_wall_analysis: dict[str, Any] = {"status": "DISABLED"}
    if paraview_case.is_dir() and (run_openfoam_tools or wall_profile_analysis):
        if run_openfoam_tools:
            rans_field_exports = run_openfoam_post_exports(
                paraview_case,
                stage_dir,
                export_vtk=False,
                run_openfoam_postprocess=True,
                timeout_s=timeout_s,
                export_vtk_all_times=False,
                simulation_mode="RANS",
            )
        if wall_profile_analysis:
            try:
                rans_inputs = read_json(paraview_case / "case_input_summary.json", {}) or {}
                rans_wall_analysis = analyze_wall_boundary_layer(
                    project_root=project_root,
                    case_dir=paraview_case,
                    output_dir=stage_dir,
                    variant=variant,
                    run_openfoam_tools=run_openfoam_tools,
                    timeout_s=timeout_s,
                    stations_xc=velocity_profile_stations,
                    sample_points=velocity_profile_sample_points,
                    solver_module=str(rans_inputs.get("solver_module", "incompressibleFluid")),
                    simulation_mode="RANS",
                    include_temporal_separation_history=False,
                )
            except Exception as exc:
                rans_wall_analysis = {
                    "status": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                }
    automatic = (
        generate_automatic_paraview_products(
            paraview_case,
            stage_dir / "ParaView",
            maximum_frames=paraview_maximum_frames,
            timeout_s=timeout_s,
            time_semantics="SIMPLE iteration counter; not physical seconds",
            stage_label="RANS",
            field_scale_mode=field_scale_mode,
            robust_percentiles=robust_percentiles,
            manual_scales=manual_scales,
            include_animations=include_paraview_animations,
        )
        if automatic_paraview_products and (paraview_case / "system" / "controlDict").is_file()
        else {
            "status": "DISABLED" if not automatic_paraview_products else "NOT_AVAILABLE",
            "reason": "automatic_products_not_requested" if not automatic_paraview_products else "steady_paraview_case_missing",
        }
    )
    report = {
        "status": "AVAILABLE",
        "stage": "RANS/SIMPLE initialization",
        "time_semantics": "iteration counter, not physical seconds",
        "archive": str(archive),
        "paraview_case": str(paraview_case) if paraview_case.is_dir() else None,
        "copied_products": copied,
        "automatic_paraview_products": automatic,
        "openfoam_field_exports": rans_field_exports,
        "wall_boundary_layer_analysis": rans_wall_analysis,
        "force_summary": force_summary,
    }
    (stage_dir / "stage_summary.json").write_text(json.dumps(json_safe(report), indent=2) + "\n", encoding="utf-8")
    return report


def mirror_urans_stage_results(out_dir: Path, case_dir: Path) -> dict[str, Any]:
    """Provide an explicit URANS view while preserving legacy root outputs."""
    stage_dir = out_dir / "URANS"
    stage_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in (
        "forceCoeffs_raw.csv",
        "forceCoeffs_averaging_window.csv",
        "forceCoeffs_mean.csv",
        "forceCoeffs_std.csv",
        "Cl_Cd_Cm_history.png",
        "aerodynamic_efficiency.csv",
        "aerodynamic_efficiency.png",
        "solver_residuals.csv",
        "solver_residuals.png",
        "courant_history.csv",
        "courant_history.png",
        "deltaT_history.csv",
        "postprocess_window_manifest.json",
        "scalar_signal_inventory.json",
        "available_time_directories.csv",
        "written_field_inventory.csv",
        "wall_yplus_vs_xc.csv",
        "wall_yplus_vs_xc.png",
        "wall_cp_vs_xc.csv",
        "wall_cp_vs_xc.png",
        "wall_normal_velocity_profiles.csv",
        "wall_normal_velocity_profiles.png",
        "boundary_layer_thickness_comparison.csv",
        "boundary_layer_thickness_comparison.png",
        "wall_shear_stress_vs_xc.csv",
        "wall_shear_stress_vs_xc.png",
        "skin_friction_coefficient_vs_xc.csv",
        "skin_friction_coefficient_vs_xc.png",
        "separation_events.json",
        "separation_events.csv",
        "separation_overlay_cp_cf.png",
        "separation_summary.md",
    ):
        source = out_dir / name
        if source.is_file() and source.stat().st_size <= 25 * 1024 * 1024:
            target = stage_dir / name
            shutil.copy2(source, target)
            copied.append(str(target))
    report = {
        "status": "AVAILABLE" if copied else "NOT_AVAILABLE",
        "stage": "URANS/PIMPLE",
        "time_semantics": "physical seconds",
        "paraview_case": str(case_dir),
        "copied_products": copied,
        "compatibility_note": "Root result files are retained for existing workflows.",
    }
    (stage_dir / "stage_summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def run_openfoam_post_exports(
    case_dir: Path,
    out_dir: Path,
    export_vtk: bool,
    run_openfoam_postprocess: bool,
    timeout_s: int,
    export_vtk_all_times: bool = False,
    simulation_mode: str = "AUTO",
) -> list[dict[str, Any]]:
    logs_dir = out_dir / "openfoam_postprocess_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    latest_times = [t for t, _ in numeric_time_dirs(case_dir) if t > 0]
    if not latest_times:
        return [{"status": "SKIPPED", "reason": "no_positive_time_directories"}]
    latest_time, latest_dir = [
        item for item in numeric_time_dirs(case_dir) if item[0] > 0
    ][-1]

    def field_path(name: str) -> Path | None:
        for candidate in (latest_dir / name, latest_dir / f"{name}.gz"):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        return None

    primary_mtime = max(
        (path.stat().st_mtime for path in (field_path("U"), field_path("p")) if path),
        default=0.0,
    )
    if run_openfoam_postprocess:
        # Cp is written by the case's pressure function object. OpenFOAM
        # Foundation 14 does not register a `pressureCoefficient` shortcut for
        # `foamPostProcess -func`, so invoking it here creates a false failure.
        functions = ["yPlus", "wallShearStress", "vorticity", "Q"]
        if str(simulation_mode).upper() == "URANS":
            functions.insert(0, "CourantNo")
        result_fields = {"CourantNo": "Co"}
        for func in functions:
            result_field = result_fields.get(func, func)
            existing = field_path(result_field)
            if existing is not None and existing.stat().st_mtime + 1.0 >= primary_mtime:
                results.append({
                    "status": "SKIPPED",
                    "reason": "latest_field_already_current",
                    "function": func,
                    "field": result_field,
                    "time": float(latest_time),
                    "path": str(existing),
                })
                continue
            time_args = [] if export_vtk_all_times else ["-latestTime"]
            results.append(run_optional_command_variants([
                ["foamPostProcess", "-solver", "incompressibleFluid", "-func", func, *time_args],
                ["postProcess", "-solver", "incompressibleFluid", "-func", func, *time_args],
                ["foamPostProcess", "-func", func, *time_args],
                ["postProcess", "-func", func, *time_args],
            ], case_dir, logs_dir / f"log.postProcess_{func}", timeout_s))
    if export_vtk:
        vtk_cmd = ["foamToVTK"] if export_vtk_all_times else ["foamToVTK", "-latestTime"]
        selected_fields = available_postprocess_fields(case_dir)
        if selected_fields:
            vtk_cmd += ["-fields", f"({' '.join(selected_fields)})"]
        results.append(run_optional_command(vtk_cmd, case_dir, logs_dir / "log.foamToVTK", timeout_s))
    return results


def launch_path(path: Path) -> dict[str, Any]:
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer.exe", str(path)])
            return {"status": "OPEN_REQUESTED", "target": str(path), "method": "explorer.exe"}
        if shutil.which("wslpath") and shutil.which("explorer.exe"):
            proc = subprocess.run(["wslpath", "-w", str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if proc.returncode == 0 and proc.stdout.strip():
                subprocess.Popen(["explorer.exe", proc.stdout.strip()])
                return {"status": "OPEN_REQUESTED", "target": str(path), "method": "wsl-explorer"}
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)])
            return {"status": "OPEN_REQUESTED", "target": str(path), "method": "xdg-open"}
    except Exception as exc:
        return {"status": "OPEN_FAILED", "target": str(path), "error": str(exc)}
    return {"status": "OPEN_SKIPPED", "target": str(path), "reason": "no_known_gui_opener"}


def launch_paraview(case_dir: Path) -> dict[str, Any]:
    try:
        return launch_paraview_case(Path(case_dir).resolve())
    except Exception as exc:
        return {"status": "OPEN_FAILED", "case_dir": str(Path(case_dir).resolve()), "error": f"{type(exc).__name__}: {exc}"}


def write_visualization_guide(case_dir: Path, out_dir: Path, export_results: list[dict[str, Any]], export_vtk_all_times: bool = False) -> None:
    vtk_dir = case_dir / "VTK"
    lines = [
        "Ram-air CFD 2D Post-processing and Visualization Guide",
        "======================================================",
        "",
        "Recommended visual tools:",
        "- ParaView/paraFoam for U, p, Cp, nut, nuTilda and derived OpenFOAM fields.",
        "- foamToVTK for portable VTK export if ParaView cannot open the case directly.",
        "- Python/matplotlib outputs in this folder for force coefficients, residuals and Courant number.",
        "",
        "Manual ParaView commands from Ubuntu/WSL:",
        f"cd \"{case_dir}\"",
        "paraFoam -builtin",
        "",
        "Optional VTK export commands:",
        f"cd \"{case_dir}\"",
        "postProcess -func yPlus -latestTime",
        "postProcess -func wallShearStress -latestTime",
        "postProcess -func vorticity -latestTime",
        "postProcess -func Q -latestTime",
        "postProcess -func CourantNo -latestTime",
        "foamToVTK -latestTime",
        "foamToVTK        # exports all written time directories; can consume much more disk space",
        "",
        "What to inspect:",
        "- U magnitude and vectors: wake development, recirculation and inlet/farfield consistency.",
        "- p: pressure distribution around LE, suction side, pressure side and TE.",
        "- Cp: nondimensional static pressure coefficient written by OpenFOAM's pressure function object.",
        "- yPlus: whether near-wall resolution matches the turbulence-wall treatment.",
        "- wallShearStress: wall load/shear consistency and separation clues.",
        "- vorticity magnitude: shear layers, wake roll-up and recirculation structures.",
        "- Q: rotation-dominated structures (Q>0) and strain-dominated regions (Q<0).",
        "- Co: cell Courant field at each normal URANS field-write time; inspect its maxima to locate the cells limiting deltaT.",
        "- forceCoeffs: Cl/Cd/Cm history and whether the values settle.",
        "- residuals: initial residual decay/plateau for U, p and nuTilda.",
        "",
        f"VTK directory expected after foamToVTK: {vtk_dir}",
        f"Current automated VTK mode: {'all written times' if export_vtk_all_times else 'latestTime only'}",
        "",
        "OpenFOAM export command results:",
    ]
    for result in export_results:
        lines.append(f"- {result}")
    (out_dir / "visualization_guide.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_ross_placeholders(case_root: Path) -> None:
    root = cfd_root(case_root) / "reference_data" / "Ross"
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "ross_reference_manifest.json"
    if not manifest.exists():
        manifest.write_text(json.dumps({"source": "Ross Computational Aerodynamics in the Design and Analysis of Ram-Air-Inflated Wings", "airfoil": "NASA LS1-0417", "standard_inlet_percent_c": 8.4, "minimum_inlet_percent_c": 4.0, "navier_stokes_re": 4.0e6, "cp_alpha_deg": 4.0, "clean_validation_re": 6.0e6, "clean_validation_alpha_pressure_deg": 4.17, "digitized": False}, indent=2), encoding="utf-8")


def postprocess(
    case_root: Path,
    variant: str,
    alpha: float,
    average_from_fraction: float,
    export_vtk: bool = False,
    export_vtk_all_times: bool = False,
    run_openfoam_postprocess: bool = False,
    openfoam_postprocess_timeout_s: int = 300,
    open_results_folder: bool = False,
    open_paraview: bool = False,
    wall_profile_analysis: bool = True,
    velocity_profile_stations: list[float] | None = None,
    velocity_profile_sample_points: int = 40,
    automatic_paraview_products: bool = False,
    include_paraview_animations: bool = True,
    paraview_animations_only: bool = False,
    paraview_maximum_frames: int = 24,
    field_scale_mode: str = "exact",
    robust_percentiles: tuple[float, float] = (1.0, 99.0),
    manual_scales: dict[str, tuple[float, float]] | None = None,
    paraview_time_range_s: tuple[float, float] | None = None,
    direct_case_dir: Path | None = None,
    direct_output_dir: Path | None = None,
    simulation_mode: str = "URANS",
    rans_average_tail_samples: int = 500,
) -> Path:
    postprocess_started = time.monotonic()
    stage_timings: dict[str, float] = {}
    case_root = project_root_from_case_root(case_root).resolve()
    manual_scales = dict(manual_scales or {})
    safe = f"alpha_{alpha:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    case_dir = (
        Path(direct_case_dir).resolve()
        if direct_case_dir is not None
        else cfd_root(case_root) / "openfoam_cases" / variant / safe
    )
    out_dir = (
        Path(direct_output_dir).resolve()
        if direct_output_dir is not None
        else cfd_root(case_root) / "results" / variant / safe
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    for obsolete_name in (
        "deltaT_history.png",
        "separation_time_history.png",
        "separation_time_history.csv",
        "reverse_flow_occupancy.png",
        "reverse_flow_occupancy.csv",
    ):
        for obsolete in out_dir.rglob(obsolete_name):
            obsolete.unlink(missing_ok=True)
    ensure_ross_placeholders(case_root)
    requested_simulation_mode = simulation_mode
    simulation_mode, simulation_mode_evidence = detect_simulation_mode(
        case_dir, requested_simulation_mode
    )
    if paraview_animations_only:
        if not case_dir.exists():
            raise FileNotFoundError(f"OpenFOAM case not found: {case_dir}")
        animation_reports: dict[str, Any] = {}
        archive = latest_steady_archive(case_dir)
        if archive is not None:
            rans_case = (
                archive / "paraview_case"
                if (archive / "paraview_case" / "system" / "controlDict").is_file()
                else archive
            )
            if (rans_case / "system" / "controlDict").is_file():
                animation_reports["RANS"] = generate_automatic_paraview_products(
                    rans_case,
                    out_dir / "RANS" / "ParaView",
                    maximum_frames=max(2, int(paraview_maximum_frames)),
                    timeout_s=max(30, int(openfoam_postprocess_timeout_s)),
                    time_semantics="SIMPLE iteration counter; not physical seconds",
                    stage_label="RANS",
                    field_scale_mode=field_scale_mode,
                    robust_percentiles=robust_percentiles,
                    manual_scales=manual_scales,
                    include_animations=True,
                )
        if str(simulation_mode).upper() == "URANS":
            animation_reports["URANS"] = generate_automatic_paraview_products(
                case_dir,
                out_dir / "URANS" / "ParaView",
                maximum_frames=max(2, int(paraview_maximum_frames)),
                timeout_s=max(30, int(openfoam_postprocess_timeout_s)),
                time_semantics="physical seconds",
                stage_label="URANS",
                field_scale_mode=field_scale_mode,
                robust_percentiles=robust_percentiles,
                manual_scales=manual_scales,
                time_range_s=paraview_time_range_s,
                include_animations=True,
            )
        report = {
            "schema_version": 1,
            "status": "COMPLETED",
            "simulation_mode": simulation_mode,
            "simulation_mode_evidence": simulation_mode_evidence,
            "scope": "animations_only",
            "field_policy": (
                "OpenFOAMReader reads existing U/p/Cp/vorticity/Q fields directly; "
                "no VTK database or unrelated final diagnostics are reconstructed."
            ),
            "stages": animation_reports,
            "elapsed_s": time.monotonic() - postprocess_started,
        }
        (out_dir / "animation_postprocess_report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        return out_dir
    if not case_dir.exists():
        summary = {
            "variant": variant,
            "alpha_deg": alpha,
            "status": "NOT_RUN_YET",
            "reason": "openfoam_case_missing",
            "case_dir": str(case_dir),
        }
    else:
        case_inputs = read_json(case_dir / "case_input_summary.json", {}) or {}
        stage_started = time.monotonic()
        reconstruction = reconstruct_pending_parallel_times(
            case_dir=case_dir,
            out_dir=out_dir,
            all_times=export_vtk_all_times,
            timeout_s=max(30, int(openfoam_postprocess_timeout_s)),
        )
        stage_timings["parallel_reconstruction_s"] = time.monotonic() - stage_started
        times = numeric_time_dirs(case_dir)
        positive_times = [float(value) for value, _ in times if float(value) > 0.0]
        temporal_animation = {
            "status": "READY" if len(positive_times) >= 2 else "INSUFFICIENT_WRITTEN_TIMES",
            "positive_time_count": int(len(positive_times)),
            "first_positive_time_s": min(positive_times) if positive_times else None,
            "last_positive_time_s": max(positive_times) if positive_times else None,
            "message": (
                "ParaView can animate the reconstructed OpenFOAM time directories."
                if len(positive_times) >= 2
                else "At least two positive reconstructed time directories are required for temporal animation. "
                     "Continue the solver long enough to cross additional field write intervals."
            ),
        }
        time_table = pd.DataFrame([{"time": t, "directory": p.name} for t, p in times])
        if not time_table.empty:
            time_table.to_csv(out_dir / "available_time_directories.csv", index=False)
        residuals, courant, solver_meta = parse_solver_log(case_dir)
        if not residuals.empty:
            residuals.to_csv(out_dir / "solver_residuals.csv", index=False)
            plot_residuals(residuals, out_dir / "solver_residuals.png")
        if not courant.empty and str(simulation_mode).upper() == "URANS":
            courant.to_csv(out_dir / "courant_history.csv", index=False)
            _delta_t_table(courant).to_csv(out_dir / "deltaT_history.csv", index=False)
            maximum_delta_t_s = case_inputs.get("maxDeltaT_s")
            try:
                maximum_delta_t_s = float(maximum_delta_t_s)
            except (TypeError, ValueError):
                maximum_delta_t_s = None
            plot_courant(
                courant,
                out_dir / "courant_history.png",
                maximum_delta_t_s=maximum_delta_t_s,
            )
            (out_dir / "deltaT_history.png").unlink(missing_ok=True)
        stage_started = time.monotonic()
        export_results = run_openfoam_post_exports(
            case_dir,
            out_dir,
            export_vtk=export_vtk,
            run_openfoam_postprocess=run_openfoam_postprocess,
            timeout_s=max(10, int(openfoam_postprocess_timeout_s)),
            export_vtk_all_times=export_vtk_all_times,
            simulation_mode=simulation_mode,
        )
        stage_timings["openfoam_field_exports_s"] = time.monotonic() - stage_started
        wall_analysis: dict[str, Any] = {"status": "DISABLED"}
        if wall_profile_analysis:
            stage_started = time.monotonic()
            try:
                wall_analysis = analyze_wall_boundary_layer(
                    project_root=case_root,
                    case_dir=case_dir,
                    output_dir=out_dir,
                    variant=variant,
                    run_openfoam_tools=run_openfoam_postprocess,
                    timeout_s=max(10, int(openfoam_postprocess_timeout_s)),
                    stations_xc=list(velocity_profile_stations or [0.1, 0.3, 0.6, 0.9]),
                    sample_points=max(10, int(velocity_profile_sample_points)),
                    solver_module=str(case_inputs.get("solver_module", "incompressibleFluid")),
                    simulation_mode=simulation_mode,
                    include_temporal_separation_history=False,
                )
            except Exception as exc:
                wall_analysis = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            stage_timings["wall_profile_analysis_s"] = time.monotonic() - stage_started
        field_inventory = derived_field_inventory(
            case_dir,
            out_dir,
            simulation_mode=simulation_mode,
        )
        pyfoam_diagnostics = copy_pyfoam_diagnostics(case_dir, out_dir)
        scalar_signals = scalar_signal_inventory(case_dir)
        (out_dir / "scalar_signal_inventory.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "path_base": "case_directory",
                    "signals": scalar_signals,
                    "purge_write_scope": "volume_time_directories_only",
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        # A zero-byte .foam marker lets ParaView's built-in OpenFOAM reader
        # discover every written time without duplicating data.
        paraview_marker = case_dir / f"{case_dir.name}.foam"
        paraview_marker.touch(exist_ok=True)
        stage_started = time.monotonic()
        rans_stage = prepare_steady_stage_results(
            case_dir,
            out_dir,
            project_root=case_root,
            variant=variant,
            run_openfoam_tools=run_openfoam_postprocess,
            wall_profile_analysis=wall_profile_analysis,
            velocity_profile_stations=list(
                velocity_profile_stations or [0.1, 0.3, 0.6, 0.9]
            ),
            velocity_profile_sample_points=max(10, int(velocity_profile_sample_points)),
            simulation_mode=simulation_mode,
            automatic_paraview_products=automatic_paraview_products,
            include_paraview_animations=include_paraview_animations,
            paraview_maximum_frames=max(2, int(paraview_maximum_frames)),
            timeout_s=max(30, int(openfoam_postprocess_timeout_s)),
            field_scale_mode=field_scale_mode,
            robust_percentiles=robust_percentiles,
            manual_scales=manual_scales,
            average_tail_samples=max(1, int(rans_average_tail_samples)),
        )
        stage_timings["rans_products_s"] = time.monotonic() - stage_started
        stage_started = time.monotonic()
        automatic_products = (
            generate_automatic_paraview_products(
                case_dir,
                out_dir / "URANS" / "ParaView",
                maximum_frames=max(2, int(paraview_maximum_frames)),
                timeout_s=max(30, int(openfoam_postprocess_timeout_s)),
                time_semantics="physical seconds",
                stage_label="URANS",
                field_scale_mode=field_scale_mode,
                robust_percentiles=robust_percentiles,
                manual_scales=manual_scales,
                time_range_s=paraview_time_range_s,
                include_animations=include_paraview_animations,
            )
            if automatic_paraview_products
            and str(simulation_mode).upper() == "URANS"
            else {"status": "DISABLED", "reason": "automatic_products_not_requested"}
        )
        stage_timings["urans_paraview_products_s"] = time.monotonic() - stage_started
        open_requests: list[dict[str, Any]] = []
        if open_results_folder:
            open_requests.append(launch_path(out_dir))
        if open_paraview:
            open_requests.append(launch_paraview(case_dir))
        write_visualization_guide(case_dir, out_dir, export_results, export_vtk_all_times=export_vtk_all_times)
        try:
            df = read_force_coeffs(case_dir)
            win, mean, std = summarize_force_coeffs(df, average_from_fraction)
            window_manifest = dict(win.attrs.get("window_manifest") or {})
            df.to_csv(out_dir / "forceCoeffs_raw.csv", index=False)
            win.to_csv(out_dir / "forceCoeffs_averaging_window.csv", index=False)
            mean.to_csv(out_dir / "forceCoeffs_mean.csv", index=False)
            std.to_csv(out_dir / "forceCoeffs_std.csv", index=False)
            (out_dir / "postprocess_window_manifest.json").write_text(
                json.dumps(window_manifest, indent=2) + "\n", encoding="utf-8"
            )
            plot_force_coeffs(
                df,
                mean,
                out_dir / "Cl_Cd_Cm_history.png",
                average_window=win,
            )
            aerodynamic_efficiency = write_aerodynamic_efficiency_products(
                win,
                out_dir / "aerodynamic_efficiency.csv",
                out_dir / "aerodynamic_efficiency.png",
                mean_from_fraction=0.0,
            )
            average_start = float(win["Time"].min()) if "Time" in win.columns and not win.empty else None
            average_end = float(win["Time"].max()) if "Time" in win.columns and not win.empty else None
            (out_dir / "average_from_fraction_explanation.txt").write_text(
                "average_from_fraction defines the final fraction of the available force history used for mean/std coefficients.\n"
                "For example, 0.6 means: ignore the first 60% of the simulated time interval and average only the last 40%.\n"
                "This is intended to avoid startup transients. It is not a physical model parameter.\n"
                f"Configured average_from_fraction: {average_from_fraction}\n"
                f"Averaging window start time: {average_start}\n"
                f"Averaging window end time: {average_end}\n",
                encoding="utf-8",
            )
            run_status = read_json(case_dir / "run_status.json", {}) or {}
            rs = str(run_status.get("status", "")).upper()
            partial_statuses = {"TIMEOUT_PARTIAL", "STOPPED_PARTIAL", "STOPPED_FORCED_PARTIAL"}
            status = "PROCESSED_PARTIAL" if rs in partial_statuses else "PROCESSED"
            summary = {
                "variant": variant,
                "alpha_deg": alpha,
                "average_from_fraction": average_from_fraction,
                "average_from_fraction_explanation": "Ignore the first fraction of the available force history and average the remaining final window; 0.6 means average the last 40%.",
                "averaging_window": {"start_time": average_start, "end_time": average_end},
                "automatic_window_selection": window_manifest,
                "mean": mean.iloc[0].to_dict(),
                "std": std.iloc[0].to_dict(),
                "aerodynamic_efficiency": aerodynamic_efficiency,
                "status": status,
                "run_status": run_status,
                "case_inputs": {
                    "velocity_m_s": case_inputs.get("velocity_m_s"),
                    "rho_kg_m3": case_inputs.get("rho_kg_m3"),
                    "chord_m": case_inputs.get("chord_m"),
                    "spanwise_thickness_m": case_inputs.get("spanwise_thickness_m"),
                    "reference_area_m2": case_inputs.get("reference_area_m2"),
                    "force_coefficients": case_inputs.get("force_coefficients"),
                    "mach_reynolds_consistency_warning": case_inputs.get("mach_reynolds_consistency_warning"),
                },
                "solver_log": solver_meta,
                "time_directories": [p.name for _, p in times],
                "temporal_animation": temporal_animation,
                "parallel_reconstruction": reconstruction,
                "field_inventory": field_inventory,
                "pyfoam_diagnostics": pyfoam_diagnostics,
                "scalar_signal_inventory": scalar_signals,
                "paraview_marker": str(paraview_marker),
                "openfoam_postprocess": export_results,
                "wall_boundary_layer_analysis": wall_analysis,
                "automatic_paraview_products": automatic_products,
                "rans_stage": rans_stage,
                "open_requests": open_requests,
            }
        except FileNotFoundError as exc:
            run_status = read_json(case_dir / "run_status.json", {}) or {}
            rs = str(run_status.get("status", "")).upper()
            if rs in {"TIMEOUT_PARTIAL", "STOPPED_PARTIAL", "STOPPED_FORCED_PARTIAL"}:
                status = "TIMEOUT_PARTIAL_NO_FORCECOEFFS"
                reason = "timeout_partial_forceCoeffs_missing"
            elif rs in {"RUN_FAILED", "TIMEOUT"}:
                status = "RUN_FAILED"
                reason = rs.lower()
            elif rs == "RUN_COMPLETED":
                status = "FORCECOEFFS_MISSING_AFTER_RUN"
                reason = "solver_completed_without_forceCoeffs"
            else:
                status = "NOT_RUN_YET"
                reason = "forceCoeffs_missing"
            summary = {
                "variant": variant,
                "alpha_deg": alpha,
                "status": status,
                "reason": reason,
                "case_dir": str(case_dir),
                "run_status": run_status,
                "case_inputs": {
                    "velocity_m_s": case_inputs.get("velocity_m_s"),
                    "rho_kg_m3": case_inputs.get("rho_kg_m3"),
                    "chord_m": case_inputs.get("chord_m"),
                    "spanwise_thickness_m": case_inputs.get("spanwise_thickness_m"),
                    "reference_area_m2": case_inputs.get("reference_area_m2"),
                    "force_coefficients": case_inputs.get("force_coefficients"),
                    "mach_reynolds_consistency_warning": case_inputs.get("mach_reynolds_consistency_warning"),
                },
                "solver_log": solver_meta,
                "time_directories": [p.name for _, p in times],
                "temporal_animation": temporal_animation,
                "parallel_reconstruction": reconstruction,
                "field_inventory": field_inventory,
                "pyfoam_diagnostics": pyfoam_diagnostics,
                "scalar_signal_inventory": scalar_signals,
                "paraview_marker": str(paraview_marker),
                "openfoam_postprocess": export_results,
                "wall_boundary_layer_analysis": wall_analysis,
                "automatic_paraview_products": automatic_products,
                "rans_stage": rans_stage,
                "open_requests": open_requests,
                "message": str(exc),
            }
        except ValueError as exc:
            summary = {
                "variant": variant,
                "alpha_deg": alpha,
                "status": "PARSE_FAILED",
                "case_dir": str(case_dir),
                "error": str(exc),
            }
        except Exception as exc:
            summary = {
                "variant": variant,
                "alpha_deg": alpha,
                "status": "POSTPROCESS_ERROR",
                "case_dir": str(case_dir),
                "error": str(exc),
            }
    if case_dir.exists() and str(simulation_mode).upper() == "URANS":
        urans_stage = mirror_urans_stage_results(out_dir, case_dir)
    else:
        urans_stage = {
            "status": "NOT_AVAILABLE",
            "reason": (
                "openfoam_case_missing"
                if not case_dir.exists()
                else "NO_PHYSICAL_URANS_TIME_EVIDENCE"
            ),
            "time_semantics": "not available; SIMPLE iterations remain under RANS",
        }
        urans_dir = out_dir / "URANS"
        urans_dir.mkdir(parents=True, exist_ok=True)
        (urans_dir / "stage_summary.json").write_text(
            json.dumps(urans_stage, indent=2) + "\n", encoding="utf-8"
        )
    summary["stages"] = {
        "RANS": summary.get("rans_stage", {"status": "NOT_AVAILABLE"}),
        "URANS": urans_stage,
    }
    summary["simulation_mode"] = simulation_mode
    summary["simulation_mode_evidence"] = simulation_mode_evidence
    summary["validation_update_mode"] = "manual_selection_in_application"
    stage_timings["total_postprocess_s"] = time.monotonic() - postprocess_started
    summary["stage_timings_s"] = stage_timings
    (out_dir / "postprocess_stage_timings.json").write_text(
        json.dumps(json_safe(stage_timings), indent=2) + "\n", encoding="utf-8"
    )
    summary_path = out_dir / "case_summary.json"
    summary_path.write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    product_candidates = [
        path
        for path in out_dir.iterdir()
        if path.name != "postprocess_manifest.json"
    ]
    separation = (
        summary.get("wall_boundary_layer_analysis", {}).get("separation", {})
        if isinstance(summary.get("wall_boundary_layer_analysis"), dict)
        else {}
    )
    write_postprocess_manifest(
        out_dir,
        run_id=str(summary.get("run_id") or variant),
        mode=simulation_mode,
        products=product_candidates,
        errors=(
            [str(summary.get("error") or summary.get("reason"))]
            if str(summary.get("status")) in {
                "POSTPROCESS_ERROR",
                "PARSE_FAILED",
                "RUN_FAILED",
                "FORCECOEFFS_MISSING_AFTER_RUN",
            }
            else []
        ),
        metadata={
            "case_summary": summary_path.name,
            "case_reference": os.path.relpath(case_dir, out_dir).replace("\\", "/"),
            "separation_method_version": separation.get("separation_method_version"),
            "separation_status": separation.get("status", "NOT_AVAILABLE"),
            "separation_confidence": separation.get("confidence", "UNRESOLVED"),
            "separation_event_count": len(separation.get("events") or []),
        },
        regeneration_commands={
            group: [sys.executable, *sys.argv]
            for group in (
                "scalar_histories",
                "statistics_convergence",
                "surface_plots",
                "field_images",
                "animations",
                "paraview",
                "technical_files",
            )
        },
    )
    (out_dir / "README_results.md").write_text(
        "# Results folder\n\n"
        "`case_summary.json` reports PROCESSED, PROCESSED_PARTIAL, TIMEOUT_PARTIAL_NO_FORCECOEFFS, NOT_RUN_YET, or POSTPROCESS_ERROR.\n\n"
        "- `forceCoeffs_*.csv/png`: aerodynamic coefficient histories when OpenFOAM wrote forceCoeffs.\n"
        "- `aerodynamic_efficiency.csv/png`: Cl/Cd history over the same stabilized averaging window.\n"
        "- `solver_residuals.csv/png`: residual history parsed from `log.foamRun`/`log.pimpleFoam`.\n"
        "- `courant_history.csv/png`: Courant number history parsed from solver log.\n"
        "- `deltaT_history.csv/png`: complete adaptive deltaT history with the configured maxDeltaT ceiling.\n"
        "- `postprocess_window_manifest.json`: automatic final continuous force window and its evidence.\n"
        "- `scalar_signal_inventory.json`: force, probe, residual and Courant sources retained outside volume-field purge.\n"
        "- `available_time_directories.csv`: written OpenFOAM time folders available for field inspection.\n"
        "- `written_field_inventory.csv`: availability of U, p, Cp, Co, turbulence, yPlus, wallShearStress, vorticity and Q at each saved time.\n"
        "- `wall_yplus_vs_xc.csv/png`: first-cell wall y+ versus x/c, separated into upper/lower surfaces.\n"
        "- `wall_cp_vs_xc.csv/png`: OpenFOAM Cp on upper/lower wall faces versus x/c.\n"
        "- `wall_shear_stress_vs_xc.csv/png`: raw and filtered tangential wall shear ordered by face connectivity.\n"
        "- `skin_friction_coefficient_vs_xc.csv/png`: kinematic Cf and branch identity.\n"
        "- `separation_events.json/csv` and `separation_overlay_cp_cf.png`: persistent separation/reattachment events with confidence and exclusions.\n"
        "- `wall_normal_velocity_profiles.csv/png`: real OpenFOAM velocity samples normal to the wall.\n"
        "- `boundary_layer_thickness_comparison.csv/png`: numerical delta99 compared with the flat-plate estimate and prism-stack height.\n"
        "- `PyFoamPlots/`: residual, Cl and Cd/Cm diagnostics from the PyFoam-managed run.\n"
        "- `RANS/`: stationary SIMPLE products with iteration-count time semantics.\n"
        "- `URANS/`: transient PIMPLE products with physical-second time semantics.\n"
        "- `URANS/ParaView/`: optional Cp and cell-Courant close-ups plus bounded U/Cp animations rendered directly from the OpenFOAM reader.\n"
        "- `visualization_guide.txt`: ParaView/foamToVTK commands for U, p, Cp, Co, yPlus and derived fields.\n",
        encoding="utf-8",
    )
    return out_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Post-process ram-air 2D OpenFOAM forceCoeffs and prepare Ross-comparison outputs.")
    p.add_argument("--case-root", type=Path, required=True)
    p.add_argument(
        "--case-dir",
        type=Path,
        help="Direct OpenFOAM case override, used by Validation Lab checkpoints.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        help="Separate output package for a direct Validation Lab case.",
    )
    p.add_argument("--variant", required=True)
    p.add_argument("--alpha", type=float, required=True)
    p.add_argument("--average-from-fraction", type=float, default=0.6)
    p.add_argument(
        "--rans-average-tail-samples", type=int, default=500,
        help="Final SIMPLE samples used for RANS means; the complete iteration history remains plotted.",
    )
    p.add_argument("--export-vtk", action="store_true", help="Run foamToVTK -latestTime when OpenFOAM utilities are available.")
    p.add_argument("--export-vtk-all-times", action="store_true", help="With --export-vtk, export every written OpenFOAM time directory instead of latestTime only.")
    p.add_argument("--run-openfoam-postprocess", action="store_true", help="Run postProcess CourantNo/yPlus/wallShearStress/vorticity/Q -latestTime when available.")
    p.add_argument("--openfoam-postprocess-timeout-s", type=int, default=300)
    p.add_argument("--open-results-folder", action="store_true", help="Open the results folder after post-processing when a GUI opener is available.")
    p.add_argument("--open-paraview", action="store_true", help="Launch paraFoam/paraview for the OpenFOAM case when available.")
    p.add_argument("--wall-profile-analysis", action=argparse.BooleanOptionalAction, default=True, help="Build y+(x/c) and wall-normal velocity-profile products when real fields are available.")
    p.add_argument("--velocity-profile-stations", type=float, nargs="+", default=[0.1, 0.3, 0.6, 0.9], help="x/c stations sampled on upper and lower surfaces.")
    p.add_argument("--velocity-profile-sample-points", type=int, default=40)
    p.add_argument("--automatic-paraview-products", action="store_true", help="Render a Cp close-up plus bounded velocity and Cp animations with pvbatch.")
    p.add_argument(
        "--include-paraview-animations",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include bounded frame sequences and videos; disable for the fast final-state post-process.",
    )
    p.add_argument(
        "--paraview-animations-only",
        action="store_true",
        help="Skip scalar/final-field reconstruction and render only the bounded ParaView animations.",
    )
    p.add_argument("--paraview-maximum-frames", type=int, default=24, help="Maximum number of written times rendered per automatic animation.")
    p.add_argument(
        "--paraview-time-range-s",
        type=float,
        nargs=2,
        metavar=("START", "END"),
        help="Optional physical-time interval rendered for URANS animations.",
    )
    p.add_argument(
        "--field-scale-mode",
        choices=("exact", "robust", "manual"),
        default="exact",
    )
    p.add_argument(
        "--robust-percentiles",
        type=float,
        nargs=2,
        default=(1.0, 99.0),
        metavar=("LOW", "HIGH"),
    )
    p.add_argument("--manual-cp-range", type=float, nargs=2)
    p.add_argument("--manual-u-range", type=float, nargs=2)
    p.add_argument(
        "--simulation-mode", choices=("AUTO", "RANS", "URANS"), default="AUTO",
        help="AUTO separates SIMPLE iteration histories from physical URANS time using case evidence.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out = postprocess(
        args.case_root,
        args.variant,
        args.alpha,
        args.average_from_fraction,
        export_vtk=args.export_vtk,
        export_vtk_all_times=args.export_vtk_all_times,
        run_openfoam_postprocess=args.run_openfoam_postprocess,
        openfoam_postprocess_timeout_s=args.openfoam_postprocess_timeout_s,
        open_results_folder=args.open_results_folder,
        open_paraview=args.open_paraview,
        wall_profile_analysis=args.wall_profile_analysis,
        velocity_profile_stations=args.velocity_profile_stations,
        velocity_profile_sample_points=args.velocity_profile_sample_points,
        automatic_paraview_products=args.automatic_paraview_products,
        include_paraview_animations=args.include_paraview_animations,
        paraview_animations_only=args.paraview_animations_only,
        paraview_maximum_frames=args.paraview_maximum_frames,
        paraview_time_range_s=(
            tuple(args.paraview_time_range_s)
            if args.paraview_time_range_s is not None else None
        ),
        field_scale_mode=args.field_scale_mode,
        robust_percentiles=tuple(args.robust_percentiles),
        manual_scales={
            name: tuple(bounds)
            for name, bounds in {
                "Cp": args.manual_cp_range,
                "U": args.manual_u_range,
            }.items()
            if bounds is not None
        },
        direct_case_dir=args.case_dir,
        direct_output_dir=args.output_dir,
        rans_average_tail_samples=max(1, int(args.rans_average_tail_samples)),
        simulation_mode=args.simulation_mode,
    )
    print(f"Post-processing outputs in: {out.resolve()}")


if __name__ == "__main__":
    main()
