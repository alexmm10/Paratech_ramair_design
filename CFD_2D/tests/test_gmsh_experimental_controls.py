from __future__ import annotations

from ramair_2d_closed_experimental_mesh import default_closed_config
from ramair_2d_bump_matching import (
    feasible_automatic_divisions,
    match_extended_inlet_distribution,
    partition_composite_bump,
)
from ramair_2d_gmsh_experimental import (
    compare_openfoam_quality,
    normalize_geometry_to_total_chord,
)
from ramair_2d_open_experimental_mesh import default_config


def test_optional_extend_controls_are_off_by_default() -> None:
    opened = default_config()
    closed = default_closed_config()
    assert opened["external_volume"]["automatic_extend_enabled"] is False
    assert opened["internal_volume"]["automatic_extend_enabled"] is False
    assert opened["boundary_layer"]["manual_four_segment_bump_enabled"] is False
    assert closed["external_volume"]["automatic_extend_enabled"] is False
    assert opened["execution"]["mesh_smoothing"] == 1
    assert closed["execution"]["mesh_smoothing"] == 1
    assert opened["boundary_layer"]["te_segment_early_start_enabled"] is False
    assert closed["boundary_layer"]["te_segment_early_start_enabled"] is False
    assert opened["boundary_layer"]["leading_segment_extension_enabled"] is False
    assert closed["boundary_layer"]["leading_segment_extension_enabled"] is False
    assert opened["external_volume"]["extend_distance_max_chord"] == 50.0
    assert closed["external_volume"]["extend_distance_max_chord"] == 50.0


def test_extended_curved_segment_gets_enough_divisions_for_exact_matching() -> None:
    selected, warnings = feasible_automatic_divisions(
        {"te": 0.03, "upper": 0.90, "leading_or_inlet": 0.45, "lower": 0.90},
        {"te": 24, "upper": 320, "leading_or_inlet": 140, "lower": 320},
    )
    assert selected["leading_or_inlet"] > 140
    assert selected["te"] == 24
    assert warnings


def test_composite_bump_preserves_total_divisions_and_split_sizes() -> None:
    result = partition_composite_bump(
        {"upper_wall": 0.04, "virtual_inlet": 0.08, "lower_wall": 0.04},
        180,
        1.6,
    )
    sections = result["sections"]
    assert sum(item["divisions"] for item in sections.values()) == 180
    assert sections["upper_wall"]["last_size_m"] < sections["upper_wall"]["first_size_m"]
    assert sections["lower_wall"]["last_size_m"] > sections["lower_wall"]["first_size_m"]
    assert max(abs(value) for value in result["split_position_errors_m"]) < 0.001


def test_extended_inlet_matches_physical_wall_and_virtual_curve_sizes() -> None:
    result = match_extended_inlet_distribution(
        {
            "upper_wall_extension": 0.062,
            "virtual_inlet": 0.086,
            "lower_wall_extension": 0.010,
        },
        180,
        0.00118,
    )
    assert sum(item["divisions"] for item in result["sections"].values()) == 180
    assert max(result["interface_size_ratios"].values()) < 1.03
    assert abs(result["sections"]["upper_wall_extension"]["junction_size_m"] - 0.00118) < 1e-12
    assert abs(result["sections"]["lower_wall_extension"]["junction_size_m"] - 0.00118) < 1e-12
    assert result["sections"]["virtual_inlet"]["minimum_size_m"] > 0.0
    assert result["sections"]["upper_wall_extension"]["maximum_size_m"] >= result["sections"]["upper_wall_extension"]["minimum_size_m"]


def test_shared_total_chord_normalization_uses_one_similarity_transform() -> None:
    import numpy as np

    normalized, report = normalize_geometry_to_total_chord(
        {
            "closed": np.array([[-0.01, 0.0], [1.02, 0.0]]),
            "open": np.array([[0.10, 0.03], [0.90, -0.02]]),
        },
        chord=1.0,
        reference_groups=("closed",),
    )
    assert report["normalized_total_chord_m"] == 1.0
    assert normalized["closed"][0, 0] == 0.0
    assert normalized["closed"][-1, 0] == 1.0
    original_delta = np.array([0.80, -0.05])
    transformed_delta = normalized["open"][1] - normalized["open"][0]
    assert np.allclose(
        transformed_delta, original_delta * report["similarity_scale"]
    )


def test_openfoam_quality_comparison_rejects_new_failure() -> None:
    base = {
        "checkMesh_status": "OK",
        "checkMesh_min_cell_determinant": 0.01,
        "checkMesh_min_face_interpolation_weight": 0.20,
        "checkMesh_min_face_volume_ratio": 0.20,
        "checkMesh_max_non_orthogonality_deg": 30.0,
        "checkMesh_max_skewness": 1.0,
    }
    candidate = dict(base)
    candidate["checkMesh_min_face_interpolation_weight"] = 0.01
    compared = compare_openfoam_quality(base, candidate)
    assert compared["accepted"] is False
    assert "interpolation_weight" in compared["newly_failed_metrics"]


def test_openfoam_quality_comparison_accepts_clear_improvement_advisory() -> None:
    base = {
        "checkMesh_status": "OK",
        "checkMesh_min_cell_determinant": 0.0012,
        "checkMesh_min_face_interpolation_weight": 0.08,
        "checkMesh_min_face_volume_ratio": 0.08,
        "checkMesh_max_non_orthogonality_deg": 55.0,
        "checkMesh_max_skewness": 2.5,
    }
    candidate = {
        **base,
        "checkMesh_min_cell_determinant": 0.003,
        "checkMesh_min_face_interpolation_weight": 0.16,
        "checkMesh_min_face_volume_ratio": 0.16,
        "checkMesh_max_non_orthogonality_deg": 40.0,
        "checkMesh_max_skewness": 1.5,
    }
    compared = compare_openfoam_quality(base, candidate)
    assert compared["accepted"] is True
    assert compared["Q"] > 0.0
    assert "explicit user approval" in compared["note"]


def test_modern_ui_discretization_does_not_need_legacy_tangential_limits() -> None:
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    open_source = (root / "CFD_2D/app/open_experimental_mesh_page.py").read_text(encoding="utf-8")
    closed_source = (root / "CFD_2D/app/closed_experimental_mesh_page.py").read_text(encoding="utf-8")
    for source in (open_source, closed_source):
        assert '"Tamaño tangencial mínimo [c]"' not in source
        assert '"Tamaño tangencial máximo [c]"' not in source
        assert '"Nodos del cierre curvo del TE"' not in source
        assert '#### Discretización tangencial de pared' in source
        assert '"bump_split_progression"' in source
        assert '"manual_split_progression"' in source
        assert "automatic_split_progression" in source


def test_geometric_boundary_layer_fixes_layer_count_and_keeps_triangular_volume() -> None:
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    for relative in (
        "CFD_2D/scripts/ramair_2d_open_experimental_mesh.py",
        "CFD_2D/scripts/ramair_2d_closed_experimental_mesh.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert 'setNumber("Mesh.RecombineAll", 0)' in source
        assert '"Geometry.Tolerance", 1.0e-12' in source
        assert '"NbLayers", int(layer["layers"])' in source
        assert 'raise ValueError("; ".join(split_report["warnings"]))' not in source
