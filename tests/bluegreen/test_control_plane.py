from types import SimpleNamespace
import urllib.error

import pytest

from deploy.bluegreen.caddy import Caddy
from deploy.bluegreen.store import digest


def test_lost_load_acknowledgement_is_verified_without_repeating_mutation(monkeypatch):
    proxy = Caddy(SimpleNamespace(admin_port=12345))
    desired = {'admin': 'synthetic'}
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        if url.endswith('/load'):
            raise ConnectionResetError('acknowledgement lost after accepted load')
        return desired
    monkeypatch.setattr('deploy.bluegreen.caddy.request', request)
    assert proxy.load(desired) == digest(desired)
    assert sum(url.endswith('/load') for url in calls) == 1


def test_rejected_load_is_not_treated_as_success(monkeypatch):
    proxy = Caddy(SimpleNamespace(admin_port=12345))
    def request(url, **kwargs):
        raise urllib.error.HTTPError(url, 400, 'invalid config', {}, None)
    monkeypatch.setattr('deploy.bluegreen.caddy.request', request)
    with pytest.raises(urllib.error.HTTPError):
        proxy.load({'invalid': True})


def test_transient_config_read_after_load_is_reconciled(monkeypatch):
    proxy = Caddy(SimpleNamespace(admin_port=12345))
    desired = {'admin': 'synthetic'}
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        if url.endswith('/load'):
            return None
        if sum(item.endswith('/config/') for item in calls) == 1:
            raise ConnectionResetError('old admin connection closed during verification')
        return desired
    monkeypatch.setattr('deploy.bluegreen.caddy.request', request)
    assert proxy.load(desired) == digest(desired)
    assert sum(url.endswith('/load') for url in calls) == 1
