from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"CFD_2D/scripts"))
import pandas as pd
import pytest
import ramair_rans_mesh_study as study
from ramair_workspace import write_json, read_json


def test_signature_keeps_physics_but_not_operational_controls():
    base = {"turbulence":"kOmegaSST", "schemes":{"U":"bounded linearUpwind"}, "force_reference":{"area":1}}
    assert study.solver_signature(base) == study.solver_signature({**base,"execution_controls":{"phase_action":"rans_only"}})
    assert study.solver_signature(base) != study.solver_signature({**base,"force_reference":{"area":2}})
    assert study.solver_signature(base) != study.solver_signature({**base,"turbulence":"laminar"})


def test_compatibility_includes_geometry_revision_and_solver(monkeypatch,tmp_path):
    context = {"geometry_id":"g", "geometry_revision":"r", "physical_revision":"p"}
    monkeypatch.setattr(study,"geometry_context",lambda p: context)
    monkeypatch.setattr(study,"compatible_mesh",lambda *a:(True,"OK"))
    setup=tmp_path/"Meshes/m/OpenFOAM/s"
    write_json(setup/"solver_setup.json",{"context":context})
    write_json(setup/"solver_config.json",{"turbulence":"sst"})
    assert study.compatible_setup(setup,tmp_path,{"turbulence":"sst"})[0]
    assert not study.compatible_setup(setup,tmp_path,{"turbulence":"laminar"})[0]
    write_json(setup/"solver_setup.json",{"context":dict(context,geometry_revision="changed")})
    assert not study.compatible_setup(setup,tmp_path,{"turbulence":"sst"})[0]


def test_analysis_uses_common_angles_and_zero_denominators_excludes_diagnostic_gci(monkeypatch,tmp_path):
    rows=[]
    for mesh,cells,angles in (("coarse",100,[0,8]),("medium",200,[0,8]),("fine",400,[0])):
        for alpha in angles:
            rows.append(dict(mesh=mesh,cells=cells,alpha=alpha,state="postprocesado",Cl=0. if alpha==0 else .8,Cd=.04,Cm=0.,
                **{"Cl/Cd":0. if alpha==0 else 20.},scientific_accepted=False,source_sha256="test"))
    monkeypatch.setattr(study,"matrix",lambda folder:pd.DataFrame(rows))
    frame,differences,gci=study.analyze(tmp_path)
    assert set(differences.alpha)=={0}
    assert differences.Cl_percent.isna().all()
    assert differences.Cm_percent.isna().all()
    assert not gci["0"]["eligible"]
    assert read_json(tmp_path/"Analysis/analysis.json")["reference_mesh"]=="fine"
    assert len(list((tmp_path/"Analysis").glob("*.png")))==12


def test_remote_rans_resume_never_starts_transient(monkeypatch,tmp_path):
    import ramair_remote_queue_runner as remote
    monkeypatch.setattr(remote,"ROOT",tmp_path)
    case=tmp_path/"case"
    write_json(case/"steadyInitialization/pending_stage.json",{"latest_iteration":80})
    command=remote.staged_command({"case":"case", "execution_mode":"rans_only", "renumber_before_decompose":False},True)
    assert "--steady-only" in command and "--resume" in command
    assert command[command.index("--steady-decision")+1]=="extend"
    assert "--no-renumber-before-decompose" in command
    assert "--resume-additional-time-star" not in command
