"""Testes dos endpoints de configuração administrativa."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import AbsenceType, OccurrenceType, StatusType
from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


class ConfigApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_agent = Agent.objects.create(
            user_lan_id="c91763a",
            full_name="Admin",
            active=True,
        )
        self.regular_agent = Agent.objects.create(
            user_lan_id="agent01",
            full_name="Agente",
            active=True,
        )
        self.admin_user = User.objects.create_user(
            username="c91763a", email="admin@test.local", password="test12345"
        )
        self.regular_user = User.objects.create_user(
            username="agent01", email="agent01@test.local", password="test12345"
        )
        admin_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin_user.groups.add(admin_group)
        UserProfile.objects.create(user=self.admin_user, agent=self.admin_agent)
        UserProfile.objects.create(user=self.regular_user, agent=self.regular_agent)

        # O catálogo é semeado por migração; este teste exercita um
        # conjunto mínimo e determinístico da API administrativa.
        StatusType.objects.exclude(pk=1).delete()
        StatusType.objects.update_or_create(
            pk=1, defaults={"name": "Disponível", "color": "#109010", "active": True}
        )
        OccurrenceType.objects.update_or_create(
            pk=1, defaults={"name": "Acesso aos Portais", "active": True}
        )

    def test_status_types_list_requires_admin(self):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get("/api/v1/escala-flex/config/status-types/")
        self.assertEqual(response.status_code, 403)

    def test_status_types_list_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/api/v1/escala-flex/config/status-types/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_create_status_type(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/config/status-types/",
            {
                "name": "Intervalo",
                "color": "#ffaa00",
                "active": True,
                "logged_in": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["name"], "Intervalo")
        self.assertTrue(StatusType.objects.filter(name="Intervalo").exists())

    def test_update_status_type(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(
            "/api/v1/escala-flex/config/status-types/1/",
            {"name": "Disponível atualizado", "active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        status_type = StatusType.objects.get(pk=1)
        self.assertEqual(status_type.name, "Disponível atualizado")
        self.assertFalse(status_type.active)

    def test_occurrence_types_list_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/api/v1/escala-flex/config/occurrence-types/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["results"]), 1)

    def test_create_occurrence_type(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/config/occurrence-types/",
            {"name": "Manutenção", "active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(OccurrenceType.objects.filter(name="Manutenção").exists())

    def test_update_occurrence_type(self):
        self.client.force_authenticate(user=self.admin_user)
        StatusType.objects.update_or_create(
            pk=4, defaults={"name": "Pausa", "color": "#f9536d", "active": True}
        )
        response = self.client.patch(
            "/api/v1/escala-flex/config/occurrence-types/1/",
            {
                "name": "Portal",
                "active": False,
                "observation": "Observação teste",
                "status_type_ids": [4],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        occurrence_type = OccurrenceType.objects.get(pk=1)
        self.assertEqual(occurrence_type.name, "Portal")
        self.assertFalse(occurrence_type.active)
        self.assertEqual(occurrence_type.observation, "Observação teste")
        self.assertEqual(list(occurrence_type.status_types.values_list("id", flat=True)), [4])

    def test_update_status_type_extended_fields(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(
            "/api/v1/escala-flex/config/status-types/1/",
            {
                "observation": "Status observação",
                "deducts_logged_time": True,
                "default_time_seconds": 900,
                "deducts_production": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        status_type = StatusType.objects.get(pk=1)
        self.assertEqual(status_type.observation, "Status observação")
        self.assertTrue(status_type.deducts_logged_time)
        self.assertEqual(status_type.default_time_seconds, 900)
        self.assertTrue(status_type.deducts_production)

    def test_absence_types_list_requires_admin(self):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get("/api/v1/escala-flex/config/absence-types/")
        self.assertEqual(response.status_code, 403)

    def test_absence_types_dimensions_active_only(self):
        AbsenceType.objects.update_or_create(
            pk=1,
            defaults={
                "code": "FOLGA",
                "name": "Folga",
                "color": "#0EA5E9",
                "active": True,
            },
        )
        AbsenceType.objects.update_or_create(
            pk=99,
            defaults={
                "code": "INATIVO",
                "name": "Inativo",
                "color": "#111111",
                "active": False,
            },
        )
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get("/api/v1/escala-flex/dimensions/absence-types/")
        self.assertEqual(response.status_code, 200)
        codes = {item["code"] for item in response.json()["results"]}
        self.assertIn("FOLGA", codes)
        self.assertNotIn("INATIVO", codes)

    def test_create_absence_type(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/config/absence-types/",
            {
                "code": "LICENCA",
                "name": "Licença",
                "color": "#22c55e",
                "active": True,
                "observation": "Licença médica",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["code"], "LICENCA")
        self.assertTrue(AbsenceType.objects.filter(code="LICENCA").exists())

    def test_update_absence_type(self):
        absence_type, _ = AbsenceType.objects.update_or_create(
            pk=2,
            defaults={
                "code": "FERIAS",
                "name": "Férias",
                "color": "#8B5CF6",
                "active": True,
            },
        )
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(
            f"/api/v1/escala-flex/config/absence-types/{absence_type.pk}/",
            {"name": "Férias atualizadas", "active": False, "observation": "Teste"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        absence_type.refresh_from_db()
        self.assertEqual(absence_type.name, "Férias atualizadas")
        self.assertFalse(absence_type.active)
        self.assertEqual(absence_type.observation, "Teste")

    def test_create_absence_type_preserves_spaces_in_code(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/config/absence-types/",
            {
                "code": "BANCO DE HORAS",
                "name": "Banco de horas",
                "color": "#F59E0B",
                "active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["code"], "BANCO DE HORAS")
        self.assertTrue(AbsenceType.objects.filter(code="BANCO DE HORAS").exists())
