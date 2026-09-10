"""Testes unitários do ranking SLA do suporte operacional."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.suporte_operacional.services.sla_ranking import (
    SupportSlaMetrics,
    compute_support_sla_metrics,
    rank_online_queue_requests,
)


class SupportSlaRankingTests(SimpleTestCase):
    def test_active_sla_is_resolved_with_keyword_only_arguments(self):
        now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        entered_at = datetime(2026, 8, 12, 11, 30, tzinfo=UTC)
        request = SimpleNamespace(
            leader_decided_at=entered_at,
            created_at=entered_at,
            cliente_id=88001,
            workflow_id=88002,
        )
        rule = SimpleNamespace(sla_segundos=3600)
        sla_map = {(88001, 88002): [rule]}

        def keyword_only_find_active_sla(*, cliente_id, workflow_id, now, sla_map_cw):
            self.assertEqual(cliente_id, 88001)
            self.assertEqual(workflow_id, 88002)
            self.assertEqual(now, datetime(2026, 8, 12, 12, 0, tzinfo=UTC))
            self.assertIs(sla_map_cw, sla_map)
            return rule

        with (
            patch(
                "apps.suporte_operacional.services.sla_ranking.projecao_windows",
                return_value=[],
            ),
            patch(
                "apps.suporte_operacional.services.sla_ranking.find_active_sla",
                side_effect=keyword_only_find_active_sla,
            ),
            patch(
                "apps.suporte_operacional.services.sla_ranking.compute_breach_moment",
                return_value=now,
            ),
            patch(
                "apps.suporte_operacional.services.sla_ranking.business_elapsed_seconds",
                return_value=1800,
            ),
        ):
            metrics = compute_support_sla_metrics(request, sla_map=sla_map, now=now)

        self.assertEqual(metrics.sla_limit_seconds, 3600)
        self.assertEqual(metrics.pct_sla, 50.0)

    def test_waiting_time_adds_five_priority_points_per_minute(self):
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        newer = SimpleNamespace(id="newer")
        older = SimpleNamespace(id="older")
        metrics_by_id = {
            "newer": SupportSlaMetrics(
                sla_limit_seconds=3600,
                deadline_at=None,
                pct_sla=62.0,
                queue_entered_at=now - timedelta(minutes=1),
            ),
            "older": SupportSlaMetrics(
                sla_limit_seconds=3600,
                deadline_at=None,
                pct_sla=50.0,
                queue_entered_at=now - timedelta(minutes=4),
            ),
        }

        with patch(
            "apps.suporte_operacional.services.sla_ranking.compute_support_sla_metrics",
            side_effect=lambda request, **_: metrics_by_id[request.id],
        ):
            ranked = rank_online_queue_requests([newer, older], sla_map={}, now=now)

        # newer: 62 + (1 * 5) = 67; older: 50 + (4 * 5) = 70.
        self.assertEqual([request.id for request in ranked], ["older", "newer"])

    def test_manual_priority_places_request_first(self):
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        urgent = SimpleNamespace(id="urgent", queue_priority_at=None)
        prioritized = SimpleNamespace(
            id="prioritized",
            queue_priority_at=now - timedelta(seconds=5),
        )
        metrics_by_id = {
            "urgent": SupportSlaMetrics(3600, None, 95.0, now - timedelta(minutes=10)),
            "prioritized": SupportSlaMetrics(3600, None, 5.0, now - timedelta(minutes=1)),
        }

        with patch(
            "apps.suporte_operacional.services.sla_ranking.compute_support_sla_metrics",
            side_effect=lambda request, **_: metrics_by_id[request.id],
        ):
            ranked = rank_online_queue_requests([urgent, prioritized], sla_map={}, now=now)

        self.assertEqual([request.id for request in ranked], ["prioritized", "urgent"])
