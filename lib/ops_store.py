"""
SQLite store for PPLID ops logs (deploy runs, service runtime, audit).
WAL mode — concurrent readers do not block writers.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(r"C:\PPLID\ops\data\ops-store.db")
SCHEMA_VERSION = 2

_local = threading.local()

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deploy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    run_id TEXT NOT NULL,
    target_sha TEXT,
    trigger_name TEXT,
    status TEXT,
    started_at TEXT,
    finished_at TEXT,
    result TEXT,
    failed_step TEXT,
    last_error TEXT,
    summary_json TEXT,
    UNIQUE(environment, run_id)
);

CREATE TABLE IF NOT EXISTS deploy_log_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    run_id TEXT NOT NULL,
    log_name TEXT NOT NULL,
    level TEXT,
    message TEXT NOT NULL,
    logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deploy_log_tail
    ON deploy_log_lines(environment, run_id, log_name, id);

CREATE TABLE IF NOT EXISTS deploy_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    label TEXT,
    phase TEXT,
    log_name TEXT,
    status TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_sec INTEGER,
    error TEXT,
    UNIQUE(environment, run_id, step_id)
);

CREATE TABLE IF NOT EXISTS service_log_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    service TEXT NOT NULL,
    stream TEXT NOT NULL,
    line TEXT NOT NULL,
    logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_service_log_tail
    ON service_log_lines(environment, service, stream, id);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    action TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    value REAL NOT NULL,
    labels_json TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monitor_samples_query
    ON monitor_samples(environment, metric_key, recorded_at);

CREATE TABLE IF NOT EXISTS monitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monitor_events_env_time
    ON monitor_events(environment, recorded_at);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS monitor_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    value REAL NOT NULL,
    labels_json TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monitor_samples_query
    ON monitor_samples(environment, metric_key, recorded_at);

CREATE TABLE IF NOT EXISTS monitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monitor_events_env_time
    ON monitor_events(environment, recorded_at);
"""


def resolve_db_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.environ.get("PPLID_OPS_STORE_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        if row:
            return int(row[0])
    except sqlite3.Error:
        pass
    return 0


def _migrate_schema(conn: sqlite3.Connection) -> None:
    current = _get_schema_version(conn)
    if current < 2:
        conn.executescript(SCHEMA_V2_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
            (str(SCHEMA_VERSION),),
        )


def init_store(db_path: Path | None = None) -> Path:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    return path


def _normalize_log_name(log_name: str) -> str:
    return log_name.replace(".log", "") if log_name.endswith(".log") else log_name


def append_deploy_log(
    environment: str,
    run_id: str,
    log_name: str,
    level: str,
    message: str,
    *,
    logged_at: str | None = None,
    db_path: Path | None = None,
) -> int:
    ts = logged_at or _utc_now_iso()
    name = _normalize_log_name(log_name)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO deploy_log_lines(environment, run_id, log_name, level, message, logged_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (environment.upper(), run_id, name, level, message, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def tail_deploy_logs(
    environment: str,
    run_id: str,
    log_name: str | None = None,
    *,
    since_id: int = 0,
    limit: int = 200,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    env = environment.upper()
    limit = max(1, min(limit, 500))
    with get_connection(db_path) as conn:
        if log_name:
            name = _normalize_log_name(log_name)
            rows = conn.execute(
                """
                SELECT id, log_name, level, message, logged_at
                FROM deploy_log_lines
                WHERE environment=? AND run_id=? AND log_name=? AND id>?
                ORDER BY id ASC
                LIMIT ?
                """,
                (env, run_id, name, since_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, log_name, level, message, logged_at
                FROM deploy_log_lines
                WHERE environment=? AND run_id=? AND id>?
                ORDER BY id ASC
                LIMIT ?
                """,
                (env, run_id, since_id, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def tail_deploy_logs_by_files(
    environment: str,
    run_id: str,
    log_names: list[str],
    *,
    since_ids: dict[str, int] | None = None,
    limit: int = 200,
    db_path: Path | None = None,
) -> dict[str, Any]:
    since_ids = since_ids or {}
    out: dict[str, Any] = {}
    max_id = 0
    for raw_name in log_names:
        name = _normalize_log_name(raw_name)
        since = int(since_ids.get(raw_name, since_ids.get(name, 0)))
        lines = tail_deploy_logs(
            environment, run_id, name, since_id=since, limit=limit, db_path=db_path
        )
        formatted_lines = []
        parsed = []
        for row in lines:
            max_id = max(max_id, int(row["id"]))
            text = f"[{row['logged_at']}] [{row['level']}] {row['message']}"
            formatted_lines.append(text)
            parsed.append({
                "level": row["level"],
                "message": row["message"],
                "text": row["message"],
                "time": row["logged_at"],
                "timestamp": row["logged_at"],
                "lineId": row["id"],
            })
        out[raw_name] = {"lines": formatted_lines, "parsed": parsed, "lastLineId": max_id}
    return out


def save_deploy_steps(
    environment: str,
    run_id: str,
    steps: list[dict[str, Any]] | dict[str, Any],
    *,
    db_path: Path | None = None,
) -> None:
    if isinstance(steps, dict):
        steps = [steps] if steps.get("id") else list(steps.values())
    normalized: list[dict[str, Any]] = []
    for step in steps:
        if isinstance(step, dict):
            normalized.append(step)
    steps = normalized
    env = environment.upper()
    with get_connection(db_path) as conn:
        for step in steps:
            conn.execute(
                """
                INSERT INTO deploy_steps(
                    environment, run_id, step_id, label, phase, log_name,
                    status, started_at, finished_at, duration_sec, error
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment, run_id, step_id) DO UPDATE SET
                    label=excluded.label,
                    phase=excluded.phase,
                    log_name=excluded.log_name,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    duration_sec=excluded.duration_sec,
                    error=excluded.error
                """,
                (
                    env,
                    run_id,
                    str(step.get("id", "")),
                    step.get("label"),
                    step.get("phase"),
                    step.get("logFile") or step.get("log_name"),
                    step.get("status"),
                    step.get("startedAt") or step.get("started_at"),
                    step.get("finishedAt") or step.get("finished_at"),
                    step.get("durationSec") or step.get("duration_sec"),
                    step.get("error"),
                ),
            )
        conn.commit()


def get_deploy_steps(
    environment: str,
    run_id: str,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    env = environment.upper()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT step_id AS id, label, phase, log_name AS logFile, status,
                   started_at AS startedAt, finished_at AS finishedAt,
                   duration_sec AS durationSec, error
            FROM deploy_steps
            WHERE environment=? AND run_id=?
            ORDER BY deploy_steps.id ASC
            """,
            (env, run_id),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_deploy_run(
    environment: str,
    run_id: str,
    *,
    target_sha: str | None = None,
    trigger_name: str | None = None,
    status: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    result: str | None = None,
    failed_step: str | None = None,
    last_error: str | None = None,
    summary: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> None:
    env = environment.upper()
    summary_json = json.dumps(summary, ensure_ascii=False) if summary else None
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO deploy_runs(
                environment, run_id, target_sha, trigger_name, status,
                started_at, finished_at, result, failed_step, last_error, summary_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(environment, run_id) DO UPDATE SET
                target_sha=COALESCE(excluded.target_sha, deploy_runs.target_sha),
                trigger_name=COALESCE(excluded.trigger_name, deploy_runs.trigger_name),
                status=COALESCE(excluded.status, deploy_runs.status),
                started_at=COALESCE(excluded.started_at, deploy_runs.started_at),
                finished_at=COALESCE(excluded.finished_at, deploy_runs.finished_at),
                result=COALESCE(excluded.result, deploy_runs.result),
                failed_step=COALESCE(excluded.failed_step, deploy_runs.failed_step),
                last_error=COALESCE(excluded.last_error, deploy_runs.last_error),
                summary_json=COALESCE(excluded.summary_json, deploy_runs.summary_json)
            """,
            (
                env,
                run_id,
                target_sha,
                trigger_name,
                status,
                started_at,
                finished_at,
                result,
                failed_step,
                last_error,
                summary_json,
            ),
        )
        conn.commit()


def get_deploy_run_summary(
    environment: str,
    run_id: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    env = environment.upper()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT summary_json, result, failed_step, last_error, finished_at, target_sha "
            "FROM deploy_runs WHERE environment=? AND run_id=?",
            (env, run_id),
        ).fetchone()
    if not row:
        return {}
    if row["summary_json"]:
        try:
            data = json.loads(row["summary_json"])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {
        "result": row["result"],
        "failedStep": row["failed_step"],
        "lastError": row["last_error"],
        "finishedAt": row["finished_at"],
        "toSha": row["target_sha"],
    }


def append_service_log(
    environment: str,
    service: str,
    stream: str,
    line: str,
    *,
    logged_at: str | None = None,
    db_path: Path | None = None,
) -> int:
    ts = logged_at or _utc_now_iso()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO service_log_lines(environment, service, stream, line, logged_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (environment.upper(), service.lower(), stream.lower(), line, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def tail_service_logs(
    environment: str,
    service: str,
    *,
    since_id: int = 0,
    limit: int = 200,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    env = environment.upper()
    limit = max(1, min(limit, 500))
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, stream, line, logged_at
            FROM service_log_lines
            WHERE environment=? AND service=? AND id>?
            ORDER BY id ASC
            LIMIT ?
            """,
            (env, service.lower(), since_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def append_audit_event(
    username: str,
    action: str,
    detail: str = "",
    *,
    db_path: Path | None = None,
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO audit_events(username, action, detail, created_at) VALUES(?, ?, ?, ?)",
            (username, action, detail, _utc_now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)


def _parse_deploy_log_line(line: str) -> tuple[str, str, str]:
    import re

    match = re.match(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)$", line)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return _utc_now_iso(), "INFO", line


def import_deploy_run_from_dir(
    environment: str,
    run_id: str,
    run_dir: Path,
    *,
    db_path: Path | None = None,
    skip_if_exists: bool = True,
) -> int:
    env = environment.upper()
    run_dir = Path(run_dir)
    with get_connection(db_path) as conn:
        if skip_if_exists:
            existing = conn.execute(
                "SELECT 1 FROM deploy_log_lines WHERE environment=? AND run_id=? LIMIT 1",
                (env, run_id),
            ).fetchone()
            if existing:
                return 0

    imported = 0
    for log_file in ("pipeline.log", "build.log", "validate.log", "promote.log", "rollback.log"):
        path = run_dir / log_file
        if not path.is_file():
            continue
        rows: list[tuple[str, str, str, str, str, str]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            logged_at, level, message = _parse_deploy_log_line(line)
            rows.append((env, run_id, _normalize_log_name(log_file), level, message, logged_at))
        if not rows:
            continue
        with get_connection(db_path) as conn:
            conn.executemany(
                """
                INSERT INTO deploy_log_lines(environment, run_id, log_name, level, message, logged_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        imported += len(rows)

    steps_path = run_dir / "steps.json"
    if steps_path.is_file():
        try:
            steps = json.loads(steps_path.read_text(encoding="utf-8"))
            if isinstance(steps, list):
                save_deploy_steps(env, run_id, steps, db_path=db_path)
        except (OSError, json.JSONDecodeError):
            pass

    summary_path = run_dir / "run-summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(summary, dict):
                upsert_deploy_run(
                    env,
                    run_id,
                    target_sha=summary.get("toSha"),
                    result=summary.get("result"),
                    finished_at=summary.get("finishedAt"),
                    failed_step=summary.get("failedStep"),
                    last_error=summary.get("lastError"),
                    summary=summary,
                    db_path=db_path,
                )
        except (OSError, json.JSONDecodeError):
            pass

    return imported


def import_legacy_runs(
    deploy_root: Path,
    *,
    db_path: Path | None = None,
    skip_if_exists: bool = True,
) -> dict[str, int]:
    deploy_root = Path(deploy_root)
    totals = {"runs": 0, "lines": 0}
    for env in ("MAIN", "DEV", "HOM"):
        runs_dir = deploy_root / env / "logs" / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            count = import_deploy_run_from_dir(
                env,
                run_dir.name,
                run_dir,
                db_path=db_path,
                skip_if_exists=skip_if_exists,
            )
            totals["runs"] += 1
            totals["lines"] += count
    return totals


def insert_monitor_sample(
    environment: str,
    metric_key: str,
    value: float,
    *,
    labels: dict[str, Any] | None = None,
    recorded_at: str | None = None,
    db_path: Path | None = None,
) -> int:
    ts = recorded_at or _utc_now_iso()
    labels_json = json.dumps(labels, ensure_ascii=False) if labels else None
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO monitor_samples(environment, metric_key, value, labels_json, recorded_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (environment.upper(), metric_key, float(value), labels_json, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def insert_monitor_event(
    environment: str,
    severity: str,
    category: str,
    title: str,
    *,
    detail: str | None = None,
    recorded_at: str | None = None,
    db_path: Path | None = None,
) -> int:
    ts = recorded_at or _utc_now_iso()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO monitor_events(environment, severity, category, title, detail, recorded_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (environment.upper(), severity, category, title, detail, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def query_monitor_series(
    environment: str,
    metric_key: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 2000,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    env = environment.upper()
    limit = max(1, min(limit, 10000))
    clauses = ["environment=?", "metric_key=?"]
    params: list[Any] = [env, metric_key]
    if since:
        clauses.append("recorded_at>=?")
        params.append(since)
    if until:
        clauses.append("recorded_at<=?")
        params.append(until)
    params.append(limit)
    sql = f"""
        SELECT id, value, labels_json, recorded_at
        FROM monitor_samples
        WHERE {' AND '.join(clauses)}
        ORDER BY recorded_at ASC
        LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item.get("labels_json"):
            try:
                item["labels"] = json.loads(item["labels_json"])
            except json.JSONDecodeError:
                item["labels"] = {}
        else:
            item["labels"] = {}
        out.append(item)
    return out


def query_monitor_events(
    environment: str | None = None,
    *,
    since: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    clauses: list[str] = []
    params: list[Any] = []
    if environment:
        clauses.append("environment=?")
        params.append(environment.upper())
    if since:
        clauses.append("recorded_at>=?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    sql = f"""
        SELECT id, environment, severity, category, title, detail, recorded_at
        FROM monitor_events
        {where}
        ORDER BY recorded_at DESC
        LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def aggregate_monitor_samples(
    environment: str,
    metric_key: str,
    *,
    since: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clauses = ["environment=?", "metric_key=?"]
    params: list[Any] = [environment.upper(), metric_key]
    if since:
        clauses.append("recorded_at>=?")
        params.append(since)
    sql = f"""
        SELECT COUNT(*) AS count,
               AVG(value) AS avg_value,
               MIN(value) AS min_value,
               MAX(value) AS max_value
        FROM monitor_samples
        WHERE {' AND '.join(clauses)}
    """
    with get_connection(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return {"count": 0, "avg": 0, "min": 0, "max": 0}
    return {
        "count": int(row["count"] or 0),
        "avg": round(float(row["avg_value"] or 0), 2),
        "min": round(float(row["min_value"] or 0), 2),
        "max": round(float(row["max_value"] or 0), 2),
    }


def purge_monitor_data(retention_days: int = 7, *, db_path: Path | None = None) -> dict[str, int]:
    with get_connection(db_path) as conn:
        d1 = conn.execute(
            "DELETE FROM monitor_samples WHERE recorded_at < datetime('now', ?)",
            (f"-{retention_days} days",),
        ).rowcount
        d2 = conn.execute(
            "DELETE FROM monitor_events WHERE recorded_at < datetime('now', ?)",
            (f"-{retention_days} days",),
        ).rowcount
        conn.commit()
    return {"monitor_samples": d1, "monitor_events": d2}


def purge_old_logs(retention_days: int = 90, *, db_path: Path | None = None) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # approximate: delete by row id batches older than retention via logged_at
    with get_connection(db_path) as conn:
        d1 = conn.execute(
            "DELETE FROM deploy_log_lines WHERE logged_at < datetime('now', ?)",
            (f"-{retention_days} days",),
        ).rowcount
        d2 = conn.execute(
            "DELETE FROM service_log_lines WHERE logged_at < datetime('now', ?)",
            (f"-{retention_days} days",),
        ).rowcount
        d3 = conn.execute(
            "DELETE FROM audit_events WHERE created_at < datetime('now', ?)",
            (f"-{retention_days} days",),
        ).rowcount
        conn.commit()
    return {"deploy_log_lines": d1, "service_log_lines": d2, "audit_events": d3}


def _cli_init(args: argparse.Namespace) -> int:
    path = init_store(resolve_db_path(args.db))
    print(str(path))
    return 0


def _cli_append_deploy_log(args: argparse.Namespace) -> int:
    row_id = append_deploy_log(
        args.env, args.run_id, args.log_name, args.level, args.message, db_path=resolve_db_path(args.db)
    )
    print(row_id)
    return 0


def _cli_save_steps(args: argparse.Namespace) -> int:
    if args.steps_file:
        steps = json.loads(Path(args.steps_file).read_text(encoding="utf-8"))
    elif args.steps_json:
        steps = json.loads(args.steps_json)
    else:
        raise SystemExit("save-steps requires --steps-json or --steps-file")
    save_deploy_steps(args.env, args.run_id, steps, db_path=resolve_db_path(args.db))
    return 0


def _cli_upsert_run(args: argparse.Namespace) -> int:
    if args.summary_file:
        summary = json.loads(Path(args.summary_file).read_text(encoding="utf-8"))
    elif args.summary_json:
        summary = json.loads(args.summary_json)
    else:
        summary = None

    target_sha = args.target_sha or (summary or {}).get("toSha") or ""
    trigger_name = args.trigger or (summary or {}).get("trigger") or ""
    status = args.status or (summary or {}).get("status") or ""
    started_at = args.started_at or (summary or {}).get("startedAt") or ""
    finished_at = args.finished_at or (summary or {}).get("finishedAt") or ""
    result = args.result or (summary or {}).get("result") or ""
    failed_step = args.failed_step or (summary or {}).get("failedStep") or ""
    last_error = args.last_error or (summary or {}).get("lastError") or ""

    upsert_deploy_run(
        args.env,
        args.run_id,
        target_sha=target_sha or None,
        trigger_name=trigger_name or None,
        status=status or None,
        started_at=started_at or None,
        finished_at=finished_at or None,
        result=result or None,
        failed_step=failed_step or None,
        last_error=last_error or None,
        summary=summary,
        db_path=resolve_db_path(args.db),
    )
    return 0


def _cli_append_service_log(args: argparse.Namespace) -> int:
    row_id = append_service_log(
        args.env, args.service, args.stream, args.line, db_path=resolve_db_path(args.db)
    )
    print(row_id)
    return 0


def _cli_import_legacy_runs(args: argparse.Namespace) -> int:
    totals = import_legacy_runs(
        Path(args.deploy_root),
        db_path=resolve_db_path(args.db),
        skip_if_exists=not args.force,
    )
    print(json.dumps(totals))
    return 0


def _cli_purge_monitor(args: argparse.Namespace) -> int:
    totals = purge_monitor_data(retention_days=args.days, db_path=resolve_db_path(args.db))
    print(json.dumps(totals))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PPLID ops SQLite store")
    parser.add_argument("--db", default="", help="Path to ops-store.db")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.set_defaults(func=_cli_init)

    p_log = sub.add_parser("append-deploy-log")
    p_log.add_argument("--env", required=True)
    p_log.add_argument("--run-id", required=True)
    p_log.add_argument("--log-name", required=True)
    p_log.add_argument("--level", required=True)
    p_log.add_argument("--message", required=True)
    p_log.set_defaults(func=_cli_append_deploy_log)

    p_steps = sub.add_parser("save-steps")
    p_steps.add_argument("--env", required=True)
    p_steps.add_argument("--run-id", required=True)
    p_steps.add_argument("--steps-json", default="")
    p_steps.add_argument("--steps-file", default="")
    p_steps.set_defaults(func=_cli_save_steps)

    p_run = sub.add_parser("upsert-run")
    p_run.add_argument("--env", required=True)
    p_run.add_argument("--run-id", required=True)
    p_run.add_argument("--target-sha", default="")
    p_run.add_argument("--trigger", default="")
    p_run.add_argument("--status", default="")
    p_run.add_argument("--started-at", default="")
    p_run.add_argument("--finished-at", default="")
    p_run.add_argument("--result", default="")
    p_run.add_argument("--failed-step", default="")
    p_run.add_argument("--last-error", default="")
    p_run.add_argument("--summary-json", default="")
    p_run.add_argument("--summary-file", default="")
    p_run.set_defaults(func=_cli_upsert_run)

    p_svc = sub.add_parser("append-service-log")
    p_svc.add_argument("--env", required=True)
    p_svc.add_argument("--service", required=True)
    p_svc.add_argument("--stream", required=True)
    p_svc.add_argument("--line", required=True)
    p_svc.set_defaults(func=_cli_append_service_log)

    p_import = sub.add_parser("import-legacy-runs")
    p_import.add_argument("--deploy-root", required=True)
    p_import.add_argument("--force", action="store_true", help="Re-import even if run exists")
    p_import.set_defaults(func=_cli_import_legacy_runs)

    p_purge_mon = sub.add_parser("purge-monitor")
    p_purge_mon.add_argument("--days", type=int, default=7)
    p_purge_mon.set_defaults(func=_cli_purge_monitor)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
