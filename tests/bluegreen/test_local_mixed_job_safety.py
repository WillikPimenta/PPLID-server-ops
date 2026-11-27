"""Business job guards refuse before HTTP mutation or opening a DB cursor."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deploy.bluegreen import local_mixed_job


def inputs():
    lab = Path(local_mixed_job.__file__).resolve().parents[2] / '.local/bluegreen-readiness/build-safety-test'
    manager = SimpleNamespace(config=SimpleNamespace(base=lab, frontend_port=12345), read=Mock(return_value={'proxy': {}}))
    database = Mock()
    return dict(lab=lab, frontend='http://127.0.0.1:12345', manager=manager, database=database,
                cookie_name='synthetic', admin_cookie='synthetic')


def test_mixed_job_rejects_external_listener_before_any_database_access():
    args = inputs()
    args['frontend'] = 'http://example.com:12345'
    with pytest.raises(ValueError, match='loopback'):
        local_mixed_job.run(**args)
    args['database'].cursor.assert_not_called()
    args['manager'].read.assert_not_called()


def test_mixed_job_rejects_nonlaboratory_path_before_any_database_access(tmp_path):
    args = inputs()
    args['lab'] = tmp_path
    with pytest.raises(ValueError, match='readiness lab'):
        local_mixed_job.run(**args)
    args['database'].cursor.assert_not_called()


def test_mixed_job_rejects_configuration_for_another_listener():
    args = inputs()
    args['manager'].config.frontend_port = 12346
    with pytest.raises(ValueError, match='configuration'):
        local_mixed_job.run(**args)
    args['database'].cursor.assert_not_called()


def test_mixed_job_rejects_unowned_proxy_before_any_database_access(monkeypatch):
    args = inputs()
    monkeypatch.setattr(local_mixed_job.processes, 'owned', lambda _: None)
    with pytest.raises(ValueError, match='not owned'):
        local_mixed_job.run(**args)
    args['database'].cursor.assert_not_called()
