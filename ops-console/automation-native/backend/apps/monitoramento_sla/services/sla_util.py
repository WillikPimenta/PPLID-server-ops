# -*- coding: utf-8 -*-
"""Cálculo de SLA útil em segundos (§10)."""
from __future__ import annotations

from datetime import date, time, timedelta

from apps.monitoramento_sla.services.keys import time_to_seconds
from apps.monitoramento_sla.services.projecao_lookup import (
    ProjecaoDia,
    find_projecao_dia,
    iter_dates,
)


def _first_day_seconds(
    *,
    data_cad: date,
    hora_cad: time | None,
    data_fim: date,
    hora_fim: time | None,
    proj_cad: ProjecaoDia | None,
) -> int:
    if proj_cad is None:
        return 0
    inicio = time_to_seconds(proj_cad.hora_inicio)
    fim = time_to_seconds(proj_cad.hora_fim)
    if inicio is None or fim is None:
        return 0
    cad_sec = time_to_seconds(hora_cad) if hora_cad else inicio
    # StartFirst = min(FimCad, max(InicioCad, HoraCad))
    start_first = min(fim, max(inicio, cad_sec or inicio))
    diff_dias = (data_fim - data_cad).days
    if diff_dias == 0:
        conc_sec = time_to_seconds(hora_fim) if hora_fim else fim
        end_first = min(fim, max(inicio, conc_sec or inicio))
    else:
        end_first = fim
    return max(0, end_first - start_first)


def _last_day_seconds(
    *,
    data_cad: date,
    data_fim: date,
    hora_fim: time | None,
    proj_conc: ProjecaoDia | None,
) -> int:
    if (data_fim - data_cad).days < 1 or proj_conc is None:
        return 0
    inicio = time_to_seconds(proj_conc.hora_inicio)
    fim = time_to_seconds(proj_conc.hora_fim)
    if inicio is None or fim is None:
        return 0
    conc_sec = time_to_seconds(hora_fim) if hora_fim else fim
    end_last = min(fim, max(inicio, conc_sec or inicio))
    return max(0, end_last - inicio)


def _middle_seconds(
    *,
    id_cliente: int,
    id_workflow: int,
    id_nh: int,
    data_cad: date,
    data_fim: date,
    proj_rows,
) -> tuple[int, list[str]]:
    problemas: list[str] = []
    total = 0
    middle_start = data_cad + timedelta(days=1)
    middle_end = data_fim - timedelta(days=1)
    if hasattr(proj_rows, "sum_duration"):
        return proj_rows.sum_duration(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            start=middle_start,
            end=middle_end,
        )
    # dias estritamente entre cadastro e conclusão
    for d in iter_dates(middle_start, middle_end):
        proj, probs = find_projecao_dia(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            on_date=d,
            rows=proj_rows,
        )
        problemas.extend(probs)
        if proj is not None and proj.duracao_segundos > 0:
            total += proj.duracao_segundos
    return total, problemas


def calcular_sla_util_segundos(
    *,
    id_cliente: int,
    id_workflow: int,
    id_nh: int,
    data_cadastro: date,
    hora_cadastro: time | None,
    data_fim: date,
    hora_fim: time | None,
    proj_rows=None,
) -> tuple[int | None, list[str]]:
    """
    Retorna (sla_segundos, problemas).
    Se DiffDias < 0 → 0 + DATA_CONCLUSAO_ANTERIOR_CADASTRO.
    """
    problemas: list[str] = []
    diff = (data_fim - data_cadastro).days
    if diff < 0:
        problemas.append("DATA_CONCLUSAO_ANTERIOR_CADASTRO")
        return 0, problemas

    proj_cad, p1 = find_projecao_dia(
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        on_date=data_cadastro,
        rows=proj_rows,
    )
    problemas.extend(p1)
    if proj_cad is None and "PROJECAO_DUPLICADA" not in p1:
        problemas.append("PROJECAO_CADASTRO_NAO_ENCONTRADA")

    proj_conc = None
    if diff >= 1:
        proj_conc, p2 = find_projecao_dia(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            on_date=data_fim,
            rows=proj_rows,
        )
        problemas.extend(p2)
        if proj_conc is None and "PROJECAO_DUPLICADA" not in p2:
            problemas.append("PROJECAO_CONCLUSAO_NAO_ENCONTRADA")

    sec_first = _first_day_seconds(
        data_cad=data_cadastro,
        hora_cad=hora_cadastro,
        data_fim=data_fim,
        hora_fim=hora_fim,
        proj_cad=proj_cad,
    )
    sec_mid, p_mid = _middle_seconds(
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        data_cad=data_cadastro,
        data_fim=data_fim,
        proj_rows=proj_rows,
    )
    problemas.extend(p_mid)
    sec_last = _last_day_seconds(
        data_cad=data_cadastro,
        data_fim=data_fim,
        hora_fim=hora_fim,
        proj_conc=proj_conc,
    )
    total = int(max(0, sec_first + sec_mid + sec_last))
    return total, problemas
