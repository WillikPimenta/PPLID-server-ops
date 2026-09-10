# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.rotina_bruto.models import (
    RotinaConferBuscaRecord,
    RotinaGedDetalhadoTratadoRecord,
    RotinaGedIrregularidadeTratadoRecord,
)
from apps.rotina_bruto.services.parquet_reader import (
    build_confer_busca_records,
    build_ged_detalhado_records,
    build_ged_irregularidade_records,
    iter_parquet_dataframe_batches,
    read_parquet,
)
from apps.rotina_bruto.services.source_path import SourceFileInfo


def _filter_period(
    qs: QuerySet,
    source: SourceFileInfo,
) -> QuerySet:
    qs = qs.filter(report_date=source.report_date)
    if source.periodo is None:
        return qs.filter(periodo__isnull=True)
    return qs.filter(periodo=source.periodo)


def sync_confer_busca_to_db(source: SourceFileInfo) -> int:
    df = read_parquet(source.path)
    if df.empty:
        return 0
    records = build_confer_busca_records(
        df,
        source.report_date,
        periodo=source.periodo,
    )
    return chunked_partition_reload(
        model=RotinaConferBuscaRecord,
        delete_queryset=_filter_period(RotinaConferBuscaRecord.objects.all(), source),
        records=records,
    )


def sync_ged_detalhado_to_db(source: SourceFileInfo) -> int:
    """Sync GED detalhado em batches (pyarrow) — evita carregar o parquet inteiro."""
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    cfg = get_bot_db_sync_runtime_config()
    delete_qs = _filter_period(RotinaGedDetalhadoTratadoRecord.objects.all(), source)

    total = 0
    deleted = False
    for batch_df in iter_parquet_dataframe_batches(source.path, cfg.chunk_size):
        if batch_df.empty:
            continue
        if not deleted:
            with transaction.atomic():
                delete_qs._raw_delete(using=delete_qs.db)
            deleted = True
        records = build_ged_detalhado_records(
            batch_df,
            source.report_date,
            periodo=source.periodo,
        )
        if not records:
            continue
        with transaction.atomic():
            RotinaGedDetalhadoTratadoRecord.objects.bulk_create(
                records, batch_size=cfg.batch_size
            )
        total += len(records)
    return total


def sync_ged_irregularidade_to_db(source: SourceFileInfo) -> int:
    df = read_parquet(source.path)
    if df.empty:
        return 0
    records = build_ged_irregularidade_records(
        df,
        source.report_date,
        periodo=source.periodo,
    )
    return chunked_partition_reload(
        model=RotinaGedIrregularidadeTratadoRecord,
        delete_queryset=_filter_period(
            RotinaGedIrregularidadeTratadoRecord.objects.all(), source
        ),
        records=records,
    )
