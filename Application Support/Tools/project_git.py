#!/usr/bin/env python3
"""Conservative Git maintenance commands used by the application UI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUSH_PREVIEW = ROOT / ".git/ramair_push_preview.json"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    print(completed.stdout, end="")
    if check and completed.returncode:
        raise SystemExit(completed.returncode)
    return completed


def clean() -> bool:
    return not run("status", "--porcelain", check=False).stdout.strip()


def configured_value(*args: str) -> str:
    return run("config", "--get", *args, check=False).stdout.strip()


def require_identity() -> None:
    missing = [
        key for key in ("user.name", "user.email") if not configured_value(key)
    ]
    if missing:
        raise SystemExit(
            "Git author is not configured for this project. In the Environment "
            "page, open 'Configurar Git' and enter author name and email before "
            "creating the first snapshot. Missing: " + ", ".join(missing)
        )


def require_remote() -> None:
    if not run("remote", "get-url", "origin", check=False).stdout.strip():
        raise SystemExit(
            "Git remote 'origin' is not configured. Create an empty private "
            "repository, then save its clone URL in Environment > Configurar Git."
        )


def repository_identity() -> dict[str, str]:
    return {
        "head": run("rev-parse", "HEAD", check=False).stdout.strip(),
        "branch": run("branch", "--show-current", check=False).stdout.strip(),
        "remote": run("remote", "get-url", "origin", check=False).stdout.strip(),
    }


def invalidate_push_preview() -> None:
    PUSH_PREVIEW.unlink(missing_ok=True)


def preview_push() -> None:
    require_remote()
    if not clean():
        raise SystemExit("Push preview refused: create a snapshot first.")
    identity = repository_identity()
    if not identity["branch"]:
        raise SystemExit("Push preview refused: detached HEAD is not publishable.")
    print("Commits that would be published:")
    run("log", "--oneline", f"origin/{identity['branch']}..HEAD", check=False)
    run("push", "--dry-run", "--set-upstream", "origin", identity["branch"])
    PUSH_PREVIEW.write_text(
        json.dumps(identity, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Push preview recorded. Publishing is allowed only while HEAD, branch and origin remain unchanged.")


def require_current_push_preview() -> dict[str, str]:
    if not PUSH_PREVIEW.is_file():
        raise SystemExit("Push refused: run 'preview-push' first and review its output.")
    preview = json.loads(PUSH_PREVIEW.read_text(encoding="utf-8"))
    current = repository_identity()
    if preview != current:
        invalidate_push_preview()
        raise SystemExit(
            "Push refused: HEAD, branch or origin changed after the preview. "
            "Run 'preview-push' again."
        )
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("status", "configure", "snapshot", "pull", "preview-push", "push"),
    )
    parser.add_argument("--message", default="RamAir DESIGN APP snapshot")
    parser.add_argument("--name")
    parser.add_argument("--email")
    parser.add_argument("--remote")
    args = parser.parse_args()
    if args.action == "status":
        run("status", "--short", "--branch")
        run("remote", "-v", check=False)
        print(f"author.name={configured_value('user.name') or 'NOT_CONFIGURED'}")
        print(f"author.email={configured_value('user.email') or 'NOT_CONFIGURED'}")
        print(f"push.preview={'READY' if PUSH_PREVIEW.is_file() else 'REQUIRED'}")
        return 0
    if args.action == "configure":
        if not args.name or not args.email:
            raise SystemExit("Both --name and --email are required for local Git authorship.")
        run("config", "user.name", args.name)
        run("config", "user.email", args.email)
        if args.remote:
            current = run("remote", "get-url", "origin", check=False).stdout.strip()
            if current:
                run("remote", "set-url", "origin", args.remote)
            else:
                run("remote", "add", "origin", args.remote)
        invalidate_push_preview()
        print("Local Git identity and optional origin remote saved for this project.")
        return 0
    if args.action == "snapshot":
        require_identity()
        audit = ROOT / "Application Support/Tools/check_repository_artifacts.py"
        subprocess.run([sys.executable, str(audit)], cwd=ROOT, check=True)
        run("add", "--all")
        if clean():
            print("No source changes to commit.")
            return 0
        run("commit", "-m", args.message)
        invalidate_push_preview()
        return 0
    if args.action == "pull":
        require_remote()
        if not clean():
            raise SystemExit("Pull refused: create a snapshot or discard local changes first.")
        run("pull", "--ff-only")
        invalidate_push_preview()
        return 0
    if args.action == "preview-push":
        preview_push()
        return 0
    if args.action == "push":
        require_remote()
        if not clean():
            raise SystemExit("Push refused: create a snapshot first.")
        identity = require_current_push_preview()
        run("push", "--set-upstream", "origin", identity["branch"])
        invalidate_push_preview()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
