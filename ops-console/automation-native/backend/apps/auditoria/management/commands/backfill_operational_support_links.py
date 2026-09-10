from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.auditoria.models import (
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.suporte_operacional_link import resolve_and_link


class Command(BaseCommand):
    help = "Vincula registros de qualidade a solicitações de suporte operacional respondidas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas reporta quantos registros seriam analisados.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        models = (
            AuditoriaAtividadeProtocolo,
            QualidadePendenteReinspecao,
            QualidadePendenteAuditoriaCompliance,
            AuditoriaFalhaCadastro,
            ContestacaoOperacional,
        )
        linked = 0
        scanned = 0
        for model in models:
            qs = model.objects.filter(operational_support_request__isnull=True)
            for record in qs.iterator(chunk_size=200):
                scanned += 1
                if dry_run:
                    continue
                before = record.operational_support_request_id
                resolve_and_link(record)
                record.refresh_from_db()
                if record.operational_support_request_id and record.operational_support_request_id != before:
                    linked += 1
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry-run: {scanned} registros sem FK seriam analisados.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Vinculados {linked} de {scanned} registros analisados.")
            )
