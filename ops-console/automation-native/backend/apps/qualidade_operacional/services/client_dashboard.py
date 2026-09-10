# -*- coding: utf-8 -*-
"""Dashboard consolidado da aba Cliente (visão 360°)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import Count

from apps.qualidade_operacional.services.analytics import (
    _build_breakdown,
    _build_kpis,
    _count_qs,
    build_ranking,
    build_serie,
    failure_weight,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.contestacao_metrics import (
    build_contestacao_metrics,
)
from apps.qualidade_operacional.services.enrichment import build_dim_lookups
from apps.qualidade_operacional.services.queries import param_list


EO_META_PCT = 99.7


def _params_with(params, **updates):
    copied = params.copy() if hasattr(params, "copy") else dict(params)
    for key, value in updates.items():
        copied[key] = value
    return copied


def _require_cliente_id(params) -> int | None:
    values = param_list(params, "id_cliente")
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def _params_without_cliente(params):
    copied = params.copy() if hasattr(params, "copy") else dict(params)
    if hasattr(copied, "setlist"):
        copied.setlist("id_cliente", [])
    else:
        copied.pop("id_cliente", None)
    return copied


def _initials(nome: str) -> str:
    parts = [p for p in (nome or "").strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[-1][0]}".upper()


def _pct(part: float | int | None, total: float | int | None, digits: int = 1) -> float | None:
    if part is None or total is None or not total:
        return None
    return round(100.0 * float(part) / float(total), digits)


def _journey(kpis: dict, contestacao: dict) -> dict[str, Any]:
    auditados = int(kpis.get("protocolos_auditados") or 0)
    com_falha = int(kpis.get("protocolos_com_falha") or 0)
    contestados = int(contestacao.get("protocolos_contestados") or 0)
    auditados_contestacao = int(contestacao.get("auditados_contestacao") or 0)
    procedentes = contestacao.get("procedentes")
    procedentes_n = int(procedentes) if procedentes is not None else None
    steps = [
        {
            "key": "auditados",
            "label": "Protocolos auditados",
            "value": auditados,
            "pct_prev": 100.0 if auditados else None,
            "pct_total": 100.0 if auditados else None,
            "drill": {"lista": "auditados"},
        },
        {
            "key": "com_falha",
            "label": "Protocolos com falha",
            "value": com_falha,
            "pct_prev": _pct(com_falha, auditados),
            "pct_total": _pct(com_falha, auditados),
            "drill": {"lista": "falhas"},
        },
        {
            "key": "contestados",
            "label": "Protocolos contestados",
            "value": contestados,
            "pct_prev": _pct(contestados, com_falha),
            "pct_total": _pct(contestados, auditados),
            "drill": {"lista": "contestacoes"},
        },
        {
            "key": "procedentes",
            "label": "Contestações procedentes",
            "value": procedentes_n,
            "pct_prev": _pct(procedentes_n, auditados_contestacao) if procedentes_n is not None else None,
            "pct_total": _pct(procedentes_n, auditados) if procedentes_n is not None else None,
            "unavailable": procedentes_n is None,
            "drill": {"lista": "contestacoes"},
        },
        {
            "key": "acoes_abertas",
            "label": "Ações corretivas abertas",
            "value": None,
            "pct_prev": None,
            "pct_total": None,
            "unavailable": True,
            "message": "Plano de ação ainda não integrado à base de Qualidade.",
        },
    ]
    return {"ok": True, "steps": steps}


def _build_insights_cliente(kpis: dict, contestacao: dict, causas: list, ranking: dict) -> list[dict]:
    items: list[dict] = []
    if causas:
        top = causas[0]
        share = top.get("share_pct")
        items.append(
            {
                "priority": 1,
                "title": f"{top.get('label')} concentra o maior impacto",
                "evidence": (
                    f"{top.get('impacto_ponderado')} pts"
                    + (f" · {share}% do impacto do cliente" if share is not None else "")
                ),
                "action": "Detalhar causa no Pareto e filtrar protocolos.",
                "drill": {"dim": "des_problemas", "key": top.get("key")},
            }
        )
    taxa_proc = kpis.get("taxa_procedencia_pct")
    if taxa_proc is not None and contestacao.get("auditados_contestacao"):
        items.append(
            {
                "priority": 2,
                "title": f"{taxa_proc}% das contestações são procedentes",
                "evidence": (
                    f"{contestacao.get('procedentes') or 0} procedentes · "
                    f"{contestacao.get('auditados_contestacao')} análises de contestação"
                ),
                "action": "Revisar critérios das contestações procedentes.",
                "drill": {"lista": "contestacoes"},
            }
        )
    leaders = (ranking.get("results") or [])[:2]
    if leaders:
        total_fal = sum(int(r.get("falhas") or 0) for r in ranking.get("results") or []) or 1
        top_fal = sum(int(r.get("falhas") or 0) for r in leaders)
        share = round(100.0 * top_fal / total_fal, 1)
        items.append(
            {
                "priority": 3,
                "title": f"{len(leaders)} líderes concentram {share}% das falhas",
                "evidence": " · ".join(
                    f"{r.get('label')}: {r.get('falhas')} falhas" for r in leaders
                ),
                "action": "Expandir responsáveis e aplicar plano de atuação.",
                "drill": {"lider": leaders[0].get("lider") or leaders[0].get("label")},
            }
        )
    eo = kpis.get("eo_ponderado_pct")
    if eo is not None and eo < EO_META_PCT and len(items) < 3:
        items.append(
            {
                "priority": len(items) + 1,
                "title": f"EO {eo}% abaixo da meta {EO_META_PCT}%",
                "evidence": f"Δ {round(eo - EO_META_PCT, 1)} p.p. versus meta.",
                "action": "Priorizar causas e localidades com maior impacto.",
                "drill": {},
            }
        )
    return items[:3]


def _causas_pareto(fal_qs, *, limit: int = 8) -> list[dict]:
    """Agrupa por des_problemas (causa) com falhas + impacto aproximado."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in fal_qs.values(
        "des_problemas",
        "etapa",
        "categoria_falha",
        "nivel_dificuldade",
        "nivel_dificuldade_confer",
        "id_cliente",
        "protocolo",
        "data",
    ).iterator(chunk_size=2000):
        label = (row.get("des_problemas") or "").strip() or "(sem causa)"
        bucket = grouped.setdefault(
            label,
            {"key": label, "label": label, "falhas": 0, "impacto_ponderado": 0.0, "protocols": set()},
        )
        bucket["falhas"] += 1
        weight = failure_weight(
            {
                "etapa": row.get("etapa"),
                "categoria_falha": row.get("categoria_falha"),
                "nivel_dificuldade": row.get("nivel_dificuldade_confer")
                or row.get("nivel_dificuldade"),
                "id_cliente": row.get("id_cliente"),
                "data": row.get("data"),
            }
        )
        bucket["impacto_ponderado"] += weight
        prot = (row.get("protocolo") or "").strip()
        if prot:
            bucket["protocols"].add(prot)

    rows = []
    total_impacto = sum(b["impacto_ponderado"] for b in grouped.values()) or 0.0
    for bucket in grouped.values():
        impacto = round(bucket["impacto_ponderado"], 1)
        rows.append(
            {
                "key": bucket["key"],
                "label": bucket["label"],
                "falhas": bucket["falhas"],
                "impacto_ponderado": impacto,
                "protocolos": len(bucket["protocols"]),
                "share_pct": round(100.0 * impacto / total_impacto, 1) if total_impacto else None,
            }
        )
    rows.sort(key=lambda r: (-r["impacto_ponderado"], -r["falhas"]))
    cumulative = 0.0
    out = []
    for row in rows[:limit]:
        share = row["share_pct"] or 0.0
        cumulative += share
        out.append({**row, "cumulative_pct": round(min(cumulative, 100.0), 1)})
    return out


def _concentration_map(fal_qs, aud_qs) -> dict[str, Any]:
    """Matriz localidade × etapa com impacto."""
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for row in fal_qs.values(
        "localidade_documento",
        "etapa",
        "categoria_falha",
        "nivel_dificuldade",
        "nivel_dificuldade_confer",
        "id_cliente",
        "protocolo",
        "data",
    ).iterator(chunk_size=2000):
        loc = (row.get("localidade_documento") or "").strip() or "(em branco)"
        etapa = (row.get("etapa") or "").strip() or "(em branco)"
        key = (loc, etapa)
        cell = cells.setdefault(
            key,
            {"localidade": loc, "etapa": etapa, "falhas": 0, "impacto_ponderado": 0.0, "protocols": set()},
        )
        cell["falhas"] += 1
        cell["impacto_ponderado"] += failure_weight(
            {
                "etapa": row.get("etapa"),
                "categoria_falha": row.get("categoria_falha"),
                "nivel_dificuldade": row.get("nivel_dificuldade_confer")
                or row.get("nivel_dificuldade"),
                "id_cliente": row.get("id_cliente"),
                "data": row.get("data"),
            }
        )
        prot = (row.get("protocolo") or "").strip()
        if prot:
            cell["protocols"].add(prot)

    # Auditados por etapa (localidade ausente em auditados)
    aud_by_etapa = {
        (r["etapa"] or "").strip() or "(em branco)": int(r["c"])
        for r in aud_qs.values("etapa").annotate(c=Count("id"))
    }

    localidades = sorted({k[0] for k in cells})[:12]
    etapas = sorted({k[1] for k in cells})[:10]
    matrix = []
    max_impacto = max((c["impacto_ponderado"] for c in cells.values()), default=0) or 1
    for loc in localidades:
        for etapa in etapas:
            cell = cells.get((loc, etapa))
            impacto = round(cell["impacto_ponderado"], 1) if cell else 0.0
            falhas = cell["falhas"] if cell else 0
            matrix.append(
                {
                    "localidade": loc,
                    "etapa": etapa,
                    "impacto_ponderado": impacto,
                    "falhas": falhas,
                    "protocolos_com_falha": len(cell["protocols"]) if cell else 0,
                    "auditados_etapa": aud_by_etapa.get(etapa),
                    "intensity": round(impacto / max_impacto, 3) if impacto else 0,
                }
            )
    return {
        "ok": True,
        "rows": localidades,
        "cols": etapas,
        "metric": "impacto",
        "cells": matrix,
    }


def _investigation_protocols(fal_qs, *, limit: int = 20) -> list[dict]:
    scored: dict[str, dict[str, Any]] = {}
    for row in fal_qs.order_by("-data", "-id")[:2000]:
        prot = (row.protocolo or "").strip()
        if not prot:
            continue
        weight = failure_weight(
            {
                "etapa": row.etapa,
                "categoria_falha": row.categoria_falha,
                "nivel_dificuldade": row.nivel_dificuldade_confer or row.nivel_dificuldade,
                "id_cliente": row.id_cliente,
                "data": row.data,
            }
        )
        is_contest = bool(
            "contest" in (row.tipo_analise or "").casefold()
            or "contest" in (row.modulo or "").casefold()
        )
        current = scored.get(prot)
        if not current:
            scored[prot] = {
                "protocolo": prot,
                "data": row.data.isoformat() if row.data else (
                    row.data_analise.isoformat() if row.data_analise else None
                ),
                "workflow_id": row.id_workflow,
                "etapa": row.etapa or "",
                "localidade": row.localidade or "",
                "causa": (row.des_problemas or "").strip() or "—",
                "impacto_ponderado": weight,
                "contestacao": is_contest,
                "lider": row.lider or "",
                "matricula": row.matricula or "",
                "tipo_analise": row.tipo_analise or "",
                "tipo_falha": row.tipo_falha or "",
                "categoria_falha": row.categoria_falha or "",
                "status_contestacao": "Contestação" if is_contest else "—",
                "procedencia": None,
                "status_acao": "—",
            }
        else:
            # Grain protocolo: maior peso entre linhas do mesmo protocolo (não soma).
            current["impacto_ponderado"] = round(
                max(float(current["impacto_ponderado"]), weight), 1
            )
            current["contestacao"] = current["contestacao"] or is_contest
            if is_contest:
                current["status_contestacao"] = "Contestação"

    rows = sorted(
        scored.values(),
        key=lambda r: (-float(r["impacto_ponderado"]), -int(bool(r["contestacao"]))),
    )
    return rows[:limit]


def build_client_dashboard(params) -> dict[str, Any]:
    cliente_id = _require_cliente_id(params)
    if cliente_id is None:
        return {
            "ok": True,
            "module": "cliente",
            "requires_cliente": True,
            "message": "Selecione um cliente para visualizar a análise detalhada.",
        }

    aud_qs = filtered_auditados(params)
    fal_qs = filtered_falhas(params)
    kpis = _build_kpis(params, aud_qs=aud_qs, fal_qs=fal_qs)

    protocolos_auditados = (
        aud_qs.exclude(protocolo="").values("protocolo").distinct().count()
    )
    protocolos_com_falha = (
        fal_qs.exclude(protocolo="").values("protocolo").distinct().count()
    )
    contestacao = build_contestacao_metrics(params)
    # Procedência já vem de build_contestacao_metrics (atividades ÷ contestados).
    procedencia = {
        "ok": contestacao.get("procedencia_disponivel"),
        "available": contestacao.get("procedencia_disponivel"),
        "taxa_procedencia_pct": contestacao.get("taxa_procedencia_pct"),
        "procedentes": contestacao.get("procedentes"),
        "finalizadas": contestacao.get("finalizadas"),
        "contestados": contestacao.get("auditados_contestacao"),
        "message": contestacao.get("procedencia_message"),
        "rule": contestacao.get("procedencia_rule"),
    }

    kpis = dict(kpis)
    kpis["protocolos_auditados"] = protocolos_auditados
    kpis["protocolos_com_falha"] = protocolos_com_falha
    kpis["protocolos_contestados"] = contestacao["protocolos_contestados"]
    kpis["eventos_contestacao"] = contestacao["eventos_contestacao"]
    kpis["contestacao"] = contestacao
    kpis["taxa_falha_protocolo_pct"] = _pct(protocolos_com_falha, protocolos_auditados, 2)
    kpis["taxa_contestacao_sobre_falha_pct"] = _pct(
        contestacao["protocolos_contestados"], protocolos_com_falha, 1
    )
    kpis["taxa_procedencia_pct"] = procedencia.get("taxa_procedencia_pct")
    kpis["procedencia"] = procedencia
    kpis["procedentes"] = contestacao.get("procedentes")

    # Carteira (mesmo período, sem cliente) para benchmark
    carteira_params = _params_without_cliente(params)
    carteira_aud = filtered_auditados(carteira_params)
    carteira_fal = filtered_falhas(carteira_params)
    carteira_kpis = _build_kpis(carteira_params, aud_qs=carteira_aud, fal_qs=carteira_fal)
    carteira_contest = build_contestacao_metrics(carteira_params)
    carteira_impacto = float(carteira_kpis.get("impacto_ponderado") or 0)
    cliente_impacto = float(kpis.get("impacto_ponderado") or 0)

    carteira_aud_n = _count_qs(carteira_aud, "etapa")
    carteira_prot_aud = (
        carteira_aud.exclude(protocolo="").values("protocolo").distinct().count()
    )
    carteira_prot_fal = (
        carteira_fal.exclude(protocolo="").values("protocolo").distinct().count()
    )

    lookups_clientes, _lookups_wf, _lookups_mat = build_dim_lookups(
        {cliente_id}, set(), set()
    )
    nome = lookups_clientes.get(cliente_id) or f"Cliente {cliente_id}"

    serie = build_serie(_params_with(params, mode="eo", weighted="1"))
    by_day = contestacao.get("by_day") or {}
    by_day_proc = contestacao.get("by_day_procedentes") or {}
    points = []
    for point in serie.get("points") or []:
        enriched = dict(point)
        day_key = point.get("date")
        contestados = int(by_day.get(day_key, 0) or 0)
        procedentes = int(by_day_proc.get(day_key, 0) or 0)
        enriched["contestacoes"] = contestados
        if contestados > 0:
            enriched["improcedencia_pct"] = round(
                100.0 * max(0, contestados - procedentes) / contestados, 1
            )
            enriched["procedentes_contestacao"] = procedentes
        else:
            enriched["improcedencia_pct"] = None
            enriched["procedentes_contestacao"] = 0
        points.append(enriched)
    serie = dict(serie)
    serie["points"] = points

    ranking = build_ranking(
        _params_with(params, workforce_only="1", by="agente")
    )
    causas = _causas_pareto(fal_qs)
    concentration = _concentration_map(fal_qs, aud_qs)
    protocols = _investigation_protocols(fal_qs)
    insights = _build_insights_cliente(kpis, contestacao, causas, ranking)
    workflows_bd = _build_breakdown(
        _params_with(params, dim="id_workflow", metric="quantidade"),
        aud_qs=aud_qs,
        fal_qs=fal_qs,
    )
    workflow_rows = []
    for row in workflows_bd.get("rows") or []:
        drill = dict(row.get("drill") or {})
        if not drill and str(row.get("key") or "").isdigit():
            drill = {"id_workflow": str(row["key"]), "lista": "falhas"}
        workflow_rows.append(
            {
                "key": row.get("key"),
                "label": row.get("label"),
                "auditados": int(row.get("auditados") or 0),
                "falhas": int(row.get("falhas") or 0),
                "eo_pct": row.get("eo_pct"),
                "falha_share_pct": row.get("falha_share_pct"),
                "impacto_ponderado": row.get("impacto_ponderado"),
                "drill": drill,
            }
        )

    eo = kpis.get("eo_ponderado_pct")
    status_eo = None
    if eo is None:
        status_eo = "sem_dados"
    elif eo < EO_META_PCT:
        status_eo = "abaixo_meta"
    elif eo < EO_META_PCT + 0.3:
        status_eo = "proximo_limite"
    else:
        status_eo = "acima_meta"

    return {
        "ok": True,
        "module": "cliente",
        "requires_cliente": False,
        "identity": {
            "id_cliente": cliente_id,
            "nome": nome,
            "iniciais": _initials(nome),
            "segmento": None,
            "abrangencia": None,
            "carteira_desde": None,
            "status_carteira": None,
            "risco": "Risco operacional" if (eo is not None and eo < EO_META_PCT) else "Monitoramento",
            "responsavel_interno": None,
            "ultima_revisao": None,
            "proxima_acao": None,
            "prazo_proxima_acao": None,
        },
        "kpis": kpis,
        "kpi_cards": {
            "eo": {
                "value": eo,
                "meta_pct": EO_META_PCT,
                "delta_pp": round(eo - EO_META_PCT, 1) if eo is not None else None,
                "status": status_eo,
            },
            "impacto": {
                "value": cliente_impacto,
                "share_carteira_pct": _pct(cliente_impacto, carteira_impacto, 1),
            },
            "protocolos_auditados": {
                "value": protocolos_auditados,
                "delta_pct": None,
            },
            "protocolos_com_falha": {
                "value": protocolos_com_falha,
                "taxa_pct": kpis.get("taxa_falha_protocolo_pct"),
            },
            "protocolos_contestados": {
                "value": contestacao["protocolos_contestados"],
                "taxa_sobre_falha_pct": kpis.get("taxa_contestacao_sobre_falha_pct"),
            },
            "taxa_procedencia": {
                "value": procedencia.get("taxa_procedencia_pct"),
                "procedentes": procedencia.get("procedentes"),
                "contestados": contestacao["auditados_contestacao"],
                "finalizadas": procedencia.get("finalizadas"),
                "available": procedencia.get("available"),
                "message": procedencia.get("message"),
            },
        },
        "health": {
            "status": "em_definicao",
            "score": None,
            "label": "Em definição",
            "message": (
                "Índice de saúde ainda sem regra oficial publicada. "
                "O componente está pronto para receber a fórmula centralizada."
            ),
            "factors": [],
            "principal_pressao": None,
        },
        "benchmark": {
            "ok": True,
            "title": "Cliente × demais no filtro",
            "description": (
                "Mesmo período e filtros, sem restringir a este cliente "
                "(universo de comparação do recorte)."
            ),
            "reference_label": "Demais no filtro",
            "cliente_label": "Este cliente",
            "metrics": [
                {
                    "key": "eo",
                    "label": "EO",
                    "cliente": eo,
                    "carteira": carteira_kpis.get("eo_ponderado_pct"),
                    "segmento": None,
                    "unit": "%",
                },
                {
                    "key": "taxa_falha",
                    "label": "Taxa de falha",
                    "cliente": kpis.get("taxa_falha_protocolo_pct"),
                    "carteira": _pct(carteira_prot_fal, carteira_prot_aud, 2),
                    "segmento": None,
                    "unit": "%",
                },
                {
                    "key": "contestacao",
                    "label": "Taxa de contestação",
                    "cliente": kpis.get("taxa_contestacao_sobre_falha_pct"),
                    "carteira": _pct(
                        carteira_contest.get("protocolos_contestados"),
                        carteira_prot_fal,
                        1,
                    ),
                    "segmento": None,
                    "unit": "%",
                },
                {
                    "key": "procedencia",
                    "label": "Taxa de procedência",
                    "cliente": procedencia.get("taxa_procedencia_pct"),
                    "carteira": None,
                    "segmento": None,
                    "unit": "%",
                    "unavailable": not procedencia.get("available"),
                },
            ],
            "populations": {
                "cliente_auditados": protocolos_auditados,
                "carteira_auditados": carteira_aud_n,
            },
        },
        "workflows": {
            "ok": True,
            "title": "Workflows do cliente",
            "description": "EO, volume e falhas por workflow neste cliente",
            "rows": workflow_rows,
            "total": len(workflow_rows),
        },
        "insights": insights,
        "serie": serie,
        "journey": _journey(kpis, contestacao),
        "causas": {"ok": True, "rows": causas, "metric": "impacto"},
        "concentration": concentration,
        "responsaveis": {
            "ok": True,
            "by": "agente",
            "results": (ranking.get("results") or [])[:40],
        },
        "protocols": {"ok": True, "results": protocols, "count": len(protocols)},
        "contestacao": contestacao,
        "meta_pct": EO_META_PCT,
    }
