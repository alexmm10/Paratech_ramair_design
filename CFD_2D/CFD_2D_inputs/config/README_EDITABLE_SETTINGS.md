# Editable CFD 2D Settings

Edit these files before regenerating a mesh or OpenFOAM case:

- `cfd2d_mesh_config.json`: Gmsh geometry, wall discretization, boundary layer and domain.
- `cfd2d_mesh_config_reference.json`: grouped explanations for every editable mesh key.
- `cfd2d_physical_defaults.json`: Reynolds, Mach and fluid properties used by case writing and y+ estimates.
- `cfd2d_solver_config.json`: OpenFOAM time controls, averaging and write cadence.
- `cfd2d_workflow_config.json`: workflow pauses, exports and run limits.

The active WSL debug scripts pause before Gmsh and open this folder. Edit the WSL copy of `cfd2d_mesh_config.json`, save it, then press Enter so the builder reads the fresh values.

## Closed Reference

The reviewed preset ladder uses target `y+=1, 2/3, 4/9, 8/27` for
Coarse/Medium/Fine/Extra Fine. The first three request 50 layers and Extra Fine
75; applying a preset creates an editable draft and does not approve or replace
a saved mesh. When y+-derived height is enabled, the mesh report records the
project, laminar and turbulent formula candidates and selects the smallest
positive height.

The standard closed-wall method is now:

```text
closed_wall_curve_method = two_spline_te_cap
```

The builder writes two non-periodic interpolating splines: one tangent-continuous TE cap and one main profile curve. `Using Bump` refines the two ends of the main curve next to the cap. This avoids the unsupported periodic-curve extrusion path in Gmsh 4.8 and keeps the TE geometry explicit.

Main knobs:

- `closed_wall_target_nodes`: total tangential nodes on the wall curve. More nodes means more BL columns along the airfoil.
- `closed_te_target_nodes`: requested nodes on the rounded TE cap itself. The
  screened default is 18. Increase it only when the curved closure is
  under-resolved; it must not be used to refine the straight aft chord.
- `closed_te_bump_strength`: transition on the neighboring main wall. `1` is uniform; smaller values cluster more strongly toward both TE-cap junctions.
- `closed_te_refinement_width_chord`: optional chordwise preprocessing refinement outside the explicit cap. The robust default is `0`, so straight aft-wall points are not refined merely because their x coordinate is close to the TE.
- `closed_profile_target_points`: geometric preprocessing points before Gmsh. This controls curve fidelity, not directly the final mesh node count.
- `closed_te_rounding_*`: tangent-continuous TE closure controls. The screened
  default uses 25 geometric cap samples. Geometry samples and requested Gmsh
  cap nodes are deliberately separate.
- `closed_use_yplus_first_cell_height`: `true` computes `y1` from `target_y_plus`; `false` uses `closed_first_cell_height_m` directly in metres. The application shows the resulting `y1/chord` beside it.
- `closed_near_wall_size_from_bl`: when `true`, derives the near-wall triangle target automatically. The shared BL-front edge is tangential, so the active target uses the larger of the last normal BL scale and 85% of the mean tangential wall spacing. It is not a forced first-triangle height.

Every closed attempt writes `airfoil_wall_curve_connectivity_audit.json/.csv` and TE metrics in `mesh_quality_report.json`: duplicate coordinate groups, minimum TE segment length, local TE radius estimate and BL-thickness-to-radius ratio.

## Open Ram-Air

The supported open-wall method is:

```text
open_wall_curve_method = segmented_outer_splines
```

The verified zero-thickness representation uses upper, rounded-TE, lower and
base-profile inlet splines with one common exterior tangential spacing. A
duplicated nonphysical inlet interface lets Gmsh build a one-sided closed BL
loop; only those inlet nodes are stitched before extrusion. The inlet never
becomes a wall or physical patch. The cavity is triangular and its wall
discretization is independent and coarser because it has no prism layer.

Main knobs:

- `open_zero_thickness_contour_target_nodes`: common tangential density along
  the full exterior contour, including the curved nonphysical inlet
  continuation.
- `open_boundary_layer_layers`, `open_boundary_layer_growth`, `open_first_cell_height_m`: normal BL stack. The manual height is always entered in metres.
- `open_inner_wall_node_factor` and `open_inner_te_node_factor`: fractions of
  the corresponding exterior counts. The defaults `0.45` and `0.35`, together
  with `Bump=0.35`, retain nodes at inlet/TE without refining the full cavity.
- `open_cavity_*`: inner-wall, transition and coarse cavity-core sizing. The cavity has triangles only, no prism BL.
- `open_internal_inlet_*`: staged refinement near the LE opening. It preserves
  the measured tangential-interface scale in the first two bands, blends over
  `open_internal_inlet_matching_transition_chord` (default `0.012c`) and then
  reaches the intermediate and cavity-core sizes.
- `open_internal_te_*`: cavity refinement derived from independent inner-TE spacing; it does not copy the exterior TE count.
- `open_near_wall_size_from_bl`: when true, TE, inlet and lip-cap triangle targets are calculated from the last BL layer and local tangential spacing. The manual `open_near_wall_size_chord` and `open_internal_inlet_size_chord` values are fallbacks only when this switch is false.

The finite-thickness-only controls `open_surface_target_nodes`,
`open_wall_end_bump_strength`, `open_boundary_layer_fan_at_lips`,
`open_minimum_fabric_thickness_chord` and
`open_inlet_marker_transfinite_nodes` are hidden by the application when the
zero-thickness representation is active.

The automatic interface rule does not impose another first-cell height. It sets
the edge scale leaving the outer BL front so the first Delaunay triangle cannot
jump directly to the coarse cavity or farfield size. The selected zero-thickness
baseline uses `min(tangential spacing, 12*y1)` in the interface-normal
direction, then preserves tangential-scale bands before growing through the
0.012c matching transition. Intermediate Threshold
fields use `StopAtDistMax=1`; the final exterior and cavity fields keep
`SizeMax` active with `StopAtDistMax=0`. With
`open_transition_sigmoid_enabled=true`, Gmsh interpolates the staged sizes
smoothly instead of producing an abrupt size jump.

The recombined throat is transfinite: its cells are governed by curve-node
counts, so cavity/exterior `Threshold` fields cannot change the throat itself.
`open_internal_inlet_*` controls the adjacent triangular regions. The
`triangles` transition option is genuinely unstructured, but current validation
shows worse non-orthogonality and it remains diagnostic rather than default.

`max_internal_parse_elements=75000` limits only the optional pure-Python MSH
statistics pass. Above that value Gmsh, `gmshToFoam` and `checkMesh` still run;
the report records that internal parsing was skipped instead of pretending that
statistics were calculated.

Use `python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --show-mesh-settings` to print the active high-level settings.

The graphical **Optimizacion corta de parametros** control compares 2--5
candidates without running a solver. It updates this JSON only after selecting
the best real mesh and backs up the previous configuration first.

Gmsh 4.15.2 is the validated version for this curved BL workflow. Gmsh 4.8.4 generated edge-recovery errors and collapsed TE triangles with the same `.geo`; install the local binary with `bash "Documents and Manuals/Application/install_gmsh_4_15_wsl.sh"`.

## Work-case persistence

The active JSON files are the editable workspace. When a Results work case is
active, pressing **Guardar** in the app also updates the corresponding package
atomically:

- geometry/CATIA settings -> geometry package;
- physical/workflow settings -> CFD case package;
- mesh settings -> mesh package;
- solver settings -> solver package and CFD case package.

A complete work-case load restores all four stages and applies the solver
package last. New work cases are seeded with the complete schema-12
configuration. The compatible Results package identifier remains
`topology_solver_v11`; it is a stable package name, not the JSON schema
version. Historical simulation outputs are never rewritten by this
synchronization.

## Time-step policy

`cfd2d_solver_config.json` schema 12 includes `time_step_mode`:

- `adaptive_courant` exposes `maxCo` and `maxDeltaT` and is the default;
- `fixed` hides those adaptive limits in the application and uses the entered
  dimensional `deltaT`.

The fixed mode is not automatically faster. The retained bounded evidence in
`CFD_2D/reports/mesh_studies/2026-07-27_open_efficiency_fixed_dt/` shows that
the reference-sized open-case step `deltaT*=0.004` is unstable on the current
mesh, whereas `deltaT=4e-8 s` is stable near `Co=1` but comparable to the
adaptive step. Select fixed mode only after a Courant diagnostic.

## Temporal accuracy budget

The common `temporal_accuracy` object and the
`topology_profiles.open_internal_cavity.temporal_accuracy` override define an
auditable study, not a new turbulence model or a claim of convergence:

- `target_min_strouhal`: slowest retained frequency; together with
  `minimum_cycles_for_statistics` it sets the minimum averaging window.
- `target_max_strouhal`: fastest retained frequency.
- `target_samples_per_cycle`: engineering sampling target. Nyquist remains
  only the two-sample theoretical limit.
- `time_step_study_values_star`: sequential values to compare at equal
  physical duration.
- `reference_*`: published or project reference values retained for
  traceability.

The current `St=0.05..20` range is conservative and must be replaced by
measured pressure/load spectra after the pilot. Generate a machine-readable
assessment with:

```bash
python CFD_2D/scripts/ramair_2d_timestep_advisor.py \
  --solver-config CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json \
  --topology closed_external_airfoil \
  --chord-m 1 --velocity-m-s 51.0438430298 \
  --courant-diagnostics CFD_2D/reports/courant_mesh_sensitivity_20260724/reference_uncut_validation_1m_baseline_screen_courant_diagnostics.json \
  --output-json CFD_2D/reports/timestep_assessment.json \
  --output-md CFD_2D/reports/timestep_assessment.md
```

Every newly written OpenFOAM case also receives
`time_step_assessment.json/.md`. `LOCAL_MESH_COURANT` means that a measured
cell limit is below the physical frequency ceiling; increasing `maxCo` is not
a substitute for repairing that hotspot.
