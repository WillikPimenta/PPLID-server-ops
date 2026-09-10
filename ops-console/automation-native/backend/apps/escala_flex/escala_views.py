from datetime import datetime

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from apps.escala_flex.rbac import ESCALAS_CONSULTA, ESCALAS_IMPORT, ESCALAS_VIEW
from rest_framework.response import Response

from .models import Escala
from .planning_serializers import EscalaSerializer
from .serializers import PublishedEscalaUpdateSerializer, ScheduleTodaySerializer
from .services.escala_column_filters import (
    apply_escala_column_filters,
    build_escala_column_filter_options,
)
from .services.escala_edit import PublishedEscalaEditService
from .services.permissions import (
    build_operational_profile,
    can_access_escala_consulta,
    can_edit_published_schedule,
    scope_escala_queryset,
)
from .scoping import constrain_escala_query_params


class EscalaPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def _base_escala_queryset(profile, *, apply_profile_scope: bool = True, user=None):
    qs = Escala.objects.select_related(
        "agent",
        "leader",
        "job_activity",
        "location",
    ).order_by("data", "agent__full_name")
    return scope_escala_queryset(qs, profile, apply_profile_scope=apply_profile_scope, user=user)


def build_escala_queryset(request, profile, *, apply_profile_scope: bool = True):
    qs = _base_escala_queryset(
        profile,
        apply_profile_scope=apply_profile_scope,
        user=request.user,
    )

    params = constrain_escala_query_params(request.user, request.query_params)

    if month := params.get("month"):
        try:
            start = datetime.strptime(month, "%Y-%m").date()
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1, day=1)
            else:
                end = start.replace(month=start.month + 1, day=1)
            qs = qs.filter(data__gte=start, data__lt=end)
        except ValueError:
            raise ValueError("Parâmetro month inválido. Use YYYY-MM.")

    if date_str := params.get("date"):
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Parâmetro date inválido. Use YYYY-MM-DD.")
        qs = qs.filter(data=target)

    if lan := params.get("agent_lan_id"):
        qs = qs.filter(agent__user_lan_id__iexact=lan)

    if equipe := params.get("equipe"):
        qs = qs.filter(equipe=equipe)

    if activity := params.get("activity"):
        qs = qs.filter(job_activity__name__icontains=activity)

    if location := params.get("location"):
        qs = qs.filter(
            Q(location__city_name__icontains=location)
            | Q(location__display_name__icontains=location)
        )

    if leader_lan := params.get("leader_lan_id"):
        qs = qs.filter(leader__user_lan_id__iexact=leader_lan)

    if search := params.get("search"):
        qs = qs.filter(
            Q(agent__full_name__icontains=search)
            | Q(agent__user_lan_id__icontains=search)
        )

    if day_schedule := params.get("dia_escala"):
        qs = qs.filter(
            Q(dia_escala=day_schedule)
            | (Q(dia_escala="") & Q(horario=day_schedule))
        )

    return apply_escala_column_filters(qs, request.query_params)


def _apply_period_filters(qs, request):
    if month := request.query_params.get("month"):
        try:
            start = datetime.strptime(month, "%Y-%m").date()
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1, day=1)
            else:
                end = start.replace(month=start.month + 1, day=1)
            qs = qs.filter(data__gte=start, data__lt=end)
        except ValueError:
            raise ValueError("Parâmetro month inválido. Use YYYY-MM.")

    if date_str := request.query_params.get("date"):
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Parâmetro date inválido. Use YYYY-MM-DD.")
        qs = qs.filter(data=target)

    return qs


def paginate_escala_response(request, qs):
    paginator = EscalaPagination()
    page = paginator.paginate_queryset(qs, request)
    serializer = EscalaSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(["GET"])
@permission_classes(ESCALAS_CONSULTA)
def escala_consulta_view(request):
    if not can_access_escala_consulta(request.user):
        return Response(
            {"detail": "Colaborador não vinculado ao usuário logado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    profile = build_operational_profile(request.user)
    try:
        qs = build_escala_queryset(request, profile)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return paginate_escala_response(request, qs)


@api_view(["GET"])
@permission_classes(ESCALAS_CONSULTA)
def escala_filter_options_view(request):
    if not can_access_escala_consulta(request.user):
        return Response(
            {"detail": "Colaborador não vinculado ao usuário logado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    profile = build_operational_profile(request.user)
    try:
        qs = _base_escala_queryset(profile, user=request.user)
        qs = _apply_period_filters(qs, request)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    columns = build_escala_column_filter_options(qs, include_horario=True)

    equipes = [v for v in columns.get("equipe", []) if v and v != "—"]
    activities = [v for v in columns.get("activity_name", []) if v and v != "—"]
    locations = [v for v in columns.get("location_name", []) if v and v != "—"]
    dia_escalas = [v for v in columns.get("dia_escala", []) if v and v != "—"]

    leaders_map: dict[str, str] = {}
    for name, lan in qs.exclude(leader__isnull=True).values_list(
        "leader__full_name",
        "leader__user_lan_id",
    ):
        if name and lan:
            leaders_map[lan.lower()] = name
    leaders = [
        {"lan_id": lan, "name": name}
        for lan, name in sorted(leaders_map.items(), key=lambda x: x[1].lower())
    ]

    return Response(
        {
            "equipes": equipes,
            "activities": activities,
            "locations": locations,
            "leaders": leaders,
            "dia_escalas": dia_escalas,
            "columns": columns,
        }
    )


def _apply_escala_edit(request, escala: Escala):
    profile = build_operational_profile(request.user)
    if not profile or not can_edit_published_schedule(profile, escala.agent, user=request.user):
        return Response(
            {"detail": "Sem permissão para alterar a escala publicada."},
            status=status.HTTP_403_FORBIDDEN,
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

    payload = {
        "escala": EscalaSerializer(escala).data,
        "schedule_today": (
            ScheduleTodaySerializer(schedule_today).data if schedule_today else None
        ),
    }
    return Response(payload)


@api_view(["PATCH"])
@permission_classes(ESCALAS_CONSULTA)
def escala_detail_view(request, pk):
    if not can_access_escala_consulta(request.user):
        return Response(
            {"detail": "Colaborador não vinculado ao usuário logado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    profile = build_operational_profile(request.user)
    try:
        qs = _base_escala_queryset(profile, user=request.user)
        escala = qs.get(pk=pk)
    except Escala.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)

    return _apply_escala_edit(request, escala)
