# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from django.db import connection, transaction

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.rotina_bruto.models import (
    RotinaBrutoSyncLog,
    RotinaDetalhadoBrutoAlerta,
    RotinaDetalhadoBrutoRecord,
    RotinaProdBrutoRecord,
)
from apps.rotina_bruto.services.monitor_tratado_sync import sync_monitor_tratado_to_db
from apps.rotina_bruto.services.g_auditoria_sync import sync_g_auditoria_to_db
from apps.rotina_bruto.services.tratado_sync import (
    sync_confer_busca_to_db,
    sync_ged_detalhado_to_db,
    sync_ged_irregularidade_to_db,
)
from apps.rotina_bruto.services.parquet_reader import (
    build_prod_records,
    iter_detalhado_chunks,
    read_parquet,
)
from apps.rotina_bruto.services.source_path import SourceFileInfo


def _delete_detalhado_partition(report_date: date) -> None:
    """Apaga alertas + records do dia via SQL (mais rápido que ORM cascade)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM rotina_detalhado_bruto_alerta AS a
            USING rotina_detalhado_bruto_record AS r
            WHERE a.record_id = r.id AND r.report_date = %s
            """,
            [report_date],
        )
        cursor.execute(
            "DELETE FROM rotina_detalhado_bruto_record WHERE report_date = %s",
            [report_date],
        )


def sync_detalhado_bruto_to_db(source: SourceFileInfo) -> int:
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    df = read_parquet(source.path)
    if df.empty:
        return 0

    cfg = get_bot_db_sync_runtime_config()
    with transaction.atomic():
        _delete_detalhado_partition(source.report_date)

    total = 0
    for main_chunk, alerta_texts in iter_detalhado_chunks(
        df, source.report_date, cfg.chunk_size
    ):
        with transaction.atomic():
            created = RotinaDetalhadoBrutoRecord.objects.bulk_create(
                main_chunk, batch_size=cfg.batch_size
            )
            alerta_rows = [
                RotinaDetalhadoBrutoAlerta(record_id=record.pk, alertas=text)
                for record, text in zip(created, alerta_texts)
                if text
            ]
            if alerta_rows:
                RotinaDetalhadoBrutoAlerta.objects.bulk_create(
                    alerta_rows, batch_size=cfg.batch_size
                )
        total += len(created)
    return total


def sync_prod_bruto_to_db(source: SourceFileInfo) -> int:
    df = read_parquet(source.path)
    if df.empty:
        return 0
    records = build_prod_records(df, source.report_date)
    return chunked_partition_reload(
        model=RotinaProdBrutoRecord,
        delete_queryset=RotinaProdBrutoRecord.objects.filter(
            report_date=source.report_date
        ),
        records=records,
    )


SYNC_HANDLERS = {
    RotinaBrutoSyncLog.REPORT_DETALHADO: sync_detalhado_bruto_to_db,
    RotinaBrutoSyncLog.REPORT_PROD: sync_prod_bruto_to_db,
    RotinaBrutoSyncLog.REPORT_MONITOR: sync_monitor_tratado_to_db,
    RotinaBrutoSyncLog.REPORT_CONFER_BUSCA: sync_confer_busca_to_db,
    RotinaBrutoSyncLog.REPORT_GED_DETALHADO: sync_ged_detalhado_to_db,
    RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE: sync_ged_irregularidade_to_db,
    RotinaBrutoSyncLog.REPORT_G_AUDITORIA: sync_g_auditoria_to_db,
}
