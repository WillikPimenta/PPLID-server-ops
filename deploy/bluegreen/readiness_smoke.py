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


def run(lab, first, second, pg_bin, caddy, *, browser_test=False):
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
    manager = None
    started = False
    runtime = browser = context = None
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
        if (lab / 'deploy/MAIN/bluegreen/state.json').exists():
            raise RuntimeError('Use a fresh smoke journal; existing runtime needs explicit review')
        config_root = read_json(lab / 'env.config.json')
        bg = config_root['MAIN']['blueGreen']
        bg.update(caddyPath=str(caddy), caddySha256=hashlib.sha256(caddy.read_bytes()).hexdigest(),
                  pythonPath=sys.executable, minimumAvailableMb=512, minimumDiskMb=512,
                  workersOwnershipConfirmed=True, startupSeconds=120, observeSeconds=3,
                  drainSeconds=2, stopSeconds=30, commandSeconds=900)
        config = Config(lab, config_root, 'MAIN')
        atomic_json(lab / 'env.config.json', config_root)
        frontend = f'http://127.0.0.1:{config.frontend_port}'
        shared = lab / 'deploy/MAIN/shared/backend.env'
        text = shared.read_text(encoding='utf-8-sig')
        text += (f'\nCSRF_TRUSTED_ORIGINS={frontend}\nCORS_ALLOWED_ORIGINS={frontend}\n'
                 f'PPLID_FRONTEND_URL={frontend}\nSESSION_COOKIE_NAME=readiness_lab_session\n')
        shared.write_text(text, encoding='utf-8')
        settings = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        os.environ.update(settings)
        os.environ.update(OPS_MACHINE_CONFIG=str(lab / 'machine.config.json'), PPLID_BASE_DIR=str(lab),
                          PPLID_BACKEND_ENV_FILE=str(shared), PPLID_WEB_ONLY='1')
        os.environ.pop('PYTHONPATH', None)
        for variable, relative in [('USERPROFILE', 'profile'), ('APPDATA', 'profile/AppData/Roaming'),
                                   ('LOCALAPPDATA', 'profile/AppData/Local'), ('TEMP', 'tmp'), ('TMP', 'tmp')]:
            path = lab / relative
            path.mkdir(parents=True, exist_ok=True)
            os.environ[variable] = str(path)
        for sha in (first, second):
            (config.release(sha) / 'backend/.env').write_text(text, encoding='utf-8')
        command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-l', lab / 'postgres.log',
                 '-o', f'-h 127.0.0.1 -p {settings["POSTGRES_PORT"]} -F', '-w', 'start'])
        started = True
        backend = config.release(second) / 'backend'
        python = backend / '.venv/Scripts/python.exe'
        command([python, 'manage.py', 'migrate', '--noinput'], cwd=backend)
        report['checks'].append('isolated-schema-prepared-from-newer-release')
        password = secrets.token_urlsafe(20)
        # The user/password exist only in this disposable DB; redact from logs.
        os.environ['READINESS_PASSWORD'] = password
        code = ("import os; from django.contrib.auth import get_user_model; "
                "u,_=get_user_model().objects.get_or_create(username='readiness_lab'); "
                "u.set_password(os.environ['READINESS_PASSWORD']); u.is_active=True; u.save()")
        command([python, 'manage.py', 'shell', '-c', code], cwd=backend)
        manager = Manager(config)
        manager.prepare(first)
        manager.activate()
        report['checks'].append('older-clean-release-activated')
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
            page.goto(frontend, wait_until='networkidle', timeout=60000)
            page.wait_for_function("document.querySelector('#app')?.children.length > 0", timeout=20000)
            browser_token = page.evaluate('''async password => {
                const csrf=await (await fetch('/api/v1/auth/csrf/')).json();
                const r=await fetch('/api/v1/auth/login/',{method:'POST',headers:{'Content-Type':'application/json',
                'X-CSRFToken':csrf.csrfToken},body:JSON.stringify({username:'readiness_lab',password})});
                if(!r.ok) throw new Error('login '+r.status); return (await r.json()).csrfToken;
            }''', password)
            page.screenshot(path=str(lab / 'clean-browser-before.png'), full_page=True)
            report['checks'].append('clean-vue-browser-mount-and-login')
        old_assets = set(path.name for path in (config.release(first) / 'frontend/dist/assets').glob('*.js'))
        new_assets = set(path.name for path in (config.release(second) / 'frontend/dist/assets').glob('*.js'))
        report['differentAssetNames'] = len(old_assets ^ new_assets)
        for action in (lambda: manager.promote(second), manager.rollback):
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
        report['checks'].append('distinct-clean-release-promotion-rollback-and-session')
        # Verify chunks from the old page remain fetchable after both transitions.
        for name in old_assets - new_assets:
            response = session.get(frontend + '/assets/' + name, timeout=20)
            response.raise_for_status()
            expected_hash = hashlib.sha256((config.release(first) / 'frontend/dist/assets' / name).read_bytes()).digest()
            assert hashlib.sha256(response.content).digest() == expected_hash
        report['checks'].append('old-release-assets-retained-with-original-hashes')
        logout = session.post(frontend + '/api/v1/auth/logout/', json={},
                              headers={'X-CSRFToken': token, 'Origin': frontend}, timeout=20)
        logout.raise_for_status()
        if page:
            status = page.evaluate('''async token => (await fetch('/api/v1/auth/logout/',{method:'POST',
                headers:{'Content-Type':'application/json','X-CSRFToken':token},body:'{}'})).status''', browser_token)
            assert status == 200
            page.screenshot(path=str(lab / 'clean-browser-after.png'), full_page=True)
        report['checks'].append('original-csrf-token-valid-after-two-version-transitions')
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
