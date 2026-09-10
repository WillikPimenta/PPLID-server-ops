"""Workflow de solicitações de suporte operacional."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.access import registry as R
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.escala_flex.services.permissions import get_agent_for_user
from apps.portal_notifications.models import PortalNotification
from apps.portal_notifications.services import emit_notification_event, users_for_lan_ids
from apps.workforce.models import Agent

from ..categories import is_valid_category
from ..models import OperationalSupportEvent, OperationalSupportRequest

Status = OperationalSupportRequest.Status
RequesterType = OperationalSupportRequest.RequesterType
RequestType = OperationalSupportRequest.RequestType
QueueOrigin = OperationalSupportRequest.QueueOrigin
LeaderDecision = OperationalSupportRequest.LeaderDecision
EventType = OperationalSupportEvent.EventType

FINAL_STATUSES = frozenset(
    {
        Status.ANSWERED,
        Status.REJECTED_LEADER,
        Status.CANCELLED,
    }
)
CANCELABLE_STATUSES = frozenset(
    {Status.PENDING_LEADER, Status.PENDING_SUPPORT, Status.PENDING_OFFLINE}
)


def _post_approval_status(request_type: str) -> str:
    if request_type == RequestType.ONLINE:
        return Status.PENDING_SUPPORT
    if request_type == RequestType.PRESENCIAL:
        return Status.IN_ANALYSIS
    return Status.PENDING_OFFLINE


def _approval_destination_label(request_type: str) -> str:
    if request_type == RequestType.ONLINE:
        return "a fila online da Qualidade"
    if request_type == RequestType.PRESENCIAL:
        return "o agente de suporte presencial indicado"
    return "o suporte offline"


class WorkflowError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _access_bypass(user) -> bool:
    return bool(resolve_user_access(user).get("bypass"))


def is_leader_actor(user) -> bool:
    if _access_bypass(user):
        return True
    return user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER)


def is_agent_only_actor(user) -> bool:
    """Agente operacional sem poder de aprovação de líder."""
    if _access_bypass(user):
        return False
    if not user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_CREATE):
        return False
    return not user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER)


def can_manage_support_answers(user) -> bool:
    """Gerência/admin: pode responder mesmo sem ser o assignee."""
    if _access_bypass(user):
        return True
    from apps.access.constants import ROLE_ADM_PORTAL, ROLE_QUAL_GERENCIA

    roles = set(resolve_user_access(user).get("roles") or [])
    if ROLE_QUAL_GERENCIA in roles or ROLE_ADM_PORTAL in roles:
        return user_has_permission(user, R.QUAL_CAPACITACAO_SUPORTE_ANSWER)
    return False


def _isoformat(value) -> str | None:
    return value.isoformat() if value else None


def _user_snapshot(user) -> dict | None:
    if not user:
        return None
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return {
        "id": str(user.pk),
        "username": getattr(user, "username", "") or "",
        "name": name or getattr(user, "username", "") or "",
    }


def _actor_roles(user) -> list[str]:
    if not user:
        return []
    roles = resolve_user_access(user).get("roles") or []
    return sorted({str(role) for role in roles if role})


def _agent_snapshot(request_obj: OperationalSupportRequest) -> dict:
    agent = request_obj.agent
    current_history = (
        agent.history.filter(active=True, final_date__isnull=True)
        .select_related("leader")
        .order_by("-start_date")
        .first()
    )
    leader = current_history.leader if current_history and current_history.leader_id else None
    return {
        "id": str(agent.pk),
        "lan_id": agent.user_lan_id or "",
        "name": agent.full_name or "",
        "leader_id": str(leader.pk) if leader else None,
        "leader_lan_id": leader.user_lan_id if leader else "",
        "leader_name": leader.full_name if leader else "",
        "office": current_history.location if current_history else "",
        "team": current_history.team if current_history else "",
        "team_sector": current_history.team_sector if current_history else "",
    }


def _request_snapshot(request_obj: OperationalSupportRequest, *, actor, actor_roles: list[str]) -> dict:
    return {
        "schema_version": 1,
        "request": {
            "id": str(request_obj.pk),
            "protocol": request_obj.protocol,
            "workflow": request_obj.workflow,
            "client": request_obj.client,
            "subject": request_obj.subject,
            "category": request_obj.category,
            "description": request_obj.description,
            "reference": request_obj.reference,
            "status": request_obj.status,
            "requester_type": request_obj.requester_type,
            "operation_origin": request_obj.operation_origin,
            "request_type": request_obj.request_type,
            "agent": _agent_snapshot(request_obj),
            "requester": _user_snapshot(request_obj.requester),
            "leader_decision": {
                "decider": _user_snapshot(request_obj.leader_decider),
                "decision": request_obj.leader_decision,
                "justification": request_obj.leader_justification,
                "decided_at": _isoformat(request_obj.leader_decided_at),
                "auto_approved": request_obj.auto_approved,
            },
            "assignment": {
                "assignee": _user_snapshot(request_obj.assignee),
                "assigned_at": _isoformat(request_obj.assigned_at),
            },
            "answer": {
                "text": request_obj.answer,
                "option": request_obj.answer_option,
                "difficulty_level": request_obj.difficulty_level,
                "document_uf": request_obj.document_uf,
                "document_type": request_obj.document_type,
                "answered_by": _user_snapshot(request_obj.answered_by),
                "answered_at": _isoformat(request_obj.answered_at),
            },
            "cancellation": {
                "cancelled_by": _user_snapshot(request_obj.cancelled_by),
                "reason": request_obj.cancel_reason,
                "cancelled_at": _isoformat(request_obj.cancelled_at),
            },
            "created_at": _isoformat(request_obj.created_at),
            "updated_at": _isoformat(request_obj.updated_at),
        },
        "actor": {
            **(_user_snapshot(actor) or {"id": None, "username": "", "name": ""}),
            "roles": actor_roles,
        },
    }


def _save_request(request_obj: OperationalSupportRequest, *, update_fields: list[str]) -> None:
    request_obj._allow_workflow_update = True
    try:
        request_obj.save(update_fields=update_fields)
    finally:
        request_obj._allow_workflow_update = False


def _notify_request_participants(
    request_obj: OperationalSupportRequest,
    *,
    actor,
    title: str,
    message: str,
    kind: str,
    event_key: str,
) -> None:
    recipients = [request_obj.requester]
    recipients.extend(users_for_lan_ids([request_obj.agent.user_lan_id]))
    catalog_event_key = {
        "leader-approved": "matrix.n035",
        "leader-rejected": "matrix.n036",
        "answered": "matrix.n038",
    }.get(event_key)
    if not catalog_event_key:
        raise ValueError(f"Evento de notificação não mapeado: {event_key}")
    emit_notification_event(
        event_key=catalog_event_key,
        recipients_by_type={"participants": recipients},
        context={"assunto": request_obj.subject, "solicitacao_id": str(request_obj.pk)},
        exclude_user=actor,
        fallback_recipients=recipients,
        fallback_title=title,
        fallback_message=message,
        fallback_kind=kind,
        fallback_target_url="/operacao/suporte-operacional",
        source_type="operational_support",
        source_id=str(request_obj.pk),
        dedupe_key=f"operational-support:{request_obj.pk}:{event_key}",
    )


def _record_event(
    request_obj: OperationalSupportRequest,
    *,
    event_type: str,
    author,
    from_status: str,
    to_status: str,
    note: str = "",
    context: dict | None = None,
) -> OperationalSupportEvent:
    actor_snapshot = _user_snapshot(author) or {}
    roles = _actor_roles(author)
    last_sequence = request_obj.events.aggregate(value=Max("sequence"))["value"] or 0
    snapshot = _request_snapshot(request_obj, actor=author, actor_roles=roles)
    if context:
        snapshot["context"] = context
    return OperationalSupportEvent.objects.create(
        request=request_obj,
        sequence=last_sequence + 1,
        event_version=1,
        event_type=event_type,
        author=author,
        actor_username=actor_snapshot.get("username", ""),
        actor_name=actor_snapshot.get("name", ""),
        actor_roles=roles,
        from_status=from_status or "",
        to_status=to_status or "",
        note=(note or "").strip(),
        snapshot=snapshot,
    )


def create_request(
    *,
    user,
    agent: Agent,
    subject: str,
    category: str,
    description: str,
    reference: str = "",
    protocol: str = "",
    workflow: str = "",
    client: str = "",
    workflow_id: int | None = None,
    cliente_id: int | None = None,
    operation_origin: str = OperationalSupportRequest.OperationOrigin.FRAUD,
    request_type: str = RequestType.ONLINE,
    presencial_support_user=None,
) -> OperationalSupportRequest:
    if not user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_CREATE) and not _access_bypass(user):
        raise WorkflowError("Sem permissão para criar solicitação.", status_code=403)

    subject = (subject or "").strip()
    description = (description or "").strip()
    category = (category or "").strip().lower()
    reference = (reference or "").strip()
    protocol = (protocol or "").strip()
    workflow = (workflow or "").strip()
    client = (client or "").strip()
    operation_origin = (operation_origin or "").strip().lower()
    request_type = (request_type or "").strip().lower()

    if operation_origin not in OperationalSupportRequest.OperationOrigin.values:
        raise WorkflowError("Origem da operação inválida.")
    if operation_origin == OperationalSupportRequest.OperationOrigin.CONFER:
        workflow = OperationalSupportRequest.CONFER_WORKFLOW
        client = OperationalSupportRequest.CONFER_CLIENT
        subject = OperationalSupportRequest.CONFER_SUBJECT
        workflow_id = None
        cliente_id = None

    if not subject:
        raise WorkflowError("Informe o assunto.")
    if not description:
        raise WorkflowError("Informe a descrição.")
    if not is_valid_category(category):
        raise WorkflowError("Categoria inválida.")
    if request_type not in RequestType.values:
        raise WorkflowError("Tipo de solicitação inválido.")
    if request_type == RequestType.PRESENCIAL and not presencial_support_user:
        raise WorkflowError("Informe o agente de suporte presencial.")
    if not agent.active:
        raise WorkflowError("O agente selecionado não está ativo.")

    leader_mode = is_leader_actor(user)
    own_agent = get_agent_for_user(user)

    if not leader_mode:
        if not own_agent or own_agent.pk != agent.pk:
            raise WorkflowError("Agente só pode abrir solicitação para si.", status_code=403)
        requester_type = RequesterType.AGENT
        status = Status.PENDING_LEADER
        auto_approved = False
    else:
        requester_type = RequesterType.LEADER
        status = _post_approval_status(request_type)
        auto_approved = True

    now = timezone.now()
    assignee = None
    assigned_at = None
    if request_type == RequestType.PRESENCIAL and auto_approved:
        assignee = presencial_support_user
        assigned_at = now

    with transaction.atomic():
        req = OperationalSupportRequest.objects.create(
            agent=agent,
            requester=user,
            requester_type=requester_type,
            operation_origin=operation_origin,
            request_type=request_type,
            protocol=protocol,
            workflow=workflow,
            client=client,
            workflow_id=workflow_id,
            cliente_id=cliente_id,
            subject=subject,
            category=category,
            description=description,
            reference=reference,
            status=status,
            auto_approved=auto_approved,
            leader_decider=user if auto_approved else None,
            leader_decision=LeaderDecision.APPROVED if auto_approved else "",
            leader_justification="Aprovação automática (abertura pelo líder)." if auto_approved else "",
            leader_decided_at=now if auto_approved else None,
            presencial_support_user=presencial_support_user,
            assignee=assignee,
            assigned_at=assigned_at,
        )
        _record_event(
            req,
            event_type=EventType.CREATED,
            author=user,
            from_status="",
            to_status=status,
            note="Solicitação criada.",
        )
        if auto_approved:
            _record_event(
                req,
                event_type=EventType.AUTO_APPROVED,
                author=user,
                from_status=Status.PENDING_LEADER,
                to_status=status,
                note="Aprovação automática na abertura pelo líder.",
            )
            if request_type == RequestType.PRESENCIAL and assignee:
                _record_event(
                    req,
                    event_type=EventType.ASSIGNED,
                    author=user,
                    from_status=status,
                    to_status=status,
                    note="Encaminhada ao agente de suporte presencial indicado.",
                )
    return req


def leader_decision(
    *,
    user,
    request_id,
    approved: bool,
    justification: str = "",
) -> OperationalSupportRequest:
    if not user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER) and not _access_bypass(user):
        raise WorkflowError("Sem permissão para decidir como líder.", status_code=403)

    justification = (justification or "").strip()
    if not approved and not justification:
        raise WorkflowError("Justificativa obrigatória na recusa.")

    with transaction.atomic():
        req = OperationalSupportRequest.objects.select_for_update().get(pk=request_id)
        if req.status != Status.PENDING_LEADER:
            raise WorkflowError("Somente solicitações aguardando líder podem ser decididas.")

        now = timezone.now()
        from_status = req.status
        if approved:
            req.status = _post_approval_status(req.request_type)
            if req.request_type == RequestType.PRESENCIAL:
                req.assignee = req.presencial_support_user
                req.assigned_at = now
            req.leader_decision = LeaderDecision.APPROVED
            req.leader_justification = justification
            event_type = EventType.APPROVED
            note = justification or "Aprovada pelo líder."
        else:
            req.status = Status.REJECTED_LEADER
            req.leader_decision = LeaderDecision.REJECTED
            req.leader_justification = justification
            event_type = EventType.REJECTED
            note = justification

        req.leader_decider = user
        req.leader_decided_at = now
        req.auto_approved = False
        update_fields = [
            "status",
            "leader_decision",
            "leader_justification",
            "leader_decider",
            "leader_decided_at",
            "auto_approved",
            "updated_at",
        ]
        if approved and req.request_type == RequestType.PRESENCIAL:
            update_fields.extend(["assignee", "assigned_at"])
        _save_request(
            req,
            update_fields=update_fields,
        )
        _record_event(
            req,
            event_type=event_type,
            author=user,
            from_status=from_status,
            to_status=req.status,
            note=note,
        )
        if approved:
            destination = _approval_destination_label(req.request_type)
            _notify_request_participants(
                req,
                actor=user,
                title="Solicitação aprovada pelo líder",
                message=f'A dúvida "{req.subject}" foi encaminhada para {destination}.',
                kind=PortalNotification.Kind.SUCCESS,
                event_key="leader-approved",
            )
            if req.request_type == RequestType.PRESENCIAL and req.assignee_id:
                _record_event(
                    req,
                    event_type=EventType.ASSIGNED,
                    author=user,
                    from_status=from_status,
                    to_status=req.status,
                    note="Encaminhada ao agente de suporte presencial indicado.",
                )
            if req.request_type == RequestType.ONLINE:
                from ..models import OperationalSupportAgentPresence
                from .fila_online import claim_next_for_agent, expire_stale_assignments

                expire_stale_assignments(actor=user)
                for presence in OperationalSupportAgentPresence.objects.filter(
                    status=OperationalSupportAgentPresence.STATUS_ONLINE
                ).select_related("user"):
                    claim_next_for_agent(user=presence.user, actor=user)
        else:
            _notify_request_participants(
                req,
                actor=user,
                title="Solicitação recusada pelo líder",
                message=f'A dúvida "{req.subject}" não foi encaminhada para a Qualidade.',
                kind=PortalNotification.Kind.WARNING,
                event_key="leader-rejected",
            )
    return req


def cancel_request(*, user, request_id, reason: str) -> OperationalSupportRequest:
    if not user_has_permission(user, R.OPERACAO_SUPORTE_OPERACIONAL_CANCEL) and not _access_bypass(user):
        raise WorkflowError("Sem permissão para cancelar.", status_code=403)

    reason = (reason or "").strip()
    if not reason:
        raise WorkflowError("Motivo de cancelamento obrigatório.")

    with transaction.atomic():
        req = OperationalSupportRequest.objects.select_for_update().get(pk=request_id)
        if req.status not in CANCELABLE_STATUSES:
            raise WorkflowError("Cancelamento permitido apenas antes da atribuição ao suporte.")

        own_agent = get_agent_for_user(user)
        is_leader = is_leader_actor(user)
        if not is_leader and not _access_bypass(user):
            if not own_agent or own_agent.pk != req.agent_id:
                raise WorkflowError("Agente só pode cancelar a própria solicitação.", status_code=403)

        from_status = req.status
        req.status = Status.CANCELLED
        req.cancelled_by = user
        req.cancel_reason = reason
        req.cancelled_at = timezone.now()
        _save_request(
            req,
            update_fields=[
                "status",
                "cancelled_by",
                "cancel_reason",
                "cancelled_at",
                "updated_at",
            ]
        )
        _record_event(
            req,
            event_type=EventType.CANCELLED,
            author=user,
            from_status=from_status,
            to_status=Status.CANCELLED,
            note=reason,
        )
    return req


def assign_request(*, user, request_id) -> OperationalSupportRequest:
    """Assumir manualmente — apenas suporte offline."""
    if not user_has_permission(user, R.QUAL_CAPACITACAO_SUPORTE_ASSIGN) and not _access_bypass(user):
        raise WorkflowError("Sem permissão para assumir solicitação.", status_code=403)

    with transaction.atomic():
        req = OperationalSupportRequest.objects.select_for_update().get(pk=request_id)
        if req.request_type != RequestType.OFFLINE or req.status != Status.PENDING_OFFLINE:
            raise WorkflowError("Somente solicitações offline pendentes podem ser assumidas manualmente.")

        from_status = req.status
        req.status = Status.IN_ANALYSIS
        req.assignee = user
        req.assigned_at = timezone.now()
        _save_request(
            req,
            update_fields=["status", "assignee", "assigned_at", "updated_at"],
        )
        _record_event(
            req,
            event_type=EventType.ASSIGNED,
            author=user,
            from_status=from_status,
            to_status=Status.IN_ANALYSIS,
            note="Solicitação assumida pelo suporte (offline).",
        )
    return req


def answer_request(
    *,
    user,
    request_id,
    answer: str,
    difficulty_level: str,
    document_uf: str,
    document_type: str,
    answer_option: str = "",
) -> OperationalSupportRequest:
    if not user_has_permission(user, R.QUAL_CAPACITACAO_SUPORTE_ANSWER) and not _access_bypass(user):
        raise WorkflowError("Sem permissão para responder.", status_code=403)

    answer = (answer or "").strip()
    answer_option = (answer_option or "").strip()
    difficulty_level = (difficulty_level or "").strip()
    document_uf = (document_uf or "").strip()
    document_type = (document_type or "").strip()
    if not answer:
        raise WorkflowError("Informe os detalhes da resposta.")
    if difficulty_level not in OperationalSupportRequest.DifficultyLevel.values:
        raise WorkflowError("Informe um nível de dificuldade válido.")
    if not document_uf:
        raise WorkflowError("Informe a UF do documento.")
    if not document_type:
        raise WorkflowError("Informe o tipo de documento.")

    with transaction.atomic():
        req = OperationalSupportRequest.objects.select_for_update().get(pk=request_id)
        if req.status != Status.IN_ANALYSIS:
            raise WorkflowError("Somente solicitações em análise podem ser respondidas.")
        if req.answered_at or req.answer:
            raise WorkflowError("Resposta já registrada.")
        if req.assignee_id != user.id and not can_manage_support_answers(user) and not _access_bypass(user):
            raise WorkflowError("Somente o atendente responsável pode responder.", status_code=403)

        from_status = req.status
        req.status = Status.ANSWERED
        req.answer = answer
        req.answer_option = answer_option
        req.difficulty_level = difficulty_level
        req.document_uf = document_uf
        req.document_type = document_type
        req.answered_by = user
        req.answered_at = timezone.now()
        _save_request(
            req,
            update_fields=[
                "status",
                "answer",
                "answer_option",
                "difficulty_level",
                "document_uf",
                "document_type",
                "answered_by",
                "answered_at",
                "updated_at",
            ]
        )
        _record_event(
            req,
            event_type=EventType.ANSWERED,
            author=user,
            from_status=from_status,
            to_status=Status.ANSWERED,
            note=answer,
        )
        _notify_request_participants(
            req,
            actor=user,
            title="Resposta da solicitação disponível",
            message=f'A Qualidade respondeu à dúvida "{req.subject}".',
            kind=PortalNotification.Kind.SUCCESS,
            event_key="answered",
        )
        from .fila_online import on_answer_completed

        on_answer_completed(req=req, actor=user)
    return req
