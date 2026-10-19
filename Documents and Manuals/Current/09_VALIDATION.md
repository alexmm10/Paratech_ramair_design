# Laboratorio independiente y galería

API 28 · Manual vigente · 2026-10-19.

## Independencia

Validation & Convergence Lab mantiene validación LS(1)-0417, convergencia espacial/temporal y comparación abierta/cerrada. No depende del workspace seleccionado ni se borran sus ejecuciones anteriores.

## Código y rutas

validation_convergence_page.py y ls1_validation_page.py son sus interfaces. ramair_2d_ls1_validation_study.py mantiene ls1_0417_closed_polar_M0p15_Re1p9e6. CFD_2D/validation_studies, experimental_meshes, openfoam_cases y Results conservan registros, mallas y campos existentes.

## Permanencia

Validation Gallery contiene figuras finales seleccionadas y evidencia de origen. No incorpora simulaciones completas al paquete de aplicación. El laboratorio conserva sus herramientas de cálculo hasta completar sus estudios.

## Criterio científico

Validación exige convergencia, independencia de malla/tiempo y comparación con referencia. La prueba de esta entrega comprueba etapas y controles reales, pero sus coeficientes parciales no sustituyen esa validación.

## Evidencia nueva

Application Support/Reports/workflow_20260909 y Workspaces/Workflow_Validation_20260909 conservan XFOIL, tres geometrías, mallas, solvers y resultados. Las iteraciones fallidas están identificadas para reproducir las correcciones.
