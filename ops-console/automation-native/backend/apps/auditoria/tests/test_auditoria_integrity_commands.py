from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.auditoria.models import (
    AuditoriaAtividade,
    QualidadePendenteAuditoria,
    QualidadePendenteAuditoriaFalha,
)


User = get_user_model()


class AuditoriaIntegrityCommandsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="auditoria-integrity")

    def test_cleanup_orphans_is_dry_run_by_default_and_applies_explicitly(self):
        orphan = QualidadePendenteAuditoria.objects.create(
            protocolo="ORFAO-1",
            created_by=self.user,
        )

        call_command("cleanup_auditoria_pending_orphans", stdout=StringIO())
        self.assertTrue(QualidadePendenteAuditoria.objects.filter(pk=orphan.pk).exists())

        call_command("cleanup_auditoria_pending_orphans", "--apply", stdout=StringIO())
        self.assertFalse(QualidadePendenteAuditoria.objects.filter(pk=orphan.pk).exists())

    def test_repair_concluded_is_dry_run_and_requires_explicit_id_to_apply(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria concluida inconsistente",
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
            created_by=self.user,
        )
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo="REPAIR-1",
            modulo="Risk Manager",
            tipo_falha="Colaborador",
            usuario="c123456a",
            novo_resultado="Aprovado",
            sinalizacao="Sim",
            motivo_falha="Documento ilegivel",
            etapa_falha="Analise visual",
            nivel_dificuldade="Alto",
            tipo_documento="RG",
            uf_documento="SP",
            qualidade_imagem="Ruim",
            created_by=self.user,
        )

        call_command("repair_auditoria_fraud_concluded", stdout=StringIO())
        self.assertEqual(atividade.tratados.count(), 0)
        self.assertEqual(atividade.falhas.count(), 1)

        call_command(
            "repair_auditoria_fraud_concluded",
            "--apply",
            "--activity-id",
            str(atividade.pk),
            stdout=StringIO(),
        )
        self.assertEqual(atividade.tratados.count(), 1)
        self.assertEqual(atividade.falhas.count(), 0)
