# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.dimensoes_processos.services.derivacao_etapa.scan_comparativo import (
    scan_derivacao_etapa_comparativo,
)
from apps.dimensoes_processos.services.derivacao_etapa.sync import import_derivacao_etapa_csv


class Command(BaseCommand):
    help = "Importa ou escaneia CSVs derivacao_etapa/FINALIZADO_*.csv."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default="",
            help="Diretório dos CSVs (default: DERIVACAO_ETAPA_CSV_DIR / derivacao_etapa/)",
        )
        parser.add_argument(
            "--file",
            default="",
            help="Importar um único arquivo (ex.: FINALIZADO_20260501.csv)",
        )
        parser.add_argument(
            "--from-date",
            default="",
            help="Filtrar arquivos a partir desta data (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--to-date",
            default="",
            help="Filtrar arquivos até esta data (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Valida leitura e match Megazord sem gravar no banco",
        )
        parser.add_argument(
            "--scan",
            action="store_true",
            help="Executa scan comparativo (distinct por dimensão)",
        )

    def handle(self, *args, **options):
        raw_dir = (options.get("dir") or "").strip()
        directory = Path(raw_dir) if raw_dir else Path(settings.DERIVACAO_ETAPA_CSV_DIR)

        from_date = self._parse_date(options.get("from_date") or "")
        to_date = self._parse_date(options.get("to_date") or "")
        file_name = (options.get("file") or "").strip()

        try:
            if options.get("scan"):
                run, metrics = scan_derivacao_etapa_comparativo(
                    directory=directory,
                    file_name=file_name,
                    from_date=from_date,
                    to_date=to_date,
                )
                self.stdout.write(self.style.SUCCESS(run.message))
                self.stdout.write(f"Métricas: {metrics.as_dict()}")
                return

            run, metrics = import_derivacao_etapa_csv(
                directory=directory,
                file_name=file_name,
                from_date=from_date,
                to_date=to_date,
                dry_run=bool(options.get("dry_run")),
            )
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        if options.get("dry_run"):
            self.stdout.write(self.style.WARNING("Dry-run — nada gravado."))
        elif run is not None:
            self.stdout.write(self.style.SUCCESS(run.message))

        self.stdout.write(f"Métricas: {metrics.as_dict()}")

    @staticmethod
    def _parse_date(raw: str):
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(f"Data inválida: {raw} (use YYYY-MM-DD)") from exc
