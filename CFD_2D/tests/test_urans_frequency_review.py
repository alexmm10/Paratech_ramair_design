from __future__ import annotations

import math
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
sys.path.insert(0, str(SCRIPTS))

from ramair_2d_convergence_analysis import welch_spectrum  # noqa: E402
from ramair_2d_urans_review import _select_analysis_window, review_run  # noqa: E402
from ramair_2d_study_registry import default_study_config  # noqa: E402
from ramair_2d_validation_study import (  # noqa: E402
    _plan_config_for_topology,
    _same_applied_value,
    _stage_plan,
)


def test_prepared_delta_t_comparison_accepts_foam_format_roundoff() -> None:
    assert _same_applied_value("0.000195910026", 0.00019591002596182035)


def test_partial_review_falls_back_from_one_e_sample_to_stage_d() -> None:
    frame = pd.DataFrame({
        "Time": np.r_[np.linspace(1.0, 1.9, 100), 2.0],
        "Cl": np.sin(np.linspace(0.0, 8.0, 101)),
        "Cd": np.full(101, 0.1),
        "Cm": np.zeros(101),
    })
    plan = {
        "stages": [
            {"stage": "D", "start_s": 1.0, "end_s": 1.9, "dt_s": 0.01},
            {"stage": "E", "start_s": 2.0, "end_s": 3.0, "dt_s": 0.01},
        ],
        "sampling_start_s": 2.0,
    }
    stage, start, end, samples = _select_analysis_window(frame, plan, {})
    assert stage == "D"
    assert start == pytest.approx(1.0)
    assert end == pytest.approx(1.9)
    assert samples == 100


def test_welch_uses_projected_reference_length_for_st_and_wave_number() -> None:
    time = np.arange(0.0, 4.0, 0.01)
    signal = np.sin(2.0 * math.pi * 5.0 * time)
    projected = 0.17 * math.cos(math.radians(8.0)) + math.sin(math.radians(8.0))
    spectrum = welch_spectrum(
        time,
        signal,
        chord_m=1.0,
        velocity_m_s=50.0,
        reference_length_m=projected,
    )
    expected_st = spectrum["dominant_frequency_hz"] * projected / 50.0
    assert spectrum["dominant_strouhal"] == pytest.approx(expected_st)
    assert spectrum["dominant_wave_number"] == pytest.approx(1.0 / expected_st)
    assert spectrum["window"] == "hann"
    assert spectrum["detrend"] == "constant"


def test_cummings_package_sets_topology_specific_production_duration() -> None:
    config = default_study_config()
    config["temporal_packages"]["active"] = "cummings_closed_low_cost"
    condition = dict(config["operating_condition"])
    condition["tc_s"] = 0.02
    for topology, expected in (("closed", 100.0), ("open", 200.0)):
        effective, package = _plan_config_for_topology(config, topology)
        plan = _stage_plan(dt_s=0.0001, condition=condition, config=effective)
        assert package == "cummings_closed_low_cost"
        assert plan["stages"][-1]["duration_tc"] == pytest.approx(expected)


def test_partial_urans_review_writes_frequency_and_moving_statistics(tmp_path: Path) -> None:
    run_root = tmp_path / "open_medium_a08"
    output = run_root / "postprocess/URANS"
    output.mkdir(parents=True)
    time = np.arange(0.0, 2.0, 0.002)
    pd.DataFrame({
        "Time": time,
        "Cl": 0.8 + 0.05 * np.sin(2.0 * math.pi * 6.0 * time),
        "Cd": 0.08 + 0.002 * np.sin(2.0 * math.pi * 6.0 * time + 0.2),
        "Cm": -0.04 + 0.003 * np.sin(2.0 * math.pi * 6.0 * time - 0.1),
    }).to_csv(output / "forceCoeffs_raw.csv", index=False)
    (run_root / "case_metadata.json").write_text(
        json.dumps({
            "run_id": "open_medium_a08",
            "topology": "open",
            "mesh_level": "medium",
            "alpha_deg": 8.0,
            "chord_m": 1.0,
            "U_inf_m_s": 50.0,
            "operating_condition": {
                "chord_m": 1.0, "velocity_m_s": 50.0, "alpha_deg": 8.0,
            },
        }),
        encoding="utf-8",
    )
    (run_root / "stage_plan.json").write_text(
        '{"stages":[{"stage":"D","start_s":0.0,"end_s":1.0,"dt_s":0.002},'
        '{"stage":"E","start_s":1.0,"end_s":2.0,"dt_s":0.002}]}',
        encoding="utf-8",
    )
    report = review_run(run_root)
    assert report["sampling_window"]["stage"] == "E"
    assert report["frequency_method"]["reference_length_formula"] == "L=t*cos(alpha)+c*sin(alpha)"
    assert (run_root / "plots/urans_review/lift_psd_frequency.png").is_file()
    assert (run_root / "plots/urans_review/lift_psd_strouhal.png").is_file()
    assert (run_root / "plots/urans_review/lift_psd_wave_number.png").is_file()
    assert (run_root / "plots/urans_review/moving_statistics.png").is_file()
