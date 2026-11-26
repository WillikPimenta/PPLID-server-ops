from __future__ import annotations

from pathlib import Path
import time

import pytest

from deploy.bluegreen import processes
from deploy.bluegreen.caddy import request
from deploy.bluegreen.store import atomic_json, digest


def test_reconcile_interrupted_switch_and_restart_proxy(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, '1')
    second = make_release(config, '2')
    manager.prepare(first)
    manager.activate()
    manager.promote(second)
    manager.rollback()
    state = manager.read()
    before = {'allowed': [state['slots']['A']['instanceId']]}
    after = {'allowed': [state['slots']['B']['instanceId']]}
    # Simulate loss of the process identity write immediately after launch.
    state['slots']['B'].pop('process')
    state['intent'] = {'before': 'A', 'after': 'B', 'beforeGate': before, 'afterGate': after}
    atomic_json(config.directory / 'traffic.json', {'allowed': before['allowed'] + after['allowed']})
    manager.save(state, 'switching')
    # Simultaneous proxy death and an interrupted route transaction must also
    # recover without editing JSON or stopping healthy backend processes.
    processes.stop(state['proxy'], None, .05, force=True)
    recovered = manager.recover()
    assert recovered['active'] == 'B'
    assert recovered['previous'] == 'A'
    assert recovered['slots']['B'].get('process')
    assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == second
    processes.stop(recovered['proxy'], None, .05, force=True)
    manager.recover()
    assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == second


def test_cancel_before_and_after_switch(laboratory, make_release, monkeypatch):
    config, manager, _ = laboratory
    first = make_release(config, '3')
    second = make_release(config, '4')
    manager.prepare(first)
    manager.activate()
    marker = config.root / 'cancel-requested.json'
    atomic_json(marker, {'requested': True})
    with pytest.raises(RuntimeError, match='DEPLOY_CANCELLED'):
        manager.promote(second)
    assert manager.read()['active'] == 'A'
    manager.recover()
    assert not marker.exists()
    def cancel_during_observation(state, slot):
        atomic_json(marker, {'requested': True})
        manager.cancelled()
    monkeypatch.setattr(manager, 'observe', cancel_during_observation)
    with pytest.raises(RuntimeError, match='DEPLOY_CANCELLED'):
        manager.promote(second)
    assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == first
    assert manager.read()['active'] == 'A'


def test_pending_migrations_block_candidate(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, '5')
    second = make_release(config, '6')
    settings = config.release(second) / 'backend/config/settings.py'
    settings.write_text(settings.read_text() + "\nINSTALLED_APPS=['django.contrib.contenttypes']\n")
    manager.prepare(first)
    manager.activate()
    with pytest.raises(RuntimeError):
        manager.promote(second)
    assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == first
    # Recovery must preserve the healthy active release despite a broken,
    # non-active candidate registered by the interrupted deployment.
    assert manager.recover()['active'] == 'A'


def test_third_release_retires_only_inactive_slot_and_keeps_assets(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, '7')
    second = make_release(config, '8')
    third = make_release(config, '9')
    manager.prepare(first)
    manager.activate()
    original = manager.read()['slots']['A']['process']
    manager.promote(second)
    manager.promote(third)
    assert not processes.owned(original)
    assert manager.read()['active'] == 'A'
    from deploy.bluegreen.caddy import OPENER
    with OPENER.open(f'http://127.0.0.1:{config.frontend_port}/assets/{first}.js') as response:
        assert first.encode() in response.read()
    stopped = manager.shutdown()
    assert stopped['phase'] == 'stopped' and not stopped['slots']
    processes.assert_free(config.backend_port)
    processes.assert_free(config.frontend_port)
