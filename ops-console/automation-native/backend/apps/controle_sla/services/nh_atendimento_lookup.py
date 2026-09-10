# -*- coding: utf-8 -*-
"""Resolve NH de atendimento a partir de prioridades_nh_fluxo (sem persistir no breach)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

# Prioridade ≤ 0 = sem prioridade efetiva → fim da fila do NH.
_UNSET_PRIORITY = 0


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _effective_priority(prio: int) -> int:
    """Maior número positivo = mais prioritário; ≤0 vai para o fim da fila."""
    return prio if prio > 0 else _UNSET_PRIORITY


def _fila_a_frente(prio: int, items_in_nh: list[tuple[int, int]]) -> int:
    """
    Soma das filas (qtd_fila) das etapas à frente neste NH.
    À frente = prioridade efetiva maior que a da etapa alvo.
    """
    mine = _effective_priority(prio)
    return sum(
        fila for other_prio, fila in items_in_nh if _effective_priority(other_prio) > mine
    )


def _lookup_key(
    *,
    cod_cliente: int | None,
    id_workflow: int | None,
    nom_cliente: str,
    nom_workflow: str,
    nom_fluxo: str,
) -> tuple:
    return (
        int(cod_cliente) if cod_cliente is not None else None,
        int(id_workflow) if id_workflow is not None else None,
        _norm(nom_cliente),
        _norm(nom_workflow),
        _norm(nom_fluxo),
    )


def _row_keys(row: Any) -> list[tuple]:
    return [
        _lookup_key(
            cod_cliente=row.prk_cliente,
            id_workflow=row.prk_workflow,
            nom_cliente="",
            nom_workflow="",
            nom_fluxo=row.nom_fluxo,
        ),
        _lookup_key(
            cod_cliente=row.prk_cliente,
            id_workflow=None,
            nom_cliente="",
            nom_workflow=row.nom_workflow,
            nom_fluxo=row.nom_fluxo,
        ),
        _lookup_key(
            cod_cliente=None,
            id_workflow=row.prk_workflow,
            nom_cliente=row.nom_cliente,
            nom_workflow="",
            nom_fluxo=row.nom_fluxo,
        ),
        _lookup_key(
            cod_cliente=None,
            id_workflow=None,
            nom_cliente=row.nom_cliente,
            nom_workflow=row.nom_workflow,
            nom_fluxo=row.nom_fluxo,
        ),
    ]


def _build_fila_lookup() -> tuple[dict[tuple[int, int, str], int], dict[tuple[int, str], int]]:
    """Filas do acompanhamento ao vivo (coluna Fila), indexadas para match com Megazord."""
    from apps.controle_sla.models import SlaBreach

    by_cwf: dict[tuple[int, int, str], int] = {}
    by_cf: dict[tuple[int, str], int] = {}
    for row in SlaBreach.objects.all().only(
        "cod_cliente", "id_workflow", "nom_fluxo", "qtd_fila"
    ):
        fluxo = _norm(row.nom_fluxo)
        if not fluxo:
            continue
        fila = int(row.qtd_fila or 0)
        if row.id_workflow is not None:
            by_cwf[(int(row.cod_cliente), int(row.id_workflow), fluxo)] = fila
        by_cf[(int(row.cod_cliente), fluxo)] = fila
    return by_cwf, by_cf


def _fila_for_prio_row(
    row: Any,
    by_cwf: dict[tuple[int, int, str], int],
    by_cf: dict[tuple[int, str], int],
) -> int:
    fluxo = _norm(row.nom_fluxo)
    if not fluxo:
        return 0
    cliente = int(row.prk_cliente)
    workflow = int(row.prk_workflow) if row.prk_workflow is not None else None
    if workflow is not None:
        hit = by_cwf.get((cliente, workflow, fluxo))
        if hit is not None:
            return hit
    return by_cf.get((cliente, fluxo), 0)


def build_nh_atendimento_index() -> dict[tuple, tuple[str, int, int]]:
    """
    Índice (cliente, workflow, etapa) → (nom_nh, prioridade, fila_a_frente).

    Escolhe o NH em que a soma das filas das etapas à frente é menor.
    Prioridade maior = melhor; ≤0 conta como fim de fila.
    Empate de fila à frente: maior prioridade efetiva, depois nome.
    """
    from apps.prioridades_nh.models import NhPrioridadeFluxo

    rows = list(
        NhPrioridadeFluxo.objects.all().only(
            "prk_cliente",
            "nom_cliente",
            "prk_workflow",
            "nom_workflow",
            "prk_nivel_hierarquico",
            "nom_nivel_hierarquico",
            "prk_fluxo",
            "nom_fluxo",
            "num_prioridade_fluxo",
        )
    )
    by_cwf, by_cf = _build_fila_lookup()

    # NH → lista de (prioridade, fila)
    items_by_nh: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in rows:
        nh_id = int(row.prk_nivel_hierarquico)
        prio = int(row.num_prioridade_fluxo or 0)
        fila = _fila_for_prio_row(row, by_cwf, by_cf)
        items_by_nh[nh_id].append((prio, fila))

    # key → (ahead_fila_sum, effective_prio, raw_prio, nh_name)
    best: dict[tuple, tuple[int, int, int, str]] = {}
    for row in rows:
        nh_name = (row.nom_nivel_hierarquico or "").strip()
        if not nh_name:
            continue
        nh_id = int(row.prk_nivel_hierarquico)
        prio = int(row.num_prioridade_fluxo or 0)
        ahead = _fila_a_frente(prio, items_by_nh[nh_id])
        eff = _effective_priority(prio)
        for key in _row_keys(row):
            if not key[4]:
                continue
            prev = best.get(key)
            if (
                prev is None
                or ahead < prev[0]
                or (ahead == prev[0] and eff > prev[1])
                or (ahead == prev[0] and eff == prev[1] and nh_name < prev[3])
            ):
                best[key] = (ahead, eff, prio, nh_name)

    return {k: (v[3], v[2], v[0]) for k, v in best.items()}


def resolve_nh_atendimento(
    *,
    cod_cliente: int | None,
    id_workflow: int | None,
    nom_cliente: str = "",
    nom_workflow: str = "",
    nom_fluxo: str = "",
    index: dict[tuple, tuple[str, int, int]] | dict[tuple, str] | None = None,
) -> str:
    detail = resolve_nh_atendimento_detail(
        cod_cliente=cod_cliente,
        id_workflow=id_workflow,
        nom_cliente=nom_cliente,
        nom_workflow=nom_workflow,
        nom_fluxo=nom_fluxo,
        index=index,
    )
    return detail["nh_atendimento"]


def resolve_nh_atendimento_detail(
    *,
    cod_cliente: int | None,
    id_workflow: int | None,
    nom_cliente: str = "",
    nom_workflow: str = "",
    nom_fluxo: str = "",
    index: dict[tuple, tuple[str, int, int]] | dict[tuple, str] | None = None,
) -> dict[str, Any]:
    """Retorna NH + prioridade + soma das filas à frente (para UI)."""
    raw_idx = index if index is not None else build_nh_atendimento_index()
    fluxo = _norm(nom_fluxo)
    empty = {"nh_atendimento": "", "nh_prioridade": None, "nh_etapas_a_frente": None}
    if not fluxo:
        return empty

    candidates = [
        _lookup_key(
            cod_cliente=cod_cliente,
            id_workflow=id_workflow,
            nom_cliente="",
            nom_workflow="",
            nom_fluxo=nom_fluxo,
        ),
        _lookup_key(
            cod_cliente=cod_cliente,
            id_workflow=None,
            nom_cliente="",
            nom_workflow=nom_workflow,
            nom_fluxo=nom_fluxo,
        ),
        _lookup_key(
            cod_cliente=None,
            id_workflow=id_workflow,
            nom_cliente=nom_cliente,
            nom_workflow="",
            nom_fluxo=nom_fluxo,
        ),
        _lookup_key(
            cod_cliente=None,
            id_workflow=None,
            nom_cliente=nom_cliente,
            nom_workflow=nom_workflow,
            nom_fluxo=nom_fluxo,
        ),
    ]
    for key in candidates:
        hit = raw_idx.get(key)
        if not hit:
            continue
        # Compat: índice antigo só com nome do NH.
        if isinstance(hit, str):
            return {"nh_atendimento": hit, "nh_prioridade": None, "nh_etapas_a_frente": None}
        nh_name, prio, ahead = hit
        return {
            "nh_atendimento": nh_name,
            "nh_prioridade": prio,
            "nh_etapas_a_frente": ahead,
        }
    return empty
