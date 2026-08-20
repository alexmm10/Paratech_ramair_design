# PROMPT MAESTRO PARA CODEX
## Reestructuración completa del Validation & Convergence Lab: RANS, URANS, convergencia espacial, discretización temporal, frecuencias, Courant y postproceso

Actúa como **ingeniero senior de CFD, aerodinámica estacionaria y transitoria, OpenFOAM, Gmsh, Python, Streamlit y arquitectura de software científico**.

Debes modificar la aplicación existente de forma incremental, verificable y compatible con los datos ya generados. No debes crear una aplicación paralela ni duplicar algoritmos CFD dentro de la interfaz.

Este documento es el contrato de implementación. Antes de editar, inspecciona el repositorio real y adapta nombres, imports, clases, rutas y contratos a la implementación existente.

---

# 0. Fuentes obligatorias y estado actual

Lee, en este orden:

1. `PROJECT_CONTEXT_FOR_CODEX.md`, versión 2026-07-30.
2. `README_PROJECT_STRUCTURE.md`.
3. `AGENTS.md`.
4. `CHANGELOG.md`.
5. Los documentos previos del Validation & Convergence Lab.
6. `CFD_2D/reports/TRANSIENT_TIMESTEP_MESH_SOLVER_STUDY_20260728.md`.
7. `Documents and Manuals/OpenFOAM/OpenFOAMUserGuide-A4.pdf`.
8. `Documents and Manuals/CFD 2D/Research Papers/Cummings_2008_Accurate_Time_Dependent_CFD_Timestep_Grid_Guidelines.pdf`.
9. Los scripts y tests reales del laboratorio, runner, writer, postproceso, monitorización, Results y ParaView.

Estado actual que debes preservar:

```text
Backend API: 18
Registro aislado del laboratorio: schema v5
OpenFOAM principal: Foundation 14
MPI máximo: 8 ranks
Workspace:
  CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8

Results:
  Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6

Condiciones:
  M = 0.15
  Re = 1.9e6
  c = 1 m
  alpha = 8 deg
  modelo = Spalart-Allmaras
```

Mallas definitivas:

| Topología | Coarse | Medium | Fine |
|---|---:|---:|---:|
| Cerrada | 203,691 | 333,826 | 618,382 |
| Abierta | 223,080 | 302,692 | 502,474 |

No regenerar ni sustituir estas mallas.

Contratos actuales que no se deben romper:

- workspace del laboratorio aislado de `active_workspace.json`;
- restauración coherente de geometría + caso + malla;
- `polyMesh` real obligatorio;
- ejecución de solver solo por acción explícita;
- single-flight por run;
- reanudación desde `latestTime`;
- campos parciales preservados tras stop/timeout;
- checkpoint SIMPLE específico de cada malla;
- startup/settling fuera de la ventana estadística;
- datos inexistentes no se representan mediante CSV vacíos;
- Results históricos no se reescriben;
- el gate automático y la revisión manual son estados distintos;
- las iteraciones SIMPLE nunca se etiquetan como tiempo físico;
- SIMPLE no utiliza Courant como diagnóstico físico.

---

# 1. Objetivo general

Reorganizar completamente el Validation & Convergence Lab para que represente un proceso científico claro:

```text
Mallas y condiciones
-> Ajustes de solver RANS/URANS
-> Generación y validación de bases RANS
-> Convergencia espacial RANS
-> Ejecución y revisión de casos URANS
-> Sensibilidad de correctores PIMPLE
-> Convergencia conjunta espacio-tiempo
-> Informes, almacenamiento y limpieza
```

La nueva implementación debe permitir:

1. gestionar seis mallas y toda su provenance;
2. ejecutar RANS individualmente o en cola;
3. revisar cada base RANS sin depender de un job activo;
4. usar una base RANS validada como estado `t=0` de cada URANS de esa malla;
5. crear, pilotar, ejecutar, continuar o reiniciar casos URANS malla–`deltaT`;
6. revisar resultados transitorios completos o parciales;
7. decidir si requieren más duración física;
8. ejecutar una sensibilidad PIMPLE 2/3/4;
9. realizar convergencia espacial RANS separada para perfil abierto y cerrado;
10. realizar convergencia espacio-temporal URANS separada para perfil abierto y cerrado;
11. integrar en el estudio espacio-temporal el análisis frecuencial y de Courant;
12. almacenar de forma compacta pero reproducible todos los resultados.

---

# 2. Nueva estructura de navegación

Reducir las once secciones actuales a seis secciones principales:

```text
1. Mallas y condiciones
2. Solver y estrategia
3. RANS
4. URANS
5. Convergencia espacio-tiempo
6. Informes y workspace
```

La barra de navegación del laboratorio debe permanecer inmediatamente bajo la barra principal de la aplicación.

## 2.1 Subsecciones de RANS

```text
RANS
  A. Ejecución
  B. Verificación y decisión
  C. Postproceso completo
  D. Convergencia espacial de malla
```

## 2.2 Subsecciones de URANS

```text
URANS
  A. Ejecución
     A1. Caso único
     A2. Matriz / cola
     A3. Prueba corta de viabilidad
  B. Revisión y convergencia individual
  C. Postproceso completo
  D. Sensibilidad PIMPLE 2/3/4
```

## 2.3 Convergencia espacio-tiempo

```text
Convergencia espacio-tiempo
  A. Perfil cerrado
  B. Perfil abierto
  C. Comparación de coste y precisión
  D. Frecuencias
  E. Courant
```

Frecuencias y Courant dejan de ser secciones principales independientes.

## 2.4 Monitor global desplegable

Debajo de la barra de secciones y por encima del contenido, mostrar en todas las páginas:

```text
▸ Monitor de ejecución activa
```

Debe estar colapsado por defecto y permitir observar el job activo sin cambiar de sección.

Este monitor es el único monitor en directo. Las vistas de revisión RANS/URANS usan datos almacenados y no se refrescan continuamente.

---

# 3. Arquitectura del workspace

Mantener la raíz actual y migrar sin destruir datos:

```text
CFD_2D/validation_studies/
  closed_open_M0p15_Re1p9e6_alpha8/
```

Organización lógica objetivo:

```text
closed_open_M0p15_Re1p9e6_alpha8/
  study_manifest.json
  workspace_state.json

  registry/
    mesh_registry.json
    execution_registry.json
    rans_checkpoint_registry.json
    review_registry.json
    batch_registry.json
    postprocess_registry.json
    space_time_registry.json

  configs/
    active_study_config.json
    solver_profiles/
    resolved_batches/
    resolved_runs/
    migrations/

  meshes/
    closed_coarse/
    closed_medium/
    closed_fine/
    open_coarse/
    open_medium/
    open_fine/

  rans/
    closed_coarse/<run_id>/
    closed_medium/<run_id>/
    closed_fine/<run_id>/
    open_coarse/<run_id>/
    open_medium/<run_id>/
    open_fine/<run_id>/

  urans/
    closed_coarse/<dt_id>/<run_id>/
    closed_medium/<dt_id>/<run_id>/
    ...
    open_fine/<dt_id>/<run_id>/

  pimple_outer_studies/
    <study_id>/

  postprocess/
    RANS/<mesh_id>/<run_id>/<postprocess_id>/
    URANS/<mesh_id>/<dt_id>/<run_id>/<postprocess_id>/

  convergence/
    rans_spatial/closed/
    rans_spatial/open/
    space_time/closed/
    space_time/open/
    frequency/
    courant/

  reports/
  logs/
  locks/
  cache/
  exports/
  archived_active_runs/
```

No mover archivos pesados innecesariamente. Si la implementación actual usa otras rutas, crear índices o symlinks/manifest references en lugar de duplicar casos.

## 3.1 Results

Publicar únicamente por acción explícita bajo:

```text
Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6/
  Convergence Studies/
```

El workspace es mutable. Results es histórico y curado.

## 3.2 Identidad

Cada run debe identificarse por:

```text
topology
mesh_id
solver_mode
dt_id, solo URANS
run_id
mesh_hash
physics_hash
solver_config_hash
checkpoint_hash
```

Los hashes no necesitan mostrarse en la tabla principal, pero deben estar en provenance.

---

# 4. Monitor global de ejecución

## 4.1 RANS

Encabezado:

```text
Closed | Medium | RANS/SIMPLE | Iteration 7,250 | Target 10,000
```

Mostrar dos gráficas compactas:

1. residuos iniciales, eje y logarítmico;
2. `Cl`, `Cd`, `Cm` y `Cl/Cd`.

No dibujar:

- continuidad;
- Courant;
- hotspots;
- campos volumétricos.

Continuidad sigue almacenada y forma parte del gate.

## 4.2 URANS

Encabezado:

```text
Open | Fine | URANS/PIMPLE | dt=<valor> | Stage D — Settling
```

Mostrar:

1. residuos por paso físico;
2. `Cl`, `Cd`, `Cm`, `Cl/Cd`.

Mostrar además métricas ligeras:

```text
physical time
t*
step
deltaT
Co max/mean
stage
elapsed
estimated remaining
```

No generar PSD ni leer campos volumétricos en directo.

## 4.3 Implementación

- refresh visual por defecto: 30 s;
- opciones: 15/30/60 s;
- parsing incremental;
- cache por offset y mtime;
- downsampling solo visual;
- single-flight;
- el monitor nunca lanza el solver;
- no regenerar PNG cuando no cambian los datos.



---

# 5. Mallas y condiciones

Mostrar las seis mallas definitivas y sus condiciones comunes.

Tabla principal:

| Topología | Nivel | Celdas | checkMesh | Calidad | Estado RANS | Estado URANS | Acción |
|---|---|---:|---|---|---|---|---|

Acciones:

```text
Cargar conjunto coherente
Abrir malla en Gmsh
Ver calidad
Ir a RANS
Ir a URANS
```

Mostrar condiciones:

```text
M
Re
c
alpha
U_inf
rho
mu
nu
modelo turbulento
dominio
Aref
lRef
```

No permitir que una restauración parcial mezcle geometría, caso y malla.

---

# 6. Solver y estrategia

Separar claramente cinco grupos:

```text
A. Recursos comunes
B. RANS/SIMPLE
C. Cola RANS
D. URANS/PIMPLE
E. Ejecución URANS y prueba corta
```

## 6.1 Recursos comunes

- ranks MPI;
- timeout por caso;
- timeout por etapa;
- prioridad;
- stop limpio;
- reanudación;
- logging;
- perfil de almacenamiento;
- refresh del monitor.

Máximo:

```text
8 ranks
```

No oversubscription.

## 6.2 Ajustes RANS/SIMPLE

Exponer:

- `potentialFoam`;
- esquemas;
- solvers lineales;
- tolerancias;
- relajación;
- `nNonOrthogonalCorrectors`;
- criterios del gate;
- ventana de fuerzas;
- bloque inicial;
- extensión;
- máximo;
- timeout por malla.

Defaults actuales:

```text
initial_iterations = 10000
extension_iterations = 2500
maximum_iterations = 20000
nNonOrthogonalCorrectors = 0
```

Preservar el perfil `robust_sa_initialization_v2` salvo cambio explícito.

## 6.3 Ajustes de la cola RANS

- orden;
- ejecutar todas;
- ejecutar seleccionadas;
- continuar desde primera incompleta;
- continuar una seleccionada;
- reiniciar una seleccionada;
- timeout por malla;
- continuar después de timeout/error no fatal;
- detener ante error de entorno;
- auto-extension;
- plateau detection.

## 6.4 Ajustes URANS/PIMPLE

Exponer:

- `nOuterCorrectors`;
- `nCorrectors`;
- `nNonOrthogonalCorrectors`;
- `momentumPredictor`;
- solvers lineales;
- scheme temporal de producción;
- `deltaT` fijo;
- duración de settling;
- duración de sampling;
- `writeInterval`;
- snapshots retenidos;
- sondas;
- criterios de boundedness;
- timeout;
- duración máxima.

Baseline:

```foam
PIMPLE
{
    momentumPredictor         yes;
    nOuterCorrectors          3;
    nCorrectors               2;
    nNonOrthogonalCorrectors  1;
}
```

Producción:

```foam
ddtSchemes
{
    default backward;
}
```

No usar `nOuterCorrectors` para compensar un `deltaT` físicamente inviable.

---

# 7. Estrategia inicial URANS configurable y reducida

El método progresivo de `deltaT` debe permanecer, pero limitarse a una fase inicial corta.

## 7.1 Editor de etapas

Añadir un subgrupo:

```text
Inicialización temporal URANS
```

Cada etapa editable debe incluir:

| Campo | Descripción |
|---|---|
| enabled | activar/desactivar |
| scheme | Euler / backward / CrankNicolson |
| dt_factor | factor respecto a `dt_target` |
| duration_mode | pasos o t* |
| duration | número |
| purpose | startup/history/transition |

## 7.2 Default corto

Usar por defecto:

```text
Stage A:
  scheme = Euler
  dt = 0.25 * dt_target
  steps = 25

Stage B:
  scheme = Euler
  dt = 0.50 * dt_target
  steps = 25

Stage C:
  scheme = Euler
  dt = 1.00 * dt_target
  steps = 50

Stage D:
  scheme = backward
  dt = dt_target
  duration = settling configurado

Stage E:
  scheme = backward
  dt = dt_target
  duration = sampling configurado
```

Razones:

- A-C ocupan solo 100 pasos;
- estabilizan la transferencia;
- Stage C crea historia suficiente a `dt_target` antes de BDF2;
- el coste se concentra en D-E;
- startup no entra en estadísticas.

Permitir valores distintos, pero mostrar advertencias si:

- se elimina toda historia a `dt_target`;
- se usa `backward` sin niveles temporales;
- se introduce un salto excesivo;
- la progresividad ocupa una fracción grande del tiempo total.

## 7.3 Variación de esquema

Permitir:

```text
startup scheme
transition scheme
production scheme
```

Opciones soportadas:

```text
Euler
backward
CrankNicolson 0.9
```

Default:

```text
Euler -> backward
```

`CrankNicolson 0.9` es sensibilidad, no baseline.

## 7.4 Presupuesto temporal

Mostrar:

- pasos A/B/C;
- pasos de settling;
- pasos de sampling;
- tiempo físico;
- t*;
- total de pasos;
- muestras por ciclo;
- Nyquist;
- tiempo estimado.

Eliminar del cálculo previo:

```text
estimación de almacenamiento
```

El almacenamiento se analiza en Informes.

---

# 8. Prueba corta de viabilidad URANS

## 8.1 Objetivo

La prueba corta verifica:

- transferencia RANS -> URANS;
- presencia y hash de campos;
- `phi`;
- boundedness;
- ausencia de divergencia inmediata;
- convergencia PIMPLE;
- Courant;
- coste por paso;
- tiempo estimado de producción.

No valida:

- frecuencias;
- medias;
- convergencia temporal;
- independencia de malla.

## 8.2 Secuencia reducida

Default:

```text
Pilot A:
  Euler
  0.25 dt
  10 pasos

Pilot B:
  Euler
  0.50 dt
  10 pasos

Pilot C:
  Euler
  1.00 dt
  20 pasos

Pilot D:
  backward
  dt target
  30 pasos
```

Total inicial:

```text
70 pasos
```

Editable.

## 8.3 Criterios

`PILOT_PASS` si:

- no hay NaN/Inf;
- fuerzas acotadas;
- variables turbulentas acotadas;
- continuidad no deriva;
- PIMPLE progresa;
- no hay error de setup;
- no hay crecimiento explosivo de Co;
- se completan los pasos.

`PILOT_WARN`:

- coste alto;
- residual interno débil;
- Co alto localizado;
- señales acotadas.

`PILOT_FAIL`:

- divergencia;
- error;
- campos incompatibles;
- timeout sin pasos suficientes.

## 8.4 Estimación de coste

Excluir:

- setup;
- decomposition;
- reconstruction;
- writes;
- primer paso;
- cambios de etapa.

Calcular:

```text
median s/step
p25/p75
estimated settling time
estimated sampling time
estimated total wall time
```

No calcular almacenamiento.

## 8.5 Pilot para una matriz

No asumir que:

```text
malla coarse + mayor dt
```

sea siempre la combinación más restrictiva.

Para un mismo `deltaT`, una malla fina suele contener celdas más pequeñas y puede producir mayor Courant.

Política default segura:

```text
ejecutar un pilot por cada malla seleccionada usando su mayor dt
```

Política económica opcional:

```text
pilot único del caso con mayor riesgo estimado
```

El riesgo se calcula con:

- `dt`;
- tamaño mínimo/volumen;
- histórico de Co;
- histórico de `deltaT` estable;
- topología;
- checkpoint.

No seleccionar el caso solo por número total de celdas.



---

# 9. Sección RANS

# 9.A Ejecución RANS

## 9.A.1 Tabla de bases

Mostrar:

| Orden | Malla | Estado | Iteración | Target | Gate | Timeout | Última actualización | Acción |
|---:|---|---|---:|---:|---|---|---|---|

No mostrar en la vista principal:

- checkpoint hash;
- mesh hash;
- rutas;
- IDs internos largos.

## 9.A.2 Ejecución individual

Acciones:

```text
Ejecutar nueva base
Continuar desde última iteración
Extender 2500
Detener y escribir estado
Archivar y reiniciar
```

Comportamiento por defecto:

```text
continuar, no reiniciar
```

## 9.A.3 Cola automática

Permitir:

```text
Ejecutar/continuar las seis bases
Ejecutar/continuar selección
```

Por malla:

```text
si no existe:
  ejecutar hasta 10000

si timeout antes de 10000:
  guardar TIMEOUT_PARTIAL
  continuar siguiente

en target:
  evaluar gate

si strict/plateau:
  finalizar
  continuar siguiente

si no convergida y sigue cambiando:
  extender 2500 hasta 20000

si no hay cambio significativo:
  marcar REVIEW_REQUIRED
  continuar siguiente

si llega a 20000:
  guardar REVIEW_REQUIRED
  continuar siguiente
```

No pedir input durante la cola.

## 9.A.4 Timeout por malla

Al alcanzar timeout:

1. solicitar `writeNow`;
2. esperar escritura limpia;
3. conservar fields/histories;
4. reconstruir únicamente lo necesario;
5. marcar `TIMEOUT_PARTIAL`;
6. liberar MPI/lock;
7. continuar con la siguiente malla.

No mantener el solver enganchado.

## 9.A.5 Reanudación

Al reanudar la cola:

- detectar bases finalizadas;
- detectar parciales;
- seleccionar la primera incompleta;
- continuar desde última iteración;
- no sobrescribir campos;
- conservar configuración congelada;
- no volver a ejecutar `potentialFoam`.

---

# 9.B Verificación y decisión RANS

Esta subsección no contiene monitores en directo.

## 9.B.1 Selector

Permitir seleccionar cualquier ejecución RANS:

- activa;
- parcial;
- finalizada;
- timeout;
- restaurada;
- batch;
- histórica compatible.

## 9.B.2 Análisis

Botón:

```text
Analizar resultado RANS
```

No ejecuta solver.

Genera:

- tabla de configuración;
- tabla de residuos;
- tabla de fuerzas;
- comparación de ventanas;
- gate;
- tiempos;
- dos gráficas principales;
- recomendaciones.

## 9.B.3 Gráficas

1. residuos vs iteración SIMPLE:
   - y log;
   - p, U, nuTilda;
   - título y ejes;
2. coeficientes vs iteración:
   - Cl, Cd, Cm;
   - Cl/Cd en eje secundario;
3. medias móviles;
4. drift;
5. comparación de bloques;
6. gate visual.

No mostrar Courant/hotspots.

Continuidad:

- tabla;
- criterio;
- descarga;
- no gráfica principal.

## 9.B.4 Tabla final

Configuración:

| Parámetro | Valor |
|---|---:|
| Malla | |
| Celdas | |
| Iteraciones | |
| Bloques | |
| Tiempo solver 1–10k | |
| Tiempo total | |
| s/iteración | |
| Modelo | |
| SIMPLE correctors | |
| Estado automático | |
| Revisión | |

Estadísticas:

| Métrica | Media | Std | RMS | Drift | Cambio ventanas | Gate |
|---|---:|---:|---:|---:|---:|---|
| Cl | | | | | | |
| Cd | | | | | | |
| Cm | | | | | | |
| Cl/Cd | | | | | | |

Residuos:

| Campo | Final | Mediana | Slope log | Preferred | Ceiling | Gate |
|---|---:|---:|---:|---:|---:|---|
| p | | | | | | |
| U | | | | | | |
| nuTilda | | | | | | |

## 9.B.5 Decisión

Opciones:

```text
Aceptar como RANS estadísticamente estacionaria
Aceptar solo como base URANS
Extender 2500 iteraciones
Rechazar
Revocar decisión
```

La nota es opcional.

Separar:

```text
automatic_gate_status
review_status
allowed_uses
```

Nunca reescribir el gate automático.

---

# 9.C Postproceso completo RANS

## 9.C.1 Selector y acciones

```text
Seleccionar ejecución RANS
Diagnóstico rápido
Postproceso completo
Generar visualizaciones finales
Abrir en ParaView
Abrir carpeta de productos
```

Debe funcionar aunque el caso esté:

- parcial;
- timeout;
- review required;
- aceptado;
- auto-convergido.

## 9.C.2 Productos escalares

- residuals;
- continuity;
- Cl/Cd/Cm;
- Cl/Cd;
- estadísticas;
- gate;
- coste;
- storage inventory.

## 9.C.3 Productos de campo

Solo en el último estado SIMPLE disponible:

- p;
- U;
- Cp;
- vorticity;
- yPlus;
- wallShearStress;
- Cf;
- streamlines;
- delta99;
- wall-normal profiles;
- separation/reattachment;
- vistas:
  - perfil cercano;
  - perfil + estela.

## 9.C.4 Prohibición de Courant en RANS

Eliminar de la rama RANS:

```text
Courant image
Courant hotspots
Co animation
Co convergence
```

SIMPLE usa pseudo-tiempo iterativo. El `Co` no debe presentarse como diagnóstico físico RANS.

Si existen productos históricos, etiquetarlos:

```text
NOT_APPLICABLE_TO_RANS
```

y no mostrarlos por defecto.

## 9.C.5 Escalas de U y Cp

Para una imagen estática:

```text
U scale default = [min(U finite), max(U finite)]
Cp scale default = [min(Cp finite), max(Cp finite)]
```

Añadir opciones:

```text
Exact min/max
Robust 1–99 percentile
Manual
```

Mostrar valores en la barra de color.

No reutilizar límites de otro caso o frame.

## 9.C.6 ParaView

Corregir el caso `closed_coarse`.

El launcher debe:

1. resolver el caso RANS reconstruido;
2. crear/verificar `.foam`;
3. descubrir la última iteración SIMPLE real;
4. usar script Python con ruta absoluta;
5. crear `OpenFOAMReader`;
6. seleccionar `internalMesh`;
7. habilitar arrays reales;
8. actualizar pipeline;
9. fijar tiempo final;
10. resetear cámara;
11. mostrar;
12. guardar readiness JSON y `.pvsm`;
13. registrar el proceso.

No depender del estado de registro de ParaView.

## 9.C.7 Registro de outputs

Cada postproceso escribe:

```text
postprocess_manifest.json
```

con:

- run;
- inputs;
- generated products;
- paths;
- timestamp;
- fields;
- errors;
- status.

La UI debe renderizar desde este manifest. No inferir rutas diferentes en frontend/backend.

---

# 9.D Convergencia espacial RANS

## 9.D.1 Casos elegibles

Incluir:

```text
AUTO_CONVERGED_STRICT
AUTO_CONVERGED_WITH_PLATEAU_WARNING
USER_ACCEPTED_STATISTICALLY_STEADY
```

Excluir:

```text
INITIALIZATION_ONLY
REVIEW_REQUIRED
REJECTED
TIMEOUT_PARTIAL no aceptado
```

## 9.D.2 Estudios separados

```text
Perfil cerrado:
  closed_coarse
  closed_medium
  closed_fine

Perfil abierto:
  open_coarse
  open_medium
  open_fine
```

No mezclar geometrías para extrapolación.

## 9.D.3 Métricas

- cell count;
- effective h;
- refinement ratios;
- mesh quality;
- Cl/Cd/Cm/ClCd;
- std/RMS/drift;
- relative difference;
- difference vs fine;
- time 1–10k;
- s/iteration;
- total time;
- Cp;
- Cf;
- y+;
- wall shear;
- separation;
- delta99.

## 9.D.4 Gráficas

- coefficient vs cells;
- coefficient vs effective h;
- relative difference vs cells;
- cost vs cells;
- cost vs error;
- Cp overlay;
- Cf overlay;
- y+ overlay;
- separation position;
- RANS acceptance provenance.

## 9.D.5 Independencia de malla

No declarar “mesh independent” solo porque dos medias parezcan próximas.

Evaluar:

- monotonicidad;
- cambios medium–fine;
- uncertainty/noise;
- window uncertainty;
- observed order cuando sea resoluble;
- GCI si ratios y datos lo permiten.

Si no:

```text
INSUFFICIENT_EVIDENCE_FOR_MESH_INDEPENDENCE
```



---

# 10. Sección URANS

# 10.A Ejecución URANS

## 10.A.1 Prerrequisito

Cada caso URANS necesita:

```text
RANS checkpoint de la misma malla
allowed_uses.urans_initialization = true
hashes compatibles
required fields completos
PILOT_PASS o override diagnóstico explícito
```

Transferir:

- U;
- p;
- nuTilda;
- phi si existe;
- nut/alphat si existen;
- campos requeridos detectados dinámicamente.

Verificar hashes.

Resetear el tiempo físico a:

```text
t = 0
```

## 10.A.2 Registro de casos

Tabla:

| Malla | dt | dt* | Estado | Pilot | Stage | t* completado | Sampling | Revisión | Acción |
|---|---:|---:|---|---|---|---:|---|---|---|

Estados:

```text
CREATED
PREPARED
PILOT_PENDING
PILOT_PASS
PILOT_WARN
PILOT_FAIL
RUNNING
TIMEOUT_PARTIAL
STOPPED_PARTIAL
DIVERGED
RUN_ERROR
COMPLETED
REVIEW_REQUIRED
VALIDATED
REJECTED
```

## 10.A.3 Caso único

Controles:

- malla;
- checkpoint RANS;
- `deltaT`;
- startup profile;
- settling;
- sampling;
- PIMPLE;
- timeout;
- ranks;
- writes;
- probes.

Acciones:

```text
Preparar y verificar
Ejecutar prueba corta
Ejecutar/continuar
Detener y escribir
Reiniciar desde t=0
```

Reiniciar requiere archivar la ejecución previa.

## 10.A.4 Matriz / cola

Seleccionar:

- mallas;
- lista de `deltaT`;
- orden;
- política de pilot;
- política de resume;
- timeout;
- continue on failure.

Por caso:

```text
si VALIDATED:
  skip

si COMPLETED/REVIEW_REQUIRED:
  no reiniciar; esperar revisión

si PARTIAL:
  resume por defecto

si PILOT no existe:
  pilot

si PILOT_PASS/WARN autorizado:
  run

si PILOT_FAIL:
  mark and continue

si timeout:
  write/preserve/continue

si divergence/error:
  preserve and continue si no es error de entorno
```

## 10.A.5 Continuidad desatendida

La cola debe:

- sobrevivir a refresh;
- usar single-flight;
- almacenar batch pointer;
- continuar tras timeout/error no fatal;
- liberar MPI;
- no repetir stages completados;
- usar `latestTime`;
- no copiar checkpoint sobre parcial;
- permitir reanudar tras cerrar la app.

## 10.A.6 Timeout

Al timeout:

1. `writeNow`;
2. cierre limpio;
3. preservar snapshots y escalares;
4. reconstruir tiempos retenidos;
5. marcar `TIMEOUT_PARTIAL`;
6. liberar lock;
7. continuar.

---

# 10.B Revisión y convergencia individual URANS

Esta subsección usa datos almacenados y no muestra monitor en directo.

## 10.B.1 Selector

Debe incluir:

- completados;
- parciales;
- timeouts;
- stopped;
- diverged con datos;
- restaurados;
- batch;
- single.

## 10.B.2 Diagnóstico de señales oscilatorias

No evaluar únicamente medias.

Calcular por bloques:

- mean Cl/Cd/Cm/ClCd;
- std;
- RMS;
- peak-to-peak;
- amplitude;
- dominant frequency;
- dominant St;
- period;
- PSD peak amplitude;
- secondary peaks;
- autocorrelation;
- integral time scale;
- drift;
- number of cycles;
- stationarity.

## 10.B.3 Ventanas

Separar:

```text
startup A-C
settling D
sampling E
```

Solo E entra en validación.

Si un caso parcial no alcanza E:

```text
INSUFFICIENT_PRODUCTION_WINDOW
```

Si E es corta:

```text
MORE_PHYSICAL_TIME_REQUIRED
```

## 10.B.4 Suficiencia temporal

Por defecto:

```text
>= 10 ciclos de la frecuencia más baja relevante
preferible >= 20
```

Comparar 4 bloques de la ventana final:

```text
mean variation
RMS variation
dominant St variation
amplitude variation
```

Criterios iniciales configurables:

```text
mean < 1–2%
RMS < 5%
dominant St < 2–3%
amplitude < 10%
```

## 10.B.5 Decisión

Opciones:

```text
Validar para estudio espacio-tiempo
Extender +Δt*
Extender +N ciclos
Aceptar solo como diagnóstico
Rechazar
Revocar
```

La extensión debe calcular:

```text
additional physical time
additional t*
additional steps
estimated wall time
```

No almacenamiento.

## 10.B.6 Parciales y fallos

Permitir postprocesar:

- hasta el último tiempo;
- fuerzas;
- Co;
- sondas;
- snapshots.

No validar un run divergido.

Un run timeout puede validarse si la ventana disponible es suficiente y la causa fue solo wall-time; esta decisión requiere revisión explícita.

---

# 10.C Postproceso completo URANS

## 10.C.1 Productos escalares

- Cl/Cd/Cm/ClCd;
- continuity;
- residuals;
- deltaT;
- Co;
- probes;
- PSD;
- St;
- spectrogram;
- block statistics;
- phase/coherence;
- execution cost.

## 10.C.2 Campos

- Cp;
- U;
- Co;
- p;
- vorticity;
- yPlus;
- wallShearStress;
- Cf;
- pressure RMS;
- velocity RMS;
- phase-averaged fields;
- selected snapshots.

## 10.C.3 Imágenes

Generar:

```text
perfil cercano
perfil + estela
```

para:

- Cp;
- U;
- Co;
- vorticity.

## 10.C.4 Escalas

Imagen estática:

```text
default exact finite min/max
```

Opciones:

```text
exact
robust 1–99
manual
```

Animaciones:

No cambiar la escala frame a frame.

Usar por defecto:

```text
global min/max calculado sobre todos los frames seleccionados
```

Opciones:

```text
global exact
global robust
manual
```

Esto permite comparar colores temporalmente.

## 10.C.5 Animaciones

Crear bajo demanda:

- Cp;
- U.

Solo si existen al menos dos snapshots temporales.

No exportar VTK de todos los tiempos si ParaView puede leer directamente el caso OpenFOAM.

Usar:

- OpenFOAM reader;
- tiempos retenidos;
- pvbacth;
- GIF/MP4;
- manifest.

## 10.C.6 Almacenamiento

No almacenar todo el dominio en cada paso.

Conservar:

- escalares cada paso;
- snapshots limitados;
- último estado;
- puntos suficientes para animación/phase analysis;
- `purgeWrite`.

---

# 10.D Sensibilidad PIMPLE 2/3/4

## 10.D.1 Caso

Preparar:

```text
topology = closed
mesh = closed_coarse
RANS base = historia final existente de 20000 iteraciones
dt = un valor que pase pilot
nOuterCorrectors = 2, 3, 4
```

## 10.D.2 Tratamiento de closed_coarse

El contexto actual registra:

```text
automatic gate = NOT_CONVERGED
review = RANS_REVIEW_REQUIRED
```

El usuario ha indicado explícitamente en esta solicitud que el caso de 20,000 iteraciones se considera finalizado/convergido externamente.

Implementar una migración trazable:

```text
execution_status = COMPLETED
automatic_gate_status = conservar NOT_CONVERGED
review_status = RANS_USER_ACCEPTED_STATISTICALLY_STEADY
review_source = EXPLICIT_USER_INSTRUCTION
allowed_uses.rans_spatial_convergence = true
allowed_uses.urans_initialization = true
```

Condiciones:

- la historia real de 20,000 debe existir;
- los hashes deben corresponder a `closed_coarse`;
- no crear datos;
- no cambiar el gate automático;
- no volver a ejecutar la base;
- reconstruir checkpoint solo a partir del estado final real.

## 10.D.3 Aislamiento

Crear tres clones desde el mismo checkpoint:

```text
closed_coarse_pimple2
closed_coarse_pimple3
closed_coarse_pimple4
```

Cambiar únicamente:

```text
nOuterCorrectors
```

Mantener idénticos:

- dt;
- mesh;
- checkpoint;
- nCorrectors;
- nNonOrthogonalCorrectors;
- schemes;
- duration;
- ranks;
- outputs.

No encadenar 2 -> 3 -> 4.

## 10.D.4 Duración

Default económico:

```text
settling = 5 t*
sampling = 20 t*
```

Si no hay suficientes ciclos, frecuencia = preliminar.

## 10.D.5 Resultados

- residual reduction por outer;
- continuity;
- boundedness;
- Cl/Cd/Cm means;
- RMS;
- PSD;
- St;
- Co;
- s/step;
- CPU/physical second;
- cost ratio;
- difference vs outer=4.

Recomendar el menor que sea equivalente.

Mantener 3 como baseline hasta completar el estudio.



---

# 11. Convergencia espacio-tiempo

Esta sección usa únicamente casos URANS revisados y autorizados.

## 11.1 Separación

```text
Perfil cerrado
Perfil abierto
```

No realizar extrapolación conjunta entre geometrías diferentes.

## 11.2 Registro

Cada caso válido aporta:

```text
mesh_id
cell_count
effective h
dt
dt*
sampling duration
cycles
mean/RMS
dominant St
PSD peak amplitude
Co statistics
cost
review provenance
```

## 11.3 Comparación temporal por malla

Para cada malla:

- mean Cl/Cd/Cm/ClCd vs `dt*`;
- RMS vs `dt*`;
- dominant St vs `dt*`;
- wave number `1/St` vs `dt*`;
- PSD peak amplitude vs `dt*`;
- relative difference vs finest accepted dt;
- cost vs `dt`.

## 11.4 Comparación espacial por dt

Para cada `dt` común o comparable:

- metrics vs cell count;
- metrics vs effective h;
- differences coarse/medium/fine;
- frequency vs mesh;
- Cp mean/RMS overlays;
- cost vs error.

No interpolar silenciosamente entre `dt` distintos. Si se necesita una sección común, etiquetar el método.

## 11.5 Matriz 2D

Generar:

```text
x = cell count o h_eff
y = dt*
color = relative error / acceptance
marker = status
```

Métricas seleccionables:

- Cl mean;
- Cd mean;
- Cm mean;
- Cl/Cd mean;
- Cl RMS;
- dominant St;
- PSD amplitude.

## 11.6 Referencia

La referencia numérica interna es:

```text
malla más fina + dt más fino aceptado + ventana suficiente
```

No llamarla “verdad física”.

## 11.7 Criterios

Defaults configurables:

```text
mean Cl < 1%
mean Cd < 2%
mean Cm < 2%
RMS < 5%
dominant St < 2%
PSD amplitude < 10%
Cp/Cf topology unchanged
```

## 11.8 Evidencia profesional

Generar:

- tablas;
- figures;
- uncertainty notes;
- accepted/rejected matrix;
- Pareto cost/accuracy;
- report Markdown;
- JSON machine-readable.

## 11.9 Limitaciones

Mostrar:

- 2D URANS;
- Spalart-Allmaras;
- incompressible Mach 0.15 approximation;
- weak refinement ratio;
- non-monotonic convergence;
- insufficient cycles;
- manually accepted cases;
- partial timeout.

---

# 12. Análisis frecuencial integrado

## 12.1 Señales

- Cl;
- Cd;
- Cm;
- Cl/Cd;
- probes;
- cavity pressure;
- inlet mass flow;
- upper/lower lip probes;
- wake probes.

## 12.2 Método

Welch:

```python
window = "hann"
detrend = "constant"
overlap = 0.5
```

Guardar:

- sampling frequency;
- Nyquist;
- df;
- nperseg;
- noverlap;
- segments;
- duration;
- cycles.

## 12.3 Salidas

- PSD;
- dominant St;
- secondary modes;
- amplitude;
- spectrogram;
- autocorrelation;
- coherence;
- phase;
- frequency convergence vs dt;
- frequency convergence vs mesh.

## 12.4 Uniformidad temporal

La producción debe usar `dt` fijo.

Si un histórico externo tiene tiempo variable:

- no aplicar FFT directa;
- remuestrear;
- marcarlo;
- guardar señal original y remuestreada.

---

# 13. Courant integrado

Solo URANS.

## 13.1 Métricas

- Co max;
- mean;
- p95;
- p99;
- p99.9;
- fraction >1;
- fraction >2;
- top cells;
- regions;
- hotspots.

## 13.2 Interpretación

No usar `Co=1` como rechazo automático.

Analizar:

- extensión espacial;
- calidad de celda;
- región física;
- convergence sensitivity;
- dt refinement.

## 13.3 Gráficas

- Co vs time;
- Co stats vs dt;
- Co stats vs cells;
- hotspot map;
- region table;
- Co vs acceptance;
- Co vs cost.

No generar productos Co en RANS.

---

# 14. Informes y workspace

Mantener y reorganizar:

```text
Resumen del workspace
Consumo de espacio
Inventario de archivos
Runs
Checkpoints
Postprocess
Limpieza
Tests
Informes
Exportación
```

## 14.1 Almacenamiento

Mostrar:

- bytes por run;
- fields;
- processor directories;
- reconstructed times;
- images;
- animations;
- logs;
- scalar histories;
- top files.

## 14.2 Limpieza

Acciones:

```text
Limpiar outputs volumétricos activos
Limpiar cache de plots
Eliminar VTK duplicado
Archivar run
Eliminar run activo
```

Preservar siempre:

- Results;
- manifests;
- scalar histories;
- último estado;
- checkpoint;
- reports.

## 14.3 Informes

Generar:

```text
RANS batch report
RANS spatial convergence report
URANS execution report
PIMPLE sensitivity report
Space-time convergence report
Frequency report
Courant report
Workspace storage report
Bug audit report
```

---

# 15. Modelo de estado

## 15.1 RANS

Separar:

```text
execution_status
automatic_gate_status
review_status
allowed_uses
```

Ejemplo:

```json
{
  "execution_status": "COMPLETED",
  "automatic_gate_status": "NOT_CONVERGED",
  "review_status": "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
  "allowed_uses": {
    "rans_spatial_convergence": true,
    "urans_initialization": true
  }
}
```

## 15.2 URANS

```json
{
  "execution_status": "TIMEOUT_PARTIAL",
  "pilot_status": "PILOT_PASS",
  "automatic_assessment": "MORE_PHYSICAL_TIME_REQUIRED",
  "review_status": "NOT_REVIEWED",
  "allowed_uses": {
    "space_time_convergence": false,
    "frequency_analysis": false
  }
}
```

## 15.3 Postprocess

```text
NOT_REQUESTED
RUNNING
PARTIAL
COMPLETED
FAILED
```

## 15.4 Batch

```text
READY
RUNNING
STOP_REQUESTED
STOPPED
COMPLETED
COMPLETED_WITH_FAILURES
FAILED_ENVIRONMENT
```

---

# 16. Correcciones de bugs obligatorias

# 16.1 Recuperar `closed_coarse`

Problema:

- desapareció de la lista de ejecuciones RANS;
- existe historia real de 20,000;
- debe considerarse finalizada para la cola.

Acciones:

1. localizar run y archivos reales;
2. verificar mesh/physics identity;
3. restaurar row en registry;
4. no relanzar;
5. preservar gate automático;
6. registrar aceptación explícita del usuario;
7. crear/verificar checkpoint;
8. excluir de la cola;
9. permitir RANS convergence y URANS initialization;
10. desbloquear PIMPLE tras pilot.

Si los datos reales no se encuentran:

```text
CLOSED_COARSE_HISTORY_MISSING
```

No fabricar.

# 16.2 Ralentización `closed_medium` cerca de iteración 7200

Auditar el log real, no especular.

Extraer por iteración:

- ClockTime;
- ExecutionTime;
- p solver iterations;
- U solver iterations;
- nuTilda iterations;
- GAMG cycles;
- residuals;
- continuity;
- force coefficients;
- write events.

Auditar sistema:

- PIDs `foamRun`;
- `mpirun`;
- workers;
- single-flight lease;
- CPU;
- RAM;
- swap;
- I/O;
- storage;
- process duplication.

Correlacionar la ralentización con:

- writeInterval;
- purge;
- reconstruction;
- monitor refresh;
- postprocessing accidental;
- solver linear iteration growth;
- residual changes;
- duplicate run script;
- duplicate job.

Generar:

```text
CFD_2D/reports/VALIDATION_LAB_CLOSED_MEDIUM_SLOWDOWN_AUDIT_<date>.md
```

No cambiar numerics antes de identificar la causa.

Si la causa es:

### I/O

- reducir field writes;
- no tocar scalar histories;
- no reconstruct durante run.

### Solver lineal

- reportar;
- comparar residuals;
- revisar configuración aplicada;
- no cambiar physics silenciosamente.

### Duplicación

- corregir single-flight;
- terminar duplicado;
- preservar principal.

### Monitor

- impedir parsing completo;
- no renderizar cada 2 s.

# 16.3 Timeout por malla/caso

Verificar que al timeout:

- `writeNow`;
- proceso termina;
- lock se libera;
- status parcial;
- queue avanza.

Añadir test real acotado o fixture de timeout.

# 16.4 Postproceso RANS generado pero no visible

Auditar:

- ruta backend;
- manifest;
- registry;
- UI filter;
- run identity;
- cache;
- permissions.

Unificar mediante `postprocess_manifest.json`.

La UI debe mostrar:

- productos;
- errors;
- timestamps;
- open folder.

# 16.5 Escala U incorrecta

Eliminar límites hardcodeados o heredados.

Usar min/max reales del field seleccionado, con opciones robust/manual.

Añadir test con field cuyo rango no coincide con defaults.

# 16.6 Courant RANS

Eliminar UI y generación automática de:

- Courant;
- hotspots;
- Co screenshots.

No borrar datos históricos.

# 16.7 ParaView `closed_coarse`

Auditar:

- path;
- `.foam`;
- reconstructed final iteration;
- arrays;
- internalMesh;
- time selection;
- WSLg/display;
- process registry.

Probar apertura real o `pvbatch` headless.

Generar readiness:

```json
{
  "case": "",
  "time": "",
  "internal_mesh": true,
  "arrays": [],
  "screenshot": "",
  "pvsm": "",
  "status": "READY"
}
```



---

# 17. Configuración y migración

## 17.1 Mantener schema general

No modificar el significado del schema 13 general.

Extender solo:

```text
validation_study
```

y migrar schema v5 del laboratorio a una nueva revisión compatible si es necesario.

## 17.2 Configuración objetivo

Ejemplo:

```json
{
  "validation_study": {
    "study_id": "closed_open_M0p15_Re1p9e6_alpha8",

    "rans": {
      "initial_iterations": 10000,
      "extension_iterations": 2500,
      "maximum_iterations": 20000,
      "per_mesh_timeout_s": null,
      "continue_after_timeout": true,
      "continue_after_nonfatal_failure": true,
      "simple_non_orthogonal_correctors": 0
    },

    "urans": {
      "time_step_policy": "fixed",
      "production_scheme": "backward",
      "pimple": {
        "nOuterCorrectors": 3,
        "nCorrectors": 2,
        "nNonOrthogonalCorrectors": 1
      },

      "startup_stages": [
        {"name": "A", "scheme": "Euler", "dt_factor": 0.25, "steps": 25},
        {"name": "B", "scheme": "Euler", "dt_factor": 0.50, "steps": 25},
        {"name": "C", "scheme": "Euler", "dt_factor": 1.00, "steps": 50}
      ],

      "pilot_stages": [
        {"name": "A", "scheme": "Euler", "dt_factor": 0.25, "steps": 10},
        {"name": "B", "scheme": "Euler", "dt_factor": 0.50, "steps": 10},
        {"name": "C", "scheme": "Euler", "dt_factor": 1.00, "steps": 20},
        {"name": "D", "scheme": "backward", "dt_factor": 1.00, "steps": 30}
      ],

      "settling_time_star": null,
      "sampling_time_star": null,
      "field_write_interval_s": null,
      "purge_write": null
    }
  }
}
```

No hardcodear valores que ya existen como settings reales; migrarlos.

## 17.3 Configuración congelada

Cada batch/run escribe:

```text
resolved_config.json
applied_configuration_audit.json
```

Después de generar OpenFOAM:

- parsear `controlDict`;
- parsear `fvSchemes`;
- parsear `fvSolution`;
- comparar;
- bloquear mismatch.

---

# 18. Backend y módulos

Antes de crear módulos nuevos, inspeccionar y reutilizar:

```text
CFD_2D/app/ramair_cfd2d_app.py
CFD_2D/app/workflow_backend.py
CFD_2D/scripts/ramair_2d_validation_study.py
CFD_2D/scripts/ramair_2d_openfoam_case_writer.py
CFD_2D/scripts/ramair_2d_openfoam_runner.py
CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py
CFD_2D/scripts/ramair_2d_postprocess.py
CFD_2D/scripts/ramair_2d_courant_diagnostics.py
CFD_2D/scripts/ramair_2d_timestep_advisor.py
Results helpers
ParaView launcher
monitor parsers
```

La UI no contiene lógica CFD.

Módulos nuevos solo si la responsabilidad no existe:

```text
ramair_2d_rans_batch_manager.py
ramair_2d_urans_matrix_manager.py
ramair_2d_urans_review.py
ramair_2d_space_time_convergence.py
ramair_2d_postprocess_registry.py
ramair_2d_closed_medium_slowdown_audit.py
```

Si cambia el contrato:

```text
incrementar BACKEND_API_VERSION y EXPECTED_BACKEND_API_VERSION juntos
```

---

# 19. Archivos por ejecución

## 19.1 RANS

```text
run_manifest.json
resolved_config.json
applied_configuration_audit.json
execution_segments.json
case_summary.json
residuals.csv
force_coeffs.csv
continuity.csv
window_statistics.csv
gate.json
review.json
checkpoint_manifest.json
storage_inventory.json
postprocess_manifest.json
```

## 19.2 URANS

```text
run_manifest.json
resolved_config.json
applied_configuration_audit.json
pilot_report.json
stage_history.json
time_history.csv
residuals.csv
force_coeffs.csv
continuity.csv
courant.csv
probes.csv
stationarity.json
psd_*.csv
dominant_modes.json
review.json
storage_inventory.json
postprocess_manifest.json
```

No crear placeholder vacío.

---

# 20. Tests obligatorios

## 20.1 Workspace y migración

- old schema v5 loads;
- data preserved;
- Results untouched;
- six meshes;
- closed_coarse restored;
- incompatible rows explicit.

## 20.2 Navigation

- six top-level sections;
- RANS/URANS subsections;
- global monitor expander everywhere;
- no duplicate frequency/Courant top-level;
- persistence.

## 20.3 RANS

- individual run;
- batch;
- 10k/2.5k/20k;
- timeout advances;
- resume;
- plateau;
- review;
- postprocess partial;
- no Co product.

## 20.4 closed_coarse

- real 20k detected;
- automatic gate preserved;
- explicit user review recorded;
- queue skips;
- checkpoint verified;
- no rerun.

## 20.5 closed_medium

- log parser;
- timing profile;
- duplicate PID detection;
- no UI relaunch;
- slowdown report;
- no numerics change without evidence.

## 20.6 URANS startup

- stages editable;
- default 25/25/50;
- BDF history;
- production excludes startup;
- pilot 70 steps;
- cost estimate no storage.

## 20.7 Matrix

- unique cases;
- resume;
- restart archives;
- pilot policy;
- timeout;
- divergence;
- continue.

## 20.8 URANS review

- oscillatory synthetic signal;
- mean/RMS/amplitude/period;
- PSD;
- St;
- cycles;
- extension recommendation;
- partial data.

## 20.9 PIMPLE

- same checkpoint;
- same dt;
- 2/3/4 only difference;
- no chaining;
- cost and accuracy outputs.

## 20.10 Postprocess

- RANS final only;
- URANS retained times;
- U/Cp scales;
- animation global scale;
- manifest;
- UI rendering;
- no empty graphs.

## 20.11 ParaView

- absolute script;
- internalMesh;
- latest RANS iteration;
- arrays;
- screenshot/state/readiness;
- closed_coarse.

## 20.12 Space-time

- accepted cases only;
- equal durations;
- temporal comparison;
- spatial comparison;
- frequency;
- Courant;
- GCI guards;
- nonmonotonic data.

## 20.13 Standard commands

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

OpenFOAM real tests opt-in.

---

# 21. Plan de implementación

## Fase 1 — Auditoría

- mapear UI;
- mapear backend;
- mapear workspace;
- mapear Results;
- mapear current runs;
- mapear postprocess;
- mapear ParaView;
- localizar closed_coarse;
- localizar closed_medium logs.

Entregar primero un plan de cambios y archivos.

## Fase 2 — Modelo de datos

- registries;
- states;
- migrations;
- closed_coarse restore;
- tests.

## Fase 3 — Navegación y monitor global

- six sections;
- subsections;
- global expander;
- no live monitor inside reviews.

## Fase 4 — Solver settings

- RANS;
- URANS;
- startup editor;
- pilot;
- budget.

## Fase 5 — RANS

- queue;
- timeout;
- review;
- postprocess;
- spatial convergence.

## Fase 6 — URANS

- single;
- matrix;
- pilot;
- resume;
- review;
- postprocess.

## Fase 7 — PIMPLE

- restore checkpoint;
- clone 2/3/4;
- prepare;
- compare.

## Fase 8 — Space-time

- matrices;
- PSD;
- Courant;
- reports.

## Fase 9 — Bugs

- slowdown audit;
- postprocess visibility;
- U scale;
- RANS Co removal;
- ParaView.

## Fase 10 — Verification

- unit tests;
- integration;
- UI visual;
- bounded real checks only when authorized;
- docs.

---

# 22. Entrega de Codex

Entregar:

1. auditoría del estado encontrado;
2. arquitectura final;
3. archivos modificados;
4. migraciones;
5. tests;
6. screenshots/descripción verificable;
7. closed_coarse restore evidence;
8. closed_medium slowdown report;
9. postprocess paths;
10. ParaView readiness;
11. PIMPLE preparation status;
12. unresolved risks;
13. exact commands;
14. confirmation of real runs executed or not executed.

No afirmar que se ejecutó una campaña si no se ejecutó.

---

# 23. Criterios de finalización

No declarar completo hasta que:

1. workspace esté ordenado y migrado;
2. navegación siga la nueva estructura;
3. monitor global funcione;
4. RANS queue funcione;
5. timeout avance;
6. review RANS funcione;
7. postprocess RANS aparezca;
8. no haya Co RANS;
9. ParaView closed_coarse funcione;
10. RANS spatial compare tres mallas;
11. URANS single/matrix/pilot funcionen;
12. startup sea editable y corto;
13. URANS review trate oscilaciones;
14. PIMPLE 2/3/4 esté preparado;
15. space-time integre frecuencias/Co;
16. closed_coarse esté restaurado sin falsificar gate;
17. slowdown medium esté auditado;
18. tests pasen;
19. Results permanezca intacto;
20. documentación esté actualizada.

---

# 24. Restricciones finales

- No regenerar mallas.
- No borrar Results.
- No inventar datos.
- No aprobar automáticamente sin regla o instrucción explícita.
- No mezclar RANS y URANS.
- No usar Courant en SIMPLE.
- No usar startup para estadísticas.
- No encadenar PIMPLE variants.
- No asumir coarse como pilot más restrictivo.
- No hacer FFT con tiempo no uniforme sin remuestreo.
- No comparar duraciones físicas diferentes.
- No generar VTK masivo.
- No abrir ParaView durante una cola.
- No cambiar physics sin changelog.
- No ejecutar CATIA.
- No implementar 3D, FEM o FSI.
