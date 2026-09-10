"""Testes do bot OneDrive (verificação e recuperação via PowerShell)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app.bots.bot_onedrive as bot_onedrive


@pytest.fixture(autouse=True)
def _reset_parar_event():
    bot_onedrive.parar_event.clear()
    yield
    bot_onedrive.parar_event.set()


def test_check_interval_default_1800(monkeypatch):
    monkeypatch.delenv("ONEDRIVE_CHECK_INTERVAL_SECONDS", raising=False)
    assert bot_onedrive._check_interval_seconds() == 1800


def test_check_interval_from_env(monkeypatch):
    monkeypatch.setenv("ONEDRIVE_CHECK_INTERVAL_SECONDS", "900")
    assert bot_onedrive._check_interval_seconds() == 900


def test_verificar_status_onedrive_ok(monkeypatch):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")

    def fake_run(script_path, timeout):
        return 0, "STATUS FINAL: OK", ""

    monkeypatch.setattr(bot_onedrive, "_run_powershell_script", fake_run)

    ok, exit_code, output = bot_onedrive.verificar_status_onedrive()
    assert ok is True
    assert exit_code == 0
    assert "OK" in output


def test_verificar_status_onedrive_dessync(monkeypatch):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")

    def fake_run(script_path, timeout):
        return 2, "STATUS FINAL: Possível dessincronização detectada.", ""

    monkeypatch.setattr(bot_onedrive, "_run_powershell_script", fake_run)

    ok, exit_code, _output = bot_onedrive.verificar_status_onedrive()
    assert ok is False
    assert exit_code == 2


def test_is_dessync_output_detects_status_final():
    output = "STATUS FINAL: Possível dessincronização detectada."
    assert bot_onedrive._is_dessync_output(output) is True


def test_extract_dessync_reasons():
    output = """
Motivos de dessincronizacao:
 - Conta OneDrive nao autenticada (ClientNotSignedInBalloonState=1)
 - Conta corporativa desvinculada: UserTriggeredUnlink
STATUS FINAL: Possivel dessincronizacao detectada.
"""
    reasons = bot_onedrive._extract_dessync_reasons(output)
    assert len(reasons) == 2
    assert "ClientNotSignedInBalloonState" in reasons[0]


def test_recuperar_sessao_onedrive_success(monkeypatch):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")

    def fake_run(script_path, timeout):
        return 0, "Processo concluído.", ""

    monkeypatch.setattr(bot_onedrive, "_run_powershell_script", fake_run)

    success, exit_code, output = bot_onedrive.recuperar_sessao_onedrive()
    assert success is True
    assert exit_code == 0
    assert "concluído" in output


def test_run_powershell_script_non_windows():
    with patch.object(bot_onedrive.os, "name", "posix"):
        exit_code, stdout, stderr = bot_onedrive._run_powershell_script(Path("x.ps1"), 60)
    assert exit_code == -1
    assert stdout == ""
    assert "Windows" in stderr


def test_run_powershell_script_missing_file():
    with patch.object(bot_onedrive.os, "name", "nt"):
        exit_code, _stdout, stderr = bot_onedrive._run_powershell_script(Path("inexistente.ps1"), 60)
    assert exit_code == -1
    assert "não encontrado" in stderr


def test_decode_process_output_cp1252():
    raw = "Possível dessincronização".encode("cp1252")
    decoded = bot_onedrive._decode_process_output(raw)
    assert "Poss" in decoded
    assert "dessincroniza" in decoded


def test_decode_process_output_invalid_bytes():
    raw = b"\xff\xfe\xc7\x00"
    decoded = bot_onedrive._decode_process_output(raw)
    assert isinstance(decoded, str)
    assert decoded


def test_run_cycle_ok_nao_chama_recuperacao(monkeypatch):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")

    verify_mock = MagicMock(return_value=(True, 0, "ok"))
    recover_mock = MagicMock()
    monkeypatch.setattr(bot_onedrive, "verificar_status_onedrive", verify_mock)
    monkeypatch.setattr(bot_onedrive, "recuperar_sessao_onedrive", recover_mock)

    assert bot_onedrive._run_cycle() is True
    verify_mock.assert_called_once()
    recover_mock.assert_not_called()


def test_run_cycle_dessync_chama_recuperacao_e_revalida(monkeypatch):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")
    monkeypatch.setattr(bot_onedrive, "_POST_RECOVER_WAIT_SECONDS", 0)

    verify_mock = MagicMock(
        side_effect=[
            (False, 2, "dessincronizado"),
            (True, 0, "ok"),
        ]
    )
    recover_mock = MagicMock(return_value=(True, 0, "recuperado"))
    monkeypatch.setattr(bot_onedrive, "verificar_status_onedrive", verify_mock)
    monkeypatch.setattr(bot_onedrive, "recuperar_sessao_onedrive", recover_mock)

    assert bot_onedrive._run_cycle() is True
    assert verify_mock.call_count == 2
    recover_mock.assert_called_once()


def test_validate_scripts_falha_quando_ausente(monkeypatch, tmp_path):
    missing = tmp_path / "missing.ps1"
    existing = tmp_path / "existing.ps1"
    existing.write_text("", encoding="utf-8")

    monkeypatch.setattr(bot_onedrive, "_SCRIPT_VERIFICAR", missing)
    monkeypatch.setattr(bot_onedrive, "_SCRIPT_RECUPERAR", existing)

    assert bot_onedrive._validate_scripts() is False


def test_run_loop_nao_windows(monkeypatch):
    monkeypatch.setattr(bot_onedrive.os, "name", "posix")
    statuses: list[str] = []
    monkeypatch.setattr(bot_onedrive, "_emit_status", lambda msg: statuses.append(msg))

    bot_onedrive._run_loop()

    assert any("Windows" in msg for msg in statuses)


def test_run_loop_encerra_quando_scripts_ausentes(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")
    missing = tmp_path / "missing.ps1"
    monkeypatch.setattr(bot_onedrive, "_SCRIPT_VERIFICAR", missing)
    monkeypatch.setattr(bot_onedrive, "_SCRIPT_RECUPERAR", missing)

    statuses: list[str] = []
    monkeypatch.setattr(bot_onedrive, "_emit_status", lambda msg: statuses.append(msg))

    bot_onedrive._run_loop()

    assert any("scripts ausentes" in msg.lower() for msg in statuses)


def test_run_loop_executa_ciclo_e_aguarda_intervalo(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_onedrive.os, "name", "nt")
    script = tmp_path / "script.ps1"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(bot_onedrive, "_SCRIPT_VERIFICAR", script)
    monkeypatch.setattr(bot_onedrive, "_SCRIPT_RECUPERAR", script)
    monkeypatch.setenv("ONEDRIVE_CHECK_INTERVAL_SECONDS", "1800")

    cycle_mock = MagicMock(return_value=True)
    wait_mock = MagicMock(side_effect=[True])
    monkeypatch.setattr(bot_onedrive, "_run_cycle", cycle_mock)
    monkeypatch.setattr(bot_onedrive.parar_event, "wait", wait_mock)

    bot_onedrive._run_loop()

    cycle_mock.assert_called_once()
    wait_mock.assert_called_once_with(1800)
