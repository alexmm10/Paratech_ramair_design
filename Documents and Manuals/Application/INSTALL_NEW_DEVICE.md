# Instalacion en un dispositivo nuevo

## Entorno soportado

La interfaz se abre en Windows, pero Gmsh, PyFoam y OpenFOAM se ejecutan en una
copia del proyecto situada en el filesystem Linux de WSL2. No depende del
usuario, de OneDrive ni de una ruta absoluta concreta.

Requisitos del sistema:

- Windows 10/11 con WSL2 y Ubuntu 22.04, o Linux compatible;
- Python 3.10 o posterior y `python3-venv` dentro de WSL;
- OpenFOAM Foundation 14 con su `etc/bashrc` bajo `~/.local/opt/openfoam14`,
  `/opt/openfoam*` o `/usr/lib/openfoam/openfoam*`;
- ParaView/WSLg opcional para visualizacion interactiva.

El instalador pregunta antes de modificar Ubuntu. Si se acepta, puede usar
`sudo apt` para los paquetes disponibles y muestra previamente que Ubuntu
puede solicitar la contrasena Linux. El bootstrap prioriza `openfoam14` cuando
su repositorio esta configurado; OpenFOAM 13 es solo un fallback compatible.
Si el repositorio no existe, se muestra la instruccion oficial y nunca se crean
placeholders para simular una instalacion correcta.

## Crear el paquete transportable

Desde la raiz del proyecto:

```powershell
python ".\Application Support\Tools\package_ramair_project.py"
```

El ZIP contiene codigo, configuraciones, perfiles, tests, CATScript, manuales y
scripts de instalacion. Excluye entornos virtuales, mallas, resultados, logs y
backups pesados. Para una copia de archivo completa:

```powershell
python ".\Application Support\Tools\package_ramair_project.py" --include-generated
```

## Primera ejecucion en Windows

Descomprime el ZIP. El acceso habitual y unico es hacer doble clic en:

```text
START_RAMAIR_CFD2D_APP.bat
```

En la primera instalacion `START_RAMAIR_CFD2D_APP.bat` detecta que falta el
marcador de entorno y pregunta si debe instalarlo. La opcion recomendada es
equivalente a:

```powershell
python .\run_ramair_cfd2d_app.py --install --install-system
```

Si `~/ramair_cfd/DESIGN APP` no existe, el launcher crea una copia limpia en
WSL, crea el layout, instala las versiones Python fijadas,
verifica Gmsh/OpenFOAM/PyFoam y abre Streamlit. Para otra ubicacion:

```powershell
python .\run_ramair_cfd2d_app.py --install --wsl-project-root ~/projects/ramair/DESIGN APP
```

No se sobrescribe una copia WSL existente durante el arranque normal.
El lanzador si actualiza de forma controlada `CFD_2D/app`, `CFD_2D/scripts`,
tests y entrypoints desde la carpeta Windows que contiene el launcher. La
actualizacion se valida en una carpeta temporal antes de activarse. No copia ni
sobrescribe `Application Support/Configurations/`, el JSON editable de malla, perfiles, mallas, casos,
resultados o logs. Esto evita ejecutar una interfaz nueva con un backend antiguo.

La carpeta Windows es la fuente de verdad del codigo. La copia nativa WSL es la
fuente de verdad de configuraciones editables activas y productos pesados. Cada
inicio normal sincroniza el codigo bajo un bloqueo `flock`, usa temporales
unicos y escribe `CFD_2D/app_state/runtime_sync_manifest.json`. Dos dobles clics
simultaneos no pueden borrar el staging del otro ni iniciar dos Streamlit en el
mismo puerto.

Para diagnosticar deliberadamente una copia WSL sin sincronizar codigo:

```powershell
python .\run_ramair_cfd2d_app.py --no-sync-code --no-browser
```

No uses esa opcion como arranque habitual.

## Primera ejecucion directamente en WSL/Linux

```bash
cd /ruta/al/proyecto/DESIGN APP
bash "Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh" --install
bash "Documents and Manuals/Application/run_cfd2d_app_wsl.sh" 8501
```

Verificacion independiente:

```bash
.venv-cfd2d-ui/bin/python CFD_2D/scripts/initialize_project_layout.py --create
.venv-cfd2d-ui/bin/python CFD_2D/scripts/check_environment.py
```

Los perfiles y configuraciones usan rutas relativas a la raiz. Los manifests
generados pueden registrar rutas absolutas como trazabilidad de una ejecucion,
pero no se reutilizan para localizar entradas en otro dispositivo.

## Dependencias del sistema

`--install` automatiza el entorno Python aislado, Gmsh y el XFOIL incluido en
el proyecto. `--install-system` agrega los paquetes Ubuntu disponibles y puede
solicitar `sudo`. Cada `MISSING` o `WARNING` incluye una linea `ACTION` y el
comando de reparacion. Si el arranque falla, el launcher deja de esperar en
cuanto termina el proceso y ofrece una reparacion completa seguida de un unico
reintento.

La verificacion incluye la compatibilidad interna `app/backend API`. Si falla,
reinicia desde Windows sin `--no-sync-code`; no reinstales paquetes Python para
resolver una copia de codigo incoherente.

## Paquete CATIA independiente

Para un PC Windows con CATIA V5 que no necesita OpenFOAM:

```powershell
python ".\Application Support\Tools\package_ramair_catia_windows.py"
```

El ZIP verificado se escribe en `Application Support\Packages\`. Incluye el
preprocesador, CATScript, perfiles, configuraciones relativas, `CATIA_inputs`
recién regenerado, scripts de instalación Windows y hashes SHA-256. Consulta
`Documents and Manuals/Application/README_CATIA_WINDOWS_PACKAGE.md`. El generador no ejecuta CATIA.
