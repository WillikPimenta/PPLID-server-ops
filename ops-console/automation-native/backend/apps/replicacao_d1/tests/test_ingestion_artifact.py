# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from pathlib import Path

from django.test import TestCase

from apps.common.models import BotDataArtifact, BotDataIngestion
from apps.replicacao_d1.services.ingestion import compute_content_sha256, start_ingestion


class IngestionArtifactTests(TestCase):
    def test_artifact_and_retry_attempt(self):
        tmp = Path(self._testMethodName + ".csv")
        tmp.write_text("a;b\n1;2\n", encoding="utf-8")
        try:
            sha = compute_content_sha256(tmp)
            ing1 = start_ingestion(
                domain="replicacao_d1",
                kind="replicados",
                reference_date=date(2026, 8, 9),
                source_file=tmp.name,
                content_path=tmp,
            )
            ing2 = start_ingestion(
                domain="replicacao_d1",
                kind="replicados",
                reference_date=date(2026, 8, 9),
                source_file=tmp.name,
                content_path=tmp,
            )
            self.assertEqual(BotDataArtifact.objects.count(), 1)
            self.assertEqual(ing1.artifact_id, ing2.artifact_id)
            self.assertEqual(ing1.attempt_number, 1)
            self.assertEqual(ing2.attempt_number, 2)
            self.assertNotEqual(ing1.pk, ing2.pk)
            self.assertEqual(ing1.artifact.content_sha256, sha)
            self.assertFalse(ing1.artifact.legacy_unverified)
        finally:
            tmp.unlink(missing_ok=True)

    def test_legacy_unverified_without_file(self):
        ing = start_ingestion(
            domain="replicacao_d1",
            kind="plano",
            run_id="run_x",
            source_file="missing.xlsx",
        )
        self.assertTrue(ing.artifact.legacy_unverified)
        self.assertEqual(ing.attempt_number, 1)
