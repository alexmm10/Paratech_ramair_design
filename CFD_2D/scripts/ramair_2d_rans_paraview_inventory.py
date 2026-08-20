#!/usr/bin/env python3
"""Resolve iteration-consistent ParaView artifact sets for six RANS bases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ramair_2d_rans_paraview_final import resolve_final_vtk_artifacts
from ramair_2d_study_registry import active_workspace_root, utc_stamp, write_json_atomic


MESH_IDS = (
    "closed_coarse", "closed_medium", "closed_fine",
    "open_coarse", "open_medium", "open_fine",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generate-missing", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=900)
    args = parser.parse_args()
    active = active_workspace_root(args.project_root.resolve())
    rows = []
    for mesh_id in MESH_IDS:
        case = active / "checkpoints" / mesh_id / "case"
        resolved = resolve_final_vtk_artifacts(
            case,
            generate_if_missing=bool(args.generate_missing),
            timeout_s=min(900, max(60, int(args.timeout_s))),
        )
        rows.append({"mesh_id": mesh_id, **resolved})
    report = {
        "schema_version": 1,
        "status": "READY" if all(row.get("status") == "READY" for row in rows) else "PARTIAL",
        "generation_policy": "latestTime only",
        "rows": rows,
        "generated_at": utc_stamp(),
    }
    write_json_atomic(args.output.resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
