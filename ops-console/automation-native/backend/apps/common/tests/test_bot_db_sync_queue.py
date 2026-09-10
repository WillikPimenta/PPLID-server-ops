# -*- coding: utf-8 -*-
"""Fila bot→banco: enqueue, dedupe, claim/process e spawn."""
from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common.bot_db_sync_lanes import LANE_HIGH, LANE_MID, LANE_LOW
from apps.common.bot_db_sync_queue import (
    claim_next_job,
    drain_pending_jobs,
    enqueue_bot_db_sync,
    process_job,
)
from apps.common.models import BotDbSyncJob


class BotDbSyncQueueTests(TestCase):
    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_enqueue_creates_pending_and_spawns(self, mock_spawn):
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\a.xlsx",
            force=True,
            spawn=True,
        )
        self.assertTrue(created)
        self.assertEqual(job.status, BotDbSyncJob.STATUS_PENDING)
        self.assertEqual(job.lane, LANE_LOW)
        mock_spawn.assert_called_once()

    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_enqueue_sets_high_lane_for_detalhado(self, mock_spawn):
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="detalhado",
            source_path=r"C:\tmp\det.parquet",
            spawn=True,
        )
        self.assertTrue(created)
        self.assertEqual(job.lane, LANE_HIGH)
        mock_spawn.assert_called_once()

    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_enqueue_sets_mid_lane_for_quality_projection(self, mock_spawn):
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
            source_path="1,2,3",
            spawn=True,
        )
        self.assertTrue(created)
        self.assertEqual(job.lane, LANE_MID)
        mock_spawn.assert_called_once()

    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_enqueue_dedupes_active_job(self, mock_spawn):
        first, created1 = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\a.xlsx",
            spawn=False,
        )
        second, created2 = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\a.xlsx",
            spawn=True,
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(BotDbSyncJob.objects.count(), 1)
        mock_spawn.assert_called_once()

    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_claim_and_process_marks_done(self, mock_dispatch):
        mock_dispatch.return_value = (True, "ok", False)
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_MONITOR_EVENTOS,
            source_path=r"C:\tmp\m.parquet",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        claimed = claim_next_job(lane=LANE_LOW)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.pk, job.pk)
        self.assertEqual(claimed.status, BotDbSyncJob.STATUS_RUNNING)

        process_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, BotDbSyncJob.STATUS_DONE)
        self.assertEqual(claimed.message, "ok")
        self.assertIsNotNone(claimed.finished_at)

    @override_settings(QUALIDADE_PROJECTION_MAX_ATTEMPTS=3)
    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_quality_projection_retries_transient_failure(self, mock_dispatch):
        mock_dispatch.side_effect = RuntimeError("database temporarily unavailable")
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
            source_path="123",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_RUNNING,
            attempts=1,
        )

        process_job(job)

        job.refresh_from_db()
        self.assertEqual(job.status, BotDbSyncJob.STATUS_PENDING)
        self.assertIsNone(job.started_at)
        self.assertIn("Nova tentativa", job.message)

    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_claim_respects_lane(self, mock_dispatch):
        mock_dispatch.return_value = (True, "ok", False)
        high = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="detalhado",
            source_path=r"C:\tmp\h.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        low = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\l.xlsx",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        claimed_low = claim_next_job(lane=LANE_LOW)
        self.assertEqual(claimed_low.pk, low.pk)
        claimed_high = claim_next_job(lane=LANE_HIGH)
        self.assertEqual(claimed_high.pk, high.pk)

    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_drain_pending_jobs_processes_batch(self, mock_dispatch):
        mock_dispatch.return_value = (True, "ok", False)
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\1.xlsx",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\2.xlsx",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        result = drain_pending_jobs(max_jobs=5, lane=LANE_LOW)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["done"], 2)
        self.assertEqual(
            BotDbSyncJob.objects.filter(status=BotDbSyncJob.STATUS_PENDING).count(),
            0,
        )

    @patch("apps.common.bot_db_sync_queue.recover_stale_running_jobs", return_value={"requeued": 0, "failed": 0})
    @patch("apps.common.bot_db_sync_queue.recover_orphan_running_jobs_for_lane", return_value={"requeued": 0, "failed": 0})
    @patch("apps.common.bot_db_sync_queue._lane_concurrency", return_value=2)
    @patch("apps.common.bot_db_sync_queue.process_job")
    @patch("apps.common.bot_db_sync_queue.claim_next_job")
    def test_drain_processes_two_low_jobs_concurrently(
        self,
        mock_claim,
        mock_process,
        _mock_concurrency,
        _mock_orphan,
        _mock_stale,
    ):
        jobs = [
            SimpleNamespace(status=BotDbSyncJob.STATUS_RUNNING),
            SimpleNamespace(status=BotDbSyncJob.STATUS_RUNNING),
        ]
        available = list(jobs)

        def claim(*, lane):
            return available.pop(0) if available else None

        mock_claim.side_effect = claim
        both_inside = threading.Barrier(2, timeout=3)

        def process(job):
            both_inside.wait()
            job.status = BotDbSyncJob.STATUS_DONE

        mock_process.side_effect = process

        result = drain_pending_jobs(max_jobs=2, lane=LANE_LOW)

        self.assertEqual(result["concurrency"], 2)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["done"], 2)

    @override_settings(BOT_DB_SYNC_PARALLEL_IDLE_GRACE_S=0.5)
    @patch("apps.common.bot_db_sync_queue.recover_stale_running_jobs", return_value={"requeued": 0, "failed": 0})
    @patch("apps.common.bot_db_sync_queue.recover_orphan_running_jobs_for_lane", return_value={"requeued": 0, "failed": 0})
    @patch("apps.common.bot_db_sync_queue._lane_concurrency", return_value=2)
    @patch("apps.common.bot_db_sync_queue.process_job")
    @patch("apps.common.bot_db_sync_queue.claim_next_job")
    def test_idle_worker_waits_for_job_enqueued_during_active_sync(
        self,
        mock_claim,
        mock_process,
        _mock_concurrency,
        _mock_orphan,
        _mock_stale,
    ):
        first = SimpleNamespace(status=BotDbSyncJob.STATUS_RUNNING)
        second = SimpleNamespace(status=BotDbSyncJob.STATUS_RUNNING)
        first_claimed = False
        second_claimed = False
        second_available = threading.Event()
        both_inside = threading.Barrier(2, timeout=3)

        def claim(*, lane):
            nonlocal first_claimed, second_claimed
            if not first_claimed:
                first_claimed = True
                return first
            if second_available.is_set() and not second_claimed:
                second_claimed = True
                return second
            return None

        def process(job):
            if job is first:
                second_available.set()
            both_inside.wait()
            job.status = BotDbSyncJob.STATUS_DONE

        mock_claim.side_effect = claim
        mock_process.side_effect = process

        result = drain_pending_jobs(max_jobs=2, lane=LANE_LOW)

        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["done"], 2)

    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_drain_lane_skips_other_lane(self, mock_dispatch):
        mock_dispatch.return_value = (True, "ok", False)
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="detalhado",
            source_path=r"C:\tmp\h.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        result = drain_pending_jobs(max_jobs=5, lane=LANE_LOW)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(
            BotDbSyncJob.objects.filter(status=BotDbSyncJob.STATUS_PENDING).count(),
            1,
        )

    @patch("apps.common.bot_db_sync_queue.subprocess.Popen")
    def test_spawn_respects_cooldown(self, mock_popen):
        from apps.common.bot_db_sync_queue import (
            _last_spawn_at,
            _last_spawn_cooldown_sec,
            spawn_drain_worker,
        )

        _last_spawn_at.clear()
        _last_spawn_cooldown_sec.clear()
        mock_popen.return_value = MagicMock()
        self.assertTrue(spawn_drain_worker(max_jobs=1, lane=LANE_HIGH))
        self.assertEqual(mock_popen.call_count, 1)
        # Segunda chamada imediata na mesma lane deve ser ignorada.
        self.assertFalse(spawn_drain_worker(max_jobs=1, lane=LANE_HIGH))
        self.assertEqual(mock_popen.call_count, 1)

    @override_settings(BOT_DB_SYNC_STALE_MINUTES=30, BOT_DB_SYNC_STALE_MAX_REQUEUE=2)
    def test_stale_running_is_requeued(self):
        from datetime import timedelta

        from apps.common.bot_db_sync_queue import recover_stale_running_jobs

        old = timezone.now() - timedelta(minutes=60)
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\stale.xlsx",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_RUNNING,
            started_at=old,
            attempts=1,
        )
        result = recover_stale_running_jobs()
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(result["failed"], 0)
        job.refresh_from_db()
        self.assertEqual(job.status, BotDbSyncJob.STATUS_PENDING)
        self.assertIsNone(job.started_at)
        self.assertIn("Reenfileirado", job.message)

    @override_settings(BOT_DB_SYNC_STALE_MINUTES=30, BOT_DB_SYNC_STALE_MAX_REQUEUE=2)
    def test_stale_running_exceeds_requeue_becomes_failed(self):
        from datetime import timedelta

        from apps.common.bot_db_sync_queue import recover_stale_running_jobs

        old = timezone.now() - timedelta(minutes=60)
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=r"C:\tmp\stale2.xlsx",
            lane=LANE_LOW,
            status=BotDbSyncJob.STATUS_RUNNING,
            started_at=old,
            attempts=3,
        )
        result = recover_stale_running_jobs()
        self.assertEqual(result["requeued"], 0)
        self.assertEqual(result["failed"], 1)
        job.refresh_from_db()
        self.assertEqual(job.status, BotDbSyncJob.STATUS_FAILED)
        self.assertIsNotNone(job.finished_at)

    def test_orphan_running_requeued_when_lane_lock_held_by_caller(self):
        from apps.common.bot_db_sync_queue import recover_orphan_running_jobs_for_lane

        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="prod",
            source_path=r"C:\tmp\prod.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_RUNNING,
            started_at=timezone.now(),
            attempts=1,
        )
        result = recover_orphan_running_jobs_for_lane(LANE_HIGH)
        self.assertEqual(result["requeued"], 1)
        job.refresh_from_db()
        self.assertEqual(job.status, BotDbSyncJob.STATUS_PENDING)
        self.assertIn("órfão", job.message)

    @patch("apps.common.bot_db_sync_queue.subprocess.Popen")
    def test_spawn_writes_to_drain_log(self, mock_popen):
        from apps.common.bot_db_sync_queue import (
            _drain_log_path,
            _last_spawn_at,
            _last_spawn_cooldown_sec,
            spawn_drain_worker,
        )

        mock_popen.return_value = MagicMock()
        _last_spawn_at.clear()
        _last_spawn_cooldown_sec.clear()
        log_path = _drain_log_path()
        if log_path.exists():
            log_path.unlink()
        ok = spawn_drain_worker(max_jobs=3)
        self.assertTrue(ok)
        self.assertEqual(mock_popen.call_count, 3)  # high + mid + low
        kwargs = mock_popen.call_args.kwargs
        self.assertIsNot(kwargs["stdout"], None)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertTrue(log_path.exists())
        self.assertIn("drain spawn", log_path.read_text(encoding="utf-8"))
        lanes = {
            call.args[0][call.args[0].index("--lane") + 1]
            for call in mock_popen.call_args_list
        }
        self.assertEqual(lanes, {LANE_HIGH, LANE_MID, LANE_LOW})

    @patch(
        "apps.common.bot_db_sync_memory.memory_ok_for_heavy_sync",
        return_value=False,
    )
    @patch("apps.common.bot_db_sync_queue.subprocess.Popen")
    def test_spawn_high_skipped_when_memory_low(self, mock_popen, _mem):
        from apps.common.bot_db_sync_queue import (
            _last_spawn_at,
            _last_spawn_cooldown_sec,
            spawn_drain_worker,
        )

        _last_spawn_at.clear()
        _last_spawn_cooldown_sec.clear()
        self.assertFalse(spawn_drain_worker(max_jobs=1, lane=LANE_HIGH))
        mock_popen.assert_not_called()

    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_process_job_memory_error_is_permanent_and_sets_cooldown(
        self, mock_dispatch
    ):
        from apps.common.bot_db_sync_queue import (
            _last_spawn_at,
            _last_spawn_cooldown_sec,
            _spawn_cooldown_for,
            process_job,
        )

        _last_spawn_at.clear()
        _last_spawn_cooldown_sec.clear()
        mock_dispatch.side_effect = MemoryError(
            "Unable to allocate 1.39 MiB for an array"
        )
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="ged_detalhado",
            source_path=r"C:\tmp\ged.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_RUNNING,
            attempts=1,
        )
        process_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, BotDbSyncJob.STATUS_FAILED)
        self.assertIn("MemoryError", job.message)
        self.assertGreaterEqual(_spawn_cooldown_for(LANE_HIGH), 300.0)

    @override_settings(BOT_DB_SYNC_STALE_MINUTES=30, BOT_DB_SYNC_STALE_MAX_REQUEUE=2)
    def test_stale_oom_message_not_requeued(self):
        from datetime import timedelta

        from apps.common.bot_db_sync_queue import recover_stale_running_jobs

        old = timezone.now() - timedelta(minutes=60)
        job = BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="ged_detalhado",
            source_path=r"C:\tmp\oom.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_RUNNING,
            started_at=old,
            attempts=1,
            message="Unable to allocate 1.39 MiB for an array with shape (90776,)",
        )
        result = recover_stale_running_jobs()
        self.assertEqual(result["requeued"], 0)
        self.assertEqual(result["failed"], 1)
        job.refresh_from_db()
        self.assertEqual(job.status, BotDbSyncJob.STATUS_FAILED)
        self.assertIn("memória", job.message)

    @patch(
        "apps.common.bot_db_sync_memory.memory_ok_for_heavy_sync",
        return_value=False,
    )
    @patch("apps.common.bot_db_sync_queue._dispatch_job")
    def test_drain_high_defers_when_memory_low(self, mock_dispatch, _mem):
        BotDbSyncJob.objects.create(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            report_type="ged_detalhado",
            source_path=r"C:\tmp\ged.parquet",
            lane=LANE_HIGH,
            status=BotDbSyncJob.STATUS_PENDING,
        )
        result = drain_pending_jobs(max_jobs=5, lane=LANE_HIGH)
        self.assertTrue(result["deferred_memory"])
        self.assertEqual(result["processed"], 0)
        mock_dispatch.assert_not_called()
        self.assertEqual(
            BotDbSyncJob.objects.filter(status=BotDbSyncJob.STATUS_PENDING).count(),
            1,
        )
