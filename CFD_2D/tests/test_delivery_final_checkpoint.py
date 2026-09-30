from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"CFD_2D/scripts"))
from ramair_2d_openfoam_case_writer import OpenFOAMCaseConfig, write_steady_initialization_templates


def test_short_rans_always_schedules_final_checkpoint(tmp_path):
    cfg = OpenFOAMCaseConfig(steady_max_iterations=80, steady_write_interval_iterations=2000)
    write_steady_initialization_templates(tmp_path, cfg, "airfoil_wall")
    text = (tmp_path/"system/steadyInitialization/controlDict").read_text()
    interval = int(re.search(r"writeInterval\s+(\d+);", text).group(1))
    assert 80 % interval == 0
    assert "purgeWrite      0;" in text
