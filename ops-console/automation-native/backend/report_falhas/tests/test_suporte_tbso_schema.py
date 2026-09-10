# -*- coding: utf-8 -*-
"""Schema TBSO_* (SharePoint) → colunas canônicas do Suporte."""
from datetime import date

import pandas as pd

from report_falhas.io.data_loader import _normalize_suporte_schema, compute_conformidade_suporte
from report_falhas.render.pages.suporte import build_suporte_page_html


def test_normalize_suporte_tbso_schema():
    df = pd.DataFrame({
        'TBSO_PROTOCOLO': ['74822684', None],
        'TBSO_DATA_CADASTRO': [pd.Timestamp('2026-07-15 10:00:00'), pd.NaT],
        'TBSO_CLIENTE': ['Picpay', None],
        'TBSO_WORKFLOW': ['WF1', None],
        'TBSO_MATRICULA_OPERACAO': ['c92735a', None],
        'TBSO_MATRICULA_SOLICITANTE': ['C92983A', None],
        'TBSO_DUVIDA': ['DOCUMENTO POSSUI RASURAS', None],
        'TBSO_RESULTADO_CORRETO': ['DOCUMENTO POSSUI RASURAS', None],
        'TBSO_GRAU_DE_DIFICULDADE': ['DIFÍCIL', None],
        'TBSO_CRITICO': ['Não', None],
        'TBSO_UF': ['MT', None],
        'TBSO_TIPO_DE_DOCUMENTO': ['RG', None],
        'Unnamed: 25': [None, None],
    })
    out = _normalize_suporte_schema(df)
    assert 'Data' in out.columns
    assert 'Protocolo' in out.columns
    assert 'Agente' in out.columns
    assert 'Dúvida' in out.columns
    assert 'Conclusão' in out.columns
    assert 'Grau de dificuldade' in out.columns
    assert 'Crítico' in out.columns
    assert 'UF de emissão' in out.columns
    assert len(out) == 1  # linha vazia descartada
    assert out.iloc[0]['Protocolo'] == '74822684'
    assert out.iloc[0]['Agente'] == 'c92735a'
    assert out.iloc[0]['Conformidade (Regra)'] == 'CONFORME'  # dificuldade DIFÍCIL


def test_normalize_suporte_legacy_schema_still_works():
    df = pd.DataFrame({
        'Data': [pd.Timestamp('2026-06-01')],
        'Protocolo': ['1'],
        'Cliente': ['C1'],
        'Workflow': ['W1'],
        'Agente': ['Fulano'],
        'Dúvida': ['X'],
        'Conclusão': ['X'],
        'Crítico': ['Não'],
        'Grau de dificuldade': ['Fácil'],
        'Localidade solicitante': ['Brasília'],
    })
    out = _normalize_suporte_schema(df)
    assert len(out) == 1
    assert out.iloc[0]['Agente'] == 'Fulano'
    assert out.iloc[0]['Conformidade (Regra)'] == 'CONFORME'


def test_build_suporte_html_with_tbso_normalized():
    df = _normalize_suporte_schema(pd.DataFrame({
        'TBSO_PROTOCOLO': ['1', '2'],
        'TBSO_DATA_CADASTRO': [pd.Timestamp('2026-07-01'), pd.Timestamp('2026-07-02')],
        'TBSO_CLIENTE': ['C1', 'C2'],
        'TBSO_WORKFLOW': ['W1', 'W2'],
        'TBSO_MATRICULA_OPERACAO': ['c90001a', 'c90002a'],
        'TBSO_DUVIDA': ['A', 'B'],
        'TBSO_RESULTADO_CORRETO': ['A', 'C'],
        'TBSO_GRAU_DE_DIFICULDADE': ['FÁCIL', 'FÁCIL'],
        'TBSO_CRITICO': ['Não', 'Sim'],
        'TBSO_UF': ['SP', 'RJ'],
    }))
    html = build_suporte_page_html(
        df, date(2026, 7, 1), date(2026, 7, 31), scope_name='Geral',
    )
    assert 'Sem dados na aba Suporte' not in html
    assert 'Sem dados de Suporte no período atual' not in html
    assert 'Solicitações Suporte' in html or 'Consolidado' in html
