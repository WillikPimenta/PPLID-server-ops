# -*- coding: utf-8 -*-
"""Reconciliação determinística plano × confirmação D-1."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Reconciliacao,
    ReplicacaoD1Replicado,
)
from apps.replicacao_d1.normalization import (
    STATUS_DIVERGENTE,
    STATUS_FALHOU,
    STATUS_PENDENTE,
    STATUS_RECEBIDO,
    STATUS_REPLICADO,
    brflow_to_status_operacional,
    normalize_protocolo,
)

RECONCILIATION_RULE_VERSION = "2"
# O ``bulk_update`` do Django monta uma expressao CASE por campo/linha antes de
# enviar o SQL. Lotes grandes fazem o processo gastar minutos em CPU para runs
# com milhares de protocolos. Lotes menores mantem essa arvore limitada; as
# reconciliacoes usam UPSERT abaixo e nao pagam mais esse custo.
RECONCILIATION_BATCH_SIZE = 100
RECONCILIATION_UPSERT_BATCH_SIZE = 1000
PROTOCOL_UPDATE_BATCH_SIZE = 1000


def _brflow_status_operacional(status_brflow: str) -> str:
    return brflow_to_status_operacional(status_brflow)


def _bulk_update_protocols(protocolos: list[ReplicacaoD1Protocolo]) -> int:
    """Atualiza o resultado sem construir milhares de CASEs no PostgreSQL."""
    if not protocolos:
        return 0

    fields = [
        "protocolo_normalizado",
        "status_operacional",
        "replicado_em",
        "erro_resumido",
    ]
    if connection.vendor != "postgresql":
        updated = 0
        for start in range(0, len(protocolos), RECONCILIATION_BATCH_SIZE):
            updated += ReplicacaoD1Protocolo.objects.bulk_update(
                protocolos[start : start + RECONCILIATION_BATCH_SIZE],
                fields,
                batch_size=RECONCILIATION_BATCH_SIZE,
            )
        return updated

    quote = connection.ops.quote_name
    table = quote(ReplicacaoD1Protocolo._meta.db_table)
    row_sql = (
        "(%s::bigint, %s::varchar(64), %s::varchar(16), "
        "%s::timestamptz, %s::varchar(255))"
    )
    updated = 0
    for start in range(0, len(protocolos), PROTOCOL_UPDATE_BATCH_SIZE):
        batch = protocolos[start : start + PROTOCOL_UPDATE_BATCH_SIZE]
        values_sql = ", ".join([row_sql] * len(batch))
        params = []
        for prot in batch:
            params.extend(
                [
                    prot.pk,
                    prot.protocolo_normalizado,
                    prot.status_operacional,
                    prot.replicado_em,
                    prot.erro_resumido,
                ]
            )
        sql = f"""
            UPDATE {table} AS target
               SET {quote('protocolo_normalizado')} = source.protocolo_normalizado,
                   {quote('status_operacional')} = source.status_operacional,
                   {quote('replicado_em')} = source.replicado_em,
                   {quote('erro_resumido')} = source.erro_resumido
              FROM (VALUES {values_sql}) AS source(
                   id, protocolo_normalizado, status_operacional,
                   replicado_em, erro_resumido
              )
             WHERE target.{quote('id')} = source.id
        """
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                updated += max(0, cursor.rowcount)
    return updated


def _replicados_multi_index(
    report_dates: set[date] | None = None,
) -> dict[tuple[date, str], list[ReplicacaoD1Replicado]]:
    qs: QuerySet[ReplicacaoD1Replicado] = ReplicacaoD1Replicado.objects.all()
    if report_dates is not None:
        qs = qs.filter(report_date__in=report_dates)
    index: dict[tuple[date, str], list[ReplicacaoD1Replicado]] = {}
    for rep in qs.iterator():
        norm = rep.protocolo_origem_normalizado or normalize_protocolo(rep.protocolo_origem)
        key = (rep.report_date, norm)
        index.setdefault(key, []).append(rep)
    return index


def _find_replicado_candidates(
    rep_index: dict[tuple[date, str], list[ReplicacaoD1Replicado]],
    execution_day: date,
    norm: str,
) -> tuple[list[ReplicacaoD1Replicado], str]:
    """Busca a confirmação oficial no eixo do dashboard: execução D, relatório D+1."""
    candidates = rep_index.get((execution_day + timedelta(days=1), norm), [])
    return candidates, ReplicacaoD1Reconciliacao.METHOD_DATE_WINDOW


def reconcile_protocolos_for_run(
    run_id: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Atualiza status_operacional e persiste reconciliação por protocolo."""
    protocolos = list(
        ReplicacaoD1Protocolo.objects.filter(run_id=run_id).select_related("run").only(
            "id",
            "data_referencia_d1",
            "protocolo",
            "protocolo_normalizado",
            "status_brflow",
            "status_operacional",
            "replicado_em",
            "erro_resumido",
            "run__data_execucao",
        )
    )
    if not protocolos:
        return 0

    execution_days = {
        p.run.data_execucao.date()
        for p in protocolos
        if p.run.data_execucao is not None
    }
    lookup_dates = {day + timedelta(days=1) for day in execution_days}
    rep_index = _replicados_multi_index(lookup_dates)
    existing_by_protocol = {
        item.protocolo_id: item
        for item in ReplicacaoD1Reconciliacao.objects.filter(
            protocolo_id__in=[p.id for p in protocolos],
            regra_version=RECONCILIATION_RULE_VERSION,
        ).only(
            "protocolo_id",
            "replicado_id",
            "status",
            "method",
            "motivo",
        )
    }

    protocol_updates = []
    reconciliations = []
    now = timezone.now()
    for prot in protocolos:
        norm = prot.protocolo_normalizado or normalize_protocolo(prot.protocolo)
        execution_day = prot.run.data_execucao.date() if prot.run.data_execucao else None
        candidates, method = (
            _find_replicado_candidates(rep_index, execution_day, norm)
            if execution_day is not None
            else ([], ReplicacaoD1Reconciliacao.METHOD_DATE_WINDOW)
        )

        new_status = _brflow_status_operacional(prot.status_brflow)
        replicado_em = None
        erro = ""
        reconc_status = ReplicacaoD1Reconciliacao.STATUS_MISSING
        reconc_rep: ReplicacaoD1Replicado | None = None
        motivo = ""

        if len(candidates) > 1:
            new_status = STATUS_PENDENTE
            reconc_status = ReplicacaoD1Reconciliacao.STATUS_AMBIGUOUS
            motivo = f"{len(candidates)} confirmações candidatas"
        elif len(candidates) == 1:
            reconc_rep = candidates[0]
            new_status = STATUS_REPLICADO
            reconc_status = ReplicacaoD1Reconciliacao.STATUS_MATCHED
            replicado_em = reconc_rep.data_cadastro_destino or reconc_rep.data_cadastro_origem
        elif new_status == STATUS_FALHOU:
            reconc_status = ReplicacaoD1Reconciliacao.STATUS_IGNORED
            erro = (prot.status_brflow or "Falha BRFlow")[:255]
            motivo = "falha operacional BRFlow"
        elif execution_day is None:
            motivo = "run sem data de execução"
        else:
            motivo = "sem confirmação em D+1 da execução"

        motivo = (motivo or "")[:255]
        existing = existing_by_protocol.get(prot.id)
        replicado_id = reconc_rep.pk if reconc_rep is not None else None
        if (
            existing is None
            or existing.replicado_id != replicado_id
            or existing.status != reconc_status
            or existing.method != method
            or existing.motivo != motivo
        ):
            reconciliations.append(
                ReplicacaoD1Reconciliacao(
                    protocolo=prot,
                    replicado=reconc_rep,
                    status=reconc_status,
                    method=method,
                    motivo=motivo,
                    regra_version=RECONCILIATION_RULE_VERSION,
                    reconciliado_em=now,
                )
            )

        if (
            prot.status_operacional != new_status
            or prot.replicado_em != replicado_em
            or prot.erro_resumido != erro
            or prot.protocolo_normalizado != norm
        ):
            prot.protocolo_normalizado = norm
            prot.status_operacional = new_status
            prot.replicado_em = replicado_em
            prot.erro_resumido = erro
            protocol_updates.append(prot)

    if progress:
        progress(
            f"Persistindo {len(reconciliations)} conciliação(ões) alterada(s) "
            f"e {len(protocol_updates)} protocolo(s)..."
        )
    for start in range(0, len(reconciliations), RECONCILIATION_UPSERT_BATCH_SIZE):
        ReplicacaoD1Reconciliacao.objects.bulk_create(
            reconciliations[start : start + RECONCILIATION_UPSERT_BATCH_SIZE],
            batch_size=RECONCILIATION_UPSERT_BATCH_SIZE,
            update_conflicts=True,
            update_fields=[
                "replicado",
                "status",
                "method",
                "motivo",
                "reconciliado_em",
            ],
            unique_fields=["protocolo", "regra_version"],
        )
    _bulk_update_protocols(protocol_updates)
    return len(protocol_updates)


def reconcile_protocolos_for_dates(
    dates: set[date],
    *,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Reconcilia runs cuja confirmação oficial pertence às datas informadas."""
    execution_days = {d - timedelta(days=1) for d in dates}
    run_ids = (
        ReplicacaoD1Protocolo.objects.filter(run__data_execucao__date__in=execution_days)
        # Limpa Meta.ordering (run/workflow/protocolo). No PostgreSQL, colunas
        # de ORDER BY entram no SELECT DISTINCT e faziam o mesmo run_id ser
        # retornado uma vez por protocolo.
        .order_by()
        .values_list("run_id", flat=True)
        .distinct()
    )
    total = 0
    for rid in run_ids:
        total += reconcile_protocolos_for_run(rid, progress=progress)
    return total


def find_divergent_replicados(
    *,
    data_de: date | None = None,
    data_ate: date | None = None,
) -> list[dict]:
    """Replicados sem protocolo planejado correspondente na mesma data."""
    qs = ReplicacaoD1Replicado.objects.all()
    if data_de:
        qs = qs.filter(report_date__gte=data_de)
    if data_ate:
        qs = qs.filter(report_date__lte=data_ate)

    planned_keys: set[tuple[date, str]] = set()
    prot_qs = ReplicacaoD1Protocolo.objects.all()
    if data_de:
        prot_qs = prot_qs.filter(data_referencia_d1__gte=data_de)
    if data_ate:
        prot_qs = prot_qs.filter(data_referencia_d1__lte=data_ate)
    for p in prot_qs.only("data_referencia_d1", "protocolo_normalizado", "protocolo"):
        norm = p.protocolo_normalizado or normalize_protocolo(p.protocolo)
        planned_keys.add((p.data_referencia_d1, norm))
        planned_keys.add((p.data_referencia_d1 + timedelta(days=1), norm))

    divergentes: list[dict] = []
    for rep in qs.iterator():
        norm = rep.protocolo_origem_normalizado or normalize_protocolo(rep.protocolo_origem)
        key_exact = (rep.report_date, norm)
        key_prev = (rep.report_date - timedelta(days=1), norm)
        if key_exact not in planned_keys and key_prev not in planned_keys:
            divergentes.append(
                {
                    "report_date": rep.report_date.isoformat(),
                    "protocolo_origem": rep.protocolo_origem,
                    "protocolo_normalizado": norm,
                    "workflow_origem": rep.workflow_origem,
                    "status_operacional": STATUS_DIVERGENTE,
                }
            )
    return divergentes
