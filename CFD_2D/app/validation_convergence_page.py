"""Streamlit page for the isolated Validation & Convergence Lab."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

from ramair_2d_mesh_numerics import automatic_non_orthogonal_controls
from ramair_2d_parallel import recommended_core_count

from validation_plotting import (
    close_figures,
    coefficient_figure,
    residual_figure,
)
from workflow_backend import (
    JobManager,
    open_local_folder,
    open_paraview_case,
    open_validation_mesh_viewer,
    request_openfoam_clean_stop,
    request_validation_pimple_stop,
    request_validation_rans_stop,
    save_validation_study_config,
    validation_monitor_snapshot,
    validation_live_execution,
    validation_campaign_command,
    validation_urans_case_snapshot,
    validation_urans_queue_command,
    validation_smoke_command,
    validation_study_command,
    validation_study_snapshot,
)
from ls1_validation_page import render_ls1_validation


StartJob = Callable[..., Any]
MESH_LEVELS = ("coarse", "medium", "fine")
def _records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        return pd.read_csv(path).to_dict(orient="records")
    except (pd.errors.EmptyDataError, ValueError):
        return []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _canonical_urans_rows(active: Path) -> list[dict[str, Any]]:
    """Return only canonical URANS cases whose solver wrote physical time."""
    rows: list[dict[str, Any]] = []
    runs_root = active / "runs"
    if not runs_root.is_dir():
        return rows
    for manifest_path in sorted(runs_root.glob("*/*/*/case_manifest.json")):
        manifest = _read_json(manifest_path)
        if not manifest:
            continue
        run_root = manifest_path.parent
        case = run_root / "case"
        physical_times: list[float] = []
        for child in case.iterdir() if case.is_dir() else ():
            if not child.is_dir():
                continue
            try:
                value = float(child.name)
            except ValueError:
                continue
            if value > 0.0:
                physical_times.append(value)
        if not physical_times:
            continue
        summary = _read_json(run_root / "execution_summary.json")
        journal = _read_json(run_root / "stage_journal.json")
        observed_phases = list(journal.get("phases") or [])
        observed_phase = observed_phases[-1] if observed_phases else {}
        scientific_key = dict(manifest.get("scientific_key") or {})
        rows.append({
            **manifest,
            **scientific_key,
            "case_id": str(manifest.get("case_id") or run_root.name),
            "case_path": str(case),
            "run_root": str(run_root),
            "topology": scientific_key.get("topology") or manifest.get("topology"),
            "mesh_level": scientific_key.get("mesh_level") or manifest.get("mesh_level"),
            "mesh_id": scientific_key.get("mesh_id") or manifest.get("mesh_id"),
            "alpha_deg": scientific_key.get("alpha_deg") or manifest.get("alpha_deg"),
            "deltaT_s": (
                scientific_key.get("deltaT_s")
                or scientific_key.get("dt_s")
                or manifest.get("deltaT_s")
                or manifest.get("dt_s")
            ),
            "latest_time_s": max(physical_times),
            "status": (
                summary.get("status")
                or manifest.get("execution_outcome")
                or "READY"
            ),
            "stage": (
                observed_phase.get("phase")
                or summary.get("stage")
                or manifest.get("current_phase")
            ),
            "phase_deltaT_s": observed_phase.get("deltaT_s"),
            "updated_at": summary.get("updated_at") or manifest.get("updated_at"),
        })
    level_order = {"coarse": 0, "medium": 1, "fine": 2}
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("topology") or ""),
            level_order.get(str(row.get("mesh_level") or ""), 99),
            -float(row.get("deltaT_s") or 0.0),
        ),
    )


def _status_badge(status: str) -> str:
    if status in {
        "ACCEPTED",
        "COMPLETED",
        "CHECKPOINT_READY",
        "MANUAL_REVIEW_CHECKPOINT_READY",
        "RANS_AUTO_CONVERGED_STRICT",
        "RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING",
        "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
        "RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY",
    }:
        return f"OK - {status}"
    if status in {
        "READY",
        "RUNNING",
        "ANALYSIS_PENDING",
        "TIMEOUT_PARTIAL",
        "RANS_BASE_MAX_ITERATIONS",
        "RANS_PARTIAL",
        "DIAGNOSTIC_CHECKPOINT",
        "RANS_REVIEW_REQUIRED",
    }:
        return f"WARNING - {status}"
    if status in {"NOT_CONFIGURED", "RANS_BASE_NOT_CREATED"}:
        return status
    return f"FAIL - {status}"


def _json_panel(path: Path, title: str, *, inline: bool = False) -> None:
    def render() -> None:
        if not path.is_file():
            st.info(f"No disponible todavia: {path.name}")
            return
        try:
            st.json(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, json.JSONDecodeError) as exc:
            st.error(f"No se puede leer {path}: {exc}")

    if inline:
        st.markdown(f"**{title}**")
        render()
    else:
        with st.expander(title, expanded=False):
            render()


def _plot_inventory(
    folder: Path,
    *,
    exclude_names: set[str] | None = None,
) -> None:
    excluded = exclude_names or set()
    images = [
        image
        for image in sorted(folder.rglob("*.png"))
        if image.name not in excluded
    ]
    if not images:
        st.info("Todavia no existen graficas reales para esta seccion.")
        return
    columns = st.columns(2)
    for index, image in enumerate(images):
        columns[index % 2].image(
            str(image),
            caption=image.name,
            width="stretch",
        )


def _postprocess_product_browser(
    manifest_path: Path,
    *,
    start_job: StartJob,
    key_scope: str,
    inline: bool = False,
) -> None:
    """List only visual products and load heavy media on explicit request."""
    project_root = next(
        (parent for parent in manifest_path.parents if (parent / "CFD_2D").is_dir()),
        None,
    )
    manager = JobManager(project_root) if project_root is not None else None
    jobs = (
        [
            manager.poll(job) for job in manager.list_jobs(limit=40)
            if "postprocess" in str(job.stage).lower()
            or "animation" in str(job.stage).lower()
        ]
        if manager is not None else []
    )
    active_on_render = any(job.status == "RUNNING" for job in jobs)

    @st.fragment(run_every=2 if active_on_render else None)
    def render_browser() -> None:
        if active_on_render:
            current = [manager.poll(job) for job in jobs] if manager is not None else []
            if any(job.status == "RUNNING" for job in current):
                st.caption("Postproceso en curso; se incorporan los productos al quedar escritos.")
            else:
                st.rerun()
        manifest = _read_json(manifest_path)
        if not manifest:
            st.info(f"No postprocess manifest is available: {manifest_path}")
            return
        rows = list(manifest.get("products") or [])
        if not rows:
            rows = [
                row
                for grouped in (manifest.get("groups") or {}).values()
                for row in grouped
            ]
        def product_path(row: dict[str, Any]) -> Path:
            value = Path(str(row.get("path") or ""))
            if value.is_absolute() or int(manifest.get("schema_version") or 0) < 3:
                return value
            return (manifest_path.parent / value).resolve()
        visual_rows = [
            row for row in rows
            if product_path(row).suffix.lower()
            in {".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm"}
            and "courant_hotspots" not in product_path(row).name.lower()
            and not (
                key_scope.lower().startswith("urans-")
                and "RANS" in product_path(row).parts
            )
            and not (
                key_scope.lower().startswith("rans-")
                and "URANS" in product_path(row).parts
            )
        ]
        if not visual_rows:
            st.info("Todavía no hay imágenes o animaciones disponibles.")
            return
        st.dataframe(
            [
                {
                    "product": row.get("name"),
                    "type": (
                        "animation"
                        if product_path(row).suffix.lower() in {".gif", ".mp4", ".webm"}
                        else "image"
                    ),
                    "status": row.get("generation_status", "AVAILABLE"),
                    "path": row.get("path"),
                    "bytes": row.get("bytes"),
                }
                for row in visual_rows
            ],
            hide_index=True,
            width="stretch",
        )
        controls = st.columns(2)
        show_images = controls[0].toggle(
            "Cargar y mostrar todas las imágenes",
            value=False,
            key=f"postprocess-browser-images-{key_scope}",
        )
        play_animations = controls[1].toggle(
            "Cargar y mostrar las animaciones",
            value=False,
            key=f"postprocess-browser-animations-{key_scope}",
            help="Al desactivarlo se retiran los reproductores y se detiene la reproducción.",
        )
        image_rows = [
            row
            for row in visual_rows
            if product_path(row).suffix.lower()
            in {".png", ".jpg", ".jpeg"}
            and "velocity_frames" not in product_path(row).parts
            and "pressure_frames" not in product_path(row).parts
            and "courant_hotspots" not in product_path(row).name.lower()
        ]
        if show_images and image_rows:
            columns = st.columns(2)
            for index, row in enumerate(image_rows):
                path = product_path(row)
                if path.is_file():
                    columns[index % 2].image(
                        str(path),
                        caption=str(row.get("name") or path.name),
                        width="stretch",
                    )
        animation_rows = [
            row for row in visual_rows
            if product_path(row).suffix.lower() in {".mp4", ".webm", ".gif"}
        ]
        if play_animations:
            for row in animation_rows:
                path = product_path(row)
                if path.is_file():
                    if path.suffix.lower() == ".gif":
                        st.image(str(path), caption=path.name, width="stretch")
                    else:
                        st.video(str(path), autoplay=True, loop=True)
    if inline:
        render_browser()
    else:
        with st.expander("Postprocess products", expanded=False):
            render_browser()


def _selected_checkpoint(
    checkpoint_rows: list[dict[str, Any]],
    mesh_id: str,
) -> dict[str, Any]:
    return next(
        (row for row in checkpoint_rows if row.get("mesh_id") == mesh_id),
        {"mesh_id": mesh_id, "status": "RANS_BASE_NOT_CREATED"},
    )


def _checkpoint_for_angle(
    checkpoint_rows: list[dict[str, Any]],
    mesh_id: str,
    alpha_deg: float,
) -> dict[str, Any]:
    return next(
        (
            row for row in checkpoint_rows
            if str(row.get("base_mesh_id") or str(row.get("mesh_id", "")).split("__alpha_", 1)[0]) == mesh_id
            and abs(float(row.get("alpha_deg") or 0.0) - float(alpha_deg)) < 1.0e-9
        ),
        {
            "mesh_id": mesh_id,
            "base_mesh_id": mesh_id,
            "alpha_deg": float(alpha_deg),
            "status": "RANS_BASE_NOT_CREATED",
        },
    )


def _rans_execution_label(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    iteration = int(row.get("iterations") or row.get("absolute_simple_iteration") or 0)
    if status in {"CHECKPOINT_READY", "MANUAL_REVIEW_CHECKPOINT_READY"}:
        return "Checkpoint validado"
    if iteration >= 20000 or status in {"COMPLETED", "RANS_BASE_MAX_ITERATIONS"}:
        return "Finalizado (no revisado)"
    if iteration > 0 or "PARTIAL" in status or "PAUSED" in status:
        return "Parcialmente ejecutado (reanudable)"
    return "No ejecutado"


def _rans_case_catalog(
    meshes: dict[str, dict[str, Any]],
    checkpoint_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for mesh_id, mesh in meshes.items():
        for alpha in (8.0, 16.0):
            state = _checkpoint_for_angle(checkpoint_rows, mesh_id, alpha)
            key = f"{mesh_id}|{alpha:g}"
            catalog[key] = {
                "mesh_id": mesh_id,
                "checkpoint_id": str(state.get("mesh_id") or mesh_id),
                "alpha_deg": alpha,
                "label": (
                    f"{str(mesh.get('topology')).title()} "
                    f"{str(mesh.get('level')).title()} {alpha:g}°"
                ),
                "status": _rans_execution_label(state),
                "state": state,
            }
    return catalog


def _compatible_checkpoint(
    checkpoint_rows: list[dict[str, Any]],
    mesh_id: str,
) -> bool:
    return str(_selected_checkpoint(checkpoint_rows, mesh_id).get("status")) in {
        "CHECKPOINT_READY",
        "DIAGNOSTIC_CHECKPOINT",
        "MANUAL_REVIEW_CHECKPOINT_READY",
    }


def _review_label(status: str) -> str:
    return {
        "RANS_AUTO_CONVERGED_STRICT": "Auto-convergida (criterio estricto)",
        "RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING": (
            "Auto-convergida con advertencia de plateau"
        ),
        "RANS_USER_ACCEPTED_STATISTICALLY_STEADY": (
            "Aprobada manualmente como estacionaria"
        ),
        "RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY": (
            "Aprobada solo como inicializacion"
        ),
        "RANS_REVIEW_REQUIRED": "Revision pendiente",
        "RANS_REJECTED": "Rechazada",
    }.get(status, status or "Sin revision")


def _mesh_content(mesh: dict[str, Any]) -> dict[str, Any]:
    package = Path(str(mesh.get("mesh_package") or ""))
    package_manifest = _read_json(package / "mesh_package_manifest.json")
    return {
        "id": mesh.get("id"),
        "topology": mesh.get("topology"),
        "level": mesh.get("level"),
        "geometry_package": mesh.get("geometry_package"),
        "case_package": mesh.get("case_package"),
        "mesh_package": mesh.get("mesh_package"),
        "mesh_hash": mesh.get("mesh_hash"),
        "geometry_hash": mesh.get("geometry_hash"),
        "case_hash": mesh.get("case_hash"),
        "cells": mesh.get("cell_count"),
        "checkMesh": mesh.get("checkMesh_status"),
        "package_manifest": package_manifest,
    }


def _monitor_charts(
    snapshot: dict[str, Any],
    *,
    key_scope: str,
) -> None:
    st.markdown(f"**{snapshot.get('title', 'Monitor escalar')}**")
    metrics = st.columns(6)
    metrics[0].metric("Estado", str(snapshot.get("status", "UNKNOWN")))
    metrics[1].metric(
        "Iteracion / tiempo",
        str(snapshot.get("iteration_or_time") or "-"),
    )
    metrics[2].metric(
        "Iteraciones totales" if str(snapshot.get("mode") or "RANS").upper() == "RANS"
        else "Timesteps totales ejecutados",
        int(snapshot.get("steps_total_executed") or snapshot.get("steps_observed") or 0),
    )
    metrics[3].metric(
        "Tiempo transcurrido",
        (
            f"{float(snapshot['elapsed_s']) / 60.0:.1f} min"
            if snapshot.get("elapsed_s") is not None
            else "-"
        ),
    )
    metrics[4].metric(
        "Tiempo restante estimado",
        (
            f"{float(snapshot['estimated_remaining_s']) / 60.0:.1f} min"
            if snapshot.get("estimated_remaining_s") is not None
            else "-"
        ),
    )
    metrics[5].metric(
        "Cores MPI",
        str(snapshot.get("n_cores") or "-"),
    )
    if str(snapshot.get("mode") or "RANS").upper() == "URANS":
        courant_rows = list(snapshot.get("courant") or [])
        maximum_courant = (
            max(float(row.get("max") or 0.0) for row in courant_rows)
            if courant_rows
            else None
        )
        urans_metrics = st.columns(6)
        urans_metrics[0].metric("Etapa", str(snapshot.get("stage") or "-"))
        urans_metrics[1].metric(
            "Target deltaT [s]",
            f"{float(snapshot['target_deltaT_s']):.6g}"
            if snapshot.get("target_deltaT_s") is not None
            else "-",
        )
        urans_metrics[2].metric(
            "Phase deltaT [s]",
            f"{float(snapshot['phase_deltaT_s']):.6g}"
            if snapshot.get("phase_deltaT_s") is not None
            else "-",
        )
        urans_metrics[3].metric(
            "Tiempo físico [s]",
            f"{float(snapshot['physical_time_s']):.6g}"
            if snapshot.get("physical_time_s") is not None
            else "-",
        )
        urans_metrics[4].metric(
            "t*",
            f"{float(snapshot['convective_time']):.4g}"
            if snapshot.get("convective_time") is not None
            else "-",
        )
        urans_metrics[5].metric(
            "Co máximo observado",
            f"{maximum_courant:.4g}" if maximum_courant is not None else "-",
        )
    run_id = str(snapshot.get("run_id") or "active")
    is_rans = str(snapshot.get("mode") or "RANS").upper() == "RANS"
    full_history = st.toggle(
        "Mostrar todo el historial escalar disponible",
        value=is_rans,
        key=f"validation-monitor-full-history-{key_scope}-{run_id}",
        help=(
            "RANS usa por defecto todo el historial (la figura solo reduce píxeles, no "
            "recorta iteraciones). En URANS puede usarse la ventana reciente para evitar "
            "que una campaña larga oculte la evolución final."
        ),
    )
    displayed = dict(snapshot)
    if not full_history:
        displayed["residuals"] = list(
            snapshot.get("residuals") or []
        )[-500:]
        displayed["forces"] = list(snapshot.get("forces") or [])[-500:]
    separate = st.segmented_control(
        "Vista de coeficientes",
        ["Compacta", "Cd/Cm separados"],
        default="Compacta",
        key=f"validation-monitor-coeff-view-{key_scope}-{run_id}",
    ) == "Cd/Cm separados"
    residual_plot, residual_meta = residual_figure(
        displayed,
        mode=str(snapshot.get("mode") or "RANS"),
    )
    coefficient_plot, coefficient_meta = coefficient_figure(
        displayed,
        mode=str(snapshot.get("mode") or "RANS"),
        separate_cd_cm=separate,
        rans_discard_initial=(
            500 if str(snapshot.get("mode") or "RANS").upper() == "RANS" else 0
        ),
    )
    try:
        # st.pyplot renders a static image: there is no toolbar, pan or zoom
        # capable of capturing the page scroll.
        chart_columns = st.columns(2)
        chart_columns[0].pyplot(
            residual_plot, width="stretch"
        )
        chart_columns[1].pyplot(
            coefficient_plot, width="stretch"
        )
    finally:
        close_figures(residual_plot, coefficient_plot)
    hidden = int(residual_meta["nonpositive_or_nonfinite_hidden"])
    discarded = int(coefficient_meta["efficiency_values_discarded"])
    if hidden or discarded:
        st.caption(
            f"Solo visualizacion: {hidden} residuales no positivos/no finitos "
            f"y {discarded} valores Cl/Cd no definidos se muestran como NaN. "
            "Los datos brutos no se modifican."
        )
    if str(snapshot.get("mode") or "RANS").upper() == "RANS":
        st.caption(
            "La continuidad se conserva en el diagnostico RANS. Courant no "
            "aplica a SIMPLE estacionario y no se genera ni se muestra."
        )
    else:
        st.caption(
            "Continuidad y Courant se conservan para el diagnostico URANS; "
            "se omiten del monitor escalar principal."
        )
    forces = pd.DataFrame(snapshot.get("forces") or [])
    recent_means: dict[str, float] = {}
    if not forces.empty:
        tail = forces.tail(max(1, int(len(forces) * 0.10)))
        for field in ("Cl", "Cd", "Cm"):
            values = pd.to_numeric(tail.get(field), errors="coerce")
            if values is not None and values.notna().any():
                recent_means[field] = float(values.mean())
    gate = snapshot.get("gate") or {}
    timing = snapshot.get("performance") or {}
    diagnostic_metrics = st.columns(5)
    diagnostic_metrics[0].metric(
        "Última iteración",
        str(snapshot.get("iteration_or_time") or "-"),
    )
    diagnostic_metrics[1].metric(
        "Cl final", f"{recent_means.get('Cl', float('nan')):.5g}"
    )
    diagnostic_metrics[2].metric(
        "Cd final", f"{recent_means.get('Cd', float('nan')):.5g}"
    )
    diagnostic_metrics[3].metric(
        "Gate", str(gate.get("status") or "PENDING")
    )
    diagnostic_metrics[4].metric(
        "s/iteración",
        (
            f"{float(timing['median_s_per_step']):.4g}"
            if timing.get("median_s_per_step") is not None
            else "-"
        ),
    )
    performance = snapshot.get("performance") or {}
    if performance.get("status") == "MEASURED":
        st.dataframe(
            [{
                "origen": "Medido en este equipo y esta ejecucion",
                "muestras": performance.get("samples"),
                "mediana [s/paso]": performance.get("median_s_per_step"),
                "p25 [s/paso]": performance.get("p25_s_per_step"),
                "p75 [s/paso]": performance.get("p75_s_per_step"),
                "media [s/paso]": performance.get("mean_s_per_step"),
                "desviacion [s/paso]": performance.get("stdev_s_per_step"),
            }],
            hide_index=True,
            width="stretch",
        )


def _render_live_monitor(
    *,
    root: Path,
    meshes: dict[str, dict[str, Any]],
    follow_active_execution: bool,
    pinned_run_id: str | None,
    refresh_seconds: int,
    tc_s: float,
    key_scope: str,
) -> None:
    @st.fragment(run_every=refresh_seconds)
    def monitor_fragment() -> None:
        try:
            # Resolve from disk on every tick so a queue can switch canonical
            # cases and phases without a full outer-page rerender.
            row = validation_live_execution(
                root,
                follow_active_execution=follow_active_execution,
                pinned_run_id=pinned_run_id,
            )
            if not row:
                st.info("No hay una ejecucion activa para monitorizar.")
                return
            mesh = meshes.get(str(row.get("mesh_id")))
            case_path = row.get("case_path")
            if not mesh or not case_path:
                st.info("La ejecucion activa aun no ha publicado una fuente valida.")
                return
            snapshot = validation_monitor_snapshot(
                Path(str(case_path)),
                mode="RANS" if row.get("mode") == "RANS" else "URANS",
                run_id=str(row.get("run_id") or mesh.get("id")),
                topology=str(mesh.get("topology")),
                mesh_level=str(mesh.get("level")),
                cell_count=int(mesh.get("cell_count") or 0),
                stage=str(row.get("stage") or ""),
                tc_s=tc_s,
                steps_planned=int(row.get("steps_planned") or 0) or None,
                queue_position=int(row.get("queue_position") or 0) or None,
                queue_total=int(row.get("queue_total") or 0) or None,
                target_delta_t=(
                    float(row["target_deltaT"])
                    if row.get("target_deltaT") is not None else None
                ),
                phase_delta_t=(
                    float(row["phase_deltaT"])
                    if row.get("phase_deltaT") is not None else None
                ),
            )
            snapshot["status"] = str(row.get("status") or snapshot.get("status"))
            snapshot["stage"] = str(row.get("stage") or snapshot.get("stage"))
            if row.get("n_cores") is not None:
                snapshot["n_cores"] = int(row["n_cores"])
        except Exception as exc:
            st.info(f"Monitor en espera: {exc}")
            return
        _monitor_charts(snapshot, key_scope=key_scope)

    monitor_fragment()


def _save_strategy(
    root: Path,
    config: dict[str, Any],
) -> None:
    try:
        save_validation_study_config(root, config)
        st.success("Configuracion aislada guardada atomicamente.")
    except Exception as exc:
        st.error(str(exc))


def _render_rans_execution_menu(
    root: Path,
    start_job: StartJob,
    *,
    meshes: dict[str, dict[str, Any]],
    checkpoint_rows: list[dict[str, Any]],
    continue_on_nonfatal_failure: bool,
) -> None:
    """Execution-only RANS menu indexed by mesh and angle."""
    catalog = _rans_case_catalog(meshes, checkpoint_rows)
    st.caption(
        "Cada identidad combina una malla y un ángulo. Los parámetros SIMPLE "
        "se guardan exclusivamente en Solver y estrategia."
    )
    status_rows = []
    for mesh_id, mesh in meshes.items():
        status_rows.append({
            "Malla": f"{str(mesh.get('topology')).title()} {str(mesh.get('level')).title()}",
            "8°": catalog[f"{mesh_id}|8"]["status"],
            "16°": catalog[f"{mesh_id}|16"]["status"],
        })
    st.dataframe(status_rows, hide_index=True, width="stretch")

    labels = {key: value["label"] for key, value in catalog.items()}
    individual_tab, queue_tab = st.tabs(["Ejecución individual", "Ejecución secuencial"])
    with individual_tab:
        selection = st.selectbox(
            "Malla y ángulo",
            list(catalog),
            format_func=lambda value: f"{labels[value]} · {catalog[value]['status']}",
            key="validation-rans-case-selection",
        )
        selected = catalog[selection]
        st.metric("Estado", selected["status"])
        confirm = st.checkbox(
            "Confirmo la ejecución o reanudación SIMPLE",
            key="validation-rans-case-confirm",
        )
        if st.button(
            "Ejecutar / continuar caso",
            type="primary",
            disabled=not confirm or selected["status"] in {
                "Finalizado (no revisado)", "Checkpoint validado",
            },
            key="validation-rans-case-run",
        ):
            start_job(
                "validation_lab_rans_run_one",
                validation_study_command(
                    root, "rans-base", mesh_id=selected["mesh_id"],
                    alpha_deg=float(selected["alpha_deg"]), run=True,
                ),
            )
    with queue_tab:
        queue_selection = st.multiselect(
            "Casos y orden de ejecución",
            list(catalog),
            format_func=lambda value: f"{labels[value]} · {catalog[value]['status']}",
            key="validation-rans-selection-queue",
        )
        st.dataframe(
            [
                {"Orden": index + 1, "Caso": labels[key], "Estado": catalog[key]["status"]}
                for index, key in enumerate(queue_selection)
            ],
            hide_index=True,
            width="stretch",
        )
        confirm_queue = st.checkbox(
            "Confirmo la cola secuencial",
            key="validation-rans-selection-queue-confirm",
        )
        if st.button(
            "Ejecutar / continuar cola",
            type="primary",
            disabled=not queue_selection or not confirm_queue,
            key="validation-rans-selection-queue-run",
        ):
            start_job(
                "validation_lab_rans_selection_queue",
                validation_study_command(
                    root,
                    "rans-selection-queue",
                    case_specs=[
                        f"{catalog[key]['mesh_id']}:{catalog[key]['alpha_deg']}"
                        for key in queue_selection
                    ],
                    continue_on_nonfatal_failure=continue_on_nonfatal_failure,
                    run=True,
                ),
            )


def render_convergence_lab(root: Path, start_job: StartJob) -> None:
    st.info(
        "Laboratorio de convergencia espacial y temporal para LS(1)-0417 cerrado "
        "y Ram-Air abierto. Condición común: M=0.15 y Re=1.9e6; la campaña "
        "empieza en 16° para cerrado y en 8° para abierto, invirtiendo después el orden. "
        "Este laboratorio no sustituye una validacion de polar."
    )
    snapshot = validation_study_snapshot(root)
    if not snapshot:
        st.warning(
            "El laboratorio aislado no esta inicializado. La inicializacion "
            "registra las seis mallas reales y calcula sus hashes; no ejecuta OpenFOAM."
        )
        if st.button(
            "Inicializar laboratorio",
            type="primary",
            key="validation-lab-init",
        ):
            start_job(
                "validation_lab_init",
                validation_study_command(root, "init"),
            )
        return

    manifest = snapshot["study_manifest"]
    registry = snapshot["mesh_registry"]
    config = json.loads(json.dumps(snapshot["study_config"]))
    matrix = snapshot["run_matrix"]
    checkpoint_rows = list(snapshot.get("rans_checkpoints") or [])
    review_rows = list(snapshot.get("rans_reviews") or [])
    reviews = {str(row["mesh_id"]): row for row in review_rows}
    execution_registry = dict(snapshot.get("execution_registry") or {})
    execution_rows = list(execution_registry.get("runs") or [])
    active = Path(manifest["active_workspace"])
    result = Path(manifest["results_workspace"])
    runs = list(matrix.get("runs", []))
    run_by_id = {str(row["run_id"]): row for row in runs}
    meshes = {
        str(row["id"]): row for row in registry.get("meshes", [])
    }
    mesh_ids = list(meshes)
    condition = config["operating_condition"]
    validation = config["validation_study"]
    rans_config = validation["rans_base_states"]
    urans_config = validation["urans"]

    active_execution_id = (
        execution_registry.get("pinned_run_id")
        or execution_registry.get("active_run_id")
    )
    active_execution = next(
        (
            row
            for row in execution_rows
            if str(row.get("run_id")) == str(active_execution_id)
        ),
        None,
    )
    sections = [
        "Mallas y condiciones",
        "Solver y estrategia",
        "RANS",
        "URANS",
        "Convergencia espacio-tiempo",
        "Informes y workspace",
    ]
    section_key = "validation-lab-section"
    if st.session_state.get(section_key) not in sections:
        st.session_state[section_key] = sections[0]
    top_section = st.radio(
        "Navegación del Validation Lab",
        sections,
        horizontal=True,
        key=section_key,
        label_visibility="collapsed",
    )
    subsection_options = {
        "Mallas y condiciones": ["Registro y condiciones"],
        "Solver y estrategia": ["Ajustes SIMPLE y PIMPLE"],
        "RANS": [
            "Ejecución",
            "Revisión y postproceso",
            "Convergencia espacial",
        ],
        "URANS": [
            "Ejecución",
            "Revisión y postproceso",
            "Sensibilidad PIMPLE 2/3/4",
        ],
        "Convergencia espacio-tiempo": [
            "Cerrado",
            "Abierto",
            "Coste y precisión",
            "Frecuencias",
            "Courant",
        ],
        "Informes y workspace": ["Informes", "Almacenamiento y limpieza"],
    }
    subsection = st.segmented_control(
        "Subsección",
        subsection_options[top_section],
        default=subsection_options[top_section][0],
        key=f"validation-lab-subsection-{top_section}",
        width="stretch",
    )
    # Streamlit segmented controls can transiently return ``None`` when the
    # selected item is toggled during a rerun.  Navigation must remain total:
    # fall back to the first subsection instead of indexing the legacy map
    # with an invalid widget value.
    if subsection not in subsection_options[top_section]:
        subsection = subsection_options[top_section][0]
    legacy_section = {
        ("Mallas y condiciones", "Registro y condiciones"): "Mallas y condiciones",
        ("Solver y estrategia", "Ajustes SIMPLE y PIMPLE"): "Solver y estrategia",
        ("RANS", "Ejecución"): "Solver y estrategia",
        ("RANS", "Revisión y postproceso"): "Análisis RANS",
        ("RANS", "Convergencia espacial"): "Convergencia RANS",
        ("URANS", "Ejecución"): "Matriz URANS",
        ("URANS", "Revisión y postproceso"): "Análisis URANS",
        ("URANS", "Sensibilidad PIMPLE 2/3/4"): "Sensibilidad PIMPLE",
        ("Convergencia espacio-tiempo", "Cerrado"): "Convergencia malla-tiempo",
        ("Convergencia espacio-tiempo", "Abierto"): "Convergencia malla-tiempo",
        ("Convergencia espacio-tiempo", "Coste y precisión"): "Convergencia malla-tiempo",
        ("Convergencia espacio-tiempo", "Frecuencias"): "Frecuencias",
        ("Convergencia espacio-tiempo", "Courant"): "Courant",
        ("Informes y workspace", "Informes"): "Informes",
        ("Informes y workspace", "Almacenamiento y limpieza"): "Informes",
    }
    section = legacy_section[(top_section, subsection)]
    completed_bases = sum(
        str(row.get("status")) in {
            "CHECKPOINT_READY",
            "MANUAL_REVIEW_CHECKPOINT_READY",
        }
        for row in checkpoint_rows
    )
    active_label = (
        f"{active_execution.get('mesh_id')} / "
        f"{active_execution.get('status')}"
        if active_execution
        else "sin ejecución activa"
    )
    st.caption(
        "Caso M0.15 | Re1.9e6 | c1m | closed 16°→8° | open 8°→16°"
        f"  ·  Bases {completed_bases}/6 completas"
        f"  ·  Job activo: {active_label}"
    )
    refresh_seconds = st.select_slider(
        "Refresco del monitor activo [s]",
        options=[15, 30, 60],
        value=int(urans_config.get("monitor_refresh_seconds", 30)),
        key="validation-lab-monitor-refresh",
        help="Solo afecta al monitor global durante una ejecución real.",
    )
    with st.expander(
        "Monitor global de la ejecución activa",
        expanded=False,
    ):
        follow_active = st.toggle(
            "Seguir automáticamente la ejecución activa",
            value=bool(execution_registry.get("follow_active_default", True)),
            key="validation-global-follow-active",
        )
        monitored_execution = active_execution
        pinned_run_id: str | None = None
        if not follow_active and execution_rows:
            monitor_ids = [str(row.get("run_id")) for row in execution_rows]
            pinned = str(execution_registry.get("pinned_run_id") or "")
            selected_monitor_id = st.selectbox(
                "Ejecución fijada en el monitor",
                monitor_ids,
                index=monitor_ids.index(pinned) if pinned in monitor_ids else 0,
                key="validation-global-pinned-run",
            )
            monitored_execution = next(
                row
                for row in execution_rows
                if str(row.get("run_id")) == selected_monitor_id
            )
            pinned_run_id = selected_monitor_id
        if monitored_execution and monitored_execution.get("case_path"):
            if monitored_execution.get("error"):
                st.error(
                    f"{monitored_execution.get('status')}: "
                    f"{monitored_execution.get('error')}"
                )
                remediation = monitored_execution.get("remediation_actions") or []
                if remediation:
                    st.markdown(
                        "**Remediation:** " + " · ".join(map(str, remediation))
                    )
            _render_live_monitor(
                root=root,
                meshes=meshes,
                follow_active_execution=bool(follow_active),
                pinned_run_id=None if follow_active else pinned_run_id,
                refresh_seconds=int(refresh_seconds),
                tc_s=float(condition["tc_s"]),
                key_scope="active",
            )
            manager = JobManager(root)
            active_jobs = manager.active_jobs()
            active_job = active_jobs[0] if active_jobs else None
            live_status = str(monitored_execution.get("status") or "")
            stop_columns = st.columns([1, 1, 4])
            if active_job is not None and active_job.status == "RUNNING":
                if stop_columns[0].button(
                    "Solicitar parada",
                    key="validation-global-request-stop",
                ):
                    mode = str(monitored_execution.get("mode") or "").upper()
                    try:
                        if "PIMPLE" in mode or "pimple" in active_job.stage.lower():
                            request_validation_pimple_stop(root)
                        elif "RANS" in mode or "rans" in active_job.stage.lower():
                            request_validation_rans_stop(root, active_job.command)
                        else:
                            request_openfoam_clean_stop(
                                Path(str(monitored_execution["case_path"])),
                                "writeNow",
                            )
                        manager.mark_stop_requested(active_job)
                        st.warning(
                            "Parada limpia solicitada. Se conservara el ultimo "
                            "estado escrito para reanudar la ejecucion."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"No se pudo solicitar la parada: {exc}")
            elif active_job is not None and active_job.status in {
                "STOP_REQUESTED", "STOPPING",
            }:
                stop_columns[1].warning("Parada limpia en curso")
                if stop_columns[0].button(
                    "Forzar parada",
                    key="validation-global-force-stop",
                ):
                    manager.force_stop(active_job)
                    st.rerun()
            elif live_status in {"RUNNING", "STOP_REQUESTED", "STOPPING"}:
                stop_columns[0].warning(
                    "El registro indicaba una ejecucion activa, pero no existe "
                    "un proceso propietario verificable. Se reconciliara al refrescar."
                )
        else:
            st.caption(
                "No hay una ejecución activa. Los monitores de revisión son "
                "instantáneas estáticas y no lanzan refresco automático."
            )

    if section == "Mallas y condiciones":
        st.markdown(
            "Seleccione una **malla** para inspeccionar su geometría registrada, "
            "calidad y checkpoints. La selección no modifica casos ni configura "
            "el solver; todas las bases comparten la misma física."
        )
        mesh_rows = [{
            "Topologia": row["topology"],
            "Nivel": row["level"],
            "Celdas": row["cell_count"],
            "Calidad": (
                f"{row['grade']} / {row['checkMesh_status']}"
            ),
            "Checkpoint 8°": _rans_execution_label(
                _checkpoint_for_angle(checkpoint_rows, row["id"], 8.0)
            ),
            "Checkpoint 16°": _rans_execution_label(
                _checkpoint_for_angle(checkpoint_rows, row["id"], 16.0)
            ),
        } for row in meshes.values()]
        st.dataframe(mesh_rows, width="stretch", hide_index=True)
        current_mesh = str(
            snapshot.get("active_selection", {}).get("mesh_id")
            or mesh_ids[0]
        )
        selected_mesh = st.selectbox(
            "Malla",
            mesh_ids,
            index=mesh_ids.index(current_mesh) if current_mesh in mesh_ids else 0,
            key="validation-lab-mesh",
            help=(
                "Geometria + condiciones de operacion + malla. Los hashes "
                "impiden reutilizar un checkpoint incompatible."
            ),
        )
        st.caption("La malla elegida solo controla la inspección mostrada en esta subsección.")
        actions = st.columns(3)
        if actions[0].button(
            "Abrir malla seleccionada en Gmsh",
            key="validation-lab-open-gmsh",
            help=(
                "Abre el archivo .msh real registrado mediante Gmsh 4.15.2. "
                "No regenera ni modifica la malla."
            ),
        ):
            try:
                request = open_validation_mesh_viewer(
                    root,
                    selected_mesh,
                    viewer="linux_wslg",
                )
                st.success(
                    f"Gmsh solicitado para {Path(request['mesh_path']).name}; "
                    f"PID {request['pid']}."
                )
            except Exception as exc:
                st.error(str(exc))
        show_quality = actions[1].button(
            "Ver calidad",
            key="validation-lab-show-quality",
        )
        if actions[2].button(
            "Ir a base RANS",
            key="validation-lab-go-rans",
        ):
            st.info(
                "Abra Solver y estrategia temporal para generar o continuar "
                f"la base {selected_mesh}."
            )
        if show_quality:
            selected_quality = meshes[selected_mesh]
            st.dataframe([{
                "No ortogonalidad maxima [deg]": selected_quality[
                    "max_non_orthogonality_deg"
                ],
                "Skewness maxima": selected_quality["max_skewness"],
                "Determinante minimo": selected_quality[
                    "min_cell_determinant"
                ],
                "Peso de interpolacion minimo": selected_quality[
                    "min_face_interpolation_weight"
                ],
                "Ratio de volumen minimo": selected_quality[
                    "min_face_volume_ratio"
                ],
            }], hide_index=True, width="stretch")
        with st.expander("Detalles tecnicos, calidad y provenance"):
            st.json(_mesh_content(meshes[selected_mesh]))
            st.markdown("**Compatibilidad de estados base RANS**")
            st.dataframe(checkpoint_rows, hide_index=True, width="stretch")
            st.markdown("**Revision de estados base RANS**")
            st.dataframe(review_rows, hide_index=True, width="stretch")
            if st.button(
                "Actualizar hashes registrados",
                key="validation-lab-refresh-hashes",
            ):
                start_job(
                    "validation_lab_refresh_hashes",
                    validation_study_command(root, "init", refresh_hashes=True),
                )
    if section == "Mallas y condiciones":
        st.markdown("### Condicion de operacion")
        cols = st.columns(4)
        cols[0].metric("Mach", f"{float(condition['mach']):.4g}")
        cols[1].metric("Reynolds", f"{float(condition['reynolds']):.4g}")
        cols[2].metric("Cuerda", f"{float(condition['chord_m']):.4g} m")
        cols[3].metric("Ángulos RANS base", "closed 16° | open 8°")
        st.dataframe(
            [{"propiedad": key, "valor": value} for key, value in condition.items()],
            hide_index=True,
            width="stretch",
        )
        st.info(
            "La condicion de referencia iguala simultaneamente Mach y Reynolds "
            "con las propiedades documentadas del caso. La densidad no se "
            "interpreta automaticamente como ISA a nivel del mar."
        )

    if top_section == "RANS" and subsection == "Ejecución":
        _render_rans_execution_menu(
            root,
            start_job,
            meshes=meshes,
            checkpoint_rows=checkpoint_rows,
            continue_on_nonfatal_failure=bool(
                rans_config.get("continue_on_nonfatal_failure", True)
            ),
        )
        return

    if section == "Solver y estrategia":
        active_mesh_id = str(
            snapshot.get("active_selection", {}).get("mesh_id")
            or mesh_ids[0]
        )
        active_mesh_quality = meshes.get(active_mesh_id, meshes[mesh_ids[0]])
        automatic_mesh_numerics = automatic_non_orthogonal_controls(
            float(active_mesh_quality.get("max_non_orthogonality_deg") or 0.0)
        )
        automatic_correctors = int(
            automatic_mesh_numerics["n_non_orthogonal_correctors"]
        )
        automatic_laplacian = str(
            automatic_mesh_numerics["laplacian_scheme"]
        )
        st.markdown("### Estado base RANS / SIMPLE")
        st.caption(
            "La cola muestra las seis bases canónicas y omite sin ocultar las ya aceptadas. "
            "Los correctores y el laplaciano se derivan del checkMesh real; la cola guarda "
            "U, p, nuTilda y phi cuando OpenFOAM lo escribe."
        )
        st.info(
            f"Malla activa `{active_mesh_id}`: no ortogonalidad máxima "
            f"{automatic_mesh_numerics['maximum_non_orthogonality_deg']:.3f}°. "
            f"SIMPLE/PIMPLE usarán {automatic_correctors} corrector(es) y "
            f"`{automatic_laplacian}`."
        )
        rans_config["simple_non_orthogonal_correctors"] = automatic_correctors
        urans_config["pimple_non_orthogonal_correctors"] = automatic_correctors
        rans_config["laplacian_scheme"] = automatic_laplacian
        urans_config["laplacian_scheme"] = automatic_laplacian
        rans_config[
            "minimum_simple_iterations_before_convergence_check"
        ] = 20000
        rans_config["initial_iterations"] = 20000
        rans_config["extension_iterations"] = 20000
        rans_config["automatic_queue_max_iterations"] = 20000
        rans_config["maximum_iterations"] = 20000
        rans_config["allow_early_stop"] = False
        rans_config["native_residual_control_enabled"] = False
        rans_config["relaxation"] = {"p": 0.3, "U": 0.7, "nuTilda": 0.7}
        contract_cols = st.columns(2)
        contract_cols[0].metric(
            "Primera evaluacion de convergencia",
            "SIMPLE 20 000",
        )
        contract_cols[1].metric(
            "Parada residual nativa de OpenFOAM",
            "Desactivada",
        )
        st.caption(
            "Cada base ejecuta un único bloque de 20 000 iteraciones. Los "
            "diagnósticos se calculan al terminar, pero la aceptación es manual."
        )
        iteration_cols = st.columns(3)
        rans_config["initial_iterations"] = iteration_cols[0].number_input(
            "Iteraciones iniciales",
            value=20000,
            disabled=True,
            help="Objetivo absoluto inicial congelado para este batch.",
        )
        rans_config["extension_iterations"] = iteration_cols[1].number_input(
            "Bloque fijo",
            value=20000,
            disabled=True,
            help="No se realizan ampliaciones automáticas por etapas.",
        )
        rans_config["maximum_iterations"] = iteration_cols[2].number_input(
            "Limite total de iteraciones",
            value=20000,
            disabled=True,
            help="Al llegar a 20000 se conserva el estado y se solicita revisión manual.",
        )
        simple_cols = st.columns(4)
        rans_config["simple_non_orthogonal_correctors"] = simple_cols[0].number_input(
            "Correctores no ortogonales SIMPLE",
            min_value=0,
            max_value=4,
            value=automatic_correctors,
            disabled=True,
            help=(
                "Automático desde checkMesh: <50° → 0; 50–<70° → 1; >=70° → 2."
            ),
        )
        rans_config["mpi_ranks"] = simple_cols[1].number_input(
            "Procesos MPI RANS",
            min_value=1,
            max_value=8,
            value=int(rans_config["mpi_ranks"]),
            help="Máximo disponible. Con selección automática el runner usa menos si la malla no escala eficientemente.",
        )
        rans_config["timeout_min"] = simple_cols[2].number_input(
            "Limite RANS por bloque [min]",
            min_value=1.0,
            value=float(rans_config["timeout_min"]),
        )
        rans_config["potentialFoam"] = simple_cols[3].toggle(
            "Inicializar con potentialFoam",
            value=bool(rans_config["potentialFoam"]),
        )
        parallel_cols = st.columns(2)
        rans_config["automatic_core_selection"] = parallel_cols[0].toggle(
            "Seleccionar procesos automáticamente",
            value=bool(rans_config.get("automatic_core_selection", True)),
            help="Objetivo 50k-200k celdas por proceso, limitado por MPI y por el máximo seleccionado.",
        )
        rans_config["renumber_before_decompose"] = parallel_cols[1].toggle(
            "Renumerar antes de descomponer",
            value=bool(rans_config.get("renumber_before_decompose", True)),
            help="Ejecuta renumberMesh -overwrite solo al iniciar un caso paralelo limpio.",
        )
        policy_cols = st.columns(3)
        policy_cols[0].metric("Parada automática", "No")
        rans_config["continue_queue_after_nonconvergence"] = policy_cols[1].toggle(
            "Continuar tras no convergencia",
            value=bool(rans_config["continue_queue_after_nonconvergence"]),
        )
        rans_config["continue_on_nonfatal_failure"] = policy_cols[2].toggle(
            "Continuar tras fallo no fatal",
            value=bool(rans_config["continue_on_nonfatal_failure"]),
        )
        with st.expander("Ajustes de parada automática", expanded=False):
            st.caption(
                "Se conservan como diagnóstico de estabilidad; el contrato actual "
                "no detiene antes de las 20 000 iteraciones."
            )
            force_cols = st.columns(3)
            rans_config["force_window_samples"] = force_cols[0].number_input(
                "Muestras para estabilidad de fuerzas",
                min_value=50,
                value=int(rans_config["force_window_samples"]),
                step=50,
            )
            rans_config["force_mean_tolerance_percent"] = force_cols[1].number_input(
                "Tolerancia de media [%]",
                min_value=0.01,
                value=float(rans_config["force_mean_tolerance_percent"]),
            )
            rans_config["force_fluctuation_tolerance_percent"] = force_cols[2].number_input(
                "Tolerancia de fluctuacion [%]",
                min_value=0.01,
                value=float(rans_config["force_fluctuation_tolerance_percent"]),
            )
        with st.expander("Solvers lineales, esquemas iniciales y relajacion RANS"):
            st.json({
                "solvers_lineales": rans_config["linear_solvers"],
                "esquemas_de_inicializacion": rans_config["initialization_schemes"],
                "relajacion": rans_config["relaxation"],
                "tolerancias_residuales": rans_config["residual_tolerances"],
                "almacenamiento": rans_config["storage_profile"],
            })

        st.markdown("### Produccion URANS / PIMPLE")
        st.caption(
            "La simulacion transitoria parte exclusivamente de un checkpoint "
            "compatible. PIMPLE conserva su propio corrector no ortogonal."
        )
        pimple_cols = st.columns(3)
        urans_config["pimple_outer_correctors"] = pimple_cols[0].number_input(
            "Correctores externos PIMPLE",
            min_value=1,
            max_value=8,
            value=int(urans_config["pimple_outer_correctors"]),
            help="Máximo de bucles externos; residualControl puede terminar antes.",
        )
        urans_config["pimple_correctors"] = pimple_cols[1].number_input(
            "Correctores de presion PIMPLE",
            min_value=1,
            max_value=8,
            value=int(urans_config["pimple_correctors"]),
        )
        urans_config["pimple_non_orthogonal_correctors"] = pimple_cols[2].number_input(
            "Correctores no ortogonales PIMPLE",
            min_value=0,
            max_value=4,
            value=automatic_correctors,
            disabled=True,
            help="Misma política automática de la malla real que en SIMPLE.",
        )
        temporal_cols = st.columns(4)
        urans_config["settling_time_star"] = temporal_cols[0].number_input(
            "Asentamiento [t*]",
            min_value=0.0,
            value=float(
                validation["settling_tc"]
                if urans_config.get("settling_time_star") is None
                else urans_config["settling_time_star"]
            ),
        )
        urans_config["sampling_time_star"] = temporal_cols[1].number_input(
            "Muestreo [t*]",
            min_value=1.0,
            value=float(
                validation["sampling_tc"]
                if urans_config.get("sampling_time_star") is None
                else urans_config["sampling_time_star"]
            ),
        )
        validation["field_write_interval_tc"] = temporal_cols[2].number_input(
            "Intervalo de campos [t*]",
            min_value=0.01,
            value=float(validation["field_write_interval_tc"]),
        )
        urans_config["retained_snapshots"] = temporal_cols[3].number_input(
            "Estados volumetricos retenidos",
            min_value=2,
            value=int(urans_config["retained_snapshots"]),
        )
        validation["settling_tc"] = float(
            urans_config["settling_time_star"]
        )
        validation["sampling_tc"] = float(
            urans_config["sampling_time_star"]
        )
        st.markdown("#### Etapas URANS")
        stage_rows = list(urans_config.get("startup_stages") or [])
        stage_rows.extend([
            {
                "name": "D", "enabled": True,
                "scheme": str(validation.get("production_scheme") or "backward"),
                "dt_factor": 1.0, "duration_mode": "t_star",
                "duration": float(urans_config["settling_time_star"]),
                "steps": 1, "purpose": "settling (excluded from statistics)",
            },
            {
                "name": "E", "enabled": True,
                "scheme": str(validation.get("production_scheme") or "backward"),
                "dt_factor": 1.0, "duration_mode": "t_star",
                "duration": float(urans_config["sampling_time_star"]),
                "steps": 1, "purpose": "production statistics",
            },
        ])
        startup_frame = pd.DataFrame(stage_rows)
        startup_editor = st.data_editor(
            startup_frame,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            disabled=["name"],
            key="validation-urans-startup-editor",
            column_config={
                "enabled": st.column_config.CheckboxColumn(
                    "Activa",
                    help="Desactiva solo esta etapa; no elimina su configuración.",
                ),
                "scheme": st.column_config.SelectboxColumn(
                    "Esquema",
                    options=["Euler", "backward", "CrankNicolson"],
                    required=True,
                ),
                "dt_factor": st.column_config.NumberColumn(
                    "Factor deltaT",
                    min_value=0.01,
                    format="%.3f",
                    required=True,
                ),
                "duration_mode": st.column_config.SelectboxColumn(
                    "Modo duración",
                    options=["steps", "t_star"],
                    required=True,
                ),
                "duration": st.column_config.NumberColumn(
                    "Duración",
                    min_value=1.0e-6,
                    format="%.6g",
                    required=True,
                    help="Número de pasos o duración en t*, según el modo.",
                ),
                "steps": st.column_config.NumberColumn(
                    "Pasos legacy",
                    min_value=1,
                    step=1,
                    help="Compatibilidad. En el cálculo manda Duración.",
                ),
                "purpose": st.column_config.TextColumn(
                    "Propósito",
                    required=True,
                ),
            },
        )
        edited_stages = startup_editor.to_dict(orient="records")
        urans_config["startup_stages"] = [
            row for row in edited_stages if str(row.get("name")) in {"A", "B", "C"}
        ]
        for row in edited_stages:
            if str(row.get("name")) == "D":
                urans_config["settling_time_star"] = float(row["duration"])
                validation["settling_tc"] = float(row["duration"])
            elif str(row.get("name")) == "E":
                urans_config["sampling_time_star"] = float(row["duration"])
                validation["sampling_tc"] = float(row["duration"])
        runtime_cols = st.columns(3)
        validation["mpi_ranks"] = runtime_cols[0].number_input(
            "Procesos MPI URANS",
            min_value=1,
            max_value=8,
            value=int(validation["mpi_ranks"]),
            help="Máximo MPI; en modo automático se reduce según las celdas de la malla activa.",
        )
        validation["timeout_hours"] = runtime_cols[1].number_input(
            "Limite URANS [h]",
            min_value=0.1,
            value=float(validation["timeout_hours"]),
        )
        urans_config["monitor_refresh_seconds"] = runtime_cols[2].selectbox(
            "Actualizacion del monitor [s]",
            [15, 30, 60],
            index=[15, 30, 60].index(
                int(urans_config["monitor_refresh_seconds"])
            ),
        )
        urans_config["automatic_core_selection"] = st.toggle(
            "Selección automática de procesos URANS",
            value=bool(urans_config.get("automatic_core_selection", True)),
            help="Usa el recuento exacto de la malla y deja --n-cores como límite superior.",
        )
        urans_config["renumber_before_decompose"] = st.toggle(
            "Renumerar malla antes de la primera descomposición URANS",
            value=bool(urans_config.get("renumber_before_decompose", True)),
            help="No se repite al continuar fases o reanudar resultados existentes.",
        )
        active_cells = int(active_mesh_quality.get("cell_count") or 0)
        rank_plan = recommended_core_count(
            active_cells or None,
            available_slots=8,
            requested_maximum=int(validation["mpi_ranks"]),
        )
        st.caption(
            f"Malla activa: {active_cells or 'recuento no disponible'} celdas; "
            f"recomendación {rank_plan['recommended_ranks']} procesos "
            f"({rank_plan['cells_per_rank']:.0f} celdas/proceso)."
            if rank_plan["cells_per_rank"] is not None
            else f"Recomendación provisional: {rank_plan['recommended_ranks']} procesos."
        )
        validation["rans_base_states"] = rans_config
        validation["urans"] = urans_config
        urans_config["pimple"] = {
            "nOuterCorrectors": int(
                urans_config["pimple_outer_correctors"]
            ),
            "nCorrectors": int(urans_config["pimple_correctors"]),
            "nNonOrthogonalCorrectors": int(
                urans_config["pimple_non_orthogonal_correctors"]
            ),
        }
        validation["nOuterCorrectors"] = int(
            urans_config["pimple_outer_correctors"]
        )
        validation["nCorrectors"] = int(urans_config["pimple_correctors"])
        validation["nNonOrthogonalCorrectors"] = int(
            urans_config["pimple_non_orthogonal_correctors"]
        )
        validation["retained_snapshots"] = int(
            urans_config["retained_snapshots"]
        )
        config["validation_study"] = validation
        st.markdown("### Auditoria previa de configuracion efectiva")
        audit_rows = [
            {
                "Parametro": "Max SIMPLE iterations",
                "Seleccionado": rans_config["maximum_iterations"],
                "Closed efectivo": rans_config["maximum_iterations"],
                "Open efectivo": rans_config["maximum_iterations"],
            },
            {
                "Parametro": "Extension block",
                "Seleccionado": rans_config["extension_iterations"],
                "Closed efectivo": rans_config["extension_iterations"],
                "Open efectivo": rans_config["extension_iterations"],
            },
            {
                "Parametro": "SIMPLE non-orthogonal",
                "Seleccionado": rans_config["simple_non_orthogonal_correctors"],
                "Closed efectivo": rans_config["simple_non_orthogonal_correctors"],
                "Open efectivo": rans_config["simple_non_orthogonal_correctors"],
            },
            {
                "Parametro": "PIMPLE outer",
                "Seleccionado": urans_config["pimple_outer_correctors"],
                "Closed efectivo": urans_config["pimple_outer_correctors"],
                "Open efectivo": urans_config["pimple_outer_correctors"],
            },
            {
                "Parametro": "PIMPLE pressure correctors",
                "Seleccionado": urans_config["pimple_correctors"],
                "Closed efectivo": urans_config["pimple_correctors"],
                "Open efectivo": urans_config["pimple_correctors"],
            },
            {
                "Parametro": "PIMPLE non-orthogonal",
                "Seleccionado": urans_config["pimple_non_orthogonal_correctors"],
                "Closed efectivo": urans_config["pimple_non_orthogonal_correctors"],
                "Open efectivo": urans_config["pimple_non_orthogonal_correctors"],
            },
            {
                "Parametro": "Laplacian scheme automático",
                "Seleccionado": automatic_laplacian,
                "Closed efectivo": automatic_laplacian,
                "Open efectivo": automatic_laplacian,
            },
            {
                "Parametro": "Time scheme",
                "Seleccionado": validation["production_scheme"],
                "Closed efectivo": validation["production_scheme"],
                "Open efectivo": validation["production_scheme"],
            },
            {
                "Parametro": "MPI ranks",
                "Seleccionado": validation["mpi_ranks"],
                "Closed efectivo": validation["mpi_ranks"],
                "Open efectivo": validation["mpi_ranks"],
            },
            {
                "Parametro": "Field write [t*]",
                "Seleccionado": validation["field_write_interval_tc"],
                "Closed efectivo": validation["field_write_interval_tc"],
                "Open efectivo": validation["field_write_interval_tc"],
            },
            {
                "Parametro": "Monitor refresh [s]",
                "Seleccionado": urans_config["monitor_refresh_seconds"],
                "Closed efectivo": urans_config["monitor_refresh_seconds"],
                "Open efectivo": urans_config["monitor_refresh_seconds"],
            },
        ]
        st.dataframe(
            [
                {column: str(value) for column, value in row.items()}
                for row in audit_rows
            ],
            hide_index=True,
            width="stretch",
        )
        frozen_batch = _read_json(active / "resolved_batch_config.json")
        st.markdown("### Configuración final que alcanzará OpenFOAM")
        final_rans_tab, final_urans_tab = st.tabs(["RANS / SIMPLE", "URANS / PIMPLE"])
        with final_rans_tab:
            st.json({
                "solver": "foamRun -solver incompressibleFluid",
                "simulation_type": "RANS",
                "turbulence_model": "SpalartAllmaras",
                "iterations": int(rans_config["maximum_iterations"]),
                "native_residual_stop": False,
                "nNonOrthogonalCorrectors": int(automatic_correctors),
                "laplacian_scheme": automatic_laplacian,
                "relaxation": dict(rans_config["relaxation"]),
                "linear_solvers": rans_config["linear_solvers"],
                "residual_tolerances": rans_config["residual_tolerances"],
                "initialization_schemes": rans_config["initialization_schemes"],
                "field_write": rans_config["storage_profile"],
                "mpi_ranks": int(rans_config["mpi_ranks"]),
                "timeout_min": float(rans_config["timeout_min"]),
            })
        with final_urans_tab:
            st.json({
                "solver": "foamRun -solver incompressibleFluid",
                "simulation_type": "URANS",
                "turbulence_model": "SpalartAllmaras",
                "time_step_mode": "fixed",
                "selected_dt_seconds": "selected per convergence run",
                "ddt_scheme": validation["production_scheme"],
                "nOuterCorrectors": int(urans_config["pimple_outer_correctors"]),
                "nCorrectors": int(urans_config["pimple_correctors"]),
                "nNonOrthogonalCorrectors": int(automatic_correctors),
                "laplacian_scheme": automatic_laplacian,
                "outer_residual_control": urans_config.get("outer_corrector_residual_control"),
                "startup_and_production_stages": edited_stages,
                "settling_time_star": float(urans_config["settling_time_star"]),
                "sampling_time_star": float(urans_config["sampling_time_star"]),
                "writeControl": "timeStep",
                "requested_field_interval_time_star": float(validation["field_write_interval_tc"]),
                "retained_snapshots": int(urans_config["retained_snapshots"]),
                "mpi_ranks": int(validation["mpi_ranks"]),
                "timeout_hours": float(validation["timeout_hours"]),
            })
        if frozen_batch.get("config_hash"):
            st.caption(
                "Una cola ya iniciada conserva internamente su revisión para ser reproducible. "
                "Esta vista muestra la configuración vigente para ejecuciones nuevas."
            )
        if st.button(
            (
                "Crear nueva revision de configuracion"
                if frozen_batch.get("config_hash")
                else "Guardar configuracion RANS y URANS"
            ),
            key="validation-lab-save-strategy",
        ):
            config["ui_revision"] = int(config.get("ui_revision") or 0) + 1
            _save_strategy(root, config)

        if top_section == "Solver y estrategia":
            return

        st.divider()
        st.markdown("### Cola autónoma de seis estados base RANS")
        rans_queue_order = list(mesh_ids)
        queue_rows = []
        for order, mesh_id in enumerate(rans_queue_order, start=1):
            state = _selected_checkpoint(checkpoint_rows, mesh_id)
            review = reviews.get(mesh_id, {})
            queue_rows.append({
                "Orden": order,
                "Malla": mesh_id,
                "Estado": (
                    state.get("queue_state")
                    or state.get("rans_state")
                    or state.get("status")
                ),
                "Iteración": int(
                    state.get("absolute_simple_iteration")
                    or state.get("iterations")
                    or 0
                ),
                "Objetivo": int(
                    state.get("block_target_iteration") or 20000
                ),
                "Gate": (
                    state.get("automatic_gate_status")
                    or review.get("automatic_gate")
                    or "-"
                ),
                "Tiempo 1-10k": (
                    state.get("solver_active_wall_time_first_10000_s")
                    or "-"
                ),
                "Acción": state.get("queue_action") or "PENDING",
            })
        st.dataframe(queue_rows, hide_index=True, width="stretch")
        completed_queue = sum(
            row["Estado"] in {
                "AUTO_CONVERGED",
                "PLATEAU_WARNING",
                "REVIEW_REQUIRED",
                "COMPLETED",
            }
            for row in queue_rows
        )
        st.caption(f"Batch {completed_queue}/6")
        st.caption(
            "Se continúa desde la primera base incompleta. Cada base ejecuta "
            "20 000 iteraciones fijas; los diagnósticos se muestran después y "
            "la decisión científica queda en manos del usuario."
        )
        selected_checkpoint_mesh = st.selectbox(
            "Malla para una accion individual",
            mesh_ids,
            key="validation-lab-checkpoint-mesh",
        )
        selected_state = _selected_checkpoint(
            checkpoint_rows,
            selected_checkpoint_mesh,
        )
        selected_review_state = reviews.get(selected_checkpoint_mesh, {})
        selected_protected = bool(
            str(selected_state.get("status"))
            in {
                "CHECKPOINT_READY",
                "MANUAL_REVIEW_CHECKPOINT_READY",
            }
            or bool(selected_review_state.get("rans_spatial"))
            or bool(selected_review_state.get("urans_initialization"))
            or str(selected_review_state.get("automatic_gate"))
            in {
                "RANS_AUTO_CONVERGED_STRICT",
                "RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING",
            }
        )
        st.metric("Estado compatible", _status_badge(str(selected_state["status"])))
        rans_confirm = st.checkbox(
            "Confirmo ejecucion RANS real",
            key="validation-lab-rans-confirm",
        )
        run_controls = st.columns(2)
        if run_controls[0].button(
            "Generar/continuar bases RANS",
            disabled=not rans_confirm,
            key="validation-lab-rans-run-all",
            type="primary",
        ):
            start_job(
                "validation_lab_rans_run_all",
                validation_study_command(
                    root,
                    "rans-queue",
                    run=True,
                    continue_on_nonfatal_failure=bool(
                        rans_config["continue_on_nonfatal_failure"]
                    ),
                ),
            )
        if run_controls[1].button(
            "Continuar base RANS seleccionada desde su ultima iteracion",
            disabled=not rans_confirm or selected_protected,
            key="validation-lab-rans-run-one",
        ):
            start_job(
                "validation_lab_rans_run_one",
                validation_study_command(
                    root,
                    "rans-base",
                    mesh_id=selected_checkpoint_mesh,
                    run=True,
                ),
            )
        if selected_protected:
            st.success(
                "La base seleccionada ya esta finalizada o aceptada y esta "
                "protegida frente a una ejecucion completa desde cero. Para "
                "reiniciarla use Eliminar base activa y comenzar de nuevo."
            )
        st.info(
            "Una base con resultados existentes nunca se reinicia desde cero. "
            "Para hacerlo debe usar la accion avanzada de eliminacion explicita."
        )
        with st.expander("Opciones avanzadas"):
            preparation = st.columns(2)
            if preparation[0].button(
                "Preparar y verificar las seis bases sin ejecutar",
                key="validation-lab-rans-prepare-all",
            ):
                start_job(
                    "validation_lab_rans_prepare_all",
                    validation_study_command(root, "rans-queue"),
                )
            if preparation[1].button(
                "Preparar y verificar solo la base seleccionada",
                key="validation-lab-rans-prepare-one",
            ):
                start_job(
                    "validation_lab_rans_prepare_one",
                    validation_study_command(
                        root,
                        "rans-base",
                        mesh_id=selected_checkpoint_mesh,
                    ),
                )
            with st.expander("Detalles tecnicos y provenance"):
                st.dataframe(checkpoint_rows, hide_index=True, width="stretch")
                _json_panel(
                    active / "rans_queue_state.json",
                    "Estado reanudable de la cola",
                    inline=True,
                )
                _json_panel(
                    active / "resolved_batch_config.json",
                    "Snapshot congelado de la cola",
                    inline=True,
                )
                _json_panel(
                    active / "applied_configuration_audit.json",
                    "Configuracion aplicada a los diccionarios",
                    inline=True,
                )

    if section == "Matriz URANS":
        temporal_packages = dict(config.get("temporal_packages") or {})
        active_package = str(temporal_packages.get("active") or "reference")
        package = st.segmented_control(
            "Paquete temporal",
            [
                "cummings_closed_low_cost", "cummings_open_low_cost",
                "reference", "frequency", "manual",
            ],
            default=(
                active_package
                if active_package in {
                    "cummings_closed_low_cost", "cummings_open_low_cost",
                    "reference", "frequency", "manual",
                }
                else "cummings_closed_low_cost"
            ),
            format_func=lambda value: {
                "cummings_closed_low_cost": "Cummings cerrado",
                "cummings_open_low_cost": "Cummings abierto",
                "reference": "Reference",
                "frequency": "Frequency",
                "manual": "Manual",
            }[value],
            key="canonical-urans-temporal-package",
        )
        package_help = {
            "cummings_closed_low_cost": (
                "Cerrado: Δt* = 0.01, 0.005 y 0.0025; orden angular 16° y 8°."
            ),
            "cummings_open_low_cost": (
                "Abierto: Δt* = 0.02, 0.01 y 0.005; orden angular 8° y 16°."
            ),
            "reference": (
                "Reference: 2.5e-4, 1.25e-4 y 6.25e-5 s. "
                "Son valores de comparación; el mayor no implica estabilidad."
            ),
            "frequency": (
                "Frequency: 2Δt_spec, Δt_spec y 0.5Δt_spec calculados con "
                "St_max, velocidad, cuerda y muestras por ciclo."
            ),
            "manual": "Manual: exactamente tres valores positivos, distintos y descendentes.",
        }
        st.caption(package_help[str(package or "reference")])
        if str(package).startswith("cummings_"):
            package_topology = "open" if "open" in str(package) else "closed"
            campaign = dict((config.get("campaign_engine") or {}).get(package_topology) or {})
            if package_topology == "closed":
                effect_rows = [
                    {"Efecto esperado": "Desprendimiento global/estela", "Intervalo St": "0.08-0.30", "Uso": "Duración y promedio"},
                    {"Efecto esperado": "Capa límite y armónicos de estela", "Intervalo St": "0.30-2", "Uso": "Resolución temporal"},
                    {"Efecto esperado": "Contenido de alta frecuencia", "Intervalo St": "2-20", "Uso": "Control espectral; confirmar con PSD"},
                ]
                dt_ladder = [0.01, 0.005, 0.0025, 0.00125, 0.000625, 0.0003125]
                production_star = float(campaign.get("final_time_star", 100.0))
            else:
                effect_rows = [
                    {"Efecto esperado": "Respiración de cavidad", "Intervalo St": "0.02-0.10", "Uso": "Duración y promedio"},
                    {"Efecto esperado": "Capa de cortadura del inlet", "Intervalo St": "0.10-1", "Uso": "Resolución temporal"},
                    {"Efecto esperado": "Estela y armónicos", "Intervalo St": "1-10", "Uso": "Control espectral; confirmar con PSD"},
                ]
                dt_ladder = [0.02, 0.01, 0.005, 0.0025, 0.00125, 0.000625]
                production_star = float(campaign.get("low_frequency_extension_time_star", 200.0))
            st.dataframe(effect_rows, hide_index=True, width="stretch")
            minimum_cycles = int((config.get("frequency_analysis") or {}).get("minimum_cycles", 10))
            st.dataframe(
                [
                    {
                        "Magnitud": "Frecuencia mínima de planificación",
                        "Valor": f"St={minimum_cycles / production_star:.4g}",
                        "Criterio": f"{minimum_cycles} ciclos en t*={production_star:g}",
                    },
                    {
                        "Magnitud": "Producción nominal",
                        "Valor": f"t*={production_star:g}",
                        "Criterio": "Misma duración física para comparar mallas y Δt",
                    },
                    {
                        "Magnitud": "Frecuencia máxima de auditoría",
                        "Valor": "St=20" if package_topology == "closed" else "St=10",
                        "Criterio": "Límite de planificación; la PSD real decide el intervalo útil",
                    },
                ],
                hide_index=True,
                width="stretch",
            )
            st.dataframe(
                [
                    {
                        "Δt*": value,
                        "Procedencia": (
                            "dt_Cummings_recommend" if index == 0
                            else f"dt_Cummings_recommend/{2 ** index}"
                        ),
                        "Puntos/ciclo a St=1": round(1.0 / value, 1),
                        "Puntos/ciclo a St máximo": round(
                            1.0 / ((20.0 if package_topology == "closed" else 10.0) * value), 2
                        ),
                    }
                    for index, value in enumerate(dt_ladder)
                ],
                hide_index=True,
                width="stretch",
            )
        manual_values: list[float] | None = None
        if package == "manual":
            manual_defaults = list(
                (temporal_packages.get("manual") or {}).get("values_s")
                or [2.5e-4, 1.25e-4, 6.25e-5]
            )
            manual_columns = st.columns(3)
            manual_values = [
                manual_columns[index].number_input(
                    f"Manual deltaT {index + 1} [s]",
                    min_value=1.0e-9,
                    value=float(manual_defaults[index]),
                    format="%.8g",
                    key=f"canonical-urans-manual-dt-{index}",
                )
                for index in range(3)
            ]
        if st.button("Aplicar paquete temporal", key="canonical-urans-apply-package"):
            start_job(
                "canonical_urans_temporal_package",
                validation_study_command(
                    root,
                    "preset",
                    preset=str(package or "reference"),
                    custom_dt_values_s=manual_values,
                ),
            )
        single_tab, queue_tab = st.tabs(["Caso único", "Ejecución secuencial"])
        with single_tab:
            st.caption(
                "Seleccione topología, malla y deltaT. La aplicación calcula si "
                "debe iniciar desde RANS, reanudar, revisar o solicitar un reinicio."
            )
            selector_columns = st.columns(4)
            preferred_topology = "open" if package == "cummings_open_low_cost" else "closed"
            topology = selector_columns[0].selectbox(
                "Topología", ["closed", "open"],
                index=1 if preferred_topology == "open" else 0,
                key=f"canonical-urans-topology-{package}",
            )
            selected_alpha = selector_columns[1].selectbox(
                "Ángulo [°]",
                [16.0, 8.0] if topology == "closed" else [8.0, 16.0],
                key=f"canonical-urans-alpha-{package}-{topology}",
            )
            topology_meshes = [
                mesh_id for mesh_id in mesh_ids if mesh_id.startswith(f"{topology}_")
            ]
            mesh_id = selector_columns[2].selectbox(
                "Malla", topology_meshes, key="canonical-urans-mesh"
            )
            available_rows = [
                row for row in runs
                if str(row.get("mesh_id")) == mesh_id
                and abs(float(row.get("alpha_deg") or 0.0) - float(selected_alpha)) < 1.0e-9
            ]
            dt_values = sorted(
                {float(row.get("dt_s")) for row in available_rows}, reverse=True
            )
            selected_dt = selector_columns[3].selectbox(
                "deltaT [s]",
                dt_values,
                format_func=lambda value: f"{float(value):.6g} s",
                key="canonical-urans-dt",
            )
            selected_row = next(
                row
                for row in available_rows
                if abs(float(row.get("dt_s")) - float(selected_dt)) <= 1.0e-15
            )
            selected_case_id = str(selected_row["run_id"])
            startup_mode = st.segmented_control(
                "Inicio temporal",
                ["progressive", "direct"],
                default=str(urans_config.get("startup_mode", "progressive")),
                format_func=lambda value: (
                    "Progresivo A-E" if value == "progressive" else "Directo"
                ),
                key="canonical-urans-startup-mode",
            )
            state = validation_urans_case_snapshot(root, selected_case_id)
            status_columns = st.columns(4)
            status_columns[0].metric("Caso", state["case_presence"])
            status_columns[1].metric("Resultado", state["execution_outcome"])
            status_columns[2].metric(
                "Fase", str(state.get("current_phase") or "-")
            )
            status_columns[3].metric(
                "Tiempo físico",
                "-" if state.get("current_time_s") is None else f"{float(state['current_time_s']):.6g} s",
            )
            st.info(
                f"Acción calculada: **{state['calculated_action']}**. "
                f"Caso: `{state['case_id']}`"
            )
            with st.expander("Configuración efectiva del solver", expanded=False):
                st.json(state.get("manifest", {}).get("effective_solver_config") or {
                    "nOuterCorrectors": urans_config.get("pimple_outer_correctors"),
                    "nCorrectors": urans_config.get("pimple_correctors"),
                    "nNonOrthogonalCorrectors": urans_config.get("pimple_non_orthogonal_correctors"),
                    "time_scheme": urans_config.get("production_scheme"),
                    "mpi_ranks": urans_config.get("mpi_ranks"),
                })
            action_columns = st.columns(3)
            if state["calculated_action"] in {"START_FROM_RANS", "RESUME"}:
                label = "Reanudar caso" if state["calculated_action"] == "RESUME" else "Ejecutar caso"
                if action_columns[0].button(
                    label, type="primary", key="canonical-urans-execute"
                ):
                    start_job(
                        "canonical_urans_execute",
                        validation_study_command(
                            root,
                            "execute",
                            run_id=selected_case_id,
                            startup_mode=str(startup_mode or "progressive"),
                            run=True,
                        ),
                    )
                if action_columns[1].button(
                    "Prueba rápida",
                    key="canonical-urans-quick-check",
                    help="Diagnóstico efímero; no habilita ni bloquea producción.",
                ):
                    start_job(
                        "canonical_urans_quick_check",
                        validation_study_command(
                            root, "quick-check", run_id=selected_case_id, run=True
                        ),
                    )
            elif state["calculated_action"] == "REVIEW":
                action_columns[0].success("El caso está completo y listo para revisión.")
            else:
                action_columns[0].warning(
                    "El caso requiere reinicio explícito antes de volver a ejecutarse."
                )
            with st.expander("Eliminar y reiniciar una ejecución existente", expanded=False):
                st.warning(
                    "El reinicio elimina únicamente este timeline URANS. Conserva malla, "
                    "checkpoint RANS, configuración compartida y Results."
                )
                existing_rows = _canonical_urans_rows(active)
                if not existing_rows:
                    st.info("No hay ejecuciones con tiempo físico positivo para reiniciar.")
                else:
                    restart_labels = {
                        str(row["case_id"]): (
                            f"{str(row.get('topology')).title()} · "
                            f"{str(row.get('mesh_level')).title()} · "
                            f"Δt={float(row.get('deltaT_s') or 0):.6g} s"
                        )
                        for row in existing_rows
                    }
                    restart_id = st.selectbox(
                        "Ejecución existente",
                        list(restart_labels),
                        format_func=lambda value: restart_labels[value],
                        key="canonical-urans-restart-selection",
                    )
                    restart_row = next(
                        row for row in existing_rows if row["case_id"] == restart_id
                    )
                    st.caption(
                        f"Ruta: {restart_row['run_root']} · "
                        f"último tiempo: {float(restart_row['latest_time_s']):.8g} s"
                    )
                    if st.button(
                        "Eliminar esta ejecución y preparar desde RANS",
                        key="canonical-urans-restart",
                    ):
                        start_job(
                            "canonical_urans_restart",
                            validation_study_command(
                                root,
                                "restart",
                                run_id=str(restart_id),
                                confirm_delete=str(restart_id),
                            ),
                        )
            with st.expander("Evidencia y rutas", expanded=False):
                st.json(state)

        with queue_tab:
            st.caption(
                "Construya una cola de hasta 18 identidades. Cada malla admite "
                "exactamente tres deltaT ordenados de mayor a menor."
            )
            queue_filter = st.columns(2)
            queue_preferred_topology = "open" if package == "cummings_open_low_cost" else "closed"
            queue_topology = queue_filter[0].selectbox(
                "Topología de la cola", ["closed", "open"],
                index=1 if queue_preferred_topology == "open" else 0,
                key=f"canonical-urans-queue-topology-{package}",
            )
            queue_alpha = queue_filter[1].selectbox(
                "Ángulo de la cola [°]",
                [16.0, 8.0] if queue_topology == "closed" else [8.0, 16.0],
                key=f"canonical-urans-queue-alpha-{package}-{queue_topology}",
            )
            queue_rows_available = [
                row for row in runs
                if str(row.get("topology")) == queue_topology
                and abs(float(row.get("alpha_deg") or 0.0) - float(queue_alpha)) < 1.0e-9
            ]
            queue_labels = {
                str(row["run_id"]): (
                    f"{row['topology']} | {row['mesh_level']} | α={float(row.get('alpha_deg') or 0):g}° | "
                    f"deltaT={float(row['dt_s']):.6g} s"
                )
                for row in queue_rows_available
            }
            level_order = {"coarse": 0, "medium": 1, "fine": 2}
            default_queue: list[str] = []
            if str(package).startswith("cummings_"):
                ordered_rows = sorted(
                    queue_rows_available,
                    key=lambda row: (
                        level_order.get(str(row.get("mesh_level")), 99),
                        -float(row.get("dt_star") or 0.0),
                    ),
                )
                for level in ("coarse", "medium", "fine"):
                    level_rows = [
                        row for row in ordered_rows if str(row.get("mesh_level")) == level
                    ]
                    default_queue.extend(str(row["run_id"]) for row in level_rows[:2])
            queue_selection = st.multiselect(
                "Casos de la cola",
                list(queue_labels),
                default=[value for value in default_queue if value in queue_labels],
                format_func=lambda value: queue_labels[value],
                max_selections=18,
                key=f"canonical-urans-queue-selection-{package}-{queue_topology}-{queue_alpha:g}",
            )
            queue_mode = st.segmented_control(
                "Inicio de los casos nuevos",
                ["progressive", "direct"],
                default="progressive",
                format_func=lambda value: "Progresivo A-E" if value == "progressive" else "Directo",
                key="canonical-urans-queue-mode",
            )
            preview_rows = [
                {
                    "Orden": index + 1,
                    "Caso": queue_labels[case_id],
                    "ID": case_id,
                }
                for index, case_id in enumerate(queue_selection)
            ]
            st.dataframe(preview_rows, hide_index=True, width="stretch")
            queue_actions = st.columns(3)
            if queue_actions[0].button(
                "Preparar cola",
                disabled=not queue_selection,
                key="canonical-urans-queue-prepare",
            ):
                start_job(
                    "canonical_urans_queue_prepare",
                    validation_urans_queue_command(
                        root,
                        "prepare",
                        run_ids=queue_selection,
                        startup_mode=str(queue_mode or "progressive"),
                    ),
                )
            queue_state = _read_json(active / "urans_queue_state.json")
            queue_ready = int(queue_state.get("schema_version") or 0) == 3
            queue_confirm = queue_actions[1].checkbox(
                "Confirmar ejecución", key="canonical-urans-queue-confirm"
            )
            if queue_actions[2].button(
                "Ejecutar / continuar cola",
                type="primary",
                disabled=not queue_ready or not queue_confirm,
                key="canonical-urans-queue-execute",
            ):
                start_job(
                    "canonical_urans_queue_execute",
                    validation_urans_queue_command(
                        root, "execute", run=True, resume=True
                    ),
                )
            if queue_state:
                st.dataframe(
                    queue_state.get("runs") or [], hide_index=True, width="stretch"
                )

    if section == "Análisis RANS":
        st.caption(
            "Revision unificada de una base RANS real. El gate automatico, "
            "la decision humana y el estado de ejecucion permanecen separados."
        )
        with st.expander("Aprobación administrativa de las seis bases", expanded=False):
            st.warning(
                "Esta acción no altera el gate automático. Verifica campos reales, "
                "identidad y hashes antes de habilitar cada base para convergencia "
                "espacial e inicialización URANS."
            )
            batch_alpha = st.selectbox(
                "Ángulo de las seis bases",
                [8.0, 16.0],
                format_func=lambda value: f"{value:g}°",
                key="validation-rans-six-batch-alpha",
            )
            batch_acceptance_path = (
                active
                / "reports"
                / f"rans_six_base_batch_acceptance_alpha_{batch_alpha:g}.json"
            )
            batch_confirm = st.checkbox(
                "Confirmo la aceptación explícita de las seis bases RANS actuales",
                key="validation-rans-six-batch-confirm",
            )
            if st.button(
                "Aprobar las seis bases actuales como estadísticamente estacionarias",
                disabled=not batch_confirm,
                type="primary",
                key="validation-rans-six-batch-accept",
            ):
                start_job(
                    "validation_rans_six_batch_acceptance",
                    validation_study_command(
                        root,
                        "rans-review",
                        review_action="accept-six-current",
                        alpha_deg=float(batch_alpha),
                        confirm=True,
                    ),
                )
            batch_acceptance = _read_json(batch_acceptance_path)
            if batch_acceptance:
                st.dataframe(
                    batch_acceptance.get("accepted") or [],
                    hide_index=True,
                    width="stretch",
                )
                exceptions = batch_acceptance.get("exceptions") or []
                if exceptions:
                    st.error("Existen bases no aceptadas por evidencia insuficiente.")
                    st.dataframe(exceptions, hide_index=True, width="stretch")
        review_catalog = _rans_case_catalog(meshes, checkpoint_rows)
        review_key = st.selectbox(
            "Ejecución RANS a revisar (malla y ángulo)",
            list(review_catalog),
            format_func=lambda value: (
                f"{review_catalog[value]['label']} · {review_catalog[value]['status']}"
            ),
            key="validation-rans-review-execution",
        )
        selected_case = review_catalog[review_key]
        rans_base_mesh = str(selected_case["mesh_id"])
        rans_mesh = str(selected_case["checkpoint_id"])
        selected_state = selected_case["state"]
        selected_review = reviews.get(rans_mesh, {})
        checkpoint_root = active / "checkpoints" / rans_mesh
        checkpoint_case = checkpoint_root / "case"
        review_manifest_path = checkpoint_root / "rans_review_manifest.json"
        review_manifest = _read_json(review_manifest_path)
        diagnostic_path = checkpoint_root / "rans_diagnostic.json"
        diagnostic = _read_json(diagnostic_path)
        execution_status = str(
            selected_state.get("execution_status") or "NOT_STARTED"
        )
        if execution_status == "DIVERGED":
            st.error("DIVERGED: existe evidencia numerica dura en el solver.")
        elif execution_status in {
            "TIMEOUT_PARTIAL", "USER_STOPPED_PARTIAL", "PARTIAL"
        }:
            st.warning(f"Ejecucion parcial: {execution_status}")
        elif execution_status == "COMPLETED":
            st.success("Ejecucion SIMPLE finalizada; revise el gate y la decision.")
        status_cols = st.columns(4)
        status_cols[0].metric("Ejecucion", execution_status)
        status_cols[1].metric(
            "Gate automatico",
            str(selected_review.get("automatic_gate") or "NOT_EVALUATED"),
        )
        status_cols[2].metric(
            "Revision",
            _review_label(str(selected_review.get("review_status") or "")),
        )
        status_cols[3].metric(
            "Iteracion absoluta",
            int(
                selected_state.get("absolute_simple_iteration")
                or selected_state.get("iterations")
                or 0
            ),
        )

        with st.expander(
            "Gráficas y métricas de convergencia",
            expanded=True,
        ):
            diagnostic_controls = st.columns(2)
            if diagnostic_controls[0].button(
                "Recalcular diagnostico escalar",
                key=f"validation-rans-diagnose-{rans_mesh}",
            ):
                start_job(
                    "validation_lab_analyze_checkpoint",
                    validation_study_command(
                        root,
                        "rans-review",
                        review_action="diagnose",
                        mesh_id=rans_mesh,
                    ),
                )
            diagnostic_controls[1].caption(
                "No ejecuta el solver ni genera campos volumetricos."
            )
            plot_names = (
                "rans_residuals.png",
                "rans_forces.png",
                "rans_moving_statistics.png",
                "rans_window_comparison.png",
                "rans_execution_cost.png",
            )
            available_plots = [
                checkpoint_root / name
                for name in plot_names
                if (checkpoint_root / name).is_file()
            ]
            if available_plots:
                columns = st.columns(2)
                for index, image in enumerate(available_plots):
                    columns[index % 2].image(
                        str(image), caption=image.name, width="stretch"
                    )
            else:
                st.info("Genere el diagnostico para obtener las graficas reales.")
            if diagnostic:
                summary_cols = st.columns(4)
                summary_cols[0].metric(
                    "Clasificacion", str(diagnostic.get("status") or "UNKNOWN")
                )
                summary_cols[1].metric(
                    "Fuerzas",
                    str(
                        (diagnostic.get("force_stationarity") or {}).get(
                            "status", "-"
                        )
                    ),
                )
                summary_cols[2].metric(
                    "Continuidad",
                    str((diagnostic.get("continuity") or {}).get("status", "-")),
                )
                history = dict(diagnostic.get("residual_history") or {})
                summary_cols[3].metric(
                    "Rango residual",
                    (
                        f"{history.get('iteration_start', '-')} - "
                        f"{history.get('iteration_end', '-')}"
                    ),
                )
                st.caption(
                    f"Segmentos: {history.get('segments_found', 0)} | "
                    f"Campos: {', '.join(history.get('fields_found') or []) or '-'} | "
                    f"Filas descartadas: {history.get('rows_discarded', 0)}"
                )
            timing = _read_json(checkpoint_root / "rans_execution_cost.json")
            if timing:
                timing_cols = st.columns(4)
                timing_cols[0].metric(
                    "Solver total [s]",
                    str(
                        selected_state.get("solver_active_total_seconds")
                        or timing.get("total_wall_seconds")
                        or "-"
                    ),
                )
                timing_cols[1].metric(
                    "Media [s/iter]",
                    str(selected_state.get("mean_solver_seconds_per_iteration") or "-"),
                )
                timing_cols[2].metric(
                    "Mediana [s/iter]",
                    str(selected_state.get("median_solver_seconds_per_iteration") or "-"),
                )
                timing_cols[3].metric(
                    "Tiempo 1-10000 [s]",
                    str(selected_state.get("time_first_10000_iterations") or "-"),
                )
            diagnostics_ready = (
                review_manifest.get("postprocess", {}).get("status") == "GENERATED"
            )
            review_confirm = st.checkbox(
                "Confirmo que he revisado las graficas y metricas",
                key=f"validation-rans-review-confirm-{rans_mesh}",
            )
            decision_cols = st.columns(3)
            decisions = (
                (
                    "Aceptar como estadisticamente estacionaria",
                    "accept-stationary",
                    "validation_rans_accept_stationary",
                ),
                (
                    "Aceptar solo para inicializacion URANS",
                    "accept-initialization",
                    "validation_rans_accept_initialization",
                ),
                ("Rechazar", "reject", "validation_rans_reject"),
            )
            for column, (label, action, stage_name) in zip(
                decision_cols, decisions
            ):
                if column.button(
                    label,
                    disabled=not diagnostics_ready or not review_confirm,
                    key=f"{stage_name}-{rans_mesh}",
                ):
                    start_job(
                        stage_name,
                        validation_study_command(
                            root,
                            "rans-review",
                            review_action=action,
                            mesh_id=rans_mesh,
                            reason=(
                                "Explicit Validation Lab decision after "
                                "reviewing scalar diagnostics."
                            ),
                        ),
                    )
            st.caption(
                "Contrato de campaña: un único bloque SIMPLE de 20 000 "
                "iteraciones. La revisión clasifica la evidencia, pero no "
                "amplía ni altera el checkpoint calculado."
            )
            secondary = st.columns(2)
            if secondary[0].button(
                "Crear checkpoint versionado",
                disabled=not bool(
                    review_manifest.get("allowed_uses", {}).get(
                        "urans_initialization"
                    )
                ),
                key=f"validation-rans-create-checkpoint-{rans_mesh}",
            ):
                start_job(
                    "validation_rans_create_reviewed_checkpoint",
                    validation_study_command(
                        root,
                        "rans-review",
                        review_action="create-checkpoint",
                        mesh_id=rans_mesh,
                    ),
                )
            if secondary[1].button(
                "Revocar aprobacion",
                disabled=not review_confirm,
                key=f"validation-rans-revoke-{rans_mesh}",
            ):
                start_job(
                    "validation_rans_revoke",
                    validation_study_command(
                        root,
                        "rans-review",
                        review_action="revoke",
                        mesh_id=rans_mesh,
                        reason="Explicit Validation Lab review revocation.",
                    ),
                )
            with st.expander("Detalles tecnicos", expanded=False):
                if diagnostic_path.is_file():
                    st.download_button(
                        "Descargar diagnostico JSON",
                        data=diagnostic_path.read_bytes(),
                        file_name=diagnostic_path.name,
                        mime="application/json",
                        key=f"validation-rans-download-{rans_mesh}",
                    )
                _json_panel(review_manifest_path, "Review manifest", inline=True)
                _json_panel(
                    checkpoint_root / "checkpoint_manifest.json",
                    "Checkpoint manifest",
                    inline=True,
                )

        with st.expander(
            "Postproceso completo y visualizaciones",
            expanded=False,
        ):
            rans_products = checkpoint_root / "rans_paraview_final"
            rans_full_post = checkpoint_root / "rans_postprocess"
            product_actions = st.columns(3)
            if product_actions[0].button(
                "Postproceso RANS rápido",
                key=f"validation-rans-final-visuals-{rans_mesh}",
                help="Genera escalares, diagnósticos y vistas finales sin recorrer iteraciones para animarlas.",
            ):
                start_job(
                    "validation_rans_full_postprocess",
                    [
                        sys.executable,
                        str(
                            root
                            / "CFD_2D/scripts/ramair_2d_rans_full_postprocess.py"
                        ),
                        "--project-root", str(root),
                        "--case", str(checkpoint_case),
                        "--output", str(rans_full_post),
                        "--variant", rans_base_mesh,
                        "--no-include-animations",
                    ],
                )
            if product_actions[1].button(
                "Generar animaciones RANS",
                key=f"validation-rans-animations-{rans_mesh}",
                help="Recorre únicamente las iteraciones guardadas y codifica U, Cp y contornos.",
            ):
                start_job(
                    "validation_rans_animations",
                    [
                        sys.executable,
                        str(
                            root
                            / "CFD_2D/scripts/ramair_2d_rans_full_postprocess.py"
                        ),
                        "--project-root",
                        str(root),
                        "--case",
                        str(checkpoint_case),
                        "--output",
                        str(rans_full_post),
                        "--variant",
                        rans_base_mesh,
                        "--include-animations",
                        "--animations-only",
                    ],
                )
            if product_actions[2].button(
                "Abrir estado final en ParaView",
                key=f"validation-rans-open-paraview-{rans_mesh}",
            ):
                try:
                    opened = open_paraview_case(root, checkpoint_case)
                    st.success(f"ParaView solicitado: {opened.get('status')}.")
                except Exception as exc:
                    st.error(str(exc))
            wall_actions = st.columns(2)
            if wall_actions[0].button(
                "Generar solo productos de pared",
                key=f"validation-rans-wall-post-{rans_mesh}",
            ):
                start_job(
                    "validation_rans_wall_postprocess",
                    [
                        sys.executable,
                        str(root / "CFD_2D/scripts/ramair_2d_rans_full_postprocess.py"),
                        "--project-root", str(root),
                        "--case", str(checkpoint_case),
                        "--output", str(rans_full_post),
                        "--variant", rans_base_mesh,
                        "--wall-only",
                    ],
                )
            if wall_actions[1].button(
                "Abrir carpeta de productos",
                key=f"validation-rans-open-products-{rans_mesh}",
            ):
                rans_full_post.mkdir(parents=True, exist_ok=True)
                open_local_folder(rans_full_post)
            manifest_candidates = (
                rans_full_post / "postprocess_manifest.json",
                rans_products / "postprocess_manifest.json",
                checkpoint_root / "postprocess_manifest.json",
            )
            product_manifest = next(
                (path for path in manifest_candidates if path.is_file()),
                manifest_candidates[0],
            )
            _postprocess_product_browser(
                product_manifest,
                start_job=start_job,
                key_scope=f"rans-{rans_mesh}",
                inline=True,
            )
            st.caption(
                "Courant: NOT_APPLICABLE_TO_RANS. Las animaciones RANS usan el numero de "
                "iteracion como eje temporal y se generan solo bajo demanda."
            )

    if section == "Análisis URANS":
        canonical_rows = _canonical_urans_rows(active)
        if not canonical_rows:
            st.info(
                "Todavía no hay casos URANS canónicos reales. La prueba rápida "
                "temporal y los estudios PIMPLE no aparecen en esta revisión."
            )
        else:
            topology_options = [value for value in ("closed", "open") if any(str(row.get("topology")) == value for row in canonical_rows)]
            topology = st.segmented_control(
                "Topología",
                topology_options,
                format_func=lambda value: "Perfil cerrado" if value == "closed" else "Perfil abierto",
                default=topology_options[0],
                key="validation-canonical-urans-topology",
            )
            topology_rows = [row for row in canonical_rows if str(row.get("topology")) == topology]
            levels = [value for value in MESH_LEVELS if any(str(row.get("mesh_level")) == value for row in topology_rows)]
            mesh_level = st.segmented_control(
                "Nivel de malla", levels, format_func=str.title,
                default=levels[0],
                key="validation-canonical-urans-level",
            ) if levels else None
            level_rows = [row for row in topology_rows if str(row.get("mesh_level")) == mesh_level]
            angles = sorted({float(row.get("alpha_deg") or 0.0) for row in level_rows})
            alpha_deg = st.segmented_control(
                "Ángulo de ataque", angles,
                format_func=lambda value: f"{value:g}°",
                default=angles[0],
                key="validation-canonical-urans-angle",
            ) if angles else None
            angle_rows = [
                row for row in level_rows
                if alpha_deg is not None and abs(float(row.get("alpha_deg") or 0.0) - float(alpha_deg)) < 1.0e-10
            ]
            dt_options = [str(row["case_id"]) for row in angle_rows]
            case_id = st.selectbox(
                "Paso temporal disponible",
                dt_options,
                format_func=lambda value: next(
                    f"dt={float(item.get('deltaT_s') or item.get('dt_s') or 0):.8g} s · {item.get('status')}"
                    for item in angle_rows if str(item["case_id"]) == value
                ),
                key="validation-canonical-urans-review-case",
                placeholder="No hay ejecuciones para esta combinación",
            ) if dt_options else None
            if case_id is None:
                st.info("No hay pasos temporales ejecutados para esta combinación.")
                return
            row = next(item for item in angle_rows if str(item["case_id"]) == case_id)
            case_path = Path(str(row["case_path"]))
            run_root = Path(str(row["run_root"]))
            mesh_id = str(row.get("mesh_id") or "")
            mesh = meshes.get(mesh_id, {})
            st.caption(
                "Vista de una única línea temporal canónica. Reanudar y reiniciar "
                "se gestionan desde Ejecución; esta sección no crea versiones."
            )
            try:
                static_snapshot = validation_monitor_snapshot(
                    case_path,
                    mode="URANS",
                    run_id=case_id,
                    topology=str(row.get("topology") or ""),
                    mesh_level=str(row.get("mesh_level") or ""),
                    cell_count=int(mesh.get("cell_count") or 0),
                    stage=str(row.get("stage") or ""),
                    tc_s=float(condition["tc_s"]),
                    steps_planned=None,
                    queue_position=None,
                    queue_total=None,
                    target_delta_t=float(row.get("deltaT_s") or 0.0),
                    phase_delta_t=(
                        float(row["phase_deltaT_s"])
                        if row.get("phase_deltaT_s") is not None
                        else None
                    ),
                )
                _monitor_charts(static_snapshot, key_scope="static-urans")
            except Exception as exc:
                st.info(f"Resumen estático no disponible: {exc}")

            interval_enabled = st.toggle(
                "Limitar las visualizaciones URANS a un intervalo físico",
                value=False,
                help="Reduce lectura y renderizado a la ventana de interés; no cambia la simulación ni la media de producción.",
                key=f"validation-urans-post-range-enabled-{case_id}",
            )
            interval_cols = st.columns(2)
            interval_start_s = interval_cols[0].number_input(
                "Inicio [s]", min_value=0.0, value=0.0, format="%.8g",
                disabled=not interval_enabled,
                key=f"validation-urans-post-range-start-{case_id}",
            )
            interval_end_default = float(
                row.get("end_time_s") or row.get("total_time_s") or 1.0
            )
            interval_end_s = interval_cols[1].number_input(
                "Final [s]", min_value=0.0, value=max(interval_end_default, 0.0), format="%.8g",
                disabled=not interval_enabled,
                key=f"validation-urans-post-range-end-{case_id}",
            )
            actions = st.columns(4)
            if actions[0].button(
                "Analizar resultado URANS",
                key=f"validation-analyze-urans-{case_id}",
            ):
                start_job(
                    "validation_lab_analyze_urans",
                    [
                        sys.executable,
                        str(root / "CFD_2D/scripts/ramair_2d_urans_review.py"),
                        "--run-root",
                        str(run_root),
                    ],
                )
            if actions[1].button(
                "Actualizar informe global",
                key=f"validation-report-urans-{case_id}",
            ):
                start_job(
                    "validation_lab_report",
                    validation_study_command(root, "report"),
                )
            if actions[2].button(
                "Postproceso URANS rápido",
                key=f"validation-postprocess-urans-{case_id}",
                help="Genera escalares, diagnósticos y vistas finales; difiere las animaciones.",
            ):
                start_job(
                    "validation_lab_postprocess_urans",
                    [
                        sys.executable,
                        str(root / "CFD_2D/scripts/ramair_2d_postprocess.py"),
                        "--case-root", str(root),
                        "--case-dir", str(case_path),
                        "--output-dir", str(run_root / "postprocess"),
                        "--variant", mesh_id,
                        "--alpha", str(config.get("study_angle_deg", 8.0)),
                        "--simulation-mode", "URANS",
                        "--no-include-rans-stage",
                        "--run-openfoam-postprocess",
                        "--automatic-paraview-products",
                        "--no-include-paraview-animations",
                        *(
                            ["--paraview-time-range-s", str(float(interval_start_s)), str(float(interval_end_s))]
                            if interval_enabled and float(interval_start_s) <= float(interval_end_s)
                            else []
                        ),
                    ],
                )
            if actions[3].button(
                "Generar animaciones",
                key=f"validation-animations-urans-{case_id}",
                help="Lee los tiempos guardados y genera solo las secuencias animadas.",
            ):
                start_job(
                    "validation_lab_animations_urans",
                    [
                        sys.executable,
                        str(root / "CFD_2D/scripts/ramair_2d_postprocess.py"),
                        "--case-root", str(root),
                        "--case-dir", str(case_path),
                        "--output-dir", str(run_root / "postprocess"),
                        "--variant", mesh_id,
                        "--alpha", str(config.get("study_angle_deg", 8.0)),
                        "--simulation-mode", "URANS",
                        "--no-include-rans-stage",
                        "--automatic-paraview-products",
                        "--paraview-animations-only",
                        *(
                            ["--paraview-time-range-s", str(float(interval_start_s)), str(float(interval_end_s))]
                            if interval_enabled and float(interval_start_s) <= float(interval_end_s)
                            else []
                        ),
                    ],
                )
            with st.expander("Gráficas y métricas de convergencia", expanded=True):
                _json_panel(run_root / "case_manifest.json", "Caso", inline=True)
                _json_panel(
                    run_root / "execution_summary.json",
                    "Ejecución",
                    inline=True,
                )
                _plot_inventory(run_root / "plots")
                review = _read_json(run_root / "review.json")
                if review:
                    review_cols = st.columns(4)
                    review_cols[0].metric("Estado", str(review.get("status") or "-"))
                    review_cols[1].metric("Fase analizada", str((review.get("sampling_window") or {}).get("stage") or "-"))
                    review_cols[2].metric("Continuidad", str((review.get("continuity") or {}).get("status") or "-"))
                    dt_data = review.get("time_step") or {}
                    review_cols[3].metric("dt [s]", str(dt_data.get("minimum_s") or "-"))
                    signal_rows = []
                    for signal_name, values in (review.get("signals") or {}).items():
                        summary = values.get("summary") or {}
                        stationarity = values.get("stationarity") or {}
                        spectrum = values.get("spectrum") or {}
                        signal_rows.append({
                            "Variable": signal_name,
                            "Media": summary.get("mean"),
                            "RMS": summary.get("rms"),
                            "Deriva media [%]": stationarity.get("mean_variation_percent"),
                            "Estacionaria": stationarity.get("passed"),
                            "f [Hz]": spectrum.get("dominant_frequency_hz"),
                            "St": spectrum.get("dominant_strouhal"),
                            "1/St": spectrum.get("dominant_wave_number"),
                        })
                    if signal_rows:
                        st.dataframe(signal_rows, hide_index=True, width="stretch")
                case_summary = _read_json(run_root / "case_summary.json")
                if case_summary.get("status") == "COMPLETED":
                    metrics_summary = dict(case_summary.get("metrics") or {})
                    modes_summary = dict(case_summary.get("dominant_modes") or {})
                    summary_rows = []
                    for metric_name in ("CL", "CD", "CM", "L_over_D"):
                        metric = dict(metrics_summary.get(metric_name) or {})
                        mode = dict(modes_summary.get(metric_name) or {})
                        summary_rows.append({
                            "Variable": metric_name.replace("L_over_D", "CL/CD"),
                            "Media producción": metric.get("mean"),
                            "RMS": metric.get("rms"),
                            "Desv. estándar": metric.get("std"),
                            "f dominante [Hz]": mode.get("frequency_hz"),
                            "St dominante": mode.get("strouhal"),
                            "W=1/St": mode.get("wave_number"),
                        })
                    st.dataframe(summary_rows, hide_index=True, width="stretch")
                with st.expander("Detalles técnicos", expanded=False):
                    _json_panel(run_root / "review.json", "Revisión URANS", inline=True)
                    _json_panel(run_root / "stage_plan.json", "Plan de fases", inline=True)
                    _json_panel(run_root / "stage_journal.json", "Transiciones registradas", inline=True)
            _postprocess_product_browser(
                run_root / "postprocess/postprocess_manifest.json",
                start_job=start_job,
                key_scope=f"urans-{case_id}",
            )

    if section == "Convergencia RANS":
        st.caption(
            "Comparacion espacial de resultados RANS aceptados para este uso. "
            "No se fabrican curvas cuando faltan mallas elegibles."
        )
        spatial_report = _read_json(active / "postprocess/reports/study_report.json")
        if spatial_report:
            rans_rows_preview = _records(
                active / "postprocess/reports/spatial_rans_comparison.csv"
            )
            included = [row.get("mesh_id") for row in rans_rows_preview if row.get("included_in_rans_mesh_convergence")]
            excluded = [
                f"{row.get('mesh_id')}: {row.get('review_status') or row.get('automatic_gate') or 'not eligible'}"
                for row in rans_rows_preview
                if not row.get("included_in_rans_mesh_convergence")
            ]
            st.caption(
                f"Last calculation: {spatial_report.get('generated_at', '-')}. "
                f"Included: {', '.join(str(value) for value in included) or '-'}"
            )
            if excluded:
                st.warning("Excluded from spatial comparison: " + "; ".join(excluded))
            products = spatial_report.get("rans_spatial_products") or []
            if products:
                st.caption(f"Generated spatial products: {len(products)}")
        if st.button(
            "Generar/actualizar análisis de convergencia espacial",
            key="validation-rans-spatial-update",
            type="primary",
        ):
            start_job(
                "validation_rans_spatial_convergence",
                validation_study_command(root, "report"),
            )
        rans = _records(
            active / "postprocess/reports/spatial_rans_comparison.csv"
        )
        closed_tab, open_tab = st.tabs(["Perfil cerrado", "Perfil abierto"])
        for spatial_view, tab in (("Closed", closed_tab), ("Open", open_tab)):
            topology = spatial_view.lower()
            selected_rows = [
                row
                for row in rans
                if str(
                    row.get("topology") or row.get("topologia") or ""
                ).lower()
                == topology
            ]
            with tab:
                if selected_rows:
                    for hidden in (
                        "registry_cell_count", "effective_h_2d",
                        "seconds_per_iteration", "automatic_gate", "review_status",
                    ):
                        for row in selected_rows:
                            row.pop(hidden, None)
                    angle_8, angle_16 = st.tabs(["8°", "16°"])
                    for alpha_deg, angle_tab in ((8.0, angle_8), (16.0, angle_16)):
                        with angle_tab:
                            angle_rows = [
                                row for row in selected_rows
                                if float(row.get("alpha_deg", -999.0)) == alpha_deg
                            ]
                            if angle_rows:
                                st.dataframe(angle_rows, hide_index=True, width="stretch")
                                _plot_inventory(
                                    active / "postprocess/spatial_rans" / topology
                                    / f"alpha_{int(alpha_deg)}"
                                )
                            else:
                                st.info(f"Aún no hay tres resultados elegibles a {alpha_deg:g}°.")
                else:
                    st.info(
                        f"No hay tres resultados RANS {topology} elegibles."
                    )

    if section == "Convergencia malla-tiempo":
        selected_topology = (
            "open"
            if top_section == "Convergencia espacio-tiempo"
            and subsection == "Abierto"
            else "closed"
        )
        st.markdown("### Campaña progresiva schema 11")
        st.caption(
            "Este plan solo escribe manifiestos pequeños: indexa casos ya "
            "ejecutados y bases RANS sin copiarlos ni lanzar OpenFOAM. La "
            "matriz completa de 18 combinaciones por ángulo queda como "
            "capacidad, no como ejecución automática."
        )
        campaign_engine = dict(config.get("campaign_engine") or {})
        topology_policy = dict(campaign_engine.get(selected_topology) or {})
        angle_order = list(topology_policy.get("angle_order_deg") or (
            [16.0, 8.0] if selected_topology == "closed" else [8.0, 16.0]
        ))
        default_strategy = str(topology_policy.get("default_strategy") or (
            "optimized" if selected_topology == "closed" else "progressive_medium_first"
        ))
        strategy_options = (
            ["optimized", "cummings", "full_capacity"]
            if selected_topology == "closed"
            else ["progressive_medium_first", "cummings", "full_capacity"]
        )
        campaign_columns = st.columns(2)
        strategy = campaign_columns[0].selectbox(
            "Estrategia",
            strategy_options,
            index=(
                strategy_options.index(default_strategy)
                if default_strategy in strategy_options else 0
            ),
            key=f"validation-campaign-strategy-{selected_topology}",
        )
        selected_angles = campaign_columns[1].multiselect(
            "Ángulos y orden científico",
            angle_order,
            default=angle_order,
            key=f"validation-campaign-angles-{selected_topology}",
        )
        if st.button(
            "Crear o actualizar el manifiesto de campaña",
            disabled=not selected_angles,
            key=f"validation-campaign-write-{selected_topology}",
        ):
            start_job(
                f"validation_campaign_{selected_topology}",
                validation_campaign_command(
                    root,
                    topology=selected_topology,
                    strategy=strategy,
                    angles_deg=selected_angles,
                    write=True,
                ),
            )
        campaign_id = (
            f"{selected_topology}_{strategy}_alpha_"
            + "_".join(
                ("m" if float(value) < 0 else "p")
                + f"{abs(float(value)):g}".replace(".", "p")
                for value in selected_angles
            )
        )
        _json_panel(
            active / "campaigns" / campaign_id / "campaign_manifest.json",
            "Manifiesto de campaña, dependencias y decisiones",
        )
        if st.button(
            "Actualizar comparación con ejecuciones URANS aceptadas",
            key="validation-space-time-update",
        ):
            start_job(
                "validation_space_time_convergence",
                [
                    sys.executable,
                    str(
                        root
                        / "CFD_2D/scripts/ramair_2d_space_time_convergence.py"
                    ),
                    "--project-root",
                    str(root),
                ],
            )
        _json_panel(
            active
            / "convergence/urans_space_time"
            / selected_topology
            / "space_time_report.json",
            f"Informe espacio-tiempo {selected_topology}",
        )
        comparison = _records(
            active / "postprocess/reports/spatial_temporal_comparison.csv"
        )
        if comparison:
            st.dataframe(comparison, hide_index=True, width="stretch")
        else:
            st.info("No hay resultados URANS reales suficientes.")
        _plot_inventory(active / "postprocess/spatial_temporal_urans")
        if subsection == "Coste y precisión":
            _json_panel(
                active / "reports/performance_hardware_audit.json",
                "Auditoría CPU / MPI / GPU con numerics fijos",
                inline=True,
            )

    if section == "Frecuencias":
        st.caption(
            "Welch usa ventana Hann, detrend constante y 50% de solape "
            "solo sobre el muestreo uniforme. Startup y settling se excluyen."
        )
        closed_frequency, open_frequency, combined_frequency = st.tabs([
            "Perfil cerrado", "Perfil abierto", "Comparación abierta/cerrada",
        ])
        with closed_frequency:
            _plot_inventory(active / "postprocess/frequency/closed")
        with open_frequency:
            _plot_inventory(active / "postprocess/frequency/open")
        with combined_frequency:
            _plot_inventory(active / "postprocess/frequency/combined")
        st.info(
            "Una frecuencia se acepta con estacionariedad y al menos 10 ciclos "
            "de la frecuencia minima relevante; se prefieren 20."
        )

    if section == "Courant":
        st.caption(
            "Diagnóstico exclusivo de URANS. Para RANS/SIMPLE se registra "
            "`NOT_APPLICABLE_TO_RANS`."
        )
        courant = _records(
            active / "postprocess/reports/courant_comparison.csv"
        )
        if courant:
            st.dataframe(courant, hide_index=True, width="stretch")
        else:
            st.info("Todavia no hay diagnosticos Courant agregados.")
        _plot_inventory(active / "postprocess/courant")
        st.warning(
            "Co_max>1 no rechaza por si solo una ejecucion. Se evalua junto "
            "con convergencia iterativa, acotacion y sensibilidad a deltaT."
        )

    if section == "Sensibilidad PIMPLE":
        st.markdown(
            "Estudio ejecutable de **2, 3 y 4 correctores externos PIMPLE** "
            "sobre la topología, malla y deltaT seleccionados, con el mismo "
            "checkpoint, condición física y duración. No requiere prueba rápida."
        )
        pimple_selectors = st.columns(3)
        pimple_topology = pimple_selectors[0].selectbox(
            "Topología PIMPLE", ["closed", "open"], key="validation-pimple-topology"
        )
        pimple_mesh_level = pimple_selectors[1].selectbox(
            "Malla PIMPLE", list(MESH_LEVELS), key="validation-pimple-mesh-level"
        )
        pimple_rows = [
            row for row in runs
            if str(row.get("topology")) == str(pimple_topology)
            and str(row.get("mesh_level")) == str(pimple_mesh_level)
        ]
        pimple_dt_values = sorted(
            {float(row["dt_s"]) for row in pimple_rows}, reverse=True
        )
        pimple_dt = pimple_selectors[2].selectbox(
            "deltaT del estudio [s]",
            pimple_dt_values,
            format_func=lambda value: f"{float(value):.8g} s",
            key="validation-pimple-dt",
        )
        pimple_confirm = st.checkbox(
            "Confirmo la ejecucion real del estudio 2/3/4",
            key="validation-pimple-confirm",
        )
        pimple_actions = st.columns(4)
        if pimple_actions[0].button("Preparar y verificar 2/3/4"):
            start_job(
                "validation_pimple_prepare",
                validation_study_command(
                    root,
                    "pimple-study",
                    study_action="prepare",
                    topology=str(pimple_topology),
                    mesh_level=str(pimple_mesh_level),
                    dt_s=pimple_dt,
                ),
            )
        if pimple_actions[1].button(
            "Ejecutar 2/3/4",
            disabled=not pimple_confirm,
        ):
            start_job(
                "validation_pimple_execute",
                validation_study_command(
                    root,
                    "pimple-study",
                    study_action="execute",
                    run=True,
                ),
            )
        if pimple_actions[2].button("Analizar 2/3/4"):
            start_job(
                "validation_pimple_analyze",
                validation_study_command(
                    root,
                    "pimple-study",
                    study_action="analyze",
                ),
            )
        if pimple_actions[3].button(
            "Reanudar incompletos",
            disabled=not pimple_confirm,
        ):
            start_job(
                "validation_pimple_resume",
                validation_study_command(
                    root,
                    "pimple-study",
                    study_action="resume",
                    run=True,
                ),
            )
        _json_panel(
            active
            / "pimple_outer_study"
            / "pimple_outer_study_manifest.json",
            "Estado del estudio PIMPLE",
        )
        _json_panel(
            active
            / "postprocess/pimple"
            / "pimple_outer_2_3_4_comparison.json",
            "Comparacion PIMPLE",
        )
        _plot_inventory(active / "postprocess/pimple")

    if section == "Informes":
        report_actions = st.columns(2)
        if report_actions[0].button(
            "Generar informe reproducible",
            type="primary",
        ):
            start_job(
                "validation_lab_report",
                validation_study_command(root, "report"),
            )
        if report_actions[1].button("Actualizar inventario de almacenamiento"):
            start_job(
                "validation_lab_storage_inventory",
                validation_study_command(root, "storage-inventory"),
            )
        report_root = active / "postprocess/reports"
        st.markdown("### Prueba acotada de integracion")
        st.caption(
            "closed_coarse: 100 iteraciones SIMPLE parciales y 40 pasos "
            "URANS. Es una verificacion de software, no un resultado aerodinamico."
        )
        smoke_confirm = st.checkbox(
            "Confirmo la prueba real acotada",
            key="validation-smoke-confirm",
        )
        smoke_actions = st.columns(2)
        if smoke_actions[0].button("Preparar prueba acotada"):
            start_job(
                "validation_smoke_prepare",
                validation_smoke_command(root),
            )
        if smoke_actions[1].button(
            "Ejecutar prueba acotada",
            disabled=not smoke_confirm,
        ):
            start_job(
                "validation_smoke_execute",
                validation_smoke_command(root, run=True),
            )
        _json_panel(
            report_root / "closed_coarse_bounded_smoke.json",
            "Resultado de integracion acotada",
        )
        inventory = _read_json(report_root / "storage_inventory.json")
        if inventory:
            inventory_metrics = st.columns(5)
            inventory_metrics[0].metric(
                "Workspace activo",
                f"{inventory.get('total_bytes', 0) / 1073741824:.2f} GB",
            )
            inventory_metrics[1].metric(
                "Estados temporales",
                inventory.get("snapshot_directories", 0),
            )
            inventory_metrics[2].metric(
                "VTK",
                f"{inventory.get('vtk_bytes', 0) / 1073741824:.2f} GB",
            )
            inventory_metrics[3].metric(
                "Animaciones",
                inventory.get("animation_files", 0),
            )
            inventory_metrics[4].metric(
                "Directorios processor",
                inventory.get("processor_directories", 0),
            )
            st.dataframe(
                inventory.get("folder_bytes") or [],
                hide_index=True,
                width="stretch",
            )
            with st.expander("Archivos mas grandes"):
                st.dataframe(
                    inventory.get("top_files") or [],
                    hide_index=True,
                    width="stretch",
                )
        st.markdown("### Limpieza acotada")
        st.warning(
            "La limpieza solo actua sobre productos volumetricos regenerables "
            "del workspace activo. No borra ni modifica la carpeta Results."
        )
        cleanup_confirm = st.checkbox(
            "Confirmo la limpieza del workspace activo",
            key="validation-storage-cleanup-confirm",
        )
        if st.button(
            "Eliminar productos volumetricos regenerables",
            disabled=not cleanup_confirm,
            key="validation-storage-cleanup",
        ):
            start_job(
                "validation_lab_storage_cleanup",
                validation_study_command(
                    root,
                    "storage-cleanup",
                    confirm=True,
                ),
            )
        _json_panel(report_root / "storage_cleanup_last.json", "Ultima limpieza")
        _json_panel(report_root / "study_report.json", "Informe machine-readable")
        if (report_root / "study_report.md").is_file():
            st.markdown(
                (report_root / "study_report.md").read_text(encoding="utf-8")
            )
        st.caption(f"Exports: {active / 'exports'}")
        st.caption(f"Results protegido: {result}")
        st.info(
            "Los CSV solo se crean cuando existen registros reales. Ningun "
            "fixture o dato sintetico se publica como validacion."
        )


def render_validation_convergence_lab(root: Path, start_job: StartJob) -> None:
    validation_tab, convergence_tab = st.tabs(["Validación", "Convergencia"])
    with validation_tab:
        render_ls1_validation(root, start_job)
    with convergence_tab:
        render_convergence_lab(root, start_job)
        with st.expander("Paquete portátil de casos de convergencia", expanded=False):
            candidates = sorted(
                path.parent
                for path in (root / "CFD_2D/validation_studies").rglob("system/controlDict")
                if not any(part.startswith("processor") for part in path.parts)
            )
            labels = {str(path.relative_to(root)): path for path in candidates}
            selected_labels = st.multiselect(
                "Casos preparados",
                list(labels),
                key="convergence-remote-cases",
                help="Se empaquetan los diccionarios, la malla y los checkpoints exactamente como están preparados.",
            )
            mode = st.selectbox(
                "Secuencia remota",
                ["rans_only", "transient_only", "rans_urans"],
                format_func=lambda value: {
                    "rans_only": "Solo RANS (conservar checkpoint)",
                    "transient_only": "Solo URANS desde checkpoint",
                    "rans_urans": "RANS seguido de URANS",
                }[value],
                key="convergence-remote-mode",
            )
            remote_cores = st.number_input(
                "Procesos solicitados",
                min_value=1,
                max_value=128,
                value=8,
                key="convergence-remote-cores",
                help="El optimizador puede sustituir este valor al reconocer por primera vez la malla.",
            )
            if st.button(
                "Generar paquete de convergencia",
                disabled=not selected_labels,
                key="convergence-remote-create",
            ):
                command = [
                    sys.executable,
                    str(root / "Application Support/Tools/package_ramair_remote_execution.py"),
                    "--project-root", str(root),
                    "--package-scope", "convergence",
                    "--execution-mode", mode,
                    "--n-cores", str(int(remote_cores)),
                    "--case-timeout-min", "360",
                    "--automatic-core-selection",
                    "--renumber-before-decompose",
                ]
                for label in selected_labels:
                    command += ["--case", str(labels[label])]
                start_job("convergence_remote_package", command)
            uploaded_return = st.file_uploader(
                "Cargar retorno de convergencia",
                type=["zip"],
                key="convergence-remote-return",
            )
            if st.button(
                "Verificar e importar retorno",
                disabled=uploaded_return is None,
                key="convergence-remote-import",
            ):
                upload_root = root / "CFD_2D/app_state/remote_uploads"
                upload_root.mkdir(parents=True, exist_ok=True)
                upload_path = upload_root / f"{int(time.time())}_{Path(uploaded_return.name).name}"
                upload_path.write_bytes(uploaded_return.getvalue())
                start_job(
                    "convergence_remote_import",
                    [
                        sys.executable,
                        str(root / "Application Support/Tools/import_ramair_remote_results.py"),
                        "--project-root", str(root),
                        "--archive", str(upload_path),
                        "--existing-action", "archive",
                    ],
                )
