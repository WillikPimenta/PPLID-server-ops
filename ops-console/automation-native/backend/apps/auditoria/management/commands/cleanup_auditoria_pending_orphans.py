from django.core.management.base import BaseCommand
from django.db import transaction

from apps.auditoria.models import QualidadePendenteAuditoria


class Command(BaseCommand):
    help = "Remove pontes pendentes de Auditoria Fraud cuja atividade foi excluida."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persiste a exclusao. Sem esta opcao, executa apenas dry-run.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        queryset = QualidadePendenteAuditoria.objects.filter(atividade__isnull=True)
        orphan_ids = list(queryset.order_by("id").values_list("id", flat=True))

        with transaction.atomic():
            if apply_changes and orphan_ids:
                QualidadePendenteAuditoria.objects.filter(pk__in=orphan_ids).delete()
            if not apply_changes:
                transaction.set_rollback(True)

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: orfaos={len(orphan_ids)}"))
        if orphan_ids:
            self.stdout.write("ids=" + ",".join(str(item) for item in orphan_ids))
