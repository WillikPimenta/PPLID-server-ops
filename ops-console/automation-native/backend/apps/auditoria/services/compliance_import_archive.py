from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaComplianceImportArquivo,
    AuditoriaComplianceImportStaging,
    AuditoriaAtividadeImportStaging,
    QualidadeComplianceImportArquivo,
)


def compliance_import_retention_days() -> int:
    return max(
        1,
        int(getattr(settings, "QUALIDADE_COMPLIANCE_IMPORT_RETENTION_DAYS", 3)),
    )


@dataclass
class ComplianceImportPurgeReport:
    deleted: int = 0
    cutoff: str = ""

    def as_dict(self) -> dict:
        return {"deleted": self.deleted, "cutoff": self.cutoff}


def archive_compliance_import_file(
    *,
    staging: AuditoriaAtividadeImportStaging,
    user,
    contexto: str,
    protocolos_importados: int,
) -> QualidadeComplianceImportArquivo | None:
    """Persiste a planilha confirmada em MEDIA_ROOT por retenção configurável."""
    if contexto not in {
        QualidadeComplianceImportArquivo.CONTEXTO_REINSPECAO,
        QualidadeComplianceImportArquivo.CONTEXTO_AUDITORIA_COMPLIANCE,
    }:
        return None
    if not staging.arquivo:
        return None

    preview = staging.preview or {}
    record = QualidadeComplianceImportArquivo(
        contexto=contexto,
        nome_arquivo_original=staging.nome_arquivo or "importacao.xlsx",
        content_sha256=str(preview.get("file_sha256") or "").strip(),
        protocolos_importados=max(0, int(protocolos_importados or 0)),
        created_by=user,
    )
    record.save()

    with staging.arquivo.open("rb") as handle:
        record.arquivo.save(
            record.nome_arquivo_original,
            ContentFile(handle.read()),
            save=True,
        )
    return record


def archive_auditoria_compliance_import_file(
    *,
    staging: AuditoriaComplianceImportStaging,
    user,
    protocolos_importados: int,
) -> AuditoriaComplianceImportArquivo | None:
    """Persiste a planilha de Auditoria Compliance em armazenamento exclusivo."""
    if not staging.arquivo:
        return None

    preview = staging.preview or {}
    record = AuditoriaComplianceImportArquivo(
        nome_arquivo_original=staging.nome_arquivo or "importacao.xlsx",
        content_sha256=str(preview.get("file_sha256") or "").strip(),
        protocolos_importados=max(0, int(protocolos_importados or 0)),
        created_by=user,
    )
    record.save()

    with staging.arquivo.open("rb") as handle:
        record.arquivo.save(
            record.nome_arquivo_original,
            ContentFile(handle.read()),
            save=True,
        )
    return record


def purge_compliance_import_arquivos(*, dry_run: bool = False) -> ComplianceImportPurgeReport:
    cutoff = timezone.now() - timedelta(days=compliance_import_retention_days())
    querysets = (
        QualidadeComplianceImportArquivo.objects.filter(created_at__lt=cutoff).order_by("id"),
        AuditoriaComplianceImportArquivo.objects.filter(created_at__lt=cutoff).order_by("id"),
    )
    report = ComplianceImportPurgeReport(cutoff=cutoff.isoformat())

    if dry_run:
        report.deleted = sum(qs.count() for qs in querysets)
        return report

    deleted = 0
    for qs in querysets:
        for record in qs.iterator():
            if record.arquivo:
                record.arquivo.delete(save=False)
            record.delete()
            deleted += 1

    report.deleted = deleted
    return report
