"""Derive OpenFOAM non-orthogonal controls from an actual checkMesh report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


def automatic_non_orthogonal_controls(maximum_deg: float) -> dict[str, Any]:
    angle = max(0.0, float(maximum_deg))
    if angle < 50.0:
        correctors = 0
    elif angle < 70.0:
        correctors = 1
    else:
        correctors = 2
    laplacian = (
        "Gauss linear limited 0.5"
        if angle >= 70.0
        else "Gauss linear corrected"
    )
    return {
        "maximum_non_orthogonality_deg": angle,
        "n_non_orthogonal_correctors": correctors,
        "laplacian_scheme": laplacian,
        "policy": "<50:0; 50-<70:1; >=70:2; limited 0.5 only at >=70",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def quality_controls_from_paths(paths: Iterable[Path]) -> dict[str, Any] | None:
    candidates = [Path(path) for path in paths]
    for path in candidates:
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        report = _read_json(path)
        value = report.get("checkMesh_max_non_orthogonality_deg")
        if value is None:
            value = report.get("max_non_orthogonality_deg")
        if value is not None:
            result = automatic_non_orthogonal_controls(float(value))
            result.update(source=str(path), source_kind="quality_json")
            return result
    pattern = re.compile(
        r"Mesh non-orthogonality Max:\s*([-+0-9.eE]+)", re.IGNORECASE
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            match = pattern.search(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if match:
            result = automatic_non_orthogonal_controls(float(match.group(1)))
            result.update(source=str(path), source_kind="checkMesh_log")
            return result
    return None


def quality_controls_for_mesh(mesh_root: Path) -> dict[str, Any] | None:
    root = Path(mesh_root)
    relative_candidates = [
        root / "mesh_quality_report.json",
        root / "mesh_report.json",
        root / "log.checkMesh",
        root / "mesh_attempt_001/mesh_quality_report.json",
        root / "mesh_attempt_001/log.checkMesh",
        root / "Mesh Data/mesh_quality_report.json",
        root / "Mesh Data/log.checkMesh",
        root / "Mesh Data/mesh_attempt_001/mesh_quality_report.json",
        root / "Mesh Data/mesh_attempt_001/log.checkMesh",
    ]
    return quality_controls_from_paths(relative_candidates)
