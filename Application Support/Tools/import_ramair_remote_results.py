#!/usr/bin/env python3
"""Validate and import a RamAir remote-return archive into its canonical cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Unsafe archive member: {member.filename}")
    archive.extractall(destination)


def read_manifest(root: Path) -> dict[str, Any]:
    path = root / "return_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or int(value.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported or missing remote-return manifest")
    return value


def verify_files(root: Path, manifest: dict[str, Any]) -> None:
    for relative, expected in (manifest.get("files") or {}).items():
        path = root / str(relative)
        if not path.is_file():
            raise FileNotFoundError(f"Remote result is incomplete: {relative}")
        expected_bytes = expected.get("bytes", -1)
        if int(path.stat().st_size) != int(expected_bytes):
            raise ValueError(f"Remote result size mismatch: {relative}")
        if sha256(path) != str(expected.get("sha256") or ""):
            raise ValueError(f"Remote result checksum mismatch: {relative}")


def monitor_recovery_evidence(case: Path) -> dict[str, Any]:
    """Describe the raw evidence from which app monitors are rebuilt."""
    logs = sorted({
        str(path.relative_to(case))
        for pattern in ("log.foamRun", "log.*Foam*", "PyFoamRunner*.logfile")
        for path in case.glob(pattern)
        if path.is_file()
    })
    force_files = sorted(
        str(path.relative_to(case))
        for path in case.glob("postProcessing/forceCoeffs/**/forceCoeffs.dat")
        if path.is_file()
    )
    statuses = sorted(
        name for name in ("run_status.json", "staged_run_status.json")
        if (case / name).is_file()
    )
    steady_archives = sorted(
        str(path.relative_to(case))
        for path in case.glob("steadyInitialization/history/run_*")
        if path.is_dir()
    )
    numeric_times = []
    for path in case.iterdir():
        if not path.is_dir():
            continue
        try:
            numeric_times.append(float(path.name))
        except ValueError:
            pass
    ready = bool(logs or force_files or statuses or steady_archives)
    return {
        "status": "READY" if ready else "NO_EXECUTION_EVIDENCE",
        "cache_policy": "monitor caches are regenerated from raw logs and postProcessing data",
        "logs": logs,
        "force_coefficient_histories": force_files,
        "lifecycle_status_files": statuses,
        "steady_archives": steady_archives,
        "reconstructed_times": sorted(numeric_times),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--existing-action",
        choices=("archive", "merge", "refuse"),
        default="archive",
        help="How to handle an existing canonical case directory.",
    )
    args = parser.parse_args()
    project = args.project_root.resolve()
    archive_path = args.archive.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    imported: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ramair_remote_import_") as temporary:
        extraction = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            safe_extract(archive, extraction)
        root = extraction / "RAMAir_REMOTE_RETURN"
        manifest = read_manifest(root)
        verify_files(root, manifest)
        payload = root / "payload"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        for row in manifest.get("cases") or []:
            relative = Path(str(row.get("case") or ""))
            source = payload / relative
            if not source.is_dir() or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid imported case path: {relative}")
            destination = project / relative
            archived = None
            if destination.exists() and args.existing_action == "refuse":
                raise FileExistsError(destination)
            if destination.exists() and args.existing_action == "archive":
                archive_root = project / "CFD_2D/openfoam_cases/_remote_import_archive" / timestamp
                archived = archive_root / relative.parent.name / relative.name
                archived.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(archived))
            shutil.copytree(source, destination, dirs_exist_ok=args.existing_action == "merge")
            monitor_recovery = monitor_recovery_evidence(destination)
            evidence = {
                "schema_version": 1,
                "package_id": manifest.get("package_id"),
                "package_scope": manifest.get("package_scope"),
                "source_archive": str(archive_path),
                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "remote_status": row.get("status"),
                "archived_previous_case": str(archived) if archived else None,
                "monitor_recovery": monitor_recovery,
            }
            (destination / "remote_import_evidence.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )
            imported.append({
                **row,
                "destination": str(destination),
                "archived": str(archived) if archived else None,
                "monitor_recovery": monitor_recovery,
            })
    report = {
        "status": "IMPORTED",
        "package_id": manifest.get("package_id"),
        "archive": str(archive_path),
        "cases": imported,
        "imported_at": time.time(),
    }
    report_root = project / "CFD_2D/remote_imports"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{manifest.get('package_id') or 'remote'}_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
