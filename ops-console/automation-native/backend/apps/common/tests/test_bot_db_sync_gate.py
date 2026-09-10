# -*- coding: utf-8 -*-
"""Filas high/low: serialização por lane e paralelismo entre lanes."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.common.bot_db_sync_gate import (
    BotDbSyncQueueTimeoutError,
    _fallback_host_heavy_lock,
    _fallback_slot_locks,
    _fallback_lock,
    _fallback_locks,
    bot_db_sync_slot,
)
from apps.common.bot_db_sync_lanes import LANE_HIGH, LANE_LOW


@override_settings(BOT_DB_SYNC_QUEUE_WAIT_S=2, BOT_DB_SYNC_POLL_MS=50)
class BotDbSyncGateTests(SimpleTestCase):
    def setUp(self):
        slot_locks = [lock for locks in _fallback_slot_locks.values() for lock in locks]
        for lock in slot_locks + [_fallback_host_heavy_lock]:
            if lock.locked():
                try:
                    lock.release()
                except RuntimeError:
                    pass

    @patch("apps.common.bot_db_sync_gate._is_postgresql", return_value=False)
    def test_second_caller_same_lane_waits(self, _mock_pg):
        order: list[str] = []
        barrier = threading.Event()

        def holder():
            with bot_db_sync_slot("first", lane=LANE_HIGH):
                order.append("first-enter")
                barrier.set()
                time.sleep(0.25)
            order.append("first-exit")

        def waiter():
            barrier.wait(timeout=2)
            with bot_db_sync_slot("second", lane=LANE_HIGH):
                order.append("second-enter")
            order.append("second-exit")

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(
            order,
            ["first-enter", "first-exit", "second-enter", "second-exit"],
        )

    @patch("apps.common.bot_db_sync_gate._is_postgresql", return_value=False)
    def test_high_and_low_run_in_parallel(self, _mock_pg):
        order: list[str] = []
        both_inside = threading.Barrier(2, timeout=3)

        def run_lane(lane: str, name: str):
            with bot_db_sync_slot(name, lane=lane):
                order.append(f"{name}-enter")
                both_inside.wait()
                time.sleep(0.05)
            order.append(f"{name}-exit")

        t_high = threading.Thread(target=run_lane, args=(LANE_HIGH, "high"))
        t_low = threading.Thread(target=run_lane, args=(LANE_LOW, "low"))
        t_high.start()
        t_low.start()
        t_high.join(timeout=5)
        t_low.join(timeout=5)

        self.assertFalse(t_high.is_alive())
        self.assertFalse(t_low.is_alive())
        self.assertIn("high-enter", order)
        self.assertIn("low-enter", order)
        # Ambos entraram antes de qualquer exit → paralelismo real.
        first_exit = min(i for i, x in enumerate(order) if x.endswith("-exit"))
        enters_before_exit = [x for x in order[:first_exit] if x.endswith("-enter")]
        self.assertEqual(set(enters_before_exit), {"high-enter", "low-enter"})

    @patch("apps.common.bot_db_sync_gate._lane_concurrency", return_value=2)
    @patch("apps.common.bot_db_sync_gate._is_postgresql", return_value=False)
    def test_two_callers_same_lane_run_when_two_slots_configured(
        self, _mock_pg, _mock_concurrency
    ):
        both_inside = threading.Barrier(2, timeout=3)
        entered: list[str] = []

        def run(name: str):
            with bot_db_sync_slot(name, lane=LANE_LOW):
                entered.append(name)
                both_inside.wait()

        threads = [threading.Thread(target=run, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(set(entered), {"a", "b"})

    @patch("apps.common.bot_db_sync_gate._is_postgresql", return_value=False)
    def test_queue_timeout_when_slot_busy(self, _mock_pg):
        acquired = _fallback_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaises(BotDbSyncQueueTimeoutError):
                with bot_db_sync_slot("timeout-test", lane=LANE_HIGH):
                    pass
        finally:
            _fallback_lock.release()

    @patch("apps.common.bot_db_sync_gate._is_postgresql", return_value=False)
    def test_host_heavy_lock_blocks_second_high(self, _mock_pg):
        """Lock host (88442200) so segundo high falha mesmo com lane livre."""
        acquired = _fallback_host_heavy_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaises(BotDbSyncQueueTimeoutError):
                with bot_db_sync_slot("blocked-by-host", lane=LANE_HIGH):
                    pass
        finally:
            _fallback_host_heavy_lock.release()

    @patch("apps.common.bot_db_sync_gate._delete_lease")
    @patch("apps.common.bot_db_sync_gate._create_lease")
    @patch("apps.common.bot_db_sync_gate._try_acquire_pair", return_value=(False, True))
    @patch("apps.common.bot_db_sync_gate._open_lock_connection")
    @patch("apps.common.bot_db_sync_gate._is_postgresql", return_value=True)
    def test_postgresql_slot_closes_dedicated_connection_on_exception(
        self,
        _mock_pg,
        mock_open,
        _mock_acquire,
        _mock_create,
        _mock_delete,
    ):
        lock_connection = MagicMock()
        lock_connection.info.backend_pid = 1234
        mock_open.return_value = lock_connection

        with self.assertRaises(RuntimeError):
            with bot_db_sync_slot("dedicated", lane=LANE_LOW):
                raise RuntimeError("sync failed")

        _mock_create.assert_called_once()
        _mock_delete.assert_called_once()
        lock_connection.close.assert_called_once()
