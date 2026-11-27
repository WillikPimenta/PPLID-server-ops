"""Validate two clean built releases using only an existing disposable build lab."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import urlparse

import requests
import psutil

from .config import Config
from .manager import Manager
from .store import atomic_json, read_json


def run(lab, first, second, pg_bin, caddy, *, browser_test=False, exercise=None, resume_stopped=False):
    lab = lab.resolve()
    expected = Path(__file__).resolve().parents[2] / '.local/bluegreen-readiness'
    if lab.parent != expected or not lab.name.startswith('build-'):
        raise ValueError('Only disposable readiness build laboratories are accepted')
    if first == second or any(not re.fullmatch('[0-9a-f]{7,40}', sha) for sha in (first, second)):
        raise ValueError('Two distinct release SHAs are required')
    # Both inputs must have passed the real clean build, not a hand-set built flag.
    for sha in (first, second):
        result = read_json(lab / ('report-' + sha + '.json'))
        if result.get('ok') is not True or result.get('sourceIncludesWorkingChanges') is not False:
            raise RuntimeError('Both releases require successful clean build reports')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = {'ok': False, 'releases': [first, second], 'checks': [], 'productionTouched': False}
    if resume_stopped:
        previous_path = lab / 'smoke-report.json'
        previous = read_json(previous_path)
        required = {'distinct-clean-release-r1-r2-r1-r2-and-session',
                    'old-release-assets-retained-with-original-hashes'}
        if (previous.get('releases') != [first, second] or previous.get('productionTouched') is not False
                or previous.get('postgresStopped') is not True
                or not required.issubset(previous.get('checks', []) + previous.get('reusedChecks', []))
                or previous.get('browserJavaScriptErrors')):
            raise RuntimeError('Resume requires the recorded successful transition/asset checks and stopped laboratory')
        archive = lab / ('smoke-report-before-resume-' + secrets.token_hex(4) + '.json')
        atomic_json(archive, previous)
        report.update(previousVerifiedReport=str(archive),
                      previousVerifiedReportSha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                      reusedChecks=list(dict.fromkeys(previous.get('checks', []) + previous.get('reusedChecks', []))))
        browser_test = False
    manager = None
    started = False
    runtime = browser = context = session = None
    original_environment = dict(os.environ)
    def command(args, *, cwd=None, timeout=600, check=True):
        with (lab / 'smoke-commands.log').open('ab') as output:
            result = subprocess.run([str(a) for a in args], cwd=cwd, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout, creationflags=flags)
        if check and result.returncode:
            raise RuntimeError(f'{Path(str(args[0])).name} exited {result.returncode}; see smoke-commands.log')
        return result.returncode
    try:
        if command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', 'status'], check=False) == 0:
            raise RuntimeError('Laboratory cluster must be stopped before this run')
        journal = lab / 'deploy/MAIN/bluegreen/state.json'
        if journal.exists() and not resume_stopped:
            raise RuntimeError('Use a fresh smoke journal; existing runtime needs explicit review')
        config_root = read_json(lab / 'env.config.json')
        bg = config_root['MAIN']['blueGreen']
        bg.update(caddyPath=str(caddy), caddySha256=hashlib.sha256(caddy.read_bytes()).hexdigest(),
                  pythonPath=sys.executable, minimumAvailableMb=512, minimumDiskMb=512,
                  workersOwnershipConfirmed=True, startupSeconds=120, observeSeconds=3,
                  drainSeconds=2, stopSeconds=30, commandSeconds=900)
        config = Config(lab, config_root, 'MAIN')
        if resume_stopped:
            stopped = Manager(config).read()
            if (stopped.get('phase') != 'stopped' or stopped.get('slots') or stopped.get('proxy')
                    or stopped.get('active') or stopped.get('intent')):
                raise RuntimeError('Only an explicitly stopped, empty runtime journal may be resumed')
            background = config.directory / 'background.json'
            if background.exists():
                from .worker_control import verify_stopped
                from .processes import owned
                record = read_json(background)
                if record.get('phase') != 'stopped' or owned(record.get('process') or {}):
                    raise RuntimeError('Previous laboratory supervisor has not confirmed shutdown')
                verify_stopped(record)
            report['checks'].append('stopped-runtime-and-worker-receipt-verified-without-deleting-journal')
        atomic_json(lab / 'env.config.json', config_root)
        frontend = f'http://127.0.0.1:{config.frontend_port}'
        shared = lab / 'deploy/MAIN/shared/backend.env'
        text = shared.read_text(encoding='utf-8-sig')
        text += (f'\nCSRF_TRUSTED_ORIGINS={frontend}\nCORS_ALLOWED_ORIGINS={frontend}\n'
                 f'PPLID_FRONTEND_URL={frontend}\nSESSION_COOKIE_NAME=readiness_lab_session\n')
        shared.write_text(text, encoding='utf-8')
        settings = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        # Keep OS executable discovery, not ambient application paths, task
        # ownership, connection strings or corporate feature switches.
        inherited = {key: value for key, value in original_environment.items()
                     if key.upper() in {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
                                        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'COMMONPROGRAMFILES',
                                        'COMMONPROGRAMFILES(X86)', 'NUMBER_OF_PROCESSORS',
                                        'PROCESSOR_ARCHITECTURE', 'OS'}}
        os.environ.clear()
        os.environ.update(inherited)
        os.environ.update(PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1')
        # Disable every scheduler before the first Django import/migration. The
        # independent supervisor is exercised only with synthetic idle modes.
        settings.update({name: 'False' for name in ('STATUS_TIMEOUT_ENABLED', 'DAILY_STATUS_RESET_ENABLED',
                         'PLANTAO_INTERVALO_CLEAR_ENABLED', 'SUPORTE_OP_QUEUE_TIMEOUT_SCHEDULER_ENABLED',
                         'BI_SYNC_SCHEDULER_AUTOSTART', 'FALHAS_REPORT_SCHEDULER_ENABLED',
                         'VACATION_RENEWAL_SCHEDULER_ENABLED')})
        settings.update(FALHAS_FAILURE_SOURCE='legacy', ROBOT_RUNTIME_DIR=str(lab / 'robot-runtime'),
                        ROBOT_LOGS_DIR=str(lab / 'robot-logs'),
                        ROTINA_IMPORT_STORAGE_DIR=str(lab / 'data/rotina-import'), ROTINA_IMPORT_OUTPUT_DIRS='{}')
        text += ''.join(f'{key}={value}\n' for key, value in settings.items())
        shared.write_text(text, encoding='utf-8')
        os.environ.update(settings)
        os.environ.update(OPS_MACHINE_CONFIG=str(lab / 'machine.config.json'), PPLID_BASE_DIR=str(lab),
                          PPLID_BACKEND_ENV_FILE=str(shared), PPLID_WEB_ONLY='1')
        for variable, relative in [('USERPROFILE', 'profile'), ('APPDATA', 'profile/AppData/Roaming'),
                                   ('LOCALAPPDATA', 'profile/AppData/Local'), ('TEMP', 'tmp'), ('TMP', 'tmp'),
                                   ('OneDrive', 'profile/OneDrive'), ('OneDriveCommercial', 'profile/OneDrive'),
                                   ('OneDriveConsumer', 'profile/OneDrive')]:
            path = lab / relative
            path.mkdir(parents=True, exist_ok=True)
            os.environ[variable] = str(path)
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
        os.environ['PYTHONPATH'] = str(guard)
        for sha in (first, second):
            (config.release(sha) / 'backend/.env').write_text(text, encoding='utf-8')
        command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-l', lab / 'postgres.log',
                 '-o', f'-h 127.0.0.1 -p {settings["POSTGRES_PORT"]} -F', '-w', 'start'])
        started = True
        backend = config.release(second) / 'backend'
        python = backend / '.venv/Scripts/python.exe'
        if not resume_stopped:
            command([python, 'manage.py', 'migrate', '--noinput'], cwd=backend)
            report['checks'].append('isolated-schema-prepared-from-newer-release')
        password = secrets.token_urlsafe(20)
        # The user/password exist only in this disposable DB; redact from logs.
        os.environ['READINESS_PASSWORD'] = password
        code = ("import os; from django.contrib.auth import get_user_model; "
                "from django.contrib.auth.models import Group; "
                "from apps.access.constants import ROLE_ADM_PORTAL,role_group_name; "
                "from apps.falhas_criticas.constants import GROUP_GLOBAL; "
                "u,_=get_user_model().objects.get_or_create(username='readiness_lab'); "
                "u.set_password(os.environ['READINESS_PASSWORD']); u.is_active=True; "
                "u.is_staff=True; u.is_superuser=True; u.must_change_password=False; u.save(); "
                "u.groups.add(Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))[0],"
                "Group.objects.get_or_create(name=GROUP_GLOBAL)[0])")
        command([python, 'manage.py', 'shell', '-c', code], cwd=backend)
        manager = Manager(config)
        manager.prepare(second if resume_stopped else first)
        manager.activate()
        report['checks'].append('candidate-reactivated-from-stopped-journal' if resume_stopped else 'older-clean-release-activated')
        session = requests.Session()
        session.trust_env = False
        csrf = session.get(frontend + '/api/v1/auth/csrf/', timeout=20)
        csrf.raise_for_status()
        login = session.post(frontend + '/api/v1/auth/login/', json={'username': 'readiness_lab', 'password': password},
                             headers={'X-CSRFToken': csrf.json()['csrfToken'], 'Origin': frontend}, timeout=20)
        login.raise_for_status()
        token = login.json()['csrfToken']
        page = None
        if browser_test:
            from playwright.sync_api import sync_playwright
            runtime = sync_playwright().start()
            browser = runtime.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context()
            def local_only(route):
                address = urlparse(route.request.url)
                if address.hostname == '127.0.0.1' and address.port in (config.backend_port, config.frontend_port):
                    route.continue_()
                else:
                    route.abort()
            context.route('**/*', local_only)
            page = context.new_page()
            report['browserJavaScriptErrors'] = []
            page.on('pageerror', lambda error: report['browserJavaScriptErrors'].append(str(error)[:500]))
            page.goto(frontend, wait_until='networkidle', timeout=60000)
            page.wait_for_function("document.querySelector('#app')?.children.length > 0", timeout=20000)
            browser_token = page.evaluate('''async password => {
                const csrf=await (await fetch('/api/v1/auth/csrf/')).json();
                const r=await fetch('/api/v1/auth/login/',{method:'POST',headers:{'Content-Type':'application/json',
                'X-CSRFToken':csrf.csrfToken},body:JSON.stringify({username:'readiness_lab',password})});
                if(!r.ok) throw new Error('login '+r.status); return (await r.json()).csrfToken;
            }''', password)
            # Hydrate the ordinary session/access stores while still on R1.
            # Later navigation must keep this document and its original imports.
            page.goto(frontend + '/home', wait_until='networkidle', timeout=60000)
            marker = secrets.token_hex(8)
            page.evaluate('marker => {window.__localBlueGreenDocument = marker}', marker)
            script_requests = []
            def record_script(response):
                if response.request.resource_type == 'script':
                    script_requests.append({'path': urlparse(response.url).path, 'status': response.status})
            page.on('response', record_script)
            page.screenshot(path=str(lab / 'clean-browser-before.png'), full_page=True)
            report['checks'].append('clean-vue-browser-mount-and-login-with-synthetic-admin')
        if not resume_stopped:
            old_root = config.release(first) / 'frontend/dist/assets'
            new_root = config.release(second) / 'frontend/dist/assets'
            old_assets = {path.relative_to(old_root).as_posix() for path in old_root.rglob('*') if path.is_file()}
            new_assets = {path.relative_to(new_root).as_posix() for path in new_root.rglob('*') if path.is_file()}
            report['differentAssetNames'] = len(old_assets ^ new_assets)
            for index, action in enumerate((lambda: manager.promote(second), manager.rollback, lambda: manager.promote(second))):
                state = action()
                usage = {}
                for name, slot in state['slots'].items():
                    owner = psutil.Process(slot['process']['pid'])
                    usage[name] = round(sum(p.memory_info().rss for p in [owner, *owner.children(recursive=True)]) / 1024**2, 2)
                report.setdefault('slotTreeRssSnapshotsMb', []).append(usage)
                manager.healthy(state['slots'][state['active']], public=True)
                response = session.get(frontend + '/api/v1/auth/me/', timeout=20)
                response.raise_for_status()
                assert response.json()['authenticated']
                if page:
                    assert page.evaluate("async()=> (await (await fetch('/api/v1/auth/me/')).json()).authenticated")
                    route = ('/secao/indicadores/gerador-falhas', '/secao/indicadores/falhas', '/home')[index]
                    before_scripts = len(script_requests)
                    # Exercise the installed router with the R1 document: creating
                    # an anchor or page.goto here would silently reload R2's index.
                    page.evaluate('''async route => {
                        const app = document.querySelector('#app').__vue_app__;
                        await app.config.globalProperties.$router.push(route);
                    }''', route)
                    page.wait_for_url(frontend + route, timeout=20000)
                    if index == 0:
                        page.wait_for_selector('.generator-page h1', timeout=20000)
                        lazy = script_requests[before_scripts:]
                        assert any('FalhasReportGeneratorView-' in item['path'] and item['status'] == 200 for item in lazy), lazy
                        report['lazyRouteScriptsAfterFirstPromotion'] = lazy
                    if index == 1:
                        page.wait_for_selector('.falhas-hero h1', timeout=20000)
                    assert page.evaluate('window.__localBlueGreenDocument') == marker, 'Original page was unexpectedly reloaded'
            report['checks'].append('distinct-clean-release-r1-r2-r1-r2-and-session')
            # Verify chunks from the old page remain fetchable after both transitions.
            verified_assets = []
            for name in sorted(old_assets - new_assets):
                response = session.get(frontend + '/assets/' + name, timeout=20)
                response.raise_for_status()
                expected_hash = hashlib.sha256((old_root / name).read_bytes()).digest()
                assert hashlib.sha256(response.content).digest() == expected_hash
                assert 'immutable' in response.headers.get('Cache-Control', '')
                content_type = response.headers.get('Content-Type', '').split(';')[0]
                if name.endswith('.js'):
                    assert content_type in ('text/javascript', 'application/javascript')
                if name.endswith('.css'):
                    assert content_type == 'text/css'
                verified_assets.append({'name': name, 'sha256': expected_hash.hex(), 'contentType': content_type})
                response.close()
            report['oldAssetsVerified'] = verified_assets
            report['checks'].append('old-release-assets-retained-with-original-hashes')
            if page:
                assert not report['browserJavaScriptErrors'], report['browserJavaScriptErrors']
                report['browserScope'] = ('synthetic administrator: R1 document retained across three transitions; '
                                          'lazy report-generator route after R2, Falhas after rollback, home and logout; '
                                          'not an exhaustive business/role navigation matrix')
        if exercise:
            report['extended'] = exercise({'manager': manager, 'config': config, 'command': command,
                                           'lab': lab, 'first': first, 'second': second,
                                           'session': session, 'frontend': frontend, 'python': python})
            if report['extended'].get('ok') is not True:
                raise RuntimeError('Extended application checks did not meet the recorded criteria')
        logout = session.post(frontend + '/api/v1/auth/logout/', json={},
                              headers={'X-CSRFToken': token, 'Origin': frontend}, timeout=20)
        logout.raise_for_status()
        if page:
            status = page.evaluate('''async token => (await fetch('/api/v1/auth/logout/',{method:'POST',
                headers:{'Content-Type':'application/json','X-CSRFToken':token},body:'{}'})).status''', browser_token)
            assert status == 200
            page.screenshot(path=str(lab / 'clean-browser-after.png'), full_page=True)
        report['checks'].append('original-csrf-token-valid-after-three-version-transitions')
        session.close()
        report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
    finally:
        for resource, method in ((context, 'close'), (browser, 'close'), (runtime, 'stop')):
            if resource:
                try:
                    getattr(resource, method)()
                except Exception as error:
                    report['browserCleanupError'] = str(error)
                    report['ok'] = False
        if session:
            session.close()
        if manager and config.state_path.exists():
            try:
                manager.shutdown()
            except Exception as error:
                report['cleanupError'] = str(error)
                report['ok'] = False
        if started:
            try:
                command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-m', 'fast', '-w', 'stop'], timeout=60)
                report['postgresStopped'] = True
            except Exception as error:
                report['postgresCleanupError'] = str(error)
                report['ok'] = False
        os.environ.clear()
        os.environ.update(original_environment)
        atomic_json(lab / 'smoke-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--first', required=True)
    parser.add_argument('--second', required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--caddy', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    report = run(args.lab, args.first, args.second, args.pg_bin.resolve(), args.caddy.resolve(), browser_test=args.browser)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report['ok'] else 1)
