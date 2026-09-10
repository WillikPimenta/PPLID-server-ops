# -*- coding: utf-8 -*-
"""Insights executivos para Qualidade EO — diagnóstico e priorização estrutural."""
from __future__ import annotations

from typing import Any

from django.db.models import Count

from apps.qualidade_operacional.services.analytics import (
    GRAIN_PROTOCOLO,
    _eo_pct,
    build_kpis,
    filtered_auditados,
    filtered_falhas,
    resolve_grain,
)
from apps.qualidade_operacional.services.enrichment import build_dim_lookups

# Meta operacional de referência (configurável no futuro via params).
EO_META_PCT = 99.7
_MIN_AUDITADOS_CLIENTE = 5
_MIN_AUDITADOS_RISCO = 30  # abaixo disso: alerta de base pequena
_MIN_FALHAS_SHARE = 1


def _impact_label(share: float | None) -> str:
    if share is None:
        return "Indefinido"
    if share >= 30:
        return "Muito alto"
    if share >= 20:
        return "Alto"
    if share >= 10:
        return "Médio"
    return "Baixo"


def _semaforo(*, eo: float | None, delta: float | None, meta: float) -> dict[str, Any]:
    if eo is None:
        return {
            "status": "indefinido",
            "label": "Sem EO",
            "message": "Sem volume suficiente de auditados para calcular EO%.",
            "meta_pct": meta,
            "eo_pct": None,
            "delta_pp": delta,
        }
    gap = eo - meta
    if eo < meta:
        status, label = "fora_meta", "Fora da meta"
        message = (
            f"EO de {eo}%, {abs(gap):.1f} p.p. abaixo da meta de {meta}%."
        )
    elif gap <= 0.3:
        status, label = "proximo_limite", "Próximo do limite"
        message = f"EO de {eo}%, próxima da meta de {meta}%."
    elif delta is not None and delta < -0.05:
        status, label = "atencao", "Atenção"
        message = (
            f"EO de {eo}% (acima da meta de {meta}%), "
            f"com queda de {abs(delta):.1f} p.p. vs período anterior."
        )
    else:
        status, label = "ok", "Dentro da meta"
        message = (
            f"EO de {eo}%, {gap:.1f} p.p. acima da meta de {meta}%."
            + (
                f" Variação vs anterior: {delta:+.1f} p.p."
                if delta is not None
                else ""
            )
        )
    return {
        "status": status,
        "label": label,
        "message": message,
        "meta_pct": meta,
        "eo_pct": eo,
        "delta_pp": delta,
    }


def _grouped_counts(qs, grain: str, field: str) -> dict[Any, int]:
    if grain == GRAIN_PROTOCOLO:
        grouped = (
            qs.exclude(protocolo="")
            .values(field)
            .annotate(c=Count("protocolo", distinct=True))
        )
    else:
        grouped = qs.values(field).annotate(c=Count("id"))
    out: dict[Any, int] = {}
    for row in grouped:
        key = row[field]
        if key is None or key == "":
            continue
        out[key] = int(row["c"])
    return out


def _merge_dim_rows(
    aud_map: dict[Any, int],
    fal_map: dict[Any, int],
    *,
    total_fal: int,
) -> list[dict[str, Any]]:
    keys = set(aud_map) | set(fal_map)
    rows: list[dict[str, Any]] = []
    for key in keys:
        a = aud_map.get(key, 0)
        f = fal_map.get(key, 0)
        if a <= 0 and f <= 0:
            continue
        share = round(100.0 * f / total_fal, 1) if total_fal else None
        rows.append(
            {
                "key": key,
                "label": str(key),
                "auditados": a,
                "falhas": f,
                "eo_pct": _eo_pct(a, f),
                "falha_share_pct": share,
            }
        )
    return rows


def _cliente_label(cid: int, nomes: dict[int, str]) -> str:
    nome = (nomes.get(cid) or "").strip()
    return nome if nome else f"Cliente {cid}"


def _workflow_label(wid: int, nomes: dict[int, str]) -> str:
    nome = (nomes.get(wid) or "").strip()
    return nome if nome else f"Workflow {wid}"


def _oportunidade(
    *,
    dimensao: str,
    item: str,
    falhas: int | None,
    share: float | None,
    eo_pct: float | None,
    auditados: int | None,
    action: str,
    drill: dict[str, str],
    metric: str = "falhas",
    small_base: bool = False,
) -> dict[str, Any]:
    return {
        "dimensao": dimensao,
        "tipo": dimensao,
        "item": item,
        "label": item,
        "metric": metric,
        "falhas": falhas,
        "auditados": auditados,
        "eo_pct": eo_pct,
        "falha_share_pct": share,
        "impacto": _impact_label(share),
        "action": action,
        "drill": drill,
        "small_base": small_base,
        "alerta_base": (
            f"Resultado crítico, porém baseado em apenas {auditados} auditorias. "
            "Interpretar com cautela."
            if small_base and auditados is not None
            else None
        ),
    }


def build_insights(
    params,
    *,
    kpis: dict[str, Any] | None = None,
    aud_qs=None,
    fal_qs=None,
) -> dict[str, Any]:
    """Diagnóstico executivo: resumo, semáforo, top oportunidades e ações."""
    grain = resolve_grain(params)
    try:
        meta = float(params.get("meta_eo") or EO_META_PCT)
    except (TypeError, ValueError):
        meta = EO_META_PCT

    kpis = kpis if kpis is not None else build_kpis(params)
    tip_rows = kpis.get("by_tipo_conclusao") or []
    tip_by = {r["label"]: r for r in tip_rows}

    aud_qs = aud_qs if aud_qs is not None else filtered_auditados(params)
    fal_qs = fal_qs if fal_qs is not None else filtered_falhas(params)
    total_fal = int(kpis.get("falhas") or 0)
    total_aud = int(kpis.get("auditados") or 0)

    aud_cli = _grouped_counts(aud_qs, grain, "id_cliente")
    fal_cli = _grouped_counts(fal_qs, grain, "id_cliente")
    cliente_rows = _merge_dim_rows(aud_cli, fal_cli, total_fal=total_fal)
    cli_ids = {int(r["key"]) for r in cliente_rows if r.get("key") is not None}
    nomes_cli, _, _ = build_dim_lookups(cli_ids, set(), set())
    for row in cliente_rows:
        cid = int(row["key"])
        row["key"] = str(cid)
        row["id_cliente"] = cid
        row["label"] = _cliente_label(cid, nomes_cli)

    aud_etapa = _grouped_counts(aud_qs, grain, "etapa")
    fal_etapa = _grouped_counts(fal_qs, grain, "etapa")
    etapa_rows = _merge_dim_rows(aud_etapa, fal_etapa, total_fal=total_fal)
    for row in etapa_rows:
        row["label"] = str(row["key"] or "(em branco)")

    aud_wf = _grouped_counts(aud_qs, grain, "id_workflow")
    fal_wf = _grouped_counts(fal_qs, grain, "id_workflow")
    wf_rows = _merge_dim_rows(aud_wf, fal_wf, total_fal=total_fal)
    wf_ids = {int(r["key"]) for r in wf_rows if r.get("key") is not None}
    _, nomes_wf, _ = build_dim_lookups(set(), wf_ids, set())
    for row in wf_rows:
        wid = int(row["key"])
        row["id_workflow"] = wid
        row["label"] = _workflow_label(wid, nomes_wf)
        row["key"] = str(wid)

    fal_loc = _grouped_counts(fal_qs, grain, "localidade_documento")

    manual = tip_by.get("Manual") or {}
    auto = tip_by.get("Automático") or {}
    processual = tip_by.get("Processual") or {}

    eo = kpis.get("eo_pct")
    delta = (kpis.get("previous") or {}).get("delta_pp")
    prev_eo = (kpis.get("previous") or {}).get("eo_pct")
    semaforo = _semaforo(eo=eo, delta=delta, meta=meta)

    best_etapa = max(
        (r for r in etapa_rows if (r.get("falhas") or 0) >= _MIN_FALHAS_SHARE),
        key=lambda r: int(r["falhas"]),
        default=None,
    )
    best_wf = max(
        (r for r in wf_rows if (r.get("falhas") or 0) >= _MIN_FALHAS_SHARE),
        key=lambda r: int(r["falhas"]),
        default=None,
    )
    best_cli_vol = max(
        (
            r
            for r in cliente_rows
            if (r.get("falhas") or 0) > 0
            and (r.get("auditados") or 0) >= _MIN_AUDITADOS_CLIENTE
        ),
        key=lambda r: int(r["falhas"]),
        default=None,
    )
    best_cli_risco = min(
        (
            r
            for r in cliente_rows
            if r.get("eo_pct") is not None
            and (r.get("auditados") or 0) >= _MIN_AUDITADOS_CLIENTE
        ),
        key=lambda r: (float(r["eo_pct"]), -int(r.get("falhas") or 0)),
        default=None,
    )
    best_loc = None
    if fal_loc and total_fal > 0:
        loc_key, loc_fal = max(fal_loc.items(), key=lambda kv: kv[1])
        if loc_fal >= _MIN_FALHAS_SHARE:
            best_loc = {
                "label": str(loc_key),
                "falhas": loc_fal,
                "falha_share_pct": round(100.0 * loc_fal / total_fal, 1),
                "drill": {"localidade": str(loc_key), "lista": "falhas"},
            }

    # Candidatos unificados por participação nas falhas (ranking transparente).
    candidatos: list[dict[str, Any]] = []
    if best_etapa:
        candidatos.append(
            _oportunidade(
                dimensao="Etapa",
                item=best_etapa["label"],
                falhas=best_etapa.get("falhas"),
                share=best_etapa.get("falha_share_pct"),
                eo_pct=best_etapa.get("eo_pct"),
                auditados=best_etapa.get("auditados"),
                action=(
                    f"Revisar regras, fluxo e causas recorrentes da etapa "
                    f"{best_etapa['label']}."
                ),
                drill={"etapa": str(best_etapa["key"]), "lista": "falhas"},
            )
        )
    if best_wf:
        candidatos.append(
            _oportunidade(
                dimensao="Workflow",
                item=best_wf["label"],
                falhas=best_wf.get("falhas"),
                share=best_wf.get("falha_share_pct"),
                eo_pct=best_wf.get("eo_pct"),
                auditados=best_wf.get("auditados"),
                action=f"Revisar o workflow {best_wf['label']} e etapas associadas.",
                drill={
                    "id_workflow": str(best_wf["id_workflow"]),
                    "lista": "falhas",
                },
            )
        )
    if best_cli_vol:
        candidatos.append(
            _oportunidade(
                dimensao="Cliente",
                item=best_cli_vol["label"],
                falhas=best_cli_vol.get("falhas"),
                share=best_cli_vol.get("falha_share_pct"),
                eo_pct=best_cli_vol.get("eo_pct"),
                auditados=best_cli_vol.get("auditados"),
                action=(
                    f"Identificar motivos, recorrência e etapas de origem em "
                    f"{best_cli_vol['label']}."
                ),
                drill={
                    "id_cliente": str(best_cli_vol["id_cliente"]),
                    "lista": "falhas",
                },
            )
        )
    if best_loc:
        candidatos.append(
            _oportunidade(
                dimensao="Localidade",
                item=best_loc["label"],
                falhas=best_loc.get("falhas"),
                share=best_loc.get("falha_share_pct"),
                eo_pct=None,
                auditados=None,
                action=(
                    f"Validar causas locais em {best_loc['label']} e comparar "
                    "com a participação no volume operacional."
                ),
                drill=best_loc["drill"],
            )
        )
    if best_cli_risco and (
        not best_cli_vol
        or int(best_cli_risco["id_cliente"]) != int(best_cli_vol["id_cliente"])
    ):
        aud_n = int(best_cli_risco.get("auditados") or 0)
        small = aud_n < _MIN_AUDITADOS_RISCO
        candidatos.append(
            _oportunidade(
                dimensao="Cliente",
                item=best_cli_risco["label"],
                falhas=best_cli_risco.get("falhas"),
                share=best_cli_risco.get("falha_share_pct"),
                eo_pct=best_cli_risco.get("eo_pct"),
                auditados=aud_n,
                action=(
                    f"Validar integridade da base e critérios de auditoria em "
                    f"{best_cli_risco['label']} antes de tratar como estrutural."
                    if small
                    else (
                        f"Investigar EO baixo em {best_cli_risco['label']} "
                        f"({best_cli_risco.get('eo_pct')}%)."
                    )
                ),
                drill={
                    "id_cliente": str(best_cli_risco["id_cliente"]),
                    "lista": "falhas",
                },
                metric="eo_pct",
                small_base=small,
            )
        )

    candidatos.sort(
        key=lambda c: (
            -float(c.get("falha_share_pct") or 0),
            -int(c.get("falhas") or 0),
        )
    )
    # Dedup por (dimensao, item)
    seen: set[tuple[str, str]] = set()
    top_oportunidades: list[dict[str, Any]] = []
    for c in candidatos:
        key = (c["dimensao"], c["item"])
        if key in seen:
            continue
        seen.add(key)
        top_oportunidades.append(c)
        if len(top_oportunidades) >= 3:
            break

    # Diagnóstico nas 4 categorias da proposta
    atencao = top_oportunidades[0] if top_oportunidades else None
    maior_risco = None
    if best_cli_risco:
        aud_n = int(best_cli_risco.get("auditados") or 0)
        maior_risco = _oportunidade(
            dimensao="Cliente",
            item=best_cli_risco["label"],
            falhas=best_cli_risco.get("falhas"),
            share=best_cli_risco.get("falha_share_pct"),
            eo_pct=best_cli_risco.get("eo_pct"),
            auditados=aud_n,
            action=(
                f"Validar base e processo — EO {best_cli_risco.get('eo_pct')}% "
                f"em {aud_n} auditados."
            ),
            drill={
                "id_cliente": str(best_cli_risco["id_cliente"]),
                "lista": "falhas",
            },
            metric="eo_pct",
            small_base=aud_n < _MIN_AUDITADOS_RISCO,
        )
    maior_oportunidade = None
    if best_cli_vol or best_etapa:
        src = best_cli_vol if (best_cli_vol and (
            not best_etapa
            or int(best_cli_vol.get("falhas") or 0) >= int(best_etapa.get("falhas") or 0)
        )) else best_etapa
        dim = "Cliente" if src is best_cli_vol else "Etapa"
        drill = (
            {"id_cliente": str(src["id_cliente"]), "lista": "falhas"}
            if dim == "Cliente"
            else {"etapa": str(src["key"]), "lista": "falhas"}
        )
        maior_oportunidade = _oportunidade(
            dimensao=dim,
            item=src["label"],
            falhas=src.get("falhas"),
            share=src.get("falha_share_pct"),
            eo_pct=src.get("eo_pct"),
            auditados=src.get("auditados"),
            action=(
                f"Atuar em {src['label']} pode impactar aproximadamente "
                f"{src.get('falha_share_pct')}% das falhas do período."
                if src.get("falha_share_pct") is not None
                else f"Atuar em {src['label']} — maior volume de falhas."
            ),
            drill=drill,
        )

    ponto_positivo = None
    if processual and processual.get("eo_pct") is not None and processual["eo_pct"] >= meta:
        ponto_positivo = _oportunidade(
            dimensao="Tipificação",
            item="Processual",
            falhas=processual.get("falhas"),
            share=None,
            eo_pct=processual.get("eo_pct"),
            auditados=processual.get("auditados"),
            action=(
                f"Processual com EO {processual.get('eo_pct')}% — "
                "manter monitoramento e replicar boas práticas."
            ),
            drill={
                "tipo_falha": "Processual",
                "lista": "falhas",
            },
            metric="eo_pct",
        )
    elif auto and auto.get("eo_pct") is not None and auto["eo_pct"] >= meta:
        ponto_positivo = _oportunidade(
            dimensao="Tipificação",
            item="Automático",
            falhas=auto.get("falhas"),
            share=None,
            eo_pct=auto.get("eo_pct"),
            auditados=auto.get("auditados"),
            action=(
                f"Automático com EO {auto.get('eo_pct')}% — "
                "resultado estável acima da meta."
            ),
            drill={"tipo_conclusao": "Automático", "lista": "falhas"},
            metric="eo_pct",
        )

    # Resumo executivo — tópicos curtos (evita parágrafo longo e repetição de KPIs)
    if eo is not None:
        situacao = (
            f"EO {eo}% · meta {meta}%"
            + (f" · Δ {delta:+.1f} p.p. vs anterior" if delta is not None else "")
        )
    else:
        situacao = "Sem volume suficiente para calcular EO%."

    if atencao:
        concentracao_txt = (
            f"{atencao['dimensao']} {atencao['item']}"
            + (
                f" ({atencao.get('falhas')} falhas, {atencao.get('falha_share_pct')}%)"
                if atencao.get("falha_share_pct") is not None
                else ""
            )
        )
    elif conc_parts := [
        x
        for x in [
            f"etapa {best_etapa['label']}" if best_etapa else None,
            f"cliente {best_cli_vol['label']}" if best_cli_vol else None,
        ]
        if x
    ]:
        concentracao_txt = "; ".join(conc_parts)
    else:
        concentracao_txt = "Sem concentração dominante no filtro."

    if maior_risco:
        risco_txt = (
            f"{maior_risco['item']} · EO {maior_risco.get('eo_pct')}%"
            + (
                " (base pequena)"
                if maior_risco.get("small_base")
                else f" · {maior_risco.get('falhas')} falhas"
            )
        )
    else:
        risco_txt = "Nenhum risco crítico com volume mínimo."

    if top_oportunidades:
        recomendacao_txt = (
            f"Priorizar {top_oportunidades[0]['dimensao'].lower()} "
            f"{top_oportunidades[0]['item']}."
        )
    else:
        recomendacao_txt = "Manter monitoramento no filtro atual."

    resumo_topicos = {
        "situacao": situacao,
        "concentracao": concentracao_txt,
        "risco": risco_txt,
        "recomendacao": recomendacao_txt,
    }
    resumo_executivo = (
        f"Situação: {situacao}. Concentração: {concentracao_txt}. "
        f"Risco: {risco_txt}. Recomendação: {recomendacao_txt}"
    )

    # Concentração (clientes)
    cli_sorted = sorted(
        (r for r in cliente_rows if (r.get("falhas") or 0) > 0),
        key=lambda r: -int(r["falhas"]),
    )
    def _acc(n: int) -> float | None:
        if not total_fal or not cli_sorted:
            return None
        return round(100.0 * sum(int(r["falhas"]) for r in cli_sorted[:n]) / total_fal, 1)

    concentracao = {
        "top1_pct": _acc(1),
        "top3_pct": _acc(3),
        "top5_pct": _acc(5),
        "dimensao": "cliente",
    }

    # prioridades legado = top oportunidades (compat FE)
    prioridades = [
        {
            "tipo": o["dimensao"],
            "metric": o["metric"],
            "eo_pct": o.get("eo_pct"),
            "falhas": o.get("falhas"),
            "auditados": o.get("auditados"),
            "action": o["action"],
            "drill": o.get("drill"),
            "falha_share_pct": o.get("falha_share_pct"),
            "impacto": o.get("impacto"),
            "item": o.get("item"),
            "small_base": o.get("small_base"),
            "alerta_base": o.get("alerta_base"),
        }
        for o in top_oportunidades
    ]

    bullets: list[str] = []
    if delta is not None:
        if delta < -0.05:
            bullets.append(f"EO caiu {abs(delta):.1f} p.p. vs período anterior.")
        elif delta > 0.05:
            bullets.append(f"EO subiu {delta:.1f} p.p. vs período anterior.")
    if concentracao["top3_pct"] is not None:
        bullets.append(
            f"Três principais clientes concentram {concentracao['top3_pct']}% das falhas."
        )
    if manual and auto and manual.get("eo_pct") is not None and auto.get("eo_pct") is not None:
        if manual["eo_pct"] + 0.5 < auto["eo_pct"]:
            bullets.append(
                f"Manual ({manual['eo_pct']}%) abaixo de Automático ({auto['eo_pct']}%)."
            )

    # Todos os clientes do filtro (não só top 5) — avaliação operacional completa.
    cli_all = sorted(
        cliente_rows,
        key=lambda r: (-int(r.get("falhas") or 0), -int(r.get("auditados") or 0), str(r.get("label") or "")),
    )
    top_clientes = [
        {
            "key": r["key"],
            "id_cliente": r["id_cliente"],
            "label": r["label"],
            "auditados": r["auditados"],
            "falhas": r["falhas"],
            "conformes": max(0, int(r.get("auditados") or 0) - int(r.get("falhas") or 0)),
            "eo_pct": r.get("eo_pct"),
            "falha_share_pct": r.get("falha_share_pct"),
            "impacto": _impact_label(r.get("falha_share_pct")),
            "drill": {
                "id_cliente": str(r["id_cliente"]),
                "lista": "falhas" if int(r.get("falhas") or 0) > 0 else "auditados",
            },
        }
        for r in cli_all
    ]

    return {
        "ok": True,
        "grain": grain,
        "titulo": "Diagnóstico executivo",
        "veredito": semaforo["message"],
        "resumo_executivo": resumo_executivo,
        "resumo_topicos": resumo_topicos,
        "semaforo": semaforo,
        "diagnostico": {
            "atencao_principal": atencao,
            "maior_risco": maior_risco,
            "maior_oportunidade": maior_oportunidade,
            "ponto_positivo": ponto_positivo,
        },
        "top_oportunidades": top_oportunidades,
        "concentracao": concentracao,
        "bullets": bullets[:8],
        "prioridades": prioridades,
        "piores_agentes": [],
        "top_clientes": top_clientes,
        "foco_tipificacao": tip_rows,
        "kpis": {
            "auditados": kpis.get("auditados"),
            "falhas": kpis.get("falhas"),
            "eo_pct": kpis.get("eo_pct"),
            "delta_pp": delta,
            "meta_pct": meta,
            "taxa_falha_pct": (
                round(100.0 * total_fal / total_aud, 2) if total_aud > 0 else None
            ),
        },
    }
