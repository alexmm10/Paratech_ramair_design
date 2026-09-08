# RamAir CFD 2D: configuracion y ejecucion OpenFOAM

Fecha de auditoria: 2026-09-08
Stack verificado: OpenFOAM Foundation 14 y OpenMPI 4.1.2

## 1. Modelo fisico y referencias

La configuracion desarrollada usa `foamRun -solver incompressibleFluid` para
flujo incompresible 2D. La turbulencia activa es Spalart-Allmaras. El escritor
rechaza otros modelos mientras no exista la generacion completa y verificada de
sus campos, evitando un selector visual que produzca un caso inconsistente.

La velocidad puede derivarse de Reynolds, Mach o un valor impuesto. El caso
registra simultaneamente `U_Re`, `U_Mach`, Reynolds resultante y las propiedades
que harian ambas condiciones compatibles. Una discrepancia superior al 5 % se
muestra como warning; no se oculta ajustando viscosidad o densidad en silencio.

Las magnitudes adimensionales usan:

```text
Re = rho U_inf c / mu
q  = rho U_inf^2 / 2
t* = t U_inf / c
dt* = dt U_inf / c
St = f c / U_inf
```

La extrusion tiene una celda en span y patches `empty`. `Aref` es cuerda por
espesor extruido, `lRef=c` y el centro de momentos es configurable, normalmente
`x/c=0.25`. Las direcciones de lift y drag se definen respecto al freestream.

## 2. Condiciones de contorno

El farfield usa la condicion `freestream` como contrato normal, con velocidad
uniforme en la direccion del caso y presion cinematica gauge. Existe un fallback
de velocidad fija solo para compatibilidad controlada.

Las paredes del perfil son no-slip. En topologia cerrada se integra la pared
exterior. En topologia abierta existen patches distintos
`airfoil_wall_external` y `airfoil_wall_internal`; el hueco del inlet comunica
ambos volumenes y no se convierte en pared.

`forceCoeffs` usa la libreria de fuerzas de OpenFOAM. Las fuerzas y momentos
incluyen las contribuciones normal de presion y tangencial viscosa sobre todos
los patches seleccionados. En flujo incompresible, `p` tiene unidades
cinematicas y se combina con `rhoInf` para obtener fuerzas dimensionales y
coeficientes.

Referencia: [OpenFOAM v14, fuerzas y coeficientes](https://doc.cfd.direct/openfoam/user-guide-v14/post-processing-functionality).

## 3. Inicializacion RANS/SIMPLE

La fase RANS desarrolla presion, capa limite y `nuTilda` antes de integrar el
tiempo fisico. En Validation Lab el maximo normal es 15 000 iteraciones; en las
bases de convergencia el contrato es un bloque fijo de 20 000 iteraciones. El
laboratorio separa el dato numerico de la decision humana: puede informar
reduccion residual y estabilidad de coeficientes sin declarar por si mismo que
el caso es fisicamente aceptable.

Los factores de relajacion de la campana simplificada son `p=0.3`, `U=0.7` y
`nuTilda=0.7`. Los historiales de residuales y Cl/Cd/Cm se escriben cada
iteracion; los campos volumetricos se guardan con una cadencia mas espaciada.

La reanudacion RANS conserva el contador de iteracion y el ultimo campo
completo. El monitor usa iteracion en x, siempre desde cero, y muestra angulo,
malla, topologia, ranks, coste por paso y estabilidad.

## 4. Integracion URANS/PIMPLE

URANS parte obligatoriamente de un checkpoint RANS real. Al copiarlo a tiempo
cero se conservan `U`, `p`, `nuTilda` y `nut`; se eliminan `uniform/time`, `phi`
y campos derivados incompatibles. Esto evita que un indice SIMPLE, un flujo de
caras obsoleto o el tiempo 20 000 se interpreten como estado transitorio.

La secuencia progresiva A-B-C-D-E usa Euler en la rampa y `backward` para
asentamiento/produccion:

- A, B y C aumentan gradualmente `dt` y pueden usar control de Courant;
- antes de `backward` se verifican tres estados temporales compatibles y, si
  faltan, se genera un bootstrap corto;
- D es asentamiento, por defecto `t*=10`;
- E es produccion, por defecto `t*=50` en validacion; el paquete Cummings
  activo usa `t*=100` para una ventana espectral comun mas larga;
- el promedio comienza al inicio de E, no durante la rampa o D.

La escritura adaptable se alinea ahora con el tiempo absoluto final de cada
fase. Cuando hay que conservar historial para `backward`, el intervalo se
ajusta levemente para dividir exactamente el objetivo y mantener al menos tres
estados. Esto corrige el fallo en que C terminaba numericamente pero quedaba a
una fraccion de `dt` del checkpoint exigido.

PIMPLE admite hasta cinco correctores externos en las configuraciones de
validacion, con salida anticipada por residual para no pagar correctores
innecesarios. El estudio Cummings congela `dt`, esquema y correctores para que
las comparaciones sean controladas. La campana incluye sensibilidad con 2, 3 y
4 correctores y compara coeficientes, coste y orden de reduccion residual.

Referencia: [OpenFOAM v14, SIMPLE y PIMPLE](https://doc.cfd.direct/openfoam/user-guide-v14/fvsolution).

## 5. Courant, no ortogonalidad y esquemas

En validacion general, `maxDeltaT` es el techo de resolucion fisica y `maxCo`
es una proteccion de estabilidad, no el criterio primario de exactitud. Las
fases abiertas A-C usan actualmente `maxCo=5` porque la prueba real mostro que
un arranque con Co mayor de 10 en las celdas de labios podia colapsar el paso.
La produccion Cummings sigue usando el `dt` fijo estudiado.

El numero de correctores no ortogonales y el laplaciano se leen del
`checkMesh` de la malla concreta:

| Maxima no ortogonalidad | Correctores SIMPLE/PIMPLE | Laplaciano |
|---:|---:|---|
| < 50 grados | 0 | `Gauss linear corrected` |
| 50 a < 70 grados | 1 | `Gauss linear corrected` |
| >= 70 grados | 2 | `Gauss linear limited 0.5` |

El mismo valor efectivo se escribe en SIMPLE y PIMPLE; los valores antiguos de
configuracion no pueden sobreescribir el resultado automatico sin seleccionar
explicitamente modo manual.

## 6. Convergencia espacial y temporal

La convergencia espacial usa niveles coarse, medium y fine con identica
geometria, condiciones y modelos. Se comparan Cl, Cd, Cm y Cl/Cd frente a
medium, numero efectivo de celdas, coste, y+, separacion, GCI y orden observado
cuando los datos permiten calcularlos. El criterio de campana revisa si un
aumento de al menos 30 % en celdas cambia las variables principales menos de
3 %, pero la aceptacion final permanece manual.

La convergencia temporal sigue la idea de Cummings: una escalera de `dt`,
duracion fisica comun, analisis de medias/RMS, espectros y numero de Strouhal.
No se compara un caso que haya cubierto menos tiempo de produccion con otro mas
largo como si fueran equivalentes. La seleccion modular usa topologia, malla,
angulo y luego unicamente los `dt` realmente disponibles.

Referencia metodologica: R. M. Cummings, S. A. Morton y D. R. McDaniel,
[DOI 10.1016/j.paerosci.2008.01.001](https://doi.org/10.1016/j.paerosci.2008.01.001).

## 7. Ejecucion individual, colas y recuperacion

Un caso preparado conserva un plan de fases congelado. El ejecutor comprueba
campos de entrada, tiempo comun entre ranks, historial `backward`, malla y
diccionarios antes de arrancar. Los cambios A-B-C-D-E son parte del mismo run;
no crean casos independientes ni permiten que una fase hija marque la cola
completa como terminada.

Las colas RANS y URANS permiten pausa del caso actual con salto, pausa total,
timeout, continuacion y omision de finalizados. Fallo, divergencia o bloqueo
registrado permiten seguir con el siguiente caso sin intervencion. Una pausa
limpia exige checkpoint antes de ofrecer reanudacion.

Los monitores leen incrementalmente el log y no campos volumetricos. Muestran
angulo, fase, tiempo/iteracion, residuales, Cl/Cd/Cm, Courant, `deltaT`, ranks,
segundos por paso y estadisticas del solver lineal.

## 8. Paralelismo y rendimiento medido

En el Ryzen 7 4800H (8 cores fisicos, 7.5 GiB WSL), el benchmark controlado de
203 691 celdas obtuvo:

| Ranks | s/paso | Speed-up | Eficiencia | core-s/paso |
|---:|---:|---:|---:|---:|
| 1 | 3.742 | 1.000 | 100.0 % | 3.742 |
| 2 | 2.093 | 1.788 | 89.4 % | 4.186 |
| 4 | 1.443 | 2.593 | 64.8 % | 5.772 |
| 8 | 1.229 | 3.045 | 38.1 % | 9.832 |

La politica automatica equilibrada parte de unas 100 000 celdas/rank, limita
ranks a cores fisicos y reutiliza solo perfiles empiricos compatibles con al
menos cinco pasos medidos. Por ello recomienda 2 ranks para unas 204-215k
celdas, 3 para unas 303k y 6 para unas 618k. Ocho ranks minimizan latencia del
caso coarse, pero desperdician CPU si importa el throughput de una campana.

Scotch permanece como descomposicion de produccion. La presion/GAMG es el
cuello de botella observado. La monitorizacion cuesta aproximadamente 0.063 s
por refresco mediano, cerca de 0.21 % de un core a 30 s. El informe completo es
`CFD_2D/reports/OPENFOAM_PERFORMANCE_AUDIT_20260907.md`.

## 9. Prueba URANS abierta de esta auditoria

La prueba `open_medium_a08_dt1p224437662261em05` usa una base RANS abierta de
8 grados. El objetivo elegido, `dt=1.22444e-5 s` (`dt*=0.000625`), mantiene un
Courant de produccion estimado alrededor de 34. La alternativa
`dt*=0.01` se rechazo: la malla de labios produciria Co del orden de 550 y la
rampa real mostro inestabilidad.

Tras normalizar el checkpoint, A y B alcanzaron sus objetivos con Co maximo
aproximado 0.7 y 1.7. C alcanzo despues su frontera exacta. El ramp de entrada a
D mostro que el objetivo fijo no era viable: para sostener Co cercano a 10 el
solver redujo `dt` hasta aproximadamente `3.02e-7 s`. El caso queda pausado y
reanudable con esa evidencia; no se fuerza `backward` con un paso incompatible.

## 10. Codigo y evidencia

- `CFD_2D/scripts/ramair_2d_openfoam_case_writer.py`
- `CFD_2D/scripts/ramair_2d_validation_staged_runner.py`
- `CFD_2D/scripts/ramair_2d_openfoam_runner.py`
- `CFD_2D/scripts/ramair_2d_parallel.py`
- `CFD_2D/scripts/ramair_2d_parallel_tuner.py`
- `CFD_2D/scripts/ramair_2d_space_time_convergence.py`
- `CFD_2D/reports/TRANSIENT_TIMESTEP_MESH_SOLVER_STUDY_20260728.md`
- [OpenFOAM Foundation v14 User Guide](https://doc.cfd.direct/openfoam/user-guide-v14/)
