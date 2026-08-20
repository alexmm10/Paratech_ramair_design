from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ramair_2d_openfoam_case_writer import (  # noqa: E402
    OpenFOAMCaseConfig,
    write_case_input_summary,
)
from ramair_2d_timestep_advisor import (  # noqa: E402
    build_timestep_assessment,
    read_required_json,
    temporal_frequency_budget,
    topology_temporal_config,
)


def temporal_profile() -> dict:
    return {
        "profile_id": "test",
        "target_min_strouhal": 0.05,
        "target_max_strouhal": 20.0,
        "target_samples_per_cycle": 20,
        "minimum_cycles_for_statistics": 10,
        "smoke_end_time_star": 20.0,
        "time_step_study_values_star": [0.01, 0.005, 0.0025],
    }


def test_frequency_budget_uses_strouhal_and_equal_physical_duration() -> None:
    budget = temporal_frequency_budget(
        delta_t_star=0.01,
        max_delta_t_star=0.0125,
        end_time_star=20.0,
        average_from_fraction=0.6,
        temporal_config=temporal_profile(),
    )
    assert budget["nyquist_deltaT_star_ceiling"] == pytest.approx(0.025)
    assert budget["engineering_deltaT_star_ceiling"] == pytest.approx(0.0025)
    assert budget["configured_initial_samples_per_fastest_cycle"] == pytest.approx(5.0)
    assert budget["minimum_average_window_time_star"] == pytest.approx(200.0)
    assert budget["configured_average_window_time_star"] == pytest.approx(8.0)
    assert [
        row["samples_per_cycle_at_target_St_max"]
        for row in budget["time_step_study_samples_per_fastest_cycle"]
    ] == pytest.approx([5.0, 10.0, 20.0])


def test_real_courant_measurement_identifies_mesh_limiter() -> None:
    report = build_timestep_assessment(
        chord_m=1.0,
        velocity_m_s=50.0,
        topology="closed_external_airfoil",
        time_step_mode="adaptive_courant",
        delta_t_star=0.01,
        max_delta_t_star=0.0125,
        max_co=1.0,
        end_time_star=20.0,
        average_from_fraction=0.6,
        temporal_config=temporal_profile(),
        courant_diagnostics={
            "status": "DIAGNOSED_FROM_REAL_SOLVER_LOG",
            "measured_final": {
                "deltaT_star": 2.5e-5,
                "courant_max": 1.0,
            },
            "maximum_courant_cell": {
                "cell_id": 123,
                "location": [1.0, -0.01, 0.005],
                "maximum_Co": 1.0,
            },
        },
    )
    mesh = report["mesh_courant_limit"]
    assert mesh["active_limiter"] == "LOCAL_MESH_COURANT"
    assert mesh["fraction_of_engineering_frequency_ceiling"] == pytest.approx(0.01)
    assert any("mesh/Courant" in warning for warning in report["warnings"])
    assert any("smoke test" in warning for warning in report["warnings"])


def test_open_topology_overrides_only_temporal_profile_values() -> None:
    solver = {
        "temporal_accuracy": {
            "target_min_strouhal": 0.1,
            "target_max_strouhal": 10.0,
            "target_samples_per_cycle": 20,
        },
        "topology_profiles": {
            "open_internal_cavity": {
                "temporal_accuracy": {
                    "target_min_strouhal": 0.05,
                    "profile_id": "open",
                }
            }
        },
    }
    effective = topology_temporal_config(solver, "open_internal_cavity")
    assert effective["profile_id"] == "open"
    assert effective["target_min_strouhal"] == pytest.approx(0.05)
    assert effective["target_max_strouhal"] == pytest.approx(10.0)
    assert effective["target_samples_per_cycle"] == 20


def test_case_summary_writes_standalone_timestep_assessment(tmp_path: Path) -> None:
    mesh_root = tmp_path / "mesh"
    mesh_root.mkdir()
    (mesh_root / "mesh_quality_report.json").write_text(
        json.dumps({"checkMesh_cell_count": 1000}),
        encoding="utf-8",
    )
    cfg = OpenFOAMCaseConfig(
        chord_m=1.0,
        velocity_m_s=50.0,
        deltaT_star=0.01,
        maxDeltaT_star=0.0125,
        endTime_star=20.0,
        average_from_fraction=0.6,
        temporal_accuracy=temporal_profile(),
    )
    write_case_input_summary(
        tmp_path,
        cfg,
        mesh_root,
        None,
        [
            {"name": "farfield", "type": "patch"},
            {"name": "airfoil_wall", "type": "wall"},
            {"name": "frontAndBack", "type": "empty"},
        ],
    )
    report = json.loads(
        (tmp_path / "time_step_assessment.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (tmp_path / "case_input_summary.json").read_text(encoding="utf-8")
    )
    assert report["physical_scales"]["convective_time_s"] == pytest.approx(0.02)
    assert summary["time_step_assessment"]["profile_id"] == "test"
    markdown = (tmp_path / "time_step_assessment.md").read_text(encoding="utf-8")
    assert "Nyquist ceiling" in markdown
    assert "not proof" in markdown


def test_required_json_rejects_missing_and_invalid_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Solver configuration"):
        read_required_json(tmp_path / "missing.json", "Solver configuration")
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"deltaT_star": }', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1, column"):
        read_required_json(invalid, "Solver configuration")
