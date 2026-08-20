# INSTRUCCIONES COMPLETAS PARA CODEX
## Revisión manual de bases RANS, monitorización, propagación de configuración, rediseño de mallas abiertas y estudio PIMPLE 2/3/4

**Proyecto:** RamAir DESIGN APP  
**Contexto mínimo esperado:** backend API 15, solver schema 13  
**Laboratorio:** `CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8`  
**Results canónico:** `Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6`

Este documento amplía las implementaciones anteriores del **Validation & Convergence Lab**. Debe aplicarse sin destruir, sobrescribir ni invalidar las ejecuciones RANS ya iniciadas.

Antes de editar:

1. Leer `PROJECT_CONTEXT_FOR_CODEX.md`, `README_PROJECT_STRUCTURE.md`, `AGENTS.md` y `CHANGELOG.md`.
2. Leer los documentos previos de implementación del laboratorio y de estrategia temporal.
3. Inspeccionar la implementación real de:
   - `CFD_2D/app/ramair_cfd2d_app.py`
   - `CFD_2D/app/workflow_backend.py`
   - `CFD_2D/scripts/ramair_2d_validation_study.py`
   - staged runner, case writer, postprocess, monitor, Results y mesh builder.
4. Inspeccionar los directorios RANS existentes antes de migrar estados.
5. No ejecutar CATIA.
6. No lanzar una campaña CFD extensa automáticamente.
7. Gmsh, `gmshToFoam` y `checkMesh` sí pueden utilizarse en iteraciones acotadas de mallado.
8. Una ejecución OpenFOAM real corta solo se realizará si el entorno está disponible y la acción está explícitamente autorizada.

---

# 1. Problema observado y criterio científico

Las bases RANS/SIMPLE han alcanzado aproximadamente 20,000 iteraciones y el gate automático las ha marcado como `NOT_CONVERGED`, aunque las señales parecen estadísticamente estabilizadas.

Esto puede ocurrir porque el gate automático combina límites estrictos de:

- residuos;
- deriva de \(C_L\), \(C_D\) y \(C_M\);
- diferencias entre ventanas;
- desviación estándar;
- continuidad;
- tolerancias absolutas o relativas.

Una solución puede presentar fuerzas visualmente estables y no superar uno de esos umbrales. También puede ocurrir lo contrario: residuos bajos con fuerzas todavía derivando.

## 1.1 No renombrar silenciosamente “no convergido” como “convergido”

No debe modificarse un resultado histórico para afirmar que cumplió criterios que en realidad no cumplió.

Implementar estados separados:

```text
RANS_AUTO_CONVERGED
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
RANS_REVIEW_REQUIRED
RANS_REJECTED
```

Interpretación:

### `RANS_AUTO_CONVERGED`

Cumple el gate automático completo.

Puede usarse:

- en convergencia espacial RANS;
- como checkpoint de cada malla;
- como base común URANS.

### `RANS_USER_ACCEPTED_STATISTICALLY_STEADY`

No cumple algún umbral automático, pero el usuario revisa el postproceso y acepta que:

- los coeficientes están estadísticamente estabilizados;
- la deriva es suficientemente pequeña para el propósito;
- la continuidad es aceptable;
- no hay divergencia ni variables no acotadas;
- la decisión queda documentada.

Puede usarse:

- en convergencia espacial RANS, con marcador y warning de aceptación manual;
- como checkpoint URANS;
- en tablas y gráficos, diferenciándolo del auto-convergido.

### `RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY`

El campo es acotado y útil para iniciar URANS, pero no es aceptado como solución estacionaria para comparar medias RANS.

Puede usarse:

- como checkpoint URANS;
- en estudios PIMPLE y temporal.

No puede usarse:

- como punto válido de convergencia espacial RANS;
- para GCI/Richardson;
- como “resultado RANS convergido”.

### `RANS_REVIEW_REQUIRED`

La ejecución terminó o alcanzó el límite, pero todavía no ha sido revisada.

### `RANS_REJECTED`

El usuario o el gate detectan deriva, divergencia, incoherencia física o datos insuficientes.

## 1.2 La aprobación manual debe ser reversible y trazable

Guardar:

```json
{
  "automatic_gate_status": "NOT_CONVERGED",
  "review_status": "RANS_USER_ACCEPTED_STATISTICALLY_STEADY",
  "reviewed_at": "",
  "reviewed_by": "user",
  "review_reason": "",
  "accepted_uses": {
    "rans_mesh_convergence": true,
    "urans_initialization": true
  },
  "evidence_files": [],
  "config_hash": "",
  "mesh_hash": ""
}
```

Permitir:

- aprobar;
- cambiar a “solo inicialización”;
- revocar;
- añadir comentario.

No reescribir logs ni coeficientes históricos.

---

# 2. Postproceso RANS previo a la revisión

## 2.1 Permitir postprocesar cualquier base existente

El botón de postproceso debe estar disponible para:

```text
RANS_AUTO_CONVERGED
RANS_REVIEW_REQUIRED
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
TIMEOUT_PARTIAL
```

si existen datos reales suficientes.

No exigir primero que el estado sea “converged”.

## 2.2 Nuevo flujo de revisión

```text
Seleccionar base RANS
-> Generar/actualizar diagnóstico RANS
-> Revisar gráficas y métricas
-> Elegir:
   - Aceptar como estadísticamente estacionaria
   - Aceptar solo como inicialización URANS
   - Extender 5,000 iteraciones
   - Rechazar
```

## 2.3 Gráficas obligatorias de revisión RANS

Generar desde datos ya existentes, sin relanzar el solver:

1. residuos de `p`, `U`, `nuTilda`;
2. continuidad;
3. \(C_L\), \(C_D\), \(C_M\);
4. \(L/D\);
5. media móvil;
6. RMS móvil;
7. pendiente/drift móvil;
8. comparación de las dos ventanas finales;
9. medias y desviaciones por bloques;
10. media acumulada;
11. histograma o densidad de coeficientes en la ventana final;
12. tabla del gate con criterio, valor, umbral y PASS/FAIL;
13. tiempo por iteración y tiempo acumulado;
14. si existe último campo:
    - presión;
    - \(C_p\);
    - velocidad;
    - y+;
    - wall shear;
    - streamlines;
    - una captura final.

Para la revisión manual, las gráficas escalares son obligatorias. Los campos volumétricos son opcionales bajo demanda.

## 2.4 Ventanas

Usar por defecto:

```text
review_window = max(1000 iteraciones, último 10% de la ejecución)
comparison_window = ventana inmediatamente anterior de igual longitud
```

Mostrar también sensibilidad con:

```text
último 5%
último 10%
último 20%
```

La aceptación no debe depender de una única ventana elegida favorablemente.

## 2.5 Perfil abierto

Si las fuerzas oscilan de forma persistente en SIMPLE:

- no ocultar la oscilación;
- indicar que puede existir una solución inherentemente no estacionaria;
- permitir aceptación para inicialización;
- aceptar como RANS estadísticamente estacionario solo con justificación explícita;
- excluir de GCI si no representa un límite estacionario claro.

---

# 3. Preservar las ejecuciones existentes

## 3.1 Prohibición de destrucción

Los cambios no deben:

- borrar tiempos;
- borrar logs;
- reiniciar contadores;
- sustituir coefficient histories;
- sobrescribir checkpoints;
- cambiar retrospectivamente el gate automático;
- regenerar una base si el usuario no lo solicita.

## 3.2 Migración de resultados actuales

Crear un migrador de solo metadatos:

1. detectar directorios RANS existentes;
2. leer `case_summary`, logs, fuerzas y campos;
3. calcular hash de malla y física;
4. crear `rans_review_manifest.json`;
5. conservar el estado automático original;
6. establecer `RANS_REVIEW_REQUIRED`;
7. permitir postproceso y revisión.

Si un checkpoint compatible puede construirse desde el último estado existente, hacerlo únicamente después de aprobación del usuario o como checkpoint diagnóstico claramente etiquetado.

## 3.3 Reprocesado manual

Botón:

```text
Recalcular diagnóstico y gráficas sin ejecutar el solver
```

Debe ser idempotente y archivar solo el postproceso anterior cuando corresponda, nunca la simulación.

---

# 4. Análisis de ejecución y monitores RANS/URANS

## 4.1 Problema actual

El selector superior parece listar solo ejecuciones URANS, por lo que no se puede observar una base SIMPLE mientras se ejecuta, especialmente dentro de la cola de seis mallas.

## 4.2 Registro unificado de ejecuciones

Crear una fuente única:

```text
execution_registry.json
```

con entradas RANS y URANS:

```json
{
  "run_id": "",
  "mode": "RANS|URANS",
  "topology": "closed|open",
  "mesh_level": "coarse|medium|fine",
  "stage": "SIMPLE|A|B|C|D|E",
  "status": "",
  "started_at": "",
  "updated_at": "",
  "log_path": "",
  "monitor_paths": {}
}
```

El selector debe permitir:

```text
Todas
RANS/SIMPLE
URANS/PIMPLE
```

y listar todas las ejecuciones reales y parciales.

## 4.3 Navegación automática

Al iniciar:

- una base individual;
- una cola RANS;
- un pilot;
- un URANS;
- un estudio PIMPLE;

la app debe:

1. cambiar a la página `Análisis de ejecución`;
2. activar el modo correcto RANS o URANS;
3. seleccionar automáticamente el `run_id`;
4. mostrar el monitor.

En una cola, al pasar a la siguiente malla:

1. actualizar `active_run_id`;
2. actualizar topología y nivel;
3. hacer `st.rerun()` o mecanismo equivalente seguro;
4. seleccionar el nuevo monitor;
5. conservar un botón `Fijar monitor actual` para el usuario que no quiera seguir automáticamente la cola.

## 4.4 Títulos

RANS:

```text
Closed | Coarse | RANS/SIMPLE | Iteration 18,450 | Base-state queue 1/6
```

URANS:

```text
Open | Medium | URANS/PIMPLE | dt=2.5e-5 s | Stage D — Settling
```

PIMPLE:

```text
Closed | Coarse | PIMPLE sensitivity | nOuter=2 | dt=<value>
```

## 4.5 Monitores RANS

- iteración;
- bloque inicial/extensión;
- residuos;
- continuidad;
- \(C_L\);
- \(C_D\), \(C_M\);
- media/drift de ventana final;
- gate parcial;
- wall time;
- estimación restante.

## 4.6 Monitores URANS

- fase A-E;
- tiempo físico;
- \(t^*\);
- `deltaT`;
- Co;
- continuidad;
- residuos por outer corrector;
- fuerzas;
- pasos;
- elapsed/remaining.

## 4.7 Rendimiento

Mantener:

```text
refresh de plots: 30 s
opciones: 15/30/60 s
```

Usar:

- parsing incremental;
- cache por offset;
- downsampling solo visual;
- última ventana visible;
- no leer campos volumétricos;
- no ejecutar ParaView;
- no regenerar PSD completa durante la ejecución.

---

# 5. Verificación de propagación de controles

## 5.1 Problema a prevenir

Los parámetros elegidos en la página solver deben aplicarse realmente a todas las mallas de la cola o matriz:

- iteraciones;
- extensiones;
- gate;
- correctores;
- relajaciones;
- esquemas;
- ranks;
- almacenamiento;
- duración;
- `deltaT`.

## 5.2 Congelar configuración al iniciar una cola

Al pulsar ejecutar:

1. resolver todos los controles;
2. aplicar overrides de topología explícitos;
3. guardar:

```text
resolved_batch_config.json
```

4. calcular hash;
5. copiar un snapshot inmutable a cada run;
6. impedir que cambios posteriores de widgets alteren una cola en curso.

Los cambios de UI posteriores crean una nueva revisión para futuras ejecuciones.

## 5.3 Auditoría previa

Mostrar tabla:

| Parámetro | Valor seleccionado | Valor efectivo closed | Valor efectivo open |
|---|---:|---:|---:|

Incluir como mínimo:

- max SIMPLE iterations;
- extension block;
- gate tolerances;
- relaxation;
- SIMPLE non-orthogonal;
- PIMPLE outer;
- PIMPLE pressure correctors;
- PIMPLE non-orthogonal;
- time scheme;
- `deltaT`;
- ranks;
- field write;
- purge;
- monitor refresh.

## 5.4 Verificación de diccionarios

Después de escribir cada caso:

- parsear `fvSolution`;
- parsear `fvSchemes`;
- parsear `controlDict`;
- comparar con `resolved_run_config.json`;
- fallar preflight si hay mismatch;
- guardar `applied_configuration_audit.json`.

La UI debe mostrar:

```text
Configuración aplicada correctamente
```

o una tabla de discrepancias.

---

# 6. Correctores no ortogonales

Mantener:

```foam
SIMPLE
{
    nNonOrthogonalCorrectors 0;
}

PIMPLE
{
    nOuterCorrectors          3;
    nCorrectors               2;
    nNonOrthogonalCorrectors  1;
}
```

OpenFOAM usa `nNonOrthogonalCorrectors` para repetir la ecuación de presión y actualizar la corrección no ortogonal explícita. Cero es habitual en estacionario; uno es una baseline prudente en transitorio.

No aplicar automáticamente más correctores por un valor máximo aislado de no ortogonalidad. Añadir una sensibilidad 0/1 separada si se desea.


---

# 7. Rediseño definitivo de las mallas abiertas coarse y fine

## 7.1 Objetivo

La familia abierta actual tiene aproximadamente:

```text
open_coarse: 269,864 celdas
open_medium: 302,692 celdas
open_fine:   420,728 celdas
```

La separación coarse-medium es demasiado pequeña para un estudio espacial robusto. Deben sustituirse **coarse y fine** por una única malla definitiva cada una, más diferenciadas de `open_medium`.

Mantener `open_medium` como referencia central mientras se rediseñan las otras dos.

## 7.2 Baseline medium que debe inspeccionarse

El contexto actual indica aproximadamente:

```text
contour discretization:        2,800 segmentos
exterior TE nodes:                32
interior factors:              0.40 / 0.28
boundary-layer rows:             50
growth ratio:                  1.075
manual y1:                     25e-6 m
compatibility strip:           0.0035 c
post-BL/exterior target:       ~0.08 c
next exterior transition:      ~0.20 c
farfield target:               ~3.5 c
algorithm:                     Frontal-Delaunay
```

Codex debe verificar los nombres y valores reales en el JSON activo y el builder. No asumir que las etiquetas anteriores coinciden literalmente con las claves.

## 7.3 Principio de refinamiento

El estudio debe variar de forma coherente:

### Coarse

- tamaños internos mayores;
- tamaño junto a interfaz mayor;
- tamaño al final de la capa prismática mayor;
- menos capas prismáticas;
- growth ratio mayor;
- menos puntos tangenciales.

### Fine

- tamaños internos menores;
- tamaño junto a interfaz menor;
- tamaño al final de la capa prismática menor;
- más capas prismáticas;
- growth ratio menor;
- más puntos tangenciales.

No cambiar `y1`, geometría física, dominio, inlet representation ni patches.

## 7.4 Preservar el espesor total de la capa prismática

No variar arbitrariamente número de capas y ratio de crecimiento, porque eso cambiaría simultáneamente la altura total de la zona prismática.

Para una primera altura \(y_1\), \(N\) capas y ratio \(r\):

\[
H_{BL}=y_1\frac{r^N-1}{r-1}
\]

Con la baseline aproximada:

```text
y1 = 25e-6 m
N = 50
r = 1.075
H_BL ≈ 0.012063 m ≈ 0.012063 c
```

Usar como valores iniciales derivados:

### Coarse

```text
N_BL = 35
growth ratio ≈ 1.1247
```

### Fine

```text
N_BL = 65
growth ratio ≈ 1.0512
```

Ambos conservan aproximadamente el mismo \(H_{BL}\) que la medium.

Si el builder utiliza `Thickness` explícita, fijar:

```text
BL_thickness = medium BL_thickness
```

y resolver el ratio compatible con `N_BL`.

No usar valores redondeados que cambien notablemente el espesor sin documentarlo.

## 7.5 Configuración definitiva inicial

Mapear estos objetivos a las claves reales.

| Parámetro | Coarse definitivo | Medium actual | Fine definitivo |
|---|---:|---:|---:|
| Contour segments | 2,200 | 2,800 | 3,600 |
| Exterior TE nodes | 24 | 32 | 42 |
| Interior core size factor | 0.52 | 0.40 | 0.30 |
| Interface/inner-wall size factor | 0.34 | 0.28 | 0.20 |
| Post-BL target | 0.11 c | 0.08 c | 0.055 c |
| Exterior transition target | 0.26 c | 0.20 c | 0.15 c |
| Farfield target | 4.0 c | 3.5 c | 3.0 c |
| BL layers | 35 | 50 | 65 |
| BL growth | 1.1247 | 1.075 | 1.0512 |
| y1 | 25 µm | 25 µm | 25 µm |
| Inlet strip | 0.0035 c | 0.0035 c | 0.0035 c |

Estos son objetivos iniciales de implementación, no resultados validados. Codex debe iterar hasta obtener una sola coarse y una sola fine aprobables.

## 7.6 Objetivos de conteo

Buscar aproximadamente:

```text
new open_coarse: 210,000–245,000 celdas
open_medium:     ~302,692 celdas
new open_fine:   500,000–600,000 celdas
```

No aceptar una coarse demasiado próxima a medium ni una fine que aumente celdas solo en el farfield sin mejorar la región física relevante.

## 7.7 Uso correcto de Gmsh

El manual de Gmsh establece que el tamaño local es el mínimo de las fuentes activas:

- tamaños en puntos;
- tamaño por curvatura;
- background fields;
- restricciones por entidad;
- restricciones transfinite.

Por tanto, una modificación puede parecer no tener efecto si otra fuente impone un tamaño menor.

Cuando el mallado esté gobernado por fields, verificar si procede:

```geo
Mesh.MeshSizeFromPoints = 0;
Mesh.MeshSizeFromCurvature = 0;
Mesh.MeshSizeExtendFromBoundary = 0;
```

No aplicarlo ciegamente: confirmar que los tamaños de LE, TE, inlet y pared están completamente prescritos por transfinite/fields.

Usar:

- `Distance`;
- `Threshold`;
- `Min`;
- `BoundaryLayer`;
- `Transfinite Curve`;

según la implementación actual.

Recordar:

```text
Transfinite Curve sobrescribe otras prescripciones de tamaño en esa curva.
```

El campo `BoundaryLayer` debe mantener:

- first layer;
- thickness;
- ratio;
- quads;
- curvas correctas.

El manual indica que Frontal-Delaunay suele producir alta calidad, mientras que Delaunay maneja mejor campos con gradientes fuertes. Mantener Frontal-Delaunay como primera opción; probar Delaunay únicamente si las transiciones de tamaño provocan fallos o degradación y comparar la calidad real.

## 7.8 Iteración de diseño

Codex puede crear candidatos internos:

```text
open_coarse_candidate_001...
open_fine_candidate_001...
```

pero la UI final no debe mostrar tres alternativas por nivel.

Proceso:

1. generar candidato;
2. convertir;
3. ejecutar `checkMesh`;
4. analizar histogramas;
5. abrir/inspeccionar visualmente;
6. ajustar;
7. seleccionar uno;
8. archivar la coarse/fine anterior;
9. promover la seleccionada al registro principal.

La Mesh Registry final debe mostrar solo:

```text
open_coarse
open_medium
open_fine
```

Añadir una sección avanzada `Historial de candidatos` sin convertirlos en opciones activas.

## 7.9 Calidad mínima

Requisitos:

```text
checkMesh = OK
negative volumes = 0
max non-orthogonality <= 45 deg
max skewness <= 0.72
min determinant >= 0.04
min interpolation weight >= 0.08
min volume ratio >= 0.12
```

Objetivo preferido: mantener o mejorar los márgenes actuales.

Además:

- comparar histogramas de calidad;
- contar caras con bajo interpolation weight;
- localizar peores celdas;
- comprobar BL;
- comprobar interfaz inlet/cavity;
- comprobar TE;
- comprobar transición post-BL;
- comprobar que no aparece sobre-refinamiento accidental.

## 7.10 Guardado y sustitución

No borrar las mallas anteriores.

Usar archivado versionado y actualizar de forma atómica:

- package;
- manifest;
- mesh registry;
- cell count;
- quality report;
- hashes;
- preview.

No tocar simulaciones históricas asociadas a las mallas anteriores.

## 7.11 Abrir en Gmsh

Mantener el botón:

```text
Abrir malla seleccionada en Gmsh
```

Después de promover coarse/fine:

- resolver el `.msh` nuevo;
- abrirlo sin regenerar;
- permitir ver estadísticas;
- mostrar claramente el nivel y conteo.

---

# 8. Estudio aislado de nOuterCorrectors = 2, 3, 4

## 8.1 Caso seleccionado

Preparar inicialmente:

```text
topology: closed
mesh: coarse
alpha: 8 deg
checkpoint: estado final RANS actual de closed_coarse
```

Si el estado automático es `NOT_CONVERGED`, exigir antes:

```text
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
```

o:

```text
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
```

Para este estudio basta la aceptación como inicialización si el campo está acotado.

## 8.2 Aislamiento correcto

Crear tres clones independientes del **mismo checkpoint**:

```text
closed_coarse_pimple2
closed_coarse_pimple3
closed_coarse_pimple4
```

No ejecutar 3 desde el final de 2 ni 4 desde el final de 3.

Mantener idénticos:

- malla;
- mesh hash;
- checkpoint;
- física;
- `deltaT`;
- scheme;
- `nCorrectors`;
- `nNonOrthogonalCorrectors=1`;
- ranks;
- duración;
- writes;
- sondas.

Cambiar únicamente:

```text
nOuterCorrectors = 2, 3, 4
```

## 8.3 Duración inicial

Usar una campaña breve:

```text
settling: 5 tc
sampling: 20 tc
```

Si la ventana no contiene al menos diez ciclos de la frecuencia dominante, marcar el análisis frecuencial como preliminar y permitir extensión.

## 8.4 deltaT

Usar:

1. un `deltaT` que haya pasado la prueba corta en `closed_coarse`;
2. no usar el `deltaT` más fino de toda la matriz;
3. no cambiar `deltaT` entre casos.

Si todavía no existe pilot válido:

- preparar los tres casos;
- dejar el botón de ejecución bloqueado;
- indicar el requisito.

## 8.5 Resultados

Comparar:

- residuales inicial/final por paso;
- residual reduction por outer;
- continuidad;
- Co;
- \(C_L,C_D,C_M\) medios;
- RMS;
- PSD;
- St dominante;
- boundedness;
- s/paso;
- CPU por segundo físico;
- coste relativo;
- diferencia frente a `nOuter=4`.

## 8.6 Criterio

Recomendar el menor número que:

- converge internamente;
- no cambia medias fuera de tolerancia;
- no cambia RMS >5%;
- no cambia frecuencia >2%;
- mantiene continuidad;
- no introduce oscilaciones numéricas.

Mantener 3 como baseline hasta obtener evidencia.

## 8.7 Ejecución

Añadir:

```text
Preparar estudio 2/3/4
Ejecutar estudio 2/3/4
Reanudar casos incompletos
Generar comparación
```

El usuario ha autorizado dejar preparado el inicio. Si el runtime está disponible y existe checkpoint/pilot compatible, Codex puede iniciar una ejecución real corta y acotada. Si no, debe dejar los tres casos `READY` y documentar el comando exacto.

---

# 9. Correcciones de bugs y robustez

Revisar:

1. selectores que solo muestran URANS;
2. active run no actualizado al cambiar de malla;
3. traceback crudo;
4. mismatch entre widgets y diccionarios;
5. pérdida de estado al rerun de Streamlit;
6. rutas de Results/workspace;
7. reanudación de cola;
8. postproceso bloqueado por estado;
9. hashes obsoletos;
10. sobrescritura accidental;
11. monitores que consumen CPU;
12. configuración no congelada;
13. selector de mallas antiguas/candidatas;
14. PIMPLE study que no clona el mismo checkpoint.

Toda corrección debe incluir test de regresión.


---

# 10. Cambios de UI

## 10.1 Mesh Registry

Añadir columnas:

- Topología
- Nivel
- Celdas
- Calidad
- Estado de conjunto
- Estado RANS
- Checkpoint
- Último análisis
- Malla activa

Acciones:

```text
Cargar conjunto
Abrir malla en Gmsh
Generar/revisar base RANS
Ver configuración efectiva
```

## 10.2 RANS review

Crear una subsección:

```text
Revisión y aprobación de bases RANS
```

Selector que incluya todas las bases existentes.

Botones:

```text
Generar postproceso
Aceptar como estadísticamente estacionaria
Aceptar solo para inicialización URANS
Extender 5,000 iteraciones
Rechazar
Revocar aprobación
```

No mostrar el botón de aceptación hasta que exista un diagnóstico actualizado.

## 10.3 Análisis de ejecución

Selector superior:

```text
Modo:
  Todas
  RANS/SIMPLE
  URANS/PIMPLE
  Sensibilidad PIMPLE
```

Selector secundario:

```text
Topología
Malla
Run ID
```

Checkbox:

```text
Seguir automáticamente la ejecución activa
```

Default `true`.

## 10.4 Indicadores visuales

Usar etiquetas:

```text
Auto-convergida
Aprobada manualmente como estacionaria
Aprobada solo como inicialización
Revisión pendiente
Rechazada
```

En gráficos de convergencia:

- auto-convergida: marcador normal;
- manual estacionaria: marcador distinto;
- solo inicialización: no incluir;
- rechazado: no incluir.

## 10.5 Explicación científica

Añadir help:

```text
Una aprobación manual no modifica el resultado del gate automático. Registra
que, tras revisar las señales, el usuario considera la solución adecuada para
un uso concreto. “Solo inicialización” permite iniciar URANS, pero no utilizar
las medias en convergencia espacial RANS.
```

---

# 11. Modelo de datos

## 11.1 RANS review manifest

```json
{
  "run_id": "",
  "topology": "",
  "mesh_level": "",
  "mesh_hash": "",
  "physics_hash": "",
  "automatic_gate": {
    "status": "NOT_CONVERGED",
    "failed_criteria": []
  },
  "review": {
    "status": "RANS_REVIEW_REQUIRED",
    "reviewed_at": null,
    "reason": null,
    "use_for_rans_mesh_convergence": false,
    "use_for_urans_initialization": false
  },
  "postprocess": {
    "status": "NOT_GENERATED",
    "generated_at": null,
    "evidence": []
  },
  "checkpoint": {
    "status": "NOT_CREATED",
    "checkpoint_id": null,
    "field_hashes": {}
  }
}
```

## 11.2 Resolved execution config

```json
{
  "batch_id": "",
  "created_at": "",
  "ui_revision": 0,
  "closed_effective": {},
  "open_effective": {},
  "config_hash": "",
  "runs": []
}
```

## 11.3 Execution registry

Debe actualizarse atómicamente y contener:

```text
active_run_id
active_mode
active_stage
queue_position
queue_total
follow_active_default
```

---

# 12. Uso de datos en convergencia de malla

## 12.1 RANS espacial

Incluir:

```text
RANS_AUTO_CONVERGED
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
```

Excluir:

```text
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
RANS_REVIEW_REQUIRED
RANS_REJECTED
```

## 12.2 Marcado y provenance

En tablas:

| Caso | Gate automático | Revisión | Uso |
|---|---|---|---|

En gráficos, añadir una leyenda específica para puntos aprobados manualmente.

## 12.3 GCI

No usar un punto manualmente aceptado sin mostrarlo en el informe.

Si la aprobación manual tiene deriva o incertidumbre mayor que la diferencia espacial:

```text
GCI_NOT_RELIABLE_RANS_REVIEW_UNCERTAINTY
```

No forzar extrapolación.

## 12.4 Checkpoint URANS

Tanto auto-convergido como aceptado manualmente pueden producir checkpoint.

El checkpoint debe conservar:

- estado automático;
- revisión;
- motivo;
- hashes;
- campos;
- última iteración.

---

# 13. Archivos de salida

Por base RANS:

```text
rans_review_manifest.json
rans_gate_table.csv
rans_window_statistics.csv
rans_block_statistics.csv
rans_review_report.md
rans_residuals.png
rans_forces.png
rans_moving_statistics.png
rans_window_comparison.png
rans_gate_summary.png
rans_execution_cost.json
```

Campos opcionales bajo demanda:

```text
rans_final_pressure.png
rans_final_cp.png
rans_final_velocity.png
rans_final_streamlines.png
rans_final_yplus.png
rans_final_wall_shear.png
```

Por batch:

```text
resolved_batch_config.json
applied_configuration_audit.json
execution_registry.json
batch_status.json
```

Por PIMPLE study:

```text
pimple_outer_study_manifest.json
pimple_outer_comparison.csv
pimple_outer_residuals.png
pimple_outer_forces.png
pimple_outer_frequency.png
pimple_outer_cost.png
pimple_outer_report.md
```

Por rediseño de malla:

```text
open_mesh_refinement_manifest.json
open_coarse_quality_report.json
open_fine_quality_report.json
open_mesh_size_field_audit.json
open_mesh_histograms.png
open_mesh_previews/
```

No crear archivos vacíos para representar datos inexistentes.

---

# 14. Almacenamiento y postproceso

## 14.1 Conservar ejecuciones RANS actuales

No limpiar los directorios actuales como parte de esta modificación.

Si existen muchos tiempos estacionarios:

- generar primero inventario;
- proponer compactación;
- requerir confirmación;
- preservar último estado, recuperación, escalares y logs.

## 14.2 RANS

Mantener el perfil compacto:

```text
steady_checkpoint_compact
```

El postproceso manual debe reutilizar el último campo ya guardado.

## 14.3 URANS

Mantener:

```text
transient_convergence_compact
```

No generar animaciones o VTK por defecto.

## 14.4 Monitor

No regenerar imágenes volumétricas mientras corre el solver.

---

# 15. Plan de ejecución y verificación

## 15.1 Primero: migración sin solver

1. detectar bases existentes;
2. crear manifests;
3. habilitar postproceso;
4. generar diagnósticos de un caso existente;
5. comprobar aprobación manual;
6. comprobar creación de checkpoint sin borrar datos;
7. comprobar selector RANS.

## 15.2 Después: mallas abiertas

1. crear copia de configs;
2. generar coarse candidate;
3. iterar;
4. promover una sola coarse;
5. generar fine candidate;
6. iterar;
7. promover una sola fine;
8. abrir ambas en Gmsh;
9. actualizar Results/workspace de forma atómica;
10. no ejecutar una matriz CFD completa.

## 15.3 Después: PIMPLE study

Si existe un estado final de `closed_coarse`:

1. generar diagnóstico;
2. obtener aprobación para inicialización;
3. localizar/crear checkpoint;
4. elegir `deltaT` pilot-approved;
5. clonar 2/3/4;
6. preparar;
7. dejar `READY`.

Si el entorno y la autorización permiten ejecución real:

- ejecutar secuencialmente;
- mantener presupuesto corto;
- preservar parciales;
- generar comparación.

Si no:

- documentar comandos exactos;
- no inventar resultados.

---

# 16. Tests obligatorios

## 16.1 Revisión manual

- no se puede aprobar sin postproceso;
- la aprobación no cambia el gate automático;
- estacionaria manual se incluye en RANS;
- initialization-only se excluye;
- revocación funciona;
- motivo y timestamp obligatorios;
- datos existentes no se borran.

## 16.2 Monitores

- selector incluye RANS;
- auto-navigation;
- cambio de run en cola;
- follow/pin;
- título correcto;
- registro atómico;
- refresh ligero.

## 16.3 Configuración

- snapshot congelado;
- parámetros aplicados a todas las mallas;
- overrides visibles;
- parseo de diccionarios;
- mismatch bloquea ejecución;
- cambios de UI no afectan cola activa.

## 16.4 Mallas

- parámetros coarse/fine correctos;
- medium no cambia;
- y1 igual;
- espesor BL conservado;
- ratios derivados;
- `.msh` real;
- `checkMesh`;
- thresholds;
- conteos suficientemente separados;
- solo una coarse y una fine activas;
- antiguas archivadas;
- históricos intactos.

## 16.5 PIMPLE

- tres clones desde mismo checkpoint;
- único parámetro variable;
- mismo tiempo físico;
- mismo `deltaT`;
- misma malla;
- comparación generada;
- no encadenamiento.

## 16.6 Regresión general

Ejecutar:

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

Tests reales OpenFOAM solo con opt-in y acción explícita.

---

# 17. Documentación

Actualizar:

- `CHANGELOG.md`;
- `PROJECT_CONTEXT_FOR_CODEX.md`;
- `README_PROJECT_STRUCTURE.md`;
- README del Validation Lab;
- notas del schema;
- ayuda UI.

Documentar:

1. diferencia entre gate automático y aceptación manual;
2. qué estados se usan en RANS y URANS;
3. selector RANS/URANS;
4. configuración congelada;
5. nuevos parámetros coarse/fine;
6. preservación del espesor BL;
7. estudio PIMPLE;
8. estado de ejecución real/preparada.

---

# 18. Criterios de finalización

No declarar completo hasta que:

1. las bases RANS existentes sigan intactas;
2. puedan postprocesarse aunque sean `NOT_CONVERGED`;
3. la revisión manual sea trazable;
4. se pueda aceptar para RANS o solo URANS;
5. los monitores RANS aparezcan durante cola;
6. el selector incluya RANS;
7. el monitor cambie automáticamente de malla;
8. se audite la configuración efectiva;
9. se sustituyan coarse y fine abiertas por una sola opción cada una;
10. las nuevas mallas pasen Gmsh, conversión y `checkMesh`;
11. puedan abrirse en Gmsh;
12. el estudio PIMPLE 2/3/4 quede preparado o ejecutado de forma acotada;
13. no se hayan destruido simulaciones;
14. los tests pasen;
15. no se presente un resultado manualmente aceptado como auto-convergido.

---

# 19. Restricciones finales

- No falsificar convergencia.
- No borrar ejecuciones actuales.
- No forzar el perfil abierto a ser estacionario.
- No usar un checkpoint de otra malla.
- No modificar una cola activa con widgets posteriores.
- No ocultar overrides de topología.
- No cambiar `y1` entre niveles.
- No variar sin control el espesor total de BL.
- No dejar tres alternativas coarse o fine en la UI.
- No encadenar los casos PIMPLE.
- No ejecutar una campaña larga sin presupuesto y confirmación.
- No generar resultados sintéticos como físicos.
- No tocar CATIA, 3D, FEM o FSI.

---

# 20. Referencias técnicas que debe consultar Codex

1. Manual OpenFOAM Foundation, sección `fvSolution`, SIMPLE/PIMPLE y correctores no ortogonales.
2. Manual Gmsh 4.15.2:
   - especificación de tamaños;
   - `Distance`;
   - `Threshold`;
   - `Min`;
   - `BoundaryLayer`;
   - `Transfinite Curve`;
   - algoritmos Delaunay y Frontal-Delaunay.
3. `PROJECT_CONTEXT_FOR_CODEX.md`.
4. `README_PROJECT_STRUCTURE.md`.
5. Cummings, Morton y McDaniel, estudio de paso temporal, malla, subiteraciones y frecuencias.
