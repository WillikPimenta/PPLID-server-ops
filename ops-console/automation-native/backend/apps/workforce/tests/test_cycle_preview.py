from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.workforce.models import Agent, AgentHistory, CycleChangeAudit

User = get_user_model()


@override_settings(
    ACCESS_ENFORCEMENT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cycle-preview-tests",
        }
    },
)
class CycleChangePreviewApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.leader = Agent.objects.create(
            user_lan_id="c97001a",
            full_name="Leader Preview",
            active=True,
            hire_date=date(2019, 1, 1),
        )
        self.agent = Agent.objects.create(
            user_lan_id="c97002a",
            full_name="Preview Agent",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacional/Compliance",
            job_title="Agente Backoffice I",
            location="São Carlos",
            journey="14:00 - 20:00",
            band="B1",
            start_date=date(2024, 5, 31),
            active=True,
        )
        self.plan_user = User.objects.create_user(
            "plan.preview", email="plan.preview@test.local", password="x"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.plan_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client.force_authenticate(user=self.plan_user)

    def _preview_url(self):
        return f"/api/v1/agents/{self.agent.id}/preview-cycle-change/"

    def _apply_url(self):
        return f"/api/v1/agents/{self.agent.id}/apply-cycle-change/"

    def test_preview_returns_entities_and_technical(self):
        response = self.client.post(
            self._preview_url(),
            data={
                "action": "update",
                "movement_date": "2026-07-20",
                "cycle": {
                    "team": "Operacional/Compliance",
                    "job_title": "Agente Backoffice II",
                    "location": "São Carlos",
                    "leader": str(self.leader.id),
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertFalse(body["blocked"])
        self.assertTrue(body["preview_id"])
        self.assertTrue(body["cycle"]["preserves_history"])
        self.assertIsNotNone(body["cycle"]["closing"])
        self.assertIsNotNone(body["cycle"]["opening"])
        self.assertTrue(body["entities"])
        self.assertTrue(body["technical"])
        self.assertTrue(any(e["label"] == "Histórico de ciclos" for e in body["entities"]))
        self.assertTrue(any(t["table"] == "agent_history" for t in body["technical"]))

    def test_apply_with_preview_id_creates_audit(self):
        preview = self.client.post(
            self._preview_url(),
            data={
                "action": "update",
                "movement_date": "2026-07-20",
                "cycle": {
                    "team": "Novo Time",
                    "job_title": "Agente Backoffice I",
                    "location": "São Carlos",
                    "leader": str(self.leader.id),
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
            },
            format="json",
        ).json()
        # Seed catalog so validation passes if catalog already seeded empty for team
        from apps.workforce.models import HeadcountCatalogItem

        HeadcountCatalogItem.objects.get_or_create(
            catalog="team",
            value="Novo Time",
            defaults={"label": "Novo Time", "active": True, "sort_order": 1},
        )
        HeadcountCatalogItem.objects.get_or_create(
            catalog="team",
            value="Operacional/Compliance",
            defaults={"label": "Operacional/Compliance", "active": True, "sort_order": 2},
        )

        response = self.client.post(
            self._apply_url(),
            data={
                "action": "update",
                "movement_date": "2026-07-20",
                "preview_id": preview["preview_id"],
                "cycle": {
                    "team": "Novo Time",
                    "job_title": "Agente Backoffice I",
                    "location": "São Carlos",
                    "leader": str(self.leader.id),
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("cycle_change_result", body)
        self.assertTrue(body["cycle_change_result"]["operation_id"])
        self.assertTrue(
            CycleChangeAudit.objects.filter(agent=self.agent, success=True).exists()
        )

    def test_stale_preview_rejected(self):
        preview = self.client.post(
            self._preview_url(),
            data={
                "action": "update",
                "movement_date": "2026-07-20",
                "cycle": {
                    "team": "Operacional/Compliance",
                    "job_title": "Agente Backoffice II",
                    "location": "São Carlos",
                    "leader": str(self.leader.id),
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
            },
            format="json",
        ).json()
        self.agent.full_name = "Changed Elsewhere"
        self.agent.save(update_fields=["full_name", "updated_at"])

        response = self.client.post(
            self._apply_url(),
            data={
                "action": "update",
                "movement_date": "2026-07-20",
                "preview_id": preview["preview_id"],
                "cycle": {
                    "team": "Operacional/Compliance",
                    "job_title": "Agente Backoffice II",
                    "location": "São Carlos",
                    "leader": str(self.leader.id),
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("preview_id", response.json())
