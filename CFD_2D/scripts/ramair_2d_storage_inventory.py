#!/usr/bin/env python3
"""Storage inventory and bounded cleanup for the active validation workspace."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from ramair_2d_study_registry import (
    active_workspace_root,
    read_json,
    utc_stamp,
    write_json_atomic,
)


VOLUMETRIC_FOLDER_NAMES = {"VTK", "postProcessingVTK"}
ANIMATION_SUFFIXES = {".gif", ".mp4", ".avi", ".webm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _file_inventory(root: Path) -> Iterable[tuple[Path, int]]:
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                yield path, path.stat().st_size
            except OSError:
                continue


def _folder_bytes(root: Path) -> dict[str, int]:
    totals: dict[str, int] = {}
    for path, size in _file_inventory(root):
        relative = path.relative_to(root)
        top = relative.parts[0] if relative.parts else "."
        totals[top] = totals.get(top, 0) + size
    return totals


def generate_storage_inventory(
    project_root: Path,
    *,
    top_file_count: int = 50,
) -> dict[str, Any]:
    active = active_workspace_root(Path(project_root).resolve())
    active.mkdir(parents=True, exist_ok=True)
    files = list(_file_inventory(active))
    folder_totals = _folder_bytes(active)
    top_files = sorted(files, key=lambda item: item[1], reverse=True)[
        : max(1, int(top_file_count))
    ]
    snapshot_dirs = {
        str(path.parent)
        for path, _ in files
        if path.parent.name.replace(".", "", 1).isdigit()
    }
    vtk_files = [(path, size) for path, size in files if "VTK" in path.parts]
    animations = [
        (path, size) for path, size in files if path.suffix.lower() in ANIMATION_SUFFIXES
    ]
    images = [
        (path, size) for path, size in files if path.suffix.lower() in IMAGE_SUFFIXES
    ]
    processors = [
        path
        for path in active.rglob("processor*")
        if path.is_dir() and path.name.removeprefix("processor").isdigit()
    ]
    recommendations: list[str] = []
    if vtk_files:
        recommendations.append(
            "Remove duplicated VTK only for a selected active run after confirming "
            "the OpenFOAM time directories are retained."
        )
    if animations:
        recommendations.append(
            "Animations are on-demand products; keep only selected publication outputs."
        )
    if processors:
        recommendations.append(
            "Processor directories may be removed only after the retained time is reconstructed."
        )
    inventory = {
        "schema_version": 1,
        "workspace": str(active),
        "generated_at": utc_stamp(),
        "total_bytes": sum(size for _, size in files),
        "free_bytes": shutil.disk_usage(active).free,
        "folder_bytes": [
            {"folder": name, "bytes": size}
            for name, size in sorted(
                folder_totals.items(), key=lambda item: item[1], reverse=True
            )
        ],
        "top_files": [
            {"path": str(path), "bytes": size} for path, size in top_files
        ],
        "snapshot_directories": len(snapshot_dirs),
        "vtk_files": len(vtk_files),
        "vtk_bytes": sum(size for _, size in vtk_files),
        "animation_files": len(animations),
        "animation_bytes": sum(size for _, size in animations),
        "image_files": len(images),
        "image_bytes": sum(size for _, size in images),
        "processor_directories": len(processors),
        "recommendations": recommendations,
    }
    reports = active / "postprocess/reports"
    json_path = reports / "storage_inventory.json"
    csv_path = reports / "storage_inventory.csv"
    write_json_atomic(json_path, inventory)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("category", "path", "bytes"))
        writer.writeheader()
        for row in inventory["folder_bytes"]:
            writer.writerow(
                {"category": "folder", "path": row["folder"], "bytes": row["bytes"]}
            )
        for row in inventory["top_files"]:
            writer.writerow(
                {"category": "top_file", "path": row["path"], "bytes": row["bytes"]}
            )
    return inventory


def _numeric_time_directories(case: Path) -> list[tuple[float, Path]]:
    result: list[tuple[float, Path]] = []
    for path in case.iterdir() if case.is_dir() else ():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0:
            result.append((value, path))
    return sorted(result)


def _has_restart_fields(path: Path) -> bool:
    return any((path / name).is_file() for name in ("U", "U.gz")) and any(
        (path / name).is_file() for name in ("p", "p.gz")
    )


def clean_active_volumetric_products(
    project_root: Path,
    *,
    confirm: bool,
) -> dict[str, Any]:
    """Remove only regenerable active volume products; Results is never touched."""
    if not confirm:
        raise ValueError("Explicit confirmation is required for active cleanup")
    project_root = Path(project_root).resolve()
    active = active_workspace_root(project_root).resolve()
    results = (project_root / "Results").resolve()
    if active == results or results in active.parents:
        raise RuntimeError("Refusing to clean a Results path")
    removed: list[dict[str, Any]] = []
    preserved: list[str] = []
    case_roots = {
        path.parent.parent.parent
        for path in active.rglob("constant/polyMesh/boundary")
        if path.is_file()
    }
    for case in sorted(case_roots):
        if not all((case / name).is_dir() for name in ("0", "constant", "system")):
            continue
        times = _numeric_time_directories(case)
        latest = times[-1][1] if times and _has_restart_fields(times[-1][1]) else None
        preserved.extend(str(case / name) for name in ("0", "constant", "system"))
        if latest is not None:
            preserved.append(str(latest))
            for _, path in times[:-1]:
                size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
                shutil.rmtree(path)
                removed.append({"path": str(path), "bytes": size, "reason": "old_volume_state"})
        for folder_name in VOLUMETRIC_FOLDER_NAMES:
            folder = case / folder_name
            if folder.is_dir():
                size = sum(item.stat().st_size for item in folder.rglob("*") if item.is_file())
                shutil.rmtree(folder)
                removed.append({"path": str(folder), "bytes": size, "reason": "regenerable_vtk"})
        if latest is not None:
            for processor in case.glob("processor*"):
                if processor.is_dir() and processor.name.removeprefix("processor").isdigit():
                    size = sum(
                        item.stat().st_size
                        for item in processor.rglob("*")
                        if item.is_file()
                    )
                    shutil.rmtree(processor)
                    removed.append(
                        {
                            "path": str(processor),
                            "bytes": size,
                            "reason": "reconstructed_processor_duplicate",
                        }
                    )
        for path in case.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in ANIMATION_SUFFIXES:
                size = path.stat().st_size
                path.unlink()
                removed.append(
                    {"path": str(path), "bytes": size, "reason": "on_demand_animation"}
                )
    report = {
        "schema_version": 1,
        "status": "COMPLETED",
        "workspace": str(active),
        "results_untouched": True,
        "removed_bytes": sum(row["bytes"] for row in removed),
        "removed": removed,
        "preserved": preserved,
        "finished_at": utc_stamp(),
    }
    write_json_atomic(
        active / "postprocess/reports/storage_cleanup_last.json",
        report,
    )
    generate_storage_inventory(project_root)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("inventory")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "inventory":
        result = generate_storage_inventory(args.project_root)
    else:
        result = clean_active_volumetric_products(
            args.project_root, confirm=args.confirm
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
