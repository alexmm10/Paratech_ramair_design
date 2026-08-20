# PROMPT DE CORRECCIÓN PARA CODEX
## Monitores RANS/URANS, flujo URANS completo, pilot opcional, estados RANS, postproceso y simplificación visual

Actúa como **ingeniero senior de CFD, OpenFOAM, aerodinámica estacionaria/transitoria, Python, Streamlit y arquitectura de software científico**.

Debes corregir la implementación real del **Validation & Convergence Lab** existente. No crees una aplicación paralela, no reescribas desde cero el laboratorio y no des por implementado un comportamiento únicamente porque figure en el `CHANGELOG.md`.

La tarea debe partir del código y de los datos reales presentes en el repositorio.

---

# 0. Fuentes obligatorias y precedencia

Lee antes de modificar código:

1. `CHANGELOG.md`
2. `PROJECT_CONTEXT_FOR_CODEX.md`
3. `README_PROJECT_STRUCTURE.md`
4. `AGENTS.md`
5. `Documents and Manuals/CFD 2D/CODEX_VALIDATION_LAB_COMPLETE_RANS_URANS_SPACE_TIME_RESTRUCTURE.md`
6. Los prompts de recuperación posteriores archivados en `Documents and Manuals/CFD 2D`.
7. Los scripts, registros, manifiestos, tests y casos activos del laboratorio.

Estado documental más reciente:

```text
Backend API:                 19
Validation Lab schema:       7
General solver schema:       13
OpenFOAM:                    Foundation 14
Workspace:
  CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8
```

El `CHANGELOG.md` declara ya implementados:

- intentos URANS separados para pilot y producción;
- registro previo al lanzamiento;
- monitor global;
- navegador diferido de productos mediante `postprocess_manifest.json`;
- análisis RANS con residuos y coeficientes;
- API 19 y schema 7;
- eliminación de `use_container_width`.

Sin embargo, el usuario observa fallos reales en esas funciones. Por tanto:

```text
el código ejecutable y la evidencia real tienen prioridad sobre el changelog
```

Antes de editar, clasifica cada función como:

```text
IMPLEMENTED_AND_WORKING
IMPLEMENTED_BUT_BROKEN
PARTIALLY_IMPLEMENTED
DOCUMENTED_ONLY
MISSING
```

No marques la tarea como terminada sin comprobar visual y funcionalmente los flujos afectados.

---

# 1. Restricciones de seguridad y preservación

No debes:

- borrar simulaciones RANS o URANS existentes;
- borrar checkpoints;
- borrar `Results`;
- sobrescribir intentos existentes;
- crear CSV vacíos;
- fabricar estados PASS;
- cambiar malla, geometría o física;
- ejecutar una campaña CFD larga;
- ejecutar CATIA;
- convertir un caso no convergido en auto-convergido;
- mezclar datos de pilot y producción;
- usar Courant como criterio físico RANS;
- perder historiales anteriores al reanudar o extender.

Puedes:

- corregir UI, backend, parsers, estado y rutas;
- ejecutar tests;
- hacer dry-run/preflight;
- ejecutar una prueba OpenFOAM real, corta y acotada con `closed_coarse`, porque el usuario la solicita explícitamente;
- archivar intentos temporales creados durante la verificación.

---

# 2. Objetivos principales

Implementar y verificar:

1. monitor global funcional para RANS, pilot URANS, producción URANS y sensibilidad PIMPLE;
2. transición correcta del monitor activo al cambiar de RANS a URANS;
3. flujo URANS completo:
   - crear/seleccionar definición;
   - crear intento pilot;
   - ejecutar pilot;
   - revisar pilot;
   - aprobar o ignorar pilot;
   - ejecutar producción;
   - reanudar producción;
4. pilot recomendable pero **no obligatorio**;
5. estimación automática de tiempo basada en medición real, sin calculadora manual de tiempo/memoria;
6. extensión manual RANS de 2.500 iteraciones sin límite de 20.000;
7. clasificación correcta de estados RANS y propagación de la aceptación;
8. eliminación de la subsección pilot duplicada;
9. cola URANS secuencial desatendida y reanudable;
10. corrección del postproceso RANS completo;
11. reconstrucción correcta de historiales de residuos y tiempo tras extensiones;
12. simplificación de la UI RANS a dos desplegables útiles;
13. eliminación del warning Matplotlib de `constrained_layout`.

---

# 3. Auditoría inicial obligatoria

Antes de modificar:

```bash
git status
git diff
```

Inspecciona:

```text
BACKEND_API_VERSION
EXPECTED_BACKEND_API_VERSION
validation schema version
migrations
execution_registry
review_registry
postprocess_registry
active run state
closed_coarse RANS review/checkpoint
existing closed_coarse URANS case definitions
existing pilot attempts
existing production attempts
```

Localiza todas las rutas y funciones relacionadas con:

```text
global monitor
active_run_id
run_kind
attempt_id
pilot approval
production gating
RANS review
RANS manual extension
RANS state labels
postprocess manifest
reconstruct_pending_parallel_times
field_scale_mode
residual parser
execution segment timing
constrained_layout
```

Entrega en el informe final qué se encontró realmente, no solo lo esperado.

---

# 4. Corrección del monitor global

## 4.1 Problema observado

Los monitores en directo no siguen correctamente:

- ejecuciones RANS individuales;
- colas RANS;
- pilot URANS;
- producción URANS;
- transición desde una base RANS a un caso URANS;
- ejecuciones URANS secuenciales.

En algunos casos el monitor conserva el run anterior, queda vacío o no muestra el intento activo.

## 4.2 Fuente autoritativa

El monitor debe resolver siempre una **ejecución concreta**, no una definición de caso genérica.

Identidad mínima:

```json
{
  "case_id": "",
  "run_kind": "RANS|PILOT|PRODUCTION|PIMPLE_SENSITIVITY",
  "attempt_id": "",
  "run_id": "",
  "mode": "RANS|URANS",
  "status": "",
  "active_stage": "",
  "log_path": "",
  "force_history_path": "",
  "residual_history_path": ""
}
```

La definición URANS:

```text
runs/<topology>/<level>/<case_id>/case/
```

no es una ejecución y no debe seleccionarse como monitor.

## 4.3 Registro antes del proceso

Antes de lanzar cualquier subprocess:

```text
registry entry = PREPARING
active_run_id = attempt-specific run_id
active_run_kind = PILOT/PRODUCTION/RANS/PIMPLE
```

Después:

```text
PREPARING -> RUNNING -> terminal status
```

Si el proceso falla antes de OpenFOAM:

- mantener la entrada;
- mostrar el error;
- mostrar remediation;
- no dejar un monitor vacío.

## 4.4 Cambio de RANS a URANS

Al lanzar pilot o producción desde un checkpoint RANS:

1. conservar la base RANS;
2. registrar el intento URANS;
3. cambiar `active_run_id` al intento URANS;
4. cambiar `active_mode` a URANS;
5. cambiar `active_run_kind`;
6. invalidar cache del monitor anterior;
7. seleccionar el nuevo log;
8. mostrar stage y `deltaT`.

No depender de la selección visual anterior.

## 4.5 Cambio dentro de colas

Cuando una cola cambia de caso:

```text
finalizar intento actual
liberar lock
actualizar batch pointer
registrar próximo intento
actualizar active_run_id
```

El monitor debe seguir el siguiente caso si:

```text
Seguir ejecución activa = true
```

## 4.6 Estado fijado

Permitir:

```text
Seguir ejecución activa
Fijar ejecución seleccionada
```

Default:

```text
seguir activa = true
```

Si el usuario fija una ejecución, la cola no cambia su monitor visual, pero el registro activo sigue actualizándose.

## 4.7 Gráficas

RANS:

- residuos iniciales vs iteración SIMPLE;
- eje y logarítmico;
- Cl, Cd, Cm;
- Cl/Cd en eje secundario.

URANS pilot/producción:

- residuos vs paso/tiempo físico;
- Cl, Cd, Cm;
- Cl/Cd;
- métricas ligeras:
  - stage;
  - `deltaT`;
  - physical time;
  - step;
  - Co;
  - elapsed.

No generar PSD o campos durante ejecución.

## 4.8 Rendimiento

- refresh 30 s por defecto;
- 15/30/60;
- parsing incremental;
- cache por offset/mtime;
- no releer logs completos;
- no abrir ParaView;
- no lanzar acciones backend en un rerun de Streamlit;
- no crear claves de widget duplicadas.

---

# 5. Flujo URANS completo y verificable

## 5.1 Definición, pilot y producción

Mantener separados:

```text
case definition
pilot attempt
production attempt
```

Rutas:

```text
runs/<topology>/<level>/<case_id>/
  case/
  pilot/pilot_attempt_NNN/case/
  production/production_attempt_NNN/case/
```

No compartir:

- tiempos;
- logs;
- manifests;
- force histories;
- monitor paths.

## 5.2 Flujo individual

En:

```text
URANS -> Ejecución -> Caso individual
```

mostrar un selector único de:

- malla/topología;
- checkpoint RANS aceptado para URANS;
- `deltaT`;
- definición existente o nueva;
- intento existente, cuando proceda.

Acciones ordenadas:

```text
1. Preparar/verificar definición
2. Ejecutar prueba rápida
3. Revisar prueba rápida
4. Ejecutar/continuar producción
5. Detener y escribir
6. Reanudar intento
7. Archivar y reiniciar
```

Los pasos 2 y 3 son opcionales.

## 5.3 Verificación del checkpoint

Antes de pilot o producción:

- misma malla;
- mesh hash compatible;
- physics hash;
- modelo turbulento;
- U/p/nuTilda;
- `phi`, `nut` si existen;
- review permite URANS;
- no copiar checkpoint sobre un intento parcial.

Un fallo de compatibilidad es:

```text
BLOCKED_INCOMPATIBLE_RANS_CHECKPOINT
```

No es divergencia.

---

# 6. Pilot URANS opcional

## 6.1 Cambio de contrato

El contexto actual afirma que producción requiere `PILOT_PASS`. El usuario solicita que el pilot sea recomendable, pero no obligatorio.

Modificar la política a:

```text
pilot_policy = recommended
```

Estados:

```text
PILOT_NOT_RUN
PILOT_RUNNING
PILOT_PASS
PILOT_WARN
PILOT_FAIL
PILOT_PARTIAL
PILOT_USER_ACCEPTED
PILOT_USER_BYPASSED
```

## 6.2 Ejecución sin pilot

Permitir ejecutar producción con:

```text
PILOT_NOT_RUN
PILOT_WARN
PILOT_FAIL
```

solo mediante una confirmación explícita:

```text
Ejecutar sin pilot aprobado
```

Mostrar warning:

```text
La prueba rápida no se ha realizado o no ha sido aprobada.
La simulación puede divergir o requerir un coste mucho mayor del previsto.
```

El warning no debe interrumpir la ejecución si el usuario confirma.

Guardar:

```json
{
  "pilot_required": false,
  "pilot_status": "PILOT_NOT_RUN",
  "pilot_bypass_confirmed": true,
  "pilot_bypass_timestamp": ""
}
```

No presentar el bypass como `PILOT_PASS`.

## 6.3 Matriz

Añadir política:

```text
Pilot por cada malla y mayor dt
Pilot del caso seleccionado
Omitir pilot con advertencia
```

No bloquear toda la matriz por falta de pilot.

## 6.4 Prueba rápida revisable

Tras el pilot mostrar una revisión estática con:

- residuals;
- Cl/Cd/Cm;
- Cl/Cd;
- Co;
- continuity summary;
- PIMPLE progression;
- boundedness;
- tiempo por paso;
- status y criterios.

Acciones:

```text
Aprobar pilot
Aceptar con advertencias
Repetir pilot
Archivar pilot
Ejecutar producción
```

La aprobación del pilot es una decisión separada de la evaluación automática.

## 6.5 Pilot fallido

Diferenciar:

```text
PILOT_NUMERICAL_FAIL
PILOT_SETUP_FAIL
PILOT_TIMEOUT_PARTIAL
PILOT_ORCHESTRATION_FAIL
```

No clasificar:

- directorio existente;
- path collision;
- manifest error;

como divergencia CFD.

---

# 7. Eliminar la subsección pilot duplicada

El pilot no tendrá una sección independiente.

Eliminar de navegación visual:

```text
Prueba corta
```

Mantener:

```text
botón pilot en Caso individual
botón pilot en Matriz
```

En ambos casos añadir un expander:

```text
Ajustes avanzados de la prueba rápida
```

Controles:

- etapas;
- esquemas;
- factores de `deltaT`;
- pasos;
- timeout;
- ranks;
- criterios;
- almacenamiento mínimo.

No duplicar esos widgets en dos fuentes de configuración independientes. Ambos deben leer el mismo bloque schema.

---

# 8. Estimación automática de tiempo

## 8.1 Eliminar calculadora manual

Eliminar de la UI normal:

```text
calculadora manual de tiempo
calculadora manual de almacenamiento
entrada manual de s/step
estimación manual de memoria
```

No eliminar internamente los datos medidos.

## 8.2 Fuente

Tras pilot o producción parcial:

```text
median_solver_seconds_per_step
p25
p75
```

Excluir:

- primer paso;
- cambios de etapa;
- setup;
- decomposition;
- reconstruction;
- writes;
- postprocess.

## 8.3 Cálculo

```python
total_steps = startup_steps + settling_steps + sampling_steps
estimated_solver_seconds = median_seconds_per_step * total_steps
```

Si ya se completaron pasos:

```python
remaining_steps = total_steps - completed_steps
estimated_remaining = median_seconds_per_step * remaining_steps
```

Mostrar:

```text
Fuente: pilot actual / producción parcial / histórico compatible
Confianza: medida / estimada
```

## 8.4 Sin evidencia

Si no hay una medición compatible:

```text
Estimación no disponible hasta ejecutar un pilot o algunos pasos de producción.
```

No solicitar al usuario un tiempo manual.

## 8.5 Matriz

Para cada celda usar:

1. pilot de esa combinación;
2. pilot de misma malla y `deltaT` cercano;
3. producción previa compatible;
4. regresión por malla/configuración;
5. no disponible.

No sumar almacenamiento.

---

# 9. Ejecución URANS matricial y secuencial

## 9.1 Ubicación

En `URANS -> Ejecución`:

```text
Caso individual
Matriz de ejecución secuencial
```

Caso individual primero.

## 9.2 Selector pilot de matriz

Añadir:

```text
Caso de la matriz para prueba rápida
```

Solo permite seleccionar celdas marcadas en la matriz.

Botón:

```text
Ejecutar prueba rápida del caso seleccionado
```

## 9.3 Cola

La cola debe ser desatendida:

```text
validado -> skip
parcial -> resume
creado -> prepare
pilot según política
producción
timeout -> preserve/continue
divergence -> preserve/continue
setup error -> record/continue
environment error -> stop batch
```

No esperar aprobación interactiva entre casos.

La validación de cada run ocurre después.

## 9.4 Reanudación

Guardar:

```text
batch_id
selected matrix
ordered case_ids
current index
attempt ids
status
```

Al reanudar:

- no duplicar intento;
- no repetir stages completados;
- usar `latestTime`;
- no copiar checkpoint.

## 9.5 Casos no obligatorios

La matriz es el conjunto de candidatos planificados.

No exigir que todas las celdas se ejecuten o validen para poder analizar las ya aceptadas.

---

# 10. Bases RANS: extensión manual sin límite de 20.000

## 10.1 Separar límites

El límite de 20.000 pertenece exclusivamente a la cola automática:

```text
automatic_queue_max_iterations = 20000
```

La revisión manual debe permitir siempre:

```text
Extender 2500 iteraciones
```

aunque el caso tenga:

```text
20000
22500
25000
...
```

No imponer hard maximum a extensiones manuales.

## 10.2 Seguridad

Cada extensión manual requiere:

- mostrar iteración actual;
- target nuevo;
- coste estimado;
- botón explícito;
- configuración original por defecto;
- nuevo execution segment;
- conservación de historias.

No requiere reiniciar.

## 10.3 Gate

Después de la extensión:

- recalcular diagnóstico;
- no cambiar review automáticamente;
- indicar si hubo mejora;
- permitir nueva decisión.

---

# 11. Corregir la clasificación `RANS:Diverged`

## 11.1 Problema

Algunas bases casi estabilizadas y que alcanzaron el máximo aparecen como:

```text
RANS:Diverged
```

Esto probablemente mezcla:

- no cumplir gate;
- alcanzar máximo;
- plateau;
- divergencia real.

## 11.2 Divergencia real

Solo declarar `DIVERGED` con evidencia dura:

- NaN/Inf;
- nonzero solver return por fallo numérico;
- fuerza runaway;
- nuTilda no acotado;
- continuidad catastrófica;
- campos corruptos;
- floating point exception real;
- crecimiento explosivo.

No declarar divergencia por:

- `NOT_CONVERGED`;
- `REVIEW_REQUIRED`;
- alcanzar 20.000;
- residual de p en plateau;
- timeout limpio;
- parada de usuario;
- fallo de postproceso.

## 11.3 Estados

Separar:

```text
execution_status:
  COMPLETED
  TIMEOUT_PARTIAL
  USER_STOPPED_PARTIAL
  SOLVER_FAILED
  DIVERGED

automatic_gate_status:
  AUTO_CONVERGED_STRICT
  AUTO_CONVERGED_WITH_PLATEAU_WARNING
  REVIEW_REQUIRED
  NOT_EVALUATED

review_status:
  NOT_REVIEWED
  USER_ACCEPTED_STATISTICALLY_STEADY
  USER_ACCEPTED_FOR_INITIALIZATION_ONLY
  USER_REJECTED
```

## 11.4 Etiqueta visual

Prioridad:

```text
DIVERGED solo si execution_status == DIVERGED
```

Ejemplos:

```text
COMPLETED + REVIEW_REQUIRED
-> Finalizada, pendiente de revisión

COMPLETED + USER_ACCEPTED_STATISTICALLY_STEADY
-> Aceptada manualmente

TIMEOUT_PARTIAL
-> Parcial por timeout
```

No mostrar `RANS:Diverged` por una traducción genérica de gate fail.

---

# 12. Propagación de aceptación RANS

Al aceptar una base:

```json
{
  "review_status": "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
  "allowed_uses": {
    "rans_spatial_convergence": true,
    "urans_initialization": true
  }
}
```

Al aceptar solo para URANS:

```json
{
  "review_status": "RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY",
  "allowed_uses": {
    "rans_spatial_convergence": false,
    "urans_initialization": true
  }
}
```

Después de escribir:

1. actualizar review registry;
2. actualizar checkpoint registry;
3. actualizar mesh/base table;
4. invalidar cache;
5. habilitar selector URANS;
6. mantener automatic gate original.

La aceptación debe persistir tras reiniciar la app.

Añadir test de ciclo completo:

```text
RANS review -> accept -> close/reopen -> selectable for URANS
```

---

# 13. Tiempo de ejecución RANS

## 13.1 Problema

Las gráficas y métricas solo incluyen el último segmento de extensión.

## 13.2 Métricas

Guardar:

```text
solver_active_total_seconds
total_iterations_solved
mean_solver_seconds_per_iteration
median_solver_seconds_per_iteration
time_first_10000_iterations
time_by_segment
```

El usuario prefiere comparar principalmente:

```text
mean/median seconds per iteration
```

pero conservar el total real.

## 13.3 Agregación

Un caso con:

```text
0–10000
10000–12500
12500–15000
```

debe combinar los tres segmentos.

No usar solo el último log.

## 13.4 Overlap

Al reanudar puede repetirse una muestra inicial.

Deduplicar por:

```text
absolute iteration
segment provenance
```

No sumar una iteración dos veces.

---

# 14. Corrección del postproceso RANS completo

## 14.1 Error real

Actualmente:

```text
TypeError:
reconstruct_pending_parallel_times()
got an unexpected keyword argument 'field_scale_mode'
```

## 14.2 Interpretación

`field_scale_mode` es una opción de visualización/postproceso de campo.

No es, conceptualmente, un parámetro de reconstrucción MPI.

Por tanto, no añadas el argumento a `reconstruct_pending_parallel_times()` sin justificarlo.

## 14.3 Corrección

Audita:

```text
ramair_2d_rans_full_postprocess.py
ramair_2d_postprocess.py
reconstruct_pending_parallel_times()
todos sus callsites
```

Separa:

```text
reconstruction options
field rendering/scaling options
```

Flujo correcto:

```python
reconstruction = reconstruct_pending_parallel_times(
    case_dir=...,
    requested_times=...,
    # únicamente argumentos de reconstrucción
)

products = generate_field_products(
    ...,
    field_scale_mode=field_scale_mode,
)
```

Si la firma cambió durante la tarea anterior:

- sincroniza caller y callee;
- usa keyword-only args;
- añade test de contrato.

## 14.4 Resultado

El postproceso completo debe:

- completar reconstrucción;
- generar productos escalares;
- generar campos final-state;
- escribir manifest;
- no fallar si falta un producto opcional;
- mostrar error específico por producto.

---

# 15. Residuales RANS por iteración absoluta

## 15.1 Problema

La gráfica no se construye correctamente y puede mostrar solo la extensión.

## 15.2 Parser

Unificar todos los logs/segmentos:

| absolute_iteration | segment | equation | initial_residual | final_residual | linear_iterations |
|---:|---|---|---:|---:|---:|

Normalizar:

```text
p
U.x
U.y
nuTilda
```

No incluir `Phi` de `potentialFoam`.

## 15.3 Iteración

No usar:

- índice de fila;
- sample number;
- último segmento desde cero.

Calcular offset mediante metadata del segmento y verificar con el log.

## 15.4 Plot

- x: iteración SIMPLE absoluta;
- y: residuo inicial;
- y log;
- límites positivos;
- líneas legibles;
- leyenda;
- no `constrained_layout` colapsado.

## 15.5 Datos incompletos

Mostrar:

```text
Segmentos encontrados
Rango de iteraciones
Campos encontrados
Filas descartadas
```

No mostrar figura vacía.

---

# 16. Simplificación visual de revisión RANS

## 16.1 Selector único

Un único selector superior:

```text
Ejecución RANS a revisar
```

Este selector controla:

- análisis;
- aprobación;
- extensión;
- postproceso;
- visualizaciones.

Eliminar selectores duplicados de caso.

## 16.2 Dos expanders principales

Mantener solo:

### A. `Gráficas y métricas de convergencia`

Contiene:

- residuals;
- coefficients;
- Cl/Cd;
- moving statistics;
- window comparison;
- gate table;
- timing;
- acceptance controls.

### B. `Postproceso completo y visualizaciones`

Contiene, si se ejecutó:

- surface plots;
- field images;
- ParaView;
- manifests/errors resumidos;
- open folder;
- regenerate products.

## 16.3 Ocultar del menú principal

No eliminar backend/manifests, pero no mostrar como secciones independientes:

```text
Aceptación y gráficas almacenadas
Postprocess products
Registro de visualizaciones RANS
Registro unificado de postproceso
Manifest de revisión RANS
Nota de revisión
```

La nota de revisión desaparece de la UI.

Los manifests quedan:

```text
Detalles técnicos
Descargar JSON
```

## 16.4 Gráficas almacenadas

Las gráficas reales se muestran dentro de:

```text
Gráficas y métricas de convergencia
```

No al final fuera del expander.

## 16.5 Productos postprocess

El navegador usa:

```text
postprocess_manifest.json
```

pero se renderiza dentro de:

```text
Postproceso completo y visualizaciones
```

No crear un tercer expander vacío.

---

# 17. Warning de Matplotlib

## 17.1 Error

```text
constrained_layout not applied because axes sizes collapsed to zero
```

## 17.2 Causa probable

- figura demasiado pequeña;
- demasiados ejes/decoraciones;
- `twinx()` + constrained layout;
- columnas Streamlit estrechas;
- leyenda/títulos excesivos.

## 17.3 Corrección

Para plots compactos con eje secundario:

- no usar `constrained_layout=True`;
- usar tamaño explícito;
- `fig.subplots_adjust(...)`;
- leyenda combinada compacta;
- reducir padding;
- usar `width="stretch"`;
- cerrar figura después de renderizar.

Ejemplo:

```python
fig, ax = plt.subplots(figsize=(7.2, 3.6))
ax2 = ax.twinx()
...
fig.subplots_adjust(
    left=0.10,
    right=0.88,
    bottom=0.18,
    top=0.86,
)
st.pyplot(fig, width="stretch")
plt.close(fig)
```

Para residuos sin `twinx`, puede usarse `tight_layout()` tras verificar.

Añadir test que trate warnings como fallo para los builders principales.

---

# 18. Postproceso URANS y RANS

Para una ejecución seleccionada, el expander de postproceso debe mostrar grupos reales:

```text
Historiales escalares
Estadísticas
Distribuciones de pared
Campos
Animaciones, solo URANS
ParaView
Errores/productos ausentes
```

Carga diferida.

RANS:

- no Courant;
- último estado;
- Cp, U, vorticity, y+, wall shear, Cf;
- no animación automática.

URANS:

- Cp/U/Co/vorticity;
- snapshots retenidos;
- animación Cp/U si hay >=2 tiempos;
- escala global entre frames.

---

# 19. Prueba real acotada solicitada

Después de implementar y pasar tests:

## 19.1 Base

Usar:

```text
closed_coarse
```

Debe estar:

```text
review accepted for URANS
compatible checkpoint
```

Si la aceptación existe pero la UI no la reconoce, corregir antes.

## 19.2 Pilot real

Crear un nuevo intento pilot:

- no sobrescribir intentos anteriores;
- pocos pasos;
- monitor visible;
- almacenamiento mínimo;
- generar review.

Verificar:

- PREPARING/RUNNING;
- gráficos;
- status;
- tiempo por paso;
- aprobación.

## 19.3 Producción real acotada

Desde la misma definición:

- nuevo production attempt;
- duración muy corta;
- no campaña científica;
- monitor;
- stop limpio;
- postprocess parcial;
- reanudación posible.

Puede ejecutarse:

- con pilot aprobado;
- y además probar en dry-run la ruta de bypass.

## 19.4 Evidencia

Generar informe:

```text
CFD_2D/reports/VALIDATION_LAB_URANS_END_TO_END_SMOKE_<date>.md
```

Incluir:

- IDs;
- paths;
- checkpoint;
- commands;
- logs;
- monitor evidence;
- timing;
- statuses;
- products;
- confirmación de que no es validación física.

## 19.5 Cola

Verificar la cola URANS con:

- tests;
- dry-run de varias celdas;
- reanudación simulada;
- timeout fixture.

No ejecutar una campaña larga.

---

# 20. Estado y migración

## 20.1 Pilot opcional

Migrar schema 7 de forma compatible:

```json
{
  "pilot_policy": "recommended",
  "allow_production_without_pilot": true
}
```

Valores históricos:

- conservar `PILOT_PASS`;
- no convertir missing en fail;
- no relanzar.

## 20.2 RANS

No modificar campos ni gates históricos.

Solo corregir mappings/labels y agregar timing segmentado.

## 20.3 Postprocess

No borrar manifests previos.

Regenerar un manifest si está incompleto, basado en productos reales.

## 20.4 API

Si cambia el contrato UI/backend:

```text
incrementar BACKEND_API_VERSION
incrementar EXPECTED_BACKEND_API_VERSION
```

juntos.

Actualizar docs solo después de tests.

---

# 21. Archivos a inspeccionar

Como mínimo:

```text
CFD_2D/app/ramair_cfd2d_app.py
CFD_2D/app/workflow_backend.py
CFD_2D/scripts/ramair_2d_validation_study.py
CFD_2D/scripts/ramair_2d_openfoam_runner.py
CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py
CFD_2D/scripts/ramair_2d_rans_full_postprocess.py
CFD_2D/scripts/ramair_2d_postprocess.py
CFD_2D/scripts/ramair_2d_urans_matrix_manager.py
CFD_2D/scripts/ramair_2d_urans_review.py
monitor/parsers
registry helpers
schema migration
```

No dupliques parsers o lógica de estado.

---

# 22. Tests obligatorios

## 22.1 Monitores

- RANS individual;
- RANS queue;
- pilot;
- production;
- PIMPLE;
- RANS->URANS switch;
- queue switch;
- pinned run;
- no duplicate keys;
- no subprocess from refresh.

## 22.2 URANS

- definition;
- pilot attempt;
- pilot review;
- optional pilot;
- bypass warning;
- production;
- resume;
- existing attempt;
- no overwrite;
- distinct paths.

## 22.3 Timing

- measured pilot;
- remaining time;
- no manual calculator;
- no memory estimator;
- no evidence -> unavailable;
- matrix estimates.

## 22.4 RANS

- manual extension beyond 20k;
- queue still capped at 20k;
- not converged != diverged;
- acceptance propagation;
- persistence;
- allowed uses.

## 22.5 Postprocess

- reproduce TypeError fixture;
- caller/callee contract fixed;
- `field_scale_mode` only rendering;
- partial products;
- manifest;
- UI product display.

## 22.6 Histories

- multiple segments;
- absolute iteration;
- overlap;
- total timing;
- residual log plot.

## 22.7 UI

- one RANS selector;
- two expanders;
- no duplicate pilot section;
- no review note;
- manifests hidden in technical details;
- no empty product sections.

## 22.8 Matplotlib

- no constrained-layout warning;
- nonzero axes;
- closed figures;
- width API.

## 22.9 Commands

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

Real OpenFOAM test only after normal tests pass.

---

# 23. Orden de implementación

## Fase 1 — Auditoría

- changelog vs code;
- current state;
- reproduce bugs;
- report.

## Fase 2 — State/registry

- monitor identity;
- optional pilot;
- RANS mappings;
- migration;
- tests.

## Fase 3 — Monitors

- active attempt;
- transitions;
- plots;
- performance.

## Fase 4 — URANS

- individual;
- optional pilot;
- review;
- production;
- matrix;
- resume.

## Fase 5 — RANS

- manual extension;
- acceptance propagation;
- timing;
- history aggregation.

## Fase 6 — Postprocess

- signature bug;
- residual parser;
- manifests;
- UI simplification;
- Matplotlib.

## Fase 7 — Verification

- tests;
- visual UI;
- real bounded closed-coarse pilot/production;
- report.

## Fase 8 — Documentation

Actualizar:

```text
CHANGELOG.md
PROJECT_CONTEXT_FOR_CODEX.md
README_PROJECT_STRUCTURE.md
```

solo con comportamiento verificado.

---

# 24. Criterios de finalización

No declares completado hasta que:

1. RANS monitor funciona.
2. Pilot monitor funciona.
3. Production monitor funciona.
4. El cambio RANS->URANS es correcto.
5. El pilot se puede ejecutar/revisar.
6. La producción puede ejecutarse sin pilot con warning.
7. No existe sección pilot duplicada.
8. La estimación usa mediciones reales.
9. No existe calculadora manual de tiempo/memoria.
10. La matriz URANS es reanudable.
11. La extensión RANS manual supera 20k.
12. `NOT_CONVERGED` no se muestra como `DIVERGED`.
13. La aceptación habilita URANS.
14. El tiempo RANS agrega todos los segmentos.
15. El postproceso completo no lanza TypeError.
16. Los residuos usan iteración absoluta.
17. La UI RANS solo tiene los dos expanders solicitados.
18. Las gráficas aparecen dentro de ellos.
19. No hay warning `constrained_layout`.
20. El smoke test cerrado completa pilot y producción corta.
21. Tests y check-only pasan.
22. No se han perdido simulaciones o Results.

---

# 25. Entrega final de Codex

Incluir:

1. estado encontrado frente a changelog;
2. causas raíz;
3. archivos modificados;
4. migraciones;
5. screenshots/descripción de UI;
6. tests;
7. ejecución real acotada;
8. logs y paths;
9. pilot bypass probado;
10. estado de cola URANS;
11. postprocess corregido;
12. problemas pendientes;
13. confirmación de preservación de datos.
