# -*- coding: utf-8 -*-
"""Catálogo de clientes do report executivo — registry + DimCliente com dados EO."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.conf import settings
from django.db.models import Count

from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.criticidade import (
    EXCLUDED_DEFAULT_REPORT_CLIENTE_IDS,
    is_excluded_default_report_client,
)
from report_brb.client_registry import CLIENTS, get_client_config, resolve_id_cliente

_DEFAULT_CS_GOALS = {
    "confirmed_rate": 5.0,
    "aged_over_90_pct": 10.0,
    "cause_concentration_pct": 35.0,
}
_DEFAULT_CORES = {
    "primary": "#174e97",
    "dark": "#0b2a59",
    "green": "#15803d",
    "red": "#b91c1c",
    "gray": "#64748b",
    "border": "#e2e8f0",
    "bg": "#f1f5f9",
}


def _slugify(nome: str) -> str:
    text = unicodedata.normalize("NFKD", (nome or "").strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64] or "cliente"


def _eo_volume_by_cliente() -> dict[int, dict[str, int]]:
    excluded = EXCLUDED_DEFAULT_REPORT_CLIENTE_IDS
    aud = (
        QualidadeAuditado.objects.exclude(id_cliente__in=excluded)
        .exclude(id_cliente__isnull=True)
        .values("id_cliente")
        .annotate(n=Count("id"))
    )
    fal = (
        QualidadeFalha.objects.exclude(id_cliente__in=excluded)
        .exclude(id_cliente__isnull=True)
        .values("id_cliente")
        .annotate(n=Count("id"))
    )
    out: dict[int, dict[str, int]] = {}
    for row in aud:
        cid = int(row["id_cliente"])
        out.setdefault(cid, {"auditados": 0, "falhas": 0})
        out[cid]["auditados"] = int(row["n"])
    for row in fal:
        cid = int(row["id_cliente"])
        out.setdefault(cid, {"auditados": 0, "falhas": 0})
        out[cid]["falhas"] = int(row["n"])
    return out


def _completeness(*, eo_auditados: int, eo_falhas: int, has_na: bool) -> str:
    if eo_auditados > 0 and eo_falhas > 0 and has_na:
        return "complete"
    if eo_auditados > 0 or eo_falhas > 0:
        return "partial"
    return "empty"


def report_uses_eo_db() -> bool:
    return bool(settings.BRB_REPORT_USE_EO_DB)


def resolve_client_config(slug: str) -> dict[str, Any]:
    """Config do registry ou cliente dinâmico com dados na EO."""
    key = (slug or "brb").strip().lower()
    if key in CLIENTS:
        return get_client_config(key)
    for row in list_report_clients(enabled_only=False):
        if row["slug"] == key:
            return {
                "slug": row["slug"],
                "nome": row["nome"],
                "nome_curto": row["nome_curto"],
                "id_cliente": row.get("id_cliente"),
                "enabled": row.get("enabled", True),
                "fg_sheet": "Falhas_Gerais-BRB",
                "cs_goals": dict(_DEFAULT_CS_GOALS),
                "cores": dict(_DEFAULT_CORES),
            }
    raise KeyError(f"Cliente não cadastrado: {slug}")


def is_report_client_enabled(slug: str) -> bool:
    try:
        return bool(resolve_client_config(slug).get("enabled", True))
    except KeyError:
        return False


_PLACEHOLDER_CLIENTE_RE = re.compile(r"^cliente\s+\d+$", re.IGNORECASE)


def _is_excluded_report_client(id_cliente: int | None, nome: str = "") -> bool:
    """Exclui Formalização, GAQ e demais clientes fora do portfólio padrão."""
    return is_excluded_default_report_client(id_cliente, nome)


def _is_listable_cliente_nome(nome: str, id_cliente: int) -> bool:
    text = (nome or "").strip()
    if not text:
        return False
    if _is_excluded_report_client(id_cliente, text):
        return False
    if _PLACEHOLDER_CLIENTE_RE.match(text):
        return False
    return text.casefold() != f"cliente {id_cliente}".casefold()


def _build_cliente_name_index() -> dict[int, str]:
    """Nomes de DimCliente + registry (consulta direta — sem cache/injeção Formalização)."""
    dim_from_db = {
        int(cid): (nome or "").strip()
        for cid, nome in DimCliente.objects.values_list("id_cliente", "nome")
        if cid is not None
    }
    names: dict[int, str] = {}
    for id_cliente, nome in dim_from_db.items():
        if _is_excluded_report_client(id_cliente, nome):
            continue
        if nome and _is_listable_cliente_nome(nome, id_cliente):
            names[id_cliente] = nome
    for cfg in CLIENTS.values():
        raw_id = cfg.get("id_cliente")
        if raw_id is None:
            continue
        id_cliente = int(raw_id)
        if _is_excluded_report_client(id_cliente, cfg.get("nome") or ""):
            continue
        if dim_from_db and id_cliente not in dim_from_db:
            continue
        names[id_cliente] = cfg.get("nome") or names.get(id_cliente, "")
    return names


def _report_cliente_ids(
    dim_names: dict[int, str],
    volumes: dict[int, dict[str, int]],
) -> set[int]:
    """Espelha DimCliente da Qualidade (+ volume EO), exceto Formalização."""
    ids = set(dim_names.keys()) | set(volumes.keys())
    return {
        cid
        for cid in ids
        if not _is_excluded_report_client(cid, dim_names.get(cid, ""))
    }


def list_report_clients(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    """Registry + todos os DimCliente listáveis (exclui Claro Formalização)."""
    volumes = _eo_volume_by_cliente()
    dim_names = _build_cliente_name_index()
    dim_loaded = bool(
        DimCliente.objects.exists()
    )
    target_ids = _report_cliente_ids(dim_names, volumes)
    seen_ids: set[int] = set()
    seen_slugs: set[str] = set()
    items: list[dict[str, Any]] = []

    for slug, cfg in CLIENTS.items():
        id_cliente = cfg.get("id_cliente")
        if id_cliente is None:
            try:
                id_cliente = resolve_id_cliente(slug)
            except KeyError:
                id_cliente = None
        if id_cliente is not None and _is_excluded_report_client(
            int(id_cliente), cfg.get("nome") or ""
        ):
            continue
        if dim_loaded and id_cliente is not None and int(id_cliente) not in dim_names:
            continue
        vol = volumes.get(int(id_cliente), {"auditados": 0, "falhas": 0}) if id_cliente else {
            "auditados": 0,
            "falhas": 0,
        }
        if id_cliente is not None:
            seen_ids.add(int(id_cliente))
        enabled = bool(cfg.get("enabled", True))
        if enabled_only and not enabled:
            continue
        seen_slugs.add(slug)
        items.append(
            {
                "slug": slug,
                "nome": cfg.get("nome", slug),
                "nome_curto": cfg.get("nome_curto", slug),
                "enabled": enabled,
                "id_cliente": id_cliente,
                "eo_auditados": vol["auditados"],
                "eo_falhas": vol["falhas"],
                "has_registry": True,
                "completeness": _completeness(
                    eo_auditados=vol["auditados"],
                    eo_falhas=vol["falhas"],
                    has_na=False,
                ),
            }
        )

    for id_cliente in sorted(target_ids):
        if id_cliente in seen_ids:
            continue
        nome = dim_names.get(id_cliente)
        if not nome or not _is_listable_cliente_nome(nome, id_cliente):
            continue
        vol = volumes.get(id_cliente, {"auditados": 0, "falhas": 0})
        slug = _slugify(nome)
        if slug in seen_slugs:
            slug = f"{slug}-{id_cliente}"
        seen_slugs.add(slug)
        items.append(
            {
                "slug": slug,
                "nome": nome,
                "nome_curto": _nome_curto(nome),
                "enabled": True,
                "id_cliente": id_cliente,
                "eo_auditados": vol["auditados"],
                "eo_falhas": vol["falhas"],
                "has_registry": False,
                "completeness": _completeness(
                    eo_auditados=vol["auditados"],
                    eo_falhas=vol["falhas"],
                    has_na=False,
                ),
            }
        )

    return sorted(
        items,
        key=lambda c: (
            -(c["eo_auditados"] + c["eo_falhas"]),
            c["nome_curto"].casefold(),
        ),
    )


def _nome_curto(nome: str, *, limit: int = 32) -> str:
    text = (nome or "").strip()
    if not text:
        return "Cliente"
    first = text.split("—", 1)[0].split("-", 1)[0].strip()
    short = first or text
    return short[:limit]


def list_scheduled_report_client_slugs(*, enabled_only: bool = True) -> list[str]:
    """Slugs para geração agendada: todos os clientes com auditados ou falhas na EO."""
    return [
        row["slug"]
        for row in list_report_clients(enabled_only=enabled_only)
        if row.get("eo_auditados") or row.get("eo_falhas")
    ]


def preview_client_eo(
    client_slug: str,
    *,
    inicio=None,
    fim=None,
    supplement_path=None,
) -> dict[str, Any]:
    """Preview de completude: EO + planilha suplemento opcional."""
    from pathlib import Path

    from report_brb.db_loaders import load_eo_core_frames
    from report_brb.brb_loaders import preview_workbook, validate_workbook

    slug = (client_slug or "brb").strip().lower()
    try:
        cfg = resolve_client_config(slug)
        id_cliente = cfg.get("id_cliente")
        if id_cliente is None:
            id_cliente = resolve_id_cliente(slug)
    except KeyError:
        cfg = None
        id_cliente = None

    eo_counts = {"auditados": 0, "falhas_gerais": 0, "contestacao": 0}
    if id_cliente is not None:
        try:
            frames = load_eo_core_frames(slug, inicio=inicio, fim=fim)
            eo_counts = {
                "auditados": len(frames["auditados"]),
                "falhas_gerais": len(frames["falhas_gerais"]),
                "contestacao": len(frames["contestacao"]),
            }
            id_cliente = frames["id_cliente"]
        except KeyError:
            pass

    supplement: dict[str, Any] = {"provided": False, "sheets": {}, "valid": False}
    has_na = False
    if supplement_path and Path(supplement_path).is_file():
        supplement["provided"] = True
        try:
            validate_workbook(Path(supplement_path), mode="supplement")
            preview = preview_workbook(Path(supplement_path))
            supplement["valid"] = True
            supplement["sheets"] = preview.get("sheets") or {}
            supplement["filename"] = preview.get("filename")
            has_na = any(
                int(supplement["sheets"].get(sheet, 0)) > 0
                for sheet in ("NA_Demandas", "NA_Falhas")
            )
        except (ValueError, FileNotFoundError) as exc:
            supplement["error"] = str(exc)

    completeness = _completeness(
        eo_auditados=eo_counts["auditados"],
        eo_falhas=eo_counts["falhas_gerais"],
        has_na=has_na,
    )

    return {
        "source_mode": "eo_hybrid" if report_uses_eo_db() else "excel_legacy",
        "client_slug": slug,
        "id_cliente": id_cliente,
        "eo": eo_counts,
        "supplement": supplement,
        "completeness": completeness,
        "warnings": _preview_warnings(eo_counts, has_na, supplement),
    }


def _preview_warnings(eo_counts: dict, has_na: bool, supplement: dict) -> list[str]:
    warnings: list[str] = []
    if report_uses_eo_db():
        if not eo_counts["auditados"] and not eo_counts["falhas_gerais"]:
            warnings.append("Sem auditados/falhas na EO para este cliente no período.")
        if not has_na and not supplement.get("provided"):
            warnings.append(
                "NA e Treinamentos ausentes — envie planilha suplemento ou o Pulse ficará parcial."
            )
        elif supplement.get("provided") and not supplement.get("valid"):
            warnings.append(f"Planilha suplemento inválida: {supplement.get('error', 'verifique as abas')}")
    return warnings
