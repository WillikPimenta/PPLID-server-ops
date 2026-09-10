"""Normaliza paths de request para metricas (reduz cardinalidade)."""
from __future__ import annotations

import re

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_NUMERIC_ID_RE = re.compile(r"/\d+(?=/|$)")


def normalize_route(path: str) -> str:
    if not path:
        return "/"
    normalized = _UUID_RE.sub("{uuid}", path)
    normalized = _NUMERIC_ID_RE.sub("/{id}", normalized)
    if len(normalized) > 255:
        normalized = normalized[:252] + "..."
    return normalized


def should_skip_path(path: str) -> bool:
    if not path:
        return True
    skip_prefixes = (
        "/static/",
        "/admin/",
        "/api/v1/health/",
        "/api/v1/ops-metrics/",
        "/favicon.ico",
        "/media/",
    )
    return any(path.startswith(prefix) for prefix in skip_prefixes)
