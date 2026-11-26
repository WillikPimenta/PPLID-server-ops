from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from deploy.bluegreen import worker_protocol as drain


@pytest.fixture
def control(tmp_path, monkeypatch):
    root = tmp_path / 'control'
    monkeypatch.setenv('PPLID_WORKER_CONTROL_DIR', str(root))
    controller = drain.Controller(root)
    controller.initialize('first')
    controller.open('first')
    return controller


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for controlled work to finish')
        time.sleep(.05)


def test_admission_closes_before_drain_and_generation_restart(control):
    lease = control.reserve('long-job')
    control.request('first')
    assert control.requested()
    with pytest.raises(drain.AdmissionClosed):
        control.reserve('new-job')
    with pytest.raises(RuntimeError, match='still running'):
        control.confirm_stopped('first')
    assert control.status()['units'][0]['ownerAlive']
    lease.close()
    control.confirm_stopped('first')
    # Retrying after a failed receipt write must not strand a drained owner.
    control.confirm_stopped('first')
    control.initialize('second')
    control.open('second')
    assert not control.requested()


def test_long_unit_finishes_without_setting_cancellation_event(control):
    entered, finish, cancellation = threading.Event(), threading.Event(), threading.Event()
    completed = []
    @drain.managed
    def work():
        while not drain.requested():
            with drain.cycle('batch') as admitted:
                if not admitted:
                    break
                entered.set()
                assert finish.wait(10)
                assert not cancellation.is_set()
                completed.append('committed')
    thread = threading.Thread(target=work)
    thread.start()
    assert entered.wait(5)
    control.request('first')
    assert thread.is_alive() and control.status()['units']
    assert drain.wait(60, event=cancellation)
    assert not cancellation.is_set()
    finish.set()
    thread.join(10)
    assert completed == ['committed'] and not thread.is_alive()
    assert control.status()['units'] == []


def test_managed_child_survives_drain_and_timeout(control, tmp_path):
    done = tmp_path / 'finish'
    started = tmp_path / 'started'
    code = "from pathlib import Path; import sys,time; Path(sys.argv[2]).touch();\nwhile not Path(sys.argv[1]).exists(): time.sleep(.05)"
    with pytest.raises(subprocess.TimeoutExpired):
        drain.run_process([sys.executable, '-c', code, str(done), str(started)], timeout=.2,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        until(started.exists)
        control.request('first')
        assert len(control.status()['units']) == 1
        with pytest.raises(RuntimeError, match='still running'):
            control.confirm_stopped('first')
    finally:
        done.touch()
        until(lambda: not control.status()['units'])
    control.confirm_stopped('first')


def test_dead_owner_lease_is_preserved_for_explicit_recovery(control):
    code = ("from deploy.bluegreen.worker_protocol import current; import os; "
            "current().reserve('orphan-intent'); os._exit(0)")
    result = subprocess.run([sys.executable, '-c', code], timeout=15, capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    control.request('first')
    assert control.status()['units'][0]['ownerAlive'] is False
    with pytest.raises(RuntimeError, match='still running'):
        control.confirm_stopped('first')
    with pytest.raises(RuntimeError, match='Unfinished'):
        control.initialize('second')


def test_corruption_and_wrong_generation_fail_closed(control):
    with pytest.raises(RuntimeError, match='generation mismatch'):
        control.request('other')
    control.state.write_text('{broken')
    assert control.requested()
    with pytest.raises(ValueError):
        control.reserve('job')


def test_legacy_mode_has_no_admission_side_effects(tmp_path, monkeypatch):
    monkeypatch.delenv('PPLID_WORKER_CONTROL_DIR', raising=False)
    calls = []
    @drain.managed
    def job():
        calls.append('done')
    job()
    assert not drain.requested()
    assert calls == ['done']


def test_supervisor_death_closes_admission(control, monkeypatch):
    value = control.read()
    value['owner']['created'] -= 100
    control.state.write_text(json.dumps(value))
    assert control.requested()
    with pytest.raises(drain.AdmissionClosed):
        control.reserve('new-work')


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object ownership')
def test_exited_parent_does_not_hide_living_grandchild(control, tmp_path):
    done = tmp_path / 'finish-child'
    started = tmp_path / 'child-started'
    child = "from pathlib import Path; import sys,time; Path(sys.argv[2]).touch();\nwhile not Path(sys.argv[1]).exists(): time.sleep(.05)"
    parent = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2],sys.argv[3]])'
    process = drain.popen([sys.executable, '-c', parent, child, str(done), str(started)],
                          creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        process.wait(timeout=10)
        until(started.exists)
        control.request('first')
        assert control.status()['units']
        with pytest.raises(RuntimeError, match='still running'):
            control.confirm_stopped('first')
    finally:
        done.touch()
        until(lambda: not control.status()['units'])
    control.confirm_stopped('first')


def test_retiring_web_group_preserves_independent_supervisor(control):
    old_web = drain.Controller(control.root, 'old-web')
    new_web = drain.Controller(control.root, 'new-web')
    worker = drain.Controller(control.root, 'independent')
    lease = old_web.reserve('current-job')
    control.retire('old-web')
    assert old_web.requested()
    assert not new_web.requested() and not worker.requested()
    with pytest.raises(drain.AdmissionClosed):
        old_web.reserve('next-job')
    assert old_web.status()['units'][0]['group'] == 'old-web'
    with new_web.reserve('allowed-new-request'), worker.reserve('allowed-scheduler-tick'):
        assert len(control.status()['units']) == 3
    lease.close()


def test_single_service_owner_across_web_groups(control):
    first = drain.Controller(control.root, 'web-A')
    second = drain.Controller(control.root, 'web-B')
    lease = first.reserve('controle-sla-service', unique=True)
    with pytest.raises(drain.AdmissionClosed):
        second.reserve('controle-sla-service', unique=True)
    lease.close()
    second.reserve('controle-sla-service', unique=True).close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows accounting without kill-on-close')
def test_owner_crash_keeps_child_running_and_preserves_uncertain_lease(control, tmp_path):
    finish, started = tmp_path / 'finish', tmp_path / 'started'
    child = 'from pathlib import Path;import sys,time;Path(sys.argv[1]).touch();\nwhile not Path(sys.argv[2]).exists():time.sleep(.05)'
    owner = ('from deploy.bluegreen import worker_protocol as d;import os,subprocess,sys;'
             'd.popen([sys.executable,"-c",sys.argv[1],sys.argv[2],sys.argv[3]],creationflags=subprocess.CREATE_NO_WINDOW);'
             'os._exit(0)')
    try:
        result = subprocess.run([sys.executable, '-c', owner, child, str(started), str(finish)], timeout=15,
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        until(started.exists)
        control.request('first')
        assert control.status()['units'][0]['ownerAlive'] is False
        with pytest.raises(RuntimeError, match='still running'):
            control.confirm_stopped('first')
    finally:
        finish.touch()


@pytest.mark.skipif(os.name != 'nt', reason='Windows suspended child admission')
def test_admitted_child_is_not_lost_when_drain_precedes_interpreter_start(control, tmp_path):
    completed = tmp_path / 'completed'
    code = ('from deploy.bluegreen.worker_protocol import managed;from pathlib import Path;import sys;'
            '\n@managed\ndef job(): Path(sys.argv[1]).touch()\njob()')
    def create_then_close_admission(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        control.request('first')
        return process
    process = drain.popen([sys.executable, '-c', code, str(completed)], _factory=create_then_close_admission,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    _, errors = process.communicate(timeout=15)
    assert process.returncode == 0, errors
    assert completed.exists()
    until(lambda: not control.status()['units'])
    control.confirm_stopped('first')
