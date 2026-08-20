#!/usr/bin/env bash
# Remove only verified, reproducible WSL runtime residue.
set -euo pipefail

RUNTIME_ROOT="${RAMAIR_RUNTIME_ROOT:-/home/alejm/ramair_cfd}"
APP_ROOT="$RUNTIME_ROOT/DESIGN_APP"
REPORT="$APP_ROOT/Application Support/Reports/storage_cleanup_20260729.tsv"

require_within_runtime() {
  local target resolved
  target="$1"
  resolved="$(realpath -m -- "$target")"
  case "$resolved" in
    "$RUNTIME_ROOT"/*) printf '%s\n' "$resolved" ;;
    *) echo "Refusing path outside runtime: $resolved" >&2; return 1 ;;
  esac
}

remove_tree() {
  local target reason bytes resolved
  target="$1"
  reason="$2"
  [ -e "$target" ] || return 0
  resolved="$(require_within_runtime "$target")"
  bytes="$(du -sb -- "$resolved" | cut -f1)"
  rm -rf -- "$resolved"
  printf '%s\t%s\t%s\n' "$bytes" "$reason" "$resolved" >> "$REPORT"
}

mkdir -p -- "$(dirname "$REPORT")"
printf 'bytes\treason\tpath\n' > "$REPORT"

# These standalone scratch roots were created by the 2026-07-27 parameter
# studies. Their compact configs, logs and conclusions are retained under
# Application Support/Reports and CFD_2D/reports.
remove_tree "$RUNTIME_ROOT/open_mesh_study_20260727" \
  "external_mesh_sweep_preserved_as_compact_evidence"
remove_tree "$RUNTIME_ROOT/fixed_dt_study_20260727" \
  "external_fixed_dt_sweep_preserved_as_compact_evidence"
remove_tree "$RUNTIME_ROOT/validation_tmp" "obsolete_validation_fixture"

# The launcher and every active tool use .venv-cfd2d-ui. The old environment
# is explicitly excluded from portable packages and has no runtime references.
remove_tree "$APP_ROOT/.venv-cfd2d" "unreferenced_legacy_virtual_environment"

# Preserve each historical trial's config and quality diagnostics, then remove
# only the embedded complete project/mesh copy.
TRIALS="$APP_ROOT/CFD_2D/reports/mesh_studies/2026-07-27_open_user_baseline_299k/trials"
if [ -d "$TRIALS" ]; then
  while IFS= read -r -d '' trial; do
    mesh="$trial/root/CFD_2D/meshes/open_ramair_validation_1m"
    if [ -d "$mesh" ]; then
      for name in mesh_quality_report.json mesh_quality_report.txt \
                  mesh_engineering_assessment.json profile_mesh_audit.json \
                  log.checkMesh log.gmsh; do
        [ -f "$mesh/$name" ] && cp -f -- "$mesh/$name" "$trial/$name"
      done
      remove_tree "$trial/root" "embedded_historical_mesh_trial_copy"
    fi
  done < <(find "$TRIALS" -mindepth 1 -maxdepth 1 -type d -print0)
fi

# Old mesh backups are never loaded by the application. Keep their geometry,
# settings, logs and quality reports, but discard regenerated MSH and polyMesh.
MESH_BACKUPS="$APP_ROOT/Previous Versions/mesh_backups"
if [ -d "$MESH_BACKUPS" ]; then
  while IFS= read -r -d '' polymesh; do
    remove_tree "$polymesh" "regenerable_polymesh_in_historical_mesh_backup"
  done < <(find "$MESH_BACKUPS" -type d -name polyMesh -print0)
  while IFS= read -r -d '' msh; do
    resolved="$(require_within_runtime "$msh")"
    bytes="$(stat -c %s -- "$resolved")"
    rm -f -- "$resolved"
    printf '%s\t%s\t%s\n' "$bytes" \
      "regenerable_msh_in_historical_mesh_backup" "$resolved" >> "$REPORT"
  done < <(find "$MESH_BACKUPS" -type f -name '*.msh' -print0)
fi

# VTK exports in OpenFOAM backups are reproducible from retained time fields.
# Delete only when a valid polyMesh and at least one U/p time are present.
CASE_BACKUPS="$APP_ROOT/Previous Versions/openfoam_case_backups"
if [ -d "$CASE_BACKUPS" ]; then
  while IFS= read -r -d '' vtk; do
    case_root="$(dirname "$vtk")"
    valid_time=""
    while IFS= read -r -d '' time_dir; do
      if { [ -f "$time_dir/U" ] || [ -f "$time_dir/U.gz" ]; } &&
         { [ -f "$time_dir/p" ] || [ -f "$time_dir/p.gz" ]; }; then
        valid_time="$time_dir"
        break
      fi
    done < <(
      find "$case_root" -mindepth 1 -maxdepth 1 -type d \
        -regextype posix-extended -regex '.*/[0-9]+([.eE+-][0-9]+)*' -print0
    )
    if [ -f "$case_root/constant/polyMesh/boundary" ] &&
       [ -n "$valid_time" ]; then
      remove_tree "$vtk" "regenerable_vtk_in_openfoam_backup"
    fi
  done < <(
    find "$CASE_BACKUPS" -type d \
      \( -name VTK -o -name VTK_wall_fields \) -print0
  )
fi

printf 'Cleanup report: %s\n' "$REPORT"
