"""Períodos civis BR (virada 03:00) para relatórios Case Manager."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_BR = ZoneInfo("America/Sao_Paulo")

MES_ABREV = {
    1: "jan",
    2: "fev",
    3: "mar",
    4: "abr",
    5: "mai",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "set",
    10: "out",
    11: "nov",
    12: "dez",
}


def calc_dia_civil_br(agora_br: datetime) -> date:
    """Dia civil com virada às 03:00 BR."""
    return (agora_br - timedelta(hours=3)).date()


def calc_periodo_hora(agora_br: datetime | None = None):
    """Janela do relatório por hora: dia civil BR (03:00 BR até 03:00 BR do dia seguinte)."""
    if agora_br is None:
        agora_br = datetime.now(TZ_BR)
    dia = calc_dia_civil_br(agora_br)
    inicio_br = datetime.combine(dia, time(3, 0), tzinfo=TZ_BR)
    fim_br = inicio_br + timedelta(days=1)
    return inicio_br.astimezone(timezone.utc), fim_br.astimezone(timezone.utc)


def calc_periodo_consolidado(agora_br: datetime | None = None, data_fim_dia=None):
    """Janela do consolidado: dia 1 do mês civil 03:00 BR até fim do dia civil BR atual."""
    if agora_br is None:
        agora_br = datetime.now(TZ_BR)
    if data_fim_dia is None:
        _, data_fim_dia = calc_periodo_hora(agora_br)
    dia_civil = calc_dia_civil_br(agora_br)
    inicio_br = datetime(dia_civil.year, dia_civil.month, 1, 3, 0, tzinfo=TZ_BR)
    fim_nome_br = datetime.combine(dia_civil, time(3, 0), tzinfo=TZ_BR)
    data_inicio_cons = inicio_br.astimezone(timezone.utc)
    data_fim_cons = data_fim_dia
    return inicio_br, fim_nome_br, data_inicio_cons, data_fim_cons


def calc_fechamento_mes_anterior(agora_br: datetime | None = None):
    """
    Parâmetros para fechar o mês anterior, ou None fora da janela.
    Executa nos dias civis 1 e 2 do mês.
    """
    if agora_br is None:
        agora_br = datetime.now(TZ_BR)
    dia_civil = calc_dia_civil_br(agora_br)
    if dia_civil.day > 2:
        return None

    primeiro_mes_atual = dia_civil.replace(day=1)
    ultimo_dia_mes_anterior = primeiro_mes_atual - timedelta(days=1)

    inicio_br = datetime(
        ultimo_dia_mes_anterior.year,
        ultimo_dia_mes_anterior.month,
        1,
        3,
        0,
        tzinfo=TZ_BR,
    )
    fim_br = datetime(
        primeiro_mes_atual.year,
        primeiro_mes_atual.month,
        1,
        3,
        0,
        tzinfo=TZ_BR,
    )
    pasta_mes = f"{MES_ABREV[inicio_br.month]}-{inicio_br.year}"
    fim_nome_br = datetime.combine(ultimo_dia_mes_anterior, time(3, 0), tzinfo=TZ_BR)
    return (
        inicio_br,
        fim_nome_br,
        inicio_br.astimezone(timezone.utc),
        fim_br.astimezone(timezone.utc),
        pasta_mes,
    )


def nome_arquivo_consolidado(inicio_br: datetime, fim_br: datetime) -> str:
    mes_nome = MES_ABREV[inicio_br.month]
    return (
        f"Relatorio_Produtividade_Consolidado_{inicio_br.strftime('%d')}-"
        f"{fim_br.strftime('%d')}{mes_nome}.xlsx"
    )


def pasta_mes_nome(dia_civil: date) -> str:
    return f"{MES_ABREV[dia_civil.month]}-{dia_civil.year}"
