from django.test import SimpleTestCase, override_settings

from apps.suporte_claro.services.jira_rest import (
    JiraCredentials,
    JiraRestError,
    _session,
    jira_proxy_mode,
)


class JiraProxyPolicyTests(SimpleTestCase):
    @override_settings(JIRA_PROXY_URL="", JIRA_TRUST_ENV_PROXY=False)
    def test_environment_proxy_is_disabled_by_default(self):
        session = _session(JiraCredentials("user", "token"))
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies, {})
        self.assertEqual(jira_proxy_mode(), "disabled")

    @override_settings(JIRA_PROXY_URL="http://proxy.example:8080", JIRA_TRUST_ENV_PROXY=False)
    def test_explicit_proxy_is_applied(self):
        session = _session(JiraCredentials("user", "token"))
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies["https"], "http://proxy.example:8080")
        self.assertEqual(jira_proxy_mode(), "explicit")

    @override_settings(JIRA_PROXY_URL="http://127.0.0.1:9", JIRA_TRUST_ENV_PROXY=False)
    def test_loopback_discard_proxy_is_rejected(self):
        with self.assertRaisesMessage(JiraRestError, "loopback na porta 9"):
            _session(JiraCredentials("user", "token"))
