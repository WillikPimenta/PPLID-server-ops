from __future__ import annotations

import json
from unittest import mock

from django.test import RequestFactory, SimpleTestCase
from rest_framework.exceptions import ValidationError

from config.api_exceptions import PUBLIC_INTERNAL_ERROR, secure_exception_handler
from config.error_views import page_not_found, server_error


class SecureApiErrorTests(SimpleTestCase):
    def test_unhandled_exception_does_not_expose_internal_details(self):
        secret = "SELECT password FROM users; C:\\Users\\private\\database.py"

        with mock.patch("config.api_exceptions.logger.error"):
            response = secure_exception_handler(RuntimeError(secret), {})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["detail"], PUBLIC_INTERNAL_ERROR)
        self.assertRegex(response.data["incident_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("SELECT", str(response.data))
        self.assertNotIn("C:\\Users", str(response.data))
        self.assertNotIn("password", str(response.data))

    def test_expected_validation_error_is_preserved(self):
        response = secure_exception_handler(
            ValidationError({"field": "Valor inválido."}),
            {},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(str(response.data["field"]), "Valor inválido.")


class SecureDjangoErrorViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_api_404_does_not_echo_requested_path(self):
        request = self.factory.get("/api/private/C:/Users/secret/report.xlsx")

        response = page_not_found(request, RuntimeError("private detail"))
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload, {"detail": "Recurso não encontrado."})
        self.assertNotIn("secret", response.content.decode())

    def test_api_500_returns_only_generic_message_and_incident_id(self):
        request = self.factory.get("/api/private/internal-path")

        response = server_error(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["detail"], PUBLIC_INTERNAL_ERROR)
        self.assertRegex(payload["incident_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("internal-path", response.content.decode())
