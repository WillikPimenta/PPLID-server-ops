from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaFalha,
)
from apps.auditoria.services.analise_origem import get_or_create_analise_origem

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaDashboardApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="auditoria_dash_user",
            email="auditoria-dash@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

        self.atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade auditoria dashboard",
            workflow="Risk Manager - PICPAY",
            cliente="PICPAY",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            data_recepcao=date.today() - timedelta(days=2),
            created_by=self.user,
        )
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=self.atividade,
            protocolo="087501293",
            tipo_falha="Colaborador",
            usuario="c12345a",
            created_by=self.user,
        )
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=self.atividade,
            protocolo="087501293",
            tipo_falha="Automático",
            usuario="SISTEMA",
            created_by=self.user,
        )
        # Contestação não deve aparecer no dashboard de auditoria
        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
            nome="Atividade contestação",
            cliente="OUTRO",
            status=AuditoriaAtividade.STATUS_PENDENTE,
            created_by=self.user,
        )

    def test_dashboard_returns_auditoria_entrega(self):
        response = self.client.get("/api/v1/qualidade/auditoria/dashboard/")
        self.assertEqual(response.status_code, 200, response.data)

        entrega = response.data["entrega"]
        self.assertEqual(entrega["atividades"]["total"], 1)
        self.assertEqual(entrega["atividades"]["em_andamento"], 1)
        self.assertEqual(entrega["atividades"]["realizadas"], 0)

        self.assertEqual(entrega["falhas"]["total"], 2)
        self.assertEqual(entrega["falhas"]["pendentes"], 2)
        self.assertEqual(entrega["falhas"]["realizados"], 0)
        self.assertEqual(entrega["falhas"]["em_analise"], 2)
        self.assertEqual(entrega["falhas"]["media_por_atividade"], 2)

        self.assertIsNotNone(entrega["sla_auditoria"]["sla_medio_segundos"])
        self.assertEqual(len(entrega["por_cliente"]), 1)
        cliente = entrega["por_cliente"][0]
        self.assertEqual(cliente["cliente"], "PICPAY")
        self.assertEqual(cliente["falhas"], 2)
        self.assertEqual(cliente["falhas_pendentes"], 2)
        self.assertNotIn("remocoes", entrega)
        self.assertNotIn("protocolos", entrega)

    def test_dashboard_does_not_mix_auditoria_compliance_results(self):
        common = {
            "atividade": self.atividade,
            "tipo_falha": "Colaborador",
            "usuario": "c12345a",
            "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            "created_by": self.user,
        }
        AuditoriaFalhaCadastro.objects.create(protocolo="AUD-FRAUD", **common)
        AuditoriaFalhaCadastro.objects.create(
            protocolo="AUD-COMPLIANCE",
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            **common,
        )
        compliance_origin = get_or_create_analise_origem(
            protocolo="AUD-COMPLIANCE-CENTRAL",
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        AuditoriaFalhaCadastro.objects.create(
            protocolo="AUD-COMPLIANCE-CENTRAL",
            analise_origem=compliance_origin,
            brflow_parsed={},
            **common,
        )

        response = self.client.get("/api/v1/qualidade/auditoria/dashboard/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["entrega"]["falhas"]["realizados"], 1)
        self.assertEqual(response.data["entrega"]["falhas"]["total"], 3)
