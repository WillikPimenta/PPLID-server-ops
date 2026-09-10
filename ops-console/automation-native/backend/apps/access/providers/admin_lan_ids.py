"""Adapter legado: LAN IDs admin via env → adm_portal (não usado em runtime)."""

from __future__ import annotations

from django.conf import settings

from apps.access.constants import ROLE_ADM_PORTAL


def roles_from_admin_lan_ids(lan_id: str) -> set[str]:
    """Descontinuado: perfis admin são atribuídos pela UI RBAC."""
    if not lan_id:
        return set()
    normalized = lan_id.strip().lower()
    env_ids = {
        x.strip().lower()
        for x in getattr(settings, "ESCALA_FLEX_ADMIN_LAN_IDS", []) or []
        if x.strip()
    }
    if normalized in env_ids:
        return {ROLE_ADM_PORTAL}
    return set()
