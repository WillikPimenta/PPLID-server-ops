import os
from pathlib import Path

import pytest

from app.config import env as env_module


@pytest.fixture(autouse=True)
def reset_env_loader(monkeypatch):
    env_module._LOCAL_ENV_LOADED = False
    for key in ("GED_USER", "GED_PASS", "TEST_ENV_LOADER_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_load_local_env_reads_backend_env(tmp_path, monkeypatch):
    backend_env = tmp_path / "backend" / ".env"
    backend_env.parent.mkdir(parents=True)
    backend_env.write_text(
        "GED_USER=backend-user\nGED_PASS=backend-pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_module, "_ENV_FILE_CANDIDATES", (backend_env,))

    env_module.load_local_env(force=True)

    assert os.environ["GED_USER"] == "backend-user"
    assert os.environ["GED_PASS"] == "backend-pass"


def test_load_local_env_does_not_override_existing(monkeypatch, tmp_path):
    backend_env = tmp_path / "backend" / ".env"
    backend_env.parent.mkdir(parents=True)
    backend_env.write_text("GED_USER=from-file\n", encoding="utf-8")
    monkeypatch.setenv("GED_USER", "from-process")
    monkeypatch.setattr(env_module, "_ENV_FILE_CANDIDATES", (backend_env,))

    env_module.load_local_env(force=True)

    assert os.environ["GED_USER"] == "from-process"


def test_load_local_env_skips_empty_values(monkeypatch, tmp_path):
    backend_env = tmp_path / "backend" / ".env"
    automacoes_env = tmp_path / "automacoes" / ".env"
    backend_env.parent.mkdir(parents=True)
    automacoes_env.parent.mkdir(parents=True)
    backend_env.write_text("TEST_ENV_LOADER_KEY=backend-value\n", encoding="utf-8")
    automacoes_env.write_text("TEST_ENV_LOADER_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(
        env_module,
        "_ENV_FILE_CANDIDATES",
        (backend_env, automacoes_env),
    )

    env_module.load_local_env(force=True)

    assert os.environ["TEST_ENV_LOADER_KEY"] == "backend-value"


def test_reload_prefixed_env_overrides_existing(monkeypatch, tmp_path):
    backend_env = tmp_path / "backend" / ".env"
    backend_env.parent.mkdir(parents=True)
    backend_env.write_text("DOCDB_PASSWORD=from-file\nDOCDB_HOST=h\n", encoding="utf-8")
    monkeypatch.setenv("DOCDB_PASSWORD", "stale")
    monkeypatch.setattr(env_module, "_ENV_FILE_CANDIDATES", (backend_env,))

    n = env_module.reload_prefixed_env("DOCDB_")

    assert n >= 1
    assert os.environ["DOCDB_PASSWORD"] == "from-file"
    assert os.environ["DOCDB_HOST"] == "h"
