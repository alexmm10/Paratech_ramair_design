"""Measured OpenFOAM performance report embedded in the validation lab."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ramair_2d_parallel import load_parallel_profile, recommended_core_count
from workflow_backend import open_local_folder


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _measured_runs(root: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in (root / "CFD_2D").rglob("measured_step_performance.json"):
        if any(part.startswith("processor") for part in path.parts):
            continue
        data = _json(path)
        if data.get("status") != "MEASURED":
            continue
        case = path.parent
        rows.append({
            "caso": case.name,
            "celdas/rank": data.get("cells_per_rank"),
            "muestras": data.get("samples"),
            "mediana [s/paso]": data.get("median_s_per_step"),
            "P95 [s/paso]": data.get("p95_s_per_step"),
            "pasos/s": data.get("steps_per_second"),
            "core-s/paso": data.get("core_seconds_per_step"),
            "s/t*": data.get("wall_seconds_per_convective_time"),
            "fuente": str(path.relative_to(root)),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["muestras", "caso"], ascending=[False, True]
    )


def render_performance_report(root: Path) -> None:
    report_path = root / "CFD_2D/reports/OPENFOAM_PERFORMANCE_AUDIT_20260907.md"
    benchmark_root = root / "CFD_2D/performance_benchmarks/closed_coarse_fixed_20260907"
    scaling = _json(benchmark_root / "strong_scaling_summary.json")
    overhead = _json(benchmark_root / "monitor_overhead.json")
    cache_path = root / "CFD_2D/app_state/parallel_execution_profiles.json"
    cache = _json(cache_path)

    st.info(
        "Rendimiento medido con OpenFOAM Foundation 14 sobre el Ryzen 7 4800H. "
        "Los ensayos conservan malla, física, esquemas, tolerancias y estado inicial."
    )
    hardware = st.columns(5)
    hardware[0].metric("CPU", "8 cores / 16 hilos")
    hardware[1].metric("RAM WSL", "7.5 GiB")
    hardware[2].metric("MPI", "Open MPI 4.1.2")
    hardware[3].metric("OpenFOAM", "Foundation 14")
    hardware[4].metric("Almacenamiento", "WSL ext4")

    st.markdown("### Escalado fuerte medido")
    rows = scaling.get("rows") or []
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "203.691 celdas, 10 pasos URANS idénticos. 8 ranks minimiza latencia; "
            "2 ranks conserva 89,4% de eficiencia y es el equilibrio de campaña."
        )
    else:
        st.warning("El resumen machine-readable del benchmark no está disponible en este runtime.")

    st.markdown("### Coste observado en ejecuciones reales")
    measured = _measured_runs(root)
    if measured.empty:
        st.info("Todavía no hay historiales medidos en este runtime.")
    else:
        st.dataframe(measured.head(30), hide_index=True, width="stretch")

    monitor = st.columns(3)
    monitor[0].metric("Monitor, mediana", f"{float(overhead.get('warm_median_ms') or 0):.1f} ms")
    monitor[1].metric("Monitor, P95", f"{float(overhead.get('warm_p95_ms') or 0):.1f} ms")
    monitor[2].metric("Sobrecoste a 30 s", "≈0.21% de un core")

    st.markdown("### Selector automático")
    examples = []
    for label, cells in (
        ("Closed coarse/validation", 215_316),
        ("Open medium", 302_692),
        ("Closed fine", 618_382),
    ):
        plan = recommended_core_count(cells, available_slots=8, requested_maximum=8)
        examples.append({
            "malla representativa": label,
            "celdas": cells,
            "ranks equilibrados": plan["recommended_ranks"],
            "celdas/rank": plan["cells_per_rank"],
            "criterio": plan["reason"],
        })
    st.dataframe(examples, hide_index=True, width="stretch")
    st.caption(
        "Auto prioriza rendimiento de campaña alrededor de 100.000 celdas/rank, dentro de "
        "50.000-200.000 y sin superar cores físicos. Solo sustituye esa regla por un perfil "
        "de la misma malla y numerics con al menos cinco pasos válidos; Manual respeta el límite elegido."
    )
    valid_profiles = 0
    for key in (cache.get("profiles") or {}):
        if load_parallel_profile(cache_path, str(key)) is not None:
            valid_profiles += 1
    st.metric("Perfiles empíricos reutilizables", valid_profiles)

    with st.expander("Informe técnico completo", expanded=True):
        if report_path.is_file():
            st.markdown(report_path.read_text(encoding="utf-8"))
        else:
            st.warning(f"No se encontró {report_path}")
    if st.button("Abrir carpeta del informe", key="performance-open-report-folder"):
        open_local_folder(report_path.parent)
