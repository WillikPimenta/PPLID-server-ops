"""Run the real preparation pipeline against a clean commit and disposable DB.

Never enables an installed environment or invokes promotion / business workers.
Requires local PostgreSQL tools; all configuration and data stay under .local.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .full_app_lab import free_ports
from .store import atomic_json
from .resources import available_commit_bytes
from .build_process import run_build
import psutil


def run(source: Path, pg_bin: Path, workspace: Path, *, resume=None, constraints=None,
        minimum_free_disk_mb=6144, minimum_commit_mb=6144):
    if constraints and not Path(constraints).is_file():
        raise ValueError('Constraints file does not exist')
    workspace = workspace.resolve()
    parent = workspace / '.local/bluegreen-readiness'
    lab = Path(resume).resolve() if resume else parent / ('build-' + uuid.uuid4().hex[:8])
    if lab.parent != parent or not lab.name.startswith('build-'):
        raise ValueError('Build laboratory must be a direct child of workspace/.local/bluegreen-readiness/build-*')
    if resume and not (lab / 'report.json').is_file():
        raise ValueError('Resume requires a completed build laboratory')
    lab.mkdir(parents=True, exist_ok=bool(resume))
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = {'laboratory': str(lab), 'productionTouched': False, 'checks': [], 'ok': False}
    def resource_preflight():
        values = {'freeDiskMb': shutil.disk_usage(workspace).free // 1024**2,
                  'availableCommitMb': available_commit_bytes() // 1024**2,
                  'availablePhysicalMb': psutil.virtual_memory().available // 1024**2}
        report.setdefault('resourcePreflights', []).append(values)
        if (values['freeDiskMb'] < minimum_free_disk_mb
                or values['availableCommitMb'] < minimum_commit_mb
                or values['availablePhysicalMb'] < 2048):
            raise RuntimeError('Insufficient laboratory build resources; no new build started')
    started = False
    def command(args, *, env=None, timeout=1200, check=True):
        with (lab / 'commands.log').open('ab') as output:
            result = subprocess.run([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT,
                                    env=env, timeout=timeout, creationflags=flags)
        if check and result.returncode:
            raise RuntimeError(f'{Path(str(args[0])).name} exited {result.returncode}; see commands.log')
        return result.returncode
    try:
        resource_preflight()
        if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=normal'], text=True).strip():
            raise RuntimeError('Source must have no tracked or untracked working changes')
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        report['sourceHead'] = head
        report['sourceIncludesWorkingChanges'] = False
        if resume:
            stored = dict(line.split('=', 1) for line in (lab / 'deploy/MAIN/shared/backend.env').read_text(encoding='utf-8-sig').splitlines() if '=' in line)
            port = int(stored['POSTGRES_PORT'])
            config = json.loads((lab / 'env.config.json').read_text(encoding='utf-8-sig'))
            backend_port, frontend_port = config['MAIN']['backendPort'], config['MAIN']['frontendPort']
        else:
            port, backend_port, frontend_port, admin_port, ab, af, bb, bf = free_ports(8)
            config = {'bindHost': '127.0.0.1', 'MAIN': {'enabled': True, 'repoName': 'READINESS_ONLY',
                'branch': head, 'postgresDb': 'readiness_lab', 'backendPort': backend_port, 'frontendPort': frontend_port,
                'deployStrategy': 'blueGreen', 'blueGreen': {'adminPort': admin_port, 'slots': {
                    'A': {'backendPort': ab, 'frontendPort': af}, 'B': {'backendPort': bb, 'frontendPort': bf}}}}}
        report['postgresPort'] = port
        pgdata = lab / 'pgdata'
        if resume:
            if command([pg_bin / 'pg_ctl.exe', '-D', pgdata, 'status'], check=False) == 0:
                raise RuntimeError('Refusing to resume a running laboratory')
        else:
            command([pg_bin / 'initdb.exe', '-D', pgdata, '-U', 'readiness_lab', '-A', 'trust', '--locale=C', '-E', 'UTF8', '--no-sync'])
        command([pg_bin / 'pg_ctl.exe', '-D', pgdata, '-l', lab / 'postgres.log', '-o', f'-h 127.0.0.1 -p {port} -F', '-w', 'start'])
        started = True
        if not resume:
            command([pg_bin / 'createdb.exe', '-h', '127.0.0.1', '-p', port, '-U', 'readiness_lab', 'readiness_lab'])
        atomic_json(lab / 'env.config.json', config)
        atomic_json(lab / 'machine.config.json', {'baseDir': str(lab), 'envConfigPath': str(lab / 'env.config.json')})
        root = lab / 'deploy/MAIN'
        root.mkdir(parents=True, exist_ok=True)
        if resume:
            command(['git', '-C', root / 'mirror', 'fetch', source, 'HEAD'])
        else:
            command(['git', 'clone', '--bare', '--no-hardlinks', source, root / 'mirror'])
        # No source .env or user data is read. All values are synthetic.
        backend_env = {'SECRET_KEY': 'isolated-readiness-lab-key', 'DEBUG': 'True',
            'ALLOWED_HOSTS': '127.0.0.1,localhost', 'POSTGRES_HOST': '127.0.0.1', 'POSTGRES_PORT': str(port),
            'POSTGRES_DB': 'readiness_lab', 'POSTGRES_USER': 'readiness_lab', 'POSTGRES_PASSWORD': 'lab-only',
            'PPLID_WEB_ONLY': '1', 'BI_SYNC_SCHEDULER_AUTOSTART': 'False',
            'MEDIA_ROOT': str(root / 'shared/media')}
        shared = root / 'shared'
        shared.mkdir(exist_ok=True)
        (shared / 'backend.env').write_text(''.join(f'{k}={v}\n' for k, v in backend_env.items()), encoding='utf-8')
        (shared / 'frontend.env').write_text(f'VITE_API_BASE_URL=http://127.0.0.1:{backend_port}\n', encoding='utf-8')
        # Keep executable discovery and package transport settings, never
        # inherit application credentials, database URLs or worker control.
        keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE',
                'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'USERNAME', 'USERDOMAIN',
                'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA', 'ALLUSERSPROFILE',
                'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE',
                'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS'}
        inherited = {key: value for key, value in os.environ.items() if key.upper() in keep}
        environment = {**inherited, **backend_env, 'OPS_MACHINE_CONFIG': str(lab / 'machine.config.json'),
            'PPLID_BACKEND_ENV_FILE': str(shared / 'backend.env'), 'PPLID_BASE_DIR': str(lab),
            'PPLID_OPS_STORE_PATH': str(lab / 'ops-store.db'),
            'PIP_CACHE_DIR': str(lab.parent / 'pip-cache'), 'npm_config_cache': str(lab.parent / 'npm-cache'),
            'NODE_OPTIONS': '--max-old-space-size=4096'}
        # Application imports can create AppData / OneDrive default folders.
        # Give subprocesses a disposable profile, never the operator's profile.
        for variable, relative in [('USERPROFILE', 'profile'), ('APPDATA', 'profile/AppData/Roaming'),
                                   ('LOCALAPPDATA', 'profile/AppData/Local'), ('TEMP', 'tmp'), ('TMP', 'tmp'),
                                   ('OneDrive', 'profile/OneDrive'), ('OneDriveCommercial', 'profile/OneDrive'),
                                   ('OneDriveConsumer', 'profile/OneDrive')]:
            directory = lab / relative
            directory.mkdir(parents=True, exist_ok=True)
            environment[variable] = str(directory)
        # Python 3.12 ensurepip can ship pip <24.2. Opt into Windows' trusted
        # certificate store; HTTPS verification stays enabled.
        environment.setdefault('PIP_USE_FEATURE', 'truststore')
        if constraints:
            environment['PIP_CONSTRAINT'] = str(Path(constraints).resolve())
            report['constraints'] = str(Path(constraints).resolve())
        command([sys.executable, workspace / 'lib/ops_store.py', '--db', lab / 'ops-store.db', 'init'], env=environment)
        environment.pop('PYTHONPATH', None)
        environment.pop('PPLID_PROMOTE_SOURCE', None)
        report['checks'].append('clean-source-and-isolated-postgresql')
        resource_preflight()
        run_id = 'readiness-' + head[:8]
        def build_resources():
            if (shutil.disk_usage(workspace).free < 1024 * 1024**2
                    or available_commit_bytes() < 768 * 1024**2
                    or psutil.virtual_memory().available < 768 * 1024**2):
                raise RuntimeError('Laboratory resource reserve reached; contained build stopped')
        with (lab / 'commands.log').open('ab') as output:
            result = run_build(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                workspace / 'deploy/bluegreen_prepare.ps1', '-Environment', 'MAIN', '-TargetSha', head[:8],
                '-TargetShaFull', head, '-RunId', run_id], stdout=output, env=environment, timeout=3600,
                receipt=root / 'bluegreen/builds' / (head[:8] + '.json'),
                release=root / 'releases' / head[:8], sha_full=head, check_alive=build_resources).returncode
        report['pipelineExitCode'] = result
        python = root / 'releases' / head[:8] / 'backend/.venv/Scripts/python.exe'
        if python.exists():
            for name, args in [('pip-freeze.txt', ['freeze']), ('pip-check.txt', ['check'])]:
                with (lab / name).open('wb') as output:
                    checked = subprocess.run([str(python), '-m', 'pip', *args], stdout=output, stderr=subprocess.STDOUT,
                                             timeout=60, creationflags=flags)
                if name == 'pip-check.txt':
                    report['pipCheckExitCode'] = checked.returncode
                (lab / (Path(name).stem + '-' + head[:8] + '.txt')).write_bytes((lab / name).read_bytes())
        if result:
            raise RuntimeError('Real clean preparation pipeline failed; inspect build logs')
        if report.get('pipCheckExitCode') != 0:
            raise RuntimeError('The clean release has dependency conflicts; see pip-check.txt')
        report['checks'].append('real-clean-build-and-validation')
        report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
    finally:
        if started:
            try:
                command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-m', 'fast', '-w', 'stop'], timeout=60)
                report['postgresStopped'] = True
            except Exception as error:
                report['cleanupError'] = str(error)
                report['ok'] = False
        atomic_json(lab / 'report.json', report)
        if report.get('sourceHead'):
            atomic_json(lab / ('report-' + report['sourceHead'][:8] + '.json'), report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--constraints', type=Path)
    parser.add_argument('--minimum-free-disk-mb', type=int, default=6144)
    parser.add_argument('--minimum-commit-mb', type=int, default=6144)
    args = parser.parse_args()
    result = run(args.source.resolve(), args.pg_bin.resolve(), Path(__file__).resolve().parents[2],
                 resume=args.resume, constraints=args.constraints,
                 minimum_free_disk_mb=args.minimum_free_disk_mb, minimum_commit_mb=args.minimum_commit_mb)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['ok'] else 1)
