# -*- coding: utf-8 -*-
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from app.services.robot_manager import (
    CASE_FILA_SAVED_PREFIX,
    PRODUTIVIDADE_CASE_SAVED_PREFIX,
    RobotProcessManager,
)


class RobotManagerProdutividadeCaseSavedTests(SimpleTestCase):
    def test_append_log_invokes_callback(self):
        manager = RobotProcessManager()
        received: list[tuple[str, str]] = []
        manager.register_produtividade_case_saved_callback(
            lambda rt, path: received.append((rt, path))
        )

        file_path = r"C:\tmp\mai-2026\Relatorio_Produtividade_Consolidado_01-15mai.xlsx"
        manager._append_log(
            "produtividade_case",
            f"{PRODUTIVIDADE_CASE_SAVED_PREFIX}consolidado|{file_path}",
        )

        self.assertEqual(received, [("consolidado", file_path)])

    def test_case_fila_saved_invokes_callback(self):
        manager = RobotProcessManager()
        received: list[tuple[str, str]] = []
        manager.register_produtividade_case_saved_callback(
            lambda rt, path: received.append((rt, path))
        )
        file_path = r"C:\tmp\fila_aberta_2026-07-17_15-00.json"
        manager._append_log(
            "produtividade_case",
            f"{CASE_FILA_SAVED_PREFIX}{file_path}",
        )
        self.assertEqual(received, [("fila_aberta", file_path)])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_hook_enqueues(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.produtividade_case_hooks import on_produtividade_case_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 11
        mock_enqueue.return_value = (job, True)

        file_path = r"C:\tmp\mai-2026\file.xlsx"
        on_produtividade_case_saved("prod_hora", file_path)

        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE_CASE,
            source_path=file_path,
            report_type="prod_hora",
            force=True,
            spawn=True,
        )
        self.assertIn("Job #11", mock_rm._append_log.call_args[0][1])
