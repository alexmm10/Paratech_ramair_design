#!/usr/bin/env python3
"""Publish explicitly selected, eligible CFD angles to the active validation work case."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ramair_2d_validation import (
    remove_active_workspace_validation,
    update_active_workspace_validation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--alphas", type=float, nargs="+", required=True)
    parser.add_argument("--action", choices=["add", "remove"], default="add")
    args = parser.parse_args()
    rows = []
    for alpha in args.alphas:
        operation = (
            update_active_workspace_validation
            if args.action == "add"
            else remove_active_workspace_validation
        )
        output = operation(args.case_root, args.variant, float(alpha))
        rows.append({
            "alpha_deg": float(alpha),
            "status": (
                "PUBLISHED_OR_AUDITED" if args.action == "add"
                else "UNPUBLISHED"
            ) if output is not None else "NO_ACTIVE_VALIDATION_WORKSPACE",
            "validation_dir": str(output) if output is not None else None,
        })
    report = {"status": "FINISHED", "action": args.action, "variant": args.variant, "rows": rows}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
