# OpenFOAM execution and performance audit

Date: 2026-09-07

## Scope and invariants

This audit covers the current RamAir 2D RANS/SIMPLE and URANS/PIMPLE execution
path. Meshes, boundary conditions, turbulence model, discretisation, residual
requirements and aerodynamic acceptance criteria were not changed. Scratch
benchmarks used copies of an already advanced solution and changed only MPI
rank count, decomposition method or field-output format/frequency.

## Installed stack

- OpenFOAM Foundation 14, build `14-b4f91ad8bbba`, using `foamRun -solver
  incompressibleFluid`.
- Open MPI 4.1.2. The runner binds one MPI rank per physical core.
- AMD Ryzen 7 4800H: 8 physical cores, 16 logical CPUs.
- 7.5 GiB RAM plus 2 GiB swap.
- Cases run on native WSL2 ext4 storage, not `/mnt/*`.
- Current decomposition is Scotch; current field output is compressed ASCII
  with uncollated parallel I/O.
- `foamLog`, `decomposePar`, `reconstructPar`, `redistributePar` and
  `renumberMesh` are installed.
- OpenCFD-v2512 gradient-cache and MPI-IO backend changes, v2606
  `agglomerationInfo`, `parProfiling`, `solverInfo` and `timeInfo` are not
  exposed by this Foundation-14 installation and are not enabled.

OpenFOAM 14 documents decomposition, parallel execution and reconstruction in
its [parallel-running guide](https://doc.cfd.direct/openfoam/user-guide-v14/running-applications-parallel)
and the available utilities in the [standard-utilities guide](https://doc.cfd.direct/openfoam/user-guide-v14/standard-utilities).
The OpenCFD-v2512 [MPI-IO](https://www.openfoam.com/news/main-news/openfoam-v2512/parallel)
and [gradient-cache](https://www.openfoam.com/news/main-news/openfoam-v2512/numerics)
features belong to another fork/release and cannot be assumed compatible.

## Monitoring implementation

Normal runs keep the solver log as the universal source. The incremental
monitor now reports:

- angle, topology, mesh level, mode and A-E phase;
- elapsed ClockTime, median/P95 seconds per iteration or timestep, steps/s,
  cells/rank, cell-steps/s and core-seconds/step;
- URANS physical-time/wall-time and wall seconds per convective time;
- linear solver name plus mean/P95/max iterations for `p`, `U` and `nuTilda`;
- warnings for high pressure iterations and fewer than 50,000 cells/rank.

The monitor reads no volume fields and launches no ParaView processing. On the
real closed-coarse log, one cold update took 0.088 s and 50 warm incremental
updates had median 0.063 s and P95 0.078 s. At a 30 s refresh this is about
0.21% of one CPU core, below normal solver timing variability.

## Controlled strong scaling

Case: closed coarse, alpha=8 deg, 203,691 cells, phase-C checkpoint. Every run
started at 0.01625 s, used fixed `dt=0.000125 s` (observed max Co about 42.22),
advanced exactly 10 steps and wrote the full field only at the end. The first
solution state was identical and no benchmark reused hard-linked mesh files.

| MPI ranks | Cells/rank | Wall s/step | Speed-up | Efficiency | Core-s/step | Cell-steps/s |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 203,691 | 3.742 | 1.000 | 100.0% | 3.742 | 54,434 |
| 2 | 101,846 | 2.093 | 1.788 | 89.4% | 4.186 | 97,320 |
| 4 | 50,923 | 1.443 | 2.593 | 64.8% | 5.772 | 141,158 |
| 8 | 25,461 | 1.229 | 3.045 | 38.1% | 9.832 | 165,737 |

Final force coefficients and maximum Courant number were invariant to the
expected floating-point reduction order: max-Co relative spread was below
`5e-5`; final coefficient differences were of order `1e-5` or smaller.

Conclusions:

- Lowest single-case latency: 8 ranks, but only 38.1% parallel efficiency.
- Best practical balance: 2 ranks for this 204k-cell mesh.
- Best campaign throughput should use several concurrent 1-2 rank cases,
  capped to 8 physical cores and available RAM, rather than one 8-rank case.
- Four ranks remain useful when reducing latency matters more than CPU cost.
- The existing automatic target near 100k cells/rank is supported for this
  mesh. The optional empirical tuner is preferable when a compatible cached
  result exists.

The existing closed-fine URANS case (618,382 cells, 8 ranks, about 77,298
cells/rank) advanced with max Co about 14.63 and approximately 4 s per step in
its latest bounded segment. It confirms that the fine case is viable in
parallel but does not by itself establish an 8-rank optimum.

## Decomposition

At 4 ranks:

| Method | Solver wall time, 10 steps | Decomposition | Processor faces | Cell imbalance |
|---|---:|---:|---:|---:|
| Scotch | 14.43 s | 10.34 s | 384 | 0.30% |
| Hierarchical `(2 2 1)` | 13.43 s | 10.15 s | 745 | <0.01% |

Hierarchical was 6.9% faster in one short run but almost doubled interface
faces. This is not enough evidence to replace Scotch: retain Scotch as the
production default and expose hierarchical only as a repeatable benchmark
alternative. The `(2 2 1)` split correctly leaves the empty direction
undecomposed.

Decomposition alone costs about 10.3 s, so short continuation fragments should
avoid unnecessary redecomposition. Long production phases amortise this cost.

## I/O

At 4 ranks and identical numerics, compressed ASCII fields written every two
steps took 18.77 s for 10 steps. Compressed binary took 13.12 s. The minimal
one-final-write scaling fixture took 14.43 s. The binary result is promising,
but cache/order effects are material in such short tests; binary is therefore
conditionally recommended after repeated validation on a longer window.

Production policy should retain frequent lightweight residual, force and
Courant histories while keeping full-volume writes sparse. ParaView rendering
and animation remain post-run tasks. Foundation 14 supports selecting collated
I/O, but the OpenCFD-v2512 MPI-IO backend is not available; collated versus
uncollated needs a separate repeated storage benchmark before changing the
default. See the Foundation [parallel-I/O guide](https://openfoam.org/guides/parallel-io/).

The open-medium alpha=8 pilot also established a numerical feasibility limit,
not a performance preference. Even the smallest original low-cost Cummings
target (`1.22444e-5 s`) forced the adaptive lip-cell ramp down to about
`3.02e-7 s` to hold `Co` near 10. Fixed-dt open runs now pause recoverably and
report the achieved stable ceiling instead of entering `backward` with an
incompatible target. The open validation default uses `maxCo=5`; changing the
fixed convergence ladder requires either improving the limiting lip cells or
explicitly accepting the much smaller timestep and its cost.

## Solver bottleneck

For the strong-scaling URANS fixture, pressure required about 4.1-4.3 linear
iterations on average and P95 9-10. In the real validation alpha=12 deg URANS
history, pressure averaged 9.8 and reached P95 23, while velocity averaged
about 0.6 iterations and `nuTilda` about 1. Pressure/GAMG is therefore the
clearest solver-side cost centre.

No GAMG tolerances, smoothers or agglomeration settings were changed. Any such
change must be tested independently against residual history, Cl/Cd/Cm,
mean/RMS URANS forces and time to equivalent solution. `agglomerationInfo` is
not present in this installation.

## Implemented low-risk changes

1. The monitor parser records the actual linear solver and exposes robust
   timing and per-equation iteration statistics.
2. Validation and convergence monitors show alpha and receive the persisted
   effective MPI count, including historical runs without a local parallel
   plan.
3. Monitor refresh is active only while a real job is running.
4. The MPI tuner and production runner now use the same profile identity,
   separated by detected RANS/URANS numerics.
5. The tuner can include serial execution and records core cost and
   cell-throughput, rather than selecting from CPU utilisation alone.
6. Benchmark fixtures deep-copy `constant`; `renumberMesh` can no longer alter
   a validated source through hard links.
7. A benchmark is invalid unless the solver starts and records at least five
   physical steps.
8. Staged child phases no longer publish whole-campaign completion. Queue and
   monitor ownership persist across A-B-C-D-E transitions.

## Recommended operating policy

- Default automatic mode: retain the current approximately 100k cells/rank
  rule bounded to physical cores, then reuse a compatible empirical profile.
- Closed/open coarse near 200k cells: 2 ranks for normal campaigns; 4 for
  reduced latency; 8 only for an explicitly latency-critical run.
- Fine near 600k cells: start with 6 ranks from the current rule and run the
  tuner over 4/6/8 before a long campaign.
- Campaign throughput: at most 8 physical-core ranks in total, normally four
  concurrent 2-rank coarse cases. Do not oversubscribe the 7.5 GiB host.
- Keep Scotch in production. Benchmark hierarchical per mesh only when repeat
  results show a stable gain despite its larger interface.
- Keep lightweight monitoring enabled. Run scaling, I/O, decomposition,
  renumbering and solver experiments only in the explicit scratch tuner.
- Treat sustained timestep collapse, increasing pressure iteration P95,
  fewer than 50k cells/rank, high memory use and repeated decomposition as
  actionable performance warnings.

## Typical task cost observed on this host

These are measured operating ranges, not fixed estimates. URANS cost changes
with angle, Courant response and the number of pressure iterations.

| Task | Representative evidence | Typical cost/resource use | Main limiter |
|---|---|---|---|
| Validation URANS, closed 215k cells, balanced 2 ranks | real alpha=-2/4 histories, 800 samples | median 3-8 s/step; 6-16 core-s/step; about 108k cells/rank | pressure GAMG and angle-dependent nonlinear work |
| Validation URANS, closed 215k cells, latency run 8 ranks | real alpha=12 history, 533 samples | median 2 s/step but 16 core-s/step; only 27k cells/rank | MPI efficiency; faster case, poorer campaign throughput |
| Convergence URANS, closed coarse 204k cells, 2 ranks | phase-D history, 168 samples | median 6 s/step; P95 8 s/step; 12 core-s/step | five PIMPLE outer correctors and pressure solves |
| Convergence URANS, closed fine 618k cells, 8 ranks | bounded production segment | about 4 s/step and 77k cells/rank | memory bandwidth plus pressure; exact optimum still needs 4/6/8 repeat test |
| Fast validation postprocess | alpha=4 and alpha=12 stage timings | 63-68 s total; wall analysis 6 s, RANS products 24-27 s, final URANS images 17-25 s | ParaView startup/rendering, not coefficient parsing |
| Lightweight live monitor | 50 repeated parser updates | median 63 ms, P95 78 ms; about 0.21% of one core at 30 s refresh | negligible compared with solver variability |
| One parallel decomposition | Scotch on 204k cells | about 10.3 s | fixed startup/I/O cost; avoid repeating it for short continuations |
| One retained open-medium field snapshot | storage model, 303k cells | about 37.5 MB compressed; 24 retained states about 0.90 GB | field I/O and storage, not scalar histories |

The historical cached recommendation of six ranks for the 215k-cell validation
mesh came from only four measured steps and is now rejected automatically.
Without a representative compatible profile, balanced automatic mode selects
2 ranks for about 215k cells, 3 for about 303k and 6 for about 618k. This
matches the measured 100k-cells/rank operating region and leaves the user free
to select manual ranks for a latency-critical single case.

## Evidence locations

- Runtime measurements:
  `/home/alejm/ramair_cfd/DESIGN_APP/CFD_2D/performance_benchmarks/closed_coarse_fixed_20260907`
- Machine-readable strong-scaling result:
  `strong_scaling_summary.json` in that directory.
- Monitor overhead result: `monitor_overhead.json` in that directory.

## Further work

Repeat each strong-scaling and binary-I/O candidate at least three times over
30-50 post-warm-up steps before promoting a machine-specific cached profile.
Only if pressure remains dominant should a separate GAMG campaign compare
smoother/agglomeration options. NUMA and advanced MPI profiling are low
priority on this single-socket eight-core host; external profilers should stay
out of production runs.
