"""Exercise a copied PPLID application against a disposable loopback PostgreSQL.

Only tracked source/static files are copied; no .env, database or user data is
copied. The source checkout is read-only. The cluster is stopped in finally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import secrets
import socket
import subprocess
import sys
import time
import uuid
import venv

from .application_patch import apply
from .config import Config
from .manager import Manager
from . import processes
from .store import atomic_json
from .caddy import request


def free_ports(count):
    streams = [socket.socket() for _ in range(count)]
    try:
        for stream in streams:
            for attempt in range(100):
                try:
                    stream.bind(('127.0.0.1', 12000 + secrets.randbelow(20000)))
                    break
                except OSError:
                    if attempt == 99:
                        raise
        return [stream.getsockname()[1] for stream in streams]
    finally:
        for stream in streams:
            stream.close()


def run(application: Path, pg_bin: Path, caddy: Path, workspace: Path, resume=None, browser_test=False):
    lab = Path(resume).resolve() if resume else workspace.resolve() / '.local' / ('bluegreen-full-app-' + uuid.uuid4().hex[:8])
    if lab.parent != workspace.resolve() / '.local' or not lab.name.startswith('bluegreen-full-app-'):
        raise ValueError('Laboratory must be a direct child of workspace/.local/bluegreen-full-app-*')
    lab.mkdir(parents=True, exist_ok=bool(resume))
    ports = free_ports(8)
    pg_port = ports.pop()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = {'laboratory': str(lab), 'source': str(application), 'postgresPort': pg_port,
              'productionTouched': False, 'checks': []}
    pg_started = False
    manager = None
    browser_runtime = browser = context = page = None
    def command(args, *, cwd=None, env=None, timeout=300):
        with (lab / 'commands.log').open('ab') as output:
            result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout, creationflags=flags)
        if result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}): {Path(str(args[0])).name}; inspect commands.log')
    try:
        if (lab / 'pgdata/PG_VERSION').exists():
            status = subprocess.run([str(pg_bin / 'pg_ctl.exe'), '-D', str(lab / 'pgdata'), 'status'], capture_output=True, creationflags=flags)
            if status.returncode == 0:
                raise RuntimeError('Refusing to copy into a running laboratory')
        head = subprocess.check_output(['git', '-C', str(application), 'rev-parse', 'HEAD'], text=True).strip()
        report['sourceHead'] = head
        report['sourceIncludesWorkingChanges'] = True
        root = {'bindHost': '127.0.0.1', 'MAIN': {'enabled': True, 'deployStrategy': 'blueGreen',
            'backendPort': ports[0], 'frontendPort': ports[1], 'blueGreen': {
                'adminPort': ports[2], 'caddyPath': str(caddy), 'caddySha256': hashlib.sha256(caddy.read_bytes()).hexdigest(),
                'pythonPath': sys.executable, 'minimumAvailableMb': 512, 'minimumDiskMb': 512,
                'workersOwnershipConfirmed': True,
                'startupSeconds': 90, 'observeSeconds': 2, 'drainSeconds': 2, 'stopSeconds': 30,
                'slots': {'A': {'backendPort': ports[3], 'frontendPort': ports[4]},
                          'B': {'backendPort': ports[5], 'frontendPort': ports[6]}}}}}
        config = Config(lab, root, 'MAIN')
        release = config.release(head[:8])
        files = subprocess.check_output(['git', '-C', str(application), 'ls-files', 'backend', 'automacoes'], text=True).splitlines()
        # Exact source-only dependencies identified from the failed import and
        # the current checkout. Never enumerate/copy arbitrary untracked data.
        local_modules = [
            'backend/apps/common/sync_resources.py',
            'backend/apps/common/migrations/0044_sync_resource_diagnostics.py',
            'backend/apps/produtividade/services/historical_stream.py',
            'automacoes/app/bots/replicacao_d1/api_execution_state.py',
        ]
        files += [relative for relative in local_modules if (application / relative).is_file()]
        report['explicitLocalModules'] = local_modules
        for relative in files:
            path = Path(relative)
            if path.suffix.lower() not in ('.py', '.json', '.html', '.txt', '.svg', '.css', '.js', '.toml', '.png'):
                continue
            if relative.startswith(('backend/data/', 'automacoes/data/')) or path.name.startswith('.env'):
                continue
            source = application / path
            if source.is_file():
                destination = release / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        shutil.copytree(application / 'frontend/dist', release / 'frontend/dist', dirs_exist_ok=True)
        apply(release)
        backend = release / 'backend'
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(backend / '.venv')
        if not (lab / 'pgdata/PG_VERSION').exists():
            command([pg_bin / 'initdb.exe', '-D', lab / 'pgdata', '-U', 'bluegreen_lab', '-A', 'trust', '--locale=C', '-E', 'UTF8', '--no-sync'])
        else:
            status = subprocess.run([str(pg_bin / 'pg_ctl.exe'), '-D', str(lab / 'pgdata'), 'status'], capture_output=True, creationflags=flags)
            if status.returncode == 0:
                raise RuntimeError('Refusing to resume a running laboratory cluster')
        command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-l', lab / 'postgres.log', '-o',
                 f'-h 127.0.0.1 -p {pg_port} -F', '-w', 'start'])
        pg_started = True
        import psycopg
        with psycopg.connect(host='127.0.0.1', port=pg_port, user='bluegreen_lab', dbname='postgres', autocommit=True) as connection:
            exists = connection.execute("SELECT 1 FROM pg_database WHERE datname='bluegreen_lab'").fetchone()
        if not exists:
            command([pg_bin / 'createdb.exe', '-h', '127.0.0.1', '-p', pg_port, '-U', 'bluegreen_lab', 'bluegreen_lab'])
        env_text = '\n'.join([
            'SECRET_KEY=isolated-bluegreen-laboratory-key', 'DEBUG=True', 'ALLOWED_HOSTS=127.0.0.1,localhost',
            'POSTGRES_DB=bluegreen_lab', 'POSTGRES_USER=bluegreen_lab', 'POSTGRES_PASSWORD=isolated-only',
            'POSTGRES_HOST=127.0.0.1', f'POSTGRES_PORT={pg_port}', 'PPLID_WEB_ONLY=1',
            'BI_SYNC_SCHEDULER_AUTOSTART=False', f'MEDIA_ROOT={lab / "media"}',
            'SESSION_COOKIE_NAME=bluegreen_lab_session', f'PPLID_FRONTEND_URL=http://127.0.0.1:{config.frontend_port}',
            f'CSRF_TRUSTED_ORIGINS=http://127.0.0.1:{config.frontend_port}',
        ]) + '\n'
        (backend / '.env').write_text(env_text, encoding='utf-8')
        env = {**os.environ, 'PPLID_WEB_ONLY': '1', 'PPLID_ENVIRONMENT': 'MAIN',
               'PPLID_BACKEND_ENV_FILE': str(backend / '.env'), 'PPLID_BASE_DIR': str(lab),
               'PYTHONPATH': str(release / 'automacoes'), 'BI_SYNC_SCHEDULER_AUTOSTART': 'False'}
        command([sys.executable, 'manage.py', 'check'], cwd=backend, env=env)
        report['checks'].append('actual-application-django-check')
        command([sys.executable, 'manage.py', 'migrate', '--noinput'], cwd=backend, env=env, timeout=600)
        report['checks'].append('actual-application-migrations-isolated-postgresql')
        password = 'lab-' + uuid.uuid4().hex
        create_user = ("from django.contrib.auth import get_user_model; "
                       "u,_=get_user_model().objects.get_or_create(username='bluegreen_lab_user'); "
                       f"u.set_password({password!r}); u.is_active=True; u.save()")
        command([sys.executable, 'manage.py', 'shell', '-c', create_user], cwd=backend, env=env)
        # Workers inherit only this explicit source path, not an editable
        # installation pointing back into the original checkout.
        os.environ['PYTHONPATH'] = str(release / 'automacoes')
        os.environ['PPLID_BASE_DIR'] = str(lab)
        atomic_json(release / 'meta.json', {'built': True, 'sha': head[:8], 'shaFull': head})
        manager = Manager(config)
        manager.prepare(head[:8])
        manager.activate()
        first = request(f'http://127.0.0.1:{config.backend_port}/__pplid/ready')
        report['checks'].append('actual-application-public-readiness')
        import requests
        session = requests.Session()
        session.trust_env = False
        frontend_url = f'http://127.0.0.1:{config.frontend_port}'
        csrf = session.get(frontend_url + '/api/v1/auth/csrf/', timeout=15)
        csrf.raise_for_status()
        login = session.post(frontend_url + '/api/v1/auth/login/',
                             json={'username': 'bluegreen_lab_user', 'password': password},
                             headers={'X-CSRFToken': csrf.json()['csrfToken'], 'Origin': frontend_url}, timeout=15)
        login.raise_for_status()
        token = login.json()['csrfToken']
        assert session.get(frontend_url + '/api/v1/auth/me/', timeout=15).json()['authenticated']
        report['checks'].append('actual-application-login-csrf')
        if browser_test:
            from playwright.sync_api import sync_playwright
            import urllib.parse
            browser_runtime = sync_playwright().start()
            browser = browser_runtime.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context()
            def local_only(route):
                address = urllib.parse.urlparse(route.request.url)
                if address.hostname == '127.0.0.1' and address.port in (config.backend_port, config.frontend_port):
                    route.continue_()
                else:
                    route.abort()
            context.route('**/*', local_only)
            page = context.new_page()
            page.goto(frontend_url, wait_until='networkidle', timeout=45000)
            page.wait_for_function("document.querySelector('#app')?.children.length > 0", timeout=15000)
            browser_login = page.evaluate('''async (password) => {
                const csrf = await (await fetch('/api/v1/auth/csrf/')).json();
                const response = await fetch('/api/v1/auth/login/', {method:'POST',
                  headers:{'Content-Type':'application/json','X-CSRFToken':csrf.csrfToken},
                  body:JSON.stringify({username:'bluegreen_lab_user',password})});
                if (!response.ok) throw new Error('Browser login failed: '+response.status);
                return (await response.json()).csrfToken;
            }''', password)
            page.screenshot(path=str(lab / 'browser-before.png'), full_page=True)
            report['checks'].append('actual-vue-browser-mount-and-browser-login')
        manager.promote(head[:8])
        second = request(f'http://127.0.0.1:{config.backend_port}/__pplid/ready')
        assert first['instanceId'] != second['instanceId']
        assert session.get(frontend_url + '/api/v1/auth/me/', timeout=15).json()['authenticated']
        if page:
            assert page.evaluate("async () => (await (await fetch('/api/v1/auth/me/')).json()).authenticated")
        manager.rollback()
        report['checks'].append('actual-application-same-release-replacement-and-rollback')
        logout = session.post(frontend_url + '/api/v1/auth/logout/', json={},
                              headers={'X-CSRFToken': token, 'Origin': frontend_url}, timeout=15)
        logout.raise_for_status()
        assert not session.get(frontend_url + '/api/v1/auth/me/', timeout=15).json()['authenticated']
        report['checks'].append('actual-application-session-and-csrf-survive-switch-and-rollback')
        if page:
            status = page.evaluate('''async (token) => (await fetch('/api/v1/auth/logout/', {
                method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token},body:'{}'})).status''', browser_login)
            assert status == 200
            page.screenshot(path=str(lab / 'browser-after.png'), full_page=True)
            report['checks'].append('actual-browser-session-and-csrf-survive-switch-and-rollback')
        # SQL timeout exercise uses a dedicated disposable table only.
        with psycopg.connect(host='127.0.0.1', port=pg_port, user='bluegreen_lab', dbname='bluegreen_lab', autocommit=True) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS bluegreen_lock_probe (id integer)')
        with psycopg.connect(host='127.0.0.1', port=pg_port, user='bluegreen_lab', dbname='bluegreen_lab') as blocker:
            blocker.execute('LOCK TABLE bluegreen_lock_probe IN ACCESS EXCLUSIVE MODE')
            with psycopg.connect(host='127.0.0.1', port=pg_port, user='bluegreen_lab', dbname='bluegreen_lab', autocommit=True) as waiting:
                waiting.execute("SET lock_timeout='250ms'")
                try:
                    waiting.execute('ALTER TABLE bluegreen_lock_probe ADD COLUMN rejected integer')
                except psycopg.errors.LockNotAvailable:
                    report['checks'].append('isolated-postgresql-lock-timeout')
                else:
                    raise AssertionError('Expected lock timeout did not occur')
            blocker.rollback()
        report['ok'] = True
    except Exception as error:
        report['ok'] = False
        report['error'] = str(error)
    finally:
        for resource, method in ((context, 'close'), (browser, 'close'), (browser_runtime, 'stop')):
            if resource:
                try:
                    getattr(resource, method)()
                except Exception as error:
                    report['browserCleanupError'] = str(error)
        if manager and manager.config.state_path.exists():
            try:
                manager.shutdown()
            except Exception as error:
                report['cleanupError'] = str(error)
        if pg_started:
            try:
                command([pg_bin / 'pg_ctl.exe', '-D', lab / 'pgdata', '-m', 'fast', '-w', 'stop'])
            except Exception as error:
                report['postgresCleanupError'] = str(error)
                report['ok'] = False
        atomic_json(lab / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--caddy', type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    result = run(args.application.resolve(), args.pg_bin.resolve(), args.caddy.resolve(), Path(__file__).resolve().parents[2], args.resume, args.browser)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result['ok'] else 1)
