# -*- coding: utf-8 -*-
"""API import Identificação dos Processos (Megazord)."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_exceptions import PUBLIC_INTERNAL_ERROR

from apps.access.permission_classes import portal_perm
from apps.access.registry import PLANEJAMENTO_MEGAZORD_VIEW
from apps.dimensoes_processos.models import IdentificacaoProcessosImportRun, IdentificacaoProcessosUpload
from apps.dimensoes_processos.services.identificacao_processos.import_job import schedule_import_run
from apps.dimensoes_processos.services.identificacao_processos.preflight import run_preflight
from apps.dimensoes_processos.services.identificacao_processos.upload_staging import (
    UploadValidationError,
    create_upload,
    get_active_upload,
    has_running_import,
)

MegazordPerm = portal_perm(PLANEJAMENTO_MEGAZORD_VIEW)


def _serialize_upload(upload: IdentificacaoProcessosUpload) -> dict:
    return {
        "token": str(upload.token),
        "status": upload.status,
        "original_name": upload.original_name,
        "sha256": upload.sha256,
        "size_bytes": upload.size_bytes,
        "created_at": upload.created_at.isoformat(),
        "expires_at": upload.expires_at.isoformat(),
        "preflight": upload.preflight_summary or None,
    }


def _serialize_run(run: IdentificacaoProcessosImportRun) -> dict:
    return {
        "id": run.pk,
        "mode": run.mode,
        "import_scope": run.import_scope,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "summary": run.summary or {},
        "message": run.message,
    }


class IdentificacaoProcessosUploadView(APIView):
    permission_classes = [MegazordPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "Informe o arquivo .xlsx em file."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            record = create_upload(user=request.user, upload=upload)
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"ok": True, "upload": _serialize_upload(record)}, status=status.HTTP_201_CREATED)


class IdentificacaoProcessosUploadDetailView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request, token):
        try:
            upload = get_active_upload(str(token), user=request.user)
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True, "upload": _serialize_upload(upload)})

    def delete(self, request, token):
        try:
            upload = IdentificacaoProcessosUpload.objects.get(token=token, created_by=request.user)
        except (IdentificacaoProcessosUpload.DoesNotExist, ValueError):
            return Response({"detail": "Upload não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        if upload.file:
            upload.file.delete(save=False)
        upload.delete()
        return Response({"ok": True})


class IdentificacaoProcessosPreflightView(APIView):
    permission_classes = [MegazordPerm]

    def post(self, request):
        token = request.data.get("token") or request.query_params.get("token")
        import_scope = (
            request.data.get("import_scope")
            or request.query_params.get("import_scope")
            or IdentificacaoProcessosImportRun.SCOPE_FULL
        )
        if not token:
            return Response({"detail": "Informe token."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            upload = get_active_upload(str(token), user=request.user)
            preflight = run_preflight(
                upload.file.path,
                file_name=upload.original_name,
                import_scope=import_scope,
            )
            upload.preflight_summary = preflight.as_dict()
            upload.save(update_fields=["preflight_summary"])
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except FileNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"ok": preflight.ok, "preflight": preflight.as_dict(), "upload": _serialize_upload(upload)})


class IdentificacaoProcessosImportView(APIView):
    permission_classes = [MegazordPerm]

    def post(self, request):
        if has_running_import(user=request.user):
            return Response(
                {"detail": "Já existe uma importação em andamento."},
                status=status.HTTP_409_CONFLICT,
            )

        token = request.data.get("token") or request.query_params.get("token")
        mode = (request.data.get("mode") or IdentificacaoProcessosImportRun.MODE_SYNC).strip()
        import_scope = (
            request.data.get("import_scope")
            or IdentificacaoProcessosImportRun.SCOPE_FULL
        ).strip()
        confirm = request.data.get("confirm") in {True, "true", "1", 1}

        if not token:
            return Response({"detail": "Informe token."}, status=status.HTTP_400_BAD_REQUEST)
        if mode not in {IdentificacaoProcessosImportRun.MODE_SYNC, IdentificacaoProcessosImportRun.MODE_REPLACE}:
            return Response({"detail": "Modo inválido. Use sync ou replace."}, status=status.HTTP_400_BAD_REQUEST)
        valid_scopes = {
            IdentificacaoProcessosImportRun.SCOPE_CADASTROS,
            IdentificacaoProcessosImportRun.SCOPE_PROJECAO_SLA,
            IdentificacaoProcessosImportRun.SCOPE_FULL,
        }
        if import_scope not in valid_scopes:
            return Response(
                {"detail": "Escopo inválido. Use cadastros, projecao_sla ou full."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if mode == IdentificacaoProcessosImportRun.MODE_REPLACE and import_scope != IdentificacaoProcessosImportRun.SCOPE_FULL:
            return Response(
                {"detail": "mode=replace exige import_scope=full."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not confirm:
            return Response({"detail": "Confirme a importação com confirm=true."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            upload = get_active_upload(str(token), user=request.user)
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if not upload.preflight_summary:
            return Response(
                {"detail": "Execute a análise (preflight) antes de importar."},
                status=status.HTTP_409_CONFLICT,
            )
        if not upload.preflight_summary.get("ok", False):
            return Response(
                {"detail": "A análise encontrou erros. Corrija o arquivo e analise novamente."},
                status=status.HTTP_409_CONFLICT,
            )

        run = IdentificacaoProcessosImportRun.objects.create(
            upload=upload,
            created_by=request.user,
            mode=mode,
            import_scope=import_scope,
            status=IdentificacaoProcessosImportRun.STATUS_RUNNING,
        )

        transaction.on_commit(lambda: schedule_import_run(run.pk))
        return Response(
            {"ok": True, "run": _serialize_run(run), "message": "Importação iniciada."},
            status=status.HTTP_202_ACCEPTED,
        )


class IdentificacaoProcessosImportRunDetailView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request, run_id):
        try:
            run = IdentificacaoProcessosImportRun.objects.get(pk=run_id, created_by=request.user)
        except IdentificacaoProcessosImportRun.DoesNotExist:
            return Response({"detail": "Importação não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True, "run": _serialize_run(run)})
