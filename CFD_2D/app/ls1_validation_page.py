"""Independent LS(1)-0417 closed-airfoil polar validation UI."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

from ramair_2d_ls1_validation_study import validation_phase_plan, validation_solver_profile
from ramair_2d_mesh_numerics import quality_controls_for_mesh
from validation_plotting import close_figures, coefficient_figure, residual_figure

from workflow_backend import (
    JobManager,
    case_directory,
    case_writer_command,
    open_local_folder,
    openfoam_case_from_command,
    open_mesh_viewer,
    open_paraview_case,
    interrupt_openfoam_case,
    postprocess_command,
    staged_runner_command,
    sweep_runner_command,
    validation_publish_command,
    validation_monitor_snapshot,
    request_openfoam_clean_stop,
)


StartJob = Callable[..., Any]
STUDY_NAME = "ls1_0417_closed_polar_M0p15_Re1p9e6"
VARIANT = "reference_uncut_validation_1m"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _alpha_from_dir(name: str) -> float | None:
    if not name.startswith("alpha_"):
        return None
    token = name[6:]
    if not token or token[0] not in {"p", "m"}:
        return None
    sign = -1.0 if token[0] == "m" else 1.0
    try:
        return sign * float(token[1:].replace("p", "."))
    except ValueError:
        return None


def _validation_monitor_mode(
    case: Path | None,
    status: dict[str, Any],
    staged_status: dict[str, Any] | None = None,
) -> str:
    """Resolve SIMPLE versus PIMPLE from the active dictionaries first."""
    label = " ".join(str(status.get(key) or "") for key in ("phase", "stage", "mode"))
    if re.search(r"transient|pimple|urans|phase_[a-e]", label, re.IGNORECASE):
        return "URANS"
    if re.search(r"steady|simple|\brans\b", label, re.IGNORECASE):
        return "RANS"
    if case is not None:
        schemes = case / "system/fvSchemes"
        if schemes.is_file():
            text = schemes.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"ddtSchemes\s*\{\s*default\s+steadyState\s*;", text, re.DOTALL):
                return "RANS"
    staged_label = " ".join(
        str((staged_status or {}).get(key) or "")
        for key in ("status", "phase", "stage", "current_phase")
    )
    if re.search(r"steady|simple|\brans\b", staged_label, re.IGNORECASE):
        return "RANS"
    if re.search(r"transient|pimple|urans|phase_[a-e]", staged_label, re.IGNORECASE):
        return "URANS"
    return "URANS"


def _effective_validation_status(
    run_status: dict[str, Any],
    staged_status: dict[str, Any],
    fallback: str = "REGISTRADO",
) -> str:
    """Reject stale wrapper completion when the aerodynamic run is partial."""
    live = str(run_status.get("status") or "").upper()
    staged = str(staged_status.get("status") or "").upper()
    if live in {"RUNNING", "STOP_REQUESTED", "STOPPING"}:
        return live
    if live in {
        "STOPPED_PARTIAL", "TIMEOUT_PARTIAL", "CASE_TIMEOUT_PARTIAL",
        "RUN_FAILED", "RUN_COMMAND_FAILED", "DIVERGED",
    }:
        return live
    if staged == "TRANSIENT_STAGE_FINISHED":
        complete = staged_status.get("production_complete") is True
        solver_complete = live in {"RUN_COMPLETED", "CONVERGED_STATISTICALLY"}
        return staged if complete and solver_complete else "TRANSIENT_STAGE_PARTIAL"
    return staged or live or fallback


def _phase_at_convective_time(plan: dict[str, Any], time_star: float) -> tuple[str, float]:
    """Return the active A-E phase and its requested dimensionless time step."""
    elapsed = 0.0
    stages = list(plan.get("stages") or [])
    for index, item in enumerate(stages):
        elapsed += max(0.0, float(item.get("duration_time_star") or 0.0))
        if time_star <= elapsed + 1.0e-9 or index == len(stages) - 1:
            target = float(plan.get("target_deltaT_star") or 0.0)
            factor = float(item.get("dt_factor") or 1.0)
            return str(item.get("stage") or ""), target * factor
    return "", 0.0


def _latest_physical_time(case: Path) -> float:
    values: list[float] = []
    for path in case.iterdir() if case.is_dir() else []:
        if not path.is_dir():
            continue
        try:
            values.append(float(path.name))
        except ValueError:
            continue
    return max(values, default=0.0)


def _maximum_reported_yplus(result_case: Path) -> float | None:
    """Return the largest measured wall y+ from the newest available report."""
    reports = [path for path in result_case.rglob("wall_yplus_vs_xc.csv") if path.is_file()]
    if not reports:
        return None
    latest = max(reports, key=lambda path: path.stat().st_mtime)
    try:
        frame = pd.read_csv(latest)
    except (OSError, pd.errors.ParserError):
        return None
    columns = [column for column in frame.columns if "yplus" in str(column).lower()]
    if not columns:
        return None
    values = pd.to_numeric(frame[columns[0]], errors="coerce").dropna()
    return float(values.max()) if not values.empty else None


def _control_dict_scalar(case: Path, key: str) -> float | None:
    path = case / "system/controlDict"
    if not path.is_file():
        return None
    match = re.search(
        rf"^\s*{re.escape(key)}\s+([-+0-9.eE]+)\s*;",
        path.read_text(encoding="utf-8", errors="ignore"),
        re.MULTILINE,
    )
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


def _selected_core_count(
    case: Path,
    status: dict[str, Any],
    staged_status: dict[str, Any],
) -> int | None:
    """Resolve the effective MPI rank count from durable execution evidence."""
    plan = _read_json(case / "parallel_execution_plan.json")
    for source in (plan, status, staged_status):
        for key in ("effective_ranks", "n_cores", "selected_n_cores", "mpi_ranks"):
            value = source.get(key)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                continue
    commands = [
        staged_status.get("transient_command"),
        staged_status.get("steady_command"),
    ]
    for command in commands:
        if not isinstance(command, list):
            continue
        try:
            index = command.index("--n-cores")
            value = int(command[index + 1])
        except (ValueError, IndexError, TypeError):
            continue
        if value > 0:
            return value
    return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            default=lambda value: str(value) if isinstance(value, Path) else _raise_json_type(value),
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _raise_json_type(value: Any) -> None:
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _validation_execution_monitor(root: Path, refresh_seconds: int) -> None:
    """Show live OpenFOAM scalars and retain the raw console as evidence."""
    @st.fragment(run_every=max(2, int(refresh_seconds)))
    def render() -> None:
        manager = JobManager(root)
        jobs = [
            manager.poll(job)
            for job in manager.list_jobs(limit=40)
            if str(job.stage).startswith("ls1_validation_")
        ]
        case_root = root / "CFD_2D/openfoam_cases" / VARIANT
        case_dirs = sorted(
            [path for path in case_root.glob("alpha_*") if path.is_dir()],
            key=lambda path: (_alpha_from_dir(path.name) is None, _alpha_from_dir(path.name) or 0.0),
        )
        if not jobs and not case_dirs:
            st.caption("No hay ejecuciones ni casos de validación registrados todavía.")
            return
        active_jobs = [job for job in jobs if job.status == "RUNNING"]
        follow_active = st.toggle(
            "Seguir automáticamente la ejecución activa",
            value=True,
            key="ls1-validation-follow-active-monitor",
        )
        active_job = active_jobs[0] if active_jobs else None
        active_case = openfoam_case_from_command(active_job.command) if active_job else None
        selectable = [path for path in case_dirs if _alpha_from_dir(path.name) is not None]
        default_case = active_case if active_case in selectable else (selectable[-1] if selectable else None)
        selected_case = default_case
        if selectable and (not follow_active or active_case is None):
            selected_case = st.selectbox(
                "Ejecución a revisar",
                selectable,
                index=selectable.index(default_case) if default_case in selectable else len(selectable) - 1,
                format_func=lambda path: f"α={_alpha_from_dir(path.name):g}° | {path.name}",
                key="ls1-validation-monitor-case",
            )
        case = active_case if follow_active and active_case is not None else selected_case
        if case is None:
            st.caption("No se pudo resolver un caso de validación para el monitor.")
            return
        job = next(
            (
                candidate for candidate in jobs
                if openfoam_case_from_command(candidate.command) == case
            ),
            active_job or (jobs[0] if jobs else None),
        )
        status = _read_json(case / "run_status.json")
        staged_status = _read_json(case / "staged_run_status.json")
        case_config = _read_json(case / "case_config.json")
        tc_s = float(case_config.get("chord_m", 1.0)) / max(
            float(case_config.get("velocity_m_s", 0.0) or 0.0), 1.0e-30,
        )
        phase = str(
            status.get("phase") or status.get("stage")
            or staged_status.get("current_phase") or ""
        )
        mode = _validation_monitor_mode(case, status, staged_status)
        requested_delta_t = 0.0
        if mode == "URANS" and not phase:
            phase_plan_path = (
                root / "CFD_2D/validation_studies" / STUDY_NAME
                / "configurations/validation_phase_plan.json"
            )
            phase_plan = _read_json(phase_plan_path) or validation_phase_plan()
            phase, requested_delta_t_star = _phase_at_convective_time(
                phase_plan,
                _latest_physical_time(case) / max(tc_s, 1.0e-30),
            )
            requested_delta_t = requested_delta_t_star * tc_s
        stage_view = st.radio(
            "Datos mostrados",
            ["current", "RANS"],
            horizontal=True,
            format_func=lambda value: "Etapa actual / URANS" if value == "current" else "RANS archivado",
            key=f"ls1-validation-monitor-stage-{case.name}",
        )
        monitor_case = case
        if stage_view == "RANS":
            histories = sorted(
                (case / "steadyInitialization/history").glob("run_*"),
                key=lambda path: path.stat().st_mtime,
            )
            if histories:
                monitor_case = histories[-1]
                mode = "RANS"
                phase = "SIMPLE"
            else:
                st.info("Este caso todavía no tiene una etapa RANS archivada.")
        quality = _read_json(root / f"CFD_2D/meshes/{VARIANT}/mesh_quality_report.json")
        snapshot = validation_monitor_snapshot(
            monitor_case,
            mode=mode,
            run_id=job.job_id if job else case.name,
            topology="closed",
            mesh_level="validation",
            cell_count=int(quality.get("checkMesh_cell_count") or quality.get("cell_count") or 0),
            stage=phase,
            tc_s=tc_s,
            target_delta_t=float(case_config.get("maxDeltaT_s") or case_config.get("deltaT_s") or 0.0),
            phase_delta_t=float(
                status.get("deltaT")
                or _control_dict_scalar(case, "deltaT")
                or requested_delta_t
                or case_config.get("deltaT_s")
                or 0.0
            ),
        )
        alpha = _alpha_from_dir(case.name)
        courant_rows = list(snapshot.get("courant") or [])
        observed_co = max(
            [float(row.get("maxCo") or row.get("maximum") or row.get("max") or 0.0) for row in courant_rows]
            or [0.0]
        )
        elapsed = snapshot.get("elapsed_s")
        metrics = st.columns(6)
        metrics[0].metric("Caso", f"α={alpha:g}°" if alpha is not None else case.name)
        live_status = str(status.get("status") or "").upper()
        displayed_status = _effective_validation_status(
            status,
            staged_status,
            job.status if job else "REGISTRADO",
        )
        metrics[1].metric("Estado", displayed_status)
        metrics[2].metric("Solver", f"{mode} / {phase or '-'}")
        metrics[3].metric("dt fijado", f"{float(snapshot.get('phase_deltaT_s') or 0.0):.6g} s" if mode == "URANS" else "iterativo")
        metrics[4].metric("Tiempo real", f"{float(elapsed):.0f} s" if elapsed is not None else "-")
        metrics[5].metric(
            "Cores MPI",
            str(_selected_core_count(case, status, staged_status) or "-"),
        )
        detail = st.columns(5)
        if mode == "RANS":
            detail[0].metric("Iteración", f"{int(float(snapshot.get('iteration_or_time') or 0)):,}")
            gate = snapshot.get("gate") or {}
            detail[1].metric("Stable", "Yes" if str(gate.get("status")).upper() in {"STABLE", "READY_FOR_TRANSIENT"} else "No")
            detail[2].metric("Muestras", str(len(snapshot.get("forces") or [])))
        else:
            detail[0].metric(
                "Timesteps totales ejecutados",
                f"{int(snapshot.get('steps_total_executed') or snapshot.get('steps_observed') or 0):,}",
            )
            detail[1].metric("Tiempo simulado", f"{float(snapshot.get('physical_time_s') or 0.0):.6g} s")
            detail[2].metric("Tiempo t*", f"{float(snapshot.get('convective_time') or 0.0):.4f}")
            detail[3].metric("Co máximo observado", f"{observed_co:.4g}")
            detail[4].metric("Fase A-E", phase or "-")
        if case and case.is_dir():
            residual_plot, _ = residual_figure(snapshot, mode=mode)
            coefficient_plot, _ = coefficient_figure(snapshot, mode=mode)
            try:
                charts = st.columns(2)
                charts[0].pyplot(residual_plot, width="stretch")
                charts[1].pyplot(coefficient_plot, width="stretch")
            finally:
                close_figures(residual_plot, coefficient_plot)
        stop_cols = st.columns(2)
        running_evidence = live_status in {"RUNNING", "STOP_REQUESTED", "STOPPING"}
        if stop_cols[0].button(
            "Solicitar parada limpia",
            disabled=not (running_evidence or (job is not None and job.status == "RUNNING")),
            key=f"ls1-validation-clean-stop-{case.name}",
            help="Cambia stopAt a writeNow; OpenFOAM escribe un checkpoint antes de salir.",
        ):
            backup = request_openfoam_clean_stop(case, "writeNow")
            if job is not None and job.status == "RUNNING":
                manager.mark_stop_requested(job)
            st.warning(f"Parada limpia solicitada. Copia de controlDict: {backup}")
        if stop_cols[1].button(
            "Interrumpir solver que no responde",
            disabled=not running_evidence,
            key=f"ls1-validation-interrupt-{case.name}",
            help="Usar solo después de solicitar writeNow y esperar; envía SIGINT al grupo real del solver.",
        ):
            st.warning(interrupt_openfoam_case(case))
        with st.expander("Consola de la ejecución", expanded=False):
            log_path = Path(job.log_path) if job else case / "log.foamRun"
            if log_path.is_file():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                st.code("\n".join(lines[-80:]), language="text")
        st.caption(
            f"Monitor actualizado cada {max(2, int(refresh_seconds))} s. "
            f"Origen: {monitor_case}."
        )

    render()


def _visible_validation_image(path: Path) -> bool:
    """Keep the application aligned with the canonical post-process catalogue."""
    lowered = path.name.lower()
    if any(part in {
        "velocity_frames", "pressure_frames",
        "pressure_contour_frames", "vorticity_contour_frames",
    } for part in path.parts):
        return False
    excluded = (
        "initial", "hotspot", "separation_time", "reverse_flow",
        "velocity_streamlines_contours", "q_positive_contour", "yplus_rans_final",
        "yplus_urans_final",
    )
    if any(token in lowered for token in excluded):
        return False
    allowed = (
        "cp_airfoil_", "velocity_", "vorticity_", "courant_",
        "pressure_contours_", "q_pressure_contours_",
        "q_vorticity_contours_", "cl_cd_cm_history",
        "aerodynamic_efficiency", "solver_residuals", "courant_history",
        "wall_yplus_vs_xc", "wall_cp_vs_xc", "wall_normal_velocity_profiles",
        "boundary_layer_thickness_comparison", "wall_shear_stress_vs_xc",
        "skin_friction_coefficient_vs_xc", "separation_overlay_cp_cf",
    )
    return any(token in lowered for token in allowed)


def _render_validation_postprocess_results(
    root: Path,
    result_case: Path,
    case: Path,
    alpha: float,
) -> None:
    """Refresh only while a postprocess job is active, then perform one final reload."""
    manager = JobManager(root)
    postprocess_prefixes = (
        "ls1_validation_postprocess", "ls1_validation_animations"
    )
    jobs = [
        manager.poll(job) for job in manager.list_jobs(limit=30)
        if str(job.stage).startswith(postprocess_prefixes)
    ]
    active_on_render = any(job.status == "RUNNING" for job in jobs)

    @st.fragment(run_every=2 if active_on_render else None)
    def render() -> None:
        current_jobs = [manager.poll(job) for job in jobs]
        still_running = any(job.status == "RUNNING" for job in current_jobs)
        if active_on_render:
            st.caption(
                "Postproceso en curso: los productos escalares aparecen al quedar escritos; "
                "la vista completa se recargará al finalizar ParaView."
            )
            if not still_running:
                st.rerun()
        rans_products, urans_products = st.tabs([
            "RANS / iteraciones", "URANS / tiempo físico",
        ])
        for container, stage_name in ((rans_products, "RANS"), (urans_products, "URANS")):
            with container:
                stage_root = result_case / stage_name
                if not stage_root.is_dir():
                    st.info(f"No hay productos {stage_name} para este ángulo.")
                    continue
                stage_summary = _read_json(stage_root / "stage_summary.json")
                if str(stage_summary.get("status", "")).upper() in {"NOT_AVAILABLE", "DISABLED"}:
                    st.info(
                        f"No hay evidencia {stage_name} válida para este ángulo: "
                        f"{stage_summary.get('reason', 'etapa no ejecutada')}."
                    )
                    continue
                paraview_case = Path(str(stage_summary.get("paraview_case") or ""))
                if not paraview_case.is_dir() and stage_name == "URANS":
                    paraview_case = case
                if paraview_case.is_dir() and st.button(
                    f"Abrir {stage_name} en ParaView",
                    key=f"ls1-validation-open-{stage_name.lower()}-{alpha}",
                ):
                    try:
                        opened = open_paraview_case(root, paraview_case)
                        st.success(f"ParaView solicitado (PID {opened.get('pid', '-')}).")
                    except Exception as exc:
                        st.error(str(exc))
                images = [
                    path for path in sorted(stage_root.rglob("*.png"))
                    if _visible_validation_image(path)
                ]
                if images:
                    image_columns = st.columns(2)
                    for index, image_path in enumerate(images):
                        image_columns[index % 2].image(
                            str(image_path), caption=image_path.stem,
                        )
                animation_names = {
                    "velocity_airfoil_wake.mp4", "velocity_airfoil_wake.gif",
                    "pressure_cp_airfoil_wake.mp4", "pressure_cp_airfoil_wake.gif",
                    "pressure_contours.mp4", "pressure_contours.gif",
                    "vorticity_contours.mp4", "vorticity_contours.gif",
                }
                animations = sorted(
                    path for path in stage_root.rglob("*")
                    if path.is_file() and path.name.lower() in animation_names
                )
                if animations:
                    play = st.toggle(
                        "Reproducir animaciones", value=False,
                        key=f"ls1-validation-play-{stage_name}-{alpha}",
                        help="Desactiva el interruptor para retirar el reproductor y detenerlo.",
                    )
                    if play:
                        for animation in animations:
                            if animation.suffix.lower() == ".gif":
                                st.image(str(animation), caption=animation.name, width="stretch")
                            else:
                                st.video(str(animation), autoplay=True, loop=True)
                summaries = sorted(stage_root.glob("*summary*.json"))
                if summaries:
                    with st.expander(f"Resumen numérico {stage_name}", expanded=True):
                        for summary_path in summaries:
                            st.caption(summary_path.name)
                            st.json(_read_json(summary_path))

    render()


def render_ls1_validation(root: Path, start_job: StartJob) -> None:
    study = root / "CFD_2D/validation_studies" / STUDY_NAME
    manifest = _read_json(study / "study_manifest.json")
    if not manifest:
        st.info(
            "La validación histórica aún está en el contenedor antiguo. La migración registra "
            "los campos existentes por referencia, conserva las tablas y no duplica las simulaciones."
        )
        if st.button("Migrar validación LS(1)-0417", type="primary", key="ls1-validation-migrate"):
            start_job(
                "ls1_validation_migrate",
                [
                    sys.executable,
                    root / "CFD_2D/scripts/ramair_2d_ls1_validation_study.py",
                    "--project-root", root,
                    "--archive-old-workcase",
                ],
            )
        return

    st.info(
        "Validación de polar independiente para LS(1)-0417 cerrado: c=1 m, M=0.15 y Re=1.9e6. "
        "La malla, los casos y los campos permanecen en sus rutas canónicas; esta pestaña conserva "
        "configuración, evidencia y puntos aceptados."
    )
    paths = manifest.get("paths") or {}
    case_root = root / str(paths.get("openfoam_cases"))
    result_root = root / str(paths.get("results"))
    validation_root = root / str(paths.get("postprocess"))
    config_root = root / str(paths.get("configurations"))
    alpha_values = sorted(
        value for value in (_alpha_from_dir(path.name) for path in case_root.glob("alpha_*"))
        if value is not None
    )
    if not alpha_values:
        alpha_values = [-4.0, 0.0, 4.0, 8.0, 12.0]

    mesh_root = root / "CFD_2D/meshes" / VARIANT
    mesh_file = next(
        (candidate for candidate in (
            mesh_root / "mesh_final.msh", mesh_root / "mesh.msh",
            mesh_root / "mesh_attempt_001/mesh.msh",
        ) if candidate.is_file()),
        None,
    )
    mesh_columns = st.columns([3, 1])
    mesh_columns[0].info(
        "Malla de validación en uso: "
        f"`{mesh_file if mesh_file is not None else mesh_root}`"
    )
    if mesh_columns[1].button(
        "Abrir malla en Gmsh",
        disabled=mesh_file is None,
        key="ls1-validation-open-mesh",
    ):
        try:
            open_mesh_viewer(root, mesh_file or mesh_root)
        except Exception as exc:
            st.error(str(exc))
    monitor_refresh = st.slider(
        "Refresco del monitor [s]", 2, 180, 30, 2,
        key="ls1-validation-monitor-refresh",
        help="Afecta solo a la visualización; no modifica ni interrumpe OpenFOAM.",
    )
    with st.expander("Monitor de ejecución de validación", expanded=True):
        _validation_execution_monitor(root, monitor_refresh)

    summary_tab, case_tab, execution_tab, post_tab = st.tabs(
        ["Polar y evidencia", "Caso OpenFOAM", "Ejecución", "Postproceso"]
    )
    with summary_tab:
        figures = [
            (validation_root / "LS1_0417_CL_alpha_validation.png", "CL frente a ángulo de ataque"),
            (validation_root / "LS1_0417_CD_CL_validation.png", "Polar de arrastre"),
            (validation_root / "LS1_0417_CL_over_CD_alpha_validation.png", "Eficiencia CL/CD frente a ángulo"),
            (validation_root / "validation_err_norm_summary.png", "Norma err de la polar publicada"),
            (validation_root / "validation_err2_peak_summary.png", "Error err2 de los máximos publicados"),
            (validation_root / "validation_differences_Cl.png", "Diferencias de CL por ángulo"),
            (validation_root / "validation_differences_Cd.png", "Diferencias de CD por ángulo"),
            (validation_root / "validation_differences_Cl_over_Cd.png", "Diferencias de CL/CD por ángulo"),
            (validation_root / "validation_relative_differences_Cl.png", "Diferencias relativas de CL"),
            (validation_root / "validation_relative_differences_Cd.png", "Diferencias relativas de CD"),
            (validation_root / "validation_relative_differences_Cl_over_Cd.png", "Diferencias relativas de CL/CD"),
        ]
        columns = st.columns(2)
        for index, (path, caption) in enumerate(figures):
            if path.is_file():
                columns[index % 2].image(str(path), caption=caption)
        points_path = validation_root / "ramair_validation_points.csv"
        try:
            points = pd.read_csv(points_path)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            points = pd.DataFrame()
        if points.empty:
            st.warning("Todavía no hay puntos OpenFOAM aceptados en la polar.")
        else:
            st.dataframe(points, width="stretch", hide_index=True)
        st.json(_read_json(validation_root / "validation_summary.json"))

    with case_tab:
        st.markdown("**Configuración trazable del solver de validación**")
        solver_path = config_root / "cfd2d_solver_config.json"
        phase_path = config_root / "validation_phase_plan.json"
        solver_payload = validation_solver_profile(_read_json(solver_path))
        phase_payload = _read_json(phase_path) or validation_phase_plan()
        rans_gate = dict(solver_payload.get("validation_rans_convergence") or {})
        write_strategy = dict(solver_payload.get("validation_write_strategy") or {})
        st.caption(
            "Todas las ejecuciones empiezan con SIMPLE/RANS. El transitorio progresa por A-B-C-D "
            "y solo la fase E entra en el promedio científico. Los límites mostrados impiden guardar "
            "una configuración menos robusta que el contrato de validación."
        )
        steady_cols = st.columns(4)
        steady_cols[0].metric("RANS máximo", "15 000 iteraciones")
        steady_cols[1].metric("Residual RANS", "1e-6")
        steady_cols[2].metric("Relajación U", "0.7")
        steady_cols[3].metric("Relajación nuTilda", "0.7")
        mesh_numerics = quality_controls_for_mesh(
            root / "CFD_2D/meshes" / VARIANT
        )
        if mesh_numerics:
            st.info(
                "Ajuste automático desde checkMesh: "
                f"max non-orthogonality={mesh_numerics['maximum_non_orthogonality_deg']:.3f}°, "
                f"nNonOrthogonalCorrectors={mesh_numerics['n_non_orthogonal_correctors']} "
                f"y laplaciano `{mesh_numerics['laplacian_scheme']}`."
            )
        else:
            st.warning(
                "No se ha encontrado un informe checkMesh para derivar automáticamente "
                "los correctores; el escritor conservará la configuración de respaldo."
            )
        quality_mode = st.selectbox(
            "Control de no ortogonalidad",
            ["automatic", "manual"],
            index=0 if solver_payload.get("mesh_quality_numerics_mode", "automatic") == "automatic" else 1,
            format_func=lambda value: "Automático desde checkMesh" if value == "automatic" else "Manual",
            help=(
                "Automático deriva correctores y laplaciano del máximo de no ortogonalidad de la "
                "malla aprobada. Manual respeta exactamente el número indicado abajo."
            ),
        )
        manual_non_orthogonal = int(
            solver_payload.get("n_non_orthogonal_correctors")
            if solver_payload.get("n_non_orthogonal_correctors") is not None
            else (mesh_numerics or {}).get("n_non_orthogonal_correctors", 0)
        )
        if quality_mode == "manual":
            manual_non_orthogonal = st.number_input(
                "Correctores no ortogonales manuales", min_value=0, max_value=4,
                value=manual_non_orthogonal,
                help="Se aplica tanto a SIMPLE como a PIMPLE solo en modo manual.",
            )
        transient_cols = st.columns(5)
        target_dt_star = transient_cols[0].number_input(
            "dt* objetivo", min_value=0.0001, max_value=0.01,
            value=float(phase_payload.get("target_deltaT_star", 0.0025)), format="%.7f",
            help="Paso convectivo máximo. adjustTimeStep solo lo reduce cuando Co supera el límite.",
        )
        max_co = transient_cols[1].number_input(
            "Co máximo", min_value=1.0, max_value=50.0,
            value=min(50.0, float(phase_payload.get("maxCo", 50.0))),
            help="Protección de estabilidad; no aumenta dt* por encima del valor objetivo.",
        )
        outer = transient_cols[2].number_input(
            "Outer correctors máximos", min_value=1, max_value=5,
            value=min(5, int(phase_payload.get("max_outer_correctors", 5))),
            help="PIMPLE puede salir antes mediante residualControl, pero nunca excede este máximo.",
        )
        n_correctors = transient_cols[3].number_input(
            "Correctores de presión", min_value=1, max_value=6,
            value=int(solver_payload.get("n_correctors", 2)),
            help="Correcciones presión-velocidad dentro de cada outer corrector PIMPLE.",
        )
        transient_residual = transient_cols[4].number_input(
            "Residual PIMPLE", min_value=1.0e-7, max_value=1.0e-2,
            value=float(((solver_payload.get("outer_corrector_residual_control") or {}).get("fields") or {}).get("U", {}).get("tolerance", 1.0e-4)),
            format="%.1e",
        )
        st.markdown("#### Gate RANS: residuales y estabilidad aerodinámica")
        gate_cols = st.columns(4)
        rans_window = gate_cols[0].number_input(
            "Ventana [muestras]", min_value=100, max_value=5000,
            value=int(rans_gate.get("window_samples", 1000)), step=100,
            help="Ventana grande usada para medias móviles y fluctuaciones de Cl, Cd y Cm.",
        )
        rans_residual = gate_cols[1].number_input(
            "Residual diagnóstico", min_value=1.0e-9, max_value=1.0e-2,
            value=float(rans_gate.get("residual_tolerance", 1.0e-6)), format="%.1e",
            help="Debe cumplirse junto con la estabilidad de fuerzas; no activa residualControl de SIMPLE.",
        )
        rans_mean_tol = gate_cols[2].number_input(
            "Cambio de media [%]", min_value=0.01, max_value=10.0,
            value=float(rans_gate.get("mean_change_tolerance_percent", 0.5)), format="%.2f",
        )
        rans_fluct_tol = gate_cols[3].number_input(
            "Fluctuación [%]", min_value=0.01, max_value=20.0,
            value=float(rans_gate.get("fluctuation_tolerance_percent", 1.0)), format="%.2f",
        )
        st.markdown("#### Escritura de campos")
        write_cols = st.columns(3)
        rans_write_interval = write_cols[0].number_input(
            "RANS: campo completo cada N iteraciones", min_value=100, max_value=10000,
            value=int(write_strategy.get("rans_full_field_interval_iterations", 1000)), step=100,
            help="Residuales y coeficientes siguen escribiéndose en cada iteración.",
        )
        urans_write_interval_star = write_cols[1].number_input(
            "URANS: intervalo físico [t*]", min_value=0.005, max_value=2.0,
            value=float(write_strategy.get("urans_interval_time_star", 0.10)), format="%.4f",
            help="Se traduce a adjustableRunTime para mantener capturas regulares aunque cambie deltaT.",
        )
        purge_write = write_cols[2].number_input(
            "Estados volumétricos conservados", min_value=0, max_value=500,
            value=int(write_strategy.get("purge_write", 24)),
            help=(
                "0 conserva todos. N>0 conserva de forma circular los N estados más recientes, "
                "no los N primeros; la retención se mantiene al cambiar de A a E."
            ),
        )
        with st.expander("Qué significan los parámetros de escritura", expanded=False):
            st.markdown(
                "- `validation_write_strategy` es la fuente autoritativa del estudio.\n"
                "- `urans_interval_time_star`/`field_write_interval_star` es el intervalo "
                "adimensional entre campos completos; se convierte mediante `Δt = Δt* c/U∞`.\n"
                "- `field_write_control=adjustableRunTime` alinea las capturas con tiempos físicos "
                "aunque el paso automático cambie.\n"
                "- `field_write_interval_s` y `field_write_interval_steps` son campos heredados y "
                "se eliminan del perfil de validación para que no sobreescriban el intervalo en `t*`.\n"
                "- `purgeWrite=N` conserva los últimos N estados volumétricos durante toda A-E.\n"
                "- `average_from_fraction` es `inicio de E / final de E`; solo E entra en la media."
            )
        phase_d_gate = dict(
            phase_payload.get("phase_d_steady_equivalence")
            or solver_payload.get("validation_phase_d_steady_equivalence")
            or {}
        )
        with st.expander("Opción estricta: aceptar la ventana final de D", expanded=False):
            st.caption(
                "Compara Cl, Cd, Cm y Cl/Cd medios del final de SIMPLE con la última ventana de D. "
                "Solo omite E cuando todas las diferencias y fluctuaciones están por debajo de los "
                "umbrales; por defecto permanece desactivado para no acortar inadvertidamente una polar."
            )
            phase_d_gate_enabled = st.toggle(
                "Permitir aceptación estacionaria tras D",
                value=bool(phase_d_gate.get("enabled", False)),
                key="ls1-validation-phase-d-gate-enabled",
            )
            d_gate_cols = st.columns(4)
            phase_d_window = d_gate_cols[0].number_input(
                "Ventana final D [t*]", min_value=0.5, max_value=10.0,
                value=float(phase_d_gate.get("window_time_star", 2.5)), format="%.2f",
            )
            phase_d_samples = d_gate_cols[1].number_input(
                "Muestras mínimas", min_value=50, max_value=5000,
                value=int(phase_d_gate.get("minimum_samples", 200)), step=50,
            )
            phase_d_mean_tol = d_gate_cols[2].number_input(
                "Diferencia RANS-D [%]", min_value=0.01, max_value=5.0,
                value=float(phase_d_gate.get("mean_difference_tolerance_percent", 0.30)), format="%.2f",
            )
            phase_d_fluct_tol = d_gate_cols[3].number_input(
                "Fluctuación D [%]", min_value=0.01, max_value=5.0,
                value=float(phase_d_gate.get("fluctuation_tolerance_percent", 0.50)), format="%.2f",
            )
        st.markdown("#### Secuencia transitoria A-E")
        phase_table = pd.DataFrame(phase_payload.get("stages") or validation_phase_plan()["stages"])
        edited_phases = st.data_editor(
            phase_table,
            hide_index=True,
            width="stretch",
            disabled=["stage", "sampling"],
            key="ls1-validation-phase-editor",
            column_config={
                "stage": st.column_config.TextColumn("Fase"),
                "scheme": st.column_config.SelectboxColumn(
                    "Esquema", options=["Euler", "backward", "CrankNicolson 0.9"], required=True,
                ),
                "dt_factor": st.column_config.NumberColumn("Factor dt", min_value=0.05, max_value=1.0),
                "duration_time_star": st.column_config.NumberColumn("Duración [t*]", min_value=0.01),
                "sampling": st.column_config.CheckboxColumn("Producción"),
            },
        )
        if st.button("Guardar configuración del solver", key="ls1-validation-save-solver"):
            edited_phase_records = edited_phases.to_dict(orient="records")
            phase_total = sum(float(item.get("duration_time_star", 0.0)) for item in edited_phase_records)
            production_start = sum(
                float(item.get("duration_time_star", 0.0))
                for item in edited_phase_records
                if not bool(item.get("sampling", False))
            )
            if phase_total <= 0.0 or production_start >= phase_total:
                st.error("La tabla A-E debe contener una fase de producción con duración positiva.")
                st.stop()
            solver_payload = validation_solver_profile(solver_payload)
            solver_payload.update({
                "deltaT_star": float(target_dt_star), "maxDeltaT_star": float(target_dt_star),
                "maxCo": float(max_co), "n_outer_correctors": int(outer),
                "n_correctors": int(n_correctors),
                "mesh_quality_numerics_mode": quality_mode,
                "n_non_orthogonal_correctors": int(manual_non_orthogonal),
                "steady_numerics": {
                    **dict(solver_payload.get("steady_numerics") or {}),
                    "n_non_orthogonal_correctors": int(manual_non_orthogonal),
                    "p_relaxation": 0.3,
                    "U_relaxation": 0.7,
                    "nuTilda_relaxation": 0.7,
                },
                "endTime_star": float(phase_total),
                "average_from_fraction": float(production_start / phase_total),
                "outer_corrector_residual_control": {
                    "enabled": True,
                    "fields": {
                        "p": {"tolerance": float(transient_residual), "relTol": 0.0},
                        "U": {"tolerance": float(transient_residual), "relTol": 0.0},
                        "nuTilda": {"tolerance": float(transient_residual), "relTol": 0.0},
                    },
                },
                "steady_write_interval_iterations": int(rans_write_interval),
                "field_write_control": "adjustableRunTime",
                "field_write_interval_star": float(urans_write_interval_star),
                "purgeWrite": int(purge_write),
                "steady_native_residual_control_enabled": False,
                "validation_rans_convergence": {
                    "window_samples": int(rans_window),
                    "minimum_samples": min(int(rans_window), max(100, int(rans_window) // 2)),
                    "residual_tolerance": float(rans_residual),
                    "mean_change_tolerance_percent": float(rans_mean_tol),
                    "fluctuation_tolerance_percent": float(rans_fluct_tol),
                    "required_consecutive_windows": 3,
                },
                "validation_write_strategy": {
                    "rans_full_field_interval_iterations": int(rans_write_interval),
                    "urans_control": "adjustableRunTime",
                    "urans_interval_time_star": float(urans_write_interval_star),
                    "purge_write": int(purge_write),
                    "authoritative": True,
                },
                "validation_phase_d_steady_equivalence": {
                    "enabled": bool(phase_d_gate_enabled),
                    "window_time_star": float(phase_d_window),
                    "minimum_samples": int(phase_d_samples),
                    "mean_difference_tolerance_percent": float(phase_d_mean_tol),
                    "fluctuation_tolerance_percent": float(phase_d_fluct_tol),
                    "coefficient_floors": {
                        "Cl": 0.05, "Cd": 0.005, "Cm": 0.005, "Cl_over_Cd": 1.0,
                    },
                },
            })
            if quality_mode == "automatic":
                solver_payload.pop("n_non_orthogonal_correctors", None)
                solver_payload["steady_numerics"].pop(
                    "n_non_orthogonal_correctors", None,
                )
            solver_payload["validation_polar_protocol"] = {
                "enforced": True,
                "steady_required": True,
                "steady_max_iterations": 15000,
                "target_deltaT_star": float(target_dt_star),
                "maxCo_emergency_guard": float(max_co),
                "maximum_outer_correctors": int(outer),
                "settling_phase_D_time_star": float(next(
                    (item["duration_time_star"] for item in edited_phase_records if item["stage"] == "D"),
                    0.0,
                )),
                "production_phase_E_time_star": float(next(
                    (item["duration_time_star"] for item in edited_phase_records if item["stage"] == "E"),
                    0.0,
                )),
                "production_start_time_star": float(production_start),
                "total_time_star": float(phase_total),
            }
            phase_payload = validation_phase_plan()
            phase_payload.update({
                "target_deltaT_star": float(target_dt_star), "maxCo": float(max_co),
                "max_outer_correctors": int(outer),
                "production_start_time_star": float(production_start),
                "total_time_star": float(phase_total),
                "average_from_fraction": float(production_start / phase_total),
                "phase_d_steady_equivalence": dict(
                    solver_payload["validation_phase_d_steady_equivalence"]
                ),
            })
            phase_payload["stages"] = edited_phase_records
            phase_payload["write_strategy"] = {
                "control": "adjustableRunTime",
                "interval_time_star": float(urans_write_interval_star),
                "purge_write": int(purge_write),
            }
            _write_json_atomic(solver_path, solver_payload)
            _write_json_atomic(phase_path, phase_payload)
            st.success("Configuración y secuencia A-B-C-D-E guardadas en el estudio independiente.")
        with st.expander("Configuración avanzada completa"):
            st.json(solver_payload)
        selected_case_alpha = st.number_input(
            "Ángulo del caso [°]", min_value=-89.0, max_value=89.0,
            value=float(alpha_values[0]), step=1.0,
            key="ls1-validation-case-alpha"
        )
        selected_case = case_directory(root, VARIANT, selected_case_alpha)
        st.code(str(selected_case), language="text")
        overwrite = st.checkbox(
            "Confirmo que deseo regenerar los diccionarios del caso seleccionado",
            key="ls1-validation-overwrite-case",
        )
        if st.button("Generar o actualizar caso OpenFOAM", disabled=not overwrite):
            start_job(
                "ls1_validation_case_writer",
                case_writer_command(
                    root, variant=VARIANT, alpha=selected_case_alpha,
                    require_converted_polymesh=True, overwrite=True,
                    existing_case_action="archive", reynolds=1.9e6,
                    solver_config_path=solver_path,
                ),
            )

    with execution_tab:
        prepared_alphas = sorted(set(alpha_values + [float(selected_case_alpha)]))
        alpha = st.selectbox("Ángulo a ejecutar", prepared_alphas, key="ls1-validation-run-alpha")
        cols = st.columns(4)
        cores = cols[0].number_input("Procesos", min_value=1, max_value=16, value=8)
        case_timeout_h = cols[1].number_input(
            "Timeout por caso [h]", min_value=1.0, max_value=72.0, value=6.0,
            help=(
                "Límite de seguridad de la ejecución secuencial completa de un ángulo. "
                "Al alcanzarlo se preservan los campos y la cola pasa al siguiente caso."
            ),
        )
        steady_timeout_min = cols[2].number_input(
            "Límite RANS [min]", min_value=30.0, max_value=720.0, value=180.0,
            help=(
                "Protección temporal visible para SIMPLE. Normalmente RANS termina antes al "
                "alcanzar 15.000 iteraciones o estabilidad; si llega a este límite con campos "
                "finitos, la cola inicia URANS desde el último estado."
            ),
        )
        resume = cols[3].checkbox("Continuar desde último estado", value=False)
        timeout = 60.0 * float(case_timeout_h)
        parallel_cols = st.columns(2)
        parallel_mode = parallel_cols[0].radio(
            "Selección paralela",
            ["Auto", "Manual"],
            horizontal=True,
            help=(
                "Auto reutiliza un perfil compatible por hash de malla/solver o calcula un plan "
                "con los núcleos físicos. Manual respeta el número de procesos indicado arriba."
            ),
            key="ls1-validation-parallel-mode",
        )
        renumber_before_decompose = parallel_cols[1].toggle(
            "Renumber en caso nuevo",
            value=True,
            help=(
                "Reordena y vuelve a comprobar una malla solo antes de descomponer un caso limpio. "
                "Nunca se aplica silenciosamente al reanudar checkpoints."
            ),
            key="ls1-validation-renumber-new-case",
        )
        automatic_core_selection = parallel_mode == "Auto"
        st.warning(
            "Una ejecución nueva siempre realiza RANS antes de A-B-C-D-E. Activa continuar solo "
            "para reanudar una fase transitoria previamente detenida."
        )
        confirm = st.checkbox("Confirmo la ejecución OpenFOAM", key="ls1-validation-run-confirm")
        if st.button("Ejecutar RANS + URANS", type="primary", disabled=not confirm):
            start_job(
                "ls1_validation_solver",
                staged_runner_command(
                    root, variant=VARIANT, alpha=alpha, solver="auto",
                    execution_backend="native", n_cores=int(cores), timeout_min=float(timeout),
                    run=True, stop_if_checkmesh_fails=True, pyfoam_live_monitor=False,
                    cleanup_processor_directories=True, stop_when_force_stable=True,
                    convergence_minimum_time_star=20.0, convergence_window_time_star=10.0,
                    convergence_mean_tolerance=0.02, convergence_oscillation_tolerance=0.1,
                    steady_initialization=True, steady_timeout_min=float(steady_timeout_min),
                    steady_force_window_samples=int(rans_window),
                    steady_force_mean_tolerance_percent=float(rans_mean_tol),
                    steady_force_fluctuation_tolerance_percent=float(rans_fluct_tol),
                    continue_transient_after_steady_timeout=True, resume=resume,
                    resume_additional_time_star=None,
                    transient_phase_plan=phase_path,
                    automatic_core_selection=automatic_core_selection,
                    renumber_before_decompose=renumber_before_decompose,
                ),
            )
        st.caption("La parada manual y el estado se gestionan desde el panel global de ejecución activa.")
        st.markdown("#### Cola secuencial sin intervención")
        queue_alphas = st.multiselect(
            "Ángulos y orden de ejecución",
            prepared_alphas,
            default=[],
            key="ls1-validation-run-queue",
            help=(
                "Cada ángulo ejecuta RANS y después A-B-C-D-E. Un fallo, timeout "
                "o divergencia queda registrado y la cola continúa con el siguiente."
            ),
        )
        queue_policy = st.radio(
            "Política para casos ya existentes",
            ["resume_or_start", "delete_and_restart"],
            format_func=lambda value: (
                "Continuar existentes, iniciar nuevos y omitir finalizados"
                if value == "resume_or_start"
                else "Regenerar y empezar todos desde cero"
            ),
            help=(
                "La segunda opción vuelve a crear cada caso desde la malla aprobada y la configuración "
                "del estudio; no reutiliza campos RANS/URANS previos."
            ),
            key="ls1-validation-queue-policy",
        )
        queue_confirm = st.checkbox(
            "Confirmo la ejecución secuencial",
            key="ls1-validation-queue-confirm",
        )
        if st.button(
            "Ejecutar cola RANS + URANS",
            disabled=not queue_alphas or not queue_confirm,
            key="ls1-validation-run-queue-button",
        ):
            phase = _read_json(phase_path) or validation_phase_plan()
            start_job(
                "ls1_validation_solver_queue",
                sweep_runner_command(
                    root, variant=VARIANT, alphas=[float(value) for value in queue_alphas],
                    solver="auto", execution_backend="native", n_cores=int(cores),
                    timeout_min_per_alpha=float(timeout), run=True,
                    steady_initialization=True, steady_timeout_min=float(steady_timeout_min),
                    steady_force_window_samples=int(rans_window),
                    steady_force_mean_tolerance_percent=float(rans_mean_tol),
                    steady_force_fluctuation_tolerance_percent=float(rans_fluct_tol),
                    continue_transient_after_steady_timeout=True,
                    resume_existing=queue_policy == "resume_or_start", resume_additional_time_star=None,
                    continue_after_timeout=True, stop_when_force_stable=True,
                    convergence_minimum_time_star=20.0,
                    convergence_window_time_star=10.0,
                    convergence_mean_tolerance=0.02,
                    convergence_oscillation_tolerance=0.1,
                    stop_if_checkmesh_fails=True, pyfoam_live_monitor=False,
                    steady_pyfoam_live_monitor=False,
                    cleanup_processor_directories=True,
                    postprocess_after_each=True, continue_after_error=True,
                    average_from_fraction=float(phase.get("average_from_fraction", 14.0 / 64.0)),
                    transient_phase_plan=phase_path,
                    restart_existing=queue_policy == "delete_and_restart",
                    solver_config_path=solver_path,
                    automatic_core_selection=automatic_core_selection,
                    renumber_before_decompose=renumber_before_decompose,
                ),
            )

        with st.expander("Paquete portátil de esta campaña", expanded=False):
            st.caption(
                "Congela los casos elegidos, el plan RANS→URANS, la selección de núcleos y "
                "los límites temporales. El servidor solo necesita Linux/WSL, Python 3, MPI y OpenFOAM 14."
            )
            portable_alphas = st.multiselect(
                "Ángulos a incluir",
                prepared_alphas,
                default=list(queue_alphas),
                key="ls1-validation-portable-alphas",
            )
            if st.button(
                "Generar paquete de ejecución",
                disabled=not portable_alphas,
                key="ls1-validation-portable-create",
            ):
                command = [
                    sys.executable,
                    str(root / "Application Support/Tools/package_ramair_remote_execution.py"),
                    "--project-root", str(root),
                    "--n-cores", str(int(cores)),
                    "--timeout-min", str(float(timeout)),
                    "--steady-timeout-min", str(float(steady_timeout_min)),
                    "--case-timeout-min", str(float(timeout)),
                    "--transient-phase-plan", str(phase_path),
                    "--package-scope", "validation",
                    "--continue-transient-after-steady-timeout",
                    "--automatic-core-selection" if automatic_core_selection else "--no-automatic-core-selection",
                    "--renumber-before-decompose" if renumber_before_decompose else "--no-renumber-before-decompose",
                ]
                for value in portable_alphas:
                    command += ["--case", str(case_directory(root, VARIANT, float(value)))]
                start_job("ls1_validation_remote_package", command)
            uploaded_return = st.file_uploader(
                "Cargar resultados devueltos",
                type=["zip"],
                key="ls1-validation-remote-return",
                help="Acepta únicamente archivos RamAir_Remote_Return_*.zip con manifiesto y hashes válidos.",
            )
            if st.button(
                "Verificar e importar resultados",
                disabled=uploaded_return is None,
                key="ls1-validation-remote-import",
            ):
                upload_root = root / "CFD_2D/app_state/remote_uploads"
                upload_root.mkdir(parents=True, exist_ok=True)
                upload_name = Path(uploaded_return.name).name
                upload_path = upload_root / f"{int(time.time())}_{upload_name}"
                upload_path.write_bytes(uploaded_return.getvalue())
                start_job(
                    "ls1_validation_remote_import",
                    [
                        sys.executable,
                        str(root / "Application Support/Tools/import_ramair_remote_results.py"),
                        "--project-root", str(root),
                        "--archive", str(upload_path),
                        "--existing-action", "archive",
                    ],
                )

    with post_tab:
        alpha = st.selectbox("Ángulo a postprocesar", prepared_alphas, key="ls1-validation-post-alpha")
        post_settings_path = config_root / "validation_postprocess_config.json"
        post_settings = _read_json(post_settings_path)
        with st.expander("Ajustes de postproceso", expanded=False):
            selected_case = case_directory(root, VARIANT, alpha)
            sampling_window = _read_json(selected_case / "validation_sampling_window.json")
            automatic_fraction = float(
                sampling_window.get(
                    "average_from_fraction",
                    (_read_json(phase_path) or validation_phase_plan()).get("average_from_fraction", 14.0 / 64.0),
                )
            )
            post_cols = st.columns(2)
            rans_tail_samples = post_cols[0].number_input(
                "RANS: muestras finales para la media", min_value=50, max_value=5000,
                value=int(post_settings.get("rans_tail_samples", 500)), step=50,
                help="La historia RANS se representa completa en iteraciones; solo esta cola entra en la media.",
            )
            production_tail_fraction = post_cols[1].number_input(
                "URANS: fracción final de producción usada", min_value=0.10,
                max_value=1.0,
                value=float(post_settings.get(
                    "urans_production_average_fraction", 0.50,
                )), format="%.2f",
                help=(
                    "Porcentaje final de la fase E que entra en la media. 0.50 usa su mitad "
                    "final; las fases A-D nunca se incluyen."
                ),
            )
            effective_urans_fraction = 1.0 - float(production_tail_fraction) * (
                1.0 - automatic_fraction
            )
            if sampling_window:
                st.caption(
                    "Ventana efectiva registrada por la ejecución: fase "
                    f"{sampling_window.get('production_stage', 'E')}. Con la selección actual "
                    f"se promedia desde la fracción global {effective_urans_fraction:.6f}."
                )
            interval_enabled = st.toggle(
                "Limitar visualizaciones URANS a un intervalo físico",
                value=bool(post_settings.get("paraview_time_range_enabled", False)),
                help="Selecciona los instantes de animación sin alterar la ventana de promedio ni los datos calculados.",
                key="ls1-validation-paraview-range-enabled",
            )
            interval_cols = st.columns(2)
            interval_start_s = interval_cols[0].number_input(
                "Inicio [s]", min_value=0.0,
                value=float(post_settings.get("paraview_time_range_start_s", 0.0)),
                format="%.8g", disabled=not interval_enabled,
                key="ls1-validation-paraview-range-start",
            )
            interval_end_s = interval_cols[1].number_input(
                "Final [s]", min_value=0.0,
                value=float(post_settings.get("paraview_time_range_end_s", 1.0)),
                format="%.8g", disabled=not interval_enabled,
                key="ls1-validation-paraview-range-end",
            )
            if st.button("Guardar ajustes de postproceso", key="ls1-validation-save-post-settings"):
                if interval_enabled and float(interval_start_s) > float(interval_end_s):
                    st.error("El inicio del intervalo URANS no puede ser posterior al final.")
                    st.stop()
                _write_json_atomic(post_settings_path, {
                    "rans_tail_samples": int(rans_tail_samples),
                    "urans_production_average_fraction": float(production_tail_fraction),
                    "urans_average_from_fraction": float(effective_urans_fraction),
                    "show_full_rans_history": True,
                    "show_full_urans_history": True,
                    "paraview_time_range_enabled": bool(interval_enabled),
                    "paraview_time_range_start_s": float(interval_start_s),
                    "paraview_time_range_end_s": float(interval_end_s),
                })
                st.success("Ajustes de postproceso guardados.")
        allow_incomplete_publication = st.toggle(
            "Permitir publicación provisional si la ejecución no ha finalizado",
            value=False,
            key=f"ls1-validation-allow-incomplete-{alpha}",
            help=(
                "Mantiene las comprobaciones de Re y Mach, pero permite publicar el promedio "
                "disponible. El punto queda marcado como provisional y puede sustituirse tras "
                "continuar la simulación usando Quitar y Añadir de nuevo."
            ),
        )
        if allow_incomplete_publication:
            st.warning(
                "Se publicará el promedio disponible aunque falten fases. El CSV y las gráficas "
                "registrarán que el punto es provisional."
            )
        post_actions = st.columns(2)
        if post_actions[0].button(
            "Postproceso rápido: escalares y estado final",
            key=f"ls1-validation-fast-post-{alpha}",
            help=(
                "Actualiza coeficientes, diagnósticos de pared y las imágenes finales de "
                "ParaView. No recorre los instantes para crear animaciones."
            ),
        ):
            start_job(
                "ls1_validation_postprocess",
                postprocess_command(
                    root, variant=VARIANT, alpha=alpha,
                    average_from_fraction=effective_urans_fraction,
                    run_openfoam_postprocess=True, export_mode="openfoam_reader", timeout_s=1800,
                    open_results_folder=False, open_paraview=False,
                    wall_profile_analysis=True, automatic_paraview_products=True,
                    include_paraview_animations=False,
                    rans_average_tail_samples=int(rans_tail_samples),
                    paraview_time_range_s=(
                        (float(interval_start_s), float(interval_end_s))
                        if interval_enabled else None
                    ),
                ),
            )
        if post_actions[1].button(
            "Generar animaciones",
            key=f"ls1-validation-animation-post-{alpha}",
            help=(
                "Usa los instantes ya almacenados y genera solo las secuencias de U, Cp, "
                "contornos de presión y vorticidad. Es el paso de mayor coste."
            ),
        ):
            start_job(
                "ls1_validation_animations",
                postprocess_command(
                    root, variant=VARIANT, alpha=alpha,
                    average_from_fraction=effective_urans_fraction,
                    run_openfoam_postprocess=False, export_mode="none", timeout_s=1800,
                    open_results_folder=False, open_paraview=False,
                    wall_profile_analysis=False, automatic_paraview_products=True,
                    include_paraview_animations=True,
                    paraview_animations_only=True,
                    rans_average_tail_samples=int(rans_tail_samples),
                    paraview_time_range_s=(
                        (float(interval_start_s), float(interval_end_s))
                        if interval_enabled else None
                    ),
                ),
            )
        actions = st.columns(4)
        if actions[0].button("Añadir a polar"):
            start_job(
                "ls1_validation_publish",
                validation_publish_command(
                    root, variant=VARIANT, alphas=[alpha],
                    allow_incomplete=allow_incomplete_publication,
                ),
            )
        if actions[1].button("Quitar de polar"):
            start_job("ls1_validation_unpublish", validation_publish_command(root, variant=VARIANT, alphas=[alpha], action="remove"))
        if actions[2].button("Abrir resultados"):
            open_local_folder(result_root / f"alpha_{alpha:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p"))
        if actions[3].button("Abrir caso en ParaView", key="ls1-validation-paraview"):
            try:
                open_paraview_case(root, case_directory(root, VARIANT, alpha))
            except Exception as exc:
                st.error(str(exc))
        result_case = result_root / case_directory(root, VARIANT, alpha).name
        measured_yplus = _maximum_reported_yplus(result_case)
        if measured_yplus is None:
            st.info("Todavía no hay un valor y+ medido para este caso.")
        elif measured_yplus > 1.0:
            st.warning(
                f"Resolución de pared insuficiente para SA integrado: y+ máximo = {measured_yplus:.3g} (> 1). "
                "Revise la primera altura y las zonas de aceleración antes de aceptar el punto."
            )
        else:
            st.success(f"Resolución de pared compatible con SA integrado: y+ máximo = {measured_yplus:.3g}.")
        _render_validation_postprocess_results(
            root,
            result_case,
            case_directory(root, VARIANT, alpha),
            alpha,
        )
        st.caption(f"Registro científico independiente: {study}")
