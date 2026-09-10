"""Restaura agent_history a partir de backup JSON gerado pelo importador."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.workforce.services.agent_history_xlsx import restore_backup


class Command(BaseCommand):
    help = "Restaura a tabela agent_history a partir de um backup JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            help="Caminho do backup JSON (agent_history_backup_*.json).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Confirma a substituição pelo conteúdo do backup.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"Backup não encontrado: {path}")
        if not options["force"]:
            raise CommandError("Restauração exige --force.")

        try:
            count = restore_backup(path)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Restaurados {count} registros de agent_history."))
        self.stdout.write("Caches de Qualidade invalidados.")
