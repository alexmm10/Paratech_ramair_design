# Tarea 07 — Contrato de ejecución y monitor común

Fecha: 2026-08-20  
Estado: implementado y validado de forma acotada  
API de aplicación: 25  
Schema del ciclo de vida: 1

## Decisión

Toda publicación nueva de ejecución usa exactamente ocho estados:
`PREPARED`, `RUNNING`, `PAUSED_RECOVERABLE`, `FAILED`, `COMPLETED`,
`REVIEW_REQUIRED`, `APPROVED` y `REJECTED`. Los nombres históricos se
normalizan al leerlos y se conservan como evidencia; no se reescriben en masa.

Cada caso mantiene `.ramair_execution_state.json`. La escritura es atómica e
incluye identidad del run, fase, secuencia, clave de idempotencia, PID y token
de inicio, resultado, evidencia de restart y las últimas 256 transiciones. Un
estado `RUNNING` huérfano se reconcilia a `PAUSED_RECOVERABLE` si existe un
tiempo de reinicio real y a `FAILED` si no existe.

## Ejecución

- El gestor de la aplicación mantiene `RUNNING` durante una parada solicitada;
  el detalle operativo vive en `stop_requested_at/stop_stage`. Sólo publica el
  terminal `PAUSED_RECOVERABLE` cuando el proceso ha terminado.
- La cola secuencial omite casos ya `COMPLETED`, reanuda sólo estados
  recuperables y publica cada dispatch con una clave derivada de caso, comando
  y fase.
- El runner progresivo publica las fases congeladas y conserva el último
  checkpoint válido. Una excepción de orquestación con checkpoint no se
  presenta como pérdida irreversible en el contrato común.

## Monitor y conservación

`ramair_monitor_core.py` es el único parser incremental para el monitor general
y el Validation Lab. Retiene colas acotadas de residuos, iteraciones lineales,
`deltaT`, Courant, continuidad y tiempo de ejecución. El inventario de señales
incluye `forceCoeffs`, probes, solverInfo y logs. `purgeWrite` sólo afecta a
directorios de tiempo volumétricos de OpenFOAM.

## Evidencia acotada

Se ejecutó el tutorial cavity de OpenFOAM 14 hasta `t=0.005` en copias bajo
`/tmp`: una ejecución serial terminó con `End`; otra con `nProcs: 2`,
`Finalising parallel run` y reconstrucción de `t=0.005`. No se modificó ni se
ejecutó ningún caso CFD de producción. La ejecución paralela usó Open MPI
4.1.2 desde `/usr/bin/mpirun`, el mismo `libmpi.so.40` enlazado por `foamRun`,
`numberOfSubdomains=2`, dos rangos puros y un hilo por rango, sin scheduler ni
sobreasignación.
