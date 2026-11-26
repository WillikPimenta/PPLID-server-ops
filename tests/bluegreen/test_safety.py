from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from deploy.bluegreen.application_patch import apply
from deploy.bluegreen.config import Config, strategy, port
from deploy.bluegreen.manager import Manager
from deploy.bluegreen import processes
from deploy.bluegreen.store import atomic_json, environment_lock, read_json
from deploy.bluegreen.slot_worker import WebApplication
import threading


@pytest.mark.parametrize('value', [0, 80, 65536, True, '8000', None])
def test_invalid_ports(value):
    with pytest.raises(ValueError):
        port(value)


def test_legacy_default():
    assert strategy({'MAIN': {}}, 'MAIN') == 'legacy'
    with pytest.raises(ValueError):
        strategy({'MAIN': {'deployStrategy': 'typo'}}, 'MAIN')


def test_disabled_and_colliding_configuration(laboratory):
    config, _, root = laboratory
    duplicate = deepcopy(root)
    duplicate['MAIN']['enabled'] = False
    with pytest.raises(ValueError, match='disabled'):
        Config(config.base, duplicate, 'MAIN')
    duplicate['MAIN']['enabled'] = True
    duplicate['MAIN']['blueGreen']['slots']['B']['backendPort'] = duplicate['MAIN']['backendPort']
    with pytest.raises(ValueError, match='collision'):
        Config(config.base, duplicate, 'MAIN')


def test_state_corruption_fails_closed(laboratory):
    config, manager, _ = laboratory
    config.directory.mkdir(parents=True)
    config.state_path.write_text('{broken')
    with pytest.raises(ValueError):
        manager.read()
    config.state_path.unlink()  # fixture has no processes in this test


def test_release_traversal_and_foreign_port(laboratory):
    config, manager, _ = laboratory
    with pytest.raises(ValueError):
        config.release('../outside')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(OSError):
            processes.assert_free(listener.getsockname()[1])
        assert listener.fileno() != -1


def test_pid_identity_mismatch_never_kills_current_process():
    record = processes.identity(os.getpid())
    assert processes.owned(record)
    record['created'] -= 1
    assert processes.owned(record) is None
    processes.stop(record, None, .1, force=True)
    assert os.getpid() > 0


def test_process_lock_conflicts_with_powershell(tmp_path):
    with environment_lock('HOM', tmp_path):
        command = r"$m=New-Object System.Threading.Mutex($false,'Global\PPLID-Deploy-HOM'); $v=$m.WaitOne(0); if($v){$m.ReleaseMutex()}; $m.Dispose(); if($v){exit 1}else{exit 0}"
        result = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True)
        assert result.returncode == 0


def test_atomic_json_is_readable(tmp_path):
    path = tmp_path / 'state.json'
    for number in range(20):
        atomic_json(path, {'generation': number})
        assert read_json(path)['generation'] == number


def test_immutable_asset_collision(laboratory, make_release):
    config, manager, _ = laboratory
    sha = make_release(config, 'e')
    release = config.release(sha)
    manager.publish_assets(release)
    asset = release / f'frontend/dist/assets/{sha}.js'
    asset.write_text('different')
    with pytest.raises(RuntimeError, match='collision'):
        manager.publish_assets(release)


def test_resources_reject_before_launch(laboratory, monkeypatch):
    config, manager, _ = laboratory
    from types import SimpleNamespace
    monkeypatch.setattr('deploy.bluegreen.manager.psutil.virtual_memory', lambda: SimpleNamespace(available=0))
    with pytest.raises(RuntimeError, match='memory'):
        manager.resources()


def test_commit_exhaustion_blocks_workers_and_build_before_launch(laboratory, monkeypatch):
    from types import SimpleNamespace
    from deploy.bluegreen.cli import pipeline
    config, manager, _ = laboratory
    monkeypatch.setattr('deploy.bluegreen.manager.psutil.virtual_memory',
                        lambda: SimpleNamespace(available=8 * 1024**3))
    monkeypatch.setattr('deploy.bluegreen.manager.available_commit_bytes', lambda: 0)
    monkeypatch.setattr('deploy.bluegreen.manager.processes.launch',
                        lambda *a, **k: pytest.fail('Resource rejection must precede process launch'))
    with pytest.raises(RuntimeError, match='commit headroom'):
        manager.workers_start('a' * 8)
    assert not (config.directory / 'background.json').exists()
    assert not config.state_path.exists()
    original = {'phase': 'idle', 'active': 'A', 'slots': {'A': {'shaFull': 'b' * 40},
                                                       'B': {'shaFull': 'c' * 40}}}
    monkeypatch.setattr(manager, 'read', lambda: original)
    monkeypatch.setattr(manager, 'retire', lambda *a: pytest.fail('Warm rollback must be preserved'))
    with pytest.raises(RuntimeError, match='commit headroom'):
        manager.promote('a' * 8)
    assert original['phase'] == 'idle' and set(original['slots']) == {'A', 'B'}
    monkeypatch.setattr('deploy.bluegreen.cli.subprocess.check_output', lambda *a, **k: 'a' * 40)
    monkeypatch.setattr('deploy.bluegreen.cli.subprocess.run',
                        lambda *a, **k: pytest.fail('Resource rejection must precede build'))
    with pytest.raises(RuntimeError, match='commit headroom'):
        pipeline(config, manager, 'a' * 8, 'guarded-build')
    assert not (config.root / 'logs/runs/guarded-build').exists()


def test_disk_exhaustion_is_rejected(laboratory, monkeypatch):
    from types import SimpleNamespace
    _, manager, _ = laboratory
    monkeypatch.setattr('deploy.bluegreen.manager.psutil.virtual_memory',
                        lambda: SimpleNamespace(available=8 * 1024**3))
    monkeypatch.setattr('deploy.bluegreen.manager.available_commit_bytes', lambda: 8 * 1024**3)
    monkeypatch.setattr('deploy.bluegreen.manager.shutil.disk_usage', lambda path: SimpleNamespace(free=0))
    with pytest.raises(RuntimeError, match='disk space'):
        manager.resources()


def test_native_commit_reading_and_optional_configuration(laboratory):
    from deploy.bluegreen.resources import available_commit_bytes
    config, _, root = laboratory
    assert available_commit_bytes() > 0
    assert config.minimum_commit_mb == config.bg['minimumAvailableMb']
    for invalid in (True, '2048', 0, -1):
        changed = deepcopy(root)
        changed['MAIN']['blueGreen']['minimumCommitMb'] = invalid
        with pytest.raises(ValueError, match='minimumCommitMb'):
            Config(config.base, changed, 'MAIN')


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction compatibility pointer')
def test_junction_failure_preserves_diagnostic(laboratory, monkeypatch):
    from types import SimpleNamespace
    config, manager, _ = laboratory
    monkeypatch.setattr('deploy.bluegreen.manager.subprocess.run', lambda *a, **k:
                        SimpleNamespace(returncode=17, stderr=b'synthetic junction diagnostic'))
    state = {'active': 'A', 'phase': 'active', 'slots': {'A': {
        'sha': 'a' * 8, 'shaFull': 'a' * 40, 'release': str(config.release('a' * 8))}}}
    with pytest.raises(RuntimeError, match=r'exit 17.*synthetic junction diagnostic'):
        manager.sync_legacy(state)


def test_build_propagates_selected_machine_config(laboratory, monkeypatch):
    from types import SimpleNamespace
    from deploy.bluegreen.cli import pipeline
    config, manager, root = laboratory
    machine = config.base / 'explicit-machine.json'
    env_path = config.base / 'explicit-environments.json'
    atomic_json(machine, {'baseDir': str(config.base), 'envConfigPath': str(env_path)})
    atomic_json(env_path, root)
    loaded = Config.load(machine, 'MAIN')
    manager.config = loaded
    state = {'active': 'A', 'slots': {'A': {'shaFull': 'b' * 40, 'sha': 'b' * 8}}}
    monkeypatch.setattr(manager, 'read', lambda: state)
    monkeypatch.setattr(manager, 'resources', lambda: None)
    monkeypatch.setattr(manager, 'promote', lambda *args: state)
    monkeypatch.setattr('deploy.bluegreen.cli.subprocess.check_output', lambda *a, **k: 'a' * 40)
    monkeypatch.setenv('OPS_MACHINE_CONFIG', 'unrelated-inherited-configuration.json')
    calls = []
    def build(*args, **kwargs):
        calls.append(kwargs['env']['OPS_MACHINE_CONFIG'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('deploy.bluegreen.cli.run_build', build)
    assert pipeline(loaded, manager, 'a' * 8, 'explicit-config-build') == state
    assert calls == [str(machine.resolve())]


def test_application_patch_is_idempotent_and_preserves_other_content(tmp_path):
    common = tmp_path / 'backend/apps/common/apps.py'
    auto = tmp_path / 'backend/apps/automacoes/apps.py'
    common.parent.mkdir(parents=True)
    auto.parent.mkdir(parents=True)
    common.write_text('    def ready(self):\n        from .bi_sync_scheduler import ensure_scheduler_started\n# untouched\n')
    auto.write_text('        if serving and not reloader_parent:\n            start_background()\n')
    apply(tmp_path)
    first = common.read_text(), auto.read_text()
    apply(tmp_path)
    assert first == (common.read_text(), auto.read_text())
    assert '# untouched' in first[0]


def test_wsgi_iterator_cleanup_counts_long_requests(tmp_path):
    ready, drain = threading.Event(), threading.Event()
    ready.set()
    closed = []
    def app(environ, start):
        try:
            yield b'first'
            yield b'last'
        finally:
            closed.append(True)
    wrapper = WebApplication(app, {}, ready, drain, lambda: None)
    output = wrapper({'PATH_INFO': '/normal'}, lambda *args: None)
    assert wrapper.active == 1
    assert next(output) == b'first'
    output.close()
    assert wrapper.active == 0 and closed


def test_contract_is_required(laboratory, make_release):
    config, manager, _ = laboratory
    sha = make_release(config, 'f')
    (config.release(sha) / 'scripts/deploy/bluegreen-contract.json').unlink()
    with pytest.raises(FileNotFoundError):
        manager.contract(config.release(sha), sha)


def test_background_jobs_require_explicit_ownership(laboratory):
    config, manager, _ = laboratory
    config.bg['workersOwnershipConfirmed'] = False
    with pytest.raises(RuntimeError, match='ownership'):
        manager.workers_start('a' * 8)
    with pytest.raises(RuntimeError, match='ownership'):
        manager.activate()


def test_executable_pin_is_enforced(laboratory):
    config, _, _ = laboratory
    config.caddy_hash = '0' * 64
    from deploy.bluegreen.caddy import verify_binary
    with pytest.raises(ValueError, match='hash'):
        verify_binary(config)
