"""Health endpoints: liveness (O(1)), readiness (DB ping), deep (async/cached)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from django.db import connection
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

_REPO_ENV_MAP = {
    "PPLID_DEV": "DEV",
    "PPLID_MAIN": "MAIN",
    "PPLID_HOM": "HOM",
}

# Static metadata — loaded once (or on long TTL), never on every probe.
_STATIC_TTL_SEC = 300.0
_static_lock = threading.Lock()
_static_cache: dict[str, object] = {
    "version": "unknown",
    "has_falhas": False,
    "checked_at": 0.0,
}

# Deep migrations diagnostics — never on the hot readiness path.
_MIGRATIONS_CACHE_TTL_SEC = 300.0
_migrations_lock = threading.Lock()
_migrations_inflight = False
_migrations_cache: dict[str, object] = {
    "status": "unknown",
    "checked_at": 0.0,
    "pending": False,
}

# Short DB ping deadline for readiness.
_DB_STATEMENT_TIMEOUT_MS = 800


def _machine_base_dir() -> Path:
    machine_config = Path("C:/PPLID/machine.config.json")
    if machine_config.exists():
        try:
            data = json.loads(machine_config.read_text(encoding="utf-8"))
            return Path(data.get("baseDir") or "C:/PPLID")
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return Path("C:/PPLID")


def _resolve_env_name() -> str | None:
    env = (os.environ.get("PPLID_ENVIRONMENT") or "").strip().upper()
    if env in _REPO_ENV_MAP.values():
        return env
    repo_root = Path(__file__).resolve().parent.parent.parent
    return _REPO_ENV_MAP.get(repo_root.name)


def _sha_from_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        version = str(data.get("sha") or "").strip()
        return version or None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _deployed_sha_candidates() -> list[Path]:
    candidates: list[Path] = []

    env_file = os.environ.get("PPLID_DEPLOYED_SHA_FILE")
    if env_file:
        candidates.append(Path(env_file))

    env_name = _resolve_env_name()
    base_dir = _machine_base_dir()
    if env_name:
        candidates.append(base_dir / "logs" / f"PPLID_{env_name}.deployed.json")

    repo_root = Path(__file__).resolve().parent.parent.parent
    mapped_env = _REPO_ENV_MAP.get(repo_root.name)
    if mapped_env and mapped_env != env_name:
        candidates.append(base_dir / "logs" / f"PPLID_{mapped_env}.deployed.json")

    backend_dir = Path(__file__).resolve().parent.parent
    candidates.append(backend_dir.parent / "meta.json")

    return candidates


def _compute_deployed_version() -> str:
    for path in _deployed_sha_candidates():
        version = _sha_from_file(path)
        if version:
            return version
    return "unknown"


def _compute_has_falhas() -> bool:
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "apps" / "falhas_criticas").is_dir()


def _get_static_meta(*, force: bool = False) -> tuple[str, bool]:
    now = time.monotonic()
    with _static_lock:
        age = now - float(_static_cache["checked_at"] or 0.0)
        if not force and _static_cache["checked_at"] and age < _STATIC_TTL_SEC:
            return str(_static_cache["version"]), bool(_static_cache["has_falhas"])

    version = _compute_deployed_version()
    has_falhas = _compute_has_falhas()
    with _static_lock:
        _static_cache["version"] = version
        _static_cache["has_falhas"] = has_falhas
        _static_cache["checked_at"] = time.monotonic()
    return version, has_falhas


def _migrations_pending_uncached() -> str:
    try:
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("showmigrations", "--plan", stdout=out, no_color=True)
        output = out.getvalue()
        if "[ ]" in output:
            return "pending"
        return "ok"
    except Exception:
        return "unknown"


def _refresh_migrations_async() -> None:
    global _migrations_inflight
    try:
        status = _migrations_pending_uncached()
        with _migrations_lock:
            _migrations_cache["status"] = status
            _migrations_cache["pending"] = status == "pending"
            _migrations_cache["checked_at"] = time.monotonic()
    finally:
        with _migrations_lock:
            _migrations_inflight = False


def _cached_migrations_status(*, start_refresh: bool = False) -> str:
    """Return cached migrations status; optionally kick off a background refresh."""
    global _migrations_inflight
    now = time.monotonic()
    with _migrations_lock:
        age = now - float(_migrations_cache["checked_at"] or 0.0)
        cached = str(_migrations_cache["status"] or "unknown")
        has_cache = bool(_migrations_cache["checked_at"])
        stale = (not has_cache) or age >= _MIGRATIONS_CACHE_TTL_SEC
        if start_refresh and stale and not _migrations_inflight:
            _migrations_inflight = True
            threading.Thread(target=_refresh_migrations_async, daemon=True, name="health-migrations").start()
        if has_cache:
            return cached
        # Never block the hot path waiting for showmigrations.
        return "ok"


def _ping_database() -> str:
    """Lightweight readiness check — SELECT 1 with short statement timeout when possible."""
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            vendor = getattr(connection, "vendor", "") or ""
            if vendor == "postgresql":
                try:
                    cursor.execute(f"SET LOCAL statement_timeout = {_DB_STATEMENT_TIMEOUT_MS}")
                except Exception:
                    pass
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return "ok"
    except Exception:
        return "error"


def _build_components(
    *,
    db_status: str,
    version: str,
    migrations_status: str,
    has_falhas: bool,
) -> dict[str, str]:
    components = {
        "backend": "ok" if db_status == "ok" else "fail",
        "database": db_status if db_status == "ok" else "error",
        "migrations": migrations_status,
        "version": "ok" if version != "unknown" else "warn",
    }
    if has_falhas:
        components["falhas"] = "ok"
    return components


def _readiness_response(*, deep_compat: bool = False) -> JsonResponse:
    """
    Readiness payload compatible with existing /api/v1/health/ consumers.
    Never runs showmigrations synchronously.
    ?deep=1 returns/starts a cached snapshot asynchronously (compat only).
    """
    db_status = _ping_database()
    version, has_falhas = _get_static_meta()
    migrations_status = (
        _cached_migrations_status(start_refresh=deep_compat)
        if db_status == "ok"
        else "unknown"
    )

    components = _build_components(
        db_status=db_status,
        version=version,
        migrations_status=migrations_status,
        has_falhas=has_falhas,
    )

    if db_status != "ok":
        status = "unhealthy"
        http_status = 503
    elif migrations_status == "pending" or version == "unknown":
        status = "degraded"
        http_status = 200
    else:
        status = "healthy"
        http_status = 200

    payload: dict[str, object] = {
        "status": status,
        "database": db_status,
        "version": version,
        "components": components,
    }
    if deep_compat:
        with _migrations_lock:
            payload["diagnostics"] = {
                "migrations": {
                    "status": _migrations_cache["status"],
                    "checkedAt": _migrations_cache["checked_at"],
                    "inflight": _migrations_inflight,
                    "source": "cache_or_async",
                }
            }

    return JsonResponse(payload, status=http_status)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_live(request):
    """Liveness: process is up. O(1), no DB, no filesystem, no subprocess."""
    return JsonResponse({"status": "alive", "ok": True}, status=200)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_ready(request):
    """Readiness alias — same contract as /api/v1/health/."""
    return _readiness_response(deep_compat=False)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """
    Canonical readiness endpoint used by deploy scripts and OPS-console.
    ?deep=1 never blocks on showmigrations; returns/starts async cache only.
    """
    deep = str(request.GET.get("deep") or "").strip().lower() in ("1", "true", "yes")
    return _readiness_response(deep_compat=deep)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_deep(request):
    """
    Explicit deep diagnostics. Returns cached migrations status and may start
    a background refresh — never blocks a Waitress worker on showmigrations.
    """
    version, has_falhas = _get_static_meta()
    migrations_status = _cached_migrations_status(start_refresh=True)
    with _migrations_lock:
        age_ms = None
        if _migrations_cache["checked_at"]:
            age_ms = int((time.monotonic() - float(_migrations_cache["checked_at"])) * 1000)
        mig = {
            "status": _migrations_cache["status"],
            "ageMs": age_ms,
            "inflight": _migrations_inflight,
            "ttlSec": _MIGRATIONS_CACHE_TTL_SEC,
        }
    return JsonResponse(
        {
            "status": "ok",
            "version": version,
            "hasFalhasModule": has_falhas,
            "migrations": mig,
            "cachedMigrationsStatus": migrations_status,
        },
        status=200,
    )
