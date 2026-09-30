# Rendimiento y recursos de ejecución

Versión 2026.09.30. La parte superior del panel consulta el equipo actual: CPU, memoria disponible, disco, MPI y OpenFOAM. Las campañas y sus perfiles medidos son datos locales y no se distribuyen como si fueran mediciones del equipo receptor.

## Selección de recursos

Compare tiempo por iteración y rendimiento por celda para una configuración física y numérica idéntica. Mantenga la misma malla, checkpoint, esquema, tolerancias y frecuencia de escritura al comparar MPI. Separe el tiempo de preparación/reconstrucción del tiempo efectivo de solver; no mezcle casos estacionarios con pasos transitorios.

El gestor limita casos simultáneos y cores al presupuesto configurado. La orientación de 30 000–100 000 celdas por rank es una heurística inicial: mida su equipo antes de elegir un perfil. Las mallas demostrativas de 108 104 y 155 824 celdas usan un rank en la prueba de entrega; no establecen un óptimo para un servidor empresarial.

## Evidencia y comparación

Los perfiles empíricos locales y los benchmarks del laboratorio aparecen cuando existen. Los ensayos históricos identifican su equipo y no sustituyen la lectura de hardware actual. Su ausencia es normal en una instalación limpia.

Las pruebas RANS de entrega comprueban continuidad y postproceso, sin acreditar convergencia científica. Para aceptar resultados revise fuerza, residuales, calidad/y+, estabilidad de producción y estudios espacial/temporal. La interpretación de una celda limitante de Courant requiere el diagnóstico de esa ejecución; un valor ausente no es cero.

## Instalación y mantenimiento

Conserve campos en disco Linux nativo, supervise RAM y espacio libre y evite sobresuscribir memoria con campañas paralelas. El retorno remoto conserva checkpoints y productos. Recalibre perfiles tras cambiar máquina, versión de solver, malla o configuración relevante. Consulte [ejecución](07_EXECUTION.md), [convergencia RANS](12_RANS_MESH_CONVERGENCE.md) y [ejecución remota](13_REMOTE_EXECUTION.md).
