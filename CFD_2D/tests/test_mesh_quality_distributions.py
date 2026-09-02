from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ramair_2d_mesh_quality_distributions import generate_quality_distributions  # noqa: E402
import ramair_2d_open_experimental_mesh as mesh_module  # noqa: E402
from ramair_2d_closed_experimental_mesh import (  # noqa: E402
    default_closed_config,
    load_closed_geometry,
)
from ramair_2d_open_experimental_mesh import (  # noqa: E402
    _canonical_base_inlet,
    _hermite_bridge,
    approve,
    generate,
)
from boundary_layer_estimates import (  # noqa: E402
    beta_law_coefficient,
    beta_law_cumulative_distances,
    first_cell_height_from_yplus,
    geometric_layers_for_thickness,
    turbulent_flat_plate_delta99,
)


def test_experimental_mesh_rejects_a_missing_explicit_config(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        generate(tmp_path, tmp_path / "missing.json", "test", False)


def test_inlet_hermite_bridge_keeps_local_handles_bounded() -> None:
    bridge = _hermite_bridge(
        np.asarray([0.0, 0.0]),
        np.asarray([0.04, 0.0]),
        np.asarray([0.002, 0.001]),
        np.asarray([0.002, -0.001]),
        41,
        tangent_scale=1.0,
    )
    assert np.allclose(bridge[0], [0.0, 0.0])
    assert np.allclose(bridge[-1], [0.04, 0.0])
    assert np.all(np.linalg.norm(np.diff(bridge, axis=0), axis=1) > 1.0e-12)
    # Local tangents must not be replaced by a handle proportional to the
    # complete lip-to-guide distance, which would create a large overshoot.
    assert float(np.max(np.abs(bridge[:, 1]))) < 0.0015


def test_polyline_cleaner_removes_nonconsecutive_coincident_points() -> None:
    points = np.asarray([[0.0, 0.0], [0.2, 0.0], [0.0, 0.0], [1.0, 0.0]])
    cleaned = mesh_module._clean_polyline(points, 1.0e-8)
    assert cleaned.tolist() == [[0.0, 0.0], [0.2, 0.0], [1.0, 0.0]]


def test_canonical_inlet_preserves_the_exact_base_segment() -> None:
    wall = np.asarray([
        [0.0, 0.03], [0.08, 0.04], [1.0, 0.0], [0.08, -0.04], [0.04, -0.03],
    ])
    base = np.asarray([
        [0.01, 0.025], [0.0, 0.01], [0.0, 0.0], [0.01, -0.015], [0.04, -0.028],
    ])
    inlet, report = _canonical_base_inlet(
        base, wall, chord=1.0, blend_length_chord=0.02,
        tolerance=1.0e-10, tangent_scale=0.4,
    )
    start = int(report["base_start_index"])
    end = int(report["base_end_index"])
    upper_anchor = int(report["upper_anchor_index"])
    lower_anchor = int(report["lower_anchor_index"])
    assert np.allclose(inlet[start : end + 1], base[upper_anchor : lower_anchor + 1])
    assert report["base_guide_exact_between_connectors"] is True
    assert np.allclose(inlet[0], wall[0])
    assert np.allclose(inlet[-1], wall[-1])


def test_closed_experimental_wall_is_one_connected_loop() -> None:
    root = Path(__file__).resolve().parents[2]
    geometry = load_closed_geometry(root, default_closed_config())
    segments = [
        geometry["upper_body"], geometry["leading_edge"],
        geometry["lower_body"], geometry["te_cap"],
    ]
    for current, following in zip(segments, segments[1:] + segments[:1]):
        assert np.linalg.norm(current[-1] - following[0]) < 1.0e-12
    loop = np.vstack([segment[:-1] for segment in segments])
    rounded = np.round(loop, 13)
    assert len(np.unique(rounded, axis=0)) == len(loop)
    assert geometry["identity"]["single_closed_wall"] is True
    assert geometry["identity"]["fluid_inside_airfoil"] is False


def test_experimental_mesh_cannot_be_approved_without_checkmesh_ok(tmp_path: Path) -> None:
    revision = (
        tmp_path
        / "CFD_2D/experimental_meshes/open_reference_from_scratch/revisions/review"
    )
    revision.mkdir(parents=True)
    (revision / "mesh_final.msh").write_text("placeholder", encoding="ascii")
    (revision / "mesh_report.json").write_text(
        json.dumps({"checkMesh_status": "FAIL"}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="checkMesh did not finish with OK"):
        approve(tmp_path, "review")


def test_quality_distributions_count_cells_and_split_topologies(tmp_path: Path) -> None:
    mesh = tmp_path / "two_hexes.msh"
    mesh.write_text(
        """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
12
1 0 0 0
2 1 0 0
3 1 1 0
4 0 1 0
5 0 0 1
6 1 0 1
7 1 1 1
8 0 1 1
9 2 0 0
10 2 1 0
11 2 0 1
12 2 1 1
$EndNodes
$Elements
2
1 5 0 1 2 3 4 5 6 7 8
2 5 0 2 9 10 3 6 11 12 7
$EndElements
""",
        encoding="ascii",
    )
    output = tmp_path / "quality"
    report = generate_quality_distributions(mesh, output)

    assert report["cell_count"] == 2
    assert report["boundary_layer_hex_cells"] == 2
    assert report["unstructured_prism_cells"] == 0
    for rows in report["distributions"].values():
        if rows:
            assert sum(int(row["count"]) for row in rows) in {0, 2}
    extrema = report["computed_extrema"]
    assert extrema["minimum_face_interpolation_weight"] > 0.0
    assert extrema["minimum_face_volume_ratio"] == 1.0
    assert (output / "quality_distributions.csv").is_file()
    assert json.loads((output / "quality_distributions.json").read_text())["cell_count"] == 2
    assert len(list(output.glob("quality_table_*.png"))) == 9
    assert (output / "quality_table_mesh_basic_characteristics.png").is_file()
    statistics = report["quality_statistics"]
    assert statistics["interpolation_weight"]["minimum"] > 0.0
    assert statistics["interpolation_weight"]["mean"] > 0.0
    assert statistics["volume_ratio"]["minimum"] == 1.0
    assert statistics["volume_ratio"]["mean"] == 1.0


def test_schlichting_beta_law_matches_required_validation_case() -> None:
    estimate = first_cell_height_from_yplus(
        target_y_plus=1.0,
        reynolds=1_900_000.0,
        rho_kg_m3=0.66606662,
        mu_pa_s=1.7894e-5,
        chord_m=1.0,
    )
    delta = turbulent_flat_plate_delta99(
        chord_m=1.0, reynolds_chord=1_900_000.0, x_over_chord=1.0
    )
    thickness = 1.20 * delta
    beta = beta_law_coefficient(
        first_cell_height_m=estimate["y1_m"],
        total_thickness_m=thickness,
        layers=75,
    )
    coordinates = beta_law_cumulative_distances(
        first_cell_height_m=estimate["y1_m"], beta=beta, layers=75
    )

    assert estimate["first_cell_centre_distance_m"] == pytest.approx(12.8515e-6, rel=2e-5)
    assert estimate["y1_m"] == pytest.approx(25.7031e-6, rel=2e-5)
    assert delta == pytest.approx(0.02053293, rel=2e-7)
    assert thickness == pytest.approx(0.02463952, rel=2e-7)
    assert beta == pytest.approx(1.01573622, rel=2e-7)
    assert coordinates[0] == pytest.approx(estimate["y1_m"], rel=1e-10)
    assert coordinates[-1] == pytest.approx(thickness, rel=1e-10)


def test_geometric_layer_count_is_rounded_up_to_cover_target() -> None:
    result = geometric_layers_for_thickness(
        first_cell_height_m=25.0e-6,
        growth_rate=1.10,
        minimum_thickness_m=0.025,
    )
    assert result["layers"] == 49
    assert result["total_thickness_m"] >= 0.025
