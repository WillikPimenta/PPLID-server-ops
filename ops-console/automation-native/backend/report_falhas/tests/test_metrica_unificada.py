# -*- coding: utf-8 -*-
"""Testes do corte métrica unificada (jul/2026+)."""
from datetime import date

import pandas as pd

from report_falhas.filters import (
    MODULOS_METRICA_OFICIAL,
    aplicar_recorte_oficial,
    filtrar_metrica_oficial,
    uses_dual_metric_mode,
)
from report_falhas.html_pages import build_client_workflow_page_html, build_tabs_index_html
from report_falhas.render.pages.ult3m import format_html_ultimos_3_meses
from report_falhas.matricula_utils import resolve_agent_name
from report_falhas.executive_report import _build_html, _card_bu, _tendencia


def _sample_kpis_helper():
    return {
        'total_atual': 20, 'total_prev': 15, 'oficial_atual': 12, 'oficial_prev': 10,
        'trend_total': _tendencia(20, 15),
        'trend_oficial': _tendencia(12, 10),
        'reincidentes': 3, 'reinc_pct': 25.0, 'novos': 2,
        'prev_eq_start': date(2026, 5, 1), 'prev_eq_end': date(2026, 5, 31),
        'dificuldade': {}, 'team_categoria': {},
    }


def test_uses_dual_metric_mode_jun_vs_jul():
    assert uses_dual_metric_mode(date(2026, 6, 1)) is True
    assert uses_dual_metric_mode(date(2026, 6, 30)) is True
    assert uses_dual_metric_mode(date(2026, 7, 1)) is False
    assert uses_dual_metric_mode(date(2026, 7, 15)) is False


def test_aplicar_recorte_oficial_jun_filtra_modulo():
    df = pd.DataFrame({
        'Módulo': ['Demais falhas', 'OUTRO_MODULO'],
        'Localidade': ['Brasília', 'Brasília'],
    })
    out = aplicar_recorte_oficial(df, date(2026, 6, 1))
    assert len(out) == 1
    assert out.iloc[0]['Módulo'] == 'Demais falhas'


def test_aplicar_recorte_oficial_jul_mantem_todos_modulos():
    df = pd.DataFrame({
        'Módulo': ['Demais falhas', 'OUTRO_MODULO'],
        'Localidade': ['Brasília', 'Brasília'],
    })
    out = aplicar_recorte_oficial(df, date(2026, 7, 1))
    assert len(out) == 2


def test_filtrar_metrica_oficial_legado_inalterado():
    df = pd.DataFrame({'Módulo': list(MODULOS_METRICA_OFICIAL) + ['X']})
    assert len(filtrar_metrica_oficial(df)) == len(MODULOS_METRICA_OFICIAL)


def test_build_tabs_sem_total_quando_unificado():
    html = build_tabs_index_html(
        'Test',
        'of.html', '', 'cw.html', 'sup.html', 'u3m.html',
        html_oficial='<html><body><p>Oficial</p></body></html>',
        include_total_tab=False,
        primary_tab_label='Report',
    )
    assert 'Total (Geral)' not in html
    assert 'Report' in html
    assert 'Métrica Oficial' not in html


def test_build_client_workflow_sem_toggle_quando_unificado():
    df = pd.DataFrame({
        'Data de Análise': pd.to_datetime(['2026-07-01']),
        'Cliente': ['A'],
        'Workflow': ['W'],
        'Módulo': ['Demais falhas'],
        'Localidade': ['Brasília'],
    })
    html = build_client_workflow_page_html(
        df, df, date(2026, 7, 1), date(2026, 7, 6),
        scope_name='Brasília',
        dual_metric_mode=False,
    )
    assert 'Total (Geral)' not in html
    assert 'Métrica Oficial' not in html
    assert 'clients_workflows_filter' not in html
    assert 'Clientes / Workflows' in html


def test_ult3m_sem_toggle_quando_unificado():
    df = pd.DataFrame(columns=[
        'Data de Análise', 'Matrícula Agente', 'Localidade', 'Módulo', 'Cenário',
    ])
    html = format_html_ultimos_3_meses(
        'Brasília',
        date(2026, 7, 1),
        date(2026, 7, 6),
        df,
        df,
        dual_metric_mode=False,
    )
    assert 'showMetricPanelUlt3m' not in html
    assert 'Total (Geral)' not in html
    assert 'Métrica Oficial' not in html


def test_resolve_agent_name_from_row_nome_agente():
    row = pd.Series({'Nome Agente': 'Maria Silva', 'Matrícula Agente': 'c12345a'})
    nome = resolve_agent_name('c12345a', 'c12345a', {}, row)
    assert nome == 'Maria Silva'


def test_resolve_agent_name_from_concatenated_matricula():
    nome = resolve_agent_name('', 'c92935aNome do agente Fulano', {})
    assert nome == 'Fulano'


def test_build_tabs_com_total_modo_dual():
    html = build_tabs_index_html(
        'Test',
        'of.html', 'tot.html', 'cw.html', 'sup.html', 'u3m.html',
        html_oficial='<html><body><p>Oficial</p></body></html>',
        html_total='<html><body><p>Total</p></body></html>',
        include_total_tab=True,
    )
    assert 'Total (Geral)' in html


def test_card_bu_sem_total_em_jul():
    k = _sample_kpis_helper()
    html = _card_bu('Brasília', k, dual_metric_mode=False)
    assert 'sem métrica oficial' not in html.lower()
    assert 'Reincidentes' in html


def test_executive_html_sem_nota_total_em_jul():
    ka = kb = _sample_kpis_helper()
    ka['team_categoria'] = {'total_atual': 0, 'categorias': {}, 'dominante': ''}
    ka['tempo_casa'] = {}
    ka['dificuldade'] = {'total_atual': 0, 'categorias': {}, 'dominante': ''}
    kb = dict(ka)
    html = _build_html(
        'Brasília', ka, 'São Carlos', kb,
        date(2026, 7, 1), date(2026, 7, 6),
        dual_metric_mode=False,
    )
    assert 'Total (sem métrica oficial)' not in html
