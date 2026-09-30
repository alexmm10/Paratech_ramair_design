"""Design-oriented CAD controls; backend geometry remains the source of previews."""
from __future__ import annotations
import copy
import pandas as pd
import streamlit as st
from parameter_controls import editor, group, optional, reference
from ramair_crossports import DEFAULT_HOLES


def canopy_controls(cfg, system):
    c = cfg["canopy_geometry"]
    group(c, ["chord_mm", "span_mm", "cells"], "Dimensiones principales", "cad/canopy")
    group(c, ["anhedral_deg"], "Ángulo de anhedral", "cad/canopy/angle")
    system["constraints"].update(editor({"R_over_b": system["constraints"]["R_over_b"]}, "cad/canopy/radius"))
    system["suspension"].update(editor({"derive_anhedral_from_R_over_b": system["suspension"].get("derive_anhedral_from_R_over_b", True)}, "cad/canopy/derive"))
    st.caption("El ángulo manual solo se utiliza cuando «Derivar anhedral de R/b» está desactivado. La derivación requiere suspensión activa en modo automático.")
    optional(c, "enable_variable_chord", ["chord_mode", "tip_chord_factor", "chord_anchor_fraction"], "Cuerda variable", "cad/chord")
    c["chord_anchor"] = "custom"
    optional(c, "enable_variable_cell_span", ["cell_span_mode", "tip_cell_width_factor"], "Ancho de celdas", "cad/cells")
    if ((c.get("enable_variable_chord") and c.get("chord_mode") == "quasi_elliptic") or
        (c.get("enable_variable_cell_span") and c.get("cell_span_mode") == "quasi_elliptic")):
        group(c, ["quasi_elliptic_exponent"], "Forma cuasi elíptica", "cad/quasi")
    optional(c, "enable_span_shrinkage", ["span_shrinkage_fraction"], "Contracción de envergadura", "cad/shrink")
    r = cfg["rib_and_cell_geometry"]
    optional(r, "enable_rib_inc_translation", [k for k in r if k.startswith(("loaded_", "nonloaded_", "tip_"))], "Incidencia y posición de costillas", "cad/ribs")
    optional(r, "enable_cell_ballooning", ["max_thickness_increase_fraction", "midcell_aft_shift_mm"], "Abombamiento entre costillas", "cad/balloon")
    with st.expander("Arco y orientación de anhedral · avanzado"):
        group(c, ["anhedral_arc_mode", "anhedral_rotation_pivot_mode", "anhedral_section_orientation"], "Anhedral", "cad/anhedral")
    reference({"Canopy": c, "Costillas": r})


def crossport_controls(cp):
    st.subheader("Orificios entre celdas")
    cp["enable_crossports"] = st.toggle("Activar crossports", cp.get("enable_crossports", True), key="cad/cp/enabled",
        help="Cada fila de la tabla crea un orificio en las costillas seleccionadas.")
    # Migrate legacy generators once into explicit rows. New UI never writes a
    # count/range/generator, and an empty table really means zero holes.
    if cp.get("position_mode") != "custom_table":
        cp["custom_specs"] = copy.deepcopy(DEFAULT_HOLES)
    cp["position_mode"] = "custom_table"
    if cp["enable_crossports"]:
        cp.update(editor({"apply_to": cp.get("apply_to", "all_internal")}, "cad/cp"))
        st.caption("Una fila por orificio. Dimensiones respecto a cuerda local: 0.025 = 2.5 %c. En un círculo, el ancho es el diámetro. El desplazamiento vertical se mide desde la línea elegida. No se redimensionan ni recolocan orificios automáticamente.")
        columns = list(DEFAULT_HOLES[0])
        frame = pd.DataFrame(cp["custom_specs"], columns=columns)
        cp["custom_specs"] = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", key="cad/cp/holes",
            column_config={
                "hole_id": st.column_config.TextColumn("ID", help="Identificador único del orificio.", required=True),
                "shape": st.column_config.SelectboxColumn("Forma", options=["circle", "ellipse"], required=True),
                "x": st.column_config.NumberColumn("Centro x/c", help="Posición longitudinal del centro.", required=True),
                "width_chord_frac": st.column_config.NumberColumn("Ancho / c", min_value=.00001, required=True, help="Diámetro de círculo o eje local longitudinal de elipse."),
                "height_chord_frac": st.column_config.NumberColumn("Alto / c", min_value=.00001, help="Segundo eje de elipse; un círculo usa el ancho en ambos ejes."),
                "reference_line": st.column_config.SelectboxColumn("Referencia vertical", options=["camber", "chord", "lower", "upper"], required=True, help="Curva media, cuerda, intradós o extradós."),
                "z_offset_chord": st.column_config.NumberColumn("Desplazamiento z/c", help="Positivo hacia el extradós desde la referencia."),
                "angle_deg": st.column_config.NumberColumn("Giro [°]", help="Rotación de la elipse en el plano de costilla."),
            }).to_dict("records")
        with st.expander("Construcción de los orificios en CATIA · avanzado"):
            internal = ["cut_mode", "split_orientation", "cut_strategy", "points_per_loop", "cutter_extrude_mode"]
            cp.update(editor({k: cp[k] for k in internal if k in cp}, "cad/cp/internal"))
            keys = ["cutter_extrude_mm"] if cp.get("cutter_extrude_mode") == "fixed" else ["cutter_extrude_factor_semicell", "cutter_extrude_min_mm", "cutter_extrude_max_mm"]
            cp.update(editor({k: cp[k] for k in keys if k in cp}, "cad/cp/cutter"))
    reference({"Crossports": {k: v for k, v in cp.items() if k not in {"count", "shape", "ellipse_orientation", "x_positions_chord", "x_start_chord", "x_end_chord", "width_fraction_chord", "height_fraction_local_thickness", "edge_clearance_fraction_local_thickness", "centerline_mode"}}})


def suspension_controls(system):
    s = system.setdefault("suspension", {})
    st.subheader("Suspensión y líneas")
    s["enabled"] = st.toggle("Activar suspensión y líneas", s.get("enabled", True), help="Genera los anclajes de todas las costillas loaded y las conexiones del sistema.")
    if not s["enabled"]: return
    s.setdefault("positioning_mode", "automatic")
    s.update(editor({"positioning_mode": s["positioning_mode"]}, "cad/suspension"))
    st.subheader("Bandas de anclaje sobre el canopy")
    st.caption("Cada fila define una banda A/B/C/D en x/c. Los anclajes se repiten en todas las costillas loaded; las bandas estructurales van a risers delanteros o traseros. BRK se activa desde Frenos.")
    banks = system["banks"]
    # BRK activation has one owner: the Brakes module below. Keep its position
    # editable without exposing a second activation switch in the table.
    if isinstance(banks, dict):
        for name, bank in banks.items():
            st.markdown("**"+name+"**")
            banks[name] = editor(bank, "cad/banks/"+name, {"active", "enabled"} if name == "BRK" else None)
    else:
        structural = [b for b in banks if b.get("name") != "BRK"]
        brake_rows = [b for b in banks if b.get("name") == "BRK"]
        system["banks"] = editor(structural, "cad/banks")
        for bank in brake_rows:
            st.markdown("**Posición de la banda BRK**")
            system["banks"].append(editor(bank, "cad/banks/BRK", {"active", "enabled", "name", "type"}))
    st.subheader("Anclajes estructurales y celdas frenadas")
    system.setdefault("loaded_rib_selection", {"mode": "all_loaded", "rib_ids": [], "enforce_symmetric_pairs": True})
    with st.expander("Selección de costillas estructurales"):
        system["loaded_rib_selection"] = editor(system["loaded_rib_selection"], "cad/structural-selection")
    from ramair_suspension_layout import migrate
    system.update(migrate(system))
    selection = system["brake_selection"]
    modes = {"outer_fraction": "Porcentaje exterior de cada b/2", "cells": "Celdas concretas", "legacy": "Conservar selección estructural original"}
    selection["mode"] = st.selectbox("Zona de frenos", list(modes), index=list(modes).index(selection["mode"]), format_func=modes.get)
    selection["symmetric"] = st.toggle("Frenos simétricos", selection.get("symmetric", True))
    for side in (["L"] if selection["symmetric"] else ["L", "R"]):
        if selection["mode"] == "outer_fraction":
            selection["fraction_"+side] = st.number_input("Cobertura exterior [% de b/2] · "+side, 0., 100., float(selection.get("fraction_"+side, .33))*100, key="brake/fraction/"+side)/100
        elif selection["mode"] == "cells":
            value = st.text_input("Celdas · "+side, ", ".join(map(str, selection.get("cells_"+side, []))), key="brake/cells/"+side)
            try:
                selection["cells_"+side] = [int(v.strip()) for v in value.split(",") if v.strip()]
            except ValueError:
                st.error("Introduce identificadores enteros separados por comas.")
    st.caption("Celdas numeradas de izquierda a derecha, empezando en 1. Se incluyen celdas completas; la vista previa muestra la cobertura real y las fijaciones. En simetría, edita el lado L y se reflejan sus celdas.")
    system["anchors"] = editor(system["anchors"], "cad/anchors", {"description", "tip_anchor_mode", "name_format", "surface"})
    system["anchors"]["tip_anchor_mode"] = "auto"
    if s["positioning_mode"] == "automatic":
        system["constraints"].update(editor(system["constraints"], "cad/constraints", {"description", "R_over_b"}))
        st.subheader("Cascadas de líneas")
        st.caption("Una fila por nivel: group_size reúne anclajes vecinos desde el centro hacia la punta de cada lado. fraction_to_target sitúa la confluencia entre el centro de sus hijos (0) y el riser (1). Niveles sucesivos agrupan las confluencias anteriores. bank_applicability admite bandas separadas por comas.")
        layout = system["cascade_layout"]
        labels = {"chordwise": "A–B / C–D + una confluencia BRK por lado", "none": "Directas, sin cascadas", "spanwise": "Vecinos en envergadura", "combined": "Longitudinal y después transversal", "legacy": "Niveles originales / compatibles"}
        layout["template"] = st.selectbox("Plantilla de agrupación", list(labels), index=list(labels).index(layout["template"]), format_func=labels.get)
        if layout["template"] == "legacy":
            system["cascades"] = editor(system["cascades"], "cad/cascades")
        elif layout["template"] != "none":
            layout["fraction"] = st.slider("Posición de confluencia hacia destino", .05, .95, float(layout["fraction"]))
            if layout["template"] in {"spanwise", "combined"}:
                layout["span_group_size"] = st.number_input("Entradas por grupo transversal", 2, 20, int(layout["span_group_size"]))
                layout["span_fraction"] = st.slider("Posición del nivel transversal hacia destino", .05, .95, float(layout["span_fraction"]))
        st.caption("Destinos: la banda define el riser delantero/trasero. A–B y C–D deben compartir destino. Los anclajes aislados continúan directamente; R/b conserva su referencia geométrica. Para conexiones explícitas usa el modo manual.")
        st.subheader("Orientación del conjunto")
        system["angles"] = editor(system["angles"], "cad/angles")
    else:
        st.info("Genera primero una vista previa para obtener los ID de anclaje. En modo manual, las conexiones usan estos ID o los nodos personalizados. Las posiciones están en milímetros del sistema global de CATIA; no se resuelve R/b.")
        manual = system.setdefault("manual_network", {"nodes": [], "segments": []})
        node_columns = ["node_id", "node_type", "x", "y", "z", "bank", "side"]
        edge_columns = ["start", "end", "bank", "side", "line_type"]
        manual["nodes"] = st.data_editor(pd.DataFrame(manual["nodes"], columns=node_columns), key="cad/manual/nodes", num_rows="dynamic", hide_index=True, width="stretch").to_dict("records")
        manual["segments"] = st.data_editor(pd.DataFrame(manual["segments"], columns=edge_columns), key="cad/manual/edges", num_rows="dynamic", hide_index=True, width="stretch").to_dict("records")
    for key, title in [("risers", "Risers · conexiones inferiores"), ("slider", "Slider · plano de guiado"), ("payload", "Carga útil"), ("agu", "AGU · unidad de guiado"), ("brakes", "Frenos independientes")]:
        st.subheader(title)
        values = system[key]
        if key == "risers":
            st.caption("Puntos de conexión estructural. Si la suspensión está activa siempre necesita risers o destinos equivalentes en la tabla manual.")
        else:
            values["active"] = st.toggle("Activar "+title.split(" · ")[0], values.get("active", False), key="cad/module/"+key,
                help=values.get("description", "Incluye este componente en el modelo."))
            if not values["active"]: continue
        excluded = {"active", "description"}
        if s["positioning_mode"] == "manual":
            excluded.update(k for k in values if any(t in k for t in ("offset", "fraction", "if_no_slider", "initial_slider_distance", "drop_below")))
        system[key].update(editor(values, "cad/system/"+key, excluded))
    st.subheader("Propiedades geométricas de las líneas")
    if isinstance(system["banks"], dict) and "BRK" in system["banks"]:
        for flag in ("active", "enabled"):
            if flag in system["banks"]["BRK"]:
                system["banks"]["BRK"][flag] = bool(system["brakes"].get("active", False))
    elif isinstance(system["banks"], list):
        for bank in system["banks"]:
            if bank.get("name") == "BRK": bank["enabled"] = bool(system["brakes"].get("active", False))
    props = system.get("line_properties", {})
    props.update(editor({"line_diameter_mm": props.get("line_diameter_mm", 1.2), "Cd0_line": props.get("Cd0_line", 1.2)}, "cad/lineprops"))
    props["segmented_drag_active"] = False
    system["line_properties"] = props
    with st.expander("Referencia geométrica y tolerancias · avanzado"):
        system["canopy_reference"] = editor(system["canopy_reference"], "cad/reference")
    reference(system, "Referencia de variables de suspensión")


def stabilizer_controls(system):
    s = system["stabilizers"]
    st.subheader("Estabilizadores laterales")
    s["active"] = st.toggle("Activar estabilizadores", s.get("active", False), key="cad/module/stabilizers", help="Añade un panel en cada costilla loaded exterior.")
    if s["active"]:
        s["attach_tip_lines_to_stabilizer"] = st.toggle("Anclar líneas de punta al estabilizador", s.get("attach_tip_lines_to_stabilizer", True), help="Distribuye las bandas estructurales por el borde inferior real del panel.")
        shapes = ["triangular", "trapezoidal", "rectangular", "partial_te", "custom"]
        s["shape"] = st.selectbox("Forma del estabilizador", shapes, index=shapes.index(s.get("shape", "triangular")), help="Custom define el borde inferior mediante una tabla; los extremos superiores se unen a la costilla.")
        keys = ["chord_start_x_c", "chord_end_x_c", "height_direction", "surface_reference"]
        if s["shape"] == "custom":
            s.setdefault("lower_edge_points", [{"x_c": .15, "down_mm": 300.}, {"x_c": .6, "down_mm": 450.}, {"x_c": .95, "down_mm": 250.}])
            s["lower_edge_points"] = st.data_editor(pd.DataFrame(s["lower_edge_points"]), num_rows="dynamic", hide_index=True, width="stretch", key="cad/stab/custom").to_dict("records")
        else:
            keys += ["height_mm", "sweep_mm"]
            if s["shape"] in {"triangular", "partial_te"}: keys.append("apex_x_c")
            if s["shape"] == "trapezoidal": keys.append("lower_edge_scale")
        s.update(editor({k: s[k] for k in keys}, "cad/stabilizer"))
        s["attach_to_loaded_ribs"] = True
        s["triangular_anchor_edge_mode"] = "lower_perimeter_by_x"
    with st.expander("Abombamiento lateral de punta · avanzado"):
        b = system["tip_side_bulge"]
        b["active"] = st.toggle("Activar abombamiento lateral de punta", b.get("active", False), key="cad/module/bulge", help="Superficie lateral curva auxiliar; desactivada de forma predeterminada.")
        if b["active"]: b.update(editor(b, "cad/tip_bulge", {"active", "description"}))
    reference({"Estabilizador": s, "Punta": system["tip_side_bulge"]})
