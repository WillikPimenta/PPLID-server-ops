"""Testes da API de progresso de escala (eventos por agente + start_shift)."""

from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import Escala, OccurrenceType, OperationalOccurrence, ScheduleToday, StatusEvent, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class ScheduleProgressApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        StatusType.objects.create(
            pk=1, name="Disponível", color="#109010", active=True, logged_in=True
        )
        StatusType.objects.create(
            pk=4, name="Intervalo", color="#f9536d", active=True, logged_in=True
        )
        StatusType.objects.create(
            pk=3, name="Deslogado", color="#bdb2b0", active=True, logged_in=False
        )
        self.leader = Agent.objects.create(
            user_lan_id="lider01", full_name="Líder", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="agent01", full_name="Agente Um", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Agente",
            start_date=date.today(),
            active=True,
        )
        self.user = User.objects.create_user(username="lider01", password="test12345")
        UserProfile.objects.create(user=self.user, agent=self.leader)
        self.client.force_authenticate(user=self.user)

        today = timezone.localdate()
        Escala.objects.create(
            agent=self.agent,
            leader=self.leader,
            data=today,
            dia_escala="08:00 - 17:00",
            horario="08:00 - 17:00",
        )
        ScheduleTodayService.build_for_date(today)
        self.entry = ScheduleToday.objects.get(agent=self.agent)
        self.entry.start_of_work = timezone.make_aware(
            datetime.combine(today, datetime.strptime("08:00", "%H:%M").time())
        )
        self.entry.status_id = 1
        self.entry.save()

        start = timezone.make_aware(
            datetime.combine(today, datetime.strptime("08:00", "%H:%M").time())
        )
        end = timezone.make_aware(
            datetime.combine(today, datetime.strptime("10:00", "%H:%M").time())
        )
        StatusEvent.objects.create(
            agent=self.agent,
            leader=self.leader,
            status_id=1,
            start_date=start,
            final_date=end,
            active_event=False,
        )
        StatusEvent.objects.create(
            agent=self.agent,
            leader=self.leader,
            status_id=4,
            start_date=end,
            active_event=True,
        )

    def test_schedule_today_list_includes_status_events_by_lan(self):
        response = self.client.get("/api/v1/escala-flex/schedule/today/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("has_overnight_schedule", data)
        self.assertFalse(data["has_overnight_schedule"])
        self.assertIn("status_events_by_lan", data)
        events = data["status_events_by_lan"].get("agent01", [])
        self.assertGreaterEqual(len(events), 2)
        self.assertIn("status_color", events[0])

    def test_schedule_today_list_includes_approved_occurrences_by_lan(self):
        occurrence_type = OccurrenceType.objects.first()
        if not occurrence_type:
            occurrence_type = OccurrenceType.objects.create(
                pk=99, name="Treinamento", active=True
            )
        OperationalOccurrence.objects.create(
            date=timezone.localdate(),
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=occurrence_type,
            forecast_seconds=1800,
            scheduled_time=time(10, 0),
            approved=True,
            cancelled=False,
            leader=self.leader,
        )
        pending = OperationalOccurrence.objects.create(
            date=timezone.localdate(),
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=occurrence_type,
            forecast_seconds=900,
            approved=None,
            cancelled=False,
            leader=self.leader,
        )
        self.assertIsNotNone(pending.id)

        response = self.client.get("/api/v1/escala-flex/schedule/today/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("operational_occurrences_by_lan", data)
        occurrences = data["operational_occurrences_by_lan"].get("agent01", [])
        self.assertEqual(len(occurrences), 1)
        self.assertTrue(occurrences[0]["approved"])
        self.assertEqual(occurrences[0]["occurrence_type_name"], occurrence_type.name)

    def test_schedule_today_list_flags_overnight_schedule(self):
        night_agent = Agent.objects.create(
            user_lan_id="night02", full_name="Noturno Dois", active=True
        )
        AgentHistory.objects.create(
            agent=night_agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Agente",
            start_date=date.today(),
            active=True,
        )
        today = timezone.localdate()
        Escala.objects.create(
            agent=night_agent,
            leader=self.leader,
            data=today,
            dia_escala="23:30 - 05:05",
            horario="23:30 - 05:05",
        )
        ScheduleTodayService.build_for_date(today)

        response = self.client.get("/api/v1/escala-flex/schedule/today/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["has_overnight_schedule"])

    def test_start_shift_without_status_id(self):
        self.entry.status_id = 3
        self.entry.start_of_work = None
        self.entry.save()
        response = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {"schedule_today_id": str(self.entry.id), "action": "start_shift"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 1)
        self.assertIsNotNone(self.entry.start_of_work)
        self.assertTrue(
            StatusEvent.objects.filter(
                agent=self.agent, status_id=1, active_event=True
            ).exists()
        )

    def test_start_shift_by_lan_and_date_creates_missing_schedule_today(self):
        """Painel all_headcount: linha panel-* sem ScheduleToday ainda."""
        today = timezone.localdate()
        ScheduleToday.objects.filter(agent=self.agent, date=today).delete()
        self.assertFalse(
            ScheduleToday.objects.filter(agent=self.agent, date=today).exists()
        )
        response = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {
                "user_lan_id": "agent01",
                "date": today.isoformat(),
                "action": "start_shift",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        entry = ScheduleToday.objects.get(agent=self.agent, date=today)
        self.assertEqual(entry.status_id, 1)
        self.assertIsNotNone(entry.start_of_work)
        self.assertEqual(response.json().get("schedule_today_id"), str(entry.id))

    def test_change_status_requires_status_id(self):
        response = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {
                "schedule_today_id": str(self.entry.id),
                "action": "change_status",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_change_status_logout_closes_active_event(self):
        response = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {
                "schedule_today_id": str(self.entry.id),
                "action": "change_status",
                "status_id": 3,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["detail"], "Deslogado.")
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 3)
        self.assertTrue(
            StatusEvent.objects.filter(
                agent=self.agent, status_id=3, active_event=True
            ).exists()
        )
        self.assertFalse(
            StatusEvent.objects.filter(
                agent=self.agent, status_id=1, active_event=True
            ).exists()
        )

    def test_start_shift_preserves_start_of_work_on_relogin(self):
        original_sow = self.entry.start_of_work
        self.assertIsNotNone(original_sow)

        logout = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {
                "schedule_today_id": str(self.entry.id),
                "action": "change_status",
                "status_id": 3,
            },
            format="json",
        )
        self.assertEqual(logout.status_code, 200)

        relogin = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {"schedule_today_id": str(self.entry.id), "action": "start_shift"},
            format="json",
        )
        self.assertEqual(relogin.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 1)
        self.assertEqual(self.entry.start_of_work, original_sow)
        self.assertTrue(
            StatusEvent.objects.filter(
                agent=self.agent, status_id=1, active_event=True
            ).exists()
        )
        self.assertTrue(
            StatusEvent.objects.filter(
                agent=self.agent, status_id=3, active_event=False
            ).exists()
        )

    def test_start_shift_recovers_start_of_work_from_events(self):
        """Se start_of_work foi sobrescrito, recupera pelo evento mais antigo do dia."""
        today = timezone.localdate()
        original = timezone.make_aware(
            datetime.combine(today, datetime.strptime("08:00", "%H:%M").time())
        )
        corrupted = timezone.make_aware(
            datetime.combine(today, datetime.strptime("12:30", "%H:%M").time())
        )
        self.entry.status_id = 3
        self.entry.start_of_work = corrupted
        self.entry.save(update_fields=["status_id", "start_of_work"])
        StatusEvent.objects.filter(agent=self.agent, active_event=True).update(
            active_event=False,
            final_date=corrupted,
        )
        StatusEvent.objects.create(
            agent=self.agent,
            leader=self.leader,
            status_id=3,
            start_date=corrupted,
            active_event=True,
        )

        response = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {"schedule_today_id": str(self.entry.id), "action": "start_shift"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.start_of_work, original)

    def test_start_shift_fails_when_status_types_missing(self):
        self.entry.status = None
        self.entry.start_of_work = None
        self.entry.save(update_fields=["status", "start_of_work"])
        StatusEvent.objects.filter(agent=self.agent).delete()
        StatusType.objects.all().delete()
        response = self.client.post(
            "/api/v1/escala-flex/status-events/",
            {"schedule_today_id": str(self.entry.id), "action": "start_shift"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("status-types-only", response.json()["detail"])
