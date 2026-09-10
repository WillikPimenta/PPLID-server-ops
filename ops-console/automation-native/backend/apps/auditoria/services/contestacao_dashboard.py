from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaControleRegistro,
)
from apps.auditoria.services.atividade_sla import (
    average_sla_seconds,
    compute_controle_sla_seconds,
    compute_sla_seconds,
    format_sla_label,
)
CONTROLE_TIPO_LABELS = {
    AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA: "Base negativa",
    AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA: "Base positiva",
    AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO: "IDAS e BIO",
}


def _parse_bound(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    parsed = parse_date(value.strip())
    if not parsed:
        return None
    if end:
        return timezone.make_aware(datetime.combine(parsed, datetime.max.time().replace(microsecond=0)))
    return timezone.make_aware(datetime.combine(parsed, datetime.min.time()))


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 1)


def _avg(part: int | float, total: int) -> int | None:
    if total <= 0:
        return None
    return int(round(part / total))


def _sla_payload(seconds: int | None) -> dict[str, Any]:
    return {
        "sla_medio_segundos": seconds,
        "sla_medio_label": format_sla_label(seconds),
    }


def _empty_grupo() -> dict[str, Any]:
    return {
        "atividades": 0,
        "atividades_pendentes": 0,
        "atividades_em_andamento": 0,
        "atividades_realizadas": 0,
        "protocolos": 0,
        "protocolos_realizados": 0,
        "protocolos_pendentes": 0,
        "protocolos_procedencia": 0,
        "_sla_values": [],
    }


def build_contestacao_dashboard(*, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    start_dt = _parse_bound(start_date, end=False)
    end_dt = _parse_bound(end_date, end=True)
    now = timezone.now()

    atividades_qs: QuerySet[AuditoriaAtividade] = AuditoriaAtividade.objects.filter(
        tipo=AuditoriaAtividade.TIPO_CONTESTACAO
    )
    # Atividade e protocolos são o histórico permanente do lote.
    protocolos_qs: QuerySet[AuditoriaAtividadeProtocolo] = AuditoriaAtividadeProtocolo.objects.filter(
        atividade__tipo=AuditoriaAtividade.TIPO_CONTESTACAO
    ).select_related("atividade", "atividade__responsavel")
    controles_qs: QuerySet[AuditoriaControleRegistro] = AuditoriaControleRegistro.objects.all()

    if start_dt:
        atividades_qs = atividades_qs.filter(created_at__gte=start_dt)
        protocolos_qs = protocolos_qs.filter(atividade__created_at__gte=start_dt)
        controles_qs = controles_qs.filter(created_at__gte=start_dt)
    if end_dt:
        atividades_qs = atividades_qs.filter(created_at__lte=end_dt)
        protocolos_qs = protocolos_qs.filter(atividade__created_at__lte=end_dt)
        controles_qs = controles_qs.filter(created_at__lte=end_dt)

    total_atividades = 0
    atividades_pendentes = 0
    atividades_em_andamento = 0
    atividades_realizadas = 0
    total_protocolos = 0
    protocolos_realizados = 0
    protocolos_aguardando = 0
    protocolos_em_analise = 0
    protocolos_procedencia = 0

    controle_por_tipo_situacao: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    controle_sla_by_tipo: dict[str, list[int]] = defaultdict(list)
    for controle in controles_qs.only("tipo", "situacao", "dados").order_by().iterator():
        situacao = (controle.situacao or "").strip() or "Sem situação"
        controle_por_tipo_situacao[controle.tipo][situacao] += 1
        seconds = compute_controle_sla_seconds(controle.dados, situacao=controle.situacao or "", now=now)
        if seconds is not None:
            controle_sla_by_tipo[controle.tipo].append(seconds)

    def _controle_tipo_counts(tipo: str) -> tuple[int, int, int]:
        situacoes = controle_por_tipo_situacao.get(tipo, {})
        total_tipo = sum(situacoes.values())
        finalizadas = sum(count for sit, count in situacoes.items() if sit.casefold() == "finalizada")
        return total_tipo, total_tipo - finalizadas, finalizadas

    neg_total, neg_abertas, neg_finalizadas = _controle_tipo_counts(
        AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA
    )
    pos_total, pos_abertas, pos_finalizadas = _controle_tipo_counts(
        AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA
    )
    idas_total, idas_abertas, idas_finalizadas = _controle_tipo_counts(
        AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO
    )

    neg_sla = average_sla_seconds(
        controle_sla_by_tipo[AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA]
    )
    pos_sla = average_sla_seconds(
        controle_sla_by_tipo[AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA]
    )
    idas_sla = average_sla_seconds(
        controle_sla_by_tipo[AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO]
    )
    remocoes_sla = average_sla_seconds(
        controle_sla_by_tipo[AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA]
        + controle_sla_by_tipo[AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA]
    )

    por_cliente_map: dict[str, dict[str, Any]] = defaultdict(_empty_grupo)
    por_responsavel_map: dict[str, dict[str, Any]] = defaultdict(_empty_grupo)
    contestacao_sla_values: list[int] = []
    for atividade in atividades_qs.select_related("responsavel").order_by().iterator():
        cliente = (atividade.cliente or "").strip() or "Sem cliente"
        status = atividade.status or AuditoriaAtividade.STATUS_PENDENTE
        total_atividades += 1
        if status == AuditoriaAtividade.STATUS_PENDENTE:
            atividades_pendentes += 1
        elif status == AuditoriaAtividade.STATUS_EM_ANDAMENTO:
            atividades_em_andamento += 1
        elif status == AuditoriaAtividade.STATUS_CONCLUIDA:
            atividades_realizadas += 1
        bucket = por_cliente_map[cliente]
        bucket["atividades"] += 1
        if status == AuditoriaAtividade.STATUS_PENDENTE:
            bucket["atividades_pendentes"] += 1
        elif status == AuditoriaAtividade.STATUS_EM_ANDAMENTO:
            bucket["atividades_em_andamento"] += 1
        elif status == AuditoriaAtividade.STATUS_CONCLUIDA:
            bucket["atividades_realizadas"] += 1
        seconds = compute_sla_seconds(atividade, now=now)
        if seconds is not None:
            bucket["_sla_values"].append(seconds)
            contestacao_sla_values.append(seconds)

        responsavel_label = (
            (atividade.responsavel.full_name or "").strip() if atividade.responsavel_id else ""
        ) or "Sem responsável"
        resp_bucket = por_responsavel_map[responsavel_label]
        resp_bucket["atividades"] += 1
        if status == AuditoriaAtividade.STATUS_PENDENTE:
            resp_bucket["atividades_pendentes"] += 1
        elif status == AuditoriaAtividade.STATUS_EM_ANDAMENTO:
            resp_bucket["atividades_em_andamento"] += 1
        elif status == AuditoriaAtividade.STATUS_CONCLUIDA:
            resp_bucket["atividades_realizadas"] += 1
        if seconds is not None:
            resp_bucket["_sla_values"].append(seconds)

    def _bump_protocolo_stats(
        *,
        cliente: str,
        responsavel_label: str,
        realizado: bool,
        procedente: bool,
    ) -> None:
        for bucket in (por_cliente_map[cliente], por_responsavel_map[responsavel_label]):
            bucket["protocolos"] += 1
            if realizado:
                bucket["protocolos_realizados"] += 1
            else:
                bucket["protocolos_pendentes"] += 1
            if procedente:
                bucket["protocolos_procedencia"] += 1

    for protocolo in protocolos_qs.order_by().iterator():
        cliente = (protocolo.atividade.cliente or "").strip() or "Sem cliente"
        responsavel_label = (
            (protocolo.atividade.responsavel.full_name or "").strip()
            if protocolo.atividade.responsavel_id
            else ""
        ) or "Sem responsável"
        realizado = protocolo.status == AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
        procedente = protocolo.situacao == AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
        total_protocolos += 1
        if realizado:
            protocolos_realizados += 1
        elif protocolo.status == AuditoriaAtividadeProtocolo.STATUS_EM_ANDAMENTO:
            protocolos_em_analise += 1
        else:
            protocolos_aguardando += 1
        if procedente:
            protocolos_procedencia += 1
        _bump_protocolo_stats(
            cliente=cliente,
            responsavel_label=responsavel_label,
            realizado=realizado,
            procedente=procedente,
        )

    protocolos_pendentes = protocolos_aguardando + protocolos_em_analise
    media_protocolos_por_atividade = _avg(total_protocolos, total_atividades)
    pct_procedencia = _pct(protocolos_procedencia, total_protocolos)
    contestacao_sla = average_sla_seconds(contestacao_sla_values)

    def _build_grupo_rows(grupo_map: dict[str, dict[str, Any]], nome_key: str) -> list[dict[str, Any]]:
        rows = []
        for nome, stats in grupo_map.items():
            sla_values = stats.pop("_sla_values")
            grupo_sla = average_sla_seconds(sla_values)
            rows.append(
                {
                    nome_key: nome,
                    **stats,
                    "pct_realizados": _pct(stats["protocolos_realizados"], stats["protocolos"]),
                    "media_protocolos_por_atividade": _avg(stats["protocolos"], stats["atividades"]),
                    "pct_procedencia": _pct(stats["protocolos_procedencia"], stats["protocolos"]),
                    **_sla_payload(grupo_sla),
                }
            )
        rows.sort(
            key=lambda item: (
                -(item["atividades_pendentes"] + item["atividades_em_andamento"]),
                -item["protocolos_pendentes"],
                -item["atividades"],
                str(item[nome_key]).lower(),
            )
        )
        return rows

    por_cliente = _build_grupo_rows(por_cliente_map, "cliente")
    por_responsavel = _build_grupo_rows(por_responsavel_map, "responsavel")

    return {
        "periodo": {
            "start_date": start_date or None,
            "end_date": end_date or None,
        },
        "entrega": {
            "atividades": {
                "total": total_atividades,
                "pendentes": atividades_pendentes,
                "em_andamento": atividades_em_andamento,
                "realizadas": atividades_realizadas,
            },
            "protocolos": {
                "total": total_protocolos,
                "realizados": protocolos_realizados,
                "pendentes": protocolos_pendentes,
                "aguardando": protocolos_aguardando,
                "em_analise": protocolos_em_analise,
                "pct_realizados": _pct(protocolos_realizados, total_protocolos),
                "procedencia": protocolos_procedencia,
                "media_por_atividade": media_protocolos_por_atividade,
                "pct_procedencia": pct_procedencia,
            },
            "sla_contestacao": _sla_payload(contestacao_sla),
            "remocoes": {
                "em_andamento": neg_abertas + pos_abertas,
                "finalizadas": neg_finalizadas + pos_finalizadas,
                **_sla_payload(remocoes_sla),
                "base_negativa": {
                    "label": CONTROLE_TIPO_LABELS[AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA],
                    "total": neg_total,
                    "em_andamento": neg_abertas,
                    "finalizadas": neg_finalizadas,
                    **_sla_payload(neg_sla),
                },
                "base_positiva": {
                    "label": CONTROLE_TIPO_LABELS[AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA],
                    "total": pos_total,
                    "em_andamento": pos_abertas,
                    "finalizadas": pos_finalizadas,
                    **_sla_payload(pos_sla),
                },
            },
            "solicitacoes": {
                "label": CONTROLE_TIPO_LABELS[AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO],
                "total": idas_total,
                "em_andamento": idas_abertas,
                "finalizadas": idas_finalizadas,
                **_sla_payload(idas_sla),
            },
            "por_cliente": por_cliente[:20],
            "por_responsavel": por_responsavel[:20],
        },
    }
