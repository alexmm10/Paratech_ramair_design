from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import ramair_2d_urans_cases as urans
from ramair_2d_parallel_campaign import run_campaign


def test_parallel_campaign_completes_independent_cases(tmp_path: Path) -> None:
    manifest = tmp_path / "campaign.json"
    command = [sys.executable, "-c", "import time; time.sleep(0.25)"]
    manifest.write_text(
        json.dumps({
            "campaign_id": "test",
            "project_root": str(tmp_path),
            "total_core_budget": 2,
            "max_concurrent_cases": 2,
            "cases": [
                {"case_id": "a", "command": command, "n_cores": 1},
                {"case_id": "b", "command": command, "n_cores": 1},
            ],
        }),
        encoding="utf-8",
    )
    started = time.monotonic()
    assert run_campaign(manifest) == 0
    elapsed = time.monotonic() - started
    state = json.loads(manifest.with_suffix(".status.json").read_text(encoding="utf-8"))
    assert state["status"] == "FINISHED"
    assert {item["status"] for item in state["cases"]} == {"COMPLETED"}
    # The coordinator polls once per second; both children must still finish
    # within one polling interval rather than two serial intervals.
    assert elapsed < 1.5
    assert state["cases"][0]["started_at"] == state["cases"][1]["started_at"]


def test_canonical_urans_leases_are_isolated_per_case(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(urans, "active_workspace_root", lambda _root: tmp_path)
    token = urans.process_start_token(os.getpid())
    first = {
        "case_id": "open_medium_a08_dt1",
        "lease_id": "lease-a",
        "PID": os.getpid(),
        "process_start_token": token,
    }
    second = {
        "case_id": "open_fine_a08_dt2",
        "lease_id": "lease-b",
        "PID": os.getpid(),
        "process_start_token": token,
    }
    urans.acquire_solver_lease(tmp_path, first)
    urans.acquire_solver_lease(tmp_path, second)
    lease_dir = tmp_path / "runtime/solver_leases"
    assert len(list(lease_dir.glob("*.json"))) == 2
    urans.release_solver_lease(tmp_path, "lease-a")
    assert len(list(lease_dir.glob("*.json"))) == 1
    urans.release_solver_lease(tmp_path, "lease-b")
    assert not list(lease_dir.glob("*.json"))
