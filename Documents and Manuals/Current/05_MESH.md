# Mallado estándar, presets y aprobación

API 28 · Manual vigente · 2026-10-19.

## Motor

El método antes llamado experimental es el estándar general. render_mesh dirige a las páginas abierta/cerrada según la geometría. Los nombres internos se mantienen por compatibilidad con el laboratorio. Los selectores generales legacy con/sin espesor se retiran.

## Geometría

Inputs/profile y, en abierto, Inputs/base son las fuentes. El dominio es circular, radio configurable en c. La abertura conecta exterior e interior; ram_air_inlet nunca es un parche físico. La extrusión tiene una capa y frontAndBack empty.

## Controles

General agrupa threads, espesor, timeouts, aviso mínimo de celdas, algoritmo/suavizado/optimización y wake opcional apagado por defecto. BL configura y+, capas, distribución beta/geométrica, espesor y distribución tangencial. Volumen externo controla tamaños/radio/transiciones; volumen interno solo aparece en abierto.

## Presets y versiones

Mesh Presets/<topología>_<coarse|medium|fine>_R<radio>c_<nombre>.json guarda parámetros reutilizables, sin propiedades del fluido ni identidad de geometría. bind_mesh_config aporta la física del caso. Importar un preset no activa su malla anterior. Regenerar una malla con casos OpenFOAM exige un nombre nuevo.

## Ejecución y salida

mesh_job invoca el motor con --output-dir y --check-mesh. Se conservan mesh_config.json, mesh_report.json, puntos/diagnósticos, MSH, polyMesh y log.checkMesh. El máximo es 2 millones de celdas. No hay límites de MB ni recortes de lectura interna en este recorrido.

## Calidad

Se muestran extremos reales: no ortogonalidad, skewness, determinante, peso de interpolación, ratio de volumen, aspect ratio, área y volumen mínimos. La guía CFDpilot orienta; la aceptación depende de checkMesh. La aprobación guarda huellas de MSH/configuración/polyMesh. Cualquier cambio de contexto o contenido la invalida.

## Interpretación

checkMesh OK confirma controles geométricos de esa malla. No demuestra independencia espacial ni exactitud aerodinámica. Las revisiones fallidas permanecen identificadas para diagnóstico y comparación.

## Presets incluidos en 2026.10.19

El borrador nuevo cerrado usa el preset de estudio de 215058 celdas de origen. El abierto parte del optimizado de 248180; el estándar de 265520 se selecciona como alternativa. Los tres conservan sus métricas, firma de la configuración de origen y procedencia en Study Presets. Las nuevas geometrías siempre se vuelven a generar y comprobar.

Study Presets/Lab References conserva además las seis configuraciones exactas activas coarse/medium/fine del laboratorio: cerrado 203691/333826/618382 y abierto 223080/302692/502474 celdas. Su formato pertenece al motor de validación legacy; no se mezcla con los presets anidados del flujo general. Son registros de reproducibilidad, no mallas activadas ni ejecuciones ficticias en la nueva instalación.
