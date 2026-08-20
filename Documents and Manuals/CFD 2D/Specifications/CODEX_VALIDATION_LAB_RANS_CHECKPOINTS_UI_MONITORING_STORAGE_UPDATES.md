# INSTRUCCIONES PARA CODEX
## Correcciones y ampliaciones del Validation & Convergence Lab

**Proyecto:** RamAir DESIGN APP  
**Backend de referencia:** API 15  
**Configuración de solver:** schema 13  
**Workspace:** `CFD_2D/validation_studies/closed_open_M0p15_Re1p9e6_alpha8`

Este documento amplía la implementación previa del laboratorio. Antes de editar, Codex debe leer `PROJECT_CONTEXT_FOR_CODEX.md`, `README_PROJECT_STRUCTURE.md`, `AGENTS.md`, `CHANGELOG.md`, el documento previo del laboratorio y los scripts reales de UI, backend, writer, staged runner, monitor, postproceso, Courant y Results.

---

# 1. Decisiones técnicas que deben explicarse en la UI

## 1.1 Correctores no ortogonales

`nNonOrthogonalCorrectors` repite la ecuación de presión para actualizar la parte explícita de la corrección no ortogonal del laplaciano. No existe una regla universal que obligue a usar 1 en todo transitorio; su selección depende de la malla.

Mantener configuraciones independientes:

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

Razón:

- SIMPLE usa actualmente el perfil robusto y disipativo `robust_sa_initialization_v2`; cero reduce el coste de hasta decenas de miles de iteraciones.
- Las seis mallas tienen máximos próximos a 38–42 grados, por lo que una corrección adicional en PIMPLE es una baseline prudente para presión, fuerzas y frecuencias.
- Añadir una sensibilidad avanzada 0 frente a 1 en una malla media, sin mezclarla con el estudio principal de `nOuterCorrectors`.

Etiquetas:

- `RANS/SIMPLE — Correcciones no ortogonales`
- `URANS/PIMPLE — Correcciones no ortogonales`

No usar un solo widget para ambos.

## 1.2 “Tripleta atómica”

Significa restaurar como una única operación coherente:

1. geometría;
2. condiciones CFD;
3. malla.

Evita combinaciones incompatibles de topología, cuerda, Mach/Reynolds, patches o hashes. La operación debe completarse entera o cancelarse sin cambios parciales.

Renombrar:

```text
Conjunto coherente de simulación
Geometría + condiciones de operación + malla
```

Botón:

```text
Cargar conjunto seleccionado en el laboratorio
```

Help:

```text
Restaura conjuntamente geometría, condiciones y malla del mismo paquete.
Cancela toda la operación si una parte no coincide. No modifica el workspace general.
```

Añadir desplegable `Ver contenido del conjunto` con nombres de paquetes, hashes, celdas y `checkMesh`.

## 1.3 Pilot

Renombrar `Pilot` como:

```text
Prueba corta de viabilidad numérica
```

No es validación ni producción. Comprueba:

- checkpoint compatible;
- transferencia de `U`, `p`, `nuTilda`, `phi`, `nut` y campos requeridos;
- arranque de OpenFOAM;
- ausencia de NaN/divergencia inmediata;
- convergencia PIMPLE inicial;
- viabilidad preliminar del `deltaT`;
- coste aproximado por paso;
- monitor y almacenamiento.

Renombrar:

- `Pilot steps` → `Pasos de la prueba corta`
- `Pilot seed source` → `Origen del deltaT inicial de la prueba`

Fuentes:

1. `Último deltaT estable medido para esta malla`
2. `Recomendación del diagnóstico de Courant`
3. `Referencia del artículo`
4. `Valor introducido por el usuario`

Mostrar siempre el valor, origen y advertencias antes de ejecutar.

## 1.4 Presets temporales

### Referencia del artículo

```text
deltaT=2.5e-4 s y refinamientos por mitades
```

Procede de la baseline LS(1)-0417; sirve como objetivo científico, no garantiza viabilidad en OpenFOAM.

### Escalera basada en un deltaT estable medido

A partir de una ejecución real:

```text
2x, 1x, 0.5x, 0.25x
```

Registra los valores que fallen.

### Resolución espectral objetivo

Calcula:

\[
\Delta t^* \leq 1/(St_{max}N_{ciclo})
\]

`St_max=20` debe mostrarse como screening conservador, no frecuencia universal Ram-Air.

### Personalizado

Lista manual validada.

Cada preset debe mostrar `deltaT`, `deltaT*`, pasos, Nyquist, muestras/ciclo, coste y estado: objetivo científico, pilot aprobado o caso aceptado.

## 1.5 Tiempo mediano del equipo

Renombrar:

```text
Tiempo mediano medido por paso en este equipo [s/paso]
```

El valor histórico de 2.48 s corresponde a una medición concreta del Ryzen 7 4800H y no debe presentarse como universal.

Después de cada prueba corta:

1. excluir primer paso, descomposición, reconstrucción, escritura y cambios de etapa;
2. usar pasos estables finales;
3. guardar mediana, p25, p75, media y desviación;
4. indexar por hash de malla, celdas, topología, ranks, versión, esquema y correctores.

Prioridad:

1. pilot actual de esa malla;
2. ejecución previa con mismo hash;
3. regresión dentro del estudio;
4. benchmark del host;
5. estimación por celdas con warning.

Mostrar la fuente de la estimación. No escalar solo linealmente por celdas.

## 1.6 Dry-run

Significa:

```text
Preparar y verificar sin ejecutar OpenFOAM
```

Valida rutas, `polyMesh`, campos, diccionarios, comandos, presupuesto y manifiestos.

Mantenerlo internamente para tests y seguridad, pero sustituir el control técnico visible por dos acciones:

```text
Preparar y verificar el caso
Ejecutar la simulación
```

La ejecución real requiere confirmación y pasa `--run`. El backend sin `--run` sigue siendo seguro.

---

# 2. Estados base RANS/SIMPLE

## 2.1 Decisión

Implementar seis checkpoints, uno por malla:

```text
closed_coarse
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

Todos los `deltaT` de una malla parten del mismo checkpoint. No usar un único checkpoint entre mallas ni interpolar como baseline del estudio espacial.

## 2.2 Nueva subsección

En `Solver & Temporal Strategy` crear:

```text
Estados base RANS/SIMPLE
```

Tabla:

| Topología | Nivel | Estado | Iteraciones | Gate | Checkpoint | Hash |
|---|---|---|---:|---|---|---|

Botones:

```text
Generar las seis bases RANS
Generar solo la base seleccionada
Extender bases no convergidas
Ver diagnóstico
Abrir postproceso RANS seleccionado
```

## 2.3 Secuencia automática

Por malla:

1. restaurar conjunto coherente;
2. validar `polyMesh`;
3. escribir SIMPLE;
4. ejecutar `potentialFoam`;
5. ejecutar bloque SIMPLE;
6. evaluar gate;
7. si converge, guardar checkpoint y continuar;
8. si no, extender 5,000;
9. repetir hasta máximo;
10. si no converge, marcar y continuar sin crear checkpoint de validación.

Defaults:

```text
Bloque inicial:       10,000 iteraciones
Extensión:             5,000
Máximo:               30,000
```

El modo inicial ejecuta el bloque completo de 10,000 para obtener una referencia de coste homogénea. Añadir una opción avanzada de parada temprana, desactivada inicialmente.

## 2.4 Gate

No usar solo residuos. Evaluar:

- residuos de `U`, `p`, `nuTilda`;
- continuidad;
- ausencia de NaN;
- medias de \(C_L,C_D,C_M\) en dos ventanas finales;
- cambio porcentual;
- drift;
- desviación relativa;
- límites de fuerzas.

Usar el gate existente del staged runner.

## 2.5 Perfil abierto

Puede no establecer un estado SIMPLE físico por la entrada abierta.

Si es acotado pero no convergido:

```text
STEADY_RANS_NOT_ESTABLISHED
BOUNDED_STEADY_INITIALIZATION_AVAILABLE
```

No usarlo para convergencia espacial RANS. Permitir con confirmación:

```text
Usar estado acotado como inicialización diagnóstica URANS
```

Guardar como `DIAGNOSTIC_TRANSFER_CHECKPOINT`, nunca como checkpoint validado.

## 2.6 Ventana y coste RANS

Usar:

```text
final_window = max(1000 iteraciones, último 10%)
```

Guardar:

```text
wall_time_to_10000_iterations
normalized_wall_time_per_10000_iterations
median_solver_seconds_per_iteration
extension_wall_time
total_wall_time
```

## 2.7 Contenido del checkpoint

Guardar compactamente:

- `U`
- `p`
- `nuTilda`
- `phi` si existe
- `nut`, `alphat` y campos descubiertos
- `constant/`
- provenance de `system/`
- mesh/physics/config hashes
- gate
- fuerzas, residuos y logs

Verificar SHA-256 normalizado.

---

# 3. Corregir el error de checkpoint

El error actual:

```text
common SIMPLE checkpoint closed_coarse_simple is not established
```

debe convertirse en:

```text
BLOCKED_MISSING_RANS_CHECKPOINT
```

Mensaje:

```text
No existe un estado base RANS compatible para closed_coarse.
Genérelo antes de iniciar la prueba corta o URANS.
```

Acciones:

```text
Generar esta base RANS
Generar las seis bases RANS
Ver requisitos
```

Deshabilitar prueba corta, URANS y estudio PIMPLE hasta disponer de checkpoint compatible.

Compatibilidad:

- mesh hash;
- topología;
- Mach/Re/c/alpha;
- modelo turbulento;
- condiciones de contorno;
- campos;
- configuración.

Estados:

```text
CHECKPOINT_STALE_MESH_CHANGED
CHECKPOINT_STALE_PHYSICS_CHANGED
```

No mostrar traceback al usuario.


---

# 4. Opciones RANS visibles

Crear:

```text
Ajustes de los estados base RANS/SIMPLE
```

Controles:

- `potentialFoam`;
- bloque inicial, extensión y máximo;
- parada temprana;
- tolerancias residuales;
- ventanas y estabilidad de fuerzas;
- solvers lineales;
- esquemas de inicialización;
- relajación de `p`, `U`, `nuTilda`;
- correctores no ortogonales;
- ranks;
- timeout;
- almacenamiento;
- política ante no convergencia.

Separar de `Ajustes URANS/PIMPLE`.

---

# 5. Monitorización ligera

## 5.1 Título obligatorio

RANS:

```text
Closed | Coarse | 203,691 cells | RANS/SIMPLE | Iteration 4,250
```

URANS:

```text
Open | Medium | URANS/PIMPLE | dt=2.5e-5 s | Stage D — Settling
```

Incluir run ID. No mostrar `deltaT` físico en SIMPLE.

## 5.2 Panel RANS

- residuos;
- Cl;
- Cd/Cm;
- gate;
- iteración;
- elapsed;
- estimación a 10,000;
- continuidad;
- bloque.

## 5.3 Panel URANS

- residuos;
- Cl;
- Cd/Cm;
- Courant;
- continuidad;
- `deltaT`;
- fase A–E;
- tiempo físico/convectivo;
- pasos;
- elapsed/remaining.

## 5.4 Refresco

Default:

```text
30 s
```

Opciones: 15/30/60 s.

Usar parsing incremental, cache por posición, ventana reciente y downsampling solo visual. Conservar datos crudos. No leer campos ni ejecutar ParaView en monitor.

---

# 6. Almacenamiento

## 6.1 Inventario

Generar:

```text
storage_inventory.json
storage_inventory.csv
```

con bytes por carpeta, top archivos, snapshots, VTK, animaciones y recomendaciones.

## 6.2 RANS compacto

Perfil:

```text
steady_checkpoint_compact
```

- conservar `0/`;
- último estado completo;
- un estado previo de recuperación;
- `purgeWrite=2`;
- fuerzas/residuos cada iteración;
- sin VTK, animación ni secuencia de imágenes;
- screenshot final solo bajo petición;
- reconstruir solo lo necesario;
- preservar todos los campos para URANS.

Postproceso:

```text
Generar postproceso RANS breve para el caso seleccionado
```

Produce Cp, presión, velocidad, streamlines, y+, wall shear y una captura final.

## 6.3 URANS compacto

Perfil:

```text
transient_convergence_compact
```

Conservar cada paso:

- fuerzas;
- sondas;
- residuos;
- continuidad;
- estadísticas Co;
- historial de fase;
- `deltaT`.

Campos volumétricos con intervalo configurable y `purgeWrite`.

Presets:

```text
Compact:     10–20 estados
Analysis:    30–50
Publication: manual con confirmación
```

No VTK duplicado ni animaciones automáticas. Generarlas solo para el caso seleccionado.

---

# 7. Malla abierta más ligera

Crear una candidata sin sobrescribir:

```text
open_medium_light_candidate
```

Objetivo prudente:

```text
280,000–295,000 celdas
```

Mantener:

- `open_coarse < candidate < open_fine`;
- topología zero-thickness;
- y1;
- BL;
- labios;
- inlet strip 0.0035c;
- primera transición de cavidad;
- near wake;
- TE.

Reducir solo:

- cavity core;
- farfield;
- transición exterior lejana;
- regiones interiores cuasi-estáticas.

Sweep de multiplicadores de tamaño no crítico:

```text
1.10, 1.20, 1.30
```

No cambiar todos los parámetros simultáneamente.

Criterios mínimos:

```text
checkMesh OK
max non-orthogonality: no degradación importante
max skewness <= 0.69
min determinant >= 0.05
min interpolation weight >= 0.09
min volume ratio >= 0.13
sin nuevas poblaciones problemáticas cerca del inlet
reducción >= 3%
```

Comparar histogramas. Si queda por debajo de coarse, guardarla como `open_runtime_light`, no como medium.

---

# 8. Botón Gmsh

En Mesh Registry:

```text
Abrir malla seleccionada en Gmsh
```

- resolver `mesh_final.msh` desde manifiesto;
- verificar identidad;
- lanzar Gmsh 4.15.2 por WSLg;
- no regenerar ni modificar;
- registrar proceso;
- cerrarlo al cerrar app;
- error explícito si falta `.msh`;
- no intentar abrir `polyMesh` como Gmsh.

Help:

```text
Abre la malla guardada para inspección. No regenera ni modifica el paquete.
```

---

# 9. Estudio PIMPLE 2–3–4

Convertir la sección conceptual existente en ejecutable.

Default:

```text
topología: closed
malla: medium
deltaT: provisional aceptado, no el mínimo
nOuterCorrectors: 2, 3, 4
nCorrectors: constante
nNonOrthogonalCorrectors: 1
mismo checkpoint y duración
```

Duración breve:

```text
settling: 5–10 tc
sampling: 20–30 tc
```

Botón:

```text
Ejecutar estudio 2–3–4 y generar comparación
```

Analizar:

- reducción residual;
- continuidad;
- medias/RMS;
- St;
- PSD;
- CPU/paso;
- coste/segundo físico;
- error respecto a 4.

Recomendar el menor equivalente al siguiente. Mantener 3 por defecto hasta completar el estudio.

---

# 10. Menús simplificados

Flujo:

```text
1. Seleccionar conjunto
2. Generar/verificar base RANS
3. Ejecutar prueba corta
4. Ejecutar URANS y analizar
```

Acciones:

```text
Preparar y verificar
Generar base RANS
Ejecutar prueba corta
Ejecutar URANS
Reanudar
Detener y escribir estado
```

No mostrar flags CLI, hashes ni “dry-run” como controles primarios. Mantenerlos en avanzado.

---

# 11. Cola de seis bases

Orden default:

```text
closed_coarse
closed_medium
closed_fine
open_coarse
open_medium
open_fine
```

- converge → guardar y continuar;
- no converge → extender 5,000;
- alcanza máximo → marcar y continuar;
- diverge → archivar y continuar solo si se activó `continue_on_nonfatal_failure`;
- no transferir casos no convergidos;
- estado atómico reanudable.

---

# 12. Prueba real parcial requerida

Tras implementar y pasar tests, realizar una prueba real acotada porque el usuario la solicita.

Caso preferido:

```text
closed_coarse
```

Parte estacionaria:

- `potentialFoam`;
- SIMPLE;
- 8 ranks;
- presupuesto corto;
- monitor;
- almacenamiento compacto;
- conservar parcial.

No es necesario completar 10,000 en el smoke test.

Si converge, transferencia normal. Si no converge y es imprescindible comprobar el arranque URANS, usar una copia desechable y el override diagnóstico existente, marcando:

```text
DIAGNOSTIC_SMOKE_TRANSFER
```

Nunca publicarlo como validación.

Parte URANS:

- iniciar A, o A–C si el presupuesto lo permite;
- 20–100 pasos;
- `deltaT` prudente;
- monitor;
- `writeNow`;
- reconstruir tiempos retenidos.

Informe:

```text
CFD_2D/reports/VALIDATION_LAB_RANS_URANS_SMOKE_TEST_<date>.md
```

Incluir comandos, versiones, hashes, pasos, residuos, continuidad, límites de fuerzas, almacenamiento, monitor, transferencia y errores.

Si Codex no dispone del runtime real, no inventar resultados: indicar que solo se verificaron tests/dry-run.

---

# 13. Estados y manifiesto

Estados:

```text
RANS_BASE_NOT_CREATED
RANS_BASE_RUNNING
RANS_BASE_EXTENDING
RANS_BASE_CONVERGED
RANS_BASE_BOUNDED_NOT_CONVERGED
RANS_BASE_DIVERGED
RANS_BASE_FAILED
CHECKPOINT_READY
DIAGNOSTIC_CHECKPOINT
CHECKPOINT_STALE_MESH_CHANGED
CHECKPOINT_STALE_PHYSICS_CHANGED
BLOCKED_MISSING_RANS_CHECKPOINT
```

Manifiesto:

```json
{
  "checkpoint_id": "",
  "topology": "",
  "mesh_level": "",
  "mesh_hash": "",
  "physics_hash": "",
  "solver_config_hash": "",
  "iterations_completed": 0,
  "initial_block": 10000,
  "extension_block": 5000,
  "max_iterations": 30000,
  "converged": false,
  "bounded": false,
  "gate": {},
  "final_window": {},
  "wall_time_to_10000_s": null,
  "required_fields": [],
  "field_hashes": {},
  "status": ""
}
```


---

# 14. Backend, configuración y módulos

## 14.1 Inspección obligatoria

Reutilizar antes de crear lógica nueva:

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
```

Posibles módulos nuevos solo cuando la responsabilidad no exista:

```text
ramair_2d_rans_checkpoint_batch.py
ramair_2d_validation_live_monitor.py
ramair_2d_storage_inventory.py
ramair_2d_pimple_outer_study.py
```

No duplicar parsers de fuerzas, residuales, Courant o Results.

## 14.2 Configuración

Añadir o ampliar el bloque aislado del laboratorio sin cambiar la política general:

```json
{
  "validation_study": {
    "rans_base_states": {
      "enabled": true,
      "initial_iterations": 10000,
      "extension_iterations": 5000,
      "maximum_iterations": 30000,
      "allow_early_stop": false,
      "continue_queue_after_nonconvergence": true,
      "simple_non_orthogonal_correctors": 0,
      "storage_profile": "steady_checkpoint_compact"
    },
    "urans": {
      "pimple_outer_correctors": 3,
      "pimple_correctors": 2,
      "pimple_non_orthogonal_correctors": 1,
      "storage_profile": "transient_convergence_compact",
      "monitor_refresh_seconds": 30
    },
    "pilot": {
      "ui_name": "short_numerical_feasibility_test",
      "steps": 200,
      "seed_source": "last_stable_for_same_mesh"
    }
  }
}
```

Versionar la migración si cambia el schema. Mantener lectura de paquetes anteriores.

## 14.3 API

Si se añaden acciones backend:

- incrementar conjuntamente `BACKEND_API_VERSION` y `EXPECTED_BACKEND_API_VERSION`;
- mantener errores estructurados;
- no devolver tracebacks crudos a Streamlit;
- usar status y remediation actions.

---

# 15. Postproceso RANS del laboratorio

Crear una vista breve por caso seleccionado:

```text
RANS case summary
```

Productos:

- historial de coeficientes;
- residuos;
- medias de ventana final;
- drift;
- gate;
- Cp final;
- presión final;
- velocidad final;
- y+;
- wall shear;
- una captura final.

No generar automáticamente productos volumétricos para los seis casos después de la cola.

Botón:

```text
Generar postproceso RANS breve para el caso seleccionado
```

El análisis de convergencia espacial RANS debe usar la ventana final y diferenciar:

```text
RANS_CONVERGED
RANS_BOUNDED_NOT_CONVERGED
RANS_NOT_AVAILABLE
```

Los dos últimos no se incluyen como puntos válidos de una extrapolación espacial.

---

# 16. Validación de almacenamiento

Añadir una auditoría al finalizar cada fase.

## 16.1 RANS

Comprobar que no se acumulan:

- múltiples tiempos SIMPLE reconstruidos;
- secuencias PNG;
- VTK;
- GIF/MP4;
- duplicados de `processorN`;
- capturas automáticas por cada base.

Conservar solo lo necesario para:

- reinicio;
- transferencia;
- diagnóstico escalar;
- postproceso final bajo demanda.

## 16.2 URANS

Comprobar:

- número de tiempos retenidos;
- tamaño por snapshot;
- `purgeWrite`;
- processor directories;
- reconstrucción;
- imágenes;
- animaciones;
- VTK.

Mostrar en la UI:

```text
Espacio usado
Espacio estimado restante
Número de estados retenidos
Historiales escalares conservados
```

Añadir botón explícito:

```text
Limpiar productos volumétricos activos
```

Debe:

- afectar solo al workspace activo;
- preservar `0/`, `constant/`, `system/`, último estado y escalares;
- no tocar Results;
- requerir confirmación.

---

# 17. Criterios para aceptar la candidata abierta ligera

No basta con `checkMesh`.

Debe pasar:

1. calidad geométrica;
2. mismo dominio;
3. misma geometría física;
4. mismo y1 y estrategia de BL;
5. misma resolución de labios/inlet/near wake;
6. comparación RANS o smoke solver;
7. ausencia de nueva concentración de Co;
8. cell count dentro del orden de refinamiento;
9. provenance completo.

Comparaciones mínimas con `open_medium`:

- Cl/Cd/Cm de estado RANS si se establece;
- Cp;
- y+;
- residual history;
- Courant hotspot en smoke test;
- coste por paso;
- calidad y histogramas.

No reemplazar el package activo automáticamente. Ofrecer:

```text
Promover candidata a open_medium del estudio
```

solo después de confirmación y archivado de la anterior.

---

# 18. Análisis aislado de `nOuterCorrectors`

Generar run IDs diferenciados:

```text
closed_medium_a08_<dt>_pimple2_backward
closed_medium_a08_<dt>_pimple3_backward
closed_medium_a08_<dt>_pimple4_backward
```

Mismo:

- mesh hash;
- checkpoint hash;
- física;
- `deltaT`;
- tiempo inicial;
- duración;
- esquemas;
- MPI;
- storage profile.

El informe debe distinguir:

```text
iterative convergence improvement
physical solution change
computational cost increase
```

No interpretar una reducción de residual como cambio físico favorable si fuerzas y PSD ya son equivalentes.

Criterio de recomendación inicial:

- diferencia de medias < tolerancias del laboratorio;
- RMS < 5%;
- frecuencia dominante < 2%;
- continuidad estable;
- PIMPLE interno suficiente.

Si 2 y 3 coinciden y 2 converge por paso, recomendar 2 por coste. Si 2 cambia el resultado o deja residual alto, mantener 3. Usar 4 como referencia de sensibilidad, no baseline permanente.

---

# 19. Mensajes y ayudas

Añadir tooltips o `st.info` compactos.

## Estado base

```text
Cada malla necesita su propia solución RANS/SIMPLE. Todos los pasos temporales
de esa malla partirán de la misma base para que las comparaciones sean justas.
```

## Prueba corta

```text
Comprueba que el caso puede arrancar con el deltaT elegido. No genera un
resultado aerodinámico válido.
```

## Paso temporal fijo

```text
El laboratorio no modifica automáticamente el deltaT por Co=1. El Courant se
monitoriza y la validez se decide comparando con pasos más finos.
```

## Estado abierto no estacionario

```text
Un perfil abierto puede no alcanzar una solución SIMPLE estacionaria. Un campo
acotado puede utilizarse como inicialización diagnóstica, pero no como resultado
RANS convergido.
```

## Presupuesto

```text
La estimación usa medidas del pilot de esta malla cuando están disponibles.
```

---

# 20. Tests obligatorios

## 20.1 Correctores

- SIMPLE escribe 0;
- PIMPLE escribe 1;
- widgets independientes;
- migración conserva configuraciones previas.

## 20.2 Checkpoints

- prueba corta bloqueada sin checkpoint;
- no traceback en UI;
- generación individual;
- generación de seis;
- extensión de 5,000;
- máximo;
- stale mesh;
- stale physics;
- hashes;
- abierto acotado/no convergido.

## 20.3 UX

No deben aparecer como etiquetas primarias:

```text
Atomic triplet
Pilot steps
Pilot seed source
Host-specific median
Dry-run
```

Deben existir traducciones, help y sección avanzada.

## 20.4 Storage

- RANS compacto retiene final y recuperación;
- escalares completos;
- no VTK/animación automática;
- URANS respeta purge;
- inventario correcto;
- Results intacto.

## 20.5 Monitor

- título RANS;
- título URANS;
- stage A-E;
- refresh 15/30/60;
- parsing incremental;
- monitor no lee campos volumétricos.

## 20.6 Gmsh

- abre `.msh` real;
- no regenera;
- registra proceso;
- error explícito si falta.

## 20.7 PIMPLE

- misma malla/checkpoint/dt/duración;
- 2/3/4;
- informe automático.

## 20.8 Malla

- baseline no sobrescrita;
- candidata guardada;
- thresholds;
- orden de celdas;
- provenance.

## 20.9 Smoke test

Las pruebas automáticas reales siguen condicionadas por:

```bash
RAMAIR_RUN_OPENFOAM_TESTS=1
```

El smoke test solicitado se ejecuta explícitamente tras la implementación y no forma parte de la suite normal.

Ejecutar:

```powershell
python -m pytest -c "Application Support/Tests/pytest.ini" CFD_2D/tests -q
python run_ramair_cfd2d_app.py --check-only --no-install-prompt
```

---

# 21. Protocolo de implementación

## Fase 1 — Auditoría

- revisar implementación actual;
- localizar dónde PIMPLE recibe cero;
- localizar widgets ambiguos;
- localizar gate/checkpoint;
- localizar monitor;
- localizar política de escritura.

## Fase 2 — Modelo de checkpoints

- estados;
- manifiestos;
- compatibilidad;
- cola;
- tests.

## Fase 3 — UI/UX

- nombres;
- ayudas;
- flujo simplificado;
- bloqueo guiado;
- tabla RANS.

## Fase 4 — Runner

- batch SIMPLE;
- extensión;
- compact storage;
- transferencia;
- smoke path.

## Fase 5 — Monitor

- títulos;
- paneles;
- parsing incremental;
- refresh.

## Fase 6 — Malla/Gmsh

- candidata ligera;
- botón Gmsh;
- tests/checkMesh.

## Fase 7 — PIMPLE study

- ejecución 2/3/4;
- comparación;
- recomendación.

## Fase 8 — Storage/postprocess

- inventario;
- on-demand products;
- limpieza activa segura.

## Fase 9 — Verificación

- tests;
- check-only;
- inspección UI;
- smoke real acotado;
- documentación.

---

# 22. Documentación

Actualizar:

- `CHANGELOG.md`;
- `PROJECT_CONTEXT_FOR_CODEX.md`;
- `README_PROJECT_STRUCTURE.md`;
- README del laboratorio;
- notas de migración de schema;
- ayudas de UI.

Documentar:

- SIMPLE 0 / PIMPLE 1;
- seis checkpoints;
- bloqueo de pilot;
- dry-run interno;
- perfiles compactos;
- PIMPLE study;
- candidata abierta;
- smoke test y sus límites.

---

# 23. Criterios de finalización

No declarar completo hasta que:

1. PIMPLE use 1 en el laboratorio y SIMPLE conserve 0.
2. Existan seis estados base gestionables.
3. El error de checkpoint se convierta en una acción guiada.
4. La cola de bases funcione y sea reanudable.
5. Los monitores identifiquen malla, modo y fase.
6. El refresco no ralentice sensiblemente.
7. El almacenamiento RANS sea compacto.
8. El almacenamiento URANS tenga límites.
9. Exista inventario.
10. Gmsh abra la malla seleccionada.
11. El estudio 2/3/4 sea ejecutable.
12. La candidata abierta no sobrescriba la baseline.
13. Los nombres ambiguos desaparezcan.
14. La UI muestre flujo base RANS → prueba corta → URANS.
15. Los tests pasen.
16. Se ejecute el smoke test real o se documente por qué no pudo ejecutarse.
17. Ningún resultado sintético o diagnóstico se publique como validación física.

---

# 24. Restricciones

- No eliminar el dry-run interno.
- No ejecutar solver sin acción explícita.
- No crear checkpoints falsos.
- No marcar convergencia por 10,000 iteraciones solamente.
- No extender indefinidamente un perfil abierto.
- No usar un checkpoint entre mallas.
- No sobrescribir Results históricos.
- No generar VTK/animaciones masivas automáticamente.
- No aumentar correctores para compensar un `deltaT` inviable.
- No usar la candidata ligera en GCI antes de integrarla coherentemente.
- No mostrar traceback crudo al usuario.
- No presentar el smoke test como validación.
- No ejecutar CATIA.
- No implementar 3D, FEM o FSI.

---

# 25. Referencias técnicas

- OpenFOAM Foundation User Guide: `fvSolution`, SIMPLE, PIMPLE y correctores no ortogonales.
- OpenFOAM Foundation Notes on CFD: corrección no ortogonal del laplaciano.
- `PROJECT_CONTEXT_FOR_CODEX.md`, API 15, schema 13.
- `README_PROJECT_STRUCTURE.md`.
- Cummings, Morton y McDaniel: estudio conjunto de malla, paso temporal, subiteraciones y damping.
- Gmsh 4.15.2 manual para inspección de `.msh`.
