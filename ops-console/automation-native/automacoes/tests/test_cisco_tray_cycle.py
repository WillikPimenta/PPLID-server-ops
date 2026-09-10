"""Testes do ciclo Cisco com vpncli no tray_ui."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import app.bots.bot_tray_ui as bot_tray_ui
from app.infrastructure.cisco_vpn import VpncliResult
from app.services import tray_install_icons as install_icons

_CISCO_BOT = bot_tray_ui._cisco_bot
assert _CISCO_BOT is not None


@pytest.fixture(autouse=True)
def _reset_bot():
    bot_tray_ui.parar_event.clear()
    _CISCO_BOT._consecutive_unknown = 0
    _CISCO_BOT._consecutive_recovery_failures = 0
    _CISCO_BOT._last_icon_match = None
    yield
    bot_tray_ui.parar_event.set()


@pytest.fixture
def mock_cisco_templates_ready():
    with patch.object(
        install_icons,
        "has_minimum_icons",
        lambda service, repo_templates_dir=None, fallback_templates_dir=None: service == "cisco",
    ):
        yield


def test_cisco_run_cycle_connected_skips_tray(mock_cisco_templates_ready):
    flow = _CISCO_BOT.load_flow()
    vpn = VpncliResult(state="connected", raw_output=">> state: Connected", command="state", exe=Path("vpncli.exe"))

    with patch("app.bots.tray_ui_runner.get_vpn_state", return_value=vpn):
        with patch.object(_CISCO_BOT, "resolve_tray_state") as resolve_tray:
            with patch.object(_CISCO_BOT, "_attempt_recovery") as recovery:
                assert _CISCO_BOT.run_cycle(flow, 0.85) is True
                resolve_tray.assert_not_called()
                recovery.assert_not_called()


def test_cisco_run_cycle_disconnected_calls_recovery(mock_cisco_templates_ready):
    flow = _CISCO_BOT.load_flow()
    vpn = VpncliResult(
        state="disconnected",
        raw_output=">> state: Disconnected",
        command="state",
        exe=Path("vpncli.exe"),
    )

    with patch("app.bots.tray_ui_runner.get_vpn_state", return_value=vpn):
        with patch.object(_CISCO_BOT, "_attempt_recovery", return_value=True) as recovery:
            with patch.object(_CISCO_BOT, "resolve_tray_state") as resolve_tray:
                assert _CISCO_BOT.run_cycle(flow, 0.85) is True
                recovery.assert_called_once()
                resolve_tray.assert_not_called()


def test_cisco_run_cycle_unknown_falls_back_to_tray(mock_cisco_templates_ready):
    flow = _CISCO_BOT.load_flow()
    vpn = VpncliResult(state="unknown", raw_output="", command="state", exe=None)

    with patch("app.bots.tray_ui_runner.get_vpn_state", return_value=vpn):
        with patch.object(_CISCO_BOT, "resolve_tray_state", return_value="connected") as resolve_tray:
            with patch.object(_CISCO_BOT, "_emit_tray_diagnostics"):
                assert _CISCO_BOT.run_cycle(flow, 0.85) is True
                resolve_tray.assert_called()
