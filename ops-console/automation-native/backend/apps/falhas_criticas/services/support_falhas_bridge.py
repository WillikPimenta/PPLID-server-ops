# -*- coding: utf-8 -*-
"""Bridge Suporte TEAMS × Falhas Críticas."""
from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd

from report_falhas.io.data_loader import compute_conformidade_suporte, norm_matricula, safe_str
from report_falhas.reincidence import reincidencia_table_full

from apps.falhas_criticas.models import Failure, FalhasAgent
from apps.falhas_criticas.services.dataframes import apply_failure_filters, load_period_frames
from apps.falhas_criticas.services.narrative import build_support_falhas_bridge_narrative
from apps.falhas_criticas.services.support_analytics import _agent_label, _is_nc, _is_r3_row


def _empty_bridge():
    empty_lists = {
        'agentes_risco': [],
        'agentes_regra3': [],
        'agentes_nc_apenas': [],
        'clientes_workflows': [],
    }
    return {
        **empty_lists,
        'summary': {
            'agentes_com_regra3_e_falha': 0,
            'agentes_com_nc_e_falha': 0,
            'workflows_com_suporte_e_falha': 0,
            'maior_risco': '',
        },
        'pre_diagnostico': build_support_falhas_bridge_narrative(
            {
                'agentes_com_regra3_e_falha': 0,
                'agentes_com_nc_e_falha': 0,
                'workflows_com_suporte_e_falha': 0,
                'maior_risco': '',
            },
            [],
            [],
        ),
    }


def _support_agent_stats(df_sup: pd.DataFrame) -> dict[str, dict]:
    agents: dict[str, dict] = {}
    for _, row in df_sup.iterrows():
        nome, mat = _agent_label(row)
        mat = norm_matricula(mat)
        if not mat:
            continue
        if mat not in agents:
            agents[mat] = {
                'matricula': mat,
                'nome_agente': nome,
                'support_total': 0,
                'nc_count': 0,
                'regra3_count': 0,
            }
        agents[mat]['support_total'] += 1
        conf = compute_conformidade_suporte({
            'Dúvida': row.get('Dúvida', ''),
            'Conclusão': row.get('Conclusão', ''),
            'Grau de dificuldade': row.get('Grau de dificuldade', ''),
            'Crítico': row.get('Crítico', ''),
        })
        if _is_nc(conf):
            agents[mat]['nc_count'] += 1
        if _is_r3_row(row):
            agents[mat]['regra3_count'] += 1
    return agents


def _support_client_workflow_stats(df_sup: pd.DataFrame) -> dict[tuple[str, str], dict]:
    cw: dict[tuple[str, str], dict] = defaultdict(
        lambda: {'support_total': 0, 'nc_count': 0, 'regra3_count': 0}
    )
    for _, row in df_sup.iterrows():
        cliente = safe_str(row.get('Cliente', ''))
        workflow = safe_str(row.get('Workflow', ''))
        if not cliente and not workflow:
            continue
        key = (cliente, workflow)
        cw[key]['support_total'] += 1
        conf = compute_conformidade_suporte({
            'Dúvida': row.get('Dúvida', ''),
            'Conclusão': row.get('Conclusão', ''),
            'Grau de dificuldade': row.get('Grau de dificuldade', ''),
            'Crítico': row.get('Crítico', ''),
        })
        if _is_nc(conf):
            cw[key]['nc_count'] += 1
        if _is_r3_row(row):
            cw[key]['regra3_count'] += 1
    return cw


def _failure_stats(params: dict) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    qs = apply_failure_filters(Failure.objects.select_related('agent'), params)
    agents: dict[str, dict] = defaultdict(lambda: {'falhas_total': 0, 'cenarios': Counter()})
    cw: dict[tuple[str, str], dict] = defaultdict(lambda: {'falhas_total': 0, 'cenarios': Counter()})
    for row in qs.values('agent__matricula_norm', 'cenario', 'novo_cenario', 'cliente', 'workflow'):
        mat = norm_matricula(row.get('agent__matricula_norm') or '')
        cen = safe_str(row.get('cenario') or row.get('novo_cenario') or '')
        if mat:
            agents[mat]['falhas_total'] += 1
            if cen:
                agents[mat]['cenarios'][cen] += 1
        cliente = safe_str(row.get('cliente') or '')
        workflow = safe_str(row.get('workflow') or '')
        if not cliente and not workflow:
            continue
        key = (cliente, workflow)
        cw[key]['falhas_total'] += 1
        if cen:
            cw[key]['cenarios'][cen] += 1
    return agents, cw


def _reincident_mats(user, query_params) -> set[str]:
    _df_all, df_cur, df_prev, _params, _scope = load_period_frames(user, query_params)
    df_reinc = reincidencia_table_full(df_prev, df_cur)
    if df_reinc.empty:
        return set()
    mats: set[str] = set()
    for _, row in df_reinc.iterrows():
        if safe_str(row.get('Reincidente', '')).lower() != 'sim':
            continue
        mat = norm_matricula(row.get('Matrícula Agente', ''))
        if mat:
            mats.add(mat)
    return mats


def build_support_falhas_bridge(
    df_sup: pd.DataFrame | None,
    params: dict,
    user=None,
    query_params=None,
) -> dict:
    """
    Cruza suporte TEAMS com falhas no período/filtros atuais.
    support_total = solicitações (linhas); falhas_total = ocorrências de falha.
    """
    if df_sup is None:
        df_sup = pd.DataFrame()

    sup_agents = _support_agent_stats(df_sup)
    sup_cw = _support_client_workflow_stats(df_sup)
    fail_agents, fail_cw = _failure_stats(params)

    reinc_mats: set[str] = set()
    if user is not None and query_params is not None:
        try:
            reinc_mats = _reincident_mats(user, query_params)
        except Exception:
            reinc_mats = set()

    fail_mats = {m for m, data in fail_agents.items() if data['falhas_total'] > 0}
    mats_needed = set(sup_agents.keys()) | fail_mats
    meta = {
        a.matricula_norm: a
        for a in FalhasAgent.objects.filter(matricula_norm__in=list(mats_needed))
    }

    agentes_risco: list[dict] = []
    count_r3_falha = 0
    count_nc_falha = 0

    for mat in fail_mats:
        sup = sup_agents.get(mat, {})
        fail = fail_agents.get(mat, {'falhas_total': 0, 'cenarios': Counter()})
        r3 = int(sup.get('regra3_count', 0))
        nc = int(sup.get('nc_count', 0))
        support_total = int(sup.get('support_total', 0))
        falhas_total = int(fail['falhas_total'])
        if falhas_total <= 0 or (r3 <= 0 and nc <= 0):
            continue
        if r3 > 0:
            count_r3_falha += 1
        if nc > 0:
            count_nc_falha += 1
        ag = meta.get(mat)
        top_cenario = fail['cenarios'].most_common(1)[0][0] if fail['cenarios'] else ''
        agentes_risco.append({
            'matricula': mat,
            'nome_agente': sup.get('nome_agente') or (ag.name if ag else ''),
            'localidade': (ag.localidade if ag else '') or '',
            'leader_nome': (ag.leader_nome if ag else '') or '',
            'regra3_count': r3,
            'nc_count': nc,
            'support_total': support_total,
            'falhas_total': falhas_total,
            'reincidente': mat in reinc_mats,
            'top_cenario': top_cenario,
        })

    agentes_risco.sort(
        key=lambda x: (x['regra3_count'], x['falhas_total'], x['nc_count']),
        reverse=True,
    )
    agentes_regra3 = [a for a in agentes_risco if a['regra3_count'] > 0][:15]
    agentes_nc_apenas = [a for a in agentes_risco if a['nc_count'] > 0 and a['regra3_count'] == 0][:15]
    agentes_risco = agentes_risco[:25]

    clientes_workflows: list[dict] = []
    overlap_keys = set(sup_cw.keys()) & set(fail_cw.keys())
    for cliente, workflow in overlap_keys:
        s = sup_cw[(cliente, workflow)]
        f = fail_cw[(cliente, workflow)]
        if s['support_total'] <= 0 or f['falhas_total'] <= 0:
            continue
        top_cenario = f['cenarios'].most_common(1)[0][0] if f['cenarios'] else ''
        clientes_workflows.append({
            'cliente': cliente,
            'workflow': workflow,
            'support_total': int(s['support_total']),
            'nc_count': int(s['nc_count']),
            'regra3_count': int(s['regra3_count']),
            'falhas_total': int(f['falhas_total']),
            'top_cenario': top_cenario,
        })

    clientes_workflows.sort(
        key=lambda x: (x['falhas_total'] + x['regra3_count'], x['support_total']),
        reverse=True,
    )
    clientes_workflows = clientes_workflows[:20]

    maior_risco = ''
    if agentes_risco:
        top = agentes_risco[0]
        maior_risco = (
            f"{top['nome_agente'] or top['matricula']} — "
            f"{top['regra3_count']} Regra 3 e {top['falhas_total']} falha(s)"
        )
    elif clientes_workflows:
        top = clientes_workflows[0]
        maior_risco = (
            f"{top['cliente']} / {top['workflow']} — "
            f"{top['falhas_total']} falha(s) e {top['support_total']} solicitações"
        )

    summary = {
        'agentes_com_regra3_e_falha': count_r3_falha,
        'agentes_com_nc_e_falha': count_nc_falha,
        'workflows_com_suporte_e_falha': len(clientes_workflows),
        'maior_risco': maior_risco,
    }

    if not agentes_risco and not clientes_workflows:
        return _empty_bridge()

    return {
        'agentes_risco': agentes_risco,
        'agentes_regra3': agentes_regra3,
        'agentes_nc_apenas': agentes_nc_apenas,
        'clientes_workflows': clientes_workflows,
        'summary': summary,
        'pre_diagnostico': build_support_falhas_bridge_narrative(
            summary, agentes_risco, clientes_workflows
        ),
    }
