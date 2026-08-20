from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "CFD_2D/scripts"))

from ramair_2d_pimple_outer_study import (  # noqa: E402
    _entry_completion_evidence,
    _measurement_signature,
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


def test_measurement_signature_ignores_only_phase_owned_runtime_cursors(
    tmp_path: Path,
) -> None:
    cases = []
    for outer, scheme, ranks in ((2, "backward", 8), (4, "Euler", 4)):
        case = tmp_path / str(outer)
        for folder in ("0", "constant", "system"):
            (case / folder).mkdir(parents=True)
        (case / "0/U").write_text("same-field")
        (case / "constant/transportProperties").write_text("same-physics")
        (case / "system/fvSolution").write_text(
            f"PIMPLE\n{{\n nOuterCorrectors {outer};\n nCorrectors 2;\n}}\n"
        )
        (case / "system/fvSchemes").write_text(
            f"ddtSchemes {{ default {scheme}; }}\n"
            "divSchemes { default none; }\n"
        )
        (case / "system/decomposeParDict").write_text(
            f"numberOfSubdomains {ranks};\nmethod scotch;\n"
        )
        (case / "system/controlDict").write_text(
            f"startFrom startTime;\nstartTime 0;\nendTime {outer};\n"
            f"deltaT 0.001;\nwriteControl timeStep;\nwriteInterval {outer};\n"
            "purgeWrite 3;\n"
        )
        cases.append(case)
    assert _measurement_signature(cases[0]) == _measurement_signature(cases[1])
    with (cases[1] / "system/fvSchemes").open("a", encoding="utf-8") as stream:
        stream.write("gradSchemes { default Gauss linear; }\n")
    assert _measurement_signature(cases[0]) != _measurement_signature(cases[1])
