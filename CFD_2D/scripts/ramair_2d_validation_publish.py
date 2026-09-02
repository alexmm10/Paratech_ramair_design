#!/usr/bin/env python3
"""Publish explicitly selected, eligible CFD angles to the active validation work case."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from ramair_2d_validation import (
    remove_active_workspace_validation,
    update_active_workspace_validation,
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except (csv.Error, OSError):
        return []


def _result_status(output: Path | None, alpha: float, action: str) -> tuple[str, str | None]:
    if output is None:
        return "NOT_APPLIED", "No active compatible validation workspace or variant."
    published = _read_rows(output / "ramair_validation_points.csv")
    is_published = any(
        abs(float(row.get("alpha_deg", "nan")) - float(alpha)) <= 1.0e-9
        for row in published
        if row.get("alpha_deg") not in (None, "")
    )
    if action == "remove":
        return (
            ("REMOVE_FAILED", "The angle remains in ramair_validation_points.csv")
            if is_published
            else ("UNPUBLISHED", None)
        )
    if is_published:
        return "PUBLISHED", None
    ignored = _read_rows(output / "ignored_nonmatching_results.csv")
    matching = []
    for row in ignored:
        try:
            if abs(float(row.get("alpha_deg", "nan")) - float(alpha)) <= 1.0e-9:
                matching.append(row)
        except (TypeError, ValueError):
            continue
    reason = str((matching[-1] if matching else {}).get("reason") or "Result was not eligible or was not found.")
    return "NOT_PUBLISHED", reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--alphas", type=float, nargs="+", required=True)
    parser.add_argument("--action", choices=["add", "remove"], default="add")
    parser.add_argument(
        "--allow-incomplete", action="store_true",
        help="Publish an available mean despite an unfinished staged workflow.",
    )
    args = parser.parse_args()
    rows = []
    for alpha in args.alphas:
        operation = (
            update_active_workspace_validation
            if args.action == "add"
            else remove_active_workspace_validation
        )
        output = (
            operation(
                args.case_root, args.variant, float(alpha),
                allow_incomplete=bool(args.allow_incomplete),
            )
            if args.action == "add"
            else operation(args.case_root, args.variant, float(alpha))
        )
        status, reason = _result_status(output, float(alpha), args.action)
        rows.append({
            "alpha_deg": float(alpha),
            "status": status,
            "reason": reason,
            "validation_dir": str(output) if output is not None else None,
        })
    report = {"status": "FINISHED", "action": args.action, "variant": args.variant, "rows": rows}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
