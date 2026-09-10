# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.jira_profiles import build_issue_fields, get_rest_profile
from apps.suporte_claro.services.jira_rest import (
    JiraCredentials,
    create_issue,
    list_transitions,
    sync_portal_status_to_jira,
    transition_issue,
)
from apps.suporte_claro.services.jira_status_map import transition_names_for_portal_status
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


class JiraStatusMapTests(SimpleTestCase):
    def test_aberto_has_no_transitions(self):
        self.assertEqual(transition_names_for_portal_status("aberto"), [])

    @override_settings(JIRA_TRANSITION_EM_ATENDIMENTO="In Progress,Em andamento")
    def test_em_atendimento_defaults(self):
        names = transition_names_for_portal_status("em_atendimento")
        self.assertEqual(names[0], "In Progress")
        self.assertIn("Em andamento", names)

    @override_settings(JIRA_TRANSITION_CONCLUIDO="Done")
    def test_concluido_env_override(self):
        self.assertEqual(transition_names_for_portal_status("concluido"), ["Done"])


class JiraAssigneeFieldsTests(SimpleTestCase):
    @override_settings(JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY="PPLID", JIRA_CATEGORY_FIELD_ID="")
    def test_assignee_reporter_in_fields(self):
        profile = get_rest_profile("planejamento")
        fields = build_issue_fields(
            profile,
            summary="S",
            description="D",
            assignee_name="c93123a",
            reporter_name="c93123a",
        )
        self.assertEqual(fields["assignee"], {"name": "c93123a"})
        self.assertEqual(fields["reporter"], {"name": "c93123a"})


class JiraTransitionsClientTests(SimpleTestCase):
    @override_settings(JIRA_BASE_URL="https://jira.test.local", JIRA_AUTH_MODE="bearer")
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_list_and_transition(self, mock_session_cls):
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False

        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.headers = {"Content-Type": "application/json"}
        list_resp.text = "{}"
        list_resp.json.return_value = {
            "transitions": [
                {"id": "11", "name": "In Progress"},
                {"id": "31", "name": "Done"},
            ]
        }
        post_resp = MagicMock()
        post_resp.status_code = 204
        post_resp.headers = {"Content-Type": "application/json"}
        post_resp.text = ""
        session.request.side_effect = [list_resp, list_resp, post_resp]

        creds = JiraCredentials(username="u", api_token="t")
        listed = list_transitions("PPLID-1", credentials=creds)
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["transitions"]), 2)

        applied = transition_issue("PPLID-1", transition_name="In Progress", credentials=creds)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["transition_id"], "11")

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
        JIRA_CATEGORY_FIELD_ID="",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_create_issue_passes_assignee(self, mock_session_cls):
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 201
        resp.headers = {"Content-Type": "application/json"}
        resp.text = '{"key":"PPLID-9"}'
        resp.json.return_value = {"key": "PPLID-9"}
        session.request.return_value = resp

        result = create_issue(
            "planejamento",
            summary="Sum",
            description="Desc",
            credentials=JiraCredentials(username="c93123a", api_token="pat"),
        )
        self.assertTrue(result["ok"])
        payload = session.request.call_args.kwargs["json"]
        self.assertEqual(payload["fields"]["assignee"]["name"], "c93123a")
        self.assertNotIn("reporter", payload["fields"])

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_attach_file_success(self, mock_session_cls):
        from apps.suporte_claro.services.jira_rest import attach_file_to_issue

        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.text = "[]"
        session.request.return_value = resp

        result = attach_file_to_issue(
            "PPLID-1",
            filename="foto.png",
            content=b"\x89PNG",
            content_type="image/png",
            credentials=JiraCredentials(username="u", api_token="t"),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(session.headers.get("X-Atlassian-Token"), "no-check")
        self.assertNotIn("Content-Type", session.headers)
        self.assertIn("files", session.request.call_args.kwargs)


@override_settings(
    MEDIA_ROOT="/tmp/pplid_suporte_claro_auto_jira_media",
    JIRA_BASE_URL="https://jira.test.local",
)
class AutoJiraCreateApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="c93123a", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="c93123a",
            jira_api_token="secret-pat",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/suporte-claro/registros/"
        self.received_at = timezone.now().replace(microsecond=0).isoformat()

    def _payload(self, **extra):
        data = {
            "titulo": "Demanda auto jira",
            "protocolo": "900001",
            "received_at": self.received_at,
            "origem": "teams",
            "status": "aberto",
            **extra,
        }
        return data

    @patch("apps.suporte_claro.services.jira_auto.create_issue")
    def test_create_links_jira_issue(self, mock_create):
        mock_create.return_value = {
            "ok": True,
            "issue_key": "PPLID-123",
            "error": None,
            "status_code": 201,
        }
        response = self.client.post(self.url, self._payload(), format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data["jira_auto"]["issue_key"], "PPLID-123")
        registro = data["registro"]
        self.assertTrue(
            any(c["sistema"] == "jira" and c["codigo"].upper() == "PPLID-123" for c in registro["chamados_externos"])
            or registro.get("chamado_codigo", "").upper() == "PPLID-123"
        )
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["assignee_name"], "c93123a")
        self.assertFalse(kwargs.get("reporter_name"))
        self.assertEqual(SuporteClaroRegistro.objects.count(), 1)

    def test_create_without_pat_returns_400(self):
        self.agent.jira_api_token = ""
        self.agent.save(update_fields=["jira_api_token", "updated_at"])
        response = self.client.post(self.url, self._payload(), format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Token", response.json()["detail"])
        self.assertEqual(SuporteClaroRegistro.objects.count(), 0)

    @patch("apps.suporte_claro.services.jira_auto.create_issue")
    def test_create_jira_failure_rolls_back(self, mock_create):
        mock_create.return_value = {
            "ok": False,
            "issue_key": None,
            "error": "Jira down",
            "status_code": 502,
        }
        response = self.client.post(self.url, self._payload(), format="multipart")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Jira down", response.json()["detail"])
        self.assertEqual(SuporteClaroRegistro.objects.count(), 0)


@override_settings(
    MEDIA_ROOT="/tmp/pplid_suporte_claro_auto_jira_media",
    JIRA_BASE_URL="https://jira.test.local",
)
class AutoJiraStatusApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="c93123a", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="c93123a",
            jira_api_token="secret-pat",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client.force_authenticate(user=self.user)
        self.registro = SuporteClaroRegistro.objects.create(
            titulo="Com Jira",
            protocolo="900002",
            received_at=timezone.now(),
            origem="teams",
            status=SuporteClaroRegistro.STATUS_ABERTO,
            avaliacao="",
            created_by=self.user,
            chamado_sistema="jira",
            chamado_codigo="PPLID-55",
        )

    @patch("apps.suporte_claro.views.sync_registro_to_jira")
    def test_patch_status_calls_sync(self, mock_sync):
        mock_sync.return_value = {
            "ok": True,
            "skipped": False,
            "issue_key": "PPLID-55",
            "transition": "In Progress",
            "error": None,
        }
        response = self.client.patch(
            f"/api/v1/suporte-claro/registros/{self.registro.id}/status/",
            {"status": "em_atendimento"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_sync.called)
        self.assertEqual(response.json()["jira_sync"]["transition"], "In Progress")

    def test_patch_avaliacao_without_status_400(self):
        response = self.client.patch(
            f"/api/v1/suporte-claro/registros/{self.registro.id}/",
            {"avaliacao": "Retorno ok"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.json()["detail"].lower())

    def test_status_concluido_requires_avaliacao(self):
        response = self.client.patch(
            f"/api/v1/suporte-claro/registros/{self.registro.id}/status/",
            {"status": "concluido"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("apps.suporte_claro.views.sync_registro_to_jira")
    def test_retorno_with_status_syncs(self, mock_sync):
        mock_sync.return_value = {
            "ok": True,
            "skipped": False,
            "issue_key": "PPLID-55",
            "transition": "Done",
            "error": None,
        }
        response = self.client.patch(
            f"/api/v1/suporte-claro/registros/{self.registro.id}/",
            {"avaliacao": "Resolvido", "status": "concluido"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_sync.called)
        self.assertNotIn("comment", mock_sync.call_args.kwargs)
        self.registro.refresh_from_db()
        self.assertEqual(self.registro.status, "concluido")
        self.assertEqual(self.registro.avaliacao, "Resolvido")


class SyncPortalStatusUnitTests(TestCase):
    def setUp(self):
        from apps.suporte_claro.models import SuporteClaroChamadoExterno

        self.user = User.objects.create_user(username="c93123a", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="c93123a",
            jira_api_token="secret-pat",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.registro = SuporteClaroRegistro.objects.create(
            titulo="Sync",
            protocolo="1",
            received_at=timezone.now(),
            origem="teams",
            status="aberto",
            created_by=self.user,
            chamado_sistema="jira",
            chamado_codigo="PPLID-77",
        )
        SuporteClaroChamadoExterno.objects.create(
            registro=self.registro,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
            codigo="PPLID-77",
            natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO,
        )

    @override_settings(JIRA_BASE_URL="https://jira.test.local")
    @patch("apps.suporte_claro.services.jira_rest.list_transitions")
    @patch("apps.suporte_claro.services.jira_rest.transition_issue")
    def test_sync_matches_transition_name(self, mock_transition, mock_list):
        mock_list.return_value = {
            "ok": True,
            "transitions": [{"id": "21", "name": "Em andamento", "to": "Em andamento"}],
            "error": None,
        }
        mock_transition.return_value = {
            "ok": True,
            "skipped": False,
            "transition_id": "21",
            "transition_name": "Em andamento",
        }
        result = sync_portal_status_to_jira(self.registro, "em_atendimento", self.user)
        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["transition"], "Em andamento")

    @override_settings(JIRA_BASE_URL="https://jira.test.local")
    @patch("apps.suporte_claro.services.jira_rest.list_transitions")
    def test_sync_skips_when_transition_missing(self, mock_list):
        mock_list.return_value = {
            "ok": True,
            "transitions": [{"id": "1", "name": "Something Else", "to": "Other"}],
            "error": None,
        }
        result = sync_portal_status_to_jira(self.registro, "em_atendimento", self.user)
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "transition_unavailable")

    @override_settings(JIRA_BASE_URL="https://jira.test.local")
    @patch("apps.suporte_claro.services.jira_rest.transition_issue")
    @patch("apps.suporte_claro.services.jira_rest.list_transitions")
    def test_sync_matches_by_target_status(self, mock_list, mock_transition):
        mock_list.return_value = {
            "ok": True,
            "transitions": [{"id": "41", "name": "Finish Work", "to": "Done"}],
            "error": None,
        }
        mock_transition.return_value = {
            "ok": True,
            "skipped": False,
            "transition_id": "41",
            "transition_name": "Finish Work",
        }
        result = sync_portal_status_to_jira(self.registro, "concluido", self.user)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transition"], "Finish Work")

    @override_settings(JIRA_BASE_URL="https://jira.test.local")
    @patch("apps.suporte_claro.services.jira_rest.add_issue_comment")
    @patch("apps.suporte_claro.services.jira_rest.sync_portal_status_to_jira")
    def test_sync_registro_does_not_post_comment(self, mock_status, mock_comment):
        from apps.suporte_claro.services.jira_rest import sync_registro_to_jira

        mock_status.return_value = {
            "ok": True,
            "skipped": False,
            "issue_key": "PPLID-77",
            "transition": "Done",
            "error": None,
        }
        result = sync_registro_to_jira(
            self.registro,
            "concluido",
            self.user,
            comment="Cliente orientado",
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("comment", result)
        mock_comment.assert_not_called()
        mock_status.assert_called_once()

@override_settings(
    MEDIA_ROOT="/tmp/pplid_suporte_claro_attach_media",
    JIRA_BASE_URL="https://jira.test.local",
)
class AutoJiraAttachmentsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="c93123a", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="c93123a",
            jira_api_token="secret-pat",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client.force_authenticate(user=self.user)

    @patch("apps.suporte_claro.services.jira_auto.create_issue")
    def test_create_does_not_upload_attachments(self, mock_create):
        from django.core.files.uploadedfile import SimpleUploadedFile

        mock_create.return_value = {
            "ok": True,
            "issue_key": "PPLID-200",
            "error": None,
            "status_code": 201,
        }

        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
            b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        with patch(
            "apps.suporte_claro.services.jira_rest.attach_registro_anexos_to_jira"
        ) as mock_attach:
            response = self.client.post(
                "/api/v1/suporte-claro/registros/",
                {
                    "titulo": "Com anexo",
                    "protocolo": "ATT-1",
                    "received_at": timezone.now().replace(microsecond=0).isoformat(),
                    "origem": "teams",
                    "status": "aberto",
                    "images": SimpleUploadedFile("foto.png", png, content_type="image/png"),
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.content)
            mock_attach.assert_not_called()
        attachments = response.json()["jira_auto"]["attachments"]
        self.assertTrue(attachments.get("skipped"))
        self.assertEqual(attachments.get("uploaded"), 0)
        self.assertNotIn("anexo", response.json()["message"].lower())
