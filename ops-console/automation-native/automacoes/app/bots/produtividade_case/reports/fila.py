"""Snapshot da fila ativa do Case Manager no ciclo mensal de auditoria.

DocumentDB (API compatível com Mongo) não oferece ``$facet`` nem ``$$NOW``.
Usamos vários aggregates leves + instante UTC injetado pelo Python.

Idade: prefere campos de criação reais; fallback ``$toDate: "$_id"`` (ObjectId).
Itens: lista completa dos protocolos em aberto (teto de segurança alto).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.bots.produtividade_case.dates import TZ_BR

IDADE_BUCKETS = ("0-1h", "1-4h", "4-24h", "24h+")
# Teto de segurança (fila real ~dezenas de milhares). Não é “amostra de 200”.
FILA_ITEMS_MAX = 100_000


def calc_periodo_fila(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Retorna a janela [início do ciclo, instante da captura) em UTC.

    O dia 1 ainda exibe o ciclo iniciado no dia 2 do mês anterior. A partir do
    dia 2, a fila passa ao ciclo do mês corrente. O limite final é o instante
    da captura, reproduzindo o contrato Mongo com ISODate sem aceitar registros
    futuros.
    """
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    ref_utc = ref.astimezone(timezone.utc)
    ref_br = ref_utc.astimezone(TZ_BR)

    if ref_br.day == 1:
        if ref_br.month == 1:
            start_year, start_month = ref_br.year - 1, 12
        else:
            start_year, start_month = ref_br.year, ref_br.month - 1
    else:
        start_year, start_month = ref_br.year, ref_br.month

    return (
        datetime(start_year, start_month, 2, tzinfo=timezone.utc),
        ref_utc,
    )


def match_fila_aberta(now: datetime | None = None) -> dict[str, Any]:
    """Filtro Mongo da fila ativa alinhada com o ciclo mensal da auditoria."""
    inicio, fim = calc_periodo_fila(now)
    return {
        "origin.requestType": "AUDIT",
        "createdAt": {"$gte": inicio, "$lt": fim},
        "transactionStatus": "NEW",
    }


def _created_ts_expr() -> dict[str, Any]:
    """Data de criação: campos reais → ObjectId timestamp."""
    return {
        "$ifNull": [
            "$creationDate",
            {
                "$ifNull": [
                    "$createdAt",
                    {
                        "$ifNull": [
                            "$origin.creationDate",
                            {
                                "$ifNull": [
                                    "$origin.createdAt",
                                    {
                                        "$ifNull": [
                                            "$insertionDate",
                                            {"$toDate": "$_id"},
                                        ]
                                    },
                                ]
                            },
                        ]
                    },
                ]
            },
        ]
    }


def _idade_bucket_expr(now: datetime) -> dict[str, Any]:
    """Buckets de idade sem ``$switch`` (melhor compat DocumentDB)."""
    age_hours = {
        "$divide": [
            {"$subtract": [now, "$created_ts"]},
            3_600_000,
        ]
    }
    return {
        "$cond": [
            {"$lt": [age_hours, 1]},
            "0-1h",
            {
                "$cond": [
                    {"$lt": [age_hours, 4]},
                    "1-4h",
                    {
                        "$cond": [
                            {"$lt": [age_hours, 24]},
                            "4-24h",
                            "24h+",
                        ]
                    },
                ]
            },
        ]
    }


def _add_created_and_idade(now: datetime) -> list[dict[str, Any]]:
    return [
        {"$addFields": {"created_ts": _created_ts_expr()}},
        {
            "$addFields": {
                "idade_bucket": {
                    "$cond": [
                        {"$eq": [{"$type": "$created_ts"}, "missing"]},
                        "24h+",
                        {
                            "$cond": [
                                {"$eq": ["$created_ts", None]},
                                "24h+",
                                _idade_bucket_expr(now),
                            ]
                        },
                    ]
                }
            }
        },
    ]


def build_pipeline_total(now: datetime | None = None) -> list[dict[str, Any]]:
    return [
        {"$match": match_fila_aberta(now)},
        {"$group": {"_id": None, "n": {"$sum": 1}}},
    ]


def build_pipeline_by_status(now: datetime | None = None) -> list[dict[str, Any]]:
    return [
        {"$match": match_fila_aberta(now)},
        {
            "$group": {
                "_id": {"$ifNull": ["$transactionStatus", "(sem status)"]},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
    ]


def build_pipeline_by_request_type(now: datetime | None = None) -> list[dict[str, Any]]:
    return [
        {"$match": match_fila_aberta(now)},
        {
            "$group": {
                "_id": {"$ifNull": ["$origin.requestType", "(sem tipo)"]},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
    ]


def build_pipeline_by_idade(now: datetime | None = None) -> list[dict[str, Any]]:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return [
        {"$match": match_fila_aberta(ref)},
        *_add_created_and_idade(ref),
        {"$group": {"_id": "$idade_bucket", "count": {"$sum": 1}}},
    ]


def build_pipeline_sample(
    now: datetime | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Lista de protocolos em aberto (mais antigos primeiro).

    Sem ``limit`` (padrão): todos os docs até ``FILA_ITEMS_MAX``.
    """
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    pipe: list[dict[str, Any]] = [
        {"$match": match_fila_aberta(ref)},
        *_add_created_and_idade(ref),
        {"$sort": {"created_ts": 1, "_id": 1}},
    ]
    if limit is None:
        lim = FILA_ITEMS_MAX
    else:
        lim = max(1, min(int(limit), FILA_ITEMS_MAX))
    pipe.append({"$limit": lim})
    pipe.append(
        {
            "$project": {
                "_id": 0,
                "protocolo_id": {"$toString": "$_id"},
                "transaction_status": {
                    "$ifNull": ["$transactionStatus", ""]
                },
                "idade_bucket": 1,
                "created_ts": 1,
                "workflow_origem": {
                    "$ifNull": [
                        "$origin.workflow",
                        {"$ifNull": ["$workflow", ""]},
                    ]
                },
                "cliente_origem": {
                    "$ifNull": ["$origin.customerName", ""]
                },
                # Mesmo mapeamento do consolidado Case
                "protocolo_origem": {
                    "$ifNull": [
                        {"$toString": "$origin.customerId"},
                        {"$ifNull": ["$origin.protocol", ""]},
                    ]
                },
                "cadastro_origem_at": {
                    "$ifNull": [
                        "$origin.createdAt",
                        {
                            "$ifNull": [
                                "$origin.creationDate",
                                "$origin.insertionDate",
                            ]
                        },
                    ]
                },
            }
        }
    )
    return pipe


# alias legado
FILA_SAMPLE_LIMIT = FILA_ITEMS_MAX


def build_pipeline_fila(now: datetime | None = None) -> list[dict[str, Any]]:
    """Compat / testes: pipeline de idade (DocumentDB não usa $facet)."""
    return build_pipeline_by_idade(now=now)


def _facet_rows(rows: list[dict] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        key = row.get("_id")
        if key is None or key == "":
            key = "(vazio)"
        out.append({"key": str(key), "count": int(row.get("count") or 0)})
    return out


def _normalize_idade(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {r["key"]: r["count"] for r in rows}
    return [{"key": b, "count": int(by_key.get(b, 0))} for b in IDADE_BUCKETS]


def _serialize_sample_item(row: dict[str, Any]) -> dict[str, Any]:
    created = row.get("created_ts")
    created_s = _iso_br(created)
    cad_origem = row.get("cadastro_origem_at")
    cad_s = _iso_br(cad_origem)
    return {
        "protocolo_id": str(row.get("protocolo_id") or ""),
        "transaction_status": str(row.get("transaction_status") or "")[:64],
        "idade_bucket": str(row.get("idade_bucket") or "")[:16],
        "created_ts": created_s,
        "workflow_origem": str(row.get("workflow_origem") or "")[:255],
        "cliente_origem": str(row.get("cliente_origem") or "")[:255],
        "protocolo_origem": str(row.get("protocolo_origem") or "")[:128],
        "cadastro_origem_at": cad_s,
    }


def _iso_br(value: Any) -> str | None:
    """Serializa datetime Mongo (UTC aware/naive) em ISO no fuso de Brasília."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ_BR).isoformat()
    return str(value)


def _run_agg(collection, pipeline: list[dict[str, Any]]) -> list[dict]:
    return list(collection.aggregate(pipeline, allowDiskUse=True))


def aggregate_fila(collection, now: datetime | None = None) -> dict[str, Any]:
    """Executa aggregates compatíveis com DocumentDB e monta o payload."""
    started = time.perf_counter()
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    total_docs = _run_agg(collection, build_pipeline_total(now=ref))
    total = int(total_docs[0]["n"]) if total_docs else 0

    by_status = _facet_rows(_run_agg(collection, build_pipeline_by_status(now=ref)))
    by_idade = _normalize_idade(
        _facet_rows(_run_agg(collection, build_pipeline_by_idade(now=ref)))
    )
    by_request = _facet_rows(
        _run_agg(collection, build_pipeline_by_request_type(now=ref))
    )
    # Lista completa (até FILA_ITEMS_MAX); chave sample_items mantida por compat do sync
    items_raw = _run_agg(collection, build_pipeline_sample(now=ref))
    sample_items = [
        _serialize_sample_item(r) for r in items_raw if r.get("protocolo_id")
    ][:FILA_ITEMS_MAX]

    return {
        "captured_at": ref.astimezone(TZ_BR).isoformat(),
        "total_abertos": total,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "by_status": by_status,
        "by_idade_bucket": by_idade,
        "by_request_type": by_request,
        "sample_items": sample_items,
        "items_count": len(sample_items),
    }


def gerar_fila_aberta(collection, arquivo: Path) -> dict[str, Any]:
    """Agrega a fila e grava JSON em ``arquivo`` (compacto — lista pode ser grande)."""
    payload = aggregate_fila(collection)
    arquivo = Path(arquivo)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    tmp = arquivo.with_suffix(arquivo.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(arquivo)
    return payload
