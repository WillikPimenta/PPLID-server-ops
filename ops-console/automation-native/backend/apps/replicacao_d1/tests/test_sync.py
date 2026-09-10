# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import SimpleTestCase, TestCase, override_settings

from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.services.excel_reader import read_replicacao_d1_excel
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db
from apps.replicacao_d1.services.sync_runner import run_sync_with_audit


def _write_fixture_excel(path: Path, *, run_id: str = "20260717_093000") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df_resumo = pd.DataFrame(
        [
            {
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Workflow BRFlow": "G Auditoria Origem",
                "Canal Destino": "BRFlow",
                "Cliente": "CLARO",
                "Segmento": "SEG",
                "Categoria": "CAT",
                "Fila": "G auditoria",
                "Amostra Diaria": 10,
                "Amostra Solicitada": 10,
                "Amostra Efetiva": 2,
                "Protocolos Salvos": 2,
                "Pct Atingido": 100.0,
                "Status": "OK",
                "Status BRFlow": "SALVO_OK",
                "Data Hora Upload BRFlow": "17/07/2026 12:05",
                "Disponivel D1": 50,
                "Faixa Horaria": "08-12",
                "RunId": run_id,
                "Data Referencia D1": "16/07/2026",
                "Data Execucao": "17/07/2026 12:00",
                "Parquet Referencia": "brflow-detalhado-tratado_20260716.parquet",
                "Auditores Ativos": 12,
            },
            {
                "Workflow": "WF Case",
                "Workflow D1": "wf_case_d1",
                "Workflow BRFlow": "Doc 3.1 Origem",
                "Canal Destino": "Case Manager",
                "Cliente": "CLARO",
                "Segmento": "SEG",
                "Categoria": "CAT",
                "Fila": "3.1",
                "Amostra Diaria": 5,
                "Amostra Solicitada": 5,
                "Amostra Efetiva": 1,
                "Protocolos Salvos": 1,
                "Pct Atingido": 100.0,
                "Status": "OK",
                "Status BRFlow": "ERRO",
                "Data Hora Upload BRFlow": "",
                "Disponivel D1": 20,
                "Faixa Horaria": "09-11",
                "RunId": run_id,
                "Data Referencia D1": "16/07/2026",
                "Data Execucao": "17/07/2026 12:00",
                "Parquet Referencia": "brflow-detalhado-tratado_20260716.parquet",
                "Auditores Ativos": 12,
            },
        ]
    )
    df_plano = pd.DataFrame(
        [
            {
                "Protocolo": "P001",
                "Workflow": "wf_g_d1",
                "Data de Análise": datetime(2026, 7, 16, 10, 30),
                "Hora": 10,
                "WorkflowConfig": "WF G",
                "Canal Destino": "BRFlow",
            },
            {
                "Protocolo": "P002",
                "Workflow": "wf_case_d1",
                "Data de Análise": datetime(2026, 7, 16, 11, 0),
                "Hora": 11,
                "WorkflowConfig": "WF Case",
                "Canal Destino": "Case Manager",
            },
        ]
    )
    df_dashboard = pd.DataFrame(
        [
            {"Secao": "Execucao", "Metrica": "RunId", "Valor": run_id},
            {"Secao": "Execucao", "Metrica": "DataReferenciaD1", "Valor": "16/07/2026"},
            {"Secao": "Capacidade", "Metrica": "AuditoresAtivos", "Valor": 12},
            {"Secao": "Capacidade", "Metrica": "AuditoresAtivosCase", "Valor": 3},
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_plano.to_excel(writer, sheet_name="Plano", index=False)
        df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
        df_dashboard.to_excel(writer, sheet_name="Dashboard", index=False)
    return path


class ExcelReaderTests(TestCase):
    def test_read_replicacao_d1_excel(self):
        path = _write_fixture_excel(Path(self._test_dir()) / "replicacao_aud_d1_relatorio_20260717_093000.xlsx")
        parsed = read_replicacao_d1_excel(path)
        self.assertEqual(parsed.run_id, "20260717_093000")
        self.assertEqual(parsed.data_referencia_d1, date(2026, 7, 16))
        self.assertEqual(len(parsed.workflows), 2)
        self.assertEqual(len(parsed.protocolos), 2)
        self.assertEqual(parsed.read_stats.valid_rows, 2)
        self.assertTrue(parsed.read_stats.accounting_ok)
        self.assertEqual(parsed.auditores_ativos_brflow, 12)
        self.assertEqual(parsed.auditores_ativos_case, 3)
        status_map = {w.workflow_config: w.status_brflow for w in parsed.workflows}
        self.assertEqual(status_map["WF G"], "SALVO_OK")
        self.assertEqual(status_map["WF Case"], "ERRO")

    def _test_dir(self) -> str:
        from django.conf import settings

        return str(Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_test")


class SyncIdempotentTests(TestCase):
    def setUp(self):
        self.dir = Path(self._test_dir())
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = _write_fixture_excel(self.dir / "replicacao_aud_d1_relatorio_20260717_093000.xlsx")

    def _test_dir(self) -> str:
        from django.conf import settings

        return str(Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_sync_test")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_sync_and_resync_same_run_id(self):
        ok, source, count, run_id, _ = sync_replicacao_d1_to_db(path=str(self.path), force=True)
        self.assertTrue(ok)
        self.assertEqual(run_id, "20260717_093000")
        self.assertEqual(ReplicacaoD1Run.objects.count(), 1)
        self.assertEqual(ReplicacaoD1WorkflowDia.objects.count(), 2)
        self.assertEqual(ReplicacaoD1Protocolo.objects.count(), 2)
        self.assertGreater(count, 0)

        # Altera status e regrava — deve substituir, não duplicar.
        df_resumo = pd.read_excel(self.path, sheet_name="Resumo", engine="openpyxl")
        df_resumo.loc[df_resumo["Workflow"] == "WF Case", "Status BRFlow"] = "SALVO_OK"
        df_plano = pd.read_excel(self.path, sheet_name="Plano", engine="openpyxl")
        df_dash = pd.read_excel(self.path, sheet_name="Dashboard", engine="openpyxl")
        with pd.ExcelWriter(self.path, engine="openpyxl") as writer:
            df_plano.to_excel(writer, sheet_name="Plano", index=False)
            df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
            df_dash.to_excel(writer, sheet_name="Dashboard", index=False)

        ok2, _, _, _, _ = sync_replicacao_d1_to_db(path=str(self.path), force=True)
        self.assertTrue(ok2)
        self.assertEqual(ReplicacaoD1Run.objects.count(), 1)
        self.assertEqual(ReplicacaoD1WorkflowDia.objects.count(), 2)
        self.assertEqual(ReplicacaoD1Protocolo.objects.count(), 2)
        wf_case = ReplicacaoD1WorkflowDia.objects.get(run_id=run_id, workflow_config="WF Case")
        self.assertEqual(wf_case.status_brflow, "SALVO_OK")
        run = ReplicacaoD1Run.objects.get(run_id=run_id)
        self.assertEqual(run.workflows_salvo_ok, 2)

    def test_run_sync_with_audit(self):
        from contextlib import contextmanager

        @contextmanager
        def _slot(_label):
            yield

        with patch(
            "apps.common.bot_db_sync_gate.bot_db_sync_slot",
            side_effect=lambda label, lane=None: _slot(label),
        ):
            success, log, skipped = run_sync_with_audit(
                path=str(self.path),
                force=True,
            )
        self.assertTrue(success)
        self.assertFalse(skipped)
        self.assertEqual(log.run_id, "20260717_093000")


class ReplicacaoD1HookTests(SimpleTestCase):
    def test_append_log_invokes_replicacao_d1_callback(self):
        from app.services.robot_manager import REPLICACAO_D1_SAVED_PREFIX, RobotProcessManager

        manager = RobotProcessManager()
        received: list[tuple[str, str]] = []
        manager.register_replicacao_d1_saved_callback(
            lambda run_id, path: received.append((run_id, path))
        )
        file_path = r"C:\tmp\replicacao_aud_d1_relatorio_20260717.xlsx"
        manager._append_log(
            "replicacao_auditoria_d1",
            f"{REPLICACAO_D1_SAVED_PREFIX}20260717|{file_path}",
        )
        self.assertEqual(received, [("20260717", file_path)])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_replicacao_d1_saved_enqueues(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.replicacao_d1_hooks import on_replicacao_d1_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 9
        mock_enqueue.return_value = (job, True)

        on_replicacao_d1_saved("20260717", r"C:\tmp\rel.xlsx")
        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
            source_path=r"C:\tmp\rel.xlsx",
            report_type="20260717",
            force=True,
            spawn=True,
        )
        self.assertIn("Job #9", mock_rm._append_log.call_args[0][1])
