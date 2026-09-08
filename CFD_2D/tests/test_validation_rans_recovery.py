from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ramair_2d_validation_rans_recovery import recover_short_urans_to_rans  # noqa: E402


def test_short_urans_is_archived_and_latest_rans_becomes_pending(tmp_path: Path) -> None:
    case = (
        tmp_path / "CFD_2D/openfoam_cases/reference_uncut_validation_1m/alpha_p0p000"
    )
    system = case / "system"
    steady = system / "steadyInitialization"
    steady.mkdir(parents=True)
    for name in ("controlDict", "fvSchemes", "fvSolution"):
        text = (
            "startFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 1;\n"
            if name == "controlDict" else
            "solvers\n{\n}\nSIMPLE\n{\n}\nrelaxationFactors\n"
            "{ fields { p 0.2; } equations { U 0.5; nuTilda 0.5; } }\n"
            if name == "fvSolution" else "ddtSchemes { default steadyState; }\n"
        )
        (system / name).write_text(text, encoding="utf-8")
        (steady / name).write_text(text, encoding="utf-8")
    (steady / "stage_config.json").write_text(
        json.dumps({
            "config_schema_version": 5,
            "geometry_topology": "closed_external_airfoil",
        }),
        encoding="utf-8",
    )
    archive = case / "steadyInitialization/history/run_20260101_000000"
    checkpoint = archive / "time_directories/4519"
    checkpoint.mkdir(parents=True)
    (checkpoint / "U").write_text("rans", encoding="utf-8")
    (archive / "postProcessing").mkdir(parents=True)
    (archive / "postProcessing/forces.dat").write_text("rans", encoding="utf-8")
    (case / "0.001").mkdir()
    (case / "postProcessing").mkdir()
    (case / "postProcessing/forces.dat").write_text("urans", encoding="utf-8")
    config = tmp_path / "solver.json"
    config.write_text(json.dumps({
        "steady_numerics": {
            "p_relaxation": 0.3, "U_relaxation": 0.7,
            "nuTilda_relaxation": 0.7,
        },
    }), encoding="utf-8")

    report = recover_short_urans_to_rans(
        tmp_path,
        variant="reference_uncut_validation_1m",
        alpha=0.0,
        solver_config=config,
    )

    assert report["status"] == "RANS_RESTART_PREPARED"
    assert report["latest_rans_iteration"] == 4519
    assert (case / "4519/U").read_text(encoding="utf-8") == "rans"
    assert not (case / "0.001").exists()
    pending = json.loads(
        (case / "steadyInitialization/pending_stage.json").read_text(encoding="utf-8")
    )
    assert pending["transition"]["maximum_iterations"] == 15000
    solution = (system / "fvSolution").read_text(encoding="utf-8")
    assert "fields { p 0.3; }" in solution
    assert "equations { U 0.7; nuTilda 0.7; }" in solution
    assert (case / "postProcessing/forces.dat").read_text(encoding="utf-8") == "rans"
