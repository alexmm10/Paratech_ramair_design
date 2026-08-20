# LS(1)-0417 validation reference

These CSV files digitize Figure 10 of Ghoreyshi et al. (2016) for Mach 0.15 and Reynolds 1.9e6.

The values are approximate pixel-derived data. They support visual and workflow validation, but they do not replace original tabulated wind-tunnel or CFD data. `reference_manifest.json` records the source and estimated digitization uncertainty.

`ramair_2d_validation.py` overlays only real RamAir results whose case metadata match the reference Reynolds and Mach tolerances. Missing simulations remain absent from the plots.
