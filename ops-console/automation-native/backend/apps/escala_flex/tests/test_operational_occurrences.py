"""Testes da API de ocorrências operacionais."""

from datetime import date, time

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import (
    Escala,
    OccurrenceType,
    OperationalOccurrence,
    OperationalOccurrenceExtension,
    ScheduleToday,
)
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.occurrence_auto_cancel import run_occurrence_auto_cancels
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class OperationalOccurrenceApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
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
            job_activity="BrFlow",
            start_date=date.today(),
            active=True,
        )
        self.user = User.objects.create_user(username="lider01", password="test12345")
        UserProfile.objects.create(user=self.user, agent=self.leader)
        self.client.force_authenticate(user=self.user)

        self.occurrence_type = OccurrenceType.objects.first()
        if not self.occurrence_type:
            self.occurrence_type = OccurrenceType.objects.create(
                pk=1, name="Acesso aos Portais", active=True
            )

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

    def test_batch_create_pending_occurrence(self):
        response = self.client.post(
            "/api/v1/escala-flex/operational-occurrences/batch/",
            {
                "items": [
                    {
                        "schedule_today_id": str(self.entry.id),
                        "occurrence_type_id": self.occurrence_type.id,
                        "forecast_seconds": 1800,
                        "description": "Portal fora do ar",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(len(payload["created"]), 1)
        self.assertEqual(payload["created"][0]["forecast_display"], "00:30")
        occurrence = OperationalOccurrence.objects.get()
        self.assertIsNone(occurrence.approved)
        self.assertEqual(occurrence.leader_id, self.leader.id)
        self.assertIsNotNone(occurrence.scheduled_time)

    def test_batch_create_with_scheduled_time(self):
        response = self.client.post(
            "/api/v1/escala-flex/operational-occurrences/batch/",
            {
                "items": [
                    {
                        "schedule_today_id": str(self.entry.id),
                        "occurrence_type_id": self.occurrence_type.id,
                        "forecast_seconds": 1800,
                        "scheduled_time": "10:30",
                        "description": "Treinamento",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        occurrence = OperationalOccurrence.objects.get()
        self.assertEqual(occurrence.scheduled_time, time(10, 30))
        self.assertEqual(response.json()["created"][0]["scheduled_time"], "10:30:00")

    def test_list_pending_occurrences(self):
        OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=900,
            description="Teste",
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.get(
            "/api/v1/escala-flex/operational-occurrences/",
            {"date": str(self.entry.date), "pending": "true"},
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["leader_name"], "Líder")
        self.assertEqual(results[0]["agent_job_activity"], "BrFlow")
        self.assertEqual(results[0]["workflow_status"], "pending")
        self.assertFalse(results[0]["cancelled"])

    def test_approve_occurrence(self):
        occurrence = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=900,
            description="Teste",
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/approve/",
            {"approved": True, "approval_notes": "Ok"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.approved)
        self.assertEqual(occurrence.approval_notes, "Ok")

    def test_approve_occurrences_batch(self):
        first = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=600,
            description="Lote 1",
            leader=self.leader,
            created_by=self.leader,
        )
        second = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=700,
            description="Lote 2",
            leader=self.leader,
            created_by=self.leader,
        )
        already = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=800,
            description="Já aprovada",
            approved=True,
            approved_by=self.leader,
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.post(
            "/api/v1/escala-flex/operational-occurrences/approve-batch/",
            {
                "approval_notes": "Lote ok",
                "items": [
                    {"id": str(first.id), "approved": True},
                    {"id": str(second.id), "approved": False},
                    {"id": str(already.id), "approved": True},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(len(payload["errors"]), 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.approved)
        self.assertFalse(second.approved)
        self.assertEqual(first.approval_notes, "Lote ok")

    def test_approved_occurrence_workflow_status(self):
        occurrence = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=3600,
            description="Em andamento",
            approved=True,
            approved_by=self.leader,
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.get(
            "/api/v1/escala-flex/operational-occurrences/",
            {"date": str(self.entry.date)},
        )
        row = response.json()["results"][0]
        self.assertEqual(row["workflow_status"], "approved")

    def test_finalized_occurrence_workflow_status(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=1800,
            scheduled_time=ref.time().replace(second=0, microsecond=0),
            description="Concluída",
            approved=True,
            approved_by=self.leader,
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.get(
            "/api/v1/escala-flex/operational-occurrences/",
            {"date": str(self.entry.date)},
        )
        row = response.json()["results"][0]
        self.assertEqual(row["workflow_status"], "finalized")

    def test_planejamento_user_can_approve_occurrence(self):
        planner = Agent.objects.create(
            user_lan_id="plan01", full_name="Planejador", active=True
        )
        AgentHistory.objects.create(
            agent=planner,
            leader=self.leader,
            location="Brasília",
            team="Planejamento",
            job_title="Analista",
            start_date=date.today(),
            active=True,
        )
        planner_user = User.objects.create_user(
            username="plan01", email="plan01@test.local", password="test12345"
        )
        UserProfile.objects.create(user=planner_user, agent=planner)

        occurrence = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=900,
            description="Aguardando planejamento",
            leader=self.leader,
            created_by=self.leader,
        )

        client = APIClient()
        client.force_authenticate(user=planner_user)
        response = client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/approve/",
            {"approved": True, "approval_notes": "Ok planejamento"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.approved)
        self.assertEqual(occurrence.approval_notes, "Ok planejamento")
        self.assertEqual(response.json()["workflow_status"], "approved")

    def _future_scheduled_time(self, hours_ahead: int = 2) -> time:
        ref = timezone.localtime() + timezone.timedelta(hours=hours_ahead)
        return ref.time().replace(second=0, microsecond=0)

    def _create_approved_occurrence(self, **overrides):
        data = {
            "date": self.entry.date,
            "agent": self.agent,
            "schedule_today": self.entry,
            "occurrence_type": self.occurrence_type,
            "forecast_seconds": 1800,
            "scheduled_time": self._future_scheduled_time(),
            "description": "Aprovada",
            "approved": True,
            "approved_by": self.leader,
            "leader": self.leader,
            "created_by": self.leader,
        }
        data.update(overrides)
        return OperationalOccurrence.objects.create(**data)

    def test_cancel_approved_occurrence(self):
        occurrence = self._create_approved_occurrence()
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/cancel/",
            {"approval_notes": "Removida da escala"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.cancelled)
        self.assertIn("Removida da escala", occurrence.approval_notes)
        self.assertEqual(response.json()["workflow_status"], "cancelled")

    def test_cancel_pending_occurrence_fails(self):
        occurrence = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=900,
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_update_approved_occurrence(self):
        occurrence = self._create_approved_occurrence()
        response = self.client.patch(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/",
            {"scheduled_time": "14:30", "forecast_seconds": 3600, "description": "Atualizada"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.scheduled_time, time(14, 30))
        self.assertEqual(occurrence.forecast_seconds, 3600)
        self.assertEqual(occurrence.description, "Atualizada")
        self.assertEqual(response.json()["forecast_display"], "01:00")

    def test_update_cancelled_occurrence_fails(self):
        occurrence = self._create_approved_occurrence(cancelled=True)
        response = self.client.patch(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/",
            {"description": "Tentativa"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_update_finalized_occurrence_fails(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_approved_occurrence(
            forecast_seconds=1800,
            scheduled_time=ref.time().replace(second=0, microsecond=0),
        )
        response = self.client.patch(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/",
            {"description": "Tentativa"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("finalizadas", response.json()["detail"].lower())

    def test_cancel_finalized_occurrence_fails(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_approved_occurrence(
            forecast_seconds=1800,
            scheduled_time=ref.time().replace(second=0, microsecond=0),
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cancel_approved_occurrence_requires_permission(self):
        occurrence = self._create_approved_occurrence()
        outsider = Agent.objects.create(
            user_lan_id="outsider01", full_name="Outsider", active=True
        )
        outsider_user = User.objects.create_user(
            username="outsider01",
            email="outsider01@test.local",
            password="test12345",
        )
        UserProfile.objects.create(user=outsider_user, agent=outsider)
        client = APIClient()
        client.force_authenticate(user=outsider_user)
        response = client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def _create_rejected_occurrence(self, **overrides):
        data = {
            "date": self.entry.date,
            "agent": self.agent,
            "schedule_today": self.entry,
            "occurrence_type": self.occurrence_type,
            "forecast_seconds": 1800,
            "scheduled_time": self._future_scheduled_time(),
            "description": "Recusada",
            "approved": False,
            "approved_by": self.leader,
            "leader": self.leader,
            "created_by": self.leader,
        }
        data.update(overrides)
        return OperationalOccurrence.objects.create(**data)

    def test_reapprove_rejected_occurrence_with_future_time(self):
        occurrence = self._create_rejected_occurrence(
            scheduled_time=self._future_scheduled_time(hours_ahead=2),
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/approve/",
            {"approved": True, "approval_notes": "Reaprovada"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.approved)
        self.assertEqual(response.json()["workflow_status"], "approved")

    def test_reapprove_rejected_occurrence_with_past_time_fails(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_rejected_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/approve/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("horário", response.json()["detail"].lower())

    def test_update_rejected_occurrence(self):
        ref = timezone.localtime() + timezone.timedelta(hours=2)
        occurrence = self._create_rejected_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
        )
        response = self.client.patch(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/",
            {"description": "Ajustada", "forecast_seconds": 3600},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.description, "Ajustada")
        self.assertEqual(occurrence.forecast_seconds, 3600)

    def test_update_rejected_occurrence_past_time_fails(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_rejected_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
        )
        past = (timezone.localtime() - timezone.timedelta(hours=1)).time().replace(
            second=0, microsecond=0
        )
        response = self.client.patch(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/",
            {"scheduled_time": past.strftime("%H:%M")},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("horário", response.json()["detail"].lower())

    def test_reapprove_rejected_after_reschedule(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_rejected_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
        )
        future = self._future_scheduled_time(hours_ahead=1)
        patch_response = self.client.patch(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/",
            {"scheduled_time": future.strftime("%H:%M")},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        approve_response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/approve/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(approve_response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.approved)

    def _create_in_progress_occurrence(self, **overrides):
        ref = timezone.localtime()
        start = (ref - timezone.timedelta(minutes=15)).time().replace(second=0, microsecond=0)
        data = {
            "date": self.entry.date,
            "agent": self.agent,
            "schedule_today": self.entry,
            "occurrence_type": self.occurrence_type,
            "forecast_seconds": 3600,
            "scheduled_time": start,
            "description": "Em andamento",
            "approved": True,
            "approved_by": self.leader,
            "leader": self.leader,
            "created_by": self.leader,
        }
        data.update(overrides)
        return OperationalOccurrence.objects.create(**data)

    def test_request_extension_for_in_progress_occurrence(self):
        occurrence = self._create_in_progress_occurrence()
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/",
            {"extra_seconds": 1800, "description": "Precisa de mais tempo"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["can_request_extension"] is False)
        self.assertIsNotNone(payload["pending_extension"])
        self.assertEqual(payload["pending_extension"]["extra_display"], "00:30")

    def test_request_extension_for_pending_occurrence_fails(self):
        occurrence = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=1800,
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/",
            {"extra_seconds": 1800},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("andamento", response.json()["detail"].lower())

    def test_approve_extension_adds_forecast(self):
        occurrence = self._create_in_progress_occurrence()
        create_response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/",
            {"extra_seconds": 1800},
            format="json",
        )
        extension_id = create_response.json()["pending_extension"]["id"]
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/{extension_id}/approve/",
            {"approved": True, "approval_notes": "Ok"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.forecast_seconds, 5400)
        self.assertIsNone(response.json()["pending_extension"])

    def test_reject_extension_keeps_forecast(self):
        occurrence = self._create_in_progress_occurrence()
        create_response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/",
            {"extra_seconds": 1800},
            format="json",
        )
        extension_id = create_response.json()["pending_extension"]["id"]
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/{extension_id}/approve/",
            {"approved": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.forecast_seconds, 3600)

    def test_duplicate_pending_extension_fails(self):
        occurrence = self._create_in_progress_occurrence()
        self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/",
            {"extra_seconds": 1800},
            format="json",
        )
        response = self.client.post(
            f"/api/v1/escala-flex/operational-occurrences/{occurrence.id}/extensions/",
            {"extra_seconds": 900},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_request_extensions_batch(self):
        first = self._create_in_progress_occurrence(description="Lote ext 1")
        second = self._create_in_progress_occurrence(description="Lote ext 2")
        pending = OperationalOccurrence.objects.create(
            date=self.entry.date,
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=self.occurrence_type,
            forecast_seconds=1800,
            description="Ainda pendente",
            leader=self.leader,
            created_by=self.leader,
        )
        response = self.client.post(
            "/api/v1/escala-flex/operational-occurrences/extensions/batch/",
            {
                "extra_seconds": 1800,
                "description": "Mais tempo em lote",
                "items": [
                    {"id": str(first.id)},
                    {"id": str(second.id)},
                    {"id": str(pending.id)},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(len(payload["errors"]), 1)
        self.assertTrue(
            all(row["pending_extension"] is not None for row in payload["results"])
        )
        self.assertEqual(
            OperationalOccurrenceExtension.objects.filter(
                occurrence__in=[first, second], approved__isnull=True
            ).count(),
            2,
        )

    def _create_pending_occurrence(self, **overrides):
        data = {
            "date": self.entry.date,
            "agent": self.agent,
            "schedule_today": self.entry,
            "occurrence_type": self.occurrence_type,
            "forecast_seconds": 1800,
            "description": "Pendente",
            "leader": self.leader,
            "created_by": self.leader,
        }
        data.update(overrides)
        return OperationalOccurrence.objects.create(**data)

    def test_auto_cancel_expired_pending_occurrence(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_pending_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
            forecast_seconds=1800,
        )
        result = run_occurrence_auto_cancels()
        self.assertEqual(result["cancelled"], 1)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.cancelled)
        self.assertIn("automaticamente", occurrence.approval_notes.lower())

    def test_auto_cancel_skips_pending_occurrence_still_in_window(self):
        ref = timezone.localtime() - timezone.timedelta(minutes=10)
        occurrence = self._create_pending_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
            forecast_seconds=3600,
        )
        result = run_occurrence_auto_cancels()
        self.assertEqual(result["cancelled"], 0)
        occurrence.refresh_from_db()
        self.assertFalse(occurrence.cancelled)

    def test_auto_cancel_skips_rejected_occurrence(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_rejected_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
            forecast_seconds=1800,
        )
        result = run_occurrence_auto_cancels()
        self.assertEqual(result["cancelled"], 0)
        occurrence.refresh_from_db()
        self.assertFalse(occurrence.cancelled)

    def test_list_auto_cancels_expired_pending_occurrence(self):
        ref = timezone.localtime() - timezone.timedelta(hours=2)
        occurrence = self._create_pending_occurrence(
            scheduled_time=ref.time().replace(second=0, microsecond=0),
            forecast_seconds=1800,
        )
        response = self.client.get(
            "/api/v1/escala-flex/operational-occurrences/",
            {"date": str(self.entry.date)},
        )
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["results"] if item["id"] == str(occurrence.id))
        self.assertEqual(row["workflow_status"], "cancelled")
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.cancelled)

    def test_occurrence_types_dimension(self):
        response = self.client.get("/api/v1/escala-flex/dimensions/occurrence-types/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["results"]), 1)
