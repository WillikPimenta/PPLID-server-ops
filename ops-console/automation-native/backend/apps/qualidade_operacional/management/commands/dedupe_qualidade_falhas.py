# -*- coding: utf-8 -*-
"""Remove falhas duplicadas por ``case_key``, mantendo auditoria mais antiga."""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.qualidade_operacional.models import QualidadeFalha
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.case_key import (
    canonical_falha_ordering,
    delete_non_canonical_falhas,
)


class Command(BaseCommand):
    help = (
        "Deduplica qualidade_falha por case_key (protocolo+matrícula), "
        "mantendo o registro com data de auditoria mais antiga."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente diagnóstico; não altera dados.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica remoção das falhas não-canônicas.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Tamanho do lote de remoção (default 500).",
        )
        parser.add_argument(
            "--output",
            type=str,
            default="",
            help="Caminho opcional para gravar manifest JSON.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        apply = bool(options["apply"])
        batch_size = max(1, int(options["batch_size"] or 500))

        if dry_run and apply:
            self.stderr.write("Use apenas --dry-run ou --apply, não ambos.")
            return

        if not dry_run and not apply:
            dry_run = True
            self.stdout.write("Modo padrão: --dry-run (nenhuma alteração).")

        dup_keys = (
            QualidadeFalha.objects.exclude(case_key="")
            .values("case_key")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
            .order_by("-total")
        )

        groups: list[dict] = []
        remove_ids: list[int] = []

        for row in dup_keys:
            case_key = row["case_key"]
            falhas = list(
                canonical_falha_ordering(
                    QualidadeFalha.objects.filter(case_key=case_key).only(
                        "id", "protocolo", "matricula", "data", "data_analise", "source_file"
                    )
                )
            )
            if len(falhas) < 2:
                continue
            kept = falhas[0]
            removed = falhas[1:]
            remove_ids.extend(f.pk for f in removed)
            groups.append(
                {
                    "case_key": case_key,
                    "kept_id": kept.pk,
                    "kept_data": kept.data.isoformat() if kept.data else None,
                    "kept_source_file": kept.source_file or "",
                    "removed_ids": [f.pk for f in removed],
                    "removed_summary": [
                        {
                            "id": f.pk,
                            "data": f.data.isoformat() if f.data else None,
                            "source_file": f.source_file or "",
                        }
                        for f in removed
                    ],
                }
            )

        manifest = {
            "dry_run": dry_run,
            "duplicate_groups": len(groups),
            "duplicate_rows": len(remove_ids),
            "groups": groups[:200],
        }

        self.stdout.write(
            f"Grupos duplicados: {manifest['duplicate_groups']:,}; "
            f"linhas a remover: {manifest['duplicate_rows']:,}"
        )

        output_path = (options.get("output") or "").strip()
        if output_path:
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
            self.stdout.write(f"Manifest gravado em {output_path}")

        if dry_run:
            return

        deleted_total = 0
        for offset in range(0, len(remove_ids), batch_size):
            chunk = remove_ids[offset : offset + batch_size]
            result = delete_non_canonical_falhas(chunk)
            deleted_total += int(result.get("deleted") or 0)

        bump_quality_cache_version()
        self.stdout.write(self.style.SUCCESS(f"Removidas {deleted_total:,} falha(s) não-canônicas."))
