from __future__ import annotations

import json
from pathlib import Path

from ramair_2d_open_validation_pilot import archive_previous_execution


def test_previous_pilot_execution_is_archived_with_phase_evidence(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "staged_run_status.json").write_text(
        json.dumps({
            "status": "TRANSIENT_STAGE_FAILED",
            "completion_reason": "run_diverged",
            "production_complete": False,
            "transient_phases": [
                {
                    "phase": "A",
                    "returncode": 0,
                    "run_status": {
                        "status": "RUN_COMPLETED",
                        "openfoam_event": {"maximum_courant": 8.0},
                    },
                },
                {
                    "phase": "E",
                    "returncode": 1,
                    "run_status": {
                        "status": "RUN_DIVERGED",
                        "openfoam_event": {
                            "maximum_courant": 2952.0,
                            "numerical_divergence": True,
                        },
                    },
                },
            ],
        }),
        encoding="utf-8",
    )
    result = archive_previous_execution(case, tmp_path / "reports")
    assert result is not None
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["software_phase_transitions_verified"] == ["A"]
    assert report["scientific_result_accepted"] is False
    assert report["phases"][1]["numerical_divergence"] is True
