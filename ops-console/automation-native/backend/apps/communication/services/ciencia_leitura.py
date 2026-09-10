"""Agregações de ciência de leitura sobre notícias críticas + HC."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.communication.models import News, NewsAcknowledgment
from apps.workforce.models import AgentHistory, UserProfile

# Status sem prazo: agendado | em_andamento | concluido
STATUS_AGENDADO = "agendado"
STATUS_EM_ANDAMENTO = "em_andamento"
STATUS_CONCLUIDO = "concluido"


def _now() -> datetime:
    return timezone.now()


def _as_aware(value: datetime | Any) -> datetime:
    """Normaliza date/datetime do campo published_at para datetime aware."""
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    # date
    return timezone.make_aware(
        datetime.combine(value, datetime.min.time()),
        timezone.get_current_timezone(),
    )

def _current_history_prefetch() -> Prefetch:
    return Prefetch(
        "agent__history",
        queryset=(
            AgentHistory.objects.filter(active=True, final_date__isnull=True)
            .select_related("leader")
            .order_by("-start_date")
        ),
        to_attr="current_histories",
    )


def eligible_profiles_qs():
    """Universo de destinatários: usuários ativos com HC ativo vinculado."""
    return (
        UserProfile.objects.filter(user__is_active=True, agent__active=True)
        .select_related("user", "agent")
        .prefetch_related(_current_history_prefetch())
    )


def _history_of(profile: UserProfile) -> AgentHistory | None:
    histories = getattr(profile.agent, "current_histories", None)
    if histories:
        return histories[0]
    return (
        AgentHistory.objects.filter(
            agent=profile.agent, active=True, final_date__isnull=True
        )
        .select_related("leader")
        .order_by("-start_date")
        .first()
    )


def _hc_dims(profile: UserProfile) -> dict[str, str]:
    hist = _history_of(profile)
    if not hist:
        return {
            "area": "",
            "unit": "",
            "shift": "",
            "team": "",
            "leader_id": "",
            "leader_name": "",
        }
    area = (hist.team_sector or hist.team or "").strip()
    leader = hist.leader
    return {
        "area": area,
        "unit": (hist.location or "").strip(),
        "shift": (hist.journey_shift or "").strip(),
        "team": (hist.team or "").strip(),
        "leader_id": str(leader.pk) if leader else "",
        "leader_name": (leader.full_name or "").strip() if leader else "",
    }


def resolve_status(news: News, pending_count: int, now: datetime | None = None) -> str:
    now = now or _now()
    published = _as_aware(news.published_at)
    if published > now:
        return STATUS_AGENDADO
    if pending_count <= 0:
        return STATUS_CONCLUIDO
    return STATUS_EM_ANDAMENTO


def critical_news_qs(*, include_scheduled: bool = True):
    qs = News.objects.filter(active=True, is_critical=True)
    if not include_scheduled:
        qs = qs.filter(published_at__lte=_now())
    return qs.annotate(ack_total=Count("acknowledgments", distinct=True))


def filter_options() -> dict[str, Any]:
    profiles = list(eligible_profiles_qs())
    areas: set[str] = set()
    units: set[str] = set()
    shifts: set[str] = set()
    leaders: dict[str, str] = {}
    for profile in profiles:
        dims = _hc_dims(profile)
        if dims["area"]:
            areas.add(dims["area"])
        if dims["unit"]:
            units.add(dims["unit"])
        if dims["shift"]:
            shifts.add(dims["shift"])
        if dims["leader_id"] and dims["leader_name"]:
            leaders[dims["leader_id"]] = dims["leader_name"]

    categories = [c.value for c in News.Category]
    statuses = [
        {"value": STATUS_EM_ANDAMENTO, "label": "Em andamento"},
        {"value": STATUS_AGENDADO, "label": "Agendado"},
        {"value": STATUS_CONCLUIDO, "label": "Concluído"},
    ]
    return {
        "areas": sorted(areas),
        "units": sorted(units),
        "shifts": sorted(shifts),
        "leaders": [
            {"value": lid, "label": name}
            for lid, name in sorted(leaders.items(), key=lambda item: item[1].lower())
        ],
        "categories": categories,
        "statuses": statuses,
    }


def _match_search(news: News, search: str) -> bool:
    q = search.strip().lower()
    if not q:
        return True
    blob = " ".join([news.title, news.category, news.author, news.summary or ""]).lower()
    return q in blob


def _period_start(period: str | None, now: datetime) -> datetime | None:
    if not period or period == "all":
        return None
    days = 7 if period == "7d" else 90 if period == "90d" else 30
    return now - timedelta(days=days)


def build_communication_row(
    news: News,
    *,
    total_recipients: int,
    acknowledged_count: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    pending = max(0, total_recipients - acknowledged_count)
    rate = round((acknowledged_count / total_recipients) * 100, 1) if total_recipients else 0.0
    status = resolve_status(news, pending, now)
    published = _as_aware(news.published_at)
    return {
        "id": str(news.id),
        "title": news.title,
        "category": news.category,
        "author": news.author,
        "publishedAt": published.isoformat(),
        "status": status,
        "acknowledgmentRequired": True,
        "targetAudience": "Colaboradores com acesso ao portal (HC ativo)",
        "totalRecipients": total_recipients,
        "acknowledgedCount": acknowledged_count,
        "pendingCount": pending,
        "acknowledgmentRate": rate,
        "obligationType": "Ciência obrigatória",
        "unitsCount": 0,
    }


def list_communications(params: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    profiles = list(eligible_profiles_qs())
    total_recipients = len(profiles)
    ack_by_news = {
        str(row["news_id"]): row["total"]
        for row in NewsAcknowledgment.objects.values("news_id").annotate(total=Count("id"))
    }

    period_since = _period_start(params.get("period"), now)
    search = (params.get("search") or "").strip()
    status_filter = (params.get("status") or "").strip()
    category = (params.get("category") or "").strip()
    area = (params.get("area") or "").strip()
    unit = (params.get("unit") or "").strip()

    # Filtros de HC no dashboard afetam o universo contado por comunicado
    # (recorte de destinatários). Sem area/unit, usa total global.
    def recipients_in_scope() -> list[UserProfile]:
        if not area and not unit:
            return profiles
        out = []
        for profile in profiles:
            dims = _hc_dims(profile)
            if area and dims["area"] != area:
                continue
            if unit and dims["unit"] != unit:
                continue
            out.append(profile)
        return out

    scoped = recipients_in_scope()
    scoped_user_ids = {p.user_id for p in scoped}
    scoped_total = len(scoped)

    rows: list[dict[str, Any]] = []
    for news in critical_news_qs(include_scheduled=True).order_by("-published_at", "-created_at"):
        published = _as_aware(news.published_at)
        if period_since and published < period_since:
            continue
        if category and news.category != category:
            continue
        if search and not _match_search(news, search):
            continue

        if scoped_total == total_recipients:
            ack_count = int(ack_by_news.get(str(news.id), 0))
            recipient_total = total_recipients
        else:
            ack_count = NewsAcknowledgment.objects.filter(
                news_id=news.id, user_id__in=scoped_user_ids
            ).count()
            recipient_total = scoped_total

        row = build_communication_row(
            news,
            total_recipients=recipient_total,
            acknowledged_count=ack_count,
            now=now,
        )
        if status_filter and row["status"] != status_filter:
            continue
        rows.append(row)

    ordering = params.get("ordering") or "-publishedAt"
    desc = ordering.startswith("-")
    field = ordering[1:] if desc else ordering
    key_map = {
        "title": lambda r: r["title"].lower(),
        "publishedAt": lambda r: r["publishedAt"],
        "acknowledgmentRate": lambda r: r["acknowledgmentRate"],
        "pendingCount": lambda r: r["pendingCount"],
    }
    key_fn = key_map.get(field, key_map["publishedAt"])
    rows.sort(key=key_fn, reverse=desc)

    page = max(1, int(params.get("page") or 1))
    page_size = max(1, min(100, int(params.get("pageSize") or 10)))
    start = (page - 1) * page_size
    return {
        "results": rows[start : start + page_size],
        "count": len(rows),
        "page": page,
        "pageSize": page_size,
    }


def build_dashboard(params: dict[str, Any]) -> dict[str, Any]:
    listed = list_communications({**params, "page": 1, "pageSize": 10_000})
    rows = listed["results"]

    acknowledged = sum(r["acknowledgedCount"] for r in rows)
    pending = sum(r["pendingCount"] for r in rows)
    total = acknowledged + pending
    rate = round((acknowledged / total) * 100, 1) if total else 0.0
    active = sum(1 for r in rows if r["status"] == STATUS_EM_ANDAMENTO)
    scheduled = sum(1 for r in rows if r["status"] == STATUS_AGENDADO)
    concluded = sum(1 for r in rows if r["status"] == STATUS_CONCLUIDO)

    # Adesão por área (HC team_sector) — só notícias já publicadas
    profiles = list(eligible_profiles_qs())
    published_ids = [r["id"] for r in rows if r["status"] != STATUS_AGENDADO]
    ack_user_ids_by_news: dict[str, set] = {nid: set() for nid in published_ids}
    if published_ids:
        for row in NewsAcknowledgment.objects.filter(news_id__in=published_ids).values(
            "news_id", "user_id"
        ):
            ack_user_ids_by_news.setdefault(str(row["news_id"]), set()).add(row["user_id"])

    area_stats: dict[str, dict[str, int]] = {}
    for profile in profiles:
        dims = _hc_dims(profile)
        area = dims["area"] or "Sem área HC"
        if area not in area_stats:
            area_stats[area] = {"ack": 0, "total": 0}
        # Cada área conta 1 slot por comunicado publicado × pessoa
        for nid in published_ids:
            area_stats[area]["total"] += 1
            if profile.user_id in ack_user_ids_by_news.get(nid, set()):
                area_stats[area]["ack"] += 1

    by_area = []
    for area, stats in area_stats.items():
        rate_area = round((stats["ack"] / stats["total"]) * 100) if stats["total"] else 0
        by_area.append(
            {
                "area": area,
                "rate": rate_area,
                "acknowledged": stats["ack"],
                "total": stats["total"],
            }
        )
    by_area.sort(key=lambda x: x["rate"], reverse=True)

    attention = [
        {
            "id": f"area-{idx}",
            "label": item["area"],
            "rate": item["rate"],
            "kind": "area",
            "severity": "critical" if item["rate"] < 60 else "warn",
        }
        for idx, item in enumerate(by_area)
        if item["rate"] < 85
    ][:5]

    return {
        "kpis": {
            "acknowledgmentRate": rate,
            "acknowledgmentRateDeltaPct": 0,
            "acknowledgedCount": acknowledged,
            "totalRecipients": total,
            "pendingCount": pending,
            "pendingPct": round((pending / total) * 100, 1) if total else 0.0,
            "overdueCount": 0,
            "overdueCommunications": 0,
            "activeCommunications": active,
            "scheduledCommunications": scheduled,
            "concludedCommunications": concluded,
            "closingThisWeek": scheduled,
        },
        "adhesion": {
            "acknowledged": acknowledged,
            "pending": pending,
            "overdue": 0,
            "scheduled": scheduled,
        },
        "byArea": by_area,
        "attention": attention,
        "minGoalPct": 85,
    }


def get_detail(news_id: str) -> dict[str, Any]:
    news = critical_news_qs(include_scheduled=True).filter(pk=news_id).first()
    if not news:
        raise News.DoesNotExist
    profiles = list(eligible_profiles_qs())
    ack_count = NewsAcknowledgment.objects.filter(news=news).count()
    row = build_communication_row(
        news, total_recipients=len(profiles), acknowledged_count=ack_count
    )
    units = { _hc_dims(p)["unit"] for p in profiles if _hc_dims(p)["unit"] }
    row["unitsCount"] = len(units)
    row["deadlineLabel"] = ""
    row["timeRemainingLabel"] = (
        "Publicação agendada"
        if row["status"] == STATUS_AGENDADO
        else ("Ciência concluída" if row["status"] == STATUS_CONCLUIDO else "Aguardando ciência")
    )
    return row


def list_recipients(params: dict[str, Any]) -> dict[str, Any]:
    news_id = params["communicationId"]
    news = News.objects.filter(pk=news_id, active=True, is_critical=True).first()
    if not news:
        raise News.DoesNotExist

    ack_map = {
        row.user_id: row.acknowledged_at
        for row in NewsAcknowledgment.objects.filter(news=news).only("user_id", "acknowledged_at")
    }

    situation = params.get("situation") or "pending"
    search = (params.get("search") or "").strip().lower()
    area = (params.get("area") or "").strip()
    unit = (params.get("unit") or "").strip()
    shift = (params.get("shift") or "").strip()
    leader_id = (
        params.get("leaderId")
        or params.get("leader_id")
        or params.get("leader")
        or ""
    )
    leader_id = str(leader_id).strip()

    results: list[dict[str, Any]] = []
    for profile in eligible_profiles_qs():
        dims = _hc_dims(profile)
        if area and dims["area"] != area:
            continue
        if unit and dims["unit"] != unit:
            continue
        if shift and dims["shift"] != shift:
            continue
        if leader_id and dims["leader_id"] != leader_id:
            continue

        acknowledged_at = ack_map.get(profile.user_id)
        status = "acknowledged" if acknowledged_at else "pending"
        if situation == "pending" and status != "pending":
            continue
        if situation == "acknowledged" and status != "acknowledged":
            continue

        user = profile.user
        agent = profile.agent
        name = agent.full_name or user.get_full_name() or user.username
        email = agent.email or getattr(user, "email", "") or ""
        registration = agent.user_lan_id or ""

        if search:
            blob = f"{name} {email} {registration}".lower()
            if search not in blob:
                continue

        results.append(
            {
                "id": f"{news_id}-{profile.user_id}",
                "communicationId": str(news_id),
                "employeeId": str(agent.id),
                "employeeName": name,
                "employeeEmail": email,
                "registrationNumber": registration,
                "department": dims["team"] or dims["area"],
                "area": dims["area"],
                "unit": dims["unit"],
                "shift": dims["shift"],
                "lastAccessAt": None,
                "viewedNews": bool(acknowledged_at),
                "acknowledgedAt": acknowledged_at.isoformat() if acknowledged_at else None,
                "status": status,
                "reminderCount": 0,
                "lastReminderAt": None,
            }
        )

    results.sort(key=lambda r: r["employeeName"].lower())
    page = max(1, int(params.get("page") or 1))
    page_size = max(1, min(100, int(params.get("pageSize") or 10)))
    start = (page - 1) * page_size
    return {
        "results": results[start : start + page_size],
        "count": len(results),
        "page": page,
        "pageSize": page_size,
    }
