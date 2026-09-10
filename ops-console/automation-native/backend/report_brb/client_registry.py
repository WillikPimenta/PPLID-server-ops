# -*- coding: utf-8 -*-
"""Registry de clientes para Quality Pulse / relatório executivo."""
from __future__ import annotations

from typing import Any

_DEFAULT_CORES = {
    "primary": "#174e97",
    "dark": "#0b2a59",
    "green": "#15803d",
    "red": "#b91c1c",
    "gray": "#64748b",
    "border": "#e2e8f0",
    "bg": "#f1f5f9",
}

_DEFAULT_CS_GOALS = {
    "confirmed_rate": 5.0,
    "aged_over_90_pct": 10.0,
    "cause_concentration_pct": 35.0,
}

CLIENTS: dict[str, dict[str, Any]] = {
    "brb": {
        "slug": "brb",
        "nome": "BRB Banco de Brasília",
        "nome_curto": "BRB",
        "filtro": "BRB",
        "id_cliente": 35,
        "enabled": True,
        "aliases": (
            "BRB",
            "BANCO DE BRASILIA",
            "BANCO DE BRASÍLIA",
            "BRB DTVM",
            "BRB CFI",
            "BRB CREDITO FINANCIAMENTO E INVESTIMENTO",
        ),
        "fg_sheet": "Falhas_Gerais-BRB",
        "cs_goals": {**_DEFAULT_CS_GOALS},
        "cores": {**_DEFAULT_CORES, "primary": "#174e97", "dark": "#0b2a59"},
    },
    "claro": {
        "slug": "claro",
        "nome": "Claro — Brsafe",
        "nome_curto": "Claro",
        "filtro": "CLARO",
        "id_cliente": 186,
        "enabled": True,
        "aliases": (
            "CLARO",
            "BRSAFE",
            "CLARO - BRSAFE",
            "CLARO BRSAFE",
        ),
        "fg_sheet": "Falhas_Gerais-BRB",
        "cs_goals": {**_DEFAULT_CS_GOALS},
        "cores": {**_DEFAULT_CORES, "primary": "#da291c", "dark": "#8b0000"},
    },
    "bmg": {
        "slug": "bmg",
        "nome": "Banco BMG",
        "nome_curto": "BMG",
        "filtro": "BMG",
        "id_cliente": 7,
        "enabled": True,
        "aliases": (
            "BANCO BMG",
            "BMG",
        ),
        "fg_sheet": "Falhas_Gerais-BRB",
        "cs_goals": {**_DEFAULT_CS_GOALS},
        "cores": {**_DEFAULT_CORES, "primary": "#f68b1f", "dark": "#c45c00"},
    },
    "picpay": {
        "slug": "picpay",
        "nome": "PicPay",
        "nome_curto": "PicPay",
        "filtro": "PICPAY",
        "id_cliente": 10,
        "enabled": True,
        "aliases": (
            "PICPAY",
            "PIC PAY",
            "Picpay Servicos",
        ),
        "fg_sheet": "Falhas_Gerais-BRB",
        "cs_goals": {**_DEFAULT_CS_GOALS},
        "cores": {**_DEFAULT_CORES, "primary": "#21c25e", "dark": "#0d7a38"},
    },
}


def list_clients(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    items = list(CLIENTS.values())
    if enabled_only:
        items = [c for c in items if c.get("enabled", True)]
    return sorted(items, key=lambda c: c.get("nome_curto", c.get("slug", "")))


def get_client_config(slug: str) -> dict[str, Any]:
    key = (slug or "brb").strip().lower()
    if key not in CLIENTS:
        raise KeyError(f"Cliente não cadastrado: {slug}")
    return CLIENTS[key]


def is_client_enabled(slug: str) -> bool:
    try:
        return bool(get_client_config(slug).get("enabled", True))
    except KeyError:
        return False


def resolve_id_cliente(slug: str) -> int:
    """Resolve ``id_cliente`` do registry ou DimCliente por aliases."""
    cfg = get_client_config(slug)
    raw = cfg.get("id_cliente")
    if raw is not None:
        return int(raw)

    from report_brb.brb_filters import _norm_client_key

    keys = {_norm_client_key(cfg.get("filtro", ""))}
    for alias in cfg.get("aliases", ()):
        k = _norm_client_key(alias)
        if k:
            keys.add(k)

    try:
        from apps.qualidade_operacional.services.dim_aliases import load_dim_alias_index

        index = load_dim_alias_index()
        for key in keys:
            if key in index.clientes:
                return int(index.clientes[key])
    except Exception:  # noqa: BLE001 — dim aliases opcionais fora do Django
        pass

    try:
        from apps.dimensoes_processos.models import DimCliente

        for row in DimCliente.objects.values_list("id_cliente", "nome"):
            cid, nome = row
            norm = _norm_client_key(nome)
            if norm and any(k in norm for k in keys if k):
                return int(cid)
    except Exception:  # noqa: BLE001
        pass

    raise KeyError(f"id_cliente não resolvido para cliente: {slug}")
