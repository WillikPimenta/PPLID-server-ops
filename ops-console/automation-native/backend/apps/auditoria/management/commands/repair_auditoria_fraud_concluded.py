from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.auditoria.models import AuditoriaAtividade
from apps.auditoria.services.qualidade_promocao import selar_tratados_auditoria
from apps.auditoria.services.text_format import (
    is_tipo_falha_automatico,
    is_tipo_falha_sem_falha,
)


REQUIRED_FIELDS = (
    "modulo",
    "novo_resultado",
    "sinalizacao",
    "motivo_falha",
    "nivel_dificuldade",
    "tipo_documento",
    "uf_documento",
    "qualidade_imagem",
)


def _draft_errors(atividade: AuditoriaAtividade) -> list[str]:
    errors: list[str] = []
    drafts = list(atividade.falhas.order_by("id"))
    if not drafts:
        return ["sem rascunhos"]
    for draft in drafts:
        if is_tipo_falha_sem_falha(draft.tipo_falha):
            continue
        missing = [field for field in REQUIRED_FIELDS if not str(getattr(draft, field, "") or "").strip()]
        if not is_tipo_falha_automatico(draft.tipo_falha) and not str(draft.etapa_falha or "").strip():
            missing.append("etapa_falha")
        if missing:
            errors.append(f"rascunho {draft.pk}: campos ausentes {','.join(missing)}")
    return errors


class Command(BaseCommand):
    help = "Promove rascunhos presos em atividades Fraud ja marcadas como concluidas."

    def add_arguments(self, parser):
        parser.add_argument("--activity-id", action="append", type=int, dest="activity_ids")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persiste a promocao. Sem esta opcao, executa apenas dry-run.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        activity_ids = options.get("activity_ids") or []
        if apply_changes and not activity_ids:
            raise CommandError("Informe ao menos um --activity-id ao usar --apply.")

        queryset = AuditoriaAtividade.objects.filter(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
            falhas__isnull=False,
            tratados__isnull=True,
        ).distinct()
        if activity_ids:
            queryset = queryset.filter(pk__in=activity_ids)

        candidates = list(queryset.order_by("id").select_related("created_by"))
        repaired = 0
        invalid = 0
        with transaction.atomic():
            for candidate in candidates:
                errors = _draft_errors(candidate)
                if candidate.created_by_id is None:
                    errors.append("sem usuario finalizador")
                if errors:
                    invalid += 1
                    self.stdout.write(f"atividade={candidate.pk} INVALIDA: {'; '.join(errors)}")
                    continue
                self.stdout.write(
                    f"atividade={candidate.pk} PRONTA rascunhos={candidate.falhas.count()}"
                )
                if apply_changes:
                    locked = AuditoriaAtividade.objects.select_for_update().get(pk=candidate.pk)
                    selar_tratados_auditoria(locked, finalizador=locked.created_by)
                    repaired += 1
            if not apply_changes:
                transaction.set_rollback(True)

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: candidatas={len(candidates)} invalidas={invalid} reparadas={repaired}"
            )
        )
