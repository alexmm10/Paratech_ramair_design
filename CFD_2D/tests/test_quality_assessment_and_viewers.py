from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from paraview_case_viewer import (  # noqa: E402
    prepare_paraview_case,
    write_automatic_products_script,
    write_paraview_case_script,
)
from ramair_2d_mesh_quality_controller import engineering_quality_assessment  # noqa: E402


def test_engineering_assessment_warns_about_small_checkmesh_margins_without_blocking() -> None:
    assessment = engineering_quality_assessment({
        "checkMesh_status": "OK",
        "checkMesh_max_non_orthogonality_deg": 69.97,
        "checkMesh_average_non_orthogonality_deg": 4.8,
        "checkMesh_max_skewness": 3.79,
        "checkMesh_min_cell_determinant": 0.0022,
        "checkMesh_min_face_interpolation_weight": 0.081,
        "checkMesh_min_face_volume_ratio": 0.089,
    })
    assert assessment["grade"] == "C"
    assert assessment["solver_risk"] == "ELEVATED"
    assert assessment["blocking"] is False
    assert assessment["workflow_gate_unchanged"] is True


def test_scripted_paraview_reader_loads_internal_mesh_latest_time_and_writes_evidence(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    marker = case_dir / "case.foam"
    marker.touch()
    script = write_paraview_case_script(case_dir / "open_case.py", marker)
    text = script.read_text(encoding="utf-8")
    assert "OpenFOAMReader(FileName=foam_path)" in text
    assert 'source.MeshRegions = ["internalMesh"]' in text
    assert "source.TimestepValues" in text
    assert "scene.AnimationTime = (physical_times or available_times)[-1]" in text
    assert "source.UpdatePipeline(time=scene.AnimationTime)" in text
    assert "SaveScreenshot" in text
    assert "SaveState(" not in text
    assert '"status": "READY"' in text
    assert "is_iteration_stage" not in text
    assert "velocity_m_s" not in text
    compile(text, str(script), "exec")


def test_prepared_paraview_package_does_not_claim_a_verified_render(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "system" / "controlDict").write_text("application foamRun;\n", encoding="utf-8")
    prepared = prepare_paraview_case(case_dir)
    assert prepared["status"] == "PREPARED_FOR_PARAVIEW"
    assert prepared["render_verified"] is False
    assert not Path(prepared["ready_file"]).exists()


def test_automatic_products_script_is_bounded_and_uses_direct_openfoam_reader(tmp_path: Path) -> None:
    marker = tmp_path / "case.foam"
    marker.touch()
    script = write_automatic_products_script(
        tmp_path / "render.py",
        marker,
        tmp_path / "products",
        chord_m=1.0,
        velocity_m_s=50.0,
        alpha_deg=4.0,
        maximum_courant=1.0,
        maximum_frames=6,
        time_semantics="physical seconds",
        stage_label="URANS",
    )
    text = script.read_text(encoding="utf-8")
    assert "OpenFOAMReader(FileName=foam_path)" in text
    assert "maximum_frames = 6" in text
    assert "Cp_airfoil_%s_final.png" in text
    assert "Velocity_%s_final.png" in text
    assert "Courant_%s_final.png" in text
    assert "Courant_hotspots_%s_final.png" in text
    assert 'Threshold(registrationName="CourantHotspots"' in text
    assert 'threshold_value = 0.70 * maximum_courant' in text
    assert 'available_array("Co")' in text
    assert 'set_camera("airfoil")' in text
    assert 'StreamTracer(' in text
    assert 'SeedType="Line"' in text
    assert 'CleanToGrid(' in text
    assert 'registrationName="AerodynamicMidPlane"' in text
    assert 'streamlines.SurfaceStreamlines = 0' in text
    assert "nearfield_streamlines" not in text
    assert 'pressure_display.SetScalarBarVisibility(view, True)' in text
    assert 'vorticity_contour_display.SetScalarBarVisibility(view, True)' in text
    assert 'pressure_surface_display' not in text
    assert "streamlines.SeedType.Resolution = 100" in text
    assert 'registrationName="RotatedFreestreamStreamlines"' in text
    assert 'streamline_display.LineWidth = 0.6' in text
    assert "set_streamline_visibility" in text
    assert "streamline_display.Visibility" in text
    assert '"Velocity_contours_%s_final.png"' in text
    assert '"Pressure_contours_%s_final.png"' in text
    assert '"Vorticity_contours_%s_final.png"' in text
    assert 'set_camera("nearfield")' in text
    assert "seed_center_x = -1.0 * chord_m" in text
    assert 'visual_source.Transform.Rotate = [0.0, 0.0, -alpha_deg]' in text
    assert 'products["courant_hotspots_png"] = None' in text
    assert '"line_perpendicular_to_freestream"' in text
    assert 'stage_label = "URANS"' in text
    assert 'frame_axis_label = "iteration" if is_iteration_stage else "t [s]"' in text
    assert '"physical_time_seconds"' in text
    assert "%s final | %s" in text
    assert "velocity_%04d.png" in text
    assert "pressure_Cp_%04d.png" in text
    assert "selected_times if include_animations else []" in text
    assert "Velocity_streamlines_contours_%s_final.png" not in text
    assert "foamToVTK" not in text
    compile(text, str(script), "exec")
