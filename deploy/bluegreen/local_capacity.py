"""Compare legacy HTTP topology and blue-green in a disposable readiness lab."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import requests

from .application_load import stage
from . import processes
from .readiness_smoke import run as smoke
from .supervisor_control import _protect
from .local_mixed_job import run as mixed_business_job


def exercise(context, *, phase_seconds=300, sustain_seconds=1800, think_seconds=5, quick=False, reuse_seed=False,
             max_in_flight=None):
    manager, config, lab = context['manager'], context['config'], context['lab']
    expected = Path(__file__).resolve().parents[2] / '.local/bluegreen-readiness'
    if (lab.resolve().parent != expected or not lab.name.startswith('build-')
            or config.base != lab.resolve() or config.bind != '127.0.0.1'):
        raise ValueError('Capacity checks require an isolated loopback readiness lab')
    if phase_seconds <= 0 or sustain_seconds <= 0 or think_seconds < 0:
        raise ValueError('Durations must be positive and think time nonnegative')
    command, python = context['command'], context['python']
    first, second = context['first'], context['second']
    backend = f'http://127.0.0.1:{config.backend_port}'
    frontend = context['frontend']
    output = lab / 'load'
    output.mkdir(exist_ok=True)
    report = {'ok': False, 'productionTouched': False, 'stages': [], 'checks': []}
    legacy = []
    worker = None
    probe_stop = threading.Event()
    probe_thread = None
    probes = []
    logs = []
    database = None
    def database_sample():
        with database.cursor() as cursor:
            cursor.execute("SELECT count(*), count(*) FILTER (WHERE state='active'), "
                           "count(*) FILTER (WHERE wait_event_type='Lock') FROM pg_stat_activity "
                           "WHERE datname=current_database()")
            total, active, waiting = cursor.fetchone()
        return {'pgConnections': total, 'pgActive': active, 'pgWaitingOnLocks': waiting}
    def roots():
        state = manager.read()
        result = [s['process'] for s in state['slots'].values() if s.get('process')]
        if state.get('proxy'):
            result.append(state['proxy'])
        if worker and worker.get('process'):
            result.append(worker['process'])
        result.extend(legacy)
        return result
    def probe():
        session = requests.Session()
        session.trust_env = False
        try:
            while not probe_stop.is_set():
                for kind, url in [('backend', backend + '/falhas/health/'), ('frontend', frontend + '/')]:
                    begin = time.monotonic()
                    entry = {'at': time.time(), 'kind': kind}
                    try:
                        response = session.get(url, timeout=(2, 10), allow_redirects=False)
                        entry.update(status=response.status_code, release=response.headers.get('X-Pplid-Release'))
                        response.close()
                    except requests.RequestException as error:
                        entry.update(status=0, error=type(error).__name__)
                    entry['ms'] = round((time.monotonic() - begin) * 1000, 2)
                    probes.append(entry)
                probe_stop.wait(.2)
        finally:
            session.close()
    try:
        # Web slots are already web-only. Only the disposable executor receives
        # these settings and empty lab runtime directories; no real job starts.
        flags = {name: 'False' for name in ('STATUS_TIMEOUT_ENABLED', 'DAILY_STATUS_RESET_ENABLED',
                 'PLANTAO_INTERVALO_CLEAR_ENABLED', 'SUPORTE_OP_QUEUE_TIMEOUT_SCHEDULER_ENABLED',
                 'BI_SYNC_SCHEDULER_AUTOSTART', 'FALHAS_REPORT_SCHEDULER_ENABLED',
                 'VACATION_RENEWAL_SCHEDULER_ENABLED')}
        flags.update(ROBOT_RUNTIME_DIR=str(lab / 'robot-runtime'), ROBOT_LOGS_DIR=str(lab / 'robot-logs'))
        shared = lab / 'deploy/MAIN/shared/backend.env'
        text = shared.read_text(encoding='utf-8-sig') + '\n' + ''.join(f'{k}={v}\n' for k, v in flags.items())
        shared.write_text(text, encoding='utf-8')
        for sha in (first, second):
            (config.release(sha) / 'backend/.env').write_text(text, encoding='utf-8')
        os.environ.update(flags)
        if not reuse_seed:
            seed = Path(__file__).with_name('load_seed.py')
            command([python, 'manage.py', 'shell', '-c', f'import runpy; runpy.run_path({str(seed)!r})'],
                    cwd=config.release(second) / 'backend', timeout=600)
        elif not all((output / name).is_file() for name in ('seed-report.json', 'private-sessions.dpapi')):
            raise RuntimeError('Seed reuse requires the existing report and encrypted independent sessions')
        private = json.loads(_protect((output / 'private-sessions.dpapi').read_bytes(), decrypt=True))
        data = json.loads((output / 'seed-report.json').read_text())
        if (data.get('failureSource') != 'legacy' or data.get('users') != 150
                or data.get('legacyFailureRows') != 20000 or data.get('agents') != 500
                or os.environ.get('FALHAS_FAILURE_SOURCE') != 'legacy'):
            raise RuntimeError('Capacity seed requires 150 users, 20000 Failure rows, 500 agents and explicit legacy source')
        cookies = private.get('cookies', [])
        if len(cookies) != 150 or len(set(cookies)) != 150 or not private.get('adminCookie') or not private.get('cookieName'):
            raise RuntimeError('Seed reuse requires 150 distinct encrypted authenticated sessions')
        query = f"start_date={data['from']}&end_date={data['to']}&oficial=false"
        import psycopg
        if int(os.environ['POSTGRES_PORT']) < 12000:
            raise RuntimeError('Unexpected capacity database port')
        database = psycopg.connect(host='127.0.0.1', port=os.environ['POSTGRES_PORT'],
                                  dbname='readiness_lab', user='readiness_lab', connect_timeout=3,
                                  autocommit=True, options='-c statement_timeout=3000')
        with database.cursor() as cursor:
            cursor.execute("SELECT current_setting('data_directory')")
            if Path(cursor.fetchone()[0]).resolve() != lab.resolve() / 'pgdata':
                raise RuntimeError('Capacity PostgreSQL data directory does not belong to this laboratory')
            cursor.execute('SELECT (SELECT count(*) FROM falhas_failure), '
                           '(SELECT count(*) FROM falhas_agent), '
                           '(SELECT count(*) FROM auth_user WHERE username LIKE %s)', ['c98%'])
            counts = cursor.fetchone()
        if tuple(counts) != (20000, 500, 150):
            raise RuntimeError('Existing capacity seed database counts do not match its approved fixture')
        report.update(seedReused=bool(reuse_seed),
                      seedDatabaseCounts={'failures': counts[0], 'agents': counts[1], 'users': counts[2]})
        journeys = [
            {'name': 'session', 'path': '/api/v1/auth/me/', 'weight': 15, 'jsonEquals': {'authenticated': True}},
            {'name': 'falhas-profile', 'path': '/api/v1/falhas/me/', 'weight': 10, 'jsonKey': 'scope'},
            {'name': 'alerts', 'path': '/api/v1/falhas/alerts/?' + query, 'weight': 15, 'jsonKey': 'alerts'},
            {'name': 'cases', 'path': '/api/v1/falhas/cases/?' + query + '&limit=50', 'weight': 25, 'jsonKey': 'items'},
            {'name': 'reincidence', 'path': '/api/v1/falhas/reincidence/?' + query + '&page={variant}', 'weight': 20, 'jsonKey': 'results'},
            {'name': 'csv-export', 'path': '/api/v1/falhas/export/failures.csv?' + query,
             'weight': 2, 'contentType': 'text/csv'},
            {'name': 'portal', 'path': '/secao/indicadores/falhas', 'weight': 13,
             'origin': 'frontend', 'contentType': 'text/html'},
        ]
        (output / 'journeys.json').write_text(json.dumps({'journeys': journeys, 'thinkSeconds': think_seconds,
                  'data': data, 'note': 'Proposed global Planning journeys; not observed corporate traffic.'}, indent=2), encoding='utf-8')
        worker = manager.workers_start(second)
        admin = requests.Session()
        admin.trust_env = False
        admin.cookies.set(private['cookieName'], private['adminCookie'], domain='127.0.0.1', path='/')
        checks = []
        try:
            for path in ('/api/v1/automacoes/status/', '/api/v1/automacoes/config/?mode=controle_sla',
                         '/api/v1/controle-sla/connection/'):
                response = admin.get(frontend + path, timeout=20)
                response.raise_for_status()
                if response.headers.get('X-Pplid-Executor-Generation') != worker['instanceId']:
                    raise RuntimeError('Actual HTTP control did not reach the expected supervisor generation')
                checks.append({'path': path, 'status': response.status_code, 'generation': worker['instanceId']})
                response.close()
            report['httpControl'] = checks
            response = admin.get(backend + '/api/v1/falhas/cases/?' + query, timeout=30)
            response.raise_for_status()
            payload = response.json()
            report['datasetProbe'] = {'count': payload.get('count'), 'returned': payload.get('returned'),
                                      'items': len(payload.get('items') or [])}
            if not payload.get('items') or payload.get('count', 0) <= 0:
                raise RuntimeError('Representative dataset produced no failure rows; load refused')
            response.close()
        finally:
            admin.close()
        report['httpControl'] = checks
        if quick:
            # A short deployment check reuses the completed application suites.
            # It deliberately makes no sustained-capacity or legacy p95 claim.
            report.update(scope='short-deployment-check', sustainedCapacityValidated=False,
                          legacyComparisonExecuted=False, p95GatePassed=None,
                          mixedBusinessJobExecuted=False)
            report['operationalProfile'] = {'activeSessions': 150, 'maxConcurrentHttpRequests': max_in_flight,
                                            'excessRequests': 'client-side queue measured separately'}
            probe_thread = threading.Thread(target=probe, daemon=True)
            probe_thread.start()
            def transition(label, operation):
                operation()
                state = manager.read()
                report.setdefault('transitionDetails', []).append({
                    'action': label, 'active': state.get('active'), 'phase': state.get('phase'),
                    'trafficSwitchMs': state.get('trafficSwitchMs'),
                    'warmupAndSwitchMs': state.get('warmupAndSwitchMs')})
            events = [(10, 'promote-r1', lambda: transition('promote-r1', lambda: manager.promote(first))),
                      (50, 'rollback-r2', lambda: transition('rollback-r2', manager.rollback)),
                      (90, 'retire-inactive', lambda: transition('retire-inactive',
                          lambda: manager.retire(manager.read(), manager.read()['previous'])))]
            result = stage(backend=backend, frontend=frontend, cookies=private['cookies'],
                           cookie_name=private['cookieName'], journeys=journeys, users=150,
                           duration=120, output=output, name='bluegreen-150-short', roots=roots,
                           think_seconds=think_seconds, events=events, sample_extra=database_sample,
                           max_in_flight=max_in_flight)
            report['stages'].append({k: v for k, v in result.items() if k != 'host'})
            state = manager.read()
            manager.healthy(state['slots'][state['active']], public=True)
            report['deploymentRecovered'] = True
            report['ok'] = bool(result['ok'] and len(result['events']) == 3
                                and all(event.get('ok') for event in result['events'])
                                and processes.owned(worker['process']))
            if report['ok']:
                report['checks'].append('short-150-users-three-transitions-and-independent-supervisor')
            return report
        # The legacy baseline uses the exact Waitress settings and Vite preview
        # command of scripts/deploy/lib.ps1, with business starters disabled.
        manager.shutdown()
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(('PPLID_WORKER_', 'PPLID_SUPERVISOR_')):
                env.pop(key, None)
        env['PPLID_WEB_ONLY'] = '1'
        release = config.release(second)
        for label, args, cwd in [
            ('backend', [str(python), '-m', 'waitress', '--channel-timeout=600', '--threads=16',
                         f'--listen=127.0.0.1:{config.backend_port}', 'config.wsgi:application'], release / 'backend'),
            ('frontend', ['node', str(release / 'frontend/node_modules/vite/bin/vite.js'), 'preview',
                          '--port', str(config.frontend_port), '--host', '127.0.0.1', '--strictPort'], release / 'frontend')]:
            processes.assert_free(config.backend_port if label == 'backend' else config.frontend_port)
            stream = (output / ('legacy-' + label + '.log')).open('ab')
            logs.append(stream)
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            legacy.append(processes.identity(process.pid))
        ready = requests.Session()
        ready.trust_env = False
        deadline = time.monotonic() + 180
        try:
            for url in (backend + '/falhas/health/', frontend + '/'):
                while True:
                    try:
                        response = ready.get(url, timeout=5)
                        response.raise_for_status()
                        response.close()
                        break
                    except requests.RequestException:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.5)
        finally:
            ready.close()
        baseline = {}
        report['warmups'] = []
        def warm(label):
            result = stage(backend=backend, frontend=frontend, cookies=private['cookies'],
                           cookie_name=private['cookieName'], journeys=journeys, users=10,
                           duration=30, output=output, name=label, roots=roots,
                           think_seconds=think_seconds, sample_extra=database_sample)
            report['warmups'].append({k: v for k, v in result.items() if k != 'host'})
            if not result['ok']:
                raise RuntimeError('Warmup failed; measured load refused')
        warm('legacy-warmup')
        for users in (10, 50, 100, 150):
            result = stage(backend=backend, frontend=frontend, cookies=private['cookies'],
                           cookie_name=private['cookieName'], journeys=journeys, users=users,
                           duration=phase_seconds, output=output, name=f'legacy-{users}',
                           roots=roots, think_seconds=think_seconds, sample_extra=database_sample)
            baseline[users] = result
            report['stages'].append({k: v for k, v in result.items() if k != 'host'})
            if not result['ok']:
                raise RuntimeError(f'Legacy baseline stopped at {users} users; inspect recorded errors/resources')
        for identity in reversed(legacy):
            # All HTTP client threads have finished; these exact isolated
            # read-only servers have no business jobs and no cooperative hook.
            processes.stop(identity, None, .1, force=True)
        legacy.clear()
        for stream in logs:
            stream.close()
        manager.prepare(second)
        manager.activate()
        warm('bluegreen-warmup')
        probe_thread = threading.Thread(target=probe, daemon=True)
        probe_thread.start()
        for users in (10, 50, 100, 150):
            seconds = max(phase_seconds, sustain_seconds) if users == 150 else phase_seconds
            events = [(max(1, seconds * .25), 'promote-r1', lambda: manager.promote(first)),
                      (max(2, seconds * .5), 'rollback-r2', manager.rollback),
                      (max(3, seconds * .75), 'retire-inactive', lambda: manager.retire(manager.read(), manager.read()['previous']))] if users == 150 else []
            result = stage(backend=backend, frontend=frontend, cookies=private['cookies'],
                           cookie_name=private['cookieName'], journeys=journeys, users=users,
                           duration=seconds, output=output, name=f'bluegreen-{users}', roots=roots,
                           think_seconds=think_seconds, events=events, sample_extra=database_sample)
            result['p95AgainstLegacy'] = (result['p95Ms'] / baseline[users]['p95Ms']) if baseline[users]['p95Ms'] else None
            (output / f'bluegreen-{users}-comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            report['stages'].append({k: v for k, v in result.items() if k != 'host'})
            if not result['ok']:
                raise RuntimeError(f'Blue-green stopped at {users} users; inspect recorded errors/resources')
        if not processes.owned(worker['process']):
            raise RuntimeError('Independent supervisor identity was lost during web transitions')
        report['checks'].append('independent-supervisor-preserved-during-all-web-transitions')
        comparisons = [s.get('p95AgainstLegacy') for s in report['stages'] if s['name'].startswith('bluegreen-')]
        report['p95GatePassed'] = len(comparisons) == 4 and all(v is not None and v <= 1.2 for v in comparisons)
        if not report['p95GatePassed']:
            raise RuntimeError('Normal blue-green stages failed the p95 comparison; additional mixed load refused')
        def import_job():
            report['businessJob'] = mixed_business_job(lab=lab, frontend=frontend, manager=manager, database=database,
                    cookie_name=private['cookieName'], admin_cookie=private['adminCookie'])
            if not report['businessJob']['ok']:
                raise RuntimeError('Mixed real import did not satisfy the persisted data/hash checks')
        mixed = stage(backend=backend, frontend=frontend, cookies=private['cookies'],
                      cookie_name=private['cookieName'], journeys=journeys, users=150, duration=300,
                      output=output, name='bluegreen-150-mixed', roots=roots,
                      think_seconds=think_seconds, events=[(10, 'real-csv-import-and-download', import_job)],
                      sample_extra=database_sample)
        report['mixedStage'] = {k: v for k, v in mixed.items() if k != 'host'}
        report['checks'].append('additional-mixed-stage-has-no-equivalent-legacy-baseline')
        report['ok'] = bool(report['p95GatePassed'] and mixed['ok'] and report.get('businessJob', {}).get('ok') is True
                            and processes.owned(worker['process']))
    except Exception as error:
        report['error'] = str(error)
    finally:
        probe_stop.set()
        if probe_thread:
            probe_thread.join(25)
        (output / 'probes.json').write_text(json.dumps(probes, indent=2), encoding='utf-8')
        failures = [p for p in probes if p['status'] != 200]
        report['probeCount'], report['probeErrors'] = len(probes), len(failures)
        if failures:
            report['ok'] = False
        for identity in reversed(legacy):
            try:
                processes.stop(identity, None, .1, force=True)
            except Exception as error:
                report.update(ok=False, legacyCleanupError=type(error).__name__)
        for stream in logs:
            stream.close()
        if database:
            database.close()
        if worker:
            try:
                manager.workers_stop()
                report['supervisorStopped'] = True
            except Exception as error:
                report.update(ok=False, workerCleanupError=type(error).__name__)
        (output / 'capacity-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('lab', 'pg-bin', 'caddy'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('first', 'second'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--phase-seconds', type=int, default=300)
    parser.add_argument('--sustain-seconds', type=int, default=1800)
    parser.add_argument('--think-seconds', type=float, default=5)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--quick', action='store_true', help='120-second deployment check; no sustained capacity claim')
    parser.add_argument('--resume-stopped', action='store_true', help='Reuse a reviewed stopped runtime and its existing synthetic seed')
    parser.add_argument('--max-in-flight', type=int, help='Bound concurrent HTTP requests; sessions above this limit wait in a measured queue')
    args = parser.parse_args()
    result = smoke(args.lab.resolve(), args.first, args.second, args.pg_bin.resolve(), args.caddy.resolve(),
                   browser_test=args.browser and not args.resume_stopped, resume_stopped=args.resume_stopped,
                   exercise=lambda c: exercise(c, phase_seconds=args.phase_seconds,
                         sustain_seconds=args.sustain_seconds, think_seconds=args.think_seconds,
                         quick=args.quick, reuse_seed=args.resume_stopped, max_in_flight=args.max_in_flight))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
