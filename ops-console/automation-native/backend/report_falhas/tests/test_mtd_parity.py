# -*- coding: utf-8 -*-
"""Paridade MTD: html_pages usa periods.get_comparativo_by_same_period_on (canônico)."""
from datetime import date

import pandas as pd

from report_falhas.periods import get_comparativo_by_same_period_on as periods_mtd


def _df_sup(dates):
    return pd.DataFrame({'Data': pd.to_datetime(dates)})


def test_mtd_canonical_same_calendar_days():
    cur_start = date(2026, 6, 1)
    cur_end = date(2026, 6, 9)
    prev_start = date(2026, 5, 1)
    df = _df_sup(['2026-06-05', '2026-06-08', '2026-05-03', '2026-05-07'])
    atual, prev, p_ini, p_fim = periods_mtd(df, cur_start, cur_end, prev_start, 'Data')
    assert atual == 2
    assert prev == 2
    assert p_ini == date(2026, 5, 1)
    assert p_fim == date(2026, 5, 9)


def test_html_pages_imports_periods_mtd():
    from report_falhas.html_pages import get_comparativo_by_same_period_on
    assert get_comparativo_by_same_period_on is periods_mtd
