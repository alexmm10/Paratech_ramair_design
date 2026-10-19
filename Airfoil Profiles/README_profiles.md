# Profile Files

All base airfoil/profile `.csv` and `.dat` files live here. Scripts should not read profile files from the project root or from `CATIA_inputs/`.

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

Do not rename active files blindly while CATIA/CFD configs still reference the existing names. Update `configs/default_case_config.json` first, then regenerate `CATIA_inputs/` and `CFD_2D/CFD_2D_inputs/`.
