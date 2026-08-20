#!/usr/bin/env python3
"""Launch the RamAir DESIGN APP in Linux/WSL with an atomic code release."""
from __future__ import annotations

import argparse
import base64
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


BACKEND_API_VERSION = 25
BOOTSTRAP = Path("Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh")
CANONICAL_WSL_ROOT = "~/ramair_cfd/DESIGN_APP"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the RamAir: Design and CFD graphical application.")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument(
        "--address",
        default="0.0.0.0",
        help="Streamlit bind address inside WSL (default: 0.0.0.0 for Windows localhost forwarding)",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--startup-timeout-s",
        type=float,
        default=300.0,
        help="Maximum wait for the Streamlit port after checks (default: 300 s). Disabled during --install.",
    )
    parser.add_argument("--install", action="store_true", help="Create/update the isolated WSL UI environment.")
    parser.add_argument(
        "--install-system",
        action="store_true",
        help="Also install available Ubuntu CAE dependencies with apt/sudo.",
    )
    parser.add_argument("--yes", action="store_true", help="Accept the recommended first-run installation.")
    parser.add_argument(
        "--no-install-prompt",
        action="store_true",
        help="Never ask to repair a missing environment; useful for automation.",
    )
    parser.add_argument("--check-only", action="store_true", help="Synchronize, verify dependencies and exit.")
    parser.add_argument("--distro", default=os.environ.get("RAMAIR_WSL_DISTRO", "Ubuntu-22.04"))
    parser.add_argument(
        "--wsl-project-root",
        default=os.environ.get("RAMAIR_WSL_PROJECT_ROOT", CANONICAL_WSL_ROOT),
    )
    parser.add_argument(
        "--allow-windows-mount",
        action="store_true",
        help="Run through /mnt/c only for path/UI debugging; native WSL storage is required for meshing.",
    )
    parser.add_argument("--no-sync-code", action="store_true", help="Keep the current WSL runtime code.")
    return parser.parse_args()


def wait_for_port(
    host: str,
    port: int,
    timeout_s: float | None = 300.0,
    process: subprocess.Popen[bytes] | subprocess.Popen[str] | None = None,
) -> bool:
    deadline = None if timeout_s is None else time.time() + max(1.0, timeout_s)
    while deadline is None or time.time() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.35)
    return False


def ask_yes_no(question: str, *, default: bool, assume_yes: bool, disabled: bool) -> bool:
    if assume_yes:
        print(f"{question} yes (--yes)")
        return True
    if disabled or not sys.stdin.isatty():
        return False
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input(question + suffix).strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "s", "si", "sí"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer yes/y/s or no/n.")


def ensure_windows_wsl(args: argparse.Namespace) -> bool:
    if shutil.which("wsl.exe") is None:
        print("MISSING Windows Subsystem for Linux (wsl.exe).", file=sys.stderr)
        print("Open PowerShell as Administrator and run:", file=sys.stderr)
        print(f"  wsl --install -d {args.distro}", file=sys.stderr)
        print("Restart Windows if requested, then run this launcher again.", file=sys.stderr)
        return False
    completed = subprocess.run(
        ["wsl.exe", "--list", "--quiet"],
        check=False,
        text=True,
        encoding="utf-16-le",
        errors="replace",
        capture_output=True,
    )
    distros = {line.strip().replace("\x00", "") for line in completed.stdout.splitlines() if line.strip()}
    if args.distro not in distros:
        print(f"MISSING WSL distribution: {args.distro}", file=sys.stderr)
        if ask_yes_no(
            f"Install {args.distro} with wsl.exe now?",
            default=True,
            assume_yes=args.yes,
            disabled=args.no_install_prompt,
        ):
            code = subprocess.call(["wsl.exe", "--install", "-d", args.distro])
            print("WSL installation requested. Restart Windows if requested, then rerun DESIGN APP.")
            return code == 0 and False
        print(f"Run from an elevated PowerShell: wsl --install -d {args.distro}", file=sys.stderr)
        return False
    return True


def wsl_path_exists(distro: str, root: str, relative: str = "") -> bool:
    target = bash_path(root)
    if relative:
        target += "/" + shlex.quote(relative)
    return subprocess.run(
        encoded_wsl_bash_command(distro, f"test -e {target}"),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def windows_to_wsl(path: Path, distro: str) -> str:
    completed = subprocess.run(
        ["wsl.exe", "-d", distro, "--", "wslpath", "-a", str(path.resolve())],
        check=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
    )
    return completed.stdout.strip()


def bash_path(path: str) -> str:
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def encoded_wsl_bash_command(distro: str, script: str) -> list[str]:
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    decoder = f"printf %s {shlex.quote(payload)} | base64 --decode | bash"
    return ["wsl.exe", "-d", distro, "--", "bash", "-lc", decoder]


def migrate_legacy_wsl_root(distro: str, requested: str) -> None:
    """Move legacy WSL runtimes to an OpenFOAM-safe path without spaces."""
    if requested != CANONICAL_WSL_ROOT:
        return
    new_root = bash_path(requested)
    spaced = bash_path("~/ramair_cfd/DESIGN APP")
    legacy = bash_path("~/ramair_cfd/INPUT_FILES")
    script = f"""
set -e
mkdir -p "$HOME/ramair_cfd"
if [ ! -e {new_root} ]; then
  if [ -d {spaced} ] && [ ! -L {spaced} ]; then
    mv {spaced} {new_root}
    echo "Migrated WSL runtime from DESIGN APP to DESIGN_APP."
  elif [ -d {legacy} ] && [ ! -L {legacy} ]; then
    mv {legacy} {new_root}
    echo "Migrated WSL runtime from INPUT_FILES to DESIGN_APP."
  fi
fi
if [ -d {new_root} ]; then
  for compatibility_name in "DESIGN APP" INPUT_FILES; do
    compatibility_path="$HOME/ramair_cfd/$compatibility_name"
    if [ -L "$compatibility_path" ]; then
      rm -f "$compatibility_path"
    fi
    if [ ! -e "$compatibility_path" ]; then
      ln -s DESIGN_APP "$compatibility_path"
    fi
  done

  # Console scripts installed while the venv lived below a path containing a
  # space have invalid kernel shebangs. Repair only text launchers in bin; the
  # packages and editable user data remain untouched.
  RAMAIR_NEW_ROOT={new_root} python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["RAMAIR_NEW_ROOT"])
bin_dir = root / ".venv-cfd2d-ui/bin"
old_roots = [
    str(Path.home() / "ramair_cfd/DESIGN APP"),
    str(Path.home() / "ramair_cfd/INPUT_FILES"),
]
if bin_dir.is_dir():
    for path in bin_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if b"\\0" in payload[:4096]:
            continue
        updated = payload
        for old in old_roots:
            updated = updated.replace(old.encode(), str(root).encode())
        if updated != payload:
            path.write_bytes(updated)
PY
fi
"""
    completed = subprocess.run(encoded_wsl_bash_command(distro, script), check=False)
    if completed.returncode != 0:
        raise RuntimeError("Could not migrate the WSL runtime to the OpenFOAM-safe DESIGN_APP path.")


def wsl_code_sync(source_root: str, project_expr: str) -> str:
    """Validate and atomically deploy one coherent UI/backend release."""
    source = shlex.quote(source_root)
    return f"""
set -euo pipefail
source_root={source}
project_root={project_expr}
state_dir="$project_root/CFD_2D/app_state"
mkdir -p "$state_dir"
exec 9>"$state_dir/runtime_sync.lock"
if ! flock -w 120 9; then
  echo "ERROR: runtime synchronization lock timed out." >&2
  exit 1
fi

RAMAIR_STATE_DIR="$state_dir" python3 - <<'PY'
import json
import os
from pathlib import Path

active = []
for path in Path(os.environ["RAMAIR_STATE_DIR"]).joinpath("jobs").glob("*.json"):
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        pid = int(job.get("pid") or 0)
        if job.get("status") not in {{"RUNNING", "STOP_REQUESTED"}} or pid <= 0:
            continue
        os.kill(pid, 0)
        active.append(f"{{job.get('stage')}} (PID {{pid}})")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
if active:
    raise SystemExit("ERROR: cannot update runtime while a CAE job is active: " + ", ".join(active))
PY

pid_file="$state_dir/streamlit.pid"
if [ -f "$pid_file" ]; then
  old_pid=$(cat "$pid_file" 2>/dev/null || true)
  case "$old_pid" in
    ''|*[!0-9]*) ;;
    *)
      if kill -0 "$old_pid" 2>/dev/null; then
        echo "Stopping stale Streamlit runtime PID $old_pid before code deployment..."
        kill -TERM "$old_pid" 2>/dev/null || true
        for _ in $(seq 1 40); do
          kill -0 "$old_pid" 2>/dev/null || break
          sleep 0.25
        done
        if kill -0 "$old_pid" 2>/dev/null; then
          echo "ERROR: stale Streamlit runtime did not stop; code was not replaced." >&2
          exit 1
        fi
      fi
      ;;
  esac
fi
rm -f "$pid_file"

RAMAIR_SYNC_SOURCE="$source_root" RAMAIR_SYNC_TARGET="$project_root" RAMAIR_EXPECTED_API={BACKEND_API_VERSION} python3 - <<'PY'
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

source = Path(os.environ["RAMAIR_SYNC_SOURCE"])
target = Path(os.environ["RAMAIR_SYNC_TARGET"])
expected_api = int(os.environ["RAMAIR_EXPECTED_API"])
required_source = [
    source / "CFD_2D/app",
    source / "CFD_2D/scripts",
    source / "CFD_2D/tests",
    source / "Application Support/Tools",
    source / "Application Support/Tests",
    source / "Documents and Manuals/Application",
    source / "CATIA/Utilities",
    source / "CFD_2D/CFD_2D_inputs/config/mesh_presets",
    source / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json",
    source / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json",
    source / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016",
    source / "CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6",
]
missing_source = [str(path) for path in required_source if not path.exists()]
if missing_source:
    raise SystemExit("ERROR: incomplete Windows DESIGN APP checkout: " + ", ".join(missing_source))

stage = Path(tempfile.mkdtemp(prefix="ramair_runtime_release_"))
try:
    for relative in (Path("CFD_2D/app"), Path("CFD_2D/scripts"), Path("CFD_2D/tests")):
        shutil.copytree(source / relative, stage / relative)
    sys.path.insert(0, str(stage / "CFD_2D/app"))
    backend = importlib.import_module("workflow_backend")
    required = {{
        "BACKEND_API_VERSION", "batch_postprocess_command", "case_library_command",
        "catia_detection", "catia_macro_command", "inlet_design_command",
        "mesh_refinement_analysis_command", "mesh_refinement_study_command", "prepare_existing_simulation",
        "mesh_command", "open_checkmesh_problem_viewer", "prepare_existing_outputs",
        "request_application_shutdown", "saved_cases", "set_workcase_selection",
        "start_application_idle_watchdog",
        "touch_application_heartbeat", "validation_publish_command", "request_openfoam_sweep_stop",
    }}
    missing = sorted(required.difference(dir(backend)))
    if missing or backend.BACKEND_API_VERSION != expected_api:
        raise SystemExit(
            f"ERROR: staged UI/backend release invalid; API={{getattr(backend, 'BACKEND_API_VERSION', None)}}, "
            f"expected={{expected_api}}, missing={{missing}}"
        )

    token = uuid.uuid4().hex
    for relative in (Path("CFD_2D/app"), Path("CFD_2D/scripts"), Path("CFD_2D/tests")):
        destination = target / relative
        incoming = destination.with_name(destination.name + f".__incoming.{{token}}")
        previous = destination.with_name(destination.name + f".__previous.{{token}}")
        shutil.copytree(stage / relative, incoming)
        try:
            if destination.exists():
                destination.rename(previous)
            incoming.rename(destination)
        except Exception:
            if previous.exists() and not destination.exists():
                previous.rename(destination)
            raise
        finally:
            shutil.rmtree(incoming, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)
        shutil.rmtree(destination / "__pycache__", ignore_errors=True)

    replace_trees = [
        (source / "Application Support/Tools", target / "Application Support/Tools"),
        (source / "Application Support/Tests", target / "Application Support/Tests"),
        (source / "Documents and Manuals/Application", target / "Documents and Manuals/Application"),
        (source / "CATIA/Utilities", target / "CATIA/Utilities"),
        (
            source / "CFD_2D/CFD_2D_inputs/config/mesh_presets",
            target / "CFD_2D/CFD_2D_inputs/config/mesh_presets",
        ),
        (
            source / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016",
            target / "CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016",
        ),
        (
            source / "CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6",
            target / "CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6",
        ),
    ]
    for src, dst in replace_trees:
        incoming = dst.with_name(dst.name + f".__incoming.{{token}}")
        previous = dst.with_name(dst.name + f".__previous.{{token}}")
        incoming.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, incoming)
        if dst.exists():
            dst.rename(previous)
        incoming.rename(dst)
        shutil.rmtree(previous, ignore_errors=True)

    for relative in (
        "run_ramair_cfd2d_app.py", "README_PROJECT_STRUCTURE.md",
        "PROJECT_CONTEXT_FOR_CODEX.md", "CHANGELOG.md", "AGENTS.md",
        "CFD_2D/README_CFD_2D.md",
        "START_RAMAIR_CFD2D_APP.bat", "INSTALL_AND_START_RAMAIR_CFD2D_APP.bat",
        "preprocess_ramair_main.py", "Generate_RamAir_Canopy_MAIN.CATScript",
        "SETUP_CATIA_PREPROCESSOR_WINDOWS.bat", "RUN_CATIA_PREPROCESSOR_WINDOWS.bat",
    ):
        src = source / relative
        if src.is_file():
            dst = target / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            temporary = dst.with_suffix(dst.suffix + ".incoming")
            shutil.copy2(src, temporary)
            temporary.replace(dst)

    reference_configs = [
        "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config_reference.json",
        "CFD_2D/CFD_2D_inputs/config/cfd2d_inlet_design_config.json",
    ]
    for relative in reference_configs:
        src = source / relative
        dst = target / relative
        if src.is_file() and ("reference" in dst.name or not dst.exists()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Migrate application-owned configuration schemas once, while preserving
    # later edits made by the user inside the WSL application.
    source_solver = source / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json"
    runtime_solver = target / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json"
    source_solver_data = json.loads(source_solver.read_text(encoding="utf-8-sig"))
    runtime_solver_data = (
        json.loads(runtime_solver.read_text(encoding="utf-8-sig"))
        if runtime_solver.is_file() else {{}}
    )
    if int(runtime_solver_data.get("config_schema_version", 0) or 0) < int(
        source_solver_data.get("config_schema_version", 0) or 0
    ):
        runtime_solver.parent.mkdir(parents=True, exist_ok=True)
        if runtime_solver.is_file():
            shutil.copy2(runtime_solver, runtime_solver.with_name("cfd2d_solver_config.pre_schema10.json"))
        temporary = runtime_solver.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(source_solver_data, indent=2) + "\\n", encoding="utf-8")
        temporary.replace(runtime_solver)

    source_workflow = source / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"
    runtime_workflow = target / "CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"
    source_workflow_data = json.loads(source_workflow.read_text(encoding="utf-8-sig"))
    runtime_workflow_data = (
        json.loads(runtime_workflow.read_text(encoding="utf-8-sig"))
        if runtime_workflow.is_file() else {{}}
    )
    if int(runtime_workflow_data.get("version", 0) or 0) < int(
        source_workflow_data.get("version", 0) or 0
    ):
        runtime_workflow.parent.mkdir(parents=True, exist_ok=True)
        if runtime_workflow.is_file():
            shutil.copy2(runtime_workflow, runtime_workflow.with_name("cfd2d_workflow_config.pre_v3.json"))
        execution = dict(runtime_workflow_data.get("execution") or {{}})
        execution.update({{
            "execution_backend": "native",
            "n_cores": 8,
            "steady_initialization": True,
        }})
        runtime_workflow_data["execution"] = execution
        runtime_workflow_data["version"] = int(source_workflow_data.get("version", 3))
        temporary = runtime_workflow.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(runtime_workflow_data, indent=2) + "\\n", encoding="utf-8")
        temporary.replace(runtime_workflow)

    # Preserve user-owned mesh settings. Only migrate the exact legacy inlet
    # transition defaults that produced a single abrupt row across the throat.
    editable_mesh_config = target / "CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
    if editable_mesh_config.is_file():
        data = json.loads(editable_mesh_config.read_text(encoding="utf-8-sig"))
        old_mode = str(data.get("open_inlet_transition_elements", "triangles")).strip().lower()
        old_nodes = int(data.get("open_inlet_connector_normal_nodes", 2) or 2)
        mesh_config_changed = False
        if (old_mode == "triangles" and old_nodes in {2, 3}) or old_mode == "graded_triangles":
            data["open_inlet_transition_elements"] = "graded_quads"
            data["open_inlet_transition_growth"] = 1.20
            data["open_inlet_connector_normal_nodes"] = 0
            mesh_config_changed = True
        if "open_inlet_marker_bump_strength" not in data:
            data["open_inlet_marker_bump_strength"] = 0.60
            mesh_config_changed = True
        if mesh_config_changed:
            backup = editable_mesh_config.with_name(
                editable_mesh_config.stem + ".pre_graded_inlet_migration.json"
            )
            if not backup.exists():
                shutil.copy2(editable_mesh_config, backup)
            temporary = editable_mesh_config.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
            temporary.replace(editable_mesh_config)

    manifest = {{
        "status": "SYNCHRONIZED",
        "backend_api": expected_api,
        "source_checkout": str(source),
        "runtime_project": str(target),
        "synchronized_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "editable_runtime_data_preserved": True,
    }}
    manifest_path = target / "CFD_2D/app_state/runtime_sync_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_manifest = manifest_path.with_suffix(".json.tmp")
    temp_manifest.write_text(json.dumps(manifest, indent=2) + "\\n", encoding="utf-8")
    temp_manifest.replace(manifest_path)
finally:
    shutil.rmtree(stage, ignore_errors=True)
PY
flock -u 9
echo "Runtime UI/backend API {BACKEND_API_VERSION} synchronized atomically."
""".strip()


def initialize_native_runtime(args: argparse.Namespace, source_root: str, requested_expr: str) -> None:
    exclusions = [
        ".venv-cfd2d-ui", ".venv-cfd2d", ".venv-catia", "__pycache__", ".pytest_cache",
        "Application Support/Logs", "Application Support/Reports", "Application Support/Temp",
        "Previous Versions", "Results", "CATIA/Exports", "CFD_2D/app_state", "CFD_2D/meshes",
        "CFD_2D/openfoam_cases", "CFD_2D/reports", "CFD_2D/results",
    ]
    exclude_args = " ".join(f"--exclude={shlex.quote(item)}" for item in exclusions)
    script = (
        f"mkdir -p {requested_expr} && "
        f"tar -C {shlex.quote(source_root)} {exclude_args} -cf - . | tar -C {requested_expr} -xf -"
    )
    completed = subprocess.run(encoded_wsl_bash_command(args.distro, script), check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Could not initialize native WSL project at {args.wsl_project_root}")


def wsl_command(args: argparse.Namespace) -> tuple[list[str], str]:
    migrate_legacy_wsl_root(args.distro, args.wsl_project_root)
    requested = args.wsl_project_root
    requested_expr = bash_path(requested)
    exists = subprocess.run(
        encoded_wsl_bash_command(args.distro, f"test -d {requested_expr}"),
        check=False,
    ).returncode == 0
    native_project = exists
    if exists:
        project_root = requested
    elif args.install:
        source_root = windows_to_wsl(Path(__file__).resolve().parent, args.distro)
        initialize_native_runtime(args, source_root, requested_expr)
        project_root = requested
        native_project = True
        print(f"Initialized native WSL project: {requested}")
    elif args.allow_windows_mount:
        project_root = windows_to_wsl(Path(__file__).resolve().parent, args.distro)
        native_project = False
        print("WARNING: running from /mnt/c; use native WSL storage before meshing or solving.")
    else:
        raise FileNotFoundError(
            f"Native WSL project not found at {requested}. Run INSTALL_AND_START_RAMAIR_CFD2D_APP.bat once."
        )

    project_expr = bash_path(project_root)
    sync = ""
    if native_project and not args.no_sync_code:
        source_root = windows_to_wsl(Path(__file__).resolve().parent, args.distro)
        sync = wsl_code_sync(source_root, project_expr) + "\n"
    bootstrap_args = ["--install" if args.install else "--check"]
    if args.install_system:
        bootstrap_args.append("--install-system")
    bootstrap_mode = " ".join(map(shlex.quote, bootstrap_args))
    bootstrap = f"bash {shlex.quote(BOOTSTRAP.as_posix())} {bootstrap_mode}"
    if args.check_only:
        shell = f"{sync}cd {project_expr} && {bootstrap}"
    else:
        streamlit = (
            f"exec .venv-cfd2d-ui/bin/python -m streamlit run CFD_2D/app/ramair_cfd2d_app.py "
            f"--server.address {shlex.quote(args.address)} --server.port {int(args.port)} "
            "--server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"
        )
        launch = (
            f"mkdir -p CFD_2D/app_state; exec 8>CFD_2D/app_state/streamlit_{int(args.port)}.lock; "
            f"if ! flock -n 8; then echo 'DESIGN APP is already running on port {int(args.port)}.'; exit 0; fi; "
            "echo $$ > CFD_2D/app_state/streamlit.pid; "
            "trap 'rm -f CFD_2D/app_state/streamlit.pid' EXIT; "
            f"{streamlit}"
        )
        # Streamlit itself does not need OpenFOAM in its parent environment.
        # Solver jobs source OpenFOAM through openfoam_environment.py, avoiding
        # nounset/ZSH_NAME failures during application startup.
        shell = f"{sync}cd {project_expr} && {bootstrap} && {launch}"
    return encoded_wsl_bash_command(args.distro, shell), project_root


def linux_command(args: argparse.Namespace) -> list[str]:
    root = Path(__file__).resolve().parent
    bootstrap_args = ["--install" if args.install else "--check"]
    if args.install_system:
        bootstrap_args.append("--install-system")
    check = subprocess.run(["bash", str(root / BOOTSTRAP), *bootstrap_args], cwd=root)
    if check.returncode != 0 or args.check_only:
        raise SystemExit(check.returncode)
    streamlit = shlex.join([
        str(root / ".venv-cfd2d-ui/bin/python"), "-m", "streamlit", "run",
        str(root / "CFD_2D/app/ramair_cfd2d_app.py"), "--server.address", args.address,
        "--server.port", str(args.port), "--server.headless", "true",
        "--server.fileWatcherType", "none", "--browser.gatherUsageStats", "false",
    ])
    return ["bash", "-lc", f"cd {shlex.quote(str(root))} && exec {streamlit}"]


def consume_shutdown_request(distro: str, project_root: str) -> bool:
    marker = bash_path(project_root) + "/CFD_2D/app_state/shutdown_wsl.request"
    command = encoded_wsl_bash_command(
        distro,
        f"if [ -f {marker} ]; then rm -f {marker}; exit 0; else exit 1; fi",
    )
    return subprocess.run(command, check=False).returncode == 0


def write_windows_results_pointer(distro: str, project_root: str) -> None:
    """Expose native-WSL Results from the visible Windows checkout."""
    if os.name != "nt":
        return
    root_expr = bash_path(project_root)
    completed = subprocess.run(
        encoded_wsl_bash_command(
            distro,
            f"mkdir -p {root_expr}/Results; cd {root_expr}/Results; wslpath -w \"$PWD\"",
        ),
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return
    windows_target = completed.stdout.strip().splitlines()[-1]
    visible = Path(__file__).resolve().parent / "Results"
    visible.mkdir(parents=True, exist_ok=True)
    (visible / "OPEN_RESULTS_IN_WSL.bat").write_text(
        f'@echo off\r\nstart "" explorer.exe "{windows_target}"\r\n',
        encoding="utf-8",
    )
    (visible / "README_RESULTS_LOCATION.txt").write_text(
        "RamAir stores reusable meshes and CFD results in the native WSL filesystem for performance.\n"
        "Open OPEN_RESULTS_IN_WSL.bat or use the Results button in the application.\n\n"
        f"Actual Windows-visible location:\n{windows_target}\n",
        encoding="utf-8",
    )


def configure_first_run(args: argparse.Namespace) -> None:
    if args.install or args.check_only:
        return
    if os.name == "nt":
        ready = wsl_path_exists(args.distro, args.wsl_project_root, "CFD_2D/app_state/environment_setup.json")
    else:
        ready = (Path(__file__).resolve().parent / "CFD_2D/app_state/environment_setup.json").is_file()
    if ready:
        return

    print("\nFirst DESIGN APP installation has not been registered.")
    print("The installer can configure the isolated Python environment and attempt the available Ubuntu packages:")
    print("  Gmsh 4.15.2, XFOIL, OpenFOAM 13, MPI, ParaView, gnuplot and required GUI libraries.")
    print("Ubuntu may request your Linux sudo password. Missing external repositories are reported with exact instructions.")
    if ask_yes_no(
        "Run the complete installation and environment verification now?",
        default=True,
        assume_yes=args.yes,
        disabled=args.no_install_prompt,
    ):
        args.install = True
        args.install_system = True
    else:
        print("Continuing with a read-only environment check. No package will be installed.")


def launch_once(args: argparse.Namespace) -> tuple[int, str, bool]:
    if os.name == "nt":
        command, project_root = wsl_command(args)
        write_windows_results_pointer(args.distro, project_root)
        print(f"WSL project: {project_root}")
    else:
        command = linux_command(args)
        project_root = str(Path(__file__).resolve().parent)
    if args.check_only:
        return subprocess.call(command), project_root, False

    print("Starting RamAir: Design and CFD...")
    process = subprocess.Popen(command)
    url = f"http://localhost:{args.port}"
    startup_timeout = None if args.install else float(args.startup_timeout_s)
    opened = wait_for_port(
        "127.0.0.1",
        args.port,
        timeout_s=startup_timeout,
        process=process,
    )
    if opened:
        print(f"Application: {url}")
        if not args.no_browser:
            webbrowser.open(url)
    else:
        if process.poll() is None:
            print(
                f"The application did not open its port within {args.startup_timeout_s:g} seconds; "
                "stopping the incomplete launch.",
                file=sys.stderr,
            )
            process.terminate()
        else:
            print(f"The application startup stopped early with exit code {process.returncode}.", file=sys.stderr)
    try:
        returncode = process.wait(timeout=15 if not opened else None)
    except subprocess.TimeoutExpired:
        process.kill()
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        returncode = 130

    if os.name == "nt" and opened and consume_shutdown_request(args.distro, project_root):
        print(f"No managed task is active; terminating WSL distribution {args.distro}...")
        subprocess.run(["wsl.exe", "--terminate", args.distro], check=False)
    return returncode, project_root, opened


def main() -> int:
    args = parse_args()
    try:
        if os.name == "nt" and not ensure_windows_wsl(args):
            return 2
        configure_first_run(args)
        returncode, _, opened = launch_once(args)
        if opened or returncode == 0 or args.check_only or args.install:
            return returncode
        if ask_yes_no(
            "Startup failed. Run the complete automatic repair and retry once?",
            default=True,
            assume_yes=args.yes,
            disabled=args.no_install_prompt,
        ):
            args.install = True
            args.install_system = True
            return launch_once(args)[0]
        print("Repair skipped. Run INSTALL_AND_START_RAMAIR_CFD2D_APP.bat when ready.", file=sys.stderr)
        return returncode
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Run INSTALL_AND_START_RAMAIR_CFD2D_APP.bat for guided setup.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
