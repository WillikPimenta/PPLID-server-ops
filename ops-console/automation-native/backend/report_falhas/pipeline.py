# report_falhas/pipeline.py
# -*- coding: utf-8 -*-

"""
Pipeline do Relatório de Falhas Críticas.

Responsabilidade:
- Orquestrar a execução do relatório
- Chamar períodos, reincidência, KPIs, gráficos e HTML
- Manter o main() limpo e legível

Não:
- Não faz regra pesada
- Não conhece detalhes de HTML
- Não conhece detalhes de gráficos
"""

from typing import Optional

import pandas as pd

from report_falhas.config_report import COL_DATA_ANALISE
from report_falhas.contestacoes import _apply_contestacoes_to_base
from report_falhas.models import ReportContext
from report_falhas.insights import gerar_insights, trend_text
from report_falhas.excel_exporter import export_reincidencia_xlsx
from report_falhas.periods import (
    first_day,
    prev_month_first_day,
    month_last_day,
    get_comparativo_by_same_period,
)
from report_falhas.reincidence import (
    reincidencia_table_full,
    novos_no_mes,
)
from report_falhas.kpis import make_kpis
from report_falhas.charts import build_charts_bundle
from report_falhas.html_pages import format_html


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

def build_report(
    df_base: pd.DataFrame,
    *,
    col_data: str = COL_DATA_ANALISE,
    today,
    pares_mes: Optional[list] = None,
    pares_diario: Optional[list] = None,
    pares_facil: Optional[list] = None,
    top3_cenarios: Optional[list] = None,
    df_ci: Optional[pd.DataFrame] = None,
    df_ce: Optional[pd.DataFrame] = None,
    aplicar_contestacoes: bool = False,
):
    """
    Executa o pipeline completo do relatório.

    Parâmetros:
    - df_base: DataFrame já tratado (datas OK, filtros OK)
    - col_data: nome da coluna de data principal
    - today: data de referência (date)
    """

    if aplicar_contestacoes:
        df_base, _, _ = _apply_contestacoes_to_base(
            df_base,
            df_ci if df_ci is not None else pd.DataFrame(),
            df_ce if df_ce is not None else pd.DataFrame(),
        )

    # ===========================
    # 1. CONTEXTO DE DATAS
    # ===========================
    cur_start = first_day(today)
    prev_start = prev_month_first_day(today)
    cur_end = min(today, month_last_day(cur_start))

    # ===========================
    # 2. COMPARATIVO EQUIVALENTE
    # ===========================
    total_atual, total_prev_equal, prev_equal_start, prev_equal_end = (
        get_comparativo_by_same_period(
            df_base,
            cur_start,
            cur_end,
            prev_start,
            col_data,
        )
    )

    # ===========================
    # 3. RECORTE DE DADOS
    # ===========================
    df_cur = df_base[
        (df_base[col_data].dt.date >= cur_start) &
        (df_base[col_data].dt.date <= cur_end)
    ].copy()

    df_prev_equal = df_base[
        (df_base[col_data].dt.date >= (prev_equal_start or prev_start)) &
        (df_base[col_data].dt.date <= (prev_equal_end or prev_start))
    ].copy()

    # ===========================
    # 4. REINCIDÊNCIA
    # ===========================
    df_reinc = reincidencia_table_full(df_prev_equal, df_cur)
    novos_mes = novos_no_mes(df_prev_equal, df_cur)

    # ===========================
    # 5. CONTEXTO CONSOLIDADO
    # ===========================
    context = ReportContext(
        today=today,
        cur_start=cur_start,
        cur_end=cur_end,
        prev_equal_start=prev_equal_start,
        prev_equal_end=prev_equal_end,
        total_atual=total_atual,
        total_prev_equal=total_prev_equal,
        pares_mes=pares_mes,
        pares_diario=pares_diario,
        pares_facil=pares_facil,
        top3_cenarios=top3_cenarios,
    )

    # ===========================
    # 6. KPIs
    # ===========================
    kpis = make_kpis(
        cur_start=context.cur_start,
        cur_end_mtd=context.cur_end,
        total_atual=context.total_atual,
        total_prev_equal=context.total_prev_equal,
        prev_equal_start=context.prev_equal_start,
        prev_equal_end=context.prev_equal_end,
        df_reinc_full=df_reinc,
        top3_cenarios=context.top3_cenarios,
        novos_mes_list=novos_mes,
    )

    # ===========================
    # 7. GRÁFICOS
    # ===========================
    charts = build_charts_bundle(df_cur, context)

    # ===========================
    # 8. HTML FINAL
    # ===========================
    html = format_html(
        kpis=kpis,
        reincidencia_df=df_reinc,
        charts=charts,
        context=context,
    )

    # ===========================
    # 9. SAÍDA PADRONIZADA
    # ===========================
    return {
        "html": html,
        "kpis": kpis,
        "reincidencia_df": df_reinc,
        "charts": charts,
        "context": context,
    }
