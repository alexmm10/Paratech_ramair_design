from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
sys.path.insert(0, str(SCRIPTS))

import ramair_2d_validation_campaigns as campaigns  # noqa: E402
from ramair_2d_convergence_decisions import (  # noqa: E402
    adaptive_next_cases,
    compare_evidence_pair,
    three_grid_gci,
)
from ramair_2d_open_divergence import (  # noqa: E402
    REQUIRED_DIAGNOSTICS,
    classify_open_rans_diagnostics,
)
from ramair_2d_study_registry import default_study_config  # noqa: E402


def _study(active: Path) -> dict[str, object]:
    meshes = []
    for topology in ("closed", "open"):
        for level, cells in (("coarse", 100), ("medium", 400), ("fine", 1600)):
            meshes.append({
                "id": f"{topology}_{level}",
                "topology": topology,
                "level": level,
                "cell_count": cells,
                "mesh_hash": f"hash-{topology}-{level}",
                "geometry_package": f"geometry/{topology}",
                "mesh_package": f"mesh/{topology}-{level}",
            })
    config = default_study_config()
    config["operating_condition"]["tc_s"] = 0.02
    return {
        "study_manifest": {"study_id": "study"},
        "mesh_registry": {"meshes": meshes},
        "study_config": config,
    }


def _thresholds() -> dict[str, float]:
    return dict(default_study_config()["acceptance_thresholds"])


def _eligible(value: float = 1.0) -> dict[str, object]:
    return {
        "approved": True,
        "settled": True,
        "cycles": 12,
        "uniform_sampling": True,
        "signals_continuous": True,
        "collection_time_star": 100.0,
        "statistics": {
            "mean_CL": value,
            "mean_CD": 0.1,
            "mean_CM": -0.05,
            "rms_CL": 0.02,
            "rms_CD": 0.01,
            "rms_CM": 0.01,
            "dominant_frequency": 5.0,
            "psd_peak_amplitude": 1.0,
        },
    }


def test_pair_rejects_nyquist_only_or_unequal_physical_time() -> None:
    first = _eligible()
    second = _eligible(1.001)
    second["collection_time_star"] = 50.0
    report = compare_evidence_pair(first, second, _thresholds())
    assert report["accepted"] is False
    assert "PHYSICAL_COLLECTION_TIME_MISMATCH" in report["reasons"]
    first["cycles"] = 2
    report = compare_evidence_pair(first, _eligible(), _thresholds())
    assert any("FEWER_THAN_10_CYCLES" in item for item in report["reasons"])


def test_three_grid_gci_is_saved_from_real_scalar_sequence() -> None:
    report = three_grid_gci([
        {"mesh_level": "coarse", "cell_count": 100, "mean_CL": 1.01},
        {"mesh_level": "medium", "cell_count": 400, "mean_CL": 1.0025},
        {"mesh_level": "fine", "cell_count": 1600, "mean_CL": 1.000625},
    ], value_key="mean_CL")
    assert report["status"] == "COMPUTED"
    assert report["observed_order"] == pytest.approx(2.0, rel=1.0e-4)
    assert report["gci_fine_percent"] > 0.0
    assert report["convergence_type"] == "monotonic"


def test_closed_sequence_unlocks_one_case_at_16_before_8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = tmp_path / "active"
    monkeypatch.setattr(campaigns, "load_study", lambda _root: _study(active))
    monkeypatch.setattr(campaigns, "active_workspace_root", lambda _root: active)
    campaign = campaigns.build_campaign(
        tmp_path, topology="closed", strategy="optimized", angles_deg=[16.0, 8.0]
    )
    first = adaptive_next_cases(campaign, {}, _thresholds())
    assert first["next_case_keys"] == [campaign["cases"][0]["case_key"]]
    evidence = {campaign["cases"][0]["case_key"]: _eligible()}
    second = adaptive_next_cases(campaign, evidence, _thresholds())
    assert second["next_case_keys"] == [campaign["cases"][1]["case_key"]]
    assert "ALPHA_16" in second["gate"]


def test_open_requires_rans_then_medium_then_spatial_crossing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = tmp_path / "active"
    monkeypatch.setattr(campaigns, "load_study", lambda _root: _study(active))
    monkeypatch.setattr(campaigns, "active_workspace_root", lambda _root: active)
    campaign = campaigns.build_campaign(
        tmp_path,
        topology="open",
        strategy="progressive_medium_first",
        angles_deg=[8.0],
    )
    rans = [row for row in campaign["cases"] if row["kind"] == "RANS_DIAGNOSTIC"]
    decision = adaptive_next_cases(campaign, {}, _thresholds())
    assert set(decision["next_case_keys"]) == {row["case_key"] for row in rans}

    evidence = {row["case_key"]: {"diagnostics_pass": True} for row in rans}
    medium = [
        row for row in campaign["cases"]
        if row["kind"] == "URANS" and row["mesh_level"] == "medium"
    ]
    decision = adaptive_next_cases(campaign, evidence, _thresholds())
    assert decision["next_case_keys"] == [medium[0]["case_key"]]
    evidence[medium[0]["case_key"]] = _eligible(1.0)
    evidence[medium[1]["case_key"]] = _eligible(1.001)
    decision = adaptive_next_cases(campaign, evidence, _thresholds())
    spatial = [
        row["case_key"] for row in campaign["cases"]
        if row["kind"] == "URANS"
        and row["mesh_level"] in {"coarse", "fine"}
        and row["deltaT_star"] == medium[1]["deltaT_star"]
    ]
    assert set(decision["next_case_keys"]) == set(spatial)
    assert decision["gate"] == "OPEN_ALPHA_8_SPATIAL_CROSSING"


def test_open_divergence_gate_requires_fixed_geometry_and_all_diagnostics() -> None:
    rows = []
    for level in ("coarse", "medium", "fine"):
        row = {"mesh_level": level, "geometry_revision": "same"}
        row.update({name: {"status": "MEASURED"} for name in REQUIRED_DIAGNOSTICS})
        rows.append(row)
    assert classify_open_rans_diagnostics(rows)["decision"] == "PROCEED_MEDIUM_TEMPORAL"
    rows[1]["inlet_backflow_unbounded"] = True
    held = classify_open_rans_diagnostics(rows)
    assert held["decision"] == "HOLD_FOR_RANS_DIAGNOSTIC_REVIEW"
    assert "OPEN_DIVERGENCE_REVIEW_REQUIRED" in held["reasons"]
