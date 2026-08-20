#!/usr/bin/env python3
"""Robust 2D profile utilities for ram-air CFD preprocessing.

Internal convention after canonicalization:
    upper: LE_upper -> TE_upper
    lower: LE_lower -> TE_lower
    inlet: LE_upper -> LE_lower (metadata only for open profiles)
    TE closure: TE_upper -> TE_lower (wall)

The most important design decision in this module is that CSV files with
columns x,y,z are interpreted intelligently: when z is constant and y varies,
y is used as the 2D vertical coordinate. This matches the exported Ross/LS1
profiles where x-y are profile coordinates and z=0 is the CAD plane.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

OPEN_VARIANTS = {"open_ramair", "ross_standard_8p4", "ross_minimum_4p0", "standard", "optimized"}
CLOSED_VARIANTS = {"reference_uncut", "closed_reference"}


@dataclass
class CanonicalProfile2D:
    upper: pd.DataFrame
    lower: pd.DataFrame
    inlet_segment: tuple[dict, dict] | None
    te_segment: tuple[dict, dict] | None
    closed_contour: pd.DataFrame
    open_contour: pd.DataFrame
    has_inlet: bool
    is_closed: bool
    le_upper_point: dict
    le_lower_point: dict
    te_upper_point: dict
    te_lower_point: dict
    profile_order_detected: str
    warnings: list[str]
    errors: list[str]
    report: dict


def write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _finite_range(s: pd.Series) -> float:
    vals = pd.to_numeric(s, errors="coerce").to_numpy(float)
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return 0.0
    return float(np.max(vals) - np.min(vals))


def _pick_vertical_coordinate(df: pd.DataFrame) -> tuple[str, list[str]]:
    """Return the dataframe column to use as profile vertical coordinate.

    Common problem fixed here: CATIA-style/plotting CSVs often contain x,y,z,
    with the actual 2D airfoil coordinate in y and z=0 because the points lie
    in the CAD XY plane. A naive x,z reader collapses the profile to a flat
    line and makes the split impossible.
    """
    cols = {c.lower().strip(): c for c in df.columns}
    warnings: list[str] = []
    if "z_norm" in cols:
        return cols["z_norm"], warnings
    if "z_chord_norm" in cols:
        return cols["z_chord_norm"], warnings
    if "y_norm" in cols:
        return cols["y_norm"], warnings
    if "y_chord_norm" in cols:
        return cols["y_chord_norm"], warnings
    if "y" in cols and "z" in cols:
        yr = _finite_range(df[cols["y"]])
        zr = _finite_range(df[cols["z"]])
        if yr > max(1e-12, 20.0 * zr):
            warnings.append("CSV has x,y,z; z is nearly constant, so y was used as the 2D vertical coordinate.")
            return cols["y"], warnings
        if zr > max(1e-12, 20.0 * yr):
            return cols["z"], warnings
        # If both vary, prefer z for mathematical convention but warn.
        warnings.append("CSV has both y and z varying; z was used as vertical. Use x_norm,z_norm or section labels to remove ambiguity.")
        return cols["z"], warnings
    if "z" in cols:
        return cols["z"], warnings
    if "y" in cols:
        return cols["y"], warnings
    raise ValueError(f"Could not identify profile vertical coordinate in columns {list(df.columns)}")


def read_profile_table(path_or_dataframe: str | Path | pd.DataFrame) -> pd.DataFrame:
    """Read CSV/DAT/TXT and return x_norm,z_norm plus optional section/order.

    Supported coordinate conventions:
      - x_norm,z_norm
      - x_chord_norm,z_chord_norm
      - x,y,z where y is profile vertical and z is CAD-plane constant
      - x,z
      - x,y
      - two-column DAT/TXT with optional title/header
    """
    source_name = "dataframe"
    warnings: list[str] = []
    if isinstance(path_or_dataframe, pd.DataFrame):
        df = path_or_dataframe.copy()
    else:
        path = Path(path_or_dataframe)
        source_name = str(path)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path)
        else:
            pts = []
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip().replace(",", " ")
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except Exception:
                    # title/header line
                    continue
            if len(pts) < 3:
                raise ValueError(f"Could not read at least 3 coordinate rows from {path}")
            out = pd.DataFrame(pts, columns=["x_norm", "z_norm"])
            out.attrs["source_path"] = source_name
            out.attrs["vertical_coordinate_source"] = "dat_second_column"
            out.attrs["reader_warnings"] = []
            return out

    cols = {c.lower().strip(): c for c in df.columns}
    if "x_norm" in cols:
        xcol = cols["x_norm"]
    elif "x_chord_norm" in cols:
        xcol = cols["x_chord_norm"]
    elif "x" in cols:
        xcol = cols["x"]
    else:
        raise ValueError(f"Unsupported profile columns; no x coordinate found: {list(df.columns)}")
    zcol, w = _pick_vertical_coordinate(df)
    warnings.extend(w)
    out = pd.DataFrame({
        "x_norm": pd.to_numeric(df[xcol], errors="coerce"),
        "z_norm": pd.to_numeric(df[zcol], errors="coerce"),
    })
    if "section" in cols:
        out["section"] = df[cols["section"]].astype(str).str.upper().str.strip()
    elif "source_section" in cols:
        out["section"] = df[cols["source_section"]].astype(str).str.upper().str.strip()
    if "order" in cols:
        out["order"] = pd.to_numeric(df[cols["order"]], errors="coerce")
    elif "source_order" in cols:
        out["order"] = pd.to_numeric(df[cols["source_order"]], errors="coerce")
    out = out.dropna(subset=["x_norm", "z_norm"]).reset_index(drop=True)
    if len(out) < 3:
        raise ValueError("Profile contains fewer than 3 finite points")
    out.attrs["source_path"] = source_name
    out.attrs["vertical_coordinate_source"] = zcol
    out.attrs["reader_warnings"] = warnings
    return out


def normalize_profile(df: pd.DataFrame, tolerance: float = 1e-12) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    x = out["x_norm"].to_numpy(float)
    z = out["z_norm"].to_numpy(float)
    if not np.isfinite(x).all() or not np.isfinite(z).all():
        raise ValueError("Profile contains non-finite coordinates")
    chord = float(np.max(x) - np.min(x))
    if chord <= tolerance:
        raise ValueError("Profile chord is zero or negative")
    xmin = float(np.min(x))
    xmax = float(np.max(x))
    # Always normalize by measured chord. Tiny negative LE coordinates become zero.
    out["x_norm"] = (out["x_norm"] - xmin) / chord
    out["z_norm"] = out["z_norm"] / chord
    out.attrs.update(df.attrs)
    out.attrs["normalization_xmin"] = xmin
    out.attrs["normalization_xmax"] = xmax
    out.attrs["normalization_chord"] = chord
    return out


def _dedupe_consecutive(df: pd.DataFrame, tol: float = 1e-12) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    cols = [c for c in ["x_norm", "z_norm", "section", "order"] if c in df.columns]
    d = df[cols].copy().reset_index(drop=True)
    keep = [0]
    arr = d[["x_norm", "z_norm"]].to_numpy(float)
    for i in range(1, len(arr)):
        if np.linalg.norm(arr[i] - arr[keep[-1]]) > tol:
            keep.append(i)
    return d.iloc[keep].reset_index(drop=True)


def _orient_branch_le_to_te(branch: pd.DataFrame) -> pd.DataFrame:
    b = _dedupe_consecutive(branch)
    if len(b) < 2:
        return b
    first_x, last_x = float(b.iloc[0].x_norm), float(b.iloc[-1].x_norm)
    if first_x > last_x:
        b = b.iloc[::-1].reset_index(drop=True)
    return b


def _branch_monotonicity_penalty(branch: pd.DataFrame) -> float:
    if len(branch) < 3:
        return 0.0
    x = branch["x_norm"].to_numpy(float)
    dx = np.diff(x)
    # after LE->TE orientation, dx should not be strongly negative
    neg = dx[dx < -2e-4]
    return float(np.sum(np.abs(neg)))


def _score_upper_lower(upper: pd.DataFrame, lower: pd.DataFrame) -> tuple[float, dict]:
    try:
        uo = _orient_branch_le_to_te(upper)
        lo = _orient_branch_le_to_te(lower)
        u = uo.sort_values("x_norm")
        l = lo.sort_values("x_norm")
        x0 = max(float(u.x_norm.min()), float(l.x_norm.min()))
        x1 = min(float(u.x_norm.max()), float(l.x_norm.max()))
        if x1 <= x0:
            return -1e12, {"reason": "no_common_x"}
        xs = np.linspace(x0, x1, 300)
        uz = np.interp(xs, u.x_norm, u.z_norm)
        lz = np.interp(xs, l.x_norm, l.z_norm)
        th = uz - lz
        frac_pos = float(np.mean(th > -1e-7))
        mean_th = float(np.nanmean(th))
        max_th = float(np.nanmax(th))
        min_th = float(np.nanmin(th))
        le_x_ok = 1.0 - 0.5 * (abs(float(uo.iloc[0].x_norm)) + abs(float(lo.iloc[0].x_norm)))
        te_x_ok = 1.0 - 0.5 * (abs(float(uo.iloc[-1].x_norm) - 1.0) + abs(float(lo.iloc[-1].x_norm) - 1.0))
        monot_pen = _branch_monotonicity_penalty(uo) + _branch_monotonicity_penalty(lo)
        score = 1000.0 * frac_pos + 500.0 * max(0.0, mean_th) + 80.0 * max(0.0, max_th)
        score += 50.0 * le_x_ok + 50.0 * te_x_ok
        score -= 2000.0 * max(0.0, -min_th)
        score -= 100.0 * monot_pen
        return score, {
            "frac_positive_thickness": frac_pos,
            "mean_thickness_norm": mean_th,
            "max_thickness_norm": max_th,
            "min_thickness_norm": min_th,
            "monotonicity_penalty": monot_pen,
            "le_upper_x": float(uo.iloc[0].x_norm),
            "le_lower_x": float(lo.iloc[0].x_norm),
            "te_upper_x": float(uo.iloc[-1].x_norm),
            "te_lower_x": float(lo.iloc[-1].x_norm),
        }
    except Exception as exc:
        return -1e12, {"reason": str(exc)}


def _split_remove_duplicate(a: pd.DataFrame, b: pd.DataFrame, tol: float = 1e-10) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(a) and len(b):
        pa = a.iloc[-1][["x_norm", "z_norm"]].to_numpy(float)
        pb = b.iloc[0][["x_norm", "z_norm"]].to_numpy(float)
        if np.linalg.norm(pa - pb) < tol:
            b = b.iloc[1:].copy()
    return a.copy(), b.copy()


def _candidate_splits(d: pd.DataFrame, input_order: str) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    n = len(d)
    cands: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []

    def add(name: str, idx: int, include_idx_left: bool = True):
        if idx <= 0 or idx >= n - 1:
            return
        if include_idx_left:
            a = d.iloc[:idx+1].copy(); b = d.iloc[idx+1:].copy()
        else:
            a = d.iloc[:idx].copy(); b = d.iloc[idx:].copy()
        if len(a) >= 2 and len(b) >= 2:
            cands.append((name, a, b))

    if input_order == "upper_TE_to_LE__lower_LE_to_TE":
        add(input_order, int(d["x_norm"].idxmin()), True)
        return cands
    if input_order == "upper_LE_to_TE__lower_TE_to_LE":
        add(input_order, int(d["x_norm"].idxmax()), True)
        return cands
    if input_order == "upper_LE_to_TE__lower_LE_to_TE":
        # two branches concatenated, both start near LE and go to TE; split near middle at second LE if possible
        x = d["x_norm"].to_numpy(float)
        near_le = np.where(x < min(0.05, np.nanmin(x) + 0.08))[0]
        for idx in near_le:
            if 2 < idx < n - 2:
                add(input_order + f"_near_LE_{idx}", idx - 1, True)
        add(input_order + "_half", n // 2, False)
        return cands
    if input_order == "closed_airfoil_standard_dat":
        add(input_order, int(d["x_norm"].idxmin()), True)
        return cands

    # auto candidates
    arr = d[["x_norm", "z_norm"]].to_numpy(float)
    dist = np.sqrt(np.sum(np.diff(arr, axis=0)**2, axis=1))
    if len(dist):
        med = float(np.nanmedian(dist)) if np.nanmedian(dist) > 0 else float(np.nanmean(dist))
        # largest gap, often the LE inlet gap for open profiles
        for k in np.argsort(dist)[-5:][::-1]:
            k = int(k)
            near_le = max(float(d.iloc[k].x_norm), float(d.iloc[k+1].x_norm)) < 0.25
            large_gap = dist[k] > max(3.0 * med, 0.01)
            if near_le or large_gap:
                add(f"split_at_sequence_gap_{k}_d={dist[k]:.5g}", k, True)
    # standard DAT style: start TE upper -> LE -> lower -> TE
    add("split_at_min_x", int(d["x_norm"].idxmin()), True)
    # LE->TE upper then TE->LE lower
    # if first/last are near LE and middle max x is the TE break
    add("split_at_max_x", int(d["x_norm"].idxmax()), True)
    # possible second TE if first point is also TE, use farthest from ends
    maxx = float(d["x_norm"].max())
    max_candidates = [i for i, x in enumerate(d["x_norm"].to_numpy(float)) if abs(x - maxx) < 1e-4 and 2 < i < n - 3]
    for i in max_candidates:
        add(f"split_at_internal_max_x_{i}", i, True)
    add("split_half", n // 2, False)
    return cands


def split_profile_auto(df: pd.DataFrame, input_order: str = "auto") -> tuple[pd.DataFrame, pd.DataFrame, str, list[str], list[str], dict]:
    warnings: list[str] = []
    errors: list[str] = []
    d = normalize_profile(df).reset_index(drop=True)
    warnings.extend(d.attrs.get("reader_warnings", []))
    split_report: dict[str, Any] = {
        "vertical_coordinate_source": d.attrs.get("vertical_coordinate_source", None),
        "normalization_xmin": d.attrs.get("normalization_xmin", None),
        "normalization_xmax": d.attrs.get("normalization_xmax", None),
        "normalization_chord": d.attrs.get("normalization_chord", None),
        "candidates": [],
    }

    if input_order == "section_column" or (input_order == "auto" and "section" in d.columns and d.section.astype(str).str.upper().isin(["UPPER", "LOWER"]).any()):
        upper = d[d["section"].astype(str).str.upper().eq("UPPER")].copy()
        lower = d[d["section"].astype(str).str.upper().eq("LOWER")].copy()
        if "order" in d.columns:
            upper = upper.sort_values("order")
            lower = lower.sort_values("order")
        if len(upper) < 2 or len(lower) < 2:
            errors.append("section_column present but UPPER/LOWER branches are incomplete")
        upper = _orient_branch_le_to_te(upper)
        lower = _orient_branch_le_to_te(lower)
        score, rep = _score_upper_lower(upper, lower)
        score_sw, rep_sw = _score_upper_lower(lower, upper)
        if score_sw > score:
            upper, lower, rep, score = lower, upper, rep_sw, score_sw
            warnings.append("UPPER/LOWER labels appeared swapped; corrected by thickness check")
        split_report["candidates"].append({"name": "section_column", "score": score, **rep})
        return upper, lower, "section_column", warnings, errors, split_report

    best = None
    for name, a, b in _candidate_splits(d, input_order):
        if len(a) < 2 or len(b) < 2:
            continue
        au, bl = _orient_branch_le_to_te(a), _orient_branch_le_to_te(b)
        score1, rep1 = _score_upper_lower(au, bl)
        score2, rep2 = _score_upper_lower(bl, au)
        if score2 > score1:
            score, upper, lower, rep = score2, bl, au, rep2
            det = name + "__swapped_by_thickness"
        else:
            score, upper, lower, rep = score1, au, bl, rep1
            det = name
        split_report["candidates"].append({"name": det, "score": score, **rep, "n_a": len(a), "n_b": len(b)})
        if best is None or score > best[0]:
            best = (score, upper, lower, det, rep)
    if best is None:
        errors.append("Could not split profile into two branches")
        return pd.DataFrame(), pd.DataFrame(), "failed", warnings, errors, split_report
    score, upper, lower, detected, rep = best
    split_report["selected_candidate"] = detected
    split_report["selected_score"] = score
    split_report["selected_metrics"] = rep
    if rep.get("frac_positive_thickness", 0) < 0.95:
        warnings.append("Less than 95% positive thickness after split; inspect profile_canonical_upper_lower.png")
    if rep.get("max_thickness_norm", 0) <= 1e-5:
        errors.append("Detected profile thickness is near zero. This often means x,y,z CSV was read with z=0 instead of y.")
    return _dedupe_consecutive(upper), _dedupe_consecutive(lower), detected, warnings, errors, split_report


def build_te_closure(upper: pd.DataFrame, lower: pd.DataFrame, mode: str = "rounded", n_points: int = 12) -> pd.DataFrame:
    mode = str(mode).lower().replace("sharp_extension", "sharp").replace("straight_gap", "straight")
    u_te = upper.iloc[-1][["x_norm", "z_norm"]].to_numpy(float)
    l_te = lower.iloc[-1][["x_norm", "z_norm"]].to_numpy(float)
    gap = float(np.linalg.norm(u_te - l_te))
    if gap < 1e-12:
        if mode == "sharp":
            p = u_te.copy(); p[1] += 1e-8
            return pd.DataFrame([p], columns=["x_norm", "z_norm"])
        return pd.DataFrame(columns=["x_norm", "z_norm"])
    if mode == "straight":
        return pd.DataFrame(columns=["x_norm", "z_norm"])
    if mode == "sharp":
        p = 0.5 * (u_te + l_te)
        p[0] = max(u_te[0], l_te[0])
        eps = max(1e-8, 1e-5 * gap)
        return pd.DataFrame([(p[0], p[1]+eps), (p[0], p[1]-eps)], columns=["x_norm", "z_norm"])
    # rounded: aft-bulging semicircle from TE_upper to TE_lower.
    center = 0.5 * (u_te + l_te)
    v = (l_te - u_te) / gap
    n = np.array([v[1], -v[0]])
    if n[0] < 0:
        n = -n
    radius = 0.5 * gap
    n_points = max(5, int(n_points))
    pts = []
    for k in range(1, n_points - 1):
        a = -math.pi/2 + math.pi * k/(n_points - 1)
        p = center + radius * math.sin(a) * v + radius * math.cos(a) * n
        pts.append((float(p[0]), float(p[1])))
    return pd.DataFrame(pts, columns=["x_norm", "z_norm"])


def read_and_canonicalize_profile_2d(path_or_dataframe: str | Path | pd.DataFrame,
                                     profile_kind: str,
                                     input_order: str = "auto",
                                     has_inlet: str | bool = "auto",
                                     te_closure_mode: str = "rounded",
                                     chord_m: float = 1.0,
                                     expected_upper: str = "positive_z",
                                     expected_lower: str = "negative_z",
                                     tolerance: float = 1e-9) -> CanonicalProfile2D:
    raw = read_profile_table(path_or_dataframe)
    try:
        upper, lower, detected, warnings, errors, split_rep = split_profile_auto(raw, input_order=input_order)
    except Exception as exc:
        empty = pd.DataFrame(columns=["x_norm", "z_norm"])
        return CanonicalProfile2D(empty, empty, None, None, empty, empty, False, False, {}, {}, {}, {}, "failed", [], [str(exc)], {"errors": [str(exc)]})
    if errors:
        empty = pd.DataFrame(columns=["x_norm", "z_norm"])
        return CanonicalProfile2D(empty, empty, None, None, empty, empty, False, False, {}, {}, {}, {}, detected, warnings, errors, {"errors": errors, **split_rep})

    score, rep = _score_upper_lower(upper, lower)
    score_sw, _ = _score_upper_lower(lower, upper)
    if score_sw > score:
        upper, lower = lower, upper
        warnings.append("Branches swapped after final thickness check")
    upper = _orient_branch_le_to_te(upper)
    lower = _orient_branch_le_to_te(lower)

    le_u = upper.iloc[0][["x_norm", "z_norm"]].to_dict()
    le_l = lower.iloc[0][["x_norm", "z_norm"]].to_dict()
    te_u = upper.iloc[-1][["x_norm", "z_norm"]].to_dict()
    te_l = lower.iloc[-1][["x_norm", "z_norm"]].to_dict()
    le_gap = float(np.linalg.norm(upper.iloc[0][["x_norm", "z_norm"]].to_numpy(float) - lower.iloc[0][["x_norm", "z_norm"]].to_numpy(float)))
    te_gap = float(np.linalg.norm(upper.iloc[-1][["x_norm", "z_norm"]].to_numpy(float) - lower.iloc[-1][["x_norm", "z_norm"]].to_numpy(float)))

    if has_inlet == "auto":
        has = (profile_kind in OPEN_VARIANTS) or le_gap > max(tolerance, 5e-4)
        if profile_kind in CLOSED_VARIANTS:
            has = False
    else:
        has = bool(has_inlet)
    if has and le_gap <= tolerance:
        warnings.append("Profile requested as open but LE gap is near zero")
    if has and (upper.iloc[0].x_norm > 0.25 or lower.iloc[0].x_norm > 0.25):
        errors.append("Inlet appears far from leading edge; profile may be reversed or wrongly split")

    te_arc = build_te_closure(upper, lower, te_closure_mode, n_points=16)
    pieces = [upper[["x_norm", "z_norm"]].copy()]
    if not te_arc.empty:
        pieces.append(te_arc)
    pieces.append(lower[["x_norm", "z_norm"]].iloc[::-1].reset_index(drop=True))
    closed_contour = pd.concat(pieces, ignore_index=True)
    open_contour = pd.concat([upper[["x_norm", "z_norm"]].copy(), lower[["x_norm", "z_norm"]].copy()], ignore_index=True)

    u = upper.sort_values("x_norm")
    l = lower.sort_values("x_norm")
    x0 = max(float(u.x_norm.min()), float(l.x_norm.min()))
    x1 = min(float(u.x_norm.max()), float(l.x_norm.max()))
    xs = np.linspace(x0, x1, 400)
    th = np.interp(xs, u.x_norm, u.z_norm) - np.interp(xs, l.x_norm, l.z_norm)
    imax = int(np.nanargmax(th)) if len(th) else 0
    if float(np.nanmax(th)) <= 1e-5:
        errors.append("Canonical profile has near-zero thickness after split; inspect vertical coordinate mapping and raw CSV columns.")

    report = {
        "profile_kind": profile_kind,
        "input_order_requested": input_order,
        "input_order_detected": detected,
        "canonical_order": "upper_LE_to_TE__lower_LE_to_TE",
        "coordinate_vertical_source": split_rep.get("vertical_coordinate_source"),
        "has_inlet": bool(has),
        "is_closed": not bool(has),
        "le_gap_norm": le_gap,
        "te_gap_norm": te_gap,
        "max_thickness_norm": float(np.nanmax(th)) if len(th) else None,
        "max_thickness_x_norm": float(xs[imax]) if len(th) else None,
        "min_thickness_norm": float(np.nanmin(th)) if len(th) else None,
        "positive_thickness_fraction": float(np.mean(th > -1e-7)) if len(th) else None,
        "number_of_upper_points": int(len(upper)),
        "number_of_lower_points": int(len(lower)),
        "le_upper_point": le_u,
        "le_lower_point": le_l,
        "te_upper_point": te_u,
        "te_lower_point": te_l,
        "warnings": warnings,
        "errors": errors,
        "order_detection": split_rep,
    }
    return CanonicalProfile2D(upper, lower, (le_u, le_l) if has else None, (te_u, te_l), closed_contour, open_contour, bool(has), not bool(has), le_u, le_l, te_u, te_l, detected, warnings, errors, report)


def build_profile_points_edges(cp: CanonicalProfile2D, variant: str, chord_m: float = 1.0, te_closure_mode: str = "rounded", closed_le: bool | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    if cp.errors:
        raise ValueError("Cannot build points/edges from invalid canonical profile: " + "; ".join(cp.errors))
    if closed_le is None:
        closed_le = not cp.has_inlet
    rows = []
    pid = 1
    upper_ids: list[int] = []
    lower_ids: list[int] = []
    te_arc_ids: list[int] = []
    for i, r in cp.upper.reset_index(drop=True).iterrows():
        role = "le_upper" if i == 0 else ("te_upper" if i == len(cp.upper)-1 else "upper_wall")
        rows.append({"point_id": pid, "x_norm": float(r.x_norm), "z_norm": float(r.z_norm), "x_m": float(r.x_norm)*chord_m, "z_m": float(r.z_norm)*chord_m, "source_section": "UPPER", "source_order": i+1, "variant": variant, "boundary_role": role, "notes": "canonical upper LE->TE"})
        upper_ids.append(pid); pid += 1
    for i, r in cp.lower.reset_index(drop=True).iterrows():
        role = "le_lower" if i == 0 else ("te_lower" if i == len(cp.lower)-1 else "lower_wall")
        rows.append({"point_id": pid, "x_norm": float(r.x_norm), "z_norm": float(r.z_norm), "x_m": float(r.x_norm)*chord_m, "z_m": float(r.z_norm)*chord_m, "source_section": "LOWER", "source_order": i+1, "variant": variant, "boundary_role": role, "notes": "canonical lower LE->TE"})
        lower_ids.append(pid); pid += 1
    te_arc = build_te_closure(cp.upper, cp.lower, te_closure_mode, 16)
    for i, r in te_arc.iterrows():
        rows.append({"point_id": pid, "x_norm": float(r.x_norm), "z_norm": float(r.z_norm), "x_m": float(r.x_norm)*chord_m, "z_m": float(r.z_norm)*chord_m, "source_section": "TE_ARC", "source_order": i+1, "variant": variant, "boundary_role": "te_closure", "notes": "explicit TE closure geometry"})
        te_arc_ids.append(pid); pid += 1
    points = pd.DataFrame(rows)
    edge_rows = []
    eid = 1

    def add(a: int, b: int, patch: str, group: str, etype: str = "polyline_segment", wall: bool = True, physical: bool = True, opening: bool = False, synthetic: bool = False, outer: bool = False, inner: bool = False) -> None:
        nonlocal eid
        edge_rows.append({
            "edge_id": eid,
            "start_point_id": int(a),
            "end_point_id": int(b),
            "patch_name": patch,
            "curve_group": group,
            "edge_type": etype,
            "is_wall": bool(wall),
            "is_inlet": False,
            "is_outlet": False,
            "is_synthetic": bool(synthetic),
            "is_physical_boundary": bool(physical),
            "is_farfield": False,
            "is_opening_between_exterior_and_cavity": bool(opening),
            "is_opening_marker": bool(opening),
            "is_inlet_marker": bool(opening),
            "is_te_closure": group == "trailing_edge_closure",
            "is_le_closure": group == "leading_edge_closure",
            "belongs_to_outer_surface": bool(outer),
            "belongs_to_inner_surface": bool(inner),
            "recommended_openfoam_patch": "none_metadata_only" if opening else "wall",
            "recommended_bc_openfoam": "none_metadata_only" if opening else "wall",
            "recommended_bc_su2": "FEATURE_OPENING" if opening else "MARKER_HEATFLUX",
            "notes": patch,
        })
        eid += 1

    up_patch = "outer_upper_wall" if cp.has_inlet else "airfoil_wall"
    lo_patch = "outer_lower_wall" if cp.has_inlet else "airfoil_wall"
    for a, b in zip(upper_ids[:-1], upper_ids[1:]):
        add(a, b, up_patch, "upper", outer=True)
    if cp.has_inlet and not closed_le:
        add(upper_ids[0], lower_ids[0], "inlet_opening_marker", "leading_edge_opening", "opening_marker", wall=False, physical=False, opening=True, synthetic=True)
    else:
        add(lower_ids[0], upper_ids[0], "airfoil_wall" if not cp.has_inlet else "leading_edge_closure_wall", "leading_edge_closure", "synthetic_le_closure", wall=True, physical=True, synthetic=True)
    for a, b in zip(lower_ids[:-1], lower_ids[1:]):
        add(a, b, lo_patch, "lower", outer=True)
    te_patch = "trailing_edge_wall" if cp.has_inlet else "airfoil_wall"
    if te_arc_ids:
        ids = [upper_ids[-1]] + te_arc_ids + [lower_ids[-1]]
        for a, b in zip(ids[:-1], ids[1:]):
            add(a, b, te_patch, "trailing_edge_closure", "rounded_te_arc_segment", wall=True, physical=True, synthetic=True)
    else:
        add(upper_ids[-1], lower_ids[-1], te_patch, "trailing_edge_closure", "straight_te_closure", wall=True, physical=True, synthetic=True)
    edges = pd.DataFrame(edge_rows)
    if cp.has_inlet:
        patches = {
            "outer_upper_wall": {"type": "wall"},
            "outer_lower_wall": {"type": "wall"},
            "trailing_edge_wall": {"type": "wall"},
            "inlet_opening_marker": {"type": "feature/opening_marker", "is_physical_boundary": False, "is_opening_between_exterior_and_cavity": True, "forbidden_physical_patch": "ram_air_inlet"},
        }
    else:
        patches = {"airfoil_wall": {"type": "wall"}, "farfield": {"type": "farfield"}}
    manifest = {
        "variant": variant,
        "axis_convention": "x_chord_positive_TE_z_positive_up",
        "length_unit": "m",
        "chord_m": chord_m,
        "te_closure_mode": te_closure_mode,
        "has_ram_air_opening_feature": bool(cp.has_inlet and not closed_le),
        "ram_air_inlet_is_physical_openfoam_patch": False,
        "canonical_order": "upper_LE_to_TE__lower_LE_to_TE",
        "input_order_detected": cp.profile_order_detected,
        "coordinate_vertical_source": cp.report.get("coordinate_vertical_source"),
        "le_gap_norm": cp.report.get("le_gap_norm"),
        "te_gap_norm": cp.report.get("te_gap_norm"),
        "max_thickness_norm": cp.report.get("max_thickness_norm"),
        "positive_thickness_fraction": cp.report.get("positive_thickness_fraction"),
        "warnings": cp.warnings,
        "errors": cp.errors,
    }
    return points, edges, patches, manifest


def write_variant_outputs(root: Path, variant: str, points: pd.DataFrame, edges: pd.DataFrame, patches: dict, manifest: dict) -> None:
    out = Path(root) / "geometry" / variant
    out.mkdir(parents=True, exist_ok=True)
    points.to_csv(out / "profile_points.csv", index=False, float_format="%.10f")
    edges.to_csv(out / "profile_edges.csv", index=False)
    write_json(out / "profile_patches.json", patches)
    write_json(out / "profile_manifest.json", manifest)
    lookup = points.set_index("point_id")
    coords = []
    for _, e in edges.iterrows():
        if str(e.patch_name) in {"inlet_opening_marker", "ram_air_inlet"}:
            continue
        if int(e.start_point_id) in lookup.index:
            p = lookup.loc[int(e.start_point_id)]
            coords.append((float(p.x_norm), float(p.z_norm)))
    (out / f"profile_{variant}.dat").write_text(variant + "\n" + "\n".join(f"{x:.10f} {z:.10f}" for x, z in coords) + "\n", encoding="utf-8")
    _write_simple_dxf(out / f"profile_{variant}.dxf", points, edges)
    plot_profile_diagnostics(points, edges, out, variant)


def _write_simple_dxf(path: Path, points: pd.DataFrame, edges: pd.DataFrame) -> None:
    lookup = points.set_index("point_id")
    lines = ["0", "SECTION", "2", "ENTITIES"]
    for _, e in edges.iterrows():
        if int(e.start_point_id) not in lookup.index or int(e.end_point_id) not in lookup.index:
            continue
        p1 = lookup.loc[int(e.start_point_id)]
        p2 = lookup.loc[int(e.end_point_id)]
        layer = str(e.patch_name)[:31]
        lines += ["0", "LINE", "8", layer, "10", f"{float(p1.x_norm):.10f}", "20", f"{float(p1.z_norm):.10f}", "30", "0", "11", f"{float(p2.x_norm):.10f}", "21", f"{float(p2.z_norm):.10f}", "31", "0"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_profile_diagnostics(points: pd.DataFrame, edges: pd.DataFrame, out_dir: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lookup = points.set_index("point_id")

    def draw(ax, zoom: str | None = None) -> None:
        for _, e in edges.iterrows():
            if int(e.start_point_id) not in lookup.index or int(e.end_point_id) not in lookup.index:
                continue
            p1 = lookup.loc[int(e.start_point_id)]
            p2 = lookup.loc[int(e.end_point_id)]
            patch = str(e.patch_name)
            if patch in {"inlet_opening_marker", "ram_air_inlet"}:
                color, lw, zord = "tab:red", 3.0, 6
            elif bool(e.get("is_te_closure", False)):
                color, lw, zord = "tab:purple", 2.6, 5
            elif "upper" in patch:
                color, lw, zord = "tab:blue", 1.8, 3
            elif "lower" in patch:
                color, lw, zord = "tab:orange", 1.8, 3
            else:
                color, lw, zord = "k", 1.2, 2
            ax.plot([p1.x_norm, p2.x_norm], [p1.z_norm, p2.z_norm], color=color, marker=".", lw=lw, ms=3, zorder=zord)
        # special points
        pts = points.copy()
        for role, label, color in [
            ("le_upper", "LE upper", "tab:blue"), ("le_lower", "LE lower", "tab:orange"),
            ("te_upper", "TE upper", "tab:blue"), ("te_lower", "TE lower", "tab:orange"),
        ]:
            sub = pts[pts.boundary_role == role]
            if not sub.empty:
                x = float(sub.iloc[0].x_norm); z = float(sub.iloc[0].z_norm)
                ax.scatter([x], [z], s=45, color=color, edgecolors="k", zorder=10)
                ax.text(x, z, "  " + label, fontsize=7, zorder=10)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, linewidth=0.3)
        ax.set_xlabel("x/c")
        ax.set_ylabel("z/c")
        ax.set_title(title)
        if zoom == "LE":
            x0 = float(points.x_norm.min())
            zr = float(points.z_norm.max() - points.z_norm.min()) or 0.1
            ax.set_xlim(x0 - 0.03, x0 + 0.18)
            zc = float(points.loc[points.x_norm.idxmin(), "z_norm"])
            ax.set_ylim(zc - 0.25 * zr - 0.04, zc + 0.25 * zr + 0.04)
        elif zoom == "TE":
            x1 = float(points.x_norm.max())
            ax.set_xlim(x1 - 0.18, x1 + 0.05)
        else:
            xmin, xmax = float(points.x_norm.min()), float(points.x_norm.max())
            zmin, zmax = float(points.z_norm.min()), float(points.z_norm.max())
            ax.set_xlim(xmin - 0.05, xmax + 0.05)
            ax.set_ylim(zmin - 0.08, zmax + 0.08)

    files = [
        ("profile_preview.png", None),
        ("profile_raw_points.png", None),
        ("profile_canonical_upper_lower.png", None),
        ("profile_open_with_inlet.png", "LE"),
        ("profile_inlet_detail.png", "LE"),
        ("profile_closed_reference.png", None),
        ("profile_te_closure_detail.png", "TE"),
        ("profile_te_detail.png", "TE"),
    ]
    for name, zoom in files:
        fig, ax = plt.subplots(figsize=(9, 4))
        draw(ax, zoom)
        ax.set_title(f"{title} - {name}")
        fig.tight_layout()
        fig.savefig(out_dir / name, dpi=180)
        plt.close(fig)


def plot_all_variants_comparison(root: Path, variants: list[str]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    root = Path(root)
    fig, ax = plt.subplots(figsize=(10, 5))
    plotted = 0
    for variant in variants:
        pts_path = root / "geometry" / variant / "profile_points.csv"
        edges_path = root / "geometry" / variant / "profile_edges.csv"
        if not pts_path.exists() or not edges_path.exists():
            continue
        pts = pd.read_csv(pts_path)
        edges = pd.read_csv(edges_path)
        lookup = pts.set_index("point_id")
        for _, e in edges.iterrows():
            if str(e.patch_name) in {"inlet_opening_marker", "ram_air_inlet"}:
                continue
            if int(e.start_point_id) in lookup.index and int(e.end_point_id) in lookup.index:
                p1 = lookup.loc[int(e.start_point_id)]; p2 = lookup.loc[int(e.end_point_id)]
                ax.plot([p1.x_norm, p2.x_norm], [p1.z_norm, p2.z_norm], lw=1.2, label=variant if plotted == 0 else None)
        plotted += 1
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linewidth=0.3)
    ax.set_xlabel("x/c")
    ax.set_ylabel("z/c")
    ax.set_title("Profile variants comparison")
    handles, labels = ax.get_legend_handles_labels()
    # unique labels
    seen = set(); uh=[]; ul=[]
    for h, l in zip(handles, labels):
        if l not in seen:
            uh.append(h); ul.append(l); seen.add(l)
    if uh:
        ax.legend(uh, ul, fontsize=8)
    out = root / "previews"
    out.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out / "profile_all_variants_comparison.png", dpi=180)
    plt.close(fig)
