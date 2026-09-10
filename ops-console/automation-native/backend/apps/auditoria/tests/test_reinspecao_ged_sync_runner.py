from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from apps.auditoria.services.reinspecao_ged_sync_runner import run_sync_with_audit
from apps.common.models import BotDataArtifact, BotDataIngestion, BotDbSyncJob


def _sync_result(*, created: int = 2, parsed: int = 3, filtered: int = 1) -> dict:
    return {
        "created": created,
        "skipped_existing": parsed - created,
        "skipped_finalized_ged": 0,
        "skipped_filtered": filtered,
        "total_parsed": parsed,
    }


class ReinspecaoGedSyncRunnerAuditTests(TestCase):
    def setUp(self):
        self.source = Path(f"{self._testMethodName}.csv")
        self.source.write_text("Protocolo;Data de resposta\n1;-\n", encoding="utf-8")

    def tearDown(self):
        self.source.unlink(missing_ok=True)
        for sibling in self.source.parent.glob(f"{self.source.stem}-copia*.csv"):
            sibling.unlink(missing_ok=True)

    @patch(
        "apps.auditoria.services.reinspecao_ged_sync_runner.sync_reinspecao_ged_to_db",
        return_value=_sync_result(),
    )
    def test_success_records_artifact_hash_rows_and_finished_attempt(self, mock_sync):
        success, message, skipped = run_sync_with_audit(path=str(self.source))

        self.assertTrue(success)
        self.assertFalse(skipped)
        self.assertIn("2 protocolo(s) inserido(s)", message)
        mock_sync.assert_called_once_with(path=self.source)
        artifact = BotDataArtifact.objects.get()
        ingestion = BotDataIngestion.objects.get()
        self.assertEqual(len(artifact.content_sha256), 64)
        self.assertFalse(artifact.legacy_unverified)
        self.assertEqual(ingestion.artifact, artifact)
        self.assertEqual(ingestion.attempt_number, 1)
        self.assertEqual(ingestion.status, BotDataIngestion.STATUS_COMPLETED)
        self.assertEqual(ingestion.rows_read, 4)
        self.assertEqual(ingestion.rows_loaded, 2)
        self.assertEqual(ingestion.rows_rejected, 1)
        self.assertIsNotNone(ingestion.finished_at)

    @patch(
        "apps.auditoria.services.reinspecao_ged_sync_runner.sync_reinspecao_ged_to_db"
    )
    def test_failed_attempt_does_not_block_retry_for_same_file(self, mock_sync):
        mock_sync.side_effect = [RuntimeError("banco indisponível"), _sync_result(created=1)]

        first = run_sync_with_audit(path=str(self.source))
        second = run_sync_with_audit(path=str(self.source))

        self.assertEqual(first, (False, "banco indisponível", False))
        self.assertTrue(second[0])
        attempts = list(BotDataIngestion.objects.order_by("attempt_number"))
        self.assertEqual(BotDataArtifact.objects.count(), 1)
        self.assertEqual([row.attempt_number for row in attempts], [1, 2])
        self.assertEqual(attempts[0].status, BotDataIngestion.STATUS_FAILED)
        self.assertEqual(attempts[0].error_summary, "banco indisponível")
        self.assertEqual(attempts[1].status, BotDataIngestion.STATUS_COMPLETED)

    @patch(
        "apps.auditoria.services.reinspecao_ged_sync_runner.sync_reinspecao_ged_to_db",
        return_value=_sync_result(created=1),
    )
    def test_same_completed_delivery_is_audited_and_skipped(self, mock_sync):
        first = run_sync_with_audit(path=str(self.source))
        second = run_sync_with_audit(path=str(self.source))

        self.assertTrue(first[0])
        self.assertEqual(second[0], True)
        self.assertEqual(second[2], True)
        self.assertIn("já processado", second[1])
        self.assertEqual(mock_sync.call_count, 1)
        attempts = list(BotDataIngestion.objects.order_by("attempt_number"))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[1].status, BotDataIngestion.STATUS_COMPLETED)
        self.assertEqual(attempts[1].rows_read, 0)

    @patch(
        "apps.auditoria.services.reinspecao_ged_sync_runner.sync_reinspecao_ged_to_db",
        return_value=_sync_result(created=0),
    )
    def test_same_content_from_new_immutable_file_is_reprocessed(self, mock_sync):
        copy = self.source.with_name(f"{self.source.stem}-copia.csv")
        copy.write_bytes(self.source.read_bytes())

        run_sync_with_audit(path=str(self.source))
        run_sync_with_audit(path=str(copy))

        self.assertEqual(BotDataArtifact.objects.count(), 1)
        self.assertEqual(BotDataIngestion.objects.count(), 2)
        self.assertEqual(mock_sync.call_count, 2)

    @patch(
        "apps.auditoria.services.reinspecao_ged_sync_runner.sync_reinspecao_ged_to_db",
        return_value=_sync_result(created=0),
    )
    def test_running_queue_job_is_linked_to_ingestion(self, mock_sync):
        source = str(self.source.resolve())
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_REINSPECAO_GED,
            source_path=source,
            lane=BotDbSyncJob.LANE_LOW,
            status=BotDbSyncJob.STATUS_RUNNING,
        )

        run_sync_with_audit(path=source)

        self.assertEqual(BotDataIngestion.objects.get().sync_job, job)
