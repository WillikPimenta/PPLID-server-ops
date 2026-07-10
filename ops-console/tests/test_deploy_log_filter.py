"""Testes do filtro temporal de logs por etapa e scroll inteligente."""
from __future__ import annotations

from deploy_log_filter import (
    classify_plain_log_line,
    filter_logs_for_step_group,
    is_history_row_active,
    should_follow_log_scroll,
)


STEPS = [
    {
        "id": "prepare",
        "logFile": "pipeline.log",
        "startedAt": "2026-07-08T09:41:40-03:00",
        "finishedAt": "2026-07-08T09:41:47-03:00",
    },
    {
        "id": "git_fetch",
        "logFile": "build.log",
        "startedAt": "2026-07-08T09:41:48-03:00",
        "finishedAt": "2026-07-08T09:42:12-03:00",
    },
    {
        "id": "deps_backend",
        "logFile": "build.log",
        "startedAt": "2026-07-08T09:42:16-03:00",
        "finishedAt": None,
        "status": "running",
    },
    {
        "id": "build_backend",
        "logFile": "build.log",
        "startedAt": None,
        "finishedAt": None,
        "status": "pending",
    },
]

LOG_LINES = [
    {"time": "2026-07-08 09:41:45", "text": "Preparacao concluida"},
    {"time": "2026-07-08 09:41:59", "text": "Iniciando git fetch..."},
    {"time": "2026-07-08 09:42:11", "text": "git fetch/pull concluido"},
    {"time": "2026-07-08 09:42:14", "text": "Worktree criado em C:\\PPLID\\deploy\\DEV\\releases\\7c2d5f3"},
    {"time": "2026-07-08 09:42:17", "text": "Criando venv..."},
    {"time": "2026-07-08 09:42:20", "text": "pip_requirements: instalando dependencias do backend..."},
    {"time": "2026-07-08 09:42:25", "text": "Collecting django==4.2"},
]


def test_checkout_excludes_deps_logs():
    checkout_steps = [s for s in STEPS if s["id"] in ("prepare", "git_fetch")]
    filtered = filter_logs_for_step_group(LOG_LINES, checkout_steps, STEPS)
    texts = [row["text"] for row in filtered or []]
    assert "Iniciando git fetch..." in texts
    assert "Worktree criado em C:\\PPLID\\deploy\\DEV\\releases\\7c2d5f3" in texts
    assert "Collecting django==4.2" not in texts
    assert "Criando venv..." not in texts


def test_deps_shows_only_deps_window():
    deps_steps = [s for s in STEPS if s["id"] == "deps_backend"]
    filtered = filter_logs_for_step_group(LOG_LINES, deps_steps, STEPS)
    texts = [row["text"] for row in filtered or []]
    assert "Collecting django==4.2" in texts
    assert "Criando venv..." in texts
    assert "Iniciando git fetch..." not in texts
    assert "Worktree criado em C:\\PPLID\\deploy\\DEV\\releases\\7c2d5f3" not in texts


def test_bridge_line_goes_to_checkout_not_deps():
    deps_steps = [s for s in STEPS if s["id"] == "deps_backend"]
    filtered = filter_logs_for_step_group(LOG_LINES, deps_steps, STEPS)
    texts = [row["text"] for row in filtered or []]
    assert not any("Worktree criado" in t for t in texts)


def test_pending_step_returns_null():
    build_steps = [STEPS[3]]
    result = filter_logs_for_step_group(LOG_LINES, build_steps, STEPS)
    assert result is None


def test_smart_scroll_follows_when_at_bottom():
    assert should_follow_log_scroll(True, 900, 1000, 100) is True
    assert should_follow_log_scroll(None, 0, 1000, 100) is True


def test_smart_scroll_pauses_when_scrolled_up():
    assert should_follow_log_scroll(False, 100, 1000, 100) is False


def test_smart_scroll_pauses_when_user_locked():
    assert should_follow_log_scroll(True, 865, 1000, 100, user_locked=True) is False
    assert should_follow_log_scroll(True, 865, 1000, 100, interacting=True) is False


def test_smart_scroll_resumes_near_bottom():
    assert should_follow_log_scroll(True, 865, 1000, 100) is True


def test_classify_plain_log_line_levels():
    assert classify_plain_log_line("[2026-01-01 00:00:00] [ERROR] pip falhou")["level"] == "ERROR"
    assert classify_plain_log_line(" backend/apps/foo.py | 12 +")["level"] == "OK"
    assert classify_plain_log_line(" backend/apps/bar.py | 3 -")["level"] == "WARN"


def test_active_badge_only_for_successful_matching_sha():
    env = {"deployedSha": "7c2d5f3", "phase": "healthy", "lastDeployResult": "success"}
    success_row = {"sha": "7c2d5f3", "statusKey": "success", "result": "success", "isRunning": False}
    failed_row = {"sha": "7c2d5f3", "statusKey": "failed", "result": "failed", "isRunning": False}
    assert is_history_row_active(success_row, env) is True
    assert is_history_row_active(failed_row, env) is False
