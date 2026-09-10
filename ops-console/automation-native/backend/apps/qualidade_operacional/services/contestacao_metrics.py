# -*- coding: utf-8 -*-
"""Métricas de contestação a partir das bases Qualidade (auditados/falhas).

Fonte de verdade (paridade Quality Overview / report BRB):
- **Contestados** = auditados com ``tipo_analise`` Contestação* no recorte de
  ``data_recepcao_contestacao`` (fallback ``data``).
- **Decisão (procedente/improcedente)** = derivação caso a caso
  (``protocolo + matrícula``): ``procedencia`` do auditado ou da falha; se
  houver falha de contestação, ``tipo_falha``; senão acerto.
- **Procedentes** = casos com classificação ``falha`` (CONFORME = Não).
"""
from __future__ import annotations

from calendar import month_name
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Iterable

from django.db import ProgrammingError, transaction
from django.db.models import Count, Q, QuerySet

from apps.qualidade_operacional.services.analytics import (
    filtered_auditados,
    filtered_falhas,
    resolve_date_range,
    shift_period,
)
from apps.qualidade_operacional.services.criticidade import (
    EXCLUDED_DEFAULT_REPORT_CLIENTE_IDS,
)
from apps.qualidade_operacional.services.queries import (
    date_field_auditados,
    date_field_falhas,
    param_list,
)

_PROTOCOLO_IN_CHUNK = 2000


@lru_cache(maxsize=1)
def _contestacao_tipo_analise_values() -> tuple[str, ...]:
    """Tipos de análise de contestação a partir do catálogo (equivalente a ILIKE %contest%).

    Usa ``tipo_analise__in`` (indexável) em vez de ``icontains``, com fallback
    vazio → caller usa ``icontains``. Cache de processo; ``bump`` de catálogo
    reinicia workers ou o lru é invalidado via ``clear_contestacao_tipo_cache``.
    """
    try:
        from apps.qualidade_operacional.services.filter_catalog import catalog_values

        values = catalog_values().get("tipo_analise") or []
    except Exception:  # noqa: BLE001 — catálogo indisponível
        return ()
    matched = tuple(
        v for v in values if v and "contest" in str(v).casefold()
    )
    return matched


def clear_contestacao_tipo_cache() -> None:
    _contestacao_tipo_analise_values.cache_clear()


def contestacao_falha_q() -> Q:
    """Filtro ORM: falha pertencente a análise/módulo de contestação."""
    tipos = _contestacao_tipo_analise_values()
    if tipos:
        # O catálogo é apenas uma otimização e pode ficar defasado durante
        # um import. Preserve subtipos novos sem depender da invalidação do LRU.
        return (
            Q(tipo_analise__in=tipos)
            | Q(tipo_analise__icontains="contest")
            | Q(modulo__icontains="contest")
        )
    return Q(tipo_analise__icontains="contest") | Q(modulo__icontains="contest")


def contestacao_auditado_q() -> Q:
    """Filtro ORM: auditado pertencente a tipo de análise de contestação."""
    tipos = _contestacao_tipo_analise_values()
    if tipos:
        # O catálogo acelera os valores conhecidos, mas imports podem trazer
        # novos subtipos antes da atualização do catálogo.
        return Q(tipo_analise__in=tipos) | Q(tipo_analise__icontains="contest")
    return Q(tipo_analise__icontains="contest")


def contestacao_compliance_exclusion_q() -> Q:
    """Exclui Contestação Compliance — fluxo Claro Formalização (cliente 9999)."""
    return ~Q(tipo_analise__icontains="Compliance")


def contestacao_operacional_auditado_q() -> Q:
    """Contestação operacional (Indicadores EO / Quality Overview): exceto Compliance."""
    return contestacao_auditado_q() & contestacao_compliance_exclusion_q()


def contestacao_operacional_falha_q() -> Q:
    """Falhas de contestação operacional — exceto Compliance."""
    return contestacao_falha_q() & contestacao_compliance_exclusion_q()


def _period_label(start: date | None, end: date | None) -> str:
    """Rótulo do período de comparação (anterior ao intervalo atual)."""
    if not start or not end:
        return "período anterior"
    p_start, p_end = shift_period(start, end)
    if start.day == 1:
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = next_month - timedelta(days=1)
        if end == last:
            meses = {
                1: "jan.",
                2: "fev.",
                3: "mar.",
                4: "abr.",
                5: "mai.",
                6: "jun.",
                7: "jul.",
                8: "ago.",
                9: "set.",
                10: "out.",
                11: "nov.",
                12: "dez.",
            }
            return meses.get(p_start.month, month_name[p_start.month][:3].lower())
    if p_start.year == p_end.year:
        return f"{p_start.strftime('%d/%m')} a {p_end.strftime('%d/%m')}"
    return f"{p_start.strftime('%d/%m/%Y')} a {p_end.strftime('%d/%m/%Y')}"


def _params_with_dates(params, *, start: date | None, end: date | None):
    copied = params.copy() if hasattr(params, "copy") else dict(params)
    if start is not None:
        copied["start_date"] = start.isoformat()
    if end is not None:
        copied["end_date"] = end.isoformat()
    return copied


def _apply_default_report_cliente_exclusion(qs: QuerySet, params) -> QuerySet:
    """Fora do filtro explícito por cliente, exclui Formalização/GAQ do escopo operacional."""
    if param_list(params, "id_cliente"):
        return qs
    return qs.exclude(id_cliente__in=EXCLUDED_DEFAULT_REPORT_CLIENTE_IDS)


def _contestacao_falhas_qs(params) -> QuerySet:
    """Falhas de contestação no mesmo escopo/filtros/eixo de data do EO."""
    qs = filtered_falhas(params).filter(contestacao_operacional_falha_q())
    return _apply_default_report_cliente_exclusion(qs, params)


def _contestacao_auditados_qs(params) -> QuerySet:
    qs = filtered_auditados(params).filter(contestacao_operacional_auditado_q())
    return _apply_default_report_cliente_exclusion(qs, params)


def resolve_population_qs(
    params, *, scope: str | None = None
) -> tuple[QuerySet, QuerySet]:
    """Retorna (auditados_qs, falhas_qs) no escopo pedido.

    ``scope="contestacao"`` → apenas análises Contest*.
    Caso contrário → população EO filtrada (global do módulo).
    """
    if scope == "contestacao":
        return _contestacao_auditados_qs(params), _contestacao_falhas_qs(params)
    return filtered_auditados(params), filtered_falhas(params)


def _pct(part: float | int | None, total: float | int | None, digits: int = 1) -> float | None:
    if part is None or total is None or not total:
        return None
    return round(100.0 * float(part) / float(total), digits)


def _chunks(values: list[str], size: int = _PROTOCOLO_IN_CHUNK) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _atividade_protocolo_base_qs(protocolos: list[str]):
    """QS de protocolos de atividade, com filtro de tipo contestação se a coluna existir."""
    from apps.auditoria.models import AuditoriaAtividade, AuditoriaAtividadeProtocolo

    qs = AuditoriaAtividadeProtocolo.objects.filter(protocolo__in=protocolos)
    # ``atividade.tipo`` pode estar no model e ainda não no banco → ProgrammingError.
    try:
        with transaction.atomic():
            typed = qs.filter(atividade__tipo=AuditoriaAtividade.TIPO_CONTESTACAO)
            typed.exists()
            return typed
    except ProgrammingError:
        return qs


def resolve_procedencia(protocolos: set[str] | list[str]) -> dict[str, Any]:
    """Taxa de procedência via módulo de atividades (situacao procedente/improcedente).

    Regra: ``procedentes ÷ protocolos contestados`` (auditados de contestação).
    """
    prots = sorted({str(p).strip() for p in (protocolos or []) if str(p).strip()})
    if not prots:
        return {
            "ok": False,
            "available": False,
            "taxa_procedencia_pct": None,
            "procedentes": None,
            "improcedentes": None,
            "finalizadas": None,
            "contestados": 0,
            "message": "Sem protocolos contestados no filtro.",
            "rule": "procedentes ÷ protocolos contestados",
        }
    try:
        from apps.auditoria.models import AuditoriaAtividadeProtocolo

        finalizadas = 0
        procedentes = 0
        improcedentes = 0
        for chunk in _chunks(prots):
            qs = _atividade_protocolo_base_qs(chunk)
            with transaction.atomic():
                finalizadas += qs.exclude(situacao="").count()
                procedentes += qs.filter(
                    situacao=AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
                ).count()
                improcedentes += qs.filter(
                    situacao=AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE
                ).count()
        contestados = len(prots)
        return {
            "ok": True,
            "available": True,
            "taxa_procedencia_pct": _pct(procedentes, contestados),
            "procedentes": procedentes,
            "improcedentes": improcedentes,
            "finalizadas": finalizadas,
            "contestados": contestados,
            "message": None,
            "rule": "procedentes ÷ protocolos contestados",
        }
    except Exception as exc:  # noqa: BLE001 — ambiente sem migração/coluna
        return {
            "ok": False,
            "available": False,
            "taxa_procedencia_pct": None,
            "procedentes": None,
            "improcedentes": None,
            "finalizadas": None,
            "contestados": len(prots),
            "message": f"Procedência indisponível neste ambiente ({exc.__class__.__name__}).",
            "rule": "procedentes ÷ protocolos contestados",
        }


def _count_metrics(
    fal_qs: QuerySet,
    aud_qs: QuerySet,
    *,
    include_auditados_contestacao: bool = True,
    include_tipos: bool = False,
) -> dict[str, Any]:
    """Métricas de contestação.

    - ``protocolos_contestados`` / ``auditados_contestacao``: população contestada
      (auditados com tipo análise Contestação*).
    - ``eventos_contestacao`` / ``protocolos_com_falha_contestacao``: falhas nessa tipificação.

    Usa um único ``aggregate`` no lado auditados (COUNT + COUNT DISTINCT) para
    evitar dois full scans. ``tipos_analise`` é opcional (não usado no Resumo).
    """
    falhas_agg = fal_qs.aggregate(
        eventos_contestacao=Count("id"),
        protocolos_com_falha=Count(
            "protocolo", distinct=True, filter=~Q(protocolo="")
        ),
    )
    eventos = int(falhas_agg["eventos_contestacao"] or 0)
    protocolos_com_falha = int(falhas_agg["protocolos_com_falha"] or 0)

    if include_auditados_contestacao:
        agg = aud_qs.aggregate(
            auditados_contestacao=Count("id"),
            protocolos_contestados=Count(
                "protocolo", distinct=True, filter=~Q(protocolo="")
            ),
        )
        auditados_contestacao = int(agg["auditados_contestacao"] or 0)
        protocolos_contestados = int(agg["protocolos_contestados"] or 0)
    else:
        protocolos_contestados = (
            aud_qs.exclude(protocolo="").values("protocolo").distinct().count()
        )
        auditados_contestacao = 0

    tipos: list[str] = []
    if include_tipos:
        tipos = list(
            aud_qs.exclude(tipo_analise="")
            .values("tipo_analise")
            .annotate(c=Count("id"))
            .order_by("-c")
            .values_list("tipo_analise", flat=True)[:12]
        )
        if not tipos:
            tipos = list(
                fal_qs.exclude(tipo_analise="")
                .values("tipo_analise")
                .annotate(c=Count("id"))
                .order_by("-c")
                .values_list("tipo_analise", flat=True)[:12]
            )

    return {
        "protocolos_contestados": protocolos_contestados,
        "eventos_contestacao": eventos,
        "protocolos_com_falha_contestacao": protocolos_com_falha,
        "auditados_contestacao": auditados_contestacao,
        "protocolos_auditados_contestacao": protocolos_contestados,
        "tipos_analise": tipos,
    }


def build_contestacao_metrics(
    params,
    *,
    include_auditados_contestacao: bool | None = None,
    aud_qs: QuerySet | None = None,
    fal_qs: QuerySet | None = None,
) -> dict[str, Any]:
    """
    Contestações no período do dashboard EO.

    Regra temporal: mesmo ``date_axis`` das falhas/auditados do EO.

    Contagem (paridade Quality Overview):
    - protocolos_contestados = protocolos distintos em casos deduplicados
    - casos_contestacao = casos ``protocolo+matrícula`` após dedup
    - auditados_contestacao = linhas de auditados Contestação* no recorte
    - eventos_contestacao = linhas de falha contestação no escopo (batimento)
    - procedentes / improcedentes = casos com decisão derivada (procedencia/falha)
    - procedência = linhas de falha ÷ linhas auditadas de contestação

    ``include_auditados_contestacao``: se False, omite o COUNT de linhas
    (campo fica 0). Default True para paridade; o Resumo pode optar por False
    via query ``include_auditados_contestacao=0`` (economia no M-1).
    """
    if include_auditados_contestacao is None:
        raw = params.get("include_auditados_contestacao")
        if raw is None:
            include_auditados_contestacao = True
        else:
            include_auditados_contestacao = str(raw).strip().lower() not in {
                "0",
                "false",
                "no",
            }

    start, end = resolve_date_range(params)
    fal_date = date_field_falhas(params)
    aud_date = date_field_auditados(params)

    from apps.qualidade_operacional.services.contestacao_derivation import (
        compute_contestacao_case_metrics,
    )

    current = compute_contestacao_case_metrics(params, start=start, end=end)

    contestados = int(current["protocolos_contestados"] or 0)
    casos = int(current["casos_contestacao"] or 0)
    auditados_linhas = int(current["auditados_contestacao"] or 0)
    eventos = int(current["eventos_contestacao"] or 0)
    procedentes = eventos
    improcedentes = max(0, auditados_linhas - procedentes)
    taxa = _pct(procedentes, auditados_linhas)

    previous = {
        "protocolos_contestados": None,
        "casos_contestacao": None,
        "eventos_contestacao": None,
        "procedentes": None,
        "taxa_procedencia_pct": None,
        "delta_abs": None,
        "delta_pct": None,
        "label": "período anterior",
        "start_date": None,
        "end_date": None,
    }
    if start and end:
        p_start, p_end = shift_period(start, end)
        prev = compute_contestacao_case_metrics(
            _params_with_dates(params, start=p_start, end=p_end),
            start=p_start,
            end=p_end,
        )
        prev_contestados = int(prev["protocolos_contestados"] or 0)
        prev_casos = int(prev["casos_contestacao"] or 0)
        prev_auditados_linhas = int(prev["auditados_contestacao"] or 0)
        prev_procedentes = int(prev["eventos_contestacao"] or 0)
        prev_taxa = _pct(prev_procedentes, prev_auditados_linhas)
        prev_n = prev_contestados
        cur_n = contestados
        delta_abs = cur_n - prev_n
        delta_pct = None
        if prev_n > 0:
            delta_pct = round(100.0 * delta_abs / prev_n, 1)
        previous = {
            "protocolos_contestados": prev_n,
            "casos_contestacao": prev_casos,
            "eventos_contestacao": prev["eventos_contestacao"],
            "procedentes": prev_procedentes,
            "taxa_procedencia_pct": prev_taxa,
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
            "label": _period_label(start, end),
            "start_date": p_start.isoformat(),
            "end_date": p_end.isoformat(),
        }

    by_day = current.get("by_day") or {}
    by_day_procedentes = current.get("by_day_eventos") or {}

    auditados_contestacao = (
        int(current["auditados_contestacao"] or 0)
        if include_auditados_contestacao
        else 0
    )

    return {
        "ok": True,
        "source": "qualidade_auditado.tipo_analise+qualidade_falha",
        "date_field": "data_recepcao_contestacao",
        "date_field_auditados": aud_date,
        "date_field_falhas": fal_date,
        "protocolos_contestados": contestados,
        "casos_contestacao": casos,
        "eventos_contestacao": current["eventos_contestacao"],
        "protocolos_com_falha_contestacao": procedentes,
        "auditados_contestacao": auditados_contestacao,
        "protocolos_auditados_contestacao": contestados,
        "tipos_analise": [],
        "procedentes": procedentes,
        "improcedentes": improcedentes,
        "finalizadas": auditados_linhas,
        "casos_procedentes": int(current.get("procedentes") or 0),
        "casos_improcedentes": int(current.get("improcedentes") or 0),
        "taxa_procedencia_pct": taxa,
        "procedencia_disponivel": auditados_linhas > 0,
        "procedencia_message": (
            None
            if auditados_linhas > 0
            else "Sem análises de contestação no filtro."
        ),
        "procedencia_rule": (
            "linhas de falha Contest* ÷ linhas auditadas Contest*; mesma granularidade "
            "do KPI Falhas (contestação), recorte temporal do EO e paridade Quality Overview"
        ),
        "previous": previous,
        "by_day": by_day,
        "by_day_procedentes": by_day_procedentes,
        "scoped_to_qualidade_protocols": True,
        "localidade_filtrada": bool(param_list(params, "localidade")),
    }


def contestacao_by_matricula(params, matriculas: list[str]) -> dict[str, int]:
    """Protocolos contestados distintos por matrícula (auditados de contestação)."""
    if not matriculas:
        return {}
    mats = {m.strip().lower() for m in matriculas if m and str(m).strip()}
    if not mats:
        return {}
    aud_qs = (
        _contestacao_auditados_qs(params)
        .exclude(protocolo="")
        .exclude(matricula="")
        .filter(matricula__in=list(mats))
    )
    out: dict[str, int] = {m: 0 for m in mats}
    for row in aud_qs.values("matricula").annotate(c=Count("protocolo", distinct=True)):
        key = (row.get("matricula") or "").strip().lower()
        if key in out:
            out[key] = int(row["c"])
    return out
