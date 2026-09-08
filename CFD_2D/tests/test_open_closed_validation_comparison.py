from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
sys.path.insert(0, str(SCRIPTS))

from ramair_2d_open_closed_comparison import (  # noqa: E402
    _aero_features,
    generate_open_closed_report,
    write_rans_urans_comparison,
)


def _write_mean(path: Path, cl: float, cd: float, cm: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"Cl": cl, "Cd": cd, "Cm": cm}]).to_csv(path, index=False)


def test_aerodynamic_features_are_derived_only_from_published_points() -> None:
    points = pd.DataFrame({
        "alpha_deg": [-4.0, 0.0, 4.0, 8.0, 12.0],
        "Cl": [-0.2, 0.2, 0.6, 1.0, 0.9],
        "Cd": [0.05, 0.03, 0.04, 0.08, 0.12],
        "Cm": [-0.02] * 5,
    })

    features = _aero_features(points)

    assert features["Cl_max"] == pytest.approx(1.0)
    assert features["alpha_Cl_max"] == pytest.approx(8.0)
    assert features["alpha_Cl_zero"] == pytest.approx(-2.0)
    assert features["Cl_alpha_per_deg"] == pytest.approx(0.1)
    assert features["alpha_stall"] == pytest.approx(8.0)


def test_rans_urans_products_use_only_angles_with_both_means(tmp_path: Path) -> None:
    published = pd.DataFrame({"alpha_deg": [0.0, 4.0]})
    base = tmp_path / "CFD_2D/results/open_ramair_validation_1m"
    for alpha_name, offset in (("alpha_p0p000", 0.0), ("alpha_p4p000", 0.1)):
        _write_mean(base / alpha_name / "RANS/forceCoeffs_RANS_mean.csv", 0.2 + offset, 0.02, -0.01)
        _write_mean(base / alpha_name / "URANS/forceCoeffs_mean.csv", 0.21 + offset, 0.021, -0.011)

    report = write_rans_urans_comparison(
        tmp_path, "open_ramair_validation_1m", published, tmp_path / "comparison",
    )

    assert report["status"] == "GENERATED"
    assert report["points"] == 2
    stats = pd.read_csv(tmp_path / "comparison/rans_urans_error_statistics.csv")
    assert set(stats["variable"]) == {"Cl", "Cd", "Cm", "L_D"}
    assert (tmp_path / "comparison/rans_urans_err_err2.png").is_file()


def test_open_closed_report_keeps_closed_and_open_registries_separate(tmp_path: Path) -> None:
    closed = (
        tmp_path / "CFD_2D/validation_studies/ls1_0417_closed_polar_M0p15_Re1p9e6"
        / "postprocess/validation/ramair_validation_points.csv"
    )
    opened = (
        tmp_path / "CFD_2D/validation_studies/open_closed_polar_M0p15_Re1p9e6"
        / "postprocess/comparison/open_validation_points.csv"
    )
    closed.parent.mkdir(parents=True)
    opened.parent.mkdir(parents=True)
    columns = {
        "alpha_deg": [-4.0, 0.0, 4.0, 8.0], "Cl": [-0.2, 0.2, 0.6, 1.0],
        "Cd": [0.05, 0.03, 0.04, 0.08], "Cm": [-0.02] * 4,
    }
    pd.DataFrame(columns).to_csv(closed, index=False)
    pd.DataFrame({**columns, "Cl": [-0.18, 0.18, 0.55, 0.9]}).to_csv(opened, index=False)

    output = generate_open_closed_report(tmp_path)

    summary = json.loads((output / "open_closed_summary.json").read_text())
    assert summary["closed_points"] == 4
    assert summary["open_points"] == 4
    assert (output / "open_closed_Cm_alpha.png").is_file()
    assert (output / "open_closed_aerodynamic_features.png").is_file()
