from django.core.management.base import BaseCommand

from apps.planejamento_demandas.services.sync import sync_jira_demandas


class Command(BaseCommand):
    help = "Sincroniza demandas Jira (PPLID e demais projetos configurados) para o portal."

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="Ignora o checkpoint da última execução e usa a janela completa configurada.",
        )

    def handle(self, *args, **options):
        run = sync_jira_demandas(force_full=bool(options.get("full")))
        self.stdout.write(
            self.style.SUCCESS(
                f"OK: {run.total_fetched} issues ({run.created_count} novas, "
                f"{run.updated_count} atualizadas, {run.duplicate_count} duplicadas ignoradas) "
                f"em {run.duration_seconds}s"
            )
        )
