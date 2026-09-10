# -*- coding: utf-8 -*-
"""Exportação CSV/XLSX com filtros do portal."""
import io

import pandas as pd

from apps.falhas_criticas.services.dataframes import apply_failure_filters, failures_qs_to_legacy_df
from apps.falhas_criticas.models import Failure


def export_failures_csv(user, query_params) -> bytes:
    from apps.falhas_criticas.scoping import scoped_query_params

    params, _ = scoped_query_params(user, query_params)
    qs = Failure.objects.select_related('agent')
    df = failures_qs_to_legacy_df(apply_failure_filters(qs, params))
    if df.empty:
        df = pd.DataFrame(columns=['Protocolo', 'Data de Análise', 'Localidade'])
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding='utf-8-sig')
    return buf.getvalue().encode('utf-8-sig')


def export_reincidencia_xlsx(user, query_params) -> bytes:
    from report_falhas.reincidence import reincidencia_table_full

    from apps.falhas_criticas.services.dataframes import load_period_frames

    df_all, df_cur, df_prev, params, _ = load_period_frames(user, query_params)
    df_reinc = reincidencia_table_full(df_prev, df_cur)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        if df_reinc is not None and not df_reinc.empty:
            df_reinc.to_excel(writer, sheet_name='Reincidência', index=False)
        else:
            pd.DataFrame(columns=['matricula', 'ocorrencias', 'reincidente']).to_excel(
                writer, sheet_name='Reincidência', index=False,
            )
        meta = pd.DataFrame([{
            'periodo_inicio': params.get('start_date', ''),
            'periodo_fim': params.get('end_date', ''),
            'localidade': params.get('localidade', 'Geral'),
        }])
        meta.to_excel(writer, sheet_name='Filtros', index=False)
    buf.seek(0)
    return buf.read()
