# LS(1)-0417 validation case

This preset prepares the Figure 10 validation polar from Ghoreyshi et al. at Mach 0.15 and Reynolds 1.9e6.

The paper reports a 1 m computational chord, a circular domain extending 50 chords, SA turbulence, second-order time integration, three Newton subiterations, 25,000 time steps of 2.5e-4 s and averaging of the final 10,000 samples. At Mach 0.15 this gives `deltaT*=U*dt/c=0.01276096076` and `endTime*=319.0240189`.

The validation-only geometry `reference_uncut_validation_1m` has exactly the paper's 1 m chord. The published preset therefore uses the nominal `2.5e-4 s` step and `6.25 s` duration directly. `backward` supplies second-order implicit time integration, three PIMPLE outer correctors are the nearest available workflow mapping, and `average_from_fraction=0.6` selects the final 10,000 of 25,000 samples. PIMPLE correctors are not mathematically identical to the paper's Newton subiterations.

At `T=288.15 K`, `mu=1.7894e-5 Pa s`, `c=1 m` and `M=0.15`, satisfying `Re=1.9e6` requires `U=51.04384 m/s`, `rho=0.6660666 kg/m3` and ideal-gas `p=55.093 kPa`. Literal ISA sea-level density would instead give approximately `Re=3.49e6`; the preset preserves Mach/Re similarity and records that thermodynamic inconsistency explicitly.

The current `incompressibleFluid` solver is a low-Mach baseline. It should be compared with the paper, but it must not be described as an exact reproduction of the compressible Cobalt/Kestrel methods. The full validation run is intentionally long; use the separate bounded smoke-test controls only to verify software operation.

Load the preset explicitly from the Case CFD page. The application backs up active configuration files before replacing them.
