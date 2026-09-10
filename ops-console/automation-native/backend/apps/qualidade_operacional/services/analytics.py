# -*- coding: utf-8 -*-
"""Agregações de Excelência Operacional (EO %)."""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Q, QuerySet

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.criticidade import (
    criticidade_label,
    is_procedimento_forcado,
    normalize_criticidade_text,
)
from apps.qualidade_operacional.services.enrichment import build_dim_lookups
from apps.qualidade_operacional.services.normalize import normalize_tipo_conclusao
from apps.qualidade_operacional.services.queries import (
    _parse_date,
    apply_common_filters,
    apply_falha_filters,
    date_field_auditados,
    date_field_falhas,
    exclude_processual_agent_links,
    params_with,
    resolve_date_axis,
)
from apps.qualidade_operacional.services.workforce_scope import (
    build_leader_history_index,
    build_responsibility_index,
    date_in_windows,
    facilitator_assignment_rows,
    facilitator_window_assignment_rows,
    facilitator_window_query,
    leader_assignment_rows,
    resolve_history_for_date,
    resolve_leader_for_date,
    workforce_assignments,
)

GRAIN_ETAPA = "etapa"
GRAIN_PROTOCOLO = "protocolo"
LEADER_TEMPORAL_UNATTRIBUTED_KEY = "__leader_temporal_unattributed__"
LEADER_TEMPORAL_UNATTRIBUTED_LABEL = "Sem atribuição na data"
VALID_GRAINS = frozenset({GRAIN_ETAPA, GRAIN_PROTOCOLO})
VALID_BREAKDOWN_DIMS = frozenset(
    {
        "tipo_analise",
        "etapa",
        "tipo_falha",
        "tipo_conclusao",
        "id_cliente",
        "id_workflow",
        "localidade",
    }
)
VALID_BREAKDOWN_METRICS = frozenset({"quantidade", "participacao", "taxa"})
TIPO_FALHA_CARDS = ("Manual", "Processual", "Automático")


def breakdown_dim_field(dim: str) -> str:
    if dim == "localidade":
        return "localidade_documento"
    return dim

# Labels estáveis para tooltip / UI (não alteram o cálculo).
_DENOM_LABELS = {
    "tipo_conclusao": "Auditados com o mesmo tipo de conclusão",
    "total": "Total de auditados do filtro (não só Processual)",
    "residual": "Auditados fora de Manual / Automático / Processual",
    "dimensao": "Auditados com o mesmo valor da dimensão",
}

# Até o dia anterior: impacto 1:1. A partir desta data (inclusive): matriz de pesos.
IMPACT_WEIGHT_CUTOVER = date(2026, 8, 1)
# Regra histórica independente da matriz: Cenários Avaliativos deixa de gerar
# impacto a partir de 04/01/2026 (data da auditoria/falha).
CENARIOS_AVALIATIVOS_ZERO_CUTOVER = date(2026, 1, 4)

_WEIGHT_FIELDS = (
    "protocolo",
    "matricula",
    "id_cliente",
    "etapa",
    "categoria_falha",
    "tipo_registro",
    "nivel_dificuldade",
    "nivel_dificuldade_confer",
    "data",
)


def _norm_weight_text(value: Any) -> str:
    return normalize_criticidade_text(value)


def _criticidade_label(value: Any) -> str:
    return criticidade_label(value)


def _failure_event_date(row: Any) -> date | None:
    """Data intrínseca da falha para o peso — sempre QualidadeFalha.data."""
    raw = row.get("data") if isinstance(row, dict) else getattr(row, "data", None)
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    return _parse_date(str(raw))


def failure_weight(row: Any) -> float:
    """Peso da falha no EO ponderado (matriz + regra temporal por QualidadeFalha.data)."""

    def value(field: str):
        return row.get(field) if isinstance(row, dict) else getattr(row, field, "")

    event_date = _failure_event_date(row)
    dificuldade = _norm_weight_text(
        value("nivel_dificuldade_confer") or value("nivel_dificuldade")
    )
    if (
        event_date is not None
        and event_date >= CENARIOS_AVALIATIVOS_ZERO_CUTOVER
        and "cenarios avaliativos" in dificuldade
    ):
        return 0.0

    # Antes do corte (ou data nula/legado): impacto 1:1, independente da matriz.
    if event_date is None or event_date < IMPACT_WEIGHT_CUTOVER:
        return 1.0

    etapa = _norm_weight_text(value("etapa"))
    id_cliente = value("id_cliente")
    # CLARO - FORMALIZAÇÃO: qualquer categoria (incl. vazia/"Não informada") = Procedimento, peso 1.
    if is_procedimento_forcado(id_cliente):
        return 1.0

    criticidade = criticidade_label(
        value("categoria_falha"),
        id_cliente=id_cliente,
        tipo_registro=value("tipo_registro"),
    )
    if "facil" in dificuldade:
        nivel = "facil"
    elif "media" in dificuldade or "medio" in dificuldade:
        nivel = "media"
    elif "dificil" in dificuldade:
        nivel = "dificil"
    else:
        nivel = ""

    if criticidade == "Crítica":
        return {"facil": 3.5, "media": 3.0, "dificil": 2.5}.get(nivel, 1.0)

    if criticidade == "Procedimento" and (
        "sobreposicao" in etapa or "validacao" in etapa
    ):
        return {"facil": 1.5, "media": 1.3, "dificil": 1.0}.get(nivel, 1.0)

    # Não crítica, procedimento em Análise Visual e categorias sem classificação.
    return 1.0


def _weighted_failures_by_field(
    qs: QuerySet,
    grain: str,
    field: str | None = None,
) -> dict[str, float]:
    fields = list(_WEIGHT_FIELDS)
    if field and field not in fields:
        fields.append(field)
    totals: dict[str, float] = defaultdict(float)
    protocol_weights: dict[tuple[str, str], float] = {}

    for row in qs.values(*fields).iterator():
        group = str(row.get(field) or "").strip() if field else "__total__"
        if field == "matricula":
            group = group.casefold()
        weight = failure_weight(row)
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (group, protocolo)
            protocol_weights[key] = max(protocol_weights.get(key, 0.0), weight)
        else:
            totals[group] += weight

    if grain == GRAIN_PROTOCOLO:
        for (group, _protocolo), weight in protocol_weights.items():
            totals[group] += weight
    return {key: round(value, 1) for key, value in totals.items()}


def _weighted_failure_total(qs: QuerySet, grain: str) -> float:
    return _weighted_failures_by_field(qs, grain).get("__total__", 0.0)


def _weighted_failures_by_day(
    qs: QuerySet,
    grain: str,
    fal_date: str,
) -> dict[date, float]:
    """Soma de pesos de falha por dia (mesmo critério do impacto ponderado)."""
    fields = list(_WEIGHT_FIELDS)
    if fal_date not in fields:
        fields.append(fal_date)
    totals: dict[date, float] = defaultdict(float)
    protocol_weights: dict[tuple[date, str], float] = {}

    for row in qs.exclude(**{f"{fal_date}__isnull": True}).values(*fields).iterator():
        raw_day = row.get(fal_date)
        if not raw_day:
            continue
        day = raw_day.date() if hasattr(raw_day, "date") else raw_day
        weight = failure_weight(row)
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (day, protocolo)
            protocol_weights[key] = max(protocol_weights.get(key, 0.0), weight)
        else:
            totals[day] += weight

    if grain == GRAIN_PROTOCOLO:
        for (day, _protocolo), weight in protocol_weights.items():
            totals[day] += weight
    return {key: round(value, 1) for key, value in totals.items()}


def build_criticidade_breakdown(
    fal_qs: QuerySet,
    *,
    grain: str,
    total_aud: int,
) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    weights: dict[str, float] = defaultdict(float)
    protocol_rows: dict[tuple[str, str], float] = {}

    for row in fal_qs.values(*_WEIGHT_FIELDS).iterator():
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        weight = failure_weight(row)
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            key = (label, protocolo)
            protocol_rows[key] = max(protocol_rows.get(key, 0.0), weight)
        else:
            counts[label] += 1
            weights[label] += weight

    if grain == GRAIN_PROTOCOLO:
        for (label, _protocolo), weight in protocol_rows.items():
            counts[label] += 1
            weights[label] += weight

    order = {"Crítica": 0, "Procedimento": 1, "Não Crítica": 2, "Não informada": 3}
    rows = [
        {
            "key": label,
            "label": label,
            "auditados": total_aud,
            "falhas": counts[label],
            "impacto_ponderado": round(weights[label], 1),
            "eo_pct": _eo_pct(total_aud, counts[label]),
            "eo_ponderado_pct": _eo_pct(total_aud, weights[label]),
            "drill": {"categoria_falha": label, "lista": "falhas"},
        }
        for label in counts
    ]
    rows.sort(
        key=lambda row: (
            -float(row["impacto_ponderado"]),
            order.get(str(row["label"]), 99),
        )
    )
    return {
        "ok": True,
        "grain": grain,
        "dim": "criticidade",
        "metric": "impacto_ponderado",
        "chart_title": "Impacto por nível de criticidade",
        "axis_title": "Impacto",
        "value_label": "Peso",
        "total_auditados": total_aud,
        "total_falhas": sum(counts.values()),
        "impacto_ponderado": round(sum(weights.values()), 1),
        "eo_pct": _eo_pct(total_aud, sum(counts.values())),
        "eo_ponderado_pct": _eo_pct(total_aud, sum(weights.values())),
        "rows": rows,
    }


def resolve_grain(params) -> str:
    grain = (params.get("grain") or GRAIN_ETAPA).strip().lower()
    return grain if grain in VALID_GRAINS else GRAIN_ETAPA


def resolve_date_range(params) -> tuple[date | None, date | None]:
    return _parse_date(params.get("start_date")), _parse_date(params.get("end_date"))


def _count_qs(qs: QuerySet, grain: str) -> int:
    if grain == GRAIN_PROTOCOLO:
        return (
            qs.exclude(protocolo="")
            .values("protocolo")
            .distinct()
            .count()
        )
    return qs.count()


def _eo_pct(auditados: int, falhas: int) -> float | None:
    if auditados <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - (falhas / auditados)) * 100.0)), 1)


def _data_quality_alert(auditados: int, falhas: int) -> dict[str, Any] | None:
    """Falhas sem população auditada associada — não inventar taxa/EO."""
    if falhas > 0 and auditados <= 0:
        n = falhas
        return {
            "code": "falhas_sem_auditados",
            "message": (
                f"{n} falha{'s' if n != 1 else ''} sem população auditada associada. "
                "Verificar classificação ou relacionamento dos dados."
            ),
            "falhas": n,
            "auditados": 0,
        }
    return None


def _enrich_tipificacao_row(row: dict[str, Any]) -> dict[str, Any]:
    mode = row.get("denominator_mode") or "tipo_conclusao"
    label = row.get("label") or row.get("key") or ""
    aud_n = int(row.get("auditados") or 0)
    fal_n = int(row.get("falhas") or 0)
    if mode == "total":
        group = "tipificacao_denom_total"
        formula = (
            f"EO% = (1 − falhas Processual / total auditados) × 100. "
            f"Aqui: (1 − {fal_n}/{aud_n}) × 100"
        )
    elif mode == "residual":
        group = "tipificacao_residual"
        formula = (
            f"EO% = (1 − falhas residuais / auditados residuais) × 100. "
            f"Aqui: (1 − {fal_n}/{aud_n}) × 100"
        )
    else:
        group = "tipificacao_pareada"
        formula = (
            f"EO% = (1 − falhas {label} / auditados {label}) × 100. "
            f"Aqui: (1 − {fal_n}/{aud_n}) × 100"
        )
    row["group"] = group
    row["denominator_label"] = _DENOM_LABELS.get(mode, mode)
    row["formula"] = formula
    dq = _data_quality_alert(aud_n, fal_n)
    if dq:
        row["data_quality"] = dq
        # Drill preferencial para falhas quando inconsistência.
        drill = dict(row.get("drill") or {})
        drill["lista"] = "falhas"
        row["drill"] = drill
    return row


def _delta_interpretation(delta_pp: float | None, *, comparable: bool) -> str | None:
    if not comparable:
        return "Período anterior sem dados comparáveis (sem auditados ou sem EO)."
    if delta_pp is None:
        return None
    if delta_pp > 0:
        return (
            f"Melhora de {delta_pp:+.1f} p.p. na EO versus o período anterior "
            "(maior EO = melhor)."
        )
    if delta_pp < 0:
        return (
            f"Piora de {delta_pp:+.1f} p.p. na EO versus o período anterior "
            "(menor EO = pior)."
        )
    return "EO estável versus o período anterior (variação 0,0 p.p.)."


def _filtered_auditados_base(params) -> QuerySet:
    from apps.qualidade_operacional.services.intranet_source import apply_source_mode_filter
    from apps.qualidade_operacional.services.official_metric import (
        apply_official_metric_auditados,
    )

    date_field = date_field_auditados(params)
    qs = apply_source_mode_filter(QualidadeAuditado.objects.all(), date_field=date_field)
    qs = apply_common_filters(qs, params, date_field=date_field)
    return apply_official_metric_auditados(qs, params)


def filtered_auditados(params) -> QuerySet:
    return _filtered_auditados_base(params)


def _filtered_falhas_base(params) -> QuerySet:
    from apps.qualidade_operacional.services.intranet_source import apply_source_mode_filter
    from apps.qualidade_operacional.services.official_metric import (
        apply_official_metric_falhas,
    )

    from apps.qualidade_operacional.services.deadline import apply_falhas_prazo_filter

    date_field = date_field_falhas(params)
    qs = apply_source_mode_filter(QualidadeFalha.objects.all(), date_field=date_field)
    qs = apply_falha_filters(qs, params)
    qs = apply_official_metric_falhas(qs, params)
    return apply_falhas_prazo_filter(qs, params)


def filtered_falhas(params) -> QuerySet:
    return _filtered_falhas_base(params)


def _group_count_map(qs: QuerySet, field: str, grain: str) -> dict[str, int]:
    if grain == GRAIN_PROTOCOLO:
        grouped = (
            qs.exclude(protocolo="")
            .values(field)
            .annotate(c=Count("protocolo", distinct=True))
        )
    else:
        grouped = qs.values(field).annotate(c=Count("id"))
    out: dict[str, int] = defaultdict(int)
    for row in grouped:
        raw = (row[field] or "").strip()
        if field in {"tipo_conclusao", "tipo_falha"}:
            # Sempre Manual | Automático | Processual (residual → Manual)
            label = normalize_tipo_conclusao(raw)
        else:
            label = raw or "(em branco)"
        out[label] += int(row["c"])
    return out


def _tipificacao_rows(
    aud_qs: QuerySet,
    fal_qs: QuerySet,
    grain: str,
    *,
    total_aud: int,
    include_outros: bool = False,
) -> list[dict[str, Any]]:
    """Cards Manual / Processual / Automático apenas (sem Outros).

    Processual usa denom = total auditados. Residual tipificação já entra em Manual
    via normalize_tipo_conclusao. ``include_outros`` é ignorado (legado).
    """
    del include_outros
    aud_map = _group_count_map(aud_qs, "tipo_conclusao", grain)
    fal_map = _group_count_map(fal_qs, "tipo_falha", grain)
    rows: list[dict[str, Any]] = []
    for label in TIPO_FALHA_CARDS:
        fal_n = fal_map.get(label, 0)
        if label == "Processual":
            aud_n = total_aud
            denom_mode = "total"
        else:
            aud_n = aud_map.get(label, 0)
            denom_mode = "tipo_conclusao"
        rows.append(
            _enrich_tipificacao_row(
                {
                    "key": label,
                    "label": label,
                    "auditados": aud_n,
                    "falhas": fal_n,
                    "eo_pct": _eo_pct(aud_n, fal_n),
                    "share_pct": round(100.0 * aud_n / total_aud, 1) if total_aud else None,
                    "denominator_mode": denom_mode,
                    "drill": {
                        "tipo_conclusao": "" if label == "Processual" else label,
                        "tipo_falha": label if label == "Processual" else "",
                        "tipo_bucket": "",
                        "lista": "falhas",
                    },
                }
            )
        )
    return rows


def _prev_calendar_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def shift_period(start: date, end: date) -> tuple[date, date]:
    """Período anterior comparável (semântica M-1 civil).

    - Mês civil completo (dia 1 → último dia) → mês civil anterior completo,
      mesmo quando o número de dias difere (jul/31 → jun/30, não 31/05–30/06).
    - Intervalo contido no mesmo mês (MTD / parcial) → mesmos dias-calendário
      no mês anterior, com clamp ao último dia (1–29/jul → 1–29/jun; 1–31/mar
      → 1–28/fev em ano não bissexto).
    - Intervalo que cruza meses → janela de mesma duração em dias imediatamente
      anterior (legado / fallback).
    """
    last_of_month = date(start.year, start.month, monthrange(start.year, start.month)[1])
    if start.day == 1 and end == last_of_month:
        py, pm = _prev_calendar_month(start.year, start.month)
        return date(py, pm, 1), date(py, pm, monthrange(py, pm)[1])

    if start.year == end.year and start.month == end.month:
        py, pm = _prev_calendar_month(start.year, start.month)
        last_prev = monthrange(py, pm)[1]
        prev_start = date(py, pm, min(start.day, last_prev))
        prev_end = date(py, pm, min(end.day, last_prev))
        if prev_end < prev_start:
            prev_end = prev_start
        return prev_start, prev_end

    days = (end - start).days
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days)
    return prev_start, prev_end


def _build_kpis(
    params,
    *,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
    auditados: int | None = None,
    falhas: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    from apps.qualidade_operacional.services.contestacao_metrics import (
        resolve_population_qs,
    )

    grain = resolve_grain(params)
    if aud_qs is None or fal_qs is None:
        def_aud, def_fal = resolve_population_qs(params, scope=scope)
        aud_qs = aud_qs if aud_qs is not None else def_aud
        fal_qs = fal_qs if fal_qs is not None else def_fal
    auditados = _count_qs(aud_qs, grain) if auditados is None else auditados
    falhas = _count_qs(fal_qs, grain) if falhas is None else falhas
    impacto_ponderado = _weighted_failure_total(fal_qs, grain)
    eo = _eo_pct(auditados, falhas)
    eo_ponderado = _eo_pct(auditados, impacto_ponderado)

    prev_eo = None
    prev_auditados = None
    prev_falhas = None
    prev_impacto_ponderado = None
    prev_eo_ponderado = None
    p_start: date | None = None
    p_end: date | None = None
    start, end = resolve_date_range(params)
    if start and end:
        p_start, p_end = shift_period(start, end)
        prev_params = params_with(
            params,
            start_date=p_start.isoformat(),
            end_date=p_end.isoformat(),
        )
        prev_aud_qs, prev_fal_qs = resolve_population_qs(prev_params, scope=scope)
        prev_aud = _count_qs(prev_aud_qs, grain)
        prev_fal = _count_qs(prev_fal_qs, grain)
        prev_auditados = prev_aud
        prev_falhas = prev_fal
        prev_eo = _eo_pct(prev_aud, prev_fal)
        prev_impacto_ponderado = _weighted_failure_total(prev_fal_qs, grain)
        prev_eo_ponderado = _eo_pct(prev_aud, prev_impacto_ponderado)

    delta_pp = None
    if eo is not None and prev_eo is not None:
        delta_pp = round(eo - prev_eo, 1)

    delta_eo_ponderado_pp = None
    if eo_ponderado is not None and prev_eo_ponderado is not None:
        delta_eo_ponderado_pp = round(eo_ponderado - prev_eo_ponderado, 1)

    impacto_delta_pct = None
    if (
        impacto_ponderado is not None
        and prev_impacto_ponderado is not None
        and prev_impacto_ponderado > 0
    ):
        impacto_delta_pct = round(
            100.0 * (impacto_ponderado - prev_impacto_ponderado) / prev_impacto_ponderado,
            1,
        )

    comparable = bool(
        start and end and prev_auditados is not None and prev_auditados > 0 and prev_eo is not None
    )
    by_tipo = _tipificacao_rows(aud_qs, fal_qs, grain, total_aud=auditados)
    crit_totals = _criticidade_counts_total(fal_qs)
    date_axis = resolve_date_axis(params)
    from apps.qualidade_operacional.services.official_metric import metric_mode_payload

    return {
        "ok": True,
        "grain": grain,
        "date_axis": date_axis,
        **metric_mode_payload(params),
        "date_field_auditados": date_field_auditados(params),
        "date_field_falhas": date_field_falhas(params),
        "auditados": auditados,
        "falhas": falhas,
        **crit_totals,
        "eo_pct": eo,
        "impacto_ponderado": impacto_ponderado,
        "eo_ponderado_pct": eo_ponderado,
        "formula": (
            f"EO% = (1 − falhas / auditados) × 100. Aqui: (1 − {falhas}/{auditados}) × 100"
            if auditados > 0
            else "EO% indisponível: auditados = 0 no filtro."
        ),
        "formula_ponderada": (
            "EO = (1 - impacto / auditados) * 100. "
            f"Aqui: (1 - {impacto_ponderado}/{auditados}) * 100"
            if auditados > 0
            else "EO indisponível: auditados = 0 no filtro."
        ),
        "previous": {
            "start_date": p_start.isoformat() if p_start else None,
            "end_date": p_end.isoformat() if p_end else None,
            "current_start_date": start.isoformat() if start else None,
            "current_end_date": end.isoformat() if end else None,
            "current_eo_pct": eo,
            "auditados": prev_auditados,
            "falhas": prev_falhas,
            "eo_pct": prev_eo,
            "impacto_ponderado": prev_impacto_ponderado,
            "eo_ponderado_pct": prev_eo_ponderado,
            "delta_pp": delta_pp,
            "delta_eo_ponderado_pp": delta_eo_ponderado_pp,
            "impacto_delta_pct": impacto_delta_pct,
            "comparable": comparable,
            "interpretation": _delta_interpretation(delta_pp, comparable=comparable),
        },
        "by_tipo_conclusao": by_tipo,
    }


def build_kpis(params) -> dict[str, Any]:
    return _build_kpis(params)


_MONTH_ABBR = (
    "",
    "Jan",
    "Fev",
    "Mar",
    "Abr",
    "Mai",
    "Jun",
    "Jul",
    "Ago",
    "Set",
    "Out",
    "Nov",
    "Dez",
)


def _month_short_label(value: date) -> str:
    return f"{_MONTH_ABBR[value.month]}/{str(value.year)[-2:]}"


def _kpis_snapshot(params, *, scope: str | None = None) -> dict[str, Any]:
    kpis = _build_kpis(params, scope=scope)
    return {
        "auditados": int(kpis.get("auditados") or 0),
        "falhas": int(kpis.get("falhas") or 0),
        "eo_ponderado_pct": kpis.get("eo_ponderado_pct"),
        "impacto_ponderado": kpis.get("impacto_ponderado"),
        "eo_pct": kpis.get("eo_pct"),
    }


def build_month_over_month_kpis(
    params,
    *,
    anchor_end: date | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Comparativo M-1 no mês civil de ``anchor_end`` (MTD quando parcial)."""
    start, end = resolve_date_range(params)
    anchor = anchor_end or end
    if not anchor:
        return {
            "ok": True,
            "comparable": False,
            "anchor_label": None,
            "previous_label": None,
            "current": None,
            "previous": None,
            "delta_eo_ponderado_pp": None,
        }

    month_start = date(anchor.year, anchor.month, 1)
    month_end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
    cur_start = max(start or month_start, month_start)
    cur_end = min(end or anchor, month_end)
    if cur_end < cur_start:
        return {
            "ok": True,
            "comparable": False,
            "anchor_label": _month_short_label(anchor),
            "previous_label": None,
            "current": None,
            "previous": None,
            "delta_eo_ponderado_pp": None,
        }

    prev_start, prev_end = shift_period(cur_start, cur_end)
    cur_params = params_with(
        params,
        start_date=cur_start.isoformat(),
        end_date=cur_end.isoformat(),
    )
    prev_params = params_with(
        params,
        start_date=prev_start.isoformat(),
        end_date=prev_end.isoformat(),
    )
    current = _kpis_snapshot(cur_params, scope=scope)
    previous = _kpis_snapshot(prev_params, scope=scope)

    delta_eo_ponderado_pp = None
    cur_eo = current.get("eo_ponderado_pct")
    prev_eo = previous.get("eo_ponderado_pct")
    if cur_eo is not None and prev_eo is not None:
        delta_eo_ponderado_pp = round(float(cur_eo) - float(prev_eo), 1)

    comparable = bool(
        previous.get("auditados", 0) > 0
        and prev_eo is not None
        and cur_eo is not None
    )

    return {
        "ok": True,
        "comparable": comparable,
        "anchor_label": _month_short_label(cur_end),
        "previous_label": _month_short_label(prev_end),
        "current_start_date": cur_start.isoformat(),
        "current_end_date": cur_end.isoformat(),
        "previous_start_date": prev_start.isoformat(),
        "previous_end_date": prev_end.isoformat(),
        "current": current,
        "previous": previous,
        "delta_eo_ponderado_pp": delta_eo_ponderado_pp,
    }


def build_serie(
    params,
    *,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
) -> dict[str, Any]:
    grain = resolve_grain(params)
    mode = (params.get("mode") or "eo").strip().lower()
    if mode not in {"eo", "volume"}:
        mode = "eo"
    date_axis = resolve_date_axis(params)
    aud_date = date_field_auditados(params)
    fal_date = date_field_falhas(params)

    include_weighted = str(params.get("weighted") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "ponderado",
    }

    aud_qs = aud_qs if aud_qs is not None else filtered_auditados(params)
    fal_qs = fal_qs if fal_qs is not None else filtered_falhas(params)

    aud_by_day: dict[date, int] = {}
    fal_by_day: dict[date, int] = {}

    if grain == GRAIN_PROTOCOLO:
        for row in (
            aud_qs.exclude(protocolo="")
            .exclude(**{f"{aud_date}__isnull": True})
            .values(aud_date)
            .annotate(c=Count("protocolo", distinct=True))
        ):
            if row[aud_date]:
                aud_by_day[row[aud_date]] = int(row["c"])
        for row in (
            fal_qs.exclude(protocolo="")
            .exclude(**{f"{fal_date}__isnull": True})
            .values(fal_date)
            .annotate(c=Count("protocolo", distinct=True))
        ):
            if row[fal_date]:
                fal_by_day[row[fal_date]] = int(row["c"])
    else:
        # DateField: agrupar direto pelo campo (TruncDay impede uso de índice).
        for row in (
            aud_qs.exclude(**{f"{aud_date}__isnull": True})
            .values(aud_date)
            .annotate(c=Count("id"))
        ):
            day = row[aud_date]
            if day:
                aud_by_day[day] = int(row["c"])
        for row in (
            fal_qs.exclude(**{f"{fal_date}__isnull": True})
            .values(fal_date)
            .annotate(c=Count("id"))
        ):
            day = row[fal_date]
            if day:
                fal_by_day[day] = int(row["c"])

    impact_by_day: dict[date, float] = {}
    if include_weighted:
        impact_by_day = _weighted_failures_by_day(fal_qs, grain, fal_date)

    days = sorted(set(aud_by_day) | set(fal_by_day) | set(impact_by_day))
    points = []
    for d in days:
        a = aud_by_day.get(d, 0)
        f = fal_by_day.get(d, 0)
        point: dict[str, Any] = {
            "date": d.isoformat(),
            "auditados": a,
            "falhas": f,
            "eo_pct": _eo_pct(a, f),
        }
        if include_weighted:
            impact = impact_by_day.get(d, 0.0)
            point["impacto_ponderado"] = impact
            point["eo_ponderado_pct"] = _eo_pct(a, impact)
        points.append(point)

    return {
        "ok": True,
        "grain": grain,
        "mode": mode,
        "weighted": include_weighted,
        "date_axis": date_axis,
        "date_field_auditados": aud_date,
        "date_field_falhas": fal_date,
        "points": points,
    }


def resolve_breakdown_metric(params) -> str:
    metric = (params.get("metric") or "quantidade").strip().lower()
    return metric if metric in VALID_BREAKDOWN_METRICS else "quantidade"


def _apply_breakdown_metric(
    rows: list[dict[str, Any]],
    *,
    metric: str,
    total_fal: int,
) -> tuple[list[dict[str, Any]], str, str]:
    """Anexa value/value_label e títulos coerentes com a métrica."""
    if metric == "participacao":
        axis_title = "Participação nas falhas (%)"
        value_label = "Participação"
        for row in rows:
            fal_n = int(row.get("falhas") or 0)
            share = round(100.0 * fal_n / total_fal, 1) if total_fal else None
            row["falha_share_pct"] = share
            row["taxa_pct"] = (
                round(100.0 * fal_n / int(row.get("auditados") or 0), 1)
                if int(row.get("auditados") or 0) > 0
                else None
            )
            row["value"] = share
            row["value_label"] = value_label
            dq = _data_quality_alert(int(row.get("auditados") or 0), fal_n)
            if dq and "data_quality" not in row:
                row["data_quality"] = dq
    elif metric == "taxa":
        axis_title = "Taxa de falhas sobre auditados (%)"
        value_label = "Taxa"
        for row in rows:
            aud_n = int(row.get("auditados") or 0)
            fal_n = int(row.get("falhas") or 0)
            share = round(100.0 * fal_n / total_fal, 1) if total_fal else None
            row["falha_share_pct"] = share
            taxa = round(100.0 * fal_n / aud_n, 1) if aud_n > 0 else None
            row["taxa_pct"] = taxa
            row["value"] = taxa
            row["value_label"] = value_label
            dq = _data_quality_alert(aud_n, fal_n)
            if dq:
                row["data_quality"] = dq
    else:
        axis_title = "Quantidade de falhas"
        value_label = "Quantidade"
        for row in rows:
            fal_n = int(row.get("falhas") or 0)
            aud_n = int(row.get("auditados") or 0)
            share = round(100.0 * fal_n / total_fal, 1) if total_fal else None
            row["falha_share_pct"] = share
            row["taxa_pct"] = round(100.0 * fal_n / aud_n, 1) if aud_n > 0 else None
            row["value"] = fal_n
            row["value_label"] = value_label
            dq = _data_quality_alert(aud_n, fal_n)
            if dq and "data_quality" not in row:
                row["data_quality"] = dq

    return rows, axis_title, value_label


def _build_breakdown(
    params,
    *,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
    total_aud: int | None = None,
    total_fal: int | None = None,
    precomputed_tip_rows: list[dict[str, Any]] | None = None,
    precomputed_dim_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    grain = resolve_grain(params)
    dim = (params.get("dim") or "tipo_analise").strip().lower()
    if dim not in VALID_BREAKDOWN_DIMS:
        dim = "tipo_analise"
    metric = resolve_breakdown_metric(params)

    aud_qs = aud_qs if aud_qs is not None else filtered_auditados(params)
    fal_qs = fal_qs if fal_qs is not None else filtered_falhas(params)
    total_aud = _count_qs(aud_qs, grain) if total_aud is None else total_aud
    total_fal = _count_qs(fal_qs, grain) if total_fal is None else total_fal

    rows: list[dict[str, Any]] = []

    if precomputed_dim_rows is not None and dim not in {"tipo_falha", "tipo_conclusao"}:
        # Mesmos auditados/falhas/eo; métrica e Pareto aplicados abaixo.
        for raw in precomputed_dim_rows:
            row = dict(raw)
            a = int(row.get("auditados") or 0)
            f = int(row.get("falhas") or 0)
            row.setdefault("eo_pct", _eo_pct(a, f))
            row.setdefault("denominator_mode", "dimensao")
            row.setdefault("denominator_label", _DENOM_LABELS["dimensao"])
            row.setdefault(
                "formula",
                (
                    f"EO% = (1 − falhas / auditados da categoria) × 100. "
                    f"Aqui: (1 − {f}/{a}) × 100"
                    if a > 0
                    else "EO% indisponível: auditados da categoria = 0."
                ),
            )
            row.setdefault("group", "dimensao")
            dq = _data_quality_alert(a, f)
            if dq:
                row["data_quality"] = dq
            rows.append(row)
        rows, axis_title, value_label = _apply_breakdown_metric(
            rows, metric=metric, total_fal=total_fal
        )
        running = 0
        for row in rows:
            fal_n = int(row.get("falhas") or 0)
            running += fal_n
            row["cum_falhas"] = running
            row["cum_share_pct"] = (
                round(100.0 * running / total_fal, 1) if total_fal else None
            )
        dim_titles = {
            "tipo_analise": "tipo de análise",
            "etapa": "etapa",
            "tipo_falha": "tipo de falha",
            "tipo_conclusao": "tipo de conclusão",
            "id_cliente": "cliente",
            "id_workflow": "workflow",
            "localidade": "UF do documento",
        }
        chart_title = {
            "quantidade": f"Concentração de falhas por {dim_titles.get(dim, dim)}",
            "participacao": f"Participação nas falhas por {dim_titles.get(dim, dim)}",
            "taxa": f"Taxa de falhas por {dim_titles.get(dim, dim)}",
        }[metric]
        pareto_note = (
            "Ordenado por taxa — o corte de ~80% aplica-se a quantidade/participação."
            if metric == "taxa"
            else (
                "Itens até ~80% acumulado — onde concentrar a ação. "
                "Percentuais referem-se às falhas desta dimensão."
            )
        )
        return {
            "ok": True,
            "grain": grain,
            "dim": dim,
            "metric": metric,
            "chart_title": chart_title,
            "axis_title": axis_title,
            "value_label": value_label,
            "total_auditados": total_aud,
            "total_falhas": total_fal,
            "eo_pct": _eo_pct(total_aud, total_fal),
            "rows": rows,
            "pareto_note": pareto_note,
        }

    dim_field = breakdown_dim_field(dim)

    if dim in {"tipo_falha", "tipo_conclusao"}:
        if precomputed_tip_rows is None:
            rows = _tipificacao_rows(
                aud_qs, fal_qs, grain, total_aud=total_aud
            )
        else:
            rows = [dict(row) for row in precomputed_tip_rows]
    else:
        # tipo_analise / etapa / id_cliente / id_workflow / localidade (UF)
        if grain == GRAIN_PROTOCOLO:
            aud_grouped = (
                aud_qs.exclude(protocolo="")
                .values(dim_field)
                .annotate(c=Count("protocolo", distinct=True))
            )
            fal_grouped = (
                fal_qs.exclude(protocolo="")
                .values(dim_field)
                .annotate(c=Count("protocolo", distinct=True))
            )
        else:
            aud_grouped = aud_qs.values(dim_field).annotate(c=Count("id"))
            fal_grouped = fal_qs.values(dim_field).annotate(c=Count("id"))

        def _dim_key(raw: Any) -> str:
            if dim in {"id_cliente", "id_workflow"}:
                if raw is None:
                    return "(sem id)"
                return str(int(raw))
            if raw is None or raw == "":
                return "(em branco)"
            text = str(raw).strip()
            return text or "(em branco)"

        aud_map = {_dim_key(r[dim_field]): int(r["c"]) for r in aud_grouped}
        fal_map = {_dim_key(r[dim_field]): int(r["c"]) for r in fal_grouped}
        keys = sorted(set(aud_map) | set(fal_map), key=lambda k: (-fal_map.get(k, 0), k))

        nomes_cli: dict[int, str] = {}
        nomes_wf: dict[int, str] = {}
        if dim == "id_cliente":
            ids = {int(k) for k in keys if k.isdigit()}
            nomes_cli, _, _ = build_dim_lookups(ids, set(), set())
        elif dim == "id_workflow":
            ids = {int(k) for k in keys if k.isdigit()}
            _, nomes_wf, _ = build_dim_lookups(set(), ids, set())

        for key in keys[:50]:
            a = aud_map.get(key, 0)
            f = fal_map.get(key, 0)
            label = key
            if dim == "id_cliente" and key.isdigit():
                cid = int(key)
                nome = (nomes_cli.get(cid) or "").strip()
                label = nome if nome else f"Cliente {cid}"
            elif dim == "id_workflow" and key.isdigit():
                wid = int(key)
                nome = (nomes_wf.get(wid) or "").strip()
                label = nome if nome else f"Workflow {wid}"
            row: dict[str, Any] = {
                "key": key,
                "label": label,
                "auditados": a,
                "falhas": f,
                "eo_pct": _eo_pct(a, f),
                "denominator_mode": "dimensao",
                "denominator_label": _DENOM_LABELS["dimensao"],
                "formula": (
                    f"EO% = (1 − falhas / auditados da categoria) × 100. "
                    f"Aqui: (1 − {f}/{a}) × 100"
                    if a > 0
                    else "EO% indisponível: auditados da categoria = 0."
                ),
                "group": "dimensao",
            }
            if dim == "id_cliente" and key.isdigit():
                row["drill"] = {"id_cliente": key, "lista": "falhas"}
            elif dim == "id_workflow" and key.isdigit():
                row["drill"] = {"id_workflow": key, "lista": "falhas"}
            elif dim == "etapa":
                row["drill"] = {"etapa": key if key != "(em branco)" else "", "lista": "falhas"}
            elif dim == "localidade":
                row["drill"] = {
                    "localidade": key if key != "(em branco)" else "",
                    "lista": "falhas",
                }
            dq = _data_quality_alert(a, f)
            if dq:
                row["data_quality"] = dq
            rows.append(row)

    rows, axis_title, value_label = _apply_breakdown_metric(
        rows, metric=metric, total_fal=total_fal
    )

    # Pareto: acumulado sobre falhas (sempre, independente da métrica do eixo).
    running = 0
    for row in rows:
        fal_n = int(row.get("falhas") or 0)
        running += fal_n
        row["cum_falhas"] = running
        row["cum_share_pct"] = (
            round(100.0 * running / total_fal, 1) if total_fal else None
        )

    dim_titles = {
        "tipo_analise": "tipo de análise",
        "etapa": "etapa",
        "tipo_falha": "tipo de falha",
        "tipo_conclusao": "tipo de conclusão",
        "id_cliente": "cliente",
        "id_workflow": "workflow",
        "localidade": "UF do documento",
    }
    chart_title = {
        "quantidade": f"Concentração de falhas por {dim_titles.get(dim, dim)}",
        "participacao": f"Participação nas falhas por {dim_titles.get(dim, dim)}",
        "taxa": f"Taxa de falhas por {dim_titles.get(dim, dim)}",
    }[metric]
    pareto_note = (
        "Ordenado por taxa — o corte de ~80% aplica-se a quantidade/participação."
        if metric == "taxa"
        else (
            "Itens até ~80% acumulado — onde concentrar a ação. "
            "Percentuais referem-se às falhas desta dimensão."
        )
    )

    return {
        "ok": True,
        "grain": grain,
        "dim": dim,
        "metric": metric,
        "chart_title": chart_title,
        "axis_title": axis_title,
        "value_label": value_label,
        "total_auditados": total_aud,
        "total_falhas": total_fal,
        "eo_pct": _eo_pct(total_aud, total_fal),
        "rows": rows,
        "pareto_note": pareto_note,
    }


def build_breakdown(params) -> dict[str, Any]:
    return _build_breakdown(params)


def _quartile_label(rank_index: int, n: int) -> str:
    """Q1 = melhor EO (topo), Q4 = pior. rank_index 0 = melhor."""
    if n <= 0:
        return "Q1"
    # Split into 4 buckets; top 25% = Q1
    pos = (rank_index + 0.5) / n
    if pos <= 0.25:
        return "Q1"
    if pos <= 0.50:
        return "Q2"
    if pos <= 0.75:
        return "Q3"
    return "Q4"


def _operational_ranking_params(params):
    scoped = params.copy() if hasattr(params, "copy") else dict(params)
    scoped["workforce_only"] = "1"
    scoped["agent_linked_only"] = "1"
    return scoped


def operational_agentes_scope_params(params):
    """Escopo operacional da aba Agentes: líder + vínculo HC + tipificação Manual."""
    scoped = _operational_ranking_params(params)
    scoped["responsibility_scope"] = "lider"
    scoped["tipo_conclusao"] = "Manual"
    return scoped


def operational_facilitator_scope_params(params):
    """Escopo operacional da tabela detalhe facilitador: janelas HC + Manual."""
    scoped = _operational_ranking_params(params)
    scoped["responsibility_scope"] = "facilitador"
    scoped["tipo_conclusao"] = "Manual"
    return scoped


def _count_queryset_rows(qs: QuerySet, grain: str) -> int:
    if grain == GRAIN_PROTOCOLO:
        return qs.exclude(protocolo="").values("protocolo").distinct().count()
    return qs.count()


def compute_operational_detail_scope(params) -> dict[str, Any]:
    """Contagens alinhadas à tabela/exportação da aba Agentes."""
    scoped = operational_agentes_scope_params(params)
    grain = resolve_grain(scoped)
    aud_qs = filtered_auditados(scoped)
    fal_qs = filtered_falhas(scoped)
    return {
        "grain": grain,
        "auditados": _count_queryset_rows(aud_qs, grain),
        "falhas": _count_queryset_rows(fal_qs, grain),
    }


def compute_operational_facilitator_detail_scope(params) -> dict[str, Any]:
    """Contagens alinhadas à tabela/exportação de facilitadores na aba Agentes."""
    scoped = operational_facilitator_scope_params(params)
    grain = resolve_grain(scoped)
    aud_qs = filtered_auditados(scoped)
    fal_qs = filtered_falhas(scoped)
    return {
        "grain": grain,
        "auditados": _count_queryset_rows(aud_qs, grain),
        "falhas": _count_queryset_rows(fal_qs, grain),
    }


def diagnose_leader_hierarchy_scope(params) -> dict[str, Any]:
    """Diagnóstico: segmentos duplicados, sem atribuição e gap detalhe × hierarquia."""
    scoped = operational_agentes_scope_params(params)
    grain = resolve_grain(scoped)
    aud_qs = filtered_auditados(scoped)
    fal_qs = filtered_falhas(scoped)
    hierarchy = build_leader_hierarchy(scoped, aud_qs=aud_qs, fal_qs=fal_qs)
    detail = compute_operational_detail_scope(params)
    results = hierarchy.get("results") or []
    multi_segment: list[dict[str, Any]] = []
    by_mat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        key = row.get("key") or ""
        if key == LEADER_TEMPORAL_UNATTRIBUTED_KEY:
            continue
        mat = (row.get("matricula") or "").strip().lower()
        if mat:
            by_mat[mat].append(row)
    for mat, rows in by_mat.items():
        if len(rows) > 1:
            multi_segment.append(
                {
                    "matricula": mat,
                    "segments": len(rows),
                    "auditados": sum(int(r.get("auditados") or 0) for r in rows),
                }
            )
    unattributed = next(
        (row for row in results if row.get("key") == LEADER_TEMPORAL_UNATTRIBUTED_KEY),
        None,
    )
    hierarchy_auditados = sum(
        int(row.get("auditados") or 0)
        for row in results
        if row.get("key") != LEADER_TEMPORAL_UNATTRIBUTED_KEY
    )
    return {
        "grain": grain,
        "detail_scope": detail,
        "hierarchy_auditados": hierarchy_auditados,
        "hierarchy_falhas": sum(int(row.get("falhas") or 0) for row in results),
        "unattributed_auditados": int((unattributed or {}).get("auditados") or 0),
        "agents_with_multiple_segments": multi_segment,
        "detail_vs_hierarchy_auditados_delta": detail["auditados"] - hierarchy_auditados,
    }


def _operational_counts_by_matricula(qs: QuerySet, grain: str) -> dict[str, int]:
    if grain == GRAIN_PROTOCOLO:
        grouped = qs.exclude(protocolo="").exclude(matricula="").values("matricula").annotate(
            c=Count("protocolo", distinct=True)
        )
    else:
        grouped = qs.exclude(matricula="").values("matricula").annotate(c=Count("id"))
    return {
        (row["matricula"] or "").strip().lower(): int(row["c"])
        for row in grouped
        if row["matricula"]
    }


def _criticidade_counts_total(fal_qs: QuerySet) -> dict[str, int]:
    """Totais de falhas por criticidade no recorte filtrado."""
    totals = {
        "falhas_criticas": 0,
        "falhas_nao_criticas": 0,
        "falhas_procedimento": 0,
    }
    for row in fal_qs.values("categoria_falha", "id_cliente").iterator():
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        if label == "Crítica":
            totals["falhas_criticas"] += 1
        elif label == "Não Crítica":
            totals["falhas_nao_criticas"] += 1
        elif label == "Procedimento":
            totals["falhas_procedimento"] += 1
    return totals


def _criticidade_counts_by_matricula(fal_qs: QuerySet) -> dict[str, dict[str, int]]:
    """Contagem de falhas por criticidade (Crítica / Não Crítica / Procedimento) por matrícula."""
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "falhas_criticas": 0,
            "falhas_nao_criticas": 0,
            "falhas_procedimento": 0,
        }
    )
    for row in fal_qs.values("matricula", "categoria_falha", "id_cliente").iterator():
        matricula = (row.get("matricula") or "").strip().lower()
        if not matricula:
            continue
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        bucket = totals[matricula]
        if label == "Crítica":
            bucket["falhas_criticas"] += 1
        elif label == "Não Crítica":
            bucket["falhas_nao_criticas"] += 1
        elif label == "Procedimento":
            bucket["falhas_procedimento"] += 1
    return totals


def _build_facilitator_agent_rows(
    params,
    *,
    assignments: dict[str, dict[str, Any]],
    aud_qs: QuerySet,
    fal_qs: QuerySet,
    grain: str,
) -> list[dict[str, Any]]:
    """Uma linha por (agente, facilitador), com métricas só das janelas daquela linha."""
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    date_field_aud = date_field_auditados(params)
    date_field_fal = date_field_falhas(params)
    index = build_responsibility_index()

    row_meta: dict[str, dict[str, Any]] = {}
    for matricula, fallback in assignments.items():
        for fac_row in facilitator_assignment_rows(
            index, matricula, start, end, fallback
        ):
            row_meta[fac_row["key"]] = fac_row

    if not row_meta:
        return []

    metrics: dict[str, dict[str, Any]] = {
        key: {
            "auditados": 0,
            "falhas": 0,
            "impacto_ponderado": 0.0,
            "falhas_criticas": 0,
            "falhas_nao_criticas": 0,
            "falhas_procedimento": 0,
            "protocol_weights": {},
            "protocol_auditados": set(),
            "protocol_falhas": set(),
        }
        for key in row_meta
    }

    def _bucket_for(matricula: str, on_date: date | None) -> str | None:
        if not on_date:
            return None
        matches = [
            window
            for window in index.get(matricula, [])
            if window["start"] <= on_date <= window["end"]
        ]
        if not matches:
            return None
        window = max(matches, key=lambda item: item["event_date"])
        fac_mat = (window["facilitator"].user_lan_id or "").strip().lower() or (
            f"id:{window['facilitator'].pk}"
        )
        key = f"{matricula}|fac|{fac_mat}"
        return key if key in metrics else None

    aud_fields = ["matricula", "protocolo", date_field_aud]
    for row in aud_qs.exclude(matricula="").values(*aud_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_aud)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        key = _bucket_for(matricula, on_date)
        if not key:
            continue
        bucket = metrics[key]
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_auditados"].add(protocolo)
        else:
            bucket["auditados"] += 1

    fal_fields = list(_WEIGHT_FIELDS) + [date_field_fal]
    # date_field_fal may already be "data"; avoid duplicates
    fal_fields = list(dict.fromkeys(fal_fields))
    for row in fal_qs.exclude(matricula="").values(*fal_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_fal)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        key = _bucket_for(matricula, on_date)
        if not key:
            continue
        bucket = metrics[key]
        weight = failure_weight(row)
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        if label == "Crítica":
            bucket["falhas_criticas"] += 1
        elif label == "Não Crítica":
            bucket["falhas_nao_criticas"] += 1
        elif label == "Procedimento":
            bucket["falhas_procedimento"] += 1

        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_falhas"].add(protocolo)
            prev = bucket["protocol_weights"].get(protocolo, 0.0)
            bucket["protocol_weights"][protocolo] = max(prev, weight)
        else:
            bucket["falhas"] += 1
            bucket["impacto_ponderado"] += weight

    items: list[dict[str, Any]] = []
    for key, meta in row_meta.items():
        bucket = metrics[key]
        if grain == GRAIN_PROTOCOLO:
            a = len(bucket["protocol_auditados"])
            f = len(bucket["protocol_falhas"])
            weighted = round(sum(bucket["protocol_weights"].values()), 1)
        else:
            a = int(bucket["auditados"])
            f = int(bucket["falhas"])
            weighted = round(float(bucket["impacto_ponderado"]), 1)
        if a <= 0 and f <= 0:
            continue
        matricula = meta["matricula"]
        items.append(
            {
                "key": key,
                "label": meta.get("agente") or matricula,
                "matricula": matricula,
                "lider": meta["lider"],
                "time": meta.get("time") or "",
                "responsabilidade": "facilitador",
                "regra_responsabilidade": meta.get("regra_responsabilidade"),
                "facilitador_matricula": meta.get("facilitador_matricula"),
                "responsibility_periods": meta.get("responsibility_periods") or [],
                "auditados": a,
                "falhas": f,
                "eo_pct": _eo_pct(a, f),
                "impacto_ponderado": weighted,
                "eo_ponderado_pct": _eo_pct(a, weighted),
                "falha_pct": round(100.0 * f / a, 1) if a else None,
                "falhas_criticas": int(bucket["falhas_criticas"]),
                "falhas_nao_criticas": int(bucket["falhas_nao_criticas"]),
                "falhas_procedimento": int(bucket["falhas_procedimento"]),
            }
        )
    return items


def _build_facilitator_hierarchy_rows(
    params,
    *,
    assignments: dict[str, dict[str, Any]],
    aud_qs: QuerySet,
    fal_qs: QuerySet,
    grain: str,
) -> list[dict[str, Any]]:
    """Uma linha por (agente, facilitador, janela), métricas só da vigência."""
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    date_field_aud = date_field_auditados(params)
    date_field_fal = date_field_falhas(params)
    index = build_responsibility_index()

    row_meta: dict[str, dict[str, Any]] = {}
    for matricula, fallback in assignments.items():
        for fac_row in facilitator_window_assignment_rows(
            index, matricula, start, end, fallback
        ):
            row_meta[fac_row["key"]] = fac_row

    if not row_meta:
        return []

    row_keys_by_matricula: dict[str, list[str]] = defaultdict(list)
    for key, meta in row_meta.items():
        row_keys_by_matricula[meta.get("matricula") or ""].append(key)

    def _empty_metrics_bucket() -> dict[str, Any]:
        return {
            "auditados": 0,
            "falhas": 0,
            "impacto_ponderado": 0.0,
            "falhas_criticas": 0,
            "falhas_nao_criticas": 0,
            "falhas_procedimento": 0,
            "protocol_weights": {},
            "protocol_auditados": set(),
            "protocol_falhas": set(),
        }

    metrics: dict[str, dict[str, Any]] = {
        key: _empty_metrics_bucket() for key in row_meta
    }

    def _bucket_for(matricula: str, on_date: date | None) -> str | None:
        if not on_date:
            return None
        for key in row_keys_by_matricula.get(matricula, ()):
            meta = row_meta[key]
            window_start = meta.get("window_start")
            window_end = meta.get("window_end")
            if (
                window_start
                and window_end
                and window_start <= on_date <= window_end
            ):
                return key
        return None

    aud_fields = ["matricula", "protocolo", date_field_aud]
    for row in aud_qs.exclude(matricula="").values(*aud_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_aud)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        key = _bucket_for(matricula, on_date)
        if not key:
            continue
        bucket = metrics[key]
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_auditados"].add(protocolo)
        else:
            bucket["auditados"] += 1

    fal_fields = list(_WEIGHT_FIELDS) + [date_field_fal]
    fal_fields = list(dict.fromkeys(fal_fields))
    for row in fal_qs.exclude(matricula="").values(*fal_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_fal)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        key = _bucket_for(matricula, on_date)
        if not key:
            continue
        bucket = metrics[key]
        weight = failure_weight(row)
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        if label == "Crítica":
            bucket["falhas_criticas"] += 1
        elif label == "Não Crítica":
            bucket["falhas_nao_criticas"] += 1
        elif label == "Procedimento":
            bucket["falhas_procedimento"] += 1
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_falhas"].add(protocolo)
            prev = bucket["protocol_weights"].get(protocolo, 0.0)
            bucket["protocol_weights"][protocolo] = max(prev, weight)
        else:
            bucket["falhas"] += 1
            bucket["impacto_ponderado"] += weight

    def _metrics_to_row(bucket: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any] | None:
        if grain == GRAIN_PROTOCOLO:
            a = len(bucket["protocol_auditados"])
            f = len(bucket["protocol_falhas"])
            weighted = round(sum(bucket["protocol_weights"].values()), 1)
        else:
            a = int(bucket["auditados"])
            f = int(bucket["falhas"])
            weighted = round(float(bucket["impacto_ponderado"]), 1)
        if a <= 0 and f <= 0:
            return None
        return {
            "key": meta["key"],
            "label": meta.get("agente") or meta.get("matricula") or "",
            "matricula": meta.get("matricula") or "",
            "lider": meta["lider"],
            "lider_matricula": meta.get("lider_matricula"),
            "time": meta.get("time") or "",
            "responsabilidade": "facilitador",
            "regra_responsabilidade": meta.get("regra_responsabilidade"),
            "facilitador_matricula": meta.get("facilitador_matricula"),
            "vigencia_inicio": meta.get("vigencia_inicio"),
            "vigencia_fim": meta.get("vigencia_fim"),
            "auditados": a,
            "falhas": f,
            "eo_pct": _eo_pct(a, f),
            "impacto_ponderado": weighted,
            "eo_ponderado_pct": _eo_pct(a, weighted),
            "falha_pct": round(100.0 * f / a, 1) if a else None,
            "falhas_criticas": int(bucket["falhas_criticas"]),
            "falhas_nao_criticas": int(bucket["falhas_nao_criticas"]),
            "falhas_procedimento": int(bucket["falhas_procedimento"]),
        }

    items: list[dict[str, Any]] = []
    for key, meta in row_meta.items():
        row = _metrics_to_row(metrics[key], meta)
        if row:
            items.append(row)
    return items


def _build_leader_hierarchy_rows(
    params,
    *,
    assignments: dict[str, dict[str, Any]],
    aud_qs: QuerySet,
    fal_qs: QuerySet,
    grain: str,
) -> list[dict[str, Any]]:
    """Uma linha por (agente, líder, vigência), métricas só da janela — fora facilitador."""
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    date_field_aud = date_field_auditados(params)
    date_field_fal = date_field_falhas(params)
    fac_index = build_responsibility_index()
    histories_by_mat = build_leader_history_index(start, end)

    row_meta: dict[str, dict[str, Any]] = {}
    for matricula, fallback in assignments.items():
        for leader_row in leader_assignment_rows(
            matricula, start, end, fallback, histories_by_mat=histories_by_mat
        ):
            row_meta[leader_row["key"]] = leader_row

    if not row_meta:
        return []

    row_keys_by_matricula: dict[str, list[str]] = defaultdict(list)
    for key, meta in row_meta.items():
        row_keys_by_matricula[meta.get("matricula") or ""].append(key)

    def _empty_metrics_bucket() -> dict[str, Any]:
        return {
            "auditados": 0,
            "falhas": 0,
            "impacto_ponderado": 0.0,
            "falhas_criticas": 0,
            "falhas_nao_criticas": 0,
            "falhas_procedimento": 0,
            "protocol_weights": {},
            "protocol_auditados": set(),
            "protocol_falhas": set(),
        }

    metrics: dict[str, dict[str, Any]] = {
        key: _empty_metrics_bucket() for key in row_meta
    }
    unattributed = _empty_metrics_bucket()

    def _is_facilitator_window(matricula: str, on_date: date | None) -> bool:
        return bool(on_date and date_in_windows(on_date, fac_index.get(matricula, [])))

    def _accumulate_unattributed(row: dict[str, Any], on_date: date | None, *, is_auditado: bool) -> None:
        matricula = (row.get("matricula") or "").strip().lower()
        if _is_facilitator_window(matricula, on_date):
            return
        bucket = unattributed
        if is_auditado:
            if grain == GRAIN_PROTOCOLO:
                protocolo = str(row.get("protocolo") or "").strip()
                if protocolo:
                    bucket["protocol_auditados"].add(protocolo)
            else:
                bucket["auditados"] += 1
            return
        weight = failure_weight(row)
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        if label == "Crítica":
            bucket["falhas_criticas"] += 1
        elif label == "Não Crítica":
            bucket["falhas_nao_criticas"] += 1
        elif label == "Procedimento":
            bucket["falhas_procedimento"] += 1
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                return
            bucket["protocol_falhas"].add(protocolo)
            prev = bucket["protocol_weights"].get(protocolo, 0.0)
            bucket["protocol_weights"][protocolo] = max(prev, weight)
        else:
            bucket["falhas"] += 1
            bucket["impacto_ponderado"] += weight

    def _bucket_for(matricula: str, on_date: date | None) -> str | None:
        if not on_date:
            return None
        if date_in_windows(on_date, fac_index.get(matricula, [])):
            return None
        candidate_keys = row_keys_by_matricula.get(matricula, ())
        for key in candidate_keys:
            meta = row_meta[key]
            window_start = meta.get("window_start")
            window_end = meta.get("window_end")
            if window_start and window_end and window_start <= on_date <= window_end:
                return key
        resolved = resolve_leader_for_date(
            matricula,
            on_date,
            histories_by_mat=histories_by_mat,
            period_start=start,
            period_end=end,
        )
        if not resolved:
            return None
        lider_mat = resolved.get("lider_matricula") or ""
        for key in candidate_keys:
            meta = row_meta[key]
            if (meta.get("lider_matricula") or "") != lider_mat:
                continue
            window_start = meta.get("window_start")
            window_end = meta.get("window_end")
            if window_start and window_end and window_start <= on_date <= window_end:
                return key
        return None

    aud_fields = ["matricula", "protocolo", date_field_aud]
    for row in aud_qs.exclude(matricula="").values(*aud_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_aud)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        key = _bucket_for(matricula, on_date)
        if not key:
            _accumulate_unattributed(row, on_date, is_auditado=True)
            continue
        bucket = metrics[key]
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_auditados"].add(protocolo)
        else:
            bucket["auditados"] += 1

    fal_fields = list(_WEIGHT_FIELDS) + [date_field_fal]
    fal_fields = list(dict.fromkeys(fal_fields))
    for row in fal_qs.exclude(matricula="").values(*fal_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_fal)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        key = _bucket_for(matricula, on_date)
        if not key:
            _accumulate_unattributed(row, on_date, is_auditado=False)
            continue
        bucket = metrics[key]
        weight = failure_weight(row)
        label = criticidade_label(
            row.get("categoria_falha"),
            id_cliente=row.get("id_cliente"),
            tipo_registro=row.get("tipo_registro"),
        )
        if label == "Crítica":
            bucket["falhas_criticas"] += 1
        elif label == "Não Crítica":
            bucket["falhas_nao_criticas"] += 1
        elif label == "Procedimento":
            bucket["falhas_procedimento"] += 1

        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_falhas"].add(protocolo)
            prev = bucket["protocol_weights"].get(protocolo, 0.0)
            bucket["protocol_weights"][protocolo] = max(prev, weight)
        else:
            bucket["falhas"] += 1
            bucket["impacto_ponderado"] += weight

    def _metrics_to_row(bucket: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any] | None:
        if grain == GRAIN_PROTOCOLO:
            a = len(bucket["protocol_auditados"])
            f = len(bucket["protocol_falhas"])
            weighted = round(sum(bucket["protocol_weights"].values()), 1)
        else:
            a = int(bucket["auditados"])
            f = int(bucket["falhas"])
            weighted = round(float(bucket["impacto_ponderado"]), 1)
        if a <= 0 and f <= 0:
            return None
        return {
            "key": meta["key"],
            "label": meta.get("agente") or meta.get("label") or meta.get("matricula") or "",
            "matricula": meta.get("matricula") or "",
            "lider": meta["lider"],
            "lider_matricula": meta.get("lider_matricula"),
            "time": meta.get("time") or "",
            "responsabilidade": meta.get("responsabilidade") or "lider",
            "vigencia_inicio": meta.get("vigencia_inicio"),
            "vigencia_fim": meta.get("vigencia_fim"),
            "auditados": a,
            "falhas": f,
            "eo_pct": _eo_pct(a, f),
            "impacto_ponderado": weighted,
            "eo_ponderado_pct": _eo_pct(a, weighted),
            "falha_pct": round(100.0 * f / a, 1) if a else None,
            "falhas_criticas": int(bucket["falhas_criticas"]),
            "falhas_nao_criticas": int(bucket["falhas_nao_criticas"]),
            "falhas_procedimento": int(bucket["falhas_procedimento"]),
        }

    items: list[dict[str, Any]] = []
    for key, meta in row_meta.items():
        row = _metrics_to_row(metrics[key], meta)
        if row:
            items.append(row)

    unattributed_row = _metrics_to_row(
        unattributed,
        {
            "key": LEADER_TEMPORAL_UNATTRIBUTED_KEY,
            "label": LEADER_TEMPORAL_UNATTRIBUTED_LABEL,
            "matricula": "",
            "lider": LEADER_TEMPORAL_UNATTRIBUTED_LABEL,
            "lider_matricula": "",
            "time": "",
            "responsabilidade": "lider",
            "vigencia_inicio": None,
            "vigencia_fim": None,
        },
    )
    if unattributed_row:
        items.append(unattributed_row)
    return items


WORKFORCE_DIMENSION_EMPTY = {
    "localidade_hc": "Sem localidade",
    "turno": "Sem turno",
}
MAX_WORKFORCE_DIMENSION_ROWS = 8


def _workforce_dimension_label(history, dimension: str) -> str:
    from apps.workforce.services.journey_shift import resolve_journey_shift

    if dimension == "localidade_hc":
        return (history.location or "").strip() or WORKFORCE_DIMENSION_EMPTY["localidade_hc"]
    shift = (history.journey_shift or "").strip()
    if not shift:
        shift = resolve_journey_shift(history.journey or "")
    return shift or WORKFORCE_DIMENSION_EMPTY["turno"]


def _empty_workforce_dimension_bucket() -> dict[str, Any]:
    return {
        "label": "",
        "auditados": 0,
        "falhas": 0,
        "impacto_ponderado": 0.0,
        "protocol_auditados": set(),
        "protocol_falhas": set(),
        "protocol_weights": {},
    }


def _resolve_workforce_dimension_key(
    matricula: str,
    on_date: date | None,
    *,
    dimension: str,
    fac_index: dict[str, list],
    histories_by_mat: dict[str, list],
    period_start: date | None,
    period_end: date | None,
) -> tuple[str, str] | None:
    if not on_date:
        return None
    matricula = (matricula or "").strip().lower()
    if not matricula or date_in_windows(on_date, fac_index.get(matricula, [])):
        return None
    history = resolve_history_for_date(
        matricula,
        on_date,
        histories_by_mat=histories_by_mat,
        period_start=period_start,
        period_end=period_end,
    )
    if not history:
        return None
    label = _workforce_dimension_label(history, dimension)
    return label.casefold(), label


def _workforce_dimension_bucket_to_row(
    key: str, bucket: dict[str, Any], *, grain: str
) -> dict[str, Any] | None:
    if grain == GRAIN_PROTOCOLO:
        auditados = len(bucket["protocol_auditados"])
        falhas = len(bucket["protocol_falhas"])
        impacto = round(sum(bucket["protocol_weights"].values()), 1)
    else:
        auditados = int(bucket["auditados"])
        falhas = int(bucket["falhas"])
        impacto = round(float(bucket["impacto_ponderado"]), 1)
    if auditados <= 0 and falhas <= 0:
        return None
    label = bucket.get("label") or key
    return {
        "key": key,
        "label": label,
        "auditados": auditados,
        "falhas": falhas,
        "impacto_ponderado": impacto,
        "eo_ponderado_pct": _eo_pct(auditados, impacto),
    }


def _cap_workforce_dimension_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) <= MAX_WORKFORCE_DIMENSION_ROWS:
        return rows
    head = rows[:MAX_WORKFORCE_DIMENSION_ROWS]
    tail = rows[MAX_WORKFORCE_DIMENSION_ROWS:]
    outros_auditados = sum(int(r["auditados"] or 0) for r in tail)
    outros_falhas = sum(int(r["falhas"] or 0) for r in tail)
    outros_impacto = round(
        sum(float(r["impacto_ponderado"] or 0.0) for r in tail),
        1,
    )
    if outros_auditados <= 0 and outros_falhas <= 0:
        return head
    head.append(
        {
            "key": "__outros__",
            "label": "Outros",
            "auditados": outros_auditados,
            "falhas": outros_falhas,
            "impacto_ponderado": outros_impacto,
            "eo_ponderado_pct": _eo_pct(outros_auditados, outros_impacto),
        }
    )
    return head


def build_workforce_dimension_breakdown(
    params,
    *,
    aud_qs: QuerySet,
    fal_qs: QuerySet,
    dimension: str,
) -> list[dict[str, Any]]:
    """EO/volume por localidade HC ou turno (AgentHistory temporal na data do evento)."""
    if dimension not in WORKFORCE_DIMENSION_EMPTY:
        return []
    grain = resolve_grain(params)
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    date_field_aud = date_field_auditados(params)
    date_field_fal = date_field_falhas(params)
    fac_index = build_responsibility_index()
    histories_by_mat = build_leader_history_index(start, end)
    metrics: dict[str, dict[str, Any]] = defaultdict(_empty_workforce_dimension_bucket)

    aud_fields = ["matricula", "protocolo", date_field_aud]
    for row in aud_qs.exclude(matricula="").values(*aud_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_aud)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        resolved = _resolve_workforce_dimension_key(
            matricula,
            on_date,
            dimension=dimension,
            fac_index=fac_index,
            histories_by_mat=histories_by_mat,
            period_start=start,
            period_end=end,
        )
        if not resolved:
            continue
        key, label = resolved
        bucket = metrics[key]
        bucket["label"] = label
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if protocolo:
                bucket["protocol_auditados"].add(protocolo)
        else:
            bucket["auditados"] += 1

    fal_fields = list(_WEIGHT_FIELDS) + [date_field_fal]
    fal_fields = list(dict.fromkeys(fal_fields))
    for row in fal_qs.exclude(matricula="").values(*fal_fields).iterator(chunk_size=4000):
        matricula = (row.get("matricula") or "").strip().lower()
        on_date = row.get(date_field_fal)
        if isinstance(on_date, str):
            on_date = _parse_date(on_date)
        resolved = _resolve_workforce_dimension_key(
            matricula,
            on_date,
            dimension=dimension,
            fac_index=fac_index,
            histories_by_mat=histories_by_mat,
            period_start=start,
            period_end=end,
        )
        if not resolved:
            continue
        key, label = resolved
        bucket = metrics[key]
        bucket["label"] = label
        weight = failure_weight(row)
        if grain == GRAIN_PROTOCOLO:
            protocolo = str(row.get("protocolo") or "").strip()
            if not protocolo:
                continue
            bucket["protocol_falhas"].add(protocolo)
            prev = bucket["protocol_weights"].get(protocolo, 0.0)
            bucket["protocol_weights"][protocolo] = max(prev, weight)
        else:
            bucket["falhas"] += 1
            bucket["impacto_ponderado"] += weight

    rows: list[dict[str, Any]] = []
    for key, bucket in metrics.items():
        item = _workforce_dimension_bucket_to_row(key, bucket, grain=grain)
        if item:
            rows.append(item)
    rows.sort(key=lambda item: (-int(item["auditados"]), item["label"].casefold()))
    return _cap_workforce_dimension_rows(rows)


def build_leader_hierarchy(
    params,
    *,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Segmentos temporais agente×líder para hierarquia (fora janelas de facilitador)."""
    from apps.qualidade_operacional.services.contestacao_metrics import (
        resolve_population_qs,
    )

    grain = resolve_grain(params)
    scoped_params = _operational_ranking_params(params)
    scoped_params["responsibility_scope"] = "lider"
    assignments = workforce_assignments(scoped_params)
    if aud_qs is None or fal_qs is None:
        def_aud, def_fal = resolve_population_qs(scoped_params, scope=scope)
        aud_qs = aud_qs if aud_qs is not None else def_aud
        fal_qs = fal_qs if fal_qs is not None else def_fal
    fal_qs = exclude_processual_agent_links(fal_qs)
    results = _build_leader_hierarchy_rows(
        scoped_params,
        assignments=assignments,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        grain=grain,
    )
    return {"ok": True, "grain": grain, "count": len(results), "results": results}


def build_facilitator_hierarchy(
    params,
    *,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Segmentos temporais agente×facilitador para hierarquia dedicada."""
    from apps.qualidade_operacional.services.contestacao_metrics import (
        resolve_population_qs,
    )

    grain = resolve_grain(params)
    scoped_params = _operational_ranking_params(params)
    scoped_params["responsibility_scope"] = "facilitador"
    assignments = workforce_assignments(scoped_params)
    if aud_qs is None or fal_qs is None:
        def_aud, def_fal = resolve_population_qs(scoped_params, scope=scope)
        aud_qs = aud_qs if aud_qs is not None else def_aud
        fal_qs = fal_qs if fal_qs is not None else def_fal
    fal_qs = exclude_processual_agent_links(fal_qs)
    results = _build_facilitator_hierarchy_rows(
        scoped_params,
        assignments=assignments,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        grain=grain,
    )
    return {"ok": True, "grain": grain, "count": len(results), "results": results}


def build_ranking(
    params,
    *,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Ranking operacional restrito ao AgentHistory, com pior EO no topo."""
    from apps.qualidade_operacional.services.contestacao_metrics import (
        resolve_population_qs,
    )

    grain = resolve_grain(params)
    by = (params.get("by") or "agente").strip().lower()
    if by not in {"agente", "lider"}:
        by = "agente"

    scoped_params = _operational_ranking_params(params)
    responsibility_scope = (params.get("responsibility_scope") or "all").strip().lower()
    if responsibility_scope not in {"all", "lider", "facilitador"}:
        responsibility_scope = "all"
    scoped_params["responsibility_scope"] = responsibility_scope
    assignments = workforce_assignments(scoped_params)
    if aud_qs is None or fal_qs is None:
        def_aud, def_fal = resolve_population_qs(scoped_params, scope=scope)
        aud_qs = aud_qs if aud_qs is not None else def_aud
        fal_qs = fal_qs if fal_qs is not None else def_fal
    # Chamadores podem fornecer uma queryset já montada; a invariável de
    # atribuição continua valendo nesse caminho.
    fal_qs = exclude_processual_agent_links(fal_qs)
    # resolve_population_qs -> apply_common_filters já aplicou exatamente este
    # escopo temporal. Reaplicá-lo aqui só duplicava o mesmo OR/NOT no SQL.
    aud_map = _operational_counts_by_matricula(aud_qs, grain)
    fal_map = _operational_counts_by_matricula(fal_qs, grain)
    weighted_map = _weighted_failures_by_field(fal_qs, grain, "matricula")
    crit_map = _criticidade_counts_by_matricula(fal_qs)
    data_matriculas = set(aud_map) | set(fal_map)

    items: list[dict[str, Any]] = []
    if by == "lider":
        hierarchy_rows = _build_leader_hierarchy_rows(
            scoped_params,
            assignments=assignments,
            aud_qs=aud_qs,
            fal_qs=fal_qs,
            grain=grain,
        )
        leaders: dict[str, dict[str, Any]] = {}
        for row in hierarchy_rows:
            leader_key = row.get("lider_matricula") or row.get("lider") or "Sem líder"
            bucket = leaders.setdefault(
                leader_key,
                {
                    "key": leader_key,
                    "label": row.get("lider") or "Sem líder",
                    "auditados": 0,
                    "falhas": 0,
                    "impacto_ponderado": 0.0,
                    "falhas_criticas": 0,
                    "falhas_nao_criticas": 0,
                    "falhas_procedimento": 0,
                    "matriculas": set(),
                    "impactos": defaultdict(int),
                },
            )
            bucket["auditados"] += int(row.get("auditados") or 0)
            bucket["falhas"] += int(row.get("falhas") or 0)
            bucket["impacto_ponderado"] += float(row.get("impacto_ponderado") or 0.0)
            bucket["falhas_criticas"] += int(row.get("falhas_criticas") or 0)
            bucket["falhas_nao_criticas"] += int(row.get("falhas_nao_criticas") or 0)
            bucket["falhas_procedimento"] += int(row.get("falhas_procedimento") or 0)
            if row.get("matricula"):
                bucket["matriculas"].add(row["matricula"])

        if grain == GRAIN_PROTOCOLO:
            fac_index = build_responsibility_index()
            histories_by_mat = build_leader_history_index(
                _parse_date(scoped_params.get("start_date")),
                _parse_date(scoped_params.get("end_date")),
            )
            date_field_fal = date_field_falhas(scoped_params)
            for impact in (
                fal_qs.exclude(protocolo="")
                .exclude(matricula="")
                .values("matricula", "etapa", "protocolo", date_field_fal)
            ):
                matricula = (impact["matricula"] or "").strip().lower()
                on_date = impact.get(date_field_fal)
                if isinstance(on_date, str):
                    on_date = _parse_date(on_date)
                if not on_date or date_in_windows(on_date, fac_index.get(matricula, [])):
                    continue
                resolved = resolve_leader_for_date(
                    matricula,
                    on_date,
                    histories_by_mat=histories_by_mat,
                )
                if not resolved:
                    continue
                leader_key = resolved.get("lider_matricula") or resolved.get("lider")
                if leader_key in leaders:
                    etapa = (impact["etapa"] or "Sem etapa").strip() or "Sem etapa"
                    leaders[leader_key]["impactos"][etapa] += 1
        else:
            for impact in fal_qs.exclude(matricula="").values("matricula", "etapa").annotate(
                c=Count("id")
            ):
                matricula = (impact["matricula"] or "").strip().lower()
                # Atribuição por etapa usa líder predominante no período filtrado
                assignment = assignments.get(matricula)
                if not assignment or assignment.get("responsabilidade") == "facilitador":
                    continue
                leader_key = assignment.get("lider_matricula") or assignment.get("lider")
                if leader_key in leaders:
                    etapa = (impact["etapa"] or "Sem etapa").strip() or "Sem etapa"
                    leaders[leader_key]["impactos"][etapa] += int(impact["c"])

        for bucket in leaders.values():
            a = int(bucket["auditados"])
            f = int(bucket["falhas"])
            weighted = round(float(bucket["impacto_ponderado"]), 1)
            top_impacto = max(bucket["impactos"].items(), key=lambda item: item[1], default=("—", 0))
            items.append(
                {
                    "key": bucket["key"],
                    "label": bucket["label"],
                    "auditados": a,
                    "falhas": f,
                    "impacto_ponderado": weighted,
                    "eo_ponderado_pct": _eo_pct(a, weighted),
                    "eo_pct": _eo_pct(a, f),
                    "falha_pct": round(100.0 * f / a, 1) if a else None,
                    "agentes": len(bucket["matriculas"]),
                    "impacto_etapa": top_impacto[0],
                    "impacto_falhas": top_impacto[1],
                    "falhas_criticas": int(bucket["falhas_criticas"]),
                    "falhas_nao_criticas": int(bucket["falhas_nao_criticas"]),
                    "falhas_procedimento": int(bucket["falhas_procedimento"]),
                }
            )
    else:
        if responsibility_scope == "facilitador":
            items.extend(
                _build_facilitator_agent_rows(
                    params,
                    assignments=assignments,
                    aud_qs=aud_qs,
                    fal_qs=fal_qs,
                    grain=grain,
                )
            )
        else:
            for matricula in data_matriculas:
                assignment = assignments.get(matricula)
                if not assignment:
                    continue
                a = aud_map.get(matricula, 0)
                f = fal_map.get(matricula, 0)
                weighted = weighted_map.get(matricula, 0.0)
                crit = crit_map.get(matricula) or {}
                items.append(
                    {
                        "key": matricula,
                        "label": assignment["agente"] or matricula,
                        "matricula": matricula,
                        "lider": assignment["lider"],
                        "time": assignment["time"],
                        "responsabilidade": assignment.get("responsabilidade", "lider"),
                        "regra_responsabilidade": assignment.get("regra_responsabilidade"),
                        "facilitador_matricula": assignment.get("facilitador_matricula")
                        or assignment.get("lider_matricula"),
                        "responsibility_periods": assignment.get("responsibility_periods")
                        or [],
                        "auditados": a,
                        "falhas": f,
                        "eo_pct": _eo_pct(a, f),
                        "impacto_ponderado": weighted,
                        "eo_ponderado_pct": _eo_pct(a, weighted),
                        "falha_pct": round(100.0 * f / a, 1) if a else None,
                        "falhas_criticas": int(crit.get("falhas_criticas") or 0),
                        "falhas_nao_criticas": int(crit.get("falhas_nao_criticas") or 0),
                        "falhas_procedimento": int(crit.get("falhas_procedimento") or 0),
                    }
                )

    ranked_best = sorted(
        [item for item in items if item["eo_ponderado_pct"] is not None],
        key=lambda item: (item["eo_ponderado_pct"], -item["impacto_ponderado"]),
        reverse=True,
    )
    for index, row in enumerate(ranked_best):
        row["quartil"] = _quartile_label(index, len(ranked_best))

    ranked = sorted(
        ranked_best,
        key=lambda item: (
            item["eo_ponderado_pct"],
            -item["impacto_ponderado"],
            item["label"].casefold(),
        ),
    )
    for index, row in enumerate(ranked):
        row["rank"] = index + 1

    no_eo = [item for item in items if item["eo_ponderado_pct"] is None]
    for row in no_eo:
        row["quartil"] = None
        row["rank"] = None
    results = ranked + sorted(no_eo, key=lambda item: item["label"].casefold())

    quartil_summary = {
        q: {
            "quartil": q,
            "agentes": 0,
            "falhas": 0,
            "impacto_ponderado": 0.0,
            "auditados": 0,
            "eo_pct": None,
            "eo_ponderado_pct": None,
        }
        for q in ("Q1", "Q2", "Q3", "Q4")
    }
    for row in ranked_best:
        bucket = quartil_summary[row["quartil"]]
        bucket["agentes"] += 1
        bucket["falhas"] += row["falhas"]
        bucket["impacto_ponderado"] += row["impacto_ponderado"]
        bucket["auditados"] += row["auditados"]
    for bucket in quartil_summary.values():
        bucket["eo_pct"] = _eo_pct(bucket["auditados"], bucket["falhas"])
        bucket["impacto_ponderado"] = round(bucket["impacto_ponderado"], 1)
        bucket["eo_ponderado_pct"] = _eo_pct(
            bucket["auditados"], bucket["impacto_ponderado"]
        )
    current_aud = None if grain == GRAIN_PROTOCOLO else sum(aud_map.values())
    current_fal = None if grain == GRAIN_PROTOCOLO else sum(fal_map.values())
    kpis = _build_kpis(
        scoped_params,
        aud_qs=aud_qs,
        fal_qs=fal_qs,
        auditados=current_aud,
        falhas=current_fal,
    )
    m1 = kpis.get("previous") or {}
    raw_limit = params.get("limit") if hasattr(params, "get") else None
    try:
        limit = min(5000, max(1, int(raw_limit))) if raw_limit else len(results)
    except (TypeError, ValueError):
        limit = len(results)

    return {
        "ok": True,
        "grain": grain,
        "by": by,
        "count": len(results),
        "eligible_agents": len(assignments),
        "results": results[:limit],
        "quartis": [quartil_summary[q] for q in ("Q1", "Q2", "Q3", "Q4")],
        "m1": {
            "eo_pct": m1.get("eo_pct"),
            "delta_pp": m1.get("delta_pp"),
            "auditados": m1.get("auditados"),
            "falhas": m1.get("falhas"),
            "impacto_ponderado": m1.get("impacto_ponderado"),
            "eo_ponderado_pct": m1.get("eo_ponderado_pct"),
        },
        "summary": {
            "auditados": kpis["auditados"],
            "falhas": kpis["falhas"],
            "eo_pct": kpis["eo_pct"],
            "impacto_ponderado": kpis["impacto_ponderado"],
            "eo_ponderado_pct": kpis["eo_ponderado_pct"],
        },
        "kpis": kpis,
    }
