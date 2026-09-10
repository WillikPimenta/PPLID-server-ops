# -*- coding: utf-8 -*-
"""Testes do intervalo de datas do export Power BI (mes_referencia)."""
from datetime import date

from app.bots.falhas_criticas.powerbi import config as pbi_config


def test_get_date_range_mtd_sem_mes_referencia(monkeypatch):
    monkeypatch.setattr(pbi_config, "_MES_REFERENCIA", None)
    start, end = pbi_config.get_date_range(today=date(2026, 7, 2))
    assert start == "01/07/2026"
    assert end == "02/07/2026"


def test_get_date_range_fechamento_junho(monkeypatch):
    monkeypatch.setattr(pbi_config, "_MES_REFERENCIA", date(2026, 6, 1))
    start, end = pbi_config.get_date_range(today=date(2026, 7, 2))
    assert start == "01/06/2026"
    assert end == "30/06/2026"
