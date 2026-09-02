#!/usr/bin/env python3
"""Explicit full postprocess for one Validation Lab RANS checkpoint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from openfoam_wall_analysis import analyze_wall_boundary_layer
from ramair_2d_postprocess import postprocess, run_openfoam_post_exports
from ramair_2d_rans_paraview_final import prepare_final_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--wall-only", action="store_true")
    parser.add_argument(
        "--include-animations",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--animations-only", action="store_true")
    args = parser.parse_args()
    readiness = prepare_final_state(args.case.resolve())
    case_config_path = args.case.resolve() / "case_config.json"
    case_config = (
        json.loads(case_config_path.read_text(encoding="utf-8"))
        if case_config_path.is_file()
        else {}
    )
    alpha_deg = float(case_config.get("alpha_deg", 0.0))
    wall_analysis: dict[str, object] | None = None
    if args.wall_only:
        field_generation = run_openfoam_post_exports(
            args.case.resolve(),
            args.output.resolve(),
            export_vtk=False,
            run_openfoam_postprocess=True,
            timeout_s=900,
            export_vtk_all_times=False,
            simulation_mode="RANS",
        )
        wall_analysis = analyze_wall_boundary_layer(
            project_root=args.project_root.resolve(),
            case_dir=args.case.resolve(),
            output_dir=args.output.resolve(),
            variant=args.variant,
            run_openfoam_tools=True,
            timeout_s=900,
            stations_xc=[0.1, 0.3, 0.6, 0.9],
            sample_points=40,
            solver_module="incompressibleFluid",
            simulation_mode="RANS",
            include_temporal_separation_history=False,
        )
    else:
        generated_output = postprocess(
            args.project_root.resolve(),
            args.variant,
            alpha_deg,
            0.6,
            export_vtk=False,
            export_vtk_all_times=False,
            run_openfoam_postprocess=True,
            openfoam_postprocess_timeout_s=900,
            open_results_folder=False,
            open_paraview=False,
            wall_profile_analysis=True,
            velocity_profile_stations=[0.1, 0.3, 0.6, 0.9],
            velocity_profile_sample_points=40,
            automatic_paraview_products=True,
            include_paraview_animations=bool(args.include_animations),
            paraview_animations_only=bool(args.animations_only),
            direct_case_dir=args.case,
            direct_output_dir=args.output,
            simulation_mode="RANS",
        )
        summary_path = generated_output / "case_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            candidate = summary.get("wall_boundary_layer_analysis")
            if isinstance(candidate, dict):
                wall_analysis = candidate
    report = {
        "status": "GENERATED",
        "final_state": readiness,
        "output": str(args.output.resolve()),
        "wall_analysis": wall_analysis,
        "field_generation": field_generation if args.wall_only else [],
        "policy": {
            "explicit_on_demand": True,
            "latest_vtk_only": False,
            "volume_reader": "OpenFOAMReader_direct",
            "animations": bool(args.include_animations or args.animations_only),
            "animations_only": bool(args.animations_only),
            "all_time_volume_copy": False,
            "wall_only": bool(args.wall_only),
        },
    }
    (args.output / "rans_full_postprocess_manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
