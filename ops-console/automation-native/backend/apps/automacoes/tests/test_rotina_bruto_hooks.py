# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from app.services.robot_manager import ROTINA_BRUTO_SAVED_PREFIX, RobotProcessManager


class RobotManagerRotinaBrutoSavedTests(SimpleTestCase):
    def test_append_log_invokes_rotina_bruto_saved_callback(self):
        manager = RobotProcessManager()
        received: list[tuple[str, str]] = []
        manager.register_rotina_bruto_saved_callback(
            lambda report_type, file_path: received.append((report_type, file_path))
        )

        file_path = r"C:\tmp\brflow-detalhado-bruto_2026-06-18.parquet"
        manager._append_log(
            "rotina",
            f"{ROTINA_BRUTO_SAVED_PREFIX}detalhado|{file_path}",
        )

        self.assertEqual(received, [("detalhado", file_path)])

    def test_append_log_invokes_g_auditoria_callback(self):
        manager = RobotProcessManager()
        received: list[tuple[str, str]] = []
        manager.register_rotina_bruto_saved_callback(
            lambda report_type, file_path: received.append((report_type, file_path))
        )
        file_path = r"C:\tmp\brflow-gauditoria_tratado_20260812.parquet"
        manager._append_log(
            "rotina",
            f"{ROTINA_BRUTO_SAVED_PREFIX}g_auditoria|{file_path}",
        )
        self.assertEqual(received, [("g_auditoria", file_path)])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_rotina_bruto_saved_enqueues(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.rotina_bruto_hooks import on_rotina_bruto_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 9
        mock_enqueue.return_value = (job, True)

        file_path = r"C:\tmp\brflow-prod-bruto_20260618.parquet"
        on_rotina_bruto_saved("prod", file_path)

        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            source_path=file_path,
            report_type="prod",
            force=True,
            spawn=True,
        )
        self.assertIn("Job #9", mock_rm._append_log.call_args[0][1])
        self.assertIn("prod", mock_rm._append_log.call_args[0][1])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_rotina_bruto_saved_enqueues_g_auditoria(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.rotina_bruto_hooks import on_rotina_bruto_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock(pk=15)
        mock_enqueue.return_value = (job, True)
        file_path = r"C:\tmp\brflow-gauditoria_tratado_20260812.parquet"
        on_rotina_bruto_saved("g_auditoria", file_path)
        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            source_path=file_path,
            report_type="g_auditoria",
            force=True,
            spawn=True,
        )

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_rotina_bruto_saved_skips_ged_quinzena_parts(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.rotina_bruto_hooks import on_rotina_bruto_saved

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm

        on_rotina_bruto_saved(
            "ged_detalhado",
            r"C:\tmp\ged-detalhado-tratado_202607_1.parquet",
        )

        mock_enqueue.assert_not_called()
        self.assertIn("Ignorado intermediário", mock_rm._append_log.call_args[0][1])

    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_rotina_bruto_saved_enqueues_ged_mensal(self, mock_get_rm, mock_enqueue):
        from apps.automacoes.rotina_bruto_hooks import on_rotina_bruto_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 12
        mock_enqueue.return_value = (job, True)

        file_path = r"C:\tmp\ged-detalhado-tratado_202607.parquet"
        on_rotina_bruto_saved("ged_detalhado", file_path)

        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            source_path=file_path,
            report_type="ged_detalhado",
            force=True,
            spawn=True,
        )
