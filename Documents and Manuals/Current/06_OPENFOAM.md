# Configuración y generación OpenFOAM

API 28 · Manual vigente · 2026-10-19.

## Selección

Seleccione caso CFD, geometría, una o varias mallas aprobadas, nombre de configuración y ángulos. Se admiten un ángulo, intervalo o lista libre, con máximo tres decimales.

## Jerarquía

Meshes/<malla>/OpenFOAM/<configuración> contiene solver_setup.json, solver_config.json, phase_plan.json y Angles/alpha_p8p000, etc. Cada ángulo posee 0, constant, system y plantillas RANS. Una repetición no sobrescribe casos existentes.

## Preset

solver_defaults parte del contrato de validación: foamRun/incompressibleFluid, SA, RANS inicial, Euler A–C y backward D–E; dt*=0.0025, cinco correctores externos y dos de presión. El arranque abierto muestra maxCo=1 como ajuste conservador editable tras observar inestabilidad con 50.

## Menú y propiedad

General; tiempo/fases; residuales/convergencia; esquemas/correctores; escritura/postproceso. La tabla de fases determina duración total e inicio de muestreo. Se usa una fuente de escritura y se ocultan alias contradictorios. Los campos efectivos quedan guardados y se pueden consultar.

## Case writer

write_case recibe mesh_directory, output_directory, physical_config y geometry_manifest explícitos. Ajusta no ortogonalidad, propiedades y referencias para cada malla. Una selección múltiple se prepara como tarea secuencial única.

## Diccionarios

0 contiene U, p, nuTilda, nut y los campos del modelo. constant contiene propiedades y transporte turbulento. system incluye controlDict, fvSchemes, fvSolution, decomposeParDict y funciones de fuerzas/campos. case_config.json registra valores efectivos. La referencia de fuerzas usa el espesor extruido real.

## Fases y escritura

Cada fase permite elegir checkpoint final, historial denso para backward o intervalo configurado. El intervalo se alinea al final de fase, y el informe registra el intervalo solicitado y el efectivo. El historial denso conserva al menos tres estados; purgeWrite=0 conserva todos. Las pruebas cortas no sustituyen el protocolo completo de validación.
