# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.produtividade.models import SOURCE_CASE, CASE_ETAPA, ProductivityRecord
from apps.produtividade.services.enrichment import (
    build_agent_maps,
    build_history_index,
    build_meta_etapa_cache_for_rows,
    enrich_row,
)
from apps.produtividade.services.produtividade_cache import invalidate_produtividade_cache
from apps.produtividade_case.constants import REPORT_TEMPO_LOGADO, REPORTS_TO_PRODUCTIVITY
from apps.produtividade_case.services.consolidado_agg import sync_consolidado_daily_aggs
from apps.produtividade_case.services.hxh_builder import build_case_parsed_rows, resolve_case_inputs

BATCH_SIZE = 2000


def sync_case_to_productivity_record(path: Path, report_type: str) -> tuple[int, str]:
    """Grava Case no ProductivityRecord (source=case), sem apagar BRFlow.

    Quando há Excel consolidado, também popula CaseConsolidadoDailyAgg.
    """
    report_type = (report_type or "").strip().lower()
    if report_type == REPORT_TEMPO_LOGADO:
        raise ValueError("tempo_logado não sincroniza para ProductivityRecord")
    if report_type not in REPORTS_TO_PRODUCTIVITY:
        raise ValueError(f"report_type inválido: {report_type}")

    periodo_mes, cons_path, hora_path = resolve_case_inputs(path)

    # Uma leitura Excel do consolidado por job (HxH + daily aggs)
    cons_rows = None
    if cons_path and Path(cons_path).is_file():
        from apps.produtividade_case.services.excel_reader import read_consolidado_rows

        cons_rows = read_consolidado_rows(Path(cons_path), periodo_mes)

    parsed = build_case_parsed_rows(
        consolidado_path=cons_path,
        hora_path=hora_path,
        consolidado_rows=cons_rows,
    )
    if not parsed and not cons_path and not hora_path:
        raise FileNotFoundError(f"Sem Excel Case para sync em {path}")

    agents_by_matricula, names_by_matricula = build_agent_maps()
    history_index = build_history_index()
    meta_cache = build_meta_etapa_cache_for_rows(parsed)
    records: list[ProductivityRecord] = []
    for row in parsed:
        data = enrich_row(
            row,
            agents_by_matricula,
            names_by_matricula,
            history_index,
            meta_cache=meta_cache,
        )
        data["source"] = SOURCE_CASE
        data["etapa"] = CASE_ETAPA
        records.append(ProductivityRecord(**data))

    # Partição: só linhas Case da etapa Case (mês via recorded_at quando possível)
    delete_qs = ProductivityRecord.objects.filter(source=SOURCE_CASE, etapa=CASE_ETAPA)
    if periodo_mes:
        # mai-2026 → filter year/month
        try:
            mes_abrev, ano_s = periodo_mes.split("-", 1)
            mes_map = {
                "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
                "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
            }
            mes_n = mes_map.get(mes_abrev.lower())
            ano_n = int(ano_s)
            if mes_n:
                delete_qs = delete_qs.filter(
                    recorded_at__year=ano_n,
                    recorded_at__month=mes_n,
                )
        except (ValueError, AttributeError):
            pass

    n = chunked_partition_reload(
        model=ProductivityRecord,
        delete_queryset=delete_qs,
        records=records,
        chunk_size=BATCH_SIZE,
        batch_size=BATCH_SIZE,
    )
    if cons_path and Path(cons_path).is_file():
        sync_consolidado_daily_aggs(
            cons_path, periodo_mes=periodo_mes, rows=cons_rows
        )
    invalidate_produtividade_cache()
    return n, periodo_mes
