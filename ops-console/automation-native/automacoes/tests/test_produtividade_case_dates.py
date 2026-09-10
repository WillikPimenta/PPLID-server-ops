"""Testes unitários de períodos civis BR (produtividade_case)."""

from datetime import datetime

from app.bots.produtividade_case.dates import (
    TZ_BR,
    calc_dia_civil_br,
    calc_fechamento_mes_anterior,
    calc_periodo_consolidado,
    calc_periodo_hora,
    nome_arquivo_consolidado,
)


def test_calc_dia_civil_br_antes_das_3h():
    agora = datetime(2026, 5, 15, 2, 30, tzinfo=TZ_BR)
    assert calc_dia_civil_br(agora).isoformat() == "2026-05-14"


def test_calc_dia_civil_br_depois_das_3h():
    agora = datetime(2026, 5, 15, 3, 0, tzinfo=TZ_BR)
    assert calc_dia_civil_br(agora).isoformat() == "2026-05-15"


def test_calc_periodo_hora_janela():
    agora = datetime(2026, 5, 15, 14, 0, tzinfo=TZ_BR)
    inicio, fim = calc_periodo_hora(agora)
    assert inicio < fim
    # 03:00 BR do dia civil = 06:00 UTC (BRT = UTC-3)
    assert inicio.hour == 6
    assert (fim - inicio).total_seconds() == 24 * 3600


def test_calc_periodo_consolidado_inicio_mes():
    agora = datetime(2026, 5, 15, 14, 0, tzinfo=TZ_BR)
    inicio_br, fim_nome_br, data_inicio_cons, data_fim_cons = calc_periodo_consolidado(agora)
    assert inicio_br.day == 1
    assert inicio_br.month == 5
    assert fim_nome_br.day == 15
    assert data_inicio_cons < data_fim_cons


def test_fechamento_so_dias_1_e_2():
    dia3 = datetime(2026, 5, 3, 10, 0, tzinfo=TZ_BR)
    assert calc_fechamento_mes_anterior(dia3) is None

    dia1 = datetime(2026, 5, 1, 10, 0, tzinfo=TZ_BR)
    fech = calc_fechamento_mes_anterior(dia1)
    assert fech is not None
    ini_br, _fim_nome, ini_utc, fim_utc, pasta = fech
    assert ini_br.month == 4
    assert pasta.startswith("abr-")
    assert ini_utc < fim_utc


def test_nome_arquivo_consolidado():
    agora = datetime(2026, 5, 15, 14, 0, tzinfo=TZ_BR)
    inicio_br, fim_nome_br, _, _ = calc_periodo_consolidado(agora)
    nome = nome_arquivo_consolidado(inicio_br, fim_nome_br)
    assert nome == "Relatorio_Produtividade_Consolidado_01-15mai.xlsx"
