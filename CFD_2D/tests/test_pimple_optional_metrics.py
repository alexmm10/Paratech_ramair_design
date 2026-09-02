from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "CFD_2D/scripts"))

from ramair_2d_convergence_analysis import compare_pimple_outer_correctors  # noqa: E402


def test_pimple_comparison_keeps_completed_runs_with_optional_none_metrics() -> None:
    report = compare_pimple_outer_correctors([
        {
            "nOuterCorrectors": 2,
            "mean_CL": 0.5,
            "mean_CD": 0.02,
            "dominant_strouhal": None,
            "cpu_seconds_per_step": None,
        },
        {
            "nOuterCorrectors": 3,
            "mean_CL": 0.51,
            "mean_CD": 0.021,
            "dominant_strouhal": None,
            "cpu_seconds_per_step": 2.0,
        },
    ])
    assert report["status"] == "COMPARISON_AVAILABLE"
    comparison = report["comparisons"][0]
    assert comparison["mean_CL_difference_percent"] is not None
    assert comparison["dominant_St_difference_percent"] is None
    assert comparison["cpu_step_ratio"] is None
    assert {item["metric"] for item in report["missing_optional_metrics"]} == {
        "dominant_strouhal",
        "cpu_seconds_per_step",
    }
