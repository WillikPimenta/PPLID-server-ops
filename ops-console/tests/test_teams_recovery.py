import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import server_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = recovery.RecoveryStore(Path(self.temp.name) / "recovery.sqlite3")
        self.request = self.store.create("admin", "127.0.0.1")

    def consume(self, code=None, browser=None):
        return self.store.consume(self.request["id"], browser or self.request["browserToken"], code or self.request["code"])

    def test_main_auth_resolves_configured_backend_port(self):
        import server
        self.assertEqual(server.resolve_auth_backend({"MAIN": {"backendPort": 8000}, "DEV": {"backendPort": 8001}, "opsConsole": {"authEnv": "DEV"}}), "http://127.0.0.1:8000")
        self.assertEqual(server.resolve_auth_backend({"MAIN": {"apiBaseUrl": "https://main.test/", "backendPort": 8000}}), "https://main.test")
        self.assertIsNone(server.resolve_auth_backend({"MAIN": {"backendPort": "invalid"}}))

    def test_browser_binding_and_single_use(self):
        with self.assertRaises(recovery.RecoveryError): self.consume(browser="wrong")
        self.assertEqual(self.consume()["state"], "approved")
        self.assertTrue(self.store.valid_session(self.request["id"]))
        with self.assertRaises(recovery.RecoveryError): self.consume()

    def test_five_failures_persist(self):
        wrong = "00000000" if self.request["code"] != "00000000" else "11111111"
        for attempt in range(5):
            result = self.consume(code=wrong)
            self.assertEqual(result["state"], "locked" if attempt == 4 else "invalid")
        other = recovery.RecoveryStore(self.store.path)
        with self.assertRaises(recovery.RecoveryError):
            other.consume(self.request["id"], self.request["browserToken"], self.request["code"])

    def test_expiry(self):
        with patch.object(recovery.time, "time", return_value=99999999999):
            with self.assertRaises(recovery.RecoveryError): self.consume()

    def test_rate_limit(self):
        with self.assertRaises(recovery.RecoveryError): self.store.create("other", "127.0.0.1")

    def test_cancel_revokes_session(self):
        self.consume()
        self.store.cancel(self.request["id"])
        self.assertFalse(self.store.valid_session(self.request["id"]))

    def test_fixed_session_expiry(self):
        self.consume()
        with patch.object(recovery.time, "time", return_value=99999999999):
            self.assertFalse(self.store.valid_session(self.request["id"]))

    def test_state_survives_restart_without_plaintext_secrets(self):
        with self.store.connect() as db:
            row = dict(db.execute("SELECT * FROM code_requests").fetchone())
        self.assertNotEqual(row["code"], self.request["code"])
        self.assertNotEqual(row["browser"], self.request["browserToken"])
        other = recovery.RecoveryStore(self.store.path)
        self.assertEqual(other.consume(self.request["id"], self.request["browserToken"], self.request["code"])["state"], "approved")

    def test_concurrent_consumption(self):
        def consume(_):
            try: return self.consume()["state"]
            except recovery.RecoveryError: return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(consume, range(2)))
        self.assertEqual(sorted(results), ["approved", "rejected"])

    def test_card_contains_code_but_never_browser_secret(self):
        class Response:
            status = 202
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(recovery.urllib.request, "urlopen", return_value=Response()) as send:
            recovery.send_request("https://example.test", self.request, "admin")
        payload = send.call_args.args[0].data.decode()
        self.assertIn(self.request["code"], payload)
        self.assertNotIn(self.request["browserToken"], payload)
        self.assertNotIn("callbackUrl", json.loads(payload))


if __name__ == "__main__": unittest.main()
