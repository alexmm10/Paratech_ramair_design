from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mesh_configuration import mesh_level_values  # noqa: E402
from ramair_2d_mesh_science import (  # noqa: E402
    BOUNDARY_LAYER_LAYER_POLICY,
    Y_PLUS_TARGETS,
    boundary_layer_stack,
    build_report,
    first_cell_height_audit,
)


def test_paper_y_plus_sequence_is_fractional_not_a_band() -> None:
    assert Y_PLUS_TARGETS == pytest.approx(
        {"coarse": 1.0, "medium": 2 / 3, "fine": 4 / 9, "extra_fine": 8 / 27}
    )


def test_level_bases_apply_paper_targets_without_changing_existing_meshes() -> None:
    for level, target in Y_PLUS_TARGETS.items():
        preset = mesh_level_values(level)
        assert preset["target_y_plus"] == pytest.approx(target)
        assert preset["closed_boundary_layer_layers"] == BOUNDARY_LAYER_LAYER_POLICY[level]
        assert preset["open_boundary_layer_layers"] == BOUNDARY_LAYER_LAYER_POLICY[level]
        assert preset["open_use_yplus_first_cell_height"] is True
    assert mesh_level_values("extra_fine")["closed_boundary_layer_layers"] == 75


def test_first_cell_audit_selects_the_most_restrictive_positive_formula() -> None:
    audit = first_cell_height_audit(1.9e6, 1.0, 1.0)
    candidates = audit["candidates"]
    assert all(value > 0 for value in candidates.values())
    assert audit["selected_first_cell_height_m"] == pytest.approx(min(candidates.values()))
    assert audit["selected_source"] == "project_flat_plate_skin_friction_m"


def test_boundary_layer_50_75_policy_stays_under_twenty_percent_growth() -> None:
    stack_50 = boundary_layer_stack(1.0e-5, 1.10, 50)
    stack_75 = boundary_layer_stack(1.0e-5, 1.10, 75)
    assert stack_50["maximum_adjacent_growth_percent"] == pytest.approx(10.0)
    assert stack_75["total_thickness_m"] > stack_50["total_thickness_m"]


def test_fixture_contract_contains_curvature_transfinite_and_openfoam_groups() -> None:
    fixture_root = ROOT / "CFD_2D" / "tests" / "fixtures" / "mesh_science"
    curvature = (fixture_root / "curvature_transfinite.geo").read_text(encoding="utf-8")
    hybrid = (fixture_root / "hybrid_algorithm.geo").read_text(encoding="utf-8")
    assert "Mesh.MeshSizeFromCurvature = 80;" in curvature
    assert "__TRANSFINITE_DIRECTIVE__" in curvature
    assert "Mesh.Algorithm = __ALGORITHM__;" in hybrid
    assert 'Physical Surface("frontAndBack")' in hybrid
    assert 'Physical Surface("airfoil_wall")' in hybrid


def test_diagnostic_report_is_no_solver_and_no_replacement(tmp_path: Path) -> None:
    report = build_report(ROOT)
    assert "no CFD solver run" in report["purpose"]
    assert report["fixtures"]["status"] == "NOT_RUN"
    assert "No generated fixture" in report["production_decision"]["approval"]
