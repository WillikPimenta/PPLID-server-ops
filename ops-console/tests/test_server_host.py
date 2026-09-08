"""Tests for host telemetry without depending on the machine hardware."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

OPS_CONSOLE = Path(__file__).resolve().parent.parent
OPS_LIB = OPS_CONSOLE.parent / "lib"
for path in (OPS_CONSOLE, OPS_LIB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ops_store  # type: ignore  # noqa: E402
import server_host as sh  # noqa: E402


class HostTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        sh._ALERT_STATES.clear()
        sh._LAST_EVENTS.clear()

    def test_snapshot_samples_are_batched_with_host_target(self) -> None:
        snapshot = {
            "generatedAt": "2026-08-10T12:00:00.000Z",
            "cpu": {"usedPct": 42},
            "memory": {"usedPct": 55, "usedBytes": 1024},
            "commit": {"usedPct": 82, "usedBytes": 2048},
            "swap": {"usedPct": 2},
            "diskIo": {"readBps": 10, "writeBps": 20},
            "network": {"rxBps": 30, "txBps": 40},
            "disks": [{"mount": "C:\\", "device": "C:", "usedPct": 70, "freeBytes": 500}],
            "gpus": [{"index": 0, "name": "GPU", "utilizationPct": 20, "memoryUsedPct": 30, "temperatureC": 60}],
        }
        rows = sh._snapshot_samples(snapshot)
        self.assertTrue(rows)
        self.assertTrue(all(row["environment"] == "HOST" for row in rows))
        self.assertIn("host_cpu_pct", {row["metric_key"] for row in rows})
        self.assertIn("host_commit_used_pct", {row["metric_key"] for row in rows})
        self.assertIn("host_commit_used_bytes", {row["metric_key"] for row in rows})
        self.assertIn("host_gpu_temperature_c", {row["metric_key"] for row in rows})

    def test_batch_insert_is_atomic_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "ops.db"
            ops_store.init_store(db)
            inserted = ops_store.insert_monitor_samples_batch(
                [
                    {"environment": "HOST", "metric_key": "host_cpu_pct", "value": 10},
                    {"environment": "HOST", "metric_key": "host_memory_used_pct", "value": 20},
                ],
                db_path=db,
            )
            self.assertEqual(inserted, 2)
            self.assertEqual(len(ops_store.query_monitor_series("HOST", "host_cpu_pct", db_path=db)), 1)

    def test_bucketed_series_covers_long_windows_without_raw_limit(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "ops.db"
            ops_store.init_store(db)
            rows = [
                {
                    "environment": "HOST",
                    "metric_key": "host_cpu_pct",
                    "value": index,
                    "recorded_at": f"2026-08-10T12:{index:02d}:00.000Z",
                }
                for index in range(10)
            ]
            ops_store.insert_monitor_samples_batch(rows, db_path=db)
            points = ops_store.query_monitor_series_bucketed(
                "HOST",
                "host_cpu_pct",
                since="2026-08-10T12:00:00.000Z",
                bucket_seconds=300,
                limit=360,
                db_path=db,
            )
            self.assertEqual(len(points), 2)
            self.assertEqual(sum(point["sampleCount"] for point in points), 10)

    def test_diagnostic_sanitizes_nested_secrets(self) -> None:
        clean = sh.sanitize_diagnostic(
            {"password": "raw", "nested": {"apiToken": "raw", "safe": "ok"}, "rows": [{"cookie": "x"}]}
        )
        self.assertEqual(clean["password"], "***")
        self.assertEqual(clean["nested"]["apiToken"], "***")
        self.assertEqual(clean["nested"]["safe"], "ok")
        self.assertEqual(clean["rows"][0]["cookie"], "***")

    def test_degraded_snapshot_remains_json_serializable(self) -> None:
        config = {"logDir": "C:/PPLID/logs", "hostMonitoring": {"gpuEnabled": False}}
        with patch.object(sh, "psutil", None), patch.object(sh, "_collect_gpus", return_value=[]):
            snapshot = sh.collect_host_snapshot(config)
        self.assertEqual(snapshot["collector"]["mode"], "degraded")
        self.assertIn("cpu", snapshot)
        self.assertIn("commit", snapshot)
        json.dumps(snapshot)

    def test_commit_pressure_creates_a_dedicated_alert(self) -> None:
        class FakeStore:
            def __init__(self):
                self.events = []

            def insert_monitor_event(self, env, severity, category, title, **kwargs):
                self.events.append((env, severity, category, title, kwargs.get("detail")))

        snapshot = {
            "cpu": {"usedPct": 1},
            "memory": {"usedPct": 2},
            "commit": {"usedPct": 99.6, "usedBytes": 65, "limitBytes": 66},
            "disks": [],
            "gpus": [],
        }
        store = FakeStore()
        with patch.object(sh, "_is_sustained", return_value=True):
            sh._evaluate_alerts({}, snapshot, store, Path("test.db"))
        event = next(item for item in store.events if "virtual comprometida" in item[3])
        self.assertEqual(event[1], "critical")

    def test_invalid_metric_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "inválida"):
            sh.build_host_series({"logDir": "C:/PPLID/logs"}, "host_password", hours=24)

    def test_alert_transitions_create_problem_and_recovery(self) -> None:
        class FakeStore:
            def __init__(self):
                self.events = []

            def insert_monitor_event(self, env, severity, category, title, **kwargs):
                self.events.append((env, severity, category, title, kwargs.get("detail")))

        store = FakeStore()
        sh._record_alert(store, Path("test.db"), "cpu", "warn", "CPU do host sob pressão", "90%")
        sh._record_alert(store, Path("test.db"), "cpu", "ok", "CPU do host sob pressão", "20%")
        self.assertEqual(store.events[0][1], "warn")
        self.assertEqual(store.events[1][1], "info")
        self.assertIn("Recuperado", store.events[1][3])


if __name__ == "__main__":
    unittest.main()
