"""Consolidação da escala do dia (CreateScheduleToday + fx_colSheduleToday)."""

from datetime import date, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from apps.workforce.models import Agent, AgentHistory

from ..models import AgentStatus, BreakTime, CurrentActivity, Escala, Schedule, ScheduleToday, StatusType
from .overtime import compute_overtime_flag, hours_from_work_schedule
from .schedule_utils import (
    is_night_shift_crossing,
    is_time_range_schedule,
    normalize_time_schedule,
    resolve_default_schedule,
    resolve_escala_work_schedule,
)


def _entry_schedule_metrics(
    work_schedule: str,
    schedule: Schedule | None,
) -> tuple[Decimal, bool]:
    """working_hour e overtime para ScheduleToday (alinhado ao painel / edição de escala)."""
    if schedule and is_time_range_schedule(normalize_time_schedule(schedule.work_schedule or "")):
        return schedule.working_hour or Decimal("0"), schedule.overtime

    computed = hours_from_work_schedule(work_schedule)
    working_hour = computed if computed is not None else Decimal("0")
    overtime = compute_overtime_flag(work_schedule) if work_schedule else False
    return working_hour, overtime


class ScheduleTodayService:
    @staticmethod
    def _rebuild_lock_key(target: date) -> int:
        return hash(f"ef-schedule-today-{target.isoformat()}") & 0x7FFFFFFF

    @staticmethod
    def _acquire_rebuild_lock(target: date) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ScheduleTodayService._rebuild_lock_key(target)])

    @staticmethod
    def _active_headcount_map() -> dict[str, dict]:
        """Agentes com histórico ativo (equivalente colHeadCount)."""
        result: dict[str, dict] = {}
        histories = (
            AgentHistory.objects.filter(active=True, final_date__isnull=True)
            .select_related("agent", "leader")
            .order_by("agent__full_name")
        )
        for h in histories:
            agent = h.agent
            lan = agent.user_lan_id.lower()
            result[lan] = {
                "agent": agent,
                "full_name": agent.full_name,
                "leader": h.leader.full_name if h.leader else "",
                "leader_lan_id": h.leader.user_lan_id.lower() if h.leader else "",
                "location": h.location or "",
                "job_activity": h.job_activity or "",
                "job_title": h.job_title or "",
                "journey": h.journey or "",
                "sector": h.team_sector or "",
            }
        return result

    @staticmethod
    def _status_map() -> dict[str, AgentStatus]:
        statuses = AgentStatus.objects.select_related("status", "agent").all()
        return {s.agent.user_lan_id.lower(): s for s in statuses}

    @staticmethod
    def _current_activity_map() -> dict[str, CurrentActivity]:
        records = CurrentActivity.objects.select_related(
            "hierarchical_level",
        ).all()
        return {r.agent.user_lan_id.lower(): r for r in records}

    @staticmethod
    def _break_time_map() -> dict[str, BreakTime]:
        breaks = BreakTime.objects.filter(active=True)
        return {b.agent_lan_id.lower(): b for b in breaks}

    @staticmethod
    def _headcount_from_escala(escala: Escala) -> dict:
        leader_lan = escala.leader.user_lan_id.lower() if escala.leader else ""
        return {
            "agent": escala.agent,
            "full_name": escala.agent.full_name,
            "leader": escala.leader.full_name if escala.leader else "",
            "leader_lan_id": leader_lan,
            "location": escala.location.display_name if escala.location else "",
            "job_activity": escala.job_activity.name if escala.job_activity else "",
            "job_title": "",
            "journey": escala.horario or "",
            "sector": escala.equipe or "",
        }

    @classmethod
    def _default_headcount(cls, agent: Agent) -> dict:
        return {
            "agent": agent,
            "full_name": agent.full_name,
            "leader": "",
            "leader_lan_id": "",
            "location": "",
            "job_activity": "",
            "job_title": "",
            "journey": "",
            "sector": "",
        }

    @classmethod
    def _create_entry(
        cls,
        target: date,
        agent: Agent,
        work_schedule: str,
        is_night: bool,
        headcount: dict[str, dict],
        status_map: dict[str, AgentStatus],
        break_map: dict[str, BreakTime],
        activity_map: dict[str, CurrentActivity],
        schedule: Schedule | None = None,
        escala: Escala | None = None,
    ) -> None:
        work_schedule = normalize_time_schedule(work_schedule)
        lan = agent.user_lan_id.lower()
        if escala:
            hc = headcount.get(lan) or cls._headcount_from_escala(escala)
        else:
            hc = headcount.get(lan) or cls._default_headcount(agent)

        agent_status = status_map.get(lan)
        activity_record = activity_map.get(lan)
        bt = break_map.get(lan)

        status_type = agent_status.status if agent_status else None
        if status_type is None:
            try:
                status_type = StatusType.objects.get(pk=3)
            except StatusType.DoesNotExist:
                status_type = None

        if escala:
            location = (
                escala.location.display_name if escala.location else hc["location"]
            )
            job_activity = (
                escala.job_activity.name if escala.job_activity else hc["job_activity"]
            )
            if escala.leader:
                leader_agent = escala.leader
                leader_lan = escala.leader.user_lan_id.lower()
            else:
                leader_agent = None
                leader_lan = hc["leader_lan_id"]
            journey = resolve_default_schedule(
                horario=escala.horario,
                journey=hc["journey"],
            )
        else:
            location = hc["location"]
            job_activity = hc["job_activity"]
            leader_agent = None
            leader_lan = hc["leader_lan_id"]
            if leader_lan:
                leader_agent = Agent.objects.filter(user_lan_id__iexact=leader_lan).first()
            journey = resolve_default_schedule(journey=hc["journey"])

        working_hour, overtime = _entry_schedule_metrics(work_schedule, schedule)

        ScheduleToday.objects.create(
            agent=agent,
            date=target,
            work_schedule=work_schedule,
            working_hour=working_hour,
            overtime=overtime,
            status=status_type,
            current_activity=agent_status.current_activity if agent_status else "",
            hierarchical_level=(
                activity_record.hierarchical_level if activity_record else None
            ),
            start_of_work=agent_status.start_of_work if agent_status else None,
            week_break=bt.week if bt else "",
            weekend_break=bt.weekend if bt else "",
            is_previous_night_shift=is_night,
            leader=leader_agent,
            location=location,
            job_activity=job_activity,
            job_title=hc["job_title"],
            journey=journey,
            sector=hc["sector"],
            full_name=hc["full_name"],
            leader_lan_id=leader_lan,
            last_change=agent_status.date_of_change if agent_status else None,
        )

    @classmethod
    def _night_shifts_from_yesterday(
        cls, target: date, processed_lans: set[str]
    ) -> list[tuple[Schedule, str]]:
        yesterday = target - timedelta(days=1)
        rows: list[tuple[Schedule, str]] = []
        qs = Schedule.objects.filter(work_day=True, date=yesterday).select_related("agent")
        for schedule in qs:
            lan = schedule.agent.user_lan_id.lower()
            if lan in processed_lans:
                continue
            work_schedule = normalize_time_schedule(schedule.work_schedule)
            if not is_time_range_schedule(work_schedule):
                continue
            if not is_night_shift_crossing(work_schedule):
                continue
            rows.append((schedule, work_schedule))
        return rows

    @classmethod
    @transaction.atomic
    def build_for_date(cls, target: date | None = None) -> int:
        """
        Reconstrói ScheduleToday a partir da Escala importada:
        data = target e dia_escala no formato hh:mm - hh:mm
        (exclui FERIAS, FOLGA, BH, AFASTADO).
        """
        target = target or timezone.localdate()
        cls._acquire_rebuild_lock(target)
        headcount = cls._active_headcount_map()
        status_map = cls._status_map()
        break_map = cls._break_time_map()
        activity_map = cls._current_activity_map()

        preserved_by_agent = {
            row.agent_id: {
                "blocked": row.blocked,
                "blocked_by": row.blocked_by,
                "blocked_description": row.blocked_description,
                "blocked_at": row.blocked_at,
                "observation": row.observation,
            }
            for row in ScheduleToday.objects.filter(date=target).only(
                "agent_id",
                "blocked",
                "blocked_by",
                "blocked_description",
                "blocked_at",
                "observation",
            )
        }

        ScheduleToday.objects.filter(date=target).delete()
        created = 0
        processed_lans: set[str] = set()

        escalas = (
            Escala.objects.filter(data=target)
            .select_related("agent", "leader", "job_activity", "location")
            .order_by("agent__full_name")
        )
        for escala in escalas:
            lan = escala.agent.user_lan_id.lower()
            if lan in processed_lans:
                continue
            work_schedule = resolve_escala_work_schedule(escala.dia_escala, escala.horario)
            if not work_schedule:
                continue
            processed_lans.add(lan)
            cls._create_entry(
                target,
                escala.agent,
                work_schedule,
                False,
                headcount,
                status_map,
                break_map,
                activity_map,
                escala=escala,
            )
            created += 1

        for schedule, work_schedule in cls._night_shifts_from_yesterday(target, processed_lans):
            lan = schedule.agent.user_lan_id.lower()
            processed_lans.add(lan)
            cls._create_entry(
                target,
                schedule.agent,
                work_schedule,
                True,
                headcount,
                status_map,
                break_map,
                activity_map,
                schedule=schedule,
            )
            created += 1

        cls._restore_manual_flags(target, preserved_by_agent)
        return created

    @classmethod
    def _restore_manual_flags(
        cls,
        target: date,
        preserved_by_agent: dict,
    ) -> None:
        """Mantém bloqueio/observação definidos manualmente após rebuild do dia."""
        if not preserved_by_agent:
            return
        now = timezone.now()
        to_update: list[ScheduleToday] = []
        for entry in ScheduleToday.objects.filter(
            date=target,
            agent_id__in=preserved_by_agent.keys(),
        ):
            data = preserved_by_agent.get(entry.agent_id)
            if not data:
                continue
            if not (
                data["blocked"]
                or data["blocked_by"]
                or data["blocked_description"]
                or data["blocked_at"]
                or data["observation"]
            ):
                continue
            entry.blocked = bool(data["blocked"])
            entry.blocked_by = data["blocked_by"] or ""
            entry.blocked_description = data["blocked_description"] or ""
            entry.blocked_at = data["blocked_at"]
            entry.observation = data["observation"] or ""
            entry.updated_at = now
            to_update.append(entry)
        if to_update:
            ScheduleToday.objects.bulk_update(
                to_update,
                [
                    "blocked",
                    "blocked_by",
                    "blocked_description",
                    "blocked_at",
                    "observation",
                    "updated_at",
                ],
            )

    @classmethod
    def upsert_agent_for_date(
        cls,
        agent: Agent,
        target: date,
        *,
        escala: Escala | None = None,
        schedule: Schedule | None = None,
    ) -> ScheduleToday | None:
        """Cria ou atualiza uma linha de ScheduleToday para um agente/data."""
        if escala is None:
            escala = (
                Escala.objects.filter(agent=agent, data=target)
                .select_related("agent", "leader", "job_activity", "location")
                .first()
            )
        if escala is None:
            return None

        work_schedule = resolve_escala_work_schedule(escala.dia_escala, escala.horario)
        if not work_schedule:
            ScheduleToday.objects.filter(agent=agent, date=target).delete()
            return None

        headcount = cls._active_headcount_map()
        status_map = cls._status_map()
        break_map = cls._break_time_map()
        activity_map = cls._current_activity_map()

        existing = ScheduleToday.objects.filter(agent=agent, date=target).first()
        if existing:
            working_hour, overtime = _entry_schedule_metrics(work_schedule, schedule)
            existing.work_schedule = work_schedule
            existing.working_hour = working_hour
            existing.overtime = overtime
            existing.save(
                update_fields=["work_schedule", "working_hour", "overtime", "updated_at"]
            )
            return existing

        cls._create_entry(
            target,
            agent,
            work_schedule,
            False,
            headcount,
            status_map,
            break_map,
            activity_map,
            schedule=schedule,
            escala=escala,
        )
        return ScheduleToday.objects.filter(agent=agent, date=target).first()

    @classmethod
    def sync_schedule_metrics(cls, target: date | None = None) -> int:
        """Corrige working_hour e overtime em linhas já existentes (uso offline/rebuild).

        Não chamar em GET de API — sob carga de polling satura DB. Preferir
        build_for_date / upsert_agent_for_date, que já gravam as métricas.
        """
        target = target or timezone.localdate()
        now = timezone.now()
        to_update: list[ScheduleToday] = []
        for entry in ScheduleToday.objects.filter(date=target).only(
            "id", "work_schedule", "working_hour", "overtime"
        ):
            working_hour, overtime = _entry_schedule_metrics(entry.work_schedule, None)
            if entry.working_hour == working_hour and entry.overtime == overtime:
                continue
            entry.working_hour = working_hour
            entry.overtime = overtime
            entry.updated_at = now
            to_update.append(entry)
        if to_update:
            ScheduleToday.objects.bulk_update(
                to_update,
                ["working_hour", "overtime", "updated_at"],
                batch_size=500,
            )
        return len(to_update)

    @classmethod
    def sync_agent_break_times(cls, agent: Agent) -> None:
        """Atualiza intervalos na escala do dia (fx_schedule)."""
        today = timezone.localdate()
        bt = BreakTime.objects.filter(
            agent_lan_id__iexact=agent.user_lan_id,
            active=True,
        ).first()
        if not bt:
            return
        ScheduleToday.objects.filter(agent=agent, date=today).update(
            week_break=bt.week,
            weekend_break=bt.weekend,
        )
