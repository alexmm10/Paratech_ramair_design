#!/usr/bin/env python3
"""Create a traceable one-metre CFD geometry from preprocessor exports.

The source variant is never modified.  Only dimensional coordinates and
dimensional metadata are scaled; normalized profile coordinates, topology and
patch roles remain identical.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from project_layout import find_project_root


SOURCE_VARIANT = "reference_uncut"
TARGET_VARIANT = "reference_uncut_validation_1m"
TARGET_CHORD_M = 1.0


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def scale_points(path: Path, target_variant: str, target_chord_m: float) -> None:
    frame = pd.read_csv(path)
    if not {"x_norm", "z_norm", "x_m", "z_m"}.issubset(frame.columns):
        raise ValueError(f"Missing normalized/dimensional coordinate columns in {path}")
    frame["x_m"] = frame["x_norm"].astype(float) * target_chord_m
    frame["z_m"] = frame["z_norm"].astype(float) * target_chord_m
    if "variant" in frame.columns:
        frame["variant"] = target_variant
    frame.to_csv(path, index=False, float_format="%.10f")


def scale_manifest(path: Path, source_variant: str, target_variant: str, target_chord_m: float) -> None:
    data = read_json(path)
    source_chord = float(data.get("chord_m", target_chord_m))
    data.update(
        variant=target_variant,
        chord_m=target_chord_m,
        geometry_scaling={
            "source_variant": source_variant,
            "source_chord_m": source_chord,
            "target_chord_m": target_chord_m,
            "scale_factor": target_chord_m / source_chord,
            "method": "dimensional_coordinates_rebuilt_from_preprocessor_normalized_coordinates",
            "normalized_geometry_unchanged": True,
        },
    )
    write_json(path, data)


def generate_preview(points_path: Path, output: Path, target_chord_m: float) -> None:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    frame = pd.read_csv(points_path).sort_values("point_id")
    fig, axis = plt.subplots(figsize=(10.0, 3.8))
    axis.plot(frame["x_m"], frame["z_m"], color="#1261a0", linewidth=1.2)
    axis.scatter(frame["x_m"], frame["z_m"], s=4, color="#d1495b", alpha=0.55)
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlabel("x [m]")
    axis.set_ylabel("z [m]")
    axis.set_title(f"LS(1)-0417 validation geometry, c={target_chord_m:g} m")
    axis.grid(True, linewidth=0.35, alpha=0.5)
    fig.tight_layout()
    fig.savefig(output, dpi=220)
    plt.close(fig)


def build_scaled_variant(
    project_root: Path,
    *,
    source_variant: str = SOURCE_VARIANT,
    target_variant: str = TARGET_VARIANT,
    target_chord_m: float = TARGET_CHORD_M,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = find_project_root(project_root)
    geometry_root = root / "CFD_2D/CFD_2D_inputs/geometry"
    package_root = root / "CFD_2D/CFD_2D_inputs/case_package"
    source_geometry = geometry_root / source_variant
    source_package = package_root / source_variant
    target_geometry = geometry_root / target_variant
    target_package = package_root / target_variant
    for source in (source_geometry, source_package):
        if not source.is_dir():
            raise FileNotFoundError(source)
    for target in (target_geometry, target_package):
        if target.exists():
            if not overwrite:
                raise FileExistsError(f"Target already exists; pass --overwrite: {target}")
            shutil.rmtree(target)
    shutil.copytree(source_geometry, target_geometry)
    shutil.copytree(source_package, target_package)

    scale_points(target_geometry / "profile_points.csv", target_variant, target_chord_m)
    scale_points(target_package / "points.csv", target_variant, target_chord_m)
    scale_manifest(
        target_geometry / "profile_manifest.json",
        source_variant,
        target_variant,
        target_chord_m,
    )
    scale_manifest(
        target_package / "manifest.json",
        source_variant,
        target_variant,
        target_chord_m,
    )
    contract = read_json(target_package / "mesh_input_contract.json")
    contract["variant"] = target_variant
    contract["chord_m"] = target_chord_m
    contract["source_variant"] = source_variant
    write_json(target_package / "mesh_input_contract.json", contract)
    summary = read_json(target_package / "geometry_summary.json")
    summary["variant"] = target_variant
    summary["source_variant"] = source_variant
    summary["target_chord_m"] = target_chord_m
    write_json(target_package / "geometry_summary.json", summary)

    # The copied DXF contains source-scale dimensional entities and would be
    # misleading. CFD consumes points.csv; omit the DXF from this derived CFD
    # variant instead of pretending it is a valid one-metre CAD deliverable.
    for dxf in target_geometry.glob("*.dxf"):
        dxf.unlink()
    generate_preview(
        target_geometry / "profile_points.csv",
        target_geometry / "profile_preview.png",
        target_chord_m,
    )
    report = {
        "status": "CREATED",
        "source_variant": source_variant,
        "target_variant": target_variant,
        "target_chord_m": target_chord_m,
        "geometry_dir": str(target_geometry.resolve()),
        "case_package_dir": str(target_package.resolve()),
        "normalized_coordinates_unchanged": True,
        "dxf_exported": False,
    }
    write_json(target_geometry / "geometry_scaling_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-variant", default=SOURCE_VARIANT)
    parser.add_argument("--target-variant", default=TARGET_VARIANT)
    parser.add_argument("--target-chord-m", type=float, default=TARGET_CHORD_M)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.target_chord_m <= 0.0:
        parser.error("--target-chord-m must be positive")
    report = build_scaled_variant(
        args.project_root,
        source_variant=args.source_variant,
        target_variant=args.target_variant,
        target_chord_m=args.target_chord_m,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
