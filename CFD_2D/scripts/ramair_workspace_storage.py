"""Small Windows publications; OpenFOAM remains on the native Linux filesystem."""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from urllib.parse import quote
from ramair_workspace import read_json, write_json, file_digest, workspaces

def source_checkout(root: Path) -> Path:
    value = read_json(root / "CFD_2D/app_state/runtime_sync_manifest.json").get("source_checkout")
    path = Path(value) if value else root
    return path if path.is_dir() else root

def windows_workspace(root: Path, workspace: Path) -> Path:
    source = source_checkout(root)
    try:
        relative = workspace.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path("Workspaces") / workspace.name
    return source / relative

def publish_workspace_links(root: Path) -> None:
    source = source_checkout(root)
    if source.resolve() == root.resolve():
        return
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu-22.04")
    for info in workspaces(root):
        workspace = Path(info["path"])
        destination = windows_workspace(root, workspace)
        destination.mkdir(parents=True, exist_ok=True)
        unc = "\\\\wsl.localhost\\" + distro + str(workspace).replace("/", "\\")
        write_json(destination / "runtime.location.json", {"workspace": info["name"], "native_path": str(workspace), "windows_path": unc, "fields_stored_in": "WSL"})
        (destination / "Abrir caso de trabajo en WSL.url").write_text("[InternetShortcut]\nURL=file://wsl.localhost/"+distro+quote(str(workspace))+"\n", encoding="utf-8")
        (destination / "README.txt").write_text("Los casos, mallas y campos de este entorno se conservan en WSL para evitar transferencias durante la simulación.\nUbicación: "+unc+"\nLos productos seleccionados se copian aquí desde Postproceso > Conservar y exportar resultados.\n", encoding="utf-8")

def publish_shared(root: Path, path: Path) -> Path:
    source = source_checkout(root)
    if source.resolve() == root.resolve():
        return path
    relative = path.resolve().relative_to(root.resolve())
    if relative.parts[0] not in {"Airfoil Profiles", "Mesh Presets", "Validation Gallery"}:
        raise ValueError("Solo se publican bibliotecas compartidas.")
    destination = source / relative
    files = [path] if path.is_file() else list(path.rglob("*"))
    for item in files:
        if not item.is_file():
            continue
        target = destination if path.is_file() else destination / item.relative_to(path)
        if target.exists() and file_digest(item) != file_digest(target):
            # Different small library revisions remain recoverable; never replace
            # results or authoritative Windows source code through this adapter.
            old = target.parent / ".history" / (target.name+"."+file_digest(target)[:12])
            old.parent.mkdir(parents=True,exist_ok=True)
            if not old.exists():
                shutil.copy2(target, old)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(item,target)
    return destination
