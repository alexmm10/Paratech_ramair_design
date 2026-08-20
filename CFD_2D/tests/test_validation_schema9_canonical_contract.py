from __future__ import annotations

import json
import sys
import warnings
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
APP = ROOT / "CFD_2D/app"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(APP))

import ramair_2d_urans_matrix_manager as queue_manager  # noqa: E402
from ramair_2d_execution_registry import (  # noqa: E402
    execution_title,
    load_registry,
    upsert_execution,
)
from ramair_2d_rans_paraview_final import (  # noqa: E402
    prepare_final_state,
    resolve_final_vtk_artifacts,
)
from ramair_2d_openfoam_runner import write_run_script  # noqa: E402
from ramair_2d_study_registry import (  # noqa: E402
    STUDY_CONFIG_SCHEMA_VERSION,
    active_workspace_root,
    build_run_matrix,
    default_study_config,
    migrate_study_config,
    write_json_atomic,
)
from ramair_2d_urans_cases import (  # noqa: E402
    CanonicalCaseError,
    canonical_case_id,
    canonical_case_root,
    inspect_canonical_case,
    restart_canonical_case,
    restart_time_evidence,
    complete_time_history,
    write_case_manifest,
)
from ramair_2d_urans_review import review_run  # noqa: E402
from ramair_2d_validation_report import _rans_scalar_change_records  # noqa: E402
from ramair_2d_validation_schema9_migration import apply, preview  # noqa: E402
from ramair_2d_validation_staged_runner import (  # noqa: E402
    _history_evidence,
    _journal,
    _expected_phase_start_time,
    _phase_boundary_write_interval,
    _persist_classification_repair,
    configure_stage,
    repair_legacy_classification,
)
from ramair_2d_urans_transition_verification import _configure_urans_dictionaries  # noqa: E402
from ramair_scientific_plot_style import save_scientific_figure  # noqa: E402
from wall_separation_analysis import _ordered_cp_branches  # noqa: E402
from openfoam_wall_analysis import _load_case_definition_json  # noqa: E402
from workflow_backend import BACKEND_API_VERSION  # noqa: E402


def row(level: str = "coarse", dt: float = 2.5e-4) -> dict[str, object]:
    return {
        "topology": "closed", "mesh_level": level, "mesh_id": f"closed_{level}",
        "alpha_deg": 8.0, "dt_s": dt,
    }


def write_time(case: Path, value: float, fields=("U", "p", "nuTilda")) -> None:
    target = case / f"{value:g}"
    target.mkdir(parents=True, exist_ok=True)
    for field in fields:
        (target / field).write_text("field\n", encoding="utf-8")


def write_exact_time(
    case: Path,
    folder: str,
    exact: str,
    index: int,
    fields=("U", "p", "nuTilda"),
) -> None:
    target = case / folder
    (target / "uniform").mkdir(parents=True, exist_ok=True)
    for field in fields:
        (target / field).write_text(f"{field}-preserved\n", encoding="utf-8")
    (target / "uniform/time").write_text(
        f"value {exact};\nindex {index};\ndeltaT 6.25e-05;\n",
        encoding="utf-8",
    )


def mesh_registry() -> dict[str, object]:
    meshes = []
    for topology in ("closed", "open"):
        for level, cells in (("coarse", 100), ("medium", 200), ("fine", 400)):
            meshes.append({
                "id": f"{topology}_{level}", "topology": topology, "level": level,
                "mesh_package": f"mesh/{topology}_{level}", "mesh_hash": f"hash-{topology}-{level}",
                "cell_count": cells,
            })
    return {"meshes": meshes, "warnings": []}


def test_api_schema_and_migration_remove_legacy_policy() -> None:
    migrated = migrate_study_config({
        "schema_version": 8,
        "validation_study": {"urans": {
            "pilot_policy": "required", "attempts": [1], "retention": {}, "archive": True,
        }},
    })
    assert BACKEND_API_VERSION == 26
    assert STUDY_CONFIG_SCHEMA_VERSION == 11
    urans = migrated["validation_study"]["urans"]
    assert not ({"pilot_policy", "attempts", "retention", "archive"} & set(urans))


def test_legacy_sigfpe_false_positive_is_repaired_without_solver_run(tmp_path: Path) -> None:
    run_root = tmp_path / "closed_coarse_a08_dt2p5em04"
    log = run_root / "logs/phase_A_001.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE)\n"
        "Time = 0.0015625\nEnd\n",
        encoding="utf-8",
    )
    write_json_atomic(run_root / "stage_journal.json", {
        "schema_version": 1,
        "case_id": run_root.name,
        "phases": [{
            "phase": "A",
            "returncode": 0,
            "terminal_reason": "NUMERICAL_DIVERGENCE",
            "primary_error": "RUN_COMPLETED",
            "log_path": str(log),
            "output_checkpoint": {"valid": True, "time_s": 0.0015625},
        }],
    })
    manifest = {
        "case_id": run_root.name,
        "execution_outcome": "DIVERGED",
        "restartable": False,
        "current_phase": "A",
        "terminal_reason": "NUMERICAL_DIVERGENCE",
        "primary_error": "RUN_COMPLETED",
    }
    write_json_atomic(run_root / "case_manifest.json", manifest)
    journal = _journal(run_root, run_root.name)
    assert journal["phases"][0]["terminal_reason"] == "PHASE_TARGET_REACHED"
    repaired = _persist_classification_repair(
        run_root,
        manifest,
        journal,
        [{"stage": "A"}, {"stage": "B"}],
        1,
        {"valid": True, "time_s": 0.0015625},
    )
    assert repaired["execution_outcome"] == "PAUSED"
    assert repaired["restartable"] is True
    assert repaired["current_phase"] == "B"
    report = json.loads((run_root / "classification_correction.json").read_text())
    assert report["status"] == "RECLASSIFIED_WITHOUT_SOLVER_EXECUTION"

    # The public entry point used by the normal execute service performs the
    # same repair before its action gate rejects a legacy DIVERGED manifest.
    write_time(run_root / "case", 0.0015625)
    write_json_atomic(run_root / "stage_plan.json", {
        "stages": [
            {"stage": "A", "start_s": 0.0, "end_s": 0.0015625},
            {"stage": "B", "start_s": 0.0015625, "end_s": 0.0046875},
        ]
    })
    manifest.update(
        execution_outcome="DIVERGED",
        restartable=False,
        current_phase="A",
        terminal_reason="NUMERICAL_DIVERGENCE",
        primary_error="RUN_COMPLETED",
    )
    manifest.pop("classification_correction", None)
    write_json_atomic(run_root / "case_manifest.json", manifest)
    automatic = repair_legacy_classification(run_root)
    assert automatic["execution_outcome"] == "PAUSED"
    assert automatic["current_phase"] == "B"


def test_rounded_time_directories_use_uniform_time_and_recover_phase_e(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "closed_fine_a08_dt6p25em05"
    case = run_root / "case"
    write_exact_time(case, "0.396047", "0.39604688000002819", 6337)
    write_exact_time(case, "0.396109", "0.396109380000028211", 6338)
    write_exact_time(case, "0.396172", "0.396171880000028231", 6339)
    before = {
        str(path.relative_to(case)): path.read_bytes()
        for path in case.rglob("*") if path.is_file()
    }
    phases = [
        {"stage": "A", "scheme": "Euler", "dt_s": 1.5625e-5},
        {"stage": "B", "scheme": "Euler", "dt_s": 3.125e-5},
        {"stage": "C", "scheme": "Euler", "dt_s": 6.25e-5},
        {"stage": "D", "scheme": "backward", "dt_s": 6.25e-5},
        {"stage": "E", "scheme": "backward", "dt_s": 6.25e-5},
    ]
    write_json_atomic(run_root / "stage_plan.json", {"stages": phases})
    write_json_atomic(run_root / "stage_journal.json", {
        "schema_version": 2,
        "phases": [
            {
                "phase": row["stage"],
                "terminal_reason": "PHASE_TARGET_REACHED",
                "returncode": 0,
                "output_checkpoint": {"valid": True},
            }
            for row in phases[:4]
        ],
    })
    write_json_atomic(run_root / "case_manifest.json", {
        "case_id": run_root.name,
        "startup_mode": "progressive",
        "execution_outcome": "ERROR",
        "restartable": True,
        "current_phase": "D",
        "terminal_reason": "ORCHESTRATION_ERROR",
        "primary_error": (
            "RuntimeError: TEMPORAL_HISTORY_MISSING: backward requires "
            "the current state and two previous states"
        ),
    })

    history = complete_time_history(case)
    assert history["times_s"] == pytest.approx([
        0.39604688000002819,
        0.396109380000028211,
        0.396171880000028231,
    ])
    assert _history_evidence(case, 6.25e-5)["valid"] is True
    repaired = repair_legacy_classification(run_root)
    assert repaired["execution_outcome"] == "PAUSED"
    assert repaired["current_phase"] == "E"
    assert repaired["terminal_reason"] == "TEMPORAL_HISTORY_RECOVERED"
    assert (case / ".ramair_execution_state.json").is_file()
    after = {
        str(path.relative_to(case)): path.read_bytes()
        for path in case.rglob("*")
        if path.is_file() and path.name != ".ramair_execution_state.json"
    }
    assert after == before


def test_urans_review_finds_existing_raw_csv_layout(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    source = run_root / "postprocess/URANS/forceCoeffs_raw.csv"
    source.parent.mkdir(parents=True)
    rows = ["Time,Cl,Cd,Cm"]
    for index in range(128):
        time_s = index * 0.01
        rows.append(f"{time_s},{1 + 0.01 * (index % 4)},0.1,-0.05")
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    write_json_atomic(run_root / "case_metadata.json", {
        "sampling_start_s": 0.0,
        "sampling_end_s": 1.27,
        "operating_condition": {"chord_m": 1.0, "velocity_m_s": 10.0},
    })
    report = review_run(run_root)
    assert report["source_csv"] == str(source)
    assert (run_root / "review.json").is_file()


def test_phase_commands_use_each_planned_boundary_not_the_initial_restart_time() -> None:
    assert _expected_phase_start_time(
        {"stage": "B", "start_s": 0.0015625}, "CONTINUE_STAGE", 0.0015625
    ) == pytest.approx(0.0015625)
    assert _expected_phase_start_time(
        {"stage": "C", "start_s": 0.0046875}, "CONTINUE_STAGE", 0.0015625
    ) == pytest.approx(0.0046875)
    assert _expected_phase_start_time(
        {"stage": "C", "start_s": 0.0046875}, "RESUME_EXISTING", 0.006
    ) == pytest.approx(0.006)


def test_case_id_is_precise_deterministic_and_has_one_path(tmp_path: Path) -> None:
    first = canonical_case_id("closed", "coarse", 8.0, 3.125e-5)
    second = canonical_case_id("closed", "coarse", 8.0, 3.126e-5)
    assert first != second
    assert canonical_case_root(tmp_path, row(dt=3.125e-5)).name == first
    assert "attempt" not in canonical_case_root(tmp_path, row()).as_posix().lower()


def test_presence_requires_positive_complete_time_and_solver_evidence(tmp_path: Path) -> None:
    root = canonical_case_root(tmp_path, row())
    case = root / "case"
    write_case_manifest(root, row(), hashes={}, effective_solver_config={}, startup_mode="progressive")
    write_time(case, 0.1)
    assert inspect_canonical_case(tmp_path, row())["case_presence"] == "NOT_STARTED"
    (case / "log.foamRun").write_text("Time = 0.1\n", encoding="utf-8")
    assert inspect_canonical_case(tmp_path, row())["case_presence"] == "STARTED"


def test_restart_rejects_wrong_confirmation_and_deletes_only_exact_case(tmp_path: Path) -> None:
    root = canonical_case_root(tmp_path, row())
    (root / "case/system").mkdir(parents=True)
    preserved = active_workspace_root(tmp_path) / "checkpoints/closed_coarse/keep"
    preserved.parent.mkdir(parents=True)
    preserved.write_text("keep", encoding="utf-8")
    with pytest.raises(CanonicalCaseError):
        restart_canonical_case(tmp_path, row(), confirm_delete="wrong")
    report = restart_canonical_case(tmp_path, row(), confirm_delete=root.name)
    assert report["deleted"] is True
    assert not root.exists() and preserved.is_file()


def test_stage_configuration_and_backward_history(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "system/controlDict").write_text(
        "startFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 1;\ndeltaT 1;\nadjustTimeStep no;\nwriteControl timeStep;\nwriteInterval 1;\npurgeWrite 2;\n",
        encoding="utf-8",
    )
    (case / "system/fvSchemes").write_text("ddtSchemes\n{\n default Euler;\n}\n", encoding="utf-8")
    stage = {"stage": "C", "scheme": "Euler", "dt_s": 0.01, "start_s": 0.0, "end_s": 0.03, "steps": 3}
    applied = configure_stage(case, stage, start_mode="CONTINUE_STAGE", preserve_temporal_history=True)
    assert applied["retains_temporal_history"] is True
    assert "startFrom latestTime;" in (case / "system/controlDict").read_text()
    write_time(case, 0.01); write_time(case, 0.02); write_time(case, 0.03)
    assert _history_evidence(case, 0.01)["valid"] is True


def test_parallel_run_script_always_defines_reconstruction_command(tmp_path: Path) -> None:
    script = tmp_path / "run_case.sh"
    write_run_script(script, "foamRun", "incompressibleFluid", 2, 10)
    text = script.read_text(encoding="utf-8")
    assert "reconstructPar -latestTime > log.reconstructPar" in text
    write_run_script(
        script,
        "foamRun",
        "incompressibleFluid",
        2,
        10,
        reconstruct_times=[0.01, 0.02, 0.03],
    )
    assert "reconstructPar -time 0.01,0.02,0.03" in script.read_text(encoding="utf-8")


def test_transition_microcase_converts_rans_simple_to_pimple(tmp_path: Path) -> None:
    system = tmp_path / "system"
    system.mkdir()
    (system / "fvSolution").write_text("SIMPLE {}\n", encoding="utf-8")
    (system / "fvSchemes").write_text(
        "div(phi,U) bounded Gauss linearUpwind limited;\n"
        "div(phi,nuTilda) bounded Gauss upwind;\n",
        encoding="utf-8",
    )
    _configure_urans_dictionaries(tmp_path)
    solution = (system / "fvSolution").read_text(encoding="utf-8")
    schemes = (system / "fvSchemes").read_text(encoding="utf-8")
    assert "PIMPLE" in solution and "nOuterCorrectors 3;" in solution
    assert "SIMPLE" not in solution
    assert "div(phi,U) Gauss linearUpwind limited;" in schemes
    assert "div(phi,nuTilda) Gauss linearUpwind limited;" in schemes


def test_stage_write_interval_hits_target_after_delta_t_change(tmp_path: Path) -> None:
    time = tmp_path / "0.0015625/uniform"
    time.mkdir(parents=True)
    (time / "time").write_text("index 25;\n", encoding="utf-8")
    interval, target_index = _phase_boundary_write_interval(
        tmp_path,
        intended_end=Decimal("0.0019375"),
        delta_t=Decimal("0.000125"),
        stage_steps=3,
    )
    assert target_index == 28
    assert interval == 2


def test_matrix_is_exact_three_values_per_six_meshes() -> None:
    matrix = build_run_matrix(mesh_registry(), dt_values_s=[2.5e-4, 1.25e-4, 6.25e-5], preset="reference")
    assert len(matrix["runs"]) == 18
    for mesh_id in {item["mesh_id"] for item in matrix["runs"]}:
        values = [item["dt_s"] for item in matrix["runs"] if item["mesh_id"] == mesh_id]
        assert values == sorted(values, reverse=True) and len(values) == 3
    with pytest.raises(ValueError):
        build_run_matrix(mesh_registry(), dt_values_s=[1e-4, 5e-5], preset="custom")


def test_queue_deduplicates_and_limits_three_dt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = {canonical_case_id("closed", "coarse", 8, dt): row(dt=dt) for dt in (3e-4, 2e-4, 1e-4)}
    aliases = {key: key for key in rows}
    monkeypatch.setattr(queue_manager, "_available_rows", lambda _: (rows, aliases))
    monkeypatch.setattr(queue_manager, "inspect_canonical_case", lambda project, value: {
        "case_id": canonical_case_id("closed", "coarse", 8, float(value["dt_s"])),
        "calculated_action": "START_FROM_RANS", "current_time_s": None,
        "current_phase": None, "terminal_reason": None, "case_path": "case",
    })
    ids = list(rows)
    state = queue_manager.prepare_queue(tmp_path, [ids[0], ids[0], ids[1], ids[2]])
    assert state["total"] == 3 and state["deduplicated_case_ids"] == [ids[0]]


def test_migration_preview_apply_is_exact_and_preserves_rans_mesh_results(tmp_path: Path) -> None:
    active = active_workspace_root(tmp_path)
    legacy = active / "runs/closed/coarse/legacy_case/production/production_attempt_001"
    legacy.mkdir(parents=True)
    (legacy / "data").write_text("legacy", encoding="utf-8")
    quick = active / "quick_checks/old"
    quick.mkdir(parents=True)
    (quick / "log").write_text("old", encoding="utf-8")
    for path in (active / "checkpoints/closed_coarse/final", active / "meshes/closed_coarse/mesh", tmp_path / "Results/curated"):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text("preserve", encoding="utf-8")
    write_json_atomic(active / "study_config.json", {**default_study_config(), "schema_version": 8})
    write_json_atomic(active / "mesh_registry.json", mesh_registry())
    dry = preview(tmp_path)
    assert dry["candidate_count"] == 2 and legacy.exists()
    report = apply(tmp_path, confirm="APPLY_SCHEMA9_RESET")
    assert report["status"] == "APPLIED" and report["schema9_case_count"] == 18
    assert not legacy.exists() and not quick.exists()
    assert (active / "checkpoints/closed_coarse/final").is_file()
    assert (active / "meshes/closed_coarse/mesh").is_file()
    assert (tmp_path / "Results/curated").is_file()
    assert preview(tmp_path)["candidate_count"] == 0


def test_migration_detects_inventory_change(tmp_path: Path) -> None:
    active = active_workspace_root(tmp_path)
    target = active / "runs/open/fine/old"
    target.mkdir(parents=True)
    (target / "a").write_text("1", encoding="utf-8")
    write_json_atomic(active / "study_config.json", {**default_study_config(), "schema_version": 8})
    write_json_atomic(active / "mesh_registry.json", mesh_registry())
    preview(tmp_path)
    (target / "b").write_text("2", encoding="utf-8")
    with pytest.raises(RuntimeError, match="INVENTORY_CHANGED"):
        apply(tmp_path, confirm="APPLY_SCHEMA9_RESET")
    assert target.exists()


def test_migration_blocks_uncertain_active_runtime(tmp_path: Path) -> None:
    active = active_workspace_root(tmp_path)
    write_json_atomic(
        active / "runtime/active_execution.json",
        {"status": "RUNNING", "case_id": "unknown_identity"},
    )
    with pytest.raises(RuntimeError, match="UNCERTAIN_EXECUTION"):
        preview(tmp_path)


def test_scientific_bundle_has_png_svg_data_manifest_and_no_layout_warning(tmp_path: Path) -> None:
    figure, axis = plt.subplots(figsize=(4, 3), constrained_layout=True)
    data = pd.DataFrame({"x/c": [0.0, 0.5, 1.0], "Cp": [1.0, None, -0.2]})
    axis.plot(data["x/c"], data["Cp"], marker="o", label=r"$C_p$")
    axis.set(xlabel=r"$x/c$ [-]", ylabel=r"$C_p$ [-]")
    with warnings.catch_warnings(record=True) as captured:
        products = save_scientific_figure(figure, tmp_path / "figure.png", data=data, metadata={"source": "fixture"})
    assert not [item for item in captured if "layout" in str(item.message).lower()]
    assert all(Path(value).is_file() for value in products.values() if value)
    ET.parse(products["svg"])
    manifest = json.loads(Path(products["manifest"]).read_text())
    assert manifest["dpi"] == 300 and manifest["missing_values"] == "preserved_as_gaps"


def test_cp_branch_pipeline_keeps_branches_separate_and_deduplicates() -> None:
    raw = pd.DataFrame([
        {"wall_side": "external", "surface": "upper", "x_over_c": 1.0, "Cp": -0.2},
        {"wall_side": "external", "surface": "upper", "x_over_c": 0.0, "Cp": 1.0},
        {"wall_side": "external", "surface": "upper", "x_over_c": 1.0, "Cp": -0.4},
        {"wall_side": "external", "surface": "lower", "x_over_c": 0.5, "Cp": 0.2},
        {"wall_side": "internal", "surface": "upper", "x_over_c": None, "Cp": 0.0},
    ])
    values, audit = _ordered_cp_branches(raw)
    assert list(values[values.branch_id == "upper_external"].x_over_c) == [0.0, 1.0]
    assert audit["duplicates_consolidated"] == 1 and audit["excluded_rows"] == 1


def test_cp_branch_pipeline_uses_edge_connectivity_before_surface_sorting() -> None:
    raw = pd.DataFrame([
        {"patch": "airfoil_wall", "x_m": 0.16, "y_m": 0.06, "edge_x0_m": 0.0, "edge_y0_m": 0.0, "edge_x1_m": 0.33, "edge_y1_m": 0.12, "x_over_c": 0.16, "Cp": -1.0},
        {"patch": "airfoil_wall", "x_m": 0.50, "y_m": 0.14, "edge_x0_m": 0.33, "edge_y0_m": 0.12, "edge_x1_m": 0.67, "edge_y1_m": 0.16, "x_over_c": 0.50, "Cp": -0.7},
        {"patch": "airfoil_wall", "x_m": 0.84, "y_m": 0.08, "edge_x0_m": 0.67, "edge_y0_m": 0.16, "edge_x1_m": 1.0, "edge_y1_m": 0.0, "x_over_c": 0.84, "Cp": -0.5},
        {"patch": "airfoil_wall", "x_m": 0.84, "y_m": -0.08, "edge_x0_m": 1.0, "edge_y0_m": 0.0, "edge_x1_m": 0.67, "edge_y1_m": -0.16, "x_over_c": 0.84, "Cp": 0.2},
        {"patch": "airfoil_wall", "x_m": 0.50, "y_m": -0.14, "edge_x0_m": 0.67, "edge_y0_m": -0.16, "edge_x1_m": 0.33, "edge_y1_m": -0.12, "x_over_c": 0.50, "Cp": 0.35},
        {"patch": "airfoil_wall", "x_m": 0.16, "y_m": -0.06, "edge_x0_m": 0.33, "edge_y0_m": -0.12, "edge_x1_m": 0.0, "edge_y1_m": 0.0, "x_over_c": 0.16, "Cp": 0.5},
    ])
    values, audit = _ordered_cp_branches(raw)
    assert audit["connectivity_used"] is True
    assert set(values["branch_id"]) == {"upper_external", "lower_external"}
    for _, branch in values.groupby("branch_id"):
        assert branch["x_over_c"].is_monotonic_increasing


def test_checkpoint_wall_postprocess_resolves_immutable_physics(tmp_path: Path) -> None:
    case = tmp_path / "checkpoint/case"
    case.mkdir(parents=True)
    write_json_atomic(
        case.parent / "checkpoint_manifest.json",
        {
            "compatibility": {"physics": {
                "chord_m": 1.0, "reynolds": 1.9e6,
                "rho_kg_m3": 0.6660666, "mu_pa_s": 1.7894e-5,
                "alpha_deg": 8.0, "topology": "closed",
            }}
        },
    )
    inputs = _load_case_definition_json(case, "case_input_summary.json")
    config = _load_case_definition_json(case, "case_config.json")
    assert inputs["velocity_m_s"] == pytest.approx(1.9e6 * 1.7894e-5 / 0.6660666)
    assert inputs["mu_Pa_s"] == 1.7894e-5
    assert config["geometry_topology"] == "closed"


def test_medium_reference_differences_are_signed_and_near_zero_is_explicit() -> None:
    rows = [
        {"topology": "closed", "mesh_level": level, "cell_count": cells,
         "included_in_rans_mesh_convergence": True, "mean_CL": cl, "mean_CD": 0.02,
         "mean_CM": cm, "mean_L_over_D": cl / 0.02}
        for level, cells, cl, cm in (("coarse", 100, 0.9, -0.01), ("medium", 200, 1.0, 0.0), ("fine", 400, 1.1, 0.01))
    ]
    records = _rans_scalar_change_records(rows)
    cl = {item["mesh_level"]: item for item in records if item["metric"] == "mean_CL"}
    assert cl["coarse"]["delta_percent"] == pytest.approx(-10.0)
    assert cl["fine"]["delta_percent"] == pytest.approx(10.0)
    cm = next(item for item in records if item["metric"] == "mean_CM" and item["mesh_level"] == "fine")
    assert cm["delta_percent"] is None and cm["percent_status"] == "NOT_DEFINED_NEAR_ZERO_REFERENCE"


def test_paraview_readiness_requires_real_fields_and_never_returns_null_path(tmp_path: Path) -> None:
    case = tmp_path / "checkpoint/case"
    (case / "constant/polyMesh").mkdir(parents=True)
    (case / "constant/polyMesh/boundary").write_text("boundary", encoding="utf-8")
    write_time(case, 100, fields=("U", "p"))
    missing = prepare_final_state(case)
    assert missing["status"] == "MISSING_FIELDS" and "nuTilda" in missing["reason"]
    (case / "100/nuTilda").write_text("field", encoding="utf-8")
    ready = prepare_final_state(case)
    assert ready["status"] == "READY" and ready["final_state"]


def test_paraview_resolver_selects_only_the_final_iteration_vtk_set(tmp_path: Path) -> None:
    case = tmp_path / "checkpoints/closed_coarse/case"
    (case / "constant/polyMesh").mkdir(parents=True)
    (case / "constant/polyMesh/boundary").write_text("boundary", encoding="utf-8")
    write_time(case, 20000)
    vtk = case / "VTK"
    (vtk / "airfoil_wall").mkdir(parents=True)
    (vtk / "farfield").mkdir(parents=True)
    (vtk / "case_10000.vtk").write_text("old", encoding="utf-8")
    (vtk / "case_20000.vtk").write_text("latest", encoding="utf-8")
    (vtk / "airfoil_wall/airfoil_wall_20000.vtk").write_text("wall", encoding="utf-8")
    (vtk / "farfield/farfield_20000.vtk").write_text("farfield", encoding="utf-8")
    resolved = resolve_final_vtk_artifacts(case)
    assert resolved["status"] == "READY"
    assert resolved["iteration"] == 20000
    assert len(resolved["reader_paths"]) == 3
    assert all("20000" in path for path in resolved["reader_paths"])


def test_registry_urans_title_has_no_legacy_attempt_language(tmp_path: Path) -> None:
    registry = upsert_execution(tmp_path, {
        "run_id": "case", "case_id": "case", "mode": "URANS", "topology": "closed",
        "mesh_level": "coarse", "mesh_id": "closed_coarse", "deltaT": 1e-4,
        "stage": "A", "status": "RUNNING",
    })
    title = execution_title(registry["runs"][0])
    assert "URANS" in title and "attempt" not in title.lower()


def test_registry_discards_noncanonical_pimple_keys(tmp_path: Path) -> None:
    active = active_workspace_root(tmp_path)
    write_json_atomic(active / "execution_registry.json", {
        "schema_version": 1,
        "active_run_id": None,
        "active_mode": "URANS",
        "active_stage": "PILOT",
        "runs": [{
            "run_id": "pimple2",
            "mode": "PIMPLE_SENSITIVITY",
            "mesh_id": "closed_coarse",
            "status": "READY",
            "pilot_required": True,
            "attempt_id": "legacy",
        }],
    })
    registry = load_registry(tmp_path)
    assert registry["active_mode"] is None
    assert registry["active_stage"] is None
    assert registry["runs"][0]["status"] == "PREPARED"
    assert registry["runs"][0]["legacy_status"] == "READY"
    assert "pilot_required" not in registry["runs"][0]
    assert "attempt_id" not in registry["runs"][0]


def test_validation_page_has_no_removed_analysis_panel_dependency() -> None:
    page = (APP / "validation_convergence_page.py").read_text(encoding="utf-8")
    assert "execution_analysis_panel(" not in page
    assert "_canonical_urans_rows(active)" in page
