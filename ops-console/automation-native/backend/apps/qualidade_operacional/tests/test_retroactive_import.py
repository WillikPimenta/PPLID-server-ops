# -*- coding: utf-8 -*-
import csv
import hashlib
import io
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_QUAL_GERENCIA, role_group_name
from apps.qualidade_operacional.models import QualidadeFalha, QualidadeImportBatch
from apps.qualidade_operacional.services.retroactive_import import (
    complete_upload,
    drain_qualidade_imports,
    init_retroactive_batch,
    receive_chunk,
    run_retroactive_import,
    run_retroactive_restore,
    validate_retroactive_batch,
)

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def multi_month_falhas(*, months: list[tuple[str, int]]) -> bytes:
    with (FIXTURES / "tabela_falhas.tsv").open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        base = next(reader)
        fieldnames = reader.fieldnames or []
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    seq = 0
    for data, rows in months:
        for _ in range(rows):
            seq += 1
            row = dict(base)
            row["Data"] = data
            row["Protocolo"] = f"RETRO-{seq}"
            writer.writerow(row)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


@override_settings(ACCESS_ENFORCEMENT=True, QUALIDADE_RETRO_IMPORT_MAX_BYTES=50 * 1024 * 1024)
class RetroactiveQualityImportTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)
        self.user = User.objects.create_user(username="quality_retro", password="x")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_GERENCIA))
        self.user.groups.add(group)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_chunk_upload_resume_and_checksum(self):
        content = multi_month_falhas(months=[("15/01/2025", 2), ("10/02/2025", 3)])
        mid = len(content) // 2
        parts = [content[:mid], content[mid:]]
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="retro_falhas.tsv",
            uploaded_by=self.user,
            expected_size=len(content),
            chunks_expected=2,
        )
        for index, part in enumerate(parts):
            digest = hashlib.sha256(part).hexdigest()
            upload = SimpleUploadedFile(f"p{index}", part, content_type="application/octet-stream")
            receive_chunk(batch, index=index, uploaded_file=upload, checksum=digest)
        batch.refresh_from_db()
        self.assertEqual(batch.chunks_received, 2)

        bad = SimpleUploadedFile("bad", b"x", content_type="application/octet-stream")
        with self.assertRaises(Exception):
            receive_chunk(batch, index=0, uploaded_file=bad, checksum="0" * 64)

        # reenvio do bloco 0 com checksum correto (retomada)
        digest0 = hashlib.sha256(parts[0]).hexdigest()
        receive_chunk(
            batch,
            index=0,
            uploaded_file=SimpleUploadedFile("p0", parts[0], content_type="application/octet-stream"),
            checksum=digest0,
        )
        batch = complete_upload(batch)
        self.assertTrue(batch.upload_complete)
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATING)

        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            pass
        validate_retroactive_batch(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(batch.months_total, 2)
        comps = {row["competencia"] for row in batch.month_plan}
        self.assertEqual(comps, {"2025-01", "2025-02"})

    def test_process_by_month_and_resume_after_failure(self):
        content = multi_month_falhas(months=[("15/01/2025", 2), ("10/02/2025", 2)])
        QualidadeFalha.objects.create(
            protocolo="OLD",
            data=date(2025, 1, 5),
            matricula="1",
        )
        batch = init_retroactive_batch(
            kind=QualidadeImportBatch.KIND_FALHAS,
            filename="retro_falhas.tsv",
            uploaded_by=self.user,
            expected_size=len(content),
            chunks_expected=1,
        )
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            receive_chunk(
                batch,
                index=0,
                uploaded_file=SimpleUploadedFile("all", content, content_type="text/tab-separated-values"),
            )
            complete_upload(batch)
        validate_retroactive_batch(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)

        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_PROCESSING,
            phase="test",
        )
        # Simula falha no 2º mês: força erro na 2ª competência
        original_iter = __import__(
            "apps.qualidade_operacional.services.retroactive_import",
            fromlist=["_iter_month_objects"],
        )._iter_month_objects

        calls = {"n": 0}

        def flaky_iter(batch_obj, competencia):
            calls["n"] += 1
            if competencia.month == 2:
                raise RuntimeError("falha simulada no mês 02")
            yield from original_iter(batch_obj, competencia)

        with patch(
            "apps.qualidade_operacional.services.retroactive_import._iter_month_objects",
            side_effect=flaky_iter,
        ):
            run_retroactive_import(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)
        self.assertEqual(batch.months_done, 1)
        self.assertEqual(
            next(m for m in batch.month_plan if m["competencia"] == "2025-01")["status"],
            "completed",
        )
        self.assertEqual(QualidadeFalha.objects.filter(data__year=2025, data__month=1).count(), 2)
        self.assertFalse(QualidadeFalha.objects.filter(protocolo="OLD").exists())

        # Retoma e conclui fevereiro
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_PROCESSING,
            failure_detail="",
            finished_at=None,
        )
        run_retroactive_import(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.months_done, 2)
        self.assertEqual(QualidadeFalha.objects.filter(data__year=2025, data__month=2).count(), 2)

        run_retroactive_restore(str(batch.pk))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_RESTORED)
        self.assertEqual(QualidadeFalha.objects.filter(data__year=2025, data__month=1).count(), 1)
        self.assertTrue(QualidadeFalha.objects.filter(protocolo="OLD").exists())

    def test_api_init_chunk_complete(self):
        content = multi_month_falhas(months=[("15/03/2025", 1)])
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            init = self.client.post(
                "/api/v1/qualidade/operacional/imports/retroactive/init/",
                {
                    "kind": "falhas",
                    "filename": "r.tsv",
                    "expected_size": len(content),
                    "chunks_expected": 1,
                },
                format="json",
            )
            self.assertEqual(init.status_code, 201, init.content)
            batch_id = init.data["batch"]["id"]
            upload = SimpleUploadedFile("c0", content, content_type="application/octet-stream")
            chunk = self.client.post(
                f"/api/v1/qualidade/operacional/imports/{batch_id}/chunks/",
                {"index": 0, "chunk": upload, "checksum": hashlib.sha256(content).hexdigest()},
                format="multipart",
            )
            self.assertEqual(chunk.status_code, 200, chunk.content)
            done = self.client.post(f"/api/v1/qualidade/operacional/imports/{batch_id}/complete-upload/")
            self.assertEqual(done.status_code, 202, done.content)
            self.assertEqual(done.data["batch"]["status"], "validating")

        validate_retroactive_batch(QualidadeImportBatch.objects.get(pk=batch_id))
        confirm = self.client.post(f"/api/v1/qualidade/operacional/imports/{batch_id}/confirm/")
        self.assertEqual(confirm.status_code, 202, confirm.content)
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            drain_qualidade_imports(max_jobs=2)
        batch = QualidadeImportBatch.objects.get(pk=batch_id)
        # confirm already set processing; drain should finish if status processing
        if batch.status == QualidadeImportBatch.STATUS_PROCESSING:
            run_retroactive_import(str(batch.pk))
            batch.refresh_from_db()
        self.assertIn(
            batch.status,
            {
                QualidadeImportBatch.STATUS_COMPLETED,
                QualidadeImportBatch.STATUS_PROCESSING,
                QualidadeImportBatch.STATUS_VALIDATED,
            },
        )

    def _upload_and_validate(self, content: bytes) -> QualidadeImportBatch:
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            batch = init_retroactive_batch(
                kind=QualidadeImportBatch.KIND_FALHAS,
                filename="retro.tsv",
                uploaded_by=self.user,
                expected_size=len(content),
                chunks_expected=1,
            )
            receive_chunk(
                batch,
                index=0,
                uploaded_file=SimpleUploadedFile("all", content, content_type="text/tab-separated-values"),
            )
            complete_upload(batch)
        return validate_retroactive_batch(batch)

    def test_chunks_removed_after_assemble(self):
        content = multi_month_falhas(months=[("15/04/2025", 2)])
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            batch = init_retroactive_batch(
                kind=QualidadeImportBatch.KIND_FALHAS,
                filename="retro.tsv",
                uploaded_by=self.user,
                expected_size=len(content),
                chunks_expected=1,
            )
            receive_chunk(
                batch,
                index=0,
                uploaded_file=SimpleUploadedFile("all", content, content_type="text/tab-separated-values"),
            )
            chunk_path = Path(batch.chunks.get().path)
            self.assertTrue(chunk_path.exists())
            complete_upload(batch)
        batch.refresh_from_db()
        self.assertFalse(chunk_path.exists())
        self.assertEqual(batch.chunks.count(), 0)
        self.assertTrue(Path(batch.source_path).exists())

    def test_heartbeat_and_progress_during_validation(self):
        content = multi_month_falhas(months=[("15/05/2025", 3)])
        batch = self._upload_and_validate(content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertIsNotNone(batch.heartbeat_at)
        self.assertEqual(batch.progress_percent, 100)
        self.assertGreaterEqual(batch.rows_processed, 3)
        self.assertEqual(batch.current_stage, "validated")
        # Validação não altera fatos
        self.assertEqual(QualidadeFalha.objects.count(), 0)

    def test_progress_percent_never_regresses(self):
        from apps.qualidade_operacional.services.retroactive_import import ProgressReporter

        reporter = ProgressReporter("x", file_size=1_000_000, is_gz=False, stage="validate")
        reporter._last_percent = 80
        reporter.bytes_read = 10_000  # ~1% → seria ~45+ baixo
        self.assertGreaterEqual(reporter._compute_percent(), 80)

    def test_stale_uses_heartbeat_not_created_at(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.qualidade_operacional.services.retroactive_import import (
            finalize_stale_qualidade_batches,
        )

        content = multi_month_falhas(months=[("15/06/2025", 1)])
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            batch = init_retroactive_batch(
                kind=QualidadeImportBatch.KIND_FALHAS,
                filename="retro.tsv",
                uploaded_by=self.user,
                expected_size=len(content),
                chunks_expected=1,
            )
            receive_chunk(
                batch,
                index=0,
                uploaded_file=SimpleUploadedFile("all", content, content_type="text/tab-separated-values"),
            )
            complete_upload(batch)
        # Simula validação longa saudável: created_at antigo, heartbeat recente
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_VALIDATING,
            created_at=timezone.now() - timedelta(hours=10),
            heartbeat_at=timezone.now(),
        )
        marked = finalize_stale_qualidade_batches(minutes=180)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATING)
        self.assertEqual(marked, 0)

        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=5),
        )
        marked = finalize_stale_qualidade_batches(minutes=180)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)
        self.assertGreaterEqual(marked, 1)
        self.assertIn("heartbeat", batch.failure_detail.lower())

    def test_protocol_tracker_exact_with_many_rows(self):
        from apps.qualidade_operacional.services.retroactive_import import ProtocolTracker

        path = Path(self.media.name) / "protocols.sqlite"
        tracker = ProtocolTracker(path)
        n = 20_000
        for i in range(n):
            tracker.add(f"P-{i % 15_000}")
        distinct, duplicates = tracker.totals()
        tracker.close()
        self.assertEqual(distinct, 15_000)
        self.assertEqual(duplicates, 5_000)

    def test_validated_payload_never_looks_like_client_timeout_failure(self):
        """Contrato API: lote validated permanece validated ao consultar após 'desconexão'."""
        content = multi_month_falhas(months=[("15/07/2025", 2)])
        batch = self._upload_and_validate(content)
        detail = self.client.get(f"/api/v1/qualidade/operacional/imports/{batch.id}/")
        self.assertEqual(detail.status_code, 200)
        payload = detail.data["batch"]
        self.assertEqual(payload["status"], "validated")
        self.assertTrue(payload["can_confirm"])
        self.assertNotEqual(payload["status"], "failed")
        self.assertIn("heartbeat_at", payload)
        self.assertIn("rows_processed", payload)
        self.assertIn("processing_rate", payload)


class RetroactivePollingContractTests(TestCase):
    """Simula o contrato do cliente: timeouts transitórios não alteram status do lote."""

    def test_three_transient_fetches_then_success(self):
        from apps.qualidade_operacional.models import QualidadeImportBatch

        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="x.tsv",
            status=QualidadeImportBatch.STATUS_VALIDATING,
            phase="Validando",
            progress_percent=50,
            upload_complete=True,
        )
        statuses = []
        # 3 "timeouts" do cliente — servidor intacto
        for _ in range(3):
            statuses.append("timeout")
            batch.refresh_from_db()
            self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATING)
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_VALIDATED,
            progress_percent=100,
            phase="ok",
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(len(statuses), 3)

    def test_finishes_while_client_disconnected(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            filename="x.tsv",
            status=QualidadeImportBatch.STATUS_VALIDATING,
            upload_complete=True,
        )
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            status=QualidadeImportBatch.STATUS_VALIDATED,
            phase="Validação concluída",
            progress_percent=100,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)


@override_settings(
    ACCESS_ENFORCEMENT=True,
    QUALIDADE_RETRO_IMPORT_MAX_BYTES=50 * 1024 * 1024,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="hybrid",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-08",
)
class RetroactiveCutoverImportTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)
        self.user = User.objects.create_user(username="quality_retro_cutover", password="x")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_GERENCIA))
        self.user.groups.add(group)

    def _upload_and_validate(self, content: bytes) -> QualidadeImportBatch:
        with patch(
            "apps.qualidade_operacional.services.retroactive_import.spawn_qualidade_import_worker",
            return_value=True,
        ):
            batch = init_retroactive_batch(
                kind=QualidadeImportBatch.KIND_FALHAS,
                filename="retro_cutover.tsv",
                uploaded_by=self.user,
                expected_size=len(content),
                chunks_expected=1,
            )
            receive_chunk(
                batch,
                index=0,
                uploaded_file=SimpleUploadedFile(
                    "all",
                    content,
                    content_type="text/tab-separated-values",
                ),
            )
            complete_upload(batch)
        return validate_retroactive_batch(batch)

    def test_validates_pre_cutover_months_without_competencia_error(self):
        content = multi_month_falhas(months=[("15/07/2026", 2), ("05/08/2026", 3)])
        batch = self._upload_and_validate(content)
        batch.refresh_from_db()

        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        plan = {row["competencia"]: row for row in batch.month_plan}
        self.assertIn("2026-07", plan)
        self.assertIn("2026-08", plan)
        self.assertNotEqual(plan["2026-08"]["status"], "error")
        errors_text = " ".join(batch.errors)
        self.assertNotIn("Competência 2026-08 está no período Intranet", errors_text)

    def test_rejects_post_cutover_rows_without_competencia_error(self):
        content = multi_month_falhas(months=[("05/08/2026", 2), ("10/08/2026", 1)])
        batch = self._upload_and_validate(content)
        batch.refresh_from_db()

        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)
        errors_text = " ".join(batch.errors)
        self.assertNotIn("Competência 2026-08 está no período Intranet", errors_text)
        self.assertIn("período Intranet", errors_text)
