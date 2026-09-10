"""Testes da validação Okta (subprocess Popen, thread em background e cancelamento)."""
from __future__ import annotations

import io
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.bots.bot_okta_validate import _chrome_launch_modes, _prefer_headless
from app.services.robot_manager import OKTA_VALIDATE_TIMEOUT_SECONDS, RobotProcessManager


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=False, name=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def _make_manager_stub(**overrides):
    manager = RobotProcessManager.__new__(RobotProcessManager)
    manager.project_root = MagicMock()
    manager.project_root.__str__ = lambda self: "C:/proj"
    manager.python_bin = MagicMock()
    manager.python_bin.__str__ = lambda self: "python"
    manager.okta_validate_script = MagicMock()
    manager.okta_validate_script.exists.return_value = True
    manager.pids_dir = MagicMock()
    manager._resolve_runner_path = MagicMock(return_value=(MagicMock(), None, None))
    manager._resolve_okta_validate_script = MagicMock(return_value=manager.okta_validate_script)
    manager._clear_credential_env = MagicMock()
    manager._credentials_fingerprint = MagicMock(return_value="fp")
    manager._credentials_validation = {
        "validated": False,
        "message": "",
        "last_checked_at": None,
        "checking": False,
        "fingerprint": None,
    }
    manager._okta_sessions = {"default": manager._credentials_validation}
    manager._okta_active_session = None
    manager._lock = MagicMock()
    manager._lock.__enter__ = MagicMock(return_value=None)
    manager._lock.__exit__ = MagicMock(return_value=False)
    manager._okta_validate_proc = None
    manager._okta_validate_cancel_requested = False
    manager._okta_validate_thread = None
    manager._write_okta_validate_pid = MagicMock()
    manager._clear_okta_validate_pid = MagicMock()
    manager._clear_okta_validate_selenium_pids = MagicMock()
    manager._read_okta_validate_selenium_pids = MagicMock(return_value=[])
    manager._okta_validate_selenium_pids_file = MagicMock(return_value=MagicMock())
    manager._collect_windows_descendants = MagicMock(return_value=[])
    manager._stop_okta_validate_process = MagicMock()
    for key, value in overrides.items():
        setattr(manager, key, value)
    return manager


def _mock_okta_proc(*, returncode: int = 0, stdout: str = "", stderr: str = "", poll_side_effect=None):
    """Popen mock compatível com drenagem de pipes em threads."""
    proc = MagicMock()
    proc.pid = 1234
    proc.returncode = returncode
    if poll_side_effect is None:
        proc.poll.side_effect = [None, returncode]
    else:
        proc.poll.side_effect = poll_side_effect
    proc.wait.return_value = returncode
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO(stderr)
    return proc


def test_prefer_headless_false_by_default(monkeypatch):
    monkeypatch.delenv("OKTA_VALIDATE_HEADLESS", raising=False)
    assert _prefer_headless() is False


def test_prefer_headless_true_when_env_set(monkeypatch):
    monkeypatch.setenv("OKTA_VALIDATE_HEADLESS", "1")
    assert _prefer_headless() is True


def test_chrome_launch_modes_headed_only(monkeypatch):
    monkeypatch.setenv("OKTA_VALIDATE_HEADLESS", "0")
    modes = _chrome_launch_modes()
    assert modes == [("headed-default", None)]
    assert all(arg is None or "headless" not in str(arg) for _, arg in modes)


def test_chrome_launch_modes_headless_first(monkeypatch):
    monkeypatch.setenv("OKTA_VALIDATE_HEADLESS", "1")
    modes = _chrome_launch_modes()
    assert modes[0][0] == "headless-new"
    assert modes[0][1] == "--headless=new"
    assert modes[-1] == ("headed-fallback", None)


def test_parse_okta_validate_output_json_ok_false():
    stdout = json.dumps({"ok": False, "message": "Credenciais inválidas"}, ensure_ascii=False)
    parsed_ok, message = RobotProcessManager._parse_okta_validate_output(stdout, "")
    assert parsed_ok is False
    assert message == "Credenciais inválidas"


def test_parse_okta_validate_output_stderr_fallback():
    stderr = "ModuleNotFoundError: No module named 'app'"
    parsed_ok, message = RobotProcessManager._parse_okta_validate_output("", stderr)
    assert parsed_ok is None
    assert "ModuleNotFoundError" in message


def test_run_okta_credentials_validation_timeout_message():
    manager = _make_manager_stub()
    proc = _mock_okta_proc(poll_side_effect=None)
    proc.poll.return_value = None
    proc.poll.side_effect = None
    proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 1.0)

    monotonic_values = iter([0.0, 1.0, float(OKTA_VALIDATE_TIMEOUT_SECONDS + 1)])

    with patch("app.services.robot_manager.subprocess.Popen", return_value=proc), patch(
        "app.services.robot_manager.time.monotonic", side_effect=lambda: next(monotonic_values)
    ):
        manager._run_okta_credentials_validation("c91123a", "senha", OKTA_VALIDATE_TIMEOUT_SECONDS, True)

    assert manager._credentials_validation["checking"] is False
    assert f"{OKTA_VALIDATE_TIMEOUT_SECONDS}s" in manager._credentials_validation["message"]
    manager._stop_okta_validate_process.assert_called()


def test_run_okta_credentials_validation_surfaces_stderr():
    manager = _make_manager_stub()
    proc = _mock_okta_proc(
        returncode=2,
        stderr="RuntimeError: Falha ao iniciar Chrome visível\n",
    )

    with patch("app.services.robot_manager.subprocess.Popen", return_value=proc):
        manager._run_okta_credentials_validation("c91123a", "senha", OKTA_VALIDATE_TIMEOUT_SECONDS, True)

    message = manager._credentials_validation["message"]
    assert "Chrome visível" in message or "RuntimeError" in message


def test_run_okta_credentials_validation_parses_json_success():
    manager = _make_manager_stub()
    payload = json.dumps({"ok": True, "message": "Credenciais Okta validadas com sucesso"}) + "\n"
    proc = _mock_okta_proc(returncode=0, stdout=payload)

    with patch("app.services.robot_manager.subprocess.Popen", return_value=proc) as popen_mock:
        manager._run_okta_credentials_validation("c91123a", "senha", OKTA_VALIDATE_TIMEOUT_SECONDS, False)

    assert manager._credentials_validation["validated"] is True
    env = popen_mock.call_args.kwargs["env"]
    assert env["OKTA_VALIDATE_HEADLESS"] == "0"
    assert env.get("HEADLESS") is None
    cmd = popen_mock.call_args.args[0]
    assert "-u" in cmd


def test_run_okta_credentials_validation_headless_uses_subprocess():
    manager = _make_manager_stub()
    payload = json.dumps({"ok": True, "message": "Credenciais Okta validadas com sucesso"}) + "\n"
    proc = _mock_okta_proc(returncode=0, stdout=payload)

    with patch("app.services.robot_manager.subprocess.Popen", return_value=proc) as popen_mock:
        manager._run_okta_credentials_validation("c91123a", "senha", OKTA_VALIDATE_TIMEOUT_SECONDS, True)

    env = popen_mock.call_args.kwargs["env"]
    assert env["OKTA_VALIDATE_HEADLESS"] == "1"
    assert env["PYTHONPATH"] == "C:/proj"


def test_start_okta_credentials_validation_rejects_duplicate():
    manager = _make_manager_stub()
    manager._credentials_validation["checking"] = True
    manager._okta_active_session = "default"
    ok, message, _status = manager.start_okta_credentials_validation("c91123a", "senha", headless=True)
    assert ok is False
    assert "andamento" in message.lower()


def test_start_okta_other_session_takes_over_busy_validation():
    manager = _make_manager_stub()
    other = {
        "validated": False,
        "message": "Validando credenciais no Okta...",
        "last_checked_at": None,
        "checking": True,
        "fingerprint": None,
    }
    manager._okta_sessions["session-a"] = other
    manager._okta_active_session = "session-a"
    with patch("app.services.robot_manager.threading.Thread"):
        ok, message, status = manager.start_okta_credentials_validation(
            "c91123a", "senha", headless=True, session_id="session-b"
        )
    assert ok is True
    assert "iniciada" in message.lower()
    assert status["checking"] is True
    assert other["checking"] is False
    assert "assumiu" in status["message"].lower()


def test_validate_okta_credentials_waits_for_background_thread():
    manager = _make_manager_stub()
    payload = json.dumps({"ok": True, "message": "Credenciais Okta validadas com sucesso"}) + "\n"
    proc = _mock_okta_proc(returncode=0, stdout=payload)

    with patch("app.services.robot_manager.threading.Thread", _ImmediateThread), patch(
        "app.services.robot_manager.subprocess.Popen", return_value=proc
    ):
        ok, message, status = manager.validate_okta_credentials("c91123a", "senha", headless=True)

    assert ok is True
    assert status["validated"] is True
    assert "validadas com sucesso" in message


def test_credentials_status_is_isolated_per_session():
    manager = _make_manager_stub()
    manager._okta_sessions["session-a"] = {
        "validated": False,
        "message": "Validando credenciais no Okta...",
        "last_checked_at": None,
        "checking": True,
        "fingerprint": None,
    }
    manager._okta_active_session = "session-a"
    status_a = manager.credentials_status(session_id="session-a")
    status_b = manager.credentials_status(session_id="session-b")
    assert status_a["checking"] is True
    assert status_b["checking"] is False
    assert status_b["validated"] is False


def test_cancel_okta_credentials_validation_stops_process():
    manager = _make_manager_stub()
    proc = MagicMock()
    proc.pid = 999
    proc.poll.return_value = None
    manager._okta_validate_proc = proc
    manager._credentials_validation["checking"] = True
    manager._okta_active_session = "default"
    manager._read_okta_validate_pid = MagicMock(return_value=1234)

    ok, message, status = manager.cancel_okta_credentials_validation(session_id="default")

    assert ok is True
    assert "cancelada" in message.lower()
    manager._stop_okta_validate_process.assert_called_once_with(proc, force=True)
    assert status["checking"] is False


def test_cancel_okta_from_other_session_is_rejected():
    manager = _make_manager_stub()
    proc = MagicMock()
    proc.pid = 999
    proc.poll.return_value = None
    manager._okta_validate_proc = proc
    manager._okta_sessions["session-a"] = {
        "validated": False,
        "message": "Validando...",
        "last_checked_at": None,
        "checking": True,
        "fingerprint": None,
    }
    manager._okta_active_session = "session-a"

    ok, message, status = manager.cancel_okta_credentials_validation(session_id="session-b")

    assert ok is False
    assert "sua em andamento" in message.lower()
    manager._stop_okta_validate_process.assert_not_called()
    assert status["checking"] is False
