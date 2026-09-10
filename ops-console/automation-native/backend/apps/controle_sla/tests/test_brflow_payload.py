from django.test import SimpleTestCase

from apps.controle_sla.exceptions import ControleSlaError, SessionExpiredError
from apps.controle_sla.services.brflow_client import _parse_fila_payload, parse_brflow_datetime


class ParseFilaPayloadTests(SimpleTestCase):
    def test_success_with_data(self):
        rows = _parse_fila_payload({"type": "success", "data": [{"codCliente": 1}]})
        self.assertEqual(len(rows), 1)

    def test_warning_with_data_list(self):
        rows = _parse_fila_payload(
            {
                "type": "warning",
                "description": "aviso",
                "data": [{"codCliente": 186, "nomFluxo": "X"}],
                "extra": {},
            }
        )
        self.assertEqual(rows[0]["codCliente"], 186)

    def test_warning_empty_data_with_redirect(self):
        with self.assertRaises(SessionExpiredError):
            _parse_fila_payload(
                {
                    "type": "warning",
                    "description": "Sessão expirada",
                    "redirectTo": "/BrFlow/login",
                    "data": None,
                }
            )

    def test_warning_login_exception(self):
        with self.assertRaises(SessionExpiredError) as ctx:
            _parse_fila_payload(
                {
                    "type": "warning",
                    "cod": r"BrScan\Exception\LoginException",
                    "description": "Sua sessão expirou.",
                    "redirectTo": "",
                    "data": "",
                }
            )
        self.assertIn("expirou", str(ctx.exception).lower())


class ParseBrflowDatetimeTests(SimpleTestCase):
    def test_br_format_without_seconds(self):
        dt = parse_brflow_datetime("08/08/2025 11:36")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2025)
        self.assertEqual(dt.month, 8)
        self.assertEqual(dt.day, 8)
        self.assertEqual(dt.hour, 11)
        self.assertEqual(dt.minute, 36)

    def test_br_format_with_seconds(self):
        dt = parse_brflow_datetime("22/07/2024 17:00:47")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.second, 47)

    def test_iso_format(self):
        dt = parse_brflow_datetime("2025-08-08 11:36:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.day, 8)
