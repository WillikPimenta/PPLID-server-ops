from __future__ import annotations

from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import OperationalAlertEvent
from .rbac import MONITORING_DASHBOARDS
from .services.operational_alert_history import ALERT_TYPE_LABELS


def _parse_date_param(value: str, label: str):
    if not value:
        return None, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, Response(
            {"detail": f"{label} inválida. Use YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )


def _serialize_alert(event: OperationalAlertEvent) -> dict:
    end = event.resolved_at or event.last_seen_at
    duration_seconds = max(0, int((end - event.first_seen_at).total_seconds()))
    return {
        "id": str(event.id),
        "operational_date": event.operational_date.isoformat(),
        "agent_lan_id": event.agent.user_lan_id,
        "agent_name": event.agent.full_name,
        "leader_lan_id": event.leader_lan_id,
        "leader_name": event.leader_name,
        "location": event.location,
        "sector": event.sector,
        "job_activity": event.job_activity,
        "alert_type": event.alert_type,
        "alert_type_label": ALERT_TYPE_LABELS.get(event.alert_type, event.alert_type),
        "source_type": event.source_type,
        "source_id": event.source_id,
        "status": event.status,
        "status_label": event.get_status_display(),
        "priority_initial": event.priority_initial,
        "priority_current": event.priority_current,
        "state": event.state,
        "reason": event.reason,
        "recommended_action": event.recommended_action,
        "first_seen_at": timezone.localtime(event.first_seen_at).isoformat(),
        "last_seen_at": timezone.localtime(event.last_seen_at).isoformat(),
        "resolved_at": timezone.localtime(event.resolved_at).isoformat()
        if event.resolved_at
        else None,
        "duration_seconds": duration_seconds,
        "occurrence_count": event.occurrence_count,
        "context": event.context,
    }


@api_view(["GET"])
@permission_classes(MONITORING_DASHBOARDS)
def operational_alert_history_view(request):
    date_from, error = _parse_date_param(request.query_params.get("date_from", ""), "Data inicial")
    if error:
        return error
    date_to, error = _parse_date_param(request.query_params.get("date_to", ""), "Data final")
    if error:
        return error
    if date_from and date_to and date_from > date_to:
        return Response(
            {"detail": "A data inicial não pode ser posterior à data final."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    qs = OperationalAlertEvent.objects.select_related("agent").all()
    if date_from:
        qs = qs.filter(operational_date__gte=date_from)
    if date_to:
        qs = qs.filter(operational_date__lte=date_to)
    if agent := request.query_params.get("agent", "").strip():
        qs = qs.filter(
            Q(agent__user_lan_id__icontains=agent) | Q(agent__full_name__icontains=agent)
        )
    if leader := request.query_params.get("leader", "").strip():
        qs = qs.filter(
            Q(leader_lan_id__icontains=leader) | Q(leader_name__icontains=leader)
        )
    for param, field in (
        ("location", "location"),
        ("sector", "sector"),
        ("job_activity", "job_activity"),
        ("alert_type", "alert_type"),
        ("priority", "priority_current"),
        ("status", "status"),
    ):
        value = request.query_params.get(param, "").strip()
        if value:
            qs = qs.filter(**{field: value})
    if search := request.query_params.get("search", "").strip():
        qs = qs.filter(
            Q(agent__full_name__icontains=search)
            | Q(agent__user_lan_id__icontains=search)
            | Q(reason__icontains=search)
            | Q(state__icontains=search)
            | Q(recommended_action__icontains=search)
        )

    try:
        page_size = min(200, max(1, int(request.query_params.get("page_size") or 25)))
        page_number = max(1, int(request.query_params.get("page") or 1))
    except ValueError:
        return Response(
            {"detail": "Paginação inválida."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    paginator = Paginator(qs.order_by("-first_seen_at", "-created_at"), page_size)
    page = paginator.get_page(page_number)
    return Response(
        {
            "count": paginator.count,
            "page": page.number,
            "page_size": page_size,
            "total_pages": paginator.num_pages,
            "results": [_serialize_alert(event) for event in page.object_list],
            "filter_options": {
                "alert_types": [
                    {"value": key, "label": label}
                    for key, label in ALERT_TYPE_LABELS.items()
                ],
                "priorities": ["Alta", "Média", "Baixa", "Info"],
                "statuses": [
                    {"value": OperationalAlertEvent.STATUS_ACTIVE, "label": "Ativo"},
                    {"value": OperationalAlertEvent.STATUS_RESOLVED, "label": "Resolvido"},
                ],
            },
        }
    )
