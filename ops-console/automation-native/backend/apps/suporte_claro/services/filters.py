# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_date

from apps.falhas_criticas.services.user_display import resolve_user_display_name
from apps.suporte_claro.models import SuporteClaroRegistro

STATUS_FILTER_PENDENTES = "pendentes"
STATUS_FILTER_VALUES = frozenset(
    {
        SuporteClaroRegistro.STATUS_ABERTO,
        SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
        SuporteClaroRegistro.STATUS_CONCLUIDO,
        STATUS_FILTER_PENDENTES,
    }
)

CATEGORIA_FILTER_VALUES = frozenset(
    {choice[0] for choice in SuporteClaroRegistro.CATEGORIA_CHOICES}
)
TIPO_INCIDENTE_FILTER_VALUES = frozenset(
    {choice[0] for choice in SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES}
)

_SEARCH_MIN_LEN = 2


@dataclass(frozen=True)
class PeriodFilters:
    date_from: date | None
    date_to: date | None
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.date_from is not None or self.date_to is not None


@dataclass(frozen=True)
class ListFilters:
    period: PeriodFilters
    created_by: str | None = None
    status: str | None = None
    q: str | None = None
    categoria: str | None = None
    tipo_incidente: str | None = None


def parse_period_filters(params) -> PeriodFilters:
    raw_from = (params.get("date_from") or params.get("from") or "").strip()
    raw_to = (params.get("date_to") or params.get("to") or "").strip()

    date_from = parse_date(raw_from) if raw_from else None
    date_to = parse_date(raw_to) if raw_to else None

    if raw_from and date_from is None:
        return PeriodFilters(None, None, "Data inicial inválida. Use AAAA-MM-DD.")
    if raw_to and date_to is None:
        return PeriodFilters(None, None, "Data final inválida. Use AAAA-MM-DD.")
    if date_from and date_to and date_from > date_to:
        return PeriodFilters(date_from, date_to, "A data inicial não pode ser posterior à data final.")

    return PeriodFilters(date_from, date_to)


def parse_list_filters(params) -> tuple[ListFilters | None, str | None]:
    period = parse_period_filters(params)
    if period.error:
        return None, period.error

    created_by = (params.get("created_by") or "").strip() or None
    status = (params.get("status") or "").strip() or None
    if status and status not in STATUS_FILTER_VALUES:
        valid = ", ".join(sorted(STATUS_FILTER_VALUES))
        return None, f"Status inválido. Use: {valid}."

    raw_q = (params.get("q") or params.get("search") or "").strip()
    q = raw_q if len(raw_q) >= _SEARCH_MIN_LEN else None

    categoria = (params.get("categoria") or "").strip() or None
    if categoria and categoria not in CATEGORIA_FILTER_VALUES:
        valid = ", ".join(sorted(CATEGORIA_FILTER_VALUES))
        return None, f"Categoria inválida. Use: {valid}."

    tipo_incidente = (params.get("tipo_incidente") or "").strip() or None
    if tipo_incidente and tipo_incidente not in TIPO_INCIDENTE_FILTER_VALUES:
        valid = ", ".join(sorted(TIPO_INCIDENTE_FILTER_VALUES))
        return None, f"tipo_incidente inválido. Use: {valid}."

    return (
        ListFilters(
            period=period,
            created_by=created_by,
            status=status,
            q=q,
            categoria=categoria,
            tipo_incidente=tipo_incidente,
        ),
        None,
    )


def apply_period_filters(qs: QuerySet, period: PeriodFilters) -> QuerySet:
    if period.date_from:
        qs = qs.filter(received_at__date__gte=period.date_from)
    if period.date_to:
        qs = qs.filter(received_at__date__lte=period.date_to)
    return qs


def apply_created_by_filter(qs: QuerySet, created_by: str | None, user) -> QuerySet:
    if not created_by:
        return qs
    if created_by == "me":
        return qs.filter(created_by=user)
    return qs.filter(created_by__username=created_by)


def apply_status_filter(qs: QuerySet, status: str | None) -> QuerySet:
    if not status:
        return qs
    if status == STATUS_FILTER_PENDENTES:
        return qs.filter(
            status__in=(
                SuporteClaroRegistro.STATUS_ABERTO,
                SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
            )
        )
    return qs.filter(status=status)


def apply_search_filter(qs: QuerySet, q: str | None) -> QuerySet:
    """Busca em título, protocolo, irregularidade, chamados, cadastrador e tratado_por."""
    term = (q or "").strip()
    if len(term) < _SEARCH_MIN_LEN:
        return qs
    return qs.filter(
        Q(titulo__icontains=term)
        | Q(protocolo__icontains=term)
        | Q(irregularidade__icontains=term)
        | Q(sent_by__icontains=term)
        | Q(emails__endereco__icontains=term)
        | Q(protocolos__numero__icontains=term)
        | Q(chamados_externos__codigo__icontains=term)
        | Q(chamados_externos__tratado_por__icontains=term)
        | Q(chamado_codigo__icontains=term)
        | Q(created_by__username__icontains=term)
        | Q(created_by__first_name__icontains=term)
        | Q(created_by__last_name__icontains=term)
    ).distinct()


def apply_categoria_filter(qs: QuerySet, categoria: str | None) -> QuerySet:
    if not categoria:
        return qs
    return qs.filter(categoria=categoria)


def apply_tipo_incidente_filter(qs: QuerySet, tipo_incidente: str | None) -> QuerySet:
    if not tipo_incidente:
        return qs
    return qs.filter(tipo_incidente=tipo_incidente)


def build_cadastradores_meta(qs: QuerySet) -> list[dict]:
    counter: Counter[int] = Counter()
    users: dict[int, object] = {}
    for registro in qs.select_related("created_by").only(
        "id",
        "created_by_id",
        "created_by__username",
        "created_by__first_name",
        "created_by__last_name",
    ):
        if not registro.created_by_id:
            continue
        counter[registro.created_by_id] += 1
        users[registro.created_by_id] = registro.created_by

    result = []
    for user_id, count in counter.most_common():
        user = users[user_id]
        result.append(
            {
                "username": user.username,
                "display_name": resolve_user_display_name(user),
                "count": count,
            }
        )
    return result


def period_label(period: PeriodFilters) -> str:
    if not period.active:
        return "Todo o histórico"
    if period.date_from and period.date_to:
        return f"{period.date_from.strftime('%d/%m/%Y')} a {period.date_to.strftime('%d/%m/%Y')}"
    if period.date_from:
        return f"A partir de {period.date_from.strftime('%d/%m/%Y')}"
    return f"Até {period.date_to.strftime('%d/%m/%Y')}"


def period_filename_suffix(period: PeriodFilters) -> str:
    if not period.active:
        return "completo"
    parts = []
    if period.date_from:
        parts.append(period.date_from.isoformat())
    if period.date_to:
        parts.append(period.date_to.isoformat())
    return "_".join(parts)
