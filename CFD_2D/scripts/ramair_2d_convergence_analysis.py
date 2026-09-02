#!/usr/bin/env python3
"""Numerical/statistical analysis for validation convergence studies."""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Iterable, Sequence

import numpy as np


ACCEPTANCE_DEFAULTS: dict[str, float] = {
    "mean_CL_percent": 1.0,
    "mean_CD_percent": 2.0,
    "mean_CM_percent": 2.0,
    "rms_percent": 5.0,
    "dominant_frequency_percent": 2.0,
    "psd_peak_amplitude_percent": 10.0,
}


def scientific_token(value: float) -> str:
    text = f"{float(value):.1e}"
    mantissa, exponent = text.split("e")
    sign = "m" if int(exponent) < 0 else "p"
    return f"{mantissa.replace('.', 'p')}{sign}{abs(int(exponent))}"


def deterministic_run_id(
    topology: str,
    mesh_level: str,
    dt_s: float,
    outer_correctors: int,
    scheme: str,
    alpha_deg: float = 8.0,
) -> str:
    """Return the canonical scientific identity.

    ``outer_correctors`` and ``scheme`` remain accepted for source
    compatibility, but they are solver configuration rather than identity.
    """
    if topology not in {"closed", "open"}:
        raise ValueError("topology must be closed or open")
    if mesh_level not in {"coarse", "medium", "fine"}:
        raise ValueError("mesh_level must be coarse, medium or fine")
    value = float(dt_s)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    alpha_value = float(alpha_deg)
    if not math.isfinite(alpha_value):
        raise ValueError("alpha_deg must be finite")
    alpha_sign = "m" if alpha_value < 0 else ""
    alpha = f"{abs(alpha_value):.6f}".rstrip("0").rstrip(".").replace(".", "p")
    if "p" not in alpha and len(alpha) < 2:
        alpha = alpha.zfill(2)
    mantissa, exponent = f"{value:.12e}".split("e")
    mantissa = mantissa.rstrip("0").rstrip(".").replace(".", "p")
    exp_value = int(exponent)
    exp_token = f"m{abs(exp_value):02d}" if exp_value < 0 else f"p{exp_value:02d}"
    return f"{topology}_{mesh_level}_a{alpha_sign}{alpha}_dt{mantissa}e{exp_token}"


def effective_h(cell_count: int) -> float:
    if int(cell_count) <= 0:
        raise ValueError("cell_count must be positive")
    return 1.0 / math.sqrt(int(cell_count))


def refinement_ratio(coarser_cells: int, finer_cells: int) -> float:
    if finer_cells <= coarser_cells:
        raise ValueError("finer_cells must exceed coarser_cells")
    return math.sqrt(float(finer_cells) / float(coarser_cells))


def _bisect_observed_order(
    coarse: float,
    medium: float,
    fine: float,
    h_coarse: float,
    h_medium: float,
    h_fine: float,
) -> float | None:
    d32 = coarse - medium
    d21 = medium - fine
    if d32 == 0.0 or d21 == 0.0 or d32 * d21 <= 0.0:
        return None
    target = d32 / d21

    def residual(order: float) -> float:
        numerator = h_coarse**order - h_medium**order
        denominator = h_medium**order - h_fine**order
        return numerator / denominator - target

    low, high = 0.05, 12.0
    f_low, f_high = residual(low), residual(high)
    if not np.isfinite(f_low) or not np.isfinite(f_high) or f_low * f_high > 0.0:
        return None
    for _ in range(100):
        mid = 0.5 * (low + high)
        f_mid = residual(mid)
        if abs(f_mid) < 1.0e-10:
            return mid
        if f_low * f_mid <= 0.0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return 0.5 * (low + high)


def generalized_gci(
    *,
    coarse_value: float,
    medium_value: float,
    fine_value: float,
    coarse_cells: int,
    medium_cells: int,
    fine_cells: int,
    minimum_ratio: float = 1.1,
    safety_factor: float = 1.25,
) -> dict[str, Any]:
    ratios = {
        "coarse_to_medium": refinement_ratio(coarse_cells, medium_cells),
        "medium_to_fine": refinement_ratio(medium_cells, fine_cells),
    }
    result: dict[str, Any] = {
        "available": False,
        "ratios": ratios,
        "monotonic": (coarse_value - medium_value) * (medium_value - fine_value) > 0,
        "oscillatory": (coarse_value - medium_value) * (medium_value - fine_value) < 0,
        "reason": None,
    }
    if min(ratios.values()) < minimum_ratio:
        result["reason"] = "NON_ASYMPTOTIC_OR_WEAK_REFINEMENT_RATIO"
        return result
    if not result["monotonic"]:
        result["reason"] = (
            "OSCILLATORY_CONVERGENCE" if result["oscillatory"]
            else "NON_MONOTONIC_OR_DEGENERATE_CONVERGENCE"
        )
        return result
    h3, h2, h1 = (
        effective_h(coarse_cells),
        effective_h(medium_cells),
        effective_h(fine_cells),
    )
    order = _bisect_observed_order(
        coarse_value, medium_value, fine_value, h3, h2, h1
    )
    if order is None or not np.isfinite(order):
        result["reason"] = "OBSERVED_ORDER_NOT_RESOLVABLE"
        return result
    denominator = ratios["medium_to_fine"] ** order - 1.0
    if abs(denominator) < 1.0e-8 or abs(fine_value) < 1.0e-30:
        result["reason"] = "ILL_CONDITIONED_GCI_DENOMINATOR"
        return result
    relative_error = abs((fine_value - medium_value) / fine_value)
    result.update(
        available=True,
        reason=None,
        observed_order=order,
        fine_gci_percent=100.0 * safety_factor * relative_error / denominator,
        fine_medium_relative_difference_percent=100.0 * relative_error,
    )
    return result


def signal_summary(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
) -> dict[str, float | int]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size < 2:
        raise ValueError("At least two finite samples are required")
    mean = float(np.mean(array))
    std = float(np.std(array, ddof=1))
    centered = array - mean
    correlation = np.correlate(centered, centered, mode="full")[array.size - 1 :]
    if correlation[0] > 0.0:
        correlation = correlation / correlation[0]
        non_positive = np.flatnonzero(correlation <= 0.0)
        stop = int(non_positive[0]) if non_positive.size else min(array.size, 1000)
        integral_samples = max(0.5, 0.5 + float(np.sum(correlation[1:stop])))
    else:
        integral_samples = 0.5
    effective_samples = max(1.0, array.size / (2.0 * integral_samples))
    z_value = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    half_width = z_value * std / math.sqrt(effective_samples)
    return {
        "samples": int(array.size),
        "mean": mean,
        "rms": float(np.sqrt(np.mean(array**2))),
        "std": std,
        "min": float(np.min(array)),
        "max": float(np.max(array)),
        "integral_time_scale_samples": integral_samples,
        "effective_independent_samples": effective_samples,
        "confidence": confidence,
        "confidence_interval_low": mean - half_width,
        "confidence_interval_high": mean + half_width,
    }


def stationarity_blocks(
    values: Sequence[float],
    *,
    blocks: int = 4,
    mean_tolerance_percent: float = 1.0,
    rms_tolerance_percent: float = 5.0,
) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size < blocks * 4:
        return {
            "passed": False,
            "reason": "INSUFFICIENT_SAMPLES_FOR_BLOCK_STATIONARITY",
            "samples": int(array.size),
        }
    segments = np.array_split(array, blocks)
    means = np.asarray([np.mean(segment) for segment in segments])
    rms = np.asarray([np.sqrt(np.mean(segment**2)) for segment in segments])
    mean_scale = max(abs(float(np.mean(array))), float(np.max(np.abs(means))), 1.0e-12)
    rms_scale = max(float(np.mean(rms)), 1.0e-12)
    mean_variation = 100.0 * float(np.ptp(means)) / mean_scale
    rms_variation = 100.0 * float(np.ptp(rms)) / rms_scale
    return {
        "passed": (
            mean_variation < mean_tolerance_percent
            and rms_variation < rms_tolerance_percent
        ),
        "reason": None,
        "blocks": blocks,
        "block_means": means.tolist(),
        "block_rms": rms.tolist(),
        "mean_variation_percent": mean_variation,
        "rms_variation_percent": rms_variation,
        "mean_tolerance_percent": mean_tolerance_percent,
        "rms_tolerance_percent": rms_tolerance_percent,
    }


def welch_spectrum(
    time_s: Sequence[float],
    values: Sequence[float],
    *,
    chord_m: float,
    velocity_m_s: float,
    nperseg: int | None = None,
) -> dict[str, Any]:
    try:
        from scipy.signal import welch
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError("scipy is required for Welch frequency analysis") from exc
    time = np.asarray(time_s, dtype=float)
    signal = np.asarray(values, dtype=float)
    mask = np.isfinite(time) & np.isfinite(signal)
    time, signal = time[mask], signal[mask]
    if time.size < 16:
        raise ValueError("At least 16 uniformly spaced samples are required")
    spacing = np.diff(time)
    median_dt = float(np.median(spacing))
    if median_dt <= 0.0 or np.max(np.abs(spacing - median_dt)) > 1.0e-3 * median_dt:
        raise ValueError("Welch analysis requires uniformly spaced time data")
    segment = int(
        nperseg
        or min(
            4096,
            max(16, 2 ** int(math.floor(math.log2(time.size)))),
        )
    )
    segment = min(segment, time.size)
    overlap = segment // 2
    frequency, density = welch(
        signal,
        fs=1.0 / median_dt,
        window="hann",
        detrend="constant",
        nperseg=segment,
        noverlap=overlap,
    )
    positive = frequency > 0.0
    if not np.any(positive):
        raise ValueError("No positive frequency bins were produced")
    peak_index = int(np.argmax(density[positive]))
    positive_frequency = frequency[positive]
    positive_density = density[positive]
    peak_frequency = float(positive_frequency[peak_index])
    return {
        "frequency_hz": frequency.tolist(),
        "psd": density.tolist(),
        "strouhal": (frequency * chord_m / velocity_m_s).tolist(),
        "dominant_frequency_hz": peak_frequency,
        "dominant_strouhal": peak_frequency * chord_m / velocity_m_s,
        "peak_amplitude": float(positive_density[peak_index]),
        "nperseg": segment,
        "noverlap": overlap,
        "duration_s": float(time[-1] - time[0]),
        "frequency_resolution_hz": 1.0 / float(time[-1] - time[0]),
        "nyquist_hz": 0.5 / median_dt,
        "normalization": "density",
        "segments_approx": max(1, (time.size - overlap) // max(1, segment - overlap)),
    }


def relative_percent(a: float, b: float, *, floor: float = 1.0e-12) -> float:
    return 100.0 * abs(float(a) - float(b)) / max(abs(float(b)), floor)


def compare_run_metrics(
    current: dict[str, Any],
    finer: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    limits = dict(ACCEPTANCE_DEFAULTS)
    limits.update(thresholds or {})
    comparisons = {
        "mean_CL_percent": relative_percent(current["mean_CL"], finer["mean_CL"]),
        "mean_CD_percent": relative_percent(current["mean_CD"], finer["mean_CD"]),
        "mean_CM_percent": relative_percent(current["mean_CM"], finer["mean_CM"]),
        "rms_percent": relative_percent(current["rms_CL"], finer["rms_CL"]),
        "dominant_frequency_percent": relative_percent(
            current["dominant_strouhal"], finer["dominant_strouhal"]
        ),
        "psd_peak_amplitude_percent": relative_percent(
            current["psd_peak_amplitude"], finer["psd_peak_amplitude"]
        ),
    }
    failures = [
        name for name, difference in comparisons.items() if difference >= limits[name]
    ]
    gates = {
        "bounded": bool(current.get("bounded", False)),
        "stationarity": bool(current.get("stationarity_passed", False)),
        "pimple": bool(current.get("pimple_converged", False)),
        "surface_topology_consistent": bool(
            current.get("surface_topology_consistent", False)
        ),
    }
    failures.extend(name for name, passed in gates.items() if not passed)
    return {
        "accepted": not failures,
        "status": "ACCEPTED" if not failures else "REJECTED_TEMPORAL",
        "comparisons": comparisons,
        "thresholds": limits,
        "gates": gates,
        "failures": failures,
        "note": "checkMesh and stability alone are not acceptance criteria.",
    }


def classify_courant(
    values: Sequence[float],
    *,
    cell_ids: Sequence[int] | None = None,
    coordinates: Sequence[Sequence[float]] | None = None,
) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        raise ValueError("No finite Courant values")
    order = np.argsort(array)[::-1][:20]
    ids = list(cell_ids or range(len(values)))
    xyz = list(coordinates or [])
    top = []
    for index in order:
        item: dict[str, Any] = {"cell_id": int(ids[int(index)]), "Co": float(array[index])}
        if int(index) < len(xyz):
            item["coordinates"] = [float(value) for value in xyz[int(index)]]
        top.append(item)
    return {
        "max": float(np.max(array)),
        "mean": float(np.mean(array)),
        "p95": float(np.percentile(array, 95)),
        "p99": float(np.percentile(array, 99)),
        "p99p9": float(np.percentile(array, 99.9)),
        "fraction_above_1": float(np.mean(array > 1.0)),
        "fraction_above_2": float(np.mean(array > 2.0)),
        "top_20_cells": top,
        "automatic_rejection": False,
    }


def compare_pimple_outer_correctors(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(records, key=lambda row: int(row["nOuterCorrectors"]))
    if len(rows) < 2:
        return {"status": "INSUFFICIENT_PIMPLE_CASES", "rows": rows}
    reference = rows[-1]
    comparisons = []
    missing_metrics: list[dict[str, Any]] = []

    def optional_difference(row: dict[str, Any], key: str) -> float | None:
        left = row.get(key)
        right = reference.get(key)
        if left is None or right is None:
            missing_metrics.append(
                {"nOuterCorrectors": int(row["nOuterCorrectors"]), "metric": key}
            )
            return None
        return relative_percent(float(left), float(right))

    def optional_ratio(row: dict[str, Any], key: str) -> float | None:
        left = row.get(key)
        right = reference.get(key)
        if left is None or right is None:
            missing_metrics.append(
                {"nOuterCorrectors": int(row["nOuterCorrectors"]), "metric": key}
            )
            return None
        return float(left) / max(float(right), 1.0e-30)

    for row in rows[:-1]:
        comparisons.append(
            {
                "nOuterCorrectors": int(row["nOuterCorrectors"]),
                "mean_CL_difference_percent": optional_difference(row, "mean_CL"),
                "mean_CD_difference_percent": optional_difference(row, "mean_CD"),
                "dominant_St_difference_percent": optional_difference(
                    row, "dominant_strouhal"
                ),
                "cpu_step_ratio": optional_ratio(row, "cpu_seconds_per_step"),
            }
        )
    return {
        "status": "COMPARISON_AVAILABLE",
        "reference_outer_correctors": int(reference["nOuterCorrectors"]),
        "comparisons": comparisons,
        "missing_optional_metrics": missing_metrics,
    }
