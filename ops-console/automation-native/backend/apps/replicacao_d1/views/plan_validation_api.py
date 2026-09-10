"""Endpoints da aba Validar plano; todas as leituras vêm do PostgreSQL."""
from __future__ import annotations

import csv
import io

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.replicacao_d1.models import ReplicacaoD1PlanReview, ReplicacaoD1Run
from apps.replicacao_d1.services.plan_validation import (
    PlanDeletionBlockedError,
    PlanImmutableError,
    build_plan_warning_groups,
    build_plan_validation,
    delete_plan,
    exclude_deleted_plans,
    review_plan,
)
from apps.replicacao_d1.views.base import ReplicacaoD1APIView


def _positive_int(value, default: int, maximum: int | None = None) -> int:
    try:
        parsed = max(1, int(value or default))
    except (TypeError, ValueError):
        parsed = default
    return min(parsed, maximum) if maximum else parsed


def _run_row(run: ReplicacaoD1Run) -> dict:
    warning_groups = build_plan_warning_groups(run.plan_warnings)
    return {
        "run_id": run.run_id,
        "data_referencia_d1": run.data_referencia_d1,
        "status": run.status_canonical,
        "validation_status": run.validation_status,
        "plan_hash": run.plan_hash,
        "plan_revision": run.plan_revision,
        "protocolos_total": run.protocolos_total,
        "workflows_total": int(getattr(run, "actionable_workflows", run.workflows_total) or 0),
        "plan_summary": run.plan_summary,
        "warnings_count": len(warning_groups),
        "created_at": run.created_at,
        "reviewed_at": run.reviewed_at,
        "reviewed_by": run.reviewed_by.get_username() if run.reviewed_by_id else "",
        "source_batch_id": run.source_batch_id,
    }


class PlanListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = exclude_deleted_plans(
            ReplicacaoD1Run.objects.select_related("reviewed_by").exclude(plan_hash="")
        ).annotate(
            actionable_workflows=Count(
                "workflows",
                filter=Q(workflows__protocolos_planejados__gt=0),
                distinct=True,
            )
        )
        validation_status = str(request.query_params.get("validation_status") or "").strip()
        if validation_status:
            qs = qs.filter(validation_status=validation_status)
        run_id = str(request.query_params.get("run_id") or "").strip()
        if run_id:
            qs = qs.filter(run_id__icontains=run_id)
        data_ref = str(request.query_params.get("data_referencia_d1") or "").strip()
        if data_ref:
            qs = qs.filter(data_referencia_d1=data_ref)
        qs = qs.order_by("-created_at", "-id")
        page_size = _positive_int(request.query_params.get("page_size"), 20, 100)
        paginator = Paginator(qs, page_size)
        page = paginator.get_page(_positive_int(request.query_params.get("page"), 1))
        return Response({
            "count": paginator.count,
            "page": page.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "results": [_run_row(run) for run in page.object_list],
        })


class PlanValidationDetailView(ReplicacaoD1APIView):
    def get(self, request, run_id: str):
        try:
            return Response(build_plan_validation(run_id))
        except ReplicacaoD1Run.DoesNotExist:
            return Response({"detail": "Plano não encontrado."}, status=status.HTTP_404_NOT_FOUND)


class PlanProtocolListView(ReplicacaoD1APIView):
    def get(self, request, run_id: str):
        try:
            run = exclude_deleted_plans(ReplicacaoD1Run.objects.all()).get(run_id=run_id)
        except ReplicacaoD1Run.DoesNotExist:
            return Response({"detail": "Plano não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        qs = run.protocolos.all()
        workflow = str(request.query_params.get("workflow") or "").strip()
        if workflow:
            qs = qs.filter(workflow_config__iexact=workflow)
        matricula_tipo = str(request.query_params.get("matricula_tipo") or "").strip()
        if matricula_tipo:
            qs = qs.filter(matricula_tipo=matricula_tipo)
        selection_reason = str(request.query_params.get("selection_reason") or "").strip()
        if selection_reason == "retroativo":
            qs = qs.filter(selection_reason__startswith="retroativo:")
        elif selection_reason == "amostra_d1":
            qs = qs.filter(selection_reason="amostra_d1")
        query = str(request.query_params.get("q") or "").strip()
        if query:
            qs = qs.filter(protocolo__icontains=query)
        ordering_raw = str(request.query_params.get("ordering") or "").strip()
        descending = ordering_raw.startswith("-")
        ordering_key = ordering_raw[1:] if descending else ordering_raw
        ordering_fields = {
            "protocolo": "protocolo",
            "workflow_config": "workflow_config",
            "data_analise": "data_analise",
            "hora": "hora",
            "matricula_tipo": "matricula_tipo",
            "canal_destino": "canal_destino",
        }
        ordering_field = ordering_fields.get(ordering_key)
        if ordering_field:
            prefix = "-" if descending else ""
            qs = qs.order_by(f"{prefix}{ordering_field}", f"{prefix}pk")
        else:
            qs = qs.order_by("workflow_config", "protocolo", "pk")
        page_size = _positive_int(request.query_params.get("page_size"), 50, 500)
        paginator = Paginator(qs, page_size)
        page = paginator.get_page(_positive_int(request.query_params.get("page"), 1))
        fields = (
            "id", "protocolo", "protocolo_normalizado", "workflow_config", "workflow_d1",
            "data_analise", "hora", "canal_destino", "matricula_tipo", "selection_reason",
        )
        rows = list(page.object_list.values(*fields))
        return Response({
            "count": paginator.count,
            "page": page.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "results": rows,
        })


class PlanReviewAPIView(ReplicacaoD1APIView):
    permission_classes = [IsAuthenticated, portal_perm(R.PLANEJAMENTO_AUTOMACAO_APPROVE)]
    action = ""

    def post(self, request, run_id: str):
        try:
            revision = int(request.data.get("plan_revision"))
        except (TypeError, ValueError):
            return Response({"detail": "plan_revision inválido."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            run = review_plan(
                run_id,
                action=self.action,
                user=request.user,
                expected_plan_hash=str(request.data.get("plan_hash") or ""),
                expected_revision=revision,
                reason=str(request.data.get("reason") or ""),
            )
        except ReplicacaoD1Run.DoesNotExist:
            return Response({"detail": "Plano não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        except PlanImmutableError as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_run_row(run))


class PlanApproveView(PlanReviewAPIView):
    action = ReplicacaoD1PlanReview.ACTION_APPROVE


class PlanRejectView(PlanReviewAPIView):
    action = ReplicacaoD1PlanReview.ACTION_REJECT


class PlanDeleteView(ReplicacaoD1APIView):
    permission_classes = [
        IsAuthenticated,
        portal_perm(R.PLANEJAMENTO_AUTOMACAO_APPROVE)
        | portal_perm(R.PLANEJAMENTO_AUTOMACAO_CONFIGURE),
    ]

    def post(self, request, run_id: str):
        try:
            revision = int(request.data.get("plan_revision"))
        except (TypeError, ValueError):
            return Response({"detail": "plan_revision inválido."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            deletion = delete_plan(
                run_id,
                user=request.user,
                expected_plan_hash=str(request.data.get("plan_hash") or ""),
                expected_revision=revision,
                confirmation_run_id=str(request.data.get("confirmation_run_id") or ""),
                reason=str(request.data.get("reason") or ""),
            )
        except ReplicacaoD1Run.DoesNotExist:
            return Response({"detail": "Plano não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        except PlanDeletionBlockedError as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "deleted": True,
            "deletion_mode": "archived",
            "run_id": deletion.run_id,
            "deleted_at": deletion.deleted_at,
            "audit_id": deletion.pk,
        })


class PlanExportCsvView(ReplicacaoD1APIView):
    """Exportação sob demanda em memória; nenhum arquivo é persistido no servidor."""

    def get(self, request, run_id: str):
        try:
            run = exclude_deleted_plans(ReplicacaoD1Run.objects.all()).get(run_id=run_id)
        except ReplicacaoD1Run.DoesNotExist:
            return Response({"detail": "Plano não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["Protocolo", "Workflow", "Data da Análise", "Hora", "Canal", "Tipo matrícula"])
        for row in run.protocolos.order_by("workflow_config", "protocolo").values_list(
            "protocolo", "workflow_config", "data_analise", "hora", "canal_destino", "matricula_tipo"
        ).iterator(chunk_size=2000):
            writer.writerow(row)
        response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="plano-replicacao-d1-{run.run_id}.csv"'
        response["Cache-Control"] = "no-store"
        return response
