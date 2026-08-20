#!/usr/bin/env python3
"""Oscillatory-signal review for one real Validation Lab URANS run."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

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
    minimum_cycles: int = 10,
    preferred_cycles: int = 20,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate only the accepted stage-E window."""
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
            spectrum = welch_spectrum(
                time_s[finite],
                values[finite],
                chord_m=chord_m,
                velocity_m_s=velocity_m_s,
            )
        except (RuntimeError, ValueError) as exc:
            spectrum = {"status": "NOT_AVAILABLE", "reason": str(exc)}
        frequency = float(spectrum.get("dominant_frequency_hz") or 0.0)
        duration = float(time_s[finite][-1] - time_s[finite][0])
        cycles = duration * frequency if frequency > 0.0 else 0.0
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
            "sufficient_cycles": cycles >= minimum_cycles,
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
            "stage": "E",
            "start_s": float(sampling_start_s),
            "end_s": float(time_s[-1]),
            "samples": int(len(sampled)),
        },
        "signals": signals,
        "hard_failures": hard_failures,
        "startup_and_settling_excluded": True,
        "automatic_assessment": recommendation,
        "review_status": "NOT_REVIEWED",
        "allowed_uses": {
            "space_time_convergence": False,
            "frequency_analysis": False,
        },
        "generated_at": utc_stamp(),
    }


def review_run(run_root: Path) -> dict[str, Any]:
    run_root = Path(run_root)
    metadata = read_json(run_root / "case_metadata.json", {}) or {}
    plan = read_json(run_root / "stage_plan.json", {}) or {}
    candidates = (
        run_root / "postprocess/force_coeffs.csv",
        run_root / "force_coeffs.csv",
        run_root / "case/postProcessing/forceCoeffs.csv",
    )
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise FileNotFoundError("No real URANS force-coefficient CSV was found")
    frame = pd.read_csv(source)
    sampling_start = float(
        metadata.get("sampling_start_s")
        or plan.get("sampling_start_s")
        or 0.0
    )
    sampling_end_raw = (
        metadata.get("sampling_end_s")
        or plan.get("sampling_end_s")
    )
    sampling_end = (
        float(sampling_end_raw)
        if sampling_end_raw is not None
        else None
    )
    condition = metadata.get("operating_condition") or {}
    report = review_urans_signals(
        frame,
        sampling_start_s=sampling_start,
        sampling_end_s=sampling_end,
        chord_m=float(condition.get("chord_m") or 1.0),
        velocity_m_s=float(condition.get("velocity_m_s") or 1.0),
    )
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
        psd.to_csv(run_root / f"psd_{name}.csv", index=False)
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
                "frequency_analysis": decision == "accept",
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
        result = review_run(args.run_root)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
