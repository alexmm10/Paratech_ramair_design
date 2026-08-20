#!/usr/bin/env bash
# Bounded diagnostic comparison for the open-airfoil inlet transition.
set -uo pipefail

ROOT="${1:-$HOME/ramair_cfd/DESIGN APP}"
TRIAL_SET="${2:-all}"
cd "$ROOT" || exit 2
STAMP=$(date +%Y%m%d_%H%M%S)
REPORT_ROOT="$ROOT/CFD_2D/reports/open_inlet_transition_trials_$STAMP"
mkdir -p "$REPORT_ROOT"
BASE="$ROOT/CFD_2D/CFD_2D_inputs/config/mesh_presets/open_ramair_debug_best_candidate.json"
SANDBOX="$REPORT_ROOT/sandbox"
mkdir -p "$SANDBOX/CFD_2D"
cp -a "$ROOT/CFD_2D/CFD_2D_inputs" "$SANDBOX/CFD_2D/CFD_2D_inputs"
PYTHON="$ROOT/.venv-cfd2d-ui/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON=$(command -v python3)
fi
echo "Python/Gmsh runtime: $PYTHON"
"$PYTHON" - "$BASE" "$REPORT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

base = json.load(open(sys.argv[1], encoding="utf-8"))
root = Path(sys.argv[2])
cases = {
    "baseline_growth122_inner70": {},
    "inner85_growth122": {
        "open_inner_wall_node_factor": 0.85,
        "open_inner_wall_min_nodes": 120,
        "open_inlet_transition_growth": 1.22,
    },
    "inner85_growth118": {
        "open_inner_wall_node_factor": 0.85,
        "open_inner_wall_min_nodes": 120,
        "open_inlet_transition_growth": 1.18,
    },
    "inner85_growth110": {
        "open_inner_wall_node_factor": 0.85,
        "open_inner_wall_min_nodes": 120,
        "open_inlet_transition_growth": 1.10,
    },
    "tri_n2_t4_n96": {
        "open_inlet_transition_elements": "triangles",
        "open_inlet_connector_normal_nodes": 2,
        "fabric_thickness_chord": 0.0004,
        "open_minimum_fabric_thickness_chord": 0.0004,
        "open_inlet_marker_transfinite_nodes": 96,
        "open_lip_transfinite_min_nodes": 96,
    },
    "quad_n2_t6_n96": {
        "open_inlet_transition_elements": "recombined_quads",
        "open_inlet_connector_normal_nodes": 2,
        "fabric_thickness_chord": 0.0006,
        "open_minimum_fabric_thickness_chord": 0.0006,
        "open_inlet_marker_transfinite_nodes": 96,
        "open_lip_transfinite_min_nodes": 96,
    },
    "quad_n3_t6_n128": {
        "open_inlet_transition_elements": "recombined_quads",
        "open_inlet_connector_normal_nodes": 3,
        "fabric_thickness_chord": 0.0006,
        "open_minimum_fabric_thickness_chord": 0.0006,
        "open_inlet_marker_transfinite_nodes": 128,
        "open_lip_transfinite_min_nodes": 96,
    },
    "transfinite_tri_n2_t6_n128": {
        "open_inlet_transition_elements": "transfinite_triangles",
        "open_inlet_connector_normal_nodes": 2,
        "fabric_thickness_chord": 0.0006,
        "open_minimum_fabric_thickness_chord": 0.0006,
        "open_inlet_marker_transfinite_nodes": 128,
        "open_lip_transfinite_min_nodes": 96,
    },
    "tri_n2_t6_n128": {
        "open_inlet_transition_elements": "triangles",
        "open_inlet_connector_normal_nodes": 2,
        "fabric_thickness_chord": 0.0006,
        "open_minimum_fabric_thickness_chord": 0.0006,
        "open_inlet_marker_transfinite_nodes": 128,
        "open_lip_transfinite_min_nodes": 96,
    },
    "triangular_no_bl_n96": {
        "open_inlet_boundary_layer_mode": "triangular_inlet_no_bl",
        "open_inlet_transition_elements": "triangles",
        "open_inlet_connector_normal_nodes": 2,
        "fabric_thickness_chord": 0.0004,
        "open_minimum_fabric_thickness_chord": 0.0004,
        "open_inlet_marker_transfinite_nodes": 96,
        "open_lip_transfinite_min_nodes": 96,
    },
    "algorithm1_meshadapt": {
        "gmsh_mesh_algorithm_2d": 1,
    },
    "algorithm6_frontal_delaunay": {
        "gmsh_mesh_algorithm_2d": 6,
    },
    "fan3_growth122": {
        "open_inlet_boundary_layer_mode": "full_prismatic_bridge_with_fans",
        "open_boundary_layer_lip_fan_points": 3,
        "open_inlet_transition_growth": 1.22,
    },
    "refined_lips_n176": {
        "open_lip_transfinite_min_nodes": 160,
        "open_inlet_marker_transfinite_nodes": 176,
        "open_internal_inlet_size_chord": 0.00035,
    },
    "refined_lip_nodes_only": {
        "open_lip_transfinite_min_nodes": 160,
        "open_inlet_marker_transfinite_nodes": 176,
    },
    "refined_inlet_size_only": {
        "open_internal_inlet_size_chord": 0.00035,
    },
    "handle060_auto_layers": {
        "open_inlet_bridge_smoothing_handle_fraction": 0.060,
        "open_inlet_connector_normal_nodes": 0,
    },
    "handle080_auto_layers": {
        "open_inlet_bridge_smoothing_handle_fraction": 0.080,
        "open_inlet_connector_normal_nodes": 0,
    },
    "handle100_auto_layers": {
        "open_inlet_bridge_smoothing_handle_fraction": 0.100,
        "open_inlet_connector_normal_nodes": 0,
    },
    "handle080_12_nodes": {
        "open_inlet_bridge_smoothing_handle_fraction": 0.080,
        "open_inlet_connector_normal_nodes": 12,
    },
    "handle080_16_nodes": {
        "open_inlet_bridge_smoothing_handle_fraction": 0.080,
        "open_inlet_connector_normal_nodes": 16,
    },
    "handle080_20_nodes": {
        "open_inlet_bridge_smoothing_handle_fraction": 0.080,
        "open_inlet_connector_normal_nodes": 20,
    },
}
for name, changes in cases.items():
    candidate = dict(base)
    candidate.update(changes)
    (root / f"{name}.json").write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
PY

set +u
for foam_file in /opt/openfoam*/etc/bashrc; do
  if [ -f "$foam_file" ]; then
    # shellcheck disable=SC1090
    . "$foam_file"
    break
  fi
done
set -u

if [ "$TRIAL_SET" = "inner-wall" ]; then
  NAMES="baseline_growth122_inner70 inner85_growth122 inner85_growth118 inner85_growth110"
elif [ "$TRIAL_SET" = "best" ]; then
  NAMES="tri_n2_t4_n96"
elif [ "$TRIAL_SET" = "isotropic" ]; then
  NAMES="tri_n2_t6_n128"
elif [ "$TRIAL_SET" = "transfinite" ]; then
  NAMES="transfinite_tri_n2_t6_n128"
elif [ "$TRIAL_SET" = "no_bl" ]; then
  NAMES="triangular_no_bl_n96"
elif [ "$TRIAL_SET" = "quality" ]; then
  NAMES="algorithm1_meshadapt algorithm6_frontal_delaunay fan3_growth122 refined_lips_n176"
elif [ "$TRIAL_SET" = "quality-isolate" ]; then
  NAMES="refined_lip_nodes_only refined_inlet_size_only"
elif [ "$TRIAL_SET" = "curvature-layers" ]; then
  NAMES="handle060_auto_layers handle080_auto_layers handle100_auto_layers handle080_12_nodes handle080_16_nodes handle080_20_nodes"
else
  NAMES="baseline_growth122_inner70 inner85_growth122 inner85_growth118 inner85_growth110 tri_n2_t4_n96 quad_n2_t6_n96 quad_n3_t6_n128 transfinite_tri_n2_t6_n128 tri_n2_t6_n128 triangular_no_bl_n96 algorithm1_meshadapt algorithm6_frontal_delaunay fan3_growth122 refined_lips_n176"
fi
for NAME in $NAMES; do
  echo "=== CANDIDATE $NAME ==="
  START=$(date +%s)
  set +e
  "$PYTHON" "$ROOT/CFD_2D/scripts/ramair_2d_mesh_builder.py" \
    --case-root "$SANDBOX" --variant open_ramair --domain debug_20c \
    --mesh-level debug --mesh-config "$REPORT_ROOT/$NAME.json" \
    --gmsh-backend python_api --gmsh-threads 8 --gmsh-timeout-s 900 \
    --openfoam-tool-timeout-s 600 --previous-output-action delete \
    --overwrite --write-openfoam-mesh --check-mesh \
    > "$REPORT_ROOT/$NAME.console.log" 2>&1
  RC=$?
  set -e
  ELAPSED=$(($(date +%s) - START))
  mkdir -p "$REPORT_ROOT/$NAME"
  for FILE in mesh_quality_report.json mesh_quality_report.txt checkMesh_problem_locations.json log.gmsh log.gmshToFoam log.checkMesh; do
    if [ -f "$SANDBOX/CFD_2D/meshes/open_ramair/$FILE" ]; then
      cp "$SANDBOX/CFD_2D/meshes/open_ramair/$FILE" "$REPORT_ROOT/$NAME/$FILE"
    fi
  done
  "$PYTHON" - "$NAME" "$RC" "$ELAPSED" "$REPORT_ROOT/$NAME/mesh_quality_report.json" <<'PY'
import json
import sys

name, return_code, elapsed, path = sys.argv[1:]
try:
    report = json.load(open(path, encoding="utf-8"))
except Exception as exc:
    print(f"RESULT {name}: rc={return_code} elapsed={elapsed}s no report: {exc}")
    raise SystemExit()
keys = [
    "checkMesh_status", "checkMesh_cell_count", "checkMesh_failed_checks",
    "checkMesh_max_non_orthogonality_deg", "checkMesh_max_skewness",
    "checkMesh_min_cell_determinant", "checkMesh_min_face_interpolation_weight",
    "checkMesh_small_interpolation_weight_faces", "checkMesh_min_face_volume_ratio",
]
print(f"RESULT {name}: rc={return_code} elapsed={elapsed}s")
for key in keys:
    print(f"  {key}={report.get(key)}")
PY
done
rm -rf "$SANDBOX"
echo "REPORT_ROOT=$REPORT_ROOT"
