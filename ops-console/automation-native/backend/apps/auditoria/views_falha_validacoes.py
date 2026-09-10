from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.constants import (
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_AUDITORIA_FRAUD,
)
from apps.access.permission_classes import portal_perm
from apps.access.registry import (
    QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
    QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
    QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
)
from apps.access.resolve import user_has_permission
from apps.access.services.agents_with_permission import users_with_role
from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.auditoria.services.falha_alteracoes import (
    analysis_definitions,
    field_definitions,
    profile_definition,
    serialize_alteracao,
    snapshot_falha,
)
from apps.auditoria.services.falha_validacoes import (
    FalhaValidacaoConflict,
    decidir_conforme,
    decidir_nao_conforme,
    serialize_validacao,
)
from apps.auditoria.services.serialization import serialize_falha_cadastro
from apps.workforce.models import Agent, UserProfile


ViewPerm = portal_perm(QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW)
DecidePerm = portal_perm(QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE)
ChangePerm = portal_perm(QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
ORDERING_FIELDS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "protocolo": "protocolo",
}


def _eligible_queryset():
    return (
        AuditoriaFalhaCadastro.objects.filter(
            Q(status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO)
            | Q(validacoes_capacitacao__isnull=False)
        )
        .select_related("agente_ref", "auditor_ref", "analise_origem", "created_by")
        .prefetch_related("validacoes_capacitacao__ator")
        .distinct()
    )


def _serialize_row(item: AuditoriaFalhaCadastro) -> dict:
    row = serialize_falha_cadastro(item)
    row["status_falha"] = item.status_falha
    row["status_falha_label"] = item.get_status_falha_display()
    history = list(item.validacoes_capacitacao.all())
    row["ultima_validacao"] = serialize_validacao(history[0]) if history else None
    return row


def _validation_errors(exc: ValidationError):
    return getattr(exc, "message_dict", None) or getattr(exc, "messages", None)


def _filter_options(queryset) -> dict:
    statuses = dict(AuditoriaFalhaCadastro.STATUS_FALHA_CHOICES)
    results = dict(AuditoriaFalhaCadastro.RESULTADO_QUALIDADE_CHOICES)
    origins = dict(AuditoriaFalhaCadastro.ORIGEM_CHOICES)
    types = dict(AuditoriaFalhaCadastro.REGISTRO_CHOICES)
    return {
        "status": [
            {"value": value, "label": statuses.get(value, value)}
            for value in queryset.order_by().values_list("status_falha", flat=True).distinct()
        ],
        "resultado": [
            {"value": value, "label": results.get(value, value)}
            for value in queryset.order_by().values_list("resultado_qualidade", flat=True).distinct()
        ],
        "origem": [
            {"value": value, "label": origins.get(value, value or "Sem origem")}
            for value in queryset.order_by().values_list("origem", flat=True).distinct()
        ],
        "tipo_registro": [
            {"value": value, "label": types.get(value, value)}
            for value in queryset.order_by().values_list("tipo_registro", flat=True).distinct()
        ],
    }


class AuditoriaFalhaValidacaoListView(APIView):
    permission_classes = [IsAuthenticated, ViewPerm]

    def get(self, request):
        base_queryset = _eligible_queryset()
        filter_options = _filter_options(base_queryset)
        queryset = base_queryset

        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(protocolo__icontains=search)
                | Q(usuario__icontains=search)
                | Q(tipo_falha__icontains=search)
                | Q(motivo_falha__icontains=search)
                | Q(auditor__icontains=search)
                | Q(agente_ref__full_name__icontains=search)
                | Q(auditor_ref__full_name__icontains=search)
            )
        for param in ("status", "origem", "tipo_registro"):
            value = str(request.query_params.get(param) or "").strip()
            if value:
                field = "status_falha" if param == "status" else param
                queryset = queryset.filter(**{field: value})
        result = str(request.query_params.get("resultado") or "").strip()
        if result:
            queryset = queryset.filter(resultado_qualidade=result)

        ordering = ORDERING_FIELDS.get(
            str(request.query_params.get("ordering") or "created_at"), "created_at"
        )
        direction = str(request.query_params.get("direction") or "desc").lower()
        prefix = "" if direction == "asc" else "-"
        queryset = queryset.order_by(f"{prefix}{ordering}", f"{prefix}id")
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = int(request.query_params.get("page_size") or DEFAULT_PAGE_SIZE)
        except (TypeError, ValueError):
            return Response(
                {"errors": {"pagination": "page e page_size devem ser inteiros."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        page_size = min(MAX_PAGE_SIZE, max(1, page_size))
        count = queryset.count()
        total_pages = max(1, (count + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        rows = list(queryset[offset : offset + page_size])
        return Response(
            {
                "results": [_serialize_row(item) for item in rows],
                "count": count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "filter_options": filter_options,
            }
        )


class AuditoriaFalhaValidacaoDetailView(APIView):
    permission_classes = [IsAuthenticated, ViewPerm]

    def get(self, request, pk: int):
        falha = _eligible_queryset().filter(pk=pk).first()
        if falha is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        changes = list(falha.alteracoes.select_related("alterado_por").order_by("-versao"))
        history = list(
            falha.validacoes_capacitacao.select_related(
                "ator", "alteracao", "alteracao__alterado_por"
            ).order_by("-created_at")
        )
        agent_ids = {
            value
            for item in changes
            for snapshot in (item.dados_anteriores, item.dados_alterados)
            for key, value in snapshot.items()
            if key in {"agente_id", "auditor_id"} and value
        }
        agent_ids.update(
            str(value) for value in (falha.agente_ref_id, falha.auditor_ref_id) if value
        )
        agents = Agent.objects.filter(id__in=agent_ids).order_by("full_name")
        serialized_falha = serialize_falha_cadastro(falha)
        serialized_falha["status_falha"] = falha.status_falha
        serialized_falha["status_falha_label"] = falha.get_status_falha_display()
        can_decide = user_has_permission(
            request.user, QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE
        )
        can_change = user_has_permission(
            request.user, QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE
        )
        return Response(
            {
                "falha": serialized_falha,
                "current": snapshot_falha(falha),
                "profile": profile_definition(falha),
                "fields": field_definitions(falha),
                "analises": analysis_definitions(falha),
                "alteracoes": [serialize_alteracao(item) for item in changes],
                "agents": [
                    {
                        "id": str(agent.id),
                        "user_lan_id": agent.user_lan_id,
                        "full_name": agent.full_name,
                        "active": agent.active,
                        "is_auditor": agent.id == falha.auditor_ref_id,
                    }
                    for agent in agents
                ],
                "historico": [serialize_validacao(item) for item in history],
                "can_decide": can_decide,
                "can_change": can_change,
            }
        )


class AuditoriaFalhaValidacaoAgentOptionsView(APIView):
    permission_classes = [IsAuthenticated, ChangePerm]

    def get(self, request):
        search = str(request.query_params.get("search") or "").strip()
        queryset = Agent.objects.order_by("full_name", "user_lan_id")
        if search:
            queryset = queryset.filter(
                Q(full_name__icontains=search)
                | Q(user_lan_id__icontains=search)
                | Q(email__icontains=search)
            )
        user_ids = {
            int(item["id"])
            for role in (ROLE_QUAL_AUDITORIA_FRAUD, ROLE_QUAL_AUDITORIA_COMPLIANCE)
            for item in users_with_role(role)
        }
        auditor_ids = set(
            UserProfile.objects.filter(user_id__in=user_ids).values_list(
                "agent_id", flat=True
            )
        )
        from apps.auditoria.services.agent_links import is_system_agent

        return Response(
            {
                "results": [
                    {
                        "id": str(agent.id),
                        "user_lan_id": agent.user_lan_id,
                        "full_name": agent.full_name,
                        "active": agent.active,
                        "is_system": is_system_agent(agent),
                        "is_auditor": agent.id in auditor_ids,
                    }
                    for agent in queryset
                ]
            }
        )


class _DecisionView(APIView):
    result_method = None

    def post(self, request, pk: int):
        if not _eligible_queryset().filter(pk=pk).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        payload = request.data if isinstance(request.data, dict) else {}
        kwargs = {
            "falha_id": pk,
            "user": request.user,
            "observacao": payload.get("observacao"),
            "expected_updated_at": payload.get("expected_updated_at"),
            "idempotency_key": payload.get("idempotency_key"),
        }
        if self.result_method is decidir_nao_conforme:
            kwargs["dados"] = payload.get("dados")
        try:
            falha, validation, change, created = self.result_method(**kwargs)
        except FalhaValidacaoConflict as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_409_CONFLICT)
        except ValidationError as exc:
            return Response(
                {"errors": _validation_errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serialized_falha = serialize_falha_cadastro(falha)
        serialized_falha["status_falha"] = falha.status_falha
        serialized_falha["status_falha_label"] = falha.get_status_falha_display()
        return Response(
            {
                "falha": serialized_falha,
                "validacao": serialize_validacao(validation),
                "alteracao": serialize_alteracao(change) if change else None,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AuditoriaFalhaValidacaoConformeView(_DecisionView):
    permission_classes = [IsAuthenticated, DecidePerm]
    result_method = staticmethod(decidir_conforme)


class AuditoriaFalhaValidacaoNaoConformeView(_DecisionView):
    permission_classes = [IsAuthenticated, DecidePerm, ChangePerm]
    result_method = staticmethod(decidir_nao_conforme)
