# URANS Restart and Stage Contract (API 23 / schema 10)

## Purpose

The Validation Lab treats a fresh calculation, an internal stage transition
and an external restart as different operations. A stage must never infer one
from the incidental existence of an OpenFOAM time directory.

| Start mode | Intended use | Required evidence |
| --- | --- | --- |
| `FRESH_FROM_CHECKPOINT` | New canonical case prepared from RANS | No positive URANS time directory |
| `CONTINUE_STAGE` | A -> B -> C -> D -> E hand-off in one timeline | Valid preceding checkpoint at the expected time |
| `RESUME_EXISTING` | User resumes an interrupted canonical case | Positive restart state and reconstructed phase cursor |

`CONTINUE_STAGE` sets `startFrom latestTime` and does not invoke the external
resume preparation path. Every stage stores its input checkpoint, requested
and actual time limits, time scheme, time step, solver start evidence, return
code, output checkpoint and terminal reason.

Before the planned backward stage, the predecessor preserves every temporal
write (`writeControl timeStep`, `writeInterval 1`, `purgeWrite 3`). The next
stage is blocked as `TEMPORAL_HISTORY_MISSING` unless the current state and two
old-time states separated by the intended `deltaT` exist. Those three times
are reconstructed, field-validated and decomposed together. This protects the temporal
history required by a second-order backward discretisation rather than
misclassifying an orchestration fault as numerical divergence.

## OpenFOAM references

- [OpenFOAM v14: time control](https://doc.cfd.direct/openfoam/user-guide-v14/controldict)
  documents `startFrom`, time-directory selection, `writeControl` and
  `purgeWrite` behaviour.
- [OpenFOAM v14: numerical schemes](https://doc.cfd.direct/openfoam/user-guide-v14/fvschemes)
  describes transient `ddtSchemes`, including the implicit Euler and backward
  options used by the staged plan.
- [OpenFOAM v14: solution and algorithm control](https://doc.cfd.direct/openfoam/user-guide-v14/fvsolution)
  is the reference for PIMPLE/linear-solver controls; this contract does not
  alter those numerical settings.
- [OpenFOAM v14: parallel execution](https://doc.cfd.direct/openfoam/user-guide-v14/running-applications-parallel)
  explains decomposition and reconstruction, which is why a stage checkpoint
  records whether it is direct/reconstructed or processor-common.

## Operational rules

1. Every phase owns immutable `decompose`, solver and `reconstruct` logs. A
   setup failure reports the phase-specific setup log and `solver_started=false`.
2. `return code 0 + End + target time + valid checkpoint` completes a phase.
   The ordinary `sigFpe : Enabling ... FOAM_SIGFPE` banner is explicitly
   ignored; a fatal floating-point trace remains an error.
3. Each transition closes the solver, validates the decimal target time and
   common processor fields, records the checkpoint, rewrites the next phase
   and starts it with `startFrom latestTime`.
4. The canonical case manifest is primary evidence. Registry synchronisation
   failures are recorded separately without replacing the original error.
5. The UI exposes single and sequential execution only. Internal start modes
   and phase cursor details remain audit data, not routine controls.
6. The RANS checkpoint's actual `polyMesh` and field sizes are canonical;
   registry hashes are recalculated evidence and never license mixed meshes.

## Bounded verification (2026-08-13)

- A->B: closed-coarse temporary copy, two MPI ranks, 25 Euler A steps followed
  by three Euler B steps with the expected `deltaT` change and continuous time.
- C->D: three complete target-step Euler states followed by two `backward`
  steps; the three-state history and fields were common to both processors.
- Open-medium diagnostic: five Euler steps at phase-A `2.5e-7 s` for target
  `1e-6 s`, with maximum Courant about 0.70. This does not establish production
  stability or physical convergence.

All cases were outside canonical run roots and deleted after the checks; compact
JSON reports preserve commands, times, return codes and numerical evidence.
