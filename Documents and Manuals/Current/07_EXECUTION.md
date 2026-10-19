# Ejecución, colas, MPI y recuperación

API 28 · Manual vigente · 2026-10-19.

## Inicio

La acción inicial comprueba el plan. Ejecutar simulación real habilita el solver. Se seleccionan ángulos, cores por caso y simultaneidad; el gestor comprueba el presupuesto del equipo y las restricciones seleccionadas. El gestor impide iniciar otra tarea de la app mientras una sigue activa.

## Orquestación

queue_manifest genera una campaña. ramair_2d_parallel_campaign coordina recursos y controles. ramair_2d_openfoam_staged_runner ejecuta RANS, conserva su historia, transfiere campos a 0 y recorre A–E con el runner y PyFoam.

## Semántica temporal

Los tiempos RANS son contadores de iteración. steadyInitialization/history conserva campos, fuerzas y revisión. URANS empieza en t=0 físico desde los campos transferidos. El bypass diagnóstico empleado en smoke queda explícito y no demuestra convergencia RANS.

## Monitor y pausa

Hay estado de campaña, log y fuerzas por ángulo. Pausar solicita escritura reiniciable y detiene el comienzo del siguiente caso. No elimine processor* ni mate mpirun durante la escritura. La consola se actualiza mientras un trabajo está RUNNING.

## Reinicio

reset_angles valida propiedad y ausencia de solver, archiva el caso y reconstruye. Reiniciar RANS invalida su URANS dependiente, conservado en Archive. Reiniciar solo URANS exige checkpoint RANS, lo copia a 0 y evita repetir RANS.

## Fases breves

La tolerancia de final de fase se basa en precisión de serialización. No se consideran ejecutadas fases en t=0. El paso se limita para disponer de al menos cuatro pasos nominales en una fase corta.

## Traslado

El paquete remoto contiene casos explícitos, plan y scripts, con instrucciones de ejecución/monitorización. Requiere OpenFOAM/MPI compatible. El paquete de la aplicación excluye datos científicos; estos se copian por separado.

## Solo RANS y convergencia general

RANS-only conserva directorios de iteración para futuras continuaciones; no inicia URANS ni transforma el contador a segundos. La escritura final se asegura aunque el intervalo nominal sea mayor que la duración solicitada. Continuar conserva campos, numeración y distribución; Reiniciar archiva y reconstruye. [Nueva pestaña de convergencia de malla](12_RANS_MESH_CONVERGENCE.md) y [paquete remoto](13_REMOTE_EXECUTION.md).
