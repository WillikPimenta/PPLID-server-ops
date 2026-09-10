# -*- coding: utf-8 -*-
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
from apps.common.models import BotDbSyncJob
from apps.produtividade_case.constants import REPORT_FILA_ABERTA
from apps.produtividade_case.permissions import CanSyncCaseManager, CanViewCaseManager
from apps.produtividade_case.services.analitica_detail import serialize_fila_amostra
from apps.produtividade_case.services.fila_sync import (
    latest_successful_snapshot,
    latest_snapshot_with_items,
    serialize_painel,
    serialize_resumo,
    serialize_serie,
    serialize_status,
)


class CaseManagerAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewCaseManager]


class StatusView(CaseManagerAPIView):
    def get(self, request):
        return Response(serialize_status(latest_successful_snapshot()))


class FilaResumoView(CaseManagerAPIView):
    def get(self, request):
        return Response(serialize_resumo(latest_successful_snapshot()))


class FilaPainelView(CaseManagerAPIView):
    def get(self, request):
        snap = latest_snapshot_with_items() or latest_successful_snapshot()
        return Response(serialize_painel(snap))


class FilaSerieView(CaseManagerAPIView):
    def get(self, request):
        raw = request.query_params.get("hours") or "72"
        try:
            hours = int(raw)
        except (TypeError, ValueError):
            hours = 72
        return Response(serialize_serie(hours=hours))


class FilaAmostraView(CaseManagerAPIView):
    def get(self, request):
        idade_bucket = (request.query_params.get("idade_bucket") or "").strip() or None
        transaction_status = (
            request.query_params.get("transaction_status") or ""
        ).strip() or None
        workflow_origem = (request.query_params.get("workflow_origem") or "").strip() or None
        q = (request.query_params.get("q") or "").strip() or None
        ordering = (request.query_params.get("ordering") or "created_ts").strip()
        try:
            page = int(request.query_params.get("page") or 1)
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size") or 50)
        except (TypeError, ValueError):
            page_size = 50
        return Response(
            serialize_fila_amostra(
                idade_bucket=idade_bucket,
                transaction_status=transaction_status,
                workflow_origem=workflow_origem,
                q=q,
                ordering=ordering,
                page=page,
                page_size=page_size,
            )
        )


class SyncView(APIView):
    permission_classes = [IsAuthenticated, CanSyncCaseManager]

    def post(self, request):
        force = request.data.get("force") in (True, "true", "1", 1)
        source_path = str(request.data.get("source_path") or "").strip()
        if not source_path:
            last = latest_successful_snapshot()
            if last and last.source_file:
                source_path = last.source_file
        try:
            job, created = enqueue_bot_db_sync(
                domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE_CASE,
                source_path=source_path,
                report_type=REPORT_FILA_ABERTA,
                force=force,
                spawn=True,
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "queued": True,
                "created": created,
                "job_id": job.pk,
                "job_status": job.status,
                "report_type": REPORT_FILA_ABERTA,
                "message": (
                    "Sincronização da fila Case enfileirada."
                    if created
                    else "Já havia sincronização pendente/em execução para esta fonte."
                ),
                **serialize_status(latest_successful_snapshot()),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class SyncJobStatusView(APIView):
    """Poll do job bot→DB da fila Case (sem exigir perm de Automações)."""

    permission_classes = [IsAuthenticated, CanSyncCaseManager]

    def get(self, request, job_id: int):
        job = (
            BotDbSyncJob.objects.filter(
                pk=job_id,
                domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE_CASE,
            )
            .only(
                "id",
                "status",
                "message",
                "report_type",
                "created_at",
                "started_at",
                "finished_at",
            )
            .first()
        )
        if job is None:
            return Response(
                {"detail": "Job não encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        status_labels = dict(BotDbSyncJob.STATUS_CHOICES)
        return Response(
            {
                "id": job.pk,
                "status": job.status,
                "status_label": status_labels.get(job.status, job.status),
                "report_type": job.report_type or "",
                "message": (job.message or "")[:500],
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
        )
