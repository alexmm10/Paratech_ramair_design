"""Resource planning and live RANS/URANS monitors for independent solver setups."""
from __future__ import annotations
import os
from pathlib import Path
import pandas as pd
import streamlit as st
import workflow_backend as backend
from ramair_workspace import read_json, write_json, alpha_name
from parameter_controls import reference
from validation_plotting import residual_figure, coefficient_figure, close_figures


def execution_options(setup, angles):
    budget = min(8, os.cpu_count() or 1)
    solver = read_json(setup/"solver_config.json")
    current = solver.get("execution_controls", {})
    st.subheader("Orden y recursos")
    cols = st.columns(2)
    cores = cols[0].selectbox("Cores por caso", list(range(1, budget+1)), index=min(3, budget-1), help="Todos los recuentos de 1 a 8 están permitidos; el planificador respeta el presupuesto total.")
    concurrent = cols[1].number_input("Casos simultáneos", 1, 8, 1, help="Máximo de casos activos. Los casos esperan si no hay cores libres.")
    allocation = pd.DataFrame([{"Orden": i+1, "Ángulo [°]": a, "Cores": cores} for i,a in enumerate(angles)])
    allocation = st.data_editor(allocation, hide_index=True, width="stretch", disabled=["Ángulo [°]"], key="exec/allocation/"+str(setup)+str(cores)+str(angles),
        column_config={"Orden": st.column_config.NumberColumn(min_value=1, step=1, help="Prioridad de salida; para una cola estricta utiliza un caso simultáneo."),
                       "Cores": st.column_config.NumberColumn(min_value=1, max_value=budget, step=1, help="Ejemplo: tres ángulos con 3, 3 y 2 cores.")})
    ordered = allocation.sort_values("Orden")["Ángulo [°]"].tolist() if len(allocation) else []
    counts = dict(zip(allocation.get("Ángulo [°]", []), allocation.get("Cores", [])))
    if len(counts) and sum(sorted(counts.values(), reverse=True)[:concurrent]) > budget:
        st.warning(f"La combinación solicitada supera {budget} cores. La cola esperará a liberar recursos; no iniciará todos a la vez.")
    report = read_json(setup.parent.parent/"mesh_report.json")
    cells = int(report.get("checkMesh_cell_count", 0))
    if cells and counts:
        ratios = [cells/int(n) for n in counts.values()]
        st.caption(f"Malla: {cells:,} celdas · {min(ratios):,.0f}–{max(ratios):,.0f} celdas/core. Orientación inicial: 30 000–100 000 celdas/core; mide el rendimiento real antes de aumentar MPI.")
        if min(ratios) < 30000: st.info("Algún caso tiene pocos elementos por core y puede gastar más tiempo en comunicación MPI.")
    st.subheader("Fase que se va a continuar")
    action = st.selectbox("Acción", ["continue", "rans_only", "start_urans"],
        index=["continue", "rans_only", "start_urans"].index(current.get("phase_action", "continue")),
        format_func=lambda v: {"continue":"Continuar la fase pendiente", "rans_only":"Polar solo RANS · ejecutar RANS sin iniciar URANS", "start_urans":"Iniciar URANS desde el RANS guardado"}[v],
        help="Una parada RANS se prolonga como RANS; no se interpreta su contador de iteraciones como segundos. Iniciar URANS desde un RANS pendiente es una decisión explícita.")
    additional = st.number_input("Iteraciones adicionales al reanudar RANS", min_value=1, value=int(current.get("steady_additional_iterations", 500)), help="Extiende el contador desde la última iteración guardada; no reinicializa con potentialFoam.")
    options = dict(phase_action=action, steady_additional_iterations=additional)
    absolute = st.toggle("Continuar hasta un límite total de iteraciones RANS", True,
        help="Evita considerar finalizada una simulación por haber terminado una tanda breve de continuación.")
    if absolute:
        options["rans_end_iteration"] = st.number_input("Límite total al continuar RANS", min_value=1, value=int(solver.get("steady_max_iterations", 20000)), step=1000,
            help="Se aplica a los ángulos con RANS pendiente. Un caso nuevo usa el límite guardado en su configuración OpenFOAM.")
    if action == "rans_only":
        st.info("Cada ángulo ejecuta la etapa RANS con sus límites y controles de convergencia. La cola continúa con el siguiente ángulo sin arrancar URANS; publica después los resultados revisados como RANS.")
    with st.expander("Lanzamiento, límites de tiempo y preparación · avanzado"):
        modes = ["native", "pyfoam"]
        options["backend"] = st.selectbox("Controlador de ejecución", modes, index=modes.index(current.get("backend", "native")),
            format_func=lambda v: "OpenFOAM nativo con monitor de la app" if v=="native" else "OpenFOAM nativo envuelto por PyFoam",
            help="Ambos ejecutan el mismo OpenFOAM instalado en WSL. PyFoam es un controlador Python adicional, no otro solver ni un contenedor.")
        options["timeout_min"] = st.number_input("Límite por fase URANS [min]", min_value=.1, value=float(current.get("timeout_min", 1440.)), help="Al agotarse solicita escribir y detener. No es la duración física de la simulación.")
        options["steady_timeout_min"] = st.number_input("Límite de ejecución RANS [min]", min_value=.1, value=float(current.get("steady_timeout_min", 1440.)), help="Tiempo de reloj para la etapa estacionaria.")
        options["stop_grace_min"] = st.number_input("Espera para escritura al parar [min]", min_value=.1, value=float(current.get("stop_grace_min", 5.)), help="Espera de parada limpia antes de escalar la señal al proceso del solver.")
        options["renumber_before_decompose"] = False
        st.caption("Se conserva el orden de celdas; la descomposición MPI puede cambiar sin renumerar la malla global.")
        options["steady_potential_foam"] = st.toggle("Inicialización potencial de RANS nuevo", current.get("steady_potential_foam", True), help="Solo se aplica a un arranque nuevo. Nunca se vuelve a aplicar al prolongar RANS.")
    rows = []
    plan = read_json(setup/"phase_plan.json")
    for i, alpha in enumerate(ordered):
        case = setup/"Angles"/alpha_name(alpha)
        pending = read_json(case/"steadyInitialization/pending_stage.json")
        from ramair_simulation_inventory import case_progress
        latest = case_progress(case)["rans_iteration"]
        rows.append({"Orden":i+1, "Ángulo [°]":alpha, "Cores":int(counts[alpha]), "Celdas/core": round(cells/counts[alpha]) if cells else None,
            "RANS inicial":latest, "RANS límite":options.get("rans_end_iteration", latest+additional if pending else solver.get("steady_max_iterations")),
            "Δt* máximo":plan.get("target_deltaT_star"), "Courant máximo":plan.get("maxCo"), "Acción":action})
    st.dataframe(rows, hide_index=True, width="stretch")
    reference(options, "Referencia de opciones de ejecución")
    return cores, concurrent, ordered, counts, options


def render_case_monitor(setup, case, key, cores=1, campaign=None):
    staged = read_json(case/"staged_run_status.json")
    status = read_json(case/"run_status.json")
    text = " ".join(str(v) for v in (status.get("phase"), status.get("stage"), staged.get("status"), staged.get("current_phase"))).upper()
    default = "URANS" if any(s in text for s in ("URANS", "PIMPLE", "TRANSIENT", "PHASE_")) and "STEADY" not in text else "RANS"
    mode = st.radio("Fase mostrada", ["RANS", "URANS"], index=int(default=="URANS"), horizontal=True, key=key+"/phase", help="Selecciona también el historial RANS anterior cuando URANS está activo.")
    manifest = read_json(setup/"solver_setup.json")
    report = read_json(setup.parent.parent/"mesh_report.json")
    physical = manifest.get("context", {}).get("physical", {})
    velocity = float(physical.get("velocity_m_s") or 0)
    tc = float(physical.get("chord_m") or 1) / velocity if velocity > 0 else None
    plan = read_json(setup/"phase_plan.json")
    stage = str(staged.get("current_phase") or ("SIMPLE" if mode == "RANS" else ""))
    target_dt = float(plan.get("target_deltaT_star") or 0) * tc if tc else None
    phase = next((row for row in plan.get("stages", []) if row.get("stage") == stage), {})
    phase_dt = target_dt * float(phase.get("dt_factor") or 1) if target_dt else None
    options = read_json(setup/"solver_config.json").get("execution_controls", {})
    snapshot = backend.validation_monitor_snapshot(case, mode=mode, run_id=case.name,
        topology=manifest["context"]["topology"], mesh_level=setup.parent.parent.name,
        cell_count=int(report.get("checkMesh_cell_count", 0)), n_cores=cores,
        alpha_deg=read_json(case/"case_config.json").get("alpha_deg"), stage=stage,
        tc_s=tc, target_delta_t=target_dt, phase_delta_t=phase_dt,
        steps_planned=options.get("rans_end_iteration") if mode == "RANS" else None)
    from validation_convergence_page import _monitor_charts
    _monitor_charts(snapshot, key_scope=key)
    if st.button("Guardar y pausar este caso", key=key+"/pause"):
        if campaign:
            write_json(campaign, {"action":"pause_case", "case_id":case.name})
        else:
            from ramair_execution_control import request_case_pause
            request_case_pause(case, campaign=True)
        st.info("Parada solicitada; esperando el último estado escrito.")
    with st.expander("Configuración y estado detallado del monitor"):
        st.json({"solver":read_json(setup/"solver_config.json"), "plan":read_json(setup/"phase_plan.json"), "execution":staged or status})


def monitors(root, setup):
    @st.fragment(run_every=5)
    def live():
        from ramair_simulation_inventory import simulation_inventory
        available=simulation_inventory(setup)
        st.dataframe([{k:r[k] for k in ("alpha","RANS","URANS","Estado")} for r in available], hide_index=True, width="stretch")
        queues = sorted((setup/"Queues").glob("*.status.json"))
        state = read_json(queues[-1]) if queues else {}
        from ramair_simulation_inventory import case_progress
        from ramair_execution_control import load_solver_process, pid_is_alive
        for row in state.get("cases", []):
            if not row.get("case_path"):continue
            case = Path(row["case_path"])
            process = load_solver_process(case)
            if row.get("status") == "RUNNING" and not pid_is_alive(process.get("pid"), process.get("pid_start_token")):
                row["status"] = "Interrumpido; revisar continuación"
            if row.get("status") == "COMPLETED" and (case/"steadyInitialization/pending_stage.json").is_file():
                row["status"] = "PAUSED · RANS pendiente"
            row["iteration"] = case_progress(case)["rans_iteration"]
        running = [c for c in state.get("cases", []) if c.get("status")=="RUNNING"]
        control = queues[-1].with_name(queues[-1].name.replace(".status.json", ".control.json")) if queues else None
        if state:
            st.dataframe([{k:c.get(k) for k in ("case_id", "status", "iteration", "n_cores", "wall_time_s")} for c in state.get("cases", [])], hide_index=True, width="stretch")
            if st.button("Guardar y pausar toda la cola", key="exec/pause_all/"+str(setup)):
                write_json(control, {"action":"pause_all"})
        cases = sorted((setup/"Angles").glob("*/case_config.json"))
        with st.expander("Monitor individual · seguimiento automático", expanded=len(running)<=1):
            follow = st.toggle("Seguir el caso activo", True, key="exec/follow/"+str(setup), help="En una cola serie, cambia de ángulo al comenzar el siguiente caso.")
            chosen = Path(running[0]["case_path"]) if follow and running else None
            if not chosen and cases:
                chosen = st.selectbox("Ángulo del monitor", [p.parent for p in cases], format_func=lambda p:p.name, key="exec/monitor/"+str(setup))
            if chosen: render_case_monitor(setup, chosen, "exec/single/"+str(chosen), int(running[0]["n_cores"]) if running else 1, control if running else None)
        with st.expander("Monitores de casos simultáneos", expanded=len(running)>1):
            if not running: st.caption("Los monitores aparecerán al ocuparse los slots de ejecución.")
            for slot, row in enumerate(running, 1):
                st.subheader(f"Slot {slot} · {row['case_id']}")
                render_case_monitor(setup, Path(row["case_path"]), "exec/parallel/"+str(setup)+"/"+str(slot)+row["case_id"], int(row["n_cores"]), control)
    live()
    with st.expander("Parada de emergencia"):
        st.caption("Detiene los procesos OpenFOAM registrados por esta aplicación, comprobando la identidad del proceso. Una interrupción forzada solo garantiza recuperar el último checkpoint ya escrito.")
        if st.button("Detener OpenFOAM de la aplicación, incluidos procesos sin monitor"):
            from ramair_execution_control import discover_live_solver_cases, request_case_pause, signal_solver_process
            results = []
            for state_path in (root/"Workspaces").glob("*/CFD_2D/*/Geometries/*/Meshes/*/OpenFOAM/*/Queues/*.status.json"):
                queue = read_json(state_path)
                if queue.get("status") in {"RUNNING", "STARTING", "PAUSING"} or any(c.get("status") == "RUNNING" for c in queue.get("cases", [])):
                    write_json(state_path.with_name(state_path.name.replace(".status.json", ".control.json")), {"action":"pause_all"})
            for row in discover_live_solver_cases(root):
                case = Path(row["case_dir"])
                request_case_pause(case, campaign=True)
                results.append(signal_solver_process(case, record=row))
            st.json(results or {"status":"No hay procesos activos registrados"})
