"""
Coleta e APIs de monitoramento do ops-console.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
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


def fetch_backend_api_metrics(config: dict[str, Any], env_name: str, window: str = "24h") -> dict[str, Any]:
    env_cfg = config.get(env_name, {})
    port = int(env_cfg.get("backendPort") or 0)
    if not port:
        return {"error": "Porta backend nao configurada"}
    url = f"http://127.0.0.1:{port}/api/v1/ops-metrics/summary/?window={window}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5.0) as response:
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
) -> None:
    key = f"{env_name.upper()}:{category}:{title}"
    now = time.time()
    if key in _RECENT_EVENT_KEYS and now - _RECENT_EVENT_KEYS[key] < _EVENT_DEDUPE_SEC:
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
        return
    if latency_ms > 3000:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "availability",
            f"Health lento ({int(latency_ms)}ms)",
            "Latencia acima de 3s",
        )
        return
    since = _iso_days_ago(1)
    agg = ops_store.aggregate_monitor_samples(
        env_name, "health_latency_ms", since=since, db_path=db_path
    )
    avg = float(agg.get("avg") or 0)
    if avg > 0 and latency_ms > avg * 2 and latency_ms > 500:
        _record_event(
            ops_store,
            db_path,
            env_name,
            "warn",
            "availability",
            f"Pico de latencia health ({int(latency_ms)}ms)",
            f"Media recente: {avg:.0f}ms",
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


def _query_sync_logs(config: dict[str, Any], env_name: str, days: int = 7) -> list[dict[str, Any]]:
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
    except Exception:
        return []

    results.sort(key=lambda r: r.get("startedAt") or "", reverse=True)
    return results[:200]


def _check_sync_anomalies(
    config: dict[str, Any], env_name: str, ops_store, db_path: Path
) -> None:
    syncs = _query_sync_logs(config, env_name, days=1)
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
    global _LAST_PURGE_AT
    settings = get_monitoring_settings(config)
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    try:
        ops_store.init_store(db_path)
    except Exception:
        pass

    now = time.time()
    if now - _LAST_PURGE_AT > 24 * 3600:
        try:
            ops_store.purge_monitor_data(
                retention_days=int(settings.get("retentionDays") or 7), db_path=db_path
            )
        except Exception:
            pass
        _LAST_PURGE_AT = now

    categories = settings.get("enabledCategories") or {}
    pg_interval = float(settings.get("pgSnapshotIntervalSec") or 300)

    for env_name in ENV_ORDER:
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


def _collector_loop(config: dict[str, Any]) -> None:
    settings = get_monitoring_settings(config)
    interval = max(15, int(settings.get("collectorIntervalSec") or 60))
    while not _COLLECTOR_STOP.wait(interval):
        try:
            _collector_tick(config)
        except Exception:
            pass


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


def _enrich_monitor_event(event: dict[str, Any]) -> dict[str, Any]:
    category = str(event.get("category") or "").lower()
    env = str(event.get("environment") or "").upper()
    title = str(event.get("title") or "")
    enriched = dict(event)
    action = "Investigar no painel"
    link = f"#/monitoring"
    if category in ("logs", "log"):
        action = "Verificar logs do serviço"
        link = f"#/deploy?env={env}" if env else "#/deploy"
    elif category == "sync":
        action = "Ver histórico de syncs"
    elif category in ("health", "availability"):
        action = "Ver tendência de latência"
    elif category == "deploy":
        action = "Ver pipeline de deploy"
        link = f"#/deploy?env={env}" if env else "#/deploy"
    elif "pico" in title.lower() or "erro" in title.lower():
        action = "Ver logs filtrados e correlacionar com deploy recente"
    enriched["recommendedAction"] = action
    enriched["investigationLink"] = link
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

    syncs = _query_sync_logs(config, env_name, days=7)
    sync_failures_24h = sum(
        1
        for s in syncs
        if not s.get("success")
        and s.get("startedAt", "") >= since
    )

    uptime_samples = ops_store.query_monitor_series(
        env_name, "health_reachable", since=since, limit=5000, db_path=db_path
    )
    uptime_pct = 100.0
    if uptime_samples:
        ok = sum(1 for s in uptime_samples if float(s.get("value") or 0) >= 1.0)
        uptime_pct = round(100.0 * ok / len(uptime_samples), 1)

    return {
        "environment": env_name,
        "windowHours": window_hours,
        "since": since,
        "health": health,
        "uptimePct": uptime_pct,
        "postgres": {"connections": pg_conn},
        "logErrors": log_errors,
        "api": api_metrics,
        "syncFailures24h": sync_failures_24h,
    }


def build_monitoring_series(
    config: dict[str, Any],
    env_name: str,
    metric_key: str,
    *,
    hours: int = 168,
) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    points = ops_store.query_monitor_series(
        env_name.upper(), metric_key, since=since, limit=5000, db_path=db_path
    )
    return {
        "environment": env_name.upper(),
        "metricKey": metric_key,
        "since": since,
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
) -> dict[str, Any]:
    ops_store = _import_ops_store()
    db_path = resolve_ops_store_path(config)
    since = _iso_days_ago(int(get_monitoring_settings(config).get("retentionDays") or 7))
    events = ops_store.query_monitor_events(
        env_name.upper() if env_name else None,
        since=since,
        limit=limit,
        db_path=db_path,
    )
    enriched = [_enrich_monitor_event(dict(e)) for e in events]
    return {
        "events": enriched,
        "retentionDays": get_monitoring_settings(config).get("retentionDays", 7),
    }


def build_monitoring_syncs(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    days = int(get_monitoring_settings(config).get("retentionDays") or 7)
    syncs = _query_sync_logs(config, env_name, days=days)
    return {"environment": env_name.upper(), "syncs": syncs, "retentionDays": days}


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
