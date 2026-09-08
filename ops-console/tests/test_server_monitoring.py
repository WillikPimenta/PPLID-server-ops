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

    def test_build_monitoring_series_prefers_newest(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        # Many old samples would formerly fill ASC LIMIT and hide today's points
        for i in range(50):
            ts = (now - timedelta(days=5, minutes=50 - i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            self.ops_store.insert_monitor_sample(
                "MAIN", "health_latency_ms", 100.0, recorded_at=ts, db_path=self.db
            )
        recent_ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.ops_store.insert_monitor_sample(
            "MAIN", "health_latency_ms", 999.0, recorded_at=recent_ts, db_path=self.db
        )
        series = sm.build_monitoring_series(self.config, "MAIN", "health_latency_ms", hours=168)
        self.assertTrue(series["points"])
        self.assertEqual(series["lastSampleAt"], recent_ts)
        self.assertEqual(float(series["points"][-1]["v"]), 999.0)

    def test_clear_monitoring_events(self) -> None:
        before = self.ops_store.query_monitor_events("DEV", db_path=self.db)
        self.assertGreaterEqual(len(before), 1)
        result = sm.clear_monitoring_events(self.config)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["deleted"], 1)
        after = self.ops_store.query_monitor_events("DEV", db_path=self.db)
        self.assertEqual(len(after), 0)

    def test_build_monitoring_uptime_hours(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        for h in (8, 9, 14):
            ts = now.replace(hour=h, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            self.ops_store.insert_monitor_sample(
                "HOM", "health_latency_ms", 150.0 + h, recorded_at=ts, db_path=self.db
            )
            self.ops_store.insert_monitor_sample(
                "HOM", "health_reachable", 1.0, recorded_at=ts, db_path=self.db
            )
        result = sm.build_monitoring_uptime_hours(self.config, "HOM", date=day)
        self.assertEqual(result["date"], day)
        self.assertEqual(len(result["hourBars"]), 24)
        self.assertTrue(any(b["avgMs"] is not None for b in result["hourBars"]))
        json.dumps(result)

    def test_build_monitoring_config(self) -> None:
        cfg = sm.build_monitoring_config(self.config)
        self.assertEqual(cfg["retentionDays"], 7)
        self.assertIn("MAIN", cfg["environments"])
        self.assertIn("slos", cfg)
        self.assertEqual(cfg["slos"]["healthP95WarnMs"], 2000)
        self.assertEqual(cfg["slos"]["healthP95CriticalMs"], 3000)
        self.assertIn("collectorStatus", cfg)
        self.assertIn("generatedAt", cfg)

    @patch.object(sm, "fetch_backend_api_metrics")
    def test_build_monitoring_api_routes_exposes_exact_traffic(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "sampling": {"mode": "priority_sample", "normalRatePct": 10},
            "totals": {"requests": 3, "sampleCount": 3, "status4xx": 2},
            "slowRoutes": [{"method": "GET", "route": "/api/v1/dashboard/", "count": 3}],
            "routeStats": [
                {
                    "method": "GET",
                    "route": "/api/v1/dashboard/",
                    "sampleCount": 3,
                    "successSamples": 1,
                    "status4xx": 2,
                    "successAvgMs": 25.0,
                    "clientErrorAvgMs": 4.0,
                }
            ],
            "traffic": {
                "available": True,
                "source": "exact_aggregate",
                "activeUsers": {"count": 81, "windowMinutes": 5},
                "totals": {"requests": 42, "peakRpm": 7, "activeUsersNow": 81},
                "points": [{"at": "2026-08-10T12:00:00Z", "requests": 7, "rpm": 7}],
                "topRoutes": [{"method": "GET", "route": "/api/v1/dashboard/", "requests": 42}],
            },
        }

        result = sm.build_monitoring_api_routes(self.config, "DEV", window="1h")

        self.assertEqual(result["instrumentation"], "active")
        self.assertEqual(result["traffic"]["totals"]["requests"], 42)
        self.assertEqual(result["traffic"]["source"], "exact_aggregate")
        self.assertEqual(result["traffic"]["activeUsers"]["peakCount"], 81)
        self.assertEqual(result["traffic"]["activeUsers"]["peakRetentionDays"], 7)
        self.assertEqual(result["sampling"]["normalRatePct"], 10)
        self.assertEqual(result["routeStats"][0]["status4xx"], 2)

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

    def test_grouped_events_lite_skips_correlate(self) -> None:
        with patch.object(sm, "_correlate_event") as mock_corr:
            lite = sm.build_monitoring_grouped_events_lite(self.config, env_name="DEV", hours=24)
        self.assertTrue(lite.get("lite"))
        self.assertIn("groups", lite)
        self.assertIn("timingMs", lite)
        mock_corr.assert_not_called()

    def test_grouped_events_full_enriches(self) -> None:
        with patch.object(sm, "_correlate_event", return_value=[]) as mock_corr:
            full = sm.build_monitoring_grouped_events(self.config, env_name="DEV", hours=24)
        self.assertFalse(full.get("lite"))
        self.assertIn("groups", full)
        # Full path goes through build_monitoring_events -> _enrich_monitor_event
        self.assertGreaterEqual(mock_corr.call_count, 1)

    def test_build_monitoring_event_detail(self) -> None:
        events = self.ops_store.query_monitor_events("DEV", db_path=self.db)
        detail = sm.build_monitoring_event_detail(self.config, "DEV", int(events[0]["id"]))
        self.assertIn("event", detail)
        self.assertIn("series", detail)
        self.assertEqual(detail["event"]["id"], events[0]["id"])

    def test_offline_window_and_event_detail(self) -> None:
        from datetime import datetime, timedelta, timezone

        base = datetime.now(timezone.utc) - timedelta(minutes=30)
        for i in range(10):
            ts = (base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            reachable = 0.0 if 3 <= i <= 7 else 1.0
            self.ops_store.insert_monitor_sample(
                "MAIN",
                "health_reachable",
                reachable,
                recorded_at=ts,
                db_path=self.db,
            )
        mid = (base + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        eid = self.ops_store.insert_monitor_event(
            "MAIN",
            "critical",
            "availability",
            "Backend offline",
            detail="Health check falhou · erro: Connection refused",
            recorded_at=mid,
            db_path=self.db,
        )
        detail = sm.build_monitoring_event_detail(self.config, "MAIN", eid)
        self.assertIsNotNone(detail.get("offline"))
        self.assertGreater(detail["offline"]["offlineDurationSec"], 0)
        self.assertIn("offlineDurationLabel", detail["event"])

    def test_build_monitoring_uptime_days(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for i in range(5):
            ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            self.ops_store.insert_monitor_sample(
                "HOM", "health_reachable", 1.0, recorded_at=ts, db_path=self.db
            )
            self.ops_store.insert_monitor_sample(
                "HOM", "health_latency_ms", 120.0, recorded_at=ts, db_path=self.db
            )
        result = sm.build_monitoring_uptime_days(self.config, "HOM", days=7)
        self.assertEqual(result["environment"], "HOM")
        self.assertEqual(len(result["dayBars"]), 7)
        self.assertTrue(any(d["samples"] > 0 for d in result["dayBars"]))
        json.dumps(result)

    @patch("server_monitoring._query_sync_logs")
    def test_build_monitoring_syncs_with_error(self, mock_sync) -> None:
        mock_sync.return_value = ([], "connection failed")
        result = sm.build_monitoring_syncs(self.config, "DEV")
        self.assertEqual(result["syncs"], [])
        self.assertEqual(result["error"], "connection failed")

    def test_sync_row_json_serializable(self) -> None:
        from datetime import datetime, timezone
        from decimal import Decimal

        import server_db as sdb

        raw_row = {
            "kind": "prod",
            "started_at": datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 7, 15, 8, 1, tzinfo=timezone.utc),
            "duration_seconds": Decimal("12.5"),
            "success": True,
            "message": "ok",
            "row_count": 10,
            "trigger_source": "cron",
        }
        item = {k: sdb.serialize_value(v) for k, v in raw_row.items()}
        item["source"] = "produtividade"
        item["startedAt"] = raw_row["started_at"].isoformat()
        item["finishedAt"] = raw_row["finished_at"].isoformat()
        json.dumps([item])  # must not raise
        self.assertIsInstance(item["started_at"], str)
        self.assertIsInstance(item["duration_seconds"], str)
        self.assertIn("T", item["startedAt"])

    def test_build_monitoring_service_logs(self) -> None:
        self.ops_store.append_service_log(
            "DEV", "backend", "stderr", "ERROR something bad", db_path=self.db
        )
        self.ops_store.append_service_log(
            "DEV", "backend", "out", "Start concluido.", db_path=self.db
        )
        only_err = sm.build_monitoring_service_logs(self.config, "DEV", pattern="ERROR")
        self.assertGreaterEqual(len(only_err["lines"]), 1)
        self.assertTrue(all("ERROR" in (ln.get("line") or "").upper() for ln in only_err["lines"]))

        all_lines = sm.build_monitoring_service_logs(self.config, "DEV", pattern=None)
        self.assertGreaterEqual(len(all_lines["lines"]), 2)
        blank = sm.build_monitoring_service_logs(self.config, "DEV", pattern="")
        self.assertGreaterEqual(len(blank["lines"]), 2)

    def test_service_logs_pattern_error_can_be_empty_while_plain_lines_exist(self) -> None:
        """Regressão: aba Logs vazia quando o default era pattern=ERROR sem ERRORs recentes."""
        self.ops_store.append_service_log(
            "HOM", "deploy", "out", "[info] Start concluido.", db_path=self.db
        )
        with_error = sm.build_monitoring_service_logs(self.config, "HOM", pattern="ERROR")
        without = sm.build_monitoring_service_logs(self.config, "HOM", pattern=None)
        self.assertEqual(len(with_error["lines"]), 0)
        self.assertGreaterEqual(len(without["lines"]), 1)

    def test_service_logs_narrow_since_excludes_older_lines(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.ops_store.append_service_log(
            "MAIN", "deploy", "out", "linha antiga", logged_at=old, db_path=self.db
        )
        since_future = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        empty = sm.build_monitoring_service_logs(
            self.config, "MAIN", since=since_future, pattern=None
        )
        self.assertEqual(len(empty["lines"]), 0)
        ok = sm.build_monitoring_service_logs(
            self.config, "MAIN", since=old, pattern=None
        )
        self.assertGreaterEqual(len(ok["lines"]), 1)
        self.assertEqual(ok["lines"][0].get("logged_at"), old)

    def test_service_logs_structured_filters_and_facets(self) -> None:
        self.ops_store.append_service_log(
            "DEV", "backend", "stderr", "ERROR request 123 failed", db_path=self.db
        )
        self.ops_store.append_service_log(
            "DEV", "frontend", "out", "WARNING bundle is large", db_path=self.db
        )
        self.ops_store.append_service_log(
            "DEV", "backend", "out", "INFO request 123 completed", db_path=self.db
        )

        payload = sm.build_monitoring_service_logs(
            self.config,
            "DEV",
            q="request 123",
            levels=["ERROR"],
            services=["backend"],
            streams=["stderr"],
        )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["lines"][0]["level"], "ERROR")
        self.assertEqual(payload["lines"][0]["service"], "backend")
        self.assertTrue(payload["lines"][0]["key"])
        self.assertIn("backend", payload["facets"]["services"])
        self.assertEqual(payload["summary"]["byLevel"]["ERROR"], 1)

    def test_service_logs_cursor_pages_without_duplicates(self) -> None:
        for index in range(5):
            self.ops_store.append_service_log(
                "MAIN",
                "deploy",
                "out",
                f"INFO page line {index}",
                logged_at=f"2026-08-11T12:00:0{index}.000Z",
                db_path=self.db,
            )

        first = sm.build_monitoring_service_logs(
            self.config,
            "MAIN",
            since="2026-08-11T11:59:00.000Z",
            limit=2,
            order="asc",
        )
        second = sm.build_monitoring_service_logs(
            self.config,
            "MAIN",
            since="2026-08-11T11:59:00.000Z",
            cursor=first["nextCursor"],
            limit=2,
            order="asc",
        )

        self.assertTrue(first["hasMore"])
        self.assertTrue(first["nextCursor"])
        self.assertTrue(set(line["key"] for line in first["lines"]).isdisjoint(
            line["key"] for line in second["lines"]
        ))
        self.assertLess(first["lines"][0]["logged_at"], first["lines"][1]["logged_at"])

    def test_service_logs_export_csv_and_json(self) -> None:
        self.ops_store.append_service_log(
            "HOM", "backend", "stderr", 'ERROR value;with separator', db_path=self.db
        )

        csv_body, csv_type, csv_name = sm.build_monitoring_service_logs_export(
            self.config, "HOM", export_format="csv", levels=["ERROR"]
        )
        json_body, json_type, json_name = sm.build_monitoring_service_logs_export(
            self.config, "HOM", export_format="json", levels=["ERROR"]
        )

        self.assertIn("text/csv", csv_type)
        self.assertTrue(csv_name.endswith(".csv"))
        self.assertIn('"ERROR value;with separator"', csv_body.decode("utf-8-sig"))
        parsed = json.loads(json_body.decode("utf-8"))
        self.assertIn("application/json", json_type)
        self.assertTrue(json_name.endswith(".json"))
        self.assertEqual(parsed["count"], 1)
        self.assertEqual(parsed["lines"][0]["level"], "ERROR")

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


class HourlyProductivityStatusTests(unittest.TestCase):
    def test_builds_status_table_from_latest_cycle(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            log_path = Path(tmp) / "production.log"
            log_path.write_text(
                "\n".join(
                    [
                        "[2026-08-11 12:00:00] STATUS|Producao: iniciando ciclo 4",
                        "[2026-08-11 12:01:00] STATUS|✓ Confer: download concluído com sucesso",
                        "[2026-08-11 12:01:10] STATUS|[CONFER] Log Eventos baixado",
                        "[2026-08-11 12:02:00] STATUS|✓ BRFlow: download concluído com sucesso",
                        "[2026-08-11 12:02:10] STATUS|[BRFLOW] Monitor de eventos tratado salvo",
                        "[2026-08-11 12:03:00] PRODUTIVIDADE_CASE_SAVED|prod_hora|C:/relatorio.xlsx",
                        "[2026-08-11 12:03:20] CASE_FILA_SAVED|C:/fila.json",
                    ]
                ),
                encoding="utf-8",
            )
            result = sm.build_hourly_productivity_status(
                {"productivityMonitoring": {"productionLog": str(log_path)}}
            )

        by_system = {row["system"]: row for row in result["systems"]}
        self.assertTrue(result["sourceAvailable"])
        self.assertEqual(by_system["BRFlow"]["production"]["status"], "ok")
        self.assertEqual(by_system["BRFlow"]["monitor"]["status"], "ok")
        self.assertEqual(by_system["Confer"]["production"]["status"], "ok")
        self.assertEqual(by_system["Confer"]["monitor"]["status"], "ok")
        self.assertEqual(by_system["Case Manager"]["production"]["status"], "ok")
        self.assertEqual(by_system["Case Manager"]["monitor"]["status"], "ok")
        self.assertEqual(result["cycleStatus"], "stale")
        case_steps = {step["label"]: step for step in by_system["Case Manager"]["steps"]}
        self.assertEqual(case_steps["Produtividade por hora"]["status"], "completed")
        self.assertEqual(case_steps["Fila em aberto"]["status"], "completed")

    def test_missing_log_returns_pending_rows(self) -> None:
        result = sm.build_hourly_productivity_status(
            {"productivityMonitoring": {"productionLog": "C:/missing/production.log"}}
        )
        self.assertFalse(result["sourceAvailable"])
        self.assertEqual(len(result["systems"]), 3)
        self.assertTrue(all(row["production"]["status"] == "pending" for row in result["systems"]))


class SyncStatusTests(unittest.TestCase):
    def test_success_after_failure_recovers_same_source_and_kind(self) -> None:
        rows = [
            {
                "source": "monitor_eventos",
                "kind": "monitor",
                "startedAt": "2026-08-13T10:00:00-03:00",
                "finishedAt": "2026-08-13T10:00:05-03:00",
                "success": False,
            },
            {
                "source": "monitor_eventos",
                "kind": "monitor",
                "startedAt": "2026-08-13T10:05:00-03:00",
                "finishedAt": "2026-08-13T10:05:05-03:00",
                "success": True,
            },
        ]

        status = sm._summarize_sync_status(rows)

        self.assertEqual(status["activeFailureCount"], 0)
        self.assertEqual(status["status"], "ok")

    def test_only_latest_terminal_execution_per_source_kind_is_active(self) -> None:
        rows = [
            {
                "source": "produtividade",
                "kind": "prod",
                "startedAt": "2026-08-13T13:00:00Z",
                "finishedAt": "2026-08-13T13:00:05Z",
                "success": False,
            },
            {
                "source": "produtividade",
                "kind": "prod",
                "startedAt": "2026-08-13T13:05:00Z",
                "finishedAt": None,
                "success": False,
            },
            {
                "source": "monitor_eventos",
                "kind": "monitor",
                "startedAt": "2026-08-13T13:01:00Z",
                "finishedAt": "2026-08-13T13:01:05Z",
                "success": True,
            },
        ]

        status = sm._summarize_sync_status(rows)

        self.assertEqual(status["activeFailureCount"], 1)
        self.assertEqual(status["activeFailures"][0]["source"], "produtividade")


class SpikeDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        sm._LATENCY_SPIKE_SAMPLES.clear()
        sm._RECENT_EVENT_KEYS.clear()
        sm._CONFIG_CACHE = None

    @patch("server_monitoring._record_event")
    def test_spike_requires_three_consecutive_samples(self, mock_record) -> None:
        ops_store = sm._import_ops_store()
        db = Path(tempfile.mkdtemp()) / "ops-store.db"
        ops_store.init_store(db)
        # Below warn (2000): no pico; need 3 consecutive samples above threshold
        sm._detect_health_spike(ops_store, db, "DEV", 2100, True)
        sm._detect_health_spike(ops_store, db, "DEV", 2200, True)
        titles_early = [c.args[5] for c in mock_record.call_args_list if len(c.args) > 5]
        self.assertFalse(any("Pico" in t for t in titles_early))
        sm._detect_health_spike(ops_store, db, "DEV", 2300, True)
        titles = [c.args[5] for c in mock_record.call_args_list if len(c.args) > 5]
        self.assertTrue(any("Pico" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
