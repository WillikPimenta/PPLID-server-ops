# -*- coding: utf-8 -*-
"""Orçamentos de performance e anti-gargalo do Indicador EO."""
from __future__ import annotations

import threading
import time
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.common.heavy_request_gate import QueueTimeoutError
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.performance_cache import (
    get_or_build,
    reset_qualidade_gate_for_tests,
)
from unittest.mock import patch

User = get_user_model()

# Orçamento CI (fixture pequena — não usa produção).
_DASHBOARD_COLD_BUDGET_MS = 2_500
_DASHBOARD_WARM_BUDGET_MS = 50


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_OPERACIONAL_CACHE_TTL=120,
    QUALIDADE_OPERACIONAL_MAX_CONCURRENT=1,
    QUALIDADE_OPERACIONAL_QUEUE_WAIT_MS=800,
    QUALIDADE_OPERACIONAL_BUILD_WAIT_S=5,
    QUALIDADE_OPERACIONAL_THROTTLE="1000/min",
    QUALIDADE_DASHBOARD_WARM_ENABLED=False,
)
class QualidadeEoPerformanceBudgetTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_qualidade_gate_for_tests()
        self.user = User.objects.create_user(
            username="eo_perf", password="x", email="eo_perf@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        for i in range(40):
            QualidadeAuditado.objects.create(
                data=date(2026, 7, 10),
                data_analise=date(2026, 7, 10),
                id_cliente=6,
                protocolo=f"PERF{i}",
                tipo_conclusao="Manual",
                matricula=f"c91{i:03d}a",
                source_file="perf",
            )
        for i in range(4):
            QualidadeFalha.objects.create(
                data=date(2026, 7, 10),
                data_analise=date(2026, 7, 10),
                id_cliente=6,
                protocolo=f"PERF{i}",
                tipo_falha="Manual",
                matricula=f"c91{i:03d}a",
                localidade="Brasília",
                source_file="perf",
            )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 2, 1),
            id_cliente=6,
            protocolo="PERF_OOD",
            tipo_conclusao="Manual",
            matricula="c91ooda",
            source_file="perf",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            protocolo="PERF_OOD",
            tipo_falha="Manual",
            matricula="c91ooda",
            localidade="Brasília",
            source_file="perf",
        )
        self.params = {
            "module": "resumo",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "date_axis": "auditoria",
            "grain": "etapa",
        }

    def test_dashboard_cold_and_warm_budgets(self):
        t0 = time.perf_counter()
        cold = build_dashboard(self.params)
        cold_ms = (time.perf_counter() - t0) * 1000
        self.assertTrue(cold.get("ok"))
        self.assertLess(
            cold_ms,
            _DASHBOARD_COLD_BUDGET_MS,
            f"dashboard frio {cold_ms:.1f}ms acima do orçamento {_DASHBOARD_COLD_BUDGET_MS}ms",
        )

        t1 = time.perf_counter()
        warm = build_dashboard(self.params)
        warm_ms = (time.perf_counter() - t1) * 1000
        self.assertEqual(warm["kpis"]["auditados"], cold["kpis"]["auditados"])
        self.assertLess(
            warm_ms,
            _DASHBOARD_WARM_BUDGET_MS,
            f"dashboard quente {warm_ms:.1f}ms acima do orçamento {_DASHBOARD_WARM_BUDGET_MS}ms",
        )

    def test_api_cache_hit_skips_second_build(self):
        builds = {"n": 0}

        def counting_build():
            builds["n"] += 1
            return {"ok": True, "n": builds["n"]}

        a = get_or_build("bench:test", self.params, counting_build)
        b = get_or_build("bench:test", self.params, counting_build)
        self.assertEqual(a, b)
        self.assertEqual(builds["n"], 1)

    def test_single_flight_same_key(self):
        started = threading.Event()
        releases = threading.Event()
        builds = {"n": 0}

        def slow_build():
            builds["n"] += 1
            started.set()
            releases.wait(timeout=2)
            return {"ok": True, "n": builds["n"]}

        results: list = []
        errors: list = []

        def worker():
            try:
                results.append(get_or_build("bench:flight", {"x": "1"}, slow_build))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        self.assertTrue(started.wait(timeout=2))
        t2.start()
        releases.set()
        t1.join(timeout=3)
        t2.join(timeout=3)
        self.assertFalse(errors)
        self.assertEqual(builds["n"], 1)
        self.assertEqual(len(results), 2)

    def test_dashboard_api_under_budget(self):
        t0 = time.perf_counter()
        res = self.client.get("/api/v1/qualidade/operacional/dashboard/", self.params)
        elapsed = (time.perf_counter() - t0) * 1000
        self.assertEqual(res.status_code, 200)
        self.assertLess(elapsed, _DASHBOARD_COLD_BUDGET_MS)
        self.assertIn("Server-Timing", res.headers)

    def test_dashboard_queue_timeout_returns_503_not_500(self):
        """Fila esgotada deve ser busy (503), não Internal Server Error."""
        with patch(
            "apps.qualidade_operacional.services.dashboard.get_or_build",
            side_effect=QueueTimeoutError("Fila de Qualidade Operacional esgotou o tempo de espera."),
        ):
            res = self.client.get("/api/v1/qualidade/operacional/dashboard/", self.params)
        self.assertEqual(res.status_code, 503)
        self.assertIn("retry_after", res.data)
        self.assertEqual(res.headers.get("Retry-After"), "3")
