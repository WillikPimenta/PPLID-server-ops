from django.core.management.base import BaseCommand

from apps.falhas_criticas.models import SyncAuditLog
from apps.falhas_criticas.services.excel_path import get_excel_source_path
from apps.falhas_criticas.services.sync_runner import run_sync_with_audit


class Command(BaseCommand):
    help = "Sincroniza os dados do Excel FALHAS_CRITICAS_MANUAL.xlsx com o banco PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=str, help="Caminho personalizado para o arquivo Excel")
        parser.add_argument(
            "--trigger",
            type=str,
            choices=["user", "system"],
            default="user",
            help="Origem do sync: user (manual) ou system (Task Scheduler / automático)",
        )

    def handle(self, *args, **options):
        path = options.get("path")
        trigger = (
            SyncAuditLog.TRIGGER_SYSTEM
            if options.get("trigger") == "system"
            else SyncAuditLog.TRIGGER_USER
        )
        excel_path = get_excel_source_path(path)
        self.stdout.write(
            self.style.NOTICE(f"Iniciando sincronização: {excel_path} (trigger={trigger})")
        )

        success, log = run_sync_with_audit(path=path, trigger_source=trigger)
        if success:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sincronização concluída em {log.duration_seconds}s (log #{log.pk})."
                )
            )
        else:
            self.stdout.write(self.style.ERROR(f"Sincronização falhou: {log.message}"))
            raise SystemExit(1)
