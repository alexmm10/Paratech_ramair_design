"""Install a verified single-file delivery and restore its lightweight example."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def verify_content(bundle):
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / 'DELIVERY_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('kind') != 'ramair_complete_delivery':
        raise ValueError('El archivo no es una entrega completa de RamAir Studio.')
    for relative, expected in manifest['files'].items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle) or not path.is_file() or path.stat().st_size != expected['bytes'] or sha256(path) != expected['sha256']:
            raise ValueError('Contenido alterado o ausente: ' + relative)
    return manifest


def relocate_snapshot_points(workspace, archive_hash):
    """Only rebind a verified snapshot's paths after checking unchanged coefficients."""
    from ramair_workspace import read_json, write_json
    from ramair_polar_comparison import available_point
    import pandas as pd
    count = 0
    for marker in workspace.glob('CFD_2D/*/Geometries/*/Meshes/*/OpenFOAM/*/delivery_snapshot.json'):
        setup = marker.parent
        points_file = setup / 'Polar/accepted_points.json'
        content = read_json(points_file, {'points': []})
        revised = []
        for old in content['points']:
            current = available_point(setup, float(old['alpha']), old['stage'])
            if not all(math.isclose(float(old[k]), float(current[k]), rel_tol=1e-12, abs_tol=1e-14) for k in ('Cl','Cd','Cm')):
                raise ValueError('Los coeficientes del snapshot han cambiado; no se conserva su revisión.')
            revised.append(dict(old, source=current['source'], source_sha256=current['source_sha256'],
                verified_relocation_archive=archive_hash))
        content['points'] = revised
        content['relocation_scope'] = 'Verified package and unchanged coefficient values; original scientific acceptance only.'
        write_json(points_file, content)
        pd.DataFrame(revised).to_csv(setup / 'Polar/polar.csv', index=False)
        count += len(revised)
    return count


def restore_example(root, archive, name='Example_Open_Polar'):
    sys.path.insert(0, str(ROOT / 'Application Support/Tools'))
    sys.path.insert(0, str(ROOT / 'CFD_2D/scripts'))
    from transfer_studio_workspace import import_workspace
    from ramair_workspace import read_json, write_json, register_workspace
    archive_hash = sha256(archive)
    # A repeat installation never overwrites edits made in the installed example.
    for marker in (root / 'Workspaces').glob('*/bundled_example.json'):
        if read_json(marker).get('archive_sha256') == archive_hash:
            register_workspace(root, marker.parent)
            return {'status': 'PRESERVED', 'workspace': str(marker.parent)}
    candidate, index = name, 1
    while (root / 'Workspaces' / candidate).exists():
        index += 1
        candidate = name + '_' + str(index)
    result = import_workspace(root, archive, candidate)
    workspace = Path(result['workspace'])
    result['retained_points_relocated'] = relocate_snapshot_points(workspace, archive_hash)
    write_json(workspace / 'bundled_example.json', {'archive_sha256': archive_hash,
        'scope': 'Initial fields and retained result products; volume checkpoints deliberately omitted.',
        'restart_policy': 'New simulations start from retained initial fields; no volume continuation is available.'})
    return result


def check_example_meshes(root, workspace):
    """Recheck real relocated meshes; never infer approval from a shipped flag."""
    sys.path.insert(0, str(ROOT / 'CFD_2D/scripts'))
    from ramair_workspace import read_json, write_json, compatible_mesh
    from ramair_2d_closed_experimental_mesh import approve
    from openfoam_environment import find_openfoam_bashrc
    bashrc = find_openfoam_bashrc()
    if not bashrc:
        return [{'status': 'RECHECK_REQUIRED', 'reason': 'OpenFOAM Foundation 14 no está disponible.'}]
    results = []
    for mesh in workspace.glob('CFD_2D/*/Geometries/*/Meshes/*/mesh_config.json'):
        mesh = mesh.parent
        case = next(mesh.glob('OpenFOAM/*/Angles/*/case_config.json'), None)
        if case is None:
            results.append({'mesh': mesh.name, 'status': 'RECHECK_REQUIRED'}); continue
        log = mesh / 'log.checkMesh.installation'
        command = 'source ' + shlex.quote(str(bashrc)) + '; checkMesh -allTopology -allGeometry -case ' + shlex.quote(str(case.parent))
        with log.open('w') as stream:
            check = subprocess.run(['bash', '-c', command], cwd=root, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        if check.returncode or 'Mesh OK.' not in log.read_text(errors='replace'):
            results.append({'mesh': mesh.name, 'status': 'RECHECK_FAILED', 'log': str(log)}); continue
        report = read_json(mesh / 'mesh_report.json')
        report.update(checkMesh_status='OK', installation_checkMesh_log=str(log))
        write_json(mesh / 'mesh_report.json', report)
        approval = approve(root, mesh.name, output_dir=mesh.parent)
        valid, reason = compatible_mesh(mesh, mesh.parent.parent)
        if not valid: raise ValueError(reason)
        results.append({'mesh': mesh.name, 'status': approval['status'], 'checkMesh': 'OK'})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle-dir', type=Path, required=True)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--restore-content', action='store_true')
    p.add_argument('--start-only', action='store_true')
    p.add_argument('--no-launch', action='store_true')
    p.add_argument('--skip-environment-install', action='store_true')
    p.add_argument('--wsl-project-root')
    p.add_argument('--distro')
    args = p.parse_args()
    bundle = args.bundle_dir.resolve()
    manifest = verify_content(bundle)
    if args.restore_content:
        root = args.root.resolve()
        result = restore_example(root, bundle / manifest['example_archive'])
        if result['status'] != 'PRESERVED':
            result['meshes'] = check_example_meshes(root, Path(result['workspace']))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if os.name != 'nt':
        raise ValueError('El instalador principal requiere Windows + WSL2. Use --restore-content dentro del runtime Linux configurado.')
    record = bundle / 'INSTALLATION_RECEIPT.json'
    prior = json.loads(record.read_text()) if record.is_file() else {}
    runtime = args.wsl_project_root or prior.get('runtime') or '~/ramair_cfd/DESIGN_APP'
    distro = args.distro or prior.get('distro') or 'Ubuntu-22.04'
    if ' ' in runtime: raise ValueError('Elija una ruta Linux sin espacios para OpenFOAM.')
    source = bundle / 'DESIGN APP'
    launcher = source / 'run_ramair_cfd2d_app.py'
    common = ['--distro', distro, '--wsl-project-root', runtime]
    if not args.start_only:
        install = [sys.executable, str(launcher), '--check-only', *common]
        if not args.skip_environment_install: install.append('--install')
        subprocess.run(install, cwd=source, check=True)
        spec = importlib.util.spec_from_file_location('ramair_delivery_launcher', launcher)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        mounted = module.windows_to_wsl(bundle, distro)
        expression = module.bash_path(runtime)
        script = 'set -e\ncd ' + expression + '\n.venv-cfd2d-ui/bin/python ' + shlex.quote('Application Support/Tools/install_studio_bundle.py') + ' --restore-content --bundle-dir ' + shlex.quote(mounted)
        subprocess.run(module.encoded_wsl_bash_command(distro, script), check=True)
        record.write_text(json.dumps({'version': manifest['release']['version'], 'commit': manifest['release'].get('commit'), 'runtime': runtime, 'distro': distro}, indent=2) + '\n')
    if not args.no_launch:
        return subprocess.call([sys.executable, str(launcher), *common], cwd=source)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
