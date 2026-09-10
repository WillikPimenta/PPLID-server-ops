# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.access.models import PortalRoleDefinition
from apps.common.models import BotDataIngestion
from apps.replicacao_d1.models import ReplicacaoD1Run, ReplicacaoD1SyncLog
from apps.replicacao_d1.services.retention import purge_replicacao_d1_retention


User = get_user_model()


class PublicApiSanitizationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="rep_d1_pub", password="x")
        try:
            role, _ = PortalRoleDefinition.objects.get_or_create(
                code="test_automacao_view_pub",
                defaults={"name": "Test Automacao View Pub", "permissions": [R.PLANEJAMENTO_AUTOMACAO_VIEW]},
            )
            if hasattr(self.user, "portal_roles"):
                self.user.portal_roles.add(role)
        except Exception:
            pass
        self.client.force_authenticate(user=self.user)
        ReplicacaoD1Run.objects.create(
            run_id="20260717_093000",
            data_referencia_d1="2026-07-16",
            parquet_referencia="brflow-detalhado-tratado_20260716.parquet",
            protocolos_total=1,
        )

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_run_api_hides_infrastructure_fields(self, _mock_perm):
        resp = self.client.get("/api/v1/replicacao-d1/runs/20260717_093000/")
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertNotIn("source_file", data)
        self.assertNotIn("config_version", data)
        self.assertNotIn("config_hash", data)
        self.assertNotIn("parquet_referencia", data)
        self.assertIn("referencia_dados", data)
        self.assertEqual(data["referencia_dados"], "brflow-detalhado-tratado_20260716.parquet")


class RetentionServiceTests(TestCase):
    def test_purge_old_sync_logs_dry_run(self):
        old = timezone.now() - timedelta(days=120)
        log = ReplicacaoD1SyncLog.objects.create(
            kind=ReplicacaoD1SyncLog.KIND_PLANO,
            success=True,
        )
        ReplicacaoD1SyncLog.objects.filter(pk=log.pk).update(started_at=old)
        ReplicacaoD1SyncLog.objects.create(
            kind=ReplicacaoD1SyncLog.KIND_PLANO,
            success=True,
        )
        report = purge_replicacao_d1_retention(dry_run=True)
        self.assertEqual(report.sync_logs_deleted, 1)
        self.assertEqual(ReplicacaoD1SyncLog.objects.count(), 2)

    def test_purge_old_ingestions(self):
        old = timezone.now() - timedelta(days=200)
        ing = BotDataIngestion.objects.create(
            source_key="old-ingestion-key-001",
            domain="replicacao_d1",
            kind="plano",
            status=BotDataIngestion.STATUS_COMPLETED,
            finished_at=old,
        )
        BotDataIngestion.objects.filter(pk=ing.pk).update(started_at=old)
        report = purge_replicacao_d1_retention(dry_run=False)
        self.assertEqual(report.ingestions_deleted, 1)
        self.assertEqual(BotDataIngestion.objects.count(), 0)

    def test_preserves_runs_and_facts(self):
        ReplicacaoD1Run.objects.create(
            run_id="keep_run",
            data_referencia_d1="2026-01-01",
        )
        old = timezone.now() - timedelta(days=120)
        log = ReplicacaoD1SyncLog.objects.create(
            kind=ReplicacaoD1SyncLog.KIND_PLANO,
            success=True,
        )
        ReplicacaoD1SyncLog.objects.filter(pk=log.pk).update(started_at=old)
        purge_replicacao_d1_retention(dry_run=False)
        self.assertTrue(ReplicacaoD1Run.objects.filter(run_id="keep_run").exists())
