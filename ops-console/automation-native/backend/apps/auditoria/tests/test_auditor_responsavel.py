from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    ReinspecaoFilaHistorico,
)
from apps.auditoria.services.contestacao_operacional import (
    build_erros_procedentes_por_auditor,
)
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


class AuditorResponsavelModelTests(TestCase):
    def test_fk_usa_protect(self):
        field = AuditoriaFalhaCadastro._meta.get_field("auditor_responsavel")
        self.assertIs(field.remote_field.model, User)
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

        auditor = User.objects.create_user(
            "auditor.protected", email="auditor.protected@test.local", password="x"
        )
        AuditoriaFalhaCadastro.objects.create(
            protocolo="PROTECT-1",
            tipo_falha="Colaborador",
            usuario="agente",
            auditor_responsavel=auditor,
        )
        with self.assertRaises(ProtectedError):
            auditor.delete()


class AuditorResponsavelBackfillTests(TestCase):
    def setUp(self):
        self.finalizador = User.objects.create_user(
            "finalizador.exato", email="finalizador.exato@test.local", password=None
        )
        self.legado_user = User.objects.create_user(
            "legado.unico", email="legado.unico@test.local", password=None
        )
        self.outro = User.objects.create_user(
            "outro.usuario", email="outro.usuario@test.local", password=None
        )
        self.tecnico = User.objects.create_user(
            "conta.tecnica", email="conta.tecnica@test.local", password=None
        )

        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
            nome="Backfill contestação",
            created_by=self.outro,
        )
        protocolo = AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade,
            protocolo="BF-CONTEST",
            excel_row=1,
            status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
            analisado_por=self.finalizador,
            analisado_em=timezone.now(),
            finalizado_em=timezone.now(),
        )
        self.contestacao = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-CONTEST",
            protocolo_origem=protocolo,
            tipo_falha="Colaborador",
            usuario="agente",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
        )

        self.reinspecao = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-REINSP",
            tipo_falha="reinspecao",
            usuario="agente",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
        )
        ReinspecaoFilaHistorico.objects.create(
            falha=self.reinspecao,
            actor=self.finalizador,
            auditor=self.outro,
            tipo=ReinspecaoFilaHistorico.TIPO_CONCLUSAO,
        )

        now = timezone.now()
        self.direto = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-DIRETO",
            tipo_falha="Colaborador",
            usuario="agente",
            created_by=self.finalizador,
            data_resposta=now,
            analise_concluida_em=now,
        )
        self.legado_exato = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-LEGADO",
            tipo_falha="Colaborador",
            usuario="agente",
            auditor=self.legado_user.username,
        )

        agent = Agent.objects.create(full_name="Outro", user_lan_id=self.finalizador.username)
        UserProfile.objects.create(user=self.outro, agent=agent)
        self.ambiguo = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-AMBIGUO",
            tipo_falha="Colaborador",
            usuario="agente",
            auditor=self.finalizador.username,
        )

        self.importado = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-IMPORT",
            tipo_falha="Colaborador",
            usuario="agente",
            created_by=self.tecnico,
            data_resposta=now,
            analise_concluida_em=now,
            brflow_parsed={
                "homolog_import_source": "fonte.xlsx",
                "homolog_import_row_id": "1",
            },
        )
        self.historico_sem_evidencia = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-SEM-EVIDENCIA",
            tipo_falha="Colaborador",
            usuario="agente",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            created_by=self.outro,
        )
        self.preenchido = AuditoriaFalhaCadastro.objects.create(
            protocolo="BF-PREENCHIDO",
            tipo_falha="Colaborador",
            usuario="agente",
            auditor_responsavel=self.outro,
        )

    @staticmethod
    def _run(**options):
        stdout = StringIO()
        call_command("backfill_auditor_responsavel", stdout=stdout, **options)
        return json.loads(stdout.getvalue())

    def test_dry_run_apply_idempotencia_e_ambiguos(self):
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "auditores.csv"
            dry = self._run(dry_run=True, batch_size=2, report_csv=str(report))
            self.assertEqual(dry["mode"], "dry-run")
            self.assertGreaterEqual(dry["resolved"], 4)
            self.assertGreaterEqual(dry["ambiguous"], 1)
            self.assertGreaterEqual(dry["unresolved"], 1)
            self.assertGreaterEqual(dry["ignored"], 1)
            self.assertEqual(dry["applied"], 0)
            with report.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), AuditoriaFalhaCadastro.objects.count())

        self.assertFalse(
            AuditoriaFalhaCadastro.objects.exclude(pk=self.preenchido.pk)
            .filter(auditor_responsavel__isnull=False)
            .exists()
        )

        applied = self._run(apply=True, batch_size=3)
        self.assertGreaterEqual(applied["applied"], 4)
        for record in (self.contestacao, self.reinspecao, self.direto):
            record.refresh_from_db()
            self.assertEqual(record.auditor_responsavel_id, self.finalizador.id)

        self.legado_exato.refresh_from_db()
        self.assertEqual(self.legado_exato.auditor_responsavel_id, self.legado_user.id)
        # Este identificador exato é ambíguo entre username e matrícula.
        self.ambiguo.refresh_from_db()
        self.assertIsNone(self.ambiguo.auditor_responsavel_id)
        self.importado.refresh_from_db()
        self.assertIsNone(self.importado.auditor_responsavel_id)
        self.historico_sem_evidencia.refresh_from_db()
        self.assertIsNone(self.historico_sem_evidencia.auditor_responsavel_id)
        self.preenchido.refresh_from_db()
        self.assertEqual(self.preenchido.auditor_responsavel_id, self.outro.id)

        again = self._run(apply=True)
        self.assertEqual(again["applied"], 0)


class AuditorResponsavelIndicatorTests(TestCase):
    def _contestacao(self, *, status, auditor=None, suffix="1"):
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo=f"IND-{suffix}",
            tipo_falha="Colaborador",
            usuario="agente",
            auditor_responsavel=auditor,
        )
        return ContestacaoOperacional.objects.create(
            falha=falha,
            dominio=ContestacaoOperacional.DOMINIO_FRAUD,
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            status=status,
            justificativa_lider="teste",
            protocolo=falha.protocolo,
            agente_usuario=falha.usuario,
            atribuida_em=timezone.now(),
        )

    def test_procedentes_sao_agrupadas_por_id_do_auditor_original(self):
        auditor = User.objects.create_user(
            "auditor.indicador", email="auditor.indicador@test.local", password="x"
        )
        agent = Agent.objects.create(full_name="Nome do Auditor", user_lan_id="auditor.indicador")
        UserProfile.objects.create(user=auditor, agent=agent)
        self._contestacao(
            status=ContestacaoOperacional.STATUS_PROCEDENTE,
            auditor=auditor,
            suffix="A",
        )
        self._contestacao(
            status=ContestacaoOperacional.STATUS_PROCEDENTE,
            auditor=auditor,
            suffix="B",
        )
        self._contestacao(
            status=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            auditor=auditor,
            suffix="C",
        )
        self._contestacao(
            status=ContestacaoOperacional.STATUS_PROCEDENTE,
            auditor=None,
            suffix="D",
        )

        rows = build_erros_procedentes_por_auditor()

        identified = next(row for row in rows if row["auditor_responsavel_id"])
        unidentified = next(row for row in rows if row["auditor_responsavel_id"] is None)
        self.assertEqual(identified["auditor_responsavel_id"], str(auditor.id))
        self.assertEqual(identified["auditor"], "Nome do Auditor")
        self.assertEqual(identified["erros_contestacao_procedente"], 2)
        self.assertEqual(unidentified["auditor"], "Auditor não identificado")
        self.assertEqual(unidentified["erros_contestacao_procedente"], 1)
