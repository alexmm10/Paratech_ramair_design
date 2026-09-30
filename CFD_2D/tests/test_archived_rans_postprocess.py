from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ramair_2d_postprocess import prepare_steady_stage_results
from ramair_simulation_inventory import case_progress
from ramair_polar_comparison import available_point

def test_archived_volume_selected_after_rans_transferred_to_zero(tmp_path):
    case=tmp_path/'case';archive=case/'steadyInitialization/history/run_20260914'
    (case/'system').mkdir(parents=True);(case/'system/controlDict').write_text('')
    (case/'0').mkdir();(case/'0/U').write_text('test')
    (archive/'paraview_case/system').mkdir(parents=True)
    (archive/'paraview_case/system/controlDict').write_text('')
    (archive/'paraview_case/20000').mkdir()
    (archive/'paraview_case/20000/U').write_text('test')
    (archive/'time_directories').mkdir()
    force=archive/'postProcessing/forceCoeffs/0';force.mkdir(parents=True)
    (force/'coefficient.dat').write_text('# Time Cd Cl Cm\n'+'\n'.join(f'{i} .02 1.3 .1' for i in range(19980,20001)))
    output=tmp_path/'Postprocess';output.mkdir()
    result=prepare_steady_stage_results(case,output,project_root=tmp_path,variant='open',run_openfoam_tools=False,
        wall_profile_analysis=False,velocity_profile_stations=[.5],velocity_profile_sample_points=20,
        simulation_mode='RANS',automatic_paraview_products=False,include_paraview_animations=False,
        paraview_maximum_frames=2,timeout_s=30,field_scale_mode='exact',robust_percentiles=(1,99),manual_scales={},average_tail_samples=10,alpha_deg=8)
    assert Path(result['paraview_case'])==archive/'paraview_case'
    assert result['force_summary']['averaging_end_iteration']==20000

def test_pending_iteration_uses_latest_field_over_stale_metadata(tmp_path):
    (tmp_path/'steadyInitialization').mkdir()
    (tmp_path/'steadyInitialization/pending_stage.json').write_text(json.dumps({'latest_iteration':1}))
    (tmp_path/'1001').mkdir();(tmp_path/'1001/U').write_text('test')
    row=case_progress(tmp_path)
    assert row['rans_iteration']==1001
    assert row['Estado']=='Pendiente de continuación'
    assert row['urans_time_s']==0

def test_rans_polar_reads_dedicated_force_summary(tmp_path):
    folder=tmp_path/'Angles/alpha_p8p000/Postprocess/RANS';folder.mkdir(parents=True)
    (folder/'stage_summary.json').write_text(json.dumps({'status':'AVAILABLE','force_summary':{'status':'AVAILABLE','mean':{'Cl':1.3,'Cd':.02,'Cm':.1}}}))
    assert available_point(tmp_path,8,'RANS')['Cl']==1.3
