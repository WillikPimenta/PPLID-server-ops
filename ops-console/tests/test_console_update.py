"""Testes de auto-atualizacao do ops-console."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import server_ops as so


def test_resolve_ops_repo_dir_prefers_installed_ops(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    ops = base / "ops"
    (ops / "lib").mkdir(parents=True)
    (ops / "lib" / "paths.ps1").write_text("# stub", encoding="utf-8")

    config = {"logDir": str(logs)}
    assert so.resolve_ops_repo_dir(config) == ops


def test_resolve_ops_repo_dir_falls_back_to_checkout(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {"logDir": str(logs)}
    resolved = so.resolve_ops_repo_dir(config)
    assert (resolved / "lib" / "paths.ps1").is_file()


def test_parse_powershell_json_last_line():
    stdout = "log line\n{\"ok\":true,\"currentSha\":\"abc1234\"}\n"
    payload = so._parse_powershell_json(stdout)
    assert payload["ok"] is True
    assert payload["currentSha"] == "abc1234"


def test_parse_powershell_json_invalid():
    payload = so._parse_powershell_json("not json")
    assert payload["ok"] is False


def test_console_update_lock_expires(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {"logDir": str(logs)}
    lock_path = so._console_update_lock_path(config)
    lock_path.write_text(
        json.dumps({"startedAt": time.time() - 200}),
        encoding="utf-8",
    )
    assert so._read_console_update_lock(config) is None


def test_console_update_lock_active(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {"logDir": str(logs)}
    so._write_console_update_lock(config, detail="test")
    lock = so._read_console_update_lock(config)
    assert lock is not None
    assert lock.get("active") is True


def test_check_console_update_missing_script(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    ops = base / "ops"
    logs.mkdir(parents=True)
    ops.mkdir(parents=True)
    config = {"logDir": str(logs)}
    with patch.object(so, "resolve_ops_repo_dir", return_value=ops):
        result = so.check_console_update(config)
    assert result["ok"] is False
    assert result["supported"] is False


def test_apply_console_update_already_up_to_date(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {"logDir": str(logs)}
    status = {
        "ok": True,
        "supported": True,
        "updateAvailable": False,
        "currentSha": "abc1234",
        "remoteSha": "abc1234",
    }
    with patch.object(so, "check_console_update", return_value=status):
        result = so.apply_console_update(config)
    assert result["ok"] is True
    assert result["restarting"] is False


def test_read_local_console_git_info_from_checkout():
    config = {"logDir": "C:/PPLID/logs"}
    info = so.read_local_console_git_info(config)
    if info.get("supported"):
        assert info.get("currentSha")
        assert info.get("branch")


def test_check_console_update_merges_local_info(tmp_path: Path, monkeypatch):
    base = tmp_path / "pplid"
    logs = base / "logs"
    ops = base / "ops"
    logs.mkdir(parents=True)
    ops.mkdir(parents=True)
    (ops / "update_ops_console.ps1").write_text("# stub", encoding="utf-8")
    config = {"logDir": str(logs)}

    def fake_local(_config):
        return {
            "ok": True,
            "supported": True,
            "branch": "main",
            "currentSha": "abc1234",
            "currentShaFull": "abc1234" * 5,
        }

    def fake_powershell(script, args, timeout=120):
        return {
            "ok": True,
            "stdout": '{"ok":false,"supported":true,"reason":"fetch falhou","branch":"main","currentSha":"abc1234"}',
            "exitCode": 1,
        }

    monkeypatch.setattr(so, "read_local_console_git_info", fake_local)
    monkeypatch.setattr(so, "run_powershell", fake_powershell)
    result = so.check_console_update(config)
    assert result["currentSha"] == "abc1234"
    assert result.get("checkedAt")


def test_apply_console_update_spawns_worker(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    ops = base / "ops"
    logs.mkdir(parents=True)
    ops.mkdir(parents=True)
    (ops / "update_ops_console.ps1").write_text("# stub", encoding="utf-8")
    config = {"logDir": str(logs), "_configPath": str(base / "config.json")}
    status = {
        "ok": True,
        "supported": True,
        "updateAvailable": True,
        "currentSha": "abc1234",
        "remoteSha": "def5678",
        "branch": "main",
    }
    with patch.object(so, "check_console_update", return_value=status):
        with patch.object(so, "_spawn_detached_powershell") as spawn:
            result = so.apply_console_update(config)
    assert result["ok"] is True
    assert result["restarting"] is True
    spawn.assert_called_once()
    saved = so._read_console_update_result(config)
    assert saved is not None
    assert saved.get("phase") == "started"
    assert saved.get("targetSha") == "def5678"


def test_console_update_result_roundtrip(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {"logDir": str(logs)}
    so._write_console_update_result(
        config,
        {
            "ok": False,
            "phase": "failed",
            "error": "git pull --ff-only falhou: divergent branches",
            "previousSha": "abc1234",
            "targetSha": "def5678",
        },
    )
    saved = so._read_console_update_result(config)
    assert saved["ok"] is False
    assert "divergent" in saved["error"]
    assert saved.get("finishedAt")


def test_check_console_update_includes_last_result_when_locked(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {"logDir": str(logs)}
    so._write_console_update_lock(config, detail="abc->def")
    so._write_console_update_result(
        config,
        {"ok": True, "phase": "started", "targetSha": "def5678"},
    )
    with patch.object(
        so,
        "read_local_console_git_info",
        return_value={"ok": True, "supported": True, "currentSha": "abc1234", "branch": "main"},
    ):
        result = so.check_console_update(config)
    assert result["inProgress"] is True
    assert result["lastResult"]["phase"] == "started"
    assert result["lockDetail"] == "abc->def"
