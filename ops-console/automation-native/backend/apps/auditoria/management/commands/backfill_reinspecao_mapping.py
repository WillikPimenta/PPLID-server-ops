from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
    ReinspecaoMappingChange,
    ReinspecaoMappingRun,
)
from apps.auditoria.services.reinspecao_mapping_backfill import (
    MAPPING_FIELDS,
    apply_decisions,
    build_backfill_preview,
    deserialize_field,
    refresh_projected_dimensions,
    snapshot,
)


class Command(BaseCommand):
    help = "Prévia, aplicação e rollback do enriquecimento histórico de Reinspeção."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--rollback", action="store_true")
        parser.add_argument("--mapping-version", default="")
        parser.add_argument("--expected-source-hash", default="")
        parser.add_argument("--preview-token", default="")
        parser.add_argument("--run-id", default="")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--executed-by", default="")
        parser.add_argument("--report", default="")

    def _user(self, username: str):
        username = str(username or "").strip()
        if not username:
            return None
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"Usuário executor não encontrado: {username}.")
        return user

    def _emit(self, report: dict, report_path: str) -> None:
        output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if report_path:
            Path(report_path).write_text(output + "\n", encoding="utf-8")
        self.stdout.write(output)

    def handle(self, *args, **options):
        del args
        batch_size = int(options["batch_size"])
        if batch_size < 1 or batch_size > 5000:
            raise CommandError("--batch-size deve estar entre 1 e 5000.")
        if options["rollback"]:
            report = self._rollback(options, batch_size=batch_size)
            self._emit(report, options["report"])
            return

        try:
            preview = build_backfill_preview()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        report = {
            "mode": "apply" if options["apply"] else "dry-run",
            "generated_at": timezone.now().isoformat(),
            **preview.report,
        }
        if options["dry_run"]:
            self._emit(report, options["report"])
            return

        expected_version = str(options["mapping_version"] or "").strip()
        expected_hash = str(options["expected_source_hash"] or "").strip().lower()
        expected_token = str(options["preview_token"] or "").strip().lower()
        if expected_version != preview.resolver.mapping_version:
            raise CommandError("--mapping-version difere da matriz ativa.")
        if expected_hash != preview.resolver.source_hash.lower():
            raise CommandError("--expected-source-hash difere da matriz ativa.")
        if expected_token != preview.preview_token.lower():
            raise CommandError("--preview-token expirou; execute --dry-run novamente.")

        user = self._user(options["executed_by"])
        from apps.qualidade_operacional.services.intranet_source import sync_queryset
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )
        from apps.qualidade_operacional.signals import quality_projection_sync_guard

        with transaction.atomic():
            run = ReinspecaoMappingRun.objects.create(
                kind=ReinspecaoMappingRun.KIND_BACKFILL,
                mapping_version=preview.resolver.mapping_version,
                source_hash=preview.resolver.source_hash,
                preview_token=preview.preview_token,
                manifest=report,
                executed_by=user,
            )
            applied = apply_decisions(
                preview.decisions,
                run=run,
                batch_size=batch_size,
            )
            applied.pop("treated_ids")
            treated_sources = applied.pop("treated_sources")
            projection_report = refresh_projected_dimensions(
                treated_sources,
                batch_size=batch_size,
            )
            missing_source_ids = projection_report.pop("missing_source_ids")
            fallback_report = None
            if missing_source_ids:
                with quality_projection_sync_guard():
                    fallback_report = sync_queryset(
                        AuditoriaFalhaCadastro.objects.filter(pk__in=missing_source_ids),
                        batch_size=batch_size,
                        force=True,
                        bump_cache=False,
                    )
            report["applied"] = applied
            report["projection"] = {
                **projection_report,
                "fallback": dict(fallback_report.__dict__) if fallback_report else {},
            }
            report["finished_at"] = timezone.now().isoformat()
            run.status = ReinspecaoMappingRun.STATUS_APPLIED
            run.manifest = report
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "manifest", "finished_at"])
            transaction.on_commit(bump_quality_cache_version)

        self._emit({**report, "run_id": str(run.pk)}, options["report"])

    def _rollback(self, options: dict, *, batch_size: int) -> dict:
        run_id = str(options["run_id"] or "").strip()
        if not run_id:
            raise CommandError("--run-id é obrigatório para rollback.")
        source_run = ReinspecaoMappingRun.objects.filter(
            pk=run_id,
            kind=ReinspecaoMappingRun.KIND_BACKFILL,
        ).first()
        if source_run is None:
            raise CommandError("Run de backfill não encontrado.")
        if source_run.status == ReinspecaoMappingRun.STATUS_ROLLED_BACK:
            raise CommandError("Esse run já foi revertido.")

        user = self._user(options["executed_by"])
        changes = list(source_run.changes.filter(rolled_back_at__isnull=True).order_by("id"))
        pending_ids = [
            change.target_id
            for change in changes
            if change.target_type == ReinspecaoMappingChange.TARGET_PENDING
        ]
        treated_ids = [
            change.target_id
            for change in changes
            if change.target_type == ReinspecaoMappingChange.TARGET_TREATED
        ]
        pending_map = QualidadePendenteReinspecao.objects.in_bulk(pending_ids)
        treated_map = AuditoriaFalhaCadastro.objects.in_bulk(treated_ids)
        pending_updates = []
        treated_updates = []
        conflicts = []
        now = timezone.now()

        for change in changes:
            target = (
                pending_map.get(change.target_id)
                if change.target_type == ReinspecaoMappingChange.TARGET_PENDING
                else treated_map.get(change.target_id)
            )
            if target is None:
                conflicts.append(
                    {"target_type": change.target_type, "target_id": change.target_id, "reason": "missing"}
                )
                change.rolled_back_at = now
                continue
            current = snapshot(target)
            restored = []
            field_conflicts = []
            for field in change.filled_fields:
                if current.get(field) != change.after_values.get(field):
                    field_conflicts.append(field)
                    continue
                setattr(
                    target,
                    field,
                    deserialize_field(field, change.before_values.get(field)),
                )
                restored.append(field)
            if field_conflicts:
                conflicts.append(
                    {
                        "target_type": change.target_type,
                        "target_id": change.target_id,
                        "fields": field_conflicts,
                        "reason": "changed_after_backfill",
                    }
                )
            if restored:
                target.updated_at = now
                if change.target_type == ReinspecaoMappingChange.TARGET_PENDING:
                    pending_updates.append(target)
                else:
                    treated_updates.append(target)
            change.rolled_back_at = now

        from apps.qualidade_operacional.services.intranet_source import sync_queryset
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )
        from apps.qualidade_operacional.signals import quality_projection_sync_guard

        report = {
            "mode": "rollback",
            "source_run_id": str(source_run.pk),
            "mapping_version": source_run.mapping_version,
            "source_hash": source_run.source_hash,
            "requested_changes": len(changes),
            "pending_restored": len(pending_updates),
            "treated_restored": len(treated_updates),
            "conflicts": conflicts,
        }
        with transaction.atomic():
            rollback_run = ReinspecaoMappingRun.objects.create(
                kind=ReinspecaoMappingRun.KIND_ROLLBACK,
                mapping_version=source_run.mapping_version,
                source_hash=source_run.source_hash,
                manifest=report,
                executed_by=user,
            )
            update_fields = list(MAPPING_FIELDS) + ["updated_at"]
            if pending_updates:
                QualidadePendenteReinspecao.objects.bulk_update(
                    pending_updates, update_fields, batch_size=batch_size
                )
            if treated_updates:
                AuditoriaFalhaCadastro.objects.bulk_update(
                    treated_updates, update_fields, batch_size=batch_size
                )
            if changes:
                ReinspecaoMappingChange.objects.bulk_update(
                    changes, ["rolled_back_at"], batch_size=batch_size
                )
            projection_report = refresh_projected_dimensions(
                treated_updates,
                batch_size=batch_size,
            )
            missing_source_ids = projection_report.pop("missing_source_ids")
            fallback_report = None
            if missing_source_ids:
                with quality_projection_sync_guard():
                    fallback_report = sync_queryset(
                        AuditoriaFalhaCadastro.objects.filter(pk__in=missing_source_ids),
                        batch_size=batch_size,
                        force=True,
                        bump_cache=False,
                    )
            report["projection"] = {
                **projection_report,
                "fallback": dict(fallback_report.__dict__) if fallback_report else {},
            }
            report["finished_at"] = timezone.now().isoformat()
            rollback_run.status = ReinspecaoMappingRun.STATUS_APPLIED
            rollback_run.manifest = report
            rollback_run.finished_at = timezone.now()
            rollback_run.save(update_fields=["status", "manifest", "finished_at"])
            source_run.status = ReinspecaoMappingRun.STATUS_ROLLED_BACK
            source_run.finished_at = timezone.now()
            source_run.save(update_fields=["status", "finished_at"])
            transaction.on_commit(bump_quality_cache_version)
        return {**report, "run_id": str(rollback_run.pk)}
