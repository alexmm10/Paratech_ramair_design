# Git Workflow for RamAir DESIGN APP

## First connection

1. Create an empty private repository in the desired Git hosting account.
2. Open `Entorno > Control de versiones > Configurar Git`.
3. Enter the Git author name, author email and repository clone URL.
4. Select `Crear snapshot` and, when it succeeds, `Publicar snapshots`.

Do not paste a password, token or SSH private key into the application. HTTPS
authentication is handled by the host Git credential manager; SSH uses the
host's existing key configuration.

## Normal updates

Use `Estado Git` to inspect local changes, `Crear snapshot` to commit authored
source/configuration changes, `Actualizar desde remoto` for a fast-forward-only
pull and `Publicar snapshots` to push. Pull is refused when local changes are
not committed so files are never merged implicitly by the application.

Before every snapshot, the artifact audit inspects tracked and untracked
candidates. It rejects generated CAE paths and files larger than 10 MiB.
Msh/VTK/OpenFOAM fields, Results, Previous Versions, application state, ZIP
packages and third-party PDFs remain local and are never included.

## Command-line equivalent

```powershell
python "Application Support/Tools/project_git.py" configure --name "Your Name" --email "you@example.com" --remote "https://github.com/user/ramair-design-cfd.git"
python "Application Support/Tools/project_git.py" snapshot --message "Describe the change"
python "Application Support/Tools/project_git.py" push
```
