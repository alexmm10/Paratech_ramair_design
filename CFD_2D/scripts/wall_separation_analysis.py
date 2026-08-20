#!/usr/bin/env python3
"""Connectivity-based wall-shear and boundary-layer separation analysis.

The detector deliberately keeps raw OpenFOAM data separate from the filtered
signal used for event detection.  It never treats an x-sorted cloud as a wall
branch: face-edge connectivity defines arc length and branch orientation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ramair_scientific_plot_style import save_scientific_figure


METHOD_VERSION = "ramair-wall-separation-connectivity-v1"
VECTOR_COLUMNS = (
    "wallShearStress_x",
    "wallShearStress_y",
    "wallShearStress_z",
)
EDGE_COLUMNS = ("edge_x0_m", "edge_y0_m", "edge_x1_m", "edge_y1_m")


def _node_key(x: float, y: float, tolerance: float) -> tuple[int, int]:
    return (int(round(float(x) / tolerance)), int(round(float(y) / tolerance)))


def _ordered_components(frame: pd.DataFrame, chord_m: float) -> list[pd.DataFrame]:
    """Return face chains/loops ordered by shared projected wall edges."""
    if frame.empty or any(column not in frame for column in EDGE_COLUMNS):
        return []
    tolerance = max(abs(float(chord_m)) * 1.0e-9, 1.0e-12)
    edge_nodes: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}
    node_edges: dict[tuple[int, int], list[int]] = {}
    for position, (_, row) in enumerate(frame.reset_index(drop=True).iterrows()):
        values = [float(row[column]) for column in EDGE_COLUMNS]
        if not np.all(np.isfinite(values)):
            continue
        nodes = (
            _node_key(values[0], values[1], tolerance),
            _node_key(values[2], values[3], tolerance),
        )
        if nodes[0] == nodes[1]:
            continue
        edge_nodes[position] = nodes
        node_edges.setdefault(nodes[0], []).append(position)
        node_edges.setdefault(nodes[1], []).append(position)
    if not edge_nodes:
        return []

    remaining = set(edge_nodes)
    components: list[pd.DataFrame] = []
    reset = frame.reset_index(drop=True)
    while remaining:
        seed = next(iter(remaining))
        stack = [seed]
        component_edges: set[int] = set()
        while stack:
            edge = stack.pop()
            if edge in component_edges:
                continue
            component_edges.add(edge)
            for node in edge_nodes[edge]:
                stack.extend(candidate for candidate in node_edges[node] if candidate not in component_edges)
        remaining.difference_update(component_edges)
        degrees: dict[tuple[int, int], int] = {}
        for edge in component_edges:
            for node in edge_nodes[edge]:
                degrees[node] = degrees.get(node, 0) + 1
        endpoints = [node for node, degree in degrees.items() if degree == 1]
        start_node = endpoints[0] if endpoints else edge_nodes[min(component_edges)][0]
        ordered: list[int] = []
        used: set[int] = set()
        current_node = start_node
        while len(used) < len(component_edges):
            candidates = [
                edge for edge in node_edges.get(current_node, [])
                if edge in component_edges and edge not in used
            ]
            if not candidates:
                # Non-manifold data are split deterministically instead of
                # inventing connectivity.
                candidates = [edge for edge in sorted(component_edges) if edge not in used]
                if not candidates:
                    break
                current_node = edge_nodes[candidates[0]][0]
            edge = min(candidates)
            used.add(edge)
            ordered.append(edge)
            first, second = edge_nodes[edge]
            current_node = second if current_node == first else first
        component = reset.iloc[ordered].copy().reset_index(drop=True)
        component["connectivity_closed"] = not bool(endpoints)
        components.append(component)
    return components


def _path_between(values: pd.DataFrame, start: int, stop: int) -> pd.DataFrame:
    if start <= stop:
        return values.iloc[start:stop + 1].copy()
    return pd.concat([values.iloc[start:], values.iloc[:stop + 1]], ignore_index=True)


def _orient_le_to_te(branch: pd.DataFrame) -> pd.DataFrame:
    branch = branch.reset_index(drop=True)
    if len(branch) > 1 and float(branch.iloc[0]["x_m"]) > float(branch.iloc[-1]["x_m"]):
        branch = branch.iloc[::-1].reset_index(drop=True)
    return branch


def split_connected_wall_branches(data: pd.DataFrame, chord_m: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Split connected wall patches into named LE/lip-to-TE branches."""
    branches: list[pd.DataFrame] = []
    diagnostics: dict[str, Any] = {"method": "face_edge_connectivity", "patches": {}}
    for patch, patch_frame in data.groupby("patch", sort=True):
        components = _ordered_components(patch_frame, chord_m)
        diagnostics["patches"][str(patch)] = {
            "faces": int(len(patch_frame)),
            "components": int(len(components)),
        }
        patch_lower = str(patch).lower()
        side = (
            "internal" if "internal" in patch_lower or "inner" in patch_lower
            else "external"
        )
        for component_id, component in enumerate(components):
            if len(component) < 3:
                continue
            closed = bool(component["connectivity_closed"].iloc[0])
            x_values = component["x_m"].to_numpy(float)
            te_index = int(np.argmax(x_values))
            if closed:
                le_index = int(np.argmin(x_values))
                paths = (
                    _path_between(component, le_index, te_index),
                    _path_between(component, te_index, le_index).iloc[::-1].reset_index(drop=True),
                )
            else:
                paths = (
                    component.iloc[:te_index + 1].copy(),
                    component.iloc[te_index:].iloc[::-1].reset_index(drop=True),
                )
            for path in paths:
                path = _orient_le_to_te(path)
                if len(path) < 3:
                    continue
                surface = "upper" if float(path["y_m"].median()) >= float(component["y_m"].median()) else "lower"
                branch_id = f"{surface}_{side}"
                path["branch_id"] = branch_id
                path["component_id"] = component_id
                path["surface"] = surface
                path["wall_side"] = side
                ds = np.hypot(path["x_m"].diff(), path["y_m"].diff()).fillna(0.0)
                path["s_m"] = ds.cumsum()
                path["s_over_c"] = path["s_m"] / max(float(chord_m), 1.0e-30)
                branches.append(path)
    if not branches:
        diagnostics["status"] = "UNRESOLVED"
        diagnostics["reason"] = "wall face connectivity was unavailable or non-manifold"
        return pd.DataFrame(), diagnostics
    result = pd.concat(branches, ignore_index=True)
    diagnostics["status"] = "RESOLVED"
    diagnostics["branches"] = sorted(result["branch_id"].unique().tolist())
    return result, diagnostics


def _arc_median_filter(values: np.ndarray, s_over_c: np.ndarray, window_over_c: float) -> tuple[np.ndarray, int]:
    if len(values) < 3:
        return values.copy(), 1
    spacing = np.diff(s_over_c)
    spacing = spacing[np.isfinite(spacing) & (spacing > 0.0)]
    median_spacing = float(np.median(spacing)) if spacing.size else float(window_over_c)
    points = max(3, int(math.ceil(float(window_over_c) / max(median_spacing, 1.0e-12))))
    if points % 2 == 0:
        points += 1
    points = min(points, len(values) if len(values) % 2 == 1 else max(1, len(values) - 1))
    filtered = pd.Series(values).rolling(points, center=True, min_periods=1).median().to_numpy(float)
    return filtered, points


def _persistent_runs(labels: list[str], s: np.ndarray, minimum_faces: int, minimum_arc: float) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(labels) + 1):
        if index < len(labels) and labels[index] == labels[start]:
            continue
        length = float(s[index - 1] - s[start]) if index - start > 1 else 0.0
        count = index - start
        runs.append({
            "label": labels[start],
            "start": start,
            "stop": index - 1,
            "count": count,
            "arc_length_over_c": length,
            "persistent": count >= minimum_faces or length >= minimum_arc,
        })
        start = index
    return runs


def analyze_branch_separation(
    branch: pd.DataFrame,
    *,
    velocity_m_s: float,
    filter_window_over_c: float = 0.003,
    tau_relative_fraction: float = 0.005,
    tau_absolute_floor: float = 1.0e-12,
    minimum_faces: int = 3,
    minimum_arc_over_c: float = 0.001,
    exclusion_arc_over_c: float = 0.003,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Project shear on an oriented branch and detect persistent sign changes."""
    ordered = branch.sort_values("s_over_c").reset_index(drop=True).copy()
    if len(ordered) < max(3, minimum_faces) or any(column not in ordered for column in VECTOR_COLUMNS):
        return ordered, {
            "branch_id": str(ordered.get("branch_id", pd.Series(["unknown"])).iloc[0]),
            "status": "UNRESOLVED",
            "confidence": "UNRESOLVED",
            "events": [],
            "reason": "insufficient connected faces or wallShearStress vector components",
        }
    xy = ordered[["x_m", "y_m"]].to_numpy(float)
    tangent = np.zeros_like(xy)
    tangent[1:-1] = xy[2:] - xy[:-2]
    tangent[0] = xy[1] - xy[0]
    tangent[-1] = xy[-1] - xy[-2]
    norms = np.linalg.norm(tangent, axis=1)
    if np.any(norms <= 1.0e-20):
        return ordered, {
            "branch_id": str(ordered["branch_id"].iloc[0]),
            "status": "UNRESOLVED",
            "confidence": "UNRESOLVED",
            "events": [],
            "reason": "degenerate wall tangent",
        }
    tangent /= norms[:, None]
    shear = ordered[list(VECTOR_COLUMNS)].to_numpy(float)
    tau_raw = np.einsum("ij,ij->i", shear[:, :2], tangent)
    s = ordered["s_over_c"].to_numpy(float)
    attached_reference_mask = (s >= max(exclusion_arc_over_c, 0.02)) & (s <= 0.35)
    reference_values = tau_raw[attached_reference_mask]
    reference_values = reference_values[np.isfinite(reference_values)]
    if not reference_values.size:
        reference_values = tau_raw[np.isfinite(tau_raw)]
    orientation_reference = float(np.median(reference_values)) if reference_values.size else 0.0
    orientation_factor = 1.0 if orientation_reference >= 0.0 else -1.0
    tau_oriented = orientation_factor * tau_raw
    tau_filtered, filter_points = _arc_median_filter(tau_oriented, s, filter_window_over_c)
    attached_tau_reference = float(np.median(np.abs(orientation_factor * reference_values))) if reference_values.size else 0.0
    tau_eps = max(float(tau_absolute_floor), float(tau_relative_fraction) * attached_tau_reference)
    labels = np.where(
        tau_filtered > tau_eps,
        "attached",
        np.where(tau_filtered < -tau_eps, "reverse", "neutral"),
    ).tolist()
    ordered["tangent_x"] = tangent[:, 0]
    ordered["tangent_y"] = tangent[:, 1]
    ordered["tau_t_raw"] = tau_raw
    ordered["orientation_factor"] = orientation_factor
    ordered["tau_t_oriented"] = tau_oriented
    ordered["tau_t_filtered"] = tau_filtered
    ordered["Cf_raw"] = 2.0 * tau_oriented / max(float(velocity_m_s) ** 2, 1.0e-30)
    ordered["Cf_filtered"] = 2.0 * tau_filtered / max(float(velocity_m_s) ** 2, 1.0e-30)
    ordered["flow_state"] = labels

    runs = _persistent_runs(labels, s, minimum_faces, minimum_arc_over_c)
    persistent = [run for run in runs if run["persistent"] and run["label"] != "neutral"]
    events: list[dict[str, Any]] = []
    total_s = float(s[-1]) if len(s) else 0.0
    for previous, current in zip(persistent, persistent[1:]):
        transition = (str(previous["label"]), str(current["label"]))
        event_type = {
            ("attached", "reverse"): "separation",
            ("reverse", "attached"): "reattachment",
        }.get(transition)
        if event_type is None:
            continue
        left = int(previous["stop"])
        right = int(current["start"])
        if right <= left:
            right = min(left + 1, len(ordered) - 1)
        tau0, tau1 = float(tau_filtered[left]), float(tau_filtered[right])
        fraction = 0.5 if abs(tau1 - tau0) < 1.0e-30 else float(np.clip(-tau0 / (tau1 - tau0), 0.0, 1.0))
        s0 = float(s[left] + fraction * (s[right] - s[left]))
        excluded = s0 <= exclusion_arc_over_c or (total_s - s0) <= exclusion_arc_over_c
        events.append({
            "type": event_type,
            "s_over_c": s0,
            "x_over_c": float(ordered.iloc[left]["x_over_c"] + fraction * (ordered.iloc[right]["x_over_c"] - ordered.iloc[left]["x_over_c"])),
            "y_over_c": float((ordered.iloc[left]["y_m"] + fraction * (ordered.iloc[right]["y_m"] - ordered.iloc[left]["y_m"])) / max(float(ordered["x_m"].max() - ordered["x_m"].min()), 1.0e-30)),
            "confidence": "MEDIUM" if not excluded else "LOW",
            "excluded_from_primary": bool(excluded),
            "exclusion_reason": "LE/TE/lip arc exclusion" if excluded else None,
        })
    physical_events = [event for event in events if not event["excluded_from_primary"]]
    if physical_events:
        status, confidence = "DETECTED", "MEDIUM"
    elif np.any(np.abs(tau_filtered) <= tau_eps):
        status, confidence = "LOW_NEAR_ZERO_ONLY", "LOW"
    else:
        status, confidence = "NOT_DETECTED", "NOT_DETECTED"
    result = {
        "branch_id": str(ordered["branch_id"].iloc[0]),
        "status": status,
        "confidence": confidence,
        "events": events,
        "primary_events": physical_events,
        "orientation_factor": orientation_factor,
        "orientation_reference_tau": orientation_reference,
        "filter": {
            "kind": "arc_length_local_median",
            "window_over_c": float(filter_window_over_c),
            "points_used": int(filter_points),
        },
        "threshold": {
            "tau_absolute_floor": float(tau_absolute_floor),
            "tau_relative_fraction": float(tau_relative_fraction),
            "robust_attached_tau_reference": attached_tau_reference,
            "tau_eps": tau_eps,
        },
        "persistence": {
            "minimum_faces": int(minimum_faces),
            "minimum_arc_over_c": float(minimum_arc_over_c),
        },
        "near_wall_velocity_corroboration": "NOT_AVAILABLE",
    }
    return ordered, result


def analyze_wall_separation(
    wall_shear: pd.DataFrame,
    *,
    chord_m: float,
    velocity_m_s: float,
    filter_window_over_c: float = 0.003,
    tau_relative_fraction: float = 0.005,
    tau_absolute_floor: float = 1.0e-12,
    minimum_faces: int = 3,
    minimum_arc_over_c: float = 0.001,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    connected, connectivity = split_connected_wall_branches(wall_shear, chord_m)
    report: dict[str, Any] = {
        "separation_method_version": METHOD_VERSION,
        "wall_field_source": "OpenFOAM wallShearStress patch faces",
        "branch_mapping": connectivity,
        "branches": [],
    }
    if connected.empty:
        report.update(status="UNRESOLVED", confidence="UNRESOLVED", events=[])
        return connected, report
    analyzed: list[pd.DataFrame] = []
    all_events: list[dict[str, Any]] = []
    for (_, component_id), branch in connected.groupby(["branch_id", "component_id"], sort=True):
        values, branch_report = analyze_branch_separation(
            branch,
            velocity_m_s=velocity_m_s,
            filter_window_over_c=filter_window_over_c,
            tau_relative_fraction=tau_relative_fraction,
            tau_absolute_floor=tau_absolute_floor,
            minimum_faces=minimum_faces,
            minimum_arc_over_c=minimum_arc_over_c,
        )
        analyzed.append(values)
        report["branches"].append(branch_report)
        for event in branch_report.get("events", []):
            all_events.append({"branch_id": branch_report["branch_id"], **event})
    report["events"] = all_events
    report["status"] = "DETECTED" if any(not item.get("excluded_from_primary") for item in all_events) else "NOT_DETECTED"
    report["confidence"] = "MEDIUM" if report["status"] == "DETECTED" else "NOT_DETECTED"
    return pd.concat(analyzed, ignore_index=True), report


def _plot_wall_signals(data: pd.DataFrame, shear_output: Path, cf_output: Path) -> None:
    import matplotlib.pyplot as plt

    colors = {"upper_external": "#0068a8", "lower_external": "#c2410c", "upper_internal": "#23a6d5", "lower_internal": "#e07a3f"}
    for column, ylabel, output in (
        ("tau_t_oriented", r"Tangential kinematic wall shear stress [m$^2$/s$^2$]", shear_output),
        ("Cf_raw", r"$C_f$ [-]", cf_output),
    ):
        fig, ax = plt.subplots(figsize=(9.2, 4.8))
        for branch_id, branch in data.groupby("branch_id", sort=True):
            branch = branch.sort_values("s_over_c")
            color = colors.get(str(branch_id), "#555555")
            ax.plot(branch["s_over_c"], branch[column], color=color, alpha=0.42, linewidth=0.75, label=f"{branch_id} raw")
            filtered = "tau_t_filtered" if column == "tau_t_oriented" else "Cf_filtered"
            ax.plot(branch["s_over_c"], branch[filtered], color=color, linewidth=1.25, label=f"{branch_id} filtered")
        ax.axhline(0.0, color="black", linestyle="--", linewidth=0.8)
        ax.set_xlabel(r"Branch arc length, $s/c$ [-]")
        ax.set_ylabel(ylabel)
        ax.grid(True, linewidth=0.35, alpha=0.65)
        ax.legend(fontsize=7, ncol=2)
        save_scientific_figure(
            fig, output, data=data,
            metadata={
                "source": "OpenFOAM wallShearStress patch faces",
                "transformation": "connectivity ordering; tangential projection; recorded median filter",
                "grouping": "branch_id", "sorting": "s_over_c ascending within branch",
            },
        )


def _ordered_cp_branches(cp_data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {"x_over_c", "Cp"}
    if cp_data.empty or not required.issubset(cp_data.columns):
        return pd.DataFrame(), {"excluded_rows": int(len(cp_data)), "reason": "missing required columns"}
    values = cp_data.copy()
    values["x_over_c"] = pd.to_numeric(values["x_over_c"], errors="coerce")
    values["Cp"] = pd.to_numeric(values["Cp"], errors="coerce")
    valid = np.isfinite(values["x_over_c"]) & np.isfinite(values["Cp"])
    excluded = int((~valid).sum())
    values = values.loc[valid].copy()
    connectivity_used = False
    connectivity_audit: dict[str, Any] = {}
    connectivity_columns = {"patch", "x_m", "y_m", *EDGE_COLUMNS}
    if connectivity_columns.issubset(values.columns):
        chord_m = max(
            float(pd.to_numeric(values["x_m"], errors="coerce").max())
            - float(pd.to_numeric(values["x_m"], errors="coerce").min()),
            1.0e-12,
        )
        connected, connectivity_audit = split_connected_wall_branches(values, chord_m)
        if not connected.empty:
            values = connected
            component_key = (
                values["patch"].astype(str)
                + ":"
                + values["component_id"].astype(str)
            )
            branch_component_count = component_key.groupby(values["branch_id"]).transform("nunique")
            values["branch_id"] = np.where(
                branch_component_count > 1,
                values["branch_id"].astype(str) + "_" + component_key,
                values["branch_id"].astype(str),
            )
            connectivity_used = True
    if not connectivity_used:
        legacy_required = {"wall_side", "surface"}
        if not legacy_required.issubset(values.columns):
            return pd.DataFrame(), {
                "excluded_rows": int(len(cp_data)),
                "reason": "wall connectivity and legacy branch labels are both unavailable",
            }
        values["branch_id"] = values["surface"].astype(str) + "_" + values["wall_side"].astype(str)
    before = len(values)
    values = (
        values.groupby(["branch_id", "wall_side", "surface", "x_over_c"], as_index=False, sort=False)["Cp"]
        .mean().sort_values(["branch_id", "x_over_c"], kind="stable").reset_index(drop=True)
    )
    return values, {
        "excluded_rows": excluded,
        "duplicates_consolidated": int(before - len(values)),
        "rule": "mean Cp at identical branch_id and x/c",
        "connectivity_used": connectivity_used,
        "connectivity": connectivity_audit,
    }


def write_separation_products(
    data: pd.DataFrame,
    report: dict[str, Any],
    output_dir: Path,
    *,
    cp_data: pd.DataFrame | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    shear_csv = output_dir / "wall_shear_stress_vs_xc.csv"
    cf_csv = output_dir / "skin_friction_coefficient_vs_xc.csv"
    data.to_csv(shear_csv, index=False)
    data[[column for column in data.columns if column not in VECTOR_COLUMNS or column.startswith("wallShearStress")]].to_csv(cf_csv, index=False)
    shear_png = output_dir / "wall_shear_stress_vs_xc.png"
    cf_png = output_dir / "skin_friction_coefficient_vs_xc.png"
    _plot_wall_signals(data, shear_png, cf_png)
    events_json = output_dir / "separation_events.json"
    events_csv = output_dir / "separation_events.csv"
    events_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    pd.DataFrame(report.get("events") or [], columns=[
        "branch_id", "type", "s_over_c", "x_over_c", "y_over_c", "confidence", "excluded_from_primary", "exclusion_reason"
    ]).to_csv(events_csv, index=False)

    import matplotlib.pyplot as plt
    overlay = output_dir / "separation_overlay_cp_cf.png"
    fig, (ax_cp, ax_cf) = plt.subplots(2, 1, figsize=(9.2, 7.4), constrained_layout=True)
    ordered_cp, cp_audit = _ordered_cp_branches(cp_data if cp_data is not None else pd.DataFrame())
    if not ordered_cp.empty:
        for branch_id, branch in ordered_cp.groupby("branch_id", sort=True):
            ax_cp.plot(branch["x_over_c"], branch["Cp"], linewidth=1.0, label=str(branch_id))
        ax_cp.invert_yaxis()
        ax_cp.set_xlabel(r"Chord coordinate, $x/c$ [-]")
        ax_cp.set_ylabel(r"$C_p$ [-]")
        ax_cp.legend(fontsize=7)
    else:
        ax_cp.text(0.5, 0.5, "Cp unavailable", transform=ax_cp.transAxes, ha="center")
    for branch_id, branch in data.groupby("branch_id", sort=True):
        branch = branch.sort_values("s_over_c", kind="stable")
        ax_cf.plot(branch["s_over_c"], branch["Cf_filtered"], linewidth=1.0, label=str(branch_id))
    for event in report.get("events") or []:
        if not event.get("excluded_from_primary"):
            ax_cf.axvline(float(event["s_over_c"]), color="#CC79A7", linestyle="--", linewidth=0.8)
    ax_cf.axhline(0.0, color="black", linestyle="--", linewidth=0.75)
    ax_cf.set_xlabel(r"Branch arc length, $s/c$ [-]")
    ax_cf.set_ylabel(r"$C_f$ [-]")
    ax_cf.legend(fontsize=7)
    for axis in (ax_cp, ax_cf):
        axis.grid(True, linewidth=0.35, alpha=0.65)
    plot_data = pd.concat([
        ordered_cp.assign(quantity="Cp", coordinate=ordered_cp.get("x_over_c"), value=ordered_cp.get("Cp")),
        data.assign(quantity="Cf", coordinate=data.get("s_over_c"), value=data.get("Cf_filtered")),
    ], ignore_index=True, sort=False)
    save_scientific_figure(
        fig, overlay, data=plot_data,
        metadata={
            "source": "OpenFOAM Cp and wallShearStress patch-face exports",
            "transformation": "Cp branch normalization and Cf connectivity ordering",
            "grouping": "branch_id", "sorting": "Cp by x/c; Cf by s/c",
            "deduplication": cp_audit,
        },
    )

    summary = output_dir / "separation_summary.md"
    lines = [
        "# Boundary-layer separation summary",
        "",
        f"- Method: `{report.get('separation_method_version')}`",
        f"- Status: `{report.get('status')}`",
        f"- Confidence: `{report.get('confidence')}`",
        "- Criterion: persistent sign change of tangential wallShearStress along a connectivity-ordered branch.",
        "- Raw and filtered signals are both retained; filtering is used only for event detection.",
        "- Near-wall velocity corroboration is reported as unavailable unless a real sampled field is supplied.",
        "- Events close to LE, TE or lips are labelled LOW and excluded from the primary result.",
        "",
        "## Events",
        "",
    ]
    if report.get("events"):
        for event in report["events"]:
            lines.append(
                f"- `{event['branch_id']}` {event['type']}: s/c={event['s_over_c']:.6g}, "
                f"x/c={event['x_over_c']:.6g}, confidence={event['confidence']}, "
                f"excluded={event['excluded_from_primary']}"
            )
    else:
        lines.append("- No robust persistent event was detected.")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    products = [shear_csv, shear_png, cf_csv, cf_png, events_json, events_csv, overlay, summary]
    report["products"] = [str(path) for path in products]
    events_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {path.name: str(path) for path in products}


def summarize_urans_separation(
    snapshots: Iterable[tuple[float, dict[str, Any]]],
    output_dir: Path,
) -> dict[str, Any]:
    """Aggregate retained real-time separation events without inventing a PSD."""
    rows: list[dict[str, Any]] = []
    for time_s, report in snapshots:
        branches = {str(item.get("branch_id")): item for item in report.get("branches") or []}
        for branch_id, branch in branches.items():
            physical = [event for event in branch.get("primary_events") or []]
            separation = next((event for event in physical if event.get("type") == "separation"), None)
            reattachment = next((event for event in physical if event.get("type") == "reattachment"), None)
            rows.append({
                "time_s": float(time_s),
                "branch_id": branch_id,
                "x_sep_over_c": separation.get("x_over_c") if separation else None,
                "x_reattach_over_c": reattachment.get("x_over_c") if reattachment else None,
                "bubble_length_over_c": (
                    float(reattachment["s_over_c"]) - float(separation["s_over_c"])
                    if separation and reattachment else None
                ),
                "separated": bool(separation),
            })
    frame = pd.DataFrame(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "separation_time_history.csv"
    frame.to_csv(csv_path, index=False)
    import matplotlib.pyplot as plt
    history_png = output_dir / "separation_time_history.png"
    obsolete_occupancy = output_dir / "reverse_flow_occupancy.png"
    if obsolete_occupancy.is_file():
        obsolete_occupancy.unlink()
    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    for branch_id, branch in frame.groupby("branch_id") if not frame.empty else []:
        ax.plot(branch["time_s"], branch["x_sep_over_c"], label=str(branch_id))
    ax.set_xlabel(r"Physical time, $t$ [s]")
    ax.set_ylabel(r"Separation location, $x_{sep}/c$ [-]")
    ax.grid(True, linewidth=0.35, alpha=0.65)
    if not frame.empty:
        ax.legend(fontsize=7)
    fig.tight_layout()
    save_scientific_figure(
        fig,
        history_png,
        data=frame,
        metadata={
            "source": "URANS wall-shear separation snapshots",
            "grouping": "connected wall branch",
            "sorting": "physical time",
        },
    )
    duration = float(frame["time_s"].max() - frame["time_s"].min()) if len(frame) > 1 else 0.0
    return {
        "status": "AVAILABLE" if not frame.empty else "UNRESOLVED",
        "samples": int(frame["time_s"].nunique()) if not frame.empty else 0,
        "duration_s": duration,
        "psd_status": "NOT_COMPUTED_INSUFFICIENT_DURATION_OR_EXPLICIT_REQUEST",
        "products": [str(csv_path), str(history_png)],
    }
