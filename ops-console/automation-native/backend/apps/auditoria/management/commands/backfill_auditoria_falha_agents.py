from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.auditoria.services.agent_links import (
    AgentResolution,
    resolve_agent_for_user,
    resolve_agent_reference,
)


class Command(BaseCommand):
    help = "Preenche agente_id e auditor_id sem modificar os campos legados."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persiste os vinculos. Sem esta opcao, executa apenas dry-run.",
        )
        parser.add_argument(
            "--report",
            help="Caminho opcional para gravar o relatorio CSV detalhado.",
        )
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        batch_size = max(1, int(options["batch_size"]))
        counters = Counter()
        details: list[dict[str, object]] = []
        pending: list[AuditoriaFalhaCadastro] = []

        queryset = AuditoriaFalhaCadastro.objects.order_by("id").only(
            "id",
            "usuario",
            "auditor",
            "tipo_registro",
            "created_by",
            "agente_ref_id",
            "auditor_ref_id",
        ).select_related("created_by")
        with transaction.atomic():
            for record in queryset.iterator(chunk_size=batch_size):
                changed = False
                row = {"falha_id": record.pk, "usuario": record.usuario, "auditor": record.auditor}
                for legacy_field, relation_field, label in (
                    ("usuario", "agente_ref", "agente"),
                    ("auditor", "auditor_ref", "auditor"),
                ):
                    current_id = getattr(record, f"{relation_field}_id")
                    if current_id:
                        status = "already_linked"
                        resolution = None
                    else:
                        legacy_value = getattr(record, legacy_field)
                        if (
                            relation_field == "auditor_ref"
                            and not str(legacy_value or "").strip()
                            and record.tipo_registro == AuditoriaFalhaCadastro.REGISTRO_AUDITORIA
                        ):
                            agent = resolve_agent_for_user(record.created_by)
                            resolution = AgentResolution(
                                agent,
                                "matched" if agent else "not_found",
                                "created_by" if agent else "",
                            )
                        else:
                            resolution = resolve_agent_reference(legacy_value)
                        status = resolution.status
                        if resolution.agent:
                            setattr(record, relation_field, resolution.agent)
                            changed = True
                    counters[f"{label}_{status}"] += 1
                    row[f"{label}_status"] = status
                    row[f"{label}_matched_by"] = resolution.matched_by if resolution else ""
                    row[f"{label}_agent_id"] = (
                        str(resolution.agent.id) if resolution and resolution.agent else str(current_id or "")
                    )
                if changed:
                    pending.append(record)
                details.append(row)

            if apply_changes and pending:
                AuditoriaFalhaCadastro.objects.bulk_update(
                    pending,
                    ["agente_ref", "auditor_ref"],
                    batch_size=batch_size,
                )
            if not apply_changes:
                transaction.set_rollback(True)

        report_path = options.get("report")
        if report_path:
            path = Path(report_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(details[0]) if details else ["falha_id"])
                writer.writeheader()
                writer.writerows(details)
            self.stdout.write(f"Relatorio: {path}")

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: registros={len(details)} atualizados={len(pending)}"))
        for key in sorted(counters):
            self.stdout.write(f"{key}={counters[key]}")
