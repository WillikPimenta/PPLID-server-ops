"""Escopo de listagem de suporte operacional (imposto no backend)."""

from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.access import registry as R
from apps.access.constants import SCOPE_OWN
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.escala_flex.services.permissions import get_agent_for_user
from apps.workforce.models import AgentHistory

from .models import OperationalSupportRequest
from .services.workflow import Status


def scoped_queryset(user) -> QuerySet[OperationalSupportRequest]:
    current_history_qs = (
        AgentHistory.objects.filter(active=True, final_date__isnull=True)
        .select_related("leader")
        .order_by("-start_date")
    )
    qs = OperationalSupportRequest.objects.select_related(
        "agent",
        "requester",
        "leader_decider",
        "assignee",
        "answered_by",
        "cancelled_by",
    ).prefetch_related(
        Prefetch(
            "agent__history",
            queryset=current_history_qs,
            to_attr="current_histories",
        )
    )
    access = resolve_user_access(user)
    if access.get("bypass"):
        return qs

    has_op_view = user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_VIEW)
    has_cap_view = user_has_permission(user, R.QUAL_CAPACITACAO_SUPORTE_VIEW)
    has_approve = user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER)

    if not has_op_view and not has_cap_view:
        return qs.none()

    # Capacitação sem visão de operação: não vê pending_leader.
    if has_cap_view and not has_op_view:
        return qs.exclude(status=Status.PENDING_LEADER)

    # Líder / gerência com approve: visão ampla (sem filtro de equipe).
    if has_approve:
        return qs

    # Agente (escopo own ou sem approve): só o próprio vínculo.
    scopes = access.get("scopes") or {}
    scope = scopes.get("operacao") or scopes.get("default")
    own_agent = get_agent_for_user(user)
    if scope == SCOPE_OWN or (has_op_view and not has_approve):
        if not own_agent:
            return qs.none()
        return qs.filter(Q(agent=own_agent) | Q(requester=user))

    return qs
