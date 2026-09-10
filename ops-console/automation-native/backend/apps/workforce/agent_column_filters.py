"""Filtros de coluna da grade de headcount (vigência atual)."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.workforce.models import Agent, AgentHistory

EMPTY_LABEL = "—"
NONE_TOKEN = "__none__"

COLUMN_PARAM_MAP: dict[str, str] = {
    "col_full_name": "full_name",
    "col_user_lan_id": "user_lan_id",
    "col_active": "active",
    "col_team": "team",
    "col_job_title": "job_title",
    "col_job_activity": "job_activity",
    "col_location": "location",
    "col_leader_name": "leader_name",
    "col_facilitator_name": "facilitator_name",
    "col_journey": "journey",
    "col_band": "band",
}

FRONTEND_COLUMN_KEYS: dict[str, str] = {
    "full_name": "col_full_name",
    "user_lan_id": "col_user_lan_id",
    "active": "col_active",
    "team": "col_team",
    "job_title": "col_job_title",
    "job_activity": "col_job_activity",
    "location": "col_location",
    "leader_name": "col_leader_name",
    "facilitator_name": "col_facilitator_name",
    "journey": "col_journey",
    "band": "col_band",
}


def _resolve_current_history(agent: Agent) -> AgentHistory | None:
    prefetched = getattr(agent, "current_histories", None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return (
        agent.history.filter(active=True, final_date__isnull=True)
        .select_related("leader", "facilitator")
        .order_by("-start_date")
        .first()
    )


def _display_value(agent: Agent, current: AgentHistory | None, field: str) -> str:
    if field == "full_name":
        return (agent.full_name or "").strip() or EMPTY_LABEL
    if field == "user_lan_id":
        return (agent.user_lan_id or "").strip() or EMPTY_LABEL
    if field == "active":
        return "Ativo" if agent.active else "Inativo"
    if not current:
        return EMPTY_LABEL
    if field == "leader_name":
        return (current.leader.full_name if current.leader else "").strip() or EMPTY_LABEL
    if field == "facilitator_name":
        return (current.facilitator.full_name if current.facilitator else "").strip() or EMPTY_LABEL
    value = getattr(current, field, "") or ""
    return str(value).strip() or EMPTY_LABEL


def build_column_filter_options(qs: QuerySet[Agent]) -> dict[str, list[str]]:
    agents = qs.prefetch_related(
        "history__leader",
        "history__facilitator",
    )
    options: dict[str, set[str]] = {key: set() for key in FRONTEND_COLUMN_KEYS}
    for agent in agents:
        current = _resolve_current_history(agent)
        for key in FRONTEND_COLUMN_KEYS:
            options[key].add(_display_value(agent, current, key))
    return {
        key: sorted(values, key=lambda item: item.casefold())
        for key, values in options.items()
    }


def apply_column_filters(qs: QuerySet[Agent], query_params) -> QuerySet[Agent]:
    for param, field in COLUMN_PARAM_MAP.items():
        raw_values = [value for value in query_params.getlist(param) if value != ""]
        if not raw_values:
            continue
        if NONE_TOKEN in raw_values:
            return qs.none()

        if field == "active":
            bool_values: list[bool] = []
            if "Ativo" in raw_values:
                bool_values.append(True)
            if "Inativo" in raw_values:
                bool_values.append(False)
            if bool_values:
                qs = qs.filter(active__in=bool_values)
            continue

        if field in {"full_name", "user_lan_id"}:
            q = Q()
            non_empty = [value for value in raw_values if value != EMPTY_LABEL]
            if non_empty:
                q |= Q(**{f"{field}__in": non_empty})
            if EMPTY_LABEL in raw_values:
                q |= Q(**{f"{field}__isnull": True}) | Q(**{field: ""})
            if q:
                qs = qs.filter(q)
            continue

        history_field_map = {
            "team": "team",
            "job_title": "job_title",
            "job_activity": "job_activity",
            "location": "location",
            "journey": "journey",
            "band": "band",
            "leader_name": "leader__full_name",
            "facilitator_name": "facilitator__full_name",
        }
        lookup = history_field_map.get(field)
        if not lookup:
            continue

        open_history = Q(history__active=True, history__final_date__isnull=True)
        q = Q()
        non_empty = [value for value in raw_values if value != EMPTY_LABEL]
        if non_empty:
            q |= open_history & Q(**{f"history__{lookup}__in": non_empty})
        if EMPTY_LABEL in raw_values:
            q |= ~open_history
            q |= open_history & (Q(**{f"history__{lookup}__isnull": True}) | Q(**{f"history__{lookup}": ""}))
        if q:
            qs = qs.filter(q).distinct()

    return qs
