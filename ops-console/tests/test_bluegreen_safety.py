from pathlib import Path
import json

import pytest
import server_ops as so


def config(tmp_path):
    return {'logDir': str(tmp_path / 'logs'), 'MAIN': {'enabled': True, 'deployStrategy': 'blueGreen',
                                            'backendPort': 8000, 'frontendPort': 5173}}


def test_port_and_toggle_actions_never_use_legacy_kill(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Legacy subprocess action must not run')
    monkeypatch.setattr(so, 'run_powershell', forbidden)
    monkeypatch.setattr(so, '_run_hidden', forbidden)
    with pytest.raises(ValueError, match='blue-green'):
        so.update_infra_ports(cfg, 'MAIN', {'backendPort': 9000})
    assert not so.apply_infra_ports(cfg, 'MAIN')['ok']
    assert not so.action_disable_environment(cfg, 'MAIN')['ok']
    assert not so.action_enable_environment(cfg, 'MAIN')['ok']


def test_existing_journal_blocks_legacy_even_if_flag_changes(tmp_path):
    cfg = config(tmp_path)
    cfg['MAIN'].pop('deployStrategy')
    path = tmp_path / 'deploy/MAIN/bluegreen/state.json'
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    assert so.uses_bluegreen(cfg, 'MAIN')
    assert so.bluegreen_status(cfg, 'MAIN')['phase'] == 'unavailable'
