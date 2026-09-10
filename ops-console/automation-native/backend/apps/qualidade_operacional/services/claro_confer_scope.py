# -*- coding: utf-8 -*-
"""Escopo HC para falhas/auditados Claro Formalização/Confer."""
from __future__ import annotations

from datetime import date

from django.db.models import Q, QuerySet

from apps.qualidade_operacional.services.criticidade import (
    CLIENTE_CLARO_FORMALIZACAO_ID,
)
from apps.qualidade_operacional.services.intranet_source import (
    CLARO_CONFER_CLIENT_ID,
    CLARO_CONFER_WORKFLOW_ID,
)
from apps.workforce.models import AgentHistory

CLARO_CONFER_HC_TEAM = "Operacional/Compliance"
_OPEN_VIGENCIA_END = date(9999, 12, 31)


def claro_formalizacao_confer_q() -> Q:
    """Identifica registros do portfólio Claro Formalização / Confer."""
    return Q(id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID) | Q(
        id_cliente=CLARO_CONFER_CLIENT_ID,
        id_workflow=CLARO_CONFER_WORKFLOW_ID,
    )


def confer_team_occurrence_query(*, date_field: str) -> Q:
    """Q temporal: ocorrência na equipe Operacional/Compliance na data do evento."""
    qs = (
        AgentHistory.objects.select_related("agent")
        .exclude(agent__user_lan_id="")
        .exclude(team="")
        .filter(team__iexact=CLARO_CONFER_HC_TEAM)
    )

    query = Q(pk__in=[])
    grouped: dict[tuple[date, date], set[str]] = {}
    for history in qs.iterator(chunk_size=2000):
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula:
            continue
        hist_start = history.start_date
        hist_end = history.final_date or _OPEN_VIGENCIA_END
        grouped.setdefault((hist_start, hist_end), set()).add(matricula)

    for (window_start, window_end), matriculas in sorted(grouped.items()):
        query |= Q(
            matricula__in=sorted(matriculas),
            **{
                f"{date_field}__gte": window_start,
                f"{date_field}__lte": window_end,
            },
        )
    return query


def apply_claro_confer_team_scope(qs: QuerySet, *, date_field: str) -> QuerySet:
    """Exclui Claro Formalização/Confer sem equipe Operacional/Compliance na data."""
    claro_q = claro_formalizacao_confer_q()
    if not qs.filter(claro_q).exists():
        return qs
    confer_q = confer_team_occurrence_query(date_field=date_field)
    return qs.exclude(claro_q & ~confer_q)
