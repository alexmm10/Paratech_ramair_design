# Mantenimiento del proyecto en GitHub

Repositorio: <https://github.com/alexmm10/Paratech_ramair_design.git>

## Que debe guardarse en Git

Git debe contener todo lo necesario para reconstruir la aplicacion, pero no los
resultados de calculo. Se versionan:

- codigo Python de la aplicacion, malladores, runners, monitores y postproceso;
- pruebas automaticas;
- lanzadores de Windows/WSL, Dockerfile y configuracion Docker;
- configuraciones JSON pequenas y presets de malla/solver;
- documentacion, manifiestos de reproducibilidad y datos de referencia CSV;
- perfiles aerodinamicos fuente y scripts CATIA.

El `.gitignore` excluye deliberadamente casos OpenFOAM, mallas, carpetas
`processor*`, VTK, estados ParaView, resultados, logs, paquetes remotos,
entornos virtuales, temporales y copias historicas. Estos datos deben conservarse
en una copia cientifica separada cuando sean importantes. No use `git add -f`
para forzar un archivo ignorado.

La aplicacion canonicamente se edita en Windows. El lanzador sincroniza el codigo
al runtime nativo `~/ramair_cfd/DESIGN_APP` de WSL. No cree un segundo repositorio
Git dentro de WSL ni copie sus carpetas de ejecucion al repositorio.

## Actualizacion habitual

Antes de empezar, actualice la referencia remota y cree una rama corta:

```powershell
cd "C:\Users\alejm\Desktop\PRACTICAS_INVICSA\3D design\DESIGN APP"
git fetch origin
git switch main
git pull --ff-only
git switch -c update/<descripcion-corta>
```

Tras desarrollar y probar, inspeccione primero lo que ha cambiado:

```powershell
git status --short
git diff --stat
git diff
git ls-files --others --exclude-standard
```

Anada de forma explicita solo codigo, configuracion, pruebas y documentacion. Este
ejemplo cubre las areas normales del proyecto sin incluir datos CFD:

```powershell
git add .gitignore .gitattributes `
  CFD_2D/app CFD_2D/scripts CFD_2D/tests `
  CFD_2D/CFD_2D_inputs/config CFD_2D/reference_data `
  "Application Support/Configurations" "Application Support/Tools" `
  "Documents and Manuals/Application" `
  run_ramair_cfd2d_app.py preprocess_ramair_main.py `
  README_PROJECT_STRUCTURE.md PROJECT_CONTEXT_FOR_CODEX.md CHANGELOG.md
```

Revise el contenido preparado antes de crear el commit:

```powershell
git diff --cached --stat
git diff --cached
git diff --cached --name-only
```

Compruebe que no se ha preparado ningun artefacto grande:

```powershell
git diff --cached --name-only | ForEach-Object {
  if (Test-Path -LiteralPath $_) {
    $item = Get-Item -LiteralPath $_
    if ($item.Length -gt 10MB) { $item | Select-Object Length, FullName }
  }
}
```

Si aparece una malla, un caso, un resultado, un ZIP o un dato no previsto,
retirelo del area preparada sin borrar el archivo local:

```powershell
git restore --staged -- "ruta/del/archivo"
```

Cuando la revision sea correcta:

```powershell
git commit -m "Describe la funcionalidad verificada"
git push -u origin HEAD
```

Revise y fusione la rama en GitHub. Despues:

```powershell
git switch main
git pull --ff-only
git branch -d update/<descripcion-corta>
```

## Comprobaciones antes de publicar

1. Ejecute las pruebas afectadas y una prueba de apertura de la aplicacion.
2. Ejecute `python run_ramair_cfd2d_app.py --check-only --no-browser` para validar
   dependencias y sincronizar el codigo con WSL.
3. Confirme con `git status --short` que los ficheros nuevos necesarios aparecen
   en el commit y que no hay datos de ejecucion preparados.
4. Confirme en GitHub que la rama/commit nuevo contiene `CFD_2D/app`,
   `CFD_2D/scripts`, `CFD_2D/tests`, configuraciones y documentacion.
5. Etiquete solo versiones que hayan pasado las pruebas, por ejemplo:

```powershell
git tag validation-lab-2026-09
git push origin validation-lab-2026-09
```

## Traslado a otro ordenador

```powershell
git clone https://github.com/alexmm10/Paratech_ramair_design.git
cd Paratech_ramair_design
python run_ramair_cfd2d_app.py
```

El lanzador informa de dependencias ausentes de WSL, OpenFOAM, Gmsh, ParaView o
Python. Las mallas aprobadas y los resultados no se descargan desde Git: deben
transferirse desde la copia de datos o mediante los paquetes remotos de la app.

## Reglas de recuperacion

- No use `git reset --hard` para resolver un problema de datos de la aplicacion.
- No haga `push --force` sobre `main`.
- Mantenga la evidencia CFD aceptada fuera de Git y referenciada por manifiestos.
- Use `git restore --staged` para corregir la seleccion sin borrar trabajo local.
- Un `push` no sustituye la copia separada de resultados y mallas cientificas.
