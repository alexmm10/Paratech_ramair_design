# TAREA 05 — Mesh-science decision

Date: 2026-08-20  
Scope: bounded diagnostics and reviewed defaults only. No active mesh was
replaced, no Work Case approval was changed and no CFD solver was run.

## Decision

- Keep Frontal-Delaunay (`Mesh.Algorithm=6`) as the general/open baseline and
  retain the already measured closed presets that use Delaunay (`5`). Do not
  switch every topology from one algorithm to the other: compare them on a
  fixed fixture and on the exact production topology.
- Compute curvature-aware curve-node counts before writing `Transfinite Curve`.
  A transfinite constraint fixes the node count; curvature sizing must not be
  expected to override it afterwards.
- Use the paper sequence `y+=1, 2/3, 4/9, 8/27` for Coarse, Medium, Fine and
  Extra Fine. These are fractional targets, not the bands 2–3 and 4–9.
- Use 50 boundary-layer layers for Coarse/Medium/Fine and retain 75 only for
  Extra Fine comparison. Use growth 1.10; reject growth above 1.20.
- Keep the open cavity core coarse while preserving the existing local inlet,
  lip and internal trailing-edge transition controls.
- Select the first-cell height as the smallest positive result from the
  existing skin-friction estimate and the requested laminar/turbulent
  reference formulae. The exact candidates and selected source are written to
  every new mesh report.

## Reproducible bounded evidence

The versioned fixture runner is
`CFD_2D/scripts/ramair_2d_mesh_science.py`. It works in a temporary directory,
never touches active mesh folders and can optionally perform the real
`gmshToFoam`/`checkMesh` handoff.

On Gmsh 4.15.2 and OpenFOAM 14, the fixed extruded hybrid fixture produced:

| Algorithm | Cells | Max non-orthogonality | Max skewness | Result |
|---|---:|---:|---:|---|
| 5 — Delaunay | 5,392 | 32.0396° | 0.480240 | `Mesh OK` |
| 6 — Frontal-Delaunay | 5,378 | 28.1480° | 0.468268 | `Mesh OK` |

The four-arc curvature-only probe did not finish within its bounded window,
while the same curves with `Transfinite Curve ... = 9` completed and contained
exactly 32 boundary elements. This confirms the production ordering rule but
does **not** claim that protected Gmsh work item 2633 is resolved—or even that
the local timing behavior is the same issue. Its protected text was not
available, so the report records `NOT_CLAIMED_RESOLVED`.

The fixture comparison is deliberately small and is not aerodynamic evidence.
The existing project studies remain the topology-specific evidence for inlet
transition, lip refinement and accepted production candidates.

## Applied defaults and compatibility

`mesh_level_values()` now exposes the four reviewed levels. Applying a preset
only updates an editable draft; schema-3 revision approval and active-output
eligibility remain separate gates. Existing mesh files, saved revisions and
approvals are unchanged.

The builder accepts `extra_fine` and publishes
`first_cell_height_formula_audit` in `mesh_quality_report.json`. Existing
manual first-cell-height configurations remain available; the formula audit
only controls presets/configurations that explicitly enable y+-derived height.

## Rerun

From the synchronized WSL source, with OpenFOAM 14 active:

```bash
python3 CFD_2D/scripts/ramair_2d_mesh_science.py \
  --project-root . \
  --output /tmp/ramair_mesh_science_task05.json \
  --gmsh ~/.local/opt/gmsh-4.15.2/bin/gmsh \
  --run-fixtures --openfoam-check --timeout-s 120
```

Review the JSON before proposing any change to an active mesh. A production
replacement still requires its normal Gmsh, `gmshToFoam`, `checkMesh`, quality
review and explicit approval gates.
