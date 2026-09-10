from __future__ import annotations

import json
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db import transaction
from django.db.models import CharField, Q, Value
from django.db.models.functions import Coalesce, Concat, NullIf, Trim
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.constants import (
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_AUDITORIA_FRAUD,
)
from apps.access.permissions import HasPortalPermission
from apps.access.registry import (
    ADM_CATALOGOS,
    QUAL_AUDITORIA_COMPLIANCE_VIEW,
    QUAL_AUDITORIA_CREATE,
    QUAL_AUDITORIA_RESULT_CHANGE,
    QUAL_AUDITORIA_VIEW,
)
from apps.access.resolve import user_has_any_permission, user_has_permission
from apps.access.services.agents_with_permission import users_with_role
from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaCatalogItem,
    AuditoriaFalhaAlteracao,
    AuditoriaFalhaCadastro,
    AuditoriaMotivoFalha,
    QualidadeConfiguracaoAlteracao,
    ReinspecaoAuditorPresence,
)
from apps.auditoria.services.catalog_items import (
    VALID_CATALOGS,
    normalize_catalog_value,
    seed_catalog_defaults,
)
from apps.auditoria.services.analise_origem import get_or_create_analise_origem
from apps.auditoria.services.agent_links import resolve_agent_for_user, resolve_agent_reference
from apps.auditoria.services.catalogs import build_auditoria_catalogs
from apps.auditoria.services.config_change_log import (
    ConfigChangeUndoError,
    build_changes,
    log_config_change,
    snapshot_fields,
    undo_config_change,
)
from apps.auditoria.services.config_bulk import (
    ConfigBulkError,
    bulk_update_catalog,
    bulk_update_scenarios,
    import_catalog_xlsx,
    import_scenarios_xlsx,
    preview_catalog_xlsx,
    preview_scenarios_xlsx,
)
from apps.auditoria.services.falha_alteracoes import (
    AUDITORIA_COMPLIANCE_CONTEXT,
    alterar_falha,
    analysis_definitions,
    field_definitions,
    profile_definition,
    resolve_analysis_context,
    serialize_alteracao,
    snapshot_falha,
)
from apps.auditoria.services.serialization import (
    serialize_catalog_item,
    serialize_falha_cadastro,
    serialize_motivo_falha,
)
from apps.auditoria.services.motivos_falha import seed_motivos_falha_defaults
from apps.auditoria.services.qualidade_promocao import (
    auditoria_fraud_tratados_qs,
    status_inicial_auditoria_fraud,
)
from apps.auditoria.services.validation import normalize_tipo_registro, validate_falha_payload
from apps.workforce.models import Agent, UserProfile

CATALOG_META = {
    AuditoriaCatalogItem.CATALOG_MODULO: {
        "title": "Módulos",
        "description": "Opções do campo Módulo no cadastro de falhas.",
        "slug": "modulos",
    },
    AuditoriaCatalogItem.CATALOG_TIPO_FALHA: {
        "title": "Tipos de falha",
        "description": "Opções do campo Tipo de falha.",
        "slug": "tipos-falha",
    },
    AuditoriaCatalogItem.CATALOG_NOVO_RESULTADO: {
        "title": "Novos resultados",
        "description": "Opções do campo Novo resultado.",
        "slug": "novos-resultados",
    },
    AuditoriaCatalogItem.CATALOG_SINALIZACAO: {
        "title": "Sinalização",
        "description": "Opções do campo Sinalização.",
        "slug": "sinalizacao",
    },
    AuditoriaCatalogItem.CATALOG_ETAPA_FALHA: {
        "title": "Etapas da falha",
        "description": "Opções do campo Etapa da falha.",
        "slug": "etapas-falha",
    },
    AuditoriaCatalogItem.CATALOG_NIVEL_DIFICULDADE: {
        "title": "Níveis de dificuldade",
        "description": "Opções do campo Nível de dificuldade.",
        "slug": "niveis-dificuldade",
    },
    AuditoriaCatalogItem.CATALOG_TIPO_DOCUMENTO: {
        "title": "Tipos de documento",
        "description": "Opções do campo Tipo de documento.",
        "slug": "tipos-documento",
    },
    AuditoriaCatalogItem.CATALOG_UF_DOCUMENTO: {
        "title": "UF do documento",
        "description": "Opções do campo UF do documento.",
        "slug": "ufs-documento",
    },
    AuditoriaCatalogItem.CATALOG_CRUZAMENTO_BASES: {
        "title": "Cruzamento de bases",
        "description": "Opções do campo Cruzamento de bases na análise de protocolo.",
        "slug": "cruzamento-bases",
    },
    AuditoriaCatalogItem.CATALOG_QUALIDADE_IMAGEM: {
        "title": "Qualidade da imagem",
        "description": "Opções do campo Qualidade da imagem na análise de protocolo.",
        "slug": "qualidade-imagem",
    },
    AuditoriaCatalogItem.CATALOG_TIPO_ACAO_CONTROLE: {
        "title": "Tipo de ação",
        "description": "Opções do campo Tipo de ação nos controles de remoção.",
        "slug": "tipo-acao",
    },
    AuditoriaCatalogItem.CATALOG_MOTIVO_BASE_NEGATIVA: {
        "title": "Motivo (remoção de bases)",
        "description": "Opções do campo Motivo nas remoções de base negativa e positiva.",
        "slug": "motivo-base-negativa",
    },
    AuditoriaCatalogItem.CATALOG_IRREGULARIDADES_CONFER: {
        "title": "Irregularidades Confer",
        "description": "Opções do campo Irregularidade na fila de reinspeção (inserção manual).",
        "slug": "irregularidades-confer",
    },
    AuditoriaCatalogItem.CATALOG_DUVIDA_SUPORTE_OPERACIONAL: {
        "title": "Dúvidas do suporte operacional",
        "description": "Opções do campo Dúvida no cadastro de suporte operacional.",
        "slug": "duvidas-suporte-operacional",
    },
}

SLUG_TO_CATALOG = {meta["slug"]: key for key, meta in CATALOG_META.items()}


def _resolve_catalog(catalog: str) -> str | None:
    if catalog in VALID_CATALOGS:
        return catalog
    return SLUG_TO_CATALOG.get(catalog)


def _catalog_not_found():
    return Response({"detail": "Catálogo não encontrado."}, status=status.HTTP_404_NOT_FOUND)


class AuditoriaCatalogsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not user_has_any_permission(
            request.user,
            QUAL_AUDITORIA_VIEW,
            QUAL_AUDITORIA_COMPLIANCE_VIEW,
        ):
            return Response({"detail": "Sem permissao para consultar os catalogos."}, status=403)
        seed_catalog_defaults()
        seed_motivos_falha_defaults()
        return Response(build_auditoria_catalogs())


class AuditoriaFalhaCreateView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        errors = validate_falha_payload(payload)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        brflow_parsed = payload.get("brflow_parsed")
        if not isinstance(brflow_parsed, dict):
            brflow_parsed = {}

        tipo_registro = normalize_tipo_registro(payload.get("tipo_registro", ""))
        protocolo = (payload.get("protocolo") or "").strip()
        brflow_raw = (payload.get("brflow_raw") or "").strip()
        analise_origem = get_or_create_analise_origem(
            protocolo=protocolo,
            brflow_raw=brflow_raw,
            brflow_parsed=brflow_parsed,
        )
        now = timezone.now()
        usuario = (payload.get("usuario") or "").strip()
        tipo_falha = (payload.get("tipo_falha") or "").strip()
        fila_contexto = str(brflow_parsed.get("fila_contexto") or "").strip().casefold()
        is_auditoria_fraud = (
            tipo_registro == AuditoriaFalhaCadastro.REGISTRO_AUDITORIA
            and fila_contexto
            != ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE.casefold()
        )
        record = AuditoriaFalhaCadastro.objects.create(
            analise_origem=analise_origem,
            protocolo=protocolo,
            brflow_raw=brflow_raw,
            brflow_parsed=brflow_parsed,
            modulo=(payload.get("modulo") or "").strip(),
            demanda_url=(payload.get("demanda_url") or "").strip(),
            tipo_falha=tipo_falha,
            usuario=usuario,
            agente_ref=resolve_agent_reference(usuario).agent,
            resultado_cliente=(payload.get("resultado_cliente") or "").strip(),
            novo_resultado=(payload.get("novo_resultado") or "").strip(),
            sinalizacao=(payload.get("sinalizacao") or "").strip(),
            motivo_falha=(payload.get("motivo_falha") or "").strip(),
            etapa_falha=(payload.get("etapa_falha") or "").strip(),
            nivel_dificuldade=(payload.get("nivel_dificuldade") or "").strip(),
            tipo_documento=(payload.get("tipo_documento") or "").strip(),
            uf_documento=(payload.get("uf_documento") or "").strip(),
            qualidade_imagem=(payload.get("qualidade_imagem") or "").strip(),
            observacao=(payload.get("observacao") or "").strip(),
            tipo_registro=tipo_registro,
            origem=tipo_registro,
            status_falha=(
                status_inicial_auditoria_fraud(tipo_falha)
                if is_auditoria_fraud
                else AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
            ),
            analise_concluida_em=now,
            data_resposta=now,
            auditor=(request.user.username or "").strip(),
            auditor_ref=resolve_agent_for_user(request.user),
            created_by=request.user,
            auditor_responsavel=request.user,
        )
        return Response(serialize_falha_cadastro(record), status=status.HTTP_201_CREATED)


REALIZADOS_PAGE_SIZE = 50
REALIZADOS_EMPTY_VALUE = "(vazio)"
REALIZADOS_NO_ACTIVITY_VALUE = "(sem atividade)"
REALIZADOS_FILTER_OPTIONS_CACHE_KEY = "auditoria:fraud:realizados:filter-options:v1"
REALIZADOS_FILTER_OPTIONS_CACHE_SECONDS = 60


def _realizados_auditor_expression():
    """Nome exibido do auditor, com fallback para o autor legado do registro."""
    return Coalesce(
        NullIf(Trim("auditor_ref__full_name"), Value("")),
        NullIf(Trim("created_by__profile__agent__full_name"), Value("")),
        NullIf(
            Trim(
                Concat(
                    "created_by__first_name",
                    Value(" "),
                    "created_by__last_name",
                )
            ),
            Value(""),
        ),
        NullIf(Trim("created_by__username"), Value("")),
        Value(REALIZADOS_EMPTY_VALUE),
        output_field=CharField(),
    )


def _realizados_filter_values(request, key: str) -> list[str] | None:
    raw = request.query_params.get(f"filter_{key}")
    if raw is None:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(values, list):
        return None
    return [str(value).strip() for value in values]


def _realizados_text_options(queryset, field: str, *, empty_label: str = REALIZADOS_EMPTY_VALUE) -> list[str]:
    values = queryset.order_by().values_list(field, flat=True).distinct()
    normalized = {str(value).strip() if value not in (None, "") else empty_label for value in values}
    return sorted(normalized, key=lambda value: value.casefold())


def _filter_realizados_text(queryset, request, key: str, field: str, *, empty_label: str = REALIZADOS_EMPTY_VALUE):
    values = _realizados_filter_values(request, key)
    if values is None:
        return queryset
    non_empty = [value for value in values if value != empty_label]
    condition = Q(**{f"{field}__in": non_empty}) if non_empty else Q(pk__in=[])
    if empty_label in values:
        condition |= Q(**{f"{field}__isnull": True}) | Q(**{field: ""})
    return queryset.filter(condition)


class AuditoriaFalhaRealizadasView(APIView):
    """Consulta paginada dos trabalhos concluÃ­dos na tabela central de tratados."""

    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        source = auditoria_fraud_tratados_qs().select_related(
            "atividade",
            "analise_origem",
            "agente_ref",
            "auditor_ref",
            "created_by",
            "created_by__profile__agent",
            "responsavel",
        ).annotate(realizado_por=_realizados_auditor_expression())

        filter_options = cache.get_or_set(
            REALIZADOS_FILTER_OPTIONS_CACHE_KEY,
            lambda: {
            "atividade": _realizados_text_options(
                source,
                "atividade__nome",
                empty_label=REALIZADOS_NO_ACTIVITY_VALUE,
            ),
            "protocolo": _realizados_text_options(source, "protocolo"),
            "usuario": _realizados_text_options(source, "usuario"),
            "modulo": _realizados_text_options(source, "modulo"),
            "tipo_falha": _realizados_text_options(source, "tipo_falha"),
            "resultado_qualidade": sorted(
                {
                    dict(AuditoriaFalhaCadastro.RESULTADO_QUALIDADE_CHOICES).get(value, "NÃ£o classificado")
                    for value in source.order_by().values_list("resultado_qualidade", flat=True).distinct()
                },
                key=lambda value: value.casefold(),
            ),
            "created_by": _realizados_text_options(source, "realizado_por"),
            "data_realizacao": sorted(
                {
                    timezone.localtime(value).strftime("%d/%m/%Y")
                    for value in source.order_by().values_list("created_at", flat=True).distinct()
                    if value is not None
                },
                reverse=True,
            ),
            },
            timeout=REALIZADOS_FILTER_OPTIONS_CACHE_SECONDS,
        )

        queryset = source
        protocolo_q = (request.query_params.get("protocolo") or "").strip()
        if protocolo_q:
            queryset = queryset.filter(protocolo__icontains=protocolo_q)

        queryset = _filter_realizados_text(
            queryset,
            request,
            "atividade",
            "atividade__nome",
            empty_label=REALIZADOS_NO_ACTIVITY_VALUE,
        )
        for key, field in (
            ("protocolo", "protocolo"),
            ("usuario", "usuario"),
            ("modulo", "modulo"),
            ("tipo_falha", "tipo_falha"),
            ("created_by", "realizado_por"),
        ):
            queryset = _filter_realizados_text(queryset, request, key, field)

        resultado_values = _realizados_filter_values(request, "resultado_qualidade")
        if resultado_values is not None:
            label_to_value = {
                label: value for value, label in AuditoriaFalhaCadastro.RESULTADO_QUALIDADE_CHOICES
            }
            queryset = queryset.filter(
                resultado_qualidade__in=[
                    label_to_value[label] for label in resultado_values if label in label_to_value
                ]
            )

        data_values = _realizados_filter_values(request, "data_realizacao")
        if data_values is not None:
            dates = []
            for value in data_values:
                try:
                    dates.append(datetime.strptime(value, "%d/%m/%Y").date())
                except ValueError:
                    continue
            queryset = queryset.filter(created_at__date__in=dates)

        sort_key = (request.query_params.get("sort") or "data_realizacao").strip()
        sort_field = {
            "atividade": "atividade__nome",
            "protocolo": "protocolo",
            "usuario": "usuario",
            "modulo": "modulo",
            "tipo_falha": "tipo_falha",
            "resultado_qualidade": "resultado_qualidade",
            "created_by": "realizado_por",
            "data_realizacao": "created_at",
        }.get(sort_key, "created_at")
        sort_prefix = "" if request.query_params.get("direction") == "asc" else "-"
        queryset = queryset.order_by(f"{sort_prefix}{sort_field}", f"{sort_prefix}id")

        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        total = queryset.count()
        total_pages = max(1, (total + REALIZADOS_PAGE_SIZE - 1) // REALIZADOS_PAGE_SIZE)
        page = min(page, total_pages)
        offset = (page - 1) * REALIZADOS_PAGE_SIZE
        rows = list(queryset[offset : offset + REALIZADOS_PAGE_SIZE])

        results = []
        for item in rows:
            serialized = serialize_falha_cadastro(item)
            serialized["created_by"] = item.realizado_por
            serialized["atividade_nome"] = (
                item.atividade.nome if item.atividade_id else REALIZADOS_NO_ACTIVITY_VALUE
            )
            serialized["can_change"] = user_has_permission(
                request.user, QUAL_AUDITORIA_RESULT_CHANGE
            )
            results.append(serialized)

        return Response(
            {
                "results": results,
                "total": total,
                "page": page,
                "page_size": REALIZADOS_PAGE_SIZE,
                "total_pages": total_pages,
                "filter_options": filter_options,
            }
        )


class AuditoriaFalhaAlteracoesView(APIView):
    permission_classes = [IsAuthenticated]

    def _falha(self, pk: int):
        return (
            AuditoriaFalhaCadastro.objects.select_related(
                "agente_ref", "auditor_ref", "analise_origem"
            )
            .filter(pk=pk)
            .first()
        )

    @staticmethod
    def _can_view(user, falha: AuditoriaFalhaCadastro) -> bool:
        permission = (
            QUAL_AUDITORIA_COMPLIANCE_VIEW
            if resolve_analysis_context(falha) == AUDITORIA_COMPLIANCE_CONTEXT
            else QUAL_AUDITORIA_VIEW
        )
        return user_has_permission(user, permission)

    def get(self, request, pk: int):
        falha = self._falha(pk)
        if falha is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not self._can_view(request.user, falha):
            return Response({"detail": "Sem permissao para consultar a falha."}, status=403)
        changes = list(
            falha.alteracoes.select_related("alterado_por").order_by("-versao")
        )
        agent_ids = {
            value
            for item in changes
            for snapshot in (item.dados_anteriores, item.dados_alterados)
            for key, value in snapshot.items()
            if key in {"agente_id", "auditor_id"} and value
        }
        agent_ids.update(
            str(value)
            for value in (falha.agente_ref_id, falha.auditor_ref_id)
            if value
        )
        auditor_ids = {
            value
            for item in changes
            for snapshot in (item.dados_anteriores, item.dados_alterados)
            for key, value in snapshot.items()
            if key == "auditor_id" and value
        }
        if falha.auditor_ref_id:
            auditor_ids.add(str(falha.auditor_ref_id))
        agents = Agent.objects.filter(id__in=agent_ids).order_by("full_name")
        return Response(
            {
                "falha": serialize_falha_cadastro(falha),
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
                        "is_auditor": str(agent.id) in auditor_ids,
                    }
                    for agent in agents
                ],
                "can_change": user_has_permission(
                    request.user, QUAL_AUDITORIA_RESULT_CHANGE
                ),
            }
        )

    def post(self, request, pk: int):
        if not user_has_permission(request.user, QUAL_AUDITORIA_RESULT_CHANGE):
            return Response({"detail": "Sem permissao para alterar a falha."}, status=403)
        falha_atual = self._falha(pk)
        if falha_atual is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not self._can_view(request.user, falha_atual):
            return Response({"detail": "Sem permissao para alterar a falha."}, status=403)
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            falha, change, created = alterar_falha(
                falha_id=pk,
                user=request.user,
                dados=payload.get("dados"),
                justificativa=payload.get("justificativa"),
                expected_updated_at=payload.get("expected_updated_at"),
                idempotency_key=payload.get("idempotency_key"),
            )
        except ValidationError as exc:
            detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", None)
            return Response({"errors": detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "falha": serialize_falha_cadastro(falha),
                "alteracao": serialize_alteracao(change),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AuditoriaFalhaAgentOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _auditor_agent_ids() -> set:
        user_ids = {
            int(item["id"])
            for role in (
                ROLE_QUAL_AUDITORIA_FRAUD,
                ROLE_QUAL_AUDITORIA_COMPLIANCE,
            )
            for item in users_with_role(role)
        }
        if not user_ids:
            return set()
        return set(
            UserProfile.objects.filter(user_id__in=user_ids).values_list(
                "agent_id", flat=True
            )
        )

    def get(self, request):
        if not user_has_permission(request.user, QUAL_AUDITORIA_RESULT_CHANGE):
            return Response({"detail": "Sem permissao para alterar a falha."}, status=403)
        from apps.auditoria.services.agent_links import get_or_create_system_agent

        get_or_create_system_agent()
        search = str(request.query_params.get("search") or "").strip()
        queryset = Agent.objects.order_by("full_name", "user_lan_id")
        if search:
            queryset = queryset.filter(
                Q(full_name__icontains=search)
                | Q(user_lan_id__icontains=search)
                | Q(email__icontains=search)
            )
        auditor_agent_ids = self._auditor_agent_ids()
        results = []
        from apps.auditoria.services.agent_links import is_system_agent

        for agent in queryset:
            results.append(
                {
                    "id": str(agent.id),
                    "user_lan_id": agent.user_lan_id,
                    "full_name": agent.full_name,
                    "active": agent.active,
                    "is_system": is_system_agent(agent),
                    "is_auditor": agent.id in auditor_agent_ids,
                }
            )
        return Response({"results": results})


class AuditoriaCatalogConfigMetaView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def get(self, request):
        seed_catalog_defaults()
        seed_motivos_falha_defaults()
        results = []
        for catalog, meta in CATALOG_META.items():
            count = AuditoriaCatalogItem.objects.filter(catalog=catalog).count()
            results.append(
                {
                    "catalog": catalog,
                    "slug": meta["slug"],
                    "title": meta["title"],
                    "description": meta["description"],
                    "count": count,
                }
            )
        results.append(
            {
                "catalog": "cenarios",
                "slug": "cenarios",
                "title": "Cenários",
                "description": "Opções do campo Cenário com criticidade, segmento e subsegmento.",
                "count": AuditoriaMotivoFalha.objects.count(),
            }
        )
        return Response({"results": results})


def _serialize_config_change(item: QualidadeConfiguracaoAlteracao) -> dict:
    user = item.usuario
    user_name = ""
    if user is not None:
        user_name = (user.get_full_name() or "").strip() or user.username
    return {
        "id": item.pk,
        "tabela_origem": item.tabela_origem,
        "registro_id": item.registro_id,
        "mudancas": item.mudancas,
        "usuario": user_name,
        "criado_em": item.criado_em.isoformat(),
        "reverte_id": item.reverte_id,
        "desfeita": bool(item.reversoes.all()),
        "pode_desfazer": item.reverte_id is None and not bool(item.reversoes.all()),
    }


class QualidadeConfiguracaoAlteracoesView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def get(self, request):
        queryset = (
            QualidadeConfiguracaoAlteracao.objects.select_related("usuario")
            .prefetch_related("reversoes")
            .order_by("-criado_em", "-id")[:200]
        )
        return Response({"results": [_serialize_config_change(item) for item in queryset]})


class QualidadeConfiguracaoAlteracaoUndoView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request, pk: int):
        try:
            reverse_log = undo_config_change(change_id=pk, user=request.user)
        except ConfigChangeUndoError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"reversao_id": reverse_log.pk})


class AuditoriaCatalogConfigListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def get(self, request, catalog: str):
        catalog_key = _resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()
        seed_catalog_defaults()
        qs = AuditoriaCatalogItem.objects.filter(catalog=catalog_key).order_by("sort_order", "value")
        meta = CATALOG_META.get(catalog_key, {})
        return Response(
            {
                "catalog": catalog_key,
                "title": meta.get("title", catalog_key),
                "description": meta.get("description", ""),
                "results": [serialize_catalog_item(item) for item in qs],
            }
        )

    @transaction.atomic
    def post(self, request, catalog: str):
        catalog_key = _resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()

        payload = request.data if isinstance(request.data, dict) else {}
        value = normalize_catalog_value(catalog_key, (payload.get("value") or "").strip())
        if not value:
            return Response({"errors": {"value": "Informe o valor."}}, status=status.HTTP_400_BAD_REQUEST)

        if AuditoriaCatalogItem.objects.filter(catalog=catalog_key, value=value).exists():
            return Response(
                {"errors": {"value": "Este valor já existe no catálogo."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label = (payload.get("label") or value).strip()
        sort_order = payload.get("sort_order")
        if sort_order is None:
            last = (
                AuditoriaCatalogItem.objects.filter(catalog=catalog_key)
                .order_by("-sort_order")
                .values_list("sort_order", flat=True)
                .first()
            )
            sort_order = (last or 0) + 1

        item = AuditoriaCatalogItem.objects.create(
            catalog=catalog_key,
            value=value,
            label=label,
            sort_order=int(sort_order),
            active=bool(payload.get("active", True)),
        )
        fields = ("catalog", "value", "label", "sort_order", "active")
        after = snapshot_fields(item, fields)
        log_config_change(
            instance=item,
            changes=build_changes({field: None for field in fields}, after),
            user=request.user,
        )
        return Response(serialize_catalog_item(item), status=status.HTTP_201_CREATED)


class AuditoriaCatalogConfigDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    @transaction.atomic
    def patch(self, request, catalog: str, pk: int):
        catalog_key = _resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()

        item = AuditoriaCatalogItem.objects.filter(pk=pk, catalog=catalog_key).first()
        if not item:
            return Response(status=status.HTTP_404_NOT_FOUND)

        fields = ("catalog", "value", "label", "sort_order", "active")
        before = snapshot_fields(item, fields)
        payload = request.data if isinstance(request.data, dict) else {}
        errors: dict[str, str] = {}

        if "value" in payload:
            value = normalize_catalog_value(catalog_key, (payload.get("value") or "").strip())
            if not value:
                errors["value"] = "Informe o valor."
            elif (
                AuditoriaCatalogItem.objects.filter(catalog=catalog_key, value=value)
                .exclude(pk=item.pk)
                .exists()
            ):
                errors["value"] = "Este valor já existe no catálogo."
            else:
                item.value = value

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        if "label" in payload:
            item.label = (payload.get("label") or item.value).strip()
        if "sort_order" in payload:
            item.sort_order = int(payload.get("sort_order") or 0)
        if "active" in payload:
            item.active = bool(payload.get("active"))

        item.save()
        log_config_change(
            instance=item,
            changes=build_changes(before, snapshot_fields(item, fields)),
            user=request.user,
        )
        return Response(serialize_catalog_item(item))


class AuditoriaMotivoFalhaConfigListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def get(self, request):
        seed_motivos_falha_defaults()
        qs = AuditoriaMotivoFalha.objects.order_by("sort_order", "motivo")
        return Response(
            {
                "title": "Cenários",
                "description": "Opções do campo Cenário com criticidade, segmento e subsegmento.",
                "results": [serialize_motivo_falha(item) for item in qs],
            }
        )

    @transaction.atomic
    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        motivo = (payload.get("motivo") or "").strip()
        if not motivo:
            return Response({"errors": {"motivo": "Informe o cenário."}}, status=status.HTTP_400_BAD_REQUEST)
        if AuditoriaMotivoFalha.objects.filter(motivo=motivo).exists():
            return Response(
                {"errors": {"motivo": "Este cenário já existe."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        criticidade = (payload.get("criticidade") or "").strip()
        segmentos = (payload.get("segmentos") or "").strip()
        subsegmento = (payload.get("subsegmento") or "").strip()
        if not criticidade:
            return Response({"errors": {"criticidade": "Informe a criticidade."}}, status=status.HTTP_400_BAD_REQUEST)
        if not segmentos:
            return Response({"errors": {"segmentos": "Informe o segmento."}}, status=status.HTTP_400_BAD_REQUEST)
        if not subsegmento:
            return Response({"errors": {"subsegmento": "Informe o subsegmento."}}, status=status.HTTP_400_BAD_REQUEST)

        sort_order = payload.get("sort_order")
        if sort_order is None:
            last = (
                AuditoriaMotivoFalha.objects.order_by("-sort_order")
                .values_list("sort_order", flat=True)
                .first()
            )
            sort_order = (last or 0) + 1

        item = AuditoriaMotivoFalha.objects.create(
            motivo=motivo,
            criticidade=criticidade,
            segmentos=segmentos,
            subsegmento=subsegmento,
            sort_order=int(sort_order),
            active=bool(payload.get("active", True)),
        )
        fields = ("motivo", "criticidade", "segmentos", "subsegmento", "sort_order", "active")
        after = snapshot_fields(item, fields)
        log_config_change(
            instance=item,
            changes=build_changes({field: None for field in fields}, after),
            user=request.user,
        )
        return Response(serialize_motivo_falha(item), status=status.HTTP_201_CREATED)


class AuditoriaMotivoFalhaConfigDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    @transaction.atomic
    def patch(self, request, pk: int):
        item = AuditoriaMotivoFalha.objects.filter(pk=pk).first()
        if not item:
            return Response(status=status.HTTP_404_NOT_FOUND)

        fields = ("motivo", "criticidade", "segmentos", "subsegmento", "sort_order", "active")
        before = snapshot_fields(item, fields)
        payload = request.data if isinstance(request.data, dict) else {}
        errors: dict[str, str] = {}

        if "motivo" in payload:
            motivo = (payload.get("motivo") or "").strip()
            if not motivo:
                errors["motivo"] = "Informe o cenário."
            elif AuditoriaMotivoFalha.objects.filter(motivo=motivo).exclude(pk=item.pk).exists():
                errors["motivo"] = "Este cenário já existe."
            else:
                item.motivo = motivo

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        if "criticidade" in payload:
            item.criticidade = (payload.get("criticidade") or "").strip()
        if "segmentos" in payload:
            item.segmentos = (payload.get("segmentos") or "").strip()
        if "subsegmento" in payload:
            item.subsegmento = (payload.get("subsegmento") or "").strip()
        if "sort_order" in payload:
            item.sort_order = int(payload.get("sort_order") or 0)
        if "active" in payload:
            item.active = bool(payload.get("active"))

        item.save()
        log_config_change(
            instance=item,
            changes=build_changes(before, snapshot_fields(item, fields)),
            user=request.user,
        )
        return Response(serialize_motivo_falha(item))


class AuditoriaCatalogConfigImportView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request, catalog: str):
        catalog_key = _resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()
        try:
            result = import_catalog_xlsx(
                catalog=catalog_key,
                upload=request.FILES.get("file"),
                user=request.user,
            )
        except ConfigBulkError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class AuditoriaCatalogConfigImportPreviewView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request, catalog: str):
        catalog_key = _resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()
        try:
            result = preview_catalog_xlsx(catalog=catalog_key, upload=request.FILES.get("file"))
        except ConfigBulkError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class AuditoriaCatalogConfigBulkUpdateView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request, catalog: str):
        catalog_key = _resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()
        try:
            updated = bulk_update_catalog(
                catalog=catalog_key,
                rows=(request.data or {}).get("items"),
                user=request.user,
            )
        except ConfigBulkError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"updated": updated})


class AuditoriaMotivoFalhaConfigImportView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request):
        try:
            result = import_scenarios_xlsx(
                upload=request.FILES.get("file"),
                user=request.user,
                mode=str(request.data.get("mode") or "append"),
            )
        except ConfigBulkError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class AuditoriaMotivoFalhaConfigImportPreviewView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request):
        try:
            result = preview_scenarios_xlsx(
                upload=request.FILES.get("file"),
                mode=str(request.data.get("mode") or "append"),
            )
        except ConfigBulkError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class AuditoriaMotivoFalhaConfigBulkUpdateView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CATALOGOS

    def post(self, request):
        try:
            updated = bulk_update_scenarios(
                rows=(request.data or {}).get("items"),
                user=request.user,
            )
        except ConfigBulkError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"updated": updated})
