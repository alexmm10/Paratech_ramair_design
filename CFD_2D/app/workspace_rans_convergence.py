"""General execution mesh-convergence UI; studies do not mutate the laboratory."""
from pathlib import Path
import sys
import streamlit as st
from ramair_workspace import read_json, write_json, parse_angles
from ramair_rans_mesh_study import create_study, matrix, prepare_queue, analyze, compatible_setup


def render(root, workspace, start_job):
    st.subheader("Convergencia de malla · solo RANS")
    st.caption("Selecciona mallas de una geometría y copia una configuración de referencia. Las pruebas cortas permiten diagnosticar el proceso; no acreditan independencia de malla.")
    geometries = sorted(p.parent for p in workspace.glob("CFD_2D/*/Geometries/*/geometry.json"))
    if not geometries:
        st.info("Crea un caso CFD e importa una geometría para iniciar el estudio.")
        return
    geometry = st.selectbox("Caso / geometría del estudio", geometries,
        format_func=lambda p: p.parents[1].name+" / "+p.name, key="mesh-study/geometry")
    meshes = sorted(p.parent for p in (geometry/"Meshes").glob("*/mesh_config.json"))
    with st.expander("Preparar un estudio nuevo", expanded=not (geometry/"Convergence/RANS").exists()):
        chosen = st.multiselect("Mallas que se compararán", meshes, format_func=lambda p:p.name, key="mesh-study/meshes")
        setups = sorted(p.parent for p in (geometry/"Meshes").glob("*/OpenFOAM/*/solver_setup.json"))
        if not setups:
            st.info("Las mallas pueden estar sin solver. Crea una primera configuración de referencia en Caso OpenFOAM.")
        else:
            reference = st.selectbox("Configuración RANS de referencia", setups, format_func=lambda p:p.parent.parent.name+" / "+p.name, key="mesh-study/reference")
            manifest = read_json(reference/"solver_setup.json")
            angles_text = st.text_input("Ángulos [°], separados por comas", ", ".join(map(str, manifest.get("angles", [0, 8]))), key="mesh-study/angles")
            name = st.text_input("Nombre del estudio", "Comparacion_RANS", key="mesh-study/name")
            solver = read_json(reference/"solver_config.json")
            st.dataframe([{"Configuración": str(p.relative_to(geometry)), "Compatibilidad": compatible_setup(p, geometry, solver)[1]} for p in setups], hide_index=True, width="stretch")
            if st.button("Guardar estudio y asignar configuraciones", disabled=len(chosen)<2):
                try:
                    create_study(root, geometry, chosen, reference, parse_angles("list", angles_text), name)
                    st.success("Estudio guardado. Las ejecuciones anteriores se conservan.")
                    st.rerun()
                except (ValueError, OSError) as exc: st.error(str(exc))
    studies = sorted(p.parent for p in (geometry/"Convergence/RANS").glob("*/study.json"))
    if not studies: return
    folder = st.selectbox("Estudio guardado", studies, format_func=lambda p:p.name, key="mesh-study/study")
    frame = matrix(folder)
    st.dataframe(frame[[c for c in ("mesh", "alpha", "cells", "quality", "state", "iteration", "reason") if c in frame]], hide_index=True, width="stretch")
    cols = st.columns(2)
    cores = cols[0].number_input("Cores por combinación", 1, 8, 1, key="mesh-study/cores")
    concurrent = cols[1].number_input("Combinaciones simultáneas", 1, 8, 1, key="mesh-study/concurrent")
    real = st.toggle("Ejecutar RANS real", False, key="mesh-study/real")
    continue_processed = st.toggle("Continuar también puntos ya postprocesados", False, key="mesh-study/continue-processed",
        help="Extiende su checkpoint RANS. Después deberá repetir postproceso y análisis; no modifica los ajustes físicos.")
    if st.button("Preparar y ejecutar pendientes" if real else "Preparar y comprobar pendientes"):
        try:
            queue = prepare_queue(root, folder, cores, concurrent, real, continue_processed=continue_processed)
            start_job("rans_mesh_queue", [sys.executable, root/"CFD_2D/scripts/ramair_2d_parallel_campaign.py", "--manifest", queue])
        except (ValueError, OSError) as exc: st.error(str(exc))
    queues = sorted((folder/"Queues").glob("*.status.json"))
    if queues:
        st.json(read_json(queues[-1]), expanded=False)
        if st.button("Guardar y pausar estudio"):
            write_json(queues[-1].with_name(queues[-1].name.replace(".status.json", ".control.json")), {"action": "pause_all"})
            st.info("Pausa solicitada. Continúa con el botón de pendientes tras guardarse el checkpoint.")
    entries = read_json(folder/"study.json")["entries"]
    selected_setup = st.selectbox("Configuración para postprocesar / monitorizar", sorted(set(row["setup"] for row in entries)), key="mesh-study/postprocess-setup")
    setup = geometry/selected_setup
    if st.button("Postprocesar RANS de esta configuración"):
        angles = [row["alpha"] for row in entries if row["setup"] == selected_setup]
        start_job("rans_mesh_postprocess", [sys.executable, root/"CFD_2D/scripts/ramair_workspace_jobs.py", "postprocess", "--root", root, "--path", setup, "--angles", *map(str, angles)])
    with st.expander("Monitor y pausa individual"):
        from ramair_workspace import alpha_name
        from workspace_execution_controls import render_case_monitor
        available_cases = [setup/"Angles"/alpha_name(row["alpha"]) for row in entries if row["setup"] == selected_setup]
        chosen_case = st.selectbox("Ángulo del monitor del estudio", available_cases, format_func=lambda p:p.name, key="mesh-study/monitor-case")
        control = queues[-1].with_name(queues[-1].name.replace(".status.json", ".control.json")) if queues else None
        render_case_monitor(setup, chosen_case, "mesh-study/monitor/"+str(chosen_case), int(cores), control)
    ref = st.selectbox("Malla de referencia del análisis", ["Más fina disponible", *sorted(frame.mesh.unique())], key="mesh-study/analysis-reference")
    if st.button("Recalcular tablas y gráficas"):
        try:
            _, differences, gci = analyze(folder, None if ref == "Más fina disponible" else ref)
            if differences.empty: st.info("No hay resultados comunes postprocesados todavía.")
            else: st.success("Análisis actualizado, con firmas de los resultados utilizados.")
        except (ValueError, OSError) as exc: st.error(str(exc))
    output = folder/"Analysis"
    if (output/"analysis.json").exists():
        saved = read_json(output/"analysis.json")
        fresh = {(r["mesh"], r["alpha"]): r.get("source_sha256") for r in frame.to_dict("records")}
        if any(fresh.get((r["mesh"], r["alpha"])) != r["source_sha256"] for r in saved.get("sources", [])):
            st.warning("Los resultados han cambiado. Recalcula el análisis antes de usar las figuras.")
        st.json(saved.get("gci", {}), expanded=False)
    for image in sorted(output.glob("*.png")):
        st.image(str(image), width="stretch")
        st.download_button("Descargar "+image.name, image.read_bytes(), image.name, key="study/download/"+str(image))
    for table in sorted(output.glob("*.csv")):
        st.download_button("Descargar "+table.name, table.read_bytes(), table.name, key="study/download/"+str(table))
    with st.expander("Ejecutar este estudio en otro Linux / WSL"):
        if st.button("Crear paquete remoto · solo RANS"):
            args = [sys.executable, root/"Application Support/Tools/package_ramair_remote_execution.py", "--project-root", root,
                "--execution-mode", "rans_only", "--package-scope", "convergence", "--no-renumber-before-decompose", "--n-cores", str(cores)]
            for row in frame.to_dict("records"):
                if row["state"] not in {"revisado", "postprocesado"}: args += ["--case", row["case"]]
            if "--case" not in args: st.info("No quedan combinaciones pendientes.")
            else: start_job("rans_mesh_remote", args)
