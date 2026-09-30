# Ejecución remota Linux / WSL y recuperación

## Preparar

La aplicación empaqueta casos seleccionados con orden congelado, modo RANS-only/RANS+URANS/transitorio, fases y recursos. El ZIP contiene scripts Python/Bash, configuración, casos y manifiesto de hashes/versión/commit; no OpenFOAM, licencias ni un entorno Python instalado. Extraer en disco Linux nativo, con suficiente espacio para campos y copia de retorno.
Se ha probado Foundation 14, MPI y Python 3.10 en Ubuntu 22.04. El servidor de la empresa debe disponer de esa pila o verificar explícitamente otra. Instale OpenFOAM fuera del paquete y cargue su bashrc; los scripts reconocen /opt/openfoam14 y ~/.local/opt/openfoam14. Internet es necesario para Python salvo wheelhouse incluido con --download-wheelhouse o --wheelhouse-dir.

## Ejecutar

- `bash run_remote.sh`: despacho idempotente de entradas pendientes; no repite automáticamente entradas completadas.
- `bash monitor_remote.sh`: estado y registros.
- `bash stop_remote.sh`: escritura y parada recuperable. `force_stop_remote.sh` es la escalada si la parada limpia no responde.
- `bash resume_remote.sh`: continuación explícita, incluso de un checkpoint cuyo proceso anterior terminó. En RANS-only extiende iteraciones y nunca inicia URANS. La renumeración está deshabilitada por defecto.
- `bash postprocess_remote.sh`: productos en Postprocess de cada caso, conservando su carácter diagnóstico cuando corresponda.
- `bash collect_results.sh`: ZIP de retorno con campos, historias, postproceso, estados, logs y hashes.

La primera ejecución crea .venv-remote con las versiones fijadas. Stop/status/collect solo requieren Python estándar. Un límite de tiempo solicita escritura antes de escalar la detención. Registros remotos contienen rutas del servidor como procedencia.

## Devolver

Importe el ZIP en la aplicación principal con el workspace original registrado. Se comprueban paths seguros, ausencia de enlaces simbólicos, tamaños y SHA-256. Se rechaza sustituir un solver activo. La ejecución anterior se archiva en Data Archive/Remote Imports; las rutas JSON se reubican y los monitores se reconstruyen con logs/fuerzas reales. Un archivo de retorno incompleto no se acepta.
La prueba de entrega usó un directorio WSL separado, continuación 42→62→82, pausa limpia, malla inalterada y retorno/importación de resultados PROCESSED_PARTIAL. El servidor real de la empresa sigue siendo una comprobación de recepción.
