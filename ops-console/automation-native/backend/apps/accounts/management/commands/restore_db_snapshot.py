import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Restaura o banco a partir de backend/data/db_snapshot.json. "
        "Execute após migrate em uma base PostgreSQL vazia."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="data/db_snapshot.json",
            help="Caminho do fixture (relativo ao diretório backend/).",
        )
        parser.add_argument(
            "--skip-migrate",
            action="store_true",
            help="Não executa migrate antes do restore.",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Apaga todos os dados existentes antes de importar (use com cuidado).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas valida o arquivo JSON sem importar.",
        )

    def handle(self, *args, **options):
        backend_dir = Path(settings.BASE_DIR)
        input_path = backend_dir / options["input"]

        if not input_path.exists():
            raise CommandError(
                f"Arquivo não encontrado: {input_path}\n"
                "Na máquina de origem, execute: python manage.py export_db_snapshot"
            )

        try:
            records = json.loads(input_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"JSON inválido em {input_path}: {exc}") from exc

        if not isinstance(records, list):
            raise CommandError("O fixture deve ser uma lista JSON de objetos Django.")

        self.stdout.write(f"Fixture: {input_path}")
        self.stdout.write(f"Registros no arquivo: {len(records)}")

        manifest_path = input_path.with_name("db_snapshot_manifest.json")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.stdout.write(f"Exportado em: {manifest.get('exported_at', '—')}")
            for model, count in sorted(manifest.get("django_models", {}).items()):
                self.stdout.write(f"  {model}: {count}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run: nenhum dado foi importado."))
            return

        if not options["skip_migrate"]:
            self.stdout.write("Executando migrate...")
            call_command("migrate", interactive=False, verbosity=1)

        if options["flush"]:
            self.stdout.write(self.style.WARNING("Flush: removendo dados existentes..."))
            call_command("flush", interactive=False, verbosity=0)

        self.stdout.write("Importando dados...")
        with transaction.atomic():
            call_command("loaddata", str(input_path), verbosity=1)

        self.stdout.write(self.style.SUCCESS("Restore concluído com sucesso."))
        self.stdout.write(
            "Se houver imagens de notícias, copie backend/media/ da máquina de origem."
        )
