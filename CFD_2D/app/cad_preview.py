"""Cached preview from the same isolated preprocessor used by named CAD models."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import streamlit as st


@st.cache_data(show_spinner="Calculando geometría y anclajes de la vista previa…", max_entries=8)
def generate_preview(root_string: str, config_json: str, system_json: str, profile: str, source_mtime: int) -> dict:
    root = Path(root_string)
    signature = hashlib.sha256((config_json+system_json+profile+str(source_mtime)).encode()).hexdigest()[:20]
    model = root / "Application Support/Temp/CAD_Previews" / signature
    cfg, system = json.loads(config_json), json.loads(system_json)
    model.mkdir(parents=True, exist_ok=True)
    cfg["profile_inputs"] = {"main_profile": profile, "profile_input_order": "auto"}
    cfg.setdefault("airfoil_processing", {})["te_closure_mode"] = "straight_gap"
    cfg.setdefault("optional_modules", {})["system_config_json"] = str(model / "system_config.json")
    cfg.setdefault("project_paths", {})["catia_inputs_dir"] = str(model / "Inputs")
    cfg["project_paths"]["catia_exports_dir"] = str(model / "Exports")
    cfg["cfd_2d_exports"] = {"enable_2d_cae_exports": False}
    cfg["debug_plots"] = {k: True for k in ("write_chord_distribution_plot", "write_cell_span_plot", "write_anhedral_plot", "write_crossports_plot", "write_stabilizer_plot", "write_suspension_plot")}
    cfg.pop("fabric_and_lines", None)
    for name, payload in (("model_config.json", cfg), ("system_config.json", system)):
        (model/name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = subprocess.run([sys.executable, str(root/"preprocess_ramair_main.py"), "--config", str(model/"model_config.json"),
        "--input-csv", profile, "--out", str(model/"Inputs"), "--cad-only"], cwd=root, capture_output=True, text=True, timeout=90)
    (model/"preview.log").write_text(result.stdout+"\n"+result.stderr, encoding="utf-8")
    return {"ok": result.returncode == 0, "folder": str(model/"Outputs/Debug"),
            "detail": (result.stderr or result.stdout)[-3000:], "log": str(model/"preview.log")}


def render_preview(root, cfg, system, profile, stabilizer_slot, suspension_slot):
    dat = next(Path(profile["path"]).glob("*.dat"))
    with stabilizer_slot:
        live = st.toggle("Actualizar previsualizaciones de geometría", value=True,
            help="Calcula la geometría Python en una carpeta temporal aislada. No inicia CATIA.", key="cad/live_preview")
    if not live: return
    sources = [dat, root/"preprocess_ramair_main.py", root/"CFD_2D/scripts/ramair_suspension_layout.py", root/"CFD_2D/scripts/ramair_cad_diagnostics.py"]
    result = generate_preview(str(root), json.dumps(cfg, sort_keys=True), json.dumps(system, sort_keys=True),
                              str(dat), max(p.stat().st_mtime_ns for p in sources))
    with stabilizer_slot:
        if not result["ok"]:
            st.warning("La vista previa ha detectado una configuración incompleta o inválida.")
            with st.expander("Detalle de geometría"): st.code(result["detail"])
        path = Path(result["folder"])/"05_stabilizer_true_projection.png"
        if result["ok"] and path.is_file(): st.image(str(path), width="stretch")
    with suspension_slot:
        if result["ok"]:
            folder = Path(result["folder"])
            path = folder/"06_suspension_network.png"
            if path.is_file(): st.image(str(path), width="stretch")
            for name in ("10_brake_cells", "07_suspension_plan", "08_suspension_front", "09_suspension_side"):
                path = folder/(name+".png")
                if path.is_file(): st.image(str(path), width="stretch")
            selection = folder/"brake_selection_effective.json"
            if selection.is_file():
                data = json.loads(selection.read_text(encoding="utf-8"))
                for side, item in data["sides"].items():
                    requested = item.get("requested_fraction")
                    st.caption(f"{side}: celdas {item['cells']} · {item['attachments']} fijaciones · cobertura efectiva {100*item['effective_fraction']:.1f}% de b/2" + (f" · solicitada {100*requested:.1f}%" if requested is not None else ""))
            with st.expander("Anclajes, nodos y longitudes reales"):
                import pandas as pd
                for name in ("manual_node_reference.csv", "manual_connection_reference.csv", "suspension_anchor_paths.csv"):
                    path = folder/name
                    if path.is_file():
                        st.download_button("Descargar "+name, path.read_bytes(), name, key="cad/download/"+name)
                        st.dataframe(pd.read_csv(path), hide_index=True, width="stretch")
