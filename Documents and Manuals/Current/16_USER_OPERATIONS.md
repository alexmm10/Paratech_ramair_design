# Manual de operaciones por sección

Versión 2026.10.19. Los menús comparten el selector lateral **Caso de trabajo**. Biblioteca de perfiles y galería son comunes; geometrías, mallas y ejecuciones pertenecen al workspace seleccionado. Leer primero [instalación y ejemplo](15_FIRST_RUN_AND_DEFAULTS.md).

## 1. Inicio

**Para qué sirve:** localizar el trabajo, comprobar instalaciones, abrir manuales y consultar resultados conservados.

- Seleccionar el workspace lateral antes de entrar en una sección que genere datos.
- Abrir **Contenido incluido · presets, ejemplo y figuras** para ver las mallas de partida, número de celdas y extremos de calidad originales. Elegir una colección y las figuras a visualizar; no se necesitan campos CFD.
- **Verificar entorno e instalaciones** genera un diagnóstico. Revisar qué herramienta falta y su ubicación; no confundir comprobar una instalación con ejecutar una simulación.
- **Resumen y verificación de esta versión** muestra el informe actual. Los manuales por sección explican entradas, archivos, límites e interpretación.

**Salida:** informe de entorno y acceso a documentación. Si una dependencia falta, las funciones que la necesitan requieren instalarla; no se declara una comprobación aprobada por ausencia de datos.

## 2. Casos de trabajo

**Entrada:** nombre y ubicación de una carpeta. Un workspace válido contiene workspace.json; una carpeta con archivos sueltos no equivale automáticamente a un proyecto válido.

1. Crear un caso de trabajo nuevo o registrar una carpeta que ya contenga workspace.json.
2. Seleccionarlo en la barra lateral. Sus modelos CAD y condiciones CFD tienen propietarios independientes.
3. Para traslado, exportar desde **Trasladar**. Incluir resultados cuando se necesiten campos de continuación; una exportación reducida solo sirve para lo que sus archivos conservados permitan.
4. Al importar, usar un nombre nuevo. El importador verifica hashes, adapta las referencias internas y conserva el origen. Repetir la comprobación y aprobación de las mallas antes de ejecutar.

**Salida:** Workspaces/nombre/CATIA y CFD_2D. Los datos se respaldan aparte del código. El instalador del paquete completo incorpora Example_Open_Polar de forma idempotente y evita colisiones de nombre.

## 3. Perfiles 2D

**Entrada:** DAT en orden XFOIL o CSV con coordenadas y, cuando corresponda, secciones UPPER/LOWER. Indicar la topología real abierta o cerrada.

1. Elegir una referencia existente o importar un archivo. Revisar el contorno mostrado antes de guardarlo.
2. Preparar el TE con el cierre elegido. El cierre redondeado conserva tangencia y el dominio normalizado; no sustituye el resto del perfil.
3. Para diseñar el corte, seleccionar una base cerrada, Re/Mach y transición. **Estándar** toma la envolvente de la polar convergida; **optimizado** restringe el intervalo CL. No usar puntos XFOIL no convergidos para completar artificialmente la envolvente.
4. Guardar con nombre nuevo y verificar la base exacta asociada al corte. Un abierto necesita su base sin cortar para la interfaz del mallador.

**Salida:** carpeta compartida con original, CSV/DAT preparado y profile.json. CAD y CFD copian los puntos al crear una geometría; editar posteriormente la biblioteca no cambia silenciosamente una geometría ya utilizada.

**Problemas:** base ausente o incompatible, columnas no reconocidas, puntos duplicados o tangencias inválidas deben resolverse en la geometría; no se arreglan relajando checkMesh. XFOIL no está incluido como licencia ni como servicio externo.

## 4. Geometría CATIA

**Entrada:** perfil y configuración de canopy/sistema. Los presets entregados reproducen la configuración preparada de estudio.

1. En **Canopy y costillas**, editar dimensiones, número de celdas, cuerda y distribución transversal. Revisar las vistas después de cambiar arco, chordwise anchor, ballooning o incidencias.
2. En **Crossports**, definir forma, posición y tamaño, comprobando su posición en el perfil y costillas.
3. En **Suspensión y líneas**, separar las costillas estructurales de los frenos. Elegir porcentaje exterior por semienvergadura o celdas concretas; comprobar cobertura efectiva y fijaciones. Con celdas completas puede superarse el porcentaje solicitado.
4. Definir cascadas con plantilla o niveles manuales: A–B y C–D longitudinales, vecinos en envergadura o combinación. Una confluencia BRK por lado es la base del modelo nuevo. Validar destinos, nodos aislados, duplicaciones y longitudes nulas.
5. Revisar estabilizadores, opciones CATIA y exportaciones. Guardar como modelo nuevo y preparar Inputs. Revisar planta, frontal, lateral y el informe de la red antes de usar la macro.

**Salida:** CATIA/modelo/model_config.json, system_config.json, Inputs y macro compatible. La preparación Python no crea por sí misma un documento CATIA. Abrir la macro manualmente en CATIA V5 con licencia y seleccionar los Inputs correctos.

**Límite:** R/b conserva su referencia geométrica; sumar longitudes de cascadas no redefine esa distancia. Esta representación no acredita resistencia estructural, trim dinámico ni validación FSI.

## 5. Caso CFD

**Entrada editable:** nombre, Re, Mach, presión y temperatura. La cuerda CFD es 1 m.

La aplicación calcula ρ=p/(RT), a=√(γRT), U=Ma y ν=Uc/Re; μ=ρν. La viscosidad se ajusta a Re y Mach en esa cuerda; no es una corrección de la geometría CAD dimensional ni una afirmación de que todos esos valores satisfagan una ley atmosférica de viscosidad. Revisar la política antes de cambiar el modelo físico.

Crear condiciones nuevas para comparar casos a otra física. No editar datos de una condición ya utilizada esperando conservar la aprobación de sus mallas y ejecuciones: el sistema detecta cambios de contexto.

**Salida:** cfd_case.json con valores efectivos y revisión física. Un Mach elevado requiere reconsiderar el supuesto incompresible; introducirlo no convierte automáticamente el solver en compresible.

## 6. Mallado

**Entrada:** condición CFD, geometría importada y preset compatible abierto/cerrado.

1. Añadir geometría desde la biblioteca. En abierto comprobar el perfil base. El preset nuevo inicial es el del estudio correspondiente; el estándar abierto está disponible como alternativa al optimizado.
2. Revisar **General**, BL, volumen exterior, volumen interior abierto, TE y refinamiento. La extrusión 2D mantiene una capa transversal y frontAndBack empty.
3. Guardar parámetros en un preset o crear una versión de malla con nombre nuevo. Un preset contiene parámetros; no presta la aprobación de su malla de origen.
4. Generar, convertir a OpenFOAM y revisar el log checkMesh. Examinar extremos de no ortogonalidad, skewness, determinante, peso de interpolación y ratio de volumen, además del tamaño de BL y y+.
5. Utilizar los diagnósticos/atlas para localizar elementos limitantes. Identificar primero la región y causa; no aumentar todos los refinamientos de forma indiscriminada.
6. Aprobar solo la malla que supera las comprobaciones. La firma vincula archivos, configuración y contexto. Modificar geometría, física o malla obliga a revisar la compatibilidad.

**Salida:** mesh_config.json, mesh_report.json, mesh_final.msh, malla convertida, logs e informe de aprobación. No sobrescribir una versión que ya tenga casos OpenFOAM; generar otra.

**Límite:** checkMesh OK es calidad geométrica, no independencia de malla ni exactitud de una polar. Los tres presets de estudio conservan la evidencia de origen, sin prometer idéntica calidad en otra geometría.

## 7. Caso OpenFOAM

**Entrada:** geometría, mallas aprobadas, preset o configuración existente, nombre y ángulos.

1. Seleccionar una o varias mallas compatibles. Elegir el preset entregado para una configuración nueva o copiar una existente.
2. Revisar **General**, **Tiempo y fases A–E**, **Residuales y convergencia**, **Esquemas y correctores**, **Escritura y postproceso**. El archivo de configuración es la referencia efectiva; los parámetros de la malla y de la condición física permanecen en sus propietarios.
3. Definir un ángulo, lista o intervalo. El ejemplo prepara −5°…20° cada 1°; sus resultados retenidos no existen para todos los ángulos.
4. Guardar con nombre nuevo y generar los casos. Revisar los diccionarios y resumen antes de ejecutar.

**Salida:** solver_setup.json, solver_config.json, phase_plan.json y Angles/alpha_*/0, constant, system. El solver de referencia es Foundation 14, foamRun/incompressibleFluid, con SIMPLE RANS y PIMPLE URANS según selección explícita. No mezclar versiones Foundation y OpenCFD sin revisar diccionarios y utilidades.

## 8. Ejecución

**Entrada:** setup preparado, ángulos, acción, orden y recursos.

1. Seleccionar una o varias incidencias. Revisar cuáles tienen resultados, checkpoint o están pendientes.
2. Elegir acción: **polar solo RANS**, **continuar fase pendiente** o **iniciar URANS desde RANS**. La base entregada usa solo RANS. La cantidad de iteraciones no se interpreta como segundos físicos.
3. Ajustar recursos dentro del presupuesto mostrado. El controlador adapta el paralelismo al equipo y permite secuencia o concurrencia. Más núcleos no garantizan mejor rendimiento para mallas pequeñas.
4. Dejar desactivada **Ejecutar simulación real** para comprobar el plan. Activarlo únicamente cuando desee iniciar el solver.
5. Para detener, solicitar parada con escritura y esperar el checkpoint. Continuar conserva campos y orden de celdas; no renumerar ni repetir potentialFoam sobre una continuación. Reiniciar es una acción distinta y conserva el histórico según el flujo documentado.

**Salida:** colas, logs, historial de recursos/tiempos y campos. Un proceso que termina no implica convergencia. Revisar residuales, coeficientes y estado antes de publicar puntos.

**Convergencia de malla · RANS:** elegir misma geometría/condiciones, dos o más mallas y ángulos comunes. Copiar una configuración comparable, preparar los casos faltantes y ejecutar con el mismo gestor. Analizar coeficientes frente a N, diferencias, polares y errores. No extrapolar puntos ausentes ni dividir por referencias próximas a cero. GCI exige tres mallas adecuadas y resultados iterativamente admisibles; dos mallas o pruebas cortas no acreditan independencia.

**Remoto:** generar desde la selección un paquete para Linux; preparar OpenFOAM/MPI allí. Monitorizar, parar, continuar, recoger e importar el retorno verificado. El receptor debe aceptar por separado el servidor real y su política de recursos.

## 9. Postproceso

**Entrada:** resultados reales de una fase, ángulos y opciones de análisis. Se recuperan los ajustes guardados del setup; en su ausencia se utilizan los presets entregados.

- **Postproceso y análisis:** elegir ángulos y operaciones de pared, normales, campos/ParaView, animación o VTK. Las estaciones normalizadas, ventana RANS y rango temporal de imágenes son controles distintos. Una animación necesita varios estados guardados; no se inventan instantes.
- **Revisión independiente:** revisar RANS o URANS con sus criterios propios. El análisis frecuencial necesita una señal temporal válida y una ventana de producción estable; FFT/PSD de una señal ausente o constante no constituye evidencia de un pico físico.
- **Polar y resultados:** elegir RANS/URANS, incluir o retirar puntos revisados. RANS puede conservarse cuando URANS no se haya ejecutado o sea inadecuado, manteniendo la etiqueta de fase.
- **Comparación:** seleccionar setups con física y referencias comparables; observar polares, diferencias y métricas en ángulos disponibles. No adjudicar una diferencia a la apertura si cambian otras hipótesis.
- **Exportación:** copiar categorías a destino y verificar. Liberar campos es otra acción y exige confirmar una copia válida; no hacerlo si necesita continuar el solver o recalcular contornos.

**Ejemplo entregado:** contiene 9 puntos revisados RANS, 26 ángulos preparados y productos seleccionados. RETAINED_RESULTS_ONLY conserva las tablas y figuras sin recalcular campos desde 0. Para nuevo postproceso volumétrico ejecute una simulación propia y conserve sus campos.

## 10. Validation & Convergence Lab

El laboratorio conserva sus propios protocolos y datos. Permite preparar/comparar RANS y URANS, revisar señales, ventanas de producción y convergencia espacio-temporal. Es independiente del ejemplo reducido y de los estudios generales RANS.

Seleccionar casos compatibles, revisar qué datos reales existen, postprocesar/revisar cada caso y publicar solo los resultados admisibles en el estudio correspondiente. Abrir el análisis individual antes de combinar malla y paso temporal. Si falta producción estable o ciclos suficientes, el estado debe permanecer limitado o no admisible. Un diagrama disponible no sustituye sus hipótesis y controles de calidad.

La pestaña **Galería** permite consultar los resultados visuales incluidos sin las campañas del laboratorio. No crea registros de convergencia ni ejecuciones ficticias para rellenar la instalación nueva.

## 11. Archivos y logs, mantenimiento

Consultar estado de trabajos, logs y rutas desde la aplicación antes de buscar archivos por carpetas históricas. El menú de archivos muestra el workspace seleccionado; no borrar campos a mano durante una ejecución.

Para incidencias, conservar versión/commit, condición física, perfil, preset efectivo, número de celdas, estado de checkMesh, comando/acción, ángulo y último log. Diferenciar dependencia ausente, entrada incompatible, error numérico y resultado científicamente insuficiente.

Para desarrollo leer AGENTS, contexto e inventarios; cambiar las capas UI/backend juntas cuando se modifica un contrato, ejecutar pruebas/auditorías y guardar evidencias. La configuración del usuario y los checkpoints no se limpian al actualizar código. Las comprobaciones en otro dispositivo, CATIA y servidor real deben registrarse con su entorno y alcance.
