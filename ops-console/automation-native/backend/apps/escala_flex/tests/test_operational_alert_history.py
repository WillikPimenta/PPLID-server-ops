from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.escala_flex.models import (
    Escala,
    OperationalAlertEvent,
    ScheduleToday,
    StatusType,
)
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.operational_alert_history import (
    build_alert_fingerprint,
    collect_operational_alerts,
)
from apps.workforce.models import Agent

User = get_user_model()


class OperationalAlertHistoryServiceTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=1, name="Disponível", active=True, logged_in=True)
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        StatusType.objects.create(pk=4, name="Intervalo", active=True, logged_in=True)
        self.agent = Agent.objects.create(user_lan_id="agt-history", full_name="Agente Histórico", active=True)
        self.today = timezone.localdate()
        Escala.objects.create(
            agent=self.agent,
            data=self.today,
            dia_escala="00:00 - 23:59",
            horario="00:00 - 23:59",
        )
        ScheduleTodayService.build_for_date(self.today)
        self.entry = ScheduleToday.objects.get(agent=self.agent, date=self.today)
        self.entry.status_id = 4
        self.entry.work_schedule = "00:00 - 23:59"
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self.entry.start_of_work = self._at(8)
        self.entry.save()

    def _at(self, hour: int, minute: int = 0):
        return timezone.make_aware(datetime.combine(self.today, time(hour, minute)))

    def test_collect_creates_updates_resolves_and_recreates(self):
        created = collect_operational_alerts(target_date=self.today, reference_time=self._at(13, 16))
        self.assertEqual(created["created"], 1)
        event = OperationalAlertEvent.objects.get()
        self.assertEqual(event.alert_type, "break_exceeded")
        self.assertEqual(event.occurrence_count, 1)

        updated = collect_operational_alerts(target_date=self.today, reference_time=self._at(13, 18))
        self.assertEqual(updated["updated"], 1)
        event.refresh_from_db()
        self.assertEqual(event.occurrence_count, 2)
        self.assertEqual(event.priority_current, "Alta")

        self.entry.status = None
        self.entry.start_of_work = None
        self.entry.save()
        resolved = collect_operational_alerts(target_date=self.today, reference_time=self._at(13, 20))
        self.assertEqual(resolved["resolved"], 1)
        event.refresh_from_db()
        self.assertEqual(event.status, OperationalAlertEvent.STATUS_RESOLVED)
        self.assertEqual(event.resolved_at, self._at(13, 20))

        self.entry.status_id = 4
        self.entry.start_of_work = self._at(8)
        self.entry.save()
        reappeared = collect_operational_alerts(target_date=self.today, reference_time=self._at(13, 21))
        self.assertEqual(reappeared["created"], 1)
        self.assertEqual(OperationalAlertEvent.objects.count(), 2)
        self.assertEqual(
            OperationalAlertEvent.objects.filter(status=OperationalAlertEvent.STATUS_ACTIVE).count(),
            1,
        )

    def test_dry_run_does_not_write(self):
        result = collect_operational_alerts(
            target_date=self.today,
            reference_time=self._at(13, 18),
            dry_run=True,
        )
        self.assertEqual(result["created"], 1)
        self.assertTrue(result["dry_run"])
        self.assertFalse(OperationalAlertEvent.objects.exists())

    def test_fingerprint_changes_with_occurrence_source(self):
        common = {
            "agent_id": self.agent.id,
            "operational_date": self.today,
            "alert_type": "occurrence_exceeded",
            "source_type": "operational_occurrence",
        }
        self.assertNotEqual(
            build_alert_fingerprint(**common, source_id="one"),
            build_alert_fingerprint(**common, source_id="two"),
        )

    def test_database_rejects_two_active_events_with_same_fingerprint(self):
        now = self._at(13, 18)
        values = {
            "agent": self.agent,
            "operational_date": self.today,
            "alert_type": "break_exceeded",
            "fingerprint": "f" * 64,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        OperationalAlertEvent.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OperationalAlertEvent.objects.create(**values)


class OperationalAlertHistoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser("alert-admin", "alert@test.local", "x")
        self.agent = Agent.objects.create(user_lan_id="api-agent", full_name="Agente API", active=True)
        now = timezone.now()
        OperationalAlertEvent.objects.create(
            agent=self.agent,
            operational_date=timezone.localdate(),
            alert_type="systemic_issue",
            fingerprint="a" * 64,
            status=OperationalAlertEvent.STATUS_ACTIVE,
            priority_initial="Alta",
            priority_current="Alta",
            state="Problema sistêmico",
            reason="Problemas sistêmicos",
            recommended_action="Acompanhar normalização.",
            first_seen_at=now,
            last_seen_at=now,
        )

    def test_history_requires_permission(self):
        response = self.client.get("/api/v1/escala-flex/monitoring/alerts/history/")
        self.assertIn(response.status_code, (401, 403))

    def test_history_is_paginated_and_filterable(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(
            "/api/v1/escala-flex/monitoring/alerts/history/",
            {"alert_type": "systemic_issue", "status": "active", "page_size": 10},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["alert_type_label"], "Problema sistêmico")
        self.assertIn("filter_options", payload)
