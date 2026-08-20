# General URANS Execution and Remote Server Workflow

Version: 2026-08-18  
Application backend API: 24  
Solver configuration schema: 14

## 1. Scope

This document governs normal closed and open 2-D URANS work cases. The
Validation and Convergence Lab is deliberately excluded: it retains fixed
timesteps and fixed corrector counts so space-time comparisons remain
controlled.

## 2. Time integration and PIMPLE

`backward` is an implicit second-order time derivative. Implicit stability
does not guarantee temporal accuracy, so `maxDeltaT_star` remains the normal
physics/spectral ceiling. `adjustTimeStep` is kept active as an emergency
nonlinear safeguard rather than as the time-resolution design method:

- closed profile: `maxCo = 50`;
- open profile: `maxCo = 20`;
- maximum outer correctors per timestep: 15;
- early outer-loop exit: `U` and `nuTilda` absolute residuals below `1e-4`,
  with `relTol = 0`;
- pressure receives its normal final solve but does not gate outer-loop exit;
- `transportCorrectionFinal = false` updates transport/turbulence inside each
  outer correction.

These are execution defaults, not a substitute for timestep independence.
Production studies must compare force means, amplitudes, dominant frequency
and flow fields after changing `deltaT_star` at fixed mesh.

## 3. Open-airfoil walls

The generated open mesh owns two real boundary patches:
`airfoil_wall_external` and `airfoil_wall_internal`, both type `wall`. The
opening connects the external and cavity fluid regions and is not a physical
patch. A baffle conversion is needed only when an internal face zone must be
split into boundary faces. Applying `createBaffles` to this mesh would create
a second representation of an already explicit wall. Forces integrate all
airfoil wall patches as one rigid body.

## 4. Stop and restart contract

Each runner writes `.ramair_solver_process.json` with the solver PID, process
group and Linux `/proc` start token. Clean stop writes a durable request,
changes `controlDict` to `stopAt writeNow` and waits for a checkpoint. If the
solver does not stop within the grace period, escalation is SIGINT, SIGTERM
and finally SIGKILL. A latest root or processor time makes the result
`PAUSED_RESTARTABLE`. Startup reconciliation repairs stale runtime labels and
never deletes fields.

## 5. ParaView products

Automatic pressure and velocity products pass cell data to point data before
tracing streamlines. The seed is a 1.5-chord line perpendicular to freestream,
centered at x/c = -0.25, with 120 seed points and maximum streamline length of
8 chords. It covers the near-airfoil affected flow rather than the complete
farfield. Generated metadata records seed geometry, resolution and angle.

## 6. Remote execution package

From the application Execution page, select written OpenFOAM cases and create
the server ZIP. The archive contains frozen cases, solver scripts,
configuration, a sequential queue, checksums and these launchers:

- `run_remote_queue.sh`: start pending cases;
- `resume_remote_queue.sh`: continue restartable cases;
- `stop_remote_queue.sh`: request clean write-and-stop;
- `force_stop_remote_queue.sh`: bounded forced stop;
- `monitor_remote_queue.sh`: show queue and active solver state;
- `postprocess_remote_queue.sh`: postprocess completed/partial cases.

OpenFOAM Foundation 14 and MPI are host requirements. Python dependencies are
installed in a package-local virtual environment; an optional wheelhouse
supports offline servers. The package does not contain CATIA or existing
project simulation history.

## 7. Git and Docker

Git tracks source, configurations, tests and authored documentation. It does
not track meshes, OpenFOAM time fields, Results, runtime state, ZIP archives,
third-party PDFs or ParaView datasets. Run
`Application Support/Tools/check_repository_artifacts.py` before committing.

The Docker image is recommended for reproducible Linux preprocessing,
meshing, headless OpenFOAM execution and automated tests. It is not a CATIA
container and it does not replace the host ParaView desktop application. WSL
remains the most ergonomic local environment; the container is especially
useful for CI and remote Linux servers.

## 8. Primary references

- OpenFOAM Foundation v14 boundary conditions:
  https://doc.cfd.direct/openfoam/user-guide-v14/boundaries
- OpenFOAM Foundation v14 mesh zones:
  https://doc.cfd.direct/openfoam/user-guide-v14/mesh-zones
- OpenFOAM Foundation 14 Ubuntu installation:
  https://openfoam.org/download/14-ubuntu/
- OpenFOAM Foundation v14 patch information:
  https://openfoam.org/news/v14-patch/
- Parafoil tutorial using `createBaffles` for an internal face-zone topology:
  https://github.com/openfoamtutorials/parafoil/tree/master
