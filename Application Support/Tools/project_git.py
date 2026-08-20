#!/usr/bin/env python3
"""Conservative Git maintenance commands used by the application UI."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "configure", "snapshot", "pull", "push"))
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
        return 0
    if args.action == "pull":
        require_remote()
        if not clean():
            raise SystemExit("Pull refused: create a snapshot or discard local changes first.")
        run("pull", "--ff-only")
        return 0
    if args.action == "push":
        require_remote()
        if not clean():
            raise SystemExit("Push refused: create a snapshot first.")
        run("push")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
