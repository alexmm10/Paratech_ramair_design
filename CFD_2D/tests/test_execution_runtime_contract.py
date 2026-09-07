from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ramair_2d_execution_registry import load_registry, upsert_execution  # noqa: E402
from ramair_execution_control import (  # noqa: E402
    ExecutionState,
    load_execution_state,
    normalize_execution_state,
    publish_solver_process,
    reconcile_solver_record,
    transition_execution_state,
)
from ramair_monitor_core import (  # noqa: E402
    SolverLogAccumulator,
    parse_openfoam_lines,
    scalar_signal_inventory,
)
from ramair_2d_validation_live_monitor import build_monitor_snapshot  # noqa: E402


def test_legacy_states_map_to_eight_state_contract() -> None:
    assert {state.value for state in ExecutionState} == {
        "PREPARED", "RUNNING", "PAUSED_RECOVERABLE", "FAILED",
        "COMPLETED", "REVIEW_REQUIRED", "APPROVED", "REJECTED",
    }
    assert normalize_execution_state("READY") is ExecutionState.PREPARED
    assert normalize_execution_state("STOPPED_PARTIAL") is ExecutionState.PAUSED_RECOVERABLE
    assert normalize_execution_state("TIMEOUT_PARTIAL", restartable=False) is ExecutionState.FAILED
    assert normalize_execution_state("RUN_DIVERGED") is ExecutionState.FAILED


def test_execution_state_is_atomic_transitioned_and_idempotent(tmp_path: Path) -> None:
    prepared = transition_execution_state(
        tmp_path, "PREPARED", run_id="run-1", idempotency_key="key-1", phase="A"
    )
    assert prepared["sequence"] == 1
    running = transition_execution_state(
        tmp_path, "RUNNING", run_id="run-1", idempotency_key="key-1", phase="A"
    )
    duplicate = transition_execution_state(
        tmp_path, "RUNNING", run_id="run-1", idempotency_key="key-1", phase="A"
    )
    assert running["sequence"] == 2
    assert duplicate["duplicate_suppressed"] is True
    paused = transition_execution_state(
        tmp_path, "PAUSED_RECOVERABLE", idempotency_key="key-1", phase="A"
    )
    resumed = transition_execution_state(
        tmp_path, "RUNNING", idempotency_key="key-2", phase="A"
    )
    assert paused["state"] == "PAUSED_RECOVERABLE"
    assert resumed["sequence"] == 4
    assert json.loads((tmp_path / ".ramair_execution_state.json").read_text())["state"] == "RUNNING"
    assert not list(tmp_path.glob("*.tmp"))


def test_illegal_review_transition_is_rejected(tmp_path: Path) -> None:
    transition_execution_state(tmp_path, "PREPARED")
    with pytest.raises(RuntimeError, match="Illegal execution transition"):
        transition_execution_state(tmp_path, "APPROVED")


def test_completed_solver_segment_can_enter_the_next_urans_phase(tmp_path: Path) -> None:
    transition_execution_state(tmp_path, "PREPARED", idempotency_key="phase-a")
    transition_execution_state(tmp_path, "RUNNING", idempotency_key="phase-a")
    transition_execution_state(tmp_path, "COMPLETED", idempotency_key="phase-a")
    next_phase = transition_execution_state(
        tmp_path, "RUNNING", idempotency_key="phase-b", phase="B"
    )
    assert next_phase["state"] == "RUNNING"
    assert next_phase["phase"] == "B"


def test_staged_solver_segment_does_not_complete_parent_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transition_execution_state(tmp_path, "PREPARED", idempotency_key="campaign")
    transition_execution_state(tmp_path, "RUNNING", idempotency_key="campaign")
    monkeypatch.setenv("RAMAIR_SUPPRESS_CANONICAL_LIFECYCLE", "1")

    publish_solver_process(tmp_path, status="COMPLETED", returncode=0)

    assert load_execution_state(tmp_path)["state"] == "RUNNING"
    assert json.loads((tmp_path / ".ramair_solver_process.json").read_text())["status"] == "COMPLETED"


def test_stale_running_record_becomes_recoverable_with_checkpoint(tmp_path: Path) -> None:
    (tmp_path / "1.25").mkdir()
    (tmp_path / ".ramair_solver_process.json").write_text(
        json.dumps({"status": "RUNNING", "pid": 99999999, "pid_start_token": "stale"}),
        encoding="utf-8",
    )
    transition_execution_state(tmp_path, "PREPARED", idempotency_key="stale")
    transition_execution_state(tmp_path, "RUNNING", idempotency_key="stale")
    result = reconcile_solver_record(tmp_path)
    assert result["restartable"] is True
    assert result["status"] == "PAUSED_RECOVERABLE"
    assert load_execution_state(tmp_path)["state"] == "PAUSED_RECOVERABLE"


def test_shared_monitor_parses_continuous_scalar_signals(tmp_path: Path) -> None:
    lines = [
        "Time = 0.1",
        "deltaT = 0.002",
        "Courant Number mean: 0.4 max: 12.5",
        "smoothSolver: Solving for Ux, Initial residual = 0.1, Final residual = 0.001, No Iterations 2",
        "time step continuity errors : sum local = 1e-6, global = -2e-7, cumulative = 3e-7",
        "ExecutionTime = 3.0 s  ClockTime = 4 s",
    ]
    parsed = parse_openfoam_lines(lines)
    assert parsed["deltaT_history"] == [{"iteration": 0.1, "deltaT": 0.002}]
    assert parsed["courant"][0]["max"] == 12.5
    assert parsed["residuals"][0]["field"] == "U.x"
    assert parsed["residuals"][0]["linear_solver"] == "smoothSolver"
    assert parsed["continuity"][0]["global"] == -2e-7

    log = tmp_path / "log.foamRun"
    log.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
    accumulator = SolverLogAccumulator(max_points=50)
    first = accumulator.update(log)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines[3:]) + "\n")
    second = accumulator.update(log)
    assert first["courant"][0]["max"] == 12.5
    assert second["residuals"]["Ux"][-1][1] == 0.1


def test_live_monitor_reports_angle_and_low_overhead_performance_metrics(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "parallel_execution_plan.json").write_text(
        json.dumps({"effective_ranks": 2}), encoding="utf-8",
    )
    rows = []
    for index in range(5):
        rows.extend([
            f"Time = {index * 0.001:g}",
            "GAMG: Solving for p, Initial residual = 0.1, Final residual = 1e-6, No Iterations 4",
            f"ExecutionTime = {index + 1}.0 s  ClockTime = {index + 1} s",
        ])
    (case / "log.foamRun").write_text("\n".join(rows) + "\n", encoding="utf-8")

    snapshot = build_monitor_snapshot(
        case,
        mode="URANS",
        run_id="example",
        topology="closed",
        mesh_level="coarse",
        cell_count=200_000,
        alpha_deg=8.0,
        stage="C",
        tc_s=0.02,
    )

    assert "alpha=8 deg" in snapshot["title"]
    assert snapshot["performance"]["p95_s_per_step"] == pytest.approx(1.0)
    assert snapshot["performance"]["cells_per_rank"] == pytest.approx(100_000.0)
    assert snapshot["linear_solver_performance"][0]["solver"] == "GAMG"


def test_solver_benchmark_requires_real_solver_steps(tmp_path: Path) -> None:
    import ramair_2d_solver_benchmark_report as report

    scenario = tmp_path / "current_2cores_native"
    scenario.mkdir()
    (scenario / "run_status.json").write_text(
        '{"status":"STOPPED_FORCED_PARTIAL","solver_started":false}\n',
        encoding="utf-8",
    )
    row = report.scenario_record(tmp_path, scenario)
    assert row is not None
    assert row["benchmark_valid"] is False
    assert row["invalid_reason"] == "solver_not_started"


def test_scalar_inventory_keeps_function_object_histories_outside_volume_times(tmp_path: Path) -> None:
    force = tmp_path / "postProcessing/forceCoeffs/0/coefficient.dat"
    probe = tmp_path / "postProcessing/probes/0/U"
    force.parent.mkdir(parents=True)
    probe.parent.mkdir(parents=True)
    force.write_text("0 1 2 3\n", encoding="utf-8")
    probe.write_text("0 (1 0 0)\n", encoding="utf-8")
    (tmp_path / "log.foamRun").write_text("Courant Number mean: 1 max: 2\n", encoding="utf-8")
    inventory = scalar_signal_inventory(tmp_path)
    assert inventory["forces"] == ["postProcessing/forceCoeffs/0/coefficient.dat"]
    assert inventory["probes"] == ["postProcessing/probes/0/U"]
    assert "log.foamRun" in inventory["residuals_and_courant"]


def test_execution_registry_schema3_normalizes_legacy_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAMAIR_VALIDATION_ACTIVE_WORKSPACE", str(tmp_path / "active"))
    upsert_execution(tmp_path, {"run_id": "legacy", "mode": "URANS", "status": "PAUSED_RESTARTABLE"})
    registry = load_registry(tmp_path)
    assert registry["schema_version"] == 3
    assert registry["runs"][0]["status"] == "PAUSED_RECOVERABLE"
    assert registry["runs"][0]["legacy_status"] == "PAUSED_RESTARTABLE"
