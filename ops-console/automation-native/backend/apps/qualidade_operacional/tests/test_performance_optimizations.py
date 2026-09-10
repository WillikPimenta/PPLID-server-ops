# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import threading
import time
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services import performance_cache
from apps.qualidade_operacional.services.dashboard import (
    build_clientes_melhorias_page,
    build_dashboard,
)
from apps.qualidade_operacional.services.performance_cache import (
    bump_quality_cache_version,
    cache_key_parts,
    get_cached,
    reset_qualidade_gate_for_tests,
    set_cached,
)


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_OPERACIONAL_CACHE_TTL=120,
    QUALIDADE_OPERACIONAL_MAX_CONCURRENT=2,
    QUALIDADE_OPERACIONAL_QUEUE_WAIT_MS=800,
    QUALIDADE_OPERACIONAL_BUILD_WAIT_S=2,
)
class QualidadePerformanceOptimizationTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_qualidade_gate_for_tests()
        self.params = {
            "module": "resumo",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "date_axis": "auditoria",
            "grain": "etapa",
            "dim": "id_cliente",
            "metric": "quantidade",
            "melhorias_page": "1",
            "melhorias_page_size": "25",
        }
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=83,
            protocolo="OPT-1",
            tipo_conclusao="Manual",
            matricula="agent-1",
            source_file="performance-optimization-test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=83,
            protocolo="OPT-1",
            tipo_falha="Manual",
            matricula="agent-1",
            source_file="performance-optimization-test",
        )

    def test_dedicated_improvements_payload_matches_dashboard_section(self):
        dashboard = build_dashboard(self.params)
        page = build_clientes_melhorias_page(self.params)
        self.assertEqual(page, dashboard["clientes_melhorias"])

        user = get_user_model().objects.create_superuser(
            username="qo_perf_optimization",
            email="qo-perf@example.com",
            password="unused",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            "/api/v1/qualidade/operacional/clientes-melhorias/", self.params
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, page)

    def test_build_does_not_publish_stale_payload_after_version_bump(self):
        route = "distributed:version-race"
        params = {"x": "1"}
        expected = {"ok": True, "version": "old"}

        def build_old_version():
            bump_quality_cache_version()
            return expected

        self.assertEqual(
            performance_cache.get_or_build(route, params, build_old_version),
            expected,
        )
        self.assertIsNone(get_cached(route, params))
    def test_distributed_follower_reuses_shared_result(self):
        route = "distributed:test"
        params = {"x": "1"}
        parts = cache_key_parts(route, params)
        lock_digest = hashlib.sha256(
            performance_cache._CACHE.key(*parts).encode("utf-8")  # noqa: SLF001
        ).hexdigest()
        lock_key = f"qualidade_operacional:build:{lock_digest}"
        cache.set(lock_key, "other-process", timeout=10)
        expected = {"ok": True, "source": "shared"}

        def publish_shared_result():
            time.sleep(0.05)
            set_cached(route, params, expected)

        publisher = threading.Thread(target=publish_shared_result)
        publisher.start()
        try:
            actual = performance_cache.get_or_build(
                route,
                params,
                lambda: self.fail("follower must not run the builder"),
            )
        finally:
            publisher.join(timeout=1)
        self.assertEqual(actual, expected)
