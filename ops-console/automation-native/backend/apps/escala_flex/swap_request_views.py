"""API de solicitações de troca de escala."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.portal_notifications.models import PortalNotification
from apps.portal_notifications.services import emit_notification_event, users_for_lan_ids
from apps.workforce.models import Agent, AgentHistory

from .models import RequestType, ScheduleRequest
from .serializers import (
    SwapRequestApprovalSerializer,
    SwapRequestCreateSerializer,
    SwapRequestSerializer,
)
from .services.permissions import OperationalProfile, build_operational_profile
from .services.swap_request_application import SwapApplicationError, apply_approved_swap
from .services.swap_request_stats import build_swap_request_dashboard
from .services.swap_request_validation import serialize_validation_result, validate_swap_request_data
from .services.swap_request_workflow import (
    SWAP_REQUEST_TYPE_NAME,
    apply_leader_auto_approval,
    can_approve_swap_leader,
    can_approve_swap_plan,
    can_create_swap_request,
    should_auto_approve_leader,
    should_finalize_peer_after_leader,
)


def _parse_date(value: str | None):
    if not value:
        return None
    from datetime import datetime

    return datetime.strptime(value, "%Y-%m-%d").date()


def _get_swap_request_type() -> RequestType:
    request_type, _ = RequestType.objects.get_or_create(
        pk=1,
        defaults={"name": SWAP_REQUEST_TYPE_NAME, "active": True},
    )
    if request_type.name != SWAP_REQUEST_TYPE_NAME:
        request_type.name = SWAP_REQUEST_TYPE_NAME
        request_type.active = True
        request_type.save(update_fields=["name", "active"])
    return request_type


def _agent_name_map(lan_ids: set[str]) -> dict[str, str]:
    if not lan_ids:
        return {}
    return {
        row["user_lan_id"].lower(): row["full_name"]
        for row in Agent.objects.filter(user_lan_id__in=lan_ids).values("user_lan_id", "full_name")
    }


def _agent_activity_map(lan_ids: set[str]) -> dict[str, str]:
    if not lan_ids:
        return {}
    return {
        row["agent__user_lan_id"].lower(): str(row["job_activity"] or "").strip()
        for row in AgentHistory.objects.filter(
            agent__user_lan_id__in=lan_ids,
            active=True,
            final_date__isnull=True,
        ).values("agent__user_lan_id", "job_activity")
    }


def _scope_swap_requests(qs, profile: OperationalProfile):
    if profile.is_admin or profile.team == "Planejamento":
        return qs
    if not profile.lan_id:
        return qs.none()

    lan = profile.lan_id.lower()
    if profile.is_agent_backoffice:
        return qs.filter(
            Q(agent_lan_id__iexact=lan)
            | Q(agent_lan_id_2__iexact=lan)
            | Q(applicant_lan_id__iexact=lan)
        )

    return qs.filter(
        Q(agent_lan_id__iexact=lan)
        | Q(agent_lan_id_2__iexact=lan)
        | Q(applicant_lan_id__iexact=lan)
        | Q(
            agent_lan_id__in=Agent.objects.filter(
                history__leader__user_lan_id__iexact=lan,
                history__active=True,
                history__final_date__isnull=True,
            ).values_list("user_lan_id", flat=True)
        )
    ).distinct()


def _serialize_requests(requests, profile: OperationalProfile):
    lan_ids = set()
    for item in requests:
        for field in ("agent_lan_id", "agent_lan_id_2", "applicant_lan_id", "approver_leader_lan_id", "approver_plan_lan_id"):
            value = (getattr(item, field) or "").strip().lower()
            if value:
                lan_ids.add(value)
    names = _agent_name_map(lan_ids)
    activities = _agent_activity_map(lan_ids)
    return SwapRequestSerializer(
        requests,
        many=True,
        context={"profile": profile, "agent_names": names, "agent_activities": activities},
    ).data


def _notify_swap_participants(swap, *, actor, title: str, message: str, kind: str, event_key: str):
    recipients = users_for_lan_ids(
        [swap.applicant_lan_id, swap.agent_lan_id, swap.agent_lan_id_2]
    )
    catalog_event_key = {
        "leader-rejected": "matrix.n026",
        "leader-approved-final": "matrix.n027",
        "leader-approved": "matrix.n028",
        "planning-approved": "matrix.n029",
        "planning-rejected": "matrix.n030",
    }[event_key]
    emit_notification_event(
        event_key=catalog_event_key,
        recipients_by_type={"participants": recipients},
        context={"solicitacao_id": str(swap.pk)},
        exclude_user=actor,
        fallback_recipients=recipients,
        fallback_title=title,
        fallback_message=message,
        fallback_kind=kind,
        fallback_target_url="/operacao/trocas",
        source_type="swap_request",
        source_id=str(swap.pk),
        dedupe_key=f"swap-request:{swap.pk}:{event_key}",
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def swap_requests_view(request):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Perfil operacional não encontrado."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        qs = ScheduleRequest.objects.select_related("request_type").order_by("-date_request", "-created_at")
        qs = qs.filter(request_type=_get_swap_request_type())

        if date_param := request.query_params.get("date"):
            qs = qs.filter(date_swap=_parse_date(date_param))

        workflow = request.query_params.get("workflow")
        if workflow == "pending_leader":
            qs = qs.filter(approved_leader__isnull=True)
        elif workflow == "pending_plan":
            qs = qs.filter(approved_leader=True, approved__isnull=True)
        elif workflow == "approved":
            qs = qs.filter(approved=True)
        elif workflow == "rejected":
            qs = qs.filter(Q(approved_leader=False) | Q(approved=False))

        qs = _scope_swap_requests(qs, profile)
        return Response({"results": _serialize_requests(qs, profile)})

    serializer = SwapRequestCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    agent_lan_id = data["agent_lan_id"].strip()

    if not can_create_swap_request(profile, agent_lan_id, user=request.user):
        return Response(
            {"detail": "Sem permissão para solicitar troca para este agente."},
            status=status.HTTP_403_FORBIDDEN,
        )

    validation = validate_swap_request_data(
        swap_kind=data["swap_kind"],
        agent_lan_id=agent_lan_id,
        date_swap=data["date_swap"],
        agent_lan_id_2=(data.get("agent_lan_id_2") or "").strip(),
        new_journey=(data.get("new_journey") or "").strip(),
    )
    if not validation.is_valid:
        return Response(
            {"detail": " ".join(validation.errors), "errors": validation.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    swap = ScheduleRequest(
        request_type=_get_swap_request_type(),
        swap_kind=data["swap_kind"],
        agent_lan_id=agent_lan_id,
        agent_lan_id_2=(data.get("agent_lan_id_2") or "").strip(),
        applicant_lan_id=profile.lan_id or "",
        date_swap=data["date_swap"],
        date_request=timezone.now(),
        description=(data.get("description") or "").strip(),
        new_journey=(data.get("new_journey") or "").strip(),
        validation_days=validation.validation_days,
        validation_hour=validation.validation_hour,
        validation_activity=validation.validation_activity,
    )

    if should_auto_approve_leader(profile, agent_lan_id):
        apply_leader_auto_approval(swap, profile)

    try:
        with transaction.atomic():
            if swap.approved_leader is True and should_finalize_peer_after_leader(swap):
                apply_approved_swap(swap)
                swap.approved = True
            swap.save()
    except SwapApplicationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        _serialize_requests([swap], profile)[0],
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def swap_requests_validate(request):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Perfil operacional não encontrado."}, status=status.HTTP_403_FORBIDDEN)

    serializer = SwapRequestCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    agent_lan_id = data["agent_lan_id"].strip()

    if not can_create_swap_request(profile, agent_lan_id, user=request.user):
        return Response(
            {"detail": "Sem permissão para solicitar troca para este agente."},
            status=status.HTTP_403_FORBIDDEN,
        )

    validation = validate_swap_request_data(
        swap_kind=data["swap_kind"],
        agent_lan_id=agent_lan_id,
        date_swap=data["date_swap"],
        agent_lan_id_2=(data.get("agent_lan_id_2") or "").strip(),
        new_journey=(data.get("new_journey") or "").strip(),
    )
    return Response(serialize_validation_result(validation))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def swap_request_leader_approve(request, pk):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Perfil operacional não encontrado."}, status=status.HTTP_403_FORBIDDEN)

    serializer = SwapRequestApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    approved = serializer.validated_data["approved"]
    try:
        with transaction.atomic():
            swap = ScheduleRequest.objects.select_for_update().filter(pk=pk).first()
            if swap is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not can_approve_swap_leader(profile, swap):
                return Response(
                    {"detail": "Sem permissão para aprovar esta solicitação como líder."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            swap.approved_leader = approved
            swap.date_approve_leader = timezone.now()
            swap.approver_leader_lan_id = profile.lan_id or ""
            if approved and should_finalize_peer_after_leader(swap):
                apply_approved_swap(swap)
                swap.approved = True
            elif not approved:
                swap.approved = False
                swap.date_approve_plan = timezone.now()
                swap.approver_plan_lan_id = profile.lan_id or ""
            swap.save()
            if not approved:
                _notify_swap_participants(
                    swap,
                    actor=request.user,
                    title="Troca recusada pelo líder",
                    message="A solicitação de troca não foi aprovada pelo líder.",
                    kind=PortalNotification.Kind.WARNING,
                    event_key="leader-rejected",
                )
            elif swap.approved is True:
                _notify_swap_participants(
                    swap,
                    actor=request.user,
                    title="Troca de escala aprovada",
                    message="A solicitação foi aprovada e concluída.",
                    kind=PortalNotification.Kind.SUCCESS,
                    event_key="leader-approved-final",
                )
            else:
                _notify_swap_participants(
                    swap,
                    actor=request.user,
                    title="Troca enviada ao Planejamento",
                    message="A solicitação foi aprovada pelo líder e encaminhada para análise.",
                    kind=PortalNotification.Kind.INFO,
                    event_key="leader-approved",
                )
    except SwapApplicationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(_serialize_requests([swap], profile)[0])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def swap_request_plan_approve(request, pk):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Perfil operacional não encontrado."}, status=status.HTTP_403_FORBIDDEN)

    serializer = SwapRequestApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    approved = serializer.validated_data["approved"]
    try:
        with transaction.atomic():
            swap = ScheduleRequest.objects.select_for_update().filter(pk=pk).first()
            if swap is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not can_approve_swap_plan(profile, swap):
                return Response(
                    {"detail": "Sem permissão para aprovar esta solicitação no planejamento."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            if approved:
                apply_approved_swap(swap)
            swap.approved = approved
            swap.date_approve_plan = timezone.now()
            swap.approver_plan_lan_id = profile.lan_id or ""
            swap.save()
            _notify_swap_participants(
                swap,
                actor=request.user,
                title="Troca de escala aprovada" if approved else "Troca de escala recusada",
                message=(
                    "O Planejamento aprovou e concluiu a solicitação de troca."
                    if approved
                    else "O Planejamento não aprovou a solicitação de troca."
                ),
                kind=(PortalNotification.Kind.SUCCESS if approved else PortalNotification.Kind.WARNING),
                event_key="planning-approved" if approved else "planning-rejected",
            )
    except SwapApplicationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(_serialize_requests([swap], profile)[0])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def swap_requests_stats(request):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Perfil operacional não encontrado."}, status=status.HTTP_403_FORBIDDEN)

    params = {
        key: request.query_params.get(key) or ""
        for key in ("date_from", "date_to", "swap_kind", "location", "job_activity")
    }
    data = build_swap_request_dashboard(profile, params)
    return Response(data)
