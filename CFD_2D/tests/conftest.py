"""Portable generated geometry fixtures; never depend on the developer's cases."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="session")
def reference_geometry_project(tmp_path_factory):
    source = Path(__file__).resolve().parents[2]
    project = tmp_path_factory.mktemp("reference_geometry")
    (project / "preprocess_ramair_main.py").write_text("# fixture project marker\n")
    profiles = project / "Airfoil Profiles"
    profiles.mkdir()
    config = json.loads((source / "Application Support/Configurations/default_case_config.json").read_text())
    for key, relative in config["profile_inputs"].items():
        if key.endswith("profile"):
            shutil.copy2(source / relative, project / relative)
    config_path = project / "fixture_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    subprocess.run([sys.executable, str(source / "preprocess_ramair_main.py"),
                    "--config", str(config_path)], cwd=project, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
    subprocess.run([sys.executable, str(source / "CFD_2D/scripts/ramair_2d_profile_case_builder.py"),
                    "--case-root", str(project), "--variant", "reference_uncut", "--validate"],
                   cwd=project, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   text=True, timeout=120)
    from ramair_2d_scale_validation_geometry import build_scaled_variant
    build_scaled_variant(project)
    return project
