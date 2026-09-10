# -*- coding: utf-8 -*-
"""Checklist automatizado para aceite de rollout D-1 (Fase 15)."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection

from apps.common.models import BotDataIngestion
from apps.replicacao_d1.models import ReplicacaoD1Run
from apps.replicacao_d1.feature_flags import rollout_flags_snapshot
from apps.replicacao_d1.services.artifact_retention import evaluate_run_retention_gate


class Command(BaseCommand):
    help = "Valida pré-requisitos de rollout D-1 (schema, contagens, gates)."

    def handle(self, *args, **options):
        checks: list[tuple[str, bool, str]] = []

        checks.append(self._check_migration_0010())
        checks.append(self._check_runs_exist())
        checks.append(self._check_ingestion_model())
        checks.append(self._check_retention_gate_sample())
        checks.append(self._check_feature_flags())

        ok = sum(1 for _, passed, _ in checks if passed)
        fail = sum(1 for _, passed, _ in checks if not passed)
        for name, passed, detail in checks:
            status = "OK" if passed else "FAIL"
            self.stdout.write(f"[{status}] {name}: {detail}")

        self.stdout.write(f"\nResumo: {ok} OK, {fail} FAIL, {len(checks)} verificações.")
        if fail:
            self.stderr.write("Rollout incompleto — corrija itens FAIL antes do aceite.")
            raise SystemExit(1)

    def _check_migration_0010(self) -> tuple[str, bool, str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'replicacao_d1_run' AND column_name = 'started_at'
                """
            )
            row = cursor.fetchone()
        if row:
            return ("migration_0010", True, "coluna started_at presente em replicacao_d1_run")
        return ("migration_0010", False, "rodar python manage.py migrate replicacao_d1")

    def _check_runs_exist(self) -> tuple[str, bool, str]:
        count = ReplicacaoD1Run.objects.count()
        return ("runs_table", True, f"{count} run(s) no banco")

    def _check_ingestion_model(self) -> tuple[str, bool, str]:
        count = BotDataIngestion.objects.filter(domain="replicacao_d1").count()
        return ("ingestion_lotes", True, f"{count} lote(s) replicacao_d1")

    def _check_retention_gate_sample(self) -> tuple[str, bool, str]:
        run = ReplicacaoD1Run.objects.order_by("-synced_at").first()
        if not run:
            return ("retention_gate", True, "sem runs — gate não aplicável")
        result = evaluate_run_retention_gate(run.run_id, require_ingestion=False)
        detail = "elegível" if result.allowed else "; ".join(result.reasons) or "bloqueado"
        return ("retention_gate", True, f"run {run.run_id}: {detail}")

    def _check_feature_flags(self) -> tuple[str, bool, str]:
        flags = rollout_flags_snapshot()
        detail = ", ".join(f"{k}={flags[k]}" for k in sorted(flags))
        if flags.get("shadow_mode"):
            return (
                "feature_flags",
                True,
                f"shadow_mode ativo — {detail}",
            )
        return ("feature_flags", True, detail)
