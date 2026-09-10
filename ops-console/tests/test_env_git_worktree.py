"""Testes de deteccao de alteracoes locais nos repositorios de ambiente."""
from __future__ import annotations

import subprocess
from pathlib import Path

import server_ops as so


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")


def test_read_env_git_worktree_status_clean(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    mirror = base / "deploy" / "DEV" / "mirror"
    _init_repo(mirror)

    status = so.read_env_git_worktree_status(base, "DEV", use_cache=False)
    assert status["supported"] is True
    assert status["dirty"] is False
    assert status["reason"] == ""


def test_read_env_git_worktree_status_dirty_mirror(tmp_path: Path):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    mirror = base / "deploy" / "DEV" / "mirror"
    _init_repo(mirror)
    (mirror / "README.md").write_text("changed\n", encoding="utf-8")

    status = so.read_env_git_worktree_status(base, "DEV", use_cache=False)
    assert status["dirty"] is True
    assert "mirror Git" in status["reason"]
    dirty = [loc for loc in status["locations"] if loc["dirty"]]
    assert len(dirty) == 1
    assert dirty[0]["id"] == "mirror"
    assert dirty[0]["changeCount"] == 1


def test_read_env_git_worktree_status_dirty_release(tmp_path: Path, monkeypatch):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    mirror = base / "deploy" / "DEV" / "mirror"
    _init_repo(mirror)

    release = base / "deploy" / "DEV" / "releases" / "abc1234"
    _git(mirror, "worktree", "add", str(release), "HEAD")
    (release / "hotfix.txt").write_text("local\n", encoding="utf-8")

    monkeypatch.setattr(so, "resolve_current_release_dir", lambda _base, _env: release)

    status = so.read_env_git_worktree_status(base, "DEV", use_cache=False)
    assert status["dirty"] is True
    assert "release ativa" in status["reason"]


def test_action_redeploy_blocks_when_worktree_dirty(tmp_path: Path, monkeypatch):
    base = tmp_path / "pplid"
    logs = base / "logs"
    logs.mkdir(parents=True)
    config = {
        "logDir": str(logs),
        "DEV": {"enabled": True, "repoDir": str(base / "repos" / "PPLID_DEV")},
    }

    def fake_worktree(*_args, **_kwargs):
        return {
            "supported": True,
            "dirty": True,
            "reason": "Working tree com alteracoes locais (release ativa). Resolva manualmente antes de atualizar.",
            "locations": [],
        }

    monkeypatch.setattr(so, "read_env_git_worktree_status", fake_worktree)
    result = so.action_redeploy(config, "DEV")
    assert result["ok"] is False
    assert "alteracoes locais" in result["error"]
