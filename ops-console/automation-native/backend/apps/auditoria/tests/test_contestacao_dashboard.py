from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaControleRegistro,
)

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class ContestacaoDashboardApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="dashboard_user",
            email="dashboard@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

        self.atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
            nome="Atividade dashboard",
            workflow="Risk Manager - PICPAY",
            cliente="PICPAY",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            total_protocolos=2,
            data_recepcao=date.today() - timedelta(days=2),
            created_by=self.user,
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=self.atividade,
            protocolo="222222",
            excel_row=1,
            status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
            situacao=AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE,
            tipo_conclusao="Mantido",
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=self.atividade,
            protocolo="111111",
            excel_row=2,
            status=AuditoriaAtividadeProtocolo.STATUS_PENDENTE,
            situacao=AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE,
            reanalisado=True,
        )
        AuditoriaControleRegistro.objects.create(
            tipo=AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA,
            situacao="Nova",
            dados={
                "cliente": "PICPAY",
                "protocolo": "111111",
                "motivo": "Higienização",
                "tipo_acao": "Remoção",
                "data_abertura": (date.today() - timedelta(days=1)).isoformat(),
            },
            created_by=self.user,
        )

    def test_dashboard_returns_entrega_focus(self):
        response = self.client.get("/api/v1/qualidade/contestacao/dashboard/")
        self.assertEqual(response.status_code, 200, response.data)

        entrega = response.data["entrega"]
        self.assertEqual(entrega["atividades"]["total"], 1)
        self.assertEqual(entrega["atividades"]["em_andamento"], 1)
        self.assertEqual(entrega["atividades"]["realizadas"], 0)

        self.assertEqual(entrega["protocolos"]["total"], 2)
        self.assertEqual(entrega["protocolos"]["realizados"], 1)
        self.assertEqual(entrega["protocolos"]["pendentes"], 1)
        self.assertEqual(entrega["protocolos"]["aguardando"], 1)
        self.assertEqual(entrega["protocolos"]["media_por_atividade"], 2)
        self.assertEqual(entrega["protocolos"]["procedencia"], 1)
        self.assertEqual(entrega["protocolos"]["pct_procedencia"], 50.0)

        self.assertEqual(entrega["remocoes"]["em_andamento"], 1)
        self.assertEqual(entrega["remocoes"]["base_negativa"]["em_andamento"], 1)
        self.assertEqual(entrega["solicitacoes"]["total"], 0)

        self.assertIsNotNone(entrega["sla_contestacao"]["sla_medio_segundos"])
        self.assertIsNotNone(entrega["sla_contestacao"]["sla_medio_label"])
        self.assertIsNotNone(entrega["remocoes"]["base_negativa"]["sla_medio_segundos"])
        self.assertIsNotNone(entrega["remocoes"]["sla_medio_label"])

        self.assertEqual(len(entrega["por_cliente"]), 1)
        cliente = entrega["por_cliente"][0]
        self.assertEqual(cliente["cliente"], "PICPAY")
        self.assertEqual(cliente["atividades"], 1)
        self.assertEqual(cliente["protocolos_realizados"], 1)
        self.assertEqual(cliente["protocolos_pendentes"], 1)
        self.assertEqual(cliente["media_protocolos_por_atividade"], 2)
        self.assertEqual(cliente["protocolos_procedencia"], 1)
        self.assertEqual(cliente["pct_procedencia"], 50.0)
        self.assertIsNotNone(cliente["sla_medio_segundos"])
        self.assertGreaterEqual(cliente["sla_medio_segundos"], 2 * 24 * 3600 - 120)
