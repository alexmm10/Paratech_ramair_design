"""Streamlit page for the isolated Validation & Convergence Lab."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

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
    validation_urans_case_snapshot,
    validation_urans_queue_command,
    validation_smoke_command,
    validation_study_command,
    validation_study_snapshot,
)


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
        for image in sorted(folder.glob("*.png"))
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
    """Render products lazily using only paths declared by the manifest."""
    labels = {
        "scalar_histories": "Scalar histories",
        "statistics_convergence": "Convergence / statistics",
        "surface_plots": "Surface distributions",
        "field_images": "Field images",
        "animations": "Animations",
        "paraview": "ParaView products",
        "technical_files": "Technical logs and manifests",
        "errors": "Errors and missing products",
    }
    def render_browser() -> None:
        manifest = _read_json(manifest_path)
        if not manifest:
            st.info(f"No postprocess manifest is available: {manifest_path}")
            return
        raw_groups = dict(manifest.get("groups") or {})
        if not raw_groups:
            for row in manifest.get("products") or []:
                raw_groups.setdefault(
                    str(row.get("group") or "technical_files"), []
                ).append(row)
        raw_groups["errors"] = [
            {"name": str(error), "generation_status": "ERROR"}
            for error in manifest.get("errors") or []
        ]
        available_groups = [
            group for group in labels if raw_groups.get(group)
        ] or ["technical_files"]
        selected_group = st.selectbox(
            "Product group",
            available_groups,
            format_func=lambda value: labels[value],
            key=f"postprocess-browser-group-{key_scope}",
        )
        rows = list(raw_groups.get(selected_group) or [])
        def product_path(row: dict[str, Any]) -> Path:
            value = Path(str(row.get("path") or ""))
            if value.is_absolute() or int(manifest.get("schema_version") or 0) < 3:
                return value
            return (manifest_path.parent / value).resolve()
        st.dataframe(
            [
                {
                    "product": row.get("name"),
                    "status": row.get("generation_status", "AVAILABLE"),
                    "path": row.get("path"),
                    "bytes": row.get("bytes"),
                    "modified": row.get("modified_at_epoch_s"),
                }
                for row in rows
            ],
            hide_index=True,
            width="stretch",
        )
        controls = st.columns(2)
        if controls[0].button(
            "Open product folder",
            key=f"postprocess-browser-open-{key_scope}-{selected_group}",
        ):
            try:
                open_local_folder(manifest_path.parent)
            except Exception as exc:
                st.error(str(exc))
        command = (manifest.get("regeneration_commands") or {}).get(
            selected_group
        )
        if controls[1].button(
            "Regenerate selected group",
            disabled=not command,
            key=f"postprocess-browser-regenerate-{key_scope}-{selected_group}",
            help=(
                "Enabled only when the backend recorded an auditable command "
                "for this product group in postprocess_manifest.json."
            ),
        ):
            start_job(
                f"postprocess_regenerate_{key_scope}_{selected_group}",
                [str(value) for value in command],
            )
        image_rows = [
            row
            for row in rows
            if product_path(row).suffix.lower()
            in {".png", ".jpg", ".jpeg"}
        ]
        if image_rows and st.checkbox(
            "Load image previews",
            value=False,
            key=f"postprocess-browser-preview-{key_scope}-{selected_group}",
            help="Images are not read until this option is enabled.",
        ):
            columns = st.columns(2)
            for index, row in enumerate(image_rows):
                path = product_path(row)
                if path.is_file():
                    columns[index % 2].image(
                        str(path),
                        caption=str(row.get("name") or path.name),
                        width="stretch",
                    )
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
    metrics = st.columns(5)
    metrics[0].metric("Estado", str(snapshot.get("status", "UNKNOWN")))
    metrics[1].metric(
        "Iteracion / tiempo",
        str(snapshot.get("iteration_or_time") or "-"),
    )
    metrics[2].metric("Pasos observados", int(snapshot.get("steps_observed") or 0))
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
    full_history = st.toggle(
        "Mostrar todo el historial escalar disponible",
        value=False,
        key=f"validation-monitor-full-history-{key_scope}-{run_id}",
        help=(
            "Desactivado usa la ventana reciente para evitar que los valores "
            "iniciales oculten la evolución final."
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


def _postprocess_scale_controls(
    root: Path,
    config: dict[str, Any],
    *,
    key_scope: str,
) -> dict[str, Any]:
    settings = dict(config.get("postprocess") or {})
    settings.setdefault("static_scale_mode", "exact")
    settings.setdefault("animation_scale_mode", "global_exact")
    settings.setdefault("robust_percentiles", [1.0, 99.0])
    settings.setdefault(
        "manual_scales",
        {"Cp": [-3.0, 1.5], "U": [0.0, 1.5]},
    )
    st.markdown("#### Escalas de campo")
    st.caption(
        "La escala exacta usa los extremos finitos reales. La robusta evita "
        "que unos pocos outliers saturen el mapa; la manual conserva un rango "
        "fijo. Las animaciones usan una unica escala global."
    )
    labels = {
        "Exact min/max": "exact",
        "Robust 1-99 percentile": "robust",
        "Manual": "manual",
    }
    inverse = {value: label for label, value in labels.items()}
    columns = st.columns(2)
    static_mode = str(settings.get("static_scale_mode") or "exact")
    static_label = columns[0].selectbox(
        "Imagen estatica",
        list(labels),
        index=list(labels).index(inverse.get(static_mode, "Exact min/max")),
        key=f"validation-scale-static-{key_scope}",
    )
    animation_mode = str(
        settings.get("animation_scale_mode") or "global_exact"
    ).removeprefix("global_")
    animation_label = columns[1].selectbox(
        "Animacion (global)",
        list(labels),
        index=list(labels).index(
            inverse.get(animation_mode, "Exact min/max")
        ),
        key=f"validation-scale-animation-{key_scope}",
    )
    settings["static_scale_mode"] = labels[static_label]
    settings["animation_scale_mode"] = (
        f"global_{labels[animation_label]}"
    )
    percentile_columns = st.columns(2)
    percentiles = list(settings.get("robust_percentiles") or [1.0, 99.0])
    low = percentile_columns[0].number_input(
        "Percentil inferior",
        min_value=0.0,
        max_value=99.0,
        value=float(percentiles[0]),
        key=f"validation-scale-low-{key_scope}",
    )
    high = percentile_columns[1].number_input(
        "Percentil superior",
        min_value=1.0,
        max_value=100.0,
        value=float(percentiles[1]),
        key=f"validation-scale-high-{key_scope}",
    )
    if low >= high:
        st.error("El percentil inferior debe ser menor que el superior.")
    else:
        settings["robust_percentiles"] = [float(low), float(high)]
    manual = dict(settings.get("manual_scales") or {})
    manual_columns = st.columns(4)
    cp_bounds = list(manual.get("Cp") or [-3.0, 1.5])
    u_bounds = list(manual.get("U") or [0.0, 1.5])
    manual["Cp"] = [
        manual_columns[0].number_input(
            "Cp min manual",
            value=float(cp_bounds[0]),
            key=f"validation-scale-cp-min-{key_scope}",
        ),
        manual_columns[1].number_input(
            "Cp max manual",
            value=float(cp_bounds[1]),
            key=f"validation-scale-cp-max-{key_scope}",
        ),
    ]
    manual["U"] = [
        manual_columns[2].number_input(
            "|U| min manual",
            value=float(u_bounds[0]),
            key=f"validation-scale-u-min-{key_scope}",
        ),
        manual_columns[3].number_input(
            "|U| max manual",
            value=float(u_bounds[1]),
            key=f"validation-scale-u-max-{key_scope}",
        ),
    ]
    settings["manual_scales"] = manual
    config["postprocess"] = settings
    if st.button(
        "Guardar escalas de postproceso",
        key=f"validation-save-scales-{key_scope}",
    ):
        _save_strategy(root, config)
    return settings


def _paraview_scale_args(
    settings: dict[str, Any],
    *,
    animation: bool,
) -> list[str]:
    key = "animation_scale_mode" if animation else "static_scale_mode"
    mode = str(settings.get(key) or "exact").removeprefix("global_")
    arguments = ["--field-scale-mode", mode]
    percentiles = list(settings.get("robust_percentiles") or [1.0, 99.0])
    arguments.extend(
        ["--robust-percentiles", str(percentiles[0]), str(percentiles[1])]
    )
    manual = dict(settings.get("manual_scales") or {})
    for flag, name in (
        ("--manual-cp-range", "Cp"),
        ("--manual-u-range", "U"),
    ):
        bounds = list(manual.get(name) or [])
        if len(bounds) == 2:
            arguments.extend([flag, str(bounds[0]), str(bounds[1])])
    return arguments


def render_validation_convergence_lab(root: Path, start_job: StartJob) -> None:
    st.info(
        "Laboratorio de convergencia espacial y temporal para LS(1)-0417 cerrado "
        "y Ram-Air abierto. Condicion comun: alpha=8 deg, M=0.15 y Re=1.9e6. "
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
        "Solver y estrategia": ["Recursos", "RANS / SIMPLE", "URANS / PIMPLE"],
        "RANS": [
            "Ejecución",
            "Verificación y decisión",
            "Postproceso completo",
            "Convergencia espacial",
        ],
        "URANS": [
            "Ejecución",
            "Revisión",
            "Postproceso",
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
        ("Solver y estrategia", "Recursos"): "Solver y estrategia",
        ("Solver y estrategia", "RANS / SIMPLE"): "Solver y estrategia",
        ("Solver y estrategia", "URANS / PIMPLE"): "Solver y estrategia",
        ("RANS", "Ejecución"): "Solver y estrategia",
        ("RANS", "Verificación y decisión"): "Análisis RANS",
        ("RANS", "Postproceso completo"): "Análisis RANS",
        ("RANS", "Convergencia espacial"): "Convergencia RANS",
        ("URANS", "Ejecución"): "Matriz URANS",
        ("URANS", "Revisión"): "Análisis URANS",
        ("URANS", "Postproceso"): "Análisis URANS",
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
        "Caso M0.15 | Re1.9e6 | c1m | alpha8"
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

    postprocess_scale_settings = dict(config.get("postprocess") or {})
    if (
        top_section == "RANS"
        and subsection == "Postproceso completo"
    ) or (
        top_section == "URANS"
        and subsection == "Postproceso"
    ):
        postprocess_scale_settings = _postprocess_scale_controls(
            root,
            config,
            key_scope=top_section.lower(),
        )

    if section == "Mallas y condiciones":
        st.markdown(
            "Seleccione un **conjunto coherente de simulacion**. Cada conjunto "
            "vincula geometria, condiciones de operacion y una malla concreta "
            "mediante hashes; cargarlo no modifica los paquetes historicos."
        )
        mesh_rows = [{
            "Topologia": row["topology"],
            "Nivel": row["level"],
            "Celdas": row["cell_count"],
            "Calidad": (
                f"{row['grade']} / {row['checkMesh_status']}"
            ),
            "Estado RANS": (
                reviews.get(row["id"], {}).get("automatic_gate")
                or reviews.get(row["id"], {}).get("review_status")
                or _selected_checkpoint(
                    checkpoint_rows,
                    row["id"],
                ).get("status")
            ),
            "Accion": "Cargar / revisar",
        } for row in meshes.values()]
        st.dataframe(mesh_rows, width="stretch", hide_index=True)
        current_mesh = str(
            snapshot.get("active_selection", {}).get("mesh_id")
            or mesh_ids[0]
        )
        selected_mesh = st.selectbox(
            "Conjunto coherente de simulacion",
            mesh_ids,
            index=mesh_ids.index(current_mesh) if current_mesh in mesh_ids else 0,
            key="validation-lab-mesh",
            help=(
                "Geometria + condiciones de operacion + malla. Los hashes "
                "impiden reutilizar un checkpoint incompatible."
            ),
        )
        st.caption("Geometria + condiciones de operacion + malla")
        actions = st.columns(4)
        if actions[0].button(
            "Cargar conjunto seleccionado en el laboratorio",
            key="validation-lab-restore-mesh",
        ):
            start_job(
                "validation_lab_select_mesh",
                validation_study_command(
                    root,
                    "select-mesh",
                    mesh_id=selected_mesh,
                ),
            )
        if actions[1].button(
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
        show_quality = actions[2].button(
            "Ver calidad",
            key="validation-lab-show-quality",
        )
        if actions[3].button(
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
        cols[3].metric("Angulo", "8 deg")
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

    if section == "Solver y estrategia":
        st.markdown("### Estado base RANS / SIMPLE")
        st.caption(
            "La cola muestra las seis bases canónicas y omite sin ocultar las ya aceptadas. "
            "SIMPLE usa por defecto nNonOrthogonalCorrectors=0; la cola guarda "
            "U, p, nuTilda y phi cuando OpenFOAM lo escribe."
        )
        rans_config[
            "minimum_simple_iterations_before_convergence_check"
        ] = 10000
        rans_config["native_residual_control_enabled"] = False
        contract_cols = st.columns(2)
        contract_cols[0].metric(
            "Primera evaluacion de convergencia",
            "SIMPLE 10 000",
        )
        contract_cols[1].metric(
            "Parada residual nativa de OpenFOAM",
            "Desactivada",
        )
        st.caption(
            "Antes de 10 000 solo pueden detener la base una solicitud explicita, "
            "un timeout, divergencia o un fallo de ejecucion. El gate estadistico "
            "externo se evalua unicamente en objetivos absolutos."
        )
        iteration_cols = st.columns(3)
        rans_config["initial_iterations"] = iteration_cols[0].number_input(
            "Iteraciones iniciales",
            value=10000,
            disabled=True,
            help="Objetivo absoluto inicial congelado para este batch.",
        )
        rans_config["extension_iterations"] = iteration_cols[1].number_input(
            "Iteraciones por extension",
            value=2500,
            disabled=True,
            help="Extensiones absolutas: 12500, 15000, 17500 y 20000.",
        )
        rans_config["maximum_iterations"] = iteration_cols[2].number_input(
            "Limite total de iteraciones",
            value=20000,
            disabled=True,
            help="En 20000 se detiene para revision si el gate no acepta.",
        )
        simple_cols = st.columns(4)
        rans_config["simple_non_orthogonal_correctors"] = simple_cols[0].number_input(
            "Correctores no ortogonales SIMPLE",
            min_value=0,
            max_value=4,
            value=int(rans_config["simple_non_orthogonal_correctors"]),
            help=(
                "Para estas mallas checkMesh-OK se usa 0 como base RANS. "
                "Es independiente del valor PIMPLE."
            ),
        )
        rans_config["mpi_ranks"] = simple_cols[1].number_input(
            "Procesos MPI RANS",
            min_value=1,
            max_value=8,
            value=int(rans_config["mpi_ranks"]),
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
        policy_cols = st.columns(3)
        rans_config["allow_early_stop"] = policy_cols[0].toggle(
            "Permitir parada temprana",
            value=bool(rans_config["allow_early_stop"]),
            help=(
                "Desactivado por defecto: se completan los bloques configurados "
                "aunque el criterio se satisfaga antes."
            ),
        )
        rans_config["continue_queue_after_nonconvergence"] = policy_cols[1].toggle(
            "Continuar tras no convergencia",
            value=bool(rans_config["continue_queue_after_nonconvergence"]),
        )
        rans_config["continue_on_nonfatal_failure"] = policy_cols[2].toggle(
            "Continuar tras fallo no fatal",
            value=bool(rans_config["continue_on_nonfatal_failure"]),
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
            value=int(urans_config["pimple_non_orthogonal_correctors"]),
            help="Valor URANS independiente del corrector SIMPLE.",
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
        st.markdown("#### Etapas iniciales URANS")
        startup_frame = pd.DataFrame(
            urans_config.get("startup_stages") or []
        )
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
        urans_config["startup_stages"] = startup_editor.to_dict(
            orient="records"
        )
        runtime_cols = st.columns(3)
        validation["mpi_ranks"] = runtime_cols[0].number_input(
            "Procesos MPI URANS",
            min_value=1,
            max_value=8,
            value=int(validation["mpi_ranks"]),
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
        if frozen_batch.get("config_hash"):
            st.info(
                "Existe una configuracion de batch congelada. Las reanudaciones "
                "usan esa configuracion original; los widgets actuales no la "
                "sobrescriben. Guarde una nueva revision solo para futuras bases."
            )
            with st.expander("Configuracion efectiva congelada"):
                st.dataframe([
                    {
                        "Topologia": "closed",
                        "Iteraciones iniciales": frozen_batch.get(
                            "closed_effective", {}
                        ).get("initial_iterations"),
                        "Extension": frozen_batch.get(
                            "closed_effective", {}
                        ).get("extension_iterations"),
                        "Maximo": frozen_batch.get(
                            "closed_effective", {}
                        ).get("maximum_iterations"),
                        "MPI": frozen_batch.get("closed_effective", {}).get(
                            "mpi_ranks"
                        ),
                    },
                    {
                        "Topologia": "open",
                        "Iteraciones iniciales": frozen_batch.get(
                            "open_effective", {}
                        ).get("initial_iterations"),
                        "Extension": frozen_batch.get(
                            "open_effective", {}
                        ).get("extension_iterations"),
                        "Maximo": frozen_batch.get(
                            "open_effective", {}
                        ).get("maximum_iterations"),
                        "MPI": frozen_batch.get("open_effective", {}).get(
                            "mpi_ranks"
                        ),
                    },
                ], hide_index=True, width="stretch")
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
                    state.get("block_target_iteration") or 10000
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
            "Se continúa desde la primera base incompleta. El gate solo se "
            "evalúa en 10000, 12500, 15000, 17500 o 20000 iteraciones."
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
            ["reference", "frequency", "manual"],
            default=active_package if active_package in {"reference", "frequency", "manual"} else "reference",
            format_func=lambda value: {
                "reference": "Reference",
                "frequency": "Frequency",
                "manual": "Manual",
            }[value],
            key="canonical-urans-temporal-package",
        )
        package_help = {
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
            selector_columns = st.columns(3)
            topology = selector_columns[0].selectbox(
                "Topología", ["closed", "open"], key="canonical-urans-topology"
            )
            topology_meshes = [
                mesh_id for mesh_id in mesh_ids if mesh_id.startswith(f"{topology}_")
            ]
            mesh_id = selector_columns[1].selectbox(
                "Malla", topology_meshes, key="canonical-urans-mesh"
            )
            available_rows = [row for row in runs if str(row.get("mesh_id")) == mesh_id]
            dt_values = sorted(
                {float(row.get("dt_s")) for row in available_rows}, reverse=True
            )
            selected_dt = selector_columns[2].selectbox(
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
            queue_labels = {
                str(row["run_id"]): (
                    f"{row['topology']} | {row['mesh_level']} | "
                    f"deltaT={float(row['dt_s']):.6g} s"
                )
                for row in runs
            }
            queue_selection = st.multiselect(
                "Casos de la cola",
                list(queue_labels),
                default=[],
                format_func=lambda value: queue_labels[value],
                max_selections=18,
                key="canonical-urans-queue-selection",
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
        batch_acceptance_path = (
            active / "reports/rans_six_base_batch_acceptance_20260804.json"
        )
        with st.expander("Aprobación administrativa de las seis bases", expanded=False):
            st.warning(
                "Esta acción no altera el gate automático. Verifica campos reales, "
                "identidad y hashes antes de habilitar cada base para convergencia "
                "espacial e inicialización URANS."
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
        rans_mesh = st.selectbox(
            "Ejecucion RANS a revisar",
            mesh_ids,
            key="validation-rans-review-execution",
        )
        selected_state = _selected_checkpoint(checkpoint_rows, rans_mesh)
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
            current_iteration = int(
                selected_state.get("absolute_simple_iteration")
                or selected_state.get("iterations")
                or 0
            )
            new_target = current_iteration + 2500
            rate = selected_state.get("median_solver_seconds_per_iteration")
            extension_cols = st.columns(4)
            extension_cols[0].metric("Iteracion actual", current_iteration)
            extension_cols[1].metric("Nuevo objetivo", new_target)
            extension_cols[2].metric(
                "Coste medido estimado",
                (
                    f"{float(rate) * 2500.0 / 60.0:.1f} min"
                    if rate is not None
                    else "No disponible"
                ),
            )
            extend_confirm = extension_cols[3].checkbox(
                "Confirmar +2500",
                key=f"validation-rans-extend-confirm-{rans_mesh}",
            )
            if st.button(
                "Extender 2 500 iteraciones",
                disabled=not extend_confirm,
                key=f"validation-rans-extend-{rans_mesh}",
            ):
                start_job(
                    "validation_rans_extend_review",
                    validation_study_command(
                        root,
                        "rans-base",
                        mesh_id=rans_mesh,
                        run=True,
                        manual_extension_iterations=2500,
                    ),
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
                "Generar campos RANS finales",
                key=f"validation-rans-final-visuals-{rans_mesh}",
            ):
                start_job(
                    "validation_rans_final_paraview_products",
                    [
                        sys.executable,
                        str(
                            root
                            / "CFD_2D/scripts/ramair_2d_rans_paraview_final.py"
                        ),
                        "--case",
                        str(checkpoint_case),
                        "--output",
                        str(rans_products),
                        *_paraview_scale_args(
                            postprocess_scale_settings, animation=False
                        ),
                    ],
                )
            if product_actions[1].button(
                "Abrir estado final en ParaView",
                key=f"validation-rans-open-paraview-{rans_mesh}",
            ):
                try:
                    opened = open_paraview_case(root, checkpoint_case)
                    st.success(f"ParaView solicitado: {opened.get('status')}.")
                except Exception as exc:
                    st.error(str(exc))
            if product_actions[2].button(
                "Generar postproceso completo",
                key=f"validation-rans-full-post-{rans_mesh}",
            ):
                start_job(
                    "validation_rans_full_postprocess",
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
                        rans_mesh,
                    ],
                )
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
                        "--variant", rans_mesh,
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
                "Courant: NOT_APPLICABLE_TO_RANS. No se crean animaciones "
                "automaticas para una base SIMPLE estacionaria."
            )

    if section == "Análisis URANS":
        canonical_rows = _canonical_urans_rows(active)
        if not canonical_rows:
            st.info(
                "Todavía no hay casos URANS canónicos reales. La prueba rápida "
                "temporal y los estudios PIMPLE no aparecen en esta revisión."
            )
        else:
            labels = {
                str(row["case_id"]): (
                    f"{str(row.get('topology') or '?').title()} · "
                    f"{str(row.get('mesh_level') or '?').title()} · "
                    f"dt={float(row.get('deltaT_s') or row.get('dt_s') or 0):.6g} s · "
                    f"{row.get('status')}"
                )
                for row in canonical_rows
            }
            case_id = st.selectbox(
                "Caso URANS canónico para revisar",
                list(labels),
                format_func=lambda value: labels[value],
                key="validation-canonical-urans-review-case",
            )
            row = next(item for item in canonical_rows if item["case_id"] == case_id)
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

            actions = st.columns(3)
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
                "Postproceso URANS completo",
                key=f"validation-postprocess-urans-{case_id}",
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
                        "--automatic-paraview-products",
                        *_paraview_scale_args(
                            postprocess_scale_settings,
                            animation=True,
                        ),
                    ],
                )
            with st.expander("Evidencia canónica y gráficas almacenadas"):
                _json_panel(run_root / "case_manifest.json", "Caso", inline=True)
                _json_panel(
                    run_root / "execution_summary.json",
                    "Ejecución",
                    inline=True,
                )
                _json_panel(run_root / "review.json", "Revisión", inline=True)
                _plot_inventory(run_root / "plots")
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
                    st.dataframe(
                        selected_rows,
                        hide_index=True,
                        width="stretch",
                    )
                    _plot_inventory(
                        active / "postprocess/spatial_rans" / topology
                    )
                else:
                    st.info(
                        f"No hay tres resultados RANS {topology} elegibles."
                    )

    if section == "Convergencia malla-tiempo":
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
        selected_topology = (
            "open"
            if top_section == "Convergencia espacio-tiempo"
            and subsection == "Abierto"
            else "closed"
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

    if section == "Frecuencias":
        st.caption(
            "Welch usa ventana Hann, detrend constante y 50% de solape "
            "solo sobre el muestreo uniforme. Startup y settling se excluyen."
        )
        _plot_inventory(active / "postprocess/frequency")
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
