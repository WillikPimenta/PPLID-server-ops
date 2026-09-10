from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import AuditoriaAtividade, AuditoriaAtividadeProtocolo
from apps.auditoria.services.atividade_import import confirm_import, create_import_staging, preview_import
from apps.auditoria.tests.test_atividade_import import build_sample_workbook

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class ProtocoloAnaliseApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="analise_user",
            email="analise@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)
        preview = preview_import(build_sample_workbook(), "contestacao.xlsx")
        staging = create_import_staging(
            user=self.user,
            file_bytes=build_sample_workbook(),
            filename="contestacao.xlsx",
            preview=preview,
        )
        self.atividade = confirm_import(
            staging=staging,
            user=self.user,
            nome="Atividade análise",
            data_recepcao=date(2026, 7, 1),
        )
        self.protocolo = self.atividade.protocolos.first()

    def test_save_protocolo_analise_with_etapas(self):
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}/protocolos/{self.protocolo.id}/analise/",
            {
                "brflow_raw": "linha brflow",
                "brflow_parsed": {"protocolo": self.protocolo.protocolo},
                "etapas": [
                    {
                        "resultado_correto": "APROVADO",
                        "nivel_dificuldade": "Fácil",
                        "tipo_documento": "RG",
                        "uf_documento": "SP",
                        "agente": "c12345a",
                        "tipo_falha": "Processual",
                        "etapa_falha": "AUDITORIA",
                        "cruzamento_bases": "NÃO POSSUI",
                        "qualidade_imagem": "Boa",
                        "situacao": "procedente",
                        "motivo_falha": "Motivo teste",
                    },
                    {
                        "resultado_correto": "SEM RISCO APARENTE",
                        "tipo_falha": "Processual",
                        "etapa_falha": "DIGITALIZAÇÃO",
                        "agente": "c12345a",
                        "situacao": "improcedente",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.protocolo.refresh_from_db()
        self.assertEqual(self.protocolo.status, AuditoriaAtividadeProtocolo.STATUS_EM_ANDAMENTO)
        self.assertEqual(self.protocolo.situacao, AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE)
        self.assertEqual(self.protocolo.etapas.count(), 2)

    def test_save_protocolo_analise_with_consideracoes_finais(self):
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}/protocolos/{self.protocolo.id}/analise/",
            {
                "consideracoes_finais": "Protocolo revisado sem pendências materiais.",
                "tipo_conclusao": "Manual",
                "reanalisado": True,
                "etapas": [
                    {
                        "resultado_correto": "APROVADO",
                        "tipo_falha": "Processual",
                        "etapa_falha": "AUDITORIA",
                        "agente": "c12345a",
                        "situacao": "improcedente",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.protocolo.refresh_from_db()
        self.assertEqual(
            self.protocolo.consideracoes_finais,
            "Protocolo revisado sem pendências materiais.",
        )
        self.assertEqual(self.protocolo.tipo_conclusao, "Manual")
        self.assertTrue(self.protocolo.reanalisado)
        self.assertEqual(response.data["consideracoes_finais"], self.protocolo.consideracoes_finais)
        self.assertEqual(response.data["tipo_conclusao"], "Manual")
        self.assertTrue(response.data["reanalisado"])

    def test_colaborador_requires_observacao(self):
        etapa = {
            "resultado_correto": "APROVADO",
            "tipo_falha": "Colaborador",
            "etapa_falha": "AUDITORIA",
            "agente": "c12345a",
            "situacao": "improcedente",
        }
        url = (
            f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}"
            f"/protocolos/{self.protocolo.id}/analise/"
        )

        missing = self.client.patch(url, {"etapas": [etapa]}, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertIn("consideracoes_finais", missing.data["errors"])

        saved = self.client.patch(
            url,
            {"etapas": [etapa], "consideracoes_finais": "Falha atribuída ao colaborador."},
            format="json",
        )
        self.assertEqual(saved.status_code, 200, saved.data)

    def test_finalizar_protocolo_analise_sets_concluido(self):
        etapa_payload = {
            "resultado_correto": "APROVADO",
            "tipo_falha": "Processual",
            "etapa_falha": "AUDITORIA",
            "agente": "c12345a",
            "situacao": "improcedente",
        }
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}/protocolos/{self.protocolo.id}/analise/",
            {"finalizar": True, "etapas": [etapa_payload]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("promovido"))
        self.protocolo.refresh_from_db()
        self.assertIsNotNone(self.protocolo.tratado_id)
        from apps.auditoria.models import AuditoriaFalhaCadastro

        tratado = AuditoriaFalhaCadastro.objects.filter(
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            protocolo=self.protocolo.protocolo,
        ).first()
        self.assertIsNotNone(tratado)
        self.assertIsNotNone(tratado.analise_concluida_em)
        self.assertTrue(response.data.get("promovido"))
        self.assertTrue(response.data.get("id"))

        for protocolo in list(self.atividade.protocolos.all()):
            self.client.patch(
                f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}/protocolos/{protocolo.id}/analise/",
                {"finalizar": True, "etapas": [etapa_payload]},
                format="json",
            )

        self.atividade.refresh_from_db()
        self.assertEqual(self.atividade.status, AuditoriaAtividade.STATUS_CONCLUIDA)
        self.assertIsNotNone(self.atividade.encerrado_em)
        detail = self.client.get(f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(str(detail.data["data_recepcao"]).startswith("2026-07-01"))
        self.assertIsNotNone(detail.data["sla_segundos"])
        self.assertIsNotNone(detail.data["sla_label"])

    def test_requires_complete_etapa(self):
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{self.atividade.id}/protocolos/{self.protocolo.id}/analise/",
            {
                "etapas": [
                    {
                        "tipo_falha": "Processual",
                        "agente": "c12345a",
                        "situacao": "procedente",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("etapas", response.data["errors"])
