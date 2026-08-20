#!/usr/bin/env python3
"""Content-based RANS-checkpoint and OpenFOAM field integrity checks."""
from __future__ import annotations

import gzip
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable


POLYMESH_FILES = ("points", "faces", "owner", "neighbour", "boundary")
DEFAULT_FIELDS = ("U", "p", "nuTilda")


def poly_mesh_digest(poly_mesh: Path) -> str:
    """Hash only the files that define the OpenFOAM mesh."""
    root = Path(poly_mesh)
    digest = hashlib.sha256()
    for name in POLYMESH_FILES:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"Required polyMesh file is missing: {path}")
        digest.update(name.encode("ascii"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _field_path(time_dir: Path, name: str) -> Path | None:
    direct = Path(time_dir) / name
    compressed = Path(time_dir) / f"{name}.gz"
    return direct if direct.is_file() else compressed if compressed.is_file() else None


def _read_field_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            return stream.read()
    return path.read_text(encoding="utf-8", errors="replace")


def internal_field_count(path: Path) -> int | None:
    """Return the nonuniform internal-field list length; uniform fields return None."""
    text = _read_field_text(Path(path))
    match = re.search(
        r"internalField\s+nonuniform\s+List<[^>]+>\s+(\d+)\s*\(",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return int(match.group(1)) if match else None


def boundary_patch_contract(boundary: Path) -> list[dict[str, str]]:
    text = Path(boundary).read_text(encoding="utf-8", errors="replace")
    rows: list[dict[str, str]] = []
    pattern = re.compile(
        r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\n\s*\{(?P<body>.*?)^\s*\}",
        flags=re.DOTALL | re.MULTILINE,
    )
    for match in pattern.finditer(text):
        type_match = re.search(r"(?m)^\s*type\s+([^;]+);", match.group("body"))
        if type_match:
            rows.append({"name": match.group(1), "type": type_match.group(1).strip()})
    return rows


def checkpoint_mesh_identity(
    checkpoint_case: Path,
    restart_zero: Path,
    *,
    required_fields: Iterable[str] = DEFAULT_FIELDS,
) -> dict[str, Any]:
    """Validate one checkpoint's own mesh and restart fields without registry trust."""
    case = Path(checkpoint_case).resolve()
    zero = Path(restart_zero).resolve()
    poly_mesh = case / "constant/polyMesh"
    missing_mesh = [name for name in POLYMESH_FILES if not (poly_mesh / name).is_file()]
    fields: dict[str, dict[str, Any]] = {}
    missing_fields: list[str] = []
    nonuniform_counts: dict[str, int] = {}
    for name in required_fields:
        path = _field_path(zero, str(name))
        if path is None:
            missing_fields.append(str(name))
            continue
        count = internal_field_count(path)
        fields[str(name)] = {"path": str(path), "internal_count": count}
        if count is not None:
            nonuniform_counts[str(name)] = count
    unique_counts = sorted(set(nonuniform_counts.values()))
    cell_count = unique_counts[0] if len(unique_counts) == 1 else None
    count_mismatch = len(unique_counts) > 1
    digest = None if missing_mesh else poly_mesh_digest(poly_mesh)
    patches = (
        boundary_patch_contract(poly_mesh / "boundary")
        if not missing_mesh
        else []
    )
    front_and_back_empty = any(
        row["name"] == "frontAndBack" and row["type"].lower() == "empty"
        for row in patches
    )
    return {
        "status": (
            "READY"
            if not missing_mesh and not missing_fields and not count_mismatch and front_and_back_empty
            else "INVALID"
        ),
        "mesh_source": "RANS_CHECKPOINT",
        "checkpoint_case": str(case),
        "restart_zero": str(zero),
        "poly_mesh": str(poly_mesh),
        "poly_mesh_hash": digest,
        "cell_count": cell_count,
        "field_counts": nonuniform_counts,
        "fields": fields,
        "patches": patches,
        "frontAndBack_empty": front_and_back_empty,
        "missing_mesh_files": missing_mesh,
        "missing_fields": missing_fields,
        "field_count_mismatch": count_mismatch,
    }


def copied_checkpoint_matches(source_identity: dict[str, Any], target_case: Path) -> dict[str, Any]:
    target_poly = Path(target_case).resolve() / "constant/polyMesh"
    target_hash = poly_mesh_digest(target_poly)
    expected = str(source_identity.get("poly_mesh_hash") or "")
    return {
        "status": "MATCH" if expected and target_hash == expected else "MISMATCH",
        "source_poly_mesh_hash": expected,
        "target_poly_mesh_hash": target_hash,
        "target_poly_mesh": str(target_poly),
        "patches": boundary_patch_contract(target_poly / "boundary"),
    }
