# -*- coding: utf-8 -*-
"""Classificação natural e ajustada (§11–14)."""
from __future__ import annotations

from datetime import date

from apps.monitoramento_sla import flags
from apps.monitoramento_sla.services.d2u_wf450 import (
    aplica_regra_wf450,
    calcular_vencimento_d2u,
    classificar_d2u,
)
from apps.monitoramento_sla.services.projecao_lookup import ProjecaoDia


DENTRO = "Dentro"
FORA = "Fora"


def classificar_natural_padrao(
    *,
    sla_segundos: int | None,
    proj_cadastro: ProjecaoDia | None,
    em_aberto: bool,
) -> tuple[str, list[str]]:
    problemas: list[str] = []
    if em_aberto and flags.OPEN_DEFAULT_DENTRO:
        return DENTRO, problemas
    if proj_cadastro is None or sla_segundos is None:
        problemas.append("PARAMETROS_SLA_INCOMPLETOS")
        return DENTRO, problemas
    limite = proj_cadastro.sla_segundos
    if limite is None:
        problemas.append("PARAMETROS_SLA_INCOMPLETOS")
        return DENTRO, problemas
    if sla_segundos <= limite:
        return DENTRO, problemas
    return FORA, problemas


def classificar_natural(
    *,
    id_workflow: int | None,
    data_cadastro: date,
    data_referencia: date,
    sla_segundos: int | None,
    proj_cadastro: ProjecaoDia | None,
    em_aberto: bool,
    id_cliente: int | None,
    id_nh: int | None,
    proj_rows=None,
) -> tuple[str, date | None, list[str]]:
    problemas: list[str] = []
    vencimento: date | None = None
    if (
        id_workflow is not None
        and id_cliente is not None
        and id_nh is not None
        and aplica_regra_wf450(id_workflow, data_cadastro)
    ):
        vencimento, p = calcular_vencimento_d2u(
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_nh=id_nh,
            data_cadastro=data_cadastro,
            proj_rows=proj_rows,
        )
        problemas.extend(p)
        return (
            classificar_d2u(data_referencia=data_referencia, vencimento=vencimento),
            vencimento,
            problemas,
        )

    nat, p2 = classificar_natural_padrao(
        sla_segundos=sla_segundos,
        proj_cadastro=proj_cadastro,
        em_aberto=em_aberto,
    )
    problemas.extend(p2)
    return nat, vencimento, problemas


def classificar_ajustado(
    *,
    sla_natural: str,
    sla_segundos: int | None,
    proj_cadastro: ProjecaoDia | None,
    rank_protocolo: int | None,
) -> str:
    if sla_natural == DENTRO:
        return DENTRO
    if proj_cadastro is None or sla_segundos is None:
        return DENTRO

    if proj_cadastro.sla_ajuste is not None:
        if flags.SLA_AJUSTE_STRICT_LT:
            if sla_segundos < proj_cadastro.sla_ajuste:
                return DENTRO
        elif sla_segundos <= proj_cadastro.sla_ajuste:
            return DENTRO

    volume = proj_cadastro.volume
    if volume is not None and rank_protocolo is not None:
        if flags.RANK_VOLUME_OP == ">=":
            if rank_protocolo >= volume:
                return DENTRO
        else:
            if rank_protocolo <= volume:
                return DENTRO

    return FORA
