# -*- coding: utf-8 -*-
"""Garante que textos do e-mail e HTML com abas usam UTF-8 correto (sem mojibake)."""
import re

from report_falhas.html_pages import build_tabs_index_html, inject_email_intro

MOJIBAKE_PATTERNS = re.compile(r"Ol├|Cr├|Navega├|ÔÇ|┬À|­ƒ")


def test_inject_email_intro_has_correct_portuguese():
    html = inject_email_intro(
        "<html><body></body></html>",
        "São Carlos",
        "01/06/2026 a 15/06/2026",
        "• <b>teste</b>",
    )
    assert "Olá, pessoal!" in html
    assert "Críticas" in html
    assert "Período" in html
    assert "📎 Anexos e como usar" in html
    assert not MOJIBAKE_PATTERNS.search(html)


def test_inject_email_intro_kpi_cards_encoding():
    kpis = {
        "fail_mtd_atual": 10,
        "fail_prev_equal": 8,
        "variacao_perc": "+5%",
        "variacao_delta": "+2",
        "reinc_total": 3,
        "top1_cenario_nome": "Formatação",
        "top1_cenario_qtd": 2,
        "periodo_mtd": "01/06/2026 a 15/06/2026",
        "periodo_equal_prev": "01/05/2026 a 15/05/2026",
    }
    html = inject_email_intro(
        "<html><body></body></html>",
        "Brasília",
        "01/06/2026 a 15/06/2026",
        "• teste",
        kpis_total=kpis,
        kpis_oficial=kpis,
    )
    assert "Variação vs comparativo" in html
    assert "cenário" in html
    assert "Métrica Oficial" in html
    assert "🟣 Resumo 30s · Total (Geral)" in html
    assert "✅ Resumo 30s · Métrica Oficial" in html
    assert not MOJIBAKE_PATTERNS.search(html)


def test_build_tabs_index_html_has_correct_portuguese():
    html = build_tabs_index_html(
        "Report Teste",
        "oficial.html",
        "total.html",
        "client.html",
        "suporte.html",
        "ult3m.html",
        html_oficial="<html><body><p>ok</p></body></html>",
    )
    assert "Navegação rápida entre as visões do relatório" in html
    assert "Métrica Oficial" in html
    assert "Falhas últimos 3 meses" in html
    assert "<noscript>" in html
    assert "Suporte TEAMS" in html
    assert "<p>ok</p>" in html
    assert not MOJIBAKE_PATTERNS.search(html)
