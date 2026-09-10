# -*- coding: utf-8 -*-
"""Testes de .eml sem destinatarios, sidecar e clipboard."""
import re
from email import message_from_bytes
from pathlib import Path

from report_falhas.render.mailer import (
    build_eml_message,
    clipboard_paste_text,
    copy_recipients_to_clipboard,
    format_recipients_for_outlook,
    write_recipients_sidecar,
)


def test_build_eml_message_omits_empty_to_header():
    msg = build_eml_message("<p>ok</p>", "Assunto", "from@experian.com", to_list=[], cc_list=[])
    raw = msg.as_bytes().decode("utf-8", errors="replace")
    assert re.search(r"^To:", raw, re.MULTILINE | re.IGNORECASE) is None
    assert re.search(r"^Cc:", raw, re.MULTILINE | re.IGNORECASE) is None


def test_build_eml_message_includes_to_when_present():
    msg = build_eml_message(
        "<p>ok</p>",
        "Assunto",
        "from@experian.com",
        to_list=["a@experian.com", "b@experian.com"],
        cc_list=["c@experian.com"],
    )
    parsed = message_from_bytes(msg.as_bytes())
    assert "a@experian.com" in parsed["To"]
    assert "b@experian.com" in parsed["To"]
    assert "c@experian.com" in parsed["Cc"]


def test_format_recipients_for_outlook_uses_semicolon():
    text = format_recipients_for_outlook(
        ["a@experian.com", "b@experian.com"],
        ["c@experian.com"],
    )
    assert text == "Para: a@experian.com; b@experian.com\nCc: c@experian.com"


def test_clipboard_paste_text_to_only():
    assert clipboard_paste_text(["a@experian.com", "b@experian.com"], []) == (
        "a@experian.com; b@experian.com"
    )


def test_write_recipients_sidecar(tmp_path):
    path = write_recipients_sidecar(
        tmp_path,
        "Brasilia",
        ["a@experian.com", "b@experian.com"],
        [],
    )
    assert path is not None
    assert path.name == "email_falhas_Brasilia_destinatarios.txt"
    content = path.read_text(encoding="utf-8")
    assert content.startswith("Para: a@experian.com; b@experian.com")


def test_write_recipients_sidecar_returns_none_when_empty(tmp_path):
    assert write_recipients_sidecar(tmp_path, "x", [], []) is None


def test_write_recipients_sidecar_with_cc(tmp_path):
    path = write_recipients_sidecar(
        tmp_path,
        "Brasilia",
        ["a@experian.com"],
        ["cc@experian.com"],
    )
    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "Cc: cc@experian.com" in content
    assert "Anexos:" not in content


def test_copy_recipients_to_clipboard_empty():
    assert copy_recipients_to_clipboard([], []) is False


def test_copy_recipients_to_clipboard_non_windows(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    assert copy_recipients_to_clipboard(["a@experian.com"], []) is False


def test_copy_recipients_to_clipboard_windows(monkeypatch):
    import subprocess
    import sys

    calls = []

    def fake_run(cmd, input=None, text=False, check=False):
        calls.append((cmd, input))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run)

    ok = copy_recipients_to_clipboard(["a@experian.com", "b@experian.com"], [])
    assert ok is True
    assert calls[0][0] == ["clip"]
    assert calls[0][1] == "a@experian.com; b@experian.com"
