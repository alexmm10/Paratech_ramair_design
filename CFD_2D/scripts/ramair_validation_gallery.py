"""Permanent selected figures, independent from deletable laboratory runs."""
from pathlib import Path
import shutil
from ramair_workspace import slug, file_digest, read_json, write_json
from ramair_workspace_storage import publish_shared

def preserve_figures(root: Path, selected: list[Path], title: str) -> Path:
    if not selected:
        raise ValueError("Selecciona al menos una figura final.")
    gallery=root/"Validation Gallery"/slug(title)
    gallery.mkdir(parents=True,exist_ok=True)
    entries=read_json(gallery/"gallery.json",{}).get("figures",[])
    for source in selected:
        source=source.resolve()
        if not source.is_file() or source.suffix.lower() not in {".png",".svg",".jpg",".jpeg"}:
            raise ValueError("La galería solo admite figuras existentes.")
        digest=file_digest(source)
        target=gallery/(source.stem+"_"+digest[:12]+source.suffix.lower())
        shutil.copy2(source,target)
        summary=next((p/"case_summary.json" for p in source.parents if (p/"case_summary.json").is_file()),None)
        evidence=read_json(summary) if summary else {}
        entry={"file":target.name,"sha256":digest,"source":str(source),"source_status":evidence.get("status","SELECTED_FIGURE"),"workflow_test":evidence.get("workflow_test")}
        if not any(e["sha256"]==digest for e in entries):entries.append(entry)
    write_json(gallery/"gallery.json",{"name":title,"figures":entries,"scope":"selected final figures only; source validity is retained"})
    publish_shared(root,gallery)
    return gallery
