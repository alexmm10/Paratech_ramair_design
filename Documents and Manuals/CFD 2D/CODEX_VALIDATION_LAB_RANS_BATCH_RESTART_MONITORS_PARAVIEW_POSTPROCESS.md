# INSTRUCCIONES PARA CODEX
## Corrección de la cola RANS, reinicio de `closed_medium`, monitores, tiempo de ejecución, ParaView y postproceso

**Proyecto:** RamAir DESIGN APP  
**Contexto de referencia:** 2026-07-29/30  
**Backend esperado:** API 18  
**Laboratorio:** `CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8`  
**Caso Results:** `Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6`

Este documento amplía el contrato actual del Validation & Convergence Lab.  
La modificación afecta principalmente a:

- interfaz;
- orquestación de la cola RANS;
- reanudación y reinicio de bases;
- medición de tiempo;
- monitorización;
- postproceso RANS;
- visualización ParaView;
- análisis de convergencia espacial.

No cambia:

- geometría;
- mallas aprobadas;
- condiciones físicas;
- modelo turbulento;
- esquema SIMPLE;
- configuración URANS salvo donde se indique expresamente.

Antes de editar:

1. Leer `PROJECT_CONTEXT_FOR_CODEX.md`.
2. Leer `README_PROJECT_STRUCTURE.md`.
3. Leer `AGENTS.md` y `CHANGELOG.md`.
4. Inspeccionar la implementación real de API 18 y schema-v4 del laboratorio.
5. Inspeccionar todos los datos existentes de `closed_coarse` y `closed_medium`.
6. No borrar `Results`.
7. No borrar la malla `closed_medium`; el usuario solicita eliminar únicamente su ejecución/base RANS activa.
8. No iniciar una campaña real hasta aplicar y probar las correcciones de single-flight, cola e iteraciones.
9. La ejecución CFD real posterior está explícitamente solicitada, pero debe comenzar únicamente después de los tests y verificaciones descritos.

---

# 1. Resultado operativo solicitado

Después de implementar y verificar las correcciones:

1. preservar completamente `closed_coarse`, que el usuario ya ha validado;
2. detener de forma limpia cualquier job activo de `closed_medium`;
3. archivar la ejecución RANS problemática de `closed_medium`;
4. eliminar únicamente su ejecución/base/checkpoint activos del laboratorio;
5. conservar:
   - geometría `closed_medium`;
   - caso CFD;
   - malla;
   - paquete Results;
   - históricos archivados;
6. regenerar `closed_medium` desde iteración 0;
7. ejecutar de manera continua y desatendida las cinco bases restantes:

```text
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

8. cada malla debe ejecutar:
   - mínimo 10,000 iteraciones;
   - extensiones automáticas de 2,500;
   - máximo 20,000;
9. no solicitar decisiones intermedias de convergencia;
10. al terminar cada malla:
    - guardar estado;
    - calcular gate;
    - registrar `AUTO_CONVERGED`, `PLATEAU_WARNING` o `REVIEW_REQUIRED`;
    - continuar con la siguiente;
11. no transferir automáticamente ninguna base a URANS;
12. después del batch, el usuario revisará y aprobará cada base de forma individual.

---

# 2. Eliminar la subsección “Resumen”

Eliminar definitivamente la subsección interna:

```text
Resumen
```

El laboratorio comenzará directamente en:

```text
Mallas y condiciones
```

No eliminar:

- métricas de estado;
- progreso global;
- warnings;
- presupuesto.

Mover la información esencial del antiguo resumen a una franja compacta no navegable situada bajo el menú de subsecciones:

```text
Caso: M0.15 | Re=1.9e6 | c=1 m | alpha=8 deg
Bases RANS: x/6 finalizadas
Job activo: <run>
```

Esta franja no cuenta como subsección.

Actualizar documentación que todavía indique diez secciones.

---

# 3. Barra de subsecciones en la parte superior

## 3.1 Posición

Mover la navegación del laboratorio inmediatamente debajo de la barra principal de la aplicación.

Orden:

```text
Barra principal
Barra horizontal de subsecciones del Validation Lab
Franja compacta de estado
Contenido
```

No colocar la navegación:

- en mitad de la página;
- después de tablas;
- en la barra lateral;
- repetida al final.

## 3.2 Implementación Streamlit

Preferir un control horizontal persistente, por ejemplo:

```python
st.segmented_control(...)
```

o:

```python
st.radio(..., horizontal=True)
```

según compatibilidad real.

Requisitos:

- clave estable;
- persistencia en `st.session_state`;
- selección actual visible;
- no perder la selección con autorefresh;
- ancho de contenedor;
- nombres cortos y comprensibles;
- scroll horizontal si la ventana es estrecha;
- navegación programática desde los jobs.

Orden definitivo:

```text
Mallas y condiciones
Solver y estrategia
Análisis RANS
Análisis URANS
Sensibilidad PIMPLE
Convergencia RANS
Matriz URANS
Convergencia malla-tiempo
Frecuencias
Courant
Informes
```

Puede abreviarse visualmente, pero el tooltip debe mostrar el nombre completo.

---

# 4. Explicación de la ralentización observada

La malla `closed_medium` contiene aproximadamente 333,826 celdas frente a 203,691 de `closed_coarse`. Por tanto, una ralentización respecto a coarse es físicamente esperable:

\[
333826/203691 \approx 1.64
\]

El coste puede crecer más que linealmente por:

- GAMG;
- número de iteraciones de los solvers lineales;
- caché/memoria;
- comunicación MPI;
- calidad local;
- frecuencia de escritura.

Sin embargo, el mensaje repetido:

```text
Run script written: .../closed_medium/case/run_case.sh
Steady extension finished without satisfying all transition criteria. User decision required.
```

cuando la ejecución está alrededor de la iteración 7,840 no es compatible con el contrato de un bloque inicial mínimo de 10,000 iteraciones. Debe tratarse como un bug de orquestación, estado o contabilidad de iteraciones hasta demostrar lo contrario.

No atribuir toda la ralentización al tamaño de malla sin investigar:

- procesos duplicados;
- relanzamientos;
- reconstrucciones repetidas;
- regeneración de scripts;
- plotting excesivo;
- múltiples jobs con el mismo `run_id`;
- lectura completa de logs en cada refresco.

---

# 5. Diagnóstico obligatorio del mensaje recurrente

## 5.1 Hipótesis que deben comprobarse

1. La UI vuelve a invocar `execute` en cada rerun/autorefresh.
2. El botón conserva un estado que provoca múltiples submit.
3. El runner interpreta la fase como extensión antes de llegar a 10,000.
4. Se usa un resumen antiguo de `closed_medium`.
5. Se mezclan:
   - iteración absoluta;
   - número de nuevas iteraciones;
   - número de muestras de residual.
6. Un log con varias ecuaciones por iteración se cuenta como varias iteraciones.
7. `run_case.sh` se reescribe en cada poll.
8. Existen dos procesos `foamRun` o dos workers para el mismo `run_id`.
9. El mensaje procede de una ejecución archivada y se vuelve a imprimir.
10. El target de la extensión se calcula como 2,500 absoluto en vez de `current+2500`.
11. La cola conserva un `pending_decision` obsoleto.
12. El monitor vuelve a disparar la acción de backend.

## 5.2 Regla central

La UI solo debe:

```text
consultar estado
leer logs
mostrar gráficas
```

Nunca debe lanzar o relanzar el solver durante un rerun de monitorización.

La ejecución solo puede iniciarse mediante una transición backend explícita y registrada.

## 5.3 Single-flight

Implementar un lock/lease atómico por:

```text
study_id
run_id
mode
```

Guardar:

```json
{
  "run_id": "",
  "job_id": "",
  "pid": null,
  "worker_id": "",
  "started_at": "",
  "heartbeat_at": "",
  "command_hash": "",
  "state": "RUNNING"
}
```

Antes de ejecutar:

- comprobar lock;
- comprobar PID;
- comprobar registry;
- comprobar proceso OpenFOAM;
- rechazar un segundo lanzamiento.

Estado de error:

```text
BLOCKED_DUPLICATE_EXECUTION
```

## 5.4 Script

`run_case.sh` puede escribirse durante preparación o nueva revisión de configuración, pero no en cada refresh.

Registrar:

```text
script_written_at
script_hash
script_revision
```

Si el contenido no cambia, no reescribir.

## 5.5 Mensaje de fin

El mensaje:

```text
Steady extension finished...
```

solo puede emitirse cuando:

1. el proceso actual ha terminado;
2. la iteración real es al menos el target del bloque;
3. el `run_id` coincide;
4. el bloque se marca `COMPLETED`;
5. el gate se ha evaluado una sola vez.

Nunca emitirlo durante una ejecución activa.

---

# 6. Contabilidad de iteraciones

## 6.1 Fuente autoritativa

No usar:

- número de líneas;
- longitud del CSV;
- cantidad de residual records;
- nombre de un archivo obsoleto.

Determinar la iteración real mediante una combinación consistente de:

1. contador SIMPLE extraído del log activo;
2. último directorio de iteración válido del archivo steady;
3. metadata del runner;
4. estado de bloque.

Guardar:

```text
absolute_simple_iteration
block_start_iteration
block_target_iteration
block_completed_iterations
```

## 6.2 Targets absolutos

Para una ejecución nueva:

```text
initial target = 10000
```

Extensiones:

```text
12500
15000
17500
20000
```

Si una base está en 7,840:

```text
target actual = 10000
```

No solicitar decisión ni evaluar extensión final antes de llegar a 10,000.

## 6.3 Reinicio de closed_medium

Después de eliminar su ejecución activa:

```text
absolute_simple_iteration = 0
current_target = 10000
extension_index = 0
```

No reutilizar:

- contador anterior;
- summary anterior;
- gate anterior;
- wall time anterior;
- checkpoint anterior;
- pending decision anterior.

Conservarlos solo en el archivo histórico.



---

# 7. Tiempo de ejecución de las primeras 10,000 iteraciones

## 7.1 Métrica solicitada

La cifra principal mostrada para comparar bases RANS debe ser:

```text
Tiempo activo del solver para completar las iteraciones SIMPLE 1–10,000
```

No debe incluir:

- espera en la cola;
- preparación de diccionarios;
- `potentialFoam`;
- `decomposePar`;
- reconstrucción;
- postproceso;
- renderizado;
- tiempo con el solver detenido;
- tiempo esperando decisión;
- lanzamiento de Streamlit;
- refresco del monitor.

## 7.2 Medición robusta

El runner debe registrar tiempos monotónicos de los segmentos activos.

Por segmento:

```json
{
  "segment_id": "",
  "run_id": "",
  "iteration_start": 0,
  "iteration_end": 0,
  "solver_started_monotonic": 0.0,
  "solver_finished_monotonic": 0.0,
  "active_solver_seconds": 0.0,
  "setup_seconds": 0.0,
  "post_seconds": 0.0
}
```

Para las primeras 10,000:

```python
solver_active_wall_time_first_10000_s
```

debe ser la suma del tiempo activo de los segmentos que cubren exactamente las iteraciones 1–10,000.

Si la ejecución se interrumpe y reanuda:

- sumar tiempo activo;
- no usar el tiempo de calendario entre segmentos;
- no duplicar iteraciones;
- documentar segmentos.

## 7.3 Otras métricas

Guardar además:

```text
total_elapsed_wall_time_to_10000_s
setup_overhead_to_10000_s
monitoring_overhead_estimate_s
median_solver_seconds_per_iteration_1_10000
p25/p75 seconds per iteration
normalized_hours_per_10000_iterations
```

La UI principal muestra:

```text
Tiempo solver 1–10,000
s/iteración
Overhead
```

## 7.4 Invalidación de closed_medium

La medición actual de `closed_medium` debe considerarse no válida porque:

- existe posible relanzamiento/duplicación;
- la ejecución no ha completado el bloque limpio;
- el estado parece mezclar información previa.

Archivar:

```text
timing_status = INVALIDATED_ORCHESTRATION_BUG
```

La nueva ejecución desde cero será la fuente válida.

## 7.5 Validación

Crear una tabla por malla:

| Malla | Iteraciones cubiertas | Tiempo solver 1–10k | Overhead | s/iter | Estado |
|---|---:|---:|---:|---:|---|

No extrapolar una ejecución incompleta como dato medido. Puede mostrarse una estimación separada.

---

# 8. Política autónoma de la cola RANS

## 8.1 Objetivo

El usuario quiere dejar las cinco bases ejecutándose sin intervención.

La cola no debe detenerse para solicitar aprobación entre mallas.

## 8.2 Algoritmo por malla

```text
START/RESUME
  -> run until at least 10,000
  -> evaluate gate
  -> if strict converged:
       save and continue next mesh
  -> if guarded plateau converged:
       save and continue next mesh
  -> if not converged and iteration < 20,000:
       extend automatically by 2,500
       repeat
  -> if iteration == 20,000:
       mark RANS_REVIEW_REQUIRED
       save and continue next mesh
```

No ejecutar más de 20,000 en este batch inicial.

## 8.3 Sin input externo

Durante el batch no mostrar un modal:

```text
User decision required
```

En su lugar, registrar decisiones automáticas de la cola:

```text
AUTO_EXTEND_TO_12500
AUTO_EXTEND_TO_15000
AUTO_EXTEND_TO_17500
AUTO_EXTEND_TO_20000
STOP_AT_MAX_REVIEW_REQUIRED
```

La revisión ocurre después.

## 8.4 Seguridad

Esto no autoriza:

- transferir un caso no aprobado a URANS;
- declarar convergencia manual;
- ignorar divergencia.

Si existe un fallo duro:

```text
NaN
runaway
missing field
solver crash
filesystem failure
MPI failure
```

marcar la malla y continuar con la siguiente solo si el fallo no compromete el entorno global.

Default:

```text
continue_after_case_failure = true
stop_after_environment_failure = true
```

## 8.5 Orden solicitado

```text
1 closed_medium — fresh
2 closed_fine
3 open_coarse
4 open_medium
5 open_fine
```

`closed_coarse` se omite y permanece intacta.

## 8.6 Reanudación del batch

Si la app se cierra:

- conservar `batch_id`;
- conservar queue pointer;
- conservar target;
- conservar proceso o estado parcial;
- reanudar desde la malla activa;
- no reiniciar mallas ya finalizadas.

---

# 9. Eliminación segura de la ejecución `closed_medium`

## 9.1 Qué eliminar

Eliminar únicamente del workspace activo del laboratorio:

- ejecución RANS activa de `closed_medium`;
- checkpoint derivado;
- postproceso derivado;
- metadata activa asociada;
- estado pendiente de decisión;
- timing inválido.

## 9.2 Qué conservar

- paquete de malla;
- geometría;
- caso CFD;
- configuración del conjunto;
- `Results`;
- historial archivado;
- logs archivados;
- informe del bug;
- `closed_coarse`.

## 9.3 Procedimiento

1. detectar job activo;
2. solicitar escritura limpia;
3. esperar terminación;
4. verificar que no hay proceso OpenFOAM;
5. archivar a:

```text
Previous Versions/ValidationLab/closed_medium_rans_orchestration_bug_<timestamp>/
```

o patrón real existente;

6. generar manifest del archivo;
7. eliminar la copia activa;
8. limpiar registry activo;
9. resetear estados;
10. verificar malla;
11. crear nueva ejecución con nuevo `run_id`.

No reutilizar el mismo `run_id`.

## 9.4 Confirmación

El usuario ha solicitado explícitamente esta eliminación. Codex puede realizarla tras mostrar en su log de trabajo:

```text
Deleting active closed_medium RANS execution only.
Mesh and Results packages remain unchanged.
```

No solicitar una segunda confirmación interactiva si impide el batch, pero debe archivar por defecto.

---

# 10. Corrección de la gráfica de residuos RANS

## 10.1 Síntoma

La gráfica en `Análisis por ejecución -> RANS` no se genera correctamente.

No ocultar el fallo con un gráfico vacío.

## 10.2 Fuente de datos

Determinar una fuente autoritativa con prioridad:

1. CSV/parser incremental RANS del laboratorio;
2. log SIMPLE archivado;
3. PyFoam residual file;
4. log OpenFOAM raw.

No usar historiales URANS para un caso RANS.

## 10.3 Parser

Construir una tabla tidy:

| iteration | equation | component | initial_residual | final_residual | n_iterations |
|---:|---|---|---:|---:|---:|

Normalizar nombres:

```text
p
U.x / U.y / U
nuTilda
Phi, solo durante potentialFoam y en gráfica separada si procede
```

No mezclar `Phi` de `potentialFoam` con residuos SIMPLE.

Si existen varias resoluciones de una ecuación en la misma iteración:

- conservar todas en raw;
- usar la primera residual inicial para el monitor;
- documentar la agregación.

## 10.4 Ejes

```text
x: Iteración SIMPLE
y: Residuo inicial [-]
escala y: log10
```

Usar:

```python
ax.set_yscale("log")
```

Filtrar para display:

```python
plot_value = NaN if residual <= 0 or nonfinite
```

No sustituir el dato raw.

## 10.5 Error explícito

Si no se encuentran datos:

```text
No se ha podido construir la gráfica de residuos.
```

Mostrar:

- paths inspeccionados;
- líneas parseadas;
- campos encontrados;
- última iteración;
- parser error.

No mostrar un espacio blanco.

## 10.6 Test con datos reales

Usar una copia/fixture de:

```text
closed_coarse
closed_medium archived
```

y comprobar:

- número de iteraciones;
- campos;
- eje log;
- no vacía;
- no mezcla tiempo físico.

---

# 11. Organización de las gráficas

## 11.1 Layout

En pantallas anchas:

```text
columna izquierda: residuos
columna derecha: coeficientes
```

En pantallas estrechas:

```text
una debajo de otra
```

Altura:

```text
260–320 px
```

Usar todo el ancho de contenedor.

## 11.2 Gráfica de coeficientes

Eje x RANS:

```text
Iteración SIMPLE
```

Eje izquierdo:

```text
Cl, Cd, Cm [-]
```

Eje derecho:

```text
Cl/Cd [-]
```

Implementar división segura.

No fijar límites rígidos globales que oculten la ventana estable.

Default:

- mostrar ventana reciente;
- límites robustos;
- botón `Mostrar historial completo`.

## 11.3 Debajo de las gráficas

Mostrar métricas compactas:

```text
Última iteración
Cl medio final
Cd medio final
Cl/Cd medio final
Gate
Tiempo solver 1–10k
```

No duplicar tablas largas en el monitor vivo.

## 11.4 Postejecución

En análisis final, añadir:

- dos ventanas comparadas;
- medias móviles;
- tabla del gate;
- tiempo;
- almacenamiento.

---

# 12. Navegación automática al monitor

Al iniciar el batch:

1. abrir `Análisis RANS`;
2. activar seguimiento;
3. seleccionar `closed_medium`;
4. mostrar monitor.

Cuando cambia la malla:

1. actualizar `active_run_id`;
2. persistir;
3. navegar al nuevo run;
4. actualizar título;
5. no crear una segunda ejecución.

Default:

```text
Seguir ejecución activa = true
```

Permitir desactivarlo.

La barra de navegación superior debe permanecer visible.



---

# 13. ParaView para las bases RANS

## 13.1 Ubicación en la interfaz

Añadir en:

```text
Análisis RANS -> Caso seleccionado -> Visualización del último estado
```

Botones:

```text
Generar visualizaciones finales RANS
Abrir último estado RANS en ParaView
Abrir carpeta de productos
```

No colocar estas acciones únicamente en la sección general de postproceso, porque el usuario debe poder acceder desde la base que está revisando.

## 13.2 Qué debe hacer “Generar visualizaciones finales RANS”

Usar el último estado SIMPLE válido y generar bajo demanda:

- presión;
- magnitud de velocidad;
- \(C_p\), si puede calcularse con referencias válidas;
- y+;
- wallShearStress;
- streamlines;
- una vista cercana al perfil;
- opcionalmente una vista del dominio.

No generar animaciones RANS automáticamente.

## 13.3 Último estado

Resolver explícitamente:

```text
final_simple_iteration
```

No elegir un tiempo URANS.

Si el caso está descompuesto:

- reconstruir únicamente la iteración final conocida;
- usar el selector explícito `-time <iteration>`;
- no reconstruir todos los estados salvo postproceso completo;
- preservar processor data hasta comprobar la reconstrucción.

## 13.4 Apertura de ParaView

Usar el contrato actual:

- script Python con rutas absolutas;
- OpenFOAM reader;
- seleccionar `internalMesh`;
- seleccionar último estado almacenado;
- aplicar filtros;
- resetear cámara;
- escribir:
  - screenshot;
  - `.pvsm`;
  - readiness JSON;
- registrar proceso;
- cerrarlo al cerrar la app.

No abrir ParaView mediante una ruta posicional sin aplicar readers.

## 13.5 Estado de disponibilidad

Mostrar:

```text
Último campo disponible: iteration 10000
Campos: U, p, nuTilda, nut, ...
ParaView readiness: ready / missing / generation required
```

Si solo existen escalares y no campos, indicarlo claramente.

---

# 14. Postproceso completo RANS

## 14.1 Ubicación

Añadir en:

```text
Análisis RANS -> Postproceso
```

Opciones:

```text
Diagnóstico RANS rápido
Postproceso RANS completo
Visualización final ParaView
```

## 14.2 Diagnóstico rápido

No ejecuta OpenFOAM. Genera:

- residuos;
- coeficientes;
- Cl/Cd;
- estadísticas;
- gate;
- coste;
- continuidad resumida;
- almacenamiento.

## 14.3 Postproceso completo

Puede ejecutar funciones de postproceso de OpenFOAM sobre el último estado:

- yPlus;
- wallShearStress;
- vorticity;
- surface fields;
- Cp;
- wall sampling;
- delta99;
- inventario de campos;
- exportación de tablas;
- productos ParaView.

Debe reutilizar:

```text
ramair_2d_postprocess.py
```

y la rama `RANS/` actual, no crear un postprocesador duplicado.

## 14.4 Disponibilidad antes de aprobación

El postproceso puede ejecutarse aunque la base sea:

```text
RANS_REVIEW_REQUIRED
RANS_PARTIAL
RANS_AUTO_CONVERGED
RANS_USER_ACCEPTED
```

La aprobación no es requisito para observar los resultados.

## 14.5 Almacenamiento

El postproceso completo es explícito y bajo demanda.

No generar:

- animaciones automáticas;
- VTK de todos los tiempos;
- múltiples copias de campos.

Guardar el paquete postprocesado separado del checkpoint.

---

# 15. Cómo funciona “Convergencia espacial RANS”

Esta sección no ejecuta SIMPLE ni decide automáticamente si una base individual es válida.

Su función es agregar y comparar las bases RANS aceptadas de cada topología.

## 15.1 Entradas

Para perfil cerrado:

```text
closed_coarse
closed_medium
closed_fine
```

Para perfil abierto:

```text
open_coarse
open_medium
open_fine
```

Solo utiliza bases con uso RANS autorizado:

```text
AUTO_CONVERGED
AUTO_CONVERGED_WITH_PLATEAU_WARNING
USER_ACCEPTED_STATISTICALLY_STEADY
```

No utiliza:

```text
INITIALIZATION_ONLY
REVIEW_REQUIRED
REJECTED
```

## 15.2 Qué compara

### Aerodinámica

- \(C_L\);
- \(C_D\);
- \(C_M\);
- \(C_L/C_D\);
- desviaciones;
- drift;
- diferencias relativas.

### Malla

- celdas;
- \(h_{\mathrm{eff}}\propto N^{-1/2}\);
- ratios de refinamiento;
- calidad.

### Coste

- tiempo activo 1–10,000;
- segundos/iteración;
- tiempo total;
- almacenamiento.

### Superficie

cuando exista:

- Cp;
- Cf;
- y+;
- wall shear;
- separación;
- delta99.

## 15.3 Resultados

Mostrar:

- tablas coarse/medium/fine;
- diferencia respecto a fine;
- métrica vs celdas;
- métrica vs \(h_{\mathrm{eff}}\);
- coste vs error;
- GCI solo cuando sea válido.

## 15.4 Estado incompleto

Hasta que las tres bases estén aceptadas:

```text
Estudio incompleto: faltan bases RANS aceptadas
```

No rellenar huecos con datos de una ejecución no revisada.

## 15.5 Relación con “Análisis RANS”

```text
Análisis RANS:
  inspecciona y aprueba un caso individual.

Convergencia espacial RANS:
  compara casos ya inspeccionados.
```

Añadir esta explicación en la UI.

---

# 16. Revisión del gate en el batch autónomo

Mantener el gate de API 18:

```text
strict convergence
guarded single-residual plateau warning
review required
divergence
```

## 16.1 Evaluación

No evaluar como fin de bloque antes del target.

A 10,000, 12,500, 15,000, 17,500 y 20,000:

- calcular gate;
- guardar diagnóstico;
- decidir continuar/detener.

## 16.2 Plateau

Puede detener antes de 20,000 si:

- hard criteria PASS;
- fuerzas estables;
- continuidad estable;
- solo un residual blando falla;
- está bajo ceiling;
- plateau demostrado.

## 16.3 Review required

Si no converge a 20,000:

- guardar último estado;
- marcar;
- continuar con siguiente malla.

No pedir input durante el batch.

## 16.4 Coarse validada

`closed_coarse` no debe recalcularse ni perder su revisión.

El batch debe verificar su estado y omitirla.

---

# 17. Configuración aplicada

Antes de iniciar el batch:

1. congelar UI en:

```text
resolved_batch_config.json
```

2. crear revisión nueva;
3. verificar:
   - initial 10000;
   - extension 2500;
   - maximum 20000;
   - ranks;
   - SIMPLE numerics;
   - storage;
   - monitor;
4. copiar config a las cinco ejecuciones;
5. auditar diccionarios.

## 17.1 No usar metadata previa de closed_medium

La nueva ejecución debe usar:

- configuración actual congelada;
- malla actual;
- nuevo run ID.

No usar el batch anterior.

## 17.2 Auditoría

Generar:

```text
applied_configuration_audit.json
```

para cada malla.

Bloquear si los diccionarios no coinciden.

---

# 18. Estado de la cola

Tabla:

| Orden | Malla | Estado | Iteración | Target | Gate | Tiempo 1–10k | Acción |
|---:|---|---|---:|---:|---|---:|---|

No mostrar:

- mesh hash;
- checkpoint ID.

Estados:

```text
PENDING
RUNNING_INITIAL_BLOCK
RUNNING_EXTENSION
AUTO_CONVERGED
PLATEAU_WARNING
REVIEW_REQUIRED
FAILED
COMPLETED
```

Mostrar:

```text
Batch 2/5
```

---

# 19. Prevención de consumo excesivo durante el batch

## 19.1 Monitor

- refresh 30 s;
- dos plots;
- no postprocess completo;
- no ParaView;
- no PSD;
- no lectura de fields.

## 19.2 Escritura

Para SIMPLE:

- escalares continuos;
- campos solo según perfil compacto;
- último estado;
- recuperación;
- `purgeWrite`.

## 19.3 Procesos

Antes de cada nueva malla:

- confirmar que el proceso anterior terminó;
- reconstruir/guardar;
- liberar MPI;
- actualizar lock;
- iniciar siguiente.

No solapar mallas.

## 19.4 Medición de slowdown

Guardar por bloques:

```text
median s/iteration
linear solver iterations
GAMG cycles
write events
```

Si `closed_medium` nueva sigue siendo anormalmente lenta:

- comparar con expected scaling;
- comprobar CPU;
- memoria/swap;
- ranks;
- solvers lineales;
- múltiples procesos;
- I/O.

---

# 20. Comandos y ejecución real

Codex no debe inventar una CLI. Debe inspeccionar las acciones existentes de API 18.

## 20.1 Secuencia

1. implementar;
2. tests unitarios;
3. check-only;
4. inspección UI;
5. dry-run/preflight del batch;
6. comprobar comandos;
7. detener/archivar `closed_medium`;
8. resetear;
9. iniciar batch real de cinco mallas.

## 20.2 Acción explícita

El usuario ha solicitado la ejecución real.

Registrar antes:

```text
User-requested real RANS batch execution:
closed_medium fresh -> closed_fine -> open_coarse -> open_medium -> open_fine
```

## 20.3 Si Codex no puede mantener el proceso

Si el entorno de Codex no permite una ejecución larga:

- implementar;
- preparar y verificar;
- dejar la cola `READY`;
- proporcionar el botón/comando exacto;
- no afirmar que se ejecutó.

No lanzar el batch en una sesión que será terminada inmediatamente sin un worker persistente.

---

# 21. Informe del incidente

Generar:

```text
CFD_2D/reports/VALIDATION_LAB_CLOSED_MEDIUM_ORCHESTRATION_INCIDENT_<date>.md
```

Incluir:

- mensaje recurrente;
- iteración observada;
- procesos;
- causa;
- datos archivados;
- corrección;
- tests;
- nuevo run ID;
- impacto sobre timing;
- confirmación de que `closed_coarse` y malla `closed_medium` no fueron borradas.

---

# 22. Tests obligatorios

## 22.1 Navegación

- no existe subsección Resumen;
- menú bajo barra principal;
- persistencia;
- navegación automática;
- active run.

## 22.2 Iteraciones

- 7,840 no dispara fin;
- target 10,000;
- extensiones absolutas;
- máximo 20,000;
- no prompts;
- batch continúa.

## 22.3 Single-flight

- doble click;
- autorefresh;
- lock;
- PID;
- script no reescrito;
- un solver por run.

## 22.4 Timing

- excluye setup;
- incluye segmentos;
- reanudación;
- exactamente 1–10k;
- invalidación de old closed_medium;
- coarse preservada.

## 22.5 Residuales

- parser RANS;
- log y;
- iteration x;
- no vacío;
- errores explícitos;
- no mezcla URANS;
- no `Phi`.

## 22.6 Plots

- dos columnas;
- ejes;
- Cl/Cd;
- compactos;
- full history toggle.

## 22.7 ParaView

- botón;
- final SIMPLE;
- absolute script;
- internalMesh;
- readiness;
- no animation automática.

## 22.8 Postprocess

- quick/full;
- run review required;
- uses existing script;
- no fake fields.

## 22.9 Convergencia RANS

- solo accepted;
- coarse/medium/fine;
- incomplete explicit;
- cost first 10k;
- GCI guard.

## 22.10 Deletion/reset

- only closed_medium active RANS;
- mesh remains;
- Results remains;
- archive exists;
- new run ID;
- queue starts medium;
- closed_coarse untouched.

## 22.11 Suite

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

Tests reales solo bajo acción explícita.

---

# 23. Documentación

Actualizar:

- `CHANGELOG.md`;
- `PROJECT_CONTEXT_FOR_CODEX.md`;
- `README_PROJECT_STRUCTURE.md`;
- README del laboratorio;
- help UI;
- API/version si cambia.

Documentar:

- nueva estructura sin resumen;
- menú superior;
- batch autónomo;
- timing 1–10k;
- ParaView RANS;
- postproceso completo;
- incidente closed_medium;
- reinicio de las cinco mallas.

---

# 24. Criterios de finalización

No declarar completo hasta que:

1. Resumen se elimine.
2. Menú quede bajo barra principal.
3. Timing 1–10k sea correcto.
4. Residual RANS se dibuje correctamente.
5. Layout sea compacto.
6. ParaView final esté accesible.
7. Postproceso completo RANS esté accesible.
8. Mensaje recurrente no aparezca antes del target.
9. Exista single-flight.
10. Batch sea desatendido.
11. `closed_medium` active run se archive/elimine de forma segura.
12. `closed_coarse` permanezca.
13. Queue de cinco quede ejecutada o realmente preparada.
14. Convergencia RANS se explique y funcione.
15. Tests pasen.
16. No se borre ninguna malla ni Results.
17. No se afirme ejecución si no se realizó.

---

# 25. Restricciones

- No borrar la malla `closed_medium`.
- No borrar `closed_coarse`.
- No borrar Results.
- No relanzar desde UI poll.
- No solicitar decisión antes de 10,000.
- No detener la cola por no convergencia no fatal.
- No transferir a URANS.
- No incluir overhead en tiempo solver 1–10k.
- No mostrar residual lineal.
- No mostrar gráfica vacía.
- No abrir ParaView durante batch.
- No generar postproceso completo automáticamente.
- No iniciar procesos duplicados.
- No usar datos archivados como estado activo.
