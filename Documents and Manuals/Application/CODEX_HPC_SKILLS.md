# Codex HPC Skills

Installed from: https://github.com/SciMate-AI/HPC-Skills.git  
Branch: `main`  
Source commit: `4ea8069188d931875121cdd418d7a26e60f47152`  
Vendor clone: `C:\Users\alejm\.codex\vendor\HPC-Skills`  
Install root: `C:\Users\alejm\.codex\skills`

Installed project skills:

- `hpc-gmsh`
- `hpc-openfoam`
- `hpc-paraview`
- `hpc-orchestration`
- `hpc-mpi`
- `hpc-foundations`
- `hpc-toolchains`

The project routing policy is in `AGENTS.md`. Future tasks must load only the
minimum relevant skills and references. GPU, Spack, SU2 and unrelated solver
skills are intentionally not installed.

To review a future upstream update, update the vendor clone with a
fast-forward-only pull, compare each selected skill directory against the
installed copy, preserve any local modifications, and reinstall only after
recording the new commit SHA. Never overwrite a locally modified skill without
an explicit conflict review.
