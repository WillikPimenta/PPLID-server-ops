"""Edição manual de escala publicada (Escala + sync Schedule / ScheduleToday)."""

from decimal import Decimal

from django.db import transaction

from apps.workforce.models import Agent

from ..models import Escala, Schedule, ScheduleToday
from .overtime import (
    compute_overtime_flag,
    hours_from_work_schedule,
    validate_schedule_overtime,
)
from .schedule_today import ScheduleTodayService
from .schedule_utils import (
    is_absence_dia_escala,
    is_time_range_schedule,
    normalize_time_schedule,
    resolve_default_schedule,
    resolve_escala_work_schedule,
)
from .absence_types import get_active_absence_codes, normalize_absence_code, resolve_absence_code, resolve_absence_code
from .permissions import get_active_history


def map_horario_dia_to_schedule(horario: str, dia_escala: str) -> tuple[str, bool]:
    """Mesma regra do importador Excel."""
    dia = str(dia_escala or "").strip()
    hor = str(horario or "").strip()
    if dia:
        normalized = normalize_time_schedule(dia)
        if is_time_range_schedule(normalized):
            return normalized, True
        if is_absence_dia_escala(dia):
            return "", False
    if hor:
        normalized = normalize_time_schedule(hor)
        if is_time_range_schedule(normalized):
            return normalized, True
    return "", False


class PublishedEscalaEditService:
    @staticmethod
    def normalize_dia_escala(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Informe o horário ou o código de ausência.")
        if is_absence_dia_escala(text):
            return resolve_absence_code(text) or normalize_absence_code(text)
        normalized = normalize_time_schedule(text)
        if is_time_range_schedule(normalized):
            return normalized
        raise ValueError(
            "Escala inválida. Use o formato hh:mm - hh:mm ou "
            + ", ".join(sorted(get_active_absence_codes()))
            + "."
        )

    @classmethod
    def get_or_create_escala_for_agent(cls, agent: Agent, target_date) -> Escala:
        escala = (
            Escala.objects.filter(agent=agent, data=target_date)
            .select_related("agent", "leader", "job_activity", "location")
            .first()
        )
        if escala:
            return escala

        history = get_active_history(agent)
        horario = (history.journey if history else "") or ""
        equipe = (history.team_sector if history else "") or ""
        leader = history.leader if history else None
        return Escala.objects.create(
            agent=agent,
            leader=leader,
            data=target_date,
            dia_escala="",
            horario=horario,
            equipe=equipe,
        )

    @classmethod
    def get_escala_for_schedule_today(cls, entry: ScheduleToday) -> Escala | None:
        return (
            Escala.objects.filter(agent=entry.agent, data=entry.date)
            .select_related("agent", "leader", "job_activity", "location")
            .first()
        )

    @classmethod
    @transaction.atomic
    def apply_changes(
        cls,
        escala: Escala,
        *,
        dia_escala: str | None = None,
        week: str | None = None,
        weekend: str | None = None,
    ) -> tuple[Escala, ScheduleToday | None]:
        if dia_escala is not None:
            escala.dia_escala = cls.normalize_dia_escala(dia_escala)
            escala.save(update_fields=["dia_escala", "updated_at"])

        work_schedule, work_day = map_horario_dia_to_schedule(
            escala.horario or "",
            escala.dia_escala or "",
        )

        if dia_escala is not None and work_schedule:
            validate_schedule_overtime(work_schedule)

        schedule, _ = Schedule.objects.get_or_create(
            agent=escala.agent,
            date=escala.data,
            defaults={
                "work_schedule": work_schedule,
                "work_day": work_day,
                "working_hour": Decimal("0"),
                "overtime": False,
            },
        )
        schedule.work_schedule = work_schedule
        schedule.work_day = work_day

        if work_schedule:
            computed = hours_from_work_schedule(work_schedule)
            if computed is not None:
                schedule.working_hour = computed
            schedule.overtime = compute_overtime_flag(work_schedule)
        elif not work_day:
            schedule.working_hour = Decimal("0")
            schedule.overtime = False

        schedule.save()

        schedule_for_break = work_schedule or resolve_default_schedule(
            horario=escala.horario or "",
            journey="",
        )
        cls._sync_break_times(
            escala,
            week=week,
            weekend=weekend,
            work_schedule=schedule_for_break,
        )

        resolved = resolve_escala_work_schedule(escala.dia_escala, escala.horario)
        if not resolved:
            ScheduleToday.objects.filter(agent=escala.agent, date=escala.data).delete()
            return escala, None

        schedule_today = ScheduleToday.objects.filter(
            agent=escala.agent,
            date=escala.data,
        ).first()

        if schedule_today:
            schedule_today.work_schedule = resolved
            schedule_today.overtime = schedule.overtime
            if schedule.working_hour is not None:
                schedule_today.working_hour = schedule.working_hour
            schedule_today.save(
                update_fields=[
                    "work_schedule",
                    "overtime",
                    "working_hour",
                    "updated_at",
                ]
            )
            if week is not None or weekend is not None:
                schedule_today.refresh_from_db()
            return escala, schedule_today

        ScheduleTodayService.upsert_agent_for_date(
            escala.agent,
            escala.data,
            escala=escala,
            schedule=schedule,
        )
        schedule_today = ScheduleToday.objects.filter(
            agent=escala.agent,
            date=escala.data,
        ).first()
        return escala, schedule_today

    @classmethod
    def _sync_break_times(
        cls,
        escala: Escala,
        *,
        week: str | None,
        weekend: str | None,
        work_schedule: str,
    ) -> None:
        if week is None and weekend is None:
            return

        from .break_times import resolve_agent_default_schedule, upsert_break_time

        schedule_for_break = work_schedule
        if not is_time_range_schedule(normalize_time_schedule(schedule_for_break or "")):
            schedule_for_break = resolve_agent_default_schedule(escala.agent, escala=escala)

        upsert_break_time(
            escala.agent,
            week=week,
            weekend=weekend,
            work_schedule=schedule_for_break or None,
        )
