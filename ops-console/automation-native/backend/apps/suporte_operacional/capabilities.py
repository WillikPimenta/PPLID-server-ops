"""Flags de capacidade calculadas no backend."""

from __future__ import annotations

from apps.access import registry as R
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.escala_flex.services.permissions import get_agent_for_user

from .models import OperationalSupportRequest
from .services.workflow import (
    CANCELABLE_STATUSES,
    Status,
    can_manage_support_answers,
    is_leader_actor,
)


def _bypass(user) -> bool:
    return bool(resolve_user_access(user).get("bypass"))


def compute_capabilities(user, req: OperationalSupportRequest) -> dict[str, bool]:
    bypass = _bypass(user)
    is_leader = is_leader_actor(user)
    own_agent = get_agent_for_user(user)
    is_own_agent = bool(own_agent and own_agent.pk == req.agent_id)

    can_approve = bypass or (
        user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER)
        and req.status == Status.PENDING_LEADER
    )
    can_reject = can_approve
    can_cancel = False
    if req.status in CANCELABLE_STATUSES:
        if bypass or is_leader:
            can_cancel = user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_CANCEL) or bypass
        elif is_own_agent and user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_CANCEL):
            can_cancel = True

    can_assign = (bypass or user_has_permission(user, R.QUAL_CAPACITACAO_SUPORTE_ASSIGN)) and (
        req.request_type == OperationalSupportRequest.RequestType.OFFLINE
        and req.status == Status.PENDING_OFFLINE
    )

    can_answer = False
    if req.status == Status.IN_ANALYSIS and (
        bypass or user_has_permission(user, R.QUAL_CAPACITACAO_SUPORTE_ANSWER)
    ):
        if bypass or (req.assignee_id and req.assignee_id == user.id) or can_manage_support_answers(user):
            can_answer = True

    return {
        "can_approve_leader": bool(can_approve),
        "can_reject_leader": bool(can_reject),
        "can_cancel": bool(can_cancel),
        "can_assign": bool(can_assign),
        "can_answer": bool(can_answer),
    }
