"""Independent open-airfoil polar campaign compared with published closed points."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

from ls1_validation_page import _alpha_from_dir, _validation_execution_monitor
from ramair_2d_ls1_validation_study import validation_phase_plan
from workflow_backend import (
    case_directory,
    case_writer_command,
    open_local_folder,
    open_paraview_case,
    postprocess_command,
    staged_runner_command,
    sweep_runner_command,
)


StartJob = Callable[..., Any]
VARIANT = "open_ramair_validation_1m"
STUDY_NAME = "open_closed_polar_M0p15_Re1p9e6"
CLOSED_STUDY = "ls1_0417_closed_polar_M0p15_Re1p9e6"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _points(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _comparison_command(root: Path, action: str, alpha: float | None = None, allow_incomplete: bool = False) -> list[str]:
    command = [
        sys.executable,
        str(root / "CFD_2D/scripts/ramair_2d_open_closed_comparison.py"),
        "--project-root", str(root), "--action", action,
    ]
    if alpha is not None:
        command += ["--alpha", str(float(alpha))]
    if allow_incomplete:
        command.append("--allow-incomplete")
    return command


def render_open_closed_validation(root: Path, start_job: StartJob) -> None:
    study = root / "CFD_2D/validation_studies" / STUDY_NAME
    output = study / "postprocess/comparison"
    closed_output = root / "CFD_2D/validation_studies" / CLOSED_STUDY / "postprocess/validation"
    case_root = root / "CFD_2D/openfoam_cases" / VARIANT
    result_root = root / "CFD_2D/results" / VARIANT
    closed_config = root / "CFD_2D/validation_studies" / CLOSED_STUDY / "configurations"
    solver_path = closed_config / "cfd2d_solver_config.json"
    phase_path = closed_config / "validation_phase_plan.json"
    pilot = _read_json(
        root / "CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8/reports/open_validation_pilot.json"
    )
    study.mkdir(parents=True, exist_ok=True)

    st.info(
        "Campaña independiente para cuantificar el efecto de abrir el perfil. La referencia está "
        "formada únicamente por los puntos aceptados en Validación; las ejecuciones abiertas usan "
        "el mismo contrato RANS + A-B-C-D-E, con la malla open medium verificada."
    )
    cols = st.columns(4)
    cols[0].metric("Malla abierta", str(pilot.get("selected_mesh_level") or "medium"))
    cols[1].metric("Celdas", f"{int(pilot.get('cell_count') or 0):,}")
    cols[2].metric("Base piloto RANS", f"{int(pilot.get('rans_iterations') or 0):,} iter")
    cols[3].metric("Control temporal", "dt adaptativo / Co <= 50")

    refresh = st.slider(
        "Refresco del monitor [s]", 2, 180, 30, 2,
        key="open-closed-monitor-refresh",
        help="Actualiza solo este monitor; no vuelve a ejecutar la página completa.",
    )
    with st.expander("Monitor de ejecución abierta", expanded=True):
        _validation_execution_monitor(
            root, refresh, variant=VARIANT, topology="open", study_name=CLOSED_STUDY,
            key_scope="open-closed", job_prefixes=("open_closed_",),
        )

    polar_tab, case_tab, execution_tab, post_tab = st.tabs(
        ["Comparación y evidencia", "Caso OpenFOAM", "Ejecución", "Postproceso"]
    )
    with polar_tab:
        actions = st.columns([1, 3])
        if actions[0].button("Actualizar productos", key="open-closed-refresh"):
            start_job("open_closed_refresh", _comparison_command(root, "refresh"))
        actions[1].caption(
            "Las tablas y curvas se recalculan exclusivamente con puntos publicados manualmente."
        )
        figures = [
            ("open_closed_Cl_alpha.png", "CL frente a ángulo"),
            ("open_closed_CD_CL.png", "Polar CL-CD"),
            ("open_closed_L_D_alpha.png", "Eficiencia frente a ángulo"),
            ("open_closed_Cm_alpha.png", "Momento frente a ángulo"),
            ("open_closed_aerodynamic_features.png", "Características de ambas polares"),
            ("open_closed_relative_at_open_LDmax.png", "Variación en el máximo CL/CD abierto"),
            ("rans_urans/rans_urans_Cl_alpha.png", "CL: RANS frente a RANS+URANS"),
            ("rans_urans/rans_urans_Cd_alpha.png", "CD: RANS frente a RANS+URANS"),
            ("rans_urans/rans_urans_Cm_alpha.png", "Cm: RANS frente a RANS+URANS"),
            ("rans_urans/rans_urans_L_D_alpha.png", "CL/CD: RANS frente a RANS+URANS"),
            ("rans_urans/rans_urans_CD_CL.png", "Polar de arrastre: RANS frente a RANS+URANS"),
            ("rans_urans/rans_urans_err_err2.png", "Normas err y err2 del efecto transitorio"),
        ]
        for coefficient in ("Cl", "Cd", "Cm", "L_D"):
            figures.extend([
                (f"rans_urans/rans_urans_absolute_{coefficient}.png", f"Cambio absoluto RANS+URANS: {coefficient}"),
                (f"rans_urans/rans_urans_relative_{coefficient}.png", f"Cambio relativo RANS+URANS: {coefficient}"),
            ])
        image_cols = st.columns(2)
        for index, (name, caption) in enumerate(figures):
            path = output / name
            if path.is_file():
                image_cols[index % 2].image(str(path), caption=caption)
        open_points = _points(output / "open_validation_points.csv")
        closed_points = _points(closed_output / "ramair_validation_points.csv")
        st.markdown("**Puntos publicados**")
        point_cols = st.columns(2)
        point_cols[0].caption("Referencia cerrada")
        point_cols[0].dataframe(closed_points, width="stretch", hide_index=True)
        point_cols[1].caption("Perfil abierto")
        point_cols[1].dataframe(open_points, width="stretch", hide_index=True)
        st.json(_read_json(output / "open_closed_summary.json"))
        audit = _read_json(output / "nondimensionalization_audit.json")
        if audit.get("comparable"):
            st.success(
                "Adimensionalización verificada: misma cuerda, Re, M, rho, mu, U, Aref, lRef, "
                "centro de momentos y direcciones aerodinámicas. Las paredes interna y externa "
                "del perfil abierto se integran conjuntamente."
            )
        elif audit:
            st.error("Las referencias aerodinámicas no coinciden; se bloquea la publicación abierta.")
        with st.expander("Auditoría científica de comparabilidad"):
            st.json(audit)

    existing_alphas = sorted(
        value for value in (_alpha_from_dir(path.name) for path in case_root.glob("alpha_*"))
        if value is not None
    )
    with case_tab:
        st.markdown("**Caso abierto con el contrato de validación**")
        st.caption(
            "El solver y el plan A-B-C-D-E se comparten por referencia con la validación cerrada; "
            "la geometría, malla, casos y resultados permanecen separados."
        )
        alpha_case = st.number_input(
            "Ángulo del caso [°]", min_value=-89.0, max_value=89.0,
            value=float(existing_alphas[0] if existing_alphas else 8.0), step=1.0,
            key="open-closed-case-alpha",
        )
        st.code(str(case_directory(root, VARIANT, alpha_case)), language="text")
        confirm_case = st.checkbox(
            "Confirmo la generación o actualización del caso",
            key="open-closed-case-confirm",
        )
        if st.button("Generar caso abierto", disabled=not confirm_case, key="open-closed-case-write"):
            start_job(
                "open_closed_case_writer",
                case_writer_command(
                    root, variant=VARIANT, alpha=float(alpha_case),
                    require_converted_polymesh=True, overwrite=True,
                    existing_case_action="archive", reynolds=1.9e6,
                    solver_config_path=solver_path,
                ),
            )
        with st.expander("Ajustes compartidos de solver y fases"):
            st.json({"solver": _read_json(solver_path), "phase_plan": _read_json(phase_path)})

    prepared = sorted(set(existing_alphas + [float(alpha_case)]))
    with execution_tab:
        alpha = st.selectbox("Ángulo a ejecutar", prepared, key="open-closed-run-alpha")
        controls = st.columns(4)
        cores = int(controls[0].number_input("Procesos", 1, 16, 8, key="open-closed-cores"))
        timeout_h = float(controls[1].number_input("Timeout por caso [h]", 1.0, 72.0, 6.0, key="open-closed-timeout"))
        steady_timeout = float(controls[2].number_input("Límite RANS [min]", 30.0, 720.0, 180.0, key="open-closed-rans-timeout"))
        resume = controls[3].checkbox("Continuar", value=True, key="open-closed-resume")
        confirm_run = st.checkbox("Confirmo la ejecución abierta", key="open-closed-run-confirm")
        if st.button("Ejecutar RANS + URANS", type="primary", disabled=not confirm_run, key="open-closed-run"):
            start_job(
                "open_closed_solver",
                staged_runner_command(
                    root, variant=VARIANT, alpha=float(alpha), solver="auto",
                    execution_backend="native", n_cores=cores, timeout_min=60.0 * timeout_h,
                    run=True, stop_if_checkmesh_fails=True, pyfoam_live_monitor=False,
                    cleanup_processor_directories=True, stop_when_force_stable=True,
                    convergence_minimum_time_star=20.0, convergence_window_time_star=10.0,
                    convergence_mean_tolerance=0.02, convergence_oscillation_tolerance=0.1,
                    steady_initialization=True, steady_timeout_min=steady_timeout,
                    steady_force_window_samples=500, steady_force_mean_tolerance_percent=0.3,
                    steady_force_fluctuation_tolerance_percent=0.5,
                    continue_transient_after_steady_timeout=True, resume=resume,
                    resume_additional_time_star=None, steady_pyfoam_live_monitor=False,
                    transient_phase_plan=phase_path, automatic_core_selection=True,
                    renumber_before_decompose=True,
                ),
            )
        st.markdown("#### Cola secuencial")
        queue = st.multiselect("Ángulos y orden", prepared, key="open-closed-queue")
        queue_confirm = st.checkbox("Confirmo la cola abierta", key="open-closed-queue-confirm")
        if st.button("Ejecutar cola RANS + URANS", disabled=not queue or not queue_confirm, key="open-closed-queue-run"):
            phase = _read_json(phase_path) or validation_phase_plan()
            start_job(
                "open_closed_solver_queue",
                sweep_runner_command(
                    root, variant=VARIANT, alphas=[float(value) for value in queue],
                    solver="auto", execution_backend="native", n_cores=cores,
                    timeout_min_per_alpha=60.0 * timeout_h, run=True,
                    steady_initialization=True, steady_timeout_min=steady_timeout,
                    steady_force_window_samples=500, steady_force_mean_tolerance_percent=0.3,
                    steady_force_fluctuation_tolerance_percent=0.5,
                    continue_transient_after_steady_timeout=True, resume_existing=True,
                    resume_additional_time_star=None, continue_after_timeout=True,
                    stop_when_force_stable=True, convergence_minimum_time_star=20.0,
                    convergence_window_time_star=10.0, convergence_mean_tolerance=0.02,
                    convergence_oscillation_tolerance=0.1, stop_if_checkmesh_fails=True,
                    pyfoam_live_monitor=False, steady_pyfoam_live_monitor=False,
                    cleanup_processor_directories=True, postprocess_after_each=True,
                    continue_after_error=True,
                    average_from_fraction=float(phase.get("average_from_fraction", 14.0 / 64.0)),
                    transient_phase_plan=phase_path, solver_config_path=solver_path,
                    automatic_core_selection=True, renumber_before_decompose=True,
                ),
            )
        st.caption("Los controles del monitor permiten guardar y saltar el caso actual o pausar toda la cola.")

    with post_tab:
        alpha_post = st.selectbox("Ángulo a postprocesar", prepared, key="open-closed-post-alpha")
        post_actions = st.columns(2)
        if post_actions[0].button("Postproceso rápido", key="open-closed-fast-post"):
            start_job(
                "open_closed_postprocess",
                postprocess_command(
                    root, variant=VARIANT, alpha=float(alpha_post), average_from_fraction=14.0 / 64.0,
                    run_openfoam_postprocess=True, export_mode="openfoam_reader", timeout_s=1800,
                    open_results_folder=False, open_paraview=False, wall_profile_analysis=True,
                    automatic_paraview_products=True, include_paraview_animations=False,
                    rans_average_tail_samples=500,
                ),
            )
        if post_actions[1].button("Generar animaciones", key="open-closed-animations"):
            start_job(
                "open_closed_animations",
                postprocess_command(
                    root, variant=VARIANT, alpha=float(alpha_post), average_from_fraction=14.0 / 64.0,
                    run_openfoam_postprocess=False, export_mode="none", timeout_s=1800,
                    open_results_folder=False, open_paraview=False, wall_profile_analysis=False,
                    automatic_paraview_products=True, include_paraview_animations=True,
                    paraview_animations_only=True, rans_average_tail_samples=500,
                ),
            )
        provisional = st.toggle(
            "Permitir publicación provisional", value=False, key="open-closed-provisional",
            help="Marca el punto como provisional si todavía no se ha completado la fase E.",
        )
        actions = st.columns(4)
        if actions[0].button("Añadir a comparación", key="open-closed-publish"):
            start_job("open_closed_publish", _comparison_command(root, "publish", alpha_post, provisional))
        if actions[1].button("Quitar de comparación", key="open-closed-remove"):
            start_job("open_closed_remove", _comparison_command(root, "remove", alpha_post))
        result = result_root / case_directory(root, VARIANT, alpha_post).name
        if actions[2].button("Abrir resultados", key="open-closed-open-results"):
            open_local_folder(result)
        if actions[3].button("Abrir caso en ParaView", key="open-closed-open-paraview"):
            try:
                open_paraview_case(root, case_directory(root, VARIANT, alpha_post))
            except Exception as exc:
                st.error(str(exc))
        if result.is_dir():
            for image in sorted(result.glob("*.png")):
                st.image(str(image), caption=image.stem)
        st.caption(f"Registro independiente: {study}")
