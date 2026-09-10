"""Cria bancos PostgreSQL locais (pplid_dev por padrao) se ainda nao existirem."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import environ

BACKEND_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap PostgreSQL databases for local PPLID dev.")
    parser.add_argument(
        "--databases",
        nargs="+",
        default=["pplid_dev"],
        help="Database names to create (default: pplid_dev)",
    )
    args = parser.parse_args()

    env_file = BACKEND_DIR / ".env"
    env = environ.Env()
    if env_file.exists():
        environ.Env.read_env(env_file)

    db_user = env("POSTGRES_USER", default="postgres")
    db_password = env("POSTGRES_PASSWORD", default="postgres")
    db_host = env("POSTGRES_HOST", default="localhost")
    db_port = env("POSTGRES_PORT", default="5432")

    import psycopg
    from psycopg import sql

    with psycopg.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY 1"
            )
            existing = {row[0] for row in cur.fetchall()}

            for name in args.databases:
                if name in existing:
                    print(f"[skip] database already exists: {name}")
                    continue
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                print(f"[ok] created database: {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
