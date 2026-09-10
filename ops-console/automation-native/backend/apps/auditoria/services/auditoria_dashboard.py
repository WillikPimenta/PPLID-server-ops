from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaFalha,
)
from apps.auditoria.services.atividade_serialization import serialize_user_display_name
from apps.auditoria.services.atividade_sla import (
    average_sla_seconds,
    compute_sla_seconds,
    format_sla_label,
)
from apps.auditoria.services.qualidade_promocao import auditoria_fraud_tratados_qs


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
        "falhas": 0,
        "falhas_realizadas": 0,
        "falhas_pendentes": 0,
        "_sla_values": [],
    }


def _responsavel_label(
    atividade: AuditoriaAtividade,
    *,
    created_by_cache: dict[object, str],
) -> str:
    if atividade.responsavel_id:
        name = (atividade.responsavel.full_name or "").strip()
        if name:
            return name
    if not atividade.created_by_id:
        return "Sem responsável"
    if atividade.created_by_id not in created_by_cache:
        created_by_cache[atividade.created_by_id] = (
            serialize_user_display_name(atividade.created_by) or "Sem responsável"
        )
    created = created_by_cache[atividade.created_by_id]
    if created:
        return created
    return "Sem responsável"


def build_auditoria_dashboard(*, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    start_dt = _parse_bound(start_date, end=False)
    end_dt = _parse_bound(end_date, end=True)
    now = timezone.now()

    atividades_qs: QuerySet[AuditoriaAtividade] = AuditoriaAtividade.objects.filter(
        tipo=AuditoriaAtividade.TIPO_AUDITORIA
    )
    falhas_qs: QuerySet[AuditoriaFalhaCadastro] = auditoria_fraud_tratados_qs().filter(
        Q(atividade__tipo=AuditoriaAtividade.TIPO_AUDITORIA) | Q(atividade__isnull=True)
    ).select_related("atividade", "atividade__responsavel", "atividade__created_by")
    pendentes_qs: QuerySet[QualidadePendenteAuditoriaFalha] = (
        QualidadePendenteAuditoriaFalha.objects.filter(
            atividade__tipo=AuditoriaAtividade.TIPO_AUDITORIA
        ).select_related("atividade", "atividade__responsavel", "atividade__created_by")
    )

    if start_dt:
        atividades_qs = atividades_qs.filter(created_at__gte=start_dt)
        falhas_qs = falhas_qs.filter(atividade__created_at__gte=start_dt)
        pendentes_qs = pendentes_qs.filter(atividade__created_at__gte=start_dt)
    if end_dt:
        atividades_qs = atividades_qs.filter(created_at__lte=end_dt)
        falhas_qs = falhas_qs.filter(atividade__created_at__lte=end_dt)
        pendentes_qs = pendentes_qs.filter(atividade__created_at__lte=end_dt)

    total_atividades = 0
    atividades_pendentes = 0
    atividades_em_andamento = 0
    atividades_realizadas = 0
    falhas_realizadas = 0
    falhas_pendentes = 0
    falhas_em_analise = 0

    por_cliente_map: dict[str, dict[str, Any]] = defaultdict(_empty_grupo)
    por_responsavel_map: dict[str, dict[str, Any]] = defaultdict(_empty_grupo)
    auditoria_sla_values: list[int] = []
    created_by_cache: dict[object, str] = {}

    for atividade in atividades_qs.select_related("responsavel", "created_by").order_by().iterator():
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
            auditoria_sla_values.append(seconds)

        resp_label = _responsavel_label(atividade, created_by_cache=created_by_cache)
        resp_bucket = por_responsavel_map[resp_label]
        resp_bucket["atividades"] += 1
        if status == AuditoriaAtividade.STATUS_PENDENTE:
            resp_bucket["atividades_pendentes"] += 1
        elif status == AuditoriaAtividade.STATUS_EM_ANDAMENTO:
            resp_bucket["atividades_em_andamento"] += 1
        elif status == AuditoriaAtividade.STATUS_CONCLUIDA:
            resp_bucket["atividades_realizadas"] += 1
        if seconds is not None:
            resp_bucket["_sla_values"].append(seconds)

    for falha in falhas_qs.order_by().iterator():
        falhas_realizadas += 1
        atividade = falha.atividade
        cliente = (
            ((atividade.cliente if atividade else "") or falha.cliente or "").strip() or "Sem cliente"
        )
        bucket = por_cliente_map[cliente]
        bucket["falhas"] += 1
        bucket["falhas_realizadas"] += 1

        resp_label = (
            _responsavel_label(atividade, created_by_cache=created_by_cache)
            if atividade
            else "Sem responsável"
        )
        resp_bucket = por_responsavel_map[resp_label]
        resp_bucket["falhas"] += 1
        resp_bucket["falhas_realizadas"] += 1

    for falha in pendentes_qs.order_by().iterator():
        falhas_pendentes += 1
        atividade = falha.atividade
        if atividade.status == AuditoriaAtividade.STATUS_EM_ANDAMENTO:
            falhas_em_analise += 1
        cliente = (atividade.cliente or "").strip() or "Sem cliente"
        bucket = por_cliente_map[cliente]
        bucket["falhas"] += 1
        bucket["falhas_pendentes"] += 1

        resp_label = _responsavel_label(atividade, created_by_cache=created_by_cache)
        resp_bucket = por_responsavel_map[resp_label]
        resp_bucket["falhas"] += 1
        resp_bucket["falhas_pendentes"] += 1

    total_falhas = falhas_realizadas + falhas_pendentes
    falhas_aguardando = falhas_pendentes - falhas_em_analise
    media_falhas_por_atividade = _avg(total_falhas, total_atividades)
    auditoria_sla = average_sla_seconds(auditoria_sla_values)

    def _build_grupo_rows(grupo_map: dict[str, dict[str, Any]], nome_key: str) -> list[dict[str, Any]]:
        rows = []
        for nome, stats in grupo_map.items():
            sla_values = stats.pop("_sla_values")
            grupo_sla = average_sla_seconds(sla_values)
            rows.append(
                {
                    nome_key: nome,
                    **stats,
                    "pct_realizados": _pct(stats["falhas_realizadas"], stats["falhas"]),
                    "media_falhas_por_atividade": _avg(stats["falhas"], stats["atividades"]),
                    **_sla_payload(grupo_sla),
                }
            )
        rows.sort(
            key=lambda item: (
                -(item["atividades_pendentes"] + item["atividades_em_andamento"]),
                -item["falhas_pendentes"],
                -item["atividades"],
                str(item[nome_key]).lower(),
            )
        )
        return rows

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
            "falhas": {
                "total": total_falhas,
                "realizados": falhas_realizadas,
                "pendentes": falhas_pendentes,
                "aguardando": falhas_aguardando,
                "em_analise": falhas_em_analise,
                "pct_realizados": _pct(falhas_realizadas, total_falhas),
                "media_por_atividade": media_falhas_por_atividade,
            },
            "sla_auditoria": _sla_payload(auditoria_sla),
            "por_cliente": _build_grupo_rows(por_cliente_map, "cliente")[:20],
            "por_responsavel": _build_grupo_rows(por_responsavel_map, "responsavel")[:20],
        },
    }
