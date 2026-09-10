# -*- coding: utf-8 -*-
"""Sincroniza auditoria_falha_cadastro → qualidade_auditado/falha."""
from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.qualidade_operacional.services.intranet_source import (
    build_reconciliation_report,
    eligible_sources_qs,
    reconcile_orphans,
    sync_one,
    sync_queryset,
)
from apps.qualidade_operacional.services.source_config import (
    effective_source_active,
    get_cutover_date,
    get_source_mode,
    intranet_source_enabled,
)


class Command(BaseCommand):
    help = (
        "Projeta tratados Intranet elegíveis em qualidade_auditado/qualidade_falha. "
        "Idempotente, retomável e seguro para reprocessamento."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Não grava alterações.")
        parser.add_argument(
            "--full",
            action="store_true",
            help="Reprocessa todos os elegíveis (ignora fingerprint).",
        )
        parser.add_argument(
            "--since",
            type=str,
            default="",
            help="ISO datetime: só updated_at >= since (sync incremental).",
        )
        parser.add_argument("--from-id", type=int, default=None, help="Retoma a partir do PK.")
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--source-id", type=int, default=None, help="Sincroniza um ID.")
        parser.add_argument(
            "--reconcile-orphans",
            action="store_true",
            help="Remove projeções órfãs / fora de elegibilidade.",
        )
        parser.add_argument(
            "--report-only",
            action="store_true",
            help="Emite relatório de reconciliação TSV×Intranet (somente leitura).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Permite gravar projeções mesmo com flag/modo legado (staging).",
        )

    def handle(self, *args, **options) -> None:
        if options["report_only"]:
            report = build_reconciliation_report()
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return

        dry_run = bool(options["dry_run"])
        force = bool(options["force"] or options["full"])
        batch_size = max(1, int(options["batch_size"] or 200))

        self.stdout.write(
            self.style.NOTICE(
                f"mode={get_source_mode()} enabled={intranet_source_enabled()} "
                f"cutover={get_cutover_date()} active={effective_source_active()} "
                f"dry_run={dry_run} force={force}"
            )
        )

        if options["reconcile_orphans"]:
            orphan_report = reconcile_orphans(dry_run=dry_run)
            self.stdout.write(json.dumps({"orphans": orphan_report.as_dict()}, ensure_ascii=False))

        source_id = options["source_id"]
        if source_id is not None:
            source = (
                AuditoriaFalhaCadastro.objects.select_related(
                    "atividade", "auditor_ref", "auditor_responsavel", "analise_origem"
                )
                .filter(pk=source_id)
                .first()
            )
            if source is None:
                raise CommandError(f"source-id {source_id} não encontrado.")
            from apps.qualidade_operacional.services.intranet_source import SyncReport

            report = SyncReport()
            sync_one(source, dry_run=dry_run, force=force, report=report, bump_cache=not dry_run)
            self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            return

        qs = eligible_sources_qs().select_related(
            "atividade", "auditor_ref", "auditor_responsavel", "analise_origem"
        )
        since_raw = (options["since"] or "").strip()
        if since_raw:
            since = parse_datetime(since_raw)
            if since is None:
                try:
                    since = datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise CommandError(f"--since inválido: {since_raw}") from exc
            if timezone.is_naive(since):
                since = timezone.make_aware(since, timezone.get_current_timezone())
            qs = qs.filter(updated_at__gte=since)

        report = sync_queryset(
            qs,
            batch_size=batch_size,
            dry_run=dry_run,
            force=force,
            from_id=options["from_id"],
        )
        self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
