"""Independent open-airfoil polar campaign compared with published closed points."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

from ls1_validation_page import (
    _alpha_from_dir,
    _effective_validation_status,
    _latest_physical_time,
    _maximum_reported_yplus,
    _render_validation_postprocess_results,
    _validation_execution_monitor,
)
from ramair_2d_ls1_validation_study import validation_phase_plan, validation_solver_profile
from ramair_2d_mesh_numerics import quality_controls_for_mesh
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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _ensure_independent_configuration(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "cfd2d_solver_config.json",
        "validation_phase_plan.json",
        "validation_postprocess_config.json",
    ):
        target = destination / name
        original = source / name
        if not target.exists() and original.is_file():
            shutil.copy2(original, target)
    safety_marker = destination / "open_validation_safety_v1.json"
    if safety_marker.exists():
        return
    solver_path = destination / "cfd2d_solver_config.json"
    phase_path = destination / "validation_phase_plan.json"
    solver = _read_json(solver_path)
    phase = _read_json(phase_path)
    if solver:
        solver["maxCo"] = min(5.0, float(solver.get("maxCo", 5.0)))
        solver["open_validation_safety"] = {
            "startup_and_adaptive_maxCo": 5.0,
            "basis": "open-medium alpha=8 pilot; Co>10 destabilized the inlet-lip cells",
        }
        _write_json_atomic(solver_path, solver)
    if phase:
        phase["maxCo"] = min(5.0, float(phase.get("maxCo", 5.0)))
        _write_json_atomic(phase_path, phase)
    _write_json_atomic(safety_marker, {
        "schema_version": 1,
        "maxCo": 5.0,
        "independent_from_closed_validation": True,
        "user_editable_after_migration": True,
    })


def _render_open_solver_editor(
    root: Path,
    solver_path: Path,
    phase_path: Path,
) -> dict[str, Any]:
    solver = validation_solver_profile(_read_json(solver_path))
    phase = _read_json(phase_path) or validation_phase_plan()
    gate = dict(solver.get("validation_rans_convergence") or {})
    writes = dict(solver.get("validation_write_strategy") or {})
    st.caption(
        "Esta configuración se creó desde el contrato cerrado, pero se guarda de forma "
        "independiente para que los casos abiertos puedan ajustarse sin alterar Validación."
    )
    mesh_numerics = quality_controls_for_mesh(root / "CFD_2D/meshes" / VARIANT)
    quality_mode = st.selectbox(
        "Control de no ortogonalidad",
        ["automatic", "manual"],
        index=0 if solver.get("mesh_quality_numerics_mode", "automatic") == "automatic" else 1,
        format_func=lambda value: "Automático desde checkMesh" if value == "automatic" else "Manual",
        key="open-closed-quality-mode",
        help="Aplica la misma política automática de correctores y laplaciano que la validación cerrada.",
    )
    manual_non_orthogonal = int(
        solver.get("n_non_orthogonal_correctors")
        if solver.get("n_non_orthogonal_correctors") is not None
        else (mesh_numerics or {}).get("n_non_orthogonal_correctors", 0)
    )
    if quality_mode == "manual":
        manual_non_orthogonal = int(st.number_input(
            "Correctores no ortogonales manuales", 0, 4, manual_non_orthogonal,
            key="open-closed-manual-non-orthogonal",
        ))
    transient = st.columns(5)
    dt_star = float(transient[0].number_input(
        "dt* objetivo", 0.0001, 0.01, float(phase.get("target_deltaT_star", 0.0025)),
        format="%.7f", key="open-closed-dt-star",
    ))
    max_co = float(transient[1].number_input(
        "Co máximo", 1.0, 50.0, min(50.0, float(phase.get("maxCo", 50.0))),
        key="open-closed-max-co",
    ))
    outer = int(transient[2].number_input(
        "Outer correctors máximos", 1, 5, min(5, int(phase.get("max_outer_correctors", 5))),
        key="open-closed-outer",
    ))
    correctors = int(transient[3].number_input(
        "Correctores de presión", 1, 6, int(solver.get("n_correctors", 2)),
        key="open-closed-correctors",
    ))
    residual = float(transient[4].number_input(
        "Residual PIMPLE", 1.0e-7, 1.0e-2,
        float(((solver.get("outer_corrector_residual_control") or {}).get("fields") or {}).get("U", {}).get("tolerance", 1.0e-4)),
        format="%.1e", key="open-closed-pimple-residual",
    ))
    st.markdown("#### Gate RANS")
    gate_cols = st.columns(4)
    window = int(gate_cols[0].number_input(
        "Ventana [muestras]", 100, 5000, int(gate.get("window_samples", 1000)), 100,
        key="open-closed-rans-window",
    ))
    rans_residual = float(gate_cols[1].number_input(
        "Residual diagnóstico", 1.0e-9, 1.0e-2,
        float(gate.get("residual_tolerance", 1.0e-6)), format="%.1e",
        key="open-closed-rans-residual",
    ))
    mean_tol = float(gate_cols[2].number_input(
        "Cambio de media [%]", 0.01, 10.0,
        float(gate.get("mean_change_tolerance_percent", 0.5)), format="%.2f",
        key="open-closed-rans-mean",
    ))
    fluct_tol = float(gate_cols[3].number_input(
        "Fluctuación [%]", 0.01, 20.0,
        float(gate.get("fluctuation_tolerance_percent", 1.0)), format="%.2f",
        key="open-closed-rans-fluctuation",
    ))
    st.markdown("#### Escritura de campos")
    write_cols = st.columns(3)
    rans_write = int(write_cols[0].number_input(
        "RANS: campo cada N iteraciones", 100, 10000,
        int(writes.get("rans_full_field_interval_iterations", 1000)), 100,
        key="open-closed-rans-write",
    ))
    urans_write = float(write_cols[1].number_input(
        "URANS: intervalo [t*]", 0.005, 2.0,
        float(writes.get("urans_interval_time_star", 0.1)), format="%.4f",
        key="open-closed-urans-write",
    ))
    purge = int(write_cols[2].number_input(
        "Estados volumétricos conservados", 0, 500,
        int(writes.get("purge_write", 24)), key="open-closed-purge",
    ))
    d_gate = dict(phase.get("phase_d_steady_equivalence") or {})
    with st.expander("Aceptación estacionaria tras D", expanded=False):
        d_enabled = st.toggle(
            "Permitir aceptación tras D", bool(d_gate.get("enabled", False)),
            key="open-closed-d-gate",
        )
        d_cols = st.columns(4)
        d_window = float(d_cols[0].number_input("Ventana D [t*]", 0.5, 10.0, float(d_gate.get("window_time_star", 2.5)), key="open-closed-d-window"))
        d_samples = int(d_cols[1].number_input("Muestras D", 50, 5000, int(d_gate.get("minimum_samples", 200)), 50, key="open-closed-d-samples"))
        d_mean = float(d_cols[2].number_input("Diferencia RANS-D [%]", 0.01, 5.0, float(d_gate.get("mean_difference_tolerance_percent", 0.3)), key="open-closed-d-mean"))
        d_fluct = float(d_cols[3].number_input("Fluctuación D [%]", 0.01, 5.0, float(d_gate.get("fluctuation_tolerance_percent", 0.5)), key="open-closed-d-fluct"))
    st.markdown("#### Secuencia A-E")
    phases = st.data_editor(
        pd.DataFrame(phase.get("stages") or validation_phase_plan()["stages"]),
        hide_index=True, width="stretch", disabled=["stage", "sampling"],
        key="open-closed-phase-editor",
    )
    if st.button("Guardar configuración abierta", key="open-closed-save-solver"):
        records = phases.to_dict(orient="records")
        total = sum(float(item.get("duration_time_star", 0.0)) for item in records)
        production_start = sum(
            float(item.get("duration_time_star", 0.0))
            for item in records if not bool(item.get("sampling", False))
        )
        if total <= 0.0 or production_start >= total:
            st.error("La tabla A-E necesita una fase de producción positiva.")
        else:
            solver.update({
                "deltaT_star": dt_star, "maxDeltaT_star": dt_star, "maxCo": max_co,
                "n_outer_correctors": outer, "n_correctors": correctors,
                "mesh_quality_numerics_mode": quality_mode,
                "endTime_star": total, "average_from_fraction": production_start / total,
                "steady_write_interval_iterations": rans_write,
                "field_write_control": "adjustableRunTime",
                "field_write_interval_star": urans_write, "purgeWrite": purge,
                "outer_corrector_residual_control": {"enabled": True, "fields": {
                    name: {"tolerance": residual, "relTol": 0.0}
                    for name in ("p", "U", "nuTilda")
                }},
                "validation_rans_convergence": {
                    "window_samples": window,
                    "minimum_samples": min(window, max(100, window // 2)),
                    "residual_tolerance": rans_residual,
                    "mean_change_tolerance_percent": mean_tol,
                    "fluctuation_tolerance_percent": fluct_tol,
                    "required_consecutive_windows": 3,
                },
                "validation_write_strategy": {
                    "rans_full_field_interval_iterations": rans_write,
                    "urans_control": "adjustableRunTime",
                    "urans_interval_time_star": urans_write,
                    "purge_write": purge, "authoritative": True,
                },
                "validation_phase_d_steady_equivalence": {
                    "enabled": d_enabled, "window_time_star": d_window,
                    "minimum_samples": d_samples,
                    "mean_difference_tolerance_percent": d_mean,
                    "fluctuation_tolerance_percent": d_fluct,
                },
            })
            solver["steady_numerics"] = {
                **dict(solver.get("steady_numerics") or {}),
                "p_relaxation": 0.3, "U_relaxation": 0.7,
                "nuTilda_relaxation": 0.7,
            }
            if quality_mode == "manual":
                solver["n_non_orthogonal_correctors"] = manual_non_orthogonal
                solver["steady_numerics"]["n_non_orthogonal_correctors"] = manual_non_orthogonal
            else:
                solver.pop("n_non_orthogonal_correctors", None)
                solver["steady_numerics"].pop("n_non_orthogonal_correctors", None)
            phase.update({
                "target_deltaT_star": dt_star, "maxCo": max_co,
                "max_outer_correctors": outer,
                "production_start_time_star": production_start,
                "total_time_star": total,
                "average_from_fraction": production_start / total,
                "stages": records,
                "write_strategy": {"control": "adjustableRunTime", "interval_time_star": urans_write, "purge_write": purge},
                "phase_d_steady_equivalence": solver["validation_phase_d_steady_equivalence"],
            })
            _write_json_atomic(solver_path, validation_solver_profile(solver))
            _write_json_atomic(phase_path, phase)
            st.success("Ajustes abiertos guardados sin modificar la validación cerrada.")
    with st.expander("Configuración avanzada completa"):
        st.json({"solver": solver, "phase_plan": phase})
    return {
        "solver": solver, "phase": phase, "rans_window": window,
        "rans_mean_tol": mean_tol, "rans_fluct_tol": fluct_tol,
    }


def render_open_closed_validation(root: Path, start_job: StartJob) -> None:
    study = root / "CFD_2D/validation_studies" / STUDY_NAME
    output = study / "postprocess/comparison"
    closed_output = root / "CFD_2D/validation_studies" / CLOSED_STUDY / "postprocess/validation"
    case_root = root / "CFD_2D/openfoam_cases" / VARIANT
    result_root = root / "CFD_2D/results" / VARIANT
    closed_config = root / "CFD_2D/validation_studies" / CLOSED_STUDY / "configurations"
    config_root = study / "configurations"
    _ensure_independent_configuration(closed_config, config_root)
    solver_path = config_root / "cfd2d_solver_config.json"
    phase_path = config_root / "validation_phase_plan.json"
    post_settings_path = config_root / "validation_postprocess_config.json"
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
    campaign_alphas = [float(value) for value in range(-10, 21, 2)]
    with case_tab:
        st.markdown("**Caso abierto con el contrato de validación**")
        st.caption(
            "Parte de los valores de validación cerrada, pero conserva un solver y un plan "
            "A-B-C-D-E independientes para la geometría abierta."
        )
        solver_settings = _render_open_solver_editor(root, solver_path, phase_path)
        alpha_case = st.selectbox(
            "Ángulo del caso [°]", campaign_alphas,
            index=campaign_alphas.index(8.0),
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

    prepared = campaign_alphas
    with execution_tab:
        alpha = st.selectbox("Ángulo a ejecutar", prepared, key="open-closed-run-alpha")
        controls = st.columns(4)
        cores = int(controls[0].number_input("Procesos", 1, 16, 8, key="open-closed-cores"))
        timeout_h = float(controls[1].number_input("Timeout por caso [h]", 1.0, 72.0, 6.0, key="open-closed-timeout"))
        steady_timeout = float(controls[2].number_input("Límite RANS [min]", 30.0, 720.0, 180.0, key="open-closed-rans-timeout"))
        resume = controls[3].checkbox("Continuar", value=True, key="open-closed-resume")
        parallel = st.columns(2)
        parallel_mode = parallel[0].radio(
            "Selección paralela", ["Auto", "Manual"], horizontal=True,
            key="open-closed-parallel-mode",
            help="Auto usa los benchmarks válidos y la política equilibrada por tamaño de malla.",
        )
        renumber = parallel[1].toggle(
            "Renumber en caso nuevo", value=True, key="open-closed-renumber",
            help="Solo se aplica a casos limpios; no reordena silenciosamente un reinicio.",
        )
        automatic_cores = parallel_mode == "Auto"
        rans_window = int(solver_settings["rans_window"])
        rans_mean_tol = float(solver_settings["rans_mean_tol"])
        rans_fluct_tol = float(solver_settings["rans_fluct_tol"])
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
                    steady_force_window_samples=rans_window,
                    steady_force_mean_tolerance_percent=rans_mean_tol,
                    steady_force_fluctuation_tolerance_percent=rans_fluct_tol,
                    continue_transient_after_steady_timeout=True, resume=resume,
                    resume_additional_time_star=None, steady_pyfoam_live_monitor=False,
                    transient_phase_plan=phase_path,
                    automatic_core_selection=automatic_cores,
                    renumber_before_decompose=renumber,
                ),
            )
        st.markdown("#### Cola secuencial")
        queue = st.multiselect("Ángulos y orden", prepared, key="open-closed-queue")
        queue_policy = st.radio(
            "Política para casos existentes", ["resume_or_start", "delete_and_restart"],
            format_func=lambda value: (
                "Continuar existentes, iniciar nuevos y omitir finalizados"
                if value == "resume_or_start" else "Regenerar y empezar desde cero"
            ),
            key="open-closed-queue-policy",
        )
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
                    steady_force_window_samples=rans_window,
                    steady_force_mean_tolerance_percent=rans_mean_tol,
                    steady_force_fluctuation_tolerance_percent=rans_fluct_tol,
                    continue_transient_after_steady_timeout=True,
                    resume_existing=queue_policy == "resume_or_start",
                    resume_additional_time_star=None, continue_after_timeout=True,
                    stop_when_force_stable=True, convergence_minimum_time_star=20.0,
                    convergence_window_time_star=10.0, convergence_mean_tolerance=0.02,
                    convergence_oscillation_tolerance=0.1, stop_if_checkmesh_fails=True,
                    pyfoam_live_monitor=False, steady_pyfoam_live_monitor=False,
                    cleanup_processor_directories=True, postprocess_after_each=True,
                    continue_after_error=True,
                    average_from_fraction=float(phase.get("average_from_fraction", 14.0 / 64.0)),
                    transient_phase_plan=phase_path, solver_config_path=solver_path,
                    restart_existing=queue_policy == "delete_and_restart",
                    automatic_core_selection=automatic_cores,
                    renumber_before_decompose=renumber,
                ),
            )
        st.caption("Los controles del monitor permiten guardar y saltar el caso actual o pausar toda la cola.")

    with post_tab:
        alpha_post = st.selectbox("Ángulo a postprocesar", prepared, key="open-closed-post-alpha")
        post_settings = _read_json(post_settings_path)
        phase = _read_json(phase_path) or validation_phase_plan()
        with st.expander("Ajustes de postproceso", expanded=False):
            setting_cols = st.columns(2)
            rans_tail = int(setting_cols[0].number_input(
                "RANS: muestras finales para la media", 50, 5000,
                int(post_settings.get("rans_tail_samples", 500)), 50,
                key="open-closed-post-rans-tail",
            ))
            production_tail = float(setting_cols[1].number_input(
                "URANS: fracción final de producción", 0.10, 1.0,
                float(post_settings.get("urans_production_average_fraction", 0.50)),
                format="%.2f", key="open-closed-post-production-tail",
            ))
            production_start = float(phase.get("average_from_fraction", 14.0 / 64.0))
            urans_fraction = 1.0 - production_tail * (1.0 - production_start)
            if st.button("Guardar ajustes de postproceso abierto", key="open-closed-save-post"):
                _write_json_atomic(post_settings_path, {
                    "rans_tail_samples": rans_tail,
                    "urans_production_average_fraction": production_tail,
                    "urans_average_from_fraction": urans_fraction,
                })
                st.success("Ajustes de postproceso abierto guardados.")
        rans_post, urans_post = st.tabs(["RANS / SIMPLE", "URANS / PIMPLE"])
        for container, mode, include_rans in (
            (rans_post, "RANS", True), (urans_post, "URANS", False),
        ):
            with container:
                post_actions = st.columns(2)
                if post_actions[0].button(
                    "Postproceso rápido", key=f"open-closed-fast-post-{mode.lower()}",
                ):
                    start_job(
                        f"open_closed_postprocess_{mode.lower()}",
                        postprocess_command(
                            root, variant=VARIANT, alpha=float(alpha_post),
                            average_from_fraction=(0.0 if mode == "RANS" else urans_fraction),
                            run_openfoam_postprocess=True, export_mode="openfoam_reader", timeout_s=1800,
                            open_results_folder=False, open_paraview=False, wall_profile_analysis=True,
                            automatic_paraview_products=True, include_paraview_animations=False,
                            rans_average_tail_samples=rans_tail, simulation_mode=mode,
                            include_rans_stage=include_rans,
                        ),
                    )
                if post_actions[1].button(
                    "Generar animaciones", key=f"open-closed-animations-{mode.lower()}",
                ):
                    start_job(
                        f"open_closed_animations_{mode.lower()}",
                        postprocess_command(
                            root, variant=VARIANT, alpha=float(alpha_post),
                            average_from_fraction=(0.0 if mode == "RANS" else urans_fraction),
                            run_openfoam_postprocess=False, export_mode="none", timeout_s=1800,
                            open_results_folder=False, open_paraview=False, wall_profile_analysis=False,
                            automatic_paraview_products=True, include_paraview_animations=True,
                            paraview_animations_only=True, rans_average_tail_samples=rans_tail,
                            simulation_mode=mode, include_rans_stage=include_rans,
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
            _render_validation_postprocess_results(
                root, result, case_directory(root, VARIANT, alpha_post), alpha_post,
            )
        st.caption(f"Registro independiente: {study}")
