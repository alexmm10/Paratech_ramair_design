# RamAir Studio · paquete único y primer uso

Versión 2026.10.19 · Windows + WSL2 Ubuntu 22.04 · API 28.

## Qué se descarga

Se entrega **un único ZIP RamAir_Studio_20261019_Completo.zip**. No es necesario descargar e importar tres paquetes separados. Extraerlo completamente en una carpeta Windows local con permisos de escritura, preferiblemente fuera de OneDrive. No ejecutar desde la vista interna de un ZIP.

| Elemento | Contenido |
|---|---|
| INSTALAR_Y_ABRIR.bat | Verifica integridad, prepara el entorno, incorpora el ejemplo y abre la aplicación. |
| ABRIR_RAMAIR.bat | Arranques posteriores; conserva la ubicación elegida durante la instalación. |
| DESIGN APP | Código, macro CATIA, bibliotecas de perfiles, presets, manuales, pruebas y herramientas. |
| Content/Example_Open_Polar.zip | Workspace exportado con geometría abierta, malla, configuraciones, campos iniciales y resultados seleccionados. |
| DELIVERY_MANIFEST.json | Versión, commit, archivos, tamaños y hashes SHA-256. |

El archivo `.sha256` que acompaña la descarga sirve para comprobar su integridad antes de extraer. El instalador comprueba además los archivos internos. La carpeta de entrega original puede conservarse como respaldo. No se incluyen licencias, publicaciones de terceros, entornos instalados ni campañas completas.

El ejemplo instalado ocupa aproximadamente 4 GB porque cada caso conserva una copia independiente de su malla. Prever espacio adicional para el entorno y futuras simulaciones. La importación deduplica únicamente el área temporal; no comparte archivos finales entre casos que puedan modificarse accidentalmente.

## Instalación paso a paso

1. Instalar Python 3.10 o posterior en Windows para el lanzador. La instalación científica Linux se ha comprobado con Python 3.10. La máquina debe disponer de espacio suficiente en el disco Linux para sus futuros campos CFD, que ocuparán mucho más que el ZIP.
2. Activar WSL2 y disponer de Ubuntu 22.04. Si falta, abrir PowerShell como administrador y usar `wsl --install -d Ubuntu-22.04`; completar el usuario Linux y reiniciar si Windows lo solicita.
3. Abrir **INSTALAR_Y_ABRIR.bat**. El bootstrap instalará las dependencias Python declaradas. Puede pedir permisos o la contraseña Linux para dependencias del sistema. Requiere conexión para descargar paquetes que todavía no estén instalados; la entrega no contiene un repositorio offline de todos los programas externos.
4. Instalar o configurar **OpenFOAM Foundation 14 y MPI** para ejecutar CFD. Gmsh **4.15.2** está declarado en el entorno Python. XFOIL se utiliza para diseñar cortes; ParaView para los campos e imágenes asociados. El diagnóstico de Inicio identifica las herramientas ausentes. Consultar el manual de instalación para sus rutas y repositorios oficiales.
5. El código se sincroniza a `~/ramair_cfd/DESIGN_APP` y la ejecución permanece en disco Linux nativo. El instalador importa el ejemplo sin sobrescribir workspaces previos. Si OpenFOAM está disponible, repite `checkMesh` sobre la malla real y registra su aprobación. Si no está disponible o falla la comprobación, no inventa una aprobación: puede consultar geometrías y figuras y completar la comprobación desde Mallado cuando se resuelva la dependencia.
6. La interfaz abre en `http://localhost:8501`. Para usos posteriores utilizar **ABRIR_RAMAIR.bat**. No cerrar la terminal del servidor mientras trabaja en el navegador.

Otra ubicación Linux: desde la carpeta extraída, ejecutar `INSTALAR_Y_ABRIR.bat --wsl-project-root ~/ramair_empresa/DESIGN_APP`. Usar una ruta Linux sin espacios. El recibo INSTALLATION_RECEIPT.json recuerda la ubicación y distribución para ABRIR_RAMAIR.bat. Instalar una nueva versión conserva los workspaces; reinstalar el mismo ejemplo no deshace las modificaciones del usuario.

CATIA V5 y su licencia se instalan por separado. La preparación Python no inicia CATIA; la macro debe comprobarse en la instalación receptora. El procedimiento remoto requiere además la aceptación de permisos, almacenamiento, MPI y políticas del servidor empresarial.

## Valores de partida incluidos

Las entradas se pueden editar en cada menú. **Los presets se aplican a configuraciones nuevas; no sustituyen una configuración existente.** La procedencia y calidad originales están en Study Presets/provenance.json y mesh_quality_reference.json.

| Sección | Base entregada | Qué modifica el usuario |
|---|---|---|
| Perfiles | Bases LS1 y variantes existentes, abiertas estándar/optimizadas y TE preparados; referencias relativas entre corte y base. | Importar otro DAT/CSV, diseñar un corte y guardar un perfil nuevo. |
| CAD | Configuración del modelo preparado de estudio: dimensiones, forma, suspensión, crossports, paneles y exportaciones. | Cuerda, envergadura, celdas, arco, anclajes, cobertura de frenos y cascadas. |
| Caso CFD | Re=1.9×10⁶, M=0.15, presión y temperatura de estudio; cuerda CFD de 1 m. | Nuevas condiciones. Densidad, velocidad y viscosidad se recalculan con la política documentada. |
| Malla cerrada | Preset medium de estudio, 215058 celdas en la geometría original aprobada. | Tamaños, BL, dominio, TE, transiciones y refinamiento; guardar otra versión. |
| Malla abierta | Default optimizado: 248180 celdas. Alternativa estándar: 265520. | Seleccionar preset compatible y ajustar la malla para la geometría concreta. |
| OpenFOAM | Ajustes efectivos de los estudios cerrado/abierto, modelo y fases conservados. | Residuales, esquemas, fases, escritura y modelo físico. |
| Ejecución | Polar **solo RANS**, sin renumerar celdas. | Ángulos, orden, recursos y límites; iniciar URANS es una elección explícita. |
| Postproceso | Pared, perfiles x/c=0.05/0.1/0.3/0.6/0.9, 80 puntos por normal, últimas 500 muestras RANS, escala exacta; animación y VTK desactivados inicialmente. | Ventanas, estaciones, imágenes, animaciones y exportaciones. |

Los números de celdas y métricas corresponden a las mallas originales. Aplicar un preset a una geometría distinta no garantiza el mismo número ni calidad. La aprobación exige generar y comprobar la nueva malla. En abierto la interfaz muestra explícitamente el límite inicial URANS maxCo=1; el archivo del solver conserva el valor histórico del estudio como procedencia. Un resultado RANS no acredita estabilidad temporal URANS.

## Recorrer el ejemplo sin ejecutar un solver

1. En **Caso de trabajo**, seleccionar **Example_Open_Polar**. Si ya existía un workspace con ese nombre, el instalador crea un nombre con sufijo y conserva el anterior.
2. **Geometría CATIA:** seleccionar el modelo preparado y revisar sus vistas, configuración e Inputs. La macro es una salida para uso manual en CATIA.
3. **Mallado:** seleccionar las condiciones del ejemplo y LS1_optimized. Revisar el perfil, las dimensiones del dominio y la malla Polar_RANS_medium_20260913. Consultar sus métricas y, cuando corresponda, repetir la comprobación y aprobación.
4. **Caso OpenFOAM:** consultar RANS_polar_20260913 y su configuración. La lista de ángulos preparada comprende −5° a 20° en pasos de 1°. La polar conservada incluye únicamente los puntos que disponían de resultados revisados; los ángulos sin resultado no se rellenan ni extrapolan.
5. **Postproceso → Polar y resultados:** se selecciona RANS inicialmente para este setup. Consultar la tabla y las gráficas CL–α, CD–α, CM–α y CL/CD–α. La sección de comparación permite usar otros setups compatibles.
6. **Postproceso y análisis:** las tablas y figuras por ángulo se conservan. No se incluyen campos volumétricos de la campaña original. Al solicitar su recálculo antes de ejecutar, la aplicación devuelve RETAINED_RESULTS_ONLY y conserva las figuras; no procesa los campos uniformes iniciales como si fueran resultados CFD.
7. **Inicio → Contenido incluido** o **Validation & Convergence Lab → Galería:** consultar las figuras de las polares estándar/optimizada, comparación y verificación conservadas. Esta galería funciona sin importar ni ejecutar una simulación.

Los datos conservan su alcance original. No se ha realizado una nueva campaña científica al empaquetarlos, ni se declara independencia de malla por disponer de figuras.

## Ejecutar una simulación propia

Crear otro workspace o una configuración con nombre nuevo. Seleccionar/importar perfil → crear condiciones CFD → importar geometría → cargar preset → generar malla → comprobar calidad → aprobar → preparar OpenFOAM → seleccionar ángulos y recursos → comprobar el plan → activar ejecución real cuando proceda.

El ejemplo incluye **campos iniciales reales**, no los checkpoints volumétricos finales: una ejecución nueva arranca desde ellos. La continuación del estudio de origen no está disponible en esta entrega reducida. Para continuar su campaña real, exportar un workspace propio incluyendo checkpoints y seguir el manual de recuperación. No utilizar una tabla de coeficientes como sustituto de campos U/p/turbulencia.

Después de una ejecución propia, revisar convergencia, postprocesar, incluir los puntos revisados en la polar y exportar los productos. Conservar los campos que necesite para continuación o nuevo cálculo de contornos. La galería conserva imágenes, pero no permite reconstruir información volumétrica omitida.

## Manuales y continuidad

En DESIGN APP/Documents and Manuals/Current hay manuales por sección. **16_USER_OPERATIONS.md** explica acciones, entradas, salidas y errores; **17_STUDY_PARAMETER_REFERENCE.md** inventaría los parámetros de partida y sus valores. CODE_INVENTORY.md y FUNCTION_INVENTORY.csv permiten continuar el desarrollo. AGENTS.md, PROJECT_CONTEXT_FOR_CODEX.md y CHANGELOG.md describen contratos y mantenimiento.

Para actualizar: conservar Workspaces, Results y Data Archive; instalar el código con el lanzador oficial; comprobar dependencias y las mallas trasladadas. No mezclar módulos de versiones distintas. Git respalda software; los datos y checkpoints propios necesitan copia independiente. Exportar desde Casos de trabajo → Trasladar antes de cambiar de equipo.
