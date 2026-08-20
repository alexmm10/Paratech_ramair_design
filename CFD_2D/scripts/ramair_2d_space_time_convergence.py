#!/usr/bin/env python3
"""Accepted-only spatial-temporal convergence assembly."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ramair_2d_convergence_analysis import generalized_gci, relative_percent
from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    utc_stamp,
    write_json_atomic,
)


ACCEPTED_URANS_STATES = {"URANS_ACCEPTED", "ACCEPTED", "ACCEPTED_WITH_WARNINGS"}


def accepted_rows(rows: Iterable[dict[str, Any]], topology: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if str(row.get("topology")) == topology
        and str(row.get("review_status") or row.get("status"))
        in ACCEPTED_URANS_STATES
        and row.get("dt_s") is not None
        and row.get("cell_count") is not None
        and row.get("sampling_duration_s") is not None
    ]


def _comparison_rows(rows: list[dict[str, Any]], axis: str) -> list[dict[str, Any]]:
    if axis == "temporal":
        key = "dt_s"
        group_names = ("mesh_id", "sampling_duration_s")
    elif axis == "spatial":
        key = "cell_count"
        group_names = ("dt_s", "sampling_duration_s")
    else:
        raise ValueError("axis must be temporal or spatial")
    output: list[dict[str, Any]] = []
    group_values = {
        tuple(row.get(name) for name in group_names)
        for row in rows
    }
    for group_value in sorted(group_values, key=str):
        subset = [
            row
            for row in rows
            if tuple(row.get(name) for name in group_names) == group_value
        ]
        subset.sort(key=lambda row: float(row[key]), reverse=axis == "temporal")
        if len(subset) < 2:
            continue
        reference = subset[-1]
        for row in subset:
            record = {
                "axis": axis,
                "group": " | ".join(
                    f"{name}={value}"
                    for name, value in zip(group_names, group_value)
                ),
                "run_id": row.get("run_id"),
                "mesh_id": row.get("mesh_id"),
                "dt_s": row.get("dt_s"),
                "cell_count": row.get("cell_count"),
                "sampling_duration_s": row.get(
                    "sampling_duration_s"
                ),
            }
            for metric in ("mean_Cl", "mean_Cd", "mean_Cm", "rms_Cl", "dominant_frequency_hz"):
                if row.get(metric) is None or reference.get(metric) is None:
                    continue
                record[metric] = row[metric]
                record[f"{metric}_difference_percent"] = relative_percent(
                    float(row[metric]),
                    float(reference[metric]),
                )
            output.append(record)
    return output


def _spatial_gci(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    groups = {
        (row.get("dt_s"), row.get("sampling_duration_s"))
        for row in rows
    }
    for dt_s, duration_s in sorted(groups, key=str):
        subset = [
            row
            for row in rows
            if row.get("dt_s") == dt_s
            and row.get("sampling_duration_s") == duration_s
        ]
        subset.sort(key=lambda row: int(row["cell_count"]))
        if len(subset) < 3:
            reports.append({
                "dt_s": dt_s,
                "sampling_duration_s": duration_s,
                "available": False,
                "reason": "THREE_ACCEPTED_MESH_LEVELS_REQUIRED",
            })
            continue
        coarse, medium, fine = subset[-3:]
        for metric in ("mean_Cl", "mean_Cd", "mean_Cm"):
            if any(row.get(metric) is None for row in (coarse, medium, fine)):
                reports.append({
                    "dt_s": dt_s,
                    "sampling_duration_s": duration_s,
                    "metric": metric,
                    "available": False,
                    "reason": "METRIC_NOT_AVAILABLE",
                })
                continue
            result = generalized_gci(
                coarse_value=float(coarse[metric]),
                medium_value=float(medium[metric]),
                fine_value=float(fine[metric]),
                coarse_cells=int(coarse["cell_count"]),
                medium_cells=int(medium["cell_count"]),
                fine_cells=int(fine["cell_count"]),
            )
            reports.append({
                "dt_s": dt_s,
                "sampling_duration_s": duration_s,
                "metric": metric,
                "coarse_run_id": coarse.get("run_id"),
                "medium_run_id": medium.get("run_id"),
                "fine_run_id": fine.get("run_id"),
                **result,
            })
    return reports


def build_space_time_report(
    rows: Iterable[dict[str, Any]],
    *,
    topology: str,
    output_root: Path | None = None,
) -> dict[str, Any]:
    all_rows = [dict(row) for row in rows]
    selected = accepted_rows(all_rows, topology)
    temporal = _comparison_rows(selected, "temporal")
    spatial = _comparison_rows(selected, "spatial")
    gci = _spatial_gci(selected)
    report = {
        "schema_version": 1,
        "topology": topology,
        "status": "AVAILABLE" if temporal or spatial else "INSUFFICIENT_ACCEPTED_RUNS",
        "accepted_run_count": len(selected),
        "excluded_run_count": len(all_rows) - len(selected),
        "temporal_comparisons": temporal,
        "spatial_comparisons": spatial,
        "gci": gci,
        "reference_policy": "finest accepted mesh and smallest accepted dt",
        "equal_sampling_duration_required": True,
        "frequency_and_courant_are_urans_only": True,
        "limitations": [
            row["reason"]
            for row in gci
            if not row.get("available") and row.get("reason")
        ],
        "generated_at": utc_stamp(),
    }
    if output_root is not None:
        output_root = Path(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output_root / "space_time_report.json", report)
        if selected:
            pd.DataFrame(selected).to_csv(
                output_root / "accepted_runs.csv", index=False
            )
        if temporal:
            pd.DataFrame(temporal).to_csv(
                output_root / "temporal_comparison.csv", index=False
            )
        if spatial:
            pd.DataFrame(spatial).to_csv(
                output_root / "spatial_comparison.csv", index=False
            )
        if gci:
            pd.DataFrame(gci).to_csv(
                output_root / "gci_guarded.csv", index=False
            )
    return report


def collect_accepted_run_rows(project_root: Path) -> list[dict[str, Any]]:
    active = active_workspace_root(Path(project_root).resolve())
    rows: list[dict[str, Any]] = []
    for metadata_path in sorted(
        (active / "runs").glob("*/*/*/case_metadata.json")
    ):
        run_root = metadata_path.parent
        metadata = read_json(metadata_path, {}) or {}
        review = read_json(run_root / "review.json", {}) or {}
        if str(review.get("status")) not in ACCEPTED_URANS_STATES:
            continue
        summary = (
            read_json(run_root / "postprocess/case_summary.json", {}) or {}
        )
        if not summary:
            summary = read_json(run_root / "case_summary.json", {}) or {}
        means = summary.get("mean") or {}
        signals = review.get("signals") or {}
        sampling_window = review.get("sampling_window") or {}
        sampling_duration = None
        if (
            sampling_window.get("start_s") is not None
            and sampling_window.get("end_s") is not None
        ):
            sampling_duration = (
                float(sampling_window["end_s"])
                - float(sampling_window["start_s"])
            )
        row = {
            "run_id": metadata.get("run_id") or run_root.name,
            "topology": metadata.get("topology"),
            "mesh_id": metadata.get("mesh_id"),
            "mesh_level": metadata.get("mesh_level"),
            "cell_count": metadata.get("cell_count"),
            "dt_s": metadata.get("dt_s"),
            "sampling_duration_s": sampling_duration,
            "review_status": review.get("status"),
            "mean_Cl": means.get("Cl")
            or (signals.get("Cl") or {}).get("summary", {}).get("mean"),
            "mean_Cd": means.get("Cd")
            or (signals.get("Cd") or {}).get("summary", {}).get("mean"),
            "mean_Cm": means.get("Cm")
            or (signals.get("Cm") or {}).get("summary", {}).get("mean"),
            "rms_Cl": (signals.get("Cl") or {})
            .get("summary", {})
            .get("rms"),
            "dominant_frequency_hz": (signals.get("Cl") or {})
            .get("spectrum", {})
            .get("dominant_frequency_hz"),
        }
        rows.append(row)
    return rows


def build_workspace_reports(project_root: Path) -> dict[str, Any]:
    active = active_workspace_root(Path(project_root).resolve())
    rows = collect_accepted_run_rows(project_root)
    reports = {}
    for topology in ("closed", "open"):
        reports[topology] = build_space_time_report(
            rows,
            topology=topology,
            output_root=(
                active
                / "convergence/space_time"
                / topology
            ),
        )
    return {
        "status": (
            "AVAILABLE"
            if any(
                value["status"] == "AVAILABLE"
                for value in reports.values()
            )
            else "INSUFFICIENT_ACCEPTED_RUNS"
        ),
        "reports": reports,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--topology", choices=("closed", "open"))
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.project_root:
        print(build_workspace_reports(args.project_root))
        return 0
    if not args.input or not args.topology or not args.output:
        raise ValueError(
            "Use --project-root or provide --input, --topology and --output"
        )
    frame = pd.read_csv(args.input)
    print(
        build_space_time_report(
            frame.to_dict(orient="records"),
            topology=args.topology,
            output_root=args.output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
