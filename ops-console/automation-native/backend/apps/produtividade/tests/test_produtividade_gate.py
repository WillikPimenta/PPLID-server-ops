# -*- coding: utf-8 -*-
"""Testes anti-gargalo produtividade: cache, 400, throttle e gate."""
from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.produtividade_cache import (
    get_cached,
    invalidate_produtividade_cache,
    set_cached,
)
from apps.produtividade.services.produtividade_serve import (
    reset_produtividade_gate_for_tests,
    resolve_gated_payload,
)


def _aware(dt: datetime) -> datetime:
    return timezone.make_aware(dt, timezone.get_current_timezone())


@override_settings(
    PRODUTIVIDADE_CACHE_TTL=120,
    PRODUTIVIDADE_MAX_CONCURRENT=1,
    PRODUTIVIDADE_QUEUE_WAIT_MS=500,
    PRODUTIVIDADE_BUILD_WAIT_S=2,
    PRODUTIVIDADE_THROTTLE="1000/min",
)
class ProdutividadeAntiGargaloTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_produtividade_gate_for_tests()
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="prod_gate",
            password="test-pass-123",
        )
        self.client.force_authenticate(user=self.user)
        ProductivityRecord.objects.create(
            matricula_norm="gate01",
            etapa="Etapa A",
            analysis_seconds=100,
            analysis_count=1,
            stage_goal=Decimal("900"),
            recorded_at=_aware(datetime(2026, 6, 18, 10, 0, 0)),
            agent_name="Gate Agent",
            team="Equipe A",
        )
        self.dates = {"start_date": "2026-06-18", "end_date": "2026-06-18"}

    def test_requires_date_filter_400(self):
        response = self.client.get("/api/v1/produtividade/dashboard/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("start_date", response.json()["detail"])

    def test_range_over_max_400(self):
        response = self.client.get(
            "/api/v1/produtividade/dashboard/",
            {"start_date": "2026-05-01", "end_date": "2026-07-01"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("31", response.json()["detail"])

    def test_dashboard_with_dates_200(self):
        response = self.client.get("/api/v1/produtividade/dashboard/", self.dates)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attainment", response.json())

    def test_cache_hit_skips_second_build(self):
        params = {**self.dates}
        calls = {"n": 0}

        def fake_build():
            calls["n"] += 1
            return {"ok": True, "n": calls["n"]}

        p1, err1, _ = resolve_gated_payload(
            route="dashboard-test", params=params, builder=fake_build
        )
        p2, err2, _ = resolve_gated_payload(
            route="dashboard-test", params=params, builder=fake_build
        )
        self.assertIsNone(err1)
        self.assertIsNone(err2)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(p1["n"], p2["n"])
        self.assertIsNotNone(get_cached("dashboard-test", params))

    def test_invalidate_bumps_cache(self):
        params = {**self.dates}
        set_cached("dashboard-test", params, {"count": 1})
        self.assertIsNotNone(get_cached("dashboard-test", params))
        invalidate_produtividade_cache()
        self.assertIsNone(get_cached("dashboard-test", params))

    def test_queue_timeout_returns_503(self):
        reset_produtividade_gate_for_tests()
        started = threading.Event()
        release = threading.Event()

        def slow_build():
            started.set()
            release.wait(timeout=5)
            return {"ok": True}

        results: list[tuple] = []

        def runner(idx: int):
            local = {**self.dates, "matricula": f"m{idx}"}
            results.append(
                resolve_gated_payload(
                    route="dashboard-test",
                    params=local,
                    builder=slow_build,
                    use_cache=False,
                )
            )

        with override_settings(PRODUTIVIDADE_QUEUE_WAIT_MS=200):
            reset_produtividade_gate_for_tests()
            t1 = threading.Thread(target=runner, args=(1,))
            t2 = threading.Thread(target=runner, args=(2,))
            t1.start()
            self.assertTrue(started.wait(timeout=2))
            t2.start()
            t2.join(timeout=3)
            release.set()
            t1.join(timeout=3)

        codes = [r[1] for r in results]
        self.assertIn("queue_timeout", codes)

    @override_settings(PRODUTIVIDADE_THROTTLE="2/min")
    def test_throttle_429(self):
        from django.core.cache import cache as dj_cache

        dj_cache.clear()
        reset_produtividade_gate_for_tests()
        url = "/api/v1/produtividade/dashboard/"
        statuses = []
        for _ in range(4):
            statuses.append(self.client.get(url, self.dates).status_code)
        self.assertIn(429, statuses)

    def test_export_requires_dates(self):
        response = self.client.get("/api/v1/produtividade/export.xlsx")
        self.assertEqual(response.status_code, 400)


class MonitorBridgeDedupeTests(TestCase):
    """Um build_dashboard não deve chamar N× build_tabela_monitor_from_records."""

    def setUp(self):
        from apps.produtividade.services.monitor_bridge import clear_monitor_bridge_cache_for_tests

        clear_monitor_bridge_cache_for_tests()
        tz = timezone.get_current_timezone()
        ProductivityRecord.objects.create(
            matricula_norm="bridge01",
            etapa="A",
            analysis_seconds=100,
            analysis_count=1,
            stage_goal=Decimal("900"),
            recorded_at=datetime(2026, 6, 18, 10, 0, 0, tzinfo=tz),
            agent_name="Bridge",
            team="T",
        )

    def test_build_dashboard_single_monitor_build(self):
        from apps.produtividade.services.analytics import build_dashboard
        from apps.produtividade.services.monitor_bridge import clear_monitor_bridge_cache_for_tests

        clear_monitor_bridge_cache_for_tests()
        qs = ProductivityRecord.objects.all()
        with patch(
            "apps.produtividade.services.monitor_bridge.build_tabela_monitor_from_records",
            return_value=[],
        ) as mocked:
            build_dashboard(qs)
            self.assertLessEqual(mocked.call_count, 1)
