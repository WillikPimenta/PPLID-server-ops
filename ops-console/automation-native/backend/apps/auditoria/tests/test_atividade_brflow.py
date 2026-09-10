from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaFalha,
)

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaAtividadeBrflowFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="auditor-brflow",
            email="auditor-brflow@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def test_from_brflow_creates_auditoria_atividade(self):
        response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/from-brflow/",
            {
                "brflow_raw": "linha brflow",
                "brflow_parsed": {
                    "protocolo": "087501293",
                    "cliente": "PicPay",
                    "workflow": "Risk Manager",
                    "nivel_hierarquico": "Digitalizador",
                    "resultado_analise": "Sem risco aparente",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["tipo"], "auditoria")
        self.assertIn("087501293", response.data["nome"])
        self.assertEqual(response.data["cliente"], "PicPay")
        self.assertTrue(response.data.get("protocolo_id"))
        self.assertEqual(response.data["id"], AuditoriaAtividade.objects.get(tipo="auditoria").id)
        self.assertNotIn("public_id", response.data)
        self.assertEqual(AuditoriaAtividade.objects.filter(tipo="auditoria").count(), 1)
        atividade = AuditoriaAtividade.objects.get(tipo="auditoria")
        self.assertEqual(atividade.protocolos.count(), 1)
        self.assertEqual(atividade.total_protocolos, 1)

    def test_from_brflow_creates_with_session_authenticated_user(self):
        session_client = APIClient()
        session_client.force_login(self.user)

        response = session_client.post(
            "/api/v1/qualidade/auditoria/atividades/from-brflow/",
            {
                "brflow_raw": "linha brflow",
                "brflow_parsed": {"protocolo": "087501294"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(AuditoriaAtividade.objects.filter(tipo="auditoria").count(), 1)

    def test_from_brflow_blocks_when_user_has_open_auditoria(self):
        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria aberta",
            cliente="PicPay",
            workflow="Risk Manager",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"protocolo": "111222333"},
        )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                "/api/v1/qualidade/auditoria/atividades/from-brflow/",
                {
                    "brflow_raw": "linha brflow",
                    "brflow_parsed": {
                        "protocolo": "087501293",
                        "cliente": "PicPay",
                        "workflow": "Risk Manager",
                        "nivel_hierarquico": "Digitalizador",
                        "resultado_analise": "Sem risco aparente",
                    },
                },
                format="json",
            )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn("atividade_em_andamento", response.data)
        self.assertEqual(response.data["atividade_em_andamento"]["nome"], "Auditoria aberta")
        self.assertEqual(AuditoriaAtividade.objects.filter(tipo="auditoria").count(), 1)
        if connection.features.has_select_for_update:
            self.assertTrue(
                any("FOR UPDATE" in query["sql"].upper() for query in queries.captured_queries)
            )

    def test_legacy_duplicate_open_fraud_audits_remain_compatible(self):
        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Primeira auditoria",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
        )

        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Segunda auditoria legada",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_PENDENTE,
        )

        response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/from-brflow/",
            {
                "brflow_raw": "linha brflow",
                "brflow_parsed": {"protocolo": "087501293"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(AuditoriaAtividade.objects.filter(tipo="auditoria").count(), 2)

    @patch("apps.auditoria.views_atividades.user_has_permission", return_value=False)
    def test_non_owner_cannot_mutate_another_users_activity(self, _permission):
        other_user = User.objects.create_user(username="owner-auditoria")
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria de outro usuario",
            created_by=other_user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"trilha_raw": "trilha preenchida"},
        )

        patch_response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/",
            {"observacao": "tentativa indevida"},
            format="json",
        )
        delete_response = self.client.delete(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/"
        )
        finalize_response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/finalizar/"
        )

        self.assertEqual(patch_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(finalize_response.status_code, 403)
        atividade.refresh_from_db()
        self.assertEqual(atividade.observacao, "")
        self.assertEqual(atividade.status, AuditoriaAtividade.STATUS_EM_ANDAMENTO)

    def test_list_mine_andamento_filters_open_auditoria(self):
        open_atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Minha auditoria aberta",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
        )
        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Minha auditoria concluída",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
        )
        other_user = User.objects.create_user(
            username="outro-auditor",
            email="outro@test.local",
            password="test12345",
        )
        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria de outro usuário",
            created_by=other_user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
        )

        response = self.client.get(
            "/api/v1/qualidade/auditoria/atividades/",
            {"tipo": "auditoria", "mine": "1", "andamento": "1"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], open_atividade.id)

    def test_create_falha_inside_atividade(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade teste",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={
                "protocolo": "087501293",
                "resultado_analise": "OK",
                "trilha_raw": "trilha preenchida",
            },
        )
        response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/falhas/",
            {
                "protocolo": "087501293",
                "brflow_raw": "",
                "brflow_parsed": {},
                "modulo": "G Auditoria",
                "demanda_url": "",
                "tipo_falha": "Automático",
                "usuario": "",
                "resultado_cliente": "OK",
                "novo_resultado": "",
                "sinalizacao": "",
                "motivo_falha": "",
                "etapa_falha": "Risk Manager - Processo Automático",
                "tempo_analise": "00:00:42",
                "nivel_dificuldade": "",
                "tipo_documento": "",
                "uf_documento": "",
                "tipo_registro": "auditoria",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(AuditoriaFalhaCadastro.objects.filter(atividade=atividade).count(), 0)
        self.assertEqual(QualidadePendenteAuditoriaFalha.objects.filter(atividade=atividade).count(), 1)
        self.assertEqual(
            response.data["id"],
            QualidadePendenteAuditoriaFalha.objects.get(atividade=atividade).id,
        )
        self.assertNotIn("public_id", response.data)
        self.assertNotIn("parent_public_id", response.data)
        self.assertIsNone(response.data["agente_id"])
        self.assertIsNone(response.data["auditor_id"])
        self.assertEqual(response.data["tempo_analise"], "00:00:42")

        listed_falhas = self.client.get(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/falhas/",
        )
        self.assertEqual(listed_falhas.status_code, 200, listed_falhas.data)
        self.assertEqual(len(listed_falhas.data["results"]), 1)
        self.assertIsNone(listed_falhas.data["results"][0]["agente_id"])
        self.assertIsNone(listed_falhas.data["results"][0]["auditor_id"])
        self.assertEqual(listed_falhas.data["results"][0]["analise_status"], "nao_atribuido")
        self.assertIn("resultado_qualidade", listed_falhas.data["results"][0])
        self.assertNotIn("codigo_irregularidade", listed_falhas.data["results"][0])
        self.assertNotIn("mapping_scenario_status", listed_falhas.data["results"][0])

        listed = self.client.get("/api/v1/qualidade/auditoria/atividades/?tipo=auditoria")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data["results"]), 1)
        self.assertNotIn("public_id", listed.data["results"][0])

    def test_create_falha_requires_trilha_analise(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade sem trilha",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"protocolo": "087501293", "resultado_analise": "OK"},
        )

        response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/falhas/",
            {
                "protocolo": "087501293",
                "tipo_falha": "Automático",
                "usuario": "",
                "tipo_registro": "auditoria",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Trilha de Análise", str(response.data))

    def test_batch_rolls_back_all_rows_when_one_is_invalid(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade lote atomico",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"trilha_raw": "trilha preenchida"},
        )
        base = {
            "protocolo": "BATCH-1",
            "brflow_raw": "trilha preenchida",
            "brflow_parsed": {},
            "modulo": "Risk Manager",
            "demanda_url": "",
            "usuario": "",
            "resultado_cliente": "OK",
            "novo_resultado": "",
            "sinalizacao": "",
            "motivo_falha": "",
            "etapa_falha": "Risk Manager - Processo Automático",
            "tempo_analise": "",
            "nivel_dificuldade": "",
            "tipo_documento": "",
            "uf_documento": "",
            "qualidade_imagem": "",
            "tipo_registro": "auditoria",
        }

        response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/falhas/lote/",
            {
                "falhas": [
                    {**base, "tipo_falha": "Automático"},
                    {**base, "tipo_falha": "TIPO INVALIDO"},
                ],
                "finalizar": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            QualidadePendenteAuditoriaFalha.objects.filter(atividade=atividade).count(),
            0,
        )

    def test_trilha_brflow_seeds_falhas(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade trilha",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"protocolo": "087501293", "resultado_analise": "OK"},
        )
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/trilha-brflow/",
            {
                "trilha_raw": "trilha exemplo",
                "extracted_users": ["Agente Um", "Agente Dois"],
                "etapas_por_usuario": {
                    "Agente Um": "Comparação De Selfie",
                    "Agente Dois": "Sobreposição",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        falhas = list(
            QualidadePendenteAuditoriaFalha.objects.filter(atividade=atividade).order_by("id")
        )
        # 2 colaboradores + 1 automático
        self.assertEqual(len(falhas), 3)
        for falha in falhas:
            self.assertTrue(falha.id)
        usuarios = {f.usuario for f in falhas if f.usuario}
        self.assertEqual(usuarios, {"Agente Um", "Agente Dois"})
        auto = [f for f in falhas if not f.usuario]
        self.assertEqual(len(auto), 1)

        # Idempotente: não duplica
        again = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/trilha-brflow/",
            {
                "trilha_raw": "trilha exemplo",
                "extracted_users": ["Agente Um", "Agente Dois"],
            },
            format="json",
        )
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(AuditoriaFalhaCadastro.objects.filter(atividade=atividade).count(), 0)
        self.assertEqual(QualidadePendenteAuditoriaFalha.objects.filter(atividade=atividade).count(), 3)

    def test_trilha_preserva_mesmo_usuario_em_etapas_diferentes(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade com etapas repetidas",
            created_by=self.user,
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"protocolo": "087501293", "resultado_analise": "OK"},
        )

        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/trilha-brflow/",
            {
                "trilha_raw": "trilha exemplo",
                "extracted_users": ["Agente Um", "Agente Um"],
                "trilha_hits": [
                    {
                        "usuario": "Agente Um",
                        "etapa_falha": "Comparacao de Selfie",
                        "tempo_analise": "00:00:08",
                    },
                    {
                        "usuario": "Agente Um",
                        "etapa_falha": "Sobreposicao",
                        "tempo_analise": "00:00:26",
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        rows = list(
            QualidadePendenteAuditoriaFalha.objects.filter(
                atividade=atividade,
                usuario="Agente Um",
            ).order_by("id")
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [(row.etapa_falha, row.tempo_analise) for row in rows],
            [
                ("Comparacao de Selfie", "00:00:08"),
                ("Sobreposicao", "00:00:26"),
            ],
        )

    def test_patch_observacao_on_atividade(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade observação",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            created_by=self.user,
        )
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/",
            {"observacao": "  Divergência no resultado do cliente  "},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["observacao"], "Divergência no resultado do cliente")
        atividade.refresh_from_db()
        self.assertEqual(atividade.observacao, "Divergência no resultado do cliente")
