# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from apps.suporte_claro.services.jira_profiles import build_issue_fields, get_rest_profile
from apps.suporte_claro.services.jira_rest import (
    JiraCredentials,
    create_issue,
    jira_rest_configured,
    resolve_jira_credentials,
    user_jira_configured,
)
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


class JiraProfilesTests(SimpleTestCase):
    @override_settings(
        JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY="PPLID",
        JIRA_CATEGORY_FIELD_ID="customfield_37898",
        JIRA_PROFILE_PLANEJAMENTO_CATEGORY="Informação",
        JIRA_PROFILE_PLANEJAMENTO_ISSUETYPE="Task",
    )
    def test_planejamento_fields_include_category(self):
        profile = get_rest_profile("planejamento")
        fields = build_issue_fields(profile, summary="S", description="D")
        self.assertEqual(fields["project"]["key"], "PPLID")
        self.assertEqual(fields["issuetype"]["name"], "Task")
        self.assertEqual(fields["components"][0]["name"], "Suporte Claro")
        self.assertEqual(fields["customfield_37898"]["value"], "Informação")

    @override_settings(JIRA_PROFILE_PROCESSOS_PROJECT_KEY="ANTIFRAUDE")
    def test_processos_no_category(self):
        profile = get_rest_profile("processos")
        fields = build_issue_fields(profile, summary="S", description="D")
        self.assertEqual(fields["project"]["key"], "ANTIFRAUDE")
        self.assertEqual(fields["issuetype"]["name"], "Análise de Compliance")
        self.assertEqual(fields["components"][0]["name"], "Suporte N1 Claro")
        self.assertNotIn("customfield_37898", fields)

    @override_settings(JIRA_PROFILE_PROCESSOS_PROJECT_KEY="ANTIFRAUDE")
    def test_processos_omits_assignee_even_when_lan_passed(self):
        profile = get_rest_profile("processos")
        self.assertFalse(profile.set_assignee_on_create)
        fields = build_issue_fields(
            profile,
            summary="[TESTE] formalização processos",
            description="Chamado de teste — pode ser descartado.",
            assignee_name="c92928a",
        )
        self.assertNotIn("assignee", fields)
        self.assertEqual(fields["project"]["key"], "ANTIFRAUDE")

    @override_settings(
        JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY="PPLID",
        JIRA_CATEGORY_FIELD_ID="",
    )
    def test_planejamento_keeps_assignee_when_lan_passed(self):
        profile = get_rest_profile("planejamento")
        self.assertTrue(profile.set_assignee_on_create)
        fields = build_issue_fields(
            profile,
            summary="[TESTE] formalização planejamento",
            description="Chamado de teste — pode ser descartado.",
            assignee_name="c92928a",
        )
        self.assertEqual(fields["assignee"], {"name": "c92928a"})


class JiraRestClientTests(SimpleTestCase):
    def test_not_configured_without_base(self):
        with override_settings(JIRA_BASE_URL=""):
            self.assertFalse(jira_rest_configured())
            result = create_issue(
                "planejamento",
                summary="a",
                description="b",
                credentials=JiraCredentials(username="u", api_token="t"),
            )
            self.assertFalse(result["ok"])

    def test_missing_credentials(self):
        with override_settings(JIRA_BASE_URL="https://jira.test.local"):
            result = create_issue("planejamento", summary="a", description="b")
            self.assertFalse(result["ok"])
            self.assertIn("Token", result["error"])

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
        JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY="PPLID",
        JIRA_CATEGORY_FIELD_ID="",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_create_issue_success(self, mock_session_cls):
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 201
        resp.headers = {"Content-Type": "application/json"}
        resp.text = '{"key":"PPLID-42"}'
        resp.json.return_value = {"key": "PPLID-42"}
        session.request.return_value = resp

        result = create_issue(
            "planejamento",
            summary="Sum",
            description="Desc",
            credentials=JiraCredentials(username="c93123a", api_token="pat-token"),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["issue_key"], "PPLID-42")
        self.assertEqual(session.headers.get("Authorization"), "Bearer pat-token")
        session.request.assert_called_once()
        _args, kwargs = session.request.call_args
        self.assertEqual(_args[0], "POST")
        self.assertEqual(kwargs.get("allow_redirects"), False)

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
        JIRA_PROFILE_PROCESSOS_PROJECT_KEY="ANTIFRAUDE",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_create_issue_processos_payload_without_assignee(self, mock_session_cls):
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 201
        resp.headers = {"Content-Type": "application/json"}
        resp.text = '{"key":"ANTIFRAUDE-99"}'
        resp.json.return_value = {"key": "ANTIFRAUDE-99"}
        session.request.return_value = resp

        result = create_issue(
            "processos",
            summary="[TESTE] ANTIFRAUDE sem assignee",
            description="Chamado de teste automatizado — descartável.",
            credentials=JiraCredentials(username="c92928a", api_token="pat-token"),
            assignee_name="c92928a",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["issue_key"], "ANTIFRAUDE-99")
        _args, kwargs = session.request.call_args
        payload = kwargs.get("json") or {}
        self.assertNotIn("assignee", payload["fields"])
        self.assertEqual(payload["fields"]["project"]["key"], "ANTIFRAUDE")
        self.assertEqual(payload["fields"]["components"][0]["name"], "Suporte N1 Claro")

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_html_401_message(self, mock_session_cls):
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 401
        resp.headers = {"Content-Type": "text/html"}
        resp.text = "<html><title>Unauthorized (401)</title></html>"
        resp.json.side_effect = ValueError("no json")
        session.request.return_value = resp

        result = create_issue(
            "planejamento",
            summary="Sum",
            description="Desc",
            credentials=JiraCredentials(username="u", api_token="t"),
        )
        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_sso_redirect_does_not_become_405(self, mock_session_cls):
        """302 HTML do gateway não deve virar 'HTTP 405 Method Not Allowed'."""
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 302
        resp.url = "https://jira.test.local/rest/api/2/issue"
        resp.headers = {
            "Content-Type": "text/html",
            "Location": "https://jira.test.local/rest/api/2/issue",
        }
        resp.text = "<HTML><HEAD><TITLE>Loading</TITLE></HEAD></HTML>"
        resp.json.side_effect = ValueError("no json")
        session.request.return_value = resp

        result = create_issue(
            "planejamento",
            summary="Sum",
            description="Desc",
            credentials=JiraCredentials(username="u", api_token="t"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 302)
        self.assertIn("VPN", result["error"])
        self.assertNotIn("405", result["error"])
        self.assertEqual(session.request.call_args.kwargs.get("allow_redirects"), False)

    @override_settings(
        JIRA_BASE_URL="https://jira.test.local",
        JIRA_AUTH_MODE="bearer",
    )
    @patch("apps.suporte_claro.services.jira_rest.requests.Session")
    def test_gateway_405_json_message_is_clarified(self, mock_session_cls):
        session = MagicMock()
        session.headers = {}
        mock_session_cls.return_value = session
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        resp = MagicMock()
        resp.status_code = 405
        resp.headers = {"Content-Type": "application/json"}
        resp.text = '{"message":"HTTP 405 Method Not Allowed","status-code":405}'
        resp.json.return_value = {"message": "HTTP 405 Method Not Allowed", "status-code": 405}
        session.request.return_value = resp

        result = create_issue(
            "planejamento",
            summary="Sum",
            description="Desc",
            credentials=JiraCredentials(username="u", api_token="t"),
        )
        self.assertFalse(result["ok"])
        self.assertIn("VPN", result["error"])
        self.assertIn("405", result["error"])


class JiraCredentialsResolveTests(TestCase):
    def test_resolve_from_agent(self):
        user = User.objects.create_user(username="c93123a", password="x")
        agent = Agent.objects.create(
            full_name="Phillipe",
            user_lan_id="c93123a",
            jira_api_token="secret-pat",
        )
        UserProfile.objects.create(user=user, agent=agent)
        with override_settings(JIRA_BASE_URL="https://jira.test.local"):
            self.assertTrue(user_jira_configured(user))
            creds = resolve_jira_credentials(user)
            self.assertIsNotNone(creds)
            assert creds is not None
            self.assertEqual(creds.username, "c93123a")
            self.assertEqual(creds.api_token, "secret-pat")
