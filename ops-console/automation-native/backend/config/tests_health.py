"""Django health endpoint tests (liveness / readiness / deep).

Run from backend dir:
  python -m pytest config/tests_health.py -q
  # or
  python config/tests_health.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import RequestFactory, SimpleTestCase  # noqa: E402

from config import health as health_mod  # noqa: E402


class HealthEndpointTests(SimpleTestCase):
    allow_database_queries = True

    def setUp(self) -> None:
        self.rf = RequestFactory()
        health_mod._static_cache.update(
            {"version": "deadbeef", "has_falhas": False, "checked_at": 1.0}
        )
        health_mod._migrations_cache.update(
            {"status": "ok", "checked_at": 1.0, "pending": False}
        )
        health_mod._migrations_inflight = False

    def test_liveness_no_db(self) -> None:
        with mock.patch.object(health_mod, "_ping_database") as ping:
            with mock.patch.object(health_mod, "_get_static_meta") as meta:
                req = self.rf.get("/api/v1/health/live/")
                resp = health_mod.health_live(req)
        self.assertEqual(resp.status_code, 200)
        ping.assert_not_called()
        meta.assert_not_called()

    def test_liveness_no_repeated_filesystem(self) -> None:
        with mock.patch.object(health_mod, "_compute_deployed_version") as ver:
            with mock.patch.object(health_mod, "_compute_has_falhas") as falhas:
                req = self.rf.get("/api/v1/health/live/")
                health_mod.health_live(req)
                health_mod.health_live(req)
        ver.assert_not_called()
        falhas.assert_not_called()

    def test_readiness_healthy(self) -> None:
        with mock.patch.object(health_mod, "_ping_database", return_value="ok"):
            with mock.patch.object(
                health_mod, "_get_static_meta", return_value=("abc1234", False)
            ):
                with mock.patch.object(
                    health_mod, "_cached_migrations_status", return_value="ok"
                ):
                    with mock.patch.object(
                        health_mod, "_migrations_pending_uncached"
                    ) as heavy:
                        req = self.rf.get("/api/v1/health/")
                        resp = health_mod.health_check(req)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("healthy", body)
        self.assertIn("abc1234", body)
        heavy.assert_not_called()

    def test_readiness_db_down(self) -> None:
        with mock.patch.object(health_mod, "_ping_database", return_value="error"):
            with mock.patch.object(
                health_mod, "_get_static_meta", return_value=("abc1234", False)
            ):
                req = self.rf.get("/api/v1/health/")
                resp = health_mod.health_check(req)
        self.assertEqual(resp.status_code, 503)
        self.assertIn("unhealthy", resp.content.decode())

    def test_health_never_calls_showmigrations(self) -> None:
        with mock.patch.object(health_mod, "_ping_database", return_value="ok"):
            with mock.patch.object(
                health_mod, "_get_static_meta", return_value=("abc", False)
            ):
                with mock.patch("django.core.management.call_command") as cc:
                    with mock.patch("threading.Thread") as thr:
                        health_mod.health_check(self.rf.get("/api/v1/health/"))
        cc.assert_not_called()
        thr.assert_not_called()

    def test_deep_compat_returns_diagnostics_without_sync_showmigrations(self) -> None:
        with mock.patch.object(health_mod, "_ping_database", return_value="ok"):
            with mock.patch.object(
                health_mod, "_get_static_meta", return_value=("abc", False)
            ):
                with mock.patch("django.core.management.call_command") as cc:
                    with mock.patch("threading.Thread") as thr:
                        thr.return_value = mock.Mock()
                        health_mod._migrations_cache["checked_at"] = 0.0
                        health_mod._migrations_inflight = False
                        resp = health_mod.health_check(
                            self.rf.get("/api/v1/health/?deep=1")
                        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content.decode())
        self.assertIn("diagnostics", data)
        # Must not block the request thread on showmigrations.
        cc.assert_not_called()
        # May schedule async refresh when cache is stale.
        thr.assert_called()
        thr.return_value.start.assert_called()

    def test_ready_alias(self) -> None:
        with mock.patch.object(health_mod, "_ping_database", return_value="ok"):
            with mock.patch.object(
                health_mod, "_get_static_meta", return_value=("v", False)
            ):
                with mock.patch.object(
                    health_mod, "_cached_migrations_status", return_value="ok"
                ):
                    resp = health_mod.health_ready(self.rf.get("/api/v1/health/ready/"))
        self.assertEqual(resp.status_code, 200)

    def test_payload_compatibility(self) -> None:
        with mock.patch.object(health_mod, "_ping_database", return_value="ok"):
            with mock.patch.object(
                health_mod, "_get_static_meta", return_value=("sha", True)
            ):
                with mock.patch.object(
                    health_mod, "_cached_migrations_status", return_value="ok"
                ):
                    resp = health_mod.health_check(self.rf.get("/api/v1/health/"))
        data = json.loads(resp.content.decode())
        for key in ("status", "database", "version", "components"):
            self.assertIn(key, data)
        self.assertIn("falhas", data["components"])

    def test_deep_endpoint(self) -> None:
        with mock.patch.object(
            health_mod, "_get_static_meta", return_value=("sha", False)
        ):
            with mock.patch.object(
                health_mod, "_cached_migrations_status", return_value="ok"
            ) as mig:
                resp = health_mod.health_deep(self.rf.get("/api/v1/health/deep/"))
        self.assertEqual(resp.status_code, 200)
        mig.assert_called()
        data = json.loads(resp.content.decode())
        self.assertIn("migrations", data)


if __name__ == "__main__":
    unittest.main()
