# Postproceso, resultados y comparación

API 28 · Manual vigente · 2026-10-19.

## Menú

Tres pestañas: Postproceso y análisis; Polar y resultados; Comparación. La cola admite varios ángulos de una configuración.

## Opciones

Cp, Cf, y+, esfuerzos de pared, Q, vorticidad y Courant; perfiles de velocidad en estaciones configurables; imágenes finales ParaView; animaciones opcionales; exportación VTK opcional; escalas, intervalo temporal y muestras RANS. Imágenes rápidas y animaciones se solicitan por separado.

Los menús recuperan las opciones guardadas del setup. Si no existen, utilizan Study Presets/postprocess.json. El valor cero de inicio de promedio RANS se conserva como cero.

El ejemplo reducido conserva tablas/figuras y nueve puntos de origen. Tras la importación verificada, el instalador contrasta que los coeficientes no hayan cambiado y adapta sus referencias, sin otorgar una validación científica nueva. Si faltan campos volumétricos, RETAINED_RESULTS_ONLY evita reemplazar los productos por un cálculo desde campos iniciales. Una ejecución propia con nuevos campos guardados habilita el postproceso, incluidos campos comprimidos U.gz.

## Salidas

Angles/<alpha>/Postprocess conserva case_summary.json, CSV, diagnósticos e imágenes, con productos por fase. El postprocesador recibe direct_case_dir y direct_output_dir; no depende de un caso global.

## Revisión

Botones independientes RANS/URANS invocan evaluate_rans_gate y review_urans_signals. Estados insuficientes o no convergidos se informan y se guardan en Reviews. Postprocesar no valida automáticamente una simulación.

## Polar aceptada

El usuario incluye o retira puntos reales revisados. accepted_points.json guarda fase, valores y SHA-256 del resumen. Reprocesarlo invalida su aceptación. workflow_test está bloqueado expresamente. Se generan CSV y gráficas de CL, CD, CM y eficiencia.

## Comparación

Dos configuraciones del mismo CFD pueden variar geometría, malla y solver. Se superponen coeficientes/polar CL-CD, diferencias relativas y distribuciones Cp/Cf/y+ para igual ángulo/fase. Se calculan pendiente, alpha CL=0, máximos observados y stall aproximado solo tras observar un descenso.

## Exportación y liberación

export_products copia categorías elegidas a Windows y verifica hashes. La copia conserva WSL. La liberación es otra acción: muestra carpetas generadas y exige selección explícita. Rechaza solvers activos o una copia incompleta y conserva configuración y malla aprobada.

## Interpretación

PROCESSED identifica productos disponibles. PROCESSED_PARTIAL y workflow_test identifican pruebas acotadas. Ninguno equivale por sí solo a independencia espacial/temporal o concordancia experimental.
