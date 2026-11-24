import hashlib
import json
import time
import pytest
import server


def test_secret_required(monkeypatch):
    monkeypatch.delenv("OPS_SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        server.get_session_secret()


def test_legacy_session_rejected(monkeypatch):
    monkeypatch.setenv("OPS_SESSION_SECRET", "x" * 40)
    assert server.parse_session_cookie(server.sign_session_payload({"username": "admin1"})) is None


def test_recovery_expiry_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_SESSION_SECRET", "x" * 40)
    monkeypatch.setattr(server, "OPS_REPO_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    path = tmp_path / "data" / "emergency-access.json"
    record = {"id": "test", "expiresAt": time.time() + 60, "passwordHash": hashlib.sha256(b"secret").hexdigest()}
    path.write_text(json.dumps(record))
    assert server.authenticate_unlock({}, "recovery", "secret")["authSource"] == "recovery"
    token = server.sign_session_payload(server.build_session_payload("recovery", "Recovery", "recovery", False, {}))
    assert server.parse_session_cookie(token)
    record["expiresAt"] = time.time() - 1
    path.write_text(json.dumps(record))
    assert server.parse_session_cookie(token) is None
    assert server.try_bootstrap_login({}, "recovery", "secret") is None


def test_main_is_mandatory():
    assert server.resolve_auth_backend({"opsConsole": {"authEnv": "DEV"}, "MAIN": {"apiBaseUrl": "main"}}) == "main"


def test_old_bootstrap_disabled():
    assert server.try_bootstrap_login({"opsConsole": {"bootstrapAuth": {"enabled": True, "username": "admin1", "password": "admin"}}}, "admin1", "admin") is None
