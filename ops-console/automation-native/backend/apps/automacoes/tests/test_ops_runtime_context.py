import json
from pathlib import Path
from unittest.mock import patch

from apps.automacoes.views import _merge_ops_runtime_context


def test_ops_context_is_merged_only_for_matching_runner_pid(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "production.json").write_text(
        json.dumps(
            {
                "runnerPid": 123,
                "supervisorPid": 99,
                "targetEnvironment": "DEV",
                "bundle": {"id": "main-abc123"},
            }
        ),
        encoding="utf-8",
    )
    robots = {
        "production": {"running": True, "pid": 123},
        "rotina": {"running": True, "pid": 456},
    }
    with patch.dict("os.environ", {"OPS_AUTOMATION_RUNTIME_ROOT": str(tmp_path)}):
        merged = _merge_ops_runtime_context(robots)
    assert merged["production"]["controller"] == "ops"
    assert merged["production"]["target_environment"] == "DEV"
    assert merged["production"]["runtime_version"] == "main-abc123"
    assert merged["rotina"]["controller"] == "portal"
