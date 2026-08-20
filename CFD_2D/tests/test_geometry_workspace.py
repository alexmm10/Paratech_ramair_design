from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ramair_geometry_workspace import (  # noqa: E402
    TE_LABELS,
    crossport_specs,
    geometry_dto,
    import_profile,
    load_profile_catalog,
)


def test_geometry_dto_preserves_generator_and_expands_per_hole_controls() -> None:
    project = {
        "profile_inputs": {"main_profile": "open.csv", "reference_uncut_profile": "closed.dat"},
        "airfoil_processing": {"te_closure_mode": "straight_gap"},
        "crossports": {
            "position_mode": "equidistant", "count": 2, "x_start_chord": 0.3, "x_end_chord": 0.7,
            "shape": "ellipse", "ellipse_orientation": "horizontal", "points_per_loop": 28,
        },
    }
    dto = geometry_dto(project)
    assert dto["trailing_edge"]["label"] == "No modification"
    assert dto["crossports"]["generator"] == {
        "position_mode": "equidistant", "count": 2, "x_start_chord": 0.3, "x_end_chord": 0.7,
    }
    assert [item["x"] for item in dto["crossports"]["holes"]] == [0.3, 0.7]
    assert all(item["points_per_loop"] == 28 for item in dto["crossports"]["holes"])


def test_custom_circle_keeps_radius_and_individual_discretization() -> None:
    specs = crossport_specs({
        "custom_specs": [{"x": 0.4, "shape": "circle", "radius_chord_frac": 0.02, "points_per_loop": 48}],
    })
    assert specs[0]["radius_chord_frac"] == 0.02
    assert specs[0]["points_per_loop"] == 48
    assert specs[0]["orientation"] == "horizontal"


def test_import_profile_preserves_original_and_catalogues_hash(tmp_path: Path) -> None:
    (tmp_path / "Airfoil Profiles").mkdir()
    content = b"x,z\n0,0\n0.5,0.1\n1,0.01\n1,-0.01\n0.5,-0.1\n0,0\n"
    entry = import_profile(tmp_path, "sample.csv", content, work_case_id="case-uuid")
    original = tmp_path / entry["source_path"]
    assert original.read_bytes() == content
    assert entry["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert entry["work_case_id"] == "case-uuid"
    catalogue = load_profile_catalog(tmp_path)
    assert any(item["profile_id"] == entry["profile_id"] for item in catalogue["profiles"])
    assert json.loads((tmp_path / "Airfoil Profiles/profile_catalog.json").read_text())["schema_version"] == 1


def test_te_labels_expose_internal_straight_gap_as_no_modification() -> None:
    assert TE_LABELS["straight_gap"] == "No modification"
