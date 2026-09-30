"""Shared source identity for application, workspace and remote packages."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path


def release_identity(root: Path) -> dict:
    metadata = root / "RELEASE.json"
    result = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else {}
    if (root / ".git").exists():
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        result["commit"] = completed.stdout.strip()
        result["working_tree_modified"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True).stdout.strip())
    else:
        # A synchronized runtime may originate from an older portable ZIP.
        # Its current deployment identity supersedes that initial manifest.
        for name in ("CFD_2D/app_state/runtime_sync_manifest.json", "PORTABLE_PACKAGE_MANIFEST.json", "RELEASE_IDENTITY.json"):
            path = root / name
            if path.is_file():
                saved = json.loads(path.read_text(encoding="utf-8"))
                result.update(saved.get("release", saved))
                break
    return result
