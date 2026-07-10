"""
Coleta e APIs de monitoramento do ops-console.
"""
from __future__ import annotations

import json
import logging
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
        "healthP95WarnMs": 400,
        "healthP95CriticalMs": 600,
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
_COLLECTOR_STOP = threading.Event()
_LAST_PG_SNAPSHOT: dict[str, float] = {}
_LAST_PURGE_AT = 0.0
_SERVICE_LOG_OFFSETS: dict[str, int] = {}
_CONFIG_CACHE: dict[str, Any] | None = None
_RECENT_EVENT_KEYS: dict[str, float] = {}
_EVENT_DEDUPE_SEC = 900
_LATENCY_SPIKE_DEDUPE_SEC = 3600
_LATENCY_SPIKE_SAMPLES: dict[str, list[float]] = {}
_LAST_COLLECTOR_TICK_AT: str | None = None
_COLLECTOR_ERRORS: list[dict[str, Any]] = []
_COLLECTOR_MAX_ERRORS = 20
_DASHBOARD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DASHBOARD_CACHE_TTL_SEC = 5.0

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


def _detect_health_spike(
    ops_store,
    db_path: Path,
    env_name: str,
    latency_ms: float,
    reachable: bool,
) -> None:
    settings = _CONFIG_CACHE or _DEFAULT_MONITORING
    slos = settings.get("slos") or _DEFAULT_MONITORING["slos"]
    warn_ms = float(slos.get("healthP95WarnMs") or 400)
    critical_ms = float(slos.get("healthP95CriticalMs") or 600)

    if not reachable:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "critical",
            "availability",
            "Backend offline",
            "Health check falhou",
        )
        _LATENCY_SPIKE_SAMPLES.pop(env_name.upper(), None)
        return

    since = _iso_days_ago(1)
    agg = ops_store.aggregate_monitor_samples(
        env_name, "health_latency_ms", since=since, db_path=db_path
    )
    avg = float(agg.get("avg") or 0)
    p95 = float(agg.get("p95") or 0)

    if p95 >= critical_ms:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "critical",
            "availability",
            f"Latencia p95 elevada ({int(p95)}ms)",
            f"p95 24h acima de {int(critical_ms)}ms",
        )
    elif p95 >= warn_ms:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "availability",
            f"Latencia p95 acima do SLO ({int(p95)}ms)",
            f"SLO: <= {int(warn_ms)}ms",
            dedupe_sec=_LATENCY_SPIKE_DEDUPE_SEC,
        )

    spike_key = env_name.upper()
    history = _LATENCY_SPIKE_SAMPLES.setdefault(spike_key, [])
    history.append(latency_ms)
    if len(history) > 3:
        history.pop(0)

    spike_threshold = max(500.0, avg * 2 if avg > 0 else 500.0)
    consecutive_spike = len(history) >= 3 and all(v > spike_threshold for v in history)

    if latency_ms > 3000:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "availability",
            f"Health lento ({int(latency_ms)}ms)",
            "Latencia acima de 3s",
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
    elif avg > 0 and latency_ms > avg * 2 and latency_ms > 500:
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


def _collect_availability(config: dict[str, Any], env_name: str, ops_store, db_path: Path) -> None:
    env_cfg = config.get(env_name, {})
    backend_port = int(env_cfg.get("backendPort") or 0)
    frontend_port = int(env_cfg.get("frontendPort") or 0)
    health_url = f"http://127.0.0.1:{backend_port}/api/v1/health/" if backend_port else ""
    health = probe_health(health_url) if health_url else {"reachable": False, "durationMs": 0}

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
        1.0 if server_ops.probe_port_listening(backend_port) else 0.0,
        db_path=db_path,
    )
    ops_store.insert_monitor_sample(
        env_name,
        "frontend_port_up",
        1.0 if server_ops.probe_port_listening(frontend_port) else 0.0,
        db_path=db_path,
    )
    _detect_health_spike(
        ops_store,
        db_path,
        env_name,
        float(health.get("durationMs") or 0),
        bool(health.get("reachable")),
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


def _collect_service_log_errors(config: dict[str, Any], env_name: str, ops_store, db_path: Path) -> None:
    key = f"{env_name}"
    since_id = _SERVICE_LOG_OFFSETS.get(key, 0)
    try:
        with ops_store.get_connection(db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, service, stream, line
                FROM service_log_lines
                WHERE environment=? AND id>?
                ORDER BY id ASC
                LIMIT 500
                """,
                (env_name.upper(), since_id),
            ).fetchall()
    except Exception:
        return

    error_count = 0
    max_id = since_id
    for row in rows:
        max_id = max(max_id, int(row["id"]))
        line = str(row["line"] or "").upper()
        if "ERROR" in line or "TRACEBACK" in line or " 500 " in line:
            error_count += 1
    _SERVICE_LOG_OFFSETS[key] = max_id

    if error_count:
        ops_store.insert_monitor_sample(
            env_name, "service_log_errors", float(error_count), db_path=db_path
        )
        if error_count >= 10:
            _record_event(
                ops_store,
                db_path,
                env_name,
                "warn",
                "logs",
                f"Pico de erros nos logs ({error_count} linhas)",
                "Verifique service_log_lines",
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
                            item = dict(row)
                            item["source"] = source
                            if item.get("started_at"):
                                item["startedAt"] = item["started_at"].isoformat()
                            if item.get("finished_at"):
                                item["finishedAt"] = item["finished_at"].isoformat()
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


def start_monitoring_collector(config: dict[str, Any]) -> None:
    global _COLLECTOR_THREAD
    if _COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive():
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
        return {"status": "no_data", "label": "Sem dados", "lastSampleAt": None, "intervalSec": interval}

    latest_dt = _parse_iso_dt(latest_at)
    now = datetime.now(timezone.utc)
    if latest_dt and (now - latest_dt).total_seconds() > interval * 3:
        return {
            "status": "stale",
            "label": "Coleta atrasada",
            "lastSampleAt": latest_at,
            "intervalSec": interval,
        }
    return {
        "status": "ok",
        "label": "Coleta OK",
        "lastSampleAt": latest_at,
        "intervalSec": interval,
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
    since_param = f"&since={urllib.parse.quote(recorded)}" if recorded else ""

    if category in ("logs", "log"):
        return "Verificar logs do serviço", f"#/monitoring/logs?env={env}{since_param}"
    if category == "sync":
        highlight = ""
        if "Sync falhou" in title and "(" in title and ")" in title:
            inner = title.split("(", 1)[1].rsplit(")", 1)[0]
            highlight = f"&highlight={urllib.parse.quote(inner)}"
        return "Ver histórico de syncs", f"#/monitoring/syncs?env={env}{highlight}"
    if category in ("health", "availability"):
        return "Ver tendência de latência", f"#/monitoring/latency?env={env}{at_param}"
    if category == "postgres":
        return "Investigar painel PG", f"#/database/{env}"
    if category == "deploy":
        return "Ver pipeline de deploy", f"#/deploy?env={env}"
    if "pico" in title.lower() or "erro" in title.lower():
        return "Ver logs filtrados", f"#/monitoring/logs?env={env}{since_param}"
    return "Investigar no painel", f"#/monitoring/incidents?env={env}"


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
    since: str
    until: str | None = None
    if center:
        center_dt = _parse_iso_dt(center)
        if center_dt:
            since = (center_dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            until = (center_dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        else:
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z"
    else:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    points = ops_store.query_monitor_series(
        env_name.upper(),
        metric_key,
        since=since,
        until=until,
        limit=min(max(hours * 4, 200), 1200),
        db_path=db_path,
    )
    return {
        "environment": env_name.upper(),
        "metricKey": metric_key,
        "since": since,
        "until": until,
        "center": center,
        "points": [
            {"t": p["recorded_at"], "v": p["value"], "labels": p.get("labels") or {}}
            for p in points
        ],
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
    return {
        "event": enriched,
        "series": series,
        "relatedEvents": [_enrich_monitor_event(dict(r), config) for r in related],
    }


def _normalize_event_group_key(event: dict[str, Any]) -> str:
    env = str(event.get("environment") or "").upper()
    category = str(event.get("category") or "").lower()
    title = str(event.get("title") or "")
    if "Pico" in title and "latencia" in title.lower():
        title = "Pico de latencia health"
    return f"{env}:{category}:{title}"


def build_monitoring_grouped_events(
    config: dict[str, Any],
    *,
    env_name: str | None = None,
    hours: int = 24,
) -> dict[str, Any]:
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
    grouped = sorted(
        groups.values(),
        key=lambda g: (g.get("lastAt") or ""),
        reverse=True,
    )
    return {"groups": grouped, "hours": hours, "totalEvents": len(events)}


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
    pattern: str | None = "ERROR",
    limit: int = 200,
) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    lines = ops_store.query_service_log_lines(
        env_name.upper(),
        since=since,
        pattern=pattern,
        limit=limit,
        db_path=db_path,
    )
    return {
        "environment": env_name.upper(),
        "since": since,
        "pattern": pattern,
        "lines": lines,
    }


def build_monitoring_api_routes(config: dict[str, Any], env_name: str, window: str = "24h") -> dict[str, Any]:
    data = fetch_backend_api_metrics(config, env_name, window=window)
    instrumentation = "unavailable"
    if data.get("error"):
        instrumentation = "unavailable"
    elif data.get("totals", {}).get("requests", 0) > 0:
        instrumentation = "active"
    else:
        instrumentation = "no_traffic"
    return {
        "environment": env_name.upper(),
        "window": window,
        "instrumentation": instrumentation,
        **data,
    }


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
    grouped = sorted(groups.values(), key=lambda g: (g.get("lastAt") or ""), reverse=True)
    return {"groups": grouped, "hours": hours, "totalEvents": len(events)}


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
    now = time.time()
    cached = _DASHBOARD_CACHE.get(cache_key)
    if cached and now - cached[0] < _DASHBOARD_CACHE_TTL_SEC:
        return cached[1]

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
        "events": [],
        "generatedAt": _utc_now_iso(),
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
                result["groupedEvents"] = [
                    g for g in (data.get("groups") or []) if g.get("environment") in env_names
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

    _DASHBOARD_CACHE[cache_key] = (now, result)
    return result
