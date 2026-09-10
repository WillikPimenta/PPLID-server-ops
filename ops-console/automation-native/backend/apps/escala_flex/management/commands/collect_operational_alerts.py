from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.escala_flex.services.operational_alert_history import collect_operational_alerts


class Command(BaseCommand):
    help = "Coleta e persiste o ciclo de vida dos alertas do painel operacional."

    def add_arguments(self, parser):
        parser.add_argument("--date", dest="target_date", help="Data operacional YYYY-MM-DD.")
        parser.add_argument("--agent", dest="agent_lan_id", default="", help="LAN ID opcional.")
        parser.add_argument("--dry-run", action="store_true", help="Simula sem gravar alterações.")

    def handle(self, *args, **options):
        target_date = None
        if options.get("target_date"):
            try:
                target_date = date.fromisoformat(options["target_date"])
            except ValueError as exc:
                raise CommandError("Data inválida. Use YYYY-MM-DD.") from exc

        result = collect_operational_alerts(
            target_date=target_date,
            agent_lan_id=options.get("agent_lan_id") or "",
            dry_run=bool(options.get("dry_run")),
        )
        prefix = "[dry-run] " if result["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{result['date']}: {result['evaluated_agents']} agente(s), "
                f"{result['alerts']} alerta(s), {result['created']} criado(s), "
                f"{result['updated']} atualizado(s), {result['resolved']} resolvido(s)."
            )
        )
