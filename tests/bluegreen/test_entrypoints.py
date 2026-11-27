import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from deploy.bluegreen.cli import pipeline
from deploy.bluegreen.config import Config
from deploy.bluegreen.store import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[2]


def test_initial_build_cli_creates_valid_receipt_without_activating_any_runtime(
        laboratory, make_release, monkeypatch, capsys):
    from deploy.bluegreen import cli
    config, manager, root = laboratory
    machine, environment_path = config.base / 'machine.json', config.base / 'environments.json'
    atomic_json(environment_path, root)
    atomic_json(machine, {'baseDir': str(config.base), 'envConfigPath': str(environment_path)})
    config = Config.load(machine, 'MAIN')
    manager.config = config
    assert not config.state_path.exists()
    for name in ('prepare', 'activate', 'promote', 'workers_start'):
        monkeypatch.setattr(manager, name, lambda *a, **k: pytest.fail('Build must never change runtime'))
    monkeypatch.setattr(cli, 'Manager', lambda _: manager)
    monkeypatch.setattr(cli.subprocess, 'check_output', lambda *a, **k: 'b' * 40)
    held = []
    @contextmanager
    def locked(environment, root):
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(cli, 'environment_lock', locked)
    def preparation(command, **kwargs):
        assert held and kwargs['sha_full'] == 'b' * 40
        assert kwargs['receipt'] == config.directory / 'builds/bbbbbbbb.json'
        make_release(config, 'b')
        atomic_json(kwargs['receipt'], {'schemaVersion': 1, 'phase': 'complete', 'success': True,
                    'treeEmpty': True, 'shaFull': kwargs['sha_full'], 'release': str(kwargs['release'])})
        atomic_json(config.root / 'logs/runs/initial-build/steps.json', [{'id': 'promote', 'status': 'pending'}])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli, 'run_build', preparation)
    monkeypatch.setattr(sys, 'argv', ['bluegreen', 'build', '--machine-config', str(machine),
                        '--environment', 'MAIN', '--sha', 'b' * 8, '--run-id', 'initial-build'])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)['state']
    assert result['built'] and not result['reused'] and result['shaFull'] == 'b' * 40
    assert manager.contract(config.release('b' * 8), 'b' * 8) == 'b' * 40
    assert not config.state_path.exists() and not (config.directory / 'background.json').exists()
    assert read_json(config.root / 'logs/runs/initial-build/run-summary.json')['fromSha'] is None
    assert read_json(config.root / 'logs/runs/initial-build/steps.json')[0]['status'] == 'pending'
    assert not held


@pytest.mark.parametrize('pin', ['slot', 'background'])
def test_build_only_reuses_pinned_artifact_without_build_promotion_or_env_changes(
        laboratory, make_release, monkeypatch, pin):
    config, manager, _ = laboratory
    sha = make_release(config, 'b')
    release = config.release(sha)
    before = (release / 'backend/.env').read_bytes()
    state = {'active': None, 'slots': {}}
    if pin == 'slot':
        state.update(active='A', slots={'A': {'sha': sha, 'shaFull': 'b' * 40}})
    else:
        atomic_json(config.directory / 'background.json',
                    {'sha': sha, 'release': str(release), 'environment': 'MAIN', 'phase': 'stopped'})
    monkeypatch.setattr(manager, 'read', lambda **kwargs: state)
    monkeypatch.setattr(manager, 'promote', lambda *a: pytest.fail('Build must never promote'))
    monkeypatch.setattr('deploy.bluegreen.cli.subprocess.check_output', lambda *a, **k: 'b' * 40)
    monkeypatch.setattr('deploy.bluegreen.cli.run_build', lambda *a, **k: pytest.fail('Pinned release must never rebuild'))
    result = pipeline(config, manager, sha, 'reuse-only', build_only=True)
    assert result['built'] and result['reused'] and result['shaFull'] == 'b' * 40
    assert (release / 'backend/.env').read_bytes() == before


def test_powershell_bridge_accepts_build_without_invoking_the_action():
    script = ("$command = Get-Command -Name $env:PPLID_TEST_BRIDGE; "
              "$command.Parameters['Action'].Attributes | Where-Object { $_ -is [Management.Automation.ValidateSetAttribute] } | "
              "ForEach-Object { $_.ValidValues } | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                            env=dict(os.environ, PPLID_TEST_BRIDGE=str(ROOT / 'deploy/bluegreen.ps1')),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'build' in json.loads(result.stdout)


@pytest.mark.parametrize('exit_code', [0, 1])
def test_pipeline_destination_build_and_result_journal(laboratory, monkeypatch, exit_code):
    config, real_manager, root = laboratory
    env_path, machine_path = config.base / 'env.json', config.base / 'machine.json'
    atomic_json(env_path, root)
    atomic_json(machine_path, {'baseDir': str(config.base), 'envConfigPath': str(env_path)})
    config = Config.load(machine_path, 'MAIN')
    before = {'active': 'A', 'slots': {'A': {'sha': 'a' * 8, 'shaFull': 'a' * 40}}}
    promoted = []
    manager = SimpleNamespace(read=lambda: before, resources=real_manager.resources,
                              cancelled=real_manager.cancelled,
                              promote=lambda sha, run: promoted.append(sha) or {'phase': 'idle'})
    monkeypatch.setenv('PPLID_PROMOTE_SOURCE', 'DEV')
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'b' * 40 + '\n')
    def preparation(command, **kwargs):
        assert command[0] == 'powershell.exe'
        assert 'PPLID_PROMOTE_SOURCE' not in kwargs['env']
        assert kwargs['env']['OPS_MACHINE_CONFIG'] == str(machine_path)
        atomic_json(config.root / 'logs/runs/test-run/steps.json', [{'id': 'prepare', 'status': 'pending'}])
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr('deploy.bluegreen.cli.run_build', preparation)
    if exit_code:
        with pytest.raises(RuntimeError, match='preserved'):
            pipeline(config, manager, 'b' * 8, 'test-run')
        assert not promoted
    else:
        assert pipeline(config, manager, 'b' * 8, 'test-run')['phase'] == 'idle'
        assert promoted == ['b' * 8]
    summary = read_json(config.root / 'logs/runs/test-run/run-summary.json')
    assert summary['result'] == ('failed' if exit_code else 'success')
    assert read_json(config.root / 'deploy-state.json')['status'] == ('failed' if exit_code else 'idle')


def test_powershell_bridge_uses_explicit_machine_configuration(laboratory):
    config, _, root = laboratory
    env_path = config.base / 'env.json'
    machine_path = config.base / 'machine.json'
    atomic_json(env_path, root)
    atomic_json(machine_path, {'baseDir': str(config.base), 'envConfigPath': str(env_path)})
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(ROOT / 'deploy/bluegreen.ps1'), '-Action', 'status', '-Environment', 'MAIN',
                             '-MachineConfig', str(machine_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['ok'] and payload['state']['phase'] == 'uninitialized'
    assert not config.state_path.exists()


@pytest.mark.parametrize('valid_receipt', [True, False])
def test_pipeline_never_rebuilds_a_release_pinned_only_by_the_independent_supervisor(
        laboratory, make_release, monkeypatch, valid_receipt):
    config, real_manager, _ = laboratory
    sha = make_release(config, 'b')
    release = config.release(sha)
    before_env = (release / 'backend/.env').read_bytes()
    atomic_json(config.directory / 'background.json', {
        'sha': sha, 'release': str(release), 'environment': 'MAIN', 'phase': 'stopped'})
    if not valid_receipt:
        (config.directory / 'builds' / (sha + '.json')).unlink()
    promoted = []
    manager = SimpleNamespace(read=lambda: {'active': 'A', 'slots': {'A': {'shaFull': 'a' * 40}}},
                              contract=real_manager.contract,
                              promote=lambda target, run: promoted.append(target) or {'phase': 'idle'})
    monkeypatch.setattr('deploy.bluegreen.cli.subprocess.check_output', lambda *a, **k: 'b' * 40)
    monkeypatch.setattr('deploy.bluegreen.cli.run_build', lambda *a, **k: pytest.fail('Pinned release must never rebuild'))
    if valid_receipt:
        assert pipeline(config, manager, sha, 'pinned-reuse')['phase'] == 'idle'
        assert promoted == [sha]
    else:
        with pytest.raises(RuntimeError, match='pins this release.*receipt'):
            pipeline(config, manager, sha, 'pinned-reuse')
        assert not promoted
    assert (release / 'backend/.env').read_bytes() == before_env


@pytest.mark.parametrize('override', ['machine', 'environment', 'missing'])
def test_ops_store_respects_explicit_configuration(tmp_path, override):
    machine = tmp_path / 'machine.json'
    atomic_json(machine, {'baseDir': str(tmp_path)})
    environment = dict(os.environ, OPS_MACHINE_CONFIG=str(machine))
    environment.pop('PPLID_OPS_STORE_PATH', None)
    expected = tmp_path / 'ops/data/ops-store.db'
    if override == 'environment':
        expected = tmp_path / 'explicit.db'
        environment['PPLID_OPS_STORE_PATH'] = str(expected)
    if override == 'missing':
        environment['OPS_MACHINE_CONFIG'] = str(tmp_path / 'missing.json')
    script = "$ErrorActionPreference='Stop'; . ./lib/ops_store.ps1; Get-OpsStoreDbPath"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15)
    if override == 'missing':
        assert result.returncode != 0
        assert 'fallback' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == expected


def test_schema_plan_reports_real_pending_migrations(laboratory, make_release):
    config, _, _ = laboratory
    sha = make_release(config, 'a')
    backend = config.release(sha) / 'backend'
    settings = backend / 'config/settings.py'
    settings.write_text(settings.read_text() + "\nINSTALLED_APPS=['django.contrib.contenttypes']\n")
    (backend / 'manage.py').write_text("import os,sys\nos.environ['DJANGO_SETTINGS_MODULE']='config.settings'\nfrom django.core.management import execute_from_command_line\nexecute_from_command_line(sys.argv)\n")
    environment = dict(os.environ, READINESS_BACKEND=str(backend),
                       READINESS_PYTHON=str(backend / '.venv/Scripts/python.exe'))
    script = ("$ErrorActionPreference='Stop'; . ./deploy/lib/backend_deploy.ps1; "
              "Invoke-PplidBackendMigratePlan -BackendDir $env:READINESS_BACKEND -VenvPython $env:READINESS_PYTHON")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '2'
