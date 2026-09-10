# -*- coding: utf-8 -*-
"""Testes do comparativo MTD (mesmos dias corridos no mês anterior)."""
from datetime import date

import pandas as pd

from report_falhas.executive_report import _abertura_volume_bu
from report_falhas.periods import (
    audit_grace_end,
    dias_corridos_periodo,
    effective_audit_end,
    first_day,
    get_comparativo_by_same_period,
    is_fechamento_mes,
    mask_spill_auditoria,
    month_last_day,
    nth_weekday_of_month,
    prev_month_first_day,
    resolve_mtd_period,
)


def _df_falhas(dates):
    return pd.DataFrame({
        'Data de Análise': pd.to_datetime(dates),
    })


def test_resolve_mtd_period_mes_atual_vazio_bot():
    """Bot com mes_referencia vazio: MTD 01/06 a hoje (não 01/06 a 01/06)."""
    ref = date(2026, 6, 30)
    cur_start, cur_end = resolve_mtd_period(ref, today=date(2026, 6, 30))
    assert cur_start == date(2026, 6, 1)
    assert cur_end == date(2026, 6, 30)


def test_resolve_mtd_period_mes_referencia_primeiro_dia():
    """MM/AAAA (dia 1) no mês corrente: MTD até hoje."""
    ref = date(2026, 6, 1)
    cur_start, cur_end = resolve_mtd_period(ref, today=date(2026, 6, 30))
    assert cur_start == date(2026, 6, 1)
    assert cur_end == date(2026, 6, 30)


def test_resolve_mtd_period_mes_anterior_cheio():
    ref = date(2026, 5, 1)
    cur_start, cur_end = resolve_mtd_period(ref, today=date(2026, 6, 30))
    assert cur_start == date(2026, 5, 1)
    assert cur_end == date(2026, 5, 31)


def test_comparativo_mtd_parcial_09_junho():
    hoje = date(2026, 6, 9)
    cur_start = first_day(hoje)
    cur_end = min(hoje, month_last_day(cur_start))
    prev_start = prev_month_first_day(cur_start)

    df = _df_falhas([
        '2026-06-05', '2026-06-08',
        '2026-05-03', '2026-05-07', '2026-05-20',
    ])
    atual, prev, prev_eq_start, prev_eq_end = get_comparativo_by_same_period(
        df, cur_start, cur_end, prev_start, 'Data de Análise',
    )

    assert cur_start == date(2026, 6, 1)
    assert cur_end == date(2026, 6, 9)
    assert atual == 2
    assert prev == 2
    assert prev_eq_start == date(2026, 5, 1)
    assert prev_eq_end == date(2026, 5, 9)
    assert dias_corridos_periodo(cur_start, cur_end) == 9


def test_comparativo_mtd_fechamento_mes():
    hoje = date(2026, 6, 30)
    cur_start = first_day(hoje)
    cur_end = min(hoje, month_last_day(cur_start))
    prev_start = prev_month_first_day(cur_start)

    df = _df_falhas(['2026-06-15', '2026-05-10', '2026-05-25'])
    atual, prev, prev_eq_start, prev_eq_end = get_comparativo_by_same_period(
        df, cur_start, cur_end, prev_start, 'Data de Análise',
    )

    assert cur_end == date(2026, 6, 30)
    assert prev_eq_start == date(2026, 5, 1)
    assert prev_eq_end == date(2026, 5, 30)
    assert dias_corridos_periodo(cur_start, cur_end) == 30
    assert atual == 1
    assert prev == 2


def test_abertura_narrativa_parcial_vs_fechado():
    parcial = _abertura_volume_bu('São Carlos', 7, date(2026, 6, 1), date(2026, 6, 9))
    fechado = _abertura_volume_bu('Brasília', 4, date(2026, 6, 1), date(2026, 6, 30))

    assert 'acumula' in parcial
    assert '01/06/2026 a 09/06/2026' in parcial
    assert 'encerrou o período' in fechado
    assert '01/06/2026 a 30/06/2026' in fechado


def test_nth_weekday_quinto_util_julho_2026():
    assert nth_weekday_of_month(2026, 7, 5) == date(2026, 7, 7)


def test_audit_grace_end_junho_2026():
    assert audit_grace_end(date(2026, 6, 30)) == date(2026, 7, 7)


def test_is_fechamento_mes_referencia_anterior():
    ref = date(2026, 6, 1)
    cur_start = date(2026, 6, 1)
    cur_end = date(2026, 6, 30)
    today = date(2026, 7, 2)
    assert is_fechamento_mes(ref, cur_start, cur_end, today) is True


def test_is_fechamento_mes_mtd_corrente_nao_fechamento():
    ref = date(2026, 7, 2)
    cur_start = date(2026, 7, 1)
    cur_end = date(2026, 7, 2)
    today = date(2026, 7, 2)
    assert is_fechamento_mes(first_day(ref), cur_start, cur_end, today) is False


def test_effective_audit_end_fechamento_vs_mtd():
    ref_jun = date(2026, 6, 1)
    cur_start = date(2026, 6, 1)
    cur_end = date(2026, 6, 30)
    assert effective_audit_end(cur_start, cur_end, ref_jun, date(2026, 7, 2)) == date(2026, 7, 7)

    ref_jul = date(2026, 7, 1)
    cur_start_jul = date(2026, 7, 1)
    cur_end_jul = date(2026, 7, 2)
    assert effective_audit_end(cur_start_jul, cur_end_jul, ref_jul, date(2026, 7, 2)) == date(2026, 7, 2)


def _df_analise_audit(analise_dates, audit_dates):
    return pd.DataFrame({
        'Data de Análise': pd.to_datetime(analise_dates),
        'Data Auditoria': pd.to_datetime(audit_dates),
    })


def test_spill_fechamento_junho_grace_02_jul():
    df = _df_analise_audit(['2026-06-30'], ['2026-07-02'])
    cur_start = date(2026, 6, 1)
    cur_end = date(2026, 6, 30)
    eff = date(2026, 7, 7)
    mask = mask_spill_auditoria(df, 'Data Auditoria', cur_start, cur_end, eff)
    assert mask.sum() == 0


def test_spill_fechamento_junho_auditoria_10_jul():
    df = _df_analise_audit(['2026-06-30'], ['2026-07-10'])
    cur_start = date(2026, 6, 1)
    cur_end = date(2026, 6, 30)
    eff = date(2026, 7, 7)
    mask = mask_spill_auditoria(df, 'Data Auditoria', cur_start, cur_end, eff)
    assert mask.sum() == 1


def test_analise_julho_nao_entra_filtro_junho():
    df = _df_analise_audit(['2026-07-07'], ['2026-07-02'])
    from report_falhas.periods import filter_by_date_range_on
    jun = filter_by_date_range_on(df, date(2026, 6, 1), date(2026, 6, 30), 'Data de Análise')
    assert len(jun) == 0
