from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pandas as pd
import pytest
from ramair_2d_urans_review import executed_analysis_plan, _select_analysis_window, restore_serialized_uniform_times
from ramair_2d_validation import validation_percentage_errors, polar_reference_error_statistics
from ramair_validation_reference import load_validation_reference


def test_journal_prevents_retroactive_production_classification():
    frame=pd.DataFrame({'Time':np.arange(.005,.396,.0000625)})
    plan={'stages':[{'stage':'E','start_s':.2,'end_s':2}]}
    journal={'phases':[{'phase':'D','solver_started':True,'actual_start_s':.0043,'actual_end_s':.3962,'deltaT_s':.0000625,'scheme':'backward'}]}
    actual,source=executed_analysis_plan(plan,journal,frame)
    assert _select_analysis_window(frame,actual,{})[0]=='D'
    assert source=='executed_stage_journal'


def test_fixed_grid_rounding_is_recovered_without_interpolating_coefficients():
    t=.16492188+np.arange(3701)*.0000625
    frame=pd.DataFrame({'Time':[float(f'{x:.6g}') for x in t],'Cl':np.sin(t)})
    restored,evidence=restore_serialized_uniform_times(frame,.0000625)
    assert evidence['status']=='FIXED_DT_CONFIRMED_WITHIN_SERIALIZATION_PRECISION'
    np.testing.assert_allclose(np.diff(restored.Time),.0000625,rtol=1e-10)
    np.testing.assert_array_equal(restored.Cl,frame.Cl)
    missing=frame.drop(index=500).reset_index(drop=True)
    assert restore_serialized_uniform_times(missing,.0000625)[1]['status']=='UNCHANGED'


def test_reference_interpolates_exact_angle_and_excludes_poststall_drag():
    lift=pd.DataFrame({'series':['Experimental']*4,'alpha_deg':[-6,-4,16,18],'Cl':[-.4,0,1.6,1.4]})
    drag=pd.DataFrame({'series':['Experimental']*3,'Cl':[-.4,0,1.6],'Cd':[.02,.01,.05]})
    points=pd.DataFrame({'alpha_deg':[-5,18],'Cl':[-.2,1.7],'Cd':[.015,.06]})
    errors,_=validation_percentage_errors(points,lift,drag)
    assert errors.iloc[0].Cl_exp==pytest.approx(-.2)
    assert errors.iloc[0].Cd_exp==pytest.approx(.015)
    assert np.isnan(errors.iloc[1].Cd_exp)
    differences,_,_=polar_reference_error_statistics(points,lift,drag)
    assert set(differences[differences.alpha_deg==18].variable)=={'Cl'}


def test_original_user_reference_coordinates_are_available():
    root=Path(__file__).resolve().parents[2]
    lift,drag,manifest=load_validation_reference(root)
    assert set(lift.series)=={'Experimental','CFD Kestrel','CFD Cobalt'}
    assert len(manifest['files'])==2
    assert drag.Cd.max()<.2
    assert lift.alpha_deg.min()<-9.9
def test_failed_last_phase_preserves_executed_force_history():
    import pandas as pd
    from ramair_2d_urans_review import executed_analysis_plan
    journal = {"phases": [
        {"phase": "D", "solver_started": True, "actual_start_s": .01, "actual_end_s": .4},
        {"phase": "E", "solver_started": True, "actual_start_s": .4, "actual_end_s": .4, "returncode": 1},
    ]}
    plan, source = executed_analysis_plan({}, journal, pd.DataFrame({"Time": [.01, .4, 2.16]}))
    assert source == "executed_stage_journal"
    assert plan["stages"][0]["end_s"] == .4
    assert plan["stages"][1]["end_s"] == 2.16
