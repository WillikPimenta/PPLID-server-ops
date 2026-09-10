"""Filtros de coluna das grades de consulta de escala."""

from __future__ import annotations

from datetime import datetime

from django.db.models import Q, QuerySet

EMPTY_LABEL = "—"
NONE_TOKEN = "__none__"

# Query params → chave lógica usada no FE / opções.
COLUMN_PARAM_MAP: dict[str, str] = {
    "col_data": "data",
    "col_agent_name": "agent_name",
    "col_agent_lan_id": "agent_lan_id",
    "col_horario": "horario",
    "col_dia_escala": "dia_escala",
    "col_activity_name": "activity_name",
    "col_location_name": "location_name",
    "col_equipe": "equipe",
    "col_leader_name": "leader_name",
}

PUBLISHED_COLUMN_KEYS = (
    "data",
    "agent_name",
    "agent_lan_id",
    "horario",
    "dia_escala",
    "activity_name",
    "location_name",
    "equipe",
    "leader_name",
)

PLANNING_COLUMN_KEYS = (
    "data",
    "agent_name",
    "agent_lan_id",
    "dia_escala",
    "activity_name",
    "location_name",
    "equipe",
    "leader_name",
)


def format_escala_date(value) -> str:
    if value is None:
        return EMPTY_LABEL
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip()
    if not text:
        return EMPTY_LABEL
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return text


def parse_escala_date_label(label: str):
    try:
        return datetime.strptime(label.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _display_day_schedule(dia_escala: str | None, horario: str | None) -> str:
    dia = (dia_escala or "").strip()
    if dia:
        return dia
    hor = (horario or "").strip()
    return hor or EMPTY_LABEL


def _sorted_labels(values: set[str]) -> list[str]:
    return sorted(values, key=lambda item: item.casefold())


def build_escala_column_filter_options(
    qs: QuerySet,
    *,
    include_horario: bool = True,
) -> dict[str, list[str]]:
    """Opções distintas do queryset completo (sem paginação)."""
    options: dict[str, set[str]] = {
        "data": set(),
        "agent_name": set(),
        "agent_lan_id": set(),
        "horario": set(),
        "dia_escala": set(),
        "activity_name": set(),
        "location_name": set(),
        "equipe": set(),
        "leader_name": set(),
    }

    for data in qs.values_list("data", flat=True).distinct():
        options["data"].add(format_escala_date(data))

    for name in qs.values_list("agent__full_name", flat=True).distinct():
        options["agent_name"].add((name or "").strip() or EMPTY_LABEL)

    for lan in qs.values_list("agent__user_lan_id", flat=True).distinct():
        options["agent_lan_id"].add((lan or "").strip() or EMPTY_LABEL)

    if include_horario:
        for horario in qs.values_list("horario", flat=True).distinct():
            options["horario"].add((horario or "").strip() or EMPTY_LABEL)

    for dia, horario in qs.values_list("dia_escala", "horario").distinct():
        options["dia_escala"].add(_display_day_schedule(dia, horario))

    for name in qs.values_list("job_activity__name", flat=True).distinct():
        options["activity_name"].add((name or "").strip() or EMPTY_LABEL)

    for display, city in qs.values_list(
        "location__display_name",
        "location__city_name",
    ).distinct():
        label = (display or "").strip() or (city or "").strip() or EMPTY_LABEL
        options["location_name"].add(label)

    for equipe in qs.values_list("equipe", flat=True).distinct():
        options["equipe"].add((equipe or "").strip() or EMPTY_LABEL)

    for name in qs.values_list("leader__full_name", flat=True).distinct():
        options["leader_name"].add((name or "").strip() or EMPTY_LABEL)

    keys = PUBLISHED_COLUMN_KEYS if include_horario else PLANNING_COLUMN_KEYS
    return {key: _sorted_labels(options[key]) for key in keys}


def apply_escala_column_filters(qs: QuerySet, query_params) -> QuerySet:
    for param, field in COLUMN_PARAM_MAP.items():
        raw_values = [value for value in query_params.getlist(param) if value != ""]
        if not raw_values:
            continue
        if NONE_TOKEN in raw_values:
            return qs.none()

        if field == "data":
            dates = []
            include_empty = False
            for label in raw_values:
                if label == EMPTY_LABEL:
                    include_empty = True
                    continue
                parsed = parse_escala_date_label(label)
                if parsed is not None:
                    dates.append(parsed)
            q = Q()
            if dates:
                q |= Q(data__in=dates)
            if include_empty:
                q |= Q(data__isnull=True)
            if q:
                qs = qs.filter(q)
            continue

        if field == "agent_name":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(agent__full_name__in=non_empty)
            if EMPTY_LABEL in raw_values:
                q |= Q(agent__full_name__isnull=True) | Q(agent__full_name="")
            if q:
                qs = qs.filter(q)
            continue

        if field == "agent_lan_id":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(agent__user_lan_id__in=non_empty)
            if EMPTY_LABEL in raw_values:
                q |= Q(agent__user_lan_id__isnull=True) | Q(agent__user_lan_id="")
            if q:
                qs = qs.filter(q)
            continue

        if field == "horario":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(horario__in=non_empty)
            if EMPTY_LABEL in raw_values:
                q |= Q(horario__isnull=True) | Q(horario="")
            if q:
                qs = qs.filter(q)
            continue

        if field == "dia_escala":
            q = Q()
            for value in raw_values:
                if value == EMPTY_LABEL:
                    q |= (Q(dia_escala="") | Q(dia_escala__isnull=True)) & (
                        Q(horario="") | Q(horario__isnull=True)
                    )
                else:
                    q |= Q(dia_escala=value) | (Q(dia_escala="") & Q(horario=value))
            if q:
                qs = qs.filter(q)
            continue

        if field == "activity_name":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(job_activity__name__in=non_empty)
            if EMPTY_LABEL in raw_values:
                q |= Q(job_activity__isnull=True) | Q(job_activity__name="")
            if q:
                qs = qs.filter(q)
            continue

        if field == "location_name":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(location__display_name__in=non_empty) | Q(
                    location__city_name__in=non_empty
                )
            if EMPTY_LABEL in raw_values:
                q |= Q(location__isnull=True) | (
                    (Q(location__display_name__isnull=True) | Q(location__display_name=""))
                    & (Q(location__city_name__isnull=True) | Q(location__city_name=""))
                )
            if q:
                qs = qs.filter(q)
            continue

        if field == "equipe":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(equipe__in=non_empty)
            if EMPTY_LABEL in raw_values:
                q |= Q(equipe__isnull=True) | Q(equipe="")
            if q:
                qs = qs.filter(q)
            continue

        if field == "leader_name":
            q = Q()
            non_empty = [v for v in raw_values if v != EMPTY_LABEL]
            if non_empty:
                q |= Q(leader__full_name__in=non_empty)
            if EMPTY_LABEL in raw_values:
                q |= Q(leader__isnull=True) | Q(leader__full_name="")
            if q:
                qs = qs.filter(q)
            continue

    return qs
