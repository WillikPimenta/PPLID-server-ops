# -*- coding: utf-8 -*-
"""Comando read-only de auditoria dos dados D-1."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.common.models import BotDataArtifact, BotDataIngestion
from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Reconciliacao,
    ReplicacaoD1Replicado,
    ReplicacaoD1Run,
    ReplicacaoD1SyncLog,
    ReplicacaoD1WorkflowDia,
)


class Command(BaseCommand):
    help = "Auditoria read-only dos dados de replicação D-1 (contagens, duplicatas, lacunas)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Saída em JSON (sem dados sensíveis de protocolo).",
        )

    def handle(self, *args, **options):
        report = self._build_report()
        if options.get("json"):
            self.stdout.write(json.dumps(report, indent=2, default=str))
        else:
            self._print_human(report)

    def _build_report(self) -> dict:
        runs = ReplicacaoD1Run.objects.count()
        workflows = ReplicacaoD1WorkflowDia.objects.count()
        protocolos = ReplicacaoD1Protocolo.objects.count()
        replicados = ReplicacaoD1Replicado.objects.count()
        ingestions = BotDataIngestion.objects.filter(domain="replicacao_d1").count()
        artifacts = BotDataArtifact.objects.filter(domain="replicacao_d1").count()
        sync_logs = ReplicacaoD1SyncLog.objects.count()
        reconciliacoes = ReplicacaoD1Reconciliacao.objects.count()

        prot_sem_ingestion = ReplicacaoD1Protocolo.objects.filter(ingestion__isnull=True).count()
        rep_sem_ingestion = ReplicacaoD1Replicado.objects.filter(ingestion__isnull=True).count()
        runs_sem_snapshot = ReplicacaoD1Run.objects.filter(config_snapshot__isnull=True).count()

        dup_raw = (
            ReplicacaoD1Protocolo.objects.values("run_id", "protocolo")
            .annotate(c=Count("id"))
            .filter(c__gt=1)
            .count()
        )
        dup_norm = (
            ReplicacaoD1Protocolo.objects.exclude(protocolo_normalizado="")
            .values("run_id", "protocolo_normalizado")
            .annotate(c=Count("id"))
            .filter(c__gt=1)
            .count()
        )

        runs_por_data = list(
            ReplicacaoD1Run.objects.values("data_referencia_d1")
            .annotate(c=Count("run_id"))
            .filter(c__gt=1)
            .order_by("-c")[:20]
        )

        status_protocolo = dict(
            ReplicacaoD1Protocolo.objects.values("status_operacional")
            .annotate(c=Count("id"))
            .values_list("status_operacional", "c")
        )

        run_status = dict(
            ReplicacaoD1Run.objects.values("status_canonical")
            .annotate(c=Count("id"))
            .values_list("status_canonical", "c")
        )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "runs": runs,
                "workflows": workflows,
                "protocolos": protocolos,
                "replicados": replicados,
                "ingestions_d1": ingestions,
                "artifacts_d1": artifacts,
                "sync_logs": sync_logs,
                "reconciliacoes": reconciliacoes,
            },
            "gaps": {
                "protocolos_sem_ingestion": prot_sem_ingestion,
                "replicados_sem_ingestion": rep_sem_ingestion,
                "runs_sem_config_snapshot": runs_sem_snapshot,
            },
            "duplicates": {
                "protocolo_bruto_por_run": dup_raw,
                "protocolo_normalizado_por_run": dup_norm,
            },
            "runs_multiplos_por_data": runs_por_data,
            "status_protocolo": status_protocolo,
            "status_run": run_status,
        }

    def _print_human(self, report: dict) -> None:
        self.stdout.write("=== Auditoria Replicação D-1 ===")
        self.stdout.write(f"Gerado em: {report['generated_at']}")
        self.stdout.write("\nContagens:")
        for key, val in report["counts"].items():
            self.stdout.write(f"  {key}: {val}")
        self.stdout.write("\nLacunas:")
        for key, val in report["gaps"].items():
            self.stdout.write(f"  {key}: {val}")
        self.stdout.write("\nDuplicatas:")
        for key, val in report["duplicates"].items():
            self.stdout.write(f"  {key}: {val}")
        if report["runs_multiplos_por_data"]:
            self.stdout.write("\nDatas com múltiplos runs:")
            for row in report["runs_multiplos_por_data"]:
                self.stdout.write(f"  {row['data_referencia_d1']}: {row['c']} runs")
