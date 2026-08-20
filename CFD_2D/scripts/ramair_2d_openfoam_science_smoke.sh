#!/usr/bin/env bash
set -euo pipefail

source_root=${1:?"usage: ramair_2d_openfoam_science_smoke.sh SOURCE_ROOT WSL_RUNTIME [OUTPUT_JSON]"}
runtime_root=${2:?"usage: ramair_2d_openfoam_science_smoke.sh SOURCE_ROOT WSL_RUNTIME [OUTPUT_JSON]"}
output_json=${3:-/tmp/ramair_t06_openfoam_smoke.json}
openfoam_bashrc=${RAMAIR_OPENFOAM_BASHRC:-/home/alejm/.local/opt/openfoam14/etc/bashrc}
variant=open_ramair_validation_1m_coarse

# OpenFOAM's interactive bashrc probes optional shell variables and may return
# a non-zero status after exporting a valid environment.
set +u
# shellcheck disable=SC1090
source "$openfoam_bashrc" >/dev/null 2>&1 || true
set -u
command -v foamRun >/dev/null
command -v foamDictionary >/dev/null
smoke_root=$(mktemp -d /tmp/ramair_t06_XXXXXX)
case "$smoke_root" in
    /tmp/ramair_t06_*) ;;
    *) echo "Refusing unexpected temporary path: $smoke_root" >&2; exit 99 ;;
esac
cleanup() {
    case "$smoke_root" in
        /tmp/ramair_t06_*) rm -rf -- "$smoke_root" ;;
        *) echo "Refusing cleanup outside bounded temporary root" >&2 ;;
    esac
}
trap cleanup EXIT
cd "$smoke_root"

mkdir -p \
    "$smoke_root/CFD_2D/CFD_2D_inputs/config" \
    "$smoke_root/CFD_2D/CFD_2D_inputs/case_package/$variant" \
    "$smoke_root/CFD_2D/meshes/$variant/constant"
cp -a "$source_root/CFD_2D/CFD_2D_inputs/config/." \
    "$smoke_root/CFD_2D/CFD_2D_inputs/config/"
cp "$runtime_root/CFD_2D/CFD_2D_inputs/case_package/physical_config.json" \
    "$smoke_root/CFD_2D/CFD_2D_inputs/case_package/physical_config.json"
cp "$runtime_root/CFD_2D/CFD_2D_inputs/case_package/$variant/manifest.json" \
    "$smoke_root/CFD_2D/CFD_2D_inputs/case_package/$variant/manifest.json"
ln -s "$runtime_root/CFD_2D/meshes/$variant/constant/polyMesh" \
    "$smoke_root/CFD_2D/meshes/$variant/constant/polyMesh"

python3 "$source_root/CFD_2D/scripts/ramair_2d_openfoam_case_writer.py" \
    --case-root "$smoke_root" \
    --variant "$variant" \
    --alpha 8 \
    --write-case \
    --no-mesh-approved-required \
    --require-converted-polymesh

case_dir="$smoke_root/CFD_2D/openfoam_cases/$variant/alpha_p8p000"
check_log="$smoke_root/checkMesh.log"
solver_log="$smoke_root/foamRun.log"
checkMesh -case "$case_dir" -constant >"$check_log" 2>&1

max_co=$(foamDictionary "$case_dir/system/controlDict" -entry maxCo -value)
outer=$(foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/nOuterCorrectors -value)
transport=$(foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/transportCorrectionFinal -value)
residual_block=$(foamDictionary "$case_dir/system/fvSolution" -entry PIMPLE/outerCorrectorResidualControl -value)
test "$max_co" = "25"
test "$outer" = "15"
test "$transport" = "false"
grep -q "U" <<<"$residual_block"
grep -q "p" <<<"$residual_block"

# One bounded Euler step verifies parser/startup compatibility only. It is not
# a convergence, stability or physics-validation run.
foamDictionary "$case_dir/system/controlDict" -entry endTime -set 1e-8 >/dev/null
foamDictionary "$case_dir/system/controlDict" -entry deltaT -set 1e-8 >/dev/null
foamDictionary "$case_dir/system/controlDict" -entry maxDeltaT -set 1e-8 >/dev/null
foamDictionary "$case_dir/system/controlDict" -entry writeControl -set timeStep >/dev/null
foamDictionary "$case_dir/system/controlDict" -entry writeInterval -set 1 >/dev/null
foamDictionary "$case_dir/system/fvSchemes" -entry ddtSchemes/default -set Euler >/dev/null

set +e
timeout 180s foamRun -case "$case_dir" -solver incompressibleFluid >"$solver_log" 2>&1
solver_exit_code=$?
set -e

export RAMAIR_T06_CHECK_LOG="$check_log"
export RAMAIR_T06_SOLVER_LOG="$solver_log"
export RAMAIR_T06_OUTPUT="$output_json"
export RAMAIR_T06_SOLVER_EXIT="$solver_exit_code"
export RAMAIR_T06_MAX_CO="$max_co"
export RAMAIR_T06_OUTER="$outer"
export RAMAIR_T06_TRANSPORT="$transport"
python3 - <<'PY'
import json
import os
from pathlib import Path

check = Path(os.environ["RAMAIR_T06_CHECK_LOG"]).read_text(encoding="utf-8", errors="replace")
solver = Path(os.environ["RAMAIR_T06_SOLVER_LOG"]).read_text(encoding="utf-8", errors="replace")
report = {
    "schema_version": 1,
    "scope": "temporary one-step OpenFOAM 14 startup; not convergence or validation",
    "maxCo": float(os.environ["RAMAIR_T06_MAX_CO"]),
    "nOuterCorrectors": int(os.environ["RAMAIR_T06_OUTER"]),
    "transportCorrectionFinal": os.environ["RAMAIR_T06_TRANSPORT"] == "true",
    "outer_residual_fields": ["U", "p"],
    "checkMesh_ok": "Mesh OK." in check,
    "solver_exit_code": int(os.environ["RAMAIR_T06_SOLVER_EXIT"]),
    "starting_time_loop": "Starting time loop" in solver,
    "completed": "End" in solver and "FOAM FATAL" not in solver,
    "fatal": "FOAM FATAL" in solver,
    "last_solver_lines": solver.splitlines()[-12:],
}
output = Path(os.environ["RAMAIR_T06_OUTPUT"])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
PY

test "$solver_exit_code" -eq 0
