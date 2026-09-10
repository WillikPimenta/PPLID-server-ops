# -*- coding: utf-8 -*-
"""Lookup de projeção diária a partir de ProjecaoSla (Megazord)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Iterable

from apps.controle_sla.services.sla_eval import expand_dias_token
from apps.dimensoes_processos.models import ProjecaoSla
from apps.monitoramento_sla.services.keys import time_to_seconds


@dataclass(frozen=True)
class ProjecaoDia:
    on_date: date
    id_cliente: int
    id_workflow: int
    id_nh: int
    hora_inicio: time | None
    hora_fim: time | None
    duracao_segundos: int
    sla_segundos: int | None
    sla_ajuste: int | None
    flag_ajuste_sla: float | None
    volume: float | None
    dia_semana: int


def _weekday_megazord(d: date) -> int:
    """0=Segunda … 6=Domingo (igual Python date.weekday())."""
    return d.weekday()


def _duracao_segundos(row: ProjecaoSla) -> int:
    if row.duracao_atendimento is None:
        # fallback: diferença hora_inicio/fim
        a = time_to_seconds(row.hora_inicio)
        b = time_to_seconds(row.hora_fim)
        if a is not None and b is not None and b >= a:
            return int(b - a)
        return 0
    # Catálogo Megazord trata duração como segundos
    return max(0, int(float(row.duracao_atendimento)))


def _covers(row: ProjecaoSla, on_date: date) -> bool:
    if row.data_inicio > on_date:
        return False
    if row.data_fim is not None and row.data_fim < on_date:
        return False
    days = expand_dias_token(row.dias_semana)
    if not days:
        return False
    return _weekday_megazord(on_date) in days


def find_projecao_dia(
    *,
    id_cliente: int,
    id_workflow: int,
    id_nh: int,
    on_date: date,
    rows: Iterable[ProjecaoSla] | None = None,
) -> tuple[ProjecaoDia | None, list[str]]:
    """
    Retorna (projecao, problemas).
    PROJECAO_DUPLICADA se >1 match; PROJECAO_*_NAO_ENCONTRADA se 0.
    """
    if rows is not None and hasattr(rows, "find"):
        return rows.find(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            on_date=on_date,
        )

    problemas: list[str] = []
    if rows is None:
        qs = ProjecaoSla.objects.filter(
            cliente_id=id_cliente,
            workflow_id=id_workflow,
            nivel_hierarquico_id=id_nh,
        )
        candidates = [r for r in qs if _covers(r, on_date)]
    else:
        candidates = [
            r
            for r in rows
            if r.cliente_id == id_cliente
            and r.workflow_id == id_workflow
            and r.nivel_hierarquico_id == id_nh
            and _covers(r, on_date)
        ]

    if not candidates:
        return None, problemas
    if len(candidates) > 1:
        problemas.append("PROJECAO_DUPLICADA")
        # não escolher arbitrariamente — retorna None
        return None, problemas

    row = candidates[0]
    return (
        ProjecaoDia(
            on_date=on_date,
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            hora_inicio=row.hora_inicio,
            hora_fim=row.hora_fim,
            duracao_segundos=_duracao_segundos(row),
            sla_segundos=int(row.sla_segundos) if row.sla_segundos else None,
            sla_ajuste=int(row.sla_ajuste) if row.sla_ajuste else None,
            flag_ajuste_sla=float(row.flag_ajuste_sla) if row.flag_ajuste_sla is not None else None,
            volume=float(row.volume) if row.volume is not None else None,
            dia_semana=_weekday_megazord(on_date),
        ),
        problemas,
    )


def iter_dates(start: date, end: date) -> list[date]:
    if end < start:
        return []
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def load_projecao_rows_for_range(
    *,
    date_min: date,
    date_max: date,
) -> list[ProjecaoSla]:
    return list(
        ProjecaoSla.objects.filter(data_inicio__lte=date_max)
        .filter(models_q_open_or_after(date_min))
        .select_related("cliente", "workflow", "nivel_hierarquico")
    )


def models_q_open_or_after(date_min: date):
    from django.db.models import Q

    return Q(data_fim__isnull=True) | Q(data_fim__gte=date_min)
