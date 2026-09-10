"""Testes DocumentDB helpers (sem cluster real)."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.bots.produtividade_case.mongo import validate_docdb_config, validate_docdb_connection


def test_validate_docdb_config_missing_vars(monkeypatch):
    for key in (
        "DOCDB_HOST",
        "DOCDB_USER",
        "DOCDB_PASSWORD",
        "DOCDB_TLS_CA_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    ok, msg = validate_docdb_config()
    assert ok is False
    assert "DOCDB_HOST" in msg


def test_validate_docdb_config_ok(tmp_path: Path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DOCDB_HOST", "host.example")
    monkeypatch.setenv("DOCDB_USER", "user")
    monkeypatch.setenv("DOCDB_PASSWORD", "secret")
    monkeypatch.setenv("DOCDB_TLS_CA_FILE", str(ca))
    ok, msg = validate_docdb_config()
    assert ok is True
    assert msg == ""


def test_validate_docdb_connection_auth_message(tmp_path: Path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DOCDB_HOST", "host.example")
    monkeypatch.setenv("DOCDB_USER", "user")
    monkeypatch.setenv("DOCDB_PASSWORD", "secret")
    monkeypatch.setenv("DOCDB_TLS_CA_FILE", str(ca))

    client = MagicMock()
    client.admin.command.side_effect = Exception("Authentication failed., code': 18")

    with patch(
        "app.bots.produtividade_case.mongo.MongoClient",
        return_value=client,
    ):
        ok, msg = validate_docdb_connection(timeout_ms=100)
    assert ok is False
    assert "autenticação falhou" in msg.lower()
