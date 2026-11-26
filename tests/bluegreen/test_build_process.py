"""Real Windows build-only Job Objects; no pip/npm downloads or business jobs."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from deploy.bluegreen import build_process, processes
from deploy.bluegreen.store import atomic_json, read_json

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows 10+ build containment')


@pytest.fixture
def tree(tmp_path):
    folder = tmp_path / 'tree'
    folder.mkdir()
    script = tmp_path / 'tree.py'
    script.write_text('''import json,os,pathlib,subprocess,sys,time
import psutil
folder=pathlib.Path(sys.argv[1]); level=int(sys.argv[2]); seconds=float(sys.argv[3])
process=psutil.Process()
record={'pid':process.pid,'created':process.create_time(),'exe':str(pathlib.Path(process.exe()).resolve()),'cmdline':process.cmdline(),
        'role':('build','pip-install-synthetic','npm-build-synthetic')[level],
        'webOnly':os.getenv('PPLID_WEB_ONLY'),'scheduler':os.getenv('BI_SYNC_SCHEDULER_AUTOSTART'),
        'inheritedWorkerFlags':[key for key in os.environ if key.startswith(('PPLID_WORKER_','PPLID_SUPERVISOR_'))]}
(folder/(str(level)+'.json')).write_text(json.dumps(record))
if level<2:
    subprocess.Popen([sys.executable,__file__,str(folder),str(level+1),str(seconds)],creationflags=subprocess.CREATE_NO_WINDOW)
if level==0: (folder/'partial.txt').write_text('building')
time.sleep(seconds + level*.15)
if level==0: (folder/'built.json').write_text('{"built":true}')
''', encoding='utf-8')
    return script, folder


def arguments(tmp_path, tree, seconds=60):
    script, folder = tree
    return dict(command=[sys.executable, str(script), str(folder), '0', str(seconds)],
                env=dict(os.environ, PPLID_WORKER_OWNER='must-not-be-inherited',
                         PPLID_SUPERVISOR_CONTROL_FILE='must-not-be-inherited'),
                timeout=5, receipt=tmp_path / 'builds/aaaaaaaa.json', release=tmp_path / 'releases/aaaaaaaa',
                sha_full='a' * 40)


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'synthetic process condition timed out'
        time.sleep(.05)


def assert_tree_gone(folder):
    records = [read_json(path) for path in folder.glob('[012].json')]
    assert len(records) == 3
    wait_for(lambda: all(processes.owned(record) is None for record in records))
    return records


def test_success_requires_parent_and_all_build_descendants_to_exit(tmp_path, tree):
    args = arguments(tmp_path, tree, seconds=.4)
    with (tmp_path / 'build.log').open('ab') as output:
        result = build_process.run_build(stdout=output, **args)
    assert result.returncode == 0
    records = assert_tree_gone(tree[1])
    assert all(row['webOnly'] == '1' and row['scheduler'] == 'False' and not row['inheritedWorkerFlags'] for row in records)
    receipt = read_json(args['receipt'])
    assert receipt['success'] and receipt['treeEmpty'] and receipt['phase'] == 'complete'
    build_process.require_receipt(args['receipt'].parent.parent, args['release'], args['sha_full'])


def test_timeout_terminates_only_the_owned_build_tree_and_never_publishes_partial(tmp_path, tree):
    args = arguments(tmp_path, tree)
    with (tmp_path / 'build.log').open('ab') as output:
        with pytest.raises(subprocess.TimeoutExpired):
            build_process.run_build(stdout=output, **args)
    assert_tree_gone(tree[1])
    assert not (tree[1] / 'built.json').exists()
    receipt = read_json(args['receipt'])
    assert receipt['treeEmpty'] and not receipt['success'] and receipt['phase'] == 'failed'
    with pytest.raises(RuntimeError, match='receipt'):
        build_process.require_receipt(args['receipt'].parent.parent, args['release'], args['sha_full'])


def test_cancellation_or_resource_refusal_waits_for_build_tree_zero(tmp_path, tree):
    args = arguments(tmp_path, tree)
    def cancelled():
        if len(list(tree[1].glob('[012].json'))) == 3:
            raise RuntimeError('controlled resource/cancellation refusal')
    with (tmp_path / 'build.log').open('ab') as output:
        with pytest.raises(RuntimeError, match='controlled'):
            build_process.run_build(stdout=output, check_alive=cancelled, **args)
    assert_tree_gone(tree[1])
    assert read_json(args['receipt'])['treeEmpty']
    assert not (tree[1] / 'built.json').exists()


@pytest.mark.parametrize('invalid_executable', [False, True])
def test_failed_create_process_leaves_no_running_build(tmp_path, tree, invalid_executable):
    args = arguments(tmp_path, tree)
    executable = tmp_path / 'missing.exe'
    if invalid_executable:
        executable.write_bytes(b'Not a Windows executable')
    args['command'] = [str(executable)]
    with (tmp_path / 'build.log').open('ab') as output:
        with pytest.raises(OSError):
            build_process.run_build(stdout=output, **args)
    receipt = read_json(args['receipt'])
    assert receipt['treeEmpty'] and not receipt['success'] and 'process' not in receipt
    assert not list(tree[1].iterdir())


def test_powershell_receives_unicode_environment_and_only_redirected_standard_handles(tmp_path):
    folder = tmp_path / 'compila\u00e7\u00e3o'
    folder.mkdir()
    log = folder / 'saida.log'
    sample = 'a\u00e7\u00e3o'
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
               "[Console]::Out.WriteLine('stdout-handle'); [Console]::Error.WriteLine('stderr-handle'); "
               "[IO.File]::WriteAllText((Join-Path $pwd 'unicode.txt'), $env:PPLID_BUILD_SAMPLE)"]
    with log.open('ab') as output:
        result = build_process.run_build(command, stdout=output, env=dict(os.environ, PPLID_BUILD_SAMPLE=sample),
                    cwd=folder, timeout=15, receipt=tmp_path / 'builds/aaaaaaaa.json',
                    release=tmp_path / 'releases/aaaaaaaa', sha_full='a' * 40)
    assert result.returncode == 0
    assert (folder / 'unicode.txt').read_text(encoding='utf-8-sig') == sample
    assert b'stdout-handle' in log.read_bytes() and b'stderr-handle' in log.read_bytes()


def test_failed_atomic_job_assignment_never_executes_the_child(tmp_path, tree, monkeypatch):
    original = build_process._BuildJob._kernel
    def rejected_assignment():
        kernel = original()
        update = kernel.UpdateProcThreadAttribute
        def reject(attributes, flags, key, *args):
            if key == 0x0002000D:
                ctypes.set_last_error(5)
                return 0
            return update(attributes, flags, key, *args)
        kernel.UpdateProcThreadAttribute = reject
        return kernel
    monkeypatch.setattr(build_process._BuildJob, '_kernel', staticmethod(rejected_assignment))
    args = arguments(tmp_path, tree)
    with (tmp_path / 'build.log').open('ab') as output:
        with pytest.raises(OSError):
            build_process.run_build(stdout=output, **args)
    assert read_json(args['receipt'])['treeEmpty']
    assert not list(tree[1].iterdir())


def test_resume_failure_kills_the_exact_suspended_build_process(tmp_path, tree, monkeypatch):
    args = arguments(tmp_path, tree)
    def fail_resume(process):
        raise RuntimeError('controlled resume failure')
    monkeypatch.setattr(build_process._BuildProcess, 'resume', fail_resume)
    with (tmp_path / 'build.log').open('ab') as output:
        with pytest.raises(RuntimeError, match='resume'):
            build_process.run_build(stdout=output, **args)
    receipt = read_json(args['receipt'])
    assert receipt['treeEmpty'] and processes.owned(receipt['process']) is None
    assert not list(tree[1].iterdir())


def test_owner_crash_kills_all_build_descendants_and_keeps_incomplete_receipt(tmp_path, tree):
    script, folder = tree
    receipt = tmp_path / 'builds/aaaaaaaa.json'
    driver = tmp_path / 'owner.py'
    root = Path(__file__).resolve().parents[2]
    driver.write_text('''import os,sys,pathlib
sys.path.insert(0,sys.argv[1])
from deploy.bluegreen.build_process import run_build
with open(sys.argv[5],'ab') as stream:
    run_build([sys.executable,sys.argv[2],sys.argv[3],'0','60'],stdout=stream,env=dict(os.environ),timeout=60,
              receipt=pathlib.Path(sys.argv[4]),release=pathlib.Path(sys.argv[6]),sha_full='a'*40)
''', encoding='utf-8')
    with subprocess.Popen([sys.executable, str(driver), str(root), str(script), str(folder), str(receipt),
                           str(tmp_path / 'owner.log'), str(tmp_path / 'releases/aaaaaaaa')],
                          creationflags=subprocess.CREATE_NO_WINDOW) as owner:
        try:
            wait_for(lambda: len(list(folder.glob('[012].json'))) == 3)
            assert read_json(receipt)['phase'] == 'building'
            owner.kill()  # Exact synthetic owner handle; the OS job owns only its build tree.
            owner.wait(timeout=10)
            assert_tree_gone(folder)
            assert not (folder / 'built.json').exists()
            assert read_json(receipt)['success'] is False
            build_process.verify_previous(receipt)
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=10)


@pytest.mark.parametrize('owner_state', ['alive', 'unknown'])
def test_rebuild_refuses_a_live_or_uninspectable_previous_owner(tmp_path, monkeypatch, owner_state):
    receipt = tmp_path / 'builds/aaaaaaaa.json'
    atomic_json(receipt, {'phase': 'building', 'treeEmpty': False, 'owner': processes.identity(os.getpid()),
                          'jobName': 'Local\\PPLID-Build-' + 'a' * 32})
    if owner_state == 'unknown':
        def denied(*args):
            raise build_process.psutil.AccessDenied()
        monkeypatch.setattr(build_process.psutil, 'Process', denied)
    with pytest.raises(RuntimeError, match='BUILD_RECOVERY_REQUIRED'):
        build_process.verify_previous(receipt)


@pytest.mark.parametrize('kind', ['missing', 'building', 'failed', 'other-sha', 'other-release'])
def test_release_contract_refuses_missing_or_incomplete_build_receipts(laboratory, make_release, kind):
    config, manager, _ = laboratory
    sha = make_release(config, 'a')
    path = config.directory / 'builds' / (sha + '.json')
    record = read_json(path)
    if kind == 'missing':
        path.unlink()
    else:
        if kind in ('building', 'failed'):
            record.update(phase=kind, success=False)
        elif kind == 'other-sha':
            record['shaFull'] = 'b' * 40
        else:
            record['release'] = str(config.release('b' * 8))
        atomic_json(path, record)
    with pytest.raises(RuntimeError, match='receipt'):
        manager.contract(config.release(sha), sha)
