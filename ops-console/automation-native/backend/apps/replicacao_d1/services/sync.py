# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from django.db import transaction

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Reconciliacao,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.normalization import STATUS_PENDENTE, STATUS_REPLICADO, normalize_protocolo
from apps.replicacao_d1.services.excel_reader import read_replicacao_d1_excel
from apps.replicacao_d1.services.read_result import ReadResult, should_replace_partition
from apps.replicacao_d1.feature_flags import reconciliation_enabled
from apps.replicacao_d1.services.run_lifecycle import close_run, mark_run_started
from apps.replicacao_d1.services.source_path import SourceFileInfo, get_source_file
from apps.replicacao_d1.services.workflow_operational import (
    build_workflow_operational_defaults,
    protocolos_por_workflow,
)


def _matched_protocol_state(run_id: str) -> dict[str, ReplicacaoD1Protocolo]:
    qs = (
        ReplicacaoD1Protocolo.objects.filter(
            run_id=run_id,
            reconciliacoes__status=ReplicacaoD1Reconciliacao.STATUS_MATCHED,
        )
        .distinct()
        .only(
            "protocolo",
            "status_operacional",
            "replicado_em",
            "erro_resumido",
        )
    )
    return {p.protocolo: p for p in qs}


def sync_replicacao_d1_to_db(
    path: str | Path | None = None,
    *,
    run_id: str | None = None,
    force: bool = False,
    ingestion_id: int | None = None,
    attempt_number: int | None = None,
) -> tuple[bool, SourceFileInfo, int, str, int]:
    """Carrega Excel D-1 e regrava a partição do run_id.

    Retorna (success, source, row_count, run_id, rows_rejected).
    """
    del force
    source = get_source_file(str(path) if path else None, run_id=run_id)
    parsed = read_replicacao_d1_excel(source.path, run_id=source.run_id)
    stats = parsed.read_stats or ReadResult()
    rows_rejected = stats.rejected_rows + stats.duplicate_rows

    if not stats.is_structurally_valid:
        return False, source, 0, parsed.run_id, rows_rejected

    existing_prot = ReplicacaoD1Protocolo.objects.filter(run_id=parsed.run_id).count()
    existing_wf = ReplicacaoD1WorkflowDia.objects.filter(run_id=parsed.run_id).count()

    if not parsed.workflows and stats.valid_rows == 0 and existing_prot == 0 and existing_wf == 0:
        return False, source, 0, parsed.run_id, rows_rejected

    preserved_protocols = _matched_protocol_state(parsed.run_id)
    prot_by_wf = protocolos_por_workflow(parsed.protocolos)

    with transaction.atomic():
        run_obj, _ = ReplicacaoD1Run.objects.update_or_create(
            run_id=parsed.run_id,
            defaults={
                "data_referencia_d1": parsed.data_referencia_d1,
                "data_execucao": parsed.data_execucao,
                "parquet_referencia": parsed.parquet_referencia or "",
                "auditores_ativos_brflow": parsed.auditores_ativos_brflow,
                "auditores_ativos_case": parsed.auditores_ativos_case,
            },
        )
        if ingestion_id and run_obj.ingestion_id != ingestion_id:
            run_obj.ingestion_id = ingestion_id
            run_obj.save(update_fields=["ingestion"])

    mark_run_started(parsed.run_id, attempt_number=attempt_number)

    row_count = 0

    if should_replace_partition(
        valid_rows=len(parsed.workflows),
        existing_count=existing_wf,
        structurally_valid=True,
    ) and parsed.workflows:
        wf_records = []
        for w in parsed.workflows:
            op = build_workflow_operational_defaults(
                w,
                protocolos_planejados=prot_by_wf.get(w.workflow_config, 0),
                data_execucao=parsed.data_execucao,
                ingestion_id=ingestion_id,
            )
            wf_records.append(
                ReplicacaoD1WorkflowDia(
                    run_id=parsed.run_id,
                    data_referencia_d1=parsed.data_referencia_d1,
                    workflow_config=w.workflow_config,
                    workflow_d1=w.workflow_d1,
                    workflow_brflow=w.workflow_brflow,
                    canal_destino=w.canal_destino,
                    cliente=w.cliente,
                    segmento=w.segmento,
                    categoria=w.categoria,
                    fila=w.fila,
                    amostra_diaria=w.amostra_diaria,
                    amostra_solicitada=w.amostra_solicitada,
                    amostra_efetiva=w.amostra_efetiva,
                    protocolos_salvos=w.protocolos_salvos,
                    pct_atingido=w.pct_atingido,
                    disponivel_d1=w.disponivel_d1,
                    status_amostra=w.status_amostra,
                    status_brflow=w.status_brflow or "PENDENTE",
                    data_hora_upload_brflow=w.data_hora_upload_brflow,
                    faixa_horaria=w.faixa_horaria,
                    **op,
                )
            )
        chunked_partition_reload(
            model=ReplicacaoD1WorkflowDia,
            delete_queryset=ReplicacaoD1WorkflowDia.objects.filter(run_id=parsed.run_id),
            records=wf_records,
        )
        row_count += len(wf_records)

    prot_records: list[ReplicacaoD1Protocolo] = []
    if should_replace_partition(
        valid_rows=stats.valid_rows,
        existing_count=existing_prot,
        structurally_valid=stats.is_structurally_valid,
    ):
        for p in parsed.protocolos:
            preserved = preserved_protocols.get(p.protocolo)
            status_operacional = preserved.status_operacional if preserved else STATUS_PENDENTE
            replicado_em = preserved.replicado_em if preserved else None
            erro_resumido = preserved.erro_resumido if preserved else ""
            if preserved and preserved.status_operacional == STATUS_REPLICADO:
                status_operacional = preserved.status_operacional
            prot_records.append(
                ReplicacaoD1Protocolo(
                    run_id=parsed.run_id,
                    data_referencia_d1=parsed.data_referencia_d1,
                    protocolo=p.protocolo,
                    protocolo_normalizado=normalize_protocolo(p.protocolo),
                    workflow_config=p.workflow_config,
                    workflow_d1=p.workflow_d1,
                    data_analise=p.data_analise,
                    hora=p.hora if p.hora is None or 0 <= p.hora <= 23 else None,
                    canal_destino=p.canal_destino,
                    status_brflow=p.status_brflow or "PENDENTE",
                    status_operacional=status_operacional,
                    replicado_em=replicado_em,
                    erro_resumido=erro_resumido,
                    ingestion_id=ingestion_id,
                )
            )
        if prot_records or existing_prot == 0:
            prot_ids = list(
                ReplicacaoD1Protocolo.objects.filter(run_id=parsed.run_id).values_list("pk", flat=True)
            )
            preserved_ids = list(
                ReplicacaoD1Protocolo.objects.filter(
                    run_id=parsed.run_id,
                    protocolo__in=preserved_protocols.keys(),
                ).values_list("pk", flat=True)
            )
            delete_reconc_ids = [pid for pid in prot_ids if pid not in preserved_ids]
            if delete_reconc_ids:
                ReplicacaoD1Reconciliacao.objects.filter(protocolo_id__in=delete_reconc_ids).delete()
            chunked_partition_reload(
                model=ReplicacaoD1Protocolo,
                delete_queryset=ReplicacaoD1Protocolo.objects.filter(run_id=parsed.run_id),
                records=prot_records,
            )
            if reconciliation_enabled():
                from apps.replicacao_d1.services.reconciliation import reconcile_protocolos_for_run

                reconcile_protocolos_for_run(parsed.run_id)
            row_count += len(prot_records)

    if row_count == 0 and existing_prot == 0 and existing_wf == 0:
        return False, source, 0, parsed.run_id, rows_rejected

    close_run(
        parsed.run_id,
        ingestion_id=ingestion_id,
        data_execucao=parsed.data_execucao,
    )

    return True, source, row_count, parsed.run_id, rows_rejected
