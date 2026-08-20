# Profile Files

## XFOIL-generated ram-air cuts

The application page **Geometria > Diseno 2D del corte ram-air** creates new
open profiles here. It repanels a closed base profile with XFOIL, runs a
viscous alpha sweep at the selected Reynolds and Mach numbers, and locates the
leading-edge stagnation point only in converged Cp solutions.

Generated names include the base profile, standard/optimized selection,
Reynolds, Mach and measured inlet gap rounded to 0.1 percent chord, for example
`NASA_LS1_0417_Cut_Optimized_Re4000000_M0p1_Gap4p1pct.csv`.
`generated_profiles_registry.json` stores the matching metadata. Detailed
polars, Cp files, logs and the stagnation envelope remain under
`CFD_2D/CFD_2D_inputs/inlet_design/`; they are evidence, not selectable profile
geometry.

The legacy `2D Design` tree was preserved under
`Previous Versions/legacy_2d_design_20260717`. Its polar CSV files are not
copied here because they are aerodynamic results rather than coordinates.

All base airfoil/profile `.csv` and `.dat` files live here. Scripts should not read profile files from the project root or from `CATIA/Inputs/`.

Current files include:

- `LS1-0417_Cut_Standard_Re3000000.csv`
- `LS1-0417_Cut_Optimized_Re3000000.csv`
- `NASA LS1-0417.dat`
- `NASA_LS1_0417_clean_converted.csv`
- `ross_standard_8p4.csv`
- `ross_minimum_4p0.csv`

Recommended canonical names for future cleanup:

- `LS1_0417_closed.dat`
- `LS1_0417_standard.csv`
- `LS1_0417_optimized.csv`
- `Ross_LS1_0417_8p4_inlet.dat`
- `Ross_LS1_0417_4p0_inlet.dat`

Do not rename active files blindly while CATIA/CFD configs still reference the existing names. Update `Application Support/Configurations/default_case_config.json` first, then regenerate `CATIA/Inputs/` and `CFD_2D/CFD_2D_inputs/`.
