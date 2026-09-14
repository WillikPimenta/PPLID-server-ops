from __future__ import annotations

import server


def test_tv_auth_heartbeat_is_public():
    assert "/api/v1/auth/heartbeat" in server.AUTH_PUBLIC_PATHS


def test_heartbeat_renews_unlocked_session(monkeypatch):
    config = {"opsConsole": {"sessionHours": 1}}
    session = {
        "username": "tv-user",
        "displayName": "TV User",
        "authSource": "bootstrap",
        "locked": False,
        "exp": 1,
    }
    refreshed = server.build_session_payload(
        session["username"],
        session["displayName"],
        session["authSource"],
        locked=False,
        config=config,
    )
    assert refreshed["exp"] > session["exp"]
    assert refreshed["locked"] is False
