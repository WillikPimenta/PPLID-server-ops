# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class MonitoramentoSlaApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sla_user", password="x", email="sla_user@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_status_ok(self):
        res = self.client.get("/api/v1/monitoramento-sla/status/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.get("ok"))

    def test_kpis_ok(self):
        res = self.client.get("/api/v1/monitoramento-sla/kpis/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("natural", res.data)

    def test_deny_without_perm(self):
        other = User.objects.create_user(
            username="no_sla", password="x", email="no_sla@example.com"
        )
        c = APIClient()
        c.force_authenticate(user=other)
        res = c.get("/api/v1/monitoramento-sla/status/")
        self.assertEqual(res.status_code, 403)
