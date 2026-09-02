#!/usr/bin/env python3
"""Postprocess selected alpha cases sequentially without hiding failures."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def safe_alpha_dir(alpha: float) -> str:
    return f"alpha_{float(alpha):+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def command_for_alpha(args: argparse.Namespace, alpha: float) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("ramair_2d_postprocess.py")),
        "--case-root", str(args.case_root),
        "--variant", args.variant,
        "--alpha", str(float(alpha)),
        "--average-from-fraction", str(float(args.average_from_fraction)),
        "--openfoam-postprocess-timeout-s", str(max(30, int(args.timeout_s))),
        "--velocity-profile-sample-points", str(max(10, int(args.velocity_profile_sample_points))),
    ]
    if args.velocity_profile_stations:
        command += ["--velocity-profile-stations", *[str(float(value)) for value in args.velocity_profile_stations]]
    if args.run_openfoam_postprocess:
        command.append("--run-openfoam-postprocess")
    if args.export_mode in {"latest_vtk", "all_vtk"}:
        command.append("--export-vtk")
    if args.export_mode == "all_vtk":
        command.append("--export-vtk-all-times")
    if not args.wall_profile_analysis:
        command.append("--no-wall-profile-analysis")
    if args.automatic_paraview_products:
        command += [
            "--automatic-paraview-products",
            "--paraview-maximum-frames", str(max(2, int(args.paraview_maximum_frames))),
        ]
        if args.paraview_time_range_s is not None:
            command += [
                "--paraview-time-range-s",
                str(float(args.paraview_time_range_s[0])),
                str(float(args.paraview_time_range_s[1])),
            ]
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--alphas", type=float, nargs="+", required=True)
    parser.add_argument("--average-from-fraction", type=float, default=0.6)
    parser.add_argument("--run-openfoam-postprocess", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--export-mode", choices=["none", "coefficients_only", "openfoam_reader", "latest_vtk", "all_vtk"], default="openfoam_reader")
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--wall-profile-analysis", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--velocity-profile-stations", type=float, nargs="*", default=[0.1, 0.3, 0.6, 0.9])
    parser.add_argument("--velocity-profile-sample-points", type=int, default=40)
    parser.add_argument("--automatic-paraview-products", action="store_true")
    parser.add_argument("--paraview-maximum-frames", type=int, default=24)
    parser.add_argument(
        "--paraview-time-range-s",
        type=float,
        nargs=2,
        metavar=("START_S", "END_S"),
        help="Optional physical-time interval used only for URANS ParaView frames.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.case_root = args.case_root.resolve()
    status_path = (
        args.case_root / "CFD_2D" / "results" / args.variant / "batch_postprocess_status.json"
    )
    rows: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "RUNNING",
        "variant": args.variant,
        "alphas_deg": [float(value) for value in args.alphas],
        "active_alpha_deg": None,
        "rows": rows,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(status_path, report)
    for alpha in args.alphas:
        case_dir = (
            args.case_root / "CFD_2D" / "openfoam_cases" / args.variant / safe_alpha_dir(alpha)
        )
        command = command_for_alpha(args, alpha)
        if not (case_dir / "system" / "controlDict").is_file():
            rows.append({"alpha_deg": float(alpha), "status": "MISSING_CASE", "case_dir": str(case_dir)})
            continue
        report.update(active_alpha_deg=float(alpha), rows=rows, updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        write_json_atomic(status_path, report)
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=str(args.case_root), text=True)
        summary_path = (
            args.case_root / "CFD_2D" / "results" / args.variant
            / safe_alpha_dir(alpha) / "case_summary.json"
        )
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            summary = {}
        rows.append({
            "alpha_deg": float(alpha),
            "status": str(summary.get("status") or ("ERROR" if completed.returncode else "UNKNOWN")),
            "returncode": int(completed.returncode),
            "wall_time_s": float(time.perf_counter() - started),
            "case_dir": str(case_dir),
            "result_dir": str(summary_path.parent),
        })
        report.update(rows=rows, updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        write_json_atomic(status_path, report)
    failures = sum(1 for row in rows if int(row.get("returncode", 0) or 0) != 0 or row.get("status") in {"ERROR", "POSTPROCESS_ERROR", "MISSING_CASE"})
    report.update(
        status="FINISHED_WITH_ISSUES" if failures else "FINISHED",
        active_alpha_deg=None,
        failure_count=int(failures),
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    write_json_atomic(status_path, report)
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
