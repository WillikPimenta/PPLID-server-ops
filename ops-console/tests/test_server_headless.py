"""Regressoes do servidor quando iniciado sem console via pythonw.exe."""
from __future__ import annotations

from pathlib import Path

import server


def test_request_logging_without_stderr_does_not_break_response(monkeypatch):
    handler = object.__new__(server.OpsConsoleHandler)
    monkeypatch.setattr(server.sys, "stderr", None)

    handler.log_message('"GET / HTTP/1.1" %s', 200)


def test_run_git_hides_child_console_window(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "abc123\n"

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    assert server.run_git(Path(__file__).parent, "rev-parse", "HEAD") == "abc123"
    assert captured["creationflags"] == server._CREATE_NO_WINDOW
