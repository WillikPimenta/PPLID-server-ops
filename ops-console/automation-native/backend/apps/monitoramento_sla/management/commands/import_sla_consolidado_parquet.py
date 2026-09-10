# -*- coding: utf-8 -*-
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.monitoramento_sla.services.import_consolidado_parquet import (
    import_consolidado_parquet,
    kpi_from_db,
    kpi_from_parquet,
)


class Command(BaseCommand):
    help = "Importa consolidado Parquet (ex.: monitoramento_sla/FY26.parquet) e compara KPIs."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default="",
            help="Caminho do parquet (default: monitoramento_sla/FY26.parquet na raiz do repo)",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Não truncar sla_util_consolidado antes do import",
        )

    def handle(self, *args, **options):
        raw = (options.get("path") or "").strip()
        if raw:
            path = Path(raw)
        else:
            # backend/ → repo root
            path = Path(__file__).resolve().parents[5] / "monitoramento_sla" / "FY26.parquet"
            if not path.is_file():
                path = Path(__file__).resolve().parents[5] / "monitoramento_sla" / "consolidado.parquet"
        if not path.is_file():
            raise CommandError(f"Arquivo não encontrado: {path}")

        self.stdout.write(f"Parquet KPIs pré-carga: {kpi_from_parquet(path)}")
        run, report = import_consolidado_parquet(
            path,
            replace=not options["keep"],
        )
        self.stdout.write(self.style.SUCCESS(run.message))
        self.stdout.write(f"DB KPIs: {kpi_from_db()}")
        self.stdout.write(f"Match: {report.get('kpis_match')}")
