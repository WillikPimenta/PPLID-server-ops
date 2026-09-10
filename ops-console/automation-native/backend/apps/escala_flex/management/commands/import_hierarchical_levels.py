"""Importa níveis hierárquicos de dimNivelHierarquico.csv."""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.escala_flex.models import HierarchicalLevel

DEFAULT_CSV = (
    Path(__file__).resolve().parents[2] / "data" / "dimNivelHierarquico.csv"
)


class Command(BaseCommand):
    help = "Importa níveis hierárquicos (dimNivelHierarquico) para ef_hierarchical_level."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(DEFAULT_CSV),
            help="Caminho do CSV (colunas: Nivel Hierarquico, ID, Active).",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Arquivo não encontrado: {path}"))
            return

        created = 0
        updated = 0
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                name = (row.get("Nivel Hierarquico") or "").strip()
                if not name:
                    continue
                raw_id = (row.get("ID") or "").strip()
                sharepoint_id = int(raw_id) if raw_id.isdigit() else None
                active_raw = (row.get("Active") or "True").strip().lower()
                active = active_raw in ("true", "1", "yes", "sim")

                _, was_created = HierarchicalLevel.objects.update_or_create(
                    sharepoint_id=sharepoint_id,
                    defaults={"name": name, "active": active},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        total = HierarchicalLevel.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Importação concluída: {created} criados, {updated} atualizados "
                f"({total} no total)."
            )
        )
