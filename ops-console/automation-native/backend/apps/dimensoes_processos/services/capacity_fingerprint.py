"""Fingerprint deterministico das fontes que alimentam o Capacity."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from django.db import connection

from apps.dimensoes_processos.models import (
    CapacityHourlyProfileSnapshot,
    DerivacaoEtapaImportRun,
    MetaEtapa,
    ProjecaoSla,
)
from apps.monitoramento_sla.models import SlaUtilSyncRun


def _feed_rows(digest, label: str, rows) -> None:
    digest.update(label.encode("utf-8"))
    for row in rows.iterator(chunk_size=2000):
        digest.update(
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )


def current_capacity_source_fingerprint(
    date_from: date,
    date_to: date,
    *,
    scenario_id: str,
) -> str:
    """Inclui os valores efetivamente mutaveis, inclusive edicoes in-place."""

    source_scenario = "planejamento" if scenario_id == "manual" else scenario_id
    if connection.vendor == "postgresql":
        return _postgres_capacity_source_fingerprint(
            date_from,
            date_to,
            source_scenario=source_scenario,
        )
    digest = hashlib.sha256()
    # Conservador e independente da faixa: qualquer alteracao de fonte invalida
    # todas as datas, permitindo reutilizar a mesma geracao em recortes menores.
    digest.update(source_scenario.encode("ascii"))

    derivation_runs = (
        DerivacaoEtapaImportRun.objects.filter(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            reviewed_scan__isnull=False,
        )
        .order_by("id")
        .values(
            "id",
            "period_from",
            "period_to",
            "finished_at",
            "source_fingerprint",
            "reviewed_scan_id",
            "reviewed_scan__source_fingerprint",
        )
    )
    _feed_rows(digest, "derivation_runs", derivation_runs)

    metas = (
        MetaEtapa.objects.order_by("id")
        .values("id", "data_inicio", "data_fim", "etapa_id", "meta_dia", "servico_id")
    )
    _feed_rows(digest, "metas", metas)

    if source_scenario == "planejamento":
        projections = (
            ProjecaoSla.objects.order_by("id")
            .values(
                "id",
                "cliente_id",
                "workflow_id",
                "nivel_hierarquico_id",
                "data_inicio",
                "data_fim",
                "dias_semana",
                "volume",
            )
        )
        _feed_rows(digest, "projections", projections)
    else:
        sync_runs = (
            SlaUtilSyncRun.objects.filter(
                status=SlaUtilSyncRun.STATUS_OK,
                finished_at__isnull=False,
            )
            .order_by("-finished_at", "-id")
            .values(
                "id",
                "finished_at",
                "cutover_at",
                "rows_detalhe",
                "rows_consolidado",
            )[:1]
        )
        _feed_rows(digest, "sla_sync", sync_runs)

    profiles = (
        CapacityHourlyProfileSnapshot.objects.order_by(
            "quarter_from",
            "quarter_to",
            "weekday",
            "scope",
            "id_cliente",
            "id_workflow",
        )
        .values(
            "quarter_from",
            "quarter_to",
            "weekday",
            "scope",
            "id_cliente",
            "id_workflow",
            "hourly_volumes",
            "trusted_volume",
        )
    )
    _feed_rows(digest, "hourly_profiles", profiles)
    return digest.hexdigest()


def _postgres_capacity_source_fingerprint(
    date_from: date,
    date_to: date,
    *,
    source_scenario: str,
) -> str:
    """Calcula os quatro checksums no banco em uma unica ida."""

    volume_source_sql = """
        SELECT md5(coalesce(string_agg(
            concat_ws('~', id, id_cliente, id_workflow, id_nivel_hierarquico,
                      data_inicio, data_fim, dias_semana, volume),
            '|' ORDER BY id), ''))
        FROM projecao_sla
    """
    volume_params: list = []
    if source_scenario != "planejamento":
        volume_source_sql = """
            SELECT md5(coalesce(string_agg(
                concat_ws('~', id, finished_at, cutover_at,
                          rows_detalhe, rows_consolidado),
                '|' ORDER BY finished_at DESC, id DESC), ''))
            FROM (
                SELECT id, finished_at, cutover_at, rows_detalhe, rows_consolidado
                FROM sla_util_sync_run
                WHERE status = 'ok' AND finished_at IS NOT NULL
                ORDER BY finished_at DESC, id DESC
                LIMIT 1
            ) latest_sync
        """
        volume_params = []

    sql = f"""
        SELECT md5(concat_ws('|', %s,
            (
                SELECT md5(coalesce(string_agg(
                    concat_ws('~', import_run.id, import_run.period_from,
                              import_run.period_to, import_run.finished_at,
                              import_run.source_fingerprint,
                              import_run.reviewed_scan_id,
                              reviewed_scan.source_fingerprint),
                    '|' ORDER BY import_run.id), ''))
                FROM derivacao_etapa_import_run import_run
                LEFT JOIN derivacao_etapa_import_run reviewed_scan
                  ON reviewed_scan.id = import_run.reviewed_scan_id
                WHERE import_run.run_kind = 'import' AND import_run.status = 'ok'
                  AND import_run.reviewed_scan_id IS NOT NULL
            ),
            (
                SELECT md5(coalesce(string_agg(
                    concat_ws('~', id, data_inicio, data_fim, id_etapa,
                              meta_dia, id_servico),
                    '|' ORDER BY id), ''))
                FROM meta_etapa
            ),
            ({volume_source_sql}),
            (
                SELECT md5(coalesce(string_agg(
                    concat_ws('~', quarter_from, quarter_to, weekday, scope,
                              id_cliente, id_workflow, hourly_volumes::text,
                              trusted_volume),
                    '|' ORDER BY quarter_from, quarter_to, weekday, scope,
                                 id_cliente, id_workflow), ''))
                FROM capacity_hourly_profile_snapshot
            )
        ))
    """
    params = [
        source_scenario,
        *volume_params,
    ]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        database_digest = cursor.fetchone()[0]
    return hashlib.sha256(database_digest.encode("ascii")).hexdigest()
