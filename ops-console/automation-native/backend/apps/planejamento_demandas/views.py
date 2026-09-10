"""API — fila de demandas Jira (Planejamento)."""

from __future__ import annotations

import logging

from django.db import IntegrityError
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permission_classes import portal_perm
from apps.access.registry import (
    PLANEJAMENTO_DEMANDAS_JIRA_SYNC,
    PLANEJAMENTO_DEMANDAS_JIRA_VIEW,
)
from apps.planejamento_demandas.models import JiraDemanda, JiraDemandaSyncRun
from apps.planejamento_demandas.services.classifier import categoria_label
from apps.planejamento_demandas.services.issue_actions import (
    assign_demanda,
    comment_demanda,
    demanda_description_fields,
    enrich_demanda,
    list_demanda_comments,
    list_demanda_transitions,
    refresh_demanda_from_jira,
    transition_demanda,
)
from apps.planejamento_demandas.services.kanban import build_kanban_columns
from apps.planejamento_demandas.services.projects import (
    build_pplid_sync_jql,
    build_sync_jql,
    build_team_jql,
    jira_demandas_primary_project,
    jira_demandas_project_keys,
    jira_demandas_team_lan_ids,
)
from apps.planejamento_demandas.services.queue_metrics import STALE_IDLE_DAYS, queue_metrics
from apps.planejamento_demandas.services.status_utils import (
    apply_active_open,
    apply_inactive_open,
    status_kind_label,
)
from apps.planejamento_demandas.services.sync import create_sync_run
from apps.planejamento_demandas.services.sync_runner import (
    reconcile_stale_runs,
    request_sync_cancel,
    schedule_sync_run,
)
from apps.planejamento_demandas.services.team_roster import agents_by_lan, team_roster_stats
from apps.suporte_claro.services.jira_rest import (
    JiraRestError,
    get_issue,
    jira_base_configured,
    jira_proxy_mode,
    resolve_jira_credentials,
    resolve_service_jira_credentials,
    user_jira_configured,
)
from apps.workforce.models import Agent

log = logging.getLogger(__name__)

VIEW_PERM = portal_perm(PLANEJAMENTO_DEMANDAS_JIRA_VIEW)
SYNC_PERM = portal_perm(PLANEJAMENTO_DEMANDAS_JIRA_SYNC)


class DemandaPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100


class PersonSerializer(serializers.Serializer):
    lan_id = serializers.CharField()
    display_name = serializers.CharField()
    agent_id = serializers.CharField(allow_null=True)
    agent_name = serializers.CharField()
    mapped = serializers.BooleanField()
    in_team = serializers.BooleanField()


class JiraDemandaSerializer(serializers.ModelSerializer):
    categoria_label = serializers.SerializerMethodField()
    assignee_person = serializers.SerializerMethodField()
    reporter_person = serializers.SerializerMethodField()
    assignee_needs_review = serializers.SerializerMethodField()
    assignee_unassigned = serializers.SerializerMethodField()
    status_kind_label = serializers.SerializerMethodField()
    queue_age_days = serializers.SerializerMethodField()
    idle_days = serializers.SerializerMethodField()
    queue_age_label = serializers.SerializerMethodField()
    idle_label = serializers.SerializerMethodField()
    staleness = serializers.SerializerMethodField()

    class Meta:
        model = JiraDemanda
        fields = (
            "issue_key",
            "project_key",
            "project_name",
            "summary",
            "description_excerpt",
            "status_name",
            "status_kind",
            "status_kind_label",
            "is_open",
            "assignee_display",
            "assignee_username",
            "reporter_display",
            "reporter_username",
            "in_team_queue",
            "priority_name",
            "issue_type",
            "labels",
            "components",
            "categoria",
            "categoria_label",
            "portal_path",
            "jira_url",
            "created_at_jira",
            "updated_at_jira",
            "synced_at",
            "assignee_person",
            "reporter_person",
            "assignee_needs_review",
            "assignee_unassigned",
            "queue_age_days",
            "idle_days",
            "queue_age_label",
            "idle_label",
            "staleness",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._agents = None

    def _roster(self):
        if self._agents is None:
            ctx = self.context.get("agents")
            self._agents = ctx if ctx is not None else agents_by_lan()
        return self._agents

    def get_categoria_label(self, obj) -> str:
        return categoria_label(obj.categoria)

    def _people(self, obj) -> dict:
        cache = getattr(self, "_people_cache", None)
        if cache is None:
            self._people_cache = {}
            cache = self._people_cache
        if obj.issue_key not in cache:
            cache[obj.issue_key] = enrich_demanda(obj, agents=self._roster())
        return cache[obj.issue_key]

    def get_assignee_person(self, obj):
        return self._people(obj)["assignee_person"]

    def get_reporter_person(self, obj):
        return self._people(obj)["reporter_person"]

    def get_assignee_needs_review(self, obj) -> bool:
        return self._people(obj)["assignee_needs_review"]

    def get_assignee_unassigned(self, obj) -> bool:
        return self._people(obj)["assignee_unassigned"]

    def get_status_kind_label(self, obj) -> str:
        return status_kind_label(obj.status_kind)

    def _queue(self, obj) -> dict:
        cache = getattr(self, "_queue_cache", None)
        if cache is None:
            self._queue_cache = {}
            cache = self._queue_cache
        if obj.issue_key not in cache:
            cache[obj.issue_key] = queue_metrics(
                created_at_jira=obj.created_at_jira,
                updated_at_jira=obj.updated_at_jira,
                is_open=obj.is_open,
                status_kind=obj.status_kind,
            )
        return cache[obj.issue_key]

    def get_queue_age_days(self, obj):
        return self._queue(obj)["queue_age_days"]

    def get_idle_days(self, obj):
        return self._queue(obj)["idle_days"]

    def get_queue_age_label(self, obj) -> str:
        return self._queue(obj)["queue_age_label"]

    def get_idle_label(self, obj) -> str:
        return self._queue(obj)["idle_label"]

    def get_staleness(self, obj) -> str:
        return self._queue(obj)["staleness"]


class JiraDemandaDetailSerializer(JiraDemandaSerializer):
    description = serializers.CharField(read_only=True, default="")
    description_html = serializers.CharField(read_only=True, default="")
    comments = serializers.ListField(child=serializers.DictField(), read_only=True, default=list)
    transitions = serializers.ListField(child=serializers.DictField(), read_only=True, default=list)

    class Meta(JiraDemandaSerializer.Meta):
        fields = JiraDemandaSerializer.Meta.fields + (
            "description",
            "description_html",
            "comments",
            "transitions",
        )


class JiraDemandaSyncRunSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    mode_label = serializers.CharField(source="get_mode_display", read_only=True)

    class Meta:
        model = JiraDemandaSyncRun
        fields = (
            "id",
            "status",
            "status_label",
            "mode",
            "mode_label",
            "projects",
            "jql",
            "total_fetched",
            "created_count",
            "updated_count",
            "duplicate_count",
            "pages_processed",
            "phase",
            "phase_index",
            "phase_count",
            "phase_fetched",
            "phase_total",
            "progress_percent",
            "message",
            "error_code",
            "duration_seconds",
            "user_name",
            "retry_of_id",
            "started_at",
            "heartbeat_at",
            "checkpoint_at",
            "cancel_requested_at",
            "finished_at",
        )

    def get_user_name(self, obj) -> str:
        if not obj.user:
            return ""
        return obj.user.get_full_name() or obj.user.username


def _team_lan_ids_upper() -> list[str]:
    return [lan.upper() for lan in jira_demandas_team_lan_ids()]


def _apply_list_filters(qs, params):
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(issue_key__icontains=q)
            | Q(summary__icontains=q)
            | Q(description_excerpt__icontains=q)
            | Q(assignee_display__icontains=q)
            | Q(assignee_username__icontains=q)
            | Q(reporter_display__icontains=q)
        )

    queue = (params.get("queue") or "").strip().lower()
    if queue == "team":
        qs = qs.filter(in_team_queue=True)

    team_member = (params.get("team_member") or "").strip().upper()
    member_role = (params.get("member_role") or "any").strip().lower()
    if team_member:
        if member_role == "assignee":
            qs = qs.filter(assignee_username__iexact=team_member)
        elif member_role == "reporter":
            qs = qs.filter(reporter_username__iexact=team_member)
        else:
            qs = qs.filter(
                Q(assignee_username__iexact=team_member)
                | Q(reporter_username__iexact=team_member)
            )

    assignee_lan = (params.get("assignee_lan") or "").strip().upper()
    if assignee_lan:
        qs = qs.filter(assignee_username__iexact=assignee_lan)

    reporter_lan = (params.get("reporter_lan") or "").strip().upper()
    if reporter_lan:
        qs = qs.filter(reporter_username__iexact=reporter_lan)

    ownership = (params.get("ownership") or "").strip().lower()
    team_lan = _team_lan_ids_upper()
    if ownership == "team_assigned":
        qs = qs.filter(assignee_username__in=team_lan)
    elif ownership == "team_reported":
        qs = qs.filter(reporter_username__in=team_lan)
    elif ownership == "unassigned":
        qs = qs.filter(Q(assignee_username="") | Q(assignee_username__isnull=True))
    elif ownership == "outside_team":
        qs = qs.exclude(Q(assignee_username="") | Q(assignee_username__isnull=True)).exclude(
            assignee_username__in=team_lan
        )

    project = (params.get("project") or "").strip().upper()
    if project:
        qs = qs.filter(project_key=project)

    categoria = (params.get("categoria") or "").strip()
    if categoria:
        qs = qs.filter(categoria=categoria)

    status_filter = (params.get("status") or "").strip()
    if status_filter == "active" and not q:
        qs = apply_active_open(qs)
    elif status_filter == "inactive":
        qs = apply_inactive_open(qs)
    elif status_filter == "open":
        qs = qs.filter(status_kind=JiraDemanda.STATUS_KIND_OPEN)
    elif status_filter == "done":
        qs = qs.filter(status_kind=JiraDemanda.STATUS_KIND_DONE)
    elif status_filter == "cancelled":
        qs = qs.filter(status_kind=JiraDemanda.STATUS_KIND_CANCELLED)
    elif status_filter == "closed":
        qs = qs.filter(is_open=False)

    updated_within = (params.get("updated_within") or "").strip()
    if updated_within.isdigit():
        days = max(1, int(updated_within))
        qs = qs.filter(updated_at_jira__gte=timezone.now() - timezone.timedelta(days=days))

    created_within = (params.get("created_within") or "").strip()
    if created_within.isdigit():
        days = max(1, int(created_within))
        qs = qs.filter(created_at_jira__gte=timezone.now() - timezone.timedelta(days=days))

    stale_days = (params.get("stale_days") or "").strip()
    if stale_days.isdigit():
        days = max(1, int(stale_days))
        cutoff = timezone.now() - timezone.timedelta(days=days)
        qs = qs.filter(
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            updated_at_jira__isnull=False,
            updated_at_jira__lte=cutoff,
        )

    assignee = (params.get("assignee") or "").strip()
    if assignee:
        qs = qs.filter(
            Q(assignee_display__icontains=assignee)
            | Q(assignee_username__icontains=assignee)
        )

    priority = (params.get("priority") or "").strip().lower()
    if priority == "high":
        qs = qs.filter(
            Q(priority_name__icontains="alta")
            | Q(priority_name__icontains="high")
            | Q(priority_name__icontains="highest")
            | Q(priority_name__icontains="critical")
            | Q(priority_name__icontains="blocker")
            | Q(priority_name__icontains="urgent")
        )
    elif priority:
        qs = qs.filter(priority_name__icontains=priority)

    issue_type = (params.get("issue_type") or "").strip()
    if issue_type:
        qs = qs.filter(issue_type__iexact=issue_type)

    age_bucket = (params.get("age_bucket") or "").strip()
    if age_bucket:
        now = timezone.now()
        open_only = qs.filter(
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            created_at_jira__isnull=False,
        )
        if age_bucket == "0_7":
            qs = open_only.filter(created_at_jira__gte=now - timezone.timedelta(days=7))
        elif age_bucket == "8_30":
            qs = open_only.filter(
                created_at_jira__lt=now - timezone.timedelta(days=7),
                created_at_jira__gte=now - timezone.timedelta(days=30),
            )
        elif age_bucket == "31_plus":
            qs = open_only.filter(created_at_jira__lt=now - timezone.timedelta(days=30))

    return qs


def _facet_summary_params(params):
    """Escopo estável para chips/abas — sem status, ownership ou stale."""
    out = {}
    queue = (params.get("queue") or "").strip().lower()
    if queue == "team":
        out["queue"] = "team"
    project = (params.get("project") or "").strip().upper()
    if project:
        out["project"] = project
    return out


def _active_stale_count(qs, cutoff):
    return (
        apply_active_open(qs)
        .filter(
            updated_at_jira__isnull=False,
            updated_at_jira__lte=cutoff,
        )
        .count()
    )


def _age_bucket_counts(qs):
    now = timezone.now()
    base = apply_active_open(qs).filter(created_at_jira__isnull=False)
    b0 = base.filter(created_at_jira__gte=now - timezone.timedelta(days=7)).count()
    b1 = base.filter(
        created_at_jira__lt=now - timezone.timedelta(days=7),
        created_at_jira__gte=now - timezone.timedelta(days=30),
    ).count()
    b2 = base.filter(created_at_jira__lt=now - timezone.timedelta(days=30)).count()
    return [
        {"key": "0_7", "label": "0–7 dias", "total": b0},
        {"key": "8_30", "label": "8–30 dias", "total": b1},
        {"key": "31_plus", "label": "31+ dias", "total": b2},
    ]


def _resolve_user_lan(user) -> str:
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    username = (getattr(user, "username", "") or "").strip().upper()
    team = {lan.upper() for lan in jira_demandas_team_lan_ids()}
    if username in team:
        return username
    agent = Agent.objects.filter(user_lan_id__iexact=username).first()
    if agent and agent.user_lan_id:
        return agent.user_lan_id.upper()
    return username


class JiraDemandaViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, VIEW_PERM]
    pagination_class = DemandaPagination
    serializer_class = JiraDemandaSerializer
    lookup_field = "issue_key"
    lookup_value_regex = r"[A-Z][A-Z0-9]+-\d+"

    def get_queryset(self):
        qs = _apply_list_filters(JiraDemanda.objects.all(), self.request.query_params)
        primary = jira_demandas_primary_project().upper()
        qs = qs.annotate(
            project_priority=Case(
                When(project_key__iexact=primary, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        queue = (self.request.query_params.get("queue") or "").strip().lower()
        sort = (self.request.query_params.get("sort") or "").strip().lower()
        if not sort and queue == "team":
            sort = "queue_age"
        if sort == "queue_age":
            return qs.order_by(
                "project_priority",
                F("created_at_jira").asc(nulls_last=True),
                "issue_key",
            )
        if sort == "idle":
            return qs.order_by(
                "project_priority",
                F("updated_at_jira").asc(nulls_last=True),
                "issue_key",
            )
        if sort == "updated":
            return qs.order_by("project_priority", "-updated_at_jira", "-issue_key")
        if sort == "created":
            return qs.order_by("project_priority", "-created_at_jira", "-issue_key")
        return qs.order_by("project_priority", "-updated_at_jira", "-issue_key")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["agents"] = agents_by_lan()
        return ctx

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        live = (request.query_params.get("live") or "").strip() in ("1", "true", "yes")
        data = JiraDemandaDetailSerializer(instance, context=self.get_serializer_context()).data
        if live and resolve_jira_credentials(request.user):
            try:
                creds = resolve_jira_credentials(request.user)
                payload = get_issue(instance.issue_key, credentials=creds)
                fields = payload.get("fields") or {}
                desc = demanda_description_fields(fields.get("description"))
                data["description"] = desc["plain"]
                data["description_html"] = desc["html"]
                resolution = fields.get("resolutiondate") or fields.get("resolutionDate")
                if resolution:
                    data["resolution_at"] = str(resolution)
                data["transitions"] = list_demanda_transitions(instance.issue_key, user=request.user)
                data["comments"] = list_demanda_comments(instance.issue_key, user=request.user)
            except JiraRestError as exc:
                data["live_error"] = str(exc)
        else:
            fallback = demanda_description_fields(instance.description_excerpt)
            data["description"] = fallback["plain"] or instance.description_excerpt
            data["description_html"] = fallback["html"]
            data["transitions"] = list_demanda_transitions(instance.issue_key, user=request.user)
            data["comments"] = list_demanda_comments(instance.issue_key, user=request.user)
        return Response(data)

    @action(detail=True, methods=["post"], url_path="assign", permission_classes=[IsAuthenticated, SYNC_PERM])
    def assign(self, request, issue_key=None):
        lan_id = (request.data.get("assignee_lan_id") or "").strip()
        if not lan_id:
            return Response(
                {"detail": "Informe assignee_lan_id (LAN ID Jira)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            obj = assign_demanda(issue_key, lan_id, user=request.user)
        except JiraRestError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            JiraDemandaDetailSerializer(obj, context=self.get_serializer_context()).data
        )

    @action(detail=True, methods=["post"], url_path="transition", permission_classes=[IsAuthenticated, SYNC_PERM])
    def transition(self, request, issue_key=None):
        transition_id = (request.data.get("transition_id") or "").strip()
        if not transition_id:
            return Response(
                {"detail": "Informe transition_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            obj = transition_demanda(issue_key, transition_id, user=request.user)
        except JiraRestError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            JiraDemandaDetailSerializer(obj, context=self.get_serializer_context()).data
        )

    @action(detail=True, methods=["post"], url_path="comment", permission_classes=[IsAuthenticated, SYNC_PERM])
    def comment(self, request, issue_key=None):
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response(
                {"detail": "Informe o texto do comentário."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = comment_demanda(issue_key, body, user=request.user)
        except JiraRestError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        detail = JiraDemandaDetailSerializer(
            self.get_object(),
            context=self.get_serializer_context(),
        ).data
        detail["last_comment_id"] = result.get("comment_id")
        detail["comments"] = list_demanda_comments(issue_key)
        return Response(detail)

    @action(detail=True, methods=["post"], url_path="refresh", permission_classes=[IsAuthenticated, SYNC_PERM])
    def refresh(self, request, issue_key=None):
        try:
            obj = refresh_demanda_from_jira(issue_key)
        except JiraRestError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            JiraDemandaDetailSerializer(obj, context=self.get_serializer_context()).data
        )

    @action(detail=False, methods=["get"], url_path="facets")
    def facets(self, request):
        params = request.query_params
        qs = _apply_list_filters(JiraDemanda.objects.all(), params)
        summary_params = _facet_summary_params(params)
        summary_qs = _apply_list_filters(JiraDemanda.objects.all(), summary_params)

        by_project = qs.values("project_key").annotate(total=Count("id")).order_by("project_key")
        by_categoria = qs.values("categoria").annotate(total=Count("id")).order_by("categoria")
        assignees = sorted(
            {a for a in qs.exclude(assignee_display="").values_list("assignee_display", flat=True) if a},
            key=str.casefold,
        )
        stale_cutoff = timezone.now() - timezone.timedelta(days=STALE_IDLE_DAYS)
        team_lan = _team_lan_ids_upper()

        team_base = JiraDemanda.objects.filter(in_team_queue=True)
        if summary_params.get("project"):
            team_base = team_base.filter(project_key=summary_params["project"])
        team_active_base = apply_active_open(team_base)
        team_inactive_base = apply_inactive_open(team_base)

        all_base = JiraDemanda.objects.all()
        if summary_params.get("project"):
            all_base = all_base.filter(project_key=summary_params["project"])
        all_active_base = apply_active_open(all_base)

        open_qs = qs.filter(status_kind=JiraDemanda.STATUS_KIND_OPEN)
        active_qs = apply_active_open(qs)

        return Response(
            {
                "total": qs.count(),
                "open": open_qs.count(),
                "active_open": active_qs.count(),
                "done": qs.filter(status_kind=JiraDemanda.STATUS_KIND_DONE).count(),
                "cancelled": qs.filter(status_kind=JiraDemanda.STATUS_KIND_CANCELLED).count(),
                "closed": qs.filter(is_open=False).count(),
                "stale_open": _active_stale_count(qs, stale_cutoff),
                "team_total": team_base.count(),
                "team_open": team_active_base.count(),
                "team_stale_open": _active_stale_count(team_base, stale_cutoff),
                "team_assigned_open": team_active_base.filter(assignee_username__in=team_lan).count(),
                "team_reported_open": team_active_base.filter(reporter_username__in=team_lan).count(),
                "unassigned_open": team_active_base.filter(
                    Q(assignee_username="") | Q(assignee_username__isnull=True)
                ).count(),
                "outside_team_open": team_active_base.exclude(
                    Q(assignee_username="") | Q(assignee_username__isnull=True)
                )
                .exclude(assignee_username__in=team_lan)
                .count(),
                "all_active_open": all_active_base.count(),
                "team_inactive_open": team_inactive_base.count(),
                "recent_created_open": (
                    team_active_base if summary_params.get("queue") == "team" else all_active_base
                )
                .filter(created_at_jira__gte=timezone.now() - timezone.timedelta(days=7))
                .count(),
                "age_buckets": _age_bucket_counts(team_base if summary_params.get("queue") == "team" else all_base),
                "priorities": [
                    {"value": row["priority_name"], "total": row["total"]}
                    for row in qs.exclude(priority_name="")
                    .values("priority_name")
                    .annotate(total=Count("id"))
                    .order_by("-total")[:12]
                ],
                "issue_types": [
                    {"value": row["issue_type"], "total": row["total"]}
                    for row in qs.exclude(issue_type="")
                    .values("issue_type")
                    .annotate(total=Count("id"))
                    .order_by("-total")[:12]
                ],
                "stale_days_threshold": STALE_IDLE_DAYS,
                "pplid_total": summary_qs.filter(
                    project_key__iexact=jira_demandas_primary_project()
                ).count(),
                "projects": list(by_project),
                "categorias": [
                    {
                        "value": row["categoria"],
                        "label": categoria_label(row["categoria"]),
                        "total": row["total"],
                    }
                    for row in by_categoria
                ],
                "assignees": assignees[:80],
                "sync_projects": jira_demandas_project_keys(),
                "primary_project": jira_demandas_primary_project(),
                "sync_jql_preview": build_sync_jql(),
                "pplid_jql_preview": build_pplid_sync_jql(),
                "team_jql_preview": build_team_jql(active_only=True),
                "team_lan_ids": jira_demandas_team_lan_ids(),
            }
        )

    @action(detail=False, methods=["get"], url_path="kanban")
    def kanban(self, request):
        params = request.query_params
        effective = dict(params.items())
        if not (effective.get("status") or "").strip() and not (effective.get("q") or "").strip():
            effective["status"] = "active"

        qs = _apply_list_filters(JiraDemanda.objects.all(), effective)
        primary = jira_demandas_primary_project().upper()
        qs = qs.annotate(
            project_priority=Case(
                When(project_key__iexact=primary, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("project_priority", "-updated_at_jira", "-issue_key")

        max_items = 800
        per_column = min(60, max(10, int((effective.get("column_limit") or "40").strip() or 40)))
        total_matching = qs.count()
        items = list(qs[:max_items])
        columns = build_kanban_columns(items, per_column_limit=per_column)
        serializer = JiraDemandaSerializer(
            items,
            many=True,
            context=self.get_serializer_context(),
        )
        serialized = {row["issue_key"]: row for row in serializer.data}
        payload_columns = []
        for col in columns:
            payload_columns.append(
                {
                    "status_name": col["status_name"],
                    "status_kind": col["status_kind"],
                    "total": col["total"],
                    "has_more": col["has_more"],
                    "items": [serialized[item.issue_key] for item in col["items"]],
                }
            )
        return Response(
            {
                "columns": payload_columns,
                "total": total_matching,
                "loaded": len(items),
                "truncated": total_matching > len(items),
            }
        )

    @action(detail=False, methods=["get"], url_path="export")
    def export_csv(self, request):
        import csv
        from io import StringIO

        from django.http import HttpResponse

        qs = self.get_queryset()[:2000]
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "issue_key",
                "project_key",
                "summary",
                "status_name",
                "assignee_username",
                "reporter_username",
                "priority_name",
                "issue_type",
                "categoria",
                "created_at_jira",
                "updated_at_jira",
                "jira_url",
            ]
        )
        for row in qs.iterator():
            writer.writerow(
                [
                    row.issue_key,
                    row.project_key,
                    row.summary,
                    row.status_name,
                    row.assignee_username,
                    row.reporter_username,
                    row.priority_name,
                    row.issue_type,
                    row.categoria,
                    row.created_at_jira.isoformat() if row.created_at_jira else "",
                    row.updated_at_jira.isoformat() if row.updated_at_jira else "",
                    row.jira_url,
                ]
            )
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="demandas-jira.csv"'
        return response

    @action(detail=False, methods=["get"], url_path="team-roster")
    def team_roster(self, request):
        qs = _apply_list_filters(
            JiraDemanda.objects.all(),
            {"queue": "team", "status": "active"},
        )
        return Response(
            {
                "members": team_roster_stats(queryset=qs),
                "team_jql_preview": build_team_jql(active_only=True),
            }
        )

    @action(detail=False, methods=["get"], url_path="config")
    def config(self, request):
        service_ok = resolve_service_jira_credentials() is not None
        user_ok = user_jira_configured(request.user)
        can_sync = user_ok
        last = JiraDemandaSyncRun.objects.filter(status=JiraDemandaSyncRun.STATUS_SUCCESS).first()
        return Response(
            {
                "jira_configured": jira_base_configured(),
                "service_token_configured": service_ok,
                "user_token_configured": user_ok,
                "can_sync": can_sync,
                "can_assign": can_sync,
                "last_sync": JiraDemandaSyncRunSerializer(last).data if last else None,
                "team_lan_ids": jira_demandas_team_lan_ids(),
                "primary_project": jira_demandas_primary_project(),
                "current_user_lan_id": _resolve_user_lan(request.user),
                "jira_proxy_mode": jira_proxy_mode(),
            }
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated, SYNC_PERM])
def sync_demandas_view(request):
    reconcile_stale_runs()
    running = (
        JiraDemandaSyncRun.objects.filter(
            status__in=[
                JiraDemandaSyncRun.STATUS_QUEUED,
                JiraDemandaSyncRun.STATUS_RUNNING,
            ],
        )
        .order_by("-started_at")
        .first()
    )
    if running:
        return Response(
            {
                "detail": "Sincronização já em andamento. Acompanhe o progresso abaixo.",
                "run": JiraDemandaSyncRunSerializer(running).data,
            },
            status=status.HTTP_409_CONFLICT,
        )
    try:
        run = create_sync_run(
            user=request.user,
            force_full=bool(request.data.get("full", False)),
        )
    except JiraRestError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except IntegrityError as exc:
        log.exception("IntegrityError ao enfileirar sync Jira demandas")
        return Response(
            {
                "detail": (
                    "Erro de integridade ao iniciar a sincronização. "
                    "Verifique se as migrations de planejamento_demandas estão aplicadas "
                    "e tente novamente."
                ),
                "error": str(exc),
            },
            status=status.HTTP_409_CONFLICT,
        )
    if not schedule_sync_run(run.pk):
        run.status = JiraDemandaSyncRun.STATUS_FAILED
        run.message = "Sync já em execução neste servidor."
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "finished_at"])
        return Response(
            {"detail": run.message},
            status=status.HTTP_409_CONFLICT,
        )
    return Response(JiraDemandaSyncRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
@permission_classes([IsAuthenticated, VIEW_PERM])
def sync_run_detail_view(request, run_id: int):
    reconcile_stale_runs()
    run = JiraDemandaSyncRun.objects.filter(pk=run_id).first()
    if run is None:
        return Response({"detail": "Sync não encontrada."}, status=status.HTTP_404_NOT_FOUND)
    return Response(JiraDemandaSyncRunSerializer(run).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, VIEW_PERM])
def sync_history_view(request):
    reconcile_stale_runs()
    runs = JiraDemandaSyncRun.objects.all()[:20]
    return Response(JiraDemandaSyncRunSerializer(runs, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, SYNC_PERM])
def sync_cancel_view(request, run_id: int):
    run, changed = request_sync_cancel(run_id)
    if run is None:
        return Response({"detail": "Sync não encontrada."}, status=status.HTTP_404_NOT_FOUND)
    response_status = status.HTTP_202_ACCEPTED if changed else status.HTTP_409_CONFLICT
    return Response(JiraDemandaSyncRunSerializer(run).data, status=response_status)


@api_view(["POST"])
@permission_classes([IsAuthenticated, SYNC_PERM])
def sync_retry_view(request, run_id: int):
    reconcile_stale_runs()
    source = JiraDemandaSyncRun.objects.filter(pk=run_id).first()
    if source is None:
        return Response({"detail": "Sync não encontrada."}, status=status.HTTP_404_NOT_FOUND)
    if not source.is_terminal:
        return Response(
            {"detail": "A sincronização ainda está ativa."},
            status=status.HTTP_409_CONFLICT,
        )
    active = JiraDemandaSyncRun.objects.filter(
        status__in=[JiraDemandaSyncRun.STATUS_QUEUED, JiraDemandaSyncRun.STATUS_RUNNING]
    ).first()
    if active:
        return Response(
            {"detail": "Já existe uma sincronização ativa.", "run": JiraDemandaSyncRunSerializer(active).data},
            status=status.HTTP_409_CONFLICT,
        )
    try:
        run = create_sync_run(
            user=request.user,
            force_full=bool(request.data.get("full", False)),
            retry_of=source,
        )
    except JiraRestError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if not schedule_sync_run(run.pk):
        run.status = JiraDemandaSyncRun.STATUS_FAILED
        run.error_code = "worker_busy"
        run.message = "Sync já em execução neste servidor."
        run.finished_at = timezone.now()
        run.save()
        return Response({"detail": run.message}, status=status.HTTP_409_CONFLICT)
    return Response(JiraDemandaSyncRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)
