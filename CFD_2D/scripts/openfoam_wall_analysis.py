#!/usr/bin/env python3
"""Wall y+ and boundary-layer profile analysis for OpenFOAM 2D cases."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from ramair_scientific_plot_style import apply_scientific_style, save_scientific_figure

from boundary_layer_estimates import boundary_layer_comparison, turbulent_flat_plate_delta99
from wall_separation_analysis import (
    analyze_wall_separation,
    summarize_urans_separation,
    write_separation_products,
)

apply_scientific_style()


CP_CATASTROPHIC_ABS_LIMIT = 50.0


def _foam_header(object_name: str) -> str:
    return f"""FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      {object_name};
}}
"""


def boundary_patches(case_dir: Path) -> list[dict[str, Any]]:
    path = case_dir / "constant" / "polyMesh" / "boundary"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    patches: list[dict[str, Any]] = []
    for match in re.finditer(r"(?m)^\s*([A-Za-z_][\w.-]*)\s*\{([^{}]*)\}", text):
        if match.group(1) == "FoamFile":
            continue
        body = match.group(2)
        type_match = re.search(r"\btype\s+([^;\s]+)\s*;", body)
        faces_match = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        patches.append({
            "name": match.group(1),
            "type": type_match.group(1) if type_match else "patch",
            "nFaces": int(faces_match.group(1)) if faces_match else 0,
        })
    return patches


def wall_patch_names(case_dir: Path) -> list[str]:
    return [str(item["name"]) for item in boundary_patches(case_dir) if item.get("type") == "wall"]


def _load_case_definition_json(case_dir: Path, name: str) -> dict[str, Any]:
    """Load case provenance from a canonical URANS case or RANS checkpoint."""
    local = case_dir / name
    if local.is_file():
        return json.loads(local.read_text(encoding="utf-8"))
    for manifest_name in ("case_manifest.json", "checkpoint_manifest.json"):
        manifest_path = case_dir.parent / manifest_name
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidates: list[Path] = []
        definition_root = str(manifest.get("definition_root") or "").strip()
        if definition_root:
            candidates.append(Path(definition_root) / "case" / name)
        source_case = str(manifest.get("source_case") or "").strip()
        if source_case:
            candidates.append(Path(source_case) / name)
        for fallback in candidates:
            if fallback.is_file():
                return json.loads(fallback.read_text(encoding="utf-8"))
        physics = dict((manifest.get("compatibility") or {}).get("physics") or {})
        if name == "case_input_summary.json" and physics:
            required = ("chord_m", "reynolds", "rho_kg_m3", "mu_pa_s")
            if all(float(physics.get(key) or 0.0) > 0.0 for key in required):
                chord = float(physics["chord_m"])
                reynolds = float(physics["reynolds"])
                rho = float(physics["rho_kg_m3"])
                mu = float(physics["mu_pa_s"])
                return {
                    "chord_m": chord,
                    "reynolds": reynolds,
                    "rho_kg_m3": rho,
                    "mu_Pa_s": mu,
                    "velocity_m_s": reynolds * mu / (rho * chord),
                    "alpha_deg": physics.get("alpha_deg"),
                    "provenance": {
                        "source": str(manifest_path),
                        "method": "checkpoint compatibility physics; velocity derived from Re*mu/(rho*c)",
                    },
                }
        if name == "case_config.json" and physics:
            return {
                "mesh_root": manifest.get("mesh_root") or manifest.get("mesh_package"),
                "geometry_topology": physics.get("topology"),
                "provenance": {
                    "source": str(manifest_path),
                    "method": "checkpoint compatibility metadata",
                },
            }
    raise FileNotFoundError(
        f"{name} is absent from the case and its recorded immutable provenance: {case_dir}"
    )


def yplus_patch_vtk_command(case_dir: Path) -> list[str]:
    patches = boundary_patches(case_dir)
    excluded = [str(item["name"]) for item in patches if item.get("type") != "wall"]
    command = [
        "foamToVTK",
        "-latestTime",
        "-fields", "(yPlus)",
        "-noInternal",
        "-ascii",
        "-noPointValues",
        "-useTimeName",
    ]
    if excluded:
        command += ["-excludePatches", f"({' '.join(excluded)})"]
    return command


def cp_patch_vtk_command(case_dir: Path) -> list[str]:
    patches = boundary_patches(case_dir)
    excluded = [str(item["name"]) for item in patches if item.get("type") != "wall"]
    command = [
        "foamToVTK",
        "-latestTime",
        "-fields", "(Cp)",
        "-noInternal",
        "-ascii",
        "-noPointValues",
        "-useTimeName",
    ]
    if excluded:
        command += ["-excludePatches", f"({' '.join(excluded)})"]
    return command


def wall_shear_patch_vtk_command(case_dir: Path) -> list[str]:
    """Export only real wall-patch wallShearStress at the latest time."""
    patches = boundary_patches(case_dir)
    excluded = [str(item["name"]) for item in patches if item.get("type") != "wall"]
    command = [
        "foamToVTK",
        "-latestTime",
        "-fields", "(wallShearStress)",
        "-noInternal",
        "-ascii",
        "-noPointValues",
        "-useTimeName",
    ]
    if excluded:
        command += ["-excludePatches", f"({' '.join(excluded)})"]
    return command


def _numeric_tokens(tokens: list[str], start: int, count: int) -> tuple[np.ndarray, int]:
    values = np.asarray([float(value) for value in tokens[start:start + count]], dtype=float)
    if values.size != count:
        raise ValueError(f"VTK section expected {count} values, found {values.size}.")
    return values, start + count


def read_legacy_vtk_wall(path: Path, field_name: str = "yPlus") -> pd.DataFrame:
    """Read ASCII legacy POLYDATA written by ``foamToVTK -noInternal``."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    tokens = text.split()
    try:
        points_index = tokens.index("POINTS")
        point_count = int(tokens[points_index + 1])
        points_flat, _ = _numeric_tokens(tokens, points_index + 3, point_count * 3)
        points = points_flat.reshape((-1, 3))
        polygons_index = tokens.index("POLYGONS")
        polygon_count = int(tokens[polygons_index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Unsupported or incomplete legacy VTK file {path}: {exc}") from exc
    cursor = polygons_index + 3
    polygons: list[list[int]] = []
    for _ in range(polygon_count):
        width = int(tokens[cursor])
        cursor += 1
        polygons.append([int(value) for value in tokens[cursor:cursor + width]])
        cursor += width
    try:
        cell_data_index = tokens.index("CELL_DATA", cursor)
        field_index = tokens.index(field_name, cell_data_index)
        components = int(tokens[field_index + 1])
        tuple_count = int(tokens[field_index + 2])
        values_flat, _ = _numeric_tokens(tokens, field_index + 4, components * tuple_count)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Field {field_name!r} was not found in {path}: {exc}") from exc
    if components not in {1, 3} or tuple_count != polygon_count:
        raise ValueError(
            f"Field {field_name} in {path} has {components} components and {tuple_count} tuples; "
            f"expected one scalar or vector per {polygon_count} wall face."
        )
    centres = np.asarray([points[indices].mean(axis=0) for indices in polygons], dtype=float)
    rows: dict[str, Any] = {
        "x_m": centres[:, 0],
        "y_m": centres[:, 1],
        "z_m": centres[:, 2],
        "patch": path.parent.name,
    }
    # A spanwise-extruded 2-D wall face has two unique projected XY vertices.
    # Preserve that edge connectivity so branch ordering never depends on x.
    edge_points: list[tuple[float, float, float, float]] = []
    for indices in polygons:
        projected = points[indices, :2]
        unique = np.unique(np.round(projected, decimals=14), axis=0)
        if len(unique) < 2:
            edge_points.append((math.nan, math.nan, math.nan, math.nan))
            continue
        distances = np.linalg.norm(unique[:, None, :] - unique[None, :, :], axis=2)
        first, second = np.unravel_index(int(np.argmax(distances)), distances.shape)
        edge_points.append((
            float(unique[first, 0]), float(unique[first, 1]),
            float(unique[second, 0]), float(unique[second, 1]),
        ))
    edges = np.asarray(edge_points, dtype=float)
    rows.update({
        "edge_x0_m": edges[:, 0],
        "edge_y0_m": edges[:, 1],
        "edge_x1_m": edges[:, 2],
        "edge_y1_m": edges[:, 3],
    })
    if components == 1:
        rows[field_name] = values_flat
    else:
        vectors = values_flat.reshape((-1, components))
        rows[f"{field_name}_x"] = vectors[:, 0]
        rows[f"{field_name}_y"] = vectors[:, 1]
        rows[f"{field_name}_z"] = vectors[:, 2]
    return pd.DataFrame(rows)


def _latest_patch_vtk(case_dir: Path, patch: str) -> Path | None:
    candidates = list((case_dir / "VTK" / patch).glob(f"{patch}_*.vtk"))
    if not candidates:
        return None

    ascii_candidates: list[Path] = []
    for path in candidates:
        try:
            header = path.read_bytes()[:256].decode("ascii", errors="ignore")
        except OSError:
            continue
        if "ASCII" in header and "DATASET POLYDATA" in header:
            ascii_candidates.append(path)
    if not ascii_candidates:
        return None
    # foamToVTK can name files by a time index or by the physical time.  File
    # modification time therefore identifies the export performed by this run
    # more reliably than the numeric filename suffix.
    return max(ascii_candidates, key=lambda path: path.stat().st_mtime)


def _patch_vtk_candidates(case_dir: Path, patch: str, field_name: str) -> list[Path]:
    archived = list((case_dir / "VTK_wall_fields" / field_name / patch).glob("*.vtk"))
    active = list((case_dir / "VTK" / patch).glob(f"{patch}_*.vtk"))
    return sorted(archived + active, key=lambda path: path.stat().st_mtime, reverse=True)


def _load_patch_field(case_dir: Path, patch: str, field_name: str) -> tuple[pd.DataFrame, Path] | None:
    for vtk in _patch_vtk_candidates(case_dir, patch, field_name):
        try:
            return read_legacy_vtk_wall(vtk, field_name), vtk
        except (OSError, ValueError):
            continue
    return None


def archive_wall_field_vtk(case_dir: Path, field_name: str) -> list[str]:
    """Preserve a field-specific wall VTK before the next foamToVTK call overwrites it."""
    archived: list[str] = []
    for patch in wall_patch_names(case_dir):
        vtk = _latest_patch_vtk(case_dir, patch)
        if vtk is None:
            continue
        try:
            read_legacy_vtk_wall(vtk, field_name)
        except (OSError, ValueError):
            continue
        destination = case_dir / "VTK_wall_fields" / field_name / patch / vtk.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vtk, destination)
        archived.append(str(destination))
    return archived


def _classify_wall_samples(data: pd.DataFrame, chord_m: float) -> pd.DataFrame:
    """Add x/c, upper/lower and external/internal labels to wall samples."""
    data = data.copy()
    x_min = float(data["x_m"].min())
    chord = max(float(chord_m), float(data["x_m"].max() - x_min), 1.0e-30)
    data["x_over_c"] = (data["x_m"] - x_min) / chord

    # Construct a local camber estimate from upper/lower envelopes.  This also
    # handles a single combined airfoil_wall patch without assuming y=0.
    bins = np.linspace(0.0, 1.0, 101)
    data["_bin"] = np.clip(np.digitize(data["x_over_c"], bins) - 1, 0, len(bins) - 2)
    envelope = data.groupby("_bin", as_index=False).agg(
        xc=("x_over_c", "mean"),
        y_min=("y_m", "min"),
        y_max=("y_m", "max"),
    )
    envelope["midline_y_m"] = 0.5 * (envelope["y_min"] + envelope["y_max"])
    midline = np.interp(
        data["x_over_c"].to_numpy(float),
        envelope["xc"].to_numpy(float),
        envelope["midline_y_m"].to_numpy(float),
    )
    data["midline_y_m"] = midline
    explicit_upper = data["patch"].str.contains("upper|extrados|suction", case=False, regex=True)
    explicit_lower = data["patch"].str.contains("lower|intrados|pressure", case=False, regex=True)
    data["surface"] = np.where(
        explicit_upper,
        "upper",
        np.where(explicit_lower, "lower", np.where(data["y_m"] >= midline, "upper", "lower")),
    )
    patch_lower = data["patch"].astype(str).str.lower()
    data["wall_side"] = np.where(
        patch_lower.str.contains("external|outer", regex=True),
        "external",
        np.where(
            patch_lower.str.contains("internal|inner", regex=True),
            "internal",
            "combined",
        ),
    )
    return data.drop(columns=["_bin"])


def _load_all_wall_patches(
    case_dir: Path,
    field_name: str,
    chord_m: float,
) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    for patch in wall_patch_names(case_dir):
        loaded = _load_patch_field(case_dir, patch, field_name)
        if loaded is None:
            continue
        frame, vtk = loaded
        frames.append(frame)
        sources.append(str(vtk))
    if not frames:
        raise FileNotFoundError(
            f"No ASCII wall-patch VTK containing {field_name} was found."
        )
    return _classify_wall_samples(pd.concat(frames, ignore_index=True), chord_m), sources


def load_wall_yplus(case_dir: Path, chord_m: float) -> tuple[pd.DataFrame, list[str]]:
    return _load_all_wall_patches(case_dir, "yPlus", chord_m)


def load_wall_cp(case_dir: Path, chord_m: float) -> tuple[pd.DataFrame, list[str]]:
    """Load the real OpenFOAM ``Cp`` wall field and classify upper/lower faces."""
    return _load_all_wall_patches(case_dir, "Cp", chord_m)


def load_wall_shear_stress(case_dir: Path, chord_m: float) -> tuple[pd.DataFrame, list[str]]:
    """Load the real vector wallShearStress field with face connectivity."""
    return _load_all_wall_patches(case_dir, "wallShearStress", chord_m)


def load_wall_shear_snapshots(
    case_dir: Path,
    chord_m: float,
) -> list[tuple[float, pd.DataFrame, list[str]]]:
    """Load every retained wallShearStress VTK snapshot keyed by physical time."""
    grouped: dict[float, list[tuple[pd.DataFrame, Path]]] = {}
    seen: set[Path] = set()
    for patch in wall_patch_names(case_dir):
        for vtk in _patch_vtk_candidates(case_dir, patch, "wallShearStress"):
            resolved = vtk.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            match = re.search(r"_(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\.vtk$", vtk.name)
            if not match:
                continue
            try:
                time_value = float(match.group(1))
                frame = read_legacy_vtk_wall(vtk, "wallShearStress")
            except (ValueError, OSError):
                continue
            grouped.setdefault(time_value, []).append((frame, vtk))
    snapshots: list[tuple[float, pd.DataFrame, list[str]]] = []
    for time_value, parts in sorted(grouped.items()):
        frame = _classify_wall_samples(
            pd.concat([item[0] for item in parts], ignore_index=True), chord_m
        )
        snapshots.append((time_value, frame, [str(item[1]) for item in parts]))
    return snapshots


def cp_field_diagnostics(data: pd.DataFrame) -> dict[str, Any]:
    """Describe Cp numerically without turning a heuristic into acceptance."""
    values = pd.to_numeric(data.get("Cp"), errors="coerce").to_numpy(float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"status": "NO_FINITE_DATA", "finite_samples": 0}
    absolute_max = float(np.max(np.abs(finite)))
    catastrophic = absolute_max > CP_CATASTROPHIC_ABS_LIMIT
    return {
        "status": "NONPHYSICAL_CATASTROPHIC" if catastrophic else "AVAILABLE",
        "finite_samples": int(finite.size),
        "minimum": float(np.min(finite)),
        "maximum": float(np.max(finite)),
        "maximum_absolute": absolute_max,
        "catastrophic_diagnostic_limit": CP_CATASTROPHIC_ABS_LIMIT,
        "blocks_workflow": False,
        "interpretation": (
            "Low-Mach airfoil diagnostic only: extreme Cp indicates a divergent or unconverged field; "
            "the raw data are preserved and this threshold is not an aerodynamic acceptance criterion."
        ),
    }


def plot_cp_distribution(data: pd.DataFrame, output: Path, diagnostic_status: str = "AVAILABLE") -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    has_split_sides = set(data.get("wall_side", pd.Series(dtype=str)).unique()).intersection(
        {"external", "internal"}
    )
    styles = {
        ("external", "upper"): ("#0068a8", "-", "Extrados exterior"),
        ("external", "lower"): ("#c2410c", "-", "Intrados exterior"),
        ("internal", "upper"): ("#23a6d5", "--", "Extrados interior"),
        ("internal", "lower"): ("#e07a3f", "--", "Intrados interior"),
        ("combined", "upper"): ("#0068a8", "-", "Extrados"),
        ("combined", "lower"): ("#c2410c", "-", "Intrados"),
    }
    groups = (
        [("external", "upper"), ("external", "lower"), ("internal", "upper"), ("internal", "lower")]
        if has_split_sides
        else [("combined", "upper"), ("combined", "lower")]
    )
    wall_side = data.get("wall_side", pd.Series("combined", index=data.index))
    for side, surface in groups:
        subset = data[(wall_side == side) & (data["surface"] == surface)].sort_values("x_over_c")
        if subset.empty:
            continue
        subset = subset.groupby("x_over_c", as_index=False)["Cp"].mean()
        color, line_style, label = styles[(side, surface)]
        ax.plot(
            subset["x_over_c"],
            subset["Cp"],
            color=color,
            linestyle=line_style,
            label=label,
            linewidth=1.15,
        )
    ax.axhline(0.0, color="0.35", linestyle="--", linewidth=0.8)
    ax.set_xlim(0.0, 1.0)
    ax.invert_yaxis()
    ax.set_xlabel(r"Chordwise position, $x/c$ [-]")
    ax.set_ylabel(r"Pressure coefficient, $C_p$ [-]")
    title = "Surface-pressure distribution"
    if diagnostic_status == "NONPHYSICAL_CATASTROPHIC":
        title += "\nDIAGNOSTICO NO FISICO: campo divergente/no convergido"
    ax.set_title(title)
    ax.grid(True, linewidth=0.35, alpha=0.7)
    ax.legend()
    fig.tight_layout()
    save_scientific_figure(
        fig, output, data=data,
        metadata={"source": "OpenFOAM wall pressure", "grouping": "surface branch", "sorting": "x/c within branch", "deduplication": "mean at coincident abscissae"},
    )


def plot_yplus_distribution(data: pd.DataFrame, target_y_plus: float, output: Path) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    has_split_sides = set(data.get("wall_side", pd.Series(dtype=str)).unique()).intersection(
        {"external", "internal"}
    )
    styles = {
        ("external", "upper"): ("#0068a8", "-", "Extrados exterior"),
        ("external", "lower"): ("#c2410c", "-", "Intrados exterior"),
        ("internal", "upper"): ("#23a6d5", "--", "Extrados interior"),
        ("internal", "lower"): ("#e07a3f", "--", "Intrados interior"),
        ("combined", "upper"): ("#0068a8", "-", "Extrados"),
        ("combined", "lower"): ("#c2410c", "-", "Intrados"),
    }
    groups = (
        [("external", "upper"), ("external", "lower"), ("internal", "upper"), ("internal", "lower")]
        if has_split_sides
        else [("combined", "upper"), ("combined", "lower")]
    )
    wall_side = data.get("wall_side", pd.Series("combined", index=data.index))
    for side, surface in groups:
        subset = data[(wall_side == side) & (data["surface"] == surface)].sort_values("x_over_c")
        if subset.empty:
            continue
        # Average coincident x stations (e.g. spanwise duplicates) without
        # inventing samples between the actual wall faces.
        subset = subset.groupby("x_over_c", as_index=False)["yPlus"].mean()
        color, line_style, label = styles[(side, surface)]
        ax.plot(
            subset["x_over_c"],
            subset["yPlus"],
            color=color,
            linestyle=line_style,
            label=label,
            linewidth=1.15,
        )
    ax.axhline(float(target_y_plus), color="#333333", linestyle="--", linewidth=0.9, label=f"y+ objetivo = {target_y_plus:g}")
    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel(r"Chordwise position, $x/c$ [-]")
    ax.set_ylabel(r"First-cell wall coordinate, $y^+$ [-]")
    ax.set_title(r"Wall $y^+$ distribution")
    ax.grid(True, linewidth=0.35, alpha=0.7)
    ax.legend()
    fig.tight_layout()
    save_scientific_figure(
        fig, output, data=data,
        metadata={"source": "OpenFOAM yPlus", "grouping": "surface branch", "sorting": "x/c within branch", "deduplication": "mean at coincident abscissae"},
    )


def _station_geometry(data: pd.DataFrame, surface: str, station_xc: float) -> dict[str, float]:
    subset = data[data["surface"] == surface].sort_values("x_over_c").reset_index(drop=True)
    if len(subset) < 3:
        raise ValueError(f"Not enough {surface} wall faces for a normal sampling line.")
    index = int((subset["x_over_c"] - float(station_xc)).abs().idxmin())
    index = min(max(index, 1), len(subset) - 2)
    previous = subset.iloc[index - 1]
    current = subset.iloc[index]
    following = subset.iloc[index + 1]
    tangent = np.asarray([
        float(following["x_m"] - previous["x_m"]),
        float(following["y_m"] - previous["y_m"]),
    ])
    tangent_norm = float(np.linalg.norm(tangent))
    if tangent_norm <= 1.0e-20:
        raise ValueError(f"Degenerate wall tangent near {surface} x/c={station_xc:g}.")
    tangent /= tangent_norm
    normal = np.asarray([-tangent[1], tangent[0]])
    away = np.asarray([0.0, float(current["y_m"] - current["midline_y_m"])])
    if float(np.dot(normal, away)) < 0.0:
        normal *= -1.0
    return {
        "x_m": float(current["x_m"]),
        "y_m": float(current["y_m"]),
        "z_m": float(current["z_m"]),
        "actual_x_over_c": float(current["x_over_c"]),
        "normal_x": float(normal[0]),
        "normal_y": float(normal[1]),
    }


def write_velocity_profile_control_dict(
    case_dir: Path,
    wall_data: pd.DataFrame,
    *,
    chord_m: float,
    reynolds: float,
    prism_thickness_m: float,
    first_cell_height_m: float,
    stations_xc: list[float],
    sample_points: int,
) -> tuple[Path, list[dict[str, Any]]]:
    definitions: list[dict[str, Any]] = []
    set_blocks: list[str] = []
    for surface in ("upper", "lower"):
        for requested_xc in stations_xc:
            geometry = _station_geometry(wall_data, surface, requested_xc)
            theoretical = turbulent_flat_plate_delta99(
                chord_m=chord_m,
                reynolds_chord=reynolds,
                x_over_chord=geometry["actual_x_over_c"],
            )
            distance = min(
                0.5 * chord_m,
                max(1.75 * theoretical, 1.35 * prism_thickness_m, 0.02 * chord_m),
            )
            epsilon = max(0.1 * first_cell_height_m, 1.0e-8 * chord_m)
            start = (
                geometry["x_m"] + epsilon * geometry["normal_x"],
                geometry["y_m"] + epsilon * geometry["normal_y"],
                geometry["z_m"],
            )
            end = (
                geometry["x_m"] + distance * geometry["normal_x"],
                geometry["y_m"] + distance * geometry["normal_y"],
                geometry["z_m"],
            )
            name = f"{surface}_xc_{requested_xc:.3f}".replace(".", "p")
            set_blocks.append(f"""        {name}
        {{
            type            lineUniform;
            axis            distance;
            start           ({start[0]:.12g} {start[1]:.12g} {start[2]:.12g});
            end             ({end[0]:.12g} {end[1]:.12g} {end[2]:.12g});
            nPoints         {max(10, int(sample_points))};
        }}""")
            definitions.append({
                "name": name,
                "surface": surface,
                "requested_x_over_c": float(requested_xc),
                "actual_x_over_c": geometry["actual_x_over_c"],
                "start_m": list(start),
                "end_m": list(end),
                "normal_xy": [geometry["normal_x"], geometry["normal_y"]],
                "sample_distance_m": distance,
                "theoretical_delta99_m": theoretical,
            })
    path = case_dir / "system" / "ramairVelocityProfiles"
    path.write_text(_foam_header("ramairVelocityProfiles") + f"""
type                sets;
libs                ("libsampling.so");
writeControl        timeStep;
interpolationScheme cellPoint;
setFormat           raw;
fields              (U p);
sets
{{
{chr(10).join(set_blocks)}
}}
""", encoding="utf-8")
    return path, definitions


def _read_raw_velocity_file(path: Path) -> pd.DataFrame:
    rows: list[list[float]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            values = [float(value) for value in line.split()]
        except ValueError:
            continue
        if len(values) >= 4:
            rows.append(values)
    if not rows:
        raise ValueError(f"No velocity samples found in {path}.")
    width = min(len(row) for row in rows)
    rows = [row[:width] for row in rows]
    data = pd.DataFrame(rows)
    data = data.rename(columns={0: "distance_m", 1: "Ux_m_s", 2: "Uy_m_s", 3: "Uz_m_s"})
    data["speed_m_s"] = np.sqrt(data["Ux_m_s"] ** 2 + data["Uy_m_s"] ** 2 + data["Uz_m_s"] ** 2)
    return data


def load_velocity_profiles(case_dir: Path, definitions: list[dict[str, Any]], velocity_m_s: float) -> tuple[pd.DataFrame, list[str]]:
    root = case_dir / "postProcessing" / "ramairVelocityProfiles"
    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    for definition in definitions:
        candidates = [
            path for path in root.glob(f"**/*{definition['name']}*")
            if path.is_file() and path.suffix.lower() in {".xy", ".raw", ".dat"}
        ]
        if not candidates:
            continue
        path = max(candidates, key=lambda item: item.stat().st_mtime)
        frame = _read_raw_velocity_file(path)
        frame["surface"] = definition["surface"]
        frame["x_over_c"] = definition["actual_x_over_c"]
        frame["profile_name"] = definition["name"]
        frame["speed_over_uinf"] = frame["speed_m_s"] / max(float(velocity_m_s), 1.0e-30)
        normal_x, normal_y = (float(value) for value in definition["normal_xy"])
        tangent_x, tangent_y = -normal_y, normal_x
        frame["tangential_speed_m_s"] = np.abs(
            frame["Ux_m_s"] * tangent_x + frame["Uy_m_s"] * tangent_y
        )
        frames.append(frame)
        sources.append(str(path))
    if not frames:
        raise FileNotFoundError(f"No raw velocity-profile files were found below {root}.")
    return pd.concat(frames, ignore_index=True), sources


def boundary_layer_velocity_ratio(
    profile: pd.DataFrame,
    velocity_m_s: float,
) -> tuple[np.ndarray, float, str]:
    clean = profile.sort_values("distance_m").dropna(subset=["distance_m", "speed_m_s"])
    if clean.empty:
        return np.asarray([], dtype=float), float("nan"), "NO_SAMPLES"
    if "tangential_speed_m_s" in clean:
        velocity = clean["tangential_speed_m_s"].to_numpy(float)
        basis = "local_wall_tangential_velocity"
    else:
        velocity = clean["speed_m_s"].to_numpy(float)
        basis = "legacy_velocity_magnitude"
    tail_count = max(3, int(math.ceil(0.1 * len(velocity))))
    local_edge_velocity = float(np.nanmedian(velocity[-tail_count:]))
    if not np.isfinite(local_edge_velocity) or local_edge_velocity <= 1.0e-12:
        local_edge_velocity = max(float(velocity_m_s), 1.0e-30)
        basis += "_freestream_fallback"
    return velocity / local_edge_velocity, local_edge_velocity, basis


def estimate_numerical_delta99(profile: pd.DataFrame, velocity_m_s: float) -> float | None:
    clean = profile.sort_values("distance_m").dropna(subset=["distance_m", "speed_m_s"])
    if clean.empty:
        return None
    ratio, _, _ = boundary_layer_velocity_ratio(clean, velocity_m_s)
    envelope = np.maximum.accumulate(ratio)
    indices = np.flatnonzero(envelope >= 0.99)
    if indices.size == 0:
        return None
    index = int(indices[0])
    distances = clean["distance_m"].to_numpy(float)
    if index == 0:
        return float(distances[0])
    x0, x1 = float(distances[index - 1]), float(distances[index])
    y0, y1 = float(envelope[index - 1]), float(envelope[index])
    if abs(y1 - y0) < 1.0e-14:
        return x1
    return x0 + (0.99 - y0) * (x1 - x0) / (y1 - y0)


def plot_velocity_profiles_and_thickness(
    profiles: pd.DataFrame,
    definitions: list[dict[str, Any]],
    *,
    velocity_m_s: float,
    chord_m: float,
    reynolds: float,
    prism_thickness_m: float,
    profile_output: Path,
    thickness_output: Path,
) -> pd.DataFrame:
    import matplotlib.pyplot as plt

    definition_map = {item["name"]: item for item in definitions}
    summary_rows: list[dict[str, Any]] = []
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 5.2), sharey=True)
    for axis, surface in zip(axes, ("upper", "lower")):
        subset = profiles[profiles["surface"] == surface]
        for name, group in subset.groupby("profile_name"):
            definition = definition_map[str(name)]
            numerical = estimate_numerical_delta99(group, velocity_m_s)
            velocity_ratio, local_edge_velocity, velocity_basis = boundary_layer_velocity_ratio(
                group,
                velocity_m_s,
            )
            theoretical = float(definition["theoretical_delta99_m"])
            group = group.sort_values("distance_m")
            axis.plot(
                velocity_ratio,
                group["distance_m"] / chord_m,
                linewidth=1.1,
                label=f"x/c={definition['actual_x_over_c']:.3f}",
            )
            if numerical is not None:
                axis.scatter([0.99], [numerical / chord_m], s=18, zorder=4)
            summary_rows.append({
                "surface": surface,
                "x_over_c": definition["actual_x_over_c"],
                "numerical_delta99_m": numerical,
                "numerical_delta99_over_c": numerical / chord_m if numerical is not None else None,
                "theoretical_delta99_m": theoretical,
                "theoretical_delta99_over_c": theoretical / chord_m,
                "prism_stack_thickness_m": prism_thickness_m,
                "prism_stack_thickness_over_c": prism_thickness_m / chord_m,
                "local_edge_velocity_m_s": local_edge_velocity,
                "velocity_ratio_basis": velocity_basis,
                "delta99_detection": "first monotonic-envelope crossing of |U_t|/Ue=0.99" if numerical is not None else "NOT_REACHED_ON_SAMPLE_LINE",
            })
        axis.axvline(0.99, color="0.35", linestyle="--", linewidth=0.8)
        axis.set_xlim(left=0.0)
        axis.set_xlabel(r"Tangential velocity, $|U_t|/U_e$ [-]")
        axis.set_title("Upper surface" if surface == "upper" else "Lower surface")
        axis.grid(True, linewidth=0.35, alpha=0.7)
        axis.legend(fontsize=8)
    axes[0].set_ylabel(r"Wall-normal distance, $n/c$ [-]")
    fig.suptitle("Wall-normal velocity profiles")
    fig.tight_layout()
    save_scientific_figure(
        fig, profile_output, data=profiles,
        metadata={"source": "OpenFOAM sampled wall-normal profiles", "grouping": "surface and x/c station"},
    )

    summary = pd.DataFrame(summary_rows).sort_values(["surface", "x_over_c"])
    fig, ax = plt.subplots(figsize=(9.2, 4.9))
    x_curve = np.linspace(0.01, 1.0, 160)
    theory_curve = [
        turbulent_flat_plate_delta99(chord_m=chord_m, reynolds_chord=reynolds, x_over_chord=value) / chord_m
        for value in x_curve
    ]
    ax.plot(x_curve, theory_curve, color="#333333", linestyle="--", linewidth=1.0, label="delta99 teorico, placa plana turbulenta")
    ax.axhline(prism_thickness_m / chord_m, color="#7c3aed", linestyle=":", linewidth=1.15, label="espesor total de capas prismaticas")
    for surface, color, label in (("upper", "#0068a8", "delta99 numerico extrados"), ("lower", "#c2410c", "delta99 numerico intrados")):
        subset = summary[(summary["surface"] == surface) & summary["numerical_delta99_over_c"].notna()]
        if not subset.empty:
            ax.plot(subset["x_over_c"], subset["numerical_delta99_over_c"], marker="o", color=color, linewidth=1.0, label=label)
    ax.set_xlabel(r"Chordwise position, $x/c$ [-]")
    ax.set_ylabel(r"Boundary-layer thickness, $\delta_{99}/c$ [-]")
    ax.set_title("Boundary-layer thickness comparison")
    ax.grid(True, linewidth=0.35, alpha=0.7)
    ax.legend(fontsize=8)
    fig.tight_layout()
    save_scientific_figure(
        fig, thickness_output, data=summary,
        metadata={"source": "OpenFOAM sampled profiles and turbulent flat-plate estimate", "transformation": "first monotonic-envelope crossing of |Ut|/Ue=0.99"},
    )
    return summary


def analyze_wall_boundary_layer(
    *,
    project_root: Path,
    case_dir: Path,
    output_dir: Path,
    variant: str,
    run_openfoam_tools: bool,
    timeout_s: int,
    stations_xc: list[float],
    sample_points: int,
    solver_module: str,
    simulation_mode: str = "AUTO",
) -> dict[str, Any]:
    """Create real wall y+ and normal velocity-profile products when data exist."""
    output_dir.mkdir(parents=True, exist_ok=True)
    case_inputs = _load_case_definition_json(case_dir, "case_input_summary.json")
    case_config = _load_case_definition_json(case_dir, "case_config.json")
    mesh_root = Path(str(case_config.get("mesh_root") or f"CFD_2D/meshes/{variant}"))
    if not mesh_root.is_absolute():
        mesh_root = project_root / mesh_root
    mesh_config_path = mesh_root / "mesh_config_used.json"
    if not mesh_config_path.is_file():
        mesh_config_path = project_root / "CFD_2D" / "CFD_2D_inputs" / "config" / "cfd2d_mesh_config.json"
    mesh_config = json.loads(mesh_config_path.read_text(encoding="utf-8"))
    mesh_quality_path = mesh_root / "mesh_quality_report.json"
    mesh_quality = json.loads(mesh_quality_path.read_text(encoding="utf-8")) if mesh_quality_path.is_file() else {}
    manifest_path = project_root / "CFD_2D" / "CFD_2D_inputs" / "case_package" / variant / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    is_open = bool(manifest.get("has_ram_air_opening_feature", "open" in variant.lower()))
    prefix = "open" if is_open else "closed"
    y1_use_yplus = bool(mesh_config.get(f"{prefix}_use_yplus_first_cell_height", True))
    comparison = boundary_layer_comparison(
        chord_m=float(case_inputs["chord_m"]),
        reynolds=float(case_inputs["reynolds"]),
        target_y_plus=float(mesh_config.get("target_y_plus", 1.0)),
        rho_kg_m3=float(case_inputs["rho_kg_m3"]),
        mu_pa_s=float(case_inputs["mu_Pa_s"]),
        layers=int(mesh_config.get(f"{prefix}_boundary_layer_layers", 0)),
        growth_rate=float(mesh_config.get(f"{prefix}_boundary_layer_growth", 1.0)),
        manual_y1_m=float(mesh_config.get(f"{prefix}_first_cell_height_m", 0.0) or 0.0),
        use_yplus_y1=y1_use_yplus,
        x_over_chord=1.0,
    )
    actual_y1 = float(mesh_quality.get("boundary_layer_first_cell_height_m", 0.0) or 0.0)
    actual_stack_over_chord = float(mesh_quality.get("boundary_layer_total_thickness_chord", 0.0) or 0.0)
    if actual_y1 > 0.0:
        comparison["y1_m"] = actual_y1
        comparison["y1_over_chord"] = actual_y1 / max(float(case_inputs["chord_m"]), 1.0e-30)
        comparison["y1_source"] = "mesh_quality_report_actual"
    if actual_stack_over_chord > 0.0:
        comparison["prism_stack_thickness_m"] = actual_stack_over_chord * float(case_inputs["chord_m"])
        comparison["prism_stack_thickness_over_chord"] = actual_stack_over_chord
        comparison["prism_to_theoretical_delta99_ratio"] = (
            comparison["prism_stack_thickness_m"] / max(float(comparison["theoretical_delta99_m"]), 1.0e-30)
        )
    report: dict[str, Any] = {
        "status": "WAITING_FOR_WALL_DATA",
        "variant": variant,
        "estimate": comparison,
        "mesh_config_source": str(mesh_config_path),
        "mesh_quality_source": str(mesh_quality_path) if mesh_quality_path.is_file() else None,
        "commands": [],
        "stations_x_over_c": stations_xc,
    }
    if run_openfoam_tools:
        if shutil.which("foamToVTK") is None:
            report["yplus_vtk_export"] = {"status": "MISSING_EXECUTABLE", "command": "foamToVTK"}
        else:
            command = yplus_patch_vtk_command(case_dir)
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(case_dir),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=max(10, int(timeout_s)),
                )
                log = output_dir / "openfoam_postprocess_logs" / "log.foamToVTK_yPlus_wall"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(completed.stdout or "", encoding="utf-8", errors="ignore")
                result = {"status": "OK" if completed.returncode == 0 else "FAIL", "returncode": completed.returncode, "command": command, "log": str(log)}
                report["commands"].append(result)
                report["yplus_vtk_export"] = result
                if completed.returncode == 0:
                    report["yplus_vtk_archives"] = archive_wall_field_vtk(case_dir, "yPlus")
            except subprocess.TimeoutExpired as exc:
                report["yplus_vtk_export"] = {"status": "TIMEOUT", "command": command, "timeout_s": timeout_s, "details": str(exc)}
    wall_data: pd.DataFrame | None = None
    try:
        wall_data, yplus_sources = load_wall_yplus(case_dir, float(case_inputs["chord_m"]))
    except Exception as exc:
        report["yplus_status"] = "NOT_AVAILABLE"
        report["yplus_reason"] = f"wall_yplus_unavailable: {type(exc).__name__}: {exc}"
    if wall_data is not None:
        wall_data.to_csv(output_dir / "wall_yplus_vs_xc.csv", index=False)
        plot_yplus_distribution(wall_data, float(mesh_config.get("target_y_plus", 1.0)), output_dir / "wall_yplus_vs_xc.png")
        report.update(status="YPLUS_PROCESSED", yplus_status="PROCESSED", yplus_sources=yplus_sources, yplus_rows=len(wall_data))

    if run_openfoam_tools and shutil.which("foamToVTK") is not None:
        command = cp_patch_vtk_command(case_dir)
        try:
            completed = subprocess.run(
                command,
                cwd=str(case_dir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(10, int(timeout_s)),
            )
            log = output_dir / "openfoam_postprocess_logs" / "log.foamToVTK_Cp_wall"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(completed.stdout or "", encoding="utf-8", errors="ignore")
            result = {
                "status": "OK" if completed.returncode == 0 else "FAIL",
                "returncode": completed.returncode,
                "command": command,
                "log": str(log),
            }
            report["commands"].append(result)
            report["cp_vtk_export"] = result
            if completed.returncode == 0:
                report["cp_vtk_archives"] = archive_wall_field_vtk(case_dir, "Cp")
        except subprocess.TimeoutExpired as exc:
            report["cp_vtk_export"] = {
                "status": "TIMEOUT",
                "command": command,
                "timeout_s": timeout_s,
                "details": str(exc),
            }
    cp_data: pd.DataFrame | None = None
    try:
        cp_data, cp_sources = load_wall_cp(case_dir, float(case_inputs["chord_m"]))
        cp_diagnostics = cp_field_diagnostics(cp_data)
        cp_data.to_csv(output_dir / "wall_cp_vs_xc.csv", index=False)
        plot_cp_distribution(
            cp_data,
            output_dir / "wall_cp_vs_xc.png",
            diagnostic_status=str(cp_diagnostics["status"]),
        )
        report.update(
            cp_status=(
                "PROCESSED_NONPHYSICAL_DIAGNOSTIC"
                if cp_diagnostics["status"] == "NONPHYSICAL_CATASTROPHIC"
                else "PROCESSED"
            ),
            cp_sources=cp_sources,
            cp_rows=len(cp_data),
            cp_diagnostics=cp_diagnostics,
        )
    except Exception as exc:
        report["cp_status"] = "NOT_AVAILABLE"
        report["cp_reason"] = f"{type(exc).__name__}: {exc}"

    if run_openfoam_tools and shutil.which("foamToVTK") is not None:
        command = wall_shear_patch_vtk_command(case_dir)
        try:
            completed = subprocess.run(
                command,
                cwd=str(case_dir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(10, int(timeout_s)),
            )
            log = output_dir / "openfoam_postprocess_logs" / "log.foamToVTK_wallShearStress_wall"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(completed.stdout or "", encoding="utf-8", errors="ignore")
            result = {
                "status": "OK" if completed.returncode == 0 else "FAIL",
                "returncode": completed.returncode,
                "command": command,
                "log": str(log),
            }
            report["commands"].append(result)
            report["wall_shear_vtk_export"] = result
            if completed.returncode == 0:
                report["wall_shear_vtk_archives"] = archive_wall_field_vtk(
                    case_dir, "wallShearStress"
                )
        except subprocess.TimeoutExpired as exc:
            report["wall_shear_vtk_export"] = {
                "status": "TIMEOUT",
                "command": command,
                "timeout_s": timeout_s,
                "details": str(exc),
            }
    try:
        shear_data, shear_sources = load_wall_shear_stress(
            case_dir, float(case_inputs["chord_m"])
        )
        separation_data, separation_report = analyze_wall_separation(
            shear_data,
            chord_m=float(case_inputs["chord_m"]),
            velocity_m_s=float(case_inputs["velocity_m_s"]),
        )
        if separation_data.empty:
            raise ValueError(
                separation_report.get("branch_mapping", {}).get("reason")
                or "wall branch connectivity was unresolved"
            )
        separation_products = write_separation_products(
            separation_data,
            separation_report,
            output_dir,
            cp_data=cp_data,
        )
        report.update(
            wall_shear_status="PROCESSED",
            wall_shear_sources=shear_sources,
            wall_shear_rows=len(separation_data),
            separation=separation_report,
            separation_products=separation_products,
        )
        if str(simulation_mode).upper() == "URANS":
            snapshot_reports: list[tuple[float, dict[str, Any]]] = []
            snapshot_sources: dict[str, list[str]] = {}
            for time_value, snapshot, sources in load_wall_shear_snapshots(
                case_dir, float(case_inputs["chord_m"])
            ):
                _, snapshot_report = analyze_wall_separation(
                    snapshot,
                    chord_m=float(case_inputs["chord_m"]),
                    velocity_m_s=float(case_inputs["velocity_m_s"]),
                )
                snapshot_reports.append((time_value, snapshot_report))
                snapshot_sources[f"{time_value:.12g}"] = sources
            report["separation_time_history"] = summarize_urans_separation(
                snapshot_reports, output_dir
            )
            report["separation_time_history"]["sources"] = snapshot_sources
    except Exception as exc:
        report["wall_shear_status"] = "UNRESOLVED"
        report["wall_shear_reason"] = f"{type(exc).__name__}: {exc}"

    if wall_data is None:
        report["velocity_profile_reason"] = (
            "wall yPlus geometry was unavailable; velocity sampling was not generated"
        )
        (output_dir / "wall_boundary_layer_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report

    control_dict, definitions = write_velocity_profile_control_dict(
        case_dir,
        wall_data,
        chord_m=float(case_inputs["chord_m"]),
        reynolds=float(case_inputs["reynolds"]),
        prism_thickness_m=float(comparison["prism_stack_thickness_m"]),
        first_cell_height_m=float(comparison["y1_m"]),
        stations_xc=stations_xc,
        sample_points=sample_points,
    )
    report["velocity_profile_control_dict"] = str(control_dict)
    report["velocity_profile_definitions"] = definitions
    if run_openfoam_tools:
        executable = shutil.which("foamPostProcess") or shutil.which("postProcess")
        if executable:
            command = [
                executable,
                "-solver", solver_module,
                "-func", control_dict.name,
                "-latestTime",
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(case_dir),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=max(10, int(timeout_s)),
                )
                log = output_dir / "openfoam_postprocess_logs" / "log.velocityProfiles"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(completed.stdout or "", encoding="utf-8", errors="ignore")
                result = {"status": "OK" if completed.returncode == 0 else "FAIL", "returncode": completed.returncode, "command": command, "log": str(log)}
                report["commands"].append(result)
                report["velocity_profile_sampling"] = result
            except subprocess.TimeoutExpired as exc:
                report["velocity_profile_sampling"] = {"status": "TIMEOUT", "command": command, "timeout_s": timeout_s, "details": str(exc)}
        else:
            report["velocity_profile_sampling"] = {"status": "MISSING_EXECUTABLE", "command": "foamPostProcess/postProcess"}
    try:
        profiles, profile_sources = load_velocity_profiles(case_dir, definitions, float(case_inputs["velocity_m_s"]))
        profiles.to_csv(output_dir / "wall_normal_velocity_profiles.csv", index=False)
        thickness_summary = plot_velocity_profiles_and_thickness(
            profiles,
            definitions,
            velocity_m_s=float(case_inputs["velocity_m_s"]),
            chord_m=float(case_inputs["chord_m"]),
            reynolds=float(case_inputs["reynolds"]),
            prism_thickness_m=float(comparison["prism_stack_thickness_m"]),
            profile_output=output_dir / "wall_normal_velocity_profiles.png",
            thickness_output=output_dir / "boundary_layer_thickness_comparison.png",
        )
        thickness_summary.to_csv(output_dir / "boundary_layer_thickness_comparison.csv", index=False)
        report.update(
            status="YPLUS_AND_VELOCITY_PROFILES_PROCESSED",
            velocity_profile_sources=profile_sources,
            velocity_profile_rows=len(profiles),
            thickness_rows=len(thickness_summary),
        )
    except Exception as exc:
        report["velocity_profile_reason"] = f"{type(exc).__name__}: {exc}"
    (output_dir / "wall_boundary_layer_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
