# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.parquet_reader import ParsedRow, read_monitor_parquet
from apps.monitor_eventos.services.source_path import SourceFileInfo, get_source_file


def _row_to_record(row: ParsedRow) -> MonitorEventoRecord:
    return MonitorEventoRecord(
        data=row.data,
        hora=row.hora,
        matricula_usuario=row.matricula_usuario,
        data_evento=row.data_evento,
        evento=row.evento,
        data_segundo_evento=row.data_segundo_evento,
        segundo_evento=row.segundo_evento,
    )


def sync_monitor_eventos_to_db(
    path: str | Path | None = None,
    force: bool = False,
) -> tuple[bool, SourceFileInfo, int]:
    del force
    source = get_source_file(str(path) if path else None)
    parsed_rows = read_monitor_parquet(source.path)
    if not parsed_rows:
        return False, source, 0

    records = [_row_to_record(row) for row in parsed_rows]

    chunked_partition_reload(
        model=MonitorEventoRecord,
        delete_queryset=MonitorEventoRecord.objects.all(),
        records=records,
    )

    from apps.monitor_eventos.services.tabela_monitor_cache import invalidate_tabela_monitor_cache
    from apps.monitor_eventos.services.tabela_monitor_serve import warm_snapshots_after_sync
    from apps.produtividade.services.produtividade_cache import invalidate_produtividade_cache

    invalidate_tabela_monitor_cache()
    # Aproveitamento / tempo logado na produtividade lê o monitor — sem bump a API
    # continua a servir pace "Sem dados" do LocMem até o TTL.
    invalidate_produtividade_cache()
    try:
        warm_snapshots_after_sync()
    except Exception:
        # Sync já gravou os eventos; falha no warm não reverte a carga.
        pass

    return True, source, len(records)
