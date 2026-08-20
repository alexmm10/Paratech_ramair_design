#!/usr/bin/env python3
"""Readers for segmented OpenFOAM histories, including restarted cases."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def force_coefficient_files(case_dir: Path, include_processor0: bool = True) -> list[Path]:
    patterns = [
        "postProcessing/forceCoeffs*/**/forceCoeffs.dat",
        "postProcessing/forceCoeffs*/**/coefficient.dat",
    ]
    if include_processor0:
        patterns += [
            "processor0/postProcessing/forceCoeffs*/**/forceCoeffs.dat",
            "processor0/postProcessing/forceCoeffs*/**/coefficient.dat",
        ]
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in Path(case_dir).glob(pattern):
            if path.is_file():
                found[str(path.resolve())] = path
    return sorted(found.values(), key=lambda path: (path.stat().st_mtime, str(path)))


def _read_segment(path: Path) -> tuple[list[str], list[list[float]]]:
    header: list[str] | None = None
    rows: list[list[float]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            candidate = line.lstrip("# ").split()
            if "Time" in candidate and "Cd" in candidate and "Cl" in candidate:
                header = candidate
            continue
        try:
            row = [float(value) for value in line.split()]
        except ValueError:
            continue
        if all(math.isfinite(value) for value in row):
            rows.append(row)
    if not rows or not header or len(header) != len(rows[-1]):
        return [], []
    return header, [row for row in rows if len(row) == len(header)]


def _segment_header(path: Path, max_bytes: int = 16_384) -> list[str]:
    """Read the OpenFOAM column header without loading a growing data file."""
    with path.open("rb") as handle:
        text = handle.read(max_bytes).decode("utf-8", errors="ignore")
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        candidate = line.lstrip("# ").split()
        if "Time" in candidate and "Cd" in candidate and "Cl" in candidate:
            return candidate
    return []


def _read_segment_tail(path: Path, max_rows: int) -> tuple[list[str], list[list[float]]]:
    """Read only recent finite rows from a forceCoeffs segment."""
    header = _segment_header(path)
    if not header:
        return [], []
    # forceCoeffs rows are short. This cap keeps live monitoring bounded even
    # after long runs while leaving ample room for the requested sample count.
    max_bytes = max(65_536, min(8_000_000, int(max_rows) * 320))
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > max_bytes:
            handle.seek(-max_bytes, 2)
            handle.readline()
        text = handle.read().decode("utf-8", errors="ignore")
    rows: list[list[float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = [float(value) for value in line.split()]
        except ValueError:
            continue
        if len(row) == len(header) and all(math.isfinite(value) for value in row):
            rows.append(row)
    return header, rows[-max(1, int(max_rows)):]


def read_force_coefficient_history(
    case_dir: Path,
    *,
    include_processor0: bool = True,
) -> tuple[list[dict[str, float]], list[str]]:
    """Aggregate all restart segments and keep the newest value at duplicate times."""
    records_by_time: dict[float, dict[str, float]] = {}
    sources: list[str] = []
    for path in force_coefficient_files(case_dir, include_processor0=include_processor0):
        header, rows = _read_segment(path)
        if not rows:
            continue
        sources.append(str(path))
        columns = {name: index for index, name in enumerate(header)}
        if not {"Time", "Cl", "Cd"}.issubset(columns):
            continue
        cm_name = "Cm" if "Cm" in columns else ("CmPitch" if "CmPitch" in columns else None)
        for row in rows:
            record = {
                "Time": row[columns["Time"]],
                "Cl": row[columns["Cl"]],
                "Cd": row[columns["Cd"]],
            }
            if cm_name is not None:
                record["Cm"] = row[columns[cm_name]]
            # Files are ordered by modification time, so a restarted segment
            # intentionally replaces an older overlapping sample.
            records_by_time[record["Time"]] = record
    return [records_by_time[key] for key in sorted(records_by_time)], sources


def read_recent_force_coefficient_history(
    case_dir: Path,
    *,
    max_rows: int = 1500,
    include_processor0: bool = True,
) -> tuple[list[dict[str, float]], list[str]]:
    """Read a bounded recent history for live monitors and safety checks.

    Full post-processing still uses :func:`read_force_coefficient_history`.
    This reader walks newest restart segments first and avoids repeatedly
    parsing tens of thousands of historical samples while a solver is active.
    """
    limit = max(1, int(max_rows))
    records_by_time: dict[float, dict[str, float]] = {}
    sources: list[str] = []
    files = force_coefficient_files(case_dir, include_processor0=include_processor0)
    for path in reversed(files):
        header, rows = _read_segment_tail(path, limit)
        if not rows:
            continue
        sources.append(str(path))
        columns = {name: index for index, name in enumerate(header)}
        if not {"Time", "Cl", "Cd"}.issubset(columns):
            continue
        cm_name = "Cm" if "Cm" in columns else ("CmPitch" if "CmPitch" in columns else None)
        for row in reversed(rows):
            time_value = row[columns["Time"]]
            if time_value in records_by_time:
                continue
            record = {
                "Time": time_value,
                "Cl": row[columns["Cl"]],
                "Cd": row[columns["Cd"]],
            }
            if cm_name is not None:
                record["Cm"] = row[columns[cm_name]]
            records_by_time[time_value] = record
        if len(records_by_time) >= limit:
            break
    selected_times = sorted(records_by_time)[-limit:]
    return [records_by_time[key] for key in selected_times], list(reversed(sources))


def latest_force_coefficients(case_dir: Path) -> dict[str, Any] | None:
    records, sources = read_force_coefficient_history(case_dir)
    if not records:
        return None
    return {**records[-1], "sources": sources}
