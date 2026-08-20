#!/usr/bin/env python3
"""Check that Codex context and release metadata match the application API."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / "PROJECT_CONTEXT_FOR_CODEX.md"
AGENTS = ROOT / "AGENTS.md"
CHANGELOG = ROOT / "CHANGELOG.md"
APP = ROOT / "CFD_2D/app/ramair_cfd2d_app.py"


def require(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"MISSING: {path}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    context = require(CONTEXT)
    agents = require(AGENTS)
    changelog = require(CHANGELOG)
    app = require(APP)
    match = re.search(r"EXPECTED_BACKEND_API_VERSION\s*=\s*(\d+)", app)
    if not match:
        raise RuntimeError("Cannot determine EXPECTED_BACKEND_API_VERSION from the app.")
    api = match.group(1)
    date_match = re.search(r"Context version:\s*(\d{4}-\d{2}-\d{2})", context)
    context_date = date_match.group(1) if date_match else ""
    checks = {
        "context_api": f"Application backend API: {api}" in context,
        "context_version_date": bool(context_date),
        "agents_requires_context": "PROJECT_CONTEXT_FOR_CODEX.md" in agents,
        "agents_requires_changelog": "CHANGELOG.md" in agents,
        "changelog_unreleased": "## [Unreleased]" in changelog,
        "changelog_context_release": bool(context_date and f"## [{context_date}]" in changelog),
    }
    failed = [name for name, passed in checks.items() if not passed]
    for name, passed in checks.items():
        print(f"{'OK' if passed else 'MISSING'}: {name}")
    if failed:
        raise RuntimeError("Project context is stale or incomplete: " + ", ".join(failed))
    print(f"OK: project context {context_date} matches application API {api} and changelog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
