# RAMair — Corrección integral de ejecución URANS, intentos, piloto, monitor y estabilidad

## 1. Objetivo

Corrige de forma integral el flujo URANS de RAMair para que:

1. Las etapas temporales de una ejecución puedan continuar de forma fiable sin confundir la continuación interna entre etapas con la reanudación de un intento anterior.
2. Los fallos queden clasificados y persistidos correctamente, sin intentos ni colas bloqueados falsamente en `RUNNING`.
3. El monitor en directo siga siempre el intento que realmente está ejecutándose y actualice sus datos durante piloto y producción.
4. El piloto sea un diagnóstico breve de viabilidad, no una aprobación obligatoria para lanzar producción.
5. Los casos e intentos tengan una identidad estable y legible, y el usuario pueda conservar, archivar o eliminar intentos anteriores coincidentes sin acumular versiones indefinidamente.
6. La evaluación de estabilidad distinga fallos numéricos reales de fallos de orquestación y proponga medidas justificadas mediante los logs existentes.

No te limites a modificar la interfaz. Sigue el flujo completo desde la acción de usuario hasta los comandos, manifiestos, registro de ejecuciones, cola, archivos del caso, logs y monitor.

## 2. Resultado esperado

Al terminar debe ser posible:

- Lanzar un caso URANS nuevo con política explícita de retención de intentos anteriores.
- Ejecutar su secuencia de etapas A–E sin que la etapa B falle con `RESUME_NOT_AVAILABLE` después de una etapa A correcta.
- Continuar un intento previo solo cuando exista un tiempo común y válido en todos los procesadores o un tiempo reconstruido válido.
- Ver en el monitor el piloto o producción actualmente activo, aunque cambie el intento activo dentro de una cola.
- Iniciar producción cuando un piloto haya terminado sin divergencia, sin aprobación manual, aprobación automática separada ni motivo escrito.
- Lanzar producción sin piloto mediante una confirmación simple; el piloto no será un gate obligatorio.
- Cerrar de manera coherente manifiesto, registro y cola ante éxito, divergencia, cancelación, fallo de preparación o excepción interna.
- Identificar cada configuración con un nombre legible y una clave científica estable, agrupando bajo ella sus intentos.
- Archivar o eliminar de forma segura intentos coincidentes, con previsualización y protección de datos activos o reiniciables.

## 3. Forma de trabajo obligatoria

Trabaja directamente sobre el estado actual del repositorio. Antes de editar:

1. Lee `AGENTS.md` y respeta todas sus instrucciones.
2. Inspecciona el árbol y localiza los archivos reales; no supongas que las rutas o números de línea de este documento siguen siendo exactos.
3. Revisa los documentos de contexto, estructura y cambios del proyecto que existan en el repositorio.
4. Comprueba el estado de Git y preserva cualquier cambio del usuario no relacionado.
5. Reconstruye el flujo de datos real entre interfaz, gestores, runners, manifiestos, registro y cola.
6. Añade primero pruebas de regresión que reproduzcan los fallos confirmados cuando resulte práctico.
7. Implementa cambios pequeños y cohesionados, manteniendo compatibilidad con datos antiguos mediante migración o normalización explícita.
8. Ejecuta pruebas unitarias y de integración sin iniciar simulaciones OpenFOAM largas ni destructivas salvo autorización expresa.

No ocultes excepciones, no conviertas fallos de infraestructura en divergencias físicas y no marques como resuelto un flujo que solo funciona en el caso feliz.

## 4. Límites y restricciones

- No ejecutes una campaña CFD completa ni una simulación costosa como parte de la verificación automática.
- No borres intentos existentes durante el desarrollo o las pruebas. Usa fixtures o directorios temporales.
- No cambies parámetros físicos, mallas o modelos de turbulencia globales sin evidencia y sin documentar el impacto.
- No uses `try/except: pass`, estados ambiguos ni reparaciones silenciosas.
- No mantengas dos implementaciones paralelas para archivar intentos; extiende la abstracción existente.
- No permitas que una etiqueta de interfaz sea la identidad persistente de un caso.
- No consideres un `SIGFPE` en el solver lineal como prueba suficiente de que ese solver es la causa raíz si los campos ya habían explotado.
- No exijas texto libre para desbloquear una ejecución.
- No alteres checkpoints RANS, definiciones compartidas de caso, mallas ni configuración común al aplicar retención a intentos URANS.

## 5. Evidencias confirmadas que debes reproducir y corregir

### 5.1. Producción nueva tratada incorrectamente como reanudación

Caso observado:

```text
run_id: closed_coarse_a08_dt3p1m5_pimple3_backward
attempt: production_attempt_003
requested/effective launch mode: FRESH
```

La etapa A se ejecuta correctamente con Euler y `deltaT = 7.8125e-6`. La etapa B se construye con `--resume` y falla antes de resolver:

```text
RuntimeError: RESUME_NOT_AVAILABLE: the case has no positive reconstructed or common processor time directory
```

En el caso y en cada directorio `processor*` solo existe el tiempo `0`. El `controlDict` usa aproximadamente:

```text
writeControl  runTime;
writeInterval 0.0195910026;
```

mientras que el final de la etapa A es aproximadamente:

```text
endTime 0.0001953125;
```

Por tanto, la etapa A no genera un estado positivo reiniciable antes de que la etapa B invoque reanudación.

El patrón de código que debe revisarse está en `CFD_2D/scripts/ramair_2d_validation_staged_runner.py`: la construcción de comandos usa conceptualmente una condición equivalente a:

```python
resume = bool(resume or previous_runtime or index > 0)
```

Esto mezcla dos conceptos diferentes:

- `resume_existing_attempt`: reanudar una ejecución previa desde disco.
- `continue_from_previous_stage`: continuar dentro del mismo intento recién lanzado.

Además, la escritura forzada al final de etapa se aplica al piloto, pero no de forma equivalente a las etapas A/B/C de producción.

### 5.2. Excepción secundaria que deja el estado corrupto

Tras una divergencia real de piloto aparece:

```text
TypeError: _update_run_manifest() got an unexpected keyword argument 'pilot_execution_status'
```

La firma existente de `_update_run_manifest(...)` acepta campos equivalentes a `pilot_status` y `execution_status`, pero una rama de fallo llama a la función con `pilot_execution_status`.

Consecuencias observadas:

- El informe del piloto puede decir `PILOT_DIVERGED`.
- El manifiesto de intento y el registro siguen en `RUNNING`.
- La cola permanece en `RUNNING`, fase `PILOT`.
- El caso activo o fijado deja de coincidir con la ejecución real.
- Se acumulan registros obsoletos y el monitor pierde la fuente correcta.

### 5.3. Divergencias numéricas reales

En `closed_coarse_a08_dt2p5m4_pimple3_backward/pilot/pilot_attempt_003` se observaron, entre otros:

```text
return code: 136
Courant max: ~1.99e103
continuity max: ~1.58e94
nuTilda max: ~2.07e81
terminal failure: SIGFPE in GAMGSolver pressure correction
```

Un caso medium del mismo `deltaT` presenta un patrón de magnitud comparable. El fallo de GAMG ocurre después de que las variables hayan crecido de forma no física; trátalo como síntoma terminal mientras no exista evidencia en sentido contrario.

La historia disponible también demuestra que no todas las divergencias coinciden con el cambio Euler → backward:

- Coarse, `deltaT = 2.5e-4`: falla en etapa C todavía con Euler.
- Fine, `deltaT = 2.5e-4`: falla en etapa C todavía con Euler.
- Medium, `deltaT = 2.5e-4`: completa C y falla en D con backward.
- Coarse, `deltaT = 1.25e-4`: llega a completar la etapa D con backward.
- Coarse, `deltaT = 3.125e-5`: un piloto escalonado A–D completa con `Co_max ≈ 10.47`, continuidad máxima del orden de `6.3e-13` y resultado `PILOT_WARN`/aceptado por usuario.

Conclusión que debe preservar la implementación: el `deltaT`, la malla, la inicialización y el escalado por etapas influyen; el cambio de esquema puede ser un desencadenante en algunos casos, pero no es una causa universal.

### 5.4. Monitor asociado a una ejecución antigua

En la página de convergencia/validación se selecciona conceptualmente:

```python
active_execution_id = pinned_run_id or active_run_id
```

antes de aplicar correctamente la opción de seguir la ejecución activa. Una ejecución fijada antigua puede ganar incluso cuando `follow active` está habilitado.

El fragmento periódico del monitor captura `case`, `row` y `mesh` desde el render exterior. Al avanzar la cola a otro intento, refresca el log anterior porque no vuelve a cargar y resolver el registro dentro de cada tick.

### 5.5. Acumulación de intentos y nombres ambiguos

Existe una función de archivado de intento, pero la interfaz opera principalmente intento por intento y no ofrece una política previa al lanzamiento para intentos científicamente equivalentes. El registro contiene numerosos intentos, incluidos estados `RUNNING` obsoletos, y algunos registros carecen de metadatos como `mesh_id`.

## 6. Diseño funcional requerido

### A. Separar lanzamiento, continuación entre etapas y reanudación

Introduce conceptos explícitos y tipados; evita que un booleano `resume` represente tres comportamientos.

Como mínimo, modela:

```text
attempt_launch_mode:
  FRESH | RESUME_EXISTING

stage_start_mode:
  FROM_INITIAL_STATE | CONTINUE_PREVIOUS_STAGE
```

Si los nombres deben adaptarse a contratos existentes, conserva la separación semántica.

#### A.1. Contrato FRESH

- Crea un intento nuevo y limpio.
- La primera etapa arranca desde el estado inicial o checkpoint RANS previsto.
- Nunca llama a `prepare_resume()` para la primera etapa.
- Las etapas siguientes continúan desde una salida garantizada de la etapa anterior.
- No reutiliza accidentalmente directorios temporales de un intento anterior.

#### A.2. Contrato de frontera entre etapas

Antes de lanzar una etapa que depende de la anterior:

1. Fuerza una escritura válida al final de la etapa previa (`writeNow`, ajuste temporal acotado de escritura u otro mecanismo robusto compatible con OpenFOAM 14).
2. Espera a que los procesos terminen correctamente.
3. Calcula el último tiempo positivo común a todos los `processor*`, o un tiempo reconstruido válido.
4. Verifica que el tiempo es finito, mayor que cero y consistente.
5. Persiste un `stage_checkpoint` con tiempo, origen, formato, etapa productora y resultado de validación.
6. Solo entonces permite `CONTINUE_PREVIOUS_STAGE`.

No dependas del `writeInterval` normal de producción si es mayor que la duración de una etapa de arranque.

#### A.3. Contrato RESUME_EXISTING

- Solo se ofrece o ejecuta si el intento elegido contiene un checkpoint reiniciable validado.
- Si no existe, la interfaz debe ofrecer `Start fresh attempt` y explicar el motivo.
- El backend debe rechazar una reanudación inválida con un error de precondición específico, sin clasificarla como divergencia.
- No conviertas automáticamente un FRESH fallido en RESUME.

#### A.4. Clasificación de fallos de etapa

Usa categorías persistentes, como mínimo:

```text
PREPARATION_FAILED
STAGE_CHECKPOINT_MISSING
RESUME_PRECONDITION_FAILED
SOLVER_DIVERGED
SOLVER_FAILED
CANCELLED
INTERRUPTED
COMPLETED
```

La ausencia de tiempo reiniciable entre A y B debe producir `STAGE_CHECKPOINT_MISSING` o categoría equivalente, no `REJECTED_SOLVER`.

### B. Actualizaciones de estado transaccionales y recuperación de cola

#### B.1. Eliminar la incompatibilidad de campos

Centraliza el esquema de actualización de manifiestos. No basta con cambiar una palabra en una llamada:

- Define una estructura o función única con parámetros nominales estables.
- Busca todas las llamadas a `_update_run_manifest` y todas las variantes de `pilot_status`, `pilot_execution_status` y `execution_status`.
- Valida valores mediante enums o constantes compartidas.
- Añade pruebas para todas las ramas terminales del piloto y producción.

#### B.2. Finalización coherente

Ante cualquier salida terminal, actualiza de manera consistente:

1. informe de etapa;
2. manifiesto de intento;
3. manifiesto del run/caso;
4. registro de ejecuciones;
5. elemento y estado global de la cola;
6. referencia activa del monitor.

Escribe archivos de forma atómica y ordenada. Si una escritura secundaria falla, conserva el error principal y registra el fallo de sincronización; no dejes el estado primario como `RUNNING`.

Implementa un finalizador idempotente que pueda ejecutarse más de una vez sin corromper contadores ni historial.

#### B.3. Reconciliación al iniciar la aplicación

Añade una rutina segura que detecte estados `RUNNING` sin proceso vivo o sin heartbeat reciente y los reconcilie como `INTERRUPTED`, `FAILED_ORCHESTRATION` o equivalente. Debe:

- no matar procesos;
- comprobar PID/identidad o señal de vida con el mecanismo disponible;
- preservar logs y checkpoints;
- registrar causa, hora y acción;
- actualizar cola, registro y manifiestos de forma idempotente;
- evitar que una cola antigua impida comenzar otra.

No marques automáticamente estos casos como divergencia.

### C. Monitor en directo correcto para piloto y producción

#### C.1. Regla de selección

La semántica debe ser inequívoca:

```text
if follow_active_execution:
    monitored_execution_id = registry.active_run_id
else:
    monitored_execution_id = user_pinned_run_id
```

Si no hay pin manual, permite una selección explícita del historial. Un pin antiguo nunca debe prevalecer sobre `follow active`.

#### C.2. Resolución dinámica en cada refresco

Dentro de cada tick periódico:

1. Vuelve a cargar el registro y el estado de cola desde disco.
2. Resuelve el intento activo actual.
3. Vuelve a resolver `run_root`, `case`, etapa, modo piloto/producción, malla y log.
4. Detecta cambios de `execution_id`, intento, etapa, ruta o inode.
5. Reinicia de forma segura el cursor incremental cuando cambia la fuente o el archivo se trunca.
6. Renderiza un encabezado visible con caso, intento, etapa, `deltaT`, esquema y última actualización.

No cierres sobre objetos obsoletos capturados por el render exterior.

#### C.3. Datos mínimos del monitor

Durante piloto y producción muestra, según disponibilidad:

- identidad y ruta relativa del intento;
- etapa actual y progreso temporal/pasos;
- tiempo físico, `deltaT` y esquema temporal;
- Courant medio/máximo;
- residuales iniciales/finales relevantes;
- errores de continuidad;
- extrema de `nuTilda` u otras variables de estabilidad ya analizadas;
- fuerzas/coefs si se están escribiendo;
- heartbeat y antigüedad del último dato;
- estado terminal y causa normalizada.

Si aún no existe `log.foamRun`, muestra `waiting for solver log` y sigue buscando; no cambies silenciosamente a otro intento.

#### C.4. Robustez del lector incremental

Prueba expresamente:

- mismo archivo con contenido añadido;
- archivo reemplazado con nuevo inode;
- archivo truncado conservando inode;
- cambio de etapa con log nuevo;
- cambio de intento por avance de cola;
- JSON parcialmente escrito;
- intento terminado mientras se refresca.

### D. Rediseñar el piloto como diagnóstico no bloqueante

#### D.1. Separar estado de ejecución, viabilidad y elegibilidad

Modela al menos:

```text
pilot_execution_status:
  NOT_RUN | RUNNING | COMPLETED | FAILED_TO_START | INTERRUPTED

pilot_viability:
  NOT_ASSESSED | NO_DIVERGENCE_OBSERVED | WARNING | DIVERGED

production_eligibility:
  ALLOWED | ALLOWED_WITH_WARNING | BLOCKED_ACTIVE_CONFLICT
```

Adapta nombres a los contratos actuales, pero no reutilices `PASS`, `ACCEPTED` y `RUNNING` para significados diferentes.

#### D.2. Política de lanzamiento

- `NO_DIVERGENCE_OBSERVED` habilita producción inmediatamente.
- `WARNING` también la habilita mostrando el diagnóstico.
- No se exige `USER_ACCEPTED`, aprobación manual ni motivo escrito.
- Si no se ejecutó piloto, permite producción directa mediante una confirmación simple y visible.
- Si el piloto no pudo arrancar por un fallo de preparación, permite producción directa con advertencia y confirmación simple.
- Si el piloto divergió explícitamente, muestra que no es viable por defecto. Si el proyecto conserva una anulación avanzada, debe ser una confirmación explícita y no requerir texto libre; registra que fue una anulación, no que el piloto pasó.
- La revisión manual puede mantenerse como anotación opcional, pero nunca como gate ordinario.

El único bloqueo absoluto de este bloque debe ser un conflicto operativo real, por ejemplo otro intento activo incompatible. No conviertas la ausencia de una aprobación en bloqueo.

#### D.3. Piloto representativo

Cuando exista una secuencia canónica de etapas de arranque A/B/C/D, el piloto debe usar esa secuencia acortada. No debe caer silenciosamente en una única etapa directa con `backward` y `deltaT` objetivo.

- Congela y persiste el plan de piloto por intento.
- Valida que contiene los cambios de `deltaT` y esquema previstos.
- Muestra el plan antes de ejecutar.
- Si falta o es incompatible, produce un error de configuración accionable o una política fallback documentada, nunca una sustitución silenciosa.
- Registra métricas por etapa para localizar si la inestabilidad precede o sigue al cambio de esquema.

#### D.4. Criterio de prueba rápida

La prueba rápida no pretende validar estadísticamente el URANS ni certificar convergencia física. Su resultado positivo debe significar únicamente:

```text
Durante la ventana observada no se detectaron señales configuradas de divergencia
ni un fallo fatal del solver.
```

Mantén umbrales configurables y registra los valores que justifican `WARNING` o `DIVERGED`. Evita declarar `stable` si solo significa `no divergence observed`.

### E. Identidad de caso y política de retención

#### E.1. Clave científica estable

Define una `case_key` canónica independiente de nombres de carpeta e índices de intento. Debe incluir como mínimo:

```text
mode = URANS
topology
mesh_id y huella/hash de malla
angle of attack
target deltaT
production time scheme
nOuterCorrectors
identificador o hash de configuración física/numérica compatible
```

Normaliza floats de forma determinista. Define qué cambios crean otra configuración —por ejemplo malla, `deltaT`, esquema, PIMPLE o física— y qué cambios solo crean otro intento —fecha, número de intento, notas o número de núcleos si no altera la solución—.

Migra o deriva la clave para registros antiguos sin renombrar destructivamente sus carpetas.

#### E.2. Nombre legible

Usa una etiqueta equivalente a:

```text
URANS · Closed/Coarse · α 8° · dt 2.50e-4 s · backward · PIMPLE outer=3
```

En selectores de intentos añade:

```text
Production attempt 003 · interrupted · 2026-08-… · last time …
Pilot attempt 003 · diverged · 2026-08-… · 200 steps
```

La interfaz debe agrupar piloto y producción bajo la configuración, sin presentarlos como configuraciones científicas distintas.

#### E.3. Política antes de crear un intento

Añade a ejecución individual y en serie una opción persistente por lanzamiento:

```text
Previous matching attempts:
  Keep all
  Archive previous matching attempts   [recommended default]
  Delete selected previous matching attempts
```

Antes de actuar, muestra una previsualización con:

- etiqueta y `attempt_id`;
- estado y fecha;
- etapa/tiempo/pasos alcanzados;
- tamaño aproximado en disco;
- si está activo, bloqueado o es reiniciable;
- acción propuesta y motivo de cualquier protección.

La coincidencia debe exigir igualdad de la `case_key` completa, no solo malla y `deltaT`.

#### E.4. Archivado

Extiende la función existente de archivado para admitir una selección o todos los intentos coincidentes:

- operación atómica por intento;
- colisiones de destino resueltas determinísticamente;
- manifiesto e índice de archivo;
- ruta de origen/destino, hora, identidad y política;
- actualización del registro;
- idempotencia ante reintento;
- posibilidad clara de recuperación.

El archivado no debe incluir la definición compartida, malla, checkpoint base RANS ni configuración común.

#### E.5. Eliminación segura

La eliminación debe ser una acción separada, explícita y conservadora:

- confirmación concreta de los intentos seleccionados; no requiere motivo escrito;
- nunca elimina un intento activo, con proceso vivo o bloqueado;
- no elimina por defecto un intento parcial reiniciable; exige selección individual adicional;
- resuelve y valida rutas absolutas dentro del directorio de intentos permitido;
- rechaza symlinks o escapes de ruta peligrosos;
- no usa globs amplios;
- actualiza el registro y escribe una tombstone/auditoría mínima;
- es idempotente frente a elementos ya ausentes;
- si el borrado parcial falla, informa exactamente qué se eliminó y qué se conservó.

Usa directorios temporales en las pruebas. No pruebes esta función sobre datos reales del usuario.

### F. Diagnóstico y mitigación numérica basada en evidencias

#### F.1. Informe automático por etapa

Genera o amplía un resumen estructurado que capture por etapa:

- esquema temporal y `deltaT` efectivo;
- duración y pasos;
- Courant medio, máximo y tendencia;
- residuales por ecuación;
- continuidad acumulada/local;
- min/max de variables críticas;
- primer indicador que cruza un umbral;
- instante del cambio de esquema;
- señal fatal y stack terminal;
- clasificación: configuración, orquestación, divergencia numérica, interrupción o desconocido.

Esto debe permitir comparar automáticamente si el crecimiento comienza antes o después de una transición.

#### F.2. Medidas recomendadas, no cambios físicos silenciosos

Tras arreglar primero la escritura y continuación de etapas, evalúa e implementa como opciones configurables y visibles, con valores conservadores y trazabilidad:

1. Reducir `deltaT` objetivo o escalarlo según Courant observado.
2. Prolongar las etapas Euler de arranque antes de activar `backward`.
3. Añadir uno o más escalones intermedios de `deltaT`.
4. Permitir que la transición a `backward` dependa de métricas de estabilidad durante una ventana, con límite máximo de extensión.
5. Aplicar rollback al último checkpoint sano y repetir con `deltaT` reducido cuando la política lo autorice.
6. Revisar relajación, correctores PIMPLE, tolerancias y solvers solo mediante perfiles explícitos y comparables.
7. Registrar toda adaptación para que el caso sea reproducible.

No hagas que el solver cambie de parámetros sin reflejarlo en el manifiesto, la etiqueta detallada y el informe final. No uses únicamente `Co_max < 1` como regla universal sin justificarla para este flujo; los logs existentes muestran un caso no divergente con un máximo superior, por lo que deben analizarse también tendencia, persistencia, residuales y límites de campos.

#### F.3. Experimentos mínimos posteriores

Deja preparados comandos o pruebas cortas, pero no los ejecutes sin autorización, para comparar al menos:

- mismo caso y malla con secuencia escalonada corregida;
- target `dt = 2.5e-4` frente a valores inferiores;
- transición Euler → backward temprana frente a prolongada;
- perfil PIMPLE actual frente a una variante conservadora;
- resultado con y sin adaptación de `deltaT`.

Cada comparación debe partir del mismo checkpoint compatible y producir un resumen máquina-legible.

## 7. Persistencia, esquema y compatibilidad

Actualiza el schema/versionado de manifiestos si corresponde. Añade normalización para registros antiguos y documenta defaults. Como mínimo, cada intento nuevo debería exponer de manera no ambigua:

```json
{
  "case_key": "stable-canonical-key",
  "attempt_id": "production_attempt_003",
  "attempt_kind": "production",
  "attempt_launch_mode": "FRESH",
  "stage_start_mode": "CONTINUE_PREVIOUS_STAGE",
  "execution_status": "RUNNING",
  "terminal_reason": null,
  "pilot_execution_status": "COMPLETED",
  "pilot_viability": "NO_DIVERGENCE_OBSERVED",
  "production_eligibility": "ALLOWED",
  "active_stage": "B",
  "stage_checkpoint": {
    "time": 0.0001953125,
    "producer_stage": "A",
    "valid": true
  },
  "heartbeat_at": "ISO-8601 timestamp",
  "retention_policy": "ARCHIVE_MATCHING"
}
```

No es obligatorio usar exactamente este JSON, pero sí conservar todas las distinciones semánticas. Los lectores antiguos deben fallar de forma clara o recibir datos normalizados; no dejes campos con significados contradictorios.

## 8. Pruebas obligatorias

### 8.1. Regresión de etapas

- FRESH: A finaliza, escribe tiempo positivo común y B continúa sin `RESUME_NOT_AVAILABLE`.
- FRESH sin checkpoint por fallo de escritura: B no se lanza y el intento termina como fallo de frontera/orquestación.
- RESUME_EXISTING con tiempo común válido: reanuda desde el tiempo esperado.
- RESUME_EXISTING sin tiempo válido: rechazo de precondición, no divergencia.
- Un tiempo presente solo en algunos procesadores no se acepta.
- La reconstrucción válida se acepta según el contrato definido.

### 8.2. Estados terminales

Parametriza éxito, warning, divergencia, fallo de preparación, fallo de solver, cancelación, interrupción y excepción del actualizador. Verifica consistencia entre:

- informe;
- manifiesto de intento;
- manifiesto de run;
- registro;
- cola;
- selección del monitor.

Incluye una prueba específica que habría detectado el keyword incorrecto `pilot_execution_status`.

### 8.3. Cola y reconciliación

- Una excepción en un elemento no deja la cola eternamente en `RUNNING`.
- La política configurada determina si la cola continúa o se detiene.
- Al reiniciar la aplicación, un intento huérfano se reconcilia sin borrar logs.
- Un proceso realmente vivo nunca se marca como huérfano.

### 8.4. Monitor

- `follow active` ignora un pin antiguo.
- modo manual respeta el pin.
- el avance de cola cambia de fuente en el siguiente tick.
- la transición piloto → producción cambia intento y encabezado.
- rotación, reemplazo y truncado de log no duplican ni pierden de forma silenciosa el stream.
- datos parciales no rompen la página.

### 8.5. Piloto

- `NO_DIVERGENCE_OBSERVED` habilita producción sin revisión.
- `WARNING` habilita producción con aviso.
- `NOT_RUN` permite producción tras confirmación simple.
- `FAILED_TO_START` permite producción tras advertencia y confirmación.
- `DIVERGED` no se transforma en aprobado; cualquier override queda auditado y no exige texto.
- el plan canónico escalonado se conserva y persiste.
- no se usa silenciosamente un único stage `backward` si existe plan A–D.

### 8.6. Retención

- La `case_key` es estable ante cambios cosméticos y cambia ante parámetros científicos relevantes.
- `Keep all` no mueve ni elimina.
- `Archive matching` actúa solo sobre la clave completa.
- un intento activo/locked queda protegido.
- un intento reiniciable requiere tratamiento conservador.
- archivado repetido es idempotente.
- eliminación valida confinamiento de ruta y rechaza escapes/symlinks.
- una operación parcial deja auditoría y registro coherentes.
- ejecución individual y en serie aplican la misma política.

### 8.7. Compatibilidad

- Registros antiguos sin `mesh_id`, `case_key` o campos nuevos se normalizan.
- Las vistas existentes de RANS y postproceso no sufren regresiones.
- Los selectores muestran solo ejecuciones reales donde corresponda, no definiciones o estados sintéticos.

## 9. Verificación manual acotada

Sin lanzar una simulación larga, verifica mediante fixtures o un runner simulado:

1. Crear un intento FRESH de cinco etapas.
2. Simular escritura de un tiempo común después de A.
3. Confirmar que B se configura como continuación interna, no reanudación externa.
4. Hacer avanzar una cola de piloto a producción y observar el cambio del monitor.
5. Simular una divergencia y comprobar que todos los estados quedan terminales.
6. Previsualizar archivo de dos intentos coincidentes y proteger uno activo.
7. Simular la reconciliación de un antiguo `RUNNING` sin proceso vivo.

Si el repositorio contiene pruebas de interfaz, añade cobertura a los controles y estados. Si no es viable automatizar una interacción visual, documenta una lista manual exacta y aporta cobertura a la lógica extraída fuera de la UI.

## 10. Entregables

Entrega cambios completos en código, migraciones y pruebas. Actualiza además la documentación técnica relevante con:

- modelo de estados;
- semántica FRESH/RESUME/continuación entre etapas;
- criterio real del piloto;
- política de selección del monitor;
- definición de `case_key`;
- política y seguridad de archivo/eliminación;
- interpretación de divergencia por etapas;
- comandos de pruebas cortas pendientes de autorización.

En tu respuesta final incluye:

1. Resumen de causas raíz confirmadas.
2. Archivos modificados y responsabilidad de cada cambio.
3. Migraciones o compatibilidad aplicada.
4. Pruebas ejecutadas con resultado exacto.
5. Pruebas no ejecutadas y motivo.
6. Riesgos restantes y siguientes experimentos CFD recomendados.
7. Confirmación explícita de que no se ejecutaron campañas largas ni se eliminaron datos reales.

## 11. Criterios de aceptación

La tarea no está terminada hasta que se cumpla todo lo siguiente:

- El caso reproducido ya no falla en B por ausencia de escritura al final de A.
- Un fallo al actualizar el manifiesto no puede dejar intento y cola falsamente activos.
- No quedan llamadas incompatibles a los actualizadores de estado.
- El monitor sigue el intento real durante piloto, producción y cambios de cola.
- Producción no depende de una aprobación del piloto ni de un motivo escrito.
- Una divergencia explícita conserva su significado y no se maquilla como aprobación.
- La identidad de casos diferencia de forma estable método, malla, ángulo, `dt`, esquema y PIMPLE.
- El usuario puede conservar, archivar o eliminar de forma segura intentos coincidentes desde ejecución individual y en serie.
- La causa `RESUME_NOT_AVAILABLE` se clasifica como orquestación/precondición cuando corresponda.
- El análisis de estabilidad informa la etapa y si el crecimiento comenzó antes o después del cambio de solver/esquema.
- Todas las pruebas relevantes pasan o cualquier excepción queda justificada con evidencia concreta.

## 12. Regla de parada

Si encuentras que los contratos reales del repositorio contradicen una premisa de este documento, no fuerces la implementación. Documenta la evidencia, conserva el objetivo funcional, adapta el diseño al contrato real y explica la decisión. Detente y solicita autorización únicamente si la solución requiere borrar datos reales, ejecutar una campaña CFD costosa o cambiar parámetros físicos globales de manera irreversible.
