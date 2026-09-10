"""API — Contestação operacional (liderança) e Contestação Interna."""

from __future__ import annotations

import json
from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access import registry as R
from apps.access.permission_classes import HasPortalPermission
from apps.access.resolve import user_has_any_permission, user_has_permission
from apps.auditoria.models import ContestacaoOperacional
from apps.auditoria.services import contestacao_operacional as svc
from apps.auditoria.services.contestacao_operacional import (
    COLUMN_FILTER_KEYS,
    FILA_COLUMN_FILTER_KEYS,
    REVISAO_COLUMN_FILTER_KEYS,
)


class ContestacaoPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    return parsed


def _validation_response(exc: ValidationError) -> Response:
    if hasattr(exc, "message_dict"):
        return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
    if hasattr(exc, "messages"):
        return Response({"detail": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _load_contestacao(pk: int) -> ContestacaoOperacional | None:
    return (
        ContestacaoOperacional.objects.select_related(
            "falha", "falha__auditor_ref", "created_by", "analista", "analista_decisao"
        )
        .prefetch_related("historico")
        .filter(pk=pk)
        .first()
    )


def _can_view_contestacao(user, item: ContestacaoOperacional) -> bool:
    if item.created_by_id == user.id and user_has_permission(user, R.OPERACAO_CONTESTACAO_VIEW):
        return True
    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        return user_has_any_permission(
            user,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE,
        )
    if item.dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
        return user_has_any_permission(
            user,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE,
        )
    return False


def _parse_column_filters(request, allowed_keys: frozenset[str]) -> dict[str, list[str]]:
    raw = (request.query_params.get("column_filters") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, values in parsed.items():
        if key not in allowed_keys:
            continue
        if isinstance(values, str):
            cleaned = [values.strip()] if values.strip() else []
        elif isinstance(values, (list, tuple, set)):
            cleaned = [str(value).strip() for value in values if str(value).strip()]
        else:
            cleaned = []
        if cleaned:
            result[key] = cleaned
    return result


def _parse_fila_column_filters(request) -> dict[str, list[str]]:
    return _parse_column_filters(request, FILA_COLUMN_FILTER_KEYS)


def _parse_revisao_column_filters(request) -> dict[str, list[str]]:
    return _parse_column_filters(request, REVISAO_COLUMN_FILTER_KEYS)


def _paginated_total(queryset) -> int:
    if isinstance(queryset, list):
        return len(queryset)
    return queryset.count()


def _paginated_payload(*, paginator, queryset, page, page_size, results):
    total = _paginated_total(queryset)
    total_pages = max(1, (total + page_size - 1) // page_size) if page_size else 1
    return {
        "count": total,
        "total_pages": total_pages,
        "page": page,
        "page_size": page_size,
        "next": page * page_size < total,
        "previous": page > 1,
        "results": results,
    }


def _parse_column_filters_legacy(request) -> dict[str, list[str]]:
    return _parse_column_filters(request, COLUMN_FILTER_KEYS)


def _falhas_filtered_queryset(request):
    qs = svc.falhas_queryset_for_user(request.user)
    qs = svc.filter_falhas(
        qs,
        protocolo=request.query_params.get("protocolo", ""),
        agente=request.query_params.get("agente", ""),
        categoria=request.query_params.get("categoria", ""),
        data_inicio=_parse_dt(request.query_params.get("data_inicio")),
        data_fim=_parse_dt(request.query_params.get("data_fim")),
    )
    qs = svc.apply_column_filters(qs, _parse_column_filters_legacy(request))
    ordering = (request.query_params.get("ordering") or "").strip()
    if not ordering:
        sort_key = (request.query_params.get("sort") or "").strip()
        sort_dir = (request.query_params.get("sort_dir") or "asc").strip().lower()
        if sort_key:
            ordering = f"-{sort_key}" if sort_dir == "desc" else sort_key
    return svc.apply_falhas_ordering(qs, ordering)


class ContestacaoOperacaoFalhasView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.OPERACAO_CONTESTACAO_VIEW

    def get(self, request):
        qs = _falhas_filtered_queryset(request)
        paginator = ContestacaoPagination()
        page_size = paginator.get_page_size(request)
        try:
            page_number = int(request.query_params.get(paginator.page_query_param, 1))
        except (TypeError, ValueError):
            page_number = 1
        groups, total = svc.paginate_falha_protocol_groups(
            qs,
            page=page_number,
            page_size=page_size,
        )
        data = svc.serialize_protocol_groups(groups)
        return Response(
            {
                "count": total,
                "next": (
                    f"?page={page_number + 1}&page_size={page_size}"
                    if page_number * page_size < total
                    else None
                ),
                "previous": (
                    f"?page={page_number - 1}&page_size={page_size}" if page_number > 1 else None
                ),
                "results": data,
            }
        )


class ContestacaoOperacaoFalhasFacetasView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.OPERACAO_CONTESTACAO_VIEW

    def get(self, request):
        qs = svc.falhas_queryset_for_user(request.user)
        qs = svc.filter_falhas(
            qs,
            protocolo=request.query_params.get("protocolo", ""),
            agente=request.query_params.get("agente", ""),
            categoria=request.query_params.get("categoria", ""),
            data_inicio=_parse_dt(request.query_params.get("data_inicio")),
            data_fim=_parse_dt(request.query_params.get("data_fim")),
        )
        return Response({"facets": svc.column_facets_for_falhas(qs)})


class ContestacaoOperacaoAgentesView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.OPERACAO_CONTESTACAO_VIEW

    def get(self, request):
        qs = svc.falhas_queryset_for_user(request.user)
        return Response({"results": svc.agent_options_for_falhas(qs)})


class ContestacaoOperacaoCriarView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.OPERACAO_CONTESTACAO_CREATE

    def post(self, request):
        try:
            falha_id = int(request.data.get("falha_id"))
        except (TypeError, ValueError):
            return Response({"falha_id": "Informe falha_id numérico."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            item = svc.criar_contestacao(
                user=request.user,
                falha_id=falha_id,
                justificativa=request.data.get("justificativa") or "",
                categoria=request.data.get("categoria") or None,
            )
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(svc.serialize_contestacao(_load_contestacao(item.pk)), status=status.HTTP_201_CREATED)


class ContestacaoOperacaoMinhasView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.OPERACAO_CONTESTACAO_VIEW

    def get(self, request):
        qs = svc.minhas_contestacoes_queryset(request.user)
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        paginator = ContestacaoPagination()
        page = paginator.paginate_queryset(qs, request)
        data = svc.serialize_contestacoes(list(page))
        return paginator.get_paginated_response(data)


class ContestacaoInternaFilaView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]

    def initial(self, request, *args, **kwargs):
        dominio = (kwargs.get("dominio") or "").strip().lower()
        if dominio == ContestacaoOperacional.DOMINIO_FRAUD:
            self.portal_permission = R.QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW
        else:
            self.portal_permission = R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW
        super().initial(request, *args, **kwargs)

    def get(self, request, dominio: str):
        qs = svc.contestacoes_fila_queryset(request.user, dominio=dominio)
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter == "respondidas":
            qs = qs.filter(
                status__in=(
                    ContestacaoOperacional.STATUS_PROCEDENTE,
                    ContestacaoOperacional.STATUS_IMPROCEDENTE,
                )
            )
        elif status_filter:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.filter(status__in=ContestacaoOperacional.STATUS_ATIVOS)

        filter_column = (request.query_params.get("filter_column") or "").strip()
        column_filters = _parse_fila_column_filters(request)
        if filter_column:
            return Response(
                {
                    "options": svc.fila_column_filter_options(
                        qs,
                        filter_column,
                        column_filters,
                    )
                }
            )

        qs = svc.apply_contestacao_fila_column_filters(qs, column_filters)
        sort_key = (request.query_params.get("sort_key") or request.query_params.get("sort") or "").strip()
        sort_dir = (request.query_params.get("sort_dir") or "asc").strip().lower()
        if sort_key:
            qs = svc.apply_contestacao_fila_ordering(qs, sort_key, sort_dir)
        elif status_filter == "respondidas":
            qs = qs.order_by("-decidida_em", "-created_at", "-id")
        else:
            qs = qs.order_by("-created_at", "-id")

        paginator = ContestacaoPagination()
        page_size = paginator.get_page_size(request)
        try:
            page_number = int(request.query_params.get(paginator.page_query_param, 1))
        except (TypeError, ValueError):
            page_number = 1
        page_number = max(1, page_number)
        offset = (page_number - 1) * page_size
        total = qs.count()
        page_qs = qs[offset : offset + page_size]
        data = svc.serialize_contestacoes(list(page_qs))
        return Response(
            _paginated_payload(
                paginator=paginator,
                queryset=qs,
                page=page_number,
                page_size=page_size,
                results=data,
            )
        )


class ContestacaoInternaRevisoesView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]

    def initial(self, request, *args, **kwargs):
        dominio = (kwargs.get("dominio") or "").strip().lower()
        if dominio == ContestacaoOperacional.DOMINIO_FRAUD:
            self.portal_permission = R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE
        else:
            self.portal_permission = R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE
        super().initial(request, *args, **kwargs)

    def get(self, request, dominio: str):
        filter_column = (request.query_params.get("filter_column") or "").strip()
        column_filters = _parse_revisao_column_filters(request)
        historico_qs = svc.revisoes_queryset(request.user, dominio=dominio)
        rows = svc.serialize_contestacao_revisoes(list(historico_qs))

        if filter_column:
            return Response(
                {
                    "options": svc.revisao_column_filter_options(
                        rows,
                        filter_column,
                        column_filters,
                    )
                }
            )

        rows = svc.apply_revisao_column_filters(rows, column_filters)
        sort_key = (request.query_params.get("sort_key") or request.query_params.get("sort") or "").strip()
        sort_dir = (request.query_params.get("sort_dir") or "desc").strip().lower()
        reverse = sort_dir == "desc"
        if sort_key == "revisado_em":
            rows.sort(key=lambda row: row.get("revisado_em") or "", reverse=reverse)
        elif sort_key == "protocolo":
            rows.sort(key=lambda row: row.get("protocolo") or "", reverse=reverse)
        else:
            rows.sort(key=lambda row: row.get("revisado_em") or "", reverse=True)

        paginator = ContestacaoPagination()
        page_size = paginator.get_page_size(request)
        try:
            page_number = int(request.query_params.get(paginator.page_query_param, 1))
        except (TypeError, ValueError):
            page_number = 1
        page_number = max(1, page_number)
        total = len(rows)
        offset = (page_number - 1) * page_size
        page_rows = rows[offset : offset + page_size]
        return Response(
            _paginated_payload(
                paginator=paginator,
                queryset=rows,
                page=page_number,
                page_size=page_size,
                results=page_rows,
            )
        )


class ContestacaoInternaDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.PORTAL_SECAO_VIEW

    def get(self, request, pk: int):
        item = _load_contestacao(pk)
        if not item:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_view_contestacao(request.user, item):
            return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            svc.serialize_contestacao(item, include_falha=True, user=request.user)
        )


class ContestacaoInternaRevisarView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.PORTAL_SECAO_VIEW

    def post(self, request, pk: int):
        item = _load_contestacao(pk)
        if not item:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        if not svc.can_user_revise_contestacao(request.user, item):
            return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)
        try:
            item = svc.revisar_resultado_contestacao(
                user=request.user,
                contestacao_id=pk,
                resultado=request.data.get("resultado") or "",
                parecer=request.data.get("parecer") or "",
                destino_falha=request.data.get("destino_falha") or "",
                cenario=request.data.get("cenario") or "",
                nivel=request.data.get("nivel") or "",
                etapa=request.data.get("etapa") or "",
                falhas_manter=request.data.get("falhas_manter"),
            )
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(
            svc.serialize_contestacao(_load_contestacao(item.pk), include_falha=True, user=request.user)
        )


class ContestacaoInternaIniciarView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.PORTAL_SECAO_VIEW

    def post(self, request, pk: int):
        try:
            item = svc.iniciar_analise(user=request.user, contestacao_id=pk)
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(svc.serialize_contestacao(_load_contestacao(item.pk)))


class ContestacaoInternaDevolverView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.PORTAL_SECAO_VIEW

    def post(self, request, pk: int):
        try:
            item = svc.devolver_analise_fila(user=request.user, contestacao_id=pk)
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(svc.serialize_contestacao(_load_contestacao(item.pk)))


class ContestacaoInternaDecidirView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.PORTAL_SECAO_VIEW

    def post(self, request, pk: int):
        try:
            item = svc.decidir_contestacao(
                user=request.user,
                contestacao_id=pk,
                resultado=request.data.get("resultado") or "",
                parecer=request.data.get("parecer") or "",
                destino_falha=request.data.get("destino_falha") or "",
                cenario=request.data.get("cenario") or "",
                nivel=request.data.get("nivel") or "",
                etapa=request.data.get("etapa") or "",
                confirmar_outro_analista=request.data.get(
                    "confirmar_outro_analista",
                    False,
                ),
                falhas_manter=request.data.get("falhas_manter"),
            )
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(svc.serialize_contestacao(_load_contestacao(item.pk)))
