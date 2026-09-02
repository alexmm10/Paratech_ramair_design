from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ramair_2d_mesh_numerics import (  # noqa: E402
    automatic_non_orthogonal_controls,
    quality_controls_for_mesh,
)


@pytest.mark.parametrize(
    ("angle", "correctors", "laplacian"),
    [
        (49.999, 0, "Gauss linear corrected"),
        (50.0, 1, "Gauss linear corrected"),
        (69.999, 1, "Gauss linear corrected"),
        (70.0, 2, "Gauss linear limited 0.5"),
        (84.0, 2, "Gauss linear limited 0.5"),
    ],
)
def test_non_orthogonal_policy(angle: float, correctors: int, laplacian: str) -> None:
    result = automatic_non_orthogonal_controls(angle)
    assert result["n_non_orthogonal_correctors"] == correctors
    assert result["laplacian_scheme"] == laplacian


def test_quality_controls_prefer_checked_json(tmp_path: Path) -> None:
    (tmp_path / "mesh_quality_report.json").write_text(
        json.dumps({"checkMesh_max_non_orthogonality_deg": 65.2}),
        encoding="utf-8",
    )
    result = quality_controls_for_mesh(tmp_path)
    assert result is not None
    assert result["n_non_orthogonal_correctors"] == 1
    assert result["source_kind"] == "quality_json"


def test_quality_controls_read_saved_mesh_package_layout(tmp_path: Path) -> None:
    mesh_data = tmp_path / "Mesh Data"
    mesh_data.mkdir()
    (mesh_data / "mesh_quality_report.json").write_text(
        json.dumps({"checkMesh_max_non_orthogonality_deg": 41.6}),
        encoding="utf-8",
    )
    result = quality_controls_for_mesh(tmp_path)
    assert result is not None
    assert result["n_non_orthogonal_correctors"] == 0
    assert Path(result["source"]).parent.name == "Mesh Data"
