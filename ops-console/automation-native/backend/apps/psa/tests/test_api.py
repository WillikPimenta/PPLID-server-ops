# -*- coding: utf-8 -*-
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.falhas_criticas.constants import GROUP_BRASILIA, GROUP_GLOBAL, GROUP_SAO_CARLOS
from apps.psa.services.registry import load_registry, live_report_path

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class PsaAPITests(TestCase):
    def setUp(self):
        for name in (GROUP_GLOBAL, GROUP_BRASILIA, GROUP_SAO_CARLOS):
            Group.objects.get_or_create(name=name)
        self.global_user = User.objects.create_user(
            "psa_global", password="test123", email="psa_global@test.local"
        )
        self.global_user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        admin_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.global_user.groups.add(admin_group)
        self.staff_user = User.objects.create_user(
            "psa_staff", password="test123", email="psa_staff@test.local", is_staff=True
        )
        # ``is_staff`` habilita o admin Django, mas não substitui permissão
        # funcional quando ACCESS_ENFORCEMENT está ativo.
        self.staff_user.groups.add(admin_group)
        self.bsb_user = User.objects.create_user(
            "psa_bsb", password="test123", email="psa_bsb@test.local"
        )
        self.bsb_user.groups.add(Group.objects.get(name=GROUP_BRASILIA))
        self.client = Client()

    def test_access_forbidden_without_permission(self):
        self.client.force_login(self.bsb_user)
        response = self.client.get("/api/v1/portal-ops/access/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["has_access"])

    def test_access_ok_for_global(self):
        self.client.force_login(self.global_user)
        response = self.client.get("/api/v1/portal-ops/access/", HTTP_HOST="localhost")
        self.assertTrue(response.json()["has_access"])

    def test_overview_forbidden_without_can_sync(self):
        self.client.force_login(self.bsb_user)
        response = self.client.get("/api/v1/portal-ops/overview/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 403)

    def test_overview_ok_for_staff(self):
        self.client.force_login(self.staff_user)
        response = self.client.get("/api/v1/portal-ops/overview/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("modules", data)
        self.assertGreater(len(data["modules"]), 10)
        self.assertIn("by_section", data["summary"])
        self.assertIn("health_quick", data)

    def test_doc_view_guia(self):
        self.client.force_login(self.staff_user)
        guia = Path(__file__).resolve().parents[4] / "documentation" / "guia-operacao-servidor.md"
        if not guia.is_file():
            self.skipTest("guia-operacao-servidor.md ausente")
        response = self.client.get(
            "/api/v1/portal-ops/docs/view/",
            {"path": "documentation/guia-operacao-servidor.md"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("content", response.json())

    def test_doc_traversal_blocked(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            "/api/v1/portal-ops/docs/view/",
            {"path": "../backend/manage.py"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 400)

    def test_registry_loads_modules(self):
        registry = load_registry()
        self.assertIn("modules", registry)
        self.assertGreater(len(registry["modules"]), 0)

    def test_run_checks_writes_report(self):
        self.client.force_login(self.staff_user)
        report_file = live_report_path()
        if report_file.exists():
            report_file.unlink()

        response = self.client.post(
            "/api/v1/portal-ops/run-checks/",
            data=json.dumps({"include_spa": False}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("suites", data["summary"])
        self.assertGreater(data["summary"]["total"], 0)
        self.assertTrue(report_file.is_file())
