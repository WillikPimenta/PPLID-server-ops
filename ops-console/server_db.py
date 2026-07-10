"""
Explorador PostgreSQL do ops-console (psycopg).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from server_ops import ENV_ORDER, get_env_paths, parse_env_file

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    sql = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
READ_ONLY_TABLES = frozenset({
    "django_migrations",
    "django_session",
    "auth_permission",
    "django_content_type",
})
TEXT_SEARCH_TYPES = frozenset({
    "character varying",
    "varchar",
    "text",
    "char",
    "character",
})


class DbError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _require_psycopg() -> None:
    if psycopg is None:
        raise DbError("psycopg nao instalado. Use o venv do backend.", 503)


def _pg_params(config: dict[str, Any], env_name: str) -> dict[str, str]:
    backend_path, _ = get_env_paths(config, env_name)
    env = parse_env_file(backend_path)
    db = env.get("POSTGRES_DB", "")
    if not db:
        env_cfg = config.get(env_name, {})
        db = env_cfg.get("postgresDb", "")
    if not db:
        raise DbError("Credenciais Postgres nao encontradas.", 404)
    return {
        "host": env.get("POSTGRES_HOST", "localhost"),
        "port": env.get("POSTGRES_PORT", "5432"),
        "dbname": db,
        "user": env.get("POSTGRES_USER", "postgres"),
        "password": env.get("POSTGRES_PASSWORD", ""),
    }


def get_pg_connection(config: dict[str, Any], env_name: str):
    _require_psycopg()
    params = _pg_params(config, env_name)
    return psycopg.connect(
        host=params["host"],
        port=int(params["port"]),
        dbname=params["dbname"],
        user=params["user"],
        password=params["password"],
        connect_timeout=5,
        application_name="pplid-ops-console",
    )


def validate_table_name(table: str) -> str:
    name = (table or "").strip().lower()
    if not IDENTIFIER_RE.match(name):
        raise DbError("Nome de tabela invalido.")
    return name


def validate_column_name(column: str) -> str:
    name = (column or "").strip().lower()
    if not IDENTIFIER_RE.match(name):
        raise DbError(f"Nome de coluna invalido: {column}")
    return name


def is_read_only_table(table: str) -> bool:
    return table in READ_ONLY_TABLES


def require_write_access(env_name: str, body: dict[str, Any]) -> None:
    if env_name == "MAIN" and not body.get("confirmMain"):
        raise DbError("Operacao em MAIN requer confirmMain: true.", 403)


def serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (dict, list)):
        return value
    return value


def _fetch_table_schema(conn, table: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        columns = cur.fetchall()

        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """,
            (table,),
        )
        pk_rows = cur.fetchall()
    pk_columns = [row["column_name"] for row in pk_rows]
    return {
        "table": table,
        "columns": [
            {
                "name": col["column_name"],
                "type": col["data_type"],
                "nullable": col["is_nullable"] == "YES",
                "default": col["column_default"],
            }
            for col in columns
        ],
        "primaryKey": pk_columns,
        "readOnly": is_read_only_table(table),
    }


def collect_pg_connection_count(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    _require_psycopg()
    params = _pg_params(config, env_name)
    dbname = params["dbname"]
    result: dict[str, Any] = {"connections": {}}

    with get_pg_connection(config, env_name) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT state, count(*) AS cnt
                FROM pg_stat_activity
                WHERE datname = %s
                GROUP BY state
                """,
                (dbname,),
            )
            by_state: dict[str, int] = {}
            total = 0
            for row in cur.fetchall():
                state = row["state"] or "unknown"
                count = int(row["cnt"])
                by_state[state] = count
                total += count
            result["connections"] = {"total": total, "byState": by_state}

    return result


def collect_pg_metrics(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    from server_ops import _human_size

    _require_psycopg()
    params = _pg_params(config, env_name)
    dbname = params["dbname"]

    result: dict[str, Any] = {
        "sizeBytes": None,
        "sizeHuman": None,
        "connections": {},
        "blockingLocks": [],
        "slowQueries": [],
    }

    with get_pg_connection(config, env_name) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT state, count(*) AS cnt
                FROM pg_stat_activity
                WHERE datname = %s
                GROUP BY state
                """,
                (dbname,),
            )
            by_state: dict[str, int] = {}
            total = 0
            for row in cur.fetchall():
                state = row["state"] or "unknown"
                count = int(row["cnt"])
                by_state[state] = count
                total += count
            result["connections"] = {"total": total, "byState": by_state}

            cur.execute("SELECT pg_database_size(%s) AS size_bytes", (dbname,))
            size_row = cur.fetchone()
            if size_row and size_row["size_bytes"] is not None:
                size_bytes = int(size_row["size_bytes"])
                result["sizeBytes"] = size_bytes
                result["sizeHuman"] = _human_size(size_bytes)

            cur.execute(
                """
                SELECT blocked.pid AS blocked_pid,
                       blocking.pid AS blocking_pid,
                       left(blocked.query, 80) AS query
                FROM pg_locks blocked_locks
                JOIN pg_stat_activity blocked ON blocked.pid = blocked_locks.pid
                JOIN pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
                    AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                    AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                    AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                    AND blocking_locks.pid != blocked_locks.pid
                JOIN pg_stat_activity blocking ON blocking.pid = blocking_locks.pid
                WHERE NOT blocked_locks.granted
                LIMIT 5
                """
            )
            for row in cur.fetchall():
                result["blockingLocks"].append(
                    {
                        "blockedPid": row["blocked_pid"],
                        "blockingPid": row["blocking_pid"],
                        "query": row["query"] or "",
                    }
                )

            cur.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements' LIMIT 1"
            )
            if cur.fetchone():
                cur.execute(
                    """
                    SELECT left(query, 100) AS query,
                           calls,
                           round(mean_exec_time::numeric, 2) AS mean_ms
                    FROM pg_stat_statements
                    WHERE dbid = (SELECT oid FROM pg_database WHERE datname = %s)
                    ORDER BY mean_exec_time DESC
                    LIMIT 5
                    """,
                    (dbname,),
                )
                for row in cur.fetchall():
                    result["slowQueries"].append(
                        {
                            "query": row["query"] or "",
                            "calls": str(row["calls"]),
                            "meanMs": str(row["mean_ms"]),
                        }
                    )
            else:
                result["slowQueriesNote"] = "pg_stat_statements nao disponivel"

    return result


def list_tables(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    _require_psycopg()
    with get_pg_connection(config, env_name) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    c.relname AS table_name,
                    CASE
                        WHEN COALESCE(s.n_live_tup, 0) > 0 THEN s.n_live_tup::bigint
                        WHEN COALESCE(c.reltuples, -1) >= 0 THEN c.reltuples::bigint
                        ELSE 0::bigint
                    END AS row_estimate,
                    GREATEST(s.last_analyze, s.last_autoanalyze) AS last_stats_update
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                ORDER BY c.relname
                """
            )
            rows = cur.fetchall()

        tables = []
        zero_estimate: list[str] = []
        for row in rows:
            last_stats = row.get("last_stats_update")
            estimate = int(row["row_estimate"] or 0)
            name = row["table_name"]
            if estimate == 0:
                zero_estimate.append(name)
            tables.append(
                {
                    "name": name,
                    "rowEstimate": estimate,
                    "readOnly": is_read_only_table(name),
                    "lastStatsUpdate": last_stats.isoformat() if last_stats else None,
                }
            )

        if zero_estimate:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000'")
                for name in zero_estimate[:30]:
                    try:
                        cur.execute(
                            sql.SQL("SELECT COUNT(*) AS cnt FROM {}").format(sql.Identifier(name))
                        )
                        count = int(cur.fetchone()[0])
                        for table in tables:
                            if table["name"] == name:
                                table["rowEstimate"] = count
                                break
                    except Exception:
                        continue

    return {"environment": env_name, "tables": tables}


FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|EXECUTE)\b",
    re.IGNORECASE,
)


def execute_read_query(
    config: dict[str, Any],
    env_name: str,
    sql_text: str,
    *,
    max_rows: int = 100,
) -> dict[str, Any]:
    stripped = sql_text.strip().rstrip(";").strip()
    if not stripped:
        raise DbError("SQL vazio.", 400)
    upper = stripped.upper()
    if not upper.startswith("SELECT"):
        raise DbError("Apenas consultas SELECT sao permitidas.", 400)
    if ";" in stripped:
        raise DbError("Apenas uma instrucao por vez.", 400)
    if FORBIDDEN_SQL.search(stripped):
        raise DbError("Instrucao SQL nao permitida.", 400)
    if "LIMIT" not in upper:
        stripped = f"{stripped} LIMIT {max_rows}"

    with get_pg_connection(config, env_name) as conn:
        with conn.transaction(readonly=True):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(stripped)
                rows = cur.fetchall()
                columns = [desc.name for desc in cur.description] if cur.description else []

    return {
        "environment": env_name,
        "columns": columns,
        "rows": [{k: serialize_value(v) for k, v in row.items()} for row in rows],
        "rowCount": len(rows),
    }


def get_table_schema(config: dict[str, Any], env_name: str, table: str) -> dict[str, Any]:
    table = validate_table_name(table)
    with get_pg_connection(config, env_name) as conn:
        schema = _fetch_table_schema(conn, table)
    return {"environment": env_name, **schema}


def get_table_rows(
    config: dict[str, Any],
    env_name: str,
    table: str,
    *,
    page: int = 1,
    limit: int = 50,
    order: str = "",
    search: str = "",
) -> dict[str, Any]:
    table = validate_table_name(table)
    page = max(1, page)
    limit = max(1, min(limit, 100))

    with get_pg_connection(config, env_name) as conn:
        schema = _fetch_table_schema(conn, table)
        columns = [c["name"] for c in schema["columns"]]
        if not columns:
            raise DbError("Tabela sem colunas.", 404)

        order_col = columns[0]
        order_dir = "ASC"
        if order:
            parts = order.strip().split()
            if parts[0].lower() in columns:
                order_col = parts[0].lower()
            if len(parts) > 1 and parts[1].upper() == "DESC":
                order_dir = "DESC"

        where_sql = sql.SQL("TRUE")
        params: list[Any] = []
        if search.strip():
            text_cols = [
                c["name"]
                for c in schema["columns"]
                if c["type"] in TEXT_SEARCH_TYPES
            ][:3]
            if text_cols:
                clauses = [
                    sql.SQL("{} ILIKE %s").format(sql.Identifier(col))
                    for col in text_cols
                ]
                where_sql = sql.SQL(" OR ").join(clauses)
                params = [f"%{search.strip()}%"] * len(text_cols)

        count_query = sql.SQL("SELECT COUNT(*) AS cnt FROM {} WHERE ").format(
            sql.Identifier(table)
        ) + where_sql
        data_query = sql.SQL("SELECT * FROM {} WHERE ").format(sql.Identifier(table))
        data_query += where_sql
        data_query += sql.SQL(" ORDER BY {} {} LIMIT %s OFFSET %s").format(
            sql.Identifier(order_col),
            sql.SQL(order_dir),
        )

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(count_query, params)
            total = int(cur.fetchone()["cnt"])
            cur.execute(data_query, [*params, limit, (page - 1) * limit])
            rows = cur.fetchall()

    serialized = []
    for row in rows:
        serialized.append({k: serialize_value(v) for k, v in row.items()})

    return {
        "environment": env_name,
        "table": table,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": max(1, (total + limit - 1) // limit),
        "columns": columns,
        "primaryKey": schema["primaryKey"],
        "readOnly": schema["readOnly"],
        "rows": serialized,
    }


def _validate_values(schema: dict[str, Any], values: dict[str, Any], *, for_insert: bool) -> dict[str, Any]:
    allowed = {c["name"]: c for c in schema["columns"]}
    cleaned: dict[str, Any] = {}
    for key, raw in (values or {}).items():
        col = validate_column_name(key)
        if col not in allowed:
            raise DbError(f"Coluna nao permitida: {col}")
        if raw is None or raw == "":
            if for_insert and not allowed[col]["nullable"] and allowed[col]["default"] is None:
                raise DbError(f"Coluna obrigatoria: {col}")
            cleaned[col] = None
        else:
            cleaned[col] = raw
    return cleaned


def insert_row(
    config: dict[str, Any],
    env_name: str,
    table: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    table = validate_table_name(table)
    if is_read_only_table(table):
        raise DbError("Tabela somente leitura.", 403)
    require_write_access(env_name, body)

    schema_payload = get_table_schema(config, env_name, table)
    values = _validate_values(
        schema_payload,
        body.get("values") or {},
        for_insert=True,
    )
    if not values:
        raise DbError("Nenhum valor para inserir.")

    cols = list(values.keys())
    with get_pg_connection(config, env_name) as conn:
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        )
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, [values[c] for c in cols])
            row = cur.fetchone()
        conn.commit()

    return {
        "ok": True,
        "environment": env_name,
        "table": table,
        "row": {k: serialize_value(v) for k, v in row.items()},
    }


def update_row(
    config: dict[str, Any],
    env_name: str,
    table: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    table = validate_table_name(table)
    if is_read_only_table(table):
        raise DbError("Tabela somente leitura.", 403)
    require_write_access(env_name, body)

    schema = get_table_schema(config, env_name, table)
    pk = schema["primaryKey"]
    if not pk:
        raise DbError("Tabela sem chave primaria; update nao permitido.")

    pk_values = body.get("pk") or {}
    if not pk_values:
        raise DbError("pk obrigatorio para update.")
    for col in pk:
        validate_column_name(col)
        if col not in pk_values:
            raise DbError(f"pk incompleto: falta {col}")

    updates = _validate_values(schema, body.get("values") or {}, for_insert=False)
    if not updates:
        raise DbError("Nenhum valor para atualizar.")

    set_parts = [
        sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder())
        for col in updates
    ]
    where_parts = [
        sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder())
        for col in pk
    ]
    query = sql.SQL("UPDATE {} SET ").format(sql.Identifier(table))
    query += sql.SQL(", ").join(set_parts)
    query += sql.SQL(" WHERE ")
    query += sql.SQL(" AND ").join(where_parts)
    query += sql.SQL(" RETURNING *")

    params = [*updates.values(), *[pk_values[col] for col in pk]]

    with get_pg_connection(config, env_name) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise DbError("Registro nao encontrado.", 404)
        conn.commit()

    return {
        "ok": True,
        "environment": env_name,
        "table": table,
        "row": {k: serialize_value(v) for k, v in row.items()},
    }


def delete_row(
    config: dict[str, Any],
    env_name: str,
    table: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    table = validate_table_name(table)
    if is_read_only_table(table):
        raise DbError("Tabela somente leitura.", 403)
    require_write_access(env_name, body)

    schema = get_table_schema(config, env_name, table)
    pk = schema["primaryKey"]
    if not pk:
        raise DbError("Tabela sem chave primaria; delete nao permitido.")

    pk_values = body.get("pk") or {}
    for col in pk:
        validate_column_name(col)
        if col not in pk_values:
            raise DbError(f"pk incompleto: falta {col}")

    where_parts = [
        sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder())
        for col in pk
    ]
    query = sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(table))
    query += sql.SQL(" AND ").join(where_parts)
    query += sql.SQL(" RETURNING *")
    params = [pk_values[col] for col in pk]

    with get_pg_connection(config, env_name) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise DbError("Registro nao encontrado.", 404)
        conn.commit()

    return {
        "ok": True,
        "environment": env_name,
        "table": table,
        "deleted": {k: serialize_value(v) for k, v in row.items()},
    }


def parse_database_path(path: str) -> tuple[str | None, str | None, str | None]:
    """
    /api/v1/database/{ENV}
    /api/v1/database/{ENV}/tables
    /api/v1/database/{ENV}/tables/{table}/schema
    /api/v1/database/{ENV}/tables/{table}/rows
    /api/v1/database/{ENV}/tables/{table}/query
    """
    rest = path.removeprefix("/api/v1/database/").strip("/")
    if not rest:
        return None, None, None
    parts = rest.split("/")
    env_name = parts[0].upper()
    if env_name not in ENV_ORDER:
        return None, None, None
    if len(parts) == 1:
        return env_name, None, "metrics"
    if parts[1] != "tables":
        return env_name, None, None
    if len(parts) == 2:
        return env_name, None, "tables"
    table = parts[2].lower()
    if len(parts) == 4 and parts[3] == "schema":
        return env_name, table, "schema"
    if len(parts) == 4 and parts[3] == "rows":
        return env_name, table, "rows"
    if len(parts) == 4 and parts[3] == "query":
        return env_name, table, "query"
    return env_name, None, None


def handle_db_request(
    method: str,
    path: str,
    config: dict[str, Any],
    query: dict[str, list[str]],
    body: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    from server_ops import fetch_database_metrics

    env_name, table, action = parse_database_path(path)
    if not env_name:
        return {"error": "Rota invalida"}, 404

    try:
        if action == "metrics" and method == "GET":
            return fetch_database_metrics(config, env_name), 200
        if action == "tables" and method == "GET":
            return list_tables(config, env_name), 200
        if action == "schema" and table and method == "GET":
            return get_table_schema(config, env_name, table), 200
        if action == "rows" and table:
            if method == "GET":
                page = int((query.get("page") or ["1"])[0])
                limit = int((query.get("limit") or ["50"])[0])
                order = (query.get("order") or [""])[0]
                search = (query.get("search") or [""])[0]
                return get_table_rows(
                    config,
                    env_name,
                    table,
                    page=page,
                    limit=limit,
                    order=order,
                    search=search,
                ), 200
            if method == "POST":
                return insert_row(config, env_name, table, body), 200
            if method == "PATCH":
                return update_row(config, env_name, table, body), 200
            if method == "DELETE":
                return delete_row(config, env_name, table, body), 200
        if action == "query" and table and method == "POST":
            sql_text = (body.get("sql") or "").strip()
            return execute_read_query(config, env_name, sql_text), 200
        return {"error": "Metodo ou rota nao suportado"}, 404
    except DbError as exc:
        return {"error": str(exc)}, exc.status
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}, 500
