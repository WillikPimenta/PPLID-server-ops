# -*- coding: utf-8 -*-
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import (
    QualidadeAgenteAcao,
    QualidadeAuditado,
    QualidadeFalha,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class QualidadeAgenteDetalheApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qo_agent_detail", password="x", email="qo_ad@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.agent = Agent.objects.create(
            full_name="Gustavo Teste", user_lan_id="c90001a", active=True
        )
        self.leader = Agent.objects.create(
            full_name="Gabriel Líder", user_lan_id="c90002a", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            start_date=date(2026, 1, 1),
            team="Onboarding Protection",
            job_title="Operador",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="G Auditoria",
            matricula="c90001a",
            protocolo="QA-100",
            etapa="Validação",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            id_workflow=144,
            protocolo="QA-100",
            matricula="c90001a",
            tipo_falha="Manual",
            tipo_falha_oficial="Validação de identidade",
            categoria_falha="Crítica",
            etapa="Validação",
            source_file="test",
        )

    def test_agente_detalhe_requires_matricula(self):
        res = self.client.get("/api/v1/qualidade/operacional/agente-detalhe/")
        self.assertEqual(res.status_code, 400)

    def test_agente_detalhe_ok(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/agente-detalhe/",
            {
                "matricula": "c90001a",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "workforce_only": "1",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        self.assertEqual(res.data["agent"]["matricula"], "c90001a")
        self.assertIn("kpis", res.data)
        self.assertIn("serie", res.data)
        self.assertIn("falhas_distribuicao", res.data)
        self.assertIn("leitura_rapida", res.data)

    def test_criar_e_listar_acao(self):
        create = self.client.post(
            "/api/v1/qualidade/operacional/agente-acoes/",
            {
                "matricula": "c90001a",
                "titulo": "Reciclagem identidade",
                "tipo": "reciclagem",
                "responsavel": "Gabriel Líder",
                "prioridade": "alta",
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        self.assertTrue(create.data["ok"])
        self.assertEqual(QualidadeAgenteAcao.objects.count(), 1)

        listed = self.client.get(
            "/api/v1/qualidade/operacional/agente-acoes/",
            {"matricula": "c90001a"},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["count"], 1)
        self.assertEqual(listed.data["results"][0]["titulo"], "Reciclagem identidade")
