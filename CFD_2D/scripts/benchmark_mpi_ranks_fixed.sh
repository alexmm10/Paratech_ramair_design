#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--run" ]]; then
  echo "Dry safety stop. Use --run for the bounded 1/2/4/8-rank fixture under /tmp."
  exit 0
fi

ROOT="${RAMAIR_PROJECT_ROOT:-$HOME/ramair_cfd/DESIGN_APP}"
BASE="${RAMAIR_BENCHMARK_CASE:-$ROOT/CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000}"
OUTPUT="${RAMAIR_BENCHMARK_OUTPUT:-/tmp/ramair_mpi_rank_fixed}"
RANKS="${RAMAIR_BENCHMARK_RANKS:-1 2 4 8}"
STOP_MIN="${RAMAIR_BENCHMARK_STOP_MIN:-0.1}"

case "$OUTPUT" in
  /tmp/ramair_mpi_rank_*) ;;
  *) echo "Refusing benchmark output outside /tmp/ramair_mpi_rank_*: $OUTPUT" >&2; exit 2 ;;
esac
[[ -f "$BASE/constant/polyMesh/boundary" ]] || { echo "Missing source mesh: $BASE" >&2; exit 2; }

rm -rf -- "$OUTPUT"
mkdir -p "$OUTPUT"
cd "$ROOT"

set +eu
OPENFOAM_BASHRC="${RAMAIR_OPENFOAM_BASHRC:-}"
if [[ -z "$OPENFOAM_BASHRC" ]]; then
  OPENFOAM_BASHRC="$(
    find "$HOME/.local/opt" /opt /usr/lib/openfoam \
      -path '*/openfoam*/etc/bashrc' -type f 2>/dev/null | sort -V | tail -1
  )"
fi
[[ -f "$OPENFOAM_BASHRC" ]] || { echo "OpenFOAM etc/bashrc not found" >&2; exit 2; }
source "$OPENFOAM_BASHRC"
set -eu

start_time="$(foamDictionary "$BASE/system/controlDict" -entry startTime -value)"
source_fv_schemes="$(sha256sum "$BASE/system/fvSchemes" | awk '{print $1}')"
source_fv_solution="$(sha256sum "$BASE/system/fvSolution" | awk '{print $1}')"

for ranks in $RANKS; do
  [[ "$ranks" =~ ^(1|2|4|8)$ ]] || { echo "Only ranks 1/2/4/8 are permitted" >&2; exit 2; }
  scenario="current_${ranks}cores_native"
  case_dir="$OUTPUT/$scenario"
  mkdir -p "$case_dir"
  cp -a "$BASE/0" "$case_dir/"
  cp -a "$BASE/system" "$case_dir/"
  cp -al "$BASE/constant" "$case_dir/"
  foamDictionary "$case_dir/system/controlDict" -entry startFrom -set startTime >/dev/null
  foamDictionary "$case_dir/system/controlDict" -entry startTime -set "$start_time" >/dev/null
  foamDictionary "$case_dir/system/controlDict" -entry endTime -set 1000 >/dev/null
  foamDictionary "$case_dir/system/controlDict" -entry purgeWrite -set 2 >/dev/null
  printf '%s\n' "$source_fv_schemes" > "$OUTPUT/fvSchemes_${scenario}.sha256"
  printf '%s\n' "$source_fv_solution" > "$OUTPUT/fvSolution_${scenario}.sha256"
  set +e
  /usr/bin/time -v -o "$OUTPUT/time_${scenario}.txt" \
    "$ROOT/.venv-cfd2d-ui/bin/python" \
    "$ROOT/CFD_2D/scripts/ramair_2d_openfoam_runner.py" \
    --case "$case_dir" --solver auto --execution-backend native \
    --n-cores "$ranks" --timeout-min 10 --stop-after-min "$STOP_MIN" \
    --stop-grace-min 0.5 --stop-mode writeNow \
    --no-stop-if-checkMesh-fails --run
  scenario_rc=$?
  set -e
  printf '%s\n' "$scenario_rc" > "$OUTPUT/returncode_${scenario}.txt"
done

"$ROOT/.venv-cfd2d-ui/bin/python" \
  "$ROOT/CFD_2D/scripts/ramair_2d_solver_benchmark_report.py" \
  --benchmark-root "$OUTPUT"
