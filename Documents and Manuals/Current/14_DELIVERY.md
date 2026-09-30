# Entrega a la empresa e instalación

## Qué enviar

Enviar la carpeta de entrega completa con:

1. **RamAir_Studio_20260930_Application.zip**: código, macro, defaults, bibliotecas pequeñas, presets, pruebas, instaladores y manuales. Es el componente obligatorio.
2. **RamAir_Studio_20260930_Example.zip**: workspace importable con CAD preparado, perfil cerrado, dos mallas y cuatro ejecuciones RANS breves con sus productos. Sirve para recepción; no aporta polares convergidas.
3. **RamAir_Studio_20260930_Remote.zip**: casos demostrativos seleccionados para un Linux remoto y scripts de ejecución/pausa/continuación/retorno. Necesario solo para el procedimiento remoto.
4. **LEEME_ENTREGA.md, DELIVERY_MANIFEST.json y SHA256SUMS.txt**: orden de instalación, versiones, commit y verificación de integridad de los tres paquetes.

El repositorio y la etiqueta de entrega deben apuntar al commit indicado en el manifiesto. Conservar una copia del paquete publicado. No enviar el árbol completo de desarrollo ni .git, venvs, archivo histórico, PDFs de terceros o campañas del usuario por defecto.

## Equipo receptor

Windows con Python 3 y WSL2 Ubuntu 22.04. Usar carpeta Windows local, con permisos de escritura, y disco Linux para mallas/campos. CATIA V5 y su licencia son externos. OpenFOAM Foundation 14/MPI son necesarios para solver; ParaView para productos visuales; XFOIL para diseño de inlet. Gmsh 4.15.2 y Python se instalan con el bootstrap.

Extraer Application y abrir INSTALL_AND_START_RAMAIR_CFD2D_APP.bat. Si falta WSL, seguir el mensaje e instalar/reiniciar Windows. El instalador puede pedir la contraseña Linux para paquetes del sistema. Debe configurarse el repositorio OpenFOAM oficial si aún no existe. Una instalación sin dependencias abre las funciones disponibles e informa cuáles faltan; no simula sin solver.
Para elegir otra ruta Linux: `python run_ramair_cfd2d_app.py --install --wsl-project-root ~/ramair_empresa/DESIGN_APP`. Comprobar después con `--check-only` y abrir sin --install. Las dependencias no se copian desde el equipo de desarrollo.

## Recepción

Importar Example desde Casos de trabajo → Trasladar en un workspace nuevo. Repetir checkMesh y aprobar por contenido las mallas importadas antes de nuevas ejecuciones. Verificar vistas CAD, condiciones, malla, generación OpenFOAM, prueba RANS corta y productos. CATIA se ejecuta manualmente si el receptor desea aceptar la macro en su instalación.
El paquete remoto se extrae aparte y se prueba con la guía 13. Requiere comprobar permisos, MPI, recursos, almacenamiento y políticas del servidor de la empresa. La prueba WSL aislada no sustituye esa recepción.
El ejemplo de esta entrega usa el nombre `Company_Delivery_20260924`; manténgalo al importar si va a usar el paquete remoto ya preparado, para que su retorno encuentre las mismas configuraciones. Dicho paquete conserva checkpoints RANS: use `bash resume_remote.sh` para ampliarlos, luego `bash postprocess_remote.sh` y `bash collect_results.sh`. `run_remote.sh` omite correctamente las ejecuciones ya terminadas. Para otro workspace, genere un paquete remoto nuevo desde su propio estudio.

## Datos científicos propios

Para enviar una campaña real, exportar su workspace aparte y activar inclusión de resultados si necesita continuar campos. Git guarda software, no esa campaña. Mantener biblioteca compartida y configuración exacta. Las aprobaciones se revisan tras traslado; no reemplazar los datos originales hasta verificar la copia.

## Actualizar y desarrollar

Conservar Workspaces/Results/Data Archive, instalar la nueva aplicación sobre código o en otra ruta y sincronizar con el lanzador oficial. No mezclar carpetas app/scripts de versiones distintas. Para desarrollo, clonar main, instalar requirements-development, leer AGENTS/contexto, cambiar módulos/contratos juntos y repetir tests/auditorías. Nunca force-push como procedimiento de actualización.
