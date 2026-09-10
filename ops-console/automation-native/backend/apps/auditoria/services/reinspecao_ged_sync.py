"""Sync bot GED irregularidade bruto → qualidade_pendente_reinspecao."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import QualidadePendenteReinspecao
from apps.auditoria.models import ReinspecaoAuditorPresence
from apps.auditoria.services.import_tabular import decode_csv_rows
from apps.auditoria.services.reinspecao_ged_divergencias import reconcile_ged_occurrence
from apps.auditoria.services.reinspecao_import import (
    TIPO_FALHA_REINSPECAO,
    ParsedReinspecaoRow,
    _parse_reinspecao_rows,
    apply_mapping_to_preview,
    normalize_header,
)
from apps.auditoria.services.reinspecao_import_dedupe import (
    MOTIVO_JA_ANALISADO,
    MOTIVO_JA_NA_FILA,
    build_reinspecao_occurrence_key,
    dedupe_key_for_reinspecao_row,
    dedupe_key_hash,
    load_reinspecao_portal_occurrences,
    partition_rows_for_persist,
)
from apps.auditoria.services.reinspecao_ocorrencias import (
    link_reinspecao_occurrence,
    reserve_reinspecao_occurrence,
)

# Aliases do export GED → cabeçalho canônico esperado pelo parser de reinspeção.
GED_IRREGULARIDADE_HEADER_ALIASES = {
    "DATA CONTESTACAO": "DATA DA CONTESTACAO",
    "DATA DE RESPOSTA": "DATA DA RESPOSTA",
    "MATRICULA DO INSPETOR": "MATRICULA DO INSPETOR",
    "DESCRICAO DAS IRREGULARIDADES": "DESCRICAO DAS IRREGULARIDADES",
    "PROTOCOLO": "PROTOCOLO",
}


def _canonical_ged_header(value: Any) -> str:
    key = normalize_header(value)
    return GED_IRREGULARIDADE_HEADER_ALIASES.get(key, key)


def normalize_ged_irregularidade_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Normaliza cabeçalhos do CSV bruto GED para o parser de reinspeção."""
    if not rows:
        return rows
    normalized: list[tuple[Any, ...]] = []
    for row_index, row in enumerate(rows):
        if row_index == 0:
            normalized.append(tuple(_canonical_ged_header(cell) for cell in row))
        else:
            normalized.append(row)
    return normalized


def _rows_to_pendentes(
    rows: list[ParsedReinspecaoRow],
    *,
    source_file: str,
    source_hash: str,
    contexto: str,
) -> list[QualidadePendenteReinspecao]:
    pendentes: list[QualidadePendenteReinspecao] = []
    mapping_applied_at = timezone.now()
    for row in rows:
        protocolo = (row.protocolo or "").strip()
        if not protocolo:
            continue
        dedupe_key = dedupe_key_for_reinspecao_row(row, contexto=contexto)
        pendentes.append(
            QualidadePendenteReinspecao(
                protocolo=protocolo,
                usuario=row.matricula_inspetor,
                descricao_irregularidades=row.descricao_irregularidades,
                data_contestacao=row.data_contestacao,
                data_analise=row.data_analise,
                codigo_irregularidade=row.codigo_irregularidade,
                motivo_falha=row.cenario_mapeado,
                etapa_falha=row.etapa_mapeada,
                mapping_scenario_status=row.mapping_scenario_status,
                mapping_stage_status=row.mapping_stage_status,
                mapping_version=row.mapping_version,
                mapping_source_hash=row.mapping_source_hash,
                mapping_applied_at=mapping_applied_at,
                tipo_falha=TIPO_FALHA_REINSPECAO,
                contexto=contexto,
                brflow_parsed={
                    "source_file": source_file,
                    "source_hash": source_hash,
                    "excel_row": row.excel_row,
                    "source": "bot_production",
                    "fila_contexto": contexto,
                    "import_dedupe_key": dedupe_key_hash(dedupe_key),
                    "reinspecao_mapping": {
                        "classification": row.classificacao_irregularidade,
                        "code": row.codigo_irregularidade,
                        "scenario_status": row.mapping_scenario_status,
                        "stage_status": row.mapping_stage_status,
                        "version": row.mapping_version,
                        "source_hash": row.mapping_source_hash,
                        "candidates": row.mapping_candidates,
                    },
                },
                cliente="",
                modulo="",
                status="",
                observacao="",
                auditor="",
                created_by=None,
            )
        )
    return pendentes


def _portal_skips_from_preview(preview) -> int:
    return sum(
        1
        for item in (preview.protocolos_ignorados or [])
        if item.get("motivo") in (MOTIVO_JA_NA_FILA, MOTIVO_JA_ANALISADO)
    )


@transaction.atomic
def sync_reinspecao_ged_to_db(*, path: str | Path) -> dict[str, Any]:
    """Lê CSV bruto GED, aplica filtros de reinspeção e insere pendentes novos."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo GED irregularidade não encontrado: {source}")

    file_bytes = source.read_bytes()
    source_hash = hashlib.sha256(file_bytes).hexdigest()
    rows = decode_csv_rows(file_bytes)
    rows = normalize_ged_irregularidade_rows(rows)
    preview = _parse_reinspecao_rows(
        rows,
        sheet_name=source.name,
        allow_no_eligible=True,
    )

    if preview.errors:
        raise ValueError("; ".join(preview.errors))

    preview = apply_mapping_to_preview(preview)
    if preview.errors:
        raise ValueError("; ".join(preview.errors))

    parsed_rows = preview.rows
    skipped_filtered = preview.linhas_filtradas
    contexto = ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO
    portal_occurrences = load_reinspecao_portal_occurrences(contexto=contexto)
    divergence_counts = {
        "created": 0,
        "seen": 0,
        "reopened": 0,
        "resolved": 0,
        "unchanged": 0,
    }
    reconciled_keys = set()
    for observation in preview.observations:
        row = observation.row
        key = build_reinspecao_occurrence_key(
            contexto=contexto,
            protocolo=row.protocolo,
            descricao_irregularidades=row.descricao_irregularidades,
            data_contestacao=row.data_contestacao,
        )
        if key in reconciled_keys:
            continue
        reconciled_keys.add(key)
        portal_match = portal_occurrences.get(key)

        if observation.ged_elegivel:
            if portal_match is None or portal_match.tratado is None:
                continue
            reconcile_result = reconcile_ged_occurrence(
                contexto=contexto,
                protocolo=row.protocolo,
                descricao_irregularidades=row.descricao_irregularidades,
                data_contestacao=row.data_contestacao,
                ged_elegivel=True,
                tratado=portal_match.tratado,
                source_file=source.name,
                source_hash=source_hash,
            )
        elif observation.motivo == "respondida":
            reconcile_result = reconcile_ged_occurrence(
                contexto=contexto,
                protocolo=row.protocolo,
                descricao_irregularidades=row.descricao_irregularidades,
                data_contestacao=row.data_contestacao,
                ged_elegivel=False,
                source_file=source.name,
                source_hash=source_hash,
            )
        else:
            continue
        divergence_counts[reconcile_result.action] += 1

    import_rows, skipped_existing, skipped_finalized_ged = partition_rows_for_persist(
        parsed_rows,
        contexto=contexto,
        skip_finalized_ged=set(),
    )
    skipped_existing += _portal_skips_from_preview(preview)
    pendentes = _rows_to_pendentes(
        import_rows,
        source_file=source.name,
        source_hash=source_hash,
        contexto=contexto,
    )

    created_count = 0
    skipped_ledger = 0
    distribution: dict[str, Any] = {}
    if pendentes:
        created: list[QualidadePendenteReinspecao] = []
        for pendente in pendentes:
            reservation = reserve_reinspecao_occurrence(
                contexto=contexto,
                protocolo=pendente.protocolo,
                descricao_irregularidades=pendente.descricao_irregularidades,
                data_contestacao=pendente.data_contestacao,
                source="bot_production",
                source_file=source.name,
                source_hash=source_hash,
            )
            if not reservation.acquired:
                skipped_ledger += 1
                continue
            pendente.save(force_insert=True)
            link_reinspecao_occurrence(
                reservation.ocorrencia,
                pendente=pendente,
            )
            created.append(pendente)
        created_count = len(created)
        if created:
            from apps.auditoria.services.reinspecao_fila import distribuir_protocolos

            distribution = distribuir_protocolos(actor=None)

    return {
        "created": created_count,
        "skipped_existing": skipped_existing,
        "skipped_finalized_ged": skipped_finalized_ged,
        "skipped_ledger": skipped_ledger,
        "skipped_filtered": skipped_filtered,
        "total_parsed": len(parsed_rows),
        "source_file": source.name,
        "source_hash": source_hash,
        "divergences": divergence_counts,
        "distribution": distribution,
    }
