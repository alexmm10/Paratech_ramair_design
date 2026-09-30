"""Export/import named Studio workspaces with verified files and relocatable metadata."""
from __future__ import annotations
import argparse,json,shutil,sys,tempfile,time,zipfile,os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_identity import release_identity
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"CFD_2D/scripts"))
from ramair_workspace import read_json,write_json,file_digest,register_workspace,slug
from ramair_execution_control import pid_is_alive

def is_result(part):
    try:return float(part)>0
    except ValueError:return part.startswith("processor") or part in {"Postprocess","postProcessing","steadyInitialization","VTK","Archive","Queues",".history","__pycache__"}

def export_workspace(workspace: Path, output: Path, include_results=False):
    workspace=workspace.resolve()
    if not (workspace/"workspace.json").is_file():raise ValueError("No es un workspace de Studio.")
    for path in workspace.glob("CFD_2D/*/Geometries/*/Meshes/*/OpenFOAM/*/Angles/*/.ramair_solver_process.json"):
        process=read_json(path);pid=process.get("pid")
        if pid and pid_is_alive(int(pid),process.get("pid_start_token") or process.get("start_token")):raise ValueError("Pausa las ejecuciones antes de exportar.")
    files=[]
    for directory, dirs, names in os.walk(workspace):
        if not include_results:dirs[:]=[d for d in dirs if not is_result(d)]
        for name in names:
            path=Path(directory)/name
            if path.resolve()==output.resolve() or path.is_symlink():continue
            if not include_results and (name.startswith("log.") or name.endswith((".log",".logfile",".pyc")) or name.startswith(".ramair")):continue
            files.append(path)
    manifest={"schema_version":1,"kind":"studio_workspace_package","release":release_identity(ROOT),"workspace_name":workspace.name,"original_workspace":str(workspace),"original_root":str(ROOT),"include_results":include_results,"files":{p.relative_to(workspace).as_posix():{"sha256":file_digest(p),"bytes":p.stat().st_size} for p in files}}
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest["storage"]="sha256_objects"
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        z.writestr("workspace_package.json",json.dumps(manifest,indent=2))
        written=set()
        for p in files:
            digest=manifest["files"][p.relative_to(workspace).as_posix()]["sha256"]
            if digest not in written:
                z.write(p,"objects/"+digest);written.add(digest)
    return {"status":"EXPORTED","archive":str(output),"files":len(files),"bytes":output.stat().st_size,"include_results":include_results}

def import_workspace(root: Path, archive: Path, name: str):
    target=(root/"Workspaces"/slug(name)).resolve()
    if target.exists():raise FileExistsError("Usa un nombre nuevo; el workspace existente se conserva.")
    with tempfile.TemporaryDirectory(prefix="studio_import_") as temp:
        temp=Path(temp)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if not (temp/info.filename).resolve().is_relative_to(temp):raise ValueError("Ruta no válida en el ZIP.")
                if (info.external_attr >> 16)&0o170000 == 0o120000:raise ValueError("No se importan enlaces simbólicos.")
            z.extractall(temp)
        manifest=read_json(temp/"workspace_package.json")
        if manifest.get("kind")!="studio_workspace_package":raise ValueError("El paquete no pertenece a Studio.")
        source=temp/"workspace"
        if manifest.get("storage")=="sha256_objects":
            source.mkdir()
            for rel,expected in manifest["files"].items():
                dest=(source/rel).resolve()
                if not dest.is_relative_to(source):raise ValueError("Ruta no válida en el manifiesto.")
                obj=temp/"objects"/expected["sha256"]
                if not obj.resolve().is_relative_to(temp/"objects") or not obj.is_file() or file_digest(obj)!=expected["sha256"]:raise ValueError("Objeto del paquete alterado.")
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(obj,dest)
        for rel,expected in manifest["files"].items():
            path=(source/rel).resolve()
            if not path.is_relative_to(source) or not path.is_file() or file_digest(path)!=expected["sha256"]:raise ValueError("Paquete incompleto o modificado: "+rel)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(source,target)
        old_workspace=manifest["original_workspace"]
        old_root=manifest["original_root"]
        def rebase(value):
            if isinstance(value,str):
                return value.replace(old_workspace,str(target)).replace(old_root,str(root))
            if isinstance(value,list):return [rebase(v) for v in value]
            if isinstance(value,dict):return {k:rebase(v) for k,v in value.items()}
            return value
        for path in target.rglob("*.json"):
            if path.stat().st_size<10_000_000:
                try:write_json(path,rebase(json.loads(path.read_text(encoding="utf-8"))))
                except (ValueError,UnicodeError):pass
        info=read_json(target/"workspace.json");info["name"]=name;write_json(target/"workspace.json",info)
        # An old approval signature must not approve relocated/rebased metadata.
        for flag in target.glob("CFD_2D/*/Geometries/*/Meshes/*/MESH_APPROVED.flag"):
            flag.rename(flag.with_name("approval_before_transfer.json"))
        write_json(target/"transfer_manifest.json",{"source_archive":str(archive),"imported_at":time.time(),"original_workspace":old_workspace,"include_results":manifest["include_results"],"mesh_action":"Repeat real checkMesh and approval after relocation; mesh files are preserved."})
        register_workspace(root,target)
    return {"status":"IMPORTED","workspace":str(target),"mesh_recheck_required":True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("action",choices=["export","import"]);p.add_argument("--workspace",type=Path);p.add_argument("--archive",type=Path,required=True);p.add_argument("--root",type=Path,default=ROOT);p.add_argument("--name");p.add_argument("--include-results",action="store_true");a=p.parse_args()
    result=export_workspace(a.workspace,a.archive,a.include_results) if a.action=="export" else import_workspace(a.root.resolve(),a.archive,a.name)
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
