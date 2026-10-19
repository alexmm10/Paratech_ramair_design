# RamAir Studio

Herramienta de diseño de perfiles y preparación CAD para CATIA V5, mallado Gmsh y CFD 2D con OpenFOAM Foundation 14.
Entrega **2026.10.19 · API 28 · studio-delivery-20261019**. La preparación CAD no inicia CATIA.

## Instalar y abrir

1. Extraer el paquete único completo en una carpeta Windows local, fuera de carpetas sincronizadas.
2. Instalar Python 3 y WSL2 con Ubuntu 22.04. CATIA V5 necesita instalación y licencia independientes.
3. En la raíz de la entrega, abrir `INSTALAR_Y_ABRIR.bat`; verifica contenido, instala el entorno e incorpora `Example_Open_Polar`. El código independiente conserva también su lanzador `INSTALL_AND_START_RAMAIR_CFD2D_APP.bat`. OpenFOAM Foundation 14 requiere su repositorio oficial de Ubuntu.
4. Abrir `ABRIR_RAMAIR.bat` para usos posteriores de la entrega. La interfaz se sirve en `http://localhost:8501`.
5. Seleccionar el ejemplo o crear otro caso. Las mallas importadas requieren repetir checkMesh y su aprobación; el instalador lo realiza cuando dispone de OpenFOAM.

[Guía de entrega e instalación](Documents%20and%20Manuals/Current/14_DELIVERY.md) · [Manuales](Documents%20and%20Manuals/Current/README.md) · [Informe de verificación](Documents%20and%20Manuals/Current/DELIVERY_REPORT.md).

## Distribución y datos

La entrega completa reúne aplicación y ejemplo en **un solo ZIP**, identificado por versión, commit y hashes. Incluye presets efectivos de estudio y figuras conservadas sin distribuir la campaña completa. Git contiene código, pruebas, configuraciones iniciales, presets y referencias pequeñas. El workspace del ejemplo se adjunta al paquete descargable, no al árbol Git. Workspaces propios, Results, Data Archive, campos volumétricos, licencias, publicaciones externas y entornos instalados se guardan por separado. Los paquetes de ejecución remota se generan desde la app cuando se necesitan.

[Primer uso y contenido incluido](Documents%20and%20Manuals/Current/15_FIRST_RUN_AND_DEFAULTS.md) · [Operaciones por sección](Documents%20and%20Manuals/Current/16_USER_OPERATIONS.md) · [Parámetros efectivos](Documents%20and%20Manuals/Current/17_STUDY_PARAMETER_REFERENCE.md).

## Desarrollo

Leer [AGENTS.md](AGENTS.md), [contexto vigente](PROJECT_CONTEXT_FOR_CODEX.md) y el manual de la sección. Las reglas preservan datos y resultados existentes. No ejecutar CATIA en las verificaciones automáticas.
Ejecutar `python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q`, los auditores de contexto y artefactos y las comprobaciones reales relevantes antes de publicar. [Estructura](README_PROJECT_STRUCTURE.md) y [cambios](CHANGELOG.md).
