#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR"
while [ "$ROOT" != "/" ] && { [ ! -f "$ROOT/preprocess_ramair_main.py" ] || [ ! -d "$ROOT/CFD_2D" ]; }; do
  ROOT="$(dirname "$ROOT")"
done
if [ ! -f "$ROOT/preprocess_ramair_main.py" ] || [ ! -d "$ROOT/CFD_2D" ]; then
  echo "MISSING RamAir DESIGN APP root above $SCRIPT_DIR" >&2
  exit 2
fi
VENV="${RAMAIR_CFD2D_UI_VENV:-$ROOT/.venv-cfd2d-ui}"
REQ="$ROOT/CFD_2D/app/requirements-cfd2d-app.txt"
INSTALL=0
INSTALL_SYSTEM=0

usage() {
  cat <<'EOF'
Usage: bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" [--check | --install] [--install-system]

  --check    Report dependencies without changing the environment (default).
  --install  Create/update .venv-cfd2d-ui and install pinned Python packages.
  --install-system
             Install available Ubuntu dependencies with apt (sudo can ask for
             the Linux password). This includes MPI, ParaView, XFOIL and GUI
             libraries. The latest supported OpenFOAM Foundation release
             (currently v14) is installed when its apt repository is already
             configured.

Without --install-system the script never invokes sudo or modifies Ubuntu.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    --install-system) INSTALL=1; INSTALL_SYSTEM=1 ;;
    --check) INSTALL=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

echo "RamAir CFD 2D application environment"
echo "Project: $ROOT"
echo "Python environment: $VENV"
cd "$ROOT"

install_system_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "WARNING apt-get is unavailable; system dependencies were not installed."
    return 0
  fi

  local requested=(
    python3 python3-venv python3-pip curl ca-certificates tar
    libglu1-mesa libxrender1 libxcursor1 libxinerama1 libxft2
    openmpi-bin gnuplot xfoil paraview
  )
  local available=()
  local package
  echo "Checking Ubuntu packages available for automatic installation..."
  if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    apt-get update
  elif command -v sudo >/dev/null 2>&1; then
    echo "Ubuntu may request your Linux password for sudo."
    sudo apt-get update
  else
    echo "WARNING sudo is unavailable. Run as root or install the packages printed below."
    return 0
  fi

  for package in "${requested[@]}"; do
    if apt-cache show "$package" >/dev/null 2>&1; then
      available+=("$package")
    else
      echo "WARNING Ubuntu package unavailable in configured repositories: $package"
    fi
  done
  if apt-cache show openfoam14 >/dev/null 2>&1; then
    if ! dpkg-query -W openfoam14 >/dev/null 2>&1; then
      available+=(openfoam14)
    fi
  elif ! find "$HOME/.local/opt" /opt /usr/lib/openfoam -path '*/openfoam14/etc/bashrc' -type f -print -quit 2>/dev/null | grep -q .; then
    echo "MISSING OpenFOAM 14 repository. Follow: https://openfoam.org/download/14-ubuntu/"
    echo "OpenFOAM 13 remains a compatibility fallback, not the current reference release."
  fi
  if apt-cache show xflr5 >/dev/null 2>&1; then
    available+=(xflr5)
  else
    echo "WARNING optional XFLR5 package is unavailable; XFOIL automation remains usable."
  fi

  if [ "${#available[@]}" -gt 0 ]; then
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
      DEBIAN_FRONTEND=noninteractive apt-get install -y "${available[@]}"
    else
      sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${available[@]}"
    fi
  fi
}

if [ "$INSTALL_SYSTEM" -eq 1 ]; then
  set +e
  install_system_packages
  system_install_code=$?
  set -e
  if [ "$system_install_code" -ne 0 ]; then
    echo "WARNING automatic Ubuntu package installation returned $system_install_code."
    echo "The application installer will continue and report every remaining dependency."
  fi
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "MISSING python3"
  echo "Install with: sudo apt update && sudo apt install -y python3 python3-venv"
  exit 1
fi

python3 "$ROOT/CFD_2D/scripts/initialize_project_layout.py" --project-root "$ROOT" --migrate --create

if [ "$INSTALL" -eq 1 ]; then
  if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating isolated virtual environment without upgrading pip..."
    python3 -m venv "$VENV"
  elif grep -q '^include-system-site-packages = true' "$VENV/pyvenv.cfg" 2>/dev/null; then
    echo "Recreating legacy environment without Ubuntu system-site packages..."
    echo "This removes only generated virtual-environment packages; project data is untouched."
    python3 -m venv --clear "$VENV"
  fi
  echo "Installing pinned application packages..."
  "$VENV/bin/python" -m pip install \
    --disable-pip-version-check \
    --progress-bar off \
    --timeout 60 \
    --retries 2 \
    -r "$REQ"
  if ! bash "$ROOT/Documents and Manuals/Application/install_gmsh_4_15_wsl.sh"; then
    echo "WARNING Gmsh 4.15.2 user-space installation failed."
    echo "Retry: bash 'Documents and Manuals/Application/install_gmsh_4_15_wsl.sh'"
  fi
  if ! bash "$ROOT/Documents and Manuals/Application/install_xfoil_wsl.sh"; then
    if command -v xfoil >/dev/null 2>&1; then
      echo "OK      xfoil        $(command -v xfoil)"
    else
      echo "WARNING XFOIL installation failed; inlet design will remain unavailable."
    fi
  fi
fi

export PATH="$HOME/.local/bin:$PATH"

PYTHON="$VENV/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "MISSING application environment: $VENV"
  echo "Run: bash 'Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh' --install"
  exit 1
fi

"$PYTHON" - <<'PY'
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
import sys

missing = []
for distribution, module in [
    ("streamlit", "streamlit"),
    ("pyarrow", "pyarrow"),
    ("gmsh", "gmsh"),
    ("PyFoam", "PyFoam"),
    ("numpy", "numpy"),
    ("scipy", "scipy"),
    ("pandas", "pandas"),
    ("matplotlib", "matplotlib"),
    ("Pillow", "PIL"),
]:
    try:
        import_module(module)
        try:
            found = version(distribution)
        except PackageNotFoundError:
            found = "system"
        print(f"OK      {distribution:12} {found}")
    except Exception as exc:
        print(f"MISSING {distribution:12} {exc}")
        missing.append(distribution)

try:
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    print("OK      Axes3D      isolated Matplotlib toolkit")
except Exception as exc:
    print(f"MISSING Axes3D      {exc}")
    missing.append("Axes3D")

if missing:
    print("Application Python environment is incomplete: " + ", ".join(missing))
    sys.exit(1)
PY

PYTHONPATH="$ROOT/CFD_2D/app" "$PYTHON" - <<'PY'
import workflow_backend

required = {
    "BACKEND_API_VERSION",
    "batch_postprocess_command",
    "catia_detection",
    "catia_macro_command",
    "mesh_command",
    "mesh_optimizer_command",
    "prepare_existing_outputs",
    "open_mesh_viewer",
    "open_checkmesh_problem_viewer",
    "inlet_design_command",
    "mesh_refinement_analysis_command",
    "mesh_refinement_study_command",
    "prepare_existing_simulation",
    "case_library_command",
    "request_application_shutdown",
    "request_openfoam_sweep_stop",
    "saved_cases",
    "set_workcase_selection",
    "start_application_idle_watchdog",
    "touch_application_heartbeat",
    "validation_publish_command",
}
missing = sorted(required.difference(dir(workflow_backend)))
if missing:
    raise SystemExit("MISSING application/backend API: " + ", ".join(missing))
if workflow_backend.BACKEND_API_VERSION != 26:
    raise SystemExit(
        "MISSING compatible workflow backend: expected API 25, "
        f"found {workflow_backend.BACKEND_API_VERSION}"
    )
print(f"OK      app/backend API {workflow_backend.BACKEND_API_VERSION}")
PY

if [ -z "${WSL_DISTRO_NAME:-}" ]; then
  echo "WARNING WSL_DISTRO_NAME is not set. OpenFOAM stages require Linux/WSL."
else
  echo "OK      WSL          $WSL_DISTRO_NAME"
fi

if command -v xfoil >/dev/null 2>&1; then
  echo "OK      xfoil        $(command -v xfoil)"
elif [ -x "$ROOT/Application Support/Tools/xfoil/linux/xfoil" ]; then
  echo "OK      xfoil        $ROOT/Application Support/Tools/xfoil/linux/xfoil"
else
  echo "WARNING xfoil not found; run bash 'Documents and Manuals/Application/install_xfoil_wsl.sh'"
fi

if ! "$PYTHON" -c "import gmsh" >/dev/null 2>&1; then
  echo ""
  echo "The gmsh wheel is installed but cannot load its native library."
  echo "On Ubuntu 22.04 install the usual runtime dependency explicitly:"
  echo "  sudo apt update && sudo apt install -y libglu1-mesa libxrender1"
  exit 1
fi

echo ""
echo "Complete CAD/CFD dependency report:"
"$PYTHON" "$ROOT/CFD_2D/scripts/check_environment.py"

if [ "$INSTALL" -eq 1 ]; then
  mkdir -p "$ROOT/CFD_2D/app_state"
  RAMAIR_SETUP_ROOT="$ROOT" RAMAIR_SETUP_SYSTEM="$INSTALL_SYSTEM" "$PYTHON" - <<'PY'
import json
import os
import time
from pathlib import Path

root = Path(os.environ["RAMAIR_SETUP_ROOT"])
path = root / "CFD_2D/app_state/environment_setup.json"
payload = {
    "status": "CORE_READY",
    "installed_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
    "system_packages_requested": os.environ.get("RAMAIR_SETUP_SYSTEM") == "1",
    "note": "Optional CAE tools are reported separately by check_environment.py.",
}
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
print(f"Installation marker written: {path}")
PY
fi

echo "Environment check completed."
