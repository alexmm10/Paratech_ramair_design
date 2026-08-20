from __future__ import annotations

import sys
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D" / "scripts"
APP = ROOT / "CFD_2D" / "app"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(APP))

from boundary_layer_estimates import (  # noqa: E402
    boundary_layer_comparison,
    geometric_prism_stack_thickness,
    turbulent_flat_plate_delta99,
)
from openfoam_history import (  # noqa: E402
    read_force_coefficient_history,
    read_recent_force_coefficient_history,
)
from openfoam_wall_analysis import (  # noqa: E402
    analyze_wall_boundary_layer,
    boundary_layer_velocity_ratio,
    cp_field_diagnostics,
    load_wall_cp,
    load_wall_yplus,
    read_legacy_vtk_wall,
)
from paraview_case_viewer import write_paraview_case_script  # noqa: E402
from pyfoam_solver_runner import (  # noqa: E402
    force_coefficient_divergence_reason,
    force_regexp_file,
    live_monitor_preflight,
    live_watcher_plot_options,
    selected_pyfoam_plot_files,
    solver_divergence_diagnostics,
    write_force_monitor_log,
    write_force_monitor_snapshot,
)
from ramair_live_monitor import parse_solver_monitor_data, write_static_monitor_products  # noqa: E402
from ramair_2d_openfoam_case_writer import (  # noqa: E402
    OpenFOAMCaseConfig,
    case_input_summary,
    prepare_existing_case_dir,
    time_integration_description,
    topology_solver_config,
    write_0,
    write_system,
)
from ramair_2d_openfoam_runner import (  # noqa: E402
    initial_field_preflight,
    prepare_resume,
    solver_log_has_fatal_error,
    solver_log_indicates_divergence,
    solver_log_indicates_setup_error,
)
from ramair_2d_openfoam_staged_runner import (  # noqa: E402
    audit_steady_to_transient_continuity,
    archive_steady_outputs,
    archive_failed_steady_outputs,
    create_steady_paraview_case,
    ensure_potential_phi_solver,
    ensure_steady_stability_numerics,
    reset_transient_time_origin,
    steady_force_plateau,
)
from ramair_2d_postprocess import (  # noqa: E402
    derived_field_inventory,
    plot_delta_t,
    plot_force_coeffs,
    write_aerodynamic_efficiency_products,
)
from ramair_2d_profile_case_builder import chord_m_for_variant_request  # noqa: E402
from ramair_2d_scale_validation_geometry import build_scaled_variant  # noqa: E402
from ramair_2d_solver_performance import benchmark_from_log  # noqa: E402
from ramair_2d_courant_diagnostics import diagnose  # noqa: E402
from ramair_2d_validation import (  # noqa: E402
    _result_record,
    collect_ramair_points,
    generate_validation_report,
    update_active_workspace_validation,
)
from workflow_backend import (  # noqa: E402
    openfoam_case_from_command,
    prepare_existing_simulation,
    request_openfoam_clean_stop,
    request_openfoam_sweep_stop,
)


def test_boundary_layer_estimates_use_geometric_stack_and_flat_plate_delta99() -> None:
    assert geometric_prism_stack_thickness(1.0e-4, 3, 2.0) == pytest.approx(7.0e-4)
    delta = turbulent_flat_plate_delta99(chord_m=1.0, reynolds_chord=4.0e6, x_over_chord=1.0)
    assert 0.015 < delta < 0.020
    result = boundary_layer_comparison(
        chord_m=1.0,
        reynolds=4.0e6,
        target_y_plus=1.0,
        rho_kg_m3=1.225,
        mu_pa_s=1.81e-5,
        layers=50,
        growth_rate=1.1,
        use_yplus_y1=True,
    )
    assert result["y1_m"] > 0.0
    assert result["prism_stack_thickness_m"] > result["y1_m"]
    assert result["prism_to_theoretical_delta99_ratio"] > 0.0
    manual = boundary_layer_comparison(
        chord_m=1.0,
        reynolds=1.9e6,
        target_y_plus=1.0,
        rho_kg_m3=0.666,
        mu_pa_s=1.7894e-5,
        layers=50,
        growth_rate=1.1,
        manual_y1_m=5.0e-5,
        use_yplus_y1=False,
    )
    assert manual["y1_m"] == pytest.approx(5.0e-5)
    assert manual["y1_over_chord"] == pytest.approx(5.0e-5)


def test_validation_geometry_scaling_rebuilds_dimensional_coordinates(tmp_path: Path) -> None:
    (tmp_path / "preprocess_ramair_main.py").write_text("# project marker\n", encoding="utf-8")
    for relative in (
        "CFD_2D/CFD_2D_inputs/geometry/reference_uncut",
        "CFD_2D/CFD_2D_inputs/case_package/reference_uncut",
    ):
        shutil.copytree(ROOT / relative, tmp_path / relative)
    report = build_scaled_variant(tmp_path)
    assert report["target_chord_m"] == 1.0
    points = pd.read_csv(
        tmp_path / "CFD_2D/CFD_2D_inputs/geometry/reference_uncut_validation_1m/profile_points.csv"
    )
    assert points["x_m"].max() == pytest.approx(points["x_norm"].max())
    manifest = json.loads(
        (
            tmp_path
            / "CFD_2D/CFD_2D_inputs/case_package/reference_uncut_validation_1m/manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["chord_m"] == 1.0
    assert manifest["geometry_scaling"]["source_chord_m"] == pytest.approx(3.016)


def test_physical_case_uses_selected_validation_variant_chord() -> None:
    assert chord_m_for_variant_request(ROOT, "reference_uncut_validation_1m") == pytest.approx(1.0)


def test_solver_performance_projection_uses_convective_time_and_adaptive_dt(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "case_config.json").write_text(
        json.dumps({"chord_m": 1.0, "velocity_m_s": 50.0, "deltaT_star": 0.01}),
        encoding="utf-8",
    )
    (case / "case_input_summary.json").write_text(
        json.dumps({"estimated_storage": {"mesh_cell_count": 334857}}),
        encoding="utf-8",
    )
    log = case / "log.foamRun"
    log.write_text(
        "deltaT = 1e-5\nTime = 0.001s\nExecutionTime = 10 s  ClockTime = 10 s\n"
        "deltaT = 2e-5\nTime = 0.002s\nExecutionTime = 20 s  ClockTime = 20 s\n",
        encoding="utf-8",
    )
    report = benchmark_from_log(case, log, [2.0])
    assert report["benchmark"]["convective_time_reached_star"] == pytest.approx(0.1)
    assert report["benchmark"]["seconds_per_convective_time_star"] == pytest.approx(200.0)
    assert report["projections"][0]["projected_wall_seconds"] == pytest.approx(400.0)
    assert report["mesh_cells"] == 334857


def test_wall_analysis_uses_actual_mesh_snapshot_and_quality_y1(tmp_path: Path) -> None:
    mesh_root = tmp_path / "CFD_2D/meshes/reference_uncut_validation_1m"
    mesh_root.mkdir(parents=True)
    (mesh_root / "mesh_config_used.json").write_text(
        json.dumps({
            "target_y_plus": 1.0,
            "closed_use_yplus_first_cell_height": True,
            "closed_first_cell_height_m": 5.0e-5,
            "closed_boundary_layer_layers": 50,
            "closed_boundary_layer_growth": 1.1,
        }),
        encoding="utf-8",
    )
    (mesh_root / "mesh_quality_report.json").write_text(
        json.dumps({
            "boundary_layer_first_cell_height_m": 1.296454e-5,
            "boundary_layer_total_thickness_chord": 0.01508954,
        }),
        encoding="utf-8",
    )
    case = tmp_path / "case"
    case.mkdir()
    (case / "case_config.json").write_text(
        json.dumps({"mesh_root": "CFD_2D/meshes/reference_uncut_validation_1m"}),
        encoding="utf-8",
    )
    (case / "case_input_summary.json").write_text(
        json.dumps({
            "chord_m": 1.0,
            "reynolds": 1.9e6,
            "rho_kg_m3": 0.66606662,
            "mu_Pa_s": 1.7894e-5,
            "velocity_m_s": 51.043843,
        }),
        encoding="utf-8",
    )
    report = analyze_wall_boundary_layer(
        project_root=tmp_path,
        case_dir=case,
        output_dir=tmp_path / "results",
        variant="reference_uncut_validation_1m",
        run_openfoam_tools=False,
        timeout_s=10,
        stations_xc=[0.1],
        sample_points=10,
        solver_module="incompressibleFluid",
    )
    assert report["estimate"]["y1_m"] == pytest.approx(1.296454e-5)
    assert report["estimate"]["y1_source"] == "mesh_quality_report_actual"
    assert report["estimate"]["prism_stack_thickness_m"] == pytest.approx(0.01508954)
    assert report["mesh_config_source"].endswith("mesh_config_used.json")


def test_force_history_aggregates_restart_segments_and_replaces_overlap(tmp_path: Path) -> None:
    first = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    second = tmp_path / "postProcessing" / "forceCoeffs" / "2" / "forceCoeffs.dat"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    header = "# Time Cm Cd Cl\n"
    first.write_text(header + "0 0.01 0.1 0.2\n1 0.02 0.2 0.3\n", encoding="utf-8")
    second.write_text(header + "1 0.03 0.25 0.35\n2 0.04 0.3 0.4\n", encoding="utf-8")
    records, sources = read_force_coefficient_history(tmp_path)
    assert [row["Time"] for row in records] == [0.0, 1.0, 2.0]
    assert records[1]["Cl"] == pytest.approx(0.35)
    assert len(sources) == 2


def test_ascii_wall_vtk_reader_returns_face_centres_and_yplus(tmp_path: Path) -> None:
    vtk = tmp_path / "airfoil_wall.vtk"
    vtk.write_text(
        """# vtk DataFile Version 2.0
wall
ASCII
DATASET POLYDATA
POINTS 4 float
0 0 0  1 0 0  1 0 1  0 0 1
POLYGONS 1 5
4 0 1 2 3
CELL_DATA 1
FIELD attributes 1
yPlus 1 1 float
0.75
""",
        encoding="utf-8",
    )
    data = read_legacy_vtk_wall(vtk)
    assert len(data) == 1
    assert data.iloc[0]["x_m"] == pytest.approx(0.5)
    assert data.iloc[0]["z_m"] == pytest.approx(0.5)
    assert data.iloc[0]["yPlus"] == pytest.approx(0.75)


def test_wall_vtk_loader_skips_newer_file_that_contains_another_field(tmp_path: Path) -> None:
    boundary = tmp_path / "constant/polyMesh/boundary"
    boundary.parent.mkdir(parents=True)
    boundary.write_text("airfoil_wall\n{\n type wall;\n nFaces 1;\n}\n", encoding="utf-8")
    vtk_dir = tmp_path / "VTK/airfoil_wall"
    vtk_dir.mkdir(parents=True)

    def write_scalar(path: Path, field: str, value: float) -> None:
        path.write_text(
            "# vtk DataFile Version 2.0\nwall\nASCII\nDATASET POLYDATA\n"
            "POINTS 4 float\n0 0 0  1 0 0  1 0 1  0 0 1\n"
            "POLYGONS 1 5\n4 0 1 2 3\nCELL_DATA 1\nFIELD attributes 1\n"
            f"{field} 1 1 float\n{value}\n",
            encoding="utf-8",
        )

    yplus_vtk = vtk_dir / "airfoil_wall_1.vtk"
    cp_vtk = vtk_dir / "airfoil_wall_2.vtk"
    write_scalar(yplus_vtk, "yPlus", 1.25)
    write_scalar(cp_vtk, "Cp", -0.4)
    os.utime(yplus_vtk, (100.0, 100.0))
    os.utime(cp_vtk, (200.0, 200.0))
    yplus, y_sources = load_wall_yplus(tmp_path, 1.0)
    cp, cp_sources = load_wall_cp(tmp_path, 1.0)
    assert yplus.iloc[0]["yPlus"] == pytest.approx(1.25)
    assert cp.iloc[0]["Cp"] == pytest.approx(-0.4)
    assert yplus.iloc[0]["wall_side"] == "combined"
    assert cp.iloc[0]["wall_side"] == "combined"
    assert y_sources == [str(yplus_vtk)]
    assert cp_sources == [str(cp_vtk)]


def test_open_wall_loader_preserves_external_internal_patch_role(tmp_path: Path) -> None:
    boundary = tmp_path / "constant/polyMesh/boundary"
    boundary.parent.mkdir(parents=True)
    boundary.write_text(
        "airfoil_wall_external\n{\n type wall;\n nFaces 1;\n}\n"
        "airfoil_wall_internal\n{\n type wall;\n nFaces 1;\n}\n",
        encoding="utf-8",
    )
    for patch, y_value, field_value in (
        ("airfoil_wall_external", 0.08, 0.8),
        ("airfoil_wall_internal", 0.06, 0.4),
    ):
        vtk_dir = tmp_path / "VTK" / patch
        vtk_dir.mkdir(parents=True)
        (vtk_dir / f"{patch}_1.vtk").write_text(
            "# vtk DataFile Version 2.0\nwall\nASCII\nDATASET POLYDATA\n"
            f"POINTS 4 float\n0 {y_value} 0  1 {y_value} 0  "
            f"1 {y_value} 1  0 {y_value} 1\n"
            "POLYGONS 1 5\n4 0 1 2 3\nCELL_DATA 1\nFIELD attributes 1\n"
            f"yPlus 1 1 float\n{field_value}\n",
            encoding="utf-8",
        )
    data, _ = load_wall_yplus(tmp_path, 1.0)
    assert set(data["wall_side"]) == {"external", "internal"}


def test_writer_creates_separate_simple_templates(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(chord_m=1.0, velocity_m_s=20.0)
    write_system(tmp_path, cfg, [{"name": "airfoil_wall", "type": "wall"}, {"name": "frontAndBack", "type": "empty"}])
    steady = tmp_path / "system" / "steadyInitialization"
    assert "steadyState" in (steady / "fvSchemes").read_text(encoding="utf-8")
    solution = (steady / "fvSolution").read_text(encoding="utf-8")
    assert "SIMPLE" in solution
    assert "residualControl" in solution
    assert "pRefCell        0;" in solution
    assert "pRefValue       0;" in solution
    assert "Phi" in solution
    assert "smoother DIC;" in solution
    assert "nNonOrthogonalCorrectors 0;" in solution
    assert "equations { U 0.5; nuTilda 0.5; }" in solution
    schemes = (steady / "fvSchemes").read_text(encoding="utf-8")
    assert "grad(U)          cellLimited Gauss linear 1;" in schemes
    assert "grad(nuTilda)    cellLimited Gauss linear 1;" in schemes
    assert "div(phi,U) bounded Gauss linearUpwind limited;" in schemes
    assert "div(phi,nuTilda) bounded Gauss upwind;" in schemes
    assert "solver PBiCGStab;" in solution
    assert "preconditioner DILU;" in solution
    assert "PIMPLE" in (tmp_path / "system" / "fvSolution").read_text(encoding="utf-8")


def test_writer_applies_configured_transient_order_and_pimple_correctors(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(
        chord_m=1.0,
        velocity_m_s=20.0,
        ddt_scheme="backward",
        n_outer_correctors=3,
        n_correctors=2,
        n_non_orthogonal_correctors=0,
    )
    write_system(
        tmp_path,
        cfg,
        [{"name": "airfoil_wall", "type": "wall"}, {"name": "frontAndBack", "type": "empty"}],
    )
    schemes = (tmp_path / "system" / "fvSchemes").read_text(encoding="utf-8")
    solution = (tmp_path / "system" / "fvSolution").read_text(encoding="utf-8")
    control = (tmp_path / "system" / "controlDict").read_text(encoding="utf-8")
    assert "ddtSchemes { default backward; }" in schemes
    assert "nOuterCorrectors 3;" in solution
    assert "nCorrectors 2;" in solution
    assert "nNonOrthogonalCorrectors 0;" in solution
    assert "pressureCoefficient" in control
    assert "result          Cp;" in control
    assert "calcCoeff       yes;" in control
    assert "type            CourantNo;" in control
    assert "result          Co;" in control
    assert "executeControl  writeTime;" in control
    description = time_integration_description(cfg)
    assert "transient PIMPLE, backward ddt" in description
    assert "first-order Euler ddt" not in description


def test_writer_omits_blank_optional_openfoam_controls(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(
        chord_m=1.0,
        velocity_m_s=20.0,
        n_outer_correctors=None,
        n_non_orthogonal_correctors=None,
        steady_n_non_orthogonal_correctors=None,
        steady_residual_p=None,
        steady_residual_U=None,
        steady_residual_nuTilda=None,
        steady_relaxation_p=None,
        steady_relaxation_U=None,
        steady_relaxation_nuTilda=None,
    )
    write_system(
        tmp_path,
        cfg,
        [{"name": "airfoil_wall", "type": "wall"}, {"name": "frontAndBack", "type": "empty"}],
    )
    transient = (tmp_path / "system" / "fvSolution").read_text(encoding="utf-8")
    steady = (tmp_path / "system" / "steadyInitialization" / "fvSolution").read_text(encoding="utf-8")
    assert "nOuterCorrectors" not in transient
    assert "nCorrectors 2;" in transient
    assert "nNonOrthogonalCorrectors" not in transient
    assert "nNonOrthogonalCorrectors" not in steady
    assert "residualControl" not in steady
    assert "relaxationFactors" not in steady


def test_writer_integrates_every_wall_patch_as_one_rigid_body(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(chord_m=1.0, velocity_m_s=20.0)
    patches = [
        {"name": "external_skin", "type": "wall"},
        {"name": "internal_skin", "type": "wall"},
        {"name": "upper_lip", "type": "wall"},
        {"name": "lower_lip", "type": "wall"},
        {"name": "farfield", "type": "patch"},
        {"name": "frontAndBack", "type": "empty"},
    ]
    write_system(tmp_path, cfg, patches)
    control = (tmp_path / "system" / "controlDict").read_text(encoding="utf-8")
    expected = "patches         (external_skin internal_skin upper_lip lower_lip);"
    assert expected in control
    assert "patches         (farfield" not in control


def test_open_wall_topology_is_explicit_and_does_not_require_baffles(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(
        chord_m=1.0,
        velocity_m_s=20.0,
        geometry_topology="open_internal_cavity",
    )
    patches = [
        {"name": "airfoil_wall_external", "type": "wall"},
        {"name": "airfoil_wall_internal", "type": "wall"},
        {"name": "farfield", "type": "patch"},
        {"name": "frontAndBack", "type": "empty"},
    ]
    audit = case_input_summary(cfg, tmp_path, None, patches)[
        "open_airfoil_wall_topology_audit"
    ]
    assert audit["status"] == "PASS"
    assert audit["create_baffles_required"] is False
    assert audit["inlet_is_connected_fluid_not_patch"] is True


def test_general_urans_uses_adaptive_outer_exit_but_validation_does_not(tmp_path: Path) -> None:
    controls = {
        "enabled": True,
        "fields": {
            "U": {"tolerance": 1.0e-4, "relTol": 0.0},
            "nuTilda": {"tolerance": 1.0e-4, "relTol": 0.0},
        },
    }
    general = tmp_path / "general"
    write_system(
        general,
        OpenFOAMCaseConfig(
            n_outer_correctors=10,
            outer_corrector_residual_control=controls,
            transport_correction_final=False,
        ),
        [{"name": "airfoil_wall", "type": "wall"}],
    )
    general_solution = (general / "system/fvSolution").read_text(encoding="utf-8")
    assert "nOuterCorrectors 10;" in general_solution
    assert "outerCorrectorResidualControl" in general_solution
    assert "transportCorrectionFinal false;" in general_solution

    validation = tmp_path / "validation"
    write_system(
        validation,
        OpenFOAMCaseConfig(
            n_outer_correctors=3,
            outer_corrector_residual_control=controls,
            validation_fixed_subiterations=True,
        ),
        [{"name": "airfoil_wall", "type": "wall"}],
    )
    validation_solution = (validation / "system/fvSolution").read_text(encoding="utf-8")
    assert "nOuterCorrectors 3;" in validation_solution
    assert "outerCorrectorResidualControl" not in validation_solution


@pytest.mark.parametrize(
    ("mode", "expected_adjustment", "expects_courant_controls"),
    [
        ("adaptive_courant", "adjustTimeStep  yes;", True),
        ("adaptive_physics_limited", "adjustTimeStep  yes;", True),
        ("fixed", "adjustTimeStep  no;", False),
    ],
)
def test_case_writer_distinguishes_fixed_and_adaptive_time_step(
    tmp_path: Path,
    mode: str,
    expected_adjustment: str,
    expects_courant_controls: bool,
) -> None:
    case = tmp_path / mode
    cfg = OpenFOAMCaseConfig(
        chord_m=1.0,
        velocity_m_s=50.0,
        time_step_mode=mode,
        deltaT_star=0.0125,
        maxDeltaT_star=0.02,
        maxCo=1.0,
    )
    write_system(
        case,
        cfg,
        [
            {"name": "airfoil_wall", "type": "wall"},
            {"name": "farfield", "type": "patch"},
            {"name": "frontAndBack", "type": "empty"},
        ],
    )
    control = (case / "system" / "controlDict").read_text(encoding="utf-8")
    assert expected_adjustment in control
    assert ("maxCo           " in control) is expects_courant_controls
    assert ("maxDeltaT       " in control) is expects_courant_controls


@pytest.mark.parametrize(
    ("delta_t", "courant", "expected"),
    [
        (2.5e-4, 0.25, "MAX_DELTA_T"),
        (2.5e-6, 0.99, "MAX_CO"),
    ],
)
def test_courant_diagnostics_identifies_active_time_step_limit(
    tmp_path: Path,
    delta_t: float,
    courant: float,
    expected: str,
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "case_config.json").write_text(
        json.dumps(
            {
                "velocity_m_s": 50.0,
                "chord_m": 1.0,
                "maxCo": 1.0,
                "maxDeltaT_star": 0.0125,
            }
        ),
        encoding="utf-8",
    )
    log = case / "log.foamRun"
    log.write_text(
        f"Courant Number mean: 0.001 max: {courant}\n"
        f"deltaT = {delta_t}\n"
        "Time = 0.01s\n",
        encoding="utf-8",
    )
    report = diagnose(case, log)
    assert report["active_limiter"] == expected


def test_topology_profiles_distinguish_closed_and_open_numerics() -> None:
    raw = {
        "n_outer_correctors": 3,
        "steady_numerics": {"U_relaxation": 0.5},
        "topology_profiles": {
            "closed_external_airfoil": {
                "profile_id": "closed",
                "n_outer_correctors": 1,
                "steady_numerics": {"U_relaxation": 0.6},
            },
            "open_internal_cavity": {
                "profile_id": "open",
                "n_outer_correctors": 2,
                "maxCo": 0.7,
                "steady_numerics": {"U_relaxation": 0.4},
            },
        },
    }
    closed, closed_topology, closed_profile = topology_solver_config(
        raw,
        {"has_ram_air_opening_feature": False},
        "reference_uncut",
    )
    opened, open_topology, open_profile = topology_solver_config(
        raw,
        {"has_ram_air_opening_feature": True},
        "open_ramair",
    )
    assert (closed_topology, closed_profile, closed["n_outer_correctors"]) == (
        "closed_external_airfoil",
        "closed",
        1,
    )
    assert (open_topology, open_profile, opened["n_outer_correctors"], opened["maxCo"]) == (
        "open_internal_cavity",
        "open",
        2,
        0.7,
    )
    assert closed["steady_numerics"]["U_relaxation"] == pytest.approx(0.6)
    assert opened["steady_numerics"]["U_relaxation"] == pytest.approx(0.4)


def test_canonical_open_solver_uses_requested_courant_and_steady_window() -> None:
    solver = json.loads(
        (ROOT / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = json.loads(
        (ROOT / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json").read_text(
            encoding="utf-8"
        )
    )
    assert solver["config_schema_version"] >= 15
    assert solver["time_step_mode"] == "adaptive_physics_limited"
    assert (
        solver["topology_profiles"]["open_internal_cavity"]["time_step_mode"]
        == "adaptive_physics_limited"
    )
    assert solver["maxCo"] == pytest.approx(50.0)
    assert solver["topology_profiles"]["open_internal_cavity"]["maxCo"] == pytest.approx(25.0)
    assert solver["n_outer_correctors"] == 10
    assert solver["topology_profiles"]["open_internal_cavity"]["n_outer_correctors"] == 15
    assert list(solver["outer_corrector_residual_control"]["fields"]) == ["U", "nuTilda"]
    assert list(solver["topology_profiles"]["open_internal_cavity"]["outer_corrector_residual_control"]["fields"]) == ["U", "p"]
    assert solver["steady_max_iterations"] == 20000
    assert solver["field_write_step_equivalent"] == 2000
    assert solver["outer_corrector_residual_control"]["enabled"] is True
    assert workflow["execution"]["steady_force_window_samples"] >= 500


def test_writer_uses_openfoam13_freestream_farfield_by_default(tmp_path: Path) -> None:
    cfg = OpenFOAMCaseConfig(chord_m=1.0, velocity_m_s=20.0)
    patches = [
        {"name": "farfield", "type": "patch"},
        {"name": "airfoil_wall", "type": "wall"},
        {"name": "frontAndBack", "type": "empty"},
    ]
    write_0(tmp_path, cfg, patches)
    assert "type freestreamVelocity;" in (tmp_path / "0" / "U").read_text(encoding="utf-8")
    assert "type freestreamPressure;" in (tmp_path / "0" / "p").read_text(encoding="utf-8")
    assert "type freestream;" in (tmp_path / "0" / "nuTilda").read_text(encoding="utf-8")


def test_resume_uses_latest_time_and_extends_convective_duration(tmp_path: Path) -> None:
    (tmp_path / "system").mkdir()
    (tmp_path / "1.5").mkdir()
    (tmp_path / "system" / "controlDict").write_text(
        "startFrom startTime;\nstopAt endTime;\nendTime 2;\n",
        encoding="utf-8",
    )
    (tmp_path / "case_config.json").write_text(
        '{"chord_m": 2.0, "velocity_m_s": 10.0}',
        encoding="utf-8",
    )
    report = prepare_resume(tmp_path, 5.0)
    assert report["new_end_time"] == pytest.approx(2.5)
    control = (tmp_path / "system" / "controlDict").read_text(encoding="utf-8")
    assert "latestTime" in control
    assert "endTime         2.5;" in control


def test_ui_stop_targets_command_case_and_writes_runner_marker(tmp_path: Path) -> None:
    case_dir = tmp_path / "case alpha"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "system/controlDict").write_text(
        "stopAt endTime;\nrunTimeModifiable true;\n",
        encoding="utf-8",
    )
    command = [sys.executable, "runner.py", "--case", str(case_dir), "--run"]
    assert openfoam_case_from_command(command) == case_dir.resolve()
    backup = request_openfoam_clean_stop(case_dir, "writeNow")
    assert backup.is_file()
    request = json.loads((case_dir / ".ramair_stop_request.json").read_text(encoding="utf-8"))
    assert request["mode"] == "writeNow"
    assert "stopAt          writeNow;" in (case_dir / "system/controlDict").read_text(encoding="utf-8")


def test_force_plot_only_writes_averaging_window(tmp_path: Path) -> None:
    window = pd.DataFrame({"Time": [1.0, 2.0], "Cl": [0.5, 0.6], "Cd": [0.05, 0.06], "Cm": [0.01, 0.02]})
    mean = window[["Cl", "Cd", "Cm"]].mean().to_frame("mean").T
    output = tmp_path / "Cl_Cd_Cm_history.png"
    plot_force_coeffs(window, mean, output)
    assert output.is_file()
    assert not (tmp_path / "Cl_Cd_Cm_history_full.png").exists()


def test_delta_t_plot_includes_configured_ceiling(tmp_path: Path) -> None:
    history = pd.DataFrame(
        {
            "Time": [0.0, 1.0e-5, 2.0e-5],
            "deltaT": [5.0e-7, 6.0e-7, 7.0e-7],
            "Co_mean": [0.01, 0.02, 0.03],
            "Co_max": [0.8, 0.9, 1.0],
        }
    )
    output = tmp_path / "deltaT_history.png"
    plot_delta_t(history, output, maximum_delta_t_s=2.5e-4)
    assert output.is_file()
    assert output.stat().st_size > 1000


def test_validation_overlay_includes_only_matching_real_results(tmp_path: Path) -> None:
    reference_src = ROOT / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016"
    reference_dst = tmp_path / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016"
    shutil.copytree(reference_src, reference_dst)
    for alpha_dir, reynolds, mach in (("alpha_p4p000", 1.9e6, 0.15), ("alpha_p6p000", 4.0e6, 0.1)):
        result = tmp_path / "CFD_2D/results/reference_uncut" / alpha_dir
        case = tmp_path / "CFD_2D/openfoam_cases/reference_uncut" / alpha_dir
        result.mkdir(parents=True)
        case.mkdir(parents=True)
        (result / "forceCoeffs_mean.csv").write_text("Cl,Cd,Cm\n0.8,0.04,0.02\n", encoding="utf-8")
        (result / "case_summary.json").write_text(
            '{"status":"PROCESSED","run_status":{"status":"RUN_COMPLETED"}}',
            encoding="utf-8",
        )
        alpha = 4.0 if "4p" in alpha_dir else 6.0
        (case / "case_config.json").write_text(
            json.dumps({"alpha_deg": alpha, "reynolds": reynolds, "mach_input": mach}),
            encoding="utf-8",
        )
    accepted, ignored = collect_ramair_points(tmp_path)
    assert list(accepted["alpha_deg"]) == [4.0]
    assert len(ignored) == 1
    output = generate_validation_report(tmp_path)
    assert (output / "LS1_0417_CL_alpha_validation.png").is_file()
    assert (output / "LS1_0417_CD_CL_validation.png").is_file()
    summary = json.loads((output / "validation_summary.json").read_text(encoding="utf-8"))
    assert summary["ramair_points_included"] == 1
    assert (output / "validation_percentage_errors.csv").is_file()
    assert (output / "validation_max_percentage_error_summary.csv").is_file()
    assert (output / "validation_max_percentage_error_summary.png").is_file()
    assert set(summary["maximum_absolute_percentage_errors"]) == {"Cl", "Cd", "Cl/Cd"}


def test_validation_update_is_scoped_to_selected_work_case(tmp_path: Path) -> None:
    reference_src = ROOT / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016"
    shutil.copytree(reference_src, tmp_path / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016")
    workspace = tmp_path / "Results/validation_case"
    workspace.mkdir(parents=True)
    (workspace / "case_manifest.json").write_text(
        json.dumps({
            "variant": "reference_uncut",
            "validation": {"enabled": True, "variant": "reference_uncut"},
        }),
        encoding="utf-8",
    )
    state = tmp_path / "CFD_2D/app_state"
    state.mkdir(parents=True)
    (state / "active_workspace.json").write_text(
        json.dumps({"case": "validation_case", "stage": "workspace"}),
        encoding="utf-8",
    )
    result = tmp_path / "CFD_2D/results/reference_uncut/alpha_p4p000"
    case = tmp_path / "CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000"
    result.mkdir(parents=True)
    case.mkdir(parents=True)
    (result / "forceCoeffs_mean.csv").write_text("Cl,Cd,Cm\n0.8,0.04,0.02\n", encoding="utf-8")
    (result / "case_summary.json").write_text(
        '{"status":"PROCESSED","run_status":{"status":"RUN_COMPLETED"}}',
        encoding="utf-8",
    )
    (case / "case_config.json").write_text(
        json.dumps({"alpha_deg": 4.0, "reynolds": 1.9e6, "mach_input": 0.15}),
        encoding="utf-8",
    )

    output = update_active_workspace_validation(tmp_path, "reference_uncut", 4.0)

    assert output == workspace / "Validation"
    points = pd.read_csv(output / "ramair_validation_points.csv")
    assert list(points["alpha_deg"]) == [4.0]
    assert not (tmp_path / "CFD_2D/results/validation").exists()


def test_validation_update_accepts_headerless_empty_history_files(tmp_path: Path) -> None:
    reference_src = ROOT / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016"
    shutil.copytree(reference_src, tmp_path / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016")
    workspace = tmp_path / "Results/validation_case"
    validation_dir = workspace / "Validation"
    validation_dir.mkdir(parents=True)
    (workspace / "case_manifest.json").write_text(
        json.dumps({
            "variant": "reference_uncut",
            "validation": {"enabled": True, "variant": "reference_uncut"},
        }),
        encoding="utf-8",
    )
    state = tmp_path / "CFD_2D/app_state"
    state.mkdir(parents=True)
    (state / "active_workspace.json").write_text(
        json.dumps({"case": "validation_case", "stage": "workspace"}),
        encoding="utf-8",
    )
    (validation_dir / "ramair_validation_points.csv").write_text("", encoding="utf-8")
    (validation_dir / "ignored_nonmatching_results.csv").write_text("", encoding="utf-8")
    result = tmp_path / "CFD_2D/results/reference_uncut/alpha_p4p000"
    case = tmp_path / "CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000"
    result.mkdir(parents=True)
    case.mkdir(parents=True)
    (result / "forceCoeffs_mean.csv").write_text(
        "Cl,Cd,Cm\n0.8,0.04,0.02\n",
        encoding="utf-8",
    )
    (result / "case_summary.json").write_text(
        '{"status":"PROCESSED","run_status":{"status":"RUN_COMPLETED"}}',
        encoding="utf-8",
    )
    (case / "case_config.json").write_text(
        json.dumps({"alpha_deg": 4.0, "reynolds": 1.9e6, "mach_input": 0.15}),
        encoding="utf-8",
    )

    output = update_active_workspace_validation(tmp_path, "reference_uncut", 4.0)

    assert output == validation_dir
    assert list(pd.read_csv(validation_dir / "ramair_validation_points.csv")["alpha_deg"]) == [4.0]
    assert list(pd.read_csv(validation_dir / "ignored_nonmatching_results.csv").columns)


def test_field_inventory_recognizes_openfoam_gzip_fields(tmp_path: Path) -> None:
    time_dir = tmp_path / "1.0"
    output = tmp_path / "results"
    time_dir.mkdir()
    output.mkdir()
    (time_dir / "U.gz").write_bytes(b"compressed-placeholder")
    (time_dir / "p").write_text("FoamFile\n", encoding="utf-8")
    inventory = derived_field_inventory(tmp_path, output)
    assert inventory["latest_fields"]["U"] is True
    assert inventory["latest_fields"]["p"] is True
    assert inventory["latest_fields"]["yPlus"] is False


def test_pyfoam_monitor_selection_omits_unwanted_live_plots() -> None:
    source = (APP / "pyfoam_solver_runner.py").read_text(encoding="utf-8")
    assert "write_static_monitor_products" in source
    assert '"--with-courant"' not in source
    assert '"--with-execution"' not in source
    assert '"--with-deltat"' not in source
    assert "pyFoamPlotWatcher.py or solver log missing" not in source


def test_pyfoam_force_snapshot_and_selected_plot_filter(tmp_path: Path) -> None:
    force_file = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    force_file.parent.mkdir(parents=True)
    force_file.write_text("# Time Cm Cd Cl\n0 0.01 0.02 0.30\n", encoding="utf-8")
    monitor_log = tmp_path / "PyFoamForceCoeffs.logfile"
    assert write_force_monitor_snapshot(tmp_path, monitor_log) == 1
    assert "Cl=0.3 Cd=0.02 Cm=0.01" in monitor_log.read_text(encoding="utf-8")

    plots = tmp_path / "plots"
    plots.mkdir()
    for name in (
        "linear_residuals.png",
        "lift_coefficient.png",
        "drag_moment_coefficients.png",
        "PyFoam_.courant.png",
        "PyFoam_.execution.png",
    ):
        (plots / name).write_bytes(b"png")
    selected = {path.name for path in selected_pyfoam_plot_files(plots)}
    assert selected == {
        "linear_residuals.png",
        "lift_coefficient.png",
        "drag_moment_coefficients.png",
    }


def test_recent_force_reader_and_static_monitor_products_are_bounded_and_unique(tmp_path: Path) -> None:
    force_file = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    force_file.parent.mkdir(parents=True)
    rows = ["# Time Cm Cd Cl"]
    rows.extend(f"{index} 0.01 0.02 {0.3 + index * 0.001}" for index in range(2500))
    force_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
    recent, _ = read_recent_force_coefficient_history(tmp_path, max_rows=75)
    assert len(recent) == 75
    assert recent[-1]["Time"] == 2499

    solver_log = tmp_path / "PyFoamRunner.foamRun.logfile"
    solver_log.write_text(
        "Time = 2499\n"
        "smoothSolver: Solving for Ux, Initial residual = 0.1, Final residual = 1e-6, No Iterations 3\n",
        encoding="utf-8",
    )
    output = tmp_path / "postProcessing" / "PyFoamPlots"
    result = write_static_monitor_products(
        tmp_path,
        solver_log,
        output,
        force_skip_initial_samples=0,
        force_window_samples=50,
    )
    assert result["status"] == "OK"
    assert {Path(path).name for path in result["png_files"]} == {
        "linear_residuals.png",
        "lift_coefficient.png",
        "drag_moment_coefficients.png",
    }


def test_pyfoam_force_snapshot_can_skip_startup_and_keep_recent_window(tmp_path: Path) -> None:
    force_file = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    force_file.parent.mkdir(parents=True)
    rows = ["# Time Cm Cd Cl"]
    rows.extend(f"{index} {1000.0 if index == 0 else 0.01} 0.02 {0.3 + index * 0.001}" for index in range(30))
    force_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monitor_log = tmp_path / "PyFoamForceCoeffs.logfile"
    assert write_force_monitor_snapshot(tmp_path, monitor_log, skip_initial_samples=20, max_samples=5) == 5
    text = monitor_log.read_text(encoding="utf-8")
    assert "Cm=1000" not in text
    assert "Time = 29" in text


def test_pyfoam_force_monitor_uses_bounded_x11_fifo_display(tmp_path: Path) -> None:
    regexp = force_regexp_file(tmp_path).read_text(encoding="utf-8")
    assert "set yrange [-0.8:2]" in regexp
    assert live_watcher_plot_options() == [
        "--gnuplot-terminal=x11",
        "--gnuplot-use-fifo",
        "--non-persist",
    ]


def test_pyfoam_live_monitor_preflight_reports_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pyfoam_solver_runner.importlib.util.find_spec", lambda name: object())
    report = live_monitor_preflight(True)
    assert report["status"] == "READY"
    assert report["implementation"] == "PyFoam logs + headless Matplotlib snapshot embedded in Streamlit"
    assert report["coefficient_display_range"] == [-0.8, 2.0]


def test_matplotlib_live_monitor_parses_residuals_and_iterations() -> None:
    parsed = parse_solver_monitor_data(
        "Time = 10s\n"
        "smoothSolver: Solving for Ux, Initial residual = 0.1, Final residual = 1e-5, No Iterations 3\n"
        "GAMG: Solving for p, Initial residual = 0.02, Final residual = 1e-6, No Iterations 2\n"
    )
    assert parsed["residuals"]["Ux"] == [(10.0, 0.1)]
    assert parsed["linear_iterations"]["p"] == [(10.0, 2.0)]


def test_solver_divergence_diagnostics_detects_nutilda_runaway() -> None:
    diagnostics = solver_divergence_diagnostics(
        "bounding nuTilda, min: -1e2, max: 2.73e22, average: 4e18\n",
        1.5e-5,
    )
    assert diagnostics["status"] == "DIVERGED"
    assert diagnostics["first_trigger"]["reason"] == "nuTilda_runaway"


def test_normal_sigfpe_banner_is_not_reported_as_divergence() -> None:
    diagnostics = solver_divergence_diagnostics(
        "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).\n",
        1.5e-5,
    )
    assert diagnostics["status"] == "NO_DIVERGENCE_DETECTED"
    assert diagnostics["fatal_markers"] == []
    assert not solver_log_has_fatal_error(
        "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).\nEnd\n"
    )
    assert solver_log_has_fatal_error("--> FOAM FATAL IO ERROR:\nmissing entry\n")
    assert not solver_log_indicates_divergence(
        "--> FOAM FATAL IO ERROR:\ncannot find file 0/nuTilda\nMPI_ABORT was invoked\n"
    )
    assert solver_log_indicates_setup_error(
        "--> FOAM FATAL IO ERROR:\ncannot find file 0/nuTilda\n"
    )


def test_initial_field_preflight_requires_spalart_allmaras_field(tmp_path: Path) -> None:
    initial = tmp_path / "0"
    initial.mkdir()
    (initial / "U.gz").write_text("fixture", encoding="utf-8")
    (initial / "p").write_text("fixture", encoding="utf-8")

    missing = initial_field_preflight(
        tmp_path,
        {"turbulence_model": "SpalartAllmaras"},
    )
    assert missing["status"] == "MISSING"
    assert missing["missing_fields"] == ["nuTilda"]

    (initial / "nuTilda").write_text("fixture", encoding="utf-8")
    ready = initial_field_preflight(
        tmp_path,
        {"turbulence_model": "SpalartAllmaras"},
    )
    assert ready["status"] == "OK"


def test_force_coefficient_runaway_is_stopped_before_catastrophic_fields() -> None:
    trigger = force_coefficient_divergence_reason(
        {"Time": 206.0, "Cl": 22.16, "Cd": -5.47, "Cm": -2.12}
    )
    assert trigger is not None
    assert trigger["reason"] == "force_coefficient_runaway"
    assert trigger["coefficient"] == "Cl"
    assert trigger["limit"] == pytest.approx(20.0)
    assert force_coefficient_divergence_reason(
        {"Time": 100.0, "Cl": 0.8, "Cd": 0.08, "Cm": -0.1}
    ) is None


def test_force_monitor_does_not_stop_on_preexisting_resume_history(tmp_path: Path) -> None:
    force_file = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    force_file.parent.mkdir(parents=True)
    force_file.write_text("# Time Cm Cd Cl\n206 -2.12 -5.47 22.16\n", encoding="utf-8")
    historical_key = (206.0, 22.16, -5.47, -2.12)
    triggers: list[dict[str, object]] = []
    stop_event = threading.Event()
    stop_event.set()
    write_force_monitor_log(
        tmp_path,
        tmp_path / "monitor.log",
        stop_event,
        skip_initial_samples=0,
        divergence_callback=triggers.append,
        preexisting_keys={historical_key},
    )
    assert triggers == []

    write_force_monitor_log(
        tmp_path,
        tmp_path / "monitor_new.log",
        stop_event,
        skip_initial_samples=0,
        divergence_callback=triggers.append,
        preexisting_keys=set(),
    )
    assert triggers[0]["reason"] == "force_coefficient_runaway"


def test_force_monitor_detects_runaway_hidden_by_display_startup_skip(tmp_path: Path) -> None:
    force_file = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    force_file.parent.mkdir(parents=True)
    force_file.write_text("# Time Cm Cd Cl\n1 -2.12 -5.47 22.16\n", encoding="utf-8")
    triggers: list[dict[str, object]] = []
    stop_event = threading.Event()
    stop_event.set()

    write_force_monitor_log(
        tmp_path,
        tmp_path / "monitor.log",
        stop_event,
        skip_initial_samples=20,
        divergence_callback=triggers.append,
    )

    assert triggers[0]["reason"] == "force_coefficient_runaway"
    assert (tmp_path / "monitor.log").read_text(encoding="utf-8") == ""


def test_cp_diagnostics_preserves_but_labels_catastrophic_partial_field() -> None:
    ordinary = cp_field_diagnostics(pd.DataFrame({"Cp": [-1.2, 0.4, 1.0]}))
    catastrophic = cp_field_diagnostics(pd.DataFrame({"Cp": [-1.2, 1300.0]}))

    assert ordinary["status"] == "AVAILABLE"
    assert catastrophic["status"] == "NONPHYSICAL_CATASTROPHIC"
    assert catastrophic["maximum_absolute"] == pytest.approx(1300.0)
    assert catastrophic["blocks_workflow"] is False


def test_legacy_simple_template_upgrade_is_scoped_and_conservative(tmp_path: Path) -> None:
    template = tmp_path / "system" / "steadyInitialization"
    template.mkdir(parents=True)
    (template / "fvSchemes").write_text(
        "gradSchemes { default Gauss linear; }\n"
        "div(phi,U) bounded Gauss linearUpwind grad(U);\n"
        "div(phi,nuTilda) bounded Gauss linearUpwind grad(nuTilda);\n",
        encoding="utf-8",
    )
    (template / "fvSolution").write_text(
        "SIMPLE { nNonOrthogonalCorrectors 1; pRefCell 0; pRefValue 0; }\n"
        "relaxationFactors { equations { U 0.7; nuTilda 0.7; } }\n",
        encoding="utf-8",
    )
    assert set(ensure_steady_stability_numerics(template)) == {"fvSchemes", "fvSolution"}
    assert "bounded Gauss upwind" in (template / "fvSchemes").read_text(encoding="utf-8")
    assert "U 0.5; nuTilda 0.5" in (template / "fvSolution").read_text(encoding="utf-8")
    stage = json.loads((template / "stage_config.json").read_text(encoding="utf-8"))
    assert stage["numerics_profile"] == "balanced_sa_initialization_v3"
    assert stage["numerics"]["div_phi_U"] == "bounded Gauss upwind"
    assert stage["numerics"]["relaxation"] == {"p": 0.3, "U": 0.5, "nuTilda": 0.5}


def test_legacy_simple_template_gets_potential_phi_solver(tmp_path: Path) -> None:
    path = tmp_path / "fvSolution"
    path.write_text("solvers\n{\n    p { solver GAMG; }\n}\nSIMPLE {}\n", encoding="utf-8")
    assert ensure_potential_phi_solver(path) is True
    text = path.read_text(encoding="utf-8")
    assert "Phi" in text
    assert "smoother        DIC;" in text
    assert ensure_potential_phi_solver(path) is False


def test_failed_steady_stage_is_archived_and_restores_initial_zero(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    archive = case_dir / "steadyInitialization" / "history" / "run_test"
    (archive / "initial_zero").mkdir(parents=True)
    (archive / "initial_zero" / "U").write_text("initial", encoding="utf-8")
    (case_dir / "0").mkdir(parents=True)
    (case_dir / "0" / "U").write_text("potential", encoding="utf-8")
    (case_dir / "250").mkdir()
    (case_dir / "250" / "U").write_text("diverged", encoding="utf-8")
    (case_dir / "postProcessing").mkdir()
    (case_dir / "log.foamRun").write_text("failed", encoding="utf-8")
    report = archive_failed_steady_outputs(case_dir, archive)
    assert report["failed_stage"] is True
    assert (archive / "time_directories" / "250" / "U").is_file()
    assert (archive / "postProcessing").is_dir()
    assert (archive / "log.foamRun").is_file()
    assert (case_dir / "0" / "U").read_text(encoding="utf-8") == "initial"
    assert not (case_dir / "250").exists()


def test_steady_archive_creates_independent_paraview_snapshots_and_resets_transient_time(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    archive = case_dir / "steadyInitialization" / "history" / "run_test"
    (case_dir / "constant" / "polyMesh").mkdir(parents=True)
    (case_dir / "constant" / "polyMesh" / "boundary").write_text("mesh", encoding="utf-8")
    steady = case_dir / "system" / "steadyInitialization"
    steady.mkdir(parents=True)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        (steady / name).write_text("application foamRun;\n", encoding="utf-8")
    (case_dir / "system" / "controlDict").write_text(
        "startFrom latestTime;\nstartTime 100;\nstopAt writeNow;\nendTime 2;\n",
        encoding="utf-8",
    )
    (case_dir / "0").mkdir()
    for value in (100, 200, 300):
        directory = case_dir / str(value)
        directory.mkdir()
        for field in ("U", "p", "phi", "nuTilda"):
            (directory / field).write_text(f"{field}-{value}", encoding="utf-8")
    archive.mkdir(parents=True)
    report = archive_steady_outputs(
        case_dir,
        archive,
        transfer_to_transient_zero=True,
        paraview_snapshot_count=2,
    )
    assert report["steady_time_semantics"].startswith("SIMPLE iteration")
    assert report["flux_consistency"] == "steady face flux phi transferred with U"
    assert (case_dir / "0" / "phi").read_text(encoding="utf-8") == "phi-300"
    assert (archive / "paraview_case" / "100" / "U").is_file()
    assert (archive / "paraview_case" / "300" / "U").is_file()
    assert (archive / "paraview_case" / "steady_initialization.foam").is_file()
    assert (archive / "paraview_case" / "postProcessing" / "ParaView" / "open_case.py").is_file()
    assert report["paraview_case"]["status"] == "PREPARED_FOR_PARAVIEW"
    assert report["paraview_case"]["render_verified"] is False
    assert not (archive / "paraview_case" / "postProcessing" / "ParaView" / "case_latest.ready.json").exists()
    reset = reset_transient_time_origin(case_dir)
    assert reset["startTime_s"] == 0.0
    control = (case_dir / "system" / "controlDict").read_text(encoding="utf-8")
    assert "startFrom       startTime;" in control
    assert "startTime       0;" in control


def test_steady_archive_reconstructs_optional_phi_when_simple_did_not_write_it(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    archive = case_dir / "steadyInitialization" / "history" / "run_without_phi"
    (case_dir / "constant" / "polyMesh").mkdir(parents=True)
    (case_dir / "constant" / "polyMesh" / "boundary").write_text("mesh", encoding="utf-8")
    steady = case_dir / "system" / "steadyInitialization"
    steady.mkdir(parents=True)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        (steady / name).write_text("application foamRun;\n", encoding="utf-8")
    (case_dir / "system" / "controlDict").write_text(
        "startFrom latestTime;\nstartTime 0;\nstopAt endTime;\nendTime 2;\n",
        encoding="utf-8",
    )
    (case_dir / "0").mkdir()
    latest = case_dir / "3400"
    latest.mkdir()
    for field in ("U", "p", "nuTilda"):
        (latest / field).write_text(f"{field}-steady", encoding="utf-8")
    archive.mkdir(parents=True)
    report = archive_steady_outputs(
        case_dir,
        archive,
        transfer_to_transient_zero=True,
        paraview_snapshot_count=1,
    )
    assert report["required_transferred_fields"] == ["U", "p", "nuTilda"]
    assert report["optional_transferred_fields"] == []
    assert "reconstructs it from transferred U" in report["flux_consistency"]
    assert not (case_dir / "0" / "phi").exists()
    assert (case_dir / "0" / "U").read_text(encoding="utf-8") == "U-steady"


def test_steady_transfer_discovers_k_omega_fields_and_verifies_digests(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    archive = case_dir / "steadyInitialization" / "history" / "run_sst"
    (case_dir / "constant" / "polyMesh").mkdir(parents=True)
    (case_dir / "constant" / "polyMesh" / "boundary").write_text("mesh", encoding="utf-8")
    steady = case_dir / "system" / "steadyInitialization"
    steady.mkdir(parents=True)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        (steady / name).write_text("application foamRun;\n", encoding="utf-8")
    (case_dir / "system" / "controlDict").write_text(
        "startFrom latestTime;\nstartTime 0;\nstopAt endTime;\nendTime 2;\n",
        encoding="utf-8",
    )
    initial = archive / "initial_zero"
    initial.mkdir(parents=True)
    (case_dir / "0").mkdir()
    latest = case_dir / "500"
    latest.mkdir()
    for field in ("U", "p", "k", "omega", "nut", "phi"):
        value = f"{field}-steady"
        (initial / field).write_text(f"{field}-initial", encoding="utf-8")
        (latest / field).write_text(value, encoding="utf-8")
    report = archive_steady_outputs(
        case_dir,
        archive,
        transfer_to_transient_zero=True,
        paraview_snapshot_count=1,
    )
    assert report["required_transferred_fields"] == ["U", "p", "k", "omega"]
    assert report["optional_transferred_fields"] == ["phi", "nut"]
    assert all(item["digest_matches"] for item in report["field_continuity"].values())
    assert (case_dir / "0" / "k").read_text(encoding="utf-8") == "k-steady"
    assert Path(report["source"]).is_dir()


def test_transition_audit_distinguishes_exact_time_zero_from_first_step_jump(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    archive = case_dir / "steadyInitialization" / "history" / "run"
    steady_force = archive / "postProcessing" / "forceCoeffs" / "0"
    transient_force = case_dir / "postProcessing" / "forceCoeffs" / "0"
    steady_force.mkdir(parents=True)
    transient_force.mkdir(parents=True)
    header = "# Time Cd Cm Cl\n"
    (steady_force / "forceCoeffs.dat").write_text(
        header + "100 0.04 0.01 0.20\n",
        encoding="utf-8",
    )
    (transient_force / "forceCoeffs.dat").write_text(
        header + "0 0.04 0.01 0.20\n1e-6 0.5 0.3 1.5\n",
        encoding="utf-8",
    )
    report = audit_steady_to_transient_continuity(
        case_dir,
        archive,
        {
            "field_continuity": {
                "U": {"digest_matches": True},
                "p": {"digest_matches": True},
            }
        },
    )
    assert report["status"] == "VERIFIED"
    assert report["coefficient_time_zero_matches_steady_final"] is True
    assert report["first_solved_step_change"]["Cl"] == pytest.approx(1.3)


def test_efficiency_product_uses_same_history_and_excludes_zero_drag(tmp_path: Path) -> None:
    history = pd.DataFrame({
        "Time": [1.0, 2.0, 3.0],
        "Cl": [0.5, 0.6, 0.7],
        "Cd": [0.05, 0.0, 0.07],
    })
    report = write_aerodynamic_efficiency_products(
        history,
        tmp_path / "efficiency.csv",
        tmp_path / "efficiency.png",
    )
    assert report["status"] == "OK"
    assert report["excluded_cd_near_zero"] == 1
    assert (tmp_path / "efficiency.png").is_file()


def test_efficiency_mean_uses_only_requested_final_window(tmp_path: Path) -> None:
    history = pd.DataFrame({
        "Time": list(range(10)),
        "Cl": [0.5] * 6 + [1.0] * 4,
        "Cd": [0.05] * 6 + [0.05] * 4,
    })
    report = write_aerodynamic_efficiency_products(
        history,
        tmp_path / "efficiency.csv",
        tmp_path / "efficiency.png",
        mean_from_fraction=0.6,
    )
    assert report["mean_window_samples"] == 4
    assert report["mean_Cl_over_Cd"] == pytest.approx(20.0)


def test_boundary_layer_ratio_uses_local_tangent_and_outer_edge_speed() -> None:
    frame = pd.DataFrame({
        "distance_m": [0.0, 0.1, 0.2, 0.3],
        "speed_m_s": [2.0, 5.0, 9.0, 10.0],
        "tangential_speed_m_s": [0.0, 4.0, 8.0, 8.0],
    })
    ratio, edge, basis = boundary_layer_velocity_ratio(frame, 10.0)
    assert edge == pytest.approx(8.0)
    assert ratio[-1] == pytest.approx(1.0)
    assert basis == "local_wall_tangential_velocity"


def test_paraview_script_records_zoom_and_temporal_readiness(tmp_path: Path) -> None:
    marker = tmp_path / "case.foam"
    marker.touch()
    script = write_paraview_case_script(
        tmp_path / "open_case.py",
        marker,
        focus_chord_m=1.0,
    )
    text = script.read_text(encoding="utf-8")
    assert "CameraParallelScale = 0.45 * focus_chord_m" in text
    assert '"temporal_animation_ready"' in text


def test_pyfoam_partial_stop_reconstructs_all_retained_times() -> None:
    source = (APP / "pyfoam_solver_runner.py").read_text(encoding="utf-8")
    assert 'reconstruct = ["reconstructPar"]' in source
    assert '"scope": "all_retained_times"' in source
    assert 'reconstruct = ["reconstructPar", "-latestTime"]' not in source


def test_sweep_case_resolver_follows_atomic_active_case(tmp_path: Path) -> None:
    active = tmp_path / "CFD_2D/openfoam_cases/reference_uncut/alpha_p8p000"
    active.mkdir(parents=True)
    status = active.parent / "alpha_sweep_status.json"
    status.write_text(json.dumps({"active_case": str(active)}), encoding="utf-8")
    command = [
        sys.executable,
        "ramair_2d_openfoam_sweep.py",
        "--case-root", str(tmp_path),
        "--variant", "reference_uncut",
        "--alphas", "4", "8",
    ]
    assert openfoam_case_from_command(command) == active.resolve()


def test_sweep_stop_request_is_written_at_variant_level(tmp_path: Path) -> None:
    sweep_root = tmp_path / "CFD_2D/openfoam_cases/reference_uncut"
    sweep_root.mkdir(parents=True)
    command = [
        sys.executable,
        "ramair_2d_openfoam_sweep.py",
        "--case-root", str(tmp_path),
        "--variant", "reference_uncut",
        "--alphas", "4", "8",
    ]
    marker = request_openfoam_sweep_stop(command)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert marker.parent == sweep_root
    assert payload["variant"] == "reference_uncut"


def test_stationary_only_staged_result_is_not_validation_eligible(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    result_dir = tmp_path / "result"
    case_dir.mkdir()
    result_dir.mkdir()
    (case_dir / "case_config.json").write_text(
        json.dumps({"alpha_deg": 8.0, "reynolds": 1.9e6, "mach_input": 0.15}),
        encoding="utf-8",
    )
    (case_dir / "staged_run_status.json").write_text(
        json.dumps({"status": "STEADY_AWAITING_USER_DECISION"}),
        encoding="utf-8",
    )
    (result_dir / "case_summary.json").write_text(
        json.dumps({"status": "PROCESSED", "run_status": {"status": "RUN_COMPLETED"}}),
        encoding="utf-8",
    )
    pd.DataFrame([{"Cl": 0.8, "Cd": 0.03, "Cm": 0.01}]).to_csv(
        result_dir / "forceCoeffs_mean.csv", index=False
    )
    record, error = _result_record(result_dir / "forceCoeffs_mean.csv", case_dir / "case_config.json")
    assert error is None
    assert record is not None
    assert record["validation_eligible"] is False
    assert record["staged_status"] == "STEADY_AWAITING_USER_DECISION"


def test_steady_force_plateau_reports_percentage_metrics_for_all_coefficients(tmp_path: Path) -> None:
    force_file = tmp_path / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    force_file.parent.mkdir(parents=True)
    rows = ["# Time Cm Cd Cl"]
    for index in range(80):
        perturbation = 1.0e-5 if index % 2 else -1.0e-5
        rows.append(f"{index} {0.01 + perturbation} {0.03 + perturbation} {0.6 + perturbation}")
    force_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
    report = steady_force_plateau(tmp_path, 30, 1.0, 2.0)
    assert report["status"] == "STABLE"
    assert set(report["metrics"]) == {"Cl", "Cd", "Cm", "Cl_over_Cd"}
    assert all(values["mean_change_percent"] <= 1.0 for values in report["metrics"].values())
    assert all("current_fluctuation_percent" in values for values in report["metrics"].values())


def test_explicit_delete_does_not_create_previous_versions_backup(tmp_path: Path) -> None:
    case_dir = tmp_path / "CFD_2D" / "openfoam_cases" / "reference_uncut" / "alpha_p4p000"
    case_dir.mkdir(parents=True)
    (case_dir / "old.txt").write_text("old", encoding="utf-8")
    prepare_existing_case_dir(tmp_path, case_dir, "reference_uncut", "alpha_p4p000", "delete")
    assert not case_dir.exists()
    assert not (tmp_path / "Previous Versions").exists()


def test_explicit_simulation_delete_preserves_case_inputs_and_results_library(tmp_path: Path) -> None:
    case_dir = tmp_path / "CFD_2D" / "openfoam_cases" / "reference_uncut" / "alpha_p4p000"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "system" / "controlDict").write_text("application foamRun;\n", encoding="utf-8")
    (case_dir / "0").mkdir()
    (case_dir / "1.0").mkdir()
    (case_dir / "postProcessing").mkdir()
    (case_dir / "log.foamRun").write_text("solver", encoding="utf-8")
    saved = tmp_path / "Results" / "saved_case"
    saved.mkdir(parents=True)
    (saved / "keep.txt").write_text("saved", encoding="utf-8")
    removed = prepare_existing_simulation(case_dir, "delete")
    assert removed
    assert (case_dir / "0").is_dir()
    assert (case_dir / "system" / "controlDict").is_file()
    assert not (case_dir / "1.0").exists()
    assert not (case_dir / "postProcessing").exists()
    assert (saved / "keep.txt").is_file()
    assert not (tmp_path / "Previous Versions").exists()


def test_staged_runner_dry_run_builds_plan_without_openfoam(tmp_path: Path) -> None:
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text("application foamRun;\n", encoding="utf-8")
    script = SCRIPTS / "ramair_2d_openfoam_staged_runner.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--case", str(tmp_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout
    plan = json.loads((tmp_path / "staged_run_plan.json").read_text(encoding="utf-8"))
    assert plan["run"] is False
    assert "--run" not in plan["transient_command"]


def test_sweep_runner_dry_run_builds_one_safe_angle_command(tmp_path: Path) -> None:
    case_dir = tmp_path / "CFD_2D" / "openfoam_cases" / "reference_uncut" / "alpha_p4p000"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "system" / "controlDict").write_text("application foamRun;\n", encoding="utf-8")
    script = SCRIPTS / "ramair_2d_openfoam_sweep.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--case-root", str(tmp_path),
            "--variant", "reference_uncut",
            "--alphas", "4",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout
    report = json.loads(
        (tmp_path / "CFD_2D" / "openfoam_cases" / "reference_uncut" / "alpha_sweep_status.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "DRY_RUN"
    assert report["rows"][0]["status"] == "DRY_RUN"
    assert "--run" not in report["rows"][0]["command"]
