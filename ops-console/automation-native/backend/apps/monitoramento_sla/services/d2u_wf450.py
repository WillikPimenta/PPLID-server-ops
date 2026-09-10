# -*- coding: utf-8 -*-
"""Regra D+2 dias úteis — workflow 450 (§12)."""
from __future__ import annotations

from datetime import date, timedelta

from apps.monitoramento_sla import flags
from apps.monitoramento_sla.services.projecao_lookup import find_projecao_dia, iter_dates


def _is_useful_day(proj) -> bool:
    return (
        proj is not None
        and 0 <= proj.dia_semana <= 4
        and proj.duracao_segundos > 0
    )


def calcular_vencimento_d2u(
    *,
    id_cliente: int,
    id_workflow: int,
    id_nh: int,
    data_cadastro: date,
    proj_rows=None,
    horizon_days: int = 60,
) -> tuple[date | None, list[str]]:
    if hasattr(proj_rows, "d2u_deadline"):
        return proj_rows.d2u_deadline(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            data_cadastro=data_cadastro,
            horizon_days=horizon_days,
        )
    problemas: list[str] = []
    # DataInicioReal: primeira data >= cadastro com dia útil e duração > 0
    data_inicio_real: date | None = None
    for d in iter_dates(data_cadastro, data_cadastro + timedelta(days=horizon_days)):
        proj, probs = find_projecao_dia(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            on_date=d,
            rows=proj_rows,
        )
        problemas.extend(probs)
        if _is_useful_day(proj):
            data_inicio_real = d
            break
    if data_inicio_real is None:
        problemas.append("VENCIMENTO_D2U_NAO_ENCONTRADO")
        return None, problemas

    futuros: list[date] = []
    for d in iter_dates(
        data_inicio_real + timedelta(days=1),
        data_inicio_real + timedelta(days=horizon_days),
    ):
        proj, probs = find_projecao_dia(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            on_date=d,
            rows=proj_rows,
        )
        problemas.extend(probs)
        if _is_useful_day(proj):
            futuros.append(d)
        if len(futuros) >= 2:
            break
    if len(futuros) < 2:
        problemas.append("VENCIMENTO_D2U_NAO_ENCONTRADO")
        return None, problemas
    return max(futuros[0], futuros[1]), problemas


def classificar_d2u(
    *,
    data_referencia: date,
    vencimento: date | None,
) -> str:
    if vencimento is None:
        return "Dentro"  # parâmetros ausentes → Dentro (modelo atual)
    return "Dentro" if data_referencia <= vencimento else "Fora"


def aplica_regra_wf450(id_workflow: int | None, data_cadastro: date) -> bool:
    if id_workflow != flags.WF450_ID:
        return False
    from datetime import date as date_cls

    limite = date_cls.fromisoformat(flags.WF450_D2U_FROM)
    return data_cadastro >= limite
