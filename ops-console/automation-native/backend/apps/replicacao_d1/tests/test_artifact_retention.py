# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.common.models import BotDataArtifact, BotDataIngestion, BotDbSyncJob
from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Run
from apps.replicacao_d1.services.artifact_retention import (
    evaluate_run_retention_gate,
    ingestion_eligible_for_purge,
)
from apps.replicacao_d1.services.retention import purge_replicacao_d1_retention


class ArtifactRetentionGateTests(TestCase):
    run_id = "20260810_120000"

    def _seed_success_ingestion(self) -> BotDataIngestion:
        run = ReplicacaoD1Run.objects.create(
            run_id=self.run_id,
            data_referencia_d1=date(2026, 8, 9),
            status_canonical=ReplicacaoD1Run.STATUS_COMPLETED,
            finished_at=timezone.now(),
            protocolos_total=2,
            workflows_total=1,
            workflows_salvo_ok=1,
        )
        artifact = BotDataArtifact.objects.create(
            domain="replicacao_d1",
            kind="plano",
            semantic_key=self.run_id,
            safe_name=f"relatorio_{self.run_id}.xlsx",
            content_sha256="abc123",
            content_size=1000,
        )
        ing = BotDataIngestion.objects.create(
            source_key=f"test-{self.run_id}",
            domain="replicacao_d1",
            kind="plano",
            run_id=self.run_id,
            artifact=artifact,
            status=BotDataIngestion.STATUS_COMPLETED,
            rows_loaded=2,
            finished_at=timezone.now(),
        )
        ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 8, 9),
            protocolo="P1",
            workflow_config="WF",
        )
        ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 8, 9),
            protocolo="P2",
            workflow_config="WF",
        )
        return ing

    def test_gate_allows_when_ingestion_proven(self):
        self._seed_success_ingestion()
        result = evaluate_run_retention_gate(self.run_id)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reasons, [])

    def test_gate_blocks_without_ingestion(self):
        ReplicacaoD1Run.objects.create(
            run_id=self.run_id,
            data_referencia_d1=date(2026, 8, 9),
            status_canonical=ReplicacaoD1Run.STATUS_COMPLETED,
        )
        result = evaluate_run_retention_gate(self.run_id)
        self.assertFalse(result.allowed)
        self.assertTrue(any("ingestão" in r for r in result.reasons))

    def test_gate_blocks_active_sync_job(self):
        self._seed_success_ingestion()
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
            report_type=self.run_id,
            status=BotDbSyncJob.STATUS_RUNNING,
        )
        result = evaluate_run_retention_gate(self.run_id)
        self.assertFalse(result.allowed)
        self.assertTrue(any("job" in r for r in result.reasons))

    def test_gate_blocks_open_run(self):
        artifact = BotDataArtifact.objects.create(
            domain="replicacao_d1",
            kind="plano",
            semantic_key=self.run_id,
            safe_name="x.xlsx",
            content_sha256="hash1",
        )
        BotDataIngestion.objects.create(
            source_key=f"open-{self.run_id}",
            domain="replicacao_d1",
            kind="plano",
            run_id=self.run_id,
            artifact=artifact,
            status=BotDataIngestion.STATUS_COMPLETED,
            rows_loaded=1,
            finished_at=timezone.now(),
        )
        ReplicacaoD1Run.objects.create(
            run_id=self.run_id,
            data_referencia_d1=date(2026, 8, 9),
            status_canonical=ReplicacaoD1Run.STATUS_RUNNING,
        )
        result = evaluate_run_retention_gate(self.run_id)
        self.assertFalse(result.allowed)

    def test_purge_keeps_ingestion_when_gate_blocks(self):
        ing = self._seed_success_ingestion()
        ReplicacaoD1Run.objects.filter(run_id=self.run_id).update(
            status_canonical=ReplicacaoD1Run.STATUS_RUNNING
        )
        old = timezone.now() - timedelta(days=200)
        BotDataIngestion.objects.filter(pk=ing.pk).update(started_at=old, finished_at=old)
        report = purge_replicacao_d1_retention(dry_run=False)
        self.assertEqual(report.ingestions_deleted, 0)
        self.assertTrue(BotDataIngestion.objects.filter(pk=ing.pk).exists())

    def test_ingestion_eligible_for_purge_failed_status(self):
        ing = BotDataIngestion.objects.create(
            source_key="failed-ing",
            domain="replicacao_d1",
            kind="plano",
            status=BotDataIngestion.STATUS_FAILED,
        )
        self.assertTrue(ingestion_eligible_for_purge(ing))
