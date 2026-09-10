# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from django.db.models import Q

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.produtividade.models import SOURCE_BRFLOW, ProductivityRecord
from apps.produtividade.services.enrichment import (
    build_agent_maps,
    build_history_index,
    build_meta_etapa_cache_for_rows,
    enrich_row,
)
from apps.produtividade.services.excel_reader import read_productivity_excel
from apps.produtividade.services.source_path import SourceFileInfo, get_source_file


def sync_productivity_to_db(
    path: str | Path | None = None,
    force: bool = False,
) -> tuple[bool, SourceFileInfo, int]:
    del force
    source = get_source_file(str(path) if path else None)
    parsed_rows = read_productivity_excel(source.path)
    if not parsed_rows:
        return False, source, 0

    agents_by_matricula, names_by_matricula = build_agent_maps()
    history_index = build_history_index()
    meta_cache = build_meta_etapa_cache_for_rows(parsed_rows)

    records: list[ProductivityRecord] = []
    for row in parsed_rows:
        data = enrich_row(
            row,
            agents_by_matricula,
            names_by_matricula,
            history_index,
            meta_cache=meta_cache,
        )
        data["source"] = SOURCE_BRFLOW
        records.append(ProductivityRecord(**data))

    chunked_partition_reload(
        model=ProductivityRecord,
        delete_queryset=ProductivityRecord.objects.filter(
            Q(source=SOURCE_BRFLOW) | Q(source="")
        ),
        records=records,
    )

    from apps.produtividade.services.produtividade_cache import invalidate_produtividade_cache

    invalidate_produtividade_cache()
    return True, source, len(records)
