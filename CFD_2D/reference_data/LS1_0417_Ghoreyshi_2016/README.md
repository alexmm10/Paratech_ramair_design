# LS(1)-0417 validation reference

These CSV files digitize Figure 10 of Ghoreyshi et al. (2016) for Mach 0.15 and Reynolds 1.9e6.

The values are approximate pixel-derived data. The high-resolution review adds the visible experimental points at alpha = -10, -6 and 4 deg and Cl = 1.50 in the drag polar; these remain subject to the uncertainty in `reference_manifest.json`. They support visual and workflow validation, but they do not replace original tabulated wind-tunnel or CFD data.

`ramair_2d_validation.py` overlays only real RamAir results whose case metadata match the reference Reynolds and Mach tolerances. Missing simulations remain absent from the plots.
