from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "CFD_2D" / "scripts"
PREPROCESSOR_MAIN = PROJECT_ROOT / "preprocess_ramair_main.py"
CATIA_SCRIPT_MAIN = PROJECT_ROOT / "Generate_RamAir_Canopy_MAIN.CATScript"
TECH_SPEC = PROJECT_ROOT / "Documents and Manuals" / "CFD 2D" / "CFD_2D_TECHNICAL_SPECIFICATIONS.txt"


def run_cmd(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        args,
        cwd=str(cwd or PROJECT_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def test_mesh_builder_exposes_openfoam_temp_workdir_option() -> None:
    result = run_cmd([sys.executable, str(SCRIPTS / "ramair_2d_mesh_builder.py"), "--help"])
    assert "--openfoam-temp-workdir" in result.stdout
    assert "--no-openfoam-temp-workdir" in result.stdout
    assert "--check-existing-mesh" in result.stdout
    assert "--gmsh-threads" in result.stdout
    assert "--gmsh-executable" in result.stdout
    assert "--allow-legacy-gmsh-boundary-layer" in result.stdout
    assert "--benchmark-gmsh-threads" in result.stdout
    assert "--previous-output-action" in result.stdout


def test_mesh_builder_turns_keyboard_interrupt_into_clean_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import ramair_2d_mesh_builder as builder

    class InterruptedProcess:
        pid = 4242
        returncode = None

        def __init__(self, *args, **kwargs):
            self.stopped = False
            self.communicate_calls = 0

        def poll(self):
            return 0 if self.stopped else None

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise KeyboardInterrupt
            return ("partial gmsh output\n", None)

        def terminate(self):
            self.stopped = True

        def kill(self):
            self.stopped = True

        def wait(self, timeout=None):
            self.stopped = True
            self.returncode = -15
            return self.returncode

    process = InterruptedProcess()
    monkeypatch.setattr(builder.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(builder.os, "killpg", lambda *args, **kwargs: process.terminate(), raising=False)
    code, output = builder.run_command(["gmsh", "mesh.geo"], tmp_path, timeout_s=60)
    assert code == 130
    assert process.stopped is True
    assert "CANCELLED" in output
    assert "partial gmsh output" in output


def test_mesh_config_override_accepts_utf8_bom_and_rejects_invalid_json(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_mesh_builder import load_mesh_config

    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps({
            "open_inlet_boundary_layer_mode": "full_prismatic_bridge_without_fans",
            "fabric_thickness_chord": 4.0e-4,
            "open_inlet_marker_transfinite_nodes": 64,
        }),
        encoding="utf-8-sig",
    )
    config = load_mesh_config(tmp_path, "debug", candidate)
    assert config["open_inlet_boundary_layer_mode"] == "full_prismatic_bridge_without_fans"
    assert config["open_boundary_layer_fan_at_lips"] is False
    assert config["fabric_thickness_chord"] == pytest.approx(4.0e-4)
    assert config["open_inlet_marker_transfinite_nodes"] == 64
    assert config["_mesh_config_source"] == str(candidate.resolve())
    assert config["_mesh_config_override_requested"] is True

    candidate.write_text("{ invalid", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Invalid JSON configuration .*candidate.json: line 1"):
        load_mesh_config(tmp_path, "debug", candidate)


def test_checkmesh_parser_accepts_openfoam_trailing_periods() -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_mesh_builder import parse_checkmesh_metrics

    metrics = parse_checkmesh_metrics(
        """
        Mesh non-orthogonality Max: 75.9566 average: 29.3851
       *Number of severely non-orthogonal (> 70 degrees) faces: 1.
     ***Max skewness = 6.61509, 9 highly skew faces detected
        Max aspect ratio = 35.6208 OK.
        Min volume = 1.23803e-05. Max volume = 0.753315.  Total volume = 6.95191.
        Cell determinant (wellposedness) : minimum: 8.8527e-05 average: 0.576332
     ***Cells with small determinant (< 0.001) found, number of cells: 22
        Face interpolation weight : minimum: 0.0038046 average: 0.318759
     ***Faces with small interpolation weight (< 0.05) found, number of faces: 9
        Face volume ratio : minimum: 0.00381913 average: 0.513237
     ***Faces with small volume ratio (< 0.01) found, number of faces: 3
        Failed 4 mesh checks.
        """
    )
    assert metrics["checkMesh_max_volume"] == 0.753315
    assert metrics["checkMesh_failed_checks_count"] == 4
    assert metrics["checkMesh_highly_skew_faces"] == 9
    assert metrics["checkMesh_small_determinant_cells"] == 22
    assert metrics["checkMesh_failed_checks"] == [
        "highly_skew_faces",
        "small_cell_determinant",
        "small_face_interpolation_weight",
        "small_face_volume_ratio",
    ]


def test_checkmesh_problem_locations_identify_open_inlet_lips(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_mesh_builder import summarize_checkmesh_problem_locations

    vtk_dir = tmp_path / "vtk"
    vtk_dir.mkdir()
    (vtk_dir / "skewFaces.vtk").write_text(
        "# vtk DataFile Version 2.0\nsampleSurface\nASCII\nDATASET POLYDATA\n"
        "POINTS 4 float\n0.001 0.05 0  -0.001 0.051 0  -0.001 0.051 0.03  0.001 0.05 0.03\n"
        "POLYGONS 1 5\n4 0 1 2 3\n",
        encoding="utf-8",
    )
    (vtk_dir / "underdeterminedCells.vtk").write_text(
        "# vtk DataFile Version 2.0\nsampleSurface\nASCII\nDATASET UNSTRUCTURED_GRID\n"
        "POINTS 4 float\n0.2 0.02 0  0.3 0.02 0  0.3 0.02 0.03  0.2 0.02 0.03\n"
        "CELLS 1 5\n4 0 1 2 3\nCELL_TYPES 1\n9\n",
        encoding="utf-8",
    )
    sets_dir = tmp_path / "sets"
    sets_dir.mkdir()
    (sets_dir / "skewFaces").write_text(
        "FoamFile\n{\n    class faceSet;\n}\n1\n(\n42\n)\n",
        encoding="utf-8",
    )
    (sets_dir / "underdeterminedCells").write_text(
        "FoamFile\n{\n    class cellSet;\n}\n2\n(\n7\n9\n)\n",
        encoding="utf-8",
    )
    profile = pd.DataFrame({"x_m": [0.0, 3.0], "z_m": [-0.1, 0.1]})
    check_log = (
        "Max skewness = 5.2, 1 highly skew faces detected\n"
        "Cell determinant (wellposedness) : minimum: 0.00012 average: 0.5\n"
    )
    result = summarize_checkmesh_problem_locations(vtk_dir, profile, check_log, sets_dir=sets_dir)
    assert result["high_skew_faces"]["entity_count"] == 1
    assert result["high_skew_faces"]["reported_maximum"] == pytest.approx(5.2)
    assert result["high_skew_faces"]["centroid_x_over_chord"] == pytest.approx(0.0, abs=1.0e-3)
    assert "inlet_lip_or_LE_transition" in result["high_skew_faces"]["likely_region"]
    assert result["high_skew_faces"]["openfoam_label_sample"] == [42]
    assert result["high_skew_faces"]["vtk_entity_centroid_count"] == 1
    assert result["small_determinant_cells"]["entity_count"] == 1
    assert result["small_determinant_cells"]["reported_minimum"] == pytest.approx(0.00012)
    assert result["small_determinant_cells"]["checkMesh_threshold"] == pytest.approx(0.001)
    assert result["small_determinant_cells"]["openfoam_label_sample"] == [7, 9]


def test_delete_previous_mesh_does_not_create_backup(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_mesh_builder import backup_existing_mesh_root

    mesh_root = tmp_path / "CFD_2D" / "meshes" / "open_ramair"
    mesh_root.mkdir(parents=True)
    (mesh_root / "mesh_final.msh").write_text("heavy mesh", encoding="utf-8")
    backup = backup_existing_mesh_root(tmp_path, mesh_root, "open_ramair", "delete")
    assert backup is None
    assert not mesh_root.exists()
    assert not (tmp_path / "previous_versions").exists()


def test_mesh_archive_inventory_includes_checkmesh_viewer_artifacts() -> None:
    source = (SCRIPTS / "ramair_2d_mesh_builder.py").read_text(encoding="utf-8")
    for name in (
        "checkMesh_problem_locations.json",
        "checkMesh_problem_locations.txt",
        "checkMesh_problem_sets",
        "checkMesh_problem_viewer.py",
        "checkMesh_problem_view.png",
        "checkMesh_quality.foam",
    ):
        assert f'"{name}"' in source


def test_pyfoam_partial_log_is_visible_to_runner_and_postprocess(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_openfoam_runner import active_solver_log
    from ramair_2d_postprocess import parse_solver_log

    pyfoam_log = tmp_path / "PyFoamRunner.foamRun"
    pyfoam_log.write_text(
        "Time = 0.1\n"
        "Courant Number mean: 0.2 max: 0.8\n"
        "smoothSolver:  Solving for Ux, Initial residual = 0.01, Final residual = 0.001, No Iterations 2\n",
        encoding="utf-8",
    )
    assert active_solver_log(tmp_path, "foamRun", "pyfoam") == pyfoam_log
    residuals, courant, metadata = parse_solver_log(tmp_path)
    assert metadata["solver_log_found"] is True
    assert metadata["solver_log"] == str(pyfoam_log)
    assert residuals.iloc[0]["field"] == "Ux"
    assert courant.iloc[0]["Co_max"] == pytest.approx(0.8)


def test_pyfoam_failed_preconditioner_log_has_priority(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_openfoam_runner import active_solver_log

    stale = tmp_path / "log.foamRun"
    stale.write_text("old solver", encoding="utf-8")
    failed = tmp_path / "PyFoamPotential.logfile"
    failed.write_text("current potentialFoam failure", encoding="utf-8")
    (tmp_path / "pyfoam_run_report.json").write_text(
        json.dumps({"status": "RUN_FAILED", "failed_stage": "potentialFoam", "failed_log": str(failed)}),
        encoding="utf-8",
    )
    assert active_solver_log(tmp_path, "foamRun", "pyfoam") == failed


def test_pyfoam_solver_log_has_priority_over_reconstruct_log(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_openfoam_runner import active_solver_log

    solver_log = tmp_path / "PyFoamRunner.foamRun.logfile"
    reconstruct_log = tmp_path / "PyFoamReconstruct.logfile"
    solver_log.write_text("Time = 0.1s\n", encoding="utf-8")
    reconstruct_log.write_text("End\n", encoding="utf-8")
    (tmp_path / "pyfoam_run_report.json").write_text(
        json.dumps({
            "active_log": str(reconstruct_log),
            "stages": [
                {"stage": "steady_or_transient_solver", "log": str(solver_log)},
                {"stage": "reconstructPar", "log": str(reconstruct_log)},
            ],
        }),
        encoding="utf-8",
    )

    assert active_solver_log(tmp_path, "foamRun", "pyfoam") == solver_log


def test_runner_resolves_relative_case_script_before_changing_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import ramair_2d_openfoam_runner as runner

    case_dir = tmp_path / "CFD_2D" / "openfoam_cases" / "reference_uncut" / "alpha_p4p000"
    case_dir.mkdir(parents=True)
    script = case_dir / "run_case.sh"
    script.write_text("#!/usr/bin/env bash\necho RELATIVE_CASE_OK\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    relative_case = case_dir.relative_to(tmp_path)
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0
        pid = 123

        def communicate(self, timeout: int | None = None) -> tuple[str, None]:
            return "RELATIVE_CASE_OK\n", None

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    code, output, outcome = runner.run_script_with_timeout(
        relative_case / "run_case.sh",
        relative_case,
        10,
    )
    assert code == 0
    assert outcome == "completed"
    assert "RELATIVE_CASE_OK" in output
    assert Path(str(captured["command"][1])).is_absolute()


def test_processor_cleanup_requires_reconstructed_latest_time(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_openfoam_runner import cleanup_reconstructed_processor_directories

    case_dir = tmp_path / "case"
    (case_dir / "1.0").mkdir(parents=True)
    (case_dir / "processor0" / "2.0").mkdir(parents=True)
    skipped = cleanup_reconstructed_processor_directories(case_dir)
    assert skipped["status"] == "SKIPPED"
    assert skipped["reason"] == "reconstructed_root_time_is_older"
    assert (case_dir / "processor0").is_dir()

    (case_dir / "2.0").mkdir()
    cleaned = cleanup_reconstructed_processor_directories(case_dir)
    assert cleaned["status"] == "OK"
    assert cleaned["removed"] == ["processor0"]
    assert not (case_dir / "processor0").exists()


def test_postprocess_command_variant_result_has_no_json_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import ramair_2d_postprocess as postprocess

    monkeypatch.setattr(
        postprocess,
        "run_optional_command",
        lambda cmd, cwd, log_path, timeout_s: {
            "command": " ".join(cmd),
            "status": "OK",
            "log": str(log_path),
        },
    )
    result = postprocess.run_optional_command_variants(
        [["foamPostProcess", "-func", "yPlus"]],
        tmp_path,
        tmp_path / "log.yPlus",
        10,
    )
    encoded = json.dumps(postprocess.json_safe(result))
    assert '"status": "OK"' in encoded
    assert len(result["attempts"]) == 1


def test_paraview_launcher_delegates_absolute_case_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import ramair_2d_postprocess as postprocess

    case_dir = tmp_path / "CFD_2D" / "openfoam_cases" / "reference_uncut" / "alpha_p4p000"
    case_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_launch(path: Path) -> dict[str, object]:
        captured["case"] = path
        marker = path / f"{path.name}.foam"
        marker.touch()
        return {"status": "OPEN_REQUESTED", "foam_marker": str(marker), "pid": 321}

    monkeypatch.setattr(postprocess, "launch_paraview_case", fake_launch)
    result = postprocess.launch_paraview(case_dir.relative_to(tmp_path))
    marker = Path(str(result["foam_marker"]))
    assert marker.is_absolute()
    assert marker.is_file()
    assert Path(str(captured["case"])).is_absolute()


def test_case_writer_accepts_project_and_openfoam_cases_roots(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_openfoam_case_writer import project_root_from_case_root

    project = tmp_path / "project"
    assert project_root_from_case_root(project) == project
    assert project_root_from_case_root(project / "CFD_2D") == project
    assert project_root_from_case_root(project / "CFD_2D" / "openfoam_cases") == project
    assert project_root_from_case_root(project / "CFD_2D" / "meshes") == project


def test_minimal_openfoam_conversion_case_preserves_mesh_precision(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    from ramair_2d_mesh_builder import _write_minimal_foam_case

    _write_minimal_foam_case(tmp_path)
    control = (tmp_path / "system" / "controlDict").read_text(encoding="utf-8")
    assert "writePrecision  16;" in control
    assert "writeCompression off;" in control


def write_open_profile(path: Path) -> None:
    rows = []
    upper = [(0.0, 0.030), (0.2, 0.090), (0.6, 0.070), (1.0, 0.002)]
    lower = [(0.0, -0.025), (0.3, -0.055), (0.7, -0.032), (1.0, -0.006)]
    for i, (x, z) in enumerate(upper, 1):
        rows.append({"section": "UPPER", "x_norm": x, "z_norm": z, "order": i})
    for i, (x, z) in enumerate(lower, 1):
        rows.append({"section": "LOWER", "x_norm": x, "z_norm": z, "order": i})
    pd.DataFrame(rows).to_csv(path, index=False)


def write_closed_dat(path: Path) -> None:
    pts = [(1.0, 0.0), (0.5, 0.09), (0.0, 0.02), (0.5, -0.05), (1.0, 0.0)]
    path.write_text("closed\n" + "\n".join(f"{x} {z}" for x, z in pts) + "\n", encoding="utf-8")


@pytest.fixture()
def generated_project(tmp_path: Path) -> Path:
    project = tmp_path / "case_project"
    profiles = project / "Airfoil Profiles"
    configs = project / "Application Support" / "Configurations"
    profiles.mkdir(parents=True)
    configs.mkdir(parents=True)
    open_profile = profiles / "open.csv"
    closed_profile = profiles / "closed.dat"
    write_open_profile(open_profile)
    write_closed_dat(closed_profile)
    config = {
        "project_paths": {
            "profiles_dir": "Airfoil Profiles",
            "catia_inputs_dir": "CATIA/Inputs",
            "catia_exports_dir": "CATIA/Exports",
            "cfd_2d_inputs_dir": "CFD_2D/CFD_2D_inputs",
        },
        "profile_inputs": {
            "main_profile": str(open_profile),
            "reference_uncut_profile": str(closed_profile),
            "profile_input_order": "section_column",
        },
        "canopy_geometry": {"chord_mm": 1000, "span_mm": 2700, "cells": 9, "anhedral_deg": 10},
    }
    cfg_path = configs / "default_case_config.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")
    run_cmd([sys.executable, str(PREPROCESSOR_MAIN), "--config", str(cfg_path)], cwd=project)
    return project


def test_root_entrypoints_and_archives_exist() -> None:
    assert PREPROCESSOR_MAIN.exists()
    assert CATIA_SCRIPT_MAIN.exists()
    assert TECH_SPEC.exists()
    assert (PROJECT_ROOT / "Previous Versions").exists()


def test_preprocessor_generates_new_layout(generated_project: Path) -> None:
    assert (generated_project / "CATIA" / "Inputs" / "ramair_global_inputs.csv").exists()
    assert (generated_project / "CATIA" / "Inputs" / "Canopy" / "ramair_profile_points_for_CATIA.csv").exists()
    assert not (generated_project / "CATIA" / "Inputs" / "CFD_2D").exists()
    assert (generated_project / "CFD_2D" / "CFD_2D_inputs" / "geometry" / "open_ramair" / "profile_manifest.json").exists()
    assert (generated_project / "CFD_2D" / "reference_data" / "Ross" / "ross_reference_manifest.json").exists()


def test_preprocessor_preserves_existing_editable_mesh_settings(generated_project: Path) -> None:
    mesh_config = generated_project / "CFD_2D" / "CFD_2D_inputs" / "config" / "cfd2d_mesh_config.json"
    values = json.loads(mesh_config.read_text(encoding="utf-8"))
    values["open_farfield_size_chord"] = 0.271
    values["open_inlet_boundary_layer_mode"] = "triangular_inlet_no_bl"
    values["open_inlet_transition_elements"] = "triangles"
    values["user_test_marker"] = "preserve-me"
    mesh_config.write_text(json.dumps(values), encoding="utf-8")

    case_config = generated_project / "Application Support" / "Configurations" / "default_case_config.json"
    run_cmd([sys.executable, str(PREPROCESSOR_MAIN), "--config", str(case_config)], cwd=generated_project)

    regenerated = json.loads(mesh_config.read_text(encoding="utf-8"))
    assert regenerated["config_schema_version"] == 3
    assert regenerated["open_farfield_size_chord"] == pytest.approx(0.271)
    assert regenerated["open_inlet_boundary_layer_mode"] == "triangular_inlet_no_bl"
    assert regenerated["open_inlet_transition_elements"] == "triangles"
    assert regenerated["user_test_marker"] == "preserve-me"


def test_mesh_builder_preserves_existing_editable_mesh_settings(generated_project: Path) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    mesh_config = generated_project / "CFD_2D" / "CFD_2D_inputs" / "config" / "cfd2d_mesh_config.json"
    values = json.loads(mesh_config.read_text(encoding="utf-8"))
    values["open_farfield_size_chord"] = 0.271
    values["closed_wall_target_nodes"] = 333
    values["user_test_marker"] = "builder-preserve-me"
    mesh_config.write_text(json.dumps(values, indent=2), encoding="utf-8")

    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--domain",
        "circular_50c",
        "--mesh-level",
        "debug",
        "--dry-run",
        "--overwrite",
    ])

    after = json.loads(mesh_config.read_text(encoding="utf-8"))
    assert after["open_farfield_size_chord"] == pytest.approx(0.271)
    assert after["closed_wall_target_nodes"] == 333
    assert after["user_test_marker"] == "builder-preserve-me"
    report = json.loads((generated_project / "CFD_2D/meshes/closed_reference/mesh_quality_report.json").read_text(encoding="utf-8"))
    assert report["closed_wall_target_nodes"] == 333


def test_profiles_and_upper_lower_split(generated_project: Path) -> None:
    info = (generated_project / "CATIA" / "Inputs" / "Canopy" / "Profile_used" / "ramair_profile_used_info.txt").read_text(encoding="utf-8")
    assert "profile_input_order_requested: section_column" in info
    assert "profile_input_order_detected: section_column" in info


def test_diagnostic_pngs_are_written(generated_project: Path) -> None:
    pytest.importorskip("matplotlib")
    assert (generated_project / "CFD_2D" / "CFD_2D_inputs" / "previews" / "profile_open_ramair_preview.png").exists()
    assert (generated_project / "CFD_2D" / "CFD_2D_inputs" / "previews" / "profile_comparison_open_vs_closed.png").exists()


def test_catscript_uses_catia_inputs_without_hardcoded_user_path() -> None:
    text = CATIA_SCRIPT_MAIN.read_text(encoding="utf-8", errors="ignore")
    assert 'Const BASE_FOLDER = ""' in text
    assert "RAMAIR_CATIA_INPUTS" in text
    assert "ramair_CATIA_input_dist" not in text
    assert "C:\\Users" not in text


def test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project: Path) -> None:
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_profile_case_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "all",
        "--overwrite",
        "--validate",
    ])
    package = generated_project / "CFD_2D" / "CFD_2D_inputs" / "case_package"
    assert (package / "variant_index.csv").exists()
    patches = json.loads((package / "open_ramair" / "patches.json").read_text(encoding="utf-8"))
    assert "inlet_opening_marker" in patches
    assert "ram_air_inlet" not in patches


def test_case_builder_overwrite_archives_previous_package(generated_project: Path) -> None:
    package = generated_project / "CFD_2D" / "CFD_2D_inputs" / "case_package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "stale_marker.txt").write_text("old package", encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_profile_case_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "reference_uncut",
        "--overwrite",
        "--validate",
    ])
    backups = sorted((generated_project / "Previous Versions" / "cfd2d_case_package_backups").glob("reference_uncut_*"))
    assert backups
    assert (backups[-1] / "stale_marker.txt").exists()
    assert (backups[-1] / "case_package_backup_manifest.json").exists()
    assert not (package / "stale_marker.txt").exists()


def test_mesh_builder_openfoam_geo_is_extruded_but_dry_run_only(generated_project: Path) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    mesh_config_path = generated_project / "CFD_2D" / "CFD_2D_inputs" / "config" / "cfd2d_mesh_config.json"
    mesh_config = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    mesh_config["debug_airfoil_curve_mode"] = "spline_branches"
    mesh_config["debug_boundary_layer_fan_at_te"] = True
    mesh_config_path.write_text(json.dumps(mesh_config, indent=2), encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--domain",
        "circular_50c",
        "--mesh-level",
        "debug",
        "--write-openfoam-mesh",
        "--dry-run",
        "--overwrite",
    ])
    mesh_root = generated_project / "CFD_2D" / "meshes" / "closed_reference"
    geo = mesh_root / "mesh_final.geo"
    text = geo.read_text(encoding="utf-8")
    report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    assert "Field[1] = BoundaryLayer;" in text
    assert "Mesh.Algorithm = 5;" in text
    assert "Mesh.RandomFactor = 1e-07;" in text
    assert "Spline(1001)" in text
    assert "Field[1].CurvesList = {" in text
    assert "airfoil_dense_te_cap_spline_segment_01" in text
    assert "airfoil_spline_grouped_profile_segment" in text
    assert "Using Bump 0.5" in text
    assert "Field[1].Quads = 1;" in text
    assert "Field[1].IntersectMetrics = 1;" in text
    assert "Field[1].AnisoMax = 170;" in text
    assert "Field[1].FanPointsList" not in text
    assert "Field[2] = Distance;" in text
    assert "Field[3] = Threshold;" in text
    assert "Field[4] = Threshold;" in text
    assert "Field[5] = Threshold;" in text
    assert "Field[6] = Min;" in text
    assert "Field[4] = Box;" not in text
    assert "Background Field = 6;" in text
    assert "Extrude" not in text
    assert 'Physical Surface("frontAndBack")' not in text
    assert report["python_extrude_mesh"] is True
    assert report["boundary_layer_requested"] is True
    assert report["boundary_layer_layers_requested"] == 50
    assert report["effective_boundary_layer_growth"] == pytest.approx(1.10)
    assert report["boundary_layer_total_thickness_limited"] is False
    assert report["boundary_layer_total_thickness_chord"] == pytest.approx(report["boundary_layer_raw_total_thickness_chord"])
    assert report["boundary_layer_exclude_te_cap_from_bl"] is False
    assert sorted(report["boundary_layer_curve_ids"]) == [1001, 1002]
    assert report["boundary_layer_excluded_te_curve_ids"] == []
    assert report["effective_debug_max_profile_points"] == 600
    assert report["profile_preprocessing_applied"] is True
    assert report["profile_preprocessing_consecutive_edges"] is True
    assert report["profile_preprocessing_self_intersections"] == 0
    assert report["te_rounding_enabled"] is True
    assert report["te_rounding_points_added"] == mesh_config["closed_te_rounding_points"]
    assert report["te_rounding_tagged_cap_points"] >= 3
    assert report["te_rounding_tag_method"] == "downstream_cyclic_path_between_nearest_cap_endpoints"
    assert "te_rounding_note" in report
    assert report["airfoil_curve_mode"] == "hybrid_te_spline"
    assert report["airfoil_curve_mode_requested"] == "hybrid_te_spline"
    assert report["closed_wall_curve_method"] == "two_spline_te_cap"
    assert report["closed_wall_target_nodes"] == 2000
    assert report["closed_te_bump_strength"] == pytest.approx(0.50)
    assert report["closed_te_target_nodes"] == mesh_config["closed_te_target_nodes"]
    assert report["rounded_te_curve_sections_enforced"] is False
    assert report["gmsh_curve_connectivity_valid"] is True
    assert report["gmsh_curve_connectivity_issue_count"] == 0
    assert (mesh_root / "airfoil_wall_curve_connectivity_audit.json").exists()
    assert report["airfoil_curve_count"] == 2
    assert report["hybrid_te_spline_curve_count"] == 2
    assert report["hybrid_te_line_curve_count"] == 0
    assert report["boundary_layer_fan_at_le"] is False
    assert report["boundary_layer_fan_at_te"] is False
    assert report["boundary_layer_te_fan_suppressed_for_rounded_cap"] is True
    assert report["boundary_layer_te_fan_points"] == 64
    assert len(report["airfoil_transfinite_curve_nodes"]) == 2
    assert report["closed_airfoil_transfinite_enabled"] is True
    assert report["closed_airfoil_target_nodes"] == 2000
    assert len(report["closed_te_cap_curve_ids"]) == 1
    assert report["closed_single_curve_experimental"] is False
    main_curve_id = next(
        key for key in report["airfoil_transfinite_curve_nodes"]
        if int(key) not in report["closed_te_cap_curve_ids"]
    )
    assert report["airfoil_transfinite_curve_distributions"][main_curve_id]["method"] == "Bump"
    assert report["closed_te_segment_min_length_chord"] is not None
    assert report["airfoil_transfinite_node_multiplier"] == pytest.approx(1.0)
    expected_outer_bl = (
        report["surface_size_from_boundary_layer"]["outer_bl_cell_height_chord"]
        * report["surface_size_from_boundary_layer"]["factor"]
    )
    assert report["effective_surface_size_general_chord"] >= expected_outer_bl
    assert report["surface_size_from_boundary_layer"]["interface_size_rule"].startswith("max(")
    assert report["effective_farfield_size_chord"] == 1.0
    assert report["wake_refinement_requested"] is False
    assert report["nearfield_refinement_requested"] is True
    assert report["status"] == "DRY_RUN"
    assert report["domain"] == "circular_50c"
    assert report["domain_type"] == "circle"
    assert report["effective_domain_radius_chord"] == pytest.approx(50.0)
    assert (mesh_root / "profile_preprocessed_points.csv").exists()
    pytest.importorskip("matplotlib")
    assert (mesh_root / "profile_preprocessing_distribution.png").exists()
    assert not (generated_project / "CFD_2D" / "openfoam_cases").exists()


def test_closed_preprocessing_is_applied_at_fine_mesh_level(generated_project: Path) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--domain",
        "circular_50c",
        "--mesh-level",
        "fine",
        "--dry-run",
        "--overwrite",
    ])
    mesh_root = generated_project / "CFD_2D/meshes/closed_reference"
    report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    preprocessing = json.loads((mesh_root / "profile_preprocessing_report.json").read_text(encoding="utf-8"))
    assert report["profile_preprocessing_applied"] is True
    assert report["te_rounding_enabled"] is True
    assert report["te_rounding_applied"] is True
    assert preprocessing["te_rounding_applied"] is True
    assert preprocessing["te_rounding_cap_method"] == "tangent_continuous_cubic_bezier"
    assert (mesh_root / "profile_preprocessing_te_zoom.png").is_file()


def test_closed_single_curve_experimental_geo_option(generated_project: Path) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    mesh_config_path = generated_project / "CFD_2D" / "CFD_2D_inputs" / "config" / "cfd2d_mesh_config.json"
    mesh_config = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    mesh_config.update({
        "closed_wall_curve_method": "single_spline_bump",
        "closed_wall_target_nodes": 444,
        "closed_te_rounding_enabled": True,
    })
    mesh_config_path.write_text(json.dumps(mesh_config, indent=2), encoding="utf-8")

    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--mesh-level",
        "debug",
        "--write-openfoam-mesh",
        "--dry-run",
        "--overwrite",
    ])

    mesh_root = generated_project / "CFD_2D" / "meshes" / "closed_reference"
    geo_text = (mesh_root / "mesh_final.geo").read_text(encoding="utf-8")
    report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    assert "Spline(1001)" in geo_text
    assert "BSpline(1001)" not in geo_text
    assert "airfoil_dense_te_cap_spline_segment" not in geo_text
    actual_nodes = int(report["airfoil_transfinite_curve_nodes"]["1001"])
    assert actual_nodes >= 444
    assert f"Transfinite Curve {{1001}} = {actual_nodes}" in geo_text
    assert report["closed_single_curve_experimental"] is True
    assert report["closed_wall_curve_method"] == "single_spline_bump"
    assert report["airfoil_curve_mode"] == "closed_spline"
    assert report["airfoil_curve_count"] == 1
    assert report["closed_te_cap_curve_ids"] == []
    assert report["rounded_te_curve_sections_enforced"] is False
    assert report["gmsh_curve_connectivity_valid"] is True
    assert report["gmsh_curve_connectivity_issue_count"] == 0


def test_open_diagnostic_mesh_is_not_pass(generated_project: Path) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    mesh_config_path = generated_project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    mesh_config = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    mesh_config["open_geometry_representation"] = "finite_thickness_fabric"
    mesh_config_path.write_text(json.dumps(mesh_config, indent=2), encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "open_ramair",
        "--mesh-level",
        "debug",
        "--dry-run",
        "--overwrite",
    ])
    mesh_root = generated_project / "CFD_2D" / "meshes" / "open_ramair"
    report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    geo_text = (mesh_root / "mesh_final.geo").read_text(encoding="utf-8")
    assert report["status"] != "PASS"
    assert "Mesh.Algorithm = 5;" in geo_text
    assert "Mesh.RandomFactor = 1e-07;" in geo_text
    assert report["diagnostic_geometry_only"] is False
    assert report["openfoam_ready"] is False
    assert report["ram_air_inlet_is_physical_patch"] is False
    assert report["boundary_layer_requested"] is True
    assert report["open_diagnostic_boundary_layer_enabled"] is True
    assert report["open_diagnostic_loop_closed"] is True
    assert report["open_fluid_topology"] == "partitioned_2d_external_inlet_cavity_surfaces"
    assert report["open_connected_fluid_surface"] is True
    assert report["open_thin_solid_fluid_surface"] is True
    assert report["cavity_connected_to_exterior"] is True
    assert report["open_wall_curve_method"] == "segmented_outer_splines"
    assert report["open_boundary_layer_single_loop_bspline"] is False
    assert report["open_boundary_layer_curve_policy"] == "exterior_fabric_sections_plus_nonphysical_inlet_bridge_external_side_only"
    assert report["open_boundary_layer_single_loop_curve_kind"] == "three_exterior_splines_with_transfinite_inlet_connector"
    assert report["open_boundary_layer_single_loop_transfinite"] is False
    assert report["open_boundary_layer_split_curvature_sections"] is True
    assert report["open_te_boundary_curve_id"] == 3011
    assert report["open_te_transfinite_min_nodes"] == 40
    assert report["open_lip_transfinite_min_nodes"] == 160
    assert report["open_surface_target_nodes"] == 1600
    assert report["open_surface_transfinite_multiplier"] == pytest.approx(1.0)
    assert len(report["open_inlet_marker_curve_ids"]) == 1
    assert report["open_internal_cavity_meshed"] is True
    assert report["open_internal_cavity_shares_inlet_marker"] is True
    assert report["open_internal_cavity_duplicate_inlet_marker"] is False
    assert report["open_internal_cavity_solver_connected"] is True
    assert "conformal fluid surfaces" in report["open_internal_cavity_note"]
    assert report["open_boundary_layer_inlet_marker_included"] is False
    assert report["open_boundary_layer_inlet_bridge_in_single_loop"] is False
    assert report["open_boundary_layer_inlet_bridge_included"] is True
    assert report["open_inlet_bridge_embedded_in_single_fluid_surface"] is False
    assert report["open_inlet_marker_transfinite_enabled"] is True
    assert report["open_inlet_marker_transfinite_nodes"] == 176
    assert report["open_inlet_marker_bump_strength"] == pytest.approx(0.60)
    assert report["open_inlet_connector_transfinite"] is True
    assert report["open_inlet_connector_curve_ids"] == [3005, 3006, 3013]
    assert report["open_inlet_refinement_bridge_enabled"] is True
    assert report["open_inlet_refinement_bridge_curve_ids"] == []
    assert report["open_inlet_refinement_bridge_is_physical_patch"] is False
    assert report["open_inlet_refinement_bridge_in_boundary_layer"] is False
    assert report["open_inlet_connector_surface_id"] == 200032
    assert report["open_inlet_boundary_layer_mode"] == "full_prismatic_bridge_without_fans"
    assert report["open_inlet_transition_elements"] == "graded_quads"

    assert report["open_inlet_transition_mesh"] == "graded_transfinite_quads_from_exterior_y1_to_cavity"
    transition = report["open_inlet_transition_distribution"]
    assert transition["normal_nodes"] > 2
    assert transition["progression"] <= 1.22 + 1.0e-12
    assert transition["actual_first_height"] == pytest.approx(report["boundary_layer_first_cell_height_m"])
    assert report["boundary_layer_layers_requested"] == 50
    assert report["open_effective_boundary_layer_growth"] == pytest.approx(1.075)
    assert report["boundary_layer_total_thickness_limited"] is False
    assert report["boundary_layer_total_thickness_chord"] == pytest.approx(report["boundary_layer_raw_total_thickness_chord"])
    assert report["open_boundary_layer_exclude_te_cap_from_bl"] is False
    assert report["open_boundary_layer_curve_ids"]
    assert report["open_boundary_layer_excluded_te_curve_ids"] == []
    assert report["open_boundary_layer_trim_end_segments"] is False
    assert report["open_boundary_layer_trim_ends_chord"] == pytest.approx(0.0)
    assert report["open_boundary_layer_trim_end_points"] == 0
    assert report["open_effective_fabric_thickness_chord"] == pytest.approx(4.0e-4)
    assert report["open_fabric_offset_self_intersections"] == 0
    assert report["open_fabric_offset_cross_intersections"] == 0
    assert report["open_effective_fabric_thickness_chord"] < report["boundary_layer_total_thickness_chord"]
    expected_outer_bl = (
        report["open_surface_size_from_boundary_layer"]["outer_bl_cell_height_chord"]
        * report["open_surface_size_from_boundary_layer"]["factor"]
    )
    assert report["open_effective_surface_size_general_chord"] >= expected_outer_bl
    assert report["open_effective_surface_size_lip_chord"] == pytest.approx(0.0012)
    assert report["open_effective_farfield_size_chord"] == pytest.approx(0.75)
    assert report["open_interface_sizes_from_boundary_layer"] is True
    assert report["open_te_interface_size_chord"] > 0.0
    assert report["open_inlet_interface_size_chord"] > 0.0
    assert report["open_lip_cap_interface_size_chord"] > 0.0
    assert report["open_internal_inlet_active_size_chord"] == pytest.approx(
        report["open_inlet_interface_size_chord"]
    )
    assert report["open_internal_inlet_active_size_chord"] >= report["open_lip_cap_interface_size_chord"]
    assert report["open_te_interface_size_chord"] >= report["open_te_tangential_spacing_chord"] * 0.85
    assert report["open_inlet_interface_size_chord"] >= report["open_inlet_tangential_spacing_chord"] * 0.85
    assert report["open_exterior_normal_upper_valid"] is True
    assert report["open_exterior_normal_lower_valid"] is True
    assert report["open_boundary_layer_restricted_to_external_side"] is True
    assert report["open_cavity_size_field_restricted_to_cavity"] is True
    assert report["open_boundary_layer_aniso_max_deg"] == pytest.approx(30.0)
    assert report["open_le_refinement_requested"] is False
    assert report["open_lip_refinement_requested"] is False
    assert report["open_internal_inlet_refinement_requested"] is True
    assert "transfinite throat" in report["open_internal_inlet_refinement_scope"]
    assert report["open_boundary_layer_fan_at_lips"] is False
    assert report["open_boundary_layer_lip_fan_points"] == 5
    assert report["open_inner_wall_node_factor"] == pytest.approx(
        mesh_config["open_inner_wall_node_factor"]
    )
    assert report["open_inner_te_node_factor"] == pytest.approx(
        mesh_config["open_inner_te_node_factor"]
    )
    assert max(report["open_inner_wall_transfinite_curve_nodes"].values()) < max(report["open_outer_wall_transfinite_curve_nodes"].values())
    marker_id = report["open_inlet_marker_curve_ids"][0]
    assert sorted(report["open_boundary_layer_curve_ids"]) == [3001, 3005, 3011, 3012]
    assert marker_id not in report["open_boundary_layer_curve_ids"]
    assert marker_id not in report["open_boundary_layer_excluded_te_curve_ids"]
    assert "Spline(3001)" in geo_text
    assert "Spline(3011)" in geo_text
    assert "Spline(3012)" in geo_text
    assert "exterior fabric wall carrying BL" in geo_text
    assert "Plane Surface(200033)" not in geo_text
    assert "Plane Surface(200030)" in geo_text
    assert "Plane Surface(200031)" in geo_text
    assert "Plane Surface(200032)" in geo_text
    assert "Line{3005} In Surface{200033}" not in geo_text
    assert 'Physical Surface("internal_cavity_diagnostic")' not in geo_text
    assert "exact_rounded_te_arc_lower_to_mid" not in geo_text
    assert "Transfinite Curve {3001}" in geo_text
    assert "Transfinite Curve {3011}" in geo_text
    assert "Transfinite Curve {3012}" in geo_text
    assert "Using Bump 0.6" in geo_text
    assert (
        f"Using Bump {report['open_inner_wall_end_bump_strength']}"
        in geo_text
    )
    assert report["open_inlet_bridge_smoothing_enabled"] is True
    assert report["open_inlet_bridge_smoothing_handle_fraction"] == pytest.approx(0.080)
    assert report["open_inlet_bridge_curve_kind"] == "Bezier"
    assert "Bezier(3005)" in geo_text
    assert "Line(3006)" in geo_text
    assert "Bezier(3013)" in geo_text
    assert "Transfinite Curve {3005, 3006, 3013} = 176 Using Bump 0.6;" in geo_text
    assert "Field[1].ExcludedSurfacesList" not in geo_text
    assert "Field[1] = BoundaryLayer;" in geo_text
    bl_line = next(line for line in geo_text.splitlines() if line.startswith("Field[1].CurvesList"))
    assert "3001" in bl_line
    assert "3011" in bl_line
    assert "3012" in bl_line
    assert "3005" in bl_line
    assert "3002" not in bl_line
    assert "3003" not in bl_line
    assert "3004" not in bl_line
    assert "3006" not in bl_line
    assert str(marker_id) not in bl_line
    assert "BoundaryLayer Field = 1;" in geo_text
    assert "Field[1].FanPointsList" not in geo_text
    assert "Field[1].PointsList" not in geo_text
    nearfield_line = next(line for line in geo_text.splitlines() if line.startswith("Field[2].CurvesList"))
    assert "3001" in nearfield_line
    assert "3005" in nearfield_line
    assert "CurvesList = {3002, 3004}" in geo_text
    assert "Transfinite Surface {200032} = {" in geo_text
    assert " Alternate;" not in geo_text
    assert "Transfinite Curve {3002, -3004}" in geo_text
    assert "Recombine Surface {200032};" in geo_text
    assert "Short orthogonal graded block" in geo_text
    assert "Field[2] = Distance;" in geo_text
    assert "Field[3] = Threshold;" in geo_text
    assert "Field[4] = Threshold;" in geo_text
    assert "Field[5] = Threshold;" in geo_text
    assert "StopAtDistMax = 1;" in geo_text
    assert "Local BL-to-triangle transition at rounded TE" in geo_text
    assert "Match adjacent triangles to tangential inlet/BL-front spacing" in geo_text
    assert " = Restrict;" in geo_text
    assert " = Box;" not in geo_text
    assert "lc_le=" in geo_text
    assert "lc_lip=" in geo_text
    assert "internal_cavity_upper_duplicate_spline_no_bl" not in geo_text
    assert report["open_internal_cavity_curve_mode"] == "three_continuous_inner_splines_without_bl"
    assert not list(mesh_root.rglob("*.svg"))

    mesh_config_path = generated_project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    with_fan = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    with_fan["open_inlet_boundary_layer_mode"] = "full_prismatic_bridge_with_fans"
    mesh_config_path.write_text(json.dumps(with_fan, indent=2), encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "open_ramair",
        "--mesh-level",
        "debug",
        "--dry-run",
        "--overwrite",
        "--previous-output-action",
        "delete",
    ])
    with_fan_report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    with_fan_geo = (mesh_root / "mesh_final.geo").read_text(encoding="utf-8")
    assert with_fan_report["open_inlet_boundary_layer_mode"] == "full_prismatic_bridge_with_fans"
    assert with_fan_report["open_boundary_layer_inlet_bridge_included"] is True
    assert with_fan_report["open_boundary_layer_fan_at_lips"] is True
    assert 3005 in with_fan_report["open_boundary_layer_curve_ids"]
    assert "Field[1].FanPointsList" in with_fan_geo
    assert "Field[1].PointsList" not in with_fan_geo

    alternative = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    alternative["open_inlet_boundary_layer_mode"] = "triangular_inlet_no_bl"
    alternative["open_inlet_transition_elements"] = "triangles"
    mesh_config_path.write_text(json.dumps(alternative, indent=2), encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "open_ramair",
        "--mesh-level",
        "debug",
        "--dry-run",
        "--overwrite",
        "--previous-output-action",
        "delete",
    ])
    alternative_report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    alternative_geo = (mesh_root / "mesh_final.geo").read_text(encoding="utf-8")
    assert alternative_report["open_inlet_boundary_layer_mode"] == "triangular_inlet_no_bl"
    assert alternative_report["open_inlet_transition_mesh"] == "unstructured_triangles_through_fabric_thickness"
    assert 3005 not in alternative_report["open_boundary_layer_curve_ids"]
    assert "Field[1].FanPointsList" not in alternative_geo
    assert "Field[1].PointsList" in alternative_geo
    assert "Transfinite Surface {200032}" not in alternative_geo


def test_open_zero_thickness_uses_uncut_base_curve_and_no_inlet_patch(
    generated_project: Path,
) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    shutil.copytree(
        PROJECT_ROOT / "CFD_2D/CFD_2D_inputs/case_package/reference_uncut",
        generated_project / "CFD_2D/CFD_2D_inputs/case_package/reference_uncut",
        dirs_exist_ok=True,
    )
    mesh_config_path = generated_project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    mesh_config = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    mesh_config.update({
        "open_geometry_representation": "zero_thickness_base_profile",
        "open_base_profile_variant": "reference_uncut",
    })
    mesh_config_path.write_text(json.dumps(mesh_config, indent=2), encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "open_ramair",
        "--mesh-level",
        "custom",
        "--dry-run",
        "--overwrite",
    ])
    mesh_root = generated_project / "CFD_2D/meshes/open_ramair"
    report = json.loads((mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8"))
    geo = (mesh_root / "mesh_final.geo").read_text(encoding="utf-8")
    assert report["surface_kind"] == "open_zero_thickness_base_profile_connected_fluid"
    assert report["open_geometry_representation"] == "zero_thickness_base_profile"
    assert report["open_base_profile_variant"] == "reference_uncut"
    assert report["base_inlet_control_points"] >= 3
    assert report["base_inlet_self_intersections"] == 0
    assert report["base_inlet_alignment_mode"] == "similarity"
    assert report["base_inlet_exact_similarity_of_uncut_arc"] is True
    assert report["base_inlet_lower_tangent_mismatch_deg"] < 45.0
    assert report["base_inlet_upper_tangent_mismatch_deg"] < 45.0
    assert report["open_connected_fluid_surface"] is True
    assert report["open_selective_inlet_interface_merge"] is True
    assert 'Physical Line("_ramair_inlet_interface_external")' in geo
    assert 'Physical Line("_ramair_inlet_interface_internal")' in geo
    assert 'Physical Line("airfoil_wall_external")' in geo
    assert 'Physical Line("airfoil_wall_internal")' in geo
    assert 'Physical Line("airfoil_wall")' not in geo
    contour_nodes = report["open_zero_thickness_curve_nodes"]
    realized_segments = sum(int(value) - 1 for value in contour_nodes.values())
    assert realized_segments == report["open_zero_thickness_contour_target_nodes"]
    assert report["open_zero_thickness_contour_realized_segments"] == realized_segments
    assert report["gmsh_curve_connectivity_valid"] is True
    assert report["gmsh_curve_connectivity_issue_count"] == 0
    assert report["open_zero_thickness_duplicate_control_points"] == 0
    assert report["open_zero_thickness_minimum_control_segment_chord"] >= 5.0e-7
    assert report["open_zero_thickness_realized_spacing_ratio"] < 2.0
    assert report["open_wall_external_nodes"]["upper"] > report["open_wall_internal_nodes"]["upper"]
    assert report["open_wall_external_nodes"]["lower"] > report["open_wall_internal_nodes"]["lower"]
    assert report["open_wall_internal_nodes"]["te"] >= mesh_config["open_inner_te_min_nodes"]
    assert report["open_internal_inlet_refinement_requested"] is True
    assert report["open_cavity_inlet_size_strategy"] == "hybrid_boundary_extension"
    assert report["open_internal_inlet_boundary_size_source"] == (
        "actual_transfinite_inlet_boundary_edges"
    )
    assert 0.0 < report["open_internal_inlet_active_size_chord"]
    assert (
        report["open_internal_inlet_active_size_chord"]
        <= report["open_inlet_interface_tangential_size_chord"]
    )
    assert report["open_internal_te_refinement_requested"] is True
    assert report["open_internal_te_interface_size_chord"] > 0.0
    assert report["open_transition_sigmoid_enabled"] is True
    assert ".Sigmoid = 1;" in geo
    assert ".StopAtDistMax = 0;" in geo
    assert " = Extend;" in geo
    assert ".SurfacesList" in geo
    assert report["open_boundary_layer_curve_ids"] == report["boundary_layer_curve_ids"]
    assert report["open_internal_inlet_normal_size_rule"].startswith(
        "short y1-compatible strip"
    )
    assert report["open_internal_inlet_normal_y1_factor"] >= 1.0
    assert "ram_air_inlet" not in geo

    mesh_config["open_cavity_inlet_size_strategy"] = "boundary_uniform"
    mesh_config_path.write_text(json.dumps(mesh_config, indent=2), encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_mesh_builder.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "open_ramair",
        "--mesh-level",
        "custom",
        "--dry-run",
        "--overwrite",
        "--previous-output-action",
        "delete",
    ])
    uniform_report = json.loads(
        (mesh_root / "mesh_quality_report.json").read_text(encoding="utf-8")
    )
    uniform_geo = (mesh_root / "mesh_final.geo").read_text(encoding="utf-8")
    assert uniform_report["open_cavity_inlet_size_strategy"] == "boundary_uniform"
    assert "Diagnostic alternative" in uniform_geo
    assert " = MathEval;" in uniform_geo
    assert "fabric_thickness" not in geo
    assert "Field[1].ExcludedSurfacesList" in geo


def test_case_writer_does_not_create_empty_polymesh(generated_project: Path) -> None:
    test_case_builder_finds_cfd_inputs_and_forbids_physical_ram_air_inlet(generated_project)
    mesh_root = generated_project / "CFD_2D" / "meshes" / "closed_reference"
    mesh_root.mkdir(parents=True)
    (mesh_root / "mesh_final.msh").write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n", encoding="utf-8")
    (mesh_root / "mesh_quality_report.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    (mesh_root / "MESH_APPROVED.flag").write_text("approved_for_unit_test=true\n", encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_openfoam_case_writer.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--alpha",
        "4",
        "--write-case",
    ])
    case_dir = generated_project / "CFD_2D" / "openfoam_cases" / "closed_reference" / "alpha_p4p000"
    assert (case_dir / "constant" / "gmsh" / "mesh_final.msh").exists()
    assert not (case_dir / "constant" / "polyMesh").exists()
    assert (case_dir / "constant" / "physicalProperties").exists()
    assert (case_dir / "system" / "fvModels").exists()
    assert (case_dir / "system" / "fvConstraints").exists()
    control = (case_dir / "system" / "controlDict").read_text(encoding="utf-8")
    assert "application     foamRun;" in control
    assert "solver          incompressibleFluid;" in control
    assert "runTimeModifiable true;" in control
    assert "maxDeltaT" in control
    assert "writeControl    adjustableRunTime;" in control
    assert "writeCompression on;" in control
    assert "Aref            " in control
    assert "models" not in (case_dir / "system" / "fvModels").read_text(encoding="utf-8")
    assert "constraints" not in (case_dir / "system" / "fvConstraints").read_text(encoding="utf-8")
    assert (case_dir / "system" / "fvSchemes").read_text(encoding="utf-8").find("wallDist") >= 0
    assert json.loads((case_dir / "case_config.json").read_text(encoding="utf-8"))["mesh_status"] == "MESH_NOT_CONVERTED"
    summary = json.loads((case_dir / "case_input_summary.json").read_text(encoding="utf-8"))
    assert summary["velocity_source"] == "reynolds"
    assert "reference_area_m2" in summary
    assert summary["field_write_interval_star"] == pytest.approx(0.25)
    assert summary["purgeWrite"] == 24
    assert summary["scalar_histories_each_iteration"] == [
        "forceCoeffs", "residuals", "Courant/deltaT in solver log"
    ]


def test_case_writer_overwrite_archives_previous_case(generated_project: Path) -> None:
    test_case_writer_does_not_create_empty_polymesh(generated_project)
    case_dir = generated_project / "CFD_2D" / "openfoam_cases" / "closed_reference" / "alpha_p4p000"
    (case_dir / "stale_case_marker.txt").write_text("old case", encoding="utf-8")
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_openfoam_case_writer.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--alpha",
        "4",
        "--write-case",
        "--overwrite",
    ])
    backups = sorted((generated_project / "Previous Versions" / "openfoam_case_backups").glob("closed_reference_alpha_p4p000_*"))
    assert backups
    assert (backups[-1] / "stale_case_marker.txt").exists()
    assert (backups[-1] / "case_backup_manifest.json").exists()
    assert not (case_dir / "stale_case_marker.txt").exists()


def test_runner_is_dry_run_by_default(generated_project: Path) -> None:
    test_case_writer_does_not_create_empty_polymesh(generated_project)
    result = run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_openfoam_runner.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--alpha",
        "4",
    ])
    assert "Run script written:" in result.stdout
    assert "foamRun" in result.stdout or "pimpleFoam" in result.stdout
    assert "-solver incompressibleFluid" in result.stdout or "pimpleFoam" in result.stdout
    case_dir = generated_project / "CFD_2D" / "openfoam_cases" / "closed_reference" / "alpha_p4p000"
    assert json.loads((case_dir / "run_status.json").read_text(encoding="utf-8"))["status"] == "DRY_RUN"


def test_postprocess_reports_not_run_yet_without_empty_csvs(generated_project: Path) -> None:
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_postprocess.py"),
        "--case-root",
        str(generated_project),
        "--variant",
        "closed_reference",
        "--alpha",
        "4",
    ])
    out = generated_project / "CFD_2D" / "results" / "closed_reference" / "alpha_p4p000"
    summary = json.loads((out / "case_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "NOT_RUN_YET"
    assert not (out / "forceCoeffs_raw.csv").exists()


def test_relative_case_root_works(generated_project: Path) -> None:
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_2d_profile_case_builder.py"),
        "--case-root",
        ".",
        "--variant",
        "open_ramair",
        "--overwrite",
    ], cwd=generated_project)
    assert (generated_project / "CFD_2D" / "CFD_2D_inputs" / "case_package" / "open_ramair" / "points.csv").exists()


def test_workflow_tool_lists_plan_and_writes_bash(generated_project: Path) -> None:
    result = run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_cfd2d_workflow_tool.py"),
        "--case-root",
        str(generated_project),
        "--list-geometry",
        "--plan",
        "--show-mesh-settings",
    ])
    assert "reference_uncut" in result.stdout
    assert "cfd2d_workflow_config.json" in result.stdout
    assert "open_wall_curve_method = segmented_outer_splines" in result.stdout
    assert "open_inlet_boundary_layer_mode = full_prismatic_bridge_without_fans" in result.stdout
    assert "open_inlet_transition_elements = graded_quads" in result.stdout
    assert "open_inlet_marker_bump_strength = 0.6" in result.stdout

    out_script = generated_project / "docs" / "run_cfd2d_custom_case_wsl.sh"
    run_cmd([
        sys.executable,
        str(SCRIPTS / "ramair_cfd2d_workflow_tool.py"),
        "--case-root",
        str(generated_project),
        "--write-script",
        str(out_script),
        "--overwrite",
    ])
    text = out_script.read_text(encoding="utf-8")
    assert "ramair_2d_mesh_builder.py" in text
    assert "ramair_2d_openfoam_case_writer.py" in text
    assert "handle_existing_path \"$MESH_DIR\"" in text
    assert "$$MESH_DIR" not in text
    assert "gmshToFoam" in text
