# RamAir CFD final integration report

Date: 2026-08-21

## Decision

The restructuring program is integrated through T17. Native WSL with
OpenFOAM 14 and CPU/Open MPI remains the supported execution path. Existing
meshes, executed cases, RANS bases, checkpoints and post-processing products
remain canonical and were not copied, replaced or deleted.

## Gates

| Gate | Result | Evidence and remaining boundary |
|---|---|---|
| A — Work Cases | PASS | Schema 3 identities, revisions, dependencies, compatibility, approval history and reversible metadata migration are implemented. |
| B — Mesh | PASS (bounded) | Closed/open generation, effective-configuration audit, quality gates and approval protection are implemented and fixture-tested. No active production mesh was substituted. |
| C — OpenFOAM | PASS (smoke) | Solver schema 15/API 26, OpenFOAM 14 dictionaries, RANS-to-URANS stages, restart controls, fixed validation timesteps and function objects passed bounded smoke/contract tests. |
| D — Runtime | PASS | Transactional lifecycle, leases, stop/resume, phase recovery and duplicate suppression are implemented. nOuter resume selected only incomplete n=4. |
| E — Post-process | PASS | Continuous critical signals, portable ParaView state, common scales, timestep history and auditable final-window selection are implemented. |
| F — Convergence | IMPLEMENTED; SCIENTIFIC EVIDENCE PENDING | Schema 11 campaigns, settling, >=10-cycle requirements, PSD/coherence, GCI and adaptive closed/open gates are implemented. No full CFD campaign was launched. nOuter 4 is still producing real phase-E evidence, so no final nOuter conclusion is declared. |

## Versions and safety state

- Work Case manifest schema: 3.
- Solver configuration schema: 15.
- Validation Lab schema: 11.
- Application/backend API: 26.
- Git branch: `main`; local author configured; no `origin` configured and no push performed.
- Docker: experimental only; no WSL integration or container MPI evidence.
- GPU: unavailable/not selected.

The final bounded regression ran 43 representative tests across Gates A-F and
the Git/Docker safeguards; all passed. The complete suite is deferred until
nOuter 4 releases the CPU.

## Recommended next action

Allow nOuter 4 to finish phase E, then run its evidence-based analysis and the
complete `CFD_2D/tests` regression while no solver is consuming the CPU. Keep
native WSL and the existing eight-rank bounded recommendation unless a later
production-sized benchmark demonstrates a better resource shape.
