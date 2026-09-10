# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.replicacao_d1.models import ReplicacaoD1Replicado
from apps.replicacao_d1.normalization import normalize_protocolo
from apps.replicacao_d1.services.read_result import should_replace_partition
from apps.replicacao_d1.feature_flags import reconciliation_enabled
from apps.replicacao_d1.services.reconciliation import reconcile_protocolos_for_dates
from apps.replicacao_d1.services.replicados_reader import read_replicados_csv
from apps.replicacao_d1.services.replicados_source_path import (
    ReplicadosSourceFileInfo,
    get_replicados_source_file,
)


def sync_replicados_to_db(
    path: str | Path | None = None,
    *,
    force: bool = False,
    ingestion_id: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[bool, ReplicadosSourceFileInfo, int, int]:
    """Carrega CSV de replicados e regrava a partição do report_date.

    Retorna (success, source, row_count, rows_rejected).
    """
    del force
    source = get_replicados_source_file(str(path) if path else None)
    if progress:
        progress("Lendo e validando o CSV de replicados...")
    report_date, result = read_replicados_csv(source.path, report_date=source.report_date)
    rows_rejected = result.rejected_rows + result.duplicate_rows

    if not result.is_structurally_valid:
        return False, source, 0, rows_rejected

    existing_count = ReplicacaoD1Replicado.objects.filter(report_date=report_date).count()
    if not should_replace_partition(
        valid_rows=result.valid_rows,
        existing_count=existing_count,
        structurally_valid=result.is_structurally_valid,
    ):
        return False, source, 0, rows_rejected

    if result.valid_rows == 0 and existing_count == 0:
        return False, source, 0, rows_rejected

    records = [
        ReplicacaoD1Replicado(
            report_date=report_date,
            cliente_destino=r.cliente_destino,
            data_cadastro_destino=r.data_cadastro_destino,
            protocolo_destino=r.protocolo_destino,
            workflow_destino=r.workflow_destino,
            protocolo_origem=r.protocolo_origem,
            protocolo_origem_normalizado=normalize_protocolo(r.protocolo_origem),
            cliente_origem=r.cliente_origem,
            workflow_origem=r.workflow_origem,
            nivel_hierarquico_origem=r.nivel_hierarquico_origem,
            data_cadastro_origem=r.data_cadastro_origem,
            tipo_conclusao_analise_origem=r.tipo_conclusao_analise_origem,
            ingestion_id=ingestion_id,
        )
        for r in result.records
    ]
    if progress:
        progress(f"Carregando {len(records)} replicados de {report_date:%d/%m/%Y}...")
    chunked_partition_reload(
        model=ReplicacaoD1Replicado,
        delete_queryset=ReplicacaoD1Replicado.objects.filter(report_date=report_date),
        records=records,
    )
    if reconciliation_enabled():
        if progress:
            progress(f"Reconciliando protocolos de {report_date:%d/%m/%Y}...")
        reconciled = reconcile_protocolos_for_dates({report_date}, progress=progress)
        if progress:
            progress(f"Reconciliação concluída: {reconciled} protocolo(s) atualizado(s).")
    return True, source, len(records), rows_rejected
