"""Consistent typed controls and a searchable reference for current parameters."""
from __future__ import annotations
import copy
import json
import pandas as pd
import streamlit as st

# label, purpose. Units are also inferred from explicit configuration suffixes.
FIELDS = {
    "reynolds": ("Reynolds", "Relación entre inercia y viscosidad basada en la cuerda."),
    "mach": ("Mach", "Velocidad dividida por la velocidad del sonido."),
    "ncrit": ("Ncrit · transición natural", "Amplificación crítica eⁿ. 9: túnel medio; 4–8: perturbación mayor; 1: aproximación a transición temprana. No equivale directamente a intensidad turbulenta."),
    "xtrip_upper": ("Trip en extradós [x/c]", "Posición límite de transición forzada. 1 permite transición natural; 0.01 fuerza transición cerca del borde de ataque."),
    "xtrip_lower": ("Trip en intradós [x/c]", "Posición límite de transición forzada. La transición natural puede ocurrir antes del trip."),
    "panel_count": ("Paneles del contorno", "Número de paneles XFOIL. Comprueba sensibilidad del corte y de la polar al refinar."),
    "panel_bunching": ("Concentración de paneles", "Control P de PPAR: modifica la concentración de puntos según la curvatura."),
    "te_le_density_ratio": ("Densidad TE/LE", "Relación de densidad de paneles entre borde de salida y borde de ataque (PPAR T)."),
    "refined_area_le_density_ratio": ("Densidad de zona refinada/LE", "PPAR R: densidad relativa en las zonas locales de refinamiento de XFOIL."),
    "alpha_start_deg": ("Ángulo inicial", "Primer ángulo solicitado de la polar."),
    "alpha_end_deg": ("Ángulo final", "Último ángulo solicitado de la polar."),
    "alpha_step_deg": ("Incremento angular", "Separación entre ángulos solicitados. Solo se usan soluciones convergidas."),
    "cl_min": ("CL mínimo del corte", "Límite inferior del intervalo usado para elegir puntos de estancamiento del corte optimizado."),
    "cl_max": ("CL máximo del corte", "Límite superior del intervalo usado para elegir puntos de estancamiento del corte optimizado."),
    "xfoil_iteration_limit": ("Iteraciones por ángulo", "Máximo de iteraciones viscosas antes de considerar que un ángulo no converge."),
    "xfoil_timeout_s": ("Tiempo máximo por proceso XFOIL", "Detiene un proceso bloqueado; no convierte un ángulo sin convergencia en válido."),
    "stagnation_search_x_over_c": ("Zona de búsqueda de estancamiento [x/c]", "Límite delantero de búsqueda del máximo de Cp; evita seleccionar puntos del borde de salida."),
    "minimum_stagnation_cp": ("Cp mínimo admisible", "Umbral de presión para aceptar un punto como candidato de estancamiento."),
    "cut_margin_panel_points": ("Margen del corte en paneles", "Añade puntos vecinos a la envolvente de estancamiento seleccionada."),
    "chord_mm": ("Cuerda central", "Longitud de referencia de la sección central del canopy."),
    "span_mm": ("Envergadura plana", "Anchura total del canopy antes de contracción y anhedral."),
    "cells": ("Número de celdas", "Celdas completas. Las costillas loaded delimitan las celdas y reciben líneas."),
    "enable_variable_chord": ("Activar cuerda variable", "Permite disminuir la cuerda hacia las puntas con la distribución elegida."),
    "chord_mode": ("Distribución de cuerda", "Rectangular: constante; elliptic: elíptica; quasi_elliptic: elíptica modificada por el exponente."),
    "chord_anchor_fraction": ("Fracción de anclaje de cuerda [x/c]", "Estación que permanece alineada: 0 borde de ataque, 0.5 media cuerda, 1 borde de salida."),
    "tip_chord_factor": ("Cuerda de punta / cuerda central", "Escala final de cuerda hacia ambas puntas."),
    "quasi_elliptic_exponent": ("Exponente cuasi elíptico", "Modifica la distribución quasi_elliptic; 1 recupera la forma elíptica."),
    "enable_variable_cell_span": ("Activar ancho variable de celdas", "Distribuye el ancho entre celdas conservando la envergadura total."),
    "cell_span_mode": ("Distribución de anchos", "Uniforme, elíptica o cuasi elíptica. El ancho de cada celda se normaliza a la envergadura."),
    "tip_cell_width_factor": ("Ancho de celda de punta / central", "Proporción usada para reducir las celdas exteriores."),
    "enable_span_shrinkage": ("Activar contracción de envergadura", "Reduce la envergadura plana con la fracción especificada."),
    "span_shrinkage_fraction": ("Fracción de contracción", "0.15 representa un acortamiento de envergadura del 15 %."),
    "anhedral_deg": ("Anhedral", "Ángulo de arco según la convención elegida. Puede derivarse de R/b en Suspensión."),
    "anhedral_arc_mode": ("Convención de arco", "center_to_tip_line mide la recta centro-punta; tip_tangent mide la tangente en la punta."),
    "anhedral_rotation_pivot_mode": ("Pivote de giro", "Punto de referencia vertical para orientar las costillas sobre el arco."),
    "anhedral_section_orientation": ("Orientación de las costillas", "focus_inward orienta las secciones hacia el foco del arco; conserva la convención actual de CATIA."),
    "enable_rib_inc_translation": ("Activar ajustes de costillas", "Aplica incidencias y desplazamientos diferenciados a costillas loaded y nonloaded."),
    "enable_cell_ballooning": ("Activar abombamiento de celdas", "Genera secciones intermedias infladas para los lofts entre costillas."),
    "max_thickness_increase_fraction": ("Incremento máximo de espesor", "0.30 aumenta el espesor un 30 % en el centro de cada celda."),
    "shape": ("Forma", "Geometría elegida para esta pieza; solo se muestran sus parámetros aplicables."),
    "height_mm": ("Altura", "Distancia desde el borde de unión en la dirección de altura seleccionada."),
    "attach_tip_lines_to_stabilizer": ("Anclar líneas de punta al estabilizador", "Mueve los anclajes exteriores de las bandas estructurales al borde inferior del panel."),
    "chord_start_x_c": ("Inicio del panel [x/c]", "Extremo delantero de unión a la costilla exterior."),
    "chord_end_x_c": ("Final del panel [x/c]", "Extremo posterior de unión a la costilla exterior."),
    "apex_x_c": ("Posición del vértice [x/c]", "Estación del vértice inferior de un panel triangular."),
    "sweep_mm": ("Desplazamiento longitudinal inferior", "Desplaza el borde inferior a lo largo de la dirección de cuerda."),
    "lower_edge_scale": ("Escala del borde inferior", "Longitud del borde inferior respecto al superior para el trapecio."),
    "height_direction": ("Dirección de altura", "rib_down sigue el plano real de costilla; global_down es vertical global; span_outward apunta hacia fuera."),
    "R_over_b": ("Relación R/b", "Radio de referencia de suspensión dividido por envergadura. Las longitudes reales se calculan sumando segmentos y se auditan por banda."),
    "derive_anhedral_from_R_over_b": ("Derivar anhedral de R/b", "Usa beta = 1/(4 R/b) en radianes para la geometría de partida."),
    "positioning_mode": ("Posicionamiento del sistema", "Automático: calcula posiciones a partir de R/b. Manual: usa nodos y conexiones definidos en tablas."),
    "alpha_op_deg": ("Ángulo de ataque de referencia", "Ángulo aerodinámico de la cuerda respecto al flujo; usado en la relación angular del esquema CAD."),
    "gamma_deg": ("Ángulo de trayectoria", "Inclinación de la trayectoria según la convención alpha + mu = gamma + theta."),
    "mu_deg": ("Desfase de rigging", "Desfase angular entre referencias de suspensión y canopy dentro de la relación de cierre."),
    "theta_deg": ("Inclinación de la suspensión", "Giro respecto a la vertical descendente en el plano X–Z; 0 muestra el sistema vertical. Vacío usa alpha + mu − gamma."),
    "steady_residual_control": ("Residuales SIMPLE", "Umbrales por campo para terminar iteraciones RANS según OpenFOAM."),
    "outer_corrector_residual_control": ("Residuales PIMPLE", "Criterios de convergencia entre correctores externos de cada paso temporal; no terminan la simulación física."),
    "steady_max_iterations": ("Iteraciones máximas RANS", "Límite absoluto de iteración del arranque o continuación estacionaria."),
    "n_outer_correctors": ("Correctores externos máximos", "Máximo de iteraciones PIMPLE por paso; los residuales pueden terminar el bucle antes."),
    "n_correctors": ("Correctores de presión", "Correcciones presión-velocidad dentro de cada iteración externa."),
    "maxCo": ("Courant máximo", "Limita el paso por advección local. Los labios abiertos requieren especial atención."),
    "deltaT_star": ("Paso inicial Δt*", "Paso adimensional: Δt* = Δt U/c. El control de Courant puede reducirlo."),
    "maxDeltaT_star": ("Paso máximo Δt*", "Límite superior de paso físico adimensional."),
    "transient_relaxation": ("Relajación URANS", "Relajación dentro del bucle PIMPLE; las entradas Final permiten resolver sin relajación la última iteración."),
    "steady_numerics": ("Esquemas y relajación RANS", "Discretización y relajación de las iteraciones estacionarias."),
    "validation_phase_d_steady_equivalence": ("Equivalencia estacionaria en fase D", "Criterio opcional de evaluación; no sustituye los residuos del solver. Los floors evitan dividir por coeficientes cercanos a cero."),
    "purgeWrite": ("Estados de campo conservados", "0 conserva todos; valores positivos limitan carpetas temporales. Preserva historial suficiente para reiniciar backward."),
    "solver": ("Lanzador de OpenFOAM", "OpenFOAM Foundation 14 usa foamRun con el módulo incompressibleFluid. SIMPLE/PIMPLE son algoritmos controlados por los diccionarios."),
    "solver_module": ("Módulo físico", "incompressibleFluid resuelve flujo incompresible en foamRun; no es una instalación separada."),
}

OPTIONS = {
    "chord_mode": ["rectangular", "elliptic", "quasi_elliptic"],
    "cell_span_mode": ["uniform", "elliptic", "quasi_elliptic"],
    "anhedral_arc_mode": ["center_to_tip_line", "tip_tangent"],
    "anhedral_rotation_pivot_mode": ["profile_min_z", "profile_zero", "te_center"],
    "anhedral_section_orientation": ["focus_inward", "legacy_outward"],
    "height_direction": ["rib_down", "global_down", "span_outward"],
    "surface_reference": ["lower", "upper", "midline"],
    "positioning_mode": ["automatic", "manual"],
    "p_ref_z_mode": ["chord_line", "lower_surface"],
    "ddt_scheme": ["Euler", "backward"], "scheme": ["Euler", "backward"],
    "apply_to": ["all_internal", "loaded_internal", "nonloaded_internal"],
    "cut_mode": ["post_split_surfaces", "curves_only"],
    "cut_strategy": ["curve_split_first", "extruded_wall_split", "curve_split"],
    "cutter_extrude_mode": ["auto_semicell_span", "fixed"],
    "brake_target": ["agu_if_active", "slider"],
    "R_definition": ["straight_anchor_to_central_confluence", "mean_path_length"],
}


def info(name: str, value=None) -> tuple[str, str, str]:
    units = next((u for suffix, u in (("_mm", "mm"), ("_deg", "°"), ("_pa", "Pa"),
                 ("_s", "s"), ("_m", "m"), ("_K", "K")) if name.endswith(suffix)), "—")
    label, help_text = FIELDS.get(name, (name.replace("_", " ").capitalize(), ""))
    if not help_text:
        help_text = ("Activa o desactiva " if isinstance(value, bool) else "Ajusta ") + label.lower() + "."
    if units != "—" and "[" not in label:
        label += f" [{units}]"
    return label, help_text, units


def reference(data: dict, title="Referencia de variables") -> None:
    rows = []
    def walk(obj, group=""):
        for key, value in obj.items():
            if key.startswith("_") or key in {"description", "descriptions"}: continue
            if isinstance(value, dict):
                walk(value, f"{group}/{key}".strip("/"))
            else:
                label, desc, units = info(key, value)
                rows.append({"Grupo": group, "Variable": label, "Función": desc, "Unidades": units,
                             "Opciones": ", ".join(OPTIONS.get(key, [])), "Valor": str(value), "Clave": key})
    walk(data)
    with st.expander(title):
        st.dataframe(rows, hide_index=True, width="stretch")


def table(values: list, key: str):
    if not values:
        return []
    if all(isinstance(v, dict) for v in values):
        frame = pd.DataFrame(values)
        # Streamlit cannot edit object columns; comma lists remain typed on return.
        list_columns = [c for c in frame if any(isinstance(v, list) for v in frame[c])]
        for c in list_columns:
            frame[c] = frame[c].map(lambda v: ", ".join(map(str, v)) if isinstance(v, list) else v)
        edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", key=key)
        rows = json.loads(edited.to_json(orient="records"))
        for row in rows:
            for c in list_columns:
                row[c] = [s.strip() for s in str(row.get(c) or "").split(",") if s.strip()]
        return rows
    frame = st.data_editor(pd.DataFrame({"Valor": values}), num_rows="dynamic", hide_index=True, key=key, width="stretch")
    return [v for v in frame.Valor.tolist() if pd.notna(v)]


def editor(data, prefix: str, exclude: set | None = None):
    if not isinstance(data, dict): return table(data, prefix) if isinstance(data, list) else data
    out = copy.deepcopy(data)
    if data.get("description"): st.caption(data["description"])
    for name, value in data.items():
        if name in (exclude or set()) or name.startswith("_") or name in {"description", "descriptions"}: continue
        key = f"{prefix}/{name}"
        label, help_text, _ = info(name, value)
        if isinstance(value, dict):
            st.markdown(f"#### {label}")
            out[name] = editor(value, key)
        elif isinstance(value, list):
            st.markdown(f"**{label}**")
            st.caption(help_text)
            out[name] = table(value, key)
        elif isinstance(value, bool):
            out[name] = st.toggle(label, value, key=key, help=help_text)
        elif name in OPTIONS:
            options = list(OPTIONS[name])
            if value not in options: options.append(value)
            out[name] = st.selectbox(label, options, index=options.index(value), key=key, help=help_text)
        elif isinstance(value, int):
            out[name] = st.number_input(label, value=value, step=1, key=key, help=help_text)
        elif isinstance(value, float):
            out[name] = st.number_input(label, value=value, format="%.7g", key=key, help=help_text)
        elif value is None:
            text = st.text_input(label+" (vacío = automático)", "", key=key, help=help_text)
            try: out[name] = json.loads(text) if text.strip() else None
            except ValueError: out[name] = text
        else:
            out[name] = st.text_input(label, str(value), key=key, help=help_text)
    return out


def group(data: dict, keys: list[str], title: str, prefix: str, description="") -> None:
    st.subheader(title)
    if description: st.caption(description)
    data.update(editor({k: data[k] for k in keys if k in data}, prefix))


def optional(data: dict, switch: str, keys: list[str], title: str, prefix: str) -> None:
    st.subheader(title)
    data.update(editor({switch: bool(data.get(switch, False))}, prefix))
    if data[switch]: data.update(editor({k: data[k] for k in keys if k in data}, prefix))
