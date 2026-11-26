import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from deploy.bluegreen.cli import pipeline
from deploy.bluegreen.store import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('exit_code', [0, 1])
def test_pipeline_destination_build_and_result_journal(laboratory, monkeypatch, exit_code):
    config, _, _ = laboratory
    before = {'active': 'A', 'slots': {'A': {'sha': 'a' * 8, 'shaFull': 'a' * 40}}}
    promoted = []
    manager = SimpleNamespace(read=lambda: before, promote=lambda sha, run: promoted.append(sha) or {'phase': 'idle'})
    monkeypatch.setenv('PPLID_PROMOTE_SOURCE', 'DEV')
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'b' * 40 + '\n')
    def preparation(command, **kwargs):
        assert command[0] == 'powershell.exe'
        assert 'PPLID_PROMOTE_SOURCE' not in kwargs['env']
        atomic_json(config.root / 'logs/runs/test-run/steps.json', [{'id': 'prepare', 'status': 'pending'}])
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(subprocess, 'run', preparation)
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
