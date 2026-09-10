"""Testes unitários dos indicadores do controle da fila online."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.suporte_operacional.services.fila_online import (
    _agent_daily_metrics,
    _answered_today_count,
    _sla_average_today_pct,
)
from apps.suporte_operacional.services.workflow import RequestType, Status


class OperationalSupportFilaMetricsTests(SimpleTestCase):
    @patch("apps.suporte_operacional.services.fila_online.compute_support_sla_metrics")
    @patch("apps.suporte_operacional.services.fila_online.load_support_sla_map")
    @patch("apps.suporte_operacional.services.fila_online.OperationalSupportEvent.objects.filter")
    @patch("apps.suporte_operacional.services.fila_online.OperationalSupportRequest.objects.filter")
    def test_agent_daily_metrics_uses_assume_event_for_average_analysis_time(
        self,
        request_filter_mock,
        event_filter_mock,
        load_sla_mock,
        compute_sla_mock,
    ):
        now = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)
        first_start = now - timedelta(minutes=20)
        second_start = now - timedelta(minutes=12)
        answered = [
            SimpleNamespace(id=1, answered_at=first_start + timedelta(minutes=5)),
            SimpleNamespace(id=2, answered_at=second_start + timedelta(minutes=10)),
        ]
        request_filter_mock.return_value = answered
        event_filter_mock.return_value.order_by.return_value.values_list.return_value = [
            (1, first_start),
            (2, second_start),
        ]
        load_sla_mock.return_value = {"workflow": 3600}
        compute_sla_mock.side_effect = [
            SimpleNamespace(sla_limit_seconds=3600, pct_sla=50.0),
            SimpleNamespace(sla_limit_seconds=3600, pct_sla=75.0),
        ]

        result = _agent_daily_metrics(user=SimpleNamespace(id=42), now=now)

        self.assertEqual(result["realizados"], 2)
        self.assertEqual(result["sla_medio_pct"], 62.5)
        self.assertEqual(result["tempo_medio_analise_segundos"], 450)

    @patch("apps.suporte_operacional.services.fila_online.OperationalSupportRequest.objects.filter")
    def test_answered_today_uses_local_day_and_online_requests(self, filter_mock):
        filter_mock.return_value.count.return_value = 7
        now = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)

        result = _answered_today_count(now)

        self.assertEqual(result, 7)
        kwargs = filter_mock.call_args.kwargs
        self.assertEqual(kwargs["request_type"], RequestType.ONLINE)
        self.assertEqual(kwargs["status"], Status.ANSWERED)
        self.assertEqual(timezone.localtime(kwargs["answered_at__gte"]).date(), timezone.localdate(now))
        self.assertEqual(
            timezone.localtime(kwargs["answered_at__lt"]).date(),
            timezone.localdate(now) + timedelta(days=1),
        )

    @patch("apps.suporte_operacional.services.fila_online.compute_support_sla_metrics")
    @patch("apps.suporte_operacional.services.fila_online.load_support_sla_map")
    @patch("apps.suporte_operacional.services.fila_online.OperationalSupportRequest.objects.filter")
    def test_sla_average_today_uses_answered_online_requests(
        self,
        filter_mock,
        load_sla_mock,
        compute_sla_mock,
    ):
        now = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)
        answered = [
            SimpleNamespace(answered_at=now),
            SimpleNamespace(answered_at=now),
        ]
        filter_mock.return_value = answered
        load_sla_mock.return_value = {"workflow": 3600}
        compute_sla_mock.side_effect = [
            SimpleNamespace(sla_limit_seconds=3600, pct_sla=50.0),
            SimpleNamespace(sla_limit_seconds=3600, pct_sla=75.0),
        ]

        result = _sla_average_today_pct(now)

        self.assertEqual(result, 62.5)
        kwargs = filter_mock.call_args.kwargs
        self.assertEqual(kwargs["request_type"], RequestType.ONLINE)
        self.assertEqual(kwargs["status"], Status.ANSWERED)
