from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.replicacao_d1.services.dashboard_serve import resolve_dashboard_payload


class DashboardServeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.cache_parts_patch = patch(
            "apps.replicacao_d1.services.dashboard_serve.cache_key_parts",
            side_effect=lambda params: (
                "test",
                hashlib.sha256(repr(sorted(params.items())).encode("utf-8")).hexdigest(),
            ),
        )
        self.cache_parts_patch.start()

    def tearDown(self):
        self.cache_parts_patch.stop()
        cache.clear()

    @override_settings(REPLICACAO_D1_DASHBOARD_CACHE_TTL=60)
    def test_short_cache_avoids_sequential_rebuild(self):
        calls = 0

        def builder():
            nonlocal calls
            calls += 1
            return {"value": calls}

        first, first_busy, first_cache = resolve_dashboard_payload({"page": "1"}, builder)
        second, second_busy, second_cache = resolve_dashboard_payload({"page": "1"}, builder)

        self.assertIsNone(first_busy)
        self.assertIsNone(second_busy)
        self.assertEqual(first, second)
        self.assertEqual(calls, 1)
        self.assertEqual(first_cache, "miss")
        self.assertEqual(second_cache, "hit")

    @override_settings(
        REPLICACAO_D1_DASHBOARD_CACHE_TTL=60,
        REPLICACAO_D1_DASHBOARD_BUILD_WAIT_S=2,
        REPLICACAO_D1_DASHBOARD_MAX_INFLIGHT=8,
    )
    def test_single_flight_builds_identical_concurrent_request_once(self):
        calls = 0
        lock = threading.Lock()

        def builder():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.05)
            return {"ok": True}

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _index: resolve_dashboard_payload(
                        {"data_de": "2026-08-01", "data_ate": "2026-08-20"},
                        builder,
                    ),
                    range(8),
                )
            )

        self.assertTrue(all(payload == {"ok": True} and busy is None for payload, busy, _cache in results))
        self.assertEqual(calls, 1)

    @override_settings(
        REPLICACAO_D1_DASHBOARD_CACHE_TTL=0,
        REPLICACAO_D1_DASHBOARD_MAX_INFLIGHT=1,
        REPLICACAO_D1_DASHBOARD_BUILD_WAIT_S=2,
    )
    def test_inflight_limit_rejects_excess_waiters_without_building(self):
        started = threading.Event()
        release = threading.Event()

        def builder():
            started.set()
            release.wait(timeout=1)
            return {"ok": True}

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(resolve_dashboard_payload, {"key": "same"}, builder)
            self.assertTrue(started.wait(timeout=1))
            excess = pool.submit(resolve_dashboard_payload, {"key": "same"}, builder)
            excess_result = excess.result(timeout=1)
            release.set()
            leader_result = leader.result(timeout=1)

        self.assertEqual(excess_result, (None, "queue_timeout", "miss"))
        self.assertIsNone(leader_result[1])

    @override_settings(REPLICACAO_D1_DASHBOARD_CACHE_TTL=60)
    def test_projection_uses_independent_cache_namespace(self):
        dashboard, _, _ = resolve_dashboard_payload({}, lambda: {"kind": "dashboard"})
        projection, _, _ = resolve_dashboard_payload(
            {},
            lambda: {"kind": "projection"},
            namespace="projection",
        )

        self.assertEqual(dashboard["kind"], "dashboard")
        self.assertEqual(projection["kind"], "projection")
