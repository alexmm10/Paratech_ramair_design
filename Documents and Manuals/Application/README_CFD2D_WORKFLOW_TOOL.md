# CFD 2D Workflow Tool

This helper is a user-facing orchestrator for the existing ram-air CFD 2D scripts.
It does not add new CFD physics; it writes a reproducible Ubuntu/WSL Bash script
from one editable JSON file.

## Main Files

- `CFD_2D/scripts/ramair_cfd2d_workflow_tool.py`: lists geometries, edits the workflow config in interactive mode, prints the plan and writes the Bash execution script.
- `CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json`: main workflow selections.
- `CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json`: detailed mesh controls, including boundary-layer layers, wall `Transfinite Curve` node counts, TE rounding and farfield sizes.
- `CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config_reference.json`: grouped explanations of the flat mesh config keys.
- `CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json`: time-step, solver and averaging controls.
- `CFD_2D/CFD_2D_inputs/config/cfd2d_physical_defaults.json`: density, viscosity, pressure, temperature, Mach/Re velocity source.
- `Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh`: generated WSL execution script.

## Basic Use

From the project root on Windows or Ubuntu:

```bash
python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --list-geometry
python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --plan
python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --write-script "Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh" --overwrite
```

Then run inside Ubuntu/WSL:

```bash
bash "Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh"
```

For guided edits:

```bash
python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --interactive --write-script "Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh" --overwrite
```

## Config Sections

`geometry` selects the profile variant and domain. Available variants are read from
`CFD_2D/CFD_2D_inputs/geometry/` and `CFD_2D/CFD_2D_inputs/case_package/`.

`case_conditions` selects alpha values, Reynolds, Mach, density, viscosity and
velocity source. If `velocity` is `auto`, the existing case builder derives speed
from Reynolds, viscosity, density and chord.

`mesh` selects mesh level, Gmsh timeout, optional `gmsh_threads`, plotting and
whether to generate an OpenFOAM-ready extruded mesh. For open profiles,
`open_diagnostic_mesh=false` uses the finite-thickness thin-solid topology;
`open_diagnostic_mesh=true` is only a legacy zero-thickness fallback.

`execution` controls whether the solver actually runs. The runner remains dry-run
unless `run_solver` is true. Optional `stop_after_min` requests a clean OpenFOAM
`stopAt writeNow` stop after the selected number of minutes so partial data can
be postprocessed without using Ctrl+C.

`postprocess` controls force/residual plots and VTK/ParaView export:

- `none`: no VTK export.
- `coefficients_only`: coefficient/residual processing only.
- `latest_vtk`: export latest available fields for ParaView.
- `all_vtk`: export all written time directories; this can use much more disk space.

In a newly generated dry workflow with `execution.run_solver = false`, VTK export
is suppressed because there are no new field time directories yet.

`safeguards` controls archive/delete prompts, pauses after geometry and mesh, and
manual mesh approval before writing OpenFOAM cases.

Generated Bash scripts pause immediately before Gmsh and open the active
`CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json` from the current working
copy. This matters on WSL: if the script copied the project from `/mnt/c` to
`~/ramair_cfd/DESIGN APP`, edit the opened WSL file at that pause, not an older
copy from a previous run.

After each Gmsh run the generated scripts open the mesh folder and ask whether to
`REMESH`. Choose `REMESH` to reopen the same mesh config, save new values, and
regenerate without repeating preprocessing or case-package generation.

## Closed Reference Debug Case

The default config uses `reference_uncut`, `ross_cgrid_like`, alpha 4 deg and the
debug mesh level. The current debug mesh uses:

- 50 Gmsh boundary-layer layers, growth 1.10 and first-cell height `1e-4 c`;
- recombined boundary-layer quads when possible;
- Delaunay surface meshing (`Mesh.Algorithm = 5`) plus a small `Mesh.RandomFactor`;
- full configured BL thickness from first-cell height, growth and layer count; there is no artificial BL-thickness cap;
- a one-cell-thick 3D extrusion for OpenFOAM 2D;
- smooth near-field refinement;
- wake refinement disabled for this first debug mesh;
- optional tangent-continuous downstream TE cap during closed-profile point preprocessing;
- up to 360 redistributed profile points with validated TE density. The rounded TE cap uses three short spline segments plus the remaining profile spline. Gmsh `Transfinite Curve` controls tangential divisions with `closed_airfoil_target_nodes` and `closed_te_target_nodes`; `closed_te_neighbor_bump_enabled` clusters the neighboring long curve progressively toward the TE without adding many curve fragments.
- two linear nearfield transitions from `0.02 c` to `4.0 c`, with automatic point/boundary size propagation disabled, so target sizes do not create a broad fine plateau followed by a sudden jump. `farfield_size_chord` is deliberately coarse for debugging.

The TE cap status is written to:

```text
CFD_2D/meshes/reference_uncut/profile_preprocessing_report.json
CFD_2D/meshes/reference_uncut/profile_preprocessed_points.csv
```

## Open Ram-air Debug Case

Use the dedicated command file:

```bash
bash "Documents and Manuals/Application/run_open_ramair_debug_wsl.sh"
```

or paste the wrapper in:

```text
Documents and Manuals/Application/RUN_OPEN_RAMAIR_GMSH_COMMANDS.txt
```

This creates a finite-thickness thin-solid Gmsh mesh for `open_ramair`. The
fabric thickness is `0.00012 c`, smaller than the current total BL thickness.
The fabric is represented as a thin solid hole, so exterior and internal cavity
are one connected fluid region through the inlet gap. The inlet gap is fluid and
is not exported as a physical `ram_air_inlet` patch. The current debug setup
uses `open_wall_curve_method=single_outer_spline_with_lip_fans`: one exterior
interpolating `Spline` carries the BL, the inlet bridge is a separate non-physical sizing
line, and Gmsh `BoundaryLayer` fans smooth the two lip turns. The interior
fabric side and the thickness caps are wall curves without a BL field.
Rectangular LE/lip `Box` refinements are removed, and smooth distance
thresholds allow cavity and farfield cells to grow.

The inlet bridge remains separate because Gmsh Physical Groups apply to whole
curve entities. A single curve cannot be partly `airfoil_wall` and partly
non-wall. `open_inlet_refinement_bridge_enabled` adds the embedded sizing line
across the inlet; it refines the inlet gap but is not a wall, not a physical
patch and not included in the BL field.

With Gmsh 4.8.4 the connected 2D mesh is available for inspection, but the BL
termination can still create about 14 collapsed triangles near the lips.
Extruding those cells preserves connectivity but fails OpenFOAM `checkMesh`;
deleting or contracting them opens cells. The current open-profile OpenFOAM path
is therefore diagnostic and deliberately remains `FAIL`, not solver-ready.

Refine/regenerate by editing `CFD_2D/CFD_2D_inputs/config/cfd2d_mesh_config.json`.
The most useful open controls are:

- `open_boundary_layer_layers`
- `open_wall_curve_method`
- `open_first_cell_height_chord`
- `open_boundary_layer_fan_at_lips`
- `open_surface_target_nodes`
- `open_te_transfinite_min_nodes`
- `open_lip_transfinite_min_nodes`
- `open_near_wall_size_chord`
- `open_surface_size_lip_chord`
- `open_surface_size_te_chord`
- `open_farfield_size_chord`
- `open_minimum_fabric_thickness_chord`
- `open_nearfield_refinement_enabled`
- `open_inlet_refinement_bridge_enabled`

Gmsh 4.8 generated the current thin-solid debug mesh in validation, but
quality can still be marked `FAIL` if very low-quality triangles are present.
Inspect `mesh_final.msh` in Gmsh before attempting OpenFOAM conversion.

## Safety Notes

- The generated Bash script asks before deleting existing meshes,
  OpenFOAM cases or results.
- It copies the project to `~/ramair_cfd/DESIGN APP` when launched from `/mnt/c`
  to avoid slow or fragile Gmsh/OpenFOAM I/O on OneDrive paths.
- It pauses after case-package creation, after mesh generation and before solver
  execution.
- It never runs CATIA.
