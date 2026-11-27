"""Run selected real Django checks with a disposable profile and PostgreSQL.

Only a direct workspace/.local/bluegreen-local-* evidence directory is accepted.
No application .env is copied and no business starters are enabled.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys


FUNCTIONAL_LABELS = [
    'apps.accounts.tests', 'apps.access.tests',
    'apps.falhas_criticas.tests.test_api', 'apps.falhas_criticas.tests.test_report_jobs',
    'apps.halloween.tests.test_events',
    'apps.escala_flex.tests.test_daily_status_reset',
    'apps.escala_flex.tests.test_clear_plantao_intervalos',
    'apps.escala_flex.tests.test_status_timeout',
    'apps.suporte_operacional.tests.test_fila_online',
]


def run(workspace, app, python, evidence, pg_bin, suite, name, labels=None, resume=False, fast_passwords=False):
    workspace, app, evidence = [p.resolve() for p in (workspace, app, evidence)]
    python, pg_bin = python.resolve(), pg_bin.resolve()
    if evidence.parent != workspace / '.local' or not evidence.name.startswith('bluegreen-local-'):
        raise ValueError('An isolated bluegreen-local evidence directory is required')
    if not app.is_relative_to(workspace / '.local'):
        raise ValueError('Use an isolated application worktree')
    if not name.replace('-', '').isalnum():
        raise ValueError('Invalid run name')
    lab = evidence / 'functional' / name
    previous = None
    if resume:
        previous = json.loads((lab / 'report.json').read_text())
        if suite != 'functional' or previous.get('postgresStopped') is not True or previous.get('suite') != suite:
            raise RuntimeError('Resume requires a confirmed stopped functional laboratory')
        (lab / ('report-history-' + secrets.token_hex(4) + '.json')).write_text(json.dumps(previous, indent=2), encoding='utf-8')
    lab.mkdir(parents=True, exist_ok=resume)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = {'suite': suite, 'productionTouched': False, 'ok': False, 'commands': []}
    started = False
    def command(args, *, cwd=None, env=None, timeout=1200, check=True):
        with (lab / 'commands.log').open('ab') as stream:
            result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=timeout, creationflags=flags)
        # No credentials or payloads are command-line arguments.
        report['commands'].append({'program': Path(str(args[0])).name, 'exit': result.returncode})
        if check and result.returncode:
            raise RuntimeError(f'{Path(str(args[0])).name} exited {result.returncode}; inspect commands.log')
        return result.returncode
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
                                      'PROGRAMFILES', 'PROGRAMFILES(X86)', 'COMMONPROGRAMFILES',
                                      'COMMONPROGRAMFILES(X86)', 'NUMBER_OF_PROCESSORS',
                                      'PROCESSOR_ARCHITECTURE', 'OS'}}
    environment['PYTHONNOUSERSITE'] = '1'
    mpl = evidence / 'matplotlib-cache'
    mpl.mkdir(exist_ok=True)
    environment['MPLCONFIGDIR'] = str(mpl)
    environment['MPLBACKEND'] = 'Agg'
    for key in list(environment):
        if key.startswith(('PPLID_WORKER_', 'PPLID_SUPERVISOR_')) or key in ('DATABASE_URL', 'PYTHONPATH'):
            environment.pop(key, None)
    for key, relative in [('USERPROFILE', 'profile'), ('APPDATA', 'profile/AppData/Roaming'),
                          ('LOCALAPPDATA', 'profile/AppData/Local'),
                          ('OneDrive', 'profile/OneDrive'), ('OneDriveCommercial', 'profile/OneDrive'),
                          ('OneDriveConsumer', 'profile/OneDrive'), ('ROBOT_RUNTIME_DIR', 'robot-runtime')]:
        path = lab / relative
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    # Deep evidence paths plus a report UUID can exceed Windows MAX_PATH.
    # Keep disposable scratch files short without requiring a registry change.
    scratch = workspace / '.local/bg-tmp' / secrets.token_hex(4)
    scratch.mkdir(parents=True)
    environment['TEMP'] = environment['TMP'] = str(scratch)
    report['temporaryRoot'] = str(scratch)
    guard = lab / 'network-guard'
    guard.mkdir(exist_ok=True)
    (guard / 'sitecustomize.py').write_text('''import socket
def allowed(address):
    if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):
        raise RuntimeError('External network disabled in local homologation')
for name in ('connect','connect_ex'):
    original=getattr(socket.socket,name)
    def wrapped(self,address,_original=original):
        allowed(address); return _original(self,address)
    setattr(socket.socket,name,wrapped)
original_lookup=socket.getaddrinfo
def lookup(host,*args,**kwargs):
    if host not in ('127.0.0.1','localhost','::1',None):
        raise RuntimeError('External DNS disabled in local homologation')
    return original_lookup(host,*args,**kwargs)
socket.getaddrinfo=lookup
''', encoding='utf-8')
    if fast_passwords:
        # Fixture acceleration only. Production settings and HTTP capacity
        # servers retain their real password hasher; baseline auth also runs it.
        (guard / 'validation_settings.py').write_text(
            "from config.settings import *\nPASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']\n",
            encoding='utf-8')
        environment['DJANGO_SETTINGS_MODULE'] = 'validation_settings'
        report['testPasswordHasher'] = 'MD5 fixture acceleration; production unchanged'
    port = 1
    try:
        if suite == 'functional':
            if resume:
                port = previous['postgresPort']
                if command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', 'status'], check=False) == 0:
                    raise RuntimeError('Refusing to resume a running cluster')
            with socket.socket() as stream:
                if resume:
                    stream.bind(('127.0.0.1', port))
                for attempt in range(100):
                    if resume:
                        break
                    try:
                        stream.bind(('127.0.0.1', 12000 + secrets.randbelow(20000)))
                        port = stream.getsockname()[1]
                        break
                    except OSError:
                        if attempt == 99:
                            raise
            if not resume:
                command([pg_bin / 'initdb.exe', '-D', lab / 'pgdata', '-U', 'local_homologation',
                         '-A', 'trust', '--locale=C', '-E', 'UTF8', '--no-sync'])
            command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-l', lab / 'postgres.log',
                     '-o', f'-h 127.0.0.1 -p {port} -F', '-w', 'start'])
            started = True
            if not resume:
                command([pg_bin / 'createdb.exe', '-h', '127.0.0.1', '-p', port,
                         '-U', 'local_homologation', 'local_homologation'])
        values = {'SECRET_KEY': 'isolated-homologation-only', 'DEBUG': 'True',
                  'ALLOWED_HOSTS': '127.0.0.1,localhost,testserver', 'POSTGRES_HOST': '127.0.0.1',
                  'POSTGRES_PORT': str(port), 'POSTGRES_DB': 'local_homologation',
                  'POSTGRES_USER': 'local_homologation', 'POSTGRES_PASSWORD': 'synthetic-only',
                  'PPLID_WEB_ONLY': '1', 'BI_SYNC_SCHEDULER_AUTOSTART': 'False',
                  'MEDIA_ROOT': str(lab / 'media')}
        config = lab / 'backend.env'
        config.write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
        environment.update(values)
        environment.update(PPLID_BACKEND_ENV_FILE=str(config), PPLID_BASE_DIR=str(lab),
                           PYTHONIOENCODING='utf-8',
                           PYTHONPATH=os.pathsep.join(str(p) for p in (guard, app / 'automacoes', app / 'backend')))
        labels = labels or (FUNCTIONAL_LABELS if suite == 'functional' else ['apps.common.tests.test_worker_drain'])
        report.update(postgresPort=port, labels=labels,
                      applicationCommit=subprocess.check_output(['git', '-C', str(app), 'rev-parse', 'HEAD'], text=True).strip(),
                      includesWorkingChanges=bool(subprocess.check_output(['git', '-C', str(app), 'status', '--porcelain', '-uno'], text=True).strip()))
        command([python, 'manage.py', 'test', *labels, '--verbosity', '2', '--noinput', '--keepdb'],
                cwd=app / 'backend', env=environment, timeout=3600)
        report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
    finally:
        if started:
            try:
                command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-m', 'fast', '-w', 'stop'], timeout=60)
                report['postgresStopped'] = True
            except Exception as error:
                report.update(cleanupError=str(error), ok=False)
        (lab / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workspace', 'app', 'python', 'evidence', 'pg-bin'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--suite', choices=('workers', 'functional'), required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--labels', nargs='+')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--fast-passwords', action='store_true')
    args = parser.parse_args()
    result = run(args.workspace, args.app, args.python, args.evidence, args.pg_bin, args.suite, args.name, args.labels, args.resume, args.fast_passwords)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['ok'] else 1)
