"""Single source for credential resolution across bots and orchestration."""

from __future__ import annotations

import os
from typing import Optional, Tuple

from app.config.env import load_local_env


def get_credentials(username: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Resolve credentials from explicit username or environment variables."""
    load_local_env()
    if not username:
        username = (
            os.getenv("OKTA_USER")
            or os.getenv("MONITOR_USER")
            or os.getenv("NIVEL_USER")
        )

    password = (
        os.getenv("OKTA_PASS")
        or os.getenv("MONITOR_PASS")
        or os.getenv("NIVEL_PASS")
    )
    if username and password:
        return username, password
    return None, None


def set_credentials(username: str, password: str) -> bool:
    """Store credentials in Config (lazy import to avoid cycles)."""
    from app.config.settings import Config

    Config.set_credentials(username, password)
    return bool(username and password)


def delete_credentials(username: str) -> bool:
    """Clear stored credentials."""
    from app.config.settings import Config

    Config.set_credentials("", "")
    return True


def get_ged_credentials() -> Tuple[str, str]:
    """Resolve GED portal login credentials from GED_USER / GED_PASS in .env."""
    load_local_env()
    user = (os.getenv("GED_USER") or "").strip()
    password = (os.getenv("GED_PASS") or "").strip()
    return user, password
