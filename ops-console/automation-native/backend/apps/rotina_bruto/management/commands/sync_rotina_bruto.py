from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from apps.rotina_bruto.models import RotinaBrutoSyncLog
from apps.rotina_bruto.services.source_path import list_source_files
from apps.rotina_bruto.services.sync_runner import run_sync_with_audit

ALL_REPORT_TYPES = [
    RotinaBrutoSyncLog.REPORT_DETALHADO,
    RotinaBrutoSyncLog.REPORT_PROD,
    RotinaBrutoSyncLog.REPORT_MONITOR,
    RotinaBrutoSyncLog.REPORT_CONFER_BUSCA,
    RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
    RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE,
    RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"Data inválida (use YYYY-MM-DD): {value}") from exc


class Command(BaseCommand):
    help = (
        "Sincroniza relatórios da rotina/GED (detalhado, prod, monitor, confer_busca, "
        "ged_detalhado, ged_irregularidade, g_auditoria) para o PostgreSQL. "
        "Use --all ou --all-types para carga histórica da pasta."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            type=str,
            choices=ALL_REPORT_TYPES,
            help="Tipo de relatório bruto (obrigatório exceto com --all-types)",
        )
        parser.add_argument("--path", type=str, help="Caminho personalizado para um único arquivo")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Importa todos os arquivos da pasta do tipo informado",
        )
        parser.add_argument(
            "--all-types",
            action="store_true",
            help="Importa todos os arquivos das seis pastas configuradas",
        )
        parser.add_argument(
            "--from-date",
            type=str,
            help="Filtra arquivos com report_date >= YYYY-MM-DD (com --all ou --all-types)",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            help="Filtra arquivos com report_date <= YYYY-MM-DD (com --all ou --all-types)",
        )
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
        report_type = options.get("type")
        path = options.get("path")
        sync_all = options.get("all", False)
        sync_all_types = options.get("all_types", False)
        force = options.get("force", False)
        from_date = _parse_date(options.get("from_date"))
        to_date = _parse_date(options.get("to_date"))
        trigger = (
            RotinaBrutoSyncLog.TRIGGER_SYSTEM
            if options.get("trigger") == "system"
            else RotinaBrutoSyncLog.TRIGGER_USER
        )

        if sync_all_types:
            if report_type or path:
                raise CommandError("--all-types não pode ser usado com --type ou --path")
            self._sync_bulk(ALL_REPORT_TYPES, from_date, to_date, force, trigger)
            return

        if sync_all:
            if not report_type:
                raise CommandError("--all requer --type")
            if path:
                raise CommandError("--all não pode ser usado com --path")
            self._sync_bulk([report_type], from_date, to_date, force, trigger)
            return

        if not report_type:
            raise CommandError("Informe --type ou use --all-types")

        self.stdout.write(
            self.style.NOTICE(f"Iniciando sync rotina bruto ({report_type}, force={force})")
        )
        self._sync_single(report_type, path, force, trigger)

    def _sync_single(
        self,
        report_type: str,
        path: str | None,
        force: bool,
        trigger: str,
    ) -> None:
        try:
            success, log, skipped = run_sync_with_audit(
                report_type=report_type,
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

    def _sync_bulk(
        self,
        report_types: list[str],
        from_date: date | None,
        to_date: date | None,
        force: bool,
        trigger: str,
    ) -> None:
        total_files = 0
        total_rows = 0
        failures = 0

        for report_type in report_types:
            files = list_source_files(report_type, from_date=from_date, to_date=to_date)
            if not files:
                self.stdout.write(
                    self.style.WARNING(
                        f"[{report_type}] Nenhum arquivo encontrado na pasta configurada."
                    )
                )
                continue

            self.stdout.write(
                self.style.NOTICE(
                    f"[{report_type}] {len(files)} arquivo(s) para importar (force={force})"
                )
            )

            for info in files:
                total_files += 1
                label = f"[{report_type}] {info.report_date} ({info.path.name})"
                try:
                    success, log, skipped = run_sync_with_audit(
                        report_type=report_type,
                        path=str(info.path),
                        trigger_source=trigger,
                        force=force,
                    )
                except Exception as exc:
                    failures += 1
                    self.stdout.write(self.style.ERROR(f"{label} — ERRO: {exc}"))
                    continue

                if skipped:
                    self.stdout.write(self.style.SUCCESS(f"{label} — sem alterações"))
                elif success:
                    rows = log.row_count or 0
                    total_rows += rows
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"{label} — {rows} registros em {log.duration_seconds}s"
                        )
                    )
                else:
                    failures += 1
                    self.stdout.write(self.style.ERROR(f"{label} — falhou: {log.message}"))

        self.stdout.write("")
        self.stdout.write(
            self.style.NOTICE(
                f"Resumo: {total_files} arquivo(s), {total_rows} registro(s) importados, {failures} falha(s)."
            )
        )
        if failures:
            raise SystemExit(1)
