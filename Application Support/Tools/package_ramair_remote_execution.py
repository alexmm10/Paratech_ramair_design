#!/usr/bin/env python3
"""Create a checked, restartable OpenFOAM execution package for a Linux/WSL server."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


REMOTE_REQUIREMENTS = ["numpy", "pandas", "matplotlib", "scipy", "pillow", "psutil", "PyFoam"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def case_identity(case: Path) -> tuple[str, float]:
    summary = read_json(case / "case_input_summary.json") or read_json(case / "case_config.json")
    variant = str(summary.get("variant") or case.parent.name)
    alpha = summary.get("alpha_deg")
    if alpha is None:
        match = re.search(r"alpha_([mp]?\d+p\d+)", case.name)
        if not match:
            raise ValueError(f"Cannot infer alpha from {case}")
        alpha = float(match.group(1).replace("m", "-").replace("p", "."))
    return variant, float(alpha)


def copy_case(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def launcher_text(action: str) -> str:
    return f'''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
for BASHRC in /opt/openfoam14/etc/bashrc "$HOME/.local/opt/openfoam14/etc/bashrc"; do
  if [ -f "$BASHRC" ]; then set +u; source "$BASHRC"; set -u; break; fi
done
command -v foamRun >/dev/null || {{ echo "OpenFOAM 14 is required on the server."; exit 2; }}
PYTHON="$ROOT/.venv-remote/bin/python"
if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$ROOT/.venv-remote"
  if compgen -G "$ROOT/wheelhouse/*.whl" >/dev/null; then
    "$PYTHON" -m pip install --no-index --find-links "$ROOT/wheelhouse" -r "$ROOT/requirements-remote.txt"
  else
    "$PYTHON" -m pip install -r "$ROOT/requirements-remote.txt"
  fi
fi
exec "$PYTHON" "$ROOT/CFD_2D/scripts/ramair_remote_queue_runner.py" {action}
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--case", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--n-cores", type=int, default=8)
    parser.add_argument("--timeout-min", type=float, default=120.0)
    parser.add_argument("--steady-timeout-min", type=float, default=120.0)
    parser.add_argument("--steady-initialization", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume-additional-time-star", type=float, default=20.0)
    parser.add_argument("--continue-after-error", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wheelhouse-dir", type=Path)
    parser.add_argument("--download-wheelhouse", action="store_true")
    args = parser.parse_args()
    project = args.project_root.resolve()
    output = (args.output or project / "Application Support/Packages" / f"RamAir_Remote_OpenFOAM_{time.strftime('%Y%m%d_%H%M%S')}.zip").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ramair_remote_") as temporary:
        stage = Path(temporary) / "DESIGN_APP_REMOTE"
        (stage / "CFD_2D/scripts").mkdir(parents=True)
        for source in (project / "CFD_2D/scripts").glob("*.py"):
            shutil.copy2(source, stage / "CFD_2D/scripts" / source.name)
        config_source = project / "CFD_2D/CFD_2D_inputs/config"
        if config_source.is_dir():
            shutil.copytree(config_source, stage / "CFD_2D/CFD_2D_inputs/config")
        entries = []
        for index, raw_case in enumerate(args.case):
            case = raw_case.resolve()
            if not (case / "system/controlDict").is_file():
                raise FileNotFoundError(f"Invalid OpenFOAM case: {case}")
            variant, alpha = case_identity(case)
            case_id = f"{index + 1:03d}_{variant}_{case.name}"
            relative = Path("CFD_2D/openfoam_cases") / variant / case.name
            copy_case(case, stage / relative)
            package_source = project / "CFD_2D/CFD_2D_inputs/case_package" / variant
            if package_source.is_dir():
                shutil.copytree(package_source, stage / "CFD_2D/CFD_2D_inputs/case_package" / variant, dirs_exist_ok=True)
            entries.append({
                "id": case_id,
                "case": relative.as_posix(),
                "variant": variant,
                "alpha_deg": alpha,
                "solver": "auto",
                "n_cores": max(1, int(args.n_cores)),
                "timeout_min": max(1.0, float(args.timeout_min)),
                "steady_initialization": bool(args.steady_initialization),
                "steady_timeout_min": max(1.0, float(args.steady_timeout_min)),
                "resume_additional_time_star": max(0.0, float(args.resume_additional_time_star)),
            })
        queue = {"schema_version": 1, "created_at": time.time(), "continue_after_error": bool(args.continue_after_error), "cases": entries}
        (stage / "remote_queue.json").write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")
        (stage / "requirements-remote.txt").write_text("\n".join(REMOTE_REQUIREMENTS) + "\n", encoding="utf-8")
        wheelhouse = stage / "wheelhouse"
        if args.wheelhouse_dir:
            shutil.copytree(args.wheelhouse_dir.resolve(), wheelhouse)
        elif args.download_wheelhouse:
            wheelhouse.mkdir()
            subprocess.run([sys.executable, "-m", "pip", "download", "--dest", str(wheelhouse), *REMOTE_REQUIREMENTS], check=True)
        for name, action in (("run_remote.sh", "run"), ("resume_remote.sh", "resume"), ("stop_remote.sh", "stop"), ("force_stop_remote.sh", "force-stop"), ("monitor_remote.sh", "status"), ("postprocess_remote.sh", "postprocess")):
            path = stage / name
            path.write_text(launcher_text(action), encoding="utf-8", newline="\n")
            path.chmod(0o755)
        for name in ("run", "resume", "stop", "force_stop", "monitor", "postprocess"):
            (stage / f"{name}_remote.bat").write_text(
                f'@echo off\r\nwsl.exe bash -lc "cd \'$(wslpath \'%~dp0\')\' && bash {name}_remote.sh"\r\n',
                encoding="ascii",
            )
        readme = """# RamAir remote OpenFOAM execution\n\n1. Extract on the Linux filesystem (for example `~/ramair_remote`).\n2. Install OpenFOAM Foundation 14 and MPI on the server. ParaView/pvbatch is optional for rendered products.\n3. Run `bash run_remote.sh`. Use `bash monitor_remote.sh` from another terminal.\n4. `bash stop_remote.sh` requests `stopAt writeNow`; `force_stop_remote.sh` is only the escalation fallback.\n5. Continue retained fields with `bash resume_remote.sh`; post-process all cases with `bash postprocess_remote.sh`.\n\nThe package uses native OpenFOAM. Python wheels are installed offline when the wheelhouse is included. Solver fields and logs remain inside this extracted package.\n"""
        (stage / "README_REMOTE_EXECUTION.md").write_text(readme, encoding="utf-8")
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        manifest = {"schema_version": 1, "created_at": time.time(), "openfoam_required": "Foundation 14", "files": {path.relative_to(stage).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size} for path in files}}
        (stage / "REMOTE_PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, Path(stage.name) / path.relative_to(stage))
    print(f"Remote execution package: {output}")
    print(f"Cases: {len(entries)} | OpenFOAM: Foundation 14 | MPI ranks: {args.n_cores}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
