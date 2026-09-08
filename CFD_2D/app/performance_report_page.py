"""Measured OpenFOAM performance report embedded in the validation lab."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ramair_2d_parallel import (
    load_parallel_profile,
    parallel_campaign_allocation,
    recommended_core_count,
)
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
    stability_report_path = (
        root
        / "Documents and Manuals/Application/manuals/OPEN_URANS_STABILITY_DIAGNOSIS_20260909.md"
    )
    stability_data = _json(
        root / "CFD_2D/reports/open_medium_a08_dt_stability_diagnostic_20260908.json"
    )
    benchmark_root = root / "CFD_2D/performance_benchmarks/closed_coarse_fixed_20260907"
    scaling = _json(benchmark_root / "strong_scaling_summary.json")
    overhead = _json(benchmark_root / "monitor_overhead.json")
    throughput_4 = _json(benchmark_root / "parallel_throughput_2x2_vs_1x4.json")
    throughput_8 = _json(benchmark_root / "parallel_throughput_2x4_vs_1x8.json")
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
            "4 ranks ofrece el compromiso medido de latencia y coste. Los ensayos "
            "simultáneos posteriores mantienen Serie como recomendación de campaña."
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

    st.markdown("### Serie frente a casos simultáneos")
    throughput_rows = []
    for budget, report in ((4, throughput_4), (8, throughput_8)):
        if not report:
            continue
        single = report.get("single_4") or report.get("single") or {}
        pair = report.get("parallel_2x2") or report.get("parallel_pair") or {}
        throughput_rows.append({
            "Presupuesto total": f"{budget} cores",
            "Serie": f"1×{budget}",
            "Serie [caso-pasos/s]": single.get("aggregate_case_steps_per_s"),
            "Simultánea": f"2×{budget // 2}",
            "Simultánea [caso-pasos/s]": pair.get("aggregate_case_steps_per_s"),
            "Cambio simultáneo": report.get("throughput_gain_percent"),
        })
    if throughput_rows:
        st.dataframe(throughput_rows, hide_index=True, width="stretch")
        st.warning(
            "En las pruebas controladas de esta máquina, dos solvers simultáneos fueron "
            "un 6–9% menos productivos que dedicar el mismo presupuesto a un solo caso. "
            "La opción paralela queda disponible para uso interactivo, pero Serie es la recomendada."
        )
    else:
        st.info("Los benchmarks de campañas simultáneas aún no están en este runtime.")

    st.markdown("### Selector automático")
    examples = []
    for label, cells in (
        ("Closed coarse/validation", 215_316),
        ("Open medium", 302_692),
        ("Closed fine", 618_382),
    ):
        plan = recommended_core_count(
            cells, available_slots=8, requested_maximum=8,
            workload_mode="single_latency",
        )
        concurrent = parallel_campaign_allocation(
            [cells, cells], total_core_budget=8, max_concurrent_cases=2,
        )[0]
        examples.append({
            "malla representativa": label,
            "celdas": cells,
            "ranks equilibrados": plan["recommended_ranks"],
            "celdas/rank": plan["cells_per_rank"],
            "ranks si paralelo": concurrent["recommended_ranks"],
            "criterio": plan["reason"],
        })
    st.dataframe(examples, hide_index=True, width="stretch")
    st.caption(
        "Para un caso, Auto prioriza latencia alrededor de 50.000 celdas/rank y no supera "
        "los 8 cores físicos. En una campaña simultánea usa alrededor de 100.000 celdas/rank "
        "y divide el presupuesto; los resultados medidos hacen que Serie siga siendo el modo recomendado."
    )
    valid_profiles = 0
    for key in (cache.get("profiles") or {}):
        if load_parallel_profile(cache_path, str(key)) is not None:
            valid_profiles += 1
    st.metric("Perfiles empíricos reutilizables", valid_profiles)

    st.markdown("### Diagnóstico URANS abierto")
    maximum_cell = stability_data.get("maximum_courant_cell") or {}
    measured = stability_data.get("measured_final") or {}
    stability_metrics = st.columns(4)
    stability_metrics[0].metric("Celda limitante", maximum_cell.get("cell_id") or "-")
    stability_metrics[1].metric("Co local", f"{float(maximum_cell.get('maximum_Co') or 0):.3g}")
    stability_metrics[2].metric("dt adaptativo", f"{float(measured.get('deltaT_s') or 0):.3g} s")
    stability_metrics[3].metric(
        "dt estimado para Co=5",
        f"{float(measured.get('estimated_deltaT_for_Co_5_s') or 0):.3g} s",
    )
    st.caption(
        "La limitación está localizada en la transición exterior del labio superior, no en el dominio completo."
    )
    with st.expander("Informe de estabilidad URANS abierta", expanded=False):
        if stability_report_path.is_file():
            st.markdown(stability_report_path.read_text(encoding="utf-8"))
        else:
            st.warning(f"No se encontró {stability_report_path}")

    with st.expander("Informe técnico completo", expanded=True):
        if report_path.is_file():
            st.markdown(report_path.read_text(encoding="utf-8"))
        else:
            st.warning(f"No se encontró {report_path}")
    if st.button("Abrir carpeta del informe", key="performance-open-report-folder"):
        open_local_folder(report_path.parent)
