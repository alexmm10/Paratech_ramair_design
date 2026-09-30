from pathlib import Path
import importlib.util
import sys
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"CFD_2D/scripts"))
from ramair_crossports import explicit_hole_loop
from ramair_2d_inlet_designer import DEFAULT_CONFIG, _polar_commands, _cp_commands, validate_design_config


def test_explicit_rotated_ellipse_keeps_size_position_and_rotation():
    upper = np.array([[0., .2], [1., .2]])
    lower = np.array([[0., -.2], [1., -.2]])
    loop, meta = explicit_hole_loop(dict(shape="ellipse", x=.4, width_chord_frac=.2,
        height_chord_frac=.06, reference_line="chord", z_offset_chord=.01, angle_deg=45.), upper, lower)
    np.testing.assert_allclose(loop.mean(axis=0), [.4, .01], atol=1e-12)
    np.testing.assert_allclose(loop[0], [.4+.1/np.sqrt(2), .01+.1/np.sqrt(2)])
    assert meta["width"] == .2 and meta["height"] == .06
    with pytest.raises(ValueError, match="No se han alterado"):
        explicit_hole_loop(dict(shape="circle", x=.4, width_chord_frac=.6), upper, lower)


def test_explicit_empty_table_really_generates_no_holes():
    spec = importlib.util.spec_from_file_location("design_test_preprocessor", ROOT/"preprocess_ramair_main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from dataclasses import replace
    cfg = replace(module.build_config_from_user_settings(), enable_crossports=True,
                  crossport_position_mode="custom_table", crossport_custom_specs=())
    upper, lower = pd.DataFrame({"x": [0., 1.], "y": [.2, .2]}), pd.DataFrame({"x": [0., 1.], "y": [-.2, -.2]})
    assert module.make_crossport_table(cfg, upper, lower).empty
    cfg = replace(cfg, crossport_custom_specs=(dict(hole_id="CP1", shape="circle", x=.3, width_chord_frac=.03),))
    frame = module.make_crossport_table(cfg, upper, lower)
    assert len(frame) == cfg.crossport_points_per_loop
    assert frame.width_chord_norm.iloc[0] == .03


def test_polar_and_cp_share_transition_commands():
    cfg = dict(DEFAULT_CONFIG, ncrit=1., xtrip_upper=.01, xtrip_lower=.02)
    validate_design_config(cfg)
    for text in (_polar_commands(cfg), _cp_commands(cfg, [0., 4.])):
        assert "VPAR\nN 1\nXTR 0.01 0.02\n\n" in text
    with pytest.raises(ValueError): validate_design_config(dict(cfg, xtrip_upper=1.1))


def test_manual_network_rejects_disconnected_anchors_and_cycles():
    from ramair_manual_rigging import apply_manual_network
    from types import SimpleNamespace
    node = lambda name, kind, x, y, z, *args, **kwargs: SimpleNamespace(node_id=name, p=np.array([x,y,z]), bank="A", side="L")
    anchor = node("A1", "anchor", 0., 0., 0.)
    network = SimpleNamespace(cfg={"manual_network": {"nodes": [dict(node_id="R", node_type="riser", x=0., y=0., z=-1.)], "segments": [dict(start="R", end="A1")]}},
        anchor_nodes={"A1": anchor}, nodes={"A1": anchor}, segments=[])
    with pytest.raises(ValueError, match="termina en otro anclaje"):
        apply_manual_network(network, node)
    network.cfg["manual_network"]["segments"] = [dict(start="A1", end="R"), dict(start="R", end="A1")]
    with pytest.raises(ValueError, match="ciclo"):
        apply_manual_network(network, node)
