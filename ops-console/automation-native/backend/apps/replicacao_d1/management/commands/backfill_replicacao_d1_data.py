# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Replicado
from apps.replicacao_d1.normalization import normalize_protocolo
from apps.replicacao_d1.models import ReplicacaoD1SyncLog
from apps.replicacao_d1.services.reconciliation import reconcile_protocolos_for_run
from apps.replicacao_d1.services.ingestion import finish_ingestion, start_ingestion


class Command(BaseCommand):
    help = "Backfill D-1: normaliza protocolos e reconcilia runs (dry-run por padrão)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula alterações sem gravar (default quando --apply não é passado).",
        )
        parser.add_argument("--apply", action="store_true", help="Aplica alterações (default: dry-run).")
        parser.add_argument("--batch-size", type=int, default=2000)
        parser.add_argument("--run-id", dest="run_id", default="")
        parser.add_argument("--date-from", dest="date_from", default="")
        parser.add_argument("--date-to", dest="date_to", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        if options.get("dry_run") and options.get("apply"):
            raise CommandError("Use apenas --dry-run ou --apply, não ambos.")
        apply = bool(options.get("apply"))
        batch_size = max(100, int(options.get("batch_size") or 2000))
        run_id = str(options.get("run_id") or "").strip()
        date_from = str(options.get("date_from") or "").strip()
        date_to = str(options.get("date_to") or "").strip()
        try:
            report_from = datetime.fromisoformat(date_from).date() + timedelta(days=1) if date_from else None
            report_to = datetime.fromisoformat(date_to).date() + timedelta(days=1) if date_to else None
        except ValueError as exc:
            raise CommandError("Datas devem usar o formato YYYY-MM-DD.") from exc

        qs_prot = ReplicacaoD1Protocolo.objects.all()
        qs_rep = ReplicacaoD1Replicado.objects.all()
        if run_id:
            qs_prot = qs_prot.filter(run_id=run_id)
        if date_from:
            qs_prot = qs_prot.filter(run__data_execucao__date__gte=date_from)
            qs_rep = qs_rep.filter(report_date__gte=report_from)
        if date_to:
            qs_prot = qs_prot.filter(run__data_execucao__date__lte=date_to)
            qs_rep = qs_rep.filter(report_date__lte=report_to)

        missing_norm = qs_prot.filter(Q(protocolo_normalizado="") | Q(protocolo_normalizado__isnull=True))
        rep_missing = qs_rep.filter(
            Q(protocolo_origem_normalizado="") | Q(protocolo_origem_normalizado__isnull=True)
        )

        run_ids = list(qs_prot.order_by().values_list("run_id", flat=True).distinct())

        legacy_logs = ReplicacaoD1SyncLog.objects.filter(ingestion__isnull=True)
        if run_id:
            legacy_logs = legacy_logs.filter(run_id=run_id)
        if date_from:
            legacy_logs = legacy_logs.filter(report_date__gte=date_from)
        if date_to:
            legacy_logs = legacy_logs.filter(report_date__lte=date_to)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": not apply,
            "before": {
                "protocolos_total": qs_prot.count(),
                "replicados_total": qs_rep.count(),
                "protocolos_sem_normalizado": missing_norm.count(),
                "replicados_sem_normalizado": rep_missing.count(),
            },
            "planned": {
                "protocolos_a_normalizar": missing_norm.count(),
                "replicados_a_normalizar": rep_missing.count(),
                "runs_a_reconciliar": len(run_ids),
                "sync_logs_a_vincular": legacy_logs.count(),
            },
        }

        if apply:
            updated_prot = 0
            for row in missing_norm.iterator(chunk_size=batch_size):
                norm = normalize_protocolo(row.protocolo)
                if norm and norm != row.protocolo_normalizado:
                    ReplicacaoD1Protocolo.objects.filter(pk=row.pk).update(protocolo_normalizado=norm)
                    updated_prot += 1
            updated_rep = 0
            for row in rep_missing.iterator(chunk_size=batch_size):
                norm = normalize_protocolo(row.protocolo_origem)
                if norm and norm != row.protocolo_origem_normalizado:
                    ReplicacaoD1Replicado.objects.filter(pk=row.pk).update(
                        protocolo_origem_normalizado=norm
                    )
                    updated_rep += 1
            reconciled_runs = 0
            for rid in run_ids:
                reconcile_protocolos_for_run(str(rid))
                reconciled_runs += 1
            linked_logs = 0
            linked_protocolos = 0
            linked_replicados = 0
            for log in legacy_logs.iterator(chunk_size=batch_size):
                kind = "replicados" if log.kind == ReplicacaoD1SyncLog.KIND_REPLICADOS else "plano"
                ingestion = start_ingestion(
                    domain="replicacao_d1",
                    kind=kind,
                    reference_date=log.report_date,
                    run_id=log.run_id,
                    source_file=log.source_file,
                    source_mtime=log.source_mtime,
                    source_size=log.source_size,
                )
                ingestion.sync_log_id = log.pk
                ingestion.save(update_fields=["sync_log_id"])
                finish_ingestion(
                    ingestion,
                    success=bool(log.success),
                    rows_read=int(log.rows_read or log.row_count or 0),
                    rows_loaded=int(log.rows_loaded or log.row_count or 0),
                    rows_rejected=int(log.rows_rejected or 0),
                    error_summary=log.message if not log.success else "",
                )
                log.ingestion = ingestion
                log.save(update_fields=["ingestion"])
                linked_logs += 1

                if kind == "plano" and log.run_id:
                    updated = ReplicacaoD1Protocolo.objects.filter(
                        run_id=log.run_id, ingestion__isnull=True
                    ).update(ingestion=ingestion)
                    linked_protocolos += int(updated)
                elif kind == "replicados" and log.report_date:
                    updated = ReplicacaoD1Replicado.objects.filter(
                        report_date=log.report_date, ingestion__isnull=True
                    ).update(ingestion=ingestion)
                    linked_replicados += int(updated)
            report["after"] = {
                "protocolos_sem_normalizado": qs_prot.filter(
                    Q(protocolo_normalizado="") | Q(protocolo_normalizado__isnull=True)
                ).count(),
                "replicados_sem_normalizado": qs_rep.filter(
                    Q(protocolo_origem_normalizado="") | Q(protocolo_origem_normalizado__isnull=True)
                ).count(),
            }
            report["applied"] = {
                "updated_protocolos": updated_prot,
                "updated_replicados": updated_rep,
                "reconciled_runs": reconciled_runs,
                "linked_sync_logs": linked_logs,
                "linked_protocolos": linked_protocolos,
                "linked_replicados": linked_replicados,
            }

        if options.get("json"):
            self.stdout.write(json.dumps(report, indent=2, default=str))
        else:
            mode = "APPLY" if apply else "DRY-RUN"
            self.stdout.write(f"Backfill D-1 [{mode}]")
            for key, val in report.items():
                self.stdout.write(f"  {key}: {val}")
