from django.core.management.base import BaseCommand

from apps.monitor_eventos.models import MonitorEventoSyncLog
from apps.monitor_eventos.services.sync_runner import run_sync_with_audit


class Command(BaseCommand):
    help = "Sincroniza monitor de eventos HxH tratado da pasta monitor-eventos-tratado."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=str, help="Caminho personalizado para o arquivo parquet")
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
            help="Origem do sync: user (manual) ou system (automático)",
        )

    def handle(self, *args, **options):
        path = options.get("path")
        force = options.get("force", False)
        trigger = (
            MonitorEventoSyncLog.TRIGGER_SYSTEM
            if options.get("trigger") == "system"
            else MonitorEventoSyncLog.TRIGGER_USER
        )

        self.stdout.write(self.style.NOTICE(f"Iniciando sync monitor eventos (force={force})"))

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
