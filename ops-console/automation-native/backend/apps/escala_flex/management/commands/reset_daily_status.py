from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.escala_flex.services.daily_status_reset import reset_daily_status


class Command(BaseCommand):
    help = (
        "Zera status e eventos do dia (ScheduleToday, AgentStatus, StatusEvent). "
        "Agendar diariamente às 05:30."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="target_date",
            help="Data alvo (AAAA-MM-DD). Padrão: hoje.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas exibe quantos registros seriam afetados.",
        )

    def handle(self, *args, **options):
        target = options.get("target_date")
        if target:
            from datetime import date

            target_date = date.fromisoformat(target)
        else:
            target_date = timezone.localdate()

        result = reset_daily_status(target_date, dry_run=options["dry_run"])

        if result["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] {target_date}: "
                    f"{result['events_deleted']} evento(s) do dia, "
                    f"{result['stale_events_closed']} evento(s) ativo(s) antigo(s), "
                    f"{result['schedule_today_cleared']} ScheduleToday, "
                    f"{result['agent_status_cleared']} AgentStatus."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Status zerados em {target_date}: "
                f"{result['events_deleted']} evento(s) removido(s), "
                f"{result['stale_events_closed']} evento(s) antigo(s) encerrado(s), "
                f"{result['schedule_today_cleared']} escala(s) do dia limpa(s), "
                f"{result['agent_status_cleared']} AgentStatus limpo(s)."
            )
        )
