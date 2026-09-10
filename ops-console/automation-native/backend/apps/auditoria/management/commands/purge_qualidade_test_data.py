# -*- coding: utf-8 -*-
"""Remove dados de teste de Auditoria, Contestação e Reinspeção até uma data limite.

Uso:
  python manage.py purge_qualidade_test_data --through-date 2026-08-08 --dry-run
  python manage.py purge_qualidade_test_data --through-date 2026-08-08 --confirm
"""

from __future__ import annotations

from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeImportStaging,
    AuditoriaComplianceFilaHistorico,
    AuditoriaControleRegistro,
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    QualidadePendenteAuditoria,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteAuditoriaFalha,
    QualidadePendenteContestacao,
    QualidadePendenteReinspecao,
    ReinspecaoFilaHistorico,
)
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha, QualidadeIntranetProjection
from apps.qualidade_operacional.services.intranet_source import _delete_projection_facts
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

QUALIDADE_TIPOS = (
    AuditoriaAtividade.TIPO_AUDITORIA,
    AuditoriaAtividade.TIPO_CONTESTACAO,
    AuditoriaAtividade.TIPO_REINSPECAO,
)


def parse_through_date(value: str) -> datetime:
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"Data inválida: {value!r}. Use YYYY-MM-DD.") from exc
    naive = datetime.combine(day, time.max)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _delete_file_field(file_field) -> None:
    if file_field:
        file_field.delete(save=False)


class Command(BaseCommand):
    help = (
        "Remove dados de teste de Qualidade (auditoria, contestação, reinspeção) "
        "até a data informada, incluindo projeções EO vinculadas."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--through-date",
            type=str,
            default="2026-08-08",
            help="Remove registros com created_at/imported_at até esta data (inclusive).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente reporta contagens; não altera o banco.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirma execução destrutiva (obrigatório sem --dry-run).",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        confirm = bool(options["confirm"])
        if not dry_run and not confirm:
            raise CommandError("Use --dry-run para simular ou --confirm para executar.")

        cutoff = parse_through_date(options["through_date"])
        stats = self._run(cutoff=cutoff, dry_run=dry_run)
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write("")
        self.stdout.write(
            self.style.NOTICE(f"{prefix}Corte: created_at/imported_at <= {cutoff.isoformat()}")
        )
        for label, count in stats.items():
            self.stdout.write(f"{prefix}{label}: {count}")

    def _run(self, *, cutoff: datetime, dry_run: bool) -> dict[str, int]:
        stats: dict[str, int] = {}

        contestacao_qs = ContestacaoOperacional.objects.filter(
            Q(created_at__lte=cutoff) | Q(falha__created_at__lte=cutoff)
        )
        stats["qualidade_contestacao_operacional"] = contestacao_qs.count()

        fila_qs = ReinspecaoFilaHistorico.objects.filter(created_at__lte=cutoff)
        stats["reinspecao_fila_historico"] = fila_qs.count()

        compliance_fila_qs = AuditoriaComplianceFilaHistorico.objects.filter(
            created_at__lte=cutoff
        )
        stats["auditoria_compliance_fila_historico"] = compliance_fila_qs.count()

        pend_aud_falha_qs = QualidadePendenteAuditoriaFalha.objects.filter(created_at__lte=cutoff)
        stats["qualidade_pendente_auditoria_falha"] = pend_aud_falha_qs.count()

        pend_aud_qs = QualidadePendenteAuditoria.objects.filter(created_at__lte=cutoff)
        stats["qualidade_pendente_auditoria"] = pend_aud_qs.count()

        pend_ct_qs = QualidadePendenteContestacao.objects.filter(created_at__lte=cutoff)
        stats["qualidade_pendente_contestacao"] = pend_ct_qs.count()

        pend_re_qs = QualidadePendenteReinspecao.objects.filter(created_at__lte=cutoff)
        stats["qualidade_pendente_reinspecao"] = pend_re_qs.count()

        pend_compliance_qs = QualidadePendenteAuditoriaCompliance.objects.filter(
            created_at__lte=cutoff
        )
        stats["qualidade_pendente_auditoria_compliance"] = pend_compliance_qs.count()

        orphan_tratados_qs = AuditoriaFalhaCadastro.objects.filter(
            created_at__lte=cutoff,
            atividade__isnull=True,
        )
        stats["auditoria_falha_cadastro_orfaos"] = orphan_tratados_qs.count()

        atividade_qs = AuditoriaAtividade.objects.filter(
            created_at__lte=cutoff,
            tipo__in=QUALIDADE_TIPOS,
        )
        stats["auditoria_atividade"] = atividade_qs.count()

        controle_qs = AuditoriaControleRegistro.objects.filter(created_at__lte=cutoff)
        stats["auditoria_controle_registro"] = controle_qs.count()

        staging_qs = AuditoriaAtividadeImportStaging.objects.filter(created_at__lte=cutoff)
        stats["auditoria_atividade_import_staging"] = staging_qs.count()

        projected_auditado_ids = QualidadeIntranetProjection.objects.filter(
            auditado_id__isnull=False,
        ).values_list("auditado_id", flat=True)
        orphan_auditado_qs = QualidadeAuditado.objects.filter(
            imported_at__lte=cutoff,
            source_file=INTRANET_SOURCE_FILE,
        ).exclude(pk__in=projected_auditado_ids)
        stats["qualidade_auditado_orfaos"] = orphan_auditado_qs.count()

        projected_falha_ids = QualidadeIntranetProjection.objects.filter(
            falha_id__isnull=False,
        ).values_list("falha_id", flat=True)
        orphan_falha_qs = QualidadeFalha.objects.filter(
            imported_at__lte=cutoff,
            source_file=INTRANET_SOURCE_FILE,
        ).exclude(pk__in=projected_falha_ids)
        stats["qualidade_falha_orfaos"] = orphan_falha_qs.count()

        if dry_run:
            stats["qualidade_intranet_projection_estimado"] = QualidadeIntranetProjection.objects.filter(
                source__created_at__lte=cutoff
            ).count()
            stats["qualidade_intranet_projection_orfaos"] = QualidadeIntranetProjection.objects.filter(
                source__created_at__lte=cutoff
            ).count()
            return stats

        with transaction.atomic():
            deleted, _ = contestacao_qs.delete()
            stats["qualidade_contestacao_operacional"] = deleted

            deleted, _ = fila_qs.delete()
            stats["reinspecao_fila_historico"] = deleted

            deleted, _ = compliance_fila_qs.delete()
            stats["auditoria_compliance_fila_historico"] = deleted

            deleted, _ = pend_aud_falha_qs.delete()
            stats["qualidade_pendente_auditoria_falha"] = deleted

            deleted, _ = pend_aud_qs.delete()
            stats["qualidade_pendente_auditoria"] = deleted

            deleted, _ = pend_ct_qs.delete()
            stats["qualidade_pendente_contestacao"] = deleted

            deleted, _ = pend_re_qs.delete()
            stats["qualidade_pendente_reinspecao"] = deleted

            deleted, _ = pend_compliance_qs.delete()
            stats["qualidade_pendente_auditoria_compliance"] = deleted

            deleted, _ = orphan_tratados_qs.delete()
            stats["auditoria_falha_cadastro_orfaos"] = deleted

            for atividade in atividade_qs.iterator():
                _delete_file_field(atividade.arquivo_original)
            deleted, _ = atividade_qs.delete()
            stats["auditoria_atividade"] = deleted

            for controle in controle_qs.iterator():
                _delete_file_field(controle.selfie)
            deleted, _ = controle_qs.delete()
            stats["auditoria_controle_registro"] = deleted

            for staging in staging_qs.iterator():
                _delete_file_field(staging.arquivo)
            deleted, _ = staging_qs.delete()
            stats["auditoria_atividade_import_staging"] = deleted

            deleted, _ = orphan_auditado_qs.delete()
            stats["qualidade_auditado_orfaos"] = deleted

            deleted, _ = orphan_falha_qs.delete()
            stats["qualidade_falha_orfaos"] = deleted

            surviving_source_ids = AuditoriaFalhaCadastro.objects.values_list("pk", flat=True)
            stale_projection_qs = QualidadeIntranetProjection.objects.exclude(
                source_id__in=surviving_source_ids
            )
            stale_removed = 0
            for projection in stale_projection_qs.iterator():
                _delete_projection_facts(projection)
                projection.delete()
                stale_removed += 1
            stats["qualidade_intranet_projection_orfaos"] = stale_removed

            bump_quality_cache_version()

        return stats
