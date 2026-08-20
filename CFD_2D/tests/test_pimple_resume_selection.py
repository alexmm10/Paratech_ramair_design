from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "CFD_2D/scripts"))

from ramair_2d_pimple_outer_study import (  # noqa: E402
    _entry_completion_evidence,
    _resume_entry_selection,
)


def _entry(root: Path, outer: int, *, complete: bool) -> dict[str, object]:
    run_id = f"case_pimple{outer}_backward"
    run_root = root / run_id
    case = run_root / "case"
    (case / "system").mkdir(parents=True)
    (case / "system/fvSolution").write_text("PIMPLE {}\n")
    (run_root / "stage_plan.json").write_text(json.dumps({
        "stages": [
            {"stage": "D", "end_s": 0.1, "dt_s": 0.001},
            {"stage": "E", "end_s": 0.5, "dt_s": 0.001},
        ]
    }))
    if complete:
        time_dir = case / "0.5"
        time_dir.mkdir()
        (case / "log.foamRun").write_text("Time = 0.5\nEnd\n")
        (run_root / "force_coeffs.csv").write_text("Time,Cl\n0.5,1\n")
        # Legacy n=3 evidence can be complete without execution_status.json.
        (run_root / "case_summary.json").write_text(json.dumps({"status": "COMPLETED"}))
    else:
        for value in (0.001, 0.002, 0.003):
            (case / f"{value:g}").mkdir()
    return {
        "run_id": run_id,
        "case": str(case),
        "dt_s": 0.001,
        "nOuterCorrectors": outer,
    }


def test_resume_preserves_completed_legacy_entries_and_selects_only_n4(
    tmp_path: Path,
) -> None:
    entries = [
        _entry(tmp_path, 2, complete=True),
        _entry(tmp_path, 3, complete=True),
        _entry(tmp_path, 4, complete=False),
    ]
    selection = _resume_entry_selection(tmp_path, entries)
    assert selection["preserve_completed"] == [
        "case_pimple2_backward", "case_pimple3_backward"
    ]
    assert selection["execute"] == ["case_pimple4_backward"]
    assert _entry_completion_evidence(tmp_path, entries[1])["complete"] is True
    assert _entry_completion_evidence(tmp_path, entries[2])["complete"] is False
