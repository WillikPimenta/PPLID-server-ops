from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from apps.auditoria.management.commands.import_auditoria_falha_xlsx import (
    _PROFILE_CLARO_CONFER,
    _PROFILE_EXPORT,
    Command,
)
from apps.auditoria.models import AuditoriaFalhaCadastro


HEADERS = [
    "Origem",
    "Cliente",
    "Protocolo",
    "M\ufffddulo / Tipo de Servi\ufffdo",
    "Status",
    "Observa\ufffd\ufffdo",
    "Irregularidades Apontadas",
    "Status da Irregularidade",
    "Matr\ufffdcula do Inspetor",
    "Nome do Inspetor",
    "Data de Inspe\ufffd\ufffdo",
    "Matr\ufffdcula do Auditor",
    "Nome do Auditor",
    "Data da Auditoria",
    "Data de Contesta\ufffd\ufffdo",
    "Data de Resposta",
    "Arquivo de Origem",
]


class FakeResolver:
    mapping_version = "test-v1"
    source_hash = "a" * 64

    def resolve(self, description):
        return SimpleNamespace(
            scenario="Cenário mapeado",
            stage="Etapa mapeada",
            scenario_status="matched",
            stage_status="matched",
            mapping_version=self.mapping_version,
            source_hash=self.source_hash,
            extracted=SimpleNamespace(code="IC - 999"),
        )


def _build_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Consolidado"
    sheet.append(HEADERS)
    sheet.append(
        [
            "Auditoria",
            "Claro",
            1001,
            "Reclassificação",
            "Conforme",
            None,
            None,
            None,
            "C13796Q",
            "Agente 1",
            datetime(2026, 8, 20),
            "C91895A",
            "Auditor 1",
            datetime(2026, 8, 20),
            None,
            datetime(2026, 8, 20, 18),
            "auditoria.xlsx",
        ]
    )
    sheet.append(
        [
            "Contesta\ufffd\ufffdo",
            "Claro",
            1002,
            "Contesta\ufffd\ufffdo",
            "Procedente",
            None,
            "IC - 999 - Teste",
            None,
            "C13796Q",
            "Agente 1",
            None,
            "C13654Q",
            "Auditor 2",
            None,
            datetime(2026, 8, 19, 10),
            datetime(2026, 8, 20, 10),
            "contestacao.xlsx",
        ]
    )
    sheet.append(
        [
            "Contesta\ufffd\ufffdo",
            "Claro",
            1003,
            "Contesta\ufffd\ufffdo",
            "N\ufffdo conforme",
            None,
            "IC - 999 - Teste",
            None,
            "SISTEMA",
            None,
            None,
            "C13654Q",
            "Auditor 2",
            None,
            datetime(2026, 8, 19, 11),
            datetime(2026, 8, 20, 11),
            "contestacao.xlsx",
        ]
    )
    workbook.save(path)


class ClaroConferConsolidatedImportTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "consolidado.xlsx"
        _build_workbook(self.path)

    @patch(
        "apps.auditoria.services.reinspecao_mapping."
        "ReinspecaoMappingResolver.from_active_mapping",
        return_value=FakeResolver(),
    )
    def test_load_rows_transforms_to_existing_auditoria_and_reinspecao_contract(self, _resolver):
        rows, summary = Command()._load_rows(
            self.path,
            profile=_PROFILE_CLARO_CONFER,
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["auditados"], 3)
        self.assertEqual(summary["falhas"], 1)
        self.assertEqual(summary["auditoria"], 1)
        self.assertEqual(summary["reinspecao"], 2)
        self.assertEqual(summary["id_cliente"], 83)
        self.assertEqual(summary["id_workflow"], 450)

        auditoria, reinspecao_ok, reinspecao_falha = rows
        self.assertEqual(auditoria["origem"], "auditoria")
        self.assertEqual(auditoria["tipo_registro"], "reinspecao")
        self.assertEqual(auditoria["tipo_falha"], "auditoria")
        self.assertEqual(auditoria["modulo"], "Auditoria")
        self.assertEqual(auditoria["status"], "Conforme")
        self.assertEqual(auditoria["etapa_falha"], "Reclassificação")
        self.assertEqual(auditoria["brflow_parsed"]["fila_contexto"], "auditoria_compliance")

        self.assertEqual(reinspecao_ok["origem"], "reinspecao")
        self.assertEqual(reinspecao_ok["status"], "Procedente")
        self.assertEqual(reinspecao_ok["motivo_falha"], "Cenário mapeado")
        self.assertEqual(reinspecao_ok["etapa_falha"], "Etapa mapeada")
        self.assertEqual(reinspecao_falha["status"], "Improcedente")

    @patch(
        "apps.auditoria.services.reinspecao_mapping."
        "ReinspecaoMappingResolver.from_active_mapping",
        return_value=FakeResolver(),
    )
    def test_dry_run_reports_expected_facts_and_writes_nothing(self, _resolver):
        output = StringIO()

        call_command(
            "import_auditoria_falha_xlsx",
            str(self.path),
            profile=_PROFILE_CLARO_CONFER,
            dry_run=True,
            stdout=output,
        )

        rendered = output.getvalue()
        self.assertIn("rows=3 created=3 updated=0 skipped=0", rendered)
        self.assertIn("auditados=3 falhas=1", rendered)
        self.assertIn("auditoria=1 reinspecao=2", rendered)
        self.assertIn("cliente_id=83 workflow_id=450", rendered)
        self.assertEqual(AuditoriaFalhaCadastro.objects.count(), 0)

    def test_legacy_export_profile_keeps_original_column_contract(self):
        legacy_path = Path(self.temp_dir.name) / "export.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["protocolo", "tipo_registro", "tipo_falha", "origem"])
        sheet.append(["LEG-1", "auditoria", "operacional", "auditoria"])
        workbook.save(legacy_path)

        rows, summary = Command()._load_rows(
            legacy_path,
            profile=_PROFILE_EXPORT,
        )

        self.assertEqual(summary, {"profile": _PROFILE_EXPORT})
        self.assertEqual(
            rows,
            [
                {
                    "protocolo": "LEG-1",
                    "tipo_registro": "auditoria",
                    "tipo_falha": "operacional",
                    "origem": "auditoria",
                }
            ],
        )
