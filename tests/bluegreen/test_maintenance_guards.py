"""Exercise real PowerShell maintenance against an explicitly isolated layout."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from deploy.bluegreen.store import atomic_json, environment_lock


ROOT = Path(__file__).resolve().parents[2]


def environment(tmp_path, **machine):
    path = tmp_path / 'machine.json'
    atomic_json(path, {'baseDir': str(tmp_path), **machine})
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PROGRAMDATA'}}
    env.update(OPS_MACHINE_CONFIG=str(path), TEST_RELEASE_LIB=str(ROOT / 'deploy/lib/release_cleanup.ps1'),
               TEST_PATHS_LIB=str(ROOT / 'lib/paths.ps1'))
    return env


@pytest.mark.parametrize('script,args', [('cleanup_release.ps1', ['-Sha', 'e' * 8]),
                                        ('prune_releases.ps1', ['-KeepCount', '0', '-DryRun'])])
def test_real_maintenance_entrypoint_refuses_deployment_lock(tmp_path, script, args):
    env = environment(tmp_path)
    marker = tmp_path / 'deploy/MAIN/releases' / ('e' * 8) / 'sentinel'
    marker.parent.mkdir(parents=True)
    marker.write_text('preserve this synthetic release')
    with environment_lock('MAIN', tmp_path / 'deploy/MAIN'):
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(ROOT / 'deploy' / script), '-Environment', 'MAIN', *args],
                                env=env, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert b'manutencao de releases bloqueada' in result.stderr
    assert marker.read_text() == 'preserve this synthetic release'


@pytest.mark.parametrize('corrupt', [False, True])
def test_slot_supervisor_and_failed_build_releases_remain_protected(tmp_path, corrupt):
    env = environment(tmp_path)
    root = tmp_path / 'deploy/MAIN'
    runtime = root / 'bluegreen/state.json'
    atomic_json(runtime, {'schemaVersion': 1, 'slots': {'A': {'sha': 'a' * 8}, 'B': {'sha': 'b' * 8}}})
    atomic_json(root / 'bluegreen/background.json', {'sha': 'c' * 8})
    atomic_json(root / 'deploy-state.json', {'targetSha': 'd' * 8, 'status': 'failed'})
    if corrupt:
        runtime.write_text('{broken')
    script = "$ErrorActionPreference='Stop'; . $env:TEST_RELEASE_LIB; @(Get-PplidProtectedReleaseShas -Environment MAIN) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                            env=env, capture_output=True, timeout=30)
    if corrupt:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert set(json.loads(result.stdout)) == {c * 8 for c in 'abcd'}


@pytest.mark.parametrize('failure', ['missing', 'invalid-json', 'missing-base', 'relative-base', 'missing-env'])
def test_explicit_invalid_configuration_never_falls_back(tmp_path, failure):
    env = environment(tmp_path)
    machine = Path(env['OPS_MACHINE_CONFIG'])
    if failure == 'missing':
        env['OPS_MACHINE_CONFIG'] = str(tmp_path / 'nonexistent.json')
    elif failure == 'invalid-json':
        machine.write_text('{broken')
    elif failure == 'missing-base':
        atomic_json(machine, {})
    elif failure == 'relative-base':
        atomic_json(machine, {'baseDir': '../unexpected'})
    else:
        atomic_json(machine, {'baseDir': str(tmp_path), 'envConfigPath': str(tmp_path / 'absent-env.json')})
    # Even with the caller's default Continue preference, invalid JSON and an
    # explicit missing path must stop before any default location is returned.
    script = ". $env:TEST_PATHS_LIB; Get-PplidEnvConfigPath"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert (b'ConvertFrom-Json' if failure == 'invalid-json' else b'fallback') in result.stderr
    assert not result.stdout.strip()
    assert not (tmp_path / 'deploy').exists()
