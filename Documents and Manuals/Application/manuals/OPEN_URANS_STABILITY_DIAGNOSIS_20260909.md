# Open URANS stability diagnosis

Date: 2026-09-09

## Scope

This audit follows the real `open_medium_a08_dt1p224437662261em05` convergence
case through phases A-C and the phase-D Euler ramp. It combines solver logs,
the exact maximum-Courant cell, mesh-set membership, cell volume and the three
approved open-mesh quality summaries. It does not infer instability from a
global `checkMesh` pass alone.

## Exact failure mechanism

The run is not globally unstable during A-C. Phase C completed with maximum
Courant about 1.73 at `dt=1.2244e-6 s`. When phase D approaches the selected
fixed target (`1.2244e-5 s`), adaptive control reduces the step to
`3.0174e-7 s`; the final observed maximum Courant is 6.31.

`cellMax(Co)` locates the controlling cell unambiguously:

- cell ID: `268549`;
- centre: `(-0.0032362071, 0.0063336216, 0.005) m`;
- normalized position: `x/c=-0.00324`, `y/c=0.00633`;
- region: `airfoil_wall_external`, immediately outside the upper inlet lip;
- volume: `1.8657e-10 m3`, in the smallest 0.59% of the mesh and about 46
  times smaller than the median cell volume.

The domain-mean Courant is only about `1.43e-5`. The timestep is therefore
limited by a highly localized lip/outer-boundary-layer-to-triangle transition,
not by the bulk flow. Pressure GAMG shows repeated large first-outer-corrector
residuals and 20-30 iterations around this event, while `U` and `nuTilda`
remain much cheaper and continuity remains near machine zero. The immediate
numerical bottleneck is pressure/flux coupling across the local size and shape
transition; it is not a Spalart-Allmaras runaway.

## Mesh comparison

| Mesh | Cells | Max non-orthogonality | Max skewness | Min determinant | Min interpolation weight | Min volume ratio |
|---|---:|---:|---:|---:|---:|---:|
| Coarse | 223,080 | 41.46 deg | 0.672 | 0.0629 | 0.0915 | 0.1343 |
| Medium | 302,692 | 41.46 deg | 0.672 | 0.0629 | 0.0915 | 0.1343 |
| Fine | 502,474 | 42.11 deg | 0.682 | 0.1562 | 0.1076 | 0.1608 |

Coarse and medium inherit essentially the same limiting local patch; adding
cells elsewhere does not repair it. Fine materially improves determinant,
interpolation weight and volume ratio without sacrificing acceptable
non-orthogonality, skewness or aspect ratio. This supports a local topology
explanation rather than a global resolution explanation.

## Implemented safeguards

1. The phase-D open-only Euler ramp now uses adaptive `maxCo=5`.
2. The runner records the last adaptive step, a conservative fixed-step limit
   (`0.8*last_dt`) and the ratio to the requested target.
3. If the requested fixed step is not within 80% of the demonstrated safe
   value, execution stops recoverably with
   `OPEN_TARGET_DT_EXCEEDS_LOCAL_COURANT_LIMIT`; it no longer creates backward
   history using an incompatible step.
4. Canonical URANS runtime files and leases are now per case. Independent
   cases can run concurrently without replacing each other's active state or
   producing an apparent unexplained jump to another run.

For the observed state, a first estimate for `Co<=5` is about `2.4-2.5e-7 s`.
That is roughly 49 times smaller than the requested fixed step. Increasing
outer correctors, accepting `Co=50`, or changing from Euler to backward cannot
remove this geometric CFL restriction and would conceal rather than solve it.

## Recommended mesh action

The preferred repair is local and should be validated with `cellMax(Co)` after
each mesh change:

1. Inspect cell `268549` and adjacent cells in the upper inlet lip VTK set.
2. Tie the first external triangle size to the local outermost prism tangential
   spacing, not to a global wall minimum.
3. Reduce the first-triangle size locally and use at least two or three smooth
   radial transition bands before the ordinary near-field target.
4. Remove abrupt tangential spacing/curvature changes at the lip junction;
   preserve the imported wall geometry and modify only the BL segmentation.
5. Optimize primarily minimum determinant, interpolation weight and neighbour
   volume ratio in this patch. Do not trade them for a small global cell-count
   reduction.
6. Prefer the fine topology for the next bounded pilot because its three
   critical minima are already better. Re-run A-C and the adaptive ramp before
   admitting any fixed timestep to the temporal ladder.

If remeshing cannot raise the local safe step sufficiently, the temporal study
must include a much smaller fixed-`dt` ladder. The production configuration
should never declare the original Cummings target feasible merely because the
mesh passes global `checkMesh` thresholds.

## Evidence

- Machine-readable diagnostic:
  `CFD_2D/reports/open_medium_a08_dt_stability_diagnostic_20260908.json`
- Stage log:
  `phase_D_EULER_RAMP_005.log` under the canonical open-medium run
- OpenFOAM 14 numerical guidance: application manual section on PIMPLE,
  Courant control and mesh non-orthogonal correction
- Project mesh audits: quality summaries for open coarse, medium and fine
