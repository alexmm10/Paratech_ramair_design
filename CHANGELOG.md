# Changelog

## [Unreleased]

### Fast/final postprocessing, deferred animations and repository handoff

- Split Validation and Convergence ParaView work into a fast scalar/final-state
  postprocess and an independent animation action. The fast path reads the latest
  OpenFOAM state directly and no longer exports a redundant full VTK database.
- Added freshness checks for derived volume fields, wall-patch VTK exports and
  wall-normal velocity samples. A repeated real alpha=12 final postprocess fell
  from about 143 s to 66 s while preserving stage-specific RANS/URANS products.
- Animation-only processing now skips coefficient analysis, reconstruction and
  wall diagnostics. It was exercised on the alpha=12 case and generated four
  bounded URANS animations from the retained physical-time states.
- Recovered the useful alpha=8 and alpha=16 validation cases from their archived
  workspaces without discarding partial histories. Alpha=8 remains a RANS
  continuation from iteration 5475; the other recovered physical-time cases are
  available for URANS continuation and postprocessing.
- Fixed unattended queue classification of numeric SIMPLE iteration folders.
  A pending alpha=8 case now resumes RANS from iteration 5475 to its configured
  15000-iteration limit (or stability), then transfers every restart field and
  starts the A-B-C-D-E transient plan instead of mistaking 5475 for physical time.
- Added a GitHub maintenance guide and audited the tracked payload. Source code,
  presets, tests, reference data and manuals are versioned; CFD results, meshes,
  VTK/ParaView data, remote ZIP packages and execution state remain excluded.

### OpenFOAM base audit, physical stage semantics and nodal ParaView fields

- Added a machine-readable audit for closed/open OpenFOAM bases covering the
  validation flow state, R50c domain, SA turbulence, farfield/wall/empty patches,
  open-inlet continuity, fixed-geometry AoA, force normalization, final numerics,
  RANS-to-URANS transfer, `checkMesh` and measured y+.
- Corrected generated low-Re SA conditions from `nuTilda=3nu` to `4nu` and from
  a wall-function `nut` boundary to `fixedValue 0`; regenerated the canonical
  validation cases. Added a visible warning whenever measured `y+max > 1`.
- Prevented incomplete SIMPLE histories from appearing as URANS physical time.
  A staged case is complete only after full production or an explicit statistical
  convergence decision; stopped and interrupted histories remain partial.
- Reworked automatic streamline input through `CleanToGrid` and point-data
  conversion, with volume rather than surface integration and additional near-wall
  seeds. Added velocity, pressure and vorticity near-airfoil contours for RANS and
  URANS. A real offscreen render produced 460 lines and more than 87k points.
- Selected efficient closed/open experimental candidates after several isolated
  trials. They save 19.6/30.1 percent of cells and improve interpolation weight
  and volume ratio, but their minimum determinant is substantially lower; the
  established medium meshes therefore remain the validation defaults.
- Generated a clean schema-2 remote package for sequential 4/8/16 degree RANS to
  URANS execution and verified extraction, startup monitoring and file inventory
  outside the project tree.
- Changed validation postprocessing from an implicit URANS mode to evidence-based
  automatic stage detection. The recovered alpha 8 history is RANS-only; alpha 10
  retains its verified but incomplete physical-time continuation. Stale URANS
  products are hidden when the stage summary has no physical-time evidence.
- Added separate RANS-archive and URANS ParaView open actions. Both paths were
  exercised through the native OpenFOAM reader and emitted READY evidence without
  the previous copy-mode dialog.
- Replaced filled nearfield maps with coloured isolines on a mid-plane slice of
  the one-cell-thick domain. The background is white, the profile has a stronger
  black outline, the window spans about -0.5c to 2c, and near-wall streamline
  seeding was reduced from 180 to 90 points.
- Remote imports now inventory the exact raw logs, force histories, state files,
  reconstructed times and stationary archives used to rebuild app monitors.

### Optional Bump + Split Progression and validation-queue recovery

- Added the optional `Bump + Split Progression` tangential law to the closed and
  open experimental meshers without changing the accepted `4 Bumps` default.
  LE/inlet and TE retain Bump laws; each body is split near `x/c=0.5` into two
  oppositely oriented Gmsh Progression curves. The shared solver uses the exact
  geometric-series length, searches integer splits without silently changing
  total divisions, and reports midpoint mismatch, actual hmax, GR and validity.
- Added equivalent automatic/manual controls to both mesh editors. Switching
  from automatic to manual preserves the last Bumps, integer split and four
  Progression coefficients. Infeasible limits report the estimated division
  count instead of mutating the requested discretization.
- Compared real closed and open meshes at hmax 0.5, 0.25 and 0.1 percent chord.
  Split Progression reduced some interface-size jumps, but its best candidates
  had lower minimum determinant and volume ratio than the accepted four-Bump
  meshes. It therefore remains an opt-in experiment; 0.25 percent chord is the
  useful diagnostic default and 0.1 percent is rejected at current divisions.
- Recovered and postprocessed the interrupted validation queue at 4, 8 and 16
  degrees. The old queue stopped its RANS cases because it carried a hidden
  30-minute steady timeout and explicitly disabled URANS handoff after timeout;
  no URANS phase had run. Partial RANS fields, residuals and force histories are
  retained and usable as incomplete evidence.
- Generated and exercised a schema-2 portable validation package for 4, 8 and
  16 degrees outside the project tree. Collection uses only system Python,
  preserves zero-byte `.foam` markers, verifies checksums, and imports the three
  canonical cases into an isolated application root.

### Standardized RANS/URANS postprocessing and verified execution evidence

- Standardized automatic ParaView products around selective OpenFOAM arrays and
  written times. RANS uses its initial/final states; URANS supports a user-selected
  physical-time interval and a bounded number of frames. Products include Cp,
  velocity with freestream-perpendicular streamlines, aft boundary-layer velocity
  with mesh edges, high-vorticity regions, positive-Q vortex cores, y+, Courant
  hotspots and shared visualization scales when the source arrays exist.
- Removed automatic ParaView `SaveState` and the redundant vector-U contour from
  the batch path after a real 215k-cell case showed both could stall WSL rendering.
  Interactive `.foam` markers remain the authoritative ParaView entry point.
- Added a production-window `fieldAverage` function object for U, p and nuTilda;
  second moments are stored for U and p and survive restart. OpenFOAM 14
  `foamDictionary` successfully parsed the generated dictionary.
- Recorded the complete initial-condition contract in each case manifest: uniform
  freestream U, gauge kinematic p, low-Re SA initialization, no-slip walls and the
  mandatory SIMPLE-to-URANS field handoff.
- Added automatic MPI preflight/tuning with bounded 4/6/8-rank pilots, load-balance
  evidence, rank-profile cache and manual override. The real alpha=12 case selected
  six ranks because its 2.50 s/step performance was within 5% of eight ranks.
- Added complete closed/open mesh-quality distributions and corrected the closed
  topology count to include all 60,031 external triangles. The accepted closed and
  open studies both pass OpenFOAM 14 `checkMesh`.
- Added `GITHUB_BACKUP_WORKFLOW.md` and exclusions for generated mesh studies,
  scratch data, parallel profiles and ParaView states.
- Verification: Python compilation and the complete `CFD_2D/tests` suite pass
  306 tests; bounded RANS/URANS ParaView rendering completes in 6.6/23.0 s.

### Optional Gmsh Extend controls and reproducible mesh-quality experiments

- Added independent, opt-in Gmsh `Extend` sizing for the external domain and
  the open-airfoil cavity. The implementation uses the official Gmsh 4.15
  controls (`DistMax`, `SizeMax`, `Power`) and restricts each field to its own
  surface, so enabling the cavity law cannot refine the farfield. Both controls
  remain off by default and the previous size fields are unchanged when off.
- Added an optional log-smooth `SizesList` transition for the non-physical inlet
  part of the open-airfoil boundary layer. It changes only the local normal
  height; it does not create a wall or alter physical groups.
- Added selectable Delaunay (5)/Frontal-Delaunay (6), `Mesh.Smoothing=0..10`,
  `AnalyseMeshQuality`, numerical minDetJac/minSIGE/minSICN summaries and
  pre-extrusion Laplace2D/Relocate2D experiments. Candidate meshes are generated
  as separate revisions and ranked with an OpenFOAM-dominated Q score; activation
  still requires explicit approval.
- Open meshing now supports three explicit tangential modes: legacy manual,
  automatic four-segment matching and manual four-segment Bump. Manual mode can
  inherit the last automatic coefficients and the report records the exact mode,
  divisions and coefficients sent to Gmsh.
- Added a short optional straight-approach extension to the closed TE Bump
  segment. A zero length preserves the accepted topology. Fixed the closed mesh
  viewer to use the supported Windows Gmsh Python viewer.
- Added a controlled study runner. On the bounded open case, algorithm 6 was
  faster and had substantially better minSIGE than algorithm 5. Smoothing 1 was
  a useful low-cost default; smoothing 5 improved minSIGE further at roughly
  twice the generation cost. Combined Laplace2D/Relocate2D gave the best Q, but
  did not repair the BL-controlled determinant and therefore remains optional.
- Fixed validation residual downsampling to preserve the complete RANS iteration
  span independently for every equation, rather than allowing one densely logged
  field to truncate the others.
- Simplified both experimental BL editors around one authoritative
  `Discretización tangencial de pared` section. Tangential min/max, global wall
  nodes, legacy body Bump, LE-node and separate TE-cap-node widgets were removed
  from the UI while their JSON/backend compatibility remains intact. Disabling
  automatic matching now exposes and applies the four manual segment Bump laws
  directly. The open lip-fan control now sits beside the BL-layer control.
- Generated immutable-reference Extend trials. The smooth closed external
  candidate passes `checkMesh` but has lower advisory Q than
  `closed_validation_quality`. The open external-only candidate also passes;
  every tested internal-Extend candidate crossed the interpolation-weight fail
  threshold, so the accepted open baseline remains unchanged and cavity Extend
  stays opt-in diagnostic evidence.

### Validation monitors, reference errors and continuous Bump matching

- Validation monitors now retain the complete RANS history (up to 120,000
  records), report total SIMPLE iterations or URANS time steps, and no longer
  expose a PID as a scientific metric. SIMPLE can hand off to URANS only after
  the residual/force-stability gate is satisfied or the configured maximum
  iteration count is actually reached; wall-clock timeout alone is not a gate.
- Added the accepted-point `Cl/Cd(alpha)` validation plot and one signed
  relative-difference plot per variable/reference. Relative percentages whose
  reference is numerically close to zero are explicitly omitted and labelled,
  avoiding arbitrary regularization and misleading infinite errors.
- Added a reusable four-segment Gmsh Bump matcher. It solves one common physical
  junction spacing for TE cap, upper wall, LE segment and lower wall without
  changing the requested divisions, then reports interface ratios, local growth
  and maximum tangential size. Closed experimental meshing uses it by default;
  open meshing retains its accepted legacy default and exposes the method as an
  opt-in experiment.
- Closed LE segmentation is now curvature based instead of a fixed point count,
  and both experimental editors expose geometry selection, matching diagnostics,
  quality review, approval and named ParaView problem sets.
- Tested optional `Laplace2D` post-optimization on a 61,810-element closed mesh.
  It left the limiting quality metrics unchanged (`minSICN=0.00446585`,
  `minSJ=0.975582`, `gamma_min=0.395103`), so it remains disabled by default.
- Verification: Python compilation succeeded and the complete test suite passes
  291 tests.

### Validation/Convergence closure and robust open-inlet guide

- Completed the exact-base inlet experiment without overwriting the accepted
  mesh. The geometry audit proved that a literal uncut leading-edge segment is
  incompatible with the current cut-lip locations: it introduces two curvature
  reversals. The mesher now rejects that condition deterministically and falls
  back to a bounded convex C1 closure while recording both diagnostics.
- Compared 180-node/bump-0.9 and 160-node/uniform inlet trials. Both exposed
  low interpolation-weight faces and remain unapproved. The verified
  `beta75_convex_fallback_inlet140_uniform` candidate passes OpenFOAM 14
  `checkMesh` with 200,116 cells, 75 measured layers, 45.9815 degree maximum
  non-orthogonality, 1.01734 maximum skewness, 0.06322 minimum interpolation
  weight and 0.08998 minimum volume ratio.
- Regenerated `reference_uncut_validation_1m` from its canonical preset instead
  of approving a stale `te_half` manifest. The new 215,316-cell mesh is correctly
  identified, passes `checkMesh` and is approved only after verification.
- The independent validation solver file is now the sole closed-topology
  contract. Its RANS gate combines residuals with long-window force statistics,
  its editable A-E table determines all durations, and PIMPLE uses 0.3/0.9/0.7
  intermediate relaxation with unrelaxed final solves.
- Validation monitoring now combines app state, RANS/URANS phase, OpenFOAM
  residuals, aerodynamic coefficients and an expandable raw console. Sequential
  runs offer resumable/skip-complete operation or clean per-angle regeneration
  from the approved mesh and study solver configuration.
- Validation post-processing preserves complete RANS and URANS histories,
  shades only the selected averaging interval and derives RANS means from the
  editable final-sample count. URANS volume fields use physical
  `adjustableRunTime`; convergence studies retain fixed `timeStep` writes.
- Added the OpenFOAM Q criterion alongside vorticity to field inventories,
  post-processing and automatic ParaView wake screenshots.
- Replaced the obsolete convergence-lab frozen-effective-config panel with
  explicit final RANS/SIMPLE and URANS/PIMPLE configuration views. Existing
  queues still retain their internal immutable revision for reproducibility.
- Verification: the complete `CFD_2D/tests` suite passes 281 tests.

- Reorganized the convergence laboratory around mesh-and-angle identities for
  both 8 and 16 degrees. RANS execution now supports individual and ordered
  queues, fixed 20,000-iteration bases, four explicit lifecycle states and
  review/postprocess selection by the exact mesh-angle checkpoint.
- Added the low-cost Cummings packages for closed and open geometries. Each
  selected angle exposes six canonical URANS runs (two time steps per mesh
  level), all initialized from its matching reviewed RANS checkpoint.
- Split spatial RANS reporting by topology and angle. Open-airfoil wall
  statistics use the external branch only; obsolete separation-bubble plots
  are not generated, and the displayed comparison table hides registry-only
  implementation columns.
- The independent LS(1)-0417 validation page now shows the active job, reports
  and opens the actual mesh, and prepares any chosen angle directly from the
  OpenFOAM-case section. The former redundant additional-angle control was
  removed.
- Hardened command and manifest serialization so `Path` objects are converted
  consistently before JSON persistence, eliminating the experimental-mesher
  and validation-case `PosixPath is not JSON serializable` failures.
- Replaced the experimental open-inlet BoundaryLayer guide with one Gmsh
  spline containing the exact compatible base-profile samples plus bounded
  local C1 Hermite connectors to the physical lips. Splitting the guide into
  several Gmsh curves and forcing the literal full uncut leading edge were
  rejected after real trials because both introduce BoundaryLayer corners or
  self-intersection for the current open geometry and layer thickness.
- Generated the isolated `beta75_c1_base_safe_v30` revision without replacing
  any prior user mesh. OpenFOAM 14 `checkMesh` passes with 206,744 cells,
  75 measured layers, max non-orthogonality 51.4598 degrees, max skewness
  1.01734, min determinant 0.00503807, min interpolation weight 0.057461 and
  min volume ratio 0.081286.
- Removed a dead RANS image reference: aerodynamic efficiency is already the
  second panel of the generated `rans_forces.png` product.
- Removed the obsolete `+2500` RANS extension action so the UI enforces the
  fixed 20,000-iteration campaign contract. URANS review now publishes the
  production-window means, RMS, standard deviation, dominant frequency,
  Strouhal number and the configured `W=1/St` wave number in one table.

## [2026-08-26]

- Context snapshot for backend API 26, Validation Lab schema 13 and the
  `beta75_c1_base_safe_v30` experimental open-mesh candidate.

### Bounded open-inlet bridge and experimental mesh v25

- The uncut base guide used to continue the external boundary layer now uses
  bounded local Hermite handles and endpoint-biased sampling. This prevents
  the high-discretization bridge from overshooting or turning inward at the
  upper/lower lips while retaining the base-profile geometry between the two
  local joins.
- Polyline cleaning now rejects non-finite coordinates and removes coincident
  points even when they are not consecutive, using the physical geometry
  tolerance. This prevents duplicate-point and near-zero-edge failures from
  reaching Gmsh.
- Automatic external interface sizing now ignores a stale fixed draft value
  and uses the measured wall/inlet tangential spacing; the report records the
  selection source explicitly. Fixed sizing remains available as an explicit
  alternative.
- Generated the independent `beta75_base_inlet_bounded_v25_unique_points`
  revision without overwriting earlier revisions. It passed Gmsh conversion
  and OpenFOAM 14 `checkMesh`: 206,898 cells, 75 measured BL layers, 101,476
  hexahedra, 105,422 prisms, maximum non-orthogonality 74.884 deg (one severe
  face), maximum skewness 0.6984, minimum determinant 0.00504, minimum face
  interpolation weight 0.0521 and minimum face volume ratio 0.0412.
- The Streamlit application was resynchronized and verified at HTTP 200;
  the targeted regression suite now passes 93 tests.

### Physically closed Gmsh boundary-layer stack

- Replaced the editable experimental `Beta` value with an internally solved
  Gmsh Beta-law coefficient. The user now supplies target `y+`, layer count
  and turbulent-thickness safety factor; `Size`, `Beta`, `NbLayers` and the
  resulting `Thickness` are mutually consistent and measured after meshing.
- The shared wall-distance calculator now uses the CFD-Online/Schlichting
  correlation and applies the OpenFOAM cell-centred finite-volume convention
  exactly once: Gmsh first-cell height is `2*y_wall`.
- Geometric progression mode now derives the smallest integer layer count
  whose stack covers `1.2*delta99` (or the selected safety factor). Only its
  growth ratio remains editable.
- Added direct quad-column auditing of real first height, wall-to-centre
  distance, layer count, total BL thickness and local normal growth. An exact
  requested layer count is confirmed only when every measured wall column
  agrees.
- Added LaTeX-style physical/topological summary tables and VTK point sets for
  low interpolation weight, low volume ratio, low determinant and abrupt
  local growth. The full study remains a manual action after `checkMesh=OK`.
- Retained the user's
  `beta75_continuous_v14_v12_geometry_smooth_farfield_try_refining` revision
  unchanged. Four isolated successor trials established the balanced
  `beta75_calculated_v20_balanced` candidate: 200,001 cells, max
  non-orthogonality 42.737 deg, max skewness 0.5286, min determinant 0.005038,
  min interpolation weight 0.07668 and min volume ratio 0.11073.

### Mesh-quality diagnostics, automatic numerics and open-mesh experiment

- Corrected the physical `y+` contract for OpenFOAM finite volumes: the
  computed wall distance is a cell-centre distance and the geometric first
  cell height is twice that value. Reports retain both quantities and the
  multiplier, avoiding an implicit factor-of-two error in Gmsh.
- Confirmed and documented that every aerodynamic coefficient and moment uses
  the complete rigid-wall patch set and therefore includes both pressure-normal
  and viscous-shear contributions. The fictitious open-airfoil inlet guide is
  never integrated as a wall.
- Added an opt-in full mesh-quality study. It reconstructs cell distributions
  for skewness, non-orthogonality, interpolation weight, volume ratio,
  determinant, smoothness and aspect ratio, splits boundary-layer hexes from
  unstructured prisms, and renders PNG tables. Normal mesh generation no
  longer pays this full-analysis cost.
- Added direct ParaView opening of the exact VTK sets written by `checkMesh`.
  Experimental sets are supported both beside `log.checkMesh` and in the
  conventional problem-set subfolder.
- SIMPLE and PIMPLE now derive non-orthogonal correctors and the Laplacian
  scheme from the selected mesh report: `<50 deg` gives 0/corrected,
  `50-<70 deg` gives 1/corrected, and `>=70 deg` gives 2/limited 0.5. The
  effective automatic values and their source are recorded in every case.
- Experimental revisions without a successful `checkMesh` can no longer be
  marked ready or approved. Unchecked, unavailable-check and quality-review
  states remain explicit.
- Superseded the older
  `beta75_continuous_v14_v12_geometry_smooth_farfield` diagnostic with the
  later user-refined baseline and the isolated Beta-law trials documented
  above; no previous revision was overwritten.
- Rejected the smaller automatic-interface trial `v16`: matching the first
  triangle to 1.75 times the inlet tangential spacing reduced its minimum
  interpolation weight to 0.01982. This establishes that the transition must
  account for the following radial row as well as wall tangential spacing.

### Polar-error metrics and enforced validation solver

- Added the paper-defined normalized RMS polar error `err` and signed peak
  error `err2` for `CL`, `CD` and `CL/CD` against Experimental, CFD Cobalt and
  CFD Kestrel data. CSV/PNG products are regenerated exclusively from the
  points currently published in the independent LS(1)-0417 validation study.
- Added an editable but bounded validation-solver contract: mandatory
  SIMPLE/RANS initialization (15 000 iterations, `1e-6` residual targets,
  `U=0.7`, `nuTilda=0.7`) followed by A-B-C-D-E PIMPLE/URANS with default
  `dt*=0.0025`, `maxCo=50`, at most five outer correctors, `D=10 t*` and the
  sole production/averaging window `E=50 t*`.
- Added an independent open-airfoil Gmsh experiment and Mesh-page UI with
  chord-relative Boundary Layer, external-volume and internal-volume groups,
  revision loading, real mesh previews, OpenFOAM quality reports and explicit
  approval state.
- Replaced the earlier `beta75_interface400` diagnostic baseline with the
  smoother continuous-size revisions described above.
- Experimental-mesh reports now declare the requested 75-layer contract before
  every generation or recheck, allowing the shared quality parser to confirm
  the actual quad/hex boundary-layer cells without claiming an exact layer
  count from `checkMesh` alone.
- The Streamlit shell now registers its own application directory explicitly,
  so the new validation and experimental-mesh pages import consistently from
  the launcher, isolated UI tests and portable installations.
- Runtime synchronization now includes the experimental Docker definition,
  Compose file and ignore rules. Regression tests distinguish immutable
  scientific contracts from user-owned solver, mesh, inlet and CATIA settings.

## [2026-08-25]

### Standalone LS(1)-0417 validation and UI bug fixes

- Split `Validation & Convergence Lab` into independent `Validación` and
  `Convergencia` pages. The LS(1)-0417 polar study now owns solver snapshots,
  accepted points, literature overlays, OpenFOAM case controls, execution and
  postprocess without depending on the active Work Case.
- Migrated the existing LS(1)-0417 validation evidence without copying the
  canonical OpenFOAM fields. The former Work Case is archived under
  `Previous Versions/Retired Work Cases` and no longer appears in the active
  Results library.
- Complete Work Case restoration now recovers its explicitly saved active
  package set even after dependency-revision drift, while preserving
  `REVIEW_REQUIRED` warnings. Isolated stale-package loading remains blocked.
- Removed mesh-refinement clones from the physical profile selector, exposed
  the 1 m open validation profile distinctly, and allowed the case-package
  builder to consume any safe exported geometry identifier.
- Repaired PIMPLE sensitivity analysis: force means and dominant frequency are
  read from the current nested analysis schema, and absent optional metrics no
  longer raise a `None` conversion error.
- Airfoil previews now follow the canonical upper LE-to-TE and lower TE-to-LE
  contour, use an x/c domain starting at -0.1, and attach stabilizer endpoints
  to the actual lower profile surface. A visible `Crear nuevo espacio de
  trabajo` action is available on the Work Case page.

### Work Case, geometry, suspension and mesh UI corrections

- Made Work Case selection candidate-only until the user presses the explicit
  load action. Creation and replacement controls now live on the Work Case
  page, while the sidebar is a read-only summary of the loaded Work Case,
  geometry, mesh and CFD execution context. Incomplete Work Cases can be
  reactivated without fabricating missing packages.
- Kept geometry and CFD-case changes inside the loaded Work Case. A geometry
  switch no longer rewrites the active Work Case identity or silently restores
  a mesh snapshot.
- Added normalized geometry identity metadata to newly saved geometry, CFD
  case and mesh packages. Saved meshes are classified by real profile digest,
  chord, open/closed state, inlet definition and contract version in addition
  to exact revision dependencies. Incompatible results remain blocked, while
  their generator settings can be imported explicitly as a warned seed.
- Simplified the Geometry/CATIA pages: primary preprocessing/CATIA actions are
  at the top, previews are filtered to representative 2-D/3-D products,
  exports are grouped under `CATIA & CFD exports`, and low-level CATIA defaults
  remain available only as advanced controls. CFD physical values used by the
  preprocessor now come from the active CFD Case.
- Simplified suspension editing around one enable switch, `all_loaded` versus
  `explicit_rib_ids`, R/b versus explicit-angle rigging and one visible
  bifurcation constraint. Fabric remains surface geometry and suspension lines
  remain line geometry; explicit fabric offsets and suspension tubes are
  disabled while their material metadata is retained. Added the lateral
  stabilizer preview and dedicated stabilizer/tip-bulge controls.
- Removed the short mesh-parameter optimizer from the normal application,
  filtered crossports from the CFD mesh preview and retained the effective
  Gmsh execution, limit and safeguard controls. Coarse/Medium/Fine packages
  remain separate mesh revisions associated with one geometry.
- Removed XFLR5 from the required user workflow. XFOIL is the reproducible
  aerodynamic preprocessing dependency.

### T17 final integration

- Consolidated Gates A-E as implemented and bounded-test verified. Gate F is
  structurally implemented but remains scientifically pending until real
  nOuter 4 phase-E evidence and future approved campaign evidence exist.
- Recorded the synchronized final contracts: Work Case schema 3, solver
  schema 15, Validation Lab schema 11 and application/backend API 26.
- Kept the final recommendation deliberately conservative: native WSL,
  existing canonical data, no campaign expansion, no Docker/GPU promotion and
  no convergence claim from incomplete evidence.
- Passed a bounded 43-test integration regression spanning Gates A-F plus the
  Git publication and Docker strategy safeguards.

### T16 DEV-DOCKER: experimental-only decision

- Audited the existing OpenFOAM 14 Dockerfile and Compose prototype without
  building an image or launching a solver. The active WSL distribution has no
  usable Docker server integration.
- Expanded the Docker build exclusions so generated CFD fields, validation
  state, archives and visualization products cannot enter the build context.
- Kept native WSL/OpenFOAM 14 as production. Docker promotion now explicitly
  requires approval and an identical-numerics serial/MPI comparison.

### T15 DEV-GIT: gated local publication

- Kept the repository lightweight and local: the artifact audit passes, the
  working branch is `main`, author identity is project-local and no remote or
  push was invented.
- Added an explicit push preview that performs a Git dry-run and binds its
  approval evidence to the current HEAD, branch and origin. A changed commit,
  branch or remote invalidates the preview and blocks publication.
- Exposed preview and publish as separate actions in the Environment page.

### T14 CONV-NOUTER: evidence-based resume selection

- Added legacy-completion evidence for PIMPLE entries using final physical
  time, force history, normal solver `End` and either execution or completed
  analysis metadata. This recognizes the complete n=3 run even though its old
  `execution_status.json` is absent.
- Resume now records an explicit execute/preserve selection and completely
  skips writes, cloning and registry updates inside completed n=2/n=3 run
  folders. Only incomplete n=4 is eligible for execution.
- Corrected legacy stage-resume parsing to accept both `phase` and `stage`
  record keys. Analysis remains gated until n=4 supplies real evidence.

### T13 BUG-URANS-REVIEW / BUG-VALIDATION-PLOTS / startup fragment

- Fixed backward-history validation to use OpenFOAM's exact persisted
  `uniform/time value` instead of rounded directory labels. The historical
  Closed Fine run was reclassified metadata-only from orchestration error to
  recoverable phase E; no solver was launched and all case-field hashes stayed
  identical.
- Extended URANS review discovery to the real
  `postprocess/URANS/forceCoeffs_raw.csv` layout. The preserved data is now
  classified honestly as partial because stage E has insufficient samples,
  rather than being reported missing.
- Reproduced repeated validation-point publication in regression tests. It is
  idempotent and both lift and drag plots remain valid, so production plot code
  was not changed.
- Removed the full-app rerun issued from the two-second job-console fragment.
  Terminal state is read from disk on subsequent fragment ticks, avoiding the
  stale-fragment-ID message during app startup/completion transitions.

### T12 performance: fixed-numerics CPU/MPI/GPU decision

- Added a bounded 1/2/4/8-rank benchmark that works only on temporary case
  copies and verifies identical `fvSchemes` and `fvSolution` hashes for every
  scenario. Canonical cases and numerical controls are never modified.
- Measured one matched OpenFOAM 14 step at 25.46, 12.52, 7.95 and 6.52 solver
  seconds for 1, 2, 4 and 8 ranks respectively on the Ryzen 7 4800H host. The
  bounded fixture therefore recommends 8 ranks, while explicitly not claiming
  production-capacity scaling.
- Published a hardware audit in the Validation Lab: Open MPI 4.1.2 is active,
  no CUDA device/toolchain is visible, and the installed OpenFOAM/Gmsh path is
  CPU based. Native WSL CPU/MPI remains production; GPU and Docker integration
  remain disabled without measured supported benefit.

### CONV-CLOSED / CONV-OPEN: adaptive evidence gates

- Added pairwise acceptance that rejects unequal physical windows, fewer than
  ten cycles, missing settling/continuous signals or incomplete mean, RMS,
  frequency and PSD evidence. Nyquist alone cannot declare convergence.
- Added a persisted three-grid GCI calculation with observed order,
  extrapolated value, convergence type and asymptotic-range check.
- Closed campaigns unlock the optimized or Cummings sequence one case at a
  time at 16 degrees before allowing 8-degree confirmation.
- Open campaigns expose all 3x6 URANS combinations as deferred capacity while
  requiring fixed-geometry Coarse/Medium/Fine RANS diagnostics, progressive
  Medium temporal evidence, a matched-deltaT spatial crossing and only then
  16-degree confirmation.
- Added an Open divergence gate for stagnation, lip separation, reattachment,
  internal pressure, recirculation and wake evidence. It holds expansion
  without changing geometry, inlet placement or an active mesh.

### CONV-LAB / POST-CONVERGENCE: schema 11 campaign structure

- Migrated Validation Lab from schema 10 to schema 11 through an atomic,
  metadata-only update with the original configuration backed up and a
  preservation report. Existing meshes, RANS bases, URANS cases, PIMPLE
  studies and postprocess products remain in their canonical locations.
- Added an extensible campaign engine with exact closed/open temporal ladders,
  immutable review history, explicit dependencies and progressive execution
  policy. Existing cases are indexed instead of copied; the 18-case matrix per
  angle is capacity and is never launched automatically.
- Encoded joint space-time acceptance, physical settling, at least ten cycles,
  Welch PSD/coherence and `W=1/St`. Closed defaults to 16 degrees before 8;
  Open defaults to RANS diagnostics and a fixed-geometry Medium ladder at 8
  degrees before any spatial crossing or 16-degree confirmation.
- Added the campaign planner to the non-technical Validation Lab UI and bumped
  the synchronized application/backend contract to API 26.

### POST-GENERAL: portable ParaView views and auditable final window

- Added deterministic selection of the final continuous `forceCoeffs` segment,
  then applies the configured tail fraction with a minimum sample safeguard.
  The exact gap, interval and sample evidence is persisted separately.
- Exported the complete `deltaT` history as CSV and PNG, split Cl/Cd/Cm into
  independently scaled panels and inventoried forces, probes, residuals and
  Courant sources that must survive volume-field purging.
- Migrated postprocess products to a schema-3 manifest with paths relative to
  the manifest directory. ParaView states replace machine-specific case paths,
  include a relative loader and publish one scale file shared by images and
  animations.
- Extended automatic ParaView products with velocity contours, streamlines,
  vorticity and y+ views when the corresponding real arrays exist. A bounded
  ParaView 5.10/pvbatch smoke rendered two times, contours and vorticity and
  verified that the saved state contains no absolute case path.

### EXEC-RUNTIME / EXEC-MONITOR: transactional lifecycle and shared scalar monitor

- Added the eight-state schema-1 execution lifecycle with atomic transitions,
  PID identity, restart evidence, a bounded journal and idempotency keys. Old
  runtime labels remain readable but new public state uses one vocabulary.
- Unified single-run, sequential queue and frozen phase orchestration around
  recoverable checkpoints. A stale process with a valid time becomes
  `PAUSED_RECOVERABLE`; without one it becomes `FAILED`, and completed queue
  entries are skipped idempotently.
- Consolidated the general and Validation Lab parsers into one bounded monitor
  core for residuals, linear iterations, `deltaT`, Courant, continuity and
  execution time. Forces, probes, logs and solverInfo are inventoried outside
  volume-field retention.
- Bumped the application backend to API 25 and verified one real OpenFOAM 14
  step both serially and with two MPI ranks in disposable tutorial copies.

### UI-OPENFOAM / OF-SCIENCE: solver schema 15

- Rebuilt the OpenFOAM editor as General, Solver Settings, Writing &
  Postprocess, and Traceability, with Closed/Open controls side by side and
  active/all/subset angle preparation.
- Made the CFD Case the only source for Reynolds, Mach, fluid properties and
  chord. The writer rejects a conflicting legacy Reynolds override and records
  an `applied_solver_configuration.json` audit in every prepared case.
- Set general Closed URANS to at most 10 PIMPLE outer correctors with
  `U+nuTilda` residual exit; Open retains at most 15 with `U+p`. Both steady
  RANS initializers allow 20,000 iterations. Validation Lab remains fixed.
- Made user `maxDeltaT*` a strict adaptive ceiling, adopted emergency
  `maxCo=50/25` for Closed/Open after a bounded OpenFOAM 14 smoke, and set
  volume-field cadence to approximately 2,000 requested physical steps while
  preserving scalar histories continuously.
- Corrected solver saves that wrote schema 13, normalized legacy residual
  controls during schema-15 migration, and documented the verified
  `transportCorrectionFinal=false` every-outer semantics without changing its
  default.

### MESH-SCIENCE: fractional y+, fixed fixtures and audited first-cell height

- Added bounded Gmsh 4.15.2 fixtures for Delaunay 5 versus
  Frontal-Delaunay 6 and for curvature sizing versus explicit transfinite
  counts. Both algorithm meshes passed real `gmshToFoam` and OpenFOAM 14
  `checkMesh`; no CFD solver or active mesh replacement is involved.
- Made the paper ladder explicit as Coarse `1`, Medium `2/3`, Fine `4/9` and
  Extra Fine `8/27`, with 50 layers normally and 75 only for Extra Fine.
  Existing saved meshes and approvals remain immutable.
- Audited y+-derived first-cell height against the project skin-friction
  estimate and the requested laminar/turbulent formulae. New mesh reports
  record every candidate, the conservative selected height and its source.
- Documented why transfinite node counts are computed after curvature-based
  sizing, retained algorithms 5/6 by topology evidence, and recorded the
  inaccessible Gmsh work item as `NOT_CLAIMED_RESOLVED`.

### UI-MESH: compatible catalogue, drafts and revision approval

- Reordered the Mesh page around the active geometry: its profile preview and
  compatible saved meshes appear first, while incompatible revisions remain
  visible with an explicit reason and are never loaded silently.
- New configurations can start from reviewed defaults, Coarse/Medium/Fine
  presets or the exact configuration of a compatible saved mesh. These sources
  are starting points rather than identities; editing writes an active draft
  and does not mutate the immutable saved source revision.
- Renamed the detailed parameter families to General, Closed and Open while
  preserving all active Gmsh, sizing, transfinite and boundary-layer controls.
  Quality review and a collapsed boundary-layer thickness review now precede
  approval.
- Separated the technical active-output eligibility flag from durable Work Case
  approval. Approve/reject/pending decisions now target the selected saved mesh
  revision with actor and evidence and survive later reuse.

### UI-GEOMETRY: Work Case gate and shared geometry DTO

- Added a dedicated Work Case page and blocked the normal geometry-to-results
  workflow until a persistent Work Case is selected. Validation Lab remains
  isolated and available without changing its scientific workspaces.
- Split Geometry into explicit 2-D and 3-D views. The 2-D view now groups inlet
  design, profile selection/import, TE treatment, parameterized crossports and
  an actionable preview; 3-D retains canopy, CATIA, fabric and system controls
  without duplicating profile, TE or crossport inputs.
- Added a persistent profile catalogue with UUID, SHA-256, provenance and
  validation metadata. Imported coordinates preserve their original bytes and
  do not replace protected validation profiles automatically.
- Added a shared geometry DTO used by the UI and crossport preprocessor. TE
  `straight_gap` is displayed as `No modification`; crossports support explicit
  per-hole shape, orientation, size and point count while retaining X-start,
  X-end and edge-clearance generation controls.
- Implemented the selectable chordline/profile-midline centerline behavior in
  both supported preprocessor entry points. Chordline is the default for new
  project configurations; existing saved configurations retain their value.

## [2026-08-20]

### Work Case architecture: manifest schema 3 / active workspace schema 4

- Added stable UUID identities for Work Cases and stage entities, SHA-256
  revision identities, provenance, artifact inventories and explicit upstream
  dependency snapshots while preserving all existing package folders.
- Added persistent approval/rejection evidence per immutable revision. Editing
  an approved package now retains the historical decision and creates a new
  pending revision.
- Added compatibility evaluation and non-destructive invalidation warnings.
  Stale packages remain visible but are not silently restored; coherent active
  packages retain deterministic precedence.
- Added a read-only schema-1/2 adapter plus an explicit metadata-only schema-3
  migrator with atomic writes, per-manifest backups and a classified Results
  index. Protected Work Cases are indexed without copying their CFD data.
- Applied the migration to five WSL Work Cases: three protected scientific
  cases plus two historical executed cases. A second dry run reported zero
  changes, all five original schema-2 manifest hashes matched their backups,
  and aggregate artifact hashes for the protected cases were unchanged.
- Updated application config synchronization and all Work Case builders to
  publish revision-aware metadata. Active workspace records now include exact
  entity/revision identities, approvals and compatibility warnings.

## [2026-08-18]

### Durable execution, adaptive URANS and portable server runs: API 24 / solver schema 14

- Added solver PID, process-group and Linux start-token ownership, clean
  `writeNow` stop requests, bounded signal escalation and startup
  reconciliation. Manual stop and timeout now preserve numerical fields as
  `PAUSED_RESTARTABLE`; stale PIMPLE state is reconciled without deleting
  results.
- Added clean and forced stop controls to the active-execution panels and kept
  the controls available while a stop request is pending.
- Separated the physical `maxDeltaT_star` ceiling from the emergency Courant
  guard. General closed/open URANS uses `maxCo=50/20`, up to 15 PIMPLE outer
  correctors and early exit at absolute `U`/`nuTilda` residual `1e-4`.
  Validation Lab retains fixed timesteps and corrector counts.
- Audited open-airfoil boundaries: internal and external fabric surfaces are
  already explicit wall patches, the opening remains fluid continuity and no
  duplicate `createBaffles` conversion is applied.
- Added near-airfoil streamline overlays to automatic ParaView pressure,
  velocity and animation products.
- Added restartable Linux/WSL server packages with frozen cases, sequential
  queue, checksums, run/resume/stop/monitor/postprocess launchers and optional
  offline Python wheels.
- Added Git artifact auditing and application snapshot/update/publish actions,
  repository exclusions for CAE outputs and third-party publications, and a
  headless OpenFOAM 14 Docker definition.
- Removed obsolete generated archives and empty malformed legacy path
  directories after verifying they were not referenced by the application.

### URANS phase stabilization: API 23 / Validation Lab schema 10

- Replaced duplicate text matching with one structured OpenFOAM event
  classifier. The normal `FOAM_SIGFPE` banner is informational; real floating
  point exceptions require fatal trace evidence. Successful return, `End`,
  target time and checkpoint evidence take precedence over banner text.
- Made A-E transitions transactional with decimal end times, immutable
  decomposition/solver/reconstruction logs, phase-local monitor offsets and
  explicit input/output checkpoint evidence. A->B continues from `latestTime`;
  C->D requires, reconstructs and re-decomposes the current state plus two old
  target-step states before `backward` starts.
- Made phase-boundary writing account for OpenFOAM's persisted global
  `timeIndex`: the selected interval divides the target global index, so a
  restarted short phase cannot finish one step before its required checkpoint.
- Made every staged runner command validate its own planned start boundary;
  later C/D/E commands no longer inherit B's restart time. The historical
  closed-coarse false-positive is reclassified through a metadata-only audit
  as paused/restartable at B, without launching a solver or changing fields.
- Made the real RANS checkpoint mesh canonical for URANS initialization.
  Preparation validates mesh fingerprints, cells, faces, patches and
  nonuniform field lengths before decomposition and reports the actual failed
  setup log when no solver was started.
- Reconciled legacy registry/package mesh hashes against content. Resume is
  allowed only when the actual prepared URANS mesh and actual RANS checkpoint
  mesh are identical; the content hash replaces the stale value with a
  separate audit report.
- Added `Reference`, `Frequency` and `Manual` three-value temporal packages,
  separate target/phase `deltaT` monitor fields, normalized URANS review labels
  and a safe restart dropdown populated only by real executions.
- Rebuilt PIMPLE 2/3/4 preparation around an explicitly selected topology,
  mesh and `deltaT`, an internal common Euler initialization and sequential
  independent outer-2/3/4 cases. It no longer depends on an externally
  prepared URANS case.
- Resolved RANS ParaView products as one exact final-iteration VTK set and used
  it for both viewer launch and velocity/pressure screenshots. Missing sets
  generate only `latestTime`; missing paths use explicit states, never null.
  Screenshot cameras are fitted from the wall artifact so the farfield does
  not collapse the airfoil view, and complete-run manifests expose the actual
  wall-analysis result instead of a misleading null value.
- Extended the common scientific exporter to external refinement analyses,
  removed position plots and obsolete reverse-flow figure families on
  regeneration, and retained signed bar differences relative to medium with a
  `Cell count, N [x10^5]` axis.
- Added CFD publication-point removal without deleting the simulation or its
  postprocess; ineligible partial points expose their exact reason.
- Kept Validation Lab navigation total when Streamlit transiently returns no
  selected subsection, preventing a `KeyError` while switching URANS views.
- Verified bounded disposable two-rank OpenFOAM transitions A(25)->B(3) and
  C(3 Euler states)->D(2 backward steps). A five-step open-medium diagnostic at
  target `deltaT=1e-6 s` kept phase-A `Co_max` near 0.70. No production campaign
  or CATIA process was launched.
- Final verification completed with 193 automated tests, Python compilation,
  project-context/API checks, WSL dependency checks and desktop/narrow
  Streamlit inspection.

### Breaking change: Validation Lab schema 9

- Raised the Streamlit/backend contract to API 22 and the isolated Validation
  Lab to schema 9. URANS now has exactly one mutable canonical case for each
  topology, mesh, angle and `deltaT` key.
- Removed the active pilot, production-attempt, bypass, archive and version
  model. The UI now exposes only single-case and sequential execution; quick
  check is an optional ephemeral sandbox and PIMPLE 2/3/4 depends only on a
  compatible reviewed RANS checkpoint.
- Added deterministic collision-resistant case IDs, separate presence/outcome
  states, idempotent preparation, exact resume, confirmed exact-case restart,
  atomic active-runtime records and a single solver lease.
- Rebuilt the sequential queue around the single-case service with a maximum
  of 18 cases and exactly three descending `deltaT` values per mesh.
- Added the two-phase schema-8 to schema-9 reset with fingerprinted preview,
  strict delete allowlist, live-process rejection and preservation of meshes,
  geometry, RANS checkpoints, Results and real PIMPLE data.
- Applied the authorized reset to the real isolated laboratory: 19 legacy
  URANS definitions were deleted (4,447,430,880 bytes), with zero skipped or
  failed targets. The rebuilt matrix contains 18 unique canonical cases,
  three `deltaT` values for each of the six meshes; protected RANS, mesh and
  PIMPLE file counts and byte totals were identical before and after.
- Removed the last persistent matrix-selection/preparation path and residual
  PIMPLE pilot gate. Canonical preparation now always names one explicit case
  ID; sequential selection belongs only to queue state.
- Removed the dormant duplicate RANS analysis surface. URANS review now reads
  only schema-9 canonical case manifests, while PIMPLE 2/3/4 retains its own
  pilot-free controls. Legacy PIMPLE registry metadata is normalized to
  `READY` without pilot, attempt or bypass fields.

### Scientific postprocess

- Added one shared STIX/serif accessible Matplotlib style and reusable export
  of 300-dpi PNG, SVG, plotted CSV/JSON and provenance manifests.
- Corrected RANS mesh differences to signed coarse/medium and fine/medium
  definitions with an explicit near-zero-reference state; added maximum
  final-iteration y+, separation, reattachment and arc-length bubble products.
- Corrected Cp/Cf branch handling: Cp is deduplicated and ordered separately
  by `x/c`; Cf and separation use connectivity-derived `s/c`. RANS no longer
  generates physical-time separation histories.
- Hardened RANS ParaView readiness around the explicitly selected checkpoint,
  complete direct/common processor state, required fields and non-null product
  paths.
- Migrated active RANS force, residual, moving-statistics and execution-cost
  diagnostics to the common 300-dpi PNG/SVG/data/manifest exporter and
  replaced the force/efficiency twin axis with aligned panels.

### Fixed

- Separated URANS start intent into `FRESH_FROM_CHECKPOINT`,
  `CONTINUE_STAGE` and `RESUME_EXISTING`. A valid stage A checkpoint can now
  start B from `latestTime` without incorrectly invoking an external resume.
- Preserved stage-local log evidence as immutable byte segments, avoiding a
  blocked stage B inheriting stage A solver output.
- Added durable preparation, checkpoint, temporal-history and solver failure
  states to the Validation Lab registry. Secondary state-sync failures are now
  retained separately from the primary orchestration exception.
- Added checkpoint/start/end/solver provenance to each staged record and
  retained every C-stage write required before the designated backward stage.

### Changed

- Documented the canonical start-mode, checkpoint and retained-time contract
  with OpenFOAM Foundation 14 as the technical reference.

All notable project changes are recorded here. Dates use `YYYY-MM-DD`.

## [2026-08-13]

- Published `PROJECT_CONTEXT_FOR_CODEX.md` for backend/UI API 22 and Validation
  Lab schema 9 after the canonical URANS migration and scientific postprocess
  restructuring documented in the Unreleased section above.

## [2026-08-12]

- Corrected Validation Lab URANS stage semantics: FRESH attempts now use an
  explicit internal stage continuation after a forced and validated restart
  write, while external `--resume` is reserved for a partial prior attempt.
  Missing stage output is persisted as an orchestration checkpoint failure,
  never disguised as solver divergence.
- Added versioned attempt-state distinctions, stable scientific `case_key`,
  terminal reason codes, idempotent terminal finalization, stale-attempt
  reconciliation and stage-level diagnostics. A secondary registry failure is
  recorded without leaving the primary attempt falsely RUNNING.
- Added matching-attempt preview, safe archival and explicit selected deletion
  with active/restartable safeguards. These operations affect only attempt
  directories and preserve shared meshes, definitions and RANS checkpoints.
- Made the Validation Lab monitor resolve its source from persistent registry
  state on every refresh, so Follow active takes precedence over a historical
  pin and queue/pilot/production changes do not retain stale log readers.
- Archived the governing URANS attempt-retention and live-monitor contract in
  `Documents and Manuals/CFD 2D/` (SHA-256
  `299018E3E63D08903931C3EE699F765191C4411E0E0086768A8FDB68C2DBF94A`).
- Made pilot fallback reproduce the canonical staged sequence whenever that
  sequence exists, rather than silently testing a single backward segment.
  Production manifests now identify the actual final stage that wrote the
  checkpoint, including reduced test plans.
- Protected restartable partial attempts from bulk archival until an explicit
  user action, and hardened archive-path validation against unsafe attempt
  paths. Added the audit report
  `CFD_2D/reports/VALIDATION_LAB_URANS_ATTEMPT_RETENTION_AUDIT_20260812.md`.
- Verified the full Python regression suite: 267 passed. The official launcher
  synchronized API 20 successfully and the Validation Lab UI was inspected at
  desktop and narrow widths without browser-console errors. No CATIA, Gmsh,
  OpenFOAM solver or real retention action was executed for this release.
- Expanded per-stage URANS diagnostics with parsed residual summaries and
  available field extrema (including `nuTilda`), plus visible non-automatic
  numerical/orchestration recommendations. This does not alter solver physics
  or apply stability changes implicitly.

## [2026-08-11]

- Strengthened Validation Lab URANS attempt intent handling with a single,
  reason-coded FRESH/RESUME preflight record. Fresh attempts retain Stage A,
  checkpoint-copy permission and no `--resume`; resume now requires a partial
  attempt with real positive-time evidence and records reconstruction need.
- Added normalized production-attempt selector metadata: stable identity,
  physical-time evidence, solved-step count, final stage/scheme/deltaT/outer
  correctors and an explicit eligibility reason. Prepared definitions, pilots,
  dry-runs and pre-solver placeholders remain excluded.
- Added a structured ParaView readiness result that rejects missing or invalid
  case paths without converting null values to a fallback or the literal
  string `None`.
- Expanded RANS spatial-convergence products with adjacent-grid deltas,
  near-zero-safe moment percentages, trends, fine-grid differences,
  accuracy-cost plots and an explicit GCI/observed-order product when the
  evidence is mathematically defensible. Missing surface data remain missing;
  no synthetic curves are generated.
- Archived the governing URANS solidity/monitor/stage-transition contract in
  `Documents and Manuals/CFD 2D/` (SHA-256
  `3F1C0B0612727286308A4B1B6FDCC513D2FC3828E20DB898136B354C99F635DF`).

## [2026-08-04]

- Made the canonical six Validation Lab meshes and RANS rows persistent in all
  views, including completed `closed_coarse`, and added the traceable
  `accept-six-current` operation. It preserves every automatic gate while
  materializing immutable reviewed restart snapshots with hashes for `U`, `p`,
  `nuTilda`, optional `phi` and `nut`.
- Added complete branch-aware wall analysis for RANS and URANS: Cp, y+, raw and
  filtered tangential wall shear, Cf, separation/reattachment events and URANS
  separation histories. Ordering follows patch face-edge connectivity and arc
  length rather than x alone; manifests record thresholds, filtering,
  persistence, branch mapping, confidence and limitations.
- Separated URANS execution intent into explicit `FRESH` and `RESUME`. Resume
  requires positive physical time, reconstructs processor-only time when
  necessary and reports `RESUME_NOT_AVAILABLE` structurally. Bypass is
  independent, and explicit attempt IDs preserve prior evidence.
- Relaxed bounded pilots to `PILOT_WARN` where appropriate, allowed production
  bypass with confirmation and an optional note, and made matrix queues run in
  descending `deltaT`, continue after local failures and stop on global
  environment, disk or MPI failures.
- Migrated the 14 remaining legacy URANS rows metadata-only to canonical mesh
  identities. Two of the originally expected 16 rows were already canonical;
  no heavy data were moved or deleted.
- Verified a bounded real `closed_medium` FRESH/stop/RESUME/stop smoke, a
  70-step `PILOT_WARN`, a three-case dry queue and RANS wall postprocessing at
  iteration 20000. Added dated audit and smoke reports under `CFD_2D/reports/`.
- Fixed Windows-safe PID probing, RANS checkpoint provenance fallback and a
  false OpenFOAM Foundation 14 `pressureCoefficient` postprocess invocation;
  Cp continues to be exported from the real case field.

## [2026-08-03]

- Upgraded the application/backend contract to API 20 and the isolated
  Validation Lab to schema 8. The global monitor follows concrete RANS,
  pilot, production and PIMPLE execution identities, registers `PREPARING`
  before process spawn and preserves structured pre-solver failures instead of
  selecting a generic case definition or showing an empty monitor.
- Made the URANS pilot recommended rather than mandatory. Automatic pilot
  evidence and explicit user review remain separate; production without an
  approved pilot requires a visible confirmation and reason and is stored as a
  bypass, never as a false PASS. Sequential matrix queues persist policy,
  pilot/production attempt identities and resume partial attempts without
  duplication.
- Restored manual RANS extensions of 2,500 iterations beyond the autonomous
  20,000-iteration cap, separated hard numerical divergence from review/gate
  outcomes, aggregated residual and timing histories over all execution
  segments with overlap removal, and materialized an immutable restart when an
  eligible review is accepted.
- Corrected the complete postprocess reconstruction contract: MPI
  reconstruction accepts keyword-only reconstruction arguments and field
  scaling remains exclusively in rendering. Simplified RANS review to one
  selector and two principal panels, made URANS product loading lazy, and
  removed the `constrained_layout`/`twinx` warning from compact monitor plots.
- Added contract coverage for schema migration, optional pilot/bypass,
  attempt-specific monitor switching and pinning, measured solver-step timing,
  matrix resume, RANS state/timing rules, postprocess signatures and warning-
  free closed Matplotlib figures. Archived the governing Validation Lab
  monitor/pilot/RANS review specification with its verified SHA-256 digest
  under `Documents and Manuals/CFD 2D/`. No historic simulation, checkpoint or
  `Results` data is rewritten by the migration.
- Completed a real bounded `closed_coarse` URANS orchestration smoke. A new
  70-step pilot completed all four stages and retained automatic `PILOT_WARN`
  because `Co_max=41.79`; its separate explicit acceptance is labelled for
  software smoke only. A production attempt stopped cleanly, resumed twice,
  retained four physical time directories and completed partial postprocess
  with real Courant, y+, wall-shear, vorticity and VTK products. The evidence
  report is `CFD_2D/reports/VALIDATION_LAB_URANS_END_TO_END_SMOKE_20260803.md`.
- Fixed issues exposed by the real smoke: monitor-only cache files no longer
  block first case preparation; pilot stages force restart writes; the normal
  OpenFOAM SIGFPE-trapping banner is not treated as a numerical FPE; clean user
  stops remain `STOPPED_PARTIAL`; partial stages resume instead of being
  skipped; resumed logs use immutable `segment_NNN` archives; timing counts
  demonstrated steps rather than planned steps; and dry-run pilot bypass is
  persisted without starting a solver.
- Made execution-registry upserts cumulative so terminal updates cannot erase
  an attempt's established start time, logs, force/residual histories or
  monitor paths. Corrected the steady-stage postprocess scale-argument
  propagation found by the partial postprocess. The synchronized OpenFOAM 14
  environment, Python compilation, visual desktop/narrow review and all 242
  `CFD_2D/tests` now pass; only external Matplotlib/pyparsing deprecation
  warnings and the optional XFLR5 environment warning remain.

- Recovered the interrupted Validation Lab orchestration as backend API 19
  and laboratory schema 7. RANS now disables OpenFOAM SIMPLE
  `residualControl`, owns convergence in the Python gate, and cannot evaluate
  or stop for convergence before absolute SIMPLE iteration 10,000. A clean
  solver exit below the active target is recorded as
  `PREMATURE_NORMAL_EXIT`; an explicit metadata-only recovery audit corrects
  historical misclassification without changing fields or time directories.
- Separated URANS case definitions from immutable pilot and production
  attempts. Identities now store `case_id`, `run_kind`, `attempt_id` and
  attempt-specific `run_id` under `pilot/pilot_attempt_NNN` and
  `production/production_attempt_NNN`. Preparation is idempotent, existing
  attempts are never overwritten implicitly, and the execution registry is
  updated through `PREPARING`, `RUNNING` and terminal states.
- Reordered URANS controls around a single-case workflow before the optional
  matrix, added attempt review/archive/resume actions and timing evidence, and
  added a lazy manifest-only postprocess product browser. Active Streamlit
  sources now use the current `width` API instead of
  `use_container_width`.
- Reorganized the isolated Validation & Convergence Lab into six stable
  top-level sections: meshes/conditions, solver strategy, RANS, URANS,
  space-time convergence and reports/workspace. RANS and URANS now expose
  their own execution, review, postprocess and convergence subsections. A
  single collapsed global monitor follows the active execution at a selectable
  15/30/60 s refresh; review panels remain static.
- Migrated only the isolated laboratory registry from schema 5 to schema 7.
  The general solver schema remains 13 and backend API is 19. URANS
  startup and pilot stages now preserve editable `enabled`, scheme,
  `dt_factor`, duration mode, duration and purpose fields. Defaults are
  25/25/50 startup steps and a 10/10/20/30, 70-step pilot.
- Added frozen per-run `resolved_config.json`, generated-dictionary
  `applied_configuration_audit.json` and `run_manifest.json`. URANS pilots no
  longer report false PASS from a zero exit code alone: real log evidence for
  boundedness, continuity and Courant is recorded as PASS/WARN/FAIL/PARTIAL.
- Added a resumable sequential URANS matrix manager, including the safe
  per-mesh/largest-deltaT pilot policy, timeout continuation and explicit
  revisit of partial runs. Added stage-E-only oscillatory review with
  four-block stationarity, Welch PSD, Strouhal number and cycle sufficiency.
- Added unified real-product-only postprocess manifests, exact/robust/manual
  field-scale calculation, accepted-only space-time comparison and guarded
  GCI. RANS postprocessing no longer generates or displays Courant products;
  historical files remain preserved and are labelled not applicable.
- Added evidence-preserving migration for the real 20,000-iteration
  `closed_coarse` history. It keeps the automatic `NOT_CONVERGED` gate while
  recording the user's explicit statistical-steady acceptance, verified mesh
  and field hashes, and allowed RANS-spatial/URANS-initialization uses without
  relaunching the solver.
- Added a real-evidence slowdown auditor for the archived `closed_medium`
  run and the complete logical laboratory layout (`registry`, `configs`,
  `meshes`, `rans`, `urans`, `pimple_outer_studies`, `postprocess`,
  `convergence`, reports/logs/locks/cache/exports) without moving heavy mesh
  or solver data. The real archive proves a legacy one-iteration relaunch
  loop: each immediate SIMPLE exit wrote a full state, producing 438 time
  directories and the observed I/O slowdown. No numerics were changed.
- Prepared the isolated closed-coarse PIMPLE sensitivity clones for
  `nOuterCorrectors=2/3/4` from the same real 20,000-iteration checkpoint,
  common mesh, `deltaT`, duration and field hashes. Execution remains
  honestly blocked as `BLOCKED_MISSING_PILOT_PASS`; no pilot or solver was
  launched.
- Corrected final-state RANS ParaView provenance. The real `closed_coarse`
  state at SIMPLE iteration 20,000 now renders with exact finite Cp/U limits,
  saves the screenshot and reusable `.pvsm`, and writes an authoritative
  `postprocess_manifest.json` visible to the postprocess registry and UI.
- Archived the controlling implementation contract
  `CODEX_VALIDATION_LAB_COMPLETE_RANS_URANS_SPACE_TIME_RESTRUCTURE.md`
  under `Documents and Manuals/CFD 2D` with SHA-256
  `B3B7768528C8A87006E3EC51FF79BC01EDBA24A306D0E948279F21E0AB737056`.
- Extended the application/backend API 18 contract and upgraded the isolated
  Validation Lab registry to schema 5. The laboratory now uses one persistent
  horizontal 11-section navigation bar; the former internal summary and
  nested RANS/URANS/PIMPLE navigation were removed.
- Rebuilt the RANS batch contract around absolute SIMPLE targets
  `10000/12500/15000/17500/20000`. `closed_coarse` is preserved and the
  autonomous queue owns only `closed_medium`, `closed_fine`, `open_coarse`,
  `open_medium` and `open_fine`. A gate can no longer run at a partial
  iteration such as 7840, and the RANS queue never transfers a case to URANS.
- Added atomic single-flight leases for queue and per-mesh execution,
  write-on-change `run_case.sh` manifests, authoritative iteration accounting
  across logs/restart directories/metadata, and separate solver-active,
  setup, postprocess and orchestration timing records.
- Added full OpenFOAM residual parsing (`initial`, `final`, linear iterations
  and velocity components), compact two-column static monitors, final-state
  RANS ParaView products and explicit full RANS postprocessing. These actions
  are on demand and do not generate all-time VTK databases or animations.
- Added guarded archive/restart support for the invalidated `closed_medium`
  orchestration run. Only its active RANS checkpoint is archived/deleted;
  meshes, geometry, CFD inputs, protected Results and histories are retained.
- Normalized every unstarted queue snapshot to the frozen
  `10000 + 2500 -> 20000` contract, removed stale resume pointers from newly
  prepared batches and made a dry preflight leave `closed_medium` as the
  selected active base instead of the last case inspected.
- Archived
  `CODEX_VALIDATION_LAB_RANS_BATCH_RESTART_MONITORS_PARAVIEW_POSTPROCESS.md`
  under `Documents and Manuals/CFD 2D` (SHA-256
  `B24F386E3FF0F073B49252363EDF0D862DE311813A872EE1C0635ED79DE6AF2E`).
- Upgraded the application/backend contract to API 18 and the Validation Lab
  registry to schema 4. The laboratory now has ten decision-ordered sections;
  RANS/SIMPLE, URANS/PIMPLE and PIMPLE sensitivity are separate, while the
  historical open-mesh candidate and redesign controls are no longer exposed.
- Added a guarded RANS plateau gate with strict, plateau-warning,
  review-required and diverged states. It evaluates all real `p`, `U` and
  `nuTilda` residuals, hard failures, force stationarity and continuity, and
  stores 2,500-iteration block evidence including residual slope, force
  mean/standard deviation/drift and continuity.
- Made six-base RANS generation resumable by default. Existing solutions are
  never overwritten implicitly, completed/accepted bases are protected, the
  queue starts at the first incomplete base, and a clean stop preserves fields,
  scalar histories, parent run identity and frozen configuration for resume.
  Explicit delete/restart requires confirmation, archives by default and never
  touches `Results` or mesh packages.
- Replaced interactive Validation Lab live charts with two compact static
  Matplotlib panels that cannot capture pan/zoom: logarithmic residuals and
  aerodynamic coefficients with safe `Cl/Cd`. Continuity and Courant remain
  stored and evaluated but are omitted from the primary monitor.
- Manual RANS review now requires visual confirmation but accepts an optional
  note. Automatic gate, review state and allowed RANS/URANS uses are stored
  independently and revocation leaves the automatic evidence unchanged.
- Fixed the WSL bootstrap API-18 compatibility check and two visual-validation
  defects: duplicate monitor widget keys when active and selected runs match,
  and legacy execution-registry rows without a current `mesh_id`. Such rows
  remain preserved and are now reported as incompatible instead of crashing
  the RANS/URANS analysis page.
- Archived
  `CODEX_VALIDATION_LAB_SIMPLIFICATION_RANS_GATE_RESUME_MONITORS_WORKFLOW.md`
  under `Documents and Manuals/CFD 2D` (SHA-256
  `F1461E15C74337FA37EC3BF6D5D7C8328DF987EAF03383A9FE17865ED2C996C4`).

## [2026-08-01]

- Context snapshot for backend API 19 and Validation Lab schema 7. It records
  the recovered 10,000-iteration RANS gate, metadata-only repair of five
  historical native-residual exits, isolated URANS pilot/production attempts,
  attempt-aware monitoring and manifest-only lazy postprocess browsing.
- Verified 221 CFD 2D tests, the synchronized WSL environment and a real
  headless rendering of the URANS execution page. No CATIA, Gmsh mesh,
  OpenFOAM solver or long CFD campaign was executed during recovery.

## [2026-07-30]

- Context snapshot for backend API 18 and isolated Validation Lab schema 6.
  It records the six-section laboratory, editable URANS startup/pilot stages,
  resumable RANS/URANS queues, explicit review provenance, guarded
  space-time convergence and the evidence-preserving `closed_coarse`
  restoration.
- Removed inherited hardcoded ParaView limits for Cp and velocity. Static
  products now default to exact finite field limits; robust percentile and
  manual modes are available, and animations keep one audited global scale
  across all selected frames.
- Verified the complete non-OpenFOAM test suite, application environment,
  Results hashes and the archived `closed_medium` slowdown evidence without
  regenerating meshes or launching a solver.
- Verified the final-state `closed_coarse` ParaView path headlessly with
  `pvbatch`; this read-only postprocess check did not reconstruct fields,
  regenerate a mesh or run OpenFOAM.

## [2026-07-29]

- Upgraded the application/backend contract to API 17 and the isolated
  Validation Lab registry to schema 3. Added the atomic unified execution
  registry for RANS/SIMPLE, URANS/PIMPLE and PIMPLE-sensitivity runs, including
  active-run following, manual pinning and queue-aware titles.
- Added metadata-only migration and traceable manual review for existing RANS
  bases. The automatic gate is immutable and separate from
  `RANS_USER_ACCEPTED_STATISTICALLY_STEADY`,
  `RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY`, `RANS_REVIEW_REQUIRED` and
  `RANS_REJECTED`. Approval requires current diagnostics and a written reason;
  it can be revoked without changing historical fields, logs or forces.
- Added real-data RANS review products: residual/continuity histories, force
  and L/D histories, moving mean/RMS/drift, final-window comparison, 5/10/20%
  sensitivity, block statistics, gate table and execution-cost report.
- Frozen each RANS queue into `resolved_batch_config.json`, copied immutable
  run snapshots and added dictionary-level preflight audits for SIMPLE and
  PIMPLE. A selected/applied mismatch now blocks execution.
- Redesigned and atomically promoted the definitive open-airfoil convergence
  meshes. `open_coarse` has 223,080 cells and `open_fine` 502,474; both pass
  real `checkMesh` and all strict laboratory thresholds. `open_medium`
  remains bitwise unchanged at 302,692 cells. Candidate histories, quality
  histograms, real front-surface/inlet/TE previews and replaced packages are
  preserved.
- Added the isolated `nOuterCorrectors=2/3/4` workflow using three independent
  clones of one `closed_coarse` checkpoint. Preparation is intentionally
  blocked while the real RANS review/checkpoint and common `PILOT_PASS` are
  absent; no synthetic comparison files are emitted.
- Archived the implementation contract
  `CODEX_VALIDATION_LAB_RANS_REVIEW_MONITORS_OPEN_MESH_PIMPLE_UPDATES.md`
  under `Documents and Manuals/CFD 2D` (SHA-256
  `F4280862604E3B5AA983EB9621862E4AD39E232B6AC1CCDF65FE5CA5BCCAC3E1`).
- Upgraded the Validation & Convergence Lab registry to schema 2 and the
  application/backend contract to API 16. RANS/SIMPLE and URANS/PIMPLE now
  have independent controls, six mesh-specific resumable checkpoint slots and
  normalized compatibility hashes for mesh, physics and solver settings.
- Added structured missing/stale checkpoint handling through
  `BLOCKED_MISSING_RANS_CHECKPOINT`, a bounded numerical-viability pilot,
  paper/measured/spectral/custom temporal presets and an isolated PIMPLE
  2/3/4 outer-corrector comparison.
- Added incremental scalar-only live monitoring, JSON/CSV storage inventory,
  active-case-only cleanup, direct opening of the registered `.msh`, an
  isolated open-light mesh-candidate sweep and a bounded closed-coarse
  SIMPLE-to-URANS smoke workflow.
- Verified the open-light sweep with real Gmsh 4.15.2 and OpenFOAM 14 runs.
  Factors 1.10, 1.20 and 1.30 produced 294,744, 287,616 and 281,182 cells;
  all three passed `checkMesh`. Factor 1.20 is recorded as
  `CANDIDATE_AVAILABLE_NOT_PROMOTED`, so the baseline was not overwritten.
- Completed the bounded native OpenFOAM smoke with eight MPI ranks: 100 SIMPLE
  iterations, normalized restart-field transfer and 40 URANS steps. The final
  status is `SMOKE_COMPLETED_DIAGNOSTIC_TRANSFER`; source/target hashes match
  for `U`, `p`, `phi`, `nuTilda` and `nut`.
- Added SciPy 1.13.1 to the reproducible UI/runtime dependency contract and
  environment verification. The final regression passes 182 tests in both
  Windows and WSL.
- Audited WSL storage and removed approximately 20.69 GB of verified scratch
  and regenerable historical data while preserving `Results/`, active meshes,
  active cases, source, configuration and compact evidence.
- Added the isolated **Validation & Convergence Lab** at
  `CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8`. It registers
  the six existing closed/open coarse-medium-fine `polyMesh` triplets using
  content hashes and a dedicated state file, without touching the general
  active workspace.
- Added fixed-step A-E case generation (`Euler` 0.25/0.5/1.0 target step,
  then `backward` settling/sampling), common SIMPLE checkpoints per mesh,
  bounded pilot states, deterministic run IDs, paper-reference and
  feasible-halving matrices, and an obligatory temporal/computational budget.
  Real execution is dry-run by default, sequential and limited to eight MPI
  ranks; production requires both `CHECKPOINT_READY` and `PILOT_PASS`.
- Added stage-aware A-E resume without reapplying the common checkpoint,
  propagated the configured 100-500 pilot length and wall-time limit to the
  runner, preserved one solver log per stage, and added real SIMPLE checkpoint
  analysis for the Spatial RANS tab. Missing checkpoint histories remain
  `RANS_ANALYSIS_PENDING`/`STEADY_RANS_NOT_ESTABLISHED`.
- Added convergence, Welch PSD, stationarity, Courant, PIMPLE and generalized
  unequal-ratio GCI helpers. Missing data remain explicit, weak open-mesh
  ratios suppress GCI, and no empty CSV or synthetic validation result is
  emitted.
- Added a 12-tab Streamlit laboratory page and removed the generic convergence
  controls from the normal workflow. Bumped the backend/UI contract to API 15
  and solver configuration to schema 13 with a versioned schema-12 migration.
- Added focused integration tests for six real mesh triplets, state isolation,
  fixed dictionaries, checkpoint gates, statistical analysis and explicit
  empty states. No CATIA or real CFD campaign was executed.
- Added the independent work case
  `Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6`. It contains
  coherent geometry, CFD-case and mesh package triplets for closed/open
  coarse, medium and fine validation grids. A single application selector now
  activates all three stages atomically, preventing mixed open/closed states.
- Added real open coarse/fine Gmsh 4.15.2 meshes and converted them with
  OpenFOAM 14. The open sequence contains 269,864 / 302,692 / 420,728 cells;
  the retained closed sequence contains 203,691 / 333,826 / 618,382 cells.
  All six pass `checkMesh` and the study acceptance thresholds. No solver was
  executed and no aerodynamic result was synthesized.
- Added `ramair_2d_closed_open_convergence_study.py`, open coarse/fine preset
  JSON files, monotonic-series validation and application/library tests. The
  builder refuses non-monotonic cell counts, missing converted `polyMesh` or
  meshes below determinant/interpolation/volume-ratio quality gates.
- Archived the Cummings, Morton and McDaniel time-accuracy paper with stable
  metadata and SHA-256 provenance under `Documents and Manuals/CFD 2D/Research
  Papers`, and added the engineering study
  `CFD_2D/reports/TRANSIENT_TIMESTEP_MESH_SOLVER_STUDY_20260728.md`.
- Added `ramair_2d_timestep_advisor.py` and solver schema 12 temporal-accuracy
  profiles. The advisor keeps physical-frequency sampling, statistical
  duration, configured `deltaT` and measured local mesh/Courant limits
  separate; each newly written OpenFOAM case now receives auditable
  `time_step_assessment.json` and `.md` files.
- Added a Streamlit **Presupuesto temporal** editor for the common closed
  profile and the open-cavity override. It displays Nyquist, the selected
  samples-per-cycle target, minimum averaging duration and sequential
  `deltaT*` study values without silently changing active solver physics.
- Applied the Cummings joint space/time methodology to retained real Courant
  diagnostics. The closed baseline is limited near the lower rounded TE to
  `deltaT*=2.6753e-5`; the historical open diagnostic is also locally
  Courant-limited. Both are over two orders of magnitude below the
  conservative 20-samples-per-cycle `St=20` screening ceiling, so local mesh
  correction precedes production-duration runs or any increase in `maxCo`.
- Diagnosed the user's 298,898-cell open-airfoil mesh with additional
  engineering thresholds (`minWeight=0.10`, `minVolRatio=0.10`) and exact
  OpenFOAM face/cell formulas. All weak faces were localized to `x/c<0.047`
  at the cavity-side inlet transition; the worst face joined cells whose
  volumes differed by a factor of 12.05.
- Completed six bounded Gmsh 4.15.2/OpenFOAM 14 inlet-transition trials.
  `open_zero_thickness_inlet_normal_y1_factor=8` was selected: the active
  302,692-cell mesh passes `checkMesh` with maximum non-orthogonality 41.464
  degrees, maximum skewness 0.6722, minimum determinant 0.06288, minimum
  interpolation weight 0.09152 and minimum volume ratio 0.13434. This improves
  the two transition metrics by 56.3% and 61.9% for 1.27% more cells. The
  user's manual `y1=25 um` is retained and the prior full mesh is archived.
- Fixed the duplicate Streamlit `case-library-mesh` form by giving the mesh
  page a single library panel. Live monitor snapshots now refresh every 30
  seconds, but headless parsing and plotting occur only when a snapshot is
  due; Cl and Cl/Cd axes use round 0.4 and 20-unit increments.
- Replaced the fixed SIMPLE-to-URANS field list with discovery of all restart
  volume fields and relevant optional flux/derived fields. Every copied field
  is verified by a normalized SHA-256 digest, and a new transition audit
  distinguishes exact continuity at `t=0` from the first solved URANS change.
- Replaced the locally warped zero-thickness inlet reconstruction with one
  exact similarity transform of the uncut base-profile arc. The transformed
  curve maps both lips exactly, preserves the complete base-curve shape and
  removes consecutive profile control points closer than `5e-7c` before Gmsh.
- Added `hybrid_boundary_extension` cavity sizing. A `0.002c` normal
  compatibility strip grows from `16*y1` to the measured inlet-edge length;
  Gmsh `Extend` then inherits that real boundary size and grows it progressively
  to the cavity core. `boundary_extension`, `boundary_uniform` and the former
  `staged_explicit` method remain selectable diagnostics.
- Validated the new open-airfoil preset with Gmsh 4.15.2 using 12 threads,
  Frontal-Delaunay (`Mesh.Algorithm=6`), `gmshToFoam` and OpenFOAM 14
  `checkMesh`. The active 337,981-cell circular-50c mesh is `Mesh OK`: maximum
  non-orthogonality 43.526 degrees, maximum skewness 1.209, minimum determinant
  0.03079, minimum interpolation weight 0.06455 and minimum volume ratio
  0.09201.
- Added the mesh package
  `open_ramair_zero_thickness_hybrid_338k_v3` to
  `Results/Open_RamAir_comparison_M0p15_Re1p9e6` without replacing either
  previous mesh package. Mesh generation and saved-package replacement are now
  separate UI actions; replacing an existing package requires an explicit
  confirmation button and archive/delete policy.
- Completed an eight-candidate open-airfoil efficiency matrix with Gmsh
  4.15.2, `gmshToFoam` and OpenFOAM 14 `checkMesh`. The selected
  `interface_factor12_f` mesh has 327,909 cells, 25.0% fewer than the
  reproduced 437,127-cell baseline, while retaining `Mesh OK`, maximum
  non-orthogonality 50.883 degrees, maximum skewness 1.136, minimum
  determinant 0.02410, minimum interpolation weight 0.05409 and minimum
  volume ratio 0.07624. The smaller 305,227-cell candidate was rejected
  because its interpolation-weight margin above 0.05 was only 0.000345.
- Replaced the two-level cavity inlet sizing with a measured three-stage
  transition. The first two internal bands now match the inlet tangential
  spacing before growing smoothly over 0.012c toward the cavity core. The
  open comparison work case now uses mesh package
  `open_ramair_zero_thickness_balanced_328k_v2` and solver package
  `topology_solver_v11`; the previous active mesh was archived.
- Added explicit `adaptive_courant` and expert `fixed` time-step modes to
  solver schema 11, the case writer, diagnostics and Streamlit editor. A real
  bounded open-airfoil comparison showed that fixed `deltaT*=0.004`
  diverges with cell Courant numbers above 3.5e5, while fixed
  `deltaT=4e-8 s` remains stable near `Co=1` but offers no speed advantage.
  Adaptive Courant stepping therefore remains the production default.
- Corrected live force-monitor axes: `Cl/Cd` now owns the right-side label,
  both y axes use seven aligned major divisions and only the primary axis
  draws horizontal grid lines. URANS ParaView products now label frames with
  physical time in seconds rather than iteration count.
- Made validation publication tolerant of missing, zero-byte or headerless
  accepted/ignored CSV files. Each publication now writes per-angle
  percentage errors and a maximum-error summary for Cl, Cd and Cl/Cd, where
  experimental Cl/Cd is derived from the digitized experimental Cl(alpha)
  and Cd(Cl) curves.
- Added the reproducible evidence package
  `CFD_2D/reports/mesh_studies/2026-07-27_open_efficiency_fixed_dt/`,
  including candidate metrics, selected-mesh evidence, inlet-transition
  measurements and bounded adaptive/fixed solver logs.
- Hardened complete work-case restoration so the case manifest is the
  authoritative geometry identity. A stale `workflow_config` embedded in an
  older mesh package can no longer redirect the application to another
  variant or display that variant's `checkMesh` metrics. The selected open
  comparison package was migrated to `open_ramair_validation_1m`. CFD-case
  packages now restore only their mutable workflow configuration, so legacy
  snapshots cannot overwrite current mesh presets, reference help or solver
  defaults owned by other stages.
- Changed the solver configuration editor from a deferred form to immediate
  controls. Selecting fixed time stepping now hides adaptive-only
  `maxCo/maxDeltaT` controls at once; switching back restores them without
  saving an intermediate invalid combination.
- Completed a real six-mesh closed TE/BL matrix with Gmsh 4.15.2,
  `gmshToFoam`, `checkMesh` and five bounded native OpenFOAM 14 runs. The
  Courant hotspot is an extruded triangular prism immediately downstream of
  the lower TE BL front. Raising the matched-thickness stack from 50 to 60/75
  layers added 8/15 percent cells but changed `deltaT` by only -2.0/+2.3
  percent; localizing the curved cap to 25 geometry samples and 18 requested
  nodes improved the measured `deltaT` by 16.8 percent.
- Promoted the 25/18-node closed TE setting while retaining 50 BL layers in
  the active configuration, validation presets and shared mesh defaults.
- Reworked the zero-thickness open-airfoil size field into staged sigmoid
  `Threshold` transitions. Intermediate stages stop at their distance limit;
  the final exterior and cavity fields remain active to produce progressive
  growth to the farfield instead of a fine plateau followed by an abrupt jump.
- Reduced open cavity over-refinement with independent 0.45/0.35 inner
  wall/TE factors, a local two-stage inlet transition and a dedicated internal
  TE field. The final real circular-50c validation variant passes OpenFOAM 14
  `checkMesh` with 536,727 cells, max non-orthogonality 41.91 degrees, max skewness 0.821,
  minimum determinant 0.03955, interpolation weight 0.07205 and volume ratio
  0.10354. It remains engineering grade C and is not claimed as validated.
- Added the reproducible evidence package
  `CFD_2D/reports/mesh_studies/2026-07-25_te_courant_open_transition/` and
  exposed every active inlet-transition distance, size and sigmoid choice in
  the mesh report and application.
- Added and verified the default `zero_thickness_base_profile` open-airfoil
  topology. Its exterior BL follows the uncut base-profile LE curve, only the
  actual cut contour is `airfoil_wall`, and the cavity remains part of the same
  fluid region without a physical inlet patch. The previous
  `finite_thickness_fabric` topology remains selectable.
- Added selective MSH2 stitching for the two nonphysical curved-inlet
  interfaces. It merges only inlet nodes, removes the temporary interface line
  elements and preserves separate coincident inner/outer wall nodes for the
  zero-thickness baffle.
- Matched the cavity-side inlet transition with one contour-wide tangential
  spacing and a normal size `min(tangential spacing, 8*y1)`. A direct
  equilateral transition failed 111 interpolation-weight faces; the measured
  `8*y1` compromise removes those failures. The final 428,047-cell mesh has
  max skewness 1.937, min determinant 0.0284, min interpolation weight 0.0680,
  min volume ratio 0.0941 and passes OpenFOAM 14 `checkMesh`.
- Added `Results/Open_RamAir_comparison_M0p15_Re1p9e6`, containing the scaled
  1 m open geometry, the same Mach 0.15/Re=1.9e6 operating conditions as the
  closed comparison, the checked zero-thickness mesh and the standard solver
  package. This is a controlled closed/open comparison workspace, not an
  experimental validation of the open geometry.
- The zero-thickness mesh editor now hides every finite-fabric/throat/fan
  control, including legacy keys that were leaking through the generic
  compatibility section. Those values remain round-trip safe in JSON but are
  not presented as active controls. The editor exposes only its common
  contour, cavity and farfield parameters and reports the realized spacing
  ratio plus duplicate/near-zero control-point audit.
- Open-airfoil wall postprocessing now separates exterior/interior and
  upper/lower branches in both `y+(x/c)` and `Cp(x/c)`. Animation playback also
  has an explicit stop/hide action.
- Verified a real bounded 8-rank OpenFOAM 14 run on the new open comparison
  mesh: 49 SIMPLE iterations were archived, `U/p/phi/nuTilda/nut` transferred,
  PIMPLE restarted at physical `t=0`, reached `1.37767e-5 s` at `Co=1.0005`
  and stopped cleanly as `TIMEOUT_PARTIAL`.
- A disposable closed alpha=8 smoke run at `maxCo=1.2` remained stable and
  reached `deltaT=9.625e-7 s`, but the production default remains `maxCo=1`
  pending force, Cp and frequency time-step independence.
- Relaxed steady-to-transient field archival to the actual OpenFOAM contract:
  `U`, `p` and `nuTilda` are required; `phi` and `nut` are preserved when
  written, while OpenFOAM reconstructs missing `phi` from `U`. This fixes
  steady cases where `phi` is legitimately `NO_WRITE`.
- Steady convergence now includes `Cl/Cd` together with Cl, Cd and Cm and uses
  500 samples per adjacent force window by default. The live/static Cl panel
  displays `Cl/Cd` on a secondary 0--100 axis.
- Completed a real bounded open-airfoil smoke workflow with OpenFOAM 14:
  Gmsh, `gmshToFoam`, `checkMesh`, 157 SIMPLE iterations, automatic transfer,
  PIMPLE with `Co_max` near 0.70, clean `writeNow` stop, Courant/y+/wall-shear/
  vorticity postprocessing and latest-time VTK export.
- Raised the coherent UI/backend contract to API 13. The application now
  starts in an explicitly temporary workspace, places the work-case selector
  above the workflow tabs, moves the active CFD profile to `Caso CFD`, and
  synchronizes edits only when the selected case matches the restored
  workspace.
- Added optional CATIA V5 detection and an explicit visible
  `CNEXT.exe -macro Generate_RamAir_Canopy_MAIN.CATScript` action after
  preprocessing. CATIA remains absent from required environment checks and is
  never started by automated tests.
- Simplified the solver editor and schema 10: one common closed/external
  configuration, one common SIMPLE initializer and only restrictive
  `open_internal_cavity` overrides. Temporal and convective schemes are
  selectable from validated OpenFOAM strings.
- Corrected the alpha=4 validation failure classification. The old case lacked
  `0/nuTilda`; it was a setup error, not numerical divergence. The runner now
  performs a turbulence-field preflight and reports `RUN_SETUP_FAILED` /
  `STEADY_STAGE_SETUP_FAILED`.
- Rewrote and verified alpha=4 with OpenFOAM 14, native 8-rank MPI, a bounded
  SIMPLE stage, automatic field transfer and a bounded PIMPLE stage. Both
  intentional one-minute timeouts retained reconstructable partial fields;
  Courant, y+, wall shear, vorticity, Cp and velocity-profile postprocessing
  completed.
- Standardized the complete schema-10 `cfd2d_solver_config.json` as the solver
  authority for every new and existing Results work case. Complete workspace
  restore now reapplies the named `topology_solver_v10` package last, and edits
  made in the app are synchronized atomically to the active work case.
- Simplified Results work-case loading to one primary complete-workspace
  action. Geometry, CFD case, mesh and solver packages are restored together;
  isolated stage restore remains available under the advanced control.
- Added `deltaT_history.png` with the configured `maxDeltaT` ceiling and
  automatic ParaView `Courant_hotspots_<stage>_final.png` products that hide
  cells below 70 percent of the actual maximum cell Courant number.
- Completed a bounded OpenFOAM 14 native sensitivity study with 8 MPI ranks.
  The controlling cell was measured on the lower rounded TE cap, not at the
  LE. Halving all wall nodes degraded determinant margin to grade C, while
  reducing only the curved TE cap from 70/45 to 35/25 geometry/mesh nodes kept
  grade B and increased the measured adaptive `deltaT` by about 44 percent.
  The evidence package is
  `CFD_2D/reports/courant_mesh_sensitivity_20260724/`.
- Promoted the localized TE candidate to the active validation work-case mesh
  package without deleting or overwriting the former approved package.
- Raised the complete regression result to 144 passing tests and verified a
  real headless Streamlit start from the synchronized WSL runtime.
- Fixed the standalone CATIA Windows packager when the WSL runtime has no
  `last_preprocessor_run_config.json`. It now falls back to the active editable
  configuration, rewrites every profile reference to `profiles/<filename>`,
  verifies those files inside the ZIP and records the exact configuration only
  after a successful portable preprocessor run.
- Added the URANS cell Courant field `Co` at normal OpenFOAM field-write times.
  Postprocessing can regenerate it with `CourantNo`; automatic ParaView
  products now include a profile close-up `Courant_<stage>_final.png`.
- Promoted solver settings to a named Results-library stage. The application
  now imports/exports the complete solver JSON, restores it with a workspace
  and omits explicitly blank optional OpenFOAM entries instead of replacing
  them silently.
- Standardized both SIMPLE topology profiles at
  `nNonOrthogonalCorrectors=0` and a 10000-iteration ceiling. Early residual,
  force-plateau and timeout transitions remain active.
- Added `ramair_2d_courant_diagnostics.py` and a bounded 6/8-rank,
  native/PyFoam, previous/optimized PIMPLE benchmark matrix. Real closed and
  open logs are Co-limited, not `maxDeltaT`-limited; the optimized 8-rank
  native first step was the fastest measured configuration.
- Made the application start timeout an explicit 300-second option and
  disabled it while the guided installer is running.
- Documented and tested that open-airfoil `forceCoeffs` integrates every
  external/internal/lip/TE wall as one rigid body while excluding the
  nonphysical inlet bridge.
- Added topology-specific OpenFOAM numerics. Closed external airfoils use one
  PIMPLE outer loop and `maxCo=1`; connected open cavities use two outer loops,
  bounded momentum transport, `maxCo=1`, a smaller adaptive `deltaT`
  ceiling and independent field-write retention.
- Verified the 421,131-cell open mesh with OpenFOAM 14 (`Mesh OK`) and
  completed a real six-rank RANS-to-URANS smoke run with clean timeout/stop
  handling. The traceable package is
  `Results/Open_RamAir_RANS_URANS_Smoke_20260723`.
- Automatic ParaView products now produce clearly titled final Cp and velocity
  PNGs for both RANS and URANS. Intermediate animation frames stay hidden from
  the normal image grid; MP4/GIF products load only through the explicit
  **Visualizar animacion** control.
- Results-library saves use same-filesystem hard links for large generated
  artifacts with a portable copy fallback. Mutable configurations and initial
  fields remain independent snapshots.
- Transient force convergence requires three consecutive stable window
  checks. Closed/open defaults use shorter topology-appropriate observation
  windows instead of one universal long-time threshold.
- Promoted OpenFOAM Foundation 14 to the active reference runtime. The
  environment helper now selects the newest user-local installation before the
  compatible system OpenFOAM 13 fallback; the attached v14 User Guide is
  preserved with a verified SHA-256.
- Fixed the root cause of the `alpha=-4 deg` transient coefficient runaway:
  steady-to-transient initialization now preserves the face flux `phi` when
  SIMPLE writes it instead of silently discarding it. The newer portable
  contract above also accepts legitimate `NO_WRITE` fluxes and lets PIMPLE
  reconstruct them from the transferred velocity field.
- Updated the steady Spalart-Allmaras initializer to the balanced v3 numerical
  profile and added final pressure/velocity/turbulence solvers plus PIMPLE
  residual control for the transient stage. Adaptive `deltaT` remains bounded
  by `maxCo` and `maxDeltaT`.
- Added explicit physical-second field write intervals. Postprocessing now
  separates `RANS/` and `URANS/`, and optional `pvbatch` automation produces a
  close-up Cp image plus velocity and Cp animations directly from native
  OpenFOAM times. Pillow supplies a GIF fallback when `ffmpeg` is absent.
- Rebuilt and independently verified the full portable and CATIA-only ZIP
  packages. The CAD package regenerated `CATIA_inputs`, reran the preprocessor
  after extraction and passed its static CATScript contract without executing
  CATIA.
- Removed 13+ GB of obsolete regenerated-output backups and four superseded
  WSL study roots. Active meshes, OpenFOAM cases, results and source/config
  backups were preserved; the cleanup manifest records every removed path.
- Added the OpenFOAM 14 solver/postprocess/portability audit and raised the
  project regression result to 125 passing tests.
- Made alpha-sweep stop requests preserve the active OpenFOAM state and stop
  the chain before launching the next angle; sweep status now reports
  `STOPPED_BY_USER` explicitly.
- Tightened LS(1)-0417 validation publication so a staged case must have a
  completed transient stage; stationary-only postprocessing is no longer
  publishable as validation evidence.
- Fixed temporal ParaView output after a PyFoam timeout or clean stop by
  reconstructing every retained MPI write interval instead of only the latest
  one. `purgeWrite` still bounds storage before processor folders are cleaned.
- Promoted the laptop validation default from the t*=2 smoke interval to a
  t*=20 first-production interval, with subsequent resumes also extending by
  20 t*. The protected existing alpha=8 medium result was not rewritten.

### Added

- Added a protected LS(1)-0417 alpha=8 mesh-refinement study with real coarse
  (203,691 cells), medium (334,857 cells) and fine (618,382 cells) meshes.
  All three pass `checkMesh`; the coarse/fine OpenFOAM cases are prepared
  independently and the existing medium solver data are preserved.
- Added batch postprocessing, incremental angle-sweep status, explicit
  per-angle error/timeout continuation and manual publication of selected,
  eligible validation points.
- Added a rerunnable mesh-refinement analyzer. It reports mesh quality, cell
  count, runtime and real CFD outputs, and withholds coefficient/error/Cp
  comparison plots until enough simulations exist instead of creating
  placeholders.
- Added the traceable `LS1_0417_M0p15_Re1p9e6` validation package with a
  complete CFD/workflow preset, provenance manifest, approximately digitized
  Experimental/Cobalt/Kestrel Figure 10 data and reference-versus-project
  Cl-alpha/CD-Cl plots that accept only matching real simulation results.
- Added the application-facing validation work case
  `Results/LS1_0417_validation_M0p15_Re1p9e6`. It contains named geometry,
  CFD-case and approved-mesh packages plus a case-local `Validation/` folder
  that accumulates only completed or statistically converged polar points.
- Added selectable transient `Euler`, second-order `backward` and
  `CrankNicolson 0.9` schemes plus editable PIMPLE outer, inner and
  non-orthogonal corrector counts. The LS(1)-0417 preset uses `backward`, three
  outer corrections and the published-equivalent `dt*=0.01276096076`.
- Added a bounded native-versus-PyFoam and 1/4/6-rank benchmark harness that
  runs only in `/tmp` and requires explicit `--run` solver permission.
- Added a WSLg-independent live solver monitor that reads the authoritative
  PyFoam logs and force histories, renders residuals, equation iterations and
  Cl/Cd/Cm headlessly, and embeds its updated snapshot in Streamlit.
- Added deterministic ParaView startup scripts using absolute `.foam` paths,
  automatic `internalMesh`/latest-time selection, camera fitting, screenshots,
  reusable `.pvsm` state and readiness metadata.
- Added standalone steady-initialization ParaView archives with configurable
  evenly spaced SIMPLE snapshots, explicit iteration-time semantics and a
  steady Cl/Cd efficiency history.
- Added transient and stationary Cl/Cd aerodynamic-efficiency CSV/PNG products.
- Added a non-blocking A--F engineering mesh-quality assessment with numerical
  margins, solver-risk guidance and plain-text/JSON reports.
- Added `Documents and Manuals/CFD 2D/CFD_SOLVER_MESH_TECHNICAL_REVIEW.md` with
  the solver-method audit, measured inlet-mesh trials and production-validation
  requirements.
- Added `CFD_2D/reports/LS1_0417_VALIDATION_EXECUTION_AUDIT_20260722.md` with
  the one-metre similarity conditions, real mesh metrics, boundary-condition
  audit, measured solver cost and remaining validation requirements.
- Added the `reference_uncut_validation_1m` CFD-only geometry, its 50c/50-layer
  mesh preset, a first laptop smoke preset at `t*=0.2`, runtime projection
  tooling, wall `Cp(x/c)` products and ParaView volume-Cp visualization.

### Changed

- The postprocessor now retains exactly three canonical live diagnostics:
  residuals, Cl, and combined Cd/Cm. Linear-iteration plots and the temporary
  duplicate monitor image are no longer presented as user diagnostics.
- Streamlit refreshes solver status/logs every two seconds but refreshes live
  monitor images every 90 seconds. Monitor discovery now follows steady
  extensions, steady-to-transient transitions and the active angle of a
  sequential sweep. Cl uses `[-0.8, 2.0]`; Cd/Cm use
  `[-0.2, 0.2]`. Raw histories remain unclipped.
- ParaView startup now reports whether at least two positive field times exist
  for animation and frames the automatic Cp screenshot around the airfoil
  chord instead of the complete farfield.
- Aerodynamic-efficiency means use only the configured final history window;
  the plotted trace and raw CSV remain complete.
- Numerical boundary-layer thickness now uses the local wall-tangential
  velocity and a sampled local edge velocity, locating the first monotonic
  crossing of `|U_t|/Ue=0.99`.
- Aerodynamic-efficiency plots now use a compact 2:1 layout and a visible
  engineering range of 0--100 while preserving all raw Cl/Cd data in CSV.
- ParaView startup now reads the OpenFOAM reader's actual time list, selects
  the latest time explicitly and uses a fixed XY camera/view size for both
  transient and archived SIMPLE cases.
- A steady ParaView package is now `PREPARED_FOR_PARAVIEW` until ParaView has
  loaded and rendered it; only its generated readiness JSON can report
  `READY`, and the app displays that evidence and screenshot.
- Six MPI ranks are now the documented production recommendation for the
  current Ryzen 7 4800H host; one rank remains preferable for short smoke
  tests where decomposition/reconstruction dominates.
- OpenFOAM case configuration no longer exposes the obsolete
  `config_scheme`; the application always writes the current complete schema.
- The validation physical configuration now reads the selected variant chord,
  so the one-metre geometry, Mach-derived velocity, Reynolds number,
  force-coefficient references and saved provenance cannot silently disagree.
- Incremented the synchronized application/backend/launcher contract to API 10.
- Hardened the optional SIMPLE initialization with the
  `robust_sa_initialization_v2` profile: bounded first-order U/nuTilda
  convection, 0.5 cell-limited gradients, GAMG/DIC pressure,
  PBiCGStab/DILU transport solvers and relaxation `p=0.2`, `U=0.3`,
  `nuTilda=0.2`. Transient PIMPLE numerics are unchanged.
- PyFoam now requests a clean write before catastrophic field growth when an
  aerodynamic coefficient exceeds an absolute diagnostic limit of 20 or the
  local continuity sum exceeds 100.
- The open-inlet sensitivity tool now runs in an isolated native-Linux sandbox
  and can compare inner-wall discretization and throat growth without replacing
  the active mesh.

### Fixed

- Decoupled force-runaway protection from the display-only startup skip: every
  new `forceCoeffs` row is now checked even when the first samples are hidden
  from the live plot. Catastrophic partial Cp fields are preserved but labelled
  explicitly as nonphysical diagnostics in the wall report and plot.

- Fixed the Windows launcher synchronizing successfully while Streamlit stayed
  unreachable inside WSL by binding the WSL server to `0.0.0.0`; the user URL
  remains the local `http://localhost:8501` endpoint.
- Fixed compressed OpenFOAM fields such as `U.gz`, `p.gz`, `yPlus.gz` and
  `wallShearStress.gz` being reported missing after successful postprocessing.
- Fixed very short live-monitor runs showing no force panel despite existing
  force samples when the normal startup-sample exclusion exceeds the bounded
  test history.
- Fixed ParaView's archived steady view opening with tiled/copy-like framing by
  setting the absolute reader time, camera normal and deterministic viewport.
- Replaced PyFoam 2026.6/Gnuplot live windows after reproducing blank copy-mode,
  FIFO command fragmentation and temporary-file races under WSLg. PyFoam still
  owns solver execution and post-run replay; only the live display transport
  changed.
- Fixed the normal OpenFOAM `sigFpe : Enabling...` banner being misclassified
  as evidence of an actual floating-point exception.
- Fixed resumed PyFoam runs treating already stored force-coefficient samples
  as newly generated runaway values; historical rows remain visible but only
  samples produced by the current process can request a clean stop.
- Fixed live-monitor time parsing for OpenFOAM lines such as `Time = 10s`,
  fixed active-log selection when `reconstructPar` is the final script stage,
  and made any nonzero solver return code override partial-stop classifications.
- Added a physical-core MPI preflight. The current WSL host exposes eight
  usable OpenMPI slots: six remains the responsive default, eight is allowed,
  and larger requests fail early instead of silently oversubscribing.
- Fixed runner timeout handling so a controlled partial write is not converted
  into a hard solver failure, and fixed normal OpenFOAM `sigFpe` startup text
  being classified as an actual divergence.
- Fixed the external application stop marker so `writeNow` is observed during
  both steady and transient execution, followed by reconstruction and clean
  child-process shutdown.
- Fixed repeated wall-field VTK exports overwriting one another. `Cp` and
  `yPlus` now have field-specific archives, and the loader skips newer VTK
  files that do not contain the requested field.
- Fixed wall-layer reporting silently reading the active/default mesh config
  instead of the mesh actually used by a saved case. The report now prefers
  `mesh_config_used.json` and exact values from `mesh_quality_report.json`.

### Verified

- Real bounded OpenFOAM 13 runs measured 28.08 s native serial versus 30.99 s
  PyFoam serial. Four and six ranks reduced the first solver step from about
  10 s to 4.90 s and 3.92 s respectively; short total time remained dominated
  by decomposition/reconstruction.
- Real postprocessing completed `yPlus`, `wallShearStress`, `vorticity` and
  `foamToVTK`, and both transient and separately archived SIMPLE cases loaded
  in ParaView with visible U-magnitude fields and reusable state files.
- Real Gmsh 4.15.2 -> `gmshToFoam` -> `checkMesh` trials rejected MeshAdapt and
  a three-node inlet fan, found no benefit from target-size-only refinement and
  reduced the open debug candidate's maximum non-orthogonality/skewness from
  69.975/3.795 to 65.184/3.471 using 160 lip and 176 inlet-marker nodes.
- Verified that SIMPLE directories are archived as iterations and PIMPLE is
  explicitly reset to physical `t=0` after final-field transfer.

- A four-case real Gmsh/OpenFOAM inlet study showed that increasing the inner
  wall node factor from 0.70 to 0.85 did not change the limiting quality
  metrics. Reducing throat growth from 1.22 to 1.18/1.10 increased maximum
  skewness from 3.795 to 4.514/4.670 and failed `checkMesh`; the measured
  `0.70/1.22` debug default is retained.
- A six-case real open-inlet curvature/thickness-layer study retained the
  `0.08c` curved transition with 16 automatically resolved thickness nodes:
  432650 cells, maximum non-orthogonality 65.184 degrees, maximum skewness
  3.471, minimum determinant 0.002194, interpolation weight 0.08171 and volume
  ratio 0.08896. Twelve and twenty thickness nodes failed different
  `checkMesh` criteria, so neither was promoted.
- A corrected six-rank PyFoam monitor run completed a controlled `writeNow`
  stop, reconstructed real fields, generated residual/Cl/Cd-Cm plots
  and left no solver, MPI or monitor process behind. Eight ranks also ran;
  ten ranks are now rejected because the host exposes only eight physical
  slots.
- A real one-metre validation mesh completed Gmsh 4.15.2, `gmshToFoam` and
  OpenFOAM 13 `checkMesh`: 334,857 cells, 50 requested BL layers with BL hex
  cells confirmed, maximum non-orthogonality 38.382 degrees, maximum skewness
  0.6226 and minimum determinant 0.006662.
- A bounded six-rank staged run verified SIMPLE/SA field transfer to a new
  PIMPLE physical `t=0`, separate steady/transient monitors, steady ParaView
  archives, volume Cp, wall Cp/y+ extraction, wall shear, vorticity, VTK export
  and external clean stop. The partial run is diagnostic, not validated.
- The latest seven-step transient measurement on that mesh conservatively
  projects roughly 14 minutes for the `t*=0.2` smoke preset, 36.3 hours for
  the 2,500-nominal-step preliminary duration and 363 hours for the published
  25,000-step duration on this laptop.

## [2026-07-28]

- Context snapshot for backend API 13. It records solver schema 12, the
  archived Cummings time-accuracy reference, topology-specific temporal
  budgets, the case-level time-step assessment and the retained real
  mesh/Courant evidence. Active CFD physics remains unchanged.

## [2026-07-27]

- Context snapshot for backend API 13. It records the selected 327,909-cell
  open comparison mesh, solver schema 11, measured adaptive/fixed time-step
  behavior, aligned force-monitor axes, physical-time URANS labels and robust
  validation publication/error summaries.

## [2026-07-24]

- Context snapshot for backend API 13. It records the optional CATIA launcher,
  schema-10 solver configuration, temporary-workspace write protection and the
  verified native 8-rank SIMPLE-to-PIMPLE workflow.

## [2026-07-23]

Context snapshot for backend API 12. The detailed OpenFOAM 14, postprocess,
portability and storage audit is recorded under the current Unreleased entries
and in `CFD_2D/reports/OPENFOAM14_SOLVER_POSTPROCESS_PORTABILITY_AUDIT_20260723.md`.

## [2026-07-20]

### Added

- Added restart-aware OpenFOAM execution with explicit `--resume` and optional
  extension in convective time, preserving overlapping `forceCoeffs` segments.
- Added sequential angle-of-attack sweeps with per-angle timeout, optional
  statistically stationary force-coefficient stopping and per-case
  postprocessing.
- Added an opt-in SIMPLE/`steadyState` initialization stage with
  `residualControl`, force-history transition checks, archived steady output
  and field transfer into the transient PIMPLE initial condition.
- Added a persistent stationary-stage decision flow. A non-converged SIMPLE
  stage now exposes percentage metrics for Cl/Cd/Cm and can be extended,
  transferred explicitly to the transient stage, or archived and finished.
- Added wall `y+` versus `x/c` plots for upper/lower surfaces, wall-normal
  velocity profiles and numerical/theoretical/prismatic boundary-layer
  thickness comparisons.
- Added live and replayed PyFoam monitors restricted to residuals, iterations
  and real Cl/Cd/Cm histories.
- Added working-case containers with multiple named geometry, CFD-case, mesh,
  simulation and postprocess packages under one traceable `Results` folder.
- Added one authoritative domain configuration for circular, C-grid-like,
  rectangular and debug domains, shared by the UI and mesh builder.
- Added explicit coarse/medium/fine starting sets with common geometry and BL
  foundations and 20/40/50 normal layers.
- Added an optional tangent-Bezier exterior inlet bridge and a measured
  open-inlet sensitivity report.
- Added early `nuTilda`/continuity runaway detection to the PyFoam worker, with
  a clean `stopAt writeNow` request and structured divergence diagnostics.

### Changed

- The postprocessor now writes only the averaging-window coefficient-history
  plot; the former full-startup-history image is no longer generated.
- PyFoam diagnostics shown or copied by the current application exclude legacy
  Courant, continuity, execution-time and deltaT plot files without deleting
  historical data.
- PyFoam coefficient displays now omit a configurable startup sample count and
  replay only a recent display window; the complete forceCoeffs history remains
  unchanged for convergence and postprocessing.
- Renamed the saved unconverged-SIMPLE override to
  `force_transient_after_unconverged_steady`; legacy configurations therefore
  default to the explicit extend/start/finish decision instead of silently
  forcing the transient stage.
- OpenFOAM case writing can prepare several alpha cases in one explicit action
  and records separate steady and transient numerical settings.
- Increased the closed validation candidate intermediate size to `0.05c` and
  farfield size to `2.5c`, preserving the slow 50c-domain transition.
- Promoted the measured open inlet-refinement candidate: 1,920 exterior nodes,
  144 inlet-interface nodes and a more local cavity transition.
- Changed mesh levels from hidden runtime overrides to one-shot editable bases;
  loaded or manually edited JSON values now have priority and show as custom.
- Centralized all domain dimensions in the mesh page and moved mesh generation
  controls above the detailed editor.
- Incremented the synchronized application/backend/launcher contract to API 9.
- New circular OpenFOAM cases use the paired OpenFOAM 13
  `freestreamVelocity`/`freestreamPressure` conditions and scalar `freestream`
  for `nuTilda`; the legacy fixed-velocity fallback remains selectable.
- SIMPLE initialization now uses limited gradients, bounded upwind convection
  for `nuTilda`, conservative relaxation and fresh-case `potentialFoam`
  preconditioning. The solver configuration schema is now 4.
- PyFoam live monitors now use the WSLg `wxt` terminal with FIFO transport and
  non-persistent windows. The Cl/Cd/Cm display range is fixed to `[-1.5, 2]`
  without filtering raw coefficient data.

### Fixed

- Fixed staged PyFoam execution stopping in `potentialFoam` because the SIMPLE
  `fvSolution` omitted the OpenFOAM 13 `Phi` solver. New and legacy cases now
  receive the official GAMG/DIC entry, and failures report the actual stage log
  instead of a nonexistent `log.foamRun`.
- Added a live-monitor preflight to the PyFoam report and retained the bounded
  `[-1.5, 2]` Cl/Cd/Cm WSLg watcher configuration for both SIMPLE and PIMPLE.
- Restored every supported open-airfoil control in the mesh editor even when a
  closed-focused or older active JSON omitted those keys; defaults are seeded
  in memory and persist only after an explicit Save.
- Promoted the measured open 20c debug baseline to inlet growth `1.22` and
  exterior Bezier handle `0.08`. The real OpenFOAM 13 check reports Mesh OK
  with 421,131 cells, max non-orthogonality 69.975 and max skewness 3.795.
- Fixed clean remeshing leaving stale checkMesh JSON, VTK sets and ParaView
  screenshots from a previous failing mesh in the active directory. Those
  artifacts now follow the selected archive/delete policy; the stale active
  files found during migration were archived, not deleted.
- ParaView checkMesh inspection now disables stale user registry/session state
  and renders failed sets with tube highlights over a translucent base mesh.
- Fixed Ubuntu ParaView 5.10 startup scripts by supplying the packaged Python
  module path, emitting valid Python for VTK-only views and framing isolated
  failed faces obliquely instead of displaying them edge-on in copy mode.

- Fixed OpenFOAM 13 SIMPLE initialization failing with `Unable to set reference
  cell for field p` by writing and upgrading `pRefCell 0`/`pRefValue 0` in the
  stationary `fvSolution` template.
- Propagated the selected archive/delete/keep policy into the OpenFOAM writer
  and added explicit active-simulation cleanup. `delete` no longer creates an
  implicit Previous Versions backup and never touches user-saved Results.

- Fixed wall postprocessing selecting an older binary VTK instead of the most
  recent ASCII wall export, and fixed the OpenFOAM 13 sampling-dictionary form
  used for normal velocity profiles.
- Fixed sweep status reporting so a rejected steady initialization is not
  mislabeled with the inner runner's last transient-style status.
- Normalized the staged/sweep `checkMesh` gate argument so dry-run and real
  scheduling no longer fail on the former mixed-case argparse destination.
- Fixed PyFoam replay so Cl/Cd/Cm plots are regenerated from the complete,
  restart-aware `forceCoeffs` history.
- Made closed profile/TE preprocessing depend on its explicit configuration,
  not on `mesh_level=debug`; `fine` meshes now receive the same audited tangent
  cap and fail clearly if enabled rounding cannot be applied.
- Restored Results stages now record `active_workspace.json`, rebuild controls,
  select the restored stage and prefer the restored active mesh JSON.
- ParaView checkMesh inspection now uses an applied startup script with visible
  problem sets, camera reset and screenshot instead of unopened positional
  readers.
- Application shutdown now closes registered project viewers and ignores stale
  managed-job PIDs that no longer match the recorded command.
- Legacy project viewers created before PID registration are recognized by
  native/UNC project paths or their project working directory; unrelated
  ParaView/Gmsh sessions are left untouched.
- Fixed named Results package creation when its stage collection directory did
  not yet exist.
- Fixed Results geometry provenance so a saved variant resolves its own source
  profile from the case-package manifest instead of inheriting the profile that
  happened to be selected in the UI.
- Fixed labeled configuration fields so their stable key, rather than their
  translated display label, selects the allowed dropdown values. Domain,
  backend and topology controls can no longer degrade into unrestricted text.
- Fixed the open inlet smoothing prototype so only the BL-carrying exterior
  bridge is curved; the cavity-side bridge remains straight and no longer
  crosses the exterior connector.
- Fixed failed SIMPLE runs leaving reconstructed steady iterations in the
  active transient case. Diverged/failed stage data is now archived and the
  pristine transient `0/` and system dictionaries are restored.
- Fixed PyFoam live Gnuplot watchers losing `/tmp/*.gnuplot` data files and
  opening unusable copy-mode windows by switching live data transport to FIFO.
- Removed the native runner's silent `potentialFoam || true` failure path;
  initialization errors now stop the stage with an explicit log.

## [2026-07-19]

### Added

- Added `PROJECT_CONTEXT_FOR_CODEX.md` as the authoritative project map for
  future Codex sessions.
- Added root `AGENTS.md` with safety, architecture, validation and documentation
  maintenance rules.
- Added a machine-checkable context/version consistency check.

### Changed

- Documented active-workspace versus reusable Results ownership and the
  expected load/edit/version workflow.
- Documented the exact current closed and open mesh validation state instead of
  presenting diagnostic outputs as accepted aerodynamic meshes.

## [2026-07-18]

### Added

- Added user-curated `Results` snapshots for geometry, operating cases, meshes,
  simulations and postprocessing.
- Added exact `checkMesh` VTK problem-set export and localized quality reports.
- Added XFOIL-driven 2D inlet-design controls and generated-profile metadata.
- Added WSL bootstrap checks, installation prompts and portable CATIA package
  tooling.

### Changed

- Renamed and reorganized the project as `DESIGN APP`, with canonical Windows
  and native WSL runtime trees.
- Expanded the Streamlit application to cover geometry, mesh, OpenFOAM case,
  execution and postprocessing stages.
- Refined the closed reference mesh defaults and introduced an explicit open
  ram-air diagnostic candidate.

### Fixed

- Made heartbeat writes concurrency-safe.
- Prevented stale active mesh reuse and empty `polyMesh` creation.
- Kept the solver dry-run by default and preserved partial results after clean
  timeout handling.
## [2026-08-27]

### Added

- Added a separate closed-airfoil experimental mesher beside the open-airfoil
  laboratory. It builds one connected exterior wall, an explicit LE, a
  tangent-continuous rounded TE cap, a 75-layer configurable boundary layer
  and a circular graded farfield without creating an internal fluid region.
- Added revision loading, generation, Gmsh opening, approval and optional full
  quality-distribution controls for closed experimental meshes.
- Added an optional strict phase-D validation gate that compares the final
  steady coefficient mean with the phase-D window and can use phase D as the
  reported production sample when the configured equivalence test passes.
- Added focused regression tests for validation phase resume, validation case
  parsing, saved-mesh quality lookup and closed experimental wall continuity.

### Changed

- Rebuilt the open experimental inlet continuation from the exact shared
  uncut-profile samples. The retained open wall now agrees with its uncut base
  to below `6e-10 m`; the inlet contains no artificial connector and its two
  lip joins are tangent-continuous to approximately `0.1 deg`.
- Reduced the default open inlet interior sizing factor from `0.50` to `0.45`
  after a controlled mesh comparison. This moved the minimum interpolation
  weight from a failing `0.04879` to a passing `0.05228`.
- Validation phase resume now reads the latest physical time and skips A-E
  phases already completed, so a clean interruption continues the unfinished
  phase instead of replaying initialization.
- Migrated historical closed RANS checkpoints at 8 degrees to explicit
  `__alpha_p8` identities. New closed 16-degree and open 16-degree campaign
  entries remain distinct and uncreated until deliberately run.
- SIMPLE/PIMPLE non-orthogonal controls now read the selected saved mesh
  package, including the `Mesh Data` Results layout. Current medium closed and
  open meshes resolve automatically to zero non-orthogonal correctors and
  `Gauss linear corrected` from maxima near 41.5 degrees.
- Validation live monitoring now distinguishes RANS from URANS unambiguously,
  parses names such as `alpha_p12p000` correctly and reports the active A-E
  phase from physical time.

### Fixed

- Fixed experimental-mesher actions serializing `pathlib.Path` objects into
  job JSON.
- Fixed clean validation stop handling so stopping the parent job cannot leave
  an OpenFOAM/MPI child running. Partial fields remain restartable.
- Fixed closed experimental quality-study invocation to use the current
  quality-distribution API.

### Verified

- `closed_validation_beta75_experimental_v1`: 116,836 cells, exactly 75 BL
  layers, `checkMesh` pass, max non-orthogonality 42.599 degrees, max skewness
  0.6098, min determinant 0.004714, min interpolation weight 0.1208 and min
  volume ratio 0.1374.
- `beta75_exact_shared_source_inlet_v33_interface045`: 166,342 cells, exactly
  75 BL layers, `checkMesh` pass, max non-orthogonality 41.936 degrees, max
  skewness 0.61255, min determinant 0.004288, min interpolation weight 0.052277
  and min volume ratio 0.073566. The preceding v32 failure remains preserved.
- Full software suite: 284 tests passed. Final focused synchronization check:
  4 tests passed; Python compilation and the complete WSL/OpenFOAM/Gmsh
  environment audit passed.
