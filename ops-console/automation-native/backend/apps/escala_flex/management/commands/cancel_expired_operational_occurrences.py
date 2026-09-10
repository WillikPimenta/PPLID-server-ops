from django.core.management.base import BaseCommand

from apps.escala_flex.services.occurrence_auto_cancel import run_occurrence_auto_cancels


class Command(BaseCommand):
    help = (
        "Cancela ocorrências pendentes que expiraram sem aprovação ou recusa."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista ocorrências que seriam canceladas sem persistir alterações.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        result = run_occurrence_auto_cancels(dry_run=dry_run)

        if result.get("skipped"):
            self.stdout.write("Outro processo já está executando o cancelamento.")
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] {result['cancelled']} ocorrência(s) seriam canceladas."
                )
            )
        elif result["cancelled"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{result['cancelled']} ocorrência(s) canceladas automaticamente."
                )
            )
        else:
            self.stdout.write("Nenhuma ocorrência pendente expirada.")

        if result["ids"]:
            self.stdout.write(", ".join(result["ids"]))
