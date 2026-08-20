# INPUT ÚNICO PARA CODEX  
## Implementación de un entorno independiente de validación y convergencia espacial-temporal para perfiles LS(1)-0417 cerrado y Ram-Air abierto

Actúa como **ingeniero senior de CFD, aerodinámica transitoria, OpenFOAM, Gmsh, Python y Streamlit**. Debes continuar el desarrollo de la aplicación existente, no crear una aplicación paralela ni reescribir algoritmos que ya funcionan.

Este documento es el contrato completo de implementación. Antes de modificar código, inspecciona el repositorio real y adapta nombres, imports y rutas a la arquitectura existente. No inventes clases o módulos sin comprobar primero si ya existe una implementación equivalente.

---

# 0. Documentos que debes leer antes de editar

Lee, en este orden:

1. `PROJECT_CONTEXT_FOR_CODEX.md`
2. `README_PROJECT_STRUCTURE.md`
3. `AGENTS.md`
4. `CHANGELOG.md`
5. `CFD_2D/reports/TRANSIENT_TIMESTEP_MESH_SOLVER_STUDY_20260728.md`
6. `Documents and Manuals/CFD 2D/CFD_2D_TECHNICAL_SPECIFICATIONS.txt`
7. `Documents and Manuals/CFD 2D/Research Papers/Cummings_2008_Accurate_Time_Dependent_CFD_Timestep_Grid_Guidelines.pdf`
8. `Documents and Manuals/OpenFOAM/OpenFOAMUserGuide-A4.pdf`
9. Los scripts actuales de UI, backend, writer, runner y postproceso.
10. Los manifiestos reales del caso:
   `Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6`

Usa como contexto científico adicional la metodología ya definida en los documentos previos sobre selección de `deltaT`, esquemas `Euler`, `backward`, `CrankNicolson 0.9`, análisis PSD, estudio malla-tiempo y diagnóstico de Courant.

---

# 1. Objetivo general

Implementa una nueva sección de la aplicación denominada provisionalmente:

```text
Validation & Convergence Lab
```

o, en español:

```text
Laboratorio de validación y convergencia
```

Debe ser un **workspace independiente del flujo general**, especializado en estudiar conjuntamente:

- topología cerrada LS(1)-0417;
- topología abierta Ram-Air;
- tres niveles de malla por topología;
- convergencia espacial RANS;
- convergencia conjunta malla-tiempo URANS;
- sensibilidad al número de correctores externos PIMPLE;
- contenido frecuencial;
- Courant local y global;
- coste computacional;
- aceptación o rechazo de cada combinación malla/`deltaT`.

Este entorno no está pensado para construir una polar completa. Su objetivo es producir un estudio reproducible del tipo propuesto por Cummings et al., donde la resolución espacial, el paso temporal, la convergencia interna y el contenido frecuencial se analizan conjuntamente.

No modifiques el comportamiento del resto de casos salvo los cambios de UI expresamente requeridos en este documento.

---

# 2. Decisión de ángulo de ataque

## 2.1 Utilizar un único ángulo en la primera campaña

La primera implementación debe utilizar:

\[
\boxed{\alpha = 8^\circ}
\]

para ambas topologías.

## 2.2 Justificación física y de proyecto

La elección de \(8^\circ\) es deliberada:

1. El proyecto ya contiene un estudio protegido de refinamiento para el perfil cerrado a
   `alpha=8 deg`, con tres mallas reales y `checkMesh` aprobado.
2. Permite reutilizar evidencia, configuraciones y nomenclatura existentes sin mezclar la nueva campaña con una condición inédita.
3. Es suficientemente alto para generar carga aerodinámica, gradiente adverso de presión, sensibilidad de estela y posibles oscilaciones del trailing edge.
4. En la geometría abierta se encuentra cerca del rango donde la literatura Ram-Air muestra recirculación de entrada, capas de cizalla de los labios y estructuras vorticales claramente no estacionarias.
5. Evita usar \(\alpha=0^\circ\), que podría no excitar adecuadamente los fenómenos de entrada y separación.
6. Evita comenzar en un ángulo de stall profundo, donde la comparación quedaría dominada por limitaciones de URANS, modelo turbulento y separación masiva, dificultando aislar los errores espaciales y temporales.

Esta elección no implica que \(8^\circ\) represente toda la envolvente aerodinámica. Tras validar la metodología podrán añadirse otros ángulos, pero no forman parte de esta primera implementación.

La UI debe mostrar claramente:

```text
Study angle: 8 deg
Reason: common closed/open convergence condition
This is not a polar validation.
```

No permitir un barrido de ángulos en la primera versión del laboratorio. Puede existir un control avanzado deshabilitado o una futura extensión documentada, pero no un selector activo que fragmente la matriz de estudio.

---

# 3. Condiciones físicas comunes

Los seis casos deben utilizar:

\[
M_\infty = 0.15
\]

\[
c = 1.0\ \mathrm{m}
\]

\[
Re_c = 1.9\times10^6
\]

\[
\alpha = 8^\circ
\]

Modelo turbulento inicial:

```text
Spalart-Allmaras
```

Solver físico:

```text
incompressible URANS
```

OpenFOAM Foundation 14 es la referencia activa. Detecta versión y distribución con el helper existente; no hardcodees una sintaxis incompatible con OpenFOAM 13.

## 3.1 Coherencia termodinámica

La aplicación debe almacenar explícitamente:

```text
Mach
speed of sound
temperature
U_inf
rho
mu
nu
Re
chord
alpha
```

La baseline trazable del proyecto utiliza aproximadamente:

```text
T = 288.15 K
U_inf = 51.04384 m/s
rho = 0.6660666 kg/m3
mu = 1.7894e-5 Pa s
nu = mu/rho
p = 55.093 kPa
```

Esto es una condición de similitud para igualar Mach y Reynolds, no densidad ISA de nivel del mar. Preserva esta distinción en la UI, los metadatos y los informes.

## 3.2 Escalas temporales

Calcular siempre:

```python
tc = chord / U_inf
time_star = physical_time * U_inf / chord
dt_star = dt * U_inf / chord
St = frequency * chord / U_inf
samples_per_cycle = 1.0 / (St * dt_star)
```

Para la baseline:

```text
U_inf ≈ 51.04384 m/s
tc ≈ 0.0195910 s
```

No hardcodees esos resultados; deben derivarse de los valores efectivos.

---

# 4. Seis mallas de referencia

Usa el caso canónico ya existente:

```text
Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6
```

Contiene seis tripletas coherentes que restauran conjuntamente geometría, caso CFD y malla:

```text
closed_coarse
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

Conteos reales de celdas:

| Topología | Coarse | Medium | Fine |
|---|---:|---:|---:|
| Cerrada | 203,691 | 333,826 | 618,382 |
| Abierta | 269,864 | 302,692 | 420,728 |

Todos contienen `polyMesh` real y `checkMesh=OK`, pero eso no demuestra independencia de malla.

## 4.1 Restauración

La nueva sección debe permitir seleccionar una de las seis variantes y restaurar de forma atómica:

- Geometry Package;
- CFD Case;
- Mesh Package;
- solver configuration local del estudio, sin sobrescribir silenciosamente paquetes guardados.

La restauración debe ocurrir dentro del workspace del laboratorio, no sobre el workspace genérico salvo que el usuario elija explícitamente exportar o publicar.

## 4.2 Advertencia sobre los ratios de refinamiento

Para una malla 2D se puede estimar:

\[
h_{\mathrm{eff}}\propto N_{\mathrm{cells}}^{-1/2}
\]

Ratios efectivos aproximados:

```text
closed coarse->medium: 1.280
closed medium->fine:   1.361

open coarse->medium:   1.059
open medium->fine:     1.179
```

La secuencia abierta, especialmente coarse-medium, tiene un ratio pequeño. Por ello:

- no presentar automáticamente GCI como concluyente;
- calcular GCI/Richardson solo cuando los datos y ratios permitan una evaluación razonable;
- mostrar una advertencia `NON_ASYMPTOTIC_OR_WEAK_REFINEMENT_RATIO`;
- no inventar orden observado si la solución no es monotónica o el sistema es mal condicionado;
- ofrecer también análisis directo de diferencias relativas entre niveles.

---

# 5. Workspace independiente

## 5.1 Ruta activa propuesta

Crear un entorno activo dedicado bajo una ruta coherente con la estructura real, por ejemplo:

```text
CFD_2D/validation_studies/
  closed_open_M0p15_Re1p9e6_alpha8/
```

Antes de fijar la ruta, inspecciona si ya existe un patrón equivalente. Reutilízalo si existe.

Estructura lógica:

```text
closed_open_M0p15_Re1p9e6_alpha8/
  study_config.json
  study_manifest.json
  mesh_registry.json
  run_matrix.json
  active_selection.json
  logs/
  checkpoints/
  runs/
    closed/
      coarse/
      medium/
      fine/
    open/
      coarse/
      medium/
      fine/
  postprocess/
    per_run/
    spatial_rans/
    spatial_temporal_urans/
    frequency/
    courant/
    pimple/
    reports/
  exports/
```

## 5.2 Separación respecto al workspace general

El laboratorio debe:

- tener estado propio;
- no depender de `active_workspace.json` del flujo general para reconstruir su matriz;
- no sincronizar configuraciones hacia un caso previamente cargado por accidente;
- no sobrescribir `CFD_2D/openfoam_cases` de otro caso;
- no sustituir paquetes históricos;
- copiar o restaurar los datos necesarios a un área mutable local;
- guardar resultados aceptados como nuevos paquetes bajo el mismo caso de Results.

Añadir un estado específico, por ejemplo:

```text
CFD_2D/app_state/validation_convergence_workspace.json
```

Usar escritura atómica.

## 5.3 Biblioteca Results

Los resultados del laboratorio deben almacenarse en:

```text
Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6
```

Añadir, preferiblemente como etapa de Results compatible:

```text
Convergence Studies/
```

Si añadir una etapa nueva exige una migración grande o rompe el contrato actual, usar:

```text
Postprocess Packages/Convergence_Study_<study_id>/
```

y un `study_manifest.json` en el paquete. No crear una segunda biblioteca Results.

No modificar simulaciones históricas. Cada ejecución debe añadirse con identidad inmutable.

---

# 6. Identidad y nomenclatura de cada ejecución

Generar IDs deterministas:

```text
<topology>_<mesh_level>_a08_dt<scientific>_pimple<outer>_<scheme>
```

Ejemplos:

```text
closed_medium_a08_dt1p0em4_pimple3_backward
open_fine_a08_dt2p5em5_pimple3_backward
```

Cada ejecución debe almacenar:

```json
{
  "study_id": "",
  "run_id": "",
  "topology": "closed|open",
  "mesh_level": "coarse|medium|fine",
  "mesh_package": "",
  "mesh_hash": "",
  "cell_count": 0,
  "alpha_deg": 8.0,
  "mach": 0.15,
  "reynolds": 1900000,
  "chord_m": 1.0,
  "U_inf_m_s": 0.0,
  "tc_s": 0.0,
  "time_scheme": "backward",
  "dt_s": 0.0,
  "dt_star": 0.0,
  "nOuterCorrectors": 3,
  "nCorrectors": 2,
  "nNonOrthogonalCorrectors": 0,
  "physical_duration_s": 0.0,
  "physical_duration_star": 0.0,
  "sampling_duration_s": 0.0,
  "steps_planned": 0,
  "steps_completed": 0,
  "status": "",
  "acceptance": ""
}
```

No identificar un caso solo por la carpeta; usar manifiesto y hashes.

---

# 7. Metodología temporal

## 7.1 Principio principal

No utilizar `Co=1` como controlador automático del paso temporal en esta campaña:

```foam
adjustTimeStep  no;
deltaT          <fixed_value>;
```

El Courant se calcula, localiza y analiza, pero no cambia automáticamente el `deltaT`.

Esto no significa ignorarlo. Un paso fijo solo se acepta si:

- la solución permanece acotada;
- PIMPLE converge razonablemente en cada paso;
- no hay deriva de continuidad;
- las fuerzas y sondas no muestran comportamiento numérico patológico;
- el caso coincide con el siguiente `deltaT` más fino;
- los hotspots de Co quedan localizados y explicados.

## 7.2 Esquemas temporales

### Inicialización

```foam
ddtSchemes
{
    default Euler;
}
```

### Producción

```foam
ddtSchemes
{
    default backward;
}
```

### Sensibilidad de damping

```foam
ddtSchemes
{
    default CrankNicolson 0.9;
}
```

`backward` es el esquema base. `CrankNicolson 0.9` solo se usa en un estudio de sensibilidad específico, no en toda la matriz.

No copiar coeficientes de damping de Cobalt. No usar `localEuler` para tiempo físico. No usar under-relaxation fuerte como sustituto de una integración temporal estable.

## 7.3 Inicio escalonado

Sin `adjustTimeStep`, implementar una rampa explícita:

```text
Stage A: Euler, 0.25 * dt_target, 1 tc
Stage B: Euler, 0.50 * dt_target, 1 tc
Stage C: Euler, 1.00 * dt_target, 2 tc
Stage D: backward, dt_target, settling
Stage E: backward, dt_target, sampling
```

Antes de cambiar a `backward`, deben existir al menos dos niveles temporales consistentes con el `dt_target`.

Los datos A-D no se mezclan con el muestreo final.

## 7.4 Checkpoint común

Para comparar `deltaT`:

- generar una inicialización SIMPLE por combinación topología/malla;
- aplicar la gate actual de convergencia;
- preservar `U`, `p`, `nuTilda`, `phi` y demás campos disponibles según el contrato existente;
- crear un checkpoint común por topología y malla;
- iniciar todos los `deltaT` de una misma malla desde ese checkpoint.

No iniciar cada `deltaT` desde un campo distinto.

---

# 8. Familia de `deltaT`

## 8.1 Valores de referencia del paper

La baseline del proyecto utiliza:

```text
dt = 2.5e-4 s
dt* ≈ 0.01276096
25,000 pasos
tiempo total = 6.25 s
ventana final = 10,000 pasos = 2.5 s
```

Esto equivale aproximadamente a:

```text
total: 319.03 tc
sampling: 127.61 tc
```

Generar la tabla completa:

| `dt` [s] | `dt*` aprox. | pasos en 6.25 s | pasos en 2.5 s | muestras/ciclo a St=1.67 | muestras/ciclo a St=20 |
|---:|---:|---:|---:|---:|---:|
| 2.5000e-4 | 1.2761e-2 | 25,000 | 10,000 | 46.9 | 3.9 |
| 1.2500e-4 | 6.3805e-3 | 50,000 | 20,000 | 93.8 | 7.8 |
| 6.2500e-5 | 3.1903e-3 | 100,000 | 40,000 | 187.7 | 15.7 |
| 3.1250e-5 | 1.5951e-3 | 200,000 | 80,000 | 375.4 | 31.3 |
| 1.5625e-5 | 7.9756e-4 | 400,000 | 160,000 | 750.8 | 62.7 |
| 7.8125e-6 | 3.9878e-4 | 800,000 | 320,000 | 1501.6 | 125.4 |

La tabla debe recalcularse con el `U_inf` real.

## 8.2 No asumir que esos pasos son ejecutables

El contexto real del proyecto demuestra que:

- la malla cerrada puede quedar limitada por una celda cerca del TE inferior;
- la malla abierta ha mostrado Courant extremadamente alto para pasos de escala de referencia;
- un `deltaT` fijo de referencia puede divergir varios órdenes de magnitud antes de que el coste físico sea aceptable.

Por tanto, separar:

```text
SCIENTIFIC_TARGET_DT
FEASIBLE_FIXED_DT
ACCEPTED_DT
```

No marcar un `SCIENTIFIC_TARGET_DT` como ejecutable o válido sin pilot run.

## 8.3 Pilot fixed-dt ladder

Antes de una ejecución larga, lanzar una prueba corta y explícita:

```text
100-500 pasos
adjustTimeStep no
mismo esquema y PIMPLE de producción
sin publicación
```

El usuario debe poder elegir una semilla:

- último `deltaT` real estable de la malla;
- valor del advisor existente;
- valor manual;
- valor de referencia del paper.

Construir una escalera configurable:

```text
0.5x, 1x, 2x, 4x seed_dt
```

o una razón geométrica elegida por el usuario.

Cada pilot debe terminar como:

```text
PILOT_PASS
PILOT_WARN
PILOT_DIVERGED
PILOT_SETUP_FAILED
PILOT_TIMEOUT_PARTIAL
```

No usar el pilot para declarar independencia temporal.

## 8.4 Matriz recomendada

La UI debe admitir cualquier selección, pero ofrecer dos presets.

### Preset A: paper-reference

```text
closed/open:
  2.5e-4
  1.25e-4
  6.25e-5
  3.125e-5
  1.5625e-5
```

Solo se habilitan para producción si pasan el pilot.

### Preset B: feasible-halving

A partir de un `dt_anchor` estable:

```text
2 * dt_anchor
1 * dt_anchor
0.5 * dt_anchor
0.25 * dt_anchor
```

El mayor puede fallar; los tres pasos estables consecutivos forman la familia temporal.

## 8.5 Matriz económica de malla-tiempo

Cuando exista una familia estable:

```text
coarse:
  dt_coarse
  dt_coarse / 2

medium:
  dt_coarse / 2
  dt_coarse / 4

fine:
  dt_coarse / 4
  dt_coarse / 8
```

La aplicación debe permitir una matriz completa 3 mallas × 3 o más `dt`, pero no ejecutarla automáticamente.

---

# 9. Presupuesto temporal y computacional

Añadir una subsección obligatoria denominada:

```text
Temporal and Computational Budget
```

## 9.1 Entradas

- `dt`;
- tiempo total;
- settling;
- sampling;
- número de pasos;
- `writeInterval`;
- snapshots retenidos;
- ranks MPI;
- mediana medida de segundos por paso;
- tamaño medio de snapshot;
- espacio libre;
- límite de wall time.

## 9.2 Cálculos

```python
steps_total = ceil(total_physical_time / dt)
steps_sampling = ceil(sampling_time / dt)
estimated_wall_seconds = steps_total * measured_seconds_per_step
estimated_snapshot_count = floor(total_physical_time / field_write_interval)
estimated_storage = estimated_snapshot_count * measured_snapshot_size
```

Mostrar:

- horas/días estimados;
- pasos de settling;
- pasos de sampling;
- tiempos convectivos;
- frecuencia de Nyquist;
- resolución espectral;
- muestras por ciclo para `St=0.05, 0.2, 1, 2, 8, 10, 20`;
- coste por malla;
- coste total de la selección.

## 9.3 Benchmark inicial

Puede usarse como estimación inicial, claramente etiquetada como host-specific:

```text
closed medium, 8 MPI ranks: ~2.48 s por primer paso medido
```

Sustituirlo por la mediana real de los últimos pasos del pilot.

## 9.4 Protección

Antes de ejecutar:

- confirmar explícitamente el número de casos;
- mostrar tiempo y almacenamiento estimados;
- impedir ejecución accidental de toda la matriz;
- permitir `dry-run`;
- permitir ejecutar solo casos seleccionados;
- no iniciar solver desde tests.

---

# 10. Secciones de la nueva UI

Crear una página independiente con estas subsecciones.

## 10.1 Overview

Mostrar:

- propósito;
- condición \(\alpha=8^\circ\);
- Mach, Re, cuerda;
- workspace Results;
- estado global;
- número de runs planificados/completados/aceptados;
- advertencia de que `checkMesh PASS` no equivale a convergencia.

## 10.2 Mesh Registry

Tabla de seis mallas:

- topology;
- level;
- package;
- cells;
- checkMesh;
- grade;
- max non-orthogonality;
- max skewness;
- min determinant;
- effective `h`;
- ratios de refinamiento;
- active/inactive;
- restore button.

## 10.3 Operating Condition

Valores bloqueados por defecto:

```text
M=0.15
Re=1.9e6
c=1m
alpha=8deg
```

Permitir ver propiedades derivadas, pero no alterar silenciosamente la condición.

## 10.4 Solver & Temporal Strategy

Controles:

- SIMPLE initialization profile;
- `Euler` startup;
- `backward` production;
- sensibilidad `CrankNicolson 0.9`;
- fixed `deltaT`;
- rampa 0.25/0.5/1.0;
- PIMPLE outer correctors;
- duration;
- sampling;
- write controls;
- ranks;
- timeout;
- stop/writeNow;
- resume.

No mostrar `maxCo` como controlador cuando la política es fixed.

## 10.5 Run Matrix

Matriz interactiva:

```text
rows = mesh variants
columns = dt values
```

Cada celda muestra:

- not configured;
- ready;
- pilot pass;
- running;
- partial;
- completed;
- accepted;
- rejected;
- invalid;
- archived.

Permitir seleccionar celdas y ejecutar secuencialmente.

## 10.6 Per-run Analysis

Tras cada run:

- estado;
- métricas;
- señales;
- Courant;
- residuos;
- PIMPLE;
- PSD;
- aceptación;
- comentarios;
- botón para guardar/publicar paquete.

## 10.7 Spatial RANS Convergence

Separar `closed` y `open`.

Analizar las tres mallas con SIMPLE/RANS:

- \(C_L,C_D,C_M\);
- \(L/D\);
- \(C_p(x/c)\);
- \(C_f(x/c)\);
- y+;
- wall shear;
- separación/reattachment;
- residuales;
- continuidad;
- estadísticas de ventanas finales;
- tiempo de CPU.

Para el abierto, si SIMPLE no cumple la gate, mostrar:

```text
STEADY_RANS_NOT_ESTABLISHED
```

No forzar una falsa convergencia espacial RANS. La convergencia espacial física del abierto podrá basarse en medias URANS cuando proceda, pero debe etiquetarse como tal.

## 10.8 Combined Spatial-Temporal URANS Convergence

Separar topologías y luego ofrecer comparación conjunta.

Mostrar:

- métrica vs `dt` para cada malla;
- métrica vs número de celdas para cada `dt`;
- mapa/heatmap `cells × dt`;
- diferencias respecto al caso más fino;
- convergencia de media;
- convergencia RMS;
- convergencia frecuencial;
- coste vs error;
- matriz PASS/WARN/FAIL.

## 10.9 Frequency Analysis

- Welch PSD;
- señales globales y locales;
- St dominante;
- modos secundarios;
- amplitud;
- espectrograma;
- coherencia;
- convergencia de frecuencia con `dt`;
- convergencia de frecuencia con malla;
- número de ciclos muestreados;
- Nyquist y resolución espectral.

## 10.10 Courant Analysis

- `Co_max`;
- media;
- percentiles;
- fracción `Co>1`, `Co>2`;
- mapa;
- hotspots;
- región;
- calidad de celdas;
- relación con divergencia;
- comparación entre `dt`;
- comparación entre mallas.

No rechazar automáticamente por `Co_max>1`. Sí rechazar si la solución es no acotada o si el refinamiento temporal modifica el resultado fuera de tolerancia.

## 10.11 PIMPLE Outer-Corrector Study

Por defecto:

```text
nOuterCorrectors = 3
```

En una única combinación seleccionada por topología, inicialmente:

```text
medium mesh
accepted provisional dt
```

comparar:

```text
1, 2, 3, 4 outer correctors
```

El contexto histórico del caso cerrado usó un outer corrector; la nueva baseline del laboratorio usa 3 para robustez del paso fijo. No confundir estos correctores con subiteraciones Newton de Cobalt.

Analizar:

- residuales por paso;
- residual reduction;
- continuidad;
- fuerzas medias/RMS;
- PSD;
- coste por paso;
- frecuencia dominante.

Seleccionar el menor número que sea equivalente al siguiente más alto. Mantener 3 como valor inicial estándar hasta completar el estudio.

## 10.12 Reports & Export

Generar:

- informe Markdown;
- JSON machine-readable;
- CSV;
- PNG;
- SVG;
- manifiesto;
- lista de casos aceptados;
- matriz completa;
- referencias y provenance.

No publicar automáticamente como validación aerodinámica.

---

# 11. Cambios en la zona general de la app

## 11.1 Eliminar el desplegable genérico de convergencia de malla

Eliminar de la UI general el desplegable o selector actual de “mesh convergence analysis” que no pertenece a un workspace especializado.

No borrar backend ni datos históricos si siguen siendo necesarios para compatibilidad. La funcionalidad se traslada al laboratorio.

## 11.2 Mantener la postproducción general

La zona general conserva:

- postproceso de un caso individual;
- polar;
- visualización estándar;
- Results.

Solo el laboratorio recibe la interfaz especializada de convergencia.

## 11.3 No contaminar otros casos

Los nuevos widgets, estados y configuraciones deben activarse únicamente cuando se abre el laboratorio.

---

# 12. Cambios de configuración de solver

## 12.1 Extender schema sin romper schema 12

Inspecciona el schema actual y realiza migración versionada. No reutilices campos con significado distinto.

Añadir una configuración de estudio, no convertir silenciosamente el solver general:

```json
{
  "validation_study": {
    "enabled": true,
    "study_id": "",
    "alpha_deg": 8.0,
    "time_policy": "fixed_staged",
    "startup_scheme": "Euler",
    "production_scheme": "backward",
    "sensitivity_scheme": "CrankNicolson",
    "crank_nicolson_psi": 0.9,
    "dt_target_s": null,
    "startup_factors": [0.25, 0.5, 1.0],
    "startup_duration_tc": [1.0, 1.0, 2.0],
    "settling_tc": null,
    "sampling_tc": null,
    "nOuterCorrectors": 3,
    "courant_controls_dt": false
  }
}
```

## 12.2 Writer

El writer debe generar:

```foam
adjustTimeStep no;
deltaT <fixed>;
```

por etapa.

Debe registrar:

- esquema;
- `deltaT`;
- `dt*`;
- duración;
- PIMPLE;
- exact case config hash.

## 12.3 Staged runner

Extender el runner para:

- ejecutar stages A-E;
- escribir/reiniciar correctamente;
- conservar `phi`;
- reiniciar el tiempo físico según contrato;
- mantener separados SIMPLE, Euler startup, settling y sampling;
- reanudar;
- escribir `writeNow`;
- preservar parciales;
- reconstruir todos los tiempos;
- no usar `-latestTime` durante reconstrucción.

## 12.4 Temporal budget

Integrar todos los parámetros de presupuesto dentro de Solver & Temporal Strategy.

---

# 13. Métricas por run

Calcular y almacenar:

## 13.1 Aerodinámica

- mean/RMS/std/min/max de \(C_L,C_D,C_M\);
- \(L/D\);
- drift;
- block means;
- confidence intervals;
- autocorrelation;
- integral time scale.

## 13.2 Superficie

- mean/RMS \(C_p\);
- mean \(C_f\);
- separation/reattachment;
- y+ min/mean/max;
- wall shear;
- delta99 numerical;
- prism-stack comparison.

## 13.3 Frecuencia

- PSD;
- dominant peaks;
- peak amplitude;
- bandwidth;
- St;
- cycles sampled;
- Nyquist;
- frequency resolution;
- signal stationarity.

## 13.4 Courant

- max;
- mean;
- p95/p99/p99.9;
- fractions above thresholds;
- top 20 cells;
- region classification;
- geometric quality.

## 13.5 Solver

- initial/final residuals per step;
- residual by outer corrector;
- continuity;
- boundedness;
- CPU/step;
- CPU/physical second;
- MPI ranks;
- timeout/partial status.

## 13.6 Open profile

Además:

- pressure mean/RMS in cavity;
- inlet mass-flow mean/RMS;
- upper/lower lip signals;
- phase/coherence;
- recirculation indicators.

---

# 14. Sondas

## 14.1 Cerrado

- upper TE;
- lower TE;
- wake \(x/c=1.02\);
- wake \(x/c=1.10\);
- wake \(x/c=1.50\);
- pressure validation stations;
- separation/reattachment candidate.

## 14.2 Abierto

- exterior upper lip;
- upper shear layer;
- exterior lower lip;
- lower shear layer;
- inlet center;
- near cavity;
- deep cavity;
- upper surface after inlet;
- lower surface after inlet;
- near wake;
- medium wake.

Definir por coordenadas normalizadas y comprobar que el punto está dentro del fluido. Si no lo está, no desplazarlo silenciosamente: reportar y permitir corrección.

---

# 15. Gráficas requeridas

## 15.1 Por run

1. \(C_L(t)\)
2. \(C_D(t)\)
3. \(C_M(t)\)
4. \(L/D(t)\)
5. medias móviles
6. RMS móvil
7. medias por bloques
8. residuos
9. continuidad
10. `deltaT(t)`
11. `dt*(t)`
12. Co time history
13. Courant hotspot map
14. PSD de fuerzas
15. PSD de sondas
16. \(C_p\) medio/RMS
17. \(C_f\)
18. y+
19. vorticidad
20. velocidad
21. presión
22. streamlines
23. snapshots de fase

## 15.2 Convergencia espacial RANS

- metric vs effective `h`;
- metric vs cell count;
- differences coarse-medium-fine;
- GCI cuando sea válido;
- observed order con advertencias;
- Cp overlays;
- Cf overlays;
- y+ overlays;
- separation position;
- cost vs cells.

## 15.3 Convergencia conjunta URANS

Reproducir el espíritu de las figuras del paper:

- dominant wave number \(1/St\) vs `dt` en escala log;
- dominant St vs `dt*`;
- una serie por malla;
- mean coefficients vs `dt`;
- RMS vs `dt`;
- PSD peak amplitude vs `dt`;
- metrics vs cell count for fixed `dt`;
- heatmap `cell_count × dt`;
- acceptance matrix;
- cost/accuracy Pareto;
- error relative to finest accepted run.

## 15.4 Frecuencia

- PSD overlays por `dt`;
- PSD overlays por malla;
- spectrogram;
- local probe PSD;
- coherence;
- frequency convergence;
- number of cycles;
- Nyquist line.

## 15.5 Courant

- max/percentiles vs `dt`;
- Co vs cell count;
- fraction `Co>1/2`;
- spatial hotspots;
- hotspot quality table;
- Co vs divergence/acceptance.

## 15.6 PIMPLE

- mean/RMS vs outer correctors;
- dominant St vs outer correctors;
- residual reduction;
- CPU/step;
- equivalence/error relative to 4 correctors.

## 15.7 Gráfica completa de estudio

Generar una figura/resumen para cada topología con:

```text
x-axis: deltaT or deltaT*
series: coarse, medium, fine
panels:
  mean CL
  mean CD
  mean CM
  RMS CL
  dominant St
  PSD peak amplitude
```

Generar otra figura 2D:

```text
x-axis: cell count or h_eff
y-axis: deltaT*
color: relative error / acceptance metric
markers: PASS, WARN, FAIL
```

---

# 16. Análisis estadístico

Usar exclusivamente la ventana de sampling aceptada.

Welch:

```python
scipy.signal.welch(
    signal,
    window="hann",
    detrend="constant",
    noverlap=50_percent
)
```

Registrar:

- `nperseg`;
- `noverlap`;
- duration;
- `df`;
- Nyquist;
- normalization;
- number of segments.

Estacionariedad:

- 4 bloques;
- mean variation < 1%;
- RMS variation < 5%;
- dominant frequency variation < 2-3%.

Ventana mínima:

```text
at least 10 cycles of lowest relevant frequency
prefer 20 cycles
```

No aplicar FFT a startup, settling o tiempo no uniforme.

---

# 17. Aceptación de cada combinación malla/`dt`

Estados:

```text
NOT_CONFIGURED
READY
PILOT_PASS
PILOT_WARN
PILOT_FAIL
RUNNING
TIMEOUT_PARTIAL
COMPLETED
ANALYSIS_PENDING
ACCEPTED
ACCEPTED_WITH_WARNINGS
REJECTED_TEMPORAL
REJECTED_SPATIAL
REJECTED_SOLVER
REJECTED_MESH
NOT_STATISTICALLY_ESTABLISHED
```

Criterios iniciales configurables:

```text
mean CL difference: < 1%
mean CD difference: < 2%
mean CM difference: < 2%
RMS difference: < 5%
dominant frequency difference: < 2%
PSD peak amplitude difference: < 10%
Cp/Cf: no relevant topology change
separation/reattachment: consistent
PIMPLE: adequate internal convergence
variables: bounded
stationarity: passed
```

El caso se compara con el siguiente `dt` más fino en la misma malla y con la siguiente malla más fina en un `dt` común o equivalente.

No declarar aceptación por:

- `checkMesh`;
- estabilidad;
- residuos solamente;
- fuerza media solamente;
- Courant máximo aislado.

---

# 18. Convergencia espacial y GCI

Implementar:

- effective \(h=N^{-1/2}\);
- diferencias relativas;
- monotonicity check;
- oscillatory convergence detection;
- generalized Richardson for unequal ratios;
- GCI si es válido;
- uncertainty warning.

No producir un número GCI si:

- ratios demasiado próximos a 1;
- orden no resoluble;
- datos no monotónicos sin método apropiado;
- denominador mal condicionado;
- run no aceptado.

La UI debe explicar por qué GCI no está disponible, especialmente para la secuencia abierta coarse-medium.

---

# 19. Archivos de salida

Por run:

```text
case_metadata.json
case_summary.json
time_history.csv
force_coeffs.csv
residuals.csv
continuity.csv
courant.csv
courant_cells.csv
courant_regions.csv
probes.csv
surface_statistics.csv
stationarity.json
psd_<signal>.csv
dominant_modes.json
acceptance.json
```

Por estudio:

```text
study_manifest.json
mesh_registry.json
run_matrix.json
spatial_rans_comparison.csv
spatial_temporal_comparison.csv
frequency_comparison.csv
courant_comparison.csv
pimple_comparison.csv
acceptance_matrix.csv
study_report.md
study_report.json
```

No crear CSV vacíos para simular resultados.

---

# 20. Backend y módulos

Inspecciona primero los scripts existentes. Reutiliza:

- `workflow_backend.py`;
- `ramair_2d_openfoam_case_writer.py`;
- `ramair_2d_openfoam_runner.py`;
- `ramair_2d_openfoam_staged_runner.py`;
- `ramair_2d_postprocess.py`;
- `ramair_2d_courant_diagnostics.py`;
- `ramair_2d_timestep_advisor.py`;
- Results save/restore;
- parsers PyFoam/OpenFOAM.

Añade módulos solo cuando exista una responsabilidad nueva clara, por ejemplo:

```text
ramair_2d_validation_study.py
ramair_2d_study_registry.py
ramair_2d_run_matrix.py
ramair_2d_temporal_budget.py
ramair_2d_convergence_analysis.py
ramair_2d_frequency_analysis.py
ramair_2d_pimple_study.py
ramair_2d_validation_report.py
```

No dupliques parsing de fuerzas, residuals o Courant.

La UI no debe contener lógica CFD; llama al backend.

Incrementa `BACKEND_API_VERSION` y `EXPECTED_BACKEND_API_VERSION` conjuntamente si cambia el contrato.

---

# 21. Ejecución

## 21.1 Seguridad

- dry-run por defecto;
- solver solo con acción explícita;
- confirmación de presupuesto;
- ejecución secuencial por defecto;
- 8 ranks máximo;
- no oversubscription;
- stop -> `writeNow`;
- preserve partial fields;
- reconstruct all retained times;
- never use `-latestTime` for normal reconstruction;
- archive before overwrite.

## 21.2 No ejecutar automáticamente durante Codex

Codex puede:

- ejecutar tests;
- hacer dry-run;
- verificar generación de diccionarios;
- usar Gmsh/checkMesh bounded si es necesario.

Codex no debe lanzar la matriz CFD real salvo petición explícita posterior.

---

# 22. Migración y compatibilidad

1. Preservar schema 12 y migrar versionadamente.
2. Mantener lectura de paquetes antiguos.
3. No modificar Results históricos.
4. Ocultar el desplegable genérico de convergencia, no destruir datos.
5. Mantener postproceso general intacto.
6. Actualizar documentación.
7. Actualizar `CHANGELOG.md`.
8. Actualizar `PROJECT_CONTEXT_FOR_CODEX.md` solo con hechos implementados/verificados.
9. No marcar el laboratorio como validado sin runs reales.

---

# 23. Tests obligatorios

## 23.1 Unitarios

- cálculo `U_inf`, `nu`, `tc`, `dt*`;
- step budget;
- samples/cycle;
- fixed `adjustTimeStep no`;
- staged ramp;
- run ID;
- manifest atomicity;
- state isolation;
- mesh restore;
- Results save;
- GCI valid/invalid;
- non-monotonic convergence;
- Welch synthetic signals;
- stationarity blocks;
- acceptance logic;
- Courant classification;
- PIMPLE comparison.

## 23.2 Integración

- load six mesh triplets;
- ensure no generic workspace overwrite;
- write six dry-run cases;
- verify real `polyMesh`;
- verify `frontAndBack empty`;
- verify force references;
- verify alpha 8;
- verify Mach/Re/chord;
- verify startup/production dictionaries;
- restore after app rerun;
- legacy package compatibility.

## 23.3 UI

- page renders;
- six-mesh registry;
- run matrix state;
- budget table;
- plots with synthetic data;
- empty states explicit;
- no fake PASS;
- generic convergence dropdown removed;
- general postprocess unaffected.

Ejecutar:

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

Tests OpenFOAM reales solo con opt-in.

---

# 24. Fases de implementación

## Fase 1: auditoría

- localizar UI y backend;
- localizar schema;
- localizar Results contracts;
- identificar controles existentes;
- proponer diff.

## Fase 2: modelo de datos

- study config;
- manifest;
- registry;
- run matrix;
- migrations;
- tests.

## Fase 3: workspace independiente

- paths;
- restore;
- isolation;
- Results integration.

## Fase 4: solver strategy

- fixed staged time;
- budget;
- writer;
- runner;
- resume;
- summaries.

## Fase 5: UI execution

- overview;
- meshes;
- solver;
- matrix;
- run controls.

## Fase 6: postprocess

- per-run;
- RANS spatial;
- combined URANS;
- frequency;
- Courant;
- PIMPLE;
- reports.

## Fase 7: cleanup

- remove generic dropdown;
- documentation;
- changelog;
- full tests;
- visual inspection.

No mezclar todas las fases en un único cambio imposible de auditar. Mantén commits/diffs lógicos si el entorno lo permite.

---

# 25. Criterios de finalización

No declares la tarea completada hasta que:

1. exista la nueva página;
2. se carguen las seis mallas reales;
3. el workspace esté aislado;
4. pueda configurarse una matriz de `dt`;
5. el budget calcule pasos, tiempo y almacenamiento;
6. se generen casos dry-run con fixed `dt`;
7. se registren stages temporalmente;
8. las métricas y gráficas funcionen con datos reales o fixtures identificados;
9. se genere el informe de estudio;
10. el dropdown genérico haya desaparecido;
11. los tests pasen;
12. no se haya ejecutado CFD real sin autorización;
13. documentación y API estén sincronizadas;
14. ningún resultado sintético aparezca como validación real.

---

# 26. Entrega final de Codex

Al terminar, entrega:

1. resumen de arquitectura;
2. lista de archivos modificados;
3. migraciones;
4. screenshots o descripción verificable de UI;
5. ejemplos de JSON;
6. comandos de ejecución;
7. tests ejecutados;
8. fallos pendientes;
9. riesgos físicos/numericos;
10. confirmación explícita de que no se ejecutó una campaña CFD real;
11. explicación de cómo se selecciona y acepta un `deltaT`;
12. explicación de cómo se distingue un hotspot de Courant patológico de una región física relevante.

---

# 27. Restricciones finales

- No implementar 3D, FEM o FSI.
- No cambiar CATIA.
- No ejecutar CATIA.
- No ejecutar CFD real sin permiso.
- No eliminar Results históricos.
- No crear `polyMesh` vacío.
- No crear CSV falsos.
- No declarar `PASS` sin evidencia.
- No usar `Co=1` como controlador automático en este laboratorio.
- No ignorar Courant.
- No mezclar startup y sampling.
- No comparar tiempos físicos diferentes.
- No hacer PSD de datos no uniformes sin tratamiento explícito.
- No confundir PIMPLE outer correctors con Newton subiterations.
- No asumir GCI válido para la secuencia abierta.
- No convertir el laboratorio en una polar.
- No contaminar el workspace general.
