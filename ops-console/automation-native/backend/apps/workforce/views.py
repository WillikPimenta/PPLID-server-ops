from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, action, permission_classes
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db.models import Count, Q, Prefetch
from django.db.models.functions import TruncMonth, TruncYear
from django.db import transaction
from datetime import datetime, timedelta
from django.utils import timezone

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.access.view_mixins import (
    AgentHistoryPortalPermissionMixin,
    HeadcountPortalPermissionMixin,
)
from .models import Agent, AgentHistory, CycleChangeAudit, UserProfile
from .agent_column_filters import apply_column_filters, build_column_filter_options
from .serializers import (
    AgentBulkUpdateSerializer,
    AgentCycleChangeSerializer,
    AgentHistorySerializer,
    AgentSerializer,
    UserProfileSerializer,
)
from .services.cycle_change import CycleChangeError, apply_cycle_change
from .services.cycle_preview import build_cycle_change_preview


BULK_PATCH_MAX_IDS = 100
BULK_PATCH_FORBIDDEN_FIELDS = {"full_name", "user_lan_id", "jira_api_token"}


class AgentViewSet(HeadcountPortalPermissionMixin, viewsets.ModelViewSet):
    queryset = Agent.objects.select_related("created_by", "updated_by").order_by(
        "full_name"
    )
    serializer_class = AgentSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        'active': ['exact'],
        'hire_date': ['gte', 'lte', 'exact', 'isnull'],
        'email': ['exact', 'isnull'],
    }
    search_fields = ['full_name', 'user_lan_id', 'email']
    ordering_fields = ['full_name', 'user_lan_id', 'hire_date', 'created_at', 'updated_at']
    ordering = ['full_name']

    def _include_current_history(self):
        raw = self.request.query_params.get("include_current_history", "true")
        return str(raw).lower() not in {"0", "false", "no"}

    def get_queryset(self):
        qs = super().get_queryset()
        if self._include_current_history():
            current_history_qs = (
                AgentHistory.objects.filter(active=True, final_date__isnull=True)
                .select_related("leader", "facilitator")
                .order_by("-start_date")
            )
            qs = qs.prefetch_related(
                Prefetch("history", queryset=current_history_qs, to_attr="current_histories")
            )
        if self.action == "list":
            qs = apply_column_filters(qs, self.request.query_params)
        return qs

    @action(detail=False, methods=["get"], url_path="column-filter-options")
    def column_filter_options(self, request):
        base_qs = Agent.objects.select_related("created_by", "updated_by").order_by("full_name")
        base_qs = self.filter_queryset(base_qs)
        current_history_qs = (
            AgentHistory.objects.filter(active=True, final_date__isnull=True)
            .select_related("leader", "facilitator")
            .order_by("-start_date")
        )
        base_qs = base_qs.prefetch_related(
            Prefetch("history", queryset=current_history_qs, to_attr="current_histories")
        )
        return Response(build_column_filter_options(base_qs))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_current_history"] = self._include_current_history()
        return context

    def perform_create(self, serializer):
        """Set created_by to current user when creating an agent."""
        if self.request.user.is_authenticated:
            serializer.save(created_by=self.request.user, updated_by=self.request.user)
        else:
            serializer.save()

    def perform_update(self, serializer):
        """Set updated_by to current user when updating an agent."""
        if self.request.user.is_authenticated:
            serializer.save(updated_by=self.request.user)
        else:
            serializer.save()

    @action(detail=True, methods=["post"], url_path="preview-cycle-change")
    def preview_cycle_change(self, request, pk=None):
        agent = self.get_object()
        payload = AgentCycleChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        preview = build_cycle_change_preview(
            agent,
            action=data["action"],
            movement_date=data.get("movement_date"),
            agent_payload=data.get("agent"),
            cycle_payload=data.get("cycle"),
            external_movement_type=data.get("external_movement_type"),
        )
        status_code = status.HTTP_200_OK if not preview.blocked else status.HTTP_400_BAD_REQUEST
        return Response(preview.to_response(), status=status_code)

    @action(detail=True, methods=["post"], url_path="apply-cycle-change")
    def apply_cycle_change(self, request, pk=None):
        agent = self.get_object()
        payload = AgentCycleChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        try:
            result = apply_cycle_change(
                agent,
                action=data["action"],
                movement_date=data.get("movement_date"),
                agent_payload=data.get("agent"),
                cycle_payload=data.get("cycle"),
                external_movement_type=data.get("external_movement_type"),
                updated_by=request.user if request.user.is_authenticated else None,
                preview_id=str(data["preview_id"]) if data.get("preview_id") else None,
            )
        except CycleChangeError as exc:
            detail = getattr(exc, "message_dict", None) or {"detail": exc.messages}
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        context = self.get_serializer_context()
        context["include_current_history"] = True
        serializer = AgentSerializer(result.agent, context=context)
        body = serializer.data
        body["cycle_change_result"] = {
            "operation_id": result.operation_id,
            "cycle_changed": result.cycle_changed,
            "terminated": result.terminated,
            "closed_history_id": result.closed_history_id,
            "created_history_id": result.created_history_id,
            "field_changes": result.field_changes or [],
            "entities": result.entities or [],
            "technical": result.technical or [],
            "estimated_records": result.estimated_records,
            "performed_at": timezone.now().isoformat(),
        }
        return Response(body)

    @action(detail=True, methods=["get"], url_path="cycle-change-audits")
    def cycle_change_audits(self, request, pk=None):
        agent = self.get_object()
        rows = (
            CycleChangeAudit.objects.filter(agent=agent)
            .select_related("performed_by")
            .order_by("-created_at")[:50]
        )
        results = []
        for row in rows:
            results.append(
                {
                    "id": str(row.id),
                    "operation_id": str(row.operation_id),
                    "action": row.action,
                    "success": row.success,
                    "error_detail": row.error_detail,
                    "closed_history_id": str(row.closed_history_id)
                    if row.closed_history_id
                    else None,
                    "created_history_id": str(row.created_history_id)
                    if row.created_history_id
                    else None,
                    "movement_date": row.movement_date.isoformat() if row.movement_date else None,
                    "field_changes": row.field_changes,
                    "entities": row.entities,
                    "technical": row.technical,
                    "result_summary": row.result_summary,
                    "performed_by": getattr(row.performed_by, "username", None),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return Response({"results": results})

    @action(detail=False, methods=["post"], url_path="bulk-patch")
    def bulk_patch(self, request):
        agent_ids = request.data.get("agent_ids") or []
        updates = request.data.get("updates") or {}

        if not isinstance(agent_ids, list) or not agent_ids:
            return Response(
                {"detail": "Informe agent_ids como lista não vazia."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(agent_ids) > BULK_PATCH_MAX_IDS:
            return Response(
                {"detail": f"Máximo de {BULK_PATCH_MAX_IDS} colaboradores por operação."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(updates, dict) or not updates:
            return Response(
                {"detail": "Informe updates com ao menos um campo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        forbidden = BULK_PATCH_FORBIDDEN_FIELDS.intersection(updates.keys())
        if forbidden:
            return Response(
                {
                    "detail": (
                        "Campos não permitidos em edição em massa: "
                        + ", ".join(sorted(forbidden))
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload_serializer = AgentBulkUpdateSerializer(data=updates)
        payload_serializer.is_valid(raise_exception=True)
        validated_updates = payload_serializer.validated_data
        if not validated_updates:
            return Response(
                {"detail": "Informe updates com ao menos um campo válido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agents = list(Agent.objects.filter(id__in=agent_ids))
        found_ids = {str(agent.id) for agent in agents}
        missing_ids = [str(agent_id) for agent_id in agent_ids if str(agent_id) not in found_ids]

        updated_count = 0
        errors = [{"agent_id": agent_id, "detail": "Colaborador não encontrado."} for agent_id in missing_ids]

        with transaction.atomic():
            for agent in agents:
                item_serializer = AgentSerializer(
                    agent,
                    data=validated_updates,
                    partial=True,
                    context=self.get_serializer_context(),
                )
                if not item_serializer.is_valid():
                    errors.append({"agent_id": str(agent.id), "detail": item_serializer.errors})
                    continue
                if request.user.is_authenticated:
                    item_serializer.save(updated_by=request.user)
                else:
                    item_serializer.save()
                updated_count += 1

        return Response({"updated": updated_count, "errors": errors})

    def destroy(self, request, *args, **kwargs):
        """Prevent agent deletion - return HTTP 405 Method Not Allowed."""
        return Response(
            {"detail": "A exclusão de colaboradores não é permitida. Use o campo 'ativo' para desativar."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )


class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.select_related("user", "agent").order_by(
        "-created_at"
    )
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated, portal_perm(R.ADM_USERS_MANAGE)]


class AgentHistoryViewSet(AgentHistoryPortalPermissionMixin, viewsets.ModelViewSet):
    queryset = AgentHistory.objects.select_related(
        "agent",
        "leader",
        "facilitator",
    ).order_by("-start_date")
    serializer_class = AgentHistorySerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = {
        "agent": ["exact"],
        "active": ["exact"],
        "team": ["icontains"],
    }
    ordering_fields = ["start_date", "final_date", "created_at"]
    ordering = ["-start_date"]

    def get_queryset(self):
        qs = super().get_queryset()
        lan_id = self.request.query_params.get("lan_id")
        if lan_id:
            qs = qs.filter(agent__user_lan_id__iexact=lan_id.strip())
        return qs


@api_view(['GET'])
@permission_classes([IsAuthenticated, portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)])
def dashboard_overview(request):
    """Dashboard overview with general headcount metrics."""
    from apps.workforce.services.agent_active_reconcile import (
        summarize_agent_history_consistency,
    )

    # Basic counts
    total_agents = Agent.objects.count()
    active_agents = Agent.objects.filter(active=True).count()
    inactive_agents = total_agents - active_agents
    
    # Agents with email
    agents_with_email = Agent.objects.exclude(Q(email='') | Q(email__isnull=True)).count()
    agents_without_email = total_agents - agents_with_email
    
    # Recent hires (last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_hires = Agent.objects.filter(hire_date__gte=thirty_days_ago).count()
    
    # Agents with hire dates
    agents_with_hire_date = Agent.objects.filter(hire_date__isnull=False).count()

    consistency_full = summarize_agent_history_consistency(sample_limit=0)
    consistency = {
        "open_histories": consistency_full["open_histories"],
        "stale_active": consistency_full["stale_active"],
        "orphan_open": consistency_full["orphan_open"],
        "multi_open": consistency_full["multi_open"],
        "active_agents_vs_open_histories_delta": consistency_full[
            "active_agents_vs_open_histories_delta"
        ],
    }
    
    return Response({
        'total_agents': total_agents,
        'active_agents': active_agents,
        'inactive_agents': inactive_agents,
        'agents_with_email': agents_with_email,
        'agents_without_email': agents_without_email,
        'recent_hires': recent_hires,
        'agents_with_hire_date': agents_with_hire_date,
        'activity_rate': round((active_agents / total_agents * 100), 1) if total_agents > 0 else 0,
        'email_completion_rate': round((agents_with_email / total_agents * 100), 1) if total_agents > 0 else 0,
        'consistency': consistency,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)])
def dashboard_hiring_trends(request):
    """Dashboard hiring trends by month for the last 12 months."""
    
    # Get hiring data by month for the last 12 months
    twelve_months_ago = timezone.now() - timedelta(days=365)
    
    hiring_by_month = (Agent.objects
                      .filter(hire_date__gte=twelve_months_ago, hire_date__isnull=False)
                      .annotate(month=TruncMonth('hire_date'))
                      .values('month')
                      .annotate(count=Count('id'))
                      .order_by('month'))
    
    # Format data for frontend
    trends_data = []
    for item in hiring_by_month:
        trends_data.append({
            'month': item['month'].strftime('%Y-%m'),
            'month_name': item['month'].strftime('%b %Y'),
            'count': item['count']
        })
    
    return Response({
        'hiring_trends': trends_data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)])
def dashboard_team_distribution(request):
    """Dashboard showing distribution by teams/sectors from agent history."""
    
    # Get current team distribution (active history records)
    team_distribution = (AgentHistory.objects
                        .filter(active=True, final_date__isnull=True)
                        .exclude(Q(team='') | Q(team__isnull=True))
                        .values('team')
                        .annotate(count=Count('agent', distinct=True))
                        .order_by('-count')[:10])  # Top 10 teams
    
    # Get location distribution
    location_distribution = (AgentHistory.objects
                           .filter(active=True, final_date__isnull=True)
                           .exclude(Q(location='') | Q(location__isnull=True))
                           .values('location')
                           .annotate(count=Count('agent', distinct=True))
                           .order_by('-count')[:10])  # Top 10 locations
    
    # Get job title distribution
    job_title_distribution = (AgentHistory.objects
                            .filter(active=True, final_date__isnull=True)
                            .exclude(Q(job_title='') | Q(job_title__isnull=True))
                            .values('job_title')
                            .annotate(count=Count('agent', distinct=True))
                            .order_by('-count')[:10])  # Top 10 job titles
    
    return Response({
        'team_distribution': list(team_distribution),
        'location_distribution': list(location_distribution),
        'job_title_distribution': list(job_title_distribution),
    })
