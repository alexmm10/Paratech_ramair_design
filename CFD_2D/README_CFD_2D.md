# CFD 2D Workflow

## Validation & Convergence Lab

The isolated laboratory uses registry schema 10 while the general solver
configuration uses schema 14 and backend API 24. It keeps the
closed/open coarse-medium-fine study separate from the normal active
workspace. Its six main sections cover meshes/conditions, solver strategy,
RANS, URANS, space-time convergence and reports/workspace. One collapsed
global scalar monitor follows the active execution; review pages remain
static.

URANS startup defaults to 25/25/50 Euler steps before production `backward`;
the bounded pilot uses 10/10/20/30 steps. RANS uses absolute targets
10,000 + 2,500 up to 20,000 and never exposes Courant as a SIMPLE diagnostic.
The real `closed_coarse` 20,000-iteration state preserves its automatic
`NOT_CONVERGED` gate and a separate explicit user acceptance. PIMPLE 2/3/4 is
prepared from that one checkpoint but remains blocked until a real pilot
passes.

Missing or stale RANS evidence produces
`BLOCKED_MISSING_RANS_CHECKPOINT`; it is not hidden and does not create a
placeholder result. Real execution remains explicit. See
`Documents and Manuals/Application/VALIDATION_CONVERGENCE_LAB.md` and
`CFD_2D/validation_studies/README_VALIDATION_CONVERGENCE_LAB.md`.

## 2D inlet design before meshing

Use the app's **Geometria > Diseno 2D del corte ram-air** section or run:

```bash
python CFD_2D/scripts/ramair_2d_inlet_designer.py \
  --project-root . \
  --config CFD_2D/CFD_2D_inputs/config/cfd2d_inlet_design_config.json
```

The automated engine is XFOIL. XFLR5 embeds XFOIL and remains useful for
manual inspection, but its desktop interface is not used as a batch API. The
standard mode uses all converged alpha rows. The optimized mode runs the same
gradual alpha sweep and filters the converged rows by the selected CL window;
this is more robust near CLmax than forcing a `CSEQ` through stalled points.
No missing Cp or unconverged operating point is silently accepted.

## General URANS execution

Outside Validation Lab, schema 14 treats `maxDeltaT_star` as the physical
temporal-resolution ceiling and Courant as an emergency nonlinear guard. The
closed profile default is `maxCo=50`; the open profile default is `maxCo=20`.
Both use at most 15 PIMPLE outer correctors and may stop the outer loop early
when `U` and `nuTilda` reach absolute residual `1e-4` with zero relative
tolerance. Validation Lab strips this early-exit block and retains fixed
timesteps and corrector counts for controlled comparisons.

The runner records the real solver PID/PGID and process-start token. A clean
stop requests `writeNow`, preserves the latest root or decomposed time and
returns `PAUSED_RESTARTABLE`; stale RUNNING records are reconciled on startup.
Force stop remains available while a clean stop is pending.

For an open airfoil, `airfoil_wall_external` and
`airfoil_wall_internal` are already explicit OpenFOAM wall patches. The inlet
opening is not a wall and not a patch. No `createBaffles` pass is applied.

Automatic ParaView products include pressure and velocity views with a
streamline seed line perpendicular to the freestream, centered just upstream
of the leading edge and limited to the near-airfoil affected region.

Remote packages created from the Execution page include frozen cases, queue
metadata, checksums and Linux/WSL launchers for run, resume, clean stop, forced
stop, monitoring and postprocessing.

The Mesh page can open the converted `polyMesh` together with the exact VTK
problem sets from `checkMesh` in ParaView. The automatic switch opens a new
failed report once; the manual button remains available for later inspection.

## Diagnostico de calidad

The reviewed mesh ladder is Coarse `y+=1`, Medium `2/3`, Fine `4/9` and Extra
Fine `8/27`. The first three use 50 boundary-layer layers; Extra Fine uses 75
for comparison. The versioned diagnostic runner
`scripts/ramair_2d_mesh_science.py` compares Gmsh algorithms 5/6 and the
curvature/transfinite interaction without touching active outputs. See
`Documents and Manuals/CFD 2D/MESH_SCIENCE_DECISION_TAREA_05.md`.

El builder ejecuta `checkMesh -allTopology -allGeometry`. Ante un fallo repite
la comprobacion con `-writeSets -writeSurfaces -setFormat vtk` y conserva:

- `checkMesh_problem_sets/`: IDs exactos de `cellSet`, `faceSet` o `pointSet`;
- `checkMesh_problem_locations/`: superficies VTK para abrir en ParaView;
- `checkMesh_problem_locations.json/.txt`: centroides, limites, distribucion
  por `x/c`, extremo global, umbral y region probable.

OpenFOAM no escribe en esa orden un valor de determinante/skewness para cada
ID. El informe no inventa esos valores: asocia el extremo global y el umbral al
set exacto y conserva los IDs para inspeccion posterior.

Para perfiles abiertos existen dos representaciones seleccionables:

- `zero_thickness_base_profile` (default): no crea espesor artificial. La BL
  exterior sigue el contorno cerrado del perfil base sin corte, incluida su
  curva de LE. El perfil abierto real es el unico `airfoil_wall`; la curva del
  inlet es una interfaz de fluido no fisica. Exterior y cavidad se mallan con
  copias coincidentes de esa interfaz y el builder cose exclusivamente sus
  nodos antes de extruir una celda.
- `finite_thickness_fabric`: conserva la topologia anterior con espesor de
  tela, labios y controles de transicion para estudios comparativos.

En el modo sin espesor, ambas copias del inlet usan exactamente los mismos
nodos tangenciales. La estrategia recomendada `hybrid_boundary_extension`
limita el uso de y1 a una franja normal de compatibilidad de `0.0035c`: parte
de `8*y1` y alcanza el ancho tangencial real de las aristas del inlet. A partir de
ahi, el campo `Extend` de Gmsh hereda el tamano medio local de esas aristas y
lo hace crecer de forma progresiva hacia el nucleo. Asi y1 no sobrerrefina toda
la cavidad y el ancho tangencial no se sustituye por una altura normal. El
inlet nunca se declara boundary fisica.

The 2D CFD workflow follows `Documents and Manuals/Application/CFD_2D_TECHNICAL_SPECIFICATIONS.txt`. The current implementation prepares geometry, generates inspectable Gmsh files and supports OpenFOAM-ready one-cell-thick 3D extrusion. Open ram-air cases can use either the verified zero-thickness baffle/interface topology or the retained finite-thickness thin-solid comparison topology.

## Graphical Python Application

The existing stages can now be controlled from one Streamlit application:

```bash
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install
.venv-cfd2d-ui/bin/python -m streamlit run CFD_2D/app/ramair_cfd2d_app.py
```

From Windows use `INSTALL_AND_START_RAMAIR_CFD2D_APP.bat` once and
`START_RAMAIR_CFD2D_APP.bat` thereafter. See
`Documents and Manuals/Application/README_CFD2D_PYTHON_APP.md` for the complete workflow. All CLI scripts
remain available and retain their existing output locations.

## Folder Roles

- `CFD_2D_inputs/`: geometry variants, case packages and CFD config exported by the preprocessor/case builder.
- `meshes/`: Gmsh `.geo/.msh`, previews, mesh reports and optional converted `constant/polyMesh`.
- `openfoam_cases/`: OpenFOAM dictionaries and copied real `polyMesh` when available.
- `results/`: postprocessed force/history outputs. No fake CSVs are written.
- `reference_data/`: Ross/Balaji/Ghoreyshi reference data or digitization placeholders.
- `scripts/`: executable workflow scripts.
- `tests/`: pytest suite; no CATIA required.
- `reports/`: environment reports and short audit outputs.

## Script Responsibilities

- `ramair_2d_profile_case_builder.py`: reads `CFD_2D/CFD_2D_inputs/geometry` and writes a case package. It does not mesh or solve.
- `ramair_2d_mesh_builder.py`: writes Gmsh `.geo` and always meshes the 2D front surface first. It then performs a connectivity-preserving one-cell MSH2 extrusion for OpenFOAM, avoiding Gmsh CAD extrusion of periodic curves. The closed wall uses a non-periodic main spline plus a tangent-continuous TE-cap spline; `Using Bump` refines both cap junctions. The default open topology uses upper/TE/lower splines plus the curved LE segment reconstructed from `open_base_profile_variant`. Duplicate inlet-interface nodes are stitched selectively, while coincident inner/outer wall nodes remain separate to form the zero-thickness baffle. The retained finite-thickness topology remains selectable. No `ram_air_inlet` patch is created. It never runs a CFD solver.
- `ramair_2d_mesh_quality_controller.py`: decides PASS/WARNING/FAIL from real mesh/check reports and adds a non-blocking A--F engineering grade with explicit metric margins. Diagnostic open geometry is not PASS.
- `ramair_2d_mesh_optimizer.py`: compares 2--5 real Gmsh/checkMesh candidates, keeps the best and removes the heavy rejected meshes. It never runs a solver.
- `ramair_2d_openfoam_case_writer.py`: writes OpenFOAM dictionaries and copies a real converted `polyMesh` if present. It never creates an empty `polyMesh`.
- `ramair_2d_openfoam_runner.py`: dry-run by default; executes only with `--run` and writes `run_status.json`. `--solver auto` prefers `foamRun -solver incompressibleFluid` for OpenFOAM Foundation 14/13 and falls back to `pimpleFoam` only for compatibility. A timeout requests `stopAt writeNow`, writes `TIMEOUT_PARTIAL`, and keeps partial fields and force histories. `--stop-after-min N` writes `STOPPED_PARTIAL`. The optional `--stop-when-force-stable` compares Cl/Cd/Cm statistics in two adjacent convective-time windows and requests `stopAt nextWrite`; it accepts statistically stationary oscillations and is disabled by default. Parallel runs rewrite `numberOfSubdomains`, reconstruct fields and remove redundant `processorN/` folders only after verifying the reconstructed time; use `--keep-processor-directories` for diagnostics.
- `ramair_2d_openfoam_staged_runner.py`: optionally initializes a fresh case with `steadyState` + SIMPLE, checks residuals and coefficient stability, archives that iteration history, transfers fields into transient `0/`, then invokes the normal PIMPLE runner. It is dry-run by default.
- `ramair_2d_openfoam_sweep.py`: runs prepared alpha cases one at a time, with independent timeout/convergence/resume policy and optional postprocessing after each angle. It is dry-run by default.
- `ramair_2d_postprocess.py`: reads restart-aware real `forceCoeffs`, parses solver residuals/Courant number from the OpenFOAM log, writes plots/CSVs, and can run `foamPostProcess`/`foamToVTK` for ParaView inspection. It writes wall `y+(x/c)` and `Cp(x/c)` as upper/lower branches; open cases additionally separate exterior and interior wall patches. It also writes normal velocity profiles and numerical/theoretical/prism-stack thickness comparisons. The URANS case writes the cell field `Co` at the normal field-output times so ParaView can identify the cells limiting adaptive `deltaT`. Missing solver output is reported as `NOT_RUN_YET`, `TIMEOUT_PARTIAL_NO_FORCECOEFFS`, `RUN_FAILED`, or `FORCECOEFFS_MISSING_AFTER_RUN`.
- `ramair_cfd2d_workflow_tool.py`: user-facing helper that lists available geometries, edits/writes `cfd2d_workflow_config.json`, prints the execution plan and generates `Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh`.

## Mesh Output Layout

For each variant, `CFD_2D/meshes/<variant>/mesh_attempt_001/` stores the raw files for one mesh attempt: `mesh.geo`, `mesh.msh`, `log.gmsh` and the attempt-local quality report. The parent folder `CFD_2D/meshes/<variant>/` then receives a copied/promoted set named `mesh_final.geo`, `mesh_final.msh`, `mesh_quality_report.*` and optional `constant/polyMesh`. They are not two different physical meshes; the parent files are the latest selected attempt, kept at stable paths for the case writer and for manual inspection.

## Minimal Reference Workflow

```bash
python CFD_2D/scripts/check_environment.py
python preprocess_ramair_main.py --config "Application Support/Configurations/default_case_config.json"
python CFD_2D/scripts/ramair_2d_profile_case_builder.py --case-root . --variant reference_uncut --overwrite --validate
python CFD_2D/scripts/ramair_2d_mesh_builder.py --case-root . --variant reference_uncut --domain circular_50c --mesh-level custom --mesh-config CFD_2D/CFD_2D_inputs/config/mesh_presets/reference_uncut_validation_candidate.json --write-openfoam-mesh --check-mesh --gmsh-timeout-s 900 --gmsh-threads 12 --previous-output-action archive --openfoam-tool-timeout-s 600
```

The reference debugging flow now uses one mesh phase: Gmsh 3D generation, `gmshToFoam` conversion and `checkMesh` in a single command. After `gmshToFoam`, the mesh builder rewrites OpenFOAM boundary patch types that Gmsh cannot encode: `frontAndBack` becomes `empty`, airfoil/wall patches become `wall`, and farfield remains `patch`.

When `--overwrite` is used for a mesh generation command, previous generated files are moved to `Previous Versions/mesh_backups/<variant>_<timestamp>/` before the clean remesh starts. This prevents stale `mesh_final.*` or `polyMesh` files from being reused accidentally while keeping a backup.

The mesh report includes basic Gmsh-side statistics such as triangle quality, equiangle skewness, edge length distribution and neighbor area-ratio smoothness. It also records the Gmsh algorithm, `RandomFactor`, requested first-cell height, raw BL thickness, BL curve IDs and whether any TE curves were excluded from the BL field. When `--check-mesh` is used, selected OpenFOAM `checkMesh` metrics and wall times for `gmshToFoam`/`checkMesh` are copied into the same report. The optional internal MSH parser is skipped above 75,000 elements to keep debug runs responsive; this does not skip either external tool.

On WSL, the canonical runtime is `~/ramair_cfd/DESIGN_APP`. Gmsh runs in a native temporary directory by default and copies `mesh.msh` back to `CFD_2D/meshes/...`; this avoids slow mesh writes under `/mnt/c` or OneDrive. `gmshToFoam` and `checkMesh` also use a native temporary case and copy the converted `constant/polyMesh` back. OpenFOAM rejects case paths with spaces, so `DESIGN APP` and `INPUT_FILES` are compatibility links only. Pass `--no-gmsh-temp-workdir` or `--no-openfoam-temp-workdir` only when debugging file locations.

If Gmsh is run directly in `mesh_attempt_001/` without a temporary workdir, the builder now detects that `mesh.msh` is already at the final attempt path and does not copy the file onto itself.

The `debug` level redistributes the reference profile without modifying case-package CSVs. For `reference_uncut`, preprocessing replaces the consecutive TE closure by a tangent-continuous downstream cap and tags those exact points. The standard wall uses one main non-periodic spline plus that cap spline. `closed_te_target_nodes` controls only the curved cap; `Using Bump` controls the neighboring upper/lower approach. The measured defaults are 2000 tangential wall nodes, Bump 0.50, 25 geometric cap samples and 18 requested Gmsh cap nodes. `closed_te_refinement_width_chord=0` disables broad x-based refinement, so straight aft-wall segments are not refined merely because they lie close to the TE. The cap samples are restored after global resampling and audited for duplicates and continuity.

The bounded sensitivity study in
`CFD_2D/reports/courant_mesh_sensitivity_20260724/` showed that the controlling
cell Courant number lies on the lower rounded TE cap around
`x/c=1.0027`, not at the LE. Reducing the complete wall from 2000 to 1000 nodes
lowered the determinant margin to engineering grade C and did not improve the
allowed time step. Localizing the reduction to the TE cap retained
`checkMesh` OK and grade B. The follow-up matrix in
`CFD_2D/reports/mesh_studies/2026-07-25_te_courant_open_transition/` located
the hotspot in the first extruded triangular prism after the lower TE BL
front. The selected 25/18 cap increased measured `deltaT` by 16.8 percent over
the 35/25 baseline. Increasing the matched-thickness stack to 60 or 75 layers
did not remove the hotspot and added 8 or 15 percent more cells. This
candidate still requires aerodynamic mesh-independence.

Each closed Gmsh attempt also writes `airfoil_wall_curve_connectivity_audit.json`
and `.csv` next to `mesh.geo`. These files verify that wall-curve point IDs are
unique, consecutive segments have nonzero length, and the curve chain is
continuous before the geometry is sent to Gmsh. If TE cells invert, inspect
`closed_te_segment_min_length_chord` and
`closed_te_boundary_layer_thickness_to_min_radius`: a nearly zero segment or a
BL stack thicker than the local TE radius can fold the prism layer even when
the `.geo` syntax is valid.

Then approve only after inspection:

```bash
python CFD_2D/scripts/ramair_2d_mesh_builder.py --case-root . --variant reference_uncut --approve-mesh
python CFD_2D/scripts/ramair_2d_openfoam_case_writer.py --case-root . --variant reference_uncut --alpha 4 --reynolds 4000000 --write-case --require-converted-polymesh
python CFD_2D/scripts/ramair_2d_openfoam_runner.py --case CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000
```

For software debugging only, if `gmshToFoam` produced a real `constant/polyMesh` but `checkMesh` still marks the coarse debug mesh as `FAIL`, you can continue the pipeline with explicit forced approval:

```bash
python CFD_2D/scripts/ramair_2d_mesh_builder.py --case-root . --variant reference_uncut --approve-mesh --force-approve
```

Do not use a forced approval for aerodynamic conclusions.

Add `--run --timeout-min 30` only for an explicit short solver run. Add `--stop-after-min 10 --stop-mode writeNow` when you want OpenFOAM to write and stop cleanly after a fixed wall-clock duration for debugging. If the timeout is reached, inspect `run_status.json`, `log.foamRun` and the partial post-processing outputs.

Postprocess partial or complete runs with:

```bash
python CFD_2D/scripts/ramair_2d_postprocess.py --case-root . --variant reference_uncut --alpha 4 --run-openfoam-postprocess --export-vtk
```

Key outputs are `solver_residuals.png`, `courant_history.png`,
`deltaT_history.png`, `Cl_Cd_Cm_history.png`, `wall_yplus_vs_xc.png`,
`wall_normal_velocity_profiles.png`,
`boundary_layer_thickness_comparison.png`,
`available_time_directories.csv`, `written_field_inventory.csv`,
`visualization_guide.txt`, selected PyFoam diagnostic plots and optionally the
case `VTK/` folder. `deltaT_history.png` shows the adaptive physical time step
and the configured `maxDeltaT` ceiling. `Cl_Cd_Cm_history.png` contains only
the averaging window; no new `Cl_Cd_Cm_history_full.png` is generated.
Postprocessing creates separate `RANS/` and `URANS/` branches and a `.foam`
marker using an absolute path so ParaView can read all retained native
OpenFOAM times without duplicating them. With
`--automatic-paraview-products`, `pvbatch` generates a profile close-up
colored by `Cp`, a full cell-`Co` close-up, a
`Courant_hotspots_<stage>_final.png` threshold view containing only cells above
70 percent of the actual maximum, velocity/Cp frames and MP4 or GIF
animations directly from the OpenFOAM reader.

The active solver configuration is topology-aware:

- closed external airfoil: at most 15 PIMPLE outer loops, two pressure
  correctors, emergency `maxCo=50` and second-order implicit `backward`;
- open connected cavity: at most 15 outer loops, two pressure correctors,
  emergency `maxCo=20`, `limitedLinearV 1` momentum and a smaller physical
  `maxDeltaT_star` ceiling;
- both can finish the outer loop early when `U` and `nuTilda` reach absolute
  residual `1e-4`; Validation Lab removes this adaptive exit;
- both use Spalart-Allmaras. Other turbulence model names are rejected until
  their complete field/boundary contract is implemented.
- both SIMPLE initializers allow up to 10000 iterations and use
  `nNonOrthogonalCorrectors=0`. This is the OpenFOAM 14 usual steady-state
  setting; an earlier open-profile override of 1 was removed.

`backward` is implicit. `maxDeltaT` controls the intended physical resolution;
the larger Courant values are nonlinear emergency guards, not accuracy
targets or explicit Euler stability bounds. OpenFOAM adjusts `deltaT`
automatically and never exceeds the configured `maxDeltaT`.

The **Caso OpenFOAM** page can import/export the complete solver JSON and save
it as a named **Solver Configuration** package inside the selected Results work
case. Restoring the whole workspace restores that package after geometry, case
and mesh, so its values are visible and editable in the application. Optional
numeric controls explicitly marked as such accept an empty value: the JSON
stores `null` and the writer omits the OpenFOAM entry. Required time/physics
controls cannot be blank.

The transient `fvSolution` follows the OpenFOAM final-solve pattern:
`pFinal`, `UFinal` and `nuTildaFinal` inherit their normal solver and set
`relTol 0`. Intermediate PIMPLE corrections can therefore stop at relative
tolerance, while the last correction reaches the absolute tolerance. It is not
forced onto a zero-non-orthogonal-correction SIMPLE loop, where there is no
equivalent sequence of cheap repeated pressure solutions.

`divSchemes` control finite-volume divergence terms, especially convection.
`linearUpwind` is second-order and upwind-biased but not strictly bounded;
`limitedLinearV 1` applies one strong limiter to all velocity components;
`upwind` is first-order and bounded. The `bounded` prefix used during SIMPLE
adds the continuity-error contribution while the stationary solution is still
converging. This improves robustness but does not change the physical
turbulence model.

Diagnose an existing transient log without executing a solver:

```bash
python CFD_2D/scripts/ramair_2d_courant_diagnostics.py \
  --case CFD_2D/openfoam_cases/reference_uncut_validation_1m/alpha_p8p000 \
  --output CFD_2D/reports/courant_diagnostics_closed_alpha8.json
```

The measured closed validation run at 8 degrees used only 1.04% of its
`maxDeltaT` ceiling while reaching 99.8% of `maxCo`; the open smoke run was
also Co-limited. Increasing `maxDeltaT` therefore does not accelerate either
case. The optional `--locate-max-courant` mode runs `CourantNo` and
`cellMax(Co)` on a disposable case copy to report the controlling cell and
location.

The verified open software-test environment is
`Results/Open_RamAir_RANS_URANS_Smoke_20260723`. Its 421,131-cell mesh passes
OpenFOAM 14 `checkMesh`; the saved packages contain geometry, operating case,
mesh, real RANS-to-URANS partial run and final postprocessing. This is a
software/topology result, not a converged aerodynamic validation.

The comparison workspace
`Results/Open_RamAir_comparison_M0p15_Re1p9e6` uses the scaled 1 m open
geometry, Mach 0.15, Re=1.9e6 and the same thermodynamic similarity condition
as the closed LS(1)-0417 validation workspace. Its circular domain extends to
50 chords and permits 3.5c cells at the farfield boundary. The mutable active
zero-thickness mesh has 302,692 cells and passes `checkMesh`; its measured
quality is max non-orthogonality 41.464 deg, max skewness 0.6722, min
determinant 0.06288, min interpolation weight 0.09152 and min volume ratio
0.13434. Saved 337,981- and 327,909-cell Results packages remain available
for direct comparison and are not silently overwritten; none is evidence of
mesh independence by itself.

### Adaptive and fixed time stepping

Solver schema 14 exposes `time_step_mode` for the general workflow:

- `adaptive_physics_limited` writes `adjustTimeStep yes`, normally limits the
  step with the physics-derived `maxDeltaT` and retains `maxCo` as an emergency
  guard;
- `adaptive_courant` remains as the conservative compatibility mode;
- `fixed` writes `adjustTimeStep no` and applies the requested dimensional
  `deltaT`.

Adaptive stepping remains the default. On the selected open mesh, fixed
`deltaT*=0.004` produced cell Courant numbers above `3.5e5` and diverged.
Fixed `deltaT=4e-8 s` completed a bounded test near `Co=1`, but was no faster
than the adaptive value of approximately `4.63e-8 s`. The fixed option is for
controlled sensitivity studies after measuring the local Courant limit; extra
outer correctors are not a substitute for a stable time step.

### Physical-frequency and duration budget

`ramair_2d_timestep_advisor.py` implements the joint space/time workflow from
Cummings, Morton and McDaniel without pretending that one universal
`deltaT` exists. It distinguishes:

1. the fastest selected physical frequency, expressed as `St_max`;
2. the averaging duration required by `St_min` and the requested cycle count;
3. the configured fixed step or adaptive `maxDeltaT`;
4. the actual local mesh/Courant limit measured by a solver diagnostic.

For the 1 m validation scale, `U=51.04384 m/s` and `t_c=0.0195910 s`.
The published `deltaT=2.5e-4 s` is `deltaT*=0.01276096`. With the deliberately
conservative screening value `St_max=20`, Nyquist permits `0.025`, but the
project target of 20 samples per cycle requires `deltaT*<=0.0025`. This
screening range must be replaced by measured force and pressure-probe spectra;
it is not a universal ram-air constant.

The retained closed-mesh diagnostic is much more restrictive:
`deltaT*=2.6753e-5` at `Co` approximately 1, controlled by a cell downstream
of the lower rounded TE. The historical open diagnostic is similarly
mesh-limited and must be repeated on the current open mesh. More boundary
layers alone did not remove the closed hotspot. Correct the local TE/BL-front
topology before increasing `maxCo` or launching `t*=319`/`680` production
runs.

Each new case contains `time_step_assessment.json/.md`. The complete
reasoning and the proposed equal-duration mesh/time/subiteration matrix are in
`CFD_2D/reports/TRANSIENT_TIMESTEP_MESH_SOLVER_STUDY_20260728.md`.

The independent **Validation & Convergence Lab** uses a stricter fixed-step
A-E contract and one common SIMPLE checkpoint per mesh. It is dry-run by
default, caps MPI at eight ranks, requires a real pilot before production and
can resume only the unfinished A-E stages without restoring the checkpoint
over a partial solution. Its operation and CLI are documented in
`CFD_2D/validation_studies/README_VALIDATION_CONVERGENCE_LAB.md`.

Automatic field products are named
`Cp_airfoil_RANS_final.png`, `Velocity_RANS_final.png`,
`Cp_airfoil_URANS_final.png` and `Velocity_URANS_final.png`. The application
shows these final states and exposes MP4/GIF animations only after the user
presses **Visualizar animacion**.

### Staged steady-to-transient execution

The OpenFOAM Foundation `airFoil2D` steady tutorial uses `ddtSchemes default
steadyState`, SIMPLE and `residualControl`. This project uses the same numerical
pattern only as an optional initializer; the final analysis remains transient
PIMPLE with the time scheme selected in the active solver configuration
(`backward` in the current topology presets). Prepare a dry plan first:

```bash
python CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py \
  --case CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000 \
  --steady-initialization --execution-backend pyfoam --n-cores 6
```

Add `--run` only after reviewing `staged_run_plan.json`. Transition requires
the SIMPLE convergence message or acceptable final p/U/nuTilda residuals and
stable Cl/Cd/Cm/Cl-over-Cd over two adjacent sample windows. The default is
500 samples per window. The report expresses mean change, final-window drift
and standard deviation as percentages for all four quantities. If the gate is not met, the command exits successfully with
`STEADY_AWAITING_USER_DECISION`; the application can extend SIMPLE from the
latest iteration, start PIMPLE explicitly with those fields, or archive and
finish. The steady fields, logs and coefficient history are moved to
`steadyInitialization/history/` before physical transient time starts. A
standalone ParaView case retains selected SIMPLE iterations plus the final
state; its time coordinate is explicitly an iteration counter. Transferred
fields become transient `0/`, and PIMPLE starts at physical `t=0`. The
transition discovers the required volume fields from the active initial
condition and turbulence model. It requires at least `U` and `p`, transfers
the active primary turbulence state (`nuTilda`, or `k`/`omega`, etc.) and
copies available restart helpers such as `phi`, `nut` or `alphat`. Some
OpenFOAM solver/module combinations mark `phi` as `NO_WRITE`; in that valid
case PIMPLE reconstructs its initial face flux from the transferred velocity
field. Every copied field is checked by a normalized SHA-256 digest. After the
URANS stage, `steady_to_transient_continuity.json` compares those digests, the
final SIMPLE force sample, the exact transient `t=0` sample and the first
solved URANS step separately.

The stationary OpenFOAM 14/13 `SIMPLE` dictionary includes `pRefCell 0` and
`pRefValue 0`. The staged runner also upgrades an older generated template
before using it. The robust Spalart-Allmaras initializer uses cell-limited
gradients, bounded upwind convection for `nuTilda`, zero non-orthogonal
corrections on the accepted closed mesh. The balanced v3 bootstrap uses
bounded first-order upwind U/nuTilda convection, 0.5 cell-limited gradients,
GAMG/DIC for pressure, PBiCGStab/DILU for U/nuTilda and relaxation factors
`p=0.3`, `U=0.5`, `nuTilda=0.5`. A fresh stage runs `potentialFoam` first by default;
use `--no-steady-potential-foam` only for an intentional comparison. These
choices affect numerical initialization, not the transient RANS model.

The maximum SIMPLE iteration count is a ceiling, not a requirement. Residual
and Cl/Cd/Cm plateau criteria can transition earlier; a wall-clock timeout also
preserves the finite stationary fields for the configured transition decision.

OpenFOAM 14/13 `potentialFoam` solves the auxiliary potential field `Phi`. The
steady template therefore contains an explicit `Phi` GAMG/DIC solver entry;
the staged runner also upgrades cases written before this correction. A
preconditioner failure is reported with its real stage and `PyFoam*.logfile`
path instead of incorrectly pointing to a nonexistent `log.foamRun`. PyFoam
live windows start when the SIMPLE solver begins, after `potentialFoam` and
parallel decomposition have completed successfully.

If `nuTilda` or continuity grows beyond a conservative diagnostic limit, the
PyFoam worker requests `stopAt writeNow` before the eventual floating-point
exception. A diverged SIMPLE attempt is reported as `STEADY_STAGE_DIVERGED`,
archived under `steadyInitialization/history/`, and the original transient
`0/` is restored. It cannot be transferred through the normal convergence
gate. A merely unconverged but finite stage still remains available for the
explicit extend/start-transient/finish decision.

### Resume and alpha sweeps

Resume a partial reconstructed run and add 20 convective time units:

```bash
python CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py \
  --case CFD_2D/openfoam_cases/reference_uncut/alpha_p4p000 \
  --resume --resume-additional-time-star 20
```

Write all cases before launching a sweep:

```bash
python CFD_2D/scripts/ramair_2d_openfoam_case_writer.py \
  --case-root . --variant reference_uncut --alphas -4 0 4 8 12 \
  --reynolds 4000000 --write-case --require-converted-polymesh

python CFD_2D/scripts/ramair_2d_openfoam_sweep.py \
  --case-root . --variant reference_uncut --alphas -4 0 4 8 12 \
  --timeout-min-per-alpha 120 --stop-when-force-stable \
  --postprocess-after-each
```

The second command is still dry-run. Add `--run` explicitly to execute. The
statistical gate compares both means and standard deviations of Cl/Cd/Cm in
adjacent convective-time windows, so a stable periodic response can pass even
when instantaneous coefficients do not become constant. A per-angle timeout
keeps written fields and can continue to the next alpha when
`--continue-after-timeout` is enabled.

### PyFoam monitors and wall analysis

PyFoam executes the same OpenFOAM solver. Its current live and replayed views
are limited to residuals, Cl, and combined Cd/Cm histories.
Continuity, Courant, execution-time and deltaT plots are disabled in the
PyFoam watcher. Courant history remains available as a separate postprocess
diagnostic parsed from the solver log. To keep the coefficient axes useful,
only the display omits the first startup samples and replay is limited to a
recent window. Live windows use the authoritative PyFoam/OpenFOAM files with a
headless Matplotlib snapshot embedded in Streamlit, replacing the blank
Gnuplot FIFO copy-mode path. Snapshot generation and Streamlit image refresh
use a 30-second interval; in headless mode no parsing or rendering is done
between snapshots. Cl is displayed over `[-0.8, 2]`;
Cd/Cm are displayed over `[-0.2, 0.2]`. Values outside those ranges are counted in
`pyfoam_run_report.json` but remain unmodified in the complete
`forceCoeffs.dat` history.

New circular-farfield cases use the OpenFOAM 14/13 paired
`freestreamVelocity`/`freestreamPressure` conditions and scalar `freestream`
for `nuTilda`. The legacy fixed-velocity/zero-gradient fallback remains
selectable as `fixed_velocity_fallback` in `cfd2d_solver_config.json`.

Transient volume fields use `adjustableRunTime`: `field_write_interval_s`
therefore means physical simulated seconds. `adjustTimeStep` enforces `maxCo`
and may recover `deltaT` only up to `maxDeltaT`. `purgeWrite` limits retained
3D snapshots, while force coefficients and residuals remain available at every
time step.

A bounded benchmark matrix is available through
`CFD_2D/scripts/benchmark_solver_backends.sh --run`. The July 23 validation
smoke comparison used the same 334857-cell mesh for old/new PIMPLE controls,
6/8 MPI ranks and native/PyFoam execution. One outer PIMPLE loop reduced the
measured first-step solver time from 4.10 to 3.06 s at 6 ranks; the optimized
8-rank native case took 2.48 s. PyFoam produced the same OpenFOAM step and
coefficients but added monitor/orchestration overhead. See
`CFD_2D/reports/solver_benchmark_matrix_20260723.json`.

The mesh page reports the y+-derived or manual `y1`, total geometric prism
height and turbulent flat-plate delta99 estimate. These are planning values,
not proof that the real boundary layer is attached or zero-pressure-gradient.
With `--run-openfoam-postprocess`, the postprocessor samples real U profiles
normal to both surfaces at the configured `x/c` stations. A missing 99% crossing
is reported as `NOT_REACHED_ON_SAMPLE_LINE`; it is not replaced by a fabricated
thickness. The same wall analysis exports real upper/lower `Cp(x/c)` and y+
curves; ParaView prioritizes the volume `Cp` field when it exists.

`average_from_fraction` controls only post-processing of force coefficients. For example, `0.6` means: ignore the first 60% of the available force history and compute mean/std over the final 40%. It is used to reduce startup-transient contamination; it is not a CFD boundary condition or solver parameter.

For the extruded 2D OpenFOAM mesh, `forceCoeffs` uses `Aref = chord * spanwise_thickness_m` and `lRef = chord`. Using `Aref = chord` would make coefficients artificially small by roughly the extrusion thickness factor.

For a full copy/paste Ubuntu sequence, use `Documents and Manuals/Application/RUN_REFERENCE_UNCUT_GMSH_OPENFOAM_COMMANDS.txt`. For a configurable workflow, edit `CFD_2D/CFD_2D_inputs/config/cfd2d_workflow_config.json` and regenerate `Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh` with:

```bash
python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --plan
python CFD_2D/scripts/ramair_cfd2d_workflow_tool.py --case-root . --write-script "Documents and Manuals/Application/run_cfd2d_custom_case_wsl.sh" --overwrite
```

## Open Ram-air Cases

Open profiles use one connected external/internal fluid region through the
inlet. The name `ram_air_inlet` is forbidden as an OpenFOAM patch. The default
`zero_thickness_base_profile` representation reconstructs the uncut base
profile across the opening, creates the exterior BL on that complete contour
and selectively stitches only the duplicated nonphysical inlet-interface
nodes. Only the actual cut upper/TE/lower contour is a wall. The older
`finite_thickness_fabric` topology remains selectable.

Use `Documents and Manuals/Application/run_open_ramair_debug_wsl.sh` or `Documents and Manuals/Application/RUN_OPEN_RAMAIR_GMSH_COMMANDS.txt` for the first open-profile run. The standard setting is `open_wall_curve_method=segmented_outer_splines`. Exterior and cavity remain triangular, the cavity side has no BL, and its upper/lower/TE node counts are independently reduced by `open_inner_wall_node_factor` and `open_inner_te_node_factor`.

The selected zero-thickness circular-50c preset uses 2,800 contour segments,
32 exterior TE nodes, interior factors 0.40/0.28, 50 BL layers, growth 1.075,
manual `y1=25 um` and Frontal-Delaunay. The curved inlet is an exact
rotated/scaled/translated copy of the uncut base-profile arc. A short
`min(inlet tangential spacing, 8*y1)` compatibility field is followed by
cavity-side Gmsh `Extend`; exterior sigmoid `Threshold` stages progress to
`0.08c`, `0.20c` and `3.5c`. The mutable active
`open_ramair_validation_1m` mesh has 302,692 cells and passes OpenFOAM 14
`checkMesh` with maximum non-orthogonality 41.464 degrees, maximum skewness
0.6722, minimum determinant 0.06288, minimum interpolation weight 0.09152 and
minimum volume ratio 0.13434. It remains a solver-test/comparison candidate,
not an aerodynamically validated mesh. Saved 337,981- and 327,909-cell meshes
remain separate Results packages for comparison.

The following finite-thickness parameters apply only when
`open_geometry_representation=finite_thickness_fabric`:

`open_inlet_marker_transfinite_nodes` controls tangential nodes on the coincident nonphysical inlet interfaces. The default is 176 and it does not discretize a wall. `open_inlet_marker_bump_strength=0.60` refines both lips without increasing uniformly the opening. `open_surface_target_nodes=1920` is distributed over the exterior contour; upper/lower branches use `Bump=0.60` at both ends and `open_te_transfinite_min_nodes=40` controls the exterior cap. The selected candidate uses `open_inlet_bridge_smoothing_enabled=true`: only the exterior BL-carrying bridge is a tangent Bezier, while the cavity bridge remains straight to prevent crossing the narrow connector. `open_inlet_bridge_smoothing_handle_fraction=0.080` is geometric and is not a cell-size setting. `open_boundary_layer_lip_fan_points` is used only when the optional fan mode is selected. Inner upper/lower walls and the inner TE retain independent factors. The open BL default is 50 layers, growth 1.10 and y+-derived first-cell height. `airfoil_wall` contains only outer/inner fabric walls and finite-thickness lip caps.

`forceCoeffs` integrates every patch whose OpenFOAM type is `wall`. In the
current open topology those surfaces are grouped as `airfoil_wall`, containing
the external skin, internal skin, both lips and the trailing edge. They are
therefore treated as one rigid aerodynamic body. The coincident inlet bridge is
an internal fluid interface, is merged before extrusion and is never included
as a force patch.

The current open-profile topology baseline uses the exterior-Bezier/interior-
line, no-fan, graded-quad configuration with inlet transition growth `1.22`
and smoothing handle fraction `0.080`. A fresh Gmsh 4.15.2 conversion contains
432,650 cells and passes the full OpenFOAM gate with `Mesh OK`: maximum non-
orthogonality 65.184 degrees, maximum skewness 3.471, minimum determinant
0.002194, minimum interpolation weight 0.08171 and minimum volume ratio
0.08896. The finite-thickness transition is resolved automatically as 16
nodes. Tests with 12 nodes failed interpolation-weight checks and 20 nodes
failed skewness; `0.06c` and `0.10c` smoothing handles also passed, but the
`0.08c` case provides the best balanced margins. Short-edge points are a
diagnostic inventory;
their minimum length equals the requested `y1` and the geometry checks found
no duplicate points. This 20c debug domain proves topology and conversion, not
aerodynamic validation or mesh independence. Failed alternatives and their
exact VTK sets remain archived; the clean active PASS mesh intentionally has
no problem VTK files. See
`CFD_2D/reports/OPEN_INLET_CURVATURE_LAYER_STUDY_20260722.md`.

The problem-cell ParaView launcher ignores stale registry/recovery state,
loads the exact archived `checkMesh` VTK sets and draws thick edge tubes around
the relevant failed entities. On Ubuntu/WSL it also supplies the system
`dist-packages` path required by the packaged ParaView 5.10 Python module.

Mesh levels are editable starting sets, not permanent modes. All levels share
profile cleanup, rounded TE, wall-node distribution, y1 policy, BL growth and
BL-front matching. `coarse`, `medium` and `fine` request 20, 40 and 50 normal
layers and vary only the cavity/nearfield/farfield refinement. A restored or
edited JSON has priority and is reported as `custom`. Domain shape and every
domain dimension are read only from the active mesh configuration.

For the closed `reference_uncut` validation candidate, use domain
`circular_50c` and preset
`CFD_2D_inputs/config/mesh_presets/reference_uncut_validation_candidate.json`.
The verified corrected-TE mesh has 392,450 cells (99,323 hexahedra and 293,127
prisms), 50 prism layers, growth 1.10, y+ target 1, maximum non-orthogonality
38.168 degrees, average non-orthogonality 4.671 degrees, maximum skewness
0.6009, minimum determinant 0.001707, minimum interpolation weight 0.1761 and
minimum volume ratio 0.2137. `checkMesh`
returns `Mesh OK`. It is a validation candidate; aerodynamic validation still
requires at least a mesh-independence comparison.

For transient runs, `--stop-when-force-stable` does not assert steady-state
convergence from residuals. After `convergence_minimum_time_star`, it compares
Cl, Cd and Cm means and standard deviations in two adjacent windows of width
`convergence_window_time_star`. If every normalized change is within tolerance,
the runner writes `convergence_monitor.json`, requests `stopAt nextWrite` and
records `CONVERGED_STATISTICALLY`. A sweep scheduler may then start its next
case; the single-case runner intentionally does not launch another alpha by
itself.

## LS(1)-0417 validation work case

The reusable application workspace is
`Results/LS1_0417_validation_M0p15_Re1p9e6`. It contains a named geometry
package, the complete `M=0.15`/`Re=1.9e6` polar definition and the approved
`reference_uncut_validation_1m` mesh. Select it in the application sidebar and
load the complete workspace before writing a new alpha. The immutable technical source
remains `CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6`.

The validation-only geometry is rebuilt at the paper's 1 m chord. The paper
uses `dt=2.5e-4 s`, 25,000 time steps and averages the last 10,000. At Mach
0.15 this is `dt*=dt U/c=0.01276096076`. With fixed `T=288.15 K` and
`mu=1.7894e-5 Pa s`, matching both Mach and Reynolds requires
`rho=0.6660666 kg/m3` and ideal-gas `p=55.093 kPa`; this is a similarity
condition, not literal sea-level density. Postprocessing adds a project point
only when the Mach/Reynolds metadata match and the run is completed or statistically
converged. Non-converged partial runs remain in the validation audit only.

The active laptop default now runs a first production interval of `t*=20` and
the resume action adds another `20 t*`. Short smoke runs remain available by
editing `endTime_star`, but they are not validation results. A bounded
six-rank run on the current approved 334,857-cell mesh gives conservative
projections of approximately 14 minutes, 36.3 hours and 363 hours
respectively because `maxCo` reduced the actual time step below the nominal
value. The very short benchmark includes startup overhead. See
`reports/LS1_0417_VALIDATION_EXECUTION_AUDIT_20260722.md`.

For each additional angle, restore the complete work case, write the selected
OpenFOAM alpha using the existing mesh, run it explicitly, postprocess it, and
save its Simulation/Postprocess packages back into that same work case. The
case-local `Validation/` CSVs and plots update only after selecting the
postprocessed angles and pressing **Anadir puntos validos a la grafica**.
This explicit publication step prevents partial or merely provisional runs
from entering the validation polar.

## Alpha=8 mesh-refinement study

`Results/LS1_0417_alpha8_mesh_refinement` indexes three independent meshes and
cases without duplicating their large `polyMesh` data:

- coarse: 203,691 cells, max non-orthogonality 35.709 deg, max skewness 0.568;
- medium: 333,826 cells, existing alpha=8 data preserved;
- fine: 618,382 cells, target y+=0.5, 65 layers, growth 1.075, max
  non-orthogonality 35.715 deg and max skewness 0.584.

All three currently have `checkMesh=OK` and quality `PASS`; the new coarse and
fine control dictionaries target `t*=20`. Run the prepared
coarse/medium/fine cases, postprocess them, then execute:

```bash
python CFD_2D/scripts/ramair_2d_mesh_refinement_analysis.py --project-root .
```

The analyzer produces coefficient-versus-cell-count, experimental-deviation,
Cp overlay and total runtime plots only from real available results. Until at
least two levels have real outputs, those plots are explicitly withheld.
The exact audit and current provisional/final distinction are recorded in
`reports/SOLVER_SWEEP_TEMPORAL_AND_MESH_REFINEMENT_AUDIT_20260723.md`.

## Joint closed/open convergence workspace

The work case
`Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6` is the common
starting point for future spatial and temporal convergence analyses at
`M=0.15`, `Re=1.9e6` and `c=1 m`. It contains these real meshes:

| Topology | Coarse | Medium | Fine |
|---|---:|---:|---:|
| Closed LS(1)-0417 | 203,691 | 333,826 | 618,382 |
| Open ram-air | 269,864 | 302,692 | 420,728 |

All six were generated with Gmsh 4.15.2, converted with `gmshToFoam` and pass
OpenFOAM 14 `checkMesh`. Their exact determinant, interpolation-weight,
volume-ratio, skewness and non-orthogonality metrics are stored in
`Convergence Study/mesh_quality_matrix.csv`.

Select the work case in the application and use **Geometria y nivel de malla**
to load one of `closed_coarse`, `closed_medium`, `closed_fine`,
`open_coarse`, `open_medium` or `open_fine`. The action restores the matching
Geometry, CFD Case and Mesh packages together. The work case defaults to
`closed_medium`; individual stage loading remains available for diagnostics.

The setup can be reproduced without running a solver:

```bash
python CFD_2D/scripts/ramair_2d_closed_open_convergence_study.py \
  --project-root . \
  --build open_coarse open_fine \
  --gmsh-timeout-s 900 \
  --existing-action archive
```

Omit `--build` to validate existing real reports and rebuild only the work-case
container. The script rejects missing `polyMesh`, failed `checkMesh`, weak
mandatory quality metrics or non-monotonic cell counts. A passing series is
only a prerequisite for grid-convergence analysis, not its conclusion.

### Validation & Convergence Lab

The application contains a separate **Validation & Convergence Lab** page for
the common `alpha=8 deg`, `M=0.15`, `Re=1.9e6`, `c=1 m` study. It owns
`CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8` and a dedicated
state file; it never overwrites the general active workspace.

The page registers the six real mesh triplets, provides paper-reference and
feasible-halving `deltaT` matrices, calculates wall-time/frequency budgets and
writes fixed-step A-E cases. Storage is audited separately under Reports;
it is not mixed into the pre-run temporal budget. Every real run requires a
common SIMPLE checkpoint and a real bounded pilot first. Solver execution
remains explicit and sequential with at most eight MPI ranks.

The schema-6 laboratory UI is divided into six stable top-level sections for
meshes/conditions, solver strategy, RANS, URANS, space-time convergence and
reports/workspace. The default transient startup is 25/25/50 Euler steps at
0.25/0.5/1.0 target `deltaT`; the short pilot is 10/10/20 Euler steps followed
by 30 `backward` steps. Stage-E-only statistics and explicit user acceptance
are required before a run can enter space-time or frequency comparisons.
RANS postprocessing never generates new Courant products.

Detailed workflow, acceptance gates, GCI limitations and commands are in
[`validation_studies/README_VALIDATION_CONVERGENCE_LAB.md`](validation_studies/README_VALIDATION_CONVERGENCE_LAB.md).

## Numerical boundary-layer thickness

At each requested x/c station the postprocessor locates the nearest upper or
lower wall face and constructs an outward normal sampling line. OpenFOAM
`sets` with `cellPoint` interpolation samples `U` and `p` along that line.
The velocity is projected onto the local wall tangent; the local edge speed
`Ue` is the median of the outermost 10 percent of valid samples. Numerical
`delta99` is the first crossing of the monotonic velocity envelope through
`|U_t|/Ue=0.99`, linearly interpolated between adjacent sample points.

This is a CFD-derived diagnostic, not an experimental measurement. It is
reported as unavailable if the sampled line has no usable crossing. The plot
also shows the zero-pressure-gradient turbulent flat-plate estimate and the
total prism-stack height; those are planning references and need not coincide
with the pressure-gradient boundary layer on the airfoil.

With `open_near_wall_size_from_bl=true`, the local TE, inlet and lip-cap targets
come from the final BL-layer height and the corresponding tangential spacing.
They control the first triangle leaving the BL front, not the wall-normal `y1`.
Local fields are restricted to their fluid surfaces and stop at their configured
distance, preventing fine inlet/TE sizes from flooding the complete cavity or
farfield.

Install the validated Gmsh 4.15.2 binary with `bash "Documents and Manuals/Application/install_gmsh_4_15_wsl.sh"`. Gmsh 4.8.4 is rejected for BL runs by default: on the same closed geometry it produced edge-recovery errors and collapsed TE triangles, while 4.15.2 completed without degenerate 2D elements. Override with `--allow-legacy-gmsh-boundary-layer` only for diagnosis. The automatic triangle target outside the BL is based primarily on tangential BL-front spacing; omitting it would not change the shared BL edge, but could let the Delaunay third vertex jump directly toward the coarse farfield size.
