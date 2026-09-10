"""Cliente DocumentDB a partir de variáveis de ambiente (sem logar senha/URI)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from pymongo import MongoClient


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def validate_docdb_config() -> tuple[bool, str]:
    """Valida deps e env necessários antes de iniciar o bot."""
    try:
        import pymongo  # noqa: F401
    except ImportError:
        return (
            False,
            "Dependência 'pymongo' não instalada. Execute: pip install -e ./automacoes",
        )

    host = _env("DOCDB_HOST")
    user = _env("DOCDB_USER")
    password = _env("DOCDB_PASSWORD")
    ca_file = _env("DOCDB_TLS_CA_FILE")

    missing = []
    if not host:
        missing.append("DOCDB_HOST")
    if not user:
        missing.append("DOCDB_USER")
    if not password:
        missing.append("DOCDB_PASSWORD")
    if not ca_file:
        missing.append("DOCDB_TLS_CA_FILE")
    if missing:
        return False, f"Variáveis DocumentDB ausentes no .env: {', '.join(missing)}"

    ca_path = Path(ca_file).expanduser()
    if not ca_path.is_file():
        return False, f"Certificado TLS não encontrado: {ca_path}"

    return True, ""


def validate_docdb_connection(*, timeout_ms: int = 12000) -> tuple[bool, str]:
    """Valida env + ping no cluster (detecta senha/usuário inválidos)."""
    ok, msg = validate_docdb_config()
    if not ok:
        return False, msg
    try:
        client = MongoClient(
            build_mongo_uri(),
            tlsCAFile=str(Path(_env("DOCDB_TLS_CA_FILE")).expanduser()),
            serverSelectionTimeoutMS=timeout_ms,
        )
        try:
            client.admin.command("ping")
        finally:
            client.close()
    except Exception as exc:
        err = str(exc)
        if "Authentication failed" in err or "code': 18" in err or "code\": 18" in err:
            return (
                False,
                "DocumentDB: autenticação falhou. Confira DOCDB_USER/DOCDB_PASSWORD no backend/.env "
                "(alinhe com o script legado em c:\\scripts\\produtividade).",
            )
        return False, f"DocumentDB: falha de conexão — {err[:160]}"
    return True, ""


def build_mongo_uri() -> str:
    host = _env("DOCDB_HOST")
    port = _env("DOCDB_PORT", "27017")
    user = quote_plus(_env("DOCDB_USER"))
    password = quote_plus(_env("DOCDB_PASSWORD"))
    auth_source = quote_plus(_env("DOCDB_AUTH_SOURCE", "admin"))
    return (
        f"mongodb://{user}:{password}@{host}:{port}/"
        f"?tls=true&replicaSet=rs0&readPreference=primaryPreferred"
        f"&retryWrites=false&authSource={auth_source}"
    )


def get_collection():
    """Abre MongoClient e retorna a collection de transactions."""
    ok, msg = validate_docdb_config()
    if not ok:
        raise RuntimeError(msg)

    ca_file = str(Path(_env("DOCDB_TLS_CA_FILE")).expanduser())
    db_name = _env("DOCDB_DB", "case-manager-prod")
    coll_name = _env("DOCDB_COLLECTION", "transactions")

    client = MongoClient(build_mongo_uri(), tlsCAFile=ca_file)
    return client, client[db_name][coll_name]
