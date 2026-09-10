# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from app.services.robot_manager import (
    PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX,
    RobotProcessManager,
)


class RobotManagerProductionGedIrregularidadeSavedTests(SimpleTestCase):
    def test_append_log_invokes_ged_irregularidade_saved_callback(self):
        manager = RobotProcessManager()
        received: list[str] = []
        manager.register_production_ged_irregularidade_saved_callback(received.append)

        file_path = r"C:\tmp\ged-irregularidade-bruto_202606_1.csv"
        manager._append_log(
            "production",
            f"{PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX}{file_path}",
        )

        self.assertEqual(received, [file_path])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_production_ged_irregularidade_saved_enqueues(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.reinspecao_ged_hooks import on_production_ged_irregularidade_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 12
        mock_enqueue.return_value = (job, True)

        file_path = r"C:\tmp\ged-irregularidade-bruto_202606_1.csv"
        on_production_ged_irregularidade_saved(file_path)

        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_REINSPECAO_GED,
            source_path=file_path,
            force=True,
            spawn=True,
        )
        mock_rm._append_log.assert_called_once()
        self.assertIn("Job #12", mock_rm._append_log.call_args[0][1])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync", side_effect=RuntimeError("fila offline"))
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_production_ged_irregularidade_saved_propagates_enqueue_failure(
        self,
        mock_get_rm,
        mock_enqueue,
    ):
        from apps.automacoes.reinspecao_ged_hooks import on_production_ged_irregularidade_saved

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm

        with self.assertRaisesRegex(RuntimeError, "fila offline"):
            on_production_ged_irregularidade_saved(r"C:\tmp\ged.csv")

        mock_enqueue.assert_called_once()
        self.assertIn("ERRO ao enfileirar", mock_rm._append_log.call_args[0][1])
