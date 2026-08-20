# RamAir Project Context for Codex

Context version: 2026-08-20
Application backend API: 26
Validation Lab schema: 11
Solver configuration schema: 15
Work Case manifest schema: 3
Active workspace schema: 4
Geometry DTO schema: 1
Profile catalogue schema: 1
Mesh-science report schema: 1
Execution lifecycle schema: 1
Postprocess manifest schema: 3
ParaView products manifest schema: 1
Canonical Windows source: `C:\Users\alejm\Desktop\PRACTICAS_INVICSA\3D design\DESIGN APP`  
Canonical WSL runtime: `/home/alejm/ramair_cfd/DESIGN_APP`

## 1. Purpose

RamAir: Design and CFD combines Python preprocessing, CATIA V5 automation,
airfoil preparation, Gmsh 2-D meshing, OpenFOAM RANS/URANS execution and
scientific postprocessing. The current priority is a robust 2-D validation
workflow for closed and open ram-air sections. Full 3-D CFD, FEM and FSI are
future work and must not be inferred from diagnostic placeholders.

## 2. Safety and execution

- Never execute CATIA from automated tests.
- OpenFOAM runners are dry-run by default. A real solver requires an explicit
  user action and is not part of normal pytest.
- Gmsh, `gmshToFoam` and `checkMesh` may be used only as bounded verification.
- Never fabricate fields, mesh results, quality PASS states or empty `polyMesh`.
- Do not delete data outside an explicit lifecycle action. Work Case schema-3
  migration is metadata-only: it may back up and atomically replace small
  `case_manifest.json` files, but it must not move, duplicate or delete package
  artifacts.
- Maximum supported MPI ranks remain eight and the laboratory has one atomic
  solver lease.

## 3. Canonical entry points

- `preprocess_ramair_main.py`: geometry/CATIA/CFD input preprocessing.
- `Generate_RamAir_Canopy_MAIN.CATScript`: CATIA V5 model generation.
- `run_ramair_cfd2d_app.py`: official Windows-to-WSL application launcher.
- `CFD_2D/app/ramair_cfd2d_app.py`: Streamlit shell.
- `CFD_2D/app/workflow_backend.py`: API 26 orchestration boundary.
- `CFD_2D/app/validation_convergence_page.py`: isolated Validation Lab UI.

The UI orchestrates. Geometry, meshing, solver and postprocess algorithms live
under `CFD_2D/scripts`.

All new runtime publications use the schema-1 lifecycle states `PREPARED`,
`RUNNING`, `PAUSED_RECOVERABLE`, `FAILED`, `COMPLETED`, `REVIEW_REQUIRED`,
`APPROVED` and `REJECTED`. Legacy spellings remain readable. Each case stores
an atomic `.ramair_execution_state.json` with phase, idempotency key, process
identity, restart evidence and a bounded transition history. Solver logs and
the scalar function-object trees are never governed by `purgeWrite`.

General postprocess manifests use schema 3 with paths relative to their
manifest directory. Force statistics use the final continuous history segment
and record the exact selection in `postprocess_window_manifest.json`.
ParaView products publish relative case/state references, a portable Python
loader and one `visualization_scales.json` shared by still images and animation
frames. Cp, velocity/streamlines/contours, vorticity and y+ are rendered only
when their real arrays exist.

The normal workflow requires a selected persistent Work Case. `Estado`, `Caso
de trabajo`, `Validation & Convergence Lab` and file/log inspection remain
available without one. Geometry uses `ramair_geometry_workspace.py` as the
shared schema-1 DTO/catalogue boundary: imported sources retain their original
bytes under `Airfoil Profiles/Imported/<uuid>/original`, and crossport geometry
is expanded into explicit per-hole records before preprocessing.

## 4. Data ownership

- `Airfoil Profiles`: all source and generated `.csv/.dat` airfoils.
- `CATIA/Inputs`: only CATIA-readable inputs.
- `CATIA/Exports`: CAD exports.
- `CFD_2D/CFD_2D_inputs`: CFD case packages.
- `CFD_2D/meshes`: active Gmsh/OpenFOAM mesh outputs.
- `CFD_2D/openfoam_cases`: ordinary OpenFOAM cases.
- `CFD_2D/validation_studies`: isolated convergence laboratory.
- `Results`: explicitly saved reusable work cases and curated outputs.
- `Previous Versions`: deliberate backups; never an active source tree.
- `Documents and Manuals`: specifications, manuals and technical decisions.

Heavy execution belongs in the Linux WSL filesystem. The Windows source is
synchronized atomically by the official launcher.

### 4.1 Work Case schema 3

Every Work Case owns a stable `work_case_id`. Every geometry, CFD case, mesh,
solver, simulation and postprocess package owns a stable `entity_id` and a
SHA-256 `revision_id`. A revision records provenance, artifact inventory and
explicit upstream dependencies as `{role, entity_id, revision_id}`. The
top-level `entities` and `active_entities` objects are indexes; stage packages
remain the source of truth and retain their existing folders.

Approvals belong to one immutable revision. Editing a package preserves the
old decision in `revision_history`, creates a new revision and resets only that
new revision to `pending`. When an active upstream entity or revision changes,
dependent packages become `stale`; they remain visible with warnings and are
never silently restored. Selection order is active compatible revision, most
recent compatible revision, then explicit creation by the caller.

The Mesh UI resolves reusable packages against the exact active geometry
entity/revision. Compatible saved meshes can be loaded or used as a
configuration base; incompatible ones remain visible with warnings. Mesh
configuration edits are active drafts (`sync_workcase=False`) until the real
mesh is explicitly saved/replaced as a Work Case package, preventing a draft
from changing an approved artifact revision. `MESH_APPROVED.flag` remains the
technical eligibility of the active output; schema-3 package approval is the
durable human decision with actor and evidence.

Mesh presets implement the reviewed fractional sequence Coarse `y+=1`, Medium
`2/3`, Fine `4/9` and Extra Fine `8/27`. Coarse/Medium/Fine use 50 layers;
Extra Fine retains 75 as a comparison because the cited study reports marginal
benefit above 50. Growth is 1.10. The builder audits the existing skin-friction
first-cell estimate against the requested laminar and turbulent formulae and
uses the smallest positive height when y+-derived height is enabled. Algorithm
6 remains the general/open baseline while measured closed presets can retain
algorithm 5. Curvature-aware node counts are calculated before transfinite
constraints. The bounded fixture runner never replaces an active mesh.

Schemas 1 and 2 are accepted through a read-only adapter. An explicit
`ramair_case_library.py migrate --apply` creates a manifest backup under
`Previous Versions/Results Library Manifest Backups`, writes schema 3
atomically and generates `Results/work_case_index.json`. The index classifies
legacy cases and marks the three protected scientific Work Cases. No heavy
artifact is hashed or copied by the migration itself.

The migration was applied on 2026-08-20 to the five WSL Work Cases. Original
schema-2 manifests are retained in the backup tree. An immediate repeat dry
run was idempotent, and aggregate hashes excluding `case_manifest.json` were
identical before and after for all three protected Work Cases.

## 5. CFD stage boundaries

- Profile case builder prepares geometry and inputs.
- Mesh builder runs Gmsh only when explicitly requested.
- Quality controller evaluates a real mesh.
- Case writer writes OpenFOAM dictionaries and never creates empty `polyMesh`.
- Runner is dry-run unless `--run` is present.
- Postprocess distinguishes not-run, partial, error and completed data.
- `frontAndBack` is `empty`; `ram_air_inlet` is never a physical patch.

### 5.1 Durable execution control

Every general OpenFOAM run publishes `.ramair_solver_process.json` with the
real solver PID, process group and Linux process-start token. A clean stop is
requested through `.ramair_stop_request.json`; the runner changes
`controlDict` to `stopAt writeNow`, waits for a written checkpoint and only
then escalates SIGINT, SIGTERM and SIGKILL if required. A stopped or timed-out
case with a numerical time directory is `PAUSED_RESTARTABLE`, not failed.
Startup reconciliation repairs stale RUNNING/STOPPING records without deleting
fields. PIMPLE studies use the equivalent queue-level stop marker and stop
between bounded stages.

### 5.2 General URANS policy

Solver schema 15 makes `maxDeltaT_star` the user-requested physical ceiling;
the internal starting step is clamped to it. `adjustTimeStep` remains active
with emergency `maxCo=50` for closed profiles and `maxCo=25` for open
profiles. Closed PIMPLE permits at most 10 outer correctors and exits on
`U+nuTilda`; Open retains at most 15 and exits on `U+p`. Absolute tolerance is
`1e-4` with `relTol=0`. Validation Lab explicitly removes adaptive outer-exit
controls and retains fixed `deltaT` and corrector counts.

Steady RANS/SIMPLE initialization is capped at 20,000 iterations and feeds the
URANS stage; it is not a second result mode. `transportCorrectionFinal=false`
means transport/turbulence is corrected on every outer corrector and remains
the reviewed advanced default. Volume fields are written at approximately
2,000 requested physical steps. Forces, residuals and Courant/deltaT histories
remain continuous and are not subject to `purgeWrite`.

Reynolds, Mach and fluid properties come only from the CFD Case physical
configuration; chord comes from its selected variant manifest. The solver UI
does not own editable copies. Every prepared case writes
`applied_solver_configuration.json` with exact effective values and source
ownership.

### 5.3 Open-airfoil wall topology

The current open mesh already contains separate
`airfoil_wall_external` and `airfoil_wall_internal` boundary patches of type
`wall`; the opening is fluid continuity and is not a physical inlet patch.
`createBaffles` is therefore not required. It is appropriate when an internal
face zone must first be converted into boundary faces, but applying it here
would duplicate a wall that the mesh already owns. Force integration includes
both wall patches.

## 6. Validation Lab schema 11

The study ID is `closed_open_M0p15_Re1p9e6_alpha8`. It owns six mesh IDs:
`closed_coarse`, `closed_medium`, `closed_fine`, `open_coarse`,
`open_medium` and `open_fine`. Geometry, meshes and compatible RANS checkpoints
are preserved independently from URANS cases.

Schema 11 adds metadata-only campaign manifests under `campaigns/`. A campaign
stores the exact geometry/mesh dependencies, RANS checkpoint requirement,
temporal ladder, angles, settling and collection windows, acceptance rules,
case states and immutable approval revisions. Existing runs are indexed at
their canonical paths and are never copied. The full 3x6 matrix is available
for planning but cannot be started automatically.

Closed planning defaults to the optimized `C1/C2/M1/M2/F1/F2` sequence at 16
degrees, followed by confirmation at 8 degrees. The alternative Cummings
sequence remains explicit. Open planning freezes geometry, requires Coarse /
Medium / Fine RANS diagnostics at 8 degrees, then advances the Medium temporal
ladder progressively before crossing space or confirming 16 degrees. All
accepted comparisons require the same physical time, signal-based settling,
at least ten cycles and Welch PSD/coherence evidence including `W=1/St`.

### Canonical URANS identity

Exactly one mutable production case exists for each structured key:

`topology + mesh_id + mesh_level + alpha_deg + deltaT_s`

The serialized case ID retains sufficient `deltaT` precision and excludes
scheme, PIMPLE controls, timestamps and version numbers. Configuration hashes
are compatibility evidence, not identity dimensions. The only case location is:

`CFD_2D/validation_studies/<study>/runs/<topology>/<mesh_level>/<case_id>/case`

Active URANS has no pilot, attempt, archive, version or bypass concept.

### Presence and outcome

Presence and execution result are separate:

- `CasePresence`: `NOT_STARTED`, `STARTED`.
- `ExecutionOutcome`: `READY`, `RUNNING`, `PAUSED`, `COMPLETED`, `DIVERGED`, `ERROR`.

A prepared directory remains `NOT_STARTED`. `STARTED` requires a complete
positive time with restart fields and solver-execution evidence. A folder or
`controlDict` alone is insufficient.

### Actions

The service calculates one action from evidence:

- not started: start from the compatible RANS checkpoint;
- paused with complete fields: resume the exact phase/time;
- completed: review;
- incompatible or invalid state: exact confirmed restart.

Restart deletes only the canonical case chosen from a sorted selector of real
executions. The confirmation shows its ID, path, latest time and affected
fields; no manual transcription is required. It preserves meshes, RANS
checkpoints, shared configuration and Results, writes a deletion report and
does not archive the deleted URANS case.

### Progressive and direct execution

Progressive mode executes A-E. Before D uses `backward`, stage C retains the
current target-`deltaT` state and two complete old-time states; only stage E
contributes statistical samples. Direct mode records the natural first-order bootstrap when
`backward` has insufficient old-time history. Internal start modes are
`FRESH_FROM_CHECKPOINT`, `CONTINUE_STAGE` and `RESUME_EXISTING`; they are not a
user-facing selector.

Each operation and phase has immutable decomposition, solver and reconstruction
logs. `stage_journal.json` records input/output checkpoints, fields, common
processor times, PID, wall time and terminal reason. Every transition closes
and validates its preceding solver transaction before writing the next phase.
The boundary writer reads OpenFOAM's persisted global `timeIndex` and selects
an interval that divides the target global index; phase-local step counts alone
are insufficient after `startFrom latestTime`.
Every generated phase command validates the planned start of that specific
phase; it must not reuse the initial resume time for later C/D/E boundaries.
The normal `FOAM_SIGFPE` banner is informational; only a fatal trace is a
floating-point failure. Secondary cleanup failures never replace the primary
error.

Legacy false-positive records are repaired by an auditable metadata-only
operation. The original classification remains in the correction report, the
cursor is reconstructed from real times, and no solver is launched or field
rewritten merely to change status.

The actual checkpoint `polyMesh` and fields are the source of truth for URANS
initialization. Stored registry hashes are evidence only and are recalculated.
If a stored package hash is stale but the actual prepared URANS `polyMesh` is
byte-identical to the actual checkpoint `polyMesh`, the content hash is adopted
and the original value is retained in `mesh_identity_correction.json`. A real
content mismatch still blocks resume.
Cell/face counts, patches and all nonuniform field-list lengths are validated
before decomposition; a setup failure identifies its actual phase log and
never points to a solver log that was not created.

### Queue

The UI exposes only `Caso único` and `Ejecución secuencial`. Both call the same
canonical preparation/execution service. A queue contains at most 18 cases and
at most three descending, unique `deltaT` values per mesh. Cases are prepared
lazily. Completed cases are skipped, local case failures are recorded and the
queue continues, while global environment/disk/MPI/lease failures stop it.

The `Reference` package has exactly `2.5e-4`, `1.25e-4`, `6.25e-5` seconds per
mesh. `Frequency` derives `2*dt_spec`, `dt_spec`, `0.5*dt_spec` from Strouhal,
speed, chord and samples/cycle. `Manual` requires exactly three positive,
unique descending values. Packages are selectors, not execution inventories;
older cases remain reviewable even when the selected package changes.

The authorized schema-8 reset was applied on 2026-08-13. It removed 19 exact
legacy URANS definitions (4,447,430,880 bytes), produced no skipped/failed
targets and rebuilt 18 unique schema-9 identities. The authoritative audit is
`reports/schema9_migration/deletion_report.json` inside the isolated study.
Meshes, RANS checkpoints/postprocess and real PIMPLE data retained identical
file counts and byte totals across the operation.

### Quick check and PIMPLE

Quick check is optional and ephemeral under `quick_check`. It writes one final
report and log, cleans its sandbox and never enables or blocks production.
PIMPLE sensitivity receives topology, mesh and `deltaT` explicitly, prepares
a common three-step Euler initialization from the matching RANS checkpoint,
then clones `outer_2`, `outer_3` and `outer_4`. A structured signature proves
that only `nOuterCorrectors` changes during the comparison. It does not depend
on quick check or a prepared canonical URANS case and is excluded from normal
URANS review.

### Runtime and monitor

`runtime/active_execution.json` and `runtime/solver_lease.json` are atomic and
contain the actual solver PID/start token, case, phase, log, offset, physical
time, deltaT and queue position. The live monitor follows these files, changes
case and phase automatically, reads scalar/log data incrementally, and never
loads volumetric fields.

## 7. RANS scientific products

`ramair_scientific_plot_style.py` owns the common STIX/serif, accessible-color
style. Scientific figures use English labels and explicit units and export:

- 300-dpi PNG;
- SVG;
- represented data as CSV when nonempty and JSON;
- a figure manifest with source, transformations, filters, grouping, sorting,
  missing-value policy, versions and files.

RANS spatial comparison treats closed/open separately and uses real compatible
accepted checkpoints. It reports coefficients versus cells and effective grid
size, signed coarse/medium and fine/medium differences, absolute differences,
cost/change and GCI only when mathematically applicable. A near-zero medium
reference yields `NOT_DEFINED_NEAR_ZERO_REFERENCE`, never an epsilon-fabricated
percentage.

Maximum final-iteration `y+` is reported by refinement with a `y+=1` reference.
Separation and reattachment are detected on connectivity-ordered wall branches.
`x/c` is Cartesian projection; `s/c` is branch arc length. Bubble length is
`(s_reattach-s_separation)/c`. Internal and external branches are not joined.

RANS does not generate or register `separation_time_history.png` or
`reverse_flow_occupancy.png`; regeneration removes those known obsolete
figure families while preserving their scientific CSV data where applicable.
Mesh convergence omits separation/reattachment-position figures, preserves
the coordinates in technical tables and plots only arc-length bubble length.
For Cp, upper/lower and internal/external branches are normalized, finite
values validated, duplicates consolidated with provenance and each branch
sorted independently by `x/c`. Cp uses an inverted axis. Cf uses `s/c`, keeps
raw/filtered signals distinct and marks zero and detected events.

## 8. ParaView RANS

Full RANS postprocess receives the selected checkpoint case explicitly. It
validates `polyMesh`, the latest complete direct or common `processorN` state,
and required `U`, `p`, `nuTilda`. If reconstruction is needed, only latestTime
is reconstructed with bounded execution. Tests simulate this contract and do
not run OpenFOAM.

The resolved artifact set uses the exact final-iteration volume, wall and
farfield VTK files. If they are missing, only `latestTime` is converted.
Screenshots use that same VTK set; the absolute `.foam` case is an explicit
fallback, not a historical state file. Their camera is fitted from the wall
artifact rather than from the farfield bounds. The complete-postprocess
manifest embeds the actual wall-analysis state and never leaves a null
placeholder after a successful run. `paraview_products.json` uses explicit states: `READY`, `NOT_GENERATED`,
`MISSING_CASE`, `MISSING_FIELDS`, `RECONSTRUCTION_REQUIRED`,
`RECONSTRUCTION_FAILED`, `VIEWER_FAILED`. Missing products omit `path`; a null
or textual `None` path is forbidden. The `.foam` marker is absolute and belongs
to the selected case.

## 9. Supported environment

Primary runtime: Ubuntu/WSL, Python 3.10, OpenFOAM Foundation 14, Gmsh 4.x and
ParaView. Plotting remains compatible with Matplotlib 3.8.4, NumPy 1.26.4 and
Pandas 2.2.3. OpenFOAM 13 may be detected for compatibility but Foundation 14
is the technical reference. Windows hosts CATIA V5 and the editable source.

## 10. Verification contract

Before completion run focused tests, full `CFD_2D/tests`, compileall,
`Application Support/Tools/check_project_context.py`, launcher `--check-only`
and UI inspection in desktop/narrow viewports. A green software test or
`checkMesh` does not establish aerodynamic validation. Real CFD validity still
requires mesh/time independence and comparison with reference data.

## 11. Current verified state and limitations

- Bounded real OpenFOAM checks on 2026-08-13 completed A(25 steps)->B(3) and
  C(3 Euler states)->D(2 backward steps) with two MPI ranks; temporary fields
  were removed and the compact report was retained.
- Open-medium completed five diagnostic Euler steps at phase-A
  `deltaT=2.5e-7 s` (`target deltaT=1e-6 s`) with `Co_max` about 0.70. This is
  initialization evidence only, not a production recommendation or proof of
  convergence.
- Open-coarse and open-fine are prepared from their exact checkpoint meshes;
  no solver was launched for either case during the integrity repair.
- Full 3-D CFD, FEM and FSI are not implemented as validated production flows.
