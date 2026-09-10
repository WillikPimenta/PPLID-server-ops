# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access import registry as R

User = get_user_model()


def _user_has_permission(user, code, **_kwargs):
    if user.username == "d1_viewer_sync" and code == R.PLANEJAMENTO_AUTOMACAO_VIEW:
        return True
    if user.username == "d1_config_sync" and code in (
        R.PLANEJAMENTO_AUTOMACAO_VIEW,
        R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
    ):
        return True
    return False


@override_settings(
    ACCESS_ENFORCEMENT=True,
    REPLICACAO_D1_SOURCE_DIR="",
    REPLICACAO_D1_REPLICADOS_DIR="",
)
class SyncRbacTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer = User.objects.create_user(
            username="d1_viewer_sync", email="d1_viewer_sync@test.local", password="x"
        )
        self.configurator = User.objects.create_user(
            username="d1_config_sync", email="d1_config_sync@test.local", password="x"
        )

    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_viewer_can_list_runs(self, _mock):
        self.client.force_authenticate(user=self.viewer)
        resp = self.client.get("/api/v1/replicacao-d1/runs/")
        self.assertEqual(resp.status_code, 200)

    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_viewer_cannot_enqueue_sync(self, _mock):
        self.client.force_authenticate(user=self.viewer)
        resp = self.client.post("/api/v1/replicacao-d1/sync/", {}, format="json")
        self.assertEqual(resp.status_code, 403)

    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    @patch("apps.replicacao_d1.views.api.get_source_file")
    @patch("apps.replicacao_d1.views.api.enqueue_bot_db_sync")
    def test_configurator_can_enqueue_by_run_id(self, mock_enqueue, mock_source, _mock):
        from apps.common.models import BotDbSyncJob

        mock_source.return_value = MagicMock(path="/tmp/x.xlsx", run_id="20260805_120000")
        job = MagicMock()
        job.pk = 1
        job.status = BotDbSyncJob.STATUS_PENDING
        mock_enqueue.return_value = (job, True)

        self.client.force_authenticate(user=self.configurator)
        resp = self.client.post(
            "/api/v1/replicacao-d1/sync/",
            {"run_id": "20260805_120000"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        mock_enqueue.assert_called_once()

    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_sync_rejects_source_path_in_body(self, _mock):
        self.client.force_authenticate(user=self.configurator)
        with patch("apps.replicacao_d1.views.api.get_source_file") as mock_source:
            mock_source.return_value = MagicMock(path="/safe/x.xlsx", run_id="r1")
            with patch("apps.replicacao_d1.views.api.enqueue_bot_db_sync") as mock_enqueue:
                from apps.common.models import BotDbSyncJob

                job = MagicMock(pk=1, status=BotDbSyncJob.STATUS_PENDING)
                mock_enqueue.return_value = (job, True)
                resp = self.client.post(
                    "/api/v1/replicacao-d1/sync/",
                    {"source_path": "C:\\evil\\file.xlsx"},
                    format="json",
                )
        self.assertEqual(resp.status_code, 202)
        mock_source.assert_called_with(run_id=None)
