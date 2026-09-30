"""Common controls and exact checkMesh display for the standard meshers."""
from __future__ import annotations
import re
import sys
from pathlib import Path
import streamlit as st
from ramair_workspace import bind_mesh_config, read_json


def reset_mesh_editor_state(topology: str) -> None:
    """Discard stale widget values when a saved configuration is loaded."""
    prefix = topology+"-"
    for key in list(st.session_state):
        if str(key).startswith(prefix) and str(key) != topology+"-exp-existing":
            del st.session_state[key]
    st.session_state[topology+"-show-loaded-editor"] = True


def quality_atlas(root: Path, mesh: Path, start_job) -> None:
    """A native quality atlas is available independently of PASS/FAIL."""
    with st.expander("Mapa de calidad · localizar celdas limitantes", expanded=False):
        st.caption("Calcula las métricas nativas de OpenFOAM para todas las celdas, también si checkMesh aprueba. El peor 1 % es una selección relativa, no un umbral de fallo.")
        atlas = read_json(mesh / "QualityAtlas/atlas.json")
        if st.button("Calcular / actualizar mapa de calidad", key="atlas-generate/"+str(mesh)):
            start_job("mesh_quality_atlas", [sys.executable, str(root/"CFD_2D/scripts/ramair_mesh_quality_atlas.py"), "--root", str(root), "--mesh", str(mesh)])
            st.success("Análisis añadido a Trabajos. Conserva los identificadores originales de esta malla.")
        if not atlas:
            return
        metrics = atlas.get("metrics", {})
        labels = {"weight":"Peso de interpolación", "volume_ratio":"Ratio de volumen", "determinant":"Determinante", "nonorth_deg":"No ortogonalidad", "skewness":"Skewness", "aspect_ratio":"Aspect ratio"}
        selected = st.selectbox("Métrica a localizar", list(metrics), format_func=lambda k: labels.get(k,k), key="atlas-metric/"+str(mesh))
        data = metrics[selected]
        if data.get("matches_checkMesh") is False:
            st.error("El extremo no coincide con el informe: revise la procedencia de esta malla.")
        image = mesh / "QualityAtlas" / (selected+".png")
        if image.is_file(): st.image(str(image), width="stretch")
        st.dataframe([{"Percentil": k, "Valor":v} for k,v in data.get("quantiles",{}).items()], hide_index=True, width="stretch")
        st.dataframe(data.get("worst_cells",[]), hide_index=True, width="stretch")
        csv = mesh / "QualityAtlas" / (selected+"_worst.csv")
        if csv.is_file(): st.download_button("Descargar 50 celdas limitantes", csv.read_bytes(), file_name=mesh.name+"_"+csv.name, mime="text/csv", key="atlas-csv/"+str(mesh)+selected)
        st.caption("Los IDs corresponden a polyMesh antes de renumerar o descomponer. La etiqueta de zona es orientativa; la posición y los polígonos proceden de la malla real.")


def front_controls(external: dict, key: str, *, choose_mode: bool = False) -> None:
    modes = ["tangential_match", "fixed", "sampled_front"]
    if choose_mode:
        external["interface_size_mode"] = st.selectbox("Ley de tamaño en la interfaz", modes, index=modes.index(external.get("interface_size_mode", "tangential_match")), key=key+"mode")
    if external.get("interface_size_mode") == "fixed" and choose_mode:
        external["interface_size_chord"] = st.number_input("Tamaño fijo de interfaz [c]", .000001, 1., float(external.get("interface_size_chord", .00035)), format="%.6f", key=key+"fixed")
    if external.get("interface_size_mode") != "sampled_front": return
    cols = st.columns(2)
    external["front_tangential_factor"] = cols[0].number_input("Factor tangencial del frente BL", .05, 5., float(external.get("front_tangential_factor", .85)), key=key+"tangent")
    external["front_normal_size_cap_factor"] = cols[1].number_input("Límite h / última altura BL (0 desactiva)", 0., 10., float(external.get("front_normal_size_cap_factor", 0.)), key=key+"normal")
    st.caption("Usa los nodos 1D reales y una estimación geométrica del frente exterior. El límite normal restringe el tamaño de los triángulos cerca de capas anisótropas. Compruebe el resultado con el atlas.")


def general_controls(config: dict, key: str) -> None:
    execution = config.setdefault("execution", {})
    geometry = config.setdefault("geometry", {})
    columns = st.columns(3)
    execution["gmsh_threads"] = columns[0].number_input("Threads Gmsh", 1, 64, int(execution.get("gmsh_threads", 8)), key=key+"threads")
    geometry["spanwise_thickness_chord"] = columns[1].number_input("Espesor transversal [c]", .00001, 1., float(geometry.get("spanwise_thickness_chord", .01)), format="%.5f", key=key+"span")
    geometry["spanwise_layers"] = columns[2].number_input("Capas transversales", 1, 1, 1, disabled=True, key=key+"layers", help="OpenFOAM 2D con frontAndBack empty exige una sola capa.")
    columns = st.columns(3)
    execution["gmsh_timeout_s"] = columns[0].number_input("Timeout Gmsh [s]", 30, 7200, int(execution.get("gmsh_timeout_s", 900)), key=key+"timeout")
    execution["openfoam_timeout_s"] = columns[1].number_input("Timeout checkMesh [s]", 30, 7200, int(execution.get("openfoam_timeout_s", 900)), key=key+"checktimeout")
    execution["minimum_cells_warning"] = columns[2].number_input("Aviso de celdas mínimas", 0, 2000000, int(execution.get("minimum_cells_warning", 10000)), key=key+"mincells")
    st.caption("Máximo: 2 000 000 celdas. Sin límite de MB ni de elementos leídos. Dominio circular.")
    with st.expander("Refinamiento de estela", expanded=False):
        wake = config.setdefault("wake_refinement", {})
        wake["enabled"] = st.checkbox("Activar refinamiento de estela", bool(wake.get("enabled", False)), key=key+"wake")
        for name, label, default in (("size_chord", "Tamaño [c]", .01), ("length_chord", "Longitud [c]", 5.), ("half_width_chord", "Semiancho [c]", .15)):
            wake[name] = st.number_input(label, .00001, 100., float(wake.get(name, default)), format="%.5f", key=key+name, disabled=not wake["enabled"])


def exact_quality(mesh: Path) -> None:
    report = read_json(mesh / "mesh_report.json")
    logfile = mesh / "log.checkMesh"
    text = logfile.read_text(errors="replace") if logfile.is_file() else ""
    def metric(key, regex):
        if report.get(key) is not None:
            return float(report[key])
        match = re.search(regex, text, re.I)
        return float(match.group(1).rstrip(".")) if match else None
    rows = []
    specs = [("No ortog. máx.", "checkMesh_max_non_orthogonality_deg", r"non-orthogonality Max:\s*([\deE+.-]+)", 1, "<40 excelente · 40–70 aceptable · >70 revisar · >85 crítica"),
             ("Skewness máx.", "checkMesh_max_skewness", r"Max skewness\s*=\s*([\deE+.-]+)", 2, "<2 buena · 2–4 aceptable · >4 revisar · >10 crítica"),
             ("Determinante mín.", "checkMesh_min_cell_determinant", r"determinant.*?minimum:\s*([\deE+.-]+)", 6, "Umbral checkMesh/configuración de calidad"),
             ("Peso interpol. mín.", "checkMesh_min_face_interpolation_weight", r"interpolation weight.*?minimum:\s*([\deE+.-]+)", 4, "Revisar salto entre celdas adyacentes"),
             ("Ratio volumen mín.", "checkMesh_min_face_volume_ratio", r"volume ratio.*?minimum:\s*([\deE+.-]+)", 4, "Revisar transiciones de tamaño"),
             ("Aspect ratio máx.", "checkMesh_max_aspect_ratio", r"Max aspect ratio\s*=\s*([\deE+.-]+)", 2, ">1000 requiere revisar alineación y zona"),
             ("Área cara mín.", "checkMesh_min_face_area", r"Minimum face area\s*=\s*([\deE+.-]+)", 6, "Debe ser positiva"),
             ("Volumen mín.", "checkMesh_min_volume", r"Min volume\s*=\s*([\deE+.-]+)", 6, "Volumen negativo impide ejecutar")]
    for label, key, pattern, places, guide in specs:
        value = metric(key, pattern)
        rendered = "No consta" if value is None else (f"{value:.{places}f}" if abs(value) >= 10**(-places) else f"{value:.3e}")
        if label in {"Área cara mín.", "Volumen mín."} and value is not None:
            rendered += f" · negativo={value < 0}"
        rows.append({"Métrica": label, "Valor": rendered, "Guía de revisión": guide})
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("Extremos del log real. Los intervalos de CFDpilot orientan la revisión; el veredicto técnico corresponde a checkMesh y sus controles efectivos.")
    if text:
        st.code(text[-16000:], language="text")
