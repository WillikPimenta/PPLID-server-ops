"""Testes do status Cisco via vpncli."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.infrastructure import cisco_vpn as vpn


def test_parse_vpncli_state_connected():
    output = """
Cisco Secure Client
>> state: Disconnected
>> state: Connected
>> notice: Connected to vpn.example.com
"""
    assert vpn.parse_vpncli_state(output) == "connected"


def test_parse_vpncli_state_disconnected():
    output = ">> state: Disconnected\n>> notice: Ready to connect."
    assert vpn.parse_vpncli_state(output) == "disconnected"


def test_parse_vpncli_state_unknown_connecting():
    assert vpn.parse_vpncli_state(">> state: Connecting") == "unknown"


def test_parse_vpncli_state_empty():
    assert vpn.parse_vpncli_state("") == "unknown"


def test_get_vpn_state_exe_missing(monkeypatch):
    monkeypatch.setattr(vpn, "find_vpncli_exe", lambda: None)
    monkeypatch.setattr(vpn.os, "name", "nt")
    result = vpn.get_vpn_state()
    assert result.state == "unknown"
    assert "not found" in result.raw_output


def test_get_vpn_state_connected_via_state(monkeypatch, tmp_path: Path):
    exe = tmp_path / "vpncli.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(vpn, "find_vpncli_exe", lambda: exe)
    monkeypatch.setattr(vpn.os, "name", "nt")

    def fake_run(_exe, arg, _timeout):
        if arg == "state":
            return 0, ">> state: Connected"
        return 1, ""

    monkeypatch.setattr(vpn, "_run_vpncli", fake_run)
    result = vpn.get_vpn_state()
    assert result.state == "connected"
    assert result.command == "state"


def test_get_vpn_state_falls_back_to_status(monkeypatch, tmp_path: Path):
    exe = tmp_path / "vpncli.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(vpn, "find_vpncli_exe", lambda: exe)
    monkeypatch.setattr(vpn.os, "name", "nt")

    def fake_run(_exe, arg, _timeout):
        if arg == "state":
            return 1, ""
        return 0, ">> state: Disconnected"

    monkeypatch.setattr(vpn, "_run_vpncli", fake_run)
    result = vpn.get_vpn_state()
    assert result.state == "disconnected"
    assert result.command == "status"


def test_get_vpn_state_timeout_treated_unknown(monkeypatch, tmp_path: Path):
    exe = tmp_path / "vpncli.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(vpn, "find_vpncli_exe", lambda: exe)
    monkeypatch.setattr(vpn.os, "name", "nt")

    def fake_run(_exe, arg, _timeout):
        return -1, "timeout after 12s"

    monkeypatch.setattr(vpn, "_run_vpncli", fake_run)
    result = vpn.get_vpn_state()
    assert result.state == "unknown"
