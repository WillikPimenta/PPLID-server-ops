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
