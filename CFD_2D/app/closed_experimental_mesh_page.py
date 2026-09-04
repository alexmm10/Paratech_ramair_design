"""Streamlit controls for the closed from-scratch experimental mesher."""
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
    split_polyline_at_x,
)
from ramair_2d_closed_experimental_mesh import (
    default_closed_config,
    load_closed_geometry,
)
from ramair_2d_open_experimental_mesh import flat_plate_first_height


EXPERIMENT_ID = "closed_reference_from_scratch"
StartJob = Callable[..., Any]


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _closed_geometry_variants(root: Path) -> list[str]:
    geometry_root = root / "CFD_2D/CFD_2D_inputs/geometry"
    values: list[str] = []
    for path in sorted(geometry_root.glob("*/profile_points.csv")):
        try:
            header = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()[0]
        except (OSError, IndexError):
            continue
        if "source_section" not in header or path.parent.name.startswith("open_"):
            continue
        values.append(path.parent.name)
    return values


def render_closed_experimental_mesh(
    root: Path,
    start_job: StartJob,
    open_mesh: Callable[[Path, Path, str], int],
) -> None:
    experiment = root / "CFD_2D/experimental_meshes" / EXPERIMENT_ID
    revisions_root = experiment / "revisions"
    revisions = sorted(
        [path for path in revisions_root.glob("*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    draft = experiment / "draft_config.json"
    if not draft.is_file():
        _write(draft, default_closed_config())
    st.info(
        "Topología cerrada: el interior del perfil no es fluido. La pared completa recibe una BL "
        "prismática; el exterior triangular reutiliza la misma ley radial continua del caso abierto."
    )
    selected = st.selectbox(
        "Revisión cerrada existente",
        [None, *revisions],
        format_func=lambda path: "Crear desde el borrador" if path is None else path.name,
        key="closed-exp-existing",
    )
    if selected is not None:
        report = _read(selected / "mesh_report.json", {}) or {}
        cols = st.columns(5)
        cols[0].metric("Estado", str(report.get("status") or "-"))
        cols[1].metric("checkMesh", str(report.get("checkMesh_status") or "-"))
        cols[2].metric("Celdas", f"{int(report.get('checkMesh_cell_count') or 0):,}")
        cols[3].metric("No ortogonalidad", f"{float(report.get('checkMesh_max_non_orthogonality_deg') or 0):.2f}°")
        cols[4].metric("Skewness", f"{float(report.get('checkMesh_max_skewness') or 0):.3f}")
        actions = st.columns(6)
        if actions[0].button("Abrir malla", key=f"closed-exp-open-{selected.name}"):
            open_mesh(root, selected / "mesh_final.msh", "windows_python")
        if actions[1].button("Cargar ajustes", key=f"closed-exp-load-{selected.name}"):
            _write(draft, _read(selected / "mesh_config.json", {}) or default_closed_config())
            st.rerun()
        if actions[2].button(
            "Aprobar", disabled=str(report.get("checkMesh_status")) != "OK",
            key=f"closed-exp-approve-{selected.name}",
        ):
            start_job(
                "closed_experimental_mesh_approve",
                [str(sys.executable), str(root / "CFD_2D/scripts/ramair_2d_closed_experimental_mesh.py"),
                 "--project-root", str(root), "--approve", selected.name],
            )
        if actions[3].button(
            "Estudio completo de calidad", disabled=str(report.get("checkMesh_status")) != "OK",
            key=f"closed-exp-quality-{selected.name}",
        ):
            start_job(
                "closed_experimental_mesh_quality_study",
                [str(sys.executable), str(root / "CFD_2D/scripts/ramair_2d_closed_experimental_mesh.py"),
                 "--project-root", str(root), "--quality-study", selected.name],
            )
        if actions[4].button("Abrir carpeta", key=f"closed-exp-folder-{selected.name}"):
            from workflow_backend import open_local_folder
            open_local_folder(selected)
        problem_vtks = sorted((selected / "checkMesh_problem_locations").glob("*.vtk"))
        problem_vtks += sorted(path for path in selected.glob("*.vtk") if path not in problem_vtks)
        if actions[5].button(
            "Abrir problemático en ParaView",
            disabled=not problem_vtks,
            key=f"closed-exp-problem-{selected.name}",
        ):
            from workflow_backend import open_checkmesh_problem_viewer
            try:
                open_checkmesh_problem_viewer(root, selected)
            except Exception as exc:
                st.error(str(exc))
        previews = st.columns(3)
        for column, filename, caption in zip(
            previews,
            ("mesh_preview_airfoil.png", "mesh_preview_inlet.png", "mesh_preview_te.png"),
            ("Perfil completo", "Leading edge", "Trailing edge"),
        ):
            path = selected / filename
            if path.is_file():
                column.image(str(path), caption=caption)
        log_path = selected / "log.checkMesh"
        with st.expander("Log de checkMesh", expanded=False):
            st.code(
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.is_file() else "No disponible",
                language="text",
            )
        quality_tables = sorted(
            (selected / "quality_distributions").glob("quality_table_*.png")
        )
        if quality_tables:
            with st.expander("Distribuciones de calidad por intervalos", expanded=False):
                st.caption(
                    "Caso cerrado: se muestran capa límite y volumen triangular exterior. "
                    "No existe ni se contabiliza un volumen triangular interno."
                )
                for start in range(0, len(quality_tables), 2):
                    table_columns = st.columns(2)
                    for column, image in zip(table_columns, quality_tables[start:start + 2]):
                        column.image(str(image), caption=image.stem.replace("quality_table_", ""))
        with st.expander("Informe completo", expanded=False):
            st.json(report)

    config = _read(draft, {}) or default_closed_config()
    boundary = dict(config.get("boundary_layer") or {})
    external = dict(config.get("external_volume") or {})
    geometry = dict(config.get("geometry") or {})
    with st.expander("Generar o modificar la malla cerrada", expanded=selected is None):
        closed_variants = _closed_geometry_variants(root)
        current_variant = str(
            geometry.get("closed_variant", "reference_uncut_validation_1m")
        )
        if current_variant not in closed_variants:
            closed_variants.insert(0, current_variant)
        geometry["closed_variant"] = st.selectbox(
            "Geometría cerrada",
            closed_variants,
            index=closed_variants.index(current_variant),
            help="Debe contener UPPER/LOWER y compartir la cuerda declarada en el caso CFD.",
            key="closed-exp-geometry-variant",
        )
        bl_tab, ext_tab = st.tabs(["Capa límite", "Volumen externo"])
        with bl_tab:
            cols = st.columns(3)
            boundary["distribution_mode"] = cols[0].selectbox(
                "Distribución normal", ["beta_law", "geometric"],
                index=0 if boundary.get("distribution_mode", "beta_law") == "beta_law" else 1,
                format_func=lambda value: "Ley Beta" if value == "beta_law" else "Progresión geométrica",
                key="closed-exp-distribution-mode",
            )
            boundary["target_y_plus"] = cols[1].number_input(
                "y+ objetivo", 0.1, 5.0, float(boundary.get("target_y_plus", 2 / 3)), format="%.4f",
                key="closed-exp-target-yplus",
            )
            boundary["thickness_safety_factor"] = cols[2].number_input(
                "Factor de espesor", 1.0, 3.0, float(boundary.get("thickness_safety_factor", 1.2)),
                key="closed-exp-thickness-factor",
            )
            law = st.columns(3)
            if boundary["distribution_mode"] == "beta_law":
                boundary["layers"] = law[0].number_input(
                    "Capas", 5, 150, int(boundary.get("layers", 75)), key="closed-exp-layers"
                )
            else:
                boundary["growth_rate"] = law[0].number_input(
                    "Growth rate", 1.0, 1.3, float(boundary.get("growth_rate", 1.1)), format="%.3f",
                    key="closed-exp-growth-rate",
                )
            diagnostic_config = dict(config)
            diagnostic_config["geometry"] = geometry
            diagnostic_config["boundary_layer"] = boundary
            try:
                diagnostic = flat_plate_first_height(diagnostic_config)
                law[1].metric("y1 Gmsh", f"{1e6 * diagnostic['first_cell_height_m']:.3f} µm")
                law[2].metric("Espesor BL", f"{diagnostic['total_thickness_m']:.5f} m")
            except Exception as exc:
                st.error(str(exc))
            st.caption(
                "La discretización longitudinal se define mediante divisiones y Bump por tramo. "
                "Las cotas tangenciales y los nodos globales legados permanecen en los JSON "
                "antiguos, pero no intervienen en este menú moderno."
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
                key="closed-exp-tangential-method",
                help=(
                    "4 Bumps conserva el método validado. Split Progression mantiene Bump en LE/TE "
                    "y divide intradós/extradós cerca de x/c=0.5 en cuatro progresiones fine-to-mid."
                ),
            )
            boundary["tangential_distribution_method"] = tangential_method
            automatic_widget_key = "closed-exp-auto-bump"
            previous_automatic = bool(st.session_state.get(
                "closed-exp-previous-automatic",
                boundary.get("automatic_bump_matching", True),
            ))
            automatic_loaded = st.toggle(
                "Automatic",
                value=bool(boundary.get("automatic_bump_matching", True)),
                key=automatic_widget_key,
                help=(
                    "Calcula cuatro leyes Bump independientes para TE, extradós, LE e intradós. "
                    "Desactivado conserva las divisiones y permite editar manualmente los cuatro "
                    "coeficientes en esta misma sección."
                ),
            )
            boundary["automatic_bump_matching"] = automatic_loaded
            automatic = automatic_loaded
            switched_to_manual = previous_automatic and not automatic
            st.session_state["closed-exp-previous-automatic"] = automatic
            boundary["te_segment_early_start_enabled"] = st.toggle(
                "Adelantar el inicio del segmento TE",
                value=bool(boundary.get("te_segment_early_start_enabled", False)),
                disabled=not automatic,
                help=(
                    "Agrupa con el cap redondeado la aproximación de intradós/extradós situada "
                    "por detrás del x/c elegido. Solo modifica la segmentación para Bump."
                ),
                key="closed-exp-te-early-enabled",
            )
            if tangential_method == "bump_split_progression":
                boundary["split_progression_midpoint_x_chord"] = st.number_input(
                    "Punto de división de cuerpos [x/c]", 0.20, 0.80,
                    float(boundary.get("split_progression_midpoint_x_chord", 0.50)),
                    format="%.3f", key="closed-exp-split-midpoint",
                    help="Se usa el nodo geométrico existente más próximo; no deforma el perfil.",
                )
            if automatic and boundary["te_segment_early_start_enabled"]:
                boundary["te_segment_start_x_over_c"] = st.number_input(
                    "Inicio del segmento TE [x/c]", 0.50, 0.999,
                    float(boundary.get("te_segment_start_x_over_c", 0.98)),
                    format="%.4f", key="closed-exp-te-start-xc",
                    help="0.98 incluye en la ley Bump del TE todo el contorno con x/c >= 0.98.",
                )
            boundary["leading_segment_extension_enabled"] = st.toggle(
                "Extender el segmento LE sobre la pared",
                value=bool(boundary.get("leading_segment_extension_enabled", False)),
                help=(
                    "Reserva una aproximación suave a ambos lados del LE. Es independiente "
                    "del segmento TE y no modifica las coordenadas del perfil."
                ),
                key="closed-exp-le-extension-enabled",
            )
            if boundary["leading_segment_extension_enabled"]:
                boundary["leading_segment_end_x_over_c"] = st.number_input(
                    "Fin del segmento LE [x/c]", 0.005, 0.30,
                    float(boundary.get("leading_segment_end_x_over_c", 0.05)),
                    format="%.4f", key="closed-exp-le-end-xc",
                    help="Longitud de pared reservada para la transición de tamaño desde el LE.",
                )
            divisions = dict(boundary.get("segment_divisions") or {})
            division_cols = st.columns(4)
            labels = {
                "te": "Divisiones TE", "upper": "Divisiones extradós",
                "leading_or_inlet": "Divisiones LE", "lower": "Divisiones intradós",
            }
            defaults = {"te": 22, "upper": 220, "leading_or_inlet": 120, "lower": 220}
            for column, segment in zip(division_cols, labels):
                divisions[segment] = column.number_input(
                    labels[segment], min_value=4, max_value=4000,
                    value=int(divisions.get(segment, defaults[segment])),
                    key=f"closed-exp-divisions-{segment}",
                )
            boundary["segment_divisions"] = divisions
            quality_cols = st.columns(3)
            boundary["bump_maximum_growth_ratio"] = quality_cols[0].number_input(
                "GR tangencial máximo", min_value=1.001, max_value=2.0,
                value=float(boundary.get("bump_maximum_growth_ratio", 1.10)), format="%.3f",
                key="closed-exp-bump-max-gr",
            )
            boundary["bump_maximum_size_percent_chord"] = quality_cols[1].number_input(
                "hmax tangencial [%c]", min_value=0.01, max_value=10.0,
                value=float(boundary.get("bump_maximum_size_percent_chord", 1.0)), format="%.3f",
                key="closed-exp-bump-max-size",
            )
            boundary["leading_edge_curvature_fraction"] = quality_cols[2].number_input(
                "Fracción de curvatura LE", min_value=0.02, max_value=0.95,
                value=float(boundary.get("leading_edge_curvature_fraction", 0.20)), format="%.3f",
                help="Expande el segmento LE hasta que la curvatura suavizada cae bajo esta fracción del pico.",
                key="closed-exp-le-curvature-fraction",
            )
            diagnostic_config = dict(config)
            diagnostic_config["geometry"] = geometry
            diagnostic_config["boundary_layer"] = boundary
            cache_key = "closed-exp-bump-matching-cache"
            recalculate = st.button(
                "Aplicar cambios y recalcular matching",
                key="closed-exp-recalculate-bump",
                help="Permite cambiar varias divisiones/límites antes de resolver los cuatro coeficientes.",
            )
            split_cache_key = "closed-exp-split-progression-cache"
            if automatic:
                try:
                    detected = load_closed_geometry(root, diagnostic_config)
                    key_map = {
                        "te": "te_cap", "upper": "upper_body",
                        "leading_or_inlet": "leading_edge", "lower": "lower_body",
                    }
                    lengths = {
                        segment: float(
                            (((detected[key][1:] - detected[key][:-1]) ** 2)
                             .sum(axis=1) ** 0.5).sum()
                        )
                        for segment, key in key_map.items()
                    }
                    matching = match_four_segment_bumps(
                        lengths, divisions,
                        chord=float(geometry.get("chord_m", 1.0)),
                        maximum_growth_ratio=float(boundary["bump_maximum_growth_ratio"]),
                        maximum_size_percent_chord=float(boundary["bump_maximum_size_percent_chord"]),
                    )
                    st.session_state[cache_key] = matching
                    boundary["manual_bump_coefficients"] = dict(matching["coefficients"])
                    if tangential_method == "bump_split_progression":
                        midpoint_x = float(detected["chord"]) * float(
                            boundary["split_progression_midpoint_x_chord"]
                        )
                        upper_te, upper_le_mid, _ = split_polyline_at_x(
                            detected["upper_body"], midpoint_x
                        )
                        lower_le_mid, lower_te, _ = split_polyline_at_x(
                            detected["lower_body"], midpoint_x
                        )
                        curve_length = lambda values: float(
                            ((((values[1:] - values[:-1]) ** 2).sum(axis=1)) ** 0.5).sum()
                        )
                        split_matching = automatic_split_progression(
                            half_lengths={
                                "upper": {
                                    "leading_or_inlet": curve_length(upper_le_mid),
                                    "te": curve_length(upper_te),
                                },
                                "lower": {
                                    "leading_or_inlet": curve_length(lower_le_mid),
                                    "te": curve_length(lower_te),
                                },
                            },
                            body_divisions={
                                "upper": int(divisions["upper"]),
                                "lower": int(divisions["lower"]),
                            },
                            curved_lengths={
                                "leading_or_inlet": lengths["leading_or_inlet"],
                                "te": lengths["te"],
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
                        bump_cols = st.columns(5)
                        bump_cols[0].metric("hJ", f"{matching['junction_size_m']:.6g} m")
                        for column, segment in zip(bump_cols[1:], labels):
                            column.metric(f"Bump {segment}", f"{matching['coefficients'][segment]:.7g}")
                        if matching["warnings"]:
                            st.warning("\n".join(matching["warnings"]))
                        else:
                            st.success("Matching recalculado: las cuatro interfaces comparten hJ.")
                except Exception as exc:
                    st.error(f"Matching incompatible: {exc}")
            else:
                manual = dict(boundary.get("manual_bump_coefficients") or {})
                cached_coefficients = dict((st.session_state.get(cache_key) or {}).get("coefficients") or {})
                manual_segments = (
                    ("te", "leading_or_inlet")
                    if tangential_method == "bump_split_progression" else tuple(labels)
                )
                manual_cols = st.columns(len(manual_segments))
                for column, segment in zip(manual_cols, manual_segments):
                    initial = (
                        cached_coefficients.get(segment, manual.get(segment, 1.0))
                        if switched_to_manual else
                        manual.get(segment, cached_coefficients.get(segment, 1.0))
                    )
                    manual[segment] = column.number_input(
                        f"Bump manual {segment}", min_value=0.0001, max_value=100.0,
                        value=float(initial), format="%.7f",
                        key=f"closed-exp-manual-bump-{segment}",
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
                    st.caption("Cada Progression se orienta desde LE/TE hacia el punto medio.")
                    keys = (
                        "upper_leading_or_inlet", "upper_te",
                        "lower_leading_or_inlet", "lower_te",
                    )
                    ncols = st.columns(4)
                    rcols = st.columns(4)
                    for key, ncol, rcol in zip(keys, ncols, rcols):
                        split_divisions[key] = ncol.number_input(
                            f"N {key}", 2, 4000,
                            int(split_divisions.get(key, max(2, divisions[key.split('_', 1)[0]] // 2))),
                            key=f"closed-exp-split-n-{key}",
                        )
                        progression[key] = rcol.number_input(
                            f"Progression {key}", 1.0, 2.0,
                            float(progression.get(key, 1.02)), format="%.8f",
                            key=f"closed-exp-split-r-{key}",
                        )
                    boundary["manual_split_progression"] = {
                        "split_divisions": split_divisions,
                        "progression_coefficients": progression,
                    }
                    try:
                        detected = load_closed_geometry(root, diagnostic_config)
                        midpoint_x = float(detected["chord"]) * float(
                            boundary["split_progression_midpoint_x_chord"]
                        )
                        upper_te, upper_le_mid, _ = split_polyline_at_x(
                            detected["upper_body"], midpoint_x
                        )
                        lower_le_mid, lower_te, _ = split_polyline_at_x(
                            detected["lower_body"], midpoint_x
                        )
                        curve_length = lambda values: float(
                            ((((values[1:] - values[:-1]) ** 2).sum(axis=1)) ** 0.5).sum()
                        )
                        curved_lengths = {
                            "leading_or_inlet": curve_length(detected["leading_edge"]),
                            "te": curve_length(detected["te_cap"]),
                        }
                        endpoint_sizes = {
                            name: float(bump_cell_sizes(
                                manual[name], curved_lengths[name], int(divisions[name])
                            )[0])
                            for name in curved_lengths
                        }
                        manual_diagnostic = evaluate_manual_split_progression(
                            half_lengths={
                                "upper": {"leading_or_inlet": curve_length(upper_le_mid), "te": curve_length(upper_te)},
                                "lower": {"leading_or_inlet": curve_length(lower_le_mid), "te": curve_length(lower_te)},
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
                    st.caption("Se mantienen los límites GR/hmax como comprobación, sin recomendaciones automáticas.")
        with ext_tab:
            external["automatic_extend_enabled"] = st.toggle(
                "Control automático exterior Gmsh Extend",
                value=bool(external.get("automatic_extend_enabled", False)),
                help=(
                    "Hereda los tamaños variables de los cuatro Bump y los propaga por el dominio. "
                    "La fila exterior de BL aún no existe cuando Gmsh evalúa el campo, por lo que se "
                    "usan las curvas transfinite que definen sus anchos."
                ),
                key="closed-exp-extend-enabled",
            )
            cols = st.columns(4)
            external["domain_radius_chord"] = cols[0].number_input(
                "Radio [c]", 10.0, 200.0, float(external.get("domain_radius_chord", 50.0)),
                key="closed-exp-domain-radius",
            )
            external["interface_tangential_factor"] = cols[1].number_input(
                "Factor primer triángulo", 0.02, 10.0,
                float(external.get("interface_tangential_factor", 0.70)),
                key="closed-exp-interface-factor",
                help="Sigue activo con Extend y gobierna la transición inmediata fuera de la última capa prismática.",
            )
            external["radial_growth_rate"] = cols[2].number_input(
                "Crecimiento radial", 0.005, 0.50, float(external.get("radial_growth_rate", 0.13)),
                disabled=external["automatic_extend_enabled"], key="closed-exp-radial-growth",
            )
            external["farfield_size_chord"] = cols[3].number_input(
                "Tamaño farfield [c]", 0.01, 200.0, float(external.get("farfield_size_chord", 5.0)),
                key="closed-exp-farfield-size",
            )
            if external["automatic_extend_enabled"]:
                extend_cols = st.columns(3)
                external["extend_distance_max_chord"] = extend_cols[0].number_input(
                    "Extend DistMax [c]", 0.001, 500.0,
                    float(external.get("extend_distance_max_chord", external["domain_radius_chord"])), format="%.3f",
                    key="closed-exp-extend-ratio",
                    help="Distancia para alcanzar SizeMax; por defecto llega hasta el farfield.",
                )
                external["extend_power"] = extend_cols[1].number_input(
                    "Extend Power", 0.1, 10.0,
                    float(external.get("extend_power", 2.0)), format="%.2f",
                    key="closed-exp-extend-power",
                    help=(
                        "Power=1 es neutro; >1 abandona antes el tamaño fino y <1 prolonga su "
                        "influencia. Verifique siempre el resultado con checkMesh."
                    ),
                )
                external["extend_size_max_chord"] = extend_cols[2].number_input(
                    "Extend tamaño máximo [c]", 0.001, 500.0,
                    float(external.get("extend_size_max_chord", external["farfield_size_chord"])),
                    format="%.3f", key="closed-exp-extend-max",
                    help="Tamaño alcanzado en DistMax; el default coincide con el farfield.",
                )
                guard_cols = st.columns(2)
                external["extend_interface_guard_enabled"] = guard_cols[0].toggle(
                    "Casar Extend con la última capa BL",
                    value=bool(external.get("extend_interface_guard_enabled", True)),
                    key="closed-exp-extend-guard",
                    help="Suaviza las primeras filas triangulares a partir de la cara exterior de BL.",
                )
                if external["extend_interface_guard_enabled"]:
                    external["extend_interface_transition_chord"] = guard_cols[1].number_input(
                        "Transición inicial Extend [c]", 0.002, 2.0,
                        float(external.get("extend_interface_transition_chord", 0.10)),
                        format="%.4f", key="closed-exp-extend-guard-distance",
                    )
            execution = dict(config.get("execution") or {})
            numeric_cols = st.columns(2)
            external["mesh_algorithm"] = numeric_cols[0].selectbox(
                "Algoritmo 2D", [6, 5],
                index=0 if int(external.get("mesh_algorithm", 6)) == 6 else 1,
                format_func=lambda value: "Frontal-Delaunay (6)" if value == 6 else "Delaunay (5)",
                key="closed-exp-algorithm",
            )
            execution["mesh_smoothing"] = numeric_cols[1].number_input(
                "Mesh.Smoothing", 0, 10, int(execution.get("mesh_smoothing", 1)),
                key="closed-exp-smoothing",
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
                    "off": "Desactivada", "laplace2d": "Laplace2D", "relocate2d": "Relocate2D",
                    "laplace2d_then_relocate2d": "Laplace2D → Relocate2D",
                }[value],
                help=(
                    "Se ejecuta antes de exportar y checkMesh. Permanece desactivada por defecto; "
                    "puede mover nodos triangulares y debe compararse como una revisión nueva."
                ),
                key="closed-exp-post-optimization",
            )
            if execution["post_generation_optimization"] != "off":
                execution["post_generation_optimization_iterations"] = st.number_input(
                    "Iteraciones Laplace2D", 1, 50,
                    int(execution.get("post_generation_optimization_iterations", 5)),
                    key="closed-exp-post-optimization-iterations",
                )
                if selected is not None:
                    execution["optimization_base_revision"] = selected.name
            config["execution"] = execution
        name = st.text_input(
            "Nombre de revisión",
            value=str(config.get("name") or "closed_validation_beta75_experimental_v1"),
            key="closed-exp-revision-name",
        )
        config.update(name=name, topology="closed", geometry=geometry, boundary_layer=boundary, external_volume=external)
        actions = st.columns(3)
        if actions[0].button("Guardar borrador", key="closed-exp-save-draft"):
            _write(draft, config)
            st.success(f"Borrador guardado: {draft}")
        if actions[1].button("Generar y comprobar", type="primary", key="closed-exp-generate"):
            _write(draft, config)
            start_job(
                "closed_experimental_mesh_generate",
                [str(sys.executable), str(root / "CFD_2D/scripts/ramair_2d_closed_experimental_mesh.py"),
                 "--project-root", str(root), "--generate", "--config", str(draft),
                 "--name", str(name), "--check-mesh"],
            )
        if actions[2].button("Restaurar defaults", key="closed-exp-defaults"):
            _write(draft, default_closed_config())
            st.rerun()

    with st.expander("Comparar calidad de revisiones", expanded=False):
        render_mesh_quality_comparator(
            experiment, topology="closed", key_prefix="closed-experimental-quality-compare",
        )
