# Contexto vigente de RamAir Studio

Context version: 2026-10-19
Application backend API: 28
Release: 2026.10.19 · studio-delivery-20261019

## Propiedad y despliegue

El checkout que contiene este documento es la fuente editable. En Windows el lanzador sincroniza código de forma atómica a `~/ramair_cfd/DESIGN_APP` en Ubuntu-22.04; puede cambiarse con `--wsl-project-root` o `RAMAIR_WSL_PROJECT_ROOT`. No desarrollar en antiguos árboles INPUT FILES ni editar a la vez la copia WSL.
Los campos y operaciones de mallado viven en disco Linux nativo. Workspaces puede estar en otro destino registrado; `runtime.location.json` y los servicios de almacenamiento describen su ubicación. No mover datos por cambiar la estructura de código.

## Entradas y responsabilidades

`run_ramair_cfd2d_app.py` / lanzadores BAT → bootstrap WSL → `CFD_2D/app/ramair_cfd2d_app.py` → `workspace_app.py`. La UI coordina; los servicios de `CFD_2D/scripts` implementan las etapas.
`preprocess_ramair_main.py --cad-only` prepara Inputs y diagnósticos; la macro `Generate_RamAir_Canopy_MAIN.CATScript` consume CSV compatibles. No ejecutar CATIA.
Bibliotecas de perfiles y Mesh Presets son compartidas; las condiciones físicas pertenecen a `cfd_case.json`. Cada geometría contiene sus mallas y estudios `Convergence/RANS`, cada malla sus setups y cada setup sus ángulos, colas y productos.

## Contratos actuales

API UI/backend/lanzador/bootstrap: 28. Solver de estudio schema: 16; suspensión schema: 2; registro independiente de laboratorio schema: 10.
La migración de suspensión conserva selecciones y cascadas antiguas. Nuevos modelos: frenos exteriores 33% de cada b/2, A–B/C–D longitudinales y una confluencia BRK por lado; cobertura calculada con las posiciones desarrolladas reales. R/b mantiene su distancia de referencia.
La aprobación de malla requiere evidencia real, hashes y contexto geométrico/físico. La importación invalida aprobaciones anteriores hasta repetir la comprobación. No fabricar polyMesh ni PASS.
RANS mide iteraciones; URANS segundos. RANS-only conserva checkpoints e historial; la continuación mantiene numeración y no vuelve a inicializar potencial. Renumerar después de guardar campos rompe correspondencia y está deshabilitado para continuaciones.
Los estudios RANS copian ajustes, comparan firmas, usan ángulos comunes, detectan resultados obsoletos y excluyen pruebas diagnósticas del GCI/aceptación científica. Postprocesar no equivale a aceptar.

## Software y aceptación

Windows + WSL2 Ubuntu 22.04; Python 3.10 probado en Linux; OpenFOAM Foundation 14; Gmsh 4.15.2. Dependencias Python fijadas en `CFD_2D/app/requirements-cfd2d-app.txt`; pytest separado.
La evidencia histórica de septiembre ejercitó el recorrido vacío, dos mallas cerradas (108104/155824), dos ángulos y pausa/continuación RANS; no son los defaults de estudio actuales. En octubre se regeneraron y comprobaron los presets de 215058/248180 celdas, se instaló un entorno nuevo en otra ruta WSL y se verificaron las once páginas y el ejemplo de polar. Evidencia actual en `Documents and Manuals/Current/Evidence/delivery_20261019`; la histórica permanece en delivery_20260930. No presentar la revisión visual nativa actual como completada: la herramienta no pudo verificar la URL del navegador. Las pruebas automatizadas de interfaz sí finalizaron sin errores.
El paquete remoto se verifica en WSL aislado; CATIA y el servidor real de la empresa requieren aceptación allí. No proclamar validación científica universal, estabilidad de todos los inlets ni duraciones de ejecución garantizadas.

## Entrega completa y presets de estudio

`Application Support/Configurations/studio_defaults.json` selecciona los JSON de `Study Presets` y los presets de malla paramétricos. Se aplican a configuraciones nuevas; nunca sobrescribir borradores ni setups existentes. Cerrada: estudio de 215058 celdas; abierta estándar 265520, optimizada 248180. Las métricas son de las mallas originales, no una aprobación heredable por otro perfil. La ejecución inicial es RANS-only; URANS se elige explícitamente.

`package_studio_delivery.py` reúne código, manuales, instalador y workspace exportado en un solo ZIP. `install_studio_bundle.py` verifica todo, utiliza el lanzador oficial, importa Example_Open_Polar sin colisiones ni sobreescritura y repite checkMesh si OpenFOAM está disponible. `verify_studio_delivery.py` comprueba todos los archivos y objetos del ejemplo. Los campos volumétricos de la campaña original no se incluyen: se conservan campos iniciales, 26 ángulos preparados y 9 puntos de polar RANS con sus productos. `retained_results_snapshot.json` impide recalcular campos desde 0 y confundirlos con resultados. Tras una ejecución propia con campos guardados, se permite nuevo postproceso.

La galería conserva figuras de comparación/validación con procedencia relativa y no requiere campañas locales. El instalador y el ejemplo no ejecutan solver ni CATIA por sí mismos. El paquete remoto se genera desde la aplicación cuando se necesita; la entrega principal es un solo archivo descargable. Manuales 15–17 explican primer uso, operaciones y parámetros efectivos.

## Mantenimiento

Leer CHANGELOG, estructura y manual relevante; actualizar juntos interfaz/backend si se rompe un contrato. Ejecutar tests completos, `check_project_context.py`, `check_repository_artifacts.py` y el verificador de paquete. CI verifica software; evidencia WSL/OpenFOAM se conserva aparte.
El archivo histórico de herramientas/contextos/bibliografía está fuera del árbol activo. Los antiguos motores usados por laboratorio/Results se conservan como compatibilidad, no son temporales. Mantener Results/Data Archive/Workspaces/checkpoints; antes de retirarlos verificar su copia o autorización explícita.
Los paquetes se generan de la fuente publicada y guardan commit/hashes. Git no reemplaza el respaldo científico de workspaces. El informe de entrega y manuales Current son la descripción vigente, no los antiguos informes fechados.
