"""Behavioral contracts for Studio API 28; no solver is launched by pytest."""
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from ramair_workspace import (
    create_workspace, create_cfd_case, physical_conditions, geometry_context, bind_mesh_config,
    parametric_mesh_config, write_json, read_json, compatible_mesh, parse_angles, alpha_name,
    save_mesh_preset, comparable_setups, export_products, solver_defaults,
)
from ramair_profile_library import process_profile, import_geometry, _branches
from ramair_polar_comparison import available_point, polar_metrics

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def profile_case(tmp_path):
    x=(1-np.cos(np.linspace(0,np.pi,100)))/2
    z=.12*np.sqrt(x)*(1-x)+.002*x
    source=tmp_path/"base.dat"
    np.savetxt(source,np.vstack([np.column_stack([x,z])[::-1],np.column_stack([x,-.8*z])[1:]]))
    profile=process_profile(tmp_path,source,"reference","rounded",reference=True,topology="closed")
    workspace=create_workspace(tmp_path,"study")
    cfd=create_cfd_case(workspace,"ReMach",physical_conditions())
    geometry=import_geometry(cfd,profile,"closed")
    return tmp_path,profile,workspace,cfd,geometry

def test_physical_state_matches_both_reynolds_and_mach():
    p=physical_conditions()
    assert p["velocity_m_s"]/p["speed_of_sound_m_s"]==pytest.approx(.15)
    assert p["rho"]*p["velocity_m_s"]*p["chord_m"]/p["mu"]==pytest.approx(1.9e6)
    assert p["rho"]==pytest.approx(1.22501226599)
    assert p["chord_m"]==1

@pytest.mark.parametrize("value", [0,-1,float("nan"),float("inf")])
def test_nonphysical_cfd_inputs_are_rejected(value):
    with pytest.raises(ValueError):physical_conditions(reynolds=value)

def test_processed_te_has_unit_chord_unique_segments_and_exact_nose(profile_case):
    root,profile,*_=profile_case
    frame=pd.read_csv(profile/"profile_points.csv")
    assert frame.x_m.max()==pytest.approx(1)
    assert frame.x_m.min()==pytest.approx(0)
    u,l,opened=_branches(profile/"profile_points.csv")
    assert not opened
    assert np.array_equal(u[0],l[0])
    cap=frame[frame.source_section=="TE_ROUNDED_CAP"][["x_m","z_m"]].to_numpy()
    assert len(cap)==33
    assert np.all(np.linalg.norm(np.diff(cap,axis=0),axis=1)>1e-12)
    original_u,original_l,_=_branches(profile/"original.dat","closed")
    assert np.array_equal(original_u[0],original_l[0])
    info=read_json(profile/"profile.json")
    assert info["te"]["method"]=="trimmed_C1_cubic_cap"

def test_parametric_preset_does_not_export_fluid_or_geometry_identity(profile_case):
    root,profile,workspace,cfd,geometry=profile_case
    from ramair_2d_closed_experimental_mesh import default_closed_config
    config=bind_mesh_config(default_closed_config(),geometry)
    saved=save_mesh_preset(root,config,"reusable","medium")
    preset=read_json(saved)
    assert "_context" not in preset
    assert not {"rho_kg_m3","mu_pa_s","reynolds","chord_m","input_directory"} & preset["geometry"].keys()
    rebound=bind_mesh_config(preset,geometry)
    assert rebound["geometry"]["mu_pa_s"]==pytest.approx(physical_conditions()["mu"])
    with pytest.raises(FileExistsError):save_mesh_preset(root,config,"reusable","medium")

def test_geometry_changes_invalidate_context(profile_case):
    root,profile,workspace,cfd,geometry=profile_case
    previous=geometry_context(geometry)
    points=geometry/"Inputs/profile/profile_points.csv"
    data=pd.read_csv(points);data.loc[4,"z_m"]+=.0001;data.to_csv(points,index=False)
    assert geometry_context(geometry)["geometry_revision"]!=previous["geometry_revision"]

def test_unapproved_or_foreign_mesh_never_activates(profile_case):
    root,profile,workspace,cfd,geometry=profile_case
    mesh=geometry/"Meshes/not_generated";mesh.mkdir()
    assert not compatible_mesh(mesh,geometry)[0]
    from ramair_2d_closed_experimental_mesh import default_closed_config
    write_json(mesh/"mesh_config.json",bind_mesh_config(default_closed_config(),geometry))
    write_json(mesh/"mesh_report.json",{"checkMesh_status":"FAILED"})
    assert compatible_mesh(mesh,geometry)[1]=="Requiere checkMesh OK."

def test_only_same_cfd_identity_and_conditions_are_comparable(tmp_path):
    a,b=tmp_path/"a",tmp_path/"b"
    context={"cfd_case_id":"one","physical_revision":"same"}
    write_json(a/"solver_setup.json",{"context":context})
    write_json(b/"solver_setup.json",{"context":context})
    assert comparable_setups(a,b)[0]
    write_json(b/"solver_setup.json",{"context":dict(context,physical_revision="changed")})
    assert not comparable_setups(a,b)[0]

def test_angle_selection_has_no_filename_aliases():
    assert parse_angles("range","",-2,2,1)==[-2,-1,0,1,2]
    assert parse_angles("list","8, 4;8 0")==[0,4,8]
    assert alpha_name(-2.5)=="alpha_m2p500"
    with pytest.raises(ValueError):parse_angles("list","1.0001")
    with pytest.raises(ValueError):parse_angles("range","",0,20,0)

def test_smoke_results_cannot_be_accepted_as_polar(tmp_path):
    write_json(tmp_path/"solver_config.json",{"workflow_test":{"purpose":"smoke"}})
    with pytest.raises(ValueError,match="parcial"):available_point(tmp_path,8)

def test_polar_metrics_do_not_invent_stall():
    f=pd.DataFrame({"alpha":[0.,4.,8.],"Cl":[.2,.6,1.],"Cd":[.02,.03,.04],"Cm":[-.1]*3})
    result=polar_metrics(f)
    assert result["Cl_alpha_per_deg"]==pytest.approx(.1)
    assert result["alpha_Cl0_deg"]==pytest.approx(-2)
    assert result["alpha_stall_approx_deg"] is None

def test_verified_export_preserves_source(tmp_path):
    setup=tmp_path/"setup"
    source=write_json(setup/"Angles/alpha_p8p000/Postprocess/case_summary.json",{"status":"PROCESSED_PARTIAL"})
    destination=tmp_path/"Windows_products"
    result=export_products(setup,destination,["coefficients"])
    assert result["files"]==1
    assert source.is_file()
    assert read_json(destination/"retention_manifest.json")["source_preserved"]

def test_solver_validation_contract_is_adjustable():
    solver,plan=solver_defaults(ROOT)
    assert solver["workspace_solver_contract"]
    assert "validation_polar_protocol" not in solver
    assert solver["deltaT_star"]==.0025
    assert [p["stage"] for p in plan["stages"]]==list("ABCDE")
    assert sum(p["duration_time_star"] for p in plan["stages"])==64


def test_short_phases_are_not_skipped_at_zero_and_each_gets_steps(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import ramair_2d_openfoam_staged_runner as runner
    import ramair_2d_validation_staged_runner as phase_writer
    case=tmp_path / "case";case.mkdir()
    write_json(case / "case_config.json", {"chord_m":1.,"velocity_m_s":51.})
    phases=[{"stage":name,"scheme":"Euler","duration_time_star":.0002,"dt_factor":.25} for name in "ABC"]
    plan=write_json(tmp_path / "plan.json", {"target_deltaT_star":.0025,"stages":phases})
    configured=[]
    def configure(case, stage, **kwargs):
        configured.append(stage)
        return stage
    def execute(*args, **kwargs):
        (case / str(configured[-1]["end_s"])).mkdir()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(phase_writer,"configure_stage",configure)
    monkeypatch.setattr(runner,"runner_command",lambda *args, **kwargs: [])
    monkeypatch.setattr(runner.subprocess,"run",execute)
    code,reports=runner.run_transient_phase_plan(SimpleNamespace(case=case,timeout_min=1),plan)
    assert code==0 and len(configured)==3
    assert all(stage["steps"]>=4 for stage in configured)


def load_tool(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / "Application Support/Tools" / (name+".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workspace_transfer_rebases_owned_paths_and_preserves_original(tmp_path):
    tool = load_tool("transfer_studio_workspace")
    source_root = tmp_path/"source"
    workspace = create_workspace(source_root,"Original")
    write_json(workspace/"config.json", {"path":str(workspace/"Inputs"), "rho":1.2})
    archive=tmp_path/"transfer.zip"
    tool.export_workspace(workspace,archive)
    destination=tmp_path/"destination"
    result=tool.import_workspace(destination,archive,"Imported")
    restored=Path(result["workspace"])
    assert read_json(restored/"config.json")["path"]==str(restored/"Inputs")
    assert read_json(workspace/"config.json")["path"]==str(workspace/"Inputs")
    with pytest.raises(FileExistsError):tool.import_workspace(destination,archive,"Imported")


def test_remote_alpha_identity_without_config(tmp_path):
    tool=load_tool("package_ramair_remote_execution")
    case=tmp_path/"open"/"alpha_p8p000";case.mkdir(parents=True)
    assert tool.case_identity(case)==("open",8.)


def test_runtime_release_preview_recognizes_scientific_time_and_only_processors(tmp_path):
    from ramair_workspace_lifecycle import releasable_paths
    case=tmp_path/"Angles/alpha_p8p000"
    for name in ("0","1.2e-05","processor0","processor_notes","system","constant"):
        (case/name).mkdir(parents=True,exist_ok=True)
    assert {p.name for p in releasable_paths(tmp_path)}=={"1.2e-05","processor0"}
