"""Real supervisor lifecycle with synthetic jobs; no corporate starters run."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

import pytest

from deploy.bluegreen import processes
from deploy.bluegreen.store import atomic_json, read_json


def install_synthetic_jobs(config, sha):
    backend = config.release(sha) / 'backend'
    settings = backend / 'config/settings.py'
    settings.write_text(settings.read_text() + "\nINSTALLED_APPS=['samplejobs']\n")
    for relative in ('samplejobs', 'samplejobs/management', 'samplejobs/management/commands', 'apps', 'apps/automacoes'):
        directory = backend / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / '__init__.py').write_text('')
    (backend / 'samplejobs/apps.py').write_text('''import os
from pathlib import Path
from django.apps import AppConfig
class SampleJobsConfig(AppConfig):
    name = 'samplejobs'
    def ready(self):
        if os.environ.get('PPLID_WEB_ONLY') != '1':
            with Path('synthetic-starts').open('a') as stream: stream.write('start\\n')
''')
    (backend / 'samplejobs/management/commands/run_event_scheduler.py').write_text('''from pathlib import Path
import time
from django.core.management.base import BaseCommand
class Command(BaseCommand):
    def add_arguments(self, parser): parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        with Path('synthetic-ticks').open('a') as stream: stream.write('tick\\n')
        if Path('synthetic-hold').exists():
            Path('synthetic-busy').touch()
            while not Path('synthetic-finish').exists(): time.sleep(.05)
            Path('synthetic-completed').touch()
''')
    (backend / 'apps/automacoes/services.py').write_text('''from pathlib import Path
class Manager:
    def stop_all(self): raise AssertionError('Forced legacy stop must never be called by deploy')
def get_robot_manager(): return Manager()
''')
    return backend


def test_background_singleton_identity_restart_and_web_isolation(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, 'e')
    second = make_release(config, 'f')
    backend = install_synthetic_jobs(config, first)
    record = None
    duplicate = None
    try:
        manager.prepare(first)
        manager.activate()
        assert not (backend / 'synthetic-starts').exists()
        record = manager.workers_start(first)
        assert manager.workers_start(first)['instanceId'] == record['instanceId']
        # Exercise the actual process lifetime mutex with a second launcher.
        command = record['process']['cmdline']
        duplicate = processes.launch(command, config.directory / 'duplicate-synthetic.log')
        assert duplicate.wait(timeout=15) != 0
        assert (backend / 'synthetic-starts').read_text().splitlines() == ['start']
        # Lost identity write is recovered from exact executable + command.
        saved = read_json(config.directory / 'background.json')
        saved['process'] = None
        saved['phase'] = 'starting'
        atomic_json(config.directory / 'background.json', saved)
        reconciled = manager.workers_start(first)
        assert reconciled['process']['pid'] == record['process']['pid']
        assert reconciled['phase'] == 'running'
        manager.promote(second)
        manager.rollback()
        assert processes.owned(record['process'])
        assert (backend / 'synthetic-starts').read_text().splitlines() == ['start']
        stopped = manager.workers_stop()
        assert stopped['phase'] == 'stopped'
        assert read_json(Path(stopped['stoppedFile']))['drained'] is True
        next_record = manager.workers_start(first)
        assert next_record['instanceId'] != record['instanceId']
        record = next_record
        assert (backend / 'synthetic-starts').read_text().splitlines() == ['start', 'start']
        manager.workers_stop()
        assert manager.workers_stop()['phase'] == 'stopped'
    finally:
        if record:
            processes.stop(record['process'], Path(record['stopFile']), 3, force=True)
        if duplicate and duplicate.poll() is None:
            duplicate.terminate()
            duplicate.wait(timeout=10)


def test_background_stale_readiness_and_unconfirmed_exit_are_refused(laboratory, make_release):
    config, manager, _ = laboratory
    sha = make_release(config, 'a')
    install_synthetic_jobs(config, sha)
    record = manager.workers_start(sha)
    try:
        atomic_json(Path(record['readyFile']), {'ready': True, 'sha': sha, 'instanceId': 'old', 'pid': record['process']['pid']})
        with pytest.raises(RuntimeError, match='identity mismatch'):
            manager.workers_start(sha)
        processes.stop(record['process'], None, .05, force=True)
        with pytest.raises(RuntimeError, match='unconfirmed'):
            manager.workers_start(sha)
        with pytest.raises(RuntimeError, match='Cannot confirm'):
            manager.workers_stop()
    finally:
        processes.stop(record['process'], Path(record['stopFile']), 3, force=True)


def test_supervisor_timeout_preserves_job_and_requires_clean_receipt(laboratory, make_release):
    config, manager, _ = laboratory
    sha = make_release(config, 'b')
    backend = install_synthetic_jobs(config, sha)
    (backend / 'synthetic-hold').touch()
    record = manager.workers_start(sha)
    try:
        deadline = time.monotonic() + 10
        while not (backend / 'synthetic-busy').exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        config.timeouts['commandSeconds'] = .2
        with pytest.raises(RuntimeError, match='still draining'):
            manager.workers_stop()
        assert processes.owned(record['process'])
        with pytest.raises(RuntimeError, match='Stop the owned'):
            manager.workers_start(sha)
        # A crash can persist draining before writing the stop marker.
        Path(record['stopFile']).unlink()
        with pytest.raises(RuntimeError, match='Stop the owned'):
            manager.workers_start(sha)
        assert read_json(config.directory / 'background.json')['phase'] == 'draining'
        assert not Path(record['stoppedFile']).exists()
        (backend / 'synthetic-finish').touch()
        config.timeouts['commandSeconds'] = 10
        stopped = manager.workers_stop()
        assert stopped['phase'] == 'stopped'
        assert (backend / 'synthetic-completed').exists()
        assert read_json(Path(stopped['stoppedFile']))['drained']
    finally:
        (backend / 'synthetic-finish').touch()
        processes.stop(record['process'], Path(record['stopFile']), 5, force=True)


def test_supervisor_retries_receipt_after_temporary_write_failure(laboratory, make_release):
    from deploy.bluegreen.worker_control import controller
    config, manager, _ = laboratory
    sha = make_release(config, 'c')
    install_synthetic_jobs(config, sha)
    record = manager.workers_start(sha)
    receipt = Path(record['stoppedFile'])
    receipt.mkdir()  # Force atomic receipt replacement to fail.
    control = controller(record['release'], record['workerControlDir'])
    try:
        control.request(record['instanceId'])
        log = receipt.parent / 'worker.log'
        deadline = time.monotonic() + 15
        while 'Drain is unconfirmed' not in log.read_text(encoding='utf-8', errors='replace'):
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert control.status()['phase'] == 'stopped'
        assert processes.owned(record['process'])
        receipt.rmdir()
        deadline = time.monotonic() + 10
        while not receipt.is_file():
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert manager.workers_stop()['phase'] == 'stopped'
        assert read_json(receipt)['drained'] is True
    finally:
        if receipt.is_dir():
            receipt.rmdir()
        processes.stop(record['process'], Path(record['stopFile']), 5, force=True)


def test_retiring_web_slot_preserves_admitted_child_and_independent_worker(laboratory, make_release):
    from deploy.bluegreen.caddy import request
    config, manager, _ = laboratory
    first, second = make_release(config, 'c'), make_release(config, 'd')
    backend = install_synthetic_jobs(config, first)
    wsgi = backend / 'config/wsgi.py'
    source = wsgi.read_text().replace('def application(', 'def normal_application(', 1)
    source += '''
import subprocess, sys
from pathlib import Path
from app.core import worker_drain as drain
def application(environ, start_response):
    if environ.get('PATH_INFO') == '/api/start-owned-job':
        code="from pathlib import Path;import sys,time;Path(sys.argv[1]).touch();\\nwhile not Path(sys.argv[2]).exists():time.sleep(.05)"
        drain.popen([sys.executable,'-c',code,str(Path('owned-started').resolve()),str(Path('owned-finish').resolve())],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        start_response('200 OK',[('Content-Type','application/json')])
        return [b'{"started":true}']
    return normal_application(environ,start_response)
'''
    wsgi.write_text(source)
    record = manager.workers_start(first)
    try:
        manager.prepare(first)
        manager.activate()
        old = manager.read()['slots']['A']['process']
        assert request(f'http://127.0.0.1:{config.backend_port}/api/start-owned-job')['started']
        deadline = time.monotonic() + 10
        while not (backend / 'owned-started').exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        manager.promote(second)
        config.timeouts['stopSeconds'] = .2
        with pytest.raises(RuntimeError, match='still draining'):
            manager.retire(manager.read(), 'A')
        assert processes.owned(old) and processes.owned(record['process'])
        assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == second
        (backend / 'owned-finish').touch()
        deadline = time.monotonic() + 10
        while processes.owned(old):
            assert time.monotonic() < deadline
            time.sleep(.05)
        manager.retire(manager.read(), 'A')
        assert processes.owned(record['process'])
        manager.workers_stop()
    finally:
        (backend / 'owned-finish').touch()
        processes.stop(record['process'], Path(record['stopFile']), 5, force=True)
