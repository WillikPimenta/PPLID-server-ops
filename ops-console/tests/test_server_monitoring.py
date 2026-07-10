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
        ops_lib = Path(__file__).resolve().parents[2] / "lib"
        if not ops_lib.is_dir():
            ops_lib = Path(__file__).resolve().parents[4] / "ops" / "lib"
        if str(ops_lib) not in sys.path:
            sys.path.insert(0, str(ops_lib))
        import ops_store  # type: ignore

        self.ops_store = ops_store
        ops_store.init_store(self.db)
        for val in [100, 120, 150, 200, 800, 900, 950]:
            ops_store.insert_monitor_sample("DEV", "health_latency_ms", val, db_path=self.db)
        ops_store.insert_monitor_sample("DEV", "health_reachable", 1, db_path=self.db)
        ops_store.insert_monitor_event(
            "DEV", "warn", "availability", "Pico de latencia health (900ms)", detail="test", db_path=self.db
        )
        ops_store.insert_monitor_event(
            "DEV", "warn", "sync", "Sync falhou (rotina_bruto/prod)", detail="erro", db_path=self.db
        )
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

    def test_build_monitoring_summary_includes_p95_and_latest(self) -> None:
        summary = sm.build_monitoring_summary(self.config, "DEV", window_hours=24)
        self.assertEqual(summary["environment"], "DEV")
        self.assertIn("p95", summary["health"])
        self.assertIn("latest", summary["health"])
        self.assertGreater(summary["health"]["p95"], summary["health"]["avg"])
        self.assertIn("dataFresh", summary)

    def test_build_monitoring_series(self) -> None:
        series = sm.build_monitoring_series(self.config, "DEV", "health_latency_ms", hours=168)
        self.assertGreaterEqual(len(series["points"]), 1)

    def test_build_monitoring_series_with_center(self) -> None:
        events = self.ops_store.query_monitor_events("DEV", db_path=self.db)
        center = events[0]["recorded_at"]
        series = sm.build_monitoring_series(
            self.config, "DEV", "health_latency_ms", hours=1, center=center
        )
        self.assertIn("center", series)
        self.assertGreaterEqual(len(series["points"]), 1)

    def test_build_monitoring_config(self) -> None:
        cfg = sm.build_monitoring_config(self.config)
        self.assertEqual(cfg["retentionDays"], 7)
        self.assertIn("MAIN", cfg["environments"])
        self.assertIn("slos", cfg)
        self.assertEqual(cfg["slos"]["healthP95WarnMs"], 400)
        self.assertIn("collectorStatus", cfg)
        self.assertIn("generatedAt", cfg)

    def test_enrich_monitor_event_availability_link(self) -> None:
        event = sm._enrich_monitor_event(
            {
                "id": 1,
                "environment": "DEV",
                "category": "availability",
                "title": "Pico de latencia health (900ms)",
                "severity": "warn",
                "recorded_at": "2026-07-10T12:00:00.000Z",
            },
            self.config,
        )
        self.assertIn("recommendedAction", event)
        self.assertIn("/monitoring/latency", event["investigationLink"])
        self.assertIn("DEV", event["investigationLink"])
        self.assertIn("correlations", event)

    def test_enrich_monitor_event_sync_link(self) -> None:
        event = sm._enrich_monitor_event(
            {
                "environment": "MAIN",
                "category": "sync",
                "title": "Sync falhou (rotina_bruto/prod)",
                "severity": "warn",
            },
            self.config,
        )
        self.assertIn("/monitoring/syncs", event["investigationLink"])
        self.assertIn("MAIN", event["investigationLink"])

    def test_enrich_monitor_event_logs_link(self) -> None:
        event = sm._enrich_monitor_event(
            {
                "environment": "DEV",
                "category": "logs",
                "title": "Pico de erros nos logs",
                "severity": "warn",
                "recorded_at": "2026-07-10T12:00:00.000Z",
            },
            self.config,
        )
        self.assertIn("/monitoring/logs", event["investigationLink"])
        self.assertIn("DEV", event["investigationLink"])

    def test_build_monitoring_grouped_events(self) -> None:
        grouped = sm.build_monitoring_grouped_events(self.config, env_name="DEV", hours=24)
        self.assertIn("groups", grouped)
        self.assertGreaterEqual(grouped["totalEvents"], 1)
        self.assertGreaterEqual(len(grouped["groups"]), 1)

    def test_build_monitoring_event_detail(self) -> None:
        events = self.ops_store.query_monitor_events("DEV", db_path=self.db)
        detail = sm.build_monitoring_event_detail(self.config, "DEV", int(events[0]["id"]))
        self.assertIn("event", detail)
        self.assertIn("series", detail)
        self.assertEqual(detail["event"]["id"], events[0]["id"])

    @patch("server_monitoring._query_sync_logs")
    def test_build_monitoring_syncs_with_error(self, mock_sync) -> None:
        mock_sync.return_value = ([], "connection failed")
        result = sm.build_monitoring_syncs(self.config, "DEV")
        self.assertEqual(result["syncs"], [])
        self.assertEqual(result["error"], "connection failed")

    def test_build_monitoring_service_logs(self) -> None:
        self.ops_store.append_service_log(
            "DEV", "backend", "stderr", "ERROR something bad", db_path=self.db
        )
        logs = sm.build_monitoring_service_logs(self.config, "DEV", pattern="ERROR")
        self.assertGreaterEqual(len(logs["lines"]), 1)

    def test_collector_status_detail(self) -> None:
        detail = sm.build_monitoring_collector_status_detail(self.config)
        self.assertIn("threadAlive", detail)
        self.assertIn("samplesByEnv2h", detail)
        self.assertIn("recentErrors", detail)

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


class SpikeDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        sm._LATENCY_SPIKE_SAMPLES.clear()
        sm._RECENT_EVENT_KEYS.clear()

    @patch("server_monitoring._record_event")
    def test_spike_requires_three_consecutive_samples(self, mock_record) -> None:
        ops_store = sm._import_ops_store()
        db = Path(tempfile.mkdtemp()) / "ops-store.db"
        ops_store.init_store(db)
        sm._detect_health_spike(ops_store, db, "DEV", 600, True)
        sm._detect_health_spike(ops_store, db, "DEV", 650, True)
        titles_early = [c.args[5] for c in mock_record.call_args_list if len(c.args) > 5]
        self.assertFalse(any("Pico" in t for t in titles_early))
        sm._detect_health_spike(ops_store, db, "DEV", 700, True)
        titles = [c.args[5] for c in mock_record.call_args_list if len(c.args) > 5]
        self.assertTrue(any("Pico" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
