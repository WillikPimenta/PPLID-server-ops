# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from django.utils import timezone

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permission_classes import portal_perm
from apps.access.registry import QUAL_OPERACIONAL_SYNC
from apps.qualidade_operacional.models import QualidadeImportBatch
from apps.qualidade_operacional.services.monthly_import import (
    MonthlyImportError,
    save_uploaded_tsv,
    serialize_batch,
    start_import,
    start_restore,
    validate_batch,
)
from apps.qualidade_operacional.services.retroactive_import import (
    complete_upload,
    init_retroactive_batch,
    receive_chunk,
    reconcile_stale_qualidade_batch,
    serialize_batch_extended,
)
from apps.qualidade_operacional.services.qualidade_import_cancel import (
    CancelConflict,
    request_cancel_qualidade_import,
)

SyncPerm = portal_perm(QUAL_OPERACIONAL_SYNC)


def _parse_competencia(raw: str) -> date:
    text = str(raw or "").strip()
    try:
        parsed = date.fromisoformat(f"{text[:7]}-01")
    except ValueError as exc:
        raise MonthlyImportError("Informe a competência no formato AAAA-MM.") from exc
    if text != parsed.strftime("%Y-%m"):
        raise MonthlyImportError("Informe a competência no formato AAAA-MM.")
    return parsed


def _serialize(batch: QualidadeImportBatch) -> dict:
    if batch.import_mode == QualidadeImportBatch.MODE_RETROACTIVE:
        return serialize_batch_extended(batch)
    return serialize_batch(batch)


class MonthlyImportListView(APIView):
    permission_classes = [SyncPerm]

    def get(self, request):
        batches = QualidadeImportBatch.objects.select_related("uploaded_by")[:20]
        return Response({"ok": True, "results": [_serialize(batch) for batch in batches]})

    def post(self, request):
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response({"ok": False, "message": "Selecione um arquivo TSV."}, status=status.HTTP_400_BAD_REQUEST)
        kind = str(request.data.get("kind") or "").strip()
        try:
            competencia = _parse_competencia(request.data.get("competencia"))
            if kind not in {QualidadeImportBatch.KIND_AUDITADOS, QualidadeImportBatch.KIND_FALHAS}:
                raise MonthlyImportError("Selecione Auditorias ou Falhas.")
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user if getattr(request.user, "is_authenticated", False) else None
        batch = QualidadeImportBatch.objects.create(
            kind=kind,
            import_mode=QualidadeImportBatch.MODE_MONTHLY,
            competencia=competencia,
            filename=str(uploaded.name or "upload.tsv")[:255],
            uploaded_by=user,
            phase="Recebendo arquivo",
            progress_percent=10,
        )
        try:
            save_uploaded_tsv(batch, uploaded)
            validate_batch(batch)
        except Exception as exc:
            batch.status = QualidadeImportBatch.STATUS_FAILED
            batch.phase = "Validação falhou"
            batch.progress_percent = 100
            batch.failure_detail = str(exc)[:1000]
            batch.finished_at = timezone.now()
            batch.save()
        response_status = status.HTTP_201_CREATED if batch.status == QualidadeImportBatch.STATUS_VALIDATED else status.HTTP_400_BAD_REQUEST
        return Response(
            {"ok": batch.status == QualidadeImportBatch.STATUS_VALIDATED, "batch": _serialize(batch)},
            status=response_status,
        )


class MonthlyImportDetailView(APIView):
    permission_classes = [SyncPerm]

    def get_object(self, batch_id):
        try:
            return QualidadeImportBatch.objects.get(pk=batch_id)
        except QualidadeImportBatch.DoesNotExist as exc:
            raise MonthlyImportError("Lote não encontrado.") from exc

    def get(self, request, batch_id):
        try:
            batch = self.get_object(batch_id)
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        if batch.import_mode == QualidadeImportBatch.MODE_RETROACTIVE:
            batch = reconcile_stale_qualidade_batch(batch)
        return Response({"ok": True, "batch": _serialize(batch)})


class MonthlyImportConfirmView(MonthlyImportDetailView):
    def post(self, request, batch_id):
        try:
            batch = self.get_object(batch_id)
            start_import(batch)
            batch.refresh_from_db()
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"ok": True, "batch": _serialize(batch)}, status=status.HTTP_202_ACCEPTED)


class MonthlyImportRestoreView(MonthlyImportDetailView):
    def post(self, request, batch_id):
        try:
            batch = self.get_object(batch_id)
            start_restore(batch)
            batch.refresh_from_db()
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"ok": True, "batch": _serialize(batch)}, status=status.HTTP_202_ACCEPTED)


class RetroactiveImportInitView(APIView):
    permission_classes = [SyncPerm]

    def post(self, request):
        user = request.user if getattr(request.user, "is_authenticated", False) else None
        try:
            batch = init_retroactive_batch(
                kind=str(request.data.get("kind") or "").strip(),
                filename=str(request.data.get("filename") or "upload.tsv"),
                uploaded_by=user,
                expected_size=int(request.data.get("expected_size") or 0),
                expected_checksum=str(request.data.get("expected_checksum") or ""),
                chunks_expected=int(request.data.get("chunks_expected") or 0),
            )
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except (TypeError, ValueError) as exc:
            return Response({"ok": False, "message": f"Parâmetros inválidos: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "batch": serialize_batch_extended(batch)}, status=status.HTTP_201_CREATED)


class RetroactiveImportChunkView(MonthlyImportDetailView):
    def post(self, request, batch_id):
        uploaded = request.FILES.get("chunk") or request.FILES.get("file")
        if uploaded is None:
            return Response({"ok": False, "message": "Envie o bloco (chunk)."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            batch = self.get_object(batch_id)
            index = int(request.data.get("index"))
            chunk = receive_chunk(
                batch,
                index=index,
                uploaded_file=uploaded,
                checksum=str(request.data.get("checksum") or ""),
            )
            batch.refresh_from_db()
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except (TypeError, ValueError):
            return Response({"ok": False, "message": "Índice de bloco inválido."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "ok": True,
                "chunk": {
                    "index": chunk.index,
                    "size": chunk.size,
                    "checksum_sha256": chunk.checksum_sha256,
                },
                "batch": serialize_batch_extended(batch),
            }
        )


class RetroactiveImportCompleteView(MonthlyImportDetailView):
    def post(self, request, batch_id):
        try:
            batch = self.get_object(batch_id)
            batch = complete_upload(batch)
        except MonthlyImportError as exc:
            return Response({"ok": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "batch": serialize_batch_extended(batch)}, status=status.HTTP_202_ACCEPTED)


class QualidadeImportCancelView(MonthlyImportDetailView):
    """POST /imports/<uuid>/cancel/ — cancelamento cooperativo da carga retroativa."""

    def post(self, request, batch_id):
        reason = str(
            request.data.get("reason")
            or request.data.get("cancellation_reason")
            or ""
        )
        user = request.user if getattr(request.user, "is_authenticated", False) else None
        try:
            batch, outcome = request_cancel_qualidade_import(
                batch_id, user, reason=reason
            )
        except CancelConflict as exc:
            try:
                batch = self.get_object(batch_id)
            except MonthlyImportError:
                return Response(
                    {"ok": False, "message": str(exc)},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"ok": False, "message": str(exc), "batch": _serialize(batch)},
                status=status.HTTP_409_CONFLICT,
            )
        except MonthlyImportError as exc:
            if "não encontrado" in str(exc).lower():
                return Response(
                    {"ok": False, "message": str(exc)},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"ok": False, "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = {"ok": True, "batch": _serialize(batch), "outcome": outcome}
        if outcome == "accepted" and batch.status in {
            QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            QualidadeImportBatch.STATUS_CANCELLING,
        }:
            return Response(payload, status=status.HTTP_202_ACCEPTED)
        if outcome == "accepted" and batch.status == QualidadeImportBatch.STATUS_CANCELLED:
            return Response(payload, status=status.HTTP_200_OK)
        # already_cancelled / pending
        return Response(payload, status=status.HTTP_200_OK)
