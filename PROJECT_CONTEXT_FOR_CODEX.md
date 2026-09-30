# Contexto vigente de RamAir Studio

Context version: 2026-09-30
Application backend API: 28
Release: 2026.09.30 · studio-delivery-20260930

## Propiedad y despliegue

El checkout que contiene este documento es la fuente editable. En Windows el lanzador sincroniza código de forma atómica a `~/ramair_cfd/DESIGN_APP` en Ubuntu-22.04; puede cambiarse con `--wsl-project-root` o `RAMAIR_WSL_PROJECT_ROOT`. No desarrollar en antiguos árboles INPUT FILES ni editar a la vez la copia WSL.
Los campos y operaciones de mallado viven en disco Linux nativo. Workspaces puede estar en otro destino registrado; `runtime.location.json` y los servicios de almacenamiento describen su ubicación. No mover datos por cambiar la estructura de código.

## Entradas y responsabilidades

`run_ramair_cfd2d_app.py` / lanzadores BAT → bootstrap WSL → `CFD_2D/app/ramair_cfd2d_app.py` → `workspace_app.py`. La UI coordina; los servicios de `CFD_2D/scripts` implementan las etapas.
`preprocess_ramair_main.py --cad-only` prepara Inputs y diagnósticos; la macro `Generate_RamAir_Canopy_MAIN.CATScript` consume CSV compatibles. No ejecutar CATIA.
Bibliotecas de perfiles y Mesh Presets son compartidas; las condiciones físicas pertenecen a `cfd_case.json`. Cada geometría contiene sus mallas y estudios `Convergence/RANS`, cada malla sus setups y cada setup sus ángulos, colas y productos.

## Contratos actuales

API UI/backend/lanzador/bootstrap: 28. Solver general schema: 15; suspensión schema: 2; registro independiente de laboratorio schema: 10.
La migración de suspensión conserva selecciones y cascadas antiguas. Nuevos modelos: frenos exteriores 33% de cada b/2, A–B/C–D longitudinales y una confluencia BRK por lado; cobertura calculada con las posiciones desarrolladas reales. R/b mantiene su distancia de referencia.
La aprobación de malla requiere evidencia real, hashes y contexto geométrico/físico. La importación invalida aprobaciones anteriores hasta repetir la comprobación. No fabricar polyMesh ni PASS.
RANS mide iteraciones; URANS segundos. RANS-only conserva checkpoints e historial; la continuación mantiene numeración y no vuelve a inicializar potencial. Renumerar después de guardar campos rompe correspondencia y está deshabilitado para continuaciones.
Los estudios RANS copian ajustes, comparan firmas, usan ángulos comunes, detectan resultados obsoletos y excluyen pruebas diagnósticas del GCI/aceptación científica. Postprocesar no equivale a aceptar.

## Software y aceptación

Windows + WSL2 Ubuntu 22.04; Python 3.10 probado en Linux; OpenFOAM Foundation 14; Gmsh 4.15.2. Dependencias Python fijadas en `CFD_2D/app/requirements-cfd2d-app.txt`; pytest separado.
La entrega ha ejercitado el recorrido vacío, dos mallas cerradas aprobadas (108104/155824 celdas), dos ángulos (0°/8°), pausa/continuación y postproceso RANS. Son pruebas acotadas, no polares convergidas ni independencia de malla. Evidencia en `Documents and Manuals/Current/Evidence/delivery_20260930`.
El paquete remoto se verifica en WSL aislado; CATIA y el servidor real de la empresa requieren aceptación allí. No proclamar validación científica universal, estabilidad de todos los inlets ni duraciones de ejecución garantizadas.

## Mantenimiento

Leer CHANGELOG, estructura y manual relevante; actualizar juntos interfaz/backend si se rompe un contrato. Ejecutar tests completos, `check_project_context.py`, `check_repository_artifacts.py` y el verificador de paquete. CI verifica software; evidencia WSL/OpenFOAM se conserva aparte.
El archivo histórico de herramientas/contextos/bibliografía está fuera del árbol activo. Los antiguos motores usados por laboratorio/Results se conservan como compatibilidad, no son temporales. Mantener Results/Data Archive/Workspaces/checkpoints; antes de retirarlos verificar su copia o autorización explícita.
Los paquetes se generan de la fuente publicada y guardan commit/hashes. Git no reemplaza el respaldo científico de workspaces. El informe de entrega y manuales Current son la descripción vigente, no los antiguos informes fechados.
