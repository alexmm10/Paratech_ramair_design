#!/usr/bin/env python3
"""Streamlit control surface for the complete ram-air CFD 2D workflow."""
from __future__ import annotations

import html
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from boundary_layer_estimates import (  # noqa: E402
    boundary_layer_comparison,
    first_cell_height_from_yplus,
    turbulent_flat_plate_delta99,
)
from ramair_2d_timestep_advisor import temporal_frequency_budget  # noqa: E402
from ramair_geometry_workspace import (  # noqa: E402
    TE_LABELS,
    geometry_dto,
    import_profile,
    load_profile_catalog,
    preview_series,
)

import workflow_backend as _workflow_backend
from mesh_configuration import (
    DOMAIN_CONFIG_KEYS,
    DOMAIN_DEFAULTS,
    apply_mesh_level,
    domain_parameters,
    mesh_level_values,
)


EXPECTED_BACKEND_API_VERSION = 25
_REQUIRED_BACKEND_SYMBOLS = {
    "BACKEND_API_VERSION",
    "batch_postprocess_command",
    "catia_detection",
    "catia_macro_command",
    "case_library_command",
    "inlet_design_command",
    "mesh_refinement_analysis_command",
    "mesh_refinement_study_command",
    "migrate_solver_config_schema",
    "open_checkmesh_problem_viewer",
    "open_paraview_case",
    "open_results_library",
    "prepare_existing_simulation",
    "request_application_shutdown",
    "request_openfoam_sweep_stop",
    "request_validation_rans_stop",
    "request_validation_pimple_stop",
    "reconcile_validation_runtime",
    "saved_cases",
    "saved_mesh_catalog",
    "saved_mesh_configuration",
    "set_workcase_selection",
    "results_library_locations",
    "start_application_idle_watchdog",
    "touch_application_heartbeat",
    "validation_publish_command",
    "validation_study_command",
    "validation_study_snapshot",
    "save_validation_study_config",
}
_missing_backend_symbols = sorted(_REQUIRED_BACKEND_SYMBOLS.difference(dir(_workflow_backend)))
if _missing_backend_symbols:
    raise RuntimeError(
        "The Streamlit UI and workflow_backend.py are not from the same release. "
        f"Missing backend symbols: {', '.join(_missing_backend_symbols)}. "
        "Close this browser tab and restart with START_RAMAIR_CFD2D_APP.bat; "
        "do not run 'streamlit run' directly from an old WSL copy."
    )
if _workflow_backend.BACKEND_API_VERSION != EXPECTED_BACKEND_API_VERSION:
    raise RuntimeError(
        "Incompatible CFD 2D application/backend copy: "
        f"UI expects API {EXPECTED_BACKEND_API_VERSION}, backend provides "
        f"{_workflow_backend.BACKEND_API_VERSION}. Restart with "
        "START_RAMAIR_CFD2D_APP.bat so the WSL runtime is synchronized."
    )

from workflow_backend import (
    BACKEND_API_VERSION,
    Job,
    JobManager,
    approve_mesh_command,
    available_variants,
    batch_postprocess_command,
    catia_detection,
    catia_macro_command,
    case_builder_command,
    case_library_command,
    case_directory,
    case_writer_command,
    command_text,
    config_path,
    environment_command,
    find_project_root,
    latest_files,
    load_config,
    inlet_design_command,
    mesh_command,
    mesh_refinement_analysis_command,
    mesh_refinement_study_command,
    migrate_solver_config_schema,
    mesh_optimizer_command,
    open_mesh_viewer,
    open_checkmesh_problem_viewer,
    open_paraview_case,
    openfoam_case_from_command,
    open_results_library,
    postprocess_command,
    project_path,
    prepare_existing_outputs,
    prepare_existing_simulation,
    preprocessor_command,
    read_json,
    request_openfoam_clean_stop,
    request_openfoam_sweep_stop,
    request_validation_rans_stop,
    request_validation_pimple_stop,
    reconcile_validation_runtime,
    request_application_shutdown,
    result_directory,
    results_library_locations,
    runner_command,
    staged_runner_command,
    save_config,
    saved_cases,
    saved_mesh_catalog,
    saved_mesh_configuration,
    set_workcase_selection,
    start_application_idle_watchdog,
    tail_file,
    touch_application_heartbeat,
    xfoil_check_command,
    sweep_runner_command,
    validation_publish_command,
    validation_study_command,
    validation_study_snapshot,
    save_validation_study_config,
)
from validation_convergence_page import render_validation_convergence_lab


st.set_page_config(page_title="RamAir: Design and CFD", page_icon=None, layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1500px;}
      [data-testid="stMetricValue"] {font-size: 1.35rem;}
      [data-testid="stSidebar"] {border-right: 1px solid #d8dde3;}
      .ramair-path {font-family: monospace; font-size: .82rem; color: #4b5563; overflow-wrap: anywhere;}
      .ramair-ok {color: #16794a; font-weight: 600;}
      .ramair-warn {color: #9a6700; font-weight: 600;}
      .ramair-fail {color: #b42318; font-weight: 600;}
      .ramair-table-wrap {overflow-x: auto; margin: .5rem 0 1rem;}
      .ramair-table {border-collapse: collapse; width: 100%; font-size: .82rem;}
      .ramair-table th {background: #e5e7eb; color: #111827; font-weight: 650; text-align: left;}
      .ramair-table td {background: #111827; color: #f9fafb; overflow-wrap: anywhere; max-width: 24rem;}
      .ramair-table th, .ramair-table td {border: 1px solid #4b5563; padding: .42rem .55rem; vertical-align: top; line-height: 1.35;}
      .ramair-table tr:nth-child(even) td {background: #1f2937; color: #f9fafb;}
    </style>
    """,
    unsafe_allow_html=True,
)


ROOT = find_project_root()


def revisioned_widget_key(base: str) -> str:
    return f"{base}:revision-{int(st.session_state.get('_config_ui_revision', 0))}"


def apply_pending_configuration_reload() -> None:
    """Invalidate widgets only after a Results restore has completed."""
    pending = st.session_state.pop("_pending_configuration_reload", None)
    if not isinstance(pending, dict):
        return
    st.session_state["_config_ui_revision"] = int(st.session_state.get("_config_ui_revision", 0)) + 1
    case_name = str(pending.get("case", ""))
    manifest = next(
        (item for item in saved_cases(ROOT) if str(item.get("folder", "")) == case_name),
        {},
    )
    restored_workspace = read_json(ROOT / "CFD_2D/app_state/active_workspace.json", {}) or {}
    if restored_workspace:
        st.session_state["_preferred_variant_after_restore"] = restored_workspace.get("variant")
        st.session_state["_preferred_alpha_after_restore"] = restored_workspace.get("alpha_deg")
        st.session_state["_preferred_package_after_restore"] = restored_workspace.get("package")
    elif manifest:
        st.session_state["_preferred_variant_after_restore"] = manifest.get("variant")
        st.session_state["_preferred_alpha_after_restore"] = manifest.get("alpha_deg")
    stage = str(pending.get("stage", ""))
    page_by_stage = {
        "geometry": "Geometria",
        "case": "Caso CFD",
        "mesh": "Malla",
        "simulation": "Ejecucion",
        "postprocess": "Postproceso",
    }
    if stage in page_by_stage:
        st.session_state["active-workflow-page"] = page_by_stage[stage]
    st.session_state["_preferred_library_case_after_restore"] = case_name
    st.session_state["_configuration_reload_notice"] = (
        f"Paquete '{pending.get('package') or 'legacy'}' de la etapa '{stage}' de '{case_name}' cargado. "
        "Los controles se han reconstruido desde los JSON restaurados y el workspace activo conserva esta procedencia."
    )


apply_pending_configuration_reload()


def render_records_table(records: list[dict[str, Any]], *, max_rows: int = 60) -> None:
    """Render small diagnostic tables without Streamlit's PyArrow serializer."""
    if not records:
        return
    columns = list(dict.fromkeys(key for record in records for key in record))

    def cell(value: Any) -> str:
        if isinstance(value, (list, tuple)):
            sample = list(value[:5])
            text = json.dumps(sample, ensure_ascii=False, default=str)
            if len(value) > len(sample):
                text = text[:-1] + f", ... (+{len(value) - len(sample)})]"
        elif isinstance(value, dict):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
        if len(text) > 240:
            text = text[:237] + "..."
        return html.escape(text)

    header = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(record.get(column, ''))}</td>" for column in columns) + "</tr>"
        for record in records[:max_rows]
    )
    st.markdown(
        f'<div class="ramair-table-wrap"><table class="ramair-table"><thead><tr>{header}</tr></thead>'
        f'<tbody>{body}</tbody></table></div>',
        unsafe_allow_html=True,
    )
    if len(records) > max_rows:
        st.caption(f"Mostrando {max_rows} de {len(records)} filas.")


@st.cache_resource
def manager_for(path: str) -> JobManager:
    return JobManager(Path(path))


@st.cache_data(ttl=60, show_spinner=False)
def cached_catia_detection(project_root: str) -> dict[str, Any]:
    """Keep CATIA probing responsive without treating it as a dependency."""
    return catia_detection(Path(project_root))


MANAGER = manager_for(str(ROOT))
reconcile_validation_runtime(ROOT)
start_application_idle_watchdog(ROOT, MANAGER)

CHOICES: dict[str, list[Any]] = {
    "te_closure_mode": ["rounded", "straight_gap", "sharp_extension"],
    "shape": ["circle", "ellipse"],
    "profile_input_order": [
        "auto",
        "upper_TE_to_LE__lower_LE_to_TE",
        "upper_LE_to_TE__lower_TE_to_LE",
        "upper_LE_to_TE__lower_LE_to_TE",
        "closed_airfoil_standard_dat",
        "section_column",
    ],
    "chord_mode": ["rectangular", "elliptic", "quasi_elliptic"],
    "chord_anchor": ["leading_edge", "trailing_edge", "mid_chord", "quarter_chord", "custom"],
    "cell_span_mode": ["uniform", "elliptic", "quasi_elliptic"],
    "anhedral_arc_mode": ["tip_tangent", "center_to_tip_line"],
    "anhedral_rotation_pivot_mode": ["profile_min_z", "profile_zero", "te_center"],
    "anhedral_section_orientation": ["focus_inward", "legacy_outward"],
    "cut_mode": ["curves_only", "post_split_surfaces", "fill_inner_boundaries"],
    "cut_strategy": ["curve_split_first", "extruded_wall_split", "curve_split"],
    "cutter_extrude_mode": ["auto_semicell_span", "fixed"],
    "ellipse_orientation": ["horizontal", "vertical", "auto"],
    "position_mode": ["standard_3", "equidistant", "custom"],
    "apply_to": ["all_internal", "loaded_internal", "nonloaded_internal"],
    "centerline_mode": ["profile_midline"],
    "fabric_thickness_strategy": ["shell_property", "post_mesh_extrusion", "catia_offset_experimental"],
    "fabric_thickness_mode": ["none", "single_offset", "symmetric_offsets"],
    "fabric_thickness_offset_side": ["normal", "opposite"],
    "suspension_line_cad_strategy": ["curve_with_properties", "catia_tubes_experimental", "mesh_cylinders_postprocess"],
    "domain": ["circular_50c", "ross_cgrid_like", "rectangular_balaji", "debug_20c"],
    "domain_type": ["circular_50c", "ross_cgrid_like", "rectangular_balaji", "debug_20c"],
    "mesh_level": ["coarse", "medium", "fine", "extra_fine"],
    "solver": ["auto", "foamRun", "pimpleFoam"],
    "solver_module": ["incompressibleFluid"],
    "stop_mode": ["writeNow", "nextWrite", "noWriteNow"],
    "export_mode": ["openfoam_reader", "coefficients_only", "latest_vtk", "all_vtk", "none"],
    "existing_mesh_action": ["ask", "archive", "delete", "keep"],
    "existing_case_results_action": ["ask", "archive", "delete", "stop"],
    "closed_wall_curve_method": ["two_spline_te_cap", "single_spline_bump"],
    "open_wall_curve_method": ["segmented_outer_splines", "single_outer_spline_with_lip_fans", "single_outer_bspline_with_lip_fans"],
    "open_inlet_boundary_layer_mode": [
        "full_prismatic_bridge_without_fans",
        "full_prismatic_bridge_with_fans",
        "triangular_inlet_no_bl",
    ],
    "open_geometry_representation": [
        "zero_thickness_base_profile",
        "finite_thickness_fabric",
    ],
    "open_base_inlet_alignment_mode": ["similarity", "endpoint_blend"],
    "open_cavity_inlet_size_strategy": [
        "hybrid_boundary_extension",
        "boundary_extension",
        "boundary_uniform",
        "staged_explicit",
    ],
    "open_inlet_transition_elements": [
        "graded_quads", "graded_triangles", "triangles", "transfinite_triangles", "recombined_quads"
    ],
    "geometry_mode": ["thin_solid_fabric"],
    "turbulence_model": ["SpalartAllmaras"],
    "gmsh_backend": ["auto", "python_api", "cli"],
    "execution_backend": ["native", "pyfoam"],
    "time_step_mode": ["adaptive_physics_limited", "adaptive_courant", "fixed"],
    "field_write_control": ["adjustableRunTime", "timeStep", "runTime"],
    "farfield_boundary_condition": ["freestream", "fixed_velocity_fallback"],
    "velocity_source": ["mach", "reynolds", "velocity"],
    "ddt_scheme": ["Euler", "backward", "CrankNicolson 0.9"],
    "transient_velocity_divergence_scheme": [
        "Gauss linearUpwind limited",
        "Gauss limitedLinearV 1",
        "Gauss upwind",
    ],
    "transient_turbulence_divergence_scheme": [
        "Gauss linearUpwind limited",
        "Gauss limitedLinear 1",
        "Gauss upwind",
    ],
    "velocity_divergence_scheme": [
        "bounded Gauss linearUpwind limited",
        "bounded Gauss limitedLinearV 1",
        "bounded Gauss upwind",
    ],
    "turbulence_divergence_scheme": [
        "bounded Gauss linearUpwind limited",
        "bounded Gauss limitedLinear 1",
        "bounded Gauss upwind",
    ],
}

CHOICE_LABELS: dict[str, dict[Any, str]] = {
    "domain_type": {
        "circular_50c": "Circular (50c por defecto)",
        "ross_cgrid_like": "C-grid-like (contorno redondeado + salida recta)",
        "rectangular_balaji": "Rectangular Balaji",
        "debug_20c": "Circular debug (20c por defecto)",
    },
    "open_inlet_boundary_layer_mode": {
        "full_prismatic_bridge_without_fans": "BL exterior completa, sin fans (recomendado)",
        "full_prismatic_bridge_with_fans": "BL exterior completa + fans (comparacion)",
        "triangular_inlet_no_bl": "Inlet triangular sin BL (comparacion; no recomendado)",
    },
    "open_geometry_representation": {
        "zero_thickness_base_profile": "Sin espesor: arco del perfil base (recomendado)",
        "finite_thickness_fabric": "Banda de tela con espesor numerico (metodo anterior)",
    },
    "open_base_inlet_alignment_mode": {
        "similarity": "Perfil base exacto: traslacion, giro y escala uniforme",
        "endpoint_blend": "Correccion local smoothstep (comparacion anterior)",
    },
    "open_cavity_inlet_size_strategy": {
        "hybrid_boundary_extension": "Ajuste corto normal + crecimiento desde aristas reales (recomendado)",
        "boundary_extension": "Heredar aristas reales del inlet y crecer",
        "boundary_uniform": "Tamano tangencial constante en toda la cavidad (diagnostico)",
        "staged_explicit": "Transicion explicita limitada por y1 (metodo anterior)",
    },
    "open_inlet_transition_elements": {
        "graded_quads": "Bloque cuadrilateral graduado desde y1 (recomendado)",
        "graded_triangles": "Triangulos graduados alternos (diagnostico)",
        "transfinite_triangles": "Banda triangular transfinita (comparacion)",
        "recombined_quads": "Banda cuadrilateral recombinada",
        "triangles": "Triangulos Delaunay sin gradacion (comparacion)",
    },
    "ddt_scheme": {
        "Euler": "Euler implicito (1er orden, robusto)",
        "backward": "Backward implicito (2o orden)",
        "CrankNicolson 0.9": "Crank-Nicolson 0.9 (2o orden, descentrado)",
    },
    "time_step_mode": {
        "adaptive_physics_limited": "Variable: maxDeltaT fisico + Courant de emergencia",
        "adaptive_courant": "Variable: ajustado automaticamente por Courant",
        "fixed": "Constante: deltaT* impuesto",
    },
}


MESH_UI_DEFAULTS: dict[str, Any] = {
    "domain_type": "circular_50c",
    "domain_circular_radius_chord": 50.0,
    "domain_cgrid_upstream_chord": 10.0,
    "domain_cgrid_downstream_chord": 20.0,
    "domain_cgrid_top_chord": 10.0,
    "domain_cgrid_bottom_chord": 10.0,
    "domain_rectangular_upstream_chord": 5.0,
    "domain_rectangular_downstream_chord": 11.0,
    "domain_rectangular_top_chord": 5.0,
    "domain_rectangular_bottom_chord": 5.0,
    "domain_debug_radius_chord": 20.0,
    "open_inlet_boundary_layer_mode": "full_prismatic_bridge_without_fans",
    "open_geometry_representation": "zero_thickness_base_profile",
    "open_base_profile_variant": "reference_uncut",
    "open_base_inlet_alignment_mode": "similarity",
    "open_base_inlet_blend_fraction": 0.30,
    "open_cavity_inlet_size_strategy": "hybrid_boundary_extension",
    "open_cavity_inlet_extension_power": 0.75,
    "open_internal_inlet_matching_transition_chord": 0.002,
    "open_inlet_transition_elements": "graded_quads",
    "open_inlet_transition_growth": 1.22,
    "open_inlet_bridge_smoothing_enabled": True,
    "open_inlet_bridge_smoothing_handle_fraction": 0.080,
    "open_lip_cap_rounding_enabled": False,
    "open_lip_cap_rounding_points": 7,
    "open_inlet_connector_normal_nodes": 0,
    "open_inlet_marker_bump_strength": 0.60,
    "open_single_connected_surface_2d": False,
    "closed_nearfield_outer_size_chord": 0.18,
    "closed_farfield_transition_dist_chord": 9.0,
    "open_nearfield_outer_size_chord": 0.35,
    "open_farfield_transition_dist_chord": 10.0,
    "mesh_level_origin": "fine",
    "mesh_configuration_mode": "custom",
}

MESH_LEGACY_HIDDEN_KEYS = {
    "open_boundary_layer_include_inlet_bridge",
    "open_boundary_layer_fan_at_lips",
    "open_single_connected_surface_2d",
    "closed_first_cell_height_chord",
    "open_first_cell_height_chord",
    "first_cell_height_chord_override",
    "open_first_cell_height_chord_override",
    "closed_first_cell_height_m_override",
    "open_first_cell_height_m_override",
    "domain_radius_chord",
    "debug_domain_radius_chord",
    "mesh_level_origin",
    "mesh_configuration_mode",
    *{key for keys in DOMAIN_CONFIG_KEYS.values() for key in keys},
}


MESH_FIELD_LABELS = {
    "target_y_plus": "Objetivo y+",
    "fabric_thickness_chord": "Espesor numerico de tela [m]",
    "closed_use_yplus_first_cell_height": "Calcular y1 desde y+ y condiciones fisicas",
    "closed_first_cell_height_m": "Altura manual y1 [m]",
    "closed_boundary_layer_layers": "Numero de capas prismaticas",
    "closed_boundary_layer_growth": "Crecimiento normal de capas",
    "closed_wall_target_nodes": "Nodos tangenciales totales de pared",
    "closed_te_target_nodes": "Nodos en el cierre curvo del TE",
    "closed_te_bump_strength": "Intensidad Bump junto al TE",
    "closed_te_transition_min_nodes": "Nodos minimos en tramos vecinos al TE",
    "closed_te_refinement_width_chord": "Extension del refinamiento geometrico TE / cuerda",
    "closed_near_wall_size_from_bl": "Derivar tamano triangular desde la BL",
    "closed_near_wall_size_chord": "Tamano triangular manual junto a pared / cuerda",
    "closed_nearfield_dist_min_chord": "Espesor de la zona mas fina / cuerda",
    "closed_nearfield_intermediate_dist_chord": "Fin de la transicion cercana / cuerda",
    "closed_nearfield_dist_max_chord": "Fin de la zona moderadamente fina / cuerda",
    "closed_nearfield_intermediate_size_chord": "Tamano intermedio / cuerda",
    "closed_nearfield_outer_size_chord": "Tamano exterior moderado / cuerda",
    "closed_farfield_transition_dist_chord": "Fin de la transicion lenta al farfield / cuerda",
    "closed_farfield_size_chord": "Tamano de farfield / cuerda",
    "domain_type": "Tipo de dominio",
    "domain_circular_radius_chord": "Radio circular / cuerda",
    "domain_debug_radius_chord": "Radio debug / cuerda",
    "domain_cgrid_upstream_chord": "C-grid-like: distancia aguas arriba / cuerda",
    "domain_cgrid_downstream_chord": "C-grid-like: distancia aguas abajo / cuerda",
    "domain_cgrid_top_chord": "C-grid-like: altura superior / cuerda",
    "domain_cgrid_bottom_chord": "C-grid-like: altura inferior / cuerda",
    "domain_rectangular_upstream_chord": "Rectangular: distancia aguas arriba / cuerda",
    "domain_rectangular_downstream_chord": "Rectangular: distancia aguas abajo / cuerda",
    "domain_rectangular_top_chord": "Rectangular: altura superior / cuerda",
    "domain_rectangular_bottom_chord": "Rectangular: altura inferior / cuerda",
    "open_inlet_boundary_layer_mode": "Tratamiento de la capa limite en el inlet",
    "open_geometry_representation": "Representacion de la pared abierta",
    "open_base_profile_variant": "Perfil base sin corte para reconstruir el inlet",
    "open_base_inlet_alignment_mode": "Alineacion del arco base con los labios",
    "open_base_inlet_blend_fraction": "Fraccion del arco usada para ajustar exactamente los labios",
    "open_inlet_transition_elements": "Elementos de transicion del inlet",
    "open_inlet_transition_growth": "Crecimiento normal maximo en la garganta",
    "open_inlet_bridge_smoothing_enabled": "Suavizar la interfaz ficticia del inlet",
    "open_inlet_bridge_smoothing_handle_fraction": "Longitud de tangencia / abertura del inlet",
    "open_lip_cap_rounding_enabled": "Redondear el cap del espesor en cada labio",
    "open_lip_cap_rounding_points": "Puntos geometricos del cap redondeado",
    "open_inlet_connector_normal_nodes": "Nodos normales de garganta (0 = automatico)",
    "open_minimum_fabric_thickness_chord": "Espesor minimo de tela [m]",
    "open_wall_curve_method": "Representacion de curvas de pared exterior",
    "open_use_yplus_first_cell_height": "Calcular y1 desde y+ y condiciones fisicas",
    "open_first_cell_height_m": "Altura manual y1 [m]",
    "open_boundary_layer_layers": "Numero de capas prismaticas exteriores",
    "open_boundary_layer_growth": "Crecimiento normal de capas",
    "open_boundary_layer_lip_fan_points": "Sectores del fan en cada labio",
    "open_surface_target_nodes": "Nodos tangenciales del contorno exterior",
    "open_zero_thickness_contour_target_nodes": "Nodos tangenciales del contorno completo",
    "open_zero_thickness_inlet_normal_y1_factor": "Tamano normal inicial de compatibilidad / y1",
    "open_surface_transfinite_multiplier": "Multiplicador global de nodos exteriores",
    "open_surface_transfinite_progression": "Progresion tangencial base exterior",
    "open_wall_end_bump_enabled": "Refinar progresivamente ambos extremos de pared",
    "open_wall_end_bump_strength": "Intensidad Bump en extremos de intrados/extrados",
    "open_te_transfinite_min_nodes": "Nodos en el cierre curvo exterior del TE",
    "open_zero_thickness_te_transfinite_min_nodes": "Nodos en el TE exterior sin espesor",
    "open_te_refinement_width_chord": "Extension de refinamiento exterior del TE / cuerda",
    "open_te_transition_distance_chord": "Longitud de transicion TE-triangulos / cuerda",
    "open_surface_size_te_chord": "Tamano local manual en TE / cuerda",
    "open_lip_transfinite_min_nodes": "Nodos minimos en ramas exteriores y labios",
    "open_inlet_marker_transfinite_nodes": "Nodos tangenciales a lo largo del inlet",
    "open_inlet_marker_bump_strength": "Concentracion tangencial en ambos labios del inlet",
    "open_inner_wall_node_factor": "Fraccion de nodos en paredes interiores",
    "open_inner_te_node_factor": "Fraccion de nodos en TE interior",
    "open_inner_wall_min_nodes": "Nodos minimos por tramo interior",
    "open_inner_te_min_nodes": "Nodos minimos en el TE interior",
    "open_inner_wall_end_bump_enabled": "Refinar extremos de paredes interiores",
    "open_inner_wall_end_bump_strength": "Intensidad Bump en paredes interiores",
    "open_cavity_wall_size_chord": "Tamano junto a pared interior / cuerda",
    "open_cavity_wall_transition_chord": "Alcance de transicion interior / cuerda",
    "open_cavity_size_chord": "Tamano en el nucleo de la cavidad / cuerda",
    "open_internal_inlet_refinement_enabled": "Refinar el volumen interior tras el inlet",
    "open_cavity_inlet_size_strategy": "Estrategia de tamano tras la interfaz del inlet",
    "open_cavity_inlet_extension_power": "Suavidad de crecimiento interior (Extend Power)",
    "open_internal_inlet_dist_min_chord": "Zona interior de tamano minimo / cuerda",
    "open_internal_inlet_matching_transition_chord": "Fin del ajuste normal-tangencial / cuerda",
    "open_internal_inlet_matching_size_factor": "Factor del espaciado tangencial del inlet",
    "open_internal_inlet_near_transition_chord": "Fin de la primera transicion interior / cuerda",
    "open_internal_inlet_intermediate_size_chord": "Tamano intermedio tras el inlet / cuerda",
    "open_internal_inlet_dist_max_chord": "Alcance del refinamiento interior / cuerda",
    "open_internal_inlet_size_chord": "Tamano manual de triangulos del inlet / cuerda",
    "open_inlet_refinement_bridge_enabled": "Activar puente geometrico de refinamiento del inlet",
    "open_internal_te_refinement_enabled": "Refinar el volumen interior junto al TE",
    "open_internal_te_dist_max_chord": "Alcance del refinamiento interior TE / cuerda",
    "open_internal_te_size_factor": "Factor de tamano respecto al espaciado del TE interior",
    "open_near_wall_size_from_bl": "Derivar tamano exterior desde la BL",
    "open_near_wall_size_chord": "Tamano exterior manual junto a BL / cuerda",
    "open_nearfield_dist_min_chord": "Espesor de la zona exterior mas fina / cuerda",
    "open_nearfield_intermediate_dist_chord": "Fin de la transicion exterior cercana / cuerda",
    "open_nearfield_dist_max_chord": "Fin de la zona exterior moderadamente fina / cuerda",
    "open_nearfield_intermediate_size_chord": "Tamano exterior intermedio / cuerda",
    "open_nearfield_outer_size_chord": "Tamano exterior moderado / cuerda",
    "open_farfield_transition_dist_chord": "Fin de la transicion lenta al farfield / cuerda",
    "open_farfield_size_chord": "Tamano de farfield abierto / cuerda",
    "open_transition_sigmoid_enabled": "Suavizar transiciones de tamano con Sigmoid",
}


TAB_INTROS = {
    "Estado": "Comprueba las dependencias de Python, Gmsh y OpenFOAM antes de lanzar etapas. El resultado esperado es un inventario reproducible del entorno WSL, sin ejecutar CATIA ni el solver.",
    "Caso de trabajo": "Selecciona o crea el contenedor versionado que enlaza geometria, caso CFD, malla, solver y resultados. Cada revision conserva dependencias y su propia decision de aprobacion.",
    "Geometria": "Define el perfil, la planta y las transformaciones de costillas que alimentan el preprocesador, junto con crossports, tejido, estabilizadores, suspension y exportaciones CATIA. Guarda primero la configuracion y ejecuta despues el preprocesador para regenerar CATIA/Inputs y las geometrias CFD 2D.",
    "Caso CFD": "Construye el paquete de una geometria ya preprocesada y le asocia Reynolds, Mach, densidad, viscosidad y barrido de angulos. Esta etapa valida contratos y unidades; todavia no genera malla ni ejecuta OpenFOAM.",
    "Malla": "Ajusta la discretizacion de pared, capa limite, transiciones y dominio. Cada ejecucion genera una malla nueva segun la politica elegida; despues puedes abrir mesh_final.msh en Gmsh, inspeccionarla y decidir si aprobarla.",
    "Caso OpenFOAM": "Escribe diccionarios, campos iniciales y funciones de control a partir de una polyMesh convertida. La etapa no ejecuta el solver y conserva separadas la descripcion del caso y sus resultados.",
    "Ejecucion": "Prepara un dry-run o inicia explicitamente OpenFOAM. La parada limpia modifica stopAt=writeNow, espera la escritura y reconstruccion MPI, y conserva salidas parciales utilizables.",
    "Postproceso": "Extrae coeficientes, residuos y campos derivados de una ejecucion real. Puedes limitar la exportacion y abrir la carpeta o ParaView; la ausencia de datos se informa como NOT_RUN_YET, no como un resultado vacio.",
    "Archivos y logs": "Reune el historial de trabajos y los artefactos recientes para auditar comandos, tiempos y salidas sin recorrer manualmente todo el proyecto.",
}


PROJECT_SECTION_INTROS = {
    "project_paths": "Rutas relativas a la raiz del proyecto. CATIA/Inputs contiene solo entradas CAD y CFD_2D permanece fuera.",
    "profile_inputs": "Nubes de puntos y orden esperado de cada perfil. El preprocesador canoniza upper/lower y conserva trazabilidad del archivo fuente.",
    "canopy_geometry": "Dimensiones globales, planta, reparto en envergadura y convenciones del arco/anhedral del canopy.",
    "rib_and_cell_geometry": "Incidencia y traslacion de costillas cargadas/no cargadas, deformacion de puntas y secciones virtuales de ballooning.",
    "airfoil_processing": "Limpieza del perfil, estrategia de cierre del trailing edge y metadatos de espesor usados por la exportacion CFD 2D.",
    "crossports": "Geometria, posicion y estrategia CATIA para los orificios de equilibrado de presion en costillas internas.",
    "fabric_and_lines": "Propiedades de tejido y lineas. Los offsets y tubos CATIA siguen marcados como experimentales; la opcion robusta exporta midsurfaces y curvas con propiedades.",
    "catia_generation": "Activa las entidades CAD que el CATScript debe construir a partir de los CSV generados.",
    "catia_exports": "Formatos, nombres y ubicacion de los ficheros exportados por CATIA V5.",
    "optional_modules": "Interruptores de estabilizadores, tip bulge y suspension, y ruta de su configuracion detallada.",
    "cfd_2d_exports": "Selecciona las interfaces geometricas y contratos que el preprocesador entrega al flujo CFD 2D.",
    "cfd_2d": "Condiciones iniciales por defecto que se escriben en las plantillas CFD; pueden sustituirse al construir cada caso.",
    "debug_plots": "Controla solamente salidas visuales de diagnostico del preprocesador.",
}


PROJECT_TAB_LAYOUT = [
    ("Rutas y perfiles", ["project_paths", "profile_inputs"]),
    ("Canopy", ["canopy_geometry", "rib_and_cell_geometry"]),
    ("Perfil y TE", ["airfoil_processing"]),
    ("Crossports", ["crossports"]),
    ("Tejido y lineas", ["fabric_and_lines"]),
    ("CATIA", ["catia_generation", "catia_exports", "optional_modules"]),
    ("CFD 2D y plots", ["cfd_2d_exports", "cfd_2d", "debug_plots"]),
]

PROJECT_TAB_INTROS = {
    "Rutas y perfiles": "Selecciona la raiz de datos y las nubes de puntos. Al ejecutar el preprocesador se validan y se copian solo los productos necesarios a CATIA/Inputs y CFD_2D_inputs.",
    "Canopy": "Define planta, envergadura, cuerda, costillas, incidencias y deformaciones geométricas del canopy antes de generar tablas para CATIA.",
    "Perfil y TE": "Controla limpieza, normalizacion, separacion upper/lower y cierre del trailing edge. Estas opciones cambian la geometria exportada, no la fisica CFD.",
    "Crossports": "Configura los orificios internos y sus margenes geometricos. El preprocesador valida que puedan construirse antes de que CATIA consuma los datos.",
    "Tejido y lineas": "Agrupa propiedades estructurales y representaciones CAD de tela y lineas; las estrategias experimentales se mantienen identificadas como tales.",
    "CATIA": "Selecciona entidades y formatos que Generate_RamAir_Canopy_MAIN.CATScript debe construir o exportar. Guardar aqui no inicia CATIA.",
    "CFD 2D y plots": "Elige las geometrías entregadas al caso CFD 2D y los diagnosticos del preproceso. El mallado se configura y ejecuta despues en Malla.",
}


PROJECT_FIELD_HELP = {
    "anhedral_deg": "Angulo usado directamente solo cuando 'Derivar anhedral desde R/b' esta desactivado. Si la derivacion esta activa, el preprocesador calcula el arco compatible con el R/b seleccionado.",
    "enable_variable_chord": "Activa una planta de cuerda variable. Desactivado mantiene chord_mm en todas las costillas.",
    "chord_mode": "Ley de distribucion de cuerda a lo largo de la envergadura cuando la cuerda variable esta activa.",
    "enable_variable_cell_span": "Activa anchos de celda variables. Desactivado reparte uniformemente span_mm entre las celdas.",
    "cell_span_mode": "Ley usada para distribuir los anchos de celda cuando enable_variable_cell_span esta activo.",
    "te_closure_mode": "Estrategia geometrica del trailing edge: rounded crea cierre tangente, straight_gap conserva cierre recto y sharp_extension prolonga intrados/extrados hasta su interseccion segura.",
    "te_rounding_points": "Puntos geometricos del cierre redondeado entregado a CATIA. No es el numero de celdas tangenciales de Gmsh.",
    "min_spline_point_distance_mm": "Filtro CATIA para puntos consecutivos casi coincidentes antes de crear splines. No controla el tamano de celda CFD.",
    "model_zero_thickness_as_thin_solid": "Para CFD abierto, representa la tela mediante un espesor finito pequeno para separar pared interior y exterior sin cerrar el inlet fluido.",
    "fabric_thickness_chord": "Espesor artificial de la tela dividido por la cuerda en la interfaz CFD 2D. Debe ser positivo y mucho menor que la geometria aerodinamica.",
    "shape": "Forma del crossport. El preprocesador admite circle y ellipse.",
    "custom_specs": "Lista JSON opcional. Ejemplo completo: [{\"x\":0.25,\"shape\":\"ellipse\",\"orientation\":\"horizontal\",\"width_chord_frac\":0.055,\"height_thickness_frac\":0.32},{\"x\":0.65,\"shape\":\"ellipse\",\"orientation\":\"vertical\",\"width_chord_frac\":0.035,\"height_thickness_frac\":0.50}]. Cuando no esta vacia sustituye la distribucion automatica.",
    "cut_mode": "Opcion avanzada CATIA. post_split_surfaces es el modo validado; cambialo solo para diagnosticar fallos de corte.",
    "split_orientation": "Opcion avanzada CATIA. Invierte el lado conservado por Split si CATIA elimina la region equivocada.",
    "cut_strategy": "Opcion avanzada CATIA. curve_split_first es la estrategia recomendada y usa cutter extruido solo como fallback.",
    "cutter_extrude_mode": "Opcion avanzada para dimensionar el cutter auxiliar; auto_semicell_span es el valor robusto.",
    "cutter_extrude_factor_semicell": "Factor de la semienvergadura de celda usado para el cutter automatico.",
    "cutter_extrude_min_mm": "Limite inferior del cutter auxiliar automatico.",
    "cutter_extrude_max_mm": "Limite superior del cutter auxiliar automatico.",
    "cutter_extrude_mm": "Longitud manual del cutter, usada solo cuando cutter_extrude_mode=fixed o como fallback.",
}


FIELD_LABELS = {
    "enable_crossports": "Activar crossports",
    "enable_canopy_stabilizers": "Activar estabilizadores del canopy",
    "enable_tip_side_bulge": "Activar deformacion lateral de punta",
    "main_profile": "Perfil principal",
    "reference_uncut_profile": "Perfil cerrado de referencia",
    "ross_standard_profile": "Perfil abierto Ross estandar",
    "ross_minimum_profile": "Perfil abierto Ross minimo",
    "span_mm": "Envergadura [mm]",
    "chord_mm": "Cuerda central [mm]",
    "cells": "Numero de celdas",
    "anhedral_deg": "Anhedral [deg]",
    "te_rounding_points": "Puntos del redondeado TE",
    "min_spline_point_distance_mm": "Distancia minima entre puntos spline [mm]",
    "fabric_thickness_chord": "Espesor de tela / cuerda",
    "model_zero_thickness_as_thin_solid": "Representar tela como solido fino",
    "system_config_json": "Configuracion detallada del sistema",
    "ddt_scheme": "Esquema temporal",
    "transient_velocity_divergence_scheme": "Conveccion de velocidad",
    "transient_turbulence_divergence_scheme": "Conveccion de turbulencia",
    "velocity_divergence_scheme": "Conveccion de velocidad SIMPLE",
    "turbulence_divergence_scheme": "Conveccion de turbulencia SIMPLE",
    "n_outer_correctors": "Correctores externos PIMPLE",
    "n_correctors": "Correctores de presion PIMPLE",
    "n_non_orthogonal_correctors": "Correctores no ortogonales",
    "maxCo": "Courant maximo",
    "time_step_mode": "Control del paso temporal",
    "deltaT_star": "Paso inicial deltaT*",
    "maxDeltaT_star": "Limite maximo deltaT*",
    "field_write_step_equivalent": "Pasos fisicos aprox. entre campos",
    "endTime_star": "Duracion objetivo t*",
    "target_min_strouhal": "Strouhal minimo de interes",
    "target_max_strouhal": "Strouhal maximo de interes",
    "target_samples_per_cycle": "Muestras objetivo por ciclo",
    "minimum_cycles_for_statistics": "Ciclos minimos para estadistica",
    "smoke_end_time_star": "Duracion t* de smoke test",
    "reference_deltaT_star": "deltaT* de la referencia",
    "reference_end_time_star": "Duracion t* de la referencia",
    "reference_average_time_star": "Ventana media t* de la referencia",
    "time_step_study_values_star": "Secuencia de estudio deltaT*",
}


def human_field_label(field_name: str) -> str:
    if field_name in FIELD_LABELS:
        return FIELD_LABELS[field_name]
    text = field_name.replace("_", " ")
    replacements = {
        "enable ": "Activar ",
        " mm": " [mm]",
        " deg": " [deg]",
        " chord": " / cuerda",
        " fraction": " (fraccion)",
        " count": " (numero)",
        " points": " (puntos)",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text[:1].upper() + text[1:]


def field_help_text(field_name: str, explicit: str | None = None) -> str:
    """Provide useful help for every editable geometry/configuration field."""
    if explicit:
        return explicit
    if field_name in PROJECT_FIELD_HELP:
        return PROJECT_FIELD_HELP[field_name]
    normalized = field_name.lower()
    if normalized.startswith(("enable_", "write_", "export_", "generate_")):
        return (
            "Interruptor funcional. Activado incluye esta operacion en la siguiente ejecucion del "
            "preprocesador; desactivado conserva la configuracion pero omite el producto."
        )
    if normalized.endswith("_mm"):
        return "Valor geometrico en milimetros utilizado por el preprocesador y exportado a CATIA."
    if normalized.endswith(("_deg", "_angle")) or "angle" in normalized:
        return "Angulo geometrico en grados. El signo sigue la convencion descrita en la seccion."
    if normalized.endswith("_chord") or "_chord_" in normalized:
        return "Magnitud adimensional normalizada por la cuerda local o de referencia."
    if "fraction" in normalized or "factor" in normalized or "ratio" in normalized:
        return "Factor adimensional. Revisa el rango antes de regenerar porque modifica la geometria derivada."
    if any(token in normalized for token in ("path", "file", "json", "profile", "output", "dir")):
        return "Ruta o identificador relativo a la raiz del proyecto; se conserva portable entre equipos."
    if any(token in normalized for token in ("count", "points", "cells", "number", "stations")):
        return "Cantidad discreta usada para construir o muestrear la geometria; valores altos aumentan coste y tamano."
    if "mode" in normalized or "strategy" in normalized or "scheme" in normalized:
        return "Selecciona la estrategia implementada para esta etapa. El valor actual es el flujo validado por defecto."
    return (
        f"Parametro `{field_name}` consumido por el preprocesador actual. Se guarda sin conversion "
        "y se aplica en la siguiente regeneracion."
    )


PROJECT_FIELD_GROUPS: dict[str, list[tuple[str, list[str], bool]]] = {
    "canopy_geometry": [
        ("Dimensiones principales", ["anhedral_deg", "chord_mm", "span_mm", "cells"], False),
        ("Plataforma", ["enable_variable_chord", "chord_mode", "tip_chord_factor", "chord_anchor", "chord_anchor_fraction", "quasi_elliptic_exponent", "enable_variable_cell_span", "cell_span_mode", "tip_cell_width_factor", "enable_span_shrinkage", "span_shrinkage_fraction"], False),
        ("Convenciones geometricas avanzadas", ["anhedral_arc_mode", "anhedral_rotation_pivot_mode", "anhedral_section_orientation"], True),
    ],
    "airfoil_processing": [
        ("Preprocesado del perfil", ["min_spline_point_distance_mm"], False),
        ("Cierre y redondeado del trailing edge", ["te_closure_mode", "te_rounding_points", "sharp_te_intersection_max_x_c", "sharp_te_min_extension_x_c", "sharp_te_safe_gap_chord"], False),
        ("Interfaz geometrica para mallado CFD", ["model_zero_thickness_as_thin_solid", "fabric_thickness_chord"], False),
    ],
    "crossports": [
        ("Distribucion y forma", ["enable_crossports", "shape", "ellipse_orientation", "count", "position_mode", "x_positions_chord", "x_start_chord", "x_end_chord", "width_fraction_chord", "height_fraction_local_thickness", "edge_clearance_fraction_local_thickness", "points_per_loop", "apply_to", "centerline_mode", "custom_specs"], False),
        ("Opciones avanzadas CATIA: modificar solo ante fallos", ["cut_mode", "split_orientation", "cut_strategy", "cutter_extrude_mode", "cutter_extrude_factor_semicell", "cutter_extrude_min_mm", "cutter_extrude_max_mm", "cutter_extrude_mm"], True),
    ],
    "optional_modules": [
        ("Modulos opcionales", ["enable_canopy_stabilizers", "enable_tip_side_bulge", "system_config_json"], False),
    ],
}


SYSTEM_TAB_LAYOUT = [
    ("Suspension", ["suspension", "canopy_reference", "banks", "loaded_rib_selection", "anchors", "cascades", "angles", "constraints"]),
    ("Lineas y risers", ["line_properties", "risers", "slider", "brakes"]),
    ("Payload y AGU", ["payload", "agu"]),
    ("Estabilizadores", ["stabilizers", "tip_side_bulge"]),
    ("Tejido y outputs", ["fabric_thickness", "outputs"]),
]

SYSTEM_TAB_INTROS = {
    "Suspension": "Define bancos, anclajes, cascadas y restricciones que el preprocesador convierte en tablas de suspension trazables para CATIA.",
    "Lineas y risers": "Ajusta propiedades de lineas, elevadores, slider y frenos; las listas conservan su estructura JSON para no perder relaciones entre ramas.",
    "Payload y AGU": "Configura masas y geometria de payload/AGU usadas por los modulos CAD opcionales y sus referencias de posicion.",
    "Estabilizadores": "Controla estabilizadores y deformacion lateral de punta; solo se procesan cuando sus interruptores estan activados en la configuracion principal.",
    "Tejido y outputs": "Fija espesores y productos opcionales del sistema. El preprocesador copia una instantanea exacta a CATIA/Inputs para reproducibilidad.",
}


def nested_descriptions(data: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = value
            else:
                result.update(nested_descriptions(value))
    return result


MESH_DESCRIPTIONS = nested_descriptions(load_config(ROOT, "mesh_reference"))
MESH_DESCRIPTIONS.update({
    "open_geometry_representation": (
        "Sin espesor crea dos superficies fluidas y cose solo el arco ficticio del inlet basado en el "
        "perfil sin corte. La banda con espesor conserva la topologia anterior para comparacion."
    ),
    "open_base_profile_variant": (
        "Nombre del paquete CFD cerrado cuya curva real de LE se usa para continuar la capa prismatica "
        "sobre el inlet. Debe existir en CFD_2D_inputs/case_package."
    ),
    "open_base_inlet_alignment_mode": (
        "Similarity aplica una unica traslacion, rotacion y escala uniforme al arco del perfil sin "
        "corte: encaja ambos labios sin deformar localmente la curvatura. Endpoint blend conserva "
        "el metodo anterior solo para comparaciones."
    ),
    "open_base_inlet_blend_fraction": (
        "Solo interviene con endpoint_blend. Fraccion de cada extremo corregida con smoothstep; "
        "se ignora cuando la alineacion es similarity."
    ),
    "open_zero_thickness_contour_target_nodes": (
        "Numero total de segmentos repartidos por longitud de arco sobre pared exterior, TE e inlet "
        "ficticio. Gmsh conserva entidades separadas solo para que el inlet no sea wall, pero todas "
        "reciben el mismo espaciado tangencial."
    ),
    "open_zero_thickness_te_transfinite_min_nodes": (
        "Reserva nodos dentro del total anterior para resolver el cierre curvo del TE sin espesor. "
        "Al aumentarlo se restan nodos de las ramas rectas; no aumenta el total del contorno."
    ),
    "open_zero_thickness_inlet_normal_y1_factor": (
        "En el metodo hibrido fija el tamano normal inicial de una franja muy corta como multiplo "
        "de y1. No modifica el ancho tangencial de la interfaz, que procede de la longitud real "
        "de sus aristas. El valor medido 8 suaviza el salto de volumen junto al inlet; reducirlo "
        "mas puede cambiar la conectividad y volver a degradar la malla. staged_explicit conserva "
        "el uso heredado."
    ),
    "open_inner_wall_node_factor": (
        "Fraccion de la discretizacion exterior usada en cada pared interior. El Bump interior "
        "concentra progresivamente esos nodos cerca del inlet y del TE."
    ),
    "open_inner_wall_end_bump_strength": (
        "Parametro Bump de Gmsh para las paredes interiores. Valores menores que 1 concentran mas "
        "nodos en ambos extremos; valores demasiado pequenos pueden crear aristas muy cortas."
    ),
    "open_internal_inlet_matching_transition_chord": (
        "En el metodo hibrido es la franja normal corta que evita que el centro de la primera "
        "celda triangular quede muy alejado del centro de la primera celda prismatica. No controla "
        "el ancho tangencial ni refina el resto de la cavidad. En staged_explicit conserva su uso anterior."
    ),
    "open_internal_inlet_matching_size_factor": (
        "Multiplica el espaciado tangencial longitud_del_inlet/(nodos-1) para obtener el primer "
        "objetivo interior. 1.0 empareja ambos tamanos sin introducir una longitud absoluta."
    ),
    "open_internal_inlet_near_transition_chord": (
        "Distancia desde el inlet en la que los triangulos pasan del espaciado de la interfaz al "
        "tamano interior intermedio. No aumenta la discretizacion de toda la cavidad."
    ),
    "open_internal_inlet_intermediate_size_chord": (
        "Solo interviene con staged_explicit. Tamano alcanzado tras su primera transicion interior."
    ),
    "open_cavity_inlet_size_strategy": (
        "Hybrid boundary extension usa primero una franja normal muy corta compatible con y1 y despues "
        "el campo Extend de Gmsh. Extend toma SizeBnd de la media local de las aristas reales del inlet "
        "y lo hace crecer hacia el nucleo. Boundary extension omite la franja corta; boundary uniform "
        "mantiene el tamano tangencial derivado en toda la cavidad; staged explicit conserva el metodo anterior."
    ),
    "open_cavity_inlet_extension_power": (
        "Exponente de Extend. Un valor inferior a 1 mantiene el tamano heredado durante mas distancia "
        "y suaviza el crecimiento; valores superiores a 1 alcanzan antes el tamano grueso."
    ),
    "open_transition_sigmoid_enabled": (
        "Usa la interpolacion sigmoidal documentada por Gmsh en los campos Threshold para evitar "
        "cambios bruscos de pendiente entre zonas de tamano."
    ),
    "domain_type": "Selecciona solo la forma del contorno exterior. Sus dimensiones se editan inmediatamente debajo y no se toman de ningun otro preset.",
    "domain_circular_radius_chord": "Radio del dominio circular medido desde el centro de la cuerda y normalizado con la cuerda.",
    "domain_debug_radius_chord": "Radio del dominio circular reducido. Es solo para depurar software, no para validar L/D.",
    "domain_cgrid_upstream_chord": "Distancia desde el LE hasta el extremo redondeado aguas arriba del contorno C-grid-like.",
    "domain_cgrid_downstream_chord": "Posicion de la frontera vertical aguas abajo medida desde el LE.",
    "domain_cgrid_top_chord": "Distancia superior del contorno C-grid-like respecto al eje de la cuerda.",
    "domain_cgrid_bottom_chord": "Distancia inferior del contorno C-grid-like respecto al eje de la cuerda.",
    "domain_rectangular_upstream_chord": "Distancia de la frontera rectangular de entrada aguas arriba del LE.",
    "domain_rectangular_downstream_chord": "Distancia de la frontera rectangular de salida aguas abajo del LE.",
    "domain_rectangular_top_chord": "Distancia de la frontera rectangular superior al eje de la cuerda.",
    "domain_rectangular_bottom_chord": "Distancia de la frontera rectangular inferior al eje de la cuerda.",
    "open_inlet_bridge_smoothing_enabled": "Sustituye la interfaz ficticia recta por una Bezier tangente a intrados y extrados. No crea una pared ni un patch fisico; reduce el giro brusco que deforma la BL en los labios.",
    "open_inlet_bridge_smoothing_handle_fraction": "Fraccion de la abertura usada por cada asa tangente de la Bezier exterior. El estudio controlado selecciono 0.08 junto con growth=1.22: esta combinacion paso checkMesh real en el dominio debug de 20c.",
    "open_inlet_transition_growth": "Limite superior del crecimiento normal a traves del bloque cuadrilateral del inlet. El solver geometrico calcula una progresion menor o igual que este valor para cubrir exactamente el espesor de tela; 1.22 produjo 1.20794 real en el candidato aprobado.",
    "open_lip_cap_rounding_enabled": "Alternativa geometrica de diagnostico. El estudio con caps semicirculares empeoro no ortogonalidad y skewness; se mantiene desactivada por defecto.",
})

SOLVER_DESCRIPTIONS = {
    "ddt_scheme": "Esquema de derivada temporal: Euler es implicito de primer orden; backward es implicito de segundo orden; Crank-Nicolson 0.9 es segundo orden con descentrado para robustez.",
    "time_step_mode": "Variable activa adjustTimeStep y limita Courant con maxCo/maxDeltaT. Constante impone deltaT* en todos los pasos y exige comprobar el Co resultante.",
    "outer_corrector_residual_control": "Permite salir antes del maximo de correctores externos cuando U y nuTilda alcanzan su tolerancia dentro del timestep. Validation Lab lo desactiva para mantener el numero fijo.",
    "transport_correction_final": "Si esta desactivado, actualiza el modelo de transporte/turbulencia en cada corrector externo; mejora la coherencia del criterio residual con un coste moderado.",
    "U_tolerance": "Residual absoluto de U que permite terminar antes el bucle externo PIMPLE.",
    "nuTilda_tolerance": "Residual absoluto de Spalart-Allmaras que permite terminar antes el bucle externo PIMPLE.",
    "p_tolerance": "Residual absoluto de presion que permite terminar antes el bucle externo PIMPLE del perfil abierto.",
    "relative_tolerance": "Reduccion relativa exigida dentro del timestep. Cero obliga a alcanzar la tolerancia absoluta.",
    "deltaT_star": "Paso temporal adimensional deltaT* = deltaT U/c. Es el valor inicial en modo variable y el valor impuesto en modo constante.",
    "maxDeltaT_star": "Techo fisico adimensional del paso temporal, elegido por resolucion espectral e independencia temporal. Es la restriccion normal del modo adaptativo.",
    "maxCo": "Salvaguarda de emergencia del ajuste automatico. No sustituye al estudio de deltaT: limita aumentos no lineales de Courant sin forzar que cada celda diminuta gobierne permanentemente el paso.",
    "endTime_star": "Duracion adimensional objetivo del caso, expresada en tiempos convectivos c/U.",
    "field_write_control": "adjustableRunTime alinea snapshots con tiempo fisico aunque deltaT cambie por Courant; timeStep guarda cada N iteraciones y runTime escribe al superar el intervalo.",
    "field_write_interval_star": "Intervalo adimensional entre campos 3D: Delta(t*)=Delta(t)U/c. 0.25 da 20 snapshots por periodo si St=0.2; no cambia el deltaT del solver.",
    "field_write_interval_s": "Intervalo fisico real [s] entre snapshots 3D cuando se usa adjustableRunTime. Si tiene valor, prevalece sobre field_write_interval_star.",
    "field_write_interval_steps": "Alternativa usada solo con timeStep: iteraciones entre snapshots 3D. Coeficientes y residuos se guardan en cada iteracion.",
    "field_write_step_equivalent": "Cadencia de campos volumetricos expresada como numero aproximado de pasos al techo fisico solicitado. Fuerzas, residuos y Courant se conservan continuamente.",
    "purgeWrite": "Numero maximo de snapshots 3D conservados. Con 24 y Delta(t*)=0.25 se retienen seis tiempos convectivos; cero conserva todo y puede consumir mucho espacio.",
    "average_from_fraction": "Fraccion inicial del historial de fuerzas ignorada al promediar; 0.6 usa el ultimo 40%. No modifica el solver.",
    "farfield_boundary_condition": "freestream usa freestreamVelocity/freestreamPressure y permite entrada/salida segun el flujo local en un contorno circular. fixed_velocity_fallback conserva el fallback legado.",
    "steady_initialization_enabled": "Activa por defecto una etapa SIMPLE estacionaria de inicializacion antes del PIMPLE transitorio. No sustituye el resultado transitorio.",
    "steady_max_iterations": "Numero maximo de iteraciones SIMPLE antes de evaluar residuos y estabilidad de Cl/Cd/Cm.",
    "steady_write_interval_iterations": "Intervalo de iteraciones para conservar campos durante la etapa estacionaria.",
    "steady_residual_control": "Tolerancias de residuos iniciales p/U/nuTilda usadas por residualControl para detener SIMPLE.",
    "steady_numerics": "Controles numericos conservadores exclusivos de la inicializacion SIMPLE; no cambian el modelo fisico ni la etapa PIMPLE.",
    "n_non_orthogonal_correctors": "Correcciones no ortogonales de SIMPLE. Cero es el valor habitual para una malla con no ortogonalidad moderada que ya pasa checkMesh.",
    "p_relaxation": "Relajacion de presion en SIMPLE. Valores menores amortiguan el acoplamiento presion-velocidad pero requieren mas iteraciones.",
    "U_relaxation": "Relajacion de velocidad exclusiva de SIMPLE. No modifica los correctores ni los esquemas de PIMPLE.",
    "nuTilda_relaxation": "Relajacion de Spalart-Allmaras durante SIMPLE. Reducela si nuTilda oscila o crece de forma no fisica.",
    "velocity_profile_stations_xc": "Estaciones x/c donde el postproceso traza lineas normales en intrados y extrados.",
    "velocity_profile_sample_points": "Numero de muestras OpenFOAM a lo largo de cada perfil de velocidad normal a pared.",
    "transient_velocity_divergence_scheme": "Discretiza la conveccion de U. linearUpwind es de segundo orden y sesgado aguas arriba; limitedLinearV limita conjuntamente las componentes vectoriales para ganar robustez.",
    "transient_turbulence_divergence_scheme": "Discretiza la conveccion de nuTilda. upwind es acotado y robusto; linearUpwind limited reduce difusion numerica con menor margen ante mallas dificiles.",
    "velocity_divergence_scheme": "Esquema convectivo de U durante SIMPLE. El prefijo bounded incluye el termino de continuidad no convergido y suele facilitar la convergencia estacionaria.",
    "turbulence_divergence_scheme": "Esquema convectivo de nuTilda durante SIMPLE. bounded Gauss upwind prioriza positividad y robustez de la inicializacion.",
    "target_min_strouhal": "Frecuencia adimensional mas lenta que debe entrar en las estadisticas. Diez ciclos exigen una ventana t*=10/St_min.",
    "target_max_strouhal": "Frecuencia adimensional mas rapida que se quiere resolver. El limite Nyquist es deltaT*=1/(2 St_max), pero una simulacion util necesita mas de dos muestras por ciclo.",
    "target_samples_per_cycle": "Objetivo de resolucion temporal del proyecto. Veinte muestras por ciclo es un criterio de ingenieria conservador, no una constante universal del paper.",
    "minimum_cycles_for_statistics": "Numero minimo de ciclos de la frecuencia aceptada mas lenta que deben entrar en medias, amplitudes y PSD.",
    "smoke_end_time_star": "Duracion reservada a comprobacion de software. Un caso con esta duracion no se considera convergido ni validado.",
    "reference_deltaT_star": "Paso nominal publicado o de referencia. Se conserva para reproduccion y debe compararse con reducciones sucesivas.",
    "reference_end_time_star": "Duracion fisica adimensional de la referencia bibliografica.",
    "reference_average_time_star": "Ventana adimensional usada para promediar en la referencia bibliografica.",
    "time_step_study_values_star": "Serie reproducible de deltaT* decrecientes. Cada candidato debe ejecutarse durante el mismo tiempo fisico.",
    "strouhal_scope": "Fenomenos que justifican el intervalo de Strouhal. Debe actualizarse con PSD de fuerzas y sondas de presion del caso real.",
    "scope_note": "Limites y diferencias de modelo que deben conservarse junto al caso para no convertir una recomendacion en una falsa validacion.",
}


# Empty text in these genuinely optional controls is stored as JSON null. The
# writer then omits the OpenFOAM entry instead of silently substituting another
# project value. Required physical/time controls remain numeric widgets.
OPTIONAL_SOLVER_NUMBER_SUFFIXES: dict[str, type[int] | type[float]] = {
    ".n_outer_correctors": int,
    ".n_non_orthogonal_correctors": int,
    ".steady_residual_control.p": float,
    ".steady_residual_control.U": float,
    ".steady_residual_control.nuTilda": float,
    ".steady_numerics.p_relaxation": float,
    ".steady_numerics.U_relaxation": float,
    ".steady_numerics.nuTilda_relaxation": float,
}


def optional_solver_number_type(label: str) -> type[int] | type[float] | None:
    if not label.startswith("solver."):
        return None
    matches = [
        (suffix, number_type)
        for suffix, number_type in OPTIONAL_SOLVER_NUMBER_SUFFIXES.items()
        if label.endswith(suffix)
    ]
    return max(matches, key=lambda item: len(item[0]))[1] if matches else None


def parse_nullable(text: str, original: Any) -> Any:
    stripped = text.strip()
    if not stripped or stripped.lower() in {"none", "null"}:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        if isinstance(original, str) or original is None:
            return stripped
        raise


def value_widget(
    label: str,
    value: Any,
    key: str,
    help_text: str | None = None,
    display_label: str | None = None,
) -> Any:
    key = revisioned_widget_key(key)
    field_name = label.split(".")[-1]
    short = display_label or field_name
    if field_name in {"main_profile", "reference_uncut_profile", "ross_standard_profile", "ross_minimum_profile"}:
        available = sorted(
            str(path.relative_to(ROOT)).replace("\\", "/")
            for suffix in ("*.dat", "*.csv", "*.txt")
            for path in project_path(ROOT, "profiles").glob(suffix)
            if path.is_file()
        )
        current = str(value)
        if current not in available:
            available.insert(0, current)
        registry = read_json(project_path(ROOT, "profiles", "generated_profiles_registry.json"), {}) or {}
        generated_labels: dict[str, str] = {}
        for item in registry.get("profiles", []):
            try:
                path = str(item["path"])
                gap = float(item["inlet_gap_percent_chord"])
                reynolds = float(item["reynolds"])
                mach = float(item["mach"])
            except (KeyError, TypeError, ValueError):
                continue
            generated_labels[path] = (
                f"{path}  |  corte {gap:.1f}%c, Re={reynolds:.3g}, M={mach:.3g}"
            )
        return st.selectbox(
            short,
            available,
            index=available.index(current),
            key=key,
            help=help_text,
            format_func=lambda option: generated_labels.get(option, option),
        )
    options = CHOICES.get(field_name)
    if options:
        current = value if value in options else options[0]
        labels = CHOICE_LABELS.get(field_name, {})
        return st.selectbox(
            short,
            options,
            index=options.index(current),
            key=key,
            help=help_text,
            format_func=lambda option: labels.get(option, option),
        )
    if isinstance(value, bool):
        return st.toggle(short, value=value, key=key, help=help_text)
    optional_number_type = optional_solver_number_type(label)
    if optional_number_type is not None:
        raw = st.text_input(
            short,
            value="" if value is None else str(value),
            key=key,
            help=(
                (help_text + " " if help_text else "")
                + "Opcional: deja el campo vacio para omitir la entrada y usar el valor por defecto de OpenFOAM."
            ),
        )
        if not raw.strip():
            return None
        try:
            parsed = float(raw)
            if optional_number_type is int:
                if not parsed.is_integer():
                    raise ValueError("must be an integer")
                return int(parsed)
            return parsed
        except ValueError:
            st.error(f"Valor numerico no valido en {label}")
            return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(st.number_input(short, value=value, step=1, key=key, help=help_text))
    if isinstance(value, float):
        return float(st.number_input(short, value=value, format="%.10g", key=key, help=help_text))
    if isinstance(value, (list, dict)):
        raw = st.text_area(short, value=json.dumps(value, ensure_ascii=False), key=key, help=help_text)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            st.error(f"JSON no valido en {label}")
            return value
    raw = st.text_input(short, value="" if value is None else str(value), key=key, help=help_text)
    return parse_nullable(raw, value)


def render_nested_object(data: dict[str, Any], prefix: str, descriptions: dict[str, str] | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {}
    descriptions = descriptions or {}
    for section, value in data.items():
        path = f"{prefix}.{section}"
        if section == "description" and isinstance(value, str):
            st.caption(value)
            output[section] = value
            continue
        if isinstance(value, dict):
            st.markdown(f"**{section.replace('_', ' ').title()}**")
            output[section] = render_nested_object(value, path, descriptions)
            st.divider()
        else:
            output[section] = value_widget(
                path,
                value,
                f"cfg:{path}",
                field_help_text(section, descriptions.get(section)),
                human_field_label(section),
            )
    return output


def render_grouped_object(
    data: dict[str, Any],
    prefix: str,
    groups: list[tuple[str, list[str], bool]],
) -> dict[str, Any]:
    """Render selected keys by relevance while preserving every hidden key."""
    output = dict(data)
    rendered: set[str] = set()
    for title, keys, advanced in groups:
        container = st.expander(title, expanded=False) if advanced else st.container()
        with container:
            if not advanced:
                st.markdown(f"**{title}**")
            else:
                st.warning("Parametros avanzados: modifica estos valores solo para diagnosticar un fallo geometrico o de CATIA.")
            for key in keys:
                if key not in data:
                    continue
                output[key] = value_widget(
                    f"{prefix}.{key}",
                    data[key],
                    f"cfg:{prefix}.{key}",
                    field_help_text(key),
                    human_field_label(key),
                )
                rendered.add(key)
        st.divider()
    ungrouped = [key for key in data if key not in rendered and key != "enable_suspension_lines"]
    if ungrouped:
        with st.expander("Compatibilidad y parametros secundarios", expanded=False):
            st.caption("Se conservan para compatibilidad con el preprocesador actual.")
            for key in ungrouped:
                output[key] = value_widget(
                    f"{prefix}.{key}",
                    data[key],
                    f"cfg:{prefix}.{key}",
                    field_help_text(key),
                    human_field_label(key),
                )
    return output


def render_solver_field_set(
    data: dict[str, Any],
    prefix: str,
    keys: list[str],
) -> dict[str, Any]:
    output = dict(data)
    for field_name in keys:
        if field_name not in data:
            continue
        output[field_name] = value_widget(
            f"{prefix}.{field_name}",
            data[field_name],
            f"cfg:{prefix}.{field_name}",
            field_help_text(field_name, SOLVER_DESCRIPTIONS.get(field_name)),
            human_field_label(field_name),
        )
    return output


def render_temporal_accuracy_editor(
    temporal: dict[str, Any],
    prefix: str,
    solver_values: dict[str, Any],
) -> dict[str, Any]:
    """Edit and immediately interpret one dimensionless temporal budget."""
    edited = render_solver_field_set(
        temporal,
        prefix,
        [
            "target_min_strouhal",
            "target_max_strouhal",
            "target_samples_per_cycle",
            "minimum_cycles_for_statistics",
            "smoke_end_time_star",
            "reference_deltaT_star",
            "reference_end_time_star",
            "reference_average_time_star",
            "time_step_study_values_star",
            "strouhal_scope",
            "scope_note",
        ],
    )
    try:
        budget = temporal_frequency_budget(
            delta_t_star=float(solver_values.get("deltaT_star", 0.005)),
            max_delta_t_star=float(solver_values.get("maxDeltaT_star", 0.02)),
            end_time_star=float(solver_values.get("endTime_star", 20.0)),
            average_from_fraction=float(solver_values.get("average_from_fraction", 0.6)),
            temporal_config=edited,
        )
    except (TypeError, ValueError) as exc:
        st.error(f"Presupuesto temporal incompleto: {exc}")
        return edited
    metrics = st.columns(4)
    metrics[0].metric(
        "Nyquist deltaT* max",
        f"{budget['nyquist_deltaT_star_ceiling']:.4g}",
    )
    metrics[1].metric(
        "Objetivo deltaT* max",
        f"{budget['engineering_deltaT_star_ceiling']:.4g}",
    )
    metrics[2].metric(
        "Muestras/ciclo rapido",
        f"{budget['configured_initial_samples_per_fastest_cycle']:.3g}",
    )
    metrics[3].metric(
        "Ventana media minima t*",
        f"{budget['minimum_average_window_time_star']:.3g}",
    )
    if (
        budget["configured_average_window_time_star"]
        < budget["minimum_average_window_time_star"]
    ):
        st.warning(
            "La duracion y la fraccion de promediado actuales no contienen los ciclos "
            "minimos de la frecuencia seleccionada mas lenta."
        )
    if solver_values.get("endTime_star", 0.0) <= edited.get("smoke_end_time_star", -1.0):
        st.info(
            "La duracion activa corresponde a un smoke test de software; no es una "
            "ventana estadistica de produccion."
        )
    rows = budget.get("time_step_study_samples_per_fastest_cycle", [])
    if rows:
        render_records_table(
            [
                {
                    "deltaT*": f"{float(row['deltaT_star']):.8g}",
                    "muestras/ciclo a St_max": (
                        f"{float(row['samples_per_cycle_at_target_St_max']):.5g}"
                    ),
                }
                for row in rows
            ],
            max_rows=12,
        )
    return edited


def render_outer_residual_control(
    control: dict[str, Any],
    prefix: str,
    field_names: tuple[str, ...],
) -> dict[str, Any]:
    """Render the exact OpenFOAM outerCorrectorResidualControl fields."""
    output = {"enabled": bool(control.get("enabled", True)), "fields": {}}
    output["enabled"] = st.toggle(
        "Salida temprana por residuos",
        value=output["enabled"],
        key=revisioned_widget_key(f"{prefix}.enabled"),
        help="El valor indicado es un maximo; el bucle puede terminar antes al cumplir todos los campos.",
    )
    source_fields = control.get("fields") if isinstance(control.get("fields"), dict) else {}
    for field_name in field_names:
        values = source_fields.get(field_name) if isinstance(source_fields.get(field_name), dict) else {}
        cols = st.columns(2)
        tolerance = cols[0].number_input(
            f"{field_name}: tolerancia absoluta",
            value=float(values.get("tolerance", 1.0e-4)),
            format="%.6g",
            min_value=1.0e-16,
            key=revisioned_widget_key(f"{prefix}.{field_name}.tolerance"),
        )
        rel_tol = cols[1].number_input(
            f"{field_name}: relTol",
            value=float(values.get("relTol", 0.0)),
            format="%.6g",
            min_value=0.0,
            key=revisioned_widget_key(f"{prefix}.{field_name}.relTol"),
        )
        output["fields"][field_name] = {"tolerance": float(tolerance), "relTol": float(rel_tol)}
    return output


def solver_config_editor() -> dict[str, Any]:
    """Render parallel closed/open OpenFOAM controls with one physical source."""
    data = load_config(ROOT, "solver")
    st.subheader("Configuracion OpenFOAM")
    st.caption(
        "Closed y Open se comparan en paralelo. Reynolds, Mach, fluido y cuerda pertenecen al CFD Case; "
        "esta pagina solo configura numerica, ejecucion y escritura."
    )
    transfer_columns = st.columns(2)
    transfer_columns[0].download_button(
        "Exportar configuracion del solver",
        data=json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        file_name="cfd2d_solver_config.json",
        mime="application/json",
        width="stretch",
    )
    uploaded = transfer_columns[1].file_uploader(
        "Importar configuracion del solver",
        type=["json"],
        key=revisioned_widget_key("solver-config-upload"),
        help="La importacion migra primero al esquema vigente y conserva los ajustes avanzados.",
    )
    if uploaded is not None:
        try:
            imported = json.loads(uploaded.getvalue().decode("utf-8-sig"))
            if not isinstance(imported, dict):
                raise TypeError("El archivo debe contener un objeto JSON.")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            st.error(f"No se puede importar la configuracion: {exc}")
        else:
            if st.button("Aplicar archivo importado", type="primary", key="apply-imported-solver-config"):
                imported = migrate_solver_config_schema(imported)
                profiles = dict(imported.get("topology_profiles") or {})
                profiles.pop("closed_external_airfoil", None)
                imported["topology_profiles"] = profiles
                save_config(ROOT, "solver", imported)
                st.session_state["_config_ui_revision"] = int(st.session_state.get("_config_ui_revision", 0)) + 1
                st.session_state["_configuration_reload_notice"] = "Configuracion del solver importada."
                st.rerun()

    edited = dict(data)
    profiles = dict(edited.get("topology_profiles") or {})
    profiles.pop("closed_external_airfoil", None)
    open_profile = dict(profiles.get("open_internal_cavity") or {})
    open_profile.setdefault("time_step_mode", edited.get("time_step_mode", "adaptive_physics_limited"))
    tabs = st.tabs(["General", "Solver Settings", "Writing & Postprocess", "Traceability"])

    with tabs[0]:
        edited = render_solver_field_set(
            edited,
            "solver",
            ["solver", "solver_module", "turbulence_model", "optional_secondary_model", "velocity_source", "farfield_boundary_condition"],
        )
        physical = read_json(ROOT / "CFD_2D/CFD_2D_inputs/case_package/physical_config.json", {}) or {}
        selected_manifest = read_json(
            ROOT / "CFD_2D/CFD_2D_inputs/case_package" / str(variant) / "manifest.json", {}
        ) or {}
        st.markdown("**Condiciones del CFD Case (solo lectura)**")
        metrics = st.columns(4)
        metrics[0].metric("Reynolds", f"{float(physical.get('reynolds', 0.0)):.6g}")
        metrics[1].metric("Mach", f"{float(physical.get('mach', 0.0)):.6g}")
        metrics[2].metric("Cuerda", f"{float(selected_manifest.get('chord_m', physical.get('chord_m', 0.0))):.6g} m")
        metrics[3].metric("Angulos", str(len(physical.get("alphas_deg", []) or load_config(ROOT, "workflow").get("case_conditions", {}).get("alphas_deg", []))))
        st.info("Para cambiar estas magnitudes, edita Condiciones CFD. El writer rechaza overrides de Reynolds distintos del CFD Case.")

    with tabs[1]:
        st.caption(
            "Stationary/Transient significa inicializacion RANS estacionaria seguida de URANS transitorio. "
            "El deltaT introducido en modo adaptativo es un techo fisico: OpenFOAM puede reducirlo por Courant, nunca superarlo."
        )
        closed_col, open_col = st.columns(2)
        with closed_col:
            st.markdown("### Closed")
            edited = render_solver_field_set(edited, "solver", ["time_step_mode", "ddt_scheme"])
            if edited.get("time_step_mode") == "fixed":
                edited = render_solver_field_set(edited, "solver", ["deltaT_star"])
            else:
                edited = render_solver_field_set(edited, "solver", ["maxDeltaT_star", "maxCo"])
                st.caption(f"Semilla interna deltaT*: {min(float(edited.get('deltaT_star', 0.0)), float(edited.get('maxDeltaT_star', 0.0))):.6g}")
            edited = render_solver_field_set(
                edited, "solver",
                ["endTime_star", "n_outer_correctors", "n_correctors", "n_non_orthogonal_correctors", "transient_velocity_divergence_scheme", "transient_turbulence_divergence_scheme"],
            )
            edited["outer_corrector_residual_control"] = render_outer_residual_control(
                dict(edited.get("outer_corrector_residual_control") or {}),
                "solver.closed.outer",
                ("U", "nuTilda"),
            )
            with st.expander("RANS initialization (Closed)", expanded=False):
                edited = render_solver_field_set(edited, "solver", ["steady_initialization_enabled", "steady_max_iterations", "steady_write_interval_iterations"])
                edited["steady_residual_control"] = render_solver_field_set(dict(edited.get("steady_residual_control") or {}), "solver.steady_residual_control", ["p", "U", "nuTilda"])
                edited["steady_numerics"] = render_solver_field_set(dict(edited.get("steady_numerics") or {}), "solver.steady_numerics", ["n_non_orthogonal_correctors", "p_relaxation", "U_relaxation", "nuTilda_relaxation", "velocity_divergence_scheme", "turbulence_divergence_scheme"])
            with st.expander("Temporal budget (Closed)", expanded=False):
                edited["temporal_accuracy"] = render_temporal_accuracy_editor(dict(edited.get("temporal_accuracy") or {}), "solver.temporal_accuracy", edited)
        with open_col:
            st.markdown("### Open")
            open_profile = render_solver_field_set(open_profile, "solver.topology_profiles.open_internal_cavity", ["velocity_source", "time_step_mode"])
            if open_profile.get("time_step_mode") == "fixed":
                open_profile = render_solver_field_set(open_profile, "solver.topology_profiles.open_internal_cavity", ["deltaT_star"])
            else:
                open_profile = render_solver_field_set(open_profile, "solver.topology_profiles.open_internal_cavity", ["maxDeltaT_star", "maxCo"])
                st.caption(f"Semilla interna deltaT*: {min(float(open_profile.get('deltaT_star', 0.0)), float(open_profile.get('maxDeltaT_star', 0.0))):.6g}")
            open_profile = render_solver_field_set(
                open_profile, "solver.topology_profiles.open_internal_cavity",
                ["endTime_star", "n_outer_correctors", "n_correctors", "n_non_orthogonal_correctors", "transient_velocity_divergence_scheme", "transient_turbulence_divergence_scheme"],
            )
            open_profile["outer_corrector_residual_control"] = render_outer_residual_control(
                dict(open_profile.get("outer_corrector_residual_control") or {}),
                "solver.open.outer",
                ("U", "p"),
            )
            with st.expander("RANS initialization (Open)", expanded=False):
                open_profile = render_solver_field_set(open_profile, "solver.topology_profiles.open_internal_cavity", ["steady_initialization_enabled", "steady_max_iterations", "steady_write_interval_iterations"])
                open_profile["steady_residual_control"] = render_solver_field_set(dict(open_profile.get("steady_residual_control") or {}), "solver.topology_profiles.open_internal_cavity.steady_residual_control", ["p", "U", "nuTilda"])
                open_profile["steady_numerics"] = render_solver_field_set(dict(open_profile.get("steady_numerics") or {}), "solver.topology_profiles.open_internal_cavity.steady_numerics", ["n_non_orthogonal_correctors", "p_relaxation", "U_relaxation", "nuTilda_relaxation", "velocity_divergence_scheme", "turbulence_divergence_scheme"])
            with st.expander("Temporal budget (Open)", expanded=False):
                effective_open = dict(edited)
                effective_open.update(open_profile)
                open_profile["temporal_accuracy"] = render_temporal_accuracy_editor(dict(open_profile.get("temporal_accuracy") or {}), "solver.topology_profiles.open_internal_cavity.temporal_accuracy", effective_open)
        with st.expander("Advanced transport correction", expanded=False):
            st.caption("false corrige transporte/turbulencia en cada outer; true solo en el ultimo. El default se conserva hasta completar la comparacion cientifica.")
            adv_closed, adv_open = st.columns(2)
            with adv_closed:
                edited = render_solver_field_set(edited, "solver", ["transport_correction_final"])
            with adv_open:
                open_profile = render_solver_field_set(open_profile, "solver.topology_profiles.open_internal_cavity", ["transport_correction_final"])

    with tabs[2]:
        st.caption(
            "Los campos volumetricos se escriben cada ~2000 pasos fisicos solicitados. Fuerzas, residuos y Courant/deltaT "
            "se conservan en cada iteracion y purgeWrite no elimina esos historiales."
        )
        edited = render_solver_field_set(
            edited, "solver",
            ["field_write_control", "field_write_step_equivalent", "purgeWrite", "average_from_fraction", "velocity_profile_stations_xc", "velocity_profile_sample_points"],
        )
        open_profile = render_solver_field_set(open_profile, "solver.topology_profiles.open_internal_cavity", ["purgeWrite"])
        st.success("Historiales continuos: forceCoeffs, residuos y Courant/deltaT. purgeWrite afecta solo a campos volumetricos.")

    with tabs[3]:
        edited = render_solver_field_set(edited, "solver", ["preset_id", "screening_note"])
        st.metric("Solver config schema", _workflow_backend.SOLVER_CONFIG_SCHEMA_VERSION)
        st.markdown(
            "**Propiedad de datos**\n\n"
            "- Reynolds, Mach y propiedades del fluido: `case_package/physical_config.json`.\n"
            "- Cuerda: manifiesto de la variante del CFD Case.\n"
            "- Numerica y escritura: esta configuracion schema 15.\n"
            "- Cada caso escrito conserva `applied_solver_configuration.json`."
        )

    profiles["open_internal_cavity"] = open_profile
    edited["topology_profiles"] = profiles
    submitted = st.button("Guardar configuracion del solver", type="primary", key="save-solver-configuration")
    if submitted:
        edited["config_schema_version"] = _workflow_backend.SOLVER_CONFIG_SCHEMA_VERSION
        backup = save_config(ROOT, "solver", edited)
        st.success("Configuracion Closed/Open schema 15 guardada.")
        if backup:
            st.caption(f"Copia anterior: {backup}")
    return edited


def config_editor(
    name: str,
    title: str,
    descriptions: dict[str, str] | None = None,
    hidden_keys: set[str] | None = None,
) -> dict[str, Any]:
    data = load_config(ROOT, name)
    hidden_keys = hidden_keys or set()
    visible_data = {key: value for key, value in data.items() if key not in hidden_keys}
    st.subheader(title)
    st.caption(str(config_path(ROOT, name)))
    if name == "solver":
        transfer_columns = st.columns(2)
        transfer_columns[0].download_button(
            "Exportar configuracion del solver",
            data=json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            file_name="cfd2d_solver_config.json",
            mime="application/json",
            width="stretch",
        )
        uploaded = transfer_columns[1].file_uploader(
            "Importar configuracion del solver",
            type=["json"],
            key=revisioned_widget_key("solver-config-upload"),
            help="Carga una instantanea JSON. Los campos null se conservan y las entradas opcionales se omiten al escribir OpenFOAM.",
        )
        if uploaded is not None:
            try:
                imported = json.loads(uploaded.getvalue().decode("utf-8-sig"))
                if not isinstance(imported, dict):
                    raise TypeError("El archivo debe contener un objeto JSON.")
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                st.error(f"No se puede importar la configuracion: {exc}")
            else:
                if st.button("Aplicar archivo importado", type="primary", key="apply-imported-solver-config"):
                    backup = save_config(ROOT, "solver", imported)
                    st.session_state["_config_ui_revision"] = int(st.session_state.get("_config_ui_revision", 0)) + 1
                    st.session_state["_configuration_reload_notice"] = (
                        f"Configuracion del solver importada. Copia anterior: {backup}" if backup
                        else "Configuracion del solver importada."
                    )
                    st.rerun()
    with st.form(f"config-form-{name}"):
        edited_visible = render_nested_object(visible_data, name, descriptions)
        submitted = st.form_submit_button("Guardar configuracion", type="primary")
    edited = dict(data)
    edited.update(edited_visible)
    if submitted:
        backup = save_config(ROOT, name, edited)
        st.success("Configuracion guardada y validada como JSON.")
        if backup:
            st.caption(f"Copia anterior: {backup}")
    return edited


def update_workflow_sections(**sections: dict[str, Any]) -> None:
    """Persist section-owned values without exposing one duplicate mega-form."""
    data = load_config(ROOT, "workflow")
    for name, values in sections.items():
        current = dict(data.get(name) or {})
        current.update(values)
        data[name] = current
    save_config(ROOT, "workflow", data)


def workflow_safeguards_editor() -> None:
    """Show only cross-stage policy; stage parameters live on their own pages."""
    data = load_config(ROOT, "workflow")
    safeguards = dict(data.get("safeguards") or {})
    st.subheader("Funcionamiento general y salvaguardas")
    st.caption(
        "Las condiciones CFD, la malla, el solver y el postproceso se editan en sus pestanas. "
        "Aqui solo se guardan decisiones que gobiernan el flujo completo."
    )
    with st.form("workflow-safeguards-form"):
        edited = render_nested_object(safeguards, "workflow.safeguards")
        submitted = st.form_submit_button("Guardar salvaguardas", type="primary")
    if submitted:
        update_workflow_sections(safeguards=edited)
        st.success("Salvaguardas generales guardadas.")


def render_selected_sections(
    data: dict[str, Any],
    sections: list[str],
    *,
    prefix: str,
    intros: dict[str, str] | None = None,
) -> dict[str, Any]:
    edited: dict[str, Any] = {}
    intros = intros or {}
    for section in sections:
        if section not in data:
            continue
        st.markdown(f"**{section.replace('_', ' ').title()}**")
        if section in intros:
            st.caption(intros[section])
        value = data[section]
        if isinstance(value, dict):
            groups = PROJECT_FIELD_GROUPS.get(section) if prefix == "project" else None
            edited[section] = (
                render_grouped_object(value, f"{prefix}.{section}", groups)
                if groups
                else render_nested_object(value, f"{prefix}.{section}")
            )
        else:
            edited[section] = value_widget(section, value, f"cfg:{prefix}:{section}")
        st.divider()
    return edited


def project_config_editor(tab_layout: list[tuple[str, list[str]]] | None = None) -> dict[str, Any]:
    name = "project"
    data = load_config(ROOT, name)
    st.subheader("Preprocesador y geometria CATIA")
    st.caption(str(config_path(ROOT, name)))
    layout = tab_layout or PROJECT_TAB_LAYOUT
    tab_labels = [label for label, _ in layout]
    selected_label = st.segmented_control(
        "Seccion de geometria",
        tab_labels,
        default=tab_labels[0],
        key=revisioned_widget_key("project-config-section"),
        label_visibility="collapsed",
    ) or tab_labels[0]
    selected_sections = dict(layout)[selected_label]
    st.caption(PROJECT_TAB_INTROS.get(selected_label, ""))
    system_data = load_config(ROOT, "catia_system")
    suspension_data = dict(system_data.get("suspension") or {})
    constraint_data = dict(system_data.get("constraints") or {})
    edit_anhedral_rule = selected_label == "Canopy"
    with st.form("project-config-form"):
        edited = dict(data)
        derive_anhedral = bool(suspension_data.get("derive_anhedral_from_R_over_b", True))
        radius_over_span = float(constraint_data.get("R_over_b", 0.8))
        if edit_anhedral_rule:
            st.markdown("**Anhedral: valor directo o derivado de R/b**")
            st.caption(
                "Activado calcula el anhedral desde R/b al preprocesar. Desactivado usa directamente "
                "canopy_geometry.anhedral_deg. El valor no seleccionado se conserva para poder alternar."
            )
            anhedral_columns = st.columns(2)
            derive_anhedral = anhedral_columns[0].toggle(
                "Derivar anhedral desde R/b",
                value=derive_anhedral,
                key=revisioned_widget_key("project:canopy:derive-anhedral"),
                help="ON: R/b gobierna el arco y anhedral. OFF: se usa anhedral_deg de Dimensiones principales.",
            )
            radius_over_span = float(anhedral_columns[1].number_input(
                "R/b objetivo",
                value=radius_over_span,
                min_value=0.05,
                format="%.6g",
                key=revisioned_widget_key("project:canopy:r-over-b"),
                help="R es la distancia recta corona-confluencia y b la envergadura efectiva. Solo gobierna la geometria cuando la derivacion esta activa.",
            ))
            st.divider()
        edited.update(render_selected_sections(
            data,
            selected_sections,
            prefix=name,
            intros=PROJECT_SECTION_INTROS,
        ))
        submitted = st.form_submit_button("Guardar configuracion de preproceso", type="primary")
    if submitted:
        backup = save_config(ROOT, name, edited)
        if edit_anhedral_rule:
            suspension_data["derive_anhedral_from_R_over_b"] = derive_anhedral
            constraint_data["R_over_b"] = radius_over_span
            system_data["suspension"] = suspension_data
            system_data["constraints"] = constraint_data
            save_config(ROOT, "catia_system", system_data)
        st.success("Configuracion CATIA/preprocesador guardada. Las opciones se aplicaran en la siguiente ejecucion.")
        if backup:
            st.caption(f"Copia anterior: {backup}")
    return edited


def geometry_2d_workspace_editor(selected_case_manifest: dict[str, Any]) -> None:
    """Edit the exact profile/TE/crossport DTO consumed by preprocessing."""
    project = load_config(ROOT, "project")
    inlet = load_config(ROOT, "inlet_design")
    catalogue = load_profile_catalog(ROOT)
    entries = list(catalogue.get("profiles") or [])
    by_path = {str(item.get("source_path")): item for item in entries if item.get("source_path")}
    profile_paths = sorted(by_path)
    profiles = dict(project.get("profile_inputs") or {})
    airfoil = dict(project.get("airfoil_processing") or {})
    crossports = dict(project.get("crossports") or {})

    st.subheader("Perfil, trailing edge y crossports")
    st.caption(
        "La vista previa y el preprocesador resuelven el mismo DTO. Los perfiles de validacion se muestran "
        "para trazabilidad y no se sustituyen al importar un perfil nuevo."
    )
    uploaded = st.file_uploader("Importar coordenadas al catalogo", type=["dat", "csv", "txt"])
    if uploaded is not None and st.button("Validar e importar perfil", type="primary"):
        try:
            entry = import_profile(
                ROOT,
                uploaded.name,
                uploaded.getvalue(),
                work_case_id=str(selected_case_manifest.get("work_case_id") or "") or None,
            )
            st.success(f"Perfil importado sin modificar el original: {entry['display_name']}")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    def profile_index(value: str) -> int:
        return profile_paths.index(value) if value in profile_paths else 0

    if not profile_paths:
        st.error("No hay perfiles validos en el catalogo del proyecto.")
        return
    current_dto = geometry_dto(project, inlet)
    holes = list((current_dto.get("crossports") or {}).get("holes") or [])
    hole_rows = [{
        "x/c": item.get("x"),
        "shape": item.get("shape"),
        "orientation": item.get("orientation"),
        "radius/c": item.get("radius_chord_frac"),
        "width/c": item.get("width_chord_frac"),
        "height/local thickness": item.get("height_thickness_frac"),
        "z fraction (optional)": item.get("z_center_fraction"),
        "points": item.get("points_per_loop"),
    } for item in holes]

    with st.form("geometry-2d-dto-form"):
        cols = st.columns(2)
        main_profile = cols[0].selectbox(
            "Perfil abierto en uso", profile_paths,
            index=profile_index(str(profiles.get("main_profile", profile_paths[0]))),
            format_func=lambda value: str(by_path[value].get("display_name") or value),
        )
        base_profile = cols[1].selectbox(
            "Perfil base cerrado", profile_paths,
            index=profile_index(str(inlet.get("base_profile") or profiles.get("reference_uncut_profile", profile_paths[0]))),
            format_func=lambda value: str(by_path[value].get("display_name") or value),
        )
        validation_cols = st.columns(4)
        validation_cols[0].text_input("Validacion LS open", value=str(profiles.get("main_profile", "")), disabled=True)
        validation_cols[1].text_input("Validacion LS closed", value=str(profiles.get("reference_uncut_profile", "")), disabled=True)
        validation_cols[2].text_input("Ross standard", value=str(profiles.get("ross_standard_profile", "")), disabled=True)
        validation_cols[3].text_input("Ross minimum", value=str(profiles.get("ross_minimum_profile", "")), disabled=True)

        st.markdown("**Trailing edge**")
        te_modes = list(TE_LABELS)
        te_mode = st.segmented_control(
            "Tratamiento TE", te_modes,
            default=str(airfoil.get("te_closure_mode", "rounded")),
            format_func=lambda value: TE_LABELS[value],
        ) or "rounded"
        te_cols = st.columns(3)
        te_points = te_cols[0].number_input("Puntos de redondeo", min_value=5, value=int(airfoil.get("te_rounding_points", 20)), disabled=te_mode != "rounded")
        thin_solid = te_cols[1].toggle("Tela como solido fino", value=bool(airfoil.get("model_zero_thickness_as_thin_solid", True)))
        fabric_thickness = te_cols[2].number_input("Espesor tela / cuerda", min_value=1e-9, value=float(airfoil.get("fabric_thickness_chord", 1e-5)), format="%.6g", disabled=not thin_solid)
        with st.expander("Salvaguardas TE avanzadas", expanded=False):
            sharp_cols = st.columns(3)
            sharp_max = sharp_cols[0].number_input("Interseccion sharp maxima x/c", value=float(airfoil.get("sharp_te_intersection_max_x_c", 1.08)), format="%.6g")
            sharp_gap = sharp_cols[1].number_input("Gap seguro sharp / cuerda", min_value=1e-9, value=float(airfoil.get("sharp_te_safe_gap_chord", 1e-5)), format="%.6g")
            min_distance = sharp_cols[2].number_input("Distancia minima entre puntos [mm]", min_value=0.0, value=float(airfoil.get("min_spline_point_distance_mm", 0.01)), format="%.6g")

        st.markdown("**Crossports**")
        general = st.columns(4)
        enabled = general[0].toggle("Activar", value=bool(crossports.get("enable_crossports", True)))
        apply_to = general[1].selectbox("Apply to", ["all_internal", "loaded", "nonloaded"], index=["all_internal", "loaded", "nonloaded"].index(str(crossports.get("apply_to", "all_internal"))) if str(crossports.get("apply_to", "all_internal")) in ["all_internal", "loaded", "nonloaded"] else 0)
        centerline = general[2].selectbox("Centerline mode", ["chordline", "profile_midline"], index=["chordline", "profile_midline"].index(str(crossports.get("centerline_mode", "chordline"))) if str(crossports.get("centerline_mode", "chordline")) in ["chordline", "profile_midline"] else 0)
        clearance = general[3].number_input("Edge clearance / local thickness", min_value=0.0, max_value=0.49, value=float(crossports.get("edge_clearance_fraction_local_thickness", 0.22)), format="%.5g")
        with st.expander("Generador de distribucion", expanded=False):
            generator = st.columns(4)
            position_mode = generator[0].selectbox("Modo", ["standard_3", "equidistant", "custom"], index=["standard_3", "equidistant", "custom"].index(str(crossports.get("position_mode", "standard_3"))))
            count = generator[1].number_input("Numero de agujeros", min_value=1, max_value=20, value=int(crossports.get("count", 3)))
            x_start = generator[2].number_input("X start", min_value=0.02, max_value=0.98, value=float(crossports.get("x_start_chord", 0.25)), format="%.5g")
            x_end = generator[3].number_input("X end", min_value=0.02, max_value=0.98, value=float(crossports.get("x_end_chord", 0.70)), format="%.5g")
            st.caption("El generador conserva la distribucion reproducible; la tabla inferior es el DTO explicito ejecutado por el backend.")
        edited_holes = st.data_editor(
            pd.DataFrame(hole_rows), num_rows="dynamic", width="stretch", hide_index=True,
            column_config={
                "shape": st.column_config.SelectboxColumn(options=["circle", "ellipse"], required=True),
                "orientation": st.column_config.SelectboxColumn(options=["horizontal", "vertical", "auto"], required=True),
                "points": st.column_config.NumberColumn(min_value=12, step=1, required=True),
            },
        )
        save_geometry = st.form_submit_button("Guardar configuracion 2D", type="primary")
    if save_geometry:
        custom_specs = []
        for index, row in edited_holes.iterrows():
            if pd.isna(row.get("x/c")):
                continue
            spec = {
                "hole_id": f"crossport-{index + 1}", "x": float(row["x/c"]),
                "shape": str(row.get("shape") or "ellipse"),
                "orientation": str(row.get("orientation") or "horizontal"),
                "width_chord_frac": float(row.get("width/c") or 0.08),
                "height_thickness_frac": float(row.get("height/local thickness") or 0.15),
                "points_per_loop": int(row.get("points") or 32),
            }
            if not pd.isna(row.get("radius/c")):
                spec["radius_chord_frac"] = float(row["radius/c"])
            if not pd.isna(row.get("z fraction (optional)")):
                spec["z_center_fraction"] = float(row["z fraction (optional)"])
            custom_specs.append(spec)
        profiles["main_profile"] = main_profile
        profiles["reference_uncut_profile"] = base_profile
        airfoil.update({
            "te_closure_mode": te_mode, "te_rounding_points": int(te_points),
            "model_zero_thickness_as_thin_solid": bool(thin_solid),
            "fabric_thickness_chord": float(fabric_thickness),
            "sharp_te_intersection_max_x_c": float(sharp_max),
            "sharp_te_safe_gap_chord": float(sharp_gap),
            "min_spline_point_distance_mm": float(min_distance),
        })
        crossports.update({
            "enable_crossports": bool(enabled), "apply_to": apply_to,
            "centerline_mode": centerline,
            "edge_clearance_fraction_local_thickness": float(clearance),
            "position_mode": position_mode, "count": int(count),
            "x_start_chord": float(x_start), "x_end_chord": float(x_end),
            "custom_specs": custom_specs,
        })
        project.update({"profile_inputs": profiles, "airfoil_processing": airfoil, "crossports": crossports})
        inlet["base_profile"] = base_profile
        save_config(ROOT, "project", project)
        save_config(ROOT, "inlet_design", inlet)
        st.success("Geometria 2D guardada como una nueva revision pendiente del caso activo.")
        st.rerun()

    dto = geometry_dto(project, inlet)
    st.markdown("**Vista previa ejecutable**")
    preview = preview_series(ROOT, dto)
    chart_rows = []
    for name, rows in preview.items():
        chart_rows.extend(
            {"x/c": row["x"], "z/c": row["z"], "serie": name, "orden": order}
            for order, row in enumerate(rows)
        )
    if chart_rows:
        st.vega_lite_chart(
            pd.DataFrame(chart_rows),
            {"mark": {"type": "line", "clip": True}, "height": 360,
             "encoding": {
                 "x": {"field": "x/c", "type": "quantitative", "scale": {"zero": False}},
                 "y": {"field": "z/c", "type": "quantitative", "scale": {"zero": False}},
                 "color": {"field": "serie", "type": "nominal"},
                 "detail": {"field": "serie", "type": "nominal"},
                 "order": {"field": "orden", "type": "quantitative"},
             }},
            width="stretch",
        )
    st.caption(
        f"TE: {dto['trailing_edge']['label']} | crossports: {len(dto['crossports']['holes'])} | "
        f"centerline: {dto['crossports']['centerline_mode']}"
    )
    with st.expander("Project Paths", expanded=False):
        render_records_table([{"nombre": key, "ruta": value} for key, value in (project.get("project_paths") or {}).items()])
    with st.expander("DTO exacto de geometria", expanded=False):
        st.json(dto)


def inlet_design_editor() -> None:
    """Render the XFOIL-backed 2D inlet design as a traceable geometry stage."""
    st.subheader("Diseno 2D del corte ram-air")
    st.caption(
        "Repaneliza el perfil cerrado con XFOIL, calcula una polar viscosa al Reynolds/Mach de diseno, "
        "localiza el punto de remanso en cada solucion convergida y recorta la envolvente del borde de ataque. "
        "XFLR5 puede usarse despues para inspeccion manual, pero la ejecucion automatica usa la consola reproducible de XFOIL."
    )
    config = load_config(ROOT, "inlet_design")
    workflow_conditions = (load_config(ROOT, "workflow").get("case_conditions") or {})
    candidates = sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for suffix in ("*.dat", "*.csv")
            for path in project_path(ROOT, "profiles").glob(suffix)
        if path.is_file() and "_Cut_" not in path.name and not path.name.lower().startswith("ross_")
    )
    current_profile = str(config.get("base_profile", "Airfoil Profiles/NASA LS1-0417.dat"))
    if current_profile not in candidates:
        candidates.insert(0, current_profile)
    with st.form("inlet-design-form"):
        st.markdown("**Perfil cerrado y condicion aerodinamica**")
        cols = st.columns(4)
        base_profile = cols[0].selectbox("Perfil base cerrado", candidates, index=candidates.index(current_profile))
        mode = cols[1].selectbox(
            "Metodo de corte",
            ["optimized_cl_window", "standard_full_polar"],
            index=["optimized_cl_window", "standard_full_polar"].index(str(config.get("design_mode", "optimized_cl_window"))),
            format_func=lambda item: {"optimized_cl_window": "Optimizado por intervalo CL", "standard_full_polar": "Estandar: polar completa"}[item],
            help="Estandar usa todos los angulos convergidos. Optimizado conserva solo los puntos cuyo CL cae dentro del intervalo seleccionado.",
        )
        use_case = cols[2].toggle("Usar Re/Mach del caso CFD", value=True)
        output_action = cols[3].selectbox(
            "Salida previa",
            ["archive", "delete", "keep"],
            index=["archive", "delete", "keep"].index(str(config.get("existing_output_action", "archive"))),
            format_func=lambda item: {"archive": "Archivar", "delete": "Eliminar", "keep": "Detener si existe"}[item],
        )
        cols = st.columns(4)
        reynolds_default = workflow_conditions.get("reynolds", config.get("reynolds", 4.0e6)) if use_case else config.get("reynolds", 4.0e6)
        mach_default = workflow_conditions.get("mach", config.get("mach", 0.1)) if use_case else config.get("mach", 0.1)
        reynolds = cols[0].number_input("Reynolds", value=float(reynolds_default), min_value=1.0, format="%.8g")
        mach = cols[1].number_input("Mach", value=float(mach_default), min_value=0.0, max_value=0.79, format="%.6g")
        panel_count = cols[2].number_input(
            "Paneles XFOIL", min_value=80, max_value=400, value=int(config.get("panel_count", 200)), step=10,
            help="PPAR N. XFOIL redistribuye estos nodos por curvatura; 200 es el objetivo de diseno actual.",
        )
        iterations = cols[3].number_input("Iteraciones XFOIL", min_value=20, max_value=1000, value=int(config.get("xfoil_iteration_limit", 180)), step=20)
        st.markdown("**Barrido y seleccion de puntos de remanso**")
        cols = st.columns(5)
        alpha_start = cols[0].number_input("Alpha inicial [deg]", value=float(config.get("alpha_start_deg", -5.0)))
        alpha_end = cols[1].number_input("Alpha final [deg]", value=float(config.get("alpha_end_deg", 15.0)))
        alpha_step = cols[2].number_input("Paso alpha [deg]", min_value=0.05, value=float(config.get("alpha_step_deg", 0.5)))
        cl_min = cols[3].number_input("CL minimo", value=float(config.get("cl_min", 0.5)), disabled=mode != "optimized_cl_window")
        cl_max = cols[4].number_input("CL maximo", value=float(config.get("cl_max", 1.5)), disabled=mode != "optimized_cl_window")
        with st.expander("Panelado y salvaguardas avanzadas", expanded=False):
            cols = st.columns(4)
            bunching = cols[0].number_input("PPAR bunching", min_value=0.1, value=float(config.get("panel_bunching", 1.0)), format="%.5g")
            te_le_ratio = cols[1].number_input("Densidad TE/LE", min_value=0.01, value=float(config.get("te_le_density_ratio", 0.15)), format="%.5g")
            refined_ratio = cols[2].number_input("Densidad zona refinada/LE", min_value=0.01, value=float(config.get("refined_area_le_density_ratio", 0.20)), format="%.5g")
            timeout_s = cols[3].number_input("Timeout XFOIL [s]", min_value=30, value=int(config.get("xfoil_timeout_s", 420)), step=30)
            cols = st.columns(3)
            search_x = cols[0].number_input("Ventana remanso x/c", min_value=0.01, max_value=0.40, value=float(config.get("stagnation_search_x_over_c", 0.18)), format="%.5g")
            min_cp = cols[1].number_input("Cp minimo de remanso", value=float(config.get("minimum_stagnation_cp", 0.7)), format="%.5g")
            margin = cols[2].number_input("Margen adicional [paneles]", min_value=0, max_value=20, value=int(config.get("cut_margin_panel_points", 1)))
        save_only = st.form_submit_button("Guardar parametros")
        run_design = st.form_submit_button("Generar perfil cortado", type="primary")
    edited = dict(config)
    edited.update({
        "base_profile": base_profile, "design_mode": mode, "reynolds": float(reynolds), "mach": float(mach),
        "panel_count": int(panel_count), "panel_bunching": float(bunching), "te_le_density_ratio": float(te_le_ratio),
        "refined_area_le_density_ratio": float(refined_ratio), "alpha_start_deg": float(alpha_start),
        "alpha_end_deg": float(alpha_end), "alpha_step_deg": float(alpha_step), "cl_min": float(cl_min),
        "cl_max": float(cl_max), "xfoil_iteration_limit": int(iterations), "xfoil_timeout_s": int(timeout_s),
        "stagnation_search_x_over_c": float(search_x), "minimum_stagnation_cp": float(min_cp),
        "cut_margin_panel_points": int(margin), "existing_output_action": output_action,
    })
    if save_only or run_design:
        save_config(ROOT, "inlet_design", edited)
        st.success("Configuracion 2D guardada.")
    check_col, info_col = st.columns([1, 3])
    if check_col.button("Verificar XFOIL"):
        start_job("xfoil_environment", xfoil_check_command(ROOT))
    info_col.caption("La verificacion ejecuta y cierra XFOIL; no inicia XFLR5 ni modifica perfiles.")
    if run_design:
        start_job("inlet_design", inlet_design_command(ROOT))

    metadata_files = latest_files(
        ROOT / "CFD_2D/CFD_2D_inputs/inlet_design", ["*/inlet_design_metadata.json"], 1
    )
    if metadata_files:
        metadata = read_json(metadata_files[0], {}) or {}
        cut = metadata.get("cut") or {}
        cols = st.columns(4)
        cols[0].metric("Estado", metadata.get("status", "UNKNOWN"))
        cols[1].metric("Polar convergida", f"{metadata.get('converged_alpha_count', 0)}/{metadata.get('requested_alpha_count', 0)}")
        cols[2].metric("Puntos de diseno", int(metadata.get("selected_stagnation_count", 0) or 0))
        cols[3].metric("Apertura", f"{float(cut.get('inlet_gap_percent_chord', 0.0)):.2f}% c")
        profile_relative = metadata.get("generated_profile")
        action_cols = st.columns([1, 3])
        if profile_relative and action_cols[0].button("Usar como main_profile"):
            project = load_config(ROOT, "project")
            profile_inputs = dict(project.get("profile_inputs") or {})
            profile_inputs["main_profile"] = str(profile_relative)
            project["profile_inputs"] = profile_inputs
            save_config(ROOT, "project", project)
            st.success(f"Perfil principal actualizado: {profile_relative}")
        action_cols[1].caption(str(metadata_files[0]))
        preview = metadata_files[0].parent / "inlet_design_summary.png"
        if preview.is_file():
            st.image(str(preview), caption="Polar convergida, envolvente de remanso y perfil abierto generado", width="stretch")
        show_json_report(metadata_files[0], "Metadatos completos del diseno 2D")
    st.divider()


def catia_system_config_editor() -> dict[str, Any]:
    name = "catia_system"
    data = load_config(ROOT, name)
    st.subheader("Sistema opcional: suspension, estabilizadores y payload")
    st.caption(str(config_path(ROOT, name)))
    st.caption(
        "Este JSON alimenta el preprocesador; CATIA recibe despues tablas y parametros derivados. "
        "Las listas de bancos, cascadas y offsets se editan como JSON para conservar su estructura."
    )
    tab_labels = [label for label, _ in SYSTEM_TAB_LAYOUT]
    selected_label = st.segmented_control(
        "Seccion del sistema CAD",
        tab_labels,
        default=tab_labels[0],
        key=revisioned_widget_key("catia-system-config-section"),
        label_visibility="collapsed",
    ) or tab_labels[0]
    selected_sections = dict(SYSTEM_TAB_LAYOUT)[selected_label]
    st.caption(SYSTEM_TAB_INTROS.get(selected_label, ""))
    with st.form("catia-system-config-form"):
        edited = dict(data)
        render_data = dict(data)
        sections_to_render = list(selected_sections)
        suspension_enabled = bool((data.get("suspension") or {}).get("enabled", False))
        if selected_label == "Suspension":
            st.markdown("**Activacion del sistema**")
            suspension_enabled = st.toggle(
                "ENABLE SUSPENSION SYSTEM",
                value=suspension_enabled,
                help="Interruptor unico. Al guardar sincroniza suspension.enabled y optional_modules.enable_suspension_lines para evitar estados contradictorios.",
            )
            suspension = dict(data.get("suspension") or {})
            constraints = dict(data.get("constraints") or {})
            suspension["enabled"] = suspension_enabled
            edited["suspension"] = suspension
            edited["constraints"] = constraints
            render_suspension = {key: value for key, value in suspension.items() if key not in {"enabled", "derive_anhedral_from_R_over_b"}}
            render_constraints = {key: value for key, value in constraints.items() if key != "R_over_b"}
            render_data["suspension"] = render_suspension
            render_data["constraints"] = render_constraints
            st.divider()
        updates = render_selected_sections(render_data, sections_to_render, prefix=name)
        for section, values in updates.items():
            if isinstance(values, dict) and isinstance(edited.get(section), dict):
                merged = dict(edited[section])
                merged.update(values)
                edited[section] = merged
            else:
                edited[section] = values
        submitted = st.form_submit_button("Guardar configuracion del sistema CAD", type="primary")
    if submitted:
        backup = save_config(ROOT, name, edited)
        project = load_config(ROOT, "project")
        optional_modules = dict(project.get("optional_modules") or {})
        optional_modules["enable_suspension_lines"] = suspension_enabled
        project["optional_modules"] = optional_modules
        save_config(ROOT, "project", project)
        st.success("Configuracion del sistema guardada; se copiara como snapshot trazable en CATIA/Inputs.")
        if backup:
            st.caption(f"Copia anterior: {backup}")
    return edited


def active_variant_chord_m(variant: str) -> float | None:
    candidates = [
        ROOT / "CFD_2D/CFD_2D_inputs/case_package" / variant / "manifest.json",
        ROOT / "CFD_2D/CFD_2D_inputs/geometry" / variant / "manifest.json",
    ]
    for path in candidates:
        data = read_json(path, {}) or {}
        try:
            chord = float(data.get("chord_m"))
        except (TypeError, ValueError):
            continue
        if chord > 0.0:
            return chord
    return None


def estimated_first_cell_height_m(chord_m: float, target_y_plus: float) -> dict[str, float]:
    """Mirror the builder's documented turbulent flat-plate y1 estimate."""
    workflow = load_config(ROOT, "workflow")
    conditions = workflow.get("case_conditions", {}) or {}
    reynolds = max(float(conditions.get("reynolds", 4.0e6)), 1.0)
    rho = max(float(conditions.get("rho_kg_m3", 1.225)), 1.0e-12)
    mu = max(float(conditions.get("mu_pa_s", 1.81e-5)), 1.0e-15)
    return first_cell_height_from_yplus(
        target_y_plus=target_y_plus,
        reynolds=reynolds,
        rho_kg_m3=rho,
        mu_pa_s=mu,
        chord_m=chord_m,
    )


def variant_has_open_inlet(variant: str) -> bool:
    manifest = read_json(
        ROOT / "CFD_2D" / "CFD_2D_inputs" / "case_package" / variant / "manifest.json",
        {},
    ) or {}
    return bool(manifest.get("has_ram_air_opening_feature", "open" in variant.lower()))


def mesh_config_editor(variant: str) -> dict[str, Any]:
    data = load_config(ROOT, "mesh")
    if "domain_type" not in data:
        workflow_domain = str((load_config(ROOT, "workflow").get("geometry") or {}).get("domain", "circular_50c"))
        data["domain_type"] = workflow_domain if workflow_domain in DOMAIN_DEFAULTS else "circular_50c"
    # Native WSL projects retain the user's editable JSON during code updates.
    # Supply new controls in memory and persist them only after an explicit Save.
    # A closed-focused or older active JSON may omit valid open-airfoil keys.
    # Seed every supported fine baseline in memory so all controls remain
    # editable; nothing is persisted until the user presses Save.
    for key, value in mesh_level_values("fine").items():
        data.setdefault(key, value)
    for key, value in MESH_UI_DEFAULTS.items():
        data.setdefault(key, value)
    is_open_profile = variant_has_open_inlet(variant)
    if is_open_profile:
        st.markdown("**Representacion del perfil abierto**")
        open_representation_options = CHOICES["open_geometry_representation"]
        current_representation = str(
            data.get("open_geometry_representation", "zero_thickness_base_profile")
        )
        if current_representation not in open_representation_options:
            current_representation = "zero_thickness_base_profile"
        selected_representation = st.segmented_control(
            MESH_FIELD_LABELS["open_geometry_representation"],
            open_representation_options,
            default=current_representation,
            format_func=lambda value: CHOICE_LABELS["open_geometry_representation"].get(value, value),
            help=MESH_DESCRIPTIONS.get("open_geometry_representation"),
            key=revisioned_widget_key("mesh-open-geometry-representation"),
        ) or current_representation
        data["open_geometry_representation"] = selected_representation
        st.caption(
            "El cambio actualiza inmediatamente las subsecciones inferiores. Pulsa Guardar para "
            "persistirlo antes de generar la malla."
        )
    selected_domain = str(data.get("domain_type", "circular_50c"))
    if selected_domain not in DOMAIN_DEFAULTS:
        selected_domain = "circular_50c"
        data["domain_type"] = selected_domain
    domain_keys = ["domain_type", *DOMAIN_CONFIG_KEYS[selected_domain]]
    if data.get("open_geometry_representation") == "zero_thickness_base_profile":
        inlet_alignment_keys = [
            "open_zero_thickness_contour_target_nodes",
            "open_zero_thickness_te_transfinite_min_nodes",
            "open_base_profile_variant",
            "open_base_inlet_alignment_mode",
        ]
        if data.get("open_base_inlet_alignment_mode") == "endpoint_blend":
            inlet_alignment_keys.append("open_base_inlet_blend_fraction")
        cavity_strategy_keys = ["open_cavity_inlet_size_strategy"]
        cavity_strategy = str(
            data.get("open_cavity_inlet_size_strategy", "hybrid_boundary_extension")
        )
        if cavity_strategy == "hybrid_boundary_extension":
            cavity_strategy_keys.extend(
                [
                    "open_zero_thickness_inlet_normal_y1_factor",
                    "open_internal_inlet_matching_transition_chord",
                    "open_cavity_inlet_extension_power",
                    "open_internal_inlet_dist_max_chord",
                ]
            )
        elif cavity_strategy == "boundary_extension":
            cavity_strategy_keys.extend(
                [
                    "open_cavity_inlet_extension_power",
                    "open_internal_inlet_dist_max_chord",
                ]
            )
        elif cavity_strategy == "staged_explicit":
            cavity_strategy_keys.extend(
                [
                    "open_zero_thickness_inlet_normal_y1_factor",
                    "open_internal_inlet_matching_transition_chord",
                    "open_internal_inlet_matching_size_factor",
                    "open_internal_inlet_near_transition_chord",
                    "open_internal_inlet_intermediate_size_chord",
                    "open_internal_inlet_dist_max_chord",
                ]
            )
        open_sections = [
            ("Boundary Layer exterior", ["open_use_yplus_first_cell_height", "open_first_cell_height_m", "open_boundary_layer_layers", "open_boundary_layer_growth", "open_boundary_layer_total_thickness_chord", "open_recombine_boundary_layer", "open_boundary_layer_aniso_max_deg"]),
            ("Contorno exterior continuo", inlet_alignment_keys),
            ("Pared y volumen interior", ["open_inner_wall_node_factor", "open_inner_wall_min_nodes", "open_inner_wall_end_bump_enabled", "open_inner_wall_end_bump_strength", "open_cavity_wall_size_chord", "open_cavity_wall_transition_chord", "open_cavity_size_chord"]),
            ("Transicion interior desde el inlet", cavity_strategy_keys),
            ("Trailing Edge interior", ["open_inner_te_node_factor", "open_inner_te_min_nodes", "open_internal_te_refinement_enabled", "open_internal_te_dist_max_chord", "open_internal_te_size_factor"]),
            ("Transicion exterior y farfield", ["open_near_wall_size_from_bl", "open_near_wall_size_chord", "open_near_wall_size_bl_factor", "open_nearfield_refinement_enabled", "open_transition_sigmoid_enabled", "open_nearfield_dist_min_chord", "open_nearfield_intermediate_dist_chord", "open_nearfield_dist_max_chord", "open_nearfield_intermediate_size_chord", "open_nearfield_outer_size_chord", "open_farfield_transition_dist_chord", "open_farfield_size_chord"]),
        ]
    else:
        open_sections = [
            ("Boundary Layer exterior", ["open_use_yplus_first_cell_height", "open_first_cell_height_m", "open_boundary_layer_layers", "open_boundary_layer_growth", "open_boundary_layer_total_thickness_chord", "open_recombine_boundary_layer", "open_boundary_layer_aniso_max_deg"]),
            ("Pared exterior: intrados y extrados", ["open_wall_curve_method", "open_surface_target_nodes", "open_surface_transfinite_multiplier", "open_surface_transfinite_progression", "open_wall_end_bump_enabled", "open_wall_end_bump_strength", "open_lip_transfinite_min_nodes", "open_surface_size_le_chord", "open_surface_size_lip_chord"]),
            ("Trailing Edge exterior", ["open_te_transfinite_min_nodes", "open_te_refinement_width_chord", "open_te_transition_distance_chord", "open_surface_size_te_chord"]),
            ("Topologia y union del inlet", ["open_inlet_boundary_layer_mode", "open_inlet_bridge_smoothing_enabled", "open_inlet_bridge_smoothing_handle_fraction", "open_inlet_transition_elements", "open_inlet_transition_growth", "open_inlet_connector_normal_nodes", "open_minimum_fabric_thickness_chord", "open_lip_cap_rounding_enabled", "open_lip_cap_rounding_points", "open_boundary_layer_lip_fan_points"]),
            ("Inlet fluido y refinamiento interior", ["open_inlet_marker_transfinite_nodes", "open_inlet_marker_bump_strength", "open_inlet_refinement_bridge_enabled", "open_internal_inlet_refinement_enabled", "open_internal_inlet_dist_min_chord", "open_internal_inlet_dist_max_chord", "open_internal_inlet_size_chord"]),
            ("Terminacion avanzada en labios", ["open_boundary_layer_trim_end_segments", "open_boundary_layer_trim_end_points"]),
            ("Pared y volumen interior", ["open_inner_wall_node_factor", "open_inner_wall_min_nodes", "open_inner_wall_end_bump_enabled", "open_inner_wall_end_bump_strength", "open_cavity_wall_size_chord", "open_cavity_wall_transition_chord", "open_cavity_size_chord"]),
            ("Trailing Edge interior", ["open_inner_te_node_factor", "open_inner_te_min_nodes", "open_internal_te_refinement_enabled", "open_internal_te_dist_max_chord", "open_internal_te_size_factor"]),
            ("Transicion exterior y farfield", ["open_near_wall_size_from_bl", "open_near_wall_size_chord", "open_near_wall_size_bl_factor", "open_nearfield_refinement_enabled", "open_nearfield_dist_min_chord", "open_nearfield_intermediate_dist_chord", "open_nearfield_dist_max_chord", "open_nearfield_intermediate_size_chord", "open_nearfield_outer_size_chord", "open_farfield_transition_dist_chord", "open_farfield_size_chord"]),
        ]
    global_boundary_keys = [
        "run_boundary_layer",
        "target_y_plus",
        "extrude_to_3d_for_openfoam",
        "spanwise_thickness_chord",
        "spanwise_layers",
    ]
    if data.get("open_geometry_representation") != "zero_thickness_base_profile":
        global_boundary_keys.insert(2, "fabric_thickness_chord")
    layout = {
        "General": [
            ("Dominio", domain_keys),
            ("Objetivo de capa limite y extrusion", global_boundary_keys),
            ("Ejecucion de Gmsh", ["gmsh_backend", "gmsh_threads", "gmsh_mesh_algorithm_2d", "gmsh_random_factor", "gmsh_random_seed"]),
            ("Limites y salvaguardas", ["max_cells", "max_internal_parse_mesh_size_mb", "max_internal_parse_elements", "min_cells_warning", "stop_if_negative_cells", "stop_if_geometry_invalid", "wake_refinement_enabled"]),
            ("Compatibilidad avanzada", ["config_schema_version", "geometry_mode"]),
        ],
        "Closed": [
            ("Boundary Layer", ["closed_use_yplus_first_cell_height", "closed_first_cell_height_m", "closed_boundary_layer_layers", "closed_boundary_layer_growth", "closed_boundary_layer_total_thickness_chord", "closed_recombine_boundary_layer", "closed_boundary_layer_aniso_max_deg", "closed_boundary_layer_intersect_metrics"]),
            ("Curvas Gmsh de la pared", ["closed_wall_curve_method", "closed_wall_target_nodes"]),
            ("Preprocesado geometrico del perfil", ["closed_profile_preprocess_enabled", "closed_profile_target_points", "closed_profile_min_spacing_chord"]),
            ("Redondeado geometrico del TE", ["closed_te_rounding_enabled", "closed_te_rounding_points", "closed_te_rounding_window_chord", "closed_te_rounding_min_gap_chord", "closed_te_refinement_width_chord", "closed_te_refinement_strength", "closed_te_refinement_max_weight"]),
            ("Discretizacion de malla en el TE", ["closed_te_target_nodes", "closed_te_transition_min_nodes", "closed_te_bump_strength"]),
            ("Sizes y transicion", ["closed_near_wall_size_from_bl", "closed_near_wall_size_chord", "closed_near_wall_size_bl_factor", "closed_nearfield_enabled", "closed_nearfield_dist_min_chord", "closed_nearfield_intermediate_dist_chord", "closed_nearfield_dist_max_chord", "closed_nearfield_intermediate_size_chord", "closed_nearfield_outer_size_chord", "closed_farfield_transition_dist_chord", "closed_farfield_size_chord"]),
        ],
        "Open": open_sections,
    }
    section_captions = {
        "Dominio": "Unica fuente de la forma y dimensiones del farfield. Todas las distancias se expresan en cuerdas; cambiar el nivel de malla no las modifica.",
        "Ejecucion de Gmsh": "Selecciona backend, hilos y algoritmo 2D sin alterar las condiciones fisicas del caso.",
        "Contorno exterior continuo": (
            "El contorno pared-TE-inlet se discretiza con una unica separacion tangencial. "
            "El inlet sigue siendo una interfaz fluida no fisica aunque Gmsh necesite separar "
            "la entidad donde cambia el rol de boundary."
        ),
        "Objetivo de capa limite, tejido y extrusion": "Fija y+, espesor numerico de tela y extrusion de una celda para el caso OpenFOAM 2D.",
        "Compatibilidad avanzada": "Esquema y modo topologico validados. Solo deben cambiarse al diagnosticar una incompatibilidad del backend.",
        "Limites y salvaguardas": "Establece limites de coste y condiciones que detienen una geometria o malla invalida antes de OpenFOAM.",
        "Boundary Layer": "Altura inicial, numero de capas, crecimiento normal y tratamiento de esquinas de la capa prismatica.",
        "Boundary Layer exterior": "La capa prismatica se aplica solo a la cara exterior de la tela y al puente no fisico del inlet cuando se selecciona el modo completo.",
        "Terminacion avanzada en labios": "El recorte de BL se conserva solo como alternativa diagnostica; la configuracion estandar usa continuidad prismatica y fans.",
        "Curvas Gmsh de la pared": "Controla la representacion de la pared y el numero global de columnas tangenciales de la BL.",
        "Preprocesado geometrico del perfil": "Limpia duplicados y redistribuye puntos para fidelidad de la curva; no fija directamente el numero de celdas.",
        "Redondeado geometrico del TE": "Construye y muestrea el cierre tangente antes de entregarlo a Gmsh.",
        "Discretizacion de malla en el TE": "Asigna nodos del cap y gradacion Bump en los tramos vecinos; afecta celdas, no modifica la geometria base.",
        "Topologia y union del inlet": "El modo recomendado conserva la BL exterior completa sobre el puente no fisico del inlet sin imponer fans. Los modos con fans y con inlet triangular permanecen como comparaciones diagnosticas; el inlet siempre sigue siendo fluido y nunca se exporta como wall.",
        "Inlet curvo desde el perfil base": "Reconstruye el tramo eliminado con la curva real del perfil sin corte. Las dos copias de esta interfaz se discretizan igual y se cosen tras Gmsh; nunca se exportan como pared.",
        "Pared exterior: intrados y extrados": "Distribuye columnas de BL y aplica Bump en ambos extremos de las ramas exteriores: labios del LE y uniones con el TE.",
        "Trailing Edge exterior": "Refina el cierre curvo exterior y limita la zona fina a la curvatura del cap y su transicion inmediata.",
        "Inlet fluido y refinamiento interior": "Controla el espaciado tangencial de la abertura y el crecimiento de triangulos hacia la cavidad. En modo automatico el tamano inicial se deriva del frente de BL, no del espesor de tela.",
        "Transicion interior desde el inlet": (
            "La opcion recomendada usa Gmsh Extend para copiar en la primera triangulacion el ancho "
            "real de las aristas del inlet, no la altura normal y1. Distancia y Power controlan despues "
            "el crecimiento progresivo hacia el nucleo de la cavidad."
        ),
        "Pared y volumen interior": "Discretiza la pared interior de forma independiente y deja crecer los triangulos hacia el nucleo casi estatico sin capa prismatica interior.",
        "Trailing Edge interior": "Controla por separado la curva y el volumen estrecho del TE dentro de la cavidad, con menos nodos que en la cara exterior.",
        "Sizes y transicion": "Controla la union BL-triangulos y el crecimiento gradual hasta el farfield.",
        "Transicion exterior y farfield": "Controla la union BL-triangulos y el crecimiento gradual hasta el farfield.",
    }
    st.subheader("Parametros detallados de Gmsh")
    st.caption(str(config_path(ROOT, "mesh")))
    tab_names = list(layout)
    tab_name = st.segmented_control(
        "Familia de parametros de malla",
        tab_names,
        default=tab_names[0],
        key=revisioned_widget_key("mesh-config-section"),
        label_visibility="collapsed",
    ) or tab_names[0]
    tab_intro = {
        "General": "Opciones comunes de Gmsh, coste, capa limite y extrusion OpenFOAM.",
        "Closed": "Discretizacion del contorno cerrado, cierre tangente del TE y crecimiento desde la capa prismatica al farfield.",
        "Open": "Malla exterior/interior conectada, BL solo en el exterior y puente no fisico a traves del inlet.",
    }
    st.caption(tab_intro[tab_name])
    st.caption("Guarda esta seccion antes de cambiar a otra; las claves no visibles se conservan sin modificacion.")
    chord_m = active_variant_chord_m(variant)
    with st.form("mesh-config-form"):
        edited: dict[str, Any] = dict(data)
        rendered: set[str] = set()
        for section_name, keys in layout[tab_name]:
            advanced_section = section_name in {
                "Compatibilidad avanzada",
                "Terminacion avanzada en labios",
            }
            container = st.expander(section_name, expanded=False) if advanced_section else st.container()
            with container:
                if advanced_section:
                    st.warning("Solo modifica estos parametros para diagnosticar una incompatibilidad topologica; conserva la configuracion estable para casos de produccion.")
                else:
                    st.markdown(f"**{section_name}**")
                if section_name in section_captions:
                    st.caption(section_captions[section_name])
                for key in keys:
                    if key in data:
                        if key in {"closed_first_cell_height_m", "open_first_cell_height_m"}:
                            columns = st.columns([2, 1, 1])
                            with columns[0]:
                                edited[key] = value_widget(
                                    key,
                                    data[key],
                                    f"mesh:{tab_name}:{key}",
                                    MESH_DESCRIPTIONS.get(key),
                                    MESH_FIELD_LABELS.get(key),
                                )
                            use_yplus_key = (
                                "closed_use_yplus_first_cell_height"
                                if key.startswith("closed_")
                                else "open_use_yplus_first_cell_height"
                            )
                            if chord_m and bool(edited.get(use_yplus_key, True)):
                                effective_y1 = estimated_first_cell_height_m(
                                    chord_m,
                                    float(edited.get("target_y_plus", data.get("target_y_plus", 1.0))),
                                )
                                columns[1].metric("y1 calculado [m]", f"{effective_y1['y1_m']:.6g}")
                                columns[2].metric("y1 / cuerda", f"{effective_y1['y1_over_chord']:.6g}")
                                columns[0].caption("El valor manual queda guardado, pero no se aplica mientras el calculo desde y+ este activo.")
                            elif chord_m and edited[key] is not None:
                                columns[1].metric("y1 aplicado [m]", f"{float(edited[key]):.6g}")
                                columns[2].metric("y1 / cuerda", f"{float(edited[key]) / chord_m:.6g}")
                            else:
                                columns[1].caption("Genera el paquete del caso para obtener la cuerda.")
                        elif key in {"fabric_thickness_chord", "open_minimum_fabric_thickness_chord"}:
                            columns = st.columns([2, 1])
                            if chord_m:
                                thickness_m = float(data[key]) * chord_m
                                with columns[0]:
                                    edited_m = st.number_input(
                                        MESH_FIELD_LABELS.get(key, key),
                                        min_value=0.0,
                                        value=thickness_m,
                                        format="%.8g",
                                        key=revisioned_widget_key(f"mesh:{tab_name}:{key}:meters"),
                                        help=MESH_DESCRIPTIONS.get(key),
                                    )
                                edited[key] = float(edited_m) / chord_m
                                columns[1].metric("Espesor / cuerda", f"{edited[key]:.6g}")
                            else:
                                edited[key] = value_widget(
                                    key,
                                    data[key],
                                    f"mesh:{tab_name}:{key}",
                                    MESH_DESCRIPTIONS.get(key),
                                    MESH_FIELD_LABELS.get(key),
                                )
                                columns[1].caption("Genera el paquete del caso para editar en metros.")
                        else:
                            edited[key] = value_widget(
                                key,
                                data[key],
                                f"mesh:{tab_name}:{key}",
                                MESH_DESCRIPTIONS.get(key),
                                MESH_FIELD_LABELS.get(key),
                            )
                        rendered.add(key)
            st.divider()
        prefix = "closed_" if tab_name == "Closed" else "open_" if tab_name == "Open" else ""
        zero_thickness_open_editor = (
            tab_name == "Open"
            and data.get("open_geometry_representation") == "zero_thickness_base_profile"
        )
        ungrouped = [
            key for key in data
            if key not in rendered
            and key not in MESH_LEGACY_HIDDEN_KEYS
            and (key.startswith(prefix) if prefix else not key.startswith(("closed_", "open_")))
        ]
        if zero_thickness_open_editor:
            # The old finite-fabric topology has many retained ``open_*`` keys.
            # They must survive round-trips so users can switch topology without
            # losing a saved setup, but exposing them here makes dormant values
            # look active and lets incompatible controls obscure the real mesh
            # definition.  The explicit zero-thickness sections above are the
            # complete editable surface for this representation.
            ungrouped = []
            st.caption(
                "Los parametros del metodo anterior con espesor se conservan en el JSON, "
                "pero permanecen ocultos porque no intervienen en esta representacion."
            )
        if ungrouped:
            st.markdown("**Otros parametros conservados**")
            st.caption("Claves compatibles que todavia usa el backend y no pertenecen a una subseccion principal.")
            for key in ungrouped:
                edited[key] = value_widget(
                    key,
                    data[key],
                    f"mesh:{tab_name}:other:{key}",
                    MESH_DESCRIPTIONS.get(key),
                    MESH_FIELD_LABELS.get(key),
                )
                rendered.add(key)
        submitted = st.form_submit_button("Guardar parametros de malla", type="primary")
    if submitted:
        # Saving from the current UI is also the schema migration point. Hidden
        # chord-normalized y1 aliases must not be allowed to override the metre
        # value the user has just edited.
        for legacy_key in (
            "closed_first_cell_height_chord",
            "open_first_cell_height_chord",
            "first_cell_height_chord_override",
            "open_first_cell_height_chord_override",
            "closed_first_cell_height_m_override",
            "open_first_cell_height_m_override",
        ):
            edited.pop(legacy_key, None)
        edited["mesh_configuration_mode"] = "custom"
        backup = save_config(ROOT, "mesh", edited, sync_workcase=False)
        update_workflow_sections(
            geometry={"variant": variant, "domain": str(edited.get("domain_type", "circular_50c"))},
            mesh={"mesh_level": "custom"},
        )
        st.success("Parametros de malla guardados. El builder los releera al iniciar el siguiente mallado.")
        if backup:
            st.caption(f"Copia anterior: {backup}")
    if chord_m and tab_name in {"Closed", "Open"}:
        prefix = "open" if variant_has_open_inlet(variant) else "closed"
        conditions = (load_config(ROOT, "workflow").get("case_conditions") or {})
        try:
            comparison = boundary_layer_comparison(
                chord_m=chord_m,
                reynolds=float(conditions.get("reynolds", 4.0e6)),
                target_y_plus=float(edited.get("target_y_plus", 1.0)),
                rho_kg_m3=float(conditions.get("rho_kg_m3", 1.225)),
                mu_pa_s=float(conditions.get("mu_pa_s", 1.81e-5)),
                layers=int(edited.get(f"{prefix}_boundary_layer_layers", 0)),
                growth_rate=float(edited.get(f"{prefix}_boundary_layer_growth", 1.0)),
                manual_y1_m=float(edited.get(f"{prefix}_first_cell_height_m", 0.0) or 0.0),
                use_yplus_y1=bool(edited.get(f"{prefix}_use_yplus_first_cell_height", True)),
                x_over_chord=1.0,
            )
            st.markdown("**Cobertura estimada de la capa limite**")
            st.caption(
                "Comparacion informativa con placa plana turbulenta sin gradiente de presion. "
                "La verificacion final usa y+ y perfiles de velocidad obtenidos por OpenFOAM."
            )
            metrics = st.columns(4)
            metrics[0].metric("y1 aplicado [m]", f"{comparison['y1_m']:.4g}")
            metrics[1].metric("Espesor prismatico / c", f"{comparison['prism_stack_thickness_over_chord']:.4g}")
            metrics[2].metric("delta99 teorico en x/c=1", f"{comparison['theoretical_delta99_over_chord']:.4g} c")
            ratio = comparison.get("prism_to_theoretical_delta99_ratio")
            metrics[3].metric("Prismas / delta99", f"{float(ratio):.3g}" if ratio is not None else "n/a")
            stations = [0.1, 0.3, 0.6, 0.9, 1.0]
            render_records_table([{
                "x/c": station,
                "delta99 teorico [m]": turbulent_flat_plate_delta99(
                    chord_m=chord_m,
                    reynolds_chord=float(conditions.get("reynolds", 4.0e6)),
                    x_over_chord=station,
                ),
                "delta99/c": turbulent_flat_plate_delta99(
                    chord_m=chord_m,
                    reynolds_chord=float(conditions.get("reynolds", 4.0e6)),
                    x_over_chord=station,
                ) / chord_m,
                "espesor prismas/c": comparison["prism_stack_thickness_over_chord"],
            } for station in stations], max_rows=len(stations))
        except (TypeError, ValueError) as exc:
            st.warning(f"No se pudo actualizar la estimacion de capa limite: {exc}")
    return edited


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


@st.fragment(run_every="2s")
def sidebar_job_status() -> None:
    touch_application_heartbeat(ROOT)
    job = selected_job()
    if job:
        st.write(f"Trabajo: **{job.stage}**")
        st.write(f"Estado: **{job.status}**")
    else:
        st.caption("Sin tareas ejecutadas en esta sesion.")


def suggested_library_case_name(variant_name: str, alpha_value: float) -> str:
    workflow_data = load_config(ROOT, "workflow")
    conditions = workflow_data.get("case_conditions") or {}
    project_data = load_config(ROOT, "project")
    profile = Path(str((project_data.get("profile_inputs") or {}).get("main_profile", "airfoil"))).stem
    raw = (
        f"{variant_name}_{profile}_Re{float(conditions.get('reynolds', 0.0)):g}_"
        f"M{float(conditions.get('mach', 0.0)):g}_alpha{float(alpha_value):+0.3f}"
    )
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")


def manifest_stage_packages(manifest: dict[str, Any], stage: str) -> dict[str, dict[str, Any]]:
    entry = (manifest.get("stages") or {}).get(stage) or {}
    packages = entry.get("packages") if isinstance(entry, dict) else None
    if isinstance(packages, dict):
        return {str(name): dict(info) for name, info in packages.items() if isinstance(info, dict)}
    if isinstance(entry, dict) and entry.get("folder"):
        return {"legacy": dict(entry)}
    return {}


def suggested_stage_package_name(stage: str, variant_name: str, alpha_value: float) -> str:
    mesh_cfg = load_config(ROOT, "mesh")
    workflow_cfg = load_config(ROOT, "workflow")
    conditions = workflow_cfg.get("case_conditions") or {}
    if stage == "geometry":
        return f"{variant_name}_geometry"
    if stage == "case":
        return f"Re{float(conditions.get('reynolds', 0.0)):g}_M{float(conditions.get('mach', 0.0)):g}"
    if stage == "mesh":
        domain_name = str(mesh_cfg.get("domain_type", "circular_50c"))
        level_name = str(mesh_cfg.get("mesh_level_origin", "custom"))
        return f"{variant_name}_{level_name}_{domain_name}"
    if stage == "solver":
        solver_cfg = load_config(ROOT, "solver")
        return str(solver_cfg.get("preset_id") or f"{variant_name}_solver")
    alpha_name = f"alpha_{float(alpha_value):+0.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    return alpha_name if stage == "simulation" else f"{alpha_name}_postprocess"


def case_library_panel(stage: str, variant_name: str, alpha_value: float, selected_case: str) -> None:
    """Save or restore a named package inside one working-case container."""
    labels = {
        "geometry": "geometria y preproceso",
        "case": "condiciones y paquete CFD",
        "mesh": "malla",
        "solver": "configuracion del solver",
        "simulation": "caso y simulacion",
        "postprocess": "postproceso",
    }
    cases = {str(item.get("folder")): item for item in saved_cases(ROOT) if item.get("folder")}
    with st.expander(f"Caso de trabajo: {labels[stage]}", expanded=False):
        st.caption(
            "Un caso de trabajo contiene varios paquetes de geometria, condiciones, mallas y simulaciones. "
            "Cada paquete conserva sus configuraciones y datos; cargarlo reconstruye el workspace activo."
        )
        default_name = selected_case if selected_case in cases else suggested_library_case_name(variant_name, alpha_value)
        selected_manifest = cases.get(selected_case) or {}
        packages = manifest_stage_packages(selected_manifest, stage)
        active_package = str(((selected_manifest.get("stages") or {}).get(stage) or {}).get("active_package") or "")
        if active_package not in packages and packages:
            active_package = next(reversed(packages))
        with st.form(f"case-library-{stage}"):
            columns = st.columns([2, 2, 2, 1])
            case_name = columns[0].text_input("Carpeta del caso", value=default_name)
            package_name = columns[1].text_input(
                "Nombre del paquete",
                value=suggested_stage_package_name(stage, variant_name, alpha_value),
                help="Permite guardar varias configuraciones de esta etapa dentro del mismo caso sin sustituir las demas.",
            )
            description = columns[2].text_input("Descripcion", value=str(selected_manifest.get("description", "")))
            action = columns[3].selectbox(
                "Si el paquete existe",
                ["archive", "keep", "delete"],
                format_func=lambda value: {"archive": "Archivar anterior", "keep": "No sobrescribir", "delete": "Reemplazar"}[value],
            )
            save_stage = st.form_submit_button(f"Guardar {labels[stage]}", type="primary")
        if save_stage:
            destination_packages = manifest_stage_packages(
                cases.get(case_name) or {},
                stage,
            )
            if stage == "mesh" and package_name in destination_packages:
                st.error(
                    "Ese paquete de malla ya existe. Para sustituirlo, seleccionalo "
                    "abajo y usa el boton de sustitucion con confirmacion."
                )
            else:
                start_job(
                    f"library_save_{stage}",
                    case_library_command(
                        ROOT,
                        "save",
                        stage=stage,
                        case_name=case_name,
                        variant=variant_name,
                        alpha=alpha_value,
                        description=description,
                        existing_action=action,
                        package_name=package_name,
                    ),
                    completion_action={"kind": "select_case", "case": case_name},
                )
        if selected_case in cases:
            manifest = cases[selected_case]
            stages = ", ".join(sorted((manifest.get("stages") or {}).keys())) or "ninguna"
            st.caption(
                f"Seleccionado: {selected_case} | airfoil={manifest.get('main_profile')} | "
                f"Re={manifest.get('reynolds')} | M={manifest.get('mach')} | alpha={manifest.get('alpha_deg')} | etapas={stages}"
            )
            if packages:
                selected_package = st.selectbox(
                    f"Paquete de {labels[stage]}",
                    list(packages),
                    index=list(packages).index(active_package),
                    key=f"library-package-{stage}",
                )
                info = packages[selected_package]
                st.caption(
                    f"Guardado: {info.get('saved_at', '-')} | archivos: {info.get('file_count', '-')} | "
                    f"tamano: {float(info.get('size_bytes', 0) or 0) / 1048576.0:.1f} MB"
                )
                if st.button(f"Cargar paquete de {labels[stage]}", key=f"restore-library-package-{stage}"):
                    start_job(
                        f"library_restore_{stage}",
                        case_library_command(
                            ROOT,
                            "restore",
                            stage=stage,
                            case_name=selected_case,
                            package_name=selected_package,
                            existing_action="archive",
                        ),
                        completion_action={
                            "kind": "reload_configuration",
                            "case": selected_case,
                            "stage": stage,
                            "package": selected_package,
                        },
                    )
                if stage == "mesh":
                    st.info(
                        "Generar o regenerar la malla activa nunca modifica este paquete guardado. "
                        "La sustitucion solo se realiza con la accion explicita siguiente."
                    )
                    replace_cols = st.columns([2, 2, 3])
                    replace_action = replace_cols[0].selectbox(
                        "Copia del paquete sustituido",
                        ["archive", "delete"],
                        format_func=lambda value: {
                            "archive": "Archivar antes de sustituir",
                            "delete": "Eliminar al sustituir",
                        }[value],
                        key=f"replace-library-package-action-{stage}",
                    )
                    confirm_replace = replace_cols[1].checkbox(
                        "Confirmo la sustitucion",
                        key=f"replace-library-package-confirm-{stage}",
                    )
                    active_mesh_exists = (
                        ROOT / "CFD_2D/meshes" / variant_name / "mesh_final.msh"
                    ).is_file()
                    replace_now = replace_cols[2].button(
                        "Sustituir este paquete por la malla activa",
                        disabled=not confirm_replace or not active_mesh_exists,
                        key=f"replace-library-package-{stage}",
                        type="primary",
                    )
                    if replace_now:
                        start_job(
                            "library_replace_mesh",
                            case_library_command(
                                ROOT,
                                "save",
                                stage="mesh",
                                case_name=selected_case,
                                variant=variant_name,
                                alpha=alpha_value,
                                description=str(selected_manifest.get("description", "")),
                                existing_action=replace_action,
                                package_name=selected_package,
                            ),
                            completion_action={
                                "kind": "select_case",
                                "case": selected_case,
                            },
                        )
            else:
                st.caption("Este caso todavia no contiene paquetes de esta etapa.")


@st.fragment(run_every="2s")
def job_console() -> None:
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
        st.rerun()
    st.caption(command_text(job.command))
    st.code(tail_file(Path(job.log_path), 180) or "Esperando salida...", language="text")
    st.caption("Estado y log actualizados cada 2 segundos.")
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
        elif job.stage in {"solver", "solver_sweep", "steady_extend", "steady_start_transient"}:
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


@st.fragment(run_every="30s")
def solver_live_monitor_panel() -> None:
    job = selected_job()
    if job is None or job.stage not in {"solver", "solver_sweep", "steady_extend", "steady_start_transient"}:
        return
    active_case = openfoam_case_from_command(job.command)
    snapshots = latest_files(
        active_case if active_case is not None else ROOT / "CFD_2D/openfoam_cases",
        ["ramair_live_monitor_*_snapshot.png", "**/ramair_live_monitor_*_snapshot.png"],
        1,
    )
    if snapshots:
        snapshot = snapshots[0]
        st.markdown("**Monitor PyFoam en vivo**")
        st.image(snapshot.read_bytes(), caption=str(snapshot.parent), width="stretch")
        monitor_status = read_json(snapshot.parent / "ramair_live_monitor_status.json", {}) or {}
        st.caption(
            f"Estado: {monitor_status.get('status', 'UNKNOWN')} | "
            f"campos residuales: {', '.join(monitor_status.get('residual_fields') or []) or 'esperando'} | "
            f"muestras forceCoeffs: {monitor_status.get('force_rows', 0)}. "
            "Imagen actualizada cada 30 segundos."
        )
    elif job.status == "RUNNING":
        st.info("El monitor integrado se iniciara cuando PyFoam cree el log del solver.")


def show_json_report(path: Path, title: str) -> None:
    if path.is_file():
        with st.expander(title, expanded=False):
            st.json(read_json(path, {}))


def show_images(paths: list[Path], columns: int = 2) -> None:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return
    cols = st.columns(columns)
    for index, path in enumerate(existing):
        cols[index % columns].image(str(path), caption=path.name, width='stretch')


def render_mesh_quality_summary(report_path: Path) -> None:
    """Show the decision-relevant checkMesh metrics and exact diagnostic sets."""
    report = read_json(report_path, {}) or {}
    if not report:
        return
    st.subheader("Resumen de calidad OpenFOAM")
    metrics = st.columns(5)
    metrics[0].metric("checkMesh", str(report.get("checkMesh_status", "NOT_RUN")))
    metrics[1].metric("Celdas", int(report.get("checkMesh_cell_count", 0) or 0))
    metrics[2].metric("No ort. max [deg]", f"{float(report.get('checkMesh_max_non_orthogonality_deg', 0.0) or 0.0):.3g}")
    metrics[3].metric("Skewness max", f"{float(report.get('checkMesh_max_skewness', 0.0) or 0.0):.3g}")
    metrics[4].metric("Checks fallidos", int(report.get("checkMesh_failed_checks_count", 0) or 0))
    assessment = report.get("engineering_quality_assessment") or {}
    if assessment:
        grade = str(assessment.get("grade", "?"))
        label = str(assessment.get("label", "Sin diagnostico"))
        risk = str(assessment.get("solver_risk", "UNKNOWN"))
        message = f"Calidad tecnica no bloqueante: {grade} - {label}. Riesgo numerico estimado: {risk}."
        if grade in {"A", "B"}:
            st.success(message)
        elif grade == "C":
            st.warning(message)
        else:
            st.error(message)
        st.caption(str(assessment.get("summary", "")))
        with st.expander("Margenes y recomendaciones tecnicas", expanded=grade in {"C", "D", "F"}):
            render_records_table(list(assessment.get("metrics") or []), max_rows=12)
            for recommendation in assessment.get("recommendations") or []:
                st.write(f"- {recommendation}")
            st.caption(str(assessment.get("aspect_ratio_note", "")))
    failed = report.get("checkMesh_failed_checks", []) or []
    if failed:
        st.warning("Checks que requieren inspeccion: " + ", ".join(map(str, failed)))
    locations = report.get("checkMesh_problem_locations", {}) or {}
    rows = []
    for name, item in locations.items():
        rows.append({
            "check": name,
            "region_probable": item.get("likely_region"),
            "entidades_checkMesh": item.get("checkMesh_reported_count"),
            "IDs_guardados": item.get("openfoam_label_count"),
            "IDs_muestra": item.get("openfoam_label_sample"),
            "x/c_mediano": (item.get("entity_x_over_chord_quantiles") or {}).get("median"),
            "extremo": item.get("reported_minimum", item.get("reported_maximum")),
            "umbral": item.get("checkMesh_threshold"),
            "causa_probable": item.get("cause_hint"),
        })
    if rows:
        render_records_table(rows, max_rows=12)
        st.caption(
            "Los IDs exactos se conservan en checkMesh_problem_sets. checkMesh informa el extremo global y el "
            "umbral del set, pero no escribe un valor individual de skewness/determinante para cada ID."
        )


def available_case_alphas(variant_name: str, workflow_config: dict[str, Any]) -> list[float]:
    values = {
        float(value)
        for value in (workflow_config.get("case_conditions", {}).get("alphas_deg") or [4.0])
    }
    for base in (
        ROOT / "CFD_2D/openfoam_cases" / variant_name,
        ROOT / "CFD_2D/results" / variant_name,
    ):
        if not base.is_dir():
            continue
        for path in base.glob("alpha_*"):
            match = re.fullmatch(r"alpha_([pm])(\d+)p(\d+)", path.name)
            if match:
                sign = 1.0 if match.group(1) == "p" else -1.0
                values.add(sign * float(f"{match.group(2)}.{match.group(3)}"))
    return sorted(values)


workflow = load_config(ROOT, "workflow")
variants = available_variants(ROOT) or [str(workflow.get("geometry", {}).get("variant", "reference_uncut"))]
preferred_variant = st.session_state.pop("_preferred_variant_after_restore", None)
default_variant = str(preferred_variant or workflow.get("geometry", {}).get("variant", variants[0]))
if default_variant not in variants:
    default_variant = variants[0]

variant = default_variant
saved_case_items = saved_cases(ROOT)
saved_case_map = {str(item.get("folder")): item for item in saved_case_items if item.get("folder")}
library_case_names = sorted(saved_case_map)
active_workspace = read_json(ROOT / "CFD_2D/app_state/active_workspace.json", {}) or {}
preferred_library_case = st.session_state.pop("_preferred_library_case_after_restore", None)
active_case_name = str(active_workspace.get("case") or "") if isinstance(active_workspace, dict) else ""
temporary_workspace_label = "Workspace temporal (no guardar automaticamente)"
library_default = (
    preferred_library_case
    if preferred_library_case in library_case_names
    else temporary_workspace_label
)
st.title("RamAir: Design and CFD")
st.caption(
    "Interfaz de control reproducible sobre los scripts existentes. CATIA solo se inicia "
    "mediante una orden explicita y cuando se detecta una instalacion local de CATIA V5."
)
st.subheader("Contexto activo")
context_columns = st.columns([3, 2, 2])
library_case_selection = context_columns[0].selectbox(
    "Caso de trabajo",
    [temporary_workspace_label, *library_case_names],
    index=[temporary_workspace_label, *library_case_names].index(library_default),
    key=revisioned_widget_key("results-library-case"),
    help=(
        "Al iniciar se usa el workspace temporal. Selecciona una carpeta existente y cargala "
        "para restaurar geometria, caso CFD, malla y solver de forma trazable."
    ),
)
set_workcase_selection(
    ROOT,
    None if library_case_selection == temporary_workspace_label else library_case_selection,
)
context_columns[1].metric("Perfil CFD cargado", variant)
context_columns[2].metric(
    "Workspace",
    "Temporal" if library_case_selection == temporary_workspace_label else library_case_selection,
)

st.sidebar.title("RamAir: Design and CFD")
st.sidebar.caption("Preproceso, Gmsh, OpenFOAM y postproceso")
with st.sidebar.expander("Crear caso de trabajo", expanded=False):
    st.caption(
        "Crea el contenedor, lo activa y copia la configuracion completa del solver estandar. "
        "La geometria, la malla y los resultados solo se guardan cuando realmente existen."
    )
    with st.form("create-working-case-form"):
        new_case_name = st.text_input("Nombre de carpeta", value=suggested_library_case_name(variant, float((workflow.get("case_conditions", {}).get("alphas_deg") or [4.0])[0])))
        new_case_description = st.text_input("Descripcion", value="")
        create_working_case = st.form_submit_button("Crear caso")
    if create_working_case:
        start_job(
            "library_create_case",
            case_library_command(
                ROOT,
                "create",
                case_name=new_case_name,
                variant=variant,
                alpha=float((workflow.get("case_conditions", {}).get("alphas_deg") or [4.0])[0]),
                description=new_case_description,
            ),
            completion_action={"kind": "select_case", "case": new_case_name},
        )
if (
    library_case_selection != temporary_workspace_label
    and isinstance(active_workspace, dict)
    and active_workspace.get("case")
):
    st.info(
        f"Workspace cargado: {active_workspace.get('case')} / {active_workspace.get('stage')} / "
        f"{active_workspace.get('package', 'legacy')}\n\n"
        f"Perfil: {active_workspace.get('variant')} | alpha: {active_workspace.get('alpha_deg')}"
    )
    st.caption(
        "Las configuraciones del caso se versionan con sus paquetes. En Malla, los cambios permanecen "
        "como borrador activo hasta guardar o sustituir explicitamente una malla real."
    )
results_locations = results_library_locations(ROOT)
with st.sidebar.expander("Ubicacion real de Results", expanded=False):
    st.caption("Los datos pesados se conservan en el filesystem Linux para no ralentizar Gmsh/OpenFOAM.")
    st.code(results_locations["linux"], language="text")
    st.code(results_locations["windows"], language="text")
    if st.button("Abrir Results en Explorador", key="open-real-results-library"):
        try:
            open_results_library(ROOT)
            st.success("Explorador abierto en la biblioteca real de WSL.")
        except Exception as exc:
            st.error(str(exc))
if library_case_selection in saved_case_map:
    selected_manifest = saved_case_map[library_case_selection]
    complete_stages = {"geometry", "case", "mesh"}
    available_complete_stages = set((selected_manifest.get("stages") or {}).keys())
    if complete_stages.issubset(available_complete_stages):
        restore_existing_action = st.selectbox(
            "Al cargar el caso",
            ["delete", "archive"],
            format_func=lambda value: {
                "delete": "Reemplazar workspace temporal",
                "archive": "Archivar workspace temporal",
            }[value],
            help=(
                "Los paquetes guardados en Results no se borran. Archivar conserva tambien "
                "los outputs temporales activos y puede consumir bastante espacio."
            ),
            key="sidebar-workspace-restore-action",
        )
        if st.button(
            "Cargar caso de trabajo completo",
            type="primary",
            key="sidebar-restore-workspace",
            width="stretch",
        ):
            start_job(
                "library_restore_workspace",
                case_library_command(
                    ROOT,
                    "restore-workspace",
                    case_name=library_case_selection,
                    existing_action=restore_existing_action,
                ),
                completion_action={
                    "kind": "reload_configuration",
                    "case": library_case_selection,
                    "stage": "workspace",
                    "package": "active_packages",
                },
            )
    with st.expander("Versiones y carga por etapa del caso seleccionado", expanded=False):
        st.caption(
            f"Perfil: {selected_manifest.get('main_profile') or selected_manifest.get('variant')} | "
            f"alpha: {selected_manifest.get('alpha_deg')} | Re: {selected_manifest.get('reynolds')}"
        )
        if complete_stages.issubset(available_complete_stages):
            st.caption(
                "La carga completa restaura geometria, condiciones CFD, malla y la configuracion "
                "de solver activa. Usa los controles inferiores solo para cargar una etapa aislada."
            )
        saved_stages = sorted((selected_manifest.get("stages") or {}).keys())
        if saved_stages:
            sidebar_restore_stage = st.selectbox("Etapa disponible", saved_stages, key="sidebar-saved-stage")
            sidebar_packages = manifest_stage_packages(selected_manifest, sidebar_restore_stage)
            if sidebar_packages:
                stage_entry = (selected_manifest.get("stages") or {}).get(sidebar_restore_stage) or {}
                preferred_package = st.session_state.pop("_preferred_package_after_restore", None)
                default_package = str(preferred_package or stage_entry.get("active_package") or "")
                if default_package not in sidebar_packages:
                    default_package = next(reversed(sidebar_packages))
                sidebar_restore_package = st.selectbox(
                    "Paquete disponible",
                    list(sidebar_packages),
                    index=list(sidebar_packages).index(default_package),
                    key="sidebar-saved-package",
                )
                stage_info = sidebar_packages[sidebar_restore_package]
                st.caption(
                    f"Guardada: {stage_info.get('saved_at', '-')} | "
                    f"archivos: {stage_info.get('file_count', '-')} | "
                    f"tamano: {float(stage_info.get('size_bytes', 0) or 0) / 1048576.0:.1f} MB"
                )
                if st.button("Cargar paquete al workspace", key="sidebar-restore-stage"):
                    start_job(
                        f"library_restore_{sidebar_restore_stage}",
                        case_library_command(
                            ROOT,
                            "restore",
                            stage=sidebar_restore_stage,
                            case_name=library_case_selection,
                            package_name=sidebar_restore_package,
                            existing_action="archive",
                        ),
                        completion_action={
                            "kind": "reload_configuration",
                            "case": library_case_selection,
                            "stage": sidebar_restore_stage,
                            "package": sidebar_restore_package,
                        },
                    )
            else:
                st.caption("La etapa no contiene ningun paquete restaurable.")
        else:
            st.caption("El manifiesto no contiene etapas reutilizables.")
st.sidebar.markdown(f'<div class="ramair-path">{ROOT}</div>', unsafe_allow_html=True)
st.sidebar.divider()
with st.sidebar:
    sidebar_job_status()

page_names = [
    "Estado",
    "Caso de trabajo",
    "Geometria",
    "Caso CFD",
    "Malla",
    "Caso OpenFOAM",
    "Ejecucion",
    "Postproceso",
    "Validation & Convergence Lab",
    "Archivos y logs",
]
active_page = st.segmented_control(
    "Etapa del workflow",
    page_names,
    default=page_names[0],
    key="active-workflow-page",
    label_visibility="collapsed",
) or page_names[0]

alpha_options = available_case_alphas(variant, workflow)
preferred_alpha = st.session_state.pop("_preferred_alpha_after_restore", None)
alpha = float(preferred_alpha if preferred_alpha in alpha_options else alpha_options[0])
if active_page in {"Caso OpenFOAM", "Ejecucion", "Postproceso"}:
    alpha = float(st.selectbox(
        "Angulo de ataque del caso [deg]",
        alpha_options,
        index=alpha_options.index(alpha),
        key=revisioned_widget_key("active-alpha"),
        help="El angulo pertenece a cada caso de simulacion. El barrido completo se define en Caso CFD; aqui seleccionas uno de esos casos.",
    ))

reload_notice = st.session_state.pop("_configuration_reload_notice", None)
if reload_notice:
    st.success(str(reload_notice))

workflow_pages_requiring_case = {
    "Geometria", "Caso CFD", "Malla", "Caso OpenFOAM", "Ejecucion", "Postproceso",
}
workflow_case_ready = library_case_selection in saved_case_map
if active_page in workflow_pages_requiring_case and not workflow_case_ready:
    st.warning(
        "Selecciona un Caso de trabajo en Contexto activo o crea uno desde la barra lateral. "
        "El flujo normal permanece bloqueado para evitar configuraciones y resultados sin identidad persistente."
    )

if active_page == "Estado":
    st.info(TAB_INTROS["Estado"])
    with st.expander("Como usar la aplicacion", expanded=True):
        st.markdown(
            """
1. **Entorno:** ejecuta la verificacion y resuelve cualquier `MISSING` antes de continuar.
2. **Geometria:** guarda perfiles, canopy y opciones CATIA; despues ejecuta el preprocesador.
3. **Caso CFD:** fija condiciones fisicas y genera el paquete de la geometria seleccionada.
4. **Malla:** ajusta Gmsh, genera una malla nueva y revisa `checkMesh` y sus localizaciones VTK.
5. **Caso OpenFOAM:** escribe los diccionarios solo cuando exista una `polyMesh` real.
6. **Ejecucion:** el solver es dry-run por defecto y solo se inicia mediante una orden explicita.
7. **Postproceso:** procesa resultados reales, revisa convergencia y abre VTK/ParaView cuando corresponda.

Los botones inician trabajos en segundo plano. La consola y **Archivos y logs** muestran el comando exacto, el estado y las salidas sin bloquear la interfaz.
"""
        )
    st.subheader("Entorno")
    sync_manifest = read_json(ROOT / "CFD_2D/app_state/runtime_sync_manifest.json", {}) or {}
    if sync_manifest:
        st.success(
            f"Codigo sincronizado: {sync_manifest.get('synchronized_at', 'unknown')} | "
            f"origen Windows: {sync_manifest.get('source_checkout', 'unknown')} | "
            f"runtime WSL: {sync_manifest.get('runtime_project', ROOT)}"
        )
    else:
        st.warning("No existe manifiesto de sincronizacion. Inicia la app con START_RAMAIR_CFD2D_APP.bat desde Windows.")
    report_path = ROOT / "CFD_2D/reports/environment_report.json"
    checks = read_json(report_path, []) or []
    ok = sum(1 for item in checks if item.get("status") == "OK")
    missing = sum(1 for item in checks if item.get("status") == "MISSING")
    warning = sum(1 for item in checks if item.get("status") == "WARNING")
    cols = st.columns(4)
    cols[0].metric("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    cols[1].metric("OK", ok)
    cols[2].metric("Warnings", warning)
    cols[3].metric("Missing", missing)
    if checks:
        render_records_table(checks)
    if st.button("Verificar entorno", type="primary"):
        start_job("environment", environment_command(ROOT))
    st.info("El solver OpenFOAM se ejecuta en Linux/WSL. La interfaz se abre en el navegador de Windows y comparte el backend WSL.")
    st.subheader("Instalacion y paquete portable")
    st.caption(
        "El paquete conserva codigo, perfiles, configuraciones y manuales, pero excluye mallas, resultados, "
        "entornos virtuales y productos generados pesados. En otro equipo se inicia con "
        "python run_ramair_cfd2d_app.py --install."
    )
    package_cols = st.columns(2)
    if package_cols[0].button("Crear paquete portable completo"):
        start_job(
            "portable_package",
            [sys.executable, str(project_path(ROOT, "tools", "package_ramair_project.py")), "--project-root", str(ROOT)],
        )
    if package_cols[1].button("Crear paquete independiente CATIA Windows"):
        start_job(
            "catia_windows_package",
            [sys.executable, str(project_path(ROOT, "tools", "package_ramair_catia_windows.py")), "--project-root", str(ROOT)],
        )
    st.subheader("Control de versiones")
    st.caption(
        "Git conserva solo codigo, perfiles, configuraciones y documentos propios. Mallas, "
        "simulaciones, PDFs de terceros y paquetes pesados permanecen fuera del repositorio."
    )
    git_tool = str(project_path(ROOT, "tools", "project_git.py"))
    with st.expander("Configurar Git", expanded=False):
        st.caption(
            "Se guarda solo en este proyecto. Crea primero un repositorio privado vacio en GitHub, "
            "GitLab u otro servidor y pega su URL de clonacion; no incluyas contrasenas ni tokens."
        )
        git_name = st.text_input("Nombre del autor Git", key="git-local-author-name")
        git_email = st.text_input("Email del autor Git", key="git-local-author-email")
        git_remote = st.text_input(
            "URL del repositorio remoto (opcional)",
            key="git-origin-url",
            placeholder="https://github.com/usuario/ramair-design-cfd.git",
        )
        if st.button("Guardar configuracion Git", key="save-local-git-configuration"):
            command = [
                sys.executable,
                git_tool,
                "configure",
                "--name",
                git_name,
                "--email",
                git_email,
            ]
            if git_remote.strip():
                command.extend(["--remote", git_remote.strip()])
            start_job("git_configure", command)
    git_columns = st.columns(4)
    if git_columns[0].button("Estado Git"):
        start_job("git_status", [sys.executable, git_tool, "status"])
    if git_columns[1].button("Crear snapshot"):
        start_job("git_snapshot", [sys.executable, git_tool, "snapshot"])
    if git_columns[2].button("Actualizar desde remoto"):
        start_job("git_pull", [sys.executable, git_tool, "pull"])
    if git_columns[3].button("Publicar snapshots"):
        start_job("git_push", [sys.executable, git_tool, "push"])
    st.subheader("Cerrar aplicacion y liberar recursos")
    st.caption(
        "El cierre se bloquea mientras exista una tarea CAE activa. Streamlit se detiene y el lanzador "
        "libera la distribucion WSL solo cuando no queda ningun proceso RamAir en ejecucion."
    )
    confirm_shutdown = st.checkbox("Confirmo que deseo cerrar la aplicacion", key="confirm-application-shutdown")
    if st.button("Cerrar DESIGN APP y liberar WSL", disabled=not confirm_shutdown):
        try:
            marker = request_application_shutdown(ROOT, MANAGER)
            st.success(f"Cierre solicitado. Marcador: {marker}")
        except Exception as exc:
            st.error(str(exc))
    workflow_safeguards_editor()

if active_page == "Caso de trabajo":
    st.info(TAB_INTROS["Caso de trabajo"])
    if not workflow_case_ready:
        st.subheader("Selecciona o crea un caso")
        st.caption(
            "La seleccion activa habilita Geometria, Caso CFD, Malla, OpenFOAM, Ejecucion y Postproceso. "
            "Validation & Convergence Lab conserva su workspace independiente."
        )
    else:
        manifest = saved_case_map[library_case_selection]
        header = st.columns(4)
        header[0].metric("Caso", str(manifest.get("case_name") or library_case_selection))
        header[1].metric("Schema", str(manifest.get("schema_version", "-")))
        header[2].metric("Perfil", str(manifest.get("variant") or manifest.get("main_profile") or "-"))
        header[3].metric("Revisado", str(manifest.get("updated_at") or "-"))
        st.caption(f"UUID: {manifest.get('work_case_id', '-')}")
        rows = []
        for stage, stage_entry in (manifest.get("stages") or {}).items():
            for package_name, package in manifest_stage_packages(manifest, stage).items():
                approval = dict(package.get("approval") or {})
                rows.append({
                    "etapa": stage,
                    "paquete": package_name,
                    "revision": package.get("revision_id"),
                    "compatibilidad": (package.get("compatibility") or {}).get("status", "unknown"),
                    "aprobacion": approval.get("status", "pending"),
                    "dependencias": len(package.get("dependencies") or []),
                    "activo": package_name == str(stage_entry.get("active_package") or ""),
                })
        if rows:
            render_records_table(rows, max_rows=100)
        else:
            st.info("El caso aun no contiene paquetes. Comienza por Geometria.")
        with st.expander("Manifest versionado", expanded=False):
            st.json(manifest)

if active_page == "Geometria" and workflow_case_ready:
    st.info(TAB_INTROS["Geometria"])
    geometry_view = st.segmented_control(
        "Dimension de geometria", ["Geometria 2D", "Geometria 3D"],
        default="Geometria 2D", key=revisioned_widget_key("geometry-dimension"),
        label_visibility="collapsed",
    ) or "Geometria 2D"
    if geometry_view == "Geometria 2D":
        inlet_design_editor()
        geometry_2d_workspace_editor(saved_case_map[library_case_selection])
    else:
        project_config_editor([
            ("Canopy", ["canopy_geometry", "rib_and_cell_geometry"]),
            ("Tejido y lineas", ["fabric_and_lines"]),
            ("CATIA", ["catia_generation", "catia_exports", "optional_modules"]),
            ("CFD 2D y plots", ["cfd_2d_exports", "cfd_2d", "debug_plots"]),
        ])
        catia_system_config_editor()
    cols = st.columns([1, 4])
    if cols[0].button("Ejecutar preprocesador", type="primary"):
        start_job("preprocess", preprocessor_command(ROOT))
    cols[1].info("Esta accion regenera CATIA/Inputs y la geometria CFD, pero no abre CATIA V5.")
    st.subheader("Generacion CAD opcional")
    catia_status = cached_catia_detection(str(ROOT))
    catia_available = bool(catia_status.get("available"))
    catia_inputs_ready = bool(catia_status.get("inputs_ready"))
    catia_macro_ready = bool(catia_status.get("macro_ready"))
    catia_cols = st.columns([1, 3])
    if catia_cols[0].button(
        "Ejecutar CATScript en CATIA V5",
        type="primary",
        disabled=not (catia_available and catia_inputs_ready and catia_macro_ready),
        help=(
            "Inicia CATIA V5 de forma visible y ejecuta Generate_RamAir_Canopy_MAIN.CATScript "
            "con RAMAIR_CATIA_INPUTS apuntando a CATIA/Inputs."
        ),
    ):
        start_job(
            "catia_macro",
            catia_macro_command(ROOT, str(catia_status.get("cnext") or "")),
        )
    if catia_available and catia_inputs_ready and catia_macro_ready:
        catia_cols[1].success(
            f"CATIA V5 detectado: {catia_status.get('cnext')}. "
            "El modelo CAD se genera solo al pulsar el boton."
        )
    elif not catia_available:
        catia_cols[1].caption(
            "CATIA V5 no se ha detectado. Esta funcion queda desactivada y no se considera "
            "un requisito para preproceso, Gmsh u OpenFOAM."
        )
    else:
        catia_cols[1].warning(
            "CATIA V5 esta disponible, pero faltan el CATScript principal o "
            "CATIA/Inputs/ramair_global_inputs.csv. Ejecuta primero el preprocesador."
        )
    preview_paths = latest_files(ROOT / "CFD_2D/CFD_2D_inputs", ["previews/*.png", "geometry/*/*preview*.png"], 8)
    show_images(preview_paths, 3)

if active_page == "Caso CFD" and workflow_case_ready:
    st.info(TAB_INTROS["Caso CFD"])
    st.subheader("Perfil CFD del caso")
    profile_columns = st.columns([3, 1])
    selected_variant = profile_columns[0].selectbox(
        "Geometria preprocesada",
        variants,
        index=variants.index(variant) if variant in variants else 0,
        key=revisioned_widget_key("case-cfd-variant"),
        help=(
            "Selecciona una geometria real del paquete CFD. Los niveles coarse/medium/fine y "
            "otras variantes de refinamiento se eligen despues como versiones de malla."
        ),
    )
    variant_changed = selected_variant != variant
    variant = selected_variant
    if profile_columns[1].button(
        "Activar perfil",
        type="primary",
        disabled=not variant_changed,
        help="Actualiza el perfil del workflow sin generar todavia geometria, malla ni caso OpenFOAM.",
    ):
        update_workflow_sections(geometry={"variant": variant})
        st.session_state["_preferred_variant_after_restore"] = variant
        st.session_state["_config_ui_revision"] = int(st.session_state.get("_config_ui_revision", 0)) + 1
        st.rerun()
    profile_columns[0].caption(
        "El perfil activo se guarda con las condiciones CFD y se restaura al cargar el caso de trabajo."
    )
    validation_preset_dir = ROOT / "CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6"
    validation_workcase_name = "LS1_0417_validation_M0p15_Re1p9e6"
    validation_workcase_dir = ROOT / "Results" / validation_workcase_name
    with st.expander("Validacion publicada LS(1)-0417: M=0.15, Re=1.9e6", expanded=False):
        st.caption(
            "Carga la polar de Ghoreyshi et al. y la configuracion temporal de segundo orden. "
            "No ejecuta el solver. La geometria CFD se escala a c=1 m; para satisfacer simultaneamente "
            "M=0.15 y Re=1.9e6 con T y viscosidad fijas se usa rho=0.6661 kg/m3 (p ideal 55.09 kPa), "
            "por lo que no representa literalmente densidad/presion de nivel del mar."
        )
        if validation_workcase_dir.is_dir():
            st.success(f"Caso de trabajo disponible: {validation_workcase_name}")
            st.code(str(validation_workcase_dir), language="text")
            st.caption(
                "Seleccionalo en la barra lateral y pulsa 'Cargar geometria + caso CFD + malla'. "
                "Las graficas de validacion se actualizan exclusivamente dentro de ese caso."
            )
        elif st.button("Crear caso de trabajo completo de validacion", type="primary", key="create-ls10417-validation-workcase"):
            start_job(
                "create_validation_workcase",
                [
                    sys.executable,
                    str(ROOT / "CFD_2D/scripts/ramair_2d_validation_workcase.py"),
                    "--project-root", str(ROOT),
                    "--case-name", validation_workcase_name,
                    "--existing-action", "keep",
                ],
                completion_action={"kind": "select_case", "case": validation_workcase_name},
            )
        validation_time_profile = st.selectbox(
            "Duracion temporal",
            [
                "Prueba portatil: t*=0.2 (~14 min estimados)",
                "Preliminar portatil: 2500 pasos nominales",
                "Publicada: 25000 pasos nominales",
            ],
            help=(
                "Los tres perfiles conservan backward, maxCo=1 y tres correctores externos. "
                "La prueba t*=0.2 comprueba estabilidad y archivos; la preliminar y la publicada requieren "
                "muchos mas pasos efectivos cuando maxCo reduce el deltaT nominal."
            ),
            key="ls10417-validation-time-profile",
        )
        if st.button("Cargar preset de validacion", type="primary", key="load-ls10417-validation-preset"):
            workflow_preset = read_json(validation_preset_dir / "workflow_preset.json", {}) or {}
            if validation_time_profile.startswith("Prueba"):
                solver_filename = "solver_preset_laptop_smoke.json"
            elif validation_time_profile.startswith("Preliminar"):
                solver_filename = "solver_preset_laptop_screening.json"
            else:
                solver_filename = "solver_preset.json"
            solver_preset = read_json(validation_preset_dir / solver_filename, {}) or {}
            active_workflow = load_config(ROOT, "workflow")
            for section in ("geometry", "case_conditions", "execution"):
                active_workflow[section] = dict(active_workflow.get(section) or {}) | dict(workflow_preset.get(section) or {})
            save_config(ROOT, "workflow", active_workflow)
            save_config(ROOT, "solver", solver_preset)
            st.session_state["_config_ui_revision"] = int(st.session_state.get("_config_ui_revision", 0)) + 1
            st.success("Preset cargado y configuraciones anteriores respaldadas. Revisa los valores antes de generar casos.")
            st.rerun()
        show_json_report(validation_preset_dir / "case_manifest.json", "Correspondencia con el paper")
    conditions = workflow.get("case_conditions", {})
    st.subheader("Paquete de geometria y condiciones")
    with st.form("case-builder-form"):
        cols = st.columns(3)
        alpha_start = cols[0].number_input("Alpha inicial [deg]", value=float(min(conditions.get("alphas_deg", [-5.0]))), key=revisioned_widget_key("case-alpha-start"))
        alpha_end = cols[1].number_input("Alpha final [deg]", value=float(max(conditions.get("alphas_deg", [15.0]))), key=revisioned_widget_key("case-alpha-end"))
        alpha_step = cols[2].number_input("Paso alpha [deg]", value=float(conditions.get("alpha_step_deg", 1.0)), min_value=0.001, key=revisioned_widget_key("case-alpha-step"))
        cols = st.columns(3)
        reynolds = cols[0].number_input("Reynolds", value=float(conditions.get("reynolds", 4.0e6)), format="%.8g", key=revisioned_widget_key("case-reynolds"))
        mach = cols[1].number_input("Mach", value=float(conditions.get("mach", 0.1)), format="%.6g", key=revisioned_widget_key("case-mach"))
        velocity = cols[2].text_input("Velocidad [m/s] o auto", value=str(conditions.get("velocity", "auto")), key=revisioned_widget_key("case-velocity"))
        cols = st.columns(2)
        rho = cols[0].number_input("Densidad [kg/m3]", value=float(conditions.get("rho_kg_m3", 1.225)), format="%.8g", key=revisioned_widget_key("case-rho"))
        mu = cols[1].number_input("Viscosidad dinamica [Pa s]", value=float(conditions.get("mu_pa_s", 1.81e-5)), format="%.8g", key=revisioned_widget_key("case-mu"))
        cols = st.columns(2)
        pressure_ref_pa = cols[0].number_input(
            "Presion termodinamica de referencia [Pa]",
            value=float(conditions.get("pressure_ref_pa", 101325.0)),
            format="%.8g",
            key=revisioned_widget_key("case-pressure-ref-pa"),
            help="Trazabilidad termodinamica y comprobacion de gas ideal. El solver incompresible usa presion cinematica de calibre.",
        )
        temperature_K = cols[1].number_input(
            "Temperatura [K]",
            value=float(conditions.get("temperature_K", 288.15)),
            min_value=1.0,
            format="%.8g",
            key=revisioned_widget_key("case-temperature-K"),
        )
        ideal_gas_density = float(pressure_ref_pa) / (287.05 * float(temperature_K))
        speed_of_sound = math.sqrt(1.4 * 287.05 * float(temperature_K))
        mach_velocity = float(mach) * speed_of_sound
        case_chord_m = active_variant_chord_m(variant) or 1.0
        resulting_reynolds = float(rho) * mach_velocity * case_chord_m / max(float(mu), 1.0e-30)
        st.caption(
            f"Gas ideal con p,T: rho={ideal_gas_density:.6g} kg/m3. "
            f"U(M,T)={mach_velocity:.6g} m/s; Re resultante con la rho seleccionada: {resulting_reynolds:.6g}."
        )
        save_case_conditions = st.form_submit_button("Guardar condiciones")
        build_case = st.form_submit_button("Generar paquete CFD", type="primary")
    if save_case_conditions or build_case:
        count = max(0, int(math.floor((float(alpha_end) - float(alpha_start)) / float(alpha_step) + 1.0e-9)))
        alpha_values = [round(float(alpha_start) + index * float(alpha_step), 10) for index in range(count + 1)]
        if not alpha_values or abs(alpha_values[-1] - float(alpha_end)) > 1.0e-8:
            alpha_values.append(float(alpha_end))
        parsed_velocity: str | float = str(velocity).strip()
        if parsed_velocity.lower() != "auto":
            parsed_velocity = float(parsed_velocity)
        update_workflow_sections(
            geometry={"variant": variant},
            case_conditions={
                "alphas_deg": alpha_values,
                "alpha_step_deg": float(alpha_step),
                "reynolds": float(reynolds),
                "mach": float(mach),
                "rho_kg_m3": float(rho),
                "mu_pa_s": float(mu),
                "pressure_ref_pa": float(pressure_ref_pa),
                "temperature_K": float(temperature_K),
                "velocity": parsed_velocity,
            },
        )
        st.success("Condiciones CFD guardadas en el workflow activo.")
    if build_case:
        start_job("case_package", case_builder_command(
            ROOT, variant=variant, alpha_start=alpha_start, alpha_end=alpha_end, alpha_step=alpha_step,
            reynolds=reynolds, mach=mach, rho=rho, mu=mu,
            pressure_ref_pa=pressure_ref_pa, temperature_K=temperature_K, velocity=velocity,
        ))
    show_json_report(ROOT / "CFD_2D/CFD_2D_inputs/case_package" / variant / "manifest.json", "Manifest de geometria")
    show_json_report(ROOT / "CFD_2D/CFD_2D_inputs/case_package" / variant / "mesh_input_contract.json", "Contrato de entrada de malla")

if active_page == "Malla" and workflow_case_ready:
    st.info(TAB_INTROS["Malla"])
    mesh_config = load_config(ROOT, "mesh")
    for key, value in MESH_UI_DEFAULTS.items():
        mesh_config.setdefault(key, value)
    domain = str(mesh_config.get("domain_type", "circular_50c"))
    if domain not in DOMAIN_DEFAULTS:
        domain = "circular_50c"
    active_workspace_mesh = (
        active_workspace
        if isinstance(active_workspace, dict) and active_workspace.get("stage") == "mesh"
        else {}
    )
    config_mode = str(mesh_config.get("mesh_configuration_mode", "custom"))
    level_origin = str(mesh_config.get("mesh_level_origin", "fine"))
    level = level_origin if config_mode == "level_base" and level_origin in CHOICES["mesh_level"] else "custom"

    st.subheader("Geometria activa y mallas compatibles")
    project_geometry = geometry_dto(load_config(ROOT, "project"), load_config(ROOT, "inlet_design"))
    geometry_rows = []
    for series_name, points in preview_series(ROOT, project_geometry).items():
        geometry_rows.extend(
            {"x/c": point["x"], "z/c": point["z"], "serie": series_name, "orden": order}
            for order, point in enumerate(points)
        )
    if geometry_rows:
        st.vega_lite_chart(
            pd.DataFrame(geometry_rows),
            {"mark": {"type": "line", "clip": True}, "height": 280,
             "encoding": {
                 "x": {"field": "x/c", "type": "quantitative", "scale": {"zero": False}},
                 "y": {"field": "z/c", "type": "quantitative", "scale": {"zero": False}},
                 "color": {"field": "serie", "type": "nominal", "legend": None},
                 "detail": {"field": "serie", "type": "nominal"},
                 "order": {"field": "orden", "type": "quantitative"},
             }},
            width="stretch",
        )
    profile_kind = "Open" if variant_has_open_inlet(variant) else "Closed"
    st.caption(
        f"Perfil/geometria: {variant} | tipo: {profile_kind}. Solo las revisiones enlazadas a la "
        "geometria activa se ofrecen para reutilizacion silenciosa."
    )
    mesh_catalog = saved_mesh_catalog(ROOT, library_case_selection)
    compatible_meshes = [item for item in mesh_catalog if item.get("compatible")]
    if compatible_meshes:
        render_records_table([{
            "caso": item["case_name"], "malla": item["package_name"],
            "revision": item.get("revision_id"), "checkMesh": item.get("checkMesh_status"),
            "aprobacion": (item.get("approval") or {}).get("status", "pending"),
            "guardada": item.get("saved_at"),
        } for item in compatible_meshes], max_rows=50)
        mesh_labels = [f"{item['case_name']} / {item['package_name']}" for item in compatible_meshes]
        selected_saved_mesh_label = st.selectbox("Malla guardada compatible", mesh_labels)
        selected_saved_mesh = compatible_meshes[mesh_labels.index(selected_saved_mesh_label)]
        saved_actions = st.columns([1, 1, 3])
        if saved_actions[0].button("Cargar malla", type="primary"):
            start_job(
                "library_restore_mesh",
                case_library_command(
                    ROOT, "restore", stage="mesh", case_name=str(selected_saved_mesh["case_name"]),
                    package_name=str(selected_saved_mesh["package_name"]), existing_action="archive",
                ),
                completion_action={
                    "kind": "reload_configuration", "case": selected_saved_mesh["case_name"],
                    "stage": "mesh", "package": selected_saved_mesh["package_name"],
                },
            )
        saved_actions[1].caption("Abrir/revisar: carga el paquete y usa los visores e informes inferiores.")
        if selected_saved_mesh.get("quality_report"):
            saved_actions[2].caption(str(selected_saved_mesh["quality_report"]))
    else:
        selected_saved_mesh = None
        st.info("No hay mallas guardadas compatibles con la revision geometrica activa. Crea una configuracion nueva.")
    incompatible_count = len(mesh_catalog) - len(compatible_meshes)
    if incompatible_count:
        with st.expander(f"Mallas incompatibles visibles ({incompatible_count})", expanded=False):
            render_records_table([{
                "caso": item["case_name"], "malla": item["package_name"],
                "motivo": item.get("compatibility_reason"),
                "estado": (item.get("approval") or {}).get("status", "pending"),
            } for item in mesh_catalog if not item.get("compatible")], max_rows=100)

    st.subheader("Nueva configuracion")
    st.caption(
        "El nivel carga una base editable una sola vez. Una configuracion restaurada o cualquier guardado manual "
        "tiene prioridad y pasa a modo personalizado; Gmsh usa siempre el JSON activo mostrado debajo."
    )
    status_cols = st.columns(4)
    status_cols[0].metric("Fuente", "Nivel base" if level != "custom" else "Personalizada/restaurada")
    status_cols[1].metric("Nivel de origen", level_origin if level_origin in CHOICES["mesh_level"] else "-")
    status_cols[2].metric("Dominio", CHOICE_LABELS["domain_type"].get(domain, domain))
    status_cols[3].metric("Paquete cargado", str(active_workspace_mesh.get("package") or "-") if active_workspace_mesh else "-")

    with st.form("mesh-level-base-form"):
        base_cols = st.columns([2, 2, 3])
        base_sources = ["defaults", "preset"] + (["saved_mesh"] if selected_saved_mesh else [])
        base_source = base_cols[0].selectbox(
            "Partir de",
            base_sources,
            format_func=lambda value: {
                "defaults": "Defaults revisables", "preset": "Preset Coarse/Medium/Fine/Extra Fine",
                "saved_mesh": "Malla guardada seleccionada",
            }[value],
        )
        base_level = base_cols[1].selectbox(
            "Preset inicial",
            CHOICES["mesh_level"],
            index=CHOICES["mesh_level"].index(level_origin) if level_origin in CHOICES["mesh_level"] else 2,
            format_func=lambda value: {
                "coarse": "Coarse - y+=1 - 50 capas",
                "medium": "Medium - y+=2/3 - 50 capas",
                "fine": "Fine - y+=4/9 - 50 capas",
                "extra_fine": "Extra Fine - y+=8/27 - 75 capas",
            }[value],
            help="Solo se aplican al pulsar el boton. Conservan forma/dimensiones del dominio y estandarizan geometria, TE y discretizacion tangencial.",
            disabled=base_source != "preset",
        )
        base_cols[2].info(
            "Los presets y las mallas guardadas son puntos de partida, no identidades. La edicion se guarda como "
            "borrador activo y no altera la revision aprobada de la malla de origen."
        )
        apply_level_base = st.form_submit_button("Crear configuracion editable desde esta base")
    if apply_level_base:
        if base_source == "saved_mesh" and selected_saved_mesh:
            updated_mesh_config = saved_mesh_configuration(
                ROOT, str(selected_saved_mesh["case_name"]), str(selected_saved_mesh["package_name"])
            )
            updated_mesh_config["mesh_configuration_mode"] = "saved_mesh_base"
            updated_mesh_config["mesh_source_entity_id"] = selected_saved_mesh.get("entity_id")
            updated_mesh_config["mesh_source_revision_id"] = selected_saved_mesh.get("revision_id")
        elif base_source == "preset":
            updated_mesh_config = apply_mesh_level(mesh_config, base_level)
        else:
            updated_mesh_config = dict(mesh_level_values("fine"))
            for key, value in MESH_UI_DEFAULTS.items():
                updated_mesh_config.setdefault(key, value)
            updated_mesh_config["mesh_configuration_mode"] = "defaults_base"
        updated_mesh_config["domain_type"] = domain
        save_config(ROOT, "mesh", updated_mesh_config, sync_workcase=False)
        update_workflow_sections(
            geometry={"variant": variant, "domain": domain},
            mesh={"mesh_level": base_level if base_source == "preset" else "custom"},
        )
        st.session_state["_config_ui_revision"] = int(st.session_state.get("_config_ui_revision", 0)) + 1
        st.success("Base aplicada como borrador. Revisa los valores antes de generar.")
        st.rerun()

    with st.form("mesh-run-form"):
        cols = st.columns(3)
        configured_mesh_backend = str(workflow.get("mesh", {}).get("gmsh_backend", "python_api"))
        mesh_backend_options = ["python_api", "cli", "auto"]
        backend = cols[0].selectbox(
            "Backend Gmsh",
            mesh_backend_options,
            index=mesh_backend_options.index(configured_mesh_backend) if configured_mesh_backend in mesh_backend_options else 0,
            key=revisioned_widget_key("mesh-run-backend"),
        )
        old_action = cols[1].selectbox(
            "Salida anterior",
            ["archive", "delete", "keep"],
            index=0,
            format_func=lambda value: {"archive": "Archivar", "delete": "Eliminar definitivamente", "keep": "Conservar"}[value],
            help="Eliminar borra CFD_2D/meshes/<variante> antes de mallar y no crea una copia en Previous Versions. Archivar conserva una copia completa.",
            key=revisioned_widget_key("mesh-run-old-action"),
        )
        cols[2].write(f"**Configuracion usada**\n\n`{config_path(ROOT, 'mesh').name}`")
        cols = st.columns(4)
        threads = cols[0].number_input("Threads", min_value=1, max_value=16, value=int(mesh_config.get("gmsh_threads", 12)), key=revisioned_widget_key("mesh-run-threads"))
        timeout_s = cols[1].number_input("Timeout Gmsh [s]", min_value=60, value=int(workflow.get("mesh", {}).get("gmsh_timeout_s", 900)), step=60, key=revisioned_widget_key("mesh-run-timeout"))
        foam_timeout_s = cols[2].number_input("Timeout conversion [s]", min_value=60, value=int(workflow.get("mesh", {}).get("openfoam_tool_timeout_s", 600)), step=60, key=revisioned_widget_key("mesh-run-foam-timeout"))
        write_foam = cols[3].toggle("Extruir para OpenFOAM", value=bool(workflow.get("mesh", {}).get("write_openfoam_mesh", True)), key=revisioned_widget_key("mesh-run-write-foam"))
        cols = st.columns(2)
        check_mesh = cols[0].toggle("Ejecutar gmshToFoam + checkMesh", value=True, key=revisioned_widget_key("mesh-run-check-mesh"))
        plot = cols[1].toggle("Generar previews utiles", value=bool(workflow.get("mesh", {}).get("plot", True)), key=revisioned_widget_key("mesh-run-plot"))
        confirm_delete = st.checkbox(
            "Confirmo la eliminacion de la malla previa",
            value=False,
            disabled=old_action != "delete",
            help="Solo afecta a la carpeta generada de la variante activa; perfiles, configuraciones y codigo no se eliminan.",
            key=revisioned_widget_key("mesh-run-confirm-delete"),
        )
        run_mesh = st.form_submit_button(
            "Generar malla nueva",
            type="primary",
            disabled=old_action == "delete" and not confirm_delete,
        )
    if run_mesh:
        update_workflow_sections(
            geometry={"variant": variant, "domain": domain},
            mesh={
                "mesh_level": level,
                "gmsh_backend": backend,
                "gmsh_timeout_s": int(timeout_s),
                "openfoam_tool_timeout_s": int(foam_timeout_s),
                "gmsh_threads": int(threads),
                "write_openfoam_mesh": bool(write_foam),
                "plot": bool(plot),
            },
        )
        start_job("mesh", mesh_command(
            ROOT, variant=variant, domain=domain, mesh_level=level, gmsh_backend=backend,
            gmsh_timeout_s=timeout_s, openfoam_timeout_s=foam_timeout_s, threads=threads,
            previous_output_action=old_action, write_openfoam_mesh=write_foam,
            check_mesh=check_mesh, plot=plot,
            mesh_config=None,
        ))
    mesh_config = mesh_config_editor(variant)
    with st.expander("Optimizacion corta de parametros", expanded=False):
        st.caption(
            "Genera entre 2 y 5 mallas reales, ejecuta gmshToFoam/checkMesh, puntua sus metricas y conserva "
            "solo la mejor. No ejecuta el solver. La primera celda solo varia si el modo y+ esta desactivado."
        )
        with st.form("mesh-optimizer-form"):
            opt_cols = st.columns(4)
            opt_iterations = opt_cols[0].number_input("Candidatos", min_value=2, max_value=5, value=3, step=1)
            opt_action = opt_cols[1].selectbox("Malla previa", ["archive", "delete"], index=0)
            opt_vary_y1 = opt_cols[2].toggle("Variar y1 manual", value=False)
            opt_confirm = opt_cols[3].checkbox("Confirmo varias mallas")
            optimize_mesh = st.form_submit_button("Optimizar y seleccionar", type="primary")
        if optimize_mesh and opt_confirm:
            start_job("mesh_optimize", mesh_optimizer_command(
                ROOT,
                variant=variant,
                domain=domain,
                mesh_level=level,
                iterations=int(opt_iterations),
                vary_first_cell=opt_vary_y1,
                gmsh_backend=backend,
                gmsh_timeout_s=int(timeout_s),
                openfoam_timeout_s=int(foam_timeout_s),
                threads=int(threads),
                previous_output_action=opt_action,
            ))
        elif optimize_mesh:
            st.error("Confirma explicitamente la generacion de varias mallas.")
    mesh_root = ROOT / "CFD_2D/meshes" / variant
    gmsh_gui = Path.home() / ".local/opt/gmsh-4.15.2/bin/gmsh"
    if gmsh_gui.is_file():
        try:
            gmsh_gui_version = subprocess.check_output([str(gmsh_gui), "--version"], text=True, timeout=5).strip()
            st.caption(f"Visor Linux seleccionado: Gmsh {gmsh_gui_version} - {gmsh_gui}")
        except Exception as exc:
            st.warning(f"No se pudo verificar el visor Gmsh local: {exc}")
    viewer_mode = st.selectbox(
        "Visor de malla",
        ["windows_python", "linux_wslg"],
        format_func=lambda value: "Gmsh Python en Windows (recomendado)" if value == "windows_python" else "Gmsh Linux mediante WSLg",
    )
    if st.button("Abrir mesh_final.msh en Gmsh", disabled=not (mesh_root / "mesh_final.msh").is_file()):
        try:
            viewer_pid = open_mesh_viewer(ROOT, mesh_root / "mesh_final.msh", viewer=viewer_mode)
            st.success(f"Gmsh iniciado con {viewer_mode} (PID {viewer_pid}).")
        except Exception as exc:
            st.error(str(exc))
    viewer_log = project_path(ROOT, "logs", "CFD 2D App", "gmsh_viewer.log")
    if viewer_log.is_file():
        with st.expander("Log del visor Gmsh", expanded=False):
            st.code(tail_file(viewer_log, 80), language="text")
    show_json_report(mesh_root / "mesh_quality_report.json", "Informe de calidad")
    render_mesh_quality_summary(mesh_root / "mesh_quality_report.json")
    with st.expander("Revision de espesor de Boundary Layer", expanded=False):
        try:
            bl_prefix = "open" if variant_has_open_inlet(variant) else "closed"
            bl_conditions = workflow.get("case_conditions") or {}
            bl_review = boundary_layer_comparison(
                chord_m=float(active_variant_chord_m(variant) or 1.0),
                reynolds=float(bl_conditions.get("reynolds", 4.0e6)),
                target_y_plus=float(mesh_config.get("target_y_plus", 1.0)),
                rho_kg_m3=float(bl_conditions.get("rho_kg_m3", 1.225)),
                mu_pa_s=float(bl_conditions.get("mu_pa_s", 1.81e-5)),
                layers=int(mesh_config.get(f"{bl_prefix}_boundary_layer_layers", 50)),
                growth_rate=float(mesh_config.get(f"{bl_prefix}_boundary_layer_growth", 1.075)),
                manual_y1_m=float(mesh_config.get(f"{bl_prefix}_first_cell_height_m", 2.5e-5)),
                use_yplus_y1=bool(mesh_config.get(f"{bl_prefix}_use_yplus_first_cell_height", True)),
            )
            render_records_table([bl_review], max_rows=1)
            st.caption("Revision informativa del borrador activo; no cambia automaticamente y1, capas ni growth.")
        except (TypeError, ValueError) as exc:
            st.warning(f"No se pudo calcular la revision de BL: {exc}")
    show_json_report(mesh_root / "checkMesh_problem_locations.json", "Localizacion de celdas/caras problematicas")
    problem_vtks = list((mesh_root / "checkMesh_problem_locations").glob("*.vtk"))
    quality_report_path = mesh_root / "mesh_quality_report.json"
    viewer_cols = st.columns([1, 2, 3])
    open_problem_now = viewer_cols[0].button(
        "Abrir fallos en ParaView",
        disabled=not problem_vtks,
        help="Carga la polyMesh convertida y los sets VTK exactos escritos por checkMesh.",
    )
    auto_open_problem = viewer_cols[1].toggle(
        "Abrir automaticamente tras un FAIL nuevo",
        value=False,
        help="Se abre una sola vez por revision del informe; no vuelve a abrirse en cada refresco de Streamlit.",
    )
    viewer_cols[2].caption(
        f"Sets VTK disponibles: {len(problem_vtks)}. ParaView permite colorear y aislar las caras/celdas que activaron cada check."
    )
    quality_data = read_json(quality_report_path, {}) or {}
    report_revision = quality_report_path.stat().st_mtime_ns if quality_report_path.is_file() else None
    auto_state_key = f"problem-vtk-opened:{variant}"
    should_auto_open = bool(
        auto_open_problem
        and problem_vtks
        and quality_data.get("checkMesh_status") == "FAIL"
        and st.session_state.get(auto_state_key) != report_revision
    )
    if open_problem_now or should_auto_open:
        try:
            paraview_pid = open_checkmesh_problem_viewer(ROOT, variant)
            st.session_state[auto_state_key] = report_revision
            st.success(f"ParaView iniciado con la malla y {len(problem_vtks)} sets de calidad (PID {paraview_pid}).")
        except Exception as exc:
            st.error(str(exc))
    optimization_reports = latest_files(ROOT / "CFD_2D/reports", [f"mesh_optimization_{variant}_*.json"], 1)
    if optimization_reports:
        show_json_report(optimization_reports[0], "Ultima optimizacion de malla")
    show_images([
        mesh_root / "mesh_preview_front_surface.png",
        mesh_root / "mesh_preview_te.png",
        mesh_root / "mesh_preview_inlet.png",
    ], 3)
    st.subheader("Aprobacion")
    st.caption(
        "La habilitacion tecnica de la salida activa y la decision persistente del paquete guardado son controles distintos. "
        "La aprobacion persistente pertenece a una revision inmutable y sobrevive a su reutilizacion."
    )
    approve_cols = st.columns([1, 1, 4])
    if approve_cols[0].button("Habilitar malla activa"):
        start_job("approve_mesh", approve_mesh_command(ROOT, variant, force=False))
    force = approve_cols[1].checkbox("Habilitar force-debug")
    if approve_cols[2].button("Forzar aprobacion de debug", disabled=not force):
        start_job("force_approve_mesh", approve_mesh_command(ROOT, variant, force=True))
    approve_cols[2].caption("Forzar solo permite continuar el debugging; no valida calidad aerodinamica.")
    if selected_saved_mesh:
        current_approval = dict(selected_saved_mesh.get("approval") or {})
        st.caption(
            f"Paquete seleccionado: {selected_saved_mesh['case_name']} / {selected_saved_mesh['package_name']} | "
            f"revision {selected_saved_mesh.get('revision_id')} | estado {current_approval.get('status', 'pending')}"
        )
        with st.form("persistent-mesh-approval-form"):
            decision_cols = st.columns([1, 1, 3])
            decision = decision_cols[0].selectbox("Decision", ["approved", "rejected", "pending"])
            actor = decision_cols[1].text_input("Responsable", value="local-user")
            evidence = decision_cols[2].text_input(
                "Evidencia / comentario",
                value=str(selected_saved_mesh.get("quality_report") or "Revision visual y de calidad pendiente"),
            )
            record_decision = st.form_submit_button("Registrar decision persistente", type="primary")
        if record_decision:
            start_job(
                "library_approve_mesh",
                case_library_command(
                    ROOT, "approve", stage="mesh",
                    case_name=str(selected_saved_mesh["case_name"]),
                    package_name=str(selected_saved_mesh["package_name"]),
                    approval_status=decision, actor=actor, evidence=evidence,
                ),
                completion_action={"kind": "select_case", "case": selected_saved_mesh["case_name"]},
            )
    else:
        st.info("Guarda la malla en el Caso de trabajo para registrar una aprobacion persistente.")
    case_library_panel(
        "mesh",
        variant,
        alpha,
        library_case_selection,
    )
if active_page == "Caso OpenFOAM" and workflow_case_ready:
    st.info(TAB_INTROS["Caso OpenFOAM"])
    st.caption(
        "La aplicacion usa automaticamente la version mas reciente del esquema interno. "
        "Solo se muestran opciones fisicas, numericas y de escritura que el usuario puede modificar."
    )
    solver_config_editor()
    with st.expander("Interpretacion de hotspots de Courant y deltaT", expanded=False):
        st.markdown(
            """
- El techo fisico normal es `maxDeltaT*`, definido por la resolucion temporal y el contenido espectral que se desea conservar.
- `adjustTimeStep` conserva una salvaguarda de Courant para responder a aumentos no lineales. En el flujo general se usa `maxCo=50` para perfiles cerrados y `maxCo=25` para abiertos; no son objetivos de precision.
- `backward` es implicito y de segundo orden, pero un Courant alto sigue degradando la precision temporal. No convierte en prescindible el estudio de independencia de `deltaT`.
- `localEuler` usa pseudo-tiempo local y solo es apropiado para acelerar el inicializador estacionario; no representa tiempo fisico URANS.
- La primera correccion debe ser geometrica: eliminar refinamiento tangencial innecesario, evitar aristas casi nulas, suavizar el crecimiento y mejorar volumen/skewness en el hotspot sin aumentar `y1` si el objetivo de `y+` lo impide.
- La independencia temporal se comprueba variando `deltaT*` con la misma malla y comparando fuerzas, frecuencia y campos. Validation Lab mantiene `deltaT` y correctores fijos y no hereda esta politica adaptativa.
"""
        )
        st.caption(
            "En la malla de validacion medida, la celda limitante estaba en el cierre TE inferior. "
            "Reducir solo la discretizacion de la curva TE de 70/45 a 35/25 mantuvo calidad B "
            "y aumento aproximadamente un 44% el deltaT adaptativo; reducir toda la pared empeoro el determinante."
        )
    case_library_panel(
        "solver",
        variant,
        alpha,
        library_case_selection,
    )
    conditions = load_config(ROOT, "workflow").get("case_conditions", {})
    configured_writer_alphas = [float(value) for value in (conditions.get("alphas_deg") or [alpha])]
    with st.form("case-writer-form"):
        condition_cols = st.columns(3)
        condition_cols[0].metric("Reynolds (CFD Case)", f"{float(conditions.get('reynolds', 0.0)):.6g}")
        condition_cols[1].metric("Mach (CFD Case)", f"{float(conditions.get('mach', 0.0)):.6g}")
        condition_cols[2].metric("Angulos disponibles", len(configured_writer_alphas))
        require_poly = st.toggle("Exigir polyMesh convertido y aprobado", value=True, key=revisioned_widget_key("writer-require-poly"))
        write_scope = st.radio(
            "Angulos a preparar",
            ["current", "all", "subset"],
            format_func=lambda value: {
                "current": "Angulo activo",
                "all": "Todos los angulos del CFD Case",
                "subset": "Subconjunto seleccionado",
            }[value],
            horizontal=True,
            help="El writer prepara las carpetas de forma secuencial y no ejecuta OpenFOAM.",
            key=revisioned_widget_key("writer-all-alphas"),
        )
        selected_writer_alphas = st.multiselect(
            "Subconjunto de angulos [deg]",
            options=sorted(set(configured_writer_alphas)),
            default=[alpha] if alpha in configured_writer_alphas else configured_writer_alphas[:1],
            disabled=write_scope != "subset",
            key=revisioned_widget_key("writer-subset-alphas"),
        )
        existing_case_action = st.selectbox("Caso/resultados existentes", ["archive", "delete", "keep"], index=0, key=revisioned_widget_key("writer-existing-action"))
        write_case = st.form_submit_button("Escribir caso OpenFOAM", type="primary")
    if write_case:
        selected_case_alphas = (
            configured_writer_alphas
            if write_scope == "all"
            else [float(value) for value in selected_writer_alphas]
            if write_scope == "subset"
            else [alpha]
        )
        if not selected_case_alphas:
            st.error("Selecciona al menos un angulo para preparar el caso.")
            st.stop()
        existing_paths = [result_directory(ROOT, variant, selected_alpha) for selected_alpha in selected_case_alphas]
        backup = prepare_existing_outputs(
            ROOT,
            existing_paths,
            existing_case_action,
            f"{variant}_{'alpha_sweep' if len(selected_case_alphas) > 1 else f'{selected_case_alphas[0]:+.3f}'}",
        )
        if backup:
            st.info(f"Salida anterior archivada en {backup}")
        start_job("openfoam_case", case_writer_command(
            ROOT, variant=variant, alpha=selected_case_alphas[0],
            require_converted_polymesh=require_poly,
            alphas=selected_case_alphas if len(selected_case_alphas) > 1 else None,
            existing_case_action=existing_case_action,
        ))
    cdir = case_directory(ROOT, variant, alpha)
    st.caption(str(cdir))
    show_json_report(cdir / "case_config.json", "Descripcion completa del caso")

if active_page == "Ejecucion" and workflow_case_ready:
    st.info(TAB_INTROS["Ejecucion"])
    solver_cfg = load_config(ROOT, "solver")
    execution_cfg = workflow.get("execution", {})
    st.subheader("OpenFOAM")
    with st.form("runner-form"):
        configured_mode = str(execution_cfg.get("execution_mode", "single"))
        execution_mode = st.radio(
            "Modo de ejecucion",
            ["single", "sweep"],
            index=0 if configured_mode != "sweep" else 1,
            format_func=lambda value: "Un angulo" if value == "single" else "Barrido secuencial de angulos",
            horizontal=True,
            help="El barrido ejecuta un unico caso a la vez y aplica el timeout/convergencia antes de pasar al siguiente angulo.",
            key=revisioned_widget_key("runner-mode"),
        )
        configured_alphas = [float(value) for value in (workflow.get("case_conditions", {}).get("alphas_deg") or [alpha])]
        sweep_alphas = st.multiselect(
            "Angulos del barrido [deg]",
            options=sorted(set(configured_alphas + available_case_alphas(variant, workflow))),
            default=configured_alphas,
            disabled=execution_mode != "sweep",
            help="Las carpetas OpenFOAM de estos angulos deben haberse escrito previamente en Caso OpenFOAM.",
            key=revisioned_widget_key("runner-sweep-alphas"),
        )
        cols = st.columns(4)
        configured_solver = str(execution_cfg.get("solver", "auto"))
        solver = cols[0].selectbox("Solver", CHOICES["solver"], index=CHOICES["solver"].index(configured_solver) if configured_solver in CHOICES["solver"] else 0, key=revisioned_widget_key("runner-solver"))
        backend_options = ["native", "pyfoam"]
        configured_backend = str(execution_cfg.get("execution_backend", "native"))
        execution_backend = cols[1].selectbox(
            "Backend",
            backend_options,
            index=backend_options.index(configured_backend) if configured_backend in backend_options else 0,
            key=revisioned_widget_key("runner-backend"),
        )
        n_cores = cols[2].number_input(
            "Procesos MPI",
            min_value=1,
            max_value=16,
            value=int(execution_cfg.get("n_cores", 8)),
            help=(
                "Ocho procesos son el estandar verificado para el Ryzen 7 4800H. El runner comprueba "
                "los slots MPI, actualiza decomposeParDict y regenera processor* antes de ejecutar."
            ),
            key=revisioned_widget_key("runner-cores"),
        )
        timeout_min = cols[3].number_input(
            "Timeout [min]",
            min_value=1.0,
            value=float(execution_cfg.get("timeout_min", 120.0)),
            help="Al alcanzarlo se solicita stopAt writeNow, se espera una escritura limpia y se conservan los resultados parciales.",
            key=revisioned_widget_key("runner-timeout"),
        )
        cols = st.columns(3)
        stop_after = cols[0].number_input("Parada limpia tras [min], 0 desactiva", min_value=0.0, value=float(execution_cfg.get("stop_after_min") or 0.0), key=revisioned_widget_key("runner-stop-after"))
        configured_stop_mode = str(execution_cfg.get("stop_mode", "writeNow"))
        stop_mode = cols[1].selectbox("Modo de parada", CHOICES["stop_mode"], index=CHOICES["stop_mode"].index(configured_stop_mode) if configured_stop_mode in CHOICES["stop_mode"] else 0, key=revisioned_widget_key("runner-stop-mode"))
        strict_check = cols[2].toggle("Bloquear si checkMesh falla", value=not bool(execution_cfg.get("allow_failed_checkmesh_for_debug", False)), key=revisioned_widget_key("runner-strict-check"))
        pyfoam_live_monitor = st.toggle(
            "Mostrar monitor PyFoam en vivo dentro de la app",
            value=bool(execution_cfg.get("pyfoam_live_monitor", True)),
            help="PyFoam ejecuta y registra el solver. Un render Matplotlib sin ventana externa actualiza aqui residuos, Cl y Cd/Cm cada 30 segundos. Evita WSLg, Gnuplot y las ventanas vacias en copy mode; no recorta los datos guardados.",
            disabled=execution_backend != "pyfoam",
            key=revisioned_widget_key("runner-pyfoam-monitor"),
        )
        cleanup_processors = st.toggle(
            "Eliminar particiones MPI tras reconstruir",
            value=bool(execution_cfg.get("cleanup_processor_directories", True)),
            help="Conserva los campos reconstruidos y elimina solo processorN cuando el ultimo tiempo ya existe en la raiz. Desactivalo para diagnosticar la descomposicion.",
            key=revisioned_widget_key("runner-cleanup-processors"),
        )
        resume_cols = st.columns(2)
        configured_existing_simulation_action = str(
            execution_cfg.get(
                "existing_simulation_action",
                "resume" if execution_cfg.get("resume_existing", True) else "stop",
            )
        )
        existing_simulation_action = resume_cols[0].selectbox(
            "Si existen resultados de solver",
            ["resume", "delete", "stop"],
            index=["resume", "delete", "stop"].index(configured_existing_simulation_action) if configured_existing_simulation_action in {"resume", "delete", "stop"} else 0,
            format_func=lambda value: {
                "resume": "Continuar desde el ultimo tiempo",
                "delete": "Eliminar salida activa y empezar de cero",
                "stop": "Detener si ya existen datos",
            }[value],
            help="Eliminar afecta solo al caso activo. Los paquetes guardados explicitamente en Results no se modifican ni se copian a Previous Versions.",
            key=revisioned_widget_key("runner-resume"),
        )
        resume_existing = existing_simulation_action == "resume"
        resume_extension = resume_cols[1].number_input(
            "Extension al reanudar [t*]",
            min_value=0.0,
            value=float(execution_cfg.get("resume_additional_time_star") or 20.0),
            disabled=not resume_existing,
            help="Duracion convectiva adicional. El valor recomendado de 20 t* permite observar varios tiempos de paso; cero conserva el endTime original si todavia no se ha alcanzado.",
            key=revisioned_widget_key("runner-resume-extension"),
        )
        with st.expander("Inicializacion estacionaria antes del transitorio", expanded=False):
            steady_initialization = st.toggle(
                "Inicializacion estacionaria: ejecutar previamente al solver transitorio",
                value=bool(execution_cfg.get("steady_initialization", False)),
                help="Usa steadyState + SIMPLE + residualControl y transfiere los campos a 0/ antes de PIMPLE. No sustituye el resultado transitorio.",
                key=revisioned_widget_key("runner-steady-init"),
            )
            steady_live_monitor = st.toggle(
                "Mostrar monitor integrado tambien durante SIMPLE",
                value=bool(execution_cfg.get("steady_pyfoam_live_monitor", True)),
                disabled=not steady_initialization or execution_backend != "pyfoam",
                help="Lee los logs generados por PyFoam y actualiza el panel integrado con residuos, Cl y Cd/Cm; no abre una ventana WSLg independiente.",
                key=revisioned_widget_key("runner-steady-live-monitor"),
            )
            steady_cols = st.columns(4)
            steady_timeout_min = steady_cols[0].number_input(
                "Timeout estacionario [min]",
                min_value=1.0,
                value=float(execution_cfg.get("steady_timeout_min", 30.0)),
                disabled=not steady_initialization,
                key=revisioned_widget_key("runner-steady-timeout"),
            )
            steady_force_window = steady_cols[1].number_input(
                "Muestras por ventana de coeficientes",
                min_value=10,
                value=int(execution_cfg.get("steady_force_window_samples", execution_cfg.get("steady_force_window_iterations", 500))),
                disabled=not steady_initialization,
                help="Se comparan dos ventanas adyacentes de este numero de muestras. forceCoeffs escribe una muestra por iteracion SIMPLE, por lo que se requieren al menos 2N muestras.",
                key=revisioned_widget_key("runner-steady-force-window"),
            )
            legacy_steady_tolerance = float(execution_cfg.get("steady_force_mean_tolerance", 0.01))
            steady_force_tolerance_percent = steady_cols[2].number_input(
                "Cambio/deriva maxima [%]",
                min_value=0.01,
                max_value=100.0,
                value=float(execution_cfg.get("steady_force_mean_tolerance_percent", 100.0 * legacy_steady_tolerance)),
                format="%.2f",
                disabled=not steady_initialization,
                help="Limite porcentual para el cambio de la media entre ventanas y para la deriva entre las dos mitades de la ventana final; deben cumplirlo Cl, Cd y Cm.",
                key=revisioned_widget_key("runner-steady-force-tolerance"),
            )
            steady_force_fluctuation_percent = steady_cols[3].number_input(
                "Fluctuacion maxima [%]",
                min_value=0.01,
                max_value=100.0,
                value=float(execution_cfg.get("steady_force_fluctuation_tolerance_percent", 2.0)),
                format="%.2f",
                disabled=not steady_initialization,
                help="Desviacion estandar de cada coeficiente en la ventana final, expresada como porcentaje de una escala robusta que permanece definida cerca de cero.",
                key=revisioned_widget_key("runner-steady-force-fluctuation"),
            )
            continue_after_steady_timeout = st.toggle(
                "Iniciar transitorio automaticamente aunque SIMPLE no cumpla los criterios",
                value=bool(execution_cfg.get("force_transient_after_unconverged_steady", False)),
                disabled=not steady_initialization,
                help="Override diagnostico. Si esta desactivado, la aplicacion mostrara las metricas y permitira ampliar SIMPLE, iniciar el transitorio manualmente o finalizar.",
                key=revisioned_widget_key("runner-steady-override"),
            )
            steady_paraview_snapshots = st.number_input(
                "Instantaneas SIMPLE conservadas para ParaView",
                min_value=2,
                max_value=30,
                value=int(execution_cfg.get("steady_paraview_snapshots", 6)),
                disabled=not steady_initialization,
                help="Conserva muestras equiespaciadas y el estado final en un caso ParaView independiente. El eje temporal representa iteraciones SIMPLE, no segundos.",
                key=revisioned_widget_key("runner-steady-paraview-snapshots"),
            )
        sweep_cols = st.columns(3)
        continue_sweep_after_timeout = sweep_cols[0].toggle(
            "Continuar al siguiente angulo tras timeout parcial",
            value=bool(execution_cfg.get("continue_sweep_after_timeout", True)),
            disabled=execution_mode != "sweep",
            key=revisioned_widget_key("runner-sweep-timeout"),
        )
        postprocess_after_each = sweep_cols[1].toggle(
            "Postprocesar cada angulo al terminar",
            value=bool(execution_cfg.get("postprocess_after_each_alpha", False)),
            disabled=execution_mode != "sweep",
            key=revisioned_widget_key("runner-sweep-postprocess"),
        )
        continue_sweep_after_error = sweep_cols[2].toggle(
            "Continuar tras error de un angulo",
            value=bool(execution_cfg.get("continue_sweep_after_error", True)),
            disabled=execution_mode != "sweep",
            help="Registra el error y continua con el siguiente caso. El angulo fallido queda disponible para inspeccion o reanudacion posterior.",
            key=revisioned_widget_key("runner-sweep-error"),
        )
        with st.expander("Parada por estabilidad estadistica de fuerzas", expanded=False):
            convergence_topology = "open" if variant_has_open_inlet(variant) else "closed"
            default_minimum_tstar = 8.0 if convergence_topology == "open" else 4.0
            default_window_tstar = 2.0 if convergence_topology == "open" else 1.0
            topology_minimum_key = f"{convergence_topology}_convergence_minimum_time_star"
            topology_window_key = f"{convergence_topology}_convergence_window_time_star"
            stop_when_stable = st.toggle(
                "Detener en la siguiente escritura cuando Cl, Cd y Cm sean estadisticamente estables",
                value=bool(execution_cfg.get("stop_when_force_stable", False)),
                help=(
                    "Compara media y desviacion estandar en dos ventanas consecutivas de tiempo convectivo t*=tU/c. "
                    "Admite oscilaciones estacionarias y no usa un residuo aislado como criterio de convergencia. "
                    "Esta salvaguarda es opcional y no sustituye la inspeccion de las historias temporales."
                ),
                key=revisioned_widget_key("runner-stop-when-stable"),
            )
            st.caption(
                f"Perfil {convergence_topology}: se requieren tres comprobaciones estables consecutivas. "
                "El perfil abierto conserva ventanas mas largas por la dinamica de la capa de cizalladura y la cavidad."
            )
            conv_cols = st.columns(4)
            convergence_minimum_time_star = conv_cols[0].number_input(
                "t* minimo",
                min_value=1.0,
                value=float(execution_cfg.get(topology_minimum_key, default_minimum_tstar)),
                disabled=not stop_when_stable,
                help="No se permite una parada estadistica antes de este numero de tiempos convectivos.",
                key=revisioned_widget_key("runner-convergence-min-time"),
            )
            convergence_window_time_star = conv_cols[1].number_input(
                "Ancho de ventana t*",
                min_value=0.5,
                value=float(execution_cfg.get(topology_window_key, default_window_tstar)),
                disabled=not stop_when_stable,
                help="Se comparan dos ventanas adyacentes de esta duracion; la simulacion debe cubrir ambas.",
                key=revisioned_widget_key("runner-convergence-window"),
            )
            convergence_mean_tolerance = conv_cols[2].number_input(
                "Tolerancia medias",
                min_value=0.0001,
                max_value=1.0,
                value=float(execution_cfg.get("convergence_mean_tolerance", 0.02)),
                format="%.4f",
                disabled=not stop_when_stable,
                help="Cambio normalizado maximo de las medias de Cl, Cd y Cm entre las dos ventanas.",
                key=revisioned_widget_key("runner-convergence-mean-tol"),
            )
            convergence_oscillation_tolerance = conv_cols[3].number_input(
                "Tolerancia amplitud",
                min_value=0.0001,
                max_value=1.0,
                value=float(execution_cfg.get("convergence_oscillation_tolerance", 0.10)),
                format="%.4f",
                disabled=not stop_when_stable,
                help="Cambio normalizado maximo de la desviacion estandar entre ventanas; no exige oscilacion nula.",
                key=revisioned_widget_key("runner-convergence-std-tol"),
            )
        confirm_run = st.checkbox("Confirmo que deseo ejecutar el solver y generar resultados CFD")
        runner_dry = st.form_submit_button("Preparar y mostrar dry-run")
        runner_real = st.form_submit_button("Ejecutar solver", type="primary")
    execution_update = {
        "execution_mode": execution_mode,
        "execution_backend": execution_backend,
        "solver": solver,
        "n_cores": int(n_cores),
        "timeout_min": float(timeout_min),
        "stop_after_min": float(stop_after) if stop_after else None,
        "stop_mode": stop_mode,
        "pyfoam_live_monitor": bool(pyfoam_live_monitor),
        "cleanup_processor_directories": bool(cleanup_processors),
        "existing_simulation_action": existing_simulation_action,
        "resume_existing": bool(resume_existing),
        "resume_additional_time_star": float(resume_extension) if resume_extension > 0 else None,
        "steady_initialization": bool(steady_initialization),
        "steady_timeout_min": float(steady_timeout_min),
        "steady_force_window_samples": int(steady_force_window),
        "steady_force_mean_tolerance_percent": float(steady_force_tolerance_percent),
        "steady_force_fluctuation_tolerance_percent": float(steady_force_fluctuation_percent),
        "steady_pyfoam_live_monitor": bool(steady_live_monitor),
        "steady_paraview_snapshots": int(steady_paraview_snapshots),
        "force_transient_after_unconverged_steady": bool(continue_after_steady_timeout),
        "continue_sweep_after_timeout": bool(continue_sweep_after_timeout),
        "continue_sweep_after_error": bool(continue_sweep_after_error),
        "postprocess_after_each_alpha": bool(postprocess_after_each),
        "stop_when_force_stable": bool(stop_when_stable),
        "convergence_minimum_time_star": float(convergence_minimum_time_star),
        "convergence_window_time_star": float(convergence_window_time_star),
        "convergence_mean_tolerance": float(convergence_mean_tolerance),
        "convergence_oscillation_tolerance": float(convergence_oscillation_tolerance),
        topology_minimum_key: float(convergence_minimum_time_star),
        topology_window_key: float(convergence_window_time_star),
    }

    def execution_command(
        run_enabled: bool,
        *,
        steady_decision: str = "auto",
        steady_additional_iterations: int = 500,
    ) -> list[str]:
        if execution_mode == "sweep" and steady_decision == "auto":
            if not sweep_alphas:
                raise ValueError("Selecciona al menos un angulo para el barrido.")
            return sweep_runner_command(
                ROOT,
                variant=variant,
                alphas=[float(value) for value in sweep_alphas],
                solver=solver,
                execution_backend=execution_backend,
                n_cores=int(n_cores),
                timeout_min_per_alpha=float(timeout_min),
                run=run_enabled,
                steady_initialization=bool(steady_initialization),
                steady_timeout_min=float(steady_timeout_min),
                steady_force_window_samples=int(steady_force_window),
                steady_force_mean_tolerance_percent=float(steady_force_tolerance_percent),
                steady_force_fluctuation_tolerance_percent=float(steady_force_fluctuation_percent),
                continue_transient_after_steady_timeout=bool(continue_after_steady_timeout),
                resume_existing=bool(resume_existing),
                resume_additional_time_star=float(resume_extension) if resume_extension > 0 else None,
                continue_after_timeout=bool(continue_sweep_after_timeout),
                stop_when_force_stable=bool(stop_when_stable),
                convergence_minimum_time_star=float(convergence_minimum_time_star),
                convergence_window_time_star=float(convergence_window_time_star),
                convergence_mean_tolerance=float(convergence_mean_tolerance),
                convergence_oscillation_tolerance=float(convergence_oscillation_tolerance),
                stop_if_checkmesh_fails=bool(strict_check),
                pyfoam_live_monitor=bool(pyfoam_live_monitor),
                steady_pyfoam_live_monitor=bool(steady_live_monitor),
                cleanup_processor_directories=bool(cleanup_processors),
                postprocess_after_each=bool(postprocess_after_each),
                continue_after_error=bool(continue_sweep_after_error),
                average_from_fraction=float(solver_cfg.get("average_from_fraction", 0.6)),
            )
        return staged_runner_command(
            ROOT,
            variant=variant,
            alpha=alpha,
            solver=solver,
            execution_backend=execution_backend,
            n_cores=int(n_cores),
            timeout_min=float(timeout_min),
            run=run_enabled,
            stop_if_checkmesh_fails=bool(strict_check),
            pyfoam_live_monitor=bool(pyfoam_live_monitor),
            cleanup_processor_directories=bool(cleanup_processors),
            stop_when_force_stable=bool(stop_when_stable),
            convergence_minimum_time_star=float(convergence_minimum_time_star),
            convergence_window_time_star=float(convergence_window_time_star),
            convergence_mean_tolerance=float(convergence_mean_tolerance),
            convergence_oscillation_tolerance=float(convergence_oscillation_tolerance),
            steady_initialization=bool(steady_initialization and steady_decision == "auto"),
            steady_timeout_min=float(steady_timeout_min),
            steady_force_window_samples=int(steady_force_window),
            steady_force_mean_tolerance_percent=float(steady_force_tolerance_percent),
            steady_force_fluctuation_tolerance_percent=float(steady_force_fluctuation_percent),
            continue_transient_after_steady_timeout=bool(continue_after_steady_timeout),
            resume=bool(resume_existing),
            resume_additional_time_star=float(resume_extension) if resume_extension > 0 else None,
            stop_grace_min=float(execution_cfg.get("stop_grace_min", 5.0)),
            stop_after_min=float(stop_after) if stop_after else None,
            steady_pyfoam_live_monitor=bool(steady_live_monitor),
            steady_decision=steady_decision,
            steady_additional_iterations=int(steady_additional_iterations),
            steady_paraview_snapshots=int(steady_paraview_snapshots),
        )

    with st.expander("Paquete de ejecucion para servidor Linux/WSL", expanded=False):
        st.caption(
            "Congela los casos seleccionados, la cola, los scripts de parada/reinicio, "
            "postproceso y comprobaciones SHA-256. OpenFOAM 14 y MPI se usan de forma "
            "nativa en el servidor; no se duplica su instalacion dentro del ZIP."
        )
        package_columns = st.columns(2)
        remote_offline_wheels = package_columns[0].toggle(
            "Incluir dependencias Python offline",
            value=True,
            help="Descarga wheels Linux para instalar el entorno sin Internet en el servidor. Aumenta el ZIP y tarda mas al crearlo.",
            key=revisioned_widget_key("remote-package-wheelhouse"),
        )
        remote_postprocess_note = package_columns[1].toggle(
            "Preparar postproceso automatico",
            value=True,
            disabled=True,
            help="El paquete siempre incluye postprocess_remote.sh; pvbatch es opcional en el servidor.",
            key=revisioned_widget_key("remote-package-postprocess"),
        )
        if st.button("Crear paquete remoto con esta cola", key="create-remote-execution-package"):
            selected_remote_alphas = (
                [float(value) for value in sweep_alphas]
                if execution_mode == "sweep"
                else [float(alpha)]
            )
            selected_cases = [case_directory(ROOT, variant, value) for value in selected_remote_alphas]
            missing_cases = [str(path) for path in selected_cases if not (path / "system/controlDict").is_file()]
            if missing_cases:
                st.error("Escribe primero los casos OpenFOAM que faltan: " + ", ".join(missing_cases))
            else:
                command = [
                    sys.executable,
                    str(project_path(ROOT, "tools", "package_ramair_remote_execution.py")),
                    "--project-root", str(ROOT),
                    "--n-cores", str(int(n_cores)),
                    "--timeout-min", str(float(timeout_min)),
                    "--steady-timeout-min", str(float(steady_timeout_min)),
                    "--resume-additional-time-star", str(float(resume_extension)),
                ]
                for selected_case in selected_cases:
                    command += ["--case", str(selected_case)]
                command.append("--steady-initialization" if steady_initialization else "--no-steady-initialization")
                if remote_offline_wheels:
                    command.append("--download-wheelhouse")
                start_job("remote_execution_package", command)

    if runner_dry:
        update_workflow_sections(execution=execution_update)
        try:
            start_job("runner_dry", execution_command(False))
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            st.error(str(exc))
    if runner_real and confirm_run:
        update_workflow_sections(execution=execution_update)
        try:
            selected_execution_alphas = [float(value) for value in sweep_alphas] if execution_mode == "sweep" else [float(alpha)]
            removed_outputs: list[str] = []
            for selected_alpha in selected_execution_alphas:
                removed_outputs.extend(
                    prepare_existing_simulation(
                        case_directory(ROOT, variant, selected_alpha),
                        existing_simulation_action,
                    )
                )
            if removed_outputs:
                st.info(f"Se eliminaron {len(removed_outputs)} salidas activas antes de iniciar. No se creo ningun backup automatico.")
            start_job("solver_sweep" if execution_mode == "sweep" else "solver", execution_command(True))
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            st.error(str(exc))
    elif runner_real:
        st.error("La ejecucion real requiere marcar la confirmacion explicita.")
    cdir = case_directory(ROOT, variant, alpha)
    show_json_report(cdir / "run_status.json", "Estado de ejecucion")
    staged_status_path = cdir / "staged_run_status.json"
    staged_status = read_json(staged_status_path, {}) or {}
    show_json_report(staged_status_path, "Estado estacionario/transitorio")
    steady_history = cdir / "steadyInitialization" / "history"
    steady_runs = sorted(
        (path for path in steady_history.glob("run_*") if (path / "paraview_case/system/controlDict").is_file()),
        key=lambda path: path.stat().st_mtime,
    ) if steady_history.is_dir() else []
    if steady_runs:
        latest_steady = steady_runs[-1]
        steady_cols = st.columns(2)
        if steady_cols[0].button(
            "Abrir inicializacion estacionaria en ParaView",
            help="Carga un caso independiente cuyas coordenadas temporales son iteraciones SIMPLE, no segundos.",
        ):
            try:
                result = open_paraview_case(ROOT, latest_steady / "paraview_case")
                st.success(f"ParaView solicitado (PID {result.get('pid')}).")
            except (RuntimeError, FileNotFoundError) as exc:
                st.error(str(exc))
        steady_cols[1].caption(str(latest_steady / "paraview_case"))
        steady_paraview_support = latest_steady / "paraview_case" / "postProcessing" / "ParaView"
        steady_paraview_ready = steady_paraview_support / "case_latest.ready.json"
        if steady_paraview_ready.is_file():
            show_json_report(steady_paraview_ready, "Evidencia de carga estacionaria en ParaView")
            steady_paraview_screenshot = steady_paraview_support / "case_latest.png"
            if steady_paraview_screenshot.is_file():
                preview_col, _ = st.columns([2, 1])
                preview_col.image(
                    str(steady_paraview_screenshot),
                    caption="Ultima vista estacionaria renderizada por ParaView",
                    width="stretch",
                )
        else:
            st.caption(
                "El paquete estacionario esta preparado. El estado READY solo aparecera cuando "
                "ParaView haya cargado y renderizado el caso."
            )
        efficiency_image = latest_steady / "aerodynamic_efficiency_steady.png"
        if efficiency_image.is_file():
            image_col, _ = st.columns([2, 1])
            image_col.image(
                str(efficiency_image),
                caption="Cl/Cd durante la inicializacion SIMPLE (rango visual 0-100)",
                width=560,
            )
    if staged_status.get("status") == "STEADY_STAGE_DIVERGED":
        failure = staged_status.get("steady_failure") or {}
        pyfoam = failure.get("pyfoam_report") or {}
        diagnostics = pyfoam.get("divergence_diagnostics") or {}
        trigger = diagnostics.get("first_trigger") or {}
        st.error(
            "La inicializacion SIMPLE divergio y no se transfirio al transitorio. "
            "Los campos parciales y logs se archivaron y el 0/ transitorio original fue restaurado."
        )
        if trigger:
            st.code(json.dumps(trigger, indent=2), language="json")
    if staged_status.get("status") == "STEADY_AWAITING_USER_DECISION":
        st.warning(
            "SIMPLE termino sin cumplir todos los criterios. Los campos, residuos y coeficientes se han conservado; "
            "elige como continuar sin repetir ni perder la etapa alcanzada."
        )
        transition = staged_status.get("steady_transition") or {}
        force_plateau = transition.get("force_plateau") or {}
        coefficient_rows = []
        for coefficient, values in (force_plateau.get("metrics") or {}).items():
            coefficient_rows.append({
                "coeficiente": coefficient,
                "media_previa": values.get("previous_mean"),
                "media_actual": values.get("current_mean"),
                "cambio_media_%": values.get("mean_change_percent"),
                "deriva_ventana_%": values.get("current_window_drift_percent"),
                "fluctuacion_%": values.get("current_fluctuation_percent"),
                "cumple": values.get("stable"),
            })
        if coefficient_rows:
            render_records_table(coefficient_rows, max_rows=4)
        residual_rows = [
            {
                "campo": field,
                "residuo_inicial_final": values.get("last_initial_residual"),
                "limite": values.get("tolerance"),
                "cumple": values.get("acceptable"),
            }
            for field, values in (transition.get("residual_metrics") or {}).items()
        ]
        if residual_rows:
            render_records_table(residual_rows, max_rows=8)
        st.caption(
            "La ventana contiene N muestras por coeficiente y se compara con las N anteriores. "
            "La fluctuacion es la desviacion estandar porcentual de la ultima ventana; la deriva compara sus dos mitades."
        )
        additional_steady_iterations = st.number_input(
            "Iteraciones SIMPLE adicionales",
            min_value=10,
            value=int(execution_cfg.get("steady_additional_iterations", 500)),
            step=50,
            key=revisioned_widget_key("steady-pending-additional-iterations"),
        )
        decision_cols = st.columns(3)
        if decision_cols[0].button("Ampliar estacionario", type="primary"):
            start_job(
                "steady_extend",
                execution_command(
                    True,
                    steady_decision="extend",
                    steady_additional_iterations=int(additional_steady_iterations),
                ),
            )
        if decision_cols[1].button("Iniciar transitorio con estos campos"):
            start_job(
                "steady_start_transient",
                execution_command(True, steady_decision="start-transient"),
            )
        if decision_cols[2].button("Finalizar sin transitorio"):
            start_job(
                "steady_finish",
                execution_command(True, steady_decision="finish"),
            )
    show_json_report(ROOT / "CFD_2D" / "openfoam_cases" / variant / "alpha_sweep_status.json", "Estado del barrido secuencial")
    st.caption(
        "PyFoam conserva el log completo y un snapshot Matplotlib sin ventana externa se actualiza dentro de esta pagina. Al finalizar o detenerse, genera graficas de residuos, "
        "Cl y Cd/Cm por separado. Courant, continuidad, iteraciones lineales, deltaT y tiempo de ejecucion "
        "permanecen en el log tecnico, pero no abren monitores. Los rangos visuales son Cl [-0.8, 2] y "
        "Cd/Cm [-0.2, 0.2]; los datos originales no se recortan."
    )
    show_images(
        latest_files(
            cdir / "postProcessing/PyFoamPlots",
            ["linear_residuals.png", "lift_coefficient.png", "drag_moment_coefficients.png"],
            3,
        ),
        3,
    )

if active_page == "Postproceso" and workflow_case_ready:
    st.info(TAB_INTROS["Postproceso"])
    post_cfg = workflow.get("postprocess", {})
    solver_cfg = load_config(ROOT, "solver")
    with st.form("postprocess-form"):
        cols = st.columns(3)
        average_fraction = cols[0].number_input("Fraccion inicial para promedio", min_value=0.0, max_value=0.99, value=float(solver_cfg.get("average_from_fraction", 0.6)), key=revisioned_widget_key("post-average-fraction"))
        configured_export_mode = str(post_cfg.get("export_mode", "openfoam_reader"))
        if configured_export_mode not in CHOICES["export_mode"]:
            configured_export_mode = "openfoam_reader"
        export_mode = cols[1].selectbox("Exportacion", CHOICES["export_mode"], index=CHOICES["export_mode"].index(configured_export_mode), key=revisioned_widget_key("post-export-mode"))
        post_timeout = cols[2].number_input("Timeout utilidades [s]", min_value=30, value=int(post_cfg.get("timeout_s", 300)), key=revisioned_widget_key("post-timeout"))
        cols = st.columns(3)
        run_post = cols[0].toggle("Calcular Co, yPlus, wallShearStress y vorticidad", value=bool(post_cfg.get("run_openfoam_postprocess", True)), key=revisioned_widget_key("post-openfoam-functions"))
        open_folder = cols[1].toggle("Abrir carpeta de resultados", value=bool(post_cfg.get("open_results_folder", False)), key=revisioned_widget_key("post-open-folder"))
        open_pv = cols[2].toggle("Abrir ParaView", value=bool(post_cfg.get("open_paraview", False)), key=revisioned_widget_key("post-open-paraview"))
        product_cols = st.columns(2)
        automatic_pv = product_cols[0].toggle(
            "Generar escenas finales Cp/Co/velocidad y animaciones",
            value=bool(post_cfg.get("automatic_paraview_products", False)),
            help="Usa pvbatch y el lector OpenFOAM directo. No duplica el volumen completo con foamToVTK.",
            key=revisioned_widget_key("post-automatic-paraview"),
        )
        maximum_frames = product_cols[1].number_input(
            "Maximo de fotogramas por animacion",
            min_value=2,
            max_value=100,
            value=int(post_cfg.get("paraview_maximum_frames", 24)),
            disabled=not automatic_pv,
            key=revisioned_widget_key("post-paraview-frames"),
        )
        wall_profiles = st.toggle(
            "Analizar y+(x/c), Cp(x/c), perfiles de velocidad y espesor de capa limite",
            value=bool(post_cfg.get("wall_profile_analysis", True)),
            help="Usa yPlus y Cp reales en las caras wall y muestreo OpenFOAM normal a intrados/extrados; no genera CSV/PNG vacios si faltan campos.",
            key=revisioned_widget_key("post-wall-profiles"),
        )
        profile_cols = st.columns(2)
        station_default = ", ".join(str(value) for value in post_cfg.get("velocity_profile_stations_xc", solver_cfg.get("velocity_profile_stations_xc", [0.1, 0.3, 0.6, 0.9])))
        stations_text = profile_cols[0].text_input(
            "Estaciones x/c",
            value=station_default,
            disabled=not wall_profiles,
            help="Valores separados por comas entre 0 y 1; se muestrean en intrados y extrados.",
            key=revisioned_widget_key("post-profile-stations"),
        )
        profile_points = profile_cols[1].number_input(
            "Puntos por perfil",
            min_value=10,
            max_value=500,
            value=int(post_cfg.get("velocity_profile_sample_points", solver_cfg.get("velocity_profile_sample_points", 40))),
            disabled=not wall_profiles,
            key=revisioned_widget_key("post-profile-points"),
        )
        post_submit = st.form_submit_button("Postprocesar", type="primary")
    try:
        profile_stations = sorted({float(value.strip()) for value in stations_text.split(",") if value.strip()})
    except ValueError:
        profile_stations = []
    if post_submit:
        if wall_profiles and (not profile_stations or any(value <= 0.0 or value >= 1.0 for value in profile_stations)):
            st.error("Las estaciones deben ser numeros separados por comas dentro de 0 < x/c < 1.")
            st.stop()
        solver_cfg["average_from_fraction"] = float(average_fraction)
        solver_cfg["velocity_profile_stations_xc"] = profile_stations
        solver_cfg["velocity_profile_sample_points"] = int(profile_points)
        save_config(ROOT, "solver", solver_cfg)
        update_workflow_sections(postprocess={
            "run_openfoam_postprocess": bool(run_post),
            "export_mode": export_mode,
            "open_results_folder": bool(open_folder),
            "open_paraview": bool(open_pv),
            "timeout_s": int(post_timeout),
            "wall_profile_analysis": bool(wall_profiles),
            "velocity_profile_stations_xc": profile_stations,
            "velocity_profile_sample_points": int(profile_points),
            "automatic_paraview_products": bool(automatic_pv),
            "paraview_maximum_frames": int(maximum_frames),
        })

        start_job("postprocess", postprocess_command(
            ROOT, variant=variant, alpha=alpha, average_from_fraction=average_fraction,
            run_openfoam_postprocess=run_post, export_mode=export_mode, timeout_s=post_timeout,
            open_results_folder=open_folder, open_paraview=open_pv,
            wall_profile_analysis=wall_profiles,
            velocity_profile_stations=profile_stations,
            velocity_profile_sample_points=profile_points,
            automatic_paraview_products=automatic_pv,
            paraview_maximum_frames=maximum_frames,
        ))
    st.subheader("Postproceso y validacion por lotes")
    postprocess_alpha_options = available_case_alphas(variant, workflow)
    selected_postprocess_alphas = st.multiselect(
        "Angulos a procesar o publicar",
        options=postprocess_alpha_options,
        default=[alpha] if alpha in postprocess_alpha_options else [],
        help="Cada angulo se procesa de forma independiente. Un fallo queda registrado y no borra los resultados de los demas.",
        key=revisioned_widget_key("post-batch-alphas"),
    )
    batch_cols = st.columns(3)
    if batch_cols[0].button(
        "Postprocesar angulos seleccionados",
        disabled=not selected_postprocess_alphas,
        type="primary",
    ):
        start_job(
            "postprocess_batch",
            batch_postprocess_command(
                ROOT,
                variant=variant,
                alphas=[float(value) for value in selected_postprocess_alphas],
                average_from_fraction=float(average_fraction),
                run_openfoam_postprocess=bool(run_post),
                export_mode=str(export_mode),
                timeout_s=int(post_timeout),
                wall_profile_analysis=bool(wall_profiles),
                velocity_profile_stations=profile_stations,
                velocity_profile_sample_points=int(profile_points),
                automatic_paraview_products=bool(automatic_pv),
                paraview_maximum_frames=int(maximum_frames),
            ),
        )
    validation_enabled = (
        isinstance(active_workspace, dict)
        and bool((active_workspace.get("validation") or {}).get("enabled"))
    )
    if batch_cols[1].button(
        "Anadir puntos validos a la grafica",
        disabled=not selected_postprocess_alphas or not validation_enabled,
        help="Solo publica resultados reales postprocesados que cumplen las condiciones y el estado de elegibilidad del caso de validacion.",
    ):
        start_job(
            "validation_publish",
            validation_publish_command(
                ROOT,
                variant=variant,
                alphas=[float(value) for value in selected_postprocess_alphas],
                action="add",
            ),
        )
    if batch_cols[2].button(
        "Eliminar puntos de la gráfica",
        disabled=not selected_postprocess_alphas or not validation_enabled,
        help="Retira solo la publicación de los ángulos seleccionados; conserva la simulación y todo su postproceso.",
    ):
        start_job(
            "validation_unpublish",
            validation_publish_command(
                ROOT,
                variant=variant,
                alphas=[float(value) for value in selected_postprocess_alphas],
                action="remove",
            ),
        )
    if not validation_enabled:
        st.caption("Selecciona un caso de trabajo de validacion para habilitar la publicacion manual de puntos.")
    results = result_directory(ROOT, variant, alpha)
    show_json_report(results / "case_summary.json", "Resumen de resultados")
    result_summary = read_json(results / "case_summary.json", {}) or {}
    temporal = result_summary.get("temporal_animation") or {}
    if temporal:
        if temporal.get("status") == "READY":
            st.success(
                f"Animacion temporal disponible: {int(temporal.get('positive_time_count', 0))} tiempos positivos reconstruidos."
            )
        else:
            st.warning(str(temporal.get("message", "No hay suficientes tiempos escritos para animar en ParaView.")))
    show_json_report(
        ROOT / "CFD_2D" / "results" / variant / "batch_postprocess_status.json",
        "Estado del postproceso por lotes",
    )
    final_field_images = sorted(
        list((results / "RANS" / "ParaView").glob("*_final.png"))
        + list((results / "URANS" / "ParaView").glob("*_final.png"))
    )
    analysis_images = sorted(
        [path for path in results.glob("*.png") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:10]
    show_images(analysis_images + final_field_images, 3)

    animation_files = sorted(
        list((results / "RANS" / "ParaView").glob("*.mp4"))
        + list((results / "RANS" / "ParaView").glob("*.gif"))
        + list((results / "URANS" / "ParaView").glob("*.mp4"))
        + list((results / "URANS" / "ParaView").glob("*.gif"))
    )
    if animation_files:
        animation_choice = st.selectbox(
            "Animacion disponible",
            options=animation_files,
            format_func=lambda path: str(path.relative_to(results)),
            key=revisioned_widget_key("post-animation-choice"),
        )
        animation_state_key = f"show-post-animation:{variant}:{alpha}"
        animation_controls = st.columns(2)
        if animation_controls[0].button(
            "Visualizar animacion",
            key=revisioned_widget_key("post-show-animation"),
        ):
            st.session_state[animation_state_key] = str(animation_choice)
        if animation_controls[1].button(
            "Detener visualizacion",
            key=revisioned_widget_key("post-stop-animation"),
        ):
            st.session_state.pop(animation_state_key, None)
        selected_animation = Path(st.session_state.get(animation_state_key, ""))
        if selected_animation.is_file():
            if selected_animation.suffix.lower() == ".gif":
                st.image(str(selected_animation), caption=selected_animation.name, width="stretch")
            else:
                st.video(str(selected_animation))
    active_validation = (
        ROOT / "Results" / str(active_workspace.get("case")) / "Validation"
        if isinstance(active_workspace, dict)
        and active_workspace.get("case")
        and isinstance(active_workspace.get("validation"), dict)
        and active_workspace.get("validation", {}).get("enabled")
        else None
    )
    if variant in {"reference_uncut", "reference_uncut_validation_1m"} and active_validation is not None:
        validation_results = active_validation
        st.subheader("Validacion LS(1)-0417")
        st.caption(
            "Las curvas Experimental/Cobalt/Kestrel son datos aproximados digitalizados de la figura publicada. "
            "Los puntos RamAir solo se agregan a este caso de trabajo cuando el resultado real coincide con "
            "M=0.15 y Re=1.9e6. Otros casos reference_uncut no contaminan estas graficas."
        )
        show_images(
            [
                validation_results / "LS1_0417_CL_alpha_validation.png",
                validation_results / "LS1_0417_CD_CL_validation.png",
            ],
            2,
        )
        show_json_report(validation_results / "validation_summary.json", "Estado de validacion")

if active_page == "Validation & Convergence Lab":
    render_validation_convergence_lab(ROOT, start_job)

if active_page == "Archivos y logs":
    st.info(TAB_INTROS["Archivos y logs"])
    st.subheader("Historial de trabajos")
    history = [job.__dict__ for job in MANAGER.list_jobs(40)]
    if history:
        render_records_table(history, max_rows=40)
    st.subheader("Archivos recientes")
    recent = latest_files(
        ROOT,
        ["Application Support/Logs/**/*.log", "CFD_2D/meshes/**/*.json", "CFD_2D/results/**/*", "Results/**/case_manifest.json"],
        40,
    )
    rows = [{
        "archivo": str(path.relative_to(ROOT)),
        "tamano_MB": round(path.stat().st_size / 1048576.0, 3),
        "modificado": path.stat().st_mtime,
    } for path in recent]
    if rows:
        render_records_table(rows, max_rows=40)

library_stage_by_page = {
    "Geometria": "geometry",
    "Caso CFD": "case",
    "Caso OpenFOAM": "simulation",
    "Ejecucion": "simulation",
    "Postproceso": "postprocess",
}
if active_page in library_stage_by_page:
    case_library_panel(
        library_stage_by_page[active_page],
        variant,
        alpha,
        library_case_selection,
    )

st.divider()
job_console()
solver_live_monitor_panel()
