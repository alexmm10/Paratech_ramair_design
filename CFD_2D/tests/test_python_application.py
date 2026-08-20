from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "CFD_2D" / "app"
SCRIPTS_DIR = ROOT / "CFD_2D" / "scripts"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_backend import (  # noqa: E402
    JobManager,
    _write_checkmesh_paraview_script,
    case_builder_command,
    find_project_root,
    mesh_command,
    mesh_optimizer_command,
    runner_command,
    inlet_design_command,
)
from ramair_2d_mesh_builder import DEFAULT_MESH_CONFIG  # noqa: E402


def test_application_sources_are_valid_python() -> None:
    paths = [
        ROOT / "run_ramair_cfd2d_app.py",
        APP_DIR / "ramair_cfd2d_app.py",
        APP_DIR / "workflow_backend.py",
        APP_DIR / "gmsh_python_runner.py",
        APP_DIR / "pyfoam_solver_runner.py",
        APP_DIR / "ramair_live_monitor.py",
        APP_DIR / "windows_gmsh_viewer.py",
        ROOT / "CFD_2D/scripts/openfoam_environment.py",
        ROOT / "CFD_2D/scripts/ramair_2d_mesh_optimizer.py",
        ROOT / "CFD_2D/scripts/ramair_2d_mesh_refinement_study.py",
        ROOT / "CFD_2D/scripts/ramair_2d_mesh_refinement_analysis.py",
        ROOT / "CFD_2D/scripts/ramair_2d_batch_postprocess.py",
        ROOT / "CFD_2D/scripts/ramair_2d_validation_publish.py",
        ROOT / "CFD_2D/scripts/paraview_case_viewer.py",
        ROOT / "CFD_2D/scripts/ramair_2d_inlet_designer.py",
        ROOT / "CFD_2D/scripts/initialize_project_layout.py",
        ROOT / "Application Support/Tools/package_ramair_project.py",
        ROOT / "Application Support/Tools/package_ramair_catia_windows.py",
        ROOT / "CATIA/Utilities/VERIFY_CATIA_PACKAGE.py",
    ]
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_project_root_and_stage_commands_use_existing_scripts() -> None:
    assert find_project_root(APP_DIR) == ROOT.resolve()
    case_command = case_builder_command(
        ROOT,
        variant="reference_uncut",
        alpha_start=4.0,
        alpha_end=4.0,
        alpha_step=1.0,
        reynolds=4e6,
        mach=0.1,
        rho=1.225,
        mu=1.81e-5,
    )
    assert Path(case_command[1]).name == "ramair_2d_profile_case_builder.py"
    assert "--validate" in case_command
    assert "--overwrite" in case_command
    inlet_command = inlet_design_command(ROOT)
    assert Path(inlet_command[1]).name == "ramair_2d_inlet_designer.py"
    assert Path(inlet_command[inlet_command.index("--config") + 1]).name == "cfd2d_inlet_design_config.json"


def test_inlet_designer_configuration_and_pure_geometry_contract() -> None:
    scripts = ROOT / "CFD_2D/scripts"
    sys.path.insert(0, str(scripts))
    from ramair_2d_inlet_designer import alpha_values, generated_profile_name, load_design_config, parse_polar

    path = ROOT / "CFD_2D/CFD_2D_inputs/config/cfd2d_inlet_design_config.json"
    config = load_design_config(path)
    assert config["panel_count"] == 200
    assert config["design_mode"] in {"standard_full_polar", "optimized_cl_window"}
    assert alpha_values(config)[0] == pytest.approx(config["alpha_start_deg"])
    assert alpha_values(config)[-1] == pytest.approx(config["alpha_end_deg"])
    name = generated_profile_name(ROOT / "Airfoil Profiles/NASA LS1-0417.dat", config, 4.097)
    assert "Re4000000" in name
    assert "Gap4p1pct" in name

    result_root = ROOT / "CFD_2D/CFD_2D_inputs/inlet_design"
    generated = sorted(result_root.glob("*/polar.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if generated:
        parsed = parse_polar(generated[0])
        assert {"alpha_deg", "CL", "CD", "CM"}.issubset(parsed.columns)
        assert not parsed.empty


def test_inlet_designer_ui_registers_generated_profile() -> None:
    app_text = (APP_DIR / "ramair_cfd2d_app.py").read_text(encoding="utf-8")
    assert "Diseno 2D del corte ram-air" in app_text
    assert "Usar como main_profile" in app_text
    assert "optimized_cl_window" in app_text
    assert "Abrir fallos en ParaView" in app_text
    assert "open_checkmesh_problem_viewer" in app_text
    assert (ROOT / "Application Support/Tools/xfoil/linux/xfoil").is_file()
    assert (ROOT / "Application Support/Tools/xfoil/source/Xfoil699src.zip").is_file()
    assert (ROOT / "Application Support/Tools/xfoil/source/xfoil699-gfortran-eof.patch").is_file()
    assert (ROOT / "Documents and Manuals/Application/build_xfoil_699_wsl.sh").is_file()


def test_mesh_command_exposes_python_api_and_fresh_output_policy() -> None:
    command = mesh_command(
        ROOT,
        variant="reference_uncut",
        domain="ross_cgrid_like",
        mesh_level="debug",
        gmsh_backend="python_api",
        gmsh_timeout_s=900,
        openfoam_timeout_s=600,
        threads=8,
        previous_output_action="archive",
        write_openfoam_mesh=True,
        check_mesh=True,
        plot=True,
    )
    assert command[command.index("--gmsh-backend") + 1] == "python_api"
    assert command[command.index("--previous-output-action") + 1] == "archive"
    assert "--write-openfoam-mesh" in command
    assert "--check-mesh" in command


def test_runner_is_dry_by_default_and_pyfoam_is_explicit() -> None:
    dry = runner_command(
        ROOT,
        variant="reference_uncut",
        alpha=4.0,
        solver="auto",
        execution_backend="pyfoam",
        n_cores=1,
        timeout_min=30,
        run=False,
        stop_after_min=None,
        stop_grace_min=2,
        stop_mode="writeNow",
        stop_if_checkmesh_fails=True,
    )
    assert "--run" not in dry
    assert dry[dry.index("--execution-backend") + 1] == "pyfoam"
    real = [*dry, "--run"]
    assert "--run" in real

    monitored = runner_command(
        ROOT,
        variant="reference_uncut",
        alpha=4.0,
        solver="auto",
        execution_backend="pyfoam",
        n_cores=1,
        timeout_min=30,
        run=False,
        stop_after_min=None,
        stop_grace_min=2,
        stop_mode="writeNow",
        stop_if_checkmesh_fails=True,
        stop_when_force_stable=True,
        convergence_minimum_time_star=40.0,
        convergence_window_time_star=10.0,
        convergence_mean_tolerance=0.02,
        convergence_oscillation_tolerance=0.1,
    )
    assert "--stop-when-force-stable" in monitored
    assert monitored[monitored.index("--convergence-window-time-star") + 1] == "10.0"


def test_force_coefficient_stability_uses_two_convective_windows(tmp_path: Path) -> None:
    scripts = ROOT / "CFD_2D/scripts"
    sys.path.insert(0, str(scripts))
    from ramair_2d_openfoam_runner import force_coeff_stability_report

    (tmp_path / "case_config.json").write_text(
        json.dumps({"chord_m": 1.0, "velocity_m_s": 1.0}),
        encoding="utf-8",
    )
    output = tmp_path / "postProcessing/forceCoeffs/0"
    output.mkdir(parents=True)
    rows = ["# Time Cd Cs Cl CmRoll CmPitch CmYaw"]
    for index in range(121):
        oscillation = 0.01 if index % 2 else -0.01
        rows.append(f"{index} {0.030 + oscillation * 0.1} 0 {0.80 + oscillation} 0 {-0.04 + oscillation * 0.2} 0")
    (output / "coefficient.dat").write_text("\n".join(rows) + "\n", encoding="utf-8")

    report = force_coeff_stability_report(
        tmp_path,
        minimum_time_star=40.0,
        window_time_star=20.0,
        mean_tolerance=0.02,
        oscillation_tolerance=0.10,
    )
    assert report["status"] == "STABLE"
    assert report["samples_previous"] >= 20
    assert set(report["metrics"]) == {"Cl", "Cd", "Cm"}


def test_openfoam_runner_rejects_whitespace_case_path_with_remediation(tmp_path: Path) -> None:
    scripts = ROOT / "CFD_2D/scripts"
    sys.path.insert(0, str(scripts))
    from ramair_2d_openfoam_runner import validate_openfoam_case_path

    with pytest.raises(RuntimeError, match="DESIGN_APP"):
        validate_openfoam_case_path(tmp_path / "DESIGN APP" / "case")


def test_pinned_application_dependencies_and_manual_exist() -> None:
    requirements = (APP_DIR / "requirements-cfd2d-app.txt").read_text(encoding="utf-8")
    assert "streamlit==" in requirements
    assert "pyarrow==18.1.0" in requirements
    assert "gmsh==4.15.2" in requirements
    assert "PyFoam==2026.6" in requirements
    manual = ROOT / "Documents and Manuals" / "Application" / "README_CFD2D_PYTHON_APP.md"
    assert manual.is_file()
    text = manual.read_text(encoding="utf-8")
    assert "Windows/WSL" in text
    assert "PyFoam.Execution.BasicRunner" in text
    bootstrap = (ROOT / "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh").read_text(encoding="utf-8")
    assert "--system-site-packages" not in bootstrap
    assert "from mpl_toolkits.mplot3d import Axes3D" in bootstrap
    assert "--install-system" in bootstrap
    assert "install_system_packages" in bootstrap
    assert "environment_setup.json" in bootstrap
    assert "install_xfoil_wsl.sh" in bootstrap
    assert "install_gmsh_4_15_wsl.sh" in bootstrap
    xfoil_installer = (ROOT / "Documents and Manuals/Application/install_xfoil_wsl.sh").read_text(encoding="utf-8")
    assert 'Application Support/Tools/xfoil/linux/xfoil' in xfoil_installer
    assert '$ROOT/tools/xfoil' not in xfoil_installer


def test_streamlit_ui_avoids_arrow_tables_and_eager_top_level_tabs() -> None:
    app_text = (APP_DIR / "ramair_cfd2d_app.py").read_text(encoding="utf-8")
    assert "st.dataframe" not in app_text
    assert "render_records_table" in app_text
    assert 'key="active-workflow-page"' in app_text
    assert "active_page ==" in app_text
    assert 'st.title("RamAir: Design and CFD")' in app_text
    assert 'if active_page in {"Caso OpenFOAM", "Ejecucion", "Postproceso"}' in app_text
    assert '"Perfil CFD del caso"' in app_text
    assert '"Contexto activo"' in app_text


def test_optional_catia_launcher_is_explicit_and_non_required() -> None:
    launcher = ROOT / "Application Support/Tools/launch_catia_macro.py"
    text = launcher.read_text(encoding="utf-8")
    app_text = (APP_DIR / "ramair_cfd2d_app.py").read_text(encoding="utf-8")
    assert "RAMAIR_CATIA_CNEXT" in text
    assert "RAMAIR_CATIA_INPUTS" in text
    assert "Start-Process" in text
    assert "action.add_argument(\"--run\"" in text
    assert "disabled=not (catia_available and catia_inputs_ready and catia_macro_ready)" in app_text
    assert "CATIA V5 no se ha detectado" in app_text
    assert '"y1 calculado [m]"' in app_text
    assert '"Espesor / cuerda"' in app_text
    assert "apply_pending_configuration_reload" in app_text
    assert "_config_ui_revision" in app_text
    assert "completion_action" in app_text
    assert ".ramair-table td {background: #111827; color: #f9fafb" in app_text
    assert "results_library_locations" in app_text
    assert "open_results_library" in app_text


def test_display_labels_do_not_disable_selectbox_choices() -> None:
    app_text = (APP_DIR / "ramair_cfd2d_app.py").read_text(encoding="utf-8")

    assert 'field_name = label.split(".")[-1]' in app_text
    assert "options = CHOICES.get(field_name)" in app_text
    assert "labels = CHOICE_LABELS.get(field_name, {})" in app_text


def test_environment_checker_reports_pyarrow_runtime_compatibility() -> None:
    checker_text = (ROOT / "CFD_2D/scripts/check_environment.py").read_text(encoding="utf-8")
    assert "def pyarrow_runtime_check" in checker_text
    assert 'found_version == "25.0.0"' in checker_text
    assert "pyarrow 18.1.0" in checker_text
    assert "pyarrow_runtime_check()," in checker_text
    assert "inspect_xfoil" in checker_text
    assert "remediation_for" in checker_text
    assert "INSTALL_HINTS" in checker_text
    foam_environment = (ROOT / "CFD_2D/scripts/openfoam_environment.py").read_text(encoding="utf-8")
    assert "Path(sys.executable).absolute().parent" in foam_environment
    assert 'ZSH_NAME="${ZSH_NAME-}"' in foam_environment
    assert "Do not call ``resolve()``" in foam_environment


def test_mesh_config_declares_backend_without_losing_existing_sections() -> None:
    config = json.loads((ROOT / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json").read_text(encoding="utf-8"))
    effective = dict(DEFAULT_MESH_CONFIG)
    effective.update(config)
    assert effective["gmsh_backend"] == "auto"
    assert 75_000 <= effective["max_internal_parse_elements"] <= 250_000
    assert "closed_boundary_layer_layers" in effective
    assert "open_boundary_layer_layers" in effective
    assert effective["open_inlet_boundary_layer_mode"] == "full_prismatic_bridge_without_fans"
    assert effective["open_inlet_transition_elements"] == "graded_quads"
    assert effective["open_inlet_connector_normal_nodes"] == 0
    assert effective["open_inlet_transition_growth"] <= 1.22
    assert effective["open_inlet_marker_bump_strength"] == pytest.approx(0.60)
    assert effective["closed_wall_target_nodes"] >= 24
    assert effective["closed_te_target_nodes"] >= 8
    assert effective["open_inlet_marker_transfinite_nodes"] >= 4
    assert effective["open_boundary_layer_lip_fan_points"] >= 3
    assert effective["open_inner_wall_node_factor"] < 1.0
    assert effective["open_single_connected_surface_2d"] is False
    assert effective["closed_farfield_transition_dist_chord"] > effective["closed_nearfield_dist_max_chord"]
    assert effective["open_farfield_transition_dist_chord"] > effective["open_nearfield_dist_max_chord"]
    app_text = (APP_DIR / "ramair_cfd2d_app.py").read_text(encoding="utf-8")
    assert 'mesh_level_values("fine")' in app_text
    legacy_runtime_only = {
        "purpose",
        "surface_size_bl_outer_min_chord",
        "surface_size_bl_outer_max_chord",
        "wake_refinement_length_chord",
        "wake_refinement_height_chord",
        "wake_size_chord",
    }
    for key in set(config).difference(legacy_runtime_only):
        assert f'"{key}"' in app_text, f"Mesh UI does not expose {key}"

    reference = json.loads((ROOT / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config_reference.json").read_text(encoding="utf-8"))
    described: set[str] = set()
    def collect_descriptions(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if isinstance(nested, str):
                    described.add(key)
                else:
                    collect_descriptions(nested)
    collect_descriptions(reference)
    assert not set(config).difference(described).difference(
        legacy_runtime_only
    ), "Every current editable mesh parameter must have UI help text"


def test_geometry_ui_exposes_grouped_catia_configuration() -> None:
    app_text = (APP_DIR / "ramair_cfd2d_app.py").read_text(encoding="utf-8")
    project = json.loads((ROOT / "Application Support/Configurations/default_case_config.json").read_text(encoding="utf-8"))
    system = json.loads((ROOT / "Application Support/Configurations/ramair_catia_system_config.json").read_text(encoding="utf-8"))
    assert "TAB_INTROS" in app_text
    for tab in ["Estado", "Geometria", "Caso CFD", "Malla", "Caso OpenFOAM", "Ejecucion", "Postproceso", "Archivos y logs"]:
        assert f'"{tab}"' in app_text
    assert "PROJECT_TAB_LAYOUT" in app_text
    assert "SYSTEM_TAB_LAYOUT" in app_text
    assert "catia_system_config_editor" in app_text
    assert "Plataforma" in app_text
    assert "Preprocesado del perfil" in app_text
    assert "Cierre y redondeado del trailing edge" in app_text
    assert "ENABLE SUSPENSION SYSTEM" in app_text
    assert "Opciones avanzadas CATIA: modificar solo ante fallos" in app_text
    assert "Ejemplo completo" not in app_text or "custom_specs" in app_text
    for section in project:
        assert f'"{section}"' in app_text, f"Project UI does not expose {section}"
    for section in system:
        assert f'"{section}"' in app_text, f"CATIA system UI does not expose {section}"


def test_catscript_exports_canopy_before_optional_assembly() -> None:
    text = (ROOT / "Generate_RamAir_Canopy_MAIN.CATScript").read_text(encoding="utf-8")
    assert 'exportCanopyIges = ParamBool(params, "export_iges"' in text
    assert 'exportIges = ParamBool(params, "export_full_assembly_iges", False)' in text
    canopy_export = text.index('partDoc.ExportData exportCanopyIgsPath, "igs"')
    suspension = text.index('GenerateSuspensionLinesFromCsv')
    assembly_export = text.index('partDoc.ExportData exportIgsPath, "igs"')
    assert canopy_export < suspension < assembly_export


def test_portable_installer_and_layout_are_relative() -> None:
    launcher = (ROOT / "run_ramair_cfd2d_app.py").read_text(encoding="utf-8")
    bootstrap = (ROOT / "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh").read_text(encoding="utf-8")
    packager = (ROOT / "Application Support/Tools/package_ramair_project.py").read_text(encoding="utf-8")
    assert "Initialized native WSL project" in launcher
    assert "synchronized atomically" in launcher
    assert "BACKEND_API_VERSION" in launcher
    assert "server.fileWatcherType" in launcher
    assert "Streamlit itself does not need OpenFOAM" in launcher
    assert "configure_first_run" in launcher
    assert "Run the complete automatic repair" in launcher
    assert "--install-system" in launcher
    assert "runtime_sync.lock" in launcher
    assert "RAMAIR_SYNC_SOURCE" in launcher
    assert 'CANONICAL_WSL_ROOT = "~/ramair_cfd/DESIGN_APP"' in launcher
    assert "DESIGN APP" in launcher and "INPUT_FILES" in launcher
    assert "runtime_sync_manifest.json" in launcher
    assert "Documents and Manuals/Application" in launcher
    assert "CATIA/Utilities" in launcher
    assert "initialize_project_layout.py" in bootstrap
    assert "GENERATED_PREFIXES" in packager
    assert "os.walk(source, topdown=True" in packager
    assert "C:/Users/alejm" not in launcher
    assert "/home/alejm" not in launcher
    assert (ROOT / "Documents and Manuals/Application/INSTALL_NEW_DEVICE.md").is_file()
    assert (ROOT / "START_RAMAIR_CFD2D_APP.bat").is_file()
    assert (ROOT / "INSTALL_AND_START_RAMAIR_CFD2D_APP.bat").is_file()


def test_standalone_catia_windows_package_contract() -> None:
    verifier = (ROOT / "CATIA/Utilities/VERIFY_CATIA_PACKAGE.py").read_text(encoding="utf-8")
    packager = (ROOT / "Application Support/Tools/package_ramair_catia_windows.py").read_text(encoding="utf-8")
    assert "ramair_profile_points_for_CATIA.csv" in verifier
    assert "CATIA was not executed" in verifier
    assert "catia_executed" in packager
    assert "critical_sha256" in packager
    assert "--use-existing-catia-inputs" in packager
    assert (ROOT / "SETUP_CATIA_PREPROCESSOR_WINDOWS.bat").is_file()
    assert (ROOT / "RUN_CATIA_PREPROCESSOR_WINDOWS.bat").is_file()
    assert (ROOT / "Documents and Manuals/Application/README_CATIA_WINDOWS_PACKAGE.md").is_file()


def test_catia_packager_falls_back_when_last_run_config_is_absent(tmp_path: Path) -> None:
    script = ROOT / "Application Support/Tools/package_ramair_catia_windows.py"
    spec = importlib.util.spec_from_file_location("ramair_catia_packager_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source"
    active = source / "Application Support/Configurations/default_case_config.json"
    active.parent.mkdir(parents=True)
    active.write_text('{"profile_inputs": {}}\n', encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    selected = module.copy_last_run_config(source, stage)
    assert selected == "active_default_fallback"
    assert (stage / "configs/last_preprocessor_run_config.json").read_text(encoding="utf-8") == active.read_text(encoding="utf-8")


def test_job_manager_records_log_and_exit_status(tmp_path: Path) -> None:
    (tmp_path / "CFD_2D").mkdir()
    manager = JobManager(tmp_path)
    job = manager.start("unit", [sys.executable, "-c", "print('ramair-app-ok')"])
    deadline = time.time() + 10
    while time.time() < deadline:
        job = manager.poll(job)
        if job.status != "RUNNING":
            break
        time.sleep(0.05)
    assert job.status == "COMPLETED"
    assert job.returncode == 0
    assert "ramair-app-ok" in Path(job.log_path).read_text(encoding="utf-8")


def test_job_manager_manual_stop_remains_visible_and_restartable(tmp_path: Path) -> None:
    (tmp_path / "CFD_2D").mkdir()
    manager = JobManager(tmp_path)
    job = manager.start("unit_stop", [sys.executable, "-c", "import time; time.sleep(30)"])
    job = manager.mark_stop_requested(job)
    assert job.status == "STOP_REQUESTED"
    job = manager.stop(job)
    assert job.status == "STOPPING"
    deadline = time.time() + 10
    while time.time() < deadline:
        job = manager.poll(job)
        if job.status not in {"RUNNING", "STOP_REQUESTED", "STOPPING"}:
            break
        time.sleep(0.05)
    assert job.status == "PAUSED_RESTARTABLE"
    assert manager.active_jobs() == []


def test_remote_execution_packager_contains_restart_stop_and_checksums(tmp_path: Path) -> None:
    runner_help = subprocess.run(
        [
            sys.executable,
            str(ROOT / "CFD_2D/scripts/ramair_remote_queue_runner.py"),
            "--help",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert runner_help.returncode == 0, runner_help.stdout
    case = tmp_path / "alpha_p4p000"
    (case / "system").mkdir(parents=True)
    (case / "system/controlDict").write_text(
        "application foamRun;\nstopAt endTime;\n", encoding="utf-8"
    )
    (case / "case_input_summary.json").write_text(
        json.dumps({"variant": "reference_uncut", "alpha_deg": 4.0}),
        encoding="utf-8",
    )
    output = tmp_path / "remote.zip"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "Application Support/Tools/package_ramair_remote_execution.py"),
            "--project-root", str(ROOT),
            "--case", str(case),
            "--output", str(output),
            "--n-cores", "8",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        prefix = "DESIGN_APP_REMOTE/"
        assert prefix + "run_remote.sh" in names
        assert prefix + "resume_remote.sh" in names
        assert prefix + "stop_remote.sh" in names
        assert prefix + "postprocess_remote.sh" in names
        assert prefix + "remote_queue.json" in names
        assert prefix + "REMOTE_PACKAGE_MANIFEST.json" in names
        queue = json.loads(archive.read(prefix + "remote_queue.json"))
        assert queue["cases"][0]["n_cores"] == 8
        assert queue["cases"][0]["variant"] == "reference_uncut"


def test_checkmesh_paraview_script_applies_readers_and_frames_problem_sets(tmp_path: Path) -> None:
    foam = tmp_path / "case.foam"
    problem = tmp_path / "highSkewFaces.vtk"
    foam.touch()
    problem.write_text("# vtk DataFile Version 2.0\n", encoding="utf-8")
    script = _write_checkmesh_paraview_script(
        tmp_path / "checkMesh_problem_viewer.py",
        foam,
        [problem],
        windows_paths=False,
    )
    text = script.read_text(encoding="utf-8")
    assert "OpenDataFile" in text
    assert "from paraview.simple import _DisableFirstRenderCameraReset" in text
    assert "UpdatePipeline" in text
    assert 'Representation = "Surface With Edges"' in text
    assert "ResetCamera(view)" in text
    assert "focus_indices" in text
    assert "Hide(base, view)" in text
    assert "ExtractEdges(Input=source)" in text
    assert "Tube(Input=edges)" in text
    assert "view.CameraFocalPoint" in text
    assert "view.CameraParallelScale" in text
    assert "SaveScreenshot" in text
    compile(text, str(script), "exec")
    vtk_only = _write_checkmesh_paraview_script(
        tmp_path / "checkMesh_problem_viewer_vtk_only.py",
        None,
        [problem],
        windows_paths=False,
    )
    vtk_only_text = vtk_only.read_text(encoding="utf-8")
    assert "foam_path = None" in vtk_only_text
    compile(vtk_only_text, str(vtk_only), "exec")
    backend = (APP_DIR / "workflow_backend.py").read_text(encoding="utf-8")
    assert 'arguments = ["--disable-registry", f"--script={script_argument}"]' in backend
    assert 'dist_packages = "/usr/lib/python3/dist-packages"' in backend


def test_gmsh_worker_version_when_module_is_available() -> None:
    if importlib.util.find_spec("gmsh") is None:
        pytest.skip("gmsh Python API is installed by the WSL application bootstrap")
    completed = subprocess.run(
        [sys.executable, str(APP_DIR / "gmsh_python_runner.py"), "--version-only"],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip()


def test_mesh_optimizer_is_bounded_and_never_requests_solver() -> None:
    command = mesh_optimizer_command(
        ROOT,
        variant="open_ramair",
        domain="ross_cgrid_like",
        mesh_level="debug",
        iterations=3,
        vary_first_cell=True,
        gmsh_backend="python_api",
        gmsh_timeout_s=900,
        openfoam_timeout_s=600,
        threads=8,
        previous_output_action="delete",
    )
    assert Path(command[1]).name == "ramair_2d_mesh_optimizer.py"
    assert command[command.index("--iterations") + 1] == "3"
    assert "--vary-first-cell" in command
    assert not any("solver" in token.lower() for token in command)

    scripts = ROOT / "CFD_2D/scripts"
    sys.path.insert(0, str(scripts))
    from ramair_2d_mesh_optimizer import candidate_configurations, quality_score

    base = {
        "open_surface_target_nodes": 800,
        "open_wall_end_bump_strength": 0.72,
        "open_te_transfinite_min_nodes": 25,
        "open_use_yplus_first_cell_height": True,
        "open_first_cell_height_m": 2.0e-5,
    }
    candidates = candidate_configurations(base, "open_ramair", 3, True)
    assert len(candidates) == 3
    assert candidates[0]["open_surface_target_nodes"] == 800
    assert candidates[0]["mesh_optimizer_candidate"]["first_cell_variation_skipped_due_to_yplus"] is True
    assert candidates[2]["open_te_transfinite_min_nodes"] < candidates[0]["open_te_transfinite_min_nodes"]

    good_score, _ = quality_score({
        "mesh_file_created": True,
        "gmsh_exit_code": 0,
        "checkMesh_status": "OK",
        "checkMesh_cell_count": 10000,
        "checkMesh_max_non_orthogonality_deg": 50,
        "checkMesh_max_skewness": 2,
    })
    missing_score, _ = quality_score({})
    assert good_score < missing_score


def test_pyfoam_worker_records_real_log_and_process_return_code() -> None:
    text = (APP_DIR / "pyfoam_solver_runner.py").read_text(encoding="utf-8")
    assert "runner.logName()" in text
    assert 'getattr(runner.run, "getReturnCode", None)' in text
