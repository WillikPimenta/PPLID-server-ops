# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand

from apps.replicacao_d1.models import ReplicacaoD1Run
from apps.replicacao_d1.services.artifact_retention import evaluate_run_retention_gate


class Command(BaseCommand):
    help = "Audita elegibilidade de retenção de artefatos D-1 (dry-run de gates)."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", type=str, default="", help="Run específico.")
        parser.add_argument(
            "--min-age-days",
            type=int,
            default=0,
            help="Exige idade mínima do run/ingestão (0 = ignorar).",
        )
        parser.add_argument(
            "--allow-without-ingestion",
            action="store_true",
            help="Não exige ingestão comprovada (modo legado).",
        )

    def handle(self, *args, **options):
        run_id = (options.get("run_id") or "").strip()
        min_age = int(options.get("min_age_days") or 0)
        require_ingestion = not bool(options.get("allow_without_ingestion"))

        if run_id:
            run_ids = [run_id]
        else:
            run_ids = list(
                ReplicacaoD1Run.objects.order_by("-data_referencia_d1", "-run_id").values_list(
                    "run_id", flat=True
                )[:200]
            )

        allowed = 0
        blocked = 0
        for rid in run_ids:
            result = evaluate_run_retention_gate(
                rid,
                min_age_days=min_age,
                require_ingestion=require_ingestion,
            )
            status = "OK" if result.allowed else "BLOCK"
            if result.allowed:
                allowed += 1
            else:
                blocked += 1
            reasons = "; ".join(result.reasons) if result.reasons else "-"
            self.stdout.write(f"[{status}] {rid} | {reasons}")

        self.stdout.write(f"\nResumo: {allowed} elegível(is), {blocked} bloqueado(s), {len(run_ids)} avaliado(s).")
