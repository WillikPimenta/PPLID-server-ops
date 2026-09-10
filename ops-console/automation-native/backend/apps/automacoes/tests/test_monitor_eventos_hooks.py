# -*- coding: utf-8 -*-
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from app.services.robot_manager import MONITOR_EVENTOS_SAVED_PREFIX, RobotProcessManager


class RobotManagerMonitorEventosSavedTests(SimpleTestCase):
    def test_append_log_invokes_monitor_eventos_callback(self):
        manager = RobotProcessManager()
        received: list[str] = []
        manager.register_monitor_eventos_saved_callback(received.append)

        file_path = r"C:\tmp\monitor-eventos-tratado_2026-06-18.parquet"
        manager._append_log("production", f"{MONITOR_EVENTOS_SAVED_PREFIX}{file_path}")

        self.assertEqual(received, [file_path])

    @patch("apps.automacoes.monitor_eventos_hooks._snapshot_monitor_source")
    @patch("apps.common.bot_db_sync_queue.enqueue_bot_db_sync")
    @patch("apps.automacoes.services.get_robot_manager")
    def test_on_monitor_eventos_saved_enqueues(
        self,
        mock_get_rm,
        mock_enqueue,
        mock_snapshot,
    ):
        from apps.automacoes.monitor_eventos_hooks import on_monitor_eventos_saved
        from apps.common.models import BotDbSyncJob

        mock_rm = MagicMock()
        mock_get_rm.return_value = mock_rm
        job = MagicMock()
        job.pk = 3
        mock_enqueue.return_value = (job, True)

        file_path = r"C:\tmp\monitor-eventos-tratado_2026-06-18.parquet"
        snapshot_path = Path(r"C:\tmp\snapshots\monitor-abc.parquet")
        mock_snapshot.return_value = snapshot_path
        on_monitor_eventos_saved(file_path)

        mock_snapshot.assert_called_once_with(file_path)
        mock_enqueue.assert_called_once_with(
            domain=BotDbSyncJob.DOMAIN_MONITOR_EVENTOS,
            source_path=str(snapshot_path),
            force=False,
            spawn=True,
        )
        self.assertIn("Job #3", mock_rm._append_log.call_args[0][1])

    def test_snapshot_monitor_source_is_immutable_and_content_addressed(self):
        from apps.automacoes.monitor_eventos_hooks import _snapshot_monitor_source

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "monitor-eventos-tratado_2026-06-18.parquet"
            source.write_bytes(b"primeira-versao")

            with override_settings(BOT_DB_SYNC_SNAPSHOT_DIR=root / "snapshots"):
                first = _snapshot_monitor_source(str(source))
                repeated = _snapshot_monitor_source(str(source))
                source.write_bytes(b"segunda-versao")
                second = _snapshot_monitor_source(str(source))

            self.assertEqual(first, repeated)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"primeira-versao")
            self.assertEqual(second.read_bytes(), b"segunda-versao")
