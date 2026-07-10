"""Tests for ops_store SQLite WAL store."""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

import ops_store


class OpsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "test-ops-store.db"
        ops_store.init_store(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_and_tail_deploy_log(self) -> None:
        row_id = ops_store.append_deploy_log(
            "DEV", "run-1", "promote.log", "INFO", "hello", db_path=self.db
        )
        self.assertGreater(row_id, 0)
        rows = ops_store.tail_deploy_logs("DEV", "run-1", "promote", db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "hello")

    def test_concurrent_append_and_read(self) -> None:
        def writer(i: int) -> None:
            ops_store.append_deploy_log(
                "DEV", "run-2", "promote.log", "INFO", f"line-{i}", db_path=self.db
            )

        def reader() -> int:
            return len(ops_store.tail_deploy_logs("DEV", "run-2", "promote", limit=500, db_path=self.db))

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            write_futs = [pool.submit(writer, i) for i in range(50)]
            read_futs = [pool.submit(reader) for _ in range(20)]
            concurrent.futures.wait(write_futs + read_futs)
        total = len(ops_store.tail_deploy_logs("DEV", "run-2", "promote", limit=500, db_path=self.db))
        self.assertEqual(total, 50)

    def test_save_and_get_steps(self) -> None:
        steps = [
            {"id": "restart_services", "label": "Reinicio", "phase": "promoting", "logFile": "promote.log", "status": "running"},
        ]
        ops_store.save_deploy_steps("DEV", "run-3", steps, db_path=self.db)
        loaded = ops_store.get_deploy_steps("DEV", "run-3", db_path=self.db)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "restart_services")

    def test_monitor_samples_and_purge(self) -> None:
        ops_store.insert_monitor_sample("MAIN", "health_latency_ms", 120.5, db_path=self.db)
        ops_store.insert_monitor_sample("MAIN", "health_latency_ms", 250.0, db_path=self.db)
        agg = ops_store.aggregate_monitor_samples("MAIN", "health_latency_ms", db_path=self.db)
        self.assertEqual(agg["count"], 2)
        self.assertAlmostEqual(agg["avg"], 185.25)
        self.assertIn("p95", agg)
        self.assertIn("latest", agg)

        ops_store.insert_monitor_event(
            "MAIN", "warn", "availability", "Health lento", detail="test", db_path=self.db
        )
        events = ops_store.query_monitor_events("MAIN", db_path=self.db)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Health lento")

        purged = ops_store.purge_monitor_data(retention_days=7, db_path=self.db)
        self.assertIn("monitor_samples", purged)


if __name__ == "__main__":
    unittest.main()
