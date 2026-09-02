"""Reusable quality comparison panel for experimental Gmsh revisions."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return math.nan


def _metric_payload(revision: Path, topology: str) -> dict[str, Any] | None:
    quality = _read(revision / "quality_distributions/quality_distributions.json")
    report = _read(revision / "mesh_report.json")
    config = _read(revision / "mesh_config.json")
    if not quality:
        return None
    basic = dict(quality.get("basic_characteristics") or {})
    stats = dict(quality.get("quality_statistics") or {})
    extrema = dict(quality.get("computed_extrema") or {})
    layer = dict(report.get("boundary_layer") or {})
    audit = dict(report.get("boundary_layer_mesh_audit") or {})
    counts = dict(audit.get("cell_counts_2d") or {})

    def stat(name: str, item: str, legacy: str | None = None) -> float:
        value = dict(stats.get(name) or {}).get(item)
        if value is None and legacy:
            value = extrema.get(legacy)
        return _number(value)

    theoretical = _number(
        basic.get("espesor objetivo delta99*FS [m]", layer.get("target_total_thickness_m"))
    )
    measured = _number(
        basic.get(
            "espesor prismatico real mediano [m]",
            basic.get("espesor prismático real mediano [m]", dict(audit.get("measured_total_thickness_m") or {}).get("median")),
        )
    )
    thickness_error = (
        abs(measured - theoretical) / theoretical
        if math.isfinite(measured) and math.isfinite(theoretical) and theoretical > 0 else math.nan
    )
    geometry = dict(config.get("geometry") or {})
    geometry_id = str(
        geometry.get("open_variant") or geometry.get("closed_variant") or "unknown"
    )
    return {
        "revision": revision.name,
        "geometry": geometry_id,
        "y+ objetivo": _number(basic.get("y+ objetivo", dict(config.get("boundary_layer") or {}).get("target_y_plus"))),
        "celdas 2D": _number(basic.get("celdas 2D totales", quality.get("cell_count"))),
        "triangulos interiores": _number(basic.get("triángulos interiores", counts.get("internal_triangles"))) if topology == "open" else math.nan,
        "capas BL": _number(basic.get("capas reales medianas", layer.get("layers"))),
        "error relativo espesor BL": thickness_error,
        "no ortogonalidad max": stat("non_orthogonality_deg", "maximum", "maximum_face_non_orthogonality_deg"),
        "no ortogonalidad media": stat("non_orthogonality_deg", "mean"),
        "skewness max": stat("skewness", "maximum", "maximum_face_skewness"),
        "skewness media": stat("skewness", "mean"),
        "peso interpolacion min": stat("interpolation_weight", "minimum", "minimum_face_interpolation_weight"),
        "peso interpolacion |media-0.5|": abs(stat("interpolation_weight", "mean") - 0.5),
        "ratio volumen min": stat("volume_ratio", "minimum", "minimum_face_volume_ratio"),
        "ratio volumen |media-1|": abs(stat("volume_ratio", "mean") - 1.0),
        "determinante min": stat("determinant", "minimum", "minimum_cell_determinant"),
        "determinante |media-1|": abs(stat("determinant", "mean") - 1.0),
        "AR quads BL max": stat("boundary_layer_aspect_ratio", "maximum", "maximum_boundary_layer_aspect_ratio"),
        "AR quads BL medio": stat("boundary_layer_aspect_ratio", "mean"),
        "AR triangulos max": stat("unstructured_aspect_ratio", "maximum", "maximum_unstructured_aspect_ratio"),
        "AR triangulos medio": stat("unstructured_aspect_ratio", "mean"),
    }


def _validation_medium_payload(experiment: Path, topology: str) -> dict[str, Any] | None:
    mesh_id = (
        "open_ramair_validation_1m"
        if topology == "open" else "reference_uncut_validation_1m"
    )
    report_path = experiment.parents[1] / "meshes" / mesh_id / "mesh_quality_report.json"
    report = _read(report_path)
    if not report:
        return None
    yplus = dict(report.get("boundary_layer_yplus_estimate") or {}).get("target_y_plus")
    return {
        "revision": f"medium actual · {mesh_id}",
        "geometry": mesh_id,
        "y+ objetivo": _number(yplus),
        "celdas 2D": _number(report.get("checkMesh_cell_count")),
        "triangulos interiores": math.nan,
        "capas BL": _number(report.get("boundary_layer_layers_requested")),
        "error relativo espesor BL": math.nan,
        "no ortogonalidad max": _number(report.get("checkMesh_max_non_orthogonality_deg")),
        "no ortogonalidad media": _number(report.get("checkMesh_average_non_orthogonality_deg")),
        "skewness max": _number(report.get("checkMesh_max_skewness")),
        "skewness media": math.nan,
        "peso interpolacion min": _number(report.get("checkMesh_min_face_interpolation_weight")),
        "peso interpolacion |media-0.5|": abs(
            _number(report.get("checkMesh_average_face_interpolation_weight")) - 0.5
        ),
        "ratio volumen min": _number(report.get("checkMesh_min_face_volume_ratio")),
        "ratio volumen |media-1|": abs(
            _number(report.get("checkMesh_average_face_volume_ratio")) - 1.0
        ),
        "determinante min": _number(report.get("checkMesh_min_cell_determinant")),
        "determinante |media-1|": abs(
            _number(report.get("checkMesh_average_cell_determinant")) - 1.0
        ),
        "AR quads BL max": _number(report.get("checkMesh_max_aspect_ratio")),
        "AR quads BL medio": math.nan,
        "AR triangulos max": math.nan,
        "AR triangulos medio": math.nan,
    }


MINIMIZE = {
    "y+ objetivo", "celdas 2D", "triangulos interiores",
    "error relativo espesor BL", "no ortogonalidad max", "no ortogonalidad media",
    "skewness max", "skewness media", "peso interpolacion |media-0.5|",
    "ratio volumen |media-1|", "determinante |media-1|", "AR quads BL max",
    "AR quads BL medio", "AR triangulos max", "AR triangulos medio",
}
MAXIMIZE = {
    "capas BL", "peso interpolacion min", "ratio volumen min", "determinante min",
}


def _highlight_best(data: pd.DataFrame) -> pd.DataFrame:
    styles = pd.DataFrame("", index=data.index, columns=data.columns)
    green = "background-color: #d9f2df; color: #17231a"
    orange = "background-color: #ffe7c2; color: #2a2114"
    for metric in data.index:
        if metric not in MINIMIZE and metric not in MAXIMIZE:
            continue
        values = pd.to_numeric(data.loc[metric], errors="coerce")
        finite = values.dropna()
        if finite.empty:
            continue
        target = finite.min() if metric in MINIMIZE else finite.max()
        winners = finite.index[(finite - target).abs() <= max(1.0e-12, abs(target) * 1.0e-9)]
        colour = orange if len(winners) == len(finite) else green
        for column in winners:
            styles.loc[metric, column] = colour
    return styles


def _highlight_best_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Highlight metrics when revisions are rows and metrics are columns."""
    return _highlight_best(data.T).T


def _metric_formatter(metric: str):
    if metric in {"celdas 2D", "triangulos interiores", "capas BL"}:
        return lambda value: "-" if pd.isna(value) else f"{int(round(value)):,}"
    if metric in {
        "peso interpolacion min", "ratio volumen min", "determinante min",
        "error relativo espesor BL", "peso interpolacion |media-0.5|",
        "ratio volumen |media-1|", "determinante |media-1|",
    }:
        return lambda value: "-" if pd.isna(value) else f"{value:.4g}"
    if metric == "y+ objetivo":
        return lambda value: "-" if pd.isna(value) else f"{value:.3g}"
    return lambda value: "-" if pd.isna(value) else f"{value:.3f}"


def render_mesh_quality_comparator(
    experiment: Path,
    *,
    topology: str,
    key_prefix: str,
) -> None:
    revisions = sorted(
        [path for path in (experiment / "revisions").glob("*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    payloads = [payload for path in revisions if (payload := _metric_payload(path, topology))]
    medium = _validation_medium_payload(experiment, topology)
    if medium is not None:
        payloads.append(medium)
    st.subheader("Comparador de calidad")
    st.caption(
        "Compara entre dos y cuatro revisiones de la misma geometria. Verde marca el mejor valor; "
        "naranja indica empate. El error de espesor BL se minimiza: una diferencia mayor no es una "
        "cualidad positiva aunque el texto inicial pudiera sugerirlo."
    )
    if len(payloads) < 2:
        st.info("Se necesitan al menos dos revisiones con estudio completo de calidad.")
        return
    geometries = sorted({str(item["geometry"]) for item in payloads})
    geometry = st.selectbox("Geometria a comparar", geometries, key=f"{key_prefix}-geometry")
    compatible = [item for item in payloads if str(item["geometry"]) == geometry]
    if len(compatible) < 2:
        st.info("Esta geometria todavia no tiene dos revisiones con tablas completas.")
        return
    names = [str(item["revision"]) for item in compatible]
    default_names = [names[0]]
    medium_names = [name for name in names if name.startswith("medium actual")]
    if medium_names:
        default_names.append(medium_names[0])
    elif len(names) > 1:
        default_names.append(names[1])
    selected = st.multiselect(
        "Revisiones (2-4)", names, default=default_names,
        max_selections=4, key=f"{key_prefix}-revisions",
    )
    if len(selected) < 2:
        st.warning("Selecciona al menos dos revisiones.")
        return
    by_name = {str(item["revision"]): item for item in compatible}
    metrics = [
        key for key in by_name[selected[0]]
        if key not in {"revision", "geometry"} and (topology == "open" or key != "triangulos interiores")
    ]
    frame = pd.DataFrame(
        {name: [by_name[name].get(metric, math.nan) for metric in metrics] for name in selected},
        index=metrics,
    )
    critical = [
        "no ortogonalidad max", "skewness max", "peso interpolacion min",
        "ratio volumen min", "determinante min", "AR quads BL max",
        "AR triangulos max",
    ]
    supporting = [metric for metric in metrics if metric not in critical]
    critical_tab, supporting_tab = st.tabs(["Calidad crítica", "Coste y distribuciones medias"])

    def render_table(container: Any, selected_metrics: list[str]) -> None:
        table = frame.loc[selected_metrics].T
        table.index.name = "revision"
        formatters = {
            metric: _metric_formatter(metric) for metric in selected_metrics
        }
        with container:
            st.dataframe(
                table.style.apply(_highlight_best_columns, axis=None).format(
                    formatters, na_rep="-"
                ),
                width="stretch",
            )

    render_table(critical_tab, critical)
    render_table(supporting_tab, supporting)
