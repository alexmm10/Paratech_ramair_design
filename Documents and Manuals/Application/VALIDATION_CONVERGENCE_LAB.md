# Validation & Convergence Lab

## Purpose

The Validation & Convergence Lab is an isolated workspace for comparing the
closed and open LS(1)-0417 geometries at Mach 0.15, Reynolds number 1.9e6,
chord 1 m and angle of attack 8 degrees. It does not alter the normal active
workflow and it does not manufacture missing CFD evidence.

The implementation contract is archived at:

`Documents and Manuals/CFD 2D/Specifications/CODEX_VALIDATION_LAB_RANS_CHECKPOINTS_UI_MONITORING_STORAGE_UPDATES.md`

The current complete contract is:

`Documents and Manuals/CFD 2D/CODEX_VALIDATION_LAB_COMPLETE_RANS_URANS_SPACE_TIME_RESTRUCTURE.md`

The isolated registry is schema 6; general solver schema 13 and backend API
18 are unchanged. The UI now has six main sections: meshes/conditions,
solver strategy, RANS, URANS, space-time convergence and reports/workspace.

## Workflow

1. Select one coherent simulation set: geometry, CFD conditions and one
   registered mesh package.
2. Verify the mesh hash, physical contract, cell count and `checkMesh` result.
3. Prepare the six independent RANS/SIMPLE checkpoint jobs.
4. Execute the queue explicitly. Jobs are sequential, resumable and bounded.
5. Select a temporal step from the paper, measured-host, spectral or custom
   families.
6. Prepare and verify the URANS/PIMPLE run before enabling execution.
7. Monitor only scalar log data during execution. Volume fields and ParaView
   are handled separately and on demand.
8. Review storage inventory before deleting regenerable active-case data.

## Solver Separation

RANS uses SIMPLE with `nNonOrthogonalCorrectors = 0`. URANS uses PIMPLE with
defaults `nOuterCorrectors = 3`, `nCorrectors = 2` and
`nNonOrthogonalCorrectors = 1`. These controls are stored in separate
configuration sections and are not silently shared.

Each RANS checkpoint is tied to normalized SHA-256 hashes of the mesh, physical
contract and solver settings. A changed mesh or physics definition makes the
checkpoint stale. Missing or incompatible checkpoints block production URANS
with the structured status `BLOCKED_MISSING_RANS_CHECKPOINT`.

## Temporal Presets

- **Paper reference:** starts at 2.5e-4 s and applies successive halvings.
- **Measured host:** uses measured median, p25, p75, mean and standard
  deviation from the current machine.
- **Spectral:** applies `dt* <= 1 / (St_max * N_cycle)`, with default
  `St_max = 20`.
- **Custom:** accepts explicit values and records their source.

Pilot runs are labelled **Prueba corta de viabilidad numerica**. They are
diagnostic only and never establish aerodynamic validity.

## Monitoring And Storage

The live monitor reads only appended log bytes and keeps a bounded force tail.
Refresh choices are 15, 30 or 60 seconds, with 30 seconds by default. It
reports residuals, time, `deltaT`, Courant number, continuity, force
coefficients and host-performance statistics. It does not read volume fields
or launch ParaView.

Storage inventory is written as JSON and CSV. Cleanup is limited to the active
laboratory cases, preserves `0/`, `constant/`, `system/` and the latest restart,
and refuses to touch `Results/`. RANS storage is compact by default; URANS
volume-state retention is bounded.

## Safety

Execution buttons are distinct from preparation and verification. Real Gmsh
or OpenFOAM work always requires an explicit execute action. CATIA is never
invoked by this laboratory. A diagnostic checkpoint override is visibly
labelled and cannot be mistaken for validated RANS convergence.

## Bounded Verification 2026-07-29

The following real, bounded checks were completed in the canonical WSL
runtime with Gmsh 4.15.2, OpenFOAM 14 and eight MPI ranks:

- The isolated open-light sweep generated three new meshes. Factors 1.10,
  1.20 and 1.30 contain 294,744, 287,616 and 281,182 cells respectively, and
  all pass `checkMesh`.
- Factor 1.20 was selected as the balanced candidate, but remains
  `CANDIDATE_AVAILABLE_NOT_PROMOTED`. No registered baseline was replaced.
- The closed-coarse smoke completed 100 SIMPLE iterations, copied and
  SHA-256-verified `U`, `p`, `phi`, `nuTilda` and `nut`, then completed 40
  URANS steps without divergence.
- The smoke report status is `SMOKE_COMPLETED_DIAGNOSTIC_TRANSFER`. This
  proves execution and restart continuity only; it is not a converged
  aerodynamic result.

The real `closed_coarse` RANS history reaches 20,000 iterations. Its automatic
gate remains `NOT_CONVERGED`, while the separate explicit review permits RANS
spatial comparison and URANS initialization. The PIMPLE 2/3/4 cases are
prepared from that exact checkpoint and remain
`BLOCKED_MISSING_PILOT_PASS`; no sensitivity result is claimed before a real
common pilot.
