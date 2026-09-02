from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
APP = ROOT / "CFD_2D/app"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(APP))

from ramair_2d_openfoam_runner import write_script_if_changed  # noqa: E402
from ramair_2d_rans_accounting import (  # noqa: E402
    authoritative_simple_iteration,
    block_accounting,
    gate_is_due,
    target_for_iteration,
    timing_summary,
)
from ramair_2d_run_lease import (  # noqa: E402
    DuplicateExecutionError,
    acquire_run_lease,
)
from ramair_2d_validation_live_monitor import _parse_increment  # noqa: E402
from ramair_2d_rans_checkpoint_batch import mesh_angle_id  # noqa: E402


def test_rans_checkpoint_identity_preserves_primary_and_separates_secondary_angle() -> None:
    study = {
        "mesh_registry": {
            "meshes": [
                {"id": "closed_medium", "topology": "closed", "level": "medium"},
                {"id": "open_medium", "topology": "open", "level": "medium"},
            ]
        }
    }
    assert mesh_angle_id(study, "closed_medium", 16.0) == "closed_medium"
    assert mesh_angle_id(study, "closed_medium", 8.0) == "closed_medium__alpha_p8"
    assert mesh_angle_id(study, "open_medium", 8.0) == "open_medium"
    assert mesh_angle_id(study, "open_medium", 16.0) == "open_medium__alpha_p16"


def test_absolute_simple_targets_never_gate_at_7840() -> None:
    assert target_for_iteration(0) == 20000
    assert target_for_iteration(7838) == 20000
    assert target_for_iteration(10000) == 20000
    assert target_for_iteration(12499) == 20000
    assert target_for_iteration(12500) == 20000
    assert target_for_iteration(19999) == 20000
    assert target_for_iteration(20000) == 20000
    assert gate_is_due(7840, 10000) is False
    assert gate_is_due(10000, 10000) is False
    assert gate_is_due(20000, 20000) is True
    accounting = block_accounting(7838, block_start=0)
    assert accounting == {
        "absolute_simple_iteration": 7838,
        "block_start_iteration": 0,
        "block_target_iteration": 20000,
        "block_completed_iterations": 7838,
    }


def test_authoritative_iteration_combines_directory_log_and_metadata(
    tmp_path: Path,
) -> None:
    case = tmp_path / "case"
    (case / "steadyInitialization/history/run_1/time_directories/7800").mkdir(
        parents=True
    )
    (case / "staged_run_status.json").write_text(
        json.dumps({"absolute_simple_iteration": 7838}),
        encoding="utf-8",
    )
    (case / "log.foamRun").write_text(
        "Time = 7839\nTime = 7840\n",
        encoding="utf-8",
    )
    result = authoritative_simple_iteration(case)
    assert result["absolute_simple_iteration"] == 7840
    assert result["sources"]["valid_steady_iteration_directory"] == 7800
    assert result["sources"]["metadata:staged_run_status.json"] == 7838
    assert result["sources"]["log:log.foamRun"] == 7840


def test_single_flight_lease_blocks_live_duplicate(tmp_path: Path) -> None:
    first = acquire_run_lease(
        tmp_path,
        study_id="validation_lab",
        run_id="closed_medium_run",
        mode="RANS",
        command=["python", "worker.py", "closed_medium"],
    )
    try:
        with pytest.raises(DuplicateExecutionError) as captured:
            acquire_run_lease(
                tmp_path,
                study_id="validation_lab",
                run_id="closed_medium_run",
                mode="RANS",
                command=["python", "worker.py", "closed_medium"],
            )
        assert (
            captured.value.payload["status"]
            == "BLOCKED_DUPLICATE_EXECUTION"
        )
        payload = json.loads(first.path.read_text(encoding="utf-8"))
        assert {
            "run_id",
            "job_id",
            "pid",
            "worker_id",
            "started_at",
            "heartbeat_at",
            "command_hash",
            "state",
        }.issubset(payload)
    finally:
        first.release(state="COMPLETED")


def test_run_script_is_not_rewritten_when_unchanged(tmp_path: Path) -> None:
    script = tmp_path / "run_case.sh"
    first = write_script_if_changed(script, "#!/bin/sh\necho ok\n")
    timestamp = script.stat().st_mtime_ns
    second = write_script_if_changed(script, "#!/bin/sh\necho ok\n")
    assert first["changed"] is True
    assert second["changed"] is False
    assert script.stat().st_mtime_ns == timestamp
    manifest = json.loads(
        (tmp_path / ".ramair_run_script_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["revision"] == 1


def test_solver_timing_separates_active_setup_post_and_normalizes() -> None:
    result = timing_summary(
        [
            {
                "iteration_start": 0,
                "iteration_end": 5000,
                "active_solver_seconds": 100.0,
                "setup_seconds": 4.0,
                "post_seconds": 2.0,
                "total_elapsed_seconds": 108.0,
            },
            {
                "iteration_start": 5000,
                "iteration_end": 10000,
                "active_solver_seconds": 120.0,
                "setup_seconds": 3.0,
                "post_seconds": 1.0,
                "total_elapsed_seconds": 126.0,
            },
        ]
    )
    assert result["timing_status"] == "COMPLETE"
    assert result["solver_active_wall_time_first_10000_s"] == 220.0
    assert result["setup_overhead_to_10000_s"] == 7.0
    assert result["total_elapsed_wall_time_to_10000_s"] == 234.0
    assert result["monitoring_overhead_estimate_s"] == 4.0


def test_residual_parser_keeps_raw_solves_and_full_columns() -> None:
    recent = _parse_increment(
        [
            "Time = 42",
            (
                "smoothSolver:  Solving for Ux, Initial residual = 1e-3, "
                "Final residual = 2e-6, No Iterations 2"
            ),
            (
                "GAMG:  Solving for p, Initial residual = 4e-4, "
                "Final residual = 8e-7, No Iterations 3"
            ),
        ],
        {},
        max_points=20,
    )
    assert len(recent["residuals"]) == 2
    assert recent["residuals"][0] == {
        "iteration": 42.0,
        "equation": "U.x",
        "component": "x",
        "field": "U.x",
        "value": 1.0e-3,
        "initial_residual": 1.0e-3,
        "final_residual": 2.0e-6,
        "n_iterations": 2,
    }


def test_validation_lab_has_final_horizontal_navigation_and_no_tabs() -> None:
    page = (APP / "validation_convergence_page.py").read_text(
        encoding="utf-8"
    )
    for label in (
        "Mallas y condiciones",
        "Solver y estrategia",
        "Análisis RANS",
        "Análisis URANS",
        "Sensibilidad PIMPLE",
        "Convergencia RANS",
        "Matriz URANS",
        "Convergencia malla-tiempo",
        "Frecuencias",
        "Courant",
        "Informes",
    ):
        assert label in page
    assert "\n    tabs = st.tabs" not in page
    assert "\n    analysis_tabs = st.tabs" not in page
    assert "Postproceso RANS rápido" in page
    assert "Generar animaciones RANS" in page
    assert "Postproceso URANS rápido" in page
    assert "Generar animaciones" in page


def test_rans_queue_contract_has_six_visible_bases_and_no_urans_transfer() -> None:
    source = (
        SCRIPTS / "ramair_2d_rans_checkpoint_batch.py"
    ).read_text(encoding="utf-8")
    assert "RANS_QUEUE_IDS = MESH_IDS" in source
    targeted = source.split(
        "def _execute_targeted_rans_blocks", 1
    )[1].split("def _execute_base_unlocked", 1)[0]
    assert "start-transient" not in targeted
    assert "AUTO_EXTEND_TO_" in targeted
    assert "COMPLETED_20000_MANUAL_REVIEW_REQUIRED" in targeted
