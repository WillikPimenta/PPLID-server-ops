"""Propagação de NH (nível hierárquico) sem alterar atividade do agente."""

from django.utils import timezone

from apps.workforce.models import Agent

from ..models import CurrentActivity, HierarchicalLevel, ScheduleToday


def apply_nh_change(
    agent: Agent,
    level: HierarchicalLevel | None,
    *,
    schedule_today_id=None,
) -> bool:
    """Atualiza somente o nível hierárquico (ScheduleToday + CurrentActivity).

    Retorna False se a escala do dia estiver bloqueada (NH não é alterado).
    """
    today = timezone.localdate()
    now = timezone.now()

    schedule_qs = ScheduleToday.objects.filter(agent=agent, date=today)
    if schedule_today_id:
        schedule_qs = schedule_qs.filter(pk=schedule_today_id)

    entry = schedule_qs.first()
    if entry is not None and entry.blocked:
        return False

    schedule_qs.update(hierarchical_level=level)

    ca, _ = CurrentActivity.objects.get_or_create(agent=agent)
    ca.hierarchical_level = level
    ca.date_of_change = now
    ca.save(update_fields=["hierarchical_level", "date_of_change", "updated_at"])
    return True
