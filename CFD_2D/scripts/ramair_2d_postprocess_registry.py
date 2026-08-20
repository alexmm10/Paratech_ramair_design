#!/usr/bin/env python3
"""Authoritative postprocess inventory for Validation Lab runs."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    utc_stamp,
    write_json_atomic,
)


POSTPROCESS_STATES = {
    "NOT_REQUESTED",
    "RUNNING",
    "PARTIAL",
    "COMPLETED",
    "FAILED",
}

PRODUCT_GROUPS = (
    "scalar_histories",
    "statistics_convergence",
    "surface_plots",
    "field_images",
    "animations",
    "paraview",
    "technical_files",
)


def product_group(path: Path) -> str:
    """Classify one real product for the manifest-driven UI browser."""
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in {".gif", ".mp4", ".avi", ".webm"}:
        return "animations"
    if suffix in {".foam", ".pvsm", ".pvd", ".vtk", ".vtu", ".vtp"} or any(
        token in name for token in ("paraview", "vtk")
    ):
        return "paraview"
    if any(token in name for token in ("cp_x", "yplus", "wall", "surface")):
        return "surface_plots"
    if suffix in {".png", ".jpg", ".jpeg", ".svg"}:
        if any(
            token in name
            for token in ("residual", "convergence", "statistic", "spectrum")
        ):
            return "statistics_convergence"
        return "field_images"
    if suffix in {".csv", ".tsv"}:
        if any(
            token in name
            for token in ("force", "coefficient", "residual", "history", "probe")
        ):
            return "scalar_histories"
        if any(token in name for token in ("cp", "yplus", "surface", "wall")):
            return "surface_plots"
        return "statistics_convergence"
    return "technical_files"


def field_scale(
    values: Sequence[float],
    *,
    mode: str = "exact",
    manual_min: float | None = None,
    manual_max: float | None = None,
    robust_percentiles: tuple[float, float] = (1.0, 99.0),
) -> dict[str, Any]:
    """Return an auditable exact, robust or manual display scale."""
    mode = str(mode).lower()
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not array.size:
        raise ValueError("A field scale requires at least one finite value")
    exact_min = float(np.min(array))
    exact_max = float(np.max(array))
    if mode == "exact":
        lower, upper = exact_min, exact_max
    elif mode == "robust":
        p_low, p_high = robust_percentiles
        if not 0.0 <= p_low < p_high <= 100.0:
            raise ValueError("Invalid robust percentile interval")
        lower, upper = (
            float(np.percentile(array, p_low)),
            float(np.percentile(array, p_high)),
        )
    elif mode == "manual":
        if manual_min is None or manual_max is None:
            raise ValueError("Manual scale requires manual_min and manual_max")
        lower, upper = float(manual_min), float(manual_max)
    else:
        raise ValueError(f"Unsupported field scale mode: {mode}")
    if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        pad = max(abs(lower), abs(upper), 1.0) * 1.0e-9
        lower, upper = lower - pad, upper + pad
    return {
        "mode": mode,
        "minimum": lower,
        "maximum": upper,
        "exact_minimum": exact_min,
        "exact_maximum": exact_max,
        "robust_percentiles": (
            list(robust_percentiles) if mode == "robust" else None
        ),
    }


def _real_products(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in paths:
        path = Path(source)
        if not path.exists():
            continue
        rows.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "kind": "directory" if path.is_dir() else "file",
                "bytes": path.stat().st_size if path.is_file() else None,
                "modified_at_epoch_s": path.stat().st_mtime,
                "group": product_group(path),
                "generation_status": "AVAILABLE",
            }
        )
    return rows


def write_postprocess_manifest(
    output_root: Path,
    *,
    run_id: str,
    mode: str,
    products: Iterable[Path],
    inputs: dict[str, Any] | None = None,
    errors: Iterable[str] = (),
    requested: bool = True,
    metadata: dict[str, Any] | None = None,
    regeneration_commands: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Write one manifest only from products that actually exist."""
    output_root = Path(output_root)
    actual = _real_products(products)
    error_rows = [str(value) for value in errors if str(value).strip()]
    if not requested:
        status = "NOT_REQUESTED"
    elif error_rows and actual:
        status = "PARTIAL"
    elif error_rows:
        status = "FAILED"
    elif actual:
        status = "COMPLETED"
    else:
        status = "PARTIAL"
        error_rows = ["NO_REAL_POSTPROCESS_PRODUCTS_FOUND"]
    normalized_mode = str(mode).upper()
    groups = {
        group: [row for row in actual if row["group"] == group]
        for group in PRODUCT_GROUPS
    }
    manifest = {
        "schema_version": 2,
        "run_id": str(run_id),
        "mode": normalized_mode,
        "status": status,
        "generated_at": utc_stamp(),
        "inputs": dict(inputs or {}),
        "products": actual,
        "groups": groups,
        "regeneration_commands": {
            str(group): [str(value) for value in command]
            for group, command in dict(regeneration_commands or {}).items()
            if group in PRODUCT_GROUPS and command
        },
        "errors": error_rows,
        "courant_policy": (
            "NOT_APPLICABLE_TO_RANS"
            if normalized_mode == "RANS"
            else "URANS_ONLY"
        ),
        "metadata": dict(metadata or {}),
    }
    write_json_atomic(output_root / "postprocess_manifest.json", manifest)
    return manifest


def build_postprocess_index(project_root: Path) -> dict[str, Any]:
    active = active_workspace_root(Path(project_root).resolve())
    manifests = []
    for path in sorted(active.rglob("postprocess_manifest.json")):
        payload = read_json(path, {}) or {}
        if not payload:
            continue
        manifests.append(
            {
                "run_id": payload.get("run_id"),
                "mode": payload.get("mode"),
                "status": payload.get("status"),
                "manifest": str(path),
                "generated_at": payload.get("generated_at"),
                "product_count": len(payload.get("products") or []),
                "error_count": len(payload.get("errors") or []),
            }
        )
    index = {
        "schema_version": 1,
        "study_id": "closed_open_M0p15_Re1p9e6_alpha8",
        "generated_at": utc_stamp(),
        "manifests": manifests,
    }
    write_json_atomic(active / "registry/postprocess_index.json", index)
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    print(build_postprocess_index(parse_args().project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
