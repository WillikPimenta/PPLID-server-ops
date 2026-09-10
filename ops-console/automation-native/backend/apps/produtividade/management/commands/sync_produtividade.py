from django.core.management.base import BaseCommand

from apps.produtividade.models import ProductivitySyncLog
from apps.produtividade.services.sync_runner import run_sync_with_audit


class Command(BaseCommand):
    help = "Sincroniza o relatório de produtividade HxH da pasta brflow-prod-hxh-bruto."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=str, help="Caminho personalizado para o arquivo Excel")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Força recarga mesmo sem alteração no arquivo",
        )
        parser.add_argument(
            "--trigger",
            type=str,
            choices=["user", "system"],
            default="user",
            help="Origem do sync: user (manual) ou system (Task Scheduler)",
        )

    def handle(self, *args, **options):
        path = options.get("path")
        force = options.get("force", False)
        trigger = (
            ProductivitySyncLog.TRIGGER_SYSTEM
            if options.get("trigger") == "system"
            else ProductivitySyncLog.TRIGGER_USER
        )

        self.stdout.write(self.style.NOTICE(f"Iniciando sync produtividade (force={force})"))

        try:
            success, log, skipped = run_sync_with_audit(
                path=path,
                trigger_source=trigger,
                force=force,
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Falha: {exc}"))
            raise SystemExit(1) from exc

        if skipped:
            self.stdout.write(self.style.SUCCESS(f"Sem alterações. {log.message}"))
            return

        if success:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sincronização concluída: {log.row_count} registros em {log.duration_seconds}s."
                )
            )
        else:
            self.stdout.write(self.style.ERROR(f"Sincronização falhou: {log.message}"))
            raise SystemExit(1)
