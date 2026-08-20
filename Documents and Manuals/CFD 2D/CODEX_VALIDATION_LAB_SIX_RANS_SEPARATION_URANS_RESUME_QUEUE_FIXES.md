# PROMPT MAESTRO DE CORRECCIÓN PARA CODEX
## Seis bases RANS, postproceso de pared y separación, aceptación coherente, monitor URANS, ejecución fresh/resume y cola temporal desatendida

Actúa como **ingeniero senior de CFD y aerodinámica estacionaria/transitoria, especialista en OpenFOAM Foundation 14, Gmsh, Python, Streamlit, postproceso de capa límite y arquitectura de software científico**.

Debes corregir y verificar la implementación existente del **Validation & Convergence Lab**. No crees una aplicación paralela, no regeneres las mallas y no reescribas desde cero funciones que ya existen.

La prioridad es conseguir un flujo reproducible y operativo:

```text
seis mallas registradas
-> seis bases RANS visibles y revisables
-> aceptación manual trazable
-> postproceso completo de pared
-> convergencia espacial RANS
-> transferencia de cada base aceptada a URANS
-> caso URANS fresh o resume correctamente resuelto
-> monitor activo durante la ejecución
-> cola de varios deltaT de mayor a menor, sin intervención
```

---

# 0. Fuentes obligatorias y estado documental

Lee antes de editar:

1. `CHANGELOG.md`
2. `PROJECT_CONTEXT_FOR_CODEX.md`
3. `README_PROJECT_STRUCTURE.md`
4. `AGENTS.md`
5. Los contratos del Validation Lab archivados bajo:
   `Documents and Manuals/CFD 2D/`
6. Los registros, manifiestos, logs y tests reales del laboratorio.

Estado documental actual:

```text
Application/backend API:     20
Validation Lab schema:       8
General solver schema:       13
OpenFOAM:                    Foundation 14
Canonical WSL runtime:
  /home/alejm/ramair_cfd/DESIGN_APP

Validation workspace:
  CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8
```

Mallas definitivas del laboratorio:

```text
closed_coarse
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

Celdas de las mallas abiertas definitivas:

```text
open_coarse = 223080
open_medium = 302692
open_fine   = 502474
```

El `CHANGELOG.md` declara funcionales varias capacidades que el usuario observa como defectuosas:

- monitor por intento concreto;
- pilot/production separados;
- ejecución URANS reanudable;
- aceptación RANS propagada;
- postproceso real mediante manifiestos;
- cola URANS secuencial.

Por tanto:

```text
el changelog es una expectativa documental;
el código, los manifests, los logs y una prueba funcional son la evidencia.
```

Clasifica cada capacidad como:

```text
IMPLEMENTED_AND_WORKING
IMPLEMENTED_BUT_BROKEN
PARTIALLY_IMPLEMENTED
DOCUMENTED_ONLY
MISSING
```

antes de modificarla.

---

# 1. Restricciones de seguridad

No debes:

- borrar `Results`;
- borrar mallas;
- borrar checkpoints RANS;
- sobrescribir simulaciones;
- inventar datos o estados PASS;
- convertir un gate automático fallido en convergencia automática;
- mezclar pilot y producción;
- copiar un checkpoint RANS sobre una producción URANS parcial;
- ejecutar CATIA;
- ejecutar una campaña URANS larga durante la corrección;
- utilizar Courant como diagnóstico físico de SIMPLE/RANS;
- eliminar historiales legacy sin archivarlos.

Debes:

- preservar los seis casos;
- archivar antes de modificar intentos activos;
- mantener `automatic_gate_status` separado de `review_status`;
- conservar los archivos históricos aunque se retiren del registro activo;
- usar solo campos OpenFOAM reales;
- realizar una prueba acotada después de superar los tests.

---

# 2. Objetivos de esta tarea

Implementar y verificar:

1. restaurar `closed_coarse` en:
   - tabla de mallas;
   - tabla de bases RANS;
   - cola de seis bases;
2. mostrar siempre las seis filas, aunque una esté completada o aceptada;
3. aceptar de forma manual y trazable las seis bases actuales;
4. propagar esa aceptación a:
   - tablas;
   - checkpoints;
   - convergencia espacial;
   - selector URANS;
5. generar:
   - `Cp(x/c)`;
   - `y+(x/c)`;
   - `Cf(x/c)` o esfuerzo cortante tangencial;
   - separación/reattachment;
6. abrir ParaView en la última iteración RANS real;
7. incorporar separación a:
   - postproceso RANS;
   - postproceso URANS;
   - convergencia de malla;
   - barridos por ángulo;
8. corregir el monitor URANS en directo;
9. migrar o archivar correctamente los 16 registros URANS incompatibles;
10. corregir el error fresh/resume:
    `Resume requested, but the case has no positive reconstructed time directory`;
11. hacer el pilot menos restrictivo:
    - PASS o WARN si el comportamiento está acotado;
    - FAIL solo con evidencia dura;
12. permitir producción directa sin pilot y sin motivo obligatorio;
13. ejecutar colas URANS de `deltaT` mayor a menor;
14. continuar tras divergencia, timeout o fallo de caso no global;
15. mantener el batch desatendido y reanudable.

---

# 3. Auditoría inicial obligatoria

Antes de editar:

```bash
git status
git diff
```

Inspecciona:

```text
BACKEND_API_VERSION
EXPECTED_BACKEND_API_VERSION
validation schema
schema migrations
mesh registry
RANS checkpoint registry
RANS review registry
URANS case registry
execution registry
batch registry
postprocess registry
active-run monitor registry
```

Inspecciona datos reales de:

```text
closed_coarse
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

Para cada base RANS determina:

```text
mesh_id
run_id
latest SIMPLE iteration
fields present
automatic gate
manual review
allowed uses
checkpoint state
postprocess state
```

Para los 16 registros URANS incompatibles determina:

```text
path exists?
case manifest exists?
mesh hash exists?
canonical mesh can be resolved?
attempt is pilot or production?
fields/times exist?
```

No borres nada durante la auditoría.

Genera:

```text
CFD_2D/reports/VALIDATION_LAB_REGISTRY_AND_URANS_RESUME_AUDIT_<date>.md
```

---

# 4. Las seis mallas deben aparecer siempre

## 4.1 Tabla de mallas

La tabla principal debe mostrar exactamente seis filas canónicas:

| Topología | Nivel | Malla | Celdas | Estado RANS | Uso RANS | Uso URANS | Acción |
|---|---|---|---:|---|---|---|---|

No omitir `closed_coarse` por estar finalizada o protegida.

## 4.2 Cola RANS

La cola también debe mostrar las seis:

| Orden | Malla | Iteración | Estado ejecución | Gate | Revisión | Acción de cola |
|---:|---|---:|---|---|---|---|

Ejemplo para `closed_coarse`:

```text
COMPLETED
USER_ACCEPTED_STATISTICALLY_STEADY
SKIP_ALREADY_COMPLETED
```

La fila permanece visible, pero la cola no la vuelve a ejecutar.

## 4.3 Selección de la primera incompleta

Al continuar:

1. recorrer las seis mallas en orden canónico;
2. mostrar todas;
3. omitir las completadas/aceptadas;
4. comenzar por la primera incompleta;
5. continuar desde su última iteración válida.

No eliminar filas del registro como mecanismo de `skip`.

## 4.4 Fuente única

La tabla de mallas y la cola deben derivar de:

```text
canonical six-mesh registry
+
RANS execution/review registries
```

No deben depender de que exista un run URANS.

---

# 5. Aprobación manual de las seis bases actuales

El usuario da una instrucción explícita para aceptar el estado actual de las seis bases RANS.

## 5.1 Operación

Añadir una acción administrativa trazable:

```text
Aprobar las seis bases actuales como estadísticamente estacionarias
```

No convertirlas en auto-convergidas.

Para cada base que tenga:

- identidad de malla correcta;
- campos finales reales;
- historial o diagnóstico disponible;

escribir:

```json
{
  "review_status": "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
  "review_source": "EXPLICIT_USER_BATCH_INSTRUCTION_2026-08-04",
  "allowed_uses": {
    "rans_spatial_convergence": true,
    "urans_initialization": true
  }
}
```

Mantener:

```text
automatic_gate_status = valor original
```

## 5.2 Base sin evidencia suficiente

Si una de las seis no tiene campos finales reales o tiene identidad incompatible:

- no fabricar checkpoint;
- no aprobar esa base;
- aprobar las demás;
- mostrar una tabla de excepciones;
- indicar la acción necesaria.

## 5.3 Snapshot URANS

Para cada base aceptada:

1. localizar la última iteración SIMPLE real;
2. materializar un restart inmutable;
3. copiar o enlazar campos requeridos;
4. verificar hashes;
5. registrar:
   - `U`;
   - `p`;
   - `nuTilda`;
   - `phi`, si existe;
   - `nut`, si existe;
6. habilitarla en el selector URANS.

## 5.4 Propagación visual

Después de aprobar:

- actualizar tabla de mallas;
- tabla RANS;
- selector de convergencia;
- selector URANS;
- checkpoint registry;
- invalidar cache;
- persistir tras reiniciar la app.

No requerir un texto de motivo.

La confirmación de la acción sí debe ser explícita.

---

# 6. Convergencia espacial RANS

## 6.1 Acción visible

Añadir en:

```text
RANS -> Convergencia espacial
```

el botón:

```text
Generar/actualizar análisis de convergencia espacial
```

Tabs:

```text
Perfil cerrado
Perfil abierto
```

## 6.2 Actualización

Al cambiar una revisión RANS:

- marcar el estudio como `OUTDATED`;
- recalcular automáticamente tablas ligeras;
- no ejecutar postproceso volumétrico pesado automáticamente.

El botón explícito genera:

- gráficas completas;
- overlays de pared;
- informe;
- GCI cuando sea admisible.

## 6.3 Entradas

Closed:

```text
closed_coarse
closed_medium
closed_fine
```

Open:

```text
open_coarse
open_medium
open_fine
```

Solo usar bases con:

```text
allowed_uses.rans_spatial_convergence = true
```

## 6.4 Resultados

Comparar:

- Cl;
- Cd;
- Cm;
- Cl/Cd;
- celdas;
- tamaño efectivo;
- diferencias relativas;
- tiempo por iteración;
- Cp;
- y+;
- Cf;
- posición de separación;
- posición de reattachment;
- longitud de burbuja separada.

No mezclar el perfil abierto y cerrado en una extrapolación común.

---

# 7. Postproceso completo RANS

## 7.1 Productos obligatorios

Para la ejecución RANS seleccionada:

```text
Cp(x/c)
y+(x/c)
Cf(x/c)
wallShearStress tangencial
residuos
continuidad
Cl/Cd/Cm
Cl/Cd
vorticidad
U
p
wallShearStress
separación/reattachment
```

## 7.2 Superficies

Closed:

```text
upper_external
lower_external
TE cap, identificado por separado
```

Open:

```text
upper_external
lower_external
upper_internal
lower_internal
physical lips/TE
```

Excluir:

```text
nonphysical inlet bridge
temporary stitching interfaces
farfield
frontAndBack
```

## 7.3 Botones

```text
Generar postproceso completo
Generar solo productos de pared
Abrir último estado en ParaView
Abrir carpeta de productos
```

## 7.4 ParaView

Al pulsar:

```text
Abrir último estado en ParaView
```

debe:

1. descubrir la última iteración SIMPLE real;
2. comprobar si está reconstruida;
3. si solo existe en `processorN`, reconstruir exclusivamente ese estado;
4. crear/verificar `.foam`;
5. lanzar un script Python con ruta absoluta;
6. usar `OpenFOAMReader`;
7. seleccionar `internalMesh`;
8. seleccionar el tiempo final;
9. habilitar arrays disponibles;
10. resetear cámara;
11. mostrar vista cercana;
12. registrar el proceso;
13. escribir readiness JSON y `.pvsm`.

No abrir el directorio genérico del checkpoint si no contiene el estado final.

---

# 8. Detección de separación de capa límite

## 8.1 Fundamento físico

En una capa límite bidimensional, la separación se asocia al punto donde el esfuerzo cortante tangencial de pared se anula y cambia de signo.

Definir:

\[
\tau_t = \boldsymbol{\tau}_w \cdot \mathbf{t}
\]

donde:

- \(\boldsymbol{\tau}_w\) es el vector `wallShearStress`;
- \(\mathbf{t}\) es la tangente local orientada según el flujo adherido.

Para un caso incomprensible donde OpenFOAM devuelve esfuerzo cinemático:

\[
C_f = \frac{2\tau_t}{U_\infty^2}
\]

Si el campo se convierte a esfuerzo dinámico:

\[
C_f = \frac{2\tau_t}{\rho_\infty U_\infty^2}
\]

La localización del cero es independiente de la escala dinámica/cinemática.

## 8.2 Campo OpenFOAM

Usar el function object:

```foam
wallShearStress
{
    type    wallShearStress;
    libs    ("libfieldFunctionObjects.so");
    patches (...);
}
```

o ejecución postproceso equivalente.

Para RANS:

```text
último estado SIMPLE
```

Para URANS:

```text
cada snapshot temporal retenido
y promedio temporal cuando corresponda
```

## 8.3 Extracción preferida

Preferir lectura directa de:

- centros de caras de pared;
- valores patch de `wallShearStress`;
- conectividad/orden geométrico.

No crear un VTK completo de todo el dominio solo para extraer la pared.

Fallback:

```text
VTK de superficie de pared
```

## 8.4 Orden de puntos

No ordenar únicamente por `x`.

El perfil puede:

- tener LE redondeado;
- tener TE redondeado;
- no ser monótono en x;
- tener superficies internas;
- tener varias ramas.

Construir cada rama mediante:

1. identidad de patch;
2. conectividad de caras/aristas;
3. geometría de perfil guardada;
4. arc length `s`.

Guardar:

```text
branch_id
s/c
x/c
y/c
```

## 8.5 Tangente local

Para centros ordenados:

```python
t_i = normalize(r_{i+1} - r_{i-1})
```

Usar diferencias unilaterales en extremos.

Orientar cada rama según el flujo adherido esperado:

```text
external upper/lower: desde LE hacia TE
internal branches: orientación definida por el manifest de geometría
```

Calibrar el signo con una región adherida de referencia.

Si la convención de `wallShearStress` produce signo invertido, no cambiar los datos brutos; guardar un factor de orientación en el manifest.

## 8.6 Cf y señal bruta

Guardar siempre:

```text
wallShearStress vector raw
tau_t raw
Cf raw
Cf filtered
```

No sobrescribir la señal bruta.

## 8.7 Filtrado

La señal cerca de cero puede alternar por ruido numérico.

Aplicar un filtro suave configurable:

```text
median local o Savitzky–Golay
```

La ventana debe definirse por longitud de arco, no por un número fijo de puntos.

Default orientativo:

```text
window length = 0.002c a 0.005c
```

El filtro solo sirve para detectar eventos. Los CSV conservan la señal original.

## 8.8 Histéresis

Definir:

```python
tau_eps = max(
    tau_absolute_floor,
    tau_relative_fraction * robust_attached_tau_reference,
)
```

Default inicial:

```text
tau_relative_fraction = 0.005
```

Configurable y reportado.

Clasificación:

```text
attached: tau_t > +tau_eps
reverse:  tau_t < -tau_eps
neutral:  |tau_t| <= tau_eps
```

Exigir persistencia:

```text
mínimo N caras
o longitud mínima de arco
```

para evitar falsos cruces.

## 8.9 Separación y reattachment

Separación:

```text
attached -> reverse
```

Reattachment:

```text
reverse -> attached
```

Interpolar el cero primero en `s`:

\[
s_0 = s_i-\tau_i\frac{s_{i+1}-s_i}{\tau_{i+1}-\tau_i}
\]

Después interpolar:

```text
x/c
y/c
```

No interpolar directamente en x en una rama no monótona.

## 8.10 Excluir falsos eventos

Excluir o etiquetar:

- región de estancamiento del LE;
- cambio geométrico de rama;
- TE cap;
- labios del inlet;
- interfaces no físicas;
- extremos donde no hay persistencia suficiente.

La región excluida debe derivarse de geometría y arc length, no de un único `x/c` hardcodeado.

## 8.11 Criterio auxiliar

Corroborar con velocidad tangencial cerca de pared:

\[
U_t = \mathbf U_\text{near-wall}\cdot\mathbf t
\]

Una zona separada debe mostrar:

```text
tau_t < 0
y preferentemente U_t < 0
```

Obtener `U` mediante:

- primera celda;
- `nearWallFields`;
- sample a una distancia normal controlada.

No sustituir el criterio de pared por un solo vector de velocidad.

## 8.12 Confianza

Guardar:

```text
HIGH:
  sign change + persistence + near-wall reversal agreement

MEDIUM:
  sign change + persistence

LOW:
  near-zero minimum without robust sign change

NOT_DETECTED:
  no event

UNRESOLVED:
  wall data or branch identity insufficient
```

## 8.13 Múltiples eventos

No limitar el resultado a un escalar.

Guardar una lista:

```json
{
  "branch_id": "upper_external",
  "events": [
    {
      "type": "separation",
      "s_over_c": 0.0,
      "x_over_c": 0.0,
      "y_over_c": 0.0,
      "confidence": "HIGH"
    },
    {
      "type": "reattachment",
      "s_over_c": 0.0,
      "x_over_c": 0.0,
      "y_over_c": 0.0,
      "confidence": "HIGH"
    }
  ]
}
```

Resultado principal para perfil cerrado:

```text
first external upper-surface separation after LE
```

pero mantener todos los eventos.

## 8.14 Perfil abierto

Para el perfil abierto, reportar por separado:

```text
external upper
external lower
internal upper
internal lower
lips/TE
```

En cavidades recirculantes, `s/c` y branch ID son más informativos que un único `x/c`.

## 8.15 URANS

Para cada tiempo retenido calcular:

```text
x_sep(t)
x_reattach(t)
bubble_length(t)
reverse-flow occupancy
```

Resultados:

- media;
- mediana;
- desviación;
- percentiles;
- min/max;
- porcentaje de tiempo separado;
- PSD opcional de `x_sep(t)` si hay muestras suficientes.

No crear una frecuencia si no hay duración temporal suficiente.

## 8.16 Outputs

```text
wall_shear_stress_vs_xc.csv
wall_shear_stress_vs_xc.png
skin_friction_coefficient_vs_xc.csv
skin_friction_coefficient_vs_xc.png
separation_events.json
separation_events.csv
separation_overlay_cp_cf.png
separation_summary.md
```

URANS:

```text
separation_time_history.csv
separation_time_history.png
reverse_flow_occupancy.png
```

## 8.17 Limitaciones

Reportar:

- y+;
- resolución tangencial;
- wall function o tratamiento de pared;
- filtro;
- thresholds;
- incertidumbre espacial;
- falta de cambio de signo;
- proximidad a LE/TE.

No declarar una separación precisa si la malla o y+ no permiten resolverla.

---

# 9. Integración de separación en toda la aplicación

## 9.1 Postproceso habitual por ángulo

Para cada `alpha_*`:

- calcular eventos;
- guardar en el paquete;
- mostrar:
  - `x_sep/c` vs alpha;
  - `x_reattach/c` vs alpha;
  - bubble length vs alpha;
- separar upper/lower.

No publicar un punto si el caso no es elegible según el flujo actual.

## 9.2 Mesh convergence

Añadir:

| Malla | x_sep/c | x_reattach/c | L_sep/c | Confianza | Δ vs fine |
|---|---:|---:|---:|---|---:|

Gráficas:

- separation location vs cell count;
- bubble length vs cell count;
- separation location vs effective h.

## 9.3 Space-time convergence

Para URANS añadir:

- mean x_sep;
- RMS x_sep;
- dominant frequency of x_sep;
- occupancy;
- sensitivity to dt and mesh.

Solo para runs aceptados y con duración suficiente.

## 9.4 Manifest

Cada postproceso debe registrar:

```text
separation_method_version
wall field source
time(s)
branch mapping
filter
thresholds
events
confidence
products
```

---

# 10. Pilot URANS: criterio de éxito

## 10.1 Objetivo real

El pilot no demuestra convergencia temporal ni precisión.

Solo comprueba:

- que el caso inicia;
- que no diverge inmediatamente;
- que los campos son compatibles;
- que PIMPLE progresa;
- que residuos y coeficientes permanecen acotados;
- que se puede estimar coste.

## 10.2 Hard FAIL

Clasificar `PILOT_FAIL` solo con evidencia dura:

- error de setup;
- checkpoint incompatible;
- campos ausentes;
- solver nonzero por fallo numérico;
- NaN/Inf;
- floating-point exception real;
- force runaway;
- `nuTilda` no físico/runaway;
- continuidad catastrófica;
- coeficientes creciendo sin cota;
- ausencia total de avance temporal;
- corrupción de campos;
- error de entorno.

## 10.3 WARN

Clasificar `PILOT_WARN` si completa o avanza de forma suficiente y está acotado, pero existe:

- Co superior al objetivo;
- residuales altos pero finitos;
- reducción residual débil;
- coeficientes oscilatorios;
- variación grande pero no runaway;
- pocos pasos;
- timeout limpio con evidencia parcial;
- continuidad por encima del objetivo pero estable.

`PILOT_WARN` debe mostrarse como:

```text
Viable con advertencias
```

y permitir producción.

## 10.4 PASS

`PILOT_PASS`:

- pasos completados;
- no hard fail;
- PIMPLE progresa;
- señales acotadas;
- warnings dentro de límites aceptables.

## 10.5 No exigir estacionariedad

No exigir en pilot:

- medias convergidas;
- PSD estable;
- número de ciclos;
- amplitud convergida;
- `Co < 1` estricto.

Co alto debe ser warning salvo que produzca inestabilidad.

## 10.6 Revisión visual

Mostrar:

- residuos;
- Cl/Cd/Cm;
- Cl/Cd;
- Co;
- continuity;
- deltaT;
- tiempo por paso;
- stages.

Acciones:

```text
Continuar a producción
Repetir pilot
Archivar
```

La aprobación manual puede conservarse, pero `PASS` o `WARN` no deben bloquear la producción.

---

# 11. Producción sin pilot

## 11.1 UI

Al seleccionar:

```text
Ejecutar sin prueba rápida
```

habilitar inmediatamente:

```text
Ejecutar producción URANS
```

## 11.2 Confirmación

No exigir motivo escrito.

Usar:

```text
checkbox:
  Confirmo que deseo ejecutar sin pilot aprobado
```

Nota opcional.

Guardar:

```json
{
  "pilot_bypass": true,
  "confirmed": true,
  "note": null,
  "timestamp": ""
}
```

No registrar como `PILOT_PASS`.

## 11.3 CLI

Eliminar la obligatoriedad de:

```text
--bypass-reason
```

o permitir que sea opcional.

Mantener:

```text
--bypass-pilot
```

La ausencia de motivo no debe bloquear.

---

# 12. Error URANS fresh/resume

## 12.1 Error observado

```text
RuntimeError:
Resume requested, but the case has no positive reconstructed time directory.
```

La producción era nueva, pero el runner entró en `prepare_resume()`.

## 12.2 Causa funcional probable

Se está infiriendo `resume=True` por:

- existencia del directorio del intento;
- existencia de `run_case.sh`;
- intento preparado;
- estado antiguo;
- flag heredado del controlador.

Eso es incorrecto.

## 12.3 Regla

La presencia del directorio no implica resume.

Definir:

```text
execution_intent = FRESH | RESUME
```

### FRESH

Usar cuando:

- intento nuevo;
- no hay tiempo físico positivo;
- solo existe `0/`;
- el checkpoint RANS se acaba de transferir.

Comportamiento:

```text
no --resume
no prepare_resume()
iniciar stage A desde t=0
```

### RESUME

Usar solo cuando:

- usuario pulsa Reanudar;
- manifest indica PARTIAL/STOPPED/TIMEOUT;
- existe tiempo físico positivo;
- o existe tiempo positivo en processor directories que puede reconstruirse.

## 12.4 Detección de tiempos

Buscar:

1. tiempos positivos reconstruidos;
2. tiempos positivos en `processorN`;
3. stage manifest;
4. logs.

Si solo existen en processor dirs:

- reconstruir;
- verificar;
- reanudar.

Si no existen:

```text
RESUME_NOT_AVAILABLE
```

Ofrecer:

```text
Ejecutar como caso nuevo
Archivar intento y crear uno nuevo
```

No lanzar un traceback genérico.

## 12.5 Bypass no implica resume

`--bypass-pilot` no debe activar `--resume`.

Son opciones independientes.

## 12.6 Intento fallido antes del solver

El `production_attempt_001` mostrado falló antes de avanzar.

Preservarlo como evidencia.

Crear:

```text
production_attempt_002
```

para la prueba corregida, salvo que el contrato permita de forma explícita un retry idempotente sin sobrescribir evidencia.

## 12.7 Tests

- fresh attempt with directory + only 0;
- resume partial with reconstructed time;
- resume partial with processor-only time;
- resume requested with no time;
- bypass fresh;
- script existing but fresh;
- failed pre-solver attempt preserved.

---

# 13. Monitor URANS y malla registrada

## 13.1 Error

```text
La ejecución activa no coincide con una malla registrada.
```

Un intento `closed_medium` debe resolver:

```text
mesh_id = closed_medium
```

## 13.2 Propagación

Al crear case definition:

```text
mesh_id
mesh_hash
topology
level
```

Al crear pilot/production:

- copiar esos campos;
- no inferirlos desde el texto del run ID durante monitorización;
- validarlos antes de `PREPARING`.

No permitir un nuevo intento sin `mesh_id` canónico.

## 13.3 Resolución de aliases

Para registros legacy, resolver en este orden:

1. mesh hash;
2. case manifest;
3. checkpoint manifest;
4. case path;
5. run ID alias, solo como último recurso.

No mapear ambiguamente por texto.

## 13.4 Los 16 registros históricos

No mostrar un warning genérico indefinidamente.

Ejecutar una migración metadata-only:

### Resoluble

Si coincide inequívocamente con una de las seis:

- añadir `mesh_id`;
- añadir provenance de migración;
- mantener archivos;
- hacerlo analizable.

### No resoluble pero archivos existentes

Mover la fila lógica a:

```text
registry/legacy_incompatible_urans.json
```

No mover ni borrar datos pesados.

### Path inexistente

Marcar:

```text
STALE_MISSING_PATH
```

retirarlo del selector activo y conservar el registro archivado.

## 13.5 Informe

```text
CFD_2D/reports/VALIDATION_LAB_LEGACY_URANS_REGISTRY_MIGRATION_<date>.md
```

Tabla:

| Run | Path | Hash | Resolución | Acción |
|---|---|---|---|---|

## 13.6 Monitor en directo

El monitor debe leer el intento activo desde que entra en `PREPARING`.

No esperar a que termine para generar las gráficas.

Flujo:

```text
PREPARING
-> RUNNING
-> incremental parsing
-> terminal state
```

Registrar desde el inicio:

```text
active log
residual path
force path
stage
mesh_id
```

Actualizar cada 30 s sin reiniciar solver.

---

# 14. Cola URANS desatendida

## 14.1 Orden

Default:

```text
deltaT descending
```

Para varios casos:

```python
sort key:
  -deltaT
  canonical mesh order
```

Esto garantiza:

```text
dt mayor -> dt menor
```

Permitir otro orden solo como opción avanzada.

## 14.2 Política por caso

```text
VALIDATED:
  skip

PARTIAL/STOPPED/TIMEOUT:
  resume

NOT_STARTED:
  fresh

PILOT PASS/WARN:
  production

PILOT missing:
  ejecutar pilot o bypass según política

PILOT hard FAIL:
  guardar y continuar o bypass si está explícitamente permitido

PRODUCTION DIVERGED:
  guardar y continuar con siguiente deltaT

SETUP FAIL:
  guardar y continuar si es específico del caso

TIMEOUT:
  writeNow, reconstruir, guardar, continuar

ENVIRONMENT/DISK/MPI GLOBAL FAIL:
  detener batch
```

## 14.3 Sin intervención

No mostrar modales entre casos.

Las decisiones se fijan antes:

```text
pilot policy
continue after divergence
continue after timeout
continue after case setup error
stop on environment error
```

Defaults:

```text
continue_after_divergence = true
continue_after_timeout = true
continue_after_case_error = true
stop_after_environment_error = true
```

## 14.4 Preservación

Para un caso divergido:

- conservar logs;
- último estado válido;
- campos diagnósticos;
- postproceso parcial;
- razón;
- no reutilizarlo como inicialización de otro `deltaT`.

Cada caso parte del mismo checkpoint RANS, no del caso anterior.

## 14.5 Reanudación del batch

Guardar:

```text
batch_id
ordered cases
current index
attempt ids
policy
last terminal state
```

Al reanudar:

- no crear duplicados;
- no volver a ejecutar casos completados;
- continuar partial;
- respetar orden `deltaT` descendente restante.

## 14.6 Estado final

```text
COMPLETED
COMPLETED_WITH_FAILURES
STOPPED_ENVIRONMENT_ERROR
STOPPED_BY_USER
```

---

# 15. Transferencia RANS -> URANS

Para cada malla:

```text
closed_coarse RANS -> closed_coarse URANS only
closed_medium RANS -> closed_medium URANS only
...
```

Antes de crear URANS:

- comparar mesh hash;
- physics hash;
- turbulence model;
- reference values;
- fields.

Transferir a `t=0` y registrar hashes.

No copiar a `t=0` si la producción ya tiene tiempo positivo.

Crear:

```text
rans_to_urans_transition_audit.json
```

con:

- source iteration;
- source fields;
- target fields;
- hashes;
- `phi` policy;
- first URANS step.

---

# 16. Postproceso y separación en URANS

Para runs parciales, divergidos o timeout:

- permitir postproceso hasta último tiempo válido;
- no declararlos aceptados;
- calcular separación solo en tiempos con campos válidos.

Mostrar:

```text
Cp
U
Co
vorticity
Cf
wallShearStress
x_sep(t)
x_reattach(t)
```

Animaciones solo bajo demanda.

Mantener una escala global temporal para U/Cp.

---

# 17. Modelo de datos

## 17.1 RANS row

```json
{
  "mesh_id": "closed_coarse",
  "execution_status": "COMPLETED",
  "automatic_gate_status": "NOT_CONVERGED",
  "review_status": "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
  "allowed_uses": {
    "rans_spatial_convergence": true,
    "urans_initialization": true
  }
}
```

## 17.2 URANS attempt

```json
{
  "case_id": "",
  "attempt_id": "",
  "run_id": "",
  "run_kind": "PRODUCTION",
  "execution_intent": "FRESH",
  "mesh_id": "closed_medium",
  "mesh_hash": "",
  "status": "PREPARING",
  "pilot_status": "PILOT_NOT_RUN",
  "pilot_bypass": true,
  "active_stage": "A"
}
```

## 17.3 Separation

```json
{
  "method_version": "wall_shear_zero_crossing_v1",
  "time": "",
  "branches": [],
  "primary_external_upper_separation": {},
  "limitations": []
}
```

---

# 18. UI requerida

## 18.1 RANS

### Ejecución

- tabla con seis;
- queue actions;
- closed_coarse visible/skipped.

### Revisión

- selector;
- aprobación individual;
- aprobación de las seis;
- extensión;
- estado propagado.

### Postproceso

- Cp;
- y+;
- Cf/separation;
- ParaView.

### Convergencia

- botón generar/actualizar;
- closed/open.

## 18.2 URANS

### Caso individual

- mesh;
- checkpoint;
- dt;
- fresh/resume selector resuelto automáticamente;
- pilot;
- bypass sin motivo;
- producción;
- monitor.

### Matriz

- casos;
- orden dt descendente;
- policy;
- start/resume;
- estado.

### Monitor global

- activo durante el proceso;
- mesh_id visible;
- stage;
- graphs.

---

# 19. Archivos que Codex debe inspeccionar

Como mínimo:

```text
CFD_2D/app/ramair_cfd2d_app.py
CFD_2D/app/workflow_backend.py

CFD_2D/scripts/ramair_2d_validation_study.py
CFD_2D/scripts/ramair_2d_validation_staged_runner.py
CFD_2D/scripts/ramair_2d_openfoam_runner.py
CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py

CFD_2D/scripts/ramair_2d_postprocess.py
CFD_2D/scripts/ramair_2d_rans_full_postprocess.py
CFD_2D/scripts/ramair_2d_urans_review.py
CFD_2D/scripts/ramair_2d_urans_matrix_manager.py

execution registry helpers
checkpoint registry helpers
monitor parsers
ParaView launcher
mesh convergence analyzer
angle sweep postprocessor
```

No dupliques lógica ya existente.

---

# 20. Tests obligatorios

## 20.1 Seis mallas

- exactly six rows;
- closed_coarse visible;
- queue skips accepted;
- continue starts first incomplete.

## 20.2 Aprobación

- batch accept six;
- gate unchanged;
- allowed uses;
- persistence;
- URANS selector;
- missing-field exception.

## 20.3 Postproceso

- Cp plot;
- y+ plot;
- wall shear;
- ParaView latest;
- manifest.

## 20.4 Separación

Usar señales sintéticas y reales:

- attached only;
- separation;
- separation + reattachment;
- noisy zero crossing;
- multiple events;
- nonmonotonic x;
- open branches;
- URANS time series;
- insufficient data.

Verificar interpolación en s, no x.

## 20.5 Pilot

- PASS bounded;
- WARN high Co;
- WARN high residual;
- FAIL NaN;
- FAIL setup;
- no stationarity requirement;
- production allowed from WARN.

## 20.6 Bypass

- no reason required;
- checkbox required;
- not PILOT_PASS;
- fresh execution.

## 20.7 Fresh/resume

- new attempt with directory and only 0 -> fresh;
- partial with positive time -> resume;
- processor-only time -> reconstruct/resume;
- no positive time -> RESUME_NOT_AVAILABLE;
- bypass does not imply resume.

## 20.8 Monitor

- active mesh matches;
- PREPARING visible;
- pilot live;
- production live;
- transition RANS->URANS;
- queue changes active run;
- no delayed-only rendering.

## 20.9 Legacy registry

- resolvable alias migrated;
- hash mismatch archived;
- missing path archived;
- no repetitive active warning;
- files preserved.

## 20.10 Matrix

- dt descending;
- divergence continues;
- timeout continues;
- case error continues;
- environment error stops;
- resume no duplication.

## 20.11 Commands

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

Real OpenFOAM tests only after unit/integration tests pass.

---

# 21. Verificación real acotada

Después de los tests:

## 21.1 RANS

- verificar las seis filas;
- aplicar la aceptación explícita solicitada;
- comprobar checkpoints;
- generar convergencia ligera.

No relanzar las seis bases.

## 21.2 URANS closed_medium

El intento `production_attempt_001` fallido debe preservarse.

Crear un nuevo intento:

```text
production_attempt_002
```

Ejecutar una producción muy corta:

- fresh;
- bypass pilot opcional sin motivo;
- monitor activo;
- confirmar tiempo positivo;
- stop limpio;
- postproceso parcial.

Después:

- pulsar reanudar;
- verificar que entra en resume;
- avanzar algunos pasos;
- detener.

## 21.3 Pilot

Ejecutar un pilot corto:

- resultado PASS o WARN si acotado;
- gráficos visibles;
- no fail por Co alto aislado;
- monitor en directo.

## 21.4 Cola

No ejecutar la campaña completa.

Hacer:

- dry-run/preflight de varios `deltaT`;
- orden descendente;
- fixture de divergencia;
- fixture de timeout;
- verificación de avance.

## 21.5 Informe

```text
CFD_2D/reports/VALIDATION_LAB_SIX_RANS_SEPARATION_URANS_QUEUE_SMOKE_<date>.md
```

---

# 22. Fases de implementación

## Fase 1

Auditoría, registry y reproducción.

## Fase 2

Seis mallas y aprobación.

## Fase 3

Postproceso Cp/y+/Cf/separación/ParaView.

## Fase 4

Fresh/resume y monitor.

## Fase 5

Pilot y bypass.

## Fase 6

Matriz desatendida.

## Fase 7

Convergencia y angle sweep.

## Fase 8

Tests y smoke.

## Fase 9

Documentación.

---

# 23. Documentación

Actualizar, solo con comportamiento verificado:

```text
CHANGELOG.md
PROJECT_CONTEXT_FOR_CODEX.md
README_PROJECT_STRUCTURE.md
```

Si cambia API/schema:

```text
BACKEND_API_VERSION
EXPECTED_BACKEND_API_VERSION
validation schema migration
```

de forma sincronizada.

Documentar referencias técnicas:

- OpenFOAM Foundation `wallShearStress`;
- NACA0012 surface skin-friction comparison;
- criterio de cero de esfuerzo cortante;
- limitaciones de wall functions/y+;
- método y versión de separación.

---

# 24. Criterios de finalización

No declares la tarea terminada hasta que:

1. las seis mallas aparezcan;
2. `closed_coarse` esté visible y no se relance;
3. las seis bases puedan aceptarse;
4. la aceptación se propague;
5. los seis checkpoints URANS sean utilizables cuando los campos existan;
6. Cp(x/c) funcione;
7. y+(x/c) funcione;
8. Cf/separación funcione;
9. ParaView abra la última iteración;
10. exista el botón de convergencia espacial;
11. pilot WARN no sea FAIL;
12. bypass no exija motivo;
13. fresh no entre en resume;
14. resume requiera tiempo positivo;
15. el monitor aparezca en directo;
16. `closed_medium` resuelva su mesh_id;
17. los 16 legacy se migren/archiven;
18. la cola ordene dt descendente;
19. la cola continúe tras divergencia;
20. los casos no se encadenen físicamente entre sí;
21. tests pasen;
22. el smoke real corto funcione;
23. no se pierdan datos.
