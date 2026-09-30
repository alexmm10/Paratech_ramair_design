"""Curated polars and comparisons of runs with identical CFD conditions."""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np
import pandas as pd
from ramair_workspace import read_json, write_json, comparable_setups, alpha_name, file_digest


def available_point(setup: Path, alpha: float, stage: str = "URANS") -> dict:
    if read_json(setup / "solver_config.json").get("workflow_test"):
        raise ValueError("Esta ejecución es una prueba parcial del workflow; no es un punto de polar validado.")
    folder = setup / "Angles" / alpha_name(alpha) / "Postprocess"
    paths = [folder / stage / "case_summary.json", folder / stage / "stage_summary.json", folder / "case_summary.json"]
    for path in paths:
        summary = read_json(path)
        if not summary:
            continue
        candidate = summary
        if stage == "RANS" and isinstance(candidate.get("force_summary"), dict):
            candidate = candidate["force_summary"]
        if stage == "RANS" and path.parent == folder:
            candidate = summary.get("rans_stage") or {}
            candidate = candidate.get("force_summary", candidate.get("summary", candidate))
        if not isinstance(candidate, dict):
            continue
        means = candidate.get("mean") or {}
        lower = {k.lower(): v for k, v in means.items()}
        row = {"alpha": alpha, "stage": stage}
        for key in ("Cl", "Cd", "Cm"):
            value = lower.get(key.lower())
            if value is None:
                break
            row[key] = float(value)
        else:
            if all(math.isfinite(row[k]) for k in ("Cl", "Cd", "Cm")):
                status = str(candidate.get("status", "RANS_REVIEWED" if stage == "RANS" else summary.get("status", "")))
                if (stage != "RANS" and "PARTIAL" in status.upper()) or any(flag in status.upper() for flag in ("DIVERGED", "FAILED", "NOT_RUN", "NO_RESULTS")):
                    raise ValueError("Resultado parcial: completar/revisar antes de incluirlo en la polar.")
                row.update(source=str(path), source_sha256=file_digest(path), status=status,
                           scientific_scope="RANS revisado; independiente de la disponibilidad o duración URANS" if stage == "RANS" else "URANS")
                return row
    raise ValueError(f"No hay coeficientes {stage} postprocesados para α={alpha:g}°.")


def curate_points(setup: Path, angles: list[float], stage: str, action: str) -> pd.DataFrame:
    path = setup / "Polar/accepted_points.json"
    rows = read_json(path, {"points": []})["points"]
    additions = [available_point(setup, a, stage) for a in angles] if action == "add" else []
    rows = [r for r in rows if not (r["stage"] == stage and r["alpha"] in angles)] + additions
    rows.sort(key=lambda r: (r["stage"], r["alpha"]))
    write_json(path, {"schema_version": 1, "points": rows, "manual_acceptance": True})
    frame = pd.DataFrame(rows)
    frame.to_csv(setup / "Polar/polar.csv", index=False)
    if not frame.empty:
        import matplotlib.pyplot as plt
        frame["Cl/Cd"] = frame.Cl / frame.Cd.replace(0, np.nan)
        for coefficient in ("Cl", "Cd", "Cm", "Cl/Cd"):
            fig, ax = plt.subplots(figsize=(8, 4.5))
            for phase, data in frame.groupby("stage"):
                ax.plot(data.alpha, data[coefficient], "o-", label=phase)
            ax.set(xlabel="α [°]", ylabel=coefficient)
            ax.grid(alpha=.2)
            ax.legend()
            fig.tight_layout()
            fig.savefig(setup / "Polar" / (coefficient.replace("/", "_")+"_alpha.png"), dpi=160)
            plt.close(fig)
    return frame


def load_polar(setup: Path) -> pd.DataFrame:
    rows = read_json(setup / "Polar/accepted_points.json", {"points": []})["points"]
    # Edits/postprocessing invalidate acceptance of that exact source revision.
    valid = [r for r in rows if Path(r["source"]).is_file() and file_digest(Path(r["source"])) == r["source_sha256"]]
    frame = pd.DataFrame(valid)
    if not frame.empty:
        frame["Cl/Cd"] = np.where(frame.Cd.abs() > 1e-10, frame.Cl/frame.Cd, np.nan)
    return frame


def load_closed_validation_polar(root: Path, setup: Path) -> pd.DataFrame:
    """Import the curated closed curve as a labelled nondimensional reference."""
    path=root/'CFD_2D/validation_studies/ls1_0417_closed_polar_M0p15_Re1p9e6/postprocess/validation/ramair_validation_points.csv'
    if not path.is_file():return pd.DataFrame()
    frame=pd.read_csv(path).rename(columns={'alpha_deg':'alpha'})
    physical=read_json(setup/'solver_setup.json').get('context',{}).get('physical',{})
    for column,key,tolerance in (('reynolds','reynolds',.01),('mach','mach',.01)):
        reference=physical.get(key)
        if reference is None and key=='mach':reference=physical.get('mach_input')
        if reference is None:raise ValueError('Falta '+key+' para comprobar la referencia cerrada')
        if not np.allclose(frame[column],float(reference),rtol=tolerance,atol=1e-8):
            raise ValueError('La polar cerrada de validación no tiene el mismo Reynolds/Mach')
    frame['Cl/Cd']=frame.Cl/frame.Cd.replace(0,np.nan)
    frame['stage']='Validation published (includes partial URANS)'
    frame['source']=str(path)
    return frame


def differences_to_reference(frame: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    joined=frame.merge(reference,on='alpha',suffixes=('_open','_closed'))
    for key in ('Cl','Cd','Cm','Cl/Cd'):
        joined[key+'_difference_percent']=np.where(joined[key+'_closed'].abs()>1e-10,
            100*(joined[key+'_open']-joined[key+'_closed'])/joined[key+'_closed'].abs(),np.nan)
    return joined


def polar_metrics(frame: pd.DataFrame, linear_range: tuple[float, float] = (-4., 8.)) -> dict:
    f = frame.sort_values("alpha")
    result = {"points": len(f), "linear_range_deg": list(linear_range)}
    if f.empty:
        return result
    linear = f[f.alpha.between(*linear_range)]
    if len(linear) >= 3 and linear.alpha.nunique() >= 3:
        slope, intercept = np.polyfit(linear.alpha, linear.Cl, 1)
        result.update(Cl_alpha_per_deg=float(slope), alpha_Cl0_deg=float(-intercept/slope) if abs(slope)>1e-12 else None)
    maxrow = f.loc[f.Cl.idxmax()]
    result.update(Cl_max_observed=float(maxrow.Cl), alpha_Cl_max_deg=float(maxrow.alpha))
    efficiency = f.Cl / f.Cd.replace(0, np.nan)
    if efficiency.notna().any():
        index = efficiency.idxmax()
        result.update(Cl_Cd_max_observed=float(efficiency.loc[index]), alpha_efficiency_max_deg=float(f.loc[index, "alpha"]))
    later = f[f.alpha > maxrow.alpha]
    result["alpha_stall_approx_deg"] = float(maxrow.alpha) if not later.empty and (later.Cl < .98*maxrow.Cl).any() else None
    result["stall_note"] = "Estimación discreta; requiere caída observada después del máximo. No extrapola fuera de los ángulos simulados."
    return result


def compare_polars(first: Path, second: Path, stage: str = "URANS") -> pd.DataFrame:
    ok, reason = comparable_setups(first, second)
    if not ok:
        raise ValueError(reason)
    a, b = load_polar(first), load_polar(second)
    if a.empty or b.empty:
        return pd.DataFrame()
    a, b = a[a.stage == stage], b[b.stage == stage]
    merged = a.merge(b, on="alpha", suffixes=("_A", "_B"))
    for coefficient in ("Cl", "Cd", "Cm", "Cl/Cd"):
        lhs, rhs = merged[f"{coefficient}_A"], merged[f"{coefficient}_B"]
        merged[f"Δ {coefficient}"] = rhs-lhs
        merged[f"Δ {coefficient} [%]"] = np.where(lhs.abs() > 1e-8, 100*(rhs-lhs)/lhs.abs(), np.nan)
    return merged
