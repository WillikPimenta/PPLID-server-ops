"""Testes do modulo server_monitoring."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server_monitoring as sm  # noqa: E402


class ProbeHealthTests(unittest.TestCase):
    def test_probe_health_unreachable(self) -> None:
        result = sm.probe_health("http://127.0.0.1:1/health/")
        self.assertFalse(result["reachable"])
        self.assertIn("durationMs", result)

    @patch("server_monitoring.urllib.request.urlopen")
    def test_probe_health_ok(self, mock_urlopen) -> None:
        class FakeResp:
            status = 200

            def read(self):
                return json.dumps({"status": "healthy", "database": "ok"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        mock_urlopen.return_value = FakeResp()
        result = sm.probe_health("http://127.0.0.1:8000/api/v1/health/")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["status"], "healthy")
        self.assertGreaterEqual(result["durationMs"], 0)


class MonitoringConfigTests(unittest.TestCase):
    def test_default_monitoring_settings(self) -> None:
        settings = sm.get_monitoring_settings({"logDir": "C:/PPLID/logs"})
        self.assertEqual(settings["retentionDays"], 7)
        self.assertTrue(settings["enabledCategories"]["api"])


class BuildMonitoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)
        (self.base / "ops" / "data").mkdir(parents=True)
        (self.base / "logs").mkdir(parents=True)
        self.db = self.base / "ops" / "data" / "ops-store.db"
        ops_lib = Path(__file__).resolve().parents[4] / "ops" / "lib"
        if str(ops_lib) not in sys.path:
            sys.path.insert(0, str(ops_lib))
        import ops_store  # type: ignore

        ops_store.init_store(self.db)
        ops_store.insert_monitor_sample("DEV", "health_latency_ms", 100, db_path=self.db)
        ops_store.insert_monitor_sample("DEV", "health_reachable", 1, db_path=self.db)
        self.config = {
            "logDir": str(self.base / "logs"),
            "DEV": {"backendPort": 8001, "frontendPort": 5174},
        }
        machine = {
            "baseDir": str(self.base),
            "opsStore": {"path": str(self.db)},
            "monitoring": {"retentionDays": 7},
        }
        (self.base / "machine.config.json").write_text(json.dumps(machine), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_monitoring_summary(self) -> None:
        summary = sm.build_monitoring_summary(self.config, "DEV", window_hours=24)
        self.assertEqual(summary["environment"], "DEV")
        self.assertIn("health", summary)
        self.assertGreaterEqual(summary["uptimePct"], 0)

    def test_build_monitoring_series(self) -> None:
        series = sm.build_monitoring_series(self.config, "DEV", "health_latency_ms", hours=168)
        self.assertGreaterEqual(len(series["points"]), 1)

    def test_build_monitoring_config(self) -> None:
        cfg = sm.build_monitoring_config(self.config)
        self.assertEqual(cfg["retentionDays"], 7)
        self.assertIn("MAIN", cfg["environments"])
        self.assertIn("slos", cfg)
        self.assertEqual(cfg["slos"]["healthP95WarnMs"], 400)
        self.assertIn("collectorStatus", cfg)
        self.assertIn("generatedAt", cfg)

    def test_enrich_monitor_event(self) -> None:
        event = sm._enrich_monitor_event(
            {
                "environment": "DEV",
                "category": "logs",
                "title": "Pico de erros nos logs",
                "severity": "warn",
            }
        )
        self.assertIn("recommendedAction", event)
        self.assertIn("investigationLink", event)
        self.assertIn("DEV", event["investigationLink"])

    def test_aggregate_deploy_runs(self) -> None:
        now = sm._utc_now_iso()
        runs = [
            {"started_at": now, "result": "failed", "failed_step": "git_fetch"},
            {"started_at": now, "result": "success"},
        ]
        agg = sm._aggregate_deploy_runs(runs, hours=24)
        self.assertEqual(agg["total24h"], 2)
        self.assertEqual(agg["failed24h"], 1)
        self.assertEqual(agg["failuresByStep"]["git_fetch"], 1)


if __name__ == "__main__":
    unittest.main()
