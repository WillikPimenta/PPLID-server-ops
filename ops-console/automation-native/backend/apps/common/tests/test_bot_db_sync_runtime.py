# -*- coding: utf-8 -*-
"""Runtime config + cancel de jobs pending."""
from __future__ import annotations

import importlib

from django.apps import apps as django_apps
from django.test import TestCase, override_settings

from apps.common.bot_db_sync_lanes import LANE_HIGH, LANE_LOW
from apps.common.bot_db_sync_queue import cancel_pending_job
from apps.common.bot_db_sync_runtime import (
    get_bot_db_sync_runtime_config,
    update_bot_db_sync_runtime_config,
)
from apps.common.models import BotDbSyncJob, BotDbSyncRuntimeConfig


class BotDbSyncRuntimeConfigTests(TestCase):
    def test_defaults_without_row(self):
        cfg = get_bot_db_sync_runtime_config()
        self.assertEqual(cfg.chunk_size, 2000)
        self.assertEqual(cfg.batch_size, 2000)
        self.assertEqual(cfg.drain_max_jobs, 10)
        self.assertEqual(cfg.high_concurrency, 1)
        self.assertEqual(cfg.mid_concurrency, 1)
        self.assertEqual(cfg.low_concurrency, 1)

    def test_update_persists_and_clamps(self):
        updated = update_bot_db_sync_runtime_config(
            chunk_size=50,
            drain_max_jobs=99,
            high_concurrency=0,
            mid_concurrency=99,
            low_concurrency=99,
        )
        self.assertEqual(updated.chunk_size, 100)  # min bound
        self.assertEqual(updated.drain_max_jobs, 50)  # max bound
        self.assertEqual(updated.high_concurrency, 1)
        self.assertEqual(updated.mid_concurrency, 10)
        self.assertEqual(updated.low_concurrency, 20)
        row = BotDbSyncRuntimeConfig.objects.get(pk=1)
        self.assertEqual(row.chunk_size, 100)
        again = get_bot_db_sync_runtime_config()
        self.assertEqual(again.chunk_size, 100)
        self.assertEqual(again.drain_max_jobs, 50)

    @override_settings(BOT_DB_SYNC_STALE_MINUTES=60, BOT_DB_SYNC_QUEUE_WAIT_S=120)
    def test_settings_fallback_when_no_row(self):
        BotDbSyncRuntimeConfig.objects.all().delete()
        cfg = get_bot_db_sync_runtime_config()
        self.assertEqual(cfg.stale_minutes, 60)
        self.assertEqual(cfg.queue_wait_s, 120)


class BotDbSyncMidLaneMigrationTests(TestCase):
    def test_moves_340_pending_quality_jobs_without_touching_running(self):
        BotDbSyncJob.objects.bulk_create(
            [
                BotDbSyncJob(
                    domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
                    source_path=str(index),
                    lane=LANE_LOW,
                    status=BotDbSyncJob.STATUS_PENDING,
                )
                for index in range(340)
            ]
        )
        running = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
            source_path="running",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_RUNNING,
        )

        migration = importlib.import_module(
            "apps.common.migrations.0015_bot_db_sync_mid_lane"
        )
        migration.move_quality_projection_to_mid(django_apps, None)

        self.assertEqual(
            BotDbSyncJob.objects.filter(
                domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
                lane="mid",
                status=BotDbSyncJob.STATUS_PENDING,
            ).count(),
            340,
        )
        running.refresh_from_db()
        self.assertEqual(running.lane, LANE_LOW)


class CancelPendingJobTests(TestCase):
    def test_cancel_pending_ok(self):
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\a.xlsx",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        cancelled = cancel_pending_job(job.pk)
        self.assertEqual(cancelled.status, BotDbSyncJob.STATUS_SKIPPED)
        self.assertIn("Cancelado", cancelled.message)
        self.assertIsNotNone(cancelled.finished_at)

    def test_cancel_running_raises(self):
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="detalhado",
            source_path=r"C:\tmp\h.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_RUNNING,
        )
        with self.assertRaises(ValueError):
            cancel_pending_job(job.pk)

    def test_cancel_missing_raises(self):
        with self.assertRaises(LookupError):
            cancel_pending_job(999999)
