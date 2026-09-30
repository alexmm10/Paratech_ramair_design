"""Regressions for the observed independent-MPI orphan / partial-angle skip."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import ramair_2d_openfoam_sweep as sweep
from ramair_execution_control import publish_solver_process, pid_is_alive


def test_staged_partial_dominates_successful_solver_segment():
    assert sweep.effective_case_status({"status":"TRANSIENT_STAGE_PARTIAL"}, {"status":"RUN_COMPLETED"}, 0) == "TRANSIENT_STAGE_PARTIAL"


def test_queue_pauses_on_partial_and_resume_keeps_rans(tmp_path, monkeypatch):
    base = tmp_path/"CFD_2D/openfoam_cases/test"
    for angle in (-10., -8.):
        case = base/sweep.safe_alpha_dir(angle)
        (case/"system").mkdir(parents=True)
        (case/"system/controlDict").write_text("stopAt endTime;\n")
        (case/"steadyInitialization").mkdir()
        (case/"steadyInitialization/pending_stage.json").write_text(json.dumps({"latest_iteration":3775,"transition":{"maximum_iterations":20000}}))
        (case/"3775").mkdir()
        (case/"3775/U").write_text("preserved checkpoint")
    monkeypatch.setattr(sys, "argv", ["sweep", "--case-root", str(tmp_path), "--variant", "test", "--alphas", "-10", "-8", "--run"])
    args = sweep.parse_args()
    calls = []
    def run(command, **kw):
        calls.append(command)
        (kw["cwd"]/"staged_run_status.json").write_text('{"status":"TRANSIENT_STAGE_PARTIAL"}')
        (kw["cwd"]/"run_status.json").write_text('{"status":"RUN_COMPLETED"}')
        return subprocess.CompletedProcess(command, 0), False
    monkeypatch.setattr(sweep, "run_with_case_timeout", run)
    assert sweep.run_sweep(args) == 1
    assert len(calls) == 1
    assert calls[0][calls[0].index("--steady-decision")+1] == "extend"
    assert (base/sweep.safe_alpha_dir(-10)/"3775/U").read_text() == "preserved checkpoint"


@pytest.mark.skipif(os.name == "nt", reason="Independent Unix MPI process groups")
def test_crashed_controller_drains_independent_child_and_preserves_write(tmp_path):
    (tmp_path/"system").mkdir()
    (tmp_path/"system/controlDict").write_text("stopAt endTime;\n")
    script = "from pathlib import Path; import time\nwhile 'writeNow' not in Path('system/controlDict').read_text(): time.sleep(.02)\nPath('checkpoint').write_text('saved')"
    child = subprocess.Popen([sys.executable, "-c", script], cwd=tmp_path, start_new_session=True)
    publish_solver_process(tmp_path, status="RUNNING", pid=child.pid)
    try:
        completed, expired = sweep.run_with_case_timeout([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_min=1, stop_grace_min=.01)
        assert not expired
        assert completed.returncode == 1  # controller completion did not complete its child
        assert not pid_is_alive(child.pid)
        assert (tmp_path/"checkpoint").read_text() == "saved"
        assert (tmp_path/".ramair_campaign_pause.json").exists()
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=5)
