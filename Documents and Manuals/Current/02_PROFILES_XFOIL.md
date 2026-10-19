# Perfiles 2D, XFOIL y tratamiento del TE

API 28 · Manual vigente · 2026-10-19.

## Independencia

Esta sección funciona sin caso de trabajo. Airfoil Profiles/Reference contiene bases y referencias; Processed variantes preparadas; Designs la evidencia de XFOIL. CAD y CFD consumen esa biblioteca sin depender entre sí.

## Lectura

Se admiten DAT en orden XFOIL y CSV con columnas reconocidas. El usuario indica abierto o cerrado al importar para evitar ambigüedades. Un DAT cerrado continuo se divide en su punto real de mínimo x, compartido por ambas ramas. Los CSV propios conservan UPPER, LOWER y TE_ROUNDED_CAP.

## Diseño del corte

ramair_2d_inlet_designer.design_inlet ejecuta repanelado, polar y exportación de Cp. standard_full_polar utiliza la envolvente de estancamiento de todos los puntos convergidos del barrido; optimized_cl_window la restringe a CL mínimo/máximo. Polar completa significa barrido solicitado, no convergencia garantizada de cada ángulo. Se guardan los recuentos y logs.

## Configuración

Re, Mach, alpha inicial/final/paso, intervalo CL, panel_count, bunching y densidades de panelado, límite de iteraciones, timeout, región de búsqueda del estancamiento, Cp mínimo y margen de paneles. La UI presenta modos discretos y edita los restantes valores con sus tipos.

## Base exacta

La referencia de un corte es el contorno realmente repanelado por XFOIL, guardado por huella. Los labios se proyectan sobre esa base; ramas retenidas y cap son compartidos. Se rechazan bases incompatibles. El corte no utiliza un perfil de otro caso para ocultar diferencias.

## Cierre y archivos

bounded_te recorta anclajes posteriores y construye un cap con dos Bézier cúbicos tangentes, limitado a x/c≤1. El extremo posterior es 1; el resto del contorno se conserva. straight y sharp ofrecen cierre recto y punta. Cada carpeta contiene original, profile_points.csv, DAT preparado y profile.json con fuente, SHA-256, identidad, topología y método. import_geometry copia puntos a Inputs/profile e Inputs/base de cada caso CFD.
