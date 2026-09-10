# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from app.services.robot_manager import PRODUCTION_DETALHADO_SAVED_PREFIX, RobotProcessManager


class RobotManagerProductionSavedTests(SimpleTestCase):
    def test_append_log_invokes_production_saved_callback(self):
        manager = RobotProcessManager()
        received: list[str] = []
        manager.register_production_saved_callback(received.append)

        file_path = r"C:\tmp\relatorio_produtividade_detalhado_2026-06-18.xlsx"
        manager._append_log("production", f"{PRODUCTION_DETALHADO_SAVED_PREFIX}{file_path}")

        self.assertEqual(received, [file_path])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_production_detalhado_saved_enqueues(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.productivity_hooks import on_production_detalhado_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 7
        mock_enqueue.return_value = (job, True)

        file_path = r"C:\tmp\relatorio_produtividade_detalhado_2026-06-18.xlsx"
        on_production_detalhado_saved(file_path)

        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=file_path,
            force=True,
            spawn=True,
        )
        mock_rm._append_log.assert_called_once()
        self.assertIn("Job #7", mock_rm._append_log.call_args[0][1])
