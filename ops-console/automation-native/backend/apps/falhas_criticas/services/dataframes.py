# -*- coding: utf-8 -*-
"""Conversão QuerySet → DataFrame com colunas do report legado."""
import pandas as pd

from datetime import datetime

from report_falhas.filters import MODULOS_METRICA_OFICIAL, uses_dual_metric_mode
from apps.falhas_criticas.models import Failure, Support
from apps.falhas_criticas.services.localidade import filter_qs_by_localidade


def _param_str(params, key, default=''):
    val = (params or {}).get(key, default)
    if isinstance(val, (list, tuple)):
        val = val[0] if val else default
    return default if val is None else val


def failures_qs_to_legacy_df(qs):
    vals = qs.values(
        'protocolo', 'data_analise', 'data_auditoria', 'etapa', 'categoria',
        'cenario', 'novo_cenario', 'localidade', 'cliente', 'workflow',
        'dificuldade', 'tipo_documento', 'uf_documento', 'tipo_falha', 'modulo',
        'agent_id', 'agent__name', 'agent__turno_atual', 'agent__tempo_casa',
        'agent__tempo_etapa', 'agent__atividade_atual', 'agent__localidade',
    )
    rows = list(vals)
    if not rows:
        return _empty_failures_df()

    df = pd.DataFrame(rows)
    df = df.rename(columns={
        'data_analise': 'Data de Análise',
        'data_auditoria': 'Data Auditoria',
        'protocolo': 'Protocolo',
        'etapa': 'Etapa',
        'categoria': 'Categoria falha',
        'cenario': 'Cenário Unificado',
        'novo_cenario': 'Novo cenário',
        'localidade': 'Localidade',
        'cliente': 'Cliente',
        'workflow': 'Workflow',
        'dificuldade': 'Nível de Dificuldade',
        'tipo_documento': 'Tipo de documento',
        'uf_documento': 'UF do documento',
        'tipo_falha': 'Tipo de Falha',
        'modulo': 'Módulo',
        'agent_id': 'Matrícula Agente',
        'agent__name': 'Nome Agente',
        'agent__turno_atual': 'Turno',
        'agent__tempo_casa': 'Tempo de casa',
        'agent__tempo_etapa': 'Tempo de etapa',
        'agent__atividade_atual': 'Atividade HC',
        'agent__localidade': 'Localidade Agente',
    })
    df['Data de Análise'] = pd.to_datetime(df['Data de Análise'], errors='coerce')
    if 'Data Auditoria' in df.columns:
        df['Data Auditoria'] = pd.to_datetime(df['Data Auditoria'], errors='coerce')
    return df


def _empty_failures_df():
    cols = [
        'Protocolo', 'Data de Análise', 'Data Auditoria', 'Etapa', 'Categoria falha',
        'Cenário Unificado', 'Novo cenário', 'Localidade', 'Cliente', 'Workflow',
        'Nível de Dificuldade', 'Tipo de documento', 'UF do documento', 'Tipo de Falha',
        'Módulo', 'Matrícula Agente', 'Nome Agente', 'Turno',
    ]
    return pd.DataFrame(columns=cols)


def supports_qs_to_legacy_df(qs):
    vals = qs.values(
        'protocolo', 'data', 'cliente', 'workflow', 'tipo_solicitacao',
        'conformidade', 'conformidade_regra', 'dificuldade', 'critico',
        'localidade', 'duvida', 'conclusao', 'agente_nome_planilha',
        'lider_solicitante', 'tipo_documento',
        'agent__matricula_norm', 'agent__name', 'agent__turno_atual',
    )
    rows = list(vals)
    if not rows:
        return pd.DataFrame(columns=[
            'Protocolo', 'Data', 'Cliente', 'Workflow', 'Tipo de solicitação',
            'Conformidade', 'Conformidade (Regra)', 'Grau de dificuldade', 'Crítico',
            'Localidade solicitante', 'Dúvida', 'Conclusão', 'Agente nome planilha',
            'Agente', 'Nome Agente', 'Turno',
        ])
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        'data': 'Data',
        'protocolo': 'Protocolo',
        'cliente': 'Cliente',
        'workflow': 'Workflow',
        'tipo_solicitacao': 'Tipo de solicitação',
        'conformidade': 'Conformidade',
        'conformidade_regra': 'Conformidade (Regra)',
        'dificuldade': 'Grau de dificuldade',
        'critico': 'Crítico',
        'localidade': 'Localidade solicitante',
        'duvida': 'Dúvida',
        'conclusao': 'Conclusão',
        'agente_nome_planilha': 'Agente nome planilha',
        'agent__matricula_norm': 'Agente',
        'agent__name': 'Nome Agente',
        'agent__turno_atual': 'Turno',
    })
    df['Data'] = pd.to_datetime(df['Data'], errors='coerce')
    return df


def _apply_matricula_team(qs, params, field='agent__matricula_norm'):
    team_mats = params.get('team_mats') or []
    matricula = _param_str(params, 'matricula')
    if matricula == '__BLOCKED__':
        return qs.none()
    if team_mats:
        return qs.filter(**{f'{field}__in': team_mats})
    if matricula:
        return qs.filter(**{f'{field}__iexact': matricula})
    return qs


def apply_failure_filters(qs, params):
    localidade = _param_str(params, 'localidade')
    turno = _param_str(params, 'turno')
    start_date = _param_str(params, 'start_date')
    end_date = _param_str(params, 'end_date')
    oficial_only = _param_str(params, 'oficial') == 'true'

    if localidade == '__BLOCKED__':
        return qs.none()
    qs = filter_qs_by_localidade(qs, 'localidade', localidade)
    if turno:
        qs = qs.filter(agent__turno_atual__iexact=turno)
    qs = _apply_matricula_team(qs, params)
    if start_date:
        qs = qs.filter(data_analise__gte=start_date)
    if end_date:
        qs = qs.filter(data_analise__lte=end_date)
    if oficial_only:
        ref_start = None
        if start_date:
            try:
                ref_start = datetime.strptime(str(start_date)[:10], '%Y-%m-%d').date()
            except ValueError:
                ref_start = None
        if ref_start is None:
            from datetime import date
            ref_start = date.today().replace(day=1)
        if uses_dual_metric_mode(ref_start):
            qs = qs.filter(modulo__in=MODULOS_METRICA_OFICIAL)
    return qs


def apply_support_filters(qs, params):
    localidade = _param_str(params, 'localidade')
    turno = _param_str(params, 'turno')
    start_date = _param_str(params, 'start_date')
    end_date = _param_str(params, 'end_date')

    if localidade == '__BLOCKED__':
        return qs.none()
    qs = filter_qs_by_localidade(qs, 'localidade', localidade)
    if turno:
        qs = qs.filter(agent__turno_atual__iexact=turno)
    qs = _apply_matricula_team(qs, params)
    if start_date:
        qs = qs.filter(data__gte=start_date)
    if end_date:
        qs = qs.filter(data__lte=end_date)
    return qs


def load_period_frames(user, query_params):
    """Carrega df_all, df_cur e df_prev com escopo de localidade aplicado."""
    from datetime import datetime

    from report_falhas.periods import filter_by_date_range

    from apps.falhas_criticas.models import Failure
    from apps.falhas_criticas.scoping import scoped_query_params

    params, scope = scoped_query_params(user, query_params)
    qs = Failure.objects.select_related('agent')

    scope_only = {k: v for k, v in params.items() if k not in ('start_date', 'end_date')}

    df_all = failures_qs_to_legacy_df(apply_failure_filters(qs, scope_only))
    df_cur = failures_qs_to_legacy_df(apply_failure_filters(qs, params))

    df_prev = _empty_failures_df()
    start_s = params.get('start_date')
    end_s = params.get('end_date')
    if start_s and end_s and not df_all.empty:
        cur_start = datetime.strptime(start_s, '%Y-%m-%d').date()
        cur_end = datetime.strptime(end_s, '%Y-%m-%d').date()
        prev_eq_start, prev_eq_end, _mode = resolve_portal_comparativo_window(cur_start, cur_end)
        if prev_eq_start and prev_eq_end:
            df_prev = filter_by_date_range(df_all, prev_eq_start, prev_eq_end, 'Data de Análise')

    return df_all, df_cur, df_prev, params, scope


def resolve_portal_comparativo_window(cur_start, cur_end):
    """Janela anterior para o portal.

    - MTD clássico (mesmo mês, início dia 1): mesmos N dias no mês anterior.
    - Recorte customizado / multi-mês: N dias corridos imediatamente anteriores.
    """
    from datetime import timedelta

    from report_falhas.periods import prev_month_first_day

    if not cur_start or not cur_end or cur_end < cur_start:
        return None, None, 'invalid'
    n_days = (cur_end - cur_start).days + 1
    is_mtd = (
        cur_start.day == 1
        and cur_start.month == cur_end.month
        and cur_start.year == cur_end.year
    )
    if is_mtd:
        prev_start = prev_month_first_day(cur_end)
        prev_end = prev_start + timedelta(days=n_days - 1)
        return prev_start, prev_end, 'mtd'
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=n_days - 1)
    return prev_start, prev_end, 'preceding'
