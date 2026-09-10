"""Testes do bot tray_ui com ícones de instalação mockados."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

import app.bots.bot_tray_ui as bot_tray_ui
import app.infrastructure.screen_automation as sa
from app.services import tray_install_icons as install_icons
from app.services.tray_install_icons import TrayIconTemplate

_ONEDRIVE_BOT = bot_tray_ui._onedrive_bot
assert _ONEDRIVE_BOT is not None


def _fake_icon(label: str, kind: str, confidence: float) -> TrayIconTemplate:
    bgr = np.zeros((16, 16, 3), dtype=np.uint8)
    mask = np.full((16, 16), 255, dtype=np.uint8)
    return TrayIconTemplate(
        bgr=bgr,
        mask=mask,
        source_path=Path(f"{label}.png"),
        label=label,
        state={"conn": "connected", "disc": "disconnected", "sync": "syncing"}[kind],
        kind=kind,
    )


@pytest.fixture(autouse=True)
def _reset_bot():
    bot_tray_ui.parar_event.clear()
    _ONEDRIVE_BOT._consecutive_unknown = 0
    _ONEDRIVE_BOT._consecutive_recovery_failures = 0
    _ONEDRIVE_BOT._last_icon_match = None
    yield
    bot_tray_ui.parar_event.set()


@pytest.fixture
def mock_install_ready(tmp_path: Path):
    with patch.object(
        install_icons,
        "has_minimum_icons",
        lambda service, repo_templates_dir=None, fallback_templates_dir=None: service == "onedrive",
    ):
        yield tmp_path


def test_validate_flow_requires_install_icons(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        install_icons,
        "has_minimum_icons",
        lambda service, repo_templates_dir=None, fallback_templates_dir=None: False,
    )
    flow = _ONEDRIVE_BOT.load_flow()
    assert _ONEDRIVE_BOT.validate_flow(flow) is False


def test_validate_flow_ok_when_install_ready(mock_install_ready):
    flow = _ONEDRIVE_BOT.load_flow()
    assert _ONEDRIVE_BOT.validate_flow(flow) is True


def test_resolve_connected(mock_install_ready):
    flow = _ONEDRIVE_BOT.load_flow()
    conn = _fake_icon("connected", "conn", 0.95)
    disc = _fake_icon("disconnected", "disc", 0.80)

    def fake_match(icon, confidence=0.0):
        if icon.label == "connected":
            return sa.MatchResult(0, 0, 16, 16, 0.95)
        if icon.label == "disconnected":
            return sa.MatchResult(0, 0, 16, 16, 0.80)
        return None

    with patch.object(_ONEDRIVE_BOT, "_install_state_entries", return_value=[("conn", conn), ("disc", disc)]):
        with patch.object(_ONEDRIVE_BOT, "_match_install_icon", side_effect=fake_match):
            assert _ONEDRIVE_BOT.resolve_tray_state(flow, 0.85) == "connected"


def test_resolve_disconnected_when_disc_beats_conn(mock_install_ready):
    flow = _ONEDRIVE_BOT.load_flow()
    conn = _fake_icon("connected", "conn", 0.88)
    disc = _fake_icon("disconnected", "disc", 0.95)

    def fake_match(icon, confidence=0.0):
        if icon.label == "connected":
            return sa.MatchResult(0, 0, 16, 16, 0.88)
        if icon.label == "disconnected":
            return sa.MatchResult(0, 0, 16, 16, 0.95)
        return None

    with patch.object(_ONEDRIVE_BOT, "_install_state_entries", return_value=[("conn", conn), ("disc", disc)]):
        with patch.object(_ONEDRIVE_BOT, "_match_install_icon", side_effect=fake_match):
            assert _ONEDRIVE_BOT.resolve_tray_state(flow, 0.85) == "disconnected"


def test_resolve_disconnected_when_disc_beats_sync(mock_install_ready):
    flow = _ONEDRIVE_BOT.load_flow()
    conn = _fake_icon("connected", "conn", 0.91)
    disc = _fake_icon("disconnected", "disc", 0.985)
    sync = _fake_icon("syncIcon", "sync", 1.0)

    def fake_match(icon, confidence=0.0):
        scores = {
            "connected": 0.915,
            "disconnected": 0.985,
            "syncIcon": 1.0,
        }
        score = scores.get(icon.label)
        if score is None:
            return None
        return sa.MatchResult(100, 200, 16, 16, score)

    entries = [("conn", conn), ("disc", disc), ("sync", sync)]
    with patch.object(_ONEDRIVE_BOT, "_install_state_entries", return_value=entries):
        with patch.object(_ONEDRIVE_BOT, "_match_install_icon", side_effect=fake_match):
            assert _ONEDRIVE_BOT.resolve_tray_state(flow, 0.85) == "disconnected"


def test_pick_best_cluster_prefers_tray_over_sync_only():
    disc_match = {
        "kind": "disc",
        "icon": _fake_icon("disconnected", "disc", 0.98),
        "match": sa.MatchResult(100, 200, 16, 16, 0.985),
        "threshold": 0.70,
    }
    conn_match = {
        "kind": "conn",
        "icon": _fake_icon("connected", "conn", 0.91),
        "match": sa.MatchResult(100, 200, 16, 16, 0.915),
        "threshold": 0.85,
    }
    tray_cluster = [disc_match, conn_match]
    sync_only = [{
        "kind": "sync",
        "icon": _fake_icon("syncIcon", "sync", 1.0),
        "match": sa.MatchResult(500, 200, 16, 16, 1.0),
        "threshold": 0.75,
    }]
    best = _ONEDRIVE_BOT._pick_best_cluster([sync_only, tray_cluster])
    assert best is tray_cluster


    flow = _ONEDRIVE_BOT.load_flow()
    step = {"action": "click", "region": "tray"}
    _ONEDRIVE_BOT._last_icon_match = sa.MatchResult(120, 200, 16, 16, 0.9)
    with patch.object(sa, "click_at") as click_mock:
        assert _ONEDRIVE_BOT._execute_step(step, flow, 0.85) is True
    click_mock.assert_called_once_with(128, 208)


def test_pick_best_cluster_penalizes_isolated_disc():
    isolated_alert = [{
        "kind": "disc",
        "icon": _fake_icon("alertIcon", "disc", 1.0),
        "match": sa.MatchResult(909, 999, 16, 16, 1.0),
        "threshold": 0.70,
    }]
    tray_cluster = [
        {
            "kind": "conn",
            "icon": _fake_icon("connected", "conn", 0.97),
            "match": sa.MatchResult(1072, 1000, 16, 16, 0.97),
            "threshold": 0.85,
        },
        {
            "kind": "sync",
            "icon": _fake_icon("syncing", "sync", 0.95),
            "match": sa.MatchResult(1072, 1000, 16, 16, 0.95),
            "threshold": 0.75,
        },
    ]
    best = _ONEDRIVE_BOT._pick_best_cluster([isolated_alert, tray_cluster])
    assert best is tray_cluster


def test_resolve_connected_when_isolated_alertIcon_elsewhere(mock_install_ready):
    flow = _ONEDRIVE_BOT.load_flow()
    conn = _fake_icon("connected", "conn", 0.97)
    sync = _fake_icon("syncing", "sync", 0.95)
    alert = _fake_icon("alertIcon", "disc", 1.0)

    def fake_match(icon, confidence=0.0):
        if icon.label == "connected":
            return sa.MatchResult(1072, 1000, 16, 16, 0.97)
        if icon.label == "syncing":
            return sa.MatchResult(1072, 1000, 16, 16, 0.95)
        if icon.label == "alertIcon":
            return sa.MatchResult(909, 999, 16, 16, 1.0)
        return None

    entries = [("conn", conn), ("sync", sync), ("disc", alert)]
    with patch.object(_ONEDRIVE_BOT, "_install_state_entries", return_value=entries):
        with patch.object(_ONEDRIVE_BOT, "_match_install_icon", side_effect=fake_match):
            state = _ONEDRIVE_BOT.resolve_tray_state(flow, 0.85)
    assert state in ("connected", "syncing")


def test_step_label_tray_click_shows_icon_position():
    flow = _ONEDRIVE_BOT.load_flow()
    step = {"action": "click", "region": "tray"}
    _ONEDRIVE_BOT._last_icon_match = sa.MatchResult(1072, 1000, 16, 16, 0.9)
    assert _ONEDRIVE_BOT._step_label(step) == "icon@1080,1008"


def test_resolve_connected_soft_when_conn_beats_fragile_disc(mock_install_ready):
    """AppBlue ~84% com disc de instalação abaixo — não deve ficar unknown."""
    flow = _ONEDRIVE_BOT.load_flow()
    conn = _fake_icon("AppBlue", "conn", 0.84)
    disc_a = _fake_icon("AppErrorBlue", "disc", 0.81)
    disc_b = _fake_icon("QuotaOverLimit_default", "disc", 0.80)

    def fake_match(icon, confidence=0.0):
        scores = {
            "AppBlue": 0.84,
            "AppErrorBlue": 0.81,
            "QuotaOverLimit_default": 0.80,
        }
        score = scores.get(icon.label)
        if score is None:
            return None
        return sa.MatchResult(1195, 957, 16, 16, score)

    entries = [("conn", conn), ("disc", disc_a), ("disc", disc_b)]
    with patch.object(_ONEDRIVE_BOT, "_install_state_entries", return_value=entries):
        with patch.object(_ONEDRIVE_BOT, "_match_install_icon", side_effect=fake_match):
            assert _ONEDRIVE_BOT.resolve_tray_state(flow, 0.85) == "connected"


def test_resolve_connected_when_disc_only_slightly_above_conn(mock_install_ready):
    """Repo disconnected+92% vs connected+89% com margem 10% — deve ser connected."""
    flow = _ONEDRIVE_BOT.load_flow()
    conn = _fake_icon("connected", "conn", 0.89)
    disc = _fake_icon("disconnected", "disc", 0.92)

    def fake_match(icon, confidence=0.0):
        scores = {"connected": 0.89, "disconnected": 0.92}
        score = scores.get(icon.label)
        if score is None:
            return None
        return sa.MatchResult(1073, 1000, 16, 16, score)

    entries = [("conn", conn), ("disc", disc)]
    with patch.object(_ONEDRIVE_BOT, "_install_state_entries", return_value=entries):
        with patch.object(_ONEDRIVE_BOT, "_match_install_icon", side_effect=fake_match):
            assert _ONEDRIVE_BOT.resolve_tray_state(flow, 0.85) == "connected"


