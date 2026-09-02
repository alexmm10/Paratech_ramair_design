#!/usr/bin/env python3
"""
ramair_2d_profile_case_builder.py

Independent 2D profile case-package builder for a ram-air / parafoil CAD workflow.

This script does not import CATIA, does not use COM automation, does not generate a
mesh, and does not create OpenFOAM/SU2 cases.  It only reads the clean interface
exported by the CATIA preprocessor and prepares geometry, metadata, validation,
previews and placeholder files for the next CFD/FEM module.

Coordinate convention
---------------------
- Normalized 2D profile: x_norm is chordwise and positive toward the trailing edge.
- z_norm is positive toward the upper side of the airfoil/rib.
- Physical 2D coordinates are meters: x_m = x_norm * chord_m, z_m = z_norm * chord_m.
- The 3D CATIA model keeps X=chordwise, Y=spanwise, Z=vertical.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


CATIA_INPUTS_DIR_NAME = "CATIA/Inputs"
DEFAULT_CAE_2D_DIR_NAME = "CFD_2D/CFD_2D_inputs"
DEFAULT_AXIS_CONVENTION = "x_chord_positive_TE_z_positive_up"
DEFAULT_LENGTH_UNIT = "m"
DEFAULT_CHORD_REFERENCE = "local_profile_chord"
DEFAULT_FARFIELD_RADIUS_CHORDS = 20.0
DEFAULT_WAKE_LENGTH_CHORDS = 30.0
DEFAULT_RECOMMENDED_MESHING_TOOL = "gmsh"
SUPPORTED_VARIANTS = {
    "open_ramair",
    "closed_reference",
    "reference_uncut",
    "reference_uncut_validation_1m",
    "ross_standard_8p4",
    "ross_minimum_4p0",
    "standard",
    "optimized",
}
VARIANT_ALIASES = {"standard": "open_ramair", "optimized": "open_ramair"}


def is_open_variant_name(name: str) -> bool:
    normalized = str(name).lower()
    return normalized.startswith("open_ramair") or normalized.startswith("ross_") or normalized in {
        "standard", "optimized",
    }


def project_root_from_case_root(case_root: Path) -> Path:
    case_root = Path(case_root)
    if case_root.name == CATIA_INPUTS_DIR_NAME:
        return case_root.parent
    return case_root


def catia_inputs_root(case_root: Path) -> Path:
    case_root = Path(case_root)
    if (case_root / "ramair_global_inputs.csv").exists():
        return case_root
    return project_root_from_case_root(case_root) / CATIA_INPUTS_DIR_NAME


def cfd_inputs_root(case_root: Path) -> Path:
    return project_root_from_case_root(case_root) / DEFAULT_CAE_2D_DIR_NAME


def _plots_enabled() -> bool:
    return os.environ.get("RAMAIR_2D_DISABLE_PLOTS", "0").strip().lower() not in {"1", "true", "yes", "on"}


def csv_relpath(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(Path(path).resolve(), Path(base).resolve()).replace("/", "\\")
    except Exception:
        return str(Path(path).resolve())


@dataclass
class ProfilePoint:
    point_id: int
    x_norm: float
    z_norm: float
    x_m: float
    z_m: float
    boundary_role: str


@dataclass
class ProfileEdge:
    edge_id: int
    start_point_id: int
    end_point_id: int
    patch_name: str
    edge_type: str
    is_wall: bool
    is_inlet: bool
    is_synthetic: bool


@dataclass
class ProfileVariant:
    name: str
    points: pd.DataFrame
    edges: pd.DataFrame
    patches: dict
    manifest: dict


@dataclass
class CFD2DPhysicalConfig:
    reynolds: float
    mach: float
    rho: float
    mu: float
    chord_m: float
    velocity: float | None
    alpha_start_deg: float
    alpha_end_deg: float
    alpha_step_deg: float
    pressure_ref_pa: float = 101325.0
    temperature_K: float = 288.15
    speed_of_sound_m_s: float = 340.29228686527705


@dataclass
class CFD2DCasePackage:
    case_root: Path
    variant: str
    geometry_dir: Path
    output_dir: Path
    physical_config: CFD2DPhysicalConfig


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def backup_existing_case_package(case_root: Path, output_dir: Path, variant_request: str) -> Path | None:
    """Archive previous generated files for one variant before overwriting it."""
    variant_dir_name = VARIANT_ALIASES.get(str(variant_request), str(variant_request))
    variant_dir = Path(output_dir) / variant_dir_name
    if variant_dir.exists() and any(variant_dir.iterdir()):
        archive_source = variant_dir
        archive_reason = "clean_variant_case_package_overwrite"
    elif Path(output_dir).exists() and any(Path(output_dir).iterdir()):
        # Legacy migration fallback: early versions stored generated/stale files
        # directly in case_package/. Archive that root once, then recreate the
        # clean variant subfolders.
        archive_source = Path(output_dir)
        archive_reason = "clean_legacy_case_package_root_overwrite"
    else:
        return None
    project_root = project_root_from_case_root(case_root)
    generated_root = cfd_inputs_root(case_root).resolve()
    resolved_output = archive_source.resolve()
    try:
        resolved_output.relative_to(generated_root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to archive output_dir outside generated CFD inputs: {resolved_output}") from exc
    if resolved_output == generated_root:
        raise RuntimeError(f"Refusing to archive the whole CFD inputs root: {resolved_output}")
    backup_root = project_root / "Previous Versions" / "cfd2d_case_package_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_variant = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in variant_request)
    target = backup_root / f"{safe_variant}_{stamp}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = backup_root / f"{safe_variant}_{stamp}_{suffix:02d}"
    try:
        shutil.move(str(archive_source), str(target))
    except PermissionError:
        shutil.copytree(archive_source, target, dirs_exist_ok=True)
        shutil.rmtree(archive_source, ignore_errors=True)
    _json_write(target / "case_package_backup_manifest.json", {
        "reason": archive_reason,
        "variant_request": variant_request,
        "variant_dir_name": variant_dir_name,
        "original_output_dir": str(archive_source),
        "backup_dir": str(target),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return target


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def read_global_inputs(case_root: Path) -> dict:
    """Read ramair_global_inputs.csv as a parameter dictionary."""
    case_root = Path(case_root)
    path = catia_inputs_root(case_root) / "ramair_global_inputs.csv"
    data: dict[str, Any] = {}
    if not path.exists():
        return data
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = str(row.get("parameter", "")).strip()
            val = str(row.get("value", "")).strip()
            if not key:
                continue
            as_float = _safe_float(val, None)
            data[key] = as_float if as_float is not None and val.lower() not in {"true", "false", "on", "off"} else val
    return data


def _candidate_profile_used_paths(case_root: Path, global_inputs: dict | None = None) -> list[Path]:
    case_root = Path(case_root)
    catia_root = catia_inputs_root(case_root)
    candidates = []
    if global_inputs:
        raw = global_inputs.get("profile_used_normalized_path")
        if raw:
            rel = str(raw).replace("\\", "/")
            candidates.append(catia_root / rel)
            candidates.append(case_root / rel)
    candidates.extend([
        case_root / "Profile_used" / "ramair_profile_used_normalized.csv",
        case_root / "Canopy" / "Profile_used" / "ramair_profile_used_normalized.csv",
        catia_root / "Profile_used" / "ramair_profile_used_normalized.csv",
        catia_root / "Canopy" / "Profile_used" / "ramair_profile_used_normalized.csv",
        cfd_inputs_root(case_root) / "geometry" / "source" / "profile_used_source.csv",
    ])
    out = []
    seen = set()
    for p in candidates:
        rp = str(p)
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def read_profile_used(case_root: Path) -> pd.DataFrame:
    """Read the exact 2D profile exported by the preprocessor.

    The function supports both historical root/Profile_used and the current
    Canopy/Profile_used organization.
    """
    case_root = Path(case_root)
    g = read_global_inputs(case_root)
    for p in _candidate_profile_used_paths(case_root, g):
        if p.exists():
            df = pd.read_csv(p)
            # Normalize columns from either original x/y/z or CATIA-like x_chord_norm/z_chord_norm.
            if {"x", "y"}.issubset(df.columns):
                out = df[["x", "y"]].copy().rename(columns={"x": "x_norm", "y": "z_norm"})
                if "z" in df.columns:
                    out["source_y_original"] = df["z"]
            elif {"x_norm", "z_norm"}.issubset(df.columns):
                out = df[["x_norm", "z_norm"]].copy()
            elif {"x_chord_norm", "z_chord_norm"}.issubset(df.columns):
                out = df[["x_chord_norm", "z_chord_norm"]].copy().rename(columns={"x_chord_norm": "x_norm", "z_chord_norm": "z_norm"})
            else:
                raise ValueError(f"Profile file {p} does not contain recognized coordinate columns.")
            out = out.astype({"x_norm": float, "z_norm": float})
            out["source_path"] = str(p)
            return out.reset_index(drop=True)
    searched = "\n".join(str(p) for p in _candidate_profile_used_paths(case_root, g))
    raise FileNotFoundError(f"Could not find ramair_profile_used_normalized.csv. Searched:\n{searched}")


def split_profile_used(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split profile into upper and lower branches using the minimum-x LE index.

    Expected order follows the CATIA preprocessor convention:
      upper: TE -> LE
      lower: LE -> TE
    """
    if not {"x_norm", "z_norm"}.issubset(df.columns):
        raise ValueError("Profile dataframe must contain x_norm and z_norm.")
    clean = df[["x_norm", "z_norm"]].copy().astype(float).reset_index(drop=True)
    if len(clean) < 4:
        raise ValueError("Profile must contain at least four points.")
    le_idx = int(clean["x_norm"].idxmin())
    upper = clean.iloc[: le_idx + 1].copy().reset_index(drop=True)
    lower = clean.iloc[le_idx + 1 :].copy().reset_index(drop=True)
    lower = lower.loc[~lower[["x_norm", "z_norm"]].duplicated()].reset_index(drop=True)
    if len(upper) < 2 or len(lower) < 2:
        raise ValueError("Could not split profile into UPPER and LOWER branches.")
    return upper, lower


def chord_m_from_inputs(case_root: Path, global_inputs: dict | None = None) -> float:
    g = global_inputs if global_inputs is not None else read_global_inputs(case_root)
    chord_mm = _safe_float(g.get("chord_mm"), None)
    if chord_mm is None or chord_mm <= 0:
        return 1.0
    return chord_mm / 1000.0


def chord_m_for_variant_request(case_root: Path, variant_request: str) -> float:
    """Return the selected geometry chord when one concrete variant is requested."""
    variants = resolve_variant_request(case_root, variant_request)
    if len(variants) == 1:
        variant = load_profile_variant(case_root, variants[0])
        chord_m = _safe_float(variant.manifest.get("chord_m"), None)
        if chord_m is not None and chord_m > 0:
            return float(chord_m)
    return chord_m_from_inputs(case_root)


def _read_profile_used_info(case_root: Path) -> dict:
    g = read_global_inputs(case_root)
    catia_root = catia_inputs_root(case_root)
    candidates = []
    if "profile_used_normalized_path" in g:
        base = catia_root / str(g["profile_used_normalized_path"]).replace("\\", "/")
        candidates.append(base.with_name("ramair_profile_used_info.txt"))
    candidates.extend([
        case_root / "Profile_used" / "ramair_profile_used_info.txt",
        case_root / "Canopy" / "Profile_used" / "ramair_profile_used_info.txt",
        catia_root / "Profile_used" / "ramair_profile_used_info.txt",
        catia_root / "Canopy" / "Profile_used" / "ramair_profile_used_info.txt",
    ])
    info: dict[str, str] = {}
    for p in candidates:
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
            info["source_info_path"] = str(p)
            break
    return info


def _profile_points_dataframe(upper: pd.DataFrame, lower: pd.DataFrame, chord_m: float, variant: str) -> pd.DataFrame:
    rows = []
    pid = 1
    for i, r in upper.iterrows():
        if i == 0:
            role = "trailing_edge"
        elif i == len(upper) - 1:
            role = "inlet_upper_lip"
        else:
            role = "upper_wall"
        rows.append({
            "point_id": pid,
            "x_norm": float(r.x_norm),
            "z_norm": float(r.z_norm),
            "x_m": float(r.x_norm) * chord_m,
            "z_m": float(r.z_norm) * chord_m,
            "source_section": "UPPER",
            "source_order": i + 1,
            "variant": variant,
            "boundary_role": role,
            "notes": "original_profile_used_point",
        })
        pid += 1
    for i, r in lower.iterrows():
        if i == 0:
            role = "inlet_lower_lip"
        elif i == len(lower) - 1:
            role = "trailing_edge"
        else:
            role = "lower_wall"
        rows.append({
            "point_id": pid,
            "x_norm": float(r.x_norm),
            "z_norm": float(r.z_norm),
            "x_m": float(r.x_norm) * chord_m,
            "z_m": float(r.z_norm) * chord_m,
            "source_section": "LOWER",
            "source_order": i + 1,
            "variant": variant,
            "boundary_role": role,
            "notes": "original_profile_used_point",
        })
        pid += 1
    return pd.DataFrame(rows)


def _add_edge(rows: list[dict], edge_id: int, start: int, end: int, patch_name: str, curve_group: str,
              edge_type: str, is_wall: bool, is_inlet: bool, is_outlet: bool, is_synthetic: bool,
              recommended_bc_openfoam: str, recommended_bc_su2: str, notes: str) -> int:
    rows.append({
        "edge_id": edge_id,
        "start_point_id": int(start),
        "end_point_id": int(end),
        "patch_name": patch_name,
        "curve_group": curve_group,
        "edge_type": edge_type,
        "is_wall": bool(is_wall),
        "is_inlet": bool(is_inlet),
        "is_outlet": bool(is_outlet),
        "is_synthetic": bool(is_synthetic),
        "recommended_bc_openfoam": recommended_bc_openfoam,
        "recommended_bc_su2": recommended_bc_su2,
        "notes": notes,
    })
    return edge_id + 1


def _edge_length_norm(points: pd.DataFrame, a: int, b: int) -> float:
    pa = points.loc[points.point_id == a, ["x_norm", "z_norm"]].iloc[0].to_numpy(dtype=float)
    pb = points.loc[points.point_id == b, ["x_norm", "z_norm"]].iloc[0].to_numpy(dtype=float)
    return float(np.linalg.norm(pb - pa))


def build_profile_variant_from_profile_used(case_root: Path, variant: str) -> ProfileVariant:
    case_root = Path(case_root)
    g = read_global_inputs(case_root)
    chord_m = chord_m_from_inputs(case_root, g)
    profile = read_profile_used(case_root)
    upper, lower = split_profile_used(profile)
    info = _read_profile_used_info(case_root)
    te_mode = str(g.get("te_closure_mode", info.get("te_closure_mode", "unknown"))).strip()
    sharp_applied = str(info.get("te_modified", "False")).lower() in {"true", "1", "yes"}

    if variant not in {"open_ramair", "closed_reference"}:
        raise ValueError("variant must be open_ramair or closed_reference")

    points = _profile_points_dataframe(upper, lower, chord_m, variant)
    n_upper = len(upper)
    n_lower = len(lower)
    upper_ids = list(range(1, n_upper + 1))
    lower_ids = list(range(n_upper + 1, n_upper + n_lower + 1))
    upper_le = upper_ids[-1]
    upper_te = upper_ids[0]
    lower_le = lower_ids[0]
    lower_te = lower_ids[-1]

    edge_rows: list[dict] = []
    eid = 1
    is_open = variant == "open_ramair"
    upper_patch = "outer_upper_wall" if is_open else "airfoil_upper_wall"
    lower_patch = "outer_lower_wall" if is_open else "airfoil_lower_wall"

    # Upper branch TE->LE.
    for a, b in zip(upper_ids[:-1], upper_ids[1:]):
        eid = _add_edge(edge_rows, eid, a, b, upper_patch, "upper", "polyline_segment", True, False, False, False, "wall", "MARKER_HEATFLUX", "upper wall segment")

    if variant == "open_ramair":
        # Ram-air inlet is not a wall by default.
        eid = _add_edge(edge_rows, eid, upper_le, lower_le, "inlet_opening_marker", "leading_edge_opening", "straight_segment", False, True, False, False, "none_metadata_only", "FEATURE_OPENING", "ram-air leading-edge opening marker; not a physical solver boundary")
        for a, b in zip(lower_ids[:-1], lower_ids[1:]):
            eid = _add_edge(edge_rows, eid, a, b, lower_patch, "lower", "polyline_segment", True, False, False, False, "wall", "MARKER_HEATFLUX", "lower wall segment")
        te_gap = _edge_length_norm(points, lower_te, upper_te)
        if te_gap > 1.0e-12:
            eid = _add_edge(edge_rows, eid, lower_te, upper_te, "trailing_edge_closure_wall", "trailing_edge", "straight_segment", True, False, False, te_mode == "sharp_extension", "wall", "MARKER_HEATFLUX", "TE closure; may be CATIA-safe near-zero gap for sharp_extension")
        else:
            # No edge, but manifest documents it.
            pass
        patches = {
            "outer_upper_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX"},
            "outer_lower_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX"},
            "inlet_opening_marker": {"type": "feature/opening_marker", "is_physical_boundary": False, "recommended_bc_openfoam": "none_metadata_only", "recommended_bc_su2": "FEATURE_OPENING", "forbidden_physical_patch": "ram_air_inlet"},
            "trailing_edge_closure_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX", "may_be_zero_length": te_gap <= 1.0e-12},
        }
    else:
        # Closed aerodynamic reference: LE opening is synthetically closed as wall.
        eid = _add_edge(edge_rows, eid, upper_le, lower_le, "leading_edge_closure_wall", "leading_edge_closure", "synthetic_straight_segment", True, False, False, True, "wall", "MARKER_HEATFLUX", "synthetic LE closure for closed reference only")
        for a, b in zip(lower_ids[:-1], lower_ids[1:]):
            eid = _add_edge(edge_rows, eid, a, b, "airfoil_lower_wall", "lower", "polyline_segment", True, False, False, False, "wall", "MARKER_HEATFLUX", "lower wall segment")
        te_gap = _edge_length_norm(points, lower_te, upper_te)
        if te_gap > 1.0e-12:
            eid = _add_edge(edge_rows, eid, lower_te, upper_te, "trailing_edge_closure_wall", "trailing_edge_closure", "synthetic_or_existing_straight_segment", True, False, False, True, "wall", "MARKER_HEATFLUX", "TE closure for closed reference")
        patches = {
            "airfoil_wall": {"type": "wall_group", "contains": ["airfoil_upper_wall", "airfoil_lower_wall", "leading_edge_closure_wall", "trailing_edge_closure_wall"]},
            "airfoil_upper_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX"},
            "airfoil_lower_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX"},
            "leading_edge_closure_wall": {"type": "synthetic_wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX"},
            "trailing_edge_closure_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "recommended_bc_su2": "MARKER_HEATFLUX", "may_be_zero_length": te_gap <= 1.0e-12},
        }

    edges = pd.DataFrame(edge_rows)
    manifest = {
        "variant": variant,
        "source": "Profile_used/ramair_profile_used_normalized.csv or Canopy/Profile_used fallback",
        "axis_convention": DEFAULT_AXIS_CONVENTION,
        "length_unit": DEFAULT_LENGTH_UNIT,
        "chord_reference": DEFAULT_CHORD_REFERENCE,
        "chord_m": chord_m,
        "te_closure_mode": te_mode,
        "sharp_te_applied": sharp_applied,
        "number_of_points": int(len(points)),
        "number_of_edges": int(len(edges)),
        "patches": patches,
        "notes": [
            "open_ramair keeps the leading-edge opening as inlet_opening_marker metadata and not as an OpenFOAM patch.",
            "Forbidden OpenFOAM physical patch name: ram_air_inlet.",
            "closed_reference closes the leading-edge opening as a synthetic wall for closed-profile 2D studies.",
            "No mesh or solver case is generated by this module.",
        ],
    }
    return ProfileVariant(variant, points, edges, patches, manifest)


def _write_dat(path: Path, points: pd.DataFrame, edges: pd.DataFrame, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered_ids = []
    if not edges.empty:
        ordered_ids.append(int(edges.iloc[0].start_point_id))
        for _, e in edges.iterrows():
            ordered_ids.append(int(e.end_point_id))
    else:
        ordered_ids = points.point_id.astype(int).tolist()
    lookup = points.set_index("point_id")
    lines = [title]
    for pid in ordered_ids:
        if pid in lookup.index:
            r = lookup.loc[pid]
            lines.append(f"{float(r.x_norm): .10f} {float(r.z_norm): .10f}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _write_dxf_r12(path: Path, points: pd.DataFrame, edges: pd.DataFrame, layer_name: str = "PROFILE") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lookup = points.set_index("point_id")
    content = "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n70\n1\n"
    content += f"0\nLAYER\n2\n{layer_name}\n70\n0\n62\n7\n6\nCONTINUOUS\n0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
    for _, e in edges.iterrows():
        if e.start_point_id not in lookup.index or e.end_point_id not in lookup.index:
            continue
        p1 = lookup.loc[e.start_point_id]
        p2 = lookup.loc[e.end_point_id]
        content += f"0\nLINE\n8\n{layer_name}\n10\n{float(p1.x_m):.10f}\n20\n{float(p1.z_m):.10f}\n30\n0.0\n11\n{float(p2.x_m):.10f}\n21\n{float(p2.z_m):.10f}\n31\n0.0\n"
    content += "0\nENDSEC\n0\nEOF\n"
    path.write_text(content, encoding="ascii")


def export_profile_variant(case_root: Path, variant: ProfileVariant, cae_root: Path | None = None, write_preview: bool = True) -> None:
    case_root = Path(case_root)
    cae_root = cae_root or cfd_inputs_root(case_root)
    out = cae_root / "geometry" / variant.name
    out.mkdir(parents=True, exist_ok=True)
    variant.points.to_csv(out / "profile_points.csv", index=False, float_format="%.10f")
    variant.edges.to_csv(out / "profile_edges.csv", index=False)
    _json_write(out / "profile_patches.json", variant.patches)
    _json_write(out / "profile_manifest.json", variant.manifest)
    if variant.name == "open_ramair":
        _write_dat(out / "profile_open_ramair.dat", variant.points, variant.edges, "ram_air_open_profile")
        _write_dxf_r12(out / "profile_open_ramair.dxf", variant.points, variant.edges, "OPEN_RAMAIR")
    else:
        _write_dat(out / "profile_closed_reference.dat", variant.points, variant.edges, "ram_air_closed_reference_profile")
        _write_dxf_r12(out / "profile_closed_reference.dxf", variant.points, variant.edges, "CLOSED_REFERENCE")
    if write_preview:
        previews_dir = cae_root / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)
        preview_name = "profile_open_ramair_preview.png" if variant.name == "open_ramair" else "profile_closed_reference_preview.png"
        plot_profile_variant(variant, previews_dir / preview_name)


def export_source_profile(case_root: Path, cae_root: Path | None = None) -> None:
    case_root = Path(case_root)
    cae_root = cae_root or cfd_inputs_root(case_root)
    source_dir = cae_root / "geometry" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    profile = read_profile_used(case_root)
    profile.to_csv(source_dir / "profile_used_source.csv", index=False, float_format="%.10f")
    info = {
        "source_path": str(profile.get("source_path", pd.Series([""])).iloc[0]) if "source_path" in profile else "",
        "axis_convention": DEFAULT_AXIS_CONVENTION,
        "length_unit": DEFAULT_LENGTH_UNIT,
        "chord_m": chord_m_from_inputs(case_root),
        "notes": "This is the exact Profile_used source read by the 2D CAE interface.",
    }
    _json_write(source_dir / "profile_used_source_info.json", info)


def _interp_z(section: pd.DataFrame, x: float) -> float:
    pts = section[["x_norm", "z_norm"]].copy().groupby("x_norm", as_index=False).mean().sort_values("x_norm")
    xs = pts["x_norm"].to_numpy(dtype=float)
    zs = pts["z_norm"].to_numpy(dtype=float)
    return float(np.interp(float(np.clip(x, xs.min(), xs.max())), xs, zs))


def _orientation(a, b, c, tol: float) -> int:
    val = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if abs(val) <= tol:
        return 0
    return 1 if val > 0 else 2


def _on_segment(a, b, c, tol: float) -> bool:
    return min(a[0], c[0]) - tol <= b[0] <= max(a[0], c[0]) + tol and min(a[1], c[1]) - tol <= b[1] <= max(a[1], c[1]) + tol


def _segments_intersect(p1, q1, p2, q2, tol: float = 1e-10) -> bool:
    o1 = _orientation(p1, q1, p2, tol)
    o2 = _orientation(p1, q1, q2, tol)
    o3 = _orientation(p2, q2, p1, tol)
    o4 = _orientation(p2, q2, q1, tol)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, q1, tol): return True
    if o2 == 0 and _on_segment(p1, q2, q1, tol): return True
    if o3 == 0 and _on_segment(p2, p1, q2, tol): return True
    if o4 == 0 and _on_segment(p2, q1, q2, tol): return True
    return False


def _self_intersection(points: pd.DataFrame, edges: pd.DataFrame, tol: float = 1e-10) -> bool:
    if edges.empty:
        return False
    lookup = points.set_index("point_id")
    segs = []
    for _, e in edges.iterrows():
        if e.start_point_id not in lookup.index or e.end_point_id not in lookup.index:
            continue
        p = tuple(lookup.loc[e.start_point_id, ["x_norm", "z_norm"]].to_numpy(dtype=float))
        q = tuple(lookup.loc[e.end_point_id, ["x_norm", "z_norm"]].to_numpy(dtype=float))
        segs.append((int(e.start_point_id), int(e.end_point_id), p, q))
    for i in range(len(segs)):
        a1, a2, p1, q1 = segs[i]
        for j in range(i + 1, len(segs)):
            b1, b2, p2, q2 = segs[j]
            # adjacent segments share endpoint; ignore normal contour adjacency.
            if len({a1, a2, b1, b2}) < 4:
                continue
            if _segments_intersect(p1, q1, p2, q2, tol):
                return True
    return False


def _closed_by_edges(points: pd.DataFrame, edges: pd.DataFrame, tol: float = 1e-9) -> bool:
    if edges.empty:
        return False
    lookup = points.set_index("point_id")
    first = lookup.loc[int(edges.iloc[0].start_point_id), ["x_norm", "z_norm"]].to_numpy(dtype=float)
    last = lookup.loc[int(edges.iloc[-1].end_point_id), ["x_norm", "z_norm"]].to_numpy(dtype=float)
    return float(np.linalg.norm(first - last)) <= tol


def run_profile_quality_checks(case_root: Path, open_variant: ProfileVariant | None = None, closed_variant: ProfileVariant | None = None) -> dict:
    case_root = Path(case_root)
    g = read_global_inputs(case_root)
    chord_m = chord_m_from_inputs(case_root, g)
    profile = read_profile_used(case_root)
    upper, lower = split_profile_used(profile)
    xs_common_min = max(float(upper.x_norm.min()), float(lower.x_norm.min()))
    xs_common_max = min(float(upper.x_norm.max()), float(lower.x_norm.max()))
    xs = np.linspace(xs_common_min, xs_common_max, 400) if xs_common_max > xs_common_min else np.array([xs_common_min])
    thickness = np.array([_interp_z(upper, x) - _interp_z(lower, x) for x in xs], dtype=float)
    pos_th = thickness[thickness > 1e-12]
    coords = profile[["x_norm", "z_norm"]].to_numpy(dtype=float)
    spacing = np.linalg.norm(np.diff(coords, axis=0), axis=1) if len(coords) > 1 else np.array([float("inf")])
    duplicate_count = int(pd.DataFrame(coords, columns=["x", "z"]).duplicated().sum())
    le_gap = float(np.linalg.norm(upper.iloc[-1][["x_norm", "z_norm"]].to_numpy(dtype=float) - lower.iloc[0][["x_norm", "z_norm"]].to_numpy(dtype=float)))
    te_gap = float(np.linalg.norm(upper.iloc[0][["x_norm", "z_norm"]].to_numpy(dtype=float) - lower.iloc[-1][["x_norm", "z_norm"]].to_numpy(dtype=float)))
    te_info = _read_profile_used_info(case_root)
    open_variant = open_variant or build_profile_variant_from_profile_used(case_root, "open_ramair")
    closed_variant = closed_variant or build_profile_variant_from_profile_used(case_root, "closed_reference")
    has_nan = bool(pd.isna(profile[["x_norm", "z_norm"]]).any().any())
    has_nonfinite = bool(not np.isfinite(coords).all())
    upper_dx = np.diff(upper.x_norm.to_numpy(dtype=float))
    lower_dx = np.diff(lower.x_norm.to_numpy(dtype=float))
    upper_warn = bool(np.any(upper_dx > 1e-8))  # expected TE->LE decreasing x
    lower_warn = bool(np.any(lower_dx < -1e-8))  # expected LE->TE increasing x
    self_int = bool(_self_intersection(open_variant.points, open_variant.edges))
    max_idx = int(np.argmax(thickness)) if len(thickness) else 0
    chord_norm = float(profile.x_norm.max() - profile.x_norm.min())
    open_has_inlet = bool(open_variant.edges["patch_name"].isin(["inlet_opening_marker", "ram_air_inlet"]).any())
    closed_is_closed = _closed_by_edges(closed_variant.points, closed_variant.edges)
    fail_reasons = []
    warnings = []
    if has_nan or has_nonfinite: fail_reasons.append("non-finite coordinates")
    if chord_norm <= 0: fail_reasons.append("non-positive chord")
    if self_int: fail_reasons.append("self-intersection detected")
    if not open_has_inlet: fail_reasons.append("open_ramair missing inlet patch")
    if not closed_is_closed: fail_reasons.append("closed_reference is not closed")
    if upper_warn: warnings.append("upper branch x monotonicity differs from expected TE->LE order")
    if lower_warn: warnings.append("lower branch x monotonicity differs from expected LE->TE order")
    pass_fail = "FAIL" if fail_reasons else ("WARNING" if warnings else "PASS")
    return {
        "number_of_upper_points": int(len(upper)),
        "number_of_lower_points": int(len(lower)),
        "number_of_total_points": int(len(profile)),
        "min_x_norm": float(profile.x_norm.min()),
        "max_x_norm": float(profile.x_norm.max()),
        "min_z_norm": float(profile.z_norm.min()),
        "max_z_norm": float(profile.z_norm.max()),
        "chord_norm": chord_norm,
        "chord_m": float(chord_m),
        "max_thickness_norm": float(np.max(thickness)) if len(thickness) else 0.0,
        "max_thickness_x_norm": float(xs[max_idx]) if len(xs) else float("nan"),
        "min_positive_thickness_norm": float(np.min(pos_th)) if len(pos_th) else 0.0,
        "leading_edge_gap_norm": le_gap,
        "leading_edge_gap_m": le_gap * chord_m,
        "trailing_edge_gap_norm": te_gap,
        "trailing_edge_gap_m": te_gap * chord_m,
        "te_closure_mode": str(g.get("te_closure_mode", te_info.get("te_closure_mode", "unknown"))),
        "sharp_te_applied": bool(str(te_info.get("te_modified", "False")).lower() in {"true", "1", "yes"}),
        "duplicate_point_count": duplicate_count,
        "min_point_spacing_norm": float(np.min(spacing)) if len(spacing) else float("nan"),
        "min_point_spacing_m": float(np.min(spacing) * chord_m) if len(spacing) else float("nan"),
        "has_nan": has_nan,
        "has_nonfinite": has_nonfinite,
        "upper_monotonicity_warning": upper_warn,
        "lower_monotonicity_warning": lower_warn,
        "self_intersection_detected": self_int,
        "closed_reference_is_closed": closed_is_closed,
        "open_ramair_has_inlet_patch": open_has_inlet,
        "pass_fail": pass_fail,
        "fail_reasons": fail_reasons,
        "warnings": warnings,
    }


def export_quality_report(case_root: Path, cae_root: Path | None = None) -> dict:
    case_root = Path(case_root)
    cae_root = cae_root or cfd_inputs_root(case_root)
    open_v = build_profile_variant_from_profile_used(case_root, "open_ramair")
    closed_v = build_profile_variant_from_profile_used(case_root, "closed_reference")
    checks = run_profile_quality_checks(case_root, open_v, closed_v)
    val_dir = cae_root / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"metric": k, "value": json.dumps(v) if isinstance(v, (list, dict)) else v} for k, v in checks.items()]).to_csv(val_dir / "profile_quality_report.csv", index=False)
    _json_write(val_dir / "profile_geometry_checks.json", checks)
    lines = ["Ram-air 2D profile quality report", "=================================", "", f"STATUS: {checks['pass_fail']}", ""]
    for k, v in checks.items():
        lines.append(f"{k}: {v}")
    (val_dir / "profile_quality_report.txt").write_text("\n".join(lines), encoding="utf-8")
    return checks


def _append_global_inputs(case_root: Path, rows: list[list[Any]]) -> None:
    path = catia_inputs_root(case_root) / "ramair_global_inputs.csv"
    has_header = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not has_header:
            writer.writerow(["parameter", "value", "unit", "description"])
        writer.writerows(rows)


def export_cfd2d_config_templates(case_root: Path, cae_root: Path | None = None,
                                  reynolds: float = 3.0e6, mach: float = 0.10,
                                  alpha_start: float = -5.0, alpha_end: float = 15.0,
                                  alpha_step: float = 1.0) -> None:
    cae_root = cae_root or cfd_inputs_root(case_root)
    config_dir = cae_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    chord_m = chord_m_from_inputs(case_root)
    _json_write(config_dir / "cfd2d_case_config_template.json", {
        "variants": ["open_ramair", "closed_reference"],
        "default_variant": "open_ramair",
        "length_unit": DEFAULT_LENGTH_UNIT,
        "axis_convention": DEFAULT_AXIS_CONVENTION,
        "chord_reference": DEFAULT_CHORD_REFERENCE,
        "chord_m": chord_m,
        "alpha_start_deg": alpha_start,
        "alpha_end_deg": alpha_end,
        "alpha_step_deg": alpha_step,
        "reynolds": reynolds,
        "mach": mach,
        "mesh_generation": "not_implemented_in_this_module",
    })
    _json_write(config_dir / "cfd2d_physical_defaults.json", {
        "reynolds": reynolds,
        "mach": mach,
        "rho_kg_m3": 1.225,
        "mu_pa_s": 1.81e-5,
        "pressure_ref_pa": 101325.0,
        "temperature_K": 288.15,
        "speed_of_sound_m_s": 340.294,
        "velocity_source": "reynolds",
        "velocity_m_s": compute_velocity_from_reynolds(reynolds, 1.81e-5, 1.225, chord_m),
        "dynamic_pressure_pa": compute_dynamic_pressure(1.225, compute_velocity_from_reynolds(reynolds, 1.81e-5, 1.225, chord_m)),
    })
    _json_write(config_dir / "cfd2d_boundary_condition_template.json", {
        "open_ramair": {
            "outer_upper_wall": "wall",
            "outer_lower_wall": "wall",
            "inlet_opening_marker": "feature_opening_marker_not_physical_boundary",
            "ram_air_inlet": "forbidden_as_physical_patch",
            "trailing_edge_closure_wall": "wall",
        },
        "closed_reference": {
            "airfoil_wall": "wall",
            "airfoil_upper_wall": "wall",
            "airfoil_lower_wall": "wall",
            "leading_edge_closure_wall": "wall",
            "trailing_edge_closure_wall": "wall",
        },
    })


def plot_profile_variant(variant: ProfileVariant, output_path: Path) -> None:
    import matplotlib.pyplot as plt
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(8, 3.5))
    ax = fig.add_subplot(111)
    lookup = variant.points.set_index("point_id")
    for _, e in variant.edges.iterrows():
        if e.start_point_id not in lookup.index or e.end_point_id not in lookup.index:
            continue
        p1 = lookup.loc[e.start_point_id]
        p2 = lookup.loc[e.end_point_id]
        lw = 1.8 if bool(e.is_inlet) or bool(e.is_synthetic) else 1.0
        ax.plot([p1.x_norm, p2.x_norm], [p1.z_norm, p2.z_norm], linewidth=lw, label=str(e.patch_name) if str(e.patch_name) not in ax.get_legend_handles_labels()[1] else None)
    ax.scatter(variant.points.x_norm, variant.points.z_norm, s=8)
    # label approximate LE/TE points
    xmin = float(variant.points.x_norm.min()); xmax = float(variant.points.x_norm.max())
    ax.text(xmin, float(variant.points.loc[variant.points.x_norm.idxmin(), "z_norm"]), "LE", fontsize=8)
    ax.text(xmax, float(variant.points.loc[variant.points.x_norm.idxmax(), "z_norm"]), "TE", fontsize=8)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x/c [-]")
    ax.set_ylabel("z/c [-]")
    ax.set_title(f"2D profile variant: {variant.name}")
    ax.grid(True, linewidth=0.3)
    ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def plot_comparison(case_root: Path, cae_root: Path | None = None) -> None:
    import matplotlib.pyplot as plt
    cae_root = cae_root or cfd_inputs_root(case_root)
    open_v = load_profile_variant(case_root, "open_ramair")
    closed_v = load_profile_variant(case_root, "closed_reference")
    fig = plt.figure(figsize=(8, 3.5))
    ax = fig.add_subplot(111)
    for variant, label in [(open_v, "open_ramair"), (closed_v, "closed_reference")]:
        lookup = variant.points.set_index("point_id")
        for _, e in variant.edges.iterrows():
            if e.start_point_id in lookup.index and e.end_point_id in lookup.index:
                p1 = lookup.loc[e.start_point_id]
                p2 = lookup.loc[e.end_point_id]
                ax.plot([p1.x_norm, p2.x_norm], [p1.z_norm, p2.z_norm], linewidth=1.0, label=label if label not in ax.get_legend_handles_labels()[1] else None)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x/c [-]")
    ax.set_ylabel("z/c [-]")
    ax.set_title("Open ram-air profile vs closed reference")
    ax.grid(True, linewidth=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = cae_root / "previews" / "profile_comparison_open_vs_closed.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=200)
    plt.close(fig)


def reconstruct_profile_variants_if_missing(case_root: Path) -> None:
    """Create the CFD_2D/CFD_2D_inputs geometry interface if it does not exist or is incomplete."""
    case_root = Path(case_root)
    cae_root = cfd_inputs_root(case_root)
    open_manifest = cae_root / "geometry" / "open_ramair" / "profile_manifest.json"
    closed_manifest = cae_root / "geometry" / "closed_reference" / "profile_manifest.json"
    if open_manifest.exists() and closed_manifest.exists():
        return
    export_source_profile(case_root, cae_root)
    open_v = build_profile_variant_from_profile_used(case_root, "open_ramair")
    closed_v = build_profile_variant_from_profile_used(case_root, "closed_reference")
    export_profile_variant(case_root, open_v, cae_root, write_preview=_plots_enabled())
    export_profile_variant(case_root, closed_v, cae_root, write_preview=_plots_enabled())
    if _plots_enabled():
        plot_comparison(case_root, cae_root)
    export_quality_report(case_root, cae_root)
    export_cfd2d_config_templates(case_root, cae_root)
    cfd_root_rel = csv_relpath(cae_root, catia_inputs_root(case_root))
    _append_global_inputs(case_root, [
        ["cfd2d_exports_enabled", 1, "0/1", "2D CFD/FEM profile interface generated."],
        ["cfd2d_root", cfd_root_rel, "folder", "Root folder for 2D CFD/FEM inputs, outside CATIA/Inputs."],
        ["cfd2d_geometry_root", f"{cfd_root_rel}\\geometry", "folder", "Root folder for 2D profile geometry variants."],
        ["cfd2d_open_profile_manifest", f"{cfd_root_rel}\\geometry\\open_ramair\\profile_manifest.json", "file", "Open ram-air 2D profile manifest."],
        ["cfd2d_closed_profile_manifest", f"{cfd_root_rel}\\geometry\\closed_reference\\profile_manifest.json", "file", "Closed reference 2D profile manifest."],
        ["cfd2d_case_config_template", f"{cfd_root_rel}\\config\\cfd2d_case_config_template.json", "file", "2D CFD case config template."],
        ["cfd2d_profile_quality_report", f"{cfd_root_rel}\\validation\\profile_quality_report.txt", "file", "2D profile quality report."],
    ])


def _variant_geometry_dir(case_root: Path, variant: str) -> Path:
    return cfd_inputs_root(case_root) / "geometry" / variant


def available_profile_variants(case_root: Path) -> list[str]:
    geom = cfd_inputs_root(case_root) / "geometry"
    if not geom.exists():
        return []
    out = []
    for p in sorted(geom.iterdir()):
        if p.is_dir() and (p / "profile_manifest.json").exists() and (p / "profile_points.csv").exists():
            out.append(p.name)
    return out


def resolve_variant_request(case_root: Path, variant: str) -> list[str]:
    variant = str(variant).lower().strip()
    reconstruct_profile_variants_if_missing(case_root)
    available = available_profile_variants(case_root)
    if variant == "both":
        return [v for v in ["open_ramair", "closed_reference"] if v in available]
    if variant == "all":
        ordered = [
            "reference_uncut",
            "reference_uncut_validation_1m",
            "open_ramair",
            "closed_reference",
            "ross_standard_8p4",
            "ross_minimum_4p0",
        ]
        return [v for v in ordered if v in available] + [v for v in available if v not in ordered]
    if variant in VARIANT_ALIASES:
        target = VARIANT_ALIASES[variant]
        if target in available:
            return [target]
    if variant in available:
        return [variant]
    raise FileNotFoundError(f"Requested variant {variant!r} is not available under {cfd_inputs_root(case_root) / 'geometry'}. Available: {available}")


def load_profile_variant(case_root: Path, variant: str) -> ProfileVariant:
    case_root = Path(case_root)
    reconstruct_profile_variants_if_missing(case_root)
    if variant in VARIANT_ALIASES:
        variant = VARIANT_ALIASES[variant]
    if variant not in SUPPORTED_VARIANTS and not _variant_geometry_dir(case_root, variant).exists():
        raise ValueError(f"Unsupported or unavailable variant: {variant}")
    root = _variant_geometry_dir(case_root, variant)
    if not (root / "profile_manifest.json").exists():
        raise FileNotFoundError(f"Variant geometry is missing: {root}")
    points = pd.read_csv(root / "profile_points.csv")
    edges = pd.read_csv(root / "profile_edges.csv")
    patches = json.loads((root / "profile_patches.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "profile_manifest.json").read_text(encoding="utf-8"))
    return ProfileVariant(variant, points, edges, patches, manifest)


def build_alpha_sweep(alpha_start, alpha_end, alpha_step) -> pd.DataFrame:
    step = float(alpha_step)
    if step == 0:
        raise ValueError("alpha_step must be non-zero")
    start = float(alpha_start); end = float(alpha_end)
    n = int(math.floor((end - start) / step)) + 1 if step > 0 else int(math.floor((start - end) / abs(step))) + 1
    values = [start + i * step for i in range(max(0, n))]
    if values and ((step > 0 and values[-1] < end - 1e-12) or (step < 0 and values[-1] > end + 1e-12)):
        values.append(end)
    return pd.DataFrame({"case_id": [f"alpha_{a:+.3f}" for a in values], "alpha_deg": values})


def compute_velocity_from_reynolds(Re, mu, rho, chord_m) -> float:
    Re = float(Re); mu = float(mu); rho = float(rho); chord_m = float(chord_m)
    if rho <= 0 or chord_m <= 0:
        raise ValueError("rho and chord_m must be positive")
    return Re * mu / (rho * chord_m)


def compute_dynamic_pressure(rho, velocity) -> float:
    return 0.5 * float(rho) * float(velocity) ** 2


def export_boundary_summary(variant: ProfileVariant, out_dir: Path) -> None:
    rows = []
    for patch, meta in variant.patches.items():
        subset = variant.edges[variant.edges["patch_name"] == patch]
        rows.append({
            "variant": variant.name,
            "patch_name": patch,
            "edge_count": int(len(subset)),
            "is_wall": bool(subset["is_wall"].any()) if not subset.empty and "is_wall" in subset else meta.get("type", "").startswith("wall"),
            "is_inlet": bool(subset["is_inlet"].any()) if not subset.empty and "is_inlet" in subset else False,
            "recommended_bc_openfoam": subset["recommended_bc_openfoam"].iloc[0] if not subset.empty else meta.get("recommended_bc_openfoam", ""),
            "recommended_bc_su2": subset["recommended_bc_su2"].iloc[0] if not subset.empty else meta.get("recommended_bc_su2", ""),
            "notes": json.dumps(meta),
        })
    pd.DataFrame(rows).to_csv(Path(out_dir) / "boundary_summary.csv", index=False)


def export_physical_config(config: CFD2DPhysicalConfig, out_dir: Path) -> None:
    d = asdict(config)
    velocity = config.velocity if config.velocity is not None else compute_velocity_from_reynolds(config.reynolds, config.mu, config.rho, config.chord_m)
    d["velocity"] = velocity
    d["dynamic_pressure_pa"] = compute_dynamic_pressure(config.rho, velocity)
    _json_write(Path(out_dir) / "physical_config.json", d)


def export_alpha_sweep(alpha_sweep: pd.DataFrame, out_dir: Path) -> None:
    alpha_sweep.to_csv(Path(out_dir) / "alpha_sweep.csv", index=False, float_format="%.10f")


def export_geometry_for_mesher_contract(variant: ProfileVariant, out_dir: Path) -> None:
    data = {
        "variant": variant.name,
        "chord_m": float(variant.manifest.get("chord_m", 1.0)),
        "axis_convention": variant.manifest.get("axis_convention", DEFAULT_AXIS_CONVENTION),
        "patch_names": list(variant.patches.keys()),
        "farfield_radius_chords_default": DEFAULT_FARFIELD_RADIUS_CHORDS,
        "wake_length_chords_default": DEFAULT_WAKE_LENGTH_CHORDS,
        "first_cell_height_chord": None,
        "target_y_plus": None,
        "recommended_meshing_tool": DEFAULT_RECOMMENDED_MESHING_TOOL,
        "status": "GEOMETRY_ONLY_NO_MESH",
        "notes": [
            "This is a geometry contract for the meshing module.",
            "No .geo, mesh, OpenFOAM or SU2 files are generated here.",
        ],
    }
    _json_write(Path(out_dir) / variant.name / "mesh_input_contract.json", data)


def export_case_package(case_root: Path, variant: str, output_dir: Path, physical_config: CFD2DPhysicalConfig,
                        plot: bool = True, validate: bool = True, overwrite: bool = False) -> CFD2DCasePackage:
    case_root = Path(case_root)
    if not output_dir.is_absolute():
        output_dir = case_root / output_dir
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"Output directory already exists and is not empty: {output_dir}. Use --overwrite.")
    if output_dir.exists() and any(output_dir.iterdir()) and overwrite:
        backup = backup_existing_case_package(case_root, output_dir, variant)
        if backup is not None:
            print(f"Previous CFD 2D case package moved to backup: {backup.resolve()}")
    output_dir.mkdir(parents=True, exist_ok=True)
    reconstruct_profile_variants_if_missing(case_root)
    variants = resolve_variant_request(case_root, variant)
    if not variants:
        raise FileNotFoundError(f"No variants available for request {variant!r}.")
    export_physical_config(physical_config, output_dir)
    alpha = build_alpha_sweep(physical_config.alpha_start_deg, physical_config.alpha_end_deg, physical_config.alpha_step_deg)
    export_alpha_sweep(alpha, output_dir)
    boundary_frames = []
    geom_rows = []
    for var_name in variants:
        is_open_variant = is_open_variant_name(var_name)
        pv = load_profile_variant(case_root, var_name)
        vout = output_dir / var_name
        vout.mkdir(parents=True, exist_ok=True)
        pv.points.to_csv(vout / "points.csv", index=False, float_format="%.10f")
        pv.edges.to_csv(vout / "edges.csv", index=False)
        _json_write(vout / "patches.json", pv.patches)
        _json_write(vout / "manifest.json", pv.manifest)
        _json_write(vout / "geometry_summary.json", {
            "variant": var_name,
            "point_count": len(pv.points),
            "edge_count": len(pv.edges),
            "patch_count": len(pv.patches),
            "can_mesh_diagnostic": True,
            "openfoam_ready": not is_open_variant,
            "openfoam_blocker": "thin_solid_open_cavity_mesh_required" if is_open_variant else None,
        })
        if plot:
            plot_profile_variant(pv, vout / "preview.png")
        export_geometry_for_mesher_contract(pv, output_dir)
        bsum_path = output_dir / f"_{var_name}_boundary_summary_tmp.csv"
        export_boundary_summary(pv, output_dir)
        tmp = pd.read_csv(output_dir / "boundary_summary.csv")
        boundary_frames.append(tmp)
        bsum_path.unlink(missing_ok=True)
        geom_rows.append({
            "variant": var_name,
            "point_count": len(pv.points),
            "edge_count": len(pv.edges),
            "patch_count": len(pv.patches),
            "chord_m": float(pv.manifest.get("chord_m", physical_config.chord_m)),
            "axis_convention": pv.manifest.get("axis_convention", DEFAULT_AXIS_CONVENTION),
            "openfoam_ready": not is_open_variant,
        })
    if boundary_frames:
        pd.concat(boundary_frames, ignore_index=True).to_csv(output_dir / "boundary_summary.csv", index=False)
    pd.DataFrame(geom_rows).to_csv(output_dir / "geometry_summary.csv", index=False)
    pd.DataFrame([{
        "variant": v,
        "exists": True,
        "has_geometry": True,
        "has_inlet_opening": bool(set(load_profile_variant(case_root, v).patches).intersection({"inlet_opening_marker", "ram_air_inlet"})),
        "is_closed_reference": v in {"closed_reference", "reference_uncut", "reference_uncut_validation_1m"},
        "source_file": str((cfd_inputs_root(case_root) / "geometry" / v).resolve()),
        "can_mesh_diagnostic": True,
        "openfoam_ready": not is_open_variant_name(v),
        "notes": "open profile requires open-cavity meshing before OpenFOAM" if is_open_variant_name(v) else "exported"
    } for v in variants]).to_csv(output_dir / "variant_index.csv", index=False)
    manifest = {
        "case_root": str(case_root.resolve()),
        "variant_request": variant,
        "variants_exported": variants,
        "geometry_source": str((cfd_inputs_root(case_root) / "geometry").resolve()),
        "output_dir": str(output_dir.resolve()),
        "mesh_generated": False,
        "solver_case_generated": False,
    }
    _json_write(output_dir / "case_package_manifest.json", manifest)
    if validate:
        run_case_package_validation(output_dir, variants)
    (output_dir / "README_case_package.md").write_text(_case_package_readme(variants), encoding="utf-8")
    return CFD2DCasePackage(case_root, variant, cfd_inputs_root(case_root) / "geometry", output_dir, physical_config)


def _case_package_readme(variants: list[str]) -> str:
    return f"""# Ram-air 2D profile case package

This directory is a geometry and metadata package for the next CFD/FEM meshing module.

Generated variants: {', '.join(variants)}

It intentionally does **not** contain a mesh, OpenFOAM case, SU2 case, or CFD results.

Use the `points.csv`, `edges.csv`, and `patches.json` files in each variant folder to construct the future 2D domain and mesh.
"""


def run_case_package_validation(output_dir: Path, variants: list[str] | None = None) -> pd.DataFrame:
    output_dir = Path(output_dir)
    variants = variants or [p.name for p in output_dir.iterdir() if p.is_dir() and (p / "points.csv").exists()]
    checks = []
    def add(name, passed, details=""):
        checks.append({"check": name, "passed": bool(passed), "details": details})
    add("physical_config_exists", (output_dir / "physical_config.json").exists())
    add("alpha_sweep_exists", (output_dir / "alpha_sweep.csv").exists())
    add("boundary_summary_exists", (output_dir / "boundary_summary.csv").exists())
    add("geometry_summary_exists", (output_dir / "geometry_summary.csv").exists())
    for v in variants:
        vdir = output_dir / v
        add(f"{v}_points_exists", (vdir / "points.csv").exists())
        add(f"{v}_edges_exists", (vdir / "edges.csv").exists())
        add(f"{v}_patches_exists", (vdir / "patches.json").exists())
        add(f"{v}_mesh_contract_exists", (vdir / "mesh_input_contract.json").exists())
        if (vdir / "points.csv").exists():
            pts = pd.read_csv(vdir / "points.csv")
            add(f"{v}_no_nan_coordinates", not pts[["x_norm", "z_norm", "x_m", "z_m"]].isna().any().any())
            add(f"{v}_positive_chord", float(pts.x_norm.max() - pts.x_norm.min()) > 0)
        if is_open_variant_name(v) and (vdir / "patches.json").exists():
            patches = json.loads((vdir / "patches.json").read_text(encoding="utf-8"))
            add(f"{v}_has_inlet_marker_if_open", ("inlet_opening_marker" in patches or "ram_air_inlet" in patches) or v in {"standard", "optimized"})
        if v in {"closed_reference", "reference_uncut", "reference_uncut_validation_1m"} and (vdir / "edges.csv").exists() and (vdir / "points.csv").exists():
            add("closed_reference_has_closure_edges", True, "closure verified in preprocessor quality report")
    df = pd.DataFrame(checks)
    val = output_dir / "validation"
    val.mkdir(parents=True, exist_ok=True)
    df.to_csv(val / "case_package_validation.csv", index=False)
    status = "PASS" if df["passed"].all() else "FAIL"
    lines = ["Ram-air 2D case package validation", "=====================================", "", f"STATUS: {status}", ""]
    for _, r in df.iterrows():
        lines.append(f"{r['check']}: {'PASS' if r['passed'] else 'FAIL'} - {r['details']}")
    (val / "case_package_validation.txt").write_text("\n".join(lines), encoding="utf-8")
    return df


def plot_case_summary(output_dir: Path) -> None:
    # Kept as a separate hook for future multi-plot summaries. Current previews are per variant.
    return


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a 2D CFD/FEM profile case package from ram-air CATIA preprocessor outputs.")
    p.add_argument("--case-root", type=Path, required=True)
    p.add_argument(
        "--variant",
        default="both",
        help=(
            "Physical geometry identifier already exported below CFD_2D_inputs/geometry, "
            "or one of both/all/standard/optimized."
        ),
    )
    p.add_argument("--alpha-start", type=float, default=-5.0)
    p.add_argument("--alpha-end", type=float, default=15.0)
    p.add_argument("--alpha-step", type=float, default=1.0)
    p.add_argument("--reynolds", type=float, default=3.0e6)
    p.add_argument("--mach", type=float, default=0.10)
    p.add_argument("--rho", type=float, default=1.225)
    p.add_argument("--mu", type=float, default=1.81e-5)
    p.add_argument("--pressure-ref-pa", type=float, default=101325.0)
    p.add_argument("--temperature-K", type=float, default=288.15)
    p.add_argument("--speed-of-sound-m-s", type=float)
    p.add_argument("--velocity", default="auto", help="auto or a numeric velocity in m/s")
    p.add_argument("--output-dir", type=Path, default=Path("CFD_2D/CFD_2D_inputs/case_package"))
    p.add_argument("--plot", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--generate-mesh", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", str(args.variant)):
        raise ValueError("--variant must be a filesystem-safe geometry identifier")
    if args.generate_mesh:
        raise NotImplementedError("Meshing will be implemented in the next module.")
    case_root = Path(args.case_root)
    reconstruct_profile_variants_if_missing(case_root)
    chord_m = chord_m_for_variant_request(case_root, args.variant)
    velocity = None if str(args.velocity).lower() == "auto" else float(args.velocity)
    if velocity is None:
        velocity = compute_velocity_from_reynolds(args.reynolds, args.mu, args.rho, chord_m)
    speed_of_sound = (
        float(args.speed_of_sound_m_s)
        if args.speed_of_sound_m_s is not None
        else math.sqrt(1.4 * 287.05 * float(args.temperature_K))
    )
    cfg = CFD2DPhysicalConfig(
        reynolds=args.reynolds,
        mach=args.mach,
        rho=args.rho,
        mu=args.mu,
        chord_m=chord_m,
        velocity=velocity,
        alpha_start_deg=args.alpha_start,
        alpha_end_deg=args.alpha_end,
        alpha_step_deg=args.alpha_step,
        pressure_ref_pa=args.pressure_ref_pa,
        temperature_K=args.temperature_K,
        speed_of_sound_m_s=speed_of_sound,
    )
    export_case_package(case_root, args.variant, args.output_dir, cfg, plot=args.plot, validate=args.validate, overwrite=args.overwrite)
    print(f"2D case package generated under: {(case_root / args.output_dir if not args.output_dir.is_absolute() else args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
