from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
APP = ROOT / "CFD_2D/app"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(APP))

from project_layout import LAYOUT, canonicalize_project_relative, find_project_root, project_path  # noqa: E402
from mesh_configuration import apply_mesh_level, domain_parameters  # noqa: E402
from ramair_case_library import (  # noqa: E402
    activate_workspace_configuration,
    approve_stage_package,
    copy_item,
    create_case,
    list_cases,
    migrate_work_case_library,
    restore_stage,
    restore_workspace,
    save_stage,
)
from workflow_backend import (  # noqa: E402
    case_library_command,
    load_config,
    save_config,
    saved_mesh_catalog,
    saved_mesh_configuration,
    set_workcase_selection,
    touch_application_heartbeat,
)


def write(path: Path, text: str = "fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "DESIGN APP"
    write(root / "preprocess_ramair_main.py")
    write(root / "CFD_2D/app/ramair_cfd2d_app.py")
    profile = root / "Airfoil Profiles/reference.dat"
    write(profile, "profile")
    config = {
        "profile_inputs": {"main_profile": "Airfoil Profiles/reference.dat"},
    }
    write(
        root / "Application Support/Configurations/default_case_config.json",
        json.dumps(config),
    )
    write(root / "Application Support/Configurations/ramair_catia_system_config.json", "{}")
    write(root / "CATIA/Inputs/ramair_global_inputs.csv", "parameter,value,unit\n")
    write(root / "CFD_2D/CFD_2D_inputs/geometry/reference_uncut/profile_manifest.json", "{}")
    write(root / "CFD_2D/CFD_2D_inputs/case_package/reference_uncut/manifest.json", "{}")
    write(
        root / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json",
        json.dumps(
            {
                "case_conditions": {
                    "reynolds": 4_000_000,
                    "mach": 0.1,
                    "rho_kg_m3": 1.225,
                    "mu_pa_s": 1.81e-5,
                }
            }
        ),
    )
    write(
        root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json",
        json.dumps({"preset_id": "test_solver", "steady_max_iterations": 10000}),
    )
    write(root / "CFD_2D/meshes/reference_uncut/mesh_final.msh")
    write(root / "CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000/system/controlDict")
    write(root / "CFD_2D/results/reference_uncut/alpha_p4p000/case_summary.json", "{}")
    return root


def test_canonical_layout_has_no_nested_cfd_under_catia() -> None:
    assert LAYOUT["profiles"] == Path("Airfoil Profiles")
    assert LAYOUT["catia_inputs"] == Path("CATIA/Inputs")
    assert LAYOUT["results_library"] == Path("Results")
    assert not str(LAYOUT["cfd2d"]).startswith(str(LAYOUT["catia_inputs"]))
    assert canonicalize_project_relative("profiles/a.dat") == Path("Airfoil Profiles/a.dat")


def test_find_project_root_accepts_design_app_name(project: Path) -> None:
    assert find_project_root(project / "CFD_2D/app") == project.resolve()


def test_loading_legacy_config_migrates_only_layout_paths(project: Path) -> None:
    path = project / "Application Support/Configurations/default_case_config.json"
    path.write_text(
        json.dumps(
            {
                "project_paths": {"profiles_dir": "profiles", "catia_inputs_dir": "CATIA_inputs"},
                "profile_inputs": {"main_profile": "profiles/reference.dat"},
                "canopy_geometry": {"chord_mm": 3016.0},
            }
        ),
        encoding="utf-8",
    )

    migrated = load_config(project, "project")

    assert migrated["project_paths"]["profiles_dir"] == "Airfoil Profiles"
    assert migrated["project_paths"]["catia_inputs_dir"] == "CATIA/Inputs"
    assert migrated["profile_inputs"]["main_profile"] == "Airfoil Profiles/reference.dat"
    assert migrated["canopy_geometry"]["chord_mm"] == 3016.0
    assert any((project / "Previous Versions/Config Backups").rglob("default_case_config.json"))


@pytest.mark.parametrize("stage", ["geometry", "case", "mesh", "solver", "simulation", "postprocess"])
def test_results_library_saves_and_restores_each_stage(project: Path, stage: str) -> None:
    result = save_stage(
        project,
        stage,
        "reference_uncut_design",
        "reference_uncut",
        4.0,
        "Reusable test case",
        "archive",
    )
    assert result["status"] == "SAVED"
    manifest = project_path(project, "results_library", "reference_uncut_design", "case_manifest.json")
    assert manifest.is_file()
    assert stage in json.loads(manifest.read_text(encoding="utf-8"))["stages"]
    restored = restore_stage(project, stage, "reference_uncut_design", None, None, "archive")
    assert restored["status"] == "RESTORED"
    assert restored["restored"]
    active_workspace = project / "CFD_2D/app_state/active_workspace.json"
    assert restored["active_workspace"] == str(active_workspace)
    active = json.loads(active_workspace.read_text(encoding="utf-8"))
    assert active["case"] == "reference_uncut_design"
    assert active["stage"] == stage
    assert active["variant"] == "reference_uncut"


def test_solver_package_restores_editable_configuration(project: Path) -> None:
    solver = project / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json"
    save_stage(
        project,
        "solver",
        "solver_library",
        "reference_uncut",
        4.0,
        "Solver snapshot",
        "archive",
        "validated_v1",
    )
    solver.write_text(json.dumps({"preset_id": "modified"}), encoding="utf-8")
    restore_stage(
        project,
        "solver",
        "solver_library",
        None,
        None,
        "archive",
        "validated_v1",
    )
    restored = json.loads(solver.read_text(encoding="utf-8"))
    assert restored["preset_id"] == "test_solver"
    assert restored["steady_max_iterations"] == 10000


def test_results_case_manifest_is_discoverable(project: Path) -> None:
    save_stage(project, "case", "saved_case", "reference_uncut", 4.0, "Case", "archive")
    cases = list_cases(project)
    assert [item["folder"] for item in cases] == ["saved_case"]
    assert cases[0]["reynolds"] == 4_000_000


def test_legacy_manifest_adapter_is_read_only_until_explicit_migration(
    project: Path,
) -> None:
    case_root = project / "Results/legacy_case"
    write(case_root / "Operating Case/Case Package/manifest.json", "{}")
    manifest_path = case_root / "case_manifest.json"
    original = {
        "schema_version": 1,
        "case_name": "legacy_case",
        "created_at": "2025-01-02 03:04:05",
        "variant": "reference_uncut",
        "alpha_deg": 4.0,
        "stages": {
            "case": {
                "folder": "Operating Case",
                "saved_at": "2025-01-02 03:04:05",
                "file_count": 1,
                "size_bytes": 2,
                "variant": "reference_uncut",
                "alpha_deg": 4.0,
            }
        },
    }
    write(manifest_path, json.dumps(original))
    before = manifest_path.read_bytes()

    adapted = list_cases(project)[0]
    dry_run = migrate_work_case_library(project, dry_run=True)

    assert adapted["schema_version"] == 3
    assert adapted["work_case_id"]
    assert adapted["stages"]["case"]["packages"]["legacy"]["entity_id"]
    assert manifest_path.read_bytes() == before
    assert dry_run["cases"][0]["written"] is False
    assert not (project / "Results/work_case_index.json").exists()

    applied = migrate_work_case_library(project, dry_run=False)
    migrated = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert applied["status"] == "MIGRATED"
    assert migrated["schema_version"] == 3
    assert migrated["migration"]["metadata_only"] is True
    assert (project / "Results/work_case_index.json").is_file()
    assert any(
        (project / "Previous Versions/Results Library Manifest Backups/legacy_case").glob(
            "*_case_manifest_schema1.json"
        )
    )


def test_revision_approval_is_preserved_when_package_is_edited(project: Path) -> None:
    for stage in ("geometry", "case", "mesh"):
        save_stage(
            project,
            stage,
            "revision_case",
            "reference_uncut",
            4.0,
            "Revision lifecycle",
            "archive",
            "baseline",
        )
    decision = approve_stage_package(
        project,
        "revision_case",
        "mesh",
        "baseline",
        "approved",
        actor="qa-user",
        evidence={"checkMesh": "passed"},
    )
    old_revision = decision["approval"]["revision_id"]
    manifest_path = project / "Results/revision_case/case_manifest.json"
    old_entity = json.loads(manifest_path.read_text(encoding="utf-8"))[
        "stages"
    ]["mesh"]["packages"]["baseline"]["entity_id"]
    write(project / "CFD_2D/meshes/reference_uncut/mesh_quality_report.json", '{"ok": true}')

    save_stage(
        project,
        "mesh",
        "revision_case",
        "reference_uncut",
        4.0,
        "Edited revision",
        "archive",
        "baseline",
    )
    current = json.loads(manifest_path.read_text(encoding="utf-8"))[
        "stages"
    ]["mesh"]["packages"]["baseline"]

    assert current["entity_id"] == old_entity
    assert current["revision_id"] != old_revision
    assert current["approval"]["status"] == "pending"
    assert current["approval"]["revision_id"] == current["revision_id"]
    assert current["revision_history"][-1]["revision_id"] == old_revision
    assert current["revision_history"][-1]["approval"]["status"] == "approved"
    assert current["revision_history"][-1]["archived_path"]


def test_saved_mesh_catalog_loads_exact_config_and_persistent_approval(project: Path) -> None:
    mesh_config = {"config_schema_version": 8, "gmsh_mesh_algorithm_2d": 6, "marker": "saved"}
    write(
        project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json",
        json.dumps(mesh_config),
    )
    write(
        project / "CFD_2D/meshes/reference_uncut/mesh_quality_report.json",
        json.dumps({"checkMesh_status": "PASS"}),
    )
    for stage in ("geometry", "case", "mesh"):
        save_stage(project, stage, "mesh_catalog_case", "reference_uncut", 4.0, "catalog", "archive", "baseline")
    approve_stage_package(
        project, "mesh_catalog_case", "mesh", "baseline", "approved",
        actor="mesh-reviewer", evidence="checkMesh PASS",
    )

    catalog = saved_mesh_catalog(project, "mesh_catalog_case")
    assert len(catalog) == 1
    assert catalog[0]["compatible"] is True
    assert catalog[0]["approval"]["status"] == "approved"
    assert catalog[0]["checkMesh_status"] == "PASS"
    assert saved_mesh_configuration(project, "mesh_catalog_case", "baseline")["marker"] == "saved"


def test_mesh_draft_save_does_not_mutate_saved_revision(project: Path) -> None:
    write(
        project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json",
        json.dumps({"marker": "original"}),
    )
    for stage in ("geometry", "case", "mesh"):
        save_stage(project, stage, "mesh_draft_case", "reference_uncut", 4.0, "draft", "archive", "baseline")
    set_workcase_selection(project, "mesh_draft_case")
    manifest_path = project / "Results/mesh_draft_case/case_manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))["stages"]["mesh"]["packages"]["baseline"]["revision_id"]

    save_config(project, "mesh", {"marker": "draft"}, sync_workcase=False)

    after = json.loads(manifest_path.read_text(encoding="utf-8"))["stages"]["mesh"]["packages"]["baseline"]["revision_id"]
    assert after == before
    assert load_config(project, "mesh")["marker"] == "draft"


def test_case_library_approval_command_contains_revision_decision(project: Path) -> None:
    command = case_library_command(
        project, "approve", stage="mesh", case_name="case", package_name="fine",
        approval_status="approved", actor="reviewer", evidence="quality PASS",
    )
    assert command[-12:] == [
        "--case-name", "case", "--stage", "mesh", "--package-name", "fine",
        "--status", "approved", "--actor", "reviewer",
        "--evidence", "quality PASS",
    ]


def test_upstream_revision_change_marks_dependents_stale_and_blocks_restore(
    project: Path,
) -> None:
    for stage in ("geometry", "case", "mesh"):
        save_stage(
            project,
            stage,
            "dependency_case",
            "reference_uncut",
            4.0,
            "Dependency graph",
            "archive",
            "baseline",
        )
    write(
        project / "CFD_2D/CFD_2D_inputs/geometry/reference_uncut/new_revision.json",
        '{"revision": 2}',
    )
    save_stage(
        project,
        "geometry",
        "dependency_case",
        "reference_uncut",
        4.0,
        "Geometry changed",
        "archive",
        "baseline",
    )

    manifest = json.loads(
        (project / "Results/dependency_case/case_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    case_info = manifest["stages"]["case"]["packages"]["baseline"]
    mesh_info = manifest["stages"]["mesh"]["packages"]["baseline"]
    assert case_info["compatibility"]["status"] == "stale"
    assert mesh_info["compatibility"]["status"] == "stale"
    assert "dependency_revision_changed:geometry" in case_info["compatibility"][
        "warnings"
    ]
    with pytest.raises(ValueError, match="stale case package"):
        restore_workspace(project, "dependency_case", "archive")


def test_complete_workspace_restore_loads_geometry_case_mesh_and_solver(project: Path) -> None:
    static_preset = project / "CFD_2D/CFD_2D_inputs/config/mesh_presets/reference.json"
    write(static_preset, json.dumps({"source": "current"}))
    for stage in ("geometry", "case", "mesh", "solver"):
        save_stage(
            project,
            stage,
            "complete_case",
            "reference_uncut",
            4.0,
            "Complete workspace",
            "archive",
            f"{stage}_package",
        )
    legacy_preset = (
        project
        / "Results/complete_case/CFD Cases/case_package/"
        "CFD Configurations/mesh_presets/reference.json"
    )
    write(legacy_preset, json.dumps({"source": "stale-package"}))
    write(project / "CFD_2D/meshes/reference_uncut/obsolete.txt", "active-only")
    packaged_workflow = (
        project
        / "Results/complete_case/Meshes/mesh_package/Configurations/"
        "cfd2d_workflow_config.json"
    )
    stale_workflow = json.loads(packaged_workflow.read_text(encoding="utf-8"))
    stale_workflow["geometry"] = {"variant": "stale_variant"}
    packaged_workflow.write_text(json.dumps(stale_workflow), encoding="utf-8")

    result = restore_workspace(project, "complete_case", "delete")

    assert result["status"] == "WORKSPACE_RESTORED"
    assert set(result["packages"]) == {"geometry", "case", "mesh", "solver"}
    assert not (project / "CFD_2D/meshes/reference_uncut/obsolete.txt").exists()
    active = json.loads((project / "CFD_2D/app_state/active_workspace.json").read_text(encoding="utf-8"))
    assert active["stage"] == "workspace"
    assert active["case"] == "complete_case"
    assert active["packages"]["mesh"] == "mesh_package"
    assert active["packages"]["solver"] == "solver_package"
    assert json.loads(static_preset.read_text(encoding="utf-8"))["source"] == "current"
    restored_workflow = json.loads(
        (
            project / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"
        ).read_text(encoding="utf-8")
    )
    assert restored_workflow["geometry"]["variant"] == "reference_uncut"


def test_workspace_configuration_switches_geometry_case_and_mesh_atomically(
    project: Path,
) -> None:
    for stage in ("geometry", "case", "mesh"):
        save_stage(
            project,
            stage,
            "convergence_study",
            "reference_uncut",
            4.0,
            "Closed medium",
            "archive",
            "closed_medium",
        )
    for collection in ("geometry", "case_package"):
        source = project / "CFD_2D/CFD_2D_inputs" / collection / "reference_uncut"
        target = project / "CFD_2D/CFD_2D_inputs" / collection / "open_ramair"
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if item.is_file():
                write(target / item.name, item.read_text(encoding="utf-8"))
    write(project / "CFD_2D/meshes/open_ramair/mesh_final.msh", "open mesh")
    for stage in ("geometry", "case", "mesh"):
        save_stage(
            project,
            stage,
            "convergence_study",
            "open_ramair",
            4.0,
            "Open medium",
            "archive",
            "open_medium",
        )

    result = activate_workspace_configuration(
        project,
        "convergence_study",
        "open_medium",
        "delete",
    )

    assert result["status"] == "WORKSPACE_RESTORED"
    assert result["configuration"] == "open_medium"
    assert result["variant"] == "open_ramair"
    assert result["packages"]["geometry"] == "open_medium"
    assert result["packages"]["case"] == "open_medium"
    assert result["packages"]["mesh"] == "open_medium"
    manifest = json.loads(
        (
            project / "Results/convergence_study/case_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["variant"] == "open_ramair"
    assert {
        manifest["stages"][stage]["active_package"]
        for stage in ("geometry", "case", "mesh")
    } == {"open_medium"}


def test_working_case_supports_multiple_named_mesh_packages(project: Path) -> None:
    create_case(project, "design_study", "reference_uncut", 4.0, "Mesh comparison")
    standard_solver = (
        project
        / "Results/design_study/Solver Configurations/topology_solver_v11/"
        "Configurations/cfd2d_solver_config.json"
    )
    assert json.loads(standard_solver.read_text(encoding="utf-8"))["preset_id"] == "test_solver"
    mesh_cfg = project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    write(mesh_cfg, json.dumps({"closed_boundary_layer_layers": 20, "domain_type": "debug_20c"}))
    save_stage(project, "mesh", "design_study", "reference_uncut", 4.0, "Coarse", "archive", "coarse_debug")
    write(mesh_cfg, json.dumps({"closed_boundary_layer_layers": 50, "domain_type": "circular_50c"}))
    save_stage(project, "mesh", "design_study", "reference_uncut", 4.0, "Fine", "archive", "fine_50c")

    manifest = json.loads((project / "Results/design_study/case_manifest.json").read_text(encoding="utf-8"))
    packages = manifest["stages"]["mesh"]["packages"]
    assert set(packages) == {"coarse_debug", "fine_50c"}
    assert (project / "Results/design_study/Meshes/coarse_debug/Mesh Data/mesh_final.msh").is_file()
    assert (project / "Results/design_study/Meshes/fine_50c/Mesh Data/mesh_final.msh").is_file()

    with pytest.raises(FileExistsError):
        save_stage(
            project,
            "mesh",
            "design_study",
            "reference_uncut",
            4.0,
            "Must confirm replacement",
            "keep",
            "coarse_debug",
        )
    original_saved = json.loads(
        (
            project
            / "Results/design_study/Meshes/coarse_debug/Configurations/"
            "cfd2d_mesh_config.json"
        ).read_text(encoding="utf-8")
    )
    assert original_saved["closed_boundary_layer_layers"] == 20

    restore_stage(project, "mesh", "design_study", None, None, "archive", "coarse_debug")
    restored = json.loads(mesh_cfg.read_text(encoding="utf-8"))
    assert restored["closed_boundary_layer_layers"] == 20
    active = json.loads((project / "CFD_2D/app_state/active_workspace.json").read_text(encoding="utf-8"))
    assert active["package"] == "coarse_debug"


def test_saved_config_updates_active_workcase_package(project: Path) -> None:
    create_case(project, "active_defaults", "reference_uncut", 4.0, "")
    set_workcase_selection(project, "active_defaults")
    approved = approve_stage_package(
        project,
        "active_defaults",
        "solver",
        "topology_solver_v11",
        "approved",
        evidence="reviewed defaults",
    )
    save_config(
        project,
        "solver",
        {"preset_id": "edited_in_app", "maxCo": 0.8, "steady_max_iterations": 10000},
    )

    package = (
        project
        / "Results/active_defaults/Solver Configurations/topology_solver_v11/"
        "Configurations/cfd2d_solver_config.json"
    )
    persisted = json.loads(package.read_text(encoding="utf-8"))
    manifest = json.loads(
        (project / "Results/active_defaults/case_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["preset_id"] == "edited_in_app"
    assert persisted["maxCo"] == 0.8
    assert (
        manifest["stages"]["solver"]["packages"]["topology_solver_v11"][
            "configuration_updated_in_app"
        ]
        is True
    )
    revision = manifest["stages"]["solver"]["packages"]["topology_solver_v11"]
    assert revision["revision_id"] != approved["approval"]["revision_id"]
    assert revision["approval"]["status"] == "pending"
    assert revision["revision_history"][-1]["approval"]["status"] == "approved"
    archived_path = Path(revision["revision_history"][-1]["archived_path"])
    assert (archived_path / "Configurations/cfd2d_solver_config.json").is_file()

    second_approval = approve_stage_package(
        project,
        "active_defaults",
        "solver",
        "topology_solver_v11",
        "approved",
        evidence="reviewed edit",
    )
    save_config(
        project,
        "solver",
        {"preset_id": "edited_in_app", "maxCo": 0.8, "steady_max_iterations": 10000},
    )
    unchanged = json.loads(
        (project / "Results/active_defaults/case_manifest.json").read_text(
            encoding="utf-8"
        )
    )["stages"]["solver"]["packages"]["topology_solver_v11"]
    assert unchanged["revision_id"] == second_approval["approval"]["revision_id"]
    assert unchanged["approval"]["status"] == "approved"


def test_temporary_workspace_does_not_mutate_last_loaded_workcase(project: Path) -> None:
    create_case(project, "protected_case", "reference_uncut", 4.0, "")
    package = (
        project
        / "Results/protected_case/Solver Configurations/topology_solver_v11/"
        "Configurations/cfd2d_solver_config.json"
    )
    before = package.read_bytes()
    set_workcase_selection(project, None)

    save_config(
        project,
        "solver",
        {"preset_id": "temporary_only", "maxCo": 0.4},
    )

    assert package.read_bytes() == before


def test_results_geometry_uses_variant_profile_not_current_ui_profile(project: Path) -> None:
    active = project / "Airfoil Profiles/active_open.csv"
    reference = project / "Airfoil Profiles/reference_base.dat"
    write(active, "0,0\n1,0\n")
    write(reference, "0 0\n1 0\n")
    write(
        project / "Application Support/Configurations/default_case_config.json",
        json.dumps({"profile_inputs": {"main_profile": "Airfoil Profiles/active_open.csv"}}),
    )
    write(
        project / "CFD_2D/CFD_2D_inputs/case_package/reference_uncut/manifest.json",
        json.dumps({"variant": "reference_uncut", "source": "/old/device/Airfoil Profiles/reference_base.dat"}),
    )
    create_case(project, "reference_case", "reference_uncut", 4.0, "")
    manifest = json.loads((project / "Results/reference_case/case_manifest.json").read_text(encoding="utf-8"))
    assert manifest["main_profile"] == "Airfoil Profiles/reference_base.dat"
    save_stage(
        project,
        "geometry",
        "reference_case",
        "reference_uncut",
        4.0,
        "",
        "archive",
        "reference_geometry",
    )
    package = project / "Results/reference_case/Geometry Packages/reference_geometry/Airfoil Profile"
    assert (package / "reference_base.dat").is_file()
    assert not (package / "active_open.csv").exists()


def test_mesh_levels_share_geometry_but_change_resolution() -> None:
    coarse = apply_mesh_level({"domain_type": "rectangular_balaji"}, "coarse")
    medium = apply_mesh_level({"domain_type": "rectangular_balaji"}, "medium")
    fine = apply_mesh_level({"domain_type": "rectangular_balaji"}, "fine")
    for key in (
        "closed_profile_target_points",
        "closed_te_rounding_points",
        "closed_wall_target_nodes",
        "closed_boundary_layer_growth",
        "open_surface_target_nodes",
        "open_lip_transfinite_min_nodes",
    ):
        assert coarse[key] == medium[key] == fine[key]
    assert [coarse["closed_boundary_layer_layers"], medium["closed_boundary_layer_layers"], fine["closed_boundary_layer_layers"]] == [50, 50, 50]
    assert [coarse["target_y_plus"], medium["target_y_plus"], fine["target_y_plus"]] == pytest.approx([1.0, 2.0 / 3.0, 4.0 / 9.0])
    assert coarse["closed_farfield_size_chord"] > fine["closed_farfield_size_chord"]
    assert fine["domain_type"] == "rectangular_balaji"


def test_domain_dimensions_come_only_from_selected_domain_keys() -> None:
    config = {
        "domain_circular_radius_chord": 42.0,
        "domain_debug_radius_chord": 8.0,
        "domain_rectangular_upstream_chord": 6.0,
        "domain_rectangular_downstream_chord": 14.0,
        "domain_rectangular_top_chord": 7.0,
        "domain_rectangular_bottom_chord": 8.0,
    }
    assert domain_parameters("circular_50c", config)["radius"] == 42.0
    assert domain_parameters("debug_20c", config)["radius"] == 8.0
    rectangular = domain_parameters("rectangular_balaji", config)
    assert rectangular == {"type": "rectangle", "upstream": 6.0, "downstream": 14.0, "top": 7.0, "bottom": 8.0}


def test_application_heartbeat_is_atomic_under_fragment_concurrency(project: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: touch_application_heartbeat(project), range(40)))
    assert len(set(paths)) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["streamlit_pid"] > 0
    assert payload["unix_time"] > 0.0
    assert not list(paths[0].parent.glob("*.tmp"))


def test_mesh_library_snapshot_uses_structured_stage_folders(project: Path) -> None:
    mesh_cfg = project / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    workflow_cfg = project / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"
    write(mesh_cfg, json.dumps({"closed_boundary_layer_layers": 50}))
    result = save_stage(project, "mesh", "structured", "reference_uncut", 4.0, "Mesh", "archive")
    assert result["status"] == "SAVED"
    saved_root = project / "Results/structured/Mesh"
    assert (saved_root / "Mesh Data/mesh_final.msh").is_file()
    assert (saved_root / "Configurations/cfd2d_mesh_config.json").is_file()
    assert (saved_root / "Configurations/cfd2d_workflow_config.json").is_file()
    mesh_cfg.write_text(json.dumps({"closed_boundary_layer_layers": 3}), encoding="utf-8")
    restore_stage(project, "mesh", "structured", None, None, "archive")
    assert json.loads(mesh_cfg.read_text(encoding="utf-8"))["closed_boundary_layer_layers"] == 50


def test_case_library_prefers_hard_links_without_changing_file_contents(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write(source / "mesh.msh", "m" * (1024 * 1024 + 1))
    copy_item(source, target)
    copied = target / "mesh.msh"
    assert copied.stat().st_size == 1024 * 1024 + 1
    assert source.joinpath("mesh.msh").stat().st_ino == copied.stat().st_ino


def test_codex_context_and_changelog_contract() -> None:
    context = (ROOT / "PROJECT_CONTEXT_FOR_CODEX.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Application backend API: 24" in context
    launcher = (ROOT / "run_ramair_cfd2d_app.py").read_text(encoding="utf-8")
    bootstrap = (ROOT / "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh").read_text(encoding="utf-8")
    assert "BACKEND_API_VERSION = 24" in launcher
    assert "expected API 24" in bootstrap
    assert "PROJECT_CONTEXT_FOR_CODEX.md" in agents
    assert "CHANGELOG.md" in agents
    assert "## [Unreleased]" in changelog
    assert "## [2026-08-18]" in changelog
    assert (ROOT / "Application Support/Tools/check_project_context.py").is_file()


def test_open_validation_workcase_assets_and_dynamic_mesh_editor_exist() -> None:
    preset = json.loads(
        (
            ROOT
            / "CFD_2D/CFD_2D_inputs/config/mesh_presets/"
            "open_ramair_validation_1m_candidate.json"
        ).read_text(encoding="utf-8")
    )
    assert preset["open_geometry_representation"] == "zero_thickness_base_profile"
    assert preset["open_zero_thickness_contour_target_nodes"] == 2800
    assert preset["domain_circular_radius_chord"] == pytest.approx(50.0)
    assert preset["open_farfield_size_chord"] == pytest.approx(3.5)
    assert preset["open_base_inlet_alignment_mode"] == "similarity"
    assert preset["open_cavity_inlet_size_strategy"] == "hybrid_boundary_extension"
    assert preset["open_cavity_inlet_extension_power"] == pytest.approx(0.75)
    assert preset["open_internal_inlet_matching_transition_chord"] == pytest.approx(0.0035)
    assert preset["open_zero_thickness_inlet_normal_y1_factor"] == pytest.approx(8.0)
    assert preset["open_use_yplus_first_cell_height"] is False
    assert preset["open_first_cell_height_m"] == pytest.approx(2.5e-5)
    assert preset["open_boundary_layer_growth"] == pytest.approx(1.075)
    assert preset["open_zero_thickness_te_transfinite_min_nodes"] == 32
    assert preset["open_te_transfinite_min_nodes"] == 40
    assert preset["gmsh_mesh_algorithm_2d"] == 6
    assert preset["gmsh_threads"] == 12
    assert preset["open_transition_sigmoid_enabled"] is True
    assert preset["open_internal_inlet_dist_max_chord"] > 0.0
    assert preset["open_inner_wall_node_factor"] == pytest.approx(0.40)
    assert preset["open_inner_te_node_factor"] == pytest.approx(0.28)
    assert (
        ROOT / "CFD_2D/scripts/ramair_2d_open_validation_workcase.py"
    ).is_file()
    app_text = (ROOT / "CFD_2D/app/ramair_cfd2d_app.py").read_text(
        encoding="utf-8"
    )
    zero_branch = app_text.index(
        'if data.get("open_geometry_representation") == "zero_thickness_base_profile":'
    )
    finite_branch = app_text.index(
        'if data.get("open_geometry_representation") != "zero_thickness_base_profile":'
    )
    assert zero_branch < finite_branch
    assert '"open_zero_thickness_contour_target_nodes"' in app_text[zero_branch:finite_branch]
    assert '"open_cavity_inlet_size_strategy"' in app_text[zero_branch:finite_branch]
    assert '"hybrid_boundary_extension"' in app_text
    assert '"open_fabric_thickness_m"' not in app_text[zero_branch:finite_branch]
    assert "zero_thickness_open_editor" in app_text
    assert "Sustituir este paquete por la malla activa" in app_text
    assert "Ese paquete de malla ya existe." in app_text


def test_solver_editor_reruns_when_time_step_policy_changes() -> None:
    app_text = (ROOT / "CFD_2D/app/ramair_cfd2d_app.py").read_text(encoding="utf-8")
    assert 'with st.form("solver-config-form")' not in app_text
    assert 'key="save-solver-configuration"' in app_text
    assert 'edited.get("time_step_mode", "adaptive_physics_limited") != "fixed"' in app_text
    assert "ungrouped = []" in app_text
    assert "permanecen ocultos porque no intervienen" in app_text
    assert '"Detener visualizacion"' in app_text


def test_mesh_page_owns_one_unique_case_library_form() -> None:
    app_text = (ROOT / "CFD_2D/app/ramair_cfd2d_app.py").read_text(
        encoding="utf-8"
    )
    mapping = app_text[app_text.index("library_stage_by_page = {") :]
    assert '"Malla": "mesh"' not in mapping
    assert app_text.count('case_library_panel(\n        "mesh",') == 1
    assert '@st.fragment(run_every="30s")\ndef solver_live_monitor_panel' in app_text


def test_live_monitor_uses_round_axes_and_throttled_snapshots() -> None:
    monitor = (ROOT / "CFD_2D/app/ramair_live_monitor.py").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "CFD_2D/app/pyfoam_solver_runner.py").read_text(
        encoding="utf-8"
    )
    assert "MultipleLocator(0.4)" in monitor
    assert "MultipleLocator(20.0)" in monitor
    assert 'default=30.0' in monitor
    assert '"--snapshot-s", "30"' in runner
