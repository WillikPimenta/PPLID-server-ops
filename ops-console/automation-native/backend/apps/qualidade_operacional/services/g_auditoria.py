# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeGAuditoriaFailureReconciliation,
    QualidadeGAuditoriaProjection,
    QualidadeIntranetProjection,
)
from apps.qualidade_operacional.services.source_config import (
    G_AUDITORIA_SOURCE_FILE,
    INTRANET_SOURCE_FILE,
)
from apps.rotina_bruto.models import RotinaGAuditoriaRecord
from apps.rotina_bruto.services.g_auditoria_sync import (
    BATCH_SIZE,
    MAPPING_VERSION,
    normalize_dimension,
    normalize_matricula,
    normalize_protocol,
    normalize_stage,
)


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _dimension_index(model, id_field: str) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(list)
    for raw_id, name in model.objects.values_list(id_field, "nome").iterator(
        chunk_size=2000
    ):
        key = normalize_dimension(name)
        if key:
            result[key].append(int(raw_id))
    return result


def _resolve_dimension(
    raw: str,
    index: dict[str, list[int]],
    label: str,
    warnings: list[str],
) -> int | None:
    key = normalize_dimension(raw)
    if not key:
        warnings.append(f"{label}_ausente")
        return None
    values = sorted(set(index.get(key) or []))
    if len(values) == 1:
        return values[0]
    if len(values) > 1:
        warnings.append(f"{label}_ambiguo:{raw[:80]}")
    else:
        warnings.append(f"{label}_nao_encontrado:{raw[:80]}")
    return None


def _projection_payload(
    staging: RotinaGAuditoriaRecord,
    clientes: dict[str, list[int]],
    workflows: dict[str, list[int]],
) -> tuple[dict[str, Any], list[str], str]:
    warnings = list(staging.validation_errors or [])
    id_cliente = _resolve_dimension(
        staging.cliente_origem, clientes, "cliente", warnings
    )
    id_workflow = _resolve_dimension(
        staging.workflow_origem, workflows, "workflow", warnings
    )
    fields: dict[str, Any] = {
        "data": staging.data_auditoria,
        "data_analise": staging.data_analise,
        "data_analise_intranet": None,
        "data_analise_origem": staging.data_analise,
        "data_criacao_origem": None,
        "data_conclusao_origem": None,
        "data_recepcao_contestacao": None,
        "data_encerramento_atividade_intranet": None,
        "id_cliente": id_cliente,
        "id_workflow": id_workflow,
        "tipo_analise": staging.workflow_destino[:128],
        "matricula": staging.matricula_agente,
        "matricula_auditor": staging.matricula_auditor,
        "protocolo": staging.protocolo_origem,
        "cenario": "",
        "etapa": staging.etapa[:256],
        "status": staging.status_destino[:128],
        "irregularidades_apontadas": "",
        "cadastrado_anteriormente": "Parquet G Auditoria",
        "id_operations": None,
        "resultado_origem": staging.resultado_origem[:128],
        "resultado_destino": staging.resultado_destino[:128],
        "protocolo_destino": staging.protocolo_destino,
        "tipo_conclusao": staging.tipo_conclusao_destino[:64],
        "tipo_registro": "auditoria",
        "origem_tratado": "auditoria",
        "tipo_falha_original": "",
        "procedencia": "",
        "source_file": G_AUDITORIA_SOURCE_FILE,
        "admin_identity": f"g-auditoria:{staging.pk}:auditado",
        "imported_at": timezone.now(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "content_hash": staging.content_hash,
                "id_cliente": id_cliente,
                "id_workflow": id_workflow,
                "mapping_version": MAPPING_VERSION,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return fields, sorted(set(warnings)), fingerprint


def _apply_fields(instance, fields: dict[str, Any]) -> bool:
    changed = False
    for key, value in fields.items():
        if getattr(instance, key) != value:
            setattr(instance, key, value)
            changed = True
    return changed


AUDITADO_UPDATE_FIELDS = [
    "data",
    "data_analise",
    "data_analise_intranet",
    "data_analise_origem",
    "data_criacao_origem",
    "data_conclusao_origem",
    "data_recepcao_contestacao",
    "data_encerramento_atividade_intranet",
    "id_cliente",
    "id_workflow",
    "tipo_analise",
    "matricula",
    "matricula_auditor",
    "protocolo",
    "cenario",
    "etapa",
    "status",
    "irregularidades_apontadas",
    "cadastrado_anteriormente",
    "id_operations",
    "resultado_origem",
    "resultado_destino",
    "protocolo_destino",
    "tipo_conclusao",
    "tipo_registro",
    "origem_tratado",
    "tipo_falha_original",
    "procedencia",
    "localidade_documento",
    "source_file",
    "imported_at",
    "admin_identity",
    "admin_suppressed",
    "admin_revision",
]


@transaction.atomic
def _project_records(records: list[RotinaGAuditoriaRecord]) -> dict[str, int]:
    clientes = _dimension_index(DimCliente, "id_cliente")
    workflows = _dimension_index(DimWorkflow, "id_workflow")
    metrics = {
        "rows_projected_created": 0,
        "rows_projected_updated": 0,
        "rows_projected_unchanged": 0,
        "unresolved_client": 0,
        "unresolved_workflow": 0,
        "unresolved_auditor": 0,
    }

    valid = [
        record
        for record in records
        if record.is_active and not record.is_quarantined
    ]
    for chunk in _chunks(valid):
        staging_ids = [record.pk for record in chunk]
        existing = {
            projection.staging_id: projection
            for projection in QualidadeGAuditoriaProjection.objects.filter(
                staging_id__in=staging_ids
            ).select_related("auditado")
        }
        auditados_to_create: list[QualidadeAuditado] = []
        new_specs: list[
            tuple[
                RotinaGAuditoriaRecord,
                QualidadeGAuditoriaProjection | None,
                QualidadeAuditado,
                list[str],
                str,
            ]
        ] = []
        auditados_to_update: list[QualidadeAuditado] = []
        projections_to_update: list[QualidadeGAuditoriaProjection] = []

        for record in chunk:
            fields, warnings, fingerprint = _projection_payload(
                record, clientes, workflows
            )
            from apps.qualidade_operacional.services.record_admin import apply_state_to_fields

            fields = apply_state_to_fields(fields, "auditado")
            metrics["unresolved_client"] += any(
                warning.startswith("cliente_") for warning in warnings
            )
            metrics["unresolved_workflow"] += any(
                warning.startswith("workflow_") for warning in warnings
            )
            metrics["unresolved_auditor"] += not bool(record.matricula_auditor)
            projection = existing.get(record.pk)
            if (
                projection
                and projection.auditado_id
                and projection.is_active
                and projection.source_fingerprint == fingerprint
                and projection.mapping_version == MAPPING_VERSION
            ):
                metrics["rows_projected_unchanged"] += 1
                continue

            if projection and projection.auditado_id:
                auditado = projection.auditado
                if _apply_fields(auditado, fields):
                    auditados_to_update.append(auditado)
                projection.source_fingerprint = fingerprint
                projection.mapping_version = MAPPING_VERSION
                projection.is_active = True
                projection.warnings = warnings
                projection.projected_at = timezone.now()
                projections_to_update.append(projection)
                metrics["rows_projected_updated"] += 1
                continue

            auditado = QualidadeAuditado(**fields)
            auditados_to_create.append(auditado)
            new_specs.append((record, projection, auditado, warnings, fingerprint))

        if auditados_to_create:
            QualidadeAuditado.all_objects.bulk_create(
                auditados_to_create, batch_size=BATCH_SIZE
            )
        projections_to_create: list[QualidadeGAuditoriaProjection] = []
        for record, projection, auditado, warnings, fingerprint in new_specs:
            if projection is None:
                projections_to_create.append(
                    QualidadeGAuditoriaProjection(
                        staging=record,
                        auditado=auditado,
                        mapping_version=MAPPING_VERSION,
                        source_fingerprint=fingerprint,
                        is_active=True,
                        warnings=warnings,
                    )
                )
            else:
                projection.auditado = auditado
                projection.mapping_version = MAPPING_VERSION
                projection.source_fingerprint = fingerprint
                projection.is_active = True
                projection.warnings = warnings
                projection.projected_at = timezone.now()
                projections_to_update.append(projection)
            metrics["rows_projected_created"] += 1
        if projections_to_create:
            QualidadeGAuditoriaProjection.objects.bulk_create(
                projections_to_create, batch_size=BATCH_SIZE
            )
        if auditados_to_update:
            QualidadeAuditado.all_objects.bulk_update(
                auditados_to_update,
                AUDITADO_UPDATE_FIELDS,
                batch_size=BATCH_SIZE,
            )
        if projections_to_update:
            QualidadeGAuditoriaProjection.objects.bulk_update(
                projections_to_update,
                [
                    "auditado",
                    "mapping_version",
                    "source_fingerprint",
                    "is_active",
                    "warnings",
                    "projected_at",
                ],
                batch_size=BATCH_SIZE,
            )
    return metrics


def _candidate_index() -> dict[tuple[str, str], list[QualidadeGAuditoriaProjection]]:
    result: dict[tuple[str, str], list[QualidadeGAuditoriaProjection]] = defaultdict(list)
    queryset = (
        QualidadeGAuditoriaProjection.objects.filter(
            is_active=True,
            staging__is_active=True,
            staging__is_quarantined=False,
            auditado__isnull=False,
        )
        .select_related("staging", "auditado")
        .order_by("staging_id")
    )
    for projection in queryset.iterator(chunk_size=2000):
        key = (
            projection.staging.protocolo_normalizado,
            projection.staging.etapa_normalizada,
        )
        result[key].append(projection)
    return result


def _payload_value(payload: dict[str, Any], field: str):
    value = payload.get(field)
    if field in {"data", "data_analise"} and isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return value


def _match_projection(
    payload: dict[str, Any],
    candidates_by_key: dict[tuple[str, str], list[QualidadeGAuditoriaProjection]],
) -> tuple[str, QualidadeGAuditoriaProjection | None, int]:
    key = (
        normalize_protocol(payload.get("protocolo")),
        normalize_stage(payload.get("etapa")),
    )
    candidates = list(candidates_by_key.get(key) or [])
    original_count = len(candidates)
    if not candidates:
        return "unmatched", None, 0
    if len(candidates) == 1:
        return "matched", candidates[0], 1

    analysis_date = _payload_value(payload, "data_analise")
    if analysis_date:
        exact = [
            candidate
            for candidate in candidates
            if candidate.staging.data_analise == analysis_date
        ]
        if exact:
            candidates = exact
        else:
            dated = [
                candidate
                for candidate in candidates
                if candidate.staging.data_analise is not None
            ]
            if dated:
                min_delta = min(
                    abs((candidate.staging.data_analise - analysis_date).days)
                    for candidate in dated
                )
                candidates = [
                    candidate
                    for candidate in dated
                    if abs((candidate.staging.data_analise - analysis_date).days)
                    == min_delta
                ]

    matricula = normalize_matricula(payload.get("matricula"))
    if len(candidates) > 1 and matricula:
        exact = [
            candidate
            for candidate in candidates
            if candidate.staging.matricula_agente == matricula
        ]
        if exact:
            candidates = exact

    id_workflow = payload.get("id_workflow")
    if len(candidates) > 1 and id_workflow not in (None, ""):
        exact = [
            candidate
            for candidate in candidates
            if candidate.auditado and candidate.auditado.id_workflow == id_workflow
        ]
        if exact:
            candidates = exact

    if len(candidates) == 1:
        return "matched", candidates[0], original_count
    return "ambiguous", None, original_count


def _serialized_payload(row: QualidadeFalha) -> dict[str, Any]:
    return {
        "data": row.data.isoformat() if row.data else None,
        "data_analise": row.data_analise.isoformat() if row.data_analise else None,
        "data_analise_intranet": (
            row.data_analise_intranet.isoformat() if row.data_analise_intranet else None
        ),
        "data_analise_origem": (
            row.data_analise_origem.isoformat() if row.data_analise_origem else None
        ),
        "data_criacao_origem": (
            row.data_criacao_origem.isoformat() if row.data_criacao_origem else None
        ),
        "data_conclusao_origem": (
            row.data_conclusao_origem.isoformat() if row.data_conclusao_origem else None
        ),
        "data_recepcao_contestacao": (
            row.data_recepcao_contestacao.isoformat()
            if row.data_recepcao_contestacao
            else None
        ),
        "data_encerramento_atividade_intranet": (
            row.data_encerramento_atividade_intranet.isoformat()
            if row.data_encerramento_atividade_intranet
            else None
        ),
        "id_cliente": row.id_cliente,
        "id_workflow": row.id_workflow,
        "tipo_analise": row.tipo_analise,
        "matricula": row.matricula,
        "usuario_auditor": row.usuario_auditor,
        "etapa": row.etapa,
        "protocolo": row.protocolo,
    }


def _is_in_scope_projection(row: QualidadeIntranetProjection) -> bool:
    """Escopo canônico: Auditoria comum; não altera Contestação/Reinspeção/Compliance."""
    source = row.source
    if source.tipo_registro != "auditoria":
        return False
    from apps.qualidade_operacional.services.intranet_source import (
        _is_claro_confer,
        analysis_origin_payload,
    )

    return not _is_claro_confer(source, analysis_origin_payload(source))


def _restore_failure(row: QualidadeFalha, original: dict[str, Any]) -> bool:
    fields = {
        "data": _payload_value(original, "data"),
        "data_analise": _payload_value(original, "data_analise"),
        "data_analise_intranet": _payload_value(original, "data_analise_intranet"),
        "data_analise_origem": _payload_value(original, "data_analise_origem"),
        "data_criacao_origem": _payload_value(original, "data_criacao_origem"),
        "data_conclusao_origem": _payload_value(original, "data_conclusao_origem"),
        "data_recepcao_contestacao": _payload_value(
            original, "data_recepcao_contestacao"
        ),
        "data_encerramento_atividade_intranet": _payload_value(
            original, "data_encerramento_atividade_intranet"
        ),
        "id_cliente": original.get("id_cliente"),
        "id_workflow": original.get("id_workflow"),
        "tipo_analise": original.get("tipo_analise") or "",
        "matricula": original.get("matricula") or "",
        "usuario_auditor": original.get("usuario_auditor") or "",
        "etapa": original.get("etapa") or "",
    }
    return _apply_fields(row, fields)


FAILURE_RECONCILED_FIELDS = [
    "data",
    "data_analise",
    "data_analise_intranet",
    "data_analise_origem",
    "data_criacao_origem",
    "data_conclusao_origem",
    "data_recepcao_contestacao",
    "data_encerramento_atividade_intranet",
    "id_cliente",
    "id_workflow",
    "tipo_analise",
    "matricula",
    "usuario_auditor",
    "etapa",
    "admin_identity",
    "admin_suppressed",
    "admin_revision",
]


@transaction.atomic
def reconcile_failures(
    *,
    failure_ids: Iterable[int] | None = None,
    candidates_by_key: dict[
        tuple[str, str], list[QualidadeGAuditoriaProjection]
    ]
    | None = None,
    bump_cache: bool = False,
) -> dict[str, int]:
    candidates_by_key = candidates_by_key or _candidate_index()
    queryset = (
        QualidadeFalha.all_objects.filter(source_file=INTRANET_SOURCE_FILE)
        .filter(
            Q(intranet_projection__source__tipo_registro="auditoria")
            | Q(intranet_projection__isnull=True)
        )
        .select_related(
            "intranet_projection__source__analise_origem",
            "intranet_projection__source__atividade",
        )
        .order_by("id")
    )
    if failure_ids is not None:
        queryset = queryset.filter(pk__in=list(failure_ids))
    failures = [
        failure
        for failure in queryset
        if not hasattr(failure, "intranet_projection")
        or _is_in_scope_projection(failure.intranet_projection)
    ]
    existing = {
        row.falha_id: row
        for row in QualidadeGAuditoriaFailureReconciliation.objects.filter(
            falha_id__in=[failure.pk for failure in failures]
        )
    }
    reconciliations: list[QualidadeGAuditoriaFailureReconciliation] = []
    failures_to_update: list[QualidadeFalha] = []
    metrics = {
        "failure_matches": 0,
        "failure_unmatched": 0,
        "failure_ambiguous": 0,
    }

    for failure in failures:
        previous = existing.get(failure.pk)
        original = (
            dict(previous.original_payload)
            if previous and previous.original_payload
            else _serialized_payload(failure)
        )
        match_payload = {**original, "protocolo": original.get("protocolo") or failure.protocolo}
        status, projection, candidate_count = _match_projection(
            match_payload, candidates_by_key
        )
        differences: dict[str, Any] = {}
        changed = False
        if status == "matched" and projection and projection.auditado:
            auditado = projection.auditado
            target = {
                "data": auditado.data,
                "data_analise": auditado.data_analise,
                "data_analise_intranet": auditado.data_analise_intranet,
                "data_analise_origem": auditado.data_analise_origem,
                "data_criacao_origem": auditado.data_criacao_origem,
                "data_conclusao_origem": auditado.data_conclusao_origem,
                "data_recepcao_contestacao": auditado.data_recepcao_contestacao,
                "data_encerramento_atividade_intranet": (
                    auditado.data_encerramento_atividade_intranet
                ),
                "id_cliente": auditado.id_cliente,
                "id_workflow": auditado.id_workflow,
                "tipo_analise": auditado.tipo_analise,
                "matricula": auditado.matricula,
                "usuario_auditor": auditado.matricula_auditor,
                "etapa": auditado.etapa,
            }
            for field, target_value in target.items():
                original_value = _payload_value(original, field)
                if original_value != target_value:
                    differences[field] = {
                        "intranet": (
                            original_value.isoformat()
                            if isinstance(original_value, date)
                            else original_value
                        ),
                        "parquet": (
                            target_value.isoformat()
                            if isinstance(target_value, date)
                            else target_value
                        ),
                    }
            changed = _apply_fields(failure, target)
            observation = "falha_localizada_no_parquet"
            metrics["failure_matches"] += 1
        else:
            changed = _restore_failure(failure, original)
            projection = None
            if status == "ambiguous":
                observation = "falha_correspondencia_ambigua"
                metrics["failure_ambiguous"] += 1
            else:
                observation = "falha_nao_localizada_no_parquet"
                metrics["failure_unmatched"] += 1
        if changed:
            failures_to_update.append(failure)
        reconciliations.append(
            QualidadeGAuditoriaFailureReconciliation(
                falha=failure,
                projection=projection,
                status=status,
                observation=observation,
                mapping_version=MAPPING_VERSION,
                original_payload=original,
                differences=differences,
                candidate_count=candidate_count,
                reconciled_at=timezone.now(),
            )
        )

    if failures_to_update:
        from apps.qualidade_operacional.services.record_admin import apply_state_to_instance

        for failure in failures_to_update:
            apply_state_to_instance(failure, "falha")
        QualidadeFalha.all_objects.bulk_update(
            failures_to_update,
            FAILURE_RECONCILED_FIELDS,
            batch_size=BATCH_SIZE,
        )
    if reconciliations:
        QualidadeGAuditoriaFailureReconciliation.objects.bulk_create(
            reconciliations,
            batch_size=BATCH_SIZE,
            update_conflicts=True,
            unique_fields=["falha"],
            update_fields=[
                "projection",
                "status",
                "observation",
                "mapping_version",
                "original_payload",
                "differences",
                "candidate_count",
                "reconciled_at",
            ],
        )
    if bump_cache and (failures_to_update or reconciliations):
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )

        bump_quality_cache_version()
    return metrics


@transaction.atomic
def reconcile_intranet_auditados(
    *,
    intranet_projection_ids: Iterable[int] | None = None,
    candidates_by_key: dict[
        tuple[str, str], list[QualidadeGAuditoriaProjection]
    ]
    | None = None,
) -> dict[str, int]:
    candidates_by_key = candidates_by_key or _candidate_index()
    queryset = QualidadeIntranetProjection.objects.filter(
        auditado__isnull=False,
        source__tipo_registro="auditoria",
    ).select_related("auditado", "source__analise_origem", "source__atividade")
    if intranet_projection_ids is not None:
        queryset = queryset.filter(pk__in=list(intranet_projection_ids))
    rows = [row for row in queryset if _is_in_scope_projection(row)]
    metrics = {
        "intranet_audit_matches": 0,
        "intranet_audit_unmatched": 0,
        "intranet_audit_ambiguous": 0,
    }
    for row in rows:
        payload = {
            "protocolo": row.auditado.protocolo,
            "etapa": row.auditado.etapa,
            "data_analise": row.auditado.data_analise,
            "matricula": row.auditado.matricula,
            "id_workflow": row.auditado.id_workflow,
        }
        status, projection, _candidate_count = _match_projection(
            payload, candidates_by_key
        )
        row.g_auditoria_match_status = status
        row.g_auditoria_projection = projection
        if status == "matched":
            row.g_auditoria_match_observation = "auditado_fornecido_pelo_parquet"
            metrics["intranet_audit_matches"] += 1
        elif status == "ambiguous":
            row.g_auditoria_match_observation = "auditado_correspondencia_ambigua"
            metrics["intranet_audit_ambiguous"] += 1
        else:
            row.g_auditoria_match_observation = "auditado_nao_localizado_no_parquet"
            metrics["intranet_audit_unmatched"] += 1
    if rows:
        QualidadeIntranetProjection.objects.bulk_update(
            rows,
            [
                "g_auditoria_projection",
                "g_auditoria_match_status",
                "g_auditoria_match_observation",
            ],
            batch_size=BATCH_SIZE,
        )
    return metrics


@transaction.atomic
def project_g_auditoria_records(
    records: list[RotinaGAuditoriaRecord],
    *,
    report_date: date,
    bump_cache: bool = True,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"projection_enabled": True}
    metrics.update(_project_records(records))

    stale = QualidadeGAuditoriaProjection.objects.filter(
        staging__report_date=report_date,
        is_active=True,
    ).exclude(staging__is_active=True, staging__is_quarantined=False)
    removed = stale.count()
    stale.update(is_active=False, projected_at=timezone.now())
    metrics["rows_projected_removed"] = removed

    candidates = _candidate_index()
    metrics.update(
        reconcile_intranet_auditados(candidates_by_key=candidates)
    )
    metrics.update(reconcile_failures(candidates_by_key=candidates))
    if bump_cache:
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )

        bump_quality_cache_version()
    return metrics


@transaction.atomic
def rollback_g_auditoria_projection(*, bump_cache: bool = True) -> dict[str, int]:
    """Desativa a fonte G Auditoria e restaura os fatos originais da Intranet."""
    reconciliations = list(
        QualidadeGAuditoriaFailureReconciliation.objects.select_related("falha")
        .exclude(original_payload={})
        .order_by("falha_id")
    )
    failures_to_update: list[QualidadeFalha] = []
    for reconciliation in reconciliations:
        if _restore_failure(
            reconciliation.falha,
            dict(reconciliation.original_payload or {}),
        ):
            failures_to_update.append(reconciliation.falha)
    if failures_to_update:
        from apps.qualidade_operacional.services.record_admin import apply_state_to_instance

        for failure in failures_to_update:
            apply_state_to_instance(failure, "falha")
        QualidadeFalha.all_objects.bulk_update(
            failures_to_update,
            FAILURE_RECONCILED_FIELDS,
            batch_size=BATCH_SIZE,
        )

    projections_deactivated = QualidadeGAuditoriaProjection.objects.filter(
        is_active=True
    ).update(is_active=False, projected_at=timezone.now())
    intranet_matches_restored = QualidadeIntranetProjection.objects.exclude(
        g_auditoria_match_status="not_checked"
    ).update(
        g_auditoria_projection=None,
        g_auditoria_match_status="not_checked",
        g_auditoria_match_observation="",
    )
    if bump_cache and (
        projections_deactivated
        or intranet_matches_restored
        or failures_to_update
    ):
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )

        bump_quality_cache_version()
    return {
        "projections_deactivated": projections_deactivated,
        "intranet_matches_restored": intranet_matches_restored,
        "failures_restored": len(failures_to_update),
    }
