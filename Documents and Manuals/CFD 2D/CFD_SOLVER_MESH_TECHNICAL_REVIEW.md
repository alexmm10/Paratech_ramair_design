# RamAir 2D CFD solver and mesh technical review

Review date: 2026-07-22

## Scope and evidence

This review covers the implemented 2D workflow from the extruded Gmsh mesh to
OpenFOAM 13 execution and postprocessing. It does not validate the aerodynamic
model. Its evidence is:

- `CFD_2D_TECHNICAL_SPECIFICATIONS.txt`;
- `2D_cfd_gridquality_openairfoils.pdf`;
- the local Gmsh and OpenFOAM manuals under `Documents and Manuals`;
- the PyFoam presentation supplied with the project;
- Ghoreyshi et al., *Aerodynamic Modeling of the Begian Experimental
  Paraglider*, DOI `10.2514/1.C033763`, including its LS(1)-0417 validation;
- Belloc et al., *Influence of the air inlet configuration on the
  performances of a paraglider open airfoil*;
- bounded open-inlet trials stored in
  `CFD_2D/reports/open_inlet_transition_trials_20260721_202859` and
  `CFD_2D/reports/open_inlet_transition_trials_20260721_203929`;
- OpenFOAM 13 official documentation:
  <https://doc.cfd.direct/openfoam/user-guide-v13/mesh>,
  <https://doc.cfd.direct/openfoam/user-guide-v13/fvschemes> and
  <https://doc.cfd.direct/openfoam/user-guide-v13/fvsolution>;
- the current Gmsh reference manual: <https://gmsh.info/doc/texinfo/gmsh.html>.

## Solver methodology

### Stationary initialization

The optional first stage is incompressible RANS with Spalart-Allmaras,
`steadyState` time discretization and SIMPLE. Its OpenFOAM time index is an
iteration counter, not physical time. The stage is only an initialization
method: it cannot represent the inherently unsteady exchange through an open
ram-air inlet and is not accepted as the final aerodynamic result.

The final SIMPLE fields are copied into transient directory `0/`. The original
SIMPLE directories are moved to a standalone archive containing selected
iterations, the final state, `constant/`, stationary dictionaries, force
history, an efficiency plot and a `.foam` marker. ParaView can therefore
inspect SIMPLE evolution without confusing iteration numbers with seconds.

### Transient calculation

After field transfer, the transient case explicitly uses `startFrom
startTime`, `startTime 0` and PIMPLE. Thus physical time starts at zero from the
stationary initial field. The current implementation uses first-order Euler,
adjustable time stepping constrained by `maxCo` and `maxDeltaT`, two outer and
two pressure-velocity correctors, and one non-orthogonal correction.

This is a conservative, robust starting method. A production result still
requires a time-step sensitivity study; first-order Euler adds numerical
dissipation. The case writer now also supports `backward` and
`CrankNicolson 0.9`. The LS(1)-0417 validation preset uses second-order
`backward`, three PIMPLE outer correctors and a nondimensional maximum time
step of `dt* = dt U/c = 0.01276096076`. This is the paper's reported
`dt=2.5e-4 s` at `c=1 m` and Mach 0.15, scaled by `c/U` for the 3.016 m
project geometry. It preserves the published nondimensional step without
claiming to reproduce the proprietary Cobalt or Kestrel discretizations.

Pressure reference values are explicitly present in SIMPLE and PIMPLE. The
circular farfield uses paired `freestreamVelocity` and `freestreamPressure`
conditions. Force normalization is written from the case density, velocity,
chord and one-cell span, and must be checked in every case manifest.

For the extruded 2D mesh the reference area is `Aref = chord * spanwise_width`.
OpenFOAM integrates pressure and viscous forces over the same physical width,
so this width cancels in Cl/Cd. It must not be replaced by chord alone, nor
must the fabric thickness be used as reference span. A very large or very low
startup efficiency is therefore not evidence of a span-normalization error;
it is normally an unconverged force-history sample and is excluded from the
configured averaging window.

### Restart semantics

A transient restart uses `latestTime` and preserves all existing reconstructed
time directories and `postProcessing` segments. Force and residual readers
merge restart segments by physical time and discard duplicate samples without
discarding later data. If the requested run is stopped or times out, OpenFOAM
is asked to write a consistent state before the process is terminated; that
state remains resumable. Changing geometry, mesh, density, viscosity, velocity,
angle, turbulence model or force references is a new case, not a restart.

### Convergence assessment

Residuals alone are insufficient for separated or periodically unsteady flow.
The workflow combines:

- residual and linear-solver iteration histories;
- boundedness/divergence checks for `nuTilda`, continuity and force values;
- comparison of consecutive Cl/Cd/Cm windows;
- mean drift and normalized fluctuations;
- a minimum convective time and a required observation window.

Automatic stopping is optional. It means statistically stationary over the
configured window, not proof of physical convergence. Open profiles should be
reviewed for periodicity, mass-flow behaviour and adequate observation time.

## Visualization and retained data

PyFoam remains an optional process runner and authoritative log producer. A
headless Matplotlib monitor incrementally reads the absolute case log and
`forceCoeffs` paths; Streamlit embeds its snapshot instead of opening the
PyFoam/Gnuplot FIFO windows that reproducibly appeared empty in copy mode. It
displays residuals, linear iterations and Cl/Cd/Cm. Exactly three replay
files are retained: `linear_residuals.png`, `linear_iterations.png` and
`force_coefficients.png`; the transient live snapshot is not copied as a fourth
duplicate diagnostic.

ParaView is opened through an absolute `.foam` marker and a generated Python
startup script. The script loads `internalMesh`, advances to the latest stored
state, colours by velocity magnitude (pressure fallback), resets the camera,
and writes a screenshot, `.pvsm` state and readiness JSON. This removes
dependence on stale ParaView registry/session state.

Both stationary and transient postprocessing produce Cl/Cd aerodynamic
efficiency histories. The transient postprocessor additionally retains the
configured field intervals for velocity, pressure, vorticity, wall shear and
`y+`. Scalar histories can be written every solver iteration while 3D fields
are sampled less often to control storage.

## LS(1)-0417 validation package

`CFD_2D/validation_cases/LS1_0417_M0p15_Re1p9e6` contains a traceable preset
for `M = 0.15`, `Re = 1.9e6` and the alpha range `-10` to `20 deg`. The
reference Experimental/Cobalt/Kestrel points were digitized from Figure 10 and
are explicitly labelled as approximate reference data. They live under
`CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016` with provenance and digitizing
uncertainty; they are never presented as generated project results.

The preset uses Mach to define velocity. Its effective viscosity is then
derived to satisfy the paper Reynolds number for the project's 3.016 m chord.
This is a similarity case, not standard sea-level air. `average_from_fraction
= 0.6` selects the final 40% of the history, equivalent to the final 10,000 of
25,000 reported steps. The validation plotter accepts only completed project
cases whose recorded Mach and Reynolds match the preset tolerances, so an
empty run produces a reference-only plot instead of fabricated comparison
points.

The present OpenFOAM baseline is incompressible RANS with Spalart-Allmaras.
That model is consistent with both supplied studies and is reasonable at
Mach 0.15, but it is not an exact reproduction of the compressible Cobalt and
Kestrel calculations. This limitation must remain visible when interpreting
drag and post-stall discrepancies. The open-airfoil study also shows that
inlet stagnation location, lip separation bubbles and cavity pressure dominate
the result; steady RANS should be used only as initialization when these
features are unsteady.

## Measured performance and bottlenecks

Bounded real runs on the current Ryzen 7 4800H / 16 GB host used the same
OpenFOAM 13 mesh and executable:

| Mode | Wall time for bounded test | Solver first-step scale | Peak parent RSS | Interpretation |
| --- | ---: | ---: | ---: | --- |
| Native serial | 28.08 s | about 10 s | 614 MB | Best for smoke tests; no decomposition cost |
| PyFoam serial | 30.99 s | about 10 s | 613 MB | About 3 s plotting/wrapper overhead only |
| Native 4 ranks | 38.82 s total | 4.90 s | 430 MB | Faster solver; short run dominated by setup |
| Native 6 ranks | 38.09 s total | 3.92 s | 429 MB | Recommended for long production runs |

Six ranks are therefore the default performance recommendation on this host:
they improve solver throughput without occupying all 16 logical CPUs. More
ranks are not justified by the current evidence and can increase MPI,
decomposition and reconstruction overhead. PyFoam and native OpenFOAM execute
the same solver; PyFoam's measured overhead is small for long runs.

The dominant costs are, in order of practical importance:

1. Fine mesh and linear-system solution at every time step.
2. Decomposition/reconstruction for short parallel runs.
3. Stored 3D time directories and `foamToVTK` export, which scale with the
   number of fields and written times.
4. ParaView loading/rendering of all stored times.
5. PNG replay and scalar CSV parsing, which are comparatively inexpensive.

For sweeps, retain force/residual scalars every iteration but write 3D fields
at a physically justified coarser interval, export VTK only for selected
angles/times, and disable the live GUI after workflow verification. Do not
disable `checkMesh`, force histories or solver logs.

## Real workflow verification (2026-07-22)

- Native and PyFoam bounded runs both produced real force data and clean
  `STOPPED_PARTIAL` states rather than false success.
- The custom live monitor reached `READY`, read the running residual log and
  closed through its stop marker without leaving a copy-mode FIFO window.
- A bounded SIMPLE stage was archived separately, retained iteration semantics
  and correctly refused automatic transient transition when it lacked enough
  force/residual samples.
- The transient stage starts at physical `t=0` after field transfer.
- Real OpenFOAM `yPlus`, `wallShearStress`, `vorticity` and `foamToVTK`
  commands completed on the bounded case. Compressed `*.gz` fields are now
  recognized by the postprocess inventory.
- ParaView loaded both transient and archived steady `.foam` cases using
  absolute paths, selected the latest available time and produced a single
  correctly framed XY-domain screenshot and reusable state.

## Mesh-quality decision model

`checkMesh` is the mandatory topological and geometric gate. The additional
engineering grade is deliberately non-blocking and reports margins for:

- maximum and mean non-orthogonality;
- maximum skewness;
- minimum cell determinant;
- minimum interpolation weight;
- minimum face-volume ratio;
- the location and count of problem sets when OpenFOAM writes them.

The grade does not certify grid independence, target `y+`, boundary-layer
coverage, time-step independence or agreement with validation data. High
aspect ratio inside a coherent wall-normal prismatic stack is not penalized by
itself; skewness, determinant, interpolation and abrupt transitions remain
decision-critical.

Practical interpretation for this project:

- Grade A/B: suitable candidate for solver testing, subject to visual review.
- Grade C: passes mandatory checks but has elevated numerical risk; use robust
  schemes and inspect localized cells before production.
- Grade D/F: diagnostic or failed mesh; do not use for aerodynamic conclusions.
- Maximum non-orthogonality approaching 70 degrees needs correction support;
  values approaching 85 degrees are generally difficult for the solver.
- Any failed determinant, skewness, interpolation or volume-ratio check is a
  rejection for production even if the mesh can technically be forced onward.

## Open-airfoil measured trials

All trials used the same circular 20c debug topology and real
Gmsh -> `gmshToFoam` -> `checkMesh`; no CFD solver was run.

| Candidate | Cells | Max non-orth. | Max skew | Min determinant | Min interpolation | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Baseline Delaunay | 430,756 | 69.975 | 3.795 | 0.002194 | 0.08153 | OK |
| MeshAdapt | 116,878 | 89.995 | 4.946 | 9.8e-9 | 1.17e-8 | FAIL |
| Frontal-Delaunay | 398,930 | 69.975 | 3.795 | 0.002194 | 0.08153 | OK |
| Three-point inlet fan | 431,532 | not improved | not improved | degraded | 0.000542 | FAIL |
| Refined lip/marker nodes | 432,650 | 65.184 | 3.471 | 0.002194 | 0.08171 | OK |

Isolation trials showed that increasing the lip and inlet-marker transfinite
node counts to 160 and 176 produced the improvement. Reducing only the local
target size to `0.00035c` produced no measurable change. The node controls are
therefore promoted to the open default; the overconstrained fan and MeshAdapt
alternatives are rejected. The circular 50c domain remains the production
default, while 20c is explicitly a debug domain.

The remaining minimum determinant is controlled by the local lip/BL topology,
not the farfield algorithm. Further optimization should therefore vary lip
curvature, tangential spacing and BL termination locally, one factor at a time,
and retain only candidates that improve all limiting metrics without excessive
cell growth.

## Production validation still required

Before comparing with experiments or publishing aerodynamic coefficients:

1. Run at least three systematically refined meshes and quantify Cl, Cd, Cm,
   inlet mass flow and wall distributions.
2. Demonstrate first-cell `y+` and total prismatic thickness appropriate to the
   selected turbulence treatment.
3. Repeat with a smaller transient time step or Courant limit.
4. Establish statistical stationarity over multiple convective times and any
   dominant oscillation periods.
5. Compare domain-size sensitivity and confirm 50c boundaries are sufficiently
   remote.
6. Compare against traceable experimental or independently validated CFD data.

Until those steps are complete, an `OK` mesh and a stable solver run are
software and numerical milestones, not aerodynamic validation.
