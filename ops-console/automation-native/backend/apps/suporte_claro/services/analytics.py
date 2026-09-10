# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from django.db.models import Count, QuerySet

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.jira_copy import _format_sla_labels, compute_sla_info


@dataclass
class BucketStat:
    key: str
    label: str
    count: int
    pct: float


@dataclass
class ReportStats:
    total: int
    by_status: list[BucketStat]
    by_origem: list[BucketStat]
    with_retorno: int
    with_retorno_pct: float
    with_anexos: int
    with_anexos_pct: float
    top_cadastradores: list[BucketStat]
    without_retorno: int = 0
    without_retorno_pct: float = 0.0
    sla_resolved: int = 0
    sla_pending: int = 0
    sla_avg_minutes: int | None = None
    sla_avg_label: str | None = None
    incidentes: int = 0


def _status_count(stats: ReportStats, key: str) -> BucketStat | None:
    return next((b for b in stats.by_status if b.key == key), None)


def build_attention_points(stats: ReportStats) -> list[str]:
    """Pontos de atenção derivados apenas dos dados reais."""
    if stats.total <= 0:
        return []

    points: list[str] = []
    aberto = _status_count(stats, SuporteClaroRegistro.STATUS_ABERTO)
    andamento = _status_count(stats, SuporteClaroRegistro.STATUS_EM_ATENDIMENTO)
    concluido = _status_count(stats, SuporteClaroRegistro.STATUS_CONCLUIDO)

    if aberto and aberto.count > 0:
        points.append(
            f"{aberto.count} demanda(s) ainda nao iniciadas ({aberto.pct:.1f}% do periodo) - "
            "requerem triagem ou encaminhamento."
        )
    if andamento and andamento.count > 0:
        points.append(
            f"{andamento.count} demanda(s) em andamento ({andamento.pct:.1f}%) - "
            "acompanhar evolucao e registro de retorno."
        )
    if stats.without_retorno > 0:
        points.append(
            f"{stats.without_retorno} demanda(s) sem retorno do suporte registrado "
            f"({stats.without_retorno_pct:.1f}%) - validar pendencias de resposta ao cliente."
        )
    if concluido and stats.total > 0 and concluido.pct < 50 and concluido.count > 0:
        points.append(
            f"Taxa de conclusao de {concluido.pct:.1f}% no periodo - "
            "avaliar gargalos no fluxo de atendimento."
        )
    active_origem = [b for b in stats.by_origem if b.count > 0]
    if len(active_origem) >= 2:
        top = max(active_origem, key=lambda b: b.count)
        if top.pct >= 40:
            points.append(
                f"Concentracao de {top.pct:.1f}% das demandas via {top.label} - "
                "verificar capacidade desse canal."
            )
    return points


def build_closing_notes(stats: ReportStats) -> list[str]:
    """Observações finais baseadas nos dados disponíveis."""
    if stats.total <= 0:
        return ["Nenhuma demanda registrada no periodo selecionado."]

    notes: list[str] = []
    aberto = _status_count(stats, SuporteClaroRegistro.STATUS_ABERTO)
    andamento = _status_count(stats, SuporteClaroRegistro.STATUS_EM_ATENDIMENTO)
    concluido = _status_count(stats, SuporteClaroRegistro.STATUS_CONCLUIDO)

    pending = (aberto.count if aberto else 0) + (andamento.count if andamento else 0)
    if pending > 0:
        notes.append(
            f"Priorizar acompanhamento das {pending} demanda(s) em aberto ou em andamento "
            "ate conclusao e registro formal do retorno."
        )
    if concluido and concluido.count > 0:
        notes.append(
            f"{concluido.count} demanda(s) concluidas no periodo - "
            "manter historico atualizado para auditoria e reunioes de status."
        )
    notes.append(
        "Para detalhamento completo, utilize a exportacao em planilha Excel no portal."
    )
    return notes


def build_report_narrative(stats: ReportStats, period_text: str) -> list[str]:
    """Narrativa curta para entrega ao cliente: tom descritivo, sem jargão interno."""
    if stats.total <= 0:
        return [
            f"No período {period_text}, não houve solicitações de suporte registradas."
        ]

    aberto = _status_count(stats, SuporteClaroRegistro.STATUS_ABERTO)
    andamento = _status_count(stats, SuporteClaroRegistro.STATUS_EM_ATENDIMENTO)
    concluido = _status_count(stats, SuporteClaroRegistro.STATUS_CONCLUIDO)
    aberto_n = aberto.count if aberto else 0
    andamento_n = andamento.count if andamento else 0
    concluido_n = concluido.count if concluido else 0
    concluido_pct = concluido.pct if concluido else 0.0
    em_fluxo = aberto_n + andamento_n

    paragraphs: list[str] = []

    # Parágrafo 1: volume e status
    if concluido_n == stats.total:
        fluxo = (
            f"Entre {period_text}, foram registradas {stats.total} demandas de suporte. "
            f"Todas foram concluídas no período ({concluido_pct:.0f}%)."
        )
    elif em_fluxo == stats.total:
        fluxo = (
            f"Entre {period_text}, foram registradas {stats.total} demandas de suporte. "
            f"Todas permanecem em tratativa, sem encerramento no período."
        )
    else:
        fluxo = (
            f"Entre {period_text}, foram registradas {stats.total} demandas de suporte. "
            f"{concluido_n} foram concluídas ({concluido_pct:.0f}%)"
        )
        partes_fluxo = []
        if aberto_n:
            partes_fluxo.append(f"{aberto_n} aguardam início")
        if andamento_n:
            partes_fluxo.append(f"{andamento_n} estão em andamento")
        if partes_fluxo:
            fluxo += f", e {' e '.join(partes_fluxo)}."
        else:
            fluxo += "."
    paragraphs.append(fluxo)

    # Parágrafo 2: SLA e retorno ao cliente
    if stats.sla_resolved and stats.sla_avg_label:
        sla = (
            f"O tempo médio de resposta, da abertura até o retorno ao cliente, "
            f"foi de {stats.sla_avg_label}, com base em {stats.sla_resolved} "
            f"{'caso finalizado' if stats.sla_resolved == 1 else 'casos finalizados'}."
        )
        if stats.sla_pending:
            if stats.sla_pending == 1:
                sla += " Uma demanda segue em acompanhamento, com retorno ainda pendente."
            else:
                sla += (
                    f" {stats.sla_pending} demandas seguem em acompanhamento, "
                    "com retorno ainda pendente."
                )
        elif stats.without_retorno:
            if stats.without_retorno == 1:
                sla += " Uma demanda concluída aguarda retorno informado ao cliente."
            else:
                sla += (
                    f" {stats.without_retorno} demandas concluídas aguardam retorno "
                    "informado ao cliente."
                )
        else:
            sla += " Todas as demandas do período possuem retorno informado ao cliente."
        paragraphs.append(sla)
    elif stats.sla_pending:
        if stats.sla_pending == 1:
            pend = "Uma demanda aguarda retorno ao cliente."
        else:
            pend = f"{stats.sla_pending} demandas aguardam retorno ao cliente."
        paragraphs.append(
            f"Até o momento, nenhuma demanda do período possui retorno informado. {pend}"
        )

    # Parágrafo 3: canais e fechamento neutro
    closing_parts: list[str] = []
    active_origem = [b for b in stats.by_origem if b.count > 0]
    if active_origem:
        if len(active_origem) == 1:
            closing_parts.append(
                f"As solicitações chegaram pelo canal {active_origem[0].label}."
            )
        else:
            ranked = sorted(active_origem, key=lambda b: b.count, reverse=True)
            top = ranked[0]
            if top.pct >= 50:
                closing_parts.append(
                    f"As solicitações chegaram principalmente pelo canal {top.label} "
                    f"({top.count} de {stats.total}, {top.pct:.0f}%)."
                )
            else:
                labels = ", ".join(f"{b.label} ({b.count})" for b in ranked[:3])
                closing_parts.append(f"As solicitações chegaram por {labels}.")

    if em_fluxo:
        if em_fluxo == 1:
            closing_parts.append(
                "A demanda em andamento segue sob tratativa da equipe de suporte."
            )
        else:
            closing_parts.append(
                "As demandas em andamento seguem sob tratativa da equipe de suporte."
            )
    elif stats.without_retorno:
        closing_parts.append(
            "O período será encerrado após a conclusão dos retornos pendentes ao cliente."
        )
    else:
        closing_parts.append(
            "Período encerrado com retorno informado em todas as demandas."
        )

    if closing_parts:
        paragraphs.append(" ".join(closing_parts))

    return paragraphs


def _pct(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count * 100.0 / total, 1)


def _status_buckets(counts: Counter, total: int) -> list[BucketStat]:
    labels = dict(SuporteClaroRegistro.STATUS_CHOICES)
    order = [choice[0] for choice in SuporteClaroRegistro.STATUS_CHOICES]
    return [
        BucketStat(
            key=key,
            label=labels.get(key, key),
            count=counts.get(key, 0),
            pct=_pct(counts.get(key, 0), total),
        )
        for key in order
    ]


def _origem_buckets(counts: Counter, total: int) -> list[BucketStat]:
    labels = dict(SuporteClaroRegistro.ORIGEM_CHOICES)
    order = [choice[0] for choice in SuporteClaroRegistro.ORIGEM_CHOICES]
    buckets = [
        BucketStat(
            key=key,
            label=labels.get(key, key),
            count=counts.get(key, 0),
            pct=_pct(counts.get(key, 0), total),
        )
        for key in order
    ]
    sem_origem = counts.get("", 0) + counts.get(None, 0)
    if sem_origem:
        buckets.append(BucketStat(key="", label="Não informado", count=sem_origem, pct=_pct(sem_origem, total)))
    return buckets


def build_report_stats(qs: QuerySet) -> ReportStats:
    rows = list(
        qs.annotate(anexos_total=Count("anexos"))
        .select_related("created_by")
        .order_by("-received_at", "-id")
    )
    total = len(rows)

    status_counts: Counter = Counter()
    origem_counts: Counter = Counter()
    cadastrador_counts: Counter = Counter()
    with_retorno = 0
    with_anexos = 0
    incidentes = 0
    sla_minutes_list: list[int] = []
    sla_pending = 0

    for row in rows:
        status_counts[row.status] += 1
        origem_counts[row.origem or ""] += 1
        if row.categoria == SuporteClaroRegistro.CATEGORIA_INCIDENTE:
            incidentes += 1
        if (row.avaliacao or "").strip():
            with_retorno += 1
        if row.anexos_total > 0:
            with_anexos += 1
        username = row.created_by.username if row.created_by else "—"
        cadastrador_counts[username] += 1

        sla = compute_sla_info(row)
        if sla["sla_pending"]:
            sla_pending += 1
        elif sla["sla_minutes"] is not None:
            sla_minutes_list.append(sla["sla_minutes"])

    sla_resolved = len(sla_minutes_list)
    sla_avg_minutes = None
    sla_avg_label = None
    if sla_minutes_list:
        sla_avg_minutes = int(sum(sla_minutes_list) / len(sla_minutes_list))
        sla_avg_label, _ = _format_sla_labels(sla_avg_minutes)

    top_cadastradores = [
        BucketStat(key=name, label=name, count=count, pct=_pct(count, total))
        for name, count in cadastrador_counts.most_common(5)
    ]

    return ReportStats(
        total=total,
        by_status=_status_buckets(status_counts, total),
        by_origem=_origem_buckets(origem_counts, total),
        with_retorno=with_retorno,
        with_retorno_pct=_pct(with_retorno, total),
        with_anexos=with_anexos,
        with_anexos_pct=_pct(with_anexos, total),
        top_cadastradores=top_cadastradores,
        without_retorno=total - with_retorno,
        without_retorno_pct=_pct(total - with_retorno, total),
        sla_resolved=sla_resolved,
        sla_pending=sla_pending,
        sla_avg_minutes=sla_avg_minutes,
        sla_avg_label=sla_avg_label,
        incidentes=incidentes,
    )


def serialize_report_stats(stats: ReportStats) -> dict:
    def bucket_list(items: list[BucketStat]) -> list[dict]:
        return [{"key": b.key, "label": b.label, "count": b.count, "pct": b.pct} for b in items]

    return {
        "total": stats.total,
        "by_status": bucket_list(stats.by_status),
        "by_origem": bucket_list(stats.by_origem),
        "with_retorno": stats.with_retorno,
        "with_retorno_pct": stats.with_retorno_pct,
        "with_anexos": stats.with_anexos,
        "with_anexos_pct": stats.with_anexos_pct,
        "without_retorno": stats.without_retorno,
        "without_retorno_pct": stats.without_retorno_pct,
        "sla_resolved": stats.sla_resolved,
        "sla_pending": stats.sla_pending,
        "sla_avg_minutes": stats.sla_avg_minutes,
        "sla_avg_label": stats.sla_avg_label,
        "top_cadastradores": bucket_list(stats.top_cadastradores),
        "incidentes": stats.incidentes,
    }
