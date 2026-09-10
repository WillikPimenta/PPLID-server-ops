# -*- coding: utf-8 -*-
from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_exceptions import secure_error_payload

from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
from apps.common.models import BotDbSyncJob
from apps.falhas_criticas.permissions import CanSyncExcel, IsAuthenticatedPortal

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
UPLOAD_DIR = Path(settings.MEDIA_ROOT) / "falhas" / "imports"
UPLOAD_FILENAME = "latest.xlsx"


def _validate_upload(uploaded_file) -> str | None:
    if not uploaded_file:
        return "Nenhum arquivo enviado."
    name = (uploaded_file.name or "").lower()
    if not name.endswith(".xlsx"):
        return "Envie um arquivo .xlsx (FALHAS_CRITICAS_MANUAL.xlsx)."
    if uploaded_file.size <= 0:
        return "Arquivo vazio."
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        return "Arquivo muito grande (máximo 50 MB)."
    return None


class SyncUploadView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanSyncExcel]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        error = _validate_upload(uploaded_file)
        if error:
            return Response({"status": "error", "message": error}, status=status.HTTP_400_BAD_REQUEST)

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / UPLOAD_FILENAME
        with dest.open("wb") as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)

        try:
            job, created = enqueue_bot_db_sync(
                domain=BotDbSyncJob.DOMAIN_FALHAS_CRITICAS,
                source_path=str(dest),
                force=True,
                spawn=True,
            )
        except Exception as exc:
            return Response(
                secure_error_payload(
                    exc,
                    operation="falhas_criticas.sync_upload",
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
                "filename": uploaded_file.name,
                "message": (
                    "Arquivo salvo e sincronização enfileirada; o drain processará em background."
                    if created
                    else "Arquivo salvo; já havia sincronização pendente/em execução."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )
