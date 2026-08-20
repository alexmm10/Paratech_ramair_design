#!/usr/bin/env python3
"""Analyze the alpha=8 LS(1)-0417 mesh-refinement study using real outputs only."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ramair_scientific_plot_style import save_scientific_figure


LEVELS = (
    ("coarse", "reference_uncut_validation_1m_coarse"),
    ("medium", "reference_uncut_validation_1m"),
    ("fine", "reference_uncut_validation_1m_fine"),
)
ALPHA = 8.0


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def numeric_times(case_dir: Path) -> list[float]:
    values: list[float] = []
    for path in case_dir.iterdir() if case_dir.is_dir() else []:
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    return sorted(values)


def total_wall_time_s(case_dir: Path) -> float | None:
    paths = [case_dir / "pyfoam_run_report.json"]
    paths.extend(sorted((case_dir / "steadyInitialization/history").glob("run_*/pyfoam_run_report.json")))
    total = 0.0
    found = False
    for path in paths:
        report = read_json(path)
        try:
            value = float(report["wall_time_s"])
        except (KeyError, TypeError, ValueError):
            continue
        total += value
        found = True
    return total if found else None


def experimental_reference(root: Path) -> dict[str, float]:
    reference = root / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016"
    cl_table = pd.read_csv(reference / "cl_alpha_digitized.csv")
    exp_cl = cl_table[cl_table["series"] == "Experimental"].sort_values("alpha_deg")
    cl = float(np.interp(ALPHA, exp_cl["alpha_deg"], exp_cl["Cl"]))
    cd_table = pd.read_csv(reference / "cd_cl_digitized.csv")
    exp_cd = cd_table[cd_table["series"] == "Experimental"].sort_values("Cl")
    cd = float(np.interp(cl, exp_cd["Cl"], exp_cd["Cd"]))
    return {"Cl": cl, "Cd": cd, "Cl_over_Cd": cl / cd}


def collect_level(root: Path, level: str, variant: str) -> tuple[dict[str, Any], pd.DataFrame | None]:
    safe = "alpha_p8p000"
    mesh_report = read_json(root / "CFD_2D/meshes" / variant / "mesh_quality_report.json")
    case_dir = root / "CFD_2D/openfoam_cases" / variant / safe
    result_dir = root / "CFD_2D/results" / variant / safe
    summary = read_json(result_dir / "case_summary.json")
    staged = read_json(case_dir / "staged_run_status.json")
    run = read_json(case_dir / "run_status.json")
    row: dict[str, Any] = {
        "level": level,
        "variant": variant,
        "cell_count": mesh_report.get("checkMesh_cell_count"),
        "mesh_quality_status": mesh_report.get("status"),
        "checkMesh_status": mesh_report.get("checkMesh_status"),
        "max_non_orthogonality_deg": mesh_report.get("checkMesh_max_non_orthogonality_deg"),
        "max_skewness": mesh_report.get("checkMesh_max_skewness"),
        "solver_stage_status": staged.get("status"),
        "solver_status": run.get("status"),
        "postprocess_status": summary.get("status"),
        "case_dir": str(case_dir),
        "result_dir": str(result_dir),
        "wall_time_total_s": total_wall_time_s(case_dir),
    }
    times = numeric_times(case_dir)
    case_config = read_json(case_dir / "case_config.json")
    if times:
        try:
            row["simulated_time_star"] = max(times) * float(case_config["velocity_m_s"]) / float(case_config["chord_m"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            row["simulated_time_star"] = None
    mean_path = result_dir / "forceCoeffs_mean.csv"
    if mean_path.is_file():
        try:
            mean = pd.read_csv(mean_path).iloc[0]
            for key in ("Cl", "Cd", "Cm"):
                row[key] = float(mean[key])
            row["Cl_over_Cd"] = row["Cl"] / row["Cd"] if abs(row["Cd"]) > 1e-15 else math.nan
        except (KeyError, IndexError, ValueError, pd.errors.EmptyDataError):
            pass
    row["result_scope"] = (
        "FINAL_TRANSIENT"
        if str(staged.get("status", "")).upper() == "TRANSIENT_STAGE_FINISHED"
        and str(run.get("status", "")).upper() in {"RUN_COMPLETED", "CONVERGED_STATISTICALLY"}
        else "PROVISIONAL_OR_INCOMPLETE" if "Cl" in row
        else "NOT_SIMULATED"
    )
    cp_path = result_dir / "wall_cp_vs_xc.csv"
    cp = None
    if cp_path.is_file():
        try:
            candidate = pd.read_csv(cp_path)
            if not candidate.empty:
                cp = candidate
        except pd.errors.EmptyDataError:
            pass
    return row, cp


def plot_mesh_quality(table: pd.DataFrame, output: Path) -> None:
    valid = table.dropna(subset=["cell_count"])
    if valid.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(12.0, 3.8), constrained_layout=True)
    axes[0].bar(valid["level"], valid["cell_count"], color=["#6a9fb5", "#337a78", "#d07c3e"][: len(valid)])
    axes[0].set_ylabel("OpenFOAM cells")
    axes[1].plot(valid["cell_count"], valid["max_non_orthogonality_deg"], marker="o")
    axes[1].set(xlabel="cells", ylabel="max non-orthogonality [deg]")
    axes[2].plot(valid["cell_count"], valid["max_skewness"], marker="o")
    axes[2].set(xlabel="cells", ylabel="max skewness")
    for axis in axes:
        axis.grid(True, alpha=0.25)
    save_scientific_figure(
        fig, output, data=valid,
        metadata={"source": "OpenFOAM mesh-quality reports", "grouping": "mesh refinement level"},
    )


def plot_coefficients(table: pd.DataFrame, output: Path) -> None:
    usable = table.dropna(subset=["cell_count", "Cl", "Cd", "Cm", "Cl_over_Cd"])
    if len(usable) < 2:
        return
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.2), constrained_layout=True)
    for axis, key, label in zip(
        axes.flat,
        ("Cl", "Cd", "Cm", "Cl_over_Cd"),
        (r"$C_L$ [-]", r"$C_D$ [-]", r"$C_M$ [-]", r"$C_L/C_D$ [-]"),
    ):
        axis.plot(usable["cell_count"], usable[key], marker="o")
        for _, row in usable.iterrows():
            axis.annotate(str(row["level"]), (row["cell_count"], row[key]), xytext=(4, 4), textcoords="offset points")
        axis.set(xlabel=r"Cell count, $N$ [-]", ylabel=label)
        axis.grid(True, alpha=0.25)
    save_scientific_figure(
        fig, output, data=usable,
        metadata={"source": "completed LS(1)-0417 refinement cases", "sorting": "cell_count"},
    )


def plot_relative_error(table: pd.DataFrame, reference: dict[str, float], output: Path) -> None:
    usable = table.dropna(subset=["cell_count", "Cl", "Cd", "Cl_over_Cd"])
    if len(usable) < 2:
        return
    fig, ax = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
    for key, label in (("Cl", r"$C_L$"), ("Cd", r"$C_D$"), ("Cl_over_Cd", r"$C_L/C_D$")):
        error = 100.0 * (usable[key] - reference[key]) / abs(reference[key])
        ax.plot(usable["cell_count"], error, marker="o", label=label)
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set(xlabel=r"Cell count, $N$ [-]", ylabel="Relative deviation from digitized experiment [%]")
    ax.grid(True, alpha=0.25)
    ax.legend()
    save_scientific_figure(
        fig, output, data=usable,
        metadata={"source": "completed LS(1)-0417 cases and digitized experiment", "transformation": "signed percent deviation"},
    )


def plot_cp_overlay(cp_tables: list[tuple[str, pd.DataFrame]], output: Path) -> None:
    if len(cp_tables) < 2:
        return
    fig, ax = plt.subplots(figsize=(8.5, 5.2), constrained_layout=True)
    for level, table in cp_tables:
        x_key = next((key for key in ("x_over_c", "x_c", "x/c") if key in table), None)
        cp_key = next((key for key in ("Cp", "cp") if key in table), None)
        surface_key = next((key for key in ("surface", "side") if key in table), None)
        if x_key is None or cp_key is None:
            continue
        groups = table.groupby(surface_key, sort=True) if surface_key else [("wall", table)]
        for surface, group in groups:
            clean = group[[x_key, cp_key]].apply(pd.to_numeric, errors="coerce").dropna()
            clean = clean.groupby(x_key, as_index=False)[cp_key].mean().sort_values(x_key)
            ax.plot(clean[x_key], clean[cp_key], label=f"{level} {surface}")
    ax.invert_yaxis()
    ax.set(xlabel=r"Chordwise position, $x/c$ [-]", ylabel=r"Pressure coefficient, $C_p$ [-]", title=r"Wall pressure distributions at $\alpha=8^{\circ}$")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8, ncol=2)
    save_scientific_figure(
        fig, output,
        data=pd.concat([table.assign(mesh_level=level) for level, table in cp_tables], ignore_index=True),
        metadata={"source": "wall Cp exports", "grouping": "mesh level and wall branch", "sorting": "x/c within each branch", "deduplication": "mean at repeated x/c"},
    )


def plot_runtime(table: pd.DataFrame, output: Path) -> None:
    usable = table.dropna(subset=["cell_count", "wall_time_total_s"])
    if usable.empty:
        return
    has_time_star = "simulated_time_star" in usable and usable["simulated_time_star"].notna().any()
    fig, axes = plt.subplots(1, 2 if has_time_star else 1, figsize=(10.6 if has_time_star else 8.2, 4.8), constrained_layout=True)
    axes_array = np.atleast_1d(axes)
    left = axes_array[0]
    left.plot(usable["cell_count"], usable["wall_time_total_s"], marker="o")
    left.set(xlabel=r"Cell count, $N$ [-]", ylabel="Total wall time [s]", title="Computational cost")
    left.grid(True, alpha=0.25)
    if has_time_star:
        right = axes_array[1]
        right.plot(
            usable["cell_count"],
            usable["simulated_time_star"],
            marker="s",
            color="#b34b3f",
            label=r"Simulated $t^*$",
        )
        right.set(xlabel=r"Cell count, $N$ [-]", ylabel=r"Simulated time, $t^*$ [-]", title="Physical-time coverage")
        right.grid(True, alpha=0.25)
    save_scientific_figure(
        fig, output, data=usable,
        metadata={"source": "solver run reports", "transformation": "sum of steady and transient wall times"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = root / "Results/LS1_0417_alpha8_mesh_refinement/Analysis"
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    cp_tables: list[tuple[str, pd.DataFrame]] = []
    for level, variant in LEVELS:
        row, cp = collect_level(root, level, variant)
        rows.append(row)
        if cp is not None:
            cp_tables.append((level, cp))
    table = pd.DataFrame(rows)
    table.to_csv(output / "mesh_refinement_results.csv", index=False)
    reference = experimental_reference(root)
    plot_mesh_quality(table, output / "mesh_cell_count_and_quality.png")
    plot_coefficients(table, output / "coefficients_vs_cell_count.png")
    plot_relative_error(table, reference, output / "relative_deviation_vs_experiment.png")
    plot_cp_overlay(cp_tables, output / "cp_xc_mesh_overlay.png")
    plot_runtime(table, output / "runtime_vs_cell_count.png")
    generated = [str(path) for path in sorted(output.glob("*.png"))]
    final_count = int((table["result_scope"] == "FINAL_TRANSIENT").sum())
    report = {
        "status": "COMPLETE" if final_count == 3 else "WAITING_FOR_SIMULATIONS",
        "alpha_deg": ALPHA,
        "experimental_reference_digitized": reference,
        "final_transient_levels": final_count,
        "provisional_levels": int((table["result_scope"] == "PROVISIONAL_OR_INCOMPLETE").sum()),
        "not_simulated_levels": int((table["result_scope"] == "NOT_SIMULATED").sum()),
        "generated_plots": generated,
        "withheld_plots": [
            name for name in (
                "coefficients_vs_cell_count.png",
                "relative_deviation_vs_experiment.png",
                "cp_xc_mesh_overlay.png",
            )
            if not (output / name).is_file()
        ],
        "note": "Withheld plots require at least two real result sets. No placeholder aerodynamic values are generated.",
    }
    (output / "analysis_status.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
