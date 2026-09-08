# RamAir CFD 2D: aplicacion y workflow

Fecha de auditoria: 2026-09-08
Estado: capacidades verificadas contra el codigo y las pruebas del repositorio

## 1. Objetivo y alcance

RamAir CFD 2D es una superficie Streamlit para preparar, ejecutar, revisar y
comparar estudios aerodinamicos 2D reproducibles. La aplicacion coordina cuatro
familias de trabajo:

1. preparacion de geometria y casos de trabajo;
2. generacion y control de mallas Gmsh/OpenFOAM;
3. ejecucion RANS/SIMPLE y URANS/PIMPLE local o portable;
4. postproceso escalar y visual, validacion y convergencia.

La interfaz no sustituye los motores cientificos. Cada accion crea una orden
trazable para un script, conserva su configuracion y registra salida, PID,
estado y evidencias. La API coordinada entre frontend y backend es la version
26. El runtime canonico es Linux/WSL2 en
`/home/alejm/ramair_cfd/DESIGN_APP`; el launcher sincroniza alli una copia
atomica del codigo fuente y comprueba dependencias antes de iniciar Streamlit.

## 2. Navegacion y capacidades

La barra lateral mantiene el contexto activo: caso de trabajo, geometria 2D,
malla, caso/ejecucion y ubicacion real de resultados. Las paginas principales
son:

| Pagina | Funcion |
|---|---|
| Estado | Dependencias, rutas y salud del entorno. |
| Caso de trabajo | Carga, creacion, cambio controlado y archivo del workspace. |
| Geometria | Seleccion de perfil, preproceso y diseno del corte ram-air. |
| Caso CFD | Condiciones fisicas, angulos y referencias dimensionales. |
| Malla | Mallador general, malladores experimentales abierto/cerrado, calidad y visualizacion. |
| Caso OpenFOAM | Escritura de diccionarios y copia de una malla real compatible. |
| Ejecucion | Caso individual, barrido, continuidad, parada y paquetes portables. |
| Postproceso | Historias, campos, productos ParaView, animaciones y revision. |
| Validation & Convergence Lab | Validacion cerrada, comparacion abierto-cerrado, convergencia e informe de rendimiento. |
| Archivos y logs | Registro de tareas y acceso a evidencias. |

El laboratorio aislado evita que una operacion del workspace general cambie
accidentalmente una campana de validacion. Sus cuatro pestanas superiores son:

- validacion LS(1)-0417 cerrada frente a referencias publicadas;
- comparacion de la polar abierta frente a puntos cerrados aceptados;
- convergencia espacial/temporal para topologias abierta y cerrada;
- rendimiento del solver y de la maquina.

## 3. Contrato de datos y reproducibilidad

Los datos se separan por responsabilidad:

- `CFD_2D_inputs`: geometria, configuracion y paquetes de caso;
- `meshes` y `experimental_meshes`: revisiones, malla y evidencia de calidad;
- `openfoam_cases`: casos generales preparados;
- `validation_polar_study`: casos y puntos de polar cerrada;
- `validation_studies`: checkpoints y matriz de convergencia cerrada/abierta;
- `results`: productos generales de postproceso;
- `reports`: auditorias, decisiones y evidencia compacta.

Los JSON de configuracion son la fuente de verdad. Las rutas `Path` se
normalizan antes de serializarse y los casos guardan configuracion efectiva,
manifiesto, hashes y plan de fases. Una ejecucion publicada conserva el origen
de malla, geometria, condiciones y ventana estadistica.

Los estados distinguen, entre otros, preparado, en ejecucion, pausado
reanudable, finalizado, divergencia, fallo y bloqueo por ausencia de checkpoint.
La existencia de carpetas de tiempo no basta para declarar una fase completa:
se exige checkpoint coherente, campos requeridos y evidencia de final normal.

## 4. Orquestacion y seguridad operacional

Cada solver adquiere un lease unico, registra PID/PGID y token de inicio del
proceso. Esto evita confundir un PID reciclado con una simulacion viva. Al
arrancar, la aplicacion reconcilia registros `RUNNING` obsoletos con los
procesos reales.

Las colas de validacion, bases RANS y casos URANS soportan:

- continuar checkpoints existentes, crear casos ausentes y omitir finalizados;
- solicitar parada limpia del caso actual y saltar al siguiente;
- detener toda la cola conservando el ultimo estado escribible;
- parada forzada como segundo recurso;
- timeout por caso;
- reanudacion desde el primer caso incompleto, sin repetir los aceptados.

La parada limpia cambia `stopAt` para pedir escritura a OpenFOAM. El estado se
marca reanudable solo si el ultimo tiempo contiene los campos comunes
necesarios. Las fases hijas A-E no pueden publicar por si solas la finalizacion
de toda la campana.

## 5. Validacion y evidencia

La validacion cerrada permite seleccionar angulos, preparar RANS, continuar a
URANS, postprocesar y publicar manualmente puntos. Las estadisticas de polar
usan unicamente los puntos publicados visibles. Se generan polares, diferencias
por angulo, norma RMS normalizada `err`, error de extremo `err2` y comparacion
RANS-URANS.

La comparacion abierto-cerrado conserva una configuracion independiente, aunque
se inicializa una vez con los valores de validacion cerrada. Ofrece angulos de
-10 a 20 grados en pasos de 2 grados, menus de solver/ejecucion equivalentes y
postproceso RANS y URANS separado. Compara la polar abierta publicada con la
polar cerrada aceptada, incluye `Cm`, parametros caracteristicos de ambas
geometrias y el cambio RANS-URANS de la geometria abierta.

La comparabilidad adimensional requiere el mismo `c`, `U_inf`, `rho`, `mu`,
`Re`, `M`, espesor spanwise, `lRef`, `Aref`, centro de momentos y direcciones de
lift/drag. La auditoria de configuracion comprueba esas referencias. En el
perfil abierto se integran las paredes interna y externa; en el cerrado solo
la pared exterior. Esa diferencia es la variable fisica del estudio, no un
cambio de normalizacion.

## 6. Rendimiento y portabilidad

La pestana Rendimiento muestra hardware, escalado fuerte, coste de tareas
reales, sobrecarga del monitor, recomendaciones automaticas y el informe
completo. Para esta maquina, la politica equilibrada usa aproximadamente
100 000 celdas por rank fisico y rechaza perfiles empiricos con menos de cinco
pasos validos.

Los paquetes remotos congelan casos, configuracion, cola, hashes y launchers
Linux/WSL. Permiten ejecutar o reanudar fuera del proyecto completo y devolver
resultados para su clasificacion y postproceso local. No incluyen toda la
interfaz ni inventan dependencias: requieren OpenFOAM, MPI y un entorno Python
minimo declarado por el paquete.

## 7. Limites y criterios de interpretacion

- `checkMesh OK` demuestra consistencia topologica/geometrica bajo sus umbrales;
  no demuestra independencia de malla ni exactitud aerodinamica.
- Una reduccion de residuales no sustituye la estabilidad de Cl/Cd/Cm ni una
  ventana URANS suficiente.
- Los puntos de polar se publican por decision del usuario; un warning permite
  publicar evidencia parcial, pero no la etiqueta automaticamente como
  convergida.
- Las animaciones son productos costosos y estan separadas del postproceso
  rapido.
- Las recomendaciones de cores son especificas de la maquina, malla y perfil
  numerico; se vuelven a medir cuando cambia esa identidad.

## 8. Trazabilidad tecnica

Codigo principal:

- `CFD_2D/app/ramair_cfd2d_app.py`
- `CFD_2D/app/workflow_backend.py`
- `CFD_2D/app/validation_convergence_page.py`
- `CFD_2D/app/ls1_validation_page.py`
- `CFD_2D/app/open_closed_validation_page.py`
- `CFD_2D/scripts/ramair_execution_control.py`
- `CFD_2D/scripts/ramair_2d_execution_registry.py`
- `CFD_2D/scripts/ramair_2d_urans_cases.py`

Informes relacionados:

- `CFD_2D/README_CFD_2D.md`
- `CFD_2D/reports/OPENFOAM_PERFORMANCE_AUDIT_20260907.md`
- `CFD_2D/reports/OPENFOAM_BASE_AND_FINAL_MESH_AUDIT_20260901.md`
- `CFD_2D/reports/VALIDATION_LAB_SCHEMA9_BASELINE_AND_IMPLEMENTATION_20260813.md`
