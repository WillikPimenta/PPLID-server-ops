from django.test import SimpleTestCase

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.qualidade_operacional.services.intranet_metric_rules import (
    classify_intranet_source,
)


class FraudValidationIndicatorTests(SimpleTestCase):
    def test_em_validacao_fica_fora_mesmo_com_resultado_classificado(self):
        classification = classify_intranet_source(
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
            tipo_falha="Automático",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        )

        self.assertFalse(classification.is_auditado)
        self.assertFalse(classification.is_falha)
        self.assertEqual(classification.skip_reason, "status_falha_em_validacao")
        self.assertIn("status_falha_em_validacao", classification.warnings)

    def test_sem_falha_ativa_permanece_no_denominador(self):
        classification = classify_intranet_source(
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            tipo_falha="Sem Falha",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        )

        self.assertTrue(classification.is_auditado)
        self.assertFalse(classification.is_falha)
