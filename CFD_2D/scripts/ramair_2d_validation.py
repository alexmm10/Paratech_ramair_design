#!/usr/bin/env python3
"""Build LS(1)-0417 validation overlays from reference and real CFD results."""
from __future__ import annotations

import argparse
import json
import math
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


REFERENCE_REYNOLDS = 1.9e6
REFERENCE_MACH = 0.15
VALIDATION_POINT_COLUMNS = [
    "alpha_deg", "Cl", "Cd", "Cm", "L_D", "reynolds", "mach",
    "velocity_source", "ddt_scheme", "result_dir", "status",
    "solver_status", "staged_status", "validation_eligible", "eligibility_reason", "updated_at",
]
IGNORED_POINT_COLUMNS = [
    *VALIDATION_POINT_COLUMNS,
    "result", "reason", "relative_reynolds_error", "absolute_mach_error",
]


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def read_csv_or_empty(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read a CSV while treating a zero-byte/headerless file as an empty table."""
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns or [])
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame(columns=columns or [])
    if columns:
        for column in columns:
            if column not in frame:
                frame[column] = pd.Series(dtype="object")
    return frame


def project_root(path: Path) -> Path:
    path = path.resolve()
    if path.name == "CFD_2D":
        return path.parent
    if path.name in {"results", "openfoam_cases", "reference_data"} and path.parent.name == "CFD_2D":
        return path.parent.parent
    return path


def _result_record(mean_path: Path, case_config_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read one postprocessed alpha without inventing missing coefficients."""
    cfg = read_json(case_config_path, {}) or {}
    summary = read_json(mean_path.parent / "case_summary.json", {}) or {}
    try:
        mean = pd.read_csv(mean_path).iloc[0]
        alpha = float(cfg.get("alpha_deg", summary.get("alpha_deg")))
        reynolds = float(cfg.get("reynolds"))
        mach = float(cfg.get("mach_input"))
        cl = float(mean["Cl"])
        cd = float(mean["Cd"])
        cm = float(mean["Cm"])
    except (KeyError, TypeError, ValueError, IndexError, pd.errors.EmptyDataError) as exc:
        return None, f"unreadable_metadata_or_coefficients: {exc}"
    summary_status = str(summary.get("status", "UNKNOWN")).upper()
    run_status = summary.get("run_status") or {}
    solver_status = str(run_status.get("status", "")).upper()
    case_dir = case_config_path.parent
    staged_status = read_json(case_dir / "staged_run_status.json", {}) or {}
    staged_outcome = str(staged_status.get("status", "")).upper()
    transient_complete = staged_outcome in {
        "TRANSIENT_STAGE_FINISHED",
        "TRANSIENT_STAGE_CONVERGED",
    }
    has_staged_workflow = bool(staged_status)
    solver_complete = solver_status in {
        "CONVERGED_STATISTICALLY",
        "RUN_COMPLETED",
    }
    validation_eligible = solver_complete and (transient_complete or not has_staged_workflow)
    eligibility_reason = (
        "ELIGIBLE"
        if validation_eligible
        else (
            f"summary={summary_status}; solver={solver_status or 'UNKNOWN'}; "
            f"staged={staged_outcome or 'NOT_STAGED'}; a completed physical duration is required"
        )
    )
    return {
        "alpha_deg": alpha,
        "Cl": cl,
        "Cd": cd,
        "Cm": cm,
        "L_D": cl / cd if abs(cd) > 1.0e-15 else float("nan"),
        "reynolds": reynolds,
        "mach": mach,
        "velocity_source": cfg.get("velocity_source"),
        "ddt_scheme": cfg.get("ddt_scheme", "legacy_or_unspecified"),
        "result_dir": str(mean_path.parent),
        "status": summary_status,
        "solver_status": solver_status or "UNKNOWN",
        "staged_status": staged_outcome or "NOT_STAGED",
        "validation_eligible": validation_eligible,
        "eligibility_reason": eligibility_reason,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, None


def collect_ramair_points(
    root: Path,
    reynolds_tolerance_fraction: float = 0.01,
    mach_tolerance: float = 0.005,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    accepted: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    results_root = root / "CFD_2D" / "results" / "reference_uncut"
    cases_root = root / "CFD_2D" / "openfoam_cases" / "reference_uncut"
    for mean_path in sorted(results_root.glob("alpha_*/forceCoeffs_mean.csv")):
        alpha_dir = mean_path.parent.name
        record, error = _result_record(mean_path, cases_root / alpha_dir / "case_config.json")
        if record is None:
            ignored.append({"result": str(mean_path), "reason": error})
            continue
        re_error = abs(float(record["reynolds"]) - REFERENCE_REYNOLDS) / REFERENCE_REYNOLDS
        mach_error = abs(float(record["mach"]) - REFERENCE_MACH)
        if not bool(record.get("validation_eligible")):
            ignored.append(record | {"reason": "result_not_validation_eligible"})
        elif re_error <= reynolds_tolerance_fraction and mach_error <= mach_tolerance:
            accepted.append(record)
        else:
            ignored.append(
                record
                | {
                    "reason": "reference_conditions_mismatch",
                    "relative_reynolds_error": re_error,
                    "absolute_mach_error": mach_error,
                }
            )
    accepted_df = (
        pd.DataFrame(accepted, columns=VALIDATION_POINT_COLUMNS).sort_values("alpha_deg")
        if accepted
        else pd.DataFrame(columns=VALIDATION_POINT_COLUMNS)
    )
    ignored_df = pd.DataFrame(ignored)
    for column in IGNORED_POINT_COLUMNS:
        if column not in ignored_df:
            ignored_df[column] = pd.Series(dtype="object")
    return accepted_df, ignored_df


def _style_reference(ax: plt.Axes, data: pd.DataFrame, x: str, y: str) -> None:
    styles = {
        "Experimental": dict(color="black", marker="o", linestyle="none", markersize=4.0),
        "CFD Cobalt": dict(color="#d62728", marker="s", linestyle="-", linewidth=1.2, markersize=3.5),
        "CFD Kestrel": dict(color="#2455d6", marker="s", linestyle="-", linewidth=1.2, markersize=3.5),
    }
    for series, group in data.groupby("series", sort=False):
        ax.plot(group[x], group[y], label=series, **styles.get(series, {}))


def validation_percentage_errors(
    ramair: pd.DataFrame,
    cl_alpha: pd.DataFrame,
    cd_cl: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Interpolate experimental Cl(alpha), Cd(Cl) and compare every accepted CFD point."""
    columns = [
        "alpha_deg",
        "Cl_sim", "Cl_exp", "Cl_abs_percentage_error",
        "Cd_sim", "Cd_exp", "Cd_abs_percentage_error",
        "Cl_over_Cd_sim", "Cl_over_Cd_exp", "Cl_over_Cd_abs_percentage_error",
    ]
    if ramair.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(
            columns=["coefficient", "maximum_abs_percentage_error", "alpha_deg"]
        )

    lift_reference = (
        cl_alpha[cl_alpha["series"] == "Experimental"][["alpha_deg", "Cl"]]
        .dropna()
        .sort_values("alpha_deg")
        .drop_duplicates("alpha_deg")
    )
    drag_reference = (
        cd_cl[cd_cl["series"] == "Experimental"][["Cl", "Cd"]]
        .dropna()
        .sort_values("Cl")
        .drop_duplicates("Cl")
    )

    def interpolate_inside(x: float, xp: np.ndarray, fp: np.ndarray) -> float:
        if not len(xp) or x < float(xp[0]) or x > float(xp[-1]):
            return float("nan")
        return float(np.interp(x, xp, fp))

    def percentage_error(actual: float, reference: float) -> float:
        if not math.isfinite(actual) or not math.isfinite(reference) or abs(reference) <= 1.0e-12:
            return float("nan")
        return 100.0 * abs(actual - reference) / abs(reference)

    lift_alpha = lift_reference["alpha_deg"].to_numpy(dtype=float)
    lift_cl = lift_reference["Cl"].to_numpy(dtype=float)
    drag_cl = drag_reference["Cl"].to_numpy(dtype=float)
    drag_cd = drag_reference["Cd"].to_numpy(dtype=float)
    rows: list[dict[str, float]] = []
    for _, point in ramair.sort_values("alpha_deg").iterrows():
        alpha = float(point["alpha_deg"])
        cl_sim = float(point["Cl"])
        cd_sim = float(point["Cd"])
        cl_exp = interpolate_inside(alpha, lift_alpha, lift_cl)
        cd_exp = interpolate_inside(cl_exp, drag_cl, drag_cd) if math.isfinite(cl_exp) else float("nan")
        ld_sim = cl_sim / cd_sim if abs(cd_sim) > 1.0e-12 else float("nan")
        ld_exp = cl_exp / cd_exp if math.isfinite(cd_exp) and abs(cd_exp) > 1.0e-12 else float("nan")
        rows.append({
            "alpha_deg": alpha,
            "Cl_sim": cl_sim,
            "Cl_exp": cl_exp,
            "Cl_abs_percentage_error": percentage_error(cl_sim, cl_exp),
            "Cd_sim": cd_sim,
            "Cd_exp": cd_exp,
            "Cd_abs_percentage_error": percentage_error(cd_sim, cd_exp),
            "Cl_over_Cd_sim": ld_sim,
            "Cl_over_Cd_exp": ld_exp,
            "Cl_over_Cd_abs_percentage_error": percentage_error(ld_sim, ld_exp),
        })
    errors = pd.DataFrame(rows, columns=columns)
    summary_rows: list[dict[str, float | str]] = []
    for coefficient, column in (
        ("Cl", "Cl_abs_percentage_error"),
        ("Cd", "Cd_abs_percentage_error"),
        ("Cl/Cd", "Cl_over_Cd_abs_percentage_error"),
    ):
        finite = errors[np.isfinite(pd.to_numeric(errors[column], errors="coerce"))]
        if finite.empty:
            summary_rows.append({
                "coefficient": coefficient,
                "maximum_abs_percentage_error": float("nan"),
                "alpha_deg": float("nan"),
            })
            continue
        worst_index = finite[column].astype(float).idxmax()
        summary_rows.append({
            "coefficient": coefficient,
            "maximum_abs_percentage_error": float(finite.loc[worst_index, column]),
            "alpha_deg": float(finite.loc[worst_index, "alpha_deg"]),
        })
    return errors, pd.DataFrame(summary_rows)


def write_validation_error_products(
    output: Path,
    ramair: pd.DataFrame,
    cl_alpha: pd.DataFrame,
    cd_cl: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    errors, maximums = validation_percentage_errors(ramair, cl_alpha, cd_cl)
    errors.to_csv(output / "validation_percentage_errors.csv", index=False)
    maximums.to_csv(output / "validation_max_percentage_error_summary.csv", index=False)

    fig, ax = plt.subplots(figsize=(6.6, 3.8), constrained_layout=True)
    finite = maximums[np.isfinite(pd.to_numeric(
        maximums["maximum_abs_percentage_error"], errors="coerce"
    ))]
    if finite.empty:
        ax.text(0.5, 0.5, "No comparable RamAir angles yet", ha="center", va="center")
    else:
        bars = ax.bar(
            finite["coefficient"],
            finite["maximum_abs_percentage_error"],
            color=["#2878b5", "#d95f02", "#14866d"][: len(finite)],
        )
        ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=8)
    ax.set_ylabel("Maximum absolute difference [%]")
    ax.set_title("RamAir vs experimental: worst analyzed angle")
    ax.grid(True, axis="y", alpha=0.25)
    save_scientific_figure(
        fig,
        output / "validation_max_percentage_error_summary.png",
        data=maximums,
        metadata={"source": "RamAir and digitized experimental validation curves", "transformation": "maximum absolute percentage error by metric"},
    )
    return errors, maximums


def generate_validation_report(
    case_root: Path,
    output_dir: Path | None = None,
    reynolds_tolerance_fraction: float = 0.01,
    mach_tolerance: float = 0.005,
    ramair_points: pd.DataFrame | None = None,
    ignored_points: pd.DataFrame | None = None,
) -> Path:
    root = project_root(case_root)
    reference = root / "CFD_2D" / "reference_data" / "LS1_0417_Ghoreyshi_2016"
    output = output_dir or (root / "CFD_2D" / "results" / "validation" / "LS1_0417_M0p15_Re1p9e6")
    output.mkdir(parents=True, exist_ok=True)
    cl_alpha = pd.read_csv(reference / "cl_alpha_digitized.csv")
    cd_cl = pd.read_csv(reference / "cd_cl_digitized.csv")
    if ramair_points is None:
        ramair, ignored = collect_ramair_points(root, reynolds_tolerance_fraction, mach_tolerance)
    else:
        ramair = ramair_points.copy()
        ignored = ignored_points.copy() if ignored_points is not None else pd.DataFrame()
        if not ramair.empty and "alpha_deg" in ramair:
            ramair = ramair.sort_values("alpha_deg")
    ramair = ramair.reindex(columns=VALIDATION_POINT_COLUMNS)
    for column in IGNORED_POINT_COLUMNS:
        if column not in ignored:
            ignored[column] = pd.Series(dtype="object")
    ignored = ignored.reindex(columns=IGNORED_POINT_COLUMNS)
    ramair.to_csv(output / "ramair_validation_points.csv", index=False)
    ignored.to_csv(output / "ignored_nonmatching_results.csv", index=False)

    fig, ax = plt.subplots(figsize=(7.2, 4.6), constrained_layout=True)
    _style_reference(ax, cl_alpha, "alpha_deg", "Cl")
    if not ramair.empty:
        ax.plot(ramair["alpha_deg"], ramair["Cl"], color="#008b5a", marker="D", linewidth=1.4, label="RamAir OpenFOAM")
    else:
        ax.text(0.02, 0.04, "No matching RamAir results yet", transform=ax.transAxes, fontsize=9, color="#555555")
    ax.set(xlabel=r"Angle of attack, $\alpha$ [deg]", ylabel=r"Lift coefficient, $C_L$ [-]", title="LS(1)-0417 validation: lift curve")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best", fontsize=8)
    lift_plot_data = pd.concat([
        cl_alpha.assign(dataset="reference"),
        ramair.assign(dataset="RamAir"),
    ], ignore_index=True, sort=False)
    save_scientific_figure(
        fig,
        output / "LS1_0417_CL_alpha_validation.png",
        data=lift_plot_data,
        metadata={"source": "Ghoreyshi et al. digitized reference and explicitly published RamAir points", "grouping": "dataset"},
    )

    fig, ax = plt.subplots(figsize=(7.2, 4.6), constrained_layout=True)
    _style_reference(ax, cd_cl, "Cl", "Cd")
    if not ramair.empty:
        ax.plot(ramair["Cl"], ramair["Cd"], color="#008b5a", marker="D", linewidth=1.4, label="RamAir OpenFOAM")
    else:
        ax.text(0.02, 0.04, "No matching RamAir results yet", transform=ax.transAxes, fontsize=9, color="#555555")
    ax.set(xlabel=r"Lift coefficient, $C_L$ [-]", ylabel=r"Drag coefficient, $C_D$ [-]", title="LS(1)-0417 validation: drag polar")
    ax.grid(True, alpha=0.28)
    ax.legend(loc="best", fontsize=8)
    drag_plot_data = pd.concat([
        cd_cl.assign(dataset="reference"),
        ramair.assign(dataset="RamAir"),
    ], ignore_index=True, sort=False)
    save_scientific_figure(
        fig,
        output / "LS1_0417_CD_CL_validation.png",
        data=drag_plot_data,
        metadata={"source": "Ghoreyshi et al. digitized reference and explicitly published RamAir points", "grouping": "dataset"},
    )
    percentage_errors, maximum_errors = write_validation_error_products(
        output,
        ramair,
        cl_alpha,
        cd_cl,
    )

    manifest = read_json(reference / "reference_manifest.json", {}) or {}
    report = {
        "status": "RAMAir_POINTS_AVAILABLE" if not ramair.empty else "REFERENCE_ONLY",
        "reference_dataset": manifest,
        "required_conditions": {"reynolds": REFERENCE_REYNOLDS, "mach": REFERENCE_MACH},
        "matching_tolerances": {
            "reynolds_fraction": reynolds_tolerance_fraction,
            "mach_absolute": mach_tolerance,
        },
        "ramair_points_included": int(len(ramair)),
        "results_ignored": int(len(ignored)),
        "percentage_error_points": int(len(percentage_errors)),
        "maximum_absolute_percentage_errors": {
            str(row["coefficient"]): (
                None
                if pd.isna(row["maximum_abs_percentage_error"])
                else {
                    "value_percent": float(row["maximum_abs_percentage_error"]),
                    "alpha_deg": float(row["alpha_deg"]),
                }
            )
            for _, row in maximum_errors.iterrows()
        },
        "experimental_error_method": (
            "Cl_exp is linearly interpolated versus alpha from the digitized experimental lift curve; "
            "Cd_exp is then interpolated versus Cl_exp from the digitized experimental drag polar; "
            "Cl/Cd_exp is derived from those two values. Angles outside the digitized range are omitted."
        ),
        "physics_scope": (
            "The current incompressible SA-RANS workflow is a low-Mach baseline. At M=0.15 it does not exactly reproduce "
            "the compressible Cobalt/Kestrel formulations, and this limitation must be retained in validation conclusions."
        ),
    }
    (output / "validation_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def active_validation_workspace(root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Resolve a validation-enabled Results workspace selected in the app."""
    active = read_json(root / "CFD_2D/app_state/active_workspace.json", {}) or {}
    case_name = str(active.get("case") or "").strip()
    if not case_name:
        return None
    workspace = root / "Results" / case_name
    manifest = read_json(workspace / "case_manifest.json", {}) or {}
    validation = manifest.get("validation") or {}
    if not isinstance(validation, dict) or not validation.get("enabled"):
        return None
    return workspace, manifest


def update_active_workspace_validation(
    case_root: Path,
    variant: str,
    alpha: float,
    reynolds_tolerance_fraction: float = 0.01,
    mach_tolerance: float = 0.005,
) -> Path | None:
    """Add one real result to the selected validation work case and redraw it."""
    root = project_root(case_root)
    resolved = active_validation_workspace(root)
    if resolved is None:
        return None
    workspace, manifest = resolved
    validation = manifest.get("validation") or {}
    required_variant = str(validation.get("variant") or manifest.get("variant") or "reference_uncut")
    if variant != required_variant:
        return None

    alpha_dir = f"alpha_{float(alpha):+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    mean_path = root / "CFD_2D/results" / variant / alpha_dir / "forceCoeffs_mean.csv"
    config_path = root / "CFD_2D/openfoam_cases" / variant / alpha_dir / "case_config.json"
    if not mean_path.is_file():
        return None
    record, error = _result_record(mean_path, config_path)
    output = workspace / "Validation"
    output.mkdir(parents=True, exist_ok=True)
    points_path = output / "ramair_validation_points.csv"
    ignored_path = output / "ignored_nonmatching_results.csv"
    points = read_csv_or_empty(points_path, VALIDATION_POINT_COLUMNS)
    ignored = read_csv_or_empty(ignored_path, IGNORED_POINT_COLUMNS)

    accepted = False
    if record is not None:
        re_error = abs(float(record["reynolds"]) - REFERENCE_REYNOLDS) / REFERENCE_REYNOLDS
        mach_error = abs(float(record["mach"]) - REFERENCE_MACH)
        accepted = (
            bool(record.get("validation_eligible"))
            and re_error <= reynolds_tolerance_fraction
            and mach_error <= mach_tolerance
        )
        if accepted:
            if not points.empty and "alpha_deg" in points:
                points = points[points["alpha_deg"].astype(float) != float(record["alpha_deg"])]
            new_point = pd.DataFrame([record])
            points = (
                new_point.reindex(columns=VALIDATION_POINT_COLUMNS)
                if points.empty
                else pd.concat([points, new_point], ignore_index=True)
            )
        elif not bool(record.get("validation_eligible")):
            record = record | {
                "reason": "result_not_validation_eligible",
                "relative_reynolds_error": re_error,
                "absolute_mach_error": mach_error,
            }
        else:
            record = record | {
                "reason": "reference_conditions_mismatch",
                "relative_reynolds_error": re_error,
                "absolute_mach_error": mach_error,
            }
    else:
        record = {"result": str(mean_path), "reason": error, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not accepted and record is not None:
        new_ignored = pd.DataFrame([record])
        ignored = (
            new_ignored.reindex(columns=IGNORED_POINT_COLUMNS)
            if ignored.empty
            else pd.concat([ignored, new_ignored], ignore_index=True)
        )

    generate_validation_report(
        root,
        output,
        reynolds_tolerance_fraction,
        mach_tolerance,
        ramair_points=points,
        ignored_points=ignored,
    )
    return output


def remove_active_workspace_validation(
    case_root: Path,
    variant: str,
    alpha: float,
    reynolds_tolerance_fraction: float = 0.01,
    mach_tolerance: float = 0.005,
) -> Path | None:
    """Unpublish one angle without deleting its simulation or postprocess."""
    root = project_root(case_root)
    resolved = active_validation_workspace(root)
    if resolved is None:
        return None
    workspace, manifest = resolved
    validation = manifest.get("validation") or {}
    required_variant = str(validation.get("variant") or manifest.get("variant") or "reference_uncut")
    if variant != required_variant:
        return None
    output = workspace / "Validation"
    output.mkdir(parents=True, exist_ok=True)
    points_path = output / "ramair_validation_points.csv"
    ignored_path = output / "ignored_nonmatching_results.csv"
    points = read_csv_or_empty(points_path, VALIDATION_POINT_COLUMNS)
    if not points.empty and "alpha_deg" in points:
        alpha_values = pd.to_numeric(points["alpha_deg"], errors="coerce")
        points = points[~np.isclose(alpha_values, float(alpha), rtol=0.0, atol=1.0e-9)]
    ignored = read_csv_or_empty(ignored_path, IGNORED_POINT_COLUMNS)
    generate_validation_report(
        root,
        output,
        reynolds_tolerance_fraction,
        mach_tolerance,
        ramair_points=points,
        ignored_points=ignored,
    )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--reynolds-tolerance-fraction", type=float, default=0.01)
    parser.add_argument("--mach-tolerance", type=float, default=0.005)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = generate_validation_report(
        args.case_root,
        args.output_dir,
        args.reynolds_tolerance_fraction,
        args.mach_tolerance,
    )
    print(f"Validation report: {output.resolve()}")


if __name__ == "__main__":
    main()
