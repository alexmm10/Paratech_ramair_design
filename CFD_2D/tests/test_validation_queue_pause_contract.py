from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ramair_2d_rans_checkpoint_batch as rans_queue  # noqa: E402
import ramair_2d_urans_matrix_manager as urans_queue  # noqa: E402
from ramair_2d_study_registry import write_json_atomic  # noqa: E402


def test_rans_selection_queue_skips_completed_and_can_pause_current_then_continue(
    tmp_path: Path, monkeypatch,
) -> None:
    active = tmp_path / "active"
    monkeypatch.setattr(rans_queue, "active_workspace_root", lambda root: active)
    monkeypatch.setattr(rans_queue, "load_study", lambda root: {})
    monkeypatch.setattr(
        rans_queue, "mesh_angle_id", lambda study, mesh_id, alpha: f"{mesh_id}_{alpha:g}"
    )

    calls: list[str] = []

    def status(root: Path, mesh_id: str):
        if mesh_id == "closed_coarse_16":
            return {"status": "CHECKPOINT_READY", "iterations_completed": 20000}
        return {"status": "RANS_BASE_NOT_CREATED", "iterations_completed": 0}

    def execute(root: Path, mesh_id: str, **kwargs):
        calls.append(mesh_id)
        if mesh_id == "closed_medium_16":
            write_json_atomic(
                active / ".rans_queue_stop_request.json",
                {"action": "pause_current_continue", "mesh_id": mesh_id},
            )
            return {"status": "RANS_PARTIAL", "iterations_completed": 11269}
        return {"status": "CHECKPOINT_READY", "iterations_completed": 20000}

    monkeypatch.setattr(rans_queue, "checkpoint_status", status)
    monkeypatch.setattr(rans_queue, "execute_base", execute)
    result = rans_queue.execute_selection_queue(
        tmp_path,
        ["closed_coarse:16", "closed_medium:16", "closed_fine:16"],
        run=True,
    )
    assert result["status"] == "COMPLETED"
    assert calls == ["closed_medium_16", "closed_fine_16"]
    assert result["cases"][0]["queue_action"] == "SKIPPED_COMPLETED"
    assert result["cases"][1]["queue_action"] == "PAUSED_AND_SKIPPED_BY_USER"
    assert not (active / ".rans_queue_stop_request.json").exists()


def test_urans_queue_pause_current_continues_but_pause_queue_retains_index(
    tmp_path: Path, monkeypatch,
) -> None:
    paths = {"json": tmp_path / "queue.json", "csv": tmp_path / "queue.csv"}
    monkeypatch.setattr(urans_queue, "queue_paths", lambda root: paths)
    monkeypatch.setattr(urans_queue, "active_workspace_root", lambda root: tmp_path)
    rows = {
        "case-a": {"run_id": "case-a", "mesh_id": "open_medium"},
        "case-b": {"run_id": "case-b", "mesh_id": "open_fine"},
    }
    monkeypatch.setattr(urans_queue, "_available_rows", lambda root: (rows, {key: key for key in rows}))
    calls: list[str] = []
    outcomes = {"case-a": "PAUSED", "case-b": "COMPLETED"}

    def inspect(root: Path, row):
        case_id = row["run_id"]
        return {
            "case_id": case_id,
            "case_presence": "STARTED",
            "calculated_action": "RESUME" if outcomes[case_id] == "PAUSED" else "REVIEW",
            "current_phase": "B",
            "current_time_s": 0.1,
            "execution_outcome": outcomes[case_id],
            "terminal_reason": "USER_REQUESTED_STOP" if outcomes[case_id] == "PAUSED" else "FINAL_TIME_REACHED",
            "case_path": str(tmp_path / case_id / "case"),
        }

    def execute(root: Path, run_id: str, **kwargs):
        calls.append(run_id)
        if run_id == "case-a":
            write_json_atomic(
                tmp_path / ".urans_queue_control_request.json",
                {"action": "pause_current_continue"},
            )
        return 0

    monkeypatch.setattr(urans_queue, "inspect_canonical_case", inspect)
    monkeypatch.setattr(urans_queue, "execute_run", execute)
    write_json_atomic(paths["json"], {
        "schema_version": 3,
        "status": "READY",
        "current_index": 0,
        "runs": [
            {"case_id": "case-a", "case_path": str(tmp_path / "case-a/case"), "startup_mode": "progressive"},
            {"case_id": "case-b", "case_path": str(tmp_path / "case-b/case"), "startup_mode": "progressive"},
        ],
    })
    result = urans_queue.execute_queue(tmp_path, run=True)
    assert result["status"] == "COMPLETED"
    assert calls == ["case-a"]
    assert result["runs"][0]["result"] == "PAUSED_AND_SKIPPED_BY_USER"
    assert result["runs"][1]["result"] == "SKIPPED_COMPLETED"
