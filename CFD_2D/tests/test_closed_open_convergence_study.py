from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
sys.path.insert(0, str(SCRIPTS))

from ramair_2d_closed_open_convergence_study import PRESETS, verify_series  # noqa: E402


def read_preset(topology: str, level: str) -> dict[str, object]:
    return json.loads((ROOT / PRESETS[(topology, level)]).read_text(encoding="utf-8"))


def test_open_convergence_presets_refine_monotonically() -> None:
    coarse = read_preset("open", "coarse")
    medium = read_preset("open", "medium")
    fine = read_preset("open", "fine")

    assert [
        coarse["open_boundary_layer_layers"],
        medium["open_boundary_layer_layers"],
        fine["open_boundary_layer_layers"],
    ] == [35, 50, 65]
    assert (
        coarse["open_nearfield_intermediate_size_chord"]
        > medium["open_nearfield_intermediate_size_chord"]
        > fine["open_nearfield_intermediate_size_chord"]
    )
    assert (
        coarse["open_farfield_size_chord"]
        > medium["open_farfield_size_chord"]
        > fine["open_farfield_size_chord"]
    )
    assert coarse["open_first_cell_height_m"] == medium["open_first_cell_height_m"]
    assert medium["open_first_cell_height_m"] == fine["open_first_cell_height_m"]


def accepted_row(topology: str, level: str, cells: int) -> dict[str, object]:
    return {
        "id": f"{topology}_{level}",
        "topology": topology,
        "level": level,
        "cell_count": cells,
        "accepted": True,
        "acceptance_failures": [],
    }


def test_convergence_series_requires_strict_cell_count_order() -> None:
    rows = [
        accepted_row(topology, level, cells)
        for topology in ("closed", "open")
        for level, cells in zip(("coarse", "medium", "fine"), (100, 200, 300))
    ]
    verify_series(rows)
    rows[-1]["cell_count"] = 150
    with pytest.raises(RuntimeError, match="not strictly increasing"):
        verify_series(rows)
