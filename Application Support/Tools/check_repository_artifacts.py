#!/usr/bin/env python3
"""Fail when Git would include generated or unexpectedly large artifacts."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 10 * 1024 * 1024
DENIED_PARTS = {
    "Results", "Previous Versions", "meshes", "openfoam_cases",
    "validation_studies", "app_state", "postProcessing", "processor0",
}


def main() -> int:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        check=True,
    )
    problems: list[str] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8", errors="surrogateescape"))
        path = ROOT / relative
        if any(part in DENIED_PARTS or part.startswith("processor") for part in relative.parts):
            problems.append(f"generated path: {relative}")
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            problems.append(f"large file ({path.stat().st_size / 1048576:.1f} MB): {relative}")
    if problems:
        print("Repository artifact audit FAILED")
        print("\n".join(f"- {problem}" for problem in problems))
        return 1
    print("Repository artifact audit PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
