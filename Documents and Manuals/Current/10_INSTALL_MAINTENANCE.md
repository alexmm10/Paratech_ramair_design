# Instalación, actualización, Git y mantenimiento

API 28 · Manual vigente · 2026-10-19.

## Equipo nuevo

Use START_RAMAIR_CFD2D_APP.bat o INSTALL_AND_START_RAMAIR_CFD2D_APP.bat desde la raíz. El lanzador prepara WSL, runtime nativo y venv. --check-only comprueba el entorno. OpenFOAM Foundation 14 es la referencia de esta entrega.

## Dependencias

requirements-cfd2d-app.txt fija Python científico, Streamlit/PyArrow, Gmsh y PyFoam. pytest se separa en requirements-development.txt. OpenFOAM incluye foamRun y utilidades: no se instalan pimpleFoam, checkMesh o decomposePar por separado. XFOIL y ParaView sirven a sus funciones específicas; CATIA requiere instalación/licencia externa.

## Fuente y despliegue

Edite el checkout Windows canónico. El lanzador sincroniza API 28 de forma atómica y preserva datos. Las bibliotecas nuevas se publican a Windows. No continúe el desarrollo en INPUT FILES de OneDrive ni copie una app antigua sobre el runtime.

## Git

Se versionan código, tests, defaults, presets paramétricos, referencias pequeñas y manuales. .gitignore excluye Workspaces, Data Archive, Results, mallas, campos, paquetes, logs y entornos. GitHub respalda software; los datos CFD necesitan copia independiente. Revise diff, pruebas y contexto antes de commit/push; no use force.

## Paquetes

package_ramair_project.py evita recorrer árboles pesados, verifica miembros críticos y SHA-256 y excluye datos CFD, Docker y publicaciones/binarios externos. Las dependencias se instalan en destino. Un paquete remoto sirve para ejecutar casos existentes.

Casos de trabajo > Trasladar utiliza transfer_studio_workspace.py. El paquete básico incluye geometrías, mallas, configuraciones y campos iniciales; la casilla de resultados añade checkpoints e historiales. Los contenidos repetidos se almacenan una sola vez mediante SHA-256. La importación comprueba archivos, conserva el workspace original, actualiza rutas internas y exige revisar/aprobar las mallas tras su reubicación. Instale también la aplicación y sus bibliotecas compartidas en destino.

Ejecución genera paquetes remotos con las rutas de Studio, MPI y plan de fases. El ZIP devuelto se valida antes de sustituir un ángulo; la versión anterior pasa a Data Archive/Remote Imports. Se rechaza importar sobre un solver activo. Las dependencias Python se instalan en el equipo receptor; OpenFOAM y MPI deben estar disponibles allí.

## Mantenimiento

Actualice PROJECT_CONTEXT_FOR_CODEX.md, CHANGELOG.md, README_PROJECT_STRUCTURE.md y estos manuales. Ejecute check_project_context.py, check_repository_artifacts.py y pytest. Tras cambios de malla haga checks reales; tras cambios visuales inspeccione escritorio y ancho reducido.

## Limpieza

Data Archive sustituye el nombre Previous Versions para datos recuperables. Los registros activos se actualizan. Docker y su manual se retiran. No borre módulos históricos basándose solo en que no aparecen en el menú: laboratorio y restaurador pueden usarlos.
