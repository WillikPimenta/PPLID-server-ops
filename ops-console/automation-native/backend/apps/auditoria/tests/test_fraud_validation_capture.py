from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaCatalogItem,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaFalha,
    ReinspecaoAuditorPresence,
)
from apps.auditoria.services.atividade_falhas import (
    finalizar_atividade_auditoria,
    save_falhas_batch,
)
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeIntranetProjection,
)
from apps.qualidade_operacional.services.intranet_source import SyncReport, sync_one


User = get_user_model()


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class FraudValidationCaptureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="fraud_validation",
            email="fraud-validation@test.local",
            password="test12345",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        for sort_order, value in enumerate(("Automático", "Sem Falha")):
            AuditoriaCatalogItem.objects.get_or_create(
                catalog=AuditoriaCatalogItem.CATALOG_TIPO_FALHA,
                value=value,
                defaults={
                    "label": value,
                    "sort_order": sort_order,
                    "active": True,
                },
            )

    def _atividade(self, suffix: str) -> AuditoriaAtividade:
        return AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome=f"Auditoria Fraud {suffix}",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            brflow_parsed={"trilha_raw": "Trilha de analise preenchida"},
            created_by=self.user,
        )

    @staticmethod
    def _falha_payload(protocolo: str, *, tipo_falha: str = "Automático") -> dict:
        return {
            "protocolo": protocolo,
            "tipo_falha": tipo_falha,
            "usuario": "" if tipo_falha == "Automático" else "c12345a",
            "modulo": "G Auditoria",
            "novo_resultado": "Resultado revisado",
            "sinalizacao": "Sinalizacao divergente",
            "motivo_falha": "Documento divergente",
            "etapa_falha": "Analise automatica",
            "nivel_dificuldade": "Medio",
            "tipo_documento": "RG",
            "uf_documento": "SP",
            "qualidade_imagem": "Boa",
        }

    def _assert_pending_without_projection(self, tratado: AuditoriaFalhaCadastro) -> None:
        self.assertEqual(
            tratado.status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
        )
        self.assertEqual(
            tratado.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
        )

        report = SyncReport()
        projection = sync_one(tratado, force=True, report=report)

        self.assertIsNone(projection)
        self.assertEqual(report.by_skip_reason, {"status_falha_em_validacao": 1})
        self.assertFalse(
            QualidadeIntranetProjection.objects.filter(source=tratado).exists()
        )
        self.assertFalse(
            QualidadeAuditado.objects.filter(protocolo=tratado.protocolo).exists()
        )
        self.assertFalse(
            QualidadeFalha.objects.filter(protocolo=tratado.protocolo).exists()
        )

    def test_finalizacao_individual_promove_falha_para_validacao_sem_fatos(self):
        atividade = self._atividade("individual")
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            created_by=self.user,
            **self._falha_payload("FRAUD-INDIVIDUAL"),
        )

        finalizar_atividade_auditoria(atividade, finalizador=self.user)

        tratado = AuditoriaFalhaCadastro.objects.get(protocolo="FRAUD-INDIVIDUAL")
        self._assert_pending_without_projection(tratado)

    def test_finalizacao_em_lote_usa_a_mesma_captura_fraud(self):
        atividade = self._atividade("lote")
        payload = self._falha_payload("FRAUD-LOTE")
        payload["observacao"] = "Evidencia preservada ate a promocao."

        atividade, rascunhos = save_falhas_batch(
            self.user,
            atividade,
            [payload],
            finalizar=True,
        )

        self.assertEqual(rascunhos, [])
        self.assertEqual(atividade.status, AuditoriaAtividade.STATUS_CONCLUIDA)
        tratado = AuditoriaFalhaCadastro.objects.get(protocolo="FRAUD-LOTE")
        self.assertEqual(
            tratado.status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
        )
        self.assertEqual(
            tratado.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
        )
        self.assertEqual(tratado.observacao, payload["observacao"])

    def test_promocao_sem_falha_nao_entra_em_validacao(self):
        atividade = self._atividade("sem-falha")
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo="FRAUD-SEM-FALHA",
            tipo_falha="Sem Falha",
            usuario="c12345a",
            created_by=self.user,
        )

        finalizar_atividade_auditoria(atividade, finalizador=self.user)

        tratado = AuditoriaFalhaCadastro.objects.get(protocolo="FRAUD-SEM-FALHA")
        self.assertEqual(
            tratado.status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
        self.assertEqual(
            tratado.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
        )

    def test_post_direto_captura_fraud_e_preserva_sem_falha_e_outros_fluxos(self):
        fraud = self.client.post(
            "/api/v1/qualidade/auditoria/falhas/",
            self._falha_payload("FRAUD-DIRETO"),
            format="json",
        )
        sem_falha = self.client.post(
            "/api/v1/qualidade/auditoria/falhas/",
            self._falha_payload("FRAUD-DIRETO-SEM", tipo_falha="Sem Falha"),
            format="json",
        )
        contestacao_payload = {
            **self._falha_payload("CONTESTACAO-DIRETA"),
            "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
        }
        contestacao = self.client.post(
            "/api/v1/qualidade/auditoria/falhas/",
            contestacao_payload,
            format="json",
        )
        compliance_payload = {
            **self._falha_payload("COMPLIANCE-DIRETA"),
            "brflow_parsed": {
                "fila_contexto": (
                    ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE
                )
            },
        }
        compliance = self.client.post(
            "/api/v1/qualidade/auditoria/falhas/",
            compliance_payload,
            format="json",
        )

        self.assertEqual(fraud.status_code, 201, fraud.data)
        self.assertEqual(sem_falha.status_code, 201, sem_falha.data)
        self.assertEqual(contestacao.status_code, 201, contestacao.data)
        self.assertEqual(compliance.status_code, 201, compliance.data)

        records = {
            row.protocolo: row
            for row in AuditoriaFalhaCadastro.objects.filter(
                protocolo__in={
                    "FRAUD-DIRETO",
                    "FRAUD-DIRETO-SEM",
                    "CONTESTACAO-DIRETA",
                    "COMPLIANCE-DIRETA",
                }
            )
        }
        self.assertEqual(
            records["FRAUD-DIRETO"].status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
        )
        self.assertEqual(
            records["FRAUD-DIRETO-SEM"].status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
        self.assertEqual(
            records["CONTESTACAO-DIRETA"].status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
        self.assertEqual(
            records["COMPLIANCE-DIRETA"].status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
