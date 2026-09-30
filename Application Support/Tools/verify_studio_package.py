"""Extract a portable application and verify every file and Python source."""
from pathlib import Path
import argparse
import hashlib
import json
import py_compile
import tempfile
import zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ramair_portable_') as temp:
        dest=Path(temp)
        with zipfile.ZipFile(args.archive) as archive:
            for info in archive.infolist():
                if not (dest/info.filename).resolve().is_relative_to(dest):
                    raise ValueError('Unsafe archive path: '+info.filename)
            archive.extractall(dest)
        source=dest/'DESIGN APP'
        manifest=json.loads((source/'PORTABLE_PACKAGE_MANIFEST.json').read_text())
        for relative,expected in manifest['files'].items():
            path=(source/relative).resolve()
            if not path.is_relative_to(source) or path.stat().st_size!=expected['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=expected['sha256']:
                raise ValueError('Manifest mismatch: '+relative)
        files=list(source.rglob('*.py'))
        for path in files:
            py_compile.compile(str(path),doraise=True)
        for forbidden in ('Workspaces','Data Archive','Results','Previous Versions','Dockerfile','Documents and Manuals/Archive'):
            if (source/forbidden).exists():
                raise ValueError('Non-delivery content: '+forbidden)
        result={'status':'PASS','files_verified':len(manifest['files']),'python_modules_compiled':len(files),
            'archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),
            'release':manifest['release'],'extracted_to_different_root':True}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
