#!/usr/bin/env python3
"""Open a Linux/WSL mesh with the Windows Gmsh Python API GUI."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", nargs="?", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--log", type=Path, default=None)
    return parser.parse_args()


def append_log(path: Path | None, message: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def main() -> int:
    args = parse_args()
    try:
        import gmsh
        if args.probe:
            print(json.dumps({"status": "OK", "gmsh_version": gmsh.__version__, "python": sys.executable}))
            return 0
        if args.mesh is None or not args.mesh.is_file():
            raise FileNotFoundError(f"Mesh does not exist: {args.mesh}")
        append_log(args.log, f"Windows Gmsh {gmsh.__version__}; mesh={args.mesh}")
        gmsh.initialize(sys.argv[:1])
        try:
            gmsh.open(str(args.mesh.resolve()))
            gmsh.fltk.run()
        finally:
            gmsh.finalize()
        append_log(args.log, "Windows Gmsh viewer closed normally")
        return 0
    except Exception as exc:
        append_log(args.log, f"ERROR {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
