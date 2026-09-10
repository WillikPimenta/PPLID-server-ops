# -*- coding: utf-8 -*-
"""Cancelamento seguro / fencing da carga retroativa."""
from __future__ import annotations

import hashlib
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_QUAL_GERENCIA, role_group_name
from apps.qualidade_operacional.models import QualidadeFalha, QualidadeImportBatch
from apps.qualidade_operacional.services.qualidade_import_cancel import (
    CancelConflict,
    conditional_batch_update,
    execute_cancel_rollback,
    request_cancel_qualidade_import,
)
from apps.qualidade_operacional.services.retroactive_import import (
    complete_upload,
    finalize_stale_qualidade_batches,
    init_retroactive_batch,
    receive_chunk,
    reconcile_stale_qualidade_batch,
    run_retroactive_import,
    validate_retroactive_batch,
)
from apps.qualidade_operacional.tests.test_retroactive_import import multi_month_falhas

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, QUALIDADE_RETRO_IMPORT_MAX_BYTES=50 * 1024 * 1024)
class QualidadeImportCancelTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)
        self.user = User.objects.create_user(
            username="quality_cancel",
            password="x",
            email="quality_cancel@test.local",
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_GERENCIA))
        self.user.groups.add(group)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.other = User.objects.create_user(
            username="no_perm_cancel",
            password="x",
            email="no_perm_cancel@test.local",
        )

    def _upload_content(self, content: bytes) -> QualidadeImportBatch:
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="retro_cancel.tsv",
            uploaded_by=self.user,
            expected_size=len(content),
            chunks_expected=1,
        )
        digest = hashlib.sha256(content).hexdigest()
        receive_chunk(
            batch,
            index=0,
            uploaded_file=SimpleUploadedFile("p0", content, content_type="application/octet-stream"),
            checksum=digest,
        )
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker"
        ):
            return complete_upload(batch)

    def test_cancel_during_uploading(self):
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="up.tsv",
            uploaded_by=self.user,
            expected_size=10,
            chunks_expected=1,
        )
        result, outcome = request_cancel_qualidade_import(batch.pk, self.user, reason="stop upload")
        self.assertEqual(outcome, "accepted")
        self.assertEqual(result.status, QualidadeImportBatch.STATUS_CANCELLED)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCELLED)
        self.assertIsNotNone(batch.cancel_requested_at)
        self.assertEqual(batch.cancelled_by_id, self.user.pk)
        from apps.qualidade_operacional.services.qualidade_import_cancel import batch_flags

        self.assertFalse(batch_flags(batch)["can_confirm"])
        self.assertTrue(batch_flags(batch)["is_terminal"])

    def test_cancel_validated_immediate(self):
        content = multi_month_falhas(months=[("15/01/2025", 2)])
        batch = self._upload_content(content)
        validate_retroactive_batch(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        result, outcome = request_cancel_qualidade_import(batch.pk, self.user)
        self.assertEqual(outcome, "accepted")
        self.assertEqual(result.status, QualidadeImportBatch.STATUS_CANCELLED)

    def test_cancel_during_validating_cooperative(self):
        content = multi_month_falhas(months=[("15/01/2025", 2)])
        batch = self._upload_content(content)
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_VALIDATING,
        )
        with patch(
            "apps.qualidade_operacional.services.qualidade_import_cancel.spawn_qualidade_import_worker",
            create=True,
        ):
            # spawn is imported inside request_cancel from retroactive_import
            with patch(
                "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker"
            ):
                result, outcome = request_cancel_qualidade_import(batch.pk, self.user, reason="stop val")
        self.assertEqual(outcome, "accepted")
        self.assertEqual(result.status, QualidadeImportBatch.STATUS_CANCEL_REQUESTED)
        validate_retroactive_batch(QualidadeImportBatch.objects.get(pk=batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCELLED)

    def test_cancel_idempotent(self):
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="idemp.tsv",
            uploaded_by=self.user,
            expected_size=10,
            chunks_expected=1,
        )
        request_cancel_qualidade_import(batch.pk, self.user)
        result, outcome = request_cancel_qualidade_import(batch.pk, self.user)
        self.assertEqual(outcome, "already_cancelled")
        self.assertEqual(result.status, QualidadeImportBatch.STATUS_CANCELLED)

    def test_cancel_completed_conflict(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="done.tsv",
            status=QualidadeImportBatch.STATUS_COMPLETED,
            uploaded_by=self.user,
        )
        with self.assertRaises(CancelConflict):
            request_cancel_qualidade_import(batch.pk, self.user)

    def test_cancel_restored_conflict(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="rest.tsv",
            status=QualidadeImportBatch.STATUS_RESTORED,
            uploaded_by=self.user,
        )
        with self.assertRaises(CancelConflict):
            request_cancel_qualidade_import(batch.pk, self.user)

    def test_cancel_api_without_permission(self):
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="noperm.tsv",
            uploaded_by=self.user,
            expected_size=10,
            chunks_expected=1,
        )
        client = APIClient()
        client.force_authenticate(self.other)
        response = client.post(f"/api/v1/qualidade/operacional/imports/{batch.pk}/cancel/", {})
        self.assertIn(response.status_code, {403, 401})

    def test_cancel_api_accepted_and_conflict(self):
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="api.tsv",
            uploaded_by=self.user,
            expected_size=10,
            chunks_expected=1,
        )
        response = self.client.post(
            f"/api/v1/qualidade/operacional/imports/{batch.pk}/cancel/",
            {"reason": "teste"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["batch"]["status"], "cancelled")
        self.assertTrue(response.data["batch"].get("is_terminal"))

        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_COMPLETED,
            finished_at=timezone.now(),
        )
        response2 = self.client.post(
            f"/api/v1/qualidade/operacional/imports/{batch.pk}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(response2.status_code, 409)

    def test_worker_respects_cancel_between_months(self):
        content = multi_month_falhas(
            months=[("15/01/2025", 2), ("10/02/2025", 2)]
        )
        batch = self._upload_content(content)
        validate_retroactive_batch(batch)
        batch.refresh_from_db()
        token = "worker-token-1"
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_PROCESSING,
            worker_token=token,
            progress_percent=5,
        )

        call_count = {"n": 0}
        original_backup = __import__(
            "apps.qualidade_operacional.services.retroactive_import", fromlist=["_backup_month_for"]
        )._backup_month_for

        def backup_then_cancel(b, competencia):
            call_count["n"] += 1
            path, prev = original_backup(b, competencia)
            if call_count["n"] == 1:
                QualidadeImportBatch.objects.filter(pk=b.pk).update(
                    status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
                    cancel_requested_at=timezone.now(),
                )
            return path, prev

        with patch(
            "apps.qualidade_operacional.services.retroactive_import._backup_month_for",
            side_effect=backup_then_cancel,
        ):
            with patch(
                "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker"
            ):
                run_retroactive_import(str(batch.pk), worker_token=token)

        batch.refresh_from_db()
        self.assertIn(
            batch.status,
            {
                QualidadeImportBatch.STATUS_CANCELLED,
                QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
                QualidadeImportBatch.STATUS_CANCELLING,
            },
        )
        # Janeiro pode ter sido importado; fevereiro não deve ter sido concluída após cancel
        feb = QualidadeFalha.objects.filter(
            data__gte="2025-02-01", data__lt="2025-03-01"
        ).count()
        # Se cancelou após 1º mês, fevereiro não foi substituída (0 na base de teste)
        self.assertEqual(feb, 0)

    def test_old_worker_cannot_overwrite_cancelled(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="fence.tsv",
            status=QualidadeImportBatch.STATUS_CANCELLED,
            worker_token="",
            uploaded_by=self.user,
        )
        updated = conditional_batch_update(
            batch.pk,
            expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
            require_token="old-token",
            status=QualidadeImportBatch.STATUS_COMPLETED,
            phase="should not apply",
        )
        self.assertEqual(updated, 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCELLED)

    def test_old_worker_cannot_overwrite_failed(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="fence2.tsv",
            status=QualidadeImportBatch.STATUS_FAILED,
            failure_code="STALE_HEARTBEAT",
            worker_token="",
            uploaded_by=self.user,
        )
        updated = conditional_batch_update(
            batch.pk,
            expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
            require_token="stale-token",
            status=QualidadeImportBatch.STATUS_COMPLETED,
        )
        self.assertEqual(updated, 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)

    def test_stale_cancel_requested_without_data_marks_cancelled(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="stale_cancel.tsv",
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            cancel_requested_at=timezone.now() - timedelta(hours=1),
            heartbeat_at=timezone.now() - timedelta(minutes=15),
            progress_percent=49,
            uploaded_by=self.user,
        )
        marked = finalize_stale_qualidade_batches(minutes=180)
        self.assertEqual(marked, 1)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCELLED)
        self.assertEqual(batch.worker_token, "")
        self.assertIn("worker parado", batch.phase.lower())

    def test_stale_cancel_requested_recent_heartbeat_not_finalized(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="live_cancel.tsv",
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            cancel_requested_at=timezone.now(),
            heartbeat_at=timezone.now(),
            uploaded_by=self.user,
        )
        marked = finalize_stale_qualidade_batches(minutes=180)
        self.assertEqual(marked, 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCEL_REQUESTED)

    def test_reconcile_stale_cancel_requested(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="get_reconcile.tsv",
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            cancel_requested_at=timezone.now() - timedelta(hours=6),
            heartbeat_at=timezone.now() - timedelta(minutes=20),
            progress_percent=49,
            uploaded_by=self.user,
        )
        reconciled = reconcile_stale_qualidade_batch(batch)
        self.assertEqual(reconciled.status, QualidadeImportBatch.STATUS_CANCELLED)
        from apps.qualidade_operacional.services.qualidade_import_cancel import batch_flags

        self.assertTrue(batch_flags(reconciled)["is_terminal"])

    def test_init_after_stale_cancel_releases_lock(self):
        QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="blocking.tsv",
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            cancel_requested_at=timezone.now() - timedelta(hours=6),
            heartbeat_at=timezone.now() - timedelta(minutes=20),
            uploaded_by=self.user,
        )
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="new.tsv",
            uploaded_by=self.user,
            expected_size=100,
            chunks_expected=1,
        )
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_UPLOADING)

    def test_stale_respects_recent_heartbeat(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="hb.tsv",
            status=QualidadeImportBatch.STATUS_VALIDATING,
            upload_complete=True,
            heartbeat_at=timezone.now(),
            uploaded_by=self.user,
        )
        marked = finalize_stale_qualidade_batches(minutes=180)
        self.assertEqual(marked, 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATING)

        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=5),
            worker_token="alive-token",
        )
        marked = finalize_stale_qualidade_batches(minutes=180)
        self.assertEqual(marked, 1)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)
        self.assertEqual(batch.failure_code, "STALE_HEARTBEAT")
        self.assertEqual(batch.worker_token, "")

        # Worker antigo não conclui
        updated = conditional_batch_update(
            batch.pk,
            expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
            require_token="alive-token",
            status=QualidadeImportBatch.STATUS_COMPLETED,
        )
        self.assertEqual(updated, 0)

    def test_rollback_quantity_mismatch_sets_rollback_failed(self):
        content = multi_month_falhas(months=[("15/01/2025", 2)])
        batch = self._upload_content(content)
        validate_retroactive_batch(batch)
        batch.refresh_from_db()
        token = "tok-rb"
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_PROCESSING,
            worker_token=token,
        )
        run_retroactive_import(str(batch.pk), worker_token=token)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)

        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            worker_token=token,
            cancel_requested_at=timezone.now(),
        )
        # Corrompe previous_rows no plano para forçar divergência
        plan = list(batch.month_plan or [])
        for row in plan:
            if row.get("status") == "completed":
                row["previous_rows"] = 999999
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(month_plan=plan)

        execute_cancel_rollback(str(batch.pk), worker_token=token)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_ROLLBACK_FAILED)
        self.assertEqual(batch.rollback_status, QualidadeImportBatch.ROLLBACK_FAILED)
        self.assertEqual(batch.failure_code, "ROLLBACK_FAILED")

    def test_cancel_processing_with_rollback(self):
        content = multi_month_falhas(months=[("15/01/2025", 2)])
        batch = self._upload_content(content)
        validate_retroactive_batch(batch)
        batch.refresh_from_db()
        token = "tok-ok"
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_PROCESSING,
            worker_token=token,
        )
        run_retroactive_import(str(batch.pk), worker_token=token)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)
        imported = QualidadeFalha.objects.filter(
            data__gte="2025-01-01", data__lt="2025-02-01"
        ).count()
        self.assertEqual(imported, 2)

        # Simula cancel após conclusão parcial: volta a processing com 1 mês done
        # (cenário de cancel mid-flight). Aqui testamos rollback via cancel_requested.
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            worker_token=token,
            cancel_requested_at=timezone.now(),
            rollback_status=QualidadeImportBatch.ROLLBACK_PENDING,
        )
        execute_cancel_rollback(str(batch.pk), worker_token=token)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCELLED)
        self.assertEqual(batch.rollback_status, QualidadeImportBatch.ROLLBACK_COMPLETED)
        self.assertEqual(
            QualidadeFalha.objects.filter(
                data__gte="2025-01-01", data__lt="2025-02-01"
            ).count(),
            0,
        )

    def test_validation_does_not_change_facts(self):
        before = QualidadeFalha.objects.count()
        content = multi_month_falhas(months=[("15/01/2025", 3)])
        batch = self._upload_content(content)
        validate_retroactive_batch(batch)
        self.assertEqual(QualidadeFalha.objects.count(), before)

    def test_chunks_kept_until_complete_upload(self):
        content = multi_month_falhas(months=[("15/01/2025", 1)])
        mid = max(1, len(content) // 2)
        parts = [content[:mid], content[mid:]]
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="chunks.tsv",
            uploaded_by=self.user,
            expected_size=len(content),
            chunks_expected=2,
        )
        for index, part in enumerate(parts):
            receive_chunk(
                batch,
                index=index,
                uploaded_file=SimpleUploadedFile(f"p{index}", part),
                checksum=hashlib.sha256(part).hexdigest(),
            )
        batch.refresh_from_db()
        self.assertEqual(batch.chunks_received, 2)
        self.assertTrue(batch.chunks.exists())
        # Cancel during upload deve manter possibilidade de diagnóstico (não apaga source)
        request_cancel_qualidade_import(batch.pk, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_CANCELLED)
        # Chunks podem permanecer (não removemos no cancel imediato de uploading)
        self.assertTrue(Path(self.media.name).exists())
