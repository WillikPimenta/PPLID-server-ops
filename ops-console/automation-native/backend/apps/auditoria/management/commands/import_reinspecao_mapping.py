from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import (
    ReinspecaoIrregularidadeMapping,
    ReinspecaoMappingRun,
)
from apps.auditoria.services.reinspecao_mapping import load_mapping_source


class Command(BaseCommand):
    help = "Valida ou importa uma versão canônica da matriz de Reinspeção."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--mapping-version", default="")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-sha256", default="")
        parser.add_argument("--preview-token", default="")
        parser.add_argument(
            "--review-status",
            choices=[
                ReinspecaoIrregularidadeMapping.REVIEW_PENDING,
                ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
                ReinspecaoIrregularidadeMapping.REVIEW_REJECTED,
            ],
            default=ReinspecaoIrregularidadeMapping.REVIEW_PENDING,
        )
        parser.add_argument("--executed-by", default="")

    def handle(self, *args, **options):
        del args
        try:
            dataset = load_mapping_source(
                options["source"], mapping_version=options["mapping_version"]
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(str(exc)) from exc

        report = {
            "mode": "apply" if options["apply"] else "dry-run",
            "mapping_version": dataset.mapping_version,
            "source_hash": dataset.source_hash,
            "source_label": dataset.source_label,
            "sheet": dataset.sheet_name,
            "range": dataset.source_range,
            "headers": list(dataset.headers),
            "preview_token": dataset.preview_token,
            "warnings": list(dataset.warnings),
            "stats": dataset.stats(),
        }
        if options["dry_run"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return

        expected_hash = str(options["expected_sha256"] or "").strip().lower()
        preview_token = str(options["preview_token"] or "").strip().lower()
        if expected_hash != dataset.source_hash.lower():
            raise CommandError("--expected-sha256 não corresponde ao arquivo analisado.")
        if preview_token != dataset.preview_token.lower():
            raise CommandError("--preview-token não corresponde à prévia atual.")

        user = None
        username = str(options["executed_by"] or "").strip()
        if username:
            user = get_user_model().objects.filter(username=username).first()
            if user is None:
                raise CommandError(f"Usuário executor não encontrado: {username}.")

        review_status = options["review_status"]
        activate = review_status == ReinspecaoIrregularidadeMapping.REVIEW_APPROVED
        with transaction.atomic():
            if not activate and ReinspecaoIrregularidadeMapping.objects.filter(
                source_hash=dataset.source_hash,
                active=True,
                review_status=ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
            ).exists():
                raise CommandError(
                    "Uma fonte já aprovada não pode ser rebaixada para pendente/rejeitada."
                )
            run = ReinspecaoMappingRun.objects.create(
                kind=ReinspecaoMappingRun.KIND_IMPORT,
                mapping_version=dataset.mapping_version,
                source_hash=dataset.source_hash,
                preview_token=dataset.preview_token,
                manifest=report,
                executed_by=user,
            )
            if activate:
                ReinspecaoIrregularidadeMapping.objects.select_for_update().filter(
                    active=True
                ).exclude(source_hash=dataset.source_hash).update(active=False)
            existing_rows = set(
                ReinspecaoIrregularidadeMapping.objects.filter(
                    source_hash=dataset.source_hash
                ).values_list("source_row", flat=True)
            )
            to_create = [
                ReinspecaoIrregularidadeMapping(
                    codigo_original=row.code_original,
                    codigo_normalizado=row.code_normalized,
                    classificacao=row.classification,
                    descricao_original=row.description_original,
                    descricao_normalizada=row.description_normalized,
                    ilha=row.island,
                    etapa=row.stage,
                    tipo_documento=row.document_type,
                    categoria=row.category,
                    cenario=row.scenario,
                    active=activate,
                    mapping_version=row.mapping_version,
                    source_hash=row.source_hash,
                    source_row=row.source_row,
                    review_status=review_status,
                    imported_by=user,
                )
                for row in dataset.variants
                if row.source_row not in existing_rows
            ]
            ReinspecaoIrregularidadeMapping.objects.bulk_create(
                to_create, batch_size=500
            )
            current = ReinspecaoIrregularidadeMapping.objects.filter(
                source_hash=dataset.source_hash
            )
            current.update(
                active=activate,
                review_status=review_status,
                mapping_version=dataset.mapping_version,
            )
            report["created"] = len(to_create)
            report["reused"] = len(existing_rows)
            report["active_rows"] = current.filter(active=True).count()
            report["review_status"] = review_status
            run.status = ReinspecaoMappingRun.STATUS_APPLIED
            run.manifest = report
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "manifest", "finished_at"])

        self.stdout.write(json.dumps({**report, "run_id": str(run.pk)}, ensure_ascii=False, indent=2))
