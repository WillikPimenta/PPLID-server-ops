# -*- coding: utf-8 -*-
"""Extensões do Resumo EO: melhorias por cliente, prioridades e contestação rápida.
A aba Contestação (visão operacional) monta Falhas por etapa à parte.

Reutiliza failure_weight / _eo_pct / shift_period / criticidade — sem novas fórmulas.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from apps.qualidade_operacional.services.analytics import (
    _eo_pct,
    failure_weight,
    filtered_auditados,
    filtered_falhas,
    resolve_date_range,
    resolve_grain,
    shift_period,
)
from apps.qualidade_operacional.services.contestacao_metrics import (
    _contestacao_auditados_qs,
    _contestacao_falhas_qs,
    _pct,
    _period_label,
    build_contestacao_metrics,
    resolve_population_qs,
)
from apps.qualidade_operacional.services.criticidade import criticidade_label
from apps.qualidade_operacional.services.enrichment import build_dim_lookups
from apps.qualidade_operacional.services.insights import _impact_label
from apps.qualidade_operacional.services.normalize import normalize_tipo_conclusao
from apps.qualidade_operacional.services.queries import params_with

MELHORIAS_VIEW_KEYS = frozenset(
    {
        "melhorias_page",
        "melhorias_page_size",
        "melhorias_q",
        "melhorias_ordering",
        "melhorias_dir",
    }
)

_SITUACAO_ORDER = {
    "piorou": 0,
    "estavel": 1,
    "melhorou": 2,
    "sem_historico": 3,
}

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100

_FALHA_SCAN_FIELDS = (
    "id_cliente",
    "protocolo",
    "etapa",
    "cenario",
    "tipo_documento",
    "des_problemas",
    "categoria_falha",
    "tipo_registro",
    "nivel_dificuldade",
    "nivel_dificuldade_confer",
    "tipo_falha",
    "tipo_analise",
    "data",
)

NAO_INFORMADO_KEY = "__nao_informado__"
NAO_INFORMADO_LABEL = "Não informado"
EVIDENCE_PREVIEW_LIMIT = 8


def strip_melhorias_view_params(params) -> dict:
    """Remove params de paginação/busca da chave de cache do dashboard."""
    copied = params_with(params)
    for key in MELHORIAS_VIEW_KEYS:
        copied.pop(key, None)
    return copied


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dificuldade_short(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.casefold()
    if "facil" in low or "fácil" in low:
        return "Fácil"
    if "media" in low or "média" in low or "medio" in low:
        return "Média"
    if "dificil" in low or "difícil" in low:
        return "Difícil"
    return text


def _classificacao_display(crit: str, dif: str) -> str:
    crit = (crit or "").strip()
    dif = (dif or "").strip()
    if crit and dif:
        return f"{crit}/{dif}"
    return crit or dif or "—"


def _top_by_weight(buckets: dict[str, float]) -> str | None:
    if not buckets:
        return None
    best = max(buckets.items(), key=lambda kv: (kv[1], kv[0]))
    return best[0] if best[1] > 0 or best[0] else best[0]


def _client_counts(qs, grain: str) -> dict[int, int]:
    from django.db.models import Count

    if grain == "protocolo":
        grouped = (
            qs.exclude(protocolo="")
            .exclude(id_cliente__isnull=True)
            .values("id_cliente")
            .annotate(c=Count("protocolo", distinct=True))
        )
    else:
        grouped = (
            qs.exclude(id_cliente__isnull=True)
            .values("id_cliente")
            .annotate(c=Count("id"))
        )
    out: dict[int, int] = {}
    for row in grouped:
        try:
            cid = int(row["id_cliente"])
        except (TypeError, ValueError):
            continue
        out[cid] = int(row["c"])
    return out


def _scan_falhas_by_cliente(fal_qs, grain: str) -> dict[int, dict[str, Any]]:
    """Agrega falhas/impacto e dimensões principais por cliente (um scan)."""
    clients: dict[int, dict[str, Any]] = {}
    protocol_weights: dict[tuple[int, str], float] = {}

    for row in fal_qs.values(*_FALHA_SCAN_FIELDS).iterator(chunk_size=4000):
        raw_cid = row.get("id_cliente")
        if raw_cid is None:
            continue
        try:
            cid = int(raw_cid)
        except (TypeError, ValueError):
            continue
        bucket = clients.setdefault(
            cid,
            {
                "falhas": 0,
                "impacto": 0.0,
                "causas": defaultdict(float),
                "etapas": defaultdict(float),
                "cenarios": defaultdict(float),
                "classifs": defaultdict(float),
                "classif_meta": {},
            },
        )
        weight = failure_weight(row)
        if grain == "protocolo":
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (cid, protocolo)
            prev = protocol_weights.get(key)
            if prev is not None and weight <= prev:
                # Já contou este protocolo; só atualiza dimensão se peso maior (skip).
                continue
            if prev is None:
                bucket["falhas"] += 1
            else:
                bucket["impacto"] -= prev
            protocol_weights[key] = weight
            bucket["impacto"] += weight
        else:
            bucket["falhas"] += 1
            bucket["impacto"] += weight

        causa = (row.get("des_problemas") or "").strip() or "(sem causa)"
        etapa = (row.get("etapa") or "").strip() or "(em branco)"
        cenario = (row.get("cenario") or "").strip() or "(em branco)"
        crit = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        dif = _dificuldade_short(
            row.get("nivel_dificuldade_confer") or row.get("nivel_dificuldade")
        )
        classif_key = _classificacao_display(crit, dif)
        bucket["causas"][causa] += weight
        bucket["etapas"][etapa] += weight
        bucket["cenarios"][cenario] += weight
        bucket["classifs"][classif_key] += weight
        bucket["classif_meta"][classif_key] = {"criticidade": crit, "dificuldade": dif}

    for bucket in clients.values():
        bucket["impacto"] = round(float(bucket["impacto"]), 1)
    return clients


def _prev_impacto_by_cliente(fal_qs, grain: str) -> dict[int, float]:
    totals: dict[int, float] = defaultdict(float)
    protocol_weights: dict[tuple[int, str], float] = {}
    fields = (
        "id_cliente",
        "protocolo",
        "etapa",
        "categoria_falha",
        "nivel_dificuldade",
        "nivel_dificuldade_confer",
        "data",
    )
    for row in fal_qs.values(*fields).iterator(chunk_size=4000):
        raw_cid = row.get("id_cliente")
        if raw_cid is None:
            continue
        try:
            cid = int(raw_cid)
        except (TypeError, ValueError):
            continue
        weight = failure_weight(row)
        if grain == "protocolo":
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (cid, protocolo)
            protocol_weights[key] = max(protocol_weights.get(key, 0.0), weight)
        else:
            totals[cid] += weight
    if grain == "protocolo":
        for (cid, _p), weight in protocol_weights.items():
            totals[cid] += weight
    return {cid: round(v, 1) for cid, v in totals.items()}


def _situacao_from_delta(delta_eo_pp: float | None, *, has_prev: bool) -> str:
    if not has_prev or delta_eo_pp is None:
        return "sem_historico"
    if delta_eo_pp < 0:
        return "piorou"
    if delta_eo_pp > 0:
        return "melhorou"
    return "estavel"


def build_clientes_melhorias_all(
    params,
    *,
    aud_qs=None,
    fal_qs=None,
    scope: str | None = None,
    current_client_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Universo completo de clientes do período filtrado (sem paginar)."""
    grain = resolve_grain(params)
    if aud_qs is None or fal_qs is None:
        def_aud, def_fal = resolve_population_qs(params, scope=scope)
        aud_qs = aud_qs if aud_qs is not None else def_aud
        fal_qs = fal_qs if fal_qs is not None else def_fal

    if current_client_rows is None:
        aud_now = _client_counts(aud_qs, grain)
    else:
        # Insights já agregou todos os clientes (não apenas o top N). Reutilizar
        # esse mapa evita um segundo GROUP BY sobre milhões de auditados.
        aud_now: dict[int, int] = {}
        for row in current_client_rows:
            raw_cid = row.get("id_cliente")
            if raw_cid is None:
                continue
            try:
                aud_now[int(raw_cid)] = int(row.get("auditados") or 0)
            except (TypeError, ValueError):
                continue
    fal_now = _scan_falhas_by_cliente(fal_qs, grain)

    start, end = resolve_date_range(params)
    aud_prev: dict[int, int] = {}
    fal_prev_count: dict[int, int] = {}
    impacto_prev: dict[int, float] = {}
    prev_label = "período anterior"
    prev_start = prev_end = None
    if start and end:
        prev_start, prev_end = shift_period(start, end)
        prev_params = params_with(
            params,
            start_date=prev_start.isoformat(),
            end_date=prev_end.isoformat(),
        )
        prev_label = _period_label(start, end)
        prev_aud_qs, prev_fal = resolve_population_qs(prev_params, scope=scope)
        aud_prev = _client_counts(prev_aud_qs, grain)
        fal_prev_count = _client_counts(prev_fal, grain)
        impacto_prev = _prev_impacto_by_cliente(prev_fal, grain)

    client_ids = set(aud_now) | set(fal_now)
    nomes, _, _ = build_dim_lookups(client_ids, set(), set())

    rows: list[dict[str, Any]] = []
    for cid in client_ids:
        fal_info = fal_now.get(cid) or {
            "falhas": 0,
            "impacto": 0.0,
            "causas": {},
            "etapas": {},
            "cenarios": {},
            "classifs": {},
            "classif_meta": {},
        }
        auditados = int(aud_now.get(cid, 0))
        falhas = int(fal_info["falhas"])
        impacto = float(fal_info["impacto"] or 0.0)
        eo_atual = _eo_pct(auditados, falhas)
        eo_ponderado = _eo_pct(auditados, impacto) if auditados > 0 else None

        prev_aud = int(aud_prev.get(cid, 0))
        prev_fal = int(fal_prev_count.get(cid, 0))
        prev_impacto = impacto_prev.get(cid)
        # Histórico comparável: auditados ou falhas no período anterior.
        has_prev = (prev_aud > 0 or prev_fal > 0) and (
            cid in aud_prev or cid in fal_prev_count
        )
        eo_anterior = (
            _eo_pct(prev_aud, prev_fal)
            if has_prev and prev_aud > 0
            else None
        )
        if has_prev and prev_impacto is None:
            prev_impacto = 0.0

        delta_eo_pp = None
        if eo_atual is not None and eo_anterior is not None:
            delta_eo_pp = round(eo_atual - eo_anterior, 1)

        delta_impacto_pct = None
        if has_prev and prev_impacto is not None and prev_impacto > 0:
            delta_impacto_pct = round(
                100.0 * (impacto - prev_impacto) / prev_impacto, 1
            )

        # Situação pela Δ EO atual (simples). Sem EO atual ou sem histórico → Sem histórico.
        situacao = _situacao_from_delta(
            delta_eo_pp,
            has_prev=has_prev and eo_atual is not None and eo_anterior is not None,
        )

        causa = _top_by_weight(dict(fal_info.get("causas") or {}))
        etapa = _top_by_weight(dict(fal_info.get("etapas") or {}))
        cenario = _top_by_weight(dict(fal_info.get("cenarios") or {}))
        classif = _top_by_weight(dict(fal_info.get("classifs") or {}))
        meta = (fal_info.get("classif_meta") or {}).get(classif or "", {})

        label = (nomes.get(cid) or "").strip() or f"Cliente {cid}"
        rows.append(
            {
                "key": str(cid),
                "id_cliente": cid,
                "label": label,
                "causa_principal": causa if falhas else None,
                "etapa_principal": etapa if falhas else None,
                "cenario": cenario if falhas else None,
                "classificacao": classif if falhas else None,
                "criticidade": meta.get("criticidade") if falhas else None,
                "dificuldade": meta.get("dificuldade") if falhas else None,
                "falhas": falhas,
                "auditados": auditados,
                "eo_atual_pct": eo_atual,
                "eo_ponderado_pct": eo_ponderado,
                "eo_anterior_pct": eo_anterior,
                "delta_eo_pp": delta_eo_pp,
                "impacto_atual": impacto if falhas or auditados else 0.0,
                "impacto_anterior": prev_impacto if has_prev else None,
                "delta_impacto_pct": delta_impacto_pct,
                "situacao": situacao,
                "situacao_label": {
                    "piorou": "Piorou",
                    "melhorou": "Melhorou",
                    "estavel": "Estável",
                    "sem_historico": "Sem histórico",
                }[situacao],
                "previous": {
                    "label": prev_label,
                    "start_date": prev_start.isoformat() if prev_start else None,
                    "end_date": prev_end.isoformat() if prev_end else None,
                    "auditados": prev_aud if has_prev else None,
                    "falhas": prev_fal if has_prev else None,
                },
                "drill": {
                    "id_cliente": str(cid),
                    "module": "cliente",
                },
            }
        )

    total_falhas = sum(int(r.get("falhas") or 0) for r in rows)
    total_falhas_prev = sum(int(fal_prev_count.get(cid, 0)) for cid in fal_prev_count)
    for row in rows:
        falhas = int(row.get("falhas") or 0)
        prev_fal = int((row.get("previous") or {}).get("falhas") or 0)
        participacao = (
            round(100.0 * falhas / total_falhas, 1) if total_falhas > 0 else None
        )
        participacao_ant = (
            round(100.0 * prev_fal / total_falhas_prev, 1)
            if total_falhas_prev > 0 and (row.get("previous") or {}).get("falhas") is not None
            else None
        )
        delta_part = None
        if participacao is not None and participacao_ant is not None:
            delta_part = round(participacao - participacao_ant, 1)
        row["participacao_falhas_pct"] = participacao
        row["participacao_anterior_pct"] = participacao_ant
        row["delta_participacao_pp"] = delta_part

    rows.sort(key=_default_melhorias_sort_key)
    return rows


def _default_melhorias_sort_key(row: dict[str, Any]) -> tuple:
    situacao = row.get("situacao") or "sem_historico"
    delta_part = row.get("delta_participacao_pp")
    # Piorou: maior aumento de participação primeiro (None no fim do grupo).
    if situacao == "piorou":
        part_key = -(delta_part if delta_part is not None else float("-inf"))
    else:
        part_key = 0.0
    return (
        _SITUACAO_ORDER.get(situacao, 9),
        part_key,
        -(float(row.get("participacao_falhas_pct") or 0)),
        -int(row.get("falhas") or 0),
        str(row.get("label") or "").casefold(),
    )


_ORDERING_FIELDS = {
    "label": lambda r: str(r.get("label") or "").casefold(),
    "falhas": lambda r: int(r.get("falhas") or 0),
    "eo_atual_pct": lambda r: (
        float(r["eo_atual_pct"]) if r.get("eo_atual_pct") is not None else float("-inf")
    ),
    "delta_eo_pp": lambda r: (
        float(r["delta_eo_pp"]) if r.get("delta_eo_pp") is not None else float("-inf")
    ),
    "impacto_atual": lambda r: float(r.get("impacto_atual") or 0),
    "delta_impacto_pct": lambda r: (
        float(r["delta_impacto_pct"])
        if r.get("delta_impacto_pct") is not None
        else float("-inf")
    ),
    "participacao_falhas_pct": lambda r: (
        float(r["participacao_falhas_pct"])
        if r.get("participacao_falhas_pct") is not None
        else float("-inf")
    ),
    "delta_participacao_pp": lambda r: (
        float(r["delta_participacao_pp"])
        if r.get("delta_participacao_pp") is not None
        else float("-inf")
    ),
    "situacao": lambda r: _SITUACAO_ORDER.get(r.get("situacao") or "", 9),
}


def _sort_melhorias_rows(
    rows: list[dict[str, Any]],
    *,
    ordering: str,
    reverse: bool,
) -> None:
    if ordering not in _ORDERING_FIELDS:
        rows.sort(key=_default_melhorias_sort_key)
        return
    key_fn = _ORDERING_FIELDS[ordering]
    nulls_last = float("inf") if reverse else float("-inf")

    def sort_key(row: dict[str, Any]):
        primary = key_fn(row)
        if primary == float("-inf"):
            primary = nulls_last
        label = str(row.get("label") or "").casefold()
        if reverse and isinstance(primary, (int, float)):
            return (-primary, label)
        if reverse and ordering == "label":
            return (label,)
        return (primary, label)

    if ordering == "label":
        rows.sort(key=lambda r: str(r.get("label") or "").casefold(), reverse=reverse)
    else:
        rows.sort(key=sort_key)


def paginate_clientes_melhorias(
    all_rows: list[dict[str, Any]],
    params,
) -> dict[str, Any]:
    """Aplica busca/ordenação/página. ``universe_count`` = todos; ``count`` = após busca."""
    q = str(params.get("melhorias_q") or "").strip().casefold()
    filtered = (
        [r for r in all_rows if q in str(r.get("label") or "").casefold()]
        if q
        else list(all_rows)
    )
    ordering = str(params.get("melhorias_ordering") or "").strip()
    direction = str(params.get("melhorias_dir") or "desc").strip().lower()
    reverse = direction != "asc"
    _sort_melhorias_rows(filtered, ordering=ordering, reverse=reverse)

    page_size = min(
        _MAX_PAGE_SIZE,
        max(1, _parse_int(params.get("melhorias_page_size"), _DEFAULT_PAGE_SIZE)),
    )
    page = max(1, _parse_int(params.get("melhorias_page"), 1))
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    results = filtered[start : start + page_size]
    return {
        "ok": True,
        "count": total,
        "universe_count": len(all_rows),
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "q": str(params.get("melhorias_q") or "").strip() or None,
        "ordering": ordering or None,
        "dir": direction if ordering else None,
        "results": results,
        "note": (
            "Participação nas falhas de contestação · comparação com o histórico próprio "
            "de cada cliente no universo Contest*."
            if str(params.get("module") or "") == "contestacao"
            else (
                "Comparação com o período anterior usando somente o histórico de cada cliente. "
                "Base atual/anterior sempre visível para evitar conclusões por queda de volume."
            )
        ),
        "title": (
            "Melhorias dos clientes — participação nas falhas de contestação"
            if str(params.get("module") or "") == "contestacao"
            else "Melhorias dos clientes — participação nas falhas"
        ),
    }


def build_prioridades_etapa(
    params,
    *,
    fal_qs=None,
    total_impacto: float | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Ranking de etapas por impacto ponderado real."""
    grain = resolve_grain(params)
    if fal_qs is None:
        _, fal_qs = resolve_population_qs(params, scope=scope)
    contest_scope = scope == "contestacao"

    etapas: dict[str, dict[str, Any]] = {}
    protocol_weights: dict[tuple[str, str], float] = {}

    for row in fal_qs.values(*_FALHA_SCAN_FIELDS).iterator(chunk_size=4000):
        etapa = (row.get("etapa") or "").strip() or "(em branco)"
        weight = failure_weight(row)
        bucket = etapas.setdefault(
            etapa,
            {
                "etapa": etapa,
                "falhas": 0,
                "impacto": 0.0,
                "cenarios": defaultdict(float),
                "classifs": defaultdict(float),
            },
        )
        if grain == "protocolo":
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (etapa, protocolo)
            prev = protocol_weights.get(key)
            if prev is not None and weight <= prev:
                continue
            if prev is None:
                bucket["falhas"] += 1
            else:
                bucket["impacto"] -= prev
            protocol_weights[key] = weight
            bucket["impacto"] += weight
        else:
            bucket["falhas"] += 1
            bucket["impacto"] += weight

        cenario = (row.get("cenario") or "").strip() or "(em branco)"
        crit = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        dif = _dificuldade_short(
            row.get("nivel_dificuldade_confer") or row.get("nivel_dificuldade")
        )
        bucket["cenarios"][cenario] += weight
        bucket["classifs"][_classificacao_display(crit, dif)] += weight

    rows = []
    for bucket in etapas.values():
        impacto = round(float(bucket["impacto"]), 1)
        rows.append(
            {
                "etapa": bucket["etapa"],
                "label": bucket["etapa"],
                "falhas": int(bucket["falhas"]),
                "impacto_ponderado": impacto,
                "cenario_principal": _top_by_weight(dict(bucket["cenarios"])) or "—",
                "classificacao": _top_by_weight(dict(bucket["classifs"])) or "—",
                "drill": {
                    "etapa": bucket["etapa"] if bucket["etapa"] != "(em branco)" else "",
                    "lista": "falhas",
                    **({"scope": "contestacao"} if contest_scope else {}),
                },
            }
        )

    rows.sort(
        key=lambda r: (
            -float(r["impacto_ponderado"]),
            -int(r["falhas"]),
            str(r["etapa"]).casefold(),
        )
    )
    total_imp = (
        float(total_impacto)
        if total_impacto is not None
        else sum(float(r["impacto_ponderado"]) for r in rows)
    )
    for i, row in enumerate(rows, start=1):
        share = (
            round(100.0 * float(row["impacto_ponderado"]) / total_imp, 1)
            if total_imp
            else None
        )
        row["rank"] = i
        row["impacto_share_pct"] = share
        row["prioridade"] = _impact_label(share)
        row["impacto"] = row["prioridade"]  # compat chip FE

    return {
        "ok": True,
        "title": "Prioridades por etapa",
        "description": (
            "Onde agir primeiro para reduzir falhas de contestação"
            if contest_scope
            else "Onde agir primeiro para reduzir o impacto no EO"
        ),
        "total": len(rows),
        "preview_limit": 6,
        "rows": rows,
        "preview": rows[:6],
        "scope": scope or "eo",
    }


def build_falhas_por_etapa(
    params, *, fal_qs=None, scope: str | None = None
) -> dict[str, Any]:
    """Tabela operacional Falhas por etapa + detalhe (cenários, criticidade, origem).

    Um scan no período atual e outro no anterior (mesma janela). Sem N+1 por etapa.
    """
    grain = resolve_grain(params)
    if fal_qs is None:
        _, fal_qs = resolve_population_qs(params, scope=scope)
    contest_scope = scope == "contestacao"

    def _scan(qs) -> dict[str, dict[str, Any]]:
        etapas: dict[str, dict[str, Any]] = {}
        protocol_weights: dict[tuple[str, str], float] = {}
        for row in qs.values(*_FALHA_SCAN_FIELDS).iterator(chunk_size=4000):
            etapa = (row.get("etapa") or "").strip() or "(em branco)"
            weight = failure_weight(row)
            bucket = etapas.setdefault(
                etapa,
                {
                    "etapa": etapa,
                    "falhas": 0,
                    "impacto": 0.0,
                    "cenarios": defaultdict(float),
                    "criticidades": defaultdict(float),
                    "origens": defaultdict(float),
                    "origem_counts": defaultdict(int),
                    "classifs": defaultdict(float),
                },
            )
            counted = False
            if grain == "protocolo":
                protocolo = str(row.get("protocolo") or "").strip()
                if not protocolo:
                    continue
                key = (etapa, protocolo)
                prev_w = protocol_weights.get(key)
                if prev_w is not None and weight <= prev_w:
                    continue
                if prev_w is None:
                    bucket["falhas"] += 1
                    counted = True
                else:
                    bucket["impacto"] -= prev_w
                protocol_weights[key] = weight
                bucket["impacto"] += weight
            else:
                bucket["falhas"] += 1
                bucket["impacto"] += weight
                counted = True

            cenario = (row.get("cenario") or "").strip() or "(em branco)"
            crit = criticidade_label(
                row.get("categoria_falha"),
                id_cliente=row.get("id_cliente"),
                tipo_registro=row.get("tipo_registro"),
            ) or "Não informada"
            tip = normalize_tipo_conclusao(row.get("tipo_falha"))
            if tip not in {"Manual", "Automático", "Processual"}:
                tip = "Manual" if not tip else tip
            dif = _dificuldade_short(
                row.get("nivel_dificuldade_confer") or row.get("nivel_dificuldade")
            )
            bucket["cenarios"][cenario] += weight
            bucket["criticidades"][crit] += weight
            bucket["origens"][tip] += weight
            if counted:
                bucket["origem_counts"][tip] += 1
            bucket["classifs"][_classificacao_display(crit, dif)] += weight
        for bucket in etapas.values():
            bucket["impacto"] = round(float(bucket["impacto"]), 1)
        return etapas

    current = _scan(fal_qs)

    prev_impact: dict[str, float] = {}
    start, end = resolve_date_range(params)
    prev_start = prev_end = None
    if start and end:
        prev_start, prev_end = shift_period(start, end)
        prev_params = params_with(
            params,
            start_date=prev_start.isoformat(),
            end_date=prev_end.isoformat(),
        )
        _, prev_fal_qs = resolve_population_qs(prev_params, scope=scope)
        for name, bucket in _scan(prev_fal_qs).items():
            prev_impact[name] = float(bucket["impacto"])

    total_falhas = sum(int(b["falhas"]) for b in current.values())
    total_impacto = sum(float(b["impacto"]) for b in current.values())

    def _share_list(weights: dict[str, float], limit: int | None = None) -> list[dict]:
        total = sum(weights.values()) or 0.0
        items = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
        if limit is not None:
            items = items[:limit]
        out = []
        for key, w in items:
            out.append(
                {
                    "key": key,
                    "label": key,
                    "impacto": round(w, 1),
                    "pct": round(100.0 * w / total, 1) if total else None,
                }
            )
        return out

    rows = []
    for bucket in current.values():
        falhas = int(bucket["falhas"])
        impacto = float(bucket["impacto"])
        part = round(100.0 * falhas / total_falhas, 1) if total_falhas else None
        impacto_share = (
            round(100.0 * impacto / total_impacto, 1) if total_impacto else None
        )
        prev_imp = prev_impact.get(bucket["etapa"])
        delta_impacto_pct = None
        if prev_imp is not None and prev_imp > 0:
            delta_impacto_pct = round(100.0 * (impacto - prev_imp) / prev_imp, 1)
        elif prev_imp == 0 and impacto > 0:
            delta_impacto_pct = None

        crit_top = _share_list(dict(bucket["criticidades"]), 1)
        origem_top = _share_list(dict(bucket["origens"]), 1)
        subtitle_parts = []
        if crit_top and crit_top[0].get("pct") is not None:
            subtitle_parts.append(f"{crit_top[0]['label']} {crit_top[0]['pct']}%")
        if origem_top and origem_top[0].get("pct") is not None:
            label = {
                "Manual": "Manual",
                "Automático": "Automático",
                "Processual": "Processual",
            }.get(origem_top[0]["label"], origem_top[0]["label"])
            subtitle_parts.append(f"{label} {origem_top[0]['pct']}%")

        origens_cards = []
        origem_counts = dict(bucket.get("origem_counts") or {})
        origem_count_total = sum(origem_counts.values()) or 0
        for key, label in (
            ("Manual", "Manuais"),
            ("Automático", "Automáticas"),
            ("Processual", "Processuais"),
        ):
            qtd = int(origem_counts.get(key, 0))
            origens_cards.append(
                {
                    "key": key,
                    "label": label,
                    "quantidade": qtd,
                    "pct": (
                        round(100.0 * qtd / origem_count_total, 1)
                        if origem_count_total
                        else None
                    ),
                }
            )

        rows.append(
            {
                "etapa": bucket["etapa"],
                "label": bucket["etapa"],
                "falhas": falhas,
                "participacao_pct": part,
                "impacto_ponderado": impacto,
                "impacto_share_pct": impacto_share,
                "delta_impacto_pct": delta_impacto_pct,
                "subtitle": " · ".join(subtitle_parts) if subtitle_parts else "",
                "cenarios": _share_list(dict(bucket["cenarios"]), 5),
                "criticidades": _share_list(dict(bucket["criticidades"])),
                "origens": origens_cards,
                "classificacao": _top_by_weight(dict(bucket["classifs"])) or "—",
                "drill": {
                    "etapa": bucket["etapa"] if bucket["etapa"] != "(em branco)" else "",
                    "lista": "falhas",
                    **({"scope": "contestacao"} if contest_scope else {}),
                },
            }
        )

    rows.sort(
        key=lambda r: (
            -float(r["impacto_ponderado"]),
            -int(r["falhas"]),
            str(r["etapa"]).casefold(),
        )
    )
    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    return {
        "ok": True,
        "title": "Falhas por etapa",
        "description": (
            "Falhas de contestação por etapa · impacto no universo Contest*"
            if contest_scope
            else "Impacto conforme a regra vigente do EO · mesmos filtros globais"
        ),
        "total": len(rows),
        "total_falhas": total_falhas,
        "total_impacto": round(total_impacto, 1),
        "preview_limit": 8,
        "metric_default": "impacto_ponderado",
        "scope": scope or "eo",
        "previous": {
            "start_date": prev_start.isoformat() if prev_start else None,
            "end_date": prev_end.isoformat() if prev_end else None,
        },
        "rows": rows,
        "preview": rows[:8],
    }


def build_contestacao_leitura(
    params, *, base: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Painel Contestação — leitura rápida (mesmo escopo/filtros do EO).

    Volume alinhado ao KPI strip: ``auditados_contestacao`` = linhas Contest*
    (não protocolos distintos). Taxa de falha = eventos ÷ auditados (mesma base do EO).
    Improcedência permanece em base de protocolos distintos.
    """
    base = base or build_contestacao_metrics(
        params, include_auditados_contestacao=True
    )
    fal_qs = _contestacao_falhas_qs(params)
    aud_qs = _contestacao_auditados_qs(params)

    auditados = int(base.get("auditados_contestacao") or 0)
    contestados = int(base.get("protocolos_contestados") or 0)
    casos_contestacao = int(base.get("casos_contestacao") or 0)
    falhas_prot = int(base.get("protocolos_com_falha_contestacao") or 0)
    eventos = int(base.get("eventos_contestacao") or 0)
    # Mesma base do EO da aba: falhas (linhas) ÷ auditados (linhas)
    taxa = _pct(eventos, auditados)
    eo_pct = _eo_pct(auditados, eventos)
    taxa_protocolo = _pct(falhas_prot, contestados)

    prev = base.get("previous") or {}
    prev_contestados = prev.get("protocolos_contestados")
    prev_eventos = prev.get("eventos_contestacao")
    # M-1 pode omitir auditados_contestacao; estimar taxa por eventos/protocolos só se
    # não houver base de linhas — preferir mesma fórmula quando possível.
    prev_auditados = prev.get("auditados_contestacao")
    if prev_auditados:
        prev_taxa = _pct(prev_eventos, prev_auditados)
    elif prev_contestados:
        prev_falhas_prot = prev.get("procedentes")
        prev_taxa = _pct(prev_falhas_prot, prev_contestados)
    else:
        prev_taxa = None
    delta_taxa_pct = None
    if taxa is not None and prev_taxa is not None and prev_taxa > 0:
        delta_taxa_pct = round(100.0 * (taxa - prev_taxa) / prev_taxa, 1)
    elif taxa is not None and prev_taxa == 0 and taxa > 0:
        delta_taxa_pct = None

    delta_falhas_pct = None
    prev_falhas_n = int(prev_eventos) if prev_eventos is not None else None
    if prev_falhas_n is not None and prev_falhas_n > 0:
        delta_falhas_pct = round(100.0 * (eventos - prev_falhas_n) / prev_falhas_n, 1)
    elif prev_falhas_n == 0 and eventos > 0:
        delta_falhas_pct = None

    # Improcedência por cliente (protocolos sem falha ÷ protocolos contestados)
    from django.db.models import Count

    contestados_by_cli: dict[int, int] = {}
    for row in (
        aud_qs.exclude(protocolo="")
        .exclude(id_cliente__isnull=True)
        .values("id_cliente")
        .annotate(c=Count("protocolo", distinct=True))
    ):
        try:
            cid = int(row["id_cliente"])
        except (TypeError, ValueError):
            continue
        contestados_by_cli[cid] = int(row["c"])

    procedentes_by_cli: dict[int, int] = {}
    for row in (
        fal_qs.exclude(protocolo="")
        .exclude(id_cliente__isnull=True)
        .values("id_cliente")
        .annotate(c=Count("protocolo", distinct=True))
    ):
        try:
            cid = int(row["id_cliente"])
        except (TypeError, ValueError):
            continue
        procedentes_by_cli[cid] = int(row["c"])

    cliente_ids = set(contestados_by_cli) | set(procedentes_by_cli)
    nomes, _, _ = build_dim_lookups(cliente_ids, set(), set())
    clientes_all: list[dict[str, Any]] = []
    for cid in cliente_ids:
        cont = int(contestados_by_cli.get(cid, 0))
        proc = int(procedentes_by_cli.get(cid, 0))
        if cont <= 0 and proc <= 0:
            continue
        # Protocolos só em falha (sem auditado Contest*) entram como procedentes.
        base_cont = max(cont, proc)
        impro = max(0, base_cont - proc)
        label = (nomes.get(cid) or "").strip() or f"Cliente {cid}"
        clientes_all.append(
            {
                "key": str(cid),
                "id_cliente": cid,
                "label": label,
                "contestados": base_cont,
                "procedentes": proc,
                "improcedentes": impro,
                "quantidade": impro,
                "pct": _pct(impro, base_cont),
                "metric": "improcedencia",
            }
        )
    # Maior volume contestado primeiro; empate: maior improcedência %
    clientes_all.sort(
        key=lambda r: (
            -int(r.get("contestados") or 0),
            -(float(r["pct"]) if r.get("pct") is not None else -1.0),
            str(r.get("label") or "").casefold(),
        )
    )
    clientes = clientes_all[:12]

    improcedentes = int(base.get("improcedentes") or 0)
    procedentes = int(base.get("procedentes") or 0)
    improcedencia = {
        "improcedentes": improcedentes,
        "procedentes": procedentes,
        "contestados": auditados,
        "pct": _pct(improcedentes, auditados),
    }

    # Origem das análises (falhas contestação)
    origem_counts: dict[str, int] = defaultdict(int)
    etapa_counts: dict[str, int] = defaultdict(int)
    filled_etapa = filled_cenario = filled_crit = 0
    total_fal_rows = 0
    for row in fal_qs.values(
        "tipo_falha",
        "etapa",
        "cenario",
        "categoria_falha",
    ).iterator(chunk_size=4000):
        total_fal_rows += 1
        tip = normalize_tipo_conclusao(row.get("tipo_falha"))
        if tip not in {"Manual", "Automático", "Processual"}:
            tip = "Manual" if not tip else tip
        if tip in {"Manual", "Automático", "Processual"}:
            origem_counts[tip] += 1
        else:
            origem_counts["Outros"] += 1
        etapa = (row.get("etapa") or "").strip()
        if etapa:
            filled_etapa += 1
            etapa_counts[etapa] += 1
        else:
            etapa_counts["(em branco)"] += 1
        if (row.get("cenario") or "").strip():
            filled_cenario += 1
        if (row.get("categoria_falha") or "").strip():
            filled_crit += 1

    origem_total = sum(origem_counts.values()) or 0
    origens = []
    for key in ("Manual", "Automático", "Processual"):
        label = {
            "Manual": "Manuais",
            "Automático": "Automáticas",
            "Processual": "Processuais",
        }[key]
        qtd = int(origem_counts.get(key, 0))
        origens.append(
            {
                "key": key,
                "label": label,
                "quantidade": qtd,
                "pct": round(100.0 * qtd / origem_total, 1) if origem_total else None,
            }
        )

    etapas_sorted = sorted(etapa_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    fal_base = total_fal_rows or eventos or 1
    etapas = [
        {
            "etapa": name,
            "falhas": qtd,
            "pct": round(100.0 * qtd / fal_base, 1) if total_fal_rows else None,
            "drill": {"etapa": name if name != "(em branco)" else "", "lista": "falhas"},
        }
        for name, qtd in etapas_sorted
    ]

    cobertura = {
        "etapa_pct": _pct(filled_etapa, total_fal_rows),
        "cenario_pct": _pct(filled_cenario, total_fal_rows),
        "criticidade_pct": _pct(filled_crit, total_fal_rows),
        "base_falhas": total_fal_rows,
    }

    return {
        "ok": True,
        "title": "Contestação — leitura rápida",
        "description": "Volume, falhas, tendência e concentração das contestações no período",
        # Volume alinhado ao KPI "Auditados (contestação)" (linhas, grain=etapa)
        "auditados_contestacao": auditados,
        "protocolos_contestados": contestados,
        "casos_contestacao": casos_contestacao,
        "falhas_contestacao": eventos,
        "protocolos_com_falha_contestacao": falhas_prot,
        "eo_pct": eo_pct,
        "taxa_falha_pct": taxa,
        "taxa_rule": "falhas_contestacao ÷ auditados_contestacao",
        "taxa_falha_protocolo_pct": taxa_protocolo,
        "vs_periodo_anterior": {
            "delta_taxa_pct": delta_taxa_pct,
            "delta_falhas_pct": delta_falhas_pct,
            "falhas_anterior": prev_falhas_n,
            "taxa_anterior_pct": prev_taxa,
            "label": prev.get("label") or "período anterior",
            "start_date": prev.get("start_date"),
            "end_date": prev.get("end_date"),
        },
        "clientes": clientes,
        "improcedencia": improcedencia,
        "origens": origens,
        "etapas": etapas,
        "etapas_preview": etapas[:5],
        "cobertura": cobertura,
        "link": {
            "route_name": "indicadores-qualidade",
            "module": "contestacao",
            "query_keys": ["start_date", "end_date"],
        },
    }


def _normalize_evidence_dim(raw: Any) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return NAO_INFORMADO_KEY, NAO_INFORMADO_LABEL
    return text, text


_EVIDENCE_SCAN_FIELDS = ("tipo_documento", "cenario", "localidade_documento", "protocolo")


def _scan_documento_e_cenario(
    qs, *, grain: str
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Uma varredura: conta falhas por tipo_documento, cenario e localidade."""
    docs: dict[str, dict[str, Any]] = {}
    scenes: dict[str, dict[str, Any]] = {}
    locais: dict[str, dict[str, Any]] = {}
    seen_docs: set[tuple[str, str]] = set()
    seen_scenes: set[tuple[str, str]] = set()
    seen_locais: set[tuple[str, str]] = set()

    def _acc(
        buckets: dict[str, dict[str, Any]],
        seen: set[tuple[str, str]],
        key: str,
        label: str,
        protocolo: str,
    ) -> None:
        bucket = buckets.setdefault(key, {"key": key, "label": label, "falhas": 0})
        if grain == "protocolo":
            if not protocolo:
                return
            pkey = (key, protocolo)
            if pkey in seen:
                return
            seen.add(pkey)
            bucket["falhas"] += 1
            return
        bucket["falhas"] += 1

    for row in qs.values(*_EVIDENCE_SCAN_FIELDS).iterator(chunk_size=4000):
        protocolo = str(row.get("protocolo") or "").strip()
        d_key, d_label = _normalize_evidence_dim(row.get("tipo_documento"))
        s_key, s_label = _normalize_evidence_dim(row.get("cenario"))
        l_key, l_label = _normalize_evidence_dim(row.get("localidade_documento"))
        _acc(docs, seen_docs, d_key, d_label, protocolo)
        _acc(scenes, seen_scenes, s_key, s_label, protocolo)
        _acc(locais, seen_locais, l_key, l_label, protocolo)
    return docs, scenes, locais


def _evidence_tendencia(
    atual: int, anterior: int | None, *, has_previous: bool
) -> str:
    if not has_previous or anterior is None:
        return "sem_historico"
    if atual > 0 and anterior == 0:
        return "novo"
    if atual > anterior:
        return "aumentou"
    if atual < anterior:
        return "diminuiu"
    return "estavel"


def _assemble_evidence_rows(
    current: dict[str, dict[str, Any]],
    previous: dict[str, dict[str, Any]],
    *,
    field: str,
    has_previous: bool,
) -> tuple[list[dict[str, Any]], int]:
    total_falhas = sum(int(b["falhas"]) for b in current.values())
    prev_total_falhas = (
        sum(int(b["falhas"]) for b in previous.values()) if has_previous else 0
    )

    rows: list[dict[str, Any]] = []
    for key, bucket in current.items():
        falhas = int(bucket["falhas"])
        prev_bucket = previous.get(key) if has_previous else None
        falhas_ant: int | None
        if not has_previous:
            falhas_ant = None
        elif prev_bucket is None:
            falhas_ant = 0
        else:
            falhas_ant = int(prev_bucket["falhas"])

        part = round(100.0 * falhas / total_falhas, 1) if total_falhas else None
        part_ant = None
        if has_previous and falhas_ant is not None and prev_total_falhas:
            part_ant = round(100.0 * falhas_ant / prev_total_falhas, 1)

        delta_falhas = None if falhas_ant is None else falhas - falhas_ant
        delta_falhas_pct = None
        if falhas_ant is not None and falhas_ant > 0:
            delta_falhas_pct = round(100.0 * (falhas - falhas_ant) / falhas_ant, 1)
        delta_part_pp = None
        if part is not None and part_ant is not None:
            delta_part_pp = round(part - part_ant, 1)

        rows.append(
            {
                "key": key,
                "label": bucket["label"],
                "falhas": falhas,
                "participacao_pct": part,
                "falhas_anterior": falhas_ant,
                "participacao_anterior_pct": part_ant,
                "delta_falhas": delta_falhas,
                "delta_falhas_pct": delta_falhas_pct,
                "delta_participacao_pp": delta_part_pp,
                "tendencia": _evidence_tendencia(
                    falhas, falhas_ant, has_previous=has_previous
                ),
                "drill": {field: key, "lista": "falhas"},
            }
        )

    rows.sort(
        key=lambda r: (
            -int(r["falhas"]),
            -(float(r["participacao_pct"]) if r["participacao_pct"] is not None else -1.0),
            str(r["label"]).casefold(),
        )
    )
    return rows, total_falhas


def build_falhas_documento_e_cenarios(
    params, *, fal_qs=None
) -> dict[str, dict[str, Any]]:
    """Dois blocos do Resumo: volume, participação e tendência (sem impacto).

    Só QualidadeFalha. Não calcula EO, taxa nem failure_weight.
    Com grain=protocolo, a soma das participações pode ultrapassar 100% se o
    mesmo protocolo aparecer em mais de uma categoria.
    """
    grain = resolve_grain(params)
    if fal_qs is None:
        fal_qs = filtered_falhas(params)

    start, end = resolve_date_range(params)
    has_previous = start is not None and end is not None
    prev_start = prev_end = None
    prev_docs: dict[str, dict[str, Any]] = {}
    prev_scenes: dict[str, dict[str, Any]] = {}
    prev_locais: dict[str, dict[str, Any]] = {}
    if has_previous:
        prev_start, prev_end = shift_period(start, end)
        prev_params = params_with(
            params,
            start_date=prev_start.isoformat(),
            end_date=prev_end.isoformat(),
        )
        prev_docs, prev_scenes, prev_locais = _scan_documento_e_cenario(
            filtered_falhas(prev_params), grain=grain
        )

    cur_docs, cur_scenes, cur_locais = _scan_documento_e_cenario(fal_qs, grain=grain)

    doc_rows, doc_total_f = _assemble_evidence_rows(
        cur_docs,
        prev_docs,
        field="tipo_documento",
        has_previous=has_previous,
    )
    scene_rows, scene_total_f = _assemble_evidence_rows(
        cur_scenes,
        prev_scenes,
        field="cenario",
        has_previous=has_previous,
    )
    local_rows, local_total_f = _assemble_evidence_rows(
        cur_locais,
        prev_locais,
        field="localidade",
        has_previous=has_previous,
    )

    previous_meta = {
        "start_date": prev_start.isoformat() if prev_start else None,
        "end_date": prev_end.isoformat() if prev_end else None,
    }
    note = (
        "Com granularidade por protocolo, a soma das participações pode "
        "ultrapassar 100% se o mesmo protocolo aparecer em mais de uma categoria."
        if grain == "protocolo"
        else None
    )

    return {
        "falhas_por_tipo_documento": {
            "ok": True,
            "dimension": "tipo_documento",
            "title": "Falhas por tipo de documento",
            "description": (
                "Volume, participação e variação das falhas por tipo documental"
            ),
            "grain": grain,
            "default_order": "falhas",
            "total_falhas": doc_total_f,
            "total_categorias": len(doc_rows),
            "preview_limit": EVIDENCE_PREVIEW_LIMIT,
            "truncated": len(doc_rows) > EVIDENCE_PREVIEW_LIMIT,
            "previous": previous_meta,
            "rows": doc_rows,
            "note": note,
        },
        "cenarios_em_evidencia": {
            "ok": True,
            "dimension": "cenario",
            "title": "Cenários em evidência",
            "description": (
                "Cenários com maior volume e representatividade no período selecionado"
            ),
            "grain": grain,
            "default_order": "falhas",
            "total_falhas": scene_total_f,
            "total_categorias": len(scene_rows),
            "preview_limit": EVIDENCE_PREVIEW_LIMIT,
            "truncated": len(scene_rows) > EVIDENCE_PREVIEW_LIMIT,
            "previous": previous_meta,
            "rows": scene_rows,
            "note": note,
        },
        "falhas_por_localidade": {
            "ok": True,
            "dimension": "localidade",
            "title": "Falhas por localidade",
            "description": (
                "Volume, participação e variação das falhas por localidade operacional"
            ),
            "grain": grain,
            "default_order": "falhas",
            "total_falhas": local_total_f,
            "total_categorias": len(local_rows),
            "preview_limit": EVIDENCE_PREVIEW_LIMIT,
            "truncated": len(local_rows) > EVIDENCE_PREVIEW_LIMIT,
            "previous": previous_meta,
            "rows": local_rows,
            "note": note,
        },
    }


def build_resumo_extensions(
    params,
    *,
    aud_qs=None,
    fal_qs=None,
    kpis: dict | None = None,
    insights: dict | None = None,
) -> dict[str, Any]:
    """Payload interno (pré-paginação) anexado ao dashboard Resumo."""
    melhorias_all = build_clientes_melhorias_all(
        params,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        current_client_rows=(insights or {}).get("top_clientes"),
    )
    total_impacto = (kpis or {}).get("impacto_ponderado")
    prioridades = build_prioridades_etapa(
        params, fal_qs=fal_qs, total_impacto=total_impacto
    )
    contestacao = build_contestacao_leitura(
        params, base=(kpis or {}).get("contestacao")
    )
    evidencias = build_falhas_documento_e_cenarios(params, fal_qs=fal_qs)
    return {
        "_clientes_melhorias_all": melhorias_all,
        "prioridades_etapa": prioridades,
        "contestacao_leitura": contestacao,
        "falhas_por_tipo_documento": evidencias["falhas_por_tipo_documento"],
        "cenarios_em_evidencia": evidencias["cenarios_em_evidencia"],
        "falhas_por_localidade": evidencias["falhas_por_localidade"],
    }


def finalize_resumo_extensions(payload: dict, params) -> dict:
    """Aplica paginação/busca das melhorias e omite a lista completa do response."""
    out = {k: v for k, v in payload.items() if k != "_clientes_melhorias_all"}
    all_rows = payload.get("_clientes_melhorias_all")
    if all_rows is None:
        out.setdefault(
            "clientes_melhorias",
            {
                "ok": True,
                "count": 0,
                "universe_count": 0,
                "page": 1,
                "page_size": _DEFAULT_PAGE_SIZE,
                "total_pages": 1,
                "results": [],
            },
        )
        return out
    out["clientes_melhorias"] = paginate_clientes_melhorias(all_rows, params)
    return out
