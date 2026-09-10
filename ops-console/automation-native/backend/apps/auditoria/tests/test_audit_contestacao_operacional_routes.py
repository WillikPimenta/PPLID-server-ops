from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    QualidadeAnaliseOrigem,
)


class AuditContestacaoOperacionalRoutesTests(TestCase):
    def setUp(self):
        origem = QualidadeAnaliseOrigem.objects.create(
            protocolo="ROTA-COMP-1",
            contexto={"fila_contexto": "auditoria_compliance"},
            conteudo_hash="audit-contestacao-rota-comp-1",
        )
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="ROTA-COMP-1",
            tipo_falha="critica",
            usuario="agente.teste",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            data_analise=timezone.now(),
            analise_origem=origem,
        )
        self.item = ContestacaoOperacional.objects.create(
            falha=falha,
            dominio=ContestacaoOperacional.DOMINIO_FRAUD,
            origem_tecnica=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            justificativa_lider="teste",
            protocolo=falha.protocolo,
            agente_usuario=falha.usuario,
            atribuida_em=falha.data_analise,
        )

    def test_dry_run_reports_without_changing_data(self):
        output = StringIO()

        call_command("audit_contestacao_operacional_routes", stdout=output)

        self.item.refresh_from_db()
        self.assertEqual(self.item.dominio, ContestacaoOperacional.DOMINIO_FRAUD)
        self.assertEqual(
            self.item.categoria,
            ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
        )
        self.assertFalse(self.item.historico.exists())
        self.assertIn("modo=dry-run", output.getvalue())
        self.assertIn("divergentes=1", output.getvalue())

    def test_apply_reclassifies_and_records_history(self):
        output = StringIO()

        call_command(
            "audit_contestacao_operacional_routes",
            apply=True,
            stdout=output,
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.dominio, ContestacaoOperacional.DOMINIO_COMPLIANCE)
        self.assertEqual(
            self.item.categoria,
            ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
        )
        historico = self.item.historico.get()
        self.assertEqual(
            historico.evento,
            historico.EVENTO_RECLASSIFICADA,
        )
        self.assertEqual(historico.get_evento_display(), "Reclassificada")
        self.assertIn("fraud/auditoria_fraud", historico.justificativa)
        self.assertIn("compliance/auditoria_compliance", historico.justificativa)
        self.assertIn("modo=apply", output.getvalue())
        self.assertIn("corrigidas=1", output.getvalue())
