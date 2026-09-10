# -*- coding: utf-8 -*-
"""Contrato único de prioridade Alta/Média/Baixa para Falhas Críticas."""
from __future__ import annotations

from typing import Any


PRIORITY_ALTA = 'alta'
PRIORITY_MEDIA = 'media'
PRIORITY_BAIXA = 'baixa'

_LEVEL_RANK = {PRIORITY_ALTA: 0, PRIORITY_MEDIA: 1, PRIORITY_BAIXA: 2}


def share_to_level(share_pct: float | int | None) -> str:
    """Cenário/combinação por participação no volume do período."""
    try:
        pct = float(share_pct or 0)
    except (TypeError, ValueError):
        pct = 0.0
    if pct >= 20:
        return PRIORITY_ALTA
    if pct >= 10:
        return PRIORITY_MEDIA
    return PRIORITY_BAIXA


def agent_priority(
    *,
    oficial: bool = False,
    critico: bool = False,
    alta_frequencia: bool = False,
    recorrente_4m: bool = False,
) -> str:
    """Prioridade de agente na fila de reincidência / capacitação."""
    if oficial and critico:
        return PRIORITY_ALTA
    if oficial or critico or alta_frequencia or recorrente_4m:
        return PRIORITY_MEDIA
    return PRIORITY_BAIXA


def driver_priority(rank: int, *, worsening: bool = False) -> str:
    """Prioridade de driver BSB×SC (1-based rank)."""
    if rank <= 1 and worsening:
        return PRIORITY_ALTA
    if rank <= 3:
        return PRIORITY_MEDIA
    return PRIORITY_BAIXA


def priority_item(
    level: str,
    title: str,
    evidence: str,
    cta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lvl = level if level in _LEVEL_RANK else PRIORITY_BAIXA
    item: dict[str, Any] = {
        'level': lvl,
        'title': title or '',
        'evidence': evidence or '',
    }
    if cta:
        item['cta'] = cta
    return item


def sort_priorities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: _LEVEL_RANK.get(x.get('level'), 9))


def build_dashboard_prioridades(
    *,
    top_cenario_nome: str | None,
    top_cenario_qtd: int | float | None,
    top_cenario_pct: float | int | None,
    reinc_total: int | None = None,
    agentes_com_falha: int | None = None,
    suporte_nc_pct: float | int | None = None,
    suporte_total: int | None = None,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """Prioridades do Início (só Alta/Média)."""
    items: list[dict[str, Any]] = []

    nome = (top_cenario_nome or '').strip()
    if nome and nome not in ('—', '-', 'N/A'):
        level = share_to_level(top_cenario_pct)
        if level in (PRIORITY_ALTA, PRIORITY_MEDIA):
            qtd = int(top_cenario_qtd or 0)
            try:
                pct_txt = f'{float(top_cenario_pct):.0f}%'
            except (TypeError, ValueError):
                pct_txt = str(top_cenario_pct or '')
            threshold = '≥20% do período' if level == PRIORITY_ALTA else '≥10% do período'
            items.append(
                priority_item(
                    level,
                    f'Cenário dominante: {nome}',
                    f'{qtd} falhas ({pct_txt} das falhas do período · prioridade {threshold})',
                    {
                        'type': 'navigate',
                        'module': 'diagnostico',
                        'label': 'Ver cenário',
                    },
                )
            )

    reinc_n = int(reinc_total or 0)
    if reinc_n > 0:
        agentes_n = int(agentes_com_falha or 0)
        pct_reinc = round((reinc_n / agentes_n) * 100) if agentes_n > 0 else None
        level = (
            PRIORITY_ALTA
            if reinc_n >= 5 or (pct_reinc is not None and pct_reinc >= 15)
            else PRIORITY_MEDIA
        )
        if pct_reinc is not None:
            evidence = (
                f'{reinc_n} reincidentes — {pct_reinc}% dos agentes com falha; '
                'prioridade alta se ≥15% (ou ≥5 agentes)'
            )
        else:
            evidence = (
                f'{reinc_n} reincidentes; prioridade alta se ≥15% (ou ≥5 agentes)'
            )
        items.append(
            priority_item(
                level,
                'Reincidentes oficiais no período',
                evidence,
                {
                    'type': 'navigate',
                    'module': 'reincidencia',
                    'label': 'Ver reincidentes',
                },
            )
        )

    if int(suporte_total or 0) > 0 and suporte_nc_pct is not None:
        try:
            nc = float(suporte_nc_pct)
        except (TypeError, ValueError):
            nc = 0.0
        if nc >= 10:
            level = PRIORITY_ALTA if nc >= 20 else PRIORITY_MEDIA
            limiar = 'alerta ≥20%' if level == PRIORITY_ALTA else 'alerta ≥10%'
            items.append(
                priority_item(
                    level,
                    'NC% elevado no Suporte TEAMS',
                    f'{nc:.1f}% de NC em {int(suporte_total)} solicitações ({limiar})',
                    {
                        'type': 'navigate',
                        'module': 'suporte',
                        'label': 'Ver Suporte TEAMS',
                    },
                )
            )

    filtered = [i for i in sort_priorities(items) if i['level'] in (PRIORITY_ALTA, PRIORITY_MEDIA)]
    return filtered[:max_items]


def build_dashboard_leitura(
    *,
    pre_diagnostico: dict | None,
    top_cenario_nome: str | None,
    top_cenario_pct: float | int | None,
    reinc_total: int | None,
    variacao_delta: Any = None,
) -> dict[str, Any]:
    """Bloco de leitura do Início (conclusão + bullets)."""
    pd = pre_diagnostico or {}
    veredito = (pd.get('veredito') or '').strip()
    bullets = list(pd.get('bullets') or [])

    if not veredito:
        nome = (top_cenario_nome or '').strip() or '—'
        try:
            pct = float(top_cenario_pct or 0)
            pct_txt = f'{pct:.0f}%'
        except (TypeError, ValueError):
            pct_txt = str(top_cenario_pct or '—')
        veredito = (
            f'Concentração em {nome} ({pct_txt}). '
            f'{int(reinc_total or 0)} reincidente(s) no período.'
        )

    if not bullets:
        bullets = [
            f'Cenário principal: {(top_cenario_nome or "—")}',
            f'Reincidentes oficiais: {int(reinc_total or 0)}',
        ]
        if variacao_delta not in (None, '', '—'):
            bullets.append(f'Variação vs período anterior: {variacao_delta}')

    return {
        'titulo': pd.get('titulo') or 'Resumo do período',
        'veredito': veredito,
        'bullets': bullets[:4],
        'tendencia': pd.get('tendencia') or '',
        'cta': {'type': 'navigate', 'module': 'diagnostico', 'label': 'Ver diagnóstico'},
    }
