"""Garante que rotas /api/* nao retornam HTML do SPA."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server  # noqa: E402


class ApiFallbackTests(unittest.TestCase):
    def test_unknown_api_returns_json_not_html(self) -> None:
        sent: list[tuple] = []

        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            public.mkdir()
            (public / "index.html").write_text("<!DOCTYPE html><html></html>", encoding="utf-8")

            handler = server.OpsConsoleHandler.__new__(server.OpsConsoleHandler)
            handler.config = {"logDir": str(Path(tmp) / "logs")}
            handler._require_unlocked_session = lambda: True  # type: ignore[method-assign]

            def capture_json(payload, status=200):
                sent.append((payload, status))

            handler._send_json = capture_json  # type: ignore[method-assign]
            handler.path = "/api/v1/rota-inexistente"

            with patch.object(server, "PUBLIC_DIR", public):
                handler.do_GET()

        self.assertTrue(sent)
        payload, status = sent[-1]
        self.assertEqual(status, 404)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
