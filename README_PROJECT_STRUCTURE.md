# Estructura vigente · RamAir Studio 2026.10.19

```text
DESIGN APP/
  README.md, RELEASE.json, CHANGELOG.md
  AGENTS.md, PROJECT_CONTEXT_FOR_CODEX.md
  run_ramair_cfd2d_app.py, *.bat         Instalación y arranque Windows
  preprocess_ramair_main.py            Preparación de entradas CAD
  Generate_RamAir_Canopy_MAIN.CATScript Macro manual compatible
  Airfoil Profiles/                    Referencias y perfiles compartidos
  Mesh Presets/                        Configuraciones paramétricas reutilizables
  Study Presets/                       Defaults efectivos y procedencia del estudio
  Validation Gallery/                  Figuras seleccionadas con procedencia
  CATIA/Utilities/                     Verificación y dependencias CAD
  CFD_2D/
    app/                               Interfaz y adaptadores API 28
    scripts/                           Servicios y motores científicos
    native/                            Fuentes del atlas OpenFOAM
    tests/, reference_data/            Pruebas y tablas pequeñas
    CFD_2D_inputs/config/               Valores iniciales y compatibilidad
  Application Support/
    Configurations/                    Defaults CAD
    Tools/                             Instalación, paquetes, traslado, auditoría y aceptación
    Tests/                             Configuración de pruebas
  Documents and Manuals/
    Current/                           Manuales, inventarios e informe de entrega
    Application/                       Instaladores Linux y guías de arranque/Git
    CFD 2D/                            Especificación física principal
  Workspaces/<nombre>/                 DATOS independientes de la aplicación
    workspace.json
    CATIA/<modelo>/                    Configuración, Inputs y Exports
    CFD_2D/<caso>/cfd_case.json
      Geometries/<geometría>/
        geometry.json, Inputs/
        Convergence/RANS/<estudio>/     study.json, Queues, Analysis
        Meshes/<malla>/
          mesh_config.json, mesh_report.json, MESH_APPROVED.flag
          OpenFOAM/<setup>/
            solver_setup.json, solver_config.json, phase_plan.json
            Angles/<alpha>/            0, constant, system, campos, historiales y Postprocess
            Queues/, Polars/
  Results/, Data Archive/               Biblioteca histórica y revisiones recuperables
```

Workspaces, Results, Data Archive, entornos, campos, Logs/Reports/Temp/Packages son datos locales; no se entregan por defecto en el ZIP de código ni en Git. La carpeta de datos se crea cuando se utiliza la aplicación. `CFD_2D/validation_studies` mantiene el laboratorio independiente existente.

Los archivos obsoletos retirados están fuera de esta estructura: archivo Windows junto al checkout y `~/ramair_cfd/Development_Archive` en WSL. Sus inventarios conservan rutas previas, hashes y motivo. No incluirlos en la entrega.
La fuente Windows puede instalarse en otra ruta; el lanzador elige runtime Linux con `--wsl-project-root`. Los paths de procedencia en evidencia son históricos, no rutas necesarias para instalar.

[Instalación y entrega](Documents%20and%20Manuals/Current/14_DELIVERY.md) · [Manuales](Documents%20and%20Manuals/Current/README.md) · [Informe](Documents%20and%20Manuals/Current/DELIVERY_REPORT.md).

La distribución completa añade una raíz exterior `RamAir_Studio_20261019/` con `INSTALAR_Y_ABRIR.bat`, `ABRIR_RAMAIR.bat`, `LEEME_PRIMERO.md`, `DELIVERY_MANIFEST.json`, el código en `DESIGN APP/` y el workspace exportado en `Content/Example_Open_Polar.zip`. Este contenido de ejemplo se adjunta al ZIP descargable y no al árbol Git.
