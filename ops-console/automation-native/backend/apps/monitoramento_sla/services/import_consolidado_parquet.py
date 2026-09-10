# -*- coding: utf-8 -*-
"""Importa consolidado Power BI (Parquet) → sla_util_consolidado (homologação / impacto)."""
from __future__ import annotations

import logging
import tempfile
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from django.db import connection, transaction
from django.db.models import Case, Count, IntegerField, Q, Sum, When
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilSyncRun
from apps.monitoramento_sla.services.resumo_cache import bump_resumo_cache_version

log = logging.getLogger(__name__)

REQUIRED_COLS = {
    "data_cadastro",
    "id_cliente",
    "id_workflow",
    "id_nivel_hierarquico",
    "quantidade",
    "sla_descricao_natural",
}


@dataclass(frozen=True)
class MonthStats:
    linhas: int
    volume: int


@dataclass(frozen=True)
class MonthPlanRow:
    competencia: str
    linhas_parquet: int
    volume_parquet: int
    linhas_db: int | None
    volume_db: int | None
    action: str  # skip | replace
    situation: str


def _parquet_path_sql(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _month_bounds(competencia: str) -> tuple[date, date]:
    year_s, month_s = competencia.split("-", 1)
    year, month = int(year_s), int(month_s)
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return start, end


def _validate_parquet_columns(path: Path) -> None:
    import duckdb

    p = _parquet_path_sql(path)
    con = duckdb.connect()
    cols = list(con.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 0").fetchdf().columns)
    missing = REQUIRED_COLS - set(cols)
    if missing:
        raise ValueError(f"Parquet sem colunas: {sorted(missing)}")


def kpi_from_parquet(path: str | Path) -> dict[str, Any]:
    import duckdb

    p = _parquet_path_sql(Path(path))
    con = duckdb.connect()
    row = con.execute(
        f"""
        SELECT
          count(*) AS linhas,
          coalesce(sum(quantidade), 0) AS volume,
          coalesce(sum(CASE WHEN sla_descricao_natural = 'Dentro' THEN quantidade ELSE 0 END), 0) AS nat_dentro,
          coalesce(sum(CASE WHEN sla_descricao_natural = 'Fora' THEN quantidade ELSE 0 END), 0) AS nat_fora,
          coalesce(sum(CASE WHEN sla_descricao_desvio_volume = 'Dentro' THEN quantidade ELSE 0 END), 0) AS aj_dentro,
          coalesce(sum(CASE WHEN sla_descricao_desvio_volume = 'Fora' THEN quantidade ELSE 0 END), 0) AS aj_fora
        FROM read_parquet('{p}')
        """
    ).fetchone()
    volume = float(row[1] or 0)
    return {
        "linhas": int(row[0] or 0),
        "volume": int(volume),
        "natural": {
            "dentro": int(row[2] or 0),
            "fora": int(row[3] or 0),
            "pct_dentro": round(100.0 * float(row[2] or 0) / volume, 2) if volume else None,
        },
        "ajustado": {
            "dentro": int(row[4] or 0),
            "fora": int(row[5] or 0),
            "pct_dentro": round(100.0 * float(row[4] or 0) / volume, 2) if volume else None,
        },
    }


def _consolidado_qs(competencias: list[str] | None = None):
    qs = SlaUtilConsolidado.objects.all()
    if not competencias:
        return qs
    month_filter = Q()
    for competencia in competencias:
        start, end = _month_bounds(competencia)
        month_filter |= Q(data_cadastro__gte=start, data_cadastro__lte=end)
    return qs.filter(month_filter)


def kpi_from_db(competencias: list[str] | None = None) -> dict[str, Any]:
    agg = _consolidado_qs(competencias).aggregate(
        linhas=Count("id"),
        volume=Sum("quantidade"),
        nat_dentro=Sum(
            Case(
                When(sla_descricao_natural="Dentro", then="quantidade"),
                default=0,
                output_field=IntegerField(),
            )
        ),
        nat_fora=Sum(
            Case(
                When(sla_descricao_natural="Fora", then="quantidade"),
                default=0,
                output_field=IntegerField(),
            )
        ),
        aj_dentro=Sum(
            Case(
                When(sla_descricao_ajustado="Dentro", then="quantidade"),
                default=0,
                output_field=IntegerField(),
            )
        ),
        aj_fora=Sum(
            Case(
                When(sla_descricao_ajustado="Fora", then="quantidade"),
                default=0,
                output_field=IntegerField(),
            )
        ),
    )
    volume = int(agg["volume"] or 0)
    nat_dentro = int(agg["nat_dentro"] or 0)
    nat_fora = int(agg["nat_fora"] or 0)
    aj_dentro = int(agg["aj_dentro"] or 0)
    aj_fora = int(agg["aj_fora"] or 0)
    return {
        "linhas": int(agg["linhas"] or 0),
        "volume": volume,
        "natural": {
            "dentro": nat_dentro,
            "fora": nat_fora,
            "pct_dentro": round(100.0 * nat_dentro / volume, 2) if volume else None,
        },
        "ajustado": {
            "dentro": aj_dentro,
            "fora": aj_fora,
            "pct_dentro": round(100.0 * aj_dentro / volume, 2) if volume else None,
        },
    }


def month_stats_from_parquet(path: str | Path) -> dict[str, MonthStats]:
    import duckdb

    p = _parquet_path_sql(Path(path))
    con = duckdb.connect()
    rows = con.execute(
        f"""
        SELECT
          strftime(CAST(try_cast(data_cadastro AS TIMESTAMP) AS DATE), '%Y-%m') AS competencia,
          count(*) AS linhas,
          coalesce(sum(try_cast(quantidade AS BIGINT)), 0) AS volume
        FROM read_parquet('{p}')
        WHERE try_cast(data_cadastro AS TIMESTAMP) IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    return {
        str(row[0]): MonthStats(linhas=int(row[1] or 0), volume=int(row[2] or 0))
        for row in rows
        if row[0]
    }


def month_stats_from_db(competencias: list[str] | None = None) -> dict[str, MonthStats]:
    rows = (
        _consolidado_qs(competencias)
        .annotate(month=TruncMonth("data_cadastro"))
        .values("month")
        .annotate(linhas=Count("id"), volume=Sum("quantidade"))
        .order_by("month")
    )
    stats: dict[str, MonthStats] = {}
    for row in rows:
        month = row["month"]
        if month is None:
            continue
        competencia = month.strftime("%Y-%m")
        stats[competencia] = MonthStats(
            linhas=int(row["linhas"] or 0),
            volume=int(row["volume"] or 0),
        )
    return stats


def _month_action(parquet: MonthStats, db: MonthStats | None) -> tuple[str, str]:
    if db is None:
        return "replace", "Nova base"
    if parquet.linhas == db.linhas and parquet.volume == db.volume:
        return "skip", "Sem alteração"
    return "replace", "Substituir"


def build_month_plan(path: str | Path) -> list[MonthPlanRow]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    _validate_parquet_columns(path)
    parquet_stats = month_stats_from_parquet(path)
    db_stats = month_stats_from_db(sorted(parquet_stats.keys()))
    plan: list[MonthPlanRow] = []
    for competencia in sorted(parquet_stats):
        pq = parquet_stats[competencia]
        db = db_stats.get(competencia)
        action, situation = _month_action(pq, db)
        plan.append(
            MonthPlanRow(
                competencia=competencia,
                linhas_parquet=pq.linhas,
                volume_parquet=pq.volume,
                linhas_db=None if db is None else db.linhas,
                volume_db=None if db is None else db.volume,
                action=action,
                situation=situation,
            )
        )
    return plan


def month_plan_to_dict(plan: list[MonthPlanRow]) -> list[dict[str, Any]]:
    return [
        {
            "competencia": row.competencia,
            "linhas_parquet": row.linhas_parquet,
            "volume_parquet": row.volume_parquet,
            "linhas_db": row.linhas_db,
            "volume_db": row.volume_db,
            "action": row.action,
            "situation": row.situation,
        }
        for row in plan
    ]


def _months_sql_filter(months: list[str] | None) -> str:
    if not months:
        return ""
    quoted = ", ".join(f"'{month}'" for month in months)
    return (
        f" AND strftime(CAST(try_cast(data_cadastro AS TIMESTAMP) AS DATE), '%Y-%m') IN ({quoted})"
    )


def _export_tsv_via_duckdb(
    path: Path,
    sync_run_id: int,
    out_tsv: Path,
    *,
    months: list[str] | None = None,
) -> int:
    import duckdb

    p = _parquet_path_sql(path)
    out = str(out_tsv.resolve()).replace("\\", "/")
    con = duckdb.connect()
    cols = list(con.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 0").fetchdf().columns)
    missing = REQUIRED_COLS - set(cols)
    if missing:
        raise ValueError(f"Parquet sem colunas: {sorted(missing)}")

    has_desvio = "sla_descricao_desvio_volume" in cols
    has_faixa = "faixa_sla" in cols
    has_tipo = "tipo_conclusao" in cols
    has_resultado = "resultado" in cols
    has_av = "av" in cols
    has_hora_cad = "hora_cadastro" in cols
    has_hora_conc = "hora_conclusao" in cols
    has_data_conc = "data_conclusao" in cols

    desvio_expr = (
        "coalesce(cast(sla_descricao_desvio_volume as varchar), '')"
        if has_desvio
        else "''"
    )
    faixa_expr = "coalesce(cast(faixa_sla as varchar), '')" if has_faixa else "''"
    tipo_expr = "coalesce(cast(tipo_conclusao as varchar), '')" if has_tipo else "''"
    resultado_expr = "coalesce(cast(resultado as varchar), '')" if has_resultado else "''"
    av_expr = "coalesce(cast(av as varchar), '')" if has_av else "''"
    hora_cad_expr = (
        """
        CASE
          WHEN hora_cadastro IS NULL THEN NULL
          WHEN try_cast(hora_cadastro AS INTEGER) BETWEEN 0 AND 23
            THEN printf('%02d:00:00', try_cast(hora_cadastro AS INTEGER))
          ELSE NULL
        END
        """
        if has_hora_cad
        else "NULL"
    )
    hora_conc_expr = (
        """
        CASE
          WHEN hora_conclusao IS NULL THEN NULL
          WHEN try_cast(hora_conclusao AS INTEGER) BETWEEN 0 AND 23
            THEN printf('%02d:00:00', try_cast(hora_conclusao AS INTEGER))
          ELSE NULL
        END
        """
        if has_hora_conc
        else "NULL"
    )
    data_conc_expr = (
        "CAST(try_cast(data_conclusao AS TIMESTAMP) AS DATE)" if has_data_conc else "NULL"
    )
    month_filter = _months_sql_filter(months)

    sql = f"""
    COPY (
      SELECT
        CAST(try_cast(data_cadastro AS TIMESTAMP) AS DATE) AS data_cadastro,
        {hora_cad_expr} AS hora_cadastro,
        CASE
          WHEN {hora_cad_expr} IS NULL THEN 'unavailable'
          ELSE 'real'
        END AS hora_cadastro_fonte,
        {data_conc_expr} AS data_conclusao,
        {hora_conc_expr} AS hora_conclusao,
        try_cast(id_cliente AS INTEGER) AS id_cliente,
        try_cast(id_workflow AS INTEGER) AS id_workflow,
        try_cast(id_nivel_hierarquico AS INTEGER) AS id_nh,
        '' AS cliente_nome,
        '' AS workflow_nome,
        '' AS nh_nome,
        {tipo_expr} AS tipo_conclusao,
        {resultado_expr} AS resultado,
        coalesce(cast(sla_descricao_natural as varchar), '') AS sla_descricao_natural,
        {desvio_expr} AS sla_descricao_ajustado,
        {faixa_expr} AS faixa,
        {av_expr} AS avaliacao,
        greatest(0, cast(coalesce(try_cast(quantidade AS BIGINT), 0) AS BIGINT)) AS quantidade,
        (
          year(CAST(try_cast(data_cadastro AS TIMESTAMP) AS DATE)) * 10000
          + month(CAST(try_cast(data_cadastro AS TIMESTAMP) AS DATE)) * 100
          + day(CAST(try_cast(data_cadastro AS TIMESTAMP) AS DATE))
        ) AS date_key_cadastro,
        {int(sync_run_id)} AS sync_run_id
      FROM read_parquet('{p}')
      WHERE try_cast(data_cadastro AS TIMESTAMP) IS NOT NULL
      {month_filter}
    ) TO '{out}' (HEADER false, DELIMITER '\\t', NULL '')
    """
    con.execute(sql)
    count_sql = (
        f"SELECT count(*) FROM read_parquet('{p}') "
        f"WHERE try_cast(data_cadastro AS TIMESTAMP) IS NOT NULL{month_filter}"
    )
    n = con.execute(count_sql).fetchone()[0]
    return int(n)


def _copy_tsv_to_db(tmp_path: Path) -> None:
    columns = [
        "data_cadastro",
        "hora_cadastro",
        "hora_cadastro_fonte",
        "data_conclusao",
        "hora_conclusao",
        "id_cliente",
        "id_workflow",
        "id_nh",
        "cliente_nome",
        "workflow_nome",
        "nh_nome",
        "tipo_conclusao",
        "resultado",
        "sla_descricao_natural",
        "sla_descricao_ajustado",
        "faixa",
        "avaliacao",
        "quantidade",
        "date_key_cadastro",
        "sync_run_id",
    ]
    cols_sql = ", ".join(columns)
    copy_sql = (
        f"COPY sla_util_consolidado ({cols_sql}) FROM STDIN WITH "
        f"(FORMAT csv, DELIMITER E'\\t', NULL '')"
    )
    with connection.cursor() as cursor:
        with open(tmp_path, "rb") as f:
            if hasattr(cursor, "copy_expert"):
                cursor.copy_expert(copy_sql, f)
            else:
                with cursor.copy(copy_sql) as copy:
                    while True:
                        chunk = f.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        copy.write(chunk)


def _delete_month(competencia: str) -> int:
    start, end = _month_bounds(competencia)
    return SlaUtilConsolidado.objects.filter(
        data_cadastro__gte=start,
        data_cadastro__lte=end,
    ).delete()[0]


def _delete_months(months: list[str]) -> int:
    deleted = 0
    for competencia in months:
        deleted += _delete_month(competencia)
    return deleted


PARQUET_IMPORT_KIND = "parquet_import"


def _merge_run_metrics(run: SlaUtilSyncRun, patch: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(run.metrics or {})
    metrics.update(patch)
    return metrics


def _import_single_month(path: Path, sync_run_id: int, competencia: str) -> int:
    with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        expected = _export_tsv_via_duckdb(path, sync_run_id, tmp_path, months=[competencia])
        _copy_tsv_to_db(tmp_path)
        return expected
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def import_consolidado_parquet_months(
    path: str | Path,
    months: list[str],
    *,
    user=None,
    filename: str = "",
    run: SlaUtilSyncRun | None = None,
    upload_id: str = "",
) -> tuple[SlaUtilSyncRun, dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not months:
        raise ValueError("Informe ao menos uma competência para importar.")

    _validate_parquet_columns(path)
    plan = build_month_plan(path)
    allowed = {row.competencia for row in plan if row.action == "replace"}
    invalid = sorted(set(months) - allowed)
    if invalid:
        raise ValueError(
            f"Competências inválidas ou sem alteração: {', '.join(invalid)}"
        )

    display_name = filename or path.name
    sorted_months = sorted(months)
    skipped = [row.competencia for row in plan if row.action == "skip"]
    created_run = run is None
    if run is None:
        cutover = timezone.now()
        run = SlaUtilSyncRun.objects.create(
            status=SlaUtilSyncRun.STATUS_RUNNING,
            cutover_at=cutover,
            triggered_by=user if getattr(user, "pk", None) else None,
            message=f"Import parquet parcial: {display_name} meses={','.join(sorted_months)}",
            metrics={
                "kind": PARQUET_IMPORT_KIND,
                "phase": "processing",
                "upload_id": upload_id,
                "filename": display_name,
                "months_total": len(sorted_months),
                "months_done": 0,
                "current_competencia": "",
                "rows_written": 0,
                "rows_deleted": 0,
                "months_applied": [],
                "months_skipped": skipped,
            },
        )

    rows_written_total = 0
    rows_deleted_total = 0
    months_applied: list[str] = []

    try:
        run.metrics = _merge_run_metrics(
            run,
            {
                "kind": PARQUET_IMPORT_KIND,
                "phase": "processing",
                "upload_id": upload_id or (run.metrics or {}).get("upload_id", ""),
                "filename": display_name,
                "months_total": len(sorted_months),
                "months_done": 0,
                "current_competencia": sorted_months[0] if sorted_months else "",
                "rows_written": 0,
                "rows_deleted": 0,
                "months_applied": [],
                "months_skipped": skipped,
            },
        )
        run.save(update_fields=["metrics"])

        for index, competencia in enumerate(sorted_months):
            run.metrics = _merge_run_metrics(
                run,
                {
                    "phase": "processing",
                    "months_done": index,
                    "current_competencia": competencia,
                    "rows_written": rows_written_total,
                    "rows_deleted": rows_deleted_total,
                    "months_applied": list(months_applied),
                },
            )
            run.save(update_fields=["metrics"])

            with transaction.atomic():
                deleted = _delete_month(competencia)
                written = _import_single_month(path, run.pk, competencia)

            rows_deleted_total += deleted
            rows_written_total += written
            months_applied.append(competencia)

        db_kpis = kpi_from_db(competencias=sorted_months)
        run.status = SlaUtilSyncRun.STATUS_OK
        run.rows_consolidado = rows_written_total
        run.rows_detalhe = 0
        run.finished_at = timezone.now()
        run.message = (
            f"Import {display_name}: {rows_written_total} linhas em {len(sorted_months)} mês(es); "
            f"removidas={rows_deleted_total}; ignorados={len(skipped)}"
        )
        run.metrics = _merge_run_metrics(
            run,
            {
                "phase": "done",
                "months_done": len(sorted_months),
                "current_competencia": "",
                "rows_written": rows_written_total,
                "rows_deleted": rows_deleted_total,
                "months_applied": months_applied,
                "months_skipped": skipped,
                "db_kpis": db_kpis,
            },
        )
        run.save(
            update_fields=[
                "status",
                "rows_consolidado",
                "rows_detalhe",
                "finished_at",
                "message",
                "metrics",
            ]
        )
        bump_resumo_cache_version()
        return run, {
            "rows_written": rows_written_total,
            "rows_deleted": rows_deleted_total,
            "months_applied": months_applied,
            "months_skipped": skipped,
            "db": db_kpis,
        }
    except Exception as exc:
        log.exception("import consolidado parcial failed")
        run.status = SlaUtilSyncRun.STATUS_ERROR
        run.message = str(exc)[:500]
        run.finished_at = timezone.now()
        run.metrics = _merge_run_metrics(
            run,
            {
                "phase": "error",
                "rows_written": rows_written_total,
                "rows_deleted": rows_deleted_total,
                "months_applied": months_applied,
                "error": str(exc)[:500],
            },
        )
        run.save(update_fields=["status", "message", "finished_at", "metrics"])
        if created_run:
            raise
        return run, {
            "rows_written": rows_written_total,
            "rows_deleted": rows_deleted_total,
            "months_applied": months_applied,
            "months_skipped": skipped,
            "error": str(exc),
        }


@transaction.atomic
def import_consolidado_parquet(
    path: str | Path,
    *,
    replace: bool = True,
    user=None,
) -> tuple[SlaUtilSyncRun, dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    parquet_kpis = kpi_from_parquet(path)
    cutover = timezone.now()
    run = SlaUtilSyncRun.objects.create(
        status=SlaUtilSyncRun.STATUS_RUNNING,
        cutover_at=cutover,
        triggered_by=user if getattr(user, "pk", None) else None,
        message=f"Import consolidado parquet: {path.name}",
    )

    try:
        if replace:
            SlaUtilConsolidado.objects.all().delete()

        with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        expected = _export_tsv_via_duckdb(path, run.pk, tmp_path)
        _copy_tsv_to_db(tmp_path)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

        db_kpis = kpi_from_db()
        run.status = SlaUtilSyncRun.STATUS_OK
        run.rows_consolidado = expected
        run.rows_detalhe = 0
        run.finished_at = timezone.now()
        match = (
            parquet_kpis["volume"] == db_kpis["volume"]
            and parquet_kpis["natural"]["pct_dentro"] == db_kpis["natural"]["pct_dentro"]
            and parquet_kpis["ajustado"]["pct_dentro"] == db_kpis["ajustado"]["pct_dentro"]
        )
        run.message = (
            f"Import {path.name}: ~{expected} linhas. "
            f"KPIs {'OK' if match else 'DIVERGEM'} | "
            f"parquet vol={parquet_kpis['volume']} nat%={parquet_kpis['natural']['pct_dentro']} "
            f"aj%={parquet_kpis['ajustado']['pct_dentro']} | "
            f"db vol={db_kpis['volume']} nat%={db_kpis['natural']['pct_dentro']} "
            f"aj%={db_kpis['ajustado']['pct_dentro']}"
        )
        run.save(
            update_fields=[
                "status",
                "rows_consolidado",
                "rows_detalhe",
                "finished_at",
                "message",
            ]
        )
        bump_resumo_cache_version()
        return run, {
            "parquet": parquet_kpis,
            "db": db_kpis,
            "rows_written": expected,
            "kpis_match": match,
        }
    except Exception as exc:
        log.exception("import consolidado failed")
        run.status = SlaUtilSyncRun.STATUS_ERROR
        run.message = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "finished_at"])
        raise
