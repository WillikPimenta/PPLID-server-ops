"""Testes unitários do serviço de ciência de leitura."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.communication.models import News, NewsAcknowledgment
from apps.communication.services.ciencia_leitura import (
    filter_options,
    list_recipients,
    resolve_status,
)
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class CienciaStatusTests(SimpleTestCase):
    def test_agendado_quando_publicacao_futura(self):
        news = News(published_at=timezone.now() + timedelta(hours=2), is_critical=True)
        self.assertEqual(resolve_status(news, pending_count=10), "agendado")

    def test_concluido_sem_pendentes(self):
        news = News(published_at=timezone.now() - timedelta(minutes=1), is_critical=True)
        self.assertEqual(resolve_status(news, pending_count=0), "concluido")

    def test_em_andamento_com_pendentes(self):
        news = News(published_at=timezone.now() - timedelta(days=1), is_critical=True)
        self.assertEqual(resolve_status(news, pending_count=3), "em_andamento")


class CienciaLeaderFilterTests(TestCase):
    def setUp(self):
        self.leader_a = Agent.objects.create(full_name="Lider Alfa", user_lan_id="ldr_a")
        self.leader_b = Agent.objects.create(full_name="Lider Beta", user_lan_id="ldr_b")
        self.closed_leader = Agent.objects.create(
            full_name="Lider Encerrado", user_lan_id="ldr_old"
        )

        self.agent_a = Agent.objects.create(full_name="Agente A", user_lan_id="agt_a")
        self.agent_b = Agent.objects.create(full_name="Agente B", user_lan_id="agt_b")
        self.agent_nolider = Agent.objects.create(
            full_name="Agente Sem Lider", user_lan_id="agt_nl"
        )

        self.user_a = User.objects.create_user(
            "agt_a", password="x", email="agt_a@test.local", is_active=True
        )
        self.user_b = User.objects.create_user(
            "agt_b", password="x", email="agt_b@test.local", is_active=True
        )
        self.user_nl = User.objects.create_user(
            "agt_nl", password="x", email="agt_nl@test.local", is_active=True
        )

        UserProfile.objects.create(user=self.user_a, agent=self.agent_a)
        UserProfile.objects.create(user=self.user_b, agent=self.agent_b)
        UserProfile.objects.create(user=self.user_nl, agent=self.agent_nolider)

        AgentHistory.objects.create(
            agent=self.agent_a,
            leader=self.closed_leader,
            location="SP",
            team="Time Antigo",
            team_sector="Backoffice",
            journey_shift="Manhã",
            start_date=date(2023, 1, 1),
            final_date=date(2023, 12, 31),
            active=False,
        )
        AgentHistory.objects.create(
            agent=self.agent_a,
            leader=self.leader_a,
            location="Curitiba",
            team="Time Alfa",
            team_sector="Backoffice",
            journey_shift="Manhã",
            start_date=date(2024, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent_b,
            leader=self.leader_b,
            location="São Paulo",
            team="Time Beta",
            team_sector="Atendimento",
            journey_shift="Tarde",
            start_date=date(2024, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent_nolider,
            leader=None,
            location="BH",
            team="Time Livre",
            team_sector="Backoffice",
            journey_shift="Manhã",
            start_date=date(2024, 1, 1),
            active=True,
        )

        self.author = User.objects.create_user(
            "author_cl", password="x", email="author_cl@test.local"
        )
        self.news = News.objects.create(
            title="Comunicado crítico",
            category="Geral",
            author="QA",
            summary="resumo",
            content="conteudo",
            published_at=timezone.now() - timedelta(hours=1),
            active=True,
            is_critical=True,
            created_by=self.author,
        )
        NewsAcknowledgment.objects.create(
            news=self.news,
            user=self.user_b,
        )

    def test_filter_options_returns_distinct_current_leaders(self):
        options = filter_options()
        leaders = options["leaders"]
        values = {item["value"] for item in leaders}
        labels = {item["label"] for item in leaders}
        self.assertIn(str(self.leader_a.pk), values)
        self.assertIn(str(self.leader_b.pk), values)
        self.assertNotIn(str(self.closed_leader.pk), values)
        self.assertIn("Lider Alfa", labels)
        self.assertIn("Lider Beta", labels)
        self.assertNotIn("Lider Encerrado", labels)
        self.assertEqual(leaders, sorted(leaders, key=lambda item: item["label"].lower()))

    def test_leader_id_filters_pending_and_acknowledged(self):
        pending = list_recipients(
            {
                "communicationId": str(self.news.pk),
                "situation": "pending",
                "leaderId": str(self.leader_a.pk),
            }
        )
        self.assertEqual(pending["count"], 1)
        self.assertEqual(pending["results"][0]["registrationNumber"], "agt_a")

        ack = list_recipients(
            {
                "communicationId": str(self.news.pk),
                "situation": "acknowledged",
                "leader_id": str(self.leader_b.pk),
            }
        )
        self.assertEqual(ack["count"], 1)
        self.assertEqual(ack["results"][0]["registrationNumber"], "agt_b")

    def test_leader_combined_with_area_unit_shift(self):
        result = list_recipients(
            {
                "communicationId": str(self.news.pk),
                "situation": "pending",
                "leaderId": str(self.leader_a.pk),
                "area": "Backoffice",
                "unit": "Curitiba",
                "shift": "Manhã",
            }
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["registrationNumber"], "agt_a")

        empty = list_recipients(
            {
                "communicationId": str(self.news.pk),
                "situation": "pending",
                "leaderId": str(self.leader_a.pk),
                "unit": "São Paulo",
            }
        )
        self.assertEqual(empty["count"], 0)

    def test_user_without_leader_excluded_from_leader_filter(self):
        result = list_recipients(
            {
                "communicationId": str(self.news.pk),
                "situation": "pending",
                "leaderId": str(self.leader_a.pk),
            }
        )
        regs = {row["registrationNumber"] for row in result["results"]}
        self.assertNotIn("agt_nl", regs)
        self.assertIn("agt_a", regs)
