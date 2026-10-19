# Registro de cambios

## [Unreleased]

Sin cambios pendientes de publicación en esta entrega. Registrar aquí el desarrollo posterior.

## [2026-10-19]

- Entrega completa en un único ZIP: código, instalador verificable Windows/WSL, manuales, bibliotecas y ejemplo importable. Instalación repetible que conserva los cambios del usuario y evita colisiones de nombres.
- Catálogo de presets de estudio para CAD, condiciones físicas, tres mallas aprobadas, solver/fases y postproceso. Las configuraciones nuevas usan esos valores; las existentes conservan sus ajustes.
- Ejemplo abierto optimizado con malla de 248180 celdas, 26 ángulos preparados desde campos iniciales y 9 puntos RANS retenidos. Las figuras y tablas se consultan sin distribuir checkpoints volumétricos.
- Protección de snapshots reducidos: el postproceso identifica campos omitidos y no sustituye las figuras conservadas por resultados calculados desde campos iniciales.
- Galería de polares/comparaciones/validación con procedencia, manual operativo por sección y referencia completa de parámetros. Recuperación de opciones de postproceso guardadas, incluido inicio de promedio RANS en iteración cero.
- Sincronización de presets de estudio, aplicación explícita de RANS-only y selección inicial del perfil optimizado. Los historiales de presets ya no cuentan como presets activos.

## [2026-09-30]

- Consolidación de RamAir Studio API 28: workspaces independientes, biblioteca de perfiles, controles modulares CAD/mesh/CFD, calidad local de malla, recuperación y postproceso RANS/URANS y comparación de polares.
- Suspensión schema 2: selección estructural independiente de frenos, porcentaje exterior o celdas explícitas, cobertura no uniforme, asimetría, migración de configuraciones, plantillas A–B/C–D y una confluencia BRK por lado, agrupación combinada y edición manual. Diagnósticos en planta/frontal/lateral y validación de la red.
- Nueva pestaña de convergencia de malla RANS en ejecución general: estudio persistente, firmas y estados, copias de solver, cola de recursos, pausa/continuación, tablas y 12 figuras, diferencias/GCI condicionado y paquete remoto RANS-only.
- Escritura final asegurada en ejecuciones RANS breves; retención de campos de iteración para continuar RANS-only, orden de celdas preservado, separación del origen temporal URANS.
- Paquetes remotos: continuación explícita de ejecuciones terminadas, rutas reubicables, dependencias fijadas, devolución/importación de postproceso, clasificación diagnóstica y rechazo de importación sobre solver activo.
- Monitor de checkpoints sin falsos estados activos; cierre de logs coordinado con el gestor; espectros constantes identificados sin escalas logarítmicas inválidas; referencias de perfiles y presets portables.
- Rendimiento desde documentación Current y hardware actual, ausencia de Courant/dt diferenciada de cero; intención de parada persistida antes de interrumpir el proceso.
- Limpieza de herramientas e informes puntuales a archivo externo inventariado; documentación vigente unificada, instaladores, manifiestos completos, pruebas de otra ubicación y automatización GitHub.
- Pruebas de geometría autónomas: crean sus propios exports desde perfiles distribuidos, sin requerir casos históricos ni carpetas vacías del equipo de desarrollo.
- Pruebas reales: dos mallas × dos ángulos, pausa y continuación; ida/vuelta remota en WSL separado. Preparación Python y macro CATIA sin ejecutar CATIA. Resultados breves sin aceptación científica.

La historia de desarrollo previa permanece en el historial Git y el archivo local externo; no forma parte de la documentación de uso de la versión entregada.
