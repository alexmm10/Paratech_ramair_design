"""User-curated LS reference curves, preserving original digitized coordinates."""
from pathlib import Path
import hashlib
import json
import pandas as pd


def load_validation_reference(root: Path):
    folder = root / "CFD_2D/reference_data/LS1_0417_user_20260914"
    files = [folder / "Datasets_CL_alfa.csv", folder / "Datasets_CL_CD.csv"]
    if not all(path.is_file() for path in files):
        legacy = root / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016"
        return (pd.read_csv(legacy / "cl_alpha_digitized.csv"), pd.read_csv(legacy / "cd_cl_digitized.csv"),
                json.loads((legacy / "reference_manifest.json").read_text()) if (legacy / "reference_manifest.json").exists() else {})
    lift_raw, drag_raw = [pd.read_csv(path, header=None, skiprows=2) for path in files]
    lift, drag = [], []
    for index, label in enumerate(("Experimental", "CFD Kestrel", "CFD Cobalt")):
        a = lift_raw.iloc[:, [2*index, 2*index+1]].apply(pd.to_numeric, errors="coerce").dropna()
        a.columns = ["alpha_deg", "Cl"]
        lift.append(a.assign(series=label))
        b = drag_raw.iloc[:, [6+2*index, 7+2*index]].apply(pd.to_numeric, errors="coerce").dropna()
        b.columns = ["Cl", "Cd"]
        drag.append(b.assign(series=label))
    manifest = {"source": "User supplied manually processed reference datasets, 2026-09-14",
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "coordinate_contract": "CL-alpha: X=alpha,Y=CL; drag pairs: X=CL,Y=CD; original values unmodified",
        "drag_alpha_range_deg": [-10,16], "interpolation": "linear within measured domain; never nearest-angle substitution"}
    return pd.concat(lift, ignore_index=True), pd.concat(drag, ignore_index=True), manifest
