from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

OPS_LIB = Path(__file__).resolve().parents[2] / "lib"
if str(OPS_LIB) not in sys.path:
    sys.path.insert(0, str(OPS_LIB))

import orphan_bot_cleanup as cleanup  # noqa: E402

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server_ops  # noqa: E402


class OrphanBotCleanupTests(unittest.TestCase):
    def test_missing_parent_is_orphan(self) -> None:
        self.assertEqual(cleanup.classify_bot(None, [101], set()), "orphan")

    def test_backend_ancestor_is_managed(self) -> None:
        self.assertEqual(cleanup.classify_bot(200, [101, 200], {200}), "managed")

    def test_summary_is_idempotent_and_json_serializable(self) -> None:
        result = cleanup.build_summary([], mode="cleanup", scanned_at="2026-08-11T00:00:00Z")
        self.assertEqual(result["detected"], 0)
        self.assertEqual(result["stopped"], 0)
        json.dumps(result)

    def test_public_payload_only_exposes_orphans_and_safe_fields(self) -> None:
        payload = server_ops._public_orphan_bot_payload(
            {
                "ok": True,
                "mode": "scan",
                "scannedAt": "2026-08-17T12:00:00Z",
                "items": [
                    {
                        "pid": 101,
                        "classification": "orphan",
                        "environment": "MAIN",
                        "release": "old1234",
                        "activeRelease": "new5678",
                        "mode": "cleanup",
                        "creationDate": "2026-08-17T11:00:00Z",
                        "reason": "release anterior",
                        "result": "preserved",
                        "chain": [101, 50],
                        "commandLine": "secret",
                    },
                    {"pid": 202, "classification": "managed"},
                ],
            }
        )
        self.assertEqual(payload["detected"], 1)
        self.assertEqual(payload["items"][0]["pid"], 101)
        self.assertNotIn("chain", payload["items"][0])
        self.assertNotIn("commandLine", payload["items"][0])

    def test_cleanup_returns_fresh_remaining_orphans(self) -> None:
        cleanup_result = {
            "ok": True,
            "mode": "cleanup",
            "detected": 2,
            "stopped": 2,
            "failed": 0,
            "items": [{"pid": 101, "classification": "orphan", "result": "stopped"}],
        }
        live_result = {"ok": True, "mode": "scan", "detected": 0, "items": []}
        from unittest.mock import patch

        with patch.object(server_ops, "_run_orphan_bot_tool", side_effect=[cleanup_result, live_result]):
            result = server_ops.action_cleanup_orphan_bots({"logDir": "C:/PPLID/logs"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["detectedBefore"], 2)
        self.assertEqual(result["stopped"], 2)
        self.assertEqual(result["items"], [])


if __name__ == "__main__":
    unittest.main()
