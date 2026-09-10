"""KPIs operacionais (fx_colStatusCount, colHECount, colNHCount, colStatusOperation)."""

from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from ..models import ScheduleToday, StatusType
from .schedule_utils import is_shift_active_at, is_time_range_schedule

AVAILABLE_STATUS_ID = 1
LOGGED_OUT_STATUS_ID = 3
PAUSE_STATUS_IDS = frozenset({2, 4, 5, 6, 7, 8, 9, 10, 11, 13})


def compute_status_counts(target_date=None) -> list[dict]:
    target_date = target_date or timezone.localdate()

    status_types = (
        StatusType.objects.filter(active=True)
        .exclude(pk=LOGGED_OUT_STATUS_ID)
        .order_by("id")
    )
    qs = ScheduleToday.objects.filter(date=target_date).select_related("status")

    counts: dict[int, int] = {}
    for row in qs:
        if row.start_of_work and row.start_of_work.date() == target_date:
            sid = row.status_id or LOGGED_OUT_STATUS_ID
            if sid == LOGGED_OUT_STATUS_ID:
                continue
            counts[sid] = counts.get(sid, 0) + 1

    return [
        {
            "id": st.id,
            "status_name": st.name,
            "color": st.color,
            "count": counts.get(st.id, 0),
        }
        for st in status_types
    ]


def compute_he_counts(target_date=None) -> list[dict]:
    target_date = target_date or timezone.localdate()
    qs = (
        ScheduleToday.objects.filter(date=target_date)
        .exclude(status_id=3)
        .filter(working_hour__gt=Decimal("6"))
        .values("job_activity")
        .annotate(count=Count("id"))
        .order_by("job_activity")
    )
    return [{"value": r["job_activity"] or "—", "count": r["count"]} for r in qs]


def compute_nh_counts(target_date=None) -> list[dict]:
    target_date = target_date or timezone.localdate()
    qs = (
        ScheduleToday.objects.filter(date=target_date)
        .exclude(status_id=3)
        .values("hierarchical_level__name")
        .annotate(count=Count("id"))
        .order_by("hierarchical_level__name")
    )
    return [
        {"value": r["hierarchical_level__name"] or "—", "count": r["count"]} for r in qs
    ]


def compute_operation_counts(reference_time=None, target_date=None) -> list[dict]:
    target_date = target_date or timezone.localdate()
    reference_time = reference_time or timezone.now()

    qs = ScheduleToday.objects.filter(date=target_date).select_related("agent")
    scheduled_now = 0
    absences_now = 0

    for row in qs:
        if not is_shift_active_at(
            reference_time,
            target_date,
            row.work_schedule,
            is_previous_night_shift=row.is_previous_night_shift,
        ):
            continue

        scheduled_now += 1
        sow = row.start_of_work
        if sow is None or sow > reference_time:
            absences_now += 1
        elif row.status_id in (None, LOGGED_OUT_STATUS_ID):
            absences_now += 1

    present_now = max(0, scheduled_now - absences_now)

    return [
        {"id": 1, "value": "Escalados", "count": scheduled_now},
        {"id": 2, "value": "Ausências", "count": absences_now},
        {"id": 3, "value": "Presentes", "count": present_now},
    ]


def compute_day_capacity(target_date=None) -> dict:
    """Capacidade no dia: escalados com horário vs ausentes (sem início de jornada)."""
    target_date = target_date or timezone.localdate()
    qs = ScheduleToday.objects.filter(date=target_date)

    scheduled = 0
    present = 0
    for row in qs:
        if not is_time_range_schedule(row.work_schedule or ""):
            continue
        scheduled += 1
        if (
            row.start_of_work
            and row.start_of_work.date() == target_date
            and row.status_id not in (None, LOGGED_OUT_STATUS_ID)
        ):
            present += 1

    absent = max(0, scheduled - present)
    center_pct = round((present / scheduled) * 100) if scheduled else 0

    return {
        "scheduled": scheduled,
        "present": present,
        "absent": absent,
        "center_pct": center_pct,
        "center_label": "presentes",
        "slices": [
            {
                "label": "Escalados",
                "count": scheduled,
                "color": "#e5e7eb",
                "legend_only": True,
            },
            {
                "label": "Presentes",
                "count": present,
                "chart_count": present,
                "color": "#1e3a5f",
            },
            {
                "label": "Ausentes",
                "count": absent,
                "chart_count": absent,
                "color": "#e5e7eb",
                "info_only": True,
            },
        ],
    }


def compute_now_capacity(reference_time=None, target_date=None) -> dict:
    """Capacidade agora: disponíveis vs em pausa (no turno e com jornada iniciada)."""
    target_date = target_date or timezone.localdate()
    reference_time = reference_time or timezone.now()
    qs = ScheduleToday.objects.filter(date=target_date)

    available = 0
    on_pause = 0
    for row in qs:
        if not is_shift_active_at(
            reference_time,
            target_date,
            row.work_schedule,
            is_previous_night_shift=row.is_previous_night_shift,
        ):
            continue
        sow = row.start_of_work
        if sow is None or sow > reference_time:
            continue
        if row.status_id in (None, LOGGED_OUT_STATUS_ID):
            continue
        if row.status_id == AVAILABLE_STATUS_ID:
            available += 1
        elif row.status_id in PAUSE_STATUS_IDS:
            on_pause += 1

    active_total = available + on_pause
    center_pct = round((available / active_total) * 100) if active_total else 0

    return {
        "available": available,
        "on_pause": on_pause,
        "center_pct": center_pct,
        "center_label": "disponíveis",
        "slices": [
            {"label": "Disponíveis", "count": available, "color": "#1e3a5f"},
            {"label": "Em pausa", "count": on_pause, "color": "#e5e7eb"},
        ],
    }


def compute_all_kpis(reference_time=None, target_date=None) -> dict:
    return {
        "status_counts": compute_status_counts(target_date),
        "he_counts": compute_he_counts(target_date),
        "nh_counts": compute_nh_counts(target_date),
        "operation_counts": compute_operation_counts(reference_time, target_date),
        "capacity_day": compute_day_capacity(target_date),
        "capacity_now": compute_now_capacity(reference_time, target_date),
    }
