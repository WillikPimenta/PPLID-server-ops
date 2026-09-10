"""Testes de resolução de templates tray_ui e parsing da seção sync."""
from __future__ import annotations

import json
from pathlib import Path

from app.bots.tray_ui_runner import TrayUiBot, TrayUiBotConfig
from app.services import tray_templates as templates


def test_seed_templates_copies_missing_files(tmp_path: Path):
    repo = tmp_path / "repo" / "onedrive"
    local = tmp_path / "local" / "onedrive"
    repo.mkdir(parents=True)
    local.mkdir(parents=True)
    (repo / "flow.json").write_text('{"confidence": 0.85}', encoding="utf-8")
    (repo / "connected.png").write_bytes(b"png")

    result = templates.seed_templates_from_repo("onedrive", local_dir=local, repo_dir=repo)
    assert result == local
    assert (local / "flow.json").read_text(encoding="utf-8") == '{"confidence": 0.85}'
    assert (local / "connected.png").read_bytes() == b"png"


def test_seed_templates_does_not_overwrite_existing(tmp_path: Path):
    repo = tmp_path / "repo" / "onedrive"
    local = tmp_path / "local" / "onedrive"
    repo.mkdir(parents=True)
    local.mkdir(parents=True)
    (repo / "connected.png").write_bytes(b"repo")
    (local / "connected.png").write_bytes(b"local")

    templates.seed_templates_from_repo("onedrive", local_dir=local, repo_dir=repo)
    assert (local / "connected.png").read_bytes() == b"local"


def test_resolve_templates_context_uses_local_flow_only(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo" / "cisco"
    local = tmp_path / "local" / "cisco"
    repo.mkdir(parents=True)
    local.mkdir(parents=True)
    (repo / "flow.json").write_text('{"confidence": 0.5}', encoding="utf-8")
    (local / "flow.json").write_text('{"confidence": 0.9}', encoding="utf-8")

    monkeypatch.setattr(templates, "get_repo_templates_dir", lambda service: repo)
    monkeypatch.setattr(templates, "get_local_templates_dir", lambda service: local)
    monkeypatch.setattr(templates, "seed_templates_from_repo", lambda *args, **kwargs: local)

    ctx = templates.resolve_templates_context("cisco", seed=False)
    assert ctx.flow_path == local / "flow.json"
    assert json.loads(ctx.flow_path.read_text(encoding="utf-8"))["confidence"] == 0.9


def test_resolve_file_local_only_no_repo_fallback(tmp_path: Path):
    repo = tmp_path / "repo"
    local = tmp_path / "local"
    repo.mkdir()
    local.mkdir()
    (repo / "btn.png").write_bytes(b"repo")
    (local / "local_only.png").write_bytes(b"local")

    ctx = templates.TemplatesContext(service="onedrive", local_dir=local, repo_dir=repo)
    assert ctx.resolve_file("local_only.png").read_bytes() == b"local"
    assert ctx.resolve_file("btn.png") is None
    assert ctx.resolve_file("missing.png") is None


def test_flow_path_requires_local_even_if_repo_has_flow(tmp_path: Path):
    repo = tmp_path / "repo"
    local = tmp_path / "local"
    repo.mkdir()
    local.mkdir()
    (repo / "flow.json").write_text("{}", encoding="utf-8")
    ctx = templates.TemplatesContext(service="cisco", local_dir=local, repo_dir=repo)
    assert ctx.flow_path == local / "flow.json"
    assert not ctx.flow_path.is_file()


def _make_bot(service: str = "onedrive") -> TrayUiBot:
    config = TrayUiBotConfig(
        service_name="OneDrive" if service == "onedrive" else "Cisco",
        templates_subdir=service,
        check_interval_env="ONEDRIVE_UI_CHECK_INTERVAL_SECONDS",
        post_recover_wait_env="ONEDRIVE_UI_POST_RECOVER_WAIT_SECONDS",
        sync_stuck_watch_enabled=True,
    )
    return TrayUiBot(config)


def test_sync_config_new_format():
    bot = _make_bot()
    flow = {
        "sync": {
            "enabled": True,
            "watch": {"poll_seconds": 10, "window_seconds": 60},
            "recovery": {"action": "none", "cooldown_seconds": 120, "wait_after_seconds": 5},
        }
    }
    assert bot._sync_enabled(flow) is True
    assert bot._sync_stuck_poll_seconds(flow) == 10
    assert bot._sync_stuck_window_seconds(flow) == 60
    assert bot._sync_recovery_action(flow) == "none"
    assert bot._sync_restart_cooldown_seconds(flow) == 120
    assert bot._sync_restart_wait_seconds(flow) == 5


def test_sync_config_legacy_sync_stuck_watch():
    bot = _make_bot()
    flow = {"sync_stuck_watch": {"poll_seconds": 7, "window_seconds": 90}}
    assert bot._sync_enabled(flow) is True
    assert bot._sync_stuck_poll_seconds(flow) == 7
    assert bot._sync_stuck_window_seconds(flow) == 90
    assert bot._sync_recovery_action(flow) == "restart_process"


def test_sync_disabled_in_flow_overrides_config():
    bot = _make_bot()
    flow = {"sync": {"enabled": False, "watch": {"poll_seconds": 5, "window_seconds": 180}}}
    assert bot._sync_enabled(flow) is False
