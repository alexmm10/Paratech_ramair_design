# RamAir CFD 2D: postproceso y ParaView

Fecha de auditoria: 2026-09-08
Herramientas: parser Python, utilidades OpenFOAM y ParaView/pvbatch

## 1. Arquitectura del postproceso

El postproceso tiene dos capas complementarias:

1. Python analiza historiales, ventanas estadisticas, residuales, Courant y
   datos de pared;
2. ParaView lee los campos OpenFOAM y genera vistas espaciales reproducibles.

RANS y URANS se almacenan y muestran por separado. En RANS el eje independiente
es la iteracion SIMPLE; en URANS es tiempo fisico. El modo `AUTO` inspecciona
diccionarios, logs y manifiestos para evitar presentar iteraciones RANS dentro
de la pestana URANS.

## 2. Flujo rapido y animaciones

El boton de postproceso rapido reconstruye solo la ultima iteracion/tiempo que
necesita campos, ejecuta operaciones de campo finales y genera graficas de
coeficientes y capturas finales. No recorre todos los instantes para crear una
animacion.

La generacion de animaciones es una tarea independiente. Selecciona un numero
acotado de tiempos dentro del intervalo elegido, reconstruye los campos
necesarios y conserva como maximo el numero de frames configurado. De este modo
una revision inicial no paga el coste de leer y renderizar toda la produccion.

Los productos se registran en manifiestos portables. La app permite activar o
ocultar tres grupos sin regenerarlos: graficas escalares, productos ParaView y
animaciones.

## 3. Historias y estadistica

Los parsers combinan reinicios sin duplicar tiempos y escriben:

- historia completa y ventana de promedio de Cl, Cd y Cm;
- media, desviacion y eficiencia Cl/Cd;
- residuales por ecuacion;
- Courant y `deltaT`;
- tiempos disponibles e inventario de campos;
- separacion, flujo inverso y diagnosticos de pared cuando existen.

La ventana URANS se toma dentro de produccion E. Puede usar una fraccion final
configurable para evitar mezclar asentamiento o una produccion excesivamente
larga. En RANS se promedian las ultimas muestras elegidas, mientras la grafica
mantiene todo el historial desde iteracion cero.

## 4. Analisis de pared

`openfoam_wall_analysis.py` exporta, cuando los campos estan disponibles:

- `wall_yplus_vs_xc`;
- `wall_cp_vs_xc` por ramas;
- esfuerzo cortante y coeficiente de friccion;
- perfiles de velocidad normales a pared;
- espesor numerico, teorico y de la pila prismatica;
- overlay de separacion mediante Cp y Cf.

Para perfil abierto, las ramas interna y externa se mantienen separadas. La
nueva salida `wall_cp_internal_minus_external` interpola unicamente dentro del
solape medido de `x/c`, por intrados y extrados, y calcula
`Delta Cp = Cp_internal - Cp_external`. No extrapola a zonas sin datos. El CSV
y PNG estan disponibles tanto en comparacion abierto-cerrado como en
convergencia para casos abiertos porque ambos llaman al mismo postproceso.

## 5. Productos espaciales ParaView

Las capturas finales disponibles incluyen:

- Cp sobre el perfil;
- velocidad y detalle de BL/TE con malla;
- streamlines de velocidad;
- contornos discretos de velocidad y presion;
- vorticidad, contornos y threshold;
- Q positivo y combinaciones Q-presion/Q-vorticidad;
- Courant final y, para diagnostico, hotspots;
- y+ final cuando el campo existe.

Los contornos se representan como lineas coloreadas por su variable sobre un
fondo blanco roto. La geometria del perfil se superpone en negro y con mayor
grosor. El encuadre cercano habitual va desde 0.5c aguas arriba del LE hasta 1c
aguas abajo del TE. El perfil se muestra con su angulo de ataque y el flujo se
mantiene paralelo a x.

Las streamlines se siembran sobre una linea perpendicular al flujo, una o dos
cuerdas aguas arriba. La resolucion nominal es 100 semillas, pero el grosor se
mantiene pequeno para no ocultar contornos. Las lineas se usan en vistas de
streamlines, vorticidad y Q; se excluyen de Cp, velocidad simple y detalle BL.
Que una streamline no atraviese la pared ni una celda solida es comportamiento
fisico correcto. Para revelar recirculacion, la linea de semillas debe incluir
el fluido cercano al labio/cavidad y el integrador debe permitir avance y
retroceso, no aumentar indefinidamente el numero de semillas externas.

## 6. Campos y escalas

El postproceso final puede calcular `yPlus`, `wallShearStress`, `vorticity` y
`Q` con las utilidades de OpenFOAM. `Cp` procede del objeto `pressure` del caso;
no se asume un alias no disponible en Foundation 14. Courant se conserva en el
instante final y su historia ligera se lee del log.

Las escalas son robustas a outliers mediante percentiles y reglas por campo.
Q se limita a valores no negativos para identificar estructuras rotacionales;
vorticidad reduce el maximo visual cuando unos pocos extremos dejan el dominio
azul. Las barras de color se muestran cuando ayudan a interpretar el threshold
o magnitud y se omiten en vistas direccionales simples.

## 7. Animaciones

Las animaciones usan las mismas escalas y camara que la captura final para
evitar parpadeo o cambios de interpretacion. Los frames se mantienen mas tiempo
que en la configuracion inicial. En RANS se conserva una primera y una ultima
captura y la transicion completa se ofrece como animacion; en URANS la interfaz
muestra capturas finales y animaciones bajo demanda, no decenas de PNG sueltos.

Los productos previstos son MP4 y, si el codec no esta disponible, GIF. La app
solo intenta reproducir archivos que existen y muestra una razon cuando hay
menos de dos estados positivos reconstruidos.

## 8. Apertura interactiva y mallas fallidas

La apertura interactiva resuelve la ruta Linux nativa del caso, crea o localiza
el descriptor `.foam` y lanza ParaView sin copiar el caso. Esto evita el antiguo
`Copy mode` provocado por abrir rutas Windows/WSL ambiguas. El launcher comprueba
que la ruta y `constant/polyMesh` existen antes de abrir.

Para una malla fallida, el visor puede abrir conjuntamente los VTK producidos
por `checkMesh -writeSets -writeSurfaces -setFormat vtk`. Cada set conserva su
nombre de problema, y la app ofrece causa probable, metrica limite y region
para orientar la correccion manual.

## 9. Rendimiento y limites

Una captura final tipica de validacion medida tardo aproximadamente 63-68 s.
El coste dominante es reconstruir/leer campos y arrancar pvbatch, no el parser
de coeficientes. Las animaciones multiplican ese coste por el numero de frames;
por ello permanecen separadas y limitadas.

Para reducir coste:

- no reconstruir todos los tiempos para una revision final;
- reutilizar campos ya calculados y manifiestos vigentes;
- no ejecutar ParaView mientras una simulacion compite por todos los cores;
- mantener historias escalares frecuentes y campos volumetricos espaciados;
- conservar solo una ventana rodante suficiente mediante `purgeWrite`.

Una imagen bonita no valida la solucion. Las conclusiones deben combinar
historiales, ventana estadistica, independencia espacio-temporal, y+ y revision
de campos.

## 10. Codigo y referencias

- `CFD_2D/scripts/ramair_2d_postprocess.py`
- `CFD_2D/scripts/openfoam_wall_analysis.py`
- `CFD_2D/scripts/paraview_case_viewer.py`
- `CFD_2D/scripts/ramair_2d_rans_paraview_final.py`
- `CFD_2D/app/ls1_validation_page.py`
- `CFD_2D/app/validation_convergence_page.py`
- [ParaView: Python y pvbatch](https://docs.paraview.org/en/v5.13.3/Tutorials/ClassroomTutorials/pythonAndBatchPvpythonAndPvbatch.html)
- [ParaView User Guide](https://docs.paraview.org/)
- [OpenFOAM v14, postproceso](https://doc.cfd.direct/openfoam/user-guide-v14/post-processing-functionality)
