#!/usr/bin/env python3
"""Oscillatory-signal review for one real Validation Lab URANS run."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from openfoam_history import read_force_coefficient_history

from ramair_2d_convergence_analysis import (
    signal_summary,
    stationarity_blocks,
    welch_spectrum,
)
from ramair_2d_study_registry import read_json, utc_stamp, write_json_atomic


URANS_REVIEW_STATES = {
    "URANS_REVIEW_REQUIRED",
    "URANS_ACCEPTED",
    "URANS_EXTENSION_REQUIRED",
    "URANS_REJECTED",
    "URANS_PARTIAL",
}
URANS_DECISIONS = {
    "accept": "URANS_ACCEPTED",
    "extend": "URANS_EXTENSION_REQUIRED",
    "reject": "URANS_REJECTED",
    "publish-partial": "URANS_PARTIAL_REVIEWED",
}


def _column(frame: pd.DataFrame, *names: str) -> str:
    lowered = {str(column).lower(): str(column) for column in frame.columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    raise KeyError(f"Missing required signal column; expected one of {names}")


def _sampling_frame(
    frame: pd.DataFrame,
    sampling_start_s: float,
    sampling_end_s: float | None = None,
) -> pd.DataFrame:
    time_column = _column(frame, "time_s", "time", "Time")
    selected = frame.copy()
    selected[time_column] = pd.to_numeric(
        selected[time_column], errors="coerce"
    )
    selected = selected[
        np.isfinite(selected[time_column])
        & (selected[time_column] >= float(sampling_start_s))
    ].sort_values(time_column)
    if sampling_end_s is not None:
        selected = selected[
            selected[time_column] <= float(sampling_end_s)
        ]
    if selected.empty:
        raise ValueError("No real samples exist in the accepted stage-E window")
    return selected


def review_urans_signals(
    frame: pd.DataFrame,
    *,
    sampling_start_s: float,
    sampling_end_s: float | None = None,
    chord_m: float,
    velocity_m_s: float,
    reference_length_m: float | None = None,
    sampling_stage: str = "E",
    minimum_cycles: int = 10,
    preferred_cycles: int = 20,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate a uniform-dt stage, preferring production stage E."""
    limits = {
        "stationarity_mean_percent": 1.0,
        "stationarity_rms_percent": 5.0,
    }
    limits.update(thresholds or {})
    sampled = _sampling_frame(
        frame,
        sampling_start_s,
        sampling_end_s,
    )
    time_column = _column(sampled, "time_s", "time", "Time")
    time_s = pd.to_numeric(sampled[time_column], errors="coerce").to_numpy()
    signals: dict[str, Any] = {}
    hard_failures: list[str] = []
    for canonical, candidates in {
        "Cl": ("Cl", "CL"),
        "Cd": ("Cd", "CD"),
        "Cm": ("Cm", "CM"),
    }.items():
        try:
            column = _column(sampled, *candidates)
        except KeyError:
            hard_failures.append(f"MISSING_{canonical.upper()}_SIGNAL")
            continue
        values = pd.to_numeric(sampled[column], errors="coerce").to_numpy()
        finite = np.isfinite(time_s) & np.isfinite(values)
        if np.count_nonzero(finite) < 16:
            hard_failures.append(f"INSUFFICIENT_{canonical.upper()}_SAMPLES")
            continue
        try:
            from ramair_frequency_analysis import uniform_signals
            spectral_time, spectral_signals, resampling = uniform_signals(time_s[finite], {canonical:values[finite]})
            spectrum = welch_spectrum(
                spectral_time,
                spectral_signals[canonical],
                chord_m=chord_m,
                velocity_m_s=velocity_m_s,
                reference_length_m=reference_length_m,
            )
            spectrum["resampling"] = resampling
        except (RuntimeError, ValueError) as exc:
            spectrum = {"status": "NOT_AVAILABLE", "reason": str(exc)}
        frequency = float(spectrum.get("dominant_frequency_hz") or 0.0)
        duration = float(time_s[finite][-1] - time_s[finite][0])
        cycles = duration * frequency if frequency > 0.0 else 0.0
        spectrum["frequency_resolved"] = bool(cycles >= minimum_cycles and not spectrum.get("peak_at_first_bin", False))
        if not spectrum["frequency_resolved"]:
            spectrum["interpretation"] = "Unresolved peak: insufficient cycles or first positive bin (possible drift); do not infer a physical shedding frequency"
        stationarity = stationarity_blocks(
            values[finite],
            blocks=4,
            mean_tolerance_percent=limits["stationarity_mean_percent"],
            rms_tolerance_percent=limits["stationarity_rms_percent"],
        )
        signals[canonical] = {
            "summary": signal_summary(values[finite]),
            "stationarity": stationarity,
            "spectrum": spectrum,
            "cycles_observed": cycles,
            "minimum_cycles": int(minimum_cycles),
            "preferred_cycles": int(preferred_cycles),
            "sufficient_cycles": bool(spectrum["frequency_resolved"]),
        }
    all_stationary = bool(signals) and all(
        bool(row["stationarity"].get("passed")) for row in signals.values()
    )
    all_cycles = bool(signals) and all(
        bool(row["sufficient_cycles"]) for row in signals.values()
    )
    if hard_failures:
        status = "URANS_PARTIAL"
        recommendation = "REPAIR_OR_EXTEND"
    elif all_stationary and all_cycles:
        status = "URANS_REVIEW_REQUIRED"
        recommendation = "ELIGIBLE_FOR_USER_ACCEPTANCE"
    else:
        status = "URANS_EXTENSION_REQUIRED"
        recommendation = "EXTEND_STAGE_E"
    return {
        "schema_version": 1,
        "status": status,
        "recommendation": recommendation,
        "sampling_window": {
            "stage": sampling_stage,
            "start_s": float(sampling_start_s),
            "end_s": float(time_s[-1]),
            "samples": int(len(sampled)),
        },
        "signals": signals,
        "hard_failures": hard_failures,
        "startup_and_settling_excluded": sampling_stage == "E",
        "automatic_assessment": recommendation,
        "review_status": "NOT_REVIEWED",
        "allowed_uses": {
            "space_time_convergence": False,
            "frequency_analysis": bool(signals) and not hard_failures,
        },
        "generated_at": utc_stamp(),
    }


def _stage_window(
    frame: pd.DataFrame,
    plan: dict[str, Any],
    stage_name: str,
) -> tuple[pd.DataFrame, float, float] | None:
    stage = next(
        (row for row in plan.get("stages", []) if str(row.get("stage")) == stage_name),
        None,
    )
    if not stage:
        return None
    start = float(stage.get("start_s", 0.0))
    end = float(stage.get("end_s", math.inf))
    try:
        selected = _sampling_frame(frame, start, end)
    except ValueError:
        return None
    time_column = _column(selected, "time_s", "time", "Time")
    return selected, start, min(end, float(selected[time_column].max()))


def _select_analysis_window(
    frame: pd.DataFrame,
    plan: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[str, float, float | None, int]:
    """Prefer production; a D-only diagnostic must never be called production."""
    for stage_name in ("E", "D"):
        selected = _stage_window(frame, plan, stage_name)
        if selected is not None and len(selected[0]) >= 16:
            return stage_name, selected[1], selected[2], int(len(selected[0]))
    fallback_start = float(
        metadata.get("sampling_start_s") or plan.get("sampling_start_s") or 0.0
    )
    selected = _sampling_frame(frame, fallback_start, None)
    return "UNCLASSIFIED", fallback_start, None, int(len(selected))


def longest_stationary_tail(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Predeclared longest-suffix search; retain failed evidence when none passes.

    Search at 1% increments, keeping at least 20% and 64 samples. No interior
    interval may be cherry-picked. All three force signals must pass the same
    four-block mean/fluctuation-RMS test. Cycle sufficiency is assessed separately.
    """
    columns = [_column(frame, name) for name in ("Cl", "Cd", "Cm")]
    tested = []
    mean_stable = None
    minimum = max(64, int(math.ceil(len(frame) * .2)))
    for start in sorted(set(np.linspace(0, max(0, len(frame)-minimum), 81, dtype=int))):
        selected = frame.iloc[start:]
        assessments = {}
        for column in columns:
            values = selected[column].to_numpy(dtype=float)
            result = stationarity_blocks(values)
            result["mean_stable"] = result["passed"]
            blocks = np.array_split(values, 4)
            rms = np.asarray([np.std(block) for block in blocks])
            variation = 100 * float(np.ptp(rms)) / max(float(np.mean(rms)), 1e-12)
            result.update(fluctuation_rms_variation_percent=variation)
            result["passed"] = bool(result["passed"] and variation < 5.)
            assessments[column] = result
        passed = all(item["passed"] for item in assessments.values())
        tested.append({"discarded_samples": int(start), "passed": passed, "signals": assessments})
        if mean_stable is None and all(item["mean_stable"] for item in assessments.values()):
            mean_stable = selected
        if passed:
            return selected, {"status": "STATIONARY_TAIL_FOUND", "selection": "longest tested suffix", "trials": tested}
    if mean_stable is not None:
        return mean_stable, {"status": "MEAN_STABLE_ONLY", "selection": "longest suffix passing mean/total-RMS tests; fluctuating RMS not stationary", "trials": tested}
    return frame, {"status": "NO_STATIONARY_TAIL", "selection": "full available window, diagnostic only", "trials": tested}


def _maximum_normal_thickness(points: pd.DataFrame) -> tuple[float, float] | None:
    """Return maximum thickness normal to the interpolated mean camber line."""
    x_name = next((name for name in ("x_m", "x") if name in points.columns), None)
    z_name = next((name for name in ("z_m", "y_m", "z", "y") if name in points.columns), None)
    if x_name is None or z_name is None:
        return None
    x = pd.to_numeric(points[x_name], errors="coerce").to_numpy(dtype=float)
    z = pd.to_numeric(points[z_name], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(x) & np.isfinite(z)
    x, z = x[finite], z[finite]
    if len(x) < 12:
        return None
    le = int(np.argmin(x))
    branches = [(x[: le + 1], z[: le + 1]), (x[le:], z[le:])]

    def collapse(branch_x: np.ndarray, branch_z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        order = np.argsort(branch_x)
        frame = pd.DataFrame({"x": branch_x[order], "z": branch_z[order]})
        grouped = frame.groupby("x", sort=True, as_index=False)["z"].mean()
        return grouped["x"].to_numpy(), grouped["z"].to_numpy()

    branch_a = collapse(*branches[0])
    branch_b = collapse(*branches[1])
    x_min = max(float(branch_a[0].min()), float(branch_b[0].min()))
    x_max = min(float(branch_a[0].max()), float(branch_b[0].max()))
    if not x_max > x_min:
        return None
    grid = np.linspace(x_min, x_max, 801)
    za = np.interp(grid, *branch_a)
    zb = np.interp(grid, *branch_b)
    upper, lower = (za, zb) if float(np.nanmean(za - zb)) >= 0.0 else (zb, za)
    mean = 0.5 * (upper + lower)
    slope = np.gradient(mean, grid)

    def upper_at(value: float) -> float:
        return float(np.interp(value, grid, upper))

    def lower_at(value: float) -> float:
        return float(np.interp(value, grid, lower))

    def crossing(x0: float, z0: float, nx: float, nz: float, surface: Any) -> float | None:
        limit = min(
            (x_max - x0) / nx if nx > 1.0e-14 else math.inf,
            (x_min - x0) / nx if nx < -1.0e-14 else math.inf,
        )
        limit = min(abs(float(limit)), x_max - x_min) if math.isfinite(limit) else x_max - x_min
        if limit <= 0.0:
            return None
        def residual(distance: float) -> float:
            xx = x0 + nx * distance
            return z0 + nz * distance - surface(xx)
        left, right = 0.0, limit * 0.999999
        f_left, f_right = residual(left), residual(right)
        if f_left == 0.0:
            return 0.0
        if f_left * f_right > 0.0:
            return None
        for _ in range(45):
            middle = 0.5 * (left + right)
            f_middle = residual(middle)
            if f_left * f_middle <= 0.0:
                right, f_right = middle, f_middle
            else:
                left, f_left = middle, f_middle
        return 0.5 * (left + right)

    best_thickness = 0.0
    best_x = float(grid[len(grid) // 2])
    for index in range(2, len(grid) - 2, 2):
        norm = math.hypot(float(slope[index]), 1.0)
        nx, nz = -float(slope[index]) / norm, 1.0 / norm
        positive = crossing(float(grid[index]), float(mean[index]), nx, nz, upper_at)
        negative = crossing(float(grid[index]), float(mean[index]), -nx, -nz, lower_at)
        thickness = (
            positive + negative
            if positive is not None and negative is not None
            else float(upper[index] - lower[index]) / norm
        )
        if thickness > best_thickness:
            best_thickness, best_x = thickness, float(grid[index])
    return (best_thickness, best_x) if best_thickness > 0.0 else None


def _profile_thickness_m(metadata: dict[str, Any], chord_m: float) -> tuple[float, str]:
    for container in (metadata, metadata.get("operating_condition") or {}):
        for key in ("airfoil_thickness_m", "maximum_thickness_m", "profile_thickness_m"):
            value = container.get(key)
            if value is not None and float(value) > 0.0:
                return float(value), f"metadata:{key}"
        for key in ("airfoil_thickness_ratio", "maximum_thickness_ratio", "thickness_ratio"):
            value = container.get(key)
            if value is not None and float(value) > 0.0:
                return float(value) * chord_m, f"metadata:{key}"
    package = Path(str(metadata.get("mesh_package") or ""))
    points = package / "Mesh Data/profile_preprocessed_points.csv"
    if points.is_file():
        try:
            table = pd.read_csv(points)
            result = _maximum_normal_thickness(table)
            if result is not None:
                thickness, x_location = result
                return thickness, (
                    f"{points}: maximum intrados-extrados distance normal to "
                    f"mean camber at x={x_location:.8g} m"
                )
        except (OSError, ValueError, pd.errors.ParserError):
            pass
    # LS(1)-0417 is the fixed Validation Lab profile; 17% is its nominal t/c.
    return 0.17 * chord_m, "LS(1)-0417 nominal t/c=0.17 fallback"


def _write_review_plots(
    run_root: Path,
    sampled: pd.DataFrame,
    report: dict[str, Any],
) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ramair_scientific_plot_style import apply_scientific_style
    apply_scientific_style()

    output = run_root / "plots/urans_review"
    output.mkdir(parents=True, exist_ok=True)
    products: list[str] = []
    t = sampled[_column(sampled, "Time", "time_s")].to_numpy(dtype=float)
    spacing = np.diff(t)
    if len(t) >= 16 and np.allclose(spacing, np.median(spacing), rtol=.001, atol=1e-12):
        fig, axes = plt.subplots(3, 2, figsize=(12, 9))
        fft_table = {}
        for index, name in enumerate(("Cl", "Cd", "Cm")):
            y = sampled[_column(sampled, name)].to_numpy(dtype=float)
            window = np.hanning(len(y))
            frequency = np.fft.rfftfreq(len(y), np.median(spacing))
            amplitude = 2 * np.abs(np.fft.rfft((y-y.mean()) * window)) / window.sum()
            amplitude[0] *= .5
            if len(y) % 2 == 0:
                amplitude[-1] *= .5
            fft_table.update(frequency_hz=frequency, **{name: amplitude})
            axes[index, 0].plot(t, y, lw=.8)
            axes[index, 0].set(xlabel="Time [s]", ylabel=name)
            axes[index, 1].plot(frequency[1:], amplitude[1:], lw=.8)
            axes[index, 1].set(xlabel="Frequency [Hz]", ylabel=f"{name} FFT amplitude", xscale="log")
        fig.suptitle("Production signal and Hann FFT — partial evidence")
        fig.tight_layout()
        target = output / "signals_fft.png"
        fig.savefig(target, dpi=180)
        plt.close(fig)
        pd.DataFrame(fft_table).to_csv(run_root / "fft_coefficients.csv", index=False)
        products.append(str(target))
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.4), constrained_layout=True)
    for row, (coefficient, symbol) in enumerate((("Cl", r"$C_L$"), ("Cd", r"$C_D$"))):
        spectrum = report.get("signals", {}).get(coefficient, {}).get("spectrum", {})
        f = np.asarray(spectrum.get("frequency_hz") or [], dtype=float)
        psd = np.asarray(spectrum.get("psd") or [], dtype=float)
        st = np.asarray(spectrum.get("strouhal") or [], dtype=float)
        for axis, x, label in zip(axes[row], (f, st, 2*np.pi*st),
                (r"Frequency, $f$ [Hz]", r"Strouhal number, $St=fL/U_\infty$", r"Convective wavenumber, $kL=2\pi fL/U_\infty$")):
            if len(x) == len(psd):
                mask = np.isfinite(x) & np.isfinite(psd) & (x > 0) & (psd > 0)
                axis.loglog(x[mask], psd[mask], lw=1, color="#246680" if row == 0 else "#b45926")
            axis.set(xlabel=label, ylabel=symbol+r" PSD [Hz$^{-1}$]")
            axis.grid(alpha=.2, which="both")
    fig.suptitle("Lift and drag spectra — Welch/Hann; frequency density on all axes")
    target = output / "lift_drag_spectral_analysis.png"
    for extension in ("png", "pdf", "svg"):
        fig.savefig(target.with_suffix("."+extension), dpi=220)
    plt.close(fig)
    products.append(str(target))
    cl_spectrum = (report.get("signals", {}).get("Cl") or {}).get("spectrum") or {}
    frequency = np.asarray(cl_spectrum.get("frequency_hz") or [], dtype=float)
    density = np.asarray(cl_spectrum.get("psd") or [], dtype=float)
    strouhal = np.asarray(cl_spectrum.get("strouhal") or [], dtype=float)
    wave = np.asarray(cl_spectrum.get("wave_number_1_over_st") or [], dtype=float)
    for name, x, label in (
        ("lift_psd_frequency.png", frequency, "Frequency, f [Hz]"),
        ("lift_psd_strouhal.png", strouhal, "Strouhal number, St [-]"),
        ("lift_psd_wave_number.png", wave, "Inverse Strouhal number, 1/St [-]"),
    ):
        mask = np.isfinite(x) & np.isfinite(density) & (x > 0.0)
        if not np.any(mask):
            continue
        order = np.argsort(x[mask])
        fig, axis = plt.subplots(figsize=(7.4, 4.2))
        axis.plot(x[mask][order], density[mask][order], lw=1.0)
        axis.set(xlabel=label, ylabel="Power spectral density", title="Lift-force PSD")
        axis.grid(alpha=0.25)
        fig.tight_layout()
        target = output / name
        fig.savefig(target, dpi=180)
        plt.close(fig)
        products.append(str(target))

    time_name = _column(sampled, "time_s", "time", "Time")
    traces: dict[str, pd.Series] = {}
    for label, names in {
        "Cl": ("Cl", "CL"), "Cd": ("Cd", "CD"), "Cm": ("Cm", "CM")
    }.items():
        try:
            traces[label] = pd.to_numeric(sampled[_column(sampled, *names)], errors="coerce")
        except KeyError:
            pass
    if "Cl" in traces and "Cd" in traces:
        traces["Cl/Cd"] = traces["Cl"] / traces["Cd"].replace(0.0, np.nan)
    if traces:
        window = max(8, min(len(sampled) // 4, max(8, len(sampled) // 20)))
        fig, axes = plt.subplots(len(traces), 3, figsize=(13.0, 2.7 * len(traces)), sharex=True)
        axes = np.atleast_2d(axes)
        time_values = pd.to_numeric(sampled[time_name], errors="coerce")
        for row, (label, values) in enumerate(traces.items()):
            moving_mean = values.rolling(window, min_periods=max(3, window // 2)).mean()
            moving_rms = np.sqrt(((values - moving_mean) ** 2).rolling(window, min_periods=max(3, window // 2)).mean())
            drift = moving_mean.diff(window)
            for axis, data, title in zip(
                axes[row], (moving_mean, moving_rms, drift),
                ("moving mean", "moving RMS", "moving drift"),
            ):
                axis.plot(time_values, data, lw=0.9)
                axis.set_ylabel(label)
                axis.set_title(title)
                axis.grid(alpha=0.25)
        for axis in axes[-1]:
            axis.set_xlabel("Physical time [s]")
        fig.tight_layout()
        target = output / "moving_statistics.png"
        fig.savefig(target, dpi=180)
        plt.close(fig)
        products.append(str(target))
    return products


def executed_analysis_plan(plan, journal, frame):
    """Executed journal phases take precedence over subsequently edited plans."""
    phases = [p for p in journal.get("phases", []) if p.get("solver_started")
              and p.get("actual_start_s") is not None and p.get("phase") in ("D", "E")]
    if not phases:
        return plan, "configured_plan_fallback"
    observed_end = float(frame[_column(frame, "Time", "time_s", "time")].max())
    stages = []
    last_phase = phases[-1]["phase"]
    for name in ("D", "E"):
        rows = [p for p in phases if p["phase"] == name]
        if not rows:
            continue
        # A failed reconstruction can leave the journal end at its input checkpoint.
        # Native force history still records the steps executed in the last phase.
        end = max(float(p.get("actual_end_s") or observed_end) for p in rows)
        if name == last_phase:
            end = max(end, observed_end)
        stages.append({"stage": name, "start_s": min(float(p["actual_start_s"]) for p in rows),
                       "end_s": end,
                       "dt_s": rows[-1].get("deltaT_s"), "scheme": rows[-1].get("scheme")})
    return {**plan, "stages": stages}, "executed_stage_journal"


def restore_serialized_uniform_times(frame, expected_dt):
    """Recover a verified fixed-dt grid rounded by six-significant-digit output.

    Coefficients and raw export are untouched. Missing samples or deviations
    beyond timestamp quantization are rejected rather than interpolated.
    """
    result = {"status": "UNCHANGED"}
    if not expected_dt or len(frame) < 16:
        return frame, result
    key = _column(frame, "Time", "time_s", "time")
    t = frame[key].to_numpy(dtype=float)
    dt = float(expected_dt)
    grid = np.arange(len(t))*dt
    origin = float(np.median(t-grid))
    ideal = origin+grid
    quantum = 10.0**(math.floor(math.log10(max(np.max(np.abs(t)), 1e-12)))-5)
    error = float(np.max(np.abs(t-ideal)))
    if quantum <= .05*dt and error <= max(.51*quantum, dt*1e-8):
        frame = frame.copy()
        frame[key] = ideal
        result = {"status": "FIXED_DT_CONFIRMED_WITHIN_SERIALIZATION_PRECISION",
                  "dt_s": dt, "timestamp_quantum_s": quantum, "max_time_adjustment_s": error,
                  "coefficient_interpolation": False}
    return frame, result


def review_run(run_root: Path, *, mark_spectral_peaks: bool | None = None) -> dict[str, Any]:
    display=read_json(run_root / 'frequency_display.json', {}) or {}
    if mark_spectral_peaks is None:mark_spectral_peaks=bool(display.get('mark_peaks',True))
    write_json_atomic(run_root / 'frequency_display.json', {'mark_peaks':mark_spectral_peaks})
    run_root = Path(run_root)
    metadata = read_json(run_root / "case_metadata.json", {}) or {}
    plan = read_json(run_root / "stage_plan.json", {}) or {}
    candidates = (
        run_root / "postprocess/URANS/forceCoeffs_raw.csv",
        run_root / "postprocess/forceCoeffs_raw.csv",
        run_root / "postprocess/force_coeffs.csv",
        run_root / "force_coeffs.csv",
        run_root / "case/postProcessing/forceCoeffs.csv",
    )
    records, sources = read_force_coefficient_history(run_root / "case")
    source = next((path for path in candidates if path.is_file()), None)
    if records:
        frame = pd.DataFrame(records)
        source = run_root / "postprocess/URANS/forceCoeffs_raw.csv"
        source.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(source, index=False)
    elif source is not None:
        frame = pd.read_csv(source)
        sources = [str(source)]
    else:
        raise FileNotFoundError("No native OpenFOAM forceCoeffs history or exported coefficient CSV exists in this run")
    plan, stage_source = executed_analysis_plan(plan, read_json(run_root / "stage_journal.json", {}) or {}, frame)
    sampling_stage, sampling_start, sampling_end, available_samples = _select_analysis_window(
        frame, plan, metadata
    )
    available = _sampling_frame(frame, sampling_start, sampling_end)
    selected_stage = next((p for p in plan.get("stages", []) if p.get("stage") == sampling_stage), {})
    available, time_serialization = restore_serialized_uniform_times(available, selected_stage.get("dt_s"))
    available, selection = longest_stationary_tail(available)
    time_column = _column(available, "time_s", "time", "Time")
    sampling_start, sampling_end = float(available[time_column].iloc[0]), float(available[time_column].iloc[-1])
    condition = metadata.get("operating_condition") or {}
    chord_m = float(condition.get("chord_m") or metadata.get("chord_m") or 1.0)
    velocity_m_s = float(condition.get("velocity_m_s") or metadata.get("U_inf_m_s") or 1.0)
    alpha_deg = float(metadata.get("alpha_deg", condition.get("alpha_deg", 0.0)))
    thickness_m, thickness_source = _profile_thickness_m(metadata, chord_m)
    alpha_rad = math.radians(alpha_deg)
    projected_length_m = abs(
        thickness_m * math.cos(alpha_rad) + chord_m * math.sin(alpha_rad)
    )
    report = review_urans_signals(
        available,
        sampling_start_s=sampling_start,
        sampling_end_s=sampling_end,
        chord_m=chord_m,
        velocity_m_s=velocity_m_s,
        reference_length_m=projected_length_m,
        sampling_stage=sampling_stage,
    )
    report["sampling_window"]["available_samples_before_analysis"] = available_samples
    report["window_selection"] = selection
    report["stage_source"] = stage_source
    report["time_serialization"] = time_serialization
    report["native_history_sources"] = sources
    report["partial_duration"] = sampling_end < float(plan.get("sampling_end_s") or sampling_end)
    import hashlib
    report["analysis_sha256"] = hashlib.sha256(available.to_csv(index=False).encode()).hexdigest()
    if selection["status"] != "STATIONARY_TAIL_FOUND" or sampling_stage != "E":
        report.update(status="URANS_PARTIAL", recommendation="STABLE_PRODUCTION_REQUIRED")
    report["frequency_method"] = {
        "source_signal": "time-accurate lift coefficient (normal-force proxy)",
        "estimator": "Welch PSD",
        "window": "Hann",
        "detrend": "constant",
        "overlap_fraction": 0.5,
        "reference_length_formula": "L=t*cos(alpha)+c*sin(alpha)",
        "airfoil_thickness_m": thickness_m,
        "airfoil_thickness_source": thickness_source,
        "projected_reference_length_m": projected_length_m,
        "alpha_deg": alpha_deg,
        "velocity_m_s": velocity_m_s,
        "wavenumber_convention": "kL=2*pi*f*L/U_inf assumes convection at U_inf; 1/St is inverse Strouhal, not wavenumber",
        "spectral_density_axes": "PSD per Hz retained when horizontal coordinates are St or kL; no density Jacobian applied",
    }
    report["mean_force_stability"] = {
        name: {
            "stationary": bool(values.get("stationarity", {}).get("passed")),
            "mean_variation_percent": values.get("stationarity", {}).get("mean_variation_percent"),
            "rms_variation_percent": values.get("stationarity", {}).get("rms_variation_percent"),
        }
        for name, values in report.get("signals", {}).items()
    }
    stage = next(
        (row for row in plan.get("stages", []) if str(row.get("stage")) == sampling_stage),
        {},
    )
    spacings = np.diff(available[time_column].to_numpy(dtype=float))
    report["time_step"] = {
        "stage": sampling_stage,
        "minimum_s": float(spacings.min()) if len(spacings) else None,
        "maximum_s": float(spacings.max()) if len(spacings) else None,
        "fixed_within_analysis_window": bool(len(spacings) and np.allclose(spacings, np.median(spacings), rtol=.001, atol=1e-12)),
    }
    residual_candidates = (
        run_root / "postprocess/URANS/solver_residuals.csv",
        run_root / "postprocess/solver_residuals.csv",
        run_root / "residuals.csv",
    )
    residual_source = next((path for path in residual_candidates if path.is_file()), None)
    if residual_source is not None:
        try:
            residuals = pd.read_csv(residual_source)
            continuity = (
                residuals[residuals["field"] == "continuity_global"]
                if "field" in residuals.columns else pd.DataFrame()
            )
            values = pd.to_numeric(continuity.get("initial_residual"), errors="coerce").dropna()
            report["continuity"] = {
                "status": "AVAILABLE" if not values.empty else "NOT_AVAILABLE",
                "samples": int(len(values)),
                "maximum_absolute_global_error": float(values.max()) if not values.empty else None,
                "final_absolute_global_error": float(values.iloc[-1]) if not values.empty else None,
                "source": str(residual_source),
            }
        except (OSError, ValueError, pd.errors.ParserError):
            report["continuity"] = {"status": "READ_FAILED", "source": str(residual_source)}
    else:
        report["continuity"] = {"status": "NOT_AVAILABLE"}
    report["run_id"] = metadata.get("run_id") or run_root.name
    report["source_csv"] = str(source)
    stationarity = {
        name: values.get("stationarity")
        for name, values in report["signals"].items()
    }
    modes = {
        name: {
            key: values.get("spectrum", {}).get(key)
            for key in (
                "dominant_frequency_hz",
                "dominant_strouhal",
                "peak_amplitude",
                "frequency_resolution_hz",
                "nyquist_hz",
            )
        }
        for name, values in report["signals"].items()
    }
    write_json_atomic(run_root / "stationarity.json", stationarity)
    write_json_atomic(run_root / "dominant_modes.json", modes)
    for name, values in report["signals"].items():
        spectrum = values.get("spectrum") or {}
        frequencies = spectrum.get("frequency_hz")
        density = spectrum.get("psd")
        strouhal = spectrum.get("strouhal")
        wave_number = spectrum.get("wave_number_1_over_st")
        if not (
            isinstance(frequencies, list)
            and isinstance(density, list)
            and len(frequencies) == len(density)
        ):
            continue
        psd = pd.DataFrame({
            "frequency_hz": frequencies,
            "psd": density,
        })
        if isinstance(strouhal, list) and len(strouhal) == len(psd):
            psd["strouhal"] = strouhal
        if isinstance(wave_number, list) and len(wave_number) == len(psd):
            psd["wave_number_1_over_st"] = wave_number
        psd.to_csv(run_root / f"psd_{name}.csv", index=False)
    sampled = _sampling_frame(available, sampling_start, sampling_end)
    report["plots"] = _write_review_plots(run_root, sampled, report)
    from ramair_frequency_analysis import export_frequency_analysis
    frequency_report = export_frequency_analysis(run_root / "plots/urans_review/frequency_complete",
        sampled[_column(sampled, "Time", "time_s", "time")],
        {k:sampled[_column(sampled,k)] for k in ("Cl","Cd")},chord_m,projected_length_m,velocity_m_s,mark_peaks=mark_spectral_peaks)
    report["frequency_analysis_complete"] = frequency_report
    report["plots"].extend(frequency_report["plots"])
    write_json_atomic(run_root / "review.json", report)
    return report


def set_review_decision(
    run_root: Path,
    decision: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    run_root = Path(run_root)
    path = run_root / "review.json"
    report = read_json(path, {}) or {}
    if not report:
        raise RuntimeError("Generate the URANS review before deciding")
    if decision not in URANS_DECISIONS:
        raise ValueError(f"Unsupported URANS decision: {decision}")
    if decision == "accept" and report.get("recommendation") != (
        "ELIGIBLE_FOR_USER_ACCEPTANCE"
    ):
        raise RuntimeError(
            "The current evidence is not eligible for URANS acceptance"
        )
    if decision == "publish-partial" and (
        report.get("sampling_window", {}).get("stage") != "E"
        or report.get("window_selection", {}).get("status") not in {"STATIONARY_TAIL_FOUND", "MEAN_STABLE_ONLY"}
        or report.get("hard_failures")
        or not report.get("time_step", {}).get("fixed_within_analysis_window")
    ):
        raise RuntimeError("Partial comparison requires real production with stable means and usable signals")
    history = list(report.get("decision_history") or [])
    history.append(
        {
            "decision": decision,
            "status": URANS_DECISIONS[decision],
            "reason": str(reason or "").strip() or None,
            "source": "EXPLICIT_USER_ACTION",
            "decided_at": utc_stamp(),
        }
    )
    report.update(
        {
            "status": URANS_DECISIONS[decision],
            "decision": decision,
            "decision_reason": str(reason or "").strip() or None,
            "decision_source": "EXPLICIT_USER_ACTION",
            "decision_history": history,
            "review_status": URANS_DECISIONS[decision],
            "allowed_uses": {
                "space_time_convergence": decision == "accept",
                "exploratory_comparison": decision in {"accept", "publish-partial"},
                "frequency_analysis": decision in {"accept", "publish-partial"},
            },
            "updated_at": utc_stamp(),
        }
    )
    write_json_atomic(path, report)
    manifest_path = run_root / "run_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    if manifest:
        manifest.update(
            {
                "review_status": URANS_DECISIONS[decision],
                "allowed_uses": report["allowed_uses"],
                "updated_at": utc_stamp(),
            }
        )
        write_json_atomic(manifest_path, manifest)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--decision",
        choices=tuple(URANS_DECISIONS),
    )
    parser.add_argument("--reason")
    parser.add_argument('--mark-spectral-peaks',action=argparse.BooleanOptionalAction,default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.decision:
        result = set_review_decision(
            args.run_root,
            args.decision,
            reason=args.reason,
        )
    else:
        result = review_run(args.run_root,mark_spectral_peaks=args.mark_spectral_peaks)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
