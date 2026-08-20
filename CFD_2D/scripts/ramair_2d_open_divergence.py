#!/usr/bin/env python3
"""Classify fixed-geometry Open RANS diagnostics before URANS expansion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import utc_stamp, write_json_atomic


REQUIRED_DIAGNOSTICS = (
    "stagnation",
    "lip_separation",
    "reattachment",
    "internal_pressure",
    "recirculation",
    "wake",
)


def classify_open_rans_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    levels = {str(row.get("mesh_level")): row for row in rows}
    missing_levels = [level for level in ("coarse", "medium", "fine") if level not in levels]
    geometry_revisions = {
        str(row.get("geometry_revision")) for row in rows if row.get("geometry_revision")
    }
    missing_diagnostics = {
        level: [name for name in REQUIRED_DIAGNOSTICS if not row.get(name)]
        for level, row in levels.items()
    }
    missing_diagnostics = {
        level: values for level, values in missing_diagnostics.items() if values
    }
    divergent = [
        level for level, row in levels.items()
        if bool(row.get("diverged")) or bool(row.get("inlet_backflow_unbounded"))
    ]
    reasons: list[str] = []
    if missing_levels:
        reasons.append("MISSING_RANS_MESH_LEVELS")
    if len(geometry_revisions) != 1:
        reasons.append("GEOMETRY_REVISION_NOT_FIXED")
    if missing_diagnostics:
        reasons.append("INCOMPLETE_PHYSICAL_DIAGNOSTICS")
    if divergent:
        reasons.append("OPEN_DIVERGENCE_REVIEW_REQUIRED")
    return {
        "schema_version": 1,
        "topology": "open",
        "generated_at": utc_stamp(),
        "geometry_fixed": len(geometry_revisions) == 1,
        "geometry_revisions": sorted(geometry_revisions),
        "missing_levels": missing_levels,
        "missing_diagnostics": missing_diagnostics,
        "divergent_levels": divergent,
        "decision": (
            "PROCEED_MEDIUM_TEMPORAL"
            if not reasons else "HOLD_FOR_RANS_DIAGNOSTIC_REVIEW"
        ),
        "reasons": reasons,
        "prohibited_automatic_actions": [
            "change_geometry",
            "move_inlet",
            "replace_active_mesh",
            "launch_urans_matrix",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8-sig"))
    report = classify_open_rans_diagnostics(rows)
    write_json_atomic(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
