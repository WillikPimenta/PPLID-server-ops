# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.access.constants import ROLE_PLAN_GERENCIA, role_group_name

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class ProdutividadeSyncPermissionTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_GERENCIA))
        self.client = Client()
        self.sync_user = User.objects.create_user(
            "plan_sync", email="plan_sync@test.local", password="test123"
        )
        self.sync_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_GERENCIA)))
        self.regular_user = User.objects.create_user(
            "regular_user", email="regular_user@test.local", password="test123"
        )

    def test_sync_requires_permission(self):
        response = self.client.post("/api/v1/produtividade/sync/", {"force": False}, content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_authorized_user_can_sync(self):
        self.client.force_login(self.sync_user)
        response = self.client.post("/api/v1/produtividade/sync/", {"force": False}, content_type="application/json")
        self.assertIn(response.status_code, (202, 400))

    def test_regular_user_cannot_sync(self):
        self.client.force_login(self.regular_user)
        response = self.client.post("/api/v1/produtividade/sync/", {"force": False}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
