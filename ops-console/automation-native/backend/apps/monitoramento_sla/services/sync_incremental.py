from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow
from apps.monitoramento_sla.models import SlaUtilDetalhe, SlaUtilSyncRun
from apps.monitoramento_sla.services.classificacao import (
    classificar_ajustado,
    classificar_natural,
)
from apps.monitoramento_sla.services.consolidado import rebuild_consolidado
from apps.monitoramento_sla.services.faixas import classificar_faixa
from apps.monitoramento_sla.services.instrumentation import SyncMetrics
from apps.monitoramento_sla.services.keys import (
    chave_nh,
    date_key,
    key_cwn,
    normalize_nome,
)
from apps.monitoramento_sla.services.projecao_lookup import (
    find_projecao_dia,
    load_projecao_rows_for_range,
)
from apps.monitoramento_sla.services.projection_calendar import ProjectionCalendar
from apps.monitoramento_sla.services.resumo_cache import bump_resumo_cache_version
from apps.monitoramento_sla.services.sla_util import calcular_sla_util_segundos
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/Sao_Paulo")
UPSERT_BATCH_SIZE = 2000


def _dim_indexes():
    clientes = {
        normalize_nome(nome): int(pk)
        for pk, nome in DimCliente.objects.values_list("id_cliente", "nome")
    }
    workflows = {
        normalize_nome(nome): int(pk)
        for pk, nome in DimWorkflow.objects.values_list("id_workflow", "nome")
    }
    nhs = {
        normalize_nome(nome): int(pk)
        for pk, nome in DimNivelHierarquico.objects.values_list("id_nh", "nome")
    }
    nomes_c = dict(DimCliente.objects.values_list("id_cliente", "nome"))
    nomes_w = dict(DimWorkflow.objects.values_list("id_workflow", "nome"))
    nomes_n = dict(DimNivelHierarquico.objects.values_list("id_nh", "nome"))
    return clientes, workflows, nhs, nomes_c, nomes_w, nomes_n


def _source_tuple(row) -> tuple[str, str, str]:
    return (
        str(row.protocolo),
        normalize_nome(row.workflow),
        normalize_nome(row.nivel_hierarquico),
    )


def _source_key(row) -> str:
    return "|".join(_source_tuple(row))


def _source_fingerprint(row) -> str:
    relevant = (
        str(row.protocolo),
        normalize_nome(row.cliente),
        normalize_nome(row.workflow),
        normalize_nome(row.nivel_hierarquico),
        row.data_cadastro,
        row.data_conclusao,
        row.resultado or "",
    )
    return hashlib.sha256(repr(relevant).encode("utf-8")).hexdigest()


def _dedupe_origem(qs) -> dict[tuple[str, str, str], Any]:
    """Último por protocolo/workflow/NH, preferindo um registro concluído."""
    best: dict[tuple[str, str, str], Any] = {}
    for row in qs.iterator(chunk_size=5000):
        if not row.protocolo or not row.data_cadastro:
            continue
        key = _source_tuple(row)
        previous = best.get(key)
        if previous is None:
            best[key] = row
            continue
        previous_closed = previous.data_conclusao is not None
        current_closed = row.data_conclusao is not None
        if current_closed and not previous_closed:
            best[key] = row
        elif current_closed == previous_closed and (
            row.report_date or date.min
        ) >= (previous.report_date or date.min):
            best[key] = row
    return best


def _last_projection_signature() -> str:
    previous = (
        SlaUtilSyncRun.objects.filter(status=SlaUtilSyncRun.STATUS_OK)
        .order_by("-finished_at")
        .values_list("metrics", flat=True)
        .first()
    )
    if not isinstance(previous, dict):
        return ""
    counts = previous.get("counts")
    return str(counts.get("projection_signature", "")) if isinstance(counts, dict) else ""


def _upsert_details(records: list[SlaUtilDetalhe]) -> int:
    if not records:
        return 0
    update_fields = [
        field.name
        for field in SlaUtilDetalhe._meta.concrete_fields
        if field.name not in {"id", "source_key"}
    ]
    for start in range(0, len(records), UPSERT_BATCH_SIZE):
        with transaction.atomic():
            SlaUtilDetalhe.objects.bulk_create(
                records[start : start + UPSERT_BATCH_SIZE],
                batch_size=UPSERT_BATCH_SIZE,
                update_conflicts=True,
                update_fields=update_fields,
                unique_fields=["source_key"],
            )
    return len(records)


def sync_monitoramento_sla(
    *,
    days: int = 90,
    user=None,
    cutover_at: datetime | None = None,
) -> SlaUtilSyncRun:
    cutover = cutover_at or timezone.now().astimezone(TZ)
    metrics = SyncMetrics()
    run = SlaUtilSyncRun.objects.create(
        status=SlaUtilSyncRun.STATUS_RUNNING,
        cutover_at=cutover,
        triggered_by=user if getattr(user, "pk", None) else None,
    )
    try:
        since = cutover.date() - timedelta(days=max(1, days))
        with metrics.stage("source_read_and_dedupe"):
            source_qs = (
                RotinaDetalhadoBrutoRecord.objects.filter(data_cadastro__gte=since)
                .order_by("report_date")
                .only(
                    "report_date",
                    "protocolo",
                    "cliente",
                    "workflow",
                    "nivel_hierarquico",
                    "data_cadastro",
                    "data_conclusao",
                    "resultado",
                )
            )
            rows_map = _dedupe_origem(source_qs)
        metrics.count("source_rows_deduped", len(rows_map))

        with metrics.stage("dimensions_and_projection_calendar"):
            clientes, workflows, nhs, nomes_c, nomes_w, nomes_n = _dim_indexes()
            date_min = since
            date_max = cutover.date()
            for row in rows_map.values():
                date_min = min(date_min, row.data_cadastro)
                if row.data_conclusao:
                    date_max = max(date_max, row.data_conclusao)
            projection_rows = load_projecao_rows_for_range(
                date_min=date_min,
                date_max=date_max + timedelta(days=60),
            )
            calendar = ProjectionCalendar(
                projection_rows,
                date_min=date_min,
                date_max=date_max + timedelta(days=60),
            )
        metrics.count("projection_rows", len(projection_rows))
        metrics.count("projection_signature", calendar.signature)

        with metrics.stage("incremental_diff"):
            existing = {
                row["source_key"]: row
                for row in SlaUtilDetalhe.objects.filter(
                    data_cadastro__gte=since
                ).values(
                    "source_key",
                    "source_fingerprint",
                    "data_cadastro",
                    "key_cwn",
                    "em_aberto",
                )
            }
            projection_changed = _last_projection_signature() != calendar.signature
            full_refresh = projection_changed or not existing
            prepared = []
            groups: dict[tuple[int, date], list[str]] = defaultdict(list)
            affected_groups: set[tuple[int, date]] = set()
            current_source_keys: set[str] = set()

            for row in rows_map.values():
                id_cliente = clientes.get(normalize_nome(row.cliente))
                id_workflow = workflows.get(normalize_nome(row.workflow))
                id_nh = nhs.get(normalize_nome(row.nivel_hierarquico))
                kcwn = (
                    key_cwn(id_cliente, id_workflow, id_nh)
                    if None not in (id_cliente, id_workflow, id_nh)
                    else None
                )
                source_key = _source_key(row)
                fingerprint = _source_fingerprint(row)
                group = (kcwn, row.data_cadastro) if kcwn is not None else None
                previous = existing.get(source_key)
                changed = (
                    full_refresh
                    or previous is None
                    or previous["source_fingerprint"] != fingerprint
                    or row.data_conclusao is None
                )
                if changed and group is not None:
                    affected_groups.add(group)
                if changed and previous and previous["key_cwn"] is not None:
                    affected_groups.add(
                        (previous["key_cwn"], previous["data_cadastro"])
                    )
                if group is not None:
                    groups[group].append(str(row.protocolo))
                current_source_keys.add(source_key)
                prepared.append(
                    (
                        row,
                        id_cliente,
                        id_workflow,
                        id_nh,
                        kcwn,
                        source_key,
                        fingerprint,
                        group,
                        changed,
                    )
                )

            for missing_key in set(existing) - current_source_keys:
                previous = existing[missing_key]
                if previous["key_cwn"] is not None:
                    affected_groups.add(
                        (previous["key_cwn"], previous["data_cadastro"])
                    )

            ranks: dict[tuple[int, date, str], int] = {}
            for (kcwn, data_cadastro), protocols in groups.items():
                for rank, protocol in enumerate(sorted(set(protocols)), start=1):
                    ranks[(kcwn, data_cadastro, protocol)] = rank

        metrics.count("full_refresh", full_refresh)
        metrics.count("projection_changed", projection_changed)
        metrics.count("affected_groups", len(affected_groups))

        staged: list[SlaUtilDetalhe] = []
        affected_dates: set[date] = set()
        with metrics.stage("sla_calculation"):
            for (
                row,
                id_cliente,
                id_workflow,
                id_nh,
                kcwn,
                source_key,
                fingerprint,
                group,
                changed,
            ) in prepared:
                if not (full_refresh or changed or group in affected_groups):
                    continue

                problems: list[str] = []
                if id_cliente is None:
                    problems.append("CLIENTE_NAO_ENCONTRADO")
                if id_workflow is None:
                    problems.append("WORKFLOW_NAO_ENCONTRADO")
                if id_nh is None:
                    problems.append("NIVEL_NAO_ENCONTRADO")

                data_cadastro = row.data_cadastro
                # A origem RotinaDetalhadoBrutoRecord possui somente a data.
                # 00:00 continua sendo usado internamente na regra de SLA para
                # preservar o resultado diário, mas não é persistido como se
                # fosse um horário real de recebimento.
                hora_cadastro_calculo = time(0, 0)
                hora_cadastro = None
                em_aberto = row.data_conclusao is None
                data_fim = cutover.date() if em_aberto else row.data_conclusao
                hora_fim = (
                    cutover.timetz().replace(tzinfo=None)
                    if em_aberto
                    else time(0, 0)
                )
                sla_segundos = None
                proj_cadastro = None
                vencimento = None
                natural = ""
                faixa = ""

                if None not in (id_cliente, id_workflow, id_nh):
                    sla_segundos, sla_problems = calcular_sla_util_segundos(
                        id_cliente=id_cliente,
                        id_workflow=id_workflow,
                        id_nh=id_nh,
                        data_cadastro=data_cadastro,
                        hora_cadastro=hora_cadastro_calculo,
                        data_fim=data_fim,
                        hora_fim=hora_fim,
                        proj_rows=calendar,
                    )
                    problems.extend(sla_problems)
                    proj_cadastro, projection_problems = find_projecao_dia(
                        id_cliente=id_cliente,
                        id_workflow=id_workflow,
                        id_nh=id_nh,
                        on_date=data_cadastro,
                        rows=calendar,
                    )
                    problems.extend(projection_problems)
                    natural, vencimento, natural_problems = classificar_natural(
                        id_workflow=id_workflow,
                        data_cadastro=data_cadastro,
                        data_referencia=data_fim,
                        sla_segundos=sla_segundos,
                        proj_cadastro=proj_cadastro,
                        em_aberto=em_aberto,
                        id_cliente=id_cliente,
                        id_nh=id_nh,
                        proj_rows=calendar,
                    )
                    problems.extend(natural_problems)
                    faixa = classificar_faixa(sla_segundos) or ""

                rank = (
                    ranks.get((kcwn, data_cadastro, str(row.protocolo)))
                    if kcwn is not None
                    else None
                )
                adjusted = classificar_ajustado(
                    sla_natural=natural or "Fora",
                    sla_segundos=sla_segundos,
                    proj_cadastro=proj_cadastro,
                    rank_protocolo=rank,
                )
                previous = existing.get(source_key)
                if previous:
                    affected_dates.add(previous["data_cadastro"])
                affected_dates.add(data_cadastro)
                staged.append(
                    SlaUtilDetalhe(
                        source_key=source_key,
                        protocolo=str(row.protocolo),
                        id_cliente=id_cliente,
                        id_workflow=id_workflow,
                        id_nh=id_nh,
                        cliente_nome=nomes_c.get(id_cliente, row.cliente or ""),
                        workflow_nome=nomes_w.get(id_workflow, row.workflow or ""),
                        nh_nome=nomes_n.get(id_nh, row.nivel_hierarquico or ""),
                        data_cadastro=data_cadastro,
                        hora_cadastro=hora_cadastro,
                        hora_cadastro_fonte=SlaUtilDetalhe.HORA_FONTE_INDISPONIVEL,
                        data_conclusao=None if em_aberto else data_fim,
                        hora_conclusao=None if em_aberto else hora_fim,
                        em_aberto=em_aberto,
                        resultado=row.resultado or "",
                        tipo_conclusao="",
                        avaliacao="",
                        date_key_cadastro=date_key(data_cadastro) or 0,
                        date_key_conclusao=date_key(None if em_aberto else data_fim),
                        key_cwn=kcwn,
                        chave_nh=chave_nh(row.protocolo, id_nh or 0),
                        sla_segundos=sla_segundos,
                        data_vencimento_d2u=vencimento,
                        sla_descricao_natural=natural,
                        sla_descricao_ajustado=adjusted,
                        faixa=faixa,
                        problemas=sorted(set(problems)),
                        source_report_date=row.report_date,
                        source_fingerprint=fingerprint,
                        sync_run=run,
                    )
                )

        with metrics.stage("detail_upsert"):
            changed_count = _upsert_details(staged)
        metrics.count("detail_rows_changed", changed_count)
        metrics.count("affected_dates", len(affected_dates))

        with metrics.stage("consolidated_incremental"):
            consolidated_count = rebuild_consolidado(
                sync_run=run,
                affected_dates=affected_dates,
            )
        metrics.count("consolidated_rows_rebuilt", consolidated_count)
        if affected_dates:
            bump_resumo_cache_version()

        run.status = SlaUtilSyncRun.STATUS_OK
        run.rows_detalhe = changed_count
        run.rows_consolidado = consolidated_count
        run.finished_at = timezone.now()
        run.metrics = metrics.payload()
        mode = "full" if full_refresh else "incremental"
        run.message = (
            f"OK mode={mode} detalhe={changed_count} "
            f"consolidado={consolidated_count}"
        )
        run.save(
            update_fields=[
                "status",
                "rows_detalhe",
                "rows_consolidado",
                "finished_at",
                "message",
                "metrics",
            ]
        )
        return run
    except Exception as exc:
        log.exception("sync_monitoramento_sla incremental failed")
        run.status = SlaUtilSyncRun.STATUS_ERROR
        run.message = str(exc)
        run.finished_at = timezone.now()
        run.metrics = metrics.payload()
        run.save(update_fields=["status", "message", "finished_at", "metrics"])
        raise
