#!/usr/bin/env bash
set -u
set -o pipefail

fail() {
  echo
  echo "ERROR: $*" >&2
  exit 1
}

pause_step() {
  echo
  read -r -p "Press Enter to continue, or Ctrl+C to stop..." _
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
      source "$f"
      break
    fi
  done
}

handle_existing_path() {
  local target="$1"
  local variant="$2"
  local label="$3"
  local action="$4"
  if [ ! -e "$target" ]; then
    return 0
  fi
  local backup_root stamp backup_dir
  backup_root="$PWD/Previous Versions/cfd2d_workflow_backups"
  stamp="$(date +%Y%m%d_%H%M%S)"
  backup_dir="$backup_root/${variant}_${label}_${stamp}"
  if [ "$action" = "ask" ]; then
    echo "Existing $label path: $target"
    echo "Type S/ARCHIVE to save a backup, N/DELETE to remove heavy previous files, or press Enter to stop."
    read -r -p "Action for existing $label [S/N]: " ans
    case "$ans" in
      S|s|ARCHIVE|archive) action="archive" ;;
      N|n|DELETE|delete) action="delete" ;;
      *) fail "Existing $label was not archived or deleted." ;;
    esac
  fi
  if [ "$action" = "archive" ]; then
    mkdir -p "$backup_dir"
    mv "$target" "$backup_dir/$(basename "$target")"
    echo "Archived $label to $backup_dir"
  elif [ "$action" = "delete" ]; then
    case "$target" in
      "$PWD"/CFD_2D/meshes/*|"$PWD"/CFD_2D/openfoam_cases/*|"$PWD"/CFD_2D/results/*) rm -rf "$target" ;;
      *) fail "Refusing to delete unexpected path: $target" ;;
    esac
  else
    fail "Existing $label action is stop."
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
    "closed_profile_target_points",
    "closed_te_rounding_enabled",
    "closed_te_rounding_points",
    "closed_near_wall_size_from_bl",
    "closed_near_wall_size_chord",
    "closed_farfield_size_chord",
    "domain_radius_chord",
]
open_keys = [
    "open_wall_curve_method",
    "open_use_yplus_first_cell_height",
    "open_first_cell_height_chord",
    "open_boundary_layer_layers",
    "open_boundary_layer_growth",
    "open_boundary_layer_fan_at_lips",
    "open_boundary_layer_lip_fan_points",
    "open_near_wall_size_from_bl",
    "open_near_wall_size_chord",
    "open_surface_target_nodes",
    "open_te_transfinite_min_nodes",
    "open_lip_transfinite_min_nodes",
    "open_surface_size_le_chord",
    "open_surface_size_lip_chord",
    "open_surface_size_te_chord",
    "open_cavity_size_chord",
    "open_farfield_size_chord",
]
keys = common + (open_keys if variant in {"open_ramair", "standard", "optimized", "ross_standard_8p4", "ross_minimum_4p0"} else closed)
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
  read -r -p "Press Enter after saving the mesh JSON, or Ctrl+C to stop..." _
  echo
  echo "Mesh config values after the edit pause:"
  print_mesh_config_summary "$mesh_config" "$variant"
}

resolve_source_project() {
  local candidate
  for candidate in     "${RAMAIR_PROJECT_ROOT:-}"     "$PWD"     "$HOME/ramair_cfd/DESIGN APP"     "$CALL_PROJECT"
  do
    if [ -n "$candidate" ] && [ -f "$candidate/preprocess_ramair_main.py" ]; then
      printf '%s
' "$candidate"
      return 0
    fi
  done

  return 1
}

echo "=== Locate project ==="
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CALL_PROJECT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
SOURCE_PROJECT="$(resolve_source_project || true)"
[ -n "$SOURCE_PROJECT" ] || fail "Could not find preprocess_ramair_main.py. Set RAMAIR_PROJECT_ROOT to the project folder and rerun."

if [[ "$SOURCE_PROJECT" == /mnt/* && true == true ]]; then
  LINUX_PROJECT="$HOME/ramair_cfd/DESIGN APP"
  mkdir -p "$LINUX_PROJECT"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude 'CFD_2D/meshes' --exclude 'CFD_2D/openfoam_cases' --exclude 'CFD_2D/results' "$SOURCE_PROJECT/" "$LINUX_PROJECT/" || fail "Project copy failed."
  else
    cp -a "$SOURCE_PROJECT/." "$LINUX_PROJECT/" || fail "Project copy failed."
  fi
  cd "$LINUX_PROJECT" || fail "Could not enter $LINUX_PROJECT."
else
  cd "$SOURCE_PROJECT" || fail "Could not enter $SOURCE_PROJECT."
fi
pwd
LOG_DIR="$PWD/Application Support/Logs"
mkdir -p "$LOG_DIR" CFD_2D/reports

echo "=== Minimal environment ==="
command -v python3 >/dev/null 2>&1 || fail "python3 missing"
command -v gmsh >/dev/null 2>&1 || fail "gmsh missing"
python3 - <<'PY' || fail "Missing numpy/pandas/matplotlib"
import numpy, pandas, matplotlib
print("Python scientific stack OK")
PY

if [ 1 -eq 1 ]; then
  source_openfoam_if_needed
  command -v gmshToFoam >/dev/null 2>&1 || fail "gmshToFoam missing"
  command -v checkMesh >/dev/null 2>&1 || fail "checkMesh missing"
fi

VARIANT=reference_uncut
MESH_DIR="$PWD/CFD_2D/meshes/$VARIANT"
ALPHAS="4.0"

echo "=== Preprocess and case package ==="
if [ 1 -eq 1 ]; then
  python3 preprocess_ramair_main.py --config "Application Support/Configurations/default_case_config.json" 2>&1 | tee "$LOG_DIR/${VARIANT}_workflow_01_preprocess.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Preprocessor failed"
fi
if [ 1 -eq 1 ]; then
  python3 CFD_2D/scripts/ramair_2d_profile_case_builder.py --case-root . --variant "$VARIANT" --alpha-start 4.0 --alpha-end 4.0 --alpha-step 1 --reynolds 4000000.0 --mach 0.1 --rho 1.225 --mu 1.81e-05 --velocity auto --overwrite --validate 2>&1 | tee "$LOG_DIR/${VARIANT}_workflow_02_case_builder.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Case builder failed"
fi
pause_step

echo "=== Review editable mesh parameters ==="
review_mesh_config_before_gmsh "$VARIANT"

echo "=== Mesh ==="
while true; do
  handle_existing_path "$MESH_DIR" reference_uncut mesh ask
  python3 CFD_2D/scripts/ramair_2d_mesh_builder.py --case-root . --variant reference_uncut --domain ross_cgrid_like --mesh-level debug --gmsh-timeout-s 900 --plot --overwrite --write-openfoam-mesh --check-mesh --openfoam-tool-timeout-s 600 2>&1 | tee "$LOG_DIR/${VARIANT}_workflow_03_mesh.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Mesh builder failed"
  open_path "$MESH_DIR"
  echo "Optional visual check: gmsh \"$MESH_DIR/mesh_final.msh\""
  echo "Type REMESH to edit cfd2d_mesh_config.json and regenerate this mesh now."
  echo "Press Enter to continue."
  read -r -p "Mesh decision [REMESH/continue]: " REMESH_CHOICE
  if [ "$REMESH_CHOICE" = "REMESH" ]; then
    review_mesh_config_before_gmsh "$VARIANT"
    continue
  fi
  break
done
pause_step

if [ 1 -eq 1 ]; then
  echo "Type APPROVE to approve a checkMesh-OK mesh, or FORCE_DEBUG for software debugging only."
  read -r -p "Mesh approval: " APPROVAL
  if [ "$APPROVAL" = "APPROVE" ]; then
    python3 CFD_2D/scripts/ramair_2d_mesh_builder.py --case-root . --variant "$VARIANT" --approve-mesh
  elif [ "$APPROVAL" = "FORCE_DEBUG" ]; then
    python3 CFD_2D/scripts/ramair_2d_mesh_builder.py --case-root . --variant "$VARIANT" --approve-mesh --force-approve
  else
    fail "Mesh was not approved"
  fi
fi

for ALPHA in $ALPHAS; do
  CASE_NAME="$(python3 - <<PY
alpha=float("$ALPHA")
sign="p" if alpha >= 0 else "m"
print(f"alpha_{sign}{abs(alpha):.3f}".replace(".", "p"))
PY
)"
  CASE_DIR="$PWD/CFD_2D/openfoam_cases/$VARIANT/$CASE_NAME"
  RESULTS_DIR="$PWD/CFD_2D/results/$VARIANT/$CASE_NAME"
  handle_existing_path "$CASE_DIR" reference_uncut openfoam_case ask
  handle_existing_path "$RESULTS_DIR" reference_uncut results ask

  echo "=== Write OpenFOAM case for alpha=$ALPHA ==="
  python3 CFD_2D/scripts/ramair_2d_openfoam_case_writer.py --case-root . --variant "$VARIANT" --alpha "$ALPHA" --reynolds 4000000.0 --write-case --require-converted-polymesh --overwrite 2>&1 | tee "$LOG_DIR/${VARIANT}_${CASE_NAME}_workflow_04_case_writer.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Case writer failed"
  open_path "$CASE_DIR"

  echo "=== Runner dry-run ==="
  python3 CFD_2D/scripts/ramair_2d_openfoam_runner.py --case "$CASE_DIR" --solver auto 2>&1 | tee "$LOG_DIR/${VARIANT}_${CASE_NAME}_workflow_05_runner_dry.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Runner dry-run failed"
  pause_step

  if [ 0 -eq 1 ]; then
    RUN_ARGS=(--case "$CASE_DIR" --solver auto --run --n-cores 1 --timeout-min 30.0)
    STOP_AFTER_MIN=''
    if [ -n "$STOP_AFTER_MIN" ]; then
      RUN_ARGS+=(--stop-after-min "$STOP_AFTER_MIN" --stop-grace-min 2.0 --stop-mode writeNow)
    fi
    if [ 0 -eq 1 ]; then
      RUN_ARGS+=(--no-stop-if-checkMesh-fails)
    fi
    python3 CFD_2D/scripts/ramair_2d_openfoam_runner.py "${RUN_ARGS[@]}" 2>&1 | tee "$LOG_DIR/${VARIANT}_${CASE_NAME}_workflow_06_solver.log"
    [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Solver run failed"
  fi

  echo "=== Postprocess alpha=$ALPHA ==="
  python3 CFD_2D/scripts/ramair_2d_postprocess.py --case-root . --variant reference_uncut --alpha "$ALPHA" --open-results-folder --openfoam-postprocess-timeout-s 300 2>&1 | tee "$LOG_DIR/${VARIANT}_${CASE_NAME}_workflow_07_postprocess.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Postprocess failed"
done

echo "Workflow finished."
