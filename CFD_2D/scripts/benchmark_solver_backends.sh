#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--run" ]]; then
  echo "Dry safety stop. Rerun with --run to execute the bounded benchmark matrix under /tmp."
  exit 0
fi
set --

ROOT="${RAMAIR_PROJECT_ROOT:-$HOME/ramair_cfd/DESIGN_APP}"
BASE="${RAMAIR_BENCHMARK_CASE:-$ROOT/CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000}"
BENCH="${RAMAIR_BENCHMARK_OUTPUT:-/tmp/ramair_solver_backend_benchmark}"
STOP_MIN="${RAMAIR_BENCHMARK_STOP_MIN:-0.25}"
MODES="${RAMAIR_BENCHMARK_MODES:-native pyfoam}"
CORES_LIST="${RAMAIR_BENCHMARK_CORES:-6 8}"
NUMERICS_PROFILES="${RAMAIR_BENCHMARK_NUMERICS:-previous optimized}"
LIVE_MONITOR="${RAMAIR_BENCHMARK_LIVE_MONITOR:-0}"

case "$BENCH" in
  /tmp/ramair_solver_*) ;;
  *) echo "Refusing benchmark output outside /tmp/ramair_solver_*: $BENCH" >&2; exit 2 ;;
esac
[[ -f "$BASE/constant/polyMesh/boundary" ]] || { echo "Missing source polyMesh: $BASE" >&2; exit 2; }
cd "$ROOT"

rm -rf -- "$BENCH"
mkdir -p "$BENCH"
set +eu
OPENFOAM_BASHRC="${RAMAIR_OPENFOAM_BASHRC:-}"
if [[ -z "$OPENFOAM_BASHRC" ]]; then
  OPENFOAM_BASHRC="$(
    find "$HOME/.local/opt" /opt /usr/lib/openfoam \
      -path '*/openfoam*/etc/bashrc' -type f 2>/dev/null |
      sort -V | tail -1
  )"
fi
[[ -f "$OPENFOAM_BASHRC" ]] || { echo "OpenFOAM etc/bashrc not found" >&2; exit 2; }
source "$OPENFOAM_BASHRC"
set -eu

for numerics in $NUMERICS_PROFILES; do
for cores in $CORES_LIST; do
for mode in $MODES; do
  scenario="${numerics}_${cores}cores_${mode}"
  case_dir="$BENCH/$scenario"
  mkdir -p "$case_dir"
  cp -a "$BASE/0" "$case_dir/"
  cp -a "$BASE/system" "$case_dir/"
  cp -al "$BASE/constant" "$case_dir/"
  for metadata in case_config.json case_input_summary.json; do
    [[ -f "$BASE/$metadata" ]] && cp -a "$BASE/$metadata" "$case_dir/"
  done
  foamDictionary "$case_dir/system/controlDict" -entry startFrom -set startTime >/dev/null
  foamDictionary "$case_dir/system/controlDict" -entry startTime -set 0 >/dev/null
  foamDictionary "$case_dir/system/controlDict" -entry endTime -set 1000 >/dev/null
  foamDictionary "$case_dir/system/controlDict" -entry purgeWrite -set 2 >/dev/null
  case "$numerics" in
    previous)
      foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nOuterCorrectors -set 3 >/dev/null
      foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nCorrectors -set 2 >/dev/null
      foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nNonOrthogonalCorrectors -set 1 >/dev/null
      ;;
    optimized)
      foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nOuterCorrectors -set 1 >/dev/null
      foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nCorrectors -set 2 >/dev/null
      foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nNonOrthogonalCorrectors -set 1 >/dev/null
      ;;
    current) ;;
    *) echo "Unknown numerics profile: $numerics" >&2; exit 2 ;;
  esac
  extra=()
  [[ "$mode" == "pyfoam" && "$LIVE_MONITOR" == "1" ]] && extra+=(--pyfoam-live-monitor)
  set +e
  /usr/bin/time -v -o "$BENCH/time_${scenario}.txt" \
    "$ROOT/.venv-cfd2d-ui/bin/python" \
    "$ROOT/CFD_2D/scripts/ramair_2d_openfoam_runner.py" \
    --case "$case_dir" --solver auto --execution-backend "$mode" --n-cores "$cores" \
    --timeout-min 1 --stop-after-min "$STOP_MIN" --stop-grace-min 0.5 \
    --stop-mode writeNow --no-stop-if-checkMesh-fails --run "${extra[@]}"
  scenario_rc=$?
  set -e
  printf '%s\n' "$scenario_rc" > "$BENCH/returncode_${scenario}.txt"
done
done
done

for numerics in $NUMERICS_PROFILES; do
for cores in $CORES_LIST; do
for mode in $MODES; do
  scenario="${numerics}_${cores}cores_${mode}"
  echo "=== $scenario ==="
  grep -E "Elapsed|Maximum resident|User time|System time" "$BENCH/time_${scenario}.txt" || true
  grep -E '"status"|"forceCoeffs_available"' "$BENCH/$scenario/run_status.json" || true
  find "$BENCH/$scenario/postProcessing/PyFoamPlots" -maxdepth 1 -type f \
    -printf '%f %s bytes\n' 2>/dev/null | sort || true
done
done
done
"$ROOT/.venv-cfd2d-ui/bin/python" \
  "$ROOT/CFD_2D/scripts/ramair_2d_solver_benchmark_report.py" \
  --benchmark-root "$BENCH"
du -sh "$BENCH"
