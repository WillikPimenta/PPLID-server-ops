"""Testes — métricas de tempo na fila."""

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from apps.planejamento_demandas.services.queue_metrics import queue_metrics


class QueueMetricsTests(SimpleTestCase):
    def test_open_fresh(self):
        now = timezone.now()
        m = queue_metrics(
            created_at_jira=now - timedelta(days=2),
            updated_at_jira=now - timedelta(hours=5),
            is_open=True,
            status_kind="open",
        )
        self.assertEqual(m["staleness"], "fresh")
        self.assertEqual(m["queue_age_days"], 2)

    def test_open_stale_idle(self):
        now = timezone.now()
        m = queue_metrics(
            created_at_jira=now - timedelta(days=20),
            updated_at_jira=now - timedelta(days=10),
            is_open=True,
            status_kind="open",
        )
        self.assertEqual(m["staleness"], "stale")
        self.assertIn("Sem movimento", m["idle_label"])

    def test_closed_has_no_queue_age(self):
        m = queue_metrics(
            created_at_jira=timezone.now() - timedelta(days=30),
            updated_at_jira=timezone.now(),
            is_open=False,
            status_kind="done",
        )
        self.assertEqual(m["staleness"], "closed")
        self.assertEqual(m["queue_age_days"], None)
