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
from django.core.management.base import BaseCommand
class Command(BaseCommand):
    def add_arguments(self, parser): parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        with Path('synthetic-ticks').open('a') as stream: stream.write('tick\\n')
''')
    (backend / 'apps/automacoes/services.py').write_text('''from pathlib import Path
class Manager:
    def stop_all(self): Path('synthetic-stopped').write_text('stopped')
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
        assert (backend / 'synthetic-stopped').read_text() == 'stopped'
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
