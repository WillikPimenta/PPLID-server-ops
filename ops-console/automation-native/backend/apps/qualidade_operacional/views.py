# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from time import perf_counter

from django.db.models import Max, Min
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.constants import ROLE_ADM_PORTAL
from apps.access.permission_classes import AdmPortalPerm, portal_perm
from apps.access.registry import (
    QUAL_FALHAS_EXPORT_EXECUTIVE,
    QUAL_OPERACIONAL_SYNC,
    QUAL_OPERACIONAL_VIEW,
)
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.qualidade_operacional.models import QualidadeAgenteAcao, QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.agent_detail import (
    build_agent_detail,
    list_acoes,
    serialize_acao,
)
from apps.qualidade_operacional.services.analytics import (
    build_breakdown,
    build_kpis,
    build_ranking,
    build_serie,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.dashboard import (
    build_clientes_melhorias_page,
    build_dashboard,
)
from apps.qualidade_operacional.services.enrichment import (
    build_dim_lookups,
    build_responsavel_lookup,
    build_source_meta_for_auditados,
    build_source_meta_for_falhas,
    serialize_auditado,
    serialize_falha,
)
from apps.qualidade_operacional.services.filter_catalog import get_filter_payload
from apps.qualidade_operacional.services.export import build_csv_export
from apps.qualidade_operacional.services.importer import import_qualidade_operacional
from apps.qualidade_operacional.services.insights import build_insights
from apps.qualidade_operacional.services.intranet_source import source_meta_payload
from apps.qualidade_operacional.services.performance_cache import (
    busy_response_detail,
    classify_gate_error,
    get_or_build,
    run_gated_export,
)
from apps.qualidade_operacional.services.queries import (
    apply_detail_column_filters,
    apply_detail_sort,
    date_field_auditados,
    date_field_falhas,
    parse_page,
)
from apps.qualidade_operacional.services.detail_column_filters import build_column_values
from apps.qualidade_operacional.throttling import (
    QualidadeOperacionalExportThrottle,
    QualidadeOperacionalListThrottle,
)

ViewPerm = portal_perm(QUAL_OPERACIONAL_VIEW)
SyncPerm = portal_perm(QUAL_OPERACIONAL_SYNC)
logger = logging.getLogger(__name__)


def _can_view_reconciliation(user) -> bool:
    access = resolve_user_access(user)
    return bool(access.get("bypass") or ROLE_ADM_PORTAL in set(access.get("roles") or []))


class QualityAPIView(APIView):
    """Inclui duracao no response e nos logs para acompanhar o P95."""

    def handle_exception(self, exc):
        busy = classify_gate_error(exc)
        if busy:
            body, http_status, headers = busy_response_detail(busy)
            response = Response(body, status=http_status)
            for key, value in headers.items():
                response[key] = value
            # Marca como tratado para o finalize_response do DRF.
            response.exception = True
            return response
        return super().handle_exception(exc)

    def dispatch(self, request, *args, **kwargs):
        started = perf_counter()
        response = super().dispatch(request, *args, **kwargs)
        elapsed_ms = (perf_counter() - started) * 1000
        response["Server-Timing"] = f"qualidade;dur={elapsed_ms:.1f}"
        logger.info(
            "qualidade_operacional route=%s duration_ms=%.1f status=%s",
            request.path,
            elapsed_ms,
            response.status_code,
        )
        return response


class MetaView(QualityAPIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        aud = QualidadeAuditado.objects.aggregate(min_data=Min("data"), max_data=Max("data"))
        fal = QualidadeFalha.objects.aggregate(
            min_data=Min("data_analise"), max_data=Max("data_analise")
        )
        payload = {
            "ok": True,
            "auditados_count": QualidadeAuditado.objects.count(),
            "falhas_count": QualidadeFalha.objects.count(),
            "auditados_date_range": {
                "min": aud["min_data"].isoformat() if aud["min_data"] else None,
                "max": aud["max_data"].isoformat() if aud["max_data"] else None,
            },
            "falhas_date_range": {
                "min": fal["min_data"].isoformat() if fal["min_data"] else None,
                "max": fal["max_data"].isoformat() if fal["max_data"] else None,
            },
            "source": source_meta_payload(),
        }
        return Response(payload)


class FiltrosView(QualityAPIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        return Response(get_filter_payload())


class DashboardView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        module = str(request.query_params.get("module") or "resumo").strip().lower()
        if module == "auditores" and not user_has_permission(
            request.user, QUAL_FALHAS_EXPORT_EXECUTIVE
        ):
            return Response(
                {"detail": "Sem permissão para a visão Auditores."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(build_dashboard(request.query_params))


class ClientesMelhoriasView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        return Response(build_clientes_melhorias_page(request.query_params))


class KpisView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        return Response(
            get_or_build(
                "kpis", request.query_params, lambda: build_kpis(request.query_params)
            )
        )


class SerieView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        return Response(
            get_or_build(
                "serie", request.query_params, lambda: build_serie(request.query_params)
            )
        )


class BreakdownView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        return Response(
            get_or_build(
                "breakdown", request.query_params, lambda: build_breakdown(request.query_params)
            )
        )


class RankingView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        return Response(
            get_or_build(
                "ranking", request.query_params, lambda: build_ranking(request.query_params)
            )
        )


class InsightsView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        return Response(
            get_or_build(
                "insights", request.query_params, lambda: build_insights(request.query_params)
            )
        )


class DetailColumnValuesView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        lista = str(request.query_params.get("lista") or "auditados").strip().lower()
        kind = "falhas" if lista == "falhas" else "auditados"
        column = str(request.query_params.get("column") or "").strip()
        if not column:
            return Response(
                {"ok": False, "detail": "Informe o parâmetro column."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = min(500, max(1, int(request.query_params.get("limit") or 200)))
        except (TypeError, ValueError):
            limit = 200
        q = str(request.query_params.get("q") or "").strip()
        if kind == "falhas":
            qs = apply_detail_column_filters(
                filtered_falhas(request.query_params), request.query_params, kind="falhas"
            )
        else:
            qs = apply_detail_column_filters(
                filtered_auditados(request.query_params),
                request.query_params,
                kind="auditados",
            )
        payload = build_column_values(
            qs,
            column,
            kind=kind,
            q=q,
            limit=limit,
            params=request.query_params,
        )
        return Response(payload)


class AuditadosView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        aud_date = date_field_auditados(request.query_params)
        qs = filtered_auditados(request.query_params)
        qs = apply_detail_column_filters(qs, request.query_params, kind="auditados")
        page, page_size = parse_page(request.query_params)
        total = qs.count()
        start = (page - 1) * page_size
        qs = apply_detail_sort(
            qs,
            request.query_params,
            kind="auditados",
            default_date_field=aud_date,
        )
        rows = list(qs[start : start + page_size])

        cliente_ids = {r.id_cliente for r in rows if r.id_cliente}
        workflow_ids = {r.id_workflow for r in rows if r.id_workflow}
        mats = {r.matricula for r in rows if r.matricula} | {
            r.matricula_auditor for r in rows if r.matricula_auditor
        }
        clientes, workflows, agents = build_dim_lookups(cliente_ids, workflow_ids, mats)
        source_meta = build_source_meta_for_auditados(rows)
        responsavel_lookup = build_responsavel_lookup(
            rows, date_field=date_field_auditados(request.query_params)
        )

        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [
                    serialize_auditado(
                        r,
                        clientes,
                        workflows,
                        agents,
                        source_meta=source_meta.get(r.pk),
                        responsavel=responsavel_lookup.get(r.pk),
                    )
                    for r in rows
                ],
            }
        )


class FalhasView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        fal_date = date_field_falhas(request.query_params)
        qs = filtered_falhas(request.query_params)
        qs = apply_detail_column_filters(qs, request.query_params, kind="falhas")
        page, page_size = parse_page(request.query_params)
        total = qs.count()
        start = (page - 1) * page_size
        qs = apply_detail_sort(
            qs,
            request.query_params,
            kind="falhas",
            default_date_field=fal_date,
        )
        rows = list(qs[start : start + page_size])

        cliente_ids = {r.id_cliente for r in rows if r.id_cliente}
        workflow_ids = {r.id_workflow for r in rows if r.id_workflow}
        mats = {r.matricula for r in rows if r.matricula} | {
            r.usuario_auditor for r in rows if r.usuario_auditor
        }
        clientes, workflows, agents = build_dim_lookups(cliente_ids, workflow_ids, mats)
        source_meta = build_source_meta_for_falhas(rows)
        include_reconciliation = _can_view_reconciliation(request.user)
        responsavel_lookup = build_responsavel_lookup(
            rows, date_field=date_field_falhas(request.query_params)
        )

        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [
                    serialize_falha(
                        r,
                        clientes,
                        workflows,
                        agents,
                        source_meta=source_meta.get(r.pk),
                        include_reconciliation=include_reconciliation,
                        lider_responsavel=(
                            responsavel_lookup.get(r.pk) or {}
                        ).get("responsavel_nome")
                        if (responsavel_lookup.get(r.pk) or {}).get("responsabilidade")
                        == "lider"
                        else None,
                        responsavel=responsavel_lookup.get(r.pk),
                    )
                    for r in rows
                ],
            }
        )


class AuditadosExportView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalExportThrottle]

    def get(self, request):
        return run_gated_export(
            "auditados",
            request.query_params,
            lambda: build_csv_export(request.query_params, kind="auditados"),
        )


class FalhasExportView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalExportThrottle]

    def get(self, request):
        return run_gated_export(
            "falhas",
            request.query_params,
            lambda: build_csv_export(request.query_params, kind="falhas"),
        )


class SyncView(QualityAPIView):
    permission_classes = [SyncPerm]

    def post(self, request):
        mode = (request.data.get("mode") or request.query_params.get("mode") or "upsert").strip()
        if mode not in {"upsert", "replace"}:
            mode = "upsert"
        only = (request.data.get("only") or request.query_params.get("only") or "").strip() or None
        if only not in (None, "auditados", "falhas"):
            only = None
        stats = import_qualidade_operacional(mode=mode, only=only)
        return Response({"ok": True, "mode": mode, "only": only, **stats.as_dict()})


class IntranetSyncView(QualityAPIView):
    """Projeta tratados Intranet → qualidade_auditado/falha (equivalente ao management command)."""

    permission_classes = [AdmPortalPerm]

    def post(self, request):
        from apps.qualidade_operacional.services.intranet_source import (
            eligible_sources_qs,
            sync_queryset,
        )
        from apps.qualidade_operacional.services.source_config import intranet_source_enabled

        if not intranet_source_enabled():
            return Response(
                {
                    "ok": False,
                    "error": "intranet_disabled",
                    "message": (
                        "Fonte Intranet desabilitada. "
                        "Ative QUALIDADE_INTRANET_SOURCE_ENABLED no ambiente."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        full = _parse_bool(request.data.get("full") if request.data else None)
        if full is None:
            full = _parse_bool(request.query_params.get("full"))
        if full is None:
            full = True

        report = sync_queryset(
            eligible_sources_qs().select_related(
                "atividade", "created_by", "analise_origem"
            ),
            force=full,
        )
        return Response({"ok": True, "full": full, **report.as_dict()})


def _parse_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


class AgenteDetalheView(QualityAPIView):
    """Visão consolidada do operador para o modal da aba Agentes."""

    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        matricula = (
            request.query_params.get("matricula")
            or (request.query_params.getlist("matricula") or [None])[0]
            or ""
        ).strip()
        if not matricula:
            return Response(
                {
                    "ok": False,
                    "error": "matricula_obrigatoria",
                    "message": "Informe a matrícula do agente.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            get_or_build(
                "agente_detalhe",
                request.query_params,
                lambda: build_agent_detail(request.query_params),
            )
        )


class AgenteAcoesView(QualityAPIView):
    """CRUD leve de ações de acompanhamento do agente."""

    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        matricula = (request.query_params.get("matricula") or "").strip()
        if not matricula:
            return Response(
                {
                    "ok": False,
                    "error": "matricula_obrigatoria",
                    "message": "Informe a matrícula do agente.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        status_filter = (request.query_params.get("status") or "").strip() or None
        return Response(list_acoes(matricula, status=status_filter))

    def post(self, request):
        data = request.data if hasattr(request, "data") else {}
        matricula = str(data.get("matricula") or "").strip().lower()
        titulo = str(data.get("titulo") or "").strip()
        if not matricula or not titulo:
            return Response(
                {
                    "ok": False,
                    "error": "validacao",
                    "message": "Matrícula e título são obrigatórios.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        tipo = str(data.get("tipo") or QualidadeAgenteAcao.Tipo.OUTRO).strip()
        if tipo not in QualidadeAgenteAcao.Tipo.values:
            tipo = QualidadeAgenteAcao.Tipo.OUTRO
        prioridade = str(data.get("prioridade") or QualidadeAgenteAcao.Prioridade.MEDIA).strip()
        if prioridade not in QualidadeAgenteAcao.Prioridade.values:
            prioridade = QualidadeAgenteAcao.Prioridade.MEDIA
        frequencia = str(data.get("frequencia") or QualidadeAgenteAcao.Frequencia.UNICA).strip()
        if frequencia not in QualidadeAgenteAcao.Frequencia.values:
            frequencia = QualidadeAgenteAcao.Frequencia.UNICA

        prazo_raw = data.get("prazo")
        prazo = None
        if prazo_raw:
            try:
                from datetime import date as date_cls

                prazo = date_cls.fromisoformat(str(prazo_raw)[:10])
            except ValueError:
                prazo = None

        auditorias = data.get("auditorias_vinculadas") or []
        if not isinstance(auditorias, list):
            auditorias = []

        acao = QualidadeAgenteAcao.objects.create(
            matricula=matricula,
            titulo=titulo[:255],
            descricao=str(data.get("descricao") or "").strip(),
            tipo=tipo,
            responsavel=str(data.get("responsavel") or "").strip()[:255],
            prioridade=prioridade,
            status=QualidadeAgenteAcao.Status.ABERTA,
            prazo=prazo,
            categoria_falha=str(data.get("categoria_falha") or "").strip()[:255],
            auditorias_vinculadas=[str(x) for x in auditorias][:50],
            criterio_sucesso=str(data.get("criterio_sucesso") or "").strip(),
            frequencia=frequencia,
            observacoes=str(data.get("observacoes") or "").strip(),
            created_by=getattr(request.user, "username", "") or "",
        )
        return Response(
            {"ok": True, "acao": serialize_acao(acao)},
            status=status.HTTP_201_CREATED,
        )


class AgenteAcaoDetailView(QualityAPIView):
    permission_classes = [ViewPerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def patch(self, request, pk: int):
        try:
            acao = QualidadeAgenteAcao.objects.get(pk=pk)
        except QualidadeAgenteAcao.DoesNotExist:
            return Response(
                {"ok": False, "error": "nao_encontrada", "message": "Ação não encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = request.data if hasattr(request, "data") else {}
        if "status" in data:
            novo = str(data.get("status") or "").strip()
            if novo in QualidadeAgenteAcao.Status.values:
                acao.status = novo
                if novo == QualidadeAgenteAcao.Status.CONCLUIDA and not acao.concluida_at:
                    acao.concluida_at = timezone.now()
                if novo != QualidadeAgenteAcao.Status.CONCLUIDA:
                    acao.concluida_at = None
        for field in (
            "titulo",
            "descricao",
            "responsavel",
            "observacoes",
            "criterio_sucesso",
            "categoria_falha",
        ):
            if field in data:
                setattr(acao, field, str(data.get(field) or "").strip())
        if "prioridade" in data:
            p = str(data.get("prioridade") or "").strip()
            if p in QualidadeAgenteAcao.Prioridade.values:
                acao.prioridade = p
        if "tipo" in data:
            t = str(data.get("tipo") or "").strip()
            if t in QualidadeAgenteAcao.Tipo.values:
                acao.tipo = t
        if "prazo" in data:
            prazo_raw = data.get("prazo")
            if not prazo_raw:
                acao.prazo = None
            else:
                try:
                    from datetime import date as date_cls

                    acao.prazo = date_cls.fromisoformat(str(prazo_raw)[:10])
                except ValueError:
                    pass
        acao.save()
        return Response({"ok": True, "acao": serialize_acao(acao)})
