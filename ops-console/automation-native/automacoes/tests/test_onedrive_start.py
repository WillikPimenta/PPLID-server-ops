"""Testes de início automático do OneDrive (tray_ui)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import app.bots.bot_tray_ui as bot_tray_ui
from app.infrastructure import onedrive_process as od_proc

_ONEDRIVE_BOT = bot_tray_ui._onedrive_bot
assert _ONEDRIVE_BOT is not None

_FLOW = {
    "startup": {"start_if_not_running": True, "wait_after_start_seconds": 1},
    "sync": {"enabled": False},
}


def test_start_onedrive_background_skips_when_running(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(od_proc.os, "name", "nt")
    exe = tmp_path / "OneDrive.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(od_proc, "find_onedrive_exe", lambda: exe)
    monkeypatch.setattr(od_proc, "is_onedrive_running", lambda: True)
    with patch.object(od_proc.subprocess, "Popen") as popen_mock:
        assert od_proc.start_onedrive_background() is True
    popen_mock.assert_not_called()


def test_start_onedrive_background_launches_when_stopped(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(od_proc.os, "name", "nt")
    exe = tmp_path / "OneDrive.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(od_proc, "find_onedrive_exe", lambda: exe)
    monkeypatch.setattr(od_proc, "is_onedrive_running", lambda: False)
    with patch.object(od_proc.subprocess, "Popen") as popen_mock:
        assert od_proc.start_onedrive_background() is True
    popen_mock.assert_called_once()


def test_run_cycle_starts_onedrive_when_process_missing():
    bot = _ONEDRIVE_BOT
    bot._onedrive_start_cooldown_until = 0.0

    with patch("app.bots.tray_ui_runner.is_onedrive_running", side_effect=[False, False, True]):
        with patch("app.bots.tray_ui_runner.start_onedrive_background", return_value=True):
            with patch("app.bots.tray_ui_runner.find_onedrive_exe", return_value=Path("C:/OneDrive.exe")):
                with patch.object(bot.parar_event, "wait", return_value=False):
                    with patch.object(bot, "_emit_tray_diagnostics"):
                        with patch.object(bot, "resolve_tray_state", side_effect=["unknown", "connected"]):
                            assert bot.run_cycle(_FLOW, 0.85) is True


def test_run_cycle_does_not_start_when_disabled():
    bot = _ONEDRIVE_BOT
    flow = {"startup": {"start_if_not_running": False}}

    with patch("app.bots.tray_ui_runner.is_onedrive_running", return_value=False):
        with patch("app.bots.tray_ui_runner.start_onedrive_background") as start_mock:
            with patch.object(bot, "resolve_tray_state", return_value="unknown"):
                with patch.object(bot, "_emit_tray_diagnostics"):
                    assert bot.run_cycle(flow, 0.85) is False
    start_mock.assert_not_called()
