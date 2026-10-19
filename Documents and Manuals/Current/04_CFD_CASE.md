# Condiciones físicas del caso CFD

API 28 · Manual vigente · 2026-10-19.

## Responsabilidad

Un caso CFD reúne las condiciones comunes a todas sus geometrías, mallas y configuraciones OpenFOAM. Los ángulos se eligen posteriormente al generar el solver.

## Entradas

Nombre, Reynolds, Mach, presión absoluta y temperatura. Defaults: Re=1.9e6, M=0.15, P=101325 Pa, T=288.15 K. Se rechazan valores no positivos o no finitos.

## Relaciones físicas

c=1 m; R=287.05 J/(kg K); gamma=1.4; rho=P/(RT); a=sqrt(gamma RT); U=Ma; nu=Uc/Re; mu=rho nu. La viscosidad se ajusta para cumplir simultáneamente Re y Mach. No es una aplicación de la ley de Sutherland.

## Archivos y funciones

physical_conditions y create_cfd_case escriben CFD_2D/<caso>/cfd_case.json, con propiedades derivadas, política e identidad. geometry_context propaga esa física a BL y al case writer. Geometries comienza vacía hasta importar perfiles.

## Comparación

Se permite comparar configuraciones con la misma identidad CFD y revisión física. Dos carpetas parecidas no se mezclan automáticamente. Para comparar variantes créelas bajo un mismo caso.

## Alcance del modelo

El preset actual es incompresible con SA. La UI avisa por Mach elevado. Crear condiciones no certifica la pertinencia del modelo ni valida sus resultados.
