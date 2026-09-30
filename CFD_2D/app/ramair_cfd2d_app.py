#!/usr/bin/env python3
"""RamAir Studio shell: version contract, background jobs and modular pages."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = APP_DIR.parent / "scripts"
for folder in (APP_DIR, SCRIPTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import workflow_backend as backend
from workflow_backend import (
    Job, JobManager, command_text, find_project_root, tail_file,
    request_validation_rans_stop, request_validation_pimple_stop,
    request_openfoam_clean_stop, request_openfoam_sweep_stop, openfoam_case_from_command,
)
from validation_convergence_page import render_validation_convergence_lab
from workspace_app import render_application

EXPECTED_BACKEND_API_VERSION = 28
if backend.BACKEND_API_VERSION != EXPECTED_BACKEND_API_VERSION:
    raise RuntimeError(
        f"UI expects API {EXPECTED_BACKEND_API_VERSION}, backend provides "
        f"{backend.BACKEND_API_VERSION}. Restart START_RAMAIR_CFD2D_APP.bat."
    )
st.set_page_config(page_title="RamAir Studio · Design and CFD", layout="wide", initial_sidebar_state="expanded")
ROOT = find_project_root()

@st.cache_resource
def manager_for(path: str) -> JobManager:
    manager = JobManager(Path(path))
    backend.reconcile_validation_runtime(Path(path))
    backend.start_application_idle_watchdog(Path(path), manager)
    return manager

MANAGER = manager_for(str(ROOT))

def start_job(
    stage: str,
    command: list[str],
    *,
    completion_action: dict[str, Any] | None = None,
) -> Job | None:
    try:
        job = MANAGER.start(stage, command)
        st.session_state["selected_job"] = job.job_id
        if completion_action is not None:
            st.session_state[f"completion-action:{job.job_id}"] = completion_action
        st.success(f"Trabajo iniciado: {stage}")
        st.code(command_text(command), language="bash")
        return job
    except Exception as exc:
        st.error(str(exc))
        return None


def selected_job() -> Job | None:
    jobs = MANAGER.list_jobs()
    if not jobs:
        return None
    selected = st.session_state.get("selected_job")
    job = next((item for item in jobs if item.job_id == selected), jobs[0])
    return MANAGER.poll(job)


def _job_console() -> None:
    job = selected_job()
    st.subheader("Ejecucion activa y logs")
    if job is None:
        st.info("Todavia no hay trabajos lanzados desde la interfaz.")
        return
    cols = st.columns([2, 1, 1, 1])
    cols[0].write(f"**{job.stage}**")
    cols[1].metric("Estado", job.status)
    cols[2].metric("PID", job.pid or "-")
    cols[3].metric("Codigo", "-" if job.returncode is None else job.returncode)
    token = f"{job.job_id}:{job.status}"
    previous_token = st.session_state.get("last-observed-job-status")
    st.session_state["last-observed-job-status"] = token
    terminal = job.status in {
        "COMPLETED", "FAILED", "PAUSED_RECOVERABLE", "REVIEW_REQUIRED", "APPROVED", "REJECTED",
    }
    if terminal and previous_token and previous_token != token:
        message = "Tarea finalizada correctamente." if job.status == "COMPLETED" else f"Tarea finalizada con estado {job.status}."
        st.toast(message)
        st.session_state["last-finished-job"] = token
        action = st.session_state.pop(f"completion-action:{job.job_id}", None)
        if job.status == "COMPLETED" and isinstance(action, dict) and action.get("kind") == "reload_configuration":
            st.session_state["_pending_configuration_reload"] = action
        elif job.status == "COMPLETED" and isinstance(action, dict) and action.get("kind") == "select_case":
            st.session_state["_preferred_library_case_after_restore"] = action.get("case")
    st.caption(command_text(job.command))
    st.code(tail_file(Path(job.log_path), 180) or "Esperando salida...", language="text")
    if job.status == "RUNNING":
        st.caption("Estado y log actualizados cada 2 segundos mientras la tarea esta activa.")
    else:
        st.caption("Estado final persistido; no hay actualizacion periodica en reposo.")
    action_cols = st.columns([1, 6])
    if job.status == "RUNNING" and not job.stop_requested_at and action_cols[0].button("Solicitar parada", key="stop-job"):
        validation_rans_stages = {
            "validation_lab_rans_run_all",
            "validation_lab_rans_run_one",
            "validation_rans_extend_review",
        }
        if job.stage in validation_rans_stages:
            try:
                request = request_validation_rans_stop(ROOT, job.command)
                MANAGER.mark_stop_requested(job)
                st.warning(
                    "Se solicito una parada reanudable de la base RANS. "
                    "OpenFOAM escribira el ultimo estado disponible y la cola "
                    "conservara campos, historiales y configuracion congelada "
                    "para continuar desde ese punto. "
                    f"Base activa: {request.get('mesh_id') or 'detectando'}."
                )
            except Exception as exc:
                st.error(f"No se pudo solicitar la parada reanudable: {exc}")
        elif job.stage in {
            "validation_pimple_execute",
            "validation_lab_pimple_execute",
            "validation_lab_pimple_run",
        }:
            try:
                request = request_validation_pimple_stop(ROOT)
                MANAGER.mark_stop_requested(job)
                st.warning(
                    "Se solicito una parada reanudable del estudio PIMPLE. "
                    "La fase activa escribira su estado y no se iniciara la siguiente. "
                    f"Caso: {request.get('case_dir') or 'pendiente de publicar'}."
                )
            except Exception as exc:
                st.error(f"No se pudo solicitar la parada PIMPLE: {exc}")
        elif job.stage in {
            "solver", "solver_sweep", "steady_extend", "steady_start_transient",
            "ls1_validation_solver", "ls1_validation_solver_queue",
        }:
            try:
                cdir = openfoam_case_from_command(job.command)
                if cdir is None:
                    raise RuntimeError("El trabajo activo no contiene un argumento --case verificable.")
                backup = request_openfoam_clean_stop(cdir, "writeNow")
                sweep_marker = None
                if job.stage == "solver_sweep":
                    sweep_marker = request_openfoam_sweep_stop(job.command)
                MANAGER.mark_stop_requested(job)
                st.warning(
                    "OpenFOAM recibio stopAt writeNow. El proceso continua hasta escribir el ultimo estado y reconstruir MPI. "
                    f"Copia de controlDict: {backup}"
                    + (f" El barrido se detendra tras este angulo: {sweep_marker}" if sweep_marker else "")
                )
            except Exception as exc:
                st.error(f"No se pudo solicitar la parada limpia: {exc}")
        elif job.stage == "workspace_queue":
            from ramair_workspace import write_json
            manifest = Path(job.command[job.command.index("--manifest")+1])
            write_json(manifest.with_suffix(".control.json"), {"action": "pause_all"})
            MANAGER.mark_stop_requested(job)
            st.info("Cola pausada: se escribirá el estado reiniciable de cada ángulo activo.")
        else:
            MANAGER.stop(job)
            st.warning("Se solicito la terminacion del proceso activo.")
    elif job.status == "RUNNING" and job.stop_requested_at:
        action_cols[1].warning(
            "Parada en curso. OpenFOAM conserva el ultimo estado escrito; use la "
            "accion forzada solo si no responde tras el periodo de gracia."
        )
        if action_cols[0].button("Forzar parada", key="force-stop-job"):
            MANAGER.force_stop(job)
            st.warning("Se envio SIGINT al grupo real del solver y MPI.")


def job_console() -> None:
    job = selected_job()
    refresh = "2s" if job is not None and job.status == "RUNNING" else None
    st.fragment(_job_console, run_every=refresh)()

backend.touch_application_heartbeat(ROOT)
render_application(ROOT, MANAGER, start_job, render_validation_convergence_lab)
with st.expander("Monitor de trabajos y registro técnico", expanded=bool(selected_job() and selected_job().status == "RUNNING")):
    job_console()
