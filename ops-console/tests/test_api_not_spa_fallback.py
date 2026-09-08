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

    def test_host_summary_route_returns_json(self) -> None:
        sent: list[tuple] = []
        handler = server.OpsConsoleHandler.__new__(server.OpsConsoleHandler)
        handler.config = {"logDir": "C:/PPLID/logs"}
        handler._require_unlocked_session = lambda: {"locked": False}  # type: ignore[method-assign]
        handler._send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[method-assign]
        handler.path = "/api/v1/host/summary"
        with patch.object(server.server_host, "build_host_summary", return_value={"hostname": "test"}):
            handler.do_GET()
        self.assertEqual(sent, [({"hostname": "test"}, 200)])

    def test_orphan_bot_scan_route_returns_live_json(self) -> None:
        sent: list[tuple] = []
        handler = server.OpsConsoleHandler.__new__(server.OpsConsoleHandler)
        handler.config = {"logDir": "C:/PPLID/logs"}
        handler._require_unlocked_session = lambda: {"locked": False}  # type: ignore[method-assign]
        handler._send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[method-assign]
        handler.path = "/api/v1/host/orphan-bots"
        payload = {"ok": True, "detected": 1, "items": [{"pid": 101}]}
        with patch.object(server.server_ops, "run_orphan_bot_scan", return_value=payload):
            handler.do_GET()
        self.assertEqual(sent, [(payload, 200)])

    def test_orphan_cleanup_route_requires_unlocked_session(self) -> None:
        sent: list[tuple] = []
        handler = server.OpsConsoleHandler.__new__(server.OpsConsoleHandler)
        handler.config = {"logDir": "C:/PPLID/logs"}
        handler._require_unlocked_session = lambda: None  # type: ignore[method-assign]
        handler._send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[method-assign]
        handler.path = "/api/v1/actions/orphan-bots/cleanup"
        handler.do_POST()
        self.assertEqual(sent[-1][1], 401)

    def test_hourly_productivity_route_returns_json(self) -> None:
        sent: list[tuple] = []
        handler = server.OpsConsoleHandler.__new__(server.OpsConsoleHandler)
        handler.config = {"logDir": "C:/PPLID/logs"}
        handler._require_unlocked_session = lambda: {"locked": False}  # type: ignore[method-assign]
        handler._send_json = lambda payload, status=200: sent.append((payload, status))  # type: ignore[method-assign]
        handler.path = "/api/v1/monitoring/productivity-hourly"

        payload = {"systems": [{"system": "BRFlow"}]}
        with patch.object(server.server_monitoring, "build_hourly_productivity_status", return_value=payload):
            handler.do_GET()

        self.assertEqual(sent, [(payload, 200)])

    def test_monitoring_tv_route_serves_dedicated_page(self) -> None:
        sent: list[tuple[bytes, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            public.mkdir()
            (public / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
            (public / "monitoring-tv.html").write_text("<html>Painel TV</html>", encoding="utf-8")

            handler = server.OpsConsoleHandler.__new__(server.OpsConsoleHandler)
            handler.config = {"logDir": str(Path(tmp) / "logs")}
            handler._require_unlocked_session = lambda: True  # type: ignore[method-assign]
            handler._send_bytes = lambda body, content_type: sent.append((body, content_type))  # type: ignore[method-assign]
            handler.path = "/monitoring/tv"

            with patch.object(server, "PUBLIC_DIR", public):
                handler.do_GET()

        self.assertEqual(sent, [(b"<html>Painel TV</html>", "text/html; charset=utf-8")])


if __name__ == "__main__":
    unittest.main()
