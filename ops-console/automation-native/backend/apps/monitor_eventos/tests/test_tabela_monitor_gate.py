# -*- coding: utf-8 -*-
"""Testes anti-gargalo: cache, 400, throttle e gate."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.heavy_request_gate import reset_gate_for_tests
from apps.monitor_eventos.services.tabela_monitor_cache import (
    get_cached_payload,
    invalidate_tabela_monitor_cache,
    set_cached_payload,
)
from apps.monitor_eventos.services.tabela_monitor_serve import resolve_tabela_monitor_payload


def _aware(dt: datetime) -> datetime:
    return timezone.make_aware(dt, timezone.get_current_timezone())


@override_settings(
    TABELA_MONITOR_CACHE_TTL=120,
    TABELA_MONITOR_MAX_CONCURRENT=1,
    TABELA_MONITOR_QUEUE_WAIT_MS=500,
    TABELA_MONITOR_BUILD_WAIT_S=2,
    TABELA_MONITOR_THROTTLE="1000/min",
)
class TabelaMonitorAntiGargaloTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_gate_for_tests()
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="anti_gargalo",
            password="test-pass-123",
        )
        self.client.force_authenticate(user=self.user)
        MonitorEventoRecord.objects.create(
            data=datetime(2026, 6, 18).date(),
            hora=10,
            matricula_usuario="c92928a",
            data_evento=_aware(datetime(2026, 6, 18, 10, 0, 0)),
            evento="Autenticação com sucesso",
            data_segundo_evento=_aware(datetime(2026, 6, 18, 12, 0, 0)),
            segundo_evento="Logout",
        )

    def test_requires_date_filter_400(self):
        response = self.client.get("/api/v1/monitor-eventos/tabela-monitor/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("data_jornada", response.json()["detail"])

    def test_returns_with_data_jornada(self):
        response = self.client.get(
            "/api/v1/monitor-eventos/tabela-monitor/",
            {"data_jornada": "2026-06-18"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()["count"], 0)

    def test_cache_hit_skips_second_build(self):
        params = {"data_jornada": "2026-06-18", "data": None, "matricula": None}
        calls = {"n": 0}

        def fake_build(qs):
            calls["n"] += 1
            return [{"matricula_usuario": "C92928A", "data_jornada": "2026-06-18", "hora": None}]

        with patch(
            "apps.monitor_eventos.services.tabela_monitor_serve.build_tabela_monitor",
            side_effect=fake_build,
        ):
            from apps.monitor_eventos.models import MonitorEventoRecord
            from apps.monitor_eventos.services.query_params import apply_record_filters

            qs = apply_record_filters(MonitorEventoRecord.objects.all(), params)
            p1, err1, _ = resolve_tabela_monitor_payload(qs, params)
            p2, err2, _ = resolve_tabela_monitor_payload(qs, params)
            self.assertIsNone(err1)
            self.assertIsNone(err2)
            self.assertEqual(calls["n"], 1)
            self.assertEqual(p1["count"], p2["count"])
            self.assertIsNotNone(get_cached_payload(params))

    def test_invalidate_bumps_cache(self):
        params = {"data_jornada": "2026-06-18", "data": None, "matricula": None}
        set_cached_payload(params, {"count": 1, "results": []})
        self.assertIsNotNone(get_cached_payload(params))
        invalidate_tabela_monitor_cache()
        self.assertIsNone(get_cached_payload(params))

    def test_queue_timeout_returns_503(self):
        reset_gate_for_tests()
        started = threading.Event()
        release = threading.Event()

        def slow_build(qs):
            started.set()
            release.wait(timeout=5)
            return []

        params = {"data_jornada": "2026-06-18", "data": None, "matricula": None}

        results: list[tuple] = []

        def runner(idx: int):
            from apps.monitor_eventos.models import MonitorEventoRecord
            from apps.monitor_eventos.services.query_params import apply_record_filters

            qs = apply_record_filters(MonitorEventoRecord.objects.all(), params)
            with patch(
                "apps.monitor_eventos.services.tabela_monitor_serve.build_tabela_monitor",
                side_effect=slow_build,
            ):
                # Chaves distintas: sem single-flight compartilhado → 2ª disputa semáforo.
                local = {**params, "matricula": f"m{idx}"}
                results.append(resolve_tabela_monitor_payload(qs, local))

        with override_settings(TABELA_MONITOR_QUEUE_WAIT_MS=200):
            reset_gate_for_tests()
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

    @override_settings(TABELA_MONITOR_THROTTLE="2/min")
    def test_throttle_429(self):
        # Recarrega rate após override: força novo throttle resetando cache de histórico.
        from django.core.cache import cache as dj_cache

        dj_cache.clear()
        reset_gate_for_tests()
        url = "/api/v1/monitor-eventos/tabela-monitor/"
        params = {"data_jornada": "2026-06-18"}
        statuses = []
        for _ in range(4):
            statuses.append(self.client.get(url, params).status_code)
        self.assertIn(429, statuses)


class TabelaMonitorAPICompatTests(TestCase):
    """Atualiza contratos antigos: data_jornada obrigatória."""

    def setUp(self):
        cache.clear()
        reset_gate_for_tests()
        self.client = APIClient()
        user = get_user_model().objects.create_user(
            username="portal",
            password="test-pass-123",
        )
        self.client.force_authenticate(user=user)
        MonitorEventoRecord.objects.create(
            data=datetime(2026, 6, 18).date(),
            hora=10,
            matricula_usuario="c92928a",
            data_evento=_aware(datetime(2026, 6, 18, 10, 0, 0)),
            evento="Autenticação com sucesso",
            data_segundo_evento=_aware(datetime(2026, 6, 18, 12, 0, 0)),
            segundo_evento="Logout",
        )

    def test_returns_tabela_monitor_with_date(self):
        response = self.client.get(
            "/api/v1/monitor-eventos/tabela-monitor/",
            {"data_jornada": "2026-06-18"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("count", payload)
        self.assertIn("results", payload)
        self.assertGreater(payload["count"], 0)

    def test_filter_matricula_with_date(self):
        response = self.client.get(
            "/api/v1/monitor-eventos/tabela-monitor/",
            {"data_jornada": "2026-06-18", "matricula": "c92928a"},
        )
        self.assertEqual(response.status_code, 200)
        for row in response.json()["results"]:
            self.assertEqual(row["matricula_usuario"], "C92928A")
