from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


class SeedDevSafetyTests(TestCase):
    def setUp(self):
        self.real_agent = Agent.objects.create(
            user_lan_id="c94002a",
            full_name="Colaborador Real",
            email="real@empresa.local",
            hire_date=date(2020, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.real_agent,
            start_date=date(2024, 1, 1),
            team="Operações",
            job_title="Analista",
            active=True,
        )

    def test_seed_dev_skips_dev_agents_when_real_headcount_exists(self):
        call_command("seed_dev")
        self.real_agent.refresh_from_db()
        self.assertEqual(self.real_agent.email, "real@empresa.local")
        self.assertFalse(Agent.objects.filter(user_lan_id="operador001").exists())

    def test_seed_dev_force_does_not_touch_real_agents(self):
        call_command("seed_dev", force=True)
        self.real_agent.refresh_from_db()
        self.assertEqual(self.real_agent.email, "real@empresa.local")
        self.assertTrue(Agent.objects.filter(user_lan_id="operador001").exists())

    def test_repair_seed_dev_damage_restores_real_agent(self):
        real = Agent.objects.create(
            user_lan_id="c91763a",
            full_name="Matheus Mendes Da Silva",
            email="matheus.mendes@experian.com",
            hire_date=date(2018, 1, 15),
            active=True,
        )
        closed = AgentHistory.objects.create(
            agent=real,
            start_date=date(2025, 12, 1),
            final_date=date(2026, 7, 7),
            team="Planejamento",
            job_title="Analista de Planejamento Operacional II",
            active=False,
        )
        AgentHistory.objects.create(
            agent=real,
            start_date=date(2026, 6, 9),
            team="Planejamento",
            job_title="Coordenador",
            job_activity="BrFlow",
            active=True,
        )
        User.objects.create_user(
            "c91763a",
            password="x",
            is_staff=True,
            is_superuser=True,
        )

        call_command("repair_seed_dev_damage")

        real.refresh_from_db()
        closed.refresh_from_db()
        self.assertEqual(real.email, "matheus.msilva@experian.com")
        self.assertEqual(real.hire_date, date(2021, 6, 21))
        self.assertIsNone(closed.final_date)
        self.assertTrue(closed.active)
        self.assertFalse(
            AgentHistory.objects.filter(
                agent=real,
                job_title="Coordenador",
                final_date__isnull=True,
            ).exists()
        )
        user = User.objects.get(username="c91763a")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
