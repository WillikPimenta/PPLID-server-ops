# -*- coding: utf-8 -*-
"""Métrica oficial de Excelência Operacional (EO).

Versão versionada da regra de recorte histórico aplicada à população
``qualidade_auditado`` / ``qualidade_falha`` antes de qualquer agregação.

Decisões (official-v1):
- Data intrínseca da regra: sempre o campo ``data`` (Data de auditoria).
- O parâmetro ``date_axis`` continua filtrando o período visualizado
  (``data`` ou ``data_analise``), mas não altera a decisão oficial.
- Cutover inclusivo: ``2026-07-01``. Antes dessa data, classificações
  listadas são excluídas; a partir dela, todas entram.
- Comparação de textos: igualdade após trim + normalização de caixa
  (sem substring / semântica de família).
- ``REGISTROS_PONTUAIS`` e ``AUDITADOS_CONTESTADOS`` são legados sem
  vínculo com o indicador atual — não modelados aqui.
- Ataque de fraude: o DAX original isenta restrição por
  ``Data diferença``. O EO não filtra por ``data_diferenca`` hoje;
  se esse filtro for criado no futuro, ``Ataque de fraude`` não deve
  ser restringido por ele. Isso não dispensa as demais regras de data
  nem as exclusões históricas abaixo.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import Q, QuerySet

METRIC_MODE_OFFICIAL = "official"
METRIC_MODE_COMPLETE = "complete"
VALID_METRIC_MODES = frozenset({METRIC_MODE_OFFICIAL, METRIC_MODE_COMPLETE})

OFFICIAL_METRIC_VERSION = "official-v1"
OFFICIAL_METRIC_CUTOVER = date(2026, 7, 1)
OFFICIAL_METRIC_DATE_FIELD = "data"

# Auditados — bloqueados apenas antes do cutover (igualdade normalizada).
BLOCKED_AUDITADO_TIPO_ANALISE = frozenset(
    {
        "AUDITORIA REDOC",
        "ANÁLISE DIRECIONADA",
        "ANÁLISE SF",
        "AUDITORIA DIRECIONADA",
    }
)

# Falhas — tipo_analise bloqueado antes do cutover.
# Inclui variantes sem acento presentes na base histórica.
BLOCKED_FALHA_TIPO_ANALISE = frozenset(
    {
        "AUDITORIA REDOC",
        "ANÁLISE DIRECIONADA",
        "ANALISE DIRECIONADA",
        "ANÁLISE SF",
        "ANALISE SF",
        "AUDITORIA DIRECIONADA",
    }
)

# Falhas — tipo_modulo_2 bloqueado antes do cutover.
BLOCKED_FALHA_TIPO_MODULO_2 = frozenset(
    {
        "ANÁLISE DIRECIONADA",
        "ANALISE DIRECIONADA",
        "ANÁLISES AVULSAS",
        "ANALISES AVULSAS",
        "CONTESTAÇÃO AF",
        "CONTESTACAO AF",
    }
)

# Falhas — tipo_falha bloqueado antes do cutover.
BLOCKED_FALHA_TIPO_FALHA = frozenset(
    {
        "BIOMETRIA",
        "MAPEAMENTO",
        "NECESSIDADE DEVOLUTIVA",
        "PROCESSUAL",
        "SISTEMA",
    }
)


def normalize_official_label(value: Any) -> str:
    """Trim + caixa alta; usado em testes e predicados ORM alinhados."""
    if value is None:
        return ""
    return str(value).strip().upper()


def resolve_metric_mode(params) -> str:
    """Resolve ``metric_mode``; ausência ou valor inválido → complete (população integral)."""
    raw = ""
    if hasattr(params, "get"):
        raw = params.get("metric_mode") or params.get("metrica") or ""
    text = str(raw).strip().lower() if raw is not None else ""
    if text in {"official", "oficial", "metric_oficial", "métrica_oficial"}:
        return METRIC_MODE_OFFICIAL
    if text in {"complete", "completo", "full", "visao_completa", "visão_completa"}:
        return METRIC_MODE_COMPLETE
    # Ausência ou inválido → complete (sem recorte histórico da métrica oficial).
    return METRIC_MODE_COMPLETE


def official_metric_meta(*, active: bool | None = None, metric_mode: str | None = None) -> dict[str, Any]:
    mode = metric_mode or (METRIC_MODE_OFFICIAL if active is not False else METRIC_MODE_COMPLETE)
    if active is None:
        active = mode == METRIC_MODE_OFFICIAL
    return {
        "active": bool(active),
        "version": OFFICIAL_METRIC_VERSION,
        "cutover": OFFICIAL_METRIC_CUTOVER.isoformat(),
        "date_field": OFFICIAL_METRIC_DATE_FIELD,
    }


def metric_mode_payload(params) -> dict[str, Any]:
    mode = resolve_metric_mode(params)
    return {
        "metric_mode": mode,
        "official_metric": official_metric_meta(metric_mode=mode),
    }


def _iexact_in_q(field: str, labels: frozenset[str]) -> Q:
    """Predicado ORM: igualdade case-insensitive após trim implícito da importação.

    A importação já limpa espaços externos dos campos textuais. Usamos
    ``__iexact`` (caixa) com OR explícito — sem ``icontains``/substring.
    """
    q = Q()
    for label in labels:
        q |= Q(**{f"{field}__iexact": label})
    return q


def official_auditados_q() -> Q:
    """Inclui auditado na métrica oficial.

    ``data`` válida E (data >= cutover OU tipo_analise não bloqueado).
    Sem ``data`` → fora da métrica (DAX conta coluna de data não vazia).
    """
    blocked = _iexact_in_q("tipo_analise", BLOCKED_AUDITADO_TIPO_ANALISE)
    return Q(data__isnull=False) & (
        Q(**{f"{OFFICIAL_METRIC_DATE_FIELD}__gte": OFFICIAL_METRIC_CUTOVER}) | ~blocked
    )


def official_falhas_q() -> Q:
    """Inclui falha na métrica oficial.

    ``data`` válida E (
      data >= cutover
      OU (tipo_analise ok E tipo_modulo_2 ok E tipo_falha ok)
    ).

    Basta uma dimensão bloqueada para excluir antes do cutover.
    Valores vazios não são tratados como bloqueados.
    """
    blocked_analise = _iexact_in_q("tipo_analise", BLOCKED_FALHA_TIPO_ANALISE)
    blocked_modulo = _iexact_in_q("tipo_modulo_2", BLOCKED_FALHA_TIPO_MODULO_2)
    blocked_falha = _iexact_in_q("tipo_falha", BLOCKED_FALHA_TIPO_FALHA)
    historically_excluded = blocked_analise | blocked_modulo | blocked_falha
    return Q(data__isnull=False) & (
        Q(**{f"{OFFICIAL_METRIC_DATE_FIELD}__gte": OFFICIAL_METRIC_CUTOVER})
        | ~historically_excluded
    )


def apply_official_metric_auditados(qs: QuerySet, params) -> QuerySet:
    if resolve_metric_mode(params) != METRIC_MODE_OFFICIAL:
        return qs
    return qs.filter(official_auditados_q())


def apply_official_metric_falhas(qs: QuerySet, params) -> QuerySet:
    if resolve_metric_mode(params) != METRIC_MODE_OFFICIAL:
        return qs
    return qs.filter(official_falhas_q())
