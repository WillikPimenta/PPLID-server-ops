# -*- coding: utf-8 -*-
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_exceptions import secure_error_payload

from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
from apps.common.models import BotDbSyncJob
from apps.falhas_criticas.permissions import CanSyncExcel, IsAuthenticatedPortal


class SyncDatabaseView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanSyncExcel]

    def post(self, request):
        path = str(request.data.get("path") or "").strip()
        try:
            job, created = enqueue_bot_db_sync(
                domain=BotDbSyncJob.DOMAIN_FALHAS_CRITICAS,
                source_path=path,
                force=True,
                spawn=True,
            )
        except Exception as exc:
            return Response(
                secure_error_payload(
                    exc,
                    operation="falhas_criticas.sync",
                    message_field="message",
                    extra={"status": "error"},
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                "status": "queued",
                "queued": True,
                "created": created,
                "job_id": job.pk,
                "job_status": job.status,
                "message": (
                    "Sincronização enfileirada; o drain processará em background."
                    if created
                    else "Já havia sincronização pendente/em execução para esta fonte."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )
