# -*- coding: utf-8 -*-
from rest_framework import status
from rest_framework.response import Response

from rest_framework.permissions import IsAuthenticated

from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
from apps.common.models import BotDbSyncJob
from apps.produtividade.permissions import CanSyncProductivity
from apps.produtividade.services.sync_status import get_sync_status
from apps.produtividade.views.base import ProdutividadeAPIView


class SyncView(ProdutividadeAPIView):
    permission_classes = [IsAuthenticated, CanSyncProductivity]

    def post(self, request):
        force = request.data.get("force") in (True, "true", "1", 1)
        try:
            job, created = enqueue_bot_db_sync(
                domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
                source_path="",
                force=force,
                spawn=True,
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        status_payload = get_sync_status()
        return Response(
            {
                "queued": True,
                "created": created,
                "job_id": job.pk,
                "job_status": job.status,
                "message": (
                    "Sincronização enfileirada; o drain processará em background."
                    if created
                    else "Já havia sincronização pendente/em execução para esta fonte."
                ),
                **status_payload,
            },
            status=status.HTTP_202_ACCEPTED,
        )
