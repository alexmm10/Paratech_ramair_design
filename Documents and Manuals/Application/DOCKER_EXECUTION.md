# Docker execution (optional)

Docker is useful for reproducible headless OpenFOAM jobs and automated tests.
It is not the recommended host for CATIA V5, interactive WSLg viewers or the
normal Windows launcher.

## Build and enter

```bash
docker compose build
docker compose run --rm ramair-openfoam
```

The image installs OpenFOAM Foundation 14 from its official Ubuntu repository,
Gmsh 4.15.2 and the Python numerical/post-processing stack. The project folder
is mounted at `/workspace`, so generated cases remain on the host.

## Recommended split

- Windows + CATIA V5: CAD generation.
- Windows launcher + native WSL Ubuntu: interactive application and local CFD.
- Docker or remote Linux package: reproducible headless CFD and CI.

Do not place proprietary CATIA installations, experimental data or large CFD
results inside the image. Mount those at runtime and keep them outside Git.
