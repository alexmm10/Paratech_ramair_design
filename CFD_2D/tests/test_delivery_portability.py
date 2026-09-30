from types import SimpleNamespace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'CFD_2D/scripts'))
import numpy as np
from ramair_suspension_layout import validate_network
import ramair_remote_queue_runner as remote


def node(name, kind, xyz):
    return SimpleNamespace(node_id=name, node_type=kind, p=np.array(xyz))


def test_manual_network_rejects_cycle_disconnection_and_zero_length():
    network=SimpleNamespace(nodes={"a":node("a","anchor",[0,0,0]),
        "b":node("b","cascade",[0,0,0]), "c":node("c","anchor",[1,0,0])},
        segments=[SimpleNamespace(start_node="a",end_node="b"),SimpleNamespace(start_node="b",end_node="a")],warnings=[])
    report=validate_network(network)
    assert not report["valid"]
    assert any("Ciclo" in e for e in report["errors"])
    assert any("longitud nula" in e for e in report["errors"])
    assert any("destino único: c" in e for e in report["errors"])


def test_remote_rans_does_not_override_review_or_renumber(tmp_path, monkeypatch):
    monkeypatch.setattr(remote,"ROOT",tmp_path)
    (tmp_path/"case/steadyInitialization").mkdir(parents=True)
    (tmp_path/"case/steadyInitialization/pending_stage.json").write_text('{}')
    command=remote.staged_command({"case":"case", "execution_mode":"rans_only",
        "continue_transient_after_steady_timeout":True},True)
    assert "--continue-transient-after-steady-timeout" not in command
    assert "--no-renumber-before-decompose" in command
    assert command[command.index("--steady-decision")+1] == "extend"


def test_component_outline_is_not_a_suspension_cycle():
    network=SimpleNamespace(nodes={"a":node("a","slider_corner",[0,0,0]),
        "b":node("b","slider_corner",[1,0,0]), "c":node("c","slider_corner",[0,1,0])},
        segments=[SimpleNamespace(start_node=a,end_node=b,line_type="slider")
            for a,b in (("a","b"),("b","c"),("c","a"))],warnings=[])
    assert validate_network(network)["valid"]


def test_release_identity_works_without_git(tmp_path):
    import sys
    tools=Path(__file__).resolve().parents[2]/"Application Support/Tools"
    sys.path.insert(0,str(tools))
    from release_identity import release_identity
    (tmp_path/"RELEASE.json").write_text('{"version":"2026.09.30"}')
    assert release_identity(tmp_path)["version"] == "2026.09.30"


def test_runtime_update_supersedes_original_portable_commit(tmp_path):
    import json
    tools=Path(__file__).resolve().parents[2]/"Application Support/Tools"
    sys.path.insert(0,str(tools))
    from release_identity import release_identity
    (tmp_path/'PORTABLE_PACKAGE_MANIFEST.json').write_text(json.dumps({'release':{'commit':'old'}}))
    state=tmp_path/'CFD_2D/app_state'
    state.mkdir(parents=True)
    (state/'runtime_sync_manifest.json').write_text(json.dumps({'release':{'commit':'updated'}}))
    assert release_identity(tmp_path)['commit']=='updated'


def test_monitor_does_not_label_finished_rans_as_running(tmp_path):
    from ramair_workspace import write_json
    from ramair_2d_validation_live_monitor import build_monitor_snapshot
    (tmp_path/'log.foamRun').write_text('Time = 10\nExecutionTime = 1 s ClockTime = 1 s\nEnd\n')
    write_json(tmp_path/'run_status.json',{'status':'RUN_COMPLETED'})
    args=dict(mode='RANS',run_id='test',topology='closed',mesh_level='medium',cell_count=100)
    assert build_monitor_snapshot(tmp_path,**args)['status']=='COMPLETED'
    write_json(tmp_path/'steadyInitialization/pending_stage.json',{'latest_iteration':10})
    assert build_monitor_snapshot(tmp_path,**args)['status']=='PAUSED_RECOVERABLE'


def test_clean_performance_panel_does_not_invent_zero_courant(tmp_path):
    from streamlit.testing.v1 import AppTest
    root=Path(__file__).resolve().parents[2]
    app=tmp_path/'performance_acceptance.py'
    app.write_text('from pathlib import Path\nimport sys\n'
        f'sys.path[:0]=[{str(root/"CFD_2D/scripts")!r},{str(root/"CFD_2D/app")!r}]\n'
        'from performance_report_page import render_performance_report\n'
        f'render_performance_report(Path({str(tmp_path)!r}))\n',encoding='utf-8')
    result=AppTest.from_file(str(app)).run(timeout=60)
    assert not result.exception
    values={m.label:m.value for m in result.metric}
    assert values['Co local']=='-'
    assert values['dt adaptativo']=='-'
    assert values['dt estimado para Co=5']=='-'
