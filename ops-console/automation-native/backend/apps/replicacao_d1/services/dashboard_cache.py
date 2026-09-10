# -*- coding: utf-8 -*-
"""Cache versionado do dashboard D-1 com watermark compartilhado pelo banco."""
from __future__ import annotations

import hashlib
import json

from django.db.models import Max

from apps.common.versioned_cache import VersionedCache


_CACHE = VersionedCache(
    "replicacao_d1_dashboard",
    ttl_setting="REPLICACAO_D1_DASHBOARD_CACHE_TTL",
    default_ttl=60,
)
_PAYLOAD_SCHEMA = "contract-v5-fast-reconciliation-v2"


def bump_dashboard_cache_version() -> int:
    return _CACHE.bump_cache_version()


def _stable_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _data_watermark() -> str:
    # O sync roda em subprocesso e MAIN usa LocMem. O watermark evita servir
    # payload antigo mesmo quando o bump ocorreu em outro processo.
    from apps.produtividade.models import ProductivityRecord
    from apps.replicacao_d1.config_models import ReplicacaoD1ConfigGeral, ReplicacaoD1EscalaDia
    from apps.replicacao_d1.models import (
        ReplicacaoD1Reconciliacao,
        ReplicacaoD1Replicado,
        ReplicacaoD1Run,
    )
    from apps.replicacao_d1.services.reconciliation import RECONCILIATION_RULE_VERSION

    run_mark = ReplicacaoD1Run.objects.aggregate(value=Max("synced_at"))["value"]
    rep_mark = ReplicacaoD1Replicado.objects.aggregate(value=Max("id"))["value"]
    rec_mark = ReplicacaoD1Reconciliacao.objects.filter(
        regra_version=RECONCILIATION_RULE_VERSION
    ).aggregate(value=Max("reconciliado_em"))["value"]
    scale_mark = ReplicacaoD1EscalaDia.objects.aggregate(value=Max("updated_at"))["value"]
    config_mark = ReplicacaoD1ConfigGeral.objects.aggregate(value=Max("updated_at"))["value"]
    prod_mark = ProductivityRecord.objects.aggregate(value=Max("id"))["value"]
    raw = "|".join(
        str(value or "0")
        for value in (run_mark, rep_mark, rec_mark, scale_mark, config_mark, prod_mark)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def cache_key_parts(params: dict) -> tuple[str, ...]:
    normalized = {str(key): params.get(key) for key in sorted(params)}
    return (_PAYLOAD_SCHEMA, _data_watermark(), _stable_hash(normalized))


def get_cached_by_parts(parts: tuple[str, ...]):
    return _CACHE.get(*parts)


def set_cached_by_parts(parts: tuple[str, ...], payload) -> None:
    _CACHE.set(payload, *parts)


def get_cached(params: dict):
    return get_cached_by_parts(cache_key_parts(params))


def set_cached(params: dict, payload) -> None:
    set_cached_by_parts(cache_key_parts(params), payload)
