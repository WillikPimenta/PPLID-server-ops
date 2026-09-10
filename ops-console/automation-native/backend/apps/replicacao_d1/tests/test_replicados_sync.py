# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.replicacao_d1.constants import REPORT_TYPE_REPLICADOS
from apps.replicacao_d1.models import ReplicacaoD1Replicado
from apps.replicacao_d1.services.replicados_reader import read_replicados_csv
from apps.replicacao_d1.services.sync_replicados import sync_replicados_to_db
from apps.replicacao_d1.services.sync_runner import run_sync_with_audit


def _write_replicados_csv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Cliente Destino;Data de Cadastro Destino;Protocolo Destino;Workflow Destino;"
        "Protocolo Origem;Cliente Origem;Workflow Origem;Nível Hierárquico Origem;"
        "Data de Cadastro Origem;Tipo de Conclusão de Análise Origem\n"
        "GAQ;16/07/2026 10:00;D001;G Auditoria Destino;P001;CLARO;WF Origem;1;"
        "15/07/2026 09:00;OK\n"
        "GAQ;16/07/2026 11:00;D002;G Auditoria Destino;P002;CLARO;WF Origem 2;1;"
        "15/07/2026 10:00;OK\n",
        encoding="utf-8-sig",
    )
    return path


class ReplicadosSyncTests(TestCase):
    def setUp(self):
        from django.conf import settings

        self.dir = Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_replicados_test"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = _write_replicados_csv(self.dir / "brflow-replicadosd1-tratado_20260716.csv")

    def test_read_csv(self):
        report_date, result = read_replicados_csv(self.path)
        self.assertEqual(report_date, date(2026, 7, 16))
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.valid_rows, 2)
        self.assertTrue(result.accounting_ok)
        self.assertEqual(result.records[0].protocolo_origem, "P001")
        self.assertEqual(result.records[0].workflow_origem, "WF Origem")
        self.assertTrue(timezone.is_aware(result.records[0].data_cadastro_destino))

    @override_settings(REPLICACAO_D1_FF_NEW_RECONCILIATION=False)
    def test_sync_reports_progress_stages(self):
        progress: list[str] = []

        ok, _, count, _ = sync_replicados_to_db(
            path=str(self.path),
            force=True,
            progress=progress.append,
        )

        self.assertTrue(ok)
        self.assertEqual(count, 2)
        self.assertTrue(any("Lendo" in message for message in progress))
        self.assertTrue(any("Carregando 2" in message for message in progress))

    @override_settings(REPLICACAO_D1_REPLICADOS_DIR="")
    def test_sync_idempotent_by_report_date(self):
        ok, source, count, _ = sync_replicados_to_db(path=str(self.path), force=True)
        self.assertTrue(ok)
        self.assertEqual(source.report_date, date(2026, 7, 16))
        self.assertEqual(count, 2)
        self.assertEqual(ReplicacaoD1Replicado.objects.count(), 2)

        ok2, _, count2, _ = sync_replicados_to_db(path=str(self.path), force=True)
        self.assertTrue(ok2)
        self.assertEqual(count2, 2)
        self.assertEqual(ReplicacaoD1Replicado.objects.count(), 2)

    def test_run_sync_with_audit_replicados(self):
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
                report_type=REPORT_TYPE_REPLICADOS,
            )
        self.assertTrue(success)
        self.assertFalse(skipped)
        self.assertEqual(log.kind, "replicados")
        self.assertEqual(log.report_date, date(2026, 7, 16))
        self.assertEqual(ReplicacaoD1Replicado.objects.count(), 2)


class ReplicadosHookTests(SimpleTestCase):
    def test_append_log_invokes_replicados_callback(self):
        from app.services.robot_manager import (
            REPLICACAO_D1_REPLICADOS_SAVED_PREFIX,
            RobotProcessManager,
        )

        manager = RobotProcessManager()
        received: list[str] = []
        manager.register_replicacao_d1_replicados_saved_callback(received.append)
        file_path = r"C:\tmp\brflow-replicadosd1-tratado_20260716.csv"
        manager._append_log("rotina", f"{REPLICACAO_D1_REPLICADOS_SAVED_PREFIX}{file_path}")
        self.assertEqual(received, [file_path])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_replicados_saved_enqueues(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.replicacao_d1_hooks import on_replicacao_d1_replicados_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 11
        mock_enqueue.return_value = (job, True)

        on_replicacao_d1_replicados_saved(r"C:\tmp\brflow-replicadosd1-tratado_20260716.csv")
        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
            source_path=r"C:\tmp\brflow-replicadosd1-tratado_20260716.csv",
            report_type=REPORT_TYPE_REPLICADOS,
            force=True,
            spawn=True,
        )
        self.assertIn("Job #11", mock_rm._append_log.call_args[0][1])
