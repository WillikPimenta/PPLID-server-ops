# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado

User = get_user_model()
FIXTURE = Path(__file__).resolve().parents[3] / "report_brb" / "tests" / "fixtures" / "BRB_Report_atualizado.xlsx"


@override_settings(BRB_REPORT_USE_EO_DB=False)
class BrbReportAPITests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user = User.objects.create_user("brb_tester", password="test123")
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = Client()
        self.client.force_login(self.user)

    def test_clients_endpoint(self):
        response = self.client.get("/api/v1/brb-report/clients/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        slugs = {c["slug"] for c in data["clients"]}
        self.assertIn("brb", slugs)
        self.assertIn("claro", slugs)

    def test_preview_requires_file(self):
        response = self.client.post("/api/v1/brb-report/preview/")
        self.assertEqual(response.status_code, 400)

    def test_cs_dashboard_without_source_returns_available_false(self):
        response = self.client.get("/api/v1/brb-report/cs-dashboard/", {"client": "brb"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_returns_html(self, serve_mock):
        serve_mock.return_value = ("<html><body>portfolio</body></html>", None)
        response = self.client.get(
            "/api/v1/brb-report/executive-portfolio/",
            {"date_from": "2026-01-01", "date_to": "2026-08-19"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertIn(b"portfolio", response.content)
        serve_mock.assert_called_once()

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_post_with_workbook(self, serve_mock):
        serve_mock.return_value = ("<html><body>portfolio</body></html>", None)
        with FIXTURE.open("rb") as handle:
            upload = SimpleUploadedFile(
                "contestacao.xlsx",
                handle.read(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        response = self.client.post(
            "/api/v1/brb-report/executive-portfolio/",
            {
                "date_from": "2026-01-01",
                "date_to": "2026-08-19",
                "file": upload,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"portfolio", response.content)
        serve_mock.assert_called_once()
        kwargs = serve_mock.call_args.kwargs
        self.assertIsNotNone(kwargs.get("workbook_path"))
        self.assertNotEqual(kwargs.get("workbook_fingerprint"), "eo")

    def test_executive_portfolio_post_rejects_workbook_without_contestacao(self):
        from io import BytesIO

        import pandas as pd

        buffer = BytesIO()
        pd.DataFrame({"x": [1]}).to_excel(buffer, index=False, sheet_name="Outra")
        buffer.seek(0)
        upload = SimpleUploadedFile(
            "sem_contestacao.xlsx",
            buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self.client.post(
            "/api/v1/brb-report/executive-portfolio/",
            {
                "date_from": "2026-01-01",
                "date_to": "2026-08-19",
                "file": upload,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Contestacao", response.json()["detail"])

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_get_with_supplement_key(self, serve_mock):
        serve_mock.return_value = ("<html><body>portfolio</body></html>", None)
        with patch("apps.brb_report.views.resolve_source_workbook") as resolve_mock:
            resolve_mock.return_value = Path("C:/tmp/contestacao.xlsx")
            response = self.client.get(
                "/api/v1/brb-report/executive-portfolio/",
                {
                    "date_from": "2026-01-01",
                    "date_to": "2026-08-19",
                    "supplement_key": "abc123",
                },
            )
        self.assertEqual(response.status_code, 200)
        kwargs = serve_mock.call_args.kwargs
        self.assertEqual(kwargs.get("workbook_fingerprint"), "sup:abc123")

    @patch("apps.brb_report.views.store_portfolio_supplement_workbook")
    def test_executive_portfolio_supplement_upload(self, store_mock):
        store_mock.return_value = "supplement-key"
        with FIXTURE.open("rb") as handle:
            upload = SimpleUploadedFile(
                "contestacao.xlsx",
                handle.read(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        response = self.client.post(
            "/api/v1/brb-report/executive-portfolio/supplement/",
            {"file": upload},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["storage_key"], "supplement-key")

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_rejects_invalid_range(self, serve_mock):
        response = self.client.get(
            "/api/v1/brb-report/executive-portfolio/",
            {"date_from": "2026-08-19", "date_to": "2026-01-01"},
        )
        self.assertEqual(response.status_code, 400)
        serve_mock.assert_not_called()

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_busy_returns_503(self, serve_mock):
        serve_mock.return_value = (None, "queue_timeout")
        response = self.client.get(
            "/api/v1/brb-report/executive-portfolio/",
            {"date_from": "2026-01-01", "date_to": "2026-08-19"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("detail", response.json())
        self.assertEqual(response["Retry-After"], "5")

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_client_view_returns_html(self, serve_mock):
        serve_mock.return_value = ("<html>cliente</html>", None)
        response = self.client.get(
            "/api/v1/brb-report/executive-portfolio/client/",
            {"client": "brb", "date_from": "2026-01-01", "date_to": "2026-08-19"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        serve_mock.assert_called_once()
        kwargs = serve_mock.call_args.kwargs
        self.assertEqual(kwargs.get("standalone_client_slug"), "brb")

    @patch("apps.brb_report.views.serve_executive_portfolio_html")
    def test_executive_portfolio_client_view_requires_client(self, serve_mock):
        response = self.client.get(
            "/api/v1/brb-report/executive-portfolio/client/",
            {"date_from": "2026-01-01", "date_to": "2026-08-19"},
        )
        self.assertEqual(response.status_code, 400)
        serve_mock.assert_not_called()

    @patch("apps.brb_report.views.serve_executive_client_rca_pdf")
    def test_executive_portfolio_client_rca_returns_pdf(self, serve_mock):
        serve_mock.return_value = (b"%PDF-1.4", None)
        response = self.client.get(
            "/api/v1/brb-report/executive-portfolio/client/rca/",
            {"client": "brb", "date_from": "2026-01-01", "date_to": "2026-08-19"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])

    @patch("apps.brb_report.views.prepare_cs_source_snapshot")
    @patch("apps.brb_report.views.store_cs_source_workbook")
    def test_cs_source_upload_does_not_generate_artifacts(self, store_mock, snapshot_mock):
        store_mock.return_value = "cs-source-key"
        snapshot_mock.return_value = {
            "storage_key": "cs-source-key",
            "client_slug": "brb",
            "contestations": 42,
            "days": 12,
        }
        upload = SimpleUploadedFile(
            "master.xlsx",
            b"fake-xlsx-for-mocked-storage",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self.client.post(
            "/api/v1/brb-report/cs-dashboard/source/",
            {"file": upload, "client": "brb"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["storage_key"], "cs-source-key")
        self.assertEqual(body["contestations"], 42)
        self.assertNotIn("artifacts", body)
        snapshot_mock.assert_called_once_with("cs-source-key", "brb")

    def test_preview_and_generate_with_fixture(self):
        if not FIXTURE.is_file():
            self.skipTest(f"Fixture ausente: {FIXTURE}")
        with FIXTURE.open("rb") as fh:
            response = self.client.post(
                "/api/v1/brb-report/preview/",
                {"file": fh},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200, response.content)
        preview = response.json()
        self.assertIn("NA_Demandas", preview["sheets"])

        with FIXTURE.open("rb") as fh:
            response = self.client.post(
                "/api/v1/brb-report/generate/",
                {
                    "file": fh,
                    "cliente": "brb",
                    "inicio": "2026-01-01",
                    "fim": "2026-08-06",
                    "formato": "pulse",
                    "enriquecer": "false",
                    "excel_cliente": "false",
                    "excel_interno": "false",
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIn("quality_pulse", body["artifacts"])
        self.assertIn("quality_pulse", body["downloads"])

        dl = self.client.get(body["downloads"]["quality_pulse"])
        self.assertEqual(dl.status_code, 200)
        self.assertIn("text/html", dl["Content-Type"])
        html = b"".join(dl.streaming_content).decode("utf-8")
        self.assertNotIn("__PULSE_PERIOD__", html)
        self.assertNotIn("pulse-period-bar", html)
        self.assertIn("nav.analytic-expanded", html)
        self.assertIn('classList.toggle("analytic-expanded",on)', html)
        self.assertIn("th.num,td.num{text-align:right", html)
        self.assertIn("32,3 h de 33 h", html)
        self.assertNotIn("32,3 h de 83,7 h", html)

        cs = self.client.get(
            "/api/v1/brb-report/cs-dashboard/",
            {
                "client": "brb",
                "storage_key": body["storage_key"],
                "date_from": "2026-01-01",
                "date_to": "2026-08-06",
            },
        )
        self.assertEqual(cs.status_code, 200, cs.content)
        cs_body = cs.json()
        self.assertTrue(cs_body["available"])
        self.assertIn("confirmed_rate", cs_body["summary"])
        self.assertIn("health", cs_body)
        self.assertIn("monthly", cs_body)
        self.assertIn("records", cs_body)

        confirmed_only = self.client.get(
            "/api/v1/brb-report/cs-dashboard/",
            {
                "client": "brb",
                "storage_key": body["storage_key"],
                "date_from": "2026-01-01",
                "date_to": "2026-08-06",
                "result": "falha",
            },
        )
        self.assertEqual(confirmed_only.status_code, 200, confirmed_only.content)
        filtered_summary = confirmed_only.json()["summary"]
        self.assertEqual(filtered_summary["total"], filtered_summary["confirmed_failures"])

        storage_key = body["storage_key"]
        regen = self.client.post(
            f"/api/v1/brb-report/regenerate/{storage_key}/",
            {
                "cliente": "brb",
                "inicio": "2026-03-01",
                "fim": "2026-03-31",
                "enriquecer": "false",
                "excel_cliente": "false",
                "excel_interno": "false",
            },
            format="multipart",
        )
        self.assertEqual(regen.status_code, 201, regen.content)
        regen_body = regen.json()
        self.assertIn("quality_pulse", regen_body["artifacts"])
        self.assertNotEqual(regen_body["storage_key"], storage_key)

        dl2 = self.client.get(regen_body["downloads"]["quality_pulse"])
        self.assertEqual(dl2.status_code, 200)
        self.assertIn("text/html", dl2["Content-Type"])

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_denied_without_permission(self):
        outsider = User.objects.create_user(
            "outsider_brb",
            email="outsider_brb@example.com",
            password="test123",
        )
        client = Client()
        client.force_login(outsider)
        response = client.get("/api/v1/brb-report/clients/")
        self.assertEqual(response.status_code, 403)

    def test_regenerate_missing_source(self):
        response = self.client.post(
            "/api/v1/brb-report/regenerate/nonexistent-key/",
            {"cliente": "brb", "inicio": "2026-01-01", "fim": "2026-01-31"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 404)


@override_settings(BRB_REPORT_USE_EO_DB=True)
class BrbReportEoModeTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user = User.objects.create_user("brb_eo_tester", password="test123")
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = Client()
        self.client.force_login(self.user)

    def test_clients_includes_eo_db_mode(self):
        response = self.client.get("/api/v1/brb-report/clients/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["eo_db_mode"])

    def test_preview_db_without_upload(self):
        response = self.client.get(
            "/api/v1/brb-report/preview-db/",
            {"client": "brb", "inicio": "2026-01-01", "fim": "2026-12-31"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["source_mode"], "eo_hybrid")
        self.assertIn("eo", body)

    @patch("apps.brb_report.views.generate_reports")
    def test_generate_without_upload_allowed(self, generate_mock):
        generate_mock.return_value = {
            "storage_key": "eo-key",
            "client_slug": "brb",
            "client_nome": "BRB",
            "periodo": "teste",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "artifacts": {"quality_pulse": "quality_overview_brb.html"},
            "completeness": "partial",
            "warnings": [],
            "source_mode": "eo_hybrid",
            "kpis": {},
            "generated_at": "2026-01-01T00:00:00",
        }
        response = self.client.post(
            "/api/v1/brb-report/generate/",
            {"cliente": "brb", "inicio": "2026-01-01", "fim": "2026-01-31"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        generate_mock.assert_called_once()
        self.assertIsNone(generate_mock.call_args.kwargs.get("workbook_path"))

    def test_cs_dashboard_live_from_eo_without_source(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")
        QualidadeAuditado.objects.create(
            id_cliente=35,
            protocolo="LIVE1",
            tipo_analise="Contestação Externa",
            data=date(2026, 2, 10),
            data_recepcao_contestacao=date(2026, 2, 10),
            data_analise=date(2026, 2, 5),
            matricula="c90001a",
            procedencia="Procedente",
            source_file="test",
        )
        response = self.client.get(
            "/api/v1/brb-report/cs-dashboard/",
            {"client": "brb", "date_from": "2026-01-01", "date_to": "2026-12-31"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["source"]["source_mode"], "eo_live")
        self.assertGreaterEqual(body["summary"]["total"], 1)
