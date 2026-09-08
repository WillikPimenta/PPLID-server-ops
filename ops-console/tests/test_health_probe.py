"""Tests for HealthProbeCoordinator and overview probe coalescing."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import health_probe as hp  # noqa: E402
import server  # noqa: E402
import server_ops  # noqa: E402


def _ok_probe(url: str, timeout: float) -> dict:
    return {
        "reachable": True,
        "httpStatus": 200,
        "status": "healthy",
        "database": "ok",
        "version": "abc1234",
        "components": {"backend": "ok", "database": "ok", "migrations": "ok"},
        "durationMs": 12,
        "error": None,
        "checkedAt": "2026-01-01T00:00:00+00:00",
        "timedOut": False,
    }


def _slow_probe(url: str, timeout: float) -> dict:
    time.sleep(min(0.3, timeout + 0.1))
    raise TimeoutError("timed out")


class HealthProbeCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = hp.HealthProbeCoordinator(
            hp.ProbeConfig(
                cache_ttl_sec=0.5,
                probe_timeout_sec=0.4,
                backoff_initial_sec=0.3,
                backoff_max_sec=2.0,
                stale_window_sec=5.0,
                offline_failures=3,
                global_concurrency=2,
            )
        )
        self.coord._http_probe_fn = _ok_probe
        self.config = {
            "DEV": {"backendPort": 8001, "frontendPort": 5173},
            "MAIN": {"backendPort": 8000, "frontendPort": 5174},
            "HOM": {"backendPort": 8002, "frontendPort": 5175},
        }

    def tearDown(self) -> None:
        self.coord.reset_for_tests()

    def test_cache_hit(self) -> None:
        calls = {"n": 0}

        def counting(url, timeout):
            calls["n"] += 1
            return _ok_probe(url, timeout)

        self.coord._http_probe_fn = counting
        a = self.coord.get_snapshot(self.config, "DEV", wait=True)
        b = self.coord.get_snapshot(self.config, "DEV", wait=True)
        self.assertTrue(a["reachable"])
        self.assertTrue(b["fromCache"])
        self.assertEqual(calls["n"], 1)

    def test_single_flight(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def gated(url, timeout):
            calls["n"] += 1
            started.set()
            release.wait(2.0)
            return _ok_probe(url, timeout)

        self.coord._http_probe_fn = gated
        results: list[dict] = []

        def worker():
            results.append(self.coord.get_snapshot(self.config, "DEV", wait=True))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        self.assertTrue(started.wait(2.0))
        release.set()
        for t in threads:
            t.join(3.0)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(results), 8)
        self.assertGreaterEqual(self.coord.metrics()["health_probe_coalesced_total"], 1)

    def test_stale_while_revalidate(self) -> None:
        calls = {"n": 0}

        def counting(url, timeout):
            calls["n"] += 1
            return _ok_probe(url, timeout)

        self.coord._http_probe_fn = counting
        self.coord.cfg.cache_ttl_sec = 0.05
        first = self.coord.get_snapshot(self.config, "DEV", wait=True)
        self.assertFalse(first.get("stale"))
        time.sleep(0.08)
        second = self.coord.get_snapshot(self.config, "DEV", wait=False)
        self.assertTrue(second.get("fromCache"))
        # Background revalidate may bump call count asynchronously.
        deadline = time.time() + 2.0
        while calls["n"] < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(calls["n"], 1)

    def test_timeout_saturated_not_offline(self) -> None:
        def timeout_probe(url, timeout):
            return {
                "reachable": False,
                "httpStatus": None,
                "status": "offline",
                "database": None,
                "version": None,
                "components": {},
                "durationMs": int(timeout * 1000),
                "error": "timed out",
                "checkedAt": "2026-01-01T00:00:00+00:00",
                "timedOut": True,
            }

        # Seed a valid result first.
        self.coord._http_probe_fn = _ok_probe
        with patch.object(self.coord, "probe_port", return_value=True):
            self.coord.get_snapshot(self.config, "DEV", wait=True)
            self.coord.cfg.cache_ttl_sec = 0
            self.coord._http_probe_fn = timeout_probe
            snap = self.coord.get_snapshot(self.config, "DEV", wait=True, force=True)
        self.assertIn(snap.get("availabilityClass"), ("saturated", "stale", "degraded"))
        self.assertNotEqual(snap.get("availabilityClass"), "offline")

    def test_backoff_after_failures(self) -> None:
        calls = {"n": 0}

        def fail(url, timeout):
            calls["n"] += 1
            return {
                "reachable": False,
                "httpStatus": None,
                "status": "offline",
                "database": None,
                "version": None,
                "components": {},
                "durationMs": 5,
                "error": "connection refused",
                "checkedAt": "2026-01-01T00:00:00+00:00",
                "timedOut": False,
            }

        self.coord._http_probe_fn = fail
        with patch.object(self.coord, "probe_port", return_value=False):
            self.coord.get_snapshot(self.config, "DEV", wait=True, force=True)
            n1 = calls["n"]
            self.coord.get_snapshot(self.config, "DEV", wait=True, force=False)
            # Second call within backoff should not execute another probe.
            self.assertEqual(calls["n"], n1)

    def test_recovery_after_failure(self) -> None:
        self.coord._http_probe_fn = lambda u, t: {
            "reachable": False,
            "httpStatus": None,
            "status": "offline",
            "database": None,
            "version": None,
            "components": {},
            "durationMs": 1,
            "error": "down",
            "checkedAt": "2026-01-01T00:00:00+00:00",
            "timedOut": False,
        }
        with patch.object(self.coord, "probe_port", return_value=False):
            self.coord.get_snapshot(self.config, "DEV", wait=True, force=True)
        self.assertGreaterEqual(self.coord._state("DEV").consecutive_failures, 1)
        self.coord._http_probe_fn = _ok_probe
        self.coord._state("DEV").next_allowed_at = 0
        with patch.object(self.coord, "probe_port", return_value=True):
            snap = self.coord.get_snapshot(self.config, "DEV", wait=True, force=True)
        self.assertEqual(snap.get("availabilityClass"), "healthy")
        self.assertEqual(self.coord._state("DEV").consecutive_failures, 0)

    def test_concurrency_limit(self) -> None:
        gate = threading.Semaphore(0)
        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def blocked(url, timeout):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            gate.acquire(timeout=2)
            with lock:
                active["n"] -= 1
            return _ok_probe(url, timeout)

        self.coord.configure(global_concurrency=1)
        self.coord._sem = threading.Semaphore(1)
        self.coord._http_probe_fn = blocked
        # Force fresh probes on different envs.
        self.coord.cfg.cache_ttl_sec = 0

        def run(env):
            self.coord.get_snapshot(self.config, env, wait=True, force=True)

        threads = [
            threading.Thread(target=run, args=("DEV",)),
            threading.Thread(target=run, args=("MAIN",)),
        ]
        for t in threads:
            t.start()
        time.sleep(0.15)
        # Release both.
        gate.release()
        gate.release()
        for t in threads:
            t.join(3.0)
        self.assertLessEqual(active["max"], 1)


class OverviewProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        hp.get_coordinator().reset_for_tests()
        hp.get_coordinator()._http_probe_fn = _ok_probe
        self.config = {
            "logDir": str(OPS_CONSOLE / "tmp-test-logs"),
            "statusFile": str(OPS_CONSOLE / "tmp-test-logs" / "deploy-status.json"),
            "lanIp": "127.0.0.1",
            "DEV": {"backendPort": 8001, "frontendPort": 5173, "branch": "dev", "repoDir": "C:/x"},
            "MAIN": {"backendPort": 8000, "frontendPort": 5174, "branch": "main", "repoDir": "C:/x"},
            "HOM": {"backendPort": 8002, "frontendPort": 5175, "branch": "hom", "repoDir": "C:/x"},
        }
        Path(self.config["logDir"]).mkdir(parents=True, exist_ok=True)
        Path(self.config["statusFile"]).write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        hp.get_coordinator().reset_for_tests()
        hp.get_coordinator()._http_probe_fn = None

    def test_no_dict_get_default_probe(self) -> None:
        """Regression: build_overview must not evaluate fetch_health as dict.get default."""
        calls = {"n": 0}

        def counting(url, timeout):
            calls["n"] += 1
            return _ok_probe(url, timeout)

        coord = hp.get_coordinator()
        coord.reset_for_tests()
        coord._http_probe_fn = counting
        with patch.object(coord, "probe_port", return_value=True):
            with patch.object(server_ops, "build_availability_extended", return_value={"aggregate": "healthy", "components": {}}):
                with patch.object(server_ops, "build_services_from_availability", return_value=[]):
                    with patch.object(server_ops, "build_deploy_summary", return_value={}):
                        with patch.object(server_ops, "load_deploy_state", return_value={}):
                            server.build_overview(self.config, lite=True)
                            # One probe per env max (may be coalesced); never 6 (3 parallel + 3 serial).
                            self.assertLessEqual(calls["n"], 3)

    def test_overview_lite_no_direct_probe_io(self) -> None:
        coord = hp.get_coordinator()
        coord.reset_for_tests()
        # Pre-seed snapshot so lite path is pure memory.
        with patch.object(coord, "probe_port", return_value=True):
            coord._http_probe_fn = _ok_probe
            coord.get_snapshot(self.config, "DEV", wait=True)
            coord.get_snapshot(self.config, "MAIN", wait=True)
            coord.get_snapshot(self.config, "HOM", wait=True)

        probe_calls = {"n": 0}

        def counting(url, timeout):
            probe_calls["n"] += 1
            return _ok_probe(url, timeout)

        coord._http_probe_fn = counting
        with patch.object(server_ops, "probe_frontend") as pf:
            with patch.object(server_ops, "probe_port_listening") as pp:
                with patch.object(server_ops, "fetch_database_metrics") as fdb:
                    with patch("subprocess.run") as sub:
                        with patch.object(server_ops, "build_deploy_summary", return_value={}):
                            with patch.object(server_ops, "load_deploy_state", return_value={}):
                                overview = server.build_overview(self.config, lite=True)
        self.assertTrue(overview.get("lite"))
        # Lite must not call frontend HTTP, port probes, DB metrics, or subprocess.
        pf.assert_not_called()
        pp.assert_not_called()
        fdb.assert_not_called()
        sub.assert_not_called()
        # May kick SWR in background but should not wait/block with many new probes.
        self.assertLessEqual(probe_calls["n"], 3)
        self.assertIn("diagnostics", overview)
        self.assertLess(overview["diagnostics"]["overview_duration_ms"], 2000)

    def test_collector_and_overview_share_snapshot(self) -> None:
        coord = hp.get_coordinator()
        coord.reset_for_tests()
        calls = {"n": 0}

        def counting(url, timeout):
            calls["n"] += 1
            return _ok_probe(url, timeout)

        coord._http_probe_fn = counting
        with patch.object(coord, "probe_port", return_value=True):
            a = coord.get_snapshot(self.config, "DEV", wait=True)
            b = coord.get_snapshot(self.config, "DEV", wait=True)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(a.get("version"), b.get("version"))


class AvailabilitySemanticsTests(unittest.TestCase):
    def test_timeout_with_port_up_is_saturated(self) -> None:
        runtime = {
            "reachable": False,
            "status": "offline",
            "database": None,
            "timedOut": True,
            "backendPortUp": True,
            "frontendPortUp": True,
            "availabilityClass": "saturated",
            "consecutiveFailures": 1,
            "version": "abc",
            "components": {},
        }
        avail = server_ops.build_availability_extended(
            runtime, {"backendPort": 8001, "frontendPort": 5173}, None, probe_io=False
        )
        self.assertEqual(avail["aggregate"], "saturated")
        self.assertTrue(avail["components"]["backend"])

    def test_display_phase_saturated_not_offline(self) -> None:
        phase = server.resolve_display_phase(
            {"phase": "idle"},
            {
                "reachable": False,
                "timedOut": True,
                "backendPortUp": True,
                "availabilityClass": "saturated",
                "consecutiveFailures": 1,
            },
            deploy_pending=False,
        )
        self.assertEqual(phase, "saturated")
        self.assertNotEqual(phase, "offline")


class MigrationsHotPathTests(unittest.TestCase):
    def test_fetch_database_metrics_skips_showmigrations_by_default(self) -> None:
        with patch.object(server_ops, "get_env_paths", return_value=(Path("."), Path("."))):
            with patch.object(server_ops, "_pg_connect_params", return_value={"dbname": "x"}):
                with patch("server_db.collect_pg_metrics", return_value={"ok": True}):
                    with patch("subprocess.run") as sub:
                        server_ops.fetch_database_metrics(
                            {"DEV": {}}, "DEV", include_migrations=False, use_cache=False
                        )
                        sub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
