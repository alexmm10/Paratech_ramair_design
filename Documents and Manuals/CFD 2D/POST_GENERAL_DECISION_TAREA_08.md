# Tarea 08 — Postproceso general portable

Fecha: 2026-08-20
Estado: implementado y validado de forma acotada
Schema de manifiesto de postproceso: 3
Schema de productos ParaView: 1

## Ventana final

El postproceso ordena y deduplica las muestras finitas de `forceCoeffs`, estima
el paso temporal mediano y separa discontinuidades mayores que cinco pasos.
Selecciona el último tramo continuo y aplica sobre él la fracción final
configurada. Si quedan menos de cinco muestras, amplía la cola hasta ese mínimo
sin salir del tramo. `postprocess_window_manifest.json` conserva tiempos,
umbral, gaps, tamaños y el motivo de selección; no declara settling científico
ni sustituye el futuro gate de convergencia.

`forceCoeffs_raw.csv` permanece completo. Cl, Cd y Cm se representan en paneles
separados con límites derivados de sus datos finitos y margen explícito. El
historial adaptativo se entrega tanto como `deltaT_history.csv` como PNG.

## Portabilidad y conservación

`postprocess_manifest.json` usa rutas relativas a su propia carpeta. La UI
resuelve tanto schema 3 relativo como manifests históricos con rutas absolutas.
`scalar_signal_inventory.json` registra forceCoeffs, probes, solverInfo/logs y
Courant; `purgeWrite` sólo gobierna directorios volumétricos de tiempo.

Los productos ParaView escriben:

- Cp o presión cuando Cp no existe;
- velocidad con streamlines y contornos de magnitud;
- vorticidad y y+ sólo si sus arrays reales están disponibles;
- `visualization_scales.json`, calculado una vez sobre los tiempos elegidos y
  compartido por imágenes finales y fotogramas;
- `.pvsm` sin ruta absoluta al caso y `load_<stage>_portable.py`, que resuelve
  estado y caso desde su propia ubicación.

## Evidencia acotada

Sobre una copia temporal del tutorial cavity de OpenFOAM 14 se ejecutaron dos
pasos escritos y `foamPostProcess -func vorticity -latestTime`. ParaView
5.10.0-RC1/pvbatch terminó con código 0, renderizó dos tiempos, streamlines,
contornos de velocidad y vorticidad, y generó animaciones mediante el fallback
GIF. y+ quedó correctamente `NOT_AVAILABLE` porque el fixture laminar no posee
ese campo. El `.pvsm` no contenía la ruta absoluta temporal y su loader compiló.
No se leyó, modificó ni ejecutó un caso CFD de producción.
