from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_OP_LIDER,
    ROLE_PLAN_ASSISTENTE,
    ROLE_PROC_USUARIO,
    ROLE_QUAL_CAPACITACAO,
)
from apps.access.inference import infer_roles_for_agent, is_leader_lan_id
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


class InferenceTests(TestCase):
    def _agent(self, lan: str, name: str = "Test User") -> Agent:
        return Agent.objects.create(
            user_lan_id=lan,
            full_name=name,
            active=True,
            hire_date=date(2020, 1, 1),
        )

    def _history(self, agent, **kwargs) -> AgentHistory:
        defaults = {
            "team": "Operacional/Fraud",
            "job_title": "Agente Backoffice I",
            "start_date": date(2024, 1, 1),
            "active": True,
            "final_date": None,
        }
        defaults.update(kwargs)
        return AgentHistory.objects.create(agent=agent, **defaults)

    def test_op_agente_inference(self):
        agent = self._agent("c90001a")
        self._history(agent)
        roles = infer_roles_for_agent(agent)
        self.assertIn(ROLE_OP_AGENTE, roles)

    def test_op_lider_inference_when_leader_of_others(self):
        leader = self._agent("c90002a", "Leader")
        sub = self._agent("c90003a", "Sub")
        self._history(leader, job_title="Líder De Backoffice I")
        self._history(sub, leader=leader)
        self.assertTrue(is_leader_lan_id("c90002a"))
        roles = infer_roles_for_agent(leader)
        self.assertIn(ROLE_OP_LIDER, roles)

    def test_plan_assistente_inference(self):
        agent = self._agent("c90004a")
        self._history(
            agent,
            team="Planejamento",
            job_title="Assistente De Planejamento Operacional II",
        )
        roles = infer_roles_for_agent(agent)
        self.assertIn(ROLE_PLAN_ASSISTENTE, roles)

    def test_proc_usuario_inference(self):
        agent = self._agent("c90005a")
        self._history(agent, team="Processos", job_title="Analista De Gestão De Projetos II")
        roles = infer_roles_for_agent(agent)
        self.assertIn(ROLE_PROC_USUARIO, roles)

    def test_qual_capacitacao_inference(self):
        agent = self._agent("c90006a")
        self._history(
            agent,
            team="Auditoria/Fraud",
            job_title="Assistente De Capacitacao II",
        )
        roles = infer_roles_for_agent(agent)
        self.assertIn(ROLE_QUAL_CAPACITACAO, roles)

    def test_qualidade_title_without_capacitacao_does_not_infer(self):
        agent = self._agent("c90006b")
        self._history(
            agent,
            team="Auditoria/Fraud",
            job_title="Assistente De Qualidade II",
        )
        roles = infer_roles_for_agent(agent)
        self.assertNotIn(ROLE_QUAL_CAPACITACAO, roles)
