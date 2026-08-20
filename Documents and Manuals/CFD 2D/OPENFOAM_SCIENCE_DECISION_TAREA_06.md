# TAREA 06 — OpenFOAM UI and science decision

Date: 2026-08-20
Runtime verified: OpenFOAM Foundation 14.

## Applied contract

- Solver configuration schema is 15. The UI writes the current constant and
  no longer downgrades saved files to schema 13.
- Reynolds, Mach, fluid properties and chord have one owner: the CFD Case.
  The case writer no longer accepts a different Reynolds as an override. Each
  prepared case records the complete effective values and their ownership in
  `applied_solver_configuration.json`.
- The OpenFOAM page has four sections: General, Solver Settings, Writing &
  Postprocess, and Traceability. Closed and Open controls are shown in parallel.
  Case preparation accepts the active angle, all CFD Case angles or an explicit
  subset.
- “Stationary/Transient” means a steady RANS/SIMPLE initialization followed by
  transient URANS/PIMPLE. Both initializers are capped at 20,000 iterations.
- General Closed URANS uses at most 10 outer correctors and can exit early on
  absolute `U+nuTilda` residuals. Open convergence retains at most 15 and uses
  `U+p`. Validation Lab remains fixed-deltaT/fixed-corrector and strips the
  adaptive outer exit.
- In adaptive mode, the entered `maxDeltaT*` is the requested physical ceiling.
  The internal starting step is clamped to that ceiling. OpenFOAM may reduce
  `deltaT` to meet Courant and may recover it only up to the ceiling.
- Emergency guards are `maxCo=50` Closed and `maxCo=25` Open. They are not
  temporal-accuracy targets.
- Volume fields are written at approximately 2,000 requested physical steps.
  `forceCoeffs`, residuals and Courant/deltaT remain continuous; `purgeWrite`
  applies only to volumetric time directories, not function-object histories.

## Transport correction audit

The OpenFOAM 14 `pimpleNoLoopControl` source reads
`transportCorrectionFinal` (with the older alias
`turbOnFinalIterOnly`) and defaults it to `true`. Its transport-correction
predicate is true on every outer iteration when the value is `false`, and only
on the final outer iteration when it is `true`.

The project default remains `false`. It is exposed as an advanced option with
the correct semantics; TAREA 06 does not change it before a controlled
scientific comparison.

## Bounded Open smoke

`CFD_2D/scripts/ramair_2d_openfoam_science_smoke.sh` creates a private
`/tmp/ramair_t06_*` workspace, generates schema-15 dictionaries, copies the
real checked `open_ramair_validation_1m_coarse` polyMesh, and removes only that
validated temporary workspace when finished.

Observed result:

| Check | Result |
|---|---|
| OpenFOAM dictionary parsing | PASS |
| `checkMesh` | `Mesh OK` |
| `maxCo` | 25 |
| `nOuterCorrectors` | 15 |
| outer residual fields | `U`, `p` |
| `transportCorrectionFinal` | `false` |
| one Euler step at `1e-8 s` | exit 0, `End`, no fatal error |
| residual-controlled outer loop | converged in 4 iterations |

This is only a bounded parser/startup smoke. It supports migrating the Open
guard from 20 to 25, but it is not evidence of URANS stability, aerodynamic
convergence, time-step independence or validation.

## Rerun

```bash
bash CFD_2D/scripts/ramair_2d_openfoam_science_smoke.sh \
  /home/alejm/ramair_cfd/DESIGN_APP \
  /home/alejm/ramair_cfd/DESIGN_APP \
  /tmp/ramair_t06_openfoam_smoke.json
```

The first argument may instead be the mounted Windows source when testing the
not-yet-synchronized working tree. No production case, Work Case revision,
mesh approval or result is modified.
