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
    reconcile_solver_record,
    transition_execution_state,
)
from ramair_monitor_core import (  # noqa: E402
    SolverLogAccumulator,
    parse_openfoam_lines,
    scalar_signal_inventory,
)


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
