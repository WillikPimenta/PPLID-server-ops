"""Isolamento de cookies/sessão entre ambientes localhost (portas diferentes)."""

from django.conf import settings
from django.test import SimpleTestCase, override_settings


class SessionCookieIsolationTests(SimpleTestCase):
    def test_csrf_cookie_derives_from_session_name(self):
        # settings já carregado: CSRF deve acompanhar SESSION quando não há override total.
        self.assertTrue(settings.SESSION_COOKIE_NAME)
        self.assertTrue(settings.CSRF_COOKIE_NAME)
        self.assertNotEqual(settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME)

    @override_settings(
        SESSION_COOKIE_NAME="pplid_dev_sessionid",
        CSRF_COOKIE_NAME="pplid_dev_csrftoken",
    )
    def test_dev_profile_cookie_names_differ_from_main_defaults(self):
        self.assertEqual(settings.SESSION_COOKIE_NAME, "pplid_dev_sessionid")
        self.assertEqual(settings.CSRF_COOKIE_NAME, "pplid_dev_csrftoken")
        self.assertNotEqual(settings.SESSION_COOKIE_NAME, "pplid_main_sessionid")
