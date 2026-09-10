from __future__ import annotations

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.dimensoes_processos.services.identificacao_processos.audit import build_audit_report
from apps.dimensoes_processos.services.identificacao_processos.cleanup_vigencia import cleanup_duplicate_vigentes
from apps.dimensoes_processos.services.importer import DEFAULT_XLSX


class Command(BaseCommand):
    help = (
        "Auditoria read-only do workbook Identificação dos Processos vs portal: "
        "duplicatas vigentes, volumes SLA, blockers Capacity e saúde de snapshots."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(DEFAULT_XLSX),
            help=f"Caminho do .xlsx para comparar volumes (default: {DEFAULT_XLSX})",
        )
        parser.add_argument("--date", help="Data de referência AAAA-MM-DD (default: hoje).")
        parser.add_argument("--json", action="store_true", help="Saída JSON.")
        parser.add_argument(
            "--skip-xlsx",
            action="store_true",
            help="Não comparar volumes com o xlsx (somente DB/Capacity).",
        )
        parser.add_argument(
            "--apply-cleanup-vigencia",
            action="store_true",
            help="Finaliza ciclos SLA/meta vigentes duplicados (sem dry-run).",
        )
        parser.add_argument(
            "--dry-run-cleanup",
            action="store_true",
            help="Preview da higienização de duplicatas vigentes.",
        )

    @staticmethod
    def _parse_date(value: str | None) -> date:
        if value is None:
            return timezone.localdate()
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("date inválida; use AAAA-MM-DD.") from exc

    def handle(self, *args, **options):
        on_date = self._parse_date(options.get("date"))
        cleanup_result = None

        if options["apply_cleanup_vigencia"]:
            cleanup_result = cleanup_duplicate_vigentes(dry_run=False)
        elif options["dry_run_cleanup"]:
            cleanup_result = cleanup_duplicate_vigentes(dry_run=True)

        report = build_audit_report(
            None if options["skip_xlsx"] else options["path"],
            on_date=on_date,
        )
        if cleanup_result is not None:
            report["cleanup_vigencia"] = cleanup_result

        if options["json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return

        dup = report["duplicate_vigentes"]
        self.stdout.write(f"Auditoria Identificação dos Processos @ {on_date.isoformat()}")
        self.stdout.write(
            f"  Duplicatas vigentes: {dup['total_groups']} grupos "
            f"({dup['total_rows_to_finalize']} ciclos a finalizar)"
        )
        if report.get("volume_compare"):
            vc = report["volume_compare"]
            self.stdout.write(
                f"  Volumes top {vc['top_n']}: {vc['mismatches']} divergência(s) vs xlsx"
            )
        for sample in report.get("capacity_blockers") or []:
            blockers = sample.get("blockers") or []
            if blockers:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Capacity {sample['date']}: "
                        + ", ".join(f"{b['code']}({b['count']})" for b in blockers)
                    )
                )
        if report.get("snapshot_health"):
            sh = report["snapshot_health"]
            self.stdout.write(
                f"  Snapshots: missing={len(sh.get('missing_dates') or [])} "
                f"incompatible={len(sh.get('incompatible_dates') or [])} "
                f"refresh_required={sh.get('refresh_required')}"
            )
        if cleanup_result:
            mode = "dry-run" if cleanup_result["dry_run"] else "apply"
            self.stdout.write(
                f"  Cleanup vigência ({mode}): {len(cleanup_result['actions'])} ação(ões)"
            )
        style = self.style.SUCCESS if report["healthy"] else self.style.WARNING
        self.stdout.write(style(f"Status geral: {'saudável' if report['healthy'] else 'atenção'}"))
