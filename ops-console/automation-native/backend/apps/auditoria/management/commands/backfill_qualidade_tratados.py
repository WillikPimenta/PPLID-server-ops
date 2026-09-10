"""Backfill da tabela central de tratados (auditoria_falha_cadastro).

Uso:
  python manage.py backfill_qualidade_tratados
  python manage.py backfill_qualidade_tratados --dry-run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoria,
    QualidadePendenteContestacao,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.atividade_protocolo_crud import sync_atividade_protocolo_metrics
from apps.auditoria.services.qualidade_promocao import (
    promover_pendente_reinspecao,
    promover_protocolo_contestacao,
    selar_tratados_auditoria,
)


class Command(BaseCommand):
    help = (
        "Preenche origem, sela falhas de auditoria concluídas, promove protocolos "
        "de contestação concluídos e pendentes de reinspeção já finalizados — sem duplicar."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente reporta o que seria alterado.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        stats = {
            "origem_preenchida": 0,
            "auditoria_selada": 0,
            "contestacao_promovida": 0,
            "reinspecao_promovida": 0,
            "pendente_auditoria_removido": 0,
            "pendente_contestacao_removido": 0,
        }

        stats["origem_preenchida"] = self._backfill_origem(dry_run=dry_run)
        stats["auditoria_selada"] = self._seal_auditoria(dry_run=dry_run)
        stats["contestacao_promovida"] = self._promote_contestacao(dry_run=dry_run)
        stats["reinspecao_promovida"] = self._promote_reinspecao(dry_run=dry_run)
        stats["pendente_auditoria_removido"] = self._cleanup_pendente_auditoria(dry_run=dry_run)
        stats["pendente_contestacao_removido"] = self._cleanup_pendente_contestacao(dry_run=dry_run)

        prefix = "[dry-run] " if dry_run else ""
        for key, value in stats.items():
            self.stdout.write(f"{prefix}{key}: {value}")

    def _backfill_origem(self, *, dry_run: bool) -> int:
        qs = AuditoriaFalhaCadastro.objects.filter(Q(origem="") | Q(origem__isnull=True)).exclude(
            tipo_registro=""
        )
        count = qs.count()
        if dry_run or count == 0:
            return count
        updated = 0
        for tipo in (
            AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
        ):
            updated += qs.filter(tipo_registro=tipo).update(origem=tipo)
        return updated

    def _seal_auditoria(self, *, dry_run: bool) -> int:
        atividades = AuditoriaAtividade.objects.filter(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
        )
        sealed = 0
        for atividade in atividades.iterator():
            pending = atividade.falhas.count()
            if pending == 0:
                continue
            sealed += atividade.falhas.count()
            if dry_run:
                continue
            with transaction.atomic():
                selar_tratados_auditoria(atividade, finalizador=None)
        return sealed

    def _promote_contestacao(self, *, dry_run: bool) -> int:
        qs = AuditoriaAtividadeProtocolo.objects.filter(
            atividade__tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
            status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
        ).select_related("atividade", "analisado_por")
        count = qs.count()
        if dry_run:
            return count
        promoted = 0
        for protocolo in list(qs.iterator()):
            atividade = protocolo.atividade
            with transaction.atomic():
                before = AuditoriaFalhaCadastro.objects.filter(
                    protocolo_origem_id=protocolo.pk,
                ).count()
                promover_protocolo_contestacao(protocolo, user=protocolo.analisado_por)
                after = AuditoriaFalhaCadastro.objects.filter(
                    protocolo_origem_id=protocolo.pk,
                ).count()
                promoted += max(0, after - before)
                if atividade is not None:
                    sync_atividade_protocolo_metrics(atividade)
        return promoted

    def _promote_reinspecao(self, *, dry_run: bool) -> int:
        qs = QualidadePendenteReinspecao.objects.filter(
            analise_status=QualidadePendenteReinspecao.ANALISE_CONCLUIDO,
        )
        count = qs.count()
        if dry_run:
            return count
        promoted = 0
        for pendente in list(qs.iterator()):
            with transaction.atomic():
                promover_pendente_reinspecao(pendente, finalizador=None)
                promoted += 1
        return promoted

    def _cleanup_pendente_auditoria(self, *, dry_run: bool) -> int:
        qs = QualidadePendenteAuditoria.objects.filter(
            atividade__status=AuditoriaAtividade.STATUS_CONCLUIDA,
        )
        count = qs.count()
        if not dry_run and count:
            qs.delete()
        return count

    def _cleanup_pendente_contestacao(self, *, dry_run: bool) -> int:
        qs = QualidadePendenteContestacao.objects.filter(
            Q(atividade__status=AuditoriaAtividade.STATUS_CONCLUIDA)
            | Q(atividade__protocolos__isnull=True)
        ).distinct()
        # Só remove se não houver protocolos vivos abertos.
        to_delete = []
        for pendente in qs.select_related("atividade").iterator():
            atividade = pendente.atividade
            if atividade is None:
                to_delete.append(pendente.pk)
                continue
            abertos = atividade.protocolos.exclude(
                status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
            ).count()
            if abertos == 0:
                to_delete.append(pendente.pk)
        count = len(to_delete)
        if not dry_run and to_delete:
            QualidadePendenteContestacao.objects.filter(pk__in=to_delete).delete()
        return count
