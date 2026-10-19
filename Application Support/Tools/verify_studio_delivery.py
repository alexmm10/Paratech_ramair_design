"""Independently verify every file in the complete downloadable delivery."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import py_compile
import shutil
import tempfile
import zipfile
from install_studio_bundle import verify_content


def verify(archive, destination=None):
    with tempfile.TemporaryDirectory(prefix='ramair_verify_delivery_') as temp:
        target = Path(destination) if destination else Path(temp)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if len(names) != len(set(names)): raise ValueError('Entradas duplicadas en el ZIP.')
            for info in z.infolist():
                p = PurePosixPath(info.filename)
                if p.is_absolute() or '..' in p.parts or (info.external_attr >> 16)&0o170000 == 0o120000:
                    raise ValueError('Ruta de ZIP no válida.')
            roots = {PurePosixPath(n).parts[0] for n in names}
            if len(roots) != 1: raise ValueError('El paquete debe tener una raíz única.')
            root_name = roots.pop()
            if (target / root_name).exists(): raise FileExistsError('Se conserva la extracción anterior.')
            z.extractall(target)
        bundle = target / root_name
        manifest = verify_content(bundle)
        files = [p for p in bundle.rglob('*') if p.is_file()]
        expected = set(manifest['files']) | {'DELIVERY_MANIFEST.json'}
        if {p.relative_to(bundle).as_posix() for p in files} != expected: raise ValueError('Archivos sin inventariar.')
        for source in (bundle / 'DESIGN APP').rglob('*.py'): py_compile.compile(str(source), doraise=True)
        example = bundle / manifest['example_archive']
        with zipfile.ZipFile(example) as z:
            m = json.loads(z.read('workspace_package.json'))
            if m['release'].get('commit') != manifest['release'].get('commit'): raise ValueError('Commits incompatibles.')
            checked = set()
            for relative, record in m['files'].items():
                if PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts: raise ValueError('Ruta de workspace no válida.')
                member = 'objects/' + record['sha256']
                if member in checked: continue
                checked.add(member)
                if z.getinfo(member).file_size != record['bytes']: raise ValueError('Tamaño de objeto incorrecto.')
                h = hashlib.sha256()
                with z.open(member) as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
                if h.hexdigest() != record['sha256']: raise ValueError('Objeto alterado.')
        return {'status': 'PASS', 'files_verified': len(manifest['files']), 'example_files_verified': len(m['files']), 'release': manifest['release'], 'extracted_bundle': str(bundle) if destination else None}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--extract-to',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    result=verify(a.archive,a.extract_to)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
