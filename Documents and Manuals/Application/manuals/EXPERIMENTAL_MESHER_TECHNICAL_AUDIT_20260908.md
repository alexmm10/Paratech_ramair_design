# RamAir CFD 2D: mallador experimental Gmsh

Fecha de auditoria: 2026-09-08
Implementacion principal: Gmsh 4.15.2 mediante Python API

## 1. Proposito

El mallador experimental genera mallas hibridas 2D orientadas a RANS/URANS
para perfiles cerrados y perfiles ram-air abiertos. La estrategia compacta
separa tres escalas fisicas:

1. capa limite cuadrilateral pegada a pared;
2. triangulos de transicion y volumen proximo;
3. crecimiento suave hasta un farfield circular de hasta 50 cuerdas.

La configuracion se almacena con cada revision. Se puede cargar una revision,
editarla, generar una nueva sin sobrescribir la anterior, aprobarla despues de
`checkMesh` y compararla con otras mallas.

## 2. Geometria y topologia abierta

El contorno abierto y el contorno base cerrado se leen de
`profile_points.csv`. Antes de mallar se eliminan duplicados consecutivos y se
rechazan puntos no finitos o segmentos de longitud cero. La recuperacion de
coordenadas verifica que ambos perfiles proceden del mismo contorno y que las
ramas coinciden, dentro de tolerancia, hasta los labios del corte.

El perfil cortado importado es la unica geometria de pared fisica. La opcion de
extension de inlet/LE o TE modifica la longitud usada para calcular divisiones,
`Bump` o `Progression`; no desplaza los labios ni ensancha el corte. El informe
registra `extension_changes_wall_geometry: false`.

Para continuar la capa limite a traves del inlet se usa la seccion exacta del
perfil base cerrado y dos conectores C1 locales hacia los labios. La guia base
no se deforma al aumentar la discretizacion. La continuidad se comprueba por
posicion, tangente, espaciado en extremos y ausencia de segmentos nulos. Este
modelo evita el antiguo puente libre, que podia introducir un salto de
curvatura en el labio superior.

La malla abierta se genera primero con una interfaz temporal coincidente entre
fluido exterior e interior. Despues se separan las caras de pared de espesor
cero conservando conectividad tangencial. Las superficies resultantes son
`fluid_external` y `fluid_internal`; el inlet sigue siendo comunicacion entre
fluidos, no una boundary condition artificial.

## 3. Discretizacion tangencial

La pared se divide en segmentos fisicos (inlet/LE, extrados, cierre TE e
intrados). Hay dos estrategias:

- cuatro distribuciones `Bump`, con refinamiento controlado hacia los extremos;
- `Bump` mas `Progression`, que divide un segmento para controlar por separado
  la aproximacion al punto de maxima curvatura y la salida hacia el cuerpo.

El ajuste automatico resuelve divisiones y coeficientes para respetar:

- longitud de arco real, incluida la extension seleccionada;
- espaciado objetivo en ambos extremos;
- crecimiento maximo entre celdas contiguas;
- limites de tamano tangencial minimo y maximo.

Los tamanos minimo/maximo son cotas de seguridad y permiten derivar un rango de
nodos; no cambian la curva geometrica. Un valor demasiado pequeno puede revelar
un defecto ya presente en la geometria o forzar una BL dificil de cerrar, pero
no crea por si mismo una curvatura. El cierre TE se gobierna por las divisiones
del segmento TE dentro del matching tangencial; las cotas min/max solo validan
su espaciado.

Las extensiones se incluyen en la misma distancia de matching. Por eso los
coeficientes se recalculan en los nuevos extremos y no se agregan segmentos
independientes con un salto de tamano.

Referencia: [Gmsh, transfinite curves y mesh size fields](https://gmsh.info/doc/texinfo/gmsh.html).

## 4. Capa limite y calculo de y+

La velocidad y propiedades del caso producen:

```text
Re_x  = rho U x / mu
Cf    = (2 log10(Re_x) - 0.65)^(-2.3)
tau_w = Cf (rho U^2 / 2)
u_tau = sqrt(tau_w / rho)
y_wall = yPlus mu / (rho u_tau)
```

OpenFOAM es volumen finito y almacena variables en el centro de celda. Por
ello `y_wall` es pared-centro y la altura completa solicitada a Gmsh es
`h1 = 2 y_wall`. El factor dos se aplica una sola vez.

El espesor turbulento de referencia a `x/c=1` es:

```text
delta_99 = 0.37 c / Re_c^(1/5)
Thickness = delta_99 * factor_de_seguridad
```

Con ley Beta, el usuario fija y+, numero de capas y factor de seguridad. Beta
no es editable: se obtiene por biseccion de

```text
h1/Thickness = 1 + Beta*tanh[(1/N - 1)*atanh(1/Beta)], Beta > 1
```

y se pasa explicitamente a Gmsh como `BetaLaw=1`, `Beta`, `Size=h1`,
`NbLayers=N` y `Thickness`. Se valida `h1*N < Thickness`.

Con progresion geometrica, el usuario fija GR y espesor; el numero entero de
capas se deriva de la suma geometrica para alcanzar o superar el espesor. Los
controles Beta se ocultan en este modo y viceversa.

El caso patron M=0.15, Re aproximadamente 1.9e6, c=1 m, y+=1 y factor 1.2
produce aproximadamente `y_wall=12.85 um`, `h1=25.70 um`,
`Thickness=0.02464 m`, 75 capas y `Beta=1.01574`.

Referencia de la estimacion: [CFD-Online, y+ wall distance](https://www.cfd-online.com/Wiki/Y_plus_wall_distance_estimation).

## 5. Volumen exterior

La primera fila de triangulos tras la BL puede fijarse manualmente o derivarse
del espaciado tangencial local. En modo automatico se toma la menor escala de
los segmentos (inlet, TE y cuerpos) multiplicada por un factor editable. Se
aplican fuentes locales, de modo que una pared muy discretizada recibe
triangulos menores sin imponer ese coste a toda la superficie.

La transicion posterior combina campos `Distance`, `Threshold`, `MathEval`,
`Extend` y `Min`. El crecimiento radial esta acotado y se prolonga hasta el
tamano de farfield, evitando una meseta fina en la mayor parte del dominio. El
tamano de farfield, las distancias de transicion, el crecimiento y el factor de
interfaz son editables en rangos amplios.

`Frontal-Delaunay` (algoritmo 6) es el valor habitual para calidad; Delaunay
(5) queda disponible para campos con gradientes complejos. El mallador activa
smoothing y optimizacion/relocation segun la configuracion. Gmsh recomienda
desactivar fuentes de tamano implicitas cuando un background field define por
completo el mallado, para evitar refinamiento no deseado.

## 6. Volumen interior

Los controles interiores tienen estas funciones:

| Control | Significado fisico/numerico |
|---|---|
| Factor de ajuste al inlet | Multiplica el espaciado tangencial de labios para fijar los primeros triangulos; menor implica mas refinamiento local. |
| Tamano del nucleo | Objetivo lejos de paredes dentro de la cavidad; ahorra celdas donde el flujo es menos sensible. |
| Zona fina tras inlet | Longitud a lo largo de la cuerda durante la que se conserva el refinamiento de labios. |
| Transicion interior | Distancia usada para crecer suavemente desde inlet/pared hacia el nucleo. |
| Tamano junto a pared interna | Escala de los triangulos pegados a la pared interna compartida. |
| Transicion desde pared | Anchura del crecimiento entre la escala de pared y el nucleo. |
| Tamano TE interno | Objetivo local en la cavidad junto al cierre estrecho del TE. |
| Transicion TE interna | Radio/longitud sobre la que ese refinamiento vuelve al tamano del nucleo. |

La pared interna de espesor cero comparte los nodos tangenciales de la externa;
no puede discretizarse de forma independiente sin romper la conformidad. El
ahorro interior se obtiene mediante el campo volumetrico, no duplicando nodos
de pared.

## 7. Calidad, revision y aprobacion

La cadena ejecuta Gmsh, convierte con `gmshToFoam`, corrige tipos de patch y
ejecuta `checkMesh -allTopology -allGeometry`. Si falla, repite con
`-writeSets -writeSurfaces -setFormat vtk` y guarda sets/VTK para abrir las
celdas problematicas en ParaView.

El resumen incluye celdas, no ortogonalidad, skewness, aspect ratio,
interpolation weight, volume ratio, determinant, regiones BL/interior/exterior,
y1 real, espesor real y distribucion empleada. El estudio completo de tablas se
ejecuta manualmente para no penalizar cada iteracion de malla. Agrupa metricas
por intervalos y genera tablas PNG de estilo tecnico.

El aspect ratio se interpreta por region: 1000-1500 puede ser admisible en
quads alineados de BL RANS, mientras que los triangulos fuera de BL deben
mantenerse normalmente por debajo de 20-50. Interpolation weight, determinant
y volume ratio se consideran especialmente criticos para estabilidad de
URANS, junto con transiciones suaves en inlet, TE y frente externo de BL.

`checkMesh OK` es condicion necesaria para aprobar desde la app, pero la
seleccion final tambien exige inspeccion VTK, calidad por region, sensibilidad
de malla y una prueba acotada de solver.

## 8. Evidencia y fuentes

Codigo:

- `CFD_2D/scripts/ramair_2d_open_experimental_mesh.py`
- `CFD_2D/scripts/ramair_2d_closed_experimental_mesh.py`
- `CFD_2D/scripts/boundary_layer_estimates.py`
- `CFD_2D/scripts/ramair_2d_bump_matching.py`
- `CFD_2D/scripts/ramair_2d_mesh_quality_distributions.py`
- `CFD_2D/app/open_experimental_mesh_page.py`
- `CFD_2D/app/closed_experimental_mesh_page.py`

Auditorias:

- `CFD_2D/reports/grid_quality_guidelines_ghoreyshi.md`
- `CFD_2D/reports/OPEN_RAMAIR_ZERO_THICKNESS_BASE_PROFILE_AUDIT_20260724.md`
- `CFD_2D/reports/OPEN_INLET_CURVATURE_LAYER_STUDY_20260722.md`
- `CFD_2D/reports/OPENFOAM_BASE_AND_FINAL_MESH_AUDIT_20260901.md`

Documentacion externa:

- [Manual de Gmsh 4.15](https://gmsh.info/doc/texinfo/gmsh.html)
- [OpenFOAM v14: descripcion de malla](https://doc.cfd.direct/openfoam/user-guide-v14/mesh-description)
- R. M. Cummings et al., DOI [10.1016/j.paerosci.2008.01.001](https://doi.org/10.1016/j.paerosci.2008.01.001)
