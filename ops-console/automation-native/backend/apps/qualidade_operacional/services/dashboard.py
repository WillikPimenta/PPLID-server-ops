"""Payloads consolidados das visoes Resumo e Agentes."""
from __future__ import annotations

from apps.qualidade_operacional.services.analytics import (
    LEADER_TEMPORAL_UNATTRIBUTED_KEY,
    _build_breakdown,
    _build_kpis,
    build_month_over_month_kpis,
    _eo_pct,
    build_facilitator_hierarchy,
    build_leader_hierarchy,
    build_ranking,
    build_serie,
    build_workforce_dimension_breakdown,
    compute_operational_detail_scope,
    compute_operational_facilitator_detail_scope,
    filtered_auditados,
    filtered_falhas,
    operational_agentes_scope_params,
    resolve_date_range,
)
from apps.qualidade_operacional.services.auditor_dashboard import build_auditor_dashboard
from apps.qualidade_operacional.services.client_dashboard import build_client_dashboard
from apps.qualidade_operacional.services.contestacao_metrics import (
    _contestacao_auditados_qs,
    _contestacao_falhas_qs,
    build_contestacao_metrics,
    contestacao_by_matricula,
)
from apps.qualidade_operacional.services.insights import build_insights
from apps.qualidade_operacional.services.performance_cache import get_or_build
from apps.qualidade_operacional.services.queries import param_list
from apps.qualidade_operacional.services.resumo_extensions import (
    build_clientes_melhorias_all,
    build_contestacao_leitura,
    build_falhas_por_etapa,
    build_prioridades_etapa,
    build_resumo_extensions,
    finalize_resumo_extensions,
    paginate_clientes_melhorias,
    strip_melhorias_view_params,
)


def _params_with(params, **updates):
    copied = params.copy() if hasattr(params, "copy") else dict(params)
    for key, value in updates.items():
        copied[key] = value
    return copied


def _enrich_kpis_with_protocols_and_contestacao(
    params,
    kpis: dict,
    aud_qs,
    fal_qs,
    *,
    include_protocol_distinct: bool = True,
) -> dict:
    """Enriquece KPIs com protocolos distintos e contestação.

    ``include_protocol_distinct=False`` (Resumo): omite COUNT DISTINCT global
    de protocolos (~1s em bases grandes). O Resumo exibe auditados/falhas por
    grain e contestação; Cliente/Agentes mantêm o distinct.
    """
    if include_protocol_distinct:
        protocolos_auditados = (
            aud_qs.exclude(protocolo="").values("protocolo").distinct().count()
        )
        protocolos_com_falha = (
            fal_qs.exclude(protocolo="").values("protocolo").distinct().count()
        )
    else:
        protocolos_auditados = None
        protocolos_com_falha = None
    contestacao = build_contestacao_metrics(params, aud_qs=aud_qs, fal_qs=fal_qs)
    kpis = dict(kpis)
    kpis["protocolos_auditados"] = protocolos_auditados
    kpis["protocolos_com_falha"] = protocolos_com_falha
    kpis["protocolos_contestados"] = contestacao["protocolos_contestados"]
    kpis["eventos_contestacao"] = contestacao["eventos_contestacao"]
    kpis["contestacao"] = contestacao
    kpis["taxa_procedencia_pct"] = contestacao.get("taxa_procedencia_pct")
    kpis["procedentes"] = contestacao.get("procedentes")
    # Taxas auxiliares
    kpis["taxa_falha_protocolo_pct"] = (
        round(100.0 * protocolos_com_falha / protocolos_auditados, 2)
        if protocolos_auditados and protocolos_com_falha is not None
        else None
    )
    kpis["taxa_contestacao_sobre_falha_pct"] = (
        round(100.0 * contestacao["protocolos_contestados"] / protocolos_com_falha, 1)
        if protocolos_com_falha
        else None
    )
    return kpis


def _build_workforce_coverage(
    params,
    scoped_aud_qs,
    *,
    scoped_rows: int | None = None,
    scoped_matriculas: set[str] | None = None,
) -> dict:
    """Explicita exclusões da aba Agentes com uma única varredura da base."""
    from django.db.models import Count, Q

    unscoped = params.copy() if hasattr(params, "copy") else dict(params)
    unscoped.pop("workforce_only", None)
    base_qs = filtered_auditados(unscoped)
    stats = base_qs.aggregate(
        total_rows=Count("id"),
        blank_rows=Count("id", filter=Q(matricula="")),
        system_rows=Count("id", filter=Q(matricula__iexact="sistema")),
        eligible_rows=Count(
            "id",
            filter=~Q(matricula="") & ~Q(matricula__iexact="sistema"),
        ),
    )
    eligible_qs = base_qs.exclude(matricula="").exclude(matricula__iexact="sistema")
    eligible_matriculas = set(eligible_qs.values_list("matricula", flat=True).distinct())
    if scoped_rows is None:
        scoped_rows = scoped_aud_qs.count()
    if scoped_matriculas is None:
        scoped_matriculas = set(
            scoped_aud_qs.exclude(matricula="")
            .values_list("matricula", flat=True)
            .distinct()
        )
    eligible_rows = int(stats["eligible_rows"] or 0)
    scoped_rows = int(scoped_rows or 0)
    unmatched_matriculas = eligible_matriculas - scoped_matriculas
    # scoped_aud_qs é subconjunto de eligible_qs; a diferença de contagens é
    # exatamente o número de linhas sem AgentHistory, sem um novo IN gigante.
    unmatched_rows = max(0, eligible_rows - scoped_rows)
    coverage_pct = round(100.0 * scoped_rows / eligible_rows, 2) if eligible_rows else None
    return {
        "total_rows": int(stats["total_rows"] or 0),
        "blank_matricula_rows": int(stats["blank_rows"] or 0),
        "system_rows": int(stats["system_rows"] or 0),
        "eligible_rows": eligible_rows,
        "scoped_rows": scoped_rows,
        "unmatched_rows": unmatched_rows,
        "eligible_matriculas": len(eligible_matriculas),
        "scoped_matriculas": len(scoped_matriculas),
        "unmatched_matriculas": len(unmatched_matriculas),
        "coverage_pct": coverage_pct,
    }

def _merge_serie_contestacao(serie: dict, contestacao: dict) -> dict:
    by_day = contestacao.get("by_day") or {}
    by_day_proc = contestacao.get("by_day_procedentes") or {}
    points = []
    for point in serie.get("points") or []:
        enriched = dict(point)
        day_key = point.get("date")
        contestados = int(by_day.get(day_key, 0) or 0)
        procedentes = int(by_day_proc.get(day_key, 0) or 0)
        enriched["contestacoes"] = contestados
        # Improcedência = contestados sem falha ÷ contestados (qualidade da auditoria contestada).
        if contestados > 0:
            enriched["improcedencia_pct"] = round(
                100.0 * max(0, contestados - procedentes) / contestados, 1
            )
            enriched["procedentes_contestacao"] = procedentes
        else:
            enriched["improcedencia_pct"] = None
            enriched["procedentes_contestacao"] = 0
        points.append(enriched)
    out = dict(serie)
    out["points"] = points
    return out


def _patch_insights_contestacao(insights: dict, contestacao: dict, kpis: dict) -> dict:
    insights = dict(insights or {})
    topicos = dict(insights.get("resumo_topicos") or {})
    prev = contestacao.get("previous") or {}
    delta_pct = prev.get("delta_pct")
    label = prev.get("label") or "período anterior"
    n = contestacao.get("protocolos_contestados") or 0
    if delta_pct is not None and delta_pct > 0:
        topicos["risco"] = (
            f"Contestações cresceram {delta_pct}% vs. {label} "
            f"({n} protocolos contestados)."
        )
    elif n:
        topicos["risco"] = topicos.get("risco") or (
            f"{n} protocolos contestados no período."
        )
    if contestacao.get("procedentes"):
        topicos["recomendacao"] = (
            "Revisar contestações procedentes e comparar critérios "
            "das auditorias manuais e automáticas."
        )
    elif n:
        topicos["recomendacao"] = (
            "Revisar contestações do período e comparar critérios "
            "das auditorias manuais e automáticas."
        )
    elif not topicos.get("recomendacao"):
        topicos["recomendacao"] = insights.get("veredito") or "—"
    eo = kpis.get("eo_pct")
    meta = (insights.get("semaforo") or {}).get("meta_pct", 99.7)
    delta = (kpis.get("previous") or {}).get("delta_pp")
    situacao_parts = []
    if eo is not None:
        situacao_parts.append(f"EO {eo}%")
    situacao_parts.append(f"meta {meta}%")
    if delta is not None:
        situacao_parts.append(f"Δ {delta:+.1f} p.p.")
    topicos["situacao"] = " • ".join(situacao_parts) if situacao_parts else topicos.get("situacao", "—")
    insights["resumo_topicos"] = topicos
    return insights


def _summary_dashboard(params) -> dict:
    aud_qs = filtered_auditados(params)
    fal_qs = filtered_falhas(params)
    kpis = _build_kpis(params, aud_qs=aud_qs, fal_qs=fal_qs)
    kpis = _enrich_kpis_with_protocols_and_contestacao(
        params,
        kpis,
        aud_qs,
        fal_qs,
        include_protocol_distinct=False,
    )
    total_aud = int(kpis["auditados"])
    total_fal = int(kpis["falhas"])
    insights = build_insights(
        params,
        kpis=kpis,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
    )
    insights = _patch_insights_contestacao(insights, kpis["contestacao"], kpis)
    serie = build_serie(_params_with(params, mode="eo"), aud_qs=aud_qs, fal_qs=fal_qs)
    serie = _merge_serie_contestacao(serie, kpis["contestacao"])
    dim = str(params.get("dim") or "tipo_analise").strip().lower()
    # Reusa agregação de clientes já materializada em insights.top_clientes.
    precomputed_dim_rows = None
    if dim == "id_cliente" and insights.get("top_clientes"):
        precomputed_dim_rows = [
            {
                "key": str(row["id_cliente"]),
                "label": row["label"],
                "auditados": row["auditados"],
                "falhas": row["falhas"],
                "eo_pct": row.get("eo_pct"),
                "drill": row.get("drill")
                or {"id_cliente": str(row["id_cliente"]), "lista": "falhas"},
            }
            for row in insights["top_clientes"][:50]
        ]
    breakdown = _build_breakdown(
        params,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        total_aud=total_aud,
        total_fal=total_fal,
        precomputed_tip_rows=kpis.get("by_tipo_conclusao"),
        precomputed_dim_rows=precomputed_dim_rows,
    )
    cards = _build_breakdown(
        _params_with(params, dim="tipo_falha", metric="quantidade"),
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        total_aud=total_aud,
        total_fal=total_fal,
        precomputed_tip_rows=kpis.get("by_tipo_conclusao"),
    )
    # Origem: Manual, Automático e Processual (Processual usa total auditados)
    origem_rows = list(cards.get("rows") or [])
    extensions = build_resumo_extensions(
        params,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        kpis=kpis,
        insights=insights,
    )
    return {
        "ok": True,
        "module": "resumo",
        "kpis": kpis,
        "insights": insights,
        "serie": serie,
        "breakdown": breakdown,
        "tipo_falha_cards": cards,
        "origem_auditorias": {
            "ok": True,
            "rows": origem_rows,
            "localidade_filtrada": bool(param_list(params, "localidade")),
            "localidades": param_list(params, "localidade"),
        },
        **extensions,
    }


def _empty_operational_panel() -> dict:
    return {
        "ok": True,
        "grain": "etapa",
        "dim": "",
        "rows": [],
        "total_auditados": 0,
        "total_falhas": 0,
        "eo_pct": None,
    }


def _sum_row_auditados(rows) -> int:
    return sum(int(row.get("auditados") or 0) for row in rows or [])


def _agents_dashboard(params) -> dict:
    """Dashboard Agentes: ranking por agente (hierarquia no FE) + série ponderada."""
    scoped = _params_with(params, workforce_only="1", by="agente")
    operational = operational_agentes_scope_params(scoped)
    op_aud_qs = filtered_auditados(operational)
    op_fal_qs = filtered_falhas(operational)

    ranking = build_ranking(_params_with(scoped, responsibility_scope="lider"))
    facilitator_ranking = build_ranking(
        _params_with(scoped, responsibility_scope="facilitador")
    )
    leader_hierarchy = build_leader_hierarchy(
        operational, aud_qs=op_aud_qs, fal_qs=op_fal_qs
    )
    facilitator_scoped = _params_with(scoped, responsibility_scope="facilitador")
    op_fac_aud_qs = filtered_auditados(facilitator_scoped)
    op_fac_fal_qs = filtered_falhas(facilitator_scoped)
    facilitator_hierarchy = build_facilitator_hierarchy(
        facilitator_scoped, aud_qs=op_fac_aud_qs, fal_qs=op_fac_fal_qs
    )
    leader_attributed_auditados = _sum_row_auditados(
        row
        for row in leader_hierarchy.get("results") or []
        if row.get("key") != LEADER_TEMPORAL_UNATTRIBUTED_KEY
    )
    leader_attributed_falhas = sum(
        int(row.get("falhas") or 0)
        for row in leader_hierarchy.get("results") or []
        if row.get("key") != LEADER_TEMPORAL_UNATTRIBUTED_KEY
    )
    leader_attributed_impact = round(
        sum(
            float(row.get("impacto_ponderado") or 0)
            for row in leader_hierarchy.get("results") or []
            if row.get("key") != LEADER_TEMPORAL_UNATTRIBUTED_KEY
        ),
        1,
    )
    attributed_auditados = leader_attributed_auditados + _sum_row_auditados(
        facilitator_hierarchy.get("results")
    )
    coverage_matriculas = {
        str(row.get("matricula") or row.get("key") or "").strip().lower()
        for source in (ranking, facilitator_ranking)
        for row in source.get("results") or []
        if (row.get("matricula") or row.get("key"))
        and int(row.get("auditados") or 0) > 0
    }
    coverage_rows = None
    if str(scoped.get("grain") or "etapa").strip().lower() != "protocolo":
        coverage_rows = attributed_auditados
    workforce_coverage = _build_workforce_coverage(
        scoped,
        op_aud_qs,
        scoped_rows=coverage_rows,
        scoped_matriculas=coverage_matriculas,
    )
    workforce_coverage["temporal_unattributed_rows"] = _sum_row_auditados(
        row
        for row in leader_hierarchy.get("results") or []
        if row.get("key") == LEADER_TEMPORAL_UNATTRIBUTED_KEY
    )
    kpis = _enrich_kpis_with_protocols_and_contestacao(
        operational, ranking["kpis"], op_aud_qs, op_fal_qs
    )
    kpis["auditados"] = leader_attributed_auditados
    kpis["falhas"] = leader_attributed_falhas
    kpis["impacto_ponderado"] = leader_attributed_impact
    kpis["eo_pct"] = _eo_pct(leader_attributed_auditados, leader_attributed_falhas)
    kpis["eo_ponderado_pct"] = _eo_pct(
        leader_attributed_auditados, leader_attributed_impact
    )
    ranking = dict(ranking)
    ranking["kpis"] = kpis

    # Contestações por agente (via protocolos com falha do agente)
    mats = [row.get("matricula") or row.get("key") for row in ranking.get("results") or []]
    mats = [m for m in mats if m]
    by_mat = contestacao_by_matricula(scoped, mats) if mats else {}
    results = []
    for row in ranking.get("results") or []:
        enriched = dict(row)
        key = (row.get("matricula") or row.get("key") or "").strip().lower()
        enriched["protocolos_contestados"] = by_mat.get(key, 0)
        # Protocolos com falha ≈ falhas quando grain=protocolo; senão distinct aproximado pelo count
        enriched["protocolos_com_falha"] = row.get("falhas")
        results.append(enriched)
    ranking["results"] = results

    quartil_by_mat = {
        (row.get("matricula") or row.get("key") or "").strip().lower(): row.get("quartil")
        for row in ranking.get("results") or []
        if (row.get("matricula") or row.get("key"))
    }
    rank_by_mat = {
        (row.get("matricula") or row.get("key") or "").strip().lower(): row.get("rank")
        for row in ranking.get("results") or []
        if (row.get("matricula") or row.get("key"))
    }
    lh_results = []
    for row in leader_hierarchy.get("results") or []:
        enriched = dict(row)
        key = (row.get("matricula") or row.get("key") or "").strip().lower()
        enriched["protocolos_contestados"] = by_mat.get(key, 0)
        enriched["protocolos_com_falha"] = row.get("falhas")
        enriched["quartil"] = quartil_by_mat.get(key)
        enriched["rank"] = rank_by_mat.get(key)
        lh_results.append(enriched)
    leader_hierarchy = dict(leader_hierarchy)
    leader_hierarchy["results"] = lh_results

    fh_results = []
    for row in facilitator_hierarchy.get("results") or []:
        enriched = dict(row)
        key = (row.get("matricula") or row.get("key") or "").strip().lower()
        enriched["protocolos_contestados"] = by_mat.get(key, 0)
        enriched["protocolos_com_falha"] = row.get("falhas")
        enriched["quartil"] = quartil_by_mat.get(key)
        enriched["rank"] = rank_by_mat.get(key)
        fh_results.append(enriched)
    facilitator_hierarchy = dict(facilitator_hierarchy)
    facilitator_hierarchy["results"] = fh_results

    serie = build_serie(
        _params_with(operational, mode="eo", weighted="1"),
        aud_qs=op_aud_qs,
        fal_qs=op_fal_qs,
    )
    serie = _merge_serie_contestacao(serie, kpis["contestacao"])
    workforce_dimensions = {
        "localidade_hc": build_workforce_dimension_breakdown(
            operational, aud_qs=op_aud_qs, fal_qs=op_fal_qs, dimension="localidade_hc"
        ),
        "turno": build_workforce_dimension_breakdown(
            operational, aud_qs=op_aud_qs, fal_qs=op_fal_qs, dimension="turno"
        ),
    }
    _, anchor_end = resolve_date_range(operational)
    kpis_m1_month = build_month_over_month_kpis(operational, anchor_end=anchor_end)
    operational_detail_scope = compute_operational_detail_scope(params)
    facilitator_detail_scope = compute_operational_facilitator_detail_scope(params)

    return {
        "ok": True,
        "module": "agentes",
        "kpis": kpis,
        "kpis_m1_month": kpis_m1_month,
        "ranking": ranking,
        "facilitator_ranking": facilitator_ranking,
        "leader_hierarchy": leader_hierarchy,
        "facilitator_hierarchy": facilitator_hierarchy,
        "operational_detail_scope": operational_detail_scope,
        "facilitator_detail_scope": facilitator_detail_scope,
        "workforce_coverage": workforce_coverage,
        "workforce_dimensions": workforce_dimensions,
        "serie": serie,
        "operational": {
            "etapa": _empty_operational_panel(),
            "criticidade": _empty_operational_panel(),
            "id_cliente": _empty_operational_panel(),
        },
    }


def _contestacao_operational_dashboard(params) -> dict:
    """Visão operacional Contestação: KPIs e painéis no universo Contest*.

    Sem SLA / atividades / entrega. Toda a população usa ``_contestacao_*_qs``.
    """
    scope = "contestacao"
    aud_qs = _contestacao_auditados_qs(params)
    fal_qs = _contestacao_falhas_qs(params)
    kpis = _enrich_kpis_with_protocols_and_contestacao(
        params,
        _build_kpis(params, aud_qs=aud_qs, fal_qs=fal_qs, scope=scope),
        aud_qs,
        fal_qs,
    )
    falhas_etapa = build_falhas_por_etapa(params, fal_qs=fal_qs, scope=scope)
    prioridades = build_prioridades_etapa(
        params,
        fal_qs=fal_qs,
        total_impacto=kpis.get("impacto_ponderado"),
        scope=scope,
    )
    melhorias_all = build_clientes_melhorias_all(
        params, aud_qs=aud_qs, fal_qs=fal_qs, scope=scope
    )
    ranking = build_ranking(
        _params_with(
            params, workforce_only="1", by="agente", responsibility_scope="lider"
        ),
        scope=scope,
    )
    # Top agentes para tabela de responsáveis (sem pendências/SLA).
    agent_rows = list(ranking.get("results") or [])[:50]
    return {
        "ok": True,
        "module": "contestacao",
        "scope": scope,
        "kpis": kpis,
        "falhas_por_etapa": falhas_etapa,
        "prioridades_etapa": prioridades,
        "contestacao_leitura": build_contestacao_leitura(
            params, base=kpis.get("contestacao")
        ),
        "_clientes_melhorias_all": melhorias_all,
        "responsaveis": {
            "ok": True,
            "title": "Impacto por responsável nas contestações",
            "description": (
                "Auditados, falhas e EO apenas em análises Contest* "
                "(sem pendências ou SLA)"
            ),
            "total": len(agent_rows),
            "scope": scope,
            "results": [
                {
                    "key": r.get("key") or r.get("matricula"),
                    "label": r.get("label") or r.get("matricula"),
                    "matricula": r.get("matricula"),
                    "lider": r.get("lider"),
                    "auditados": r.get("auditados"),
                    "falhas": r.get("falhas"),
                    "eo_pct": r.get("eo_pct"),
                    "eo_ponderado_pct": r.get("eo_ponderado_pct"),
                    "impacto_ponderado": r.get("impacto_ponderado"),
                    "rank": r.get("rank"),
                    "drill": {
                        "matricula": r.get("matricula") or r.get("key") or "",
                        "lista": "falhas",
                        "scope": "contestacao",
                    },
                }
                for r in agent_rows
            ],
        },
    }


def build_dashboard(params) -> dict:
    return _build_dashboard(params)


def _build_dashboard(params) -> dict:
    module = str(params.get("module") or "resumo").strip().lower()
    if module not in {"resumo", "contestacao", "agentes", "auditores", "cliente"}:
        module = "resumo"
    if module == "contestacao":
        core_params = strip_melhorias_view_params(params)
        payload = get_or_build(
            "dashboard:contestacao",
            core_params,
            lambda: _contestacao_operational_dashboard(core_params),
        )
        out = {k: v for k, v in payload.items() if k != "_clientes_melhorias_all"}
        all_rows = payload.get("_clientes_melhorias_all")
        if all_rows is None:
            out["clientes_melhorias"] = {
                "ok": True,
                "count": 0,
                "universe_count": 0,
                "page": 1,
                "page_size": 25,
                "total_pages": 1,
                "results": [],
            }
        else:
            out["clientes_melhorias"] = paginate_clientes_melhorias(all_rows, params)
        return out
    if module == "agentes":
        return get_or_build(
            "dashboard:agentes", params, lambda: _agents_dashboard(params)
        )
    if module == "auditores":
        return get_or_build(
            "dashboard:auditores", params, lambda: build_auditor_dashboard(params)
        )
    if module == "cliente":
        return get_or_build(
            "dashboard:cliente", params, lambda: build_client_dashboard(params)
        )

    # Resumo: cache ignora paginação/busca de melhorias; fatia aplicada após o hit.
    core_params = strip_melhorias_view_params(params)
    payload = get_or_build(
        "dashboard:resumo",
        core_params,
        lambda: _summary_dashboard(core_params),
    )
    return finalize_resumo_extensions(payload, params)


def build_clientes_melhorias_page(params) -> dict:
    """Pagina somente a tabela de melhorias, reutilizando o core do dashboard.

    Busca, ordenacao e pagina nao invalidam o payload analitico pesado. A rota
    dedicada tambem evita devolver KPIs, series e rankings que a tabela nao usa.
    """
    module = str(params.get("module") or "resumo").strip().lower()
    if module == "contestacao":
        core_params = strip_melhorias_view_params(params)
        payload = get_or_build(
            "dashboard:contestacao",
            core_params,
            lambda: _contestacao_operational_dashboard(core_params),
        )
        all_rows = payload.get("_clientes_melhorias_all") or []
        return paginate_clientes_melhorias(all_rows, params)

    core_params = strip_melhorias_view_params(params)
    payload = get_or_build(
        "dashboard:resumo",
        core_params,
        lambda: _summary_dashboard(core_params),
    )
    all_rows = payload.get("_clientes_melhorias_all") or []
    return paginate_clientes_melhorias(all_rows, params)
