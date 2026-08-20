#!/usr/bin/env python3
"""Design a ram-air leading-edge opening from XFOIL stagnation points.

XFLR5 embeds XFOIL, but its desktop GUI is not a stable automation API.  This
module therefore drives the documented XFOIL command interface directly and
writes files that can still be inspected in XFLR5.  Only converged polar rows
are admitted to the stagnation envelope; missing or malformed Cp files fail
the design instead of producing a plausible-looking placeholder profile.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ramair_profile_utils import read_and_canonicalize_profile_2d


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "base_profile": "Airfoil Profiles/NASA LS1-0417.dat",
    "design_mode": "optimized_cl_window",
    "reynolds": 4000000.0,
    "mach": 0.10,
    "panel_count": 200,
    "panel_bunching": 1.0,
    "te_le_density_ratio": 0.15,
    "refined_area_le_density_ratio": 0.20,
    "alpha_start_deg": -5.0,
    "alpha_end_deg": 15.0,
    "alpha_step_deg": 0.5,
    "cl_min": 0.5,
    "cl_max": 1.5,
    "xfoil_iteration_limit": 180,
    "xfoil_timeout_s": 420,
    "stagnation_search_x_over_c": 0.18,
    "minimum_stagnation_cp": 0.70,
    "cut_margin_panel_points": 1,
    "existing_output_action": "archive",
}


@dataclass(frozen=True)
class XfoilInstallation:
    executable: str | None
    source: str
    version: str | None
    status: str
    detail: str


def _json_read(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "Airfoil Profiles").is_dir() and (candidate / "CFD_2D").is_dir():
            return candidate
    raise FileNotFoundError("Could not locate the project root containing Airfoil Profiles/ and CFD_2D/")


def _candidate_xfoil_paths(project_root: Path) -> Iterable[tuple[Path, str]]:
    requested = os.environ.get("RAMAIR_XFOIL_EXECUTABLE")
    if requested:
        yield Path(requested).expanduser(), "RAMAIR_XFOIL_EXECUTABLE"
    for name in ("xfoil", "xfoil.exe"):
        found = shutil.which(name)
        if found:
            yield Path(found), "PATH"
    for relative in (
        "Application Support/Tools/xfoil/bin/xfoil",
        "Application Support/Tools/xfoil/bin/xfoil.exe",
        "Application Support/Tools/xfoil/linux/xfoil",
        "Application Support/Tools/xfoil/windows/xfoil.exe",
    ):
        yield project_root / relative, "project tools"
    for ancestor in [project_root, *project_root.parents]:
        yield ancestor / "XFOIL6.99/xfoil.exe", "legacy XFOIL6.99"
        yield ancestor / "XFOIL/xfoil.exe", "legacy XFOIL"
    if os.environ.get("WSL_DISTRO_NAME"):
        users_root = Path("/mnt/c/Users")
        if users_root.is_dir():
            for candidate in users_root.glob("*/Desktop/PRACTICAS_INVICSA/XFOIL6.99/xfoil.exe"):
                yield candidate, "Windows executable through WSL interop"


def _run_xfoil_process(executable: Path, command_text: str, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(executable)],
        input=command_text,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(5, int(timeout_s)),
        check=False,
    )


def inspect_xfoil(project_root: Path | None = None, timeout_s: int = 15) -> XfoilInstallation:
    root = (project_root or find_project_root()).resolve()
    seen: set[str] = set()
    failures: list[str] = []
    for candidate, source in _candidate_xfoil_paths(root):
        text_path = str(candidate)
        if text_path in seen or not candidate.is_file():
            continue
        seen.add(text_path)
        try:
            with tempfile.TemporaryDirectory(prefix="ramair_xfoil_probe_") as raw_tmp:
                result = _run_xfoil_process(candidate, "\nQUIT\n", Path(raw_tmp), timeout_s)
            output = result.stdout or ""
            match = re.search(r"XFOIL\s+(?:Version\s+)?(\d+(?:\.\d+)*)", output, re.IGNORECASE)
            if result.returncode == 0 and match:
                return XfoilInstallation(str(candidate.resolve()), source, match.group(1), "OK", "XFOIL command interface responded")
            failures.append(f"{candidate}: exit {result.returncode}")
        except Exception as exc:
            failures.append(f"{candidate}: {type(exc).__name__}: {exc}")
    detail = "XFOIL was not found. Install it or set RAMAIR_XFOIL_EXECUTABLE."
    if failures:
        detail += " Probes: " + "; ".join(failures[:4])
    return XfoilInstallation(None, "none", None, "MISSING", detail)


def load_design_config(path: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    loaded = _json_read(path, {}) or {}
    if not isinstance(loaded, dict):
        raise TypeError(f"The inlet design configuration must be a JSON object: {path}")
    config.update(loaded)
    validate_design_config(config)
    return config


def validate_design_config(config: dict[str, Any]) -> None:
    mode = str(config.get("design_mode", ""))
    if mode not in {"standard_full_polar", "optimized_cl_window"}:
        raise ValueError("design_mode must be standard_full_polar or optimized_cl_window")
    if float(config["reynolds"]) <= 0 or not 0 <= float(config["mach"]) < 0.8:
        raise ValueError("Reynolds must be positive and this incompressible XFOIL workflow requires 0 <= Mach < 0.8")
    if not 80 <= int(config["panel_count"]) <= 400:
        raise ValueError("panel_count must be between 80 and 400 (XFOIL 6.99 panel-array limit)")
    if float(config["alpha_step_deg"]) <= 0 or float(config["alpha_end_deg"]) < float(config["alpha_start_deg"]):
        raise ValueError("The alpha interval and positive alpha step are inconsistent")
    if float(config["cl_max"]) < float(config["cl_min"]):
        raise ValueError("cl_max must be greater than or equal to cl_min")
    if not 0.01 <= float(config["stagnation_search_x_over_c"]) <= 0.40:
        raise ValueError("stagnation_search_x_over_c must lie between 0.01 and 0.40")


def _profile_to_xfoil_dat(profile_path: Path, destination: Path) -> dict[str, Any]:
    canonical = read_and_canonicalize_profile_2d(profile_path, "reference_uncut", has_inlet=False)
    if canonical.errors:
        raise ValueError("Invalid closed base profile: " + "; ".join(canonical.errors))
    upper = canonical.upper[["x_norm", "z_norm"]].iloc[::-1].reset_index(drop=True)
    lower = canonical.lower[["x_norm", "z_norm"]].reset_index(drop=True)
    if np.linalg.norm(upper.iloc[-1].to_numpy(float) - lower.iloc[0].to_numpy(float)) < 1.0e-8:
        lower = lower.iloc[1:].reset_index(drop=True)
    contour = pd.concat([upper, lower], ignore_index=True)
    if len(contour) < 20:
        raise ValueError("Base profile has fewer than 20 usable contour points")
    lines = ["RAMAIR_BASE_PROFILE"] + [f"{row.x_norm:.12g} {row.z_norm:.12g}" for row in contour.itertuples()]
    destination.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {
        "source_profile": str(profile_path),
        "source_points": int(len(contour)),
        "source_profile_report": canonical.report,
    }


def _repanel_commands(config: dict[str, Any]) -> str:
    return "\n".join([
        "PLOP", "G F", "", "LOAD base_profile.dat", "PPAR",
        f"N {int(config['panel_count'])}",
        f"P {float(config['panel_bunching']):.8g}",
        f"T {float(config['te_le_density_ratio']):.8g}",
        f"R {float(config['refined_area_le_density_ratio']):.8g}",
        "", "", "PANE", "SAVE repanelled_profile.dat", "", "QUIT", "",
    ])


def alpha_values(config: dict[str, Any]) -> list[float]:
    start = float(config["alpha_start_deg"])
    end = float(config["alpha_end_deg"])
    step = float(config["alpha_step_deg"])
    count = int(math.floor((end - start) / step + 0.5)) + 1
    values = [start + index * step for index in range(max(1, count))]
    if values[-1] < end - 1.0e-8:
        values.append(end)
    return [round(value, 8) for value in values]


def _polar_commands(config: dict[str, Any]) -> str:
    values = alpha_values(config)
    return "\n".join([
        "PLOP", "G F", "", "LOAD repanelled_profile.dat", "", "OPER",
        f"VISC {float(config['reynolds']):.12g}",
        f"MACH {float(config['mach']):.12g}",
        f"ITER {int(config['xfoil_iteration_limit'])}",
        "PACC", "polar.txt", "",
        f"ASEQ {values[0]:.8g} {values[-1]:.8g} {float(config['alpha_step_deg']):.8g}",
        "PACC", "", "QUIT", "",
    ])


def parse_polar(path: Path) -> pd.DataFrame:
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            values = [float(value) for value in parts[:7]]
        except ValueError:
            continue
        rows.append(values)
    if not rows:
        raise RuntimeError(f"XFOIL produced no converged polar rows: {path}")
    frame = pd.DataFrame(rows, columns=["alpha_deg", "CL", "CD", "CDp", "CM", "Top_Xtr", "Bot_Xtr"])
    return frame.drop_duplicates(subset=["alpha_deg"], keep="last").sort_values("alpha_deg").reset_index(drop=True)


def _cp_file_name(alpha: float) -> str:
    token = f"{alpha:+.4f}".replace("+", "p").replace("-", "m").replace(".", "p")
    return f"cp_alpha_{token}.dat"


def _cp_commands(config: dict[str, Any], converged_alphas: Iterable[float]) -> str:
    commands = [
        "PLOP", "G F", "", "LOAD repanelled_profile.dat", "", "OPER",
        f"VISC {float(config['reynolds']):.12g}",
        f"MACH {float(config['mach']):.12g}",
        f"ITER {int(config['xfoil_iteration_limit'])}",
    ]
    for alpha in converged_alphas:
        commands.extend([f"ALFA {float(alpha):.8g}", f"CPWR {_cp_file_name(float(alpha))}"])
    commands.extend(["", "QUIT", ""])
    return "\n".join(commands)


def parse_cp(path: Path) -> pd.DataFrame:
    rows: list[tuple[float, float, float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    if len(rows) < 20:
        raise RuntimeError(f"Malformed or incomplete XFOIL Cp output: {path}")
    return pd.DataFrame(rows, columns=["x_over_c", "z_over_c", "Cp"])


def stagnation_point(cp: pd.DataFrame, *, max_x: float, minimum_cp: float) -> dict[str, Any]:
    candidates = cp[np.isfinite(cp["Cp"]) & (cp["x_over_c"] <= max_x)].copy()
    if candidates.empty:
        raise RuntimeError(f"No Cp samples inside the leading-edge search window x/c <= {max_x}")
    index = int(candidates["Cp"].idxmax())
    row = cp.loc[index]
    accepted = bool(float(row.Cp) >= minimum_cp)
    return {
        "cp_row_index": index,
        "x_over_c": float(row.x_over_c),
        "z_over_c": float(row.z_over_c),
        "Cp": float(row.Cp),
        "accepted": accepted,
        "reason": "accepted" if accepted else f"Cp below minimum {minimum_cp}",
    }


def _nearest_branch_index(branch: pd.DataFrame, point: np.ndarray) -> tuple[int, float]:
    coordinates = branch[["x_norm", "z_norm"]].to_numpy(float)
    distances = np.linalg.norm(coordinates - point.reshape(1, 2), axis=1)
    index = int(np.argmin(distances))
    return index, float(distances[index])


def classify_stagnation_points(
    repanelled_profile: Path,
    polar: pd.DataFrame,
    cp_directory: Path,
    config: dict[str, Any],
) -> tuple[Any, list[dict[str, Any]]]:
    canonical = read_and_canonicalize_profile_2d(repanelled_profile, "reference_uncut", has_inlet=False)
    if canonical.errors:
        raise RuntimeError("Repanelled XFOIL profile is invalid: " + "; ".join(canonical.errors))
    points: list[dict[str, Any]] = []
    for row in polar.itertuples(index=False):
        cp_path = cp_directory / _cp_file_name(float(row.alpha_deg))
        if not cp_path.is_file():
            points.append({"alpha_deg": float(row.alpha_deg), "CL": float(row.CL), "accepted": False, "reason": "Cp file missing"})
            continue
        candidate = stagnation_point(
            parse_cp(cp_path),
            max_x=float(config["stagnation_search_x_over_c"]),
            minimum_cp=float(config["minimum_stagnation_cp"]),
        )
        point = np.array([candidate["x_over_c"], candidate["z_over_c"]], dtype=float)
        upper_index, upper_distance = _nearest_branch_index(canonical.upper, point)
        lower_index, lower_distance = _nearest_branch_index(canonical.lower, point)
        if upper_distance <= lower_distance:
            surface, branch_index, mapping_distance = "upper", upper_index, upper_distance
        else:
            surface, branch_index, mapping_distance = "lower", lower_index, lower_distance
        candidate.update({
            "alpha_deg": float(row.alpha_deg), "CL": float(row.CL), "CD": float(row.CD),
            "surface": surface, "branch_index": int(branch_index), "mapping_distance_over_c": float(mapping_distance),
        })
        if mapping_distance > 0.02:
            candidate.update({"accepted": False, "reason": "Cp point could not be mapped reliably to the repanelled profile"})
        points.append(candidate)
    return canonical, points


def select_design_points(points: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    accepted = [point for point in points if point.get("accepted")]
    if str(config["design_mode"]) == "optimized_cl_window":
        cl_min, cl_max = float(config["cl_min"]), float(config["cl_max"])
        accepted = [point for point in accepted if cl_min <= float(point["CL"]) <= cl_max]
    if len(accepted) < 2:
        raise RuntimeError(
            "Fewer than two valid stagnation points remain after convergence/Cp/design-range filtering; "
            "expand the alpha or Cl range and inspect polar.csv"
        )
    return accepted


def build_cut_profile(canonical: Any, selected: list[dict[str, Any]], margin_points: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    margin = max(0, int(margin_points))
    upper_candidates = [int(point["branch_index"]) for point in selected if point["surface"] == "upper"]
    lower_candidates = [int(point["branch_index"]) for point in selected if point["surface"] == "lower"]
    upper_index = max(upper_candidates, default=0)
    lower_index = max(lower_candidates, default=0)
    upper_index = min(max(0, upper_index + margin), len(canonical.upper) - 2)
    lower_index = min(max(0, lower_index + margin), len(canonical.lower) - 2)
    upper = canonical.upper.iloc[upper_index:][["x_norm", "z_norm"]].reset_index(drop=True)
    lower = canonical.lower.iloc[lower_index:][["x_norm", "z_norm"]].reset_index(drop=True)
    upper_lip = upper.iloc[0].to_numpy(float)
    lower_lip = lower.iloc[0].to_numpy(float)
    gap = float(np.linalg.norm(upper_lip - lower_lip))
    if not 0.002 <= gap <= 0.30:
        raise RuntimeError(f"Designed inlet gap {100.0 * gap:.3f}%c is outside the guarded 0.2-30%c range")
    # Conventional DAT/CSV contour order: upper TE -> upper lip, then lower lip -> lower TE.
    output_upper = upper.iloc[::-1].copy()
    output_upper["section"] = "UPPER"
    output_lower = lower.copy()
    output_lower["section"] = "LOWER"
    output = pd.concat([output_upper, output_lower], ignore_index=True)
    output["order"] = np.arange(1, len(output) + 1)
    output["x"] = output["x_norm"]
    output["y"] = output["z_norm"]
    output["z"] = 0.0
    report = {
        "upper_lip_branch_index": int(upper_index),
        "lower_lip_branch_index": int(lower_index),
        "upper_lip": {"x_over_c": float(upper_lip[0]), "z_over_c": float(upper_lip[1])},
        "lower_lip": {"x_over_c": float(lower_lip[0]), "z_over_c": float(lower_lip[1])},
        "inlet_gap_over_c": gap,
        "inlet_gap_percent_chord": 100.0 * gap,
        "output_points": int(len(output)),
    }
    return output[["x", "y", "z", "section", "order", "x_norm", "z_norm"]], report


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return cleaned or "profile"


def _reynolds_token(value: float) -> str:
    reynolds = float(value)
    if abs(reynolds - round(reynolds)) < 1.0e-6:
        return str(int(round(reynolds)))
    return f"{reynolds:.6g}".replace("+", "p").replace(".", "p")


def generated_profile_name(base_profile: Path, config: dict[str, Any], gap_percent: float) -> str:
    mode = "Standard" if config["design_mode"] == "standard_full_polar" else "Optimized"
    re_token = _reynolds_token(float(config["reynolds"]))
    mach_token = f"{float(config['mach']):.3f}".rstrip("0").rstrip(".").replace(".", "p")
    gap_token = f"{gap_percent:.1f}".replace(".", "p")
    return f"{_safe_name(base_profile.stem)}_Cut_{mode}_Re{re_token}_M{mach_token}_Gap{gap_token}pct.csv"


def _archive_or_remove(path: Path, project_root: Path, action: str) -> None:
    if not path.exists():
        return
    if action == "keep":
        raise FileExistsError(f"Output already exists and existing_output_action=keep: {path}")
    if action == "delete":
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return
    if action != "archive":
        raise ValueError("existing_output_action must be archive, delete or keep")
    relative_name = path.name
    destination = project_root / "Previous Versions/inlet_design_backups" / f"{relative_name}_{time.strftime('%Y%m%d_%H%M%S')}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))


def _plot_outputs(output_dir: Path, polar: pd.DataFrame, points: list[dict[str, Any]], selected: list[dict[str, Any]], cut: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].plot(polar["alpha_deg"], polar["CL"], marker="o", markersize=3, linewidth=1)
    selected_pairs = {(round(float(p["alpha_deg"]), 8), round(float(p["CL"]), 8)) for p in selected}
    chosen = polar[[((round(float(r.alpha_deg), 8), round(float(r.CL), 8)) in selected_pairs) for r in polar.itertuples()]]
    if not chosen.empty:
        axes[0].scatter(chosen["alpha_deg"], chosen["CL"], color="#c23b22", s=24, label="Used for cut")
        axes[0].legend()
    axes[0].set(xlabel="alpha [deg]", ylabel="CL", title="Converged XFOIL polar")
    axes[0].grid(True, alpha=0.25)
    axes[1].plot(cut["x"], cut["y"], color="#1f4e79", linewidth=1.2)
    accepted = [p for p in points if p.get("accepted")]
    if accepted:
        axes[1].scatter([p["x_over_c"] for p in accepted], [p["z_over_c"] for p in accepted], c="#777777", s=18, label="Valid stagnation")
    axes[1].scatter([p["x_over_c"] for p in selected], [p["z_over_c"] for p in selected], c="#c23b22", s=28, label="Design envelope")
    axes[1].set(xlabel="x/c", ylabel="z/c", title="Cut profile and stagnation envelope", aspect="equal")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(output_dir / "inlet_design_summary.png", dpi=180)
    plt.close(fig)


def _update_registry(project_root: Path, profile_path: Path, metadata: dict[str, Any]) -> None:
    registry_path = project_root / "Airfoil Profiles/generated_profiles_registry.json"
    registry = _json_read(registry_path, {"version": 1, "profiles": []}) or {"version": 1, "profiles": []}
    current_relative = str(profile_path.relative_to(project_root)).replace("\\", "/")
    records = [
        record for record in registry.get("profiles", [])
        if record.get("path") != current_relative
        and isinstance(record.get("path"), str)
        and (project_root / str(record["path"])).is_file()
    ]
    records.append({
        "path": current_relative,
        "created_at": metadata["created_at"],
        "base_profile": metadata["base_profile"],
        "design_mode": metadata["design_mode"],
        "reynolds": metadata["reynolds"],
        "mach": metadata["mach"],
        "inlet_gap_percent_chord": metadata["cut"]["inlet_gap_percent_chord"],
        "metadata": str(Path(metadata["metadata_path"]).relative_to(project_root)).replace("\\", "/"),
    })
    registry["profiles"] = sorted(records, key=lambda record: record["path"])
    _json_write(registry_path, registry)


def design_inlet(project_root: Path, config_path: Path, executable_override: str | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    config = load_design_config(config_path)
    base_profile = Path(str(config["base_profile"]))
    if not base_profile.is_absolute():
        base_profile = root / base_profile
    if not base_profile.is_file():
        raise FileNotFoundError(f"Base profile not found: {base_profile}")
    installation = inspect_xfoil(root)
    executable = Path(executable_override).expanduser() if executable_override else (Path(installation.executable) if installation.executable else None)
    if executable is None or not executable.is_file():
        raise RuntimeError(installation.detail)

    mode_token = "standard" if config["design_mode"] == "standard_full_polar" else "optimized"
    case_token = (
        f"{_safe_name(base_profile.stem)}_{mode_token}_"
        f"Re{_reynolds_token(float(config['reynolds']))}_M{float(config['mach']):.3f}"
    )
    output_dir = root / "CFD_2D/CFD_2D_inputs/inlet_design" / _safe_name(case_token)
    _archive_or_remove(output_dir, root, str(config.get("existing_output_action", "archive")))
    output_dir.mkdir(parents=True, exist_ok=False)

    source_report = _profile_to_xfoil_dat(base_profile, output_dir / "base_profile.dat")
    logs: dict[str, str] = {}
    stages = [
        ("repanel", _repanel_commands(config)),
        ("polar", _polar_commands(config)),
    ]
    for stage, commands in stages:
        (output_dir / f"xfoil_{stage}.in").write_text(commands, encoding="ascii")
        completed = _run_xfoil_process(executable, commands, output_dir, int(config["xfoil_timeout_s"]))
        log_path = output_dir / f"log.xfoil_{stage}"
        log_path.write_text(completed.stdout or "", encoding="utf-8")
        logs[stage] = str(log_path)
        if completed.returncode != 0:
            raise RuntimeError(f"XFOIL {stage} failed with exit {completed.returncode}; inspect {log_path}")
    repanelled = output_dir / "repanelled_profile.dat"
    polar_path = output_dir / "polar.txt"
    if not repanelled.is_file() or not polar_path.is_file():
        raise RuntimeError("XFOIL did not produce the required repanelled profile and polar files")
    polar = parse_polar(polar_path)
    polar.to_csv(output_dir / "polar.csv", index=False)

    cp_commands = _cp_commands(config, polar["alpha_deg"].tolist())
    (output_dir / "xfoil_cp.in").write_text(cp_commands, encoding="ascii")
    cp_completed = _run_xfoil_process(executable, cp_commands, output_dir, int(config["xfoil_timeout_s"]))
    cp_log = output_dir / "log.xfoil_cp"
    cp_log.write_text(cp_completed.stdout or "", encoding="utf-8")
    logs["cp"] = str(cp_log)
    if cp_completed.returncode != 0:
        raise RuntimeError(f"XFOIL Cp export failed with exit {cp_completed.returncode}; inspect {cp_log}")

    canonical, stagnation = classify_stagnation_points(repanelled, polar, output_dir, config)
    selected = select_design_points(stagnation, config)
    cut_profile, cut_report = build_cut_profile(canonical, selected, int(config["cut_margin_panel_points"]))
    profile_name = generated_profile_name(base_profile, config, cut_report["inlet_gap_percent_chord"])
    profile_path = root / "Airfoil Profiles" / profile_name
    if profile_path.exists():
        archive = root / "Previous Versions/generated_profile_backups" / f"{profile_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{profile_path.suffix}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(profile_path, archive)
    cut_profile.to_csv(profile_path, index=False, quoting=csv.QUOTE_MINIMAL)
    cut_profile.to_csv(output_dir / profile_name, index=False, quoting=csv.QUOTE_MINIMAL)
    pd.DataFrame(stagnation).to_csv(output_dir / "stagnation_points.csv", index=False)
    pd.DataFrame(selected).to_csv(output_dir / "selected_stagnation_envelope.csv", index=False)
    _plot_outputs(output_dir, polar, stagnation, selected, cut_profile)

    metadata_path = output_dir / "inlet_design_metadata.json"
    metadata = {
        "status": "PASS",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_profile": str(base_profile.relative_to(root)).replace("\\", "/"),
        "generated_profile": str(profile_path.relative_to(root)).replace("\\", "/"),
        "design_mode": config["design_mode"],
        "reynolds": float(config["reynolds"]),
        "mach": float(config["mach"]),
        "requested_alpha_count": len(alpha_values(config)),
        "converged_alpha_count": int(len(polar)),
        "selected_stagnation_count": int(len(selected)),
        "xfoil": asdict(installation) | {"executable_used": str(executable)},
        "config": config,
        "source": source_report,
        "cut": cut_report,
        "logs": logs,
        "metadata_path": str(metadata_path),
    }
    _json_write(metadata_path, metadata)
    _update_registry(root, profile_path, metadata)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--xfoil-executable", default=None)
    parser.add_argument("--check-environment", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = (args.project_root or find_project_root()).resolve()
    if args.check_environment:
        result = inspect_xfoil(root)
        print(json.dumps(asdict(result), indent=2))
        raise SystemExit(0 if result.status == "OK" else 2)
    config = args.config or root / "CFD_2D/CFD_2D_inputs/config/cfd2d_inlet_design_config.json"
    design_inlet(root, config.resolve(), args.xfoil_executable)


if __name__ == "__main__":
    main()
