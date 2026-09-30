# Informe de entrega · RamAir Studio 2026.09.30

## Resultado y alcance

La versión consolidada reúne diseño de perfiles, preparación CAD, Gmsh/OpenFOAM, ejecución RANS/URANS, postproceso y laboratorio. API 28 coordina la interfaz modular con sus servicios. Se incorpora la nueva suspensión y la convergencia RANS en ejecución general. La entrega separa aplicación, ejemplo y ejecución remota; los manifests fijan versión, commit y hashes.
Los archivos de tareas previas se revisaron antes de incorporarlos. Se conserva el código de compatibilidad que aún usan el laboratorio y Results; las herramientas puntuales y documentación obsoleta salen del árbol activo.

## Suspensión CAD

Selección estructural y frenos independientes, porcentaje exterior editable o celdas, simetría/asimetría y cálculo por posiciones reales. Nuevos modelos parten de 33% exterior por semienvergadura; modelos anteriores preservan datos originales. Se reportan IDs, fijaciones y cobertura efectiva.
Plantillas directas, transversales, A–B/C–D, combinadas y manuales. Una confluencia BRK por lado; destinos compatibles y continuaciones sin confluencias artificiales para elementos aislados. Validación de ciclo, duplicación, longitud nula y desconexión; informe JSON/CSV junto al modelo. R/b no se redefine por la cascada.
Previsualización con planta, frontal, lateral y mapa de celdas frenadas; caché dependiente de fuentes y configuración. Preparación Python y macro verificadas sin ejecutar CATIA. La red no constituye una comprobación estructural o de equilibrio CFD–FSI.

## Convergencia RANS y recuperación

Estudios persistentes por geometría con copias de ajustes efectivos, firma física/numérica, matriz de estados y fuente/hash de resultados. Selecciones de uno/múltiples ángulos, reutilización compatible y creación de casos faltantes. Usa el gestor de recursos y monitores con pausa/continuación; fuerza RANS-only también fuera del equipo principal.
Doce figuras y CSV: polares y coeficientes frente a N, diferencias por ángulo, referencia fina editable. Puntos ausentes y porcentajes próximos a cero identificados; análisis obsoletos se recalculan. GCI 2D requiere tres mallas admisibles y convergencia iterativa/revisión; pruebas cortas bloqueadas para aceptación.
Se corrigió el guardado final de RANS cuando su límite de iteraciones es menor que el intervalo de escritura. Las continuaciones conservan campos/numeración; no renumeran ni repiten potentialFoam. RANS-only no traslada prematuramente su contador de iteración a t=0 URANS. Reiniciar y continuar son acciones separadas, con archivo de resultados previos.

## Pruebas reales y evidencia

Desde un workspace nuevo se procesó LS1 con TE redondeado, se preparó CAD y se crearon condiciones Re=1.9e6, M=0.15, cuerda 1 m. Se generaron dos mallas reales aprobadas por checkMesh, con 108104 y 155824 celdas; se crearon casos de 0° y 8° y se ejecutaron cuatro RANS acotados.
La malla coarse se pausó en iteración 22 y continuó hasta 42 para ambos ángulos; medium alcanzó 80. Los hashes de malla mantuvieron el orden de celdas. Los cuatro casos generan postproceso PROCESSED_PARTIAL y doce figuras comparativas. Cl/Cd/Cm siguen transitorios de convergencia; no deben usarse para rendimiento aerodinámico final.
El recorrido remoto aislado continuó 42→62→82, pausó guardando campos, conservó malla, postprocesó, recogió y verificó el ZIP de retorno e importó en receptor separado. No inició URANS y preservó la clasificación parcial.
La prueba de recuperación que Windows omite por requerir grupos de procesos Unix se ejecutó adicionalmente en WSL: los tres tests de esa sección aprobaron.
Las pruebas completas en Windows finalizaron con 428 aprobadas y una omitida porque requiere grupos de procesos Unix, sin warnings. Las once secciones no generaron excepciones ni errores; el laboratorio informa por separado de y+ y del protocolo RANS/URANS. El recálculo de las doce gráficas se verificó desde el menú. La vista de 1366 px y la de 390 px no presentaron desbordamiento de documento ni imágenes rotas. La instalación en una ruta distinta creó un entorno Python nuevo, importó el ejemplo y repitió checkMesh y aprobación de ambas mallas. La evidencia y verificadores de paquete se registran en Evidence/delivery_20260930. La evidencia distingue software, pruebas CFD breves y validación científica; las tablas no declaran independencia con dos mallas.

## Limpieza y estructura

Se archivaron 501 archivos Windows y 186 WSL históricos/puntuales/bibliográficos y temporales fuera de la aplicación. Todos sus hashes se verificaron contra los inventarios. Estos guardan ruta, hash y razón. No se movieron Workspaces, Results, Data Archive, resultados o checkpoints del usuario. Los temporales y entornos regenerables quedan fuera de Git y paquetes. Current contiene manuales por sección, inventario de módulos/funciones, reglas de mantenimiento e informe vigente.
Se eliminaron referencias de arranque a informes de septiembre anteriores y se actualizó el panel de informe en Inicio. Instaladores y rutas son configurables; la documentación de recepción no depende de un usuario de Windows concreto.

## Distribución y GitHub

Código, macro, dependencias declaradas, presets, perfiles pequeños y pruebas pertenecen a Application. Example contiene solo la demostración acotada seleccionada. Remote conserva casos y herramientas de ejecución/retorno, sin entornos instalados. Los tres incluyen identidad de release y hash por archivo; SHA256SUMS verifica los archivos enviados.
Se publica main sin reescribir historia, etiqueta studio-delivery-20260930 y paquetes. CI verifica Python, contratos, contexto, artefactos y manifiestos; las pruebas de WSL/OpenFOAM no se sustituyen por CI genérico.

## Límites de aceptación en la empresa

- CATIA V5: preprocesado y macro preparada; operaciones CATIA reales y licencia pendientes en instalación receptora.
- Servidor real: procedimiento probado en WSL aislado; hardware, scheduler/políticas y almacenamiento del servidor empresarial pendientes de recepción.
- CFD: smoke funcional con RANS, no campaña de independencia espacial/temporal ni polar científicamente convergida. La validación del laboratorio y sus datos propios permanecen independientes.
- Inlets abiertos: un checkMesh correcto no garantiza estabilidad para todo dt ni Reynolds; controlar calidad local, Courant y suficiente producción estable antes de aceptar URANS/FFT/GCI.
- Transporte: no se distribuyen licencias, programas externos instalados o PDFs sin autorización. El receptor debe instalar dependencias y revalidar las aprobaciones tras importar.

## Continuidad

Leer README, estructura, AGENTS, contexto y manual de la sección. Guardar la evidencia y el manifiesto del release. Respaldar workspaces aparte. Registrar cambios, repetir pruebas y publicar commits nuevos. Las referencias físicas principales se mantienen en CFD_2D_TECHNICAL_SPECIFICATIONS.txt; Current describe el comportamiento efectivo de la versión entregada.

## Correcciones de cierre

El monitor distingue una ejecución activa de un checkpoint recuperable y no deduce RUNNING solo por existir un log. Se eliminó una carrera entre la consulta de trabajos y el cierre de sus archivos de log. Los espectros de señales constantes muestran ausencia de energía positiva sin forzar escalas logarítmicas inválidas. Las referencias de perfiles y presets empaquetados usan rutas relativas; los registros de procedencia no son dependencias de ejecución. Los informes históricos adicionales y temporales se conservan en inventarios externos de limpieza; no forman parte de la distribución.

El informe de rendimiento se distribuye desde Current y consulta hardware actual. Los benchmarks históricos son datos opcionales del usuario; su ausencia no es un fallo de instalación. Los indicadores de Courant/dt ausentes se muestran como ausentes. El gestor registra la intención de parada antes de interrumpir el proceso y permite iniciar un nuevo trabajo tras la parada.
