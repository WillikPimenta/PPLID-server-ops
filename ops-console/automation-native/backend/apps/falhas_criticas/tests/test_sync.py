import pandas as pd
from django.test import TestCase, override_settings
from unittest.mock import patch

from apps.falhas_criticas.models import FalhasAgent, SyncAuditLog
from apps.falhas_criticas.services.excel_path import get_excel_source_path, require_excel_source_path
from apps.falhas_criticas.services.sync_helpers import (
    looks_like_matricula,
    resolve_agent_from_suporte_row,
)
from apps.falhas_criticas.services.sync_runner import run_sync_with_audit


class ExcelPathTests(TestCase):
    @override_settings(EXCEL_SOURCE_PATH=r"C:\env\planilha.xlsx")
    def test_excel_source_path_env_priority(self):
        self.assertEqual(get_excel_source_path(), r"C:\env\planilha.xlsx")

    def test_excel_source_path_override(self):
        self.assertEqual(get_excel_source_path(r"C:\override.xlsx"), r"C:\override.xlsx")

    def test_require_excel_source_path_empty_raises_friendly_message(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            require_excel_source_path("")
        self.assertIn("Importar Falhas", str(ctx.exception))


class AgentMatriculaSyncTests(TestCase):
    def test_looks_like_matricula(self):
        self.assertTrue(looks_like_matricula("c92935a"))
        self.assertFalse(looks_like_matricula("yasmimgracianodossantos"))

    def test_resolve_suporte_matricula_direct(self):
        agent = FalhasAgent(matricula_norm="c92935a", name="Test User")
        db = {"c92935a": agent}
        row = pd.Series({"Agente": "c92935a"})
        agent_obj, _, mat = resolve_agent_from_suporte_row(row, {}, db)
        self.assertEqual(mat, "c92935a")
        self.assertIs(agent_obj, agent)

    def test_sync_removes_invalid_agents(self):
        from apps.falhas_criticas.services.excel_sync import sync_excel_to_db

        FalhasAgent.objects.create(matricula_norm="yasmimgracianodossantos", name="")
        FalhasAgent.objects.create(matricula_norm="999", name="Old Name")

        df_base = pd.DataFrame(
            [
                {
                    "Matrícula Agente": "999",
                    "Data de Análise": pd.Timestamp("2026-05-01"),
                    "Localidade": "Brasília",
                    "Protocolo": "P1",
                    "Novo cenário": "Cenário teste",
                    "Módulo": "Demais falhas",
                }
            ]
        )
        dfh = pd.DataFrame(
            [
                {
                    "matricula_agente": "999",
                    "nome_agente": "Valid Agent",
                    "data_inicial": pd.Timestamp("2026-01-01"),
                }
            ]
        )
        df_suporte = pd.DataFrame(
            [{"Agente": "Yasmim Graciano dos Santos", "Data": pd.Timestamp("2026-05-01")}]
        )

        with (
            patch(
                "apps.falhas_criticas.services.excel_path.get_excel_source_path",
                return_value=r"C:\fake.xlsx",
            ),
            patch("apps.falhas_criticas.services.excel_sync.Path") as mock_path_cls,
            patch("apps.falhas_criticas.services.excel_sync.read_base", return_value=df_base),
            patch("apps.falhas_criticas.services.excel_sync.read_hc", return_value=dfh),
            patch(
                "apps.falhas_criticas.services.excel_sync.read_suporte",
                return_value=df_suporte,
            ),
            patch(
                "apps.falhas_criticas.services.excel_sync.read_treinamentos",
                return_value=pd.DataFrame(),
            ),
            patch(
                "apps.falhas_criticas.services.excel_sync._read_contestacoes",
                return_value=(pd.DataFrame(), pd.DataFrame()),
            ),
        ):
            mock_path_cls.return_value.exists.return_value = True
            ok = sync_excel_to_db(path=r"C:\fake.xlsx")

        self.assertTrue(ok[0] if isinstance(ok, tuple) else ok)
        self.assertIsInstance(ok[1] if isinstance(ok, tuple) else {}, dict)
        stats = ok[1] if isinstance(ok, tuple) else {}
        self.assertIn("sheets", stats)
        self.assertIn("totals", stats)
        self.assertFalse(
            FalhasAgent.objects.filter(matricula_norm="yasmimgracianodossantos").exists()
        )
        valid = FalhasAgent.objects.get(matricula_norm="999")
        self.assertEqual(valid.name, "Valid Agent")


class SyncAuditTests(TestCase):
    def test_sync_audit_log_system_trigger_fields(self):
        log = SyncAuditLog.objects.create(
            path=r"C:\test.xlsx",
            trigger_source=SyncAuditLog.TRIGGER_SYSTEM,
            success=True,
            message="OK",
            duration_seconds=12.5,
        )
        self.assertEqual(log.trigger_source, "system")

    def test_run_sync_with_audit_records_failure(self):
        with (
            patch(
                "apps.falhas_criticas.services.sync_runner.require_excel_source_path",
                return_value=r"C:\fake.xlsx",
            ),
            patch(
                "apps.falhas_criticas.services.sync_runner.sync_excel_to_db",
                side_effect=PermissionError("Excel aberto"),
            ),
        ):
            success, log = run_sync_with_audit(trigger_source=SyncAuditLog.TRIGGER_SYSTEM)
        self.assertFalse(success)
        self.assertIn("Excel aberto", log.message)
        self.assertEqual(log.stats, {})

    def test_run_sync_with_audit_persists_stats(self):
        fake_stats = {
            "sheets": {"base": {"rows_read": 10, "rows_valid": 9, "rows_persisted": 9, "warnings": []}},
            "totals": {"rows_read": 10, "rows_valid": 9, "rows_removed": 1, "rows_persisted": 9},
        }
        with (
            patch(
                "apps.falhas_criticas.services.sync_runner.require_excel_source_path",
                return_value=r"C:\fake.xlsx",
            ),
            patch(
                "apps.falhas_criticas.services.sync_runner.sync_excel_to_db",
                return_value=(True, fake_stats),
            ),
        ):
            success, log = run_sync_with_audit(trigger_source=SyncAuditLog.TRIGGER_USER)
        self.assertTrue(success)
        self.assertEqual(log.stats, fake_stats)
