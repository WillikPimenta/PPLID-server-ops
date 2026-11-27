"""Hash a clean readiness release and verify its installed deployment pins.

Only disposable build laboratories are accepted. No application import, package
installation, database connection, archive or environment-file content is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from .build_process import require_receipt
from .store import atomic_json, read_json, utc


EXCLUDED_DIRECTORIES = frozenset({
    '.git', '.local', '.tmp-local', '.venv', 'venv', 'node_modules', 'runtime',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.vite',
})
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def _physical(path: Path, root: Path, *, file=False) -> Path:
    """Reject links before resolve(), including links in intermediate parents."""
    path, root = Path(os.path.abspath(path)), root.resolve()
    try:
        parts = path.relative_to(root).parts
    except ValueError as error:
        raise ValueError('Manifest path must stay inside its allowed root') from error
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError('Manifest paths must not contain symlinks or junctions')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Manifest path resolved outside its allowed root')
    if file and not path.is_file():
        raise ValueError(f'Required physical file is missing: {path.name}')
    return path


def _hash(path: Path) -> dict:
    before = path.stat()
    digest = hashlib.sha256()
    count = 0
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            count += len(block)
    after = path.stat()
    if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or count != after.st_size):
        raise RuntimeError('Artifact changed while its manifest was being created')
    return {'bytes': count, 'sha256': digest.hexdigest()}


def _git(release: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
         '-C', str(release), *arguments],
        capture_output=True, timeout=60, creationflags=FLAGS,
    )
    if result.returncode:
        raise RuntimeError('Git verification failed; manifest not written')
    return result.stdout


def _relative(raw: bytes) -> str:
    value = raw.decode('utf-8', errors='surrogateescape')
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or value != path.as_posix()
            or any(part in ('.', '..') or ':' in part or '\\' in part for part in path.parts)):
        raise ValueError('Git returned an unsafe release-relative path')
    return value


def _excluded(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return (any(part.lower() in EXCLUDED_DIRECTORIES for part in parts)
            or any(part.lower().startswith('.env') or part.lower().endswith('.env') for part in parts))


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)', line)
        if not match:
            raise ValueError(f'Deployment constraint on line {number} is not an exact pin')
        name = re.sub(r'[-_.]+', '-', match[1]).lower()
        if name in pins:
            raise ValueError('Deployment constraints contain a duplicate normalized package name')
        pins[name] = match[2]
    if not pins:
        raise ValueError('Deployment constraints must contain exact pinned versions')
    return pins


def _installed(python: Path, site_packages: Path) -> dict[str, str]:
    # -S bypasses site/.pth startup entirely. Read distribution metadata from
    # the supplied venv directory explicitly; editable application modules are
    # never imported. -I ignores ambient PYTHONPATH and the user site.
    script = r'''
import json, re, socket, sys
def offline(*args, **kwargs):
    raise RuntimeError('Network access is disabled during metadata verification')
socket.socket = offline
socket.create_connection = offline
socket.getaddrinfo = offline
socket.gethostbyname = offline
socket.gethostbyaddr = offline
socket.getfqdn = offline
from importlib import metadata
installed = {}
for distribution in metadata.distributions(path=[sys.argv[1]]):
    name = distribution.metadata.get('Name')
    if not name:
        continue
    key = re.sub(r'[-_.]+', '-', name).lower()
    if key in installed:
        raise RuntimeError('Duplicate normalized installed distribution')
    installed[key] = distribution.version
print(json.dumps(installed, sort_keys=True))
'''
    keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}
    environment = {k: v for k, v in os.environ.items() if k.upper() in keep}
    environment.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    result = subprocess.run(
        [str(python), '-I', '-S', '-c', script, str(site_packages)],
        cwd=python.parent, env=environment, capture_output=True,
        timeout=60, creationflags=FLAGS,
    )
    if result.returncode:
        raise RuntimeError('Isolated venv metadata verification failed; manifest not written')
    return json.loads(result.stdout.decode('utf-8'))


def run(lab: Path, sha: str, output: Path) -> dict:
    workspace = Path(__file__).resolve().parents[2]
    if not re.fullmatch('[0-9a-f]{8}', sha):
        raise ValueError('Pass the eight-character lowercase release SHA')
    lab = _physical(lab, workspace)
    if lab.parent != workspace / '.local/bluegreen-readiness' or not lab.name.startswith('build-'):
        raise ValueError('Only direct workspace/.local/bluegreen-readiness/build-* laboratories are accepted')
    output = _physical(output, workspace)
    if (output.name != 'releases' or output.parent.parent != workspace / '.local'
            or not output.parent.name.startswith('bluegreen-local-')):
        raise ValueError('Output must be workspace/.local/bluegreen-local-*/releases')
    report_path = _physical(lab / f'report-{sha}.json', lab, file=True)
    report = read_json(report_path)
    full = report.get('sourceHead', '')
    if (report.get('ok') is not True or report.get('sourceIncludesWorkingChanges') is not False
            or not isinstance(full, str) or not re.fullmatch('[0-9a-f]{40}', full)
            or not full.startswith(sha) or report.get('productionTouched') is not False
            or Path(report.get('laboratory', '')).resolve() != lab
            or type(report.get('pipelineExitCode')) is not int or report['pipelineExitCode'] != 0
            or type(report.get('pipCheckExitCode')) is not int or report['pipCheckExitCode'] != 0):
        raise RuntimeError('A successful clean build and pip-check report for this SHA are required')
    release = _physical(lab / 'deploy/MAIN/releases' / sha, lab)
    meta_path = _physical(release / 'meta.json', release, file=True)
    meta = read_json(meta_path)
    if meta.get('built') is not True or meta.get('shaFull') != full or meta.get('sha') != sha:
        raise RuntimeError('Release metadata and clean build source commit disagree')
    if _git(release, 'rev-parse', '--show-toplevel').decode().strip().lower() != str(release).replace('\\', '/').lower():
        raise RuntimeError('Release must be the Git worktree root')
    if _git(release, 'rev-parse', '--verify', 'HEAD').decode().strip() != full:
        raise RuntimeError('Release Git HEAD differs from the clean build commit')
    receipt_path = _physical(lab / 'deploy/MAIN/bluegreen/builds' / f'{sha}.json', lab, file=True)
    require_receipt(receipt_path.parent.parent, release, full)
    receipt = read_json(receipt_path)
    if type(receipt.get('exitCode')) is not int or receipt['exitCode'] != 0:
        raise RuntimeError('Contained build receipt must record a zero exit code')
    pip_path = _physical(lab / f'pip-check-{sha}.txt', lab, file=True)
    if pip_path.read_text(encoding='utf-8-sig').strip() != 'No broken requirements found.':
        raise RuntimeError('Saved pip-check output does not confirm successful validation')

    artifacts = {}
    omitted = 0
    excluded_gitlinks = {}
    entries = _git(release, 'ls-files', '--stage', '-z').split(b'\0')
    for entry in entries:
        if not entry:
            continue
        header, raw = entry.split(b'\t', 1)
        mode, object_id, stage = header.split()
        relative = _relative(raw)
        if stage != b'0':
            raise RuntimeError('Unmerged Git index cannot identify a release artifact')
        if _excluded(relative) or mode == b'120000':
            if mode == b'160000':
                excluded_gitlinks[relative] = object_id.decode('ascii')
            omitted += 1
            continue
        if mode not in (b'100644', b'100755'):
            raise RuntimeError('Submodules or unsupported Git entries require explicit manifest support')
        path = _physical(release / relative, release, file=True)
        artifacts[relative] = {**_hash(path), 'source': 'git-tracked'}
    changed = [_relative(p) for p in _git(release, 'diff', '--ignore-submodules=all', '--name-only', '-z', 'HEAD', '--').split(b'\0') if p]
    # Editable installation regenerates this already-versioned setuptools
    # metadata. Keep its actual hashes, while still rejecting source changes.
    generated_metadata = {'automacoes/sistematest.egg-info/' + name for name in (
        'PKG-INFO', 'SOURCES.txt', 'requires.txt', 'dependency_links.txt',
        'entry_points.txt', 'top_level.txt', 'not-zip-safe')}
    if any(not _excluded(p) and p not in generated_metadata for p in changed):
        raise RuntimeError('Tracked release sources changed after the clean commit')
    for path in generated_metadata & artifacts.keys():
        artifacts[path]['source'] = 'generated-package-metadata'
    dist = _physical(release / 'frontend/dist', release)
    _physical(dist / 'index.html', release, file=True)
    for directory, folders, files in os.walk(dist, followlinks=False):
        for name in list(folders):
            path = Path(directory) / name
            relative = path.relative_to(release).as_posix()
            if _excluded(relative):
                folders.remove(name)
            else:
                _physical(path, release)
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(release).as_posix()
            if _excluded(relative):
                continue
            path = _physical(path, release, file=True)
            artifacts[relative] = {**_hash(path), 'source': 'frontend-dist'}

    important = {
        'pythonConstraints': 'backend/requirements.deploy.constraints.txt',
        'frontendLock': 'frontend/package-lock.json',
        'workerProtocol': 'automacoes/app/core/worker_drain.py',
        'supervisorControl': 'backend/apps/automacoes/supervisor_control.py',
        'webContract': 'scripts/deploy/bluegreen-contract.json',
        'workersContract': 'scripts/deploy/bluegreen-workers-contract.json',
    }
    references = {}
    for label, relative in important.items():
        if relative not in artifacts:
            raise RuntimeError(f'Required versioned manifest input is missing: {label}')
        references[label] = {'path': relative, **{k: artifacts[relative][k] for k in ('sha256', 'bytes')}}
    for label, canonical in [('workerProtocol', 'worker_protocol.py'), ('supervisorControl', 'supervisor_control.py')]:
        original = _physical(Path(__file__).with_name(canonical), workspace, file=True)
        release_copy = release / important[label]
        normalize = lambda p: p.read_bytes().replace(b'\r\n', b'\n')
        normalized = hashlib.sha256(normalize(release_copy)).hexdigest()
        if normalized != hashlib.sha256(normalize(original)).hexdigest():
            raise RuntimeError('Release and operations protocol implementations differ')
        references[label]['normalizedSha256'] = normalized
        references[label]['operationsCanonical'] = _hash(original)
    constraints = _pins(release / important['pythonConstraints'])
    venv = _physical(release / 'backend/.venv', release)
    python = _physical(venv / 'Scripts/python.exe', release, file=True)
    site_packages = _physical(venv / 'Lib/site-packages', release)
    if not site_packages.is_dir():
        raise RuntimeError('Built venv site-packages is missing')
    installed = _installed(python, site_packages)
    mismatches = [name for name, version in constraints.items() if installed.get(name) != version]
    if mismatches:
        raise RuntimeError('Installed versions disagree with exact deployment pins: ' + ', '.join(sorted(mismatches)))
    manifest = {
        'schemaVersion': 1, 'createdAt': utc(), 'ok': True, 'productionTouched': False,
        'release': sha, 'sourceCommit': full, 'laboratory': str(lab),
        'operationsCommit': _git(workspace, 'rev-parse', 'HEAD').decode().strip(),
        'operationsIncludesWorkingChanges': bool(_git(workspace, 'status', '--porcelain', '-uno', '--ignore-submodules=all').strip()),
        'generatedMetadataChanged': sorted(generated_metadata & set(changed)),
        'manifestTool': _hash(Path(__file__)),
        'cleanBuildReport': _hash(report_path), 'containedBuildReceipt': _hash(receipt_path),
        'releaseMetadata': _hash(meta_path), 'pipCheck': {**_hash(pip_path), 'exitCode': 0},
        'references': references,
        'pythonConstraints': {'count': len(constraints), 'matched': len(constraints),
                              'versions': dict(sorted(constraints.items())),
                              'verification': 'venv -I -S; importlib.metadata; network disabled'},
        'excluded': {'directories': sorted(EXCLUDED_DIRECTORIES),
                     'temporaryGitlinks': excluded_gitlinks,
                     'environmentFiles': '.env*, *.env', 'trackedEntriesOmitted': omitted,
                     'symlinksAndJunctions': 'not followed; physical manifest inputs required'},
        'artifactCount': len(artifacts), 'artifactBytes': sum(a['bytes'] for a in artifacts.values()),
        'artifacts': dict(sorted(artifacts.items())),
    }
    destination = _physical(output / f'artifact-manifest-{sha}.json', workspace)
    atomic_json(destination, manifest)
    return {'ok': True, 'sourceCommit': full, 'artifactCount': len(artifacts),
            'constraintsMatched': len(constraints), 'manifest': str(destination)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.lab, args.sha, args.output)
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
