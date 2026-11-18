import contextlib
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server_monitoring as monitoring


class AvailabilityViewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE monitor_samples (id INTEGER PRIMARY KEY, environment TEXT, metric_key TEXT, value REAL, recorded_at TEXT, labels_json TEXT);
            CREATE TABLE monitor_events (id INTEGER PRIMARY KEY, environment TEXT, category TEXT, severity TEXT, title TEXT, recorded_at TEXT);
            CREATE TABLE deploy_runs (environment TEXT, run_id TEXT, status TEXT, result TEXT, started_at TEXT, finished_at TEXT);
        """)
        self.now = datetime.now(timezone.utc)
        self.patches = [
            patch.object(monitoring, "_import_ops_store", return_value=self),
            patch.object(monitoring, "resolve_ops_store_path", return_value=Path("unused")),
            patch.object(monitoring, "get_monitoring_settings", return_value={"collectorIntervalSec": 60, "retentionDays": 30}),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.db.close)

    @contextlib.contextmanager
    def get_connection(self, _path):
        yield self.db

    def sample(self, minutes, value, env="MAIN"):
        timestamp = (self.now - timedelta(minutes=minutes)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.db.execute("INSERT INTO monitor_samples(environment, metric_key, value, recorded_at) VALUES (?, 'health_reachable', ?, ?)", (env, value, timestamp))

    def test_window_totals_coverage_and_incident(self):
        self.sample(4, 1)
        self.sample(3, 0)
        self.sample(2, 0)
        self.sample(1, 1)
        self.sample(2, 0, "DEV")
        with patch.object(monitoring, "fetch_backend_api_metrics", return_value={"totals": {"requests": 100, "errors5xx": 2}}):
            result = monitoring.build_monitoring_availability({}, "MAIN", window="24h")
        self.assertEqual(result["api"]["availabilityPct"], 98.0)
        self.assertEqual(result["health"]["samples"], 4)
        self.assertEqual(result["health"]["uptimePct"], 50.0)
        self.assertEqual(len(result["history"]), 48)
        self.assertEqual(sum(bucket["samples"] for bucket in result["history"]), 4)
        self.assertEqual(sum(bucket["failures"] for bucket in result["history"]), 2)
        self.assertLess(result["health"]["coveragePct"], 1)
        incident = next(row for row in result["timeline"] if row["kind"] == "outage")
        self.assertEqual(incident["durationSec"], 120)

    def test_gap_and_unverified_period(self):
        self.sample(10, 1)
        self.sample(5, 0)
        self.sample(1, 1)
        with patch.object(monitoring, "fetch_backend_api_metrics", return_value={"totals": {"requests": 100, "errors5xx": 0}}):
            result = monitoring.build_monitoring_availability({}, "MAIN", window="30d")
        self.assertNotIn("availabilityPct", result["api"])
        self.assertIsNone(next(row for row in result["timeline"] if row["kind"] == "outage")["durationSec"])

    def test_confirmed_period_and_empty_traffic(self):
        def fetch(_config, _env, *, window, since, until):
            return {"since": since, "until": until, "totals": {"requests": 0, "errors5xx": 0}}
        with patch.object(monitoring, "fetch_backend_api_metrics", side_effect=fetch):
            result = monitoring.build_monitoring_availability({}, "MAIN", window="7d")
        self.assertTrue(result["api"]["periodVerified"])
        self.assertIsNone(result["health"]["uptimePct"])
        self.assertNotIn("availabilityPct", result["api"])

    def test_verified_window_includes_events_and_deploys(self):
        at = (self.now - timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.db.execute("INSERT INTO monitor_events VALUES (1, 'MAIN', 'availability', 'warn', 'health lento', ?)", (at,))
        self.db.execute("INSERT INTO deploy_runs VALUES ('MAIN', 'run-1', 'done', 'ok', ?, ?)", (at, at))
        def fetch(_config, _env, *, window, since, until):
            return {"since": since, "until": until, "totals": {"requests": 1000, "errors5xx": 1}}
        with patch.object(monitoring, "fetch_backend_api_metrics", side_effect=fetch):
            result = monitoring.build_monitoring_availability({}, "MAIN", window="7d")
        self.assertEqual(result["api"]["availabilityPct"], 99.9)
        self.assertEqual({row["kind"] for row in result["timeline"]}, {"event", "deploy"})

    def test_thirty_days_includes_more_than_ten_thousand_samples(self):
        rows = [("MAIN", 1, (self.now - timedelta(minutes=i)).isoformat(timespec="milliseconds").replace("+00:00", "Z")) for i in range(10005)]
        self.db.executemany("INSERT INTO monitor_samples(environment, metric_key, value, recorded_at) VALUES (?, 'health_reachable', ?, ?)", rows)
        with patch.object(monitoring, "fetch_backend_api_metrics", return_value={"error": "unavailable"}):
            result = monitoring.build_monitoring_availability({}, "MAIN", window="30d")
        self.assertEqual(result["health"]["samples"], 10005)

    def test_gap_splits_episodes_and_bounds_downtime(self):
        self.sample(12, 1)
        self.sample(11, 0)
        self.sample(5, 0)
        self.sample(4, 1)
        since = (self.now - timedelta(minutes=13)).isoformat()
        with patch.object(monitoring, 'fetch_backend_api_metrics') as api:
            result = monitoring.build_monitoring_availability_detail({}, 'MAIN', since=since, until=self.now.isoformat())
        api.assert_not_called()
        self.assertEqual(result['impact']['incidentCount'], 2)
        self.assertEqual(result['impact']['estimatedDowntimeSec'], 120)
        self.assertEqual(result['impact']['unknownSec'], 9 * 60)
        self.assertEqual(sum(1 for item in result['timeline'] if item['kind'] == 'outage'), 2)

    def test_impact_not_limited_to_timeline_and_zero_is_not_missing(self):
        for i in range(110):
            self.sample(i * 2 + 1, 0)
            self.sample(i * 2 + 2, 1)
        with patch.object(monitoring, 'fetch_backend_api_metrics', return_value={'totals': {'requests': 50, 'errors5xx': 0}}):
            result = monitoring.build_monitoring_availability({}, 'MAIN')
        self.assertEqual(result['impact']['incidentCount'], 110)
        self.assertEqual(result['impact']['failedRequests'], 0)
        self.assertEqual(len(result['timeline']), 100)
        self.assertTrue(result['timelineTruncated'])

    def test_predecessor_is_clipped_to_window(self):
        self.sample(5, 0)
        self.sample(4, 1)
        start = self.now - timedelta(minutes=4, seconds=30)
        result = monitoring.build_monitoring_availability_detail({}, 'MAIN', since=start.isoformat(), until=self.now.isoformat())
        self.assertEqual(result['impact']['estimatedDowntimeSec'], 30)
        self.assertEqual(result['impact']['incidentCount'], 1)

    def test_detail_rejects_invalid_windows_and_reports_missing_data(self):
        for since, until in [('invalid', 'invalid'), ('2026-01-01', '2026-01-02'),
                             (self.now.isoformat(), (self.now - timedelta(hours=1)).isoformat()),
                             ((self.now - timedelta(days=31)).isoformat(), self.now.isoformat())]:
            with self.assertRaises(ValueError):
                monitoring.build_monitoring_availability_detail({}, 'MAIN', since=since, until=until)
        result = monitoring.build_monitoring_availability_detail({}, 'MAIN', since=(self.now - timedelta(hours=1)).isoformat(), until=self.now.isoformat())
        self.assertIsNone(result['impact']['incidentCount'])
        self.assertIsNone(result['impact']['estimatedDowntimeSec'])
        self.assertEqual(result['impact']['unknownSec'], 3600)

    def test_current_state_uses_nonblocking_coordinator_and_age(self):
        coordinator = SimpleNamespace(cfg=SimpleNamespace(stale_window_sec=120))
        for state, age, disabled, expected in [('healthy', 1000, False, 'healthy'), ('offline', 1000, False, 'offline'),
                                               ('saturated', 1000, False, 'saturated'), ('healthy', 180000, False, 'stale'),
                                               ('offline', 1000, True, 'disabled')]:
            from unittest.mock import Mock
            coordinator.get_snapshot = Mock(return_value={'availabilityClass': state, 'ageMs': age,
                                                         'checkedAt': self.now.isoformat(), 'envDisabled': disabled})
            with patch('health_probe.get_coordinator', return_value=coordinator):
                result = monitoring.build_monitoring_availability_current({}, 'MAIN')
            self.assertEqual(result['state'], expected)
            coordinator.get_snapshot.assert_called_once_with({}, 'MAIN', wait=False, force=False)

    def test_api_series_accepts_exact_interval_instead_of_a_relative_window(self):
        since = (self.now - timedelta(days=2, hours=1)).isoformat()
        until = (self.now - timedelta(days=2)).isoformat()
        with patch.object(self, 'query_monitor_series_bucketed', create=True, return_value=[
            {'recorded_at': since, 'value': 120, 'labels': {}},
            {'recorded_at': until, 'value': 180, 'labels': {}},
        ]) as query:
            result = monitoring.build_monitoring_series({}, 'MAIN', 'api_avg_ms', range_since=since, range_until=until)
        self.assertEqual(monitoring._parse_iso_dt(result['windowFrom']).replace(microsecond=0), monitoring._parse_iso_dt(since).replace(microsecond=0))
        self.assertEqual(monitoring._parse_iso_dt(result['windowTo']).replace(microsecond=0), monitoring._parse_iso_dt(until).replace(microsecond=0))
        self.assertEqual(result['pointCount'], 2)
        self.assertEqual(query.call_args.kwargs['limit'], 800)
        with self.assertRaises(ValueError):
            monitoring.build_monitoring_series({}, 'MAIN', 'api_avg_ms', range_since=since)

    def labeled_sample(self, seconds_ago, value=1, interval=30, tolerance=2):
        at = (self.now - timedelta(seconds=seconds_ago)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        self.db.execute("INSERT INTO monitor_samples(environment, metric_key, value, recorded_at, labels_json) VALUES ('MAIN', 'health_reachable', ?, ?, ?)",
                        (value, at, json.dumps({'intervalSec': interval, 'jitterToleranceSec': tolerance})))

    def detail(self, seconds):
        return monitoring.build_monitoring_availability_detail({}, 'MAIN',
            since=(self.now - timedelta(seconds=seconds)).isoformat(), until=self.now.isoformat())

    def test_coverage_duration_and_bars_share_one_definition(self):
        for seconds in range(30, 1801, 30):
            self.labeled_sample(seconds)
        result = self.detail(1800)
        self.assertAlmostEqual(result['health']['coveragePct'], 100, places=1)
        self.assertEqual(result['impact']['unknownSec'], 0)
        self.assertEqual(len(result['history']), 60)
        self.assertTrue(all(bucket['coveragePct'] > 99 for bucket in result['history']))
        self.assertAlmostEqual(sum(bucket['observedSec'] for bucket in result['history']), result['impact']['observedSec'], places=1)
        self.assertEqual(result['health']['actualIntervalSec'], 30)

    def test_legacy_cadence_is_preserved_and_detail_has_no_half_interval_holes(self):
        for minutes in range(1, 31):
            self.sample(minutes, 1)
        result = self.detail(1800)
        self.assertEqual(len(result['history']), 30)
        self.assertEqual(result['health']['recordedIntervalsSec'], [60])
        self.assertEqual(result['health']['coveragePct'], 100)
        self.assertEqual(result['impact']['unknownSec'], 0)

    def test_real_gap_is_unknown_and_bars_are_partial(self):
        self.labeled_sample(120)
        self.labeled_sample(30)
        result = self.detail(120)
        self.assertEqual(result['health']['coveragePct'], 50)
        self.assertEqual(result['impact']['unknownSec'], 60)
        self.assertEqual(result['health']['largestGapSec'], 60)
        self.assertEqual(result['health']['actualIntervalSec'], 90)
        self.assertEqual(result['health']['lastSampleAgeSec'], 30)

    def test_jitter_only_bridged_between_actual_observations(self):
        self.labeled_sample(62, value=0)
        self.labeled_sample(31)
        result = self.detail(62)
        self.assertEqual(result['impact']['unknownSec'], 1)
        self.assertEqual(result['impact']['estimatedDowntimeSec'], 31)
        self.assertEqual(result['health']['largestGapSec'], 1)

    def test_interval_change_does_not_change_legacy_or_recorded_coverage(self):
        self.sample(5, 1)  # historical 60s observation
        self.labeled_sample(180, interval=30)
        self.labeled_sample(60, interval=30)
        result = self.detail(300)
        with patch.object(monitoring, 'get_monitoring_settings', return_value={'availabilityIntervalSec': 15}):
            changed = self.detail(300)
        self.assertEqual(result['impact'], changed['impact'])
        self.assertEqual(result['health']['coveragePct'], changed['health']['coveragePct'])
        self.assertEqual(result['health']['recordedIntervalsSec'], [30, 60])


if __name__ == "__main__":
    unittest.main()
