# -*- coding: utf-8 -*-
"""Testes de auto-login Microsoft no export Power BI."""
from unittest.mock import MagicMock, patch

from app.bots.falhas_criticas import orchestration
from app.bots.falhas_criticas.powerbi import browser, config as pbi_config


def test_page_has_login_microsoft_url():
    page = MagicMock()
    page.url = "https://login.microsoftonline.com/common/oauth2/authorize"
    page.frames = []
    assert browser._page_has_login(page) is True


def test_page_has_login_power_bi_url():
    page = MagicMock()
    page.url = "https://app.powerbi.com/groups/me/reports/abc"
    page.frames = []
    page.get_by_text.return_value.first.is_visible.side_effect = Exception("not found")
    assert browser._page_has_login(page) is False


def test_login_auto_click_selectors_order():
    assert pbi_config.LOGIN_AUTO_CLICK_SELECTORS[0] == '[data-test-id="account"]'
    assert "#idSIButton9" in pbi_config.LOGIN_AUTO_CLICK_SELECTORS
    assert 'input[type="submit"]' in pbi_config.LOGIN_AUTO_CLICK_SELECTORS


def test_login_auto_click_labels_cover_sso_buttons():
    labels = " ".join(pbi_config.LOGIN_AUTO_CLICK_LABELS)
    for token in ("Entrar", "Sign in", "Continuar", "Sim", "Yes"):
        assert token in labels


def test_login_poll_seconds_is_short():
    assert pbi_config.LOGIN_POLL_SECONDS == 3


def test_wait_login_single_poll_not_900s(monkeypatch):
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(orchestration.time, "sleep", fake_sleep)
    monkeypatch.setattr(orchestration, "is_reveal_requested", lambda: False)
    monkeypatch.setattr(orchestration.parar_event, "is_set", lambda: False)

    orchestration._wait_login("Autenticando no Microsoft...")

    assert sleeps == [pbi_config.LOGIN_POLL_SECONDS]
    assert sum(sleeps) < 10


def test_try_auto_login_clicks_account_tile():
    page = MagicMock()
    page.frames = []
    locator = MagicMock()
    locator.count.return_value = 1
    locator.nth.return_value.is_visible.return_value = True
    locator.nth.return_value.is_enabled.return_value = True
    page.get_by_text.return_value = locator

    with patch.object(browser, "_try_click_locator", return_value=True) as click_mock:
        assert browser._try_auto_login(page) is True
        click_mock.assert_called_once()
