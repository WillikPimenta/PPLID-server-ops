from __future__ import annotations

from collections import Counter

from django.db.models import Avg, DurationField, ExpressionWrapper, F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permission_classes import portal_perm, portal_perm_any
from apps.access.registry import (
    OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER,
    OPERACAO_SUPORTE_OPERACIONAL_CANCEL,
    OPERACAO_SUPORTE_OPERACIONAL_CREATE,
    OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
    OPERACAO_SUPORTE_OPERACIONAL_VIEW,
    QUAL_CAPACITACAO_SUPORTE_ANSWER,
    QUAL_CAPACITACAO_SUPORTE_ASSIGN,
    QUAL_CAPACITACAO_SUPORTE_VIEW,
)
from apps.access.resolve import user_has_any_permission
from apps.access.services.agents_with_permission import users_with_permission
from apps.auditoria.models import AuditoriaCatalogItem
from apps.auditoria.services.catalog_items import get_catalog_values, seed_catalog_defaults
from apps.dimensoes_processos.models import ProjecaoSla

from apps.workforce.models import Agent

from .categories import categories_payload
from .models import OperationalSupportNotice, OperationalSupportRequest
from .scoping import scoped_queryset
from .serializers import (
    AnswerSerializer,
    CancelSerializer,
    LeaderDecisionSerializer,
    OperationalSupportCreateSerializer,
    OperationalSupportNoticeSerializer,
    OperationalSupportNoticeUpdateSerializer,
    OperationalSupportRequestListSerializer,
    OperationalSupportRequestSerializer,
)
from .services.workflow import (
    Status,
    WorkflowError,
    answer_request,
    assign_request,
    cancel_request,
    create_request,
    leader_decision,
)
from .services.fila_online import (
    has_available_support_agent,
    list_presencial_agents,
    online_support_agent_count,
    queue_positions_by_id,
)

from .services.notices import mural_notices_queryset

RequestCreatePerm = portal_perm_any(
    OPERACAO_SUPORTE_OPERACIONAL_CREATE,
    QUAL_CAPACITACAO_SUPORTE_ANSWER,
)
NoticeManagePerm = portal_perm(OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE)
ApprovePerm = portal_perm(OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER)
CancelPerm = portal_perm(OPERACAO_SUPORTE_OPERACIONAL_CANCEL)
AssignPerm = portal_perm(QUAL_CAPACITACAO_SUPORTE_ASSIGN)
AnswerPerm = portal_perm(QUAL_CAPACITACAO_SUPORTE_ANSWER)
AnyViewPerm = portal_perm_any(
    OPERACAO_SUPORTE_OPERACIONAL_VIEW,
    QUAL_CAPACITACAO_SUPORTE_VIEW,
)
ActiveAgentsPerm = portal_perm(OPERACAO_SUPORTE_OPERACIONAL_CREATE)


class FilterValidationError(ValueError):
    pass


class NoticePagination(PageNumberPagination):
    page_size = 5
    page_size_query_param = "page_size"
    max_page_size = 50


class NoticeListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [NoticeManagePerm()]
        return [AnyViewPerm()]

    def get(self, request):
        scope = (request.query_params.get("scope") or "mural").strip().lower()
        include_all = scope == "manage" and user_has_any_permission(
            request.user,
            OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
        )
        notices = mural_notices_queryset(include_all=include_all)
        paginator = NoticePagination()
        page = paginator.paginate_queryset(notices, request)
        data = OperationalSupportNoticeSerializer(page, many=True).data
        return paginator.get_paginated_response(data)

    def post(self, request):
        serializer = OperationalSupportNoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notice = serializer.save(created_by=request.user)
        return Response(OperationalSupportNoticeSerializer(notice).data, status=status.HTTP_201_CREATED)


class NoticeDetailView(APIView):
    permission_classes = [NoticeManagePerm]

    def patch(self, request, notice_id):
        notice = get_object_or_404(OperationalSupportNotice, pk=notice_id)
        serializer = OperationalSupportNoticeUpdateSerializer(
            notice,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        notice = serializer.save()
        return Response(OperationalSupportNoticeSerializer(notice).data)


def _workflow_error_response(exc: WorkflowError) -> Response:
    return Response({"detail": exc.message}, status=exc.status_code)


def _parse_date(value: str | None):
    if not value:
        return None
    from datetime import datetime

    return datetime.strptime(value, "%Y-%m-%d").date()


def _apply_filters(qs, params, *, user):
    view = (params.get("view") or "").strip()
    has_cap = user_has_any_permission(user, QUAL_CAPACITACAO_SUPORTE_VIEW)
    has_op = user_has_any_permission(user, OPERACAO_SUPORTE_OPERACIONAL_VIEW)

    if view == "leader_pending":
        qs = qs.filter(status=Status.PENDING_LEADER)
    elif view == "in_progress":
        qs = qs.filter(
            status__in=[
                Status.PENDING_LEADER,
                Status.PENDING_SUPPORT,
                Status.PENDING_OFFLINE,
                Status.IN_ANALYSIS,
            ]
        )
    elif view == "support_queue":
        # Capacitação nunca vê pending_leader na fila.
        qs = qs.filter(
            status=Status.PENDING_SUPPORT,
            request_type=OperationalSupportRequest.RequestType.ONLINE,
        )
    elif view == "history":
        history_statuses = [Status.ANSWERED, Status.REJECTED_LEADER, Status.CANCELLED]
        if has_cap and not has_op:
            qs = qs.filter(status__in=history_statuses)
        else:
            qs = qs.filter(status__in=history_statuses)

    status_param = (params.get("status") or "").strip()
    if status_param:
        # Capacitação não pode forçar pending_leader via filtro.
        if status_param == Status.PENDING_LEADER and has_cap and not has_op:
            qs = qs.none()
        else:
            qs = qs.filter(status=status_param)

    request_type = (params.get("request_type") or "").strip().lower()
    if request_type:
        if request_type not in OperationalSupportRequest.RequestType.values:
            raise FilterValidationError("Tipo de solicitação inválido.")
        qs = qs.filter(request_type=request_type)

    operation_origin = (params.get("operation_origin") or "").strip().lower()
    if operation_origin:
        if operation_origin not in OperationalSupportRequest.OperationOrigin.values:
            raise FilterValidationError("Origem da operação inválida.")
        qs = qs.filter(operation_origin=operation_origin)

    agent = (params.get("agent") or "").strip()
    if agent:
        if _looks_uuid(agent):
            qs = qs.filter(Q(agent_id=agent) | Q(agent__user_lan_id__iexact=agent))
        else:
            qs = qs.filter(agent__user_lan_id__iexact=agent)

    date_from = _parse_date(params.get("date_from"))
    date_to = _parse_date(params.get("date_to"))
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(protocol__icontains=search)
            | Q(workflow__icontains=search)
            | Q(client__icontains=search)
            | Q(subject__icontains=search)
            | Q(description__icontains=search)
            | Q(reference__icontains=search)
            | Q(agent__full_name__icontains=search)
            | Q(agent__user_lan_id__icontains=search)
        )
    return qs


def _looks_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def _serialize_detail(req, request) -> dict:
    req = (
        OperationalSupportRequest.objects.select_related(
            "agent",
            "requester",
            "leader_decider",
            "assignee",
            "presencial_support_user",
            "answered_by",
            "cancelled_by",
        )
        .prefetch_related("events__author")
        .get(pk=req.pk)
    )
    return OperationalSupportRequestSerializer(
        req,
        context={"request": request, "queue_positions": queue_positions_by_id()},
    ).data


class CategoriesView(APIView):
    permission_classes = [AnyViewPerm]

    def get(self, request):
        return Response({"results": categories_payload()})


class PresencialAgentsView(APIView):
    permission_classes = [AnyViewPerm]

    def get(self, request):
        return Response({"results": list_presencial_agents(viewer=request.user)})


class CapacitacaoSupportUsersView(APIView):
    """Usuários com acesso à página de suporte de capacitação (modal presencial)."""

    permission_classes = [RequestCreatePerm]

    def get(self, request):
        return Response({"results": users_with_permission(QUAL_CAPACITACAO_SUPORTE_VIEW)})


class MetadataView(APIView):
    permission_classes = [RequestCreatePerm]

    def get(self, request):
        seed_catalog_defaults()
        rows = list(
            ProjecaoSla.objects.filter(
                data_fim__isnull=True,
                workflow__ind_considerar=True,
            )
            .values(
                "workflow_id",
                "workflow__nome",
                "cliente_id",
                "cliente__nome",
            )
            .distinct()
            .order_by("workflow__nome", "workflow_id", "cliente__nome")
        )
        client_counts = Counter(row["workflow_id"] for row in rows)
        workflow_clients = []
        for row in rows:
            workflow_name = (row["workflow__nome"] or "").strip()
            client_name = (row["cliente__nome"] or "").strip()
            if not workflow_name or not client_name:
                continue
            label = f'{row["workflow_id"]} — {workflow_name}'
            if client_counts[row["workflow_id"]] > 1:
                label = f"{label} · {client_name}"
            workflow_clients.append(
                {
                    "key": f'{row["workflow_id"]}:{row["cliente_id"]}',
                    "workflow_id": row["workflow_id"],
                    "workflow_name": workflow_name,
                    "client_id": row["cliente_id"],
                    "client_name": client_name,
                    "label": label,
                }
            )
        return Response(
            {
                "questions": get_catalog_values(
                    AuditoriaCatalogItem.CATALOG_DUVIDA_SUPORTE_OPERACIONAL
                ),
                "workflow_clients": workflow_clients,
                "support_available": has_available_support_agent(),
                "online_support_agents": online_support_agent_count(),
            }
        )


class ActiveAgentsView(APIView):
    """Lista agentes ativos para seleção na abertura (líder: qualquer equipe)."""

    permission_classes = [ActiveAgentsPerm]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        qs = Agent.objects.filter(active=True).order_by("full_name")
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search) | Q(user_lan_id__icontains=search)
            )
        rows = [
            {
                "id": str(row.id),
                "user_lan_id": row.user_lan_id,
                "full_name": row.full_name,
            }
            for row in qs[:500]
        ]
        return Response({"results": rows})


class RequestListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [RequestCreatePerm()]
        return [AnyViewPerm()]

    def get(self, request):
        qs = scoped_queryset(request.user).order_by("-created_at")
        try:
            qs = _apply_filters(qs, request.query_params, user=request.user)
        except FilterValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response({"detail": "Parâmetros de data inválidos."}, status=status.HTTP_400_BAD_REQUEST)

        average_queue_duration = qs.filter(
            request_type=OperationalSupportRequest.RequestType.ONLINE,
            leader_decided_at__isnull=False,
            assigned_at__isnull=False,
            assigned_at__date=timezone.localdate(),
        ).aggregate(
            value=Avg(
                ExpressionWrapper(
                    F("assigned_at") - F("leader_decided_at"),
                    output_field=DurationField(),
                )
            )
        )["value"]
        queue_average_seconds = (
            max(0, round(average_queue_duration.total_seconds()))
            if average_queue_duration is not None
            else None
        )

        # Paginação simples alinhada ao DRF default
        try:
            page = max(int(request.query_params.get("page") or 1), 1)
        except ValueError:
            page = 1
        page_size = 50
        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs[start : start + page_size])
        data = OperationalSupportRequestListSerializer(
            rows,
            many=True,
            context={"request": request, "queue_positions": queue_positions_by_id()},
        ).data
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "queue_average_seconds": queue_average_seconds,
                "results": data,
            }
        )

    def post(self, request):
        request_type = (
            request.data.get("request_type")
            or OperationalSupportRequest.RequestType.ONLINE
        )
        if (
            str(request_type).strip().lower() == OperationalSupportRequest.RequestType.ONLINE
            and not has_available_support_agent()
        ):
            return Response(
                {
                    "detail": (
                        "Nenhum agente de suporte está disponível no momento. "
                        "Tente novamente quando houver um agente Online."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        serializer = OperationalSupportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            req = create_request(
                user=request.user,
                agent=data["agent"],
                subject=data["subject"],
                category=data["category"],
                description=data["description"],
                reference=data.get("reference") or "",
                protocol=data["protocol"],
                workflow=data["workflow"],
                client=data["client"],
                workflow_id=data.get("workflow_id"),
                cliente_id=data.get("cliente_id"),
                operation_origin=data["operation_origin"],
                request_type=data["request_type"],
                presencial_support_user=data.get("presencial_support_user"),
            )
        except WorkflowError as exc:
            return _workflow_error_response(exc)
        return Response(_serialize_detail(req, request), status=status.HTTP_201_CREATED)


class RequestDetailView(APIView):
    permission_classes = [AnyViewPerm]

    def get(self, request, request_id):
        qs = scoped_queryset(request.user)
        req = get_object_or_404(qs, pk=request_id)
        return Response(_serialize_detail(req, request))


class LeaderDecisionView(APIView):
    permission_classes = [ApprovePerm]

    def post(self, request, request_id):
        serializer = LeaderDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Garante que o registro está no escopo do usuário (líder = amplo).
        get_object_or_404(scoped_queryset(request.user), pk=request_id)
        try:
            req = leader_decision(
                user=request.user,
                request_id=request_id,
                approved=data["approved"],
                justification=data.get("justification") or "",
            )
        except OperationalSupportRequest.DoesNotExist:
            return Response({"detail": "Solicitação não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        except WorkflowError as exc:
            return _workflow_error_response(exc)
        return Response(_serialize_detail(req, request))


class CancelView(APIView):
    permission_classes = [CancelPerm]

    def post(self, request, request_id):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        get_object_or_404(scoped_queryset(request.user), pk=request_id)
        try:
            req = cancel_request(
                user=request.user,
                request_id=request_id,
                reason=serializer.validated_data["reason"],
            )
        except OperationalSupportRequest.DoesNotExist:
            return Response({"detail": "Solicitação não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        except WorkflowError as exc:
            return _workflow_error_response(exc)
        return Response(_serialize_detail(req, request))


class AssignView(APIView):
    permission_classes = [AssignPerm]

    def post(self, request, request_id):
        # Capacitação: escopo exclui pending_leader; se só view cap, ok.
        qs = scoped_queryset(request.user)
        get_object_or_404(qs, pk=request_id)
        try:
            req = assign_request(user=request.user, request_id=request_id)
        except OperationalSupportRequest.DoesNotExist:
            return Response({"detail": "Solicitação não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        except WorkflowError as exc:
            return _workflow_error_response(exc)
        return Response(_serialize_detail(req, request))


class AnswerView(APIView):
    permission_classes = [AnswerPerm]

    def post(self, request, request_id):
        serializer = AnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        get_object_or_404(scoped_queryset(request.user), pk=request_id)
        try:
            req = answer_request(
                user=request.user,
                request_id=request_id,
                answer=serializer.validated_data["answer"],
                answer_option=serializer.validated_data["answer_option"],
                difficulty_level=serializer.validated_data["difficulty_level"],
                document_uf=serializer.validated_data["document_uf"],
                document_type=serializer.validated_data["document_type"],
            )
        except OperationalSupportRequest.DoesNotExist:
            return Response({"detail": "Solicitação não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        except WorkflowError as exc:
            return _workflow_error_response(exc)
        return Response(_serialize_detail(req, request))
