# -*- coding: utf-8 -*-
import json
import zipfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.cyber_psa.models import CyberRiskOverride
from apps.cyber_psa.services.env_profile import get_env_profile
from apps.cyber_psa.services.registry import findings_path, load_registry
from apps.cyber_psa.services.risk_store import history_dir
from apps.falhas_criticas.constants import GROUP_BRASILIA, GROUP_GLOBAL, GROUP_SAO_CARLOS

User = get_user_model()


class CyberPsaAPITests(TestCase):
    def setUp(self):
        for name in (GROUP_GLOBAL, GROUP_BRASILIA, GROUP_SAO_CARLOS):
            Group.objects.get_or_create(name=name)
        self.global_user = User.objects.create_user(
            "cyber_global", password="test123", email="cyber_global@test.local"
        )
        self.global_user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        self.staff_user = User.objects.create_user(
            "cyber_staff", password="test123", email="cyber_staff@test.local", is_staff=True
        )
        self.bsb_user = User.objects.create_user(
            "cyber_bsb", password="test123", email="cyber_bsb@test.local"
        )
        self.bsb_user.groups.add(Group.objects.get(name=GROUP_BRASILIA))
        self.client = Client()

    def test_access_forbidden_without_permission(self):
        self.client.force_login(self.bsb_user)
        response = self.client.get("/api/v1/cyber-psa/access/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["has_access"])

    def test_overview_ok_for_staff(self):
        self.client.force_login(self.staff_user)
        response = self.client.get("/api/v1/cyber-psa/overview/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("governance", data)
        self.assertIn("glossary", data)
        self.assertGreater(len(data["glossary"]["terms"]), 5)
        self.assertIn("env_profile", data["meta"])
        self.assertGreater(len(data["governance"]), 5)

    def test_run_scan_writes_findings_and_history(self):
        self.client.force_login(self.staff_user)
        report_file = findings_path()
        if report_file.exists():
            report_file.unlink()
        for old in history_dir().glob("*.json"):
            old.unlink()

        response = self.client.post(
            "/api/v1/cyber-psa/run-scan/",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertGreater(data["summary"]["total"], 0)
        self.assertTrue(report_file.is_file())
        self.assertGreaterEqual(len(list(history_dir().glob("*.json"))), 1)

    def test_export_markdown(self):
        self.client.force_login(self.staff_user)
        response = self.client.get("/api/v1/cyber-psa/export/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PSA Cyber", response.content)

    def test_export_package_zip(self):
        self.client.force_login(self.staff_user)
        response = self.client.get("/api/v1/cyber-psa/export-package/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        zf = zipfile.ZipFile(BytesIO(response.content))
        names = zf.namelist()
        self.assertIn("00-resumo-governanca.md", names)
        self.assertIn("manifest.json", names)

    def test_doc_view_and_download(self):
        self.client.force_login(self.staff_user)
        guia = Path(__file__).resolve().parents[4] / "documentation" / "guia-operacao-servidor.md"
        if not guia.is_file():
            self.skipTest("guia-operacao-servidor.md ausente")
        view = self.client.get(
            "/api/v1/cyber-psa/docs/view/",
            {"path": "documentation/guia-operacao-servidor.md"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(view.status_code, 200)
        self.assertIn("content", view.json())
        dl = self.client.get(
            "/api/v1/cyber-psa/docs/download/",
            {"path": "documentation/guia-operacao-servidor.md"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(dl.status_code, 200)

    def test_doc_path_traversal_blocked(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            "/api/v1/cyber-psa/docs/view/",
            {"path": "../../backend/manage.py"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 400)

    def test_risk_accept_requires_note(self):
        self.client.force_login(self.staff_user)
        response = self.client.patch(
            "/api/v1/cyber-psa/risks/CYBER-M-001/",
            data=json.dumps({"status": "accepted", "note": ""}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 400)

    def test_risk_accept_persists(self):
        self.client.force_login(self.staff_user)
        response = self.client.patch(
            "/api/v1/cyber-psa/risks/CYBER-M-001/",
            data=json.dumps({"status": "accepted", "note": "Aceito para intranet local"}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CyberRiskOverride.objects.filter(risk_id="CYBER-M-001", status="accepted").exists())

    def test_history_list_after_scan(self):
        self.client.force_login(self.staff_user)
        self.client.post(
            "/api/v1/cyber-psa/run-scan/",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        response = self.client.get("/api/v1/cyber-psa/history/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.json()["items"]), 0)

    @override_settings(PPLID_ENV_PROFILE="local")
    def test_env_profile_local(self):
        self.assertEqual(get_env_profile(), "local")

    def test_registry_loads(self):
        registry = load_registry()
        self.assertIn("governance", registry)
        self.assertGreater(len(registry["governance"]), 0)
