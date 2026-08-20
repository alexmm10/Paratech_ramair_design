# RamAir: Design and CFD Python application

## Geometry and 2D inlet design

The Geometry page starts with an XFOIL-backed inlet designer. Choose a closed
base profile, use the current CFD Reynolds/Mach or enter another design
condition, select a complete alpha envelope or an optimized CL window, then
generate. The result is not offered as `main_profile` unless XFOIL produced a
real repanelled profile, converged polar rows, valid Cp files and at least two
accepted stagnation locations.

XFLR5 is optional for manual comparison. The app controls XFOIL directly so
that every command, convergence result and cut decision is reproducible.

## Flujo de uso

La pagina `Estado` resume el orden de trabajo y verifica el entorno. Cada
editor guarda un JSON concreto y los scripts lo releen al comenzar su etapa;
cambiar una pestaña sin pulsar `Guardar` no modifica el caso. Los trabajos de
preproceso, mallado y solver se lanzan en segundo plano y dejan comando, PID,
estado y log persistentes.

El arranque desde Windows sincroniza siempre el conjunto interfaz/backend en la
copia WSL nativa. Las configuraciones editables y los productos generados no se
sincronizan desde Windows y por tanto no se reinician al volver a abrir la app.
La biblioteca pesada `Results` se conserva en
`~/ramair_cfd/DESIGN_APP/Results`; el directorio Windows `Results` contiene un
acceso directo y un TXT con la ruta UNC visible desde el Explorador. Al cargar
una etapa guardada, la app restaura sus JSON, invalida los widgets anteriores y
muestra inmediatamente los valores recuperados para poder modificarlos.

### Cargar, modificar y versionar un caso de trabajo

1. Selecciona el caso en `Caso guardado en Results` de la barra lateral.
2. Pulsa `Cargar caso de trabajo completo`. La opcion `Reemplazar workspace
   temporal` evita crear copias pesadas; `Archivar workspace temporal` conserva
   una copia explicita.
3. Geometria, caso CFD, malla y solver se restauran juntos. El solver se aplica
   al final para que ningun paquete anterior oculte su configuracion.
4. La app invalida los widgets anteriores y muestra inmediatamente los valores
   recuperados. Para una malla restaurada, usa `Configuracion editable activa`; no selecciones
   el preset si quieres conservar exactamente la base cargada.
5. Edita y pulsa `Guardar` en cada pagina. El JSON activo y el paquete del caso
   se actualizan atomica y simultaneamente, por lo que esos valores vuelven a
   cargarse en la siguiente apertura.
6. Regenera sobre el mismo paquete o guarda una nueva variante para comparar.
   Las cargas aisladas de `geometry`, `case`, `mesh`, `simulation` o
   `postprocess` permanecen en `Carga avanzada por etapa`.

La procedencia activa se muestra en la barra lateral y queda registrada en
`CFD_2D/app_state/active_workspace.json`. Abrir Gmsh, los informes y los VTK de
`checkMesh` usa la salida activa, por lo que una malla cargada se puede revisar
antes de remallarla.

## Estrategias del inlet abierto

`open_inlet_boundary_layer_mode` ofrece tres topologias comparables:

- `full_prismatic_bridge_without_fans`: modo por defecto. Continua la BL sobre
  un puente geometrico no fisico entre los labios sin forzar fans radiales. El
  puente no es `wall`, no es un patch y no bloquea la comunicacion exterior-cavidad.
- `full_prismatic_bridge_with_fans`: comparacion explicita para labios cuya
  geometria requiera sectores radiales.
- `triangular_inlet_no_bl`: termina la BL en los labios y comienza el inlet con
  triangulos refinados. Se conserva como alternativa de diagnostico.

`open_inlet_transition_elements=graded_quads` controla otra zona distinta: la
estrecha superficie fluida entre la interfaz exterior y la cavidad. El numero
de filas normal se calcula desde `y1`, espesor y crecimiento maximo 1.20; los
quads comienzan a escala `y1` y crecen hacia la cavidad triangular.
`open_inlet_marker_bump_strength=0.60` concentra los 144 nodos tangenciales en
ambos labios. Los triangulos graduados alternos se conservan como diagnostico:
en la prueba real redujeron pesos bajos pero introdujeron no ortogonalidad
cercana a 90 grados.

Cuando `checkMesh` falla, la app muestra el extremo, umbral, region estimada y
una muestra de IDs. Los sets completos quedan en
`CFD_2D/meshes/<variant>/checkMesh_problem_sets/` y sus superficies VTK en
`checkMesh_problem_locations/`.

## 1. Purpose

`run_ramair_cfd2d_app.py` opens a single graphical control surface for the
existing ram-air CAD/CFD workflow. The application does not replace or remove
the stage scripts. It validates and edits their JSON inputs, launches each
stage, records the exact command and displays the generated reports, images and
logs.

The application covers:

1. environment verification;
2. profile and canopy preprocessor;
3. CATIA input generation, without launching CATIA;
4. CFD 2D case-package generation;
5. Gmsh geometry, mesh and remeshing;
6. `gmshToFoam` and `checkMesh`;
7. OpenFOAM case writing;
8. dry-run and explicit solver execution;
9. partial-result preservation after timeout or requested stop;
10. coefficients, residuals, OpenFOAM function objects, VTK and ParaView.
11. optional statistically stationary force-coefficient stopping.

## 2. Supported Windows/WSL execution environment

### Recommended and supported

Use Windows 10/11 for VSCode and the browser, with Ubuntu 22.04 under WSL2 as
the execution backend. Keep the active CFD checkout on the Linux filesystem,
for example:

```text
$HOME/ramair_cfd/DESIGN_APP
```

This arrangement provides one browser interface while Gmsh, PyFoam,
`gmshToFoam`, `checkMesh`, OpenFOAM and ParaView all see Linux paths and the
same OpenFOAM environment.

### Why native Windows alone is not the project backend

The Gmsh Python wheel can run on Windows. The OpenFOAM Foundation distribution
used by this project is installed and supported on Windows through WSL2, not as
a native Windows solver toolchain. PyFoam is a Python controller for OpenFOAM;
installing PyFoam on Windows does not provide `foamRun`, `gmshToFoam`,
`checkMesh` or OpenFOAM libraries. Moving only the GUI to native Windows would
therefore split paths and environments without eliminating Linux.

### VSCode

Open the native WSL checkout with the VSCode WSL extension. The same source is
then used by the editor, Streamlit, Gmsh and OpenFOAM. Avoid running production
meshes from `/mnt/c`, OneDrive or a Windows network path.

## 3. Installation

### First installation from Windows

From PowerShell in the Windows project checkout:

```powershell
python .\run_ramair_cfd2d_app.py --install
```

The launcher expects the native WSL checkout at
`~/ramair_cfd/DESIGN_APP`. Override it when required:

```powershell
python .\run_ramair_cfd2d_app.py --install --wsl-project-root ~/another/path/DESIGN_APP
```

The launcher starts Streamlit in WSL and opens `http://localhost:8501` in the
Windows browser.

For normal daily use, double-click `START_RAMAIR_CFD2D_APP.bat`. Use
`INSTALL_AND_START_RAMAIR_CFD2D_APP.bat` only for the first setup or an explicit
dependency refresh. The Windows checkout owns code; the native WSL project owns
active editable configurations and generated meshes/cases/results. A locked,
transactional synchronization updates runtime code and records both locations
in `CFD_2D/app_state/runtime_sync_manifest.json`.

### First installation inside WSL

```bash
cd "$HOME/ramair_cfd/DESIGN_APP"
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install --install-system
.venv-cfd2d-ui/bin/python -m streamlit run CFD_2D/app/ramair_cfd2d_app.py
```

The equivalent short launcher is:

```bash
bash "Documents and Manuals/Application/run_cfd2d_app_wsl.sh" 8501
```

The bootstrap creates an isolated `.venv-cfd2d-ui`; it deliberately does not
use `--system-site-packages`. A legacy environment that contains that option is
rebuilt in place so the Ubuntu `mpl_toolkits` package cannot be mixed with the
pinned Matplotlib wheel. It does not upgrade `pip`, avoiding the slow
uninstall/reinstall path seen in the old debug command file. It installs the
pinned application dependencies:

```text
streamlit==1.58.0
pyarrow==18.1.0
gmsh==4.15.2
PyFoam==2026.6
numpy==1.26.4
pandas==2.2.3
matplotlib==3.8.4
numexpr==2.10.2
bottleneck==1.4.2
pytest==8.4.1
```

The launcher asks before enabling `--install-system`. When accepted, the
bootstrap can invoke `sudo apt` for available Ubuntu dependencies. OpenFOAM
Foundation 14 is the current reference and is installed only if its apt
repository is already configured; OpenFOAM 13 remains a compatibility
fallback. A user-local `~/.local/opt/openfoam14` installation is also detected.
Otherwise the official repository action is printed. Its final import check includes
`mpl_toolkits.mplot3d.Axes3D`; a failure is treated as an environment error,
not hidden as a plotting warning. Streamlit is deliberately started without
sourcing OpenFOAM globally: each CFD subprocess obtains an isolated environment
through `openfoam_environment.py`, including a nounset-safe `ZSH_NAME`.

The interface deliberately renders diagnostic records as escaped HTML tables
instead of `st.dataframe`. PyArrow 25.0.0 produced repeatable native crashes in
`libarrow.so.2500` on the tested WSL2 kernel; the isolated environment pins
PyArrow 18.1.0 as an additional safeguard. Top-level workflow pages and large
configuration families use lazy segmented navigation, so saving one parameter
does not rebuild every CATIA, mesh, result and history view.

### Read-only environment check

```bash
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --check
.venv-cfd2d-ui/bin/python CFD_2D/scripts/check_environment.py
```

From Windows:

```powershell
python .\run_ramair_cfd2d_app.py --check-only
```

The checker and every Python-launched CFD stage load the newest available
`/opt/openfoam*/etc/bashrc` themselves. Streamlit, PyFoam and direct Python
calls therefore do not depend on `.bashrc` having been sourced in the terminal.
`RAMAIR_OPENFOAM_BASHRC` can select an explicit installation.
The environment merge explicitly keeps the active venv `bin` directory before
the OpenFOAM PATH, so `pyFoamPlotWatcher.py` remains available to both the
checker and solver jobs.

## 4. Application layout

### Estado

Displays Python, WSL, Python packages, Gmsh, OpenFOAM utilities, MPI and
ParaView. The check is read-only. The workflow JSON can be edited here.

### Geometria

Edits `Application Support/Configurations/default_case_config.json` by logical section and
`Application Support/Configurations/ramair_catia_system_config.json` in separate suspension, line,
payload, stabilizer and output tabs. The canopy, rib/cell transformations,
airfoil/TE processing, crossports, fabric, CATIA generation and CATIA export
switches shown in the interface map to real preprocessor options. Running the
preprocessor calls:

```bash
python preprocess_ramair_main.py --config "Application Support/Configurations/default_case_config.json"
```

The stage writes `CATIA/Inputs/` and `CFD_2D/CFD_2D_inputs/`. It does not launch
CATIA V5. The system configuration is copied to `CATIA/Inputs/` as a provenance
snapshot; existing CATScript operation remains separate and unchanged.

### Caso CFD

Selects profile variant, alpha range, Reynolds, Mach, density, viscosity and
velocity policy. It calls `ramair_2d_profile_case_builder.py` and shows the
geometry manifest and mesh-input contract.

### Malla

All keys from `cfd2d_mesh_config.json` remain editable. They are grouped as:

- global/Gmsh and extrusion;
- closed-profile geometry, BL, trailing edge and size fields;
- open-profile BL, geometry/inlet, tangential discretization, trailing edge,
  cavity and exterior transitions.

Saving is atomic and creates a timestamped copy under
`Previous Versions/config_backups/`. Unknown future keys are preserved.

The execution controls select:

- Gmsh backend: `python_api`, `cli` or `auto`;
- domain and mesh level;
- threads and strict timeout;
- previous output action: archive, delete or keep;
- 2D-only or one-cell OpenFOAM extrusion;
- optional `gmshToFoam` and `checkMesh`.

`python_api` calls `gmsh.initialize`, sets `General.NumThreads` and the 1D/2D/3D
thread limits, opens the generated `.geo`, calls `model.mesh.generate` and
writes MSH 2.2. It runs in a worker process so the existing timeout and failure
reports remain effective. `cli` preserves the previously validated Gmsh 4.15.2
command. `auto` prefers the API only when it is importable.

No backend silently reuses an old `.msh`. The selected previous-output action
is applied before generation and freshness is still checked by the builder.
Selecting `delete` removes the active variant mesh directory directly and does
not create a backup under `Previous Versions/`; `archive` is the explicit
space-consuming preservation mode.

For the open profile, the validated topology is one connected fluid surface
around a finite-thickness fabric hole. The inlet line is embedded only as a
triangle-size marker: it is not a wall, patch or BoundaryLayer curve. The
exterior BL terminates at the two lips with Gmsh `PointsList`; requesting a
one-edge fan there is rejected because `FanPointsList` requires BL curves that
actually meet around a corner. Experimental early trimming remains available
under the advanced lip-termination expander, but is disabled by default.

When `checkMesh` fails, the builder reruns it with `-writeSets` and
`-writeSurfaces`. It stores `skewFaces`, `lowWeightFaces`, `lowVolRatioFaces`
and `underdeterminedCells` as VTK and writes
`checkMesh_problem_locations.json/.txt` with entity count, bounds, centroid,
normalized chord location, reported extreme and OpenFOAM threshold. This
separates a local lip transition defect from a global farfield or TE problem.
The generated `mesh_final.msh` can be opened with native Linux/WSLg Gmsh
4.15.2 or, preferably when WSLg is unreliable, the Windows Python Gmsh 4.15.2
API. Windows mode converts the path with `wslpath`, opens the UNC file through
`gmsh.open()` and keeps its own viewer log; it does not use a file association
or terminal copy mode.

The optional short optimizer generates 2--5 real candidates, runs
`gmshToFoam` and `checkMesh` for each, and keeps only the best mesh. It varies
tangential nodes, Bump coefficient and TE-cap nodes; manual `y1` is varied only
when explicitly enabled. Selection also includes low triangle angle, aspect
ratio and cell cost. Rejected candidate meshes are deleted after the JSON/CSV
comparison report is written.

### Caso OpenFOAM

Edits `cfd2d_solver_config.json` and writes the selected alpha case with
`ramair_2d_openfoam_case_writer.py`. By default it requires a real converted
`constant/polyMesh` and never creates an empty placeholder.

### Ejecucion

Dry-run remains the default and writes the exact run script. A real run needs
the visible confirmation checkbox and the explicit run button.

Backends:

- `native`: the existing shell execution path;
- `pyfoam`: `PyFoam.Execution.BasicRunner` executes and monitors OpenFOAM,
  records PyFoam state/history and copies its solver log to the canonical
  `log.<solver>` expected by the existing postprocessor.

The parent runner still owns `checkMesh`, timeout, clean `stopAt`, process-group
termination and partial-output preservation. PyFoam does not bypass these
safeguards. If a timeout occurs before PyFoam copies its canonical log, runner
and postprocessor read the progressive `PyFoamRunner*` log directly.

For a controlled partial run, set `stop_after_min` and use `writeNow`. The
runner edits `controlDict` with a backup, waits for a clean write, and keeps the
time directories and force coefficients for postprocessing.

The page also supports three explicit extensions:

- **Resume** continues from the latest reconstructed time and can extend the
  run by a selected convective duration `t*=tU/c`.
- **Steady initialization** runs `steadyState`/SIMPLE first, requires residual
  and percentage Cl/Cd/Cm plateau checks, archives its iteration history, then
  transfers U/p/turbulence fields into transient Euler/PIMPLE. It never
  relabels steady iterations as physical time. If the checks are not met, the
  page shows the residual/force metrics and offers three explicit choices:
  extend SIMPLE, start transient from the current fields, or finish.
- **Sweep** runs already-written alpha folders sequentially. Each angle can
  stop on statistically stationary Cl/Cd/Cm or its own timeout, preserve
  partial data and optionally postprocess before moving to the next case.

PyFoam live/replay windows show residuals, linear iterations and Cl/Cd/Cm.
Continuity, Courant, execution-time and deltaT watcher plots are deliberately
excluded; the postprocessor can still create the independent Courant history.
The coefficient viewer omits startup samples and uses a recent replay window so
large initial values do not dominate the automatic axis range. Raw force data
is preserved.

Before a solver run, `resume` continues active output, `delete` removes only
generated active times/postProcessing/processor/log files, and `stop` refuses
to proceed if such output exists. Explicit delete never creates a Previous
Versions backup and never modifies a package already saved in `Results/`.

### Postproceso

The interface exposes:

- stabilized averaging fraction;
- OpenFOAM `yPlus`, `wallShearStress` and vorticity function objects;
- coefficients-only, latest-time VTK or all-times VTK export;
- results folder and optional ParaView launch.
- wall `y+` versus `x/c` with separate upper/lower curves and target line;
- wall-normal velocity samples at editable `x/c` stations;
- numerical delta99 versus the turbulent flat-plate estimate and total prism
  stack height.

`average_from_fraction = 0.6` means that the first 60% of the available
coefficient samples is discarded and the final 40% is used for the reported
mean. It is a sampling choice, not a turbulence or time-integration parameter.
Only this reduced averaging-window coefficient plot is generated; the former
full startup-history image is not recreated.

### Archivos y logs

Every UI job receives an ID, persistent JSON status and a log under:

```text
CFD_2D/app_state/jobs/
logs/cfd2d_app/
```

Only one mutating workflow job can run at a time. For a solver job the stop
button edits `controlDict` to `stopAt writeNow`; MPI exits after writing and
PyFoam reconstructs the partial time. SIGINT is reserved for non-solver jobs.
After verified reconstruction, `processorN/` folders are deleted by default to
avoid storing duplicate partitions; disable the execution-page toggle only for
decomposition diagnostics.

## 5. Configuration ownership

The interface does not invent a second configuration source:

| File | Owner |
|---|---|
| `Application Support/Configurations/default_case_config.json` | preprocessor, profiles, canopy and exports |
| `Application Support/Configurations/ramair_catia_system_config.json` | suspension, lines, payload, stabilizers and optional CAD outputs |
| `cfd2d_workflow_config.json` | selected variant and stage-level policy |
| `cfd2d_mesh_config.json` | Gmsh geometry, BL, sizes, domain and extrusion |
| `cfd2d_physical_defaults.json` | derived physical operating point |
| `cfd2d_solver_config.json` | complete schema-10 common solver, open-cavity overrides, numerics, time step, Courant, turbulence, writes and postprocess controls |

The UI reloads the files on every Streamlit rerun. A saved change is therefore
the value read by the next stage; it is not held in a hidden session-only copy.
When a Results work case is active, the same save also updates its matching
package. New cases start from `topology_solver_v10`; complete restore applies
that solver package after geometry, CFD case and mesh.

## 6. Output continuity

Existing output locations are unchanged:

```text
CATIA/Inputs/
CATIA/Exports/
CFD_2D/CFD_2D_inputs/
CFD_2D/meshes/<variant>/
CFD_2D/openfoam_cases/<variant>/<alpha>/
CFD_2D/results/<variant>/<alpha>/
reports/
logs/
Previous Versions/
```

The UI reads current mesh-quality JSON, previews, case descriptions,
`run_status.json`, postprocessed plots and result summaries directly from these
folders. Postprocessing includes `deltaT_history.png` and the ParaView
`Courant_hotspots_<stage>_final.png`, which hides cells below 70 percent of the
actual maximum `Co` to expose the time-step-limiting region.

## 7. Portable use on another computer

1. Install Windows 10/11, WSL2 and Ubuntu 22.04, or use native Ubuntu.
2. Install OpenFOAM Foundation and verify `foamRun`, `gmshToFoam` and
   `checkMesh` after sourcing its `etc/bashrc`.
3. Place the project on the Linux filesystem.
4. Run `bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install`.
5. Run `python run_ramair_cfd2d_app.py` from Windows, or start Streamlit inside
   Linux.
6. Use the Estado page before meshing or running a solver.

CATIA V5 remains a Windows-only external CAD stage. The Python application
generates and inspects its inputs but intentionally does not automate a CATIA
launch.

## 8. Troubleshooting

### Streamlit is missing

```bash
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install
```

### Gmsh Python API is missing but CLI works

Select `cli` in the Malla page, or reinstall the pinned application
environment. `auto` reports the selected backend in the mesh-quality report.

### Gmsh enters copy mode or closes instead of showing the mesh

Do not open the `.msh` through a Windows association from the WSL terminal.
Use **Abrir mesh_final.msh en Gmsh** in the Malla page, or run the validated
Linux binary directly:

```bash
~/.local/opt/gmsh-4.15.2/bin/gmsh --version
~/.local/opt/gmsh-4.15.2/bin/gmsh CFD_2D/meshes/reference_uncut/mesh_final.msh
```

The UI records executable, version, mesh path and return code in
`logs/cfd2d_app/gmsh_viewer.log`. WSLg must provide `DISPLAY`,
`WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`.

### Matplotlib warns that Axes3D cannot be imported

Rebuild the generated UI environment:

```bash
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install
```

The environment must report `include-system-site-packages = false` in
`.venv-cfd2d-ui/pyvenv.cfg`. Mixing Ubuntu's Matplotlib 3.5 toolkit with the
application's Matplotlib 3.8 wheel is the cause of this warning.

### OpenFOAM commands are missing in the application

Verify the relevant OpenFOAM `etc/bashrc`. The launchers source the first
matching `/opt/openfoam*/etc/bashrc`; a nonstandard installation can be sourced
before starting Streamlit.

### Streamlit keeps loading and then disconnects after saving

Check for a native Arrow crash:

```bash
dmesg -T | grep -Ei 'libarrow|segfault|signal 11' | tail
```

Repair only the isolated UI environment and restart the launcher:

```bash
cd "$HOME/ramair_cfd/DESIGN_APP"
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install
bash "Documents and Manuals/Application/run_cfd2d_app_wsl.sh" 8501
```

The launcher disables Streamlit's source watcher because editable JSON files
are runtime data, not Python source. Configuration writes remain atomic and
create backups under `Previous Versions/config_backups/`.

### PyFoam run fails immediately

Run:

```bash
.venv-cfd2d-ui/bin/python -c "import PyFoam; print(PyFoam.__path__)"
.venv-cfd2d-ui/bin/pyFoamVersion.py
```

Then inspect `pyfoam_run_report.json`, `PyFoamRunner.*.logfile`,
`log.<solver>` and `run_status.json` in the OpenFOAM case.

The execution page embeds the RamAir live monitor while PyFoam runs the case.
It reads the authoritative PyFoam/OpenFOAM log and force files, renders
residuals, Cl and combined Cd/Cm into a headless Matplotlib snapshot,
and Streamlit refreshes that image approximately every 90 seconds. No external graph window
is expected. This avoids the blank Gnuplot/FIFO copy-mode windows observed
under WSLg. Continuity, Courant
number, `deltaT` and execution time remain in the technical solver log without
opening extra windows. Cl uses `[-0.8, 2]` and Cd/Cm use `[-0.2, 0.2]`; raw
force data is never clipped. The same selected plots are replayed to PNG after
the run. The persistent set contains exactly `linear_residuals.png`,
`lift_coefficient.png` and `drag_moment_coefficients.png`; the live window is not
copied as a duplicate image. Six MPI processes were validated on the Ryzen 7 4800H; the runner rewrites
`numberOfSubdomains=6` before `decomposePar -force`.

For a fresh SIMPLE initialization, the staged runner applies `potentialFoam`,
then conservative Spalart-Allmaras numerics. A finite but unconverged stage can
be extended or transferred explicitly. A detected `nuTilda`/continuity
runaway is archived as `STEADY_STAGE_DIVERGED`; its fields are not reused for
the transient stage and the original transient `0/` is restored.

### ParaView does not open the case

Run postprocess once. It creates `<case>.foam` and a deterministic ParaView
startup script with absolute paths. The application launches ParaView with:

```bash
paraview --disable-registry --script=/absolute/path/to/postProcessing/ParaView/open_case.py
```

The script selects `internalMesh`, reads the actual OpenFOAM time list,
advances to the latest retained state,
colours velocity magnitude, resets the camera and writes a screenshot and
`.pvsm` state. The native reader exposes every retained time and field.
`latest_vtk` is a portable copy of the last time only; choose all-times VTK
deliberately because it can consume much more storage.

For parallel PyFoam runs, normal completion, timeout and clean user stops all
execute `reconstructPar` for every retained write interval. The previous
`-latestTime` fallback exposed only one ParaView frame and has been removed.
With `purgeWrite=24`, the temporal sequence is bounded before redundant
`processorN` directories are deleted. The postprocess summary reports whether
at least two positive reconstructed times are available for animation.

### LS(1)-0417 validation case

The normal entry point is the complete work case
`Results/LS1_0417_validation_M0p15_Re1p9e6`, not the immutable technical
preset alone. In the sidebar select that work case and click **Load geometry +
CFD case + mesh**. This restores `reference_uncut_validation_1m_geometry`, the
`M0p15_Re1p9e6_polar` operating package and the approved reference mesh.

The preset defines `M=0.15`, `Re=1.9e6`, alpha `-10:2:20`, second-order
`backward` time integration, three PIMPLE outer correctors and
`dt*=0.01276096076`. The validation-only profile is scaled to the paper's 1 m
chord, so its nominal step is `dt=2.5e-4 s`. With fixed `T=288.15 K` and
`mu=1.7894e-5 Pa s`, matching both Mach and Reynolds requires
`rho=0.6660666 kg/m3` and `p=55.093 kPa`; this is a similarity condition, not
literal sea-level density. **Load LS(1)-0417 validation preset** in **Caso CFD** remains a
recovery action when the complete Results work case has not yet been created.

Choose the smoke `t*=0.2` preset for a first end-to-end software test, the
2,500-step preliminary preset for trends, or the 25,000-step preset only for
the published duration. The current 334,857-cell, six-rank bounded measurement
projects approximately 14 minutes, 36.3 hours and 363 hours respectively. The
short measurement is conservative and none of these presets replaces
convergence, mesh or time-step independence checks.

For a new angle:

1. Keep this work case selected and load its complete workspace.
2. In **Caso OpenFOAM**, select/write that alpha; reuse the loaded mesh.
3. In **Ejecucion**, select the same alpha, PyFoam and normally six MPI ranks,
   then confirm the real run. Do not enable steady initialization when strict
   fidelity to the published unsteady method is required.
4. Resume the same alpha explicitly if a previous controlled stop or timeout
   left valid fields. Use delete only when a genuinely fresh run is intended.
5. Postprocess the completed or statistically converged run and save its
   Simulation/Postprocess packages in this same work case.

The work-case `Validation/` folder is then updated angle by angle. The plots
overlay only real project points whose case metadata matches Mach and Reynolds
tolerances and whose run is completed or statistically converged. Interrupted
non-converged points are recorded separately and never enter the validation
curve. Before accepted results exist, plots are explicitly `REFERENCE_ONLY`
and show only the approximately digitized Experimental/Cobalt/Kestrel Figure
10 data. Provenance and digitizing limits are documented in
`CFD_2D/reference_data/LS1_0417_Ghoreyshi_2016/README.md`.

For long runs on the current Ryzen 7 4800H use six MPI ranks. For a seconds-
long smoke test use one rank because decomposition/reconstruction can cost more
than the solver work. Eight ranks are valid but occupy every physical core;
requests above eight are rejected because OpenMPI exposes eight physical
slots. Disable live windows and per-angle VTK export for an unattended sweep
after visual operation has been verified; keep force, residual and solver logs
enabled.

### Browser opens but the WSL project is not found

Set the native checkout explicitly:

```powershell
$env:RAMAIR_WSL_PROJECT_ROOT='~/ramair_cfd/DESIGN_APP'
python .\run_ramair_cfd2d_app.py
```

Do not use `--allow-windows-mount` for production meshing. It is a path/UI
diagnostic fallback only.

## 9. Technical references used

- `Documents and Manuals/Gmsh/gmsh_MANUAL.pdf`: Python API initialization, model mesh
  generation, writing, fields and thread options.
- `Documents and Manuals/OpenFOAM/OpenFOAMUserGuide-A4.pdf`: case structure, `gmshToFoam`,
  `checkMesh`, function objects, `foamPostProcess`, VTK and ParaView.
- `Pyfoam_manual_presentation.pdf`: PyFoam installation, case manipulation,
  runner/log workflow, history and monitoring concepts.

The application is an orchestration and usability refactor. It does not change
the selected CFD equations, turbulence model or mesh-physics assumptions.
