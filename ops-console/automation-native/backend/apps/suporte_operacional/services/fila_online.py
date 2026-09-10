"""Fila de atendimento online do suporte operacional (dual-slot, SLA, presença)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
import unicodedata

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Prefetch
from django.utils import timezone

from apps.access import registry as R
from apps.access.resolve import user_has_permission
from apps.workforce.models import Agent, AgentHistory

from ..models import (
    OperationalSupportAgentPresence,
    OperationalSupportEvent,
    OperationalSupportRequest,
)
from .sla_ranking import (
    compute_support_sla_metrics,
    load_support_sla_map,
    rank_online_queue_requests,
)
from .workflow import (
    EventType,
    QueueOrigin,
    RequestType,
    Status,
    _record_event,
    _save_request,
)

User = get_user_model()

FILA_TIMEOUT_SECONDS = 60
MAX_ACTIVE_PER_AGENT = 2
MAX_AUTO_ACTIVE_PER_AGENT = 1
TIMEOUT_EXCLUDE_SECONDS = 60

PRESENCE_LABELS = dict(OperationalSupportAgentPresence.STATUS_CHOICES)


class FilaError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _agent_for_user(user):
    if not user:
        return None
    profile = getattr(user, "profile", None)
    agent = getattr(profile, "agent", None)
    if agent is not None:
        return agent
    return Agent.objects.filter(user_lan_id__iexact=user.username).first()


def user_display_name(user) -> str:
    if not user:
        return ""
    agent = _agent_for_user(user)
    agent_name = (getattr(agent, "full_name", "") or "").strip()
    if agent_name:
        return agent_name
    full = (user.get_full_name() or "").strip()
    return full or user.username


def get_or_create_presence(user) -> OperationalSupportAgentPresence:
    presence, _ = OperationalSupportAgentPresence.objects.get_or_create(user=user)
    return presence


def has_available_support_agent() -> bool:
    return OperationalSupportAgentPresence.objects.filter(
        status=OperationalSupportAgentPresence.STATUS_ONLINE,
        user__is_active=True,
    ).exists()


def online_support_agent_count() -> int:
    return OperationalSupportAgentPresence.objects.filter(
        status=OperationalSupportAgentPresence.STATUS_ONLINE,
        user__is_active=True,
    ).count()


def _with_agent_context(queryset):
    return queryset.select_related("agent").prefetch_related(
        Prefetch(
            "agent__history",
            queryset=AgentHistory.objects.filter(
                active=True,
                final_date__isnull=True,
            ).select_related("leader"),
            to_attr="current_histories",
        ),
        Prefetch(
            "events",
            queryset=OperationalSupportEvent.objects.filter(
                event_type=EventType.ASSIGNED,
            ).order_by("-created_at"),
            to_attr="analysis_start_events",
        ),
    )


def _online_unassigned_qs():
    return _with_agent_context(
        OperationalSupportRequest.objects.filter(
            request_type=RequestType.ONLINE,
            status=Status.PENDING_SUPPORT,
            assignee__isnull=True,
        )
    )


def _active_for_agent(agent_id):
    return _with_agent_context(
        OperationalSupportRequest.objects.filter(
            request_type=RequestType.ONLINE,
            status=Status.IN_ANALYSIS,
            assignee_id=agent_id,
        )
    ).order_by(F("queue_slot").asc(nulls_last=True), F("assigned_at").asc(nulls_last=True), "created_at")


def _slot_items(agent_id) -> list[OperationalSupportRequest]:
    return list(_active_for_agent(agent_id)[:MAX_ACTIVE_PER_AGENT])


def _answered_counts(user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = (
        OperationalSupportRequest.objects.filter(
            status=Status.ANSWERED,
            answered_by_id__in=user_ids,
        )
        .values("answered_by_id")
        .annotate(total=Count("id"))
    )
    return {row["answered_by_id"]: row["total"] for row in rows}


def _timeout_excluded_ids(agent_id: int) -> set:
    cutoff = timezone.now() - timedelta(seconds=TIMEOUT_EXCLUDE_SECONDS)
    return set(
        OperationalSupportRequest.objects.filter(
            assignee_id=agent_id,
            events__event_type=EventType.QUEUE_TIMEOUT,
            events__created_at__gte=cutoff,
        )
        .values_list("id", flat=True)
        .distinct()
    )


def _clear_queue_fields(req: OperationalSupportRequest) -> None:
    req.assignee = None
    req.assigned_at = None
    req.queue_origin = ""
    req.queue_slot = None
    req.queue_sla_started_at = None
    req.queue_priority_at = None
    req.status = Status.PENDING_SUPPORT


def _assign_to_agent(
    req: OperationalSupportRequest,
    *,
    agent,
    actor,
    slot: int,
    origin: str,
    start_sla: bool,
    event_type: str,
    note: str,
) -> OperationalSupportRequest:
    now = timezone.now()
    from_status = req.status
    req.status = Status.IN_ANALYSIS
    req.assignee = agent
    req.assigned_at = now
    req.queue_slot = slot
    req.queue_origin = origin
    req.queue_sla_started_at = now if start_sla else None
    req.queue_priority_at = None
    _save_request(
        req,
        update_fields=[
            "status",
            "assignee",
            "assigned_at",
            "queue_slot",
            "queue_origin",
            "queue_sla_started_at",
            "queue_priority_at",
            "updated_at",
        ],
    )
    _record_event(
        req,
        event_type=event_type,
        author=actor,
        from_status=from_status,
        to_status=Status.IN_ANALYSIS,
        note=note,
    )
    presence = get_or_create_presence(agent)
    presence.last_assigned_at = now
    presence.save(update_fields=["last_assigned_at", "updated_at"])
    return req


def _release_request(
    req: OperationalSupportRequest,
    *,
    actor,
    event_type: str,
    note: str,
) -> None:
    from_status = req.status
    released_assignee_id = req.assignee_id
    released_assignee_username = req.assignee.username if req.assignee_id else ""
    _clear_queue_fields(req)
    _save_request(
        req,
        update_fields=[
            "status",
            "assignee",
            "assigned_at",
            "queue_origin",
            "queue_slot",
            "queue_sla_started_at",
            "queue_priority_at",
            "updated_at",
        ],
    )
    _record_event(
        req,
        event_type=event_type,
        author=actor,
        from_status=from_status,
        to_status=Status.PENDING_SUPPORT,
        note=note,
        context={
            "released_assignee_id": str(released_assignee_id) if released_assignee_id else None,
            "released_assignee_username": released_assignee_username,
        },
    )


def _activate_slot_two(agent_id: int, *, actor=None) -> None:
    waiting = (
        _active_for_agent(agent_id)
        .filter(queue_slot=2, queue_sla_started_at__isnull=True)
        .select_for_update()
        .first()
    )
    if not waiting:
        return
    now = timezone.now()
    waiting.queue_slot = 1
    waiting.queue_sla_started_at = now
    _save_request(
        waiting,
        update_fields=["queue_slot", "queue_sla_started_at", "updated_at"],
    )
    _record_event(
        waiting,
        event_type=EventType.QUEUE_ASSIGNED,
        author=actor,
        from_status=Status.IN_ANALYSIS,
        to_status=Status.IN_ANALYSIS,
        note="Protocolo direcionado promovido para análise (posição 1).",
    )


@transaction.atomic
def release_agent_active_queue(*, agent, actor, motivo: str) -> int:
    items = list(_active_for_agent(agent.id).select_for_update())
    for req in items:
        _release_request(
            req,
            actor=actor,
            event_type=EventType.QUEUE_RELEASED,
            note=motivo,
        )
    return len(items)


@transaction.atomic
def set_agent_status(*, user, status: str, actor=None) -> OperationalSupportAgentPresence:
    status = (status or "").strip().lower()
    valid = {c[0] for c in OperationalSupportAgentPresence.STATUS_CHOICES}
    if status not in valid:
        raise FilaError("Status inválido. Use online, offline ou presencial.")

    actor = actor or user
    presence = OperationalSupportAgentPresence.objects.select_for_update().filter(user=user).first()
    if not presence:
        presence = OperationalSupportAgentPresence(user=user)

    presence.status = status
    presence.status_changed_at = timezone.now()
    presence.save()

    if status in {
        OperationalSupportAgentPresence.STATUS_OFFLINE,
        OperationalSupportAgentPresence.STATUS_PRESENCIAL,
    }:
        release_agent_active_queue(agent=user, actor=actor, motivo=f"status_{status}")

    return presence


def _next_unassigned(*, agent_id: int | None = None, exclude_ids: set | None = None) -> OperationalSupportRequest | None:
    exclude_ids = set(exclude_ids or [])
    if agent_id:
        exclude_ids |= _timeout_excluded_ids(agent_id)
    qs = _online_unassigned_qs().exclude(id__in=exclude_ids)
    ranked = rank_online_queue_requests(list(qs[:200]))
    return ranked[0] if ranked else None


def queue_positions_by_id() -> dict[str, int]:
    ranked = rank_online_queue_requests(list(_online_unassigned_qs()))
    return {str(req.pk): position for position, req in enumerate(ranked, start=1)}


@transaction.atomic
def expire_stale_assignments(*, actor=None) -> int:
    cutoff = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS)
    stale = list(
        OperationalSupportRequest.objects.filter(
            request_type=RequestType.ONLINE,
            status=Status.IN_ANALYSIS,
            queue_slot=1,
            queue_sla_started_at__lt=cutoff,
        )
        .select_for_update(skip_locked=True)
        .order_by("id")
    )
    released = 0
    affected_agents: set[int] = set()
    for req in stale:
        if req.assignee_id:
            affected_agents.add(req.assignee_id)
        _release_request(
            req,
            actor=actor,
            event_type=EventType.QUEUE_TIMEOUT,
            note="Timeout de 1 minuto na fila pessoal.",
        )
        released += 1
    for agent_id in affected_agents:
        _activate_slot_two(agent_id, actor=actor)
    return released


@transaction.atomic
def claim_next_for_agent(*, user, actor=None) -> OperationalSupportRequest | None:
    actor = actor or user
    expire_stale_assignments(actor=actor)

    presence = get_or_create_presence(user)
    if presence.status != OperationalSupportAgentPresence.STATUS_ONLINE:
        return None

    if len(_slot_items(user.id)) >= MAX_AUTO_ACTIVE_PER_AGENT:
        return None

    candidate = _next_unassigned(agent_id=user.id)
    if not candidate:
        return None

    next_req = (
        OperationalSupportRequest.objects.select_for_update()
        .filter(pk=candidate.pk, status=Status.PENDING_SUPPORT, assignee__isnull=True)
        .first()
    )
    if not next_req:
        return None

    return _assign_to_agent(
        next_req,
        agent=user,
        actor=actor,
        slot=1,
        origin=QueueOrigin.AUTO,
        start_sla=True,
        event_type=EventType.QUEUE_ASSIGNED,
        note="Atribuição automática da fila online.",
    )


def assumir_protocolo(*, user, request_id, actor=None) -> OperationalSupportRequest:
    """Confirma o protocolo atual e encerra seu timeout de aceite."""
    actor = actor or user
    expired = False
    with transaction.atomic():
        req = (
            OperationalSupportRequest.objects.select_for_update()
            .filter(
                pk=request_id,
                assignee=user,
                request_type=RequestType.ONLINE,
                status=Status.IN_ANALYSIS,
                queue_slot=1,
            )
            .first()
        )
        if not req:
            raise FilaError("O protocolo não está mais disponível na sua fila.", status_code=409)
        if req.queue_sla_started_at is None:
            return req

        elapsed = (timezone.now() - req.queue_sla_started_at).total_seconds()
        if elapsed >= FILA_TIMEOUT_SECONDS:
            agent_id = req.assignee_id
            _release_request(
                req,
                actor=actor,
                event_type=EventType.QUEUE_TIMEOUT,
                note="Timeout de 1 minuto na fila pessoal.",
            )
            if agent_id:
                _activate_slot_two(agent_id, actor=actor)
            expired = True
        else:
            req.queue_sla_started_at = None
            _save_request(req, update_fields=["queue_sla_started_at", "updated_at"])
            _record_event(
                req,
                event_type=EventType.ASSIGNED,
                author=actor,
                from_status=Status.IN_ANALYSIS,
                to_status=Status.IN_ANALYSIS,
                note="Atendimento assumido antes do timeout da fila pessoal.",
            )
    if expired:
        raise FilaError("O tempo para assumir este protocolo expirou.", status_code=409)
    return req


def serialize_request_brief(req: OperationalSupportRequest) -> dict[str, Any]:
    metrics = compute_support_sla_metrics(req)
    analysis_start_events = getattr(req, "analysis_start_events", None)
    if analysis_start_events is not None:
        analysis_started_at = (
            analysis_start_events[0].created_at if analysis_start_events else None
        )
    else:
        analysis_started_at = (
            req.events.filter(event_type=EventType.ASSIGNED)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
    current_histories = getattr(req.agent, "current_histories", None)
    if current_histories is not None:
        current_history = current_histories[0] if current_histories else None
    else:
        current_history = (
            req.agent.history.filter(active=True, final_date__isnull=True)
            .select_related("leader")
            .first()
        )
    timeout_remaining = None
    if req.queue_sla_started_at and req.queue_slot == 1:
        elapsed = (timezone.now() - req.queue_sla_started_at).total_seconds()
        timeout_remaining = max(0, int(FILA_TIMEOUT_SECONDS - elapsed))
    elif req.queue_slot == 1:
        timeout_remaining = 0
    return {
        "id": str(req.pk),
        "protocol": req.protocol,
        "subject": req.subject,
        "workflow": req.workflow,
        "client": req.client,
        "agent_lan_id": (req.agent.user_lan_id or "").strip(),
        "agent_name": (req.agent.full_name or "").strip(),
        "leader_name": (
            (current_history.leader.full_name or "").strip()
            if current_history and current_history.leader_id
            else ""
        ),
        "office": (current_history.location or "").strip() if current_history else "",
        "status": req.status,
        "queue_origin": req.queue_origin,
        "queue_slot": req.queue_slot,
        "assigned_at": req.assigned_at.isoformat() if req.assigned_at else None,
        "analysis_started_at": analysis_started_at.isoformat() if analysis_started_at else None,
        "queue_sla_started_at": req.queue_sla_started_at.isoformat() if req.queue_sla_started_at else None,
        "queue_priority_at": req.queue_priority_at.isoformat() if req.queue_priority_at else None,
        "timeout_restante_segundos": timeout_remaining,
        "timeout_ativo": bool(req.queue_slot == 1 and req.queue_sla_started_at),
        "sla_pct": round(metrics.pct_sla, 1),
        "sla_limit_seconds": metrics.sla_limit_seconds,
        "deadline_at": metrics.deadline_at.isoformat() if metrics.deadline_at else None,
        "leader_decided_at": req.leader_decided_at.isoformat() if req.leader_decided_at else None,
        "created_at": req.created_at.isoformat(),
    }


def list_minha_fila(*, user) -> dict[str, Any]:
    expire_stale_assignments(actor=user)
    claim_next_for_agent(user=user, actor=user)

    items = _slot_items(user.id)
    slots = []
    for index, req in enumerate(items[:MAX_ACTIVE_PER_AGENT], start=1):
        slots.append({"posicao": index, "request": serialize_request_brief(req)})
    while len(slots) < MAX_ACTIVE_PER_AGENT:
        slots.append({"posicao": len(slots) + 1, "request": None})

    primary = slots[0]["request"]
    return {
        "results": [primary] if primary else [],
        "slots": slots,
        "total": 1 if primary else 0,
        "fila_timeout_segundos": FILA_TIMEOUT_SECONDS,
        "presence": serialize_presence(get_or_create_presence(user)),
        "metricas_hoje": _agent_daily_metrics(user=user),
    }


def serialize_presence(presence: OperationalSupportAgentPresence) -> dict[str, Any]:
    return {
        "status": presence.status,
        "status_label": PRESENCE_LABELS.get(presence.status, presence.status),
        "status_changed_at": presence.status_changed_at.isoformat() if presence.status_changed_at else None,
        "last_assigned_at": presence.last_assigned_at.isoformat() if presence.last_assigned_at else None,
    }


def _location_scope_key(location: str) -> str:
    normalized = unicodedata.normalize("NFKD", (location or "").strip().casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    if "sao carlos" in normalized:
        return "sao carlos"
    if "brasilia" in normalized:
        return "brasilia"
    return normalized


def _user_location(user) -> str:
    agent = _agent_for_user(user)
    if agent is None:
        return ""
    history = (
        agent.history.filter(active=True, final_date__isnull=True)
        .order_by("-start_date")
        .only("location")
        .first()
    )
    if history is None:
        history = agent.history.order_by("-start_date").only("location").first()
    return (getattr(history, "location", "") or "").strip()


def list_presencial_agents(*, viewer) -> list[dict[str, str]]:
    viewer_scope = _location_scope_key(_user_location(viewer))
    if not viewer_scope:
        return []

    presences = (
        OperationalSupportAgentPresence.objects.filter(
            status=OperationalSupportAgentPresence.STATUS_PRESENCIAL,
            user__is_active=True,
        )
        .select_related("user", "user__profile__agent")
        .order_by("user__first_name", "user__last_name", "user__username")
    )
    results = []
    for presence in presences:
        if _location_scope_key(_user_location(presence.user)) != viewer_scope:
            continue
        results.append(
            {
                "user_id": str(presence.user_id),
                "name": user_display_name(presence.user),
            }
        )
    return results


@transaction.atomic
def direcionar_solicitacao(
    *,
    actor,
    agent_id,
    request_id=None,
    justificativa: str = "",
) -> OperationalSupportRequest:
    if not user_has_permission(actor, R.QUAL_CAPACITACAO_SUPORTE_ASSIGN):
        raise FilaError("Sem permissão para direcionar.", status_code=403)

    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise FilaError("Justificativa obrigatória.")

    try:
        agent = User.objects.filter(pk=agent_id, is_active=True).first()
    except (TypeError, ValueError, ValidationError):
        agent = None
    if not agent:
        raise FilaError("Agente de suporte inválido.")

    active = _slot_items(agent.id)
    if len(active) >= MAX_ACTIVE_PER_AGENT:
        raise FilaError("A fila pessoal do agente está cheia (2 posições).")

    slot = 2 if active else 1
    start_sla = slot == 1

    if request_id:
        req = (
            OperationalSupportRequest.objects.select_for_update()
            .filter(
                pk=request_id,
                request_type=RequestType.ONLINE,
                status=Status.PENDING_SUPPORT,
                assignee__isnull=True,
            )
            .first()
        )
    else:
        candidate = _next_unassigned(agent_id=agent.id)
        req = (
            OperationalSupportRequest.objects.select_for_update().filter(pk=candidate.pk).first()
            if candidate
            else None
        )
    if not req:
        raise FilaError("Solicitação indisponível na fila geral.")

    return _assign_to_agent(
        req,
        agent=agent,
        actor=actor,
        slot=slot,
        origin=QueueOrigin.DIRECTED,
        start_sla=start_sla,
        event_type=EventType.QUEUE_DIRECTED,
        note=justificativa,
    )


@transaction.atomic
def priorizar_solicitacao(*, actor, request_id) -> OperationalSupportRequest:
    if not user_has_permission(actor, R.QUAL_CAPACITACAO_SUPORTE_ASSIGN):
        raise FilaError("Sem permissão para priorizar.", status_code=403)

    req = (
        OperationalSupportRequest.objects.select_for_update()
        .filter(
            pk=request_id,
            request_type=RequestType.ONLINE,
            status=Status.PENDING_SUPPORT,
            assignee__isnull=True,
        )
        .first()
    )
    if not req:
        raise FilaError("Solicitação indisponível na fila geral.", status_code=409)

    req.queue_priority_at = timezone.now()
    _save_request(req, update_fields=["queue_priority_at", "updated_at"])
    _record_event(
        req,
        event_type=EventType.QUEUE_PRIORITIZED,
        author=actor,
        from_status=Status.PENDING_SUPPORT,
        to_status=Status.PENDING_SUPPORT,
        note="Protocolo priorizado para ser o próximo da fila geral.",
    )
    return req


@transaction.atomic
def remover_direcionado(*, user, request_id) -> dict[str, Any]:
    req = (
        OperationalSupportRequest.objects.select_for_update()
        .filter(
            pk=request_id,
            assignee=user,
            request_type=RequestType.ONLINE,
            status=Status.IN_ANALYSIS,
            queue_origin=QueueOrigin.DIRECTED,
            queue_slot=2,
        )
        .first()
    )
    if not req:
        raise FilaError("Somente solicitação direcionada na posição 2 pode ser removida.")
    _release_request(
        req,
        actor=user,
        event_type=EventType.QUEUE_RELEASED,
        note="Remoção manual de direcionamento.",
    )
    return {"removed": True, "id": str(req.pk)}


def build_controle_operacoes() -> dict[str, Any]:
    expire_stale_assignments()
    now = timezone.now()

    fila_geral = rank_online_queue_requests(list(_online_unassigned_qs()[:100]), now=now)
    fila_payload = [serialize_request_brief(req) for req in fila_geral[:50]]

    presences = (
        OperationalSupportAgentPresence.objects.select_related("user", "user__profile__agent")
        .filter(user__is_active=True)
        .order_by("user__first_name", "user__username")
    )
    user_ids = list(presences.values_list("user_id", flat=True))
    daily_metrics = _agents_daily_metrics(user_ids=user_ids, now=now)
    agents_payload = []
    for presence in presences:
        slots = _slot_items(presence.user_id)
        metrics = daily_metrics.get(
            presence.user_id,
            {"respondidas_hoje": 0, "sla_medio_hoje_pct": None, "timeouts_hoje": 0},
        )
        agents_payload.append(
            {
                "user_id": presence.user_id,
                "name": user_display_name(presence.user),
                "username": presence.user.username,
                "presence": serialize_presence(presence),
                "active_count": len(slots),
                **metrics,
                "slots": [serialize_request_brief(item) for item in slots],
            }
        )

    return {
        "generated_at": now.isoformat(),
        "fila_timeout_segundos": FILA_TIMEOUT_SECONDS,
        "kpis": {
            "fila_geral": len(fila_geral),
            "em_analise": OperationalSupportRequest.objects.filter(
                request_type=RequestType.ONLINE,
                status=Status.IN_ANALYSIS,
            ).count(),
            "respondidas_hoje": _answered_today_count(now),
            "sla_medio_hoje_pct": _sla_average_today_pct(now),
            "online_agents": presences.filter(
                status=OperationalSupportAgentPresence.STATUS_ONLINE
            ).count(),
        },
        "fila_geral": fila_payload,
        "agentes": agents_payload,
    }


def _answered_today_count(now) -> int:
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return OperationalSupportRequest.objects.filter(
        request_type=RequestType.ONLINE,
        status=Status.ANSWERED,
        answered_at__gte=day_start,
        answered_at__lt=day_end,
    ).count()


def _sla_average_today_pct(now) -> float | None:
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    answered = list(
        OperationalSupportRequest.objects.filter(
            request_type=RequestType.ONLINE,
            status=Status.ANSWERED,
            answered_at__gte=day_start,
            answered_at__lt=day_end,
        )
    )
    if not answered:
        return None

    sla_map = load_support_sla_map()
    values = []
    for req in answered:
        metrics = compute_support_sla_metrics(req, sla_map=sla_map, now=req.answered_at or now)
        if metrics.sla_limit_seconds is not None:
            values.append(metrics.pct_sla)
    return round(sum(values) / len(values), 1) if values else None


def _agent_daily_metrics(*, user, now=None) -> dict[str, int | float | None]:
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    answered = list(
        OperationalSupportRequest.objects.filter(
            request_type=RequestType.ONLINE,
            status=Status.ANSWERED,
            answered_by_id=user.id,
            answered_at__gte=day_start,
            answered_at__lt=day_end,
        )
    )
    if not answered:
        return {
            "realizados": 0,
            "sla_medio_pct": None,
            "tempo_medio_analise_segundos": None,
        }

    analysis_started_by_request = dict(
        OperationalSupportEvent.objects.filter(
            request_id__in=[req.id for req in answered],
            event_type=EventType.ASSIGNED,
        )
        .order_by("request_id", "created_at")
        .values_list("request_id", "created_at")
    )
    analysis_durations = [
        max(0, int((req.answered_at - analysis_started_by_request[req.id]).total_seconds()))
        for req in answered
        if req.answered_at and req.id in analysis_started_by_request
    ]

    sla_map = load_support_sla_map()
    metrics = [
        compute_support_sla_metrics(
            req,
            sla_map=sla_map,
            now=req.answered_at or now,
        )
        for req in answered
    ]
    sla_values = [item.pct_sla for item in metrics if item.sla_limit_seconds is not None]
    return {
        "realizados": len(answered),
        "sla_medio_pct": (
            round(sum(sla_values) / len(sla_values), 1)
            if sla_values
            else None
        ),
        "tempo_medio_analise_segundos": (
            round(sum(analysis_durations) / len(analysis_durations))
            if analysis_durations
            else None
        ),
    }


def _agents_daily_metrics(*, user_ids: list, now=None) -> dict:
    if not user_ids:
        return {}
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    answered = list(
        OperationalSupportRequest.objects.filter(
            request_type=RequestType.ONLINE,
            status=Status.ANSWERED,
            answered_by_id__in=user_ids,
            answered_at__gte=day_start,
            answered_at__lt=day_end,
        )
    )
    result = {
        user_id: {"respondidas_hoje": 0, "sla_medio_hoje_pct": None, "timeouts_hoje": 0}
        for user_id in user_ids
    }
    sla_map = load_support_sla_map() if answered else {}
    sla_by_user: dict[int, list[float]] = {user_id: [] for user_id in user_ids}
    for req in answered:
        user_id = req.answered_by_id
        if not user_id or user_id not in result:
            continue
        result[user_id]["respondidas_hoje"] += 1
        metrics = compute_support_sla_metrics(req, sla_map=sla_map, now=req.answered_at or now)
        if metrics.sla_limit_seconds is not None:
            sla_by_user[user_id].append(metrics.pct_sla)
    for user_id, values in sla_by_user.items():
        if values:
            result[user_id]["sla_medio_hoje_pct"] = round(sum(values) / len(values), 1)

    timeout_events = OperationalSupportEvent.objects.filter(
        event_type=EventType.QUEUE_TIMEOUT,
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).values_list("request_id", "snapshot")
    user_id_by_text = {str(user_id): user_id for user_id in user_ids}
    timeout_requests_by_user: dict = {user_id: set() for user_id in user_ids}
    for request_id, snapshot in timeout_events:
        raw_user_id = (snapshot or {}).get("context", {}).get("released_assignee_id")
        user_id = user_id_by_text.get(str(raw_user_id))
        if user_id is not None:
            timeout_requests_by_user[user_id].add(request_id)
    for user_id, request_ids in timeout_requests_by_user.items():
        result[user_id]["timeouts_hoje"] = len(request_ids)
    return result


def on_answer_completed(*, req: OperationalSupportRequest, actor) -> None:
    if req.request_type != RequestType.ONLINE or not req.assignee_id:
        return
    _activate_slot_two(req.assignee_id, actor=actor)
    presence = OperationalSupportAgentPresence.objects.filter(user_id=req.assignee_id).first()
    if presence and presence.status == OperationalSupportAgentPresence.STATUS_ONLINE:
        claim_next_for_agent(user=req.assignee, actor=actor)
