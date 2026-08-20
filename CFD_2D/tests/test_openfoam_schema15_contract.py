from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "CFD_2D" / "app"
SCRIPTS = ROOT / "CFD_2D" / "scripts"
for location in (APP, SCRIPTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from workflow_backend import (  # noqa: E402
    SOLVER_CONFIG_SCHEMA_VERSION,
    case_writer_command,
    migrate_solver_config_schema,
)
from ramair_2d_openfoam_case_writer import (  # noqa: E402
    OpenFOAMCaseConfig,
    case_input_summary,
    load_physical,
    write_system,
)


def residual(fields: tuple[str, ...]) -> dict[str, object]:
    return {
        "enabled": True,
        "fields": {
            name: {"tolerance": 1.0e-4, "relTol": 0.0}
            for name in fields
        },
    }


def test_schema14_migration_sets_reviewed_closed_open_contract() -> None:
    migrated = migrate_solver_config_schema({
        "config_schema_version": 14,
        "n_outer_correctors": 15,
        "steady_max_iterations": 10000,
        "outer_corrector_residual_control": {
            "enabled": True,
            "U_tolerance": 1.0e-4,
            "nuTilda_tolerance": 1.0e-4,
            "relative_tolerance": 0.0,
        },
        "topology_profiles": {
            "open_internal_cavity": {
                "maxCo": 20.0,
                "n_outer_correctors": 15,
                "steady_max_iterations": 10000,
                "outer_corrector_residual_control": {
                    "enabled": True,
                    "U_tolerance": 1.0e-4,
                    "nuTilda_tolerance": 1.0e-4,
                    "relative_tolerance": 0.0,
                },
            }
        },
    })
    opened = migrated["topology_profiles"]["open_internal_cavity"]
    assert migrated["config_schema_version"] == SOLVER_CONFIG_SCHEMA_VERSION == 15
    assert migrated["n_outer_correctors"] == 10
    assert opened["n_outer_correctors"] == 15
    assert opened["maxCo"] == pytest.approx(25.0)
    assert list(migrated["outer_corrector_residual_control"]["fields"]) == ["U", "nuTilda"]
    assert list(opened["outer_corrector_residual_control"]["fields"]) == ["U", "p"]
    assert migrated["steady_max_iterations"] == opened["steady_max_iterations"] == 20000
    assert migrated["field_write_step_equivalent"] == 2000


def test_writer_command_has_no_physical_override() -> None:
    command = case_writer_command(
        ROOT,
        variant="open_ramair",
        alpha=4.0,
        reynolds=9.9e9,
        require_converted_polymesh=True,
    )
    assert "--reynolds" not in command


def test_writer_rejects_a_reynolds_override_different_from_cfd_case() -> None:
    with pytest.raises(ValueError, match="no longer overrides the CFD Case"):
        load_physical(ROOT, 4.0, 9.9e9, "open_ramair")


def test_requested_adaptive_dt_is_a_ceiling_and_drives_2000_step_writes() -> None:
    cfg = OpenFOAMCaseConfig(
        velocity_m_s=20.0,
        chord_m=1.0,
        time_step_mode="adaptive_physics_limited",
        deltaT_star=0.02,
        maxDeltaT_star=0.005,
        field_write_step_equivalent=2000,
    )
    assert cfg.deltaT == pytest.approx(cfg.maxDeltaT)
    assert cfg.field_write_interval == pytest.approx(cfg.maxDeltaT * 2000)


def test_closed_and_open_residual_fields_are_written_exactly(tmp_path: Path) -> None:
    closed = tmp_path / "closed"
    opened = tmp_path / "open"
    write_system(
        closed,
        OpenFOAMCaseConfig(n_outer_correctors=10, outer_corrector_residual_control=residual(("U", "nuTilda"))),
        [{"name": "airfoil_wall", "type": "wall"}],
    )
    write_system(
        opened,
        OpenFOAMCaseConfig(
            geometry_topology="open_internal_cavity",
            n_outer_correctors=15,
            maxCo=25.0,
            outer_corrector_residual_control=residual(("U", "p")),
        ),
        [{"name": "airfoil_wall_external", "type": "wall"}],
    )
    closed_solution = (closed / "system/fvSolution").read_text(encoding="utf-8")
    open_solution = (opened / "system/fvSolution").read_text(encoding="utf-8")
    assert "nOuterCorrectors 10;" in closed_solution
    assert "nuTilda\n" in closed_solution
    assert "nOuterCorrectors 15;" in open_solution
    residual_block = open_solution.split("outerCorrectorResidualControl", 1)[1]
    assert "        p\n" in residual_block
    assert "        nuTilda\n" not in residual_block
    assert "maxCo           25;" in (opened / "system/controlDict").read_text(encoding="utf-8")


def test_summary_traces_transport_writes_and_physical_ownership(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(
        transport_correction_final=False,
        outer_corrector_residual_control=residual(("U", "nuTilda")),
    )
    summary = case_input_summary(cfg, tmp_path, None, [{"name": "airfoil_wall", "type": "wall"}])
    assert "every outer corrector" in summary["transport_correction"]["effective_behavior"]
    assert summary["field_write_step_equivalent"] == 2000
    assert summary["physical_input_ownership"]["solver_override_allowed"] is False
    assert summary["purge_write_scope"].startswith("volume fields only")


def test_canonical_schema15_json_matches_contract() -> None:
    data = json.loads((ROOT / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json").read_text(encoding="utf-8"))
    assert data["config_schema_version"] == 15
    assert data["maxCo"] == pytest.approx(50.0)
    assert data["topology_profiles"]["open_internal_cavity"]["maxCo"] == pytest.approx(25.0)
