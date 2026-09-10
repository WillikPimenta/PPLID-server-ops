# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from time import perf_counter

from django.db.models import Q
from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response

from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
from apps.common.models import BotDbSyncJob
from apps.replicacao_d1.constants import REPORT_TYPE_REPLICADOS
from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Replicado,
    ReplicacaoD1Run,
    ReplicacaoD1SyncLog,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.serializers import (
    ReplicacaoD1ProtocoloSerializer,
    ReplicacaoD1ReplicadoSerializer,
    ReplicacaoD1RunSerializer,
    ReplicacaoD1WorkflowDiaSerializer,
)
from apps.replicacao_d1.services.dashboard import (
    build_dashboard_export_csv,
    build_protocolo_detail,
    build_replicacao_d1_dashboard,
    build_workflow_projection,
    build_workflow_projection_export_csv,
    parse_dashboard_params,
    parse_workflow_projection_params,
)
from apps.replicacao_d1.services.dashboard_agentes import (
    build_agentes_dashboard,
    build_agentes_export_csv,
    parse_agentes_params,
)
from apps.replicacao_d1.services.runs_operational import (
    build_run_operational_summaries,
    serialize_run_operational,
)
from apps.replicacao_d1.services.projection_pdf import export_workflow_projection_pdf
from apps.replicacao_d1.services.secure_path import InsecurePathError, sanitize_error_message
from apps.replicacao_d1.services.source_path import get_source_file, resolve_source_by_run_id
from apps.replicacao_d1.services.replicados_source_path import get_replicados_source_file
from apps.replicacao_d1.views.base import ReplicacaoD1APIView, ReplicacaoD1SyncWriteAPIView
from apps.replicacao_d1.services.dashboard_serve import dashboard_busy_response, resolve_dashboard_payload
from apps.replicacao_d1.throttling import ReplicacaoD1DashboardThrottle


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%d/%m/%Y").date()
        except ValueError:
            return None


def _paginate(queryset, request, serializer_class):
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int(request.query_params.get("page_size") or 50)))
    except (TypeError, ValueError):
        page_size = 50
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    return Response(
        {
            "count": paginator.count,
            "page": page_obj.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "results": serializer_class(page_obj.object_list, many=True).data,
        }
    )


class RunsListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1Run.objects.all()
        run_id = (request.query_params.get("run_id") or "").strip()
        if run_id:
            qs = qs.filter(run_id=run_id)
        data_de = _parse_date(request.query_params.get("data_de"))
        data_ate = _parse_date(request.query_params.get("data_ate"))
        if data_de:
            qs = qs.filter(data_referencia_d1__gte=data_de)
        if data_ate:
            qs = qs.filter(data_referencia_d1__lte=data_ate)
        operacional = (request.query_params.get("operacional") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if operacional:
            try:
                page = max(1, int(request.query_params.get("page") or 1))
            except (TypeError, ValueError):
                page = 1
            try:
                page_size = min(500, max(1, int(request.query_params.get("page_size") or 50)))
            except (TypeError, ValueError):
                page_size = 50
            paginator = Paginator(qs.order_by("-data_referencia_d1", "-run_id"), page_size)
            page_obj = paginator.get_page(page)
            summaries = build_run_operational_summaries(list(page_obj.object_list))
            results = [
                serialize_run_operational(run, summaries.get(run.run_id))
                for run in page_obj.object_list
            ]
            return Response(
                {
                    "count": paginator.count,
                    "page": page_obj.number,
                    "page_size": page_size,
                    "num_pages": paginator.num_pages,
                    "results": results,
                }
            )
        return _paginate(qs, request, ReplicacaoD1RunSerializer)


class RunDetailView(ReplicacaoD1APIView):
    def get(self, request, run_id: str):
        try:
            run = ReplicacaoD1Run.objects.get(run_id=run_id)
        except ReplicacaoD1Run.DoesNotExist:
            return Response({"detail": "Run não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        summary = build_run_operational_summaries([run]).get(run.run_id, {})
        payload = dict(ReplicacaoD1RunSerializer(run).data)
        payload["resumo_operacional"] = serialize_run_operational(run, summary)
        return Response(payload)


class WorkflowsListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1WorkflowDia.objects.all()
        run_id = (request.query_params.get("run_id") or "").strip()
        if run_id:
            qs = qs.filter(run_id=run_id)
        cliente = (request.query_params.get("cliente") or "").strip()
        if cliente:
            qs = qs.filter(cliente__iexact=cliente)
        status_br = (request.query_params.get("status_brflow") or "").strip()
        if status_br:
            qs = qs.filter(status_brflow__iexact=status_br)
        canal = (request.query_params.get("canal_destino") or "").strip()
        if canal:
            qs = qs.filter(canal_destino__iexact=canal)
        data_de = _parse_date(request.query_params.get("data_de"))
        data_ate = _parse_date(request.query_params.get("data_ate"))
        if data_de:
            qs = qs.filter(data_referencia_d1__gte=data_de)
        if data_ate:
            qs = qs.filter(data_referencia_d1__lte=data_ate)
        return _paginate(qs.order_by("-data_referencia_d1", "workflow_config"), request, ReplicacaoD1WorkflowDiaSerializer)


class ProtocolosListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1Protocolo.objects.all()
        run_id = (request.query_params.get("run_id") or "").strip()
        if run_id:
            qs = qs.filter(run_id=run_id)
        workflow = (request.query_params.get("workflow_config") or "").strip()
        if workflow:
            qs = qs.filter(workflow_config=workflow)
        protocolo = (request.query_params.get("protocolo") or "").strip()
        if protocolo:
            from apps.replicacao_d1.normalization import normalize_protocolo

            norm = normalize_protocolo(protocolo)
            if norm:
                qs = qs.filter(
                    Q(protocolo__icontains=protocolo)
                    | Q(protocolo_normalizado=norm)
                )
            else:
                qs = qs.filter(protocolo__icontains=protocolo)
        status_op = (request.query_params.get("status_operacional") or "").strip()
        if status_op:
            qs = qs.filter(status_operacional=status_op)
        data_de = _parse_date(request.query_params.get("data_de"))
        data_ate = _parse_date(request.query_params.get("data_ate"))
        if data_de:
            qs = qs.filter(data_referencia_d1__gte=data_de)
        if data_ate:
            qs = qs.filter(data_referencia_d1__lte=data_ate)
        return _paginate(
            qs.order_by("-data_referencia_d1", "workflow_config", "protocolo"),
            request,
            ReplicacaoD1ProtocoloSerializer,
        )


class ReplicadosListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1Replicado.objects.all()
        protocolo = (request.query_params.get("protocolo_origem") or "").strip()
        if protocolo:
            qs = qs.filter(protocolo_origem__icontains=protocolo)
        workflow = (request.query_params.get("workflow_origem") or "").strip()
        if workflow:
            qs = qs.filter(workflow_origem__icontains=workflow)
        data_de = _parse_date(request.query_params.get("data_de"))
        data_ate = _parse_date(request.query_params.get("data_ate"))
        if data_de:
            qs = qs.filter(report_date__gte=data_de)
        if data_ate:
            qs = qs.filter(report_date__lte=data_ate)
        report_date = _parse_date(request.query_params.get("report_date"))
        if report_date:
            qs = qs.filter(report_date=report_date)
        return _paginate(
            qs.order_by("-report_date", "protocolo_origem"),
            request,
            ReplicacaoD1ReplicadoSerializer,
        )


class SyncView(ReplicacaoD1SyncWriteAPIView):
    def post(self, request):
        force = request.data.get("force") in (True, "true", "1", 1)
        run_id = (request.data.get("run_id") or "").strip()
        report_date = _parse_date(request.data.get("report_date"))
        kind = (request.data.get("kind") or request.data.get("report_type") or "").strip().lower()
        is_replicados = kind == REPORT_TYPE_REPLICADOS

        try:
            if is_replicados:
                info = get_replicados_source_file(report_date=report_date)
                source_path = str(info.path)
                report_type = REPORT_TYPE_REPLICADOS
            else:
                info = get_source_file(run_id=run_id or None)
                source_path = str(info.path)
                report_type = run_id or info.run_id
            job, created = enqueue_bot_db_sync(
                domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
                source_path=source_path,
                report_type=report_type,
                force=force,
                spawn=True,
            )
        except (InsecurePathError, FileNotFoundError, ValueError) as exc:
            return Response(
                {"detail": sanitize_error_message(str(exc))},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"detail": sanitize_error_message(str(exc))},
                status=status.HTTP_400_BAD_REQUEST,
            )

        last = ReplicacaoD1SyncLog.objects.order_by("-started_at").first()
        return Response(
            {
                "queued": True,
                "created": created,
                "job_id": job.pk,
                "job_status": job.status,
                "kind": REPORT_TYPE_REPLICADOS if report_type == REPORT_TYPE_REPLICADOS else "plano",
                "message": (
                    "Sincronização enfileirada; o drain processará em background."
                    if created
                    else "Já havia sincronização pendente/em execução para esta fonte."
                ),
                "last_sync": (
                    {
                        "success": last.success,
                        "message": last.message,
                        "kind": last.kind,
                        "run_id": last.run_id,
                        "report_date": last.report_date,
                        "finished_at": last.finished_at,
                    }
                    if last
                    else None
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class DashboardView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        started_at = perf_counter()
        params = parse_dashboard_params(request.query_params.dict())
        payload, busy, cache_status = resolve_dashboard_payload(
            request.query_params.dict(),
            lambda: build_replicacao_d1_dashboard(params),
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            response = Response(body, status=http_status, headers=headers)
        else:
            response = Response(payload)
        elapsed_ms = (perf_counter() - started_at) * 1000
        response["Server-Timing"] = (
            f'replicacao_d1;dur={elapsed_ms:.1f};desc="cache-{cache_status}"'
        )
        response["X-Dashboard-Cache"] = cache_status
        return response


class DashboardExportView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        params = parse_dashboard_params(request.query_params.dict())
        result, busy, _cache_status = resolve_dashboard_payload(
            request.query_params.dict(),
            lambda: build_dashboard_export_csv(params),
            namespace="dashboard_export_csv",
            cacheable=False,
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            return Response(body, status=http_status, headers=headers)
        filename, content = result
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class DashboardProjectionView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        params = parse_workflow_projection_params(request.query_params.dict())
        payload, busy, _cache_status = resolve_dashboard_payload(
            request.query_params.dict(),
            lambda: build_workflow_projection(params),
            namespace="projection",
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            return Response(body, status=http_status, headers=headers)
        return Response(payload)


class DashboardProjectionExportView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        params = parse_workflow_projection_params(request.query_params.dict())
        result, busy, _cache_status = resolve_dashboard_payload(
            request.query_params.dict(),
            lambda: build_workflow_projection_export_csv(params),
            namespace="projection_export_csv",
            cacheable=False,
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            return Response(body, status=http_status, headers=headers)
        filename, content = result
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class DashboardProjectionPdfExportView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        params = parse_workflow_projection_params(request.query_params.dict())
        generated_by = request.user.get_full_name() or request.user.get_username()
        gate_params = {
            **request.query_params.dict(),
            "_generated_by_user_id": str(request.user.pk),
        }
        result, busy, _cache_status = resolve_dashboard_payload(
            gate_params,
            lambda: export_workflow_projection_pdf(params, generated_by=generated_by),
            namespace="projection_export_pdf",
            cacheable=False,
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            return Response(body, status=http_status, headers=headers)
        filename, content = result
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class DashboardAgentesView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        params = parse_agentes_params(request.query_params.dict())
        payload, busy, _cache_status = resolve_dashboard_payload(
            request.query_params.dict(),
            lambda: build_agentes_dashboard(params),
            namespace="agentes",
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            return Response(body, status=http_status, headers=headers)
        return Response(payload)


class DashboardAgentesExportView(ReplicacaoD1APIView):
    throttle_classes = [ReplicacaoD1DashboardThrottle]

    def get(self, request):
        result, busy, _cache_status = resolve_dashboard_payload(
            request.query_params.dict(),
            lambda: build_agentes_export_csv(request.query_params.dict()),
            namespace="agentes_export_csv",
            cacheable=False,
        )
        if busy:
            body, http_status, headers = dashboard_busy_response(busy)
            return Response(body, status=http_status, headers=headers)
        filename, content = result
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class ProtocoloDetailView(ReplicacaoD1APIView):
    def get(self, request, run_id: str, protocolo: str):
        detail = build_protocolo_detail(run_id, protocolo)
        if detail is None:
            return Response({"detail": "Protocolo não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return Response(detail)
