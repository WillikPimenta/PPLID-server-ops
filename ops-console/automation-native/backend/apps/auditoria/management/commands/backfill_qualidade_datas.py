from __future__ import annotations

import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditoria.models import AuditoriaFalhaCadastro, QualidadeAnaliseOrigem
from apps.auditoria.services.analise_origem import extrair_datas_origem


ORIGIN_FIELDS = (
    "protocolo_criado_em",
    "protocolo_analisado_em",
    "protocolo_concluido_em",
)
TREATED_FIELDS = (
    "data_analise_intranet",
    "data_recepcao_contestacao",
    "data_encerramento_atividade_intranet",
)


def _serialized(value):
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value)
        value = value.astimezone(dt_timezone.utc)
    return value.isoformat() if value is not None else None


def _changes(instance, proposed: dict) -> dict:
    return {
        field: {"before": _serialized(getattr(instance, field)), "after": _serialized(value)}
        for field, value in proposed.items()
        if getattr(instance, field) is None and value is not None
    }


def _decoded(value):
    if value is None:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"Timestamp inválido no backup: {value!r}")
    return parsed


class Command(BaseCommand):
    help = (
        "Preenche datas explícitas de Qualidade. É dry-run por padrão; "
        "--apply exige backup JSONL e --rollback-from desfaz somente valores inalterados."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--backup-path", default="")
        parser.add_argument("--rollback-from", default="")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        if options["rollback_from"]:
            if options["apply"] or options["backup_path"]:
                raise CommandError("--rollback-from não pode ser combinado com --apply/--backup-path.")
            self._rollback(Path(options["rollback_from"]))
            return
        apply = bool(options["apply"])
        backup_path = Path(options["backup_path"]) if options["backup_path"] else None
        if apply and backup_path is None:
            raise CommandError("--apply exige --backup-path em arquivo novo.")
        if backup_path and backup_path.exists():
            raise CommandError("O backup já existe; informe um caminho novo para não sobrescrevê-lo.")
        self._backfill(apply=apply, backup_path=backup_path, limit=options["limit"])

    def _backfill(self, *, apply: bool, backup_path: Path | None, limit: int | None):
        # A tabela central contém apenas tratados concluídos; ``analise_status``
        # virou propriedade de compatibilidade e não é mais coluna consultável.
        eligible = Q(analise_concluida_em__isnull=False)
        treated_qs = (
            AuditoriaFalhaCadastro.objects.filter(eligible)
            .select_related("atividade", "analise_origem")
            .order_by("pk")
        )
        if limit is not None:
            treated_qs = treated_qs[: max(0, limit)]
        treated = list(treated_qs)
        origin_ids = sorted({row.analise_origem_id for row in treated if row.analise_origem_id})
        origins = list(QualidadeAnaliseOrigem.objects.filter(pk__in=origin_ids).order_by("pk"))

        records: list[dict] = []
        origin_updates = []
        for origin in origins:
            changes = _changes(origin, extrair_datas_origem(origin.brflow_parsed))
            if changes:
                records.append({"model": "auditoria.QualidadeAnaliseOrigem", "pk": origin.pk, "changes": changes})
                for field, values in changes.items():
                    setattr(origin, field, _decoded(values["after"]))
                origin_updates.append(origin)

        treated_updates = []
        for row in treated:
            proposed = {
                "data_analise_intranet": row.analise_concluida_em,
                "data_recepcao_contestacao": row.data_contestacao,
                "data_encerramento_atividade_intranet": (
                    row.atividade.encerrado_em if row.atividade_id and row.atividade else None
                ),
            }
            changes = _changes(row, proposed)
            if changes:
                records.append({"model": "auditoria.AuditoriaFalhaCadastro", "pk": row.pk, "changes": changes})
                for field, values in changes.items():
                    setattr(row, field, _decoded(values["after"]))
                treated_updates.append(row)

        summary = {
            "mode": "apply" if apply else "dry-run",
            "eligible_tratados": len(treated),
            "origens_compartilhadas": len(origins),
            "origens_a_atualizar": len(origin_updates),
            "tratados_a_atualizar": len(treated_updates),
            "campos_a_atualizar": sum(len(record["changes"]) for record in records),
        }
        if not apply:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        assert backup_path is not None
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("x", encoding="utf-8") as backup:
            for record in records:
                backup.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            with transaction.atomic():
                if origin_updates:
                    QualidadeAnaliseOrigem.objects.bulk_update(origin_updates, ORIGIN_FIELDS)
                if treated_updates:
                    AuditoriaFalhaCadastro.objects.bulk_update(treated_updates, TREATED_FIELDS)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
        summary["backup_path"] = str(backup_path.resolve())
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))

    def _rollback(self, backup_path: Path):
        if not backup_path.is_file():
            raise CommandError(f"Backup não encontrado: {backup_path}")
        records = [json.loads(line) for line in backup_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        restored = 0
        with transaction.atomic():
            for record in records:
                app_label, model_name = record["model"].split(".", 1)
                model = apps.get_model(app_label, model_name)
                instance = model.objects.select_for_update().get(pk=record["pk"])
                fields = []
                for field, values in record["changes"].items():
                    if _serialized(getattr(instance, field)) != values["after"]:
                        raise CommandError(
                            f"Rollback abortado: {record['model']}#{record['pk']}.{field} mudou após o backfill."
                        )
                    setattr(instance, field, _decoded(values["before"]))
                    fields.append(field)
                instance.save(update_fields=fields)
                restored += len(fields)
        self.stdout.write(json.dumps({"mode": "rollback", "campos_restaurados": restored}, ensure_ascii=False))
