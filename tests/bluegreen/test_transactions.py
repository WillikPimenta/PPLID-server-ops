"""Regression coverage for failures after a routing transaction is persisted."""
from __future__ import annotations

import pytest

from deploy.bluegreen.manager import Manager


@pytest.fixture
def transaction(monkeypatch):
    manager = object.__new__(Manager)
    state = {'phase': 'idle', 'active': 'A', 'previous': None, 'candidate': None,
             'intent': None, 'history': [], 'slots': {'A': {'sha': 'a'}}}
    calls = []
    monkeypatch.setattr(manager, 'read', lambda **kw: state)
    monkeypatch.setattr(manager, 'healthy', lambda *a, **kw: None)
    def save(value, phase=None):
        if phase:
            value['phase'] = phase
    monkeypatch.setattr(manager, 'save', save)
    monkeypatch.setattr(manager, 'cancelled', lambda: None)
    monkeypatch.setattr(manager, 'start_slot', lambda s, name, sha: s['slots'].setdefault(name, {'sha': sha}))
    monkeypatch.setattr(manager, 'wait_ready', lambda slot: None)
    monkeypatch.setattr(manager, 'sync_legacy', lambda s: None)
    def reload(value, target):
        calls.append(target)
        old = value['active']
        value['active'] = target
        if old != target:
            value['previous'] = old
        value['intent'] = None
        if target == 'B':
            raise RuntimeError('public readiness failed after persisted switch')
    monkeypatch.setattr(manager, 'reload', reload)
    return manager, state, calls


def test_failed_public_check_after_committed_switch_rolls_back(transaction):
    manager, state, calls = transaction
    with pytest.raises(RuntimeError, match='public readiness failed'):
        manager.promote('b')
    assert calls == ['A', 'B', 'A']
    assert state['active'] == 'A'
    assert state['previous'] == 'B'
    assert state['phase'] == 'failed'
    assert state['lastGoodSha'] == 'a'
    assert 'public readiness failed' in state['lastError']


def test_failed_rollback_preserves_both_errors_and_requires_recovery(transaction, monkeypatch):
    manager, state, calls = transaction
    original = manager.reload
    def reload(value, target):
        if target == 'A' and value['active'] == 'B':
            raise RuntimeError('rollback unavailable')
        original(value, target)
    monkeypatch.setattr(manager, 'reload', reload)
    with pytest.raises(RuntimeError, match='public readiness failed'):
        manager.promote('b')
    assert state['phase'] == 'recovery-required'
    assert 'public readiness failed' in state['lastError']
    assert 'rollback unavailable' in state['recoveryError']
    assert set(state['slots']) == {'A', 'B'}


@pytest.mark.parametrize('failure', ['public', 'junction'])
def test_real_proxy_failure_after_switch_restores_source(laboratory, make_release, monkeypatch, failure):
    from deploy.bluegreen.caddy import request
    config, manager, _ = laboratory
    first = make_release(config, 'a')
    second = make_release(config, 'b')
    manager.prepare(first)
    manager.activate()
    if failure == 'public':
        healthy = manager.healthy
        def fail_public(slot, *, public=False):
            if public and slot['sha'] == second:
                raise RuntimeError('injected public frontend identity failure')
            return healthy(slot, public=public)
        monkeypatch.setattr(manager, 'healthy', fail_public)
    else:
        sync = manager.sync_legacy
        def fail_junction(state):
            if state['slots'][state['active']]['sha'] == second:
                raise RuntimeError('injected junction reconciliation failure')
            return sync(state)
        monkeypatch.setattr(manager, 'sync_legacy', fail_junction)
    with pytest.raises(RuntimeError, match='injected'):
        manager.promote(second)
    state = manager.read()
    assert state['phase'] == 'failed'
    assert state['active'] == 'A' and state['previous'] == 'B'
    assert state['lastGoodSha'] == first
    if failure == 'junction':
        assert state['history'][-1]['result'] == 'rolled-back'
    manager.healthy(state['slots']['A'], public=True)
    assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == first


def test_recover_after_retirement_has_no_dangling_previous(laboratory, make_release, monkeypatch):
    config, manager, _ = laboratory
    first = make_release(config, 'c')
    second = make_release(config, 'd')
    manager.prepare(first)
    manager.activate()
    manager.promote(second)
    reload = manager.reload
    monkeypatch.setattr(manager, 'reload', lambda *a: (_ for _ in ()).throw(RuntimeError('interrupted retirement')))
    with pytest.raises(RuntimeError, match='interrupted retirement'):
        manager.retire(manager.read(), 'A')
    assert manager.read()['previous'] is None
    monkeypatch.setattr(manager, 'reload', reload)
    state = manager.recover()
    assert state['active'] == 'B' and state['phase'] == 'idle'
