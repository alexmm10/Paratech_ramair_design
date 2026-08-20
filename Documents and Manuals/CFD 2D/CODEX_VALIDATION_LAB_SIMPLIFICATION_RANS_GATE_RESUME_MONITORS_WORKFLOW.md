# INSTRUCCIONES PARA CODEX
## Simplificación del Validation & Convergence Lab, revisión del gate RANS, reanudación, monitores y reorganización del análisis

**Proyecto:** RamAir DESIGN APP  
**Contexto de referencia:** 2026-07-29  
**Backend actual indicado por el contexto:** API 17  
**Solver general:** schema 13  
**Laboratorio:** `CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8`  
**Caso Results:** `Results/RamAir_closed_open_mesh_convergence_M0p15_Re1p9e6`

Este documento sustituye las instrucciones anteriores que todavía proponían
candidatas o un nuevo rediseño de las mallas abiertas. Las mallas abiertas
definitivas ya han sido generadas, verificadas y promovidas:

| Nivel | Celdas |
|---|---:|
| `open_coarse` | 223,080 |
| `open_medium` | 302,692 |
| `open_fine` | 502,474 |

No regenerar ni sustituir estas tres mallas como parte de esta tarea.

Antes de editar:

1. Leer `PROJECT_CONTEXT_FOR_CODEX.md`.
2. Leer `README_PROJECT_STRUCTURE.md`.
3. Leer `AGENTS.md` y `CHANGELOG.md`.
4. Inspeccionar la implementación real del laboratorio y sus tests.
5. Preservar las ejecuciones RANS/URANS existentes.
6. No ejecutar CATIA.
7. No iniciar una campaña CFD larga.
8. No presentar un caso aprobado manualmente como auto-convergido.

---

# 1. Objetivo de esta actualización

La actualización debe:

1. simplificar la estructura visual del laboratorio;
2. corregir y compactar los monitores;
3. mejorar la reanudación de las seis bases RANS;
4. permitir revisar y aprobar bases RANS recientes o almacenadas;
5. evitar extensiones estacionarias innecesarias;
6. centrar la convergencia espacial RANS en la comparación de mallas;
7. colocar la matriz URANS después de validar las bases RANS;
8. mantener separadas las vistas RANS, URANS y sensibilidad PIMPLE;
9. eliminar subsecciones de mallado que ya no son necesarias;
10. garantizar que la configuración elegida en la UI sea la configuración aplicada.

---

# 2. Estructura definitiva de la página

Reorganizar el laboratorio en este orden:

```text
1. Resumen
2. Mallas y condiciones
3. Solver y estrategia temporal
4. Análisis por ejecución
   4.1 RANS/SIMPLE
   4.2 URANS/PIMPLE
   4.3 Sensibilidad a correctores externos PIMPLE
5. Convergencia espacial RANS
6. Matriz de ejecuciones URANS
7. Convergencia conjunta malla-tiempo URANS
8. Análisis de frecuencias
9. Análisis de Courant
10. Informes y exportación
```

Eliminar:

```text
Todos
Candidato abierto ligero
Rediseño definitivo open coarse/fine
```

No dejar páginas vacías ni accesos duplicados.

## 2.1 Motivo del orden

La secuencia de decisión debe ser:

```text
seleccionar malla/condición
-> generar/revisar base RANS
-> aceptar el uso de cada base
-> comparar convergencia espacial RANS
-> configurar y ejecutar la matriz URANS
-> estudiar convergencia temporal, frecuencias y Courant
```

La matriz transitoria no debe aparecer como paso principal antes de disponer de
estados iniciales RANS revisados y utilizables.

---

# 3. Mallas y condiciones

Unificar:

```text
Mesh Registry
Operating Conditions
```

en:

```text
Mallas y condiciones
```

## 3.1 Tabla simplificada de mallas

Mostrar:

| Topología | Nivel | Celdas | Calidad | Estado RANS | Acción |
|---|---|---:|---|---|---|

No mostrar en la vista principal:

- `checkpoint_id`;
- `mesh_hash`;
- identificadores internos largos;
- rutas completas.

Estos datos permanecen en:

```text
Detalles técnicos
Provenance
Descargar manifiesto
```

## 3.2 Condiciones

Mostrar junto a la tabla:

```text
Mach
Reynolds
cuerda
ángulo de ataque
U_inf
rho
mu/nu
modelo turbulento
```

La primera campaña permanece bloqueada a:

```text
M = 0.15
Re = 1.9e6
c = 1 m
alpha = 8 deg
Spalart-Allmaras
```

Explicar que no es una polar.

## 3.3 Acciones

```text
Cargar conjunto seleccionado
Abrir malla en Gmsh
Ver calidad
Ir a base RANS
```

Mantener la restauración atómica de geometría, caso y malla, pero usar el
nombre comprensible:

```text
Conjunto coherente de simulación
```

---

# 4. Eliminar subsecciones de rediseño de malla abierta

Eliminar de UI, navegación y configuración activa:

```text
Candidato abierto ligero
Rediseño definitivo open coarse/fine
Promover candidata
Historial de candidatos como sección principal
```

Mantener únicamente el historial archivado en Results/Previous Versions.

No borrar:

- candidatos históricos;
- paquetes reemplazados;
- informes Gmsh/checkMesh;
- hashes y provenance.

El laboratorio debe usar directamente:

```text
open_coarse = 223080
open_medium = 302692
open_fine = 502474
```

Actualizar tests y documentación para que no esperen controles de rediseño.

---

# 5. Monitores: diseño definitivo

## 5.1 Eliminar continuidad del monitor visual

No mostrar una gráfica de continuidad durante ejecución.

Continuidad debe:

- seguir calculándose;
- seguir registrándose;
- aparecer en la tabla final;
- formar parte del gate;
- aparecer en el diagnóstico;
- activar alarmas.

No eliminar sus datos del solver ni del backend.

## 5.2 Dos gráficos compactos

Mostrar únicamente dos paneles principales, con altura moderada.

### Panel 1 — Residuales del solver

Título RANS:

```text
Convergencia de residuos — RANS/SIMPLE
```

Título URANS:

```text
Residuos por paso físico — URANS/PIMPLE
```

Ejes RANS:

```text
x: Iteración SIMPLE
y: Residuo inicial
```

Ejes URANS:

```text
x: Tiempo físico [s] o paso temporal
y: Residuo inicial
```

Configurar:

```python
ax.set_yscale("log")
```

Requisitos:

- no representar valores <=0 directamente;
- sustituir solo para visualización por `NaN` o floor documentado;
- conservar los valores brutos;
- líneas para `p`, `Ux/U`, `nuTilda` y otros campos reales;
- leyenda descriptiva;
- grid mayor y menor;
- títulos de ejes obligatorios;
- no etiquetar iteración SIMPLE como tiempo físico.

### Panel 2 — Coeficientes aerodinámicos

Título RANS:

```text
Evolución de coeficientes aerodinámicos — RANS/SIMPLE
```

Título URANS:

```text
Evolución de coeficientes aerodinámicos — URANS/PIMPLE
```

Eje x:

```text
Iteración SIMPLE
```

o:

```text
Tiempo físico [s]
```

Usar doble eje y:

```text
eje izquierdo:
  Cl
  Cd
  Cm

eje derecho:
  Cl/Cd
```

Etiquetas:

```text
izquierdo: Coeficiente aerodinámico [-]
derecho: Eficiencia Cl/Cd [-]
```

Calcular:

```python
efficiency = Cl / Cd
```

con:

- `NaN` cuando `abs(Cd) < epsilon`;
- sin clip en CSV;
- límites robustos solo para display;
- conteo de valores descartados/no finitos.

La combinación de Cl, Cd y Cm en el mismo eje puede comprimir Cd/Cm. Añadir
un control de visualización:

```text
Vista compacta
Vista separada de Cd/Cm
```

El monitor por defecto usa la vista compacta solicitada. El postproceso puede
usar paneles separados.

## 5.3 Tamaño y actualización

Default:

```text
refresh: 30 s
altura por panel: 260–320 px
```

Usar:

- parsing incremental;
- downsampling de display;
- cache;
- ventana reciente configurable;
- ejecución activa fijada por defecto.

No volver a generar PNG si los archivos fuente no cambiaron.

## 5.4 Títulos de caso

Encabezado:

```text
Closed | Coarse | RANS/SIMPLE | Iteration 18,450
```

o:

```text
Open | Medium | URANS/PIMPLE | dt=2.5e-5 s | Stage D — Settling
```

Incluir estado y progreso de batch cuando proceda.



---

# 6. Solver y estrategia temporal: reanudación de bases RANS

## 6.1 Comportamiento por defecto

La operación por defecto debe ser:

```text
Continuar desde la última ejecución disponible
```

No borrar ni reiniciar automáticamente una base existente.

## 6.2 Reanudar el paquete completo

Añadir:

```text
Continuar generación de las seis bases RANS
```

Lógica:

1. recorrer el orden canónico:
   - closed_coarse;
   - closed_medium;
   - closed_fine;
   - open_coarse;
   - open_medium;
   - open_fine;
2. omitir bases:
   - auto-convergidas;
   - aceptadas manualmente para el uso requerido;
   - finalizadas y pendientes de revisión si el usuario ha elegido no extender;
3. seleccionar la primera base incompleta;
4. prioridad:
   - ejecución `RUNNING/PARTIAL`;
   - menos de 10,000 iteraciones;
   - extensión interrumpida;
   - base configurada pero no ejecutada;
5. reanudar desde `latestTime`;
6. nunca copiar el checkpoint inicial sobre una solución parcial.

El texto visible debe indicar:

```text
Se continuará desde la primera base incompleta de la secuencia.
```

## 6.3 Reanudar un caso seleccionado

Añadir:

```text
Continuar base RANS seleccionada desde su última iteración
```

Debe:

- detectar la última iteración válida;
- comprobar campos;
- no repetir `potentialFoam`;
- mantener histories;
- añadir un nuevo bloque;
- registrar la relación con la ejecución anterior.

## 6.4 Eliminar y reiniciar

Añadir una acción avanzada:

```text
Eliminar base activa y comenzar de nuevo
```

No usar texto obligatorio de confirmación. Usar:

```text
checkbox: Confirmo que deseo eliminar la base activa del workspace
button: Eliminar y reiniciar
```

Antes de borrar:

- mostrar qué se elimina;
- no tocar Results;
- ofrecer archivar;
- preservar informe y logs si se selecciona archivado.

Default:

```text
archivar antes de eliminar
```

## 6.5 Iteraciones

Mantener:

```text
bloque inicial: 10000
extensión: 2500
máximo: configurable
```

Cambiar el valor por defecto de extensión de 5,000 a:

```text
2,500 iteraciones
```

Migrar configuraciones anteriores sin reescribir ejecuciones en curso.

---

# 7. Qué significa “preparar las bases”

La acción actual de preparar sin ejecutar significa:

- restaurar conjunto;
- validar malla;
- escribir diccionarios;
- verificar campos;
- crear manifiesto;
- calcular presupuesto;
- construir comandos;
- no ejecutar OpenFOAM.

Es útil para:

- tests;
- auditoría;
- verificar configuración;
- preparar una cola antes de ejecutarla;
- detectar errores sin gastar horas de solver.

No es necesaria como paso manual independiente para el usuario habitual.

## 7.1 Simplificación

La acción principal:

```text
Generar/continuar bases RANS
```

debe ejecutar internamente:

```text
preparar
-> validar
-> ejecutar
```

Mantener en `Opciones avanzadas`:

```text
Preparar y verificar sin ejecutar
```

Eliminar un botón principal redundante de “Preparar todas las bases” si no
añade funcionalidad distinta.

## 7.2 Tabla de cola simplificada

Mostrar:

| Orden | Malla | Estado | Iteraciones | Última ejecución | Acción |
|---:|---|---|---:|---|---|

Eliminar:

- checkpoint ID;
- mesh hash.

Mostrar checkpoint/hashes solo en detalles técnicos.

---

# 8. Análisis por ejecución: estructura simplificada

Eliminar la subsección:

```text
Todos
```

Mantener solo:

```text
RANS/SIMPLE
URANS/PIMPLE
Sensibilidad PIMPLE
```

## 8.1 Selección activa

Por defecto:

```text
Seguir ejecución activa = true
```

Al iniciar una ejecución:

- abrir `Análisis por ejecución`;
- seleccionar modo;
- seleccionar run;
- fijar monitor;
- actualizar al cambiar el run activo.

Si el usuario desactiva seguimiento:

- mantener el caso fijado;
- no cambiarlo con la cola.

## 8.2 Selector

RANS debe listar:

- ejecuciones activas;
- ejecuciones parciales;
- ejecuciones finalizadas;
- ejecuciones de batch;
- resultados almacenados en el laboratorio.

URANS debe listar únicamente casos transitorios.

PIMPLE debe listar únicamente estudios de correctores.

## 8.3 Problemas gráficos

Corregir:

- figuras vacías;
- ejes sin etiquetas;
- rangos fijados por spikes antiguos;
- escalas inconsistentes;
- títulos genéricos;
- selección que vuelve a un run incorrecto;
- reruns de Streamlit que pierden la selección;
- figuras excesivamente grandes.

Usar:

- percentiles robustos para display;
- botón `Ver rango completo`;
- persistencia de `selected_run_id`;
- cache con invalidación por mtime;
- dos monitores compactos.

---

# 9. RANS/SIMPLE dentro de Análisis por ejecución

Trasladar aquí el análisis individual y la aprobación de una base.

## 9.1 Resumen final

Mostrar una tabla:

### Configuración

| Parámetro | Valor |
|---|---:|
| Topología | |
| Nivel | |
| Celdas | |
| Iteraciones totales | |
| Bloques ejecutados | |
| Tiempo total | |
| Tiempo/iteración | |
| Modelo | |
| Correctores no ortogonales | |
| Estado automático | |
| Estado revisado | |

### Métricas de ventana final

| Métrica | Media | Desv. estándar | Drift | Cambio entre ventanas | Gate |
|---|---:|---:|---:|---:|---|
| Cl | | | | | |
| Cd | | | | | |
| Cm | | | | | |
| Cl/Cd | | | | | |

### Solver

| Campo | Residuo final | Mediana ventana | Pendiente log | Umbral | Gate |
|---|---:|---:|---:|---:|---|
| p | | | | | |
| U | | | | | |
| nuTilda | | | | | |
| continuidad | | | | | |

## 9.2 Diagnóstico

El diagnóstico debe:

- leer logs e historiales existentes;
- calcular ventanas;
- calcular gate;
- generar gráficas;
- no ejecutar solver;
- no cambiar estado;
- no crear datos falsos.

La salida principal debe ser una **tabla visual compacta**, no un JSON extenso.

El JSON detallado permanece:

- para backend;
- descarga;
- provenance;
- tests.

Renombrar botón:

```text
Analizar resultado RANS
```

Help:

```text
Calcula estadísticas, evalúa los criterios y genera las gráficas usando los
datos almacenados. No ejecuta OpenFOAM.
```

## 9.3 Aprobación

Eliminar la obligación de escribir un motivo.

Botones:

```text
Aprobar como estadísticamente estacionaria
Aprobar solo como base URANS
Extender 2500 iteraciones
Rechazar
Revocar aprobación
```

Usar:

- confirmación visual;
- nota opcional;
- fecha;
- usuario;
- hashes.

La aprobación debe funcionar tanto:

- inmediatamente después de una ejecución;
- sobre resultados almacenados de un batch;
- después de cerrar y abrir la aplicación.

No depender de un job activo.

## 9.4 Gráficas RANS

Mostrar:

1. residuales vs iteración, y logarítmico;
2. coeficientes vs iteración, con Cl/Cd;
3. medias móviles;
4. comparación de ventanas;
5. gate visual.

Eliminar:

```text
Coefficient value / SIMPLE iteration
```

si está vacía o duplica información.

Sustituirla por la gráfica de coeficientes con doble eje y `Cl/Cd`.

No mostrar continuidad como monitor principal. Puede aparecer en la tabla y en
un desplegable diagnóstico.



---

# 10. Gate RANS revisado

## 10.1 Problema

El residual de presión puede alcanzar un plateau y no cumplir el límite estricto,
aunque fuerzas, continuidad y otras variables ya no cambien de forma relevante.
Extender indefinidamente no mejora necesariamente la solución.

No debe implementarse una regla ciega:

```text
si cumple todas menos una -> convergente
```

porque el criterio fallido podría ser crítico.

## 10.2 Clasificar criterios

### Criterios duros

Nunca pueden ignorarse:

- NaN/Inf;
- fuerza runaway;
- continuidad creciente/no acotada;
- campos turbulentos no físicos;
- fallo del solver;
- drift grande de fuerzas;
- pérdida de masa importante;
- campos incompletos.

### Criterios de estacionariedad principales

- estabilidad de medias de Cl/Cd/Cm;
- drift;
- desviación relativa;
- comparación de ventanas;
- continuidad estable.

### Criterios residuales blandos

- residuo de p;
- residuo de U;
- residuo de nuTilda;

siempre que estén acotados y en plateau.

## 10.3 Estados automáticos

Añadir:

```text
RANS_AUTO_CONVERGED_STRICT
RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING
RANS_REVIEW_REQUIRED
RANS_DIVERGED
```

### Strict

Todos los criterios se cumplen.

### Plateau warning

Puede activarse solo si:

1. todos los criterios duros se cumplen;
2. todos los criterios de fuerzas/estacionariedad se cumplen;
3. falla exactamente un criterio residual blando;
4. el residual fallido está por debajo de un techo de seguridad;
5. no mejora de forma significativa entre extensiones;
6. la continuidad es estable;
7. dos ventanas finales son compatibles.

No cambiar el gate histórico de casos ya ejecutados sin recalcular y versionar
el diagnóstico.

## 10.4 Presión

No relajar el residual de p con un valor arbitrario global.

Implementar dos umbrales:

```text
p_residual_preferred_limit
p_residual_plateau_ceiling
```

Default:

```python
p_residual_plateau_ceiling = min(
    10 * p_residual_preferred_limit,
    1e-2
)
```

Ejemplo:

```text
preferred = 1e-4
plateau ceiling = 1e-3
```

El valor real debe derivarse de la configuración existente. Mostrar ambos en
la UI.

No aceptar plateau si el residual está por encima del ceiling.

## 10.5 Comparar extensiones

Después de 10,000 iteraciones, extender en bloques de 2,500.

Para cada bloque comparar con el anterior:

```text
residual median
log10 slope
force means
force drift
force standard deviation
continuity
```

Definir “mejora notable” mediante parámetros configurables:

```text
residual reduction >= 0.10 decade por bloque
o
median residual reduction >= 20%
o
mejora relevante en el criterio de fuerzas
```

Definir plateau si durante dos bloques consecutivos:

```text
residual improvement < threshold
force statistics remain inside tolerance
continuity remains stable
```

Resultado:

```text
NO_MEANINGFUL_EXTENSION_IMPROVEMENT
```

En ese momento:

- detener nuevas extensiones automáticas;
- clasificar strict/plateau/review;
- no consumir iteraciones indefinidamente.

## 10.6 Regla “todas menos una”

Implementarla solo así:

```text
exactamente un criterio residual blando falla
+ criterios duros PASS
+ fuerzas PASS
+ continuidad PASS
+ plateau demostrado
+ residual bajo ceiling
```

No aplicarla a:

- Cd plateau;
- drift;
- continuidad;
- boundedness;
- NaN;
- más de un residual fallido.

## 10.7 Aprobación manual

Aunque el algoritmo clasifique `REVIEW_REQUIRED`, el usuario puede aprobar:

```text
estadísticamente estacionaria
solo inicialización URANS
```

sin motivo obligatorio, pero con confirmación y nota opcional.

---

# 11. Convergencia espacial RANS

Esta sección deja de ser una revisión de casos individuales. Su objetivo es
comparar las tres mallas dentro de cada topología.

## 11.1 Organización

Tabs:

```text
Perfil cerrado
Perfil abierto
Comparación de topologías
```

La comparación de topologías no debe interpretarse como convergencia de una
misma geometría; sirve para ver coste y tendencias.

## 11.2 Casos elegibles

Incluir:

```text
RANS_AUTO_CONVERGED_STRICT
RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
```

Excluir:

```text
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
RANS_REVIEW_REQUIRED
RANS_REJECTED
```

Marcar visualmente la procedencia.

## 11.3 Métricas

### Malla

- cell count;
- effective \(h=N^{-1/2}\);
- ratio de refinamiento;
- no ortogonalidad;
- skewness;
- determinant;
- interpolation weight;
- volume ratio.

### Aerodinámica

- Cl medio;
- Cd medio;
- Cm medio;
- Cl/Cd medio;
- desviaciones;
- drift;
- diferencia relativa coarse-medium;
- diferencia relativa medium-fine;
- diferencia respecto a fine.

### Solver/coste

- iteraciones;
- tiempo total;
- segundos/iteración;
- tiempo normalizado por 10,000 iteraciones;
- memoria/almacenamiento;
- estado/gate.

### Superficie, cuando exista

- Cp;
- Cf;
- y+;
- wall shear;
- separación/reattachment;
- delta99.

No crear curvas vacías cuando un producto no exista.

## 11.4 Tablas

Tabla principal:

| Nivel | Celdas | Cl | ΔCl vs fine | Cd | ΔCd vs fine | Cm | Cl/Cd | Tiempo/10k | Estado |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|

Tabla de calidad:

| Nivel | Non-orth | Skew | Determinant | Interp. weight | Volume ratio |
|---|---:|---:|---:|---:|---:|

## 11.5 Gráficas

- Cl/Cd/Cm/ClCd vs cell count;
- métricas vs effective h;
- diferencias relativas;
- tiempo vs celdas;
- coste vs diferencia;
- Cp overlay;
- Cf overlay;
- y+ overlay;
- estado de aprobación.

Usar títulos descriptivos:

```text
Convergencia de Cl con el refinamiento — perfil cerrado
Diferencia relativa de Cd respecto a la malla fine — perfil abierto
Coste estacionario normalizado por 10,000 iteraciones
```

Ejes con unidades.

## 11.6 GCI

Mantener las salvaguardas actuales:

- no monotonicidad;
- ratios débiles;
- extrapolación mal condicionada;
- resultados no aceptados.

Mostrar por qué no se calcula.

---

# 12. Matriz de ejecuciones URANS

Moverla después de:

```text
Análisis RANS
Convergencia espacial RANS
```

La matriz debe usar los checkpoints aprobados.

## 12.1 Estado de preparación

Cada fila de malla muestra:

```text
Base RANS:
  auto-convergida
  plateau warning
  aceptada manualmente
  solo inicialización
  no disponible
```

Permitir URANS desde:

- auto-convergida;
- plateau warning;
- manual stationary;
- initialization-only.

No permitir desde review/rejected.

## 12.2 Navegación

Al pulsar una celda:

```text
Abrir análisis URANS de esta ejecución
```

debe llevar a:

```text
Análisis por ejecución -> URANS/PIMPLE
```

No duplicar postproceso individual dentro de la matriz.

## 12.3 Contenido

La matriz conserva:

- mesh;
- dt;
- pilot;
- status;
- acceptance;
- cost.

No mostrar identificadores técnicos largos.

---

# 13. Sensibilidad PIMPLE

Mantener la subsección dentro de:

```text
Análisis por ejecución -> Sensibilidad PIMPLE
```

No crear otra sección top-level duplicada.

Debe:

- seleccionar malla/caso;
- ejecutar 2/3/4;
- mostrar monitor;
- comparar;
- aprobar recomendación.

Por defecto puede seguir usando `closed_coarse`, siempre desde el mismo
checkpoint y `deltaT`.

---

# 14. Revisión y compatibilidad de bases: tabla única

Unificar las tablas anteriores en:

| Malla | Iteraciones | Gate automático | Revisión | Uso RANS | Uso URANS | Última actualización | Acción |
|---|---:|---|---|---|---|---|---|

No mostrar en principal:

- checkpoint ID;
- mesh hash;
- physics hash;
- rutas;
- JSON status interno.

Acciones:

```text
Analizar
Continuar
Aprobar
Eliminar/reiniciar
Ver detalles
```

Los detalles técnicos permanecen en un expander.

---

# 15. Compatibilidad de resultados almacenados

Las acciones de análisis/aprobación deben funcionar sobre:

- ejecución recién terminada;
- ejecución de batch;
- ejecución parcial;
- ejecución restaurada;
- ejecución creada antes de esta actualización.

Implementar migración de metadatos sin alterar datos.

No depender de `active_job_id`.

Resolver el caso por:

```text
run_id + registry + manifest
```

Si falta un informe actualizado:

```text
Analizar resultado RANS
```

lo genera desde datos existentes.

---

# 16. Configuración efectiva

Mantener el snapshot congelado de batch:

```text
resolved_batch_config.json
```

Al reanudar:

- usar la configuración original por defecto;
- mostrarla;
- no mezclar nuevos widgets;
- ofrecer `Crear nueva revisión` para cambiar parámetros.

Verificar después de escribir:

- `fvSolution`;
- `fvSchemes`;
- `controlDict`.

Mostrar una tabla compacta de valores efectivos.



---

# 17. Modelo de estados revisado

Usar:

```text
RANS_NOT_STARTED
RANS_PREPARED
RANS_RUNNING
RANS_PARTIAL
RANS_AUTO_CONVERGED_STRICT
RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING
RANS_REVIEW_REQUIRED
RANS_USER_ACCEPTED_STATISTICALLY_STEADY
RANS_USER_ACCEPTED_FOR_INITIALIZATION_ONLY
RANS_REJECTED
RANS_DELETED_FROM_ACTIVE_WORKSPACE
```

No reutilizar `CONVERGED` para estados diferentes.

## 17.1 Separar estado de ejecución y revisión

```json
{
  "execution_status": "COMPLETED",
  "automatic_gate_status": "RANS_REVIEW_REQUIRED",
  "review_status": "NOT_REVIEWED",
  "allowed_uses": {
    "rans_spatial_convergence": false,
    "urans_initialization": false
  }
}
```

La aprobación manual cambia `review_status` y `allowed_uses`, no el gate
automático.

## 17.2 Sin motivo obligatorio

Eliminar el campo de texto obligatorio.

Guardar:

```json
{
  "review_note": null,
  "confirmation": true,
  "reviewed_at": "",
  "reviewed_by": "user"
}
```

La nota puede ser opcional.

---

# 18. Diagnóstico y resumen visual

## 18.1 Salida principal

La UI debe priorizar:

- tabla de configuración;
- tabla de estadísticas;
- tabla del gate;
- dos gráficas principales;
- recomendación;
- acciones.

No mostrar un JSON completo por defecto.

## 18.2 JSON

Mantener:

```text
rans_diagnostic.json
```

para:

- reproducibilidad;
- tests;
- exportación;
- auditoría.

Añadir:

```text
Descargar diagnóstico técnico
```

## 18.3 Recomendación

El diagnóstico puede devolver:

```text
AUTO_CONVERGED_STRICT
AUTO_CONVERGED_WITH_PLATEAU_WARNING
REVIEW_RECOMMENDED
EXTENSION_RECOMMENDED
REJECT_RECOMMENDED
```

No debe aprobar automáticamente en nombre del usuario cuando el estado
requiere revisión.

---

# 19. Correcciones de títulos, escalas y ejes

Auditar todas las figuras del laboratorio.

Cada figura debe tener:

- título descriptivo;
- etiqueta x;
- etiqueta y;
- unidades;
- leyenda;
- fuente del caso;
- ventana analizada;
- topología/malla.

## 19.1 Residuales

```text
y log
x iteración para SIMPLE
x tiempo físico/paso para URANS
```

## 19.2 Coeficientes

```text
Cl, Cd, Cm [-]
Cl/Cd [-]
```

No usar:

```text
Coefficient value
Simple iteration
```

como títulos genéricos sin contexto.

## 19.3 Coste

```text
Tiempo de pared [s]
Tiempo por iteración [s/iteración]
Tiempo normalizado [h/10,000 iteraciones]
```

## 19.4 Diferencias

```text
Diferencia relativa [%]
```

Evitar mezclar porcentaje y fracción.

## 19.5 Rangos

No usar límites rígidos que oculten resultados.

Default:

- percentiles robustos;
- margen;
- botón de rango completo;
- warning de valores fuera del rango visible.

Los CSV preservan todos los valores.

---

# 20. Cambios en el gate de presión: implementación exacta

Codex debe localizar los umbrales actuales antes de cambiarlos.

Añadir configuración:

```json
{
  "rans_convergence": {
    "extension_iterations": 2500,
    "pressure_residual_preferred_limit": null,
    "pressure_residual_plateau_multiplier": 10.0,
    "pressure_residual_absolute_ceiling": 0.01,
    "plateau_log_decade_improvement_min": 0.10,
    "plateau_relative_improvement_min": 0.20,
    "consecutive_plateau_blocks": 2,
    "allow_single_soft_failure": true
  }
}
```

Si `preferred_limit` es `null`, usar el valor estricto actual.

Calcular:

```python
plateau_ceiling = min(
    preferred_limit * plateau_multiplier,
    absolute_ceiling,
)
```

## 20.1 Pendiente logarítmica

Para residuos positivos:

```python
log_r = log10(residual)
slope = robust_linear_slope(iteration, log_r)
```

Usar regresión robusta o mediana de pendientes; no una diferencia entre dos
muestras ruidosas.

## 20.2 Bloques

Guardar:

```text
block_start
block_end
median residual
final residual
log slope
force mean
force std
force drift
continuity
```

## 20.3 Decisión

Pseudocódigo:

```python
if any(hard_fail):
    status = RANS_REVIEW_REQUIRED or RANS_DIVERGED
elif all(strict_criteria):
    status = RANS_AUTO_CONVERGED_STRICT
elif (
    exactly_one_soft_residual_fails
    and force_stationarity_passes
    and continuity_passes
    and residual_below_plateau_ceiling
    and plateau_detected_for_required_blocks
):
    status = RANS_AUTO_CONVERGED_WITH_PLATEAU_WARNING
else:
    status = RANS_REVIEW_REQUIRED
```

## 20.4 Evitar extensiones inútiles

Si:

```text
dos bloques consecutivos sin mejora notable
+ fuerzas estables
+ continuidad estable
```

no seguir extendiendo automáticamente.

Mostrar:

```text
La solución ha alcanzado un plateau numérico. Revise el resultado o acepte con advertencia.
```

No ejecutar hasta el máximo solo porque un residual no alcanza un límite
inalcanzable.

---

# 21. Reanudación y borrado: detalles de seguridad

## 21.1 Reanudación

Verificar:

- último tiempo/iteración;
- campos requeridos;
- integridad;
- mesh hash;
- config hash original;
- no existe job activo.

Actualizar:

```text
parent_run_id
resume_from_iteration
resume_block_index
```

## 21.2 Borrado activo

La acción explícita puede eliminar solo:

- base activa del laboratorio;
- checkpoint activo;
- postproceso derivado activo.

No eliminar:

- Results;
- simulaciones publicadas;
- archivos archivados;
- malla.

Default:

```text
archivar antes de eliminar
```

## 21.3 Nueva ejecución

Después de eliminar:

- estado `RANS_NOT_STARTED`;
- nuevo run ID;
- no reutilizar aprobación anterior;
- conservar provenance archivado.

---

# 22. Backend y UI

Inspeccionar y reutilizar:

```text
CFD_2D/app/ramair_cfd2d_app.py
CFD_2D/app/workflow_backend.py
CFD_2D/scripts/ramair_2d_validation_study.py
CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py
CFD_2D/scripts/ramair_2d_postprocess.py
monitor/parsers existentes
```

No duplicar parsers.

Si cambia el contrato backend:

- incrementar API en UI/backend;
- migrar schema del laboratorio;
- mantener schema 13 general.

Posibles módulos nuevos solo si no existe responsabilidad equivalente:

```text
ramair_2d_rans_plateau_gate.py
ramair_2d_rans_resume_manager.py
ramair_2d_validation_plotting.py
```

---

# 23. Migración

## 23.1 Mallas

Eliminar solo controles/UI de rediseño. No borrar historial.

## 23.2 RANS

Migrar resultados existentes:

- mantener gate;
- crear resumen visual;
- hacer aprobaciones accesibles;
- convertir extensiones futuras a 2,500;
- no cambiar bloques ya ejecutados.

## 23.3 Navegación

Migrar `selected_mode="all"` a:

```text
RANS
```

si el run activo es SIMPLE, o:

```text
URANS
```

si es transitorio.

## 23.4 Motivo

El campo antiguo obligatorio queda:

- preservado si existe;
- opcional en el futuro;
- no requerido para aprobar/revocar.

---

# 24. Tests obligatorios

## 24.1 Monitores

- continuidad no aparece como plot principal;
- datos de continuidad siguen disponibles;
- residual y log;
- ejes/títulos;
- Cl/Cd;
- división segura;
- vista compacta;
- tamaño reducido;
- active run pinned.

## 24.2 Secciones

- no existe “Todos”;
- no existe candidata/rediseño;
- Mallas y condiciones unificadas;
- orden correcto;
- matriz después de RANS spatial.

## 24.3 Reanudación

- batch desde primera incompleta;
- caso manual desde latest;
- bases completas omitidas;
- review pending no se borra;
- configuración original;
- borrar solo con confirmación;
- Results intacto.

## 24.4 Preparación

- ejecución principal prepara automáticamente;
- advanced preflight existe;
- no solver en preflight.

## 24.5 RANS review

- análisis sin solver;
- tabla visual;
- no motivo obligatorio;
- stored batch approval;
- recent approval;
- revoke;
- initialization-only excluded from spatial;
- manual stationary included and marked.

## 24.6 Gate

- strict;
- plateau;
- hard fail;
- exact one soft failure;
- two soft failures not auto-accepted;
- p ceiling;
- 2,500 extension;
- comparison of blocks;
- stop useless extensions.

## 24.7 RANS spatial

- open/closed tabs;
- metrics;
- relative differences;
- costs;
- missing products explicit;
- GCI guards.

## 24.8 Configuration

- controls frozen;
- dictionaries audited;
- resume uses original config;
- new revision required for changes.

Ejecutar:

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

No ejecutar OpenFOAM real en tests normales.

---

# 25. Verificación visual obligatoria

Después de implementar:

1. abrir el laboratorio;
2. seleccionar una base RANS almacenada;
3. comprobar tablas;
4. comprobar log residual;
5. comprobar Cl/Cd;
6. aprobar/revocar en un fixture o copia;
7. reabrir app y comprobar persistencia;
8. comprobar cola y resume;
9. comprobar mallas actuales;
10. comprobar orden de secciones;
11. comprobar que no existen subsecciones eliminadas;
12. comprobar que la matriz abre URANS en el análisis por ejecución.

No usar datos sintéticos sin etiquetarlos como fixture.

---

# 26. Documentación

Actualizar:

- `CHANGELOG.md`;
- `PROJECT_CONTEXT_FOR_CODEX.md`;
- `README_PROJECT_STRUCTURE.md`;
- README del laboratorio;
- ayuda de UI;
- migración.

Documentar:

- mallas definitivas;
- gate plateau;
- extensión 2,500;
- reanudación;
- aprobación sin motivo obligatorio;
- continuidad almacenada pero no dibujada;
- nueva estructura.

---

# 27. Criterios de finalización

No declarar completo hasta que:

1. solo se usen las mallas actuales;
2. la UI esté simplificada;
3. los monitores tengan escalas/ejes correctos;
4. Cl/Cd aparezca;
5. continuidad no aparezca como plot;
6. RANS/URANS/PIMPLE estén separados;
7. se puedan analizar resultados almacenados;
8. aprobación funcione sin texto obligatorio;
9. reanudación de batch/caso funcione;
10. eliminación sea explícita y segura;
11. extensión sea 2,500;
12. el gate de plateau esté probado;
13. RANS spatial compare tres mallas;
14. la matriz esté después;
15. configuración aplicada esté auditada;
16. tests pasen;
17. datos históricos permanezcan intactos.

---

# 28. Restricciones

- No regenerar mallas abiertas.
- No borrar candidatos archivados.
- No convertir un fail duro en convergencia.
- No aceptar automáticamente más de un criterio fallido.
- No ignorar continuidad.
- No confundir iteración SIMPLE con tiempo.
- No reiniciar por defecto.
- No cambiar configuración en una cola activa.
- No exigir motivo escrito.
- No mostrar JSON técnico como salida principal.
- No duplicar análisis individual en RANS spatial.
- No ejecutar CATIA.
- No ejecutar campañas CFD reales sin acción explícita.
