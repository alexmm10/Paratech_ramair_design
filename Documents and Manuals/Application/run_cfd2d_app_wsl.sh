#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${1:-8501}"
ADDRESS="${2:-0.0.0.0}"

if [ ! -x "$ROOT/.venv-cfd2d-ui/bin/python" ]; then
  echo "Missing .venv-cfd2d-ui. Run: bash 'Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh' --install" >&2
  exit 1
fi

cd "$ROOT"
# OpenFOAM is loaded per CFD subprocess by openfoam_environment.py.  Do not
# source it in this nounset launcher: Streamlit does not require FOAM variables.
exec .venv-cfd2d-ui/bin/python -m streamlit run CFD_2D/app/ramair_cfd2d_app.py \
  --server.address "$ADDRESS" \
  --server.port "$PORT" \
  --server.headless true \
  --server.fileWatcherType none \
  --browser.gatherUsageStats false
