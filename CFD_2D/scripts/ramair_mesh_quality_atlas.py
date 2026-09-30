"""Native OpenFOAM-14 quality maps for PASS and FAIL meshes, with exact IDs.

The exporter links libmeshCheck; no reconstructed MSH cell numbering or quality
formula substitutes for the native polyMesh metrics. Only derived files in
QualityAtlas are written. Building uses the same wmake/GCC/ABI as OpenFOAM.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from scipy.spatial import cKDTree

METRICS = {
    "weight":("Peso de interpolación mínimo",True,"checkMesh_min_face_interpolation_weight"),
    "volume_ratio":("Ratio de volumen mínimo",True,"checkMesh_min_face_volume_ratio"),
    "determinant":("Determinante",True,"checkMesh_min_cell_determinant"),
    "nonorth_deg":("No ortogonalidad [°]",False,"checkMesh_max_non_orthogonality_deg"),
    "skewness":("Skewness",False,"checkMesh_max_skewness"),
    "aspect_ratio":("Aspect ratio cartesiano",False,"checkMesh_max_aspect_ratio"),
}


def native_export(root: Path, case: Path, output: Path, bashrc: Path):
    source = root/"CFD_2D/native/meshQualityAtlas"
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*")) if p.is_file())).hexdigest()
    build = Path.home()/".cache/ramair/openfoam14/meshQualityAtlas"
    stamp = build/"source_sha256.txt"
    build.mkdir(parents=True, exist_ok=True)
    env = "source "+shlex.quote(str(bashrc))+" >/dev/null 2>&1; "
    available = subprocess.run(["bash", "-lc", env+'test "$WM_PROJECT_VERSION" = 14 && command -v ramairMeshQualityAtlas'], capture_output=True, timeout=60).returncode == 0
    if not available or not stamp.exists() or stamp.read_text()!=digest:
        for p in source.rglob("*"):
            if p.is_file():
                dest=build/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        result = subprocess.run(["bash","-lc",env+"test \"$WM_PROJECT_VERSION\" = 14 && wmake"],cwd=build,capture_output=True,text=True,timeout=180)
        (output/"build.log").write_text(result.stdout+result.stderr)
        if result.returncode: raise RuntimeError("Native atlas build failed; see "+str(output/"build.log"))
        stamp.write_text(digest)
    command=env+"ramairMeshQualityAtlas -case "+shlex.quote(str(case))+" -output "+shlex.quote(str(output))
    result=subprocess.run(["bash","-lc",command],cwd=case,capture_output=True,text=True,timeout=180)
    (output/"native_export.log").write_text(result.stdout+result.stderr)
    if result.returncode: raise RuntimeError("Native atlas export failed; see "+str(output/"native_export.log"))
    return {"exporter_source_sha256":digest,"engine":"OpenFOAM Foundation 14 libmeshCheck","command":command}


def polygon(text): return np.array([[float(v) for v in point.split(":")] for point in text.split("|")])


def maps(cells, faces, output, chord, bl_height, *, language="es", mesh_label=None, metrics=None):
    wall=faces[faces.patch.str.contains("wall|airfoil",case=False,regex=True)]
    if len(wall):
        cells["wall_distance_m"]=cKDTree(wall[["x","y"]].values).query(cells[["x","y"]].values)[0]
    else: cells["wall_distance_m"]=np.nan
    cells["zone"]="volumen exterior"
    near=cells.wall_distance_m < max(2*bl_height,.02*chord)
    cells.loc[near,"zone"]="cercanía de BL/interfaz"
    cells.loc[near & (cells.x<.15*chord),"zone"]="LE / inlet"
    cells.loc[near & (cells.x>.90*chord),"zone"]="TE / transición"
    # This is a geometric screening label, never a substitute for inspecting
    # the exported polygon, native cell ID and adjacent face ID.
    near_view=cells[(cells.x.between(-.1*chord,1.15*chord)) & (abs(cells.y)<.35*chord)]
    polygons=[polygon(v) for v in near_view.polygon]
    summary={}
    for key,(label,ascending,exact_key) in METRICS.items():
        if metrics is not None and key not in metrics:
            continue
        english = language == "en"
        if english:
            label = {"weight":"Minimum interpolation weight", "volume_ratio":"Minimum volume ratio",
                     "determinant":"Cell determinant", "nonorth_deg":"Non-orthogonality [deg]",
                     "skewness":"Skewness", "aspect_ratio":"Cartesian aspect ratio"}[key]
        ordered=cells.sort_values(key,ascending=ascending)
        worst=ordered.head(max(100,int(.01*len(cells))))
        first=worst.iloc[0]
        lower,upper=float(cells[key].min()),float(cells[key].max())
        fig,axes=plt.subplots(1,3,figsize=(16,4.8),constrained_layout=True)
        axes[0].hist(cells[key],bins=70,color="#246680",log=True)
        axes[0].set(xlabel=label,ylabel="Cells (log scale)" if english else "Celdas (escala log)",title="Full distribution" if english else "Distribución completa")
        for ax in axes[1:]:
            bg=PolyCollection(polygons,facecolors="#f4f6f7",edgecolors="#a6adb4",linewidths=.1)
            ax.add_collection(bg)
            visible=worst[(worst.x.between(-.1*chord,1.15*chord)) & (abs(worst.y)<.35*chord)]
            collection=PolyCollection([polygon(v) for v in visible.polygon],array=visible[key].to_numpy(),cmap="magma_r" if ascending else "magma",edgecolors="#111111",linewidths=.2)
            ax.add_collection(collection);ax.set_aspect("equal");ax.set(xlabel="x [m]",ylabel="y [m]")
        axes[1].set(xlim=(-.08*chord,1.1*chord),ylim=(-.25*chord,.25*chord),title="Worst 1% on the mesh" if english else "Peor 1 % sobre la malla")
        worst_polygon=polygon(first.polygon)
        half=max(.003*chord, 4*float(np.ptp(worst_polygon,axis=0).max()))
        local=cells[cells.x.between(first.x-half*1.1,first.x+half*1.1) & cells.y.between(first.y-half*1.1,first.y+half*1.1)]
        axes[2].collections[0].remove()
        axes[2].collections[0].remove()
        axes[2].add_collection(PolyCollection([polygon(v) for v in local.polygon],facecolors="none",edgecolors="#78828b",linewidths=.5))
        local_worst=worst[worst.cell_id.isin(local.cell_id)]
        axes[2].add_collection(PolyCollection([polygon(v) for v in local_worst.polygon],array=local_worst[key].to_numpy(),cmap=collection.cmap,norm=collection.norm,edgecolors="#111111",linewidths=.2))
        axes[2].plot(first.x,first.y,"+",color="#00a0c0",markersize=10)
        axes[2].annotate(f"{'Cell' if english else 'Celda'} {int(first.cell_id)}\n{first[key]:.7g}",(first.x,first.y),xytext=(12,14),textcoords="offset points",fontsize=8)
        axes[2].set(xlim=(first.x-half,first.x+half),ylim=(first.y-half,first.y+half),title="Mesh Location" if english else "Localización Malla")
        fig.suptitle(label+" · "+(mesh_label or output.parent.name))
        fig.colorbar(collection,ax=axes[1:],label=label,shrink=.75)
        if english:
            for axis in axes[1:]:
                for artist in axis.collections:
                    artist.set_rasterized(True)
        image=output/(key+".png");fig.savefig(image,dpi=200)
        if english:
            for extension in ("pdf", "svg"):
                fig.savefig(output/(key+"."+extension), dpi=200)
        plt.close(fig)
        exported=ordered.head(50).drop(columns="polygon")
        exported.to_csv(output/(key+"_worst.csv"),index=False)
        summary[key]={"min":lower,"max":upper,"mean":float(cells[key].mean()),
                      "quantiles":{str(q):float(cells[key].quantile(q)) for q in (0,.01,.05,.5,.95,.99,1)},
                      "worst_cells":json.loads(exported.head(10).to_json(orient="records")),
                      "image":str(image),"exact_report_key":exact_key}
    cells.to_csv(output/"cells.csv.gz",index=False,compression="gzip")
    return summary


def generate(root,mesh,bashrc):
    root,mesh=Path(root).resolve(),Path(mesh).resolve()
    case=mesh/"openfoam_mesh_check_case"
    if not (case/"constant/polyMesh/boundary").exists():
        if (mesh/"constant/polyMesh/boundary").exists():case=mesh
        else:raise FileNotFoundError("A real converted polyMesh is required")
    output=mesh/"QualityAtlas";output.mkdir(exist_ok=True)
    # Mesh approval retains only polyMesh. Supply a separate read-only diagnostic
    # case instead of adding dictionaries to the approved mesh package.
    poly=(case/"constant/polyMesh").resolve()
    case=output/"Case"
    (case/"system").mkdir(parents=True,exist_ok=True)
    (case/"constant").mkdir(exist_ok=True)
    link=case/"constant/polyMesh"
    if not link.exists():link.symlink_to(poly,target_is_directory=True)
    elif link.resolve()!=poly:raise RuntimeError("Atlas diagnostic mesh target changed; preserve and rebuild the atlas")
    (case/"system/controlDict").write_text("FoamFile {version 2.0;format ascii;class dictionary;object controlDict;}\napplication ramairMeshQualityAtlas;startFrom startTime;startTime 0;stopAt endTime;endTime 0;deltaT 1;writeControl timeStep;writeInterval 1;\n")
    started=time.monotonic()
    source=native_export(root,case,output,bashrc)
    cells=pd.read_csv(output/"cells.csv");faces=pd.read_csv(output/"faces.csv")
    config=json.loads((mesh/"mesh_config.json").read_text()) if (mesh/"mesh_config.json").exists() else {}
    report=json.loads((mesh/"mesh_report.json").read_text()) if (mesh/"mesh_report.json").exists() else {}
    if not report and (mesh/"mesh_quality_report.json").is_file():
        report=json.loads((mesh/"mesh_quality_report.json").read_text())
    chord=float(config.get("geometry",{}).get("chord_m",1))
    height=float(report.get("boundary_layer",{}).get("total_thickness_m",.02*chord))
    metrics=maps(cells,faces,output,chord,height)
    for key,(_,ascending,exact_key) in METRICS.items():
        actual=metrics[key]["min" if ascending else "max"]
        reference=report.get(exact_key)
        metrics[key]["checkMesh_extreme"]=reference
        metrics[key]["matches_checkMesh"]=None if reference is None else bool(np.isclose(actual,reference,rtol=2e-5,atol=1e-9))
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (case/"constant/polyMesh").iterdir() if p.name in {"points","faces","owner","neighbour","boundary"}}
    result={"schema_version":1,"status":"GENERATED","mesh":str(mesh),"mesh_sha256":hashes,"cells":len(cells),
            "metrics":metrics,"wall_time_s":time.monotonic()-started,**source,
            "notes":["Cell and face IDs refer to this exact unrenumbered polyMesh; subsequent renumbering changes IDs.",
                     "Face metrics are reduced to the worst adjacent face per cell. Histograms count cells.",
                     "Location zone labels are heuristic. Polygons and coordinates are native geometry.",
                     "A relative worst percentile is visualized even for passing meshes; it is not a failure threshold."]}
    (output/"atlas.json").write_text(json.dumps(result,indent=2,ensure_ascii=False))
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--mesh",type=Path,required=True)
    p.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument("--bashrc",type=Path,default=Path.home()/".local/opt/openfoam14/etc/bashrc")
    a=p.parse_args();r=generate(a.root,a.mesh,a.bashrc)
    print(json.dumps({"status":r["status"],"cells":r["cells"],"wall_time_s":r["wall_time_s"],"mesh":r["mesh"]}))
