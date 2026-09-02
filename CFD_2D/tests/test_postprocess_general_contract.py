from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from paraview_case_viewer import write_automatic_products_script  # noqa: E402
from ramair_2d_postprocess import (  # noqa: E402
    available_postprocess_fields,
    readable_axis_limits,
    select_final_force_window,
    summarize_force_coeffs,
)
from ramair_2d_postprocess_registry import write_postprocess_manifest  # noqa: E402


def test_final_force_window_uses_last_continuous_segment() -> None:
    history = pd.DataFrame(
        {
            "Time": [0.0, 0.1, 0.2, 0.3, 5.0, 5.1, 5.2, 5.3, 5.4, 5.5],
            "Cl": [0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            "Cd": [0.1] * 10,
            "Cm": [0.0] * 10,
        }
    )
    window, evidence = select_final_force_window(history, 0.5)
    assert evidence["detected_large_gaps"] == 1
    assert evidence["continuous_segment_start_time"] == pytest.approx(5.0)
    assert window["Time"].min() >= 5.1
    assert evidence["selected_window_start_time"] == float(window["Time"].min())


def test_final_force_window_retains_auditable_minimum_sample_count() -> None:
    history = pd.DataFrame(
        {"Time": [float(index) for index in range(20)], "Cl": range(20), "Cd": [1.0] * 20}
    )
    window, evidence = select_final_force_window(history, 0.99)
    assert len(window) == 5
    assert evidence["minimum_samples_override_applied"] is True
    summarized, _, _ = summarize_force_coeffs(history, 0.99)
    assert summarized.attrs["window_manifest"]["selected_samples"] == 5


def test_readable_force_axis_has_padding_for_constant_and_varying_data() -> None:
    assert readable_axis_limits(pd.Series([2.0, 2.0])) == pytest.approx((1.84, 2.16))
    lower, upper = readable_axis_limits(pd.Series([-0.1, 0.2]))
    assert lower < -0.1 and upper > 0.2


def test_postprocess_field_selection_uses_only_latest_relevant_arrays(tmp_path: Path) -> None:
    older = tmp_path / "0.1"
    latest = tmp_path / "0.2"
    older.mkdir()
    latest.mkdir()
    (older / "temperature").write_text("old", encoding="utf-8")
    for name in ("U", "p.gz", "Q", "irrelevantField"):
        (latest / name).write_text("field", encoding="utf-8")
    assert available_postprocess_fields(tmp_path) == ["U", "p", "Q"]


def test_postprocess_manifest_schema3_uses_manifest_relative_paths(tmp_path: Path) -> None:
    output = tmp_path / "results/run"
    product = output / "plots/force.png"
    product.parent.mkdir(parents=True)
    product.write_bytes(b"png")
    manifest = write_postprocess_manifest(
        output,
        run_id="run",
        mode="URANS",
        products=[product],
    )
    assert manifest["schema_version"] == 3
    assert manifest["path_base"] == "manifest_directory"
    assert manifest["products"][0]["path"] == "plots/force.png"
    assert not Path(manifest["products"][0]["path"]).is_absolute()


def test_paraview_script_contains_portable_state_and_complete_views(tmp_path: Path) -> None:
    marker = tmp_path / "case/case.foam"
    marker.parent.mkdir()
    marker.touch()
    script = write_automatic_products_script(
        tmp_path / "results/render.py",
        marker,
        tmp_path / "results/products",
        chord_m=1.0,
        velocity_m_s=10.0,
        alpha_deg=8.0,
        maximum_courant=25.0,
        maximum_frames=4,
        time_semantics="physical seconds",
        stage_label="URANS",
        time_range_s=(0.2, 0.8),
    )
    text = script.read_text(encoding="utf-8")
    assert 'registrationName="VelocityMagnitudeContours"' in text
    assert 'color_scalar_field(vorticity_name, vector_magnitude=True)' in text
    assert '"yPlus_%s_final.png"' not in text
    assert 'if include_animations else ["final_images"]' in text
    assert 'RAMAIR_PVBATCH_SAVE_STATE' in text
    assert 'disabled_for_automatic_batch_to_avoid_WSL_SaveState_stall' in text
    assert 'products["case_reference"] = relative_foam_path' in text
    assert "requested_time_range_s = [0.2, 0.8]" in text
    assert 'display.Representation = "Surface With Edges"' in text
    assert 'registrationName="PositiveQRegions"' in text
    assert 'products["q_positive_filter"] = "Threshold(Q >= 0) for one-cell-thick 2-D mesh"' in text
    assert 'registrationName="HighVorticity"' in text
    compile(text, str(script), "exec")


def test_static_paraview_mode_uses_only_latest_time_for_scales_and_defers_frames(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "case/case.foam"
    marker.parent.mkdir()
    marker.touch()
    script = write_automatic_products_script(
        tmp_path / "results/render_static.py",
        marker,
        tmp_path / "results/products",
        chord_m=1.0,
        velocity_m_s=10.0,
        alpha_deg=12.0,
        maximum_courant=50.0,
        maximum_frames=24,
        time_semantics="physical seconds",
        stage_label="URANS",
        include_animations=False,
    )
    text = script.read_text(encoding="utf-8")
    assert "include_animations = False" in text
    assert "range_times = selected_times if include_animations else selected_times[-1:]" in text
    assert 'products["animation_generation"] = "included" if include_animations else "deferred"' in text
    compile(text, str(script), "exec")
