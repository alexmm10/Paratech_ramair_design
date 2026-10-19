"""Build one download: application, installer, user manuals and an importable example."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_identity import release_identity


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def build_bundle(root, example, output):
    identity = release_identity(root)
    with zipfile.ZipFile(example) as z:
        source_manifest = json.loads(z.read('workspace_package.json'))
    if source_manifest.get('kind') != 'studio_workspace_package': raise ValueError('El ejemplo debe exportarse como workspace de Studio.')
    if source_manifest['release'].get('commit') != identity.get('commit'):
        raise ValueError('Reexporte el ejemplo con el mismo commit de la aplicación.')
    with tempfile.TemporaryDirectory(prefix='ramair_delivery_') as temp:
        temp = Path(temp)
        source_archive = temp / 'application.zip'
        subprocess.run([sys.executable, str(root / 'Application Support/Tools/package_ramair_project.py'), '--project-root', str(root), '--output', str(source_archive)], check=True)
        stage = temp / ('RamAir_Studio_' + identity['version'].replace('.', ''))
        stage.mkdir()
        with zipfile.ZipFile(source_archive) as z: z.extractall(stage)
        (stage / 'Content').mkdir()
        shutil.copy2(example, stage / 'Content/Example_Open_Polar.zip')
        guide = root / 'Documents and Manuals/Current/15_FIRST_RUN_AND_DEFAULTS.md'
        shutil.copy2(guide, stage / 'LEEME_PRIMERO.md')
        for name, option in [('INSTALAR_Y_ABRIR.bat', ''), ('ABRIR_RAMAIR.bat', '--start-only')]:
            text = '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nwhere py >nul 2>nul\r\nif errorlevel 1 (\r\n  python "DESIGN APP\\Application Support\\Tools\\install_studio_bundle.py" --bundle-dir "%~dp0." '+option+' %*\r\n) else (\r\n  py -3 "DESIGN APP\\Application Support\\Tools\\install_studio_bundle.py" --bundle-dir "%~dp0." '+option+' %*\r\n)\r\nif errorlevel 1 (\r\n echo Error de instalacion o arranque. Consulte LEEME_PRIMERO.md y conserve el mensaje anterior.\r\n pause\r\n exit /b 1\r\n)\r\n'
            (stage / name).write_text(text, encoding='ascii', newline='')
        files = sorted(p for p in stage.rglob('*') if p.is_file())
        manifest = {'schema_version': 1, 'kind': 'ramair_complete_delivery', 'release': identity,
            'example_archive': 'Content/Example_Open_Polar.zip',
            'example_scope': 'Open optimized RANS polar, source result products and initial fields; no volume checkpoints, no invented results.',
            'install': 'INSTALAR_Y_ABRIR.bat', 'start': 'ABRIR_RAMAIR.bat',
            'files': {p.relative_to(stage).as_posix(): {'bytes': p.stat().st_size, 'sha256': checksum(p)} for p in files}}
        (stage / 'DELIVERY_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(stage.rglob('*')):
                if p.is_file(): z.write(p, stage.name + '/' + p.relative_to(stage).as_posix(), compress_type=zipfile.ZIP_STORED if p.suffix == '.zip' else zipfile.ZIP_DEFLATED)
    digest = checksum(output)
    output.with_suffix('.sha256').write_text(digest + '  ' + output.name + '\n')
    return {'status': 'BUILT', 'archive': str(output), 'bytes': output.stat().st_size, 'sha256': digest, 'release': identity, 'files': len(files)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-root', type=Path, default=ROOT)
    p.add_argument('--example', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build_bundle(a.project_root.resolve(), a.example.resolve(), a.output.resolve()), indent=2))


if __name__ == '__main__': main()
