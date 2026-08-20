# Docker strategy

Decision: **native WSL/OpenFOAM 14 remains the production runtime; Docker is experimental**.

The existing Dockerfile and Compose definition are retained as a headless
prototype. Their static contract declares OpenFOAM 14, pins Gmsh 4.15.2,
mounts the project workspace from the host and excludes generated CFD data
from the build context.

Production promotion is blocked because the active WSL distribution has no
usable Docker server integration, the image currently runs as root, apt
dependencies are not immutably pinned, and container Open MPI compatibility
and scaling have not been measured. No image was built and no solver case was
executed for this audit.

A future promotion requires separate approval and a small serial/MPI
comparison against native WSL using identical numerics, host-mounted outputs
and checksum evidence. Until then, canonical meshes, RANS bases and results
remain on the host/WSL filesystem and never inside a container layer.

Reproduce the static/runtime audit with:

```text
python "Application Support/Tools/audit_container_strategy.py"
```
