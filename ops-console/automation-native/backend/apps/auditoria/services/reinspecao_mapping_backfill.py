from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
    ReinspecaoMappingChange,
)
from apps.auditoria.services.reinspecao_mapping import (
    STATUS_AMBIGUOUS,
    STATUS_CONFLICT,
    STATUS_MATCHED,
    STATUS_PREEXISTING,
    STATUS_UNMATCHED,
    ReinspecaoMappingResolver,
    normalize_display_text,
    status_for_existing,
)

MAPPING_FIELDS = (
    "codigo_irregularidade",
    "motivo_falha",
    "etapa_falha",
    "mapping_scenario_status",
    "mapping_stage_status",
    "mapping_version",
    "mapping_source_hash",
    "mapping_applied_at",
)


def treated_reinspection_q() -> Q:
    return Q(origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO) | Q(
        origem="",
        tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
    )


def target_querysets():
    fields = (
        "id",
        "protocolo",
        "descricao_irregularidades",
        "codigo_irregularidade",
        "motivo_falha",
        "etapa_falha",
        "mapping_scenario_status",
        "mapping_stage_status",
        "mapping_version",
        "mapping_source_hash",
        "mapping_applied_at",
        "data_contestacao",
        "data_analise",
        "created_at",
        "updated_at",
    )
    pending = QualidadePendenteReinspecao.objects.filter(contexto="reinspecao").only(
        *fields
    )
    treated = AuditoriaFalhaCadastro.objects.filter(treated_reinspection_q()).only(
        *fields,
        "analise_concluida_em",
    )
    return (
        (ReinspecaoMappingChange.TARGET_PENDING, pending.order_by("pk")),
        (ReinspecaoMappingChange.TARGET_TREATED, treated.order_by("pk")),
    )


def _serial_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def snapshot(instance) -> dict[str, Any]:
    return {field: _serial_value(getattr(instance, field)) for field in MAPPING_FIELDS}


@dataclass
class BackfillDecision:
    target_type: str
    instance: Any
    before: dict[str, Any]
    after: dict[str, Any]
    changed_fields: list[str]
    code: str
    parsed: bool
    scenario_status: str
    stage_status: str
    candidates: list[dict[str, Any]]


def decide(instance, *, target_type: str, resolver: ReinspecaoMappingResolver) -> BackfillDecision:
    resolution = resolver.resolve(instance.descricao_irregularidades)
    before = snapshot(instance)
    after = dict(before)

    existing_code = normalize_display_text(instance.codigo_irregularidade)
    if not existing_code and resolution.extracted.code:
        after["codigo_irregularidade"] = resolution.extracted.code

    same_mapping = (
        instance.mapping_version == resolution.mapping_version
        and instance.mapping_source_hash.lower() == resolution.source_hash.lower()
    )
    scenario_status = (
        resolution.scenario_status
        if same_mapping
        and normalize_display_text(instance.motivo_falha)
        == normalize_display_text(resolution.scenario)
        else status_for_existing(
            instance.motivo_falha, resolution.scenario, resolution.scenario_status
        )
    )
    stage_status = (
        resolution.stage_status
        if same_mapping
        and normalize_display_text(instance.etapa_falha)
        == normalize_display_text(resolution.stage)
        else status_for_existing(
            instance.etapa_falha, resolution.stage, resolution.stage_status
        )
    )
    if not normalize_display_text(instance.motivo_falha) and resolution.scenario:
        after["motivo_falha"] = resolution.scenario
    if not normalize_display_text(instance.etapa_falha) and resolution.stage:
        after["etapa_falha"] = resolution.stage

    after["mapping_scenario_status"] = scenario_status
    after["mapping_stage_status"] = stage_status
    after["mapping_version"] = resolution.mapping_version
    after["mapping_source_hash"] = resolution.source_hash
    changed_fields = [field for field in MAPPING_FIELDS if after[field] != before[field]]
    if changed_fields and "mapping_applied_at" not in changed_fields:
        changed_fields.append("mapping_applied_at")
    return BackfillDecision(
        target_type=target_type,
        instance=instance,
        before=before,
        after=after,
        changed_fields=changed_fields,
        code=resolution.extracted.code,
        parsed=resolution.extracted.parsed,
        scenario_status=scenario_status,
        stage_status=stage_status,
        candidates=resolution.diagnostic_candidates(),
    )


def _period(instance) -> str:
    value = (
        getattr(instance, "data_analise", None)
        or getattr(instance, "data_contestacao", None)
        or getattr(instance, "analise_concluida_em", None)
        or getattr(instance, "created_at", None)
    )
    if value is None:
        return "sem_data"
    return value.strftime("%Y-%m")


@dataclass
class BackfillPreview:
    resolver: ReinspecaoMappingResolver
    decisions: list[BackfillDecision]
    report: dict[str, Any]
    preview_token: str


def build_backfill_preview(
    *, resolver: ReinspecaoMappingResolver | None = None
) -> BackfillPreview:
    resolver = resolver or ReinspecaoMappingResolver.from_active_mapping()
    if not resolver.mapping_version or not resolver.source_hash:
        raise ValueError("Nenhuma matriz de Reinspeção aprovada e ativa está disponível.")

    decisions: list[BackfillDecision] = []
    counters = Counter()
    issues: dict[tuple[str, str, str], dict[str, Any]] = {}
    distributions = {"scenario": Counter(), "stage": Counter()}
    periods: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"rows": 0, "protocols": set()}
    )
    target_counts: dict[str, int] = Counter()

    for target_type, queryset in target_querysets():
        for instance in queryset.iterator(chunk_size=500):
            decision = decide(instance, target_type=target_type, resolver=resolver)
            decisions.append(decision)
            target_counts[target_type] += 1
            counters["rows"] += 1
            counters["parsed"] += int(decision.parsed)
            counters["matched"] += int(
                decision.scenario_status in {STATUS_MATCHED, STATUS_PREEXISTING}
                or decision.stage_status in {STATUS_MATCHED, STATUS_PREEXISTING}
            )
            counters["unmatched"] += int(
                decision.scenario_status == STATUS_UNMATCHED
                and decision.stage_status == STATUS_UNMATCHED
            )
            counters["ambiguous_scenario"] += int(
                decision.scenario_status == STATUS_AMBIGUOUS
            )
            counters["ambiguous_stage"] += int(decision.stage_status == STATUS_AMBIGUOUS)
            counters["already_filled"] += int(
                bool(normalize_display_text(decision.before["motivo_falha"]))
                or bool(normalize_display_text(decision.before["etapa_falha"]))
            )
            counters["conflicting_existing"] += int(
                decision.scenario_status == STATUS_CONFLICT
                or decision.stage_status == STATUS_CONFLICT
            )
            counters["changed"] += int(bool(decision.changed_fields))
            counters["would_fill_scenario"] += int(
                "motivo_falha" in decision.changed_fields
            )
            counters["would_fill_stage"] += int("etapa_falha" in decision.changed_fields)
            counters["would_fill_code"] += int(
                "codigo_irregularidade" in decision.changed_fields
            )

            scenario = normalize_display_text(decision.after["motivo_falha"])
            stage = normalize_display_text(decision.after["etapa_falha"])
            distributions["scenario"][scenario or "Não informado"] += 1
            distributions["stage"][stage or "Não informado"] += 1
            period = periods[_period(instance)]
            period["rows"] += 1
            if normalize_display_text(instance.protocolo):
                period["protocols"].add(normalize_display_text(instance.protocolo))

            if decision.scenario_status in {STATUS_UNMATCHED, STATUS_AMBIGUOUS, STATUS_CONFLICT} or decision.stage_status in {
                STATUS_UNMATCHED,
                STATUS_AMBIGUOUS,
                STATUS_CONFLICT,
            }:
                key = (decision.code or "<sem_codigo>", decision.scenario_status, decision.stage_status)
                issue = issues.setdefault(
                    key,
                    {
                        "code": decision.code,
                        "scenario_status": decision.scenario_status,
                        "stage_status": decision.stage_status,
                        "rows": 0,
                        "targets": Counter(),
                        "candidates": decision.candidates,
                    },
                )
                issue["rows"] += 1
                issue["targets"][target_type] += 1

    token_rows = [
        {
            "target_type": item.target_type,
            "target_id": item.instance.pk,
            "source_updated_at": _serial_value(item.instance.updated_at),
            "before": item.before,
            "after": {key: value for key, value in item.after.items() if key != "mapping_applied_at"},
        }
        for item in decisions
    ]
    token_payload = {
        "mapping_version": resolver.mapping_version,
        "source_hash": resolver.source_hash,
        "targets": token_rows,
    }
    preview_token = hashlib.sha256(
        json.dumps(token_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()

    report_issues = []
    for issue in sorted(issues.values(), key=lambda value: value["code"] or ""):
        report_issues.append({**issue, "targets": dict(issue["targets"])})
    report = {
        "mapping_version": resolver.mapping_version,
        "source_hash": resolver.source_hash,
        "preview_token": preview_token,
        "targets": dict(target_counts),
        "counts": dict(counters),
        "issues": report_issues,
        "distribution": {
            "scenario": dict(distributions["scenario"]),
            "stage": dict(distributions["stage"]),
        },
        "periods": {
            key: {
                "rows": value["rows"],
                "distinct_protocols": len(value["protocols"]),
            }
            for key, value in sorted(periods.items())
        },
    }
    return BackfillPreview(
        resolver=resolver,
        decisions=decisions,
        report=report,
        preview_token=preview_token,
    )


def apply_decisions(
    decisions: Iterable[BackfillDecision],
    *,
    run,
    batch_size: int,
) -> dict[str, Any]:
    now = timezone.now()
    changes: list[ReinspecaoMappingChange] = []
    pending: list[QualidadePendenteReinspecao] = []
    treated: list[AuditoriaFalhaCadastro] = []
    treated_ids: list[int] = []
    updated_fields = list(MAPPING_FIELDS) + ["updated_at"]

    for decision in decisions:
        if not decision.changed_fields:
            continue
        decision.after["mapping_applied_at"] = now.isoformat()
        before = decision.before
        for field in MAPPING_FIELDS:
            value = decision.after[field]
            if field == "mapping_applied_at" and isinstance(value, str):
                value = parse_datetime(value)
            setattr(decision.instance, field, value)
        decision.instance.updated_at = now
        changes.append(
            ReinspecaoMappingChange(
                run=run,
                target_type=decision.target_type,
                target_id=decision.instance.pk,
                before_values=before,
                after_values=decision.after,
                filled_fields=decision.changed_fields,
            )
        )
        if decision.target_type == ReinspecaoMappingChange.TARGET_PENDING:
            pending.append(decision.instance)
        else:
            treated.append(decision.instance)
            treated_ids.append(decision.instance.pk)

    if pending:
        QualidadePendenteReinspecao.objects.bulk_update(
            pending, updated_fields, batch_size=batch_size
        )
    if treated:
        AuditoriaFalhaCadastro.objects.bulk_update(
            treated, updated_fields, batch_size=batch_size
        )
    if changes:
        ReinspecaoMappingChange.objects.bulk_create(changes, batch_size=batch_size)
    return {
        "changed": len(changes),
        "pending_changed": len(pending),
        "treated_changed": len(treated),
        "treated_ids": treated_ids,
        "treated_sources": treated,
    }


def deserialize_field(field: str, value: Any) -> Any:
    if field == "mapping_applied_at" and isinstance(value, str):
        return parse_datetime(value)
    return value


def refresh_projected_dimensions(
    sources: Iterable[AuditoriaFalhaCadastro],
    *,
    batch_size: int,
) -> dict[str, Any]:
    """Atualiza somente Cenário/Etapa nas pontes existentes, sem mudar população."""
    from apps.qualidade_operacional.models import (
        QualidadeAuditado,
        QualidadeFalha,
        QualidadeIntranetProjection,
    )
    from apps.qualidade_operacional.services.source_config import MAPPING_VERSION

    source_map = {source.pk: source for source in sources}
    if not source_map:
        return {
            "sources": 0,
            "projections": 0,
            "auditados_updated": 0,
            "falhas_updated": 0,
            "missing_source_ids": [],
        }
    projection_rows = list(
        QualidadeIntranetProjection.objects.filter(source_id__in=source_map).values(
            "id", "source_id", "auditado_id", "falha_id"
        )
    )
    auditados = []
    falhas = []
    projections = []
    projected_source_ids = set()
    for row in projection_rows:
        source = source_map[row["source_id"]]
        projected_source_ids.add(source.pk)
        if row["auditado_id"]:
            auditados.append(
                QualidadeAuditado(
                    pk=row["auditado_id"],
                    cenario=source.motivo_falha or "",
                    etapa=source.etapa_falha or "",
                )
            )
        if row["falha_id"]:
            falhas.append(
                QualidadeFalha(
                    pk=row["falha_id"],
                    cenario=source.motivo_falha or "",
                    etapa=source.etapa_falha or "",
                )
            )
        projections.append(
            QualidadeIntranetProjection(
                pk=row["id"],
                mapping_version=MAPPING_VERSION,
                source_updated_at=source.updated_at,
            )
        )

    if auditados:
        QualidadeAuditado.objects.bulk_update(
            auditados, ["cenario", "etapa"], batch_size=batch_size
        )
    if falhas:
        QualidadeFalha.objects.bulk_update(
            falhas, ["cenario", "etapa"], batch_size=batch_size
        )
    if projections:
        QualidadeIntranetProjection.objects.bulk_update(
            projections,
            ["mapping_version", "source_updated_at"],
            batch_size=batch_size,
        )
    return {
        "sources": len(source_map),
        "projections": len(projections),
        "auditados_updated": len(auditados),
        "falhas_updated": len(falhas),
        "missing_source_ids": sorted(set(source_map) - projected_source_ids),
    }
