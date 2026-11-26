"""Resource exhaustion must stop a local build before allocating its services."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.bluegreen import readiness_build


@pytest.mark.parametrize('shortage', ['disk', 'commit'])
def test_build_refuses_resource_shortage_before_any_subprocess(tmp_path, monkeypatch, shortage):
    monkeypatch.setattr(readiness_build.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=(1024 if shortage == 'disk' else 8192) * 2**20))
    monkeypatch.setattr(readiness_build, 'available_commit_bytes',
                        lambda: (1024 if shortage == 'commit' else 8192) * 2**20)
    monkeypatch.setattr(readiness_build.psutil, 'virtual_memory',
                        lambda: SimpleNamespace(available=8192 * 2**20))
    monkeypatch.setattr(readiness_build.subprocess, 'check_output',
                        lambda *a, **k: pytest.fail('No process may start after resource refusal'))
    monkeypatch.setattr(readiness_build.subprocess, 'run',
                        lambda *a, **k: pytest.fail('No database or package process may start'))
    result = readiness_build.run(tmp_path / 'source', tmp_path / 'postgres', tmp_path)
    assert result['ok'] is False
    assert 'Insufficient laboratory build resources' in result['error']
    assert result['checks'] == []
    assert len(result['resourcePreflights']) == 1
    assert not (Path(result['laboratory']) / 'pgdata').exists()
