# Informe de entrega · RamAir Studio 2026.10.19

## Resultado

La entrega se reúne en un solo ZIP: instalación Windows/WSL, código, macro CATIA, perfiles existentes, presets efectivos de estudio, manuales por sección, galería de resultados y workspace abierto importable. No requiere recuperar el árbol de desarrollo ni copiar entornos instalados. El paquete, su manifiesto y el archivo SHA-256 fijan la versión y el commit exactos.

El código mantiene separados interfaz, servicios científicos, bibliotecas y workspaces. La aplicación sigue siendo API 28; los nuevos defaults y herramientas de entrega amplían el flujo sin cambiar el contrato existente. Las configuraciones del usuario no se reemplazan al cargar los defaults.

## Configuraciones de partida

Application Support/Configurations/studio_defaults.json selecciona los archivos de Study Presets. La procedencia identifica la configuración efectiva utilizada, evitando acumular ajustes posteriores sobre un nombre de malla antiguo.

- CAD: dimensiones, forma, suspensión, crossports y opciones del modelo preparado. Los perfiles siguen siendo seleccionables de forma independiente del diseño dimensional.
- CFD: condición Re=1.9e6 y M=0.15, presión/temperatura de estudio y cuerda CFD 1 m, con propiedades derivadas recalculadas.
- Malla cerrada general: configuración aprobada de 215058 celdas de origen.
- Malla abierta optimizada: configuración aprobada de 248180 celdas; abierta estándar: 265520.
- OpenFOAM: archivos efectivos de solver y fases de los estudios, con ejecución inicial RANS-only y sin renumeración. URANS se elige explícitamente. El arranque abierto nuevo muestra maxCo=1; el archivo de procedencia mantiene el valor histórico del estudio.
- Postproceso: recuperación de ajustes guardados; en su ausencia, estaciones de pared, 80 muestras por normal, ventana final de 500 muestras RANS y opciones de visualización/exportación del estudio.

Se añaden seis referencias exactas coarse/medium/fine del laboratorio: cerrado 203691/333826/618382 y abierto 223080/302692/502474 celdas. Sus formatos pertenecen al motor legacy de validación y se conservan separados de los presets anidados del mallador general. No se crean mallas ni ejecuciones ficticias para aparentar la existencia de esa campaña en el equipo receptor.

## Geometría y mallas comprobadas

Se regeneraron realmente las mallas con los defaults cerrado y abierto optimizado, incluyendo Gmsh, conversión a OpenFOAM y checkMesh. Se reprodujeron 215058 y 248180 celdas, ambas con checkMesh OK.

| Métrica | Cerrada | Abierta optimizada |
|---|---:|---:|
| No ortogonalidad máxima [°] | 42.2286 | 51.8483 |
| Skewness máxima | 0.651769 | 0.667073 |
| Determinante mínimo | 0.00881479 | 0.01135534 |
| Peso de interpolación mínimo | 0.121769 | 0.0857932 |
| Ratio de volumen mínimo | 0.138652 | 0.0870889 |

La evidencia completa conserva todos los indicadores y comandos. Las métricas describen esas geometrías; cargar un preset en otro perfil no hereda una aprobación. La interfaz mantiene una advertencia de crecimiento tangencial previsto 1.1111 frente a objetivo 1.1 en el ajuste abierto original. No se ocultó ni se modificó silenciosamente la configuración de estudio para eliminarla; la malla regenerada supera los controles geométricos reales. Cualquier refinamiento posterior exige revisar su nueva calidad.

## Ejemplo que recibe el usuario

Example_Open_Polar contiene geometría LS1 optimizada, base sin cortar, malla real de 248180 celdas, configuración RANS_polar_20260913, 26 ángulos preparados entre −5° y 20° y nueve puntos RANS revisados disponibles en el estudio de origen. Se conservan tablas, historias y figuras seleccionadas; no se rellenan ángulos ausentes ni se extrapola la polar.

Los checkpoints volumétricos se omiten. Algunos archivos 0 de origen contenían datos transferidos desde RANS: se archivaron fuera de la entrega y se reconstruyeron campos iniciales coherentes mediante el escritor oficial, con física, ángulo y condiciones de contorno correspondientes. Los casos originales del usuario permanecen intactos. El ejemplo inicia cálculos nuevos; no permite continuar el estado volumétrico de la campaña original.

Cada snapshot conserva su etiqueta. RETAINED_RESULTS_ONLY impide recalcular campos desde la condición inicial y sustituir las figuras del estudio por resultados aparentes. Tras una ejecución propia con nuevos campos, el postproceso vuelve a estar disponible, incluidos campos comprimidos U.gz.

Al trasladar los JSON cambian las rutas y sus hashes. El instalador solo adapta las referencias de los puntos tras verificar el paquete y contrastar que Cl/Cd/Cm no hayan cambiado. Conserva el alcance científico original y no otorga una aceptación nueva.

## Instalación y transferencia

INSTALAR_Y_ABRIR verifica contenido, prepara el entorno con el lanzador oficial, incorpora el ejemplo y repite checkMesh si OpenFOAM está disponible. Si una dependencia o comprobación falta, informa y mantiene pendiente la aprobación. ABRIR_RAMAIR recuerda la ubicación elegida. Reinstalar el mismo ejemplo conserva las modificaciones; una colisión de nombre crea otro workspace.

La recepción del instalador también contempla una carpeta parcialmente preparada que ya tenga entorno Python: la sincronización repone configuraciones básicas y archivos raíz de biblioteca que falten sin reemplazar los existentes. Esto evita que una instalación interrumpida quede inutilizable por la sola presencia de su entorno.

La importación verifica cada objeto único y deduplica únicamente el área temporal. Los archivos finales de los distintos casos siguen siendo copias independientes. Esta mejora evita duplicar innecesariamente varios gigabytes durante la recepción y no introduce enlaces compartidos entre casos que puedan alterar sus mallas al editar otro.

La instalación se probó con un entorno Python nuevo en otra ruta WSL del mismo dispositivo. Se importó el ejemplo, se repitió checkMesh y su aprobación, se comprobaron los 26 campos iniciales, los nueve puntos retenidos y un plan RANS-only de dos ángulos sin ejecutar solver. La prueba no equivale a haber utilizado un segundo dispositivo físico.

## Interfaz, manuales y figuras

Las once secciones se ejercitaron mediante la interfaz automatizada de Streamlit sin excepciones ni errores: Inicio, Casos de trabajo, Perfiles 2D, Geometría CATIA, Caso CFD, Mallado, Caso OpenFOAM, Ejecución, Postproceso, laboratorio y Archivos/logs. La advertencia geométrica original se conserva separada de errores de funcionamiento.

Se añaden 36 figuras de polares estándar/optimizada, comparación y validación, con procedencia y alcance. Inicio y el laboratorio permiten consultarlas sin disponer de las campañas volumétricas. Los manuales 01–14 describen secciones y mantenimiento; 15 explica instalación/primer uso, 16 las operaciones de cada sección y 17 los parámetros efectivos. Los inventarios AST y de funciones se han regenerado.

La revisión visual nativa actual de escritorio y pantalla estrecha no pudo completarse: Computer Use se detuvo al no poder verificar la URL del navegador con suficiente seguridad. No se intentó eludir esa restricción. Las comprobaciones automatizadas de las once páginas sí están completadas; la evidencia visual de septiembre corresponde a aquella versión y no se presenta como verificación visual de estos cambios.

## Pruebas de software y auditoría

La suite final termina con **438 pruebas aprobadas y una omitida**. Las pruebas específicas de entrega/workspaces y contratos de aplicación completaron 29 y 33 pruebas respectivamente. Se comprueban integridad, rutas, conservación de casos, importación idempotente, colisiones, aislamiento de archivos finales, perfiles reubicables, snapshots sin campos, coeficientes tras traslado y recuperación de opciones de postproceso, incluido inicio RANS en cero.

Los auditores de contexto y artefactos están aprobados. Los enlaces locales de los manuales se revisaron sin referencias rotas. El verificador de la entrega comprueba todos los archivos y objetos del ejemplo, compila Python y contrasta commit y manifiestos. El recibo final y SHA-256 del ZIP se generan junto al paquete para identificar la distribución concreta.

La evidencia de código/mallas/instalación está en Evidence/delivery_20261019. Los recibos del ZIP final se adjuntan a la carpeta exterior de verificación, porque incluir su propio hash dentro del código produciría una referencia circular.

## Estructura y continuidad

Git contiene código, defaults, presets, referencias pequeñas, figuras y manuales; los objetos del ejemplo se adjuntan al ZIP descargable. Los workspaces, campos y archivos del usuario permanecen separados. Los informes antiguos sustituidos y las copias auxiliares de preparación se conservan fuera del árbol activo. No se eliminan checkpoints ni campañas existentes del usuario.

Para continuar: leer README, AGENTS, contexto, estructura y manual correspondiente; instalar requirements-development; revisar los contratos afectados; ejecutar pruebas y auditorías; publicar un commit nuevo y reproducir el paquete con el workspace exportado de ese mismo commit. El generador de la entrega rechaza ejemplos de otro commit.

## Límites de recepción

- CATIA no se ejecutó: se preparan macro y datos compatibles. La empresa debe comprobarlos con su instalación y licencia.
- El servidor empresarial requiere comprobación propia de OpenFOAM, MPI, recursos, almacenamiento y políticas.
- No se ejecutó una nueva campaña RANS/URANS para empaquetar. Las pruebas reales de solver anteriores conservan su evidencia histórica; los datos retenidos mantienen la revisión original.
- checkMesh y pruebas de software no acreditan independencia espacial/temporal ni validación aerodinámica universal. Estas se aceptan por caso con resultados suficientes y referencias comparables.
- La revisión visual actual en navegador queda pendiente por la restricción de la herramienta indicada; no se declara completada.
