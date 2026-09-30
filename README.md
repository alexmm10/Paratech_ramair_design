# RamAir Studio

Herramienta de diseño de perfiles y preparación CAD para CATIA V5, mallado Gmsh y CFD 2D con OpenFOAM Foundation 14.
Entrega **2026.09.30 · API 28 · studio-delivery-20260930**. La preparación CAD no inicia CATIA.

## Instalar y abrir

1. Extraer el paquete de aplicación completo en una carpeta Windows local, fuera de carpetas sincronizadas.
2. Instalar Python 3 y WSL2 con Ubuntu 22.04. CATIA V5 necesita instalación y licencia independientes.
3. Abrir `INSTALL_AND_START_RAMAIR_CFD2D_APP.bat`. El instalador explica las dependencias ausentes; OpenFOAM Foundation 14 requiere su repositorio oficial de Ubuntu.
4. Abrir `START_RAMAIR_CFD2D_APP.bat` para usos posteriores. La interfaz se sirve en `http://localhost:8501`.
5. Crear un caso de trabajo o importar el paquete de ejemplo desde **Casos de trabajo → Trasladar**. Las mallas importadas requieren repetir checkMesh y su aprobación.

[Guía de entrega e instalación](Documents%20and%20Manuals/Current/14_DELIVERY.md) · [Manuales](Documents%20and%20Manuals/Current/README.md) · [Informe de verificación](Documents%20and%20Manuals/Current/DELIVERY_REPORT.md).

## Distribución y datos

La aplicación, el ejemplo y el paquete remoto son tres ZIP diferentes, identificados por versión, commit y hashes. Git contiene código, pruebas, configuraciones iniciales, presets y referencias pequeñas. Workspaces, Results, Data Archive, campos, licencias, publicaciones externas y entornos instalados se guardan por separado.

## Desarrollo

Leer [AGENTS.md](AGENTS.md), [contexto vigente](PROJECT_CONTEXT_FOR_CODEX.md) y el manual de la sección. Las reglas preservan datos y resultados existentes. No ejecutar CATIA en las verificaciones automáticas.
Ejecutar `python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q`, los auditores de contexto y artefactos y las comprobaciones reales relevantes antes de publicar. [Estructura](README_PROJECT_STRUCTURE.md) y [cambios](CHANGELOG.md).
