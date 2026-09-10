from datetime import datetime, timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from apps.escala_flex.rbac import ANY_ESCALA_FLEX_VIEW, IMPERSONATE, OPERACAO_STATUS_CHANGE, OCORRENCIAS_OP_CREATE, OCORRENCIAS_PLAN_APPROVE
from rest_framework.response import Response

from apps.workforce.models import Agent, AgentHistory

from .models import (
    AbsenceType,
    CurrentActivity,
    HierarchicalLevel,
    Holiday,
    JobActivity,
    Location,
    NotifyEntry,
    OccurrenceType,
    OperationalOccurrence,
    OperationalOccurrenceExtension,
    RequestType,
    ScheduleRequest,
    ScheduleToday,
    StatusEvent,
    StatusType,
)
from .serializers import (
    BlockedBatchSerializer,
    BreakTimeBatchUpdateSerializer,
    HierarchicalLevelSerializer,
    HolidaySerializer,
    JobActivitySerializer,
    LocationSerializer,
    NHUpdateBatchSerializer,
    NotifyEntrySerializer,
    OccurrenceTypeSerializer,
    OccurrenceTypeWriteSerializer,
    OperationalOccurrenceApprovalSerializer,
    OperationalOccurrenceBatchApprovalSerializer,
    OperationalOccurrenceBatchCreateSerializer,
    OperationalOccurrenceBatchExtensionSerializer,
    OperationalOccurrenceCancelSerializer,
    OperationalOccurrenceExtensionApprovalSerializer,
    OperationalOccurrenceExtensionRequestSerializer,
    OperationalOccurrenceSerializer,
    OperationalOccurrenceUpdateSerializer,
    PublishedEscalaUpdateSerializer,
    RequestTypeSerializer,
    ScheduleRequestSerializer,
    ScheduleTodaySerializer,
    ScheduleTodayUpdateSerializer,
    PanelScheduleSerializer,
    AbsenceTypeSerializer,
    AbsenceTypeWriteSerializer,
    AgentScheduleEscalaEditSerializer,
    StatusEventCreateSerializer,
    StatusEventApprovalSerializer,
    StatusEventBatchApprovalSerializer,
    StatusEventSerializer,
    StatusTypeSerializer,
    StatusTypeWriteSerializer,
)
from .services import (
    ScheduleTodayService,
    StatusConfigurationError,
    StatusService,
    build_operational_profile,
    can_impersonate,
    clear_impersonation_session,
    compute_all_kpis,
    get_active_history,
    get_agent_for_user,
    get_effective_agent,
    profile_to_dict,
    real_profile_summary,
    resolve_operational_profile,
    set_impersonation_session,
    build_operational_dashboard,
)
from .services.escala_edit import PublishedEscalaEditService
from .services.absence_types import clear_absence_type_cache
from .services.schedule_utils import is_night_shift_crossing
from .services.nh_sync import apply_nh_change
from .services.break_times import (
    apply_break_time_updates,
    compute_break_time_stats,
    filter_options as break_time_filter_options,
    list_break_time_rows,
)
from .services.dimension_colors import (
    resolve_occurrence_color,
    sync_occurrence_colors_for_status,
    sync_status_colors_for_occurrence,
)
from .services.occurrence_workflow import (
    is_operational_occurrence_editable,
    is_operational_occurrence_in_progress,
    is_operational_occurrence_manageable,
    is_operational_occurrence_rejected,
    occurrence_has_pending_extension,
    validate_rejected_occurrence_scheduled_time,
)
from .services.occurrence_auto_cancel import run_occurrence_auto_cancels
from .services.permissions import can_edit_published_schedule
from .services.activity_panel_snapshot import build_activity_panel_snapshot
from .services.panel_schedule import (
    build_panel_rows,
    filter_panel_rows,
    panel_filter_options,
    panel_has_overnight_schedule,
    panel_leader_options,
    panel_nav_max_date,
)
from .planning_serializers import EscalaSerializer


def _parse_date(value: str | None):
    if not value:
        return timezone.localdate()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _filter_schedule_today(queryset, params):
    qs = queryset
    if params.get("logged_only") in ("true", "1", "yes"):
        qs = qs.exclude(status_id=3)
    if names := params.getlist("full_name"):
        qs = qs.filter(full_name__in=names)
    if nh := params.getlist("current_activity"):
        qs = qs.filter(hierarchical_level__name__in=nh)
    if schedules := params.getlist("work_schedule"):
        qs = qs.filter(work_schedule__in=schedules)
    if locations := params.getlist("location"):
        qs = qs.filter(location__in=locations)
    if statuses := params.getlist("status"):
        qs = qs.filter(status_id__in=[int(s) for s in statuses])
    if params.get("overtime") == "true":
        qs = qs.filter(overtime=True)
    if leader_lan := params.get("leader_lan_id"):
        qs = qs.filter(leader_lan_id__iexact=leader_lan)
    if search := params.get("search"):
        qs = qs.filter(
            Q(full_name__icontains=search)
            | Q(agent__user_lan_id__icontains=search)
        )
    if params.get("time_schedule_only") in ("true", "1", "yes"):
        qs = qs.filter(work_schedule__regex=r"^\d{2}:\d{2}\s*-\s*\d{2}:\d{2}$")
    return qs.order_by("full_name")


def _build_context_payload(request) -> dict:
    effective_profile, real_profile, impersonate_lan = resolve_operational_profile(request)
    if not effective_profile or not real_profile:
        return {}

    extra_menu_keys = None

    today = timezone.localdate()
    schedule = ScheduleToday.objects.filter(
        agent__user_lan_id__iexact=effective_profile.lan_id,
        date=today,
    ).select_related("status").first()
    agent_status = StatusService.get_initial_agent_status(schedule) if schedule else {
        "status": 3,
        "name": "Deslogado",
    }
    is_holiday = Holiday.objects.filter(
        date=today, location=effective_profile.location
    ).exists()
    if today.weekday() >= 5:
        is_holiday = True

    payload = {
        "profile": profile_to_dict(effective_profile, extra_menu_keys=extra_menu_keys),
        "agent_status": agent_status,
        "schedule_today_id": str(schedule.id) if schedule else None,
        "is_holiday": is_holiday,
        "impersonating": bool(impersonate_lan),
        "impersonate_lan_id": impersonate_lan,
        "real_profile": real_profile_summary(real_profile) if impersonate_lan else None,
        "last_notify": NotifyEntrySerializer(NotifyEntry.objects.first()).data
        if NotifyEntry.objects.exists()
        else None,
    }
    return payload


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def context_view(request):
    payload = _build_context_payload(request)
    if not payload:
        return Response(
            {"detail": "Colaborador não vinculado ao usuário logado."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return Response(payload)


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def impersonate_view(request):
    if not can_impersonate(request.user):
        return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)

    user_lan_id = str(request.data.get("user_lan_id", "")).strip()
    if not user_lan_id:
        return Response(
            {"detail": "Informe o UserLanID."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    agent = Agent.objects.filter(user_lan_id__iexact=user_lan_id, active=True).first()
    if not agent:
        return Response(
            {"detail": "Colaborador não encontrado."},
            status=status.HTTP_404_NOT_FOUND,
        )

    set_impersonation_session(request, user_lan_id)
    payload = _build_context_payload(request)
    return Response(payload)


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def impersonate_clear_view(request):
    clear_impersonation_session(request)
    payload = _build_context_payload(request)
    if not payload:
        return Response(
            {"detail": "Colaborador não vinculado ao usuário logado."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return Response(payload)


def _schedule_today_has_overnight_schedule(qs) -> bool:
    for work_schedule, is_previous_night_shift in qs.values_list(
        "work_schedule",
        "is_previous_night_shift",
    ):
        if is_previous_night_shift:
            return True
        if is_night_shift_crossing(work_schedule or ""):
            return True
    return False


def _schedule_today_filter_options(qs):
    status_rows = (
        qs.exclude(status_id__isnull=True)
        .values("status_id", "status__name")
        .distinct()
        .order_by("status__name")
    )
    statuses = [
        {"id": row["status_id"], "name": row["status__name"]}
        for row in status_rows
        if row["status_id"] is not None
    ]
    return {
        "full_names": list(
            qs.exclude(full_name="")
            .values_list("full_name", flat=True)
            .distinct()
            .order_by("full_name")
        ),
        "current_activities": list(
            qs.exclude(hierarchical_level__isnull=True)
            .values_list("hierarchical_level__name", flat=True)
            .distinct()
            .order_by("hierarchical_level__name")
        ),
        "work_schedules": list(
            qs.exclude(work_schedule="")
            .values_list("work_schedule", flat=True)
            .distinct()
            .order_by("work_schedule")
        ),
        "locations": list(
            qs.exclude(location="")
            .values_list("location", flat=True)
            .distinct()
            .order_by("location")
        ),
        "statuses": statuses,
    }


def _schedule_today_leader_options(qs):
    leaders_map: dict[str, str] = {}
    for name, lan in qs.exclude(leader__isnull=True).values_list(
        "leader__full_name",
        "leader__user_lan_id",
    ):
        if name and lan:
            leaders_map[lan.lower()] = name
    return [
        {"lan_id": lan, "name": name}
        for lan, name in sorted(leaders_map.items(), key=lambda x: x[1].lower())
    ]


def _paginate_list(items: list, params) -> tuple[list, int, int, int, int, int | None, int | None]:
    total_count = len(items)
    try:
        page = max(1, int(params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int(params.get("page_size", 100))))
    except (TypeError, ValueError):
        page_size = 100

    total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    page_items = items[offset : offset + page_size]
    next_page = page + 1 if page < total_pages else None
    previous_page = page - 1 if page > 1 else None
    return page_items, total_count, page, page_size, total_pages, next_page, previous_page


def _schedule_events_payload(target, agent_ids: list[int]) -> tuple[dict, dict]:
    status_events_by_lan: dict[str, list] = {}
    operational_occurrences_by_lan: dict[str, list] = {}
    if not agent_ids:
        return status_events_by_lan, operational_occurrences_by_lan

    events_qs = (
        StatusEvent.objects.filter(
            agent_id__in=agent_ids,
            start_date__date=target,
        )
        .select_related("agent", "status", "leader")
        .order_by("agent__user_lan_id", "start_date")
    )
    for event in events_qs:
        lan = event.agent.user_lan_id.lower()
        status_events_by_lan.setdefault(lan, []).append(StatusEventSerializer(event).data)

    occurrences_qs = (
        OperationalOccurrence.objects.filter(
            agent_id__in=agent_ids,
            date=target,
            approved=True,
            cancelled=False,
        )
        .select_related(
            "agent",
            "occurrence_type",
            "schedule_today",
        "schedule_today__hierarchical_level",
        "agent__current_activity_record__hierarchical_level",
            "created_by",
            "approved_by",
            "leader",
        )
        .order_by("agent__user_lan_id", "created_at")
    )
    for occurrence in occurrences_qs:
        lan = occurrence.agent.user_lan_id.lower()
        operational_occurrences_by_lan.setdefault(lan, []).append(
            OperationalOccurrenceSerializer(occurrence).data
        )
    return status_events_by_lan, operational_occurrences_by_lan


def _schedule_today_list_panel(request, target):
    effective_profile, _, _ = resolve_operational_profile(request)
    today = timezone.localdate()

    base_rows = build_panel_rows(target, effective_profile)
    filtered_rows = filter_panel_rows(base_rows, request.query_params)

    filter_options = None
    if request.query_params.get("filter_options") in ("true", "1", "yes"):
        filter_options = panel_filter_options(filtered_rows)
        filter_options["leaders"] = panel_leader_options(base_rows)

    page_rows, total_count, page, page_size, total_pages, next_page, previous_page = (
        _paginate_list(filtered_rows, request.query_params)
    )
    serializer = PanelScheduleSerializer(page_rows, many=True)
    agent_ids = [row["agent_id"] for row in page_rows]
    if target > today:
        status_events_by_lan, operational_occurrences_by_lan = {}, {}
    else:
        status_events_by_lan, operational_occurrences_by_lan = _schedule_events_payload(
            target, agent_ids
        )

    payload = {
        "date": str(target),
        "max_panel_date": str(panel_nav_max_date(effective_profile)),
        "count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "next": next_page,
        "previous": previous_page,
        "has_overnight_schedule": panel_has_overnight_schedule(filtered_rows),
        "results": serializer.data,
        "status_events_by_lan": status_events_by_lan,
        "operational_occurrences_by_lan": operational_occurrences_by_lan,
    }
    if filter_options is not None:
        payload["filter_options"] = filter_options
    return Response(payload)


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def schedule_today_list(request):
    target = _parse_date(request.query_params.get("date"))
    if request.query_params.get("all_headcount") in ("true", "1", "yes"):
        return _schedule_today_list_panel(request, target)

    # sync_schedule_metrics não roda em GET: rebuild/upsert já gravam working_hour/overtime.
    # Chamá-lo aqui satura Waitress+Postgres sob polling global (NhAlertHost, painéis).
    base_qs = ScheduleToday.objects.filter(date=target).select_related(
        "agent", "status", "leader", "hierarchical_level"
    )
    qs = _filter_schedule_today(base_qs, request.query_params)
    total_count = qs.count()

    filter_options = None
    if request.query_params.get("filter_options") in ("true", "1", "yes"):
        filter_options = _schedule_today_filter_options(qs)
        filter_options["leaders"] = _schedule_today_leader_options(base_qs)

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int(request.query_params.get("page_size", 100))))
    except (TypeError, ValueError):
        page_size = 100

    total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    page_qs = qs[offset : offset + page_size]
    serializer = ScheduleTodaySerializer(page_qs, many=True)

    agent_ids = [entry.agent_id for entry in page_qs]
    status_events_by_lan, operational_occurrences_by_lan = _schedule_events_payload(
        target, agent_ids
    )

    payload = {
        "date": str(target),
        "count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "next": page + 1 if page < total_pages else None,
        "previous": page - 1 if page > 1 else None,
        "has_overnight_schedule": _schedule_today_has_overnight_schedule(qs),
        "results": serializer.data,
        "status_events_by_lan": status_events_by_lan,
        "operational_occurrences_by_lan": operational_occurrences_by_lan,
    }
    if filter_options is not None:
        payload["filter_options"] = filter_options
    return Response(payload)


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def schedule_today_kpis(request):
    target = _parse_date(request.query_params.get("date"))
    data = compute_all_kpis(target_date=target)
    return Response({"date": str(target), **data})


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_dashboard_view(request):
    target = _parse_date(request.query_params.get("date"))
    data = build_operational_dashboard(target, request.query_params)
    return Response(data)


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def activity_panel_snapshot_view(request):
    target = _parse_date(request.query_params.get("date"))
    job_activity = (request.query_params.get("job_activity") or "").strip()
    if not job_activity:
        return Response(
            {"detail": "Informe o parâmetro job_activity."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    effective_profile, _, _ = resolve_operational_profile(request)
    data = build_activity_panel_snapshot(target, job_activity, effective_profile)
    return Response(data)


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def schedule_today_rebuild(request):
    target = _parse_date(request.data.get("date"))
    count = ScheduleTodayService.build_for_date(target)
    return Response({"date": str(target), "created": count})


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def schedule_today_detail(request, pk):
    try:
        entry = ScheduleToday.objects.select_related("agent").get(pk=pk)
    except ScheduleToday.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)
    serializer = ScheduleTodayUpdateSerializer(entry, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(ScheduleTodaySerializer(entry).data)


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def schedule_today_escala_edit(request, pk):
    try:
        entry = ScheduleToday.objects.select_related("agent").get(pk=pk)
    except ScheduleToday.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)

    profile = build_operational_profile(request.user)
    if not profile or not can_edit_published_schedule(profile, entry.agent, user=request.user):
        return Response(
            {"detail": "Sem permissão para alterar a escala publicada."},
            status=status.HTTP_403_FORBIDDEN,
        )

    escala = PublishedEscalaEditService.get_escala_for_schedule_today(entry)
    if escala is None:
        return Response(
            {"detail": "Nenhuma escala publicada encontrada para este agente e data."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = PublishedEscalaUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    try:
        escala, schedule_today = PublishedEscalaEditService.apply_changes(
            escala,
            dia_escala=data.get("dia_escala"),
            week=data.get("week"),
            weekend=data.get("weekend"),
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "escala": EscalaSerializer(escala).data,
            "schedule_today": (
                ScheduleTodaySerializer(schedule_today).data if schedule_today else None
            ),
        }
    )


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def agent_schedule_escala_edit(request):
    serializer = AgentScheduleEscalaEditSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    agent = Agent.objects.filter(user_lan_id__iexact=data["user_lan_id"]).first()
    if not agent:
        return Response(
            {"detail": "Agente não encontrado."},
            status=status.HTTP_404_NOT_FOUND,
        )

    profile = build_operational_profile(request.user)
    if not profile or not can_edit_published_schedule(profile, agent, user=request.user):
        return Response(
            {"detail": "Sem permissão para alterar a escala publicada."},
            status=status.HTTP_403_FORBIDDEN,
        )

    escala = PublishedEscalaEditService.get_or_create_escala_for_agent(
        agent,
        data["date"],
    )

    try:
        escala, schedule_today = PublishedEscalaEditService.apply_changes(
            escala,
            dia_escala=data.get("dia_escala"),
            week=data.get("week"),
            weekend=data.get("weekend"),
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "escala": EscalaSerializer(escala).data,
            "schedule_today": (
                ScheduleTodaySerializer(schedule_today).data if schedule_today else None
            ),
        }
    )


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def schedule_today_blocked_batch(request):
    serializer = BlockedBatchSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    updated = 0
    for item in serializer.validated_data["items"]:
        entry = ScheduleToday.objects.filter(pk=item.get("ID") or item.get("id")).first()
        if not entry:
            continue
        entry.blocked = item.get("Blocked", item.get("blocked", False))
        entry.blocked_by = item.get("BlockedBy", item.get("blocked_by", ""))
        entry.blocked_description = item.get(
            "BlockedDescription", item.get("blocked_description", "")
        )
        if entry.blocked:
            if item.get("DateBlocked") or item.get("blocked_at"):
                entry.blocked_at = timezone.now()
            elif entry.blocked_at is None:
                entry.blocked_at = timezone.now()
        else:
            entry.blocked_at = None
            entry.blocked_by = ""
            entry.blocked_description = ""
        entry.save()
        updated += 1
    return Response({"updated": updated})


@api_view(["GET", "POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_events_view(request):
    if request.method == "GET":
        scope = request.query_params.get("scope", "today")
        agent = get_effective_agent(request)
        if not agent:
            return Response({"results": []})
        qs = StatusEvent.objects.select_related("agent", "status", "leader")
        if scope == "today":
            today = timezone.localdate()
            qs = qs.filter(
                agent=agent,
                start_date__date=today,
            )
        elif scope == "history":
            cutoff = timezone.now() - timedelta(days=90)
            qs = qs.filter(
                agent=agent,
                active_event=False,
                start_date__gte=cutoff,
            )
        elif scope == "team":
            effective_profile, _, _ = resolve_operational_profile(request)
            lan = effective_profile.lan_id if effective_profile else ""
            qs = qs.filter(leader__user_lan_id__iexact=lan)
        else:
            qs = qs.none()
        return Response({"results": StatusEventSerializer(qs, many=True).data})

    serializer = StatusEventCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    schedule = None
    if data.get("schedule_today_id") is not None:
        try:
            schedule = ScheduleToday.objects.select_related("agent", "leader").get(
                pk=data["schedule_today_id"]
            )
        except ScheduleToday.DoesNotExist:
            return Response(
                {"detail": "Escala do dia não encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )
    else:
        lan = data["user_lan_id"]
        target_date = data["date"]
        agent = Agent.objects.filter(user_lan_id__iexact=lan).first()
        if not agent:
            return Response(
                {"detail": "Agente não encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        schedule = ScheduleToday.objects.select_related("agent", "leader").filter(
            agent=agent,
            date=target_date,
        ).first()
        if schedule is None:
            schedule = ScheduleTodayService.upsert_agent_for_date(agent, target_date)
        if schedule is None:
            return Response(
                {
                    "detail": (
                        "Não há escala publicada com horário para este agente nesta data. "
                        "Importe/reconstrua a escala do dia antes de iniciar o turno."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        schedule = ScheduleToday.objects.select_related("agent", "leader").get(pk=schedule.pk)

    _, _, impersonate_lan = resolve_operational_profile(request)
    effective_agent = get_effective_agent(request)
    if impersonate_lan:
        if not effective_agent or schedule.agent_id != effective_agent.pk:
            return Response(
                {"detail": "Escala não pertence ao usuário simulado."},
                status=status.HTTP_403_FORBIDDEN,
            )
        actor = effective_agent
    else:
        actor = get_agent_for_user(request.user)
    try:
        if data["action"] == "start_shift":
            StatusService.start_shift(schedule.agent, schedule)
            return Response({"detail": "Turno iniciado.", "schedule_today_id": str(schedule.id)})
        event = StatusService.change_status(
            schedule.agent,
            schedule,
            data["status_id"],
            leader=actor,
        )
    except StatusConfigurationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if data.get("status_id") == StatusService.LOGGED_OUT_ID or event is None:
        return Response({"detail": "Deslogado.", "schedule_today_id": str(schedule.id)})
    payload = StatusEventSerializer(event).data
    payload["schedule_today_id"] = str(schedule.id)
    return Response(payload, status=status.HTTP_201_CREATED)


_HISTORY_SORT_MAP = {
    "start_date": "start_date",
    "-start_date": "-start_date",
    "agent_name": "agent__full_name",
    "-agent_name": "-agent__full_name",
    "status_name": "status__name",
    "-status_name": "-status__name",
    "total_duration": "total_duration",
    "-total_duration": "-total_duration",
    "approved_at": "approved_at",
    "-approved_at": "-approved_at",
    "current_leader_name": "agent__history__leader__full_name",
    "-current_leader_name": "-agent__history__leader__full_name",
}

PAUSE_STATUS_IDS = {2, 4, 5, 6, 7, 8, 9, 10, 11, 13}

_ACTIVE_HISTORY_PREFETCH = Prefetch(
    "agent__history",
    queryset=AgentHistory.objects.filter(
        active=True,
        final_date__isnull=True,
    ).select_related("leader"),
    to_attr="_active_histories",
)


def _scope_status_event_history(qs, profile):
    if profile.is_admin or profile.team == "Planejamento":
        return qs
    if profile.is_agent_backoffice:
        return qs.filter(agent__user_lan_id__iexact=profile.lan_id)
    if profile.lan_id:
        return qs.filter(
            agent__history__leader__user_lan_id__iexact=profile.lan_id,
            agent__history__active=True,
            agent__history__final_date__isnull=True,
        ).distinct()
    return qs.none()


def _filter_by_current_leader(qs, leader_lan: str):
    return qs.filter(
        agent__history__leader__user_lan_id__iexact=leader_lan,
        agent__history__active=True,
        agent__history__final_date__isnull=True,
    ).distinct()


def _can_approve_status_event(profile, event: StatusEvent) -> bool:
    if profile.is_admin or profile.team == "Planejamento":
        return True
    history = get_active_history(event.agent)
    if history and history.leader:
        return history.leader.user_lan_id.lower() == profile.lan_id
    if event.leader:
        return event.leader.user_lan_id.lower() == profile.lan_id
    return False


def _validate_event_for_approval(event: StatusEvent, profile) -> str | None:
    if event.status_id not in PAUSE_STATUS_IDS:
        return "Somente pausas podem ser aprovadas."
    if event.active_event:
        return "A pausa ainda está em andamento."
    if event.approved is not None:
        return "Ocorrência já processada."
    if not _can_approve_status_event(profile, event):
        return "Sem permissão."
    from .services.status_event_coverage import coverage_for_event

    coverage = coverage_for_event(event)
    if not coverage.get("status_linked"):
        return "Status sem vínculo com tipo de ocorrência; não é possível aprovar."
    if coverage.get("excess_duration", 0) <= 0:
        return "Não há tempo excedente para aprovar (coberto por ocorrência)."
    return None


def _apply_status_event_approval(
    event: StatusEvent,
    *,
    actor: Agent | None,
    approved: bool,
    approved_duration: int | None,
    approval_notes: str,
) -> StatusEvent:
    from .services.status_event_coverage import coverage_for_event

    if approved and approved_duration is not None:
        excess = coverage_for_event(event).get("excess_duration", 0)
        if approved_duration > excess:
            raise ValueError(
                f"O tempo aprovado não pode exceder o excedente ({excess}s)."
            )

    now = timezone.now()
    event.approved = approved
    event.approved_by = actor
    event.approved_at = now
    event.approval_notes = approval_notes
    if approved:
        event.approved_duration = approved_duration
    else:
        event.approved_duration = None
    event.save(
        update_fields=[
            "approved",
            "approved_by",
            "approved_at",
            "approved_duration",
            "approval_notes",
        ]
    )
    return event


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_event_history_list(request):
    """Lista paginada de eventos de status para a página Histórico."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"count": 0, "page": 1, "page_size": 50, "total_pages": 1, "results": []}
        )

    qs = (
        StatusEvent.objects.all()
        .select_related("agent", "status", "leader", "approved_by")
        .prefetch_related(_ACTIVE_HISTORY_PREFETCH)
        .order_by("-start_date")
    )
    qs = _scope_status_event_history(qs, profile)

    if leader_lan := request.query_params.get("leader_lan_id"):
        qs = _filter_by_current_leader(qs, leader_lan)

    if date_from := request.query_params.get("date_from"):
        qs = qs.filter(start_date__date__gte=_parse_date(date_from))
    if date_to := request.query_params.get("date_to"):
        qs = qs.filter(start_date__date__lte=_parse_date(date_to))

    if search := request.query_params.get("search", "").strip():
        qs = qs.filter(
            Q(agent__full_name__icontains=search)
            | Q(agent__user_lan_id__icontains=search)
        )

    if status_ids := request.query_params.getlist("status"):
        qs = qs.filter(status_id__in=[int(s) for s in status_ids if s.isdigit()])

    approved = request.query_params.get("approved")
    if approved in ("true", "1", "yes"):
        qs = qs.filter(approved=True)
    elif approved in ("false", "0", "no"):
        qs = qs.filter(approved=False)
    elif approved == "pending":
        qs = qs.filter(approved__isnull=True)

    pauses_only = request.query_params.get("pauses_only")
    if pauses_only in ("true", "1", "yes"):
        qs = qs.exclude(status_id__in=[1, 3])

    finalized_only = request.query_params.get("finalized_only")
    if finalized_only in ("true", "1", "yes"):
        qs = qs.filter(active_event=False).exclude(status_id__in=[1, 3])

    from .services.status_event_coverage import build_coverage_map, load_linked_status_ids

    approvable_only = request.query_params.get("approvable_only") in ("true", "1", "yes")
    if approvable_only:
        linked_ids = load_linked_status_ids() & PAUSE_STATUS_IDS
        qs = qs.filter(
            status_id__in=list(linked_ids),
            active_event=False,
            approved__isnull=True,
            total_duration__isnull=False,
        )

    sort_key = request.query_params.get("sort", "-start_date")
    qs = qs.order_by(_HISTORY_SORT_MAP.get(sort_key, "-start_date"))

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(200, max(1, int(request.query_params.get("page_size", 50))))
    except (TypeError, ValueError):
        page_size = 50

    if approvable_only:
        candidates = list(qs)
        coverage_full = build_coverage_map(candidates)
        page_source = [
            event
            for event in candidates
            if (cov := coverage_full.get(event.id))
            and cov.get("status_linked")
            and int(cov.get("excess_duration", 0) or 0) > 0
        ]
        total_count = len(page_source)
        total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count else 1
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        page_list = page_source[offset : offset + page_size]
        coverage_map = {
            event.id: coverage_full[event.id]
            for event in page_list
            if event.id in coverage_full
        }
    else:
        total_count = qs.count()
        total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count else 1
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        page_list = list(qs[offset : offset + page_size])
        coverage_map = build_coverage_map(page_list)

    return Response(
        {
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "next": page + 1 if page < total_pages else None,
            "previous": page - 1 if page > 1 else None,
            "results": StatusEventSerializer(
                page_list,
                many=True,
                context={"coverage_map": coverage_map},
            ).data,
        }
    )


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_event_history_filters(request):
    """Opções de filtro (líderes) para a página Histórico."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"leaders": []})

    qs = StatusEvent.objects.all()
    qs = _scope_status_event_history(qs, profile)

    leaders_map: dict[str, str] = {}
    for name, lan in (
        qs.filter(
            agent__history__active=True,
            agent__history__final_date__isnull=True,
            agent__history__leader__isnull=False,
        )
        .values_list(
            "agent__history__leader__full_name",
            "agent__history__leader__user_lan_id",
        )
        .distinct()
    ):
        if name and lan:
            leaders_map[lan.lower()] = name

    leaders = [
        {"lan_id": lan, "name": name}
        for lan, name in sorted(leaders_map.items(), key=lambda x: x[1].lower())
    ]
    return Response({"leaders": leaders})


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_event_approve(request, pk):
    """Aprova ou rejeita uma ocorrência de pausa."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)

    try:
        event = (
            StatusEvent.objects.select_related("agent", "status")
            .prefetch_related(_ACTIVE_HISTORY_PREFETCH)
            .get(pk=pk)
        )
    except StatusEvent.DoesNotExist:
        return Response({"detail": "Evento não encontrado."}, status=status.HTTP_404_NOT_FOUND)

    err = _validate_event_for_approval(event, profile)
    if err:
        code = (
            status.HTTP_403_FORBIDDEN
            if err == "Sem permissão."
            else status.HTTP_400_BAD_REQUEST
        )
        return Response({"detail": err}, status=code)

    serializer = StatusEventApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    actor = get_agent_for_user(request.user)
    try:
        _apply_status_event_approval(
            event,
            actor=actor,
            approved=data["approved"],
            approved_duration=data.get("approved_duration"),
            approval_notes=data.get("approval_notes", ""),
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    event = (
        StatusEvent.objects.select_related("agent", "status", "leader", "approved_by")
        .prefetch_related(_ACTIVE_HISTORY_PREFETCH)
        .get(pk=event.pk)
    )
    from .services.status_event_coverage import coverage_for_event

    return Response(
        StatusEventSerializer(
            event,
            context={"coverage_map": {event.id: coverage_for_event(event)}},
        ).data
    )


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_event_approve_batch(request):
    """Aprova ou rejeita ocorrências de pausa em lote."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)

    serializer = StatusEventBatchApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    notes = data.get("approval_notes", "")
    actor = get_agent_for_user(request.user)

    ids = [item["id"] for item in data["items"]]
    events_by_id = {
        ev.id: ev
        for ev in StatusEvent.objects.select_related("agent", "status", "leader")
        .prefetch_related(_ACTIVE_HISTORY_PREFETCH)
        .filter(pk__in=ids)
    }

    updated: list = []
    errors: list[dict] = []

    for item in data["items"]:
        event = events_by_id.get(item["id"])
        if not event:
            errors.append({"id": str(item["id"]), "detail": "Evento não encontrado."})
            continue
        err = _validate_event_for_approval(event, profile)
        if err:
            label = event.agent.full_name if event.agent else str(item["id"])
            errors.append({"id": str(item["id"]), "detail": f"{label}: {err}"})
            continue
        try:
            _apply_status_event_approval(
                event,
                actor=actor,
                approved=item["approved"],
                approved_duration=item.get("approved_duration"),
                approval_notes=notes,
            )
        except ValueError as exc:
            label = event.agent.full_name if event.agent else str(item["id"])
            errors.append({"id": str(item["id"]), "detail": f"{label}: {exc}"})
            continue
        refreshed = (
            StatusEvent.objects.select_related("agent", "status", "leader", "approved_by")
            .prefetch_related(_ACTIVE_HISTORY_PREFETCH)
            .get(pk=event.pk)
        )
        updated.append(StatusEventSerializer(refreshed).data)

    status_code = status.HTTP_200_OK if updated else status.HTTP_400_BAD_REQUEST
    return Response({"results": updated, "errors": errors}, status=status_code)


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_event_related_occurrences(request, pk):
    """Ocorrências vinculadas ao status do evento (mesmo agente/dia) para consulta."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)

    qs = StatusEvent.objects.select_related("agent", "status")
    qs = _scope_status_event_history(qs, profile)
    event = qs.filter(pk=pk).first()
    if not event:
        return Response({"detail": "Evento não encontrado."}, status=status.HTTP_404_NOT_FOUND)

    from .services.status_event_coverage import related_occurrences_for_event

    occurrences = related_occurrences_for_event(event)
    return Response(
        {"results": OperationalOccurrenceSerializer(occurrences, many=True).data}
    )


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def nh_updates_view(request):
    serializer = NHUpdateBatchSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    updated = 0
    for item in serializer.validated_data["items"]:
        agent = Agent.objects.filter(
            user_lan_id__iexact=item["user_lan_id"]
        ).first()
        if not agent:
            continue

        level = None
        if item.get("hierarchical_level_id"):
            level = HierarchicalLevel.objects.filter(
                pk=item["hierarchical_level_id"],
                active=True,
            ).first()
        elif item.get("hierarchical_level"):
            level = HierarchicalLevel.objects.filter(
                name__iexact=item["hierarchical_level"].strip(),
                active=True,
            ).first()
        elif item.get("current_activity"):
            level = HierarchicalLevel.objects.filter(
                name__iexact=item["current_activity"].strip(),
                active=True,
            ).first()

        sid = item.get("id_schedule")
        if apply_nh_change(agent, level, schedule_today_id=sid):
            updated += 1
    return Response({"updated": updated})


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def nh_updates_export_view(request):
    import json

    from django.http import HttpResponse

    payload = request.data.get("items") or request.data
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    response = HttpResponse(content, content_type="application/json")
    response["Content-Disposition"] = (
        f'attachment; filename="nh_update_{timezone.now():%Y%m%d_%H%M%S}.json"'
    )
    return response


def _break_time_query_params(params):
    return {
        "search": (params.get("search") or "").strip() or None,
        "location": (params.get("location") or "").strip() or None,
        "job_activity": (params.get("job_activity") or "").strip() or None,
    }


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def break_times_list(request):
    filters = _break_time_query_params(request.query_params)
    rows = list_break_time_rows(**filters)
    return Response(
        {
            "results": rows,
            "count": len(rows),
            "filter_options": break_time_filter_options(),
        }
    )


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def break_times_stats(request):
    filters = _break_time_query_params(request.query_params)
    return Response(compute_break_time_stats(**filters))


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def break_times_update(request):
    serializer = BreakTimeBatchUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    updated, errors = apply_break_time_updates(serializer.validated_data["items"])
    status_code = (
        status.HTTP_200_OK if updated or not errors else status.HTTP_400_BAD_REQUEST
    )
    return Response({"updated": updated, "errors": errors}, status=status_code)


DIMENSION_MAP = {
    "status-types": (StatusType, StatusTypeSerializer),
    "absence-types": (AbsenceType, AbsenceTypeSerializer),
    "locations": (Location, LocationSerializer),
    "job-activities": (JobActivity, JobActivitySerializer),
    "hierarchical-levels": (HierarchicalLevel, HierarchicalLevelSerializer),
    "request-types": (RequestType, RequestTypeSerializer),
    "occurrence-types": (OccurrenceType, OccurrenceTypeSerializer),
    "holidays": (Holiday, HolidaySerializer),
}


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def dimensions_view(request, resource: str):
    entry = DIMENSION_MAP.get(resource)
    if not entry:
        return Response(status=status.HTTP_404_NOT_FOUND)
    model, ser = entry
    qs = model.objects.filter(active=True) if hasattr(model, "active") else model.objects.all()
    if resource == "holidays":
        qs = model.objects.all()
    return Response({"results": ser(qs, many=True).data})


class ScheduleRequestViewSet(viewsets.ModelViewSet):
    permission_classes = ANY_ESCALA_FLEX_VIEW
    serializer_class = ScheduleRequestSerializer
    queryset = ScheduleRequest.objects.select_related("request_type").order_by(
        "-date_request"
    )

    def get_queryset(self):
        qs = super().get_queryset()
        month = self.request.query_params.get("month")
        year = self.request.query_params.get("year")
        if month and year:
            qs = qs.filter(
                date_swap__year=int(year),
                date_swap__month=int(month),
            )
        return qs


def _scope_operational_occurrences(qs, profile):
    if profile.is_admin or profile.team == "Planejamento":
        return qs
    if profile.is_agent_backoffice:
        return qs.filter(agent__user_lan_id__iexact=profile.lan_id)
    if profile.lan_id:
        return qs.filter(
            agent__history__leader__user_lan_id__iexact=profile.lan_id,
            agent__history__active=True,
            agent__history__final_date__isnull=True,
        ).distinct()
    return qs.none()


def _can_manage_occurrence_for_schedule(profile, schedule: ScheduleToday) -> bool:
    if profile.is_admin or profile.team == "Planejamento":
        return True
    if schedule.leader_lan_id and schedule.leader_lan_id.lower() == profile.lan_id:
        return True
    history = get_active_history(schedule.agent)
    if history and history.leader:
        return history.leader.user_lan_id.lower() == profile.lan_id
    return False


def _can_approve_operational_occurrence(profile, occurrence: OperationalOccurrence) -> bool:
    if profile.is_admin or profile.team == "Planejamento":
        return True
    history = get_active_history(occurrence.agent)
    if history and history.leader:
        return history.leader.user_lan_id.lower() == profile.lan_id
    if occurrence.leader:
        return occurrence.leader.user_lan_id.lower() == profile.lan_id
    return False


def _can_request_occurrence_extension(profile, occurrence: OperationalOccurrence) -> bool:
    if profile.is_admin or profile.team == "Planejamento":
        return True
    if occurrence.leader and occurrence.leader.user_lan_id.lower() == profile.lan_id:
        return True
    history = get_active_history(occurrence.agent)
    if history and history.leader:
        return history.leader.user_lan_id.lower() == profile.lan_id
    if occurrence.schedule_today:
        return _can_manage_occurrence_for_schedule(profile, occurrence.schedule_today)
    return False


@api_view(["GET"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrences_list(request):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response({"results": []})

    run_occurrence_auto_cancels()

    pending_ext_qs = OperationalOccurrenceExtension.objects.filter(
        approved__isnull=True,
    ).select_related("created_by", "approved_by")

    qs = OperationalOccurrence.objects.select_related(
        "agent",
        "occurrence_type",
        "leader",
        "created_by",
        "approved_by",
        "schedule_today",
        "schedule_today__hierarchical_level",
        "agent__current_activity_record__hierarchical_level",
    ).prefetch_related(
        Prefetch("extensions", queryset=pending_ext_qs, to_attr="_pending_extensions"),
    ).order_by("-date", "agent__full_name")

    if date_param := request.query_params.get("date"):
        qs = qs.filter(date=_parse_date(date_param))

    pending = request.query_params.get("pending")
    if pending in ("true", "1", "yes"):
        qs = qs.filter(approved__isnull=True)
    elif pending in ("false", "0", "no"):
        qs = qs.exclude(approved__isnull=True)

    pending_extension = request.query_params.get("pending_extension")
    if pending_extension in ("true", "1", "yes"):
        qs = qs.filter(extensions__approved__isnull=True).distinct()

    qs = _scope_operational_occurrences(qs, profile)
    return Response({"results": OperationalOccurrenceSerializer(qs, many=True).data})


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrences_batch_create(request):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = OperationalOccurrenceBatchCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    actor = get_agent_for_user(request.user)

    schedule_ids = [item["schedule_today_id"] for item in serializer.validated_data["items"]]
    schedules = {
        row.pk: row
        for row in ScheduleToday.objects.select_related("agent", "leader").filter(
            pk__in=schedule_ids
        )
    }

    type_ids = {item["occurrence_type_id"] for item in serializer.validated_data["items"]}
    valid_types = set(
        OccurrenceType.objects.filter(id__in=type_ids, active=True).values_list("id", flat=True)
    )

    created = []
    errors = []
    for item in serializer.validated_data["items"]:
        schedule = schedules.get(item["schedule_today_id"])
        if not schedule:
            errors.append(
                {"schedule_today_id": str(item["schedule_today_id"]), "detail": "Escala não encontrada."}
            )
            continue
        if not _can_manage_occurrence_for_schedule(profile, schedule):
            errors.append(
                {"schedule_today_id": str(item["schedule_today_id"]), "detail": "Sem permissão."}
            )
            continue
        if item["occurrence_type_id"] not in valid_types:
            errors.append(
                {
                    "schedule_today_id": str(item["schedule_today_id"]),
                    "detail": "Tipo de ocorrência inválido.",
                }
            )
            continue

        history = get_active_history(schedule.agent)
        leader = history.leader if history else schedule.leader
        scheduled_time = item.get("scheduled_time") or timezone.localtime().time().replace(
            second=0, microsecond=0
        )
        occurrence = OperationalOccurrence.objects.create(
            date=timezone.localdate(),
            agent=schedule.agent,
            schedule_today=schedule,
            occurrence_type_id=item["occurrence_type_id"],
            forecast_seconds=item["forecast_seconds"],
            scheduled_time=scheduled_time,
            description=item.get("description", ""),
            approved=None,
            created_by=actor,
            leader=leader,
        )
        created.append(occurrence)

    payload = {
        "created": OperationalOccurrenceSerializer(created, many=True).data,
        "errors": errors,
    }
    status_code = status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST
    return Response(payload, status=status_code)


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrence_approve(request, pk):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        occurrence = OperationalOccurrence.objects.select_related(
            "agent",
            "occurrence_type",
            "leader",
            "created_by",
            "approved_by",
            "schedule_today",
            "schedule_today__hierarchical_level",
            "agent__current_activity_record__hierarchical_level",
        ).get(pk=pk)
    except OperationalOccurrence.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if occurrence.cancelled:
        return Response(
            {"detail": "Ocorrência cancelada."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if occurrence.approved is True:
        return Response(
            {"detail": "Ocorrência já processada."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = OperationalOccurrenceApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    approved = serializer.validated_data["approved"]

    if occurrence.approved is False:
        if not approved:
            return Response(
                {"detail": "Ocorrência já recusada."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        time_error = validate_rejected_occurrence_scheduled_time(
            occurrence, occurrence.scheduled_time
        )
        if time_error:
            return Response(
                {"detail": time_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if not _can_approve_operational_occurrence(profile, occurrence):
        return Response(
            {"detail": "Sem permissão."},
            status=status.HTTP_403_FORBIDDEN,
        )

    actor = get_agent_for_user(request.user)
    _apply_operational_occurrence_approval(
        occurrence,
        actor=actor,
        approved=approved,
        approval_notes=serializer.validated_data.get("approval_notes", ""),
    )
    return Response(OperationalOccurrenceSerializer(occurrence).data)


def _validate_occurrence_for_batch_approval(occurrence, profile) -> str | None:
    if occurrence.cancelled:
        return "Ocorrência cancelada."
    if occurrence.approved is True:
        return "Ocorrência já processada."
    if occurrence.approved is False:
        return "Ocorrência já recusada. Use a aprovação individual."
    if not _can_approve_operational_occurrence(profile, occurrence):
        return "Sem permissão."
    return None


def _apply_operational_occurrence_approval(
    occurrence: OperationalOccurrence,
    *,
    actor,
    approved: bool,
    approval_notes: str,
) -> None:
    now = timezone.now()
    occurrence.approved = approved
    occurrence.approved_by = actor
    occurrence.approved_at = now
    occurrence.approval_notes = approval_notes or ""
    occurrence.save(
        update_fields=["approved", "approved_by", "approved_at", "approval_notes"]
    )


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrences_approve_batch(request):
    """Aprova ou recusa ocorrências pendentes em lote."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = OperationalOccurrenceBatchApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    notes = data.get("approval_notes", "")
    actor = get_agent_for_user(request.user)

    ids = [item["id"] for item in data["items"]]
    occurrences_by_id = {
        item.id: item
        for item in OperationalOccurrence.objects.select_related(
            "agent",
            "occurrence_type",
            "leader",
            "created_by",
            "approved_by",
            "schedule_today",
            "schedule_today__hierarchical_level",
            "agent__current_activity_record__hierarchical_level",
        ).filter(pk__in=ids)
    }

    updated: list = []
    errors: list[dict] = []

    for item in data["items"]:
        occurrence = occurrences_by_id.get(item["id"])
        if not occurrence:
            errors.append({"id": str(item["id"]), "detail": "Ocorrência não encontrada."})
            continue
        err = _validate_occurrence_for_batch_approval(occurrence, profile)
        if err:
            label = occurrence.agent.full_name if occurrence.agent_id else str(item["id"])
            errors.append({"id": str(item["id"]), "detail": f"{label}: {err}"})
            continue
        _apply_operational_occurrence_approval(
            occurrence,
            actor=actor,
            approved=item["approved"],
            approval_notes=notes,
        )
        refreshed = _get_operational_occurrence(occurrence.pk)
        updated.append(OperationalOccurrenceSerializer(refreshed).data)

    status_code = status.HTTP_200_OK if updated else status.HTTP_400_BAD_REQUEST
    return Response({"results": updated, "errors": errors}, status=status_code)


def _get_operational_occurrence(pk):
    try:
        return OperationalOccurrence.objects.select_related(
            "agent",
            "occurrence_type",
            "schedule_today",
            "schedule_today__hierarchical_level",
            "agent__current_activity_record__hierarchical_level",
            "leader",
            "created_by",
            "approved_by",
        ).get(pk=pk)
    except OperationalOccurrence.DoesNotExist:
        return None


def _can_manage_approved_operational_occurrence(profile, occurrence: OperationalOccurrence) -> bool:
    if not is_operational_occurrence_manageable(occurrence):
        return False
    return _can_approve_operational_occurrence(profile, occurrence)


def _can_edit_operational_occurrence(profile, occurrence: OperationalOccurrence) -> bool:
    if not is_operational_occurrence_editable(occurrence):
        return False
    return _can_approve_operational_occurrence(profile, occurrence)


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrence_cancel(request, pk):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    occurrence = _get_operational_occurrence(pk)
    if occurrence is None:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if not _can_manage_approved_operational_occurrence(profile, occurrence):
        if not is_operational_occurrence_manageable(occurrence):
            return Response(
                {"detail": "Ocorrências finalizadas ou canceladas não podem ser alteradas."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"detail": "Sem permissão."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = OperationalOccurrenceCancelSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    note = serializer.validated_data.get("approval_notes", "").strip()
    if note:
        existing = occurrence.approval_notes.strip()
        occurrence.approval_notes = f"{existing}\n{note}".strip() if existing else note

    occurrence.cancelled = True
    update_fields = ["cancelled"]
    if note:
        update_fields.append("approval_notes")
    occurrence.save(update_fields=update_fields)
    return Response(OperationalOccurrenceSerializer(occurrence).data)


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrence_update(request, pk):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    occurrence = _get_operational_occurrence(pk)
    if occurrence is None:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if not _can_edit_operational_occurrence(profile, occurrence):
        if not is_operational_occurrence_editable(occurrence):
            return Response(
                {"detail": "Ocorrências finalizadas ou canceladas não podem ser alteradas."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"detail": "Sem permissão."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = OperationalOccurrenceUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    if (
        is_operational_occurrence_rejected(occurrence)
        and "scheduled_time" in serializer.validated_data
    ):
        time_error = validate_rejected_occurrence_scheduled_time(
            occurrence, serializer.validated_data["scheduled_time"]
        )
        if time_error:
            return Response(
                {"detail": time_error},
                status=status.HTTP_400_BAD_REQUEST,
            )
    update_fields = []
    for field in ("scheduled_time", "forecast_seconds", "description"):
        if field in serializer.validated_data:
            setattr(occurrence, field, serializer.validated_data[field])
            update_fields.append(field)
    occurrence.save(update_fields=update_fields)
    return Response(OperationalOccurrenceSerializer(occurrence).data)


def _validate_occurrence_for_batch_extension(occurrence, profile) -> str | None:
    if not _can_request_occurrence_extension(profile, occurrence):
        return "Sem permissão."
    if not is_operational_occurrence_in_progress(occurrence):
        return "Ocorrência não está em andamento."
    if occurrence_has_pending_extension(occurrence):
        return "Já existe solicitação de tempo adicional pendente."
    return None


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrence_request_extension(request, pk):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    occurrence = _get_operational_occurrence(pk)
    if occurrence is None:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if not _can_request_occurrence_extension(profile, occurrence):
        return Response(
            {"detail": "Sem permissão."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not is_operational_occurrence_in_progress(occurrence):
        return Response(
            {"detail": "Tempo adicional só pode ser solicitado para ocorrências em andamento."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if occurrence_has_pending_extension(occurrence):
        return Response(
            {"detail": "Já existe uma solicitação de tempo adicional pendente."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = OperationalOccurrenceExtensionRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    actor = get_agent_for_user(request.user)
    extension = OperationalOccurrenceExtension.objects.create(
        occurrence=occurrence,
        extra_seconds=serializer.validated_data["extra_seconds"],
        description=serializer.validated_data.get("description", ""),
        created_by=actor,
    )
    occurrence._pending_extensions = [extension]
    return Response(
        OperationalOccurrenceSerializer(occurrence).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrences_request_extension_batch(request):
    """Solicita tempo adicional em lote para ocorrências em andamento."""
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = OperationalOccurrenceBatchExtensionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    extra_seconds = data["extra_seconds"]
    description = data.get("description", "")
    actor = get_agent_for_user(request.user)

    ids = [item["id"] for item in data["items"]]
    occurrences_by_id = {
        item.id: item
        for item in OperationalOccurrence.objects.select_related(
            "agent",
            "occurrence_type",
            "leader",
            "created_by",
            "approved_by",
            "schedule_today",
            "schedule_today__hierarchical_level",
            "agent__current_activity_record__hierarchical_level",
        ).filter(pk__in=ids)
    }

    updated: list = []
    errors: list[dict] = []

    for item in data["items"]:
        occurrence = occurrences_by_id.get(item["id"])
        if not occurrence:
            errors.append({"id": str(item["id"]), "detail": "Ocorrência não encontrada."})
            continue
        err = _validate_occurrence_for_batch_extension(occurrence, profile)
        if err:
            label = occurrence.agent.full_name if occurrence.agent_id else str(item["id"])
            errors.append({"id": str(item["id"]), "detail": f"{label}: {err}"})
            continue
        extension = OperationalOccurrenceExtension.objects.create(
            occurrence=occurrence,
            extra_seconds=extra_seconds,
            description=description,
            created_by=actor,
        )
        occurrence._pending_extensions = [extension]
        updated.append(OperationalOccurrenceSerializer(occurrence).data)

    status_code = status.HTTP_200_OK if updated else status.HTTP_400_BAD_REQUEST
    return Response({"results": updated, "errors": errors}, status=status_code)


@api_view(["POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def operational_occurrence_extension_approve(request, pk, extension_id):
    profile = build_operational_profile(request.user)
    if not profile:
        return Response(
            {"detail": "Perfil operacional não encontrado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    occurrence = _get_operational_occurrence(pk)
    if occurrence is None:
        return Response(status=status.HTTP_404_NOT_FOUND)

    try:
        extension = OperationalOccurrenceExtension.objects.select_related(
            "created_by", "approved_by"
        ).get(pk=extension_id, occurrence=occurrence)
    except OperationalOccurrenceExtension.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if extension.approved is not None:
        return Response(
            {"detail": "Solicitação já processada."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not _can_approve_operational_occurrence(profile, occurrence):
        return Response(
            {"detail": "Sem permissão."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = OperationalOccurrenceExtensionApprovalSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    approved = serializer.validated_data["approved"]
    actor = get_agent_for_user(request.user)
    now = timezone.now()

    extension.approved = approved
    extension.approved_by = actor
    extension.approved_at = now
    extension.approval_notes = serializer.validated_data.get("approval_notes", "")
    extension.save(
        update_fields=["approved", "approved_by", "approved_at", "approval_notes"]
    )

    if approved:
        occurrence.forecast_seconds += extension.extra_seconds
        occurrence.save(update_fields=["forecast_seconds"])

    occurrence._pending_extensions = []
    return Response(OperationalOccurrenceSerializer(occurrence).data)


def _require_admin_profile(request):
    profile = build_operational_profile(request.user)
    if not profile or not profile.is_admin:
        return None, Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)
    return profile, None


def _next_smallint_id(model):
    from django.db.models import Max

    current = model.objects.aggregate(max_id=Max("id"))["max_id"] or 0
    return current + 1


@api_view(["GET", "POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_types_config_view(request):
    _, denied = _require_admin_profile(request)
    if denied:
        return denied

    if request.method == "GET":
        qs = StatusType.objects.all().order_by("id")
        return Response({"results": StatusTypeSerializer(qs, many=True).data})

    serializer = StatusTypeWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    pk = data.pop("id", None) or _next_smallint_id(StatusType)
    if StatusType.objects.filter(pk=pk).exists():
        return Response(
            {"detail": f"Já existe um status com o ID {pk}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    instance = StatusType.objects.create(id=pk, **data)
    sync_occurrence_colors_for_status(instance)
    return Response(StatusTypeSerializer(instance).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def status_type_config_detail(request, pk: int):
    _, denied = _require_admin_profile(request)
    if denied:
        return denied

    instance = StatusType.objects.filter(pk=pk).first()
    if not instance:
        return Response(status=status.HTTP_404_NOT_FOUND)

    serializer = StatusTypeWriteSerializer(instance, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    sync_occurrence_colors_for_status(instance)
    return Response(StatusTypeSerializer(instance).data)


@api_view(["GET", "POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def occurrence_types_config_view(request):
    _, denied = _require_admin_profile(request)
    if denied:
        return denied

    if request.method == "GET":
        qs = OccurrenceType.objects.prefetch_related("status_types").all().order_by("name")
        return Response({"results": OccurrenceTypeSerializer(qs, many=True).data})

    serializer = OccurrenceTypeWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = dict(serializer.validated_data)
    if not data.get("color"):
        data["color"] = resolve_occurrence_color(data.get("name", ""))
    pk = data.pop("id", None) or _next_smallint_id(OccurrenceType)
    if OccurrenceType.objects.filter(pk=pk).exists():
        return Response(
            {"detail": f"Já existe um tipo de ocorrência com o ID {pk}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    status_types = data.pop("status_types", None)
    instance = OccurrenceType.objects.create(id=pk, **data)
    if status_types is not None:
        instance.status_types.set(status_types)
    sync_status_colors_for_occurrence(instance)
    instance = OccurrenceType.objects.prefetch_related("status_types").get(pk=instance.pk)
    return Response(OccurrenceTypeSerializer(instance).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def occurrence_type_config_detail(request, pk: int):
    _, denied = _require_admin_profile(request)
    if denied:
        return denied

    instance = OccurrenceType.objects.filter(pk=pk).prefetch_related("status_types").first()
    if not instance:
        return Response(status=status.HTTP_404_NOT_FOUND)

    serializer = OccurrenceTypeWriteSerializer(instance, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    sync_status_colors_for_occurrence(instance)
    instance = OccurrenceType.objects.prefetch_related("status_types").get(pk=instance.pk)
    return Response(OccurrenceTypeSerializer(instance).data)


@api_view(["GET", "POST"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def absence_types_config_view(request):
    _, denied = _require_admin_profile(request)
    if denied:
        return denied

    if request.method == "GET":
        qs = AbsenceType.objects.all().order_by("name")
        return Response({"results": AbsenceTypeSerializer(qs, many=True).data})

    serializer = AbsenceTypeWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    pk = data.pop("id", None) or _next_smallint_id(AbsenceType)
    if AbsenceType.objects.filter(pk=pk).exists():
        return Response(
            {"detail": f"Já existe um tipo de ausência com o ID {pk}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if AbsenceType.objects.filter(code__iexact=data["code"]).exists():
        return Response(
            {"detail": f"Já existe um tipo de ausência com o código {data['code']}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    instance = AbsenceType.objects.create(id=pk, **data)
    clear_absence_type_cache()
    return Response(AbsenceTypeSerializer(instance).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes(ANY_ESCALA_FLEX_VIEW)
def absence_type_config_detail(request, pk: int):
    _, denied = _require_admin_profile(request)
    if denied:
        return denied

    instance = AbsenceType.objects.filter(pk=pk).first()
    if not instance:
        return Response(status=status.HTTP_404_NOT_FOUND)

    serializer = AbsenceTypeWriteSerializer(instance, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    code = serializer.validated_data.get("code")
    if code and (
        AbsenceType.objects.filter(code__iexact=code).exclude(pk=pk).exists()
    ):
        return Response(
            {"detail": f"Já existe um tipo de ausência com o código {code}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    serializer.save()
    clear_absence_type_cache()
    return Response(AbsenceTypeSerializer(instance).data)

