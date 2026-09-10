from django.core.management.base import BaseCommand

from apps.escala_flex.services.status_timeout import run_status_timeouts


class Command(BaseCommand):
    help = (
        "Encerra status de agentes que permanecem logados após o horário de saída da escala."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista agentes que seriam encerrados sem persistir alterações.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        result = run_status_timeouts(dry_run=dry_run)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] {result['closed']} agente(s) seriam encerrados."
                )
            )
        elif result["closed"]:
            self.stdout.write(
                self.style.SUCCESS(f"{result['closed']} agente(s) encerrados.")
            )
        else:
            self.stdout.write("Nenhum agente para encerrar.")

        if result["agents"]:
            self.stdout.write(", ".join(result["agents"]))
