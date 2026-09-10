"""Testes do watchdog de sync infinito do OneDrive (tray_ui)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.bots.bot_tray_ui as bot_tray_ui
from app.infrastructure import onedrive_process as od_proc

_ONEDRIVE_BOT = bot_tray_ui._onedrive_bot
assert _ONEDRIVE_BOT is not None

_FLOW = {
    "sync": {
        "enabled": True,
        "watch": {"poll_seconds": 5, "window_seconds": 15},
        "recovery": {"action": "restart_process"},
    }
}


@pytest.fixture(autouse=True)
def _reset_bot_state():
    bot_tray_ui.parar_event.clear()
    _ONEDRIVE_BOT._sync_restart_cooldown_until = 0.0
    _ONEDRIVE_BOT._consecutive_recovery_failures = 0
    _ONEDRIVE_BOT._onedrive_start_cooldown_until = 0.0
    yield
    bot_tray_ui.parar_event.set()


def test_sync_watch_exits_when_state_changes():
    states = iter(["syncing", "connected"])

    def fake_wait(timeout):
        return False

    with patch.object(_ONEDRIVE_BOT.parar_event, "wait", fake_wait):
        with patch.object(_ONEDRIVE_BOT, "resolve_tray_state", lambda flow, conf: next(states)):
            assert _ONEDRIVE_BOT._handle_syncing_state(_FLOW, 0.85) is True


def test_sync_watch_restarts_after_window(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.bots.tray_ui_runner.time.monotonic", lambda: clock[0])
    restart_calls: list[bool] = []

    def fake_wait(timeout):
        clock[0] += timeout
        return False

    with patch.object(_ONEDRIVE_BOT.parar_event, "wait", fake_wait):
        with patch.object(_ONEDRIVE_BOT, "resolve_tray_state", lambda flow, conf: "syncing"):
            with patch(
                "app.bots.tray_ui_runner.restart_onedrive_background",
                lambda simulate=None: restart_calls.append(True) or True,
            ):
                after = iter(["connected"])

                def resolve(flow, conf):
                    if restart_calls:
                        return next(after)
                    return "syncing"

                with patch.object(_ONEDRIVE_BOT, "resolve_tray_state", resolve):
                    assert _ONEDRIVE_BOT._handle_syncing_state(_FLOW, 0.85) is True
    assert len(restart_calls) == 1


def test_run_cycle_routes_syncing_to_watch(monkeypatch):
    bot = _ONEDRIVE_BOT
    flow = {
        "sync": {
            "enabled": True,
            "watch": {"poll_seconds": 5, "window_seconds": 15},
            "recovery": {"action": "restart_process"},
        }
    }
    calls: list[bool] = []

    def fake_handle(f, conf):
        calls.append(True)
        return True

    with patch("app.bots.tray_ui_runner.is_onedrive_running", return_value=True):
        with patch.object(bot, "resolve_tray_state", lambda f, c: "syncing"):
            with patch.object(bot, "_emit_tray_diagnostics"):
                with patch.object(bot, "_handle_syncing_state", side_effect=fake_handle):
                    assert bot.run_cycle(flow, 0.85) is True
    assert calls == [True]


def test_run_cycle_skips_sync_watch_when_disabled():
    bot = _ONEDRIVE_BOT
    flow = {
        "sync": {
            "enabled": False,
            "watch": {"poll_seconds": 5, "window_seconds": 15},
        }
    }

    with patch("app.bots.tray_ui_runner.is_onedrive_running", return_value=True):
        with patch.object(bot, "resolve_tray_state", lambda f, c: "syncing"):
            with patch.object(bot, "_emit_tray_diagnostics"):
                with patch.object(bot, "_handle_syncing_state") as handle_mock:
                    assert bot.run_cycle(flow, 0.85) is True
    handle_mock.assert_not_called()


def test_sync_watch_skips_restart_when_recovery_none():
    flow = {
        "sync": {
            "enabled": True,
            "watch": {"poll_seconds": 5, "window_seconds": 15},
            "recovery": {"action": "none"},
        }
    }

    def fake_wait(timeout):
        return False

    with patch.object(_ONEDRIVE_BOT.parar_event, "wait", fake_wait):
        with patch.object(_ONEDRIVE_BOT, "resolve_tray_state", lambda flow, conf: "syncing"):
            with patch(
                "app.bots.tray_ui_runner.restart_onedrive_background",
                side_effect=AssertionError("restart should not be called"),
            ):
                assert _ONEDRIVE_BOT._handle_syncing_state(flow, 0.85) is True


def test_restart_onedrive_simulate(monkeypatch, tmp_path):
    monkeypatch.setattr(od_proc.os, "name", "nt")
    monkeypatch.setenv("ONEDRIVE_SIMULATE_RESTART", "1")
    exe = tmp_path / "OneDrive.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(od_proc, "find_onedrive_exe", lambda: exe)
    with patch.object(od_proc.subprocess, "run") as run_mock:
        with patch.object(od_proc.subprocess, "Popen") as popen_mock:
            assert od_proc.restart_onedrive_background() is True
    run_mock.assert_not_called()
    popen_mock.assert_not_called()
