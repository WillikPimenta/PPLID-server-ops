# -*- coding: utf-8 -*-
from __future__ import annotations

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.monitor_eventos.services.parquet_reader import ParsedRow, read_monitor_parquet
from apps.rotina_bruto.models import RotinaMonitorTratadoRecord
from apps.rotina_bruto.services.source_path import SourceFileInfo


def _row_to_record(row: ParsedRow, report_date) -> RotinaMonitorTratadoRecord:
    return RotinaMonitorTratadoRecord(
        report_date=report_date,
        data=row.data,
        hora=row.hora,
        matricula_usuario=row.matricula_usuario,
        data_evento=row.data_evento,
        evento=row.evento,
        data_segundo_evento=row.data_segundo_evento,
        segundo_evento=row.segundo_evento,
    )


def sync_monitor_tratado_to_db(source: SourceFileInfo) -> int:
    parsed_rows = read_monitor_parquet(source.path)
    if not parsed_rows:
        return 0
    records = [_row_to_record(row, source.report_date) for row in parsed_rows]
    return chunked_partition_reload(
        model=RotinaMonitorTratadoRecord,
        delete_queryset=RotinaMonitorTratadoRecord.objects.filter(
            report_date=source.report_date
        ),
        records=records,
    )
