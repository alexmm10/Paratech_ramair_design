# RAMair Validation Lab — implementación y verificación de convergencia RANS, solidez URANS, monitores y transiciones temporales

> Prompt de ejecución para Codex · revisión 2026-08-07  
> Proyecto: RAMair DESIGN APP · OpenFOAM Foundation 14 · Gmsh 4.15.2  
> Baseline observado: backend API 20 · Validation Lab schema 8 · solver config schema 13

## Resultado requerido

Audita e implementa una corrección integral, conservadora y verificable del **Validation & Convergence Lab** de RAMair para que:

1. la convergencia espacial RANS produzca análisis cuantitativos y gráficos profesionales, incluidos incrementos entre mallas y no solo valores absolutos;
2. la revisión URANS muestre únicamente ejecuciones físicas reales, con identidades inequívocas y metadatos temporales suficientes;
3. ParaView abra el intento y el tiempo correctos sin errores por valores `null`;
4. los intentos URANS `FRESH` y `RESUME`, las etapas A–E, los pilots y el estudio PIMPLE 2/3/4 sean coherentes y trazables;
5. los monitores RANS/URANS se registren antes del arranque y se actualicen durante la ejecución;
6. una cola URANS desatendida avance después de fallos locales, conserve toda la evidencia y se detenga únicamente ante un fallo global;
7. ninguna corrección destruya, reescriba o reclasifique silenciosamente resultados CFD existentes.

No des por resuelto un requisito porque aparezca en `CHANGELOG.md`. Contrasta cada afirmación con código activo, estado persistido, pruebas y evidencia de ejecución. Si ya está correctamente implementado, consérvalo y añade o ajusta solo la prueba que demuestre el contrato.

## Contexto que debes cargar antes de editar

Trabaja desde el repositorio canónico que corresponda al entorno:

- fuente Windows: `C:\Users\alejm\Desktop\PRACTICAS_INVICSA\3D design\DESIGN APP`;
- runtime WSL: `/home/alejm/ramair_cfd/DESIGN_APP`.

Lee en este orden y trata el contenido más reciente y el código activo como fuente de verdad:

1. `AGENTS.md`;
2. `PROJECT_CONTEXT_FOR_CODEX.md`;
3. `CHANGELOG.md`, especialmente 2026-08-03 y 2026-08-04;
4. `README_PROJECT_STRUCTURE.md`;
5. `CFD_2D/CFD_2D_TECHNICAL_SPECIFICATIONS.txt`;
6. `Documents and Manuals/CFD 2D/CODEX_VALIDATION_LAB_COMPLETE_RANS_URANS_SPACE_TIME_RESTRUCTURE.md`;
7. informes recientes de `CFD_2D/reports/` sobre URANS, portabilidad, ralentización, migración y smoke tests;
8. configuración, estado, código y pruebas enumerados en este prompt.

Inspecciona como mínimo:

```text
CFD_2D/app/ramair_cfd2d_app.py
CFD_2D/app/validation_convergence_page.py
CFD_2D/app/validation_plotting.py
CFD_2D/app/workflow_backend.py
CFD_2D/app/ramair_live_monitor.py

CFD_2D/scripts/ramair_2d_study_registry.py
CFD_2D/scripts/ramair_2d_execution_registry.py
CFD_2D/scripts/ramair_2d_validation_study.py
CFD_2D/scripts/ramair_2d_validation_staged_runner.py
CFD_2D/scripts/ramair_2d_openfoam_runner.py
CFD_2D/scripts/ramair_2d_validation_live_monitor.py
CFD_2D/scripts/ramair_2d_urans_attempts.py
CFD_2D/scripts/ramair_2d_urans_matrix_manager.py
CFD_2D/scripts/ramair_2d_urans_review.py
CFD_2D/scripts/ramair_2d_pimple_outer_study.py
CFD_2D/scripts/ramair_2d_rans_checkpoint_batch.py
CFD_2D/scripts/ramair_2d_rans_review.py
CFD_2D/scripts/ramair_2d_rans_full_postprocess.py
CFD_2D/scripts/ramair_2d_rans_paraview_final.py
CFD_2D/scripts/ramair_2d_closed_open_convergence_study.py
CFD_2D/scripts/ramair_2d_space_time_convergence.py
CFD_2D/scripts/ramair_2d_postprocess_registry.py

CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json
CFD_2D/app_state/validation_convergence_workspace.json
CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8/
CFD_2D/tests/test_validation_convergence_lab.py
CFD_2D/tests/test_validation_lab_complete_restructure.py
CFD_2D/tests/test_validation_lab_optional_pilot_monitor_contract.py
CFD_2D/tests/test_validation_lab_recovery_contract.py
CFD_2D/tests/test_validation_lab_six_rans_separation_resume_queue.py
CFD_2D/tests/test_validation_rans_batch_restart_contract.py
CFD_2D/tests/test_closed_open_convergence_study.py
```

Si alguno no existe o fue reemplazado, localiza el propietario actual mediante referencias/imports y registra la sustitución en el informe final. No crees un segundo sistema paralelo.

## Límites de autonomía y seguridad

Este encargo autoriza a inspeccionar el repositorio y los registros, editar el código/configuración/documentación dentro del alcance, migrar **metadatos** de manera no destructiva y ejecutar pruebas no destructivas.

No autoriza:

- ejecutar CATIA;
- iniciar una campaña CFD larga;
- ejecutar un solver OpenFOAM real sin una autorización explícita adicional del usuario en la sesión de implementación;
- borrar o sobrescribir historiales, checkpoints, campos, mallas o paquetes de `Results`;
- alterar de forma silenciosa modelo de turbulencia, propiedades físicas, condiciones de contorno, referencias de fuerzas, esquema temporal o definición de las seis mallas;
- fabricar resultados, tiempos físicos, CSV, imágenes, manifests, estados PASS o evidencia de convergencia.

Las migraciones deben ser atómicas, idempotentes, versionadas y, para datos históricos, preferentemente **metadata-only**. Cualquier acción de limpieza debe ser explícita, previsualizable y recuperable.

## Hechos y contratos que debes preservar

### Identidades canónicas

Las seis identidades deben permanecer visibles y ordenadas en toda tabla, cola y selector donde corresponda:

```text
closed_coarse
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

Una fila completada usa una acción como `SKIP_ALREADY_COMPLETED`; nunca desaparece para representar el skip.

Las mallas abiertas activas declaradas son:

| Mesh ID | Cells | Contract |
|---|---:|---|
| `open_coarse` | 223,080 | promoted, real `checkMesh=OK` |
| `open_medium` | 302,692 | preserved baseline |
| `open_fine` | 502,474 | promoted, real `checkMesh=OK` |

No sustituyas estas mallas, no restaures candidatos descartados y no regeneres Gmsh para resolver problemas de interfaz o registro.

### Separación entre gate automático y revisión humana

Un resultado RANS puede conservar un gate automático `NOT_CONVERGED` y, simultáneamente, una decisión trazable como:

```text
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
RANS_REVIEW_REQUIRED
RANS_REJECTED
```

La decisión humana no reescribe el gate ni los historiales. Un checkpoint revisado debe ser inmutable, estar ligado a la misma malla/física y registrar hashes de los campos necesarios.

### Intentos URANS

Mantén separadas estas entidades:

```text
case definition
pilot attempt
production attempt
PIMPLE sensitivity attempt
```

Cada ejecución mutable posee `case_id`, `run_kind`, `attempt_id` y `run_id`. Una definición `READY` o `PREPARED` no es una ejecución y no puede aparecer como resultado analizable.

### Configuración congelada

Cada batch debe guardar su configuración efectiva en `resolved_batch_config.json`. Cada caso recibe el snapshot inmutable correspondiente y un `applied_configuration_audit.json` que compara la selección de interfaz con los diccionarios realmente escritos.

No permitas que un rerun de Streamlit cambie los parámetros de una cola activa.

## Método de trabajo obligatorio

### 1. Reconstruye el estado real antes de modificar

Genera primero un inventario de solo lectura con:

- versiones API/schema observadas en código y archivos persistidos;
- identidades de las seis bases RANS y sus estados automático/revisado;
- definiciones URANS e intentos reales, con `deltaT`, esquema, correctores, pasos resueltos, último tiempo físico y terminal status;
- última cola URANS secuencial y su puntero antes/después del primer fallo;
- logs por etapa y segmento de los últimos intentos relevantes;
- rutas registradas para monitor, caso, `.foam`, `.OpenFOAM`, `.pvsm`, tiempo reconstruido y productos;
- tests actuales que cubren cada contrato;
- diferencias entre lo declarado en changelog y el comportamiento demostrable.

No edites todavía. Guarda la auditoría como informe compacto bajo `CFD_2D/reports/` solo cuando comience la implementación autorizada.

### 2. Formula hipótesis comprobables

Para cada fallo identifica:

```text
symptom
first bad state transition
owning module
evidence
root-cause hypothesis
minimal correction
regression test
runtime verification
```

No atribuyas un fallo a `backward`, PIMPLE, MPI o ParaView solo por proximidad temporal. Separa errores Python/orquestación, persistencia, construcción de comandos, estado, I/O y divergencia numérica real.

### 3. Implementa por capas

Orden recomendado:

1. identidad y modelo de estado;
2. `FRESH/RESUME` y transiciones A–E;
3. registro/monitor incremental;
4. cola y clasificación local/global;
5. selección/revisión URANS;
6. ParaView;
7. convergencia y gráficos RANS;
8. pilots y PIMPLE 2/3/4;
9. migraciones y documentación;
10. verificación integrada.

Tras cada capa ejecuta las pruebas focalizadas antes de continuar.

## Workstream A — convergencia espacial RANS

### A1. Elegibilidad y agrupación

Solo compara resultados con:

- mesh ID canónico;
- malla y física compatibles;
- campos y escalares reales;
- gate automático elegible o revisión humana que autorice explícitamente convergencia espacial;
- definición consistente de coeficientes, cuerda y referencias.

Separa `closed` y `open`. No mezcles superficies externas e internas de un perfil abierto ni combines resultados con normalizaciones distintas.

### A2. Magnitudes escalares

Para cada topología y para `Cl`, `Cd`, `Cm` y `Cl/Cd`, presenta:

- valor absoluto por nivel;
- diferencia firmada y absoluta `coarse → medium` y `medium → fine`;
- variación relativa respecto a la malla anterior;
- desviación respecto a la malla fine;
- celdas, `h_eff`, tiempo total y tiempo por iteración/paso cuando exista;
- clasificación de la tendencia: monotonic, oscillatory, divergent, insufficient data.

Usa:

```text
delta_abs(a→b) = phi_b - phi_a
delta_pct(a→b) = 100 * (phi_b - phi_a) / abs(phi_a)
```

Cuando `abs(phi_a) < eps_phi`, especialmente para `Cm`, no muestres porcentajes explosivos. Define `eps_phi` con unidades/escala documentadas y usa una de estas alternativas, etiquetándola:

```text
symmetric_pct = 200 * (phi_b - phi_a) / (abs(phi_a) + abs(phi_b))
absolute_difference_only
```

Nunca conviertas `NaN`, división por cero o dato ausente en cero.

### A3. Distribuciones superficiales

Cuando haya datos compatibles, compara por rama:

- `Cp(x/c)`;
- `Cf(x/c)` bruto y filtrado;
- `y+(x/c)`;
- separación y reattachment;
- diferencias interpoladas sobre una abscisa común;
- normas `L1`, `L2` y `L∞` respecto a la malla fine.

La interpolación no puede cruzar huecos, labios o ramas no conectadas. Conserva la señal original y registra método, dominio común y puntos descartados.

### A4. Separación de capa límite

Preserva `ramair-wall-separation-connectivity-v1`:

1. leer campos reales del patch de pared;
2. ordenar caras mediante conectividad de aristas y longitud de arco;
3. construir tangente local orientada;
4. proyectar `wallShearStress` cinemático sobre la tangente;
5. calcular `Cf = 2*tau_t/U_inf^2` con la convención del proyecto;
6. detectar cambio de signo con filtro, histéresis y persistencia documentados;
7. conservar eventos candidatos, separación y reattachment por rama;
8. no superar confianza `MEDIUM` sin corroboración de velocidad cerca de pared.

Para perfiles abiertos trata por separado exterior, interior y labio, sin puentes geométricos artificiales.

### A5. Gráficos mínimos

Genera figuras separadas y legibles, no un único panel saturado:

1. `Aerodynamic coefficients vs. cell count`;
2. `Aerodynamic coefficients vs. effective grid size`;
3. `Relative change between consecutive grids`;
4. `Difference from the fine-grid solution`;
5. `Accuracy–cost trade-off`;
6. `Surface-pressure convergence`;
7. `Skin-friction convergence`;
8. `Wall-resolution convergence`;
9. `Separation and reattachment convergence`;
10. `Surface-distribution error norms`;
11. GCI/observed-order figure solo si es matemáticamente defendible.

Todos los textos de las figuras deben estar en inglés. Requisitos visuales:

- títulos descriptivos que incluyan topología y magnitud;
- ejes con nombre, símbolo y unidad;
- leyendas no superpuestas;
- estilos y colores coherentes para coarse/medium/fine;
- marcadores distinguibles en escala de grises;
- grid sutil, DPI adecuado y `tight_layout`/layout sin clipping;
- escala logarítmica únicamente cuando sea matemáticamente apropiada;
- anotación clara de datos ausentes o no elegibles;
- exportación PNG y datos CSV/JSON trazables.

### A6. GCI y orden observado

No fuerces Richardson/GCI con tres nombres de malla. Antes exige:

- tres soluciones compatibles;
- tamaños efectivos y ratios reales;
- diferencias no degeneradas;
- régimen asintótico razonable;
- tratamiento válido de ratios no uniformes;
- tendencia compatible con el método.

Si no se cumplen, devuelve `GCI_NOT_APPLICABLE` con motivos. No inventes orden de convergencia ni uses solo el número de celdas sin declarar cómo se obtiene `h_eff`.

### A7. UI y ejecución

Debe existir una acción inequívoca para generar/actualizar el análisis espacial tras validar o aceptar las bases. Muestra:

- timestamp y versión del último cálculo;
- mallas incluidas/excluidas y causa;
- archivos producidos;
- estado parcial si falta alguna malla;
- acceso a figuras y datos sin necesidad de abrir ParaView.

## Workstream B — selector y revisión URANS

### B1. Selector basado en intentos reales

El selector de revisión debe listar únicamente intentos que hayan resuelto al menos un paso físico real y posean evidencia coherente de ejecución.

Excluye:

```text
READY
PREPARED
case definitions
dry-runs
pilots del selector de producción
PIMPLE sensitivity cases del selector de producción
pre-solver failures with zero solved steps
synthetic or migrated placeholders without physical evidence
```

No excluyas un intento real solo por ser parcial, fallido o detenido. Muéstralo con su estado correcto.

### B2. Etiqueta estable

Cada opción debe permitir identificar el caso sin abrir manifests. Formato orientativo:

```text
Closed · Medium · dt 1.30e-4 s · backward · outer 3 · partial · 2,184 steps · production_attempt_002
```

Incluye, cuando existan:

- topología y nivel;
- `deltaT` objetivo y efectivo;
- esquema temporal de la etapa final;
- `nOuterCorrectors`;
- estado terminal;
- pasos demostrados y último tiempo físico;
- `attempt_id` corto.

Ordena por timestamp real descendente y usa una identidad estable como valor interno; nunca el label como clave.

### B3. Diagnóstico de revisión

La revisión debe distinguir:

- convergencia iterativa por paso;
- boundedness y estabilidad numérica;
- suficiencia de settling/sampling;
- estacionariedad de medias y RMS;
- periodicidad y frecuencia dominante;
- completitud del intento;
- causa de terminación.

No declares una producción científicamente válida porque un pilot avanzó o porque el proceso devolvió código 0.

## Workstream C — ParaView y postproceso

### C1. Corrige el origen de valores `null`

Traza el valor nulo desde UI hasta backend y registro. Valida explícitamente antes de construir comandos:

- `run_id`, `attempt_id` y `run_kind`;
- ruta del caso;
- ruta del artefacto registrado;
- último tiempo válido;
- estado de reconstrucción MPI;
- existencia y contenido del `.foam`, `.OpenFOAM` o `.pvsm`;
- disponibilidad de `pvpython`, `pvbatch` o ParaView GUI.

No conviertas `None` en la cadena `"None"`, no concatenes rutas nulas y no selecciones un caso por fallback implícito.

### C2. Semántica del botón

En revisión RANS, el botón debe abrir el archivo registrado del último estado SIMPLE válido de esa ejecución. En revisión URANS, debe abrir el intento seleccionado y el último tiempo físico reconstruido o disponible.

Si el caso está descompuesto:

- no asumas que existe reconstrucción;
- informa si puede reconstruirse;
- usa una acción explícita y acotada para reconstruir cuando proceda;
- no alteres el estado científico al preparar visualización.

La apertura debe usar rutas absolutas correctamente citadas/escapadas y no depender del directorio actual. Registra comando, artefacto y tiempo elegidos.

### C3. Fallo visible y accionable

Si ParaView no puede abrirse, devuelve un error estructurado con:

```text
error_code
selected_run_id
artifact_path
latest_time
reconstruction_state
missing_requirement
recommended_action
```

No muestres un traceback genérico ni marques postproceso como completado.

## Workstream D — `FRESH`, `RESUME` y modelo de intentos

### D1. Invariantes

`FRESH` significa:

- crear un nuevo `production_attempt_NNN`;
- partir del checkpoint RANS compatible;
- copiar los campos de restart a `0/`;
- no llamar `prepare_resume()`;
- no añadir `--resume`;
- comenzar en Stage A;
- no poseer tiempo físico positivo antes de resolver.

`RESUME` significa:

- continuar exactamente un intento parcial existente;
- exigir tiempo físico positivo real o reconstruible;
- preservar logs/segmentos anteriores;
- no copiar de nuevo el checkpoint RANS sobre el intento;
- no crear pasos duplicados ni reiniciar en `t=0`;
- continuar desde el primer stage incompleto.

La existencia de `case/`, `run_case.sh`, manifest, logs o directorios `processorN` por sí sola no autoriza resume.

La combinación siguiente es siempre un bug interno:

```text
execution_intent = FRESH
runner argument = --resume
```

### D2. Preflight único

Implementa una resolución centralizada de intención que produzca un objeto auditable antes del spawn:

```json
{
  "requested_intent": "FRESH|RESUME",
  "effective_intent": "FRESH|RESUME|BLOCKED",
  "attempt_id": "...",
  "positive_time_available": true,
  "reconstruction_required": false,
  "checkpoint_copy_allowed": false,
  "start_stage": "A",
  "reason_code": "..."
}
```

UI, backend y runners deben consumir la misma decisión; no re-inferirla de manera distinta.

## Workstream E — auditoría de transiciones A–E

### E1. Línea temporal

Reconstruye para cada intento relevante:

```text
Stage A — Euler, 0.25 × target dt
Stage B — Euler, 0.50 × target dt
Stage C — Euler, 1.00 × target dt
Stage D — backward, settling
Stage E — backward, sampling
```

No confíes únicamente en el manifest planificado. Extrae configuración aplicada, primer/último paso, tiempos escritos, `deltaT`, exit code, señal, log y directorios de tiempo reales por stage/segmento.

### E2. Hipótesis que debes discriminar

Determina si el punto repetido de fallo proviene de:

- cambio de esquema `Euler → backward`;
- falta de los niveles temporales anteriores requeridos;
- escritura tardía o incompleta de los últimos tiempos de Stage C;
- reescritura incorrecta de `controlDict`, `fvSchemes` o `fvSolution`;
- `startFrom`, `startTime`, `latestTime`, `endTime` o `deltaT` inconsistentes;
- reset involuntario a `t=0`;
- reconstrucción/descomposición MPI;
- campos incompatibles o incompletos;
- clasificación incorrecta de banner SIGFPE frente a FPE numérico real;
- excepción Python o estado de registry;
- divergencia CFD real.

Compara varios intentos solo después de normalizar el evento por stage, paso y tiempo físico.

### E3. Condición para cambiar de esquema

No sustituyas `backward` solo porque varios casos terminen cerca de la transición. Primero demuestra que:

- el estado se transfiere correctamente;
- existen tiempos consecutivos suficientes;
- los diccionarios aplicados son los esperados;
- el primer paso de Stage D falla dentro del solver y no en la orquestación;
- el mismo estado avanza con una alternativa controlada.

Solo entonces ejecuta —si el usuario autoriza solver real— una comparación acotada y reproducible:

```text
A. baseline A/B/C → backward
B. A/B/C → short Euler bridge → backward
C. A/B/C → CrankNicolson 0.9
D. Euler over the same diagnostic interval
```

Mismo checkpoint, malla, `deltaT`, duración, MPI y controles PIMPLE. Compara estabilidad, fase, amplitud, Courant, residuales, fuerzas y coste. Que una alternativa avance unos pasos no la convierte automáticamente en nuevo baseline.

## Workstream F — pilots y PIMPLE 2/3/4

### F1. Propósito del pilot

El pilot responde únicamente: “¿el caso puede avanzar de forma acotada sin divergencia inmediata?”. No exige convergencia estadística, PSD estable ni campaña completa.

Clasificación:

- `PILOT_PASS`: avanza lo previsto, sin NaN/FPE real/runaway y dentro de límites de seguridad;
- `PILOT_WARN`: avanza y permanece acotado, pero presenta Courant/residuos/continuidad elevados o variaciones que requieren revisión;
- `PILOT_FAIL`: no avanza, diverge, genera NaN/FPE real, campos incompatibles o runaway demostrado.

Un retorno cero es evidencia necesaria, no suficiente. Un Courant alto no convierte automáticamente un caso que avanza de forma acotada en `FAIL`; debe quedar como warning con métricas.

### F2. Bypass

El pilot es recomendado, no obligatorio. La producción sin pilot debe requerir confirmación explícita y almacenar:

```text
bypass = true
confirmation timestamp
optional note
user/session provenance
```

La nota no debe ser obligatoria. El bypass nunca crea `PILOT_PASS` ni habilita `RESUME`.

### F3. Estudio `nOuterCorrectors = 2/3/4`

La UI debe permitir, para el `deltaT` y checkpoint seleccionados:

```text
Run pilot for outer=2
Run pilot for outer=3
Run pilot for outer=4
Run pilots for all three
```

Cada variante parte de un clon independiente del mismo checkpoint RANS, malla, física y `deltaT`. No encadenes 2 → 3 → 4. Registra configuración aplicada y compara:

- avance y estabilidad;
- residuales por corrector/paso;
- continuidad y Courant;
- medias/RMS y frecuencia cuando haya ventana suficiente;
- tiempo por paso y coste total.

No presentes preparación como ejecución ni ausencia de pilot como resultado fallido del solver.

## Workstream G — monitores en directo

### G1. Registro antes del spawn

Antes de lanzar el proceso:

1. crea/upsert del intento concreto;
2. fija `PREPARING` con identidad y rutas;
3. crea manifiesto y paths de logs/monitores;
4. persiste atómicamente;
5. lanza el proceso;
6. transiciona a `RUNNING` al demostrar inicio.

Un pre-solver failure debe quedar asociado al intento y no convertirse en una definición genérica.

### G2. Flujo incremental

Los logs deben escribirse sin buffering perceptible. El parser:

- lee incrementos mediante byte offset/inode/segment identity;
- tolera truncación o rotación;
- no relee archivos completos en cada refresco;
- deduplica puntos por identidad física, no solo por texto;
- combina segmentos de resume sin borrar historia;
- persiste el último offset de forma atómica;
- no bloquea Streamlit con lectura volumétrica.

El monitor debe aparecer desde `PREPARING → RUNNING`, no al terminar.

### G3. Datos mostrados

Para RANS:

- SIMPLE iteration absoluta;
- residuos en eje logarítmico;
- `Cl`, `Cd`, `Cm` y `Cl/Cd`;
- stage/segmento, tiempo por iteración y estado;
- continuidad registrada para gate/tabla, aunque no sea gráfico principal;
- Courant como `NOT_APPLICABLE_TO_RANS`.

Para URANS:

- stage A–E, segmento y paso;
- tiempo físico y `deltaT`;
- `Co_mean` y `Co_max`;
- residuos y continuidad;
- `Cl`, `Cd`, `Cm`, `Cl/Cd`;
- pasos completados/planificados y tiempo por paso.

La interfaz debe seguir automáticamente el intento activo al pasar al siguiente caso de una cola, salvo que el usuario haya fijado manualmente otro intento. El pin es visual y no cambia el registro ni la cola.

### G4. Reglas de actualización

- Un refresco de Streamlit jamás lanza solver, pilot, postproceso o migración.
- Las actualizaciones terminales son acumulativas: campos omitidos no borran timestamps, paths o historiales ya registrados.
- Un stop del usuario con `writeNow` conserva estado y se registra `STOPPED_PARTIAL` cuando procede.
- La ausencia temporal de un punto nuevo no debe marcar el monitor como roto.

## Workstream H — cola URANS robusta y desatendida

### H1. Orden y aislamiento

Ordena los casos seleccionados por `deltaT` descendente. Cada caso comienza desde su propio checkpoint RANS compatible, no desde otra ejecución URANS.

### H2. Clasificación de fallos

Continúa automáticamente tras fallos locales:

```text
numerical divergence of one case
case-specific timeout
case setup/configuration error
missing output of one attempt
postprocess failure of one attempt
```

Detén la cola ante fallos globales demostrados:

```text
OpenFOAM environment unavailable
global disk exhaustion or write failure
MPI runtime/service failure affecting subsequent cases
corrupt shared registry that cannot be recovered atomically
explicit user stop of the batch
```

No clasifiques una excepción genérica como global sin comprobar su alcance.

### H3. Finalización de un item

Después de cualquier estado terminal local:

1. solicita escritura/terminación limpia cuando sea posible;
2. conserva logs, campos y estado parcial;
3. finaliza procesos MPI hijos y libera locks/leases del intento;
4. actualiza estado, causa y recomendación;
5. avanza atómicamente el puntero;
6. registra el siguiente intento antes de lanzarlo;
7. continúa sin intervención.

Haz idempotente la recuperación tras reinicio de la app. Un item terminal no se relanza y un item parcial no se duplica.

### H4. Tabla de seguimiento

Muestra al menos:

```text
mesh
deltaT
attempt
execution intent
pilot/bypass
stage
solved steps
physical time
status
cause code
recommended action
started/updated/ended timestamps
```

La causa debe ser específica: primer stage/segmento fallido, excepción o señal, último paso válido y evidencia enlazada.

## Modelo de estados mínimo

Usa enums/constantes centralizados; no disperses strings incompatibles entre UI y scripts.

### Ejecución

```text
DEFINED
PREPARED
PREPARING
RUNNING
STOP_REQUESTED
STOPPED_PARTIAL
COMPLETED
FAILED_LOCAL
FAILED_GLOBAL
TIMED_OUT_LOCAL
BLOCKED
ARCHIVED
```

### Resume

```text
FRESH_AVAILABLE
RESUME_AVAILABLE
RESUME_NOT_AVAILABLE
BLOCKED_INCOMPATIBLE_CHECKPOINT
BLOCKED_MISSING_RANS_CHECKPOINT
```

### Pilot

```text
PILOT_NOT_RUN
PILOT_RUNNING
PILOT_PASS
PILOT_WARN
PILOT_FAIL
PILOT_BYPASSED
```

Si el sistema actual usa nombres distintos pero semánticamente correctos, no migres por estética. Documenta el mapping y conserva compatibilidad.

## Persistencia y compatibilidad

### Escritura atómica

Para JSON/CSV de estado:

- escribir temporal en el mismo filesystem;
- flush/fsync cuando el contrato lo requiera;
- rename atómico;
- mantener última copia válida o backup pequeño;
- validar schema antes de sustituir;
- aplicar locking/lease de intento.

### Migración histórica

Una migración puede normalizar mesh IDs, añadir campos derivados y vincular identidades cuando la evidencia es inequívoca. Debe conservar:

- ruta original;
- valor original;
- regla de migración;
- timestamp;
- versión;
- resultado y ambigüedades.

Los históricos sin una de las seis mallas actuales pueden conservarse como legacy, pero no deben aparecer como ejecución analizable canónica salvo identificación demostrable. No los borres para eliminar warnings.

### API y schemas

Incrementa API/schema únicamente ante un cambio de contrato persistido o interfaz backend incompatible. Si lo haces:

- migra versiones anteriores;
- actualiza UI y backend juntos;
- actualiza fixtures, documentos y tests;
- verifica carga idempotente;
- no reescribe datos pesados.

## Pruebas obligatorias

### Unitarias y de contrato

Añade o ajusta pruebas que demuestren:

1. seis filas RANS siempre visibles y `closed_coarse` no eliminada;
2. cálculo de diferencias absolutas/relativas y fallback cerca de cero;
3. figuras en inglés, sin warnings de layout y con archivos no vacíos;
4. GCI suprimido con explicación cuando no aplica;
5. selector URANS excluye definiciones/pilots/dry-runs y conserva intentos reales parciales;
6. labels contienen mesh, `deltaT`, esquema, outer, pasos e intento;
7. ParaView rechaza paths nulos con error estructurado;
8. ParaView RANS usa último SIMPLE válido y URANS último tiempo físico;
9. `FRESH` jamás llama resume ni contiene `--resume`;
10. `RESUME` exige tiempo positivo y no recopia checkpoint;
11. resume desde `processorN` se resuelve de forma explícita;
12. transición C→D conserva historia temporal y diccionarios aplicados;
13. banner SIGFPE normal no se clasifica como FPE numérico;
14. monitor se registra en `PREPARING` antes del spawn;
15. parser incremental tolera append, rotación y resume segments;
16. update terminal no borra paths/historias previas;
17. pin manual y follow-active funcionan de forma independiente;
18. pilot diferencia PASS/WARN/FAIL sin exigir estacionariedad;
19. bypass no crea PASS ni RESUME y la nota es opcional;
20. pilots PIMPLE 2/3/4 se clonan desde el mismo checkpoint;
21. cola ordena `deltaT` descendente;
22. fallo local avanza al siguiente item;
23. fallo global detiene la cola;
24. reinicio de app no duplica intentos terminales/parciales;
25. migración histórica es idempotente y no toca datos pesados;
26. postproceso de pared conserva ramas y señal bruta;
27. separación no supera confianza MEDIUM sin corroboración;
28. no se muestran resultados sintéticos ante datos ausentes.

Usa fixtures pequeños, logs realistas y directorios temporales. No simules el éxito creando campos OpenFOAM vacíos.

### Pruebas focalizadas

Ejecuta primero los módulos directamente afectados, por ejemplo:

```bash
python -m pytest -c "Application Support/Tests/pytest.ini" \
  CFD_2D/tests/test_validation_lab_optional_pilot_monitor_contract.py \
  CFD_2D/tests/test_validation_lab_six_rans_separation_resume_queue.py \
  CFD_2D/tests/test_validation_rans_batch_restart_contract.py \
  CFD_2D/tests/test_closed_open_convergence_study.py -q
```

Adapta la lista si el inventario identifica otros propietarios.

### Suite completa

Antes de terminar:

```bash
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python "Application Support/Tools/check_project_context.py"
```

Ejecuta además compilación/import checks y las verificaciones de configuración que ya use el proyecto.

### Verificación UI

Con el runtime sincronizado y sin iniciar solver:

- abre la app oficial;
- revisa desktop y viewport estrecho;
- comprueba tablas, labels, estados y acciones;
- verifica que monitores vacíos/parciales no rompan renderizado;
- prueba selección de intentos, pin/follow y errores de ParaView mediante fixtures/paths controlados;
- conserva capturas o informe de evidencia.

### Verificación CFD real acotada

No la ejecutes sin autorización explícita. Si el usuario la autoriza, limita alcance y timeout, usa una copia/attempt nuevo y demuestra únicamente:

- un FRESH que entra en A sin resume;
- una parada limpia con tiempo positivo;
- un RESUME que continúa sin copiar de nuevo el checkpoint;
- transición C→D o el punto exacto de fallo;
- actualización del monitor durante el proceso;
- avance de una cola tras un fallo local controlado.

No presentes este smoke como validación aerodinámica ni convergencia temporal.

## Criterios de aceptación

La tarea está terminada solo si se cumple todo lo siguiente:

- cada requisito tiene evidencia de código y prueba;
- el selector URANS contiene solo intentos físicos reales;
- `FRESH` y `RESUME` no pueden confundirse en ninguna capa;
- la causa de la transición A–E está demostrada o queda como incertidumbre acotada con siguiente prueba exacta;
- el monitor aparece antes del final y actualiza el intento activo;
- la cola conserva evidencia y continúa tras fallos locales;
- ParaView abre el artefacto correcto o devuelve diagnóstico estructurado, nunca un `null` genérico;
- la convergencia RANS incluye valores absolutos, incrementos consecutivos, referencia fine, coste y distribuciones;
- gráficos y ejes están en inglés y pasan revisión visual;
- GCI y separación respetan sus límites científicos;
- no se han perdido ni sobrescrito resultados históricos;
- API/schemas, changelog y contexto son coherentes;
- las pruebas focalizadas y completas pasan, o cada fallo preexistente queda demostrado y separado;
- no quedan procesos solver/MPI/ParaView huérfanos iniciados por la verificación.

No declares “completado” basándote solo en que compila, en tests mockeados o en que el changelog ya lo afirma.

## Entregables

Entrega cambios pequeños y revisables. Actualiza cuando proceda:

- código y pruebas;
- `CHANGELOG.md` para comportamiento visible;
- `PROJECT_CONTEXT_FOR_CODEX.md` si cambia un contrato, schema, path, estado o limitación;
- documentación técnica directamente afectada;
- un informe de auditoría/verificación bajo `CFD_2D/reports/`.

El informe final de Codex debe seguir exactamente esta estructura:

### 1. Outcome

Qué quedó corregido y qué sigue bloqueado.

### 2. Root causes

Tabla con síntoma, causa demostrada, evidencia y corrección.

### 3. Changes made

Archivos y contratos cambiados, incluyendo migraciones.

### 4. Preserved behavior and data

Datos, física, rutas y contratos deliberadamente no modificados.

### 5. Validation evidence

Comandos, resultados, tests, revisión UI y cualquier smoke autorizado.

### 6. CFD interpretation limits

Qué conclusiones son de software, estabilidad numérica, convergencia o validación científica; no las mezcles.

### 7. Remaining risks and next action

Riesgo, impacto, evidencia faltante y acción mínima concreta.

## Regla final

Preserva la funcionalidad existente, las rutas, los outputs y el comportamiento visible que no estén expresamente dentro del alcance. No elimines ni desactives una capacidad requerida para hacer pasar las pruebas. Antes de finalizar, revisa el diff completo, ejecuta la validación relevante y comprueba que la evidencia respalda cada afirmación.
