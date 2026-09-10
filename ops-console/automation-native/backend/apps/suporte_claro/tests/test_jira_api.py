# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.workforce.models import Agent, UserProfile

User = get_user_model()

JIRA_BASE = {"JIRA_BASE_URL": "https://jira.test.local"}


class SuporteClaroJiraApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="agent001",
            password="pass12345",
            email="agent001@test.local",
        )
        self.agent = Agent.objects.create(
            full_name="Agent Test",
            user_lan_id="agent001",
            email="agent001@test.local",
            jira_api_token="pat-user-token",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client.force_authenticate(user=self.user)
        self.preview_url = "/api/v1/suporte-claro/jira/preview/"
        self.run_url = "/api/v1/suporte-claro/jira/run/"
        self.config_url = "/api/v1/suporte-claro/jira/config/"
        self.creds_url = "/api/v1/suporte-claro/jira/credentials/"
        self.status_url = "/api/v1/suporte-claro/jira/status/"
        self.received_at = timezone.now().replace(microsecond=0).isoformat()
        # Cadastro agora auto-cria Jira; nestes testes o foco é formalização manual/legado.
        self._auto_jira = patch(
            "apps.suporte_claro.views.create_and_link_jira_for_registro",
            return_value={"ok": True, "skipped": True, "reason": "test_skip", "issue_key": None},
        )
        self._auto_jira.start()
        self.addCleanup(self._auto_jira.stop)

    def _create_registro(self, protocolo: str = "JIRA-1") -> int:
        response = self.client.post(
            "/api/v1/suporte-claro/registros/",
            {
                "titulo": f"Demanda {protocolo}",
                "protocolo": protocolo,
                "irregularidade": "Teste Jira",
                "received_at": self.received_at,
                "origem": "teams",
                "status": "aberto",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["registro"]["id"]

    def _create_eligible_registro(self, protocolo: str = "JIRA-1") -> int:
        response = self.client.post(
            "/api/v1/suporte-claro/registros/",
            {
                "titulo": f"Demanda {protocolo}",
                "protocolo": protocolo,
                "irregularidade": "Teste Jira",
                "avaliacao": "Retorno registrado",
                "received_at": self.received_at,
                "origem": "teams",
                "status": "concluido",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["registro"]["id"]

    @override_settings(**JIRA_BASE)
    def test_jira_config_with_user_token(self):
        response = self.client.get(self.config_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["jira_api_configured"])
        self.assertTrue(data["jira_token_configured"])
        self.assertEqual(data["jira_username"], "agent001")
        self.assertFalse(data["job"]["running"])

    @override_settings(JIRA_BASE_URL="")
    def test_jira_config_without_base_url(self):
        response = self.client.get(self.config_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["jira_api_configured"])

    @override_settings(**JIRA_BASE)
    def test_save_credentials(self):
        self.agent.jira_api_token = ""
        self.agent.save(update_fields=["jira_api_token"])
        response = self.client.put(
            self.creds_url,
            {"jira_api_token": "new-pat"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.jira_api_token, "new-pat")
        self.assertTrue(response.json()["jira_token_configured"])

    def test_jira_preview_weekly(self):
        self._create_registro()
        response = self.client.post(
            self.preview_url,
            {"kind": "weekly", "profile": "planejamento"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["kind"], "weekly")
        self.assertIn("summary", data)

    def test_jira_preview_registro(self):
        registro_id = self._create_eligible_registro()
        response = self.client.post(
            self.preview_url,
            {"kind": "registro", "registro_id": registro_id, "profile": "processos"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("[Suporte Claro]", response.json()["summary"])

    def test_jira_preview_registro_rejects_non_eligible(self):
        registro_id = self._create_registro()
        response = self.client.post(
            self.preview_url,
            {"kind": "registro", "registro_id": registro_id, "profile": "processos"},
        )
        self.assertEqual(response.status_code, 400)

    def test_jira_pendentes_lists_today_eligible_only(self):
        eligible_id = self._create_eligible_registro()
        self._create_registro("JIRA-ABERTO")
        response = self.client.get("/api/v1/suporte-claro/jira/pendentes/")
        self.assertEqual(response.status_code, 200)
        ids = [item["id"] for item in response.json()["items"]]
        self.assertIn(eligible_id, ids)

    @override_settings(**JIRA_BASE)
    def test_jira_run_requires_user_token(self):
        self.agent.jira_api_token = ""
        self.agent.save(update_fields=["jira_api_token"])
        registro_id = self._create_eligible_registro("CFG-1")
        response = self.client.post(
            self.run_url,
            {"kind": "registro", "registro_id": registro_id, "profile": "planejamento"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Token", response.json()["detail"])

    @override_settings(JIRA_BASE_URL="")
    def test_jira_run_requires_base_url(self):
        registro_id = self._create_eligible_registro("CFG-2")
        response = self.client.post(
            self.run_url,
            {"kind": "registro", "registro_id": registro_id, "profile": "planejamento"},
        )
        self.assertEqual(response.status_code, 503)

    @override_settings(**JIRA_BASE)
    @patch("apps.suporte_claro.jira_views.create_issue")
    def test_jira_run_registro_creates_and_links(self, mock_create):
        registro_id = self._create_eligible_registro("RUN-1")
        mock_create.return_value = {
            "ok": True,
            "issue_key": "PPLID-100",
            "error": None,
            "status_code": 201,
        }
        response = self.client.post(
            self.run_url,
            {
                "kind": "registro",
                "registro_id": registro_id,
                "profile": "planejamento",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["issue_key"], "PPLID-100")
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs.get("user"), self.user)

        reg = SuporteClaroRegistro.objects.get(pk=registro_id)
        self.assertTrue(
            reg.chamados_externos.filter(sistema="jira", codigo__iexact="PPLID-100").exists()
            or (reg.chamado_codigo or "").upper() == "PPLID-100"
        )

    @override_settings(**JIRA_BASE)
    @patch("apps.suporte_claro.jira_views.create_issue")
    def test_jira_run_batch_partial(self, mock_create):
        id_a = self._create_eligible_registro("BATCH-A")
        id_b = self._create_eligible_registro("BATCH-B")

        def _side_effect(_profile, **kwargs):
            summary = kwargs.get("summary") or ""
            if "BATCH-A" in summary:
                return {"ok": True, "issue_key": "PPLID-1", "error": None, "status_code": 201}
            return {
                "ok": False,
                "issue_key": None,
                "error": "Field components is required",
                "status_code": 400,
            }

        mock_create.side_effect = _side_effect
        response = self.client.post(
            self.run_url,
            {
                "kind": "batch",
                "registro_ids": [id_a, id_b],
                "profile": "planejamento",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count_ok"], 1)
        self.assertEqual(data["count_fail"], 1)

    def test_jira_status_idle(self):
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["job"]["running"])

    def test_jira_pendentes_scope_all_and_today(self):
        id_today = self._create_eligible_registro("PEND-TODAY")
        yesterday = timezone.localdate() - timedelta(days=1)
        reg = SuporteClaroRegistro.objects.get(pk=id_today)
        reg.received_at = timezone.make_aware(datetime.combine(yesterday, datetime.min.time()))
        reg.save(update_fields=["received_at"])

        response_all = self.client.get("/api/v1/suporte-claro/jira/pendentes/?scope=all")
        self.assertEqual(response_all.status_code, 200)
        self.assertEqual(response_all.json()["total"], 1)
        self.assertEqual(response_all.json()["total_today"], 0)
