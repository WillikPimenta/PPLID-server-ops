"""Testes da janela operacional H/H (23h do dia anterior → agora / 23h)."""

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from app.bots.bot_production import (
    _bounds_janela_operacional_hxh,
    _periodo_analise_hxh,
    _processar_dataframe_producao,
)

TZ_BR = ZoneInfo("America/Sao_Paulo")


def test_periodo_hoje_ate_agora():
    agora = datetime(2026, 7, 29, 14, 30, 45, tzinfo=TZ_BR)
    inicio, fim = _periodo_analise_hxh(agora, agora)
    assert inicio == "28/07/2026 23:00"
    assert fim == "29/07/2026 14:30"


def test_periodo_dia_fechado_ate_23h():
    agora = datetime(2026, 7, 29, 14, 30, tzinfo=TZ_BR)
    ontem = datetime(2026, 7, 28, 10, 0, tzinfo=TZ_BR)
    inicio, fim = _periodo_analise_hxh(ontem, agora)
    assert inicio == "27/07/2026 23:00"
    assert fim == "28/07/2026 23:00"


def test_periodo_anteontem():
    agora = datetime(2026, 7, 29, 9, 5, tzinfo=TZ_BR)
    anteontem = datetime(2026, 7, 27, 12, 0, tzinfo=TZ_BR)
    inicio, fim = _periodo_analise_hxh(anteontem, agora)
    assert inicio == "26/07/2026 23:00"
    assert fim == "27/07/2026 23:00"


def test_periodo_virada_de_mes():
    agora = datetime(2026, 8, 1, 8, 15, tzinfo=TZ_BR)
    inicio, fim = _periodo_analise_hxh(agora, agora)
    assert inicio == "31/07/2026 23:00"
    assert fim == "01/08/2026 08:15"

    ontem = datetime(2026, 7, 31, 12, 0, tzinfo=TZ_BR)
    inicio2, fim2 = _periodo_analise_hxh(ontem, agora)
    assert inicio2 == "30/07/2026 23:00"
    assert fim2 == "31/07/2026 23:00"


def test_bounds_dia_fechado_exclui_23h_do_proprio_dia():
    inicio, fim = _bounds_janela_operacional_hxh(
        date(2026, 6, 20),
        agora_br=datetime(2026, 6, 21, 10, 0, tzinfo=TZ_BR),
    )
    assert inicio == datetime(2026, 6, 19, 23, 0)
    assert fim == datetime(2026, 6, 20, 23, 0)


def test_bounds_hoje_inclui_ate_agora():
    agora = datetime(2026, 7, 29, 14, 30, tzinfo=TZ_BR)
    inicio, fim = _bounds_janela_operacional_hxh(date(2026, 7, 29), agora_br=agora)
    assert inicio == datetime(2026, 7, 28, 23, 0)
    assert fim == datetime(2026, 7, 29, 14, 31)


@patch("app.bots.bot_production.pd.read_excel")
def test_processar_mantem_23h_dia_anterior(mock_read_excel):
    mock_read_excel.side_effect = FileNotFoundError("sem metas")
    df = pd.DataFrame(
        [
            {
                "Matrícula": "C92928A",
                "Data de Análise": "19/06/2026 23:15:00",
                "Workflow": "WF1",
                "Etapa": "Madrugada",
                "Tempo Total": "00:02:00",
                "Total de Análise": "1",
            },
            {
                "Matrícula": "C92928A",
                "Data de Análise": "19/06/2026 09:00:00",
                "Workflow": "WF1",
                "Etapa": "Cedo",
                "Tempo Total": "00:01:00",
                "Total de Análise": "1",
            },
            {
                "Matrícula": "C92928A",
                "Data de Análise": "20/06/2026 10:00:00",
                "Workflow": "WF1",
                "Etapa": "Dia",
                "Tempo Total": "00:01:00",
                "Total de Análise": "1",
            },
        ]
    )
    out = _processar_dataframe_producao(df, target_date=date(2026, 6, 20))
    assert len(out) == 2
    assert set(out["Data de Análise"].tolist()) == {date(2026, 6, 19), date(2026, 6, 20)}


@patch("app.bots.bot_production.pd.read_excel")
def test_processar_vetorizado_converte_tempo_e_hora(mock_read_excel):
    mock_read_excel.side_effect = FileNotFoundError("sem metas")
    df = pd.DataFrame(
        [
            {
                "Matrícula": "C92928A",
                "Data de Análise": "20/06/2026 14:05:00",
                "Workflow": "WF1",
                "Etapa": "Etapa A",
                "Tempo Total": "00:02:30",
                "Total de Análise": "2",
            },
            {
                "Matrícula": "C92928A",
                "Data de Análise": "20/06/2026 14:40:00",
                "Workflow": "WF1",
                "Etapa": "Etapa A",
                "Tempo Total": "90",
                "Total de Análise": "1",
            },
        ]
    )
    out = _processar_dataframe_producao(df, target_date=date(2026, 6, 20))
    assert len(out) == 1
    assert int(out.iloc[0]["Hora"]) == 14
    assert float(out.iloc[0]["Tempo Total"]) == 240.0  # 150 + 90
    assert float(out.iloc[0]["Total de Análise"]) == 3.0
    assert int(out.iloc[0]["Media_Tempo_por_Analise"]) == 80
