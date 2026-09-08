"""Reusable Streamlit controls and monitors for independent case campaigns."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import streamlit as st

from validation_plotting import close_figures, coefficient_figure, residual_figure
from workflow_backend import validation_monitor_snapshot


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def campaign_manifest_path(root: Path, campaign_id: str) -> Path:
    return Path(root) / "CFD_2D/app_state/parallel_campaigns" / f"{campaign_id}.json"


def render_parallel_campaign_monitor(
    root: Path,
    campaign_id: str,
    *,
    key_scope: str,
    refresh_seconds: int = 10,
) -> None:
    """Render one independent monitor tab per simultaneously running case."""
    manifest_path = campaign_manifest_path(root, campaign_id)
    status_path = manifest_path.with_suffix(".status.json")
    initial_state = _read(status_path)
    manifest_just_created = bool(
        manifest_path.is_file()
        and time.time() - manifest_path.stat().st_mtime < 60.0
        and not initial_state
    )
    refresh_active = (
        str(initial_state.get("status") or "") == "RUNNING"
        or manifest_just_created
    )

    @st.fragment(run_every=max(3, int(refresh_seconds)) if refresh_active else None)
    def monitor() -> None:
        state = _read(status_path)
        if not state:
            st.caption("La campaña paralela todavía no tiene estado de ejecución.")
            return
        cases = list(state.get("cases") or [])
        metrics = st.columns(4)
        metrics[0].metric("Campaña", str(state.get("status") or "-") )
        metrics[1].metric("Casos activos", len(state.get("active_case_ids") or []))
        metrics[2].metric("Cores activos", int(state.get("active_cores") or 0))
        metrics[3].metric("Presupuesto", int(state.get("total_core_budget") or 0))
        st.dataframe(
            [
                {
                    "Caso": item.get("label") or item.get("case_id"),
                    "Estado": item.get("status"),
                    "Cores": item.get("n_cores"),
                    "Tiempo [s]": item.get("wall_time_s"),
                }
                for item in cases
            ],
            hide_index=True,
            width="stretch",
        )
        running = [item for item in cases if item.get("status") == "RUNNING"]
        if not running:
            return
        tabs = st.tabs([str(item.get("label") or item.get("case_id")) for item in running])
        for tab, item in zip(tabs, running):
            with tab:
                case = Path(str(item.get("case_path") or ""))
                status = _read(case / "run_status.json")
                staged = _read(case / "staged_run_status.json")
                label = " ".join(
                    str(value or "")
                    for value in (
                        status.get("phase"), status.get("stage"),
                        staged.get("current_phase"), staged.get("status"),
                    )
                ).lower()
                mode = "URANS" if any(token in label for token in ("urans", "pimple", "phase_")) else "RANS"
                try:
                    snapshot = validation_monitor_snapshot(
                        case,
                        mode=mode,
                        run_id=str(item.get("case_id") or case.name),
                        topology=str(item.get("topology") or "unknown"),
                        mesh_level=str(item.get("mesh_level") or "validation"),
                        cell_count=int(item.get("cell_count") or 0),
                        n_cores=int(item.get("n_cores") or 1),
                        alpha_deg=(
                            float(item["alpha_deg"])
                            if item.get("alpha_deg") is not None else None
                        ),
                        stage=str(status.get("phase") or staged.get("current_phase") or ""),
                    )
                    cols = st.columns(4)
                    cols[0].metric("Estado", str(status.get("status") or item.get("status")))
                    cols[1].metric("Solver", mode)
                    cols[2].metric("Ángulo", f"{float(item['alpha_deg']):g}°" if item.get("alpha_deg") is not None else "-")
                    cols[3].metric("Cores", int(item.get("n_cores") or 1))
                    residual, _ = residual_figure(snapshot, mode=mode)
                    coefficient, _ = coefficient_figure(snapshot, mode=mode)
                    plot_cols = st.columns(2)
                    plot_cols[0].pyplot(residual, width="stretch")
                    plot_cols[1].pyplot(coefficient, width="stretch")
                    close_figures(residual, coefficient)
                except Exception as exc:
                    st.info(f"El caso está arrancando; monitor aún no disponible: {exc}")
                if st.button(
                    "Guardar y detener este caso",
                    key=f"{key_scope}-pause-{item.get('case_id')}",
                ):
                    _write(
                        manifest_path.with_suffix(".control.json"),
                        {"action": "pause_case", "case_id": item.get("case_id")},
                    )
                    st.rerun()
        if st.button("Guardar y detener toda la campaña", key=f"{key_scope}-pause-all"):
            _write(manifest_path.with_suffix(".control.json"), {"action": "pause_all"})
            st.rerun()

    monitor()
