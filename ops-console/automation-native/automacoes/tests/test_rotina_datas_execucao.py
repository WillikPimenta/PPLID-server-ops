"""Testes de resolução de datas de execução da rotina."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from app.bots.rotina.io import _resolver_datas_execucao

TZ_BR = timezone(timedelta(hours=-3))
HOJE = datetime(2026, 6, 11, 8, 0, 0, tzinfo=TZ_BR)


@pytest.fixture(autouse=True)
def _limpar_env_rotina(monkeypatch):
    for key in ("ROTINA_DATA_INICIO", "ROTINA_DATA_FIM", "ROTINA_DATA_EXECUCAO"):
        monkeypatch.delenv(key, raising=False)


@patch("app.bots.rotina.io._get_tz_br", return_value=TZ_BR)
@patch("app.bots.rotina.io.datetime")
def test_sem_settings_retorna_hoje(mock_datetime, _mock_tz):
    mock_datetime.now.return_value = HOJE
    mock_datetime.strptime = datetime.strptime

    assert _resolver_datas_execucao() == ["2026-06-11"]


@patch("app.bots.rotina.io._get_tz_br", return_value=TZ_BR)
@patch("app.bots.rotina.io.datetime")
def test_ignora_rotina_data_execucao_stale(mock_datetime, _mock_tz, monkeypatch):
    mock_datetime.now.return_value = HOJE
    mock_datetime.strptime = datetime.strptime
    monkeypatch.setenv("ROTINA_DATA_EXECUCAO", "2026-06-10")

    assert _resolver_datas_execucao() == ["2026-06-11"]


@patch("app.bots.rotina.io._get_tz_br", return_value=TZ_BR)
@patch("app.bots.rotina.io.datetime")
def test_periodo_explicito_via_settings(mock_datetime, _mock_tz):
    mock_datetime.now.return_value = HOJE
    mock_datetime.strptime = datetime.strptime

    settings = {
        "rotina_data_inicio": "2026-06-09",
        "rotina_data_fim": "2026-06-11",
    }
    assert _resolver_datas_execucao(settings=settings) == [
        "2026-06-09",
        "2026-06-10",
        "2026-06-11",
    ]


@patch("app.bots.rotina.io._get_tz_br", return_value=TZ_BR)
@patch("app.bots.rotina.io.datetime")
def test_periodo_via_env_vars(mock_datetime, _mock_tz, monkeypatch):
    mock_datetime.now.return_value = HOJE
    mock_datetime.strptime = datetime.strptime
    monkeypatch.setenv("ROTINA_DATA_INICIO", "2026-06-10")
    monkeypatch.setenv("ROTINA_DATA_FIM", "2026-06-10")
    monkeypatch.setenv("ROTINA_DATA_EXECUCAO", "2026-06-09")

    assert _resolver_datas_execucao() == ["2026-06-10"]


@patch("app.bots.rotina.io._get_tz_br", return_value=TZ_BR)
@patch("app.bots.rotina.io.datetime")
def test_dia_unico_via_settings(mock_datetime, _mock_tz):
    mock_datetime.now.return_value = HOJE
    mock_datetime.strptime = datetime.strptime

    settings = {"rotina_data_inicio": "2026-06-11", "rotina_data_fim": "2026-06-11"}
    assert _resolver_datas_execucao(settings=settings) == ["2026-06-11"]
