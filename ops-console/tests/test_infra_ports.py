"""Portas de infraestrutura vs VITE_* derivadas."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server_ops


class InfraPortsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.config_path = self.base / "ops" / "config" / "env.config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.shared = self.base / "deploy" / "DEV" / "shared"
        self.shared.mkdir(parents=True)
        (self.shared / "media").mkdir()
        (self.shared / "backend.env").write_text(
            "POSTGRES_DB=pplid_dev\nSECRET_KEY=x\nDEBUG=True\n",
            encoding="utf-8",
        )
        (self.shared / "frontend.env").write_text(
            "VITE_API_BASE_URL=\nVITE_DEV_SERVER_PORT=5174\n"
            "VITE_BACKEND_PORT=8001\nVITE_BACKEND_PROXY_TARGET=http://localhost:8001\n",
            encoding="utf-8",
        )
        disk = {
            "opsConsolePort": 5190,
            "MAIN": {"backendPort": 8000, "frontendPort": 5173, "repoName": "PPLID_MAIN"},
            "DEV": {
                "backendPort": 8001,
                "frontendPort": 5174,
                "repoName": "PPLID_DEV",
                "repoDir": str(self.base / "repos" / "PPLID_DEV"),
            },
            "HOM": {"backendPort": 8002, "frontendPort": 5175, "repoName": "PPLID_HOM"},
        }
        self.config_path.write_text(json.dumps(disk, indent=2) + "\n", encoding="utf-8")
        self.config = {
            "logDir": str(self.base / "logs"),
            "_configPath": str(self.config_path),
            "opsConsolePort": 5190,
            "MAIN": disk["MAIN"],
            "DEV": dict(disk["DEV"]),
            "HOM": disk["HOM"],
        }
        (self.base / "logs").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_derived_vite_vars(self) -> None:
        derived = server_ops.derived_vite_vars(8001, 5174)
        self.assertEqual(derived["VITE_DEV_SERVER_PORT"], "5174")
        self.assertEqual(derived["VITE_BACKEND_PORT"], "8001")
        self.assertEqual(derived["VITE_BACKEND_PROXY_TARGET"], "http://localhost:8001")

    def test_update_infra_ports_writes_config_and_syncs_vite(self) -> None:
        with mock.patch.object(server_ops, "get_base_dir", return_value=self.base):
            result = server_ops.update_infra_ports(
                self.config,
                "DEV",
                {"backendPort": 8001, "frontendPort": 5188},
                config_path=self.config_path,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["frontendPort"], 5188)
        self.assertEqual(result["previous"]["frontendPort"], 5174)
        disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["DEV"]["frontendPort"], 5188)
        fe = (self.shared / "frontend.env").read_text(encoding="utf-8")
        self.assertIn("VITE_DEV_SERVER_PORT=5188", fe)
        self.assertIn("VITE_BACKEND_PROXY_TARGET=http://localhost:8001", fe)

    def test_update_infra_ports_rejects_collision(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            server_ops.update_infra_ports(
                self.config,
                "DEV",
                {"backendPort": 8000, "frontendPort": 5174},
                config_path=self.config_path,
            )
        self.assertIn("ja em uso", str(ctx.exception))

    def test_update_env_vars_ignores_derived_vite_keys(self) -> None:
        with mock.patch.object(server_ops, "get_base_dir", return_value=self.base):
            server_ops.update_env_vars(
                self.config,
                "DEV",
                {
                    "frontend": {
                        "VITE_DEV_SERVER_PORT": "9999",
                        "VITE_API_BASE_URL": "http://example.test",
                    }
                },
            )
        fe = (self.shared / "frontend.env").read_text(encoding="utf-8")
        self.assertNotIn("VITE_DEV_SERVER_PORT=9999", fe)
        self.assertIn("VITE_API_BASE_URL=http://example.test", fe)


if __name__ == "__main__":
    unittest.main()
