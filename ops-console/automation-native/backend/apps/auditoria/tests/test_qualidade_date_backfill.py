from __future__ import annotations

import importlib
import json
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.db import migrations
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.auditoria.models import AuditoriaFalhaCadastro, QualidadeAnaliseOrigem


class ExplicitDateMigrationContractTests(SimpleTestCase):
    def test_migrations_sao_schema_only_e_reversiveis(self):
        modules = (
            "apps.auditoria.migrations.0051_explicit_quality_dates",
            "apps.qualidade_operacional.migrations.0017_explicit_quality_dates",
        )
        for module_name in modules:
            operations = importlib.import_module(module_name).Migration.operations
            self.assertTrue(operations)
            self.assertTrue(all(isinstance(operation, migrations.AddField) for operation in operations))
            self.assertFalse(any(isinstance(operation, migrations.RunPython) for operation in operations))


class QualidadeDateBackfillCommandTests(TestCase):
    def setUp(self):
        self.origin = QualidadeAnaliseOrigem.objects.create(
            protocolo="BACKFILL-1",
            brflow_raw="raw não alterável",
            brflow_parsed={
                "data_criacao": "01/08/2026 08:00",
                "data_analise": "02/08/2026 09:00",
                "data_conclusao": "03/08/2026 10:00",
            },
            conteudo_hash="a" * 64,
        )
        concluded = timezone.make_aware(datetime(2026, 8, 7, 12, 0))
        received = timezone.make_aware(datetime(2026, 8, 5, 12, 0))
        self.treated = AuditoriaFalhaCadastro.objects.create(
            protocolo="BACKFILL-1",
            analise_origem=self.origin,
            tipo_falha="Sem Falha",
            usuario="agente",
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=concluded,
            data_contestacao=received,
        )
        self.pending = AuditoriaFalhaCadastro.objects.create(
            protocolo="BACKFILL-PENDENTE",
            tipo_falha="Sem Falha",
            usuario="agente",
            analise_status=AuditoriaFalhaCadastro.ANALISE_EM_ANALISE,
            data_contestacao=received,
        )

    def test_dry_run_nao_altera_registros(self):
        stdout = StringIO()
        call_command("backfill_qualidade_datas", stdout=stdout)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["mode"], "dry-run")
        self.origin.refresh_from_db()
        self.treated.refresh_from_db()
        self.assertIsNone(self.origin.protocolo_concluido_em)
        self.assertIsNone(self.treated.data_analise_intranet)

    def test_apply_backup_rollback_e_exclusao_de_pendente(self):
        with TemporaryDirectory() as temp_dir:
            backup = Path(temp_dir) / "qualidade-datas.jsonl"
            call_command(
                "backfill_qualidade_datas",
                apply=True,
                backup_path=str(backup),
                stdout=StringIO(),
            )
            self.origin.refresh_from_db()
            self.treated.refresh_from_db()
            self.pending.refresh_from_db()
            self.assertEqual(self.origin.protocolo_concluido_em.date().isoformat(), "2026-08-03")
            self.assertEqual(self.treated.data_analise_intranet.date().isoformat(), "2026-08-07")
            self.assertIsNone(self.pending.data_analise_intranet)
            self.assertEqual(self.origin.brflow_raw, "raw não alterável")
            self.assertTrue(backup.is_file())

            call_command(
                "backfill_qualidade_datas",
                rollback_from=str(backup),
                stdout=StringIO(),
            )
            self.origin.refresh_from_db()
            self.treated.refresh_from_db()
            self.assertIsNone(self.origin.protocolo_concluido_em)
            self.assertIsNone(self.treated.data_analise_intranet)
