from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Application Support/Tools'))
from install_studio_bundle import verify_content,restore_example,relocate_snapshot_points
from transfer_studio_workspace import export_workspace,import_workspace
from ramair_studio_defaults import catalog,stage_defaults
from ramair_workspace import solver_defaults
from ramair_workspace_jobs import retained_results_only,postprocess_cases


def test_shipped_defaults_match_approved_study():
    quality=stage_defaults(ROOT,'quality_reference')
    assert {m['quality']['checkMesh_cell_count'] for m in quality['meshes']}=={215058,248180,265520}
    assert all(m['approved_source'] and m['quality']['checkMesh_status']=='OK' for m in quality['meshes'])
    for top in ('open','closed'):
        config,plan=solver_defaults(ROOT,top)
        assert config['execution_controls']['phase_action']=='rans_only'
        assert config['execution_controls']['renumber_before_decompose'] is False
        assert config['solver_module']=='incompressibleFluid'
        assert plan and stage_defaults(ROOT,'mesh',top)['topology']==top
    assert stage_defaults(ROOT,'case')['reynolds']==1.9e6


def test_stage_defaults_preserves_independent_edited_copies():
    first=stage_defaults(ROOT,'solver','open');first['steady_max_iterations']=3
    assert stage_defaults(ROOT,'solver','open')['steady_max_iterations']==20000


def test_defaults_rejects_path_outside_application(tmp_path):
    directory=tmp_path/'Application Support/Configurations';directory.mkdir(parents=True)
    (directory/'studio_defaults.json').write_text(json.dumps({'case':'../outside.json'}))
    with pytest.raises(ValueError,match='fuera'):stage_defaults(tmp_path,'case')
    assert stage_defaults(tmp_path,'missing',fallback={'value':0})=={'value':0}


def test_bundle_verification_detects_tampering_and_escape(tmp_path):
    file=tmp_path/'content.txt';file.write_text('original')
    record={'kind':'ramair_complete_delivery','files':{'content.txt':{'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}}}
    manifest=tmp_path/'DELIVERY_MANIFEST.json';manifest.write_text(json.dumps(record))
    assert verify_content(tmp_path)['kind']=='ramair_complete_delivery'
    file.write_text('tampered')
    with pytest.raises(ValueError,match='alterado'):verify_content(tmp_path)
    record['files']={'../elsewhere':{'bytes':0,'sha256':''}};manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError):verify_content(tmp_path)


def test_example_install_is_idempotent_and_preserves_name_collisions(tmp_path):
    original=tmp_path/'Source';original.mkdir();(original/'workspace.json').write_text(json.dumps({'name':'Example','kind':'workspace','id':'source'}))
    (original/'data.txt').write_text('retained source data')
    archive=tmp_path/'example.zip';export_workspace(original,archive,True)
    receiver=tmp_path/'Receiver';existing=receiver/'Workspaces/Example_Open_Polar';existing.mkdir(parents=True)
    (existing/'user.txt').write_text('preserve')
    installed=restore_example(receiver,archive)
    assert Path(installed['workspace']).name=='Example_Open_Polar_2'
    (Path(installed['workspace'])/'data.txt').write_text('user edited')
    again=restore_example(receiver,archive)
    assert again['status']=='PRESERVED'
    assert (Path(again['workspace'])/'data.txt').read_text()=='user edited'
    assert (existing/'user.txt').read_text()=='preserve'


def test_retained_products_are_not_reprocessed_as_zero_initial_fields(tmp_path):
    setup=tmp_path/'Setup';case=setup/'Angles/alpha_p0p000';(case/'0').mkdir(parents=True)
    (case/'retained_results_snapshot.json').write_text('{}')
    (case/'0/U').write_text('genuine initial field')
    assert retained_results_only(case)
    result=postprocess_cases(tmp_path,setup,[0.],{})
    assert result[0]['status']=='RETAINED_RESULTS_ONLY'
    saved=case/'100';saved.mkdir();(saved/'U.gz').write_bytes(b'compressed field placeholder used only for inventory')
    assert not retained_results_only(case)


def test_all_shipped_profiles_can_be_imported_without_original_device(tmp_path):
    from ramair_profile_library import catalogue,import_geometry
    for item in catalogue(ROOT):
        geometry=import_geometry(tmp_path,Path(item['path']),item['id'])
        assert (geometry/'Inputs/profile/profile_points.csv').is_file()
        if item['topology']=='open':assert (geometry/'Inputs/base/profile_points.csv').is_file()


def test_postprocess_controls_restore_zero_and_saved_stations():
    from streamlit.testing.v1 import AppTest
    program="import sys\nsys.path[:0]="+repr([str(ROOT/'CFD_2D/scripts'),str(ROOT/'CFD_2D/app')])+"\n"+"""
from workspace_postprocess_controls import postprocess_controls
postprocess_controls({'rans_average_start_iteration':0,'velocity_profile_stations':[0.2,0.7],'rans_average_tail_samples':200})
"""
    app=AppTest.from_string(program).run(timeout=30)
    assert not app.exception
    assert next(n.value for n in app.number_input if n.label=='Primera iteración del promedio RANS')==0
    assert next(n.value for n in app.number_input if n.label=='Muestras finales para estadísticas RANS')==200
    assert app.text_input[0].value=='0.2, 0.7'


def test_snapshot_relocation_preserves_values_and_rejects_changes(tmp_path):
    setup=tmp_path/'CFD_2D/Case/Geometries/Profile/Meshes/Mesh/OpenFOAM/Setup'
    folder=setup/'Angles/alpha_p0p000/Postprocess/RANS';folder.mkdir(parents=True)
    (folder/'stage_summary.json').write_text(json.dumps({'status':'RANS_REVIEWED','mean':{'Cl':0.5,'Cd':0.03,'Cm':0.02}}))
    (setup/'delivery_snapshot.json').write_text('{}')
    (setup/'Polar').mkdir()
    points=setup/'Polar/accepted_points.json'
    points.write_text(json.dumps({'points':[{'alpha':0.,'stage':'RANS','Cl':0.5,'Cd':0.03,'Cm':0.02,'source':'old/source','source_sha256':'old'}]}))
    assert relocate_snapshot_points(tmp_path,'verified-archive')==1
    from ramair_polar_comparison import load_polar
    assert load_polar(setup).Cl.iloc[0]==0.5
    changed=json.loads(points.read_text());changed['points'][0]['Cl']=0.9;points.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='han cambiado'):relocate_snapshot_points(tmp_path,'verified-archive')


def test_object_deduplication_does_not_share_final_case_files(tmp_path):
    source=tmp_path/'Source';source.mkdir();(source/'workspace.json').write_text(json.dumps({'name':'Example','kind':'workspace','id':'source'}))
    for name in ('first.txt','second.txt'):(source/name).write_text('same bytes')
    archive=tmp_path/'objects.zip';export_workspace(source,archive,True)
    result=import_workspace(tmp_path/'Receiver',archive,'Imported')
    target=Path(result['workspace']);(target/'first.txt').write_text('changed independently')
    assert (target/'second.txt').read_text()=='same bytes'


def test_windows_path_bridge_avoids_shell_backslash_interpretation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('delivery_launcher_test',ROOT/'run_ramair_cfd2d_app.py')
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    calls=[]
    def capture(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(stdout='/mnt/c/FolderWithoutSpaces\n')
    monkeypatch.setattr(launcher.subprocess,'run',capture)
    assert launcher.windows_to_wsl(tmp_path/'FolderWithoutSpaces','Ubuntu-22.04')=='/mnt/c/FolderWithoutSpaces'
    assert calls[0][:6]==['wsl.exe','-d','Ubuntu-22.04','--exec','wslpath','-a']
