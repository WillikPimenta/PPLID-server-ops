# -*- coding: utf-8 -*-
"""Testes de run_report(settings) sem prompts."""
from datetime import date
from pathlib import Path

import pytest

from report_falhas.legacy_main import _coerce_bool, _parse_mes_referencia


def test_parse_mes_referencia_mm_aaaa():
    assert _parse_mes_referencia("06/2026") == date(2026, 6, 1)


def test_parse_mes_referencia_vazio_usa_hoje(monkeypatch):
    from datetime import date as real_date

    class FixedToday(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 6, 30)

    monkeypatch.setattr("report_falhas.legacy_main.date", FixedToday)
    assert _parse_mes_referencia("") == real_date(2026, 6, 30)
    assert _parse_mes_referencia(None) == real_date(2026, 6, 30)


def test_coerce_bool_strings():
    assert _coerce_bool("sim", False) is True
    assert _coerce_bool("n", True) is False


def test_coerce_bool_salvar_html_individuais_default_false():
    assert _coerce_bool(None, False) is False
    assert _coerce_bool("false", True) is False
    assert _coerce_bool(0, True) is False


def test_run_report_excel_path_override_used_by_impl(monkeypatch, tmp_path):
    """falhas_excel_path deve valer em runtime (nao o EXCEL_PATH importado no load)."""
    import report_falhas.config_report as cfg

    custom = tmp_path / "FALHAS_CRITICAS_MANUAL.xlsx"
    custom.write_bytes(b"")
    seen: dict[str, str] = {}

    def fake_impl(**_kwargs):
        seen["path"] = cfg.require_excel_path()

    monkeypatch.setattr("report_falhas.legacy_main._legacy_main_impl", fake_impl)

    from report_falhas.legacy_main import run_report

    run_report({"falhas_excel_path": str(custom)})
    assert seen["path"] == str(custom)
