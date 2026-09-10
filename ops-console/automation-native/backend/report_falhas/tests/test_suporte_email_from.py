# -*- coding: utf-8 -*-
"""Testes para resolve_email_from e filtro de Suporte por localidade."""
import pandas as pd

import report_falhas.config_report as cfg
from report_falhas.config_report import resolve_email_from
from report_falhas.render.pages.suporte import build_suporte_page_html


def test_resolve_email_from_uses_configured(monkeypatch):
    monkeypatch.setattr(cfg, "EMAIL_FROM", "analista@empresa.com.br")
    assert resolve_email_from() == "analista@empresa.com.br"


def test_resolve_email_from_prompts_when_local(monkeypatch):
    monkeypatch.setattr(cfg, "EMAIL_FROM", "relatorio@local")
    prompts = iter(["invalido", "nome@empresa.com.br"])
    assert resolve_email_from(prompt_fn=lambda _: next(prompts)) == "nome@empresa.com.br"


def test_suporte_filter_by_localidade_solicitante():
    df_sup = pd.DataFrame({
        "Data": pd.to_datetime(["2026-06-01", "2026-06-02"]),
        "Localidade solicitante": ["Brasília", "São Carlos"],
        "Agente": ["A", "B"],
        "Protocolo": ["1", "2"],
        "Cliente": ["C1", "C2"],
        "Workflow": ["W1", "W2"],
        "Conformidade": ["Sim", "Sim"],
        "Dúvida": ["D1", "D2"],
        "Conclusão": ["C1", "C2"],
        "Crítico": ["Não", "Não"],
        "Grau de dificuldade": ["Fácil", "Fácil"],
    })
    html_bsb = build_suporte_page_html(
        df_sup, pd.Timestamp("2026-06-01").date(), pd.Timestamp("2026-06-30").date(),
        scope_name="Brasília",
    )
    assert "Filtro de BU/local aplicado: Brasília" in html_bsb
    assert "Sem dados de Suporte no período atual" not in html_bsb
