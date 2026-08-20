#!/usr/bin/env bash

# Ram-air CFD 2D open_ramair thin-solid Gmsh debug flow.
# Run from Ubuntu/WSL with:
#   bash "Documents and Manuals/Application/run_open_ramair_debug_wsl.sh"
#
# This is intentionally a short Gmsh mesh/geometry debug flow. It writes the
# open profile as a finite-thickness fabric band, so the exterior and internal
# cavity are one connected fluid region through the inlet gap. It does not run
# OpenFOAM automatically.

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

archive_existing_mesh_outputs() {
  local mesh_dir="$1"
  local variant="$2"
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
  backup_dir="$backup_root/${variant}_$stamp"
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
open_keys = [
    "open_wall_curve_method",
    "open_use_yplus_first_cell_height",
    "open_first_cell_height_chord",
    "open_boundary_layer_layers",
    "open_boundary_layer_growth",
    "open_boundary_layer_total_thickness_chord",
    "open_boundary_layer_fan_at_lips",
    "open_boundary_layer_lip_fan_points",
    "open_minimum_fabric_thickness_chord",
    "open_near_wall_size_from_bl",
    "open_near_wall_size_chord",
    "open_near_wall_size_bl_factor",
    "open_surface_target_nodes",
    "open_surface_transfinite_multiplier",
    "open_te_transfinite_min_nodes",
    "open_lip_transfinite_min_nodes",
    "open_inlet_marker_transfinite_nodes",
    "open_surface_size_le_chord",
    "open_surface_size_lip_chord",
    "open_surface_size_te_chord",
    "open_internal_inlet_size_chord",
    "open_cavity_size_chord",
    "open_farfield_size_chord",
]
keys = common + (open_keys if variant == "open_ramair" else [])
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
    cp -a "$SOURCE_PROJECT/." "$LINUX_PROJECT/" || fail "Project copy with cp failed."
  fi
  cd "$LINUX_PROJECT" || fail "Could not enter $LINUX_PROJECT."
else
  cd "$SOURCE_PROJECT" || fail "Could not enter $SOURCE_PROJECT."
fi

echo "Working directory:"
pwd
echo "Important: for normal Gmsh runs this path should not start with /mnt/c."
LOG_DIR="$PWD/Application Support/Logs"
mkdir -p "$LOG_DIR" CFD_2D/reports

pause_step

step "Check minimal environment"
configure_gmsh
command -v python3 >/dev/null 2>&1 || fail "python3 is missing in Ubuntu/WSL."
python3 - <<'PY' || fail "Missing Python packages. Install manually: numpy pandas matplotlib"
import numpy
import pandas
import matplotlib
print("Python scientific stack OK")
PY
command -v gmsh >/dev/null 2>&1 || fail "gmsh executable is missing in Ubuntu/WSL."
gmsh --version
python3 CFD_2D/scripts/check_environment.py

pause_step

step "Run preprocessor"
python3 preprocess_ramair_main.py --config "Application Support/Configurations/default_case_config.json" 2>&1 | tee "$LOG_DIR/open_ramair_01_preprocess.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "Preprocessor failed. See logs/open_ramair_01_preprocess.log"

PROFILE_USED_DIR="$PWD/CATIA/Inputs/Profile_used"
if [ -d "$PROFILE_USED_DIR" ]; then
  open_path "$PROFILE_USED_DIR"
  echo "Check the rounded TE geometry before building the open profile package if needed:"
  echo "  $PROFILE_USED_DIR/ramair_profile_used_cfd_contour_te_zoom.png"
  echo "  $PROFILE_USED_DIR/ramair_profile_used_cfd_contour_preview.png"
fi

pause_step

step "Build CFD 2D input package for open_ramair"
python3 CFD_2D/scripts/ramair_2d_profile_case_builder.py \
  --case-root . \
  --variant open_ramair \
  --overwrite \
  --validate \
  --plot 2>&1 | tee "$LOG_DIR/open_ramair_02_case_builder.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "Case builder failed. See logs/open_ramair_02_case_builder.log"

CASE_INPUT_DIR="$PWD/CFD_2D/CFD_2D_inputs/case_package/open_ramair"
find "$CASE_INPUT_DIR" -maxdepth 2 -type f | sort || true

pause_step

step "Review editable mesh parameters before Gmsh"
review_mesh_config_before_gmsh "open_ramair"

MESH_DIR="$PWD/CFD_2D/meshes/open_ramair"
while true; do
  step "Archive old open_ramair mesh output and generate fresh thin-solid Gmsh mesh"
  archive_existing_mesh_outputs "$MESH_DIR" "open_ramair"

  python3 CFD_2D/scripts/ramair_2d_mesh_builder.py \
    --case-root . \
    --variant open_ramair \
    --domain ross_cgrid_like \
    --mesh-level debug \
    --write-2d-mesh \
    --no-gmsh-temp-workdir \
    --gmsh-timeout-s 900 \
    --plot \
    --overwrite \
    --previous-output-action keep 2>&1 | tee "$LOG_DIR/open_ramair_03_gmsh_thin_solid.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Open thin-solid Gmsh mesh failed. See logs/open_ramair_03_gmsh_thin_solid.log and $MESH_DIR/log.gmsh"

  echo
  echo "Open-profile thin-solid mesh files:"
  find "$MESH_DIR" -maxdepth 3 -type f | sort || true
  ls -lh "$MESH_DIR/mesh_final.geo" "$MESH_DIR/mesh_final.msh" 2>/dev/null || true
  tail -n 120 "$MESH_DIR/log.gmsh" 2>/dev/null || true

  open_path "$MESH_DIR"
  echo "Optional visual files are in the mesh folder; open them manually if needed:"
  echo "  $MESH_DIR/open_te_rounding_geometry_zoom.png"
  echo "  $MESH_DIR/mesh_preview_front_surface.png"
  echo "  $MESH_DIR/mesh_preview_inlet.png"
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
  echo "Press Enter to continue to the debug stop."
  read -r -p "Mesh decision [REMESH/continue]: " REMESH_CHOICE
  if [ "$REMESH_CHOICE" = "REMESH" ]; then
    step "Edit mesh parameters for remesh"
    review_mesh_config_before_gmsh "open_ramair"
    continue
  fi
  break
done

pause_step

step "Debug stop"
cat <<'TXT'
The open_ramair mesh generated here uses a finite-thickness fabric band:
  - the inlet gap is fluid, never a physical ram_air_inlet patch;
  - exterior and internal cavity are one connected fluid surface;
  - BoundaryLayer is requested only on exterior fabric wall sections;
  - the inlet is fluid and locally refined with an embedded non-physical sizing bridge, not a wall patch;
  - rectangular LE/lip refinement boxes are disabled for this lightweight debug case.

This script stops after Gmsh so you can inspect mesh_final.geo/msh manually.
Use OpenFOAM conversion only after visual inspection and a dedicated 3D extrusion/checkMesh step.
TXT
