# -*- coding: utf-8 -*-
"""Bridge Capacitação × Reincidência/Falhas."""
from __future__ import annotations

from collections import Counter, defaultdict

from report_falhas.io.data_loader import norm_matricula, safe_str
from report_falhas.reincidence import reincidencia_table_full

from apps.falhas_criticas.models import Failure, FalhasAgent
from apps.falhas_criticas.services.analytics import build_ult3m_payload
from apps.falhas_criticas.services.dataframes import (
    apply_failure_filters,
    failures_qs_to_legacy_df,
    load_period_frames,
)
from apps.falhas_criticas.services.narrative import build_training_reincidence_bridge_narrative


def _empty_bridge():
    empty_summary = {
        'agentes_criticos_com_pendencia': 0,
        'reincidentes_com_vencidos': 0,
        'alta_frequencia_com_pendencia': 0,
        'lideres_com_risco': 0,
        'maior_risco': '',
    }
    return {
        'agentes_criticos': [],
        'lideres': [],
        'cenarios': [],
        'summary': empty_summary,
        'pre_diagnostico': build_training_reincidence_bridge_narrative(empty_summary, [], []),
    }


def _training_agent_stats(qs, ref_date=None) -> dict[str, dict]:
    from datetime import date as _date
    from apps.falhas_criticas.services.training_analytics import effective_training_situacao

    ref_date = ref_date or _date.today()
    agents: dict[str, dict] = {}
    for row in qs.values(
        'matricula', 'nome_agente', 'status', 'situacao_calculada',
        'data_assinatura', 'data_limite',
    ):
        mat = norm_matricula(row.get('matricula') or '')
        if not mat:
            continue
        if mat not in agents:
            agents[mat] = {
                'matricula': mat,
                'nome_agente': safe_str(row.get('nome_agente')),
                'treinamentos_previstos': 0,
                'treinamentos_vencidos': 0,
                'treinamentos_a_vencer_7d': 0,
                'treinamentos_assinados': 0,
                'previstos_pendentes': 0,
            }
        ag = agents[mat]
        if not ag['nome_agente']:
            ag['nome_agente'] = safe_str(row.get('nome_agente'))
        sit = effective_training_situacao(row, ref_date)
        st = safe_str(row.get('status')).lower()
        if sit == 'Finalizado':
            ag['treinamentos_assinados'] += 1
        elif sit == 'Vencido':
            ag['treinamentos_vencidos'] += 1
        elif sit == 'A vencer':
            ag['treinamentos_a_vencer_7d'] += 1
        else:
            # Aberto (prazo ok): assinatura pendente ou treinamento não concluído
            if st == 'previsto' or sit == 'Treinamento não concluído':
                ag['treinamentos_previstos'] += 1
                ag['previstos_pendentes'] += 1
            elif st == 'ministrado' or sit == 'Pendente de assinatura do agente':
                ag['previstos_pendentes'] += 1
    return agents


def _has_pendencia(stats: dict) -> bool:
    return (
        stats.get('treinamentos_vencidos', 0) > 0
        or stats.get('treinamentos_a_vencer_7d', 0) > 0
        or stats.get('previstos_pendentes', 0) > 0
    )


def _failure_agent_stats(params: dict) -> dict[str, dict]:
    qs = apply_failure_filters(Failure.objects.select_related('agent'), params)
    agents: dict[str, dict] = defaultdict(lambda: {'falhas_total': 0, 'cenarios': Counter()})
    for row in qs.values('agent__matricula_norm', 'cenario', 'novo_cenario'):
        mat = norm_matricula(row.get('agent__matricula_norm') or '')
        if not mat:
            continue
        agents[mat]['falhas_total'] += 1
        cen = safe_str(row.get('cenario') or row.get('novo_cenario') or '')
        if cen:
            agents[mat]['cenarios'][cen] += 1
    return agents


def _critical_agent_sets(user, query_params, params: dict) -> tuple[set[str], set[str], set[str], set[str], dict[str, int]]:
    reinc_mats: set[str] = set()
    alta_freq_mats: set[str] = set()
    recorrente_mats: set[str] = set()
    fail_counts: dict[str, int] = {}

    try:
        _df_all, df_cur, df_prev, _params, _scope = load_period_frames(user, query_params)
        df_reinc = reincidencia_table_full(df_prev, df_cur)
        for _, row in df_reinc.iterrows():
            mat = norm_matricula(row.get('Matrícula Agente', ''))
            if not mat:
                continue
            occ = int(row.get('Ocorrências Mês Atual') or 0)
            fail_counts[mat] = occ
            is_reinc = safe_str(row.get('Reincidente', '')).lower() == 'sim'
            if is_reinc:
                reinc_mats.add(mat)
            elif occ > 1:
                alta_freq_mats.add(mat)
    except Exception:
        pass

    try:
        scope_only = dict(params)
        scope_only.pop('start_date', None)
        scope_only.pop('end_date', None)
        df_scope = failures_qs_to_legacy_df(
            apply_failure_filters(Failure.objects.select_related('agent'), scope_only)
        )
        ult = build_ult3m_payload(df_scope, params)
        for agente in ult.get('agentes') or []:
            if agente.get('recorrente'):
                mat = norm_matricula(agente.get('matricula') or '')
                if mat:
                    recorrente_mats.add(mat)
    except Exception:
        pass

    critical = reinc_mats | alta_freq_mats | recorrente_mats
    return critical, reinc_mats, alta_freq_mats, recorrente_mats, fail_counts


def _risco_capacitacao(row: dict) -> str:
    venc = int(row.get('treinamentos_vencidos') or 0)
    a_vencer = int(row.get('treinamentos_a_vencer_7d') or 0)
    if venc > 0 and (row.get('reincidente') or row.get('recorrente_4m')):
        return 'alto'
    if venc >= 2:
        return 'alto'
    if venc > 0 and row.get('alta_frequencia'):
        return 'alto'
    if venc > 0 or (row.get('reincidente') and a_vencer > 0):
        return 'medio'
    if a_vencer > 0:
        return 'medio'
    return 'baixo'


def build_training_reincidence_bridge(
    qs,
    params: dict,
    user=None,
    query_params=None,
) -> dict:
    """
    Cruza treinamentos (data_limite/status) com falhas/reincidência (data_analise).
    Agente crítico: reincidente oficial, alta frequência no período ou recorrente 4M.
    Pendência: vencido, a vencer em 7d ou previsto/ministrado sem assinatura.
    """
    from apps.falhas_criticas.services.training_analytics import _ref_date_from_params

    ref_date = _ref_date_from_params(params)
    train_agents = _training_agent_stats(qs, ref_date=ref_date)
    fail_agents = _failure_agent_stats(params)

    reinc_mats: set[str] = set()
    alta_freq_mats: set[str] = set()
    recorrente_mats: set[str] = set()
    fail_counts: dict[str, int] = {}
    if user is not None and query_params is not None:
        _critical, reinc_mats, alta_freq_mats, recorrente_mats, fail_counts = _critical_agent_sets(
            user, query_params, params,
        )

    critical_mats = reinc_mats | alta_freq_mats | recorrente_mats
    mats_needed = set(train_agents.keys()) | critical_mats | set(fail_agents.keys())
    meta = {
        a.matricula_norm: a
        for a in FalhasAgent.objects.filter(matricula_norm__in=list(mats_needed))
    }

    agentes_criticos: list[dict] = []
    for mat in critical_mats:
        train = train_agents.get(mat, {})
        if not train or not _has_pendencia(train):
            continue
        fail = fail_agents.get(mat, {'falhas_total': 0, 'cenarios': Counter()})
        ag = meta.get(mat)
        falhas_total = int(fail.get('falhas_total') or fail_counts.get(mat) or 0)
        top_cenario = fail['cenarios'].most_common(1)[0][0] if fail.get('cenarios') else ''
        row = {
            'matricula': mat,
            'nome_agente': train.get('nome_agente') or (ag.name if ag else ''),
            'localidade': (ag.localidade if ag else '') or '',
            'leader_nome': (ag.leader_nome if ag else '') or '',
            'falhas_total': falhas_total,
            'reincidente': mat in reinc_mats,
            'alta_frequencia': mat in alta_freq_mats,
            'recorrente_4m': mat in recorrente_mats,
            'top_cenario': top_cenario,
            'treinamentos_previstos': int(train.get('treinamentos_previstos', 0)),
            'treinamentos_vencidos': int(train.get('treinamentos_vencidos', 0)),
            'treinamentos_a_vencer_7d': int(train.get('treinamentos_a_vencer_7d', 0)),
            'treinamentos_assinados': int(train.get('treinamentos_assinados', 0)),
        }
        row['risco_capacitacao'] = _risco_capacitacao(row)
        agentes_criticos.append(row)

    agentes_criticos.sort(
        key=lambda x: (
            {'alto': 3, 'medio': 2, 'baixo': 1}.get(x['risco_capacitacao'], 0),
            x['treinamentos_vencidos'],
            x['falhas_total'],
            x['treinamentos_a_vencer_7d'],
        ),
        reverse=True,
    )
    agentes_criticos = agentes_criticos[:25]

    lideres_map: dict[tuple[str, str], dict] = defaultdict(lambda: {
        'leader_nome': '',
        'localidade': '',
        'agentes_criticos': 0,
        'com_vencidos': 0,
        'com_a_vencer_7d': 0,
        'falhas_total': 0,
        'reincidentes': 0,
    })
    for row in agentes_criticos:
        leader = row.get('leader_nome') or 'Sem líder'
        loc = row.get('localidade') or '—'
        key = (leader, loc)
        ld = lideres_map[key]
        ld['leader_nome'] = leader
        ld['localidade'] = loc
        ld['agentes_criticos'] += 1
        if row['treinamentos_vencidos'] > 0:
            ld['com_vencidos'] += 1
        if row['treinamentos_a_vencer_7d'] > 0:
            ld['com_a_vencer_7d'] += 1
        ld['falhas_total'] += row['falhas_total']
        if row['reincidente']:
            ld['reincidentes'] += 1

    lideres = sorted(
        lideres_map.values(),
        key=lambda x: (x['com_vencidos'], x['agentes_criticos'], x['falhas_total']),
        reverse=True,
    )[:15]

    cenarios_map: dict[str, dict] = defaultdict(lambda: {
        'cenario': '',
        'agentes': 0,
        'falhas_total': 0,
        'treinamentos_vencidos': 0,
        'treinamentos_a_vencer_7d': 0,
    })
    for row in agentes_criticos:
        cen = row.get('top_cenario') or 'Sem cenário'
        c = cenarios_map[cen]
        c['cenario'] = cen
        c['agentes'] += 1
        c['falhas_total'] += row['falhas_total']
        c['treinamentos_vencidos'] += row['treinamentos_vencidos']
        c['treinamentos_a_vencer_7d'] += row['treinamentos_a_vencer_7d']

    cenarios = sorted(
        cenarios_map.values(),
        key=lambda x: (x['treinamentos_vencidos'], x['falhas_total'], x['agentes']),
        reverse=True,
    )[:15]

    reinc_vencidos = sum(1 for a in agentes_criticos if a['reincidente'] and a['treinamentos_vencidos'] > 0)
    alta_pend = sum(1 for a in agentes_criticos if a['alta_frequencia'] and _has_pendencia(a))

    maior_risco = ''
    if agentes_criticos:
        top = agentes_criticos[0]
        maior_risco = (
            f"{top['nome_agente'] or top['matricula']} — "
            f"{top['treinamentos_vencidos']} vencido(s), {top['falhas_total']} falha(s)"
        )

    summary = {
        'agentes_criticos_com_pendencia': len(agentes_criticos),
        'reincidentes_com_vencidos': reinc_vencidos,
        'alta_frequencia_com_pendencia': alta_pend,
        'lideres_com_risco': len(lideres),
        'maior_risco': maior_risco,
    }

    if not agentes_criticos:
        return _empty_bridge()

    return {
        'agentes_criticos': agentes_criticos,
        'lideres': lideres,
        'cenarios': cenarios,
        'summary': summary,
        'pre_diagnostico': build_training_reincidence_bridge_narrative(
            summary, agentes_criticos, lideres,
        ),
    }
