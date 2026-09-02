from __future__ import annotations

from ramair_2d_closed_experimental_mesh import default_closed_config
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
    assert opened["external_volume"]["extend_distance_max_chord"] == 50.0
    assert closed["external_volume"]["extend_distance_max_chord"] == 50.0


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
