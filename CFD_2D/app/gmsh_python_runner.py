#!/usr/bin/env python3
"""Isolated Gmsh Python API worker.

The worker deliberately runs in a separate process.  A malformed geometry or a
native Gmsh failure can then be timed out by the mesh builder without taking the
graphical application down with it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a mesh through the Gmsh Python API.")
    parser.add_argument("--geo", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dimension", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--msh-version", type=float, default=2.2)
    parser.add_argument("--binary", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--version-only", action="store_true")
    return parser.parse_args()


def write_report(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    report: dict[str, Any] = {
        "backend": "python_api",
        "dimension": int(args.dimension),
        "threads": max(1, int(args.threads)),
    }
    try:
        import gmsh  # type: ignore
    except Exception as exc:
        report.update(status="MISSING", error=f"Could not import gmsh: {exc}")
        write_report(args.report, report)
        print(report["error"], file=sys.stderr)
        return 2

    report["gmsh_version"] = str(getattr(gmsh, "__version__", "unknown"))
    if args.version_only:
        print(report["gmsh_version"])
        write_report(args.report, report | {"status": "OK"})
        return 0
    if args.geo is None or args.output is None:
        print("--geo and --output are required unless --version-only is used", file=sys.stderr)
        return 2
    if not args.geo.is_file():
        print(f"Gmsh input does not exist: {args.geo}", file=sys.stderr)
        return 2

    initialized = False
    logger_started = False
    try:
        gmsh.initialize(["ramair-gmsh-python", "-nt", str(report["threads"])], interruptible=True)
        initialized = True
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.option.setNumber("General.NumThreads", report["threads"])
        gmsh.option.setNumber("Mesh.MaxNumThreads1D", report["threads"])
        gmsh.option.setNumber("Mesh.MaxNumThreads2D", report["threads"])
        gmsh.option.setNumber("Mesh.MaxNumThreads3D", report["threads"])
        gmsh.option.setNumber("Mesh.MshFileVersion", float(args.msh_version))
        gmsh.option.setNumber("Mesh.Binary", 1 if args.binary else 0)
        gmsh.logger.start()
        logger_started = True
        gmsh.open(str(args.geo.resolve()))
        gmsh.model.mesh.generate(int(args.dimension))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(args.output.resolve()))
        node_tags, _, _ = gmsh.model.mesh.getNodes()
        element_types, element_tags, _ = gmsh.model.mesh.getElements()
        report.update(
            status="OK",
            nodes=int(len(node_tags)),
            elements=int(sum(len(tags) for tags in element_tags)),
            element_types=[int(value) for value in element_types],
            physical_groups=int(len(gmsh.model.getPhysicalGroups())),
            output=str(args.output.resolve()),
            output_size_bytes=int(args.output.stat().st_size) if args.output.exists() else 0,
        )
        return_code = 0
    except Exception as exc:
        report.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        print(f"Gmsh Python API failed: {report['error']}", file=sys.stderr)
        return_code = 1
    finally:
        if initialized:
            if logger_started:
                try:
                    for line in gmsh.logger.get():
                        print(line)
                    gmsh.logger.stop()
                except Exception:
                    pass
            try:
                gmsh.finalize()
            except Exception:
                pass
        report["wall_time_s"] = float(time.perf_counter() - started)
        write_report(args.report, report)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

