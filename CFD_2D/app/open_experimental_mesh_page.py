"""Streamlit panel for the isolated open-airfoil Gmsh experiment."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from experimental_mesh_comparison import render_mesh_quality_comparator

from ramair_2d_bump_matching import bump_cell_sizes, match_four_segment_bumps
from ramair_2d_split_progression import (
    automatic_split_progression,
    evaluate_manual_split_progression,
)
from ramair_2d_open_experimental_mesh import (
    default_config,
    flat_plate_first_height,
    load_geometry,
)


EXPERIMENT_ID = "open_reference_from_scratch"
DEFAULT_NAME = "open_reference_hybrid_experimental_v1"
StartJob = Callable[..., Any]


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            default=lambda value: str(value) if isinstance(value, Path) else _raise_json_type(value),
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _raise_json_type(value: Any) -> None:
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _default_config(root: Path) -> dict[str, Any]:
    del root
    return default_config()


def _geometry_variants(root: Path, *, open_only: bool) -> list[str]:
    values = [
        path.parent.name
        for path in sorted((root / "CFD_2D/CFD_2D_inputs/geometry").glob("*/profile_points.csv"))
        if path.parent.name.startswith("open_") == open_only
    ]
    return values


def _checkmesh_guidance(report: dict[str, Any], log_text: str) -> list[tuple[str, str]]:
    """Translate native checkMesh failures into bounded meshing actions."""
    text = log_text.lower()
    guidance: list[tuple[str, str]] = []
    if "interpolation weight" in text and (
        "failed" in text or float(report.get("checkMesh_min_face_interpolation_weight", 1.0) or 1.0) < 0.05
    ):
        guidance.append((
            "Peso de interpolación bajo",
            "La cara queda demasiado cerca de uno de los dos centros de celda. En esta topología suele "
            "ser el salto quad-triángulo del inlet: reduce gradualmente el primer triángulo o el factor "
            "sobre el espaciado tangencial; evita refinar solo un lado de la cara.",
        ))
    if "volume ratio" in text and (
        "failed" in text or float(report.get("checkMesh_min_face_volume_ratio", 1.0) or 1.0) < 0.01
    ):
        guidance.append((
            "Ratio de volumen bajo",
            "Dos celdas vecinas tienen volúmenes muy distintos. Amplía la distancia de transición o "
            "reduce la diferencia entre tamaños objetivo; no compenses únicamente con más nodos de pared.",
        ))
    if "non-orthogonality" in text and float(
        report.get("checkMesh_max_non_orthogonality_deg", 0.0) or 0.0
    ) >= 70.0:
        guidance.append((
            "No ortogonalidad severa",
            "Corrige la geometría/transición local antes de aumentar correctores. Revisa normales de BL, "
            "curvatura de labios/TE y tamaño del triángulo que recibe el frente prismático.",
        ))
    if "skewness" in text and float(report.get("checkMesh_max_skewness", 0.0) or 0.0) >= 4.0:
        guidance.append((
            "Skewness interna excesiva",
            "Suaviza la variación tangencial y normal y localiza el set VTK exacto. La skewness de "
            "OpenFOAM no es la métrica equiangular normalizada de ICEM.",
        ))
    if "determinant" in text and float(report.get("checkMesh_min_cell_determinant", 1.0) or 1.0) < 1.0e-3:
        guidance.append((
            "Determinante pequeño",
            "La celda está cerca de perder independencia geométrica. Reduce curvatura/torsión local, "
            "evita aristas casi coincidentes y distribuye la transición entre más celdas.",
        ))
    if not guidance:
        guidance.append((
            "Diagnóstico general",
            "Abre los sets VTK de checkMesh y compara la posición con inlet, TE y frente de BL. "
            "Modifica una familia de tamaños cada vez y conserva la última revisión que pasa.",
        ))
    return guidance


def render_open_experimental_mesh(
    root: Path,
    start_job: StartJob,
    open_mesh: Callable[[Path, Path, str], int],
) -> None:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revisions_root = experiment / "revisions"
    revisions = sorted(
        [path for path in revisions_root.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if revisions_root.is_dir() else []
    draft_path = experiment / "draft_config.json"
    if not draft_path.is_file():
        _write_json(draft_path, _default_config(root))

    with st.expander("Experimental: malla abierta híbrida desde cero", expanded=False):
        st.info(
            "Este laboratorio no reutiliza la arquitectura del mallador abierto anterior. Genera una "
            "capa límite exterior, un campo triangular exterior hasta 50c y una cavidad triangular "
            "conectada por el inlet. Todos los tamaños se expresan respecto a la cuerda."
        )
        selected: Path | None = None
        if revisions:
            labels = [path.name for path in revisions]
            selected_name = st.selectbox("Malla experimental existente", labels, key="open-exp-existing")
            selected = revisions[labels.index(selected_name)]
            report = _read_json(selected / "mesh_report.json", {}) or {}
            metrics = st.columns(4)
            metrics[0].metric("Estado", str(report.get("status", "-")))
            metrics[1].metric("Celdas 2D", str(report.get("mesh_level_extrusion_surface_cells", "-")))
            metrics[2].metric("minSICN", f"{float(report.get('minimum_gmsh_minSICN', 0.0)):.3g}")
            metrics[3].metric("checkMesh", str(report.get("checkMesh_status", "no ejecutado")))
            actions = st.columns(4)
            if actions[0].button("Abrir malla", disabled=not (selected / "mesh_final.msh").is_file()):
                open_mesh(root, selected / "mesh_final.msh", "windows_python")
            if actions[1].button("Cargar sus ajustes"):
                loaded = _read_json(selected / "mesh_config.json", {}) or {}
                _write_json(draft_path, loaded)
                st.success("Ajustes cargados como borrador editable; la revisión guardada no ha cambiado.")
                st.rerun()
            check_mesh_ok = str(report.get("checkMesh_status")) == "OK"
            if actions[2].button(
                "Aprobar tras revisión",
                disabled=not check_mesh_ok,
                help=(
                    "La aprobación solo se habilita después de un checkMesh OK."
                    if not check_mesh_ok else
                    "Registra la aprobación explícita de esta revisión comprobada."
                ),
            ):
                start_job(
                    "open_experimental_mesh_approve",
                    [str(sys.executable), str(root / "CFD_2D/scripts/ramair_2d_open_experimental_mesh.py"),
                     "--project-root", str(root), "--approve", selected.name],
                )
            if actions[3].button("Abrir carpeta"):
                from workflow_backend import open_local_folder
                open_local_folder(selected)
            problem_vtks = sorted(selected.glob("*.vtk"))
            problem_vtks += sorted((selected / "checkMesh_problem_locations").glob("*.vtk"))
            problem_vtks += sorted((selected / "quality_distributions").glob("*.vtk"))
            quality_actions = st.columns(2)
            if quality_actions[0].button(
                "Abrir sets problemáticos en ParaView",
                disabled=not problem_vtks,
                help="Carga los sets exactos escritos por checkMesh junto con la polyMesh convertida.",
                key=f"open-exp-paraview-{selected.name}",
            ):
                from workflow_backend import open_checkmesh_problem_viewer
                try:
                    pid = open_checkmesh_problem_viewer(root, selected)
                    st.success(f"ParaView iniciado con {len(problem_vtks)} sets (PID {pid}).")
                except Exception as exc:
                    st.error(str(exc))
            if quality_actions[1].button(
                "Generar estudio completo de calidad",
                key=f"open-exp-quality-study-{selected.name}",
                disabled=not check_mesh_ok,
                help=(
                    "Calcula las distribuciones por intervalos y las tablas PNG. "
                    "Es manual, se habilita tras checkMesh OK y puede tardar varios minutos."
                ),
            ):
                start_job(
                    "open_experimental_mesh_quality_study",
                    [
                        str(sys.executable),
                        str(root / "CFD_2D/scripts/ramair_2d_open_experimental_mesh.py"),
                        "--project-root", str(root),
                        "--quality-study", selected.name,
                    ],
                )
            previews = [
                (selected / "mesh_preview_airfoil.png", "Entorno del perfil"),
                (selected / "mesh_preview_inlet.png", "Interfase del inlet"),
                (selected / "mesh_preview_te.png", "Trailing edge"),
            ]
            preview_columns = st.columns(3)
            for index, (image, caption) in enumerate(previews):
                if image.is_file():
                    preview_columns[index].image(str(image), caption=caption)
            quality_tables = sorted((selected / "quality_distributions").glob("quality_table_*.png"))
            if quality_tables:
                with st.expander("Distribuciones de calidad por intervalos", expanded=False):
                    st.caption(
                        "Distribuciones reconstruidas desde MSH2: skewness, no ortogonalidad, peso y "
                        "ratio se reducen al peor valor adyacente de cada celda. Los extremos y el "
                        "PASS/FAIL exactos siguen siendo los emitidos por checkMesh sobre polyMesh. "
                        "El aspect ratio se separa entre hexas de capa límite y prismas triangulares."
                    )
                    st.info(
                        "La skewness de checkMesh no es la skewness equiangular normalizada de ICEM: "
                        "mide el desplazamiento del centro de cara respecto a la línea entre centros "
                        "de celda y no está limitada a 1. OpenFOAM 14 usa por defecto 4 para caras "
                        "internas y 20 para caras de contorno. En aspect ratio se admite 1000-1500 "
                        "solo en la capa límite alineada; en el volumen no estructurado se revisan "
                        "por separado los intervalos 20-50."
                    )
                    for start in range(0, len(quality_tables), 2):
                        columns = st.columns(2)
                        for column, image in zip(columns, quality_tables[start : start + 2]):
                            column.image(str(image), caption=image.stem.replace("quality_table_", ""))
            if not check_mesh_ok:
                log_text = (selected / "log.checkMesh").read_text(
                    encoding="utf-8", errors="replace"
                ) if (selected / "log.checkMesh").is_file() else ""
                with st.expander("Por qué falla checkMesh y qué modificar", expanded=True):
                    for title, explanation in _checkmesh_guidance(report, log_text):
                        st.markdown(f"**{title}.** {explanation}")
            log_path = selected / "log.checkMesh"
            with st.expander("Log de checkMesh", expanded=False):
                st.code(
                    log_path.read_text(encoding="utf-8", errors="replace")
                    if log_path.is_file() else "No se ha generado log.checkMesh.",
                    language="text",
                )
            with st.expander("Informe completo de la malla seleccionada"):
                st.json(report)
        else:
            st.warning("Todavía no existe una revisión experimental generada.")

        config = _read_json(draft_path, {}) or _default_config(root)
        with st.expander("Generación y parámetros editables", expanded=not revisions):
            st.caption(
                "Puedes partir de los defaults o cargar arriba una revisión. Guardar modifica solo el "
                "borrador; generar crea una revisión nueva o archiva la anterior del mismo nombre."
            )
            geometry = dict(config.get("geometry") or {})
            geometry_cols = st.columns(2)
            open_variants = _geometry_variants(root, open_only=True)
            base_variants = _geometry_variants(root, open_only=False)
            current_open = str(geometry.get("open_variant", "open_ramair_validation_1m"))
            current_base = str(geometry.get("base_variant", "reference_uncut_validation_1m"))
            if current_open not in open_variants:
                open_variants.insert(0, current_open)
            if current_base not in base_variants:
                base_variants.insert(0, current_base)
            geometry["open_variant"] = geometry_cols[0].selectbox(
                "Geometría abierta",
                open_variants,
                index=open_variants.index(current_open),
                help="Perfil cortado que define la pared física y las posiciones exactas de los labios.",
            )
            geometry["base_variant"] = geometry_cols[1].selectbox(
                "Perfil cerrado base",
                base_variants,
                index=base_variants.index(current_base),
                help=(
                    "Perfil sin corte del que procede la geometría abierta. Su arco original entre "
                    "los labios guía la BL del inlet y se audita contra la pared retenida."
                ),
            )
            config["geometry"] = geometry
            bl_tab, external_tab, internal_tab = st.tabs(
                ["Capa límite", "Volumen externo", "Volumen interno"]
            )
            boundary = dict(config.get("boundary_layer") or {})
            external = dict(config.get("external_volume") or {})
            internal = dict(config.get("internal_volume") or {})
            with bl_tab:
                common = st.columns(3)
                boundary["distribution_mode"] = common[0].selectbox(
                    "Distribución normal", ["beta_law", "geometric"],
                    index=0 if boundary.get("distribution_mode", "beta_law") == "beta_law" else 1,
                    format_func=lambda value: "Ley Beta (capas fijadas)" if value == "beta_law" else "Progresión geométrica (GR fijado)",
                    help="Beta calcula internamente el coeficiente; geométrica calcula cuántas capas cubren delta99 por GR.",
                )
                boundary["target_y_plus"] = common[1].number_input(
                    "y+ objetivo", min_value=0.1, max_value=5.0,
                    value=float(boundary.get("target_y_plus", 2.0 / 3.0)), format="%.4f",
                    help=("Calcula la distancia de pared desde y+ y fija como altura geométrica y1 el doble: "
                          "OpenFOAM almacena las variables en el centro de cada volumen finito."),
                )
                boundary["thickness_safety_factor"] = common[2].number_input(
                    "Factor de seguridad de espesor", min_value=1.0, max_value=3.0,
                    value=float(boundary.get("thickness_safety_factor", 1.20)), format="%.2f",
                    help="Multiplica delta99=0.37c/Re_c^(1/5) para fijar el espesor normal objetivo.",
                )
                law_cols = st.columns(3)
                if boundary["distribution_mode"] == "beta_law":
                    boundary["layers"] = law_cols[0].number_input(
                        "Capas", min_value=5, max_value=150, value=int(boundary.get("layers", 75)),
                        help="Junto con y1 y el espesor objetivo determina Beta por bisección.",
                    )
                else:
                    boundary["growth_rate"] = law_cols[0].number_input(
                        "Growth rate", min_value=1.0, max_value=1.3,
                        value=float(boundary.get("growth_rate", 1.1)), format="%.3f",
                        help="El número entero mínimo de capas se calcula para cubrir el espesor objetivo.",
                    )
                boundary.pop("beta_coefficient", None)
                boundary.pop("total_thickness_chord", None)
                boundary["lip_fan_elements"] = law_cols[1].number_input(
                    "Elementos de fan", min_value=0, max_value=30,
                    value=int(boundary.get("lip_fan_elements", 0)),
                    help=(
                        "0 desactiva el fan. Los valores positivos añaden elementos radiales "
                        "en los labios como alternativa experimental; no cambian la curva del inlet."
                    ),
                )
                boundary["continue_over_base_inlet"] = law_cols[2].toggle(
                    "Continuar por inlet base", value=bool(boundary.get("continue_over_base_inlet", True)),
                    help=(
                        "Incluye en CurvesList la spline del perfil base sin corte. Así la BL exterior "
                        "continúa alrededor de los labios del perfil abierto; la curva del inlet sigue "
                        "siendo una interfaz de fluido y no una pared física."
                    ),
                )
                # Legacy inlet_y1_* keys are intentionally ignored.  A bounded
                # factor 1/2/4/8 study produced identical node coordinates and
                # checkMesh metrics: Gmsh's transfinite inlet spline overrides
                # the point-wise BoundaryLayer SizesList in this topology.
                boundary.pop("inlet_y1_transition_enabled", None)
                boundary.pop("inlet_y1_factor", None)
                boundary.pop("inlet_y1_transition_fraction", None)
                diagnostic_config = dict(config)
                diagnostic_config["boundary_layer"] = boundary
                try:
                    diagnostic = flat_plate_first_height(diagnostic_config)
                    diagnostics = st.columns(5)
                    diagnostics[0].metric("y pared-centro", f"{1e6 * diagnostic['first_cell_centre_distance_m']:.3f} µm")
                    diagnostics[1].metric("Altura y1 Gmsh", f"{1e6 * diagnostic['first_cell_height_m']:.3f} µm")
                    diagnostics[2].metric("Espesor BL", f"{diagnostic['total_thickness_m']:.6f} m")
                    diagnostics[3].metric("Capas efectivas", str(diagnostic["layers"]))
                    diagnostics[4].metric(
                        "Beta calculado" if boundary["distribution_mode"] == "beta_law" else "GR",
                        f"{diagnostic['beta_calculated']:.8f}" if diagnostic["beta_calculated"] is not None else f"{diagnostic['growth_rate']:.4f}",
                    )
                except (ValueError, KeyError) as exc:
                    st.error(f"Configuración normal de BL incompatible: {exc}")
                st.caption(
                    "La discretización longitudinal ya no usa cotas tangenciales mínimo/máximo: "
                    "cada tramo queda definido inequívocamente por sus divisiones y su ley Bump. "
                    "Las claves antiguas se conservan solo para reproducir revisiones legadas."
                )
                st.markdown("#### Discretización tangencial de pared")
                tangential_method = st.selectbox(
                    "Método tangencial",
                    ["four_bumps", "bump_split_progression"],
                    index=(
                        1 if boundary.get("tangential_distribution_method")
                        == "bump_split_progression" else 0
                    ),
                    format_func=lambda value: (
                        "4 Bumps" if value == "four_bumps" else "Bump + Split Progression"
                    ),
                    key="open-exp-tangential-method",
                    help=(
                        "4 Bumps conserva la topología actual. Split Progression mantiene Bump "
                        "en inlet/TE y usa cuatro progresiones orientadas desde el extremo fino al midpoint."
                    ),
                )
                boundary["tangential_distribution_method"] = tangential_method
                automatic_widget_key = "open-exp-automatic-bump-matching"
                previous_automatic = bool(st.session_state.get(
                    "open-exp-previous-automatic",
                    boundary.get("automatic_bump_matching", False),
                ))
                automatic_loaded = st.toggle(
                    "Automatic",
                    value=bool(boundary.get("automatic_bump_matching", False)),
                    key=automatic_widget_key,
                    help=(
                        "Calcula Bump independientes para TE, pared superior, arco exacto del inlet "
                        "del perfil base y pared inferior. Desactivado permite editar aquí los "
                        "cuatro coeficientes, manteniendo las mismas divisiones por tramo."
                    ),
                )
                boundary["automatic_bump_matching"] = automatic_loaded
                automatic = automatic_loaded
                switched_to_manual = previous_automatic and not automatic
                st.session_state["open-exp-previous-automatic"] = automatic
                boundary["te_segment_early_start_enabled"] = st.toggle(
                    "Adelantar el inicio del segmento TE",
                    value=bool(boundary.get("te_segment_early_start_enabled", False)),
                    disabled=(not automatic or tangential_method == "bump_split_progression"),
                    help=(
                        "Solo afecta al matching automático: incorpora al segmento TE la parte de "
                        "intradós y extradós situada por detrás del x/c indicado. La geometría no "
                        "cambia; cambia qué arco comparte la ley Bump del cierre redondeado."
                    ),
                )
                if tangential_method == "bump_split_progression":
                    boundary["te_segment_early_start_enabled"] = False
                    boundary["split_progression_midpoint_x_chord"] = st.number_input(
                        "Punto de división de cuerpos [x/c]", 0.20, 0.80,
                        float(boundary.get("split_progression_midpoint_x_chord", 0.50)),
                        format="%.3f", key="open-exp-split-midpoint",
                        help="Selecciona el punto existente más cercano, sin alterar el contorno compartido.",
                    )
                if automatic and boundary["te_segment_early_start_enabled"]:
                    boundary["te_segment_start_x_over_c"] = st.number_input(
                        "Inicio del segmento TE [x/c]", min_value=0.50, max_value=0.999,
                        value=float(boundary.get("te_segment_start_x_over_c", 0.98)),
                        format="%.4f",
                        help=(
                            "0.98 incluye en el segmento TE todo el contorno con x/c >= 0.98. "
                            "Reducirlo inicia antes la concentración progresiva."
                        ),
                    )
                divisions = dict(boundary.get("segment_divisions") or {})
                div_cols = st.columns(4)
                segments = ("te", "upper", "leading_or_inlet", "lower")
                segment_labels = ("TE", "Pared superior", "Inlet base", "Pared inferior")
                defaults = (24, 320, 140, 320)
                for column, segment, label, default in zip(div_cols, segments, segment_labels, defaults):
                    divisions[segment] = column.number_input(
                        f"Divisiones {label}", 4, 5000,
                        int(divisions.get(segment, default)),
                    )
                boundary["segment_divisions"] = divisions
                gate_cols = st.columns(2)
                boundary["bump_maximum_growth_ratio"] = gate_cols[0].number_input(
                    "GR tangencial máximo", 1.001, 2.0,
                    float(boundary.get("bump_maximum_growth_ratio", 1.10)), format="%.3f",
                )
                boundary["bump_maximum_size_percent_chord"] = gate_cols[1].number_input(
                    "hmax tangencial [%c]", 0.01, 10.0,
                    float(boundary.get("bump_maximum_size_percent_chord", 1.0)), format="%.3f",
                )
                matching_cache_key = "open-exp-bump-matching-cache"
                split_cache_key = "open-exp-split-progression-cache"
                recalculate = st.button(
                    "Aplicar cambios y recalcular matching",
                    key="open-exp-recalculate-bump",
                    help="Permite editar varias divisiones y límites antes de resolver los cuatro Bump.",
                )
                if automatic:
                    diagnostic_config = dict(config)
                    diagnostic_config["boundary_layer"] = boundary
                    try:
                        detected = load_geometry(root, diagnostic_config)
                        wall = detected["wall"]
                        upper_end = int(detected["upper_end_index"])
                        cap_end = int(detected["cap_end_index"])
                        length = lambda points: float(
                            ((((points[1:] - points[:-1]) ** 2).sum(axis=1)) ** 0.5).sum()
                        )
                        upper_start, lower_end = upper_end, cap_end
                        if boundary.get("te_segment_early_start_enabled", False):
                            x_start = float(detected["chord"]) * float(
                                boundary.get("te_segment_start_x_over_c", 0.98)
                            )
                            upper_start = int(abs(wall[:upper_end, 0] - x_start).argmin())
                            lower_end = cap_end + int(abs(wall[cap_end:, 0] - x_start).argmin())
                        matching = match_four_segment_bumps(
                            {
                                "upper": length(wall[:upper_start + 1]),
                                "te": length(wall[upper_start:lower_end + 1]),
                                "lower": length(wall[lower_end:]),
                                "leading_or_inlet": length(detected["inlet"]),
                            },
                            divisions,
                            chord=float(detected["chord"]),
                            maximum_growth_ratio=float(boundary["bump_maximum_growth_ratio"]),
                            maximum_size_percent_chord=float(boundary["bump_maximum_size_percent_chord"]),
                        )
                        st.session_state[matching_cache_key] = matching
                        boundary["manual_bump_coefficients"] = dict(matching["coefficients"])
                        if tangential_method == "bump_split_progression":
                            midpoint_x = float(detected["chord"]) * float(
                                boundary["split_progression_midpoint_x_chord"]
                            )
                            upper_index = min(upper_end - 3, max(3, int(
                                abs(wall[:upper_end, 0] - midpoint_x).argmin()
                            )))
                            lower_index = cap_end + int(
                                abs(wall[cap_end:, 0] - midpoint_x).argmin()
                            )
                            lower_index = min(len(wall) - 4, max(cap_end + 3, lower_index))
                            split_matching = automatic_split_progression(
                                half_lengths={
                                    "upper": {
                                        "leading_or_inlet": length(wall[:upper_index + 1]),
                                        "te": length(wall[upper_index:upper_end + 1]),
                                    },
                                    "lower": {
                                        "leading_or_inlet": length(wall[lower_index:]),
                                        "te": length(wall[cap_end:lower_index + 1]),
                                    },
                                },
                                body_divisions={
                                    "upper": int(divisions["upper"]),
                                    "lower": int(divisions["lower"]),
                                },
                                curved_lengths={
                                    "leading_or_inlet": length(detected["inlet"]),
                                    "te": length(wall[upper_end:cap_end + 1]),
                                },
                                curved_divisions={
                                    "leading_or_inlet": int(divisions["leading_or_inlet"]),
                                    "te": int(divisions["te"]),
                                },
                                curved_bumps={
                                    name: float(matching["coefficients"][name])
                                    for name in ("leading_or_inlet", "te")
                                },
                                chord=float(detected["chord"]),
                                maximum_growth_ratio=float(boundary["bump_maximum_growth_ratio"]),
                                maximum_size_percent_chord=float(
                                    boundary["bump_maximum_size_percent_chord"]
                                ),
                            )
                            st.session_state[split_cache_key] = split_matching
                            boundary["manual_split_progression"] = {
                                "split_divisions": dict(split_matching["split_divisions"]),
                                "progression_coefficients": dict(
                                    split_matching["progression_coefficients"]
                                ),
                            }
                            metrics = st.columns(4)
                            metrics[0].metric("GR máximo", f"{split_matching['maximum_growth_ratio']:.5f}")
                            metrics[1].metric("hmax real", f"{split_matching['maximum_size_percent_chord']:.4f}%c")
                            metrics[2].metric("Mismatch midpoint", f"{split_matching['maximum_midpoint_mismatch_percent']:.3f}%")
                            metrics[3].metric("Estado", split_matching["status"])
                            st.dataframe([
                                {
                                    "tramo": key,
                                    "N": split_matching["split_divisions"][key],
                                    "Progression": split_matching["progression_coefficients"][key],
                                }
                                for key in split_matching["split_divisions"]
                            ], use_container_width=True, hide_index=True)
                            if split_matching["warnings"]:
                                st.warning("\n".join(split_matching["warnings"]))
                        else:
                            metrics = st.columns(5)
                            metrics[0].metric("hJ", f"{matching['junction_size_m']:.6g} m")
                            for column, segment in zip(metrics[1:], segments):
                                column.metric(f"Bump {segment}", f"{matching['coefficients'][segment]:.7g}")
                            if matching["warnings"]:
                                st.warning("\n".join(matching["warnings"]))
                            else:
                                st.success("Matching compatible con GR y hmax.")
                    except Exception as exc:
                        st.error(f"Matching incompatible: {exc}")
                else:
                    boundary["manual_four_segment_bump_enabled"] = True
                    manual = dict(boundary.get("manual_bump_coefficients") or {})
                    cached = st.session_state.get(matching_cache_key) or {}
                    cached_coefficients = dict(cached.get("coefficients") or {})
                    selected_manual = (
                        (("te", "TE"), ("leading_or_inlet", "Inlet base"))
                        if tangential_method == "bump_split_progression"
                        else tuple(zip(segments, segment_labels))
                    )
                    manual_cols = st.columns(len(selected_manual))
                    for column, (segment, label) in zip(manual_cols, selected_manual):
                        initial = (
                            cached_coefficients.get(segment, manual.get(segment, 1.0))
                            if switched_to_manual else
                            manual.get(segment, cached_coefficients.get(segment, 1.0))
                        )
                        manual[segment] = column.number_input(
                            f"Bump manual {label}", 0.001, 100.0,
                            float(initial),
                            format="%.7f",
                        )
                    boundary["manual_bump_coefficients"] = manual
                    if tangential_method == "bump_split_progression":
                        manual_split = dict(boundary.get("manual_split_progression") or {})
                        cached_split = st.session_state.get(split_cache_key) or {}
                        split_divisions = dict(
                            (cached_split.get("split_divisions") if switched_to_manual else None)
                            or manual_split.get("split_divisions")
                            or cached_split.get("split_divisions") or {}
                        )
                        progression = dict(
                            (cached_split.get("progression_coefficients") if switched_to_manual else None)
                            or manual_split.get("progression_coefficients")
                            or cached_split.get("progression_coefficients") or {}
                        )
                        keys = (
                            "upper_leading_or_inlet", "upper_te",
                            "lower_leading_or_inlet", "lower_te",
                        )
                        ncols = st.columns(4)
                        rcols = st.columns(4)
                        for key, ncol, rcol in zip(keys, ncols, rcols):
                            side = key.split("_", 1)[0]
                            split_divisions[key] = ncol.number_input(
                                f"N {key}", 2, 5000,
                                int(split_divisions.get(key, max(2, divisions[side] // 2))),
                                key=f"open-exp-split-n-{key}",
                            )
                            progression[key] = rcol.number_input(
                                f"Progression {key}", 1.0, 2.0,
                                float(progression.get(key, 1.02)), format="%.8f",
                                key=f"open-exp-split-r-{key}",
                            )
                        boundary["manual_split_progression"] = {
                            "split_divisions": split_divisions,
                            "progression_coefficients": progression,
                        }
                        try:
                            diagnostic_config = dict(config)
                            diagnostic_config["boundary_layer"] = boundary
                            detected = load_geometry(root, diagnostic_config)
                            wall = detected["wall"]
                            upper_end = int(detected["upper_end_index"])
                            cap_end = int(detected["cap_end_index"])
                            midpoint_x = float(detected["chord"]) * float(
                                boundary["split_progression_midpoint_x_chord"]
                            )
                            upper_index = min(upper_end - 3, max(3, int(
                                abs(wall[:upper_end, 0] - midpoint_x).argmin()
                            )))
                            lower_index = cap_end + int(
                                abs(wall[cap_end:, 0] - midpoint_x).argmin()
                            )
                            lower_index = min(len(wall) - 4, max(cap_end + 3, lower_index))
                            length = lambda points: float(
                                ((((points[1:] - points[:-1]) ** 2).sum(axis=1)) ** 0.5).sum()
                            )
                            curved_lengths = {
                                "leading_or_inlet": length(detected["inlet"]),
                                "te": length(wall[upper_end:cap_end + 1]),
                            }
                            endpoint_sizes = {
                                name: float(bump_cell_sizes(
                                    manual[name], curved_lengths[name], int(divisions[name])
                                )[0])
                                for name in curved_lengths
                            }
                            manual_diagnostic = evaluate_manual_split_progression(
                                half_lengths={
                                    "upper": {
                                        "leading_or_inlet": length(wall[:upper_index + 1]),
                                        "te": length(wall[upper_index:upper_end + 1]),
                                    },
                                    "lower": {
                                        "leading_or_inlet": length(wall[lower_index:]),
                                        "te": length(wall[cap_end:lower_index + 1]),
                                    },
                                },
                                split_divisions=split_divisions,
                                progression_coefficients=progression,
                                endpoint_sizes=endpoint_sizes,
                                chord=float(detected["chord"]),
                                maximum_growth_ratio=float(boundary["bump_maximum_growth_ratio"]),
                                maximum_size_percent_chord=float(boundary["bump_maximum_size_percent_chord"]),
                            )
                            monitor = st.columns(4)
                            monitor[0].metric("GR máximo", f"{manual_diagnostic['maximum_growth_ratio']:.5f}")
                            monitor[1].metric("hmax real", f"{manual_diagnostic['maximum_size_percent_chord']:.4f}%c")
                            monitor[2].metric("Mismatch midpoint", f"{manual_diagnostic['maximum_midpoint_mismatch_percent']:.3f}%")
                            monitor[3].metric("Estado", manual_diagnostic["status"])
                            if manual_diagnostic["warnings"]:
                                st.warning("\n".join(manual_diagnostic["warnings"]))
                        except Exception as exc:
                            st.error(f"Diagnóstico manual incompatible: {exc}")
                    else:
                        st.caption(
                            "Modo manual por tramos: estos cuatro coeficientes y las divisiones de "
                            "arriba son la única fuente de discretización. Los checks GR/hmax son "
                            "diagnósticos y no alteran los valores introducidos."
                        )
                with st.expander("Unión con el contorno base sin corte", expanded=False):
                    st.caption(
                        "La BL del inlet usa el contorno del perfil base sin corte. Como el labio "
                        "del perfil abierto no coincide exactamente con un nodo del perfil base, "
                        "se inserta un puente C1 corto y limitado por las tangentes locales."
                    )
                    boundary["prefer_exact_base_inlet"] = st.toggle(
                        "Intentar conservar el LE exacto del perfil base",
                        value=bool(boundary.get("prefer_exact_base_inlet", True)),
                        help=(
                            "Primero conserva muestras del perfil sin corte a ambos lados del LE. "
                            "El código audita inversión de curvatura y la posición del mínimo x. "
                            "Si los labios cortados hacen imposible una unión C1 convexa, selecciona "
                            "automáticamente el cierre convexo acotado y registra el motivo."
                        ),
                    )
                    boundary["base_inlet_blend_length_chord"] = st.number_input(
                        "Longitud del puente C1 [c]", min_value=0.005, max_value=0.20,
                        value=float(boundary.get("base_inlet_blend_length_chord", 0.035)),
                        format="%.4f",
                        help=(
                            "Distancia de perfil base conservada alrededor de cada labio. Un valor "
                            "pequeño sigue más fielmente el perfil base; un valor grande suaviza más "
                            "pero puede modificar demasiado la zona del LE."
                        ),
                    )
                    boundary["base_inlet_tangent_scale"] = st.number_input(
                        "Escala de tangente local", min_value=0.05, max_value=1.50,
                        value=float(boundary.get("base_inlet_tangent_scale", 1.0)),
                        format="%.3f",
                        help=(
                            "Multiplica la tangente local medida en el perfil cortado y en el perfil "
                            "base. El código limita además el manejador a 75%% del puente para evitar "
                            "inflexiones o curvas hacia dentro. 1.0 conserva la magnitud local."
                        ),
                    )
                    boundary["leading_edge_curvature_fraction"] = st.number_input(
                        "Fracción de curvatura LE", min_value=0.05, max_value=0.90,
                        value=float(boundary.get("leading_edge_curvature_fraction", 0.50)),
                        format="%.2f",
                        help=(
                            "Limita cuánto arco lip-to-LE puede consumir cada conector C1 cuando los "
                            "labios no coinciden exactamente con el perfil base. Con coincidencia exacta "
                            "no altera la curva; valores menores preservan una fracción mayor del LE real."
                        ),
                    )
            with external_tab:
                external["automatic_extend_enabled"] = st.toggle(
                    "Control automático exterior Gmsh Extend",
                    value=bool(external.get("automatic_extend_enabled", False)),
                    help=(
                        "Hereda los tamaños variables de las curvas transfinite Bump y los propaga "
                        "por el volumen exterior. Gmsh evalúa el campo antes de crear la última fila "
                        "de BL, por lo que usa las curvas que gobiernan exactamente sus anchos."
                    ),
                )
                cols = st.columns(4)
                external["domain_radius_chord"] = cols[0].number_input(
                    "Radio del dominio [c]", min_value=10.0,
                    max_value=200.0, value=float(external.get("domain_radius_chord", 50.0)),
                    help="Radio del dominio circular medido desde el centro geométrico, en cuerdas.",
                )
                if external["automatic_extend_enabled"]:
                    cols[1].caption("El primer tamaño se hereda por segmento mediante Extend.")
                else:
                    external["interface_size_mode"] = cols[1].selectbox(
                        "Primer triángulo tras BL",
                        ["tangential_match", "fixed"],
                        index=0 if external.get("interface_size_mode", "tangential_match") == "tangential_match" else 1,
                        format_func=lambda value: "Automático desde discretización tangencial" if value == "tangential_match" else "Tamaño fijo",
                    )
                external["farfield_size_chord"] = cols[2].number_input(
                    "Tamaño en farfield [c]", min_value=0.01, max_value=200.0,
                    value=float(external.get("farfield_size_chord", 4.0)), format="%.3f",
                    help=(
                        "Tamaño máximo objetivo en el límite del dominio. La ley radial crece "
                        "continuamente hasta este valor; ampliar este número reduce celdas lejanas "
                        "sin cambiar la resolución junto al perfil."
                    ),
                )
                external["radial_growth_rate"] = cols[3].number_input(
                    "Crecimiento radial máximo", min_value=0.005, max_value=0.50,
                    value=min(0.50, max(0.005, float(external.get("radial_growth_rate", 0.12)))), format="%.2f",
                    help=("Pendiente de la ley h(d)=h_interfaz+g·d. Con g<=0.20, el tamaño "
                          "aumenta de forma conservadora; valores mayores aceleran la transición "
                          "y ahorran celdas, pero pueden empeorar la calidad. El valor no puede "
                          "saltarse el tamaño del farfield porque la ley se evalúa de forma continua."),
                )
                if external["automatic_extend_enabled"]:
                    extend_cols = st.columns(3)
                    external["extend_distance_max_chord"] = extend_cols[0].number_input(
                        "Extend DistMax exterior [c]", 0.001, 500.0,
                        float(external.get("extend_distance_max_chord", external["domain_radius_chord"])), format="%.3f",
                        help=(
                            "Distancia desde perfil/inlet hasta alcanzar SizeMax. Por defecto coincide "
                            "con el radio del dominio y el crecimiento continúa hasta farfield."
                        ),
                    )
                    external["extend_power"] = extend_cols[1].number_input(
                        "Extend Power exterior", 0.1, 10.0,
                        float(external.get("extend_power", 2.0)), format="%.2f",
                        help=(
                            "Moldea la ponderación con la distancia. Power=1 es neutro; valores >1 "
                            "abandonan antes el tamaño fino y valores <1 prolongan su influencia."
                        ),
                    )
                    external["extend_size_max_chord"] = extend_cols[2].number_input(
                        "Extend tamaño máximo exterior [c]", 0.001, 500.0,
                        float(external.get("extend_size_max_chord", external["farfield_size_chord"])),
                        format="%.3f",
                        help="Tamaño alcanzado en DistMax; el default coincide con el tamaño de farfield.",
                    )
                    guard_cols = st.columns(2)
                    external["extend_interface_guard_enabled"] = guard_cols[0].toggle(
                        "Casar Extend con la última capa BL",
                        value=bool(external.get("extend_interface_guard_enabled", True)),
                        help=(
                            "Limita la primera fila triangular al tamaño tangencial de interfaz y "
                            "la libera mediante una transición sigmoidal después del espesor BL."
                        ),
                    )
                    if external["extend_interface_guard_enabled"]:
                        external["extend_interface_transition_chord"] = guard_cols[1].number_input(
                            "Transición inicial Extend [c]", 0.002, 2.0,
                            float(external.get("extend_interface_transition_chord", 0.10)),
                            format="%.4f",
                            help="Longitud, desde la cara exterior de BL, para liberar la ley Extend.",
                        )
                interface_cols = st.columns(2)
                external["interface_size_chord"] = interface_cols[0].number_input(
                    "Límite del primer triángulo [c]", min_value=0.0000001, max_value=2.0,
                    value=float(external.get("interface_size_chord", 0.00045)), format="%.6f",
                    disabled=external["automatic_extend_enabled"], help=(
                        "Tamaño objetivo de la primera fila triangular después de la BL. Se refiere "
                        "al ancho tangencial de la celda, no a la altura y1; en modo automático es "
                        "un valor informativo y solo se aplica en modo Tamaño fijo. En modo automático "
                        "se calcula a partir del espaciado tangencial real de pared e inlet."
                    ),
                )
                external["interface_tangential_factor"] = interface_cols[1].number_input(
                    "Factor sobre espaciado tangencial", min_value=0.02, max_value=10.0,
                    value=float(external.get("interface_tangential_factor", 1.25)), format="%.2f",
                    disabled=external["automatic_extend_enabled"], help=(
                        "Multiplica el menor espaciado medio de inlet y pared para casar el ancho "
                        "de la primera celda exterior con la BL. 1.0 hace match directo; valores "
                        "menores refinan esa primera transición y valores mayores la relajan."
                    ),
                )
                st.caption(
                    "Gmsh no ofrece nCellsBetweenLevels para Delaunay. Se usa una ley continua "
                    "h(d)=h0+g·d: al avanzar una celda local, g=0.12 limita aproximadamente el "
                    "crecimiento al 12 %, y el mayor farfield retrasa la meseta hasta el exterior."
                )
                execution = dict(config.get("execution") or {})
                numeric_cols = st.columns(2)
                external["mesh_algorithm"] = numeric_cols[0].selectbox(
                    "Algoritmo 2D",
                    [6, 5],
                    index=0 if int(external.get("mesh_algorithm", 6)) == 6 else 1,
                    format_func=lambda value: "Frontal-Delaunay (6)" if value == 6 else "Delaunay (5)",
                )
                execution["mesh_smoothing"] = numeric_cols[1].number_input(
                    "Mesh.Smoothing", 0, 10,
                    int(execution.get("mesh_smoothing", 1)),
                    help="Número de pasadas de suavizado Gmsh; 1 es el default experimental.",
                )
                execution["post_generation_optimization"] = st.selectbox(
                    "Optimización 2D posterior (experimental)",
                    ["off", "laplace2d", "relocate2d", "laplace2d_then_relocate2d"],
                    index=["off", "laplace2d", "relocate2d", "laplace2d_then_relocate2d"].index(
                        execution.get("post_generation_optimization", "off")
                        if execution.get("post_generation_optimization", "off") in {"off", "laplace2d", "relocate2d", "laplace2d_then_relocate2d"}
                        else "off"
                    ),
                    format_func=lambda value: {
                        "off": "Desactivada", "laplace2d": "Laplace2D",
                        "relocate2d": "Relocate2D",
                        "laplace2d_then_relocate2d": "Laplace2D → Relocate2D",
                    }[value],
                    help=(
                        "Mueve nodos después del mallado 2D y antes de exportar/checkMesh. "
                        "No es estándar y debe probarse siempre en una revisión nueva."
                    ),
                )
                if execution["post_generation_optimization"] != "off":
                    execution["post_generation_optimization_iterations"] = st.number_input(
                        "Iteraciones Laplace2D", 1, 50,
                        int(execution.get("post_generation_optimization_iterations", 5)),
                    )
                    if selected is not None:
                        execution["optimization_base_revision"] = selected.name
                config["execution"] = execution
            with internal_tab:
                internal["automatic_extend_enabled"] = st.toggle(
                    "Control automático interior Gmsh Extend",
                    value=bool(internal.get("automatic_extend_enabled", False)),
                    help=(
                        "Propaga de forma independiente los tamaños reales del inlet y de la pared "
                        "interna hacia el núcleo. Los refinamientos locales manuales de inlet, pared "
                        "y TE se conservan y se combinan con Extend mediante el tamaño más fino."
                    ),
                )
                if internal["automatic_extend_enabled"]:
                    extend_cols = st.columns(3)
                    internal["extend_distance_max_chord"] = extend_cols[0].number_input(
                        "Extend DistMax interior [c]", 0.0001, 2.0,
                        float(internal.get("extend_distance_max_chord", 0.10)), format="%.4f",
                        help=(
                            "Distancia desde inlet/pared interna hasta SizeMax. Reducirla acelera el "
                            "crecimiento y ahorra celdas, pero concentra la transición."
                        ),
                    )
                    internal["extend_power"] = extend_cols[1].number_input(
                        "Extend Power interior", 0.1, 10.0,
                        float(internal.get("extend_power", 2.5)), format="%.2f",
                        help=(
                            "Power >1 alcanza antes el tamaño de núcleo; Power <1 conserva más "
                            "refinamiento junto a las curvas fuente."
                        ),
                    )
                    internal["extend_size_max_chord"] = extend_cols[2].number_input(
                        "Extend tamaño máximo interior [c]", 0.0001, 1.0,
                        float(internal.get("extend_size_max_chord", max(0.015, internal.get("core_size_chord", 0.01)))),
                        format="%.5f",
                        help="Tamaño máximo de la cavidad; no altera la discretización conformal de la pared.",
                    )
                cols = st.columns(2)
                internal["inlet_size_factor"] = cols[0].number_input(
                    "Factor de ajuste al inlet", min_value=0.02, max_value=10.0,
                    value=float(internal.get("inlet_size_factor", 0.45)), format="%.2f",
                    help=(
                        "Multiplica el ancho tangencial medio del inlet para definir el tamaño mínimo "
                        "de los triángulos internos pegados a la interfase. No multiplica y1 ni el "
                        "espesor de la BL. 1.0 hace coincidir ambos anchos; valores menores refinan."
                    ),
                )
                internal["core_size_chord"] = cols[1].number_input(
                    "Tamaño del núcleo [c]", min_value=0.00005, max_value=0.5,
                    value=float(internal.get("core_size_chord", 0.010)), format="%.4f",
                    help=(
                        "Tamaño máximo permitido en el núcleo interno alejado de las paredes y del "
                        "inlet. Aumentarlo ahorra celdas en la cavidad; la transición se controla "
                        "con la distancia de transición, no cambia la discretización de la pared."
                    ),
                )
                st.info(
                    "En la topología de espesor cero las caras interna y externa comparten "
                    "exactamente los nodos de pared. Una discretización distinta rompería la "
                    "conformidad; el ahorro interior se controla con el bump común y con el "
                    "crecimiento de los triángulos hacia el núcleo."
                )
                distances = st.columns(2)
                internal["inlet_fine_distance_chord"] = distances[0].number_input(
                    "Zona fina tras inlet [c]", min_value=0.0001, max_value=0.25,
                    value=float(internal.get("inlet_fine_distance_chord", 0.025)), format="%.4f",
                    help=(
                        "Distancia desde el inlet en la que se mantiene el tamaño mínimo de los "
                        "triángulos interiores. Reducirla concentra el refinamiento junto a la "
                        "interfase y evita sobre-refinar toda la cavidad."
                    ),
                )
                internal["transition_distance_chord"] = distances[1].number_input(
                    "Transición interior [c]", min_value=0.001, max_value=1.0,
                    value=float(internal.get("transition_distance_chord", 0.10)), format="%.3f",
                    help=(
                        "Distancia hasta alcanzar el tamaño de núcleo. Un valor mayor hace crecer "
                        "los triángulos más gradualmente; un valor menor ahorra celdas pero crea un "
                        "cambio más concentrado."
                    ),
                )
                wall_cols = st.columns(4)
                internal["inner_wall_size_chord"] = wall_cols[0].number_input(
                    "Tamaño junto a pared interna [c]", min_value=0.000005, max_value=0.10,
                    value=float(internal.get("inner_wall_size_chord", 0.0012)), format="%.5f",
                    help=(
                        "Tamaño de los triángulos inmediatamente junto a la pared interna del perfil. "
                        "No crea BL interna; solo mejora la transición desde la pared sólida hacia "
                        "el fluido de la cavidad."
                    ),
                )
                internal["inner_wall_transition_distance_chord"] = wall_cols[1].number_input(
                    "Transición desde pared [c]", min_value=0.001, max_value=0.5,
                    value=float(internal.get("inner_wall_transition_distance_chord", 0.045)), format="%.3f",
                    help=(
                        "Distancia en la cavidad sobre la que el tamaño pasa de la pared interna al "
                        "tamaño del núcleo mediante Threshold sigmoidal. Aumentarla suaviza el cambio."
                    ),
                )
                internal["te_internal_size_chord"] = wall_cols[2].number_input(
                    "Tamaño TE interno [c]", min_value=0.000005, max_value=0.10,
                    value=float(internal.get("te_internal_size_chord", 0.0008)), format="%.5f",
                    help=(
                        "Tamaño local máximo alrededor del cierre curvo del TE dentro de la cavidad. "
                        "El campo identifica el segmento geométrico TE también cuando el cierre continuo "
                        "lo integra en una curva Bump; debe guardar relación con la separación disponible."
                    ),
                )
                internal["te_internal_transition_distance_chord"] = wall_cols[3].number_input(
                    "Transición TE interna [c]", min_value=0.001, max_value=0.3,
                    value=float(internal.get("te_internal_transition_distance_chord", 0.025)), format="%.3f",
                    help=(
                        "Radio de transición del refinamiento del TE interno hacia el núcleo. "
                        "Aumentarlo suaviza la expansión; reducirlo limita el refinamiento a la zona "
                        "curvada y ahorra celdas."
                    ),
                )
                st.info(
                    "La pared de espesor cero es un baffle conformal: sus dos caras deben conservar "
                    "la misma conectividad tangencial. Por eso no se permite una discretización distinta "
                    "en la cara interna; el ahorro se controla con nodos totales, bump y crecimiento del núcleo."
                )
            mode = st.radio(
                "Destino", ["new", "replace"], horizontal=True,
                format_func=lambda value: "Nueva revisión" if value == "new" else "Reemplazar nombre seleccionado",
                disabled=not revisions,
            )
            default_name = selected.name if mode == "replace" and selected else str(config.get("name", DEFAULT_NAME))
            revision_name = st.text_input("Nombre de revisión", value=default_name, disabled=mode == "replace")
            config["boundary_layer"] = boundary
            config["external_volume"] = external
            config["internal_volume"] = internal
            config["geometry"] = geometry
            config["name"] = selected.name if mode == "replace" and selected else revision_name
            action_cols = st.columns(3)
            if action_cols[0].button("Guardar borrador"):
                _write_json(draft_path, config)
                st.success(f"Borrador guardado: {draft_path}")
            if action_cols[1].button("Generar y comprobar", type="primary"):
                _write_json(draft_path, config)
                start_job(
                    "open_experimental_mesh_generate",
                    [str(sys.executable), str(root / "CFD_2D/scripts/ramair_2d_open_experimental_mesh.py"),
                     "--project-root", str(root), "--generate", "--config", str(draft_path),
                     "--name", str(config["name"]), "--check-mesh"],
                )
            if action_cols[2].button("Restaurar defaults"):
                _write_json(draft_path, _default_config(root))
                st.rerun()

        with st.expander("Comparar calidad de revisiones", expanded=False):
            render_mesh_quality_comparator(
                experiment, topology="open", key_prefix="open-experimental-quality-compare",
            )
