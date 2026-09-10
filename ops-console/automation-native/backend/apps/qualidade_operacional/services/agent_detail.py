# -*- coding: utf-8 -*-
"""Detalhe individual do agente para o modal da aba Agentes."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from django.db.models.functions import Lower

from apps.qualidade_operacional.models import QualidadeAgenteAcao
from apps.qualidade_operacional.services.analytics import (
    _build_kpis,
    build_ranking,
    build_serie,
    failure_weight,
    filtered_auditados,
    filtered_falhas,
    resolve_date_range,
    resolve_grain,
    shift_period,
)
from apps.qualidade_operacional.services.contestacao_metrics import (
    build_contestacao_metrics,
)
from apps.qualidade_operacional.services.criticidade import criticidade_label
from apps.qualidade_operacional.services.dashboard import (
    _enrich_kpis_with_protocols_and_contestacao,
    _merge_serie_contestacao,
    _params_with,
)
from apps.qualidade_operacional.services.enrichment import (
    build_dim_lookups,
    build_lider_responsavel_lookup,
    serialize_falha,
)
from apps.qualidade_operacional.services.queries import (
    clone_params,
    date_field_falhas,
    param_list,
    params_with,
)
from apps.qualidade_operacional.services.workforce_scope import (
    build_leader_history_index,
    resolve_leader_for_date,
    workforce_assignments,
)
from apps.workforce.models import AgentHistory

META_PCT = 99.7
_WEIGHT_FIELDS = (
    "protocolo",
    "matricula",
    "id_cliente",
    "etapa",
    "categoria_falha",
    "nivel_dificuldade",
    "nivel_dificuldade_confer",
    "tipo_falha_oficial",
    "tipo_falha",
    "des_problemas",
    "data",
)


def _initials(name: str) -> str:
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[-1][0]}".upper()


def _resolve_matricula(params) -> str:
    mats = param_list(params, "matricula")
    if not mats:
        raw = str(params.get("matricula") or "").strip()
        mats = [raw] if raw else []
    return (mats[0] if mats else "").strip().lower()


def _agent_identity(matricula: str, params) -> dict[str, Any]:
    assignments = workforce_assignments(params)
    assignment = assignments.get(matricula) or {}
    nome = assignment.get("agente") or matricula
    lider_no_periodo = assignment.get("lider") or "Sem líder"
    time = assignment.get("time") or ""
    funcao = "Operador"
    operacao = time
    lider_atual = lider_no_periodo

    histories = list(
        AgentHistory.objects.select_related("agent", "leader")
        .annotate(lan_lower=Lower("agent__user_lan_id"))
        .filter(lan_lower=matricula)
        .order_by("-start_date", "-id")
    )
    if histories:
        current = histories[0]
        if current.agent and current.agent.full_name:
            nome = current.agent.full_name.strip() or nome
        if current.job_title:
            funcao = current.job_title.strip() or funcao
        if current.team:
            time = current.team.strip() or time
            operacao = time
        if current.leader and current.leader.full_name:
            lider_atual = current.leader.full_name.strip() or lider_atual

    start, end = resolve_date_range(params)
    if start or end:
        mid = end or start
        if start and end:
            mid = start + (end - start) // 2
        resolved = resolve_leader_for_date(
            matricula,
            mid,
            period_start=start,
            period_end=end,
        )
        if resolved and resolved.get("lider"):
            lider_no_periodo = resolved["lider"]

    return {
        "matricula": matricula,
        "nome": nome,
        "iniciais": _initials(nome),
        "funcao": funcao,
        "lider": lider_no_periodo,
        "lider_atual": lider_atual,
        "lider_no_periodo": lider_no_periodo,
        "time": time,
        "operacao": operacao or None,
    }


def _quartile_from_ranking(matricula: str, params) -> dict[str, Any]:
    """Obtém quartil do ranking com os mesmos filtros globais (sem forçar matrícula)."""
    ranking_params = clone_params(params)
    ranking_params.pop("matricula", None)
    ranking_params["workforce_only"] = "1"
    ranking_params["by"] = "agente"

    ranking = build_ranking(ranking_params)
    row = next(
        (
            r
            for r in ranking.get("results") or []
            if (r.get("matricula") or r.get("key") or "").strip().lower() == matricula
        ),
        None,
    )
    if not row:
        return {"quartil": None, "rank": None, "prioridade": False, "ranking_row": None}
    quartil = row.get("quartil")
    return {
        "quartil": quartil,
        "rank": row.get("rank"),
        "prioridade": quartil in {"Q3", "Q4"},
        "ranking_row": row,
    }


def _failure_category_label(row: dict[str, Any]) -> str:
    official = (row.get("tipo_falha_oficial") or "").strip()
    if official:
        return official
    problem = (row.get("des_problemas") or "").strip()
    if problem:
        # Primeira linha / truncagem curta
        return problem.split("\n")[0][:120]
    tipo = (row.get("tipo_falha") or "").strip()
    return tipo or "Não informada"


def _build_falhas_distribuicao(fal_qs, grain: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    weights: dict[str, float] = defaultdict(float)
    criticidades: dict[str, str] = {}
    protocol_rows: dict[tuple[str, str], float] = {}

    for row in fal_qs.values(*_WEIGHT_FIELDS).iterator():
        label = _failure_category_label(row)
        weight = failure_weight(row)
        crit = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        if grain == "protocolo":
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (label, protocolo)
            prev = protocol_rows.get(key, 0.0)
            protocol_rows[key] = max(prev, weight)
            if label not in criticidades or crit == "Crítica":
                criticidades[label] = crit
        else:
            counts[label] += 1
            weights[label] += weight
            if label not in criticidades or crit == "Crítica":
                criticidades[label] = crit

    if grain == "protocolo":
        for (label, _prot), weight in protocol_rows.items():
            counts[label] += 1
            weights[label] += weight

    total_impacto = sum(weights.values()) or 0.0
    rows = []
    for label, falhas in counts.items():
        impacto = round(weights[label], 1)
        share = round(100.0 * impacto / total_impacto, 1) if total_impacto else None
        rows.append(
            {
                "key": label,
                "label": label,
                "falhas": falhas,
                "impacto_ponderado": impacto,
                "share_pct": share,
                "criticidade": criticidades.get(label),
                "drill": {"tipo_falha_oficial": label, "lista": "falhas"},
            }
        )
    rows.sort(key=lambda r: (-float(r["impacto_ponderado"]), -int(r["falhas"])))
    return rows[:12]


def _build_falhas_recentes(fal_qs, params, *, limit: int = 8) -> list[dict[str, Any]]:
    fal_date = date_field_falhas(params)
    rows = list(fal_qs.order_by(f"-{fal_date}", "-id")[:limit])
    cliente_ids = {r.id_cliente for r in rows if r.id_cliente}
    workflow_ids = {r.id_workflow for r in rows if r.id_workflow}
    mats = {r.matricula for r in rows if r.matricula} | {
        r.usuario_auditor for r in rows if r.usuario_auditor
    }
    clientes, workflows, agents = build_dim_lookups(cliente_ids, workflow_ids, mats)
    lider_lookup = build_lider_responsavel_lookup(rows, date_field=fal_date)

    open_actions = {
        (a.categoria_falha or "").strip().lower()
        for a in QualidadeAgenteAcao.objects.filter(
            matricula=_resolve_matricula(params),
            status__in=["aberta", "em_andamento"],
        ).only("categoria_falha")
    }

    out = []
    for r in rows:
        serialized = serialize_falha(
            r,
            clientes,
            workflows,
            agents,
            lider_responsavel=lider_lookup.get(r.pk),
        )
        crit = serialized.get("categoria_falha") or "Não informada"
        weight = failure_weight(
            {
                "protocolo": r.protocolo,
                "matricula": r.matricula,
                "id_cliente": r.id_cliente,
                "etapa": r.etapa,
                "categoria_falha": r.categoria_falha,
                "nivel_dificuldade": r.nivel_dificuldade,
                "nivel_dificuldade_confer": r.nivel_dificuldade_confer,
                "data": r.data,
            }
        )
        cat_key = (serialized.get("tipo_falha_oficial") or serialized.get("tipo_falha") or "").strip().lower()
        if cat_key and cat_key in open_actions:
            status_tratativa = "em_tratativa"
            status_label = "Em tratativa"
        elif crit == "Crítica":
            status_tratativa = "critica"
            status_label = "Crítica"
        else:
            status_tratativa = "sem_acao"
            status_label = "Sem ação"
        out.append(
            {
                **serialized,
                "impacto_ponderado": round(weight, 1),
                "status_tratativa": status_tratativa,
                "status_tratativa_label": status_label,
                "falha_identificada": _failure_category_label(
                    {
                        "tipo_falha_oficial": r.tipo_falha_oficial,
                        "des_problemas": r.des_problemas,
                        "tipo_falha": r.tipo_falha,
                    }
                ),
            }
        )
    return out


def _compute_trend(serie_points: list[dict[str, Any]], meta_pct: float) -> dict[str, Any]:
    """Tendência recente com base nos últimos pontos com EO ponderado."""
    with_eo = [
        p
        for p in serie_points
        if p.get("eo_ponderado_pct") is not None or p.get("eo_pct") is not None
    ]
    recent = with_eo[-7:] if len(with_eo) >= 2 else with_eo
    if len(recent) < 2:
        return {
            "code": "indefinido",
            "label": "Sem amostra",
            "days": len(recent),
            "message": "Amostra insuficiente para tendência.",
        }

    def _val(p):
        v = p.get("eo_ponderado_pct")
        return v if v is not None else p.get("eo_pct")

    first = _val(recent[0])
    last = _val(recent[-1])
    if first is None or last is None:
        return {
            "code": "indefinido",
            "label": "Sem amostra",
            "days": len(recent),
            "message": "Amostra insuficiente para tendência.",
        }
    delta = round(last - first, 1)
    spark = [_val(p) for p in recent]
    below = sum(1 for p in recent if (_val(p) or 0) < meta_pct)

    if delta <= -0.3:
        code, label = "queda", "Em queda"
    elif delta >= 0.3:
        code, label = "melhorando", "Melhorando"
    else:
        code, label = "estavel", "Estável"

    return {
        "code": code,
        "label": label,
        "days": len(recent),
        "delta_pp": delta,
        "sparkline": spark,
        "dias_abaixo_meta": below,
        "message": f"Últimos {len(recent)} dias",
    }


def _days_below_meta_streak(serie_points: list[dict[str, Any]], meta_pct: float) -> int | None:
    streak = 0
    for point in reversed(serie_points):
        eo = point.get("eo_ponderado_pct")
        if eo is None:
            eo = point.get("eo_pct")
        if eo is None:
            break
        if eo < meta_pct:
            streak += 1
        else:
            break
    return streak if streak > 0 else None


def _build_leitura_rapida(
    *,
    kpis: dict[str, Any],
    trend: dict[str, Any],
    falhas_dist: list[dict[str, Any]],
    serie_points: list[dict[str, Any]],
    meta_pct: float,
    quartil: str | None,
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    prev = kpis.get("previous") or {}
    auditados = int(kpis.get("auditados") or 0)
    small_base = auditados > 0 and auditados < 30

    streak = _days_below_meta_streak(serie_points, meta_pct)
    if streak and streak >= 3:
        insights.append(
            {
                "kind": "alerta",
                "text": f"EO abaixo da meta há {streak} dia{'s' if streak != 1 else ''}.",
            }
        )

    if falhas_dist:
        top = falhas_dist[0]
        share = top.get("share_pct")
        if share is not None and share >= 25 and int(top.get("falhas") or 0) >= 2:
            insights.append(
                {
                    "kind": "concentracao",
                    "text": (
                        f"Falhas concentram-se em {top['label']} "
                        f"({share:.0f}% do impacto)."
                    ),
                }
            )

    delta_impacto = prev.get("impacto_delta_pct")
    if delta_impacto is not None and delta_impacto > 10 and prev.get("comparable"):
        insights.append(
            {
                "kind": "impacto",
                "text": f"Impacto cresceu {delta_impacto:.0f}% vs. período anterior.",
            }
        )

    delta_eo = prev.get("delta_eo_ponderado_pp")
    if delta_eo is None:
        delta_eo = prev.get("delta_pp")
    if delta_eo is not None and prev.get("comparable") and delta_eo <= -1:
        insights.append(
            {
                "kind": "piora",
                "text": f"EO caiu {abs(delta_eo):.1f} p.p. em relação ao período anterior.",
            }
        )
    elif delta_eo is not None and prev.get("comparable") and delta_eo >= 1:
        insights.append(
            {
                "kind": "melhora",
                "text": f"EO melhorou {delta_eo:.1f} p.p. em relação ao período anterior.",
            }
        )

    crit_rows = [r for r in falhas_dist if r.get("criticidade") == "Crítica"]
    if crit_rows and int(crit_rows[0].get("falhas") or 0) >= 2:
        insights.append(
            {
                "kind": "critica",
                "text": (
                    f"Recorrência de falha crítica em {crit_rows[0]['label']} "
                    f"({crit_rows[0]['falhas']} ocorrências)."
                ),
            }
        )

    if quartil == "Q4":
        insights.append(
            {
                "kind": "prioridade",
                "text": "Agente no quartil Q4 (Prioridade) no ranking do período.",
            }
        )

    if trend.get("code") == "queda":
        insights.append(
            {
                "kind": "tendencia",
                "text": "Tendência de EO em queda nos últimos dias.",
            }
        )

    if small_base:
        insights.append(
            {
                "kind": "amostra",
                "text": (
                    f"Base pequena ({auditados} auditados): comparações podem ser frágeis."
                ),
            }
        )

    if not insights:
        if auditados == 0:
            return [
                {
                    "kind": "vazio",
                    "text": "Sem amostra suficiente no período e filtros atuais.",
                }
            ]
        return [
            {
                "kind": "neutro",
                "text": "Sem alertas relevantes com a amostra disponível.",
            }
        ]
    return insights[:5]


def _build_sugestoes(
    *,
    leitura: list[dict[str, Any]],
    falhas_dist: list[dict[str, Any]],
    trend: dict[str, Any],
    kpis: dict[str, Any],
    lider: str,
) -> list[dict[str, Any]]:
    sugestoes: list[dict[str, Any]] = []
    prev = kpis.get("previous") or {}
    # prazo sugerido: +7 dias a partir do fim do período atual
    current_end = prev.get("current_end_date")
    prazo = None
    if current_end:
        try:
            end_d = date.fromisoformat(str(current_end)[:10])
            prazo = (end_d + timedelta(days=7)).isoformat()
        except ValueError:
            prazo = None

    if falhas_dist:
        top = falhas_dist[0]
        sugestoes.append(
            {
                "tipo": "reciclagem",
                "titulo": "Reforço imediato",
                "descricao": (
                    f"Reciclagem do checklist relacionado a “{top['label']}”."
                ),
                "evidencia": (
                    f"{top['falhas']} ocorrências · {top.get('share_pct') or 0}% do impacto"
                ),
                "responsavel_sugerido": lider,
                "prazo_sugerido": prazo,
                "categoria_falha": top["label"],
                "prioridade": "alta" if top.get("criticidade") == "Crítica" else "media",
            }
        )

    eo = kpis.get("eo_ponderado_pct")
    if eo is not None and eo < META_PCT:
        sugestoes.append(
            {
                "tipo": "monitoria",
                "titulo": "Monitoria dirigida",
                "descricao": (
                    "Amostragem focada nos itens críticos. "
                    f"Meta: EO ≥ {META_PCT:g}% e zero reincidência."
                ),
                "evidencia": f"EO atual {eo}% (meta {META_PCT:g}%)",
                "responsavel_sugerido": lider,
                "prazo_sugerido": prazo,
                "categoria_falha": falhas_dist[0]["label"] if falhas_dist else None,
                "prioridade": "alta",
            }
        )

    if trend.get("code") == "queda":
        sugestoes.append(
            {
                "tipo": "acompanhamento",
                "titulo": "Acompanhamento semanal",
                "descricao": "Check-in semanal com o operador até estabilizar o EO.",
                "evidencia": trend.get("message") or "Tendência em queda",
                "responsavel_sugerido": lider,
                "prazo_sugerido": prazo,
                "categoria_falha": None,
                "prioridade": "media",
            }
        )

    kinds = {i.get("kind") for i in leitura}
    if "critica" in kinds:
        sugestoes.append(
            {
                "tipo": "calibracao",
                "titulo": "Calibração com qualidade",
                "descricao": "Sessão de calibração nos itens de falha crítica recorrente.",
                "evidencia": next(
                    (i["text"] for i in leitura if i.get("kind") == "critica"),
                    "Falha crítica recorrente",
                ),
                "responsavel_sugerido": lider,
                "prazo_sugerido": prazo,
                "categoria_falha": None,
                "prioridade": "alta",
            }
        )

    return sugestoes[:4]


def _criterio_saida(
    *,
    quartil: str | None,
    kpis: dict[str, Any],
    trend: dict[str, Any],
) -> dict[str, Any] | None:
    if quartil not in {"Q3", "Q4"} and (kpis.get("eo_ponderado_pct") or 100) >= META_PCT:
        return None
    return {
        "aplicavel": True,
        "titulo": "Critério de saída do acompanhamento",
        "descricao": (
            f"2 semanas consecutivas com EO ≥ {META_PCT:g}% "
            "e sem falha crítica no período."
        ),
        "regras": [
            {"code": "eo_meta", "label": f"EO ≥ {META_PCT:g}% por 2 semanas"},
            {"code": "sem_critica", "label": "Nenhuma falha crítica nas auditorias"},
            {"code": "acoes", "label": "Ações obrigatórias concluídas"},
        ],
        "fonte": "regra_operacional_padrao",
    }


def build_agent_detail(params) -> dict[str, Any]:
    matricula = _resolve_matricula(params)
    if not matricula:
        return {
            "ok": False,
            "error": "matricula_obrigatoria",
            "message": "Informe a matrícula do agente.",
        }

    scoped = params_with(params, matricula=matricula, workforce_only="1")
    identity = _agent_identity(matricula, scoped)
    quartil_info = _quartile_from_ranking(matricula, params)

    aud_qs = filtered_auditados(scoped)
    fal_qs = filtered_falhas(scoped)
    grain = resolve_grain(scoped)

    kpis = _build_kpis(scoped, aud_qs=aud_qs, fal_qs=fal_qs)
    kpis = _enrich_kpis_with_protocols_and_contestacao(scoped, kpis, aud_qs, fal_qs)

    serie = build_serie(_params_with(scoped, mode="eo", weighted="1"), aud_qs=aud_qs, fal_qs=fal_qs)
    serie = _merge_serie_contestacao(serie, kpis.get("contestacao") or {})

    # Série do período anterior (mesma duração / regra M-1)
    serie_anterior = {"ok": True, "points": []}
    start, end = resolve_date_range(scoped)
    if start and end:
        p_start, p_end = shift_period(start, end)
        prev_params = params_with(
            scoped,
            start_date=p_start.isoformat(),
            end_date=p_end.isoformat(),
        )
        serie_anterior = build_serie(
            _params_with(prev_params, mode="eo", weighted="1")
        )
        contest_prev = build_contestacao_metrics(prev_params)
        serie_anterior = _merge_serie_contestacao(serie_anterior, contest_prev)

    falhas_dist = _build_falhas_distribuicao(fal_qs, grain)
    falhas_recentes = _build_falhas_recentes(fal_qs, scoped)
    trend = _compute_trend(serie.get("points") or [], META_PCT)
    leitura = _build_leitura_rapida(
        kpis=kpis,
        trend=trend,
        falhas_dist=falhas_dist,
        serie_points=serie.get("points") or [],
        meta_pct=META_PCT,
        quartil=quartil_info.get("quartil"),
    )
    sugestoes = _build_sugestoes(
        leitura=leitura,
        falhas_dist=falhas_dist,
        trend=trend,
        kpis=kpis,
        lider=identity.get("lider") or "Líder",
    )
    criterio = _criterio_saida(
        quartil=quartil_info.get("quartil"),
        kpis=kpis,
        trend=trend,
    )

    ranking_row = quartil_info.get("ranking_row") or {}
    agent = {
        **identity,
        "quartil": quartil_info.get("quartil"),
        "rank": quartil_info.get("rank"),
        "prioridade": quartil_info.get("prioridade"),
        "auditados": ranking_row.get("auditados", kpis.get("auditados")),
        "falhas": ranking_row.get("falhas", kpis.get("falhas")),
        "eo_pct": ranking_row.get("eo_pct", kpis.get("eo_pct")),
        "eo_ponderado_pct": ranking_row.get(
            "eo_ponderado_pct", kpis.get("eo_ponderado_pct")
        ),
        "impacto_ponderado": ranking_row.get(
            "impacto_ponderado", kpis.get("impacto_ponderado")
        ),
    }

    return {
        "ok": True,
        "agent": agent,
        "kpis": kpis,
        "meta_pct": META_PCT,
        "trend": trend,
        "leitura_rapida": leitura,
        "serie": {
            "atual": serie,
            "anterior": serie_anterior,
            "meta_pct": META_PCT,
        },
        "falhas_distribuicao": falhas_dist,
        "falhas_recentes": falhas_recentes,
        "sugestoes": sugestoes,
        "criterio_saida": criterio,
        "contestacao_procedencia_disponivel": False,
        "notes": {
            "procedentes": (
                "Procedência de contestação ainda não está nas tabelas Qualidade; "
                "campo reservado para integração futura."
            ),
        },
    }


def serialize_acao(acao: QualidadeAgenteAcao) -> dict[str, Any]:
    return {
        "id": acao.pk,
        "matricula": acao.matricula,
        "titulo": acao.titulo,
        "descricao": acao.descricao,
        "tipo": acao.tipo,
        "responsavel": acao.responsavel,
        "prioridade": acao.prioridade,
        "status": acao.status,
        "prazo": acao.prazo.isoformat() if acao.prazo else None,
        "categoria_falha": acao.categoria_falha,
        "auditorias_vinculadas": acao.auditorias_vinculadas or [],
        "criterio_sucesso": acao.criterio_sucesso,
        "frequencia": acao.frequencia,
        "observacoes": acao.observacoes,
        "created_by": acao.created_by,
        "created_at": acao.created_at.isoformat() if acao.created_at else None,
        "updated_at": acao.updated_at.isoformat() if acao.updated_at else None,
        "concluida_at": acao.concluida_at.isoformat() if acao.concluida_at else None,
    }


def list_acoes(matricula: str, *, status: str | None = None) -> dict[str, Any]:
    qs = QualidadeAgenteAcao.objects.filter(matricula__iexact=matricula.strip())
    if status:
        qs = qs.filter(status=status)
    results = [serialize_acao(a) for a in qs.order_by("-created_at")[:100]]
    abertas = sum(1 for a in results if a["status"] in {"aberta", "em_andamento"})
    concluidas = sum(1 for a in results if a["status"] == "concluida")
    return {
        "ok": True,
        "count": len(results),
        "abertas": abertas,
        "concluidas": concluidas,
        "results": results,
    }
