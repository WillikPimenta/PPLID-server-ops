# -*- coding: utf-8 -*-
from __future__ import annotations

from django.conf import settings


def get_env_profile() -> str:
    profile = str(getattr(settings, "PPLID_ENV_PROFILE", "local") or "local").lower()
    if profile not in ("local", "staging", "production"):
        return "local"
    return profile


def adjust_finding_severity(check_id: str, severity: str) -> str:
    """Em ambiente local, achados esperados de DEV não são high."""
    if get_env_profile() != "local":
        return severity
    if check_id in ("CYBER-AUTO-DEBUG", "CYBER-AUTO-SECRET", "CYBER-AUTO-SESSION"):
        if severity == "high":
            return "medium"
    return severity
