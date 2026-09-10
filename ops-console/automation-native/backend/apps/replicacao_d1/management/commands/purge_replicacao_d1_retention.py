# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand

from apps.replicacao_d1.services.retention import purge_replicacao_d1_retention


class Command(BaseCommand):
    help = (
        "Aplica retenção de logs técnicos D-1 (sync_log, lotes de ingestão). "
        "Não remove runs, protocolos nem replicados."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas reporta quantos registros seriam removidos.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        report = purge_replicacao_d1_retention(dry_run=dry_run)
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Sync logs removíveis: {report.sync_logs_deleted} "
            f"(antes de {report.sync_log_cutoff})"
        )
        self.stdout.write(
            f"{prefix}Lotes de ingestão removíveis: {report.ingestions_deleted} "
            f"(antes de {report.ingestion_cutoff})"
        )
