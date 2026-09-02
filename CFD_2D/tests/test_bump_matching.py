from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ramair_2d_bump_matching import (  # noqa: E402
    bump_cell_sizes,
    match_four_segment_bumps,
)
from ramair_2d_closed_experimental_mesh import (  # noqa: E402
    default_closed_config,
    load_closed_geometry,
)
from ramair_2d_split_progression import (  # noqa: E402
    automatic_split_progression,
    progression_length,
    solve_progression_ratio,
)


def test_bump_distribution_is_symmetric_and_conserves_length() -> None:
    for coefficient in (0.077195, 1.03098, 1.42842):
        sizes = bump_cell_sizes(coefficient, 0.7, 120)
        assert sizes.sum() == pytest.approx(0.7, rel=1.0e-10)
        assert sizes[0] == pytest.approx(sizes[-1], rel=1.0e-9)
        assert np.all(sizes > 0.0)


def test_four_segment_match_has_continuous_interfaces() -> None:
    result = match_four_segment_bumps(
        {
            "te": 0.011,
            "upper": 1.00,
            "leading_or_inlet": 0.084,
            "lower": 0.99,
        },
        {"te": 22, "upper": 220, "leading_or_inlet": 120, "lower": 220},
        chord=1.0,
        maximum_growth_ratio=1.2,
        maximum_size_percent_chord=1.2,
    )
    assert result["coefficients"]["te"] > 1.0
    assert result["coefficients"]["leading_or_inlet"] > 1.0
    assert 0.0 < result["coefficients"]["upper"] < 1.0
    assert 0.0 < result["coefficients"]["lower"] < 1.0
    assert max(abs(value - 1.0) for value in result["interface_ratios"].values()) < 1.0e-5


def test_closed_le_curvature_segment_starts_before_the_fixed_legacy_split() -> None:
    if not (ROOT / "CFD_2D/CFD_2D_inputs/geometry/reference_uncut_validation_1m/profile_points.csv").is_file():
        pytest.skip("Canonical validation geometry is not present in this checkout")
    geometry = load_closed_geometry(ROOT, default_closed_config())
    assert len(geometry["leading_edge"]) > 16
    assert geometry["leading_edge"][0, 0] > 0.015
    assert geometry["leading_edge"][-1, 0] > 0.015
    assert geometry["identity"]["leading_edge_segmentation"]["method"] == "smoothed_curvature_fraction"


def test_four_segment_match_blocks_impossible_division_interval() -> None:
    with pytest.raises(ValueError, match="No common tangential junction size"):
        match_four_segment_bumps(
            {"te": 0.1, "upper": 1.0, "leading_or_inlet": 0.2, "lower": 1.0},
            {"te": 10, "upper": 1000, "leading_or_inlet": 10, "lower": 1000},
            chord=1.0,
            maximum_growth_ratio=1.1,
            maximum_size_percent_chord=1.0,
        )


def test_progression_ratio_reconstructs_exact_segment_length() -> None:
    ratio = solve_progression_ratio(0.5, 120, 0.001)
    assert ratio > 1.0
    assert progression_length(0.001, ratio, 120) == pytest.approx(0.5, rel=1.0e-9)


def test_automatic_split_progression_preserves_total_divisions() -> None:
    result = automatic_split_progression(
        half_lengths={
            "upper": {"leading_or_inlet": 0.50, "te": 0.50},
            "lower": {"leading_or_inlet": 0.50, "te": 0.50},
        },
        body_divisions={"upper": 900, "lower": 850},
        curved_lengths={"leading_or_inlet": 0.08, "te": 0.025},
        curved_divisions={"leading_or_inlet": 120, "te": 60},
        curved_bumps={"leading_or_inlet": 1.02, "te": 1.20},
        chord=1.0,
        maximum_growth_ratio=1.10,
        maximum_size_percent_chord=0.5,
    )
    split = result["split_divisions"]
    assert split["upper_leading_or_inlet"] + split["upper_te"] == 900
    assert split["lower_leading_or_inlet"] + split["lower_te"] == 850
    assert result["maximum_growth_ratio"] <= 1.10
    assert result["maximum_size_percent_chord"] <= 0.5
