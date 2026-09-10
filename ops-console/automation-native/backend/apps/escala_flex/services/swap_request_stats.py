"""Agregações para dashboard de solicitações de troca de escala."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.workforce.models import Agent

from ..models import RequestType, ScheduleRequest
from .permissions import OperationalProfile, get_active_history
from .swap_request_validation import _job_activity_for_agent
from .swap_request_workflow import SWAP_REQUEST_TYPE_NAME, resolve_swap_workflow_status

WEEKDAY_LABELS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

SWAP_KIND_LABELS = {
    "shift_schedule": "Troca de turno",
    "shift_bh": "Banco de horas",
    "peer": "Troca entre colaboradores",
    "": "Não informado",
}

STATUS_LABELS = {
    "approved": "Aprovada",
    "rejected": "Recusada",
    "pending_leader": "Aguardando líder",
    "pending_plan": "Aguardando planejamento",
}


def _parse_date(value: str | list[str] | None) -> date | None:
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _default_period() -> tuple[date, date]:
    today = timezone.localdate()
    return today - timedelta(days=180), today


def _status_bucket(item: ScheduleRequest) -> str:
    return resolve_swap_workflow_status(item)


def _kind_label(kind: str) -> str:
    return SWAP_KIND_LABELS.get(kind or "", SWAP_KIND_LABELS[""])


def _agent_cache() -> dict[str, Agent | None]:
    return {}


def _resolve_agent(agent_lan_id: str, cache: dict[str, Agent | None]) -> Agent | None:
    key = (agent_lan_id or "").strip().lower()
    if not key:
        return None
    if key not in cache:
        cache[key] = Agent.objects.filter(user_lan_id__iexact=key).first()
    return cache[key]


def _resolve_dimensions(item: ScheduleRequest, cache: dict[str, Agent | None]) -> dict[str, str]:
    agent = _resolve_agent(item.agent_lan_id, cache)
    if not agent:
        return {
            "activity": "Não identificada",
            "location": "Não identificada",
            "leader": "Não identificado",
            "team": "Não identificada",
        }

    activity = ""
    if item.date_swap:
        activity = _job_activity_for_agent(agent, item.date_swap)

    history = get_active_history(agent)
    leader_name = "Não identificado"
    location = "Não identificada"
    team = "Não identificada"
    if history:
        if history.leader:
            leader_name = history.leader.full_name or history.leader.user_lan_id
        location = history.location or location
        team = history.team_sector or history.team or team

    return {
        "activity": activity or "Não identificada",
        "location": location,
        "leader": leader_name,
        "team": team,
    }


def _hours_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    start_aware = timezone.make_aware(start) if timezone.is_naive(start) else start
    end_aware = timezone.make_aware(end) if timezone.is_naive(end) else end
    return round((end_aware - start_aware).total_seconds() / 3600, 1)


def _increment_status(target: dict[str, int], status: str, amount: int = 1) -> None:
    target[status] = target.get(status, 0) + amount


def _stacked_row(label: str, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "label": label,
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "pending_leader": counts.get("pending_leader", 0),
        "pending_plan": counts.get("pending_plan", 0),
        "total": sum(counts.values()),
    }


def _get_swap_request_type() -> RequestType:
    request_type, _ = RequestType.objects.get_or_create(
        pk=1,
        defaults={"name": SWAP_REQUEST_TYPE_NAME, "active": True},
    )
    return request_type


def _scope_swap_requests(qs, profile: OperationalProfile):
    if profile.is_admin or profile.team == "Planejamento":
        return qs
    if not profile.lan_id:
        return qs.none()

    lan = profile.lan_id.lower()
    if profile.is_agent_backoffice:
        return qs.filter(
            Q(agent_lan_id__iexact=lan)
            | Q(agent_lan_id_2__iexact=lan)
            | Q(applicant_lan_id__iexact=lan)
        )

    return qs.filter(
        Q(agent_lan_id__iexact=lan)
        | Q(agent_lan_id_2__iexact=lan)
        | Q(applicant_lan_id__iexact=lan)
        | Q(
            agent_lan_id__in=Agent.objects.filter(
                history__leader__user_lan_id__iexact=lan,
                history__active=True,
                history__final_date__isnull=True,
            ).values_list("user_lan_id", flat=True)
        )
    ).distinct()


def build_swap_request_dashboard(
    profile: OperationalProfile,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    params = params or {}
    date_from = _parse_date(params.get("date_from"))
    date_to = _parse_date(params.get("date_to"))
    if not date_from or not date_to:
        date_from, date_to = _default_period()

    qs = ScheduleRequest.objects.filter(request_type=_get_swap_request_type())
    qs = qs.filter(
        Q(date_request__date__gte=date_from, date_request__date__lte=date_to)
        | Q(
            date_request__isnull=True,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
    )
    qs = _scope_swap_requests(qs, profile)

    swap_kind_filter = (params.get("swap_kind") or "").strip()
    if swap_kind_filter:
        qs = qs.filter(swap_kind=swap_kind_filter)

    location_filter = (params.get("location") or "").strip()
    activity_filter = (params.get("job_activity") or "").strip()

    items = list(qs.order_by("-date_request", "-created_at"))
    agent_cache: dict[str, Agent | None] = {}

    filtered_items: list[ScheduleRequest] = []
    for item in items:
        dims = _resolve_dimensions(item, agent_cache)
        if location_filter and dims["location"] != location_filter:
            continue
        if activity_filter and dims["activity"] != activity_filter:
            continue
        filtered_items.append(item)

    summary = {
        "total": 0,
        "approved": 0,
        "rejected": 0,
        "pending_leader": 0,
        "pending_plan": 0,
        "approval_rate": 0.0,
        "rejection_rate": 0.0,
        "avg_leader_hours": None,
        "avg_plan_hours": None,
        "avg_total_hours": None,
    }

    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_weekday: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_location: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_leader: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_team: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    validation_counts = {
        "days_ok": 0,
        "days_fail": 0,
        "hour_ok": 0,
        "hour_fail": 0,
        "activity_ok": 0,
        "activity_fail": 0,
        "not_applicable": 0,
    }
    top_agents: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    leader_hours: list[float] = []
    plan_hours: list[float] = []
    total_hours: list[float] = []

    for item in filtered_items:
        status = _status_bucket(item)
        kind = item.swap_kind or ""
        dims = _resolve_dimensions(item, agent_cache)

        summary["total"] += 1
        summary[status] += 1
        _increment_status(by_kind[kind], status)
        _increment_status(by_activity[dims["activity"]], status)
        _increment_status(by_location[dims["location"]], status)
        _increment_status(by_leader[dims["leader"]], status)
        _increment_status(by_team[dims["team"]], status)

        agent_label = (item.agent_lan_id or "").strip()
        if agent_label:
            _increment_status(top_agents[agent_label], status)

        request_dt = item.date_request or item.created_at
        if request_dt:
            month_key = request_dt.strftime("%Y-%m")
            _increment_status(by_month[month_key], status)
            day_key = request_dt.date().isoformat()
            _increment_status(by_day[day_key], status)

        if item.date_swap:
            weekday = item.date_swap.weekday()
            _increment_status(by_weekday[weekday], status)

        for field, ok_key, fail_key in (
            ("validation_days", "days_ok", "days_fail"),
            ("validation_hour", "hour_ok", "hour_fail"),
            ("validation_activity", "activity_ok", "activity_fail"),
        ):
            value = (getattr(item, field) or "").strip().lower()
            if value in ("", "n/a"):
                validation_counts["not_applicable"] += 1
            elif value == "ok":
                validation_counts[ok_key] += 1
            elif value == "sem escala":
                validation_counts["not_applicable"] += 1
            else:
                validation_counts[fail_key] += 1

        leader_h = _hours_between(item.date_request, item.date_approve_leader)
        if leader_h is not None and leader_h >= 0:
            leader_hours.append(leader_h)
        plan_h = _hours_between(item.date_approve_leader, item.date_approve_plan)
        if plan_h is not None and plan_h >= 0:
            plan_hours.append(plan_h)
        final_decision_at = item.date_approve_plan or (
            item.date_approve_leader if item.approved is not None else None
        )
        total_h = _hours_between(item.date_request, final_decision_at)
        if total_h is not None and total_h >= 0:
            total_hours.append(total_h)

    decided = summary["approved"] + summary["rejected"]
    if decided:
        summary["approval_rate"] = round(summary["approved"] / decided * 100, 1)
        summary["rejection_rate"] = round(summary["rejected"] / decided * 100, 1)
    if leader_hours:
        summary["avg_leader_hours"] = round(sum(leader_hours) / len(leader_hours), 1)
    if plan_hours:
        summary["avg_plan_hours"] = round(sum(plan_hours) / len(plan_hours), 1)
    if total_hours:
        summary["avg_total_hours"] = round(sum(total_hours) / len(total_hours), 1)

    def sort_rows(mapping: dict[str, dict[str, int]], limit: int | None = None) -> list[dict[str, Any]]:
        rows = [_stacked_row(label, counts) for label, counts in mapping.items()]
        rows.sort(key=lambda row: row["total"], reverse=True)
        if limit:
            return rows[:limit]
        return rows

    filter_options = {
        "locations": sorted({dims["location"] for item in items for dims in [_resolve_dimensions(item, agent_cache)]}),
        "activities": sorted({dims["activity"] for item in items for dims in [_resolve_dimensions(item, agent_cache)]}),
        "swap_kinds": [
            {"key": key, "label": label}
            for key, label in SWAP_KIND_LABELS.items()
            if key
        ],
    }

    top_agent_rows: list[dict[str, Any]] = []
    for lan, counts in sorted(top_agents.items(), key=lambda item: sum(item[1].values()), reverse=True)[:10]:
        agent = _resolve_agent(lan, agent_cache)
        top_agent_rows.append(
            {
                "agent_lan_id": lan,
                "agent_name": agent.full_name if agent else lan,
                **_stacked_row(lan, counts),
            }
        )

    return {
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
        "filters": filter_options,
        "summary": summary,
        "by_status": [
            {"key": key, "label": STATUS_LABELS[key], "count": summary[key]}
            for key in ("approved", "rejected", "pending_leader", "pending_plan")
            if summary[key] > 0
        ],
        "by_kind": [
            {
                "key": key or "unknown",
                "label": _kind_label(key),
                **_stacked_row(_kind_label(key), counts),
            }
            for key, counts in sorted(by_kind.items(), key=lambda item: sum(item[1].values()), reverse=True)
        ],
        "by_month": [
            {"month": month, **_stacked_row(month, counts)}
            for month, counts in sorted(by_month.items())
        ],
        "by_weekday": [
            {"weekday": index, "label": WEEKDAY_LABELS[index], **_stacked_row(WEEKDAY_LABELS[index], counts)}
            for index, counts in sorted(by_weekday.items())
        ],
        "by_activity": sort_rows(by_activity, limit=12),
        "by_location": sort_rows(by_location, limit=10),
        "by_leader": sort_rows(by_leader, limit=10),
        "by_team": sort_rows(by_team, limit=10),
        "by_day": [
            {"date": day, **_stacked_row(day, counts)}
            for day, counts in sorted(by_day.items())
        ],
        "top_agents": top_agent_rows,
        "validation": validation_counts,
        "lead_times": {
            "leader_hours": leader_hours,
            "plan_hours": plan_hours,
            "total_hours": total_hours,
        },
    }
