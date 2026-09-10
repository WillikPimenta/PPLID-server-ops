# -*- coding: utf-8 -*-
"""Backfill estruturado + re-sync completo após bump de MAPPING_VERSION."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.qualidade_operacional.services.dim_aliases import seed_default_dim_aliases
from apps.qualidade_operacional.services.intranet_source import (
    build_cascade_reconciliation,
    eligible_sources_qs,
    sync_queryset,
)
from apps.qualidade_operacional.services.source_config import MAPPING_VERSION


class Command(BaseCommand):
    help = (
        "Backfill data_analise de contestações (analise_concluida_em) e "
        f"reprocessa projeções Intranet com mapping_version={MAPPING_VERSION}."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-backfill", action="store_true")
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options) -> None:
        dry_run = bool(options["dry_run"])
        batch_size = max(1, int(options["batch_size"] or 200))

        seeded = seed_default_dim_aliases(force=True)
        self.stdout.write(self.style.NOTICE(f"Aliases sincronizados/criados: {seeded}"))

        if not options["skip_backfill"]:
            qs = AuditoriaFalhaCadastro.objects.filter(
                origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
                data_analise__isnull=True,
                analise_concluida_em__isnull=False,
            )
            count = qs.count()
            self.stdout.write(f"Contestação candidata a backfill data_analise: {count}")
            if count and not dry_run:
                with transaction.atomic():
                    updated = qs.update(data_analise=F("analise_concluida_em"))
                self.stdout.write(self.style.SUCCESS(f"Backfill data_analise: {updated} registros"))

        before = build_cascade_reconciliation()
        self.stdout.write(
            f"Antes — unmapped={before.get('unmapped_cliente_workflow')} "
            f"projected={before.get('projected_rows')}"
        )

        qs = eligible_sources_qs().order_by("pk")
        report = sync_queryset(
            qs,
            dry_run=dry_run,
            force=True,
            batch_size=batch_size,
        )
        import json

        self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, default=str))

        after = build_cascade_reconciliation()
        self.stdout.write(
            f"Depois — unmapped={after.get('unmapped_cliente_workflow')} "
            f"projected={after.get('projected_rows')}"
        )
