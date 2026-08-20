#!/usr/bin/env python3
"""Interactive/configurable ram-air CFD 2D workflow helper.

This tool does not implement new physics. It centralizes user-facing choices and
writes a reproducible WSL Bash script that calls the existing preprocessor,
profile case builder, Gmsh mesh builder, OpenFOAM case writer/runner and
postprocessor in the correct order.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

OPEN_VARIANTS = {"open_ramair", "ross_standard_8p4", "ross_minimum_4p0", "standard", "optimized"}

DEFAULT_WORKFLOW_CONFIG: dict[str, Any] = {
    "version": 1,
    "geometry": {
        "variant": "reference_uncut",
        "domain": "ross_cgrid_like",
        "run_preprocessor": True,
        "rebuild_case_package": True,
    },
    "case_conditions": {
        "alphas_deg": [4.0],
        "reynolds": 4000000.0,
        "mach": 0.10,
        "rho_kg_m3": 1.225,
        "mu_pa_s": 1.81e-5,
        "velocity": "auto",
    },
    "mesh": {
        "mesh_level": "debug",
        "gmsh_backend": "auto",
        "write_openfoam_mesh": True,
        "open_diagnostic_mesh": False,
        "gmsh_timeout_s": 900,
        "openfoam_tool_timeout_s": 600,
        "gmsh_threads": None,
        "plot": True,
        "overwrite": True,
    },
    "execution": {
        "run_solver": False,
        "execution_backend": "pyfoam",
        "solver": "auto",
        "n_cores": 6,
        "timeout_min": 120,
        "stop_after_min": None,
        "stop_grace_min": 5,
        "stop_mode": "writeNow",
        "allow_failed_checkmesh_for_debug": False,
    },
    "postprocess": {
        "run_openfoam_postprocess": True,
        "export_mode": "latest_vtk",
        "open_results_folder": True,
        "open_paraview": False,
        "timeout_s": 300,
    },
    "safeguards": {
        "existing_mesh_action": "ask",
        "existing_case_results_action": "ask",
        "pause_after_geometry": True,
        "pause_after_mesh": True,
        "pause_before_solver": True,
        "require_manual_mesh_approval": True,
        "copy_to_native_wsl_fs": True,
    },
}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Could not parse JSON config {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def project_root(case_root: Path) -> Path:
    return Path(case_root).resolve()


def geometry_root(root: Path) -> Path:
    return root / "CFD_2D" / "CFD_2D_inputs" / "geometry"


def case_package_root(root: Path) -> Path:
    return root / "CFD_2D" / "CFD_2D_inputs" / "case_package"


def mesh_config_path(root: Path) -> Path:
    return root / "CFD_2D" / "CFD_2D_inputs" / "config" / "cfd2d_mesh_config.json"


def mesh_settings(root: Path) -> dict[str, Any]:
    return read_json(mesh_config_path(root), {}) or {}


def available_geometry(root: Path) -> list[str]:
    names: set[str] = set()
    for base in [geometry_root(root), case_package_root(root)]:
        if not base.exists():
            continue
        for child in base.iterdir():
            if child.is_dir() and child.name != "validation":
                names.add(child.name)
    return sorted(names)


def alpha_case_name(alpha: float) -> str:
    sign = "p" if alpha >= 0 else "m"
    return f"alpha_{sign}{abs(alpha):.3f}".replace(".", "p")


def q(value: Any) -> str:
    return shlex.quote(str(value))


def parse_float_list(values: Any) -> list[float]:
    if isinstance(values, list):
        return [float(v) for v in values]
    if isinstance(values, str):
        return [float(v.strip()) for v in values.split(",") if v.strip()]
    return [float(values)]


def validate_config(root: Path, cfg: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    variant = str(cfg["geometry"].get("variant", ""))
    names = available_geometry(root)
    if names and variant not in names:
        warnings.append(f"Selected variant '{variant}' is not currently available. Available: {', '.join(names)}")
    if variant in OPEN_VARIANTS:
        detailed_mesh = mesh_settings(root)
        if cfg["mesh"].get("open_diagnostic_mesh"):
            warnings.append("Open profile diagnostic fallback selected: zero-thickness diagnostic mode is not solver-ready.")
        elif cfg["mesh"].get("write_openfoam_mesh"):
            warnings.append("Open profile selected with OpenFOAM mesh writing: thin-solid 3D extrusion is experimental; inspect gmshToFoam/checkMesh before running a solver.")
        if detailed_mesh.get("open_boundary_layer_include_inlet_bridge"):
            warnings.append(
                "Experimental inlet bridge BoundaryLayer is enabled. Gmsh 4.8.4 cannot robustly mesh this internal bridge; "
                "the supported debug preset keeps it false and refines the inlet without claiming prismatic layers there."
            )
        if int(detailed_mesh.get("open_boundary_layer_layers", 50) or 0) > 75:
            warnings.append(
                "Open debug BL requests more than 75 layers. Use a strict timeout and validate manually."
            )
    export_mode = str(cfg["postprocess"].get("export_mode", "latest_vtk"))
    if export_mode not in {"none", "coefficients_only", "latest_vtk", "all_vtk"}:
        warnings.append(f"Unknown export_mode '{export_mode}', using latest_vtk in generated script.")
        cfg["postprocess"]["export_mode"] = "latest_vtk"
    return warnings


def print_geometry(root: Path) -> None:
    print("Available CFD 2D geometries:")
    names = available_geometry(root)
    if not names:
        print("  (none found yet; run preprocess_ramair_main.py first)")
        return
    for name in names:
        marker = "open thin-solid capable" if name in OPEN_VARIANTS else "closed/reference"
        print(f"  - {name} ({marker})")


def describe_plan(root: Path, cfg: dict[str, Any]) -> None:
    validate_config(root, cfg)
    variant = cfg["geometry"]["variant"]
    alphas = parse_float_list(cfg["case_conditions"].get("alphas_deg", [4.0]))
    print("Workflow plan")
    print("=============")
    print(f"Project root: {root}")
    print(f"Geometry: {variant}")
    print(f"Domain: {cfg['geometry'].get('domain')}")
    print(f"Angles: {', '.join(f'{a:g}' for a in alphas)} deg")
    print(f"Reynolds: {cfg['case_conditions'].get('reynolds')}")
    print(f"Mach input: {cfg['case_conditions'].get('mach')}")
    print(f"Mesh level: {cfg['mesh'].get('mesh_level')}")
    print(f"Open diagnostic fallback: {cfg['mesh'].get('open_diagnostic_mesh')}")
    print(f"Write OpenFOAM mesh: {cfg['mesh'].get('write_openfoam_mesh')}")
    print(f"Run solver: {cfg['execution'].get('run_solver')}")
    print(f"Postprocess export mode: {cfg['postprocess'].get('export_mode')}")
    detailed_mesh = mesh_settings(root)
    if detailed_mesh:
        print(f"Wall curve method (closed/open): {detailed_mesh.get('closed_wall_curve_method')} / {detailed_mesh.get('open_wall_curve_method')}")
        print(f"BL layers (closed/open): {detailed_mesh.get('closed_boundary_layer_layers')} / {detailed_mesh.get('open_boundary_layer_layers')}")
        print(f"BL growth (closed/open): {detailed_mesh.get('closed_boundary_layer_growth')} / {detailed_mesh.get('open_boundary_layer_growth')}")
        print(f"Open fabric thickness: {detailed_mesh.get('open_minimum_fabric_thickness_chord')} c")
        print(f"Open lip fans: {detailed_mesh.get('open_boundary_layer_fan_at_lips')} ({detailed_mesh.get('open_boundary_layer_lip_fan_points')} points)")
        print(f"Farfield target size (closed/open): {detailed_mesh.get('closed_farfield_size_chord')} c / {detailed_mesh.get('open_farfield_size_chord')} c")
    print("")
    print("Main editable files:")
    print("  CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json")
    print("  CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json")
    print("  CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json")
    print("  CFD_2D/CFD_2D_inputs/config/cfd2d_physical_defaults.json")


def prompt_bool(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    ans = input(f"{label} [{suffix}]: ").strip().lower()
    if not ans:
        return default
    return ans in {"y", "yes", "s", "si", "true", "1"}


def run_interactive(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    print_geometry(root)
    variant = input(f"Geometry variant [{cfg['geometry']['variant']}]: ").strip()
    if variant:
        cfg["geometry"]["variant"] = variant
    domain = input(f"Domain [{cfg['geometry']['domain']}]: ").strip()
    if domain:
        cfg["geometry"]["domain"] = domain
    alphas = input(f"Angles deg comma-separated [{','.join(map(str, cfg['case_conditions']['alphas_deg']))}]: ").strip()
    if alphas:
        cfg["case_conditions"]["alphas_deg"] = parse_float_list(alphas)
    reynolds = input(f"Reynolds [{cfg['case_conditions']['reynolds']}]: ").strip()
    if reynolds:
        cfg["case_conditions"]["reynolds"] = float(reynolds)
    mach = input(f"Mach input [{cfg['case_conditions']['mach']}]: ").strip()
    if mach:
        cfg["case_conditions"]["mach"] = float(mach)
    mesh_level = input(f"Mesh level debug/coarse/medium/fine/ross_like [{cfg['mesh']['mesh_level']}]: ").strip()
    if mesh_level:
        cfg["mesh"]["mesh_level"] = mesh_level
    cfg["execution"]["run_solver"] = prompt_bool("Run OpenFOAM solver after case writing", bool(cfg["execution"]["run_solver"]))
    timeout = input(f"Solver timeout min [{cfg['execution']['timeout_min']}]: ").strip()
    if timeout:
        cfg["execution"]["timeout_min"] = float(timeout)
    stop_after = input(f"Clean stop/writeNow after min [{cfg['execution'].get('stop_after_min') or 'disabled'}]: ").strip()
    if stop_after:
        cfg["execution"]["stop_after_min"] = float(stop_after)
    export_mode = input(f"Export mode none/coefficients_only/latest_vtk/all_vtk [{cfg['postprocess']['export_mode']}]: ").strip()
    if export_mode:
        cfg["postprocess"]["export_mode"] = export_mode
    cfg["postprocess"]["open_paraview"] = prompt_bool("Open ParaView/paraFoam after postprocess", bool(cfg["postprocess"]["open_paraview"]))
    validate_config(root, cfg)
    return cfg


def action_block(path_var: str, variant: str, action: str, label: str) -> str:
    safe_action = action if action in {"ask", "archive", "delete", "stop"} else "ask"
    return f'handle_existing_path "{path_var}" {q(variant)} {q(label)} {q(safe_action)}'


def generate_bash(root: Path, cfg: dict[str, Any]) -> str:
    validate_config(root, cfg)
    geom = cfg["geometry"]
    cond = cfg["case_conditions"]
    mesh = cfg["mesh"]
    exe = cfg["execution"]
    post = cfg["postprocess"]
    safe = cfg["safeguards"]
    variant = str(geom["variant"])
    is_open = variant in OPEN_VARIANTS
    alphas = parse_float_list(cond.get("alphas_deg", [4.0]))
    alpha_str = " ".join(str(a) for a in alphas)

    mesh_args = [
        "python3 CFD_2D/scripts/ramair_2d_mesh_builder.py",
        "--case-root .",
        f"--variant {q(variant)}",
        f"--domain {q(geom.get('domain', 'ross_cgrid_like'))}",
        f"--mesh-level {q(mesh.get('mesh_level', 'debug'))}",
        f"--gmsh-backend {q(mesh.get('gmsh_backend', 'auto'))}",
        f"--gmsh-timeout-s {int(mesh.get('gmsh_timeout_s', 900))}",
    ]
    if mesh.get("plot", True):
        mesh_args.append("--plot")
    if mesh.get("overwrite", True):
        mesh_args.append("--overwrite")
    if mesh.get("gmsh_threads") is not None:
        mesh_args.append(f"--gmsh-threads {int(mesh['gmsh_threads'])}")
    if mesh.get("open_diagnostic_mesh"):
        mesh_args.extend(["--open-diagnostic-mesh", "--write-2d-mesh"])
    elif is_open and not mesh.get("write_openfoam_mesh", False):
        mesh_args.append("--write-2d-mesh")
    elif mesh.get("write_openfoam_mesh", True):
        mesh_args.extend(["--write-openfoam-mesh", "--check-mesh", f"--openfoam-tool-timeout-s {int(mesh.get('openfoam_tool_timeout_s', 600))}"])

    will_write_openfoam_mesh = bool(mesh.get("write_openfoam_mesh", True)) and not bool(mesh.get("open_diagnostic_mesh", False))
    will_run_solver = bool(exe.get("run_solver", False)) and will_write_openfoam_mesh
    needs_openfoam_tools = will_write_openfoam_mesh or will_run_solver
    post_args = [
        "python3 CFD_2D/scripts/ramair_2d_postprocess.py",
        "--case-root .",
        f"--variant {q(variant)}",
        "--alpha \"$ALPHA\"",
    ]
    if post.get("run_openfoam_postprocess", True) and will_run_solver:
        post_args.append("--run-openfoam-postprocess")
    export_mode = str(post.get("export_mode", "latest_vtk"))
    if export_mode in {"latest_vtk", "all_vtk"} and will_run_solver:
        post_args.append("--export-vtk")
    if export_mode == "all_vtk" and will_run_solver:
        post_args.append("--export-vtk-all-times")
    if post.get("open_results_folder", True):
        post_args.append("--open-results-folder")
    if post.get("open_paraview", False):
        post_args.append("--open-paraview")
    post_args.append(f"--openfoam-postprocess-timeout-s {int(post.get('timeout_s', 300))}")

    pause_after_geometry = "pause_step" if safe.get("pause_after_geometry", True) else ":"
    pause_after_mesh = "pause_step" if safe.get("pause_after_mesh", True) else ":"
    pause_before_solver = "pause_step" if safe.get("pause_before_solver", True) else ":"
    run_solver_flag = "1" if will_run_solver else "0"
    allow_failed_checkmesh = "1" if exe.get("allow_failed_checkmesh_for_debug", False) else "0"
    require_approval = "1" if safe.get("require_manual_mesh_approval", True) and will_write_openfoam_mesh else "0"

    if is_open and not will_write_openfoam_mesh:
        post_mesh_workflow = """echo "Open profile Gmsh-only workflow finished."
echo "Inspect mesh_final.msh manually. Enable mesh.write_openfoam_mesh only after the thin-solid mesh is acceptable."
echo "Workflow finished."
"""
    else:
        post_mesh_workflow = f"""if [ {require_approval} -eq 1 ]; then
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
print(f"alpha_{{sign}}{{abs(alpha):.3f}}".replace(".", "p"))
PY
)"
  CASE_DIR="$PWD/CFD_2D/openfoam_cases/$VARIANT/$CASE_NAME"
  RESULTS_DIR="$PWD/CFD_2D/results/$VARIANT/$CASE_NAME"
  {action_block('$CASE_DIR', variant, str(safe.get('existing_case_results_action', 'ask')), 'openfoam_case')}
  {action_block('$RESULTS_DIR', variant, str(safe.get('existing_case_results_action', 'ask')), 'results')}

  echo "=== Write OpenFOAM case for alpha=$ALPHA ==="
  python3 CFD_2D/scripts/ramair_2d_openfoam_case_writer.py --case-root . --variant "$VARIANT" --alpha "$ALPHA" --reynolds {float(cond.get('reynolds', 4e6))} --write-case --require-converted-polymesh --overwrite 2>&1 | tee "$LOG_DIR/${{VARIANT}}_${{CASE_NAME}}_workflow_04_case_writer.log"
  [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Case writer failed"
  open_path "$CASE_DIR"

  echo "=== Runner dry-run ==="
  python3 CFD_2D/scripts/ramair_2d_openfoam_runner.py --case "$CASE_DIR" --solver {q(exe.get('solver', 'auto'))} --execution-backend {q(exe.get('execution_backend', 'native'))} 2>&1 | tee "$LOG_DIR/${{VARIANT}}_${{CASE_NAME}}_workflow_05_runner_dry.log"
  [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Runner dry-run failed"
  {pause_before_solver}

  if [ {run_solver_flag} -eq 1 ]; then
    RUN_ARGS=(--case "$CASE_DIR" --solver {q(exe.get('solver', 'auto'))} --execution-backend {q(exe.get('execution_backend', 'pyfoam'))} --run --n-cores {int(exe.get('n_cores', 6))} --timeout-min {float(exe.get('timeout_min', 120))})
    STOP_AFTER_MIN={q(exe.get('stop_after_min', '') or '')}
    if [ -n "$STOP_AFTER_MIN" ]; then
      RUN_ARGS+=(--stop-after-min "$STOP_AFTER_MIN" --stop-grace-min {float(exe.get('stop_grace_min', 2))} --stop-mode {q(exe.get('stop_mode', 'writeNow'))})
    fi
    if [ {allow_failed_checkmesh} -eq 1 ]; then
      RUN_ARGS+=(--no-stop-if-checkMesh-fails)
    fi
    python3 CFD_2D/scripts/ramair_2d_openfoam_runner.py "${{RUN_ARGS[@]}}" 2>&1 | tee "$LOG_DIR/${{VARIANT}}_${{CASE_NAME}}_workflow_06_solver.log"
    [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Solver run failed"
  fi

  echo "=== Postprocess alpha=$ALPHA ==="
  {' '.join(post_args)} 2>&1 | tee "$LOG_DIR/${{VARIANT}}_${{CASE_NAME}}_workflow_07_postprocess.log"
  [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Postprocess failed"
done

echo "Workflow finished."
"""

    return f"""#!/usr/bin/env bash
set -u
set -o pipefail

fail() {{
  echo
  echo "ERROR: $*" >&2
  exit 1
}}

pause_step() {{
  echo
  read -r -p "Press Enter to continue, or Ctrl+C to stop..." _
}}

open_path() {{
  local path="$1"
  if command -v explorer.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$path")" >/dev/null 2>&1 || true
  else
    echo "Manual inspection path: $path"
  fi
}}

open_mesh_file() {{
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
}}

source_openfoam_if_needed() {{
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
}}

handle_existing_path() {{
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
  backup_dir="$backup_root/${{variant}}_${{label}}_${{stamp}}"
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
}}

print_mesh_config_summary() {{
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
    "closed_first_cell_height_m",
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
    "open_inlet_boundary_layer_mode",
    "open_inlet_transition_elements",
    "open_inlet_transition_growth",
    "open_inlet_connector_normal_nodes",
    "open_inlet_marker_transfinite_nodes",
    "open_inlet_marker_bump_strength",
    "open_use_yplus_first_cell_height",
    "open_first_cell_height_m",
    "open_boundary_layer_layers",
    "open_boundary_layer_growth",
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
keys = common + (open_keys if variant in {{"open_ramair", "standard", "optimized", "ross_standard_8p4", "ross_minimum_4p0"}} else closed)
print(f"Active editable mesh config: {{path.resolve()}}")
for key in keys:
    print(f"  {{key}}: {{data.get(key)}}")
PY
}}

review_mesh_config_before_gmsh() {{
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
}}

resolve_source_project() {{
  local candidate
  for candidate in \
    "${{RAMAIR_PROJECT_ROOT:-}}" \
    "$PWD" \
    "$HOME/ramair_cfd/DESIGN_APP" \
    "$CALL_PROJECT"
  do
    if [ -n "$candidate" ] && [ -f "$candidate/preprocess_ramair_main.py" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}}

echo "=== Locate project ==="
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd -P)"
CALL_PROJECT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SOURCE_PROJECT="$(resolve_source_project || true)"
[ -n "$SOURCE_PROJECT" ] || fail "Could not find preprocess_ramair_main.py. Set RAMAIR_PROJECT_ROOT to the project folder and rerun."

if [[ "$SOURCE_PROJECT" == /mnt/* && {str(safe.get('copy_to_native_wsl_fs', True)).lower()} == true ]]; then
  LINUX_PROJECT="$HOME/ramair_cfd/DESIGN_APP"
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

if [ {1 if needs_openfoam_tools else 0} -eq 1 ]; then
  source_openfoam_if_needed
  command -v gmshToFoam >/dev/null 2>&1 || fail "gmshToFoam missing"
  command -v checkMesh >/dev/null 2>&1 || fail "checkMesh missing"
fi

VARIANT={q(variant)}
MESH_DIR="$PWD/CFD_2D/meshes/$VARIANT"
ALPHAS="{alpha_str}"

echo "=== Preprocess and case package ==="
if [ {1 if geom.get('run_preprocessor', True) else 0} -eq 1 ]; then
  python3 preprocess_ramair_main.py --config "Application Support/Configurations/default_case_config.json" 2>&1 | tee "$LOG_DIR/${{VARIANT}}_workflow_01_preprocess.log"
  [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Preprocessor failed"
fi
if [ {1 if geom.get('rebuild_case_package', True) else 0} -eq 1 ]; then
  python3 CFD_2D/scripts/ramair_2d_profile_case_builder.py --case-root . --variant "$VARIANT" --alpha-start {float(min(alphas))} --alpha-end {float(max(alphas))} --alpha-step 1 --reynolds {float(cond.get('reynolds', 4e6))} --mach {float(cond.get('mach', 0.1))} --rho {float(cond.get('rho_kg_m3', 1.225))} --mu {float(cond.get('mu_pa_s', 1.81e-5))} --velocity {q(cond.get('velocity', 'auto'))} --overwrite --validate 2>&1 | tee "$LOG_DIR/${{VARIANT}}_workflow_02_case_builder.log"
  [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Case builder failed"
fi
{pause_after_geometry}

echo "=== Review editable mesh parameters ==="
review_mesh_config_before_gmsh "$VARIANT"

echo "=== Mesh ==="
while true; do
  {action_block('$MESH_DIR', variant, str(safe.get('existing_mesh_action', 'ask')), 'mesh')}
  {' '.join(mesh_args)} 2>&1 | tee "$LOG_DIR/${{VARIANT}}_workflow_03_mesh.log"
  [ "${{PIPESTATUS[0]}}" -eq 0 ] || fail "Mesh builder failed"
  open_path "$MESH_DIR"
  echo "Optional visual check: gmsh \\"$MESH_DIR/mesh_final.msh\\""
  read -r -p "Open mesh_final.msh now? Type OPEN or press Enter to skip: " OPEN_GMSH
  if [ "$OPEN_GMSH" = "OPEN" ]; then
    open_mesh_file "$MESH_DIR/mesh_final.msh"
  fi
  echo "Type REMESH to edit cfd2d_mesh_config.json and regenerate this mesh now."
  echo "Press Enter to continue."
  read -r -p "Mesh decision [REMESH/continue]: " REMESH_CHOICE
  if [ "$REMESH_CHOICE" = "REMESH" ]; then
    review_mesh_config_before_gmsh "$VARIANT"
    continue
  fi
  break
done
{pause_after_mesh}

{post_mesh_workflow}
"""


def load_config(path: Path) -> dict[str, Any]:
    return deep_merge(DEFAULT_WORKFLOW_CONFIG, read_json(path, {}) or {})


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure and write ram-air CFD 2D workflow scripts.")
    parser.add_argument("--case-root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json"))
    parser.add_argument("--init-config", action="store_true", help="Write the default workflow config and exit unless combined with other actions.")
    parser.add_argument("--overwrite", action="store_true", help="Allow --init-config or --write-script to overwrite existing files.")
    parser.add_argument("--list-geometry", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--show-mesh-settings", action="store_true", help="Print the main editable Gmsh settings and their config path.")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--write-script", type=Path, nargs="?", const=Path("Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh"), help="Write a WSL Bash workflow script.")
    args = parser.parse_args()

    root = project_root(args.case_root)
    cfg_path = args.config if args.config.is_absolute() else root / args.config

    if args.init_config:
        if cfg_path.exists() and not args.overwrite:
            raise SystemExit(f"Config already exists: {cfg_path}. Pass --overwrite to replace it.")
        write_json(cfg_path, DEFAULT_WORKFLOW_CONFIG)
        print(f"Wrote default workflow config: {cfg_path}")

    cfg = load_config(cfg_path)
    if args.interactive:
        cfg = run_interactive(root, cfg)
        write_json(cfg_path, cfg)
        print(f"Updated workflow config: {cfg_path}")

    warnings = validate_config(root, cfg)
    for warning in warnings:
        print(f"WARNING: {warning}")

    if args.list_geometry:
        print_geometry(root)
    if args.plan:
        describe_plan(root, cfg)
    if args.show_mesh_settings:
        print(f"Mesh config: {mesh_config_path(root)}")
        settings = mesh_settings(root)
        for key in [
            "run_boundary_layer",
            "closed_wall_curve_method",
            "closed_wall_target_nodes",
            "closed_te_bump_strength",
            "closed_use_yplus_first_cell_height",
            "closed_first_cell_height_m",
            "closed_boundary_layer_layers",
            "closed_boundary_layer_growth",
            "closed_profile_target_points",
            "closed_te_rounding_enabled",
            "closed_te_rounding_points",
            "closed_near_wall_size_from_bl",
            "closed_near_wall_size_chord",
            "closed_farfield_size_chord",
            "domain_radius_chord",
            "open_wall_curve_method",
            "open_inlet_boundary_layer_mode",
            "open_inlet_transition_elements",
            "open_inlet_transition_growth",
            "open_inlet_connector_normal_nodes",
            "open_inlet_marker_transfinite_nodes",
            "open_inlet_marker_bump_strength",
            "open_use_yplus_first_cell_height",
            "open_first_cell_height_m",
            "open_boundary_layer_layers",
            "open_boundary_layer_growth",
            "open_boundary_layer_lip_fan_points",
            "open_minimum_fabric_thickness_chord",
            "open_near_wall_size_from_bl",
            "open_near_wall_size_chord",
            "open_surface_target_nodes",
            "open_te_transfinite_min_nodes",
            "open_lip_transfinite_min_nodes",
            "open_surface_size_lip_chord",
            "open_surface_size_te_chord",
            "open_farfield_size_chord",
            "open_inlet_refinement_bridge_enabled",
        ]:
            print(f"  {key} = {settings.get(key)}")
    if args.write_script is not None:
        out = args.write_script if args.write_script.is_absolute() else root / args.write_script
        if out.exists() and not args.overwrite:
            raise SystemExit(f"Script already exists: {out}. Pass --overwrite to replace it.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generate_bash(root, cfg), encoding="utf-8")
        try:
            os.chmod(out, 0o755)
        except Exception:
            pass
        print(f"Wrote workflow script: {out}")

    if not any([args.init_config, args.list_geometry, args.plan, args.show_mesh_settings, args.interactive, args.write_script is not None]):
        parser.print_help()


if __name__ == "__main__":
    main()
