#!/usr/bin/env bash

# Ram-air CFD 2D reference_uncut debug flow.
# Run this script from Ubuntu/WSL with:
#   bash "Documents and Manuals/Application/run_reference_uncut_debug_wsl.sh"
#
# It intentionally does not install packages, does not run CATIA, and does not
# launch a long CFD campaign. The solver run is optional and short.

set -u
set -o pipefail

STEP_N=0

step() {
  STEP_N=$((STEP_N + 1))
  echo
  echo "=== ${STEP_N}. $* ==="
}

pause_step() {
  echo
  read -r -p "Press Enter to continue, or Ctrl+C to stop..." _
}

fail() {
  echo
  echo "ERROR: $*" >&2
  echo "Stopped before the next stage." >&2
  exit 1
}

configure_gmsh() {
  local validated="$HOME/.local/opt/gmsh-4.15.2/bin/gmsh"
  if [ -x "$validated" ]; then
    export RAMAIR_GMSH_EXECUTABLE="$validated"
    export PATH="$HOME/.local/bin:$(dirname "$validated"):$PATH"
  fi
}

open_path() {
  local path="$1"
  if command -v explorer.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$path")" >/dev/null 2>&1 || true
  else
    echo "Manual inspection path: $path"
  fi
}

open_mesh_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Mesh file not found: $path"
    return 0
  fi
  if command -v explorer.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$path")" >/dev/null 2>&1 && return 0
  fi
  if command -v gmsh >/dev/null 2>&1; then
    (gmsh "$path" >/dev/null 2>&1 &)
    echo "Started gmsh in background for: $path"
  else
    echo "Open manually: $path"
  fi
}

source_openfoam_if_needed() {
  if command -v gmshToFoam >/dev/null 2>&1 && command -v checkMesh >/dev/null 2>&1; then
    return 0
  fi

  local f
  for f in /opt/openfoam*/etc/bashrc /usr/lib/openfoam/openfoam*/etc/bashrc; do
    if [ -f "$f" ]; then
      # shellcheck source=/dev/null
      source "$f"
      break
    fi
  done
}

archive_existing_mesh_outputs() {
  local mesh_dir="$1"
  if [ ! -d "$mesh_dir" ]; then
    return 0
  fi

  echo
  echo "Previous mesh output exists:"
  echo "  $mesh_dir"
  echo "Type S/ARCHIVE to keep a backup, N/DELETE to remove heavy previous files, or press Enter to stop."
  read -r -p "Previous mesh action [S/N]: " MESH_ACTION

  local backup_root stamp backup_dir
  backup_root="$PWD/Previous Versions/mesh_active_output_backups"
  stamp="$(date +%Y%m%d_%H%M%S)"
  backup_dir="$backup_root/reference_uncut_$stamp"
  case "$MESH_ACTION" in
    S|s|ARCHIVE|archive)
      mkdir -p "$backup_root"
      mv "$mesh_dir" "$backup_dir"
      echo "Archived previous mesh output:"
      echo "  $backup_dir"
      ;;
    N|n|DELETE|delete)
      case "$mesh_dir" in
        "$PWD"/CFD_2D/meshes/*) rm -rf "$mesh_dir" ;;
        *) fail "Refusing to delete unexpected mesh path: $mesh_dir" ;;
      esac
      echo "Deleted previous mesh output."
      ;;
    *)
      fail "Previous mesh output was not archived or deleted."
      ;;
  esac
}

prepare_previous_cfd_run_outputs() {
  local case_dir="$1"
  local results_dir="$2"
  if [ ! -d "$case_dir" ] && [ ! -d "$results_dir" ]; then
    return 0
  fi

  echo
  echo "A previous OpenFOAM case/results folder already exists for this variant/alpha:"
  [ -d "$case_dir" ] && echo "  case:    $case_dir"
  [ -d "$results_dir" ] && echo "  results: $results_dir"
  echo "Type S/ARCHIVE to keep previous outputs in Previous Versions/cfd_run_backups."
  echo "Type N/DELETE to remove previous outputs before the new run."
  echo "Press Enter to stop and inspect manually."
  read -r -p "Previous run action [S/N/stop]: " PREV_ACTION

  local backup_root stamp backup_dir
  backup_root="$PWD/Previous Versions/cfd_run_backups"
  stamp="$(date +%Y%m%d_%H%M%S)"
  backup_dir="$backup_root/reference_uncut_alpha_p4p000_$stamp"

  if [[ "$PREV_ACTION" == "S" || "$PREV_ACTION" == "s" || "$PREV_ACTION" == "ARCHIVE" || "$PREV_ACTION" == "archive" ]]; then
    mkdir -p "$backup_dir"
    if [ -d "$case_dir" ]; then
      mv "$case_dir" "$backup_dir/openfoam_case"
    fi
    if [ -d "$results_dir" ]; then
      mv "$results_dir" "$backup_dir/results"
    fi
    echo "Previous CFD run archived:"
    echo "  $backup_dir"
  elif [[ "$PREV_ACTION" == "N" || "$PREV_ACTION" == "n" || "$PREV_ACTION" == "DELETE" || "$PREV_ACTION" == "delete" ]]; then
    case "$case_dir" in
      "$PWD"/CFD_2D/openfoam_cases/*) [ -d "$case_dir" ] && rm -rf "$case_dir" ;;
      *) fail "Refusing to delete unexpected case path: $case_dir" ;;
    esac
    case "$results_dir" in
      "$PWD"/CFD_2D/results/*) [ -d "$results_dir" ] && rm -rf "$results_dir" ;;
      *) fail "Refusing to delete unexpected results path: $results_dir" ;;
    esac
    echo "Previous CFD run outputs deleted."
  else
    fail "Previous run outputs were not archived or deleted."
  fi
}

print_mesh_config_summary() {
  local config_path="$1"
  local variant="$2"
  python3 - "$config_path" "$variant" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
variant = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
common = [
    "gmsh_mesh_algorithm_2d",
    "gmsh_threads",
    "run_boundary_layer",
    "target_y_plus",
]
closed = [
    "closed_wall_curve_method",
    "closed_wall_target_nodes",
    "closed_te_bump_strength",
    "closed_use_yplus_first_cell_height",
    "closed_first_cell_height_chord",
    "closed_boundary_layer_layers",
    "closed_boundary_layer_growth",
    "closed_boundary_layer_total_thickness_chord",
    "closed_boundary_layer_aniso_max_deg",
    "closed_profile_target_points",
    "closed_profile_min_spacing_chord",
    "closed_te_rounding_enabled",
    "closed_te_rounding_points",
    "closed_te_refinement_width_chord",
    "closed_te_refinement_strength",
    "closed_near_wall_size_from_bl",
    "closed_near_wall_size_chord",
    "closed_near_wall_size_bl_factor",
    "closed_nearfield_enabled",
    "closed_nearfield_intermediate_size_chord",
    "closed_farfield_size_chord",
    "domain_radius_chord",
]
keys = common + (closed if variant == "reference_uncut" else [])
print(f"Active editable mesh config: {path.resolve()}")
for key in keys:
    print(f"  {key}: {data.get(key)}")
PY
}

review_mesh_config_before_gmsh() {
  local variant="$1"
  local mesh_config="$PWD/CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json"
  local mesh_reference="$PWD/CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config_reference.json"
  [ -f "$mesh_config" ] || fail "Editable mesh config not found: $mesh_config"
  echo
  echo "Editable mesh config for the next Gmsh run:"
  echo "  $mesh_config"
  echo "Reference/descriptions:"
  echo "  $mesh_reference"
  print_mesh_config_summary "$mesh_config" "$variant"
  open_path "$mesh_config"
  echo
  echo "Edit and save this JSON now if you want to change mesh parameters."
  echo "The mesh builder will read this same file after the pause."
  pause_step
  echo
  echo "Mesh config values after the edit pause:"
  print_mesh_config_summary "$mesh_config" "$variant"
}

resolve_source_project() {
  local candidate
  for candidate in \
    "${RAMAIR_PROJECT_ROOT:-}" \
    "$PWD" \
    "$HOME/ramair_cfd/DESIGN APP" \
    "$CALL_PROJECT"
  do
    if [ -n "$candidate" ] && [ -f "$candidate/preprocess_ramair_main.py" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

step "Locate project and copy to native Linux filesystem"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CALL_PROJECT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"

SOURCE_PROJECT="$(resolve_source_project || true)"
[ -n "$SOURCE_PROJECT" ] || fail "Could not find preprocess_ramair_main.py. Set RAMAIR_PROJECT_ROOT to the project folder and rerun."

if [[ "$SOURCE_PROJECT" == /mnt/* ]]; then
  LINUX_PROJECT="$HOME/ramair_cfd/DESIGN APP"
  mkdir -p "$LINUX_PROJECT"

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git' \
      --exclude '__pycache__' \
      --exclude 'CFD_2D/meshes' \
      --exclude 'CFD_2D/openfoam_cases' \
      --exclude 'CFD_2D/results' \
      --exclude 'Previous Versions/mesh_backups' \
      --exclude 'Previous Versions/mesh_attempt_backups' \
      --exclude 'Previous Versions/mesh_active_output_backups' \
      "$SOURCE_PROJECT/" "$LINUX_PROJECT/" || fail "Project copy with rsync failed."
  else
    mkdir -p "$LINUX_PROJECT"
    cp -a "$SOURCE_PROJECT/." "$LINUX_PROJECT/" || fail "Project copy with cp failed."
  fi
  cd "$LINUX_PROJECT" || fail "Could not enter $LINUX_PROJECT."
else
  cd "$SOURCE_PROJECT" || fail "Could not enter $SOURCE_PROJECT."
fi

echo "Working directory:"
pwd
echo
echo "Important: this path should not start with /mnt/c for normal Gmsh runs."
LOG_DIR="$PWD/Application Support/Logs"
mkdir -p "$LOG_DIR" CFD_2D/reports

pause_step

step "Check minimal environment without installing packages"
configure_gmsh
command -v python3 >/dev/null 2>&1 || fail "python3 is missing in Ubuntu/WSL."
python3 --version

python3 - <<'PY' || fail "Missing Python packages. Install them manually, then rerun: numpy pandas matplotlib"
import numpy
import pandas
import matplotlib
print("Python scientific stack OK")
PY

command -v gmsh >/dev/null 2>&1 || fail "gmsh executable is missing in Ubuntu/WSL."
gmsh --version
python3 CFD_2D/scripts/check_environment.py

pause_step

step "Run ram-air preprocessor"
python3 preprocess_ramair_main.py --config "Application Support/Configurations/default_case_config.json" 2>&1 | tee "$LOG_DIR/reference_uncut_01_preprocess.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "Preprocessor failed. See logs/reference_uncut_01_preprocess.log"

echo
echo "Generated input snapshots:"
find CATIA/Inputs -maxdepth 2 -type f | sort | head -80 || true
find CFD_2D/CFD_2D_inputs -maxdepth 3 -type f | sort | head -120 || true
PROFILE_USED_DIR="$PWD/CATIA/Inputs/Profile_used"
if [ -d "$PROFILE_USED_DIR" ]; then
  open_path "$PROFILE_USED_DIR"
  echo "Check the rounded TE geometry before meshing if needed:"
  echo "  $PROFILE_USED_DIR/ramair_profile_used_cfd_contour_te_zoom.png"
  echo "  $PROFILE_USED_DIR/ramair_profile_used_cfd_contour_preview.png"
fi

pause_step

step "Build CFD 2D input package for reference_uncut"
python3 CFD_2D/scripts/ramair_2d_profile_case_builder.py \
  --case-root . \
  --variant reference_uncut \
  --overwrite \
  --validate 2>&1 | tee "$LOG_DIR/reference_uncut_02_case_builder.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "Case builder failed. See logs/reference_uncut_02_case_builder.log"

CASE_INPUT_DIR="$PWD/CFD_2D/CFD_2D_inputs/case_package/reference_uncut"
find "$CASE_INPUT_DIR" -maxdepth 2 -type f | sort || true

pause_step

step "Review editable mesh parameters before Gmsh"
review_mesh_config_before_gmsh "reference_uncut"

MESH_DIR="$PWD/CFD_2D/meshes/reference_uncut"
while true; do
  step "Archive old mesh output and generate a fresh Gmsh mesh"
  archive_existing_mesh_outputs "$MESH_DIR"

  python3 CFD_2D/scripts/ramair_2d_mesh_builder.py \
    --case-root . \
    --variant reference_uncut \
    --domain ross_cgrid_like \
    --mesh-level debug \
    --write-openfoam-mesh \
    --no-gmsh-temp-workdir \
    --gmsh-timeout-s 900 \
    --plot \
    --overwrite \
    --previous-output-action keep 2>&1 | tee "$LOG_DIR/reference_uncut_03_gmsh.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Gmsh mesh stage failed. See logs/reference_uncut_03_gmsh.log and $MESH_DIR/log.gmsh"

  echo
  echo "Mesh files:"
  find "$MESH_DIR" -maxdepth 3 -type f | sort || true
  ls -lh "$MESH_DIR/mesh_final.geo" "$MESH_DIR/mesh_final.msh" 2>/dev/null || true
  tail -n 120 "$MESH_DIR/log.gmsh" 2>/dev/null || true
  cat "$MESH_DIR/gmsh_performance.json" 2>/dev/null || true

  open_path "$MESH_DIR"
  echo "Optional visual files are in the mesh folder; open them manually if needed:"
  echo "  $MESH_DIR/profile_preprocessing_distribution.png"
  echo "  $MESH_DIR/profile_preprocessing_te_zoom.png"
  echo "  $MESH_DIR/mesh_preview_front_surface.png"
  echo "  $MESH_DIR/mesh_preview_te.png"

  echo
  echo "Optional manual visual check:"
  echo "  gmsh \"$MESH_DIR/mesh_final.msh\""
  read -r -p "Open mesh_final.msh with gmsh now? Type OPEN or press Enter to skip: " OPEN_GMSH
  if [ "$OPEN_GMSH" = "OPEN" ]; then
    open_mesh_file "$MESH_DIR/mesh_final.msh"
  fi

  echo
  echo "Type REMESH to edit cfd2d_mesh_config.json and regenerate this mesh now."
  echo "Press Enter to continue to OpenFOAM conversion/checkMesh."
  read -r -p "Mesh decision [REMESH/continue]: " REMESH_CHOICE
  if [ "$REMESH_CHOICE" = "REMESH" ]; then
    step "Edit mesh parameters for remesh"
    review_mesh_config_before_gmsh "reference_uncut"
    continue
  fi
  break
done

pause_step

step "Convert current mesh to OpenFOAM and run checkMesh"
source_openfoam_if_needed
command -v gmshToFoam >/dev/null 2>&1 || fail "gmshToFoam is missing. Source OpenFOAM before rerunning."
command -v checkMesh >/dev/null 2>&1 || fail "checkMesh is missing. Source OpenFOAM before rerunning."

python3 CFD_2D/scripts/ramair_2d_mesh_builder.py \
  --case-root . \
  --variant reference_uncut \
  --mesh-level debug \
  --check-existing-mesh \
  --openfoam-tool-timeout-s 600 2>&1 | tee "$LOG_DIR/reference_uncut_04_gmshToFoam_checkMesh.log"
CHECK_STATUS=${PIPESTATUS[0]}

echo
echo "Converted boundary file, if available:"
sed -n '1,220p' "$MESH_DIR/constant/polyMesh/boundary" 2>/dev/null || true
tail -n 160 "$MESH_DIR/log.gmshToFoam" 2>/dev/null || true
tail -n 160 "$MESH_DIR/log.checkMesh" 2>/dev/null || true

if [ "$CHECK_STATUS" -ne 0 ]; then
  echo
  echo "checkMesh/conversion returned a failure status."
  echo "You may still continue only for software debugging if a real polyMesh exists."
fi

python3 - <<'PY' || true
import json
from pathlib import Path
report = Path("CFD_2D/meshes/reference_uncut/mesh_quality_report.json")
if report.exists():
    data = json.loads(report.read_text(encoding="utf-8"))
    print("")
    print("Quality summary:")
    print(f"  internal_status: {data.get('status')}")
    print(f"  openfoam_execution_gate: {data.get('openfoam_execution_gate')} - {data.get('openfoam_execution_gate_reason')}")
    if data.get("failed_checks"):
        print(f"  failed_checks: {', '.join(map(str, data.get('failed_checks', [])))}")
    if data.get("warnings"):
        print(f"  warnings: {', '.join(map(str, data.get('warnings', [])[:8]))}")
    print(f"  report: {report.resolve()}")
PY

pause_step

step "Approve mesh for this debug flow"
if [ ! -f "$MESH_DIR/constant/polyMesh/boundary" ]; then
  fail "No converted constant/polyMesh/boundary exists. Cannot write a real OpenFOAM case."
fi

ALLOW_FAILED_CHECKMESH=0
echo "Type APPROVE if checkMesh is OK and you accept the internal quality report for this debug run."
echo "Type FORCE_DEBUG to continue despite checkMesh warnings, only for software debugging."
read -r -p "Approval choice: " APPROVAL

if [ "$APPROVAL" = "APPROVE" ]; then
  python3 CFD_2D/scripts/ramair_2d_mesh_builder.py \
    --case-root . \
    --variant reference_uncut \
    --approve-mesh 2>&1 | tee "$LOG_DIR/reference_uncut_05_mesh_approval.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Mesh approval failed."
elif [ "$APPROVAL" = "FORCE_DEBUG" ]; then
  ALLOW_FAILED_CHECKMESH=1
  python3 CFD_2D/scripts/ramair_2d_mesh_builder.py \
    --case-root . \
    --variant reference_uncut \
    --approve-mesh \
    --force-approve 2>&1 | tee "$LOG_DIR/reference_uncut_05_mesh_approval_FORCE_DEBUG.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Forced debug mesh approval failed."
else
  fail "Mesh was not approved."
fi

pause_step

step "Write OpenFOAM case"
CASE_DIR="$PWD/CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000"
RESULTS_DIR="$PWD/CFD_2D/results/reference_uncut/alpha_p4p000"
prepare_previous_cfd_run_outputs "$CASE_DIR" "$RESULTS_DIR"

python3 CFD_2D/scripts/ramair_2d_openfoam_case_writer.py \
  --case-root . \
  --variant reference_uncut \
  --alpha 4 \
  --reynolds 4000000 \
  --write-case \
  --require-converted-polymesh \
  --overwrite 2>&1 | tee "$LOG_DIR/reference_uncut_06_case_writer.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "OpenFOAM case writer failed."

find "$CASE_DIR" -maxdepth 3 -type f | sort || true
sed -n '1,220p' "$CASE_DIR/constant/polyMesh/boundary" 2>/dev/null || true
open_path "$CASE_DIR"

pause_step

step "Runner dry-run"
python3 CFD_2D/scripts/ramair_2d_openfoam_runner.py \
  --case "$CASE_DIR" \
  --solver auto 2>&1 | tee "$LOG_DIR/reference_uncut_07_runner_dry_run.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "Runner dry-run failed."

echo
read -r -p "Run a short solver test now? Type RUN or press Enter to skip: " RUN_SOLVER
if [ "$RUN_SOLVER" = "RUN" ]; then
  read -r -p "Request clean stop/writeNow after minutes? Example 10, or press Enter for timeout only: " STOP_AFTER_MIN
  RUNNER_ARGS=(
    --case "$CASE_DIR"
    --solver auto
    --run
    --n-cores 1
    --timeout-min 30
  )
  if [ -n "$STOP_AFTER_MIN" ]; then
    RUNNER_ARGS+=(--stop-after-min "$STOP_AFTER_MIN" --stop-grace-min 2 --stop-mode writeNow)
  fi
  if [ "$ALLOW_FAILED_CHECKMESH" -eq 1 ]; then
    RUNNER_ARGS+=(--no-stop-if-checkMesh-fails)
  fi
  python3 CFD_2D/scripts/ramair_2d_openfoam_runner.py "${RUNNER_ARGS[@]}" 2>&1 | tee "$LOG_DIR/reference_uncut_08_solver_short_run.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Short solver run failed."
fi

pause_step

step "Postprocess"
POST_ARGS=(
  --case-root .
  --variant reference_uncut
  --alpha 4
  --run-openfoam-postprocess
  --export-vtk
  --open-results-folder
  --openfoam-postprocess-timeout-s 300
)
echo
read -r -p "Export VTK for all written time directories? Type ALLVTK or press Enter for latestTime only: " VTK_MODE
if [ "$VTK_MODE" = "ALLVTK" ]; then
  POST_ARGS+=(--export-vtk-all-times)
fi
echo
read -r -p "Open ParaView/paraFoam after postprocess? Type PARAVIEW or press Enter to skip: " OPEN_PV
if [ "$OPEN_PV" = "PARAVIEW" ]; then
  POST_ARGS+=(--open-paraview)
fi

python3 CFD_2D/scripts/ramair_2d_postprocess.py \
  "${POST_ARGS[@]}" 2>&1 | tee "$LOG_DIR/reference_uncut_09_postprocess.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "Postprocess failed."

cat CFD_2D/results/reference_uncut/alpha_p4p000/case_summary.json 2>/dev/null || true

echo
echo "Debug flow finished."
