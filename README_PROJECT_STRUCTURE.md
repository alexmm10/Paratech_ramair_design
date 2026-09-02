# RamAir DESIGN APP

Aplicacion de diseno y preproceso CAD, mallado Gmsh, preparacion/ejecucion
OpenFOAM y postproceso de perfiles ram-air. CATIA V5 no se ejecuta desde la
interfaz; OpenFOAM solo se inicia mediante una accion explicita del usuario.

El respaldo y actualización del código en GitHub se describe en
`Documents and Manuals/Application/GITHUB_BACKUP_WORKFLOW.md`. Los casos CFD,
mallas, VTK/ParaView y estados de ejecución se excluyen de Git y requieren una
copia de datos científicos independiente.

## Inicio habitual

En Windows, desde la raiz del proyecto:

```bat
START_RAMAIR_CFD2D_APP.bat
```

En la primera instalacion o en un equipo nuevo:

```bat
INSTALL_AND_START_RAMAIR_CFD2D_APP.bat
```

`START_RAMAIR_CFD2D_APP.bat` tambien detecta una primera ejecucion y pregunta
si debe preparar automaticamente el entorno. La opcion recomendada instala el
entorno Python fijado y solicita confirmacion antes de usar `sudo apt` dentro
de Ubuntu para MPI, ParaView, XFOIL y bibliotecas de Gmsh. OpenFOAM Foundation
14 es la referencia actual; se instala desde su repositorio cuando esta
disponible y OpenFOAM 13 queda como compatibilidad. Tambien se admite una
instalacion local bajo `~/.local/opt/openfoam14`. Una dependencia opcional
ausente no se oculta ni impide abrir la interfaz de diagnostico.

El lanzador mantiene el codigo editable en Windows y un runtime rapido en el
filesystem Linux de WSL, por defecto en `~/ramair_cfd/DESIGN_APP`. La interfaz
se abre en `http://localhost:8501`. Cada arranque valida y despliega
atomicamente la misma API para la app y `workflow_backend.py`, evitando mezclar
versiones cacheadas.

`~/ramair_cfd/DESIGN APP` y `~/ramair_cfd/INPUT_FILES` se mantienen unicamente
como enlaces de compatibilidad a `DESIGN_APP`. OpenFOAM no admite espacios en
el nombre del caso; no se debe usar la ruta antigua como raiz real.

Las ejecuciones generales usan el esquema de solver 15. El paso temporal se
limita por `maxDeltaT*` para conservar la resolucion fisica y mantiene una
salvaguarda adaptativa de Courant. Las paradas solicitadas escriben un
checkpoint reiniciable y la interfaz reconcilia procesos interrumpidos al
volver a abrirse.

Comprobacion sin modificar el sistema:

```powershell
python .\run_ramair_cfd2d_app.py --check-only
```

## Validation Lab: RANS y URANS canonico

El laboratorio aislado usa las seis mallas `closed/open` en niveles `coarse`,
`medium` y `fine`. Su configuracion es schema 10 y la aplicacion/backend usan API
25. Cada combinacion de topologia, malla, angulo y `deltaT` posee una unica
linea temporal URANS mutable. La interfaz ofrece solo `Caso unico` y
`Ejecucion secuencial`; no usa pilotos, intentos, archivados ni bypass.

Un caso preparado permanece `NOT_STARTED`. Solo un tiempo fisico positivo,
completo y escrito por el solver lo convierte en `STARTED`. La aplicacion
calcula si corresponde iniciar desde el checkpoint RANS compatible, reanudar
la fase exacta, revisar o reiniciar. Reiniciar usa un selector de ejecuciones,
muestra la identidad y el ultimo tiempo y borra solo ese caso; conserva malla,
RANS, configuracion compartida y Results.

El arranque puede ser progresivo A-E o directo. Los paquetes `Reference`,
`Frequency` y `Manual` aportan exactamente tres `deltaT` descendentes. La cola
admite 18 casos como maximo, prepara de forma diferida y usa
el mismo ejecutor que el caso individual. La prueba rapida es temporal,
opcional y nunca condiciona produccion. PIMPLE 2/3/4 depende del checkpoint
RANS compatible, no de una prueba rapida.

Cada fase tiene logs independientes y una transicion transaccional. El banner
normal `FOAM_SIGFPE` no se clasifica como divergencia. Antes de `backward` se
validan el estado actual y dos estados anteriores completos, se reconstruyen y
descomponen juntos. La malla real del checkpoint RANS es la fuente canonica.

El postproceso cientifico separa ramas fisicas. Cp usa `x/c`, Cf y las
longitudes de burbuja usan `s/c`. Las figuras nuevas exportan PNG 300 dpi, SVG,
datos CSV/JSON y manifiesto de procedencia. ParaView resuelve los VTK de la
ultima iteracion (volumen, pared y farfield), genera solo `latestTime` si falta
y nunca abre una ruta nula.

La migracion real a schema 9 se aplico el 2026-08-13: se retiraron 19 casos
URANS legacy (4.447.430.880 bytes) y se reconstruyeron 18 identidades
canonicas. No se modificaron las seis mallas, los checkpoints/postprocesos
RANS ni los estudios PIMPLE reales. El informe verificable permanece en
`CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8/reports/schema9_migration/deletion_report.json`.

El contrato operativo completo esta en
`CFD_2D/validation_studies/README_VALIDATION_CONVERGENCE_LAB.md`.

La auditoria tecnica y de portabilidad mas reciente esta en
`CFD_2D/reports/OPENFOAM14_SOLVER_POSTPROCESS_PORTABILITY_AUDIT_20260723.md`.

## Estructura

```text
DESIGN APP/
  preprocess_ramair_main.py
  Generate_RamAir_Canopy_MAIN.CATScript
  START_RAMAIR_CFD2D_APP.bat
  INSTALL_AND_START_RAMAIR_CFD2D_APP.bat
  RUN_CATIA_PREPROCESSOR_WINDOWS.bat
  SETUP_CATIA_PREPROCESSOR_WINDOWS.bat
  README_PROJECT_STRUCTURE.md
  Airfoil Profiles/
  Application Support/
    Configurations/
    Logs/
    Packages/
    Reports/
    Temp/
    Tests/
    Tools/
  CATIA/
    Inputs/
    Exports/
    Utilities/
  CFD_2D/
    app/
    CFD_2D_inputs/
    meshes/
    openfoam_cases/
    results/
    scripts/
    tests/
  Documents and Manuals/
    Application/
    CATIA/
    CFD 2D/
    Gmsh/
    OpenFOAM/
    PyFoam/
    XFOIL and XFLR5/
  Previous Versions/
  Results/
```

`CATIA/Inputs` contiene exclusivamente el contrato CSV que consume el
CATScript. `CFD_2D` permanece fuera de esa carpeta. Todos los perfiles base y
los generados por XFOIL se registran en `Airfoil Profiles`.

## Flujo de trabajo

1. **Caso de trabajo:** crear o elegir un candidato en su pagina y pulsar
   explicitamente `Cargar caso de trabajo`. La barra lateral solo informa del
   contexto activo; cambiar geometria o malla no cambia este contenedor.
2. **Geometria:** seleccionar o generar el perfil, editar la configuracion de
   canopy/CATIA y ejecutar el preprocesador.
3. **Caso CFD:** definir Reynolds, Mach, propiedades del fluido y angulos; crear
   el paquete de caso reutilizable.
4. **Malla:** cargar una malla compatible o generar una nueva, revisar Gmsh y
   `checkMesh`, y aprobarla si corresponde.
5. **Caso OpenFOAM:** escribir diccionarios sobre una `polyMesh` real.
6. **Ejecucion:** dry-run por defecto; el solver solo arranca con confirmacion.
7. **Postproceso:** operar sobre la simulacion activa o restaurar una previa.

Los trabajos se ejecutan en segundo plano. Estado y logs se actualizan cada dos
segundos sin pulsar refrescar. El boton **Cerrar DESIGN APP y liberar WSL** se
bloquea si hay una tarea CAE activa. Si se cierra el navegador, el watchdog
detiene una app abandonada tras 15 minutos de inactividad; se puede desactivar
con `RAMAIR_APP_IDLE_SHUTDOWN_MIN=0`.

El angulo de ataque no es una propiedad global de la geometria. El barrido se
define en **Caso CFD** y el selector aparece solo al escribir, ejecutar o
postprocesar uno de los casos `alpha_*`. La barra lateral identifica el perfil,
la malla y el caso activos; la carga y creacion se realizan en **Caso de
trabajo**.

En **Malla**, `y1` manual se introduce en metros y se muestra tambien como
`y1/c`. Cuando se activa el calculo desde y+, la app muestra el valor fisico
calculado y deja claro que el valor manual no se aplica. El espesor de tela se
edita igualmente en metros y se muestra su fraccion de cuerda.

## Biblioteca Results

Para evitar el coste de E/S de `/mnt/c`, la biblioteca activa vive en el
filesystem Linux:

```text
\\wsl.localhost\Ubuntu-22.04\home\alejm\ramair_cfd\DESIGN_APP\Results
```

La carpeta `Results` del proyecto Windows contiene
`OPEN_RESULTS_IN_WSL.bat` y `README_RESULTS_LOCATION.txt`; no es una segunda
biblioteca oculta. La app muestra ambas rutas y puede abrir directamente la
ubicacion real en el Explorador.

La app puede guardar y restaurar etapas independientes bajo:

```text
Results/<nombre_descriptivo>/
  Geometry Packages/<paquete>/
  CFD Cases/<paquete>/
  Meshes/<paquete>/
  Simulations/<paquete>/
  Postprocess Packages/<paquete>/
  case_manifest.json
```

El laboratorio independiente de convergencia usa
`CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8` como workspace
activo y publica informes bajo `Convergence Studies/` dentro del caso Results
cerrado/abierto existente. No restaura ni sobrescribe
`CFD_2D/app_state/active_workspace.json`. Se abre desde
**Validation & Convergence Lab** y mantiene juntas las tripletas atomicas de
geometria, caso y malla.

La pagina del laboratorio se organiza en seis secciones: mallas y condiciones,
solver y estrategia, RANS, URANS, convergencia espacio-tiempo e informes/
workspace. Frecuencias y Courant son subsecciones de la convergencia URANS, no
secciones principales. Un unico monitor global, colapsado por defecto, permite
seguir la ejecucion activa con refresco 15/30/60 s; las vistas de revision son
estaticas. En RANS muestra residuos logaritmicos y coeficientes con `Cl/Cd`;
continuidad sigue registrada y evaluada, mientras Courant se etiqueta
`NOT_APPLICABLE_TO_RANS` y no se genera ni se muestra.

Las colas congelan sus controles en `resolved_batch_config.json`; cada caso
recibe su snapshot y un `applied_configuration_audit.json` antes de poder
ejecutarse. El bloque inicial RANS es de 10,000 iteraciones y las extensiones
son de 2,500. Una base parcial se reanuda desde su ultima iteracion y una base
completa no se reinicia salvo eliminacion explicita. La parada solicitada usa
`writeNow`, conserva campos e historiales y deja la base lista para continuar.

El laboratorio usa esquema 9 y separa la decision de convergencia del cierre
nativo de OpenFOAM. `SIMPLE.residualControl` se elimina de los casos generados:
el gate Python no puede evaluarse antes de la iteracion SIMPLE absoluta 10,000
y solo se ejecuta en 10,000/12,500/15,000/17,500/20,000. Un proceso con codigo
0 que termine antes de su objetivo sin timeout, parada explicita o fallo queda
como `PREMATURE_NORMAL_EXIT`, nunca como convergido. La accion CLI
`recovery-audit --apply` modifica solo metadatos historicos y conserva campos,
directorios temporales y resultados.

Cada combinacion cientifica URANS se guarda y ejecuta en una unica ruta:

```text
runs/<topology>/<level>/<case_id>/
  case/
  case_manifest.json
  resolved_config.json
  stage_plan.json
  stage_journal.json
  execution_summary.json
  scalar_history/
  logs/
```

Preparar dos veces es idempotente mientras el caso no haya comenzado. Una
parada limpia conserva campos y escalares como `PAUSED`, y **Reanudar**
continua desde `latestTime` y la fase exacta sin copiar de nuevo el checkpoint
RANS. Un cambio incompatible exige reinicio confirmado del caso exacto. El
monitor global sigue siempre el PID, caso, fase y log reales publicados en el
runtime atomico. Los productos RANS/URANS se exploran de forma diferida desde
manifiestos de postproceso verificables.

Las bases RANS que no superan el gate automatico pueden postprocesarse sin
relanzar el solver. El gate distingue convergencia estricta, plateau numerico
acotado de un unico residual, revision y divergencia. La aprobacion manual
nunca cambia ese gate: registra por separado si el usuario acepta el resultado
como estadisticamente estacionario, solo como inicializacion URANS, o lo
rechaza. Toda decision exige diagnostico y confirmacion; la nota es opcional y
la decision puede revocarse.

Las mallas abiertas definitivas del laboratorio son:

| Nivel | Celdas | Estado |
|---|---:|---|
| `open_coarse` | 223,080 | Promovida; `checkMesh=OK` |
| `open_medium` | 302,692 | Baseline preservada |
| `open_fine` | 502,474 | Promovida; `checkMesh=OK` |

Las candidatas descartadas y las mallas sustituidas no aparecen como niveles
activos; se conservan en el historial versionado del laboratorio.

Esto permite reutilizar una geometria, un caso de operacion, una malla aprobada
o una simulacion anterior sin repetir todas las etapas. Al restaurar, la salida
activa se archiva primero en `Previous Versions`; la opcion de reemplazo sin
copia solo se ejecuta cuando el usuario la selecciona expresamente. La carga
incrementa la revision de los widgets: los JSON restaurados vuelven a leerse y
sus valores aparecen en los controles editables, no solo en el manifiesto.

Flujo recomendado para reutilizar o versionar una malla:

1. En la barra lateral, seleccionar o crear la carpeta bajo **Caso de trabajo**.
2. Abrir **Contenido del caso guardado**, elegir etapa y paquete, y pulsar
   **Cargar paquete al workspace**.
3. Esperar a que el trabajo termine. La app abre **Malla**, muestra el origen
   cargado y reconstruye los controles desde el JSON restaurado.
4. Mantener **Configuracion editable activa**, revisar/editar los valores y
   guardar los parametros antes de regenerar.
5. Para actualizar una variante, conservar el nombre de paquete y usar
   **Archivar anterior**. Para comparar refinamientos, guardar otro nombre de
   paquete dentro del mismo caso de trabajo.

Prioridad de configuracion de malla: paquete restaurado o valores editados >
base de nivel seleccionada expresamente. `coarse`, `medium` y `fine` rellenan
los controles solo al pulsar **Cargar valores base**; no siguen actuando como
un override oculto. El dominio y sus dimensiones se editan una sola vez en
**Malla > Dominio**.

`CFD_2D/app_state/active_workspace.json` registra caso, etapa, perfil, angulo,
origen y fecha de la ultima restauracion. Las tablas e informes de Malla se leen
siempre de la salida activa restaurada, incluida `mesh_quality_report.json` y
los sets de `checkMesh`.

El caso preparado para comparar el perfil abierto con la validacion cerrada es
`Open_RamAir_comparison_M0p15_Re1p9e6`. Al cargar su workspace completo, la
app restaura automaticamente la geometria abierta escalada a 1 m, las
condiciones Mach 0.15/Re=1.9e6, la malla sin espesor circular de 50c y el
solver topologico actual. La malla permite un tamano objetivo de 3c en el
farfield para reducir celdas lejos del perfil sin alterar la discretizacion
cercana.

## CATIA Windows

Para regenerar el contrato CAD sin la app CFD:

```bat
SETUP_CATIA_PREPROCESSOR_WINDOWS.bat
RUN_CATIA_PREPROCESSOR_WINDOWS.bat
```

El preprocesador lee
`Application Support/Configurations/default_case_config.json`, escribe
`CATIA/Inputs` y conserva la ultima configuracion editable. El CATScript busca
primero `RAMAIR_CATIA_INPUTS` y despues `CATIA\Inputs`. El generador de paquete
independiente crea un ZIP compacto con layout propio para otro PC con CATIA V5;
no ejecuta CATIA durante la validacion.

En la pestana `Geometria`, despues de un preproceso correcto, la aplicacion
detecta `CNEXT.exe` sin abrir CATIA. Si CATIA V5 y
`CATIA/Inputs/ramair_global_inputs.csv` existen, se habilita
`Ejecutar CATScript en CATIA V5`. La accion es explicita, inicia CATIA de forma
visible y fija `RAMAIR_CATIA_INPUTS`; si CATIA no esta instalado, el resto del
workflow permanece disponible.

## Verificacion

```powershell
python run_ramair_cfd2d_app.py --check-only
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
```

Las pruebas OpenFOAM reales solo se habilitan con
`RAMAIR_RUN_OPENFOAM_TESTS=1`. Los manuales originales se conservan clasificados
en `Documents and Manuals`; no se sustituyen por documentos tecnicos nuevos.
Los articulos cientificos usados para justificar decisiones se archivan bajo
`Documents and Manuals/CFD 2D/Research Papers` con nombre reconocible,
metadatos y hash cuando proceda. El estudio temporal vigente es
`CFD_2D/reports/TRANSIENT_TIMESTEP_MESH_SOLVER_STUDY_20260728.md`.

## Ejecucion remota, Git y contenedor

La pagina `Ejecucion` puede generar un ZIP autocontenido para un servidor
Linux/WSL. El paquete congela los casos seleccionados, la cola, scripts,
configuracion, hashes y lanzadores para ejecutar, reanudar, parar, monitorizar
y postprocesar. OpenFOAM 14 y MPI deben existir en el servidor; las
dependencias Python pueden incluirse como wheelhouse offline.

El repositorio Git excluye mallas, campos, resultados, ZIP, PDF de terceros y
estado de ejecucion. Antes de confirmar cambios se ejecuta
`Application Support/Tools/check_repository_artifacts.py`. El `Dockerfile`
ofrece un entorno Linux reproducible para preproceso, mallado, OpenFOAM y
pruebas sin interfaz; CATIA permanece fuera del contenedor y ParaView GUI se
abre en el host.

## Migracion

El layout anterior (`INPUT FILES`, `profiles`, `configs`, `CATIA_inputs`,
`CATIA_exports`, `docs`, `previous_versions`) fue migrado sin sobrescribir
datos. Conflictos, codigo antiguo y elementos obsoletos de la raiz se conservan
en `Previous Versions`. El manifiesto de la operacion se guarda en
`Application Support/Reports/layout_migration_manifest.json`.
