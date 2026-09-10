"""Job diário: reconstrói escala do dia (equivalente cron/Celery)."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.escala_flex.services import ScheduleTodayService


class Command(BaseCommand):
    help = "Reconstrói tblScheduleToday para hoje (agendar via cron diário)."

    def handle(self, *args, **options):
        today = timezone.localdate()
        count = ScheduleTodayService.build_for_date(today)
        self.stdout.write(
            self.style.SUCCESS(f"Escala {today} reconstruída: {count} registros.")
        )
