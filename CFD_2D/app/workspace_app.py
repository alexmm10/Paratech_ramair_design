"""Modular desktop workflow. Profiles, CAD and CFD have independent ownership."""
from __future__ import annotations
import copy
import json
import shutil
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st
import workflow_backend as backend
from ramair_workspace import (initialize, workspaces, create_workspace, register_workspace, read_json, write_json,
    physical_conditions, create_cfd_case, list_entities, geometry_context, bind_mesh_config,
    save_mesh_preset, parse_angles, solver_defaults, create_solver_setup, all_solver_setups,
    comparable_setups, compatible_mesh, alpha_name, slug, export_products, new_manifest)
from ramair_profile_library import catalogue, process_profile, import_geometry
from ramair_workspace_jobs import queue_manifest
from ramair_polar_comparison import curate_points, load_polar, polar_metrics, compare_polars


PAGES = ["Inicio", "Casos de trabajo", "Perfiles 2D", "Geometría CATIA", "Caso CFD", "Mallado", "Caso OpenFOAM", "Ejecución", "Postproceso", "Validation & Convergence Lab", "Archivos y logs"]
LABELS = {"canopy_geometry": "Canopy", "rib_and_cell_geometry": "Costillas y celdas", "crossports": "Crossports",
    "deltaT_star": "Paso inicial Δt*", "maxDeltaT_star": "Paso máximo Δt*", "maxCo": "Courant máximo",
    "ddt_scheme": "Esquema temporal", "n_outer_correctors": "Correctores externos máximos",
    "n_correctors": "Correctores de presión", "steady_max_iterations": "Iteraciones máximas RANS",
    "steady_residual_control": "Residuales SIMPLE", "outer_corrector_residual_control": "Residuales PIMPLE",
    "validation_write_strategy": "Escritura de campos", "validation_rans_convergence": "Convergencia RANS",
    "transient_relaxation": "Relajación transitoria", "steady_numerics": "Esquemas y relajación RANS"}


from parameter_controls import editor, reference, group


def image_gallery(folder: Path, key: str = "images") -> None:
    images = sorted(folder.glob("**/*.png")) if folder.is_dir() else []
    if not images:
        st.caption("Las figuras aparecerán cuando existan resultados postprocesados.")
        return
    choice = st.multiselect("Figuras", images, default=images[:4], format_func=lambda p: str(p.relative_to(folder)), key=key)
    cols = st.columns(2)
    for i, path in enumerate(choice):
        cols[i % 2].image(str(path), caption=path.stem, width="stretch")


def profile_preview(profile: Path, crossports: dict | None = None) -> None:
    from ramair_profile_library import _branches
    try:
        if profile.name == "profile_points.csv":
            frame = pd.read_csv(profile)
            u = frame[frame.source_section == "UPPER"].sort_values("source_order")
            l = frame[frame.source_section == "LOWER"].sort_values("source_order")
            cap = frame[frame.source_section == "TE_ROUNDED_CAP"].sort_values("source_order")
            contour = pd.concat([u.iloc[::-1], cap.iloc[::-1], l.iloc[::-1]])
            xy = contour[["x_m", "z_m"]].to_numpy()
        else:
            u, l, _ = _branches(profile)
            xy = np.vstack([u, l[::-1]])
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 3.4))
        ax.plot(xy[:, 0], xy[:, 1], color="#087f8c", linewidth=1.7, label=profile.parent.name)
        if crossports and crossports.get("enable_crossports", False):
            from importlib.util import spec_from_file_location, module_from_spec
            module_name = "ramair_cad_preview"
            if module_name not in sys.modules:
                spec = spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "preprocess_ramair_main.py")
                module = module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            module = sys.modules[module_name]
            from dataclasses import replace
            cfg = module.build_config_from_user_settings()
            updates = {}
            for key, value in crossports.items():
                field = "enable_crossports" if key == "enable_crossports" else "crossport_ellipse_orientation" if key == "ellipse_orientation" else "crossport_" + key
                if hasattr(cfg, field):
                    updates[field] = value
            cfg = replace(cfg, **updates)
            up, lo, _ = _branches(profile)
            loops = module.make_crossport_table(cfg, pd.DataFrame(up, columns=["x", "y"]), pd.DataFrame(lo, columns=["x", "y"]))
            for loop_id, rows in loops.groupby("loop_id"):
                values = rows[["x_chord_norm", "z_chord_norm"]].to_numpy()
                values = np.vstack([values, values[0]])
                ax.plot(values[:, 0], values[:, 1], color="#e79f29", linewidth=1.5)
        ax.set_aspect("equal", adjustable="datalim")
        ax.set(xlabel="x/c", ylabel="z/c", xlim=(-.05, 1.05))
        ax.grid(alpha=.18)
        ax.spines[["top", "right"]].set_visible(False)
        st.pyplot(fig, width="stretch")
        plt.close(fig)
    except Exception as exc:
        st.warning(f"No se puede previsualizar el perfil: {exc}")


def command(root: Path, action: str, path: Path, *args) -> list[str]:
    return [sys.executable, str(root / "CFD_2D/scripts/ramair_workspace_jobs.py"), action, "--root", str(root), "--path", str(path), *map(str, args)]


def select_entity(paths: list[Path], label: str, marker: str, key: str) -> Path | None:
    if not paths:
        st.info(f"Todavía no hay elementos disponibles: {label.lower()}.")
        return None
    return st.selectbox(label, paths, format_func=lambda p: read_json(p / marker).get("name", p.name), key=key)


def render_workspaces(root: Path, start_job) -> None:
    st.markdown("Crea una carpeta para reunir modelos CAD y casos CFD independientes. Las bibliotecas de perfiles y presets están disponibles en todos los casos.")
    create, load = st.tabs(["Crear", "Abrir carpeta existente"])
    with create:
        name = st.text_input("Nombre del caso de trabajo", "Nuevo diseño")
        parent = st.text_input("Carpeta contenedora", str(root / "Workspaces"))
        if st.button("Crear y abrir", type="primary"):
            path = create_workspace(root, name, Path(parent))
            st.session_state["workspace-path"] = str(path)
            st.rerun()
    with load:
        discovered = workspaces(root)
        if discovered:
            selected_workspace = st.selectbox("Caso de trabajo existente", discovered,
                format_func=lambda item: f"{item['name']} · {item['path']}",
                help="Incluye las carpetas de Workspaces y las ubicaciones externas ya registradas.")
            if st.button("Abrir caso seleccionado", type="primary"):
                st.session_state["workspace-path"] = selected_workspace["path"]
                st.rerun()
        else:
            st.info("Todavía no hay casos de trabajo. Crea uno o importa su paquete.")
        with st.expander("Registrar una ubicación externa · avanzado"):
            location = st.text_input("Carpeta que contiene workspace.json", help="Registra una carpeta externa una sola vez; después aparecerá en el selector.")
            if st.button("Registrar y abrir carpeta", disabled=not location):
                register_workspace(root, Path(location))
                st.session_state["workspace-path"] = str(Path(location).resolve())
                st.rerun()
    items = workspaces(root)
    if items:
        st.dataframe([{k: i.get(k) for k in ("name", "created_at", "path")} for i in items], hide_index=True, width="stretch")
        with st.expander("Trasladar un caso de trabajo a otro dispositivo"):
            selected_export = st.selectbox("Caso a empaquetar", items, format_func=lambda i:i["name"])
            keep_results = st.checkbox("Incluir también campos e historiales de cálculo", value=False)
            st.caption("El paquete básico conserva geometrías, configuraciones, mallas y campos iniciales. Para continuar exactamente desde un checkpoint incluye los resultados. Detén el solver antes de copiar.")
            if st.button("Crear paquete del caso de trabajo"):
                package = root / "Application Support/Packages" / (slug(selected_export["name"])+"_"+str(time.time_ns())+".zip")
                args = [sys.executable, root / "Application Support/Tools/transfer_studio_workspace.py", "export", "--workspace", selected_export["path"], "--archive", package]
                if keep_results: args.append("--include-results")
                start_job("workspace_export", args)
    with st.expander("Instalar un caso de trabajo trasladado"):
        uploaded_workspace = st.file_uploader("Paquete de caso de trabajo", type=["zip"])
        imported_name = st.text_input("Nombre en este dispositivo", "Caso importado")
        if st.button("Verificar e instalar caso de trabajo", disabled=uploaded_workspace is None):
            package = root / "Application Support/Temp" / (str(time.time_ns())+"_workspace.zip")
            package.parent.mkdir(parents=True,exist_ok=True)
            package.write_bytes(uploaded_workspace.getvalue())
            start_job("workspace_import", [sys.executable, root / "Application Support/Tools/transfer_studio_workspace.py", "import", "--root", root, "--archive", package, "--name", imported_name])
    with st.expander("Casos y resultados anteriores conservados"):
        st.caption("Los paquetes históricos permanecen en sus rutas originales. El laboratorio sigue usándolos; no se duplican campos ni mallas.")
        st.dataframe(backend.saved_cases(root), hide_index=True, width="stretch")
        if st.button("Abrir biblioteca histórica"):
            backend.open_results_library(root)


def render_profiles(root: Path, start_job) -> None:
    library, prepare, cut = st.tabs(["Biblioteca", "Importar y preparar TE", "Corte con XFOIL"])
    items = catalogue(root)
    with library:
        if items:
            selection = st.selectbox("Perfil compartido", items, format_func=lambda i: f"{i['name']} · {i['topology'].upper()}")
            profile_preview(Path(selection["file"]))
            st.caption(f"Cuerda base: 1 m · TE: {selection['te']['mode']} · {selection['path']}")
            dat = next(Path(selection["path"]).glob("*.dat"), None)
            if dat:
                st.download_button("Descargar DAT en orden XFOIL", dat.read_bytes(), file_name=dat.name)
        else:
            st.info("Prepara un perfil para incorporarlo a la biblioteca compartida.")
    with prepare:
        topology = st.radio("Tipo del perfil importado", ["closed", "open"], horizontal=True, format_func=lambda v: "Cerrado" if v == "closed" else "Abierto")
        uploaded = st.file_uploader("Importar DAT o CSV", type=["dat", "csv"])
        raw = sorted(p for p in (root / "Airfoil Profiles").glob("*") if p.suffix.lower() in {".dat", ".csv"})
        source = st.selectbox("Archivo existente", [None, *raw], format_func=lambda p: "Seleccionar" if p is None else p.name)
        if uploaded:
            destination = root / "Airfoil Profiles/Imported" / f"{time.time_ns()}_{slug(Path(uploaded.name).stem)}{Path(uploaded.name).suffix}"
            if st.button("Guardar archivo importado"):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(uploaded.getvalue())
                st.session_state["imported-profile"] = str(destination)
        source = Path(st.session_state["imported-profile"]) if st.session_state.get("imported-profile") else source
        mode = st.radio("Tratamiento del borde de salida", ["rounded", "straight", "sharp"], horizontal=True,
                        format_func=lambda v: {"rounded": "Redondeado", "straight": "Cierre recto", "sharp": "En punta"}[v])
        name = st.text_input("Nombre del perfil preparado", source.stem if source else "perfil")
        bases = [i for i in items if i["topology"] == "closed"]
        base = st.selectbox("Perfil base del corte abierto (si corresponde)", [None, *bases], format_func=lambda i: "Sin corte" if i is None else i["name"])
        st.caption("El redondeo recorta la zona posterior, conserva el resto del contorno y une con tangentes continuas. El punto más retrasado queda en x/c = 1.")
        if st.button("Preparar y guardar perfil", disabled=source is None, type="primary"):
            target = process_profile(root, source, name, mode, Path(base["path"]) if base else None, topology=topology)
            st.success(f"Perfil guardado en {target}")
        if source:
            profile_preview(source)
    with cut:
        from ramair_2d_inlet_designer import DEFAULT_CONFIG
        bases = [i for i in items if i["topology"] == "closed"]
        if not bases:
            st.info("Prepara primero el perfil base cerrado.")
        else:
            selected = st.selectbox("Base para XFOIL", bases, format_func=lambda i: i["name"])
            config = copy.deepcopy(DEFAULT_CONFIG)
            dat = next(Path(selected["path"]).glob("*.dat"))
            config["base_profile"] = str(dat.relative_to(root))
            transition = st.selectbox("Hipótesis de transición", ["natural", "early_trip", "custom"],
                format_func=lambda v: {"natural": "Natural · Ncrit 9, sin trip anticipado", "early_trip": "Trip temprano · comparación con RANS turbulento", "custom": "Personalizada"}[v],
                help="El trip temprano es una hipótesis de comparación, no una medición de rugosidad del tejido.")
            if transition == "early_trip":
                config.update(ncrit=1., xtrip_upper=.01, xtrip_lower=.01)
            group(config, ["reynolds", "mach", "ncrit", "xtrip_upper", "xtrip_lower"], "Condiciones de operación", "xfoil/"+transition)
            st.caption("XFOIL se aplica al perfil cerrado que origina el corte. El trip al 1 %c aproxima un arranque turbulento; comprueba sensibilidad a Ncrit, trip y panelado antes de interpretar una polar como evidencia aerodinámica.")
            st.link_button("Criterio de transición y precisión · manual de XFOIL", "https://web.mit.edu/drela/Public/web/xfoil/xfoil_doc.txt")
            st.subheader("Corte y polar")
            config["design_mode"] = st.selectbox("Envolvente del corte", ["standard_full_polar", "optimized_cl_window"], format_func=lambda v: "Estándar · polar completa" if v == "standard_full_polar" else "Optimizado · intervalo de CL", help="Estándar toma toda la polar convergida; optimizado limita la envolvente al intervalo CL.")
            keys = ["alpha_start_deg", "alpha_end_deg", "alpha_step_deg", "cut_margin_panel_points"]
            if config["design_mode"] == "optimized_cl_window": keys += ["cl_min", "cl_max"]
            config.update(editor({k: config[k] for k in keys}, "xfoil/cut"))
            group(config, ["xfoil_iteration_limit", "xfoil_timeout_s", "stagnation_search_x_over_c", "minimum_stagnation_cp"], "Solución y búsqueda de estancamiento", "xfoil/solver")
            group(config, ["panel_count", "panel_bunching", "te_le_density_ratio", "refined_area_le_density_ratio"], "Discretización del perfil XFOIL", "xfoil/panels")
            st.subheader("Guardar el nuevo perfil")
            from ramair_2d_inlet_designer import generated_profile_name
            suggested = generated_profile_name(dat, config, 0.).removesuffix(".csv").replace("Gap0p0pct", "Gap{gap}pct")
            name = st.text_input("Nombre del nuevo corte", suggested, key="xfoil/name/"+suggested,
                help="{gap} se sustituye al finalizar por la longitud real de corte en %c con un decimal. Puedes editar el nombre o conservar este marcador.")
            reference(config, "Referencia de variables XFOIL")
            if st.button("Ejecutar corte y preparar TE", type="primary"):
                path = write_json(root / "Airfoil Profiles/.settings/xfoil_config.json", config)
                start_job("xfoil_profile", command(root, "xfoil", Path(selected["path"]), "--config", path, "--name", name))
            summaries = sorted((root / "Airfoil Profiles/Designs").rglob("inlet_design_summary.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            if summaries:
                st.subheader("Resultado del corte · perfil y puntos de remanso")
                figure = st.selectbox("Corte generado", summaries, format_func=lambda p: p.parent.name, key="xfoil/result-figure")
                st.image(str(figure), width="stretch")
                metadata = read_json(figure.parent / "inlet_design_metadata.json")
                st.caption(f"Base: {metadata.get('base_profile', '')} · Método: {metadata.get('design_mode', '')}")



def render_catia(root: Path, workspace: Path | None, start_job) -> None:
    st.caption("Cada generación conserva sus Inputs, configuración, informes y macro CATIA dentro de un modelo con nombre propio.")
    items = catalogue(root)
    if not items:
        st.info("Prepara un perfil en Perfiles 2D.")
        return
    profile = st.selectbox("Perfil del modelo", items, format_func=lambda i: i["name"])
    name = st.text_input("Nombre de esta geometría CAD", "modelo_01")
    parent = st.text_input("Guardar modelos en", str(workspace / "CATIA" if workspace else root / "CATIA/Models"))
    cfg = backend.load_config(root, "project")
    system = read_json(root / "Application Support/Configurations/ramair_catia_system_config.json")
    from ramair_suspension_layout import migrate
    system = migrate(system, new=True)
    from cad_design_controls import canopy_controls, crossport_controls, suspension_controls, stabilizer_controls
    tabs = st.tabs(["Canopy y costillas", "Crossports", "Suspensión y líneas", "Estabilizadores", "Opciones CATIA y exportaciones"])
    with tabs[0]:
        canopy_controls(cfg, system)
    with tabs[1]:
        crossport_controls(cfg["crossports"])
        profile_preview(Path(profile["file"]), cfg["crossports"])
    with tabs[2]:
        suspension_controls(system)
        suspension_preview_slot = st.container()
    with tabs[3]:
        stabilizer_controls(system)
        stabilizer_preview_slot = st.container()
    with tabs[4]:
        for key in ("catia_generation", "catia_exports", "debug_plots"):
            st.subheader(key.replace("_", " ").capitalize())
            cfg[key] = editor(cfg.get(key, {}), "cad/"+key)
        with st.expander("Archivos auxiliares y visualización · avanzado"):
            system["outputs"] = editor(system.get("outputs", {}), "cad/system/outputs")
        reference({k: cfg[k] for k in ("catia_generation", "catia_exports", "debug_plots")})
    # One visible switch per module; legacy export flags are derived at save time.
    cfg["optional_modules"].update(enable_canopy_stabilizers=system["stabilizers"]["active"],
        enable_suspension_lines=system["suspension"]["enabled"], enable_tip_side_bulge=system["tip_side_bulge"]["active"])
    from cad_preview import render_preview
    render_preview(root, cfg, system, profile, stabilizer_preview_slot, suspension_preview_slot)
    if st.button("Guardar modelo y generar datos CATIA", type="primary"):
        model = Path(parent) / slug(name)
        model.mkdir(parents=True, exist_ok=False)
        dat = next(Path(profile["path"]).glob("*.dat"))
        cfg["profile_inputs"] = {"main_profile": str(dat), "profile_input_order": "auto"}
        cfg["airfoil_processing"]["te_closure_mode"] = "straight_gap"
        cfg.pop("fabric_and_lines", None)
        cfg["optional_modules"]["system_config_json"] = str(model / "system_config.json")
        system.pop("fabric_thickness", None)
        write_json(model / "system_config.json", system)
        write_json(model / "model_config.json", cfg)
        write_json(model / "model.json", new_manifest("catia_model", name, profile=profile["id"]))
        shutil.copy2(root / "Generate_RamAir_Canopy_MAIN.CATScript", model / "Generate_RamAir_Canopy_MAIN.CATScript")
        start_job("cad_preprocess", command(root, "catia", model))
    models = list_entities(Path(parent), "model.json")
    if models:
        selected = select_entity(models, "Modelos guardados", "model.json", "cad-existing")
        if st.button("Abrir carpeta del modelo"):
            backend.open_local_folder(selected)
        image_gallery(selected / "Outputs/Debug", "cad/model/"+str(selected))
        st.caption("Ejecuta la macro del modelo desde CATIA V5, seleccionando sus Inputs. La preparación Python no inicia CATIA.")


def render_cfd(workspace: Path) -> None:
    st.markdown("Define las condiciones antes de seleccionar la geometría. Todos los perfiles se simulan con cuerda de 1 m; la viscosidad se calcula para satisfacer simultáneamente Reynolds y Mach.")
    name = st.text_input("Nombre de las condiciones", "M015_Re1900000")
    cols = st.columns(4)
    re = cols[0].number_input("Reynolds", 100., 1e10, 1.9e6, format="%.0f")
    mach = cols[1].number_input("Mach", .0001, 5., .15, format="%.4f")
    p = cols[2].number_input("Presión [Pa]", 1., 1e8, 101325.)
    t = cols[3].number_input("Temperatura [K]", 1., 5000., 288.15)
    physical = physical_conditions(re, mach, p, t)
    cols = st.columns(4)
    for col, key, label in zip(cols, ("velocity_m_s", "rho", "mu", "nu"), ("U [m/s]", "ρ [kg/m³]", "μ [Pa·s]", "ν [m²/s]")):
        col.metric(label, f"{physical[key]:.6g}")
    if mach > .3:
        st.warning("El solver de validación predeterminado es incompresible. Estas condiciones requieren revisar el modelo físico antes de generar casos OpenFOAM.")
    if st.button("Crear caso CFD", type="primary"):
        create_cfd_case(workspace, name, physical)
        st.success("Caso creado. Selecciona una geometría en Mallado.")


def select_cfd_geometry(workspace: Path, root: Path, allow_import: bool) -> tuple[Path | None, Path | None]:
    cfd = select_entity(list_entities(workspace / "CFD_2D", "cfd_case.json"), "Caso CFD", "cfd_case.json", "active-cfd/"+str(workspace))
    if cfd is None:
        return None, None
    info = read_json(cfd / "cfd_case.json")
    p = info["physical"]
    st.info(f"{info['name']} · c=1 m · Re={p['reynolds']:g} · M={p['mach']:g} · U={p['velocity_m_s']:.3f} m/s · μ={p['mu']:.6g} Pa·s")
    if allow_import:
        with st.expander("Añadir geometría desde la biblioteca"):
            items = catalogue(root)
            if items:
                profile = st.selectbox("Perfil 2D", items, format_func=lambda i: f"{i['name']} · {i['topology'].upper()}")
                bases = [i for i in items if i["topology"] == "closed"]
                base = st.selectbox("Base sin corte", bases, format_func=lambda i: i["name"]) if profile["topology"] == "open" and bases else None
                name = st.text_input("Nombre en este caso", profile["name"])
                if st.button("Importar geometría"):
                    import_geometry(cfd, Path(profile["path"]), name, Path(base["path"]) if base else None)
                    st.rerun()
    geometry = select_entity(list_entities(cfd / "Geometries", "geometry.json"), "Geometría", "geometry.json", "geometry/"+str(cfd))
    return cfd, geometry


def render_mesh(root: Path, workspace: Path, start_job) -> None:
    cfd, geometry = select_cfd_geometry(workspace, root, True)
    if geometry is None:
        return
    context = geometry_context(geometry)
    top = context["topology"]
    mesh_identity = str(geometry) + str((geometry / "Meshes/draft_config.json").stat().st_mtime_ns if (geometry / "Meshes/draft_config.json").exists() else "")
    if st.session_state.get("mesh-widget-context") != mesh_identity:
        for key in list(st.session_state):
            if key.startswith(("open-exp-", "closed-exp-", "open-general-", "closed-general-")):
                del st.session_state[key]
        st.session_state["mesh-widget-context"] = mesh_identity
    st.segmented_control("Tipo de perfil detectado", ["OPEN", "CLOSED"], default=top.upper(), disabled=True,
                         help="El tipo pertenece a la geometría importada. Selecciona otra geometría para cambiar de topología.")
    profile_preview(geometry / "Inputs/profile/profile_points.csv")
    mesh_rows = []
    for mesh in list_entities(geometry / "Meshes", "mesh_config.json"):
        report = read_json(mesh / "mesh_report.json")
        mesh_rows.append({"Malla": mesh.name, "Tipo": top.upper(), "Celdas": report.get("mesh_level_extrusion_volume_cells"), "checkMesh": report.get("checkMesh_status", "Pendiente"), "Aprobada y compatible": compatible_mesh(mesh, geometry)[0]})
    if mesh_rows:
        st.dataframe(mesh_rows, hide_index=True, width="stretch")
    draft = geometry / "Meshes/draft_config.json"
    presets = [p for p in (root / "Mesh Presets").rglob("*.json") if read_json(p).get("topology", p.stem.split("_")[0]) == top]
    with st.expander("Presets compartidos y configuraciones de otros casos"):
        preset = st.selectbox("Preset compatible", [None, *presets], format_func=lambda p: "Defaults" if p is None else p.stem)
        if st.button("Cargar preset como borrador"):
            if preset:
                write_json(draft, bind_mesh_config(read_json(preset), geometry))
                from mesh_workspace_controls import reset_mesh_editor_state
                reset_mesh_editor_state(top)
                st.rerun()
        candidates = [p for w in workspaces(root) for p in Path(w["path"]).glob("CFD_2D/*/Geometries/*/Meshes/*/mesh_config.json") if p.parent.parent != draft.parent]
        candidates = sorted((p for p in candidates if read_json(p).get("topology") == top), key=lambda p: (read_json(p).get("_context", {}).get("geometry_revision") != context["geometry_revision"], str(p)))
        seed = st.selectbox("Otras configuraciones", [None, *candidates], format_func=lambda p: "Seleccionar" if p is None else str(p.parent))
        if seed:
            st.warning("Solo se importarán parámetros. La malla de otro caso no se activará: es necesario regenerar con estas condiciones.")
            if st.button("Importar configuración y recalcular"):
                write_json(draft, bind_mesh_config(read_json(seed), geometry))
                st.rerun()
    def scoped_job(label, args, **kwargs):
        args = list(map(str, args))
        if label == "mesh_quality_atlas":
            return start_job(label, args, **kwargs)
        if "--generate" in args:
            cfg_path = Path(args[args.index("--config")+1])
            name = slug(args[args.index("--name")+1])
            return start_job("mesh_generate", command(root, "mesh", geometry / "Meshes", "--config", cfg_path, "--name", name))
        args += ["--output-dir", str(geometry / "Meshes")]
        return start_job(label, args, **kwargs)
    if top == "open":
        from open_experimental_mesh_page import render_open_experimental_mesh
        render_open_experimental_mesh(root, scoped_job, backend.open_mesh_viewer, workspace_geometry=geometry)
    else:
        from closed_experimental_mesh_page import render_closed_experimental_mesh
        render_closed_experimental_mesh(root, scoped_job, backend.open_mesh_viewer, workspace_geometry=geometry)
    with st.expander("Guardar configuración paramétrica en la biblioteca"):
        saved = list_entities(geometry / "Meshes", "mesh_config.json")
        source = st.selectbox("Configuración a compartir", [draft, *[p / "mesh_config.json" for p in saved]], format_func=lambda p: p.parent.name if p != draft else "Borrador", key="mesh-share-source/"+str(geometry))
        name = st.text_input("Nombre del preset", "mi_malla")
        level = st.selectbox("Refinamiento aproximado", ["coarse", "medium", "fine"])
        overwrite = st.checkbox("Sobrescribir preset del mismo nombre")
        if st.button("Guardar preset compartido"):
            path = save_mesh_preset(root, bind_mesh_config(read_json(source), geometry), name, level, overwrite)
            st.success(f"Preset guardado: {path.name}")


def select_setup(root: Path, workspace: Path) -> Path | None:
    setups = all_solver_setups(workspace)
    if not setups:
        st.info("Prepara primero un caso OpenFOAM desde una malla aprobada.")
        return None
    return st.selectbox("Caso OpenFOAM", setups, format_func=lambda p: str(p.relative_to(workspace)).replace("/Geometries/", " · ").replace("/Meshes/", " · ").replace("/OpenFOAM/", " · "), key="setup/"+str(workspace))


def render_solver(root: Path, workspace: Path, start_job) -> None:
    cfd, geometry = select_cfd_geometry(workspace, root, False)
    if not geometry:
        return
    meshes = [p for p in list_entities(geometry / "Meshes", "mesh_config.json") if compatible_mesh(p, geometry)[0]]
    if not meshes:
        st.info("Genera una malla, comprueba checkMesh y apruébala en Mallado.")
        return
    chosen = st.multiselect("Mallas aprobadas que recibirán esta configuración", meshes, default=meshes[:1], format_func=lambda p: p.name)
    solver, plan = solver_defaults(root)
    if geometry_context(geometry)["topology"] == "open":
        solver["maxCo"] = plan["maxCo"] = 1.0
        st.info("Arranque abierto: Courant máximo inicial 1. El valor 50 del ejemplo cerrado no es un ajuste general para los labios del corte. Puedes modificarlo tras comprobar estabilidad y resolución temporal.")
    existing = [p for mesh in meshes for p in list_entities(mesh / "OpenFOAM", "solver_setup.json")]
    source = st.selectbox("Partir de configuración", [None, *existing], format_func=lambda p: "Defaults actuales de Validation" if p is None else f"{p.parent.parent.name}/{p.name}")
    if source:
        solver, plan = read_json(source / "solver_config.json"), read_json(source / "phase_plan.json")
    revision = str(geometry)+"/"+str(source or "defaults")
    solver.pop("optional_secondary_model", None)
    for key in ("velocity_profile_stations", "velocity_profile_sample_points", "wall_profile_analysis", "validation_phase_d_steady_equivalence"):
        solver.pop(key, None)
    st.info("OpenFOAM Foundation 14 ejecuta foamRun con el módulo incompressibleFluid. SIMPLE prepara el estado RANS y PIMPLE avanza URANS; simpleFoam/pimpleFoam son nombres compatibles de lanzamiento. La malla y las condiciones físicas proceden del caso seleccionado.")
    hidden = {"config_schema_version", "preset_id", "velocity_source", "topology_profiles", "validation_study", "screening_note", "single_solver_contract", "workspace_solver_contract", "time_step_mode", "n_non_orthogonal_correctors", "mesh_quality_numerics_mode", "endTime_star", "average_from_fraction", "field_write_control", "field_write_interval_star", "purgeWrite", "steady_write_interval_iterations"}
    hidden.add("temporal_accuracy")
    solver.get("validation_write_strategy", {}).pop("authoritative", None)
    general_keys = {"solver", "solver_module", "turbulence_model", "optional_secondary_model", "farfield_boundary_condition", "transient", "steady_initialization_enabled"}
    groups = {"General": {}, "Tiempo y fases A–E": {}, "Residuales y convergencia": {}, "Esquemas y correctores": {}, "Escritura y postproceso": {}}
    for key, value in solver.items():
        if key in hidden:
            continue
        group = "General" if key in general_keys else "Residuales y convergencia" if any(x in key.lower() for x in ("residual", "convergence", "max_iterations")) else "Escritura y postproceso" if any(x in key.lower() for x in ("write", "purge", "profile", "average")) else "Tiempo y fases A–E" if any(x in key for x in ("deltaT", "DeltaT", "maxCo", "endTime", "ddt")) else "Esquemas y correctores"
        groups[group][key] = value
    for tab, (name, values) in zip(st.tabs(list(groups)), groups.items()):
        with tab:
            solver.update(editor(values, "solver/"+revision+"/"+name))
            if name == "Tiempo y fases A–E":
                st.caption("Δt* = Δt U/c. El ajuste por Courant puede reducir el paso sin exceder el máximo físico elegido.")
                for i, phase in enumerate(plan["stages"]):
                    with st.expander("Fase "+phase["stage"]):
                        phase.setdefault("maxCo", float(plan.get("maxCo", solver["maxCo"])))
                        phase = editor(phase, "phase/"+revision+phase["stage"], {"stage", "sampling", "write_policy"})
                        next_phase = plan["stages"][i+1] if i+1 < len(plan["stages"]) else {}
                        default_policy = "restart_history" if any(p.get("scheme", "").lower() == "backward" for p in (phase, next_phase)) else "phase_boundary"
                        policies = ["phase_boundary", "restart_history", "configured_interval"]
                        phase["write_policy"] = st.selectbox("Escritura durante esta fase", policies, index=policies.index(phase.get("write_policy", default_policy)),
                            format_func=lambda p: {"phase_boundary": "Checkpoint al final", "restart_history": "Historial denso para reinicio backward", "configured_interval": "Intervalo de escritura configurado"}[p], key="write/"+revision+phase["stage"])
                        st.caption("El intervalo se alinea al final de fase para garantizar el checkpoint. El historial denso escribe como máximo cada paso nominal y conserva al menos tres estados; purgeWrite=0 conserva todos. Un intervalo más amplio requiere revisar el reinicio backward.")
                        plan["stages"][i] = phase
    solver["mesh_quality_numerics_mode"] = "automatic"
    with st.expander("Evaluación opcional al terminar la fase D"):
        gate = plan.setdefault("phase_d_steady_equivalence", {})
        gate["enabled"] = st.toggle("Aceptar equivalencia RANS/URANS al terminar D", bool(gate.get("enabled", False)), help="Desactivado por defecto: siempre pasa a producción E. Si se activa, exige suficientes muestras y compara medias y fluctuaciones; no equivale a validación experimental.")
        if gate["enabled"]:
            gate.setdefault("coefficient_floors", {"Cl":.05,"Cd":.005,"Cm":.005,"Cl_over_Cd":1.})
            gate.update(editor(gate, "solver/gate/"+revision, {"enabled"}))
            st.caption("Floors: mínimos usados solo como denominador de errores relativos cerca de cero. No modifican fuerzas ni coeficientes calculados.")
    solver.setdefault("validation_write_strategy", {})["authoritative"] = True
    solver["time_step_mode"] = "adaptive_physics_limited"
    solver.pop("n_non_orthogonal_correctors", None)
    solver.get("steady_numerics", {}).pop("n_non_orthogonal_correctors", None)
    plan.update(target_deltaT_star=solver["maxDeltaT_star"], maxCo=solver["maxCo"], max_outer_correctors=solver["n_outer_correctors"])
    plan["total_time_star"] = sum(float(p["duration_time_star"]) for p in plan["stages"])
    plan["production_start_time_star"] = sum(float(p["duration_time_star"]) for p in plan["stages"] if not p["sampling"])
    plan["average_from_fraction"] = plan["production_start_time_star"]/max(plan["total_time_star"], 1e-12)
    solver.update(endTime_star=plan["total_time_star"], average_from_fraction=plan["average_from_fraction"])
    st.dataframe(plan["stages"], hide_index=True, width="stretch")
    st.caption("Correctores no ortogonales y laplacianos se recalculan para cada malla a partir de checkMesh.")
    with st.expander("Cummings · orientación temporal según topología"):
        from ramair_2d_timestep_advisor import temporal_frequency_budget
        temporal = solver.setdefault("temporal_accuracy", {})
        temporal.update(editor(temporal, "temporal/"+revision, {"profile_id", "strouhal_scope"}))
        budget = temporal_frequency_budget(delta_t_star=float(solver["deltaT_star"]), max_delta_t_star=float(solver["maxDeltaT_star"]), end_time_star=float(solver["endTime_star"]), average_from_fraction=float(solver["average_from_fraction"]), temporal_config=temporal)
        st.write(f"Resolución objetivo: Δt* ≤ {budget['engineering_deltaT_star_ceiling']:.6g}. Ventana estadística mínima: {budget['minimum_average_window_time_star']:.6g} t*.")
        st.caption("Criterio de muestreo Δt* ≤ 1/(St máximo × muestras/ciclo). Es un límite de precisión; la estabilidad local por Courant puede exigir un paso menor. Los rangos St son hipótesis editables, no frecuencias ya medidas.")
        def apply_time_ceiling():
            prefix = "solver/"+revision+"/Tiempo y fases A–E/"
            st.session_state[prefix+"maxDeltaT_star"] = float(budget["engineering_deltaT_star_ceiling"])
            st.session_state[prefix+"deltaT_star"] = min(float(solver["deltaT_star"]), float(budget["engineering_deltaT_star_ceiling"]))
        st.button("Aplicar límite de paso recomendado", on_click=apply_time_ceiling, key="apply-time/"+revision)
        st.json(budget)
    mode = st.radio("Ángulos", ["single", "range", "list"], horizontal=True, format_func=lambda v: {"single": "Un ángulo", "range": "Intervalo", "list": "Grupo libre"}[v])
    if mode == "range":
        cols = st.columns(3)
        a, b, step = cols[0].number_input("Inicio [°]", value=0.), cols[1].number_input("Fin [°]", value=16.), cols[2].number_input("Salto [°]", min_value=.001, value=4.)
        angles = parse_angles(mode, "", a, b, step)
    else:
        values = st.text_input("Ángulo [°]" if mode == "single" else "Ángulos separados por coma", "8" if mode == "single" else "0,4,8,12,16")
        angles = parse_angles(mode, values)
        if mode == "single" and len(angles) != 1:
            st.error("Selecciona un solo ángulo.")
            return
    name = st.text_input("Nombre de la configuración OpenFOAM", "solver_01")
    with st.expander("Resumen completo de configuración"):
        st.json({"solver": solver, "phases": plan, "angles": angles, "physical": geometry_context(geometry)["physical"]})
    if st.button("Guardar y generar casos OpenFOAM", disabled=not chosen, type="primary"):
        if any(float(p["duration_time_star"]) <= 0 for p in plan["stages"]):
            raise ValueError("Todas las fases deben tener duración positiva.")
        setups = []
        for mesh in chosen:
            setup = create_solver_setup(root, mesh, name, solver, plan, angles)
            setups.append(setup)
        args = command(root, "prepare", setups[0])
        for setup in setups[1:]:
            args += ["--additional-path", str(setup)]
        start_job("prepare_openfoam", args)


def phase_result_gallery(setup: Path, key: str) -> None:
    cases=[p.parent.parent for p in (setup/"Angles").glob("*/Postprocess/case_summary.json")]
    if not cases:return
    st.subheader("Resultados postprocesados por ángulo")
    case=st.selectbox("Ángulo postprocesado",sorted(cases),format_func=lambda p:p.name,key=key+"/angle")
    rans,urans=st.tabs(["RANS","URANS"])
    with rans:
        folder=case/"Postprocess/RANS"
        st.json(read_json(folder/"RANS_force_summary.json"),expanded=False)
        image_gallery(folder,key+"/rans/"+case.name)
    with urans:
        summary=read_json(case/"Postprocess/case_summary.json")
        if summary.get("simulation_mode")=="RANS" or summary.get("simulation_mode_evidence",{}).get("resolved")=="RANS":
            st.caption("URANS no ejecutado en este ángulo.")
        else:image_gallery(case/"Postprocess/URANS",key+"/urans/"+case.name)


def render_execution(root: Path, workspace: Path, start_job) -> None:
    ordinary, convergence = st.tabs(["Ejecución habitual", "Convergencia de malla · RANS"])
    with ordinary:
        render_standard_execution(root, workspace, start_job)
    with convergence:
        from workspace_rans_convergence import render
        render(root, workspace, start_job)


def render_standard_execution(root: Path, workspace: Path, start_job) -> None:
    setup = select_setup(root, workspace)
    if not setup:
        return
    manifest = read_json(setup / "solver_setup.json")
    angles = st.multiselect("Ángulos a ejecutar", manifest["angles"], default=manifest["angles"])
    from workspace_execution_controls import execution_options, monitors
    cores, concurrent, angles, counts, options = execution_options(setup, angles)
    real_run = st.toggle("Ejecutar simulación real", False, help="Desactivado: comprueba y registra el plan sin ejecutar el solver.")
    if st.button("Iniciar / continuar cola" if real_run else "Comprobar plan de ejecución", disabled=not angles, type="primary"):
        queue = queue_manifest(root, setup, angles, cores, concurrent, real_run, cores_by_angle=counts, options=options)
        start_job("workspace_queue", [sys.executable, root / "CFD_2D/scripts/ramair_2d_parallel_campaign.py", "--manifest", queue])
        st.success("Plan registrado con recursos, acciones y comandos explícitos.")
    monitors(root, setup)
    with st.expander("Paquete para otro dispositivo con OpenFOAM"):
        if st.button("Generar paquete portable", disabled=not angles):
            args = [sys.executable, root / "Application Support/Tools/package_ramair_remote_execution.py", "--project-root", root, "--transient-phase-plan", setup / "phase_plan.json", "--n-cores", cores]
            args += ["--execution-mode", "rans_only" if options.get("phase_action") == "rans_only" else "rans_urans", "--no-renumber-before-decompose"]
            for alpha in angles:
                args += ["--case", setup / "Angles" / alpha_name(alpha)]
            start_job("remote_package", args)
        returned = st.file_uploader("Paquete de resultados devuelto", type=["zip"], key="studio-remote-return")
        if st.button("Verificar e importar resultados devueltos", disabled=returned is None):
            upload = root / "Application Support/Temp" / (str(time.time_ns())+"_"+Path(returned.name).name)
            upload.parent.mkdir(parents=True, exist_ok=True)
            upload.write_bytes(returned.getvalue())
            start_job("remote_import", [sys.executable, root / "Application Support/Tools/import_ramair_remote_results.py", "--project-root", root, "--archive", upload, "--existing-action", "archive"])
    phase_result_gallery(setup,"execution/results/"+str(setup))
    with st.expander("Eliminar resultados de ángulos y reiniciar fases"):
        reset_selection = st.multiselect("Ángulos que se reiniciarán", manifest["angles"], default=[], key="reset/angles/"+str(setup), help="Esta selección es independiente de la cola de ejecución.")
        phase = st.selectbox("Fase a reiniciar", ["RANS", "URANS", "RANS y URANS"])
        st.caption("Se archivan los datos anteriores antes de reconstruir el caso. Recalcular RANS invalida el arranque URANS que dependía de él; su evidencia anterior sigue conservada.")
        if st.button("Archivar y reiniciar fases", disabled=not reset_selection):
            from ramair_workspace_lifecycle import reset_angles
            reset_angles(root, setup, reset_selection, phase)
            st.success("Fases archivadas y casos preparados para un nuevo arranque.")
            st.rerun()


def render_postprocess(root: Path, workspace: Path, start_job) -> None:
    setup = select_setup(root, workspace)
    if not setup:
        return
    manifest = read_json(setup / "solver_setup.json")
    from ramair_simulation_inventory import simulation_inventory
    with st.expander("Simulaciones disponibles", expanded=True):
        available=simulation_inventory(setup)
        st.dataframe([{k:r[k] for k in ("alpha","RANS","URANS","Estado")} for r in available], hide_index=True, width="stretch")
    analysis, polar, compare = st.tabs(["Postproceso y análisis", "Polar y resultados", "Comparación"])
    with analysis:
        angles = st.multiselect("Ángulos a postprocesar", manifest["angles"], default=manifest["angles"][:1])
        from workspace_postprocess_controls import postprocess_controls
        options = postprocess_controls()
        if st.button("Postprocesar selección", disabled=not angles or options is None, type="primary"):
            path = write_json(setup / "postprocess_options.json", options)
            start_job("postprocess", command(root, "postprocess", setup, "--config", path, "--angles", *angles))
        phase_result_gallery(setup,"postprocess/results/"+str(setup))
        with st.expander("Revisión independiente de convergencia"):
            cols = st.columns(2)
            for col, phase in zip(cols, ("RANS", "URANS")):
                if col.button("Revisar "+phase, disabled=not angles):
                    from ramair_workspace_lifecycle import review_angles
                    st.json(review_angles(setup, angles, phase))
        with st.expander("Conservar y exportar resultados"):
            from ramair_workspace_storage import windows_workspace
            destination = st.text_input("Carpeta de destino de los resultados", str(windows_workspace(root, workspace) / "Selected Results" / setup.parent.parent.parent.parent.name / setup.parent.parent.name / setup.name))
            categories = st.multiselect("Datos a conservar", ["polars", "coefficients", "wall", "images", "animations"], default=["polars", "coefficients", "wall", "images"])
            if st.button("Copiar y verificar datos seleccionados"):
                st.json(export_products(setup, Path(destination), categories))
            st.caption("La copia conserva los datos originales de WSL. El manifiesto registra los archivos exportados y verifica sus contenidos.")
            with st.expander("Liberar campos de cálculo después de exportar"):
                from ramair_workspace_lifecycle import releasable_paths, release_runtime_data
                retention = Path(destination) / "retention_manifest.json"
                if retention.is_file():
                    paths = releasable_paths(setup)
                    st.dataframe([{"Carpeta de resultados que se eliminará": str(p)} for p in paths], hide_index=True)
                    confirmed = st.checkbox("He revisado la copia exportada y deseo eliminar estas carpetas de resultados de WSL", value=False)
                    if st.button("Eliminar únicamente los resultados mostrados", disabled=not confirmed):
                        st.json(release_runtime_data(setup, retention))
                else:
                    st.caption("Primero copia y verifica los productos que deseas conservar.")
    with polar:
        stage = st.radio("Resultados de", ["URANS", "RANS"], horizontal=True)
        if stage == "RANS":
            st.caption("Puede añadir el promedio RANS aunque URANS no exista, sea breve o haya divergido. Revise la estabilidad del tramo RANS: el punto se etiqueta RANS y no acredita convergencia temporal URANS.")
        accepted = st.multiselect("Ángulos para la polar", manifest["angles"])
        cols = st.columns(2)
        if cols[0].button("Incluir puntos revisados", disabled=not accepted):
            curate_points(setup, accepted, stage, "add")
        if cols[1].button("Retirar puntos", disabled=not accepted):
            curate_points(setup, accepted, stage, "remove")
        frame = load_polar(setup)
        if not frame.empty:
            if stage == "URANS":
                st.caption("URANS se compara con los puntos RANS revisados de esta misma configuración. Cada fase mantiene su criterio de aceptación y su ventana de promedio.")
            else:
                frame = frame[frame.stage == "RANS"]
            st.dataframe(frame, hide_index=True, width="stretch")
            from ramair_comparison_figures import polar_figures
            for path in polar_figures(frame,setup/"Polar/Figures",group="stage"):st.image(path,width="stretch")
            for phase in frame.stage.unique():
                st.json({phase: polar_metrics(frame[frame.stage == phase])})
            st.download_button("Descargar polar CSV", frame.to_csv(index=False), "polar.csv")
        else:
            st.info("Incluye puntos de resultados reales después de revisarlos.")
    with compare:
        setups = all_solver_setups(workspace)
        left = st.selectbox("Ejecución A", setups, format_func=lambda p: str(p.relative_to(workspace)), key="comparison-A")
        compatible = [p for p in setups if p != left and comparable_setups(left, p)[0]]
        if not compatible:
            st.info("Se necesita otra configuración OpenFOAM con las mismas condiciones CFD.")
            return
        right = st.selectbox("Ejecución B", compatible, format_func=lambda p: str(p.relative_to(workspace)), key="comparison-B")
        third = st.selectbox("Ejecución C (opcional)", [None, *[p for p in compatible if p != right]],
            format_func=lambda p: "Sin tercera ejecución" if p is None else str(p.relative_to(workspace)), key="comparison-C")
        stage = st.radio("Fase de comparación", ["URANS", "RANS"], horizontal=True)
        include_closed = st.toggle("Cargar polar cerrada de Validación LS", False,
            help="Superpone los puntos publicados del laboratorio, incluidos sus URANS parciales. Comprueba Reynolds/Mach y conserva su procedencia.")
        frames = []
        selected_comparisons = [("A", left), ("B", right)] + ([("C", third)] if third else [])
        st.caption(" · ".join(f"{tag}: {path.parent.parent.parent.parent.name} / {path.name}" for tag, path in selected_comparisons))
        for tag, path in selected_comparisons:
            frame = load_polar(path)
            if not frame.empty:
                frame = frame[frame.stage == stage].copy()
                frame["Ejecución"] = tag+" · "+path.parent.parent.parent.parent.name
                frames.append(frame)
                st.json({tag: polar_metrics(frame)})
        closed_reference = pd.DataFrame()
        if include_closed:
            from ramair_polar_comparison import load_closed_validation_polar, differences_to_reference
            try:
                closed_reference = load_closed_validation_polar(root, left)
                if not closed_reference.empty:
                    closed_reference["Ejecución"] = "Closed · Validación publicada"
                    frames.append(closed_reference)
                    st.caption("La referencia cerrada es la polar publicada del laboratorio, con puntos URANS parciales. La comparación con RANS abierto es de coeficientes adimensionales a igual Re/Mach; no demuestra equivalencia de modelo ni convergencia temporal.")
                    for tag,path in selected_comparisons:
                        f=load_polar(path)
                        if not f.empty:
                            st.caption(f"{tag} frente a Closed · ángulos comunes")
                            st.dataframe(differences_to_reference(f[f.stage==stage],closed_reference),hide_index=True,width="stretch")
            except ValueError as exc:
                st.warning(str(exc))
        if frames:
            data = pd.concat(frames, ignore_index=True)
            st.dataframe([{"Ejecución":label,**polar_metrics(f)} for label,f in data.groupby("Ejecución")],hide_index=True,width="stretch")
            from ramair_comparison_figures import polar_figures
            for path in polar_figures(data,setup/"Polar/Comparison"):
                st.image(path,width="stretch")
            st.dataframe(data, hide_index=True, width="stretch")
            st.download_button("Descargar comparación combinada", data.to_csv(index=False), "comparacion_polares.csv")
        differences = compare_polars(left, right, stage)
        st.dataframe(differences, hide_index=True, width="stretch")
        if not differences.empty:
            from ramair_comparison_figures import series_figure
            plotted=differences.melt(id_vars="alpha",value_vars=["Δ Cl [%]","Δ Cd [%]","Δ Cm [%]"],var_name="coefficient",value_name="difference")
            st.image(series_figure(plotted,setup/"Polar/Comparison/differences.png",x="alpha",y="difference",group="coefficient",xlabel=r"$\alpha$ [deg]",ylabel="Relative difference [%]"),width="stretch")
        if third:
            st.caption("Diferencias C respecto a A · solo ángulos comunes")
            st.dataframe(compare_polars(left, third, stage), hide_index=True, width="stretch")
            st.caption("Diferencias C respecto a B · solo ángulos comunes")
            st.dataframe(compare_polars(right, third, stage), hide_index=True, width="stretch")
        with st.expander("Distribuciones Cp, Cf, y+ por ángulo"):
            from ramair_workspace_lifecycle import wall_comparison
            shared = sorted(set(read_json(left / "solver_setup.json")["angles"]) & set(read_json(right / "solver_setup.json")["angles"]))
            if shared:
                alpha = st.selectbox("Ángulo común", shared)
                for label, data in wall_comparison(left, right, alpha, stage).items():
                    st.caption(label)
                    if not data.empty:
                        from ramair_comparison_figures import series_figure
                        st.image(series_figure(data,setup/"Polar/Comparison"/(label.replace('+','plus')+"_wall.png"),x="x/c",y="value",group="series",xlabel="x/c",ylabel=label,invert_y=label=="Cp",title=f"{stage}, alpha={alpha:g} deg"),width="stretch")


def render_application(root: Path, manager, start_job, validation_renderer) -> None:
    initialize(root)
    from ramair_workspace_storage import publish_workspace_links
    publish_workspace_links(root)
    st.markdown("""<style>.block-container{padding-top:1.5rem;max-width:1480px}h1{letter-spacing:-.025em}
    [data-testid=stMetric]{padding:.6rem;border:1px solid #71809666;border-radius:10px}
    [data-testid=stMetricValue]{font-size:1.3rem}.stButton button{border-radius:8px}div[data-testid=stExpander]{border-radius:10px}
    @media(max-width:760px){.block-container{padding-left:1rem;padding-right:1rem}}</style>""", unsafe_allow_html=True)
    items = workspaces(root)
    paths = [i["path"] for i in items]
    current = st.session_state.get("workspace-path")
    st.sidebar.title("RamAir Studio")
    st.sidebar.caption("Diseño CAD · Aerodinámica 2D")
    selected = st.sidebar.selectbox("Caso de trabajo", [None, *paths], index=paths.index(current)+1 if current in paths else 0,
        format_func=lambda p: "Bibliotecas compartidas" if p is None else read_json(Path(p) / "workspace.json").get("name", Path(p).name))
    st.session_state["workspace-path"] = selected
    workspace = Path(selected) if selected else None
    page = st.sidebar.radio("Sección", PAGES, key="studio-page", label_visibility="collapsed")
    with st.sidebar.expander("Capturas de producción URANS"):
        capture_path=root/"CFD_2D/app_state/production_capture.json"
        capture=read_json(capture_path)
        enabled=st.checkbox("Guardar más instantes en producción", value=bool(capture.get("enabled",False)))
        frames=st.number_input("Capturas finales objetivo",8,1000,int(capture.get("frames",120)))
        fraction=st.number_input("Fracción final de producción",.01,1.,float(capture.get("tail_fraction",.1)))
        st.caption("Se aplica al próximo inicio de fase E en todas las secciones. Aumenta las escrituras durante producción; retiene aproximadamente el intervalo final indicado. No recupera tiempos que no se guardaron.")
        if st.button("Guardar política de capturas"):
            write_json(capture_path,{"enabled":enabled,"frames":frames,"tail_fraction":fraction})
            st.success("Política guardada para próximas fases de producción.")
    st.title(page)
    if workspace:
        st.caption(f"{read_json(workspace / 'workspace.json')['name']} · {workspace}")
    try:
        if page == "Inicio":
            st.markdown("**Un entorno común, dos herramientas independientes.** Prepara perfiles una vez y reutilízalos en tus modelos CATIA o en estudios CFD con geometrías, mallas y solvers propios.")
            cols = st.columns(3)
            cols[0].metric("Casos de trabajo", len(items))
            cols[1].metric("Perfiles preparados", len(catalogue(root)))
            cols[2].metric("Presets de malla", len(list((root / "Mesh Presets").rglob("*.json"))))
            st.markdown("1. Prepara perfiles 2D y su borde de salida.\n2. Crea un caso de trabajo.\n3. Genera modelos CATIA o define condiciones CFD.\n4. Importa geometrías, genera y aprueba mallas.\n5. Prepara OpenFOAM, ejecuta ángulos y compara resultados.")
            if st.button("Verificar entorno e instalaciones"):
                start_job("environment", backend.environment_command(root))
            checks = read_json(root / "CFD_2D/reports/environment_report.json", [])
            if checks:
                with st.expander("Estado de las instalaciones y diagnóstico"):
                    st.dataframe(checks, hide_index=True, width="stretch")
            with st.expander("Instalación y paquetes"):
                st.caption("Requeridos: Python y bibliotecas científicas, Gmsh, OpenFOAM en WSL. XFOIL para cortes, ParaView para figuras de campos y CATIA V5 para CAD. simpleFoam/foamRun son ejecutables de OpenFOAM, no instalaciones independientes.")
                if st.button("Crear paquete portable de la aplicación"):
                    start_job("package", [sys.executable, root / "Application Support/Tools/package_ramair_project.py", "--project-root", root])
            report = root / "Documents and Manuals/Current/DELIVERY_REPORT.md"
            if report.is_file():
                st.download_button("Descargar informe de arquitectura y auditoría", report.read_bytes(), report.name)
                with st.expander("Resumen y verificación de esta versión"):
                    st.markdown(report.read_text(encoding="utf-8"))
            with st.expander("Manuales de cada sección y mantenimiento"):
                manuals = sorted((root / "Documents and Manuals/Current").glob("*.md"))
                if manuals:
                    manual = st.selectbox("Manual", manuals, format_func=lambda p: p.stem.replace("_", " "))
                    st.markdown(manual.read_text(encoding="utf-8"))
                st.link_button("Repositorio del proyecto en GitHub", "https://github.com/alexmm10/Paratech_ramair_design")
                st.caption("GitHub conserva código, pruebas, presets y documentación. Los campos CFD se transfieren como datos de trabajo independientes; consulta Instalación y mantenimiento.")
        elif page == "Casos de trabajo":
            render_workspaces(root, start_job)
        elif page == "Perfiles 2D":
            render_profiles(root, start_job)
        elif page == "Geometría CATIA":
            render_catia(root, workspace, start_job)
        elif page == "Validation & Convergence Lab":
            validation_renderer(root, start_job)
            with st.expander("Galería permanente de resultados finales"):
                image_gallery(root / "Validation Gallery", "validation-gallery")
                source_folder = st.text_input("Carpeta con figuras finales que quieres conservar", key="gallery-source")
                if source_folder and Path(source_folder).is_dir():
                    available = sorted(Path(source_folder).glob("*.png"))
                    selected_figures = st.multiselect("Seleccionar figuras finales", available, format_func=lambda p:p.name)
                    gallery_name = st.text_input("Nombre del conjunto de figuras", "Resultados seleccionados")
                    if st.button("Conservar figuras en la galería", disabled=not selected_figures):
                        from ramair_validation_gallery import preserve_figures
                        st.success(str(preserve_figures(root, selected_figures, gallery_name)))
        elif page == "Archivos y logs":
            st.dataframe([j.__dict__ for j in manager.list_jobs(40)], hide_index=True, width="stretch")
            folder = workspace or root / "Application Support/Logs"
            if st.button("Abrir carpeta"):
                backend.open_local_folder(folder)
            st.caption(str(folder))
        elif workspace is None:
            st.info("Crea o selecciona un caso de trabajo para continuar.")
        elif page == "Caso CFD":
            render_cfd(workspace)
        elif page == "Mallado":
            render_mesh(root, workspace, start_job)
        elif page == "Caso OpenFOAM":
            render_solver(root, workspace, start_job)
        elif page == "Ejecución":
            render_execution(root, workspace, start_job)
        elif page == "Postproceso":
            render_postprocess(root, workspace, start_job)
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        st.error(str(exc))
    st.sidebar.divider()
    jobs = manager.list_jobs(5)
    if jobs:
        st.sidebar.caption("Trabajos recientes")
        for job in jobs[:3]:
            st.sidebar.caption(f"{job.stage} · {job.status}")
