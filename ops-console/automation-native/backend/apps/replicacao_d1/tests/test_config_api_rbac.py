# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access import registry as R

User = get_user_model()

BASE = "/api/v1/replicacao-d1/config"


def _user_has_permission(user, code, **_kwargs):
    if user.username in ("d1_reader", "d1_config") and code == R.PLANEJAMENTO_AUTOMACAO_VIEW:
        return True
    if user.username in ("d1_config", "d1_import") and code == R.PLANEJAMENTO_AUTOMACAO_CONFIGURE:
        return True
    return False


def _can_configure(user):
    return user.username in ("d1_config", "d1_import")


@override_settings(ACCESS_ENFORCEMENT=True)
class ConfigApiRbacTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.reader = User.objects.create_user(
            username="d1_reader", email="d1_reader@test.local", password="x"
        )
        self.configurator = User.objects.create_user(
            username="d1_config", email="d1_config@test.local", password="x"
        )
        self.nobody = User.objects.create_user(
            username="d1_nobody", email="d1_nobody@test.local", password="x"
        )

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_reader_get_ok_post_patch_forbidden(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        resp_get = self.client.get(f"{BASE}/geral/")
        self.assertEqual(resp_get.status_code, 200)
        resp_post = self.client.post(f"{BASE}/fonte/ativar/", {"force": True}, format="json")
        self.assertEqual(resp_post.status_code, 403)
        resp_patch = self.client.patch(f"{BASE}/geral/", {"seed": 99}, format="json")
        self.assertEqual(resp_patch.status_code, 403)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_configurator_can_patch(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        resp = self.client.patch(f"{BASE}/geral/", {"seed": 77}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["seed"], 77)

    @patch("apps.access.permissions.user_has_permission", return_value=False)
    def test_no_access_get_forbidden(self, _mock):
        self._auth(self.nobody)
        resp = self.client.get(f"{BASE}/geral/")
        self.assertEqual(resp.status_code, 403)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_reader_can_get_resumo(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        resp = self.client.get(f"{BASE}/resumo/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("contagens", resp.data)
