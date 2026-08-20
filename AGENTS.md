# Codex Instructions for RamAir: Design and CFD

## Start here

Read these files before changing the project:

1. `PROJECT_CONTEXT_FOR_CODEX.md`
2. `CHANGELOG.md`
3. `README_PROJECT_STRUCTURE.md`
4. The README and technical/manual documents relevant to the requested stage.

Canonical editable source:
`C:\Users\alejm\Desktop\PRACTICAS_INVICSA\3D design\DESIGN APP`

Canonical WSL runtime:
`/home/alejm/ramair_cfd/DESIGN_APP`

Do not develop in the old OneDrive `INPUT FILES` tree. Synchronize with
`run_ramair_cfd2d_app.py` or the official Windows launcher.

## Non-negotiable rules

- Do not execute CATIA.
- Do not run a solver unless the user explicitly requests a real CFD run.
- Short Gmsh, `gmshToFoam` and `checkMesh` checks are permitted with timeouts.
- Never delete data without archive or explicit user-selected deletion.
- Never create empty `polyMesh` or empty/fake result files.
- Never mark diagnostic geometry or a failing open mesh as PASS.
- Preserve the technical specification as the primary CFD 2D contract.
- Do not silently alter turbulence, boundary conditions, force references,
  fluid properties, time integration or solver selection.
- Work with user changes; do not reset or revert unrelated files.

## Architecture

- Keep the Streamlit app as an orchestrator. CAE logic belongs in stage scripts.
- Keep the case builder, mesh builder, quality controller, case writer, runner
  and postprocessor responsibilities separate.
- Keep CATIA inputs limited to `CATIA/Inputs`.
- Keep profiles under `Airfoil Profiles`.
- Keep active generated data under `CFD_2D` and reusable snapshots under
  `Results`.
- Keep automatic backups under `Previous Versions`.
- Maintain UI/backend API compatibility and increment both API constants for a
  breaking backend change.

## Mesh and validation discipline

- Audit final geometry sent to Gmsh, not only the original airfoil file.
- Require unique points, nonzero consecutive segments, continuous curve chains
  and documented orientation.
- For rounded closed TE, require an applied tangent-continuous cap when enabled;
  a silent straight fallback is a failure.
- `frontAndBack` must be `empty`; `ram_air_inlet` must never be physical.
- Record exact `checkMesh` metrics, failed categories and VTK problem sets.
- Call a mesh a validation candidate only after real `checkMesh` succeeds.
- Call a CFD setup validated only after mesh independence and reference-data
  comparison; do not infer validation from software completion.

## Required maintenance

Update `CHANGELOG.md` for every user-visible behavior, layout, configuration,
geometry, mesh, solver or postprocessing change.

Update `PROJECT_CONTEXT_FOR_CODEX.md` whenever any of these change:

- canonical paths or folder ownership;
- main entry points or stage contracts;
- configuration ownership or schema;
- validated/default mesh strategy or measured quality metrics;
- supported software versions or installation process;
- Results loading/versioning behavior;
- known critical limitations and current priorities.

Run `Application Support/Tools/check_project_context.py` before completion.

## Verification before completion

1. Run focused tests for the edited modules.
2. Run `python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q`.
3. For mesh changes, run bounded Gmsh plus real `gmshToFoam`/`checkMesh` when
   the WSL tools are available; report if this was not run.
4. For UI changes, synchronize WSL, start the official app and inspect it at
   desktop and narrow viewport sizes.
5. Report exact files changed, checks run and remaining engineering risk.

## HPC skill routing policy

Use the installed HPC skills progressively and only when relevant:

- Gmsh geometry, boundary layers, refinement, physical groups, mesh quality or
  export: use `hpc-gmsh`.
- OpenFOAM cases, dictionaries, boundary conditions, turbulence, numerics,
  RANS/URANS, timestep/Courant control, function objects, decomposition,
  `checkMesh` or solver failures: use `hpc-openfoam`.
- ParaView pipelines, state files, `pvpython`, `pvbatch`, screenshots or
  automated postprocessing: use `hpc-paraview`.
- MPI launch, ranks, affinity or parallel OpenFOAM: use `hpc-mpi`, normally
  together with `hpc-openfoam`.
- Multi-stage execution, resources, process lifecycle, monitoring, logs,
  staging or concurrency: use `hpc-orchestration`; add `hpc-foundations` when
  OS, WSL, hardware or storage fundamentals matter.
- Compiler, CMake, native build, modules, ABI/link or compiled dependencies:
  use `hpc-toolchains`.

For cross-domain tasks, combine only the minimum relevant skills. Read each
selected `SKILL.md` first and only the references required by the task.
Solver-specific skills govern technical solver/mesh decisions; orchestration
coordinates execution. Do not activate GPU, Spack or unrelated solver skills
unless the task explicitly requires them. Streamlit-only and ordinary Python
changes do not require an HPC skill unless they affect an HPC subsystem.
