# Entrega completa a la empresa · 2026.10.19

La entrega actual utiliza **un único ZIP**, RamAir_Studio_20261019_Completo.zip. Sustituye la distribución anterior de aplicación y ejemplo por separado. No se necesita el archivo de desarrollo para instalarla.

El ZIP contiene INSTALAR_Y_ABRIR y ABRIR_RAMAIR, código DESIGN APP, perfiles existentes, presets de CAD/CFD/mallado/solver/postproceso, manuales, galería y workspace abierto. La instalación incorpora el ejemplo y comprueba su malla cuando dispone de OpenFOAM. Conserva los workspaces previos.

El ejemplo incluye LS1 optimizado, malla de 248180 celdas, configuración y 26 ángulos preparados. Conserva 9 puntos RANS de origen y productos seleccionados. Omite los campos volumétricos finales: permite consultar resultados e iniciar cálculos nuevos desde campos iniciales, pero no continuar la campaña original. Los snapshots se identifican expresamente.

Los presets guardan parámetros originales y métricas de calidad; no heredan una aprobación para otro perfil. La galería añade 36 figuras de polares, comparación y validación sin duplicar campañas.

## Recepción

1. Verificar SHA-256 y extraer completamente el ZIP.
2. Disponer de Python Windows y WSL2 Ubuntu 22.04.
3. Ejecutar INSTALAR_Y_ABRIR.bat y revisar el diagnóstico de dependencias.
4. Seleccionar Example_Open_Polar; revisar perfil, CAD, malla, calidad y polar.
5. Crear otro workspace o configuración para cálculos propios y mantener respaldos independientes.

Leer [primer uso](15_FIRST_RUN_AND_DEFAULTS.md), [operaciones por sección](16_USER_OPERATIONS.md) y [parámetros efectivos](17_STUDY_PARAMETER_REFERENCE.md). El informe distingue comprobaciones de software, malla y aceptación científica.

## Equipo receptor

CATIA y su licencia son externos. OpenFOAM Foundation 14, MPI, XFOIL y ParaView se instalan para sus funciones específicas; no se distribuyen entornos instalados ni publicaciones de terceros. El bootstrap descarga las dependencias Python que falten.

Los paquetes Linux remotos se generan desde Ejecución para los casos seleccionados; no son una descarga obligatoria adicional. El servidor real requiere recepción independiente de recursos, permisos y políticas.

## Código y mantenimiento

Git contiene código, defaults, presets, perfiles pequeños, figuras y manuales. El ejemplo pesado se adjunta al ZIP descargable, no al árbol Git. Workspaces, Results y Data Archive propios quedan separados de las actualizaciones.

Para reproducir la entrega, exportar un workspace con el mismo commit y ejecutar Application Support/Tools/package_studio_delivery.py. El builder rechaza un ejemplo de otro commit y genera manifiesto y hash. Verificar con verify_studio_delivery.py y repetir la instalación en una ruta limpia. CATIA no se ejecuta en las verificaciones automáticas; el empaquetado no convierte resultados insuficientes en aceptados.
