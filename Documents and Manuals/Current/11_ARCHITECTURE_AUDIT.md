# Arquitectura y auditoría del código

API 28 · Manual vigente · 2026-10-19.

## Capas

Shell pequeño de versión y tareas; workspace_app como router; páginas de mallado/laboratorio reutilizadas; servicios bajo CFD_2D/scripts. Los algoritmos científicos quedan fuera del router. La compatibilidad histórica se separa del recorrido general.

## Jerarquía

Biblioteca de perfiles → caso CFD físico → geometría importada → malla aprobada → configuración OpenFOAM → ángulos → postproceso. El workspace contiene modelos CAD y estudios CFD independientes. CAD no es un prerrequisito del cálculo 2D.

## Persistencia

Manifiestos JSON con temporal único y renombrado; UUID para identidades; hashes para puntos, física, configuraciones y aprobación. No se aceptan mallas vacías, PASS inventados ni resultados de otro contexto.

## Inventario

CODE_INVENTORY.md enumera módulos, líneas, descripción y número de símbolos. FUNCTION_INVENTORY.csv registra nombre, tipo y línea de cada símbolo. La detección estática no resuelve procesos externos ni importaciones dinámicas; no se equipara ausencia de importación a código inútil.

## Retirado y compatible

Se elimina el antiguo cuerpo monolítico de la app general, los selectores legacy de mallado y los controles de material/tensión/vibración. Se conservan motores y librerías históricas usados por el laboratorio y la recuperación de Results, sin ofrecerlos como métodos nuevos.

## Reproducibilidad

La fuente está en Git; cada objeto de datos conserva configuración/condiciones. Aprobar afecta a contenido concreto, no al nombre. Cambiar geometría, física o evidencia obliga a regenerar/reaprobar. Los logs y metadatos son parte de la evidencia.
