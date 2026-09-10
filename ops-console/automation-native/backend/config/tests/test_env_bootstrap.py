import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from config.env_bootstrap import _apply_env_file, _deploy_roots, bootstrap_environ


class EnvBootstrapTests(SimpleTestCase):
    def test_bootstrap_runs_for_backend_dir(self):
        base_dir = Path(__file__).resolve().parents[1]
        bootstrap_environ(base_dir)

    def test_deploy_roots_includes_default_base(self):
        roots = _deploy_roots()
        self.assertTrue(any(str(root).endswith("PPLID") for root in roots))

    def test_profile_from_release_path(self):
        from config.env_bootstrap import _profile_from_path

        base = Path("C:/PPLID/deploy/DEV/releases/7c179836/backend")
        self.assertEqual(_profile_from_path(base), "DEV")

    def test_apply_env_file_strips_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "backend.env"
            env_file.write_bytes(b"\xef\xbb\xbfPOSTGRES_DB=pplid_main\nPOSTGRES_HOST=127.0.0.1\n")
            with patch.dict(os.environ, {}, clear=True):
                _apply_env_file(env_file, overwrite=True)
                self.assertEqual(os.environ.get("POSTGRES_DB"), "pplid_main")
                self.assertEqual(os.environ.get("POSTGRES_HOST"), "127.0.0.1")

    def test_explicit_deploy_env_overrides_inherited_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "backend.env"
            env_file.write_text("POSTGRES_DB=pplid_dev\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"PPLID_BACKEND_ENV_FILE": str(env_file), "PPLID_ENVIRONMENT": "DEV"}, clear=True),
                patch("config.env_bootstrap._deploy_roots", return_value=[]),
            ):
                bootstrap_environ(Path(tmp))
                self.assertEqual(os.environ.get("POSTGRES_DB"), "pplid_dev")

    def test_inferred_release_env_overrides_inherited_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = root / "deploy" / "DEV" / "releases" / "abc123" / "backend"
            shared_env = root / "deploy" / "DEV" / "shared" / "backend.env"
            shared_env.parent.mkdir(parents=True, exist_ok=True)
            shared_env.write_text("POSTGRES_DB=pplid_dev\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"POSTGRES_DB": "pplid_main", "PPLID_ENVIRONMENT": "DEV"}, clear=True),
                patch("config.env_bootstrap._deploy_roots", return_value=[root]),
            ):
                bootstrap_environ(backend)
                self.assertEqual(os.environ.get("POSTGRES_DB"), "pplid_dev")

    def test_shared_env_is_completed_by_active_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release_backend = root / "deploy" / "DEV" / "releases" / "abc123" / "backend"
            shared_env = root / "deploy" / "DEV" / "shared" / "backend.env"
            active_env = root / "deploy" / "DEV" / "current" / "backend" / ".env"
            shared_env.parent.mkdir(parents=True, exist_ok=True)
            active_env.parent.mkdir(parents=True, exist_ok=True)
            shared_env.write_text("POSTGRES_DB=pplid_dev\nSHARED_ONLY=1\n", encoding="utf-8")
            active_env.write_text("RELEASE_ONLY=1\n", encoding="utf-8")

            with (
                patch.dict(
                    os.environ,
                    {"PPLID_ENVIRONMENT": "DEV", "PPLID_BACKEND_ENV_FILE": ""},
                    clear=True,
                ),
                patch("config.env_bootstrap._deploy_roots", return_value=[root]),
            ):
                bootstrap_environ(release_backend)

                self.assertEqual(os.environ.get("POSTGRES_DB"), "pplid_dev")
                self.assertEqual(os.environ.get("SHARED_ONLY"), "1")
                self.assertEqual(os.environ.get("RELEASE_ONLY"), "1")
