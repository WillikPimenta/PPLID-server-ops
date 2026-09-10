"""Testes de validação de troca de escala."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import Escala, RequestType, Schedule
from apps.escala_flex.services.swap_request_workflow import SWAP_REQUEST_TYPE_NAME
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class SwapRequestValidationTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        RequestType.objects.get_or_create(pk=1, defaults={"name": SWAP_REQUEST_TYPE_NAME, "active": True})

        self.leader = Agent.objects.create(user_lan_id="lider02", full_name="Líder Dois", active=True)
        self.agent_a = Agent.objects.create(user_lan_id="agent02a", full_name="Daniela Silva", active=True)
        self.agent_b = Agent.objects.create(user_lan_id="agent02b", full_name="Anderson Souza", active=True)
        today = timezone.localdate()
        for agent in (self.agent_a, self.agent_b):
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Operacional",
                job_title="Agente Backoffice I",
                job_activity="Atendimento",
                start_date=today - timedelta(days=30),
                active=True,
            )

        self.agent_user = User.objects.create_user(
            username="agent02a", password="test12345", email="agent02a@test.local"
        )
        UserProfile.objects.create(user=self.agent_user, agent=self.agent_a)

        self.swap_date = today + timedelta(days=5)
        self._seed_week_schedule(self.agent_a, "08:00 - 17:00")
        self._seed_week_schedule(self.agent_b, "08:00 - 17:00")

    def _seed_published_escala(self, agent: Agent, target, work_schedule: str = "08:00 - 17:00") -> None:
        Escala.objects.update_or_create(
            agent=agent,
            data=target,
            defaults={
                "leader": self.leader,
                "horario": work_schedule,
                "dia_escala": work_schedule,
            },
        )

    def _seed_week_schedule(self, agent: Agent, work_schedule: str) -> None:
        start = self.swap_date - timedelta(days=3)
        for offset in range(7):
            day = start + timedelta(days=offset)
            is_work = offset != 0
            Schedule.objects.update_or_create(
                agent=agent,
                date=day,
                defaults={
                    "work_schedule": work_schedule if is_work else "",
                    "work_day": is_work,
                },
            )
            self._seed_published_escala(
                agent,
                day,
                work_schedule=work_schedule if is_work else "FOLGA",
            )

    def test_rejects_swap_date_before_minimum_advance(self):
        today = timezone.localdate()
        self.client.force_authenticate(user=self.agent_user)
        # Hoje nunca é elegível (mínimo é D+1 antes das 17h ou D+2 depois).
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_bh",
                "agent_lan_id": "agent02a",
                "date_swap": str(today),
                "description": "Data muito próxima",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        error_text = str(payload.get("detail") or payload.get("date_swap") or payload)
        self.assertIn("data da troca", error_text.lower())

    def test_peer_swap_marks_different_headcount_activity_without_blocking(self):
        history_b = self.agent_b.history.filter(active=True).first()
        history_b.job_activity = "Termo / Documento de Identificação"
        history_b.save(update_fields=["job_activity"])
        history_a = self.agent_a.history.filter(active=True).first()
        history_a.job_activity = "Reclassificação | Documento De Identificação"
        history_a.save(update_fields=["job_activity"])

        self.client.force_authenticate(user=self.agent_user)
        validation_response = self.client.post(
            "/api/v1/escala-flex/swap-requests/validate/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent02a",
                "agent_lan_id_2": "agent02b",
                "date_swap": str(self.swap_date),
            },
            format="json",
        )
        self.assertEqual(validation_response.status_code, 200, validation_response.content)
        validation_payload = validation_response.json()
        self.assertTrue(validation_payload["is_valid"])
        self.assertEqual(validation_payload["errors"], [])
        activity_check = next(
            check for check in validation_payload["checks"] if check["key"] == "activity"
        )
        self.assertFalse(activity_check["passed"])

        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent02a",
                "agent_lan_id_2": "agent02b",
                "date_swap": str(self.swap_date),
                "description": "Troca entre pares",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn(
            "Daniela: Reclassificação | Documento De Identificação\n"
            "Anderson: Termo / Documento de Identificação",
            response.json()["validation_activity"],
        )

    def test_peer_swap_passes_when_rules_are_met(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent02a",
                "agent_lan_id_2": "agent02b",
                "date_swap": str(self.swap_date),
                "description": "Troca entre pares",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["swap_kind"], "peer")
        self.assertEqual(payload["validation_activity"], "ok")

    def test_shift_schedule_rejects_non_standard_workload(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_schedule",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "new_journey": "08:00 - 17:00",
                "description": "Troca com carga inválida",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        error_text = str(payload.get("detail") or payload.get("new_journey") or payload)
        self.assertIn("6 horas", error_text)

    def test_shift_schedule_accepts_standard_workload(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_schedule",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "new_journey": "08:00 - 14:00",
                "description": "Troca de turno válida",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["new_journey"], "08:00 - 14:00")

    def test_shift_schedule_rejects_insufficient_rest(self):
        next_day = self.swap_date + timedelta(days=1)
        self._seed_published_escala(self.agent_a, next_day, "06:00 - 12:00")

        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_schedule",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "new_journey": "18:00 - 00:00",
                "description": "Troca para turno noturno",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("descanso", response.json()["detail"].lower())
        self.assertIn("turno posterior", response.json()["detail"].lower())

    def test_shift_schedule_rejects_insufficient_rest_before_requested_shift(self):
        previous_day = self.swap_date - timedelta(days=1)
        self._seed_published_escala(self.agent_a, previous_day, "22:00 - 06:00")

        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_schedule",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "new_journey": "14:00 - 20:00",
                "description": "Troca sem descanso antes do novo turno",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("descanso", response.json()["detail"].lower())
        self.assertIn("turno anterior", response.json()["detail"].lower())

    def test_peer_swap_checks_rest_after_swapped_shift_for_both_agents(self):
        self._seed_published_escala(self.agent_a, self.swap_date, "08:00 - 14:00")
        self._seed_published_escala(self.agent_b, self.swap_date, "18:00 - 00:00")
        self._seed_published_escala(
            self.agent_a,
            self.swap_date + timedelta(days=1),
            "06:00 - 12:00",
        )

        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent02a",
                "agent_lan_id_2": "agent02b",
                "date_swap": str(self.swap_date),
                "description": "Troca entre pares sem descanso posterior",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("descanso", response.json()["detail"].lower())
        self.assertIn("turno posterior", response.json()["detail"].lower())

    def test_validate_peer_swap_endpoint(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/validate/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent02a",
                "agent_lan_id_2": "agent02b",
                "date_swap": str(self.swap_date),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["is_valid"])
        self.assertEqual(len(payload["checks"]), 3)
        self.assertEqual(payload["checks"][0]["label"], "7 dias consecutivos trabalhados")
        self.assertEqual(payload["checks"][1]["label"], "11 horas de descanso entre turnos")
        self.assertEqual(payload["checks"][2]["label"], "Mesma atividade")
        self.assertTrue(payload["checks"][2]["passed"])

    def test_peer_swap_blocks_when_rest_cannot_be_validated(self):
        Schedule.objects.filter(agent__in=[self.agent_a, self.agent_b]).delete()
        Escala.objects.filter(agent__in=[self.agent_a, self.agent_b]).delete()
        for agent in (self.agent_a, self.agent_b):
            Schedule.objects.create(
                agent=agent,
                date=self.swap_date,
                work_schedule="08:00 - 14:00",
                work_day=True,
            )

        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent02a",
                "agent_lan_id_2": "agent02b",
                "date_swap": str(self.swap_date),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("11 horas de descanso", response.json()["detail"])

    def test_shift_bh_accepts_with_published_schedule_only(self):
        self._seed_published_escala(self.agent_a, self.swap_date)
        prev_day = self.swap_date - timedelta(days=6)
        next_day = self.swap_date - timedelta(days=5)
        Schedule.objects.update_or_create(
            agent=self.agent_a,
            date=prev_day,
            defaults={"work_schedule": "22:00 - 06:00", "work_day": True},
        )
        Schedule.objects.update_or_create(
            agent=self.agent_a,
            date=next_day,
            defaults={"work_schedule": "08:00 - 14:00", "work_day": True},
        )

        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_bh",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "description": "Troca para BH",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["swap_kind"], "shift_bh")

    def test_shift_bh_rejects_without_published_schedule(self):
        Escala.objects.filter(agent=self.agent_a, data=self.swap_date).delete()
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_bh",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "description": "Troca para BH",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        error_text = str(payload.get("detail") or payload)
        self.assertIn("escala publicada", error_text.lower())

    def test_shift_bh_rejects_when_published_day_is_not_work_shift(self):
        self._seed_published_escala(self.agent_a, self.swap_date, work_schedule="FOLGA")
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_bh",
                "agent_lan_id": "agent02a",
                "date_swap": str(self.swap_date),
                "description": "Troca para BH",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        error_text = str(payload.get("detail") or payload)
        self.assertIn("turno publicado", error_text.lower())
