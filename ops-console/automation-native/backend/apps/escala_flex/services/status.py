"""Operações de status (login, pausa, eventos)."""

from django.db import transaction
from django.utils import timezone

from apps.workforce.models import Agent

from ..models import AgentStatus, ScheduleToday, StatusEvent, StatusType

_STATUS_TYPES_HINT = (
    "Execute: python manage.py sync_sharepoint --status-types-only"
)


class StatusConfigurationError(Exception):
    """Tipos de status ausentes em ef_status_type."""


class StatusService:
    LOGGED_OUT_ID = 3
    AVAILABLE_ID = 1

    @classmethod
    def _get_status_type(cls, status_id: int) -> StatusType:
        try:
            return StatusType.objects.get(pk=status_id)
        except StatusType.DoesNotExist as exc:
            raise StatusConfigurationError(
                f"Tipo de status {status_id} não encontrado. {_STATUS_TYPES_HINT}"
            ) from exc

    @staticmethod
    def _close_active_event(active_event: StatusEvent, final_date) -> None:
        active_event.final_date = final_date
        active_event.active_event = False
        delta = final_date - active_event.start_date
        active_event.total_duration = max(0, int(delta.total_seconds()))
        active_event.save(
            update_fields=["final_date", "active_event", "total_duration"]
        )

    @staticmethod
    def _earliest_event_start_on_date(agent: Agent, target_date):
        """Primeiro StatusEvent do dia (timezone local) — recupera histórico se start_of_work foi sobrescrito."""
        for start in (
            StatusEvent.objects.filter(agent=agent)
            .order_by("start_date")
            .values_list("start_date", flat=True)
            .iterator()
        ):
            if timezone.localtime(start).date() == target_date:
                return start
        return None

    @classmethod
    @transaction.atomic
    def start_shift(cls, agent: Agent, schedule_today: ScheduleToday) -> None:
        now = timezone.now()
        available = cls._get_status_type(cls.AVAILABLE_ID)

        active_event = StatusEvent.objects.filter(
            agent=agent,
            active_event=True,
        ).first()
        if active_event:
            cls._close_active_event(active_event, now)

        # Preserva / recupera o primeiro login do dia para não apagar o histórico na barra.
        existing_sow = schedule_today.start_of_work
        if existing_sow is not None and timezone.localtime(existing_sow).date() == schedule_today.date:
            start_of_work = existing_sow
        else:
            start_of_work = now

        earliest = cls._earliest_event_start_on_date(agent, schedule_today.date)
        if earliest is not None and earliest < start_of_work:
            start_of_work = earliest

        agent_status, _ = AgentStatus.objects.get_or_create(agent=agent)
        agent_status.status = available
        agent_status.start_of_work = start_of_work
        agent_status.date_of_change = now
        agent_status.save()

        schedule_today.status = available
        schedule_today.start_of_work = start_of_work
        schedule_today.last_change = now
        schedule_today.save(
            update_fields=["status", "start_of_work", "last_change", "updated_at"]
        )

        StatusEvent.objects.create(
            agent=agent,
            leader=schedule_today.leader,
            status=available,
            start_date=now,
            active_event=True,
        )

    @classmethod
    @transaction.atomic
    def change_status(
        cls,
        agent: Agent,
        schedule_today: ScheduleToday,
        status_id: int,
        leader: Agent | None = None,
    ) -> StatusEvent | None:
        now = timezone.now()
        status_type = cls._get_status_type(status_id)

        active_event = StatusEvent.objects.filter(
            agent=agent,
            active_event=True,
        ).first()
        if active_event:
            cls._close_active_event(active_event, now)

        if status_id == cls.LOGGED_OUT_ID:
            agent_status, _ = AgentStatus.objects.get_or_create(agent=agent)
            agent_status.status = status_type
            agent_status.date_of_change = now
            agent_status.save(update_fields=["status", "date_of_change", "updated_at"])

            schedule_today.status = status_type
            schedule_today.last_change = now
            schedule_today.save(update_fields=["status", "last_change", "updated_at"])

            # Evento ativo de Deslogado: período sem preenchimento na barra, histórico intacto.
            return StatusEvent.objects.create(
                agent=agent,
                leader=leader or schedule_today.leader,
                status=status_type,
                start_date=now,
                active_event=True,
            )

        agent_status, _ = AgentStatus.objects.get_or_create(agent=agent)
        agent_status.status = status_type
        agent_status.date_of_change = now
        agent_status.save()

        schedule_today.status = status_type
        schedule_today.last_change = now
        schedule_today.save(update_fields=["status", "last_change", "updated_at"])

        return StatusEvent.objects.create(
            agent=agent,
            leader=leader or schedule_today.leader,
            status=status_type,
            start_date=now,
            active_event=True,
        )

    @classmethod
    @transaction.atomic
    def auto_end_shift(
        cls,
        agent: Agent,
        schedule_today: ScheduleToday,
        end_at,
    ) -> bool:
        """Encerra evento ativo e define agente como Deslogado após fim da escala."""
        logged_out = StatusType.objects.filter(pk=cls.LOGGED_OUT_ID).first()
        if not logged_out:
            return False

        active_event = StatusEvent.objects.filter(
            agent=agent,
            active_event=True,
        ).first()
        if active_event:
            cls._close_active_event(active_event, end_at)

        agent_status, _ = AgentStatus.objects.get_or_create(agent=agent)
        agent_status.status = logged_out
        agent_status.date_of_change = end_at
        agent_status.save(update_fields=["status", "date_of_change", "updated_at"])

        schedule_today.status = logged_out
        schedule_today.last_change = end_at
        schedule_today.save(update_fields=["status", "last_change", "updated_at"])

        StatusEvent.objects.create(
            agent=agent,
            leader=schedule_today.leader,
            status=logged_out,
            start_date=end_at,
            active_event=True,
        )
        return True

    @classmethod
    def get_initial_agent_status(cls, schedule_today: ScheduleToday) -> dict:
        if schedule_today.status_id and schedule_today.status_id != cls.LOGGED_OUT_ID:
            return {
                "status": schedule_today.status_id,
                "name": schedule_today.status.name if schedule_today.status else "",
            }
        return {"status": cls.LOGGED_OUT_ID, "name": "Deslogado"}
