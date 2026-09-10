# -*- coding: utf-8 -*-
"""Derivação de contestação EO — paridade Indicadores × Quality Overview / report BRB."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from django.db.models import Count, Q, QuerySet


def norm_protocolo(value: object) -> str:
    return str(value or "").strip()


def norm_matricula(value: object) -> str:
    return str(value or "").strip().casefold()


def case_key(protocolo: object, matricula: object) -> tuple[str, str]:
    return norm_protocolo(protocolo), norm_matricula(matricula)


def chave_caso(protocolo: object, matricula: object) -> str:
    p, m = case_key(protocolo, matricula)
    return f"{p}|{m}"


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _procedencia_to_conforme(procedencia: object) -> str:
    text = _safe_str(procedencia).lower()
    if not text:
        return ""
    if "improcedente" in text:
        return "Sim"
    if "procedente" in text:
        return "Não"
    return ""


def _falha_tipo_to_conforme(tipo_falha: object) -> str:
    tf = _safe_str(tipo_falha).upper()
    if not tf or "SEM FALHA" in tf:
        return "Sim"
    return "Não"


def derive_conforme_value(
    *,
    procedencia: object = "",
    tipo_falha: object = "",
    has_contestacao_falha: bool = False,
    origem_tratado: object = "",
    tipo_registro: object = "",
) -> str:
    """Deriva CONFORME (Sim/Não) com semântica específica por fluxo.

    Em contestação, ``Procedente`` confirma a falha e ``Improcedente`` a
    rejeita. Em reinspeção esses rótulos têm semântica diferente; nesse fluxo
    a fonte canônica é a existência da ``QualidadeFalha`` projetada.
    """
    origem = _safe_str(origem_tratado or tipo_registro).casefold()
    if origem != "reinspecao":
        from_procedencia = _procedencia_to_conforme(procedencia)
        if from_procedencia:
            return from_procedencia
    if has_contestacao_falha:
        return _falha_tipo_to_conforme(tipo_falha)
    return "Sim"


def classificacao_conforme(conforme: str) -> str:
    if conforme == "Não":
        return "falha"
    if conforme == "Sim":
        return "nao_falha"
    return "indefinido"


def apply_contestacao_reception_period(
    qs: QuerySet,
    *,
    start: date | None,
    end: date | None,
) -> QuerySet:
    """Recorte temporal de contestação: data_recepcao_contestacao, fallback ``data``."""
    if start is None and end is None:
        return qs
    period_q = Q()
    if start is not None:
        period_q &= Q(data_recepcao_contestacao__gte=start)
    if end is not None:
        period_q &= Q(data_recepcao_contestacao__lte=end)
    aud_period = qs.filter(period_q)
    if aud_period.exists():
        return aud_period
    fallback = Q()
    if start is not None:
        fallback &= Q(data__gte=start)
    if end is not None:
        fallback &= Q(data__lte=end)
    return qs.filter(fallback)


def _params_without_dates(params):
    copied = params.copy() if hasattr(params, "copy") else dict(params)
    if hasattr(copied, "pop"):
        copied.pop("start_date", None)
        copied.pop("end_date", None)
    return copied


def contestacao_auditados_scope(params) -> QuerySet:
    """Auditados contestação com filtros EO, sem recorte ``date_axis``."""
    from apps.qualidade_operacional.services.contestacao_metrics import (
        _contestacao_auditados_qs,
    )

    return _contestacao_auditados_qs(_params_without_dates(params))


def contestacao_falhas_scope(params) -> QuerySet:
    """Falhas contestação com filtros EO, sem recorte temporal (batimento caso a caso)."""
    from apps.qualidade_operacional.services.contestacao_metrics import (
        _contestacao_falhas_qs,
    )

    return _contestacao_falhas_qs(_params_without_dates(params))


def _row_recency_key(row: dict[str, Any]) -> tuple[date, int]:
    return row.get("data_analise") or date.min, row.get("id") or 0


def _falha_row_is_newer(candidate: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    if previous is None:
        return True
    return _row_recency_key(candidate) > _row_recency_key(previous)


def build_falha_lookup_indexes(
    falhas: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    """Índices de falha por ``protocolo+matrícula`` e fallback por protocolo.

    Falhas importadas (TSV/planilha) frequentemente vêm sem matrícula; o batimento
    tenta primeiro o par completo e depois o protocolo mais recente.
    """
    by_case: dict[tuple[str, str], dict[str, Any]] = {}
    by_protocolo: dict[str, dict[str, Any]] = {}
    for row in falhas:
        proto = norm_protocolo(row.get("protocolo"))
        if not proto:
            continue
        mat = norm_matricula(row.get("matricula"))
        case_tuple = (proto, mat)
        if _falha_row_is_newer(row, by_case.get(case_tuple)):
            by_case[case_tuple] = row
        if _falha_row_is_newer(row, by_protocolo.get(proto)):
            by_protocolo[proto] = row
    return by_case, by_protocolo


def lookup_falha_for_auditado(
    aud: dict[str, Any],
    *,
    by_case: dict[tuple[str, str], dict[str, Any]],
    by_protocolo: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve falha de contestação para um auditado (par ou fallback protocolo)."""
    proto = norm_protocolo(aud.get("protocolo"))
    if not proto:
        return None
    mat = norm_matricula(aud.get("matricula"))
    fal = by_case.get((proto, mat))
    if fal is not None:
        return fal
    if mat:
        fal = by_case.get((proto, ""))
        if fal is not None:
            return fal
    return by_protocolo.get(proto)


def _build_falha_by_case(falhas: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    by_case, _by_protocolo = build_falha_lookup_indexes(falhas)
    return by_case


def derive_contestacao_cases_indexed(
    auditados: list[dict[str, Any]],
    *,
    by_case: dict[tuple[str, str], dict[str, Any]],
    by_protocolo: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Uma linha por auditado contestação, com CONFORME derivado (antes do dedup)."""
    out: list[dict[str, Any]] = []
    for aud in auditados:
        fal = lookup_falha_for_auditado(aud, by_case=by_case, by_protocolo=by_protocolo)
        conforme = derive_conforme_value(
            procedencia=aud.get("procedencia") or (fal or {}).get("procedencia", ""),
            tipo_falha=(fal or {}).get("tipo_falha") or (fal or {}).get("categoria_falha", ""),
            has_contestacao_falha=fal is not None,
            origem_tratado=aud.get("origem_tratado"),
            tipo_registro=aud.get("tipo_registro"),
        )
        ref_date = aud.get("data_recepcao_contestacao") or aud.get("data")
        out.append(
            {
                "_auditado_id": aud.get("id") or 0,
                "protocolo": norm_protocolo(aud.get("protocolo")),
                "matricula": norm_matricula(aud.get("matricula")),
                "chave_caso": chave_caso(aud.get("protocolo"), aud.get("matricula")),
                "conforme": conforme,
                "classificacao_conforme": classificacao_conforme(conforme),
                "data_recepcao": ref_date,
                "data_analise": aud.get("data_analise"),
                "has_falha": fal is not None,
            }
        )
    return out


def derive_contestacao_cases(
    auditados: list[dict[str, Any]],
    falha_by_case: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compat: deriva casos a partir de índice só por par protocolo+matrícula."""
    return derive_contestacao_cases_indexed(
        auditados,
        by_case=falha_by_case,
        by_protocolo={},
    )


def dedupe_contestacao_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup por chave_caso — maior data_analise e, no empate, maior ID."""
    by_key: dict[str, dict[str, Any]] = {}
    for row in cases:
        key = row["chave_caso"]
        prev = by_key.get(key)
        if prev is None or (
            row.get("data_analise") or date.min,
            row.get("_auditado_id") or 0,
        ) > (
            prev.get("data_analise") or date.min,
            prev.get("_auditado_id") or 0,
        ):
            by_key[key] = row
    return list(by_key.values())


def compute_contestacao_case_metrics(
    params,
    *,
    start: date | None,
    end: date | None,
    aud_scope: QuerySet | None = None,
    fal_scope: QuerySet | None = None,
) -> dict[str, Any]:
    """Métricas de contestação alinhadas ao Quality Overview (caso + batimento falha)."""
    from apps.qualidade_operacional.services.contestacao_metrics import (
        _contestacao_falhas_qs,
        contestacao_operacional_auditado_q,
        contestacao_operacional_falha_q,
    )

    aud_qs = (aud_scope or contestacao_auditados_scope(params)).filter(
        contestacao_operacional_auditado_q()
    )
    aud_qs = apply_contestacao_reception_period(aud_qs, start=start, end=end)

    fal_qs = (fal_scope or contestacao_falhas_scope(params)).filter(
        contestacao_operacional_falha_q()
    )
    # O batimento caso a caso precisa consultar o histórico de falhas, pois a
    # data da falha pode divergir da recepção da contestação. O volume exibido,
    # porém, deve respeitar exatamente o período e os filtros do EO. Reutilizar
    # ``fal_qs`` aqui fazia o card contar toda a base histórica (11,3 mil linhas).
    metric_fal_qs = _contestacao_falhas_qs(params)
    from apps.qualidade_operacional.services.queries import date_field_falhas

    metric_date_field = date_field_falhas(params)
    by_day_eventos: dict[str, int] = {}
    for row in metric_fal_qs.values(metric_date_field).annotate(c=Count("id")):
        ref = row.get(metric_date_field)
        if not ref:
            continue
        day_key = ref.isoformat() if hasattr(ref, "isoformat") else str(ref)
        by_day_eventos[day_key] = int(row["c"] or 0)

    auditados = list(
        aud_qs.values(
            "id",
            "protocolo",
            "matricula",
            "data",
            "data_analise",
            "data_recepcao_contestacao",
            "procedencia",
            "origem_tratado",
            "tipo_registro",
        )
    )
    falhas = list(
        fal_qs.values(
            "id",
            "protocolo",
            "matricula",
            "tipo_falha",
            "categoria_falha",
            "procedencia",
            "data_analise",
        )
    )

    falha_by_case, falha_by_protocolo = build_falha_lookup_indexes(falhas)
    raw_cases = derive_contestacao_cases_indexed(
        auditados,
        by_case=falha_by_case,
        by_protocolo=falha_by_protocolo,
    )
    cases = dedupe_contestacao_cases(raw_cases)

    procedentes = sum(1 for c in cases if c["classificacao_conforme"] == "falha")
    improcedentes = sum(1 for c in cases if c["classificacao_conforme"] == "nao_falha")
    protocolos = {c["protocolo"] for c in cases if c["protocolo"]}

    by_day: dict[str, int] = defaultdict(int)
    by_day_procedentes: dict[str, int] = defaultdict(int)
    for row in cases:
        ref = row.get("data_recepcao")
        if not ref:
            continue
        day_key = ref.isoformat() if hasattr(ref, "isoformat") else str(ref)
        by_day[day_key] += 1
        if row["classificacao_conforme"] == "falha":
            by_day_procedentes[day_key] += 1

    return {
        "auditados_contestacao": len(auditados),
        "casos_contestacao": len(cases),
        "protocolos_contestados": len(protocolos),
        "eventos_contestacao": metric_fal_qs.count(),
        "protocolos_com_falha_contestacao": procedentes,
        "procedentes": procedentes,
        "improcedentes": improcedentes,
        "by_day": dict(by_day),
        "by_day_procedentes": dict(by_day_procedentes),
        "by_day_eventos": by_day_eventos,
    }
