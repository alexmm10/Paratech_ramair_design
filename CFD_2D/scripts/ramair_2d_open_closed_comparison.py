"""Publish open-airfoil validation points and compare them with the closed polar."""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ramair_scientific_plot_style import apply_scientific_style, save_scientific_figure


apply_scientific_style()

OPEN_VARIANT = "open_ramair_validation_1m"
CLOSED_STUDY = "ls1_0417_closed_polar_M0p15_Re1p9e6"
COMPARISON_STUDY = "open_closed_polar_M0p15_Re1p9e6"
POINT_COLUMNS = [
    "alpha_deg", "Cl", "Cd", "Cm", "L_D", "reynolds", "mach",
    "result_dir", "status", "published_incomplete", "publication_warning",
    "updated_at",
]
VARIABLES = {
    "Cl": (r"$C_L$", "Lift coefficient"),
    "Cd": (r"$C_D$", "Drag coefficient"),
    "Cm": (r"$C_m$", "Pitching-moment coefficient"),
    "L_D": (r"$C_L/C_D$", "Aerodynamic efficiency"),
}


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_csv(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns or [])
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame(columns=columns or [])
    for column in columns or []:
        if column not in frame:
            frame[column] = pd.Series(dtype="object")
    return frame


def alpha_directory(alpha: float) -> str:
    return f"alpha_{float(alpha):+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def comparison_root(project_root: Path) -> Path:
    return project_root / "CFD_2D/validation_studies" / COMPARISON_STUDY


def comparison_output(project_root: Path) -> Path:
    return comparison_root(project_root) / "postprocess/comparison"


def closed_points_path(project_root: Path) -> Path:
    return (
        project_root / "CFD_2D/validation_studies" / CLOSED_STUDY
        / "postprocess/validation/ramair_validation_points.csv"
    )


def _dictionary_entry(text: str, name: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s+([^;]+);", text)
    return match.group(1).strip() if match else None


def nondimensionalization_audit(project_root: Path, alpha: float = 8.0) -> dict[str, Any]:
    """Verify that open and closed coefficients use the same 2-D references."""
    cases = {
        "closed": project_root / "CFD_2D/openfoam_cases/reference_uncut_validation_1m" / alpha_directory(alpha),
        "open": project_root / "CFD_2D/openfoam_cases" / OPEN_VARIANT / alpha_directory(alpha),
    }
    evidence: dict[str, Any] = {}
    for label, case in cases.items():
        cfg = _read_json(case / "case_config.json", {}) or {}
        control_path = case / "system/controlDict"
        control = control_path.read_text(encoding="utf-8", errors="ignore") if control_path.is_file() else ""
        evidence[label] = {
            "case": str(case),
            "chord_m": cfg.get("chord_m"), "reynolds": cfg.get("reynolds"),
            "mach": cfg.get("mach_input"), "rho": cfg.get("rho"), "mu": cfg.get("mu"),
            "velocity_m_s": cfg.get("velocity_m_s"),
            "spanwise_thickness_chord": cfg.get("spanwise_thickness_chord"),
            "geometry_topology": cfg.get("geometry_topology"),
            "lRef": _dictionary_entry(control, "lRef"),
            "Aref": _dictionary_entry(control, "Aref"),
            "CofR": _dictionary_entry(control, "CofR"),
            "rhoInf": _dictionary_entry(control, "rhoInf"),
            "magUInf": _dictionary_entry(control, "magUInf"),
            "liftDir": _dictionary_entry(control, "liftDir"),
            "dragDir": _dictionary_entry(control, "dragDir"),
            "force_patches": _dictionary_entry(control, "patches"),
        }
    scalar_keys = (
        "chord_m", "reynolds", "mach", "rho", "mu", "velocity_m_s",
        "spanwise_thickness_chord", "lRef", "Aref", "CofR", "rhoInf",
        "magUInf", "liftDir", "dragDir",
    )
    checks = {key: evidence["closed"].get(key) == evidence["open"].get(key) for key in scalar_keys}
    force_patch_check = (
        evidence["closed"].get("force_patches") == "(airfoil_wall)"
        and evidence["open"].get("force_patches") == "(airfoil_wall_external airfoil_wall_internal)"
    )
    checks["topology_specific_force_patches"] = force_patch_check
    comparable = all(checks.values())
    return {
        "status": "COMPARABLE" if comparable else "REFERENCE_MISMATCH",
        "comparable": comparable,
        "checks": checks,
        "evidence": evidence,
        "coefficient_definition": (
            "Cl=Fl/(0.5*rho*Uinf^2*Aref), Cd=Fd/(0.5*rho*Uinf^2*Aref), "
            "Cm=Mz/(0.5*rho*Uinf^2*Aref*lRef). OpenFOAM forceCoeffs integrates "
            "pressure and viscous contributions on every listed wall patch."
        ),
        "scientific_scope": (
            "Both meshes represent a unit-chord section extruded 0.01c with identical flow "
            "properties and quarter-chord moment centre. The coefficients are therefore directly "
            "comparable; the different wall-patch sets express the intended closed/open topology."
        ),
    }


def _mean_record(project_root: Path, variant: str, alpha: float) -> tuple[dict[str, Any] | None, str | None]:
    name = alpha_directory(alpha)
    result = project_root / "CFD_2D/results" / variant / name
    case = project_root / "CFD_2D/openfoam_cases" / variant / name
    cfg = _read_json(case / "case_config.json", {}) or {}
    summary = _read_json(result / "case_summary.json", {}) or {}
    mean_path = result / "forceCoeffs_mean.csv"
    try:
        row = pd.read_csv(mean_path).iloc[0]
        cl, cd, cm = (float(row[key]) for key in ("Cl", "Cd", "Cm"))
        reynolds = float(cfg["reynolds"])
        mach = float(cfg["mach_input"])
    except (OSError, KeyError, ValueError, IndexError, pd.errors.EmptyDataError) as exc:
        return None, f"No se puede leer el promedio del caso: {exc}"
    run_status = str((summary.get("run_status") or {}).get("status") or "").upper()
    if run_status in {"RUN_DIVERGED", "NUMERICAL_DIVERGENCE", "SOLVER_FAILED"}:
        return None, f"El promedio pertenece a una ejecución divergida ({run_status})"
    if max(abs(cl), abs(cd), abs(cm)) > 100.0:
        return None, "Los coeficientes exceden el límite físico de publicación; revise divergencia"
    staged = _read_json(case / "staged_run_status.json", {}) or {}
    staged_status = str(staged.get("status") or "NOT_STAGED").upper()
    complete = staged_status in {"TRANSIENT_STAGE_FINISHED", "TRANSIENT_STAGE_CONVERGED"}
    return {
        "alpha_deg": float(alpha), "Cl": cl, "Cd": cd, "Cm": cm,
        "L_D": cl / cd if abs(cd) > 1.0e-15 else float("nan"),
        "reynolds": reynolds, "mach": mach, "result_dir": str(result),
        "status": str(summary.get("status") or staged_status),
        "published_incomplete": not complete,
        "publication_warning": "" if complete else "Punto provisional: la fase transitoria no ha finalizado.",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, None


def publish_open_point(
    project_root: Path,
    alpha: float,
    *,
    allow_incomplete: bool = False,
) -> Path:
    output = comparison_output(project_root)
    output.mkdir(parents=True, exist_ok=True)
    record, error = _mean_record(project_root, OPEN_VARIANT, alpha)
    if record is None:
        raise RuntimeError(error or "Resultado abierto no disponible")
    audit = nondimensionalization_audit(project_root, alpha)
    if not bool(audit.get("comparable")):
        raise RuntimeError(f"Referencias aerodinámicas incompatibles: {audit.get('checks')}")
    if abs(float(record["reynolds"]) - 1.9e6) / 1.9e6 > 0.01 or abs(float(record["mach"]) - 0.15) > 0.005:
        raise RuntimeError("El resultado abierto no coincide con Re=1.9e6 y M=0.15")
    if bool(record["published_incomplete"]) and not allow_incomplete:
        raise RuntimeError("La ejecución no ha finalizado; active la publicación provisional para incluirla")
    path = output / "open_validation_points.csv"
    points = _read_csv(path, POINT_COLUMNS)
    if not points.empty:
        values = pd.to_numeric(points["alpha_deg"], errors="coerce")
        points = points[~np.isclose(values, float(alpha), atol=1.0e-9, rtol=0.0)]
    points = pd.concat([points, pd.DataFrame([record])], ignore_index=True)
    points.reindex(columns=POINT_COLUMNS).sort_values("alpha_deg").to_csv(path, index=False)
    generate_open_closed_report(project_root)
    return path


def remove_open_point(project_root: Path, alpha: float) -> Path:
    output = comparison_output(project_root)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "open_validation_points.csv"
    points = _read_csv(path, POINT_COLUMNS)
    if not points.empty:
        values = pd.to_numeric(points["alpha_deg"], errors="coerce")
        points = points[~np.isclose(values, float(alpha), atol=1.0e-9, rtol=0.0)]
    points.reindex(columns=POINT_COLUMNS).to_csv(path, index=False)
    generate_open_closed_report(project_root)
    return path


def _finite_sorted(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("alpha_deg", "Cl", "Cd", "Cm", "L_D"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    if "L_D" not in result and {"Cl", "Cd"}.issubset(result):
        result["L_D"] = result["Cl"] / result["Cd"].replace(0.0, np.nan)
    return result.dropna(subset=["alpha_deg", "Cl", "Cd"]).sort_values("alpha_deg")


def _aero_features(frame: pd.DataFrame) -> dict[str, float | None]:
    data = _finite_sorted(frame)
    if data.empty:
        return {key: None for key in ("Cl_max", "alpha_Cl_max", "L_D_max", "alpha_L_D_max", "alpha_Cl_zero", "Cl_alpha_per_deg", "alpha_stall")}
    cl_peak = data.loc[data["Cl"].idxmax()]
    ld_data = data.dropna(subset=["L_D"])
    ld_peak = ld_data.loc[ld_data["L_D"].idxmax()] if not ld_data.empty else None
    zero_alpha: float | None = None
    for (_, left), (_, right) in zip(data.iloc[:-1].iterrows(), data.iloc[1:].iterrows()):
        if float(left["Cl"]) == 0.0:
            zero_alpha = float(left["alpha_deg"])
            break
        if float(left["Cl"]) * float(right["Cl"]) < 0.0:
            zero_alpha = float(np.interp(0.0, [left["Cl"], right["Cl"]], [left["alpha_deg"], right["alpha_deg"]]))
            break
    linear = data[(data["alpha_deg"] >= -4.0) & (data["alpha_deg"] <= 8.0)]
    slope = float(np.polyfit(linear["alpha_deg"], linear["Cl"], 1)[0]) if len(linear) >= 2 else None
    after_peak = data[data["alpha_deg"] > float(cl_peak["alpha_deg"])]
    stall = float(cl_peak["alpha_deg"]) if not after_peak.empty and float(after_peak["Cl"].min()) < float(cl_peak["Cl"]) else None
    return {
        "Cl_max": float(cl_peak["Cl"]), "alpha_Cl_max": float(cl_peak["alpha_deg"]),
        "L_D_max": None if ld_peak is None else float(ld_peak["L_D"]),
        "alpha_L_D_max": None if ld_peak is None else float(ld_peak["alpha_deg"]),
        "alpha_Cl_zero": zero_alpha, "Cl_alpha_per_deg": slope, "alpha_stall": stall,
    }


def _save_table(frame: pd.DataFrame, path: Path, title: str, footnote: str) -> None:
    height = max(2.6, 1.0 + 0.42 * len(frame))
    fig, ax = plt.subplots(figsize=(9.0, height), constrained_layout=True)
    ax.axis("off")
    formatted = frame.copy()
    if formatted.empty:
        formatted = pd.DataFrame([["--"] * max(1, len(frame.columns))], columns=list(frame.columns) or ["Data"])
    for column in formatted.columns:
        formatted[column] = formatted[column].map(
            lambda value: "--" if pd.isna(value) else (f"{value:.4g}" if isinstance(value, (float, np.floating)) else str(value))
        )
    table = ax.table(cellText=formatted.values, colLabels=formatted.columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.45)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#6d7480")
        cell.set_linewidth(0.55)
        cell.set_facecolor("#e8eaed" if row == 0 else ("#f7f5f0" if row % 2 else "#ffffff"))
        if row == 0:
            cell.set_text_props(weight="bold")
    ax.set_title(title, fontsize=13, pad=12)
    fig.text(0.5, 0.02, footnote, ha="center", fontsize=8, color="#555555")
    save_scientific_figure(fig, path, data=frame, metadata={"table_style": "publication", "note": footnote})


def _interpolate_row(frame: pd.DataFrame, alpha: float) -> dict[str, float] | None:
    data = _finite_sorted(frame)
    if data.empty or alpha < float(data["alpha_deg"].min()) or alpha > float(data["alpha_deg"].max()):
        return None
    return {key: float(np.interp(alpha, data["alpha_deg"], data[key])) for key in VARIABLES}


def write_rans_urans_comparison(
    project_root: Path,
    variant: str,
    published_points: pd.DataFrame,
    output: Path,
) -> dict[str, Any]:
    """Compare the archived RANS tail with the selected RANS+URANS mean."""
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for alpha in pd.to_numeric(published_points.get("alpha_deg", pd.Series(dtype=float)), errors="coerce").dropna():
        result = project_root / "CFD_2D/results" / variant / alpha_directory(float(alpha))
        candidates = {
            "RANS": result / "RANS/forceCoeffs_RANS_mean.csv",
            "RANS+URANS": result / "URANS/forceCoeffs_mean.csv",
        }
        if not candidates["RANS+URANS"].is_file():
            candidates["RANS+URANS"] = result / "forceCoeffs_mean.csv"
        values: dict[str, dict[str, float]] = {}
        for method, path in candidates.items():
            try:
                raw = pd.read_csv(path).iloc[0]
                cl, cd, cm = (float(raw[key]) for key in ("Cl", "Cd", "Cm"))
                values[method] = {"Cl": cl, "Cd": cd, "Cm": cm, "L_D": cl / cd if abs(cd) > 1.0e-15 else math.nan}
            except (OSError, KeyError, ValueError, IndexError, pd.errors.EmptyDataError):
                continue
        if len(values) != 2:
            continue
        for method, coefficients in values.items():
            rows.append({"alpha_deg": float(alpha), "method": method, **coefficients})
    data = pd.DataFrame(rows)
    data.to_csv(output / "rans_urans_points.csv", index=False)
    if data.empty:
        return {"status": "NO_MATCHED_RANS_URANS_POINTS", "points": 0}

    for variable, (symbol, title) in VARIABLES.items():
        fig, ax = plt.subplots(figsize=(7.2, 4.5), constrained_layout=True)
        for method, style in (("RANS", {"color": "#c23b22", "marker": "o"}), ("RANS+URANS", {"color": "#146b55", "marker": "D"})):
            subset = data[data["method"] == method].sort_values("alpha_deg")
            ax.plot(subset["alpha_deg"], subset[variable], label=method, linewidth=1.4, **style)
        ax.set(xlabel=r"Angle of attack, $\alpha$ [deg]", ylabel=f"{symbol} [-]", title=f"{title}: RANS versus RANS+URANS")
        ax.grid(True, alpha=0.25); ax.legend()
        save_scientific_figure(fig, output / f"rans_urans_{variable}_alpha.png", data=data[["alpha_deg", "method", variable]])

    fig, ax = plt.subplots(figsize=(7.2, 4.5), constrained_layout=True)
    for method, style in (("RANS", {"color": "#c23b22", "marker": "o"}), ("RANS+URANS", {"color": "#146b55", "marker": "D"})):
        subset = data[data["method"] == method].sort_values("alpha_deg")
        ax.plot(subset["Cl"], subset["Cd"], label=method, linewidth=1.4, **style)
    ax.set(xlabel=r"$C_L$ [-]", ylabel=r"$C_D$ [-]", title="Drag polar: RANS versus RANS+URANS")
    ax.grid(True, alpha=0.25); ax.legend()
    save_scientific_figure(fig, output / "rans_urans_CD_CL.png", data=data)

    pivot = data.pivot(index="alpha_deg", columns="method", values=list(VARIABLES)).sort_index()
    differences: list[dict[str, Any]] = []
    statistics: list[dict[str, Any]] = []
    for variable in VARIABLES:
        rans = pivot[(variable, "RANS")].to_numpy(dtype=float)
        urans = pivot[(variable, "RANS+URANS")].to_numpy(dtype=float)
        alphas = pivot.index.to_numpy(dtype=float)
        scale = float(np.ptp(rans))
        err = (
            math.nan if len(rans) < 2 or scale <= 1.0e-12
            else 100.0 * float(np.sqrt(np.mean((urans - rans) ** 2))) / scale
        )
        peak = float(np.max(rans))
        err2 = math.nan if abs(peak) <= 1.0e-12 else 100.0 * (float(np.max(urans)) - peak) / peak
        statistics.append({"variable": variable, "err_percent": err, "err2_percent": err2, "matched_angles": len(alphas)})
        for alpha, left, right in zip(alphas, rans, urans):
            absolute = right - left
            relative = math.nan if abs(left) <= max(1.0e-10, 0.005 * max(np.max(np.abs(rans)), 1.0)) else 100.0 * absolute / left
            differences.append({"alpha_deg": alpha, "variable": variable, "RANS": left, "RANS_URANS": right, "absolute_difference": absolute, "relative_difference_percent": relative})
        fig, ax = plt.subplots(figsize=(7.2, 4.3), constrained_layout=True)
        ax.plot(alphas, urans - rans, color="#7a3e9d", marker="o")
        ax.axhline(0.0, color="#555555", linewidth=0.8)
        ax.set(xlabel=r"Angle of attack, $\alpha$ [deg]", ylabel=rf"$\Delta$ {VARIABLES[variable][0]} [-]", title=f"RANS+URANS minus RANS: {variable}")
        ax.grid(True, alpha=0.25)
        save_scientific_figure(fig, output / f"rans_urans_absolute_{variable}.png", data=pd.DataFrame(differences)[lambda x: x.variable == variable])
        relative_values = np.array([
            row["relative_difference_percent"]
            for row in differences if row["variable"] == variable
        ], dtype=float)
        fig, ax = plt.subplots(figsize=(7.2, 4.3), constrained_layout=True)
        ax.plot(alphas, relative_values, color="#247ba0", marker="o")
        ax.axhline(0.0, color="#555555", linewidth=0.8)
        ax.set(xlabel=r"Angle of attack, $\alpha$ [deg]", ylabel="Relative difference [%]", title=f"Relative RANS+URANS effect: {variable}")
        ax.grid(True, alpha=0.25)
        save_scientific_figure(fig, output / f"rans_urans_relative_{variable}.png", data=pd.DataFrame(differences)[lambda x: x.variable == variable])

    diff_frame = pd.DataFrame(differences)
    stats = pd.DataFrame(statistics)
    diff_frame.to_csv(output / "rans_urans_differences.csv", index=False)
    stats.to_csv(output / "rans_urans_error_statistics.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.1), constrained_layout=True)
    axes[0].bar(stats["variable"], stats["err_percent"], color="#247ba0")
    axes[1].bar(stats["variable"], stats["err2_percent"], color="#e07a2d")
    axes[0].set(title="Full-polar err", ylabel="Normalized RMS error [%]")
    axes[1].set(title="Maximum-value err2", ylabel="Signed peak error [%]")
    for ax in axes: ax.grid(True, axis="y", alpha=0.25)
    save_scientific_figure(fig, output / "rans_urans_err_err2.png", data=stats, metadata={
        "err": "100*RMS(RANS+URANS-RANS)/range(RANS)",
        "err2": "100*(max(RANS+URANS)-max(RANS))/max(RANS)",
        "scope": "published angles with both archived means",
    })
    return {"status": "GENERATED", "points": int(len(pivot)), "statistics": statistics}


def generate_open_closed_report(project_root: Path) -> Path:
    output = comparison_output(project_root)
    output.mkdir(parents=True, exist_ok=True)
    closed = _finite_sorted(_read_csv(closed_points_path(project_root)))
    open_points = _finite_sorted(_read_csv(output / "open_validation_points.csv", POINT_COLUMNS))
    styles = {
        "Closed LS(1)-0417": (closed, "#202124", "o"),
        "Open RamAir": (open_points, "#b3261e", "D"),
    }
    plot_frames = [frame.assign(geometry=label) for label, (frame, _, _) in styles.items() if not frame.empty]
    combined_plot_data = pd.concat(plot_frames, ignore_index=True, sort=False) if plot_frames else pd.DataFrame()
    for variable, (symbol, title) in VARIABLES.items():
        fig, ax = plt.subplots(figsize=(7.2, 4.6), constrained_layout=True)
        for label, (frame, color, marker) in styles.items():
            if not frame.empty:
                ax.plot(frame["alpha_deg"], frame[variable], color=color, marker=marker, linewidth=1.4, label=label)
        ax.set(xlabel=r"Angle of attack, $\alpha$ [deg]", ylabel=f"{symbol} [-]", title=f"Closed versus open: {title}")
        ax.grid(True, alpha=0.25); ax.legend()
        save_scientific_figure(fig, output / f"open_closed_{variable}_alpha.png", data=combined_plot_data)
    fig, ax = plt.subplots(figsize=(7.2, 4.6), constrained_layout=True)
    for label, (frame, color, marker) in styles.items():
        if not frame.empty:
            ax.plot(frame["Cl"], frame["Cd"], color=color, marker=marker, linewidth=1.4, label=label)
    ax.set(xlabel=r"$C_L$ [-]", ylabel=r"$C_D$ [-]", title="Closed versus open: drag polar")
    ax.grid(True, alpha=0.25); ax.legend()
    save_scientific_figure(fig, output / "open_closed_CD_CL.png", data=combined_plot_data)

    feature_rows = []
    for label, (frame, _, _) in styles.items():
        feature_rows.append({"Geometry": label, **_aero_features(frame)})
    features = pd.DataFrame(feature_rows)
    features.to_csv(output / "open_closed_aerodynamic_features.csv", index=False)
    _save_table(features, output / "open_closed_aerodynamic_features.png", "Aerodynamic polar characteristics", "Values are inferred only from manually published angles; '--' means the feature is not bracketed.")

    relative_rows: list[dict[str, Any]] = []
    open_features = _aero_features(open_points)
    alpha_ld = open_features.get("alpha_L_D_max")
    if alpha_ld is not None:
        open_at = _interpolate_row(open_points, float(alpha_ld))
        closed_at = _interpolate_row(closed, float(alpha_ld))
        if open_at and closed_at:
            for variable in VARIABLES:
                reference = closed_at[variable]
                relative_rows.append({
                    "Coefficient": variable, "alpha_deg": float(alpha_ld),
                    "Closed": reference, "Open": open_at[variable],
                    "Relative change [%]": math.nan if abs(reference) <= 1.0e-12 else 100.0 * (open_at[variable] - reference) / reference,
                })
    relative = pd.DataFrame(relative_rows, columns=["Coefficient", "alpha_deg", "Closed", "Open", "Relative change [%]"])
    relative.to_csv(output / "open_closed_relative_at_open_LDmax.csv", index=False)
    _save_table(relative, output / "open_closed_relative_at_open_LDmax.png", "Open-airfoil change at its maximum-efficiency angle", "Closed values are linearly interpolated only inside the published closed-angle range.")

    transient = write_rans_urans_comparison(project_root, OPEN_VARIANT, open_points, output / "rans_urans")
    audit = nondimensionalization_audit(project_root)
    (output / "nondimensionalization_audit.json").write_text(
        json.dumps(audit, indent=2) + "\n", encoding="utf-8",
    )
    report = {
        "schema_version": 1,
        "status": "READY" if not open_points.empty and not closed.empty else "WAITING_FOR_PUBLISHED_POINTS",
        "closed_points": int(len(closed)), "open_points": int(len(open_points)),
        "reference_policy": "Only points manually published in the existing closed validation polar are used.",
        "open_mesh": "open_medium",
        "nondimensionalization": audit,
        "rans_urans": transient,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (output / "open_closed_summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--action", choices=["refresh", "publish", "remove"], default="refresh")
    parser.add_argument("--alpha", type=float)
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.project_root.resolve()
    if args.action == "publish":
        if args.alpha is None: raise SystemExit("--alpha is required for publish")
        path = publish_open_point(root, args.alpha, allow_incomplete=args.allow_incomplete)
    elif args.action == "remove":
        if args.alpha is None: raise SystemExit("--alpha is required for remove")
        path = remove_open_point(root, args.alpha)
    else:
        path = generate_open_closed_report(root)
    print(path)


if __name__ == "__main__":
    main()
