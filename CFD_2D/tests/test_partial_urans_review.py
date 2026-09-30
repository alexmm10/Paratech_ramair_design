from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import json
import numpy as np
import pandas as pd
import pytest

from ramair_2d_urans_review import review_run, longest_stationary_tail, set_review_decision
from ramair_2d_space_time_convergence import _comparison_rows
from ramair_workspace_jobs import execution_command


def test_native_review_without_export_and_with_case_angle(tmp_path):
    folder = tmp_path / "case/postProcessing/forceCoeffs/0.2"
    folder.mkdir(parents=True)
    t = np.arange(0.2, 2.201, .001)
    values = np.sin(2*np.pi*20*t)
    text = "# Time Cd Cl Cm\n" + "\n".join(f"{x:.6f} {0.05+.001*y} {1.8+.02*y} {.08+.001*y}" for x,y in zip(t,values))
    (folder / "coefficient.dat").write_text(text)
    (tmp_path / "case_metadata.json").write_text(json.dumps({"alpha_deg":16, "chord_m":1, "U_inf_m_s":51, "operating_condition":{"alpha_deg":8}}))
    (tmp_path / "stage_plan.json").write_text(json.dumps({"stages":[{"stage":"E","start_s":.2,"end_s":3,"dt_s":.001}], "sampling_end_s":3}))
    result = review_run(tmp_path)
    assert result["frequency_method"]["alpha_deg"] == 16
    assert result["native_history_sources"]
    assert result["sampling_window"]["stage"] == "E"
    assert result["signals"]["Cl"]["spectrum"]["dominant_frequency_hz"] == pytest.approx(20, rel=.05)
    assert (tmp_path / "fft_coefficients.csv").exists()
    assert all(Path(p).stat().st_size > 1000 for p in result["plots"])
    assert result["partial_duration"]


def test_diagnostic_d_cannot_be_published(tmp_path):
    (tmp_path / "review.json").write_text(json.dumps({"sampling_window":{"stage":"D"}, "window_selection":{"status":"STATIONARY_TAIL_FOUND"}}))
    with pytest.raises(RuntimeError, match="production"):
        set_review_decision(tmp_path, "publish-partial")


def test_mean_stability_does_not_imply_fluctuation_stability():
    t = np.arange(4000) * .001
    wave = .0001 * np.sin(2*np.pi*20*t) * np.exp(-t)
    frame = pd.DataFrame({"Time":t, "Cl":1.8+wave, "Cd":.05+wave, "Cm":.08+wave})
    selected, result = longest_stationary_tail(frame)
    assert result["status"] == "MEAN_STABLE_ONLY"
    assert len(selected) == len(frame)


def test_temporal_groups_do_not_mix_angles_or_durations():
    common = {"mesh_id":"mesh", "cell_count":1234, "mean_Cl":1, "sampling_duration_s":1, "operating_condition_key":"same"}
    rows = [{**common,"alpha_deg":8,"dt_s":.01}, {**common,"alpha_deg":16,"dt_s":.005}]
    assert _comparison_rows(rows,"temporal") == []
    rows[1]["alpha_deg"] = 8
    rows[1]["sampling_duration_s"] = .5
    assert _comparison_rows(rows,"temporal") == []
    assert len(_comparison_rows(rows,"temporal", equal_duration=False)) == 2


def test_rans_only_pending_queue_explicitly_extends(tmp_path):
    case = tmp_path / "Angles/alpha_p16p000"
    (case / "steadyInitialization").mkdir(parents=True)
    (case / "steadyInitialization/pending_stage.json").write_text("{}")
    command = execution_command(tmp_path, tmp_path, case, 1, True, options={"phase_action":"rans_only"})
    assert "--steady-only" in command
    assert command[command.index("--steady-decision")+1] == "extend"
    assert "--no-renumber-before-decompose" in command
