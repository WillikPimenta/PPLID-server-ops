# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from apps.common.bot_db_sync_lanes import LANE_MID

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.models import BotDbSyncJob


@override_settings(ACCESS_ENFORCEMENT=True)
class SyncJobsViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sync_ui", password="x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\prod.xlsx",
            status=BotDbSyncJob.STATUS_RUNNING,
            message="Sincronizando…",
        )
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="detalhado",
            source_path=r"C:\tmp\rotina.parquet",
            status=BotDbSyncJob.STATUS_PENDING,
        )
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_FALHAS_CRITICAS,
            source_path=r"C:\tmp\falhas.xlsx",
            status=BotDbSyncJob.STATUS_DONE,
            message="ok",
        )

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.access.permissions.user_has_any_permission", return_value=True)
    @patch("apps.common.bot_db_sync_queue.reconcile_bot_db_sync_jobs")
    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker")
    def test_lists_jobs_and_summary(self, _spawn, _reconcile, _any, _can):
        res = self.client.get("/api/v1/automacoes/sync-jobs/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["pending"], 1)
        self.assertEqual(body["summary"]["running"], 1)
        self.assertEqual(body["summary"]["done"], 1)
        self.assertTrue(body["summary"]["queue_busy"])
        self.assertGreaterEqual(len(body["jobs"]), 3)
        first = body["jobs"][0]
        self.assertIn("domain_label", first)
        self.assertIn("status_label", first)
        self.assertIn("source_name", first)
        self.assertIn("lane", first)
        self.assertIn("lane_label", first)
        self.assertIn("active_by_lane", body)
        self.assertIn("high", body["active_by_lane"])
        self.assertIn(LANE_MID, body["active_by_lane"])
        self.assertIn("low", body["active_by_lane"])
        self.assertIn("drain_status", body)
        self.assertIn("queue_wait_s", body)
        self.assertEqual(len(body["active_by_lane"]["low"]), 2)
        _reconcile.assert_called_once_with(spawn_if_requeued=True)

    @patch("apps.automacoes.views.can_configure_automacoes", return_value=True)
    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.access.permissions.user_has_any_permission", return_value=True)
    def test_cancel_pending_job(self, _any, _can, _cfg):
        pending = BotDbSyncJob.objects.filter(status=BotDbSyncJob.STATUS_PENDING).first()
        self.assertIsNotNone(pending)
        res = self.client.post(f"/api/v1/automacoes/sync-jobs/{pending.pk}/cancel/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        pending.refresh_from_db()
        self.assertEqual(pending.status, BotDbSyncJob.STATUS_SKIPPED)

    @patch("apps.automacoes.views.can_configure_automacoes", return_value=True)
    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.access.permissions.user_has_any_permission", return_value=True)
    @patch("apps.common.bot_db_sync_queue.reconcile_bot_db_sync_jobs")
    def test_cancel_running_conflict(self, _reconcile, _any, _can, _cfg):
        running = BotDbSyncJob.objects.filter(status=BotDbSyncJob.STATUS_RUNNING).first()
        res = self.client.post(f"/api/v1/automacoes/sync-jobs/{running.pk}/cancel/")
        self.assertEqual(res.status_code, 409)

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.access.permissions.user_has_any_permission", return_value=True)
    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker")
    def test_get_reconciles_orphan_running(self, mock_spawn, _any, _can):
        """Poll da UI refileira running órfão (sem drain ativo) e spawna drain."""
        from apps.common.bot_db_sync_lanes import LANE_LOW

        running = BotDbSyncJob.objects.filter(status=BotDbSyncJob.STATUS_RUNNING).first()
        running.lane = LANE_LOW
        running.started_at = timezone.now()
        running.attempts = 1
        running.save(update_fields=["lane", "started_at", "attempts"])

        res = self.client.get("/api/v1/automacoes/sync-jobs/")
        self.assertEqual(res.status_code, 200)
        running.refresh_from_db()
        self.assertEqual(running.status, BotDbSyncJob.STATUS_PENDING)
        self.assertIn("órfão", running.message)
        mock_spawn.assert_called()
        body = res.json()
        self.assertEqual(body["summary"]["running"], 0)
        self.assertGreaterEqual(body["summary"]["pending"], 2)

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.access.permissions.user_has_any_permission", return_value=True)
    def test_get_sync_config(self, _any, _can):
        res = self.client.get("/api/v1/automacoes/sync-config/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertIn("chunk_size", body["config"])
        self.assertIn("bounds", body["config"])

    @patch("apps.automacoes.views.can_configure_automacoes", return_value=True)
    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.access.permissions.user_has_any_permission", return_value=True)
    def test_put_sync_config(self, _any, _can, _cfg):
        res = self.client.put(
            "/api/v1/automacoes/sync-config/",
            {
                "chunk_size": 1500,
                "batch_size": 1500,
                "high_concurrency": 1,
                "mid_concurrency": 2,
                "low_concurrency": 3,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["config"]["chunk_size"], 1500)
        self.assertEqual(body["config"]["mid_concurrency"], 2)
        self.assertEqual(body["config"]["low_concurrency"], 3)
