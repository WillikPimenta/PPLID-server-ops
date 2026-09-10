# -*- coding: utf-8 -*-

"""Testes do filtro de previsões de treinamentos (vencidos e previsto do mês)."""

from datetime import date



import pandas as pd



from report_falhas.io.data_loader import normalize_text, norm_matricula, safe_str

from report_falhas.html_pages import _build_mats_ativos_hc

from report_falhas.periods import first_day, month_last_day, next_calendar_month_range





def test_next_calendar_month_range():

    start, end = next_calendar_month_range(date(2026, 6, 2))

    assert start == date(2026, 7, 1)

    assert end == date(2026, 7, 31)





def _split_previstos_logic(df, ref_date, hoje, mats_ativos=None):

    if mats_ativos is not None:

        df = df[df['mat_norm'].isin(mats_ativos)].copy()

    mes_start = first_day(ref_date)

    mes_end = month_last_day(ref_date)

    status_norm = df['Status'].apply(safe_str).apply(normalize_text)

    dl = pd.to_datetime(df['Event:EventDeadline'], errors='coerce')

    base = status_norm == 'previsto'

    no_mes = (

        base & dl.notna()

        & (dl.dt.date >= mes_start) & (dl.dt.date <= mes_end)

    )

    mask_venc = no_mes & (dl.dt.date < hoje)

    mask_mes = no_mes & (dl.dt.date >= hoje)

    return df.loc[mask_venc], df.loc[mask_mes]





def test_previsto_vencido_e_mes():

    ref = date(2026, 6, 30)

    hoje = date(2026, 6, 3)

    df = pd.DataFrame([

        {

            'Event': 'E-old',

            'mat_norm': 'a1',

            'Status': 'previsto',

            'Event:EventDeadline': pd.Timestamp(2025, 1, 10),

        },

        {

            'Event': 'E-jun-venc',

            'mat_norm': 'a1',

            'Status': 'previsto',

            'Event:EventDeadline': pd.Timestamp(2026, 6, 2),

        },

        {

            'Event': 'E-jun-futuro',

            'mat_norm': 'a1',

            'Status': 'previsto',

            'Event:EventDeadline': pd.Timestamp(2026, 6, 10),

        },

        {

            'Event': 'E-jun-ok',

            'mat_norm': 'a2',

            'Status': 'previsto',

            'Event:EventDeadline': pd.Timestamp(2026, 6, 30),

        },

        {

            'Event': 'E-jul',

            'mat_norm': 'a2',

            'Status': 'previsto',

            'Event:EventDeadline': pd.Timestamp(2026, 7, 5),

        },

    ])

    df_venc, df_mes = _split_previstos_logic(df, ref, hoje)

    assert set(df_venc['Event'].tolist()) == {'E-jun-venc'}

    assert set(df_mes['Event'].tolist()) == {'E-jun-futuro', 'E-jun-ok'}

    assert 'E-old' not in df_venc['Event'].tolist()

    assert 'E-jul' not in df_mes['Event'].tolist()





def test_mats_ativos_hc_vinculo_aberto():

    hc = pd.DataFrame([

        {'matricula_agente': '100', 'data_inicial': pd.Timestamp(2024, 1, 1), 'data_final': pd.NaT, 'id': 1},

        {'matricula_agente': '100', 'data_inicial': pd.Timestamp(2025, 6, 1), 'data_final': pd.Timestamp(2025, 6, 30), 'id': 2},

        {'matricula_agente': '200', 'data_inicial': pd.Timestamp(2024, 1, 1), 'data_final': pd.Timestamp(2025, 1, 1), 'id': 3},

    ])

    ativos = _build_mats_ativos_hc(hc)

    assert norm_matricula('100') in ativos

    assert norm_matricula('200') not in ativos


