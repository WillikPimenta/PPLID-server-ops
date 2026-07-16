"""
Coleta e APIs de monitoramento do ops-console.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import server_db
import server_ops

ENV_ORDER = ("MAIN", "DEV", "HOM")

_DEFAULT_MONITORING = {
    "retentionDays": 7,
    "collectorIntervalSec": 60,
    "pgSnapshotIntervalSec": 300,
    "defaultEnvs": list(ENV_ORDER),
    "slowRequestMs": 2000,
    "slos": {
        "healthP95WarnMs": 2000,
        "healthP95CriticalMs": 3000,
        "uptimeWarnPct": 99.0,
        "syncFailuresWarn24h": 1,
        "deployFailureRateWarnPct": 30.0,
    },
    "enabledCategories": {
        "api": True,
        "availability": True,
        "postgres": True,
        "syncs": True,
        "deploy": True,
        "logs": True,
    },
}

_COLLECTOR_THREAD: threading.Thread | None = None
_COLLECTOR_WATCHDOG_THREAD: threading.Thread | None = None
_COLLECTOR_STOP = threading.Event()
_COLLECTOR_CONFIG: dict[str, Any] | None = None
_LAST_PG_SNAPSHOT: dict[str, float] = {}
_LAST_API_SNAPSHOT: dict[str, float] = {}
_LAST_PURGE_AT = 0.0
_SERVICE_LOG_OFFSETS: dict[str, int] = {}
_CONFIG_CACHE: dict[str, Any] | None = None
_RECENT_EVENT_KEYS: dict[str, float] = {}
_EVENT_DEDUPE_SEC = 900
_LATENCY_SPIKE_DEDUPE_SEC = 3600
_LATENCY_SPIKE_SAMPLES: dict[str, list[float]] = {}
_LAST_REACHABLE: dict[str, bool] = {}
_LAST_OFFLINE_SINCE: dict[str, str] = {}
_LAST_COLLECTOR_TICK_AT: str | None = None
_COLLECTOR_ERRORS: list[dict[str, Any]] = []
_COLLECTOR_MAX_ERRORS = 20
_DASHBOARD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DASHBOARD_CACHE_TTL_SEC = 20.0
_API_SAMPLE_INTERVAL_SEC = 60
_LOGS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOGS_CACHE_TTL_SEC = 12.0
_API_ROUTES_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_API_ROUTES_CACHE_TTL_SEC = 12.0
_PROD_ENVS = ("MAIN", "HOM")

_logger = logging.getLogger("ops.monitoring")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _import_ops_store():
    base_dir = Path(server_ops.DEFAULT_BASE_DIR if hasattr(server_ops, "DEFAULT_BASE_DIR") else "C:/PPLID")
    ops_root = base_dir / "ops" / "lib"
    if str(ops_root) not in sys.path:
        sys.path.insert(0, str(ops_root))
    import ops_store  # type: ignore

    return ops_store


def resolve_ops_store_path(config: dict[str, Any]) -> Path:
    base_dir = server_ops.get_base_dir(config)
    db_path = server_ops.get_ops_store_db_path(base_dir)
    if db_path:
        return db_path
    return base_dir / "ops" / "data" / "ops-store.db"


def get_monitoring_settings(config: dict[str, Any]) -> dict[str, Any]:
    global _CONFIG_CACHE
    base_dir = server_ops.get_base_dir(config)
    machine_path = base_dir / "machine.config.json"
    runtime_path = base_dir / "ops" / "data" / "monitoring-config.json"
    merged = dict(_DEFAULT_MONITORING)
    if machine_path.is_file():
        try:
            raw = json.loads(machine_path.read_text(encoding="utf-8"))
            mon = raw.get("monitoring") or {}
            merged.update({k: v for k, v in mon.items() if k != "enabledCategories"})
            if isinstance(mon.get("enabledCategories"), dict):
                merged["enabledCategories"] = {
                    **merged["enabledCategories"],
                    **mon["enabledCategories"],
                }
        except (OSError, json.JSONDecodeError):
            pass
    if runtime_path.is_file():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(runtime.get("enabledCategories"), dict):
                merged["enabledCategories"] = {
                    **merged["enabledCategories"],
                    **runtime["enabledCategories"],
                }
            if isinstance(runtime.get("defaultEnvs"), list):
                merged["defaultEnvs"] = runtime["defaultEnvs"]
        except (OSError, json.JSONDecodeError):
            pass
    _CONFIG_CACHE = merged
    return merged


def probe_health(url: str, timeout: float = 5.0) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body) if body else {}
            duration_ms = int((time.perf_counter() - start) * 1000)
            return {
                "reachable": True,
                "httpStatus": response.status,
                "status": data.get("status"),
                "database": data.get("database"),
                "version": data.get("version"),
                "durationMs": duration_ms,
                "error": None,
                "checkedAt": _utc_now_iso(),
            }
    except urllib.error.HTTPError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return {
            "reachable": True,
            "httpStatus": exc.code,
            "status": "unhealthy",
            "database": "error",
            "version": None,
            "durationMs": duration_ms,
            "error": detail or str(exc),
            "checkedAt": _utc_now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "reachable": False,
            "httpStatus": None,
            "status": "offline",
            "database": None,
            "version": None,
            "durationMs": duration_ms,
            "error": str(exc),
            "checkedAt": _utc_now_iso(),
        }


def fetch_backend_api_metrics(
    config: dict[str, Any], env_name: str, window: str = "24h", *, timeout: float = 5.0
) -> dict[str, Any]:
    env_cfg = config.get(env_name, {})
    port = int(env_cfg.get("backendPort") or 0)
    if not port:
        return {"error": "Porta backend nao configurada"}
    url = f"http://127.0.0.1:{port}/api/v1/ops-metrics/summary/?window={window}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "reachable": False}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "reachable": False}


def fetch_backend_api_samples_around(
    config: dict[str, Any],
    env_name: str,
    at: str,
    *,
    radius_minutes: int = 5,
    min_ms: int = 0,
    timeout: float = 5.0,
) -> dict[str, Any]:
    env_cfg = config.get(env_name, {})
    port = int(env_cfg.get("backendPort") or 0)
    if not port:
        return {"error": "Porta backend nao configurada"}
    params = urllib.parse.urlencode(
        {
            "at": at,
            "radiusMinutes": max(1, min(60, int(radius_minutes or 5))),
            "minMs": max(0, int(min_ms or 0)),
        }
    )
    url = f"http://127.0.0.1:{port}/api/v1/ops-metrics/around/?{params}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {"error": f"HTTP {exc.code}", "detail": body, "reachable": False}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "reachable": False}


def _record_event(
    ops_store,
    db_path: Path,
    env_name: str,
    severity: str,
    category: str,
    title: str,
    detail: str | None = None,
    *,
    dedupe_sec: int | None = None,
) -> None:
    key = f"{env_name.upper()}:{category}:{title}"
    now = time.time()
    window = dedupe_sec if dedupe_sec is not None else _EVENT_DEDUPE_SEC
    if key in _RECENT_EVENT_KEYS and now - _RECENT_EVENT_KEYS[key] < window:
        return
    _RECENT_EVENT_KEYS[key] = now
    try:
        ops_store.insert_monitor_event(
            env_name, severity, category, title, detail=detail, db_path=db_path
        )
    except Exception:
        pass


def _format_offline_detail(
    health: dict[str, Any],
    *,
    backend_port_up: bool,
    frontend_port_up: bool,
) -> str:
    parts = ["Health check falhou"]
    err = str(health.get("error") or "").strip()
    if err:
        parts.append(f"erro: {err[:180]}")
    status = health.get("status")
    if status and status != "offline":
        parts.append(f"status={status}")
    http_status = health.get("httpStatus")
    if http_status:
        parts.append(f"HTTP {http_status}")
    parts.append(f"backend_port={'up' if backend_port_up else 'down'}")
    parts.append(f"frontend_port={'up' if frontend_port_up else 'down'}")
    return " · ".join(parts)


def _detect_health_spike(
    ops_store,
    db_path: Path,
    env_name: str,
    latency_ms: float,
    reachable: bool,
    *,
    health: dict[str, Any] | None = None,
    backend_port_up: bool = True,
    frontend_port_up: bool = True,
) -> None:
    settings = _CONFIG_CACHE or _DEFAULT_MONITORING
    slos = settings.get("slos") or _DEFAULT_MONITORING["slos"]
    warn_ms = float(slos.get("healthP95WarnMs") or 2000)
    critical_ms = float(slos.get("healthP95CriticalMs") or 3000)
    env_key = env_name.upper()
    health = health or {}

    if not reachable:
        detail = _format_offline_detail(
            health, backend_port_up=backend_port_up, frontend_port_up=frontend_port_up
        )
        _record_event(
            ops_store,
            db_path,
            env_name,
            "critical",
            "availability",
            "Backend offline",
            detail,
        )
        if env_key not in _LAST_OFFLINE_SINCE:
            _LAST_OFFLINE_SINCE[env_key] = _utc_now_iso()
        _LAST_REACHABLE[env_key] = False
        _LATENCY_SPIKE_SAMPLES.pop(env_key, None)
        return

    prev = _LAST_REACHABLE.get(env_key)
    offline_since = _LAST_OFFLINE_SINCE.pop(env_key, None)
    if prev is False:
        duration_label = ""
        if offline_since:
            started = _parse_iso_dt(offline_since)
            if started:
                dur_sec = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
                duration_label = f" · offline por {_format_duration_sec(dur_sec)}"
        _record_event(
            ops_store,
            db_path,
            env_name,
            "info",
            "availability",
            "Backend recuperado",
            f"Health check voltou a responder{duration_label}",
            dedupe_sec=60,
        )
    _LAST_REACHABLE[env_key] = True

    since = _iso_days_ago(1)
    agg = ops_store.aggregate_monitor_samples(
        env_name, "health_latency_ms", since=since, db_path=db_path
    )
    avg = float(agg.get("avg") or 0)
    p95 = float(agg.get("p95") or 0)

    if p95 >= critical_ms or p95 >= warn_ms:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "availability",
            f"Latencia p95 elevada ({int(p95)}ms)",
            f"p95 24h acima do SLO ({int(warn_ms)}ms)"
            + (f" · limiar critico {int(critical_ms)}ms" if p95 >= critical_ms else ""),
            dedupe_sec=_LATENCY_SPIKE_DEDUPE_SEC,
        )

    history = _LATENCY_SPIKE_SAMPLES.setdefault(env_key, [])
    history.append(latency_ms)
    if len(history) > 3:
        history.pop(0)

    spike_threshold = max(warn_ms, avg * 2 if avg > 0 else warn_ms)
    consecutive_spike = len(history) >= 3 and all(v > spike_threshold for v in history)

    if latency_ms > critical_ms:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "availability",
            f"Health lento ({int(latency_ms)}ms)",
            f"Latencia acima de {int(critical_ms)}ms",
            dedupe_sec=_LATENCY_SPIKE_DEDUPE_SEC,
        )
    elif consecutive_spike:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "info",
            "availability",
            f"Pico de latencia health ({int(latency_ms)}ms)",
            f"Media recente: {avg:.0f}ms · 3 amostras consecutivas acima de {int(spike_threshold)}ms",
            dedupe_sec=_LATENCY_SPIKE_DEDUPE_SEC,
        )
    elif avg > 0 and latency_ms > avg * 2 and latency_ms > warn_ms:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "info",
            "availability",
            f"Pico pontual de latencia ({int(latency_ms)}ms)",
            f"Media recente: {avg:.0f}ms",
            dedupe_sec=_LATENCY_SPIKE_DEDUPE_SEC,
        )


def _format_duration_sec(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        rem = seconds % 60
        return f"{minutes} min" if rem == 0 else f"{minutes} min {rem}s"
    hours = minutes // 60
    rem_min = minutes % 60
    return f"{hours}h" if rem_min == 0 else f"{hours}h {rem_min}min"


def _compute_offline_window(
    ops_store,
    db_path: Path,
    env_name: str,
    around_iso: str | None,
    *,
    lookback_hours: int = 12,
) -> dict[str, Any] | None:
    center = _parse_iso_dt(around_iso) or datetime.now(timezone.utc)
    since = (center - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    until = (center + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    samples = ops_store.query_monitor_series(
        env_name,
        "health_reachable",
        since=since,
        until=until,
        limit=5000,
        db_path=db_path,
    )
    if not samples:
        return None

    points = []
    for s in samples:
        dt = _parse_iso_dt(s.get("recorded_at"))
        if not dt:
            continue
        points.append((dt, float(s.get("value") or 0) < 0.5, s.get("recorded_at")))
    if not points:
        return None

    # Find index nearest to center
    nearest_idx = min(range(len(points)), key=lambda i: abs((points[i][0] - center).total_seconds()))
    if not points[nearest_idx][1]:
        # Center sample is online — still search nearby offline streak
        found = None
        for i, (_dt, offline, _iso) in enumerate(points):
            if offline and abs((_dt - center).total_seconds()) <= 900:
                found = i
                break
        if found is None:
            return None
        nearest_idx = found

    start_idx = nearest_idx
    while start_idx > 0 and points[start_idx - 1][1]:
        start_idx -= 1
    end_idx = nearest_idx
    while end_idx + 1 < len(points) and points[end_idx + 1][1]:
        end_idx += 1

    offline_from = points[start_idx][2]
    last_offline = points[end_idx]
    ongoing = end_idx == len(points) - 1 and last_offline[1]
    offline_until = None if ongoing else (
        points[end_idx + 1][2] if end_idx + 1 < len(points) else last_offline[2]
    )
    end_dt = datetime.now(timezone.utc) if ongoing else (
        _parse_iso_dt(offline_until) or last_offline[0]
    )
    start_dt = points[start_idx][0]
    duration_sec = max(0, int((end_dt - start_dt).total_seconds()))
    return {
        "offlineFrom": offline_from,
        "offlineUntil": offline_until,
        "ongoing": ongoing,
        "offlineDurationSec": duration_sec,
        "offlineDurationLabel": _format_duration_sec(duration_sec),
    }


def _collect_availability(config: dict[str, Any], env_name: str, ops_store, db_path: Path) -> None:
    env_cfg = config.get(env_name, {})
    backend_port = int(env_cfg.get("backendPort") or 0)
    frontend_port = int(env_cfg.get("frontendPort") or 0)
    health_url = f"http://127.0.0.1:{backend_port}/api/v1/health/" if backend_port else ""
    health = (
        probe_health(health_url)
        if health_url
        else {"reachable": False, "durationMs": 0, "error": "Porta backend nao configurada"}
    )
    backend_up = bool(server_ops.probe_port_listening(backend_port)) if backend_port else False
    frontend_up = bool(server_ops.probe_port_listening(frontend_port)) if frontend_port else False

    ops_store.insert_monitor_sample(
        env_name,
        "health_latency_ms",
        float(health.get("durationMs") or 0),
        labels={"reachable": health.get("reachable")},
        db_path=db_path,
    )
    ops_store.insert_monitor_sample(
        env_name,
        "health_reachable",
        1.0 if health.get("reachable") else 0.0,
        db_path=db_path,
    )
    ops_store.insert_monitor_sample(
        env_name,
        "backend_port_up",
        1.0 if backend_up else 0.0,
        db_path=db_path,
    )
    ops_store.insert_monitor_sample(
        env_name,
        "frontend_port_up",
        1.0 if frontend_up else 0.0,
        db_path=db_path,
    )
    _detect_health_spike(
        ops_store,
        db_path,
        env_name,
        float(health.get("durationMs") or 0),
        bool(health.get("reachable")),
        health=health,
        backend_port_up=backend_up,
        frontend_port_up=frontend_up,
    )


def _collect_api_metrics(config: dict[str, Any], env_name: str, ops_store, db_path: Path) -> None:
    data = fetch_backend_api_metrics(config, env_name, window="1h")
    if data.get("error") or data.get("reachable") is False:
        return
    totals = data.get("totals") or data.get("summary") or {}
    avg_ms = totals.get("avgMs")
    if avg_ms is None:
        avg_ms = totals.get("avg_ms")
    errors_5xx = totals.get("errors5xx")
    if errors_5xx is None:
        errors_5xx = totals.get("errors_5xx") or totals.get("status5xx") or 0
    requests = totals.get("requests") or totals.get("count") or 0
    top_routes = []
    for row in (data.get("slowRoutes") or [])[:5]:
        top_routes.append(
            {
                "method": row.get("method"),
                "route": row.get("route"),
                "avgMs": row.get("avgMs") if row.get("avgMs") is not None else row.get("avg_ms"),
                "maxMs": row.get("maxMs") if row.get("maxMs") is not None else row.get("max_ms"),
                "count": row.get("count"),
                "errors5xx": row.get("errors5xx") if row.get("errors5xx") is not None else row.get("errors_5xx"),
            }
        )
    if avg_ms is not None:
        ops_store.insert_monitor_sample(
            env_name,
            "api_avg_ms",
            float(avg_ms),
            labels={"window": "1h", "requests": requests, "topRoutes": top_routes},
            db_path=db_path,
        )
    ops_store.insert_monitor_sample(
        env_name,
        "api_5xx",
        float(errors_5xx or 0),
        labels={"window": "1h"},
        db_path=db_path,
    )


def _collect_postgres(config: dict[str, Any], env_name: str, ops_store, db_path: Path) -> None:
    try:
        metrics = server_db.collect_pg_metrics(config, env_name)
    except Exception as exc:  # noqa: BLE001
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "postgres",
            "Falha ao coletar metricas PG",
            str(exc),
        )
        return

    connections = metrics.get("connections") or {}
    total = float(connections.get("total") or 0)
    ops_store.insert_monitor_sample(env_name, "pg_connections_total", total, db_path=db_path)
    by_state = connections.get("byState") or {}
    for state, count in by_state.items():
        ops_store.insert_monitor_sample(
            env_name,
            f"pg_connections_{state}",
            float(count),
            db_path=db_path,
        )

    size_bytes = metrics.get("sizeBytes")
    if size_bytes is not None:
        ops_store.insert_monitor_sample(
            env_name, "pg_database_size_bytes", float(size_bytes), db_path=db_path
        )

    locks = metrics.get("blockingLocks") or []
    ops_store.insert_monitor_sample(
        env_name, "pg_blocking_locks", float(len(locks)), db_path=db_path
    )
    if locks:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "postgres",
            f"{len(locks)} lock(s) bloqueante(s)",
            str(locks[0].get("query", ""))[:120],
        )


def _is_errorish_log_line(line: str) -> bool:
    """Detecta erro real; evita ruído PowerShell (FullyQualifiedErrorId) e waitress queue."""
    text = str(line or "")
    upper = text.upper()
    if "TASK QUEUE DEPTH" in upper:
        return False
    if "TRACEBACK" in upper:
        return True
    if re.search(r"\bCRITICAL\b", upper):
        return True
    if re.search(r"\bERROR\b", upper):
        return True
    if " 500 " in upper or upper.rstrip().endswith(" 500"):
        return True
    return False


_LOG_LINE_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\]")
_SERVICE_LOG_FILE_OFFSETS: dict[str, int] = {}


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_bracket_log_ts(line: str) -> datetime | None:
    m = _LOG_LINE_TS_RE.match(str(line or "").strip())
    if not m:
        return None
    raw = m.group(1).replace("T", " ")
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=_local_tzinfo()).astimezone(timezone.utc)


def _service_log_sources(config: dict[str, Any], env_name: str) -> list[tuple[str, str, Path]]:
    log_dir = Path(config.get("logDir") or "C:/PPLID/logs")
    env_cfg = config.get(env_name, {}) or {}
    repo = str(env_cfg.get("repoName") or f"PPLID_{env_name}")
    return [
        ("app", "out", log_dir / f"{repo}.log"),
        ("backend", "stderr", log_dir / f"{repo}.backend.err.log"),
        ("backend", "stdout", log_dir / f"{repo}.backend.out.log"),
        ("frontend", "stderr", log_dir / f"{repo}.frontend.err.log"),
        ("frontend", "stdout", log_dir / f"{repo}.frontend.out.log"),
    ]


def _tail_text_file_lines(path: Path, *, max_bytes: int = 512_000, max_lines: int = 500) -> list[str]:
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        # Arquivos de app (~5MB+) precisam olhar mais do que 512KB quando filtramos por since
        budget = max_bytes
        if size > 1_000_000:
            budget = max(max_bytes, 1_500_000)
        with path.open("rb") as fh:
            start = max(0, size - budget)
            fh.seek(start)
            raw = fh.read().decode("utf-8", errors="replace")
        lines = raw.splitlines()
        if start > 0 and lines:
            lines = lines[1:]
        return lines[-max_lines:]
    except OSError:
        return []


def _line_matches_pattern(line: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return pattern.upper() in str(line or "").upper()


def _query_service_logs_from_files(
    config: dict[str, Any],
    env_name: str,
    *,
    since: str | None = None,
    pattern: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    since_dt = _parse_iso_dt(since) if since else None
    sources = [s for s in _service_log_sources(config, env_name) if s[2].is_file()]
    if not sources:
        return []
    # reserva por arquivo para o stderr ruidoso (waitress) não expulsar o .log principal
    per_cap = max(40, (limit + len(sources) - 1) // len(sources) + 20)
    buckets: list[list[dict[str, Any]]] = []
    for service, stream, path in sources:
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_local_tzinfo()).astimezone(
                timezone.utc
            )
        except OSError:
            mtime = datetime.now(timezone.utc)
        per_file_limit = max(per_cap * 3, 400) if service == "app" else max(per_cap * 2, 200)
        rows: list[dict[str, Any]] = []
        seen_local: set[str] = set()
        for raw in _tail_text_file_lines(path, max_lines=per_file_limit):
            if not _line_matches_pattern(raw, pattern):
                continue
            ts = _parse_bracket_log_ts(raw)
            if ts is None:
                if since_dt and mtime < since_dt:
                    continue
                ts = mtime
            elif since_dt and ts < since_dt:
                continue
            # Dedup ruidoso (mesma WARNING repetida)
            dedupe_key = f"{raw.strip()}"
            if dedupe_key in seen_local:
                continue
            seen_local.add(dedupe_key)
            rows.append(
                {
                    "service": service,
                    "stream": stream,
                    "line": raw,
                    "logged_at": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "source": "file",
                    "file": path.name,
                }
            )
        rows.sort(key=lambda r: r.get("logged_at") or "", reverse=True)
        buckets.append(rows[:per_cap])

    out: list[dict[str, Any]] = []
    for bucket in buckets:
        out.extend(bucket)
    out.sort(key=lambda r: r.get("logged_at") or "", reverse=True)
    return out[:limit]


def _ingest_service_log_files(
    config: dict[str, Any], env_name: str, ops_store, db_path: Path
) -> list[dict[str, Any]]:
    """Lê novos bytes dos logs em disco e grava em service_log_lines. Retorna linhas novas."""
    ingested: list[dict[str, Any]] = []
    for service, stream, path in _service_log_sources(config, env_name):
        if not path.is_file():
            continue
        key = str(path.resolve()) if path.exists() else str(path)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        offset = _SERVICE_LOG_FILE_OFFSETS.get(key)
        if offset is None:
            # No restart, não reingestar histórico inteiro — só os últimos ~64KB
            offset = max(0, size - 64_000)
        if offset > size:
            offset = 0
        if offset == size:
            continue
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                new_offset = fh.tell()
        except OSError:
            continue
        text = chunk.decode("utf-8", errors="replace")
        if offset > 0 and text and not text.startswith("\n"):
            # descarta linha parcial no início do slice
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl + 1 :]
        now_iso = _utc_now_iso()
        for raw in text.splitlines():
            line = raw.rstrip("\r")
            if not line.strip():
                continue
            ts = _parse_bracket_log_ts(line)
            logged_at = (
                ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if ts else now_iso
            )
            try:
                ops_store.append_service_log(
                    env_name,
                    service,
                    stream,
                    line,
                    logged_at=logged_at,
                    db_path=db_path,
                )
            except Exception:
                continue
            ingested.append(
                {
                    "service": service,
                    "stream": stream,
                    "line": line,
                    "logged_at": logged_at,
                }
            )
        _SERVICE_LOG_FILE_OFFSETS[key] = new_offset
    return ingested


def _collect_service_log_errors(config: dict[str, Any], env_name: str, ops_store, db_path: Path) -> None:
    # 1) Ingestão dos arquivos em C:\PPLID\logs → SQLite
    try:
        fresh = _ingest_service_log_files(config, env_name, ops_store, db_path)
    except Exception:
        fresh = []

    # 2) Conta só linhas novas deste tick (não rescan histórico do SQLite)
    error_lines = [ln for ln in fresh if _is_errorish_log_line(ln.get("line") or "")]
    error_count = len(error_lines)

    # Fallback: se não houve ingestão de arquivo, mantém varredura incremental por id
    # mas só em linhas com logged_at recente (evita alertas fantasmas pós-restart).
    if not fresh:
        key = f"{env_name}"
        since_id = _SERVICE_LOG_OFFSETS.get(key)
        if since_id is None:
            try:
                with ops_store.get_connection(db_path) as conn:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(id), 0) AS mid FROM service_log_lines WHERE environment=?",
                        (env_name.upper(),),
                    ).fetchone()
                    since_id = int(row["mid"] if row else 0)
            except Exception:
                since_id = 0
            _SERVICE_LOG_OFFSETS[key] = since_id
            return
        try:
            with ops_store.get_connection(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT id, service, stream, line, logged_at
                    FROM service_log_lines
                    WHERE environment=? AND id>?
                    ORDER BY id ASC
                    LIMIT 500
                    """,
                    (env_name.upper(), since_id),
                ).fetchall()
        except Exception:
            return
        max_id = since_id
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        for row in rows:
            max_id = max(max_id, int(row["id"]))
            logged = _parse_iso_dt(row["logged_at"])
            if logged and logged < cutoff:
                continue
            if _is_errorish_log_line(str(row["line"] or "")):
                error_count += 1
        _SERVICE_LOG_OFFSETS[key] = max_id

    if error_count:
        ops_store.insert_monitor_sample(
            env_name, "service_log_errors", float(error_count), db_path=db_path
        )
        if error_count >= 10:
            sample = (error_lines[0].get("line") if error_lines else "") or ""
            _record_event(
                ops_store,
                db_path,
                env_name,
                "warn",
                "logs",
                f"Pico de erros nos logs ({error_count} linhas)",
                (sample[:180] or "Verifique logs de serviço em disco"),
            )


def _query_sync_logs(
    config: dict[str, Any], env_name: str, days: int = 7
) -> tuple[list[dict[str, Any]], str | None]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    results: list[dict[str, Any]] = []

    queries = [
        (
            "rotina_bruto",
            """
            SELECT report_type AS kind, started_at, finished_at, duration_seconds,
                   success, message, row_count, trigger_source
            FROM rotina_bruto_sync_log
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 100
            """,
        ),
        (
            "falhas_criticas",
            """
            SELECT 'excel' AS kind, started_at, finished_at, duration_seconds,
                   success, message, NULL::int AS row_count, trigger_source
            FROM falhas_sync_audit_log
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 100
            """,
        ),
        (
            "monitor_eventos",
            """
            SELECT 'monitor' AS kind, started_at, finished_at, duration_seconds,
                   success, message, row_count, trigger_source
            FROM monitor_evento_sync_log
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 100
            """,
        ),
        (
            "produtividade",
            """
            SELECT 'prod' AS kind, started_at, finished_at, duration_seconds,
                   success, message, row_count, trigger_source
            FROM produtividade_sync_log
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 100
            """,
        ),
        (
            "escala_flex",
            """
            SELECT filename AS kind, created_at AS started_at, finished_at,
                   EXTRACT(EPOCH FROM (finished_at - created_at)) AS duration_seconds,
                   (status = 'completed') AS success,
                   COALESCE(failure_detail, '') AS message,
                   rows_upserted AS row_count,
                   'user' AS trigger_source
            FROM escala_import_batch
            WHERE created_at >= %s
            ORDER BY created_at DESC
            LIMIT 100
            """,
        ),
    ]

    try:
        with server_db.get_pg_connection(config, env_name) as conn:
            from psycopg.rows import dict_row

            with conn.cursor(row_factory=dict_row) as cur:
                for source, sql in queries:
                    try:
                        cur.execute(sql, (since,))
                        for row in cur.fetchall():
                            raw = dict(row)
                            item = {k: server_db.serialize_value(v) for k, v in raw.items()}
                            item["source"] = source
                            started = raw.get("started_at")
                            finished = raw.get("finished_at")
                            if started is not None:
                                item["startedAt"] = (
                                    started.isoformat()
                                    if hasattr(started, "isoformat")
                                    else str(started)
                                )
                            if finished is not None:
                                item["finishedAt"] = (
                                    finished.isoformat()
                                    if hasattr(finished, "isoformat")
                                    else str(finished)
                                )
                            results.append(item)
                    except Exception:
                        continue
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)

    results.sort(key=lambda r: r.get("startedAt") or "", reverse=True)
    return results[:200], None


def _check_sync_anomalies(
    config: dict[str, Any], env_name: str, ops_store, db_path: Path
) -> None:
    syncs, _error = _query_sync_logs(config, env_name, days=1)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=3)
    for sync in syncs:
        started_raw = sync.get("startedAt")
        if not started_raw:
            continue
        try:
            started_dt = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if started_dt < cutoff:
            continue
        if not sync.get("success"):
            _record_event(
                ops_store,
                db_path,
                env_name,
                "warn",
                "sync",
                f"Sync falhou ({sync.get('source')}/{sync.get('kind')})",
                str(sync.get("message") or "")[:200],
            )


def _collector_tick(config: dict[str, Any]) -> None:
    global _LAST_PURGE_AT, _LAST_COLLECTOR_TICK_AT
    settings = get_monitoring_settings(config)
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    try:
        ops_store.init_store(db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("monitoring collector init_store failed: %s", exc)

    now = time.time()
    if now - _LAST_PURGE_AT > 24 * 3600:
        try:
            ops_store.purge_monitor_data(
                retention_days=int(settings.get("retentionDays") or 7), db_path=db_path
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("monitoring purge failed: %s", exc)
        _LAST_PURGE_AT = now

    categories = settings.get("enabledCategories") or {}
    pg_interval = float(settings.get("pgSnapshotIntervalSec") or 300)
    api_interval = float(settings.get("apiSampleIntervalSec") or _API_SAMPLE_INTERVAL_SEC)

    for env_name in ENV_ORDER:
        try:
            if categories.get("availability", True):
                _collect_availability(config, env_name, ops_store, db_path)
            last_pg = _LAST_PG_SNAPSHOT.get(env_name, 0.0)
            if categories.get("postgres", True) and (now - last_pg >= pg_interval):
                _collect_postgres(config, env_name, ops_store, db_path)
                _LAST_PG_SNAPSHOT[env_name] = now
            if categories.get("logs", True):
                _collect_service_log_errors(config, env_name, ops_store, db_path)
            if categories.get("syncs", True):
                _check_sync_anomalies(config, env_name, ops_store, db_path)
            last_api = _LAST_API_SNAPSHOT.get(env_name, 0.0)
            if (
                categories.get("api", True)
                and env_name in _PROD_ENVS
                and (now - last_api >= api_interval)
            ):
                _collect_api_metrics(config, env_name, ops_store, db_path)
                _LAST_API_SNAPSHOT[env_name] = now
        except Exception as exc:  # noqa: BLE001
            msg = f"{env_name}: {exc}"
            _logger.exception("monitoring collector tick failed for %s", env_name)
            _COLLECTOR_ERRORS.append({"at": _utc_now_iso(), "message": msg})
            if len(_COLLECTOR_ERRORS) > _COLLECTOR_MAX_ERRORS:
                _COLLECTOR_ERRORS.pop(0)

    _LAST_COLLECTOR_TICK_AT = _utc_now_iso()


def _collector_loop(config: dict[str, Any]) -> None:
    settings = get_monitoring_settings(config)
    interval = max(15, int(settings.get("collectorIntervalSec") or 60))
    while not _COLLECTOR_STOP.wait(interval):
        try:
            _collector_tick(config)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("monitoring collector loop failed: %s", exc)
            _COLLECTOR_ERRORS.append({"at": _utc_now_iso(), "message": str(exc)})
            if len(_COLLECTOR_ERRORS) > _COLLECTOR_MAX_ERRORS:
                _COLLECTOR_ERRORS.pop(0)


def _collector_watchdog_loop() -> None:
    while not _COLLECTOR_STOP.wait(30):
        global _COLLECTOR_THREAD
        cfg = _COLLECTOR_CONFIG
        if not cfg:
            continue
        if _COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive():
            continue
        _logger.warning("monitoring collector thread dead — restarting")
        _COLLECTOR_ERRORS.append(
            {"at": _utc_now_iso(), "message": "collector thread dead; restarting"}
        )
        if len(_COLLECTOR_ERRORS) > _COLLECTOR_MAX_ERRORS:
            _COLLECTOR_ERRORS.pop(0)
        _COLLECTOR_THREAD = threading.Thread(
            target=_collector_loop,
            args=(cfg,),
            name="ops-monitoring-collector",
            daemon=True,
        )
        _COLLECTOR_THREAD.start()


def start_monitoring_collector(config: dict[str, Any]) -> None:
    global _COLLECTOR_THREAD, _COLLECTOR_WATCHDOG_THREAD, _COLLECTOR_CONFIG
    _COLLECTOR_CONFIG = config
    if _COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive():
        if not (_COLLECTOR_WATCHDOG_THREAD and _COLLECTOR_WATCHDOG_THREAD.is_alive()):
            _COLLECTOR_WATCHDOG_THREAD = threading.Thread(
                target=_collector_watchdog_loop,
                name="ops-monitoring-watchdog",
                daemon=True,
            )
            _COLLECTOR_WATCHDOG_THREAD.start()
        return
    _COLLECTOR_STOP.clear()
    _COLLECTOR_THREAD = threading.Thread(
        target=_collector_loop,
        args=(config,),
        name="ops-monitoring-collector",
        daemon=True,
    )
    _COLLECTOR_THREAD.start()
    threading.Thread(target=_collector_tick, args=(config,), name="ops-monitoring-initial", daemon=True).start()
    if not (_COLLECTOR_WATCHDOG_THREAD and _COLLECTOR_WATCHDOG_THREAD.is_alive()):
        _COLLECTOR_WATCHDOG_THREAD = threading.Thread(
            target=_collector_watchdog_loop,
            name="ops-monitoring-watchdog",
            daemon=True,
        )
        _COLLECTOR_WATCHDOG_THREAD.start()


def stop_monitoring_collector() -> None:
    _COLLECTOR_STOP.set()


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _collector_status(config: dict[str, Any]) -> dict[str, Any]:
    settings = get_monitoring_settings(config)
    interval = max(15, int(settings.get("collectorIntervalSec") or 60))
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    latest_at: str | None = None
    sample_count = 0
    try:
        for env_name in ENV_ORDER:
            points = ops_store.query_monitor_series(
                env_name, "health_latency_ms", since=since, limit=5, db_path=db_path
            )
            sample_count += len(points)
            for point in points:
                recorded = point.get("recorded_at")
                if recorded and (latest_at is None or recorded > latest_at):
                    latest_at = recorded
    except Exception:
        pass

    if sample_count == 0:
        return {
            "status": "no_data",
            "label": "Sem dados",
            "lastSampleAt": None,
            "intervalSec": interval,
            "threadAlive": bool(_COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive()),
            "recentErrors": list(_COLLECTOR_ERRORS[-5:]),
            "lastTickAt": _LAST_COLLECTOR_TICK_AT,
        }

    latest_dt = _parse_iso_dt(latest_at)
    now = datetime.now(timezone.utc)
    if latest_dt and (now - latest_dt).total_seconds() > interval * 3:
        return {
            "status": "stale",
            "label": "Coleta atrasada",
            "lastSampleAt": latest_at,
            "intervalSec": interval,
            "threadAlive": bool(_COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive()),
            "recentErrors": list(_COLLECTOR_ERRORS[-5:]),
            "lastTickAt": _LAST_COLLECTOR_TICK_AT,
        }
    return {
        "status": "ok",
        "label": "Coleta OK",
        "lastSampleAt": latest_at,
        "intervalSec": interval,
        "threadAlive": bool(_COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive()),
        "recentErrors": list(_COLLECTOR_ERRORS[-5:]),
        "lastTickAt": _LAST_COLLECTOR_TICK_AT,
    }


def build_monitoring_collector_status_detail(config: dict[str, Any]) -> dict[str, Any]:
    settings = get_monitoring_settings(config)
    interval = max(15, int(settings.get("collectorIntervalSec") or 60))
    base = _collector_status(config)
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    samples_by_env: dict[str, int] = {}
    try:
        for env_name in ENV_ORDER:
            points = ops_store.query_monitor_series(
                env_name, "health_latency_ms", since=since, limit=500, db_path=db_path
            )
            samples_by_env[env_name] = len(points)
    except Exception:
        pass
    thread_alive = _COLLECTOR_THREAD.is_alive() if _COLLECTOR_THREAD else False
    return {
        **base,
        "lastTickAt": _LAST_COLLECTOR_TICK_AT,
        "threadAlive": thread_alive,
        "samplesByEnv2h": samples_by_env,
        "recentErrors": list(_COLLECTOR_ERRORS),
    }


def _event_investigation_link(event: dict[str, Any]) -> tuple[str, str]:
    category = str(event.get("category") or "").lower()
    env = str(event.get("environment") or "").upper()
    title = str(event.get("title") or "")
    recorded = str(event.get("recorded_at") or "")
    at_param = f"&at={urllib.parse.quote(recorded)}" if recorded else ""
    # Logs do pico ficam tipicamente ANTES do instante do evento
    since_for_logs = recorded
    recorded_dt = _parse_iso_dt(recorded)
    if recorded_dt:
        since_for_logs = (recorded_dt - timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    since_param = f"&since={urllib.parse.quote(since_for_logs)}" if since_for_logs else ""

    # History API paths (não usar #/ — o router ignora hash fora de /)
    if category in ("logs", "log"):
        return "Verificar logs do serviço", f"/monitoring/logs?env={env}{since_param}"
    if category == "sync":
        highlight = ""
        if "Sync falhou" in title and "(" in title and ")" in title:
            inner = title.split("(", 1)[1].rsplit(")", 1)[0]
            highlight = f"&highlight={urllib.parse.quote(inner)}"
        return "Ver histórico de syncs", f"/monitoring/syncs?env={env}{highlight}"
    if category in ("health", "availability"):
        return "Ver tendência de latência", f"/monitoring/latency?env={env}{at_param}"
    if category == "postgres":
        return "Investigar painel PG", f"/database/{env}"
    if category == "deploy":
        return "Ver pipeline de deploy", f"/?env={env}"
    if "pico" in title.lower() or "erro" in title.lower():
        return "Ver logs filtrados", f"/monitoring/logs?env={env}{since_param}"
    return "Investigar no painel", f"/monitoring/incidents?env={env}"


def _nearby_service_logs(
    ops_store,
    db_path: Path,
    env_name: str,
    *,
    since: str,
    until: str,
    limit: int = 30,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Busca logs próximos em disco + SQLite."""
    combined: list[dict[str, Any]] = []
    if config:
        try:
            combined.extend(
                _query_service_logs_from_files(
                    config, env_name, since=since, pattern=None, limit=limit * 2
                )
            )
        except Exception:
            pass
    try:
        lines = ops_store.query_service_log_lines(
            env_name.upper(),
            since=since,
            until=until,
            pattern=None,
            limit=min(200, max(limit * 4, 40)),
            db_path=db_path,
        )
        combined.extend(lines)
    except TypeError:
        try:
            lines = ops_store.query_service_log_lines(
                env_name.upper(),
                since=since,
                pattern=None,
                limit=min(200, max(limit * 4, 40)),
                db_path=db_path,
            )
            combined.extend([ln for ln in lines if (ln.get("logged_at") or "") <= until])
        except Exception:
            pass
    except Exception:
        pass

    until_dt = _parse_iso_dt(until)
    if until_dt:
        filtered = []
        for ln in combined:
            ts = _parse_iso_dt(ln.get("logged_at"))
            if ts and ts > until_dt:
                continue
            filtered.append(ln)
        combined = filtered

    # dedupe by line+logged_at
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for ln in sorted(combined, key=lambda r: r.get("logged_at") or "", reverse=True):
        key = f"{ln.get('logged_at')}|{ln.get('line')}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ln)

    errorish = [ln for ln in uniq if _is_errorish_log_line(ln.get("line") or "")]
    if errorish:
        return errorish[:limit]
    return uniq[: min(limit, 15)]


def _correlate_event(
    event: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    env = str(event.get("environment") or "").upper()
    recorded_dt = _parse_iso_dt(event.get("recorded_at"))
    if not recorded_dt or not env:
        return hints

    window = timedelta(minutes=10)
    start = recorded_dt - window
    end = recorded_dt + window

    try:
        deploy_stats = build_monitoring_deploy_stats(config, env)
        for run in deploy_stats.get("runs") or []:
            started = _parse_iso_dt(run.get("started_at"))
            if started and start <= started <= end:
                hints.append(
                    {
                        "type": "deploy",
                        "label": f"Possível causa: deploy às {started.strftime('%H:%M')}",
                    }
                )
                break
    except Exception:
        pass

    syncs, _err = _query_sync_logs(config, env, days=1)
    for sync in syncs:
        if sync.get("success"):
            continue
        started = _parse_iso_dt(sync.get("startedAt"))
        if started and start <= started <= end:
            src = sync.get("source") or "sync"
            kind = sync.get("kind") or ""
            hints.append(
                {
                    "type": "sync",
                    "label": f"Sync falhou antes do incidente ({src}/{kind})",
                }
            )
            break

    category = str(event.get("category") or "").lower()
    if category in ("health", "availability"):
        ops_store = _import_ops_store()
        db_path = resolve_ops_store_path(config)
        since_iso = start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        until_iso = end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        try:
            log_events = ops_store.query_monitor_events(
                env,
                since=since_iso,
                until=until_iso,
                category="logs",
                limit=5,
                db_path=db_path,
            )
            if log_events:
                hints.append(
                    {
                        "type": "logs",
                        "label": "Erros em service_log_lines no mesmo intervalo",
                    }
                )
        except Exception:
            pass

    return hints


def _enrich_monitor_event(event: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    enriched = dict(event)
    action, link = _event_investigation_link(event)
    enriched["recommendedAction"] = action
    enriched["investigationLink"] = link
    if config is not None:
        enriched["correlations"] = _correlate_event(event, config)
    else:
        enriched["correlations"] = []
    return enriched


def _aggregate_deploy_runs(runs: list[dict[str, Any]], *, hours: int = 24) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent: list[dict[str, Any]] = []
    for run in runs:
        started_dt = _parse_iso_dt(run.get("started_at"))
        if started_dt and started_dt >= cutoff:
            recent.append(run)

    def _is_success(run: dict[str, Any]) -> bool:
        status = str(run.get("result") or run.get("status") or "").lower()
        return status in ("success", "ok", "completed", "passed")

    def _is_failed(run: dict[str, Any]) -> bool:
        status = str(run.get("result") or run.get("status") or "").lower()
        return status in ("failed", "error", "failure")

    failed = [r for r in recent if _is_failed(r)]
    success = [r for r in recent if _is_success(r)]
    failures_by_step: dict[str, int] = {}
    for run in failed:
        step = str(run.get("failed_step") or "desconhecido")
        failures_by_step[step] = failures_by_step.get(step, 0) + 1

    total = len(recent)
    success_rate = round(100.0 * len(success) / total, 1) if total else None
    last_success = next((r for r in runs if _is_success(r)), None)
    last_failed = failed[0] if failed else None

    return {
        "total24h": total,
        "success24h": len(success),
        "failed24h": len(failed),
        "successRate24h": success_rate,
        "failuresByStep": failures_by_step,
        "lastSuccess": last_success,
        "lastFailed": last_failed,
        "lastSuccessAt": last_success.get("finished_at") if last_success else None,
        "timeSinceLastSuccess": None,
    }


def build_monitoring_config(config: dict[str, Any]) -> dict[str, Any]:
    settings = get_monitoring_settings(config)
    collector = _collector_status(config)
    return {
        "retentionDays": settings.get("retentionDays", 7),
        "collectorIntervalSec": settings.get("collectorIntervalSec", 60),
        "pgSnapshotIntervalSec": settings.get("pgSnapshotIntervalSec", 300),
        "defaultEnvs": settings.get("defaultEnvs", list(ENV_ORDER)),
        "slowRequestMs": settings.get("slowRequestMs", 2000),
        "slos": settings.get("slos", _DEFAULT_MONITORING["slos"]),
        "enabledCategories": settings.get("enabledCategories", _DEFAULT_MONITORING["enabledCategories"]),
        "environments": list(ENV_ORDER),
        "collectorStatus": collector,
        "generatedAt": _utc_now_iso(),
        "metricKeys": [
            "health_latency_ms",
            "health_reachable",
            "backend_port_up",
            "frontend_port_up",
            "pg_connections_total",
            "pg_blocking_locks",
            "pg_database_size_bytes",
            "service_log_errors",
            "api_avg_ms",
            "api_5xx",
        ],
    }


def _is_data_fresh(config: dict[str, Any], env_name: str | None = None) -> bool:
    collector = _collector_status(config)
    if collector.get("status") != "ok":
        latest_at = collector.get("lastSampleAt")
        latest_dt = _parse_iso_dt(latest_at)
        if latest_dt:
            stale_sec = (datetime.now(timezone.utc) - latest_dt).total_seconds()
            if stale_sec > 3600:
                return False
        if collector.get("status") in ("stale", "no_data"):
            return False
    if env_name:
        ops_store = _import_ops_store()
        db_path = resolve_ops_store_path(config)
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        health = ops_store.aggregate_monitor_samples(
            env_name.upper(), "health_latency_ms", since=since, db_path=db_path
        )
        if health.get("latestAt"):
            latest_dt = _parse_iso_dt(health.get("latestAt"))
            if latest_dt and (datetime.now(timezone.utc) - latest_dt).total_seconds() > 3600:
                return False
    return True


def build_monitoring_summary(
    config: dict[str, Any], env_name: str, *, window_hours: int = 24
) -> dict[str, Any]:
    env_name = env_name.upper()
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"

    health = ops_store.aggregate_monitor_samples(
        env_name, "health_latency_ms", since=since, db_path=db_path
    )
    pg_conn = ops_store.aggregate_monitor_samples(
        env_name, "pg_connections_total", since=since, db_path=db_path
    )
    log_errors = ops_store.aggregate_monitor_samples(
        env_name, "service_log_errors", since=since, db_path=db_path
    )
    api_metrics = fetch_backend_api_metrics(config, env_name, window="24h")

    syncs, sync_error = _query_sync_logs(config, env_name, days=7)
    sync_failures_24h = sum(
        1
        for s in syncs
        if not s.get("success")
        and s.get("startedAt", "") >= since
    )

    uptime_samples = ops_store.query_monitor_series(
        env_name, "health_reachable", since=since, limit=1000, db_path=db_path
    )
    uptime_pct = 100.0
    if uptime_samples:
        ok = sum(1 for s in uptime_samples if float(s.get("value") or 0) >= 1.0)
        uptime_pct = round(100.0 * ok / len(uptime_samples), 1)

    latest_reachable = 1.0
    if uptime_samples:
        latest_reachable = float(uptime_samples[-1].get("value") or 0)

    data_fresh = _is_data_fresh(config, env_name)

    return {
        "environment": env_name,
        "windowHours": window_hours,
        "since": since,
        "health": health,
        "uptimePct": uptime_pct,
        "latestReachable": latest_reachable >= 1.0,
        "dataFresh": data_fresh,
        "postgres": {"connections": pg_conn},
        "logErrors": log_errors,
        "api": api_metrics,
        "syncFailures24h": sync_failures_24h,
        "syncQueryError": sync_error,
    }


def build_monitoring_series(
    config: dict[str, Any],
    env_name: str,
    metric_key: str,
    *,
    hours: int = 168,
    center: str | None = None,
) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    now = datetime.now(timezone.utc)
    since: str
    until: str | None = None
    if center:
        center_dt = _parse_iso_dt(center)
        if center_dt:
            since = (center_dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            until = (center_dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        else:
            since = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            until = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    else:
        since = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        until = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    # Cap leitura bruta: ~1/min, mas não varre 10k em janelas longas sem downsample antecipado
    raw_limit = min(max(int(hours) * 30 + 50, 200), 3600 if hours >= 48 else 5000)
    points_raw = ops_store.query_monitor_series(
        env_name.upper(),
        metric_key,
        since=since,
        until=until,
        limit=raw_limit,
        db_path=db_path,
    )
    points_dicts = [
        {"t": p["recorded_at"], "v": p["value"], "labels": p.get("labels") or {}}
        for p in points_raw
    ]
    max_points = 360 if hours >= 48 else 800
    if not center and len(points_dicts) > max_points:
        points_dicts = _downsample_series_points(points_dicts, max_points=max_points)
    last_sample_at = points_dicts[-1]["t"] if points_dicts else None
    return {
        "environment": env_name.upper(),
        "metricKey": metric_key,
        "since": since,
        "until": until,
        "windowFrom": since,
        "windowTo": until or now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "center": center,
        "lastSampleAt": last_sample_at,
        "points": points_dicts,
        "pointCount": len(points_dicts),
    }


def build_monitoring_events(
    config: dict[str, Any],
    env_name: str | None = None,
    *,
    limit: int = 100,
    category: str | None = None,
    severity: str | None = None,
    hours: int | None = None,
) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    retention = int(get_monitoring_settings(config).get("retentionDays") or 7)
    since = _iso_days_ago(retention)
    if hours is not None:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    events = ops_store.query_monitor_events(
        env_name.upper() if env_name else None,
        since=since,
        category=category,
        severity=severity,
        limit=limit,
        db_path=db_path,
    )
    enriched = [_enrich_monitor_event(dict(e), config) for e in events]
    return {
        "events": enriched,
        "retentionDays": retention,
    }


def build_monitoring_event_detail(
    config: dict[str, Any], env_name: str, event_id: int
) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    event = ops_store.get_monitor_event(event_id, db_path=db_path)
    if not event or str(event.get("environment") or "").upper() != env_name.upper():
        return {"error": "Evento nao encontrado"}
    enriched = _enrich_monitor_event(dict(event), config)
    recorded = enriched.get("recorded_at")
    category = str(enriched.get("category") or "").lower()
    title = str(enriched.get("title") or "")
    metric_key = "health_latency_ms"
    if category in ("logs", "log"):
        metric_key = "service_log_errors"
    series = build_monitoring_series(
        config, env_name, metric_key, hours=1, center=recorded
    )
    related: list[dict[str, Any]] = []
    since_dt = _parse_iso_dt(recorded)
    if since_dt:
        rel_since = (since_dt - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        rel_until = (since_dt + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        related = ops_store.query_monitor_events(
            env_name.upper(),
            since=rel_since,
            until=rel_until,
            limit=20,
            db_path=db_path,
        )
        related = [r for r in related if int(r.get("id") or 0) != int(event_id)]

    offline = None
    if "offline" in title.lower() or (
        category == "availability" and str(enriched.get("severity") or "").lower() == "critical"
    ):
        offline = _compute_offline_window(ops_store, db_path, env_name, recorded)
        if offline:
            enriched["offlineFrom"] = offline.get("offlineFrom")
            enriched["offlineUntil"] = offline.get("offlineUntil")
            enriched["offlineDurationSec"] = offline.get("offlineDurationSec")
            enriched["offlineDurationLabel"] = offline.get("offlineDurationLabel")
            enriched["offlineOngoing"] = offline.get("ongoing")

    nearby_logs: list[dict[str, Any]] = []
    if since_dt and (
        "offline" in title.lower()
        or category in ("availability", "logs", "log")
        or "pico" in title.lower()
        or "erro" in title.lower()
    ):
        log_since = (since_dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        log_until = (since_dt + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        try:
            nearby_logs = _nearby_service_logs(
                ops_store,
                db_path,
                env_name,
                since=log_since,
                until=log_until,
                limit=30,
                config=config,
            )
        except Exception:
            nearby_logs = []

    return {
        "event": enriched,
        "series": series,
        "relatedEvents": [_enrich_monitor_event(dict(r), config) for r in related],
        "offline": offline,
        "nearbyLogs": nearby_logs,
    }


def _normalize_event_group_key(event: dict[str, Any]) -> str:
    env = str(event.get("environment") or "").upper()
    category = str(event.get("category") or "").lower()
    title = str(event.get("title") or "")
    if "Pico" in title and "latencia" in title.lower():
        title = "Pico de latencia health"
    return f"{env}:{category}:{title}"


def _enrich_group_with_offline(
    group: dict[str, Any], ops_store, db_path: Path
) -> dict[str, Any]:
    title = str(group.get("title") or "")
    if "offline" not in title.lower():
        return group
    env = str(group.get("environment") or "")
    around = group.get("lastAt") or group.get("firstAt")
    offline = _compute_offline_window(ops_store, db_path, env, around)
    if offline:
        group["offlineFrom"] = offline.get("offlineFrom")
        group["offlineUntil"] = offline.get("offlineUntil")
        group["offlineDurationSec"] = offline.get("offlineDurationSec")
        group["offlineDurationLabel"] = offline.get("offlineDurationLabel")
        group["offlineOngoing"] = offline.get("ongoing")
    return group


def build_monitoring_grouped_events(
    config: dict[str, Any],
    *,
    env_name: str | None = None,
    hours: int = 24,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    raw = build_monitoring_events(
        config, env_name, limit=500, hours=hours
    )
    events = raw.get("events") or []
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        key = _normalize_event_group_key(event)
        bucket = groups.get(key)
        if not bucket:
            groups[key] = {
                "key": key,
                "environment": event.get("environment"),
                "category": event.get("category"),
                "severity": event.get("severity"),
                "title": event.get("title"),
                "count": 1,
                "firstAt": event.get("recorded_at"),
                "lastAt": event.get("recorded_at"),
                "sampleEventId": event.get("id"),
                "sampleEvent": event,
            }
            continue
        bucket["count"] += 1
        recorded = event.get("recorded_at")
        if recorded and (not bucket["firstAt"] or recorded < bucket["firstAt"]):
            bucket["firstAt"] = recorded
        if recorded and (not bucket["lastAt"] or recorded > bucket["lastAt"]):
            bucket["lastAt"] = recorded
        sev_rank = {"critical": 0, "warn": 1, "info": 2}
        if sev_rank.get(str(event.get("severity")).lower(), 9) < sev_rank.get(
            str(bucket.get("severity")).lower(), 9
        ):
            bucket["severity"] = event.get("severity")
            bucket["sampleEventId"] = event.get("id")
            bucket["sampleEvent"] = event
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    grouped = sorted(
        (_enrich_group_with_offline(g, ops_store, db_path) for g in groups.values()),
        key=lambda g: (g.get("lastAt") or ""),
        reverse=True,
    )
    return {
        "groups": grouped,
        "hours": hours,
        "totalEvents": len(events),
        "lite": False,
        "timingMs": round((time.perf_counter() - t0) * 1000, 1),
    }


def build_monitoring_uptime_days(
    config: dict[str, Any],
    env_name: str,
    *,
    days: int = 7,
) -> dict[str, Any]:
    """Daily uptime bars (GitHub-status style) from health_reachable samples."""
    env_name = env_name.upper()
    days = max(1, min(int(days), 30))
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    settings = get_monitoring_settings(config)
    slos = settings.get("slos") or _DEFAULT_MONITORING["slos"]
    warn_ms = float(slos.get("healthP95WarnMs") or 2000)

    now = datetime.now(timezone.utc)
    since_dt = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    reachable = ops_store.query_monitor_series(
        env_name, "health_reachable", since=since, limit=10000, db_path=db_path
    )
    latency = ops_store.query_monitor_series(
        env_name, "health_latency_ms", since=since, limit=10000, db_path=db_path
    )
    events = ops_store.query_monitor_events(
        env_name, since=since, limit=500, db_path=db_path
    )

    by_day: dict[str, dict[str, Any]] = {}
    for i in range(days):
        day = (since_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        by_day[day] = {
            "date": day,
            "ok": 0,
            "total": 0,
            "latencies": [],
            "incidentCount": 0,
            "hasOffline": False,
            "hasWarn": False,
        }

    for s in reachable:
        dt = _parse_iso_dt(s.get("recorded_at"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        bucket = by_day.get(day)
        if not bucket:
            continue
        bucket["total"] += 1
        if float(s.get("value") or 0) >= 1.0:
            bucket["ok"] += 1
        else:
            bucket["hasOffline"] = True

    for s in latency:
        dt = _parse_iso_dt(s.get("recorded_at"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        bucket = by_day.get(day)
        if not bucket:
            continue
        try:
            bucket["latencies"].append(float(s.get("value") or 0))
        except (TypeError, ValueError):
            pass

    for e in events:
        dt = _parse_iso_dt(e.get("recorded_at"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        bucket = by_day.get(day)
        if not bucket:
            continue
        bucket["incidentCount"] += 1
        sev = str(e.get("severity") or "").lower()
        title = str(e.get("title") or "").lower()
        # Vermelho só para queda; eventos de latência contam como warning (amarelo).
        if "offline" in title:
            bucket["hasOffline"] = True
        elif sev in ("critical", "warn", "warning") or "latencia" in title or "lento" in title:
            bucket["hasWarn"] = True

    day_list: list[dict[str, Any]] = []
    uptime_ok = 0
    uptime_total = 0
    for day in sorted(by_day.keys()):
        b = by_day[day]
        total = b["total"]
        ok = b["ok"]
        uptime_pct = round(100.0 * ok / total, 2) if total else None
        lats = b["latencies"]
        p95 = None
        if lats:
            ordered = sorted(lats)
            idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
            p95 = round(ordered[idx], 1)
            if p95 >= warn_ms:
                b["hasWarn"] = True

        # Vermelho (major) só com queda real; lentidão fica amarela (degraded).
        if b["hasOffline"] or (uptime_pct is not None and uptime_pct < 99.0):
            status = "major"
        elif b["hasWarn"] or (uptime_pct is not None and uptime_pct < 99.9):
            status = "degraded"
        elif total == 0:
            status = "none"
        else:
            status = "ok"

        if total:
            uptime_ok += ok
            uptime_total += total

        day_list.append(
            {
                "date": day,
                "status": status,
                "uptimePct": uptime_pct,
                "p95Ms": p95,
                "samples": total,
                "incidentCount": b["incidentCount"],
            }
        )

    period_uptime = round(100.0 * uptime_ok / uptime_total, 2) if uptime_total else None
    return {
        "environment": env_name,
        "days": days,
        "since": since,
        "uptimePct": period_uptime,
        "dayBars": day_list,
    }


def build_monitoring_uptime_hours(
    config: dict[str, Any],
    env_name: str,
    *,
    date: str,
) -> dict[str, Any]:
    """Hourly breakdown for a calendar day (drill-down from status bars)."""
    env_name = env_name.upper()
    try:
        day_start = datetime.strptime(date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return {"error": "Data invalida (use YYYY-MM-DD)", "environment": env_name}
    day_end = day_start + timedelta(days=1)
    since = day_start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    until = day_end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    settings = get_monitoring_settings(config)
    slos = settings.get("slos") or _DEFAULT_MONITORING["slos"]
    warn_ms = float(slos.get("healthP95WarnMs") or 2000)

    reachable = ops_store.query_monitor_series(
        env_name, "health_reachable", since=since, until=until, limit=10000, db_path=db_path
    )
    latency = ops_store.query_monitor_series(
        env_name, "health_latency_ms", since=since, until=until, limit=10000, db_path=db_path
    )
    events = ops_store.query_monitor_events(
        env_name, since=since, until=until, limit=500, db_path=db_path
    )

    by_hour: dict[int, dict[str, Any]] = {
        h: {
            "hour": h,
            "label": f"{h:02d}:00",
            "ok": 0,
            "total": 0,
            "latencies": [],
            "incidentCount": 0,
            "eventTitles": [],
        }
        for h in range(24)
    }

    for s in reachable:
        dt = _parse_iso_dt(s.get("recorded_at"))
        if not dt:
            continue
        bucket = by_hour.get(dt.hour)
        if not bucket:
            continue
        bucket["total"] += 1
        if float(s.get("value") or 0) >= 1.0:
            bucket["ok"] += 1

    for s in latency:
        dt = _parse_iso_dt(s.get("recorded_at"))
        if not dt:
            continue
        bucket = by_hour.get(dt.hour)
        if not bucket:
            continue
        try:
            bucket["latencies"].append(float(s.get("value") or 0))
        except (TypeError, ValueError):
            pass

    for e in events:
        dt = _parse_iso_dt(e.get("recorded_at"))
        if not dt:
            continue
        bucket = by_hour.get(dt.hour)
        if not bucket:
            continue
        bucket["incidentCount"] += 1
        title = str(e.get("title") or "")
        if title and title not in bucket["eventTitles"] and len(bucket["eventTitles"]) < 5:
            bucket["eventTitles"].append(title)

    hour_bars: list[dict[str, Any]] = []
    series_points: list[dict[str, Any]] = []
    for h in range(24):
        b = by_hour[h]
        total = b["total"]
        ok = b["ok"]
        uptime_pct = round(100.0 * ok / total, 2) if total else None
        lats = b["latencies"]
        avg_ms = round(sum(lats) / len(lats), 1) if lats else None
        max_ms = round(max(lats), 1) if lats else None
        p95 = None
        if lats:
            ordered = sorted(lats)
            idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
            p95 = round(ordered[idx], 1)

        if total == 0 and not lats:
            status = "none"
        elif uptime_pct is not None and uptime_pct < 99.0:
            # Vermelho só quando a hora teve queda real (unreachable).
            status = "major"
        elif (
            (p95 is not None and p95 >= warn_ms)
            or (max_ms is not None and max_ms >= warn_ms)
            or b["incidentCount"] > 0
        ):
            status = "degraded"
        else:
            status = "ok"

        hour_iso = (day_start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        hour_bars.append(
            {
                "hour": h,
                "label": b["label"],
                "iso": hour_iso,
                "status": status,
                "uptimePct": uptime_pct,
                "avgMs": avg_ms,
                "maxMs": max_ms,
                "p95Ms": p95,
                "samples": total or len(lats),
                "incidentCount": b["incidentCount"],
                "eventTitles": b["eventTitles"],
            }
        )
        if avg_ms is not None:
            series_points.append({"t": hour_iso, "v": avg_ms, "labels": {"hour": h}})

    return {
        "environment": env_name,
        "date": date[:10],
        "since": since,
        "until": until,
        "sloWarnMs": warn_ms,
        "hourBars": hour_bars,
        "series": {
            "environment": env_name,
            "metricKey": "health_latency_ms_hourly_avg",
            "points": series_points,
            "windowFrom": since,
            "windowTo": until,
            "lastSampleAt": series_points[-1]["t"] if series_points else None,
        },
    }


def clear_monitoring_events(config: dict[str, Any]) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    deleted = ops_store.clear_monitor_events(db_path=db_path)
    _RECENT_EVENT_KEYS.clear()
    return {"ok": True, "deleted": deleted}


def build_monitoring_syncs(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    days = int(get_monitoring_settings(config).get("retentionDays") or 7)
    syncs, error = _query_sync_logs(config, env_name, days=days)
    payload: dict[str, Any] = {
        "environment": env_name.upper(),
        "syncs": syncs,
        "retentionDays": days,
    }
    if error:
        payload["error"] = error
    return payload


def build_monitoring_service_logs(
    config: dict[str, Any],
    env_name: str,
    *,
    since: str | None = None,
    pattern: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    # Treat blank pattern as "no text filter".
    if pattern is not None and not str(pattern).strip():
        pattern = None

    cache_key = json.dumps(
        {"env": env_name.upper(), "since": since, "pattern": pattern or "", "limit": limit},
        sort_keys=True,
    )
    now = time.time()
    cached = _LOGS_CACHE.get(cache_key)
    if cached and now - cached[0] < _LOGS_CACHE_TTL_SEC:
        hit = dict(cached[1])
        hit["cacheHit"] = True
        hit["timingMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return hit

    # Fonte principal: arquivos em logDir (PPLID_{ENV}.log / backend.err / …)
    file_lines = _query_service_logs_from_files(
        config, env_name, since=since, pattern=pattern, limit=limit
    )
    db_lines: list[dict[str, Any]] = []
    # Só consulta SQLite se disco não trouxe linhas suficientes
    if len(file_lines) < min(20, limit):
        ops_store = _import_ops_store()
        db_path = resolve_ops_store_path(config)
        try:
            db_lines = ops_store.query_service_log_lines(
                env_name.upper(),
                since=since,
                pattern=pattern,
                limit=limit,
                db_path=db_path,
            )
        except Exception:
            db_lines = []

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ln in list(file_lines) + list(db_lines):
        key = f"{ln.get('logged_at')}|{ln.get('service')}|{ln.get('line')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(ln)
    merged.sort(key=lambda r: r.get("logged_at") or "", reverse=True)
    lines = merged[:limit]

    sources = [
        {"service": s, "stream": st, "file": p.name, "exists": p.is_file()}
        for s, st, p in _service_log_sources(config, env_name)
    ]
    result = {
        "environment": env_name.upper(),
        "since": since,
        "pattern": pattern or "",
        "lines": lines,
        "count": len(lines),
        "sources": sources,
        "fromFiles": len(file_lines),
        "fromDb": len(db_lines),
        "cacheHit": False,
        "timingMs": round((time.perf_counter() - t0) * 1000, 1),
    }
    _LOGS_CACHE[cache_key] = (now, result)
    return result


def build_monitoring_api_routes(config: dict[str, Any], env_name: str, window: str = "24h") -> dict[str, Any]:
    t0 = time.perf_counter()
    cache_key = f"{env_name.upper()}:{window}"
    now = time.time()
    cached = _API_ROUTES_CACHE.get(cache_key)
    if cached and now - cached[0] < _API_ROUTES_CACHE_TTL_SEC:
        hit = dict(cached[1])
        hit["cacheHit"] = True
        hit["timingMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return hit
    data = fetch_backend_api_metrics(config, env_name, window=window)
    instrumentation = "unavailable"
    if data.get("error"):
        instrumentation = "unavailable"
    elif data.get("totals", {}).get("requests", 0) > 0:
        instrumentation = "active"
    else:
        instrumentation = "no_traffic"
    result = {
        "environment": env_name.upper(),
        "window": window,
        "instrumentation": instrumentation,
        "cacheHit": False,
        "timingMs": round((time.perf_counter() - t0) * 1000, 1),
        **data,
    }
    _API_ROUTES_CACHE[cache_key] = (now, result)
    return result


def build_monitoring_api_samples(
    config: dict[str, Any],
    env_name: str,
    *,
    at: str,
    radius_minutes: int = 5,
    min_ms: int = 0,
) -> dict[str, Any]:
    """Drill-down: rotas/amostras de API em torno de um ponto do gráfico."""
    env_name = env_name.upper()
    live = fetch_backend_api_samples_around(
        config,
        env_name,
        at,
        radius_minutes=radius_minutes,
        min_ms=min_ms,
    )
    sample_labels: dict[str, Any] = {}
    try:
        ops_store = _import_ops_store()
        db_path = resolve_ops_store_path(config)
        center_dt = _parse_iso_dt(at)
        if center_dt:
            since = (center_dt - timedelta(minutes=max(radius_minutes, 5))).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z"
            until = (center_dt + timedelta(minutes=max(radius_minutes, 5))).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z"
            points = ops_store.query_monitor_series(
                env_name,
                "api_avg_ms",
                since=since,
                until=until,
                limit=20,
                db_path=db_path,
            )
            if points:
                center_ts = center_dt.timestamp()
                nearest = min(
                    points,
                    key=lambda p: abs(
                        (_parse_iso_dt(p.get("recorded_at")) or center_dt).timestamp() - center_ts
                    ),
                )
                sample_labels = nearest.get("labels") or {}
    except Exception:
        sample_labels = {}

    result = {
        "environment": env_name,
        "at": at,
        "radiusMinutes": radius_minutes,
        "minMs": min_ms,
        "collectorTopRoutes": sample_labels.get("topRoutes") or [],
        "collectorRequests": sample_labels.get("requests"),
        "collectorWindow": sample_labels.get("window"),
    }
    if live.get("error"):
        result["liveError"] = live.get("error")
        result["slowRoutes"] = result["collectorTopRoutes"]
        result["samples"] = []
        result["totals"] = {"requests": 0, "avgMs": 0, "maxMs": 0, "errors5xx": 0}
        result["source"] = "collector_labels" if result["collectorTopRoutes"] else "unavailable"
    else:
        result.update(
            {
                "slowRoutes": live.get("slowRoutes") or [],
                "samples": live.get("samples") or [],
                "totals": live.get("totals") or {},
                "since": live.get("since"),
                "until": live.get("until"),
                "source": "live",
            }
        )
    return result


def build_monitoring_deploy_stats(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = _iso_days_ago(int(get_monitoring_settings(config).get("retentionDays") or 7))
    try:
        with ops_store.get_connection(db_path) as conn:
            runs = conn.execute(
                """
                SELECT run_id, status, result, started_at, finished_at, failed_step, last_error
                FROM deploy_runs
                WHERE environment=? AND started_at >= ?
                ORDER BY started_at DESC
                LIMIT 50
                """,
                (env_name.upper(), since),
            ).fetchall()
            steps = conn.execute(
                """
                SELECT step_id, label, status, duration_sec, error, finished_at
                FROM deploy_steps
                WHERE environment=? AND finished_at >= ?
                ORDER BY finished_at DESC
                LIMIT 200
                """,
                (env_name.upper(), since),
            ).fetchall()
    except Exception:
        return {"environment": env_name.upper(), "runs": [], "steps": []}

    runs_list = [dict(r) for r in runs]
    aggregates = _aggregate_deploy_runs(runs_list, hours=24)
    aggregates7d = _aggregate_deploy_runs(runs_list, hours=24 * 7)

    return {
        "environment": env_name.upper(),
        "runs": runs_list,
        "steps": [dict(s) for s in steps],
        "aggregates24h": aggregates,
        "aggregates7d": aggregates7d,
    }


def _count_sync_failure_events_24h(
    ops_store, db_path: Path, env_name: str, since: str
) -> int:
    try:
        events = ops_store.query_monitor_events(
            env_name.upper(),
            since=since,
            category="sync",
            limit=200,
            db_path=db_path,
        )
        return sum(
            1
            for event in events
            if str(event.get("severity") or "").lower() in ("warn", "critical")
        )
    except Exception:
        return 0


def build_monitoring_summary_lite(
    config: dict[str, Any],
    env_name: str,
    *,
    window_hours: int = 24,
    include_api: bool = False,
    api_timeout: float = 2.0,
) -> dict[str, Any]:
    """Resumo rápido: SQLite only, sem consultas PG de sync."""
    env_name = env_name.upper()
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"

    health = ops_store.aggregate_monitor_samples(
        env_name, "health_latency_ms", since=since, db_path=db_path
    )
    pg_conn = ops_store.aggregate_monitor_samples(
        env_name, "pg_connections_total", since=since, db_path=db_path
    )
    log_errors = ops_store.aggregate_monitor_samples(
        env_name, "service_log_errors", since=since, db_path=db_path
    )
    reach_agg = ops_store.aggregate_monitor_samples(
        env_name, "health_reachable", since=since, db_path=db_path
    )
    uptime_pct = round(float(reach_agg.get("avg") or 0) * 100, 1)
    latest_reachable = float(reach_agg.get("latest") or 0) >= 1.0

    api_metrics: dict[str, Any] = {"deferred": True}
    if include_api:
        api_metrics = fetch_backend_api_metrics(
            config, env_name, window="24h", timeout=api_timeout
        )

    return {
        "environment": env_name,
        "windowHours": window_hours,
        "since": since,
        "health": health,
        "uptimePct": uptime_pct,
        "latestReachable": latest_reachable,
        "dataFresh": _is_data_fresh(config),
        "postgres": {"connections": pg_conn},
        "logErrors": log_errors,
        "api": api_metrics,
        "syncFailures24h": _count_sync_failure_events_24h(
            ops_store, db_path, env_name, since
        ),
        "syncQueryError": None,
        "lite": True,
    }


def _downsample_series_points(points: list[dict[str, Any]], max_points: int = 360) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def build_monitoring_grouped_events_lite(
    config: dict[str, Any],
    *,
    env_name: str | None = None,
    hours: int = 24,
    limit: int = 200,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    events = ops_store.query_monitor_events(
        env_name.upper() if env_name else None,
        since=since,
        limit=limit,
        db_path=db_path,
    )
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        item = dict(event)
        key = _normalize_event_group_key(item)
        bucket = groups.get(key)
        if not bucket:
            groups[key] = {
                "key": key,
                "environment": item.get("environment"),
                "category": item.get("category"),
                "severity": item.get("severity"),
                "title": item.get("title"),
                "count": 1,
                "firstAt": item.get("recorded_at"),
                "lastAt": item.get("recorded_at"),
                "sampleEventId": item.get("id"),
                "sampleEvent": item,
            }
            continue
        bucket["count"] += 1
        recorded = item.get("recorded_at")
        if recorded and (not bucket["firstAt"] or recorded < bucket["firstAt"]):
            bucket["firstAt"] = recorded
        if recorded and (not bucket["lastAt"] or recorded > bucket["lastAt"]):
            bucket["lastAt"] = recorded
        sev_rank = {"critical": 0, "warn": 1, "info": 2}
        if sev_rank.get(str(item.get("severity")).lower(), 9) < sev_rank.get(
            str(bucket.get("severity")).lower(), 9
        ):
            bucket["severity"] = item.get("severity")
            bucket["sampleEventId"] = item.get("id")
            bucket["sampleEvent"] = item
    grouped = sorted(
        (_enrich_group_with_offline(g, ops_store, db_path) for g in groups.values()),
        key=lambda g: (g.get("lastAt") or ""),
        reverse=True,
    )
    return {
        "groups": grouped,
        "hours": hours,
        "totalEvents": len(events),
        "lite": True,
        "timingMs": round((time.perf_counter() - t0) * 1000, 1),
    }


def build_monitoring_dashboard(
    config: dict[str, Any],
    *,
    env_names: list[str],
    tab: str = "summary",
    include_health_series: bool = False,
    include_deploy: bool = False,
    include_api: bool = False,
    event_hours: int = 24,
    series_hours: int = 168,
    event_limit: int = 100,
    severity: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    env_names = [e.upper() for e in env_names if e.upper() in ENV_ORDER]
    if not env_names:
        env_names = list(ENV_ORDER)

    cache_key = json.dumps(
        {
            "envs": env_names,
            "tab": tab,
            "hs": include_health_series,
            "dep": include_deploy,
            "api": include_api,
            "eh": event_hours,
            "sh": series_hours,
            "el": event_limit,
            "sev": severity,
            "cat": category,
        },
        sort_keys=True,
    )
    t0 = time.perf_counter()
    now = time.time()
    cached = _DASHBOARD_CACHE.get(cache_key)
    if cached and now - cached[0] < _DASHBOARD_CACHE_TTL_SEC:
        hit = dict(cached[1])
        hit["timingMs"] = round((time.perf_counter() - t0) * 1000, 1)
        hit["cacheHit"] = True
        return hit

    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since_events = (datetime.now(timezone.utc) - timedelta(hours=event_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"

    result: dict[str, Any] = {
        "config": build_monitoring_config(config),
        "tab": tab,
        "envSummaries": [],
        "healthSeries": {},
        "deploys": {},
        "groupedEvents": [],
        "alertGroups": [],
        "events": [],
        "generatedAt": _utc_now_iso(),
        "cacheHit": False,
    }

    futures_map: dict[Any, str] = {}
    with ThreadPoolExecutor(max_workers=min(12, max(4, len(env_names) * 3))) as pool:
        if tab in ("summary", "latency"):
            for env in env_names:
                fut = pool.submit(
                    build_monitoring_summary_lite,
                    config,
                    env,
                    include_api=include_api,
                    api_timeout=2.0,
                )
                futures_map[fut] = f"summary:{env}"

        if include_health_series and tab in ("summary", "latency"):
            for env in env_names:
                fut = pool.submit(
                    build_monitoring_series,
                    config,
                    env,
                    "health_latency_ms",
                    hours=series_hours,
                )
                futures_map[fut] = f"series:{env}"

        if include_deploy and tab == "summary":
            for env in env_names:
                fut = pool.submit(build_monitoring_deploy_stats, config, env)
                futures_map[fut] = f"deploy:{env}"

        if tab in ("summary", "incidents"):
            fut = pool.submit(
                build_monitoring_grouped_events_lite,
                config,
                hours=event_hours,
                limit=200,
            )
            futures_map[fut] = "grouped"

        if tab == "incidents":
            for env in env_names:
                fut = pool.submit(
                    ops_store.query_monitor_events,
                    env,
                    since=since_events,
                    category=category,
                    severity=severity,
                    limit=event_limit,
                    db_path=db_path,
                )
                futures_map[fut] = f"events:{env}"

        for future in as_completed(futures_map):
            kind = futures_map[future]
            try:
                data = future.result()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("monitoring dashboard task failed (%s): %s", kind, exc)
                continue
            if kind.startswith("summary:"):
                env = kind.split(":", 1)[1]
                result["envSummaries"].append({"env": env, "summary": data})
            elif kind.startswith("series:"):
                env = kind.split(":", 1)[1]
                data["points"] = _downsample_series_points(data.get("points") or [])
                result["healthSeries"][env] = data
            elif kind.startswith("deploy:"):
                env = kind.split(":", 1)[1]
                result["deploys"][env] = data
            elif kind == "grouped":
                all_groups = data.get("groups") or []
                result["alertGroups"] = all_groups
                result["groupedEvents"] = [
                    g for g in all_groups if g.get("environment") in env_names
                ]
            elif kind.startswith("events:"):
                env = kind.split(":", 1)[1]
                for row in data or []:
                    item = dict(row)
                    item["environment"] = env
                    result["events"].append(item)

    result["envSummaries"].sort(
        key=lambda item: ENV_ORDER.index(item["env"]) if item["env"] in ENV_ORDER else 99
    )
    result["events"].sort(
        key=lambda item: str(item.get("recorded_at") or ""), reverse=True
    )
    result["events"] = result["events"][:200]
    result["timingMs"] = round((time.perf_counter() - t0) * 1000, 1)

    _DASHBOARD_CACHE[cache_key] = (now, result)
    return result
