import json
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Exporta todos os dados do banco para backend/data/db_snapshot.json "
        "(fixture Django) e gera db_snapshot_manifest.json com metadados."
    )

    EXCLUDE = [
        "contenttypes",
        "auth.permission",
        "sessions.session",
        "admin.logentry",
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="data/db_snapshot.json",
            help="Caminho do arquivo JSON de saída (relativo ao diretório backend/).",
        )
        parser.add_argument(
            "--indent",
            type=int,
            default=2,
            help="Indentação do JSON (0 = compacto).",
        )

    def handle(self, *args, **options):
        backend_dir = Path(settings.BASE_DIR)
        output_path = backend_dir / options["output"]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        buffer = StringIO()
        call_command(
            "dumpdata",
            natural_foreign=True,
            natural_primary=True,
            exclude=self.EXCLUDE,
            indent=options["indent"],
            stdout=buffer,
        )

        payload = buffer.getvalue()
        output_path.write_text(payload, encoding="utf-8")

        records = json.loads(payload)
        counts = Counter(item["model"] for item in records)
        table_counts = self._postgres_table_counts()

        manifest = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "database": settings.DATABASES["default"]["NAME"],
            "fixture_file": str(output_path.relative_to(backend_dir)).replace("\\", "/"),
            "total_records": len(records),
            "django_models": dict(sorted(counts.items())),
            "postgres_tables": dict(sorted(table_counts.items())),
            "excluded_from_export": self.EXCLUDE,
            "notes": [
                "Permissões Django (auth.permission) são recriadas pelo migrate.",
                "Copie também a pasta backend/media/ se houver imagens de notícias.",
                "Na máquina destino: python manage.py migrate && python manage.py restore_db_snapshot",
            ],
        }

        manifest_path = output_path.with_name("db_snapshot_manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        self.stdout.write(self.style.SUCCESS(f"Snapshot exportado: {output_path}"))
        self.stdout.write(self.style.SUCCESS(f"Manifesto gerado: {manifest_path}"))
        self.stdout.write(f"Total de registros: {len(records)}")
        for model, count in sorted(counts.items()):
            self.stdout.write(f"  {model}: {count}")

    def _postgres_table_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[table] = cursor.fetchone()[0]
        return counts
