from pathlib import Path
import json
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from ramair_2d_openfoam_staged_runner import set_steady_extension
from ramair_2d_rans_checkpoint_batch import _pending_latest_iteration, _runner_command
from ramair_2d_rans_accounting import authoritative_simple_iteration


def test_archived_fields_are_restored_without_destroying_original(tmp_path):
    case=tmp_path/"case"
    archive=case/"steadyInitialization/history/run_old"
    checkpoint=archive/"time_directories/15403"
    checkpoint.mkdir(parents=True)
    for field in ("U","p","nuTilda"):(checkpoint/field).write_text("preserved "+field)
    (case/"system").mkdir()
    (case/"system/controlDict").write_text("startFrom startTime; startTime 0; endTime 20000;")
    (case/"steadyInitialization/pending_stage.json").write_text(json.dumps({"archive":str(archive)}))
    result=set_steady_extension(case,4597)
    assert result["new_end_iteration"]==20000
    assert (case/"15403/U").read_bytes()==(checkpoint/"U").read_bytes()
    (case/"log.foamRun").write_text("Time = 15420s\n")
    (case/"15425").mkdir()  # incomplete write must not be used for restart
    assert _pending_latest_iteration(case)==15403
    assert authoritative_simple_iteration(case)["sources"]["log:log.foamRun"]==15420
    command=_runner_command(tmp_path,case,{"timeout_min":1440},decision="extend",extension=4597,run=True)
    assert "--resume" in command and "--potentialFoam" not in command


def test_cannot_misinterpret_transient_seconds_as_iterations(tmp_path):
    (tmp_path/"0.001").mkdir()
    with pytest.raises(RuntimeError,match="transient"):
        set_steady_extension(tmp_path,10)


def test_compressed_mesh_is_recognized_and_duplicate_orderings_are_blocked(tmp_path):
    import gzip
    from ramair_2d_checkpoint_integrity import poly_mesh_digest,checkpoint_mesh_identity
    poly=tmp_path/"constant/polyMesh";poly.mkdir(parents=True)
    for name in ("points","faces","owner","neighbour"):
        (poly/name).write_text("native mesh "+name)
    (poly/"boundary").write_text("frontAndBack\n{\n type empty;\n}\n")
    zero=tmp_path/"10";zero.mkdir()
    for name in ("U","p","nuTilda"):(zero/name).write_text("internalField uniform 0;")
    original=poly_mesh_digest(poly)
    for path in list(poly.iterdir()):
        path.with_name(path.name+".gz").write_bytes(gzip.compress(path.read_bytes()));path.unlink()
    assert poly_mesh_digest(poly)==original
    assert checkpoint_mesh_identity(tmp_path,zero)["status"]=="READY"
    (poly/"faces").write_text("obsolete different ordering")
    identity=checkpoint_mesh_identity(tmp_path,zero)
    assert identity["status"]=="INVALID" and identity["ambiguous_mesh_files"]==["faces"]


def test_bounded_manual_extension_can_stop_below_campaign_target():
    from ramair_2d_rans_accounting import target_for_iteration
    assert target_for_iteration(15403,maximum=15413)==15413


def test_extension_writes_final_iteration_and_preserves_previous_archive(tmp_path):
    from ramair_2d_openfoam_staged_runner import preserve_archive_collision
    (tmp_path/"15403").mkdir()
    (tmp_path/"system").mkdir()
    control=tmp_path/"system/controlDict"
    control.write_text("startFrom latestTime; startTime 15403; endTime 20000; writeInterval 500; writeControl timeStep;")
    result=set_steady_extension(tmp_path,10)
    assert 15413 % result["write_interval_iterations"] == 0
    assert "writeControl runTime;" in control.read_text()
    old=tmp_path/"archive";old.mkdir();(old/"U").write_text("original")
    preserve_archive_collision(old)
    assert not old.exists()
    assert next((tmp_path/"previous_versions").glob("*/archive/U")).read_text()=="original"


def test_restart_ledger_rejects_same_size_reordered_mesh(tmp_path):
    from ramair_2d_openfoam_runner import verify_restart_mesh
    poly=tmp_path/'constant/polyMesh';poly.mkdir(parents=True)
    for name in ('points','faces','owner','neighbour'):(poly/name).write_text('ordering A')
    (poly/'boundary').write_text('frontAndBack\n{\n type empty;\n}\n')
    fields=tmp_path/'1.0';fields.mkdir()
    for name in ('U','p'):(fields/name).write_text('internalField uniform 0;')
    assert verify_restart_mesh(tmp_path,fields)['status']=='READY'
    (poly/'owner').write_text('ordering B')
    with pytest.raises(RuntimeError,match='ordering changed'):verify_restart_mesh(tmp_path,fields)


def test_production_capture_is_disabled_outside_sampling_and_bounds_retention(tmp_path):
    from ramair_production_capture import capture_policy
    config=tmp_path/'CFD_2D/app_state/production_capture.json';config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'enabled':True,'frames':120,'tail_fraction':.1}))
    case=tmp_path/'Workspaces/example/case';case.mkdir(parents=True)
    assert capture_policy(case,{'stage':'D'})=={}
    policy=capture_policy(case,{'stage':'E','start_s':1,'end_s':11})
    assert policy['write_interval_s']==pytest.approx(1/119)
    assert policy['purge_write']==123
