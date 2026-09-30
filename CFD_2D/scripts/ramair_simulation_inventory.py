"""Read-only phase-aware progress; completion of a process is not convergence."""
from pathlib import Path
from ramair_workspace import read_json


def numeric_states(folder):
    values=[]
    if not folder.is_dir():return values
    for path in folder.iterdir():
        if not path.is_dir():continue
        try:value=float(path.name)
        except ValueError:continue
        if value>0 and ((path/'U').is_file() or (path/'U.gz').is_file()):values.append(value)
    return values


def case_progress(case):
    case=Path(case);cfg=read_json(case/'case_config.json');staged=read_json(case/'staged_run_status.json')
    status=read_json(case/'run_status.json');pending=read_json(case/'steadyInitialization/pending_stage.json')
    transition=staged.get('steady_transition') or {}
    rans_cache=read_json(case/'validation_live_monitor_rans.json')
    urans_cache=read_json(case/'validation_live_monitor_urans.json')
    current_phase=str(staged.get('current_phase') or staged.get('status') or '').upper()
    transient=bool(staged.get('transient_started') or staged.get('transient_command_executed') or staged.get('transient_phases') or current_phase in ('A','B','C','D','E') or current_phase.startswith('TRANSIENT'))
    local=numeric_states(case)
    processors=[set(numeric_states(p)) for p in case.glob('processor[0-9]*') if p.is_dir()]
    if processors:local+=list(set.intersection(*processors))
    iterations=max(float(pending.get('latest_iteration') or 0),float(transition.get('latest_iteration') or 0),float(rans_cache.get('iteration_or_time') or 0))
    if not transient:iterations=max([iterations,*local])
    for folder in (case/'steadyInitialization/history').glob('*/time_directories'):
        iterations=max([iterations,*numeric_states(folder)])
    urans_time=max([0.,*local]) if transient else float(urans_cache.get('physical_time_s') or 0)
    initiated=iterations>0 or urans_time>0
    stable=bool(transition.get('statistically_stable'))
    state='Pendiente de continuación' if pending else str(staged.get('status') or status.get('status') or 'Preparado')
    if status.get('status')=='RUNNING':
        from ramair_execution_control import load_solver_process,pid_is_alive
        process=load_solver_process(case)
        state='En ejecución' if pid_is_alive(process.get('pid'),process.get('pid_start_token')) else 'Interrumpido; continuación disponible'
    return {'alpha':cfg.get('alpha_deg'),'case':str(case),'initiated':initiated,'rans_iteration':int(iterations),
            'rans_stable':stable,'RANS':f"Iteración {int(iterations)} ({'Estable' if stable else 'Pendiente de revisar' if iterations else 'No iniciado'})",
            'urans_time_s':urans_time,'URANS':f"t = {urans_time:.8g} s ({'Iniciado' if urans_time else 'No iniciado'})",'Estado':state}


def simulation_inventory(setup):
    rows=[case_progress(p.parent) for p in (Path(setup)/'Angles').glob('*/case_config.json')]
    return sorted([r for r in rows if r['initiated']],key=lambda r:float(r['alpha']))
