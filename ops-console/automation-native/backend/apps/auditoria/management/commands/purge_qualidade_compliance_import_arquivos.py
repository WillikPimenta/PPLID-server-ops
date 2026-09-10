from django.core.management.base import BaseCommand

from apps.auditoria.services.compliance_import_archive import (
    compliance_import_retention_days,
    purge_compliance_import_arquivos,
)


class Command(BaseCommand):
    help = (
        "Remove planilhas arquivadas de cadastro Compliance/Reinspeção "
        "com idade acima da retenção configurada."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas conta quantos arquivos seriam removidos.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        report = purge_compliance_import_arquivos(dry_run=dry_run)
        retention_days = compliance_import_retention_days()
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Planilhas removidas: {report.deleted} "
                f"(retenção={retention_days} dias, cutoff={report.cutoff})"
            )
        )
