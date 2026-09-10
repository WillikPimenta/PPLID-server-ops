# -*- coding: utf-8 -*-
"""Gera HTML executivo comparativo a partir do banco do portal."""
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from report_falhas.config_report import COL_CENARIO, COL_DATA
from report_falhas.executive_report import _build_html, _kpis_bu, _kpis_tempo_casa
from report_falhas.executive_report import _filtrar_hc_por_localidades as _filtrar_hc_exec
from report_falhas.filters import LOCALIDADES_ALVO, aplicar_recorte_oficial, filtrar_por_localidades, uses_dual_metric_mode
from report_falhas.team_category import CATEGORIA_NAO_CLASSIFICADO, map_team_to_categoria

from django.conf import settings

from apps.falhas_criticas.models import FalhasAgent, ExecutiveReportArchive, Failure
from apps.falhas_criticas.services.dataframes import failures_qs_to_legacy_df


def _agents_to_hc_df() -> pd.DataFrame:
    rows = list(FalhasAgent.objects.values(
        'matricula_norm', 'name', 'localidade', 'tempo_casa', 'admissao_date', 'team',
    ))
    if not rows:
        return pd.DataFrame(columns=['matricula_agente', 'localidade', 'data_admissao', 'team'])
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        'matricula_norm': 'matricula_agente',
        'admissao_date': 'data_admissao',
    })
    return df


def _team_categoria_map_from_agents() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in FalhasAgent.objects.values('matricula_norm', 'team', 'team_categoria'):
        mat = row['matricula_norm']
        cat = safe_str(row.get('team_categoria', ''))
        if not cat:
            cat = map_team_to_categoria(row.get('team', ''))
        out[mat] = cat or CATEGORIA_NAO_CLASSIFICADO
    return out


def safe_str(x) -> str:
    return '' if x is None else str(x).strip()


def _parse_period(params) -> tuple[date, date]:
    start_s = (params or {}).get('start_date')
    end_s = (params or {}).get('end_date')
    if start_s and end_s:
        return (
            datetime.strptime(str(start_s)[:10], '%Y-%m-%d').date(),
            datetime.strptime(str(end_s)[:10], '%Y-%m-%d').date(),
        )
    today = date.today()
    return today.replace(day=1), today


def build_executive_html(params) -> str:
    """Retorna HTML do comparativo executivo BSB × SC."""
    df_base = failures_qs_to_legacy_df(Failure.objects.select_related('agent').all())
    df_hc = _agents_to_hc_df()
    cur_start, cur_end = _parse_period(params)
    team_map = _team_categoria_map_from_agents()

    nomes = list(LOCALIDADES_ALVO.keys())
    if len(nomes) < 2:
        raise ValueError('Relatório executivo requer duas praças configuradas.')

    nome_a, nome_b = nomes[0], nomes[1]
    df_a = filtrar_por_localidades(df_base, LOCALIDADES_ALVO[nome_a])
    df_b = filtrar_por_localidades(df_base, LOCALIDADES_ALVO[nome_b])
    df_a_of = aplicar_recorte_oficial(df_a, cur_start)
    df_b_of = aplicar_recorte_oficial(df_b, cur_start)
    dual_metric_mode = uses_dual_metric_mode(cur_start)

    df_hc_a = _filtrar_hc_por_localidades(df_hc, LOCALIDADES_ALVO[nome_a])
    df_hc_b = _filtrar_hc_por_localidades(df_hc, LOCALIDADES_ALVO[nome_b])

    ka = _kpis_bu(
        df_a, df_a_of, cur_start, cur_end, COL_DATA, COL_CENARIO, team_map
    )
    kb = _kpis_bu(
        df_b, df_b_of, cur_start, cur_end, COL_DATA, COL_CENARIO, team_map
    )
    ka['tempo_casa'] = _kpis_tempo_casa(df_a_of, df_hc_a, cur_start, cur_end, COL_DATA)
    kb['tempo_casa'] = _kpis_tempo_casa(df_b_of, df_hc_b, cur_start, cur_end, COL_DATA)

    return _build_html(nome_a, ka, nome_b, kb, cur_start, cur_end, dual_metric_mode=dual_metric_mode)


def _archive_dir() -> Path:
    base = getattr(settings, 'MEDIA_ROOT', None)
    if not base:
        base = Path(settings.BASE_DIR) / 'media'
    out = Path(base) / 'executive_reports'
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_executive_report(user, params) -> tuple[ExecutiveReportArchive, Path]:
    """Gera HTML, salva em disco e registra no histórico."""
    cur_start, cur_end = _parse_period(params)
    html = build_executive_html(params)
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M')
    fname = f'relatorio_EXECUTIVO_BU_{ts}.html'
    out_path = _archive_dir() / fname
    out_path.write_text(html, encoding='utf-8')

    archive = ExecutiveReportArchive.objects.create(
        period_start=cur_start,
        period_end=cur_end,
        generated_by=user if user and user.is_authenticated else None,
        file_path=str(out_path),
    )
    return archive, out_path
