#!/usr/bin/env python3
"""Shared, versioned geometry DTO and reusable airfoil catalogue.

The UI and the preprocessor both consume the crossport part of this DTO.  The
catalogue preserves imported source bytes and stores only small metadata in the
project checkout.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

from ramair_profile_utils import read_and_canonicalize_profile_2d


GEOMETRY_DTO_SCHEMA_VERSION = 1
PROFILE_CATALOG_SCHEMA_VERSION = 1
TE_LABELS = {
    "rounded": "Rounded",
    "sharp_extension": "Sharp",
    "straight_gap": "No modification",
}


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _profile_roles(path: Path) -> list[str]:
    name = path.name.lower()
    roles: list[str] = []
    if "ls1-0417_cut" in name:
        roles.extend(["open_profile", "validation_ls_open"])
    if "nasa ls1-0417" in name or "ls1-0417_clean" in name:
        roles.extend(["base_profile", "validation_ls_closed"])
    if "ross_standard" in name:
        roles.append("validation_ross_standard")
    if "ross_minimum" in name:
        roles.append("validation_ross_minimum")
    return roles or ["profile"]


def _catalogue_entry(root: Path, path: Path, *, profile_id: str | None = None,
                     imported: bool = False, work_case_id: str | None = None) -> dict[str, Any]:
    raw = path.read_bytes()
    canonical = read_and_canonicalize_profile_2d(path, "open_ramair", has_inlet="auto")
    points = canonical.open_contour
    bounds = {}
    if not points.empty:
        bounds = {
            "x_min": float(points.x_norm.min()),
            "x_max": float(points.x_norm.max()),
            "z_min": float(points.z_norm.min()),
            "z_max": float(points.z_norm.max()),
        }
    rel = _relative(root, path)
    return {
        "profile_id": profile_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"ramair-profile:{rel}")),
        "display_name": path.stem,
        "source_path": rel,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_type": "imported" if imported else "project",
        "work_case_id": work_case_id,
        "roles": _profile_roles(path),
        "point_count": int(len(points)),
        "bounds": bounds,
        "validation": {
            "valid": not canonical.errors,
            "warnings": list(canonical.warnings),
            "errors": list(canonical.errors),
        },
    }


def load_profile_catalog(root: Path) -> dict[str, Any]:
    root = Path(root)
    catalogue_path = root / "Airfoil Profiles/profile_catalog.json"
    persisted = {}
    if catalogue_path.is_file():
        try:
            persisted = json.loads(catalogue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            persisted = {}
    entries = {
        str(item.get("profile_id")): dict(item)
        for item in (persisted.get("profiles") or [])
        if isinstance(item, dict) and item.get("profile_id")
    }
    for suffix in ("*.dat", "*.csv"):
        for path in sorted((root / "Airfoil Profiles").glob(suffix)):
            try:
                entry = _catalogue_entry(root, path)
            except Exception:
                continue
            entries.setdefault(entry["profile_id"], entry)
    return {
        "schema_version": PROFILE_CATALOG_SCHEMA_VERSION,
        "profiles": sorted(entries.values(), key=lambda item: str(item.get("display_name", "")).lower()),
    }


def import_profile(root: Path, filename: str, content: bytes, *, work_case_id: str | None = None) -> dict[str, Any]:
    root = Path(root)
    if not content:
        raise ValueError("El archivo de perfil esta vacio.")
    safe_name = Path(filename).name
    if Path(safe_name).suffix.lower() not in {".dat", ".csv", ".txt"}:
        raise ValueError("El perfil debe usar un formato de coordenadas .dat, .csv o .txt.")
    profile_id = str(uuid.uuid4())
    destination = root / "Airfoil Profiles/Imported" / profile_id / "original" / safe_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    try:
        entry = _catalogue_entry(
            root, destination, profile_id=profile_id, imported=True, work_case_id=work_case_id
        )
        if not entry["validation"]["valid"] or entry["point_count"] < 4:
            raise ValueError("; ".join(entry["validation"]["errors"]) or "El perfil no contiene suficientes coordenadas finitas.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    metadata_path = destination.parents[1] / "profile_metadata.json"
    _atomic_json(metadata_path, entry)
    catalogue = load_profile_catalog(root)
    catalogue["profiles"] = [item for item in catalogue["profiles"] if item.get("profile_id") != profile_id]
    catalogue["profiles"].append(entry)
    catalogue["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_json(root / "Airfoil Profiles/profile_catalog.json", catalogue)
    return entry


def crossport_x_positions(crossports: dict[str, Any]) -> list[float]:
    mode = str(crossports.get("position_mode", "standard_3")).strip().lower()
    count = max(1, int(crossports.get("count", 3)))
    if mode == "standard_3":
        return [0.25, 0.45, 0.65] if count == 3 else (
            [0.45] if count == 1 else [0.25 + index * (0.45 / (count - 1)) for index in range(count)]
        )
    if mode == "custom":
        return [float(value) for value in crossports.get("x_positions_chord", [])]
    if mode == "equidistant":
        start = float(crossports.get("x_start_chord", 0.25))
        end = float(crossports.get("x_end_chord", 0.70))
        return [(start + end) / 2.0] if count == 1 else [start + index * (end - start) / (count - 1) for index in range(count)]
    raise ValueError(f"Unsupported crossport position_mode: {mode!r}")


def crossport_specs(crossports: dict[str, Any]) -> list[dict[str, Any]]:
    custom = crossports.get("custom_specs") or []
    source = custom if custom else [{"x": value} for value in crossport_x_positions(crossports)]
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(source, start=1):
        item = dict(raw)
        if "x" not in item or not math.isfinite(float(item["x"])):
            raise ValueError(f"Crossport {index} has no finite x/c position.")
        shape = str(item.get("shape", crossports.get("shape", "ellipse"))).lower()
        orientation = str(item.get("orientation", crossports.get("ellipse_orientation", "horizontal"))).lower()
        if shape not in {"circle", "ellipse"}:
            raise ValueError(f"Crossport {index} has unsupported shape {shape!r}.")
        if orientation not in {"horizontal", "vertical", "auto"}:
            raise ValueError(f"Crossport {index} has unsupported orientation {orientation!r}.")
        result.append({
            "hole_id": str(item.get("hole_id") or f"crossport-{index}"),
            "x": float(item["x"]),
            "shape": shape,
            "orientation": orientation,
            "radius_chord_frac": float(item["radius_chord_frac"]) if item.get("radius_chord_frac") is not None else None,
            "width_chord_frac": float(item.get("width_chord_frac", crossports.get("width_fraction_chord", 0.08))),
            "height_thickness_frac": float(item.get("height_thickness_frac", crossports.get("height_fraction_local_thickness", 0.15))),
            "z_center_fraction": float(item["z_center_fraction"]) if item.get("z_center_fraction") is not None else None,
            "points_per_loop": max(12, int(item.get("points_per_loop", crossports.get("points_per_loop", 32)))),
        })
    return result


def geometry_dto(project_config: dict[str, Any], inlet_config: dict[str, Any] | None = None) -> dict[str, Any]:
    profile_inputs = dict(project_config.get("profile_inputs") or {})
    airfoil = dict(project_config.get("airfoil_processing") or {})
    crossports = dict(project_config.get("crossports") or {})
    mode = str(airfoil.get("te_closure_mode", "rounded"))
    if mode not in TE_LABELS:
        raise ValueError(f"Unsupported trailing-edge mode: {mode!r}")
    return {
        "schema_version": GEOMETRY_DTO_SCHEMA_VERSION,
        "profiles": {
            "active_open": profile_inputs.get("main_profile"),
            "base_closed": (inlet_config or {}).get("base_profile") or profile_inputs.get("reference_uncut_profile"),
            "validation_ls_open": profile_inputs.get("main_profile"),
            "validation_ls_closed": profile_inputs.get("reference_uncut_profile"),
            "validation_ross_standard": profile_inputs.get("ross_standard_profile"),
            "validation_ross_minimum": profile_inputs.get("ross_minimum_profile"),
        },
        "trailing_edge": {
            "mode": mode,
            "label": TE_LABELS[mode],
            "rounding_points": int(airfoil.get("te_rounding_points", 20)),
            "sharp_max_x_c": float(airfoil.get("sharp_te_intersection_max_x_c", 1.08)),
            "sharp_safe_gap_chord": float(airfoil.get("sharp_te_safe_gap_chord", 1e-5)),
            "model_zero_thickness_as_thin_solid": bool(airfoil.get("model_zero_thickness_as_thin_solid", True)),
            "fabric_thickness_chord": float(airfoil.get("fabric_thickness_chord", 1e-5)),
        },
        "crossports": {
            "enabled": bool(crossports.get("enable_crossports", True)),
            "apply_to": str(crossports.get("apply_to", "all_internal")),
            "centerline_mode": str(crossports.get("centerline_mode", "chordline")),
            "edge_clearance_fraction_local_thickness": float(crossports.get("edge_clearance_fraction_local_thickness", 0.22)),
            "generator": {
                "position_mode": str(crossports.get("position_mode", "standard_3")),
                "count": int(crossports.get("count", 3)),
                "x_start_chord": float(crossports.get("x_start_chord", 0.25)),
                "x_end_chord": float(crossports.get("x_end_chord", 0.70)),
            },
            "holes": crossport_specs(crossports),
        },
    }


def preview_series(root: Path, dto: dict[str, Any]) -> dict[str, list[dict[str, float]]]:
    root = Path(root)
    series: dict[str, list[dict[str, float]]] = {}
    for key in ("base_closed", "active_open"):
        relative = (dto.get("profiles") or {}).get(key)
        if not relative:
            continue
        profile = read_and_canonicalize_profile_2d(
            root / str(relative), "open_ramair" if key == "active_open" else "reference_uncut",
            te_closure_mode=str((dto.get("trailing_edge") or {}).get("mode", "rounded")),
        )
        if profile.errors:
            continue
        contour = profile.closed_contour if key == "base_closed" else profile.open_contour
        series[key] = [
            {"x": float(row.x_norm), "z": float(row.z_norm)} for row in contour.itertuples()
        ]
    return series
