"""Workflow de solicitação de troca de escala."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone

from apps.workforce.models import Agent

from ..models import ScheduleRequest
from .permissions import OperationalProfile, get_active_history

SWAP_REQUEST_TYPE_NAME = "Troca de escala"
# Segunda a quinta: até 17:00 (inclusive), D+1; após 17:00, D+2.
# Sexta-feira: sábado e domingo permanecem elegíveis até o fim do dia.
# Sábado e domingo: primeira data elegível é a terça-feira seguinte.
SWAP_CUTOFF_TIME = time(17, 0)
SWAP_MIN_ADVANCE_DAYS_BEFORE_CUTOFF = 1
SWAP_MIN_ADVANCE_DAYS_AFTER_CUTOFF = 2
# Compat: valor legado (pós-corte). Preferir min_swap_date().
SWAP_MIN_ADVANCE_DAYS = SWAP_MIN_ADVANCE_DAYS_AFTER_CUTOFF

SwapWorkflowStatus = str  # pending_leader | pending_plan | approved | rejected


def min_swap_date(now: datetime | date | None = None) -> date:
    """Menor data aceita para solicitação de troca, conforme horário do pedido."""
    if now is None:
        local_now = timezone.localtime()
    elif isinstance(now, datetime):
        local_now = timezone.localtime(now) if timezone.is_aware(now) else now
    else:
        # Só data: assume início do dia (antes do corte → D+1).
        local_now = datetime.combine(now, time.min)

    reference = local_now.date()
    weekday = reference.weekday()
    if weekday == 4:  # Sexta-feira: aceita o fim de semana até o fim do dia.
        return reference + timedelta(days=1)
    if weekday in (5, 6):  # Fim de semana: somente a partir de terça-feira.
        days_until_tuesday = (1 - weekday) % 7
        return reference + timedelta(days=days_until_tuesday)

    advance = (
        SWAP_MIN_ADVANCE_DAYS_BEFORE_CUTOFF
        if local_now.time() <= SWAP_CUTOFF_TIME
        else SWAP_MIN_ADVANCE_DAYS_AFTER_CUTOFF
    )
    return reference + timedelta(days=advance)


def validate_swap_date(date_swap: date, now: datetime | date | None = None) -> str | None:
    minimum = min_swap_date(now)
    if date_swap < minimum:
        return f"A data da troca deve ser a partir de {minimum:%d/%m/%Y}."
    return None


def resolve_swap_workflow_status(request: ScheduleRequest) -> SwapWorkflowStatus:
    if request.approved_leader is False or request.approved is False:
        return "rejected"
    if request.approved is True:
        return "approved"
    if request.approved_leader is True:
        return "pending_plan"
    return "pending_leader"


def should_finalize_peer_after_leader(request: ScheduleRequest) -> bool:
    return bool(
        request.swap_kind == "peer"
        and request.validation_days == "ok"
        and request.validation_hour == "ok"
        and request.validation_activity == "ok"
    )


def is_leader_of_agent(profile: OperationalProfile, agent_lan_id: str) -> bool:
    if not profile.lan_id or not agent_lan_id:
        return False
    agent = Agent.objects.filter(user_lan_id__iexact=agent_lan_id).first()
    if not agent:
        return False
    history = get_active_history(agent)
    return bool(history and history.leader and history.leader.user_lan_id.lower() == profile.lan_id)


def can_create_swap_request(
    profile: OperationalProfile,
    agent_lan_id: str,
    *,
    user=None,
) -> bool:
    """Agente (escopo own) só solicita para si; líder para a equipe; admin/plan global."""
    if not agent_lan_id:
        return False

    if user is not None:
        from apps.access.constants import SCOPE_OWN
        from apps.access.resolve import resolve_user_access, user_has_any_permission
        from apps.access import registry as R

        access = resolve_user_access(user)
        if access.get("bypass"):
            return True
        if user_has_any_permission(
            user,
            R.PLANEJAMENTO_TROCAS_VIEW,
            R.PLANEJAMENTO_TROCAS_APPROVE,
        ):
            return True

        scope = (access.get("scopes") or {}).get("operacao") or (access.get("scopes") or {}).get(
            "default"
        )
        if scope == SCOPE_OWN:
            return bool(profile.lan_id and profile.lan_id.lower() == agent_lan_id.lower())

    if profile.is_admin or profile.team == "Planejamento":
        return True
    if profile.lan_id and profile.lan_id.lower() == agent_lan_id.lower():
        return True
    return is_leader_of_agent(profile, agent_lan_id)


def can_approve_swap_leader(profile: OperationalProfile, request: ScheduleRequest) -> bool:
    if resolve_swap_workflow_status(request) != "pending_leader":
        return False
    if profile.is_admin:
        return True
    return is_leader_of_agent(profile, request.agent_lan_id)


def can_approve_swap_plan(profile: OperationalProfile, request: ScheduleRequest) -> bool:
    if resolve_swap_workflow_status(request) != "pending_plan":
        return False
    return profile.is_admin or profile.team == "Planejamento"


def should_auto_approve_leader(profile: OperationalProfile, agent_lan_id: str) -> bool:
    if profile.is_admin or profile.team == "Planejamento":
        return True
    return is_leader_of_agent(profile, agent_lan_id)


def apply_leader_auto_approval(request: ScheduleRequest, profile: OperationalProfile) -> None:
    request.approved_leader = True
    request.date_approve_leader = timezone.now()
    request.approver_leader_lan_id = profile.lan_id or ""
