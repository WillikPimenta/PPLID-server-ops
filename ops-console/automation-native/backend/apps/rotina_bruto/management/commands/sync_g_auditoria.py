import json
from contextlib import nullcontext
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from apps.qualidade_operacional.services.g_auditoria import (
    rollback_g_auditoria_projection,
)
from apps.rotina_bruto.models import RotinaBrutoSyncLog
from apps.rotina_bruto.services.g_auditoria_sync import sync_g_auditoria_to_db
from apps.rotina_bruto.services.source_path import (
    get_source_file,
    list_source_files,
)
from apps.rotina_bruto.services.sync_runner import run_sync_with_audit


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"Data inválida (use YYYY-MM-DD): {value}") from exc


class Command(BaseCommand):
    help = (
        "Valida, carrega e projeta arquivos G Auditoria. "
        "Dry-run não escreve no banco; --project efetiva a projeção mesmo com a flag desligada."
    )

    def add_arguments(self, parser):
        parser.add_argument("--path", type=str, help="Um Parquet específico")
        parser.add_argument("--all", action="store_true", help="Todos os arquivos da pasta")
        parser.add_argument("--from-date", type=str)
        parser.add_argument("--to-date", type=str)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--project", action="store_true")
        parser.add_argument(
            "--rollback",
            action="store_true",
            help="Desativa a projeção e restaura os valores originais da Intranet",
        )
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        path = options.get("path")
        sync_all = bool(options.get("all"))
        dry_run = bool(options.get("dry_run"))
        project = bool(options.get("project"))
        rollback = bool(options.get("rollback"))
        force = bool(options.get("force"))
        from_date = _parse_date(options.get("from_date"))
        to_date = _parse_date(options.get("to_date"))
        if rollback:
            incompatible = (
                path
                or sync_all
                or dry_run
                or project
                or force
                or from_date
                or to_date
            )
            if incompatible:
                raise CommandError("--rollback deve ser usado sozinho")
            metrics = rollback_g_auditoria_projection()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Rollback lógico concluído — "
                    f"{json.dumps(metrics, ensure_ascii=False, sort_keys=True)}"
                )
            )
            return

        if bool(path) == sync_all:
            raise CommandError("Informe exatamente um entre --path e --all")
        if dry_run and force:
            raise CommandError("--force não é necessário com --dry-run")

        if path:
            files = [
                get_source_file(
                    RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
                    override_path=path,
                )
            ]
        else:
            files = list_source_files(
                RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
                from_date=from_date,
                to_date=to_date,
            )
        if not files:
            raise CommandError("Nenhum arquivo G Auditoria encontrado")

        total = 0
        failures = 0
        for source in files:
            label = f"{source.report_date} {source.path.name}"
            try:
                if dry_run:
                    result = sync_g_auditoria_to_db(
                        source,
                        dry_run=True,
                    )
                    total += result.row_count
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"{label} — {json.dumps(result.metrics, ensure_ascii=False, sort_keys=True)}"
                        )
                    )
                else:
                    projection_override = (
                        override_settings(
                            QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=True
                        )
                        if project
                        else nullcontext()
                    )
                    with projection_override:
                        success, log, skipped = run_sync_with_audit(
                            report_type=RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
                            path=str(source.path),
                            force=force or project,
                        )
                    if not success:
                        failures += 1
                        self.stdout.write(self.style.ERROR(f"{label} — {log.message}"))
                    elif skipped:
                        self.stdout.write(self.style.SUCCESS(f"{label} — sem alterações"))
                    else:
                        total += int(log.row_count or 0)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"{label} — {json.dumps(log.metrics, ensure_ascii=False, sort_keys=True)}"
                            )
                        )
            except Exception as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f"{label} — ERRO: {exc}"))

        self.stdout.write(
            self.style.NOTICE(
                f"Resumo: {len(files)} arquivo(s), {total} linha(s) válidas, {failures} falha(s)."
            )
        )
        if failures:
            raise SystemExit(1)
