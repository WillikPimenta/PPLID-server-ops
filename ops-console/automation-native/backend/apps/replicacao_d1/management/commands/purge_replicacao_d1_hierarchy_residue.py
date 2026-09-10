# -*- coding: utf-8 -*-
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.replicacao_d1.services.hierarchy_cleanup import cleanup_replicacao_d1_hierarchy_residue


class Command(BaseCommand):
    help = (
        "Remove resíduos das migrações 0023/0024: reativa segmentos, consolida HIGH/LOW/MID "
        "e apaga duplicatas inativas de segmento/categoria."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas estima o impacto sem alterar o banco.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica a limpeza (default: dry-run).",
        )

    def handle(self, *args, **options):
        dry_run = not options.get("apply")
        if options.get("dry_run"):
            dry_run = True

        report = cleanup_replicacao_d1_hierarchy_residue(dry_run=dry_run)
        prefix = "[dry-run] " if dry_run else ""

        if report.errors:
            for err in report.errors:
                self.stderr.write(self.style.ERROR(err))
            return

        self.stdout.write(f"{prefix}Segmentos reativados: {report.segmentos_reactivated}")
        self.stdout.write(f"{prefix}Categorias consolidadas/removidas: {report.categorias_consolidated}")
        self.stdout.write(f"{prefix}Clientes relinkados: {report.clientes_relinked}")
        self.stdout.write(f"{prefix}Segmentos resíduo removidos: {report.segmentos_deleted}")
        self.stdout.write(f"{prefix}Categorias resíduo removidas: {report.categorias_deleted}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Nenhuma alteração gravada. Use --apply para executar."))
        else:
            self.stdout.write(self.style.SUCCESS("Limpeza concluída."))
